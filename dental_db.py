"""The dental EMR's tables. Everything here is on the server (not in the browser), scoped to a clinic by org_key and, for
multi-location groups (DSOs), to a location by location_id.

Patients are the existing org_patients rows (auth.OrgPatient), joined here by (org_key, mrn), so the Patients list, the
claim screens and the browser sync keep working as before.

Encryption at rest: free text and files that hold PHI (notes, narratives, transcripts, perio readings, raw X12, images,
form answers, messages) are sealed with AES-256-GCM in the application (crypto_box, PHI_ENC_KEY) through the Sealed and
SealedJSON column types, on top of the database's own disk encryption. Identifiers that are needed for lookups (MRN,
claim number, CDT codes, dates, amounts) stay plain so they can be indexed.

Every row has `sample`: 1 for the synthetic practice (dental_synth.py), so it can be removed without touching real data.
"""
import datetime as dt
import json
from datetime import timezone

from sqlalchemy import DateTime, Float, Integer, String, Text, Index
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.types import TypeDecorator

import crypto_box
from auth import Base, engine

_PURPOSE, _ENV = "dental-phi", "PHI_ENC_KEY"


class Sealed(TypeDecorator):
    """Text encrypted with AES-256-GCM before it reaches the database."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None or value == "":
            return value
        return crypto_box.seal_text(str(value), _PURPOSE, _ENV)

    def process_result_value(self, value, dialect):
        if not value:
            return value or ""
        try:
            return crypto_box.open_text(value, _PURPOSE, _ENV)
        except Exception:
            return ""


class SealedJSON(TypeDecorator):
    """A JSON value, encrypted."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is None:
            return None
        return crypto_box.seal_text(json.dumps(value), _PURPOSE, _ENV)

    def process_result_value(self, value, dialect):
        if not value:
            return None
        try:
            return json.loads(crypto_box.open_text(value, _PURPOSE, _ENV))
        except Exception:
            return None


class PlainJSON(TypeDecorator):
    """A JSON value with no PHI in it (codes, amounts, settings)."""
    impl = Text
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else json.dumps(value)

    def process_result_value(self, value, dialect):
        return json.loads(value) if value else None


def _now():
    return dt.datetime.now(timezone.utc).replace(tzinfo=None)


class _Row:
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    sample: Mapped[int] = mapped_column(Integer, default=0)
    created: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)

    def as_dict(self, skip=()):
        out = {}
        for c in self.__table__.columns:
            if c.name in skip or c.name == "org_key":
                continue
            v = getattr(self, c.name)
            out[c.name] = v.isoformat() if isinstance(v, (dt.datetime, dt.date)) else v
        return out


# ── practice setup ──────────────────────────────────────────────────────────────────────────────────────────────
class Location(_Row, Base):
    __tablename__ = "dental_locations"
    name: Mapped[str] = mapped_column(String(120), default="")
    addr: Mapped[str] = mapped_column(String(200), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(2), default="")
    zip: Mapped[str] = mapped_column(String(10), default="")
    phone: Mapped[str] = mapped_column(String(20), default="")
    npi: Mapped[str] = mapped_column(String(10), default="")
    tin: Mapped[str] = mapped_column(String(12), default="")
    timezone: Mapped[str] = mapped_column(String(40), default="America/Chicago")
    region: Mapped[str] = mapped_column(String(80), default="")        # DSO grouping
    online_booking: Mapped[int] = mapped_column(Integer, default=1)
    active: Mapped[int] = mapped_column(Integer, default=1)


class Operatory(_Row, Base):
    __tablename__ = "dental_operatories"
    location_id: Mapped[int] = mapped_column(Integer, index=True)
    name: Mapped[str] = mapped_column(String(60), default="")
    kind: Mapped[str] = mapped_column(String(20), default="restorative")   # restorative | hygiene | surgery | consult
    sort: Mapped[int] = mapped_column(Integer, default=0)


