"""Sign-in security: sessions with an idle sign-out, two-step login, password rules and account locks.

Sessions
  Every sign-in is a row in user_sessions, named by the `sid` in the session cookie's signed token (auth.create_token).
  A request is signed in only while that row exists, isn't revoked, hasn't passed its end, and has seen activity within
  the clinic's idle limit (Settings > Security; 15 minutes unless the clinic chose otherwise). Activity is a page load
  or the browser's ping (static/js/session-guard.js, sent at most once a minute while someone is moving the mouse or
  typing), never a background request, so a screen left open polls without keeping the session alive. Signing out,
  a password change and a 2FA reset end sessions on the server. The admin console works the same way (kind "admin").

Two-step login (TOTP, RFC 6238, any authenticator app)
  Required for a clinic's admins and billers unless the clinic chooses otherwise (everyone, or optional). With it on,
  a correct password returns a short-lived challenge instead of a session; the 6-digit code (or a one-time recovery
  code) finishes the sign-in. Someone who must use it but hasn't set it up yet is held on /setup-2fa (middleware
  below). Codes can't be reused, and wrong codes count toward the account lock. The secret is stored encrypted.
  The admin console asks for a code when ADMIN_TOTP_SECRET is set (/admin/2fa shows how to set it up).

Passwords and locks
  At least 12 characters, not a common password, not built from the email address. Five wrong passwords or codes in a
  row lock the account for 15 minutes (the owner is emailed); many failures from one address are slowed the same way.
"""
import base64
import datetime as dt
import hashlib
import hmac
import json
import os
import re
import secrets
import struct
import time
from collections import defaultdict, deque
from datetime import timedelta, timezone

import jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import JSONResponse, RedirectResponse
from sqlalchemy import DateTime, Integer, String, select, update
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import (ADMIN_COOKIE, APP_URL, COOKIE_NAME, COOKIE_SECURE, SECRET_KEY, TOKEN_TTL_HOURS, Base, OrgSettings, SessionLocal, User,
                  _doc_org_key, _email_html, _load_doc, decode_token, engine, get_db, require_user, send_email)

router = APIRouter()

IDLE_CHOICES = (5, 10, 15, 30, 60)
DEFAULT_IDLE = 15
ADMIN_IDLE_MINUTES = 30
MFA_POLICIES = ("admins_billers", "everyone", "optional")
LOCK_AFTER = 5                      # wrong passwords or codes in a row
LOCK_MINUTES = 15
IP_LIMIT = (30, 15 * 60)            # failures from one address in 15 minutes
TOUCH_EVERY = 30                    # seconds: at most one last-seen write per session this often
CATEGORY = "security"               # org_settings: the clinic's choices


def _now():
    return dt.datetime.now(timezone.utc)


def _aware(d):
    return d.replace(tzinfo=timezone.utc) if d is not None and d.tzinfo is None else d


def enforced() -> bool:
    """MFA_ENFORCE=0 turns the two-step requirement off (local development and tests only)."""
    return os.environ.get("MFA_ENFORCE", "1") != "0"


# ── sessions ────────────────────────────────────────────────────────────────
class UserSession(Base):
    __tablename__ = "user_sessions"
    sid: Mapped[str] = mapped_column(String(64), primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, index=True)
    kind: Mapped[str] = mapped_column(String(16), default="user")          # user | admin | view_as
    created_at: Mapped[dt.datetime] = mapped_column(DateTime)
    last_seen: Mapped[dt.datetime] = mapped_column(DateTime)
    ends_at: Mapped[dt.datetime] = mapped_column(DateTime)
    idle_minutes: Mapped[int] = mapped_column(Integer, default=DEFAULT_IDLE)
    revoked: Mapped[int] = mapped_column(Integer, default=0)
    ended_why: Mapped[str] = mapped_column(String(32), default="")
    mfa_setup: Mapped[int] = mapped_column(Integer, default=0)              # 1 = must set up two-step login before anything else
    ip: Mapped[str] = mapped_column(String(64), default="")
    agent: Mapped[str] = mapped_column(String(255), default="")


Base.metadata.create_all(engine, tables=[UserSession.__table__])


def _client_ip(request):
    if request is None:
        return ""
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else ""))[:64]


