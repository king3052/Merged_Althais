"""
staff_lifecycle.py — what Althais keeps doing after the paperwork is in: reminders, renewals and training cycles.

Runs from the periodic job in main.py (and whenever a manager or employee opens their page), so it must be safe to
run any number of times: every reminder is recorded in staff_notifications and never sent twice.

    Reminders   onboarding items (7, 3, 1 days before, due today, overdue), corrections the employee needs to make,
                a weekly digest of unfinished onboarding, and credential renewals (90, 60, 30, 7 days, expired).
                Each goes out once per window; when the job misses a day, only the current window is sent, never a
                burst of stale ones. Clinics set the windows in Staff > Onboarding Templates > Reminders.
    Renewals    computed from verified credentials: anything expiring within the renewal horizon shows up as the
                employee's next action, and a verified renewal (staff_onboarding.record_credential) clears it and
                starts the next cycle by itself.
    Training    completed training that has expired is assigned again (the old completion stays in its history).
"""

import datetime as dt
import html as _html
import os

from sqlalchemy import select

import staff_onboarding as so

DEFAULT_RULES = {
    "taskDays": [7, 3, 1],          # reminders before an onboarding item's deadline (plus due today and overdue)
    "renewalDays": [90, 60, 30, 7], # reminders before a credential expires (plus when it has expired)
    "trainingRenewalDays": [30, 7], # reminders before completed training expires
    "correctionFollowupDays": 3,    # a second nudge when a sent-back item still isn't fixed
    "weeklyDigest": True,           # one weekly summary of unfinished onboarding
}
RENEWAL_HORIZON = 90
ONBOARDING = ("INVITE_ACCEPTED", "ONBOARDING", "PENDING_REVIEW")


def rules(doc: dict) -> dict:
    raw = (doc.get("clinic") or {}).get("reminderRules") or {}
    out = dict(DEFAULT_RULES)
    for k in ("taskDays", "renewalDays", "trainingRenewalDays"):
        if isinstance(raw.get(k), list):
            days = sorted({int(x) for x in raw[k] if str(x).lstrip("-").isdigit() and 0 < int(x) <= 365}, reverse=True)
            out[k] = days[:6] or out[k]
    try:
        out["correctionFollowupDays"] = max(1, min(30, int(raw.get("correctionFollowupDays", out["correctionFollowupDays"]))))
    except (TypeError, ValueError):
        pass
    if isinstance(raw.get("weeklyDigest"), bool):
        out["weeklyDigest"] = raw["weeklyDigest"]
    return out


def _window(days_left: int, offsets) -> int:
    """The reminder window we're in: the smallest offset that's still >= days_left (None when it's further off)."""
    fits = [o for o in offsets if days_left <= o]
    return min(fits) if fits else None


def _days(iso: str, today: dt.date):
    try:
        return (dt.date.fromisoformat(iso) - today).days
    except (TypeError, ValueError):
        return None


# ──────────────────────────────────────────────────────────────────────────
#  Renewals
# ──────────────────────────────────────────────────────────────────────────
def renewal_items(doc: dict, p: dict, today: dt.date = None, horizon: int = RENEWAL_HORIZON) -> list:
    """Verified credentials that expire within the horizon (or already have), most urgent first."""
    today = today or so._today()
    out = []
    for c in doc["credentials"]:
        if c.get("personId") != p["id"] or c.get("status") != "verified" or not c.get("expires"):
            continue
        days = _days(c["expires"], today)
        if days is None or days > horizon:
            continue
        t = so._find(p["requirements"], c.get("fromRequirement")) or {}
        title = t.get("title") or so.CREDENTIAL_LABELS.get(c.get("type"), "Credential")
        stage = "expired" if days < 0 else "urgent" if days <= 7 else "soon" if days <= 30 else "upcoming"
        out.append({"credentialId": c["id"], "requirementKey": c.get("fromRequirement", ""), "title": title,
                    "expires": c["expires"], "days": days, "stage": stage,
                    "section": "credentials" if t.get("credentialType") else "documents"})
    return sorted(out, key=lambda x: x["days"])


