"""Patient engagement: online booking from real provider hours, two-way texting, appointment reminders, hygiene recall
campaigns, digital intake and medical history forms that write into the chart, and post-visit review requests.

Texts go through dental_vendors' SMS provider (a mock until a contract is signed). They carry as little PHI as
possible: a first name, a date and time, a link; never procedures, diagnoses or balances beyond the amount due.
Patients who reply STOP are never texted again (TCPA). Review requests go to everyone alike; Google doesn't allow asking
only happy patients, so a low rating is passed to the office as a follow-up, never used to hide the public link.
"""
import datetime as dt
import os
import re
import secrets
import time
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

import audit
import auth
import dental_db as m
import dental_rcm as rcm
import dental_vendors as vendors
from dental_api import router, public, need, C, patient, patient_dict, save_patient, providers, today, next_mrn, appt_dict

# what patients can book online: (label, minutes, provider kind, operatory kind)
BOOKABLE = {
    "cleaning": ("Cleaning and checkup", 60, "hygienist", "hygiene"),
    "new": ("New patient exam and X-rays", 90, "dentist", "restorative"),
    "emergency": ("Toothache or emergency", 30, "dentist", "restorative"),
    "consult": ("Consultation (crowns, implants, cosmetic)", 30, "dentist", "consult"),
}


def _base():
    return os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")


def _first(name: str) -> str:
    return ((name.split(", ") + [""])[1] or name).split(" ")[0] or "there"


def _hm(s: str) -> dt.time:
    h, mi = s.split(":")
    return dt.time(int(h), int(mi))


def send_sms(db, org: str, mrn: str, phone: str, body: str, kind: str, appt_id=None, pt: dict = None) -> m.Message:
    if pt is not None and pt.get("sms_opt_out"):
        msg = m.Message(org_key=org, mrn=mrn, direction="out", phone=phone, body=body, kind=kind, status="opted_out", appt_id=appt_id)
        db.add(msg)
        return msg
    res = vendors.vendor("sms").send(phone, body)
    msg = m.Message(org_key=org, mrn=mrn, direction="out", phone=phone, body=body, kind=kind, status=res.status, ref=res.ref, appt_id=appt_id)
    db.add(msg)
    return msg


# ── availability and online booking ─────────────────────────────────────────────────────────────────────────────
def slots(db, org: str, location_id: int, kind: str, days: int = 14, start: dt.date = None, provider_id: int = None, limit: int = 60) -> list:
    """Open times: inside the provider's hours (not lunch), with the provider and a chair of the right kind free."""
    label, minutes, pkind, okind = BOOKABLE[kind]
    start = start or dt.date.today() + dt.timedelta(days=1)
    provs = {p.id: p for p in db.scalars(select(m.Provider).where(m.Provider.org_key == org, m.Provider.active == 1, m.Provider.kind == pkind))}
    if provider_id:
        provs = {k: v for k, v in provs.items() if k == provider_id}
    ops = db.scalars(select(m.Operatory).where(m.Operatory.org_key == org, m.Operatory.location_id == location_id)).all()
    ops_ok = [o for o in ops if o.kind == okind] or [o for o in ops if o.kind != "hygiene"] or ops
    hours = db.scalars(select(m.ProviderHours).where(m.ProviderHours.org_key == org, m.ProviderHours.location_id == location_id, m.ProviderHours.provider_id.in_(list(provs) or [0]))).all()
    t0, t1 = dt.datetime.combine(start, dt.time()), dt.datetime.combine(start + dt.timedelta(days=days), dt.time())
    busy = db.scalars(select(m.Appointment).where(m.Appointment.org_key == org, m.Appointment.start >= t0 - dt.timedelta(hours=8), m.Appointment.start < t1,
                                                 m.Appointment.status.notin_(("cancelled", "no_show")))).all()
    by_prov, by_op = defaultdict(list), defaultdict(list)
    for a in busy:
        span = (a.start, a.start + dt.timedelta(minutes=a.minutes))
        if a.provider_id:
            by_prov[a.provider_id].append(span)
        if a.operatory_id:
            by_op[a.operatory_id].append(span)
    free = lambda spans, s, e: all(not (x[0] < e and x[1] > s) for x in spans)
    out = []
    now = dt.datetime.now()
    for d in range(days):
        day = start + dt.timedelta(days=d)
        for h in sorted((h for h in hours if h.weekday == day.weekday()), key=lambda h: h.start):
            s = dt.datetime.combine(day, _hm(h.start))
            end_day = dt.datetime.combine(day, _hm(h.end))
            lunch = (dt.datetime.combine(day, _hm(h.lunch_start)), dt.datetime.combine(day, _hm(h.lunch_end))) if h.lunch_start else None
            while s + dt.timedelta(minutes=minutes) <= end_day:
                e = s + dt.timedelta(minutes=minutes)
                if s > now and (not lunch or not (lunch[0] < e and lunch[1] > s)) and free(by_prov[h.provider_id], s, e):
                    chairs = [h.operatory_id] if h.operatory_id else [o.id for o in ops_ok]
                    op = next((o for o in chairs if o and free(by_op[o], s, e)), None)
                    if op:
                        out.append({"start": s.isoformat(timespec="minutes"), "provider_id": h.provider_id, "provider": provs[h.provider_id].name, "operatory_id": op, "minutes": minutes})
                s += dt.timedelta(minutes=30 if minutes >= 60 else 15)
            if len(out) >= limit:
                return out
    return out


