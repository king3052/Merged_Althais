"""Third-party services the dental EMR talks to, each behind an interface with a mock, so vendors can be swapped and
everything can be built and tested before a contract is signed.

  Kind            Interface             Mock (default)          Real ones we expect to write
  clearinghouse   DentalClearinghouse   MockClearinghouse       DentalXChange, Vyne (Tesia), Claim.MD
  attachments     AttachmentService     MockAttachments         Vyne FastAttach (NEA), DentalXChange
  sms             SmsProvider           MockSms                 Twilio, Bandwidth
  payments        PaymentProcessor      MockPayments            Stripe, Rectangle Health, Worldpay
  radiograph_ai   RadiographAI          MockRadiographAI        Overjet, Pearl, VideaHealth (FDA-cleared; we never diagnose)
  transcriber     Transcriber           MockTranscriber         Deepgram, AssemblyAI, AWS Transcribe Medical
  erx             EPrescriber           MockEPrescriber         DoseSpot, DrFirst (EPCS needs a DEA-audited vendor)
  imaging         ImagingBridge         MockImagingBridge       Dexis/Carestream/Planmeca bridges, VDDS, TWAIN
  reviews         ReviewPlatform        MockReviews             Google Business Profile

vendor(kind) returns the one configured with DENTAL_VENDOR_<KIND> (e.g. DENTAL_VENDOR_SMS=mock). Only mocks exist today,
and every mock result says environment="mock" so nothing it produces can be mistaken for a real transmission or payment.
Mocks are deterministic (seeded from their input), never call the network and never see real patient data.
"""
import datetime as dt
import hashlib
import os
import random
import secrets
from dataclasses import dataclass, field, asdict

import dental_x12 as x12

MOCK = "mock"


def _rng(*parts) -> random.Random:
    return random.Random(int(hashlib.sha256("|".join(str(p) for p in parts).encode()).hexdigest()[:12], 16))


@dataclass
class VendorResult:
    ok: bool
    status: str
    message: str = ""
    ref: str = ""
    environment: str = MOCK
    data: dict = field(default_factory=dict)

    def as_dict(self):
        return asdict(self)


# ── clearinghouse: eligibility (270/271), claims (837D), remittance (835), claim status (276/277), pre-auth ───────
class DentalClearinghouse:
    name = "none"

    def eligibility(self, x270: str, req: dict) -> str:
        """Sends a 270, returns the payer's 271."""
        raise NotImplementedError

    def submit_claim(self, x837: str, claim: dict) -> VendorResult:
        raise NotImplementedError

    def fetch_remittances(self, since: str) -> list:
        """New 835 files since a date, as raw X12 strings."""
        raise NotImplementedError

    def preauth(self, x837: str, claim: dict) -> VendorResult:
        """A predetermination: an 837D with BHT06 = predetermination, answered later with an approved amount."""
        raise NotImplementedError

    def preauth_status(self, ref: str) -> VendorResult:
        raise NotImplementedError


