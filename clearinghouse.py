"""
clearinghouse.py: how Althais talks to a clearinghouse, and what it can honestly claim.

Althais has no clearinghouse partner connected today. The claim screens used to *animate* a transmission; nothing was
ever sent. So:

  NotConnected   the production default. It supports nothing, and it says so. Nothing is ever marked connected, sent
                 or approved through it.
  Sandbox        a test double for clinics an Althais administrator has put in test mode (BILLING_SANDBOX_ORGS). Every
                 response is stamped environment="sandbox". Test results never count toward a production clinic's
                 readiness, and a production clinic can never get this connector.

A real partner is added by writing a Connector subclass against that partner's documented API and registering it in
PARTNERS, with its credentials in the environment (see docs/BILLING_ACTIVATION.md). Endpoints are never guessed: until a
partner's documentation and credentials are in hand, its entry stays unimplemented and every capability stays False.

Inbound status (enrollment decisions, 999/277CA acknowledgments, payer responses) arrives through
/api/billing/webhooks/<partner>, authenticated with an HMAC-SHA256 signature over the raw body
(CLEARINGHOUSE_WEBHOOK_SECRET), or through polling where the partner allows it.
"""

import hashlib
import hmac
import os
import re
import secrets
import datetime as dt
from dataclasses import dataclass, field, asdict

CAPABILITIES = ("payer_list", "enrollment_submit", "enrollment_status", "claim_submit", "ack_999", "ack_277ca", "era_835", "webhooks",
                "connection_check", "attachments")


@dataclass
class Result:
    ok: bool
    environment: str                 # "production" | "sandbox"
    reference: str = ""              # the partner's reference number
    status: str = ""                 # normalized: SENT, ACCEPTED, REJECTED, PENDING, APPROVED, DENIED, CONNECTED, NOT_REQUIRED, REQUIRED, UNKNOWN
    message: str = ""
    raw_digest: str = ""             # sha256 of the partner's raw response (the response itself isn't logged)
    at: str = field(default_factory=lambda: dt.datetime.now(dt.timezone.utc).isoformat())

    def as_dict(self):
        return asdict(self)


class NotSupported(Exception):
    pass


class Connector:
    name = "none"
    label = "No clearinghouse connected"
    environment = "production"
    capabilities = {c: False for c in CAPABILITIES}

    def describe(self) -> dict:
        return {"name": self.name, "label": self.label, "environment": self.environment, "capabilities": dict(self.capabilities)}

    def _need(self, cap):
        if not self.capabilities.get(cap):
            raise NotSupported(f"{self.label} can't {cap.replace('_', ' ')}.")

    def check_connection(self, account: dict) -> Result:
        self._need("connection_check")

    def payer_requirement(self, payer: str, transaction: str) -> Result:
        """Whether the payer needs enrollment before this transaction (e.g. 837P) can be sent."""
        self._need("payer_list")

    def submit_enrollment(self, packet: dict) -> Result:
        self._need("enrollment_submit")

    def enrollment_status(self, reference: str) -> Result:
        self._need("enrollment_status")

    def submit_claim(self, claim: dict, idempotency_key: str) -> Result:
        self._need("claim_submit")

    def parse_webhook(self, payload: dict) -> dict:
        self._need("webhooks")

    def send_attachment(self, acn: str, pdf: bytes) -> Result:
        """Send the documents for a claim that carries attachment control number `acn` (PWK06)."""
        self._need("attachments")


class NotConnected(Connector):
    name = "none"
    label = "No clearinghouse connected"


