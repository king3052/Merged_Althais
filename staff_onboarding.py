"""
staff_onboarding.py: Staff onboarding: invitations, the employee Staff Portal, and the workflow between them.

One staff record, two views of it. Every person lives in the clinic's staff document (org_settings, category
"staff", the same record Team, Credentials, Training and Compliance read through static/js/staff-store.js).
Onboarding adds to that record; it never keeps a second copy:

    person = { id, name, email, role, location, start, employment, supervisor,
               status:    "onboarding" | "active" | "inactive"      (what the older Staff pages read)
               lifecycle: DRAFT … OFFBOARDED                         (the detailed state, below)
               userId,                                               (the Althais login, once the invite is accepted)
               info: { personal, emergency, professional }, pendingInfo, infoMeta, infoVerified,
               requirements: [task, …], audit: [...] }

The tasks ("requirements") come from the clinic's onboarding template for the person's role. Roles are the
clinic's own, from Staff > Roles: when someone is activated, their role's permission matrix there becomes their
real, server-enforced Althais access (role_access below).

Kept in their own tables, because they're security-sensitive or binary:
    OrgMembership      which clinics a login belongs to, with what access (organization-scoped, never a flag
                       on the user; users.organization/role/blocked/portal_only is a cache of the active one)
    StaffInvitation    single-use, expiring, revocable invitations; only a SHA-256 hash of the token is stored
    StaffDocument/File uploaded documents (bytes in the database; never a public path)
    StaffAuditEvent    who did what to whom
    StaffNotification  reminders already sent, so nobody gets the same one twice

Everything is checked on the server: the clinic comes from the signed-in membership, never from the request.
"""

import datetime as dt
import hashlib
import html as _html
import os
import re
import secrets
from datetime import timezone, timedelta

from fastapi import APIRouter, Depends, Request, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, Response
from sqlalchemy import String, Integer, DateTime, Text, LargeBinary, UniqueConstraint, select, text, func
from sqlalchemy.orm import Mapped, mapped_column, Session

from auth import (
    Base, engine, get_db, require_user, current_user, User, OrgSettings, AREAS, _json,
    _doc_org_key, send_email, _email_html, RESEND_API_KEY, hash_password, verify_password, _set_session_cookie,
    ensure_product, ensure_area,
)
import asyncio
import staff_extraction
import staff_doc_review

router = APIRouter()

INVITE_TTL_DAYS = 7
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
CREDENTIAL_WARN_DAYS = 30   # overridden by Settings > Team (team_prefs.credential_warn_days)


def _now() -> dt.datetime:
    return dt.datetime.now(timezone.utc)


def _today() -> dt.date:
    return dt.date.today()


def _iso(d) -> str:
    return d.isoformat() if d else ""


# ──────────────────────────────────────────────────────────────────────────
#  Tables
# ──────────────────────────────────────────────────────────────────────────
class OrgMembership(Base):
    """A login's access to one clinic. kind "member" = someone added the older way (sign-up, Manager);
    kind "staff" = came in through a staff invitation and is linked to a person in the staff record."""
    __tablename__ = "org_memberships"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="member")
    staff_person_id: Mapped[str] = mapped_column(String(64), default="")
    status: Mapped[str] = mapped_column(String(16), default="active")        # active | suspended | offboarded
    app_access: Mapped[int] = mapped_column(Integer, default=1)              # 0 = Staff Portal only
    role: Mapped[str] = mapped_column(String(32), default="viewer")          # login access level (auth.ROLE_LEVELS)
    tools: Mapped[str] = mapped_column(String(255), default="")
    blocked: Mapped[str] = mapped_column(String(1024), default="")
    paused_login: Mapped[int] = mapped_column(Integer, default=0)            # 1 = we paused users.active when suspending
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, onupdate=_now)
    __table_args__ = (UniqueConstraint("user_id", "org_key", name="uq_membership_user_org"),)


class StaffInvitation(Base):
    __tablename__ = "staff_invitations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    person_id: Mapped[str] = mapped_column(String(64), index=True)
    email: Mapped[str] = mapped_column(String(255))
    role_name: Mapped[str] = mapped_column(String(128), default="")
    invited_by: Mapped[int] = mapped_column(Integer)
    invited_by_name: Mapped[str] = mapped_column(String(255), default="")
    token_hash: Mapped[str] = mapped_column(String(64), unique=True, index=True)   # sha256 of the emailed token
    status: Mapped[str] = mapped_column(String(16), default="pending")             # pending | accepted | revoked | superseded
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    accepted_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    accepted_user_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    revoked_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


class StaffDocument(Base):
    __tablename__ = "staff_documents"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    person_id: Mapped[str] = mapped_column(String(64), index=True)
    requirement_key: Mapped[str] = mapped_column(String(64), default="")
    doc_type: Mapped[str] = mapped_column(String(32), default="other")
    original_filename: Mapped[str] = mapped_column(String(255), default="")
    content_type: Mapped[str] = mapped_column(String(64), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    uploaded_by: Mapped[int] = mapped_column(Integer)
    uploaded_by_name: Mapped[str] = mapped_column(String(255), default="")
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    # UPLOADED -> PROCESSING -> NEEDS_REVIEW -> VERIFIED, or REJECTED; EXPIRED once past expiration_date
    status: Mapped[str] = mapped_column(String(20), default="UPLOADED")
    expiration_date: Mapped[str] = mapped_column(String(10), default="")
    fields: Mapped[str] = mapped_column(Text, default="{}")         # the details as confirmed/edited by people
    extraction: Mapped[str] = mapped_column(Text, default="{}")     # what the extractor suggested, and how sure it was
    employee_confirmed: Mapped[int] = mapped_column(Integer, default=0)
    verified_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    verified_by: Mapped[str] = mapped_column(String(255), default="")
    review_note: Mapped[str] = mapped_column(Text, default="")       # why it was rejected / what to correct
    superseded: Mapped[int] = mapped_column(Integer, default=0)      # a newer upload replaced it
    history: Mapped[str] = mapped_column(Text, default="[]")
    sha256: Mapped[str] = mapped_column(String(64), default="", index=True)   # spots the same file on two staff records
    decision: Mapped[str] = mapped_column(String(40), default="")    # the automated review's decision (staff_doc_review)
    review_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None)


class StaffFile(Base):
    __tablename__ = "staff_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    document_id: Mapped[int] = mapped_column(Integer, unique=True, index=True)
    data: Mapped[bytes] = mapped_column(LargeBinary)


class StaffAuditEvent(Base):
    __tablename__ = "staff_audit_events"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    actor_user_id: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    actor_name: Mapped[str] = mapped_column(String(255), default="")
    action: Mapped[str] = mapped_column(String(64), index=True)
    person_id: Mapped[str] = mapped_column(String(64), default="", index=True)
    object_type: Mapped[str] = mapped_column(String(32), default="")
    object_id: Mapped[str] = mapped_column(String(64), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


class StaffNotification(Base):
    """One row per reminder sent. The unique key is what stops the same reminder going out twice."""
    __tablename__ = "staff_notifications"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    person_id: Mapped[str] = mapped_column(String(64))
    ref: Mapped[str] = mapped_column(String(128))          # task key or credential id
    kind: Mapped[str] = mapped_column(String(32))          # due_soon | due_today | overdue | credential_expiring | credential_expired
    due: Mapped[str] = mapped_column(String(10), default="")
    recipient: Mapped[str] = mapped_column(String(255), default="")
    sent: Mapped[int] = mapped_column(Integer, default=0)  # 0 = recorded but email isn't set up
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)
    __table_args__ = (UniqueConstraint("org_key", "person_id", "ref", "kind", "due", name="uq_staff_notification"),)


class StaffFormFile(Base):
    """A clinic's own onboarding form (PDF), one row per version."""
    __tablename__ = "staff_form_files"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    form_key: Mapped[str] = mapped_column(String(64), index=True)
    version: Mapped[str] = mapped_column(String(16), default="1")
    filename: Mapped[str] = mapped_column(String(255), default="")
    size: Mapped[int] = mapped_column(Integer, default=0)
    sha256: Mapped[str] = mapped_column(String(64), default="")
    data: Mapped[bytes] = mapped_column(LargeBinary)
    uploaded_by: Mapped[str] = mapped_column(String(255), default="")
    uploaded_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now)


class StaffDocumentReview(Base):
    """One row per automated review of a document: what was detected and extracted, every check's result, the issues,
    the decision and its reason codes. Machine-readable only, no model reasoning is stored."""
    __tablename__ = "staff_document_reviews"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    person_id: Mapped[str] = mapped_column(String(64), index=True)
    document_id: Mapped[int] = mapped_column(Integer, index=True)
    requirement_key: Mapped[str] = mapped_column(String(64), default="")
    detected_type: Mapped[str] = mapped_column(String(40), default="")
    type_confidence: Mapped[str] = mapped_column(String(10), default="")
    extracted_fields: Mapped[str] = mapped_column(Text, default="{}")     # key -> value, confidence, page, location, raw
    validation_results: Mapped[str] = mapped_column(Text, default="{}")   # check -> PASS / FAIL / WARN / NOT_RUN / NOT_APPLICABLE
    external_verification: Mapped[str] = mapped_column(Text, default="{}")
    issue_flags: Mapped[str] = mapped_column(Text, default="[]")
    decision: Mapped[str] = mapped_column(String(40), index=True)
    reason_codes: Mapped[str] = mapped_column(Text, default="[]")
    employee_message: Mapped[str] = mapped_column(Text, default="")
    manager_summary: Mapped[str] = mapped_column(Text, default="[]")
    analysis_status: Mapped[str] = mapped_column(String(20), default="")  # analyzed | unavailable | failed
    processor_version: Mapped[str] = mapped_column(String(120), default="")
    created_at: Mapped[dt.datetime] = mapped_column(DateTime, default=_now, index=True)


Base.metadata.create_all(engine)
for _ddl in ("ALTER TABLE staff_documents ADD COLUMN sha256 VARCHAR(64) DEFAULT ''",
             "ALTER TABLE staff_documents ADD COLUMN decision VARCHAR(40) DEFAULT ''",
             "ALTER TABLE staff_documents ADD COLUMN review_id INTEGER"):
    try:
        with engine.connect() as _conn:
            _conn.execute(text(_ddl))
            _conn.commit()
    except Exception:
        pass   # column already there


# ──────────────────────────────────────────────────────────────────────────
#  Reference data: lifecycle, task statuses, requirement catalog, default templates
# ──────────────────────────────────────────────────────────────────────────
LIFECYCLE_LABELS = {
    "DRAFT": "Draft", "INVITED": "Invite Sent", "INVITE_ACCEPTED": "Onboarding", "ONBOARDING": "Onboarding",
    "PENDING_REVIEW": "Needs Review", "ACTIVE": "Active", "SUSPENDED": "Suspended", "OFFBOARDED": "Offboarded",
}
ONBOARDING_STATES = ("DRAFT", "INVITED", "INVITE_ACCEPTED", "ONBOARDING", "PENDING_REVIEW")
TASK_STATUSES = ("NOT_STARTED", "IN_PROGRESS", "WAITING_ON_EMPLOYEE", "WAITING_ON_MANAGER", "NEEDS_REVIEW", "COMPLETE")

# type: info (a My Information section) | document (upload; credentialType makes it a credential) |
#       form (read and acknowledge/sign) | training | manager (the clinic does it)
CATALOG = {
    "personal":          dict(type="info", section="personal", owner="employee", title="Personal Information", dueDays=-3),
    "emergency_contact": dict(type="info", section="emergency", owner="employee", title="Emergency Contact", dueDays=-3),
    "role_info":         dict(type="info", section="employment", owner="employee", title="Employment Information", dueDays=-3,
                              description="Check the role, location and start date your clinic set up for you."),
    "professional":      dict(type="info", section="professional", owner="employee", title="Professional Information", dueDays=-3, review=True,
                              description="Your NPI, license and other professional details. Your manager confirms them."),
    "license":           dict(type="document", docType="license", credentialType="medical_license", owner="employee", title="Professional License", dueDays=0),
    "bls":               dict(type="document", docType="bls", credentialType="bls", owner="employee", title="BLS / CPR Certification", dueDays=0),
    "dea":               dict(type="document", docType="dea", credentialType="dea", owner="employee", title="DEA Registration", dueDays=0, required=False),
    "immunizations":     dict(type="document", docType="immunizations", credentialType="immunizations", owner="employee", title="Immunization Records", dueDays=0),
    "gov_id":            dict(type="document", docType="gov_id", owner="employee", title="Government-Issued ID", dueDays=-3),
    "hipaa":             dict(type="training", trainingKey="hipaa", owner="employee", title="HIPAA / Privacy Training", dueDays=14),
    "security_training": dict(type="training", trainingKey="security_training", owner="employee", title="Security Awareness Training", dueDays=14),
    "althais_training":  dict(type="training", trainingKey="althais_training", owner="employee", title="Althais Training", dueDays=7),
    "confidentiality":   dict(type="form", formKey="confidentiality", owner="employee", title="Confidentiality Agreement", dueDays=-3),
    "handbook":          dict(type="form", formKey="handbook", owner="employee", title="Employee Handbook Acknowledgment", dueDays=0),
    "policies":          dict(type="form", formKey="policies", owner="employee", title="Clinic Policies", dueDays=0),
    "security_policy":   dict(type="form", formKey="security_policy", owner="employee", title="Security Policy", dueDays=0),
    "background":        dict(type="manager", owner="manager", title="Background Check", dueDays=0,
                              description="Record the result once your screening provider returns it."),
    "location":          dict(type="manager", owner="manager", title="Assigned Location", dueDays=0),
    "ehr_access":        dict(type="manager", owner="manager", title="EHR Access", dueDays=0,
                              description="Set up their accounts in any outside systems they need."),
    "access":            dict(type="manager", owner="manager", title="Althais Permissions", dueDays=0,
                              description="Set from their role on Staff > Roles when you activate them."),
    "manager_review":    dict(type="manager", owner="manager", title="Manager Review", dueDays=0,
                              description="Your final review. Completed when you approve and activate them."),
}
AUTO_AT_ACTIVATION = ("access", "manager_review")   # completed by Approve & Activate itself

_CLINICAL = ["personal", "emergency_contact", "role_info", "professional", "license", "bls", "dea", "hipaa", "security_training",
             "confidentiality", "policies", "althais_training", "background", "immunizations", "gov_id", "location", "ehr_access",
             "access", "manager_review"]
ROLE_TEMPLATE_KEYS = {
    "Physician": _CLINICAL,
    "NP / PA": _CLINICAL,
    "Nurse": [k for k in _CLINICAL if k != "dea"],
    "Medical Assistant": ["personal", "emergency_contact", "role_info", "bls", "hipaa", "security_training", "policies", "althais_training",
                          "background", "immunizations", "gov_id", "location", "ehr_access", "access", "manager_review"],
    "Front Desk": ["personal", "emergency_contact", "role_info", "hipaa", "security_training", "confidentiality", "policies",
                   "althais_training", "background", "gov_id", "location", "access", "manager_review"],
    "Biller": ["personal", "emergency_contact", "role_info", "hipaa", "security_training", "confidentiality", "policies",
               "althais_training", "background", "gov_id", "access", "manager_review"],
    "Practice Manager": ["personal", "emergency_contact", "role_info", "hipaa", "security_training", "confidentiality", "handbook",
                         "policies", "althais_training", "background", "gov_id", "access", "manager_review"],
    "Administrator": ["personal", "emergency_contact", "role_info", "hipaa", "security_training", "confidentiality", "handbook",
                      "policies", "althais_training", "background", "gov_id", "access", "manager_review"],
    "Volunteer": ["personal", "emergency_contact", "role_info", "hipaa", "confidentiality", "policies", "background",
                  "immunizations", "gov_id", "manager_review"],
    "Student": ["personal", "emergency_contact", "role_info", "hipaa", "confidentiality", "policies", "background", "gov_id", "manager_review"],
}
GENERIC_TEMPLATE_KEYS = ["personal", "emergency_contact", "role_info", "hipaa", "security_training", "confidentiality", "policies",
                         "althais_training", "access", "manager_review"]

