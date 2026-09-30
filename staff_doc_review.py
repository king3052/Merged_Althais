"""
staff_doc_review.py, deciding what happens to an uploaded staff document.

    file checks -> classification -> quality -> fields -> identity -> document rules -> dates -> consistency
    -> duplicates -> potential issues -> external verification -> DECISION

The analyzer (staff_extraction.py) reports what a document shows. Everything here is deterministic: explicit rules
over that report, the staff record and the clinic's policy, so every decision comes with machine-readable reason
codes and per-check results. No single confidence score decides anything.

    AUTO_APPROVED                  every check passed with confidence
    ACTION_REQUIRED_FROM_EMPLOYEE  something the employee can clearly fix (wrong document, blurry, expired...)
    MANAGER_REVIEW_REQUIRED        anything uncertain, conflicting or unusual, never approved silently

This module has no database or network access; staff_onboarding.py gathers the context and applies the outcome.
"""

import datetime as dt
import re
import struct
import unicodedata
from dataclasses import dataclass, field
from typing import Callable, Optional

import staff_extraction as sx

PROCESSOR_VERSION = "althais-doc-review/1.0"

AUTO_APPROVED = "AUTO_APPROVED"
ACTION_REQUIRED = "ACTION_REQUIRED_FROM_EMPLOYEE"
MANAGER_REVIEW = "MANAGER_REVIEW_REQUIRED"

PASS, FAIL, WARN, NOT_RUN, NA = "PASS", "FAIL", "WARN", "NOT_RUN", "NOT_APPLICABLE"

DEFAULT_POLICY = {
    "enabled": True,              # off: documents aren't read or checked automatically at all; every one goes to a manager
    "autoApprove": True,          # off: every document goes to a manager (with the checks already done)
    "minConfidence": 0.9,         # every critical field, and the document type, must be read at least this surely
    "anomalyThreshold": "medium", # potential issues at or above this severity go to a manager
    "requireExternal": [],        # requirement keys whose clinic insists on an external check (e.g. ["license"])
    "expiringSoonDays": 30,
}

CHECK_LABELS = {
    "FILE_INTEGRITY": "File Integrity", "AUTOMATED_ANALYSIS": "Automated Analysis", "DOCUMENT_TYPE_MATCH": "Document Type Match",
    "READABLE": "Readable", "DOCUMENT_COMPLETE": "Complete Document", "REQUIRED_FIELDS_PRESENT": "Required Fields Present",
    "FIELD_CONFIDENCE": "Field Confidence", "NAME_MATCH": "Name Match", "DOB_MATCH": "Date Of Birth Match",
    "CREDENTIAL_TYPE_MATCH": "Credential Type Match", "CREDENTIAL_NUMBER_FORMAT": "Credential Number Format",
    "STATE_VALID": "State Valid", "EXPIRATION_PRESENT": "Expiration Present", "NOT_EXPIRED": "Not Expired",
    "DATE_ORDER": "Dates Consistent", "INTERNAL_CONSISTENCY": "Internal Consistency", "EXISTING_RECORD_MATCH": "Matches Staff Record",
    "DUPLICATE_CHECK": "Duplicate Check", "SUSPICIOUS_SIGNALS": "Potential Issues", "EXTERNAL_VERIFICATION": "External Verification",
}

# reason codes that point at risk (identity, conflicts, possible duplicates or alterations): these go to a manager
# even when the employee also has something to fix
RISK_CODES = {"NAME_MISMATCH", "NAME_PARTIAL_MATCH", "DOB_MISMATCH", "DUPLICATE_DOCUMENT", "CREDENTIAL_NUMBER_IN_USE",
              "POTENTIAL_ANOMALY", "MULTIPLE_NAMES", "FIELD_CONFLICT", "EXISTING_RECORD_MISMATCH", "PROFILE_MISMATCH",
              "EXTERNAL_VERIFICATION_MISMATCH", "DEA_CHECK_DIGIT_FAILED", "EXPIRATION_EARLIER_THAN_RECORD"}

US_STATES = set("AL AK AZ AR CA CO CT DE DC FL GA HI ID IL IN IA KS KY LA ME MD MA MI MN MS MO MT NE NV NH NJ NM NY NC ND OH OK "
                "OR PA RI SC SD TN TX UT VT VA WA WV WI WY PR GU VI AS MP".split())