class Sandbox(Connector):
    """Test responses only. Stamped sandbox everywhere; never usable by a production clinic."""
    name = "sandbox"
    label = "Althais test clearinghouse (sandbox)"
    environment = "sandbox"
    capabilities = {c: True for c in CAPABILITIES}

    def _res(self, status, message="", ok=True, ref=None):
        return Result(ok=ok, environment="sandbox", reference=ref or f"TEST-{secrets.token_hex(4).upper()}", status=status,
                      message="Test response. Nothing was sent to a payer. " + message)

    def check_connection(self, account):
        return self._res("CONNECTED", "Test account connected.")

    def payer_requirement(self, payer, transaction):
        # a fixed, obviously fake rule so tests exercise both branches
        return self._res("REQUIRED" if (payer or "").lower().startswith(("medicare", "medicaid")) else "NOT_REQUIRED")

    def submit_enrollment(self, packet):
        return self._res("PENDING", "Test enrollment received.")

    def enrollment_status(self, reference):
        return self._res("PENDING", ref=reference)

    def submit_claim(self, claim, idempotency_key):
        if is_dental(claim) and claim.get("billingProfile"):
            try:   # test mode builds the same 837D, so missing details show up before going live
                build_837d(claim, "TEST", claim.get("payer") or "TEST PAYER")
            except ValueError as e:
                return self._res("REJECTED", str(e), ok=False)
        return self._res("SENT", "Test claim accepted by the test clearinghouse.", ref="TEST-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:10].upper())

    def send_attachment(self, acn, pdf):
        return self._res("SENT", f"Test attachment received ({len(pdf) // 1024 or 1} KB).", ref=acn)

    def parse_webhook(self, payload):
        return payload


class ClaimMD(Connector):
    """Claim.MD (https://api.claim.md): claims go up as X12 837P (medical) or 837D (dental) files to /services/upload/, authenticated with the clinic's
    own AccountKey (Claim.MD portal > Settings > Account Settings). The key is stored encrypted per clinic; see save_connection.
    Endpoints and fields follow Claim.MD's published API reference. Enrollment stays a manual step (Claim.MD returns a
    form link per payer), so enrollment_submit is off; acknowledgments and ERAs are read by polling when that's built."""
    name = "claimmd"
    label = "Claim.MD"
    BASE = "https://svc.claim.md/services/"
    capabilities = {c: c in ("connection_check", "payer_list", "claim_submit", "attachments") for c in CAPABILITIES}

    def __init__(self, key: str, environment: str = "production"):
        self.key, self.environment = key, environment

    def _post(self, path, fields=None, files=None, timeout=30):
        import requests
        r = requests.post(self.BASE + path, data=dict({"AccountKey": self.key}, **(fields or {})), files=files, timeout=timeout,
                          headers={"Accept": "application/xml"})
        return r.status_code, r.text or ""

    def _res(self, ok, status, message="", ref="", raw=""):
        return Result(ok=ok, environment=self.environment, reference=ref, status=status, message=message,
                      raw_digest=hashlib.sha256(raw.encode()).hexdigest() if raw else "")

    @staticmethod
    def _xml(text):
        """Claim.MD's XML. Its payer list can carry a bare "&" or control characters in payer names, which a strict
        parser rejects, so a second try cleans those up before giving up."""
        import xml.etree.ElementTree as ET

        def bare(root):                                # a default namespace would hide <payer>/<claim>/<error> from find()
            for el in root.iter():
                if isinstance(el.tag, str) and "}" in el.tag:
                    el.tag = el.tag.split("}", 1)[1]
            return root
        try:
            return bare(ET.fromstring(text))
        except Exception:
            pass
        try:
            fixed = re.sub(r"&(?!(?:amp|lt|gt|quot|apos|#\d+|#x[0-9a-fA-F]+);)", "&amp;", text or "")
            fixed = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", fixed)
            fixed = re.sub(r"^\s*<\?xml[^>]*\?>", "", fixed)
            fixed = re.sub(r"(\s)(\d[\w.\-]*)(\s*=)", r"\1_\2\3", fixed)   # Claim.MD names some attributes 1500_claims: invalid XML, kept as _1500_claims
            return bare(ET.fromstring(fixed.strip()))
        except Exception:
            return None

    def _error(self, root, text):
        if root is None:
            return (text or "No response from Claim.MD.")[:300]
        e = root.find(".//error")
        if e is not None:
            return (e.get("error_mesg") or e.get("message") or e.text or "Claim.MD returned an error.").strip()
        return ""

    def check_connection(self, account: dict) -> Result:
        try:
            code, text = self._post("payerlist/", {"payer_name": "Aetna"})
        except Exception as e:
            return self._res(False, "UNKNOWN", f"Couldn't reach Claim.MD: {e}")
        root = self._xml(text)
        listed = (root is not None and root.find(".//payer") is not None) or (root is None and re.search(r"<payer\s[^>]*payerid=", text or "") and "<error" not in (text or ""))
        if code == 200 and listed:                     # a payer list only comes back for a key Claim.MD accepted
            return self._res(True, "CONNECTED", "Claim.MD accepted the account key.", raw=text)
        msg = self._error(root, text) if root is not None else ""
        return self._res(False, "UNKNOWN", (msg if msg and not msg.lstrip().startswith("<") else "") or
                         (f"Claim.MD answered {code}." if code != 200 else "Claim.MD didn't accept that account key."), raw=text)

    def find_payer(self, payer: str):
        """Claim.MD's payer record for a payer name: (payerid, attributes) or (None, {})."""
        try:
            code, text = self._post("payerlist/", {"payer_name": (payer or "").strip()})
        except Exception:
            return None, {}
        root = self._xml(text)
        if root is None:
            return None, {}
        rows = root.findall(".//payer")
        if not rows:
            return None, {}
        want = re.sub(r"[^a-z]", "", (payer or "").lower())
        exact = [r for r in rows if re.sub(r"[^a-z]", "", (r.get("payer_name") or "").lower()) == want]
        best = exact[0] if len(exact) == 1 else rows[0] if len(rows) == 1 else None
        if best is None and exact:
            ids = {r.get("payerid") for r in exact}
            if len(ids) == 1:                          # the same payer listed more than once (per state, per program)
                best = exact[0]
            else:                                      # the payer's main nationwide listing: no state, takes claims and eligibility
                main = [r for r in exact if not (r.get("payer_state") or "").strip() and "yes" in (r.get("_1500_claims", "yes"), r.get("dent_claims", "yes"))
                        and (r.get("eligibility") or "yes") != "no"]
                if len({r.get("payerid") for r in main}) == 1:
                    best = main[0]
        return (best.get("payerid"), dict(best.attrib)) if best is not None else (None, {"matches": len(rows)})   # still never guesses between different payers

    def payer_requirement(self, payer: str, transaction: str) -> Result:
        pid, attrs = self.find_payer(payer)
        if not pid:
            return self._res(True, "UNKNOWN", f"Claim.MD has no payer named {payer}. Check the payer name or its Claim.MD payer ID.")
        t = transaction.upper()
        if t.startswith("837D"):   # Claim.MD's payer list may not say whether dental claims need enrollment: then a person checks
            flag = (attrs.get("dental_claims") or attrs.get("dental") or "").lower()
            if not flag:
                return self._res(True, "UNKNOWN", f"Claim.MD payer {pid} found. Check in Claim.MD whether {attrs.get('payer_name') or payer} needs enrollment for dental claims.", ref=pid)
        else:
            flag = (attrs.get("1500_claims") or "").lower() if t.startswith("837P") else (attrs.get("era") or "").lower()
        if "enroll" in flag:
            return self._res(True, "REQUIRED", f"Claim.MD lists enrollment for {attrs.get('payer_name')} ({pid}).", ref=pid)
        if flag in ("yes", "y", "1", "true"):
            return self._res(True, "NOT_REQUIRED", f"Claim.MD sends to {attrs.get('payer_name')} ({pid}) without enrollment.", ref=pid)
        return self._res(True, "UNKNOWN", f"Claim.MD payer {pid} found; check its enrollment rules in Claim.MD.", ref=pid)

    def responses(self, last_id: str = "0"):
        """Claim status updates since last_id (Claim.MD /services/response/): (list of dicts, new last id)."""
        code, text = self._post("response/", {"ResponseID": str(last_id or "0")}, timeout=60)
        root = self._xml(text)
        if code != 200 or root is None:
            raise RuntimeError(self._error(root, text) or "No response from Claim.MD.")
        err = root.find(".//error")
        if err is not None:
            raise RuntimeError(self._error(root, text))
        out = []
        for cl in root.findall(".//claim"):
            msgs = [{"status": m.get("status") or "", "message": (m.get("message") or "").strip(), "responseid": m.get("responseid") or ""} for m in cl.findall(".//message")]
            out.append({"claimid": cl.get("claimid") or "", "claimmd_id": cl.get("claimmd_id") or "", "status": (cl.get("status") or "").upper(),
                        "sender": cl.get("sender_name") or "", "messages": msgs})
        return out, root.get("last_responseid") or last_id

    def submit_claim(self, claim: dict, idempotency_key: str) -> Result:
        dental = is_dental(claim)
        try:
            prof = claim.get("billingProfile") or {}
            want = re.sub(r"[^a-z]", "", (claim.get("payer") or "").lower())
            mine = next((x for x in prof.get("payers") or [] if x.get("payerId") and re.sub(r"[^a-z]", "", (x.get("name") or "").lower()) == want), None)
            pid, attrs = (mine["payerId"], {"payer_name": mine.get("name")}) if mine else self.find_payer(claim.get("payer"))
            if not pid:
                why = f"{attrs.get('matches')} Claim.MD payers match {claim.get('payer')}" if attrs.get("matches") else f"Claim.MD has no payer named {claim.get('payer')}"
                return self._res(False, "REJECTED", why + ". Add its Claim.MD payer ID to the payer in Billing Activation (Setup Checklist, step 3).")
            edi = (build_837d if dental else build_837p)(claim, pid, attrs.get("payer_name") or claim.get("payer"))
        except ValueError as e:
            return self._res(False, "REJECTED", str(e))
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(claim.get("claimId") or "claim")) + (".837d" if dental else ".837")
        try:
            code, text = self._post("upload/", {"Filename": name}, files={"File": (name, edi.encode(), "application/edi-x12")}, timeout=60)
        except Exception as e:
            return self._res(False, "UNKNOWN", f"Couldn't reach Claim.MD: {e}")
        root = self._xml(text)
        err = self._error(root, text) if (code != 200 or root is None or root.find(".//claim") is None) else ""
        if err:
            return self._res(False, "REJECTED", "Claim.MD didn't accept the file: " + err, raw=text)
        cl = root.find(".//claim")
        msgs = "; ".join((m.get("message") or "").strip() for m in cl.findall(".//message") if (m.get("message") or "").strip())
        if (cl.get("status") or "").upper() == "R":
            return self._res(False, "REJECTED", "Claim.MD rejected the claim" + (": " + msgs if msgs else "."), ref=cl.get("claimmd_id") or "", raw=text)
        return self._res(True, "SENT", "Sent to Claim.MD" + (" (" + msgs + ")" if msgs else "."), ref=cl.get("claimmd_id") or cl.get("claimid") or "", raw=text)

    def send_attachment(self, acn: str, pdf: bytes) -> Result:
        """Claim.MD "automated PDF" attachments: the claim carries the attachment control number (PWK06), then one PDF named
        <ACN>.pdf is uploaded to /services/upload/. Claim.MD holds the claim pending documentation until the PDF arrives,
        and sends it to payers that take electronic attachments."""
        name = re.sub(r"[^A-Za-z0-9]", "", acn) + ".pdf"
        try:
            code, text = self._post("upload/", {"Filename": name}, files={"File": (name, pdf, "application/pdf")}, timeout=90)
        except Exception as e:
            return self._res(False, "UNKNOWN", f"Couldn't reach Claim.MD: {e}")
        root = self._xml(text)
        err = self._error(root, text) if (code != 200 or root is None or root.find(".//error") is not None) else ""
        if err:
            return self._res(False, "REJECTED", "Claim.MD didn't take the attachment: " + err, raw=text)
        return self._res(True, "SENT", "Attachment sent to Claim.MD.", ref=acn, raw=text)


