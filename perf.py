"""
perf.py: keeps page-to-page navigation fast.

Every page and API call used to look up the signed-in session and user three or four times (once in each middleware and
again in the route), and the middlewares did it with blocking database calls on the server's one event loop, so while
one request waited on the database every other request waited too. A page fires about six API calls at once, so they
queued behind each other.

Here:
  * identity(request): the session row and user, read once per request in a worker thread and kept on request.state.
    The middlewares use it, and current_user() puts the same rows into its own database session instead of reading them again.
  * memo(): a dict that lives for one request, for values several helpers read (the clinic's entitlements). Cleared
    whenever that request writes anything, so a request that changes a setting reads the new value.
  * middleware: sets both up and adds a Server-Timing header (database time and query count), which shows in the
    browser's Network tab, so slow pages can be measured on the live site.
"""
import contextvars
import time

from sqlalchemy import event
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

_memo = contextvars.ContextVar("althais_req_memo", default=None)
_stats = contextvars.ContextVar("althais_req_stats", default=None)


def memo():
    """This request's memo dict, or None outside a request (scripts, background jobs: no caching there)."""
    return _memo.get()


@event.listens_for(Session, "after_flush")
def _forget_on_write(session, ctx):
    m = _memo.get()
    if m:
        m.clear()


def watch(engine):
    """Count query time per request (for the Server-Timing header)."""
    @event.listens_for(engine, "before_cursor_execute")
    def _start(conn, cursor, stmt, params, ctx, many):
        ctx._althais_t = time.perf_counter()

    @event.listens_for(engine, "after_cursor_execute")
    def _end(conn, cursor, stmt, params, ctx, many):
        s = _stats.get()
        t = getattr(ctx, "_althais_t", None)
        if s is not None and t is not None:
            s[0] += 1
            s[1] += time.perf_counter() - t


class Ident:
    __slots__ = ("token", "data", "session", "user")

    def __init__(self, token="", data=None, session=None, user=None):
        self.token, self.data, self.session, self.user = token, data, session, user


def _load(token):
    from auth import SessionLocal, User, decode_token
    from security import UserSession
    data = decode_token(token) if token else None
    if not data:
        return Ident(token, None)
    with SessionLocal() as db:
        row = db.get(UserSession, data["sid"]) if data.get("sid") else None
        try:
            u = db.get(User, int(data["sub"]))
        except (KeyError, ValueError, TypeError):
            u = None
        for o in (row, u):
            if o is not None:
                db.expunge(o)
    return Ident(token, data, row, u)


async def identity(request):
    """The signed-in session row and user for this request's cookie (either may be None), read once, off the event loop."""
    from auth import COOKIE_NAME
    token = request.cookies.get(COOKIE_NAME) or ""
    got = getattr(request.state, "althais_ident", None)
    if got is None or got.token != token:
        got = await run_in_threadpool(_load, token)
        request.state.althais_ident = got
    return got


def cached_ident(request, token):
    """The identity already read for this request with this token, or None."""
    try:
        got = getattr(request.state, "althais_ident", None)
    except Exception:
        return None
    return got if got is not None and got.token == token else None


def adopt(db, request, token):
    """Put the session and user rows this request already read into `db`, so db.get() finds them without a query."""
    got = cached_ident(request, token)
    if got is None:
        return
    keep = db.info.setdefault("althais_adopted", [])   # the identity map holds rows weakly: keep them alive for this session
    for o in (got.session, got.user):
        if o is not None:
            try:
                keep.append(db.merge(o, load=False))
            except Exception:
                pass


async def middleware(request, call_next):
    m, s = _memo.set({}), _stats.set([0, 0.0])
    t = time.perf_counter()
    try:
        resp = await call_next(request)
        st = _stats.get()
        total = (time.perf_counter() - t) * 1000
        resp.headers["Server-Timing"] = f'db;dur={st[1] * 1000:.0f};desc="{st[0]} queries", app;dur={total:.0f}'
        return resp
    finally:
        _memo.reset(m)
        _stats.reset(s)
