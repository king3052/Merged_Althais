"""
claim_edits.py: diagnosis edits with exact answers, checked in code before a claim goes out.

Age and sex conflicts: a diagnosis that can't apply to this patient (a pregnancy code on a male patient, a newborn code on
an adult) is denied, or means the wrong patient or the wrong code. The ranges follow the ICD-10-CM chapters and the
Medicare Code Editor's age and sex categories, kept to the clear-cut ones: a flag here should always be worth a look.
"""
from __future__ import annotations

import datetime as dt
import re
from typing import List, Optional, Tuple


def _in(code: str, lo: str, hi: str) -> bool:
    """Whether a code (dots ignored) falls in the category range lo..hi (3-character categories, inclusive)."""
    c = code.replace(".", "").upper()
    return lo <= c[:len(lo)] <= hi


FEMALE_ONLY = [("O00", "O9A"), ("Z33", "Z34"), ("Z36", "Z37"), ("Z39", "Z39"), ("Z3A", "Z3A"), ("N70", "N98"), ("C51", "C58"),
               ("D25", "D28"), ("Z124", "Z124"), ("Z0141", "Z0142")]
MALE_ONLY = [("N40", "N53"), ("C60", "C63"), ("D29", "D29"), ("Z125", "Z125")]
NEWBORN = [("P00", "P96"), ("Z38", "Z38")]          # perinatal: age 0 (MCE "newborn")
MATERNITY = [("O00", "O9A"), ("Z33", "Z34"), ("Z3A", "Z3A")]   # MCE maternity ages 9-64


def _cat(code: str, ranges) -> Optional[Tuple[str, str]]:
    return next((r for r in ranges if _in(code, *r)), None)


def patient_from_header(header: str) -> Tuple[Optional[int], Optional[str], Optional[dt.date]]:
    """(age in years on the date of service, "F"/"M", date of service) read from the claim header the EMR sends
    ("... DOB 1961-11-22, sex F, ... date of service 2026-10-21"). Anything not on file is None."""
    h = header or ""
    def date(pat):
        m = re.search(pat + r"\s*:?\s*(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})", h, re.I)
        if not m:
            return None
        s = m.group(1)
        try:
            return dt.date.fromisoformat(s) if "-" in s else dt.datetime.strptime(s, "%m/%d/%Y").date()
        except ValueError:
            return None
    dob, dos = date(r"\bDOB"), date(r"date of service")
    m = re.search(r"\bsex\s*:?\s*(female|male|f|m)\b", h, re.I)
    sex = m.group(1)[0].upper() if m else None
    age = None
    if dob:
        ref = dos or dt.date.today()
        age = ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))
    return age, sex, dos


def age_sex_problems(codes: List[dict], header: str) -> List[dict]:
    """[{"code", "message", "hard"}] for diagnoses that conflict with the patient's sex or age. hard=True: a payer denies it."""
    age, sex, _ = patient_from_header(header)
    out = []
    for c in codes or []:
        if str(c.get("type", "")).upper().startswith("CPT"):
            continue
        code = str(c.get("code") or "").upper()
        if not code:
            continue
        if sex == "M":
            r = _cat(code, FEMALE_ONLY)
            if r:
                out.append({"code": code, "hard": True,
                            "message": f"{code} is a female-only diagnosis and the patient is listed as male. Check the patient's sex on file or the code."})
        if sex == "F":
            r = _cat(code, MALE_ONLY)
            if r:
                out.append({"code": code, "hard": True,
                            "message": f"{code} is a male-only diagnosis and the patient is listed as female. Check the patient's sex on file or the code."})
        if age is not None:
            if _cat(code, NEWBORN) and age > 0:
                out.append({"code": code, "hard": True,
                            "message": f"{code} is a newborn (perinatal) diagnosis and the patient is {age}. Use the code for the condition itself."})
            if _cat(code, MATERNITY) and not 9 <= age <= 64:
                out.append({"code": code, "hard": True,
                            "message": f"{code} is a pregnancy diagnosis and the patient is {age}, outside the ages payers accept (9-64). Check the date of birth or the code."})
    return out
