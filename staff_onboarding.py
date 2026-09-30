"""
staff_onboarding.py — Staff onboarding: invitations, the employee Staff Portal, and the workflow between them.

One staff record, two views of it. Every person lives in the clinic's staff document (org_settings, category
"staff" — the same record Team, Credentials, Training and Compliance read through static/js/staff-store.js).
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
from sqlalchemy import String, Integer, DateTime, Text, LargeBinary, UniqueConstraint, select
from sqlalchemy.orm import Mapped, mapped_column, Session

from auth import (
    Base, engine, get_db, require_user, current_user, User, OrgSettings, AREAS, _json,
    _doc_org_key, send_email, _email_html, RESEND_API_KEY, hash_password, verify_password, _set_session_cookie,
    ensure_product, ensure_area,
)
import staff_extraction

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


Base.metadata.create_all(engine)


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
    {"key": "althais_training", "title": "Althais Training", "category": "Role-Specific", "version": "1", "validMonths": 0, "url": "",
     "description": "Finding your way around Althais for your role."},
]

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


def templates_for(doc: dict) -> list:
    """The clinic's templates; every role on Staff > Roles without one gets the default for its name."""
    have = {t.get("role") for t in doc["onboardingTemplates"]}
    return doc["onboardingTemplates"] + [default_template(r["name"]) for r in doc["roles"] if r.get("name") not in have]


def forms_for(doc: dict) -> list:
    have = {f.get("key") for f in doc["onboardingForms"]}
    return doc["onboardingForms"] + [f for f in DEFAULT_FORMS if f["key"] not in have]


def trainings_for(doc: dict) -> list:
    have = {t.get("key") for t in doc["onboardingTrainings"]}
    return doc["onboardingTrainings"] + [t for t in DEFAULT_TRAININGS if t["key"] not in have]


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
def _anchor(p: dict) -> dt.date:
    for v in (p.get("start"), (p.get("createdAt") or "")[:10]):
        try:
            return dt.date.fromisoformat(v)
        except Exception:
            continue
    return _today()


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
        return "Invite expired — resend" if invite.get("state") == "expired" else "Waiting for invite to be accepted"
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
    emailed = send_email(p["email"], f"You're invited to join {_clinic_name(org_key)} on Althais", _email_html(
        f"You're invited to join {clinic} on Althais",
        f"{_html.escape(by.full_name or by.email)} invited you to complete your onboarding with {clinic}.<br><br>{details}",
        link, "Accept Invitation",
        note=f"This invitation is for {_html.escape(p['email'])}, expires in {INVITE_TTL_DAYS} days and can only be used once."))
    return {"invitation": inv, "emailed": emailed, "link": None if emailed else link}


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
_DATE_FIELDS = {"dob", "license_expiration", "dea_expiration", "issue_date", "expiration_date"}


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
    c = _credential_for(doc, p, t["key"])
    if not c:
        c = {"id": _uid("c"), "personId": p["id"], "fromRequirement": t["key"]}
        doc["credentials"].append(c)
    c.update({"type": t.get("credentialType") or "other", "identifier": fields.get("identifier", ""),
              "issuingAuthority": fields.get("issuing_authority", ""), "state": fields.get("state", ""),
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
    """The signed-in employee's own staff record in the clinic they're signed in to — never an id from the request."""
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
    org_key, _, doc = manager_ctx(user, db)
    return {"catalog": {k: _item_from_catalog(k) for k in CATALOG}, "templates": templates_for(doc), "forms": forms_for(doc),
            "trainings": trainings_for(doc), "roles": [r["name"] for r in doc["roles"]], "lifecycle": LIFECYCLE_LABELS,
            "roleAccess": {r["name"]: role_access(doc, r["name"]) for r in doc["roles"]},
            "clinic": _clinic_name(org_key), "docFields": {k: v for k, v in staff_extraction.DOC_FIELDS.items()},
            "inviteDays": INVITE_TTL_DAYS, "generic": dict(default_template("Custom"), name="Standard")}


def _warn_days(db: Session, org_key: str) -> int:
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == org_key, OrgSettings.category == "team_prefs"))
    try:
        n = int((_json.loads(row.data) if row else {}).get("credential_warn_days") or 0)
    except Exception:
        n = 0
    return n if n > 0 else CREDENTIAL_WARN_DAYS