@router.get("/availability")
def availability(location_id: int, kind: str = "cleaning", days: int = 14, provider_id: int = 0, c: C = Depends(need("read"))):
    if kind not in BOOKABLE:
        raise HTTPException(400, "Unknown visit type")
    return slots(c.db, c.org, location_id, kind, days, provider_id=provider_id or None)


_hits = defaultdict(list)


def _limit(ip: str, n=20, per=3600):
    now = time.time()
    _hits[ip] = [t for t in _hits[ip] if now - t < per]
    if len(_hits[ip]) >= n:
        raise HTTPException(429, "Too many requests. Please call the office.")
    _hits[ip].append(now)


def _org_ok(db, org):
    locs = db.scalars(select(m.Location).where(m.Location.org_key == org, m.Location.active == 1, m.Location.online_booking == 1)).all()
    if not locs:
        raise HTTPException(404, "Online booking isn't available for this office")
    return locs


@public.get("/book/{org}")
def book_info(org: str, db: Session = Depends(auth.get_db)):
    locs = _org_ok(db, org)
    return {"locations": [{"id": l.id, "name": l.name, "addr": f"{l.addr}, {l.city}, {l.state}", "phone": l.phone} for l in locs],
            "kinds": [{"key": k, "label": v[0], "minutes": v[1]} for k, v in BOOKABLE.items()]}


@public.get("/book/{org}/slots")
def book_slots(org: str, location_id: int, kind: str, db: Session = Depends(auth.get_db)):
    _org_ok(db, org)
    if kind not in BOOKABLE:
        raise HTTPException(400, "Unknown visit type")
    return [{"start": s["start"], "provider": s["provider"], "token": f"{s['provider_id']}.{s['operatory_id']}"} for s in slots(db, org, location_id, kind, 21, limit=80)]