# ---- X12 837P (005010X222A1) for one professional claim ----
def _x(v, n=None):
    v = re.sub(r"[~*:^]", " ", str(v or "")).strip().upper()
    return v[:n] if n else v


def build_837p(c: dict, payer_id: str, payer_name: str) -> str:
    b = c.get("billingProfile") or {}
    addr = b.get("address") or {}
    need = [("the clinic's NPI", b.get("npi")), ("the clinic's Tax ID", b.get("tin")), ("the billing address", addr.get("line1")),
            ("the billing city, state and ZIP", addr.get("city") and addr.get("state") and addr.get("zip")), ("the rendering provider's NPI", c.get("npi"))]
    missing = [n for n, v in need if not v]
    if missing:
        raise ValueError("Before Claim.MD can take this claim, add " + ", ".join(missing) + " in Billing Activation.")
    now = dt.datetime.now()
    d8, t4, ctl = now.strftime("%Y%m%d"), now.strftime("%H%M"), now.strftime("%H%M%S")[-9:].rjust(9, "0")
    last, _, first = (c.get("patient") or "").partition(",")
    pl, _, pf = (c.get("provider") or "").replace("Dr. ", "").partition(",")
    if not pf and " " in pl.strip():
        pf, pl = pl.strip().split(" ", 1)[0], pl.strip().split(" ", 1)[1]
    m = re.match(r"\s*(.*?),\s*([^,]+?),?\s+([A-Za-z]{2})\s+(\d{5})", c.get("addrStreet") or "")
    p_street, p_city, p_st, p_zip = (m.group(1), m.group(2), m.group(3), m.group(4)) if m else ("", "", "", "")
    dob = re.sub(r"\D", "", c.get("dob") or "")
    dos = re.sub(r"\D", "", c.get("svcDate") or "") or d8
    payer_l = (payer_name or "").lower()
    filing = "MB" if "medicare" in payer_l else "MC" if "medicaid" in payer_l else "CI"
    lines = [x for x in (c.get("cpts") or []) if x.get("code")]
    dx = [re.sub(r"[^A-Z0-9]", "", x["code"].upper()) for x in (c.get("icds") or []) if x.get("code")][:12]
    total = sum(float(x.get("charge") or 0) for x in lines) or float(c.get("totalAmt") or 0)
    seg = []
    seg.append(f"ST*837*0001*005010X222A1")
    seg.append(f"BHT*0019*00*{_x(c.get('claimId'), 30)}*{d8}*{t4}*CH")
    seg.append(f"NM1*41*2*{_x(b.get('legalName'), 60)}*****46*{_x(b.get('tin'), 80) or 'ALTHAIS'}")
    seg.append(f"PER*IC*{_x((b.get('contact') or {}).get('name') or b.get('legalName'), 60)}*EM*{_x((b.get('contact') or {}).get('email') or 'billing@althais.com', 80)}")
    seg.append("NM1*40*2*CLAIMMD*****46*CLAIMMD")
    seg.append("HL*1**20*1")
    if b.get("taxonomy"):
        seg.append(f"PRV*BI*PXC*{_x(b.get('taxonomy'))}")
    seg.append(f"NM1*85*2*{_x(b.get('legalName'), 60)}*****XX*{_x(b.get('npi'))}")
    seg.append(f"N3*{_x(addr.get('line1'), 55)}")
    seg.append(f"N4*{_x(addr.get('city'), 30)}*{_x(addr.get('state'), 2)}*{re.sub(r'[^0-9]', '', str(addr.get('zip') or ''))[:9]}")
    seg.append(f"REF*EI*{re.sub(r'[^0-9]', '', str(b.get('tin')))}")
    seg.append("HL*2*1*22*0")
    seg.append(f"SBR*P*18*******{filing}")
    seg.append(f"NM1*IL*1*{_x(last, 60)}*{_x(first, 35)}****MI*{_x(c.get('insuredId'), 80)}")
    if p_street:
        seg.append(f"N3*{_x(p_street, 55)}")
        seg.append(f"N4*{_x(p_city, 30)}*{_x(p_st, 2)}*{p_zip}")
    if dob:
        seg.append(f"DMG*D8*{dob}*{(c.get('sex') or 'U')[:1].upper() if (c.get('sex') or 'U')[:1].upper() in 'MF' else 'U'}")
    seg.append(f"NM1*PR*2*{_x(payer_name, 60)}*****PI*{_x(payer_id)}")
    seg.append(f"CLM*{_x(c.get('claimId'), 38)}*{total:.2f}***{_x(c.get('pos') or '11')}:B:1*Y*A*Y*Y")
    acn = re.sub(r"[^A-Za-z0-9]", "", str(c.get("attachmentAcn") or ""))
    if acn:
        seg.append(f"PWK*{_x(c.get('attachmentType') or 'OZ', 2)}*EL***AC*{acn[:50]}")
    if dx:
        seg.append("HI*" + "*".join(("ABK:" if i == 0 else "ABF:") + d for i, d in enumerate(dx)))
    seg.append(f"NM1*82*1*{_x(pl, 60)}*{_x(pf, 35)}****XX*{_x(c.get('npi'))}")
    for i, x in enumerate(lines, 1):
        mods = [m for m in re.split(r"[\s,-]+", str(x.get("modifier") or "")) if m][:4]
        ptr = ":".join(str(k + 1) for k in range(min(len(dx), 4))) or "1"
        seg.append(f"LX*{i}")
        seg.append(f"SV1*HC:{_x(x['code'])}" + "".join(":" + _x(mm, 2) for mm in mods) + f"*{float(x.get('charge') or 0):.2f}*UN*{int(x.get('units') or 1)}***{ptr}")
        seg.append(f"DTP*472*D8*{dos}")
    seg.append(f"SE*{len(seg) + 1}*0001")
    isa = (f"ISA*00*          *00*          *ZZ*{'ALTHAIS'.ljust(15)}*ZZ*{'CLAIMMD'.ljust(15)}*{now.strftime('%y%m%d')}*{t4}*^*00501*{ctl}*0*P*:")
    return "~\n".join([isa, f"GS*HC*ALTHAIS*CLAIMMD*{d8}*{t4}*1*X*005010X222A1"] + seg + ["GE*1*1", f"IEA*1*{ctl}"]) + "~\n"


