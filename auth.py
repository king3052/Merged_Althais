"""
auth.py — Self-hosted authentication for Althais.

Provides:
  • A User model (SQLAlchemy) with email + hashed password + organization.
  • Password hashing (bcrypt) — raw passwords are never stored.
  • Session handling via a signed JWT kept in an HttpOnly cookie.
  • Two dependencies for protecting routes:
        current_user  -> returns the User or None   (use for HTML pages)
        require_user  -> returns the User or 401     (use for /api routes)
  • An APIRouter with /register, /login, and /logout endpoints.

Wire it into main.py with:
    from auth import router as auth_router, current_user, require_user
    app.include_router(auth_router)
"""

import os
import datetime as dt
from datetime import timezone, timedelta

from fastapi import APIRouter, Depends, Request, Form, status
from fastapi.responses import JSONResponse
from sqlalchemy import create_engine, String, Integer, DateTime, Text, UniqueConstraint, select, text
from sqlalchemy.orm import declarative_base, sessionmaker, Mapped, mapped_column, Session
import bcrypt
import jwt  # PyJWT

# ──────────────────────────────────────────────────────────────────────────
#  Configuration (all overridable via environment variables)
# ──────────────────────────────────────────────────────────────────────────
# Local dev defaults to a SQLite file. In production set DATABASE_URL to your
# Railway Postgres connection string so accounts survive redeploys.
DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///./althais.db")

# Secret used to sign session tokens. MUST be set to a long random value in
# production — if it leaks or changes, all sessions are invalidated.
SECRET_KEY = os.environ.get("SECRET_KEY", "dev-only-insecure-change-me")

COOKIE_NAME = "althais_session"
TOKEN_TTL_HOURS = 12
# Set COOKIE_SECURE=1 in production so the cookie is only sent over HTTPS.
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "0") == "1"

# ──────────────────────────────────────────────────────────────────────────
#  Database setup
# ──────────────────────────────────────────────────────────────────────────
_connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=_connect_args)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)
Base = declarative_base()


import secrets

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), default="")
    organization: Mapped[str] = mapped_column(String(255), default="")
    # Role controls what actions a user can perform within their organization.
    # admin   = full access, manage users, billing, settings
    # biller  = create/edit/submit claims, edit patient billing info
    # provider = view patients + claims, limited editing
    # viewer  = read-only access to patients and claim status
    role: Mapped[str] = mapped_column(String(32), default="admin")
    # Links this login to the provider identity used throughout appointments/
    # claims data (e.g. "Dr. R. Patel") — without this, "what's my schedule"
    # has no way to mean anything more specific than "everyone's schedule".
    # Blank for accounts that aren't a specific provider (admins, office
    # managers) — Althea falls back to unfiltered results when this is empty.
    provider_name: Mapped[str] = mapped_column(String(255), default="")
    # Set to 1 after the user completes the first-login onboarding screen
    # (sets their display name and provider identity). Used to redirect
    # invited users through setup before they land in the main app.
    onboarding_complete: Mapped[int] = mapped_column(Integer, default=0)
    login_count: Mapped[int] = mapped_column(Integer, default=0)
    claims_submitted: Mapped[int] = mapped_column(Integer, default=0)
    last_login: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)
    email_verified: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )


class PasswordResetToken(Base):
    __tablename__ = "password_reset_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )


class EmailVerificationToken(Base):
    __tablename__ = "email_verification_tokens"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    token: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    expires_at: Mapped[dt.datetime] = mapped_column(DateTime)
    used: Mapped[bool] = mapped_column(Integer, default=0)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )


def _org_namespace(user) -> str:
    """Return a stable, filesystem-safe org key for scoping DB rows to an org."""
    safe = re.sub(r'[^a-z0-9]+', '_', (user.organization or 'default').lower()).strip('_')
    return f"org_{safe or 'default'}"


class OrgPatient(Base):
    """Server-side patient records scoped to an org — shared by web + desktop clients.
    Core columns (mrn/name/dob/sex/payer/provider) support simple lookups and
    the plain-CSV import path; `data` stores the full rich patient object the
    dashboard actually works with (insurance meta, allergies, problems, balance,
    etc.) as JSON, so nothing the UI tracks is lost on a round trip."""
    __tablename__ = "org_patients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    mrn: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    dob: Mapped[str] = mapped_column(String(20), default="")
    sex: Mapped[str] = mapped_column(String(4), default="")
    payer: Mapped[str] = mapped_column(String(128), default="")
    provider: Mapped[str] = mapped_column(String(255), default="")
    data: Mapped[str] = mapped_column(Text, default="{}")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc),
        onupdate=lambda: dt.datetime.now(timezone.utc),
    )
    __table_args__ = (UniqueConstraint("org_key", "mrn", name="uq_org_patient_mrn"),)


class OrgClaim(Base):
    """Server-side claims scoped to an org — shared by web + desktop clients."""
    __tablename__ = "org_claims"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    claim_id: Mapped[str] = mapped_column(String(64), default="")
    patient_name: Mapped[str] = mapped_column(String(255), default="")
    mrn: Mapped[str] = mapped_column(String(64), default="")
    payer: Mapped[str] = mapped_column(String(128), default="")
    codes: Mapped[str] = mapped_column(String(8192), default="[]")
    note: Mapped[str] = mapped_column(String(32768), default="")
    amount: Mapped[float] = mapped_column(Integer, default=0)
    status: Mapped[str] = mapped_column(String(32), default="Draft")
    score: Mapped[int] = mapped_column(Integer, nullable=True)
    flags: Mapped[str] = mapped_column(String(4096), default="[]")
    appeal_letter: Mapped[str] = mapped_column(String(32768), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )


