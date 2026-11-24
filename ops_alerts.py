"""
ops_alerts.py: tell the Althais team, by email, when something a clinic depends on breaks, instead of leaving it in the
logs. A failed Claim.MD pull, a failed hourly payment check, a failed report or any server error.

  - Recipients: ALERT_EMAIL (comma-separated). Without it, alerts are only logged (and /api/admin/alerts says so).
  - Never more than one email per kind of problem per clinic every ALERT_EVERY_MINUTES (default 360), so a broken
    connection doesn't send one an hour.
  - No clinic data in alerts: the clinic's name, what failed, the error's type and a short message with digits and
    email addresses removed (they could be claim numbers or patients' details).
  - Every alert is also logged as one JSON line with severity ERROR, which Google Cloud Logging picks up, so a
    log-based alert policy (deploy/gcloud-alerts.sh) catches it even when email is down.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
from collections import deque
from typing import Optional

from fastapi import APIRouter, Depends, Request

router = APIRouter()

_lock = threading.Lock()
_last: dict = {}
RECENT: deque = deque(maxlen=100)


def recipients() -> list:
    return [x.strip() for x in os.environ.get("ALERT_EMAIL", "").split(",") if "@" in x]


def _every() -> dt.timedelta:
    try:
        return dt.timedelta(minutes=int(os.environ.get("ALERT_EVERY_MINUTES", "360")))
    except ValueError:
        return dt.timedelta(hours=6)


def scrub(text: str) -> str:
    """Remove anything that could be clinic data: email addresses, long numbers (claim numbers, member IDs, NPIs)."""
    t = re.sub(r"[\w.+-]+@[\w-]+\.[\w.]+", "[email]", str(text or ""))
    t = re.sub(r"\d{4,}", "[n]", t)
    return re.sub(r"\s+", " ", t)[:300]


def notify(kind: str, org: str = "", error: Optional[BaseException] = None, detail: str = "", now: Optional[dt.datetime] = None) -> bool:
    """Log the problem, and email the team unless the same kind for the same clinic was sent recently. Never raises.
    Returns True when an email was sent."""
    try:
        now = now or dt.datetime.utcnow()
        what = scrub(detail or (str(error) if error else ""))
        etype = type(error).__name__ if error else ""
        entry = {"severity": "ERROR", "alert": kind, "clinic": org or "", "error": etype, "detail": what,
                 "at": now.replace(microsecond=0).isoformat() + "Z"}
        print(json.dumps(entry), flush=True)
        RECENT.appendleft(entry)
        key = (kind, org or "")
        with _lock:
            last = _last.get(key)
            if last and now - last < _every():
                return False
            _last[key] = now
        to = recipients()
        if not to:
            return False
        from auth import send_email
        body = (f"<p><b>{kind}</b>{' for <b>' + _h(org) + '</b>' if org else ''} at {entry['at']}.</p>"
                f"<p>{_h(etype)}{': ' + _h(what) if what else ''}</p>"
                "<p>Check Cloud Run logs for the service <code>althais</code> around that time. "
                "This alert won't repeat for the same problem and clinic for a few hours.</p>")
        ok = False
        for r in to:
            ok = send_email(r, f"[Althais alert] {kind}" + (f" — {org}" if org else ""), body) or ok
        return ok
    except Exception:
        return False


def _h(s) -> str:
    import html
    return html.escape(str(s or ""))


async def middleware(request: Request, call_next):
    """Any unhandled server error becomes an alert (the path's shape, never its contents)."""
    try:
        return await call_next(request)
    except Exception as e:
        path = re.sub(r"/\d+", "/:id", request.url.path)[:120]
        notify("Server error", detail=f"{request.method} {path}", error=e)
        raise


@router.get("/api/admin/alerts")
def recent_alerts(request: Request):
    from auth import require_admin
    require_admin(request)
    return {"recipients": len(recipients()), "emailOn": bool(recipients()), "recent": list(RECENT)[:50]}