def start_session(user_id: int, request=None, kind="user", idle_minutes=None, ttl_minutes=None, mfa_setup=None) -> str:
    """A new session row; returns its id for the token."""
    with SessionLocal() as db:
        u = db.get(User, user_id) if kind == "user" else None
        if idle_minutes is None:
            idle_minutes = policy_for(u, db)["idle_minutes"] if u else DEFAULT_IDLE
        if mfa_setup is None:
            mfa_setup = bool(u and mfa_required(u, db) and not u.mfa_enabled)
        now = _now()
        sid = secrets.token_urlsafe(24)
        db.add(UserSession(sid=sid, user_id=user_id, kind=kind, created_at=now, last_seen=now,
                           ends_at=now + timedelta(minutes=ttl_minutes or TOKEN_TTL_HOURS * 60), idle_minutes=idle_minutes,
                           revoked=0, mfa_setup=1 if mfa_setup else 0, ip=_client_ip(request),
                           agent=(request.headers.get("user-agent", "") if request is not None else "")[:255]))
        db.commit()
        return sid


def _is_page_load(request) -> bool:
    if request is None or request.method != "GET":
        return False
    p = request.url.path
    return not p.startswith(("/api/", "/static/", "/admin/api/")) and "text/html" in request.headers.get("accept", "text/html")


def session_ok(db: Session, data: dict, request=None) -> bool:
    """Whether a decoded token's session is still signed in. A page load counts as activity."""
    sid = data.get("sid") if data else None
    if not sid:
        return False             # tokens from before sessions existed: sign in again
    row = db.get(UserSession, sid)
    if row is None or row.revoked:
        return False
    if str(row.user_id) != str(data.get("sub")) and not (row.kind == "admin" and data.get("sub") == "admin"):
        return False
    now = _now()
    if now >= _aware(row.ends_at):
        _end(db, row, "expired")
        return False
    if (now - _aware(row.last_seen)).total_seconds() > row.idle_minutes * 60:
        _end(db, row, "idle")
        return False
    if _is_page_load(request) and (now - _aware(row.last_seen)).total_seconds() > TOUCH_EVERY:
        row.last_seen = now
        db.commit()
    return True


def _end(db, row, why):
    row.revoked, row.ended_why = 1, why
    db.commit()


def session_row(request, admin=False):
    token = request.cookies.get(ADMIN_COOKIE if admin else COOKIE_NAME)
    if not token:
        return None, None
    try:
        data = jwt.decode(token, SECRET_KEY + "_admin", algorithms=["HS256"]) if admin else decode_token(token)
    except jwt.PyJWTError:
        data = None
    if not data or not data.get("sid"):
        return None, None
    with SessionLocal() as db:
        row = db.get(UserSession, data["sid"])
        if row is not None:
            db.expunge(row)
        return row, data


def end_request_session(request, admin=False, why="signed_out"):
    row, _ = session_row(request, admin)
    if row is None:
        return
    with SessionLocal() as db:
        r = db.get(UserSession, row.sid)
        if r and not r.revoked:
            _end(db, r, why)


def end_user_sessions(db, user_id: int, keep_sid: str = "", why="signed_out_everywhere") -> int:
    rows = db.scalars(select(UserSession).where(UserSession.user_id == user_id, UserSession.revoked == 0, UserSession.kind == "user")).all()
    n = 0
    for r in rows:
        if r.sid != keep_sid:
            r.revoked, r.ended_why = 1, why
            n += 1
    db.commit()
    return n


def _status(row, touch=False):
    if row is None or row.revoked:
        return None
    now = _now()
    left_idle = row.idle_minutes * 60 - (now - _aware(row.last_seen)).total_seconds()
    left_end = (_aware(row.ends_at) - now).total_seconds()
    if left_idle <= 0 or left_end <= 0:
        return None
    if touch:
        with SessionLocal() as db:
            r = db.get(UserSession, row.sid)
            r.last_seen = now
            db.commit()
        left_idle = row.idle_minutes * 60
    return {"idle_seconds": row.idle_minutes * 60, "remaining": int(min(left_idle, left_end)), "ends_in": int(left_end)}


@router.post("/api/session/ping")
def session_ping(request: Request):
    """Someone is using the page: keep the session alive. Sent by session-guard.js at most once a minute."""
    out = _status(session_row(request)[0], touch=True)
    return out or JSONResponse({"error": "Signed out", "signed_out": True}, status_code=401)


@router.get("/api/session/status")
def session_status(request: Request):
    """How long until the idle sign-out. Doesn't count as activity."""
    out = _status(session_row(request)[0])
    return out or JSONResponse({"error": "Signed out", "signed_out": True}, status_code=401)


