"""The dental EMR's API: /api/dental/... for the app, /api/public/dental/... for links patients open (booking, forms,
treatment plan, text-to-pay, reviews).

This file: access control, practice setup (locations, chairs, providers, hours, fees), patients and the dental chart
(tooth conditions, perio, notes, procedures, treatment plans, images), and the schedule. Billing is dental_api_rcm.py,
patient engagement dental_engage.py, operations dental_ops.py.

Access (HIPAA minimum necessary), by the login's role:
  read            everyone at the clinic, viewers too: the schedule and patient list
  front           front desk, providers, billers, admins: booking, check-in, coverage, payments, messages
  clinical_read   providers, billers (claims need the chart), admins: the chart
  clinical_write  providers and admins: chart entries, notes, plans, approving code suggestions
  billing         billers and admins: claims, ERAs, the denial queue, write-offs
  admin           admins: practice setup, imports, sample data
Every call is in the access log (audit.py's middleware); the patient's MRN or the claim is recorded with it.
"""
import datetime as dt
import json
import re
import secrets
import threading

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select, delete, or_
from sqlalchemy.orm import Session

import audit
import auth
import dental_clinical as clin
import dental_db as m
import dental_rcm as rcm
import dental_rules
import dental_vendors as vendors

router = APIRouter(prefix="/api/dental")
public = APIRouter(prefix="/api/public/dental")

ROLES = {
    "read": {"admin", "biller", "provider", "front_desk", "viewer"},
    "front": {"admin", "biller", "provider", "front_desk"},
    "clinical_read": {"admin", "provider", "biller"},
    "clinical_write": {"admin", "provider"},
    "billing": {"admin", "biller"},
    "admin": {"admin"},
}

# the access log names dental records (first match wins, so these go ahead of the generic patterns)
audit._WHAT[:0] = [(re.compile(r"^/api/dental/patients/([^/]+)"), "dental chart"), (re.compile(r"^/api/dental/claims/(\d+)"), "dental claim"),
                   (re.compile(r"^/api/dental/plans/(\d+)"), "treatment plan"), (re.compile(r"^/api/dental/images/(\d+)"), "dental image"),
                   (re.compile(r"^/api/dental/(?:era|remittances)"), "remittance"), (re.compile(r"^/api/dental/"), "dental")]


class Q:
    """A clinic-scoped query: iterate it, or .order_by() / .limit() / .all() / .first()."""
    def __init__(self, db, stmt):
        self.db, self.stmt = db, stmt

    def order_by(self, *a):
        return Q(self.db, self.stmt.order_by(*a))

    def limit(self, n):
        return Q(self.db, self.stmt.limit(n))

    def all(self):
        return self.db.scalars(self.stmt).all()

    def first(self):
        return self.db.scalars(self.stmt.limit(1)).first()

    def __iter__(self):
        return iter(self.all())


class C:
    """Who's asking, for which clinic."""
    def __init__(self, user, db: Session):
        self.user, self.db, self.org = user, db, auth._org_namespace(user)
        self.email = user.email

    def q(self, model, *where):
        return Q(self.db, select(model).where(model.org_key == self.org, *where))

    def get(self, model, id_):
        row = self.db.get(model, int(id_)) if str(id_).isdigit() else None
        if row is None or row.org_key != self.org:
            raise HTTPException(404, "Not found")
        return row

    def add(self, row):
        row.org_key = self.org
        self.db.add(row)
        return row


def need(perm: str):
    def dep(user=Depends(auth.require_user), db: Session = Depends(auth.get_db)) -> C:
        if (user.role or "viewer") not in ROLES[perm]:
            raise HTTPException(403, f"Your role ({user.role}) can't do this.")
        if auth.org_practice(user, db) != "dental":
            raise HTTPException(403, "This is part of the dental version of Althais.")
        c = C(user, db)
        ready(c)
        return c
    return dep


_ready: set = set()
_ready_lock = threading.Lock()


def ready(c: C) -> None:
    """A dental clinic is never an empty shell. The first time anyone in it opens the dental app: a test clinic (/admin)
    gets the synthetic practice to try; a real clinic gets a starter setup to edit (its office, three chairs, the person
    signing in as its dentist, weekday hours and a fee schedule) and no patients. Starter rows have sample=2 so the
    synthetic practice can replace them while nothing has been booked on them."""
    if c.org in _ready:
        return
    with _ready_lock:
        if c.org in _ready or c.db.scalar(select(m.Location.id).where(m.Location.org_key == c.org)):
            _ready.add(c.org)
            return
        import dental_synth
        if auth.org_test_clinic(c.user, c.db):
            try:
                dental_synth.load(c)
            except HTTPException:
                pass
        else:
            dental_synth.starter(c)
        _ready.add(c.org)


def today() -> str:
    return dt.date.today().isoformat()


def _age(dob, on=None):
    try:
        b, d = dt.date.fromisoformat(str(dob)[:10]), dt.date.fromisoformat(on or today())
        return d.year - b.year - ((d.month, d.day) < (b.month, b.day))
    except ValueError:
        return None


# ── patients ────────────────────────────────────────────────────────────────────────────────────────────────────
def patient_dict(row: auth.OrgPatient) -> dict:
    try:
        d = json.loads(row.data or "{}")
    except ValueError:
        d = {}
    d.update({"mrn": row.mrn, "name": row.name or d.get("name", ""), "dob": row.dob or d.get("dob", ""), "sex": row.sex or d.get("sex", ""),
              "payer": row.payer or d.get("payer", "")})
    return d


def patient(c: C, mrn: str) -> auth.OrgPatient:
    row = c.db.scalar(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org, auth.OrgPatient.mrn == mrn))
    if not row:
        raise HTTPException(404, "No patient with that MRN")
    return row


def save_patient(c: C, data: dict, row: auth.OrgPatient = None) -> auth.OrgPatient:
    if row is None:
        row = auth.OrgPatient(org_key=c.org, mrn=data["mrn"])
        c.db.add(row)
        cur = {}
    else:
        cur = patient_dict(row)
    cur.update({k: v for k, v in data.items() if v is not None})
    row.name, row.dob, row.sex, row.payer = cur.get("name", ""), cur.get("dob", ""), cur.get("sex", ""), cur.get("payer", "")
    row.provider = cur.get("provider", row.provider or "")
    row.data = json.dumps(cur)
    return row


