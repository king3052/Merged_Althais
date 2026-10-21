"""
tebra.py: the Tebra (formerly Kareo) PM connection for "Connect EMR" (emr_connect.py).

How access works (Tebra Help Center, "Get Started: Tebra SOAP APIs"):
  A practice's System Administrator generates a Customer Key in Tebra and sets the API user's security permissions. Every call
  carries that key plus a Tebra username and password. So a clinic connects Althais itself; no Tebra partnership is needed to
  start (Tebra's API Terms of Use still apply).

What is verified and what is not (read before relying on this):
  Verified against the KareoServices 2.1 WSDL (SOAP 1.1, document/literal, BasicHttpBinding):
    - endpoint https://webservice.kareo.com/services/soap/2.1/KareoServices.svc, namespace http://www.kareo.com/api/schemas/,
      SOAPAction http://www.kareo.com/api/schemas/KareoServices/GetCharges
    - GetCharges(request: GetChargesReq) where GetChargesReq = RequestHeader{ClientVersion, CustomerKey, Password, User},
      Fields (ChargeFieldsToReturn: one boolean per field) and Filter (ChargeFilter: FromServiceDate, ToServiceDate, PracticeName...)
    - the reply GetChargesResult has ErrorResponse{IsError, ErrorMessage}, SecurityResponse{Authenticated, Authorized,
      CustomerKeyValid, SecurityResultSuccess, SecurityResult} and Charges (ChargeData rows, every value a string)
    - ChargeData field names used here: EncounterID, EncounterStatus, ServiceStartDate, ProcedureCode, ProcedureModifier1-4,
      EncounterDiagnosisID1-4, TotalCharges, UnitCharge, Units, PrimaryInsuranceCompanyName, PrimaryInsurancePlanName,
      PatientID, PatientFirstName, PatientLastName, PatientDateOfBirth, RenderingProviderName, ServiceLocationPlaceOfServiceCode
  NOT verified (no Tebra account to test against yet):
    - the date format the filters expect (MM/DD/YYYY is sent)
    - what EncounterDiagnosisID1-4 hold. If they hold ICD-10 codes they are used; if they hold internal IDs the preview says
      the diagnoses couldn't be read, and the claim shows as missing a diagnosis rather than being guessed.
  So a pull only ever produces a *preview* for a person to review: nothing is saved until they press Save.

The clinic's Tebra login and Customer Key are stored encrypted (Fernet; key from TEBRA_ENC_KEY, else SECRET_KEY) and are never
sent back to the browser. Tebra recommends, and Althais asks for, a separate Tebra user for the API with only the permissions it needs.
"""

import base64
import datetime as dt
import hashlib
import json
import os
import re
import xml.etree.ElementTree as ET
from collections import OrderedDict
from datetime import timezone
from xml.sax.saxutils import escape

import requests
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

import emr_connect as ec
from auth import OrgSettings, _org_namespace, get_db, require_biller

router = APIRouter()

CATEGORY = "tebra"
ENDPOINT = "https://webservice.kareo.com/services/soap/2.1/KareoServices.svc"
NS = "http://www.kareo.com/api/schemas/"
ACTION = NS + "KareoServices/"
CLIENT_VERSION = "althais-1"
MAX_DAYS = 90
MAX_CLAIMS = 300
MAX_BYTES = 20 * 1024 * 1024
ICD = re.compile(r"^[A-TV-Z][0-9][0-9AB](?:\.?[0-9A-TV-Z]{1,4})?$")

# Only what the checks need (WSDL order matters to WCF, so this list is kept in the WSDL's alphabetical order).
FIELDS = ["EncounterDiagnosisID1", "EncounterDiagnosisID2", "EncounterDiagnosisID3", "EncounterDiagnosisID4", "EncounterID", "EncounterStatus",
          "ID", "PatientDateOfBirth", "PatientFirstName", "PatientID", "PatientLastName", "PracticeName", "PrimaryInsuranceCompanyName",
          "PrimaryInsurancePlanName", "ProcedureCode", "ProcedureModifier1", "ProcedureModifier2", "ProcedureModifier3", "ProcedureModifier4",
          "RenderingProviderName", "ServiceLocationPlaceOfServiceCode", "ServiceStartDate", "Status", "TotalCharges", "UnitCharge", "Units"]