def attention_items(db: Session, org_key: str, doc: dict, invites: dict) -> list:
    """Exceptions a manager should act on, most urgent first — not a list of everything that's fine."""
    items = []
    docs = db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.status == "NEEDS_REVIEW",
                                                  StaffDocument.superseded == 0)).all()
    people = {p["id"]: p for p in doc["people"]}
    for d in docs:
        p = people.get(d.person_id)
        if not p or p.get("lifecycle") == "OFFBOARDED":
            continue
        t = _find(p["requirements"], d.requirement_key) or {"title": d.doc_type}
        items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "review_document", "ref": d.id,
                      "text": f"{t['title']} awaiting review"})
    for p in doc["people"]:
        lc = p.get("lifecycle")
        if lc == "OFFBOARDED":
            continue
        if p.get("pendingInfo", {}).get("professional") or (_find(p["requirements"], "professional") or {}).get("status") == "NEEDS_REVIEW":
            items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "review_info", "ref": "professional",
                          "text": "Professional information awaiting confirmation"})
        if lc == "PENDING_REVIEW" and not any(t.get("status") == "NEEDS_REVIEW" for t in p["requirements"]):
            items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 1, "action": "review_onboarding", "ref": "",
                          "text": "Employee requirements complete — ready for your review"})
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
        elif days <= warn:
            items.append({"personId": p["id"], "name": p.get("name", ""), "severity": 2, "action": "open", "ref": c["id"],
                          "text": f"{label} expires {_fmt_date(c['expires'])}"})
    return sorted(items, key=lambda i: i["severity"])


@router.get("/api/staff/onboarding/overview")
def onboarding_overview(user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc = manager_ctx(user, db)
    run_reminders(db, org_key, doc)
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
    return {"stats": {"total": sum(1 for p in doc["people"] if p["lifecycle"] != "OFFBOARDED"),
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
    p["requirements"] = build_tasks(p, _clean_items(items, role) if isinstance(items, list) and items else tpl["items"])
    if p["location"] and not any(l.get("name") == p["location"] for l in doc["clinic"].setdefault("locations", [])):
        doc["clinic"]["locations"].append({"name": p["location"], "address": ""})
    for t in p["requirements"]:
        if t.get("type") == "training":
            training_record(doc, p, t)
    doc["people"].append(p)
    audit(db, org_key, user, "staff_added", p, "person", p["id"], f"Added as {role}")
    out = {"ok": True, "personId": p["id"]}
    if body.get("sendInvite"):
        res = send_invite(db, request, org_key, p, user)
        p["lifecycle"] = "INVITED"
        audit(db, org_key, user, "invite_sent", p, "invitation", "", f"Onboarding invitation sent to {email}")
        out.update(emailed=res["emailed"], inviteLink=res["link"])
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
    return {"ok": True, "emailed": res["emailed"], "inviteLink": res["link"], "invite": _invite_json(res["invitation"])}


@router.post("/api/staff/onboarding/people/{pid}/invite/cancel")
def cancel_invite(pid: str, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p = _manager_person(user, db, pid)
    if not _revoke_pending(db, org_key, pid):
        return _err("There’s no open invitation to cancel.")
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
        p["requirements"] = build_tasks(p, tpl["items"])
    for inv in db.scalars(select(StaffInvitation).where(StaffInvitation.org_key == org_key, StaffInvitation.person_id == pid,
                                                         StaffInvitation.status == "pending")):
        inv.role_name = role
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
            p["email"] = email
            if _revoke_pending(db, org_key, pid):
                p["lifecycle"] = "DRAFT"   # the old invitation went to the old address
            changed.append("email")
    if changed:
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
            "verifiedAt": _iso(d.verified_at), "verifiedBy": d.verified_by, "reviewNote": d.review_note, "superseded": bool(d.superseded)}


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
                                            "start", "supervisor", "lifecycle", "notes", "info", "pendingInfo", "infoVerified", "infoMeta")},
            "label": LIFECYCLE_LABELS[p["lifecycle"]], "progress": progress(p), "tasks": _tasks_json(p),
            "documents": [_doc_json(d) for d in docs], "invite": _invite_json(inv),
            "sections": {s: section_fields(p, s) for s in ("personal", "emergency", "professional")},
            "credentials": [c for c in doc["credentials"] if c.get("personId") == pid],
            "trainings": [t for t in doc["trainings"] if t.get("personId") == pid],
            "access": role_access(doc, p.get("role", "")),
            "membership": {"status": m.status, "appAccess": bool(m.app_access), "role": m.role} if m else None,
            "events": [{"at": _iso(e.created_at), "by": e.actor_name, "action": e.action, "detail": e.detail} for e in events]}