# Keep in step with DEFAULT_ROLES in static/js/staff-store.js (used when a clinic has never saved its roles).
DEFAULT_ROLES = [
    {"name": "Physician",         "perms": {"patients": 1, "notes": 1, "coding": 1, "claims": 1, "revenue": 0, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "NP / PA",           "perms": {"patients": 1, "notes": 1, "coding": 1, "claims": 1, "revenue": 0, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "Nurse",             "perms": {"patients": 1, "notes": 1, "coding": 0, "claims": 0, "revenue": 0, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "Medical Assistant", "perms": {"patients": 1, "notes": 1, "coding": 0, "claims": 0, "revenue": 0, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "Front Desk",        "perms": {"patients": 1, "notes": 0, "coding": 0, "claims": 0, "revenue": 0, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "Biller",            "perms": {"patients": 1, "notes": 0, "coding": 1, "claims": 1, "revenue": 1, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "Practice Manager",  "perms": {"patients": 1, "notes": 0, "coding": 0, "claims": 1, "revenue": 1, "staff": 1, "compliance": 1, "settings": 1}},
    {"name": "Volunteer",         "perms": {"patients": 0, "notes": 0, "coding": 0, "claims": 0, "revenue": 0, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "Student",           "perms": {"patients": 1, "notes": 1, "coding": 0, "claims": 0, "revenue": 0, "staff": 0, "compliance": 0, "settings": 0}},
    {"name": "Administrator",     "perms": {"patients": 1, "notes": 1, "coding": 1, "claims": 1, "revenue": 1, "staff": 1, "compliance": 1, "settings": 1}},
]

_SAMPLE = "Sample text from Althais. {clinic} should replace it with its own under Staff > Onboarding Templates before asking anyone to sign."
DEFAULT_FORMS = [
    {"key": "confidentiality", "title": "Confidentiality Agreement", "action": "sign", "version": "1", "isSample": True,
     "body": "I, {name}, understand that in my work as {role} at {clinic} I may see patient health information and the clinic's "
             "business information. I will use it only as my job requires, share it only with people who need it for their work, "
             "and keep it private during and after my time with {clinic}.\n\n" + _SAMPLE},
    {"key": "handbook", "title": "Employee Handbook Acknowledgment", "action": "acknowledge", "version": "1", "isSample": True,
     "body": "I have received {clinic}'s employee handbook, had the chance to ask questions about it, and understand it is my "
             "responsibility to follow it.\n\n" + _SAMPLE},
    {"key": "policies", "title": "Clinic Policies", "action": "acknowledge", "version": "1", "isSample": True,
     "body": "I have read {clinic}'s clinic policies and agree to follow them in my work as {role}.\n\n" + _SAMPLE},
    {"key": "security_policy", "title": "Security Policy", "action": "acknowledge", "version": "1", "isSample": True,
     "body": "I will keep my Althais password to myself, lock my screen when I step away, and report anything that looks like a "
             "security problem to my manager right away.\n\n" + _SAMPLE},
]
DEFAULT_TRAININGS = [
    {"key": "hipaa", "title": "HIPAA / Privacy Training", "category": "HIPAA", "version": "1", "validMonths": 12, "url": "",
     "description": "How patient health information is protected at {clinic}, and your part in it."},
    {"key": "security_training", "title": "Security Awareness Training", "category": "Clinic Policy", "version": "1", "validMonths": 12, "url": "",
     "description": "Passwords, phishing, and keeping devices and records safe."},
    {"key": "althais_training", "title": "Althais Training", "category": "Role-Specific", "version": "2.1", "validMonths": 0, "url": "",
     "builtIn": True, "passScore": 80,
     "description": "Learn Althais on the real software: shared basics, then your role's path, hands-on practice in a copy of Althais with made-up patients, and real-life situations that show you've got it. Althea can explain any step (about 50 minutes)."},
]
ALTHAIS_COURSE_VERSION = "2.1"
DEFAULT_FORM_PREFILL = ["legal_first", "legal_last", "role", "start", "location", "address1", "city", "state", "zip", "phone", "email"]   # althais_training.COURSE_VERSION; bump when the course changes

# Credential types (keep the keys in step with CREDENTIAL_TYPES in staff-store.js)
CREDENTIAL_LABELS = {"medical_license": "Medical License", "nursing_license": "Nursing License", "bls": "BLS / CPR", "dea": "DEA Registration",
                     "background": "Background Check", "immunizations": "Immunizations", "other": "Other"}

# Staff > Roles permission areas -> the Althais pages and features (auth.AREAS) they open
PERMISSION_AREAS = {
    "patients":   ["patients", "schedule"],
    "notes":      ["scribe"],
    "coding":     ["code_a_note", "coding_review"],
    "claims":     ["claims", "denials", "appeals"],
    "revenue":    ["payments", "payer_intelligence"],
    "staff":      ["team", "onboarding", "clinic_onboarding", "credentials", "training", "roles"],
    "compliance": ["compliance"],
    "settings":   ["settings"],
}


def _item_from_catalog(key: str, role: str = "") -> dict:
    base = dict(CATALOG.get(key) or {"type": "manager", "owner": "manager", "title": key})
    item = {"key": key, "required": base.pop("required", True), "reminderDaysBefore": 2, "dependsOn": [], "description": ""}
    item.update(base)
    if key == "license":
        item["credentialType"] = "nursing_license" if "nurse" in (role or "").lower() else "medical_license"
    return item


def default_template(role: str) -> dict:
    keys = ROLE_TEMPLATE_KEYS.get(role) or GENERIC_TEMPLATE_KEYS
    return {"id": "tpl_" + re.sub(r"[^a-z0-9]+", "_", role.lower()).strip("_"), "role": role, "name": f"{role} Onboarding",
            "items": [_item_from_catalog(k, role) for k in keys]}


# ──────────────────────────────────────────────────────────────────────────
#  The staff document (same storage and revision numbers as auth's /api/staff)
# ──────────────────────────────────────────────────────────────────────────
def load_staff(db: Session, org_key: str):
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == org_key, OrgSettings.category == "staff"))
    doc = {}
    if row:
        try:
            doc = _json.loads(row.data) or {}
        except Exception:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    return row, normalize_doc(doc)


def save_staff(db: Session, org_key: str, row, doc: dict) -> None:
    """Server-side edits bump the revision, so a manager's open page re-applies its own edits on top (StaffStore.update)."""
    doc["rev"] = int(doc.get("rev") or 0) + 1
    if row:
        row.data = _json.dumps(doc)
    else:
        db.add(OrgSettings(org_key=org_key, category="staff", data=_json.dumps(doc)))
    db.commit()


def normalize_doc(d: dict) -> dict:
    d.setdefault("rev", 0)
    for k in ("people", "credentials", "trainings"):
        if not isinstance(d.get(k), list):
            d[k] = []
    if not isinstance(d.get("roles"), list) or not d["roles"]:
        d["roles"] = _json.loads(_json.dumps(DEFAULT_ROLES))
    if not isinstance(d.get("clinic"), dict):
        d["clinic"] = {}
    for k in ("onboardingTemplates", "onboardingForms", "onboardingTrainings"):
        if not isinstance(d.get(k), list):
            d[k] = []
    for p in d["people"]:
        normalize_person(p)
    return d


def normalize_person(p: dict) -> dict:
    """Records made by the older checklist ({key, done}) gain the task fields; nothing already there is dropped."""
    p.setdefault("audit", [])
    p.setdefault("info", {})
    for sec in ("personal", "emergency", "professional"):
        if not isinstance(p["info"].get(sec), dict):
            p["info"][sec] = {}
    for k in ("pendingInfo", "infoMeta", "infoVerified"):
        if not isinstance(p.get(k), dict):
            p[k] = {}
    if not isinstance(p.get("requirements"), list):
        p["requirements"] = []
    for t in p["requirements"]:
        if "type" not in t:
            base = _item_from_catalog(t.get("key", ""), p.get("role", ""))
            for k, v in base.items():
                t.setdefault(k, v)
        t.setdefault("required", True)
        t.setdefault("dependsOn", [])
        if "status" not in t:
            t["status"] = "COMPLETE" if t.get("done") else ("WAITING_ON_MANAGER" if t.get("owner") == "manager" else "NOT_STARTED")
        t["done"] = t["status"] == "COMPLETE"
    if p.get("lifecycle") not in LIFECYCLE_LABELS:
        p["lifecycle"] = {"active": "ACTIVE", "inactive": "OFFBOARDED"}.get(p.get("status"), "DRAFT")
    # the Team page can still mark people inactive / active itself: follow it
    if p.get("status") == "inactive" and p["lifecycle"] not in ("SUSPENDED", "OFFBOARDED"):
        p["lifecycle"] = "SUSPENDED"
    if p.get("status") == "active" and p["lifecycle"] != "ACTIVE":
        p["lifecycle"] = "ACTIVE"
    return p


# ──────────────────────────────────────────────────────────────────────────
#  Staff profile: every known value once, with where it came from
#
#  person["profile"][key] = {value, source, verified, ref, at, by}
#  Onboarding, forms and the portal read values from here, so nobody types the same thing twice. A value confirmed
#  by the clinic or read from a verified document is "verified"; an unverified value never replaces a verified one
#  (it waits in "proposed" until the clinic confirms it).
# ──────────────────────────────────────────────────────────────────────────
PROFILE_LABELS = {
    "legal_first": "Legal First Name", "middle": "Middle Name", "legal_last": "Legal Last Name", "preferred": "Preferred Name",
    "dob": "Date Of Birth", "email": "Email", "phone": "Phone", "address1": "Home Address", "address2": "Apartment, Suite, Etc.",
    "city": "City", "state": "State", "zip": "ZIP Code", "role": "Role", "location": "Location", "start": "Start Date",
    "employment": "Employment Type", "supervisor": "Supervisor", "npi": "NPI", "credentials": "Professional Credentials",
    "specialty": "Specialty", "license_number": "License Number", "license_state": "License State", "license_expiration": "License Expiration",
    "license_type": "License Type", "dea_number": "DEA Number", "dea_expiration": "DEA Expiration", "bls_expiration": "BLS Expiration",
    "bls_issuer": "BLS Issuer", "acls_expiration": "ACLS Expiration", "id_expiration": "ID Expiration",
}
# verified document field -> profile key, per requirement
DOC_PROFILE_FIELDS = {
    "license": {"license_number": "license_number", "state": "license_state", "expiration_date": "license_expiration", "credential_type": "license_type"},
    "bls": {"expiration_date": "bls_expiration", "issuer": "bls_issuer"},
    "dea": {"dea_number": "dea_number", "expiration_date": "dea_expiration", "state": "dea_state"},
    "gov_id": {"expiration_date": "id_expiration"},
}


def set_profile(p: dict, key: str, value, source: str, verified: bool, ref="", by: str = "") -> None:
    value = "" if value is None else str(value).strip()
    if not value:
        return
    prof = p.setdefault("profile", {})
    cur = prof.get(key)
    now = _now().isoformat()
    if cur and cur.get("verified") and not verified:
        if cur.get("value") != value:
            cur["proposed"] = {"value": value, "source": source, "at": now, "by": by}
        return
    prof[key] = {"value": value, "source": source, "verified": bool(verified), "ref": str(ref or ""), "at": now, "by": by}


def profile_value(p: dict, key: str) -> str:
    return ((p.get("profile") or {}).get(key) or {}).get("value", "")


def templates_for(doc: dict) -> list:
    """The clinic's templates; every role on Staff > Roles without one gets the default for its name."""
    have = {t.get("role") for t in doc["onboardingTemplates"]}
    return doc["onboardingTemplates"] + [default_template(r["name"]) for r in doc["roles"] if r.get("name") not in have]


def forms_for(doc: dict) -> list:
    have = {f.get("key") for f in doc["onboardingForms"]}
    return doc["onboardingForms"] + [f for f in DEFAULT_FORMS if f["key"] not in have]


def role_form_items(doc: dict, role: str, have: set) -> list:
    """Clinic forms assigned to this role (Onboarding Templates > Forms), as requirement items."""
    out = []
    for f in forms_for(doc):
        key = f.get("key")
        if not key or role not in (f.get("roles") or []) or key in have or any(x == key for x in have):
            continue
        out.append({"key": key, "type": "form", "formKey": key, "owner": "employee", "title": f.get("title") or "Form",
                    "required": f.get("required", True) is not False, "dueDays": f.get("dueDays", 0), "reminderDaysBefore": 2,
                    "dependsOn": [], "description": f.get("description", "")})
    return out


def trainings_for(doc: dict) -> list:
    have = {t.get("key") for t in doc["onboardingTrainings"]}
    out = doc["onboardingTrainings"] + [t for t in DEFAULT_TRAININGS if t["key"] not in have]
    # the built-in course's version is Althais's, whatever a clinic saved
    return [dict(t, version=ALTHAIS_COURSE_VERSION, builtIn=True, url="") if t.get("key") == "althais_training" else t for t in out]


def training_status(r: dict) -> str:
    """NOT_STARTED / IN_PROGRESS / COMPLETED / EXPIRED for a training record."""
    if r.get("completed"):
        try:
            if r.get("expires") and dt.date.fromisoformat(r["expires"]) < _today():
                return "EXPIRED"
        except ValueError:
            pass
        return "COMPLETED"
    return "IN_PROGRESS" if r.get("startedAt") or r.get("modulesDone") else "NOT_STARTED"


def _find(items, key, field="key"):
    for it in items:
        if it.get(field) == key:
            return it
    return None


def person_of(doc: dict, pid: str):
    return _find(doc["people"], pid, "id")


def _uid(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(5)}"


def _clinic_name(org_key: str) -> str:
    return org_key if not org_key.startswith("user:") else "your clinic"


def _fill(text: str, p: dict, org_key: str) -> str:
    """Pre-fill a form or training from what the staff record already knows."""
    info = p.get("info", {}).get("personal", {})
    legal = " ".join(x for x in (info.get("legal_first"), info.get("legal_last")) if x) or p.get("name", "")
    vals = {"name": legal, "role": p.get("role", ""), "clinic": _clinic_name(org_key), "location": p.get("location", "") or "",
            "start_date": _fmt_date(p.get("start")), "today": _fmt_date(_today().isoformat())}
    return re.sub(r"\{(name|role|clinic|location|start_date|today)\}", lambda m: vals[m.group(1)], text or "")


def _fmt_date(iso) -> str:
    try:
        return dt.date.fromisoformat(str(iso)[:10]).strftime("%b %-d, %Y")
    except Exception:
        return ""


# ──────────────────────────────────────────────────────────────────────────
#  Tasks, progress and lifecycle
# ──────────────────────────────────────────────────────────────────────────
NO_START_DAYS = 14   # no start date yet: deadlines count from two weeks after they were added


def _anchor(p: dict) -> dt.date:
    """Deadlines count from the start date. Without one, from two weeks after they were added, so items due
    "before start" aren't overdue the day the invitation goes out."""
    try:
        return dt.date.fromisoformat(p.get("start") or "")
    except ValueError:
        pass
    try:
        return dt.date.fromisoformat((p.get("createdAt") or "")[:10]) + timedelta(days=NO_START_DAYS)
    except ValueError:
        return _today() + timedelta(days=NO_START_DAYS)


def build_tasks(p: dict, items: list) -> list:
    """Turn template items into this person's tasks, keeping any progress on tasks they already have."""
    keep = {t["key"]: t for t in p.get("requirements", [])}
    out = []
    for it in items:
        if it["key"] in keep:
            t = keep[it["key"]]
            for k in ("title", "description", "required", "dueDays", "reminderDaysBefore", "dependsOn", "owner", "type",
                      "docType", "credentialType", "formKey", "trainingKey", "section", "review"):
                if k in it:
                    t[k] = it[k]
        else:
            t = dict(it)
            t["status"] = "WAITING_ON_MANAGER" if t.get("owner") == "manager" else "NOT_STARTED"
            t["assignedAt"] = _now().isoformat()
        if isinstance(t.get("dueDays"), (int, float)):
            t["dueDate"] = (_anchor(p) + timedelta(days=int(t["dueDays"]))).isoformat()
        t["done"] = t.get("status") == "COMPLETE"
        out.append(t)
    return out


def task_available(p: dict, t: dict) -> bool:
    deps = [_find(p["requirements"], k) for k in (t.get("dependsOn") or [])]
    return all(d is None or d.get("status") == "COMPLETE" for d in deps)


def effective_status(t: dict) -> str:
    if t.get("status") != "COMPLETE" and t.get("dueDate"):
        try:
            if dt.date.fromisoformat(t["dueDate"]) < _today():
                return "OVERDUE"
        except Exception:
            pass
    return t.get("status", "NOT_STARTED")


def progress(p: dict) -> int:
    req = [t for t in p["requirements"] if t.get("required", True)]
    if not req:
        return 100 if p.get("lifecycle") == "ACTIVE" else 0
    return round(100 * sum(1 for t in req if t.get("status") == "COMPLETE") / len(req))


def set_task(p: dict, key: str, status: str, actor: str = "", note: str = None):
    t = _find(p["requirements"], key)
    if not t:
        return None
    t["status"] = status
    t["done"] = status == "COMPLETE"
    if status == "COMPLETE":
        t["completedAt"] = t.get("completedAt") or _now().isoformat()
        if actor:
            t["approvedBy"], t["approvedAt"] = actor, _now().isoformat()
            t["verifiedBy"], t["verifiedAt"] = actor, _now().isoformat()   # the older checklist's names
    else:
        t.pop("completedAt", None)
    if note is not None:
        t["reviewNote"] = note
    return t


def refresh(p: dict) -> dict:
    """Recompute what follows from the tasks: the location task, and where the person is in onboarding."""
    loc = _find(p["requirements"], "location")
    if loc and p.get("location") and loc.get("status") != "COMPLETE":
        set_task(p, "location", "COMPLETE", "Althais")
    lc = p.get("lifecycle")
    if lc in ("INVITE_ACCEPTED", "ONBOARDING", "PENDING_REVIEW"):
        mine = [t for t in p["requirements"] if t.get("required", True) and t.get("owner") != "manager"]
        # nothing left for the employee to do: the rest is waiting on the manager
        p["lifecycle"] = "PENDING_REVIEW" if all(t.get("status") in ("COMPLETE", "NEEDS_REVIEW") for t in mine) else "ONBOARDING"
    p["status"] = {"ACTIVE": "active", "SUSPENDED": "inactive", "OFFBOARDED": "inactive"}.get(p["lifecycle"], "onboarding")
    return p


def next_action(p: dict, invite: dict) -> str:
    lc = p.get("lifecycle")
    if lc == "DRAFT":
        return "Send invite"
    if lc == "INVITED":
        return "Invite expired. Resend it" if invite.get("state") == "expired" else "Waiting for invite to be accepted"
    if lc in ("SUSPENDED", "OFFBOARDED"):
        return LIFECYCLE_LABELS[lc]
    review = [t for t in p["requirements"] if t.get("status") == "NEEDS_REVIEW"]
    if review:
        return f"{review[0]['title']} awaiting review"
    open_emp = [t for t in p["requirements"] if t.get("required", True) and t.get("owner") != "manager" and t.get("status") != "COMPLETE"]
    if lc == "ACTIVE":
        return "Complete" if not open_emp else f"{open_emp[0]['title']} needed"
    if open_emp:
        t = sorted(open_emp, key=lambda x: x.get("dueDate") or "9999")[0]
        verb = {"document": "required", "form": "to sign" if t.get("type") == "form" else "", "training": "to complete"}.get(t.get("type"), "needed")
        return f"{t['title']} {verb or 'needed'}"
    return "Review onboarding"


# ──────────────────────────────────────────────────────────────────────────
#  Access: memberships and role permissions
# ──────────────────────────────────────────────────────────────────────────
def role_access(doc: dict, role_name: str) -> dict:
    """What a Staff > Roles role gives in Althais: whether they use the app at all, their login access level,
    and the pages/features (auth.AREAS) that stay switched off."""
    role = _find(doc["roles"], role_name, "name") or {"perms": {}}
    perms = {k: bool(v) for k, v in (role.get("perms") or {}).items()}
    allowed = set()
    for key, areas in PERMISSION_AREAS.items():
        if perms.get(key):
            allowed.update(areas)
    app = bool(allowed)
    if app and (perms.get("patients") or perms.get("claims") or perms.get("revenue") or perms.get("notes")):
        allowed.add("overview")
    if app:
        allowed.add("althea")
    if perms.get("staff") and perms.get("settings"):
        level = "admin"
    elif perms.get("claims") or perms.get("coding") or perms.get("revenue"):
        level = "biller"
    elif perms.get("patients") or perms.get("notes"):
        level = "provider"
    else:
        level = "viewer"
    return {"app_access": app, "role": level, "blocked": ",".join(sorted(set(AREAS) - allowed)),
            "areas": sorted(allowed), "perms": {k: int(v) for k, v in perms.items()}}


def membership(db: Session, user_id: int, org_key: str):
    return db.scalar(select(OrgMembership).where(OrgMembership.user_id == user_id, OrgMembership.org_key == org_key))


def can_view_staff(user: User, db: Session) -> bool:
    """Everyone's staff records (Staff pages, /api/staff): clinic admins, and staff whose Staff > Roles role includes
    Staff Records. Everyone else sees only their own record, in their staff profile (/portal)."""
    if getattr(user, "portal_only", 0):
        return False
    if (user.role or "admin") == "admin":
        return True
    m = membership(db, user.id, (user.organization or "").strip())
    return bool(m and m.kind == "staff" and m.status == "active" and "team" not in {a for a in (m.blocked or "").split(",") if a})


def has_staff_profile(user: User, db: Session) -> bool:
    m = membership(db, user.id, (user.organization or "").strip())
    return bool(m and m.staff_person_id and m.status == "active")


def ensure_membership(db: Session, user: User):
    """Logins from before memberships existed get one for the clinic they're in, with the access they have now."""
    key = (user.organization or "").strip()
    if not key:
        return None
    m = membership(db, user.id, key)
    if not m:
        m = OrgMembership(user_id=user.id, org_key=key, kind="member", status="active", app_access=0 if user.portal_only else 1,
                          role=user.role or "viewer", tools=user.tools or "", blocked=user.blocked or "")
        db.add(m)
        db.commit()
    return m


def _snapshot(db: Session, user: User) -> None:
    """Before leaving a clinic, keep what Manager may have changed on the user row in that clinic's membership."""
    m = ensure_membership(db, user)
    if m:
        m.role, m.tools, m.blocked = user.role or "viewer", user.tools or "", user.blocked or ""


def apply_membership(user: User, m: OrgMembership) -> None:
    user.organization = m.org_key
    user.role = m.role or "viewer"
    user.tools = m.tools or ""
    user.blocked = m.blocked or ""
    user.portal_only = 0 if m.app_access else 1


def switch_to(db: Session, user: User, m: OrgMembership) -> None:
    if (user.organization or "").strip() != m.org_key:
        _snapshot(db, user)
    apply_membership(user, m)
    db.commit()


def sync_if_active(db: Session, user_id: int, m: OrgMembership) -> None:
    u = db.get(User, user_id)
    if u and (u.organization or "").strip() == m.org_key:
        apply_membership(u, m)


def _other_active(db: Session, user_id: int, org_key: str):
    return db.scalar(select(OrgMembership).where(OrgMembership.user_id == user_id, OrgMembership.org_key != org_key,
                                                 OrgMembership.status == "active"))


# ──────────────────────────────────────────────────────────────────────────
#  Audit
# ──────────────────────────────────────────────────────────────────────────
def audit(db: Session, org_key: str, actor, action: str, p: dict = None, obj_type: str = "", obj_id="", detail: str = "") -> None:
    """actor: a User, or None for Althais itself. Never pass secrets (tokens, passwords, file contents) in detail."""
    name = (actor.full_name or actor.email) if actor else "Althais"
    db.add(StaffAuditEvent(org_key=org_key, actor_user_id=actor.id if actor else None, actor_name=name, action=action,
                           person_id=(p or {}).get("id", ""), object_type=obj_type, object_id=str(obj_id or ""), detail=detail[:2000]))
    if p is not None and detail:
        p.setdefault("audit", []).append({"text": detail, "at": _now().isoformat(), "by": name})


# ──────────────────────────────────────────────────────────────────────────
#  Invitations
# ──────────────────────────────────────────────────────────────────────────
def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _aware(d):
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def invite_state(inv) -> str:
    if not inv:
        return "none"
    if inv.status != "pending":
        return inv.status
    return "expired" if _aware(inv.expires_at) < _now() else "sent"


def latest_invite(db: Session, org_key: str, pid: str):
    return db.scalar(select(StaffInvitation).where(StaffInvitation.org_key == org_key, StaffInvitation.person_id == pid)
                     .order_by(StaffInvitation.id.desc()).limit(1))


def _invite_json(inv) -> dict:
    return {"state": invite_state(inv), "sent_at": _iso(inv.created_at) if inv else "", "expires_at": _iso(inv.expires_at) if inv else "",
            "accepted_at": _iso(inv.accepted_at) if inv else "", "email": inv.email if inv else ""}


def _revoke_pending(db: Session, org_key: str, pid: str, status: str = "revoked") -> int:
    n = 0
    for inv in db.scalars(select(StaffInvitation).where(StaffInvitation.org_key == org_key, StaffInvitation.person_id == pid,
                                                         StaffInvitation.status == "pending")):
        inv.status, inv.revoked_at = status, _now()
        n += 1
    return n


def _link_base(request: Request) -> str:
    return (os.environ.get("APP_URL") or str(request.base_url)).rstrip("/")


def send_invite(db: Session, request: Request, org_key: str, p: dict, by: User) -> dict:
    """Replace any open invitation with a new one (only one token is ever live) and email it."""
    _revoke_pending(db, org_key, p["id"], status="superseded")
    token = secrets.token_urlsafe(32)
    inv = StaffInvitation(org_key=org_key, person_id=p["id"], email=p["email"], role_name=p.get("role", ""), invited_by=by.id,
                          invited_by_name=by.full_name or by.email, token_hash=_hash(token), status="pending",
                          expires_at=_now() + timedelta(days=INVITE_TTL_DAYS))
    db.add(inv)
    link = f"{_link_base(request)}/invite/{token}"
    clinic = _html.escape(_clinic_name(org_key))
    details = (f"<strong>Clinic:</strong> {clinic}<br><strong>Role:</strong> {_html.escape(p.get('role', ''))}"
               + (f"<br><strong>Start date:</strong> {_html.escape(_fmt_date(p.get('start')))}" if p.get("start") else ""))
    # People new to Althais get a login now, with a temporary password in this email. It only works while an
    # invitation is open (signing in with it accepts the invitation, auth.login), and they must choose their own
    # right after (/set-password). People who already have an Althais login sign in with their own password.
    user = db.scalar(select(User).where(User.email == p["email"]))
    temp = None
    if user is None:
        temp = _temp_password()
        user = User(email=p["email"], password_hash=hash_password(temp), full_name=p.get("name", ""), organization="",
                    role="viewer", email_verified=0, onboarding_complete=1, portal_only=1, must_change_password=1, active=1)
        db.add(user)
        db.flush()
    elif user.must_change_password:
        temp = _temp_password()          # a resend replaces the earlier temporary password
        user.password_hash, user.active = hash_password(temp), 1
    who = _html.escape(p["email"])
    how = (f"<strong>Sign in with:</strong><br>Email: {who}<br>Temporary password: "
           f"<strong style='font-family:monospace;font-size:15px'>{_html.escape(temp)}</strong><br><br>"
           "After you sign in, you'll create your own password."
           if temp else
           "You already have an Althais account with this email, so sign in with your existing password to accept. "
           "If you don't remember it, use <strong>Forgot password</strong> on that page.")
    emailed = send_email(p["email"], f"You're invited to join {_clinic_name(org_key)} on Althais", _email_html(
        f"You're invited to join {clinic} on Althais",
        f"{_html.escape(by.full_name or by.email)} invited you to complete your onboarding with {clinic}.<br><br>{details}<br><br>{how}",
        link, "Accept Invitation",
        note=f"This invitation is for {who} and expires in {INVITE_TTL_DAYS} days."
             + (" The temporary password stops working once you set your own, or if your clinic sends a new invitation." if temp else "")))
    return {"invitation": inv, "emailed": emailed, "link": None if emailed else link, "tempPassword": temp if not emailed else None}


def _temp_password() -> str:
    """Easy to type from an email: no look-alike characters (0/O, 1/l/I)."""
    alphabet = "abcdefghjkmnpqrstuvwxyzABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    return "".join(secrets.choice(alphabet) for _ in range(12))


def _retire_temp_login(db: Session, email: str) -> None:
    """No open invitation left for a login that never set its own password: its temporary password stops working."""
    db.flush()
    u = db.scalar(select(User).where(User.email == email))
    if u and u.must_change_password and not db.scalar(
            select(StaffInvitation.id).where(StaffInvitation.email == email, StaffInvitation.status == "pending")):
        u.active = 0


def _valid_invite(db: Session, token: str):
    inv = db.scalar(select(StaffInvitation).where(StaffInvitation.token_hash == _hash(token or "")))
    if not inv:
        return None, "This invitation link isn't valid. Ask your clinic to send a new one."
    state = invite_state(inv)
    if state == "expired":
        return None, "This invitation has expired. Ask your clinic to send a new one."
    if state == "accepted":
        return None, "This invitation has already been used. Sign in to continue."
    if state != "sent":
        return None, "This invitation was cancelled. Ask your clinic to send a new one."
    return inv, ""


# ──────────────────────────────────────────────────────────────────────────
#  My Information: sections, fields and validation
# ──────────────────────────────────────────────────────────────────────────
SECTION_FIELDS = {
    "personal": [("legal_first", "Legal First Name", True), ("middle", "Middle Name", False), ("legal_last", "Legal Last Name", True),
                 ("preferred", "Preferred Name", False), ("dob", "Date Of Birth", True), ("phone", "Phone", True),
                 ("address1", "Home Address", True), ("address2", "Apartment, Suite, Etc.", False), ("city", "City", True),
                 ("state", "State", True), ("zip", "ZIP Code", True)],
    "emergency": [("ec_name", "Emergency Contact Name", True), ("ec_relationship", "Relationship", True), ("ec_phone", "Emergency Contact Phone", True)],
    "professional": [("npi", "NPI", False), ("credentials", "Professional Credentials (e.g. MD, RN)", False), ("specialty", "Specialty", False),
                     ("license_number", "Medical License Number", False), ("license_state", "License State", False),
                     ("license_expiration", "License Expiration", False), ("dea_number", "DEA Registration Number", False),
                     ("dea_expiration", "DEA Expiration", False)],
}
_DATE_FIELDS = {"dob", "license_expiration", "dea_expiration", "issue_date", "expiration_date", "completion_date", "record_date",
                "date_of_birth", "document_date"}


def professional_fields(p: dict) -> list:
    """Only what applies: DEA only with a DEA requirement; license fields required with a license requirement."""
    keys = {t["key"] for t in p["requirements"]}
    out = []
    for key, label, _ in SECTION_FIELDS["professional"]:
        if key.startswith("dea_") and "dea" not in keys:
            continue
        out.append((key, label, key.startswith("license_") and "license" in keys))
    return out


def section_fields(p: dict, section: str) -> list:
    return professional_fields(p) if section == "professional" else SECTION_FIELDS[section]


def _npi_ok(npi: str) -> bool:
    """NPI check digit (Luhn over "80840" + the first nine digits)."""
    if not re.fullmatch(r"\d{10}", npi):
        return False
    digits = "80840" + npi[:9]
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = int(ch)
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10 == int(npi[9])


def validate_fields(fields: list, data: dict, partial: bool = False):
    """Clean the values for these fields. Returns (clean, errors); errors maps field key -> message."""
    clean, errors = {}, {}
    for key, label, required in fields:
        v = str((data or {}).get(key) or "").strip()[:200]
        if not v:
            if required and not partial:
                errors[key] = f"{label} is required."
            clean[key] = ""
            continue
        if key in _DATE_FIELDS:
            try:
                d = dt.date.fromisoformat(v)
            except ValueError:
                errors[key] = "Use a real date."
                continue
            if key == "dob" and not (dt.date(1900, 1, 1) < d < _today() - timedelta(days=365 * 14)):
                errors[key] = "Check the date of birth."
        elif key in ("phone", "ec_phone"):
            if not 10 <= len(re.sub(r"\D", "", v)) <= 15:
                errors[key] = "Enter a phone number with area code."
        elif key in ("state", "license_state"):
            v = v.upper()
            if not re.fullmatch(r"[A-Z]{2}", v):
                errors[key] = "Use the two-letter state code."
        elif key == "zip":
            if not re.fullmatch(r"\d{5}(-\d{4})?", v):
                errors[key] = "Enter a 5-digit ZIP code."
        elif key == "npi":
            if not _npi_ok(v):
                errors[key] = "That isn't a valid 10-digit NPI."
        clean[key] = v
    return clean, errors


# ──────────────────────────────────────────────────────────────────────────
#  Credentials and training records (the same lists the Credentials and Training pages read)
# ──────────────────────────────────────────────────────────────────────────
def _credential_for(doc: dict, p: dict, key: str):
    for c in doc["credentials"]:
        if c.get("personId") == p["id"] and c.get("fromRequirement") == key:
            return c
    return None


def record_credential(doc: dict, p: dict, t: dict, d: StaffDocument, fields: dict, by: str) -> dict:
    """One current credential per requirement. A renewal (or a replacement) moves the previous version into its
    history, so there's never a confusing second "active" copy and nothing is lost."""
    c = _credential_for(doc, p, t["key"])
    if not c:
        c = {"id": _uid("c"), "personId": p["id"], "fromRequirement": t["key"]}
        doc["credentials"].append(c)
    elif c.get("status") == "verified" and c.get("sourceDocumentId") != d.id:
        prev = {k: c.get(k) for k in ("identifier", "issuingAuthority", "state", "issueDate", "expires", "verifiedBy", "verifiedAt",
                                     "sourceDocumentId", "verification")}
        same = (c.get("identifier") or "") == (fields.get("license_number") or fields.get("credential_number") or fields.get("dea_number") or "")
        prev.update(archivedAt=_now().isoformat(), reason="renewed" if same else "replaced")
        c.setdefault("history", []).insert(0, prev)
    c.update({"type": t.get("credentialType") or "other",
              "identifier": fields.get("license_number") or fields.get("credential_number") or fields.get("dea_number") or fields.get("identifier", ""),
              "issuingAuthority": fields.get("issuing_authority") or fields.get("issuer", ""), "state": fields.get("state", ""),
              "issueDate": fields.get("issue_date", ""), "expires": fields.get("expiration_date", ""),
              "status": "verified", "verifiedBy": by, "verifiedAt": _today().isoformat(), "sourceDocumentId": d.id})
    return c


def training_record(doc: dict, p: dict, t: dict, create: bool = True):
    for r in doc["trainings"]:
        if r.get("personId") == p["id"] and r.get("trainingKey") == t.get("trainingKey"):
            return r
    if not create:
        return None
    lib = _find(trainings_for(doc), t.get("trainingKey")) or {}
    r = {"id": _uid("t"), "personId": p["id"], "trainingKey": t.get("trainingKey"), "course": lib.get("title") or t["title"],
         "category": lib.get("category") or "Role-Specific", "due": t.get("dueDate", ""), "completed": "", "certificate": "",
         "assignedAt": _today().isoformat(), "version": lib.get("version", "1")}
    doc["trainings"].append(r)
    return r


# ──────────────────────────────────────────────────────────────────────────
#  Request context helpers
# ──────────────────────────────────────────────────────────────────────────
def _err(msg: str, code: int = 400, **extra):
    return JSONResponse({"error": msg, **extra}, status_code=code)


def manager_ctx(user: User, db: Session):
    """The signed-in clinic admin, their clinic, and its staff document. Everything is scoped to their clinic."""
    if getattr(user, "portal_only", 0) or (user.role or "admin") != "admin":
        raise HTTPException(status_code=403, detail="Only your clinic’s admins can manage staff onboarding.")
    ensure_product(user, db, "staff")
    ensure_area(user, "onboarding")
    org_key = _doc_org_key(user)
    if org_key.startswith("user:"):
        raise HTTPException(status_code=400, detail="Add your clinic’s name in Settings > Practice before adding staff.")
    row, doc = load_staff(db, org_key)
    return org_key, row, doc


def portal_ctx(user: User, db: Session):
    """The signed-in employee's own staff record in the clinic they're signed in to, never an id from the request."""
    org_key = _doc_org_key(user)
    m = membership(db, user.id, org_key)
    if not m or not m.staff_person_id:
        raise HTTPException(status_code=403, detail="You don’t have a Staff Portal at this clinic.")
    if m.status != "active":
        raise HTTPException(status_code=403, detail="Your access to this clinic is paused. Contact your manager.")
    row, doc = load_staff(db, org_key)
    p = person_of(doc, m.staff_person_id)
    if not p:
        raise HTTPException(status_code=404, detail="Your staff record wasn’t found. Contact your manager.")
    return org_key, m, row, doc, p


def _actor_name(user: User) -> str:
    return user.full_name or user.email


# ──────────────────────────────────────────────────────────────────────────
#  Manager: reference data and overview
# ──────────────────────────────────────────────────────────────────────────
@router.get("/api/staff/onboarding/meta")
def onboarding_meta(user: User = Depends(require_user), db: Session = Depends(get_db)):
    import staff_lifecycle
    import credential_verification
    org_key, _, doc = manager_ctx(user, db)
    return {"catalog": {k: _item_from_catalog(k) for k in CATALOG}, "templates": templates_for(doc), "forms": forms_for(doc),
            "trainings": trainings_for(doc), "roles": [r["name"] for r in doc["roles"]], "lifecycle": LIFECYCLE_LABELS,
            "roleAccess": {r["name"]: role_access(doc, r["name"]) for r in doc["roles"]},
            "clinic": _clinic_name(org_key), "docFields": {k: staff_extraction.fields_for(k) for k in staff_extraction.REQUIREMENT_TYPES},
            "inviteDays": INVITE_TTL_DAYS, "generic": dict(default_template("Custom"), name="Standard"),
            "reminderRules": staff_lifecycle.rules(doc), "profileLabels": PROFILE_LABELS, "defaultPrefill": DEFAULT_FORM_PREFILL,
            "automation": {"connected": staff_extraction.available(), "processor": staff_extraction.CLAUDE_MODEL,
                           "policy": automation_policy(doc), "externalSources": credential_verification.source_names()}}


def _warn_days(db: Session, org_key: str) -> int:
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == org_key, OrgSettings.category == "team_prefs"))
    try:
        n = int((_json.loads(row.data) if row else {}).get("credential_warn_days") or 0)
    except Exception:
        n = 0
    return n if n > 0 else CREDENTIAL_WARN_DAYS


def attention_items(db: Session, org_key: str, doc: dict, invites: dict) -> list:
    """Exceptions a manager should act on, most urgent first, not a list of everything that's fine."""
    items = []
    docs = db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.status == "NEEDS_REVIEW",
                                                  StaffDocument.superseded == 0)).all()
    people = {p["id"]: p for p in doc["people"]}
    for d in docs:
        p = people.get(d.person_id)
        if not p or p.get("lifecycle") == "OFFBOARDED":
            continue
        t = _find(p["requirements"], d.requirement_key) or {"title": d.doc_type}
        rv = _latest_review(db, d)
        top = (_json.loads(rv.manager_summary or "[]") or [""])[0] if rv else ""
        items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "review_document", "ref": d.id,
                      "text": f"{t['title']}: {top}" if top else f"{t['title']} awaiting review"})
    for p in doc["people"]:
        lc = p.get("lifecycle")
        if lc == "OFFBOARDED":
            continue
        if p.get("pendingInfo", {}).get("professional") or (_find(p["requirements"], "professional") or {}).get("status") == "NEEDS_REVIEW":
            items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "review_info", "ref": "professional",
                          "text": "Professional information awaiting confirmation"})
        if lc == "PENDING_REVIEW" and not any(t.get("status") == "NEEDS_REVIEW" for t in p["requirements"]):
            items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "review_onboarding", "ref": "",
                          "text": "Employee requirements complete and ready for your review"})
        if lc == "INVITED" and invites.get(p["id"], {}).get("state") == "expired":
            items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 2, "action": "resend_invite", "ref": "",
                          "text": "Invitation expired"})
        if lc in ("INVITE_ACCEPTED", "ONBOARDING", "PENDING_REVIEW", "ACTIVE"):
            for t in p["requirements"]:
                if effective_status(t) == "OVERDUE":
                    who = "" if t.get("owner") != "manager" else " (yours)"
                    items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 0, "action": "open", "ref": t["key"],
                                  "text": f"{t['title']} overdue{who}"})
    warn = _warn_days(db, org_key)
    for c in doc["credentials"]:
        p = people.get(c.get("personId"))
        if not p or p.get("lifecycle") != "ACTIVE" or not c.get("expires"):
            continue
        try:
            days = (dt.date.fromisoformat(c["expires"]) - _today()).days
        except Exception:
            continue
        label = CREDENTIAL_LABELS.get(c.get("type"), "Credential")
        if days < 0:
            items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 0, "action": "open", "ref": c["id"], "text": f"{label} expired"})
    for p in doc["people"]:
        for t in (p.get("offboarding") or {}).get("tasks") or []:
            if not t.get("done"):
                items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "offboarding_task", "ref": t["key"],
                              "text": f"Offboarding: {t['title']}"})
        if p.get("lifecycle") in ("ONBOARDING", "PENDING_REVIEW", "INVITE_ACCEPTED"):
            waiting = [t for t in p["requirements"] if t.get("owner") == "manager" and t.get("status") not in ("COMPLETE", "CLOSED")
                       and t["key"] not in AUTO_AT_ACTIVATION and t["key"] != "location"]
            mine = [t for t in p["requirements"] if t.get("required", True) and t.get("owner") != "manager"]
            if waiting and all(t.get("status") == "COMPLETE" for t in mine):
                for t in waiting:   # e.g. EHR Access: outside systems are set up by a person
                    items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "manager_task", "ref": t["key"],
                                  "text": f"{t['title']} needed"})
    import althais_training as _at      # here, not at the top: althais_training imports this module
    for r in _at.readiness_rows(db, org_key, doc):
        if r["status"] == "NEEDS_HELP":
            topics = ", ".join(_at.LESSON_TITLES.get(k, k) for k in r["struggling"])
            items.append({"personId": r["personId"], "name": r["name"], "severity": 1, "action": "training_help", "ref": "",
                          "text": f"Having trouble with Althais Training ({topics})", "category": "Training"})
    for it in items:
        it.setdefault("role", (people.get(it["personId"]) or {}).get("role", ""))
        it.setdefault("category", {"review_document": "Documents", "review_info": "Identity & Professional Information",
                                   "review_onboarding": "Ready To Activate", "resend_invite": "Invitations", "offboarding_task": "Offboarding",
                                   "manager_task": "Access Setup"}.get(it["action"], "Credentials" if str(it["ref"]).startswith("c_") else "Overdue"))
    return sorted(items, key=lambda i: i["severity"])