# ---- 837D (005010X224A2) for one dental claim from the claim gate (the browser extension and the older claim screens) ----
# Checked here first (teeth 1-32 / A-T / supernumerary, surfaces B D F I L M O, areas of the mouth, CDT codes, the billing
# profile), then built by dental_x12.build_837d, the same builder the dental EMR uses, and checked again like a 999 would.
ORAL_AREA = {"UR": "10", "UL": "20", "LL": "30", "LR": "40", "UA": "01", "U": "01", "UPPER": "01", "LA": "02", "L": "02", "LOWER": "02",
             "FM": "00", "WHOLE": "00", "00": "00", "01": "01", "02": "02", "10": "10", "20": "20", "30": "30", "40": "40"}
REPORT_TYPES = {"xray": "RB", "perio": "P6", "narrative": "OZ", "photo": "OZ", "report": "DG", "other": "OZ"}
_TOOTH = re.compile(r"^(?:[1-9]|[12][0-9]|3[0-2]|[A-T]|5[1-9]|[67][0-9]|8[0-2]|[A-T]S)$")


def is_dental(c: dict) -> bool:
    return any(re.fullmatch(r"D\d{4}", str(x.get("code") or "").strip().upper()) for x in c.get("cpts") or [])


def norm_tooth(t) -> str:
    t = re.sub(r"^#", "", str(t or "").strip().upper())
    return t.lstrip("0") if t.isdigit() else t