STATE_NAMES = {"alabama": "AL", "alaska": "AK", "arizona": "AZ", "arkansas": "AR", "california": "CA", "colorado": "CO", "connecticut": "CT",
               "delaware": "DE", "district of columbia": "DC", "florida": "FL", "georgia": "GA", "hawaii": "HI", "idaho": "ID", "illinois": "IL",
               "indiana": "IN", "iowa": "IA", "kansas": "KS", "kentucky": "KY", "louisiana": "LA", "maine": "ME", "maryland": "MD",
               "massachusetts": "MA", "michigan": "MI", "minnesota": "MN", "mississippi": "MS", "missouri": "MO", "montana": "MT",
               "nebraska": "NE", "nevada": "NV", "new hampshire": "NH", "new jersey": "NJ", "new mexico": "NM", "new york": "NY",
               "north carolina": "NC", "north dakota": "ND", "ohio": "OH", "oklahoma": "OK", "oregon": "OR", "pennsylvania": "PA",
               "rhode island": "RI", "south carolina": "SC", "south dakota": "SD", "tennessee": "TN", "texas": "TX", "utah": "UT",
               "vermont": "VT", "virginia": "VA", "washington": "WA", "west virginia": "WV", "wisconsin": "WI", "wyoming": "WY",
               "puerto rico": "PR"}


# ──────────────────────────────────────────────────────────────────────────
#  What a requirement accepts and needs
# ──────────────────────────────────────────────────────────────────────────
def accepted_types(task: dict, role: str) -> set:
    doc_type = task.get("docType") or ("training_certificate" if task.get("type") == "training" else "other")
    if doc_type == "license":
        r = (role or "").lower()
        if "physician" in r and "assistant" not in r:
            return {"medical_license"}
        if r.startswith("nurse") or r in ("nurse", "rn", "lpn"):
            return {"nursing_license"}
        return {"medical_license", "nursing_license", "professional_credential"}
    if doc_type == "other":
        return set(sx.DOC_TYPES) - {"unreadable"}
    return {sx.REQUIREMENT_TYPES.get(doc_type, doc_type)}


REQUIRED_FIELDS = {
    "medical_license": ["holder_name", "license_number", "state", "credential_type", "expiration_date"],
    "nursing_license": ["holder_name", "license_number", "state", "credential_type", "expiration_date"],
    "professional_credential": ["holder_name", "license_number", "state", "credential_type", "expiration_date"],
    "bls": ["holder_name", "credential_type", "issuer", "expiration_date"],
    "acls": ["holder_name", "credential_type", "issuer", "expiration_date"],
    "pals": ["holder_name", "credential_type", "issuer", "expiration_date"],
    "training_certificate": ["holder_name", "training_type", "completion_date"],
    "dea": ["holder_name", "dea_number", "expiration_date"],
    "immunization_record": ["holder_name", "record_date"],
    "government_id": ["holder_name", "expiration_date"],
    "background_check": ["holder_name", "completion_date"],
    "other": ["holder_name"],
}
MUST_EXPIRE = {"medical_license", "nursing_license", "professional_credential", "bls", "acls", "pals", "dea", "government_id"}
CREDENTIAL_WORDS = {
    "medical_license": r"\b(md|do|m\.d\.|d\.o\.|physician|medicine|medical|surgeon|osteopath\w*)\b",
    "nursing_license": r"\b(rn|lpn|lvn|aprn|np|crna|cnm|registered nurse|practical nurse|vocational nurse|nurse practitioner|nursing)\b",
    "professional_credential": r"\b(pa|pa-c|physician assistant|aprn|np|nurse practitioner|md|do|rn)\b",
    "bls": r"\b(bls|basic life support|cpr|heartsaver)\b",
    "acls": r"\b(acls|advanced cardiovascular life support|advanced cardiac life support)\b",
    "pals": r"\b(pals|pediatric advanced life support)\b",
}
TRAINING_WORDS = {"hipaa": r"hipaa|privacy", "security_training": r"security|cyber|phishing", "althais_training": r"althais"}
LIFE_SUPPORT_MAX_DAYS = 2 * 365 + 62   # BLS/ACLS/PALS cards are issued for two years


# ──────────────────────────────────────────────────────────────────────────
#  Outcome
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class Issue:
    code: str
    route: str        # "employee" | "manager" | "info"
    employee: str     # what the employee sees (plain, specific, no accusations)
    manager: str      # what the manager sees
    check: str = ""


@dataclass
class ReviewOutcome:
    decision: str
    reason_codes: list
    checks: dict                       # check -> PASS / FAIL / WARN / NOT_RUN / NOT_APPLICABLE
    issues: list                       # [Issue]
    detected_type: str = ""
    type_confidence: float = 0.0
    fields: dict = field(default_factory=dict)       # key -> normalized value
    field_meta: dict = field(default_factory=dict)   # key -> {confidence, page, location, raw, others}
    external: dict = field(default_factory=dict)
    employee_message: str = ""
    manager_summary: list = field(default_factory=list)
    processor: str = ""

    def issues_json(self) -> list:
        return [{"code": i.code, "route": i.route, "employee": i.employee, "manager": i.manager, "check": i.check} for i in self.issues]