class MockClearinghouse(DentalClearinghouse):
    """Answers like a payer would, from the synthetic plan in the request. The same subscriber always gets the same plan."""
    name = MOCK
    _remits: list = []      # 835s waiting to be fetched (module-level so the API and tests see the same queue)
    _preauths: dict = {}

    def eligibility(self, x270, req):
        r = _rng("elig", req.get("member_id"), req.get("payer"))
        if str(req.get("member_id", "")).upper().startswith("TERM"):
            return x12.build_271(req, active=False)
        annual_max = r.choice([1000, 1500, 1500, 2000, 2500])
        used = round(r.choice([0, 0, 120, 340, 610, 980]) * annual_max / 1500, 2)
        ded = r.choice([50, 50, 75, 100])
        plan = {
            "plan_name": req.get("payer", "Dental PPO") + " " + r.choice(["Basic", "Plus", "Premier"]),
            "group": "GRP" + str(r.randint(10000, 99999)),
            "annual_max": annual_max, "max_used": min(used, annual_max), "deductible": ded,
            "deductible_met": r.choice([0, ded]), "ortho_lifetime_max": r.choice([0, 1000, 1500]),
            "coinsurance": {"preventive": 100, "diagnostic": 100, "basic": 80, "major": 50, "endo": 80, "perio": 80, "oral_surgery": 80, "ortho": r.choice([0, 50])},
            "waiting_period_major_months": r.choice([0, 0, 6, 12]),
            "frequencies": [
                {"group": "exam", "codes": ["D0120", "D0150", "D0180"], "limit": 2, "per": "calendar_year"},
                {"group": "prophy", "codes": ["D1110", "D1120", "D4910"], "limit": 2, "per": "calendar_year"},
                {"group": "bitewings", "codes": ["D0272", "D0274"], "limit": 1, "per": "calendar_year"},
                {"group": "fmx_pano", "codes": ["D0210", "D0330"], "limit": 1, "per_months": 60},
                {"group": "fluoride", "codes": ["D1206", "D1208"], "limit": 2, "per": "calendar_year", "age_max": 18},
                {"group": "srp", "codes": ["D4341", "D4342"], "limit": 1, "per_months": 24, "per_quadrant": True},
                {"group": "crown", "codes": ["D2740", "D2750", "D2790"], "limit": 1, "per_months": 60, "per_tooth": True},
            ],
            "history": [],   # the mock plan has no claims history of its own; the EMR's own records fill this in
        }
        return x12.build_271(req, active=True, plan=plan)

    def submit_claim(self, x837, claim):
        errors = x12.validate_837d(x837)
        if errors:
            return VendorResult(False, "rejected", "999 rejected: " + "; ".join(errors[:3]), data={"errors": errors})
        ref = "MCK" + secrets.token_hex(5).upper()
        # the payer's answer comes back later as an 835: queue one so "Fetch ERAs" has something real to post
        MockClearinghouse._remits.append(mock_835_for(claim, ref))
        return VendorResult(True, "accepted", "999 accepted, 277CA accepted by payer (mock)", ref=ref)

    def fetch_remittances(self, since):
        out, MockClearinghouse._remits = list(MockClearinghouse._remits), []
        return out

    def preauth(self, x837, claim):
        ref = "PD" + secrets.token_hex(4).upper()
        total = sum(float(l.get("fee") or 0) for l in claim.get("lines", []))
        r = _rng("pa", claim.get("id"), total)
        MockClearinghouse._preauths[ref] = {"approved": round(total * r.choice([0.5, 0.8, 0.8, 0.5]), 2), "denied": r.random() < 0.12}
        return VendorResult(True, "submitted", "Predetermination received by payer (mock)", ref=ref)

    def preauth_status(self, ref):
        p = MockClearinghouse._preauths.get(ref)
        if not p:
            return VendorResult(True, "pending", "No decision yet")
        if p["denied"]:
            return VendorResult(True, "denied", "Not a covered benefit under this plan (mock)", ref=ref)
        return VendorResult(True, "approved", "Approved", ref=ref, data={"approved_amount": p["approved"],
                            "expires": (dt.date.today() + dt.timedelta(days=180)).isoformat()})


def mock_835_for(claim: dict, payer_claim_id: str) -> str:
    """What a payer would pay on this claim: plan coinsurance by category, an occasional denial, deterministic."""
    r = _rng("835", claim.get("id"))
    lines, cats = [], {"preventive": 1.0, "diagnostic": 1.0}
    for l in claim.get("lines", []):
        fee = float(l.get("fee") or 0)
        cat = x12.benefit_category(l.get("code", ""))
        pct = cats.get(cat, 0.8 if cat in ("basic", "endo", "perio", "oral_surgery") else 0.5)
        allowed = round(fee * r.choice([0.72, 0.78, 0.85]), 2)
        deny = r.random() < 0.08
        if deny:
            lines.append({**l, "paid": 0.0, "allowed": allowed, "adj": [["CO", "96" if cat == "preventive" else "16", fee]]})
            continue
        paid = round(allowed * pct, 2)
        adj = [["CO", "45", round(fee - allowed, 2)]]
        if allowed - paid > 0:
            adj.append(["PR", "2", round(allowed - paid, 2)])
        lines.append({**l, "paid": paid, "allowed": allowed, "adj": adj})
    return x12.build_835({"payer": claim.get("payer", ""), "check": "EFT" + str(r.randint(100000, 999999)),
                          "date": dt.date.today().isoformat(), "claims": [{**claim, "payer_claim_id": payer_claim_id, "lines": lines}]})


