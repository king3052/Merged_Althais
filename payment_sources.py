"""
payment_sources.py: a clinic connects its own clearinghouse account, and Althais pulls its payment files (835 / ERA) itself.

Why: most clinics don't know how to export 835 files, and "send us your files" is where a free Lost Money Report stalls.
With the clinic's Claim.MD account key, Althais reads the payment files straight from Claim.MD: the last 200 days the
first time, then every new one each hour. Nothing else is done with the key: no claims are sent from the clinic's account.

  - The key is checked with Claim.MD before it's kept, and stored encrypted (clearinghouse.seal), never shown again.
  - Files go to the payment monitor (monitor.PaymentFile, encrypted, kept 400 days) like uploads do, so the Lost Money
    Report, Recovery and the Monday email all work from them.
  - The first pull is done in steps the page asks for (POST /api/payment-source/sync, a few dozen files a call), so no
    request runs long; after that, monitor.run_due calls sync_due() every hour.
  - New payments close Recovery cases straight away (recovery.match, limited to this clinic).

Claim.MD is the only clearinghouse with a connection today; the others are listed so the page can say so.
"""

from __future__ import annotations

import datetime as dt
import json
import re
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from sqlalchemy import select

import clearinghouse as chx
import monitor
from auth import OrgSettings, SessionLocal

router = APIRouter()

CATEGORY = "payment_source"
PER_SYNC = 25          # payment files read per call from the page (each is one request to Claim.MD)
PER_HOUR = 150         # per clinic per hourly run
CONNECTOR = chx.ClaimMD   # replaced in tests


def _now():
    return dt.datetime.utcnow()


# ── what's stored ──

def _row(db, org):
    return db.scalar(select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))


def load(db, org: str) -> dict:
    row = _row(db, org)
    try:
        return json.loads(row.data or "{}") if row else {}
    except ValueError:
        return {}


def _save(db, org: str, doc: dict):
    row = _row(db, org)
    if row is None:
        row = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(row)
    row.data = json.dumps(doc)
    db.flush()


def status(db, org: str) -> dict:
    """What the page shows. Never includes the key."""
    d = load(db, org)
    connected = bool(d.get("key"))
    return {"connected": connected, "partner": d.get("partner") or "", "label": "Claim.MD" if d.get("partner") == "claimmd" else "",
            "connectedAt": d.get("connectedAt") or "", "connectedBy": d.get("connectedBy") or "", "lastSync": d.get("lastSync") or "",
            "filesRead": int(d.get("filesRead") or 0), "skippedOld": int(d.get("skippedOld") or 0),
            "backfillDone": bool(d.get("backfillDone")), "error": d.get("error") or ""}


def _clean_key(key: str) -> str:
    return re.sub(r"\s+", "", key or "")


def connect(db, org: str, key: str, by: str = "") -> dict:
    """Check the key with Claim.MD, then keep it (encrypted). Returns {"ok", "message"}."""
    key = _clean_key(key)
    if not key or len(key) > 200 or not re.fullmatch(r"[A-Za-z0-9_\-]+", key):
        return {"ok": False, "message": "That doesn't look like a Claim.MD account key. Copy it from Claim.MD: Settings > Account Settings."}
    res = CONNECTOR(key).check_connection({})
    if not res.ok:
        return {"ok": False, "message": res.message or "Claim.MD didn't accept that account key."}
    _save(db, org, {"partner": "claimmd", "key": chx.seal(key), "lastEraId": "0", "connectedAt": _now().isoformat(timespec="seconds") + "Z",
                    "connectedBy": by[:200], "filesRead": 0, "skippedOld": 0, "backfillDone": False, "error": ""})
    return {"ok": True, "message": "Connected. Althais is reading your payment files now."}


def disconnect(db, org: str):
    """Stops the pulls and forgets the key. Payment files already read stay with the monitor (kept 400 days)."""
    row = _row(db, org)
    if row is not None:
        db.delete(row)
        db.flush()


# ── pulling ──

