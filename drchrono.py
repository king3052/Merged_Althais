"""
drchrono.py: the DrChrono connection for "Connect EMR" (emr_connect.py).

What is verified and what is not (read before relying on this):
  Verified against DrChrono's published API tutorial: the OAuth2 flow (authorize at /o/authorize/, token at /o/token/, the
  authorization code is single-use, access tokens last 48 hours and are refreshed with the refresh token), the API base URL
  https://app.drchrono.com/api/, paginated lists (`results` + `next`), the /patients and /appointments endpoints, and the
  default limit of 500 API calls per hour per app.
  NOT verified: the exact fields on appointments, line items and patients that carry the codes, charges and insurance, and
  the filters /line_items accepts. The reader below is written to tolerate several likely field names, but it has never run
  against a real DrChrono account. So a sync only ever produces a *preview* for a person to review: nothing is saved until they
  press Save, and anything it couldn't read shows up as a missing diagnosis, payer or date in the claim check.
  First real test: connect a DrChrono test account, pull a few days, and compare the preview with what DrChrono shows.

Setup (once, by whoever owns the DrChrono account): in DrChrono, Account > API > New Application. Set the redirect URI to
<APP_URL>/api/integrations/drchrono/callback, then set DRCHRONO_CLIENT_ID and DRCHRONO_CLIENT_SECRET (and APP_URL) on the server.

Tokens are stored encrypted per clinic (same sealing as the clearinghouse key) and are never sent to the browser.
"""

import datetime as dt
import json
import os
import secrets
import time
from datetime import timezone
from urllib.parse import urlencode

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

import emr_connect as ec
from auth import APP_URL, OrgSettings, _org_namespace, get_db, require_biller
from clearinghouse import seal, unseal

router = APIRouter()

AUTH_URL = "https://app.drchrono.com/o/authorize/"
TOKEN_URL = "https://app.drchrono.com/o/token/"
API = "https://app.drchrono.com/api"
CATEGORY = "drchrono"
MAX_CALLS = 400            # DrChrono allows 500 calls an hour per app by default; stay under it
MAX_APPOINTMENTS = 150
SKIP_STATUS = {"cancelled", "canceled", "no show", "rescheduled"}


class DrChronoError(Exception):
    pass


def redirect_uri() -> str:
    return os.environ.get("DRCHRONO_REDIRECT_URI") or f"{APP_URL.rstrip('/')}/api/integrations/drchrono/callback"


def configured() -> bool:
    return bool(os.environ.get("DRCHRONO_CLIENT_ID") and os.environ.get("DRCHRONO_CLIENT_SECRET"))


# ---- the clinic's stored connection ----
def _load(db, org):
    row = db.scalar(sa_select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))
    try:
        return row, (json.loads(row.data or "{}") if row else {})
    except Exception:
        return row, {}


def _save(db, org, doc):
    row, _ = _load(db, org)
    if not row:
        row = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(row)
    row.data = json.dumps(doc)
    db.commit()


def _store_tokens(db, org, tok: dict, keep: dict = None):
    doc = dict(keep or {})
    doc.update({"access": seal(tok["access_token"]), "refresh": seal(tok["refresh_token"]) if tok.get("refresh_token") else doc.get("refresh", ""),
                "expires_at": time.time() + float(tok.get("expires_in") or 0)})
    doc.setdefault("connected_at", dt.datetime.now(timezone.utc).isoformat())
    _save(db, org, doc)


class Client:
    """A small, rate-aware DrChrono API client for one clinic."""

    def __init__(self, db, org):
        self.db, self.org, self.calls = db, org, 0
        _, self.doc = _load(db, org)
        if not self.doc.get("access"):
            raise DrChronoError("DrChrono isn't connected yet.")

    def _refresh(self):
        if not self.doc.get("refresh"):
            raise DrChronoError("DrChrono sign-in expired. Connect again.")
        r = requests.post(TOKEN_URL, data={"grant_type": "refresh_token", "refresh_token": unseal(self.doc["refresh"]),
                                           "client_id": os.environ.get("DRCHRONO_CLIENT_ID", ""), "client_secret": os.environ.get("DRCHRONO_CLIENT_SECRET", "")}, timeout=30)
        if r.status_code != 200:
            raise DrChronoError("DrChrono sign-in expired. Connect again.")
        _store_tokens(self.db, self.org, r.json(), keep=self.doc)
        _, self.doc = _load(self.db, self.org)

    def get(self, path, params=None):
        if self.calls >= MAX_CALLS:
            raise DrChronoError(f"Stopped after {MAX_CALLS} calls to stay under DrChrono's hourly limit. Try a shorter date range or try again later.")
        if self.doc.get("expires_at", 0) - time.time() < 300:
            self._refresh()
        url = path if path.startswith("http") else API + path
        for attempt in (0, 1):
            self.calls += 1
            r = requests.get(url, params=params if not path.startswith("http") else None, headers={"Authorization": "Bearer " + unseal(self.doc["access"])}, timeout=30)
            if r.status_code == 401 and attempt == 0:
                self._refresh()
                continue
            if r.status_code == 429:
                raise DrChronoError("DrChrono's hourly call limit was reached. Try again later.")
            if r.status_code >= 400:
                raise DrChronoError(f"DrChrono returned an error ({r.status_code}) for {path}.")
            return r.json()

    def paged(self, path, params=None, limit=None):
        out, url, p = [], path, params
        while url:
            data = self.get(url, p)
            out.extend(data.get("results", []) if isinstance(data, dict) else [])
            url, p = (data.get("next") if isinstance(data, dict) else None), None
            if limit and len(out) >= limit:
                return out[:limit]
        return out