# ──────────────────────────────────────────────────────────────────────────
#  Training cycles
# ──────────────────────────────────────────────────────────────────────────
def cycle_training(db, org_key: str, doc: dict, today: dt.date = None) -> bool:
    """Completed training past its expiration is assigned again. Returns True when the staff record changed."""
    today = today or so._today()
    changed = False
    for r in doc["trainings"]:
        if not r.get("completed") or not r.get("expires"):
            continue
        days = _days(r["expires"], today)
        if days is None or days >= 0:
            continue
        p = so.person_of(doc, r.get("personId"))
        if not p or p.get("lifecycle") in ("SUSPENDED", "OFFBOARDED"):
            continue
        r.setdefault("history", []).insert(0, {k: r.get(k) for k in ("completed", "expires", "version", "score", "certificate", "startedAt")})
        due = (today + dt.timedelta(days=14)).isoformat()
        r.update(completed="", expires="", startedAt="", score=None, certificate="", due=due, assignedAt=today.isoformat(), expiredOn=today.isoformat())
        t = next((t for t in p["requirements"] if t.get("type") == "training" and t.get("trainingKey") == r.get("trainingKey")), None)
        if t:
            so.set_task(p, t["key"], "NOT_STARTED")
            t["dueDate"] = due
        so.audit(db, org_key, None, "training_reassigned", p, "training", r["id"], f"{r.get('course')} expired and was assigned again (due {so._fmt_date(due)})")
        so.refresh(p)
        changed = True
    return changed


# ──────────────────────────────────────────────────────────────────────────
#  Reminders
# ──────────────────────────────────────────────────────────────────────────
def _portal_url() -> str:
    return f"{(os.environ.get('APP_URL') or 'https://app.althais.com').rstrip('/')}/portal"


def _notify_once(db, org_key: str, p: dict, ref: str, kind: str, due: str, subject: str, body: str) -> bool:
    N = so.StaffNotification
    if not p.get("email") or db.scalar(select(N.id).where(N.org_key == org_key, N.person_id == p["id"], N.ref == ref,
                                                         N.kind == kind, N.due == due)):
        return False
    sent = so.send_email(p["email"], subject, so._email_html(subject, body, _portal_url(), "Open Staff Portal",
                                                             note="You're getting this because your clinic manages your staff records on Althais."))
    db.add(N(org_key=org_key, person_id=p["id"], ref=ref, kind=kind, due=due, recipient=p["email"], sent=1 if sent else 0))
    db.flush()
    return True


def _sent_today(db, org_key: str, p: dict, today: dt.date) -> bool:
    N = so.StaffNotification
    start = dt.datetime.combine(today, dt.time.min)
    return bool(db.scalar(select(N.id).where(N.org_key == org_key, N.person_id == p["id"], N.created_at >= start).limit(1)))


def _last_rejection(db, org_key: str, p: dict, key: str):
    D = so.StaffDocument
    return db.scalar(select(D).where(D.org_key == org_key, D.person_id == p["id"], D.requirement_key == key, D.status == "REJECTED",
                                     D.superseded == 0).order_by(D.id.desc()).limit(1))