# ──────────────────────────────────────────────────────────────────────────
#  File checks (before anything reads the document)
# ──────────────────────────────────────────────────────────────────────────
def image_size(content_type: str, data: bytes):
    """(width, height) for PNG and JPEG, else None."""
    try:
        if content_type == "image/png" and data[12:16] == b"IHDR":
            return struct.unpack(">II", data[16:24])
        if content_type == "image/jpeg":
            i = 2
            while i + 9 < len(data):
                if data[i] != 0xFF:
                    i += 1
                    continue
                marker = data[i + 1]
                if marker in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                    h, w = struct.unpack(">HH", data[i + 5:i + 9])
                    return w, h
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                i += 2 + struct.unpack(">H", data[i + 2:i + 4])[0]
    except Exception:
        return None
    return None


def file_issues(content_type: str, data: bytes) -> list:
    out = []
    if content_type == "application/pdf":
        if b"%%EOF" not in data[-4096:]:
            out.append(Issue("CORRUPTED_FILE", "employee", "The PDF looks damaged or incomplete, so we couldn't open all of it. Please save or scan it again and upload the new file.",
                             "The PDF is truncated or damaged.", "FILE_INTEGRITY"))
        if b"/Encrypt" in data:
            out.append(Issue("PROTECTED_FILE", "employee", "The PDF is password-protected, so we can't read it. Please upload a copy without a password.",
                             "The PDF is password-protected.", "FILE_INTEGRITY"))
    size = image_size(content_type, data)
    if size and (max(size) < 800 or min(size) < 400):
        out.append(Issue("LOW_RESOLUTION", "employee", f"The image is too small ({size[0]}×{size[1]} pixels) to read reliably. Please upload a larger photo or a PDF.",
                         f"The image is only {size[0]}×{size[1]} pixels.", "FILE_INTEGRITY"))
    return out


# ──────────────────────────────────────────────────────────────────────────
#  Identity
# ──────────────────────────────────────────────────────────────────────────
_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v", "md", "do", "rn", "np", "pa", "pac", "lpn", "lvn", "phd", "dds", "dmd", "mr", "mrs",
             "ms", "miss", "dr", "aprn", "fnp", "bsn", "msn", "cna", "ma", "mba", "mph", "crna", "dnp", "facp", "facep", "esq"}


def name_tokens(name: str) -> list:
    s = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode().lower()
    if "," in s:   # "SMITH, JAMES R" -> "james r smith"
        last, _, rest = s.partition(",")
        s = f"{rest} {last}"
    toks = [t for t in re.split(r"[^a-z'-]+", s.replace(".", " ")) if t]
    toks = [t.strip("'-") for t in toks if t.strip("'-") and t.strip("'-").replace("-", "") not in _SUFFIXES]
    return toks


def match_name(doc_name: str, staff: dict) -> str:
    """MATCH, PARTIAL (surname matches, given name only loosely: initial, shortening) or MISMATCH.
    Middle names/initials, suffixes, credentials, case, spacing and "LAST, FIRST" order don't matter."""
    toks = name_tokens(doc_name)
    if len(toks) < 2:
        return "MISMATCH" if toks else "MISSING"
    firsts = {t for n in staff.get("first", []) for t in name_tokens(n)[:1]}
    lasts = set()
    for n in staff.get("last", []):
        t = name_tokens(n)
        if t:
            lasts.add(t[-1])
            lasts.update(t[-1].split("-"))
    d_first, d_last = toks[0], toks[-1]
    doc_lasts = {d_last, *d_last.split("-")} | set(toks[1:])
    if not (lasts & doc_lasts):
        return "MISMATCH"
    if d_first in firsts:
        return "MATCH"
    if any(len(d_first) == 1 and f.startswith(d_first) for f in firsts):
        return "PARTIAL"
    if any(len(d_first) >= 3 and len(f) >= 3 and (f.startswith(d_first) or d_first.startswith(f)) for f in firsts):
        return "PARTIAL"
    return "MISMATCH"


# ──────────────────────────────────────────────────────────────────────────
#  Field rules
# ──────────────────────────────────────────────────────────────────────────
def parse_date(v: str):
    if not v or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", v):
        return None
    try:
        return dt.date.fromisoformat(v)
    except ValueError:
        return None


def normalize_state(v: str) -> str:
    s = (v or "").strip()
    if s.upper() in US_STATES:
        return s.upper()
    return STATE_NAMES.get(s.lower(), "")


def dea_check_digit_ok(num: str) -> bool:
    """DEA numbers: two letters and seven digits; the last digit is a checksum of the first six."""
    m = re.fullmatch(r"[A-Z]{2}(\d{7})", (num or "").upper().replace(" ", ""))
    if not m:
        return False
    d = [int(c) for c in m.group(1)]
    return (d[0] + d[2] + d[4] + 2 * (d[1] + d[3] + d[5])) % 10 == d[6]