def next_mrn(c: C) -> str:
    n = c.db.scalar(select(auth.OrgPatient.mrn).where(auth.OrgPatient.org_key == c.org, auth.OrgPatient.mrn.like("D%")).order_by(auth.OrgPatient.id.desc()))
    k = int(re.sub(r"\D", "", n or "") or 100000) + 1
    while c.db.scalar(select(auth.OrgPatient.id).where(auth.OrgPatient.org_key == c.org, auth.OrgPatient.mrn == f"D{k}")):
        k += 1
    return f"D{k}"


def balance(c: C, mrn: str) -> float:
    return round(sum(e.amount for e in c.q(m.LedgerEntry, m.LedgerEntry.mrn == mrn)), 2)


def history(c: C, mrn: str) -> list:
    return [{"code": p.code, "date": p.date, "tooth": p.tooth, "quad": p.quad} for p in c.q(m.Procedure, m.Procedure.mrn == mrn)]


def coverage(c: C, mrn: str, rank="primary"):
    return c.db.scalar(select(m.Coverage).where(m.Coverage.org_key == c.org, m.Coverage.mrn == mrn, m.Coverage.rank == rank))


def fee_for(c: C, code: str) -> float:
    f = c.db.scalar(select(m.FeeSchedule).where(m.FeeSchedule.org_key == c.org, m.FeeSchedule.code == code, m.FeeSchedule.payer == ""))
    return f.fee if f else 0.0


def providers(c: C) -> dict:
    return {p.id: p for p in c.q(m.Provider)}


@router.get("/patients")
def list_patients(q: str = "", c: C = Depends(need("read"))):
    rows = c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org).order_by(auth.OrgPatient.name)).all()
    s = q.strip().lower()
    bal, last, cov = {}, {}, {}
    for e in c.q(m.LedgerEntry):
        bal[e.mrn] = bal.get(e.mrn, 0) + e.amount
    for p in c.q(m.Procedure):
        if p.code in rcm.RECALL_CODES:
            last.setdefault(p.mrn, []).append({"code": p.code, "date": p.date})
    for v in c.q(m.Coverage, m.Coverage.rank == "primary"):
        cov[v.mrn] = v
    mem = {x.mrn for x in c.q(m.Membership, m.Membership.status == "active")}
    out = []
    for r in rows:
        if s and s not in (r.name + " " + r.mrn + " " + (r.dob or "")).lower():
            continue
        d = patient_dict(r)
        rc = rcm.recall_due(last.get(r.mrn, []))
        v = cov.get(r.mrn)
        out.append({"mrn": r.mrn, "name": r.name, "dob": r.dob, "sex": r.sex, "phone": d.get("phone", ""), "payer": (v.payer if v else "") or d.get("payer", ""),
                    "member": (v.member_id if v else "") or d.get("member", ""), "elig": (v.checked_at.isoformat() if v and v.checked_at else ""),
                    "elig_active": bool(v and v.active), "balance": round(bal.get(r.mrn, 0), 2), "recall_due": rc["due"], "recall_overdue": rc["overdue_days"],
                    "member_plan": r.mrn in mem, "sample": d.get("sample", False), "status": d.get("status", "Active")})
    return out


@router.post("/patients")
async def create_patient(request: Request, c: C = Depends(need("front"))):
    b = await request.json()
    if not (b.get("name") and b.get("dob")):
        raise HTTPException(400, "Name and date of birth are required")
    mrn = b.get("mrn") or next_mrn(c)
    if c.db.scalar(select(auth.OrgPatient.id).where(auth.OrgPatient.org_key == c.org, auth.OrgPatient.mrn == mrn)):
        raise HTTPException(409, "That MRN is taken")
    data = {k: b.get(k, "") for k in ("name", "dob", "sex", "phone", "email", "addr", "allergies", "problems", "meds", "payer", "member")}
    data.update({"mrn": mrn, "status": "Active", "created": today(), "balance": 0})
    save_patient(c, data)
    if b.get("payer") and b.get("payer") != "Self-pay":
        c.add(m.Coverage(mrn=mrn, payer=b["payer"], payer_id=b.get("payer_id", ""), member_id=b.get("member", ""), group=b.get("group", "")))
    c.db.commit()
    return {"ok": True, "mrn": mrn}


@router.patch("/patients/{mrn}")
async def update_patient(mrn: str, request: Request, c: C = Depends(need("front"))):
    b = await request.json()
    row = patient(c, mrn)
    allowed = ("name", "dob", "sex", "phone", "email", "addr", "allergies", "problems", "meds", "sms_opt_out", "preferred_location", "status")
    save_patient(c, {k: b[k] for k in allowed if k in b}, row)
    c.db.commit()
    return {"ok": True}


