"""
billing_activation.py: getting a clinic ready to send claims, and deciding (with explicit rules) whether a claim may go.

Two separate things, on purpose:

  Individual biller onboarding   only people whose role is Biller. Training, a practice claim with made-up data, the
                                 claim-submission acknowledgment, then submission access. Access comes from an owner's
                                 approval, or from an auto-activation rule an owner switched on. The Biller role alone
                                 never grants it.
  Shared clinic setup            the clinic's billing profile, its rendering providers, locations and payers, and each
                                 requirement from billing_registry.py, tracked per scope (clinic, entity, provider,
                                 location, payer, transaction). Done once for the clinic and reused by every biller.

Readiness is computed by rules in readiness(), never from AI confidence. A route (payer + rendering provider, 837P) is
ready only when every blocking requirement is APPROVED/VERIFIED/NOT_REQUIRED, backed by evidence, effective today or
earlier, not expired, and recorded in the clinic's own environment (test results never count in production). Unknown,
pending, expired and future-effective all count as not ready.

Before a claim is sent: the claim passes the current checks, the person sending it has active submission access, the
route is ready, and a connected clearinghouse can actually send it. Otherwise the claim is held, unchanged, with the
exact reasons. Held claims are released automatically only when the clinic's owner turned that policy on and every
check passes again.

Background work runs as persistent jobs (BillingJob) with idempotency keys, retries with backoff, and an escalation
task when a job keeps failing. Nothing here signs or attests for anyone: signature requests go to the signer's own
login. Sensitive values (Tax ID, NPI) are masked for everyone but owners and never go into notifications or logs.
"""

import asyncio
import datetime as dt
import hashlib
import html as _html
import json
import re
import secrets
from concurrent.futures import ThreadPoolExecutor

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, Response
from sqlalchemy import DateTime, Integer, LargeBinary, String, Text, UniqueConstraint, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Mapped, Session, mapped_column

import billing_registry as reg
import clearinghouse as chx
import credential_verification as cv
import staff_onboarding as so
from auth import Base, SessionLocal, User, engine, ensure_area, ensure_product, get_db, require_user, send_email, _email_html

router = APIRouter()


def _now():
    return dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)


def _today():
    return so._today()


def _iso(d):
    return d.isoformat() if d else ""


def _date(s):
    try:
        return dt.date.fromisoformat(str(s)[:10]) if s else None
    except ValueError:
        return None


# ──────────────────────────────────────────────────────────────────────────
#  Tables (created on startup; see BILLING_ACTIVATION.md for the list)
# ──────────────────────────────────────────────────────────────────────────
class BillingProfile(Base):
    """The clinic's billing profile: one per clinic, versioned with rev."""
    __tablename__ = "billing_profiles"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    data: Mapped[str] = mapped_column(Text, default="{}")
    rev: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)


