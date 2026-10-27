"""
emr_connect.py: the layer that sits on top of a clinic's own EMR (Experity, DrChrono, athenahealth, AdvancedMD, ...).

The idea, in one line: the clinic keeps its EMR. Claims come in from it (an export today, a live connection when one is
built), Althais checks them before they go out, and the results come back as a worklist.

How the pieces fit
  Source        where claims come from. Every EMR has the same two doors:
                  1. "upload"  a charges/claims export (CSV or Excel). Works for any EMR today.
                  2. "live"    a connector class that pulls claims through the EMR's API. None is built yet; each EMR
                               below says exactly what is missing, and nothing is ever shown as connected until it is.
  Canonical     every source is turned into the same claim shape (see CANON), so the checks and the screens never care
                which EMR the claim came from.
  Checks        claim_check() runs the same pre-submission checks on every claim: code format, NCCI bundling, missing
                diagnosis/payer/date, timely-filing age, duplicates.
  Store         checked claims are saved as ordinary OrgClaim rows, so they show up in the existing Claims, Denials and
                Payer pages with no new tables.

Adding a live connector for an EMR
  Subclass EmrConnector, fill in `fetch_claims()` against that vendor's documented API (and `to_canonical()` if its field
  names differ), then replace the `None` in EMRS with the class. Endpoints are never guessed: until the vendor's
  documentation and credentials are in hand, the entry stays None and the page says what is missing.

Honesty rules (same as clearinghouse.py): nothing here claims a connection, an integration or a certification that does
not exist. Checks are pre-submission screening, not a guarantee that a payer will pay.
"""

import csv
import datetime as dt
import io
import json
import os
import re
from collections import Counter, defaultdict
from datetime import timezone

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import JSONResponse, RedirectResponse, Response
from sqlalchemy import select as sa_select
from sqlalchemy.orm import Session

from auth import (OrgClaim, SessionLocal, _org_namespace, current_user, ensure_area, ensure_product, get_db,
                  record_activity, require_biller, require_user)
import dental_rules
from code_validation import check_code_format
from ncci_checker import check_claim_ncci

router = APIRouter()

MAX_BYTES = 5 * 1024 * 1024
MAX_ROWS = 5000

# ──────────────────────────────────────────────────────────────────────────
#  The EMRs. `live` is the connector class, or None when no live connection is built.
# ──────────────────────────────────────────────────────────────────────────


class NotSupported(Exception):
    pass


class EmrConnector:
    """Base class for a live EMR connection. A subclass must say honestly what it can do."""
    key = ""
    label = ""
    env_needed = ()          # environment variables the connector needs (client id, secret, ...)

    def configured(self) -> bool:
        return all(os.environ.get(v) for v in self.env_needed)

    def status(self, db, org_key: str) -> dict:
        """{"state": unavailable|ready|connected, "text": ..., "actions": [...]} for this clinic. Subclasses override."""
        return {"state": "unavailable", "text": "Live connection isn't built yet.", "actions": []}

    def fetch_claims(self, account: dict, since: str):
        """Return a list of raw records from the EMR for this clinic. Implemented per vendor, against its documentation."""
        raise NotSupported(f"{self.label}: live claim pull isn't built yet.")

    def to_canonical(self, raw: dict) -> dict:
        """Map one raw record to the canonical shape (CANON). The default expects the keys to match already."""
        return {k: raw.get(k) for k in CANON}


