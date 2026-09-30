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
PARTNERS, with its credentials in the environment (see BILLING_ACTIVATION.md). Endpoints are never guessed: until a
partner's documentation and credentials are in hand, its entry stays unimplemented and every capability stays False.

Inbound status (enrollment decisions, 999/277CA acknowledgments, payer responses) arrives through
/api/billing/webhooks/<partner>, authenticated with an HMAC-SHA256 signature over the raw body
(CLEARINGHOUSE_WEBHOOK_SECRET), or through polling where the partner allows it.
"""

import hashlib
import hmac
import os
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


# Real partners go here once their API documentation and credentials exist. Each value is (label, connector class or None).
# None means "known, not built": selecting it keeps every capability off and the screens say partner access is missing.
PARTNERS = {
    "claimmd": ("Claim.MD", None),
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
    partner = (os.environ.get("CLEARINGHOUSE_PARTNER") or "").strip().lower()
    entry = PARTNERS.get(partner)
    if entry and entry[1] is not None and os.environ.get("CLEARINGHOUSE_API_KEY"):
        return entry[1]()
    c = NotConnected()
    if entry:
        c.label = f"{entry[0]} selected, but its connection isn't built yet (partner API access needed)"
    return c


def verify_signature(raw: bytes, signature: str, secret: str = None) -> bool:
    secret = secret if secret is not None else os.environ.get("CLEARINGHOUSE_WEBHOOK_SECRET", "")
    if not secret or not signature:
        return False
    want = hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest()
    return hmac.compare_digest(want, signature.strip().removeprefix("sha256="))
