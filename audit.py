"""Access log: who looked at or changed patient and billing data, and when (HIPAA audit controls, 45 CFR 164.312(b)).

What gets recorded
  * Every call to the app's data APIs (/api/...), with the person, clinic, action, path, result, IP address and browser.
    Changes (POST, PUT, PATCH, DELETE) are always recorded. Reads (GET) of the same address by the same person are
    recorded once per READ_WINDOW, because the app re-reads patients and claims every 30 seconds while a tab is open.
  * Opening a patient chart, a visit or a claim in the app (the pages send a small "viewed" event, see app9.js).
  * Signing in, failed sign-ins, signing out, and the browser extension acting for a person.

What doesn't get recorded
  * Request or response bodies. The log says *that* someone opened patient 12345, not what the chart said.
  * Static files, the marketing site and health checks.

Integrity
  Each row carries an HMAC of its own contents, keyed with SECRET_KEY. /api/access-log/verify recomputes them, so a
  row changed directly in the database shows up as altered. Nothing in the app updates or deletes rows; keep them for
  at least six years (HIPAA documentation retention).

Who can read it
  A clinic's admins see their own clinic's log on Settings > Access Log (/v4/settings/access-log), with filters and a
  CSV export. Exporting is itself recorded.

Writing the log never breaks or slows a request: rows from the middleware are queued and written by one background
thread, and if a write fails the error is printed.
"""
import csv
import datetime as dt
import hashlib
import hmac
import io
import json
import queue
import re
import threading
import time
from datetime import timezone

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy import DateTime, Integer, String, Text, select
from sqlalchemy.orm import Mapped, Session, mapped_column

from auth import (COOKIE_NAME, SECRET_KEY, Base, SessionLocal, User, _org_namespace, decode_token, engine, get_db,
                  require_user)

router = APIRouter()

READ_WINDOW = 15 * 60          # seconds: the same person reading the same address is recorded once in this window
VIEW_WINDOW = 2 * 60           # seconds: the same person opening the same chart/claim again
MAX_ROWS = 500                 # one page of results


class AccessLog(Base):
    __tablename__ = "access_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    at: Mapped[dt.datetime] = mapped_column(DateTime, index=True, default=lambda: dt.datetime.now(timezone.utc))
    org_key: Mapped[str] = mapped_column(String(255), index=True, default="")
    user_id: Mapped[int] = mapped_column(Integer, index=True, nullable=True)
    user_email: Mapped[str] = mapped_column(String(255), default="")
    role: Mapped[str] = mapped_column(String(32), default="")
    on_behalf: Mapped[str] = mapped_column(String(255), default="")    # Althais admin using "View As", or "extension"
    action: Mapped[str] = mapped_column(String(32), index=True, default="")   # view | create | change | delete | export | sign_in | sign_in_failed | sign_out
    what: Mapped[str] = mapped_column(String(64), default="")          # patients, claims, patient, claim, extension...
    record_id: Mapped[str] = mapped_column(String(128), index=True, default="")   # MRN, claim number... when known
    method: Mapped[str] = mapped_column(String(8), default="")
    path: Mapped[str] = mapped_column(String(300), default="")
    status: Mapped[int] = mapped_column(Integer, default=0)
    ip: Mapped[str] = mapped_column(String(64), default="")
    agent: Mapped[str] = mapped_column(String(200), default="")
    detail: Mapped[str] = mapped_column(Text, default="")
    sig: Mapped[str] = mapped_column(String(64), default="")


Base.metadata.create_all(engine, tables=[AccessLog.__table__])

_FIELDS = ("at", "org_key", "user_id", "user_email", "role", "on_behalf", "action", "what", "record_id", "method", "path", "status", "ip", "agent", "detail")


def _sig(row: AccessLog) -> str:
    def norm(k):
        v = getattr(row, k)
        if k == "at" and v is not None:
            v = v.replace(tzinfo=None).isoformat(timespec="seconds")
        return "" if v is None else str(v)
    msg = "\x1f".join(norm(k) for k in _FIELDS).encode()
    return hmac.new(SECRET_KEY.encode(), msg, hashlib.sha256).hexdigest()