@router.get("/patients/{mrn}")
def chart(mrn: str, c: C = Depends(need("clinical_read"))):
    """Everything the dental chart shows, in one call."""
    p = patient_dict(patient(c, mrn))
    provs = providers(c)
    plans = c.q(m.TreatmentPlan, m.TreatmentPlan.mrn == mrn).all()
    items = {}
    if plans:
        for it in c.db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.plan_id.in_([x.id for x in plans]))):
            items.setdefault(it.plan_id, []).append(it.as_dict())
    procs = c.q(m.Procedure, m.Procedure.mrn == mrn).all()
    cov = [v.as_dict(skip=("raw_271",)) for v in c.q(m.Coverage, m.Coverage.mrn == mrn)]
    claims = c.q(m.Claim, m.Claim.mrn == mrn).all()
    lines = {}
    if claims:
        for l in c.db.scalars(select(m.ClaimLine).where(m.ClaimLine.claim_id.in_([x.id for x in claims]))):
            lines.setdefault(l.claim_id, []).append(l.as_dict())
    hist = [{"code": x.code, "date": x.date, "tooth": x.tooth, "quad": x.quad} for x in procs]
    mem = c.db.scalar(select(m.Membership).where(m.Membership.org_key == c.org, m.Membership.mrn == mrn, m.Membership.status == "active"))
    appts = c.q(m.Appointment, m.Appointment.mrn == mrn).all()
    return {
        "patient": p, "age": _age(p.get("dob")),
        "providers": {i: {"name": x.name, "kind": x.kind, "color": x.color} for i, x in provs.items()},
        "conditions": [x.as_dict() for x in c.q(m.ToothCondition, m.ToothCondition.mrn == mrn)],
        "perio": [dict(x.as_dict(skip=("readings",)), has_readings=bool(x.readings)) for x in c.q(m.PerioExam, m.PerioExam.mrn == mrn).order_by(m.PerioExam.date.desc())],
        "notes": [x.as_dict(skip=("transcript",)) for x in c.q(m.ClinicalNote, m.ClinicalNote.mrn == mrn).order_by(m.ClinicalNote.created.desc())],
        "procedures": sorted([x.as_dict() for x in procs], key=lambda x: x["date"], reverse=True),
        "plans": [dict(x.as_dict(skip=("signature",)), signed=bool(x.signature), items=sorted(items.get(x.id, []), key=lambda i: (i["phase"], i["id"]))) for x in plans],
        "images": [x.as_dict(skip=("data",)) for x in c.q(m.Image, m.Image.mrn == mrn).order_by(m.Image.taken.desc())],
        "coverage": cov,
        "claims": [dict(x.as_dict(skip=("x837", "narrative")), lines=lines.get(x.id, [])) for x in sorted(claims, key=lambda x: x.dos, reverse=True)],
        "ledger": [x.as_dict() for x in c.q(m.LedgerEntry, m.LedgerEntry.mrn == mrn).order_by(m.LedgerEntry.date.desc(), m.LedgerEntry.id.desc())],
        "balance": balance(c, mrn),
        "recall": rcm.recall_due(hist),
        "membership": dict(mem.as_dict(), plan=(c.get(m.MembershipPlan, mem.plan_id).as_dict())) if mem else None,
        "suggestions": [x.as_dict() for x in c.q(m.CodeSuggestion, m.CodeSuggestion.mrn == mrn, m.CodeSuggestion.status == "pending")],
        "appointments": [appt_dict(a, provs) for a in sorted(appts, key=lambda a: a.start, reverse=True)],
        "preauths": [x.as_dict() for x in c.q(m.PreAuth, m.PreAuth.mrn == mrn)],
        "labs": [x.as_dict() for x in c.q(m.LabCase, m.LabCase.mrn == mrn)],
        "rx": [x.as_dict() for x in c.q(m.Prescription, m.Prescription.mrn == mrn)],
        "forms": [x.as_dict(skip=("answers",)) for x in c.q(m.FormSubmission, m.FormSubmission.mrn == mrn)],
        "payment_plans": [x.as_dict(skip=("card_token",)) for x in c.q(m.PaymentPlan, m.PaymentPlan.mrn == mrn)],
    }


# ── tooth chart ─────────────────────────────────────────────────────────────────────────────────────────────────
CONDITIONS = {"caries", "composite", "amalgam", "crown", "missing", "extracted", "implant", "implant_crown", "rct", "fracture", "sealant", "watch",
              "bridge", "veneer", "periapical", "impacted", "inlay", "onlay", "post_core", "denture", "abrasion", "recession", "bone_loss"}