class TebraError(Exception):
    pass


# ---- the clinic's stored connection (encrypted) ----
def _fernet():
    from cryptography.fernet import Fernet
    secret = (os.environ.get("TEBRA_ENC_KEY") or os.environ.get("SECRET_KEY") or "dev-only-insecure-change-me").encode()
    return Fernet(base64.urlsafe_b64encode(hashlib.sha256(b"althais-tebra|" + secret).digest()))


def _load(db, org):
    row = db.scalar(sa_select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))
    try:
        doc = json.loads(row.data or "{}") if row else {}
    except Exception:
        doc = {}
    return row, doc


def _save(db, org, doc):
    row, _ = _load(db, org)
    if not row:
        row = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(row)
    row.data = json.dumps(doc)
    db.commit()


def _creds(doc) -> dict:
    try:
        return json.loads(_fernet().decrypt(doc["secret"].encode()).decode())
    except Exception:
        return {}


# ---- SOAP ----
def _envelope(op: str, creds: dict, fields, filt: "OrderedDict[str, str]") -> str:
    vals = (("ClientVersion", CLIENT_VERSION), ("CustomerKey", creds.get("customer_key", "")), ("Password", creds.get("password", "")), ("User", creds.get("user", "")))
    hdr = "".join(f"<k:{k}>{escape(str(v))}</k:{k}>" for k, v in vals)
    flds = "".join(f"<k:{f}>true</k:{f}>" for f in fields)
    flt = "".join(f"<k:{k}>{escape(v)}</k:{k}>" for k, v in filt.items() if v)
    return ('<?xml version="1.0" encoding="utf-8"?>'
            f'<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:k="{NS}"><soapenv:Body>'
            f"<k:{op}><k:request><k:RequestHeader>{hdr}</k:RequestHeader><k:Fields>{flds}</k:Fields><k:Filter>{flt}</k:Filter></k:request></k:{op}>"
            "</soapenv:Body></soapenv:Envelope>")