@router.get("/api/staff/onboarding/overview")
def onboarding_overview(user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc = manager_ctx(user, db)
    run_reminders(db, org_key, doc, row)
    invites = {p["id"]: _invite_json(latest_invite(db, org_key, p["id"])) for p in doc["people"]}
    attention = attention_items(db, org_key, doc, invites)
    warn = _warn_days(db, org_key)
    expiring = 0
    for c in doc["credentials"]:
        p = person_of(doc, c.get("personId"))
        if p and p.get("lifecycle") == "ACTIVE" and c.get("expires"):
            try:
                if (dt.date.fromisoformat(c["expires"]) - _today()).days <= warn:
                    expiring += 1
            except Exception:
                pass
    rows = []
    for p in doc["people"]:
        refresh(p)
        rows.append({"id": p["id"], "name": p.get("name", ""), "email": p.get("email", ""), "role": p.get("role", ""),
                     "location": p.get("location", ""), "lifecycle": p["lifecycle"], "label": LIFECYCLE_LABELS[p["lifecycle"]],
                     "progress": progress(p), "nextAction": next_action(p, invites[p["id"]]), "invite": invites[p["id"]],
                     "hasAccount": bool(p.get("userId")), "start": p.get("start", "")})
    counts = {k: 0 for k in (staff_doc_review.AUTO_APPROVED, staff_doc_review.ACTION_REQUIRED, staff_doc_review.MANAGER_REVIEW)}
    for dec, in db.execute(select(StaffDocumentReview.decision).where(StaffDocumentReview.org_key == org_key)):
        if dec in counts:
            counts[dec] += 1
    automation = {"processed": sum(counts.values()), "autoApproved": counts[staff_doc_review.AUTO_APPROVED],
                  "employeeCorrections": counts[staff_doc_review.ACTION_REQUIRED], "managerReviews": counts[staff_doc_review.MANAGER_REVIEW],
                  "connected": staff_extraction.available(), "autoApprove": automation_policy(doc)["autoApprove"],
                  "enabled": automation_policy(doc)["enabled"]}
    return {"automation": automation, "stats": {"total": sum(1 for p in doc["people"] if p["lifecycle"] != "OFFBOARDED"),
                      "onboarding": sum(1 for p in doc["people"] if p["lifecycle"] in ONBOARDING_STATES),
                      "attention": len({(i["personId"], i["text"]) for i in attention}), "expiring": expiring, "warnDays": warn},
            "rows": rows, "attention": attention}


# ──────────────────────────────────────────────────────────────────────────
#  Manager: adding people, invitations, role and requirement changes
# ──────────────────────────────────────────────────────────────────────────
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _clean_items(items, role: str) -> list:
    """Requirement items from the manager's editor: known keys keep their catalog meaning; custom ones need a title."""
    out, seen = [], set()
    for it in items if isinstance(items, list) else []:
        if not isinstance(it, dict):
            continue
        key = str(it.get("key") or "").strip()[:64]
        if not key or key in seen:
            continue
        seen.add(key)
        base = _item_from_catalog(key, role) if key in CATALOG else {
            "key": key, "type": it.get("type") if it.get("type") in ("document", "form", "training", "manager") else "manager",
            "owner": "manager" if it.get("type") == "manager" else "employee", "required": True, "reminderDaysBefore": 2,
            "dependsOn": [], "description": ""}
        if key not in CATALOG:
            base["title"] = str(it.get("title") or "").strip()[:120] or "Custom Requirement"
            base["description"] = str(it.get("description") or "").strip()[:500]
            if base["type"] == "document":
                base["docType"] = "other"
            if base["type"] == "form":
                base["formKey"] = str(it.get("formKey") or key)[:64]
            if base["type"] == "training":
                base["trainingKey"] = str(it.get("trainingKey") or key)[:64]
        base["required"] = bool(it.get("required", base.get("required", True)))
        for k in ("dueDays", "reminderDaysBefore"):
            if k in it:
                try:
                    base[k] = None if it[k] in (None, "") else max(-365, min(365, int(it[k])))
                except (TypeError, ValueError):
                    pass
        deps = it.get("dependsOn") if isinstance(it.get("dependsOn"), list) else []
        base["dependsOn"] = [str(d)[:64] for d in deps if d != key][:5]
        out.append(base)
    keys = {i["key"] for i in out}
    for i in out:
        i["dependsOn"] = [d for d in i["dependsOn"] if d in keys]
    return out


@router.post("/api/staff/onboarding/people")
async def add_staff(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc = manager_ctx(user, db)
    body = await request.json()
    s = lambda k, n=120: str(body.get(k) or "").strip()[:n]
    first, last, email = s("firstName"), s("lastName"), s("email", 255).lower()
    role = s("role")
    if not first or not last:
        return _err("Add their first and last name.")
    if not _EMAIL.match(email):
        return _err("Enter a valid email address.")
    if any((p.get("email") or "").lower() == email and p.get("lifecycle") != "OFFBOARDED" for p in doc["people"]):
        return _err("Someone with that email is already on your staff list.")
    if not role:
        return _err("Pick a role.")
    if not _find(doc["roles"], role, "name"):
        # a custom role: added to Staff > Roles with no access until the manager grants some there
        doc["roles"].append({"name": role, "perms": {k: 0 for k in PERMISSION_AREAS}})
    start = s("start", 10)
    if start:
        try:
            dt.date.fromisoformat(start)
        except ValueError:
            return _err("Use a real start date.")
    p = {"id": _uid("p"), "name": f"{first} {last}", "firstName": first, "lastName": last, "email": email, "phone": s("phone", 40),
         "role": role, "employment": s("employment", 40) or "Full-Time", "location": s("location"), "start": start,
         "supervisor": s("supervisor"), "status": "onboarding", "lifecycle": "DRAFT", "notes": "", "createdAt": _now().isoformat(),
         "info": {"personal": {"legal_first": first, "legal_last": last, "phone": s("phone", 40)}, "emergency": {}, "professional": {}},
         "pendingInfo": {}, "infoMeta": {}, "infoVerified": {}, "requirements": [], "audit": []}
    items = body.get("requirements")
    tpl = _find(templates_for(doc), role, "role") or default_template(role)
    chosen = _clean_items(items, role) if isinstance(items, list) and items else tpl["items"]
    p["requirements"] = build_tasks(p, chosen + role_form_items(doc, role, {i["key"] for i in chosen}))
    if p["location"] and not any(l.get("name") == p["location"] for l in doc["clinic"].setdefault("locations", [])):
        doc["clinic"]["locations"].append({"name": p["location"], "address": ""})
    for t in p["requirements"]:
        if t.get("type") == "training":
            training_record(doc, p, t)
    for k in ("email", "phone", "role", "location", "start", "employment", "supervisor"):
        set_profile(p, k, p.get(k), "Set by your clinic", True, by=_actor_name(user))
    set_profile(p, "legal_first", first, "Set by your clinic", False, by=_actor_name(user))
    set_profile(p, "legal_last", last, "Set by your clinic", False, by=_actor_name(user))
    doc["people"].append(p)
    audit(db, org_key, user, "staff_added", p, "person", p["id"], f"Added as {role}")
    out = {"ok": True, "personId": p["id"]}
    if body.get("sendInvite"):
        res = send_invite(db, request, org_key, p, user)
        p["lifecycle"] = "INVITED"
        audit(db, org_key, user, "invite_sent", p, "invitation", "", f"Onboarding invitation sent to {email}")
        out.update(emailed=res["emailed"], inviteLink=res["link"], tempPassword=res["tempPassword"])
    refresh(p)
    save_staff(db, org_key, row, doc)
    return out


def _manager_person(user, db, pid):
    org_key, row, doc = manager_ctx(user, db)
    p = person_of(doc, pid)
    if not p:
        raise HTTPException(status_code=404, detail="Staff member not found.")
    return org_key, row, doc, p


@router.post("/api/staff/onboarding/people/{pid}/invite")
def invite_person(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Send, or resend: any earlier link stops working."""
    org_key, row, doc, p = _manager_person(user, db, pid)
    if p.get("userId") or p["lifecycle"] not in ("DRAFT", "INVITED"):
        return _err("They’ve already accepted their invitation.")
    if not _EMAIL.match(p.get("email") or ""):
        return _err("Add a valid email address for them first.")
    resend = p["lifecycle"] == "INVITED"
    res = send_invite(db, request, org_key, p, user)
    p["lifecycle"] = "INVITED"
    audit(db, org_key, user, "invite_resent" if resend else "invite_sent", p, "invitation", "",
          f"Onboarding invitation {'resent' if resend else 'sent'} to {p['email']}")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True, "emailed": res["emailed"], "inviteLink": res["link"], "tempPassword": res["tempPassword"],
            "invite": _invite_json(res["invitation"])}


@router.post("/api/staff/onboarding/people/{pid}/invite/cancel")
def cancel_invite(pid: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    if not _revoke_pending(db, org_key, pid):
        return _err("There’s no open invitation to cancel.")
    _retire_temp_login(db, p["email"])
    if p["lifecycle"] == "INVITED":
        p["lifecycle"] = "DRAFT"
    audit(db, org_key, user, "invite_cancelled", p, "invitation", "", "Invitation cancelled")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.post("/api/staff/onboarding/people/{pid}/role")
async def change_role(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    role = str(body.get("role") or "").strip()[:120]
    if not _find(doc["roles"], role, "name"):
        return _err("Pick one of your clinic’s roles (Staff > Roles).")
    if role == p.get("role"):
        return {"ok": True}
    old = p.get("role", "")
    p["role"] = role
    if body.get("applyTemplate", True) and p["lifecycle"] != "ACTIVE":
        tpl = _find(templates_for(doc), role, "role") or default_template(role)
        p["requirements"] = build_tasks(p, tpl["items"] + role_form_items(doc, role, {i["key"] for i in tpl["items"]}))
    for inv in db.scalars(select(StaffInvitation).where(StaffInvitation.org_key == org_key, StaffInvitation.person_id == pid,
                                                         StaffInvitation.status == "pending")):
        inv.role_name = role
    set_profile(p, "role", role, "Set by your clinic", True, by=_actor_name(user))
    audit(db, org_key, user, "role_changed", p, "person", pid, f"Role changed from {old} to {role}")
    if p["lifecycle"] == "ACTIVE":
        _apply_access(db, org_key, doc, p, user)
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.put("/api/staff/onboarding/people/{pid}/requirements")
async def edit_requirements(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    items = _clean_items(body.get("items"), p.get("role", ""))
    if not items:
        return _err("Keep at least one requirement.")
    before = {t["key"] for t in p["requirements"]}
    p["requirements"] = build_tasks(p, items)
    for t in p["requirements"]:
        if t.get("type") == "training":
            training_record(doc, p, t)
    added = [t["title"] for t in p["requirements"] if t["key"] not in before]
    removed = len(before - {t["key"] for t in p["requirements"]})
    audit(db, org_key, user, "requirements_changed", p, "person", pid,
          "Requirements edited" + (f": added {', '.join(added)}" if added else "") + (f"; {removed} removed" if removed else ""))
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.put("/api/staff/onboarding/people/{pid}")
async def edit_person(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The clinic-controlled fields: location, start date, employment type, supervisor, phone, email (before the invite is accepted)."""
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    changed = []
    for k, n in (("location", 120), ("employment", 40), ("supervisor", 120), ("phone", 40)):
        if k in body and str(body[k] or "").strip()[:n] != (p.get(k) or ""):
            p[k] = str(body[k] or "").strip()[:n]
            changed.append(k)
    if "start" in body and (body["start"] or "") != (p.get("start") or ""):
        try:
            if body["start"]:
                dt.date.fromisoformat(body["start"])
        except ValueError:
            return _err("Use a real start date.")
        p["start"] = body["start"] or ""
        p["requirements"] = build_tasks(p, p["requirements"])   # due dates follow the start date
        changed.append("start date")
    if "email" in body:
        email = str(body["email"] or "").strip().lower()
        if email != p.get("email"):
            if p.get("userId"):
                return _err("Their email is their Althais login now. They can change it themselves.")
            if not _EMAIL.match(email):
                return _err("Enter a valid email address.")
            old = p["email"]
            p["email"] = email
            if _revoke_pending(db, org_key, pid):
                p["lifecycle"] = "DRAFT"   # the old invitation went to the old address
                _retire_temp_login(db, old)
            changed.append("email")
    if changed:
        for k in ("location", "employment", "supervisor", "phone", "start", "email"):
            set_profile(p, k, p.get(k), "Set by your clinic", True, by=_actor_name(user))
        audit(db, org_key, user, "staff_updated", p, "person", pid, "Updated " + ", ".join(changed))
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
#  Manager: one person in detail, reviews, and completing manager tasks
# ──────────────────────────────────────────────────────────────────────────
def _doc_json(d: StaffDocument) -> dict:
    return {"id": d.id, "requirementKey": d.requirement_key, "docType": d.doc_type, "filename": d.original_filename,
            "contentType": d.content_type, "size": d.size, "uploadedAt": _iso(d.uploaded_at), "uploadedBy": d.uploaded_by_name,
            "status": d.status if not (d.status == "VERIFIED" and _expired(d)) else "EXPIRED",
            "expirationDate": d.expiration_date, "fields": _json.loads(d.fields or "{}"),
            "extraction": _json.loads(d.extraction or "{}"), "employeeConfirmed": bool(d.employee_confirmed),
            "verifiedAt": _iso(d.verified_at), "verifiedBy": d.verified_by, "reviewNote": d.review_note, "superseded": bool(d.superseded),
            "decision": d.decision or "", "autoVerified": d.status == "VERIFIED" and d.verified_by == "Althais"}


def _expired(d: StaffDocument) -> bool:
    try:
        return bool(d.expiration_date) and dt.date.fromisoformat(d.expiration_date) < _today()
    except ValueError:
        return False


def _tasks_json(p: dict) -> list:
    return [dict(t, effectiveStatus=effective_status(t), available=task_available(p, t)) for t in p["requirements"]]


@router.get("/api/staff/onboarding/people/{pid}")
def person_detail(pid: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    refresh(p)
    docs = db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.person_id == pid)
                      .order_by(StaffDocument.id.desc())).all()
    events = db.scalars(select(StaffAuditEvent).where(StaffAuditEvent.org_key == org_key, StaffAuditEvent.person_id == pid)
                        .order_by(StaffAuditEvent.id.desc()).limit(60)).all()
    inv = latest_invite(db, org_key, pid)
    m = db.scalar(select(OrgMembership).where(OrgMembership.org_key == org_key, OrgMembership.staff_person_id == pid))
    return {"person": {k: p.get(k) for k in ("id", "name", "firstName", "lastName", "email", "phone", "role", "employment", "location",
                                            "start", "supervisor", "lifecycle", "notes", "info", "pendingInfo", "infoVerified", "infoMeta",
                                            "offboarding", "profile", "verifications")},
            "label": LIFECYCLE_LABELS[p["lifecycle"]], "progress": progress(p), "tasks": _tasks_json(p),
            "documents": [_doc_json(d) for d in docs], "invite": _invite_json(inv),
            "sections": {s: section_fields(p, s) for s in ("personal", "emergency", "professional")},
            "credentials": [c for c in doc["credentials"] if c.get("personId") == pid],
            "trainings": [t for t in doc["trainings"] if t.get("personId") == pid],
            "access": role_access(doc, p.get("role", "")),
            "membership": {"status": m.status, "appAccess": bool(m.app_access), "role": m.role} if m else None,
            "events": [{"at": _iso(e.created_at), "by": e.actor_name, "action": e.action, "detail": e.detail} for e in events]}


def approve_document(db: Session, org_key: str, doc: dict, p: dict, d: StaffDocument, fields: dict, actor, auto: bool = False,
                     note: str = "") -> None:
    """Verify a document (a manager's approval, or Althais's automatic one) and carry it through: the staff record,
    the credential or training record, the onboarding task, and reminders (which follow credential expirations)."""
    name = "Althais" if actor is None else _actor_name(actor)
    t = _find(p["requirements"], d.requirement_key)
    title = t["title"] if t else d.doc_type
    d.fields = _json.dumps(fields)
    d.expiration_date = fields.get("expiration_date", "")
    d.status, d.verified_at, d.verified_by, d.review_note = "VERIFIED", _now(), name, ""
    hist = _json.loads(d.history or "[]")
    hist.append({"status": "VERIFIED", "at": _now().isoformat(), "by": name, "auto": auto})
    d.history = _json.dumps(hist)
    for old in db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.person_id == d.person_id,
                                                      StaffDocument.requirement_key == d.requirement_key, StaffDocument.id != d.id)):
        old.superseded = 1   # a renewal replaces the earlier verified copy
    how = "automatically verified by Althais" if auto else "verified"
    extra = f": {note}" if note else ""
    if not t:
        audit(db, org_key, actor, "document_auto_approved" if auto else "document_verified", p, "document", d.id, f"{title} {how}{extra}")
        return
    set_task(p, t["key"], "COMPLETE", name, note="")
    source = f"{title} document"
    for fk, pk in DOC_PROFILE_FIELDS.get(t.get("docType") or "", {}).items():
        if fields.get(fk):
            set_profile(p, pk, fields[fk], source, True, ref=d.id, by=name)
            prof = p["info"]["professional"]
            if pk in ("license_number", "license_state", "license_expiration", "dea_number", "dea_expiration") and not prof.get(pk):
                prof[pk] = fields[fk]   # My Information shows what the verified document says
    if t.get("type") == "training":
        r = training_record(doc, p, t)
        done = fields.get("completion_date") or _today().isoformat()
        lib = _find(trainings_for(doc), t.get("trainingKey")) or {}
        months = int(lib.get("validMonths") or 0)
        r.update({"completed": done, "certificate": f"Certificate on file (document {d.id})", "version": str(lib.get("version") or "1"),
                  "expires": fields.get("expiration_date") or ((dt.date.fromisoformat(done) + timedelta(days=round(months * 30.44))).isoformat()
                                                                if months else "")})
        audit(db, org_key, actor, "document_auto_approved" if auto else "training_completed", p, "training", r["id"], f"{title} certificate {how}{extra}")
    elif t.get("credentialType"):
        c = record_credential(doc, p, t, d, fields, name)
        audit(db, org_key, actor, "document_auto_approved" if auto else "credential_verified", p, "credential", c["id"], f"{title} {how}{extra}")
    else:
        audit(db, org_key, actor, "document_auto_approved" if auto else "document_verified", p, "document", d.id, f"{title} {how}{extra}")


