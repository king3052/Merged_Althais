"""The deterministic checks every AI-built claim passes through: plain rules from public billing guidance, run in
Althais's own code. No patient data goes to any model here, the same claim always gets the same result, and every
change or warning says which rule made it and which reference entry (rag_corpus.py) explains it.

This sits next to the rules main._claim_auto_work already applies (and coding_rules.py, which sets time-based codes) (services.py: tests and procedures the note shows,
modifier 25 with minor procedures; _em_floor; the NCCI pair edits in the claim check). apply() adds:

  QW         CLIA-waived tests: adds QW for Medicare and Medicaid, warns for other payers
  VACCINE    a vaccine without an administration code (or the reverse), the under-19 counseling codes, Z23
  NEW-EST    a new-patient visit code for a patient the note says was seen in the last three years (or the reverse)
  TIME       the visit level the documented total time supports, against the level billed
  LACERATION two repair codes of the same type and body group, which should be one code for the summed length
  EXT-CAUSE  an external cause code (V00-Y99) listed first: moved after the diagnoses it explains
  INJURY-7TH an injury code's 7th character against the note (A for active treatment, D for healing visits)
  DUPLICATE  the same procedure code twice without a repeat modifier

Fixes are made only where the rule is mechanical (QW for Medicare and Medicaid, diagnosis order). Everything that
depends on judgment is a warning for the biller, never a silent change.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional, Tuple

WAIVED_QW = {"87880": "rapid strep", "87804": "rapid flu", "87807": "rapid RSV", "87811": "rapid COVID antigen", "86308": "mono spot"}
VACCINES = {"90715", "90714", "90686", "90688", "90674", "90756", "90662", "90682", "90732", "90670", "90677", "90746", "90739",
            "90636", "90632", "90633", "90707", "90710", "90716", "90651", "90619", "90734", "90620", "90621", "90649", "90750"}
VACCINE_ADMIN = {"90471", "90472", "90473", "90474", "90460", "90461", "G0008", "G0009", "G0010", "90480"}
NEW_EM = {"99202": 15, "99203": 30, "99204": 45, "99205": 60}
EST_EM = {"99212": 10, "99213": 20, "99214": 30, "99215": 40}
LAC_FAMILIES = [("simple repair of scalp, neck, trunk, arms or legs", "12001", "12007"),
                ("simple repair of face, ears, eyelids, nose or lips", "12011", "12018"),
                ("intermediate repair of scalp, trunk, arms or legs", "12031", "12037"),
                ("intermediate repair of neck, hands or feet", "12041", "12047"),
                ("intermediate repair of face, ears, eyelids, nose or lips", "12051", "12057")]
REPEAT_MODS = {"76", "77", "91", "59", "XE", "XS", "XP", "XU", "RT", "LT"}
_GOV_PAYER = re.compile(r"(?i)medicare|medicaid|tmhp|novitas|chip\b")


def _is_cpt(c) -> bool:
    return str(c.get("type", "")).upper().startswith(("CPT", "HCPCS"))


def _mods(c) -> List[str]:
    return [m.upper() for m in str(c.get("modifier") or "").replace(",", " ").split()]


def _code(c) -> str:
    return str(c.get("code", "")).strip().upper()


def _age(header: str, note: str) -> Optional[int]:
    import deid
    for text in (header or "", note or ""):
        m = re.search(r"(?i)\bDOB\s*[:\-]?\s*([0-9/\-]{8,10}|[A-Za-z]{3,9}\.? \d{1,2},? \d{4})", text)
        if m:
            dob = deid._parse_date(m.group(1))
            dos_m = re.search(r"(?i)date of service\s*[:\-]?\s*([0-9/\-]{8,10})", (header or "") + "\n" + (note or ""))
            ref = (deid._parse_date(dos_m.group(1)) if dos_m else None) or dt.date.today()
            if dob:
                return ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))
    m = re.search(r"\b(\d{1,3})[\s\-]?(?:y/?o|yo|years?[\s\-]old)\b", note or "", re.I)
    return int(m.group(1)) if m else None


def _payer(header: str) -> str:
    m = re.search(r"(?i)\bpayer\s+([^,]+)", header or "")
    return m.group(1).strip() if m else ""


class Result:
    def __init__(self):
        self.notes: List[str] = []         # what was changed, shown as "Coding rule: ..."
        self.flags: List[str] = []         # what the biller should look at, shown on the claim check
        self.audit: List[dict] = []        # the same, structured: rule, action, detail, ref

    def fix(self, rule: str, detail: str, ref: str) -> None:
        self.notes.append(f"[{rule}] {detail} (ref: {ref})")
        self.audit.append({"rule": rule, "action": "changed", "detail": detail, "ref": ref})

    def warn(self, rule: str, detail: str, ref: str) -> None:
        self.flags.append(f"[{rule}] {detail} (ref: {ref})")
        self.audit.append({"rule": rule, "action": "check", "detail": detail, "ref": ref})


def apply(codes: List[dict], note: str = "", header: str = "") -> Tuple[List[dict], Result]:
    """Run every rule. Returns the (possibly fixed) codes, in a new list, and what was changed or flagged."""
    out = [dict(c) for c in codes or []]
    r = Result()
    payer = _payer(header)
    gov = bool(_GOV_PAYER.search(payer))
    cpts = [c for c in out if _is_cpt(c)]
    dxs = [c for c in out if not _is_cpt(c)]
    codes_set = {_code(c) for c in cpts}
    low = (note or "").lower()

    # QW
    for c in cpts:
        code = _code(c)
        if code in WAIVED_QW and "QW" not in _mods(c):
            if gov:
                c["modifier"] = " ".join(_mods(c) + ["QW"]).strip()
                r.fix("QW", f"Added modifier QW to {code} ({WAIVED_QW[code]}): {payer} requires it on CLIA-waived tests, with the clinic's CLIA number on the claim.", "mod-qw")
            else:
                r.warn("QW", f"{code} ({WAIVED_QW[code]}) is a CLIA-waived test: check whether {payer or 'this payer'} wants modifier QW, and that the claim carries the clinic's CLIA number.", "mod-qw")

    # VACCINE
    vax = sorted(codes_set & VACCINES)
    adm = sorted(codes_set & VACCINE_ADMIN)
    age = _age(header, note)
    if vax and not adm:
        if gov and re.search(r"(?i)medicare", payer) and any(v in {"90686", "90688", "90674", "90756", "90662", "90682"} for v in vax):
            how = "G0008 (Medicare's flu shot administration code)"
        elif age is not None and age < 19 and re.search(r"counsel", low):
            how = "90460 (under 19, with the clinician's counseling documented)"
        else:
            how = "90471 for the first vaccine and 90472 for each additional one"
        r.warn("VACCINE", f"Vaccine {', '.join(vax)} is billed without an administration code: add {how}.", "svc-vaccines")
    if adm and not vax:
        r.warn("VACCINE", f"Administration code {', '.join(adm)} is billed without the vaccine product code.", "svc-vaccines")
    if (vax or adm) and age is not None and age < 19 and "90471" in codes_set and re.search(r"counsel", low):
        r.warn("VACCINE", "The patient is under 19 and counseling is documented: 90460/90461 may apply instead of 90471/90472.", "svc-vaccines")
    if vax and not any(_code(d).replace(".", "") == "Z23" for d in dxs):
        r.warn("VACCINE", "A vaccine is billed but Z23 (encounter for immunization) isn't among the diagnoses.", "svc-vaccines")

    # NEW-EST
    new_codes = sorted(codes_set & set(NEW_EM))
    est_codes = sorted(codes_set & set(EST_EM))
    seen = re.search(r"(?i)\bestablished patient\b|last (?:seen|visit|office visit)[^.\n]{0,40}?(20\d\d)", note or "")
    if new_codes and seen:
        yr = seen.group(1)
        recent = not yr or int(yr) >= (dt.date.today().year - 3)
        if recent:
            r.warn("NEW-EST", f"{new_codes[0]} is a new-patient code, but the note says the patient was seen here before"
                              f"{' in ' + yr if yr else ''}: within three years, the patient is established (99212-99215).", "em-new-established")
    if est_codes and re.search(r"(?i)\bnew patient\b", note or "") and not re.search(r"(?i)\bnot a new patient\b", note or ""):
        r.warn("NEW-EST", f"{est_codes[0]} is an established-patient code, but the note calls this a new patient.", "em-new-established")

    # TIME
    m = re.search(r"(?i)(?:total time|spent)\D{0,25}?(\d{1,3})\s*(?:min|minutes)\b|(\d{1,3})\s*(?:min|minutes)\s+(?:total|spent)", note or "")
    if m:
        minutes = int(m.group(1) or m.group(2))
        for table in (EST_EM, NEW_EM):
            billed = [c for c in sorted(codes_set & set(table))]
            if not billed:
                continue
            supported = max((code for code, mins in table.items() if minutes >= mins), default=None, key=lambda k: table[k])
            b = billed[0]
            if supported is None:
                r.warn("TIME", f"The note documents {minutes} minutes, below the lowest time for {b}; the level must rest on medical decision making.", "em-time")
            elif table[b] > table[supported]:
                r.warn("TIME", f"The note documents {minutes} minutes, which supports {supported} by time; {b} needs medical decision making that supports it.", "em-time")
            elif table[b] < table[supported]:
                r.warn("TIME", f"The note documents {minutes} minutes, which supports {supported} by time (billed {b}).", "em-time")

    # LACERATION
    for label, lo, hi in LAC_FAMILIES:
        fam = [c for c in cpts if lo <= _code(c) <= hi]
        if len(fam) > 1:
            r.warn("LACERATION", f"{' and '.join(_code(c) for c in fam)} are both {label}: add the lengths and bill one code for the total.", "svc-laceration")

    # EXT-CAUSE
    if dxs and re.match(r"^[V-Y]\d", _code(dxs[0])):
        ext = [d for d in dxs if re.match(r"^[V-Y]\d", _code(d))]
        rest = [d for d in dxs if d not in ext]
        if rest:
            first = _code(ext[0])
            order = iter(rest + ext)
            out = [c if _is_cpt(c) else next(order) for c in out]
            r.fix("EXT-CAUSE", f"Moved {first} after {_code(rest[0])}: an external cause code explains how an injury happened and is never listed first.", "dx-outpatient")
        else:
            r.warn("EXT-CAUSE", f"{_code(ext[0])} is an external cause code and can't be the only or first diagnosis: add the injury itself.", "dx-outpatient")

    # INJURY-7TH
    # a visit that is itself the follow-up ("here for suture removal"), not a plan that mentions one
    followup = re.search(r"(?i)\b(?:here|presents|returns|returned|seen|comes? in|came in)\s+(?:today\s+)?for\s+(?:a\s+)?"
                         r"(?:suture removal|staple removal|wound check|follow[\s\-]?up|recheck)", note or "")
    for d in dxs:
        code = _code(d).replace(".", "")
        if re.match(r"^[ST]\d", code) and len(code) == 7:
            seventh = code[-1]
            if seventh == "D" and not followup and re.search(r"(?i)\b(repaired|sutured|splinted|reduced|today|hour[s]? ago|initial)\b", note or ""):
                r.warn("INJURY-7TH", f"{_code(d)} ends in D (a healing visit), but the note describes active treatment: initial-treatment codes end in A.", "dx-specificity")
            if seventh == "A" and followup and not re.search(r"(?i)\b(repaired|sutured|new injury)\b", note or ""):
                r.warn("INJURY-7TH", f"{_code(d)} ends in A (active treatment), but the note describes a follow-up visit: healing visits end in D.", "dx-specificity")

    # DUPLICATE
    seen_codes = {}
    for c in cpts:
        code = _code(c)
        if code in seen_codes and not (set(_mods(c)) & REPEAT_MODS):
            r.warn("DUPLICATE", f"{code} appears twice without a repeat modifier (76, 77, 91, or 59/X): the second line will be denied as a duplicate.", "mod-76-77-91")
        seen_codes[code] = True

    return out, r