# ── attachments (radiographs, perio charts, narratives) ─────────────────────────────────────────────────────────
class AttachmentService:
    def send(self, claim_id: str, files: list) -> VendorResult:
        raise NotImplementedError


class MockAttachments(AttachmentService):
    def send(self, claim_id, files):
        if not files:
            return VendorResult(False, "empty", "Nothing to attach")
        return VendorResult(True, "sent", f"{len(files)} attachment(s) accepted (mock)", ref="NEA" + str(_rng("nea", claim_id).randint(10 ** 7, 10 ** 8 - 1)))


# ── SMS ──────────────────────────────────────────────────────────────────────────────────────────────────────────
class SmsProvider:
    def send(self, to: str, body: str) -> VendorResult:
        raise NotImplementedError


class MockSms(SmsProvider):
    outbox: list = []

    def send(self, to, body):
        digits = "".join(ch for ch in str(to) if ch.isdigit())
        if len(digits) < 10:
            return VendorResult(False, "invalid_number", "Not a valid mobile number")
        MockSms.outbox.append({"to": digits[-10:], "body": body, "at": dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()})
        return VendorResult(True, "delivered", "Delivered (mock, nothing was sent)", ref="SM" + secrets.token_hex(6))


# ── payments ─────────────────────────────────────────────────────────────────────────────────────────────────────
class PaymentProcessor:
    def charge(self, amount_cents: int, token: str, description: str) -> VendorResult:
        raise NotImplementedError

    def refund(self, ref: str, amount_cents: int) -> VendorResult:
        raise NotImplementedError


class MockPayments(PaymentProcessor):
    """Test cards: tok_visa succeeds, tok_declined is declined. We never see or store a card number."""

    def charge(self, amount_cents, token, description):
        if amount_cents <= 0:
            return VendorResult(False, "invalid_amount", "Amount must be above $0")
        if token == "tok_declined":
            return VendorResult(False, "declined", "Card declined (mock)")
        return VendorResult(True, "succeeded", "Paid (mock, no money moved)", ref="ch_mock_" + secrets.token_hex(6), data={"amount_cents": amount_cents})

    def refund(self, ref, amount_cents):
        return VendorResult(True, "refunded", "Refunded (mock)", ref="re_mock_" + secrets.token_hex(6))


# ── AI radiograph analysis: FDA-cleared vendors only. We show their findings; we never make our own. ─────────────
class RadiographAI:
    vendor = ""

    def analyze(self, image_id: str, kind: str, teeth: list) -> VendorResult:
        raise NotImplementedError


class MockRadiographAI(RadiographAI):
    vendor = "Mock vendor (stands in for Overjet / Pearl / VideaHealth)"

    def analyze(self, image_id, kind, teeth):
        r = _rng("ai", image_id)
        findings = []
        for t in teeth or []:
            roll = r.random()
            if roll < 0.18:
                findings.append({"tooth": str(t), "finding": "caries", "surface": r.choice(["M", "D", "O"]), "confidence": round(r.uniform(.62, .95), 2)})
            elif roll < 0.25:
                findings.append({"tooth": str(t), "finding": "calculus", "surface": "", "confidence": round(r.uniform(.6, .9), 2)})
            elif roll < 0.29 and kind in ("pa", "pano", "fmx"):
                findings.append({"tooth": str(t), "finding": "periapical radiolucency", "surface": "", "confidence": round(r.uniform(.55, .85), 2)})
        bone = [{"tooth": str(t), "bone_level_mm": round(r.uniform(1.0, 4.5), 1)} for t in (teeth or [])[:6]] if kind in ("bitewing", "fmx") else []
        return VendorResult(True, "complete", "Findings are the vendor's, for the dentist to review (mock)", ref="AI" + image_id[-6:],
                            data={"vendor": self.vendor, "findings": findings, "bone_levels": bone, "cleared": "Mock: no FDA clearance"})


# ── speech to text, for ambient notes and voice perio ───────────────────────────────────────────────────────────
class Transcriber:
    def transcribe(self, audio: bytes, mime: str) -> VendorResult:
        raise NotImplementedError