class BillingItem(Base):
    """One registry requirement at one scope. The unique key stops duplicate applications."""
    __tablename__ = "billing_items"
    __table_args__ = (UniqueConstraint("org_key", "requirement", "provider_id", "location_id", "payer", "transaction", name="uq_billing_item"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    requirement: Mapped[str] = mapped_column(String(64))
    scope: Mapped[str] = mapped_column(String(16))
    track: Mapped[str] = mapped_column(String(24))
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    location_id: Mapped[str] = mapped_column(String(64), default="")
    payer: Mapped[str] = mapped_column(String(64), default="")
    transaction: Mapped[str] = mapped_column(String(16), default="")
    status: Mapped[str] = mapped_column(String(24), default="NOT_STARTED")
    environment: Mapped[str] = mapped_column(String(16), default="production")
    active: Mapped[int] = mapped_column(Integer, default=1)          # 0 once the payer/provider is removed (history kept)
    reference: Mapped[str] = mapped_column(String(128), default="")
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    effective_date: Mapped[str] = mapped_column(String(10), default="")
    expires_at: Mapped[str] = mapped_column(String(10), default="")
    evidence_document_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    evidence_note: Mapped[str] = mapped_column(String(1024), default="")
    evidence_source: Mapped[str] = mapped_column(String(32), default="")    # nppes | clearinghouse | document | sandbox
    verified_by: Mapped[str] = mapped_column(String(255), default="")
    verified_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    last_status_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    last_status_source: Mapped[str] = mapped_column(String(32), default="")
    packet_digest: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(String(2048), default="")          # plain-language status detail (no sensitive values)
    registry_version: Mapped[str] = mapped_column(String(16), default=reg.VERSION)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BillingEvent(Base):
    """History of every status change. external_id makes webhook and poll updates idempotent."""
    __tablename__ = "billing_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    item_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None, index=True)
    claim_id: Mapped[str] = mapped_column(String(64), default="")
    external_id: Mapped[str] = mapped_column(String(128), nullable=True, default=None, unique=True)
    kind: Mapped[str] = mapped_column(String(32))
    from_status: Mapped[str] = mapped_column(String(24), default="")
    to_status: Mapped[str] = mapped_column(String(24), default="")
    source: Mapped[str] = mapped_column(String(32), default="")
    environment: Mapped[str] = mapped_column(String(16), default="production")
    reference: Mapped[str] = mapped_column(String(128), default="")
    note: Mapped[str] = mapped_column(String(1024), default="")
    raw_digest: Mapped[str] = mapped_column(String(64), default="")
    actor: Mapped[str] = mapped_column(String(255), default="")
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BillingTask(Base):
    __tablename__ = "billing_tasks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    dedupe_key: Mapped[str] = mapped_column(String(191), unique=True)
    kind: Mapped[str] = mapped_column(String(32))        # info | signature | review | manual_submit | payer_request | escalation | access
    title: Mapped[str] = mapped_column(String(255))
    detail: Mapped[str] = mapped_column(String(2048), default="")
    assignee: Mapped[str] = mapped_column(String(80), default="owner")   # "owner" | "person:<id>" | "user:<id>"
    item_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    status: Mapped[str] = mapped_column(String(16), default="open")       # open | done | cancelled
    due_date: Mapped[str] = mapped_column(String(10), default="")
    blocking: Mapped[int] = mapped_column(Integer, default=0)            # blocks the item's route while open
    done_by: Mapped[str] = mapped_column(String(255), default="")
    done_note: Mapped[str] = mapped_column(String(1024), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    done_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


class BillingSignature(Base):
    """A request for one named person to sign or attest. Only that person's own login can complete it."""
    __tablename__ = "billing_signatures"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    item_id: Mapped[int] = mapped_column(Integer, index=True)
    signer: Mapped[str] = mapped_column(String(80))                      # "owner" (any clinic admin, signing as themselves) | "person:<id>"
    statement: Mapped[str] = mapped_column(Text)
    packet_digest: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="requested")  # requested | signed | declined | void
    signed_by_user: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    signed_name: Mapped[str] = mapped_column(String(255), default="")
    signed_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BillingDocument(Base):
    """Uploaded billing documents: stored in the database, served only to the clinic's owners and the uploader."""
    __tablename__ = "billing_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    doc_type: Mapped[str] = mapped_column(String(40))
    item_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    filename: Mapped[str] = mapped_column(String(255))
    content_type: Mapped[str] = mapped_column(String(80))
    data: Mapped[bytes] = mapped_column(LargeBinary)
    sha256: Mapped[str] = mapped_column(String(64), index=True)
    environment: Mapped[str] = mapped_column(String(16), default="production")
    status: Mapped[str] = mapped_column(String(16), default="checking")  # checking | checked | needs_review | reviewed
    fields: Mapped[str] = mapped_column(Text, default="{}")
    issues: Mapped[str] = mapped_column(Text, default="[]")
    expires: Mapped[str] = mapped_column(String(10), default="")
    processor: Mapped[str] = mapped_column(String(64), default="")
    uploaded_by: Mapped[int] = mapped_column(Integer)
    reviewed_by: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class BillerAccess(Base):
    """One login's permission to send claims for one clinic. Never granted by the Biller role by itself."""
    __tablename__ = "biller_access"
    __table_args__ = (UniqueConstraint("org_key", "user_id", name="uq_biller_access"),)
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    person_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="PENDING")   # PENDING | ACTIVE | REVOKED
    granted_via: Mapped[str] = mapped_column(String(16), default="")     # owner | rule | self (owners only)
    rule_snapshot: Mapped[str] = mapped_column(String(1024), default="")
    granted_by: Mapped[str] = mapped_column(String(255), default="")
    granted_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    assigned_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    revoked_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


class BillingJob(Base):
    __tablename__ = "billing_jobs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(32))
    payload: Mapped[str] = mapped_column(String(2048), default="{}")
    idempotency_key: Mapped[str] = mapped_column(String(191), unique=True)
    status: Mapped[str] = mapped_column(String(16), default="queued")    # queued | running | done | failed
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    next_run_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)
    lease_until: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    last_error: Mapped[str] = mapped_column(String(512), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    finished_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


class ClaimTransmission(Base):
    """A claim's trip out of Althais. Delivery, clearinghouse acknowledgment, payer decision and payment are tracked apart."""
    __tablename__ = "claim_transmissions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    claim_id: Mapped[str] = mapped_column(String(64), index=True)
    content_hash: Mapped[str] = mapped_column(String(64))
    idempotency_key: Mapped[str] = mapped_column(String(191), unique=True)
    payer: Mapped[str] = mapped_column(String(64), default="")
    provider_id: Mapped[str] = mapped_column(String(64), default="")
    environment: Mapped[str] = mapped_column(String(16), default="production")
    user_id: Mapped[int] = mapped_column(Integer)
    held: Mapped[int] = mapped_column(Integer, default=0)
    blockers: Mapped[str] = mapped_column(Text, default="[]")
    delivery: Mapped[str] = mapped_column(String(16), default="HELD")    # HELD | SENT | FAILED | RELEASED
    ack: Mapped[str] = mapped_column(String(16), default="")             # PENDING | ACCEPTED | REJECTED (clearinghouse 999/277CA)
    payer_status: Mapped[str] = mapped_column(String(16), default="")    # PENDING | ACCEPTED | REJECTED
    payment: Mapped[str] = mapped_column(String(16), default="")         # PAID | DENIED | PARTIAL
    reference: Mapped[str] = mapped_column(String(128), default="")
    snapshot: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    sent_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    ack_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    payer_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    paid_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


Base.metadata.create_all(engine)

READY = ("APPROVED", "VERIFIED", "NOT_REQUIRED")
DATED_TRACKS = ("enrollment", "credentialing", "contracting", "edi", "era", "eft")
STATUS_LABELS = {
    "NOT_STARTED": "Not started", "INFO_NEEDED": "Information needed", "ASK_EXISTING": "Do you already bill them?",
    "EXISTING_UNVERIFIED": "Existing, needs proof", "NEEDS_REVIEW": "Needs review", "SIGNATURE_NEEDED": "Signature needed",
    "READY_TO_SUBMIT": "Ready to submit", "SUBMITTED": "Submitted", "IN_REVIEW": "With the payer", "PAYER_REQUEST": "Payer asked for more",
    "APPROVED": "Approved", "VERIFIED": "Verified", "NOT_REQUIRED": "Not required", "DENIED": "Denied", "EXPIRED": "Expired",
    "NOT_CONNECTED": "Not connected", "MANUAL": "Submit it yourself",
}


# ──────────────────────────────────────────────────────────────────────────
#  Who's asking
# ──────────────────────────────────────────────────────────────────────────
def org_of(user: User) -> str:
    return so._doc_org_key(user)


def claims_namespace(user: User) -> str:
    """The claim store's key for a clinic (same derivation as main._org_namespace)."""
    base = (user.organization or user.email or "unknown").strip().lower()
    return "org_" + re.sub(r"[^a-z0-9]+", "_", base).strip("_")


def is_owner(user: User) -> bool:
    return (user.role or "") == "admin" and not getattr(user, "portal_only", 0)


def person_id_of(db: Session, user: User, org_key: str) -> str:
    m = so.membership(db, user.id, org_key)
    return (m.staff_person_id if m else "") or ""


def _family(role: str) -> str:
    import althais_training as at
    return at._role_family(role)


class Ctx:
    def __init__(self, user, db):
        self.user, self.db = user, db
        self.org = org_of(user)
        if self.org.startswith("user:"):
            raise HTTPException(status_code=400, detail="Add your clinic's name in Settings before setting up billing.")
        self.env = chx.environment_for(self.org)
        self.owner = is_owner(user)
        self.person_id = person_id_of(db, user, self.org)
        self.row, self.doc = so.load_staff(db, self.org)
        self.person = next((p for p in self.doc["people"] if p["id"] == self.person_id), None) if self.person_id else None
        self.family = _family(self.person.get("role", "")) if self.person else ""
        self.biller = self.family == "biller" or (not self.owner and (user.role or "") == "biller")

    def assignee_keys(self):
        keys = {f"user:{self.user.id}"}
        if self.person_id:
            keys.add(f"person:{self.person_id}")
        if self.owner:
            keys.add("owner")
        return keys


def ctx(user: User = Depends(require_user), db: Session = Depends(get_db)) -> Ctx:
    ensure_product(user, db, "insurance")
    return Ctx(user, db)


def owner_ctx(user: User = Depends(require_user), db: Session = Depends(get_db)) -> Ctx:
    c = ctx(user, db)
    if not c.owner:
        raise HTTPException(status_code=403, detail="Only the clinic's owners and admins can do this.")
    return c


def audit(c_or_db, org_key, actor, action, detail):
    db = c_or_db.db if isinstance(c_or_db, Ctx) else c_or_db
    so.audit(db, org_key, actor, action, None, "billing", "", detail)


# ──────────────────────────────────────────────────────────────────────────
#  The clinic's billing profile, prefilled from what the clinic already confirmed
# ──────────────────────────────────────────────────────────────────────────
def npi_ok(v: str) -> bool:
    return cv.npi_valid(re.sub(r"\D", "", v or ""))


def tin_ok(v: str) -> bool:
    return len(re.sub(r"\D", "", v or "")) == 9


def taxonomy_ok(v: str) -> bool:
    return bool(re.fullmatch(r"[0-9A-Z]{9}X", (v or "").strip().upper()))


def mask_tin(v: str) -> str:
    d = re.sub(r"\D", "", v or "")
    return f"••-•••{d[-4:]}" if len(d) >= 4 else ""


def load_profile(db, org_key, create=True):
    row = db.scalar(select(BillingProfile).where(BillingProfile.org_key == org_key))
    if not row and create:
        row = BillingProfile(org_key=org_key, data=json.dumps({"setupStartedAt": _today().isoformat(), "webhookRef": secrets.token_hex(12)}))
        db.add(row)
        try:
            db.flush()
        except IntegrityError:
            db.rollback()
            row = db.scalar(select(BillingProfile).where(BillingProfile.org_key == org_key))
    data = json.loads(row.data) if row else {}
    for k, v in (("locations", []), ("payers", []), ("providers", []), ("sources", {}), ("confirmed", {}), ("policy", {}), ("address", {}), ("contact", {})):
        data.setdefault(k, v if not isinstance(v, (list, dict)) else type(v)())
    return row, data


def save_profile(db, row, data):
    row.data = json.dumps(data)
    row.rev = (row.rev or 0) + 1


def prefill(doc: dict, data: dict) -> list:
    """Fill empty billing fields from Clinic Onboarding and staff records. Never overwrites, never marks anything confirmed."""
    changed, c, src = [], doc.get("clinic") or {}, data["sources"]

    def put(k, v, where):
        if v and not data.get(k):
            data[k] = v
            src[k] = where
            changed.append(k)
    put("legalName", c.get("name"), "Clinic Onboarding")
    put("npi", re.sub(r"\D", "", c.get("npi") or ""), "Clinic Onboarding")
    put("tin", re.sub(r"\D", "", c.get("tin") or ""), "Clinic Onboarding")
    if c.get("address") and not data["address"].get("line1"):
        data["address"]["line1"] = c["address"]
        src["address"] = "Clinic Onboarding"
        changed.append("address")
    have_loc = {l["name"].lower() for l in data["locations"]}
    for l in c.get("locations") or []:
        if l.get("name") and l["name"].lower() not in have_loc:
            data["locations"].append({"id": "loc_" + secrets.token_hex(4), "name": l["name"], "address": l.get("address", ""), "source": "Clinic Onboarding"})
            changed.append("location")
    have_pay = {p["key"] for p in data["payers"]}
    for p in c.get("payers") or []:
        key = reg.payer_key(p.get("name"))
        if key and key not in have_pay:
            # what the clinic typed about this payer is kept as a claim to verify, never as an approval
            data["payers"].append({"key": key, "name": reg.KNOWN_PAYERS.get(p["name"].lower(), (p["name"],))[0], "type": reg.payer_type(p.get("name")),
                                   "alreadyBills": True if (p.get("status") == "Approved" or p.get("providerId")) else None,
                                   "network": None, "reported": {k: p.get(k, "") for k in ("status", "effective", "providerId")},
                                   "source": "Clinic Onboarding"})
            have_pay.add(key)
            changed.append("payer")
    have_prov = {x.get("personId") for x in data["providers"]}
    for person in doc.get("people") or []:
        if person.get("lifecycle") in ("OFFBOARDED",) or _family(person.get("role", "")) != "physician" or person["id"] in have_prov:
            continue
        prof = (person.get("info") or {}).get("professional") or {}
        npi = re.sub(r"\D", "", so.profile_value(person, "npi") or prof.get("npi") or "")
        data["providers"].append({"id": "prv_" + secrets.token_hex(4), "personId": person["id"], "name": person.get("name", ""), "npi": npi,
                                  "taxonomy": prof.get("taxonomy", ""), "licenseNumber": so.profile_value(person, "license_number") or prof.get("license_number", ""),
                                  "licenseState": so.profile_value(person, "license_state") or prof.get("license_state", ""),
                                  "licenseExpires": "", "source": "Staff record"})
        changed.append("provider")
    return changed


# ──────────────────────────────────────────────────────────────────────────
#  Requirement items for every scope (idempotent)
# ──────────────────────────────────────────────────────────────────────────
def _item(db, org, env, r, provider="", location="", payer="", txn=""):
    q = select(BillingItem).where(BillingItem.org_key == org, BillingItem.requirement == r["id"], BillingItem.provider_id == provider,
                                  BillingItem.location_id == location, BillingItem.payer == payer, BillingItem.transaction == txn)
    it = db.scalar(q)
    if it:
        it.active = 1
        return it, False
    it = BillingItem(org_key=org, requirement=r["id"], scope=r["scope"], track=r["track"], provider_id=provider, location_id=location,
                     payer=payer, transaction=txn, environment=env, status="NOT_STARTED")
    db.add(it)
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:          # another worker created it first
        return db.scalar(q), False
    return it, True


def sync_items(db, org, env, data) -> int:
    made = 0
    seen = set()

    def mk(r, **kw):
        nonlocal made
        it, new = _item(db, org, env, r, **kw)
        seen.add(it.id if it.id else id(it))
        made += new
        return it
    for r in reg.common("clinic") + reg.common("entity"):
        if r["id"] == "entity_w9" and not any(p["type"] in ("commercial", "medicaid", "other") for p in data["payers"]):
            continue
        mk(r)
    for pv in data["providers"]:
        for r in reg.common("provider"):
            mk(r, provider=pv["id"])
    for py in data["payers"]:
        for r in reg.for_payer(py["type"]):
            txn = "837P" if r["track"] == "edi" else ("835" if r["track"] == "era" else "")
            if r["scope"] == "provider":
                for pv in data["providers"]:
                    mk(r, provider=pv["id"], payer=py["key"], txn=txn)
            else:
                mk(r, payer=py["key"], txn=txn)
    db.flush()
    live = {i for i in seen if isinstance(i, int)}
    for it in db.scalars(select(BillingItem).where(BillingItem.org_key == org, BillingItem.active == 1)):
        if it.id not in live:
            it.active = 0
    return made


def set_status(db, it: BillingItem, status: str, source: str, actor: str = "", note: str = "", reference: str = "", external_id=None,
               occurred_at=None, raw_digest: str = "", environment: str = None) -> bool:
    """Every status change goes through here: recorded, idempotent by external_id, and stale updates are ignored."""
    occurred_at = occurred_at or _now()
    if external_id and db.scalar(select(BillingEvent.id).where(BillingEvent.external_id == external_id)):
        return False
    if it.last_status_at and occurred_at < it.last_status_at and source in ("clearinghouse", "webhook", "poll"):
        db.add(BillingEvent(org_key=it.org_key, item_id=it.id, external_id=external_id, kind="stale_update", from_status=it.status, to_status=status,
                            source=source, environment=environment or it.environment, reference=reference, note="Older than the last update; ignored",
                            raw_digest=raw_digest, actor=actor, occurred_at=occurred_at))
        return False
    db.add(BillingEvent(org_key=it.org_key, item_id=it.id, external_id=external_id, kind="status", from_status=it.status, to_status=status,
                        source=source, environment=environment or it.environment, reference=reference, note=note[:1000], raw_digest=raw_digest,
                        actor=actor, occurred_at=occurred_at))
    it.status, it.last_status_at, it.last_status_source = status, occurred_at, source
    if reference:
        it.reference = reference[:128]
    if note:
        it.detail = note[:2000]
    return True


# ──────────────────────────────────────────────────────────────────────────
#  Tasks, signatures, notifications
# ──────────────────────────────────────────────────────────────────────────
def task(db, org, key, kind, title, detail="", assignee="owner", item_id=None, due_days=7, blocking=0, notify=True):
    """Create a task once (dedupe_key). Returns (task, created)."""
    t = db.scalar(select(BillingTask).where(BillingTask.dedupe_key == f"{org}|{key}"))
    if t:
        if t.status == "cancelled":
            t.status = "open"
        return t, False
    t = BillingTask(org_key=org, dedupe_key=f"{org}|{key}"[:191], kind=kind, title=title[:255], detail=detail[:2000], assignee=assignee,
                    item_id=item_id, due_date=(_today() + dt.timedelta(days=due_days)).isoformat(), blocking=blocking)
    db.add(t)
    db.flush()
    if notify:
        notify_assignee(db, org, assignee, "New billing setup task", f"There's a new billing setup task for you: {title}. Open Billing Activation in Althais to see it.")
    return t, True


def close_tasks(db, org, prefix, by="Althais", note=""):
    for t in db.scalars(select(BillingTask).where(BillingTask.org_key == org, BillingTask.status == "open", BillingTask.dedupe_key.like(f"{org}|{prefix}%"))):
        t.status, t.done_by, t.done_at, t.done_note = "done", by, _now(), note


def _owners(db, org):
    rows = db.scalars(select(User).where(User.organization == org, User.role == "admin")).all()
    return [u for u in rows if not getattr(u, "portal_only", 0)]


def purchasing_owner(db, org):
    o = _owners(db, org)
    return min(o, key=lambda u: u.id) if o else None


def notify_assignee(db, org, assignee, subject, body):
    """Plain, generic notices: never a Tax ID, NPI, patient detail or payer letter text."""
    emails = []
    if assignee == "owner":
        emails = [u.email for u in _owners(db, org)]
    elif assignee.startswith("user:"):
        u = db.get(User, int(assignee[5:]))
        emails = [u.email] if u else []
    elif assignee.startswith("person:"):
        _, doc = so.load_staff(db, org)
        p = next((x for x in doc["people"] if x["id"] == assignee[7:]), None)
        emails = [p["email"]] if p and p.get("email") else []
    for e in emails:
        try:
            send_email(e, subject, _email_html(subject, _html.escape(body), f"{_app_url()}/revenue/billing-activation", "Open Billing Activation",
                                               note="You're getting this because you help set up billing for your clinic on Althais."))
        except Exception:
            pass


def _app_url():
    import os
    return (os.environ.get("APP_URL") or "https://app.althais.com").rstrip("/")


def request_signature(db, org, it: BillingItem, signer: str, statement: str, digest: str):
    live = db.scalar(select(BillingSignature).where(BillingSignature.item_id == it.id, BillingSignature.status.in_(("requested", "signed"))))
    if live and live.packet_digest == digest:
        return live
    if live:   # the details changed since it was requested or signed: that's a material change, so it has to be signed again
        live.status = "void"
    s = BillingSignature(org_key=org, item_id=it.id, signer=signer, statement=statement, packet_digest=digest)
    db.add(s)
    db.flush()
    task(db, org, f"sign:{it.id}:{digest[:12]}", "signature", "Review and sign: " + reg.BY_ID[it.requirement]["title"],
         "Only you can sign this. Althais never signs for anyone.", assignee=signer, item_id=it.id)
    return s


# ──────────────────────────────────────────────────────────────────────────
#  Applications: prefilled from verified data, never invented
# ──────────────────────────────────────────────────────────────────────────
FIELD_LABELS = {"legal_name": "Legal business name", "npi": "NPI", "tin": "Tax ID", "address": "Billing address", "name": "Practitioner name",
                "license_number": "License number", "state": "License state", "expires": "License expiration", "taxonomy": "Taxonomy code"}


def packet_for(data: dict, it: BillingItem, reveal: bool = False) -> dict:
    r = reg.BY_ID[it.requirement]
    pv = next((x for x in data["providers"] if x["id"] == it.provider_id), None) if it.provider_id else None
    addr = data.get("address") or {}
    values = {"legal_name": data.get("legalName", ""), "npi": (pv or {}).get("npi") if r["scope"] == "provider" else data.get("npi", ""),
              "tin": data.get("tin", ""), "address": ", ".join(x for x in (addr.get("line1"), addr.get("city"), addr.get("state"), addr.get("zip")) if x),
              "name": (pv or {}).get("name", ""), "license_number": (pv or {}).get("licenseNumber", ""), "state": (pv or {}).get("licenseState", ""),
              "expires": (pv or {}).get("licenseExpires", ""), "taxonomy": (pv or {}).get("taxonomy") or data.get("taxonomy", "")}
    fields, missing = [], []
    for k in r["prefill"]:
        v = values.get(k, "")
        if not v:
            missing.append(FIELD_LABELS.get(k, k))
        confirmed = k in data["confirmed"] or (k in ("name", "npi", "license_number", "state", "expires") and pv and pv.get("confirmed"))
        fields.append({"key": k, "label": FIELD_LABELS.get(k, k), "value": v if (reveal or k != "tin") else mask_tin(v),
                       "confirmed": bool(confirmed), "source": data["sources"].get(k, "Entered in Billing Activation")})
    unconfirmed = [f["label"] for f in fields if f["value"] and not f["confirmed"]]
    digest = hashlib.sha256(json.dumps([r["id"], it.payer, it.provider_id, [values.get(k, "") for k in r["prefill"]]]).encode()).hexdigest()
    payer = next((p for p in data["payers"] if p["key"] == it.payer), None)
    return {"requirement": r, "payer": (payer or {}).get("name", ""), "provider": (pv or {}).get("name", ""), "fields": fields, "missing": missing,
            "unconfirmed": unconfirmed, "digest": digest, "registryVersion": reg.VERSION}


def responsible(it: BillingItem, data: dict) -> str:
    r = reg.BY_ID[it.requirement]
    if r["signature"] == "provider" or (r["scope"] == "provider" and r["track"] == "identity"):
        pv = next((x for x in data["providers"] if x["id"] == it.provider_id), None)
        if pv and pv.get("personId"):
            return f"person:{pv['personId']}"
    return "owner"


# ──────────────────────────────────────────────────────────────────────────
#  Readiness: explicit rules only
# ──────────────────────────────────────────────────────────────────────────
def item_ready(it: BillingItem, env: str, today=None):
    """(ready, reason). Pending, unknown, expired, future-effective, other-environment and evidence-less all fail."""
    today = today or _today()
    r = reg.BY_ID.get(it.requirement, {})
    if it.environment != env:
        return False, "Recorded in test mode, so it doesn't count here" if it.environment == "sandbox" else "Recorded in a different environment"
    if it.status not in READY:
        return False, STATUS_LABELS.get(it.status, it.status.title())
    if not (it.evidence_document_id or it.evidence_note or it.evidence_source in ("nppes", "clearinghouse", "signature")):
        return False, "Marked done without evidence"
    if it.status == "APPROVED" and it.track in DATED_TRACKS:
        eff = _date(it.effective_date)
        if not eff:
            return False, "Approved, but no effective date is recorded"
        if eff > today:
            return False, f"Approved, effective {so._fmt_date(it.effective_date)} ({(eff - today).days} days from now)"
    exp = _date(it.expires_at)
    if exp and exp <= today:
        return False, f"Expired {so._fmt_date(it.expires_at)}"
    return True, ""


def route_items(db, org, data, payer_key: str, provider_id: str) -> list:
    """Every blocking requirement for sending 837P claims to this payer for this rendering provider."""
    py = next((p for p in data["payers"] if p["key"] == payer_key), None)
    if not py:
        return []
    need = [r for r in reg.common("clinic") + reg.common("entity") + reg.common("provider") + reg.for_payer(py["type"]) if r["blocksClaims"]]
    if py.get("network") == "out" and py.get("networkConfirmedBy"):
        need = [r for r in need if r["track"] != "contracting"]
    out = []
    for r in need:
        q = select(BillingItem).where(BillingItem.org_key == org, BillingItem.requirement == r["id"], BillingItem.active == 1)
        if r["scope"] == "provider":
            q = q.where(BillingItem.provider_id == provider_id)
        if r["id"] in [x["id"] for x in reg.for_payer(py["type"])]:
            q = q.where(BillingItem.payer == payer_key)
        out.append((r, db.scalar(q)))
    return out


def readiness(db, org, env, data, payer_key: str, provider_id: str, connector=None) -> dict:
    blockers = []
    connector = connector or chx.connector_for(org)
    if not connector.capabilities.get("claim_submit"):
        blockers.append({"requirement": "clearinghouse_connection", "title": "Althais can send claims through a connected clearinghouse",
                         "reason": connector.label, "next": "Althais needs partner access to your clearinghouse. Until then claims wait here, safely.",
                         "responsible": "Althais"})
    if not next((p for p in data["payers"] if p["key"] == payer_key), None):
        blockers.append({"requirement": "payer_setup", "title": "Payer set up for billing", "reason": "This payer isn't in your billing setup",
                         "next": "Add the payer in Billing Activation", "responsible": "owner"})
        return {"ready": False, "blockers": blockers}
    if not next((p for p in data["providers"] if p["id"] == provider_id), None):
        blockers.append({"requirement": "provider_setup", "title": "Rendering provider set up for billing", "reason": "This provider isn't in your billing setup",
                         "next": "Add the provider in Billing Activation", "responsible": "owner"})
    needed = route_items(db, org, data, payer_key, provider_id)
    needed_ids = {it.id for _, it in needed if it is not None}
    for r, it in needed:
        if it is None:
            if r["scope"] == "provider" and not provider_id:
                continue
            blockers.append({"requirement": r["id"], "title": r["title"], "reason": "Not set up yet", "next": "Open Billing Activation",
                             "responsible": "owner"})
            continue
        ok, why = item_ready(it, env)
        if not ok:
            w = waiting_on(db, it, env) if it.status == "NOT_STARTED" else []
            blockers.append({"requirement": r["id"], "itemId": it.id, "title": r["title"], "reason": why,
                             "next": ("Starts automatically after: " + "; ".join(w)) if w else next_action(it, data),
                             "responsible": responsible(it, data)})
    for t in db.scalars(select(BillingTask).where(BillingTask.org_key == org, BillingTask.status == "open", BillingTask.blocking == 1)):
        it = db.get(BillingItem, t.item_id) if t.item_id else None
        if it and it.id in needed_ids:          # an open request only blocks a requirement this route actually needs
            blockers.append({"requirement": it.requirement, "itemId": it.id, "title": t.title, "reason": "Open request", "next": t.title,
                             "responsible": t.assignee})
    return {"ready": not blockers, "blockers": blockers}


def next_action(it: BillingItem, data: dict) -> str:
    r = reg.BY_ID[it.requirement]
    s = it.status
    if s == "ASK_EXISTING":
        return "Tell us whether you already bill this payer"
    if s == "EXISTING_UNVERIFIED":
        return "Upload the approval letter (or the payer's provider ID and effective date) so an admin can confirm it"
    if s == "INFO_NEEDED":
        if r["track"] == "identity" and not r["verify"]:
            return "Upload the supporting document; an admin then confirms it"
        return "Add the missing information" + (f": {it.detail}" if it.detail else "")
    if s == "NEEDS_REVIEW":
        return "An admin needs to check this"
    if s == "SIGNATURE_NEEDED":
        return "Waiting for a signature from " + ("the provider" if r["signature"] == "provider" else "a clinic owner")
    if s == "READY_TO_SUBMIT" or s == "MANUAL":
        return "Submit the prepared application, then record the reference number"
    if s in ("SUBMITTED", "IN_REVIEW"):
        return "Waiting on the payer. Althais will remind you if it takes too long"
    if s == "PAYER_REQUEST":
        return "Answer the payer's request"
    if s == "DENIED":
        return "Review the denial and decide whether to reapply"
    if s == "EXPIRED":
        return "Renew it"
    if s == "APPROVED" and _date(it.effective_date) and _date(it.effective_date) > _today():
        return "Nothing to do. It becomes active on the effective date"
    if s == "NOT_CONNECTED":
        return "Connect a clearinghouse"
    if s in READY:
        return ""
    if r["verify"] == "nppes":
        return "Add the NPI so Althais can check it with the NPI Registry"
    if not r["supported"]:
        return "Althais tracks this one, but you submit it yourself. Record the reference and upload the approval when you get it"
    return "Start it"


# ──────────────────────────────────────────────────────────────────────────
#  Planning: move each item forward as far as verified data allows
# ──────────────────────────────────────────────────────────────────────────
def plan(db, org, env, data, connector) -> list:
    """Advance every item that can move without a person. Returns job requests [(kind, payload, key)]."""
    jobs = []
    items = db.scalars(select(BillingItem).where(BillingItem.org_key == org, BillingItem.active == 1, BillingItem.environment == env)).all()
    by_req = {}
    for it in items:
        by_req.setdefault(it.requirement, []).append(it)

    def deps_ready(it):
        for d in reg.BY_ID[it.requirement]["dependsOn"]:
            cands = [x for x in by_req.get(d, []) if (not x.provider_id or x.provider_id == it.provider_id) and (not x.payer or x.payer == it.payer)]
            if not cands or not all(item_ready(x, env)[0] for x in cands):
                return False
        return True
    for it in items:
        r = reg.BY_ID.get(it.requirement)
        if not r or it.status in READY + ("SUBMITTED", "IN_REVIEW", "DENIED"):
            continue
        py = next((p for p in data["payers"] if p["key"] == it.payer), None) if it.payer else None
        # payer applications: first ask if the clinic already bills this payer, and verify existing enrollment
        if py and r["track"] in ("enrollment", "credentialing", "contracting"):
            if py.get("alreadyBills") is None:
                if it.status != "ASK_EXISTING":
                    set_status(db, it, "ASK_EXISTING", "althais", note=f"Do you already bill {py['name']}?")
                task(db, org, f"ask_existing:{py['key']}", "info", f"Do you already bill {py['name']}?",
                     "If you do, Althais reuses your existing enrollment once you upload proof. If not, Althais prepares new applications.")
                continue
            if py.get("alreadyBills") is True and it.status in ("NOT_STARTED", "ASK_EXISTING"):
                set_status(db, it, "EXISTING_UNVERIFIED", "althais", note="You said you already bill this payer. Upload proof to use it.")
                task(db, org, f"existing:{it.id}", "info", f"Upload proof of your existing {py['name']} {({'credentialing': 'credentialing', 'contracting': 'contract'}).get(r['track'], 'enrollment')}"
                     + (f" ({next((x['name'] for x in data['providers'] if x['id'] == it.provider_id), '')})" if it.provider_id else ""),
                     "An approval letter or the payer's provider ID with its effective date. An admin confirms it before it counts.", item_id=it.id)
                continue
            if py.get("alreadyBills") is True:
                continue
            if it.status in ("ASK_EXISTING", "EXISTING_UNVERIFIED"):      # they said no: new applications instead
                set_status(db, it, "NOT_STARTED", "althais", note="New enrollment needed")
        if r["track"] == "authorization":
            if it.status == "NOT_STARTED":
                set_status(db, it, "SIGNATURE_NEEDED", "althais", note="A clinic owner signs this in Althais")
            digest = hashlib.sha256(f"{org}|clinic_authorization|v1".encode()).hexdigest()
            request_signature(db, org, it, "owner", AUTH_STATEMENT, digest)
            continue
        if r["track"] == "connectivity":
            if not connector.capabilities.get("connection_check"):
                if it.status != "NOT_CONNECTED":
                    set_status(db, it, "NOT_CONNECTED", "althais", note=connector.label)
            elif deps_ready(it):
                jobs.append(("check_connection", {"item": it.id}, f"conn:{it.id}:{_today().isoformat()}"))
            continue
        if r["verify"] == "nppes":
            npi = (data.get("npi") if r["scope"] == "entity" else (next((x for x in data["providers"] if x["id"] == it.provider_id), {}) or {}).get("npi"))
            if not npi:
                if it.status != "INFO_NEEDED":
                    set_status(db, it, "INFO_NEEDED", "althais", note="NPI")
                task(db, org, f"info:{it.id}:npi", "info", "Add the " + ("clinic's organization NPI" if r["scope"] == "entity" else "provider's NPI"),
                     assignee=responsible(it, data), item_id=it.id)
            else:
                jobs.append(("verify_npi", {"item": it.id}, f"npi:{it.id}:{hashlib.sha256(npi.encode()).hexdigest()[:10]}"))
            continue
        if r["verify"] == "clearinghouse" and r["track"] == "edi":
            if not connector.capabilities.get("payer_list"):
                if it.status not in ("NOT_CONNECTED", "EXISTING_UNVERIFIED"):
                    set_status(db, it, "NOT_CONNECTED", "althais", note="Needs a connected clearinghouse to check the payer's rules")
            elif deps_ready(it):
                jobs.append(("payer_requirement", {"item": it.id}, f"payreq:{it.id}:{_today().isoformat()}"))
            continue
        if r["track"] == "identity":            # Tax ID, license: need a document an admin confirms
            if it.status == "NOT_STARTED":
                set_status(db, it, "INFO_NEEDED", "althais", note="Upload the supporting document")
                task(db, org, f"doc:{it.id}", "info", f"Upload proof: {r['title']}", r["evidence"], assignee=responsible(it, data), item_id=it.id)
            continue
        if not deps_ready(it):
            continue
        if not r["supported"]:
            if it.status == "NOT_STARTED":
                set_status(db, it, "MANUAL", "althais", note="Submitted outside Althais")
                task(db, org, f"manual:{it.id}", "manual_submit", f"Submit it yourself: {r['title']}" + (f" ({py['name']})" if py else ""),
                     (r.get("note") or "") + " Then record the reference number here.", item_id=it.id)
            continue
        pk = packet_for(data, it)
        if pk["missing"]:
            if it.status != "INFO_NEEDED" or it.detail != ", ".join(pk["missing"]):
                set_status(db, it, "INFO_NEEDED", "althais", note=", ".join(pk["missing"]))
            task(db, org, f"info:{it.id}:{pk['digest'][:8]}", "info", f"Add missing details for {r['title']}", ", ".join(pk["missing"]),
                 assignee=responsible(it, data), item_id=it.id)
            continue
        if pk["unconfirmed"]:
            if it.status != "NEEDS_REVIEW":
                set_status(db, it, "NEEDS_REVIEW", "althais", note="Confirm: " + ", ".join(pk["unconfirmed"]))
            task(db, org, f"confirm:{pk['digest'][:12]}", "review", "Confirm the prefilled billing details", ", ".join(pk["unconfirmed"]))
            continue
        if r["signature"]:
            signer = "owner" if r["signature"] == "owner" else responsible(it, data)
            s = request_signature(db, org, it, signer, SIGN_STATEMENT.format(title=r["title"]), pk["digest"])
            if s.status != "signed":
                if it.status != "SIGNATURE_NEEDED":
                    set_status(db, it, "SIGNATURE_NEEDED", "althais")
                continue
        it.packet_digest = pk["digest"]
        if it.status != "READY_TO_SUBMIT":
            set_status(db, it, "READY_TO_SUBMIT", "althais", note="Application prepared from verified details")
        jobs.append(("submit_enrollment", {"item": it.id}, f"submit:{it.id}:{pk['digest'][:16]}"))
    return jobs


AUTH_STATEMENT = ("I am an owner or authorized administrator of this clinic. I authorize Althais to prepare billing enrollment "
                  "applications from the information our clinic confirms, and to send our claims through our connected clearinghouse, "
                  "only for services our clinic provided and documented. I understand Althais never signs or attests for anyone.")
SIGN_STATEMENT = ("I have reviewed the details prepared for \"{title}\" and confirm they are accurate. I approve this application for "
                  "submission. This approval is mine; if the details change, I'll be asked to review them again.")


# ──────────────────────────────────────────────────────────────────────────
#  Individual billers: training, practice claim, acknowledgment, access
# ──────────────────────────────────────────────────────────────────────────
BILLER_ITEMS = [
    {"key": "althais_training", "type": "training", "trainingKey": "althais_training", "owner": "employee", "title": "Althais Training", "dueDays": 7},
    {"key": "billing_practice_claim", "type": "training", "trainingKey": "billing_practice_claim", "owner": "employee",
     "title": "Practice Claim", "dueDays": 10, "required": False,
     "description": "Build and check a claim with made-up patient data. Needed before you can send real claims."},
    {"key": "billing_ack", "type": "form", "formKey": "billing_ack", "owner": "employee", "title": "Claim Submission Responsibilities", "dueDays": 10,
     "required": False, "description": "Read and acknowledge your responsibilities when sending claims."},
    {"key": "billing_access", "type": "manager", "owner": "manager", "title": "Claim Submission Access", "dueDays": 14, "required": False,
     "description": "A clinic owner approves claim submission access once the biller's training, practice claim and acknowledgment are done."},
]
BILLER_KEYS = [i["key"] for i in BILLER_ITEMS]


def biller_items_hook(doc, role, have):
    """Registered with staff_onboarding: only the Biller role gets billing onboarding items."""
    if _family(role) != "biller":
        return []
    return [dict(i, dependsOn=[], reminderDaysBefore=2) for i in BILLER_ITEMS if i["key"] not in have]


def sync_billers(db, org, doc) -> bool:
    """Existing billers get the billing items; people moved out of the Biller role have them closed and their access revoked."""
    changed = False
    for p in doc["people"]:
        is_b = _family(p.get("role", "")) == "biller" and p.get("lifecycle") not in ("OFFBOARDED",)
        have = {t["key"] for t in p["requirements"]}
        if is_b:
            add = [i for i in biller_items_hook(doc, p.get("role", ""), have)]
            if add:
                p["requirements"] = so.build_tasks(p, p["requirements"] + add)
                for t in p["requirements"]:
                    if t.get("type") == "training" and t.get("trainingKey") == "althais_training":
                        so.training_record(doc, p, t)
                changed = True
        else:
            for t in p["requirements"]:
                if t["key"] in BILLER_KEYS[1:] and t.get("status") not in ("COMPLETE", "CLOSED"):
                    t["status"], t["required"] = "CLOSED", False
                    changed = True
            for a in db.scalars(select(BillerAccess).where(BillerAccess.org_key == org, BillerAccess.person_id == p["id"], BillerAccess.status != "REVOKED")):
                a.status, a.revoked_at = "REVOKED", _now()
                changed = True
    return changed


def biller_checklist(doc, p) -> dict:
    t = {x["key"]: x for x in p["requirements"]}
    rec = next((r for r in doc["trainings"] if r.get("personId") == p["id"] and r.get("trainingKey") == "althais_training"), {})
    steps = [
        {"key": "althais_training", "title": "Finish Althais Training", "done": bool(rec.get("completed")), "link": "/portal#course"},
        {"key": "billing_practice_claim", "title": "Build a practice claim (made-up data)", "done": (t.get("billing_practice_claim") or {}).get("status") == "COMPLETE", "link": "/portal#billing"},
        {"key": "billing_ack", "title": "Acknowledge claim submission responsibilities", "done": (t.get("billing_ack") or {}).get("status") == "COMPLETE", "link": "/portal#forms"},
    ]
    return {"steps": steps, "readyForAccess": all(s["done"] for s in steps)}


def user_for_person(db, org, pid):
    m = db.scalar(select(so.OrgMembership).where(so.OrgMembership.org_key == org, so.OrgMembership.staff_person_id == pid))
    return m.user_id if m else None


def access_row(db, org, user_id):
    return db.scalar(select(BillerAccess).where(BillerAccess.org_key == org, BillerAccess.user_id == user_id))


def evaluate_billers(db, org, doc, data) -> bool:
    """Checklist -> access. Only an owner's approval, or the owner-approved rule, turns access on."""
    changed = False
    rule = (data.get("policy") or {}).get("autoActivateBillers") or {}
    for p in doc["people"]:
        if _family(p.get("role", "")) != "biller" or p.get("lifecycle") == "OFFBOARDED":
            continue
        uid = user_for_person(db, org, p["id"])
        if not uid:
            continue
        a = access_row(db, org, uid)
        if not a:
            a = BillerAccess(org_key=org, user_id=uid, person_id=p["id"], status="PENDING")
            db.add(a)
            db.flush()
        if a.status == "ACTIVE":
            continue
        cl = biller_checklist(doc, p)
        if not cl["readyForAccess"]:
            continue
        if rule.get("enabled") and rule.get("approvedBy") and p.get("lifecycle") == "ACTIVE":
            a.status, a.granted_via, a.granted_at, a.granted_by = "ACTIVE", "rule", _now(), f"Rule approved by {rule['approvedBy']}"
            a.rule_snapshot = json.dumps({"requires": ["althais_training", "billing_practice_claim", "billing_ack", "active staff member"],
                                          "approvedBy": rule["approvedBy"], "approvedAt": rule.get("approvedAt")})
            so.set_task(p, "billing_access", "COMPLETE", actor="Althais (owner-approved rule)")
            close_tasks(db, org, f"access:{p['id']}")
            changed = True
        else:
            task(db, org, f"access:{p['id']}", "access", f"Approve claim submission for {p.get('name', 'a biller')}",
                 "Their training, practice claim and acknowledgment are done.")
    return changed


# ──────────────────────────────────────────────────────────────────────────
#  Jobs: persistent, idempotent, retried, escalated
# ──────────────────────────────────────────────────────────────────────────
def enqueue(db, org, kind, payload=None, key=None, delay_s=0, repeat=False) -> bool:
    """One job per key. repeat=True (re-checks) allows a new run once the previous one with that key has finished;
    one-time work (submitting an application) keeps its key forever, so it can never run twice."""
    key = f"{org}|{key or kind}"[:191]
    old = db.scalar(select(BillingJob).where(BillingJob.idempotency_key == key))
    if old:
        if not repeat or old.status in ("queued", "running"):
            return False
        old.idempotency_key = f"{key[:170]}#{old.id}"
        db.flush()
    db.add(BillingJob(org_key=org, kind=kind, payload=json.dumps(payload or {}), idempotency_key=key,
                      next_run_at=_now() + dt.timedelta(seconds=delay_s)))
    try:
        with db.begin_nested():
            db.flush()
    except IntegrityError:
        return False
    return True


def request_sync(db, org, reason=""):
    """Re-evaluate readiness soon (one queued sync per clinic per minute)."""
    enqueue(db, org, "sync", {"reason": reason}, key="sync", repeat=True)


def _run_sync(db, org):
    env = chx.environment_for(org)
    row, data = load_profile(db, org)
    staff_row, doc = so.load_staff(db, org)
    if prefill(doc, data):
        save_profile(db, row, data)
    sync_items(db, org, env, data)
    connector = chx.connector_for(org)
    for kind, payload, key in plan(db, org, env, data, connector):
        enqueue(db, org, kind, payload, key=key)
    staff_changed = sync_billers(db, org, doc)
    staff_changed = evaluate_billers(db, org, doc, data) or staff_changed
    for p in doc["people"]:
        staff_changed = so.check_portal_lock(db, org, doc, p) or staff_changed      # anyone who finished while offline
    if staff_changed:
        for p in doc["people"]:
            so.refresh(p)
        so.save_staff(db, org, staff_row, doc)
    _release_held(db, org, env, data)


def _run_verify_npi(db, org, payload):
    it = db.get(BillingItem, payload["item"])
    if not it or it.org_key != org:
        return
    row, data = load_profile(db, org)
    if it.scope == "entity":
        npi, names = data.get("npi", ""), {"legal": data.get("legalName", "")}
    else:
        pv = next((x for x in data["providers"] if x["id"] == it.provider_id), None) or {}
        npi, parts = pv.get("npi", ""), (pv.get("name", "") or "").split()
        names = {"first": [parts[0]] if parts else [], "last": [parts[-1]] if parts else []}
    if it.scope == "entity":
        res = _nppes_org(npi, data.get("legalName", ""))
    else:
        res = cv.verify_npi(npi, names)
    status = res.get("status")
    if status == "VERIFIED":
        it.evidence_source, it.verified_by, it.verified_at = "nppes", cv.NPPESProvider.name, _now()
        set_status(db, it, "VERIFIED", "nppes", note=res.get("details", "")[:500])
        close_tasks(db, org, f"info:{it.id}")
    elif status == "ERROR":
        raise RuntimeError("NPI Registry didn't answer")
    else:
        set_status(db, it, "NEEDS_REVIEW", "nppes", note=res.get("details", "") or "The NPI Registry didn't confirm this NPI.")
        task(db, org, f"npi_review:{it.id}:{hashlib.sha256(npi.encode()).hexdigest()[:8]}", "review", "Check an NPI the NPI Registry didn't confirm",
             res.get("details", "")[:500], item_id=it.id, blocking=1)


def _nppes_org(npi: str, legal: str) -> dict:
    if not cv.npi_valid(npi or ""):
        return {"status": "NOT_RUN", "details": "Not a valid NPI."}
    try:
        rec = cv.NPPESProvider().lookup(npi)
    except Exception as e:
        return {"status": "ERROR", "details": type(e).__name__}
    if not rec:
        return {"status": "NOT_FOUND", "details": "This NPI isn't in the NPI Registry."}
    basic = rec.get("basic") or {}
    if rec.get("enumeration_type") != "NPI-2":
        return {"status": "MISMATCH", "details": "This is an individual NPI, not an organization NPI."}
    if basic.get("status") != "A":
        return {"status": "MISMATCH", "details": "This NPI isn't active in the NPI Registry."}
    org_name = (basic.get("organization_name") or "").lower()
    norm = lambda s: re.sub(r"[^a-z0-9]", "", s.lower().replace(" llc", "").replace(" inc", "").replace(" pc", "").replace(" pllc", ""))
    if legal and norm(legal) and norm(legal) not in norm(org_name) and norm(org_name) not in norm(legal):
        return {"status": "MISMATCH", "details": "The NPI Registry lists a different organization name for this NPI."}
    return {"status": "VERIFIED", "details": "Active organization NPI in the NPI Registry."}


def _run_submit(db, org, payload):
    it = db.get(BillingItem, payload["item"])
    if not it or it.org_key != org or it.status != "READY_TO_SUBMIT":
        return
    row, data = load_profile(db, org)
    pk = packet_for(data, it, reveal=True)
    if pk["digest"] != it.packet_digest:         # changed since signing: back through review
        set_status(db, it, "NEEDS_REVIEW", "althais", note="Details changed after approval. Review and sign again.")
        return
    connector = chx.connector_for(org)
    r = reg.BY_ID[it.requirement]
    if connector.capabilities.get("enrollment_submit") and r["supported"]:
        res = connector.submit_enrollment({"requirement": r["id"], "fields": {f["key"]: f["value"] for f in pk["fields"]}})
        if res.ok:
            it.submitted_at = _now()
            set_status(db, it, "SUBMITTED", "clearinghouse", reference=res.reference, note=res.message, raw_digest=res.raw_digest,
                       environment=res.environment, external_id=f"submit:{it.id}:{it.packet_digest[:16]}")
            return
        raise RuntimeError(res.message or "Submission failed")
    task(db, org, f"manual:{it.id}:{it.packet_digest[:12]}", "manual_submit", f"Submit the prepared application: {r['title']}" + (f" ({pk['payer']})" if pk["payer"] else ""),
         f"Althais can't submit this for you yet. Download the prepared packet, submit it through {r['source']['title']}, then record the reference number.",
         item_id=it.id)


def _run_poll(db, org, payload):
    it = db.get(BillingItem, payload["item"])
    connector = chx.connector_for(org)
    if not it or not it.reference or not connector.capabilities.get("enrollment_status"):
        return
    res = connector.enrollment_status(it.reference)
    apply_external(db, it, res.status, "poll", res.reference, res.message, res.environment, external_id=None, raw_digest=res.raw_digest)


def _run_connection(db, org, payload):
    it = db.get(BillingItem, payload["item"])
    connector = chx.connector_for(org)
    if not it or not connector.capabilities.get("connection_check"):
        return
    res = connector.check_connection({})
    if res.ok and res.status == "CONNECTED":
        it.evidence_source = "clearinghouse"
        set_status(db, it, "VERIFIED", "clearinghouse", reference=res.reference, note=res.message, environment=res.environment)


def _run_payer_requirement(db, org, payload):
    it = db.get(BillingItem, payload["item"])
    connector = chx.connector_for(org)
    if not it or not connector.capabilities.get("payer_list"):
        return
    res = connector.payer_requirement(it.payer, it.transaction or "837P")
    if res.status == "NOT_REQUIRED":
        it.evidence_source = "clearinghouse"
        set_status(db, it, "NOT_REQUIRED", "clearinghouse", reference=res.reference, note=res.message, environment=res.environment)
    elif res.status == "REQUIRED" and it.status in ("NOT_STARTED", "NOT_CONNECTED"):
        set_status(db, it, "NOT_STARTED", "clearinghouse", note="This payer requires electronic claims enrollment", environment=res.environment)
        request_sync(db, org, "payer rule")


def _run_reminders(db, org):
    """Overdue tasks get a reminder; applications stuck with a payer get a follow-up task (escalation)."""
    today = _today()
    for t in db.scalars(select(BillingTask).where(BillingTask.org_key == org, BillingTask.status == "open")):
        d = _date(t.due_date)
        if d and d < today and (today - d).days in (0, 1, 7, 14):
            notify_assignee(db, org, t.assignee, "Billing setup task overdue", f"This billing setup task is overdue: {t.title}.")
            if (today - d).days >= 7:
                task(db, org, f"escalate:task:{t.id}", "escalation", f"Overdue for a week: {t.title}", "Reassign it or follow up.")
    for it in db.scalars(select(BillingItem).where(BillingItem.org_key == org, BillingItem.active == 1, BillingItem.status.in_(("SUBMITTED", "IN_REVIEW")))):
        if it.submitted_at and (today - it.submitted_at.date()).days >= 30:
            n = (today - it.submitted_at.date()).days // 30
            task(db, org, f"followup:{it.id}:{n}", "escalation", f"Follow up with the payer: {reg.BY_ID[it.requirement]['title']}",
                 f"Submitted {so._fmt_date(it.submitted_at.date().isoformat())}, {(today - it.submitted_at.date()).days} days ago, with no decision recorded.",
                 item_id=it.id)
        if it.reference and chx.connector_for(org).capabilities.get("enrollment_status"):
            enqueue(db, org, "poll", {"item": it.id}, key=f"poll:{it.id}:{today.isoformat()}")
    for it in db.scalars(select(BillingItem).where(BillingItem.org_key == org, BillingItem.active == 1, BillingItem.status.in_(READY))):
        exp = _date(it.expires_at)
        if exp and exp <= today:
            set_status(db, it, "EXPIRED", "althais", note=f"Expired {so._fmt_date(it.expires_at)}")
            task(db, org, f"renew:{it.id}:{it.expires_at}", "info", f"Renew: {reg.BY_ID[it.requirement]['title']}", item_id=it.id, blocking=0)
        elif exp and (exp - today).days in (60, 30, 7):
            notify_assignee(db, org, "owner", "A billing approval expires soon", f"{reg.BY_ID[it.requirement]['title']} expires {so._fmt_date(it.expires_at)}.")


HANDLERS = {"sync": lambda db, org, p: _run_sync(db, org), "verify_npi": _run_verify_npi, "submit_enrollment": _run_submit, "poll": _run_poll,
            "check_connection": _run_connection, "payer_requirement": _run_payer_requirement, "reminders": lambda db, org, p: _run_reminders(db, org)}
MAX_ATTEMPTS = 5
_POOL = ThreadPoolExecutor(max_workers=4)


def _claim_jobs(db, limit=25) -> list:
    now = _now()
    jobs = db.scalars(select(BillingJob).where(BillingJob.status.in_(("queued", "running")), BillingJob.next_run_at <= now)
                      .order_by(BillingJob.next_run_at).limit(limit)).all()
    mine = []
    for j in jobs:
        if j.status == "running" and j.lease_until and j.lease_until > now:
            continue
        j.status, j.lease_until, j.attempts = "running", now + dt.timedelta(minutes=5), j.attempts + 1
        mine.append(j.id)
    db.commit()
    return mine


def _run_one(job_id: int):
    with SessionLocal() as db:
        j = db.get(BillingJob, job_id)
        if not j or j.status != "running":
            return
        try:
            HANDLERS[j.kind](db, j.org_key, json.loads(j.payload or "{}"))
            j.status, j.finished_at, j.last_error = "done", _now(), ""
            if j.kind not in ("sync", "reminders"):
                request_sync(db, j.org_key, "after " + j.kind)      # anything waiting on this step can start now
            db.commit()
        except Exception as e:
            db.rollback()
            j = db.get(BillingJob, job_id)
            j.last_error = type(e).__name__ + ": " + str(e)[:300]
            if j.attempts >= MAX_ATTEMPTS:
                j.status = "failed"
                task(db, j.org_key, f"jobfail:{j.id}", "escalation", "An automatic billing step keeps failing",
                     f"Althais tried {j.attempts} times ({j.kind}). Someone from Althais support may need to look.")
            else:
                j.status, j.next_run_at = "queued", _now() + dt.timedelta(minutes=2 ** j.attempts)
            db.commit()


def run_due(limit=25) -> int:
    """Run what's due. Independent jobs run side by side (one at a time on SQLite, which allows a single writer);
    each has its own session and retries."""
    with SessionLocal() as db:
        ids = _claim_jobs(db, limit)
    if engine.dialect.name == "sqlite":
        for i in ids:
            _run_one(i)
    else:
        list(_POOL.map(_run_one, ids))
    return len(ids)


def run_until_idle(max_rounds=20):
    for _ in range(max_rounds):
        if not run_due():
            break


def daily(db):
    orgs = {r.org_key for r in db.scalars(select(BillingProfile))}
    for org in orgs:
        enqueue(db, org, "reminders", key=f"reminders:{_today().isoformat()}")
        request_sync(db, org, "daily")
    db.commit()


# ──────────────────────────────────────────────────────────────────────────
#  Claims: check, send or hold
# ──────────────────────────────────────────────────────────────────────────
def validate_claim(c: dict) -> list:
    """Deterministic checks run at the moment of sending (so they're always current)."""
    probs = []
    if not (c.get("patient") or "").strip():
        probs.append("The claim has no patient name.")
    if not (c.get("payer") or "").strip() or re.fullmatch(r"[\u2014-]", (c.get("payer") or "").strip()):
        probs.append("The patient has no insurance payer on the claim.")
    if not (c.get("insuredId") or "").strip():
        probs.append("The insurance member ID is missing.")
    cpts = [x for x in c.get("cpts") or [] if (x.get("code") or "").strip()]
    icds = [x for x in c.get("icds") or [] if (x.get("code") or "").strip()]
    if not cpts:
        probs.append("There's no procedure (CPT) code.")
    if not icds:
        probs.append("There's no diagnosis (ICD-10) code.")
    for x in cpts:
        if not re.fullmatch(r"\d{4}[0-9A-Z]", x["code"].strip().upper()):
            probs.append(f"{x['code']} doesn't look like a CPT/HCPCS code.")
    for x in icds:
        if not re.fullmatch(r"[A-Z][0-9][0-9A-Z](\.?[0-9A-Z]{1,4})?", x["code"].strip().upper()):
            probs.append(f"{x['code']} doesn't look like an ICD-10 code.")
    try:
        if float(c.get("totalAmt") or c.get("amount") or 0) <= 0:
            probs.append("The claim total is $0.")
    except (TypeError, ValueError):
        probs.append("The claim total isn't a number.")
    if _date(c.get("svcDate")) and _date(c.get("svcDate")) > _today():
        probs.append("The date of service is in the future.")
    return probs


def content_hash(c: dict) -> str:
    keep = {k: c.get(k) for k in ("patient", "mrn", "payer", "insuredId", "provider", "svcDate", "totalAmt")}
    keep["cpts"] = sorted([(x.get("code"), x.get("modifier", ""), x.get("units", 1)) for x in c.get("cpts") or []])
    keep["icds"] = sorted([x.get("code") for x in c.get("icds") or []])
    return hashlib.sha256(json.dumps(keep, sort_keys=True, default=str).encode()).hexdigest()


def provider_for(data, name: str):
    n = re.sub(r"[^a-z]", "", (name or "").lower().replace("dr.", "").replace("dr ", ""))
    for pv in data["providers"]:
        pn = re.sub(r"[^a-z]", "", (pv.get("name") or "").lower())
        if n and pn and (n == pn or n in pn or pn in n):
            return pv
    return None


def claim_decision(db, org, env, data, user, c: dict, connector=None) -> dict:
    connector = connector or chx.connector_for(org)
    blockers = [{"requirement": "claim_check", "title": "Claim check", "reason": p, "next": "Fix the claim, then send it again", "responsible": "biller"}
                for p in validate_claim(c)]
    a = access_row(db, org, user.id)
    pid = person_id_of(db, user, org)
    if a and a.status == "ACTIVE" and a.granted_via != "self" and pid:
        _, doc = so.load_staff(db, org)
        p = next((x for x in doc["people"] if x["id"] == pid), None)
        if not p or _family(p.get("role", "")) != "biller" or p.get("lifecycle") in ("OFFBOARDED", "SUSPENDED"):
            a = None      # their role changed since access was granted: treated as no access until an owner looks again
    if a and a.status == "ACTIVE" and a.granted_via == "self" and not is_owner(user):
        a = None
    if not a or a.status != "ACTIVE":
        blockers.append({"requirement": "biller_access", "title": "Your claim submission access",
                         "reason": "You don't have claim submission access yet" if not a or a.status == "PENDING" else "Your claim submission access was turned off",
                         "next": "Finish your billing checklist; a clinic owner then approves access", "responsible": "owner"})
    pv = provider_for(data, c.get("provider"))
    payer_key = reg.payer_key(c.get("payer"))
    rd = readiness(db, org, env, data, payer_key, pv["id"] if pv else "", connector)
    blockers += rd["blockers"]
    return {"ready": not blockers, "blockers": blockers, "payer": payer_key, "providerId": pv["id"] if pv else ""}


def transmit(db, org, env, data, user, c: dict, release=False) -> dict:
    connector = chx.connector_for(org)
    h = content_hash(c)
    cid = str(c.get("claimId") or "")[:64]
    sent = db.scalar(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.claim_id == cid,
                                                     ClaimTransmission.delivery == "SENT"))
    if sent:
        if sent.content_hash == h:
            return {"status": "already_sent", "reference": sent.reference, "environment": sent.environment,
                    "message": "This claim was already sent. It wasn't sent again."}
        return {"status": "duplicate_blocked", "blockers": [{"title": "Already sent", "reason": "This claim number was already sent with different details",
                                                             "next": "Send a corrected or replacement claim instead", "responsible": "biller"}]}
    dec = claim_decision(db, org, env, data, user, c, connector)
    hold = db.scalar(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.claim_id == cid, ClaimTransmission.delivery == "HELD"))
    if not dec["ready"]:
        if not hold:
            hold = ClaimTransmission(org_key=org, claim_id=cid, content_hash=h, idempotency_key=f"{org}|{cid}|{h}|hold"[:191], payer=dec["payer"],
                                     provider_id=dec["providerId"], environment=env, user_id=user.id, held=1)
            db.add(hold)
        hold.content_hash, hold.blockers, hold.snapshot, hold.user_id = h, json.dumps(dec["blockers"]), json.dumps(c), user.id
        return {"status": "held", "blockers": dec["blockers"],
                "message": "Saved and held. It wasn't sent. It'll be ready to send as soon as these are done."}
    key = f"{org}|{cid}|{h}"[:191]
    tx = hold or ClaimTransmission(org_key=org, claim_id=cid, content_hash=h, idempotency_key=key, payer=dec["payer"], provider_id=dec["providerId"],
                                   environment=env, user_id=user.id)
    if not hold:
        db.add(tx)
    res = connector.submit_claim(c, idempotency_key=key)
    if not res.ok:
        tx.delivery, tx.held, tx.blockers = "FAILED", 1, json.dumps([{"title": "Clearinghouse", "reason": res.message, "next": "Try again later", "responsible": "Althais"}])
        return {"status": "failed", "message": res.message}
    tx.idempotency_key, tx.held, tx.blockers, tx.delivery, tx.reference, tx.sent_at = key, 0, "[]", "SENT", res.reference, _now()
    tx.ack, tx.payer_status, tx.environment, tx.snapshot = "PENDING", "PENDING", res.environment, json.dumps(c)
    db.add(BillingEvent(org_key=org, claim_id=cid, kind="claim_sent", to_status="SENT", source="clearinghouse", environment=res.environment,
                        reference=res.reference, raw_digest=res.raw_digest, actor=user.email))
    return {"status": "sent", "reference": res.reference, "environment": res.environment, "message": res.message,
            "test": res.environment == "sandbox"}