EMRS = {
    "experity": {
        "label": "Experity", "segment": "Urgent care",
        "live": None,
        "live_state": "Needs a partner conversation with Experity. No public developer program was found.",
        "access": "Partner program (contact Experity)",
    },
    "drchrono": {
        "label": "DrChrono", "segment": "Independent practices",
        "live": None,
        "live_state": "Next to build. DrChrono lets a clinic create its own API app (Account > API), so no partnership is needed to start.",
        "access": "Self-service API app",
    },
    "athenahealth": {
        "label": "athenahealth", "segment": "Independent and group practices",
        "live": None,
        "live_state": "Apply to the athenahealth developer program for sandbox access, then a Marketplace review for a listing.",
        "access": "Developer program + Marketplace",
    },
    "advancedmd": {
        "label": "AdvancedMD", "segment": "Independent practices",
        "live": None,
        "live_state": "Needs an AdvancedMD developer license and a certified integration (API connection request).",
        "access": "Developer license + certification",
    },
    "tebra": {
        "label": "Tebra", "segment": "Independent practices",
        "live": None,
        "live_state": "A practice's Tebra admin generates a Customer Key; the clinic connects Althais itself.",
        "access": "Customer Key (self-service)",
    },
    # ── dental practice software ── live connections need each vendor's developer or partner program; until then a
    # practice uploads a procedure/claims report, and on the web-based systems the browser extension works on top.
    "opendental": {
        "label": "Open Dental", "segment": "Dental",
        "live": None,
        "live_state": "Next dental system to connect. Open Dental has an open API with a self-serve developer portal (a monthly fee per location). Upload a procedures report now.",
        "access": "Self-serve API (per-location fee)",
    },
    "dentrix": {
        "label": "Dentrix", "segment": "Dental",
        "live": None,
        "live_state": "Desktop software, so the browser extension can't sit on top of it. A live connection needs the Dentrix Developer Program (application, review and a fee). Upload a Dentrix procedures or claims report now.",
        "access": "Developer program (paid, approval)",
    },
    "eaglesoft": {
        "label": "Eaglesoft", "segment": "Dental",
        "live": None,
        "live_state": "Desktop software. A live connection needs a partnership with Patterson Dental. Upload an Eaglesoft report now.",
        "access": "Partner program (Patterson)",
    },
    "dentrix_ascend": {
        "label": "Dentrix Ascend", "segment": "Dental",
        "live": None,
        "live_state": "Web-based, so the Althais browser extension works on top of it today. A live connection needs approval in the Dentrix Ascend API Exchange.",
        "access": "API Exchange (application) + browser extension",
    },
    "denticon": {
        "label": "Denticon", "segment": "Dental",
        "live": None,
        "live_state": "Web-based, so the Althais browser extension works on top of it today. Planet DDS has no public API; live data needs a partner integration.",
        "access": "Browser extension; partner program",
    },
    "curve": {
        "label": "Curve Dental", "segment": "Dental",
        "live": None,
        "live_state": "Web-based, so the Althais browser extension works on top of it today. A live connection needs Curve's partner program.",
        "access": "Browser extension; partner program",
    },
    "carestream": {
        "label": "CS PracticeWorks / CS R4+", "segment": "Dental",
        "live": None,
        "live_state": "Desktop software. Live data needs the vendor's partner program. Upload a procedures report now.",
        "access": "Partner program",
    },
    "tab32": {
        "label": "tab32", "segment": "Dental",
        "live": None,
        "live_state": "Web-based, so the Althais browser extension works on top of it today. Ask tab32 about API access for a live connection.",
        "access": "Browser extension; API by request",
    },
    "dolphin": {
        "label": "Dolphin (orthodontics)", "segment": "Dental",
        "live": None,
        "live_state": "Orthodontic practice software. Live data needs the vendor's partner program. Upload a report now.",
        "access": "Partner program",
    },
    "cloud9": {
        "label": "Cloud 9 (orthodontics)", "segment": "Dental",
        "live": None,
        "live_state": "Web-based orthodontic software, so the Althais browser extension works on top of it today. Live data needs a Planet DDS partner integration.",
        "access": "Browser extension; partner program",
    },
    "other": {
        "label": "Other EMR", "segment": "Any system that can export a charges report",
        "live": None,
        "live_state": "Upload works with any export that has patient, payer, date, CPT and diagnosis columns. Dental reports also need tooth and surface columns.",
        "access": "Upload only",
    },
}


def for_practice(key: str, practice: str) -> bool:
    """Dental clinics see dental practice software; medical clinics see medical EMRs. "Other" (any export) is for both."""
    return key == "other" or (EMRS[key]["segment"] == "Dental") == (practice == "dental")


def emr_cards(db=None, org=None, practice=None):
    out = []
    for key, e in EMRS.items():
        if practice and not for_practice(key, practice):
            continue
        live = e["live"]
        st = live().status(db, org) if (live and db is not None) else None
        connected = bool(st and st["state"] == "connected")
        label = "Other dental software" if key == "other" and practice == "dental" else e["label"]
        out.append({"key": key, "label": label, "segment": e["segment"], "access": e["access"], "upload": True, "live": connected,
                    "live_state": st["text"] if st else e["live_state"], "actions": st["actions"] if st else []})
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Canonical claim shape and the file reader
# ──────────────────────────────────────────────────────────────────────────

CANON = ("claim_id", "patient_name", "mrn", "dob", "dos", "payer", "cpt", "icd", "modifiers", "amount", "provider", "pos")
# dental exports also carry, per procedure line: tooth, surface, quadrant (claims get `lines`, see dental_rules.py)

