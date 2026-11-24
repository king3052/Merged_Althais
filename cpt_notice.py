"""
cpt_notice.py: the American Medical Association's notices for CPT content, which Althais's CPT license requires.

  - SHORT: one line wherever CPT codes are shown (the Lost Money Report, its CSV worklist, Recovery, the app pages).
  - LONG: shown to each person once, before they first see CPT content, and accepted. A new version of the text
    (a different hash) is shown again.

The long text must be the one in the signed license agreement (its Appendix E). Put that text in
legal/ama_cpt_notice.txt and it replaces the placeholder below; `final()` says whether that's been done, and the
clinic checklist and /admin show it. Dental-only clinics (CDT, not CPT) don't get the long notice.

Turn the sign-in check off with CPT_NOTICE_ENFORCE=0 (tests do; production should not).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import html
import json
import os
from urllib.parse import quote

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select

router = APIRouter()

CATEGORY = "cpt_notice"
YEAR = dt.date.today().year
SHORT = f"CPT® copyright {YEAR} American Medical Association. All rights reserved. CPT is a registered trademark of the American Medical Association."

_PLACEHOLDER = f"""CPT® is a registered trademark of the American Medical Association (AMA). CPT codes, descriptions and other data are copyright {YEAR} American Medical Association. All rights reserved.

You may use CPT content in Althais only as part of using Althais for your organization's own billing and practice operations. You may not copy, distribute, sell or make derivative works of CPT content outside Althais.

Fee schedules, relative value units, conversion factors and related components are not assigned by the AMA, are not part of CPT, and the AMA does not recommend their use. The AMA does not directly or indirectly practice medicine or dispense medical services. The AMA assumes no liability for data contained or not contained herein.

U.S. Government end users: CPT is commercial technical data, which was developed exclusively at private expense by the American Medical Association. Use by the U.S. Government is subject to the restrictions in FAR 52.227-14 (Data Rights - General) and DFARS 252.227-7015 (Technical Data - Commercial Items)."""

_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "legal", "ama_cpt_notice.txt")


def long_text() -> str:
    try:
        with open(_FILE, encoding="utf-8") as f:
            t = f.read().strip()
            if t:
                return t
    except OSError:
        pass
    return _PLACEHOLDER


def final() -> bool:
    """True once the license agreement's own notice text is installed (legal/ama_cpt_notice.txt)."""
    return long_text() != _PLACEHOLDER


def version() -> str:
    return hashlib.sha256(long_text().encode()).hexdigest()[:12]


def enforced() -> bool:
    return os.environ.get("CPT_NOTICE_ENFORCE", "1") != "0"


def _row(db, org):
    from auth import OrgSettings
    return db.scalar(select(OrgSettings).where(OrgSettings.org_key == org, OrgSettings.category == CATEGORY))


def accepted(db, org: str, email: str) -> bool:
    row = _row(db, org)
    try:
        doc = json.loads(row.data or "{}") if row else {}
    except ValueError:
        doc = {}
    got = (doc.get("accepted") or {}).get((email or "").lower())
    return bool(got) and got.get("version") == version()


def accept(db, org: str, email: str) -> None:
    from auth import OrgSettings
    row = _row(db, org)
    if row is None:
        row = OrgSettings(org_key=org, category=CATEGORY, data="{}")
        db.add(row)
    try:
        doc = json.loads(row.data or "{}")
    except ValueError:
        doc = {}
    doc.setdefault("accepted", {})[(email or "").lower()] = {"version": version(), "at": dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"}
    row.data = json.dumps(doc)
    db.flush()


# pages that show CPT codes (their APIs aren't gated: the page is where a person first sees the content)
GATED = ("/v4/", "/lost-money", "/recovery")


def _needs(user, db) -> bool:
    import auth
    if user is None or getattr(user, "portal_only", 0) or getattr(user, "must_change_password", 0):
        return False
    org = auth._doc_org_key(user)
    if auth.org_practice(user, db) == "dental":
        return False
    return not accepted(db, org, user.email)


def _check(token: str) -> bool:
    import auth
    import perf
    with auth.SessionLocal() as db:
        ident = perf._load(token)
        u = db.get(auth.User, ident.user.id) if ident and ident.user is not None else None
        return _needs(u, db)


async def middleware(request: Request, call_next):
    path = request.url.path
    if enforced() and request.method == "GET" and path.startswith(GATED) and not path.startswith("/v4/dental/"):
        from auth import COOKIE_NAME
        from starlette.concurrency import run_in_threadpool
        token = request.cookies.get(COOKIE_NAME)
        if token:
            try:
                need = await run_in_threadpool(_check, token)
            except Exception:
                need = False        # never lock someone out because the check failed
            if need:
                nxt = path + ("?" + request.url.query if request.url.query else "")
                return RedirectResponse("/cpt-notice?next=" + quote(nxt), status_code=302)
    return await call_next(request)


def _safe_next(n: str) -> str:
    return n if n.startswith("/") and not n.startswith("//") else "/v4/overview"


@router.get("/cpt-notice", response_class=HTMLResponse)
def page(request: Request, next: str = "/v4/overview"):
    import lost_money as lm
    body = "".join(f"<p>{html.escape(p)}</p>" for p in long_text().split("\n\n"))
    nxt = html.escape(_safe_next(next), quote=True)
    return HTMLResponse(f"""<!DOCTYPE html><html lang=en><head><meta charset=utf-8><meta name=viewport content="width=device-width,initial-scale=1">
<title>CPT notice</title><style>{lm.STYLE}main{{max-width:720px}}</style></head><body><main>
<h1>Before you see CPT codes</h1><p class=sub>Althais shows CPT codes under a license from the American Medical Association, which asks everyone to read this once.</p>
<div class=card>{body}</div>
<form method=post action="/cpt-notice" style="margin-top:14px"><input type=hidden name=next value="{nxt}"><button>I've read this and agree</button></form>
</main></body></html>""", headers={"Cache-Control": "no-store"})


@router.post("/cpt-notice")
async def accept_route(request: Request):
    import auth
    form = await request.form()
    nxt = _safe_next(str(form.get("next") or "/v4/overview"))
    with auth.SessionLocal() as db:
        u = auth.current_user(request, db)
        if not u:
            return RedirectResponse("/login?next=" + quote(nxt), status_code=303)
        accept(db, auth._doc_org_key(u), u.email)
        db.commit()
        try:
            import audit
            audit.record(user=u, org_key=auth._doc_org_key(u), action="update", what="cpt_notice", method="POST", path="/cpt-notice",
                         status=303, ip=audit.client_ip(request), detail=f"Accepted the AMA CPT notice (version {version()})")
        except Exception:
            pass
    return RedirectResponse(nxt, status_code=303)


@router.get("/api/cpt-notice")
def status():
    return {"short": SHORT, "version": version(), "final": final()}