class Provider(_Row, Base):
    __tablename__ = "dental_providers"
    name: Mapped[str] = mapped_column(String(120), default="")
    first: Mapped[str] = mapped_column(String(60), default="")
    last: Mapped[str] = mapped_column(String(60), default="")
    cred: Mapped[str] = mapped_column(String(20), default="DDS")         # DDS, DMD, RDH...
    kind: Mapped[str] = mapped_column(String(20), default="dentist")     # dentist | hygienist | specialist
    npi: Mapped[str] = mapped_column(String(10), default="")
    color: Mapped[str] = mapped_column(String(9), default="#1d5bb8")
    user_email: Mapped[str] = mapped_column(String(255), default="")     # the login this provider signs in as
    dea: Mapped[str] = mapped_column(String(12), default="")              # needed for EPCS
    active: Mapped[int] = mapped_column(Integer, default=1)


class ProviderHours(_Row, Base):
    """When a provider works, where, and in which chair. Online booking offers only these hours."""
    __tablename__ = "dental_provider_hours"
    provider_id: Mapped[int] = mapped_column(Integer, index=True)
    location_id: Mapped[int] = mapped_column(Integer, index=True)
    operatory_id: Mapped[int] = mapped_column(Integer, nullable=True)
    weekday: Mapped[int] = mapped_column(Integer)                         # 0 = Monday
    start: Mapped[str] = mapped_column(String(5), default="08:00")
    end: Mapped[str] = mapped_column(String(5), default="17:00")
    lunch_start: Mapped[str] = mapped_column(String(5), default="12:00")
    lunch_end: Mapped[str] = mapped_column(String(5), default="13:00")


class FeeSchedule(_Row, Base):
    __tablename__ = "dental_fees"
    code: Mapped[str] = mapped_column(String(5), index=True)
    desc: Mapped[str] = mapped_column(String(120), default="")
    fee: Mapped[float] = mapped_column(Float, default=0)
    minutes: Mapped[int] = mapped_column(Integer, default=30)
    payer: Mapped[str] = mapped_column(String(120), default="")           # "" = the office's UCR fee; a payer = its PPO fee


# ── scheduling ──────────────────────────────────────────────────────────────────────────────────────────────────
class Appointment(_Row, Base):
    __tablename__ = "dental_appointments"
    location_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    mrn: Mapped[str] = mapped_column(String(64), index=True, default="")
    provider_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    operatory_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    start: Mapped[dt.datetime] = mapped_column(DateTime, index=True)
    minutes: Mapped[int] = mapped_column(Integer, default=60)
    procedures: Mapped[list] = mapped_column(PlainJSON, default=list)    # [{code, tooth, surface, quad}]
    reason: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="scheduled")  # requested|scheduled|confirmed|arrived|seated|complete|no_show|cancelled
    source: Mapped[str] = mapped_column(String(20), default="staff")      # staff | online | recall
    request: Mapped[dict] = mapped_column(SealedJSON, nullable=True)      # what an online booker typed, until matched to a patient
    confirmed_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    reminders: Mapped[list] = mapped_column(PlainJSON, default=list)      # ["72h", "24h", "2h"] sent
    note: Mapped[str] = mapped_column(Sealed, default="")
    plan_item_ids: Mapped[list] = mapped_column(PlainJSON, default=list)


# ── clinical chart ──────────────────────────────────────────────────────────────────────────────────────────────
class ToothCondition(_Row, Base):
    """One mark on the tooth chart: an existing restoration, a finding, planned or completed work."""
    __tablename__ = "dental_tooth_conditions"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    tooth: Mapped[str] = mapped_column(String(3), default="")
    surface: Mapped[str] = mapped_column(String(6), default="")
    condition: Mapped[str] = mapped_column(String(40), default="")       # caries, composite, amalgam, crown, missing, implant, rct, fracture, watch...
    status: Mapped[str] = mapped_column(String(12), default="existing")   # existing | planned | completed | watch
    code: Mapped[str] = mapped_column(String(5), default="")
    date: Mapped[str] = mapped_column(String(10), default="")
    provider_id: Mapped[int] = mapped_column(Integer, nullable=True)
    note: Mapped[str] = mapped_column(Sealed, default="")


