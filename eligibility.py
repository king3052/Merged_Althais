"""
eligibility.py, checking a patient's insurance coverage after registration.

check_eligibility(request) is the one entry point. It returns an EligibilityResult and never raises:
  - "not_connected" until a provider is set up (the stub; the default)
  - Claim.MD's eligibility API (/services/eligdata/) once Althais's Claim.MD account is connected in the admin
    console (clearinghouse.PLATFORM). It needs the payer's Claim.MD payer ID and the clinic's billing NPI and Tax ID.

Dental clinics ask with service type 35 (dental care) and get a dental summary: the annual maximum and what's left of it,
the deductible and what's left, the orthodontic lifetime maximum, what the plan pays per category (preventive,
basic, major...), frequency limits and waiting periods. Payers put limits and waiting periods in free-text notes, so
those are passed through as written, never guessed.

POST /api/eligibility/check runs a check for a patient from the app (the clinic's NPI and Tax ID come from Billing
Activation).

Nothing here logs PHI. Responses are summarized into the result; the raw 271 isn't kept.
"""

import datetime as dt
import re
from dataclasses import dataclass, field, asdict
from typing import List, Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

import clearinghouse as chx
from auth import ensure_product, get_db, org_practice, require_any_role

router = APIRouter()


@dataclass
class EligibilityRequest:
    payer: str                       # payer name as registered
    member_id: str
    patient_dob: str                 # YYYY-MM-DD
    subscriber_first: str = ""
    subscriber_last: str = ""
    subscriber_dob: str = ""         # YYYY-MM-DD; same as the patient when they're the subscriber
    relationship: str = "self"       # self | spouse | child | other
    payer_id: str = ""               # the clearinghouse's payer ID, when known
    provider_npi: str = ""           # the clinic's billing NPI
    provider_tin: str = ""           # the clinic's Tax ID
    service_date: str = ""           # YYYY-MM-DD; today when empty
    service_code: str = "30"         # X12 service type: 30 health benefit plan coverage, 35 dental care


@dataclass
class EligibilityResult:
    status: str                      # active | inactive | not_connected | error
    copay: Optional[float] = None
    deductible: Optional[float] = None
    deductible_remaining: Optional[float] = None
    plan: str = ""
    errors: List[str] = field(default_factory=list)
    source: str = ""                 # which provider answered
    dental: Optional[dict] = None    # dental plans: maximums, deductibles, coverage by category, limits (see dental_summary)
    checked_at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    def as_dict(self):
        return asdict(self)


class EligibilityProvider:
    name = "none"

    def check(self, req: EligibilityRequest) -> EligibilityResult:
        return EligibilityResult(status="not_connected", source=self.name,
                                 errors=["Eligibility checks aren't connected yet. Verify coverage with the payer."])


class ClaimMDEligibility(EligibilityProvider):
    """Claim.MD /services/eligdata/ (271 as XML). Fields per Claim.MD's API reference."""
    name = "claimmd"

    def __init__(self, key: str):
        self.cmd = chx.ClaimMD(key)

    def check(self, req: EligibilityRequest) -> EligibilityResult:
        missing = [n for n, v in (("the clinic's billing NPI", req.provider_npi), ("the clinic's Tax ID", req.provider_tin),
                                  ("the member ID", req.member_id), ("the subscriber's name", req.subscriber_last)) if not v]
        if missing:
            return EligibilityResult(status="error", source=self.name, errors=["Can't check eligibility without " + ", ".join(missing) + "."])
        pid = req.payer_id
        if not pid:
            pid, _ = self.cmd.find_payer(req.payer)
        if not pid:
            return EligibilityResult(status="error", source=self.name, errors=[f"Claim.MD payer ID not found for {req.payer}. Add it to the payer in Billing Activation."])
        ymd = lambda d: (d or "").replace("-", "")
        fields = {"service_code": req.service_code or "30", "ins_name_l": req.subscriber_last, "ins_name_f": req.subscriber_first, "payerid": pid,
                  "pat_rel": "18" if req.relationship in ("", "self") else "G8", "fdos": ymd(req.service_date) or dt.date.today().strftime("%Y%m%d"),
                  "prov_npi": req.provider_npi, "prov_taxid": req.provider_tin, "ins_number": req.member_id,
                  "ins_dob": ymd(req.subscriber_dob or req.patient_dob)}
        if req.relationship not in ("", "self"):
            fields["pat_dob"] = ymd(req.patient_dob)
        try:
            code, text = self.cmd._post("eligdata/", fields, timeout=45)
        except Exception as e:
            return EligibilityResult(status="error", source=self.name, errors=[f"Couldn't reach Claim.MD: {type(e).__name__}"])
        return parse_claimmd_271(code, text, dental=(req.service_code or "").startswith("35"))