class OrgSettings(Base):
    """
    Generic per-organization settings storage, one row per (org, category).
    Each category (e.g. 'ai', 'billing', 'notifications', 'practice',
    'security', 'appearance') stores its whole settings object as a JSON
    blob in `data`. This keeps every settings tab on one flexible schema
    instead of needing a new table per tab — new toggles/fields just add
    new keys to the JSON, no migration needed.
    """
    __tablename__ = "org_settings"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    category: Mapped[str] = mapped_column(String(64), index=True)
    data: Mapped[str] = mapped_column(Text, default="{}")
    updated_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc),
        onupdate=lambda: dt.datetime.now(timezone.utc),
    )
    __table_args__ = (UniqueConstraint("org_key", "category", name="uq_org_settings_org_category"),)


Base.metadata.create_all(engine)

# create_all() only creates tables that don't exist yet — it does NOT add
# new columns to a table that's already there. Since `users` already exists
# on any already-deployed database, provider_name needs an explicit ALTER
# TABLE to actually show up. Wrapped in try/except and safe to run every
# startup: it fails harmlessly (duplicate column) once already applied.
try:
    with engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE users ADD COLUMN provider_name VARCHAR(255) DEFAULT ''"))
        _conn.commit()
except Exception:
    pass  # column already exists — this is expected on every run after the first

try:
    with engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE users ADD COLUMN onboarding_complete INTEGER DEFAULT 0"))
        # Existing users who already have a session are considered complete —
        # only newly invited users (created after this migration) should see
        # the onboarding screen. Mark everyone currently in the DB as done.
        _conn.execute(text("UPDATE users SET onboarding_complete = 1"))
        _conn.commit()
except Exception:
    pass  # column already exists

# org_patients existed before the `data`/`updated_at` columns were added for
# full-object sync (allergies, insurance meta, problems, etc.) — same
# safe-ALTER pattern as above, harmless once already applied.
try:
    with engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE org_patients ADD COLUMN data TEXT DEFAULT '{}'"))
        _conn.commit()
except Exception:
    pass  # column already exists

try:
    with engine.connect() as _conn:
        _conn.execute(text("ALTER TABLE org_patients ADD COLUMN updated_at TIMESTAMP"))
        _conn.commit()
except Exception:
    pass  # column already exists


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


# ──────────────────────────────────────────────────────────────────────────
#  Password hashing
# ──────────────────────────────────────────────────────────────────────────
pwd_context = None  # using the bcrypt library directly (see below)

# bcrypt has a hard 72-byte limit on the input; we truncate to stay within it.
def hash_password(raw: str) -> str:
    pw = (raw or "").encode("utf-8")[:72]
    return bcrypt.hashpw(pw, bcrypt.gensalt()).decode("utf-8")