# ──────────────────────────────────────────────────────────────────────────
#  DrChrono records -> Althais's canonical claim (tolerant of field-name differences; see the note at the top)
# ──────────────────────────────────────────────────────────────────────────
def _codes(v):
    """Codes from a list of strings or dicts, or a delimited string."""
    if isinstance(v, str):
        return ec._split_codes(v)
    out = []
    for x in v or []:
        c = x.get("code") or x.get("icd_code") or x.get("icd10_code") if isinstance(x, dict) else x
        if c:
            out.append(str(c).strip().upper())
    return out


def _payer(patient: dict) -> str:
    for k in ("primary_insurance", "insurance", "primary_insurance_company"):
        v = patient.get(k)
        if isinstance(v, dict):
            for kk in ("insurance_company", "payer_name", "insurance_payer_name", "name", "company"):
                if v.get(kk):
                    return str(v[kk])
        elif v:
            return str(v)
    return ""


def to_canonical(appt: dict, items: list, patient: dict, doctor: dict) -> dict:
    when = str(appt.get("scheduled_time") or appt.get("date") or "")
    mods = []
    cpt = []
    amount = 0.0
    for it in items:
        code = str(it.get("code") or it.get("cpt_code") or it.get("procedure_code") or "").strip().upper()
        if code and code not in cpt:
            cpt.append(code)
        for k, v in it.items():
            if str(k).startswith("modifier") and v:
                mods += ec._split_codes(v if isinstance(v, str) else ",".join(map(str, v)))
        try:
            amount += float(it.get("price") or it.get("billed") or it.get("charge") or 0) * float(it.get("quantity") or it.get("units") or 1)
        except (TypeError, ValueError):
            pass
    icd = _codes(appt.get("icd10_codes") or appt.get("diagnoses") or [])
    for it in items:
        for c in _codes(it.get("icd10_codes") or []):
            if c not in icd:
                icd.append(c)
    name = f"{patient.get('first_name', '')} {patient.get('last_name', '')}".strip()
    prov = f"{doctor.get('first_name', '')} {doctor.get('last_name', '')}".strip()
    return {"claim_id": f"DC-{appt.get('id')}", "patient_name": name, "mrn": str(patient.get("chart_id") or patient.get("id") or ""),
            "dob": ec._date(patient.get("date_of_birth")), "dos": ec._date(when[:10]), "dos_raw": when, "payer": _payer(patient),
            "cpt": cpt, "icd": icd, "modifiers": list(dict.fromkeys(mods)), "amount": round(amount, 2), "provider": prov, "pos": str(appt.get("place_of_service") or "")[:8],
            "rows": []}


def fetch_claims(client: Client, days: int):
    """Appointments in the last `days` days that have billed line items, as canonical claims. Returns (claims, notes)."""
    today = dt.datetime.now(timezone.utc).date()
    start = today - dt.timedelta(days=max(1, min(int(days or 30), 90)))
    appts = client.paged("/appointments", {"date_range": f"{start}/{today}"}, limit=MAX_APPOINTMENTS + 1)
    notes = []
    if len(appts) > MAX_APPOINTMENTS:
        appts = appts[:MAX_APPOINTMENTS]
        notes.append(f"Only the first {MAX_APPOINTMENTS} appointments were read, to stay under DrChrono's hourly limit. Pull a shorter range for the rest.")
    patients, doctors, claims = {}, {}, []
    for a in appts:
        if str(a.get("status") or "").strip().lower() in SKIP_STATUS:
            continue
        items = client.paged("/line_items", {"appointment": a.get("id")})
        if not items:
            continue
        pid, did = a.get("patient"), a.get("doctor")
        if pid and pid not in patients:
            patients[pid] = client.get(f"/patients/{pid}")
        if did and did not in doctors:
            doctors[did] = client.get(f"/doctors/{did}")
        claims.append(to_canonical(a, items, patients.get(pid, {}), doctors.get(did, {})))
    notes.append(f"Read {len(appts)} appointments with {client.calls} DrChrono calls.")
    return claims, notes