def parse_claimmd_271(code: int, text: str, dental: bool = False) -> EligibilityResult:
    """Summarize Claim.MD's eligdata XML: X12 271 benefit codes 1 = active coverage, 6 = inactive, B = co-payment,
    C = deductible (time period 29 = remaining)."""
    root = chx.ClaimMD._xml(text)
    if code != 200 or root is None:
        return EligibilityResult(status="error", source="claimmd", errors=["No answer from Claim.MD."])
    err = root.find(".//error")
    if err is not None:
        return EligibilityResult(status="error", source="claimmd", errors=[(err.get("error_mesg") or err.text or "Claim.MD returned an error.").strip()])
    el = root.find(".//elig")
    if el is None:
        return EligibilityResult(status="error", source="claimmd", errors=["Claim.MD returned no eligibility result."])
    res = EligibilityResult(status="", source="claimmd", plan=el.get("plan_number") or el.get("group_name") or "")
    for b in el.findall(".//benefit"):
        code_ = (b.get("benefit_code") or "").upper()
        amt = b.get("benefit_amount")
        try:
            amt = float(amt) if amt not in (None, "") else None
        except ValueError:
            amt = None
        if code_ == "1":
            res.status = "active"
        elif code_ == "6" and res.status != "active":
            res.status = "inactive"
        elif code_ == "B" and amt is not None and res.copay is None and (b.get("inplan_network") or "Y").upper() != "N":
            res.copay = amt
        elif code_ == "C" and amt is not None:
            if (b.get("benefit_period_code") or "") == "29":
                res.deductible_remaining = amt if res.deductible_remaining is None else res.deductible_remaining
            elif res.deductible is None:
                res.deductible = amt
    if not res.status:
        res.status, res.errors = "error", ["Claim.MD's answer didn't say whether coverage is active."]
    if dental and res.status == "active":
        res.dental = dental_summary(el.findall(".//benefit"))
    return res


# ── dental benefits (X12 271 EB segments as Claim.MD returns them) ───────────
# EB03 service types for dental, grouped the way offices talk about them
DENTAL_CATEGORIES = {"41": "Preventive", "23": "Diagnostic", "25": "Restorative (basic)", "24": "Periodontics", "26": "Endodontics",
                     "40": "Oral surgery", "36": "Crowns", "39": "Prosthodontics", "27": "Maxillofacial prosthetics", "28": "Adjunctive",
                     "38": "Orthodontics", "37": "Dental accident", "35": "Dental care (general)"}
ORDER = ["41", "23", "25", "24", "26", "40", "36", "39", "27", "28", "38", "37", "35"]
_FREQ = re.compile(r"\b(\d+|one|two|three|four)\s*(?:x\s*)?(?:per|every|in|/)\s*(\d+\s*)?(month|mo|year|yr|calendar|benefit|plan|lifetime|consecutive)", re.I)


def _f(v):
    try:
        return float(v) if v not in (None, "") else None
    except ValueError:
        return None