def send_back_document(db: Session, org_key: str, p: dict, d: StaffDocument, reason: str, actor, action: str) -> None:
    """The employee needs to upload a new one. They see the reason."""
    t = _find(p["requirements"], d.requirement_key)
    d.status, d.review_note = "REJECTED", reason
    hist = _json.loads(d.history or "[]")
    hist.append({"status": "REJECTED", "at": _now().isoformat(), "by": "Althais" if actor is None else _actor_name(actor), "action": action})
    d.history = _json.dumps(hist)
    if t:
        set_task(p, t["key"], "WAITING_ON_EMPLOYEE", note=reason)


@router.post("/api/staff/onboarding/documents/{doc_id}/review")
async def review_document(doc_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """approve (optionally with corrected details and a note), reject, or request_correction (a new document):
    the last two need a reason, which the employee sees."""
    org_key, row, doc = manager_ctx(user, db)
    d = db.scalar(select(StaffDocument).where(StaffDocument.id == doc_id, StaffDocument.org_key == org_key))
    if not d:
        return _err("Document not found.", 404)
    p = person_of(doc, d.person_id)
    if not p:
        return _err("Staff member not found.", 404)
    body = await request.json()
    action, reason = body.get("action"), str(body.get("reason") or "").strip()[:1000]
    note = str(body.get("note") or "").strip()[:1000]
    t = _find(p["requirements"], d.requirement_key)
    title = t["title"] if t else d.doc_type
    if action == "approve":
        field_defs = staff_extraction.fields_for(d.doc_type, (t or {}).get("credentialType", ""))
        clean, errors = validate_fields([(k, l, False) for k, l in field_defs],
                                        body.get("fields") if isinstance(body.get("fields"), dict) else _json.loads(d.fields or "{}"), partial=True)
        if errors:
            return _err("Check the highlighted details.", fields=errors)
        approve_document(db, org_key, doc, p, d, clean, user, note=note)
    elif action in ("reject", "request_correction"):
        if not reason:
            return _err("Tell them what’s wrong so they can fix it.")
        send_back_document(db, org_key, p, d, reason, user, action)
        verb = "rejected" if action == "reject" else "new document requested"
        audit(db, org_key, user, "document_rejected", p, "document", d.id, f"{title} {verb}: {reason}" + (f" (note: {note})" if note else ""))
    else:
        return _err("Unknown review action.")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


def _latest_review(db: Session, d: StaffDocument):
    if d.review_id:
        rv = db.get(StaffDocumentReview, d.review_id)
        if rv:
            return rv
    return db.scalar(select(StaffDocumentReview).where(StaffDocumentReview.document_id == d.id).order_by(StaffDocumentReview.id.desc()).limit(1))


def _review_json(rv) -> dict:
    if not rv:
        return None
    checks = _json.loads(rv.validation_results or "{}")
    return {"id": rv.id, "decision": rv.decision, "reasonCodes": _json.loads(rv.reason_codes or "[]"),
            "detectedType": rv.detected_type, "detectedLabel": staff_extraction.DOC_TYPES.get(rv.detected_type, ""),
            "typeConfidence": rv.type_confidence, "fields": _json.loads(rv.extracted_fields or "{}"),
            "checks": [{"key": k, "label": staff_doc_review.CHECK_LABELS.get(k, k), "result": v} for k, v in checks.items()],
            "issues": _json.loads(rv.issue_flags or "[]"), "external": _json.loads(rv.external_verification or "{}"),
            "managerSummary": _json.loads(rv.manager_summary or "[]"), "analysisStatus": rv.analysis_status,
            "processor": rv.processor_version, "at": _iso(rv.created_at)}


@router.get("/api/staff/onboarding/documents/{doc_id}/review")
def document_review_detail(doc_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Everything the manager needs to decide: the document, what Althais read, what's on file, and every check."""
    org_key, row, doc = manager_ctx(user, db)
    d = db.scalar(select(StaffDocument).where(StaffDocument.id == doc_id, StaffDocument.org_key == org_key))
    if not d:
        return _err("Document not found.", 404)
    p = person_of(doc, d.person_id) or {}
    t = _find(p.get("requirements", []), d.requirement_key) or {"title": d.doc_type}
    cred = _credential_for(doc, p, d.requirement_key) if p else None
    personal, prof = p.get("info", {}).get("personal", {}), p.get("info", {}).get("professional", {})
    on_file = {"Name": " ".join(x for x in (personal.get("legal_first"), personal.get("middle"), personal.get("legal_last")) if x) or p.get("name", "")}
    if cred and cred.get("status") == "verified":
        on_file.update({"Number On File": cred.get("identifier", ""), "State On File": cred.get("state", ""), "Expires On File": cred.get("expires", "")})
    if t.get("docType") == "license" and (prof.get("license_number") or prof.get("license_state")):
        on_file.update({"License # (Employee Entered)": prof.get("license_number", ""), "License State (Employee Entered)": prof.get("license_state", ""),
                        "License Expiration (Employee Entered)": prof.get("license_expiration", "")})
    return {"document": _doc_json(d), "employee": p.get("name", ""), "personId": p.get("id", ""), "requirement": t.get("title", ""),
            "requiredType": ", ".join(staff_extraction.DOC_TYPES.get(x, x) for x in sorted(staff_doc_review.accepted_types(t, p.get("role", "")))) if t.get("docType") != "other" else t.get("title", ""),
            "fieldDefs": staff_extraction.fields_for(d.doc_type, t.get("credentialType", "")), "onFile": {k: v for k, v in on_file.items() if v},
            "review": _review_json(_latest_review(db, d))}


@router.post("/api/staff/onboarding/people/{pid}/info/review")
async def review_info(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Confirm (optionally corrected) or send back the employee's professional information."""
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    action, reason = body.get("action"), str(body.get("reason") or "").strip()[:1000]
    proposed = p["pendingInfo"].get("professional") or p["info"]["professional"]
    if action == "approve":
        data = body.get("fields") if isinstance(body.get("fields"), dict) else proposed
        clean, errors = validate_fields(professional_fields(p), data)
        if errors:
            return _err("Check the highlighted details.", fields=errors)
        p["info"]["professional"] = clean
        p["pendingInfo"].pop("professional", None)
        p["infoVerified"]["professional"] = {"at": _now().isoformat(), "by": _actor_name(user)}
        for k, v in clean.items():
            set_profile(p, k, v, "Confirmed by your clinic", True, by=_actor_name(user))
        set_task(p, "professional", "COMPLETE", _actor_name(user), note="")
        audit(db, org_key, user, "info_verified", p, "info", "professional", "Professional information confirmed")
    elif action in ("reject", "request_correction"):
        if not reason:
            return _err("Tell them what to correct.")
        p["pendingInfo"].pop("professional", None)
        set_task(p, "professional", "WAITING_ON_EMPLOYEE", note=reason)
        audit(db, org_key, user, "info_rejected", p, "info", "professional", f"Professional information sent back: {reason}")
    else:
        return _err("Unknown review action.")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.post("/api/staff/onboarding/people/{pid}/tasks/{key}")
async def manager_task(pid: str, key: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """complete / reopen a task. Managers can complete their own tasks, or mark any task done after checking it
    themselves (e.g. a paper form), both are audited."""
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    t = _find(p["requirements"], key)
    if not t:
        return _err("Requirement not found.", 404)
    note = str(body.get("note") or "").strip()[:500]
    if body.get("action") == "complete":
        if key in AUTO_AT_ACTIVATION:
            return _err("This one is completed when you approve and activate them.")
        set_task(p, key, "COMPLETE", _actor_name(user), note=note or None)
        if t.get("type") == "training":
            r = training_record(doc, p, t)
            r["completed"] = r.get("completed") or _today().isoformat()
        audit(db, org_key, user, "task_completed", p, "task", key, f"{t['title']} marked complete" + (f": {note}" if note else ""))
    elif body.get("action") == "reopen":
        set_task(p, key, "WAITING_ON_MANAGER" if t.get("owner") == "manager" else "WAITING_ON_EMPLOYEE", note=note or None)
        audit(db, org_key, user, "task_reopened", p, "task", key, f"{t['title']} reopened" + (f": {note}" if note else ""))
    else:
        return _err("Unknown action.")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.get("/api/staff/onboarding/documents/{doc_id}/file")
def manager_file(doc_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, _, _ = manager_ctx(user, db)
    d = db.scalar(select(StaffDocument).where(StaffDocument.id == doc_id, StaffDocument.org_key == org_key))
    return _file_response(db, d)


def _file_response(db: Session, d):
    if not d:
        return _err("Document not found.", 404)
    f = db.scalar(select(StaffFile).where(StaffFile.document_id == d.id))
    if not f:
        return _err("The file is missing.", 404)
    safe = re.sub(r'[^A-Za-z0-9._ -]+', "_", d.original_filename or "document")
    return Response(content=f.data, media_type=d.content_type or "application/octet-stream",
                    headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, no-store",
                             "X-Content-Type-Options": "nosniff"})


# ──────────────────────────────────────────────────────────────────────────
#  Manager: activation and after
# ──────────────────────────────────────────────────────────────────────────
def _staff_membership(db: Session, org_key: str, p: dict):
    if not p.get("userId"):
        return None
    return membership(db, p["userId"], org_key)


def _apply_access(db: Session, org_key: str, doc: dict, p: dict, actor) -> dict:
    """Give an active staff member the access their role on Staff > Roles grants (server-enforced)."""
    access = role_access(doc, p.get("role", ""))
    m = _staff_membership(db, org_key, p)
    if m and m.kind == "staff":
        m.app_access, m.role, m.blocked, m.status = 1 if access["app_access"] else 0, access["role"], access["blocked"], "active"
        sync_if_active(db, p["userId"], m)
        opened = ", ".join(a for a in access["areas"]) or "Staff Portal only"
        audit(db, org_key, actor, "permissions_changed", p, "membership", m.id, f"Althais access set from the {p.get('role')} role: {opened}")
    return access


@router.get("/api/staff/onboarding/people/{pid}/activation")
def activation_check(pid: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    return _activation(p, doc)


def _activation(p: dict, doc: dict) -> dict:
    left = [{"key": t["key"], "title": t["title"], "owner": t.get("owner"), "status": effective_status(t)}
            for t in p["requirements"] if t.get("required", True) and t.get("status") != "COMPLETE" and t["key"] not in AUTO_AT_ACTIVATION]
    return {"ready": bool(p.get("userId")) and not left and p["lifecycle"] in ("ONBOARDING", "PENDING_REVIEW", "INVITE_ACCEPTED"),
            "accepted": bool(p.get("userId")), "remaining": left, "access": role_access(doc, p.get("role", "")),
            "employeeDone": all(t.get("status") == "COMPLETE" for t in p["requirements"] if t.get("required", True) and t.get("owner") != "manager")}


@router.post("/api/staff/onboarding/people/{pid}/activate")
def activate(pid: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    check = _activation(p, doc)
    if not check["accepted"]:
        return _err("They haven’t accepted their invitation yet.")
    if not check["ready"]:
        return _err("Finish the remaining requirements first.", remaining=check["remaining"])
    for key in AUTO_AT_ACTIVATION:
        set_task(p, key, "COMPLETE", _actor_name(user))
    p["lifecycle"], p["activeSince"] = "ACTIVE", _today().isoformat()
    access = _apply_access(db, org_key, doc, p, user)
    audit(db, org_key, user, "staff_activated", p, "person", pid, "Approved and activated")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True, "access": access}


OFFBOARD_DEFAULT_SYSTEMS = ["EHR", "Email", "Building Access"]


@router.post("/api/staff/onboarding/people/{pid}/offboard")
async def offboard(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """End someone's time at the clinic. Nothing is deleted: access ends, permissions are revoked, open items close,
    credentials are archived, and every record, document and audit event is kept. Outside systems can't be switched
    off from here, so each becomes a task for the clinic."""
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    if p.get("lifecycle") == "OFFBOARDED":
        return _err("They’re already offboarded.")
    if str(body.get("confirm") or "").strip().lower() != (p.get("name") or "").strip().lower():
        return _err("Type their full name exactly to confirm.")
    m = _staff_membership(db, org_key, p)
    if m and m.user_id == user.id:
        return _err("You can’t offboard yourself.")
    last_day = str(body.get("lastDay") or "")[:10] or _today().isoformat()
    try:
        dt.date.fromisoformat(last_day)
    except ValueError:
        return _err("Use a real date for their last day.")
    steps = []
    # 1-2. access and permissions
    if m:
        m.status, m.app_access, m.role, m.blocked = "offboarded", 0, "viewer", ",".join(sorted(AREAS))
        u = db.get(User, m.user_id)
        if u and (u.organization or "").strip() == org_key:
            other = _other_active(db, u.id, org_key)
            if other:
                apply_membership(u, other)   # their other clinics are untouched
            else:
                u.active, m.paused_login = 0, 1
        steps.append({"key": "access", "title": f"Althais access to {_clinic_name(org_key)} ended", "done": True})
        steps.append({"key": "permissions", "title": "Internal Althais permissions revoked", "done": True})
    else:
        steps.append({"key": "access", "title": "No Althais login to end (they never accepted an invitation)", "done": True})
    # 3. invitations
    if _revoke_pending(db, org_key, pid):
        _retire_temp_login(db, p.get("email", ""))
        steps.append({"key": "invites", "title": "Open invitation cancelled", "done": True})
    # 4. open items
    closed = 0
    for t in p["requirements"]:
        if t.get("status") != "COMPLETE":
            t["status"], t["done"], t["closedAt"] = "CLOSED", False, _now().isoformat()
            closed += 1
    steps.append({"key": "tasks", "title": f"{closed} open onboarding item{'s' if closed != 1 else ''} closed", "done": True})
    # 5. credentials
    archived = 0
    for c in doc["credentials"]:
        if c.get("personId") == pid and c.get("status") != "archived":
            c["statusBeforeArchive"], c["status"], c["archivedAt"] = c.get("status"), "archived", _now().isoformat()
            archived += 1
    steps.append({"key": "credentials", "title": f"{archived} credential{'s' if archived != 1 else ''} archived (history kept)", "done": True})
    steps.append({"key": "records", "title": "Documents, training records and audit history kept", "done": True})
    # 6. outside systems: a task each, never pretended
    systems = [str(x).strip()[:80] for x in (body.get("systems") if isinstance(body.get("systems"), list) else []) if str(x).strip()][:12]
    tasks = [{"key": _uid("off"), "title": f"Remove {x}" if x.lower().endswith("access") else f"Remove {x} access", "system": x, "done": False}
             for x in systems]
    p["previousLifecycle"] = p["lifecycle"]
    p["lifecycle"] = "OFFBOARDED"
    p["offboarding"] = {"date": last_day, "by": _actor_name(user), "at": _now().isoformat(), "note": str(body.get("note") or "").strip()[:500],
                        "steps": steps, "tasks": tasks}
    audit(db, org_key, user, "staff_offboarded", p, "person", pid,
          f"Offboarded (last day {_fmt_date(last_day)})" + (f"; outside access to remove: {', '.join(systems)}" if systems else ""))
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True, "offboarding": p["offboarding"]}


@router.post("/api/staff/onboarding/people/{pid}/offboarding/tasks/{key}")
async def offboarding_task(pid: str, key: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    t = _find((p.get("offboarding") or {}).get("tasks") or [], key)
    if not t:
        return _err("Task not found.", 404)
    body = await request.json()
    t["done"] = bool(body.get("done", True))
    t["doneAt"], t["doneBy"] = (_now().isoformat(), _actor_name(user)) if t["done"] else ("", "")
    audit(db, org_key, user, "offboarding_task", p, "person", pid, f"{t['title']} {'done' if t['done'] else 'reopened'}")
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.post("/api/staff/onboarding/people/{pid}/status")
async def change_status(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """suspend | offboard | reactivate. Suspending or offboarding takes away their access to this clinic right away."""
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    action = body.get("action")
    m = _staff_membership(db, org_key, p)
    if m and m.user_id == user.id:
        return _err("You can’t change your own access here.")
    if action == "offboard":
        return _err("Use Offboard Staff Member, which runs the offboarding checklist.")
    if action in ("suspend", "offboard"):
        if action == "offboard":
            _revoke_pending(db, org_key, pid)
            _retire_temp_login(db, p.get("email", ""))
        if m:
            m.status = "suspended" if action == "suspend" else "offboarded"
            u = db.get(User, m.user_id)
            if u and (u.organization or "").strip() == org_key:
                other = _other_active(db, u.id, org_key)
                if other:
                    apply_membership(u, other)      # signed in to this clinic: move them to one they still belong to
                else:
                    u.active, m.paused_login = 0, 1
        p["previousLifecycle"] = p["lifecycle"] if p["lifecycle"] not in ("SUSPENDED", "OFFBOARDED") else p.get("previousLifecycle")
        p["lifecycle"] = "SUSPENDED" if action == "suspend" else "OFFBOARDED"
        audit(db, org_key, user, "staff_suspended" if action == "suspend" else "staff_offboarded", p, "person", pid,
              "Suspended" if action == "suspend" else "Offboarded")
    elif action == "reactivate":
        if p["lifecycle"] not in ("SUSPENDED", "OFFBOARDED"):
            return _err("They’re not suspended or offboarded.")
        for c in doc["credentials"]:
            if c.get("personId") == pid and c.get("status") == "archived":
                c["status"] = c.pop("statusBeforeArchive", "verified") or "verified"
        for t in p["requirements"]:
            if t.get("status") == "CLOSED":
                t["status"] = "WAITING_ON_MANAGER" if t.get("owner") == "manager" else "NOT_STARTED"
        back = p.get("previousLifecycle") or ("ACTIVE" if p.get("activeSince") else "DRAFT")
        p["lifecycle"] = back if back in LIFECYCLE_LABELS else "DRAFT"
        if m:
            m.status = "active"
            if m.paused_login:
                u = db.get(User, m.user_id)
                if u:
                    u.active = 1
                    if (u.organization or "").strip() != org_key and not _other_active(db, u.id, org_key):
                        apply_membership(u, m)
                m.paused_login = 0
        if p["lifecycle"] == "ACTIVE":
            _apply_access(db, org_key, doc, p, user)
        audit(db, org_key, user, "staff_reactivated", p, "person", pid, "Reactivated")
    else:
        return _err("Unknown action.")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.post("/api/staff/onboarding/roles/sync")
def sync_roles(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """After Staff > Roles changes: bring every active staff member's access in line with their role."""
    org_key, row, doc = manager_ctx(user, db)
    n = 0
    for p in doc["people"]:
        if p.get("lifecycle") != "ACTIVE" or not p.get("userId"):
            continue
        m = _staff_membership(db, org_key, p)
        if not m or m.kind != "staff":
            continue
        want = role_access(doc, p.get("role", ""))
        if (m.app_access, m.role, m.blocked) != (1 if want["app_access"] else 0, want["role"], want["blocked"]):
            _apply_access(db, org_key, doc, p, user)
            n += 1
    save_staff(db, org_key, row, doc) if n else db.commit()
    return {"ok": True, "updated": n}


@router.post("/api/staff/onboarding/automation")
async def set_automation(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The manager's on/off switch for automated document review (and, optionally, automatic approval)."""
    org_key, row, doc = manager_ctx(user, db)
    body = await request.json()
    cur = dict(doc["clinic"].get("documentAutomation") or {})
    changed = []
    for k, label in (("enabled", "Automated review"), ("autoApprove", "Automatic approval")):
        if isinstance(body.get(k), bool) and body[k] != automation_policy(doc)[k]:
            cur[k] = body[k]
            changed.append(f"{label} turned {'on' if body[k] else 'off'}")
    if changed:
        doc["clinic"]["documentAutomation"] = cur
        db.add(StaffAuditEvent(org_key=org_key, actor_user_id=user.id, actor_name=_actor_name(user), action="automation_changed",
                               object_type="settings", object_id="documentAutomation", detail="; ".join(changed)))
        save_staff(db, org_key, row, doc)
    return {"ok": True, "policy": automation_policy(doc)}


def _form_entry(doc: dict, key: str) -> dict:
    """The clinic's saved copy of a form (created from the built-in sample the first time it's changed)."""
    f = _find(doc["onboardingForms"], key)
    if not f:
        base = _find(DEFAULT_FORMS, key)
        if not base:
            return None
        f = dict(base)
        doc["onboardingForms"].append(f)
    return f


@router.post("/api/staff/onboarding/forms/{key}/file")
async def upload_form_file(key: str, file: UploadFile = File(...), user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Upload (or replace) a clinic form's PDF. Each upload is a new version; people who already signed keep theirs."""
    org_key, row, doc = manager_ctx(user, db)
    f = _form_entry(doc, key)
    if not f:
        return _err("Form not found.", 404)
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return _err("That file is over 10 MB.")
    if _sniff(data, file.filename or "") != "application/pdf":
        return _err("Upload the form as a PDF.")
    try:
        n = int(f.get("version") or 1)
    except (TypeError, ValueError):
        n = 1
    version = str(n + 1 if (f.get("fileId") or f.get("body")) else n)   # the first file for a new, empty form is version 1
    ff = StaffFormFile(org_key=org_key, form_key=key, version=version, filename=(file.filename or "form.pdf")[:255], size=len(data),
                       sha256=hashlib.sha256(data).hexdigest(), data=data, uploaded_by=_actor_name(user))
    db.add(ff)
    db.flush()
    f.update(fileId=ff.id, fileName=ff.filename, version=version, isSample=False)
    audit(db, org_key, user, "form_uploaded", None, "form", key, f"{f.get('title')} version {version} uploaded ({ff.filename})")
    save_staff(db, org_key, row, doc)
    return {"ok": True, "version": version, "fileId": ff.id}


def _form_file_response(db: Session, org_key: str, f: dict):
    ff = db.scalar(select(StaffFormFile).where(StaffFormFile.id == (f or {}).get("fileId"), StaffFormFile.org_key == org_key))
    if not ff:
        return _err("This form has no file.", 404)
    safe = re.sub(r'[^A-Za-z0-9._ -]+', "_", ff.filename or "form.pdf")
    return Response(content=ff.data, media_type="application/pdf",
                    headers={"Content-Disposition": f'inline; filename="{safe}"', "Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})


@router.get("/api/staff/onboarding/forms/{key}/file")
def manager_form_file(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc = manager_ctx(user, db)
    return _form_file_response(db, org_key, _find(forms_for(doc), key))


@router.post("/api/staff/onboarding/forms/{key}/assign")
def assign_form(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Give a form to everyone currently on staff whose role it's assigned to (people who have it already are skipped)."""
    org_key, row, doc = manager_ctx(user, db)
    f = _find(forms_for(doc), key)
    if not f:
        return _err("Form not found.", 404)
    n = 0
    for p in doc["people"]:
        if p.get("lifecycle") in ("OFFBOARDED", "SUSPENDED") or p.get("role") not in (f.get("roles") or []):
            continue
        have = {t["key"] for t in p["requirements"]} | {t.get("formKey") for t in p["requirements"]}
        items = role_form_items(doc, p["role"], have)
        items = [i for i in items if i["key"] == key]
        if not items:
            continue
        if p.get("lifecycle") == "ACTIVE" and items[0].get("dueDays", 0) is not None:
            items[0]["dueDays"] = max(int(items[0].get("dueDays") or 0), (_today() - _anchor(p)).days + 14)   # a fair deadline for people already working
        p["requirements"] = build_tasks(p, p["requirements"] + items)
        audit(db, org_key, user, "form_assigned", p, "form", key, f"{f.get('title')} assigned")
        refresh(p)
        n += 1
    save_staff(db, org_key, row, doc) if n else None
    return {"ok": True, "assigned": n}


@router.get("/api/portal/forms/{key}/file")
def portal_form_file(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, m, row, doc, p = portal_ctx(user, db)
    t = next((t for t in p["requirements"] if t.get("type") == "form" and t.get("formKey") == key), None)
    if not t:
        return _err("That form isn't assigned to you.", 404)
    return _form_file_response(db, org_key, _find(forms_for(doc), key))


@router.post("/api/staff/onboarding/trainings/{key}/require-current")
def require_current_training(key: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """A training changed materially: everyone who completed an earlier version takes the current one. Their earlier
    completion moves to the record's history (never deleted)."""
    org_key, row, doc = manager_ctx(user, db)
    lib = _find(trainings_for(doc), key)
    if not lib:
        return _err("Training not found.", 404)
    version, due = str(lib.get("version") or "1"), (_today() + timedelta(days=14)).isoformat()
    n = 0
    for r in doc["trainings"]:
        if r.get("trainingKey") != key or not r.get("completed") or str(r.get("version") or "") == version:
            continue
        p = person_of(doc, r.get("personId"))
        if not p or p.get("lifecycle") in ("SUSPENDED", "OFFBOARDED"):
            continue
        r.setdefault("history", []).insert(0, {k: r.get(k) for k in ("completed", "expires", "version", "score", "certificate", "startedAt")})
        r.update(completed="", expires="", score=None, certificate="", startedAt="", modulesDone=[], due=due, assignedAt=_today().isoformat())
        t = next((t for t in p["requirements"] if t.get("type") == "training" and t.get("trainingKey") == key), None)
        if t:
            set_task(p, t["key"], "NOT_STARTED")
            t["dueDate"] = due
        audit(db, org_key, user, "training_reassigned", p, "training", r["id"], f"{lib.get('title')} version {version} required")
        refresh(p)
        n += 1
    save_staff(db, org_key, row, doc) if n else None
    return {"ok": True, "reassigned": n, "version": version}


@router.get("/api/staff/onboarding/audit")
def audit_log(person: str = "", limit: int = 100, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, _, _ = manager_ctx(user, db)
    q = select(StaffAuditEvent).where(StaffAuditEvent.org_key == org_key)
    if person:
        q = q.where(StaffAuditEvent.person_id == person)
    rows = db.scalars(q.order_by(StaffAuditEvent.id.desc()).limit(max(1, min(limit, 500)))).all()
    return [{"at": _iso(e.created_at), "by": e.actor_name, "action": e.action, "personId": e.person_id,
             "object": e.object_type, "objectId": e.object_id, "detail": e.detail} for e in rows]


# ──────────────────────────────────────────────────────────────────────────
#  Invitation acceptance (public page /invite/{token} in main.py)
# ──────────────────────────────────────────────────────────────────────────
@router.get("/api/invitations/{token}")
def invitation_info(token: str, request: Request, db: Session = Depends(get_db)):
    inv, problem = _valid_invite(db, token)
    if not inv:
        return _err(problem, 404)
    _, doc = load_staff(db, inv.org_key)
    p = person_of(doc, inv.person_id) or {}
    me = current_user(request, db)
    existing = db.scalar(select(User).where(User.email == inv.email))
    return {"clinic": _clinic_name(inv.org_key), "role": inv.role_name, "start": p.get("start", ""), "email": inv.email,
            "name": p.get("name", ""), "invitedBy": inv.invited_by_name, "expiresAt": _iso(inv.expires_at),
            "accountExists": bool(existing),
            "tempPassword": bool(existing and existing.must_change_password),
            "signedInAs": me.email if me else None}


@router.post("/api/invitations/accept")
async def accept_invitation(request: Request, db: Session = Depends(get_db)):
    body = await request.json()
    inv, problem = _valid_invite(db, str(body.get("token") or ""))
    if not inv:
        return _err(problem, 400)
    row, doc = load_staff(db, inv.org_key)
    p = person_of(doc, inv.person_id)
    if not p or p.get("lifecycle") in ("SUSPENDED", "OFFBOARDED"):
        return _err("This invitation is no longer active. Ask your clinic to send a new one.")
    password = str(body.get("password") or "")
    existing = db.scalar(select(User).where(User.email == inv.email))
    me = current_user(request, db)
    if existing:
        # the account behind the invited email has to prove it's them: signed in as it, or its password
        if me and me.id != existing.id:
            return _err(f"You’re signed in as {me.email}. This invitation is for {inv.email}. Sign out, then open the link again.", 403)
        if not (me and me.id == existing.id) and not verify_password(password, existing.password_hash):
            return _err("That temporary password isn’t right. Copy it from your newest invitation email." if existing.must_change_password
                        else "That password isn’t right for this account.", 401)
        if not existing.active:
            return _err("This Althais account is paused. Contact the clinic that paused it.", 403)
        user = existing
        _snapshot(db, user)
    else:
        if me:
            return _err(f"You’re signed in as {me.email}. This invitation is for {inv.email}. Sign out, then open the link again.", 403)
        full_name = str(body.get("fullName") or "").strip()[:255]
        if not full_name:
            return _err("Add your name.")
        if len(password) < 8:
            return _err("Use at least 8 characters for your password.")
        # opening the emailed link is what verifies the address
        user = User(email=inv.email, password_hash=hash_password(password), full_name=full_name, organization=inv.org_key,
                    role="viewer", email_verified=1, onboarding_complete=1, portal_only=1, active=1)
        db.add(user)
        db.flush()
    _accept(db, inv, user, p, row, doc)
    resp = JSONResponse({"ok": True, "redirect": "/set-password" if user.must_change_password else "/portal"})
    _set_session_cookie(resp, user.id)
    return resp


def _accept(db: Session, inv, user: User, p: dict, row, doc: dict) -> None:
    m = membership(db, user.id, inv.org_key)
    if m:   # already works at this clinic in Althais: keep their access, link the staff record
        m.staff_person_id, m.status = p["id"], "active"
    else:
        m = OrgMembership(user_id=user.id, org_key=inv.org_key, kind="staff", staff_person_id=p["id"], status="active",
                          app_access=0, role="viewer")
        db.add(m)
    db.flush()
    user.onboarding_complete = 1
    user.email_verified = 1
    switch_to(db, user, m)
    inv.status, inv.accepted_at, inv.accepted_user_id = "accepted", _now(), user.id
    p["userId"] = user.id
    if p["lifecycle"] in ("DRAFT", "INVITED"):
        p["lifecycle"] = "INVITE_ACCEPTED"
    audit(db, inv.org_key, user, "invite_accepted", p, "invitation", inv.id, f"Invitation accepted by {user.email}")
    refresh(p)
    save_staff(db, inv.org_key, row, doc)


def accept_pending_invites(db: Session, user: User) -> int:
    """Signing in with an invitation's temporary password (auth.login) accepts every open invitation to that email."""
    n = 0
    for inv in db.scalars(select(StaffInvitation).where(StaffInvitation.email == user.email, StaffInvitation.status == "pending")
                          .order_by(StaffInvitation.id)).all():
        if invite_state(inv) != "sent":
            continue
        row, doc = load_staff(db, inv.org_key)
        p = person_of(doc, inv.person_id)
        if not p or p.get("lifecycle") in ("SUSPENDED", "OFFBOARDED"):
            continue
        _snapshot(db, user)
        _accept(db, inv, user, p, row, doc)
        n += 1
    return n


# ──────────────────────────────────────────────────────────────────────────
#  Organizations a login belongs to
# ──────────────────────────────────────────────────────────────────────────
@router.get("/api/memberships")
def my_memberships(user: User = Depends(require_user), db: Session = Depends(get_db)):
    ensure_membership(db, user)
    here = (user.organization or "").strip()
    rows = db.scalars(select(OrgMembership).where(OrgMembership.user_id == user.id, OrgMembership.status == "active")).all()
    return {"current": here, "organizations": [{"key": m.org_key, "name": _clinic_name(m.org_key), "current": m.org_key == here,
                                                 "portal": bool(m.staff_person_id), "app": bool(m.app_access)} for m in rows]}


@router.post("/api/memberships/switch")
async def switch_membership(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    body = await request.json()
    key = str(body.get("org") or "")
    m = membership(db, user.id, key)
    if not m or m.status != "active":   # only clinics this login belongs to
        return _err("You don’t have access to that organization.", 403)
    switch_to(db, user, m)
    return {"ok": True, "redirect": "/overview" if m.app_access else "/portal"}


# ──────────────────────────────────────────────────────────────────────────
#  Staff Portal (the employee's own record only)
# ──────────────────────────────────────────────────────────────────────────
GROUPS = [("personal", "Personal Information"), ("employment", "Employment Information"), ("documents", "Documents"),
          ("credentials", "Credentials"), ("forms", "Forms & Agreements"), ("training", "Training")]


def _group_of(t: dict) -> str:
    if t.get("type") == "info":
        return "employment" if t.get("section") == "employment" else "personal"
    if t.get("type") == "document":
        return "credentials" if t.get("credentialType") else "documents"
    return {"form": "forms", "training": "training"}.get(t.get("type"), "")


@router.get("/api/portal/me")
def portal_me(user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, m, row, doc, p = portal_ctx(user, db)
    run_reminders(db, org_key, doc, row)
    refresh(p)
    mine = [t for t in p["requirements"] if t.get("owner") != "manager"]
    groups = []
    for key, label in GROUPS:
        ts = [t for t in mine if _group_of(t) == key]
        if ts:
            groups.append({"key": key, "label": label, "done": sum(1 for t in ts if t.get("status") == "COMPLETE"), "total": len(ts)})
    order = {"WAITING_ON_EMPLOYEE": 0, "OVERDUE": 0, "NOT_STARTED": 1, "IN_PROGRESS": 1}
    todo = [t for t in _tasks_json(p) if t.get("owner") != "manager" and t["effectiveStatus"] in order and t["available"]]
    todo.sort(key=lambda t: (order[t["effectiveStatus"]], t.get("dueDate") or "9999"))
    docs = db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.person_id == p["id"])
                      .order_by(StaffDocument.id.desc())).all()
    forms = {f["key"]: f for f in forms_for(doc)}
    libs = {t["key"]: t for t in trainings_for(doc)}
    info = p["info"]
    return {
        "clinic": _clinic_name(org_key), "email": user.email,
        "person": {"name": p.get("name", ""), "firstName": info["personal"].get("preferred") or p.get("firstName") or p.get("name", "").split(" ")[0],
                   "role": p.get("role", ""), "location": p.get("location", ""), "start": p.get("start", ""),
                   "employment": p.get("employment", ""), "supervisor": p.get("supervisor", ""), "lifecycle": p["lifecycle"],
                   "label": LIFECYCLE_LABELS[p["lifecycle"]]},
        "appAccess": bool(m.app_access), "progress": progress(p), "groups": groups, "nextSteps": todo[:4],
        "tasks": [t for t in _tasks_json(p) if t.get("owner") != "manager"],
        "managerTasks": [{"title": t["title"], "status": effective_status(t)} for t in p["requirements"] if t.get("owner") == "manager"],
        "info": {"personal": info["personal"], "emergency": info["emergency"], "professional": info["professional"],
                 "pendingProfessional": p["pendingInfo"].get("professional"), "verified": p["infoVerified"], "meta": p["infoMeta"]},
        "sections": {s: section_fields(p, s) for s in ("personal", "emergency")} | (
            {"professional": section_fields(p, "professional")} if _find(p["requirements"], "professional") else {}),
        "documents": [_doc_json(d) for d in docs],
        "credentials": [dict(c, label=CREDENTIAL_LABELS.get(c.get("type"), "Credential")) for c in doc["credentials"] if c.get("personId") == p["id"]],
        "forms": {t["formKey"]: dict(forms.get(t["formKey"]) or {"title": t["title"], "body": "", "action": "acknowledge", "version": "1"},
                                     body=_fill((forms.get(t["formKey"]) or {}).get("body", ""), p, org_key),
                                     prefillFields=[{"key": k, "label": PROFILE_LABELS.get(k, k), **{x: (p.get("profile") or {}).get(k, {}).get(x) for x in ("value", "source", "verified")}}
                                                    for k in ((forms.get(t["formKey"]) or {}).get("prefill") or DEFAULT_FORM_PREFILL)])
                  for t in p["requirements"] if t.get("type") == "form" and t.get("formKey")},
        "trainings": {t["trainingKey"]: dict(libs.get(t["trainingKey"]) or {"title": t["title"], "description": "", "url": ""},
                                             description=_fill((libs.get(t["trainingKey"]) or {}).get("description", ""), p, org_key),
                                             record=training_record(doc, p, t, create=False))
                      for t in p["requirements"] if t.get("type") == "training" and t.get("trainingKey")},
        "docFields": {(t.get("docType") or "training_certificate"): staff_extraction.fields_for(t.get("docType") or "training_certificate", t.get("credentialType", ""))
                      for t in p["requirements"] if t.get("type") in ("document", "training")},
        **_portal_summary(db, org_key, doc, p),
        "profile": p.get("profile") or {}, "profileLabels": PROFILE_LABELS, "verifications": p.get("verifications") or {},
        "accessAreas": role_access(doc, p.get("role", ""))["areas"],
        "phone": p.get("phone", ""),
        "trainingRecords": [dict(r, status=training_status(r)) for r in doc["trainings"] if r.get("personId") == p["id"]],
    }


def _portal_summary(db: Session, org_key: str, doc: dict, p: dict) -> dict:
    """What the employee should do next, and where they stand, onboarding or long after it."""
    import staff_lifecycle
    today = _today()
    renewals = staff_lifecycle.renewal_items(doc, p, today)
    tasks = [dict(t, effectiveStatus=effective_status(t)) for t in p["requirements"] if t.get("owner") != "manager" and task_available(p, t)]
    def task_action(t, why):
        verb = {"document": "Upload", "form": "Complete", "training": "Complete", "info": "Fill in"}.get(t.get("type"), "Finish")
        if t.get("effectiveStatus") == "WAITING_ON_EMPLOYEE":
            verb = "Re-upload" if t.get("type") == "document" else "Fix"
        section = "credentials" if t.get("credentialType") else {"document": "documents", "form": "forms", "training": "training", "info": "info"}.get(t.get("type"), "tasks")
        return {"title": f"{verb} {t['title']}", "due": t.get("dueDate", ""), "why": why, "href": "#course" if t.get("trainingKey") == "althais_training" else f"#{section}", "key": t["key"]}
    def ren_action(r):
        return {"title": f"Upload your renewed {r['title']}", "due": r["expires"], "why": "Expired" if r["days"] < 0 else f"Expires in {r['days']} days",
                "href": f"#{r['section']}", "key": r["requirementKey"]}
    candidates = ([task_action(t, t.get("reviewNote") or "Needs a fix") for t in tasks if t["effectiveStatus"] == "WAITING_ON_EMPLOYEE"]
                  + [ren_action(r) for r in renewals if r["days"] < 0]
                  + [task_action(t, "Overdue") for t in tasks if t["effectiveStatus"] == "OVERDUE"]
                  + [ren_action(r) for r in renewals if 0 <= r["days"] <= 30]
                  + [task_action(t, "") for t in sorted((t for t in tasks if t["effectiveStatus"] in ("NOT_STARTED", "IN_PROGRESS")),
                                                        key=lambda t: t.get("dueDate") or "9999")]
                  + [ren_action(r) for r in renewals if r["days"] > 30])
    creds = [c for c in doc["credentials"] if c.get("personId") == p["id"] and c.get("status") == "verified"]
    active = [c for c in creds if not c.get("expires") or c["expires"] >= today.isoformat()]
    trains = [t for t in p["requirements"] if t.get("type") == "training"]
    train_open = [t for t in trains if t.get("status") != "COMPLETE"]
    ndocs = db.scalar(select(func.count(StaffDocument.id)).where(StaffDocument.org_key == org_key, StaffDocument.person_id == p["id"],
                                                                StaffDocument.superseded == 0, StaffDocument.status == "VERIFIED")) or 0
    upcoming = next((r for r in renewals if r["days"] >= 0), None)
    return {"renewals": renewals, "nextAction": candidates[0] if candidates else None,
            "status": {"onboardingComplete": p.get("lifecycle") == "ACTIVE", "credentialsActive": len(active), "credentialsTotal": len(creds),
                       "trainingOpen": len(train_open), "trainingTotal": len(trains), "documents": ndocs,
                       "upcoming": {"title": upcoming["title"], "days": upcoming["days"], "expires": upcoming["expires"]} if upcoming else None}}


@router.get("/api/portal/summary")
def portal_summary(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """For the name menu in the top bar: how many of your own items are open, and the next one."""
    if not has_staff_profile(user, db):
        return {"hasProfile": False}
    org_key, m, row, doc, p = portal_ctx(user, db)
    mine = [t for t in p["requirements"] if t.get("owner") != "manager" and t.get("status") not in ("COMPLETE", "NEEDS_REVIEW", "CLOSED")]
    st = [effective_status(t) for t in mine]
    summary = _portal_summary(db, org_key, doc, p)
    renew_now = [r for r in summary["renewals"] if r["days"] <= 30]
    return {"hasProfile": True, "open": len(mine) + len(renew_now), "overdue": st.count("OVERDUE") + sum(1 for r in renew_now if r["days"] < 0),
            "fix": st.count("WAITING_ON_EMPLOYEE"), "nextAction": summary["nextAction"], "lifecycle": p.get("lifecycle")}


def _portal_task(p: dict, key: str, kind: str):
    t = _find(p["requirements"], key)
    if not t or t.get("owner") == "manager" or t.get("type") != kind:
        raise HTTPException(status_code=404, detail="That isn’t one of your onboarding items.")
    if not task_available(p, t):
        raise HTTPException(status_code=400, detail="Finish the items this one depends on first.")
    return t


@router.put("/api/portal/info/{section}")
async def portal_info(section: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, m, row, doc, p = portal_ctx(user, db)
    body = await request.json()
    if section == "employment":   # clinic-controlled: the employee only confirms it
        t = _find(p["requirements"], "role_info")
        if t and t.get("status") != "COMPLETE":
            set_task(p, "role_info", "COMPLETE")
            audit(db, org_key, user, "info_confirmed", p, "info", "employment", "Confirmed employment information")
        refresh(p)
        save_staff(db, org_key, row, doc)
        return {"ok": True}
    if section not in ("personal", "emergency", "professional") or (section == "professional" and not _find(p["requirements"], "professional")):
        return _err("Unknown section.", 404)
    clean, errors = validate_fields(section_fields(p, section), body.get("fields") if isinstance(body.get("fields"), dict) else {},
                                    partial=bool(body.get("draft")))
    if errors:
        return _err("Check the highlighted fields.", fields=errors)
    task_key = {"personal": "personal", "emergency": "emergency_contact", "professional": "professional"}[section]
    t = _find(p["requirements"], task_key)
    changed = [k for k, v in clean.items() if v != (p["info"][section].get(k) or "")]
    if section == "professional" and p["infoVerified"].get("professional"):
        # verified details stay until the manager confirms the change
        if changed:
            p["pendingInfo"]["professional"] = clean
            if t:
                set_task(p, task_key, "NEEDS_REVIEW", note="")
    else:
        p["info"][section] = clean
        if section == "personal":
            if clean.get("legal_first") and clean.get("legal_last"):
                p["name"] = f"{clean['legal_first']} {clean['legal_last']}"
            if clean.get("phone"):
                p["phone"] = clean["phone"]
        if t and not body.get("draft"):
            set_task(p, task_key, "NEEDS_REVIEW" if t.get("review") else "COMPLETE", note="")
        elif t and t.get("status") == "NOT_STARTED":
            set_task(p, task_key, "IN_PROGRESS")
    for k, v in clean.items():
        if k in PROFILE_LABELS:
            set_profile(p, k, v, "Entered by you", False, by=_actor_name(user))
    if section == "professional" and clean.get("npi"):
        import credential_verification
        personal = p["info"]["personal"]
        names = {"first": [x for x in (personal.get("legal_first"), personal.get("preferred"), p.get("firstName")) if x],
                 "last": [x for x in (personal.get("legal_last"), p.get("lastName")) if x]}
        res = await asyncio.to_thread(credential_verification.verify_npi, clean["npi"], names)
        p.setdefault("verifications", {})["npi"] = res
        if res["status"] == "VERIFIED":
            set_profile(p, "npi", clean["npi"], res["source"], True)
        audit(db, org_key, None, "npi_checked", p, "verification", "npi", f"NPI checked against {res.get('source') or 'the NPI Registry'}: {res['status']}")
    p["infoMeta"][section] = {"updatedAt": _now().isoformat(), "updatedBy": _actor_name(user)}
    if changed:
        # names of the fields only: the values are personal information and stay out of the audit log
        audit(db, org_key, user, "info_changed", p, "info", section, f"Updated {section} information ({len(changed)} field{'s' if len(changed) != 1 else ''})")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


_SNIFF = [(b"%PDF", "application/pdf", ".pdf"), (b"\x89PNG", "image/png", ".png"), (b"\xff\xd8\xff", "image/jpeg", ".jpg")]


def _sniff(data: bytes, filename: str):
    for magic, ctype, ext in _SNIFF:
        if data.startswith(magic):
            return ctype
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return "image/webp"
    if data[4:12] in (b"ftypheic", b"ftypheix", b"ftypmif1", b"ftypmsf1"):
        return "image/heic"
    return None


def automation_policy(doc: dict) -> dict:
    """The clinic's document-automation settings (Staff > Onboarding Templates > Document Automation), sanitized."""
    raw = doc.get("clinic", {}).get("documentAutomation") or {}
    pol = dict(staff_doc_review.DEFAULT_POLICY)
    if isinstance(raw.get("enabled"), bool):
        pol["enabled"] = raw["enabled"]
    if isinstance(raw.get("autoApprove"), bool):
        pol["autoApprove"] = raw["autoApprove"]
    try:
        pol["minConfidence"] = min(0.99, max(0.8, float(raw.get("minConfidence", pol["minConfidence"]))))
    except (TypeError, ValueError):
        pass
    if raw.get("anomalyThreshold") in ("low", "medium", "high"):
        pol["anomalyThreshold"] = raw["anomalyThreshold"]
    if isinstance(raw.get("requireExternal"), list):
        pol["requireExternal"] = [str(k)[:64] for k in raw["requireExternal"]][:20]
    return pol


def _review_context(db: Session, org_key: str, doc: dict, p: dict, t: dict, sha: str, doc_id: int) -> "staff_doc_review.ReviewContext":
    info = p.get("info", {})
    personal = info.get("personal", {})
    firsts = [x for x in (personal.get("legal_first"), personal.get("preferred"), p.get("firstName")) if x]
    lasts = [x for x in (personal.get("legal_last"), p.get("lastName")) if x]
    if p.get("name"):
        parts = p["name"].split()
        firsts.append(parts[0])
        lasts.append(parts[-1])
    dup = db.scalar(select(StaffDocument.id).where(StaffDocument.org_key == org_key, StaffDocument.sha256 == sha,
                                                   StaffDocument.person_id != p["id"]).limit(1))
    rejected_same = db.scalar(select(StaffDocument.id).where(StaffDocument.org_key == org_key, StaffDocument.sha256 == sha,
                                                             StaffDocument.person_id == p["id"], StaffDocument.requirement_key == t["key"],
                                                             StaffDocument.status == "REJECTED", StaffDocument.id != doc_id).limit(1))
    lib = _find(trainings_for(doc), t.get("trainingKey")) or {}
    return staff_doc_review.ReviewContext(
        task=t, role=p.get("role", ""), staff_names={"first": firsts, "last": lasts}, staff_dob=personal.get("dob", ""),
        profile=info.get("professional", {}), existing_credential=_credential_for(doc, p, t["key"]),
        duplicate_file_owner=bool(dup), same_file_as_rejected=bool(rejected_same),
        training_valid_months=int(lib.get("validMonths") or 0), policy=automation_policy(doc),
        npi=(info.get("professional") or {}).get("npi") or profile_value(p, "npi"))


def _number_in_use(doc: dict, p: dict, fields: dict) -> bool:
    num = re.sub(r"[^A-Z0-9]", "", (fields.get("license_number") or fields.get("credential_number") or fields.get("dea_number") or "").upper())
    if not num:
        return False
    return any(c.get("personId") != p["id"] and re.sub(r"[^A-Z0-9]", "", (c.get("identifier") or "").upper()) == num
               for c in doc["credentials"])


def apply_review(db: Session, org_key: str, row, doc: dict, p: dict, d: StaffDocument, outcome, result, actor) -> None:
    """Record the automated review and act on its decision."""
    t = _find(p["requirements"], d.requirement_key)
    title = t["title"] if t else d.doc_type
    fields_json = {k: {"value": v, **outcome.field_meta.get(k, {})} for k, v in outcome.fields.items()}
    rv = StaffDocumentReview(org_key=org_key, person_id=p["id"], document_id=d.id, requirement_key=d.requirement_key,
                             detected_type=outcome.detected_type, type_confidence=f"{outcome.type_confidence:.2f}",
                             extracted_fields=_json.dumps(fields_json), validation_results=_json.dumps(outcome.checks),
                             external_verification=_json.dumps(outcome.external), issue_flags=_json.dumps(outcome.issues_json()),
                             decision=outcome.decision, reason_codes=_json.dumps(outcome.reason_codes),
                             employee_message=outcome.employee_message, manager_summary=_json.dumps(outcome.manager_summary),
                             analysis_status=result.status, processor_version=outcome.processor[:120])
    db.add(rv)
    db.flush()
    d.review_id, d.decision = rv.id, outcome.decision
    d.extraction = _json.dumps({"status": "extracted" if result.status == "analyzed" and outcome.fields else result.status,
                                "provider": result.processor, "note": result.note, "fields": fields_json, "at": _now().isoformat()})
    d.fields = _json.dumps(outcome.fields)
    d.expiration_date = outcome.fields.get("expiration_date", "") if staff_doc_review.parse_date(outcome.fields.get("expiration_date", "")) else ""
    codes = ", ".join(outcome.reason_codes)
    if outcome.decision == staff_doc_review.AUTO_APPROVED:
        approve_document(db, org_key, doc, p, d, outcome.fields, None, auto=True)
        c = _credential_for(doc, p, d.requirement_key)
        if c and outcome.external and outcome.external.get("status") not in (None, "NOT_RUN"):
            c["verification"] = outcome.external   # source, time, result and matched fields, as returned
    elif outcome.decision == staff_doc_review.ACTION_REQUIRED:
        send_back_document(db, org_key, p, d, outcome.employee_message, None, "automated")
        audit(db, org_key, None, "document_needs_correction", p, "document", d.id, f"{title} sent back automatically ({codes})")
    else:
        d.status = "NEEDS_REVIEW"
        hist = _json.loads(d.history or "[]")
        hist.append({"status": "NEEDS_REVIEW", "at": _now().isoformat(), "by": "Althais"})
        d.history = _json.dumps(hist)
        if t:
            set_task(p, t["key"], "NEEDS_REVIEW", note="")
        audit(db, org_key, None, "document_escalated", p, "document", d.id, f"{title} sent for manager review ({codes})")
    refresh(p)
    save_staff(db, org_key, row, doc)


@router.post("/api/portal/documents")
async def portal_upload(request: Request, requirement: str = Form(...), file: UploadFile = File(...),
                        user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Upload -> file checks -> automated review (staff_doc_review) -> verified, sent back to the employee, or sent to the manager."""
    org_key, m, row, doc, p = portal_ctx(user, db)
    t = _find(p["requirements"], requirement)
    if not t or t.get("owner") == "manager" or t.get("type") not in ("document", "training"):
        return _err("That isn’t one of your onboarding items.", 404)
    if not task_available(p, t):
        return _err("Finish the items this one depends on first.")
    if t.get("status") == "COMPLETE" and not t.get("credentialType"):
        return _err("This one is already complete.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return _err("That file is over 10 MB. Try a smaller scan or photo.")
    if not data:
        return _err("That file is empty.")
    ctype = _sniff(data, file.filename or "")   # by content, not the name or the browser's word for it
    if not ctype:
        return _err("Upload a PDF or a photo (JPG, PNG, WEBP or HEIC).")
    sha = hashlib.sha256(data).hexdigest()
    same = db.scalar(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.person_id == p["id"],
                                                 StaffDocument.requirement_key == t["key"], StaffDocument.sha256 == sha,
                                                 StaffDocument.superseded == 0, StaffDocument.status.in_(("VERIFIED", "NEEDS_REVIEW", "PROCESSING"))))
    if same:   # the identical file again: nothing new to check
        msg = ("This exact file is already on file and verified." if same.status == "VERIFIED"
               else "This exact file is already with your clinic for review.")
        return {"ok": True, "document": _doc_json(same), "decision": "ALREADY_ON_FILE", "message": msg,
                "fields": staff_extraction.fields_for(same.doc_type, t.get("credentialType", ""))}
    for old in db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.person_id == p["id"],
                                                      StaffDocument.requirement_key == t["key"], StaffDocument.superseded == 0)):
        if old.status != "VERIFIED":
            old.superseded = 1
    doc_type = t.get("docType") or ("training_certificate" if t.get("type") == "training" else "other")
    d = StaffDocument(org_key=org_key, person_id=p["id"], requirement_key=t["key"], doc_type=doc_type,
                      original_filename=(file.filename or "document")[:255], content_type=ctype, size=len(data), sha256=sha,
                      uploaded_by=user.id, uploaded_by_name=_actor_name(user), status="PROCESSING",
                      history=_json.dumps([{"status": "UPLOADED", "at": _now().isoformat()}, {"status": "PROCESSING", "at": _now().isoformat()}]))
    db.add(d)
    db.flush()
    db.add(StaffFile(document_id=d.id, data=data))
    audit(db, org_key, user, "document_uploaded", p, "document", d.id, f"Uploaded {t['title']}")
    db.commit()   # saved (and shown as Processing) before the analysis runs

    ctx = _review_context(db, org_key, doc, p, t, sha, d.id)
    expected = sorted(staff_doc_review.accepted_types(t, p.get("role", "")))[0]
    if ctx.policy.get("enabled", True):
        result = await asyncio.to_thread(staff_extraction.analyze, expected, d.original_filename, ctype, data)
    else:   # the clinic switched automated review off: nothing is sent to be read
        result = staff_extraction.AnalysisResult(status="unavailable", note="AUTOMATED_REVIEW_OFF")
    if result.status == "analyzed":
        probe = staff_doc_review.review(ctx, ctype, data, result)
        ctx.number_in_use_by_other = _number_in_use(doc, p, probe.fields)
    outcome = staff_doc_review.review(ctx, ctype, data, result)

    # the analysis took a while: act on the latest staff record so nobody's edits in the meantime are lost
    row, doc = load_staff(db, org_key)
    p = person_of(doc, p["id"])
    d = db.get(StaffDocument, d.id)
    if not p:
        return _err("Your staff record wasn’t found. Contact your manager.", 404)
    apply_review(db, org_key, row, doc, p, d, outcome, result, user)
    return {"ok": True, "document": _doc_json(d), "decision": outcome.decision, "message": outcome.employee_message,
            "fields": staff_extraction.fields_for(doc_type, t.get("credentialType", ""))}


@router.post("/api/portal/documents/{doc_id}/confirm")
async def portal_confirm(doc_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The employee checks (or types) the details from their document. Still waits for the manager."""
    org_key, m, row, doc, p = portal_ctx(user, db)
    d = db.scalar(select(StaffDocument).where(StaffDocument.id == doc_id, StaffDocument.org_key == org_key, StaffDocument.person_id == p["id"]))
    if not d:
        return _err("Document not found.", 404)
    if d.status != "NEEDS_REVIEW":
        return _err("This document has already been reviewed.")
    body = await request.json()
    t = _find(p["requirements"], d.requirement_key) or {}
    fields = [(k, l, k == "expiration_date" and d.doc_type in ("license", "bls", "dea"))
              for k, l in staff_extraction.fields_for(d.doc_type, t.get("credentialType", ""))]
    clean, errors = validate_fields(fields, body.get("fields") if isinstance(body.get("fields"), dict) else {})
    if errors:
        return _err("Check the highlighted fields.", fields=errors)
    d.fields, d.expiration_date, d.employee_confirmed = _json.dumps(clean), clean.get("expiration_date", ""), 1
    db.commit()
    return {"ok": True}


@router.get("/api/portal/documents/{doc_id}/file")
def portal_file(doc_id: int, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, m, row, doc, p = portal_ctx(user, db)
    d = db.scalar(select(StaffDocument).where(StaffDocument.id == doc_id, StaffDocument.org_key == org_key, StaffDocument.person_id == p["id"]))
    return _file_response(db, d)


@router.post("/api/portal/forms/{key}")
async def portal_form(key: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """open | complete. Signing needs the employee's typed full name."""
    org_key, m, row, doc, p = portal_ctx(user, db)
    t = _portal_task(p, key, "form")
    body = await request.json()
    form = _find(forms_for(doc), t.get("formKey")) or {"action": "acknowledge", "version": "1", "title": t["title"]}
    if body.get("action") == "open":
        t.setdefault("openedAt", _now().isoformat())
        if t.get("status") in ("NOT_STARTED", "WAITING_ON_EMPLOYEE"):
            set_task(p, key, "IN_PROGRESS")
    elif body.get("action") == "complete":
        if t.get("status") == "COMPLETE":
            return {"ok": True}
        signature = str(body.get("signature") or "").strip()[:200]
        if form.get("action") == "sign" and len(signature) < 3:
            return _err("Type your full name to sign.")
        if not body.get("agree"):
            return _err("Check the box to confirm.")
        t.setdefault("openedAt", _now().isoformat())
        t["version"], t["signature"] = str(form.get("version") or "1"), signature
        t["fileId"] = form.get("fileId")
        t["prefilled"] = {k: {"value": v.get("value"), "source": v.get("source"), "verified": v.get("verified")}
                          for k, v in (p.get("profile") or {}).items() if k in (form.get("prefill") or DEFAULT_FORM_PREFILL)}
        if signature:
            t["signedAt"] = _now().isoformat()
        set_task(p, key, "COMPLETE", note="")
        audit(db, org_key, user, "form_completed", p, "form", key,
              f"{'Signed' if signature else 'Acknowledged'} {form.get('title') or t['title']} (version {t['version']})")
    else:
        return _err("Unknown action.")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


@router.post("/api/portal/training/{key}")
async def portal_training(key: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """start | complete (with the employee's attestation that they finished it)."""
    org_key, m, row, doc, p = portal_ctx(user, db)
    t = _portal_task(p, key, "training")
    body = await request.json()
    r = training_record(doc, p, t)
    lib = _find(trainings_for(doc), t.get("trainingKey")) or {}
    if body.get("action") == "start":
        r.setdefault("startedAt", _today().isoformat())
        if t.get("status") in ("NOT_STARTED", "WAITING_ON_EMPLOYEE"):
            set_task(p, key, "IN_PROGRESS")
    elif body.get("action") == "complete":
        if t.get("trainingKey") == "althais_training":
            return _err("Althais Training is completed by passing the course's knowledge check.")
        if not body.get("attest"):
            return _err("Confirm that you completed the training.")
        r.setdefault("startedAt", _today().isoformat())
        r["completed"], r["version"] = _today().isoformat(), str(lib.get("version") or "1")
        months = int(lib.get("validMonths") or 0)
        r["expires"] = (_today() + timedelta(days=round(months * 30.44))).isoformat() if months else ""
        evidence = str(body.get("evidence") or "").strip()[:300]
        if evidence:
            r["certificate"] = evidence
        set_task(p, key, "COMPLETE", note="")
        audit(db, org_key, user, "training_completed", p, "training", r["id"], f"Completed {r['course']} (version {r['version']})")
    else:
        return _err("Unknown action.")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
#  Reminders
# ──────────────────────────────────────────────────────────────────────────
def run_reminders(db: Session, org_key: str, doc: dict, row=None) -> int:
    """Reminders, renewals and training cycles live in staff_lifecycle.py."""
    import staff_lifecycle
    return staff_lifecycle.run(db, org_key, doc, row)


def run_all_reminders() -> int:
    """For the periodic job in main.py: every clinic with a staff record."""
    from auth import SessionLocal
    total = 0
    with SessionLocal() as db:
        keys = [r for r in db.scalars(select(OrgSettings.org_key).where(OrgSettings.category == "staff")).all()]
        for key in keys:
            try:
                row, doc = load_staff(db, key)
                total += run_reminders(db, key, doc, row)
            except Exception as e:
                print(f"[REMINDERS] {key}: {e}")
                db.rollback()
    return total
