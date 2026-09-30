"""
credential_verification.py, checking credentials against authoritative outside sources.

Every provider implements CredentialVerificationProvider and returns a VerificationResult:
    status          VERIFIED | MISMATCH | NOT_FOUND | UNCONFIRMED | ERROR | NOT_RUN
    source          the service actually queried (never set unless it answered)
    timestamp       when
    matched_fields  field -> True / False / None (not checked)
    details         plain-language explanation

Nothing is reported as verified unless a real source was queried and answered. A provider that can't run
(no identifier to look up, the service is down) says so, and the review sends the document to a person when the
clinic requires outside verification.

Connected today: the federal NPI Registry (NPPES, CMS), free and public. It confirms that an NPI exists and is
active and whose it is. License numbers in NPPES are reported by the providers themselves, so a license found there
is labeled "as reported to NPPES", not verified by the licensing board. State licensing boards (Nursys, FSMB, each
state's board), the DEA and the American Heart Association have no free public API; add a provider here once the
clinic has access to one.
"""

import datetime as dt
import re
import time
from dataclasses import dataclass, field, asdict
from typing import Optional

import requests


@dataclass
class VerificationResult:
    status: str
    source: str = ""
    timestamp: str = ""
    matched_fields: dict = field(default_factory=dict)
    details: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


class CredentialVerificationProvider:
    name = "provider"

    def applies(self, doc_type: str, fields: dict, context: dict) -> bool:
        return False

    def verify(self, doc_type: str, fields: dict, context: dict) -> VerificationResult:
        raise NotImplementedError


def _now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat()


def _norm(v: str) -> str:
    return re.sub(r"[^A-Z0-9]", "", (v or "").upper())


def npi_valid(npi: str) -> bool:
    if not re.fullmatch(r"\d{10}", npi or ""):
        return False
    total = 0
    for i, ch in enumerate(reversed("80840" + npi[:9])):
        d = int(ch) * (2 if i % 2 == 0 else 1)
        total += d - 9 if d > 9 else d
    return (10 - total % 10) % 10 == int(npi[9])


class NPPESProvider(CredentialVerificationProvider):
    """The NPI Registry (https://npiregistry.cms.hhs.gov). Used for professional licenses when the person has an NPI."""
    name = "NPPES NPI Registry (CMS)"
    URL = "https://npiregistry.cms.hhs.gov/api/"
    LICENSE_TYPES = {"medical_license", "nursing_license", "professional_credential"}
    _cache: dict = {}
    TTL = 24 * 3600

    def applies(self, doc_type, fields, context):
        return doc_type in self.LICENSE_TYPES and npi_valid(context.get("npi", ""))

    def lookup(self, npi: str) -> Optional[dict]:
        hit = self._cache.get(npi)
        if hit and time.time() - hit[0] < self.TTL:
            return hit[1]
        r = requests.get(self.URL, params={"version": "2.1", "number": npi}, timeout=8)
        r.raise_for_status()
        data = r.json()
        rec = (data.get("results") or [None])[0] if data.get("result_count") else None
        self._cache[npi] = (time.time(), rec)
        return rec

    def verify(self, doc_type, fields, context):
        import staff_doc_review   # name matching lives with the rest of the document rules
        npi = context["npi"]
        try:
            rec = self.lookup(npi)
        except Exception as e:
            return VerificationResult("ERROR", details=f"The NPI Registry didn't answer ({type(e).__name__}).", timestamp=_now())
        ts = _now()
        if not rec:
            return VerificationResult("NOT_FOUND", self.name, ts, {"npi": False}, f"NPI {npi} isn't in the NPI Registry.")
        basic = rec.get("basic") or {}
        active = basic.get("status") == "A"
        reg_name = " ".join(x for x in (basic.get("first_name"), basic.get("last_name")) if x)
        name_ok = staff_doc_review.match_name(reg_name, context.get("names") or {}) == "MATCH" if reg_name else False
        listed = [(t.get("state", ""), _norm(t.get("license"))) for t in rec.get("taxonomies") or [] if t.get("license")]
        num, state = _norm(fields.get("license_number")), (fields.get("state") or "").upper()
        lic_ok = bool(num) and any(n == num and (not state or s == state) for s, n in listed)
        matched = {"npi": True, "npi_active": active, "name": name_ok, "license_number": lic_ok if num else None,
                   "state": (lic_ok if state else None)}
        if not active:
            return VerificationResult("MISMATCH", self.name, ts, matched, f"NPI {npi} is not active in the NPI Registry.")
        if not name_ok:
            return VerificationResult("MISMATCH", self.name, ts, matched, f"NPI {npi} is registered to {reg_name or 'someone else'}, not this staff member.")
        if not num:   # only the NPI itself was asked about
            return VerificationResult("VERIFIED", self.name, ts, matched, f"NPI {npi} is active and registered to {reg_name}.")
        if lic_ok:
            return VerificationResult("VERIFIED", self.name, ts, matched,
                                      f"NPI {npi} is active and registered to {reg_name}, with license {fields.get('license_number')} ({state}) as reported to NPPES.")
        return VerificationResult("UNCONFIRMED", self.name, ts, matched,
                                  f"NPI {npi} is active and registered to {reg_name}, but license {fields.get('license_number') or '-'} ({state or '-'}) isn't listed "
                                  "in the NPI Registry. License numbers there are self-reported and can be out of date.")


PROVIDERS: list = [NPPESProvider()]


def register(provider: CredentialVerificationProvider) -> None:
    PROVIDERS.append(provider)


def verify(doc_type: str, fields: dict, context: dict) -> dict:
    for p in PROVIDERS:
        try:
            if p.applies(doc_type, fields, context):
                return p.verify(doc_type, fields, context).as_dict()
        except Exception as e:
            return VerificationResult("ERROR", details=f"{p.name} failed ({type(e).__name__}).", timestamp=_now()).as_dict()
    why = ("Add the NPI in My Information to check it against the NPI Registry." if doc_type in NPPESProvider.LICENSE_TYPES
           else "No outside source can check this kind of document yet.")
    return VerificationResult("NOT_RUN", details=why, timestamp=_now()).as_dict()


def verify_npi(npi: str, names: dict) -> dict:
    """The NPI on its own (My Information > Professional Information)."""
    if not npi_valid(npi):
        return VerificationResult("NOT_RUN", details="Not a valid NPI.", timestamp=_now()).as_dict()
    return NPPESProvider().verify("medical_license", {}, {"npi": npi, "names": names}).as_dict()


def source_names() -> list:
    return [p.name for p in PROVIDERS]