@router.post("/patients/{mrn}/conditions")
async def add_condition(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    patient(c, mrn)
    b = await request.json()
    tooth = dental_rules.norm_tooth(b.get("tooth"))
    if b.get("condition") not in CONDITIONS:
        raise HTTPException(400, "Unknown condition")
    if tooth and not dental_rules.tooth_kind(tooth):
        raise HTTPException(400, f"#{tooth} isn't a tooth number")
    row = c.add(m.ToothCondition(mrn=mrn, tooth=tooth, surface=dental_rules.norm_surface(b.get("surface")), condition=b["condition"],
                                 status=b.get("status") or "existing", code=(b.get("code") or "").upper(), date=b.get("date") or today(),
                                 provider_id=b.get("provider_id"), note=b.get("note", "")))
    c.db.commit()
    return row.as_dict()


@router.patch("/conditions/{cid}")
async def edit_condition(cid: int, request: Request, c: C = Depends(need("clinical_write"))):
    row = c.get(m.ToothCondition, cid)
    b = await request.json()
    for k in ("status", "surface", "note", "code"):
        if k in b:
            setattr(row, k, dental_rules.norm_surface(b[k]) if k == "surface" else b[k])
    c.db.commit()
    return row.as_dict()


@router.delete("/conditions/{cid}")
def delete_condition(cid: int, c: C = Depends(need("clinical_write"))):
    c.db.delete(c.get(m.ToothCondition, cid))
    c.db.commit()
    return {"ok": True}


# ── perio ───────────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/perio/{pid}")
def perio_get(pid: int, c: C = Depends(need("clinical_read"))):
    x = c.get(m.PerioExam, pid)
    return dict(x.as_dict(), readings=x.readings)


@router.post("/patients/{mrn}/perio")
async def perio_save(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    patient(c, mrn)
    b = await request.json()
    readings = b.get("readings") or {}
    for t, v in readings.items():
        if not (str(t).isdigit() and 1 <= int(t) <= 32):
            raise HTTPException(400, f"#{t} isn't a permanent tooth")
        for d in (v.get("pd") or []):
            if d is not None and not (0 <= int(d) <= 15):
                raise HTTPException(400, f"#{t}: probing depth {d} mm is out of range")
    summary = clin.perio_summary(readings)
    row = c.get(m.PerioExam, b["id"]) if b.get("id") else c.add(m.PerioExam(mrn=mrn))
    row.date, row.provider_id, row.readings, row.summary = b.get("date") or today(), b.get("provider_id"), readings, summary
    row.note, row.source = b.get("note", ""), b.get("source", "manual")
    c.db.commit()
    return dict(row.as_dict(), readings=row.readings)


@router.post("/perio/voice")
async def perio_voice(request: Request, c: C = Depends(need("clinical_write"))):
    """Hands-free charting: the browser sends what it heard; this places the numbers on the chart."""
    b = await request.json()
    return clin.parse_perio_speech(b.get("text", ""), b.get("readings"), int(b.get("position") or 0), b.get("missing") or [])


# ── notes, procedures and coding ────────────────────────────────────────────────────────────────────────────────
@router.post("/patients/{mrn}/notes")
async def note_save(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    patient(c, mrn)
    b = await request.json()
    row = c.get(m.ClinicalNote, b["id"]) if b.get("id") else c.add(m.ClinicalNote(mrn=mrn, date=b.get("date") or today()))
    if row.status == "signed":
        raise HTTPException(409, "Signed notes can't be changed; add an addendum")
    row.text, row.kind, row.appt_id, row.provider_id = b.get("text", row.text), b.get("kind", row.kind or "progress"), b.get("appt_id", row.appt_id), b.get("provider_id", row.provider_id)
    if b.get("transcript"):
        row.transcript = b["transcript"]
    if b.get("sign"):
        row.status, row.signed_by, row.signed_at = "signed", c.email, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    c.db.commit()
    return row.as_dict(skip=("transcript",))


@router.post("/patients/{mrn}/notes/ambient")
async def note_ambient(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    """A visit recording (or the browser's own transcript) becomes a draft note. Nothing is signed or billed from it."""
    patient(c, mrn)
    b = await request.json()
    text = b.get("transcript")
    if not text:
        res = vendors.vendor("transcriber").transcribe(b.get("audio", "").encode(), b.get("mime", "audio/webm"))
        text = res.data.get("text", "")
    d = clin.draft_note(text)
    row = c.add(m.ClinicalNote(mrn=mrn, date=today(), kind="ambient_draft", transcript=text, text=d["note"], appt_id=b.get("appt_id"), provider_id=b.get("provider_id")))
    c.db.commit()
    return dict(d, note_id=row.id, transcript=text)


@router.post("/patients/{mrn}/code-check")
async def code_check(mrn: str, request: Request, c: C = Depends(need("clinical_read"))):
    """CDT suggestions from the note: what's documented but not billed, or billed lower than documented. Rules always run;
    the AI coder adds its suggestions when it's configured. Every suggestion waits for a dentist's approval."""
    p = patient_dict(patient(c, mrn))
    b = await request.json()
    note, lines = b.get("note", ""), b.get("lines") or []
    found = [dict(f, source="rules") for f in rcm.find_missed(note, lines, _age(p.get("dob")))]
    ai_used = False
    if b.get("use_ai", True):
        try:
            import dental_coding
            have = {(l.get("code"), dental_rules.norm_tooth(l.get("tooth"))) for l in lines} | {(f["code"], f["tooth"]) for f in found}
            for s in dental_coding.clean(dental_coding._ask_ai(note)):
                if (s.get("code"), dental_rules.norm_tooth(s.get("tooth"))) not in have:
                    found.append({"kind": "suggested", "code": s.get("code"), "tooth": s.get("tooth", ""), "surface": s.get("surface", ""), "quad": s.get("quad", ""),
                                  "billed": "", "evidence": s.get("justification", ""), "message": s.get("description", "AI suggestion"), "source": "ai"})
            ai_used = True
        except Exception:
            ai_used = False   # no AI key or the service is down: the rules still ran
    c.db.execute(delete(m.CodeSuggestion).where(m.CodeSuggestion.org_key == c.org, m.CodeSuggestion.mrn == mrn, m.CodeSuggestion.status == "pending",
                                                m.CodeSuggestion.note_id == b.get("note_id")))
    rows = [c.add(m.CodeSuggestion(mrn=mrn, note_id=b.get("note_id"), claim_id=b.get("claim_id"), source=f["source"], kind=f["kind"], code=f["code"],
                                   tooth=f.get("tooth", ""), surface=f.get("surface", ""), quad=f.get("quad", ""), billed=f.get("billed", ""),
                                   message=f["message"][:300], evidence=f.get("evidence", ""))) for f in found]
    c.db.commit()
    return {"suggestions": [r.as_dict() for r in rows], "ai": ai_used}


@router.post("/suggestions/{sid}")
async def decide_suggestion(sid: int, request: Request, c: C = Depends(need("clinical_write"))):
    """Approve or reject. Approving an undercoded line replaces the billed code on the visit's lines; the caller
    re-reads them. Only providers and admins can approve: a code is a clinical statement."""
    row = c.get(m.CodeSuggestion, sid)
    b = await request.json()
    if b.get("decision") not in ("approved", "rejected"):
        raise HTTPException(400, "decision must be approved or rejected")
    row.status, row.decided_by, row.decided_at = b["decision"], c.email, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    c.db.commit()
    return row.as_dict()


@router.post("/patients/{mrn}/procedures")
async def complete_procedures(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    """Marks procedures done: frequency history, the ledger charge, the tooth chart, the treatment plan item. Optionally
    makes the insurance claim right away (`claim: true`)."""
    patient(c, mrn)
    b = await request.json()
    date, prov, loc = b.get("date") or today(), b.get("provider_id"), b.get("location_id")
    made = []
    for l in b.get("lines") or []:
        code = (l.get("code") or "").upper()
        if not dental_rules.CDT.match(code):
            raise HTTPException(400, f"{code or 'blank'} isn't a CDT code")
        fee = float(l.get("fee") if l.get("fee") not in (None, "") else fee_for(c, code))
        p = c.add(m.Procedure(mrn=mrn, date=date, code=code, tooth=dental_rules.norm_tooth(l.get("tooth")), surface=dental_rules.norm_surface(l.get("surface")),
                              quad=(l.get("quad") or "").upper(), fee=fee, provider_id=prov, location_id=loc, plan_item_id=l.get("plan_item_id")))
        c.db.flush()
        c.add(m.LedgerEntry(mrn=mrn, date=date, kind="charge", amount=fee, procedure_id=p.id, provider_id=prov, location_id=loc, memo=code + (f" #{p.tooth}" if p.tooth else "")))
        if l.get("plan_item_id"):
            it = c.db.get(m.TreatmentItem, int(l["plan_item_id"]))
            if it:
                it.status = "completed"
        cond = CHART_MARK.get(code[:4]) or CHART_MARK.get(code)
        if p.tooth and cond:
            for x in c.q(m.ToothCondition, m.ToothCondition.mrn == mrn, m.ToothCondition.tooth == p.tooth, m.ToothCondition.status == "planned", m.ToothCondition.code == code):
                c.db.delete(x)
            if cond != "_":
                c.add(m.ToothCondition(mrn=mrn, tooth=p.tooth, surface=p.surface, condition=cond, status="completed", code=code, date=date, provider_id=prov))
            if code.startswith("D2") or code.startswith("D3"):
                for x in c.q(m.ToothCondition, m.ToothCondition.mrn == mrn, m.ToothCondition.tooth == p.tooth, m.ToothCondition.condition == "caries"):
                    c.db.delete(x)
        made.append(p)
    c.db.commit()
    out = {"procedures": [p.as_dict() for p in made]}
    if b.get("claim") and made:
        import dental_api_rcm
        cov = coverage(c, mrn)
        if cov and cov.payer:
            out["claim"] = dental_api_rcm.make_claim(c, mrn, made)
    return out


def _mark(code):
    n = int(code[1:])
    if 2140 <= n <= 2161:
        return "amalgam"
    if 2330 <= n <= 2394:
        return "composite"
    if 2700 <= n <= 2799:
        return "crown"
    if 3310 <= n <= 3348:
        return "rct"
    if 7140 <= n <= 7250:
        return "extracted"
    if 6010 <= n <= 6013:
        return "implant"
    if n == 1351:
        return "sealant"
    if 2950 <= n <= 2954:
        return "post_core"
    return None


CHART_MARK = {}
for _n in range(1000, 8000):
    _k = _mark(f"D{_n}")
    if _k:
        CHART_MARK[f"D{_n}"] = _k


# ── treatment plans ─────────────────────────────────────────────────────────────────────────────────────────────
def plan_estimate(c: C, plan: m.TreatmentPlan) -> dict:
    """Re-estimates every open item against the patient's current benefits (or membership, for the uninsured)."""
    p = patient_dict(patient(c, plan.mrn))
    items = c.db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.plan_id == plan.id).order_by(m.TreatmentItem.phase, m.TreatmentItem.id)).all()
    open_items = [i for i in items if i.status not in ("completed", "declined")]
    cov = coverage(c, plan.mrn)
    lines = [{"code": i.code, "tooth": i.tooth, "quad": i.quad, "fee": i.fee} for i in open_items]
    mem = c.db.scalar(select(m.Membership).where(m.Membership.org_key == c.org, m.Membership.mrn == plan.mrn, m.Membership.status == "active"))
    if cov and cov.benefits and cov.active:
        est = rcm.estimate(cov.benefits, lines, history(c, plan.mrn), today(), _age(p.get("dob")))
        for i, r in zip(open_items, est["lines"]):
            i.est_insurance, i.est_patient, i.est_note = r["insurance"], r["patient"], r["note"][:200]
        basis = "insurance"
    elif mem:
        mp = c.get(m.MembershipPlan, mem.plan_id)
        est = rcm.membership_price({"included": mp.included, "discount_pct": mp.discount_pct}, lines, mem.used)
        for i, r in zip(open_items, est["lines"]):
            i.est_insurance, i.est_patient, i.est_note = 0, r["price"], r["note"]
        basis = "membership"
    else:
        for i in open_items:
            i.est_insurance, i.est_patient, i.est_note = 0, i.fee, "No insurance on file" if not cov else "Benefits not checked yet"
        basis = "none"
    c.db.commit()
    return {"basis": basis, "fee": round(sum(i.fee for i in open_items), 2), "insurance": round(sum(i.est_insurance for i in open_items), 2),
            "patient": round(sum(i.est_patient for i in open_items), 2),
            "by_phase": {ph: round(sum(i.est_patient for i in open_items if i.phase == ph), 2) for ph in sorted({i.phase for i in open_items})}}


@router.post("/patients/{mrn}/plans")
async def plan_create(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    patient(c, mrn)
    b = await request.json()
    plan = c.add(m.TreatmentPlan(mrn=mrn, name=b.get("name") or "Treatment plan", provider_id=b.get("provider_id"), location_id=b.get("location_id"), status="draft"))
    c.db.flush()
    for it in b.get("items") or []:
        _add_item(c, plan, it)
    c.db.commit()
    plan_estimate(c, plan)
    return {"id": plan.id}


def _add_item(c: C, plan, it):
    code = (it.get("code") or "").upper()
    if not dental_rules.CDT.match(code):
        raise HTTPException(400, f"{code or 'blank'} isn't a CDT code")
    tooth = dental_rules.norm_tooth(it.get("tooth"))
    row = m.TreatmentItem(org_key=c.org, plan_id=plan.id, phase=int(it.get("phase") or 1), priority=it.get("priority") or "needed", code=code, tooth=tooth,
                          surface=dental_rules.norm_surface(it.get("surface")), quad=(it.get("quad") or "").upper(),
                          fee=float(it.get("fee") if it.get("fee") not in (None, "") else fee_for(c, code)))
    c.db.add(row)
    cond = CHART_MARK.get(code)
    if tooth and cond and cond != "extracted":
        c.add(m.ToothCondition(mrn=plan.mrn, tooth=tooth, surface=row.surface, condition=cond, status="planned", code=code, date=today()))
    elif tooth and cond == "extracted":
        c.add(m.ToothCondition(mrn=plan.mrn, tooth=tooth, condition="extracted", status="planned", code=code, date=today()))
    return row


@router.post("/plans/{pid}/items")
async def plan_add_items(pid: int, request: Request, c: C = Depends(need("clinical_write"))):
    plan = c.get(m.TreatmentPlan, pid)
    b = await request.json()
    for it in b.get("items") or [b]:
        _add_item(c, plan, it)
    c.db.commit()
    return plan_estimate(c, plan)


@router.patch("/plan-items/{iid}")
async def plan_item_edit(iid: int, request: Request, c: C = Depends(need("clinical_write"))):
    it = c.get(m.TreatmentItem, iid)
    b = await request.json()
    for k in ("phase", "priority", "status", "fee"):
        if k in b:
            setattr(it, k, b[k])
    c.db.commit()
    return plan_estimate(c, c.get(m.TreatmentPlan, it.plan_id))


@router.delete("/plan-items/{iid}")
def plan_item_delete(iid: int, c: C = Depends(need("clinical_write"))):
    it = c.get(m.TreatmentItem, iid)
    plan = c.get(m.TreatmentPlan, it.plan_id)
    for x in c.q(m.ToothCondition, m.ToothCondition.mrn == plan.mrn, m.ToothCondition.tooth == it.tooth, m.ToothCondition.code == it.code, m.ToothCondition.status == "planned"):
        c.db.delete(x)
    c.db.delete(it)
    c.db.commit()
    return plan_estimate(c, plan)


@router.post("/plans/{pid}/estimate")
def plan_reestimate(pid: int, c: C = Depends(need("front"))):
    return plan_estimate(c, c.get(m.TreatmentPlan, pid))


@router.patch("/plans/{pid}")
async def plan_edit(pid: int, request: Request, c: C = Depends(need("clinical_write"))):
    plan = c.get(m.TreatmentPlan, pid)
    b = await request.json()
    for k in ("name", "status", "note"):
        if k in b:
            setattr(plan, k, b[k])
    c.db.commit()
    return plan.as_dict(skip=("signature",))


@router.post("/plans/{pid}/present")
def plan_present(pid: int, c: C = Depends(need("front"))):
    """The patient-facing presentation: a link the patient opens on the office tablet or their phone."""
    plan = c.get(m.TreatmentPlan, pid)
    est = plan_estimate(c, plan)
    plan.token = plan.token or secrets.token_urlsafe(24)
    plan.status = "presented" if plan.status == "draft" else plan.status
    plan.presented_at = plan.presented_at or dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    c.db.commit()
    return {"url": f"/dental/plan/{plan.token}", "estimate": est}


@public.get("/plan/{token}")
def plan_public(token: str, request: Request, db: Session = Depends(auth.get_db)):
    plan = db.scalar(select(m.TreatmentPlan).where(m.TreatmentPlan.token == token)) if len(token) > 20 else None
    if not plan:
        raise HTTPException(404, "This link has expired")
    row = db.scalar(select(auth.OrgPatient).where(auth.OrgPatient.org_key == plan.org_key, auth.OrgPatient.mrn == plan.mrn))
    items = db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.plan_id == plan.id).order_by(m.TreatmentItem.phase, m.TreatmentItem.id)).all()
    conds = db.scalars(select(m.ToothCondition).where(m.ToothCondition.org_key == plan.org_key, m.ToothCondition.mrn == plan.mrn)).all()
    loc = db.scalar(select(m.Location).where(m.Location.org_key == plan.org_key))
    fee_desc = {f.code: f.desc for f in db.scalars(select(m.FeeSchedule).where(m.FeeSchedule.org_key == plan.org_key))}
    audit.record(None, org_key=plan.org_key, email="patient (plan link)", action="view", what="treatment plan", record_id=str(plan.id),
                 method="GET", path="/api/public/dental/plan", ip=audit.client_ip(request))
    first = (row.name.split(", ")[1] if row and ", " in row.name else (row.name if row else "")).split(" ")[0]
    return {"first_name": first, "practice": loc.name if loc else "", "name": plan.name, "status": plan.status, "signed": bool(plan.signature),
            "items": [{"id": i.id, "phase": i.phase, "priority": i.priority, "code": i.code, "tooth": i.tooth, "surface": i.surface, "quad": i.quad,
                       "label": PLAIN.get(i.code) or fee_desc.get(i.code) or i.code, "fee": i.fee, "insurance": i.est_insurance, "patient": i.est_patient, "status": i.status} for i in items],
            "conditions": [{"tooth": x.tooth, "surface": x.surface, "condition": x.condition, "status": x.status} for x in conds]}


@public.post("/plan/{token}/accept")
async def plan_public_accept(token: str, request: Request, db: Session = Depends(auth.get_db)):
    plan = db.scalar(select(m.TreatmentPlan).where(m.TreatmentPlan.token == token)) if len(token) > 20 else None
    if not plan:
        raise HTTPException(404, "This link has expired")
    b = await request.json()
    sig = (b.get("signature") or "").strip()
    if len(sig) < 2:
        raise HTTPException(400, "Type your full name to sign")
    keep = {int(x) for x in b.get("accepted") or []}
    items = db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.plan_id == plan.id)).all()
    for i in items:
        if i.status == "proposed":
            i.status = "accepted" if i.id in keep else "declined"
    n = sum(1 for i in items if i.status in ("accepted", "scheduled", "completed"))
    plan.status = "accepted" if n == len(items) else "partial" if n else "declined"
    plan.signature, plan.decided_at = f"{sig} | {dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat()}Z | {audit.client_ip(request)}", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    db.commit()
    audit.record(None, org_key=plan.org_key, email="patient (plan link)", action="change", what="treatment plan", record_id=str(plan.id),
                 method="POST", path="/api/public/dental/plan/accept", ip=audit.client_ip(request), detail=plan.status)
    return {"ok": True, "status": plan.status}