def dental_summary(benefits) -> dict:
    """Turn EB rows into what a front desk needs before treatment. Only what the payer said; nothing estimated."""
    out = {"annual_max": None, "annual_max_remaining": None, "deductible": None, "deductible_remaining": None,
           "family_deductible": None, "family_deductible_remaining": None, "ortho_lifetime_max": None, "ortho_remaining": None,
           "coverage": [], "limits": [], "waiting_periods": [], "not_covered": [], "notes": []}
    pct = {}
    seen_text = set()

    def note(bucket, cat, text):
        text = re.sub(r"\s+", " ", (text or "").strip())
        key = (bucket, cat, text.lower())
        if text and key not in seen_text:
            seen_text.add(key)
            out[bucket].append({"category": cat, "text": text[:300]})

    def put(key, val, net):
        # in-network values win over out-of-network ones
        if val is None:
            return
        cur = out.get(key)
        if cur is None or (net != "N" and out.get("_" + key) == "N"):
            out[key], out["_" + key] = val, net

    for b in benefits:
        code = (b.get("benefit_code") or "").upper()
        cov = (b.get("benefit_coverage_code") or "").strip()
        cat = DENTAL_CATEGORIES.get(cov, b.get("benefit_coverage_description") or "Dental")
        period = (b.get("benefit_period_code") or "").strip()
        level = (b.get("benefit_level_code") or "").upper()
        net = (b.get("inplan_network") or "").upper()
        amt = _f(b.get("benefit_amount"))
        notes = " ".join(x for x in (b.get("benefit_notes"), b.get("benefit_description")) if x)
        if code == "F" and amt is not None:              # maximums
            if cov == "38":
                put("ortho_remaining" if period in ("29", "33") else "ortho_lifetime_max", amt, net)
            elif period in ("29",):
                put("annual_max_remaining", amt, net)
            elif period in ("32", "33"):
                pass                                     # a lifetime maximum outside ortho: kept as a note below
            else:
                put("annual_max", amt, net)
        elif code == "C" and amt is not None:            # deductibles
            fam = level == "FAM"
            if period == "29":
                put("family_deductible_remaining" if fam else "deductible_remaining", amt, net)
            else:
                put("family_deductible" if fam else "deductible", amt, net)
        elif code == "A":                                # co-insurance: the patient's share
            p = _f(b.get("benefit_percent"))
            if p is not None:
                share = p * 100 if p <= 1 else p
                k = cov or "35"
                if k not in pct or (net != "N" and pct[k][1] == "N"):
                    pct[k] = (round(100 - share), net)
        elif code in ("I", "E"):                         # not covered / exclusions
            if cov:
                note("not_covered", cat, notes or "Not covered")
        if notes:
            if re.search(r"wait", notes, re.I):
                note("waiting_periods", cat, notes)
            elif _FREQ.search(notes) or b.get("benefit_qnty"):
                note("limits", cat, notes + (f" ({b.get('benefit_qnty')} allowed)" if b.get("benefit_qnty") and b.get("benefit_qnty") not in notes else ""))
            elif code not in ("1",):
                note("notes", cat, notes)
        elif code == "F" and b.get("benefit_qnty") and amt is None:
            note("limits", cat, f"{b.get('benefit_qnty')} allowed" + (f" per {b.get('benefit_period_description')}" if b.get("benefit_period_description") else ""))
    out["coverage"] = [{"category": DENTAL_CATEGORIES[k] if k in DENTAL_CATEGORIES else k, "plan_pays": v[0], "network": "out" if v[1] == "N" else "in"}
                       for k, v in sorted(pct.items(), key=lambda kv: ORDER.index(kv[0]) if kv[0] in ORDER else 99)]
    for k in list(out):
        if k.startswith("_"):
            del out[k]
    return out


def provider() -> EligibilityProvider:
    conn = chx.load_connection(chx.PLATFORM)
    if conn.get("partner") == "claimmd" and conn.get("key"):
        try:
            return ClaimMDEligibility(chx.unseal(conn["key"]))
        except Exception:
            pass
    return EligibilityProvider()


def check_eligibility(req: EligibilityRequest) -> EligibilityResult:
    try:
        return provider().check(req)
    except Exception as e:   # never fail registration because of eligibility
        return EligibilityResult(status="error", source="", errors=[f"Eligibility check failed ({type(e).__name__})."])


def service_code_for(user, db) -> str:
    return "35" if org_practice(user, db) == "dental" else "30"


def saved_payer_id(db, user, payer: str) -> str:
    """The clearinghouse payer ID the clinic saved for this payer in Billing Activation, if any."""
    try:
        import billing_activation as ba
        from billing_registry import payer_key
        _, data = ba.load_profile(db, ba.org_of(user))
        key = payer_key(payer)
        return next((str(p.get("payerId") or "") for p in (data or {}).get("payers") or [] if p.get("key") == key and p.get("payerId")), "")
    except Exception:
        return ""


@router.post("/api/eligibility/check")
async def check_now(request: Request, user=Depends(require_any_role("admin", "biller", "provider", "front_desk")), db: Session = Depends(get_db)):
    """Check one patient's coverage now (the patient chart's Check Eligibility)."""
    ensure_product(user, db, "insurance")
    from starlette.concurrency import run_in_threadpool
    from patient_intake import _billing_ids
    try:
        b = await request.json()
    except Exception:
        b = {}
    s = lambda k, n=80: str(b.get(k) or "").strip()[:n]
    date = lambda k: s(k, 10) if re.fullmatch(r"\d{4}-\d{2}-\d{2}", s(k, 10)) else ""
    if not s("payer") or not s("member_id"):
        return JSONResponse({"error": "Add the patient's insurance and member ID first."}, status_code=400)
    rel = s("relationship", 10).lower()
    req = EligibilityRequest(payer=s("payer", 128), member_id=s("member_id"), patient_dob=date("patient_dob"), subscriber_first=s("subscriber_first"),
                             subscriber_last=s("subscriber_last"), subscriber_dob=date("subscriber_dob"),
                             relationship=rel if rel in ("self", "spouse", "child", "other") else "self", payer_id=s("payer_id", 20) or saved_payer_id(db, user, s("payer", 128)),
                             service_date=date("service_date"), service_code=service_code_for(user, db), **_billing_ids(db, user))
    res = (await run_in_threadpool(check_eligibility, req)).as_dict()
    return res