def _a(label: str) -> str:
    return ("an " if label[:1].lower() in "aeiou" else "a ") + label


def _num(fields: dict) -> str:
    return (fields.get("license_number") or fields.get("credential_number") or fields.get("dea_number") or "").strip()


def _norm_id(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


# ──────────────────────────────────────────────────────────────────────────
#  External verification (credential_verification.py: real sources only, NOT_RUN otherwise)
# ──────────────────────────────────────────────────────────────────────────
def external_verify(doc_type: str, fields: dict, context: dict = None) -> dict:
    import credential_verification
    return credential_verification.verify(doc_type, fields, context or {})


# ──────────────────────────────────────────────────────────────────────────
#  The review
# ──────────────────────────────────────────────────────────────────────────
@dataclass
class ReviewContext:
    task: dict                      # the onboarding requirement (key, title, type, docType, credentialType, trainingKey)
    role: str
    staff_names: dict               # {"first": [...], "last": [...]} from the staff record
    staff_dob: str = ""
    profile: dict = field(default_factory=dict)          # the person's professional info (license_number, license_state...)
    existing_credential: Optional[dict] = None           # verified credential already on file for this requirement
    duplicate_file_owner: bool = False                   # the same file is on another staff member's record
    same_file_as_rejected: bool = False                  # the employee re-uploaded a file that was already sent back
    number_in_use_by_other: bool = False                 # another staff member's credential has this number
    training_valid_months: int = 0
    npi: str = ""                                        # from the staff record, for NPI Registry checks
    policy: dict = field(default_factory=lambda: dict(DEFAULT_POLICY))
    today: dt.date = field(default_factory=dt.date.today)


def review(ctx: ReviewContext, content_type: str, data: bytes, result: sx.AnalysisResult) -> ReviewOutcome:
    pol = {**DEFAULT_POLICY, **(ctx.policy or {})}
    thr = float(pol["minConfidence"])
    title = ctx.task.get("title") or "document"
    checks = {k: NOT_RUN for k in CHECK_LABELS}
    issues: list = []

    def add(code, route, employee, manager, check=""):
        issues.append(Issue(code, route, employee, manager, check))
        if check:
            checks[check] = FAIL if route != "info" else (WARN if checks.get(check) in (NOT_RUN, PASS) else checks[check])

    # 1. file
    fi = file_issues(content_type, data)
    checks["FILE_INTEGRITY"] = PASS
    for i in fi:
        add(i.code, i.route, i.employee, i.manager, "FILE_INTEGRITY")
    if ctx.same_file_as_rejected:
        add("SAME_FILE_REUPLOADED", "employee", f"This is the same file you uploaded before for your {title}, which couldn't be verified. Please upload a new file.",
            "The employee re-uploaded a file that was already sent back.", "DUPLICATE_CHECK")

    # 2. analysis
    a = result.analysis if result.status == "analyzed" else None
    if a is None:
        checks["AUTOMATED_ANALYSIS"] = FAIL if result.status == "failed" else NOT_RUN
        if result.note in ("UNSUPPORTED_FOR_ANALYSIS", "IMAGE_TOO_LARGE_FOR_ANALYSIS"):
            why = "HEIC photos can't be checked automatically" if result.note == "UNSUPPORTED_FOR_ANALYSIS" else "the photo is too large to check automatically"
            add(result.note, "employee", f"We couldn't check your {title} because {why}. Please upload it as a PDF, or a JPG or PNG under 5 MB.",
                f"Althais couldn't analyze this file ({why}).", "AUTOMATED_ANALYSIS")
        elif result.note == "AUTOMATED_REVIEW_OFF":
            add("AUTOMATED_REVIEW_OFF", "manager", "", "Automated review is turned off for your clinic, so this needs your review.", "AUTOMATED_ANALYSIS")
        elif result.status == "failed":
            add("AUTOMATED_REVIEW_FAILED", "manager", "", f"Automatic review didn't complete ({result.note or 'error'}). Please review it yourself.", "AUTOMATED_ANALYSIS")
        else:
            add("AUTOMATED_REVIEW_UNAVAILABLE", "manager", "", "Automatic document review isn't connected, so this needs your review.", "AUTOMATED_ANALYSIS")
        return _decide(ctx, pol, checks, issues, "", 0.0, {}, {}, {}, result.processor)
    checks["AUTOMATED_ANALYSIS"] = PASS

    # 3. classification
    accepted = accepted_types(ctx.task, ctx.role)
    detected, tconf = a.document_type, float(a.document_type_confidence or 0)
    want = sx.DOC_TYPES.get(sorted(accepted)[0], "document") if len(accepted) == 1 else title
    if detected == "unreadable":
        checks["DOCUMENT_TYPE_MATCH"] = FAIL
    elif detected in accepted and tconf >= thr:
        checks["DOCUMENT_TYPE_MATCH"] = PASS
    elif detected in accepted:
        add("DOCUMENT_TYPE_UNCLEAR", "manager", "", f"Althais isn't sure this is {_a(want)} (confidence {tconf:.0%}).", "DOCUMENT_TYPE_MATCH")
    elif tconf >= thr:
        add("WRONG_DOCUMENT_TYPE", "employee",
            f"This looks like {_a(sx.DOC_TYPES.get(detected, 'different document'))}, but this requirement needs your {want}. Please upload your {want}.",
            f"Uploaded document looks like {_a(sx.DOC_TYPES.get(detected, detected))}, not {_a(want)}.", "DOCUMENT_TYPE_MATCH")
    else:
        add("DOCUMENT_TYPE_UNCLEAR", "manager", "", f"Althais couldn't tell what kind of document this is (best guess: {sx.DOC_TYPES.get(detected, detected)}).",
            "DOCUMENT_TYPE_MATCH")
    if ctx.task.get("docType") == "other" and detected != "unreadable":
        add("CUSTOM_REQUIREMENT", "manager", "", f"“{title}” is a clinic-specific requirement, so it needs a person to confirm it.", "")

    # 4. quality
    major = [q for q in a.quality_issues if q.severity == "major"]
    if not a.readable or detected == "unreadable":
        checks["READABLE"] = FAIL
        add("UNREADABLE", "employee", f"We couldn't read your {title}. Please upload a clear, well-lit photo or a PDF where all the text is visible.",
            "The document couldn't be read.", "READABLE")
    else:
        checks["READABLE"] = PASS
    if major:
        what = {"BLURRY": "it's blurry", "CUT_OFF_EDGES": "part of it is cut off", "GLARE": "glare covers part of it",
                "OBSTRUCTED": "something is covering part of it", "LOW_RESOLUTION": "the image resolution is too low",
                "MISSING_PAGES": "a page or side is missing", "UNREADABLE_TEXT": "some text can't be read",
                "INCOMPLETE": "it looks incomplete", "CORRUPTED": "the file looks damaged", "OTHER": "the image quality is too poor"}
        reasons = ", and ".join(dict.fromkeys(what.get(q.code, "the image quality is too poor") for q in major))
        add("POOR_QUALITY", "employee", f"We couldn't verify your {title} because {reasons}. Please upload a clearer image or a PDF.",
            "Quality problems: " + "; ".join(q.detail or q.code for q in major), "READABLE")
    if not a.looks_complete:
        add("INCOMPLETE_DOCUMENT", "employee", f"Your {title} looks incomplete (a page, side or edge seems to be missing). Please upload the whole document.",
            "The document appears incomplete.", "DOCUMENT_COMPLETE")
    else:
        checks["DOCUMENT_COMPLETE"] = PASS

    # 5. fields (for the type the requirement expects, or what was detected when it's acceptable)
    stype = detected if detected in accepted and detected in sx.FIELD_SCHEMAS else sx.schema_type(
        ctx.task.get("docType") or ("training_certificate" if ctx.task.get("type") == "training" else "other"), ctx.task.get("credentialType", ""))
    labels = dict(sx.FIELD_SCHEMAS.get(stype, sx.FIELD_SCHEMAS["other"]))
    fmap = {f.key: f for f in a.fields if f.key in labels}
    fields = {k: (f.value or "").strip() for k, f in fmap.items()}
    meta = {k: {"confidence": round(float(f.confidence or 0), 3), "page": f.page, "location": f.location, "raw": f.raw_text,
                "others": list(f.other_values_seen or [])} for k, f in fmap.items()}
    if "state" in fields and fields["state"]:
        fields["state"] = normalize_state(fields["state"]) or fields["state"]
    required = REQUIRED_FIELDS.get(stype, ["holder_name"])
    if checks["DOCUMENT_TYPE_MATCH"] == PASS or detected in accepted:
        missing, low = [], []
        for k in required:
            f = fmap.get(k)
            if not fields.get(k):
                if f and f.raw_text and k in sx.DATE_FIELDS:
                    add("DATE_AMBIGUOUS", "manager", "", f"{labels[k]} is printed as “{f.raw_text}”, which Althais couldn't read as one definite date.", "REQUIRED_FIELDS_PRESENT")
                else:
                    missing.append(k)
            elif float(f.confidence or 0) < thr:
                low.append(k)
        if missing:
            checks["REQUIRED_FIELDS_PRESENT"] = FAIL
            names = ", ".join(labels[k].lower() for k in missing)
            if major or not a.readable:
                pass   # already asked for a clearer copy
            elif stype in MUST_EXPIRE and missing == ["expiration_date"]:
                add("EXPIRATION_MISSING", "manager", "", f"No expiration date was found on this {sx.DOC_TYPES.get(stype, 'document')}.", "EXPIRATION_PRESENT")
            else:
                add("MISSING_REQUIRED_FIELD", "manager", "", f"Couldn't find: {names}.", "REQUIRED_FIELDS_PRESENT")
        else:
            checks["REQUIRED_FIELDS_PRESENT"] = PASS if checks["REQUIRED_FIELDS_PRESENT"] == NOT_RUN else checks["REQUIRED_FIELDS_PRESENT"]
        if low:
            add("CRITICAL_FIELD_LOW_CONFIDENCE", "manager", "",
                "Read with low confidence: " + ", ".join(f"{labels[k]} ({float(fmap[k].confidence):.0%})" for k in low) + ".", "FIELD_CONFIDENCE")
        else:
            checks["FIELD_CONFIDENCE"] = PASS
        # conflicting values for the same field inside the document
        conflicts = [k for k in required if k in fmap and [v for v in fmap[k].other_values_seen if v and v != fields.get(k)]]
        if conflicts:
            add("FIELD_CONFLICT", "manager", "", "The document shows different values for: " + ", ".join(
                f"{labels[k]} ({fields.get(k) or fmap[k].raw_text} vs {', '.join(fmap[k].other_values_seen)})" for k in conflicts) + ".", "INTERNAL_CONSISTENCY")
        holders = [n for pg in a.pages for n in pg.holder_names if n.strip()]
        distinct = []
        for n in holders:
            if not any(match_name(n, {"first": [d], "last": [d]}) == "MATCH" for d in distinct):
                distinct.append(n)
        if len(distinct) > 1:
            add("MULTIPLE_NAMES", "manager", "", "Different names appear on different pages: " + ", ".join(distinct) + ".", "INTERNAL_CONSISTENCY")
        if checks["INTERNAL_CONSISTENCY"] == NOT_RUN:
            checks["INTERNAL_CONSISTENCY"] = PASS

        # 6. identity
        holder = fields.get("holder_name", "")
        if holder:
            m = match_name(holder, ctx.staff_names)
            if m == "MATCH":
                checks["NAME_MATCH"] = PASS
            elif m == "PARTIAL":
                add("NAME_PARTIAL_MATCH", "manager", "", f"Name on the document (“{holder}”) only partly matches the staff record.", "NAME_MATCH")
            else:
                add("NAME_MISMATCH", "manager", "", f"Name on the document (“{holder}”) doesn't match the staff record.", "NAME_MATCH")
        if stype == "government_id" and fields.get("date_of_birth") and ctx.staff_dob:
            if fields["date_of_birth"] != ctx.staff_dob:
                add("DOB_MISMATCH", "manager", "", "Date of birth on the ID doesn't match the one the employee entered.", "DOB_MATCH")
            else:
                checks["DOB_MATCH"] = PASS
        else:
            checks["DOB_MATCH"] = NA

        # 7. document-specific rules
        ctype = (fields.get("credential_type") or "").lower()
        if stype in CREDENTIAL_WORDS and ctype:
            if re.search(CREDENTIAL_WORDS[stype], ctype):
                checks["CREDENTIAL_TYPE_MATCH"] = PASS
            else:
                add("CREDENTIAL_TYPE_MISMATCH", "manager", "", f"Credential type “{fields.get('credential_type')}” isn't what this requirement expects.", "CREDENTIAL_TYPE_MATCH")
        elif stype == "training_certificate":
            words = TRAINING_WORDS.get(ctx.task.get("trainingKey", ""))
            tt = fields.get("training_type", "")
            if words and tt and not re.search(words, tt.lower()):
                add("WRONG_TRAINING", "employee", f"This certificate is for “{tt}”, not {title}. Please upload the certificate for {title}.",
                    f"Certificate is for “{tt}”.", "CREDENTIAL_TYPE_MATCH")
            else:
                checks["CREDENTIAL_TYPE_MATCH"] = PASS if tt else NOT_RUN
        else:
            checks["CREDENTIAL_TYPE_MATCH"] = NA
        number = _num(fields)
        if stype == "dea" and number:
            if dea_check_digit_ok(number):
                checks["CREDENTIAL_NUMBER_FORMAT"] = PASS
            else:
                add("DEA_CHECK_DIGIT_FAILED", "manager", "", f"DEA number {number} doesn't pass the DEA check-digit test.", "CREDENTIAL_NUMBER_FORMAT")
        elif stype in ("medical_license", "nursing_license", "professional_credential") and number:
            if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .\-/]{1,24}", number):
                checks["CREDENTIAL_NUMBER_FORMAT"] = PASS
            else:
                add("CREDENTIAL_NUMBER_FORMAT", "manager", "", f"License number “{number}” has an unusual format.", "CREDENTIAL_NUMBER_FORMAT")
        else:
            checks["CREDENTIAL_NUMBER_FORMAT"] = NA
        if "state" in labels and fields.get("state"):
            if fields["state"] in US_STATES:
                checks["STATE_VALID"] = PASS
            else:
                add("STATE_INVALID", "manager", "", f"“{fields['state']}” isn't a recognized US state.", "STATE_VALID")
        else:
            checks["STATE_VALID"] = NA

        # 8. dates
        dates = {}
        for k in sx.DATE_FIELDS:
            if fields.get(k):
                d = parse_date(fields[k])
                if d is None:
                    add("DATE_UNPARSEABLE", "manager", "", f"{labels.get(k, k)} “{fields[k]}” isn't a valid date.", "DATE_ORDER")
                else:
                    dates[k] = d
        exp, iss, done = dates.get("expiration_date"), dates.get("issue_date"), dates.get("completion_date")
        if exp:
            checks["EXPIRATION_PRESENT"] = PASS
            if exp < ctx.today:
                add("DOCUMENT_EXPIRED", "employee", f"Your {title} expired on {exp.strftime('%B %-d, %Y')}. Please upload your current one.",
                    f"Expired {exp.isoformat()}.", "NOT_EXPIRED")
            else:
                checks["NOT_EXPIRED"] = PASS
                if (exp - ctx.today).days <= int(pol["expiringSoonDays"]):
                    add("EXPIRING_SOON", "info", "", f"Expires {exp.isoformat()}, within {pol['expiringSoonDays']} days.", "NOT_EXPIRED")
        elif stype not in MUST_EXPIRE:
            checks["EXPIRATION_PRESENT"] = checks["NOT_EXPIRED"] = NA
        order_ok = True
        if iss and exp and iss > exp:
            order_ok = False
            add("DATE_ORDER_INVALID", "manager", "", "Issue date is after the expiration date.", "DATE_ORDER")
        if iss and iss > ctx.today:
            order_ok = False
            add("ISSUE_DATE_IN_FUTURE", "manager", "", f"Issue date {iss.isoformat()} is in the future.", "DATE_ORDER")
        if done and done > ctx.today:
            order_ok = False
            add("COMPLETION_DATE_IN_FUTURE", "manager", "", f"Completion date {done.isoformat()} is in the future.", "DATE_ORDER")
        if stype in ("bls", "acls", "pals") and iss and exp and (exp - iss).days > LIFE_SUPPORT_MAX_DAYS:
            order_ok = False
            add("UNUSUAL_VALIDITY_PERIOD", "manager", "", f"Valid for {(exp - iss).days} days; these cards are normally valid for two years.", "DATE_ORDER")
        if stype == "training_certificate" and done and ctx.training_valid_months and \
                (ctx.today - done).days > ctx.training_valid_months * 30.44 and not (exp and exp >= ctx.today):
            add("TRAINING_OUT_OF_DATE", "employee", f"This certificate is from {done.strftime('%B %Y')}, which is too long ago. Please complete the training again and upload the new certificate.",
                f"Completed {done.isoformat()}, older than {ctx.training_valid_months} months.", "NOT_EXPIRED")
        if order_ok and checks["DATE_ORDER"] == NOT_RUN and dates:
            checks["DATE_ORDER"] = PASS

        # 9. against the staff record
        prev = ctx.existing_credential
        compared = False
        if prev and prev.get("status") == "verified":
            compared = True
            pn, ps = _norm_id(prev.get("identifier")), (prev.get("state") or "").upper()
            if pn and number and _norm_id(number) != pn or ps and fields.get("state") and fields["state"] != ps:
                add("EXISTING_RECORD_MISMATCH", "manager", "", f"Differs from the verified record on file ({ps or '-'} #{prev.get('identifier') or '-'}): "
                    f"this document shows {fields.get('state') or '-'} #{number or '-'}. It may be a new credential, a renewal, or an upload mistake.",
                    "EXISTING_RECORD_MATCH")
            elif exp and prev.get("expires") and parse_date(prev["expires"]) and exp < parse_date(prev["expires"]):
                add("EXPIRATION_EARLIER_THAN_RECORD", "manager", "", f"Expires {exp.isoformat()}, earlier than the one on file ({prev['expires']}). "
                    "It may be an older copy uploaded by mistake.", "EXISTING_RECORD_MATCH")
            elif exp and prev.get("expires") and parse_date(prev["expires"]) and exp > parse_date(prev["expires"]):
                add("RENEWAL", "info", "", f"Renewal of the credential on file (it expired or expires {prev['expires']}).", "")
        if stype in ("medical_license", "nursing_license", "professional_credential"):
            pl, pst = _norm_id(ctx.profile.get("license_number")), (ctx.profile.get("license_state") or "").upper()
            if pl or pst:
                compared = True
                if pl and number and _norm_id(number) != pl or pst and fields.get("state") and fields["state"] != pst:
                    add("PROFILE_MISMATCH", "manager", "", f"Differs from the license the employee entered in My Information ({pst or '-'} #{ctx.profile.get('license_number') or '-'}).",
                        "EXISTING_RECORD_MATCH")
        if checks["EXISTING_RECORD_MATCH"] == NOT_RUN:
            checks["EXISTING_RECORD_MATCH"] = PASS if compared else NA

    # 10. duplicates
    if ctx.duplicate_file_owner:
        add("DUPLICATE_DOCUMENT", "manager", "", "The same file is already on another staff member's record.", "DUPLICATE_CHECK")
    if ctx.number_in_use_by_other:
        add("CREDENTIAL_NUMBER_IN_USE", "manager", "", "Another staff member has a credential with this same number.", "DUPLICATE_CHECK")
    if checks["DUPLICATE_CHECK"] == NOT_RUN:
        checks["DUPLICATE_CHECK"] = PASS

    # 11. potential issues
    levels = {"low": 0, "medium": 1, "high": 2}
    cut = levels.get(pol["anomalyThreshold"], 1)
    flagged = [x for x in a.anomalies if levels.get(x.severity, 0) >= cut]
    if flagged:
        add("POTENTIAL_ANOMALY", "manager", "", "Potential issue detected: " + "; ".join(x.detail or x.code for x in flagged) +
            ". This needs a person to look; it isn't a finding of wrongdoing.", "SUSPICIOUS_SIGNALS")
    else:
        checks["SUSPICIOUS_SIGNALS"] = PASS
        for x in a.anomalies:
            add("MINOR_OBSERVATION", "info", "", x.detail or x.code, "SUSPICIOUS_SIGNALS")

    # 12. external verification
    ext = {}
    if detected in accepted:
        ext = external_verify(stype, fields, {"npi": ctx.npi, "names": ctx.staff_names})
        required_ext = ctx.task.get("key") in (pol.get("requireExternal") or [])
        if ext["status"] == "VERIFIED":
            checks["EXTERNAL_VERIFICATION"] = PASS
        elif ext["status"] in ("MISMATCH", "NOT_FOUND"):
            add("EXTERNAL_VERIFICATION_MISMATCH", "manager", "", f"External verification conflict ({ext.get('source') or 'outside source'}): {ext.get('details', '')}", "EXTERNAL_VERIFICATION")
        elif required_ext:
            why = ext.get("details") or "it couldn't be run"
            add("EXTERNAL_VERIFICATION_UNAVAILABLE", "manager", "", f"Your clinic requires outside verification for this, and it wasn't confirmed: {why}", "EXTERNAL_VERIFICATION")
        elif ext["status"] == "UNCONFIRMED":
            add("EXTERNAL_UNCONFIRMED", "info", "", ext.get("details", ""), "EXTERNAL_VERIFICATION")
        else:
            checks["EXTERNAL_VERIFICATION"] = NOT_RUN if ext["status"] == "NOT_RUN" else WARN

    return _decide(ctx, pol, checks, issues, detected, tconf, fields, meta, ext, result.processor)