def _release_held(db, org, env, data):
    policy = (data.get("policy") or {}).get("autoReleaseHeld") or {}
    for tx in db.scalars(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.delivery == "HELD")).all():
        u = db.get(User, tx.user_id)
        if not u:
            continue
        c = json.loads(tx.snapshot or "{}")
        dec = claim_decision(db, org, env, data, u, c)
        tx.blockers = json.dumps(dec["blockers"])
        if not dec["ready"]:
            continue
        if policy.get("enabled") and policy.get("approvedBy"):
            r = transmit(db, org, env, data, u, c, release=True)
            if r["status"] == "sent":
                notify_assignee(db, org, f"user:{u.id}", "A held claim was sent", "A claim you queued was sent automatically now that billing is ready.")
        else:
            task(db, org, f"release:{tx.claim_id}:{tx.content_hash[:10]}", "info", f"A held claim can be sent now ({tx.claim_id})",
                 "Every check passes. Open the claim and send it.", assignee=f"user:{u.id}")


def apply_external(db, it: BillingItem, status: str, source, reference, message, environment, external_id=None, raw_digest="",
                   occurred_at=None, effective_date=""):
    """Status from a clearinghouse or payer integration. Sandbox results can't touch a production item."""
    if environment != it.environment:
        return False
    mapping = {"APPROVED": "APPROVED", "DENIED": "DENIED", "PENDING": "IN_REVIEW", "REQUEST": "PAYER_REQUEST", "NOT_REQUIRED": "NOT_REQUIRED"}
    to = mapping.get(status)
    if not to:
        return False
    if to == "APPROVED" and not effective_date:
        to = "NEEDS_REVIEW"          # approval without an effective date can't count: a person checks it
        message = "Approval received without an effective date. " + (message or "")
    ok = set_status(db, it, to, source, note=message, reference=reference, external_id=external_id, occurred_at=occurred_at,
                    raw_digest=raw_digest, environment=environment)
    if ok and to == "APPROVED":
        it.effective_date, it.evidence_source, it.verified_at, it.verified_by = effective_date, "clearinghouse", _now(), source
    if ok and to == "PAYER_REQUEST":
        task(db, it.org_key, f"payer_request:{it.id}:{external_id or reference}", "payer_request",
             f"A payer asked for more information: {reg.BY_ID[it.requirement]['title']}", "Open the request in Billing Activation.", item_id=it.id, blocking=1)
        notify_assignee(db, it.org_key, "owner", "A payer asked for more information",
                        "A payer asked for more information about one of your enrollments. Open Billing Activation to answer.")
    if ok and to == "NEEDS_REVIEW":
        task(db, it.org_key, f"review_ext:{it.id}:{external_id or reference}", "review", "Check an approval that arrived without an effective date",
             item_id=it.id)
    return ok