class PerioExam(_Row, Base):
    """Six sites per tooth (DB, B, MB on the facial; DL, L, ML on the lingual)."""
    __tablename__ = "dental_perio_exams"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[str] = mapped_column(String(10), default="")
    provider_id: Mapped[int] = mapped_column(Integer, nullable=True)
    readings: Mapped[dict] = mapped_column(SealedJSON, nullable=True)    # {tooth: {pd: [6], rec: [6], bop: [6], sup: [6], mob, furc}}
    summary: Mapped[dict] = mapped_column(PlainJSON, nullable=True)      # counts only: sites >=4/5/6mm, bop %, stage
    note: Mapped[str] = mapped_column(Sealed, default="")
    source: Mapped[str] = mapped_column(String(12), default="manual")    # manual | voice


class ClinicalNote(_Row, Base):
    __tablename__ = "dental_notes"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    appt_id: Mapped[int] = mapped_column(Integer, nullable=True)
    provider_id: Mapped[int] = mapped_column(Integer, nullable=True)
    date: Mapped[str] = mapped_column(String(10), default="")
    kind: Mapped[str] = mapped_column(String(20), default="progress")    # progress | ambient_draft | medical_history
    transcript: Mapped[str] = mapped_column(Sealed, default="")
    text: Mapped[str] = mapped_column(Sealed, default="")
    status: Mapped[str] = mapped_column(String(12), default="draft")     # draft | signed
    signed_by: Mapped[str] = mapped_column(String(255), default="")
    signed_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)


class Procedure(_Row, Base):
    """Completed (and imported) procedures: frequency history, production, and what claims are made from."""
    __tablename__ = "dental_procedures"
    location_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    code: Mapped[str] = mapped_column(String(5), index=True)
    tooth: Mapped[str] = mapped_column(String(3), default="")
    surface: Mapped[str] = mapped_column(String(6), default="")
    quad: Mapped[str] = mapped_column(String(2), default="")
    fee: Mapped[float] = mapped_column(Float, default=0)
    provider_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    claim_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    plan_item_id: Mapped[int] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(20), default="chart")     # chart | import | outside (done elsewhere, for frequency only)


class TreatmentPlan(_Row, Base):
    __tablename__ = "dental_treatment_plans"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    location_id: Mapped[int] = mapped_column(Integer, nullable=True)
    name: Mapped[str] = mapped_column(String(120), default="Treatment plan")
    status: Mapped[str] = mapped_column(String(20), default="draft")     # draft|presented|accepted|partial|declined|completed
    provider_id: Mapped[int] = mapped_column(Integer, nullable=True)
    token: Mapped[str] = mapped_column(String(48), index=True, default="")   # the patient's presentation link
    presented_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    signature: Mapped[str] = mapped_column(Sealed, default="")
    note: Mapped[str] = mapped_column(Sealed, default="")


class TreatmentItem(_Row, Base):
    __tablename__ = "dental_treatment_items"
    plan_id: Mapped[int] = mapped_column(Integer, index=True)
    phase: Mapped[int] = mapped_column(Integer, default=1)
    priority: Mapped[str] = mapped_column(String(12), default="needed")  # urgent | needed | elective
    code: Mapped[str] = mapped_column(String(5), default="")
    tooth: Mapped[str] = mapped_column(String(3), default="")
    surface: Mapped[str] = mapped_column(String(6), default="")
    quad: Mapped[str] = mapped_column(String(2), default="")
    fee: Mapped[float] = mapped_column(Float, default=0)
    est_insurance: Mapped[float] = mapped_column(Float, default=0)
    est_patient: Mapped[float] = mapped_column(Float, default=0)
    est_note: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(12), default="proposed")  # proposed|accepted|scheduled|completed|declined
    preauth_id: Mapped[int] = mapped_column(Integer, nullable=True)
    appt_id: Mapped[int] = mapped_column(Integer, nullable=True)


class Image(_Row, Base):
    __tablename__ = "dental_images"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    kind: Mapped[str] = mapped_column(String(12), default="bitewing")   # bitewing | pa | fmx | pano | cbct | photo | scan
    teeth: Mapped[list] = mapped_column(PlainJSON, default=list)
    taken: Mapped[str] = mapped_column(String(10), default="")
    device: Mapped[str] = mapped_column(String(60), default="")
    mime: Mapped[str] = mapped_column(String(40), default="image/svg+xml")
    data: Mapped[str] = mapped_column(Sealed, default="")                # base64 / svg; real deployments put files in object storage
    synthetic: Mapped[int] = mapped_column(Integer, default=0)
    ai: Mapped[dict] = mapped_column(SealedJSON, nullable=True)          # the vendor's findings, never ours
    ai_status: Mapped[str] = mapped_column(String(12), default="")      # "" | complete | failed
    ai_review: Mapped[str] = mapped_column(String(12), default="")      # "" | accepted | dismissed (the dentist's call)


