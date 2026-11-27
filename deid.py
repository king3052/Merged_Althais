"""Take patient identity out of text before it goes to an AI model, and put it back in what comes out.

Coding a visit needs the clinical facts (what happened, the findings, age, sex), not who the patient is. So before a
note, transcript or claim header is sent to Gemini, this replaces:
  names (from the header and the note's labeled fields, wherever they appear, and after Mr./Mrs./Ms.),
  birth dates (with the age, which coding does need; 90 and over becomes "90+"),
  other dates (kept to the year), phone and fax numbers, email addresses, street addresses, ZIP codes, city names in a
  "City, ST" pattern, Social Security numbers, MRNs, member, group, policy and account numbers, NPIs, URLs and IP addresses.
The placeholders are stable within one note ([PATIENT], [MEMBER ID]...), and restore() puts the real values back into
text the model returns (a suggested note addition that says [PATIENT], an appeal letter).

This is minimum-necessary de-identification on top of Google's BAA, not a certified HIPAA Safe Harbor guarantee:
a name typed only in the middle of free text and never in a labeled field can still get through. DEID_NOTES=0 turns
it off.
"""
from __future__ import annotations

import datetime as dt
import os
import re
from dataclasses import dataclass, field
from typing import Dict, List, Optional

_MONTHS = "jan|feb|mar|apr|may|jun|jul|aug|sep|sept|oct|nov|dec"
_MONTH_NUM = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}
_DATE_PATTERNS = [
    r"\b(\d{1,2})/(\d{1,2})/(\d{4}|\d{2})\b",                     # 03/14/1991, 3/14/91
    r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b",                            # 1991-03-14
    rf"\b(?:{_MONTHS})[a-z]*\.? \d{{1,2}},? (\d{{4}})\b",          # Nov 24, 2026
    rf"\b\d{{1,2}} (?:{_MONTHS})[a-z]* (\d{{4}})\b",               # 24 Nov 2026
    r"\b(\d{1,2})/(\d{4})\b",                                      # 04/2025
]
_LABELED_ID = re.compile(r"(?i)\b(MRN|medical record (?:number|no\.?|#)|member(?: id| #| number)?|subscriber(?: id)?|policy(?: number| #| no\.?)?|"
                         r"group(?: number| #| no\.?)?|account(?: number| #| no\.?)?|acct\.?(?: #)?|insured id|patient id|chart(?: #| number)?|"
                         r"claim(?: id| #| number))(\s*[:#]\s*|\s+)((?=[A-Z\-]*\d)[A-Z0-9][A-Z0-9\-]{2,})")
_NAME_FIELD = re.compile(r"(?im)^\s*(?:patient(?: name)?|name|pt|guarantor|subscriber name|insured name|emergency contact)\s*[:\-]\s*([A-Za-z][A-Za-z ,.'\-]{1,60}?)\s*(?:$|[·|;(]|\bDOB\b)")
_HEADER_NAME = re.compile(r"(?i)\bpatient\s+([A-Za-z][A-Za-z ,.'\-]{1,60}?)\s*,\s*DOB\b")
_DOB = re.compile(r"(?i)\b(?:DOB|D\.O\.B\.|date of birth|birth ?date|born)\s*[:\-]?\s*(" + "|".join(p.replace(r"\b", "") for p in _DATE_PATTERNS[:4]) + r")")
_DOS = re.compile(r"(?i)\b(?:date of service|DOS|visit date|service date)\s*[:\-]?\s*(" + "|".join(p.replace(r"\b", "") for p in _DATE_PATTERNS[:4]) + r")")
_PHONE = re.compile(r"(?<!\d)(?:\+?1[\s.\-]?)?\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}(?!\d)")
_EMAIL = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")
_SSN = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
_URL = re.compile(r"\bhttps?://\S+|\bwww\.\S+", re.I)
_IP = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_NPI = re.compile(r"(?i)\bNPI\s*[:#]?\s*\d{10}\b")
_STREET = re.compile(r"(?i)\b\d{1,6}\s+(?:[NSEW]\.?\s+)?(?:[A-Za-z0-9.'\-]+\s){0,4}(?:st|street|ave|avenue|rd|road|dr|drive|blvd|boulevard|ln|lane|"
                     r"hwy|highway|pkwy|parkway|ct|court|way|pl|place|cir|circle|trl|trail|loop|fwy)\b\.?(?:,?\s*(?:apt|unit|ste|suite|#)\s*[\w\-]+)?")