# ──────────────────────────────────────────────────────────────────────────
#  Documents: AI reads, rules decide, people confirm
# ──────────────────────────────────────────────────────────────────────────
BILLING_DOC_TYPES = {"w9": "W-9", "irs_tin_letter": "IRS Tax ID letter (CP 575 or 147C)", "payer_approval": "Payer approval or welcome letter",
                     "medical_license": "Professional license", "malpractice_coi": "Malpractice insurance certificate",
                     "business_license": "Business license", "clia_certificate": "CLIA certificate", "bank_letter": "Bank letter (for EFT)",
                     "other": "Other"}
NO_EXTRACTION = {"bank_letter"}      # never extract account numbers


def check_document(data: dict, doc_type: str, fields: dict, provider=None) -> list:
    """Deterministic checks on what the reader extracted. Anything here sends the document to a person."""
    issues = []
    norm = lambda s: re.sub(r"[^a-z0-9]", "", (s or "").lower())
    exp = _date(fields.get("expiration_date"))
    if exp and exp <= _today():
        issues.append("It has expired.")
    elif exp and (exp - _today()).days < 30:
        issues.append(f"It expires soon ({so._fmt_date(exp.isoformat())}).")
    if doc_type in ("w9", "irs_tin_letter"):
        tin = re.sub(r"\D", "", fields.get("tin", ""))
        if tin and data.get("tin") and tin != re.sub(r"\D", "", data["tin"]):
            issues.append("The Tax ID doesn't match the billing profile.")
        name = fields.get("legal_name") or fields.get("business_name")
        if name and data.get("legalName") and norm(name) not in norm(data["legalName"]) and norm(data["legalName"]) not in norm(name):
            issues.append("The legal name doesn't match the billing profile.")
    if doc_type == "payer_approval":
        if not fields.get("effective_date"):
            issues.append("No effective date was found.")
        npi = re.sub(r"\D", "", fields.get("npi", ""))
        known = {re.sub(r"\D", "", data.get("npi", ""))} | {re.sub(r"\D", "", p.get("npi", "")) for p in data["providers"]}
        if npi and npi not in known:
            issues.append("The NPI on the letter isn't one in your billing profile.")
    if doc_type == "medical_license" and provider:
        if fields.get("license_number") and provider.get("licenseNumber") and norm(fields["license_number"]) != norm(provider["licenseNumber"]):
            issues.append("The license number doesn't match the provider's record.")
        if fields.get("holder_name") and provider.get("name") and not set(norm(x) for x in provider["name"].split()) & set(norm(x) for x in fields["holder_name"].split()):
            issues.append("The name on the license doesn't match the provider.")
    return issues