def _strip(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _find(el, name):
    for x in el.iter():
        if _strip(x.tag) == name:
            return x
    return None


def _text(el, name) -> str:
    x = _find(el, name) if el is not None else None
    return (x.text or "").strip() if x is not None else ""


def parse_charges(xml_bytes: bytes):
    """ChargeData rows as plain dicts. Raises TebraError with Tebra's own reason when the reply is an error."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        raise TebraError("Tebra sent a reply Althais couldn't read.")
    fault = _find(root, "Fault")
    if fault is not None:
        raise TebraError("Tebra rejected the request." + (f" Reason given: {_text(fault, 'faultstring')[:200]}" if _text(fault, "faultstring") else ""))
    sec = _find(root, "SecurityResponse")
    if sec is not None:
        if _text(sec, "CustomerKeyValid").lower() == "false":
            raise TebraError("Tebra says the Customer Key isn't valid. Generate a new one in Tebra and connect again.")
        if _text(sec, "Authenticated").lower() == "false":
            raise TebraError("Tebra didn't accept the username or password.")
        if _text(sec, "Authorized").lower() == "false" or _text(sec, "SecurityResultSuccess").lower() == "false":
            why = _text(sec, "SecurityResult")
            raise TebraError("Tebra says this user isn't allowed to read charges. Give the API user permission to view charges." + (f" ({why[:160]})" if why else ""))
    err = _find(root, "ErrorResponse")
    if err is not None and _text(err, "IsError").lower() == "true":
        raise TebraError("Tebra returned an error." + (f" Reason given: {_text(err, 'ErrorMessage')[:200]}" if _text(err, "ErrorMessage") else ""))
    rows = []
    for x in root.iter():
        if _strip(x.tag) == "ChargeData":
            rows.append({_strip(c.tag): (c.text or "").strip() for c in x})
    return rows


def get_charges(creds: dict, start: dt.date, end: dt.date):
    filt = OrderedDict([("FromServiceDate", start.strftime("%m/%d/%Y")), ("PracticeName", creds.get("practice", "")), ("ToServiceDate", end.strftime("%m/%d/%Y"))])
    body = _envelope("GetCharges", creds, FIELDS, filt)
    try:
        r = requests.post(ENDPOINT, data=body.encode("utf-8"), headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": f'"{ACTION}GetCharges"'}, timeout=60)
    except requests.RequestException:
        raise TebraError("Couldn't reach Tebra. Try again in a minute.")
    if len(r.content) > MAX_BYTES:
        raise TebraError("Tebra sent more data than Althais reads in one pull. Try a shorter date range.")
    if r.status_code >= 500 and b"Fault" not in r.content:
        raise TebraError(f"Tebra had a problem ({r.status_code}). Try again later.")
    return parse_charges(r.content)


# ---- Tebra charge lines -> Althais canonical claims (one claim per encounter) ----
def _num(x) -> float:
    try:
        return float(str(x).replace(",", "").replace("$", "") or 0)
    except ValueError:
        return 0.0


def to_claims(rows):
    """Group charge lines by EncounterID. Returns (claims, notes)."""
    enc, notes, unreadable_dx = OrderedDict(), [], 0
    for r in rows:
        key = r.get("EncounterID") or r.get("ID") or ""
        if not key:
            continue
        enc.setdefault(key, []).append(r)
    claims = []
    for key, lines in list(enc.items())[:MAX_CLAIMS]:
        first = lines[0]
        cpt, mods, icd, amount, odd = [], [], [], 0.0, False
        for ln in lines:
            code = (ln.get("ProcedureCode") or "").strip().upper()
            if code and code not in cpt:
                cpt.append(code)
            for i in range(1, 5):
                m = (ln.get(f"ProcedureModifier{i}") or "").strip().upper()
                if m and m not in mods:
                    mods.append(m)
                d = (ln.get(f"EncounterDiagnosisID{i}") or "").strip().upper()
                if d:
                    if ICD.match(d) or ("." in d and re.match(r"^[A-Z]", d)):
                        if d not in icd:
                            icd.append(d)
                    else:
                        odd = True
            amount += _num(ln.get("TotalCharges")) or _num(ln.get("UnitCharge")) * (_num(ln.get("Units")) or 1)
        unreadable_dx += 1 if (odd and not icd) else 0
        when = first.get("ServiceStartDate", "")
        name = f"{first.get('PatientFirstName', '')} {first.get('PatientLastName', '')}".strip() or (f"Patient {first.get('PatientID')}" if first.get("PatientID") else "")
        claims.append({"claim_id": f"TB-{key}", "patient_name": name, "mrn": first.get("PatientID", ""), "dob": ec._date(first.get("PatientDateOfBirth", "")),
                       "dos": ec._date(when), "dos_raw": when, "payer": first.get("PrimaryInsuranceCompanyName") or first.get("PrimaryInsurancePlanName") or "",
                       "cpt": cpt, "icd": icd, "modifiers": mods, "amount": round(amount, 2), "provider": first.get("RenderingProviderName", ""),
                       "pos": first.get("ServiceLocationPlaceOfServiceCode", ""), "rows": []})
    if len(enc) > MAX_CLAIMS:
        notes.append(f"Showing the first {MAX_CLAIMS} of {len(enc)} encounters. Pull a shorter range to see the rest.")
    if unreadable_dx:
        notes.append(f"Tebra sent diagnosis IDs instead of ICD-10 codes for {unreadable_dx} encounter(s), so those show as missing a diagnosis. Check them in Tebra.")
    return claims, notes


class Tebra(ec.EmrConnector):
    key = "tebra"
    label = "Tebra"
    env_needed = ()

    def status(self, db, org):
        _, doc = _load(db, org)
        if doc.get("secret"):
            return {"state": "connected", "text": f"Connected as {doc.get('user_hint', 'your API user')}" + (f" for {doc['practice']}" if doc.get("practice") else "") +
                    (f". Last pull: {doc['last_sync'][:10]}, {doc.get('last_count', 0)} claims." if doc.get("last_sync") else "."),
                    "actions": [{"label": "Pull last 30 days", "kind": "sync", "days": 30}, {"label": "Disconnect", "kind": "disconnect"}]}
        return {"state": "ready", "text": "Ready to connect with your Tebra Customer Key. Field mapping hasn't been tested on a real account, so check each preview.",
                "actions": [{"label": "Connect Tebra", "kind": "tebra"}]}


def _mask(user: str) -> str:
    u = user.split("@")[0]
    return (u[:2] + "•" * max(1, len(u) - 2)) + ("@" + user.split("@")[1] if "@" in user else "")


@router.post("/api/integrations/tebra/connect")
async def connect(request: Request, user=Depends(require_biller), db: Session = Depends(get_db)):
    ec._gate_medical(user, db)
    try:
        b = await request.json()
    except Exception:
        b = {}
    creds = {k: str(b.get(k) or "").strip() for k in ("user", "password", "customer_key", "practice")}
    if not creds["user"] or not creds["password"] or not creds["customer_key"]:
        return JSONResponse({"error": "Enter the Tebra username, password and Customer Key."}, status_code=400)
    if any(len(v) > 200 for v in creds.values()):
        return JSONResponse({"error": "One of those values is too long."}, status_code=400)
    today = dt.datetime.now(timezone.utc).date()
    try:
        get_charges(creds, today - dt.timedelta(days=1), today)          # proves the key, login and permissions work
    except TebraError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    _save(db, _org_namespace(user), {"secret": _fernet().encrypt(json.dumps(creds).encode()).decode(), "user_hint": _mask(creds["user"]),
                                     "practice": creds["practice"], "connected_at": dt.datetime.now(timezone.utc).isoformat()})
    return {"ok": True}


@router.post("/api/integrations/tebra/disconnect")
def disconnect(user=Depends(require_biller), db: Session = Depends(get_db)):
    ec._gate(user, db)
    row, _ = _load(db, _org_namespace(user))
    if row:
        row.data = "{}"
        db.commit()
    return {"ok": True}


@router.post("/api/integrations/tebra/sync")
async def sync(request: Request, user=Depends(require_biller), db: Session = Depends(get_db)):
    """Pull recent charges and return them as a preview (same shape as an upload preview). Saves nothing."""
    ec._gate_medical(user, db)
    org = _org_namespace(user)
    _, doc = _load(db, org)
    creds = _creds(doc) if doc.get("secret") else {}
    if not creds:
        return JSONResponse({"error": "Tebra isn't connected yet."}, status_code=400)
    try:
        days = max(1, min(MAX_DAYS, int((await request.json()).get("days", 30))))
    except Exception:
        days = 30
    today = dt.datetime.now(timezone.utc).date()
    try:
        rows = get_charges(creds, today - dt.timedelta(days=days), today)
    except TebraError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    claims, notes = to_claims(rows)
    if not claims:
        return JSONResponse({"error": "No charges found in that range."}, status_code=404)
    checked = ec.run_checks(claims, existing_ids=ec._existing_ids(db, org))
    doc["last_sync"], doc["last_count"] = dt.datetime.now(timezone.utc).isoformat(), len(checked)
    _save(db, org, doc)
    return {"emr": "tebra", "file": f"Tebra, last {days} days", "unmapped_columns": [], "notes": notes, "summary": ec.summarize(checked), "claims": [ec._public(c) for c in checked]}
