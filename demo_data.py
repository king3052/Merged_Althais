"""
demo_data.py: demo clinics for showing the admin console, loaded and removed with one click (Organizations & Access).

Everything here is fictional: made-up clinic names and people, emails on the reserved .example domain (so nothing can
ever reach a real inbox, and "Hide Test Accounts" hides them), random passwords nobody knows (so nobody can sign in as
them), and activity rows marked seeded=1. Loading is idempotent; removing deletes exactly what loading created.
"""

import datetime as dt
import json
import random
import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import auth
from auth import ENTITLEMENTS_CATEGORY, OrgSettings, User, UserActivity, get_db, hash_password, log_admin_action, require_admin

router = APIRouter()

SITES = {  # site -> (email domain, plan)
    "Lakeshore Health": ("lakeshorehealth.example", ["suite"]),
    "Lakeshore Health Site 2": ("lakeshorehealth.example", ["suite"]),
    "Riverbend Medical Group": ("riverbendmedical.example", ["suite"]),
    "Riverbend Medical Group Site 2": ("riverbendmedical.example", ["suite"]),
    "Cedar Ridge Clinic": ("cedarridgeclinic.example", ["scribe", "coding", "insurance"]),
    "Cedar Ridge Clinic Site 2": ("cedarridgeclinic.example", ["scribe", "coding", "insurance"]),
}
PEOPLE = [  # first, last, name on notes and claims, site, July join day
    ("Arjun", "Mehta", "Dr. Arjun Mehta, MD", "Lakeshore Health", 10), ("Priya", "Raman", "Priya Raman, FNP-C", "Lakeshore Health", 12),
    ("Lauren", "Mitchell", "Lauren Mitchell, PA-C", "Lakeshore Health", 22),
    ("Meera", "Nair", "Dr. Meera Nair, MD", "Lakeshore Health Site 2", 4), ("Vikram", "Iyer", "Dr. Vikram Iyer, DO", "Lakeshore Health Site 2", 17),
    ("Sarah", "Whitfield", "Dr. Sarah Whitfield, MD", "Lakeshore Health Site 2", 21),
    ("Ananya", "Krishnan", "Dr. Ananya Krishnan, MD", "Riverbend Medical Group", 7), ("Megan", "O'Connell", "Megan O'Connell, NP", "Riverbend Medical Group", 12),
    ("Aditya", "Rao", "Dr. Aditya Rao, MD", "Riverbend Medical Group", 26),
    ("Marcus", "Okafor", "Dr. Marcus Okafor, DO", "Riverbend Medical Group Site 2", 14), ("Rohan", "Desai", "Dr. Rohan Desai, MD", "Riverbend Medical Group Site 2", 28),
    ("Emily", "Tran", "Dr. Emily Tran, MD", "Cedar Ridge Clinic", 3), ("Kavya", "Reddy", "Dr. Kavya Reddy, MD", "Cedar Ridge Clinic", 24),
    ("Neha", "Sharma", "Neha Sharma, PA-C", "Cedar Ridge Clinic", 24),
    ("Jonathan", "Pierce", "Dr. Jonathan Pierce, MD", "Cedar Ridge Clinic Site 2", 16), ("Sanjay", "Patel", "Dr. Sanjay Patel, MD", "Cedar Ridge Clinic Site 2", 16),
]
DOMAINS = {d for d, _ in SITES.values()}
START_H = [7] * 3 + [8] * 7 + [9] * 5 + [10] * 2 + [11] + [12] + [13] * 2


def _email(first, last, site):
    return f"{first[0].lower()}{last.lower().replace(chr(39), '')}@{SITES[site][0]}"


def _demo_users(db):
    return [u for u in db.scalars(select(User).where(User.email.like("%.example"))) if u.email.split("@")[-1] in DOMAINS]