# ── recording ────────────────────────────────────────────────────────────────
_seen: dict = {}
_seen_lock = threading.Lock()


def _recent(key, window) -> bool:
    """True if this key was recorded within the window (and remember it now if not)."""
    now = time.monotonic()
    with _seen_lock:
        if len(_seen) > 20000:     # keep memory bounded
            for k in [k for k, t in _seen.items() if now - t > READ_WINDOW]:
                _seen.pop(k, None)
        t = _seen.get(key)
        if t is not None and now - t < window:
            return True
        _seen[key] = now
        return False


def client_ip(request: Request) -> str:
    fwd = request.headers.get("x-forwarded-for", "")
    if fwd:
        return fwd.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def record(db: Session | None = None, *, user=None, email: str = "", org_key: str = "", action: str, what: str = "", record_id: str = "",
           method: str = "", path: str = "", status: int = 0, ip: str = "", agent: str = "", detail: str = "", on_behalf: str = "") -> None:
    """Write one row. Safe to call from anywhere; never raises."""
    try:
        row = AccessLog(
            at=dt.datetime.now(timezone.utc).replace(microsecond=0),
            org_key=(org_key or getattr(user, "_org", "") or (_org_namespace(user) if isinstance(user, User) else ""))[:255],
            user_id=getattr(user, "id", None), user_email=(email or getattr(user, "email", "") or "")[:255],
            role=(getattr(user, "role", "") or "")[:32], on_behalf=(on_behalf or "")[:255], action=action[:32], what=(what or "")[:64],
            record_id=(record_id or "")[:128], method=(method or "")[:8], path=(path or "")[:300], status=int(status or 0),
            ip=(ip or "")[:64], agent=(agent or "")[:200], detail=(detail or "")[:1000])
        row.sig = _sig(row)
        if db is not None:
            db.add(row)
            db.commit()
            return
        for attempt in range(3):        # SQLite (local development) can be briefly locked by the request's own write
            try:
                with SessionLocal() as s:
                    s.add(row)
                    s.commit()
                return
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(0.25 * (attempt + 1))
                row = AccessLog(**{k: getattr(row, k) for k in _FIELDS}, sig=row.sig)
    except Exception as e:      # the log must never take the app down
        print(f"[ACCESS LOG] could not record {action} {path}: {e}")


# Rows from the middleware are written by one background thread, so a request never waits on the log
# (and, on SQLite in development, log writes never compete with each other for the database lock).
_queue: "queue.Queue" = queue.Queue(maxsize=10000)


def _writer():
    while True:
        kw = _queue.get()
        try:
            record(None, **kw)
        finally:
            _queue.task_done()


threading.Thread(target=_writer, name="access-log-writer", daemon=True).start()


def later(**kw) -> None:
    """Queue a row for the background writer. If the queue is ever full, write it here instead of dropping it."""
    try:
        _queue.put_nowait(kw)
    except queue.Full:
        record(None, **kw)


def drain() -> None:
    """Wait until every queued row is written (tests, and before reading the log)."""
    _queue.join()


# What the request was about, from its address. First match wins.
_WHAT = [
    (re.compile(r"^/api/patients/([^/]+)"), "patient"), (re.compile(r"^/api/patients"), "patients"),
    (re.compile(r"^/api/claims/([^/]+)"), "claim"), (re.compile(r"^/api/claims"), "claims"),
    (re.compile(r"^/api/extension/"), "extension"), (re.compile(r"^/api/integrations/"), "EMR connection"),
    (re.compile(r"^/api/billing/"), "billing"), (re.compile(r"^/api/code-note"), "coding"),
    (re.compile(r"^/api/(?:clinic|org)/"), "clinic settings"), (re.compile(r"^/api/access-log"), "access log"),
    (re.compile(r"^/api/(?:me|change-password)"), "own account"), (re.compile(r"^/api/portal/"), "staff portal"),
    (re.compile(r"^/api/(?:althea|assistant)"), "Althea"),
]
_EXPORT = re.compile(r"(\.csv|\.pdf|\.xlsx|/export|/download)(/|$|\?)", re.I)
# Addresses that hold no patient data and are hit constantly; not worth a row each time.
_SKIP = ("/api/branding", "/api/health", "/api/access-log/view")