@public.post("/book/{org}")
async def book(org: str, request: Request, db: Session = Depends(auth.get_db)):
    _limit(audit.client_ip(request))
    locs = _org_ok(db, org)
    b = await request.json()
    kind, loc_id = b.get("kind"), int(b.get("location_id") or 0)
    if kind not in BOOKABLE or loc_id not in {l.id for l in locs}:
        raise HTTPException(400, "Choose a visit type and office")
    name = re.sub(r"\s+", " ", f"{(b.get('last') or '').strip()}, {(b.get('first') or '').strip()}").strip(", ")
    phone = re.sub(r"\D", "", b.get("phone") or "")
    if len(name) < 4 or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", b.get("dob") or "") or len(phone) < 10:
        raise HTTPException(400, "Name, date of birth and a mobile number are required")
    pick = next((s for s in slots(db, org, loc_id, kind, 21, limit=200) if s["start"] == b.get("start") and f"{s['provider_id']}.{s['operatory_id']}" == b.get("token")), None)
    if not pick:
        raise HTTPException(409, "That time was just taken. Please pick another.")
    match = db.scalar(select(auth.OrgPatient).where(auth.OrgPatient.org_key == org, auth.OrgPatient.name.ilike(name), auth.OrgPatient.dob == b["dob"]))
    a = m.Appointment(org_key=org, location_id=loc_id, mrn=match.mrn if match else "", provider_id=pick["provider_id"], operatory_id=pick["operatory_id"],
                      start=dt.datetime.fromisoformat(pick["start"]), minutes=pick["minutes"], reason=BOOKABLE[kind][0], status="scheduled", source="recall" if b.get("recall") else "online",
                      request=None if match else {"name": name, "dob": b["dob"], "phone": phone, "email": b.get("email", ""), "insurance": b.get("insurance", ""),
                                                   "member": b.get("member", ""), "note": (b.get("note") or "")[:300]})
    db.add(a)
    db.flush()
    loc = next(l for l in locs if l.id == loc_id)
    send_sms(db, org, a.mrn, phone, f"Hi {_first(name)}, you're booked at {loc.name} on {a.start:%a %b %-d at %-I:%M %p}. Reply C to confirm or call {loc.phone} to change.", "booking", a.id)
    if b.get("recall"):
        camp = db.scalar(select(m.Campaign).where(m.Campaign.org_key == org, m.Campaign.kind == "recall").order_by(m.Campaign.id.desc()))
        if camp:
            camp.booked += 1
    db.commit()
    audit.record(None, org_key=org, email="patient (online booking)", action="create", what="appointment", record_id=str(a.id), method="POST", path="/api/public/dental/book", ip=audit.client_ip(request))
    return {"ok": True, "when": a.start.isoformat(timespec="minutes"), "office": loc.name}


@router.post("/appointments/{aid}/accept-request")
async def accept_request(aid: int, request: Request, c: C = Depends(need("front"))):
    """An online booking from someone new: make them a patient (or link an existing one) and keep the time."""
    a = c.get(m.Appointment, aid)
    b = await request.json()
    r = a.request or {}
    if b.get("mrn"):
        patient(c, b["mrn"])
        a.mrn = b["mrn"]
    else:
        mrn = next_mrn(c)
        save_patient(c, {"mrn": mrn, "name": r.get("name", ""), "dob": r.get("dob", ""), "phone": r.get("phone", ""), "email": r.get("email", ""),
                         "payer": r.get("insurance", ""), "member": r.get("member", ""), "status": "New", "created": today()})
        if r.get("insurance"):
            c.add(m.Coverage(mrn=mrn, payer=r["insurance"], member_id=r.get("member", "")))
        a.mrn = mrn
    a.request = None
    c.db.commit()
    return {"ok": True, "mrn": a.mrn}


# ── texting ─────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/messages")
def threads(c: C = Depends(need("front"))):
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    th = {}
    for msg in c.q(m.Message).order_by(m.Message.created):
        key = msg.mrn or msg.phone
        t = th.setdefault(key, {"mrn": msg.mrn, "phone": msg.phone, "patient": names.get(msg.mrn, "") or msg.phone, "last": "", "at": "", "unread": 0, "kind": ""})
        t["last"], t["at"], t["kind"] = msg.body[:90], msg.created.isoformat(), msg.kind
        if msg.direction == "in" and not msg.read:
            t["unread"] += 1
    return sorted(th.values(), key=lambda t: t["at"], reverse=True)


@router.get("/messages/{key}")
def thread(key: str, c: C = Depends(need("front"))):
    rows = c.q(m.Message, (m.Message.mrn == key) | (m.Message.phone == key)).order_by(m.Message.created).all()
    for r in rows:
        if r.direction == "in":
            r.read = 1
    c.db.commit()
    return [r.as_dict() for r in rows]