def _history(rnd, user, joined, last_login):
    """Weekday sign-ins that ramp up after joining; codes and claims always after that day's first sign-in."""
    days = [joined.date() + dt.timedelta(days=i) for i in range((last_login.date() - joined.date()).days)]
    wd = [d for d in days if d.weekday() < 5] or days or [joined.date()]
    weights = [0.35 + 0.65 * min(1, (d - joined.date()).days / 28) for d in wd]
    picks = sorted(rnd.choices(wd, weights=weights, k=max(1, user.login_count - 1)))
    per_day = {}
    for d in picks:
        per_day[d] = per_day.get(d, 0) + 1
    claims = {}
    for d in rnd.choices(sorted(per_day), k=user.claims_submitted or 0):
        claims[d] = claims.get(d, 0) + 1
    clock = lambda d, h, m: dt.datetime(d.year, d.month, d.day, h, m, rnd.randint(0, 59))
    out = []
    for d, n in sorted(per_day.items()):
        first = clock(d, rnd.choice(START_H), rnd.randint(0, 59))
        sessions = [first] + sorted(min(first + dt.timedelta(minutes=rnd.randint(150, 420)), clock(d, 18, rnd.randint(0, 50))) for _ in range(n - 1))
        out += [("login", 1, s) for s in sessions]
        end = clock(d, 18, 55)
        after = lambda: min(rnd.choice(sessions) + dt.timedelta(minutes=rnd.randint(6, 170)), end)
        if rnd.random() < 0.68:
            out += [("codes", rnd.randint(2, 7), after()) for _ in range(rnd.choice([1, 1, 2, 2, 3]))]
        out += [("claim_sent", 1, after()) for _ in range(claims.get(d, 0))]
    out.append(("login", 1, last_login))
    return out


def load(db) -> int:
    rnd = random.Random(20260701)
    made = 0
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    for first, last, prov, site, day in PEOPLE:
        email = _email(first, last, site)
        joined = dt.datetime(2026, 7, day, rnd.randint(8, 18), rnd.randint(0, 59))
        last_login = min(now - dt.timedelta(hours=rnd.randint(2, 30 * 24)), now)
        if last_login < joined + dt.timedelta(days=21):
            last_login = joined + dt.timedelta(days=21)
        login_count, claims = rnd.randint(18, 140), rnd.randint(0, 85)
        if db.scalar(select(User).where(User.email == email)):
            continue
        u = User(email=email, password_hash=hash_password(secrets.token_urlsafe(24)), full_name=f"{first} {last}", organization=site, role="provider",
                 provider_name=prov, onboarding_complete=1, email_verified=1, active=1, login_count=login_count, claims_submitted=claims,
                 created_at=joined, last_login=last_login)
        db.add(u)
        db.flush()
        db.add_all([UserActivity(user_id=u.id, org_key=site, kind=k, count=c, at=t, seeded=1) for k, c, t in _history(rnd, u, joined, last_login)])
        made += 1
    for site, (_, products) in SITES.items():
        if not db.scalar(select(OrgSettings).where(OrgSettings.org_key == site, OrgSettings.category == ENTITLEMENTS_CATEGORY)):
            db.add(OrgSettings(org_key=site, category=ENTITLEMENTS_CATEGORY,
                               data=json.dumps({"rev": 0, "products": products, "althea": True, "manager": True, "demo": True})))
    db.commit()
    return made


def remove(db) -> int:
    users = _demo_users(db)
    ids = [u.id for u in users]
    if ids:
        db.execute(delete(UserActivity).where(UserActivity.user_id.in_(ids)))
    for u in users:
        db.delete(u)
    for row in db.scalars(select(OrgSettings).where(OrgSettings.org_key.in_(list(SITES)))):
        if json.loads(row.data or "{}").get("demo"):
            db.delete(row)
    db.commit()
    return len(ids)


@router.get("/api/admin/demo-data")
def status(db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    return {"loaded": len(_demo_users(db)), "total": len(PEOPLE), "sites": list(SITES)}


@router.post("/api/admin/demo-data")
async def change(request: Request, db: Session = Depends(get_db), _: bool = Depends(require_admin)):
    action = (await request.json()).get("action")
    if action == "load":
        n = load(db)
        log_admin_action(db, "demo_data_loaded", target="demo clinics", detail=f"Loaded {n} demo clinicians across {len(SITES)} demo clinic sites")
        return {"ok": True, "added": n}
    if action == "remove":
        n = remove(db)
        log_admin_action(db, "demo_data_removed", target="demo clinics", detail=f"Removed {n} demo clinicians and their activity")
        return {"ok": True, "removed": n}
    return JSONResponse({"error": "Use load or remove."}, status_code=400)
