"""
extension_api.py: the server side of the Althais browser extension (extension/ folder).

The extension reads a claim off the EMR page the person is looking at (only when they press Read) and asks Althais to check it.
This module is deliberately small and strict:
  - A person with billing access creates a pairing token on the Connect EMR page. Only a hash of it is stored. It can be
    revoked, and an org keeps at most 5.
  - The extension sends that token as a Bearer header. It never uses the person's login cookie.
  - /api/extension/check takes ONLY the fields the checks need (codes, payer, date, charge). It stores nothing and logs nothing
    about the claim. The patient's name is never sent; a placeholder stands in for it so the "no patient" check doesn't fire.
  - Checks run through emr_connect.claim_check, the same code as uploads, so results match everywhere.

Two more actions, each only when the person presses its button, and each acting as the person who created the token:
  - /api/extension/code-note sends a chart note to the same coder as Code a Note and returns suggested codes. Nothing is kept.
  - /api/extension/claim checks, saves (draft) or approves (queue) a full claim for Althais to send through the clinic's
    clearinghouse with the end-of-day batch. This one does carry patient details, because a claim can't go out without them.
    Approving goes through billing_activation.queue_claim, so the batch re-runs every check and holds the claim if anything
    (payer enrollment, clearinghouse, biller access) isn't ready.
"""

import datetime as dt
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import defaultdict, deque
from datetime import timezone

from fastapi import APIRouter, Depends, Header, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

import emr_connect as ec
import icd10
from auth import OrgSettings, _org_namespace, get_db, require_biller

router = APIRouter()
CATEGORY = "extension"
MAX_TOKENS = 5
RATE = (60, 60)               # 60 checks per minute per token
_hits = defaultdict(deque)
NO_STORE = {"Cache-Control": "no-store"}


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _row(db, org):
    return db.scalar(sa_select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))


def _doc(row):
    try:
        return json.loads(row.data or "{}") if row else {}
    except Exception:
        return {}


def _auth(db, authorization):
    """The org a Bearer token belongs to, or None. Token format: <row id>.<secret>."""
    m = re.fullmatch(r"Bearer\s+(\d{1,9})\.([A-Za-z0-9_\-]{20,80})", authorization or "")
    if not m:
        return None
    row = db.get(OrgSettings, int(m.group(1)))
    if not row or row.category != CATEGORY:
        return None
    h = _hash(m.group(2))
    for t in _doc(row).get("tokens", []):
        if hmac.compare_digest(t.get("h", ""), h):
            return row.org_key, f"{row.id}", t.get("uid")
    return None


def _limited(key) -> bool:
    now, q = time.time(), _hits[key]
    while q and now - q[0] > RATE[1]:
        q.popleft()
    if len(q) >= RATE[0]:
        return True
    q.append(now)
    return False


@router.post("/api/extension/token")
def create_token(user=Depends(require_biller), db: Session = Depends(get_db)):
    ec._gate(user, db)
    org = _org_namespace(user)
    row = _row(db, org)
    if not row:
        row = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(row)
        db.commit()
        db.refresh(row)
    doc = _doc(row)
    secret = secrets.token_urlsafe(32)
    toks = (doc.get("tokens", []) + [{"h": _hash(secret), "created": dt.datetime.now(timezone.utc).isoformat(), "uid": user.id}])[-MAX_TOKENS:]
    row.data = json.dumps({"tokens": toks})
    db.commit()
    return JSONResponse({"token": f"{row.id}.{secret}", "count": len(toks)}, headers=NO_STORE)


@router.post("/api/extension/revoke")
def revoke_tokens(user=Depends(require_biller), db: Session = Depends(get_db)):
    ec._gate(user, db)
    row = _row(db, _org_namespace(user))
    if row:
        row.data = "{}"
        db.commit()
    return {"ok": True}


@router.get("/api/extension/ping")
def ping(authorization: str = Header(default=""), db: Session = Depends(get_db)):
    if not _auth(db, authorization):
        return JSONResponse({"error": "That token isn't valid. Create a new one in Althais under Revenue > Connect EMR."}, status_code=401, headers=NO_STORE)
    return JSONResponse({"ok": True, "version": 1}, headers=NO_STORE)