def run(db, org_key: str, doc: dict, row=None) -> int:
    """Everything that's due today: training cycles, then reminders. Returns the number of reminders sent."""
    today = so._today()
    if cycle_training(db, org_key, doc, today) and row is not None:
        so.save_staff(db, org_key, row, doc)
    R = rules(doc)
    clinic = _html.escape(so._clinic_name(org_key))
    n = 0
    for p in doc["people"]:
        lc = p.get("lifecycle")
        if lc not in ONBOARDING + ("ACTIVE",) or not p.get("userId"):
            continue
        open_items = []
        for t in p["requirements"]:
            if t.get("owner") == "manager" or t.get("status") in ("COMPLETE", "NEEDS_REVIEW"):
                continue
            open_items.append(t)
            title = _html.escape(t["title"])
            # corrections: right away, then once more if it's still not fixed
            if t.get("status") == "WAITING_ON_EMPLOYEE" and t.get("reviewNote"):
                d = _last_rejection(db, org_key, p, t["key"])
                ref_due = str(d.id) if d else "info"
                n += _notify_once(db, org_key, p, t["key"], "correction", ref_due, f"Action needed: {t['title']}",
                                  f"{title} for {clinic} needs your attention: {_html.escape(t['reviewNote'])}")
                since = (so._aware(d.uploaded_at) if d else None)
                if since and (dt.datetime.now(dt.timezone.utc) - since).days >= R["correctionFollowupDays"]:
                    n += _notify_once(db, org_key, p, t["key"], "correction_followup", ref_due, f"Still needed: {t['title']}",
                                      f"{title} for {clinic} still needs a new upload: {_html.escape(t['reviewNote'])}")
            days = _days(t.get("dueDate"), today)
            if days is None:
                continue
            when = so._fmt_date(t["dueDate"])
            offsets = sorted(set(R["taskDays"]) | ({int(t["reminderDaysBefore"])} if str(t.get("reminderDaysBefore", "")).isdigit() and int(t["reminderDaysBefore"]) > 0 else set()))
            if days < 0:
                kind, subject, body = "overdue", f"Overdue: {t['title']}", f"{title} for {clinic} was due {when}. Please finish it as soon as you can."
            elif days == 0:
                kind, subject, body = "due_today", f"Due today: {t['title']}", f"{title} for {clinic} is due today."
            else:
                w = _window(days, offsets)
                if w is None:
                    continue
                kind = f"due_{w}"
                subject = f"Due tomorrow: {t['title']}" if days == 1 else f"Reminder: {t['title']} is due {when}"
                body = f"{title} for {clinic} is due {'tomorrow' if days == 1 else when}."
            n += _notify_once(db, org_key, p, t["key"], kind, t["dueDate"], subject, body)

        # credential renewals
        for it in renewal_items(doc, p, today):
            title = _html.escape(it["title"])
            if it["days"] < 0:
                n += _notify_once(db, org_key, p, it["credentialId"], "credential_expired", it["expires"], f"Your {it['title']} has expired",
                                  f"Your {title} on file with {clinic} expired {so._fmt_date(it['expires'])}. Upload your renewed one in the Staff Portal.")
                continue
            w = _window(it["days"], R["renewalDays"])
            if w is None:
                continue
            urgent = "Urgent: " if it["days"] <= 7 else ""
            n += _notify_once(db, org_key, p, it["credentialId"], f"renewal_{w}", it["expires"],
                              f"{urgent}Your {it['title']} expires {so._fmt_date(it['expires'])}",
                              f"Your {title} on file with {clinic} expires {so._fmt_date(it['expires'])} ({it['days']} days). "
                              "When you have the renewed one, upload it in the Staff Portal and Althais will update your record.")

        # training that's about to expire
        for r in doc["trainings"]:
            if r.get("personId") != p["id"] or not r.get("completed") or not r.get("expires"):
                continue
            days = _days(r["expires"], today)
            w = _window(days, R["trainingRenewalDays"]) if days is not None and days >= 0 else None
            if w is not None:
                n += _notify_once(db, org_key, p, r["id"], f"training_renewal_{w}", r["expires"], f"{r.get('course')} expires {so._fmt_date(r['expires'])}",
                                  f"Your {_html.escape(r.get('course') or 'training')} for {clinic} expires {so._fmt_date(r['expires'])}. "
                                  "You'll be asked to complete it again then.")
        # weekly digest of unfinished onboarding, only on a day nothing else went out
        if R["weeklyDigest"] and lc in ONBOARDING and open_items and not _sent_today(db, org_key, p, today):
            y, w, _ = today.isocalendar()
            items = "".join(f"<br>• {_html.escape(t['title'])}" + (f" (due {so._fmt_date(t['dueDate'])})" if t.get("dueDate") else "") for t in open_items[:8])
            n += _notify_once(db, org_key, p, "onboarding", "digest", f"{y}-W{w:02d}", f"Your onboarding with {so._clinic_name(org_key)}",
                              f"Here's what's left to finish your onboarding with {clinic}:{items}")
    if n:
        db.commit()
    return n