# ── revenue cycle ───────────────────────────────────────────────────────────────────────────────────────────────
class Coverage(_Row, Base):
    """The patient's dental plan, and the last eligibility and benefits answer (271)."""
    __tablename__ = "dental_coverage"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    rank: Mapped[str] = mapped_column(String(10), default="primary")      # primary | secondary
    payer: Mapped[str] = mapped_column(String(120), default="")
    payer_id: Mapped[str] = mapped_column(String(20), default="")
    member_id: Mapped[str] = mapped_column(String(40), default="")
    group: Mapped[str] = mapped_column(String(40), default="")
    subscriber: Mapped[str] = mapped_column(String(10), default="self")   # self | spouse | child | other
    checked_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    active: Mapped[int] = mapped_column(Integer, default=0)
    benefits: Mapped[dict] = mapped_column(PlainJSON, nullable=True)    # parse_271 output (plan facts, no clinical data)
    raw_271: Mapped[str] = mapped_column(Sealed, default="")


class Claim(_Row, Base):
    __tablename__ = "dental_claims"
    location_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    claim_no: Mapped[str] = mapped_column(String(30), index=True, default="")
    kind: Mapped[str] = mapped_column(String(16), default="claim")        # claim | predetermination
    payer: Mapped[str] = mapped_column(String(120), default="")
    payer_id: Mapped[str] = mapped_column(String(20), default="")
    member_id: Mapped[str] = mapped_column(String(40), default="")
    dos: Mapped[str] = mapped_column(String(10), index=True, default="")
    provider_id: Mapped[int] = mapped_column(Integer, nullable=True)
    status: Mapped[str] = mapped_column(String(16), index=True, default="draft")  # draft|ready|submitted|accepted|paid|partial|denied|appealed|void|rejected
    total: Mapped[float] = mapped_column(Float, default=0)
    paid: Mapped[float] = mapped_column(Float, default=0)
    write_off: Mapped[float] = mapped_column(Float, default=0)
    patient_resp: Mapped[float] = mapped_column(Float, default=0)
    est_insurance: Mapped[float] = mapped_column(Float, default=0)
    narrative: Mapped[str] = mapped_column(Sealed, default="")
    attachments: Mapped[list] = mapped_column(PlainJSON, default=list)   # [{kind, image_id|perio_id, ref, sent}]
    frequency: Mapped[str] = mapped_column(String(1), default="1")       # 1 original, 7 replacement, 8 void
    original_claim_no: Mapped[str] = mapped_column(String(30), default="")
    preauth_ref: Mapped[str] = mapped_column(String(40), default="")
    payer_claim_id: Mapped[str] = mapped_column(String(40), default="")
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    x837: Mapped[str] = mapped_column(Sealed, default="")
    checks: Mapped[list] = mapped_column(PlainJSON, default=list)        # check/EFT numbers already posted
    edits: Mapped[list] = mapped_column(PlainJSON, default=list)         # dental_rules findings at the last check
    history: Mapped[list] = mapped_column(PlainJSON, default=list)       # [[iso time, text]]


class ClaimLine(_Row, Base):
    __tablename__ = "dental_claim_lines"
    claim_id: Mapped[int] = mapped_column(Integer, index=True)
    procedure_id: Mapped[int] = mapped_column(Integer, nullable=True)
    code: Mapped[str] = mapped_column(String(5), default="")
    tooth: Mapped[str] = mapped_column(String(3), default="")
    surface: Mapped[str] = mapped_column(String(6), default="")
    quad: Mapped[str] = mapped_column(String(2), default="")
    fee: Mapped[float] = mapped_column(Float, default=0)
    units: Mapped[int] = mapped_column(Integer, default=1)
    est_insurance: Mapped[float] = mapped_column(Float, default=0)
    paid: Mapped[float] = mapped_column(Float, default=0)
    write_off: Mapped[float] = mapped_column(Float, default=0)
    patient_resp: Mapped[float] = mapped_column(Float, default=0)
    adjustments: Mapped[list] = mapped_column(PlainJSON, default=list)
    status: Mapped[str] = mapped_column(String(12), default="")          # paid | denied | ""