# Plain-language names for the patient presentation (our own words, not ADA descriptors)
PLAIN = {"D0120": "Checkup exam", "D0150": "New patient exam", "D0140": "Exam for a specific problem", "D0180": "Gum health exam",
         "D0210": "Full set of X-rays", "D0220": "X-ray of one tooth", "D0274": "Checkup X-rays (4)", "D0272": "Checkup X-rays (2)", "D0330": "Panoramic X-ray",
         "D1110": "Cleaning", "D1120": "Child cleaning", "D1206": "Fluoride", "D1351": "Sealant", "D2140": "Silver filling, 1 surface", "D2150": "Silver filling, 2 surfaces",
         "D2330": "Tooth-colored filling, 1 surface (front)", "D2331": "Tooth-colored filling, 2 surfaces (front)", "D2391": "Tooth-colored filling, 1 surface",
         "D2392": "Tooth-colored filling, 2 surfaces", "D2393": "Tooth-colored filling, 3 surfaces", "D2394": "Tooth-colored filling, 4+ surfaces",
         "D2740": "Porcelain crown", "D2750": "Porcelain-fused-to-metal crown", "D2950": "Crown buildup", "D2954": "Post and core",
         "D3310": "Root canal (front tooth)", "D3320": "Root canal (premolar)", "D3330": "Root canal (molar)", "D4341": "Deep cleaning, one area",
         "D4342": "Deep cleaning, small area", "D4346": "Cleaning with gum inflammation", "D4910": "Gum maintenance cleaning", "D5110": "Upper full denture",
         "D5120": "Lower full denture", "D6010": "Implant post", "D6065": "Implant crown", "D6058": "Implant crown", "D7140": "Tooth removal", "D7210": "Surgical tooth removal",
         "D7240": "Wisdom tooth removal", "D7953": "Bone graft where the tooth was", "D6057": "Implant connector (abutment)", "D2962": "Porcelain veneer", "D9110": "Emergency pain relief", "D9944": "Night guard", "D9230": "Laughing gas"}