class MockTranscriber(Transcriber):
    """No real speech recognition: returns a fixed synthetic visit so the note-drafting pipeline can be built and tested.
    Browsers can also send text they recognized themselves (Web Speech), which skips this."""
    SAMPLE = ("Doctor: Any sensitivity since last time? Patient: Cold hurts on the upper left. "
              "Doctor: Okay, I see decay on fourteen, mesial and occlusal, and on three occlusal. We'll do composites today. "
              "Two carpules of lidocaine with epi. Fourteen MO composite placed, three O composite placed, occlusion checked. "
              "Hygienist completed adult prophy. Four bitewings taken today. Next recall in six months.")

    def transcribe(self, audio, mime):
        return VendorResult(True, "complete", "Mock transcript (no audio was processed)", data={"text": self.SAMPLE})


# ── e-prescribing (EPCS for controlled substances) ──────────────────────────────────────────────────────────────
class EPrescriber:
    def send(self, rx: dict) -> VendorResult:
        raise NotImplementedError


class MockEPrescriber(EPrescriber):
    def send(self, rx):
        if rx.get("controlled") and not rx.get("two_factor_ok"):
            return VendorResult(False, "epcs_auth_required", "Controlled substances need EPCS two-factor signing by the prescriber")
        if not rx.get("drug") or not rx.get("sig"):
            return VendorResult(False, "incomplete", "Drug and directions are required")
        return VendorResult(True, "sent", "Sent to pharmacy (mock, nothing was transmitted)", ref="RX" + secrets.token_hex(5).upper())


# ── imaging devices (sensors, pano, CBCT, intraoral scanners) ───────────────────────────────────────────────────
class ImagingBridge:
    def acquire(self, device: str, kind: str, teeth: list) -> VendorResult:
        raise NotImplementedError


class MockImagingBridge(ImagingBridge):
    DEVICES = [{"id": "sensor-1", "name": "Intraoral sensor (mock)", "kinds": ["bitewing", "pa"]},
               {"id": "pano-1", "name": "Panoramic unit (mock)", "kinds": ["pano"]},
               {"id": "cbct-1", "name": "CBCT (mock)", "kinds": ["cbct"]},
               {"id": "ios-1", "name": "Intraoral scanner (mock)", "kinds": ["scan"]},
               {"id": "cam-1", "name": "Intraoral camera (mock)", "kinds": ["photo"]}]

    def acquire(self, device, kind, teeth):
        if not any(d["id"] == device and kind in d["kinds"] for d in self.DEVICES):
            return VendorResult(False, "unsupported", "That device can't take that kind of image")
        return VendorResult(True, "captured", "Synthetic image captured (mock)", ref="IMG" + secrets.token_hex(4).upper(), data={"synthetic": True})


# ── review requests ─────────────────────────────────────────────────────────────────────────────────────────────
class ReviewPlatform:
    def review_link(self, location_id: int) -> str:
        raise NotImplementedError


class MockReviews(ReviewPlatform):
    def review_link(self, location_id):
        return f"https://reviews.example.test/location/{location_id}"


_REGISTRY = {
    "clearinghouse": {MOCK: MockClearinghouse}, "attachments": {MOCK: MockAttachments}, "sms": {MOCK: MockSms},
    "payments": {MOCK: MockPayments}, "radiograph_ai": {MOCK: MockRadiographAI}, "transcriber": {MOCK: MockTranscriber},
    "erx": {MOCK: MockEPrescriber}, "imaging": {MOCK: MockImagingBridge}, "reviews": {MOCK: MockReviews},
}


def vendor(kind: str):
    choice = os.environ.get("DENTAL_VENDOR_" + kind.upper(), MOCK).strip().lower() or MOCK
    impl = _REGISTRY[kind].get(choice)
    if impl is None:
        raise RuntimeError(f"No {kind} vendor called {choice!r} is implemented yet. Set DENTAL_VENDOR_{kind.upper()}=mock.")
    return impl()


def status() -> list:
    """For Settings › Integrations: what each service is set to, and whether it's a mock."""
    out = []
    for kind in _REGISTRY:
        choice = os.environ.get("DENTAL_VENDOR_" + kind.upper(), MOCK).strip().lower() or MOCK
        out.append({"kind": kind, "vendor": choice, "mock": choice == MOCK, "available": sorted(_REGISTRY[kind])})
    return out