@router.post("/patients/{mrn}/messages")
async def message_send(mrn: str, request: Request, c: C = Depends(need("front"))):
    p = patient_dict(patient(c, mrn))
    b = await request.json()
    body = (b.get("body") or "").strip()
    if not body:
        raise HTTPException(400, "Write a message")
    if p.get("sms_opt_out"):
        raise HTTPException(400, "This patient replied STOP; they can't be texted")
    msg = send_sms(c.db, c.org, mrn, b.get("phone") or p.get("phone", ""), body[:600], "manual", pt=p)
    c.db.commit()
    if msg.status not in ("delivered", "sent"):
        raise HTTPException(400, "Not sent: " + msg.status)
    return msg.as_dict()


def inbound(db, org: str, phone: str, body: str) -> dict:
    """A patient's reply. C/Y confirms their next appointment; STOP opts out; anything else waits for the front desk."""
    digits = re.sub(r"\D", "", phone)[-10:]
    pt_row = next((r for r in db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == org)) if re.sub(r"\D", "", patient_dict(r).get("phone", ""))[-10:] == digits), None)
    mrn = pt_row.mrn if pt_row else ""
    db.add(m.Message(org_key=org, mrn=mrn, direction="in", phone=digits, body=body[:600], kind="reply", status="received"))
    word = body.strip().upper()
    action = ""
    if word in ("STOP", "UNSUBSCRIBE", "CANCEL ALL", "QUIT", "END") and pt_row:
        d = patient_dict(pt_row)
        d["sms_opt_out"] = True
        pt_row.data = __import__("json").dumps(d)
        action = "opted_out"
    elif word in ("C", "Y", "YES", "CONFIRM") and mrn:
        a = db.scalar(select(m.Appointment).where(m.Appointment.org_key == org, m.Appointment.mrn == mrn, m.Appointment.start > dt.datetime.now(),
                                                 m.Appointment.status == "scheduled").order_by(m.Appointment.start))
        if a:
            a.status, a.confirmed_at = "confirmed", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
            action = "confirmed"
    db.commit()
    return {"mrn": mrn, "action": action}


@router.post("/sms/simulate")
async def sms_simulate(request: Request, c: C = Depends(need("front"))):
    """Development: pretend a patient texted back (the mock SMS provider has no real phones)."""
    b = await request.json()
    return inbound(c.db, c.org, b.get("phone", ""), b.get("body", ""))


@public.post("/sms/inbound/{org}")
async def sms_webhook(org: str, request: Request, db: Session = Depends(auth.get_db)):
    """The SMS provider's webhook. Signed with DENTAL_SMS_WEBHOOK_SECRET; refused when it isn't set."""
    import hmac, hashlib
    secret = os.environ.get("DENTAL_SMS_WEBHOOK_SECRET", "")
    raw = await request.body()
    sig = request.headers.get("x-signature", "")
    if not secret or not hmac.compare_digest(hmac.new(secret.encode(), raw, hashlib.sha256).hexdigest(), sig):
        raise HTTPException(401, "Bad signature")
    b = __import__("json").loads(raw or b"{}")
    return inbound(db, org, b.get("from", ""), b.get("body", ""))


# ── reminders ───────────────────────────────────────────────────────────────────────────────────────────────────
def run_reminders(db, org: str, now: dt.datetime = None) -> dict:
    """72-hour and 24-hour reminders for booked appointments, each sent once."""
    now = now or dt.datetime.now()
    sent = 0
    loc_names = {l.id: l for l in db.scalars(select(m.Location).where(m.Location.org_key == org))}
    for a in db.scalars(select(m.Appointment).where(m.Appointment.org_key == org, m.Appointment.start > now, m.Appointment.start < now + dt.timedelta(hours=74),
                                                   m.Appointment.status.in_(("scheduled", "confirmed")))):
        hours = (a.start - now).total_seconds() / 3600
        which = "72h" if 46 <= hours <= 74 else "24h" if 2 <= hours <= 26 else None
        if not which or which in (a.reminders or []) or not a.mrn:
            continue
        row = db.scalar(select(auth.OrgPatient).where(auth.OrgPatient.org_key == org, auth.OrgPatient.mrn == a.mrn))
        if not row:
            continue
        p = patient_dict(row)
        loc = loc_names.get(a.location_id) or next(iter(loc_names.values()), None)
        ask = " Reply C to confirm." if a.status == "scheduled" else ""
        send_sms(db, org, a.mrn, p.get("phone", ""), f"Hi {_first(row.name)}, reminder: {loc.name if loc else 'your dental visit'} {a.start:%a %b %-d at %-I:%M %p}.{ask}", "reminder", a.id, pt=p)
        a.reminders = (a.reminders or []) + [which]
        sent += 1
    db.commit()
    return {"sent": sent}