def verify_password(raw: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw((raw or "").encode("utf-8")[:72], hashed.encode("utf-8"))
    except Exception:
        return False


# ──────────────────────────────────────────────────────────────────────────
#  Session tokens (signed JWT in an HttpOnly cookie)
# ──────────────────────────────────────────────────────────────────────────
def create_token(user_id: int) -> str:
    now = dt.datetime.now(timezone.utc)
    payload = {"sub": str(user_id), "iat": now, "exp": now + timedelta(hours=TOKEN_TTL_HOURS)}
    return jwt.encode(payload, SECRET_KEY, algorithm="HS256")


def decode_token(token: str):
    try:
        return jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def _set_session_cookie(resp: JSONResponse, user_id: int) -> None:
    resp.set_cookie(
        key=COOKIE_NAME,
        value=create_token(user_id),
        httponly=True,          # JavaScript can't read it -> protects against XSS theft
        samesite="lax",         # sent on top-level navigations, blocks most CSRF
        secure=COOKIE_SECURE,   # HTTPS-only in production
        max_age=TOKEN_TTL_HOURS * 3600,
        path="/",
    )


# ──────────────────────────────────────────────────────────────────────────
#  Dependencies
# ──────────────────────────────────────────────────────────────────────────
def current_user(request: Request, db: Session = Depends(get_db)):
    """Return the logged-in User, or None. Use for HTML page routes so you can
    redirect to /login yourself."""
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return None
    data = decode_token(token)
    if not data:
        return None
    try:
        return db.get(User, int(data["sub"]))
    except (KeyError, ValueError, TypeError):
        return None


def require_user(request: Request, db: Session = Depends(get_db)) -> User:
    """Return the logged-in User, or raise 401. Use for /api/* routes."""
    user = current_user(request, db)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Not authenticated")
    return user


# ── Role-based access control ──────────────────────────────────────────────
ROLE_LEVELS = {"viewer": 0, "provider": 1, "biller": 2, "admin": 3}


def _role_level(user: User) -> int:
    return ROLE_LEVELS.get(user.role or "viewer", 0)


def require_role(min_role: str):
    """Factory: returns a dependency that requires at least `min_role`."""
    def _check(user: User = Depends(require_user)):
        if _role_level(user) < ROLE_LEVELS.get(min_role, 0):
            from fastapi import HTTPException
            raise HTTPException(
                status_code=403,
                detail=f"Your role ({user.role}) does not have permission for this action."
            )
        return user
    return _check


# Convenience shorthands
require_biller   = require_role("biller")    # billers, admins
require_admin_role = require_role("admin")   # org admins only (not the super-admin)


# ──────────────────────────────────────────────────────────────────────────
#  Email sending via Resend
# ──────────────────────────────────────────────────────────────────────────
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
APP_URL = os.environ.get("APP_URL", "https://app.althais.com")
FROM_EMAIL = "noreply@app.althais.com"


def send_email(to: str, subject: str, html: str) -> bool:
    """Send an email via Resend. Returns True on success."""
    if not RESEND_API_KEY:
        print(f"[EMAIL] No RESEND_API_KEY set — would send to {to}: {subject}")
        return False
    try:
        import requests as _requests
        r = _requests.post(
            "https://api.resend.com/emails",
            json={"from": f"Althais <{FROM_EMAIL}>", "to": [to], "subject": subject, "html": html},
            headers={"Authorization": f"Bearer {RESEND_API_KEY}"},
            timeout=10,
        )
        if r.status_code == 200:
            return True
        print(f"[EMAIL] Send failed: {r.status_code} {r.text}")
        return False
    except Exception as e:
        print(f"[EMAIL] Send failed: {e}")
        return False


def _email_html(title: str, body: str, cta_url: str, cta_text: str) -> str:
    return f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#f4f5f7;font-family:Inter,sans-serif">
<table width="100%" cellpadding="0" cellspacing="0"><tr><td align="center" style="padding:40px 16px">
<table width="480" cellpadding="0" cellspacing="0" style="background:#fff;border-radius:8px;border:1px solid #e4e6eb">
<tr><td style="padding:32px 40px;border-bottom:1px solid #f0f0f0">
  <span style="font-size:13px;font-weight:700;letter-spacing:0.2em;color:#0d5bd7;text-transform:uppercase">ALTHAIS</span>
</td></tr>
<tr><td style="padding:32px 40px">
  <h1 style="font-size:20px;font-weight:600;color:#0f1116;margin:0 0 12px">{title}</h1>
  <p style="font-size:14px;color:#6b7280;line-height:1.6;margin:0 0 28px">{body}</p>
  <a href="{cta_url}" style="display:inline-block;background:#0d5bd7;color:#fff;text-decoration:none;font-size:13px;font-weight:600;letter-spacing:0.05em;padding:14px 32px;border-radius:6px">{cta_text}</a>
  <p style="font-size:12px;color:#9ca3af;margin:24px 0 0">Or copy this link: <a href="{cta_url}" style="color:#0d5bd7">{cta_url}</a></p>
  <p style="font-size:12px;color:#9ca3af;margin:8px 0 0">This link expires in 1 hour and can only be used once.</p>
</td></tr>
<tr><td style="padding:20px 40px;border-top:1px solid #f0f0f0">
  <p style="font-size:11px;color:#d1d5db;margin:0">© 2026 Althais Health, Inc. · If you didn't request this, ignore this email.</p>
</td></tr>
</table></td></tr></table>
</body></html>"""


# ──────────────────────────────────────────────────────────────────────────
#  Routes
# ──────────────────────────────────────────────────────────────────────────
router = APIRouter()


@router.post("/register")
def register(
    email: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(""),
    organization: str = Form(""),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()
    prefs = admin_settings(db)   # Althais admin console > Settings
    if not prefs["signups_open"]:
        return JSONResponse({"error": "New sign-ups are paused right now. Contact Althais to get access."}, status_code=403)
    if not email or "@" not in email:
        return JSONResponse({"error": "Enter a valid email address."}, status_code=400)
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters."}, status_code=400)
    if db.scalar(select(User).where(User.email == email)):
        return JSONResponse({"error": "An account with that email already exists."}, status_code=400)

    org = organization.strip()
    new_clinic = not org or not db.scalar(select(User).where(User.organization == org))
    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        organization=org,
        email_verified=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # A brand-new clinic starts on the plan chosen in the admin console (joining an existing clinic keeps its plan)
    if new_clinic and prefs["new_clinic_plan"] != "suite":
        plan = [] if prefs["new_clinic_plan"] == "none" else [prefs["new_clinic_plan"]]
        db.add(OrgSettings(org_key=_doc_org_key(user), category=ENTITLEMENTS_CATEGORY,
                           data=_json.dumps({"rev": 0, "products": plan, "updatedAt": dt.datetime.now(timezone.utc).isoformat()})))
        db.commit()

    # Send verification email
    token = secrets.token_urlsafe(48)
    ev = EmailVerificationToken(
        user_id=user.id,
        token=token,
        expires_at=dt.datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(ev)
    db.commit()
    verify_url = f"{APP_URL}/verify-email?token={token}"
    send_email(
        email,
        "Verify your Althais email",
        _email_html(
            "Verify your email address",
            f"Hi {user.full_name or 'there'}, thanks for signing up for Althais. Click below to verify your email address and activate your account.",
            verify_url,
            "Verify email",
        ),
    )

    resp = JSONResponse({"ok": True, "redirect": "/onboarding"})   # new accounts land on the welcome page first
    _set_session_cookie(resp, user.id)
    return resp


@router.post("/login")
def login(
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    email = (email or "").strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    # Same generic message whether the email is unknown or the password is wrong,
    # so an attacker can't tell which emails have accounts.
    if not user or not verify_password(password, user.password_hash):
        return JSONResponse({"error": "Invalid email or password."}, status_code=401)

    # Track login stats
    user.login_count = (user.login_count or 0) + 1
    user.last_login = dt.datetime.now(timezone.utc)
    db.commit()

    resp = JSONResponse({"ok": True, "redirect": "/overview"})
    _set_session_cookie(resp, user.id)
    return resp


@router.get("/api/me")
def me(user: User = Depends(require_user)):
    """Handy endpoint for the frontend to check who's logged in."""
    return {
        "id": user.id,
        "email": user.email,
        "full_name": user.full_name,
        "organization": user.organization,
        "email_verified": bool(user.email_verified),
        "role": user.role or "admin",
        "provider_name": user.provider_name or "",
    }


@router.post("/api/me/provider-name")
def set_provider_name(
    provider_name: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Links this login to the provider identity used throughout appointments/
    claims data (e.g. "Dr. R. Patel"), so Althea's "what's my schedule" can
    actually mean something specific instead of showing everyone's. Blank
    is valid — clears the link for accounts that aren't a specific provider.
    """
    user.provider_name = (provider_name or "").strip()
    db.commit()
    return JSONResponse({"ok": True, "provider_name": user.provider_name})


@router.post("/api/me/complete-onboarding")
def complete_onboarding(
    full_name: str = Form(""),
    provider_name: str = Form(""),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """
    Called from the first-login onboarding screen. Saves the user's display
    name and provider identity, then marks onboarding as complete so they
    land in the main app on all subsequent logins.
    """
    if full_name.strip():
        user.full_name = full_name.strip()
    user.provider_name = provider_name.strip()
    user.onboarding_complete = 1
    db.commit()
    return JSONResponse({"ok": True, "redirect": "/overview"})


@router.post("/api/change-password")
def change_password(
    current_password: str = Form(...),
    new_password: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Allow a logged-in user to change their own password."""
    if not verify_password(current_password, user.password_hash):
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Current password is incorrect.")
    if len(new_password) < 8:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="New password must be at least 8 characters.")
    if current_password == new_password:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="New password must be different from your current password.")
    user.password_hash = hash_password(new_password)
    db.commit()
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
#  Org user management (admin role only)
# ──────────────────────────────────────────────────────────────────────────
@router.get("/api/org/users")
def org_users(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """List all users in the same organization. Admins only."""
    if (user.role or "admin") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin role required.")
    users = db.scalars(
        select(User).where(User.organization == user.organization).order_by(User.created_at)
    ).all()
    return [{"id": u.id, "email": u.email, "full_name": u.full_name, "role": u.role or "admin",
             "created_at": u.created_at.isoformat() if u.created_at else None} for u in users]


@router.post("/api/org/users/{user_id}/role")
def update_user_role(
    user_id: int,
    new_role: str = Form(...),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Change a user's role. Admins only, cannot change their own role."""
    if (user.role or "admin") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin role required.")
    if new_role not in ROLE_LEVELS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail=f"Invalid role. Choose from: {list(ROLE_LEVELS.keys())}")
    target = db.get(User, user_id)
    if not target or target.organization != user.organization:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")
    if target.id == user.id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="You cannot change your own role.")
    target.role = new_role
    db.commit()
    return {"ok": True, "user_id": user_id, "new_role": new_role}