def _what(path: str):
    for rx, label in _WHAT:
        m = rx.match(path)
        if m:
            return label, (m.group(1) if m.groups() else "")
    return path.split("/")[2] if path.count("/") >= 2 else "", ""


def _action(method: str, path: str) -> str:
    if _EXPORT.search(path):
        return "export"
    return {"GET": "view", "HEAD": "view", "POST": "create", "PUT": "change", "PATCH": "change", "DELETE": "delete"}.get(method, method.lower())


_users: dict = {}


def _user_for(user_id: int):
    """(id, email, role, org_key) for a user id, cached for a minute so the log doesn't add a query per request."""
    now = time.monotonic()
    hit = _users.get(user_id)
    if hit and now - hit[0] < 60:
        return hit[1]
    with SessionLocal() as db:
        u = db.get(User, user_id)
        val = (u.id, u.email, u.role or "", _org_namespace(u)) if u else None
    _users[user_id] = (now, val)
    return val


class _Who:
    def __init__(self, t):
        self.id, self.email, self.role, self._org = t


def _who_from_request(request: Request):
    """The signed-in person (from the session cookie), or the person who connected the extension (Bearer token)."""
    token = request.cookies.get(COOKIE_NAME)
    data = decode_token(token) if token else None
    if data:
        try:
            u = _user_for(int(data["sub"]))
        except (KeyError, ValueError, TypeError):
            u = None
        if u:
            return _Who(u), ("View As by " + str(data.get("by") or "an admin")) if data.get("imp") else ""
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("bearer ") and request.url.path.startswith("/api/extension/"):
        try:
            import extension_api
            with SessionLocal() as db:
                got = extension_api._auth(db, auth)
            if got and got[2]:
                u = _user_for(int(got[2]))
                if u:
                    return _Who(u), "browser extension"
        except Exception:
            pass
    return None, ""


async def middleware(request: Request, call_next):
    """Registered in main.py. Records data API calls after they finish."""
    path = request.url.path
    method = request.method
    response = await call_next(request)
    if not path.startswith("/api/") or path.startswith(_SKIP) or method == "OPTIONS":
        return response
    try:
        who, behalf = _who_from_request(request)
        if who is None:
            return response          # anonymous calls are refused by the API anyway; sign-in attempts are recorded in auth.login
        what, rid = _what(path)
        action = _action(method, path)
        if action == "view" and _recent((who.id, method, path), READ_WINDOW):
            return response
        later(user=who, org_key=who._org, action=action, what=what, record_id=rid, method=method, path=path,
              status=response.status_code, ip=client_ip(request), agent=request.headers.get("user-agent", ""), on_behalf=behalf)
    except Exception as e:
        print(f"[ACCESS LOG] {e}")
    return response


def record_request(request: Request, *, user=None, email="", action: str, what="", record_id="", status=200, detail="", on_behalf=""):
    """Explicit events (sign-in, sign-out, pages opened) with the request's IP and browser."""
    record(None, user=user, email=email, action=action, what=what, record_id=record_id, method=request.method, path=request.url.path,
           status=status, ip=client_ip(request), agent=request.headers.get("user-agent", ""), detail=detail, on_behalf=on_behalf)


# ── reading ──────────────────────────────────────────────────────────────────
def _admin(user: User):
    if (user.role or "") != "admin":
        return JSONResponse({"error": "Only your clinic's admins can see the access log."}, status_code=403)
    return None


def _query(db: Session, user: User, p: dict):
    q = select(AccessLog).where(AccessLog.org_key == _org_namespace(user))
    if p.get("user"):
        q = q.where(AccessLog.user_email == p["user"])
    if p.get("action"):
        q = q.where(AccessLog.action == p["action"])
    if p.get("record"):
        like = "%" + p["record"].strip()[:60] + "%"
        q = q.where((AccessLog.record_id.ilike(like)) | (AccessLog.path.ilike(like)))
    for k, op in (("since", "ge"), ("until", "le")):
        v = p.get(k)
        if v:
            try:
                d = dt.datetime.fromisoformat(v)
                if k == "until" and len(v) <= 10:
                    d = d + dt.timedelta(days=1) - dt.timedelta(seconds=1)
                q = q.where(AccessLog.at >= d if op == "ge" else AccessLog.at <= d)
            except ValueError:
                pass
    if p.get("before"):
        try:
            q = q.where(AccessLog.id < int(p["before"]))
        except ValueError:
            pass
    return q.order_by(AccessLog.id.desc())