def _decide(ctx, pol, checks, issues, detected, tconf, fields, meta, ext, processor) -> ReviewOutcome:
    title = ctx.task.get("title") or "document"
    emp = [i for i in issues if i.route == "employee"]
    mgr = [i for i in issues if i.route == "manager"]
    risky = [i for i in mgr if i.code in RISK_CODES]
    if risky:
        decision = MANAGER_REVIEW
    elif emp:
        decision = ACTION_REQUIRED
    elif mgr:
        decision = MANAGER_REVIEW
    elif not pol.get("autoApprove", True):
        decision = MANAGER_REVIEW
        mgr.append(Issue("AUTO_APPROVAL_OFF", "manager", "", "Every check passed. Your clinic reviews all documents before approval.", ""))
        issues.append(mgr[-1])
    else:
        decision = AUTO_APPROVED
    codes = list(dict.fromkeys(i.code for i in (risky or emp or mgr) if i.route != "info")) if decision != AUTO_APPROVED else []
    if decision == ACTION_REQUIRED:
        message = emp[0].employee
    elif decision == MANAGER_REVIEW:
        message = f"We've sent your {title} to your clinic manager because we found information that needs review."
    else:
        exp = parse_date(fields.get("expiration_date", ""))
        message = f"Your {title} is verified." + (f" Expires {exp.strftime('%B %-d, %Y')}." if exp else "")
    return ReviewOutcome(decision=decision, reason_codes=codes, checks=checks, issues=issues, detected_type=detected,
                         type_confidence=tconf, fields=fields, field_meta=meta, external=ext, employee_message=message,
                         manager_summary=[i.manager for i in issues if i.route == "manager"] + ([i.manager for i in emp] if decision == MANAGER_REVIEW else []),
                         processor=f"{PROCESSOR_VERSION}+{processor}" if processor else PROCESSOR_VERSION)
