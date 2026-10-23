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
                "connection_check")


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
        return self._res("SENT", "Test claim accepted by the test clearinghouse.", ref="TEST-" + hashlib.sha256(idempotency_key.encode()).hexdigest()[:10].upper())

    def parse_webhook(self, payload):
        return payload


class ClaimMD(Connector):
    """Claim.MD (https://api.claim.md): claims go up as X12 837P files to /services/upload/, authenticated with the clinic's
    own AccountKey (Claim.MD portal > Settings > Account Settings). The key is stored encrypted per clinic; see save_connection.
    Endpoints and fields follow Claim.MD's published API reference. Enrollment stays a manual step (Claim.MD returns a
    form link per payer), so enrollment_submit is off; acknowledgments and ERAs are read by polling when that's built."""
    name = "claimmd"
    label = "Claim.MD"
    BASE = "https://svc.claim.md/services/"
    capabilities = {c: c in ("connection_check", "payer_list", "claim_submit") for c in CAPABILITIES}

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
        import xml.etree.ElementTree as ET
        try:
            return ET.fromstring(text)
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
        if code == 200 and root is not None and root.find(".//payer") is not None:
            return self._res(True, "CONNECTED", "Claim.MD accepted the account key.", raw=text)
        return self._res(False, "UNKNOWN", self._error(root, text) or "Claim.MD didn't accept that account key.", raw=text)

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
        best = exact[0] if len(exact) == 1 else rows[0] if len(rows) == 1 else None   # never guess between several payers
        return (best.get("payerid"), dict(best.attrib)) if best is not None else (None, {"matches": len(rows)})

    def payer_requirement(self, payer: str, transaction: str) -> Result:
        pid, attrs = self.find_payer(payer)
        if not pid:
            return self._res(True, "UNKNOWN", f"Claim.MD has no payer named {payer}. Check the payer name or its Claim.MD payer ID.")
        flag = (attrs.get("1500_claims") or "").lower() if transaction.upper().startswith("837P") else (attrs.get("era") or "").lower()
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
        try:
            prof = claim.get("billingProfile") or {}
            want = re.sub(r"[^a-z]", "", (claim.get("payer") or "").lower())
            mine = next((x for x in prof.get("payers") or [] if x.get("payerId") and re.sub(r"[^a-z]", "", (x.get("name") or "").lower()) == want), None)
            pid, attrs = (mine["payerId"], {"payer_name": mine.get("name")}) if mine else self.find_payer(claim.get("payer"))
            if not pid:
                why = f"{attrs.get('matches')} Claim.MD payers match {claim.get('payer')}" if attrs.get("matches") else f"Claim.MD has no payer named {claim.get('payer')}"
                return self._res(False, "REJECTED", why + ". Add its Claim.MD payer ID to the payer in Billing Activation (Setup Checklist, step 3).")
            edi = build_837p(claim, pid, attrs.get("payer_name") or claim.get("payer"))
        except ValueError as e:
            return self._res(False, "REJECTED", str(e))
        name = re.sub(r"[^A-Za-z0-9_-]", "_", str(claim.get("claimId") or "claim")) + ".837"
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