class CodeSuggestion(_Row, Base):
    """A CDT code the rules or the AI think is missing or undercoded. Nothing is billed until a dentist approves it."""
    __tablename__ = "dental_code_suggestions"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    note_id: Mapped[int] = mapped_column(Integer, nullable=True)
    claim_id: Mapped[int] = mapped_column(Integer, nullable=True)
    source: Mapped[str] = mapped_column(String(10), default="rules")      # rules | ai
    kind: Mapped[str] = mapped_column(String(12), default="missed")       # missed | undercoded | suggested
    code: Mapped[str] = mapped_column(String(5), default="")
    tooth: Mapped[str] = mapped_column(String(3), default="")
    surface: Mapped[str] = mapped_column(String(6), default="")
    quad: Mapped[str] = mapped_column(String(2), default="")
    billed: Mapped[str] = mapped_column(String(20), default="")
    message: Mapped[str] = mapped_column(String(300), default="")
    evidence: Mapped[str] = mapped_column(Sealed, default="")
    status: Mapped[str] = mapped_column(String(10), default="pending")    # pending | approved | rejected
    decided_by: Mapped[str] = mapped_column(String(255), default="")
    decided_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)


class PreAuth(_Row, Base):
    __tablename__ = "dental_preauths"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[int] = mapped_column(Integer, nullable=True)
    claim_id: Mapped[int] = mapped_column(Integer, nullable=True)        # the predetermination claim
    payer: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(16), index=True, default="draft")  # draft|submitted|pending|approved|partial|denied|expired
    ref: Mapped[str] = mapped_column(String(40), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    approved_amount: Mapped[float] = mapped_column(Float, default=0)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    decided_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    expires: Mapped[str] = mapped_column(String(10), default="")
    follow_up: Mapped[str] = mapped_column(String(10), default="")
    note: Mapped[str] = mapped_column(String(300), default="")


class Remittance(_Row, Base):
    __tablename__ = "dental_remittances"
    payer: Mapped[str] = mapped_column(String(120), default="")
    check_no: Mapped[str] = mapped_column(String(40), index=True, default="")
    check_date: Mapped[str] = mapped_column(String(10), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(16), default="posted")     # posted | exceptions | unposted
    summary: Mapped[dict] = mapped_column(PlainJSON, nullable=True)
    raw_835: Mapped[str] = mapped_column(Sealed, default="")
    posted_by: Mapped[str] = mapped_column(String(255), default="")


class WorkItem(_Row, Base):
    """The denial and follow-up queue."""
    __tablename__ = "dental_work_items"
    kind: Mapped[str] = mapped_column(String(16), index=True, default="denial")   # denial | exception | attachment | preauth | rejection | aging
    mrn: Mapped[str] = mapped_column(String(64), default="")
    claim_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    title: Mapped[str] = mapped_column(String(200), default="")
    carc: Mapped[str] = mapped_column(String(8), default="")
    action: Mapped[str] = mapped_column(String(12), default="review")     # fix | attach | appeal | patient | review
    how: Mapped[str] = mapped_column(String(300), default="")
    amount: Mapped[float] = mapped_column(Float, default=0)
    status: Mapped[str] = mapped_column(String(12), index=True, default="open")   # open | working | resolved
    assigned_to: Mapped[str] = mapped_column(String(255), default="")
    due: Mapped[str] = mapped_column(String(10), default="")
    notes: Mapped[list] = mapped_column(PlainJSON, default=list)


class LedgerEntry(_Row, Base):
    """The patient account. Positive amounts are charges, negative are payments and credits."""
    __tablename__ = "dental_ledger"
    location_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    date: Mapped[str] = mapped_column(String(10), index=True)
    kind: Mapped[str] = mapped_column(String(20))      # charge | insurance_payment | patient_payment | write_off | adjustment | refund | membership
    amount: Mapped[float] = mapped_column(Float, default=0)
    claim_id: Mapped[int] = mapped_column(Integer, nullable=True)
    procedure_id: Mapped[int] = mapped_column(Integer, nullable=True)
    provider_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    ref: Mapped[str] = mapped_column(String(60), default="")
    memo: Mapped[str] = mapped_column(String(200), default="")


class PaymentRequest(_Row, Base):
    """Text-to-pay: a link the patient opens to pay a balance."""
    __tablename__ = "dental_payment_requests"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    amount: Mapped[float] = mapped_column(Float, default=0)
    token: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(12), default="sent")      # sent | paid | cancelled | expired
    sent_to: Mapped[str] = mapped_column(String(20), default="")
    paid_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    ref: Mapped[str] = mapped_column(String(60), default="")
    plan_id: Mapped[int] = mapped_column(Integer, nullable=True)