def norm_surfaces(s) -> list:
    out = []
    for ch in re.sub(r"[^A-Z]", "", str(s or "").upper()):
        ch = {"V": "F"}.get(ch, ch)        # some systems mark the facial (class V) surface as V
        if ch in "BDFILMO" and ch not in out:
            out.append(ch)
    return out


def build_837d(c: dict, payer_id: str, payer_name: str) -> str:
    b = c.get("billingProfile") or {}
    addr = b.get("address") or {}
    need = [("the clinic's NPI", b.get("npi")), ("the clinic's Tax ID", b.get("tin")), ("the billing address", addr.get("line1")),
            ("the billing city, state and ZIP", addr.get("city") and addr.get("state") and addr.get("zip")), ("the treating dentist's NPI", c.get("npi"))]
    missing = [n for n, v in need if not v]
    if missing:
        raise ValueError("Before Claim.MD can take this dental claim, add " + ", ".join(missing) + " in Billing Activation.")
    lines = [x for x in (c.get("cpts") or []) if str(x.get("code") or "").strip()]
    if not lines:
        raise ValueError("The dental claim has no procedures.")
    if len(lines) > 50:
        raise ValueError("A dental claim can carry up to 50 procedures. Split this one.")
    problems = []
    for x in lines:
        code = str(x["code"]).strip().upper()
        if not re.fullmatch(r"D\d{4}", code):
            problems.append(f"{code} isn't a CDT code; a dental claim carries CDT codes only")
        t = norm_tooth(x.get("tooth"))
        if t and not _TOOTH.match(t):
            problems.append(f"{code}: tooth {x.get('tooth')} isn't a valid tooth number (1-32, A-T)")
        q = str(x.get("quad") or "").strip().upper()
        if q and q not in ORAL_AREA:
            problems.append(f"{code}: {q} isn't an area of the mouth (UR, UL, LR, LL, upper or lower arch)")
        if len(norm_surfaces(x.get("surface"))) != len(re.sub(r"[^A-Z]", "", str(x.get("surface") or "").upper())):
            problems.append(f"{code}: surfaces {x.get('surface')} can only use M, O, D, B, L, I and F, each once")
    if problems:
        raise ValueError("Fix before sending: " + "; ".join(problems) + ".")
    # one 837D builder for all of Althais: dental_x12 (also used by the dental EMR). This maps the claim gate's shape onto it.
    import dental_x12 as x12
    last, _, first = (c.get("patient") or "").partition(",")
    pl, _, pf = (c.get("provider") or "").replace("Dr. ", "").partition(",")
    if not pf and " " in pl.strip():
        pf, pl = pl.strip().split(" ", 1)[0], pl.strip().split(" ", 1)[1]
    m = re.match(r"\s*(.*?),\s*([^,]+?),?\s+([A-Za-z]{2})\s+(\d{5})", c.get("addrStreet") or "")
    rev = {v: k for k, v in x12.PWK_TYPE.items()}
    acn = re.sub(r"[^A-Za-z0-9]", "", str(c.get("attachmentAcn") or ""))
    claim = {"id": c.get("claimId"), "dos": str(c.get("svcDate") or "")[:10], "payer": payer_name, "payer_id": payer_id, "group": c.get("groupNumber") or "",
             "filing_code": "MC" if "medicaid" in (payer_name or "").lower() else "CI",
             "attachments": [{"kind": rev.get(c.get("attachmentType") or "OZ", "narrative"), "ref": acn}] if acn else [],
             "provider": {"last": pl.strip(), "first": pf.strip(), "npi": c.get("npi"), "taxonomy": c.get("providerTaxonomy") or ""},
             "patient": {"last": last.strip(), "first": first.strip(), "dob": c.get("dob") or "", "sex": c.get("sex") or "U", "member_id": c.get("insuredId") or "",
                         "addr": m.group(1) if m else "", "city": m.group(2) if m else "", "state": m.group(3) if m else "", "zip": m.group(4) if m else ""},
             "diagnoses": [x["code"] for x in (c.get("icds") or []) if x.get("code")],
             "lines": [{"code": str(x["code"]).strip().upper(), "tooth": norm_tooth(x.get("tooth")), "surface": "".join(norm_surfaces(x.get("surface"))[:5]),
                        "quad": x12.CODE_QUAD.get(ORAL_AREA.get(str(x.get("quad") or "").strip().upper(), ""), ""),
                        "units": max(1, int(float(x.get("units") or 1))), "fee": round(float(x.get("charge") or 0) / max(1, int(float(x.get("units") or 1))), 2),
                        "prosthesis": x.get("prosthesis") or ""} for x in lines]}
    practice = {"name": b.get("legalName"), "npi": b.get("npi"), "tin": b.get("tin"), "addr": addr.get("line1"), "city": addr.get("city"), "state": addr.get("state"),
                "zip": str(addr.get("zip") or ""), "phone": (b.get("contact") or {}).get("phone") or b.get("phone") or "", "taxonomy": b.get("taxonomy") or ""}
    edi = x12.for_receiver(x12.build_837d(claim, practice), "CLAIMMD", "CLAIMMD", production=True, payer_id=payer_id)
    errs = x12.validate_837d(edi)
    if errs:
        raise ValueError("Fix before sending: " + "; ".join(errs[:4]) + ".")
    return edi