def _clean_claim(b: dict):
    """Only the fields the checks need, trimmed and bounded. Returns (claim, error)."""
    def codes(v, pat, cap=20):
        out = []
        for x in (v if isinstance(v, list) else [])[:cap]:
            x = re.sub(r"\s+", "", str(x)).upper()[:12]
            if x and re.fullmatch(pat, x) and x not in out:
                out.append(x)
        return out
    try:
        amount = round(float(str(b.get("amount") or 0).replace("$", "").replace(",", "")), 2)
    except ValueError:
        amount = 0.0
    if not (0 <= amount < 10_000_000):
        amount = 0.0
    claim = {"claim_id": str(b.get("claim_id") or "")[:40], "patient_name": "On screen (not sent)", "mrn": "", "dob": None,
             "dos": ec._date(str(b.get("dos") or "")[:20]), "dos_raw": str(b.get("dos") or "")[:20], "payer": str(b.get("payer") or "")[:120].strip(),
             "cpt": codes(b.get("cpt"), r"[A-Za-z0-9]{4,6}"), "icd": codes(b.get("icd"), r"[A-Za-z0-9.]{3,9}"), "modifiers": codes(b.get("modifiers"), r"[A-Za-z0-9]{2}", 8),
             "amount": amount, "provider": "", "pos": "", "rows": [],
             # dental screens: each procedure with its tooth, surface and quadrant; tooth_column says whether the screen showed teeth at all
             "lines": ec._lines(b.get("lines")), "tooth_column": bool(b.get("tooth_column"))}
    if not claim["cpt"] and not claim["icd"] and not claim["payer"]:
        return None, "Nothing to check yet. Read the page or type the codes in first."
    return claim, None