def _row(r: AccessLog) -> dict:
    return {"id": r.id, "at": (r.at.replace(tzinfo=timezone.utc).isoformat() if r.at else ""), "user": r.user_email, "role": r.role, "on_behalf": r.on_behalf,
            "action": r.action, "what": r.what, "record": r.record_id, "method": r.method, "path": r.path, "status": r.status, "ip": r.ip,
            "agent": r.agent, "detail": r.detail}


@router.get("/api/access-log")
def list_log(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    bad = _admin(user)
    if bad:
        return bad
    drain()
    p = dict(request.query_params)
    rows = db.scalars(_query(db, user, p).limit(MAX_ROWS + 1)).all()
    org = _org_namespace(user)
    people = sorted({e for e in db.scalars(select(AccessLog.user_email).where(AccessLog.org_key == org).distinct()).all() if e})
    return {"rows": [_row(r) for r in rows[:MAX_ROWS]], "more": len(rows) > MAX_ROWS, "people": people}


@router.get("/api/access-log.csv")
def export_log(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    bad = _admin(user)
    if bad:
        return bad
    drain()
    rows = db.scalars(_query(db, user, dict(request.query_params)).limit(100000)).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Time (UTC)", "Person", "Role", "On behalf of", "Action", "What", "Record", "Method", "Address", "Result", "IP address", "Browser", "Detail"])
    for r in rows:
        w.writerow([r.at.isoformat(timespec="seconds") if r.at else "", r.user_email, r.role, r.on_behalf, r.action, r.what, r.record_id,
                    r.method, r.path, r.status, r.ip, r.agent, r.detail])
    name = "althais-access-log-" + dt.date.today().isoformat() + ".csv"
    return Response(buf.getvalue(), media_type="text/csv", headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.get("/api/access-log/verify")
def verify_log(user: User = Depends(require_user), db: Session = Depends(get_db)):
    """Recompute every row's signature for this clinic. Altered rows mean someone changed the database directly."""
    bad = _admin(user)
    if bad:
        return bad
    drain()
    n, altered = 0, []
    for r in db.scalars(select(AccessLog).where(AccessLog.org_key == _org_namespace(user)).order_by(AccessLog.id)).yield_per(1000):
        n += 1
        if not hmac.compare_digest(r.sig or "", _sig(r)):
            altered.append(r.id)
    return {"checked": n, "altered": altered[:200], "altered_count": len(altered)}


_VIEW_KINDS = {"patient", "claim", "visit"}


@router.post("/api/access-log/view")
async def page_view(request: Request, user: User = Depends(require_user)):
    """The app's pages report which chart, visit or claim a person opened (static/v4/app9.js)."""
    try:
        b = await request.json()
    except Exception:
        b = {}
    kind = str(b.get("what") or "")
    rid = re.sub(r"[^\w\-.]", "", str(b.get("id") or ""))[:128]
    if kind not in _VIEW_KINDS or not rid:
        return JSONResponse({"error": "Unknown record."}, status_code=400)
    if not _recent((user.id, "open", kind, rid), VIEW_WINDOW):
        extra = re.sub(r"[^\w\-.]", "", str(b.get("patient") or ""))[:64]
        record_request(request, user=user, action="view", what=kind, record_id=rid, detail=("patient " + extra) if extra else "",
                       on_behalf=_behalf(request))
    return {"ok": True}


def _behalf(request: Request) -> str:
    token = request.cookies.get(COOKIE_NAME)
    data = decode_token(token) if token else None
    return ("View As by " + str(data.get("by") or "an admin")) if data and data.get("imp") else ""