# ── images ──────────────────────────────────────────────────────────────────────────────────────────────────────
@router.post("/patients/{mrn}/images")
async def image_add(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    """Upload (data URL) or acquire from a device bridge. A device in mock mode draws a synthetic image from the chart."""
    patient(c, mrn)
    b = await request.json()
    kind, teeth = b.get("kind") or "bitewing", [str(t) for t in b.get("teeth") or []]
    if b.get("device"):
        res = vendors.vendor("imaging").acquire(b["device"], kind, teeth)
        if not res.ok:
            raise HTTPException(400, res.message)
        conds = [x.as_dict() for x in c.q(m.ToothCondition, m.ToothCondition.mrn == mrn) if x.status != "planned"]
        data, mime, synth = clin.synthetic_radiograph(kind, teeth or TEETH_FOR.get(kind, []), conds, res.ref), "image/svg+xml", 1
    else:
        url = b.get("data") or ""
        mt = re.match(r"^data:(image/(?:png|jpeg|webp|svg\+xml));base64,", url)
        if not mt:
            raise HTTPException(400, "Upload a PNG, JPEG or WebP image")
        if len(url) > 8 * 1024 * 1024:
            raise HTTPException(413, "Images over 6 MB go to the imaging archive, not the chart")
        data, mime, synth = url, mt.group(1), 0
    row = c.add(m.Image(mrn=mrn, kind=kind, teeth=teeth or TEETH_FOR.get(kind, []), taken=b.get("taken") or today(), device=b.get("device", "upload"), mime=mime, data=data, synthetic=synth))
    c.db.commit()
    return row.as_dict(skip=("data",))


TEETH_FOR = {"bitewing": ["2", "3", "4", "5", "28", "29", "30", "31"], "pa": ["30"], "pano": [str(t) for t in range(1, 33)],
             "fmx": [str(t) for t in range(1, 33)], "cbct": [str(t) for t in range(1, 33)], "photo": [], "scan": []}


@router.get("/images/{iid}")
def image_get(iid: int, c: C = Depends(need("clinical_read"))):
    x = c.get(m.Image, iid)
    return dict(x.as_dict(), data=x.data)


@router.post("/images/{iid}/ai")
def image_ai(iid: int, c: C = Depends(need("clinical_read"))):
    """Sends the image to the configured FDA-cleared vendor. Their findings are shown as theirs, for the dentist to accept or dismiss."""
    x = c.get(m.Image, iid)
    res = vendors.vendor("radiograph_ai").analyze(f"{x.org_key}:{x.id}", x.kind, x.teeth)
    x.ai, x.ai_status = res.data if res.ok else {"error": res.message}, "complete" if res.ok else "failed"
    c.db.commit()
    return x.as_dict(skip=("data",))


@router.patch("/images/{iid}")
async def image_review(iid: int, request: Request, c: C = Depends(need("clinical_write"))):
    x = c.get(m.Image, iid)
    b = await request.json()
    if b.get("ai_review") in ("accepted", "dismissed", ""):
        x.ai_review = b["ai_review"]
        if b["ai_review"] == "accepted":
            for f in (x.ai or {}).get("findings", []):
                if f["finding"] == "caries" and not c.db.scalar(select(m.ToothCondition.id).where(m.ToothCondition.org_key == c.org, m.ToothCondition.mrn == x.mrn,
                                                                                               m.ToothCondition.tooth == f["tooth"], m.ToothCondition.condition == "caries")):
                    c.add(m.ToothCondition(mrn=x.mrn, tooth=f["tooth"], surface=f.get("surface", ""), condition="caries", status="existing", date=today(),
                                           note=f"From AI finding ({(x.ai or {}).get('vendor', '')}), accepted by {c.email}"))
    c.db.commit()
    return x.as_dict(skip=("data",))


# ── practice setup ──────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/setup")
def setup(c: C = Depends(need("read"))):
    return {
        "locations": [x.as_dict() for x in c.q(m.Location)],
        "operatories": [x.as_dict() for x in c.q(m.Operatory).order_by(m.Operatory.location_id, m.Operatory.sort)],
        "providers": [x.as_dict() for x in c.q(m.Provider)],
        "hours": [x.as_dict() for x in c.q(m.ProviderHours)],
        "fees": [x.as_dict() for x in c.q(m.FeeSchedule, m.FeeSchedule.payer == "").order_by(m.FeeSchedule.code)],
        "membership_plans": [x.as_dict() for x in c.q(m.MembershipPlan)],
        "form_templates": [x.as_dict() for x in c.q(m.FormTemplate)],
        "vendors": vendors.status(),
        "role": c.user.role, "me": c.email,
    }


_SETUP = {"locations": (m.Location, ("name", "addr", "city", "state", "zip", "phone", "npi", "tin", "timezone", "region", "online_booking", "active")),
          "operatories": (m.Operatory, ("location_id", "name", "kind", "sort")),
          "providers": (m.Provider, ("name", "first", "last", "cred", "kind", "npi", "color", "user_email", "dea", "active")),
          "hours": (m.ProviderHours, ("provider_id", "location_id", "operatory_id", "weekday", "start", "end", "lunch_start", "lunch_end")),
          "fees": (m.FeeSchedule, ("code", "desc", "fee", "minutes", "payer")),
          "membership-plans": (m.MembershipPlan, ("name", "monthly", "annual", "included", "discount_pct", "active"))}


@router.post("/setup/{kind}")
async def setup_save(kind: str, request: Request, c: C = Depends(need("admin"))):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup")
    model, fields = _SETUP[kind]
    b = await request.json()
    if kind == "providers" and b.get("npi") and not re.fullmatch(r"\d{10}", b["npi"]):
        raise HTTPException(400, "NPI must be 10 digits")
    row = c.get(model, b["id"]) if b.get("id") else c.add(model())
    for k in fields:
        if k in b:
            setattr(row, k, b[k])
    c.db.commit()
    return row.as_dict()


@router.delete("/setup/{kind}/{rid}")
def setup_delete(kind: str, rid: int, c: C = Depends(need("admin"))):
    if kind not in _SETUP:
        raise HTTPException(404, "Unknown setup")
    c.db.delete(c.get(_SETUP[kind][0], rid))
    c.db.commit()
    return {"ok": True}


# ── schedule ────────────────────────────────────────────────────────────────────────────────────────────────────
def appt_dict(a: m.Appointment, provs: dict, names: dict = None) -> dict:
    d = a.as_dict(skip=("request", "note"))
    d["end"] = (a.start + dt.timedelta(minutes=a.minutes)).isoformat()
    p = provs.get(a.provider_id)
    d["provider"] = p.name if p else ""
    d["color"] = p.color if p else "#8a94a3"
    if names is not None:
        d["patient"] = names.get(a.mrn, "") or ((a.request or {}).get("name", "") + " (online request)" if a.request else "")
    return d


@router.get("/schedule")
def schedule(date: str = "", location_id: int = 0, days: int = 1, c: C = Depends(need("read"))):
    day = dt.date.fromisoformat(date) if date else dt.date.today()
    start, end = dt.datetime.combine(day, dt.time()), dt.datetime.combine(day + dt.timedelta(days=max(1, min(days, 14))), dt.time())
    provs = providers(c)
    locs = c.q(m.Location).all()
    loc = next((l for l in locs if l.id == location_id), locs[0] if locs else None)
    ops = c.q(m.Operatory, m.Operatory.location_id == loc.id).order_by(m.Operatory.sort).all() if loc else []
    wh = [m.Appointment.start >= start, m.Appointment.start < end]
    if loc:
        wh.append(or_(m.Appointment.location_id == loc.id, m.Appointment.location_id.is_(None)))
    appts = c.q(m.Appointment, *wh).order_by(m.Appointment.start).all()
    names, flags = {}, {}
    mrns = {a.mrn for a in appts if a.mrn}
    if mrns:
        for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org, auth.OrgPatient.mrn.in_(mrns))):
            d = patient_dict(r)
            names[r.mrn] = r.name
            flags[r.mrn] = {"age": _age(r.dob), "allergies": d.get("allergies", ""), "medical": d.get("problems", ""), "phone": d.get("phone", "")}
        for v in c.q(m.Coverage, m.Coverage.mrn.in_(mrns), m.Coverage.rank == "primary"):
            f = flags.setdefault(v.mrn, {})
            f.update({"payer": v.payer, "elig": v.checked_at.isoformat()[:10] if v.checked_at else "", "elig_active": bool(v.active)})
        bal = {}
        for e in c.q(m.LedgerEntry, m.LedgerEntry.mrn.in_(mrns)):
            bal[e.mrn] = bal.get(e.mrn, 0) + e.amount
        import dental_api_rcm   # here, not at the top: it imports this module
        pending = dental_api_rcm.insurance_pending(c)
        for k, v in bal.items():
            flags.setdefault(k, {})["balance"] = round(max(0.0, v - pending.get(k, 0)), 2)   # what the patient owes now
    hours = [h.as_dict() for h in c.q(m.ProviderHours, m.ProviderHours.location_id == (loc.id if loc else 0), m.ProviderHours.weekday == day.weekday())]
    out = []
    for a in appts:
        d = appt_dict(a, provs, names)
        d["flags"] = flags.get(a.mrn, {})
        out.append(d)
    return {"date": day.isoformat(), "location": loc.as_dict() if loc else None, "locations": [l.as_dict() for l in locs],
            "operatories": [o.as_dict() for o in ops], "providers": [p.as_dict() for p in provs.values()], "hours": hours, "appointments": out}