@router.post("/api/org/invite")
def invite_user(
    email: str = Form(...),
    full_name: str = Form(""),
    role: str = Form("biller"),
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Invite a new user to the organization with a specific role."""
    if (user.role or "admin") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin role required.")
    if role not in ROLE_LEVELS:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Invalid role.")
    email = email.strip().lower()
    if db.scalar(select(User).where(User.email == email)):
        return JSONResponse({"error": "An account with that email already exists."}, status_code=400)
    import secrets as _secrets
    temp_password = _secrets.token_urlsafe(12)
    new_user = User(
        email=email,
        password_hash=hash_password(temp_password),
        full_name=full_name.strip(),
        organization=user.organization,
        role=role,
        email_verified=0,
    )
    db.add(new_user)
    db.commit()
    db.refresh(new_user)
    # Send invite email with temp password
    app_url = APP_URL
    send_email(
        email,
        f"You've been invited to Althais — {user.organization}",
        _email_html(
            f"You've been invited to join {user.organization}",
            f"{user.full_name or user.email} has added you to <strong>{user.organization}</strong> on Althais as a <strong>{role}</strong>.<br><br>"
            f"Your temporary password is: <strong style='font-family:monospace'>{temp_password}</strong><br><br>"
            f"Please sign in and change your password immediately.",
            f"{app_url}/login",
            "Sign in to Althais",
        ),
    )
    return JSONResponse({"ok": True, "user_id": new_user.id})


@router.delete("/api/org/users/{user_id}")
def remove_team_member(
    user_id: int,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Remove a teammate from the organization. Admins only, cannot remove self."""
    if (user.role or "admin") != "admin":
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Admin role required.")
    target = db.get(User, user_id)
    if not target or target.organization != user.organization:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found.")
    if target.id == user.id:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="You cannot remove your own account.")
    db.delete(target)
    db.commit()
    return {"ok": True, "removed_user_id": user_id}


# ──────────────────────────────────────────────────────────────────────────
#  Org settings — generic per-category JSON storage shared across the team.
#  Powers every toggle/threshold/table in the Settings tabs (AI Automation,
#  Billing & Claims, Notifications, Security, Appearance, etc.) so changes
#  persist server-side and sync across every teammate's login, not just the
#  browser that made the change.
# ──────────────────────────────────────────────────────────────────────────
import json as _json

# Kept by their own endpoints (and the admin console), never through the generic settings API:
# otherwise a clinic could write its own plan, or skip the admins-only rule on staff and branding.
_RESERVED_SETTINGS = {"entitlements", "staff", "tasks", "branding", "admin_prefs"}
TOOL_PRODUCTS = ("scribe", "coding", "insurance", "staff")   # every single tool (Settings comes with each one)


def _settings_access(user, db, category: str) -> None:
    if category in _RESERVED_SETTINGS:
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="This setting can't be changed here.")
    ensure_product(user, db, *TOOL_PRODUCTS)   # any plan