@router.post("/api/extension/check")
async def check(request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    who = _auth(db, authorization)
    if not who:
        return JSONResponse({"error": "That token isn't valid. Create a new one in Althais under Revenue > Connect EMR."}, status_code=401, headers=NO_STORE)
    if _limited(who[1]):
        return JSONResponse({"error": "Too many checks in a minute. Wait a moment and try again."}, status_code=429, headers=NO_STORE)
    try:
        body = await request.json()
    except Exception:
        body = {}
    claim, err = _clean_claim(body.get("claim") if isinstance(body, dict) and isinstance(body.get("claim"), dict) else {})
    if err:
        return JSONResponse({"error": err}, status_code=400, headers=NO_STORE)
    c = ec.run_checks([claim])[0]
    return JSONResponse({**_result(c), "echo": {k: c[k] for k in ("cpt", "icd", "modifiers", "payer", "dos", "amount")}}, headers=NO_STORE)


EM_OFFICE = re.compile(r"^992(0[2-5]|1[1-5])$")   # office / outpatient E/M visits


def _extra_flags(c):
    """Checks the panel adds on top of the shared upload checks: diagnosis codes that don't exist or aren't billable
    (the 2026 ICD-10-CM table), and more than one office visit on the same claim."""
    out = []
    if icd10.LOADED:
        for code in c["icd"]:
            exists, billable, _ = icd10.lookup(code)
            if not exists:
                out.append({"severity": "error", "code": "icd_unknown", "message": f"{code} isn't in the 2026 ICD-10-CM code set. Check for a typo."})
            elif not billable:
                kids = ", ".join(k for k, _ in icd10.billable_children(code, 4))
                out.append({"severity": "error", "code": "icd_header", "message": f"{code} is a category, not a billable code. Use a more specific code" + (f", such as {kids}." if kids else ".")})
    em = [x for x in c["cpt"] if EM_OFFICE.match(x)]
    if len(em) > 1:
        out.append({"severity": "warn", "code": "multi_em", "message": f"{' and '.join(em)} are both office visits. Payers usually pay one E/M visit per day, so keep the one the note supports."})
    return out


def _codes(c, flags):
    """One row per CPT and ICD-10 code: description (ICD-10 only; CPT text is AMA-licensed) and the worst flag that names it."""
    rank = {"error": 3, "warn": 2, "info": 1}
    rows = []
    for typ, codes in (("CPT", c["cpt"]), ("ICD-10", c["icd"])):
        for code in codes:
            desc = icd10.lookup(code)[2] if typ == "ICD-10" else ""     # CDT text is ADA-licensed, like CPT's
            if typ == "CPT" and re.fullmatch(r"D\d{4}", code):
                typ_out = "CDT"
            else:
                typ_out = typ
            hits = [f for f in flags if code in f["codes"]]
            worst = max((f["severity"] for f in hits), key=lambda s: rank.get(s, 0), default="ok")
            rows.append({"code": code, "type": typ_out, "description": desc, "status": worst, "issues": len(hits)})
    return rows


def _result(c):
    flags = [{k: f[k] for k in ("severity", "code", "message", "tooth") if k in f} for f in c["flags"] if f["code"] != "no_patient"]
    flags += _extra_flags(c)
    every = c["cpt"] + c["icd"]
    for f in flags:
        f["codes"] = [x for x in every if re.search(r"(?<![A-Z0-9.])" + re.escape(x) + r"(?![A-Z0-9])", f["message"].upper())]
    return {"verdict": ec.verdict_for(flags), "score": ec.score_for(flags), "flags": flags, "codes": _codes(c, flags)}


@router.post("/api/extension/lookup")
async def lookup(request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    """The page only needs to supply a claim number. If the clinic's athenahealth is connected, Althais reads the claim itself and checks it.
    The patient is never returned: only codes, payer, date and charge come back."""
    who = _auth(db, authorization)
    if not who:
        return JSONResponse({"error": "That token isn't valid. Create a new one in Althais under Revenue > Connect EMR."}, status_code=401, headers=NO_STORE)
    if _limited(who[1]):
        return JSONResponse({"error": "Too many checks in a minute. Wait a moment and try again."}, status_code=429, headers=NO_STORE)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cid = re.sub(r"^(AH-)", "", str((body or {}).get("claim_id") or "").strip(), flags=re.I)
    if not re.fullmatch(r"\d{1,12}", cid):
        return JSONResponse({"error": "That doesn't look like an athenahealth claim number (digits only)."}, status_code=400, headers=NO_STORE)
    import athenahealth as ah
    _, doc = ah._load(db, who[0])
    if not doc.get("practice_id") or not ah.configured():
        return JSONResponse({"error": "athenahealth isn't connected in Althais yet. Connect it under Revenue > Connect EMR, or type the codes in."}, status_code=409, headers=NO_STORE)
    try:
        cl = ah.Client(doc["practice_id"])
        raw = cl.get(f"/claims/{cid}")
        raw = raw[0] if isinstance(raw, list) and raw else raw
        if not isinstance(raw, dict) or not raw:
            return JSONResponse({"error": "athenahealth has no claim with that number for this practice."}, status_code=404, headers=NO_STORE)
        claim = ah.to_canonical(raw, None, ah.patient_insurers(cl, raw.get("patientid")) if raw.get("patientid") else {})
    except ah.AthenaError as e:
        return JSONResponse({"error": str(e)}, status_code=400, headers=NO_STORE)
    except Exception:
        return JSONResponse({"error": "Couldn't reach athenahealth. Try again in a minute."}, status_code=502, headers=NO_STORE)
    claim["patient_name"] = "On screen (not sent)"
    c = ec.run_checks([claim])[0]
    fields = {k: c[k] for k in ("cpt", "icd", "modifiers", "payer", "dos", "amount")}
    fields["claim_id"] = cid
    return JSONResponse({**_result(c), "fields": fields}, headers=NO_STORE)



# ──────────────────────────────────────────────────────────────────────────
#  Code a chart note, and create / approve a claim, from the extension
# ──────────────────────────────────────────────────────────────────────────
def _who(db, authorization):
    """(user, token key) for actions that act as a person, or a JSONResponse to return."""
    from auth import User
    who = _auth(db, authorization)
    if not who:
        return JSONResponse({"error": "That token isn't valid. Connect this browser again from Revenue > Connect EMR."}, status_code=401, headers=NO_STORE)
    if _limited(who[1]):
        return JSONResponse({"error": "Too many requests in a minute. Wait a moment and try again."}, status_code=429, headers=NO_STORE)
    user = db.get(User, who[2]) if who[2] else None
    if not user or not getattr(user, "active", 1) or _org_namespace(user) != who[0]:
        return JSONResponse({"error": "This browser was connected before this feature existed. Connect it again from Revenue > Connect EMR."}, status_code=409, headers=NO_STORE)
    return user, who[1]


def _err(e):
    from fastapi import HTTPException
    if isinstance(e, HTTPException):
        return JSONResponse({"error": str(e.detail)}, status_code=e.status_code, headers=NO_STORE)
    raise e


@router.post("/api/extension/code-note")
async def code_note(request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    got = _who(db, authorization)
    if isinstance(got, JSONResponse):
        return got
    user, _ = got
    try:
        body = await request.json()
    except Exception:
        body = {}
    note = str((body or {}).get("note") or "").strip()[:20000]
    if len(note) < 40:
        return JSONResponse({"error": "That's too little text to code. Select the visit note, or open the chart note, and try again."}, status_code=400, headers=NO_STORE)
    import dental_coding
    if dental_coding.is_dental_note(note, body.get("specialty")):
        try:
            return JSONResponse({"codes": dental_coding.code_note(note, user, db), "dental": True}, headers=NO_STORE)
        except Exception as e:
            return _err(e) if hasattr(e, "status_code") else JSONResponse({"error": "Althais couldn't code that note right now."}, status_code=502, headers=NO_STORE)
    import main
    try:
        r = main.code_note({"note": note, "encounter_type": str(body.get("encounter_type") or "emergency")[:20],
                            "specialty": str(body.get("specialty") or "emergency department / urgent care")[:80]}, user=user, db=db)
    except Exception as e:
        return _err(e)
    try:
        j = json.loads(r.body)
    except Exception:
        j = {}
    if getattr(r, "status_code", 200) >= 400:
        return JSONResponse({"error": j.get("error") or "Althais couldn't code that note right now."}, status_code=r.status_code, headers=NO_STORE)
    codes = []
    for x in (j.get("codes") or [])[:20]:
        if not isinstance(x, dict) or not x.get("code"):
            continue
        codes.append({k: x.get(k) for k in ("code", "description", "type", "confidence", "justification", "modifier", "units", "documentation_gap")})
    return JSONResponse({"codes": codes}, headers=NO_STORE)


def _ensure_patient(db, ns: str, snap: dict) -> None:
    """The claim's patient, so Althais shows the claim with the patient's date of birth, sex and member ID. A patient the
    clinic already has is never overwritten; only empty fields are filled in."""
    from auth import OrgPatient
    mrn = (snap.get("mrn") or snap.get("insuredId") or "")[:64]
    if not mrn or not snap.get("patient"):
        return
    p = db.scalar(sa_select(OrgPatient).where(OrgPatient.org_key == ns, OrgPatient.mrn == mrn))
    if p is None:
        p = OrgPatient(org_key=ns, mrn=mrn, name=snap["patient"][:255], dob=snap.get("dob") or "", sex=(snap.get("sex") or "")[:1].upper(),
                       payer=snap.get("payer", "")[:128], provider=snap.get("provider", "")[:255], data="{}")
        db.add(p)
    else:
        p.dob, p.sex, p.payer = p.dob or snap.get("dob") or "", p.sex or (snap.get("sex") or "")[:1].upper(), p.payer or snap.get("payer", "")[:128]
    try:
        data = json.loads(p.data or "{}")
    except ValueError:
        data = {}
    if not data.get("member") and snap.get("insuredId"):
        data["member"] = snap["insuredId"]
    if not data.get("insPrimary") and snap.get("payer"):
        data["insPrimary"] = snap["payer"]
    data.setdefault("source", "extension")
    p.data = json.dumps(data)


def _dental_findings(snap: dict) -> list:
    """For a dental claim: what the dental rules would flag (surfaces, tooth type, same-tooth conflicts, X-rays to attach...)."""
    import dental_rules
    lines = [{"code": x["code"], "tooth": x.get("tooth", ""), "surface": x.get("surface", ""), "quad": x.get("quad", "")} for x in snap["cpts"]]
    c = {"cpt": [x["code"] for x in snap["cpts"]], "lines": lines, "dob": snap.get("dob") or "", "dos": snap.get("svcDate") or "", "tooth_column": True}
    if not dental_rules.is_dental(c):
        return []
    return [{"severity": f["severity"], "message": f["message"]} for f in dental_rules.check(c) if f["severity"] in ("error", "warn")]


def _snapshot(b: dict) -> tuple:
    """The claim in the shape billing_activation sends, trimmed and bounded. Returns (snapshot, problems)."""
    s = lambda k, n=120: re.sub(r"\s+", " ", str(b.get(k) or "")).strip()[:n]
    cpts, icds = [], []
    for x in (b.get("cpts") if isinstance(b.get("cpts"), list) else [])[:24]:
        if not isinstance(x, dict):
            continue
        code = re.sub(r"\s", "", str(x.get("code") or "")).upper()[:6]
        if not code:
            continue
        try:
            units = max(1, min(99, int(x.get("units") or 1)))
        except (TypeError, ValueError):
            units = 1
        try:
            charge = round(max(0.0, float(str(x.get("charge") or 0).replace("$", "").replace(",", ""))), 2)
        except ValueError:
            charge = 0.0
        line = {"code": code, "modifier": re.sub(r"[^A-Z0-9]", "", str(x.get("modifier") or "").upper())[:2], "units": units, "charge": charge, "type": "CPT"}
        if re.fullmatch(r"D\d{4}", code):     # dental: the ADA claim needs tooth, surface and quadrant per procedure
            line.update(type="CDT", tooth=re.sub(r"[^A-Za-z0-9]", "", str(x.get("tooth") or "")).upper()[:4],
                        surface=re.sub(r"[^A-Za-z]", "", str(x.get("surface") or "")).upper()[:7], quad=re.sub(r"[^A-Za-z]", "", str(x.get("quad") or "")).upper()[:4])
        cpts.append(line)
    for x in (b.get("icds") if isinstance(b.get("icds"), list) else [])[:12]:
        code = re.sub(r"\s", "", str(x.get("code") if isinstance(x, dict) else x or "")).upper()[:8]
        if code and code not in [i["code"] for i in icds]:
            icds.append({"code": code, "type": "ICD-10", "description": icd10.lookup(code)[2] if icd10.LOADED else ""})
    dos = ec._date(s("svcDate", 20))
    cid = s("claimId", 40) or "EXT-" + dt.datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S") + "-" + secrets.token_hex(2).upper()
    snap = {"claimId": re.sub(r"[^A-Za-z0-9\-_]", "", cid)[:40], "patient": s("patient"), "dob": ec._date(s("dob", 20)), "sex": s("sex", 10),
            "mrn": s("mrn", 40), "payer": s("payer"), "insuredId": s("insuredId", 40), "provider": s("provider"), "svcDate": dos, "pos": s("pos", 4) or "11",
            "cpts": cpts, "icds": icds, "totalAmt": round(sum(x["charge"] for x in cpts), 2), "source": "extension"}
    return snap


@router.post("/api/extension/claim")
async def claim(request: Request, authorization: str = Header(default=""), db: Session = Depends(get_db)):
    """action "check": what would stop this claim; "draft": save it to Althais's claims; "queue": approve it for sending."""
    got = _who(db, authorization)
    if isinstance(got, JSONResponse):
        return got
    user, _ = got
    try:
        body = await request.json()
    except Exception:
        body = {}
    action = str((body or {}).get("action") or "check")
    snap = _snapshot(body.get("claim") if isinstance(body.get("claim"), dict) else {})
    import billing_activation as ba
    from auth import OrgClaim
    try:
        c = ba.ctx(user, db)
        problems = ba.validate_claim(snap)
        _, data = ba.load_profile(db, c.org)
        dec = ba.claim_decision(db, c.org, c.env, data, user, snap, by_althais=True)
    except Exception as e:
        return _err(e)
    blockers = [{"title": x.get("title", ""), "reason": x.get("reason", ""), "next": x.get("next", "")} for x in dec["blockers"] if x.get("requirement") != "claim_check"]
    out = {"claim": snap, "problems": problems, "blockers": blockers, "dental": ba.is_dental_claim(snap), "findings": _dental_findings(snap)}
    if action == "check":
        return JSONResponse(out, headers=NO_STORE)
    if action not in ("draft", "queue"):
        return JSONResponse({"error": "Unknown action."}, status_code=400, headers=NO_STORE)
    if action == "queue" and problems:
        return JSONResponse(dict(out, error="Fix these before approving: " + " ".join(problems)), status_code=400, headers=NO_STORE)
    ns = _org_namespace(user)
    row = db.scalar(sa_select(OrgClaim).where(OrgClaim.org_key == ns, OrgClaim.claim_id == snap["claimId"]))
    codes = [{k: x[k] for k in ("code", "type", "modifier", "units", "charge", "tooth", "surface", "quad") if k in x} for x in snap["cpts"]] + \
            [{"code": x["code"], "type": "ICD-10"} for x in snap["icds"]]
    if not row:
        row = OrgClaim(org_key=ns, claim_id=snap["claimId"], status="Draft", created_at=dt.datetime.now(timezone.utc))
        db.add(row)
    row.patient_name, row.mrn, row.payer, row.codes, row.amount = snap["patient"], snap["mrn"] or snap["insuredId"], snap["payer"], json.dumps(codes), snap["totalAmt"]
    row.dos, row.provider, row.source = snap["svcDate"], snap["provider"], "extension"
    _ensure_patient(db, ns, snap)
    if action == "draft":
        db.commit()
        return JSONResponse(dict(out, status="draft", message="Saved to Althais as a draft. Approve it here or from Claims when it's ready."), headers=NO_STORE)
    try:
        snap["signedAt"] = dt.datetime.now(timezone.utc).isoformat()
        q = ba.queue_claim(c, snap)
    except Exception as e:
        db.rollback()
        return _err(e)
    row.status = "Submitted" if q.get("status") == "already_sent" else "Queued"
    db.commit()
    msg = q.get("message") or "Approved."
    if blockers:
        msg += " It will be held, not sent, until the items listed are done."
    return JSONResponse(dict(out, status=q.get("status"), submitAt=q.get("submitAt"), message=msg), headers=NO_STORE)