def _era_date(s: str) -> Optional[dt.date]:
    s = (s or "").strip()[:10]
    for f in ("%Y-%m-%d", "%m/%d/%Y", "%Y%m%d", "%m/%d/%y", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def sync(db, org: str, limit: int = PER_SYNC, now: Optional[dt.datetime] = None) -> dict:
    """Read the next payment files from the clinic's Claim.MD account. Files older than the monitor's 200-day window are
    skipped without downloading. Returns {"read", "kept", "skipped", "more", "error"}."""
    now = now or _now()
    doc = load(db, org)
    out = {"read": 0, "kept": 0, "skipped": 0, "more": False, "error": ""}
    if not doc.get("key"):
        out["error"] = "No clearinghouse account connected."
        return out
    try:
        cmd = CONNECTOR(chx.unseal(doc["key"]))
        rows, last = cmd.era_list(doc.get("lastEraId") or "0")
    except Exception as e:
        doc["error"], doc["lastSync"] = _short(e), now.isoformat(timespec="seconds") + "Z"
        _save(db, org, doc)
        out["error"] = doc["error"]
        return out
    rows = sorted(rows, key=lambda r: int(r["eraid"]) if str(r["eraid"]).isdigit() else 0)
    cutoff = (now - dt.timedelta(days=monitor.WINDOW_DAYS)).date()
    done, broke = doc.get("lastEraId") or "0", False
    for r in rows[:limit]:
        d = _era_date(r.get("date") or "")
        if d and d < cutoff:
            out["skipped"] += 1
            done = r["eraid"]
            continue
        try:
            text = cmd.era_835(r["eraid"])
        except Exception as e:
            out["error"] = f"Payment file {r['eraid']}: {_short(e)}"
            broke = True          # this one is tried again next time; the ones before it are kept
            break
        out["read"] += 1
        if monitor.add_file(db, org, f"claimmd-{r['eraid']}.835", text, "clinic_claimmd"):
            out["kept"] += 1
            _close_cases(db, org, text)
        done = r["eraid"]
    if not broke and len(rows) <= limit:
        done = last if str(last).isdigit() and str(done).isdigit() and int(last) >= int(done) else done
    out["more"] = broke is False and len(rows) > limit
    doc.update(lastEraId=str(done), lastSync=now.isoformat(timespec="seconds") + "Z", error=out["error"],
               filesRead=int(doc.get("filesRead") or 0) + out["kept"], skippedOld=int(doc.get("skippedOld") or 0) + out["skipped"])
    if not out["more"] and not out["error"]:
        doc["backfillDone"] = True
    _save(db, org, doc)
    return out


def _close_cases(db, org, text):
    """A payment that settles a Recovery case closes it now, not at the next hourly run. Never blocks the pull."""
    try:
        import lost_money as lm
        import recovery
        remits = [r for ix in lm.interchanges(text) for r in lm.parse_835(ix, "claimmd")]
        recovery.match(db, remits, org)
    except Exception as e:
        print(f"[PAYMENT_SOURCE] cases: {type(e).__name__}")


def _short(e) -> str:
    return re.sub(r"\s+", " ", str(e))[:200] or type(e).__name__


def sync_due(now: Optional[dt.datetime] = None) -> int:
    """Hourly (from monitor.run_due): every connected clinic gets its new payment files. Returns files kept."""
    n = 0
    with SessionLocal() as db:
        orgs = [r.org_key for r in db.scalars(select(OrgSettings).where(OrgSettings.category == CATEGORY)).all()]
    for org in orgs:
        with SessionLocal() as db:
            try:
                got = sync(db, org, limit=PER_HOUR, now=now)
                db.commit()
                n += got["kept"]
            except Exception as e:
                db.rollback()
                print(f"[PAYMENT_SOURCE] {type(e).__name__}")      # no details: they could include clinic data
    return n


# ── routes ──

def _db():
    from auth import get_db
    yield from get_db()


def _org(request: Request, db):
    """(org, error response). Billers and admins of a clinic only."""
    from auth import ROLE_LEVELS, current_user
    import billing_activation as ba
    u = current_user(request, db)
    if not u:
        return None, None, JSONResponse({"error": "Not signed in."}, status_code=401)
    if ROLE_LEVELS.get(u.role or "viewer", 0) < ROLE_LEVELS["biller"]:
        return None, None, JSONResponse({"error": "Billers and admins only."}, status_code=403)
    org = ba.org_of(u)
    if org.startswith("user:"):
        return None, None, JSONResponse({"error": "Join or create a clinic first."}, status_code=400)
    return u, org, None


@router.get("/api/payment-source")
def api_status(request: Request, db=Depends(_db)):
    u, org, err = _org(request, db)
    return err or JSONResponse(status(db, org))


@router.post("/api/payment-source/connect")
async def api_connect(request: Request, db=Depends(_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    try:
        body = await request.json()
    except Exception:
        body = {}
    if (body.get("partner") or "claimmd") != "claimmd":
        return JSONResponse({"error": "Only Claim.MD can be connected today. Upload your payment files instead."}, status_code=400)
    res = connect(db, org, str(body.get("key") or ""), getattr(u, "email", "") or "")
    if not res["ok"]:
        db.rollback()
        return JSONResponse({"error": res["message"]}, status_code=400)
    db.commit()
    return JSONResponse(dict(status(db, org), message=res["message"]))


@router.post("/api/payment-source/sync")
def api_sync(request: Request, db=Depends(_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    got = sync(db, org)
    db.commit()
    return JSONResponse(dict(got, status=status(db, org)))


@router.post("/api/payment-source/disconnect")
def api_disconnect(request: Request, db=Depends(_db)):
    u, org, err = _org(request, db)
    if err:
        return err
    disconnect(db, org)
    db.commit()
    return JSONResponse(status(db, org))


@router.post("/api/payment-source/report", response_class=HTMLResponse)
def api_report(request: Request, db=Depends(_db)):
    """The Lost Money Report over every payment file Althais has for the clinic (pulled and uploaded), last 200 days.
    New items are saved to Recovery, paid ones closed, exactly like an upload with "save" on."""
    import lost_money as lm
    u, org, err = _org(request, db)
    if err:
        return err
    a, saved = monitor.analyse(db, org, by=getattr(u, "email", "") or "Althais")
    if not a.ds.remits and not a.ds.claims:
        db.rollback()
        return JSONResponse({"error": "No payment files yet. If you just connected, give it a minute, or upload files below."}, status_code=400)
    db.commit()
    return HTMLResponse(lm.render_html(a, saved=saved), headers={"Cache-Control": "no-store"})