_CITY_STATE_ZIP = re.compile(r"\b([A-Z][a-zA-Z]+(?:\s[A-Z][a-zA-Z]+){0,2}),\s*([A-Z]{2})(?:\s+(\d{5})(?:-\d{4})?)?\b")
_ZIP = re.compile(r"\b([A-Z]{2})\s+\d{5}(?:-\d{4})?\b")
_OLD_AGE = re.compile(r"\b(9\d|1[01]\d)[\s\-]?(y/?o|yo|years?[\s\-]old|year[\s\-]old)\b", re.I)
_TITLE_NAME = re.compile(r"\b(Mr|Mrs|Ms|Miss|Mx)\.?\s+([A-Z][a-zA-Z'\-]+)")
_STATES = {"AL", "AK", "AZ", "AR", "CA", "CO", "CT", "DE", "FL", "GA", "HI", "ID", "IL", "IN", "IA", "KS", "KY", "LA", "ME", "MD", "MA",
           "MI", "MN", "MS", "MO", "MT", "NE", "NV", "NH", "NJ", "NM", "NY", "NC", "ND", "OH", "OK", "OR", "PA", "RI", "SC", "SD", "TN",
           "TX", "UT", "VT", "VA", "WA", "WV", "WI", "WY", "DC", "PR"}
# words that look like names in labeled fields but aren't (never replaced)
_NOT_NAMES = {"patient", "name", "the", "and", "not", "file", "on", "dob", "male", "female", "self", "same", "unknown", "none", "test",
              "data", "mr", "mrs", "ms", "dr", "md", "do", "np", "pa", "jr", "sr", "ii", "iii"}


def on() -> bool:
    return os.environ.get("DEID_NOTES", "1") != "0"


def _year(m: re.Match) -> Optional[int]:
    for g in m.groups():
        if g and len(g) == 4 and g.isdigit():
            return int(g)
        if g and len(g) == 2 and g.isdigit() and m.re.pattern == _DATE_PATTERNS[0] and g == m.group(3):
            y = int(g)
            return 2000 + y if y <= (dt.date.today().year % 100) else 1900 + y
    return None


def _parse_date(s: str) -> Optional[dt.date]:
    s = s.strip()
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%Y-%m-%d", "%b %d, %Y", "%b %d %Y", "%B %d, %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y"):
        try:
            return dt.datetime.strptime(s.replace("Sept", "Sep").replace(".", ""), fmt).date()
        except ValueError:
            continue
    return None