@router.post("/reminders/run")
def reminders_run(c: C = Depends(need("front"))):
    return run_reminders(c.db, c.org)


def run_all_reminders():
    """Background job (main.py): every dental clinic, every 15 minutes."""
    with auth.SessionLocal() as db:
        for org in {o for (o,) in db.execute(select(m.Location.org_key).distinct())}:
            try:
                run_reminders(db, org)
            except Exception as e:
                print(f"[DENTAL REMINDERS] {org}: {e}")


# ── recall ──────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/recall")
def recall(window: int = 30, c: C = Depends(need("front"))):
    """Patients due for hygiene within `window` days or overdue, who don't already have a hygiene visit booked."""
    hist = defaultdict(list)
    for p in c.q(m.Procedure, m.Procedure.code.in_(list(rcm.RECALL_CODES))):
        hist[p.mrn].append({"code": p.code, "date": p.date})
    hyg = {pid for pid, p in providers(c).items() if p.kind == "hygienist"}
    booked = {a.mrn for a in c.q(m.Appointment, m.Appointment.start > dt.datetime.now(), m.Appointment.status.in_(("scheduled", "confirmed"))) if a.provider_id in hyg or re.search(r"clean|recall|prophy|perio", a.reason or "", re.I)}
    last_msg = {}
    for msg in c.q(m.Message, m.Message.kind == "recall"):
        last_msg[msg.mrn] = max(last_msg.get(msg.mrn, ""), msg.created.isoformat()[:10])
    out = []
    horizon = dt.date.today() + dt.timedelta(days=window)
    for row in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org)):
        d = patient_dict(row)
        if d.get("status") == "Inactive" or row.mrn in booked:
            continue
        r = rcm.recall_due(hist.get(row.mrn, []))
        if not hist.get(row.mrn) or dt.date.fromisoformat(r["due"]) > horizon:
            continue
        out.append({"mrn": row.mrn, "patient": row.name, "phone": d.get("phone", ""), "last": r["last"], "due": r["due"], "overdue_days": r["overdue_days"],
                    "type": r["type"], "contacted": last_msg.get(row.mrn, ""), "opted_out": bool(d.get("sms_opt_out"))})
    return sorted(out, key=lambda x: -x["overdue_days"])


@router.post("/recall/campaign")
async def recall_campaign(request: Request, c: C = Depends(need("front"))):
    b = await request.json()
    mrns = b.get("mrns") or []
    tpl = b.get("template") or "Hi {first}, it's time for your {type} at {office}. Book online: {link} or call {phone}. Reply STOP to opt out."
    loc = c.q(m.Location).first()
    due = {r["mrn"]: r for r in recall(365, c)}
    camp = c.add(m.Campaign(name=b.get("name") or f"Recall {today()}", kind="recall", template=tpl, filters={"mrns": len(mrns)}))
    n = 0
    for mrn in mrns:
        r = due.get(mrn)
        if not r or r["opted_out"]:
            continue
        p = patient_dict(patient(c, mrn))
        body = tpl.format(first=_first(p.get("name", "")), type="cleaning" if r["type"] == "Prophy" else "periodontal maintenance",
                          office=loc.name if loc else "our office", link=f"{_base()}/dental/book/{c.org}?recall=1", phone=loc.phone if loc else "")
        msg = send_sms(c.db, c.org, mrn, p.get("phone", ""), body, "recall", pt=p)
        n += 1 if msg.status in ("delivered", "sent") else 0
    camp.sent = n
    c.db.commit()
    return {"sent": n, "campaign": camp.as_dict()}