# ---- the clinic's own clearinghouse connection (stored encrypted in org_settings, category "clearinghouse") ----
PLATFORM = "__althais_platform__"   # Althais's own clearinghouse account: clinics never connect one themselves


def load_connection(org_key: str) -> dict:
    try:
        import json as _json
        from auth import SessionLocal, OrgSettings
        from sqlalchemy import select
        with SessionLocal() as db:
            row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == org_key, OrgSettings.category == "clearinghouse"))
            return _json.loads(row.data or "{}") if row else {}
    except Exception:
        return {}


def save_connection(db, org_key: str, doc: dict):
    import json as _json
    from auth import OrgSettings
    from sqlalchemy import select
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == org_key, OrgSettings.category == "clearinghouse"))
    if not row:
        row = OrgSettings(org_key=org_key, category="clearinghouse", data="{}")
        db.add(row)
    row.data = _json.dumps(doc)


def seal(text: str) -> str:
    """AES-256-GCM (crypto_box.py), key from CLEARINGHOUSE_ENC_KEY or SECRET_KEY. Also used for DrChrono tokens."""
    import crypto_box
    return crypto_box.seal_text(text, "clearinghouse", "CLEARINGHOUSE_ENC_KEY")


def unseal(token: str) -> str:
    import crypto_box
    return crypto_box.open_text(token, "clearinghouse", "CLEARINGHOUSE_ENC_KEY", b"althais-clearinghouse|")