# ──────────────────────────────────────────────────────────────────────────
#  API: the Billing Activation page
# ──────────────────────────────────────────────────────────────────────────
SOURCE_LABELS = {"althais": "Althais", "nppes": "NPI Registry", "manual": "Confirmed by a person", "signature": "Signature",
                 "clearinghouse": "Clearinghouse", "webhook": "Clearinghouse update", "poll": "Status check", "document": "Document"}


def waiting_on(db, it: BillingItem, env: str) -> list:
    """The earlier requirements this one is waiting for (titles)."""
    out = []
    for d in reg.BY_ID[it.requirement]["dependsOn"]:
        cands = db.scalars(select(BillingItem).where(BillingItem.org_key == it.org_key, BillingItem.requirement == d, BillingItem.active == 1)).all()
        cands = [x for x in cands if (not x.provider_id or x.provider_id == it.provider_id) and (not x.payer or x.payer == it.payer)]
        if not cands or not all(item_ready(x, env)[0] for x in cands):
            out.append(reg.BY_ID[d]["title"])
    return out


def _item_json(db, it: BillingItem, data: dict, env: str, owner: bool) -> dict:
    r = reg.BY_ID.get(it.requirement, {})
    ok, why = item_ready(it, env)
    nxt = next_action(it, data)
    if it.status == "NOT_STARTED":
        w = waiting_on(db, it, env)
        if w:
            nxt = "Starts automatically after: " + "; ".join(w)
    py = next((p for p in data["payers"] if p["key"] == it.payer), None)
    pv = next((p for p in data["providers"] if p["id"] == it.provider_id), None)
    days = (_today() - it.submitted_at.date()).days if it.submitted_at and it.status in ("SUBMITTED", "IN_REVIEW", "PAYER_REQUEST") else None
    resp = responsible(it, data)
    return {"id": it.id, "requirement": it.requirement, "title": r.get("title", it.requirement), "scope": it.scope, "track": it.track,
            "payer": (py or {}).get("name", ""), "payerKey": it.payer, "provider": (pv or {}).get("name", ""), "providerId": it.provider_id,
            "transaction": it.transaction, "status": it.status, "statusLabel": STATUS_LABELS.get(it.status, it.status), "ready": ok, "why": why,
            "next": nxt, "responsible": _resp_label(resp, data), "submittedAt": _iso(it.submitted_at.date()) if it.submitted_at else "",
            "daysPending": days, "effectiveDate": it.effective_date, "expiresAt": it.expires_at, "reference": it.reference if owner else ("•••" if it.reference else ""),
            "evidence": {"documentId": it.evidence_document_id, "note": it.evidence_note if owner else "", "source": it.evidence_source,
                         "verifiedBy": it.verified_by, "verifiedAt": _iso(it.verified_at.date()) if it.verified_at else ""},
            "lastUpdate": _iso(it.last_status_at) if it.last_status_at else "", "lastUpdateSource": SOURCE_LABELS.get(it.last_status_source, it.last_status_source), "detail": it.detail,
            "environment": it.environment, "blocksClaims": r.get("blocksClaims", True), "supported": r.get("supported", True),
            "source": r.get("source", {}), "dependsOn": r.get("dependsOn", [])}