@router.post("/admin/api/session/ping")
def admin_session_ping(request: Request):
    out = _status(session_row(request, admin=True)[0], touch=True)
    return out or JSONResponse({"error": "Signed out", "signed_out": True}, status_code=401)


@router.get("/admin/api/session/status")
def admin_session_status(request: Request):
    out = _status(session_row(request, admin=True)[0])
    return out or JSONResponse({"error": "Signed out", "signed_out": True}, status_code=401)


# ── the clinic's choices ────────────────────────────────────────────────────
def policy_for(user, db) -> dict:
    """{idle_minutes, mfa}. Demo clinics (flagged in /admin) default to optional two-step login."""
    row, doc = _load_doc(user, db, CATEGORY)
    _, ent = _load_doc(user, db, "entitlements")
    idle = doc.get("idle_minutes") if row else None
    mfa = doc.get("mfa") if row else None
    if ent.get("test_clinic"):
        mfa = "optional"   # marked as a test clinic in /admin: overrides the clinic's own choice
    return {"idle_minutes": idle if idle in IDLE_CHOICES else DEFAULT_IDLE,
            "mfa": mfa if mfa in MFA_POLICIES else ("optional" if ent.get("demo") else "admins_billers")}


def mfa_required(user, db) -> bool:
    if not enforced() or user is None:
        return False
    p = policy_for(user, db)["mfa"]
    return p == "everyone" or (p == "admins_billers" and (user.role or "admin") in ("admin", "biller"))


# ── passwords ───────────────────────────────────────────────────────────────
_COMMON = set("""
password password1 password12 password123 password1234 passw0rd p@ssw0rd p@ssword 123456 1234567 12345678 123456789 1234567890
12345678910 123123123 111111111111 000000000000 qwerty qwertyuiop qwerty123 qwerty12345 asdfghjkl asdfghjkl1 zxcvbnm 1q2w3e4r5t6y
iloveyou iloveyou123 welcome welcome1 welcome123 welcome2026 letmein letmein123 admin admin123 administrator changeme changeme123
monkey dragon football baseball basketball superman batman trustno1 sunshine princess starwars whatever freedom shadow master
master123 hello123 abc123 abcd1234 abcdefgh abcdefghijkl summer2026 winter2026 spring2026 fall2026 autumn2026 password2026
password2025 medical123 doctor123 clinic123 health123 healthcare billing123 dental123 dentist123 hospital123 nurse123
""".split())


def password_problem(pw: str, email: str = "", name: str = "") -> str:
    """'' when the password is acceptable, else what to change, in plain words."""
    pw = pw or ""
    if len(pw) < 12:
        return "Use at least 12 characters. A short phrase of a few words is easy to remember and hard to guess."
    if len(pw) > 128:
        return "Use 128 characters or fewer."
    low = pw.lower()
    core = re.sub(r"[^a-z0-9]", "", low)
    if low in _COMMON or core in _COMMON or re.sub(r"\d+$", "", core) in _COMMON or len(set(pw)) <= 3:
        return "That password is too common or too simple. Try a few unrelated words."
    if re.fullmatch(r"(0123456789|1234567890|abcdefghijkl|qwertyuiop)+.*", core):
        return "That password is too easy to guess. Try a few unrelated words."
    local = (email or "").split("@")[0].lower()
    if len(local) >= 4 and local in low:
        return "Don't use your email address in your password."
    for part in re.split(r"\s+", (name or "").lower()):
        if len(part) >= 4 and part in low and len(low) - len(part) < 8:
            return "Don't build your password from your name."
    if "althais" in low and len(low) < 16:
        return "Don't build your password from Althais."
    return ""


def password_changed(db, user, keep_sid=""):
    """A new password: clear the lock, end the person's other sessions."""
    user.password_changed_at = _now()
    user.failed_logins = 0
    user.locked_until = None
    db.commit()
    end_user_sessions(db, user.id, keep_sid=keep_sid, why="password_changed")


# ── locks ───────────────────────────────────────────────────────────────────
_ip_fails = defaultdict(deque)


def ip_blocked(request) -> bool:
    q, now = _ip_fails[_client_ip(request)], time.time()
    while q and now - q[0] > IP_LIMIT[1]:
        q.popleft()
    return len(q) >= IP_LIMIT[0]


def note_ip_failure(request):
    _ip_fails[_client_ip(request)].append(time.time())