# Real partners go here once their API documentation and credentials exist. Each value is (label, connector class or None).
# None means "known, not built": selecting it keeps every capability off and the screens say partner access is missing.
PARTNERS = {
    "claimmd": ("Claim.MD", ClaimMD),
    "availity": ("Availity", None),
    "officeally": ("Office Ally", None),
    "waystar": ("Waystar", None),
    "optum": ("Optum (Change Healthcare)", None),
}


def sandbox_orgs() -> set:
    return {x.strip() for x in (os.environ.get("BILLING_SANDBOX_ORGS") or "").split("|") if x.strip()}


def environment_for(org_key: str) -> str:
    return "sandbox" if org_key in sandbox_orgs() else "production"


def connector_for(org_key: str) -> Connector:
    """Sandbox only for test-mode clinics. Production gets the configured partner, or NotConnected."""
    if environment_for(org_key) == "sandbox":
        return Sandbox()
    conn = load_connection(PLATFORM)   # Althais submits for every clinic through its own clearinghouse account (admin console)
    if conn.get("partner") == "claimmd" and conn.get("key"):
        try:
            c = ClaimMD(unseal(conn["key"]))
            c.label = "Althais (through Claim.MD)"
            return c
        except Exception:
            pass
    partner = (os.environ.get("CLEARINGHOUSE_PARTNER") or "").strip().lower()
    entry = PARTNERS.get(partner)
    if entry and entry[1] is not None and os.environ.get("CLEARINGHOUSE_API_KEY"):
        return entry[1](os.environ["CLEARINGHOUSE_API_KEY"])
    c = NotConnected()
    c.label = "Althais is finishing its clearinghouse connection"
    if entry:
        c.label = f"{entry[0]} selected, but its connection isn't built yet (partner API access needed)"
    return c


def verify_signature(raw: bytes, signature: str, secret: str = None) -> bool:
    secret = secret if secret is not None else os.environ.get("CLEARINGHOUSE_WEBHOOK_SECRET", "")
    if not secret or not signature:
        return False
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.strip().removeprefix("sha256="))