@router.get("/campaigns")
def campaigns(c: C = Depends(need("front"))):
    return [x.as_dict() for x in c.q(m.Campaign).order_by(m.Campaign.created.desc())]


# ── forms ───────────────────────────────────────────────────────────────────────────────────────────────────────
DEFAULT_FORMS = [
    {"name": "Medical history", "fields": [
        {"k": "allergies", "label": "Allergies (medicines, latex, foods)", "type": "text", "maps_to": "allergies"},
        {"k": "meds", "label": "Medicines you take, one per line", "type": "textarea", "maps_to": "meds"},
        {"k": "heart", "label": "Heart condition, artificial valve, or joint replacement?", "type": "yesno", "flag": "Cardiac/joint: check premedication"},
        {"k": "blood_thinner", "label": "Do you take blood thinners (warfarin, Eliquis, aspirin)?", "type": "yesno", "flag": "Blood thinner"},
        {"k": "bisphosphonate", "label": "Have you taken bone medicine (Fosamax, Boniva, Prolia)?", "type": "yesno", "flag": "Bisphosphonates: extraction risk"},
        {"k": "diabetes", "label": "Diabetes?", "type": "yesno", "flag": "Diabetes"},
        {"k": "pregnant", "label": "Pregnant or nursing?", "type": "yesno", "flag": "Pregnant/nursing"},
        {"k": "conditions", "label": "Other medical conditions", "type": "textarea", "maps_to": "problems"},
        {"k": "signature", "label": "Type your full name to sign", "type": "signature"}]},
    {"name": "New patient registration", "fields": [
        {"k": "phone", "label": "Mobile phone", "type": "tel", "maps_to": "phone"}, {"k": "email", "label": "Email", "type": "email", "maps_to": "email"},
        {"k": "addr", "label": "Address", "type": "text", "maps_to": "addr"}, {"k": "emergency", "label": "Emergency contact (name and phone)", "type": "text", "maps_to": "emergency"},
        {"k": "payer", "label": "Dental insurance company", "type": "text", "maps_to": "coverage.payer"},
        {"k": "member", "label": "Subscriber / member ID", "type": "text", "maps_to": "coverage.member_id"},
        {"k": "signature", "label": "Type your full name to sign", "type": "signature"}]},
    {"name": "Consent and HIPAA acknowledgment", "fields": [
        {"k": "hipaa", "label": "I received the Notice of Privacy Practices", "type": "check"},
        {"k": "financial", "label": "I understand I'm responsible for amounts insurance doesn't pay", "type": "check"},
        {"k": "texts", "label": "You may text me about appointments and balances", "type": "check", "maps_to": "sms_consent"},
        {"k": "signature", "label": "Type your full name to sign", "type": "signature"}]},
]


def ensure_forms(db, org):
    if not db.scalar(select(m.FormTemplate.id).where(m.FormTemplate.org_key == org)):
        for f in DEFAULT_FORMS:
            db.add(m.FormTemplate(org_key=org, name=f["name"], fields=f["fields"]))
        db.commit()


@router.get("/forms")
def forms(c: C = Depends(need("front"))):
    ensure_forms(c.db, c.org)
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    tpls = {t.id: t for t in c.q(m.FormTemplate)}
    subs = [dict(s.as_dict(skip=("answers", "token")), patient=names.get(s.mrn, ""), form=tpls[s.template_id].name if s.template_id in tpls else "")
            for s in c.q(m.FormSubmission).order_by(m.FormSubmission.created.desc()).limit(200)]
    return {"templates": [t.as_dict() for t in tpls.values()], "submissions": subs}


@router.post("/patients/{mrn}/forms")
async def form_send(mrn: str, request: Request, c: C = Depends(need("front"))):
    ensure_forms(c.db, c.org)
    p = patient_dict(patient(c, mrn))
    b = await request.json()
    out = []
    for tid in b.get("template_ids") or []:
        t = c.get(m.FormTemplate, tid)
        sub = c.add(m.FormSubmission(mrn=mrn, template_id=t.id, token=secrets.token_urlsafe(24)))
        c.db.flush()
        out.append({"id": sub.id, "form": t.name, "url": f"/dental/form/{sub.token}"})
    if out and b.get("text", True):
        send_sms(c.db, c.org, mrn, p.get("phone", ""), f"Hi {_first(p.get('name', ''))}, please fill out your forms before your visit: {_base()}{out[0]['url']}", "form", pt=p)
    c.db.commit()
    return {"sent": out}