@router.get("/api/org/settings/{category}")
def get_org_settings(
    category: str,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    _settings_access(user, db, category)
    row = db.scalar(
        select(OrgSettings).where(
            OrgSettings.org_key == user.organization,
            OrgSettings.category == category,
        )
    )
    if not row:
        return JSONResponse({"category": category, "data": {}})
    try:
        parsed = _json.loads(row.data)
    except Exception:
        parsed = {}
    return JSONResponse({"category": category, "data": parsed,
                          "updated_at": row.updated_at.isoformat() if row.updated_at else None})


@router.post("/api/org/settings/{category}")
async def save_org_settings(
    category: str,
    request: Request,
    user: User = Depends(require_user),
    db: Session = Depends(get_db),
):
    """Accepts a raw JSON body and stores it as this org's settings for
    the given category. Any role can save — admins-only writes (like fee
    schedule or scrubber rules) are enforced by the frontend hiding the
    controls from non-admins; this endpoint just persists what's sent."""
    _settings_access(user, db, category)
    try:
        body = await request.json()
    except Exception:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Request body must be valid JSON.")
    row = db.scalar(
        select(OrgSettings).where(
            OrgSettings.org_key == user.organization,
            OrgSettings.category == category,
        )
    )
    if row:
        row.data = _json.dumps(body)
    else:
        row = OrgSettings(org_key=user.organization, category=category, data=_json.dumps(body))
        db.add(row)
    db.commit()
    return {"ok": True, "category": category}


# ──────────────────────────────────────────────────────────────────────────
#  Shared workspace documents — one JSON document per organization per kind,
#  stored in org_settings under the kind's category:
#    /api/staff  team members, onboarding, credentials, training, roles and
#                clinic setup (static/js/staff-store.js). Admins only can edit.
#    /api/tasks  the practice's task list and dismissed AI Inbox alerts
#                (static/js/practice-data.js). Anyone in the org can edit.
#    /api/branding  the practice's brand color (static/js/brand.js), set in
#                Settings > Appearance. Admins only can edit.
#  Kept apart from the generic settings endpoints above because these hold
#  personal data: an account with no organization gets its own private
#  document instead of the shared "" key.
#
#  Saves are versioned: the document carries a "rev" number, a PUT must send
#  the rev it was based on, and a stale rev gets 409 plus the current
#  document, so two people editing at once never silently overwrite each other.
# ──────────────────────────────────────────────────────────────────────────
def _doc_org_key(user: User) -> str:
    return user.organization.strip() if (user.organization or "").strip() else f"user:{user.email}"


def _load_doc(user: User, db: Session, category: str):
    row = db.scalar(
        select(OrgSettings).where(
            OrgSettings.org_key == _doc_org_key(user),
            OrgSettings.category == category,
        )
    )
    doc = {}
    if row:
        try:
            doc = _json.loads(row.data) or {}
        except Exception:
            doc = {}
    if not isinstance(doc, dict):
        doc = {}
    doc.setdefault("rev", 0)
    return row, doc


def _can_edit_doc(user: User, admin_only: bool) -> bool:
    return (user.role or "admin") == "admin" if admin_only else True


# ──────────────────────────────────────────────────────────────────────────
#  Product access (tiers). A clinic can buy the full Althais suite or single
#  tools: Scribe, Coding, Insurance, Staff. What each organization has is kept
#  in org_settings (category "entitlements") and switched on by Althais in the
#  admin console (/admin). An organization with no record has the full suite,
#  so every existing account keeps working exactly as before.
# ──────────────────────────────────────────────────────────────────────────
PRODUCTS = ("suite", "scribe", "coding", "insurance", "staff")
ENTITLEMENTS_CATEGORY = "entitlements"


def org_products(user: User, db: Session) -> set:
    """The products this user's organization can use. {"suite"} unlocks everything."""
    row, doc = _load_doc(user, db, ENTITLEMENTS_CATEGORY)
    if not row or not isinstance(doc.get("products"), list):
        return {"suite"}
    return {p for p in doc["products"] if p in PRODUCTS}


def org_althea(user: User, db: Session) -> bool:
    """Althea, the assistant, is switched on per clinic in /admin. Until it's set, it comes with the full suite only."""
    row, doc = _load_doc(user, db, ENTITLEMENTS_CATEGORY)
    if row and isinstance(doc.get("althea"), bool):
        return doc["althea"]
    return "suite" in org_products(user, db)


def ensure_althea(user: User, db: Session) -> None:
    if not org_althea(user, db):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Althea isn't switched on for your clinic.")


def has_product(products: set, *needed: str) -> bool:
    """True when the org has the full suite, or any one of the tools named."""
    return "suite" in products or any(p in products for p in needed)


def ensure_product(user: User, db: Session, *needed: str) -> None:
    """For API handlers: 403 unless the org bought a tool that includes this feature."""
    if not has_product(org_products(user, db), *needed):
        from fastapi import HTTPException
        raise HTTPException(status_code=403, detail="Your Althais plan doesn't include this tool.")


def _register_doc_routes(path: str, category: str, admin_only: bool, denied_message: str, products=None, validate=None):
    """GET/PUT a versioned per-organization document. `products`: None = any plan; otherwise the tools (besides the
    full suite, which can always use it) that may, so () means full suite only."""
    def check(user, db):
        if products is not None:
            ensure_product(user, db, *products)
    @router.get(path)
    def get_doc(user: User = Depends(require_user), db: Session = Depends(get_db)):
        check(user, db)
        _, doc = _load_doc(user, db, category)
        return JSONResponse({"data": doc, "can_edit": _can_edit_doc(user, admin_only)})

    @router.put(path)
    async def save_doc(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
        check(user, db)
        if not _can_edit_doc(user, admin_only):
            return JSONResponse({"error": denied_message}, status_code=403)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Request body must be valid JSON."}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Expected a JSON object."}, status_code=400)
        problem = validate(body, db) if validate else None
        if problem:
            return JSONResponse({"error": problem}, status_code=400)
        row, current = _load_doc(user, db, category)
        if body.get("rev") != current["rev"]:
            return JSONResponse({"error": "These records changed since you loaded them.", "data": current}, status_code=409)
        body["rev"] = current["rev"] + 1
        if row:
            row.data = _json.dumps(body)
        else:
            db.add(OrgSettings(org_key=_doc_org_key(user), category=category, data=_json.dumps(body)))
        db.commit()
        return JSONResponse({"ok": True, "data": body})


_register_doc_routes("/api/staff", "staff", admin_only=True, denied_message="Only admins can change staff records.", products=("staff",))
_register_doc_routes("/api/tasks", "tasks", admin_only=False, denied_message="", products=())
_register_doc_routes("/api/branding", "branding", admin_only=True, denied_message="Only admins can change the brand color.",
                     validate=lambda body, db: _check_brand_color(body, db))