def _clash(c: C, a: m.Appointment):
    end = a.start + dt.timedelta(minutes=a.minutes)
    for o in c.q(m.Appointment, m.Appointment.id != (a.id or 0), m.Appointment.status.notin_(("cancelled", "no_show")),
                 m.Appointment.start < end, m.Appointment.start >= a.start - dt.timedelta(hours=8)):
        o_end = o.start + dt.timedelta(minutes=o.minutes)
        if o.start < end and o_end > a.start and ((a.operatory_id and o.operatory_id == a.operatory_id) or (a.provider_id and o.provider_id == a.provider_id)):
            return o
    return None


@router.post("/appointments")
async def appt_save(request: Request, c: C = Depends(need("front"))):
    b = await request.json()
    a = c.get(m.Appointment, b["id"]) if b.get("id") else c.add(m.Appointment(status="scheduled", source="staff"))
    if b.get("mrn") is not None:
        if b["mrn"]:
            patient(c, b["mrn"])
        a.mrn = b["mrn"]
    for k in ("location_id", "provider_id", "operatory_id", "minutes", "reason", "status", "procedures", "plan_item_ids"):
        if k in b:
            setattr(a, k, b[k])
    if b.get("start"):
        a.start = dt.datetime.fromisoformat(b["start"][:16])
    if not a.start:
        raise HTTPException(400, "Start time is required")
    if b.get("status") == "confirmed":
        a.confirmed_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if b.get("note") is not None:
        a.note = b["note"]
    if not a.mrn and not a.request:
        raise HTTPException(400, "Choose a patient")
    if a.status not in ("cancelled", "no_show"):
        o = _clash(c, a)
        if o and not b.get("force"):
            c.db.rollback()
            raise HTTPException(409, f"That chair or provider is booked {o.start.strftime('%-I:%M %p')} to {(o.start + dt.timedelta(minutes=o.minutes)).strftime('%-I:%M %p')}")
    for iid in a.plan_item_ids or []:
        it = c.db.get(m.TreatmentItem, int(iid))
        if it and it.org_key == c.org and it.status in ("proposed", "accepted"):
            it.status, it.appt_id = "scheduled", a.id
    c.db.commit()
    return appt_dict(a, providers(c))


@router.delete("/appointments/{aid}")
def appt_delete(aid: int, c: C = Depends(need("front"))):
    a = c.get(m.Appointment, aid)
    a.status = "cancelled"
    c.db.commit()
    return {"ok": True}


# ── synthetic practice ──────────────────────────────────────────────────────────────────────────────────────────
@router.post("/sample")
def sample_load(c: C = Depends(need("admin"))):
    import dental_synth
    return dental_synth.load(c)


@router.delete("/sample")
def sample_remove(c: C = Depends(need("admin"))):
    import dental_synth
    return dental_synth.remove(c)