# ──────────────────────────────────────────────────────────────────────────
#  The connector, and the routes
# ──────────────────────────────────────────────────────────────────────────
class DrChrono(ec.EmrConnector):
    key = "drchrono"
    label = "DrChrono"
    env_needed = ("DRCHRONO_CLIENT_ID", "DRCHRONO_CLIENT_SECRET")

    def status(self, db, org):
        if not configured():
            return {"state": "unavailable", "text": "Built, not switched on: DRCHRONO_CLIENT_ID and DRCHRONO_CLIENT_SECRET aren't set on the server yet.", "actions": []}
        _, doc = _load(db, org)
        if doc.get("access"):
            return {"state": "connected", "text": "Connected" + (f". Last pull: {doc['last_sync'][:10]}, {doc.get('last_count', 0)} claims." if doc.get("last_sync") else "."),
                    "actions": [{"label": "Pull last 30 days", "kind": "sync", "days": 30}, {"label": "Disconnect", "kind": "disconnect"}]}
        return {"state": "ready", "text": "Ready to connect. Field mapping hasn't been tested on a real account, so check each preview.",
                "actions": [{"label": "Connect DrChrono", "kind": "link", "url": "/api/integrations/drchrono/connect"}]}


@router.get("/api/integrations/drchrono/connect")
def connect(request: Request, user=Depends(require_biller)):
    if not configured():
        return JSONResponse({"error": "DrChrono isn't switched on for this server yet."}, status_code=400)
    state = secrets.token_urlsafe(24)
    url = AUTH_URL + "?" + urlencode({"redirect_uri": redirect_uri(), "response_type": "code", "client_id": os.environ["DRCHRONO_CLIENT_ID"], "state": state})
    resp = RedirectResponse(url=url, status_code=302)
    resp.set_cookie("drc_state", state, max_age=600, httponly=True, samesite="lax", secure=request.url.scheme == "https")
    return resp


@router.get("/api/integrations/drchrono/callback")
def callback(request: Request, code: str = "", state: str = "", error: str = "", user=Depends(require_biller), db: Session = Depends(get_db)):
    ec._gate_medical(user, db)
    expected = request.cookies.get("drc_state", "")
    if error or not code or not state or not expected or not secrets.compare_digest(state, expected):
        return RedirectResponse(url="/revenue/connect?drchrono=failed", status_code=302)
    r = requests.post(TOKEN_URL, data={"code": code, "grant_type": "authorization_code", "redirect_uri": redirect_uri(),
                                       "client_id": os.environ["DRCHRONO_CLIENT_ID"], "client_secret": os.environ["DRCHRONO_CLIENT_SECRET"]}, timeout=30)
    if r.status_code != 200 or "access_token" not in r.json():
        return RedirectResponse(url="/revenue/connect?drchrono=failed", status_code=302)
    _store_tokens(db, _org_namespace(user), r.json())
    resp = RedirectResponse(url="/revenue/connect?drchrono=connected", status_code=302)
    resp.delete_cookie("drc_state")
    return resp


@router.post("/api/integrations/drchrono/disconnect")
def disconnect(user=Depends(require_biller), db: Session = Depends(get_db)):
    ec._gate(user, db)
    row, _ = _load(db, _org_namespace(user))
    if row:
        row.data = "{}"
        db.commit()
    return {"ok": True}


@router.post("/api/integrations/drchrono/sync")
async def sync(request: Request, user=Depends(require_biller), db: Session = Depends(get_db)):
    """Pull recent billed visits and return them as a preview (same shape as an upload preview). Saves nothing."""
    ec._gate_medical(user, db)
    org = _org_namespace(user)
    try:
        days = int((await request.json()).get("days", 30))
    except Exception:
        days = 30
    try:
        client = Client(db, org)
        claims, notes = fetch_claims(client, days)
    except DrChronoError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except requests.RequestException:
        return JSONResponse({"error": "Couldn't reach DrChrono. Try again in a minute."}, status_code=502)
    if not claims:
        return JSONResponse({"error": "No billed visits found in that range." + " " + " ".join(notes)}, status_code=404)
    checked = ec.run_checks(claims, existing_ids=ec._existing_ids(db, org))
    doc = client.doc
    doc["last_sync"], doc["last_count"] = dt.datetime.now(timezone.utc).isoformat(), len(checked)
    _save(db, org, doc)
    return {"emr": "drchrono", "file": f"DrChrono, last {days} days", "unmapped_columns": [], "notes": notes, "summary": ec.summarize(checked), "claims": [ec._public(c) for c in checked]}