class PaymentPlan(_Row, Base):
    __tablename__ = "dental_payment_plans"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    total: Mapped[float] = mapped_column(Float, default=0)
    down: Mapped[float] = mapped_column(Float, default=0)
    months: Mapped[int] = mapped_column(Integer, default=6)
    schedule: Mapped[list] = mapped_column(PlainJSON, default=list)      # [{n, due, amount, kind, paid_at, ref}]
    status: Mapped[str] = mapped_column(String(12), default="active")    # active | completed | defaulted | cancelled
    autopay: Mapped[int] = mapped_column(Integer, default=0)
    card_token: Mapped[str] = mapped_column(String(60), default="")      # the processor's token; we never hold card numbers


class MembershipPlan(_Row, Base):
    __tablename__ = "dental_membership_plans"
    name: Mapped[str] = mapped_column(String(80), default="")
    monthly: Mapped[float] = mapped_column(Float, default=0)
    annual: Mapped[float] = mapped_column(Float, default=0)
    included: Mapped[dict] = mapped_column(PlainJSON, default=dict)      # {code: times per year}
    discount_pct: Mapped[float] = mapped_column(Float, default=0)
    active: Mapped[int] = mapped_column(Integer, default=1)


class Membership(_Row, Base):
    __tablename__ = "dental_memberships"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    plan_id: Mapped[int] = mapped_column(Integer, index=True)
    start: Mapped[str] = mapped_column(String(10), default="")
    renews: Mapped[str] = mapped_column(String(10), default="")
    billing: Mapped[str] = mapped_column(String(8), default="monthly")   # monthly | annual
    status: Mapped[str] = mapped_column(String(12), default="active")    # active | lapsed | cancelled
    used: Mapped[dict] = mapped_column(PlainJSON, default=dict)          # {code: times this membership year}


# ── patient engagement ──────────────────────────────────────────────────────────────────────────────────────────
class Message(_Row, Base):
    __tablename__ = "dental_messages"
    mrn: Mapped[str] = mapped_column(String(64), index=True, default="")
    direction: Mapped[str] = mapped_column(String(4), default="out")     # out | in
    phone: Mapped[str] = mapped_column(String(20), default="")
    body: Mapped[str] = mapped_column(Sealed, default="")
    kind: Mapped[str] = mapped_column(String(12), default="manual")      # manual | reminder | recall | review | pay | form | booking
    status: Mapped[str] = mapped_column(String(16), default="sent")
    ref: Mapped[str] = mapped_column(String(40), default="")
    read: Mapped[int] = mapped_column(Integer, default=0)
    appt_id: Mapped[int] = mapped_column(Integer, nullable=True)


class Campaign(_Row, Base):
    __tablename__ = "dental_campaigns"
    name: Mapped[str] = mapped_column(String(120), default="")
    kind: Mapped[str] = mapped_column(String(12), default="recall")
    template: Mapped[str] = mapped_column(Text, default="")
    filters: Mapped[dict] = mapped_column(PlainJSON, nullable=True)
    sent: Mapped[int] = mapped_column(Integer, default=0)
    booked: Mapped[int] = mapped_column(Integer, default=0)


class FormTemplate(_Row, Base):
    __tablename__ = "dental_form_templates"
    name: Mapped[str] = mapped_column(String(120), default="")
    fields: Mapped[list] = mapped_column(PlainJSON, default=list)        # [{k, label, type, options, maps_to}]


class FormSubmission(_Row, Base):
    __tablename__ = "dental_form_submissions"
    mrn: Mapped[str] = mapped_column(String(64), index=True, default="")
    template_id: Mapped[int] = mapped_column(Integer)
    token: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(12), default="sent")      # sent | completed | applied
    answers: Mapped[dict] = mapped_column(SealedJSON, nullable=True)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    applied_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True)
    applied_by: Mapped[str] = mapped_column(String(255), default="")