# ──────────────────────────────────────────────────────────────────────────
#  Password reset
# ──────────────────────────────────────────────────────────────────────────
@router.post("/forgot-password")
def forgot_password(email: str = Form(...), db: Session = Depends(get_db)):
    email = (email or "").strip().lower()
    user = db.scalar(select(User).where(User.email == email))
    # Always return success — don't leak whether the email exists
    if user:
        # Invalidate any existing unused tokens
        old = db.scalars(select(PasswordResetToken).where(
            PasswordResetToken.user_id == user.id,
            PasswordResetToken.used == 0
        )).all()
        for t in old:
            t.used = 1
        db.commit()
        # Create new token
        token = secrets.token_urlsafe(48)
        prt = PasswordResetToken(
            user_id=user.id,
            token=token,
            expires_at=dt.datetime.now(timezone.utc) + timedelta(hours=1),
        )
        db.add(prt)
        db.commit()
        reset_url = f"{APP_URL}/reset-password?token={token}"
        send_email(
            email,
            "Reset your Althais password",
            _email_html(
                "Reset your password",
                "We received a request to reset your password. Click the button below to choose a new one. This link expires in 1 hour.",
                reset_url,
                "Reset password",
            ),
        )
    return JSONResponse({"ok": True})


@router.post("/reset-password")
def reset_password(
    token: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters."}, status_code=400)
    now = dt.datetime.now(timezone.utc)
    prt = db.scalar(select(PasswordResetToken).where(
        PasswordResetToken.token == token,
        PasswordResetToken.used == 0,
    ))
    if not prt:
        return JSONResponse({"error": "Invalid or expired reset link."}, status_code=400)
    expires = prt.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        return JSONResponse({"error": "This reset link has expired. Please request a new one."}, status_code=400)
    user = db.get(User, prt.user_id)
    if not user:
        return JSONResponse({"error": "Account not found."}, status_code=400)
    user.password_hash = hash_password(password)
    prt.used = 1
    db.commit()
    return JSONResponse({"ok": True, "redirect": "/login"})


# ──────────────────────────────────────────────────────────────────────────
#  Email verification
# ──────────────────────────────────────────────────────────────────────────
@router.get("/verify-email")
def verify_email(token: str, db: Session = Depends(get_db)):
    from fastapi.responses import RedirectResponse as RR
    now = dt.datetime.now(timezone.utc)
    ev = db.scalar(select(EmailVerificationToken).where(
        EmailVerificationToken.token == token,
        EmailVerificationToken.used == 0,
    ))
    if not ev:
        return RR(url="/login?verified=invalid")
    expires = ev.expires_at
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if expires < now:
        return RR(url="/login?verified=expired")
    user = db.get(User, ev.user_id)
    if user:
        user.email_verified = 1
    ev.used = 1
    db.commit()
    return RR(url="/overview?verified=1")


@router.post("/resend-verification")
def resend_verification(user: User = Depends(require_user), db: Session = Depends(get_db)):
    if user.email_verified:
        return JSONResponse({"ok": True, "message": "Already verified."})
    # Invalidate old tokens
    old = db.scalars(select(EmailVerificationToken).where(
        EmailVerificationToken.user_id == user.id,
        EmailVerificationToken.used == 0,
    )).all()
    for t in old:
        t.used = 1
    db.commit()
    token = secrets.token_urlsafe(48)
    ev = EmailVerificationToken(
        user_id=user.id,
        token=token,
        expires_at=dt.datetime.now(timezone.utc) + timedelta(hours=24),
    )
    db.add(ev)
    db.commit()
    verify_url = f"{APP_URL}/verify-email?token={token}"
    send_email(
        user.email,
        "Verify your Althais email",
        _email_html(
            "Verify your email address",
            "Click below to verify your email address and activate your account.",
            verify_url,
            "Verify email",
        ),
    )
    return JSONResponse({"ok": True})

# ──────────────────────────────────────────────────────────────────────────
#  Admin authentication — completely separate from user sessions.
#  Admin credentials live in environment variables (not the database),
#  so there's no user account to compromise.
# ──────────────────────────────────────────────────────────────────────────
ADMIN_USERNAME = os.environ.get("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")   # must be set in production
ADMIN_COOKIE   = "althais_admin_session"
ADMIN_TOKEN_TTL_HOURS = 8


def create_admin_token(hours: int = ADMIN_TOKEN_TTL_HOURS) -> str:
    now = dt.datetime.now(timezone.utc)
    payload = {"sub": "admin", "iat": now, "exp": now + timedelta(hours=hours)}
    return jwt.encode(payload, SECRET_KEY + "_admin", algorithm="HS256")


def verify_admin_token(token: str) -> bool:
    try:
        data = jwt.decode(token, SECRET_KEY + "_admin", algorithms=["HS256"])
        return data.get("sub") == "admin"
    except jwt.PyJWTError:
        return False


def current_admin(request: Request) -> bool:
    token = request.cookies.get(ADMIN_COOKIE)
    if not token:
        return False
    return verify_admin_token(token)


def require_admin(request: Request):
    if not current_admin(request):
        from fastapi import HTTPException
        raise HTTPException(status_code=401, detail="Admin access required")
    return True


@router.post("/admin/login")
def admin_login(username: str = Form(...), password: str = Form(...), db: Session = Depends(get_db)):
    if not ADMIN_PASSWORD:
        return JSONResponse({"error": "Admin not configured."}, status_code=503)
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return JSONResponse({"error": "Invalid admin credentials."}, status_code=401)
    hours = admin_settings(db)["session_hours"]   # admin console > Settings
    resp = JSONResponse({"ok": True, "redirect": "/admin"})
    resp.set_cookie(
        key=ADMIN_COOKIE, value=create_admin_token(hours),
        httponly=True, samesite="lax", secure=COOKIE_SECURE,
        max_age=hours * 3600, path="/"
    )
    return resp


@router.post("/admin/logout")
def admin_logout():
    resp = JSONResponse({"ok": True, "redirect": "/admin/login"})
    resp.delete_cookie(ADMIN_COOKIE, path="/")
    return resp


@router.get("/api/admin/users")
def admin_users(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    """Return all users with stats for the admin dashboard."""
    users = db.scalars(select(User).order_by(User.created_at.desc())).all()
    return [{
        "id": u.id,
        "email": u.email,
        "full_name": u.full_name,
        "organization": u.organization,
        "login_count": u.login_count or 0,
        "claims_submitted": u.claims_submitted or 0,
        "last_login": u.last_login.isoformat() if u.last_login else None,
        "created_at": u.created_at.isoformat() if u.created_at else None,
    } for u in users]


@router.delete("/api/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    user = db.get(User, user_id)
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"ok": True}


# ──────────────────────────────────────────────────────────────────────────
#  Admin console settings (the Althais team's own, not any clinic's). Kept under
#  a key no organization can have and never served by the clinic settings API.
# ──────────────────────────────────────────────────────────────────────────
ADMIN_SETTINGS_KEY = "__althais_admin__"
ADMIN_DEFAULTS = {
    "signups_open": True,          # new accounts can register
    "new_clinic_plan": "suite",    # plan a brand-new clinic starts on ("none" = no access until switched on)
    "session_hours": 8,            # how long an admin sign-in lasts
    "hide_test_accounts": False,   # admin page display only
    "inactive_after_days": 30,     # admin page display only
    "start_page": "overview",      # which admin page opens first
    "brand_color": "#0d5bd7",      # the admin console's own color (Althais blue); clinics keep theirs
    "compact": False,              # tighter table rows
    # the brand colors clinics can choose from in their Settings > Appearance (names live in static/js/brand.js)
    "clinic_palette": ["#0d5bd7", "#87cefa", "#f7b6cb", "#93dfc0", "#8b5e3c", "#c9b3f5", "#16161a", "#ffffff", "#a8bfa0", "#d4a5ae",
                       "#f5c6a5", "#f2dea0", "#a9b7ec", "#9cc5c9", "#1f3a5f", "#2f5d50", "#d18f76", "#b58db6", "#64748b", "#c8bfb3"],
    "clinic_custom_color": True,   # clinics may also type any hex code
}
BRAND_DEFAULT = "#0d5bd7"


def _is_hex(c) -> bool:
    return isinstance(c, str) and len(c) == 7 and c.startswith("#") and all(x in "0123456789abcdefABCDEF" for x in c[1:])


def admin_settings(db: Session) -> dict:
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == ADMIN_SETTINGS_KEY, OrgSettings.category == "admin_prefs"))
    try:
        saved = _json.loads(row.data) if row else {}
    except Exception:
        saved = {}
    return {**ADMIN_DEFAULTS, **{k: v for k, v in saved.items() if k in ADMIN_DEFAULTS}}