def _resp_label(key: str, data: dict) -> str:
    if key == "owner":
        return "Clinic owner or admin"
    if key.startswith("person:"):
        pv = next((p for p in data["providers"] if p.get("personId") == key[7:]), None)
        return (pv or {}).get("name") or "Provider"
    return key


def _metrics(db, org, data, doc) -> dict:
    items = db.scalars(select(BillingItem).where(BillingItem.org_key == org, BillingItem.active == 1)).all()
    prep = [(i.submitted_at.date() - i.created_at.date()).days for i in items if i.submitted_at]
    approvals = []
    for i in items:
        if i.status == "APPROVED" and i.submitted_at and i.verified_at:
            approvals.append((i.verified_at.date() - i.submitted_at.date()).days)
    staff = []
    for a in db.scalars(select(BillerAccess).where(BillerAccess.org_key == org, BillerAccess.status == "ACTIVE")):
        if a.granted_at:
            staff.append((a.granted_at.date() - a.assigned_at.date()).days)
    first = db.scalar(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.payer_status == "ACCEPTED")
                      .order_by(ClaimTransmission.payer_at))
    start = _date(data.get("setupStartedAt"))
    avg = lambda xs: round(sum(xs) / len(xs), 1) if xs else None
    return {"staffReadinessDays": avg(staff), "applicationPrepDays": avg(prep), "externalApprovalDays": avg(approvals),
            "daysToFirstAcceptedClaim": (first.payer_at.date() - start).days if first and first.payer_at and start else None,
            "note": "Payers decide how long approvals take. Althais tracks the time; it doesn't control it."}


@router.get("/api/billing/overview")
def overview(c: Ctx = Depends(ctx)):
    db, org = c.db, c.org
    ensure_area(c.user, "billing_activation")
    row, data = load_profile(db, org)
    if prefill(c.doc, data):
        save_profile(db, row, data)
    sync_items(db, org, c.env, data)
    request_sync(db, org, "page opened")
    db.commit()
    connector = chx.connector_for(org)
    mine = c.assignee_keys()
    tasks = db.scalars(select(BillingTask).where(BillingTask.org_key == org, BillingTask.status == "open").order_by(BillingTask.created_at)).all()
    sigs = db.scalars(select(BillingSignature).where(BillingSignature.org_key == org, BillingSignature.status == "requested")).all()
    view = "owner" if c.owner else ("biller" if c.biller else ("provider" if any(p.get("personId") == c.person_id for p in data["providers"]) and c.person_id else "other"))
    out = {"view": view, "environment": c.env, "clearinghouse": connector.describe(), "registry": {"version": reg.VERSION, "review": reg.REVIEW},
           "myTasks": [_task_json(t) for t in tasks if t.assignee in mine],
           "mySignatures": [_sig_json(s) for s in sigs if s.signer in mine]}
    if c.person and c.biller:
        a = access_row(db, org, c.user.id)
        out["me"] = {"checklist": biller_checklist(c.doc, c.person), "access": a.status if a else "PENDING"}
    elif c.owner:
        a = access_row(db, org, c.user.id)
        out["me"] = {"access": a.status if a else "NONE"}
    items = db.scalars(select(BillingItem).where(BillingItem.org_key == org, BillingItem.active == 1).order_by(BillingItem.id)).all()
    routes = []
    for py in data["payers"]:
        for pv in data["providers"] or [{"id": "", "name": ""}]:
            rd = readiness(db, org, c.env, data, py["key"], pv["id"], connector)
            routes.append({"payer": py["name"], "payerKey": py["key"], "provider": pv.get("name", ""), "providerId": pv["id"], "transaction": "837P",
                           "ready": rd["ready"], "blockers": rd["blockers"] if (c.owner or view == "biller") else []})
    out["routes"] = routes
    if view == "provider":
        pv_ids = {p["id"] for p in data["providers"] if p.get("personId") == c.person_id}
        out["items"] = [_item_json(db, i, data, c.env, False) for i in items if i.provider_id in pv_ids]
    if view == "biller":
        out["items"] = [dict(_item_json(db, i, data, c.env, False), reference="") for i in items]
        out["held"] = [_held_json(t) for t in db.scalars(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.delivery == "HELD",
                                                                                          ClaimTransmission.user_id == c.user.id))]
    if c.owner:
        out["items"] = [_item_json(db, i, data, c.env, True) for i in items]
        out["tasks"] = [_task_json(t) for t in tasks]
        out["signatures"] = [_sig_json(s) for s in sigs]
        out["profile"] = _profile_json(data, owner=True)
        out["billers"] = _billers_json(db, org, c.doc)
        out["held"] = [_held_json(t) for t in db.scalars(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.delivery == "HELD"))]
        out["sent"] = [_held_json(t) for t in db.scalars(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.delivery == "SENT")
                                                          .order_by(ClaimTransmission.sent_at.desc()).limit(20))]
        out["documents"] = [_doc_json(d) for d in db.scalars(select(BillingDocument).where(BillingDocument.org_key == org).order_by(BillingDocument.id.desc()))]
        out["metrics"] = _metrics(db, org, data, c.doc)
        out["staffPhysicians"] = [{"personId": p["id"], "name": p.get("name", "")} for p in c.doc["people"]
                                  if _family(p.get("role", "")) == "physician" and p.get("lifecycle") != "OFFBOARDED"]
        out["docTypes"] = BILLING_DOC_TYPES
        out["payerTypes"] = reg.PAYER_TYPES
        owner = purchasing_owner(db, org)
        out["purchasingOwner"] = owner.full_name if owner else ""
        out["activity"] = [{"at": _iso(e.occurred_at), "kind": e.kind, "from": e.from_status, "to": e.to_status, "source": e.source,
                            "environment": e.environment, "note": e.note[:200], "actor": e.actor}
                           for e in db.scalars(select(BillingEvent).where(BillingEvent.org_key == org).order_by(BillingEvent.id.desc()).limit(40))]
    return out


def _task_json(t):
    return {"id": t.id, "kind": t.kind, "title": t.title, "detail": t.detail, "assignee": t.assignee, "itemId": t.item_id, "due": t.due_date,
            "blocking": bool(t.blocking), "overdue": bool(_date(t.due_date) and _date(t.due_date) < _today()), "created": _iso(t.created_at.date())}


def _sig_json(s):
    return {"id": s.id, "itemId": s.item_id, "signer": s.signer, "statement": s.statement, "requested": _iso(s.created_at.date())}


def _held_json(t):
    snap = json.loads(t.snapshot or "{}")
    return {"claimId": t.claim_id, "patient": snap.get("patient", ""), "payer": snap.get("payer", ""), "amount": snap.get("totalAmt"),
            "delivery": t.delivery, "ack": t.ack, "payerStatus": t.payer_status, "payment": t.payment, "reference": t.reference,
            "environment": t.environment, "blockers": json.loads(t.blockers or "[]"), "since": _iso(t.created_at.date()),
            "sentAt": _iso(t.sent_at.date()) if t.sent_at else ""}


def _doc_json(d):
    return {"id": d.id, "type": d.doc_type, "typeLabel": BILLING_DOC_TYPES.get(d.doc_type, d.doc_type), "filename": d.filename, "status": d.status,
            "issues": json.loads(d.issues or "[]"), "expires": d.expires, "itemId": d.item_id, "uploaded": _iso(d.created_at.date()),
            "fields": {k: (mask_tin(v) if k == "tin" else v) for k, v in json.loads(d.fields or "{}").items()}, "processor": d.processor,
            "environment": d.environment}


def _profile_json(data, owner):
    return {"legalName": data.get("legalName", ""), "npi": data.get("npi", "") if owner else "", "tinMasked": mask_tin(data.get("tin", "")),
            "hasTin": bool(data.get("tin")), "taxonomy": data.get("taxonomy", ""), "address": data.get("address", {}), "contact": data.get("contact", {}),
            "locations": data["locations"], "payers": data["payers"], "providers": data["providers"], "sources": data["sources"],
            "confirmed": data["confirmed"], "policy": data.get("policy", {}), "setupStartedAt": data.get("setupStartedAt", "")}


def _billers_json(db, org, doc):
    out = []
    for p in doc["people"]:
        if _family(p.get("role", "")) != "biller" or p.get("lifecycle") == "OFFBOARDED":
            continue
        uid = user_for_person(db, org, p["id"])
        a = access_row(db, org, uid) if uid else None
        out.append({"personId": p["id"], "name": p.get("name", ""), "lifecycle": so.LIFECYCLE_LABELS.get(p.get("lifecycle"), ""),
                    "lock": so.lock_view(doc, p),
                    "checklist": biller_checklist(doc, p), "access": a.status if a else ("NO_LOGIN" if not uid else "PENDING"),
                    "grantedVia": a.granted_via if a else "", "grantedBy": a.granted_by if a else "", "grantedAt": _iso(a.granted_at.date()) if a and a.granted_at else ""})
    return out


# ── owner actions ──
PROFILE_FIELDS = ("legalName", "npi", "tin", "taxonomy")
MATERIAL = ("legalName", "npi", "tin")


@router.put("/api/billing/profile")
async def put_profile(request: Request, c: Ctx = Depends(owner_ctx)):
    body = await request.json()
    row, data = load_profile(c.db, c.org)
    errs = {}
    for k in PROFILE_FIELDS:
        if k in body:
            v = str(body[k] or "").strip()
            if k == "npi":
                v = re.sub(r"\D", "", v)
                if v and not npi_ok(v):
                    errs[k] = "That isn't a valid NPI (10 digits that pass the check digit)."
            if k == "tin":
                v = re.sub(r"\D", "", v)
                if v and not tin_ok(v):
                    errs[k] = "A Tax ID is 9 digits."
            if k == "taxonomy" and v and not taxonomy_ok(v):
                errs[k] = "A taxonomy code is 10 characters ending in X, like 207Q00000X."
            if k not in errs and v != data.get(k, ""):
                if k in MATERIAL and data.get(k):
                    audit(c, c.org, c.user, "billing_material_change", f"Changed {k} (was set)")
                data[k] = v
                data["sources"][k] = "Entered in Billing Activation"
                data["confirmed"].pop(k, None)
    if isinstance(body.get("address"), dict):
        data["address"] = {k: str(body["address"].get(k) or "")[:120] for k in ("line1", "city", "state", "zip")}
        data["confirmed"].pop("address", None)
    if isinstance(body.get("contact"), dict):
        data["contact"] = {k: str(body["contact"].get(k) or "")[:120] for k in ("name", "email", "phone")}
    if errs:
        return JSONResponse({"error": "Some details need fixing.", "fields": errs}, status_code=400)
    save_profile(c.db, row, data)
    request_sync(c.db, c.org, "profile changed")
    audit(c, c.org, c.user, "billing_profile_saved", "Billing profile updated")
    c.db.commit()
    return {"ok": True}


@router.post("/api/billing/profile/confirm")
async def confirm_profile(request: Request, c: Ctx = Depends(owner_ctx)):
    body = await request.json()
    row, data = load_profile(c.db, c.org)
    keys = [k for k in (body.get("fields") or []) if k in ("legal_name", "npi", "tin", "address", "taxonomy")]
    for k in keys:
        data["confirmed"][k] = {"by": c.user.full_name or c.user.email, "at": _today().isoformat()}
    for pid in body.get("providers") or []:
        for pv in data["providers"]:
            if pv["id"] == pid:
                pv["confirmed"] = {"by": c.user.full_name or c.user.email, "at": _today().isoformat()}
    save_profile(c.db, row, data)
    close_tasks(c.db, c.org, "confirm:", by=c.user.email)
    request_sync(c.db, c.org, "details confirmed")
    audit(c, c.org, c.user, "billing_details_confirmed", ", ".join(keys) or "providers")
    c.db.commit()
    return {"ok": True}


@router.put("/api/billing/payers")
async def put_payers(request: Request, c: Ctx = Depends(owner_ctx)):
    body = await request.json()
    row, data = load_profile(c.db, c.org)
    out = []
    for p in body.get("payers") or []:
        name = str(p.get("name") or "").strip()[:80]
        if not name:
            continue
        key = reg.payer_key(name)
        old = next((x for x in data["payers"] if x["key"] == key), {})
        ptype = p.get("type") if p.get("type") in reg.PAYER_TYPES else reg.payer_type(name)
        rec = dict(old, key=key, name=reg.KNOWN_PAYERS.get(name.lower(), (name,))[0], type=ptype,
                   alreadyBills=p.get("alreadyBills") if p.get("alreadyBills") in (True, False) else old.get("alreadyBills"),
                   network=p.get("network") if p.get("network") in ("in", "out") else old.get("network"))
        if rec.get("network") == "out" and old.get("network") != "out":
            rec["networkConfirmedBy"], rec["networkConfirmedAt"] = c.user.full_name or c.user.email, _today().isoformat()
        if rec.get("network") != "out":
            rec.pop("networkConfirmedBy", None)
        out.append(rec)
    if len({x["key"] for x in out}) != len(out):
        return JSONResponse({"error": "Each payer can only be listed once."}, status_code=400)
    data["payers"] = out
    save_profile(c.db, row, data)
    for p in out:
        if p.get("alreadyBills") is not None:
            close_tasks(c.db, c.org, f"ask_existing:{p['key']}", by=c.user.email)
    sync_items(c.db, c.org, c.env, data)
    request_sync(c.db, c.org, "payers changed")
    audit(c, c.org, c.user, "billing_payers_saved", f"{len(out)} payers")
    c.db.commit()
    return {"ok": True}