# Header names seen in practice-management exports, lower-cased with punctuation stripped. First match wins.
# These are generic aliases, not any one vendor's documented export format. If a header isn't recognized the preview
# says so and the clinic can rename the column and upload again.
ALIASES = {
    "claim_id":     ["claimid", "claimnumber", "claimno", "claim", "chargeid", "encounterid", "visitid", "invoice", "invoicenumber", "accountnumber"],
    "patient_name": ["patient", "patientname", "name", "fullname", "patientfullname"],
    "first_name":   ["firstname", "patientfirstname", "first", "fname"],
    "last_name":    ["lastname", "patientlastname", "last", "lname"],
    "mrn":          ["mrn", "patientid", "chartnumber", "chartno", "medicalrecordnumber", "accountid", "patientnumber"],
    "dob":          ["dob", "dateofbirth", "birthdate", "birthday"],
    "dos":          ["dos", "dateofservice", "servicedate", "visitdate", "encounterdate", "date", "fromdate", "servicefromdate"],
    "payer":        ["payer", "payername", "insurance", "insurancename", "primaryinsurance", "primarypayer", "carrier", "plan", "insurancecompany"],
    "cpt":          ["cpt", "cptcode", "cptcodes", "procedure", "procedurecode", "procedurecodes", "hcpcs", "code", "cptcodehcpcs", "service",
                     "cdt", "cdtcode", "adacode", "proccode", "proc", "procedurecodecdt"],
    "icd":          ["icd", "icd10", "icd10code", "icd10codes", "diagnosis", "diagnosiscode", "diagnosiscodes", "dx", "dxcodes", "dx1"],
    "modifiers":    ["modifier", "modifiers", "mod", "mod1"],
    "amount":       ["amount", "charge", "charges", "chargeamount", "billed", "billedamount", "totalcharge", "totalcharges", "fee", "price", "lineamount"],
    "provider":     ["provider", "providername", "renderingprovider", "doctor", "physician", "clinician"],
    "pos":          ["pos", "placeofservice", "placeofservicecode"],
    "tooth":        ["tooth", "toothnumber", "toothno", "toothnum", "th", "teeth"],
    "surface":      ["surface", "surfaces", "surf", "toothsurface"],
    "quad":         ["quadrant", "quad", "arch", "area", "toothrange"],
}


def _norm_header(h) -> str:
    return re.sub(r"[^a-z0-9]", "", str(h or "").lower())


def _read_table(filename: str, data: bytes):
    """Return (headers, rows) from a CSV or XLSX upload. Raises ValueError with a plain message."""
    name = (filename or "").lower()
    if name.endswith((".xlsx", ".xlsm")):
        try:
            import openpyxl
            wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
            ws = wb.worksheets[0]
            it = ws.iter_rows(values_only=True)
            headers = [("" if c is None else str(c).strip()) for c in next(it, [])]
            rows = []
            for r in it:
                if r is None or all(c in (None, "") for c in r):
                    continue
                rows.append([c for c in r])
                if len(rows) > MAX_ROWS:
                    raise ValueError(f"That file has more than {MAX_ROWS} rows. Split it into smaller files.")
            return headers, rows
        except ValueError:
            raise
        except Exception:
            raise ValueError("That Excel file couldn't be read. Save it as .xlsx or export a CSV instead.")
    if name.endswith((".csv", ".txt", ".tsv")):
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = data.decode("latin-1")
        sample = text[:4096]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        rows_all = [r for r in csv.reader(io.StringIO(text), dialect) if any((c or "").strip() for c in r)]
        if not rows_all:
            raise ValueError("That file is empty.")
        if len(rows_all) - 1 > MAX_ROWS:
            raise ValueError(f"That file has more than {MAX_ROWS} rows. Split it into smaller files.")
        return [h.strip() for h in rows_all[0]], rows_all[1:]
    raise ValueError("Upload a .csv or .xlsx file.")


def _map_headers(headers):
    """canonical field -> column index, plus the headers that matched nothing."""
    idx, used = {}, set()
    normed = [_norm_header(h) for h in headers]
    for field, names in ALIASES.items():
        for want in names:
            hit = next((i for i, h in enumerate(normed) if h == want and i not in used), None)
            if hit is not None:
                idx[field] = hit
                used.add(hit)
                break
    unmapped = [headers[i] for i in range(len(headers)) if i not in used and headers[i]]
    return idx, unmapped