class ReviewRequest(_Row, Base):
    __tablename__ = "dental_review_requests"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    appt_id: Mapped[int] = mapped_column(Integer, nullable=True)
    token: Mapped[str] = mapped_column(String(48), index=True)
    status: Mapped[str] = mapped_column(String(12), default="sent")      # sent | rated | posted
    rating: Mapped[int] = mapped_column(Integer, nullable=True)
    comment: Mapped[str] = mapped_column(Sealed, default="")


# ── operations ──────────────────────────────────────────────────────────────────────────────────────────────────
class LabCase(_Row, Base):
    __tablename__ = "dental_lab_cases"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    lab: Mapped[str] = mapped_column(String(120), default="")
    item: Mapped[str] = mapped_column(String(120), default="")
    tooth: Mapped[str] = mapped_column(String(12), default="")
    shade: Mapped[str] = mapped_column(String(12), default="")
    status: Mapped[str] = mapped_column(String(12), default="to_send")   # to_send | sent | received | seated | remake
    sent: Mapped[str] = mapped_column(String(10), default="")
    due: Mapped[str] = mapped_column(String(10), default="")
    seat_appt_id: Mapped[int] = mapped_column(Integer, nullable=True)
    tracking: Mapped[str] = mapped_column(String(60), default="")
    cost: Mapped[float] = mapped_column(Float, default=0)


class Prescription(_Row, Base):
    __tablename__ = "dental_prescriptions"
    mrn: Mapped[str] = mapped_column(String(64), index=True)
    drug: Mapped[str] = mapped_column(String(120), default="")
    sig: Mapped[str] = mapped_column(String(200), default="")
    qty: Mapped[str] = mapped_column(String(20), default="")
    refills: Mapped[int] = mapped_column(Integer, default=0)
    controlled: Mapped[int] = mapped_column(Integer, default=0)
    schedule: Mapped[str] = mapped_column(String(4), default="")          # II-V
    pharmacy: Mapped[str] = mapped_column(String(120), default="")
    status: Mapped[str] = mapped_column(String(16), default="draft")
    ref: Mapped[str] = mapped_column(String(40), default="")
    prescriber_id: Mapped[int] = mapped_column(Integer, nullable=True)


class ProviderGoal(_Row, Base):
    __tablename__ = "dental_provider_goals"
    provider_id: Mapped[int] = mapped_column(Integer, index=True)
    month: Mapped[str] = mapped_column(String(7), index=True)            # YYYY-MM
    production: Mapped[float] = mapped_column(Float, default=0)
    collections: Mapped[float] = mapped_column(Float, default=0)


class ImportJob(_Row, Base):
    __tablename__ = "dental_import_jobs"
    source: Mapped[str] = mapped_column(String(16), default="")          # dentrix | eaglesoft | opendental | csv
    kind: Mapped[str] = mapped_column(String(16), default="")            # patients | procedures | appointments | insurance
    status: Mapped[str] = mapped_column(String(12), default="preview")   # preview | imported | failed
    rows: Mapped[int] = mapped_column(Integer, default=0)
    summary: Mapped[dict] = mapped_column(PlainJSON, nullable=True)
    by: Mapped[str] = mapped_column(String(255), default="")


Index("ix_dental_appt_org_start", Appointment.org_key, Appointment.start)
Index("ix_dental_proc_org_mrn", Procedure.org_key, Procedure.mrn)

TABLES = [Location, Operatory, Provider, ProviderHours, FeeSchedule, Appointment, ToothCondition, PerioExam, ClinicalNote, Procedure,
          TreatmentPlan, TreatmentItem, Image, Coverage, Claim, ClaimLine, CodeSuggestion, PreAuth, Remittance, WorkItem, LedgerEntry,
          PaymentRequest, PaymentPlan, MembershipPlan, Membership, Message, Campaign, FormTemplate, FormSubmission, ReviewRequest,
          LabCase, Prescription, ProviderGoal, ImportJob]

Base.metadata.create_all(engine, tables=[t.__table__ for t in TABLES])