def locked(user) -> int:
    """Minutes left on the account's lock, or 0."""
    until = _aware(getattr(user, "locked_until", None))
    if until and until > _now():
        return max(1, int((until - _now()).total_seconds() // 60) + 1)
    return 0


def failed(db, user, request, why="wrong password"):
    """A wrong password or code: count it, and lock the account after LOCK_AFTER in a row."""
    note_ip_failure(request)
    if user is None:
        return
    user.failed_logins = (user.failed_logins or 0) + 1
    if user.failed_logins >= LOCK_AFTER:
        user.failed_logins = 0
        user.locked_until = _now() + timedelta(minutes=LOCK_MINUTES)
        db.commit()
        try:
            import audit
            audit.record_request(request, user=user, email=user.email, action="account_locked", what="session", status=429,
                                 detail=f"{LOCK_AFTER} failed sign-ins ({why})")
        except Exception:
            pass
        try:
            send_email(user.email, "Your Althais account was locked for 15 minutes",
                       _email_html("Too many sign-in attempts",
                                   f"Someone entered the wrong password or code for your Althais account {LOCK_AFTER} times in a row, so we locked "
                                   f"it for {LOCK_MINUTES} minutes. If this was you, wait and try again, or reset your password. If it wasn't you, "
                                   "reset your password now and tell your clinic's admin.", f"{APP_URL}/forgot-password", "Reset your password",
                                   note="You can ignore this email if it was you."))
        except Exception:
            pass
    else:
        db.commit()


def lock_message(minutes):
    return (f"Too many sign-in attempts. Your account is locked for {minutes} more minute{'s' if minutes != 1 else ''}. "
            "Wait and try again, or reset your password.")


# ── two-step login (TOTP) ───────────────────────────────────────────────────
def _seal(s: str) -> str:
    """Authenticator secrets: AES-256-GCM (crypto_box.py), key from MFA_ENC_KEY or SECRET_KEY."""
    import crypto_box
    return crypto_box.seal_text(s, "mfa", "MFA_ENC_KEY")


def _open(s: str) -> str:
    import crypto_box
    try:
        return crypto_box.open_text(s or "", "mfa", "MFA_ENC_KEY", b"althais-mfa|")
    except Exception:
        return ""


def _upgrade(user):
    """A secret saved before AES-256-GCM is re-sealed the first time it's used."""
    import crypto_box
    if user.mfa_secret and not crypto_box.is_current(user.mfa_secret):
        s = _open(user.mfa_secret)
        if s:
            user.mfa_secret = _seal(s)


def new_secret() -> str:
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")


def hotp(secret: str, counter: int) -> str:
    key = base64.b32decode(secret.upper() + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    o = h[-1] & 15
    return "%06d" % ((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 1000000)


def check_code(secret: str, code: str, last_step: int = 0, at: float = None):
    """The time step a 6-digit code matches (one step of clock drift either way), if it's newer than the last one used."""
    code = re.sub(r"\D", "", code or "")
    if len(code) != 6 or not secret:
        return None
    now = int((at if at is not None else time.time()) // 30)
    for step in (now - 1, now, now + 1):
        if step > (last_step or 0) and hmac.compare_digest(hotp(secret, step), code):
            return step
    return None


def otpauth_uri(secret: str, account: str, issuer="Althais") -> str:
    from urllib.parse import quote
    return f"otpauth://totp/{quote(issuer)}:{quote(account)}?secret={secret}&issuer={quote(issuer)}&algorithm=SHA1&digits=6&period=30"


def qr_svg(uri: str) -> str:
    try:
        import segno
        return segno.make(uri, error="m").svg_data_uri(scale=5, border=2, dark="#0f1116")
    except Exception:
        return ""


def _hash_code(c: str) -> str:
    return hashlib.sha256(("althais-recovery|" + re.sub(r"[^a-z0-9]", "", c.lower())).encode()).hexdigest()


def new_recovery_codes(user) -> list:
    alphabet = "abcdefghjkmnpqrstuvwxyz23456789"
    codes = ["".join(secrets.choice(alphabet) for _ in range(4)) + "-" + "".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(10)]
    user.mfa_recovery = json.dumps([_hash_code(c) for c in codes])
    return codes


def recovery_left(user) -> int:
    try:
        return len(json.loads(user.mfa_recovery or "[]"))
    except ValueError:
        return 0


def use_code(db, user, code: str) -> str:
    """'totp' or 'recovery' when the code signs this person in (and it's used up), else ''."""
    step = check_code(_open(user.mfa_secret), code, user.mfa_last_step or 0)
    if step:
        user.mfa_last_step = step
        _upgrade(user)
        db.commit()
        return "totp"
    try:
        left = json.loads(user.mfa_recovery or "[]")
    except ValueError:
        left = []
    h = _hash_code(code or "")
    if len(re.sub(r"[^a-z0-9]", "", (code or "").lower())) == 8 and h in left:
        left.remove(h)
        user.mfa_recovery = json.dumps(left)
        db.commit()
        return "recovery"
    return ""


# the step between the password and the code: a signed, 5-minute challenge, a few tries each
_challenge_tries = defaultdict(int)


def make_challenge(user) -> str:
    now = _now()
    return jwt.encode({"sub": str(user.id), "purpose": "mfa", "n": secrets.token_hex(8), "iat": now, "exp": now + timedelta(minutes=5)},
                      SECRET_KEY + "_mfa", algorithm="HS256")


def read_challenge(token: str):
    try:
        data = jwt.decode(token or "", SECRET_KEY + "_mfa", algorithms=["HS256"])
    except jwt.PyJWTError:
        return None
    return data if data.get("purpose") == "mfa" else None


def _audit(request, user, action, detail="", status=200):
    try:
        import audit
        audit.record_request(request, user=user, email=getattr(user, "email", ""), action=action, what="session", status=status, detail=detail)
    except Exception:
        pass


@router.post("/login/mfa")
def login_mfa(request: Request, challenge: str = Form(...), code: str = Form(...), db: Session = Depends(get_db)):
    """The second step of signing in: the code from the authenticator app, or a recovery code."""
    ch = read_challenge(challenge)
    if not ch:
        return JSONResponse({"error": "That sign-in took too long. Enter your password again.", "restart": True}, status_code=401)
    if ip_blocked(request):
        return JSONResponse({"error": "Too many sign-in attempts from this network. Try again in 15 minutes."}, status_code=429)
    user = db.get(User, int(ch["sub"]))
    if user is None or not user.mfa_enabled or getattr(user, "active", 1) == 0:
        return JSONResponse({"error": "Enter your password again.", "restart": True}, status_code=401)
    mins = locked(user)
    if mins:
        return JSONResponse({"error": lock_message(mins), "restart": True}, status_code=429)
    _challenge_tries[ch["n"]] += 1
    if _challenge_tries[ch["n"]] > LOCK_AFTER:
        return JSONResponse({"error": "Too many wrong codes. Enter your password again.", "restart": True}, status_code=401)
    how = use_code(db, user, code)
    if not how:
        failed(db, user, request, "wrong two-step code")
        _audit(request, user, "sign_in_failed", "wrong two-step code", 401)
        return JSONResponse({"error": "That code didn't work. Use the newest code from your authenticator app, or a recovery code."}, status_code=401)
    _challenge_tries.pop(ch["n"], None)
    import auth
    resp = auth.finish_sign_in(request, db, user, detail="two-step login" + (" (recovery code)" if how == "recovery" else ""))
    if how == "recovery":
        body = json.loads(resp.body)
        body["notice"] = f"You used a recovery code. {recovery_left(user)} left. Make new ones in Settings > Security."
        resp.body = json.dumps(body).encode()
        resp.headers["content-length"] = str(len(resp.body))
    return resp


# ── setting it up ───────────────────────────────────────────────────────────
@router.get("/api/me/security")
def my_security(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row, _ = session_row(request)
    pol = policy_for(user, db)
    return {"mfa_enabled": bool(user.mfa_enabled), "mfa_required": mfa_required(user, db), "recovery_left": recovery_left(user) if user.mfa_enabled else 0,
            "policy": pol, "is_admin": (user.role or "admin") == "admin", "email": user.email,
            "sessions": [{"current": row is not None and r.sid == row.sid, "started": _aware(r.created_at).isoformat(), "last_seen": _aware(r.last_seen).isoformat(),
                          "ip": r.ip, "agent": r.agent[:80]} for r in db.scalars(select(UserSession).where(
                              UserSession.user_id == user.id, UserSession.revoked == 0, UserSession.kind == "user").order_by(UserSession.last_seen.desc())).all()
                         if _status(r)]}


@router.post("/api/me/2fa/start")
def mfa_start(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """A new secret to scan. It only takes effect once a code from it is entered (/api/me/2fa/enable)."""
    if user.mfa_enabled:
        return JSONResponse({"error": "Two-step login is already on. Turn it off first to move it to a new phone."}, status_code=400)
    secret = new_secret()
    user.mfa_secret = _seal(secret)
    user.mfa_last_step = 0
    db.commit()
    uri = otpauth_uri(secret, user.email)
    return {"secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)), "uri": uri, "qr": qr_svg(uri)}


@router.post("/api/me/2fa/enable")
async def mfa_enable(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    body = await request.json()
    secret = _open(user.mfa_secret)
    if user.mfa_enabled:
        return JSONResponse({"error": "Two-step login is already on."}, status_code=400)
    if not secret:
        return JSONResponse({"error": "Start again: scan a new code."}, status_code=400)
    step = check_code(secret, str(body.get("code") or ""))
    if not step:
        return JSONResponse({"error": "That code didn't match. Check the time on your phone is set automatically, and use the newest code."}, status_code=400)
    user.mfa_enabled, user.mfa_last_step = 1, step
    codes = new_recovery_codes(user)
    db.commit()
    row, _ = session_row(request)
    if row is not None:
        r = db.get(UserSession, row.sid)
        r.mfa_setup = 0
        db.commit()
    end_user_sessions(db, user.id, keep_sid=row.sid if row else "", why="mfa_enabled")
    _audit(request, user, "mfa_enabled")
    return {"ok": True, "recovery_codes": codes}


@router.post("/api/me/2fa/disable")
async def mfa_disable(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    from auth import verify_password
    body = await request.json()
    if mfa_required(user, db):
        return JSONResponse({"error": "Your clinic requires two-step login for your role. To move it to a new phone, ask your clinic's admin to reset it."}, status_code=403)
    if not verify_password(str(body.get("password") or ""), user.password_hash):
        return JSONResponse({"error": "Your password isn't right."}, status_code=400)
    if not use_code(db, user, str(body.get("code") or "")):
        return JSONResponse({"error": "That code didn't work."}, status_code=400)
    user.mfa_enabled, user.mfa_secret, user.mfa_recovery, user.mfa_last_step = 0, None, None, 0
    db.commit()
    _audit(request, user, "mfa_disabled")
    return {"ok": True}


@router.post("/api/me/2fa/recovery-codes")
async def mfa_new_codes(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    body = await request.json()
    if not user.mfa_enabled:
        return JSONResponse({"error": "Turn on two-step login first."}, status_code=400)
    if not use_code(db, user, str(body.get("code") or "")):
        return JSONResponse({"error": "That code didn't work."}, status_code=400)
    codes = new_recovery_codes(user)
    db.commit()
    _audit(request, user, "mfa_recovery_codes_replaced")
    return {"ok": True, "recovery_codes": codes}


@router.post("/api/me/sessions/end-others")
def end_my_other_sessions(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    row, _ = session_row(request)
    n = end_user_sessions(db, user.id, keep_sid=row.sid if row else "")
    _audit(request, user, "signed_out_other_sessions", f"{n} session(s)")
    return {"ok": True, "ended": n}


# ── the clinic's admins ─────────────────────────────────────────────────────
def _clinic_admin(user):
    if (user.role or "admin") != "admin":
        return JSONResponse({"error": "Only your clinic's admins can change security settings."}, status_code=403)
    return None


def _members(user, db):
    from auth import _clinic_members
    return _clinic_members(user, db)


@router.get("/api/clinic/security")
def clinic_security(user: User = Depends(require_user), db: Session = Depends(get_db)):
    pol = policy_for(user, db)
    out = {"policy": pol, "idle_choices": list(IDLE_CHOICES), "mfa_choices": list(MFA_POLICIES), "can_edit": (user.role or "admin") == "admin"}
    if out["can_edit"]:
        out["members"] = [{"id": m.id, "name": m.full_name or m.email, "email": m.email, "role": m.role or "admin", "mfa": bool(m.mfa_enabled),
                           "required": mfa_required(m, db), "locked": locked(m), "active": bool(getattr(m, "active", 1))} for m in _members(user, db)]
    return out


@router.put("/api/clinic/security")
async def set_clinic_security(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    bad = _clinic_admin(user)
    if bad:
        return bad
    body = await request.json()
    idle, mfa = body.get("idle_minutes"), body.get("mfa")
    if idle not in IDLE_CHOICES or mfa not in MFA_POLICIES:
        return JSONResponse({"error": "Choose an automatic sign-out time and a two-step login rule from the lists."}, status_code=400)
    key = _doc_org_key(user)
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == key, OrgSettings.category == CATEGORY))
    before = policy_for(user, db)
    data = {"idle_minutes": idle, "mfa": mfa, "updatedBy": user.email, "updatedAt": _now().isoformat()}
    if row:
        row.data = json.dumps(data)
    else:
        db.add(OrgSettings(org_key=key, category=CATEGORY, data=json.dumps(data)))
    db.commit()
    # open sessions follow the new sign-out time right away; people who now need two-step login set it up next page
    for m in _members(user, db):
        db.execute(update(UserSession).where(UserSession.user_id == m.id, UserSession.revoked == 0, UserSession.kind == "user")
                   .values(idle_minutes=idle, mfa_setup=1 if (mfa_required(m, db) and not m.mfa_enabled) else 0))
    db.commit()
    _audit(request, user, "security_settings_changed", f"sign-out {before['idle_minutes']}→{idle} min, two-step {before['mfa']}→{mfa}")
    return {"ok": True, "policy": policy_for(user, db)}


def reset_member_mfa(db, target, request, by=""):
    target.mfa_enabled, target.mfa_secret, target.mfa_recovery, target.mfa_last_step = 0, None, None, 0
    target.failed_logins, target.locked_until = 0, None
    db.commit()
    end_user_sessions(db, target.id, why="mfa_reset")
    _audit(request, target, "mfa_reset", f"by {by}")


@router.post("/api/clinic/members/{member_id}/reset-2fa")
def clinic_reset_mfa(member_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    """A lost phone: turn off this person's two-step login and sign them out; they set it up again at their next sign-in."""
    bad = _clinic_admin(user)
    if bad:
        return bad
    from auth import _same_clinic
    target = db.get(User, member_id)
    if not target or not _same_clinic(target, user):
        return JSONResponse({"error": "Employee not found."}, status_code=404)
    if target.id == user.id:
        return JSONResponse({"error": "Another admin at your clinic, or Althais, can reset your own two-step login."}, status_code=400)
    reset_member_mfa(db, target, request, by=user.email)
    return {"ok": True}


@router.post("/api/clinic/members/{member_id}/unlock")
def clinic_unlock(member_id: int, request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    bad = _clinic_admin(user)
    if bad:
        return bad
    from auth import _same_clinic
    target = db.get(User, member_id)
    if not target or not _same_clinic(target, user):
        return JSONResponse({"error": "Employee not found."}, status_code=404)
    target.failed_logins, target.locked_until = 0, None
    db.commit()
    _audit(request, target, "account_unlocked", f"by {user.email}")
    return {"ok": True}


# ── the setup page, and holding people there until it's done ───────────────
@router.get("/setup-2fa")
def setup_page(request: Request, db: Session = Depends(get_db)):
    from auth import current_user
    user = current_user(request, db)
    if not user:
        return RedirectResponse("/login", status_code=302)
    import main
    return main.templates.TemplateResponse(request, "setup_2fa.html", {"email": user.email, "required": mfa_required(user, db),
                                                                        "enabled": bool(user.mfa_enabled), "next": "/portal" if getattr(user, "portal_only", 0) else "/overview"})


_SETUP_OK = ("/setup-2fa", "/logout", "/api/me/2fa/", "/api/me/security", "/api/me", "/api/session/", "/static/", "/login", "/favicon")


async def middleware(request: Request, call_next):
    """Someone whose clinic requires two-step login and who hasn't set it up yet: only the setup page until they do.
    Also adds the idle sign-out script to every signed-in page, and standard security headers to every response."""
    path = request.url.path
    if not path.startswith(_SETUP_OK) and request.cookies.get(COOKIE_NAME):
        row, data = session_row(request)
        if row is not None and row.mfa_setup and not row.revoked and row.kind == "user":
            if path.startswith("/api/"):
                return JSONResponse({"error": "Set up two-step login first.", "detail": "Set up two-step login first.", "setup": "/setup-2fa"}, status_code=403)
            if request.method == "GET":
                return RedirectResponse("/setup-2fa", status_code=302)
    resp = await call_next(request)
    resp.headers.setdefault("X-Content-Type-Options", "nosniff")
    resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
    resp.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    if COOKIE_SECURE:
        resp.headers.setdefault("Strict-Transport-Security", "max-age=31536000; includeSubDomains")
    ctype = resp.headers.get("content-type", "")
    admin = path.startswith("/admin") and not path.startswith(("/admin/login", "/admin/api/"))
    signed_in = request.cookies.get(ADMIN_COOKIE if admin else COOKIE_NAME)
    if ctype.startswith("text/html") and signed_in and request.method == "GET" and resp.status_code == 200 and _guarded_page(path, admin):
        body = b"".join([chunk async for chunk in resp.body_iterator])
        tag = (f'<script src="/static/js/session-guard.js?v=2" data-admin="{1 if admin else 0}" defer></script>').encode()
        i = body.rfind(b"</body>")
        body = body[:i] + tag + body[i:] if i >= 0 else body + tag
        headers = {k: v for k, v in resp.headers.items() if k.lower() != "content-length"}
        from starlette.responses import Response
        return Response(content=body, status_code=resp.status_code, headers=headers, media_type=None)
    return resp


_PUBLIC = ("/login", "/logout", "/register", "/forgot-password", "/reset-password", "/verify-email", "/invite", "/legal", "/static", "/admin/login")


def _guarded_page(path, admin):
    if admin:
        return True
    if path.startswith(_PUBLIC) or path in ("/", ""):
        return False
    return path.startswith(("/overview", "/emr", "/revenue", "/staff", "/scribe", "/coding", "/settings", "/manager", "/onboarding", "/v4",
                            "/portal", "/set-password", "/setup-2fa", "/encounter"))


# ── the admin console's code ────────────────────────────────────────────────
_admin_fails = defaultdict(deque)


def admin_blocked(request) -> bool:
    q, now = _admin_fails[_client_ip(request)], time.time()
    while q and now - q[0] > LOCK_MINUTES * 60:
        q.popleft()
    return len(q) >= LOCK_AFTER


def admin_failed(request):
    _admin_fails[_client_ip(request)].append(time.time())


def admin_secret() -> str:
    return re.sub(r"\s", "", os.environ.get("ADMIN_TOTP_SECRET", "")).upper()


_admin_last_step = {"step": 0}


def admin_code_ok(code: str) -> bool:
    step = check_code(admin_secret(), code, _admin_last_step["step"])
    if step:
        _admin_last_step["step"] = step
        return True
    return False


@router.get("/admin/2fa")
def admin_2fa_page(request: Request):
    """For the founders: a secret to put in ADMIN_TOTP_SECRET (Railway) so the admin console asks for a code."""
    from auth import current_admin
    if not current_admin(request):
        return RedirectResponse("/admin/login", status_code=302)
    on = bool(admin_secret())
    secret = new_secret()
    uri = otpauth_uri(secret, "admin console", issuer="Althais Admin")
    import main
    return main.templates.TemplateResponse(request, "admin_2fa.html", {"on": on, "secret": " ".join(secret[i:i + 4] for i in range(0, len(secret), 4)),
                                                                       "raw": secret, "qr": qr_svg(uri)})


@router.post("/admin/2fa/check")
def admin_2fa_check(request: Request, secret: str = Form(...), code: str = Form(...)):
    """Check a code against a new secret before it goes into Railway, so nobody locks themselves out."""
    from auth import current_admin
    if not current_admin(request):
        return JSONResponse({"error": "Sign in to the admin console first."}, status_code=401)
    return {"ok": bool(check_code(re.sub(r"\s", "", secret).upper(), code))}


# ── Althais's own console: reset or unlock anyone ───────────────────────────
@router.post("/api/admin/users/{user_id}/reset-2fa")
def admin_reset_mfa(user_id: int, request: Request, db: Session = Depends(get_db)):
    from auth import log_admin_action, require_admin
    require_admin(request)
    target = db.get(User, user_id)
    if not target:
        return JSONResponse({"error": "Not found."}, status_code=404)
    reset_member_mfa(db, target, request, by="Althais")
    log_admin_action(db, "mfa_reset", target=target.email, detail="Two-step login reset; signed out everywhere")
    return {"ok": True}


@router.post("/api/admin/users/{user_id}/unlock")
def admin_unlock(user_id: int, request: Request, db: Session = Depends(get_db)):
    from auth import log_admin_action, require_admin
    require_admin(request)
    target = db.get(User, user_id)
    if not target:
        return JSONResponse({"error": "Not found."}, status_code=404)
    target.failed_logins, target.locked_until = 0, None
    db.commit()
    log_admin_action(db, "account_unlocked", target=target.email)
    return {"ok": True}