@dataclass
class Scrubber:
    """One per note: the same name gets the same placeholder in the note, transcript and header."""
    names: List[str] = field(default_factory=list)
    values: Dict[str, str] = field(default_factory=dict)      # placeholder -> original (for restore)
    found: Dict[str, int] = field(default_factory=dict)       # what kind of identifier was removed, how many times
    service_date: Optional[dt.date] = None

    def _count(self, kind: str, n: int = 1) -> None:
        if n:
            self.found[kind] = self.found.get(kind, 0) + n

    def learn(self, *texts: str) -> None:
        """Collect the patient's name and the visit date from labeled fields before scrubbing anything."""
        for t in texts:
            for m in list(_NAME_FIELD.finditer(t or "")) + list(_HEADER_NAME.finditer(t or "")):
                raw = m.group(1).strip(" ,.")
                if raw.lower() in ("not on file", "unknown", "none") or not raw:
                    continue
                if "[PATIENT]" not in self.values:
                    self.values["[PATIENT]"] = raw if "," not in raw else " ".join(p.strip().title() for p in reversed(raw.split(",", 1)))
                for tok in re.split(r"[\s,]+", raw):
                    tok = tok.strip(".'")
                    if len(tok) >= 2 and tok.isalpha() and tok.lower() not in _NOT_NAMES and tok not in self.names:
                        self.names.append(tok)
            for m in _TITLE_NAME.finditer(t or ""):
                if m.group(2) not in self.names and m.group(2).lower() not in _NOT_NAMES:
                    self.names.append(m.group(2))
            if self.service_date is None:
                m = _DOS.search(t or "")
                if m:
                    self.service_date = _parse_date(m.group(1))

    def _age(self, dob: dt.date) -> str:
        ref = self.service_date or dt.date.today()
        age = ref.year - dob.year - ((ref.month, ref.day) < (dob.month, dob.day))
        return "90+" if age >= 90 else str(max(age, 0))

    def scrub(self, text: str) -> str:
        if not text:
            return text or ""
        t = text

        def dob(m):
            d = _parse_date(m.group(1))
            self._count("birth date")
            return f"[DOB removed, age {self._age(d)}]" if d else "[DOB removed]"
        t = _DOB.sub(dob, t)
        for pat, kind, rep in ((_EMAIL, "email", "[EMAIL]"), (_URL, "web address", "[URL]"), (_SSN, "SSN", "[SSN]"),
                               (_NPI, "NPI", "NPI [NPI]"), (_PHONE, "phone number", "[PHONE]"), (_IP, "IP address", "[IP]")):
            t, n = pat.subn(rep, t)
            self._count(kind, n)

        def labeled(m):
            label = m.group(1)
            ph = "[MEMBER ID]" if re.match(r"(?i)member|subscriber|insured|policy", label) else "[ID]"
            self.values.setdefault(ph, m.group(3))
            self._count("record or member number")
            return f"{label}{m.group(2)}{ph}"
        t = _LABELED_ID.sub(labeled, t)
        t, n = _STREET.subn("[ADDRESS]", t)
        self._count("street address", n)

        def city(m):
            if m.group(2) not in _STATES:
                return m.group(0)
            if m.group(2) in ("MD", "DO", "PA") and not m.group(3):     # "Alex Smith, MD" is a credential, not Maryland
                return m.group(0)
            self._count("city")
            return f"[CITY], {m.group(2)}"
        t = _CITY_STATE_ZIP.sub(city, t)
        t, n = _ZIP.subn(lambda m: m.group(1), t)
        self._count("ZIP code", n)
        for i, pat in enumerate(_DATE_PATTERNS):
            def date(m, i=i):
                if re.fullmatch(r"\d{1,2}/\d{1,2}", m.group(0)):
                    return m.group(0)
                y = None
                for g in m.groups()[::-1] if i != 1 else m.groups():
                    if g and len(g) == 4 and g.isdigit():
                        y = g
                        break
                if y is None and i == 0 and m.group(3) and len(m.group(3)) == 2:
                    yy = int(m.group(3))
                    y = str(2000 + yy if yy <= dt.date.today().year % 100 else 1900 + yy)
                self._count("date")
                return f"[DATE {y}]" if y else "[DATE]"
            t = re.sub(pat, date, t, flags=re.I)
        t, n = _OLD_AGE.subn(lambda m: "90+ " + m.group(2), t)
        self._count("age over 89", n)
        t, n = _TITLE_NAME.subn(lambda m: f"{m.group(1)}. [PATIENT]", t)
        self._count("name", n)
        for name in sorted(self.names, key=len, reverse=True):
            t, n = re.subn(rf"\b{re.escape(name)}\b", "[PATIENT]", t, flags=re.I)
            self._count("name", n)
        t = re.sub(r"\[PATIENT\](?:[\s,]+\[PATIENT\])+", "[PATIENT]", t)          # "[PATIENT] [PATIENT]" -> one
        return t

    def restore(self, text: str) -> str:
        """Put the real values back into text the model wrote (only placeholders we created)."""
        if not text:
            return text or ""
        for ph, val in self.values.items():
            text = text.replace(ph, val)
        return text

    def summary(self) -> str:
        if not self.found:
            return ""
        plural = {"city": "cities", "street address": "street addresses", "record or member number": "record or member numbers",
                  "email": "emails", "SSN": "SSNs", "NPI": "NPIs", "IP address": "IP addresses"}
        return "Removed before the AI saw the note: " + ", ".join(f"{n} {plural.get(k, k + 's') if n > 1 else k}"
                                                                 for k, n in sorted(self.found.items())) + "."


def scrubber(*texts: str) -> Scrubber:
    s = Scrubber()
    s.learn(*texts)
    return s


def restore_obj(s: Scrubber, obj):
    """restore() through a JSON-like structure (a claim result's note additions, unresolved items...)."""
    if isinstance(obj, str):
        return s.restore(obj)
    if isinstance(obj, list):
        return [restore_obj(s, x) for x in obj]
    if isinstance(obj, dict):
        return {k: restore_obj(s, v) for k, v in obj.items()}
    return obj