@router.put("/api/billing/providers")
async def put_providers(request: Request, c: Ctx = Depends(owner_ctx)):
    body = await request.json()
    row, data = load_profile(c.db, c.org)
    errs, out = [], []
    for p in body.get("providers") or []:
        old = (next((x for x in data["providers"] if p.get("id") and x["id"] == p.get("id")), None)
               or next((x for x in data["providers"] if p.get("personId") and x.get("personId") == p.get("personId")), None) or {})
        npi = re.sub(r"\D", "", str(p.get("npi") or ""))
        if npi and not npi_ok(npi):
            errs.append(f"{p.get('name') or 'A provider'}: that isn't a valid NPI.")
        tax = str(p.get("taxonomy") or "").strip().upper()
        if tax and not taxonomy_ok(tax):
            errs.append(f"{p.get('name') or 'A provider'}: a taxonomy code is 10 characters ending in X.")
        rec = dict(old, id=old.get("id") or "prv_" + secrets.token_hex(4), personId=p.get("personId") or old.get("personId", ""),
                   name=str(p.get("name") or old.get("name") or "").strip()[:120], npi=npi, taxonomy=tax,
                   licenseNumber=str(p.get("licenseNumber") or "").strip()[:40], licenseState=str(p.get("licenseState") or "").strip().upper()[:2],
                   licenseExpires=str(p.get("licenseExpires") or "")[:10])
        if old and any(old.get(k) != rec.get(k) for k in ("npi", "name", "licenseNumber")):
            rec.pop("confirmed", None)
        if rec["name"]:
            out.append(rec)
    if errs:
        return JSONResponse({"error": " ".join(errs)}, status_code=400)
    data["providers"] = out
    save_profile(c.db, row, data)
    sync_items(c.db, c.org, c.env, data)
    request_sync(c.db, c.org, "providers changed")
    audit(c, c.org, c.user, "billing_providers_saved", f"{len(out)} providers")
    c.db.commit()
    return {"ok": True}


@router.put("/api/billing/policy")
async def put_policy(request: Request, c: Ctx = Depends(owner_ctx)):
    body = await request.json()
    row, data = load_profile(c.db, c.org)
    pol = data.setdefault("policy", {})
    who = c.user.full_name or c.user.email
    for k in ("autoActivateBillers", "autoReleaseHeld"):
        if k in body:
            on = bool(body[k])
            pol[k] = {"enabled": on, "approvedBy": who if on else "", "approvedAt": _today().isoformat() if on else ""}
            audit(c, c.org, c.user, "billing_policy", f"{k} {'on' if on else 'off'}")
    save_profile(c.db, row, data)
    request_sync(c.db, c.org, "policy changed")
    c.db.commit()
    return {"ok": True, "policy": pol}


@router.post("/api/billing/billers/{person_id}/access")
async def biller_access(person_id: str, request: Request, c: Ctx = Depends(owner_ctx)):
    body = await request.json()
    action = body.get("action")
    p = next((x for x in c.doc["people"] if x["id"] == person_id), None)
    if not p or _family(p.get("role", "")) != "biller":
        raise HTTPException(status_code=404, detail="Not a biller in your clinic.")
    uid = user_for_person(c.db, c.org, person_id)
    if not uid:
        raise HTTPException(status_code=400, detail="They haven't signed in to Althais yet.")
    a = access_row(c.db, c.org, uid) or BillerAccess(org_key=c.org, user_id=uid, person_id=person_id)
    if a.id is None:
        c.db.add(a)
    who = c.user.full_name or c.user.email
    if action == "approve":
        if not biller_checklist(c.doc, p)["readyForAccess"] and not body.get("override"):
            return JSONResponse({"error": "Their billing checklist isn't finished yet."}, status_code=400)
        a.status, a.granted_via, a.granted_by, a.granted_at, a.revoked_at = "ACTIVE", "owner", who, _now(), None
        so.set_task(p, "billing_access", "COMPLETE", actor=who)
        close_tasks(c.db, c.org, f"access:{person_id}", by=c.user.email)
    elif action == "revoke":
        a.status, a.revoked_at = "REVOKED", _now()
        so.set_task(p, "billing_access", "WAITING_ON_MANAGER", actor=who)
    else:
        raise HTTPException(status_code=400, detail="Unknown action.")
    so.refresh(p)
    so.save_staff(c.db, c.org, c.row, c.doc)
    audit(c, c.org, c.user, "biller_access", f"{action} claim submission for {p.get('name')}")
    request_sync(c.db, c.org, "biller access")
    c.db.commit()
    return {"ok": True, "access": a.status}


@router.post("/api/billing/me/access")
def owner_self_access(c: Ctx = Depends(owner_ctx)):
    """An owner can allow their own login to send claims: an explicit, audited choice, never automatic."""
    a = access_row(c.db, c.org, c.user.id) or BillerAccess(org_key=c.org, user_id=c.user.id, person_id=c.person_id)
    if a.id is None:
        c.db.add(a)
    a.status, a.granted_via, a.granted_by, a.granted_at = "ACTIVE", "self", c.user.full_name or c.user.email, _now()
    audit(c, c.org, c.user, "biller_access", "Owner allowed their own login to send claims")
    c.db.commit()
    return {"ok": True}


def _own_item(c: Ctx, item_id: int) -> BillingItem:
    it = c.db.get(BillingItem, item_id)
    if not it or it.org_key != c.org:
        raise HTTPException(status_code=404, detail="Not found.")
    return it


@router.post("/api/billing/items/{item_id}/confirm")
async def confirm_item(item_id: int, request: Request, c: Ctx = Depends(owner_ctx)):
    """A person confirms an outcome (approved, verified, not required, denied). Evidence is required, and so is an effective date for approvals."""
    it = _own_item(c, item_id)
    body = await request.json()
    status = body.get("status")
    if status not in ("APPROVED", "VERIFIED", "NOT_REQUIRED", "DENIED"):
        raise HTTPException(status_code=400, detail="Choose approved, verified, not required or denied.")
    doc_id = body.get("documentId")
    d = c.db.get(BillingDocument, int(doc_id)) if doc_id else None
    if d and d.org_key != c.org:
        raise HTTPException(status_code=404, detail="Not found.")
    note = str(body.get("note") or "").strip()[:1000]
    if not d and len(note) < 8:
        return JSONResponse({"error": "Attach the evidence (a document), or describe it (for example, the payer's confirmation number and who you spoke with)."}, status_code=400)
    if d and d.environment != it.environment:
        return JSONResponse({"error": "That document was uploaded in test mode and can't be evidence here."}, status_code=400)
    eff = str(body.get("effectiveDate") or "")[:10]
    if status == "APPROVED" and it.track in DATED_TRACKS and not _date(eff):
        return JSONResponse({"error": "Add the effective date from the approval."}, status_code=400)
    exp = str(body.get("expiresAt") or "")[:10]
    it.evidence_document_id, it.evidence_note, it.evidence_source = (d.id if d else None), note, "document" if d else "manual"
    it.effective_date, it.expires_at = eff, exp if _date(exp) else it.expires_at
    it.verified_by, it.verified_at = c.user.full_name or c.user.email, _now()
    ref = str(body.get("reference") or "")[:128]
    set_status(c.db, it, status, "manual", actor=c.user.email, note=f"Confirmed by {it.verified_by}", reference=ref)
    close_tasks(c.db, c.org, f"existing:{it.id}", by=c.user.email)
    close_tasks(c.db, c.org, f"npi_review:{it.id}", by=c.user.email)
    close_tasks(c.db, c.org, f"doc:{it.id}", by=c.user.email)
    close_tasks(c.db, c.org, f"review_ext:{it.id}", by=c.user.email)
    close_tasks(c.db, c.org, f"manual:{it.id}", by=c.user.email)
    audit(c, c.org, c.user, "billing_item_confirmed", f"{it.requirement} {status}")
    request_sync(c.db, c.org, "item confirmed")
    c.db.commit()
    return {"ok": True}


@router.post("/api/billing/items/{item_id}/submitted")
async def item_submitted(item_id: int, request: Request, c: Ctx = Depends(owner_ctx)):
    it = _own_item(c, item_id)
    body = await request.json()
    ref = str(body.get("reference") or "").strip()[:128]
    if len(ref) < 3:
        return JSONResponse({"error": "Add the confirmation or reference number the payer gave you."}, status_code=400)
    on = _date(body.get("submittedOn")) or _today()
    if on > _today():
        return JSONResponse({"error": "The submission date can't be in the future."}, status_code=400)
    it.submitted_at = dt.datetime.combine(on, dt.time(12))
    set_status(c.db, it, "SUBMITTED", "manual", actor=c.user.email, reference=ref, note="Submitted outside Althais")
    close_tasks(c.db, c.org, f"manual:{it.id}", by=c.user.email)
    audit(c, c.org, c.user, "billing_item_submitted", it.requirement)
    c.db.commit()
    return {"ok": True}


@router.get("/api/billing/items/{item_id}/packet")
def item_packet(item_id: int, c: Ctx = Depends(owner_ctx)):
    it = _own_item(c, item_id)
    row, data = load_profile(c.db, c.org)
    pk = packet_for(data, it, reveal=True)
    r = pk["requirement"]
    sig = c.db.scalar(select(BillingSignature).where(BillingSignature.item_id == it.id, BillingSignature.status == "signed"))
    rows = "".join(f"<tr><td>{_html.escape(f['label'])}</td><td><b>{_html.escape(f['value'] or 'MISSING')}</b></td><td>{_html.escape(f['source'])}"
                   f"{' · confirmed' if f['confirmed'] else ' · not confirmed'}</td></tr>" for f in pk["fields"])
    audit(c, c.org, c.user, "billing_packet_viewed", r["id"])
    c.db.commit()
    page = f"""<!doctype html><html><head><meta charset="utf-8"><title>Application packet</title><style>body{{font:14px system-ui;margin:32px;color:#111}}
    table{{border-collapse:collapse;width:100%}}td{{border:1px solid #ddd;padding:6px 8px}}h1{{font-size:20px}}.n{{color:#555;font-size:12px}}</style></head><body>
    <h1>{_html.escape(r['title'])}{(' · ' + _html.escape(pk['payer'])) if pk['payer'] else ''}{(' · ' + _html.escape(pk['provider'])) if pk['provider'] else ''}</h1>
    <p class="n">Prepared by Althais from details your clinic confirmed. Registry {reg.VERSION}. Source: {_html.escape(r['source']['title'])} {_html.escape(r['source']['url'])}</p>
    <p class="n">{_html.escape(r.get('note') or '')}</p><table>{rows}</table>
    <p>Evidence the payer usually asks for: {_html.escape(r['evidence'])}</p>
    <p>{('Approved in Althais by ' + _html.escape(sig.signed_name) + ' on ' + sig.signed_at.date().isoformat() + '. Any signature the payer requires on its own form must still be done by that person on that form.') if sig else 'Not yet approved in Althais.'}</p>
    <p class="n">Contains a Tax ID. Keep this document private.</p></body></html>"""
    return HTMLResponse(page, headers={"Cache-Control": "no-store"})


@router.post("/api/billing/tasks/{task_id}/done")
async def task_done(task_id: int, request: Request, c: Ctx = Depends(ctx)):
    t = c.db.get(BillingTask, task_id)
    if not t or t.org_key != c.org:
        raise HTTPException(status_code=404, detail="Not found.")
    if t.assignee not in c.assignee_keys() and not c.owner:
        raise HTTPException(status_code=403, detail="This task isn't yours.")
    if t.kind in ("signature",):
        raise HTTPException(status_code=400, detail="Sign it from the signature request instead.")
    body = await request.json()
    t.status, t.done_by, t.done_at, t.done_note = "done", c.user.email, _now(), str(body.get("note") or "")[:1000]
    request_sync(c.db, c.org, "task done")
    c.db.commit()
    return {"ok": True}


@router.post("/api/billing/signatures/{sig_id}/sign")
async def sign(sig_id: int, request: Request, c: Ctx = Depends(ctx)):
    return _sign(c, sig_id, await request.json())


