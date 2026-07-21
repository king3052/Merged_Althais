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
from sqlalchemy import create_engine, String, Integer, DateTime, select, text
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
    """Server-side patient records scoped to an org — shared by web + desktop clients."""
    __tablename__ = "org_patients"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    mrn: Mapped[str] = mapped_column(String(64), default="")
    name: Mapped[str] = mapped_column(String(255), default="")
    dob: Mapped[str] = mapped_column(String(20), default="")
    sex: Mapped[str] = mapped_column(String(4), default="")
    payer: Mapped[str] = mapped_column(String(128), default="")
    provider: Mapped[str] = mapped_column(String(255), default="")
    created_at: Mapped[dt.datetime] = mapped_column(
        DateTime, default=lambda: dt.datetime.now(timezone.utc)
    )


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
    if not email or "@" not in email:
        return JSONResponse({"error": "Enter a valid email address."}, status_code=400)
    if len(password) < 8:
        return JSONResponse({"error": "Password must be at least 8 characters."}, status_code=400)
    if db.scalar(select(User).where(User.email == email)):
        return JSONResponse({"error": "An account with that email already exists."}, status_code=400)

    user = User(
        email=email,
        password_hash=hash_password(password),
        full_name=full_name.strip(),
        organization=organization.strip(),
        email_verified=0,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

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

    resp = JSONResponse({"ok": True, "redirect": "/overview"})
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


def create_admin_token() -> str:
    now = dt.datetime.now(timezone.utc)
    payload = {"sub": "admin", "iat": now, "exp": now + timedelta(hours=ADMIN_TOKEN_TTL_HOURS)}
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
def admin_login(username: str = Form(...), password: str = Form(...)):
    if not ADMIN_PASSWORD:
        return JSONResponse({"error": "Admin not configured."}, status_code=503)
    if username != ADMIN_USERNAME or password != ADMIN_PASSWORD:
        return JSONResponse({"error": "Invalid admin credentials."}, status_code=401)
    resp = JSONResponse({"ok": True, "redirect": "/admin"})
    resp.set_cookie(
        key=ADMIN_COOKIE, value=create_admin_token(),
        httponly=True, samesite="lax", secure=COOKIE_SECURE,
        max_age=ADMIN_TOKEN_TTL_HOURS * 3600, path="/"
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