@router.get("/api/admin/settings")
def get_admin_settings(db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    return admin_settings(db)


@router.put("/api/admin/settings")
async def put_admin_settings(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    body = await request.json()
    cur = admin_settings(db)
    if "signups_open" in body: cur["signups_open"] = bool(body["signups_open"])
    if "hide_test_accounts" in body: cur["hide_test_accounts"] = bool(body["hide_test_accounts"])
    if body.get("new_clinic_plan") in PRODUCTS + ("none",): cur["new_clinic_plan"] = body["new_clinic_plan"]
    if body.get("session_hours") in (1, 4, 8, 12, 24): cur["session_hours"] = body["session_hours"]
    if body.get("inactive_after_days") in (14, 30, 60, 90): cur["inactive_after_days"] = body["inactive_after_days"]
    if body.get("start_page") in ("overview", "orgs", "accounts", "plans"): cur["start_page"] = body["start_page"]
    if "compact" in body: cur["compact"] = bool(body["compact"])
    if "clinic_custom_color" in body: cur["clinic_custom_color"] = bool(body["clinic_custom_color"])
    pal = body.get("clinic_palette")
    if isinstance(pal, list) and 0 < len(pal) <= 120 and all(_is_hex(c) for c in pal):
        cur["clinic_palette"] = list(dict.fromkeys([BRAND_DEFAULT] + [c.lower() for c in pal]))   # Althais Blue is always offered
    if _is_hex(body.get("brand_color")): cur["brand_color"] = body["brand_color"].lower()
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == ADMIN_SETTINGS_KEY, OrgSettings.category == "admin_prefs"))
    if row:
        row.data = _json.dumps(cur)
    else:
        db.add(OrgSettings(org_key=ADMIN_SETTINGS_KEY, category="admin_prefs", data=_json.dumps(cur)))
    db.commit()
    return cur