def _s(v) -> str:
    if v is None:
        return ""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    return str(v).strip()


def _date(v) -> str:
    """ISO date from the common export formats, or '' when it can't be read."""
    if isinstance(v, (dt.datetime, dt.date)):
        return v.strftime("%Y-%m-%d")
    t = _s(v)
    if not t:
        return ""
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%m-%d-%Y", "%Y/%m/%d", "%b %d, %Y", "%B %d, %Y", "%m/%d/%Y %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return dt.datetime.strptime(t, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def _money(v) -> float:
    t = re.sub(r"[^0-9.\-()]", "", _s(v))
    if not t:
        return 0.0
    neg = t.startswith("(") and t.endswith(")")
    t = t.strip("()")
    try:
        n = float(t)
    except ValueError:
        return 0.0
    return -n if neg else n


def _split_codes(v):
    return [c.strip().upper() for c in re.split(r"[,;|/\s]+", _s(v)) if c.strip()]


def to_canonical_rows(headers, rows):
    """Turn an uploaded table into canonical claims. Line-level exports (one row per CPT, same claim id) are grouped."""
    idx, unmapped = _map_headers(headers)
    problems = []
    if "cpt" not in idx:
        problems.append("No CPT/procedure column found.")
    if "patient_name" not in idx and not ("first_name" in idx and "last_name" in idx):
        problems.append("No patient name column found.")
    if problems:
        return [], unmapped, problems

    def cell(r, f):
        i = idx.get(f)
        return r[i] if i is not None and i < len(r) else None

    grouped, order = {}, []
    for n, r in enumerate(rows, start=2):
        name = _s(cell(r, "patient_name"))
        if not name:
            name = (_s(cell(r, "first_name")) + " " + _s(cell(r, "last_name"))).strip()
        cpts = _split_codes(cell(r, "cpt"))
        if not name and not cpts:
            continue
        cid = _s(cell(r, "claim_id"))
        dos = _date(cell(r, "dos"))
        # a claim id when the export has none: patient + service date identify one visit
        key = cid or f"{name.lower()}|{_s(cell(r, 'mrn')).lower()}|{dos}"
        if key not in grouped:
            grouped[key] = {"claim_id": cid, "patient_name": name, "mrn": _s(cell(r, "mrn")), "dob": _date(cell(r, "dob")),
                            "dos": dos, "payer": _s(cell(r, "payer")), "cpt": [], "icd": [], "modifiers": [], "amount": 0.0,
                            "provider": _s(cell(r, "provider")), "pos": _s(cell(r, "pos")), "rows": [], "dos_raw": _s(cell(r, "dos")),
                            "lines": [], "tooth_column": "tooth" in idx}
            order.append(key)
        g = grouped[key]
        g["rows"].append(n)
        if "tooth" in idx or "surface" in idx or "quad" in idx:       # dental export: keep each procedure with its tooth
            for code in cpts:
                g["lines"].append({"code": code, "tooth": _s(cell(r, "tooth")), "surface": _s(cell(r, "surface")), "quad": _s(cell(r, "quad")),
                                   "units": 1, "charge": _money(cell(r, "amount")) if len(cpts) == 1 else 0.0})
        for c in cpts:
            if c not in g["cpt"]:
                g["cpt"].append(c)
        for c in _split_codes(cell(r, "icd")):
            if c not in g["icd"]:
                g["icd"].append(c)
        for c in _split_codes(cell(r, "modifiers")):
            if c not in g["modifiers"]:
                g["modifiers"].append(c)
            if len(cpts) == 1:
                g.setdefault("line_mods", []).append({"code": cpts[0], "mod": c})
        g["amount"] += _money(cell(r, "amount"))
        for f in ("payer", "provider", "mrn", "pos"):          # fill blanks from later line items
            if not g[f] and _s(cell(r, f)):
                g[f] = _s(cell(r, f))
    return [grouped[k] for k in order], unmapped, []


# ──────────────────────────────────────────────────────────────────────────
#  The pre-submission check
# ──────────────────────────────────────────────────────────────────────────

def _pos2(v) -> str:
    """Place of service as two digits ("23"), or ""."""
    d = re.sub(r"\D", "", str(v or ""))[:2]
    return d.zfill(2) if d and d != "0" else ""


def line_mods(c: dict) -> dict:
    """{procedure code: [modifiers on its line]} from what the claim knows: explicit line_mods, then the lines themselves."""
    out = {}
    for l in list(c.get("line_mods") or []) + list(c.get("lines") or []):
        if isinstance(l, dict) and l.get("code"):
            m = re.sub(r"[^A-Z0-9]", "", str(l.get("mod") or l.get("modifier") or "").upper())[:2]
            if m:
                out.setdefault(str(l["code"]).upper(), [])
                if m not in out[str(l["code"]).upper()]:
                    out[str(l["code"]).upper()].append(m)
    return out


# Severity: "error" = fix before sending, "warn" = look at it, "info" = for your records.
def claim_check(c: dict, today: dt.date = None, seen_ids=None, existing_ids=None):
    """Return a list of {severity, code, message}. `c` is a canonical claim (CANON keys, cpt/icd as lists)."""
    today = today or dt.datetime.now(timezone.utc).date()
    out = []

    def add(sev, code, msg):
        out.append({"severity": sev, "code": code, "message": msg})

    cpts, icds = c.get("cpt") or [], c.get("icd") or []
    dental = dental_rules.is_dental(c)
    if not cpts:
        add("error", "no_cpt", "No procedure (CPT) code on this claim.")
    for code in cpts:
        ok, warn = check_code_format(code, "CPT")
        if not ok:
            add("error", "bad_cpt", warn)
    for code in icds:
        ok, warn = check_code_format(code, "ICD-10")
        if not ok:
            add("error", "bad_icd", warn)
    if cpts and not icds and not dental:      # dental claims (ADA form) usually don't need a diagnosis code
        add("error", "no_dx", "No diagnosis (ICD-10) code. Payers deny procedures that have no diagnosis to support them.")
    if not c.get("payer"):
        add("error", "no_payer", "No payer on this claim, so it can't be sent or checked against payer rules.")
    if not c.get("patient_name"):
        add("error", "no_patient", "No patient name.")
    if not c.get("dos"):
        add("error" if c.get("dos_raw") else "warn", "no_dos", "Service date is missing or in a format Althais couldn't read." if c.get("dos_raw") else "No service date.")
    else:
        try:
            age = (today - dt.date.fromisoformat(c["dos"])).days
            if age < 0:
                add("error", "future_dos", "The service date is in the future.")
            elif age > 365:
                add("error", "stale", f"Service date is {age} days ago. Many payers' filing windows are 90 to 365 days, so this one is likely past some of them. Check the payer's deadline now.")
            elif age > 90:
                add("warn", "aging", f"Service date is {age} days ago. Some payers' filing windows are close to this, so send it soon.")
        except ValueError:
            pass
    if c.get("amount", 0) <= 0:
        add("warn", "no_charge", "No charge amount (or zero). Check the fee schedule.")
    if c.get("dob") is not None and c.get("dob") == "" and c.get("patient_name"):
        add("info", "no_dob", "No date of birth in the file, so eligibility can't be checked from it.")
    # NCCI bundling between the procedure codes (existing Althais edit table); medical claims only
    if not dental:
        try:
            for f in check_claim_ncci(cpts, c.get("modifiers") or [], line_mods(c)):
                if f.get("bypassed"):
                    add("info", "ncci_mod", f["message"])
                else:
                    add("warn" if f.get("modifier_allowed") else "error", "ncci", f["message"])
        except Exception:
            pass
    else:
        out.extend(dental_rules.check(c))
    # duplicates
    cid = c.get("claim_id")
    if cid and seen_ids is not None:
        if cid in seen_ids:
            add("error", "dup_in_file", f"Claim number {cid} appears more than once in this upload with different visit details.")
        seen_ids.add(cid)
    if cid and existing_ids and cid in existing_ids:
        add("warn", "dup_existing", f"Claim number {cid} is already in Althais. Importing it again would update the saved copy.")
    return out


def score_for(flags) -> int:
    s = 100
    for f in flags:
        s -= {"error": 30, "warn": 10, "info": 0}.get(f["severity"], 0)
    return max(0, min(100, s))


def verdict_for(flags) -> str:
    sev = {f["severity"] for f in flags}
    if "error" in sev:
        return "fix"
    if "warn" in sev:
        return "review"
    return "clean"


STATUS_FOR = {"clean": "Ready to Submit", "review": "Pending Review", "fix": "Draft"}


def run_checks(claims, existing_ids=None):
    seen, out = set(), []
    history = dental_rules.history_flags(claims)          # dental frequency limits across the visits in this batch
    for i, c in enumerate(claims):
        flags = claim_check(c, seen_ids=seen, existing_ids=existing_ids) + history.get(i, [])
        out.append({**c, "flags": flags, "score": score_for(flags), "verdict": verdict_for(flags)})
    return out


def summarize(checked):
    total = len(checked)
    by = Counter(c["verdict"] for c in checked)
    dollars_fix = sum(c["amount"] for c in checked if c["verdict"] == "fix")
    dollars_review = sum(c["amount"] for c in checked if c["verdict"] == "review")
    reasons = Counter(f["code"] for c in checked for f in c["flags"] if f["severity"] in ("error", "warn"))
    return {"total": total, "clean": by.get("clean", 0), "review": by.get("review", 0), "fix": by.get("fix", 0),
            "billed": round(sum(c["amount"] for c in checked), 2),
            "at_risk": round(dollars_fix + dollars_review, 2), "must_fix_amount": round(dollars_fix, 2),
            "top_reasons": [{"code": k, "count": v} for k, v in reasons.most_common(6)]}


# ──────────────────────────────────────────────────────────────────────────
#  Routes
# ──────────────────────────────────────────────────────────────────────────

def _gate(user, db):
    ensure_product(user, db, "insurance")
    ensure_area(user, "claims")


def _gate_medical(user, db):
    """The live medical EMR connections (athenahealth, Tebra, DrChrono) are for medical clinics only."""
    from auth import ensure_practice
    _gate(user, db)
    ensure_practice(user, db, "medical")


def _existing_ids(db, org):
    return {r for (r,) in db.execute(sa_select(OrgClaim.claim_id).where(OrgClaim.org_key == org)).all()}


def _lines(v):
    """Procedure lines (dental): code, tooth, surface, quadrant, units, charge. Trimmed and bounded."""
    out = []
    for l in (v if isinstance(v, list) else [])[:60]:
        if not isinstance(l, dict) or not _s(l.get("code")):
            continue
        try:
            units = max(1, min(99, int(l.get("units") or 1)))
        except (TypeError, ValueError):
            units = 1
        out.append({"code": _s(l.get("code")).upper()[:8], "tooth": _s(l.get("tooth"))[:4], "surface": _s(l.get("surface"))[:7].upper(),
                    "quad": _s(l.get("quad"))[:5].upper(), "units": units, "charge": _money(l.get("charge")),
                    "mod": re.sub(r"[^A-Z0-9]", "", _s(l.get("mod") or l.get("modifier")).upper())[:2]})
    return out


def _public(c):
    """What the browser gets back for each claim."""
    return {k: c.get(k) for k in ("claim_id", "patient_name", "mrn", "dob", "dos", "payer", "cpt", "icd", "modifiers", "amount",
                                   "provider", "pos", "dos_raw", "rows", "flags", "score", "verdict", "lines", "tooth_column")}


@router.get("/api/integrations/emrs")
def api_emrs(user=Depends(require_user), db: Session = Depends(get_db)):
    _gate(user, db)
    from auth import org_practice
    practice = org_practice(user, db)
    return {"emrs": emr_cards(db, _org_namespace(user), practice), "practice": practice}


@router.get("/api/integrations/template.csv")
def api_template(kind: str = "", user=Depends(require_user), db: Session = Depends(get_db)):
    from auth import org_practice
    if (kind or org_practice(user, db)) == "dental":
        body = ("claim_id,patient_name,mrn,dob,date_of_service,payer,cdt,tooth,surface,quadrant,charge,provider\n"
                "SAMPLE-D1,Sample Patient,MRN-0001,1980-01-31,2026-10-01,Sample Dental Plan,D0120,,,,65.00,Dr Example\n"
                "SAMPLE-D1,Sample Patient,MRN-0001,1980-01-31,2026-10-01,Sample Dental Plan,D2392,14,MO,,210.00,Dr Example\n"
                "SAMPLE-D2,Sample Patient,MRN-0001,1980-01-31,2026-10-08,Sample Dental Plan,D4341,,,UR,240.00,Dr Example\n")
        return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="althais-dental-claims-template.csv"'})
    body = ("claim_id,patient_name,mrn,dob,date_of_service,payer,cpt,icd10,modifier,charge,provider\n"
            "SAMPLE-001,Sample Patient,MRN-0001,1980-01-31,2026-10-01,Sample Payer,99284,R07.9,25,450.00,Dr Example\n"
            "SAMPLE-001,Sample Patient,MRN-0001,1980-01-31,2026-10-01,Sample Payer,93005,R07.9,,85.00,Dr Example\n")
    return Response(body, media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="althais-claims-template.csv"'})


@router.post("/api/integrations/import/preview")
async def api_preview(file: UploadFile = File(...), emr: str = Form("other"), user=Depends(require_biller), db: Session = Depends(get_db)):
    """Read the file and run the checks. Nothing is saved."""
    _gate(user, db)
    data = await file.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        return JSONResponse({"error": "That file is over 5 MB. Split it into smaller files."}, status_code=413)
    try:
        headers, rows = _read_table(file.filename, data)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    claims, unmapped, problems = to_canonical_rows(headers, rows)
    if problems:
        return JSONResponse({"error": " ".join(problems) + " Columns found: " + ", ".join(h for h in headers if h)}, status_code=400)
    if not claims:
        return JSONResponse({"error": "No claim rows found in that file."}, status_code=400)
    checked = run_checks(claims, existing_ids=_existing_ids(db, _org_namespace(user)))
    return {"emr": emr if emr in EMRS else "other", "file": file.filename, "unmapped_columns": unmapped,
            "summary": summarize(checked), "claims": [_public(c) for c in checked]}


@router.post("/api/integrations/import/commit")
async def api_commit(request: Request, user=Depends(require_biller), db: Session = Depends(get_db)):
    """Save previewed claims. The checks run again here, so the browser can't hand in a score it didn't earn."""
    _gate(user, db)
    body = await request.json()
    items = body.get("claims") or []
    emr = body.get("emr") if body.get("emr") in EMRS else "other"
    if not isinstance(items, list) or not items:
        return JSONResponse({"error": "Nothing to import."}, status_code=400)
    if len(items) > MAX_ROWS:
        return JSONResponse({"error": f"More than {MAX_ROWS} claims in one import."}, status_code=400)
    only_clean = bool(body.get("only_clean"))
    org = _org_namespace(user)
    claims = []
    for it in items:
        if not isinstance(it, dict):
            continue
        claims.append({"claim_id": _s(it.get("claim_id"))[:64], "patient_name": _s(it.get("patient_name"))[:255], "mrn": _s(it.get("mrn"))[:64],
                       "dob": _date(it.get("dob")), "dos": _date(it.get("dos")), "dos_raw": _s(it.get("dos_raw")), "payer": _s(it.get("payer"))[:128],
                       "cpt": [c for c in map(lambda x: _s(x).upper(), it.get("cpt") or [])][:40],
                       "icd": [c for c in map(lambda x: _s(x).upper(), it.get("icd") or [])][:40],
                       "modifiers": [c for c in map(lambda x: _s(x).upper(), it.get("modifiers") or [])][:20],
                       "amount": _money(it.get("amount")), "provider": _s(it.get("provider"))[:255], "pos": _s(it.get("pos"))[:8],
                       "lines": _lines(it.get("lines")), "tooth_column": bool(it.get("tooth_column", True))})
    checked = run_checks(claims)    # duplicates against saved claims are updated in place below, not rejected
    made = updated = skipped = 0
    for i, c in enumerate(checked, start=1):
        if only_clean and c["verdict"] != "clean":
            skipped += 1
            continue
        cid = c["claim_id"] or f"IMP-{dt.datetime.now(timezone.utc):%y%m%d}-{i:04d}"
        flags = [f"{f['severity'].upper()}: {f['message']}" for f in c["flags"] if f["severity"] != "info"]
        if c.get("lines"):        # dental: each procedure with its tooth, surface and quadrant
            codes = [{"code": l["code"], "type": "CDT" if dental_rules.CDT.match(l["code"]) else "CPT", "tooth": l["tooth"], "surface": l["surface"],
                      "quad": l["quad"], "charge": l["charge"]} for l in c["lines"]] + [{"code": x, "type": "ICD-10"} for x in c["icd"]]
        else:
            codes = ([{"code": x, "type": "CPT"} for x in c["cpt"]] + [{"code": x, "type": "ICD-10"} for x in c["icd"]])
        note = f"Imported from {EMRS[emr]['label']} export. Service date {c['dos'] or 'unknown'}." + (f" Provider: {c['provider']}." if c["provider"] else "")
        row = db.scalar(sa_select(OrgClaim).where(OrgClaim.org_key == org, OrgClaim.claim_id == cid))
        # never move a claim that has already gone out back to Draft
        keep_status = row is not None and row.status in ("Submitted", "Denied", "Appealed", "Paid")
        if row:
            updated += 1
            row.patient_name, row.mrn, row.payer = c["patient_name"], c["mrn"], c["payer"]
            row.codes, row.amount, row.score, row.flags = json.dumps(codes), c["amount"], c["score"], json.dumps(flags)
            row.dos, row.provider = c["dos"], c["provider"]
            row.pos = _pos2(c.get("pos")) or row.pos
            if not row.source:
                row.source = "import"
            if not keep_status:
                row.status = STATUS_FOR[c["verdict"]]
        else:
            made += 1
            db.add(OrgClaim(org_key=org, claim_id=cid, patient_name=c["patient_name"], mrn=c["mrn"], payer=c["payer"],
                            codes=json.dumps(codes), note=note, amount=c["amount"], status=STATUS_FOR[c["verdict"]],
                            score=c["score"], flags=json.dumps(flags), dos=c["dos"], provider=c["provider"], pos=_pos2(c.get("pos")), source=(emr if emr in ("drchrono", "athenahealth", "tebra") else "import")))
    record_activity(db, user, "codes", made + updated)
    db.commit()
    return {"ok": True, "created": made, "updated": updated, "skipped": skipped, "summary": summarize(checked)}


@router.get("/api/integrations/summary")
def api_summary(user=Depends(require_user), db: Session = Depends(get_db)):
    """The numbers behind the 'what is at risk' panel, from claims already saved."""
    _gate(user, db)
    org = _org_namespace(user)
    rows = db.scalars(sa_select(OrgClaim).where(OrgClaim.org_key == org)).all()
    open_rows = [r for r in rows if r.status in ("Draft", "Pending Review", "Ready to Submit")]
    flagged = [r for r in open_rows if json.loads(r.flags or "[]")]
    by_payer = defaultdict(lambda: {"claims": 0, "amount": 0.0})
    for r in flagged:
        p = by_payer[r.payer or "No payer"]
        p["claims"] += 1
        p["amount"] += float(r.amount or 0)
    return {"claims": len(rows), "open": len(open_rows), "flagged": len(flagged),
            "at_risk": round(sum(float(r.amount or 0) for r in flagged), 2),
            "by_payer": sorted(({"payer": k, **v} for k, v in by_payer.items()), key=lambda x: -x["amount"])[:8]}


@router.get("/api/integrations/worklist")
def api_worklist(user=Depends(require_user), db: Session = Depends(get_db)):
    """Saved claims that still need a person: held for fixes or review first, then ready to send."""
    _gate(user, db)
    org = _org_namespace(user)
    rows = db.scalars(sa_select(OrgClaim).where(OrgClaim.org_key == org, OrgClaim.status.in_(("Draft", "Pending Review", "Ready to Submit")))
                      .order_by(OrgClaim.score.asc(), OrgClaim.created_at.desc()).limit(500)).all()
    return {"claims": [{"id": r.claim_id, "patient": r.patient_name, "payer": r.payer, "amount": float(r.amount or 0), "status": r.status,
                        "score": r.score, "flags": json.loads(r.flags or "[]"), "codes": json.loads(r.codes or "[]")} for r in rows]}


@router.get("/revenue/connect")
async def connect_page(request: Request, user=Depends(current_user)):
    if not user:
        return RedirectResponse(url="/login", status_code=302)
    if not getattr(user, "onboarding_complete", 1):
        return RedirectResponse(url="/onboarding", status_code=302)
    import main as _main      # reuse the app's own template setup and user payload
    to_v4 = _main._v4_redirect(request, user)      # clinics on the new EMR get the new page
    if to_v4:
        return to_v4
    ctx = {"user": user, "verified": None, "user_json": _main._app_user_json(user)}
    return _main.templates.TemplateResponse(request, "integrations.html", ctx)


# live connectors register themselves here (imported last, because they build on the names above)
from drchrono import DrChrono   # noqa: E402

EMRS["drchrono"]["live"] = DrChrono
EMRS["drchrono"]["live_state"] = "Built, but not switched on until the DrChrono app credentials are set."

from athenahealth import Athenahealth   # noqa: E402

EMRS["athenahealth"]["live"] = Athenahealth
EMRS["athenahealth"]["live_state"] = "Built, but not switched on until the athenahealth app credentials are set."

from tebra import Tebra   # noqa: E402
EMRS["tebra"]["live"] = Tebra