def _sub(db, token):
    s = db.scalar(select(m.FormSubmission).where(m.FormSubmission.token == token)) if len(token) > 20 else None
    if not s or (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - s.created).days > 30:
        raise HTTPException(404, "This form link has expired. Ask the office for a new one.")
    return s


@public.get("/form/{token}")
def form_public(token: str, db: Session = Depends(auth.get_db)):
    s = _sub(db, token)
    t = db.get(m.FormTemplate, s.template_id)
    row = db.scalar(select(auth.OrgPatient).where(auth.OrgPatient.org_key == s.org_key, auth.OrgPatient.mrn == s.mrn))
    loc = db.scalar(select(m.Location).where(m.Location.org_key == s.org_key))
    others = db.scalars(select(m.FormSubmission).where(m.FormSubmission.org_key == s.org_key, m.FormSubmission.mrn == s.mrn, m.FormSubmission.status == "sent",
                                                      m.FormSubmission.id != s.id, m.FormSubmission.created >= s.created - dt.timedelta(minutes=5))).all()
    return {"form": t.name, "fields": t.fields, "status": s.status, "first_name": _first(row.name if row else ""), "practice": loc.name if loc else "",
            "next": [f"/dental/form/{o.token}" for o in others]}


@public.post("/form/{token}")
async def form_public_submit(token: str, request: Request, db: Session = Depends(auth.get_db)):
    """Saves the answers and writes them into the chart right away (marked as patient-reported, for staff to review)."""
    _limit(audit.client_ip(request), 40)
    s = _sub(db, token)
    if s.status != "sent":
        return {"ok": True, "already": True}
    t = db.get(m.FormTemplate, s.template_id)
    b = await request.json()
    answers = {f["k"]: b.get(f["k"]) for f in t.fields}
    sig = next((f for f in t.fields if f["type"] == "signature"), None)
    if sig and len(str(answers.get(sig["k"]) or "").strip()) < 2:
        raise HTTPException(400, "Please sign by typing your full name")
    s.answers, s.status, s.submitted_at = answers, "completed", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    apply_form(db, s, t, "patient")
    db.commit()
    audit.record(None, org_key=s.org_key, email="patient (form link)", action="change", what="medical history", record_id=s.mrn, method="POST", path="/api/public/dental/form", ip=audit.client_ip(request))
    return {"ok": True}


def apply_form(db, s, t, by: str):
    row = db.scalar(select(auth.OrgPatient).where(auth.OrgPatient.org_key == s.org_key, auth.OrgPatient.mrn == s.mrn))
    if not row:
        return
    import json
    d = patient_dict(row)
    flags = []
    lines = []
    cov_upd = {}
    for f in t.fields:
        v = (s.answers or {}).get(f["k"])
        if f["type"] == "signature":
            continue
        shown = "Yes" if v is True or v == "yes" else "No" if v in (False, "no") else (v or "")
        lines.append(f"{f['label']}: {shown}")
        if f.get("flag") and (v is True or v == "yes"):
            flags.append(f["flag"])
        mt = f.get("maps_to")
        if mt and v not in (None, ""):
            if mt.startswith("coverage."):
                cov_upd[mt.split(".", 1)[1]] = v
            elif mt == "sms_consent":
                d["sms_consent"] = bool(v)
            else:
                d[mt] = v
    if flags:
        d["medical_alerts"] = sorted(set((d.get("medical_alerts") or []) + flags))
    d["mh_updated"], d["mh_review_pending"] = today(), True
    row.data = json.dumps(d)
    if cov_upd.get("payer"):
        cov = db.scalar(select(m.Coverage).where(m.Coverage.org_key == s.org_key, m.Coverage.mrn == s.mrn, m.Coverage.rank == "primary"))
        if not cov:
            cov = m.Coverage(org_key=s.org_key, mrn=s.mrn, rank="primary")
            db.add(cov)
        cov.payer, cov.member_id = cov_upd.get("payer", cov.payer), cov_upd.get("member_id", cov.member_id)
        row.payer = cov.payer
    db.add(m.ClinicalNote(org_key=s.org_key, mrn=s.mrn, date=today(), kind="medical_history", status="signed", signed_by=by,
                          signed_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), text=f"{t.name} (patient-reported)\n" + "\n".join(lines)))
    s.status, s.applied_at, s.applied_by = "applied", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), by