@router.post("/api/portal/billing/signatures/{sig_id}/sign")
async def portal_sign(sig_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The same signing, for providers and billers who only have the Staff Portal."""
    return _sign(Ctx(user, db), sig_id, await request.json())


@router.post("/api/portal/billing/tasks/{task_id}/done")
async def portal_task_done(task_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    c = Ctx(user, db)
    t = c.db.get(BillingTask, task_id)
    if not t or t.org_key != c.org or t.assignee not in c.assignee_keys() - {"owner"}:
        raise HTTPException(status_code=404, detail="Not found.")
    if t.kind == "signature":
        raise HTTPException(status_code=400, detail="Sign it from the signature request instead.")
    body = await request.json()
    t.status, t.done_by, t.done_at, t.done_note = "done", c.user.email, _now(), str(body.get("note") or "")[:1000]
    request_sync(c.db, c.org, "task done")
    c.db.commit()
    return {"ok": True}


def _sign(c: "Ctx", sig_id: int, body: dict):
    """Only the named signer's own login. Never on someone's behalf."""
    s = c.db.get(BillingSignature, sig_id)
    if not s or s.org_key != c.org:
        raise HTTPException(status_code=404, detail="Not found.")
    if s.status != "requested":
        raise HTTPException(status_code=400, detail="This request isn't open anymore.")
    if s.signer == "owner" and not c.owner or (s.signer.startswith("person:") and s.signer[7:] != c.person_id) or s.signer.startswith("user:") and s.signer[5:] != str(c.user.id):
        raise HTTPException(status_code=403, detail="Only the person this request is for can sign it.")
    name = str(body.get("typedName") or "").strip()
    if not body.get("agree") or len(name) < 3:
        return JSONResponse({"error": "Type your full name and tick the box to sign."}, status_code=400)
    if (c.user.full_name or "").strip() and name.lower().replace(" ", "") != (c.user.full_name or "").lower().replace(" ", ""):
        return JSONResponse({"error": "Type your own name, exactly as it appears on your account."}, status_code=400)
    s.status, s.signed_by_user, s.signed_name, s.signed_at = "signed", c.user.id, name, _now()
    it = c.db.get(BillingItem, s.item_id)
    if it and reg.BY_ID[it.requirement]["track"] == "authorization":
        it.evidence_source, it.verified_by, it.verified_at = "signature", name, _now()
        set_status(c.db, it, "VERIFIED", "signature", actor=c.user.email, note=f"Signed by {name}")
    close_tasks(c.db, c.org, f"sign:{s.item_id}:", by=c.user.email)
    audit(c, c.org, c.user, "billing_signed", f"Signed request {s.id}")
    request_sync(c.db, c.org, "signed")
    c.db.commit()
    return {"ok": True}


@router.post("/api/billing/documents")
async def upload_document(doc_type: str = Form(...), item_id: str = Form(""), provider_id: str = Form(""), file: UploadFile = File(...),
                          c: Ctx = Depends(ctx)):
    if doc_type not in BILLING_DOC_TYPES:
        raise HTTPException(status_code=400, detail="Unknown document type.")
    row, data = load_profile(c.db, c.org)
    it = _own_item(c, int(item_id)) if item_id else None
    my_provider_ids = {p["id"] for p in data["providers"] if c.person_id and p.get("personId") == c.person_id}
    if not c.owner and not ((it and it.provider_id in my_provider_ids) or provider_id in my_provider_ids):
        raise HTTPException(status_code=403, detail="Only owners, or a provider uploading their own documents, can upload here.")
    raw = await file.read()
    if not raw or len(raw) > 15 * 1024 * 1024:
        raise HTTPException(status_code=400, detail="Upload a file up to 15 MB.")
    ctype = file.content_type or "application/octet-stream"
    if ctype not in ("application/pdf", "image/jpeg", "image/png", "image/webp", "image/heic"):
        raise HTTPException(status_code=400, detail="Upload a PDF or a photo.")
    sha = hashlib.sha256(raw).hexdigest()
    dup = c.db.scalar(select(BillingDocument).where(BillingDocument.org_key == c.org, BillingDocument.sha256 == sha))
    if dup:
        return {"ok": True, "document": _doc_json(dup), "duplicate": True}
    d = BillingDocument(org_key=c.org, doc_type=doc_type, item_id=it.id if it else None, provider_id=provider_id or (it.provider_id if it else ""),
                        filename=(file.filename or "document")[:255], content_type=ctype, data=raw, sha256=sha, environment=c.env, uploaded_by=c.user.id)
    c.db.add(d)
    c.db.flush()
    fields, issues, processor = {}, [], ""
    if doc_type in NO_EXTRACTION:
        issues = ["Stored for a person to review. Althais doesn't read bank details."]
    else:
        import staff_extraction as sx
        res = await asyncio.to_thread(sx.analyze, doc_type, d.filename, ctype, raw)
        processor = res.processor
        if res.status != "analyzed" or not res.analysis:
            issues.append("Althais couldn't read this document automatically, so a person will check it.")
        else:
            a = res.analysis
            fields = {f.key: f.value for f in a.fields if f.value}
            low = [f.key for f in a.fields if f.value and f.confidence < 0.85]
            if a.document_type not in (doc_type, "other") and not (doc_type == "medical_license" and a.document_type in ("nursing_license", "professional_credential")):
                issues.append(f"It looks like a {sx.DOC_TYPES.get(a.document_type, a.document_type)}, not a {BILLING_DOC_TYPES[doc_type]}.")
            if not a.readable or not a.looks_complete:
                issues.append("It's hard to read or looks incomplete.")
            if low:
                issues.append("Some details were hard to read: " + ", ".join(low) + ".")
            if any(x.severity in ("medium", "high") for x in a.anomalies):
                issues.append("Something about it looks unusual.")
            pv = next((p for p in data["providers"] if p["id"] == d.provider_id), None)
            issues += check_document(data, doc_type, fields, pv)
    d.fields, d.issues, d.processor = json.dumps(fields), json.dumps(issues), processor
    d.expires = fields.get("expiration_date", "")[:10] if _date(fields.get("expiration_date")) else ""
    d.status = "needs_review" if issues else "checked"
    if issues:
        task(c.db, c.org, f"docreview:{d.id}", "review", f"Check a document: {BILLING_DOC_TYPES[doc_type]}", " ".join(issues)[:500], item_id=d.item_id)
    audit(c, c.org, c.user, "billing_document_uploaded", f"{doc_type} ({d.status})")
    request_sync(c.db, c.org, "document")
    c.db.commit()
    return {"ok": True, "document": _doc_json(d)}


@router.get("/api/billing/documents/{doc_id}/file")
def document_file(doc_id: int, c: Ctx = Depends(ctx)):
    d = c.db.get(BillingDocument, doc_id)
    if not d or d.org_key != c.org or not (c.owner or d.uploaded_by == c.user.id):
        raise HTTPException(status_code=404, detail="Not found.")
    return Response(d.data, media_type=d.content_type, headers={"Content-Disposition": f'inline; filename="document-{d.id}"', "Cache-Control": "no-store"})


@router.post("/api/billing/documents/{doc_id}/reviewed")
def document_reviewed(doc_id: int, c: Ctx = Depends(owner_ctx)):
    d = c.db.get(BillingDocument, doc_id)
    if not d or d.org_key != c.org:
        raise HTTPException(status_code=404, detail="Not found.")
    d.status, d.reviewed_by = "reviewed", c.user.full_name or c.user.email
    close_tasks(c.db, c.org, f"docreview:{d.id}", by=c.user.email)
    audit(c, c.org, c.user, "billing_document_reviewed", str(d.id))
    c.db.commit()
    return {"ok": True}


@router.post("/api/billing/sync")
def sync_now(c: Ctx = Depends(owner_ctx)):
    request_sync(c.db, c.org, "manual")
    c.db.commit()
    return {"ok": True}


# ── claims ──
@router.post("/api/billing/claims/transmit")
async def claims_transmit(request: Request, c: Ctx = Depends(ctx)):
    """The EMR calls this before anything is sent. The server decides: send, or hold with the exact reasons."""
    if (c.user.role or "") not in ("admin", "biller"):
        raise HTTPException(status_code=403, detail="Only billers and clinic admins can send claims.")
    ensure_area(c.user, "claims")
    body = await request.json()
    claim = body.get("claim") if isinstance(body.get("claim"), dict) else {}
    if not claim.get("claimId"):
        raise HTTPException(status_code=400, detail="The claim has no claim number.")
    row, data = load_profile(c.db, c.org)
    ns = claims_namespace(c.user)
    from auth import OrgClaim
    oc = c.db.scalar(select(OrgClaim).where(OrgClaim.org_key == ns, OrgClaim.claim_id == str(claim["claimId"])[:64]))
    if not oc:
        oc = OrgClaim(org_key=ns, claim_id=str(claim["claimId"])[:64])
        c.db.add(oc)
    oc.patient_name, oc.mrn, oc.payer = str(claim.get("patient") or "")[:255], str(claim.get("mrn") or "")[:64], str(claim.get("payer") or "")[:128]
    oc.codes = json.dumps([{"code": x.get("code"), "type": x.get("type")} for x in (claim.get("cpts") or []) + (claim.get("icds") or [])])[:8000]
    oc.amount = int(float(claim.get("totalAmt") or 0))
    res = transmit(c.db, c.org, c.env, data, c.user, claim)
    if res["status"] == "held":
        oc.status = "Held"
    elif res["status"] == "sent":
        oc.status = "Submitted (Test)" if res.get("test") else "Submitted"
        c.user.claims_submitted = (c.user.claims_submitted or 0) + 1
    audit(c, c.org, c.user, "claim_" + res["status"], f"Claim {claim['claimId']}")
    if res["status"] in ("sent", "held"):
        from auth import record_activity
        record_activity(c.db, c.user, "claim_sent" if res["status"] == "sent" else "claim_held")
    c.db.commit()
    return res


@router.get("/api/billing/claims/{claim_id}")
def claim_status(claim_id: str, c: Ctx = Depends(ctx)):
    ensure_area(c.user, "claims")
    t = c.db.scalar(select(ClaimTransmission).where(ClaimTransmission.org_key == c.org, ClaimTransmission.claim_id == claim_id)
                    .order_by(ClaimTransmission.id.desc()))
    if not t:
        return {"status": "none"}
    return _held_json(t)


# ── portal: the biller's checklist and practice claim, and providers' requests ──
PRACTICE = {
    "encounter": {"patient": "Rivera, Ana", "dob": "1979-06-02", "visit": "Office visit, established patient, moderate complexity",
                  "provider": "Dr. R. Patel", "date": "2026-10-06", "place": "Office"},
    "card": {"payer": "UnitedHealthcare", "member": "UHC5530127", "name": "RIVERA, ANA"},
    "diagnoses": [["E11.9", "Type 2 diabetes without complications"], ["I10", "Essential hypertension"]],
    "procedures": [["99214", "Office visit, established, moderate"], ["99285", "Emergency department visit, high complexity"], ["83036", "Hemoglobin A1c"]],
    "note": "Established patient seen in the office for diabetes and blood pressure follow-up. A1c drawn in clinic.",
}


def check_practice(a: dict) -> tuple:
    a = a if isinstance(a, dict) else {}
    bad = []
    norm = lambda s: re.sub(r"[^A-Z0-9]", "", str(s or "").upper())
    if norm(a.get("member")) != "UHC5530127":
        bad.append("the member ID doesn't match the card")
    if norm(a.get("patient")) != "RIVERAANA":
        bad.append("the patient name doesn't match the card (Last, First)")
    if a.get("pos") != "11":
        bad.append("the place of service should match where the visit happened")
    cpts = set(a.get("cpts") or [])
    if cpts != {"99214", "83036"}:
        if "99285" in cpts:
            bad.append("99285 is an emergency department code, and this visit was in the office")
        else:
            bad.append("the procedure codes don't match the note")
    ptr = a.get("pointers") or {}
    if set(ptr.get("99214", [])) != {"A", "B"} or set(ptr.get("83036", [])) - {"A"} or "A" not in set(ptr.get("83036", [])):
        bad.append("each procedure should point to the diagnoses that justify it (the A1c is for the diabetes)")
    return (not bad, "Correct. That claim would pass the checks." if not bad else "Not yet: " + "; ".join(bad) + ".")


@router.get("/api/portal/billing")
def portal_billing(user: User = Depends(require_user), db: Session = Depends(get_db)):
    org = org_of(user)
    pid = person_id_of(db, user, org)
    _, doc = so.load_staff(db, org)
    p = next((x for x in doc["people"] if x["id"] == pid), None)
    if not p:
        raise HTTPException(status_code=404, detail="No staff record.")
    out = {"biller": _family(p.get("role", "")) == "biller", "requests": [], "signatures": []}
    if out["biller"]:
        a = access_row(db, org, user.id)
        out.update(checklist=biller_checklist(doc, p), access=a.status if a else "PENDING", practice=PRACTICE,
                   practiceDone=(so._find(p["requirements"], "billing_practice_claim") or {}).get("status") == "COMPLETE")
    keys = {f"person:{pid}", f"user:{user.id}"}
    out["requests"] = [_task_json(t) for t in db.scalars(select(BillingTask).where(BillingTask.org_key == org, BillingTask.status == "open"))
                       if t.assignee in keys]
    out["signatures"] = [_sig_json(s) for s in db.scalars(select(BillingSignature).where(BillingSignature.org_key == org,
                                                                                         BillingSignature.status == "requested")) if s.signer in keys]
    return out


@router.post("/api/portal/billing/practice")
async def portal_practice(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org = org_of(user)
    pid = person_id_of(db, user, org)
    row, doc = so.load_staff(db, org)
    p = next((x for x in doc["people"] if x["id"] == pid), None)
    if not p or _family(p.get("role", "")) != "biller":
        raise HTTPException(status_code=403, detail="The practice claim is part of biller onboarding.")
    ok, msg = check_practice((await request.json()).get("claim"))
    unlocked = False
    if ok and so._find(p["requirements"], "billing_practice_claim"):
        so.set_task(p, "billing_practice_claim", "COMPLETE", actor=p.get("name", ""))
        unlocked = so.check_portal_lock(db, org, doc, p)
        so.refresh(p)
        so.save_staff(db, org, row, doc)
        request_sync(db, org, "practice claim")
    so.audit(db, org, user, "billing_practice_claim", p, "training", "billing_practice_claim", "Passed" if ok else "Tried")
    db.commit()
    return {"ok": ok, "message": msg, "unlocked": unlocked}


# ── inbound status from a clearinghouse ──
@router.post("/api/billing/webhooks/{partner}")
async def webhook(partner: str, request: Request, db: Session = Depends(get_db)):
    raw = await request.body()
    if not chx.verify_signature(raw, request.headers.get("X-Althais-Signature", "")):
        return JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        ev = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)
    ref = str(ev.get("account") or "")
    row = next((r for r in db.scalars(select(BillingProfile)) if json.loads(r.data or "{}").get("webhookRef") == ref), None) if ref else None
    if not row:
        return JSONResponse({"error": "unknown account"}, status_code=404)
    org = row.org_key
    env = chx.environment_for(org)
    connector = chx.connector_for(org)
    if connector.name != partner or not connector.capabilities.get("webhooks"):
        return JSONResponse({"error": "this clinic isn't connected to that partner"}, status_code=409)
    ev = connector.parse_webhook(ev)
    if ev.get("environment", connector.environment) != env:
        return JSONResponse({"error": "environment mismatch"}, status_code=409)
    eid = str(ev.get("event_id") or "")[:100]
    if not eid:
        return JSONResponse({"error": "event_id required"}, status_code=400)
    if db.scalar(select(BillingEvent.id).where(BillingEvent.external_id == f"{partner}:{eid}")):
        return {"ok": True, "duplicate": True}
    try:
        occurred = dt.datetime.fromisoformat(str(ev.get("occurred_at")).replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        occurred = _now()
    digest = hashlib.sha256(raw).hexdigest()
    if ev.get("type") == "enrollment.status":
        it = db.get(BillingItem, int(ev.get("item") or 0))
        if not it or it.org_key != org:
            return JSONResponse({"error": "unknown item"}, status_code=404)
        ok = apply_external(db, it, str(ev.get("status") or "").upper(), "webhook", str(ev.get("reference") or ""), str(ev.get("message") or "")[:500],
                            env, external_id=f"{partner}:{eid}", raw_digest=digest, occurred_at=occurred, effective_date=str(ev.get("effective_date") or "")[:10])
    elif ev.get("type", "").startswith("claim."):
        t = db.scalar(select(ClaimTransmission).where(ClaimTransmission.org_key == org, ClaimTransmission.reference == str(ev.get("reference") or ""),
                                                      ClaimTransmission.delivery == "SENT"))
        if not t:
            return JSONResponse({"error": "unknown claim"}, status_code=404)
        st = str(ev.get("status") or "").upper()
        field, at = {"claim.ack": ("ack", "ack_at"), "claim.payer": ("payer_status", "payer_at"), "claim.payment": ("payment", "paid_at")}.get(ev["type"], (None, None))
        if not field:
            return JSONResponse({"error": "unknown type"}, status_code=400)
        prev = getattr(t, at)
        ok = not (prev and occurred < prev)
        db.add(BillingEvent(org_key=org, claim_id=t.claim_id, external_id=f"{partner}:{eid}", kind=ev["type"] if ok else "stale_update", to_status=st,
                            source="webhook", environment=env, reference=t.reference, raw_digest=digest, occurred_at=occurred))
        if ok:
            setattr(t, field, st[:16])
            setattr(t, at, occurred)
    else:
        return JSONResponse({"error": "unknown type"}, status_code=400)
    db.commit()
    return {"ok": True, "applied": bool(ok)}


# ── wiring ──
def _hook():
    if biller_items_hook not in so.EXTRA_ROLE_ITEMS:
        so.EXTRA_ROLE_ITEMS.append(biller_items_hook)


_hook()