@router.get("/api/branding/palette")
def branding_palette(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """The brand colors this clinic can choose from, set by Althais in the admin console."""
    prefs = admin_settings(db)
    return {"colors": prefs["clinic_palette"], "custom": prefs["clinic_custom_color"]}


def _check_brand_color(body: dict, db: Session):
    """Branding saves: when clinics can't pick their own color, only the offered ones (or Althais Blue) are allowed."""
    color = str(body.get("color") or "").lower()
    if not _is_hex(color):
        return "That isn’t a valid color."
    prefs = admin_settings(db)
    if not prefs["clinic_custom_color"] and color != BRAND_DEFAULT and color not in prefs["clinic_palette"]:
        return "That color isn’t one of the options Althais offers right now."
    return None


@router.get("/api/admin/orgs")
def admin_orgs(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    """Every organization (grouped the same way their records are), its people, and which Althais products it has."""
    orgs = {}
    for u in db.scalars(select(User).order_by(User.created_at)).all():
        key = _doc_org_key(u)
        o = orgs.setdefault(key, {"org_key": key, "name": (u.organization or "").strip() or f"{u.email} (no organization)", "users": [], "sample": u})
        o["users"].append(u.email)
    out = []
    for o in orgs.values():
        row, _doc = _load_doc(o["sample"], db, ENTITLEMENTS_CATEGORY)
        out.append({"org_key": o["org_key"], "name": o["name"], "users": o["users"],
                    "products": sorted(org_products(o["sample"], db)), "althea": org_althea(o["sample"], db), "custom": bool(row)})
    return sorted(out, key=lambda o: o["name"].lower())


@router.post("/api/admin/orgs/products")
async def admin_set_org_products(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    """Set which products an organization has, and whether Althea is on. Choosing the full suite clears the single
    tools (it includes them). Althea is separate: it can be on or off with any plan."""
    body = await request.json()
    key, products = str(body.get("org_key") or ""), body.get("products")
    althea = body.get("althea")
    if althea is not None and not isinstance(althea, bool):
        return JSONResponse({"error": "althea must be true or false."}, status_code=400)
    if not key or not isinstance(products, list) or any(p not in PRODUCTS for p in products):
        return JSONResponse({"error": "Send an org_key and a list of products from: " + ", ".join(PRODUCTS)}, status_code=400)
    chosen = ["suite"] if "suite" in products else sorted(set(products))
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == key, OrgSettings.category == ENTITLEMENTS_CATEGORY))
    data = {"rev": 0, "products": chosen, "updatedAt": dt.datetime.now(timezone.utc).isoformat()}
    if althea is not None:
        data["althea"] = althea
    if row:
        prev = _json.loads(row.data or "{}")
        data["rev"] = (prev.get("rev") or 0) + 1
        if althea is None and isinstance(prev.get("althea"), bool):
            data["althea"] = prev["althea"]   # not sent: keep what it was
        row.data = _json.dumps(data)
    else:
        db.add(OrgSettings(org_key=key, category=ENTITLEMENTS_CATEGORY, data=_json.dumps(data)))
    db.commit()
    return {"ok": True, "org_key": key, "products": chosen, "althea": data.get("althea", "suite" in chosen)}

# ──────────────────────────────────────────────────────────────────────────
#  Demo request table + route
# ──────────────────────────────────────────────────────────────────────────
class DemoRequest(Base):
    __tablename__ = "demo_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    practice_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )

Base.metadata.create_all(engine)   # creates demo_requests if it doesn't exist


@router.post("/request-demo")
def request_demo(
    full_name: str = Form(...),
    email: str = Form(...),
    practice_name: str = Form(...),
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    # 1. Save to database (always works even if email fails)
    req = DemoRequest(
        full_name=full_name.strip(),
        email=email.strip().lower(),
        practice_name=practice_name.strip(),
        phone=phone.strip(),
    )
    db.add(req)
    db.commit()

    # 2. Send notification email to Kevin
    send_email(
        "kevinqu@althais.com",
        f"New demo request — {practice_name}",
        _email_html(
            "New demo request",
            f"""
            <strong>Name:</strong> {full_name}<br>
            <strong>Email:</strong> {email}<br>
            <strong>Practice:</strong> {practice_name}<br>
            <strong>Phone:</strong> {phone or '—'}<br><br>
            Reply directly to this email to follow up.
            """,
            f"mailto:{email}",
            f"Reply to {full_name}",
        ),
    )

    # 3. Send confirmation email to the requester
    send_email(
        email,
        "We received your Althais demo request",
        _email_html(
            "Thanks for your interest in Althais",
            f"Hi {full_name}, we received your demo request for <strong>{practice_name}</strong>. Someone from our team will reach out within 1 business day to schedule your demo.",
            "https://althais.com",
            "Visit Althais",
        ),
    )

    return JSONResponse({"ok": True})


class PilotRequest(Base):
    """Sign-ups from the public "Join the Pilot" page. Kept separate from
    demo_requests so the existing demo flow and its table are untouched."""
    __tablename__ = "pilot_requests"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    full_name: Mapped[str] = mapped_column(String(255))
    email: Mapped[str] = mapped_column(String(255))
    practice_name: Mapped[str] = mapped_column(String(255))
    phone: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )

Base.metadata.create_all(engine)   # creates pilot_requests if it doesn't exist


@router.post("/request-pilot")
def request_pilot(
    full_name: str = Form(...),
    email: str = Form(...),
    practice_name: str = Form(...),
    phone: str = Form(""),
    db: Session = Depends(get_db),
):
    import html as _html
    full_name, email = full_name.strip()[:255], email.strip().lower()[:255]
    practice_name, phone = practice_name.strip()[:255], phone.strip()[:64]
    if not full_name or not practice_name or "@" not in email or "." not in email.split("@")[-1]:
        return JSONResponse({"ok": False, "error": "Please check your name, email and practice name."}, status_code=400)

    # 1. Save to database (always works even if email fails)
    db.add(PilotRequest(full_name=full_name, email=email, practice_name=practice_name, phone=phone))
    db.commit()

    # Values go into HTML emails, so escape them first.
    n, e, pr, ph = (_html.escape(v) for v in (full_name, email, practice_name, phone))

    # 2. Notify the team
    send_email(
        "kevinqu@althais.com",
        f"New pilot request: {practice_name}",
        _email_html(
            "New pilot request",
            f"""
            <strong>Name:</strong> {n}<br>
            <strong>Email:</strong> {e}<br>
            <strong>Practice:</strong> {pr}<br>
            <strong>Phone:</strong> {ph or '-'}<br><br>
            Reply directly to this email to follow up.
            """,
            f"mailto:{e}",
            f"Reply to {n}",
        ),
    )

    # 3. Confirm to the requester
    send_email(
        email,
        "We received your Althais pilot request",
        _email_html(
            "Thanks for your interest in Althais",
            f"Hi {n}, we received your request to join the Althais pilot for <strong>{pr}</strong>. Someone from our team will reach out within 1 business day.",
            "https://althais.com",
            "Visit Althais",
        ),
    )

    return JSONResponse({"ok": True})


class NewsletterSubscriber(Base):
    __tablename__ = "newsletter_subscribers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    email: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )

Base.metadata.create_all(engine)   # creates newsletter_subscribers if it doesn't exist


@router.post("/api/newsletter-signup")
def newsletter_signup(email: str = Form(...), db: Session = Depends(get_db)):
    clean_email = email.strip().lower()
    if "@" not in clean_email or "." not in clean_email.split("@")[-1]:
        return JSONResponse({"ok": False, "error": "Enter a valid email address."}, status_code=400)

    existing = db.execute(
        select(NewsletterSubscriber).where(NewsletterSubscriber.email == clean_email)
    ).scalar_one_or_none()
    if not existing:
        db.add(NewsletterSubscriber(email=clean_email))
        db.commit()

    return JSONResponse({"ok": True})