@router.post("/api/staff/onboarding/documents/{doc_id}/review")
async def review_document(doc_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """approve (Confirm, optionally with edited details), reject, or request_correction — the latter two need a reason."""
    org_key, row, doc = manager_ctx(user, db)
    d = db.scalar(select(StaffDocument).where(StaffDocument.id == doc_id, StaffDocument.org_key == org_key))
    if not d:
        return _err("Document not found.", 404)
    p = person_of(doc, d.person_id)
    if not p:
        return _err("Staff member not found.", 404)
    body = await request.json()
    action, reason = body.get("action"), str(body.get("reason") or "").strip()[:1000]
    t = _find(p["requirements"], d.requirement_key)
    title = t["title"] if t else d.doc_type
    hist = _json.loads(d.history or "[]")
    if action == "approve":
        clean, errors = validate_fields([(k, l, False) for k, l in staff_extraction.fields_for(d.doc_type)],
                                        body.get("fields") if isinstance(body.get("fields"), dict) else _json.loads(d.fields or "{}"), partial=True)
        if errors:
            return _err("Check the highlighted details.", fields=errors)
        d.fields = _json.dumps(clean)
        d.expiration_date = clean.get("expiration_date", "")
        d.status, d.verified_at, d.verified_by, d.review_note = "VERIFIED", _now(), _actor_name(user), ""
        hist.append({"status": "VERIFIED", "at": _now().isoformat(), "by": _actor_name(user)})
        for old in db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.person_id == d.person_id,
                                                          StaffDocument.requirement_key == d.requirement_key, StaffDocument.id != d.id)):
            old.superseded = 1   # a renewal replaces the earlier verified copy
        if t:
            set_task(p, t["key"], "COMPLETE", _actor_name(user), note="")
            if t.get("credentialType"):
                c = record_credential(doc, p, t, d, clean, _actor_name(user))
                audit(db, org_key, user, "credential_verified", p, "credential", c["id"], f"{title} verified")
            else:
                audit(db, org_key, user, "document_verified", p, "document", d.id, f"{title} verified")
    elif action in ("reject", "request_correction"):
        if not reason:
            return _err("Tell them what’s wrong so they can fix it.")
        d.status, d.review_note = "REJECTED", reason
        hist.append({"status": "REJECTED", "at": _now().isoformat(), "by": _actor_name(user), "action": action})
        if t:
            set_task(p, t["key"], "WAITING_ON_EMPLOYEE", note=reason)
        verb = "rejected" if action == "reject" else "sent back for correction"
        audit(db, org_key, user, "document_rejected", p, "document", d.id, f"{title} {verb}: {reason}")
    else:
        return _err("Unknown review action.")
    d.history = _json.dumps(hist)
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True}


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
    themselves (e.g. a paper form) — both are audited."""
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
        audit(db, org_key, user, "task_completed", p, "task", key, f"{t['title']} marked complete" + (f" — {note}" if note else ""))
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


@router.post("/api/staff/onboarding/people/{pid}/status")
async def change_status(pid: str, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """suspend | offboard | reactivate. Suspending or offboarding takes away their access to this clinic right away."""
    org_key, row, doc, p = _manager_person(user, db, pid)
    body = await request.json()
    action = body.get("action")
    m = _staff_membership(db, org_key, p)
    if m and m.user_id == user.id:
        return _err("You can’t change your own access here.")
    if action in ("suspend", "offboard"):
        if action == "offboard":
            _revoke_pending(db, org_key, pid)
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
    return {"clinic": _clinic_name(inv.org_key), "role": inv.role_name, "start": p.get("start", ""), "email": inv.email,
            "name": p.get("name", ""), "invitedBy": inv.invited_by_name, "expiresAt": _iso(inv.expires_at),
            "accountExists": bool(db.scalar(select(User.id).where(User.email == inv.email))),
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
            return _err("That password isn’t right for this account.", 401)
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
    resp = JSONResponse({"ok": True, "redirect": "/portal"})
    _set_session_cookie(resp, user.id)
    return resp


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
    run_reminders(db, org_key, doc)
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
                                     body=_fill((forms.get(t["formKey"]) or {}).get("body", ""), p, org_key))
                  for t in p["requirements"] if t.get("type") == "form" and t.get("formKey")},
        "trainings": {t["trainingKey"]: dict(libs.get(t["trainingKey"]) or {"title": t["title"], "description": "", "url": ""},
                                             description=_fill((libs.get(t["trainingKey"]) or {}).get("description", ""), p, org_key),
                                             record=training_record(doc, p, t, create=False))
                      for t in p["requirements"] if t.get("type") == "training" and t.get("trainingKey")},
        "docFields": {t["docType"]: staff_extraction.fields_for(t["docType"]) for t in p["requirements"] if t.get("type") == "document"},
    }


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


@router.post("/api/portal/documents")
async def portal_upload(request: Request, requirement: str = Form(...), file: UploadFile = File(...),
                        user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, m, row, doc, p = portal_ctx(user, db)
    t = _portal_task(p, requirement, "document")
    if t.get("status") in ("COMPLETE",) and t.get("credentialType") is None:
        return _err("This document is already verified.")
    data = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(data) > MAX_UPLOAD_BYTES:
        return _err("That file is over 10 MB. Try a smaller scan or photo.")
    if not data:
        return _err("That file is empty.")
    ctype = _sniff(data, file.filename or "")   # by content, not the name or the browser's word for it
    if not ctype:
        return _err("Upload a PDF or a photo (JPG, PNG, WEBP or HEIC).")
    for old in db.scalars(select(StaffDocument).where(StaffDocument.org_key == org_key, StaffDocument.person_id == p["id"],
                                                      StaffDocument.requirement_key == t["key"], StaffDocument.superseded == 0)):
        if old.status != "VERIFIED":
            old.superseded = 1
    d = StaffDocument(org_key=org_key, person_id=p["id"], requirement_key=t["key"], doc_type=t.get("docType") or "other",
                      original_filename=(file.filename or "document")[:255], content_type=ctype, size=len(data),
                      uploaded_by=user.id, uploaded_by_name=_actor_name(user), status="UPLOADED",
                      history=_json.dumps([{"status": "UPLOADED", "at": _now().isoformat()}]))
    db.add(d)
    db.flush()
    db.add(StaffFile(document_id=d.id, data=data))
    hist = _json.loads(d.history)
    hist.append({"status": "PROCESSING", "at": _now().isoformat()})
    result = staff_extraction.extract(d.doc_type, d.original_filename, ctype, data)
    d.extraction = _json.dumps({"status": result.status, "provider": result.provider, "note": result.note, "fields": result.fields,
                                "at": _now().isoformat()})
    d.fields = _json.dumps({k: v.get("value", "") for k, v in result.fields.items()})
    d.status = "NEEDS_REVIEW"   # extracted or not, a person confirms it before it counts
    hist.append({"status": "NEEDS_REVIEW", "at": _now().isoformat()})
    d.history = _json.dumps(hist)
    set_task(p, t["key"], "NEEDS_REVIEW", note="")
    audit(db, org_key, user, "document_uploaded", p, "document", d.id, f"Uploaded {t['title']}")
    refresh(p)
    save_staff(db, org_key, row, doc)
    return {"ok": True, "document": _doc_json(d), "fields": staff_extraction.fields_for(d.doc_type)}


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
    fields = [(k, l, k == "expiration_date" and d.doc_type in ("license", "bls", "dea")) for k, l in staff_extraction.fields_for(d.doc_type)]
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
def _notify_once(db: Session, org_key: str, p: dict, ref: str, kind: str, due: str, subject: str, body: str) -> bool:
    exists = db.scalar(select(StaffNotification.id).where(StaffNotification.org_key == org_key, StaffNotification.person_id == p["id"],
                                                          StaffNotification.ref == ref, StaffNotification.kind == kind,
                                                          StaffNotification.due == due))
    if exists or not p.get("email"):
        return False
    sent = send_email(p["email"], subject, _email_html(subject, body, f"{(os.environ.get('APP_URL') or 'https://app.althais.com').rstrip('/')}/portal",
                                                       "Open Staff Portal", note="You're getting this because you're onboarding with your clinic on Althais."))
    db.add(StaffNotification(org_key=org_key, person_id=p["id"], ref=ref, kind=kind, due=due, recipient=p["email"], sent=1 if sent else 0))
    return True


def run_reminders(db: Session, org_key: str, doc: dict) -> int:
    """Send the reminders that are due today and haven't gone out yet. Safe to call as often as you like."""
    n = 0
    clinic = _html.escape(_clinic_name(org_key))
    today = _today()
    for p in doc["people"]:
        lc = p.get("lifecycle")
        if lc not in ("INVITE_ACCEPTED", "ONBOARDING", "PENDING_REVIEW", "ACTIVE") or not p.get("userId"):
            continue
        for t in p["requirements"]:
            if t.get("owner") == "manager" or t.get("status") in ("COMPLETE", "NEEDS_REVIEW") or not t.get("dueDate"):
                continue
            try:
                days = (dt.date.fromisoformat(t["dueDate"]) - today).days
            except ValueError:
                continue
            before = t.get("reminderDaysBefore")
            title, when = _html.escape(t["title"]), _fmt_date(t["dueDate"])
            if days < 0:
                kind, subject, body = "overdue", f"Overdue: {t['title']}", f"{title} for {clinic} was due {when}. Please finish it as soon as you can."
            elif days == 0:
                kind, subject, body = "due_today", f"Due today: {t['title']}", f"{title} for {clinic} is due today."
            elif before is not None and 0 < days <= int(before):
                kind, subject, body = "due_soon", f"Reminder: {t['title']} is due {when}", f"{title} for {clinic} is due {when}."
            else:
                continue
            n += _notify_once(db, org_key, p, t["key"], kind, t["dueDate"], subject, body)
    warn = _warn_days(db, org_key)
    for c in doc["credentials"]:
        p = person_of(doc, c.get("personId"))
        if not p or p.get("lifecycle") != "ACTIVE" or not c.get("expires"):
            continue
        try:
            days = (dt.date.fromisoformat(c["expires"]) - today).days
        except ValueError:
            continue
        label = CREDENTIAL_LABELS.get(c.get("type"), "credential")
        if days < 0:
            n += _notify_once(db, org_key, p, c["id"], "credential_expired", c["expires"], f"Your {label} has expired",
                              f"Your {label} on file with {clinic} expired {_fmt_date(c['expires'])}. Upload your renewed one in the Staff Portal.")
        elif days <= warn:
            n += _notify_once(db, org_key, p, c["id"], "credential_expiring", c["expires"], f"Your {label} expires {_fmt_date(c['expires'])}",
                              f"Your {label} on file with {clinic} expires {_fmt_date(c['expires'])}. Start your renewal now.")
    if n:
        db.commit()
    return n


def run_all_reminders() -> int:
    """For the periodic job in main.py: every clinic with a staff record."""
    from auth import SessionLocal
    total = 0
    with SessionLocal() as db:
        keys = [r for r in db.scalars(select(OrgSettings.org_key).where(OrgSettings.category == "staff")).all()]
        for key in keys:
            try:
                _, doc = load_staff(db, key)
                total += run_reminders(db, key, doc)
            except Exception as e:
                print(f"[REMINDERS] {key}: {e}")
                db.rollback()
    return total