@router.post("/patients/{mrn}/mh-reviewed")
def mh_reviewed(mrn: str, c: C = Depends(need("clinical_write"))):
    row = patient(c, mrn)
    save_patient(c, {"mh_review_pending": False, "mh_reviewed_by": c.email, "mh_reviewed": today()}, row)
    c.db.commit()
    return {"ok": True}


# ── reviews ─────────────────────────────────────────────────────────────────────────────────────────────────────
@router.post("/appointments/{aid}/review-request")
def review_request(aid: int, c: C = Depends(need("front"))):
    a = c.get(m.Appointment, aid)
    if not a.mrn:
        raise HTTPException(400, "No patient on this appointment")
    if c.db.scalar(select(m.ReviewRequest.id).where(m.ReviewRequest.org_key == c.org, m.ReviewRequest.appt_id == a.id)):
        raise HTTPException(409, "Already asked after this visit")
    p = patient_dict(patient(c, a.mrn))
    rr = c.add(m.ReviewRequest(mrn=a.mrn, appt_id=a.id, token=secrets.token_urlsafe(24)))
    loc = c.db.get(m.Location, a.location_id) if a.location_id else c.q(m.Location).first()
    send_sms(c.db, c.org, a.mrn, p.get("phone", ""), f"Thanks for visiting {loc.name if loc else 'us'} today, {_first(p.get('name', ''))}! How did we do? {_base()}/dental/review/{rr.token}", "review", a.id, pt=p)
    c.db.commit()
    return rr.as_dict(skip=("token",))


@router.get("/reviews")
def reviews(c: C = Depends(need("front"))):
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    rows = [dict(r.as_dict(skip=("token",)), patient=names.get(r.mrn, "")) for r in c.q(m.ReviewRequest).order_by(m.ReviewRequest.created.desc())]
    rated = [r["rating"] for r in rows if r["rating"]]
    return {"requests": rows, "sent": len(rows), "rated": len(rated), "average": round(sum(rated) / len(rated), 2) if rated else None}


@public.get("/review/{token}")
def review_public(token: str, db: Session = Depends(auth.get_db)):
    r = db.scalar(select(m.ReviewRequest).where(m.ReviewRequest.token == token)) if len(token) > 20 else None
    if not r:
        raise HTTPException(404, "This link has expired")
    loc = db.scalar(select(m.Location).where(m.Location.org_key == r.org_key))
    return {"practice": loc.name if loc else "", "status": r.status, "public_link": vendors.vendor("reviews").review_link(loc.id if loc else 0)}


@public.post("/review/{token}")
async def review_public_submit(token: str, request: Request, db: Session = Depends(auth.get_db)):
    r = db.scalar(select(m.ReviewRequest).where(m.ReviewRequest.token == token)) if len(token) > 20 else None
    if not r:
        raise HTTPException(404, "This link has expired")
    b = await request.json()
    rating = int(b.get("rating") or 0)
    if not 1 <= rating <= 5:
        raise HTTPException(400, "Choose 1 to 5 stars")
    r.rating, r.comment, r.status = rating, (b.get("comment") or "")[:1000], "rated"
    if rating <= 3:
        db.add(m.WorkItem(org_key=r.org_key, kind="review", mrn=r.mrn, title=f"Patient rated their visit {rating}/5", how="Call the patient to follow up.", action="review"))
    db.commit()
    loc = db.scalar(select(m.Location).where(m.Location.org_key == r.org_key))
    return {"ok": True, "public_link": vendors.vendor("reviews").review_link(loc.id if loc else 0)}
