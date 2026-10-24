"""Dental revenue cycle API ("the EMR that gets your claims paid"): eligibility and benefits, claims (837D) with
narratives and attachments, pre-authorizations, ERA (835) auto-posting, the denial work queue, and patient billing
(text-to-pay, payment plans, membership plans). Rules live in dental_rcm.py and X12 in dental_x12.py; this file stores
and sends. Every vendor call goes through dental_vendors (mocks until contracts are signed).
"""
import datetime as dt
import secrets

from fastapi import Depends, HTTPException, Request
from fastapi.responses import Response
from sqlalchemy import select, func
from sqlalchemy.orm import Session

import audit
import auth
import dental_db as m
import dental_rcm as rcm
import dental_rules
import dental_vendors as vendors
import dental_x12 as x12
from dental_api import router, public, need, C, patient, patient_dict, coverage, history, balance, providers, today, _age, fee_for


def _hist_add(claim: m.Claim, text: str):
    claim.history = (claim.history or []) + [[dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds"), text]]


# ── eligibility and benefits ────────────────────────────────────────────────────────────────────────────────────
COMMON = ["D0120", "D0150", "D0274", "D0210", "D0330", "D1110", "D4910", "D1206", "D4341", "D2740"]


def check_eligibility(c: C, mrn: str) -> dict:
    p = patient_dict(patient(c, mrn))
    cov = coverage(c, mrn)
    if not cov or not cov.payer or cov.payer == "Self-pay":
        raise HTTPException(400, "Add the patient's dental plan first")
    if not cov.member_id:
        raise HTTPException(400, "Add the subscriber ID first")
    loc = c.q(m.Location).first()
    name = p.get("name", "")
    last, first = (name.split(", ") + [""])[:2] if ", " in name else (name, "")
    req = {"payer": cov.payer, "payer_id": cov.payer_id, "member_id": cov.member_id, "last": last, "first": first, "dob": p.get("dob"), "sex": p.get("sex"),
           "provider_name": loc.name if loc else "", "npi": loc.npi if loc else "", "trace": f"E{mrn}"}
    x270 = x12.build_270(req)
    try:
        x271 = vendors.vendor("clearinghouse").eligibility(x270, req)
    except vendors.VendorError as e:
        raise HTTPException(502, str(e))
    b = x12.parse_271(x271)
    cov.benefits, cov.raw_271, cov.active, cov.checked_at = b, x271, 1 if b["active"] else 0, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    if b.get("group"):
        cov.group = b["group"]
    c.db.commit()
    return benefits_view(c, mrn, cov, p)


def benefits_view(c: C, mrn: str, cov: m.Coverage, p: dict) -> dict:
    """Benefits as the front desk needs them: remaining max, deductible, coverage by category, and for each common
    procedure whether the plan will pay for it today and when it will next."""
    b = cov.benefits or {}
    hist = history(c, mrn)
    freq = rcm.frequency_check(b, hist, [{"code": k} for k in COMMON], today(), _age(p.get("dob"))) if b else []
    seen = set()
    rows = []
    for k, f in zip(COMMON, freq):
        rule = rcm._rule_for(b, k)
        if not rule:
            continue
        grp = frozenset(rcm._group_of(k))
        if grp in seen:
            continue
        seen.add(grp)
        last = max((h["date"] for h in hist if h["code"] in grp), default="")
        per = f"{rule['limit']}x / " + (f"{rule['per_months']} mo" if rule.get("per_months") else "calendar yr")
        rows.append({"code": k, "label": FREQ_LABEL.get(k, k), "rule": per + (" per tooth" if rule.get("per_tooth") else "") + (" per quad" if rule.get("per_quadrant") else ""),
                     "last": last, "ok": f["ok"], "next": f["next_eligible"], "reason": f["reason"]})
    return {"mrn": mrn, "payer": cov.payer, "member_id": cov.member_id, "group": cov.group, "checked_at": cov.checked_at.isoformat() if cov.checked_at else None,
            "active": bool(cov.active), "plan_name": b.get("plan_name", ""), "annual_max": b.get("annual_max"), "max_used": b.get("max_used"),
            "max_remaining": b.get("max_remaining"), "deductible": b.get("deductible"), "deductible_remaining": b.get("deductible_remaining"),
            "ortho_lifetime_max": b.get("ortho_lifetime_max"), "coinsurance": b.get("coinsurance", {}), "waiting_periods": b.get("waiting_periods", []),
            "messages": list(dict.fromkeys(b.get("messages", [])))[:12], "frequency": rows, "environment": "mock" if vendors.status()[0]["mock"] else "live"}


FREQ_LABEL = {"D0120": "Exams", "D0274": "Bitewings", "D0210": "FMX / pano", "D1110": "Cleanings / perio maint.", "D1206": "Fluoride",
              "D4341": "SRP (per quad)", "D2740": "Crowns (per tooth)"}


@router.post("/patients/{mrn}/eligibility")
def eligibility(mrn: str, c: C = Depends(need("front"))):
    return check_eligibility(c, mrn)


@router.get("/patients/{mrn}/benefits")
def benefits(mrn: str, c: C = Depends(need("front"))):
    cov = coverage(c, mrn)
    if not cov:
        return {"mrn": mrn, "payer": "", "active": False, "none": True}
    return benefits_view(c, mrn, cov, patient_dict(patient(c, mrn)))


@router.post("/patients/{mrn}/coverage")
async def coverage_save(mrn: str, request: Request, c: C = Depends(need("front"))):
    patient(c, mrn)
    b = await request.json()
    rank = b.get("rank") or "primary"
    cov = coverage(c, mrn, rank) or c.add(m.Coverage(mrn=mrn, rank=rank))
    for k in ("payer", "payer_id", "member_id", "group", "subscriber"):
        if k in b:
            setattr(cov, k, (b[k] or "").strip())
    cov.benefits, cov.active, cov.checked_at = None, 0, None   # re-verify after a change
    c.db.commit()
    return cov.as_dict(skip=("raw_271",))


@router.post("/eligibility/batch")
async def eligibility_batch(request: Request, c: C = Depends(need("front"))):
    """Verifies everyone booked on a day (the night-before run)."""
    b = await request.json()
    day = dt.date.fromisoformat(b.get("date") or (dt.date.today() + dt.timedelta(days=1)).isoformat())
    appts = c.q(m.Appointment, m.Appointment.start >= dt.datetime.combine(day, dt.time()), m.Appointment.start < dt.datetime.combine(day + dt.timedelta(days=1), dt.time()),
                m.Appointment.status.notin_(("cancelled", "no_show"))).all()
    done, skipped = [], []
    for mrn in sorted({a.mrn for a in appts if a.mrn}):
        try:
            v = check_eligibility(c, mrn)
            done.append({"mrn": mrn, "active": v["active"], "max_remaining": v["max_remaining"]})
        except HTTPException as e:
            skipped.append({"mrn": mrn, "reason": e.detail})
    return {"date": day.isoformat(), "checked": done, "skipped": skipped}


@router.post("/patients/{mrn}/estimate")
async def estimate(mrn: str, request: Request, c: C = Depends(need("front"))):
    p = patient_dict(patient(c, mrn))
    b = await request.json()
    lines = [dict(l, fee=l.get("fee") if l.get("fee") not in (None, "") else fee_for(c, (l.get("code") or "").upper())) for l in b.get("lines") or []]
    cov = coverage(c, mrn)
    return rcm.estimate((cov.benefits or {}) if cov else {}, lines, history(c, mrn), today(), _age(p.get("dob")))


# ── coding a visit, the way the medical encounter does ─────────────────────────────────────────────────────────
@router.post("/patients/{mrn}/code-note")
async def code_visit(mrn: str, request: Request, c: C = Depends(need("clinical_read"))):
    """CDT codes for a visit note: the AI coder (dental_coding) reads the note, and the dental rules add anything it
    missed or billed low. Each code comes with its tooth, surfaces, confidence and the reason. Nothing is billed here;
    the dentist keeps or drops each code and signs."""
    p = patient_dict(patient(c, mrn))
    b = await request.json()
    note = (b.get("note") or "").strip()
    if len(note) < 15:
        return {"codes": [], "ai": False, "check": check_lines(c, mrn, [])}
    ai_codes, ai = [], False
    try:
        import dental_coding
        import main
        key, hit = main._ai_cache_get("dental_code", {"note": note})
        ai_codes = hit if hit is not None else dental_coding.clean(dental_coding._ask_ai(note))
        if hit is None:
            main._ai_cache_put(key, ai_codes)
        ai = True
    except HTTPException as e:
        if e.status_code == 429:
            raise
    except Exception as e:
        print(f"[DENTAL CODING] {type(e).__name__}")   # no AI key or the service is down: the rules still code the note
    fees = {f.code: f for f in c.q(m.FeeSchedule, m.FeeSchedule.payer == "")}
    out = []
    for x in ai_codes:
        if x.get("type") != "CDT":
            continue
        out.append({"code": x["code"], "tooth": x.get("tooth", ""), "surface": x.get("surface", ""), "quad": x.get("quad", ""),
                    "desc": fees[x["code"]].desc if x["code"] in fees else x.get("description", ""), "conf": x.get("confidence", 0),
                    "why": (x.get("justification", "") + (" " + x["documentation_gap"] if x.get("documentation_gap") else "")).strip(), "source": "ai"})
    # the rules: what the note documents that the AI left out, and codes it billed lower than documented
    for f in rcm.find_missed(note, [{"code": o["code"], "tooth": o["tooth"], "surface": o["surface"], "quad": o["quad"]} for o in out], _age(p.get("dob"))):
        row = {"code": f["code"], "tooth": f["tooth"], "surface": f["surface"], "quad": f["quad"], "desc": fees[f["code"]].desc if f["code"] in fees else "",
               "conf": 85 if f["kind"] == "missed" else 80, "source": "rules",
               "why": f"From the note: \u201c{f['evidence'].strip()}\u201d" if f["kind"] == "missed" and f.get("evidence") else f["message"]}
        if f["kind"] == "undercoded" and f.get("billed"):
            low = next((o for o in out if o["code"] == f["billed"] and (not f["tooth"] or o["tooth"] == f["tooth"])), None)
            if low:
                low.update(row, why=f"{f['message']} (was {f['billed']})")
                continue
        out.append(row)
    return {"codes": out, "ai": ai, "check": check_lines(c, mrn, out)}


@router.post("/patients/{mrn}/claim-preview")
async def claim_preview(mrn: str, request: Request, c: C = Depends(need("clinical_read"))):
    b = await request.json()
    return check_lines(c, mrn, b.get("lines") or [])


def check_lines(c: C, mrn: str, lines: list) -> dict:
    """The claim check for codes that aren't a claim yet: dental_rules, the plan's frequency limits, what the payer will
    want attached, and the insurance estimate."""
    p = patient_dict(patient(c, mrn))
    lines = [dict(l, code=(l.get("code") or "").upper(), fee=l.get("fee") if l.get("fee") not in (None, "") else fee_for(c, (l.get("code") or "").upper())) for l in lines if l.get("code")]
    edits = [{"lvl": "red" if f["severity"] == "error" else "amb", "msg": f["message"]} for f in dental_rules.check({"lines": lines, "dos": today(), "dob": p.get("dob")})] if lines else []
    cov = coverage(c, mrn)
    est = None
    if cov and cov.benefits and lines:
        prior = [h for h in history(c, mrn) if h["date"] != today()]
        for f in rcm.frequency_check(cov.benefits, prior, lines, today(), _age(p.get("dob"))):
            if not f["ok"]:
                edits.append({"lvl": "amb", "msg": f"{f['code']}: {f['reason']}" + (f"; covered again {f['next_eligible']}" if f["next_eligible"] else "")})
        est = rcm.estimate(cov.benefits, lines, prior, today(), _age(p.get("dob")))
    elif lines and cov and cov.payer:
        edits.append({"lvl": "amb", "msg": "Benefits haven't been verified for this patient"})
    for n in rcm.attachments_needed(lines):
        if n["kind"] != "narrative":
            edits.append({"lvl": "amb", "msg": f"{n['code']}{(' #' + n['tooth']) if n.get('tooth') else ''}: the payer will want a {n['why']} (attach it on the claim)"})
    seen, uniq = set(), []
    for e in edits:
        if e["msg"] not in seen:
            seen.add(e["msg"])
            uniq.append(e)
    red = sum(e["lvl"] == "red" for e in uniq)
    return {"edits": uniq, "score": max(5, 98 - 25 * red - 7 * (len(uniq) - red)) if lines else None, "total": round(sum(float(l["fee"] or 0) for l in lines), 2),
            "insurance": est["insurance"] if est else None, "patient": est["patient"] if est else None, "insured": bool(cov and cov.payer and cov.payer != "Self-pay")}


# ── claims ──────────────────────────────────────────────────────────────────────────────────────────────────────
def _claim_no(c: C) -> str:
    n = c.db.scalar(select(func.count(m.Claim.id)).where(m.Claim.org_key == c.org)) or 0
    return f"DC{dt.date.today():%y%m}{n + 1:05d}"


def chart_context(c: C, mrn: str) -> dict:
    exam = c.q(m.PerioExam, m.PerioExam.mrn == mrn).order_by(m.PerioExam.date.desc()).first()
    conds = [x.as_dict() for x in c.q(m.ToothCondition, m.ToothCondition.mrn == mrn)]
    bone = next((x["condition"] for x in conds if x["condition"] == "bone_loss"), "")
    return {"conditions": conds, "perio": (exam.readings if exam else {}) or {}, "bone_loss": "radiographic bone loss" if bone or exam else ""}


def make_claim(c: C, mrn: str, procs: list, kind="claim", payer_rank="primary") -> dict:
    cov = coverage(c, mrn, payer_rank)
    if not cov or not cov.payer:
        raise HTTPException(400, "The patient has no dental plan on file")
    first = procs[0]
    claim = c.add(m.Claim(mrn=mrn, kind=kind, claim_no=_claim_no(c), payer=cov.payer, payer_id=cov.payer_id, member_id=cov.member_id,
                          dos=getattr(first, "date", "") or today(), provider_id=getattr(first, "provider_id", None), location_id=getattr(first, "location_id", None), status="draft"))
    c.db.flush()
    ctx = chart_context(c, mrn)
    narr = []
    for p in procs:
        l = m.ClaimLine(org_key=c.org, claim_id=claim.id, procedure_id=getattr(p, "id", None) if kind == "claim" else None, code=p.code, tooth=p.tooth,
                        surface=p.surface, quad=p.quad, fee=p.fee)
        c.db.add(l)
        if kind == "claim":
            p.claim_id = claim.id
        t = rcm.narrative({"code": p.code, "tooth": p.tooth, "quad": p.quad}, ctx)
        if t:
            narr.append(t)
    claim.total = round(sum(p.fee for p in procs), 2)
    claim.narrative = " ".join(narr)
    _hist_add(claim, "Created" + (" (predetermination)" if kind != "claim" else ""))
    c.db.commit()
    run_check(c, claim)
    return claim_view(c, claim)


def claim_lines(c: C, claim: m.Claim) -> list:
    return c.db.scalars(select(m.ClaimLine).where(m.ClaimLine.claim_id == claim.id).order_by(m.ClaimLine.id)).all()


def run_check(c: C, claim: m.Claim) -> dict:
    """The pre-submission scrub: dental_rules (tooth, surfaces, quadrant, frequency, conflicts), the plan's frequency
    limits from the 271, attachments and narrative the payer will want, and missing subscriber info."""
    p = patient_dict(patient(c, claim.mrn))
    lines = [{"code": l.code, "tooth": l.tooth, "surface": l.surface, "quad": l.quad, "fee": l.fee} for l in claim_lines(c, claim)]
    edits = []
    for f in dental_rules.check({"lines": lines, "dos": claim.dos, "dob": p.get("dob"), "payer": claim.payer, "member_id": claim.member_id,
                                 "attached": bool(claim.attachments), "narrative": claim.narrative}):
        edits.append({"lvl": "red" if f["severity"] == "error" else "amb", "msg": f["message"], "code": f.get("code", "")})
    cov = coverage(c, claim.mrn)
    if cov and cov.benefits:
        prior = [h for h in history(c, claim.mrn) if not (h["date"] == claim.dos)]
        for f in rcm.frequency_check(cov.benefits, prior, lines, claim.dos, _age(p.get("dob"))):
            if not f["ok"]:
                edits.append({"lvl": "amb", "msg": f"{f['code']}: {f['reason']}" + (f"; covered again {f['next_eligible']}" if f["next_eligible"] else ""), "code": "frequency"})
    elif claim.kind == "claim":
        edits.append({"lvl": "amb", "msg": "Benefits haven't been verified for this patient", "code": "eligibility"})
    have = {a.get("kind") for a in claim.attachments or []}
    for need_ in rcm.attachments_needed(lines):
        if need_["kind"] == "narrative":
            if not claim.narrative:
                edits.append({"lvl": "amb", "msg": f"{need_['code']}: payers usually want a narrative", "code": "narrative"})
        elif need_["kind"] not in have:
            edits.append({"lvl": "amb", "msg": f"{need_['code']}{(' #' + need_['tooth']) if need_.get('tooth') else ''}: attach a {need_['why']}", "code": "attach_" + need_["kind"]})
    if not claim.member_id:
        edits.append({"lvl": "red", "msg": "Subscriber ID is missing", "code": "member"})
    prov = c.db.get(m.Provider, claim.provider_id) if claim.provider_id else None
    if not prov or not prov.npi:
        edits.append({"lvl": "red", "msg": "Rendering dentist's NPI is missing", "code": "npi"})
    seen, uniq = set(), []
    for e in edits:
        if e["msg"] not in seen:
            seen.add(e["msg"])
            uniq.append(e)
    claim.edits = uniq
    if claim.status in ("draft", "ready"):
        claim.status = "ready" if not any(e["lvl"] == "red" for e in uniq) else "draft"
    if cov and cov.benefits:
        est = rcm.estimate(cov.benefits, lines, [h for h in history(c, claim.mrn) if h["date"] != claim.dos], claim.dos, _age(p.get("dob")))
        claim.est_insurance = est["insurance"]
        for l, r in zip(claim_lines(c, claim), est["lines"]):
            l.est_insurance = r["insurance"]
    score = max(5, 98 - 25 * sum(e["lvl"] == "red" for e in uniq) - 7 * sum(e["lvl"] == "amb" for e in uniq))
    c.db.commit()
    return {"edits": uniq, "score": score}


def claim_view(c: C, claim: m.Claim) -> dict:
    p = patient_dict(patient(c, claim.mrn))
    prov = c.db.get(m.Provider, claim.provider_id) if claim.provider_id else None
    lines = [l.as_dict() for l in claim_lines(c, claim)]
    red, amb = sum(e["lvl"] == "red" for e in claim.edits or []), sum(e["lvl"] == "amb" for e in claim.edits or [])
    work = [w.as_dict() for w in c.q(m.WorkItem, m.WorkItem.claim_id == claim.id)]
    return dict(claim.as_dict(skip=("x837",)), lines=lines, patient={"name": p.get("name"), "dob": p.get("dob"), "mrn": claim.mrn},
                provider=prov.name if prov else "", score=max(5, 98 - 25 * red - 7 * amb), attachments_needed=rcm.attachments_needed(lines), work=work)


@router.get("/claims")
def claims_list(status: str = "", kind: str = "claim", c: C = Depends(need("clinical_read"))):
    wh = [m.Claim.kind == kind] if kind else []
    if status:
        wh.append(m.Claim.status.in_(status.split(",")))
    rows = c.q(m.Claim, *wh).order_by(m.Claim.dos.desc(), m.Claim.id.desc()).all()
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    lines = {}
    if rows:
        for l in c.db.scalars(select(m.ClaimLine).where(m.ClaimLine.claim_id.in_([r.id for r in rows]))):
            lines.setdefault(l.claim_id, []).append(dental_rules.norm_tooth(l.tooth) and f"{l.code} #{l.tooth}{(' ' + l.surface) if l.surface else ''}" or (f"{l.code} {l.quad}".strip()))
    provs = providers(c)
    today_d = dt.date.today()
    out = []
    for r in rows:
        age = (today_d - dt.date.fromisoformat(r.dos)).days if r.dos else 0
        out.append({"id": r.id, "claim_no": r.claim_no, "mrn": r.mrn, "patient": names.get(r.mrn, ""), "payer": r.payer, "dos": r.dos, "status": r.status,
                    "total": r.total, "paid": r.paid, "est_insurance": r.est_insurance, "patient_resp": r.patient_resp, "lines": lines.get(r.id, []),
                    "edits": r.edits or [], "provider": (provs.get(r.provider_id).name if provs.get(r.provider_id) else ""), "age": age,
                    "attachments": len(r.attachments or []), "submitted_at": r.submitted_at.isoformat() if r.submitted_at else "", "sample": r.sample})
    return out


@router.get("/claims/{cid}")
def claim_get(cid: int, c: C = Depends(need("clinical_read"))):
    return claim_view(c, c.get(m.Claim, cid))


@router.post("/claims")
async def claim_create(request: Request, c: C = Depends(need("billing"))):
    """From completed procedures (procedure_ids) that aren't on a claim yet."""
    b = await request.json()
    procs = [c.get(m.Procedure, i) for i in b.get("procedure_ids") or []]
    if not procs:
        raise HTTPException(400, "Choose the procedures to bill")
    if any(p.claim_id for p in procs):
        raise HTTPException(409, "Some of those procedures are already on a claim")
    if len({p.mrn for p in procs}) > 1:
        raise HTTPException(400, "One patient per claim")
    return make_claim(c, procs[0].mrn, procs)


@router.patch("/claims/{cid}")
async def claim_edit(cid: int, request: Request, c: C = Depends(need("billing"))):
    claim = c.get(m.Claim, cid)
    if claim.status in ("submitted", "accepted", "paid", "void"):
        raise HTTPException(409, "Sent claims can't be edited; make a corrected claim instead")
    b = await request.json()
    if "narrative" in b:
        claim.narrative = b["narrative"][:2000]
    if "lines" in b:
        for l in b["lines"]:
            row = c.get(m.ClaimLine, l["id"])
            if row.claim_id != claim.id:
                raise HTTPException(400, "Line isn't on this claim")
            for k in ("code", "tooth", "surface", "quad", "fee"):
                if k in l:
                    setattr(row, k, dental_rules.norm_tooth(l[k]) if k == "tooth" else dental_rules.norm_surface(l[k]) if k == "surface" else (l[k].upper() if isinstance(l[k], str) else l[k]))
        claim.total = round(sum(x.fee for x in claim_lines(c, claim)), 2)
    if "member_id" in b:
        claim.member_id = b["member_id"]
    _hist_add(claim, "Edited by " + c.email)
    c.db.commit()
    run_check(c, claim)
    return claim_view(c, claim)


@router.post("/claims/{cid}/check")
def claim_check(cid: int, c: C = Depends(need("clinical_read"))):
    return run_check(c, c.get(m.Claim, cid))


@router.post("/claims/{cid}/narrative")
def claim_narrative(cid: int, c: C = Depends(need("billing"))):
    """Drafts the narrative from the chart (perio depths for SRP, conditions for crowns). The biller or dentist edits it."""
    claim = c.get(m.Claim, cid)
    ctx = chart_context(c, claim.mrn)
    parts = [rcm.narrative({"code": l.code, "tooth": l.tooth, "quad": l.quad}, ctx) for l in claim_lines(c, claim)]
    claim.narrative = " ".join(p for p in parts if p)
    c.db.commit()
    run_check(c, claim)
    return {"narrative": claim.narrative}


@router.post("/claims/{cid}/attach")
async def claim_attach(cid: int, request: Request, c: C = Depends(need("billing"))):
    """Attaches radiographs and the latest perio chart. They go to the attachment service when the claim is sent."""
    claim = c.get(m.Claim, cid)
    b = await request.json()
    att = list(claim.attachments or [])
    for iid in b.get("image_ids") or []:
        img = c.get(m.Image, iid)
        if img.mrn != claim.mrn:
            raise HTTPException(400, "That image belongs to another patient")
        if not any(a.get("image_id") == img.id for a in att):
            att.append({"kind": "radiograph" if img.kind != "photo" else "photo", "image_id": img.id, "label": f"{img.kind} {img.taken}", "ref": ""})
    if b.get("perio_id") or b.get("perio_latest"):
        ex = c.get(m.PerioExam, b["perio_id"]) if b.get("perio_id") else c.q(m.PerioExam, m.PerioExam.mrn == claim.mrn).order_by(m.PerioExam.date.desc()).first()
        if not ex:
            raise HTTPException(400, "No perio chart on file")
        if not any(a.get("perio_id") == ex.id for a in att):
            att.append({"kind": "perio_chart", "perio_id": ex.id, "label": f"Perio chart {ex.date}", "ref": ""})
    if b.get("remove") is not None:
        att = [a for i, a in enumerate(att) if i != int(b["remove"])]
    claim.attachments = att
    _hist_add(claim, f"Attachments: {len(att)}")
    c.db.commit()
    run_check(c, claim)
    return claim_view(c, claim)


def _x837(c: C, claim: m.Claim, predetermination=False) -> str:
    p = patient_dict(patient(c, claim.mrn))
    loc = c.db.get(m.Location, claim.location_id) if claim.location_id else c.q(m.Location).first()
    prov = c.db.get(m.Provider, claim.provider_id) if claim.provider_id else None
    name = p.get("name", "")
    last, first = (name.split(", ") + [""])[:2] if ", " in name else (name, "")
    addr = (p.get("addr") or "").split(",")
    cov = coverage(c, claim.mrn)
    data = {"id": claim.claim_no, "dos": claim.dos, "payer": claim.payer, "payer_id": claim.payer_id, "group": cov.group if cov else "",
            "frequency": claim.frequency, "original_ref": claim.original_claim_no, "preauth_ref": claim.preauth_ref, "narrative": claim.narrative,
            "attachments": claim.attachments, "provider": {"last": prov.last if prov else "", "first": prov.first if prov else "", "npi": prov.npi if prov else ""},
            "patient": {"last": last, "first": first, "dob": p.get("dob"), "sex": p.get("sex"), "member_id": claim.member_id, "addr": addr[0] if addr else "",
                        "city": addr[1].strip() if len(addr) > 1 else "", "state": (addr[2].split() or [""])[0] if len(addr) > 2 else "", "zip": (addr[2].split() + ["", ""])[1] if len(addr) > 2 else ""},
            "lines": [{"code": l.code, "tooth": l.tooth, "surface": l.surface, "quad": l.quad, "fee": l.fee, "units": l.units, "dos": claim.dos} for l in claim_lines(c, claim)]}
    practice = {"name": loc.name if loc else "", "npi": loc.npi if loc else "", "tin": loc.tin if loc else "", "addr": loc.addr if loc else "",
                "city": loc.city if loc else "", "state": loc.state if loc else "", "zip": loc.zip if loc else "", "phone": loc.phone if loc else ""}
    return x12.build_837d(data, practice, ctrl=claim.id, predetermination=predetermination), data


def _attachments_pdf(c: C, claim: m.Claim) -> bytes:
    """Every attachment on the claim (radiographs, the perio chart, the narrative) as one PDF, in the order attached."""
    import io
    from pypdf import PdfReader, PdfWriter
    import claim_attachments as ca
    p = patient_dict(patient(c, claim.mrn))
    sub = " · ".join(x for x in (f"Claim {claim.claim_no}", p.get("name", ""), f"DOS {claim.dos}" if claim.dos else "") if x)
    w = PdfWriter()
    for a in claim.attachments or []:
        try:
            if a.get("image_id"):
                img = c.get(m.Image, a["image_id"])
                if img.synthetic:      # the synthetic practice's drawn X-rays: a labeled stand-in page (real vendors refuse synthetic patients)
                    pdf, _ = ca.narrative_to_pdf(f"SYNTHETIC {img.kind} from the sample practice. Not a real radiograph.", f"Radiograph: {img.kind} {img.taken}", sub)
                    w.append(PdfReader(io.BytesIO(pdf)))
                    continue
                pdf, _ = ca.data_url_image(img.data, f"Radiograph: {img.kind} {img.taken}" + (f", teeth {', '.join(map(str, img.teeth))}" if img.teeth else ""), sub)
            elif a.get("perio_id"):
                ex = c.get(m.PerioExam, a["perio_id"])
                pdf, _ = ca.perio_to_pdf(ex.readings or {}, f"Periodontal chart {ex.date}", sub)
            else:
                continue
        except ca.BadFile as e:
            raise ValueError(f"{a.get('label') or a.get('kind')}: {e}")
        w.append(PdfReader(io.BytesIO(pdf)))
    if claim.narrative:
        pdf, _ = ca.narrative_to_pdf(claim.narrative, "Narrative", sub)
        w.append(PdfReader(io.BytesIO(pdf)))
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


@router.post("/claims/{cid}/submit")
def claim_submit(cid: int, c: C = Depends(need("billing"))):
    claim = c.get(m.Claim, cid)
    if claim.status not in ("draft", "ready", "rejected"):
        raise HTTPException(409, f"This claim is {claim.status}")
    chk = run_check(c, claim)
    reds = [e["msg"] for e in chk["edits"] if e["lvl"] == "red"]
    if reds:
        raise HTTPException(422, "Fix first: " + "; ".join(reds))
    av = vendors.vendor("attachments")
    docs = None
    if claim.attachments and any(not a.get("ref") for a in claim.attachments):
        try:     # render the documents first, so a file that can't be sent stops the claim instead of going out without it
            docs = _attachments_pdf(c, claim)
        except ValueError as e:
            raise HTTPException(422, f"The attachments can't be sent: {e}")
        prep = av.prepare(claim.claim_no, claim.attachments)
        if not prep.ok:
            raise HTTPException(502, "Attachments: " + prep.message)
        claim.attachments = [dict(a, ref=prep.ref) for a in claim.attachments]
    x, data = _x837(c, claim)
    res = vendors.vendor("clearinghouse").submit_claim(x, data)
    claim.x837 = x
    if res.ok:
        claim.status, claim.submitted_at, claim.payer_claim_id = "submitted", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), res.ref
        _hist_add(claim, res.message)
        if docs is not None:
            ref = claim.attachments[0]["ref"]
            dr = av.deliver(claim.claim_no, claim.attachments, ref, docs)
            claim.attachments = [dict(a, sent=today() if dr.ok else "", failed="" if dr.ok else dr.message[:200]) for a in claim.attachments]
            _hist_add(claim, f"Attachments ({len(claim.attachments)}): " + dr.message)
            if not dr.ok:
                c.add(m.WorkItem(kind="attachment", mrn=claim.mrn, claim_id=claim.id, title=f"{claim.claim_no}: attachments weren't sent", how=dr.message[:300],
                                 action="attach", amount=claim.total))
    else:
        claim.status = "rejected"
        _hist_add(claim, res.message)
        c.add(m.WorkItem(kind="rejection", mrn=claim.mrn, claim_id=claim.id, title=f"{claim.claim_no} rejected by the clearinghouse", how=res.message[:300], action="fix", amount=claim.total))
    c.db.commit()
    return dict(claim_view(c, claim), result=res.as_dict())


@router.post("/claims/submit-ready")
def claims_submit_ready(c: C = Depends(need("billing"))):
    sent, held = [], []
    for claim in c.q(m.Claim, m.Claim.status == "ready", m.Claim.kind == "claim").all():
        try:
            claim_submit(claim.id, c)
            sent.append(claim.claim_no)
        except HTTPException as e:
            held.append({"claim": claim.claim_no, "why": e.detail})
    return {"sent": sent, "held": held}


@router.get("/claims/{cid}/837")
def claim_837(cid: int, c: C = Depends(need("billing"))):
    claim = c.get(m.Claim, cid)
    x = claim.x837 or _x837(c, claim, claim.kind == "predetermination")[0]
    return Response(x.replace("~", "~\n"), media_type="text/plain", headers={"Content-Disposition": f"attachment; filename={claim.claim_no}.837"})


@router.post("/claims/{cid}/corrected")
def claim_corrected(cid: int, c: C = Depends(need("billing"))):
    """A replacement claim (frequency 7) copying the original, for a fix after a denial."""
    old = c.get(m.Claim, cid)
    new = c.add(m.Claim(mrn=old.mrn, kind=old.kind, claim_no=_claim_no(c), payer=old.payer, payer_id=old.payer_id, member_id=old.member_id, dos=old.dos,
                        provider_id=old.provider_id, location_id=old.location_id, status="draft", total=old.total, narrative=old.narrative, attachments=old.attachments,
                        frequency="7", original_claim_no=old.payer_claim_id or old.claim_no, preauth_ref=old.preauth_ref))
    c.db.flush()
    for l in claim_lines(c, old):
        c.db.add(m.ClaimLine(org_key=c.org, claim_id=new.id, procedure_id=l.procedure_id, code=l.code, tooth=l.tooth, surface=l.surface, quad=l.quad, fee=l.fee))
    _hist_add(new, f"Corrected claim for {old.claim_no}")
    _hist_add(old, f"Replaced by {new.claim_no}")
    old.status = "void" if old.status in ("denied", "rejected") else old.status
    c.db.commit()
    run_check(c, new)
    return claim_view(c, new)


# ── pre-authorizations ──────────────────────────────────────────────────────────────────────────────────────────
@router.get("/preauths")
def preauths(c: C = Depends(need("clinical_read"))):
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    out = []
    for p in c.q(m.PreAuth).order_by(m.PreAuth.created.desc()):
        d = p.as_dict()
        d["patient"] = names.get(p.mrn, "")
        if p.claim_id:
            d["lines"] = [f"{l.code}{(' #' + l.tooth) if l.tooth else ''}" for l in c.db.scalars(select(m.ClaimLine).where(m.ClaimLine.claim_id == p.claim_id))]
        d["days_waiting"] = (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - p.submitted_at).days if p.submitted_at and p.status in ("submitted", "pending") else None
        out.append(d)
    return out


@router.post("/preauths")
async def preauth_create(request: Request, c: C = Depends(need("front"))):
    """A predetermination for treatment plan items: built as an 837D with CLM19 = PB and sent through the clearinghouse."""
    b = await request.json()
    items = [c.get(m.TreatmentItem, i) for i in b.get("item_ids") or []]
    if not items:
        raise HTTPException(400, "Choose the planned procedures")
    plan = c.get(m.TreatmentPlan, items[0].plan_id)

    class P:   # treatment items stand in for procedures on the predetermination
        def __init__(s, it):
            s.code, s.tooth, s.surface, s.quad, s.fee, s.date, s.provider_id, s.location_id, s.id = it.code, it.tooth, it.surface, it.quad, it.fee, today(), plan.provider_id, plan.location_id, None
    claim_d = make_claim(c, plan.mrn, [P(i) for i in items], kind="predetermination")
    claim = c.get(m.Claim, claim_d["id"])
    pa = c.add(m.PreAuth(mrn=plan.mrn, plan_id=plan.id, claim_id=claim.id, payer=claim.payer, amount=claim.total, status="draft",
                         follow_up=(dt.date.today() + dt.timedelta(days=14)).isoformat()))
    c.db.flush()
    for i in items:
        i.preauth_id = pa.id
    c.db.commit()
    if b.get("send", True):
        return preauth_send(pa.id, c)
    return pa.as_dict()


@router.post("/preauths/{pid}/send")
def preauth_send(pid: int, c: C = Depends(need("front"))):
    pa = c.get(m.PreAuth, pid)
    claim = c.get(m.Claim, pa.claim_id)
    x, data = _x837(c, claim, predetermination=True)
    errs = x12.validate_837d(x)
    if errs:
        raise HTTPException(422, "Fix first: " + "; ".join(errs[:3]))
    res = vendors.vendor("clearinghouse").preauth(x, data)
    claim.x837 = x
    if not res.ok:
        c.db.commit()
        raise HTTPException(502, res.message)
    pa.status, pa.ref, pa.submitted_at = "submitted", res.ref, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    claim.status = "submitted"
    c.db.commit()
    return pa.as_dict()


@router.post("/preauths/{pid}/refresh")
def preauth_refresh(pid: int, c: C = Depends(need("front"))):
    pa = c.get(m.PreAuth, pid)
    if not pa.ref:
        raise HTTPException(400, "Not sent yet")
    res = vendors.vendor("clearinghouse").preauth_status(pa.ref)
    return _preauth_decide(c, pa, res.status, res.data.get("approved_amount"), res.data.get("expires", ""), res.message)


@router.patch("/preauths/{pid}")
async def preauth_edit(pid: int, request: Request, c: C = Depends(need("front"))):
    """Records a decision that came by fax, portal or phone."""
    b = await request.json()
    return _preauth_decide(c, c.get(m.PreAuth, pid), b.get("status"), b.get("approved_amount"), b.get("expires", ""), b.get("note", ""), b.get("ref"))


def _preauth_decide(c: C, pa, status, amount, expires, note, ref=None):
    if status not in ("pending", "submitted", "approved", "partial", "denied", "expired"):
        raise HTTPException(400, "Unknown status")
    pa.status, pa.note = status, (note or pa.note)[:300]
    if ref:
        pa.ref = ref
    if status in ("approved", "partial", "denied"):
        pa.decided_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
        pa.approved_amount = float(amount or 0)
        pa.expires = expires or pa.expires
        for it in c.db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.preauth_id == pa.id)):
            if status == "denied":
                it.est_insurance, it.est_patient, it.est_note = 0, it.fee, "Predetermination denied"
        if status == "denied":
            c.add(m.WorkItem(kind="preauth", mrn=pa.mrn, title=f"Predetermination denied ({pa.payer})", how="Talk with the patient: alternative treatment, self-pay, payment plan or an appeal.", action="patient", amount=pa.amount))
        if pa.claim_id:
            cl = c.db.get(m.Claim, pa.claim_id)
            cl.status = "paid" if status != "denied" else "denied"
            cl.est_insurance = pa.approved_amount
    c.db.commit()
    return pa.as_dict()


# ── ERA (835) auto-posting ──────────────────────────────────────────────────────────────────────────────────────
def post_835(c: C, raw: str) -> dict:
    remit = x12.parse_835(raw)
    nos = [x["claim_id"] for x in remit["claims"]]
    mine = {cl.claim_no: cl for cl in c.q(m.Claim, m.Claim.claim_no.in_(nos))}
    if c.db.scalar(select(m.Remittance.id).where(m.Remittance.org_key == c.org, m.Remittance.check_no == remit["check"], m.Remittance.check_no != "")):
        raise HTTPException(409, f"Check/EFT {remit['check']} was already posted")
    lines = {no: claim_lines(c, cl) for no, cl in mine.items()}
    res = rcm.post_remittance(remit, {no: {"lines": [{"code": l.code, "fee": l.fee} for l in ls], "posted_checks": mine[no].checks} for no, ls in lines.items()})
    era = c.add(m.Remittance(payer=remit["payer"], check_no=remit["check"], check_date=remit["date"], amount=remit["total"], raw_835=raw, posted_by=c.email))
    c.db.flush()
    touched = {}
    for p in res["postings"]:
        cl = mine[p["claim_id"]]
        line = lines[p["claim_id"]][p["line_index"]]
        line.paid, line.write_off, line.patient_resp, line.adjustments = p["paid"], p["write_off"], p["patient_resp"], p["adjustments"]
        line.status = "paid" if p["paid"] > 0 else "denied"
        proc = c.db.get(m.Procedure, line.procedure_id) if line.procedure_id else None
        prov = proc.provider_id if proc else cl.provider_id
        if p["paid"]:
            c.add(m.LedgerEntry(mrn=cl.mrn, date=remit["date"] or today(), kind="insurance_payment", amount=-p["paid"], claim_id=cl.id, procedure_id=line.procedure_id,
                                provider_id=prov, location_id=cl.location_id, ref=remit["check"], memo=f"{cl.payer} {line.code}"))
        if p["write_off"]:
            c.add(m.LedgerEntry(mrn=cl.mrn, date=remit["date"] or today(), kind="write_off", amount=-p["write_off"], claim_id=cl.id, procedure_id=line.procedure_id,
                                provider_id=prov, location_id=cl.location_id, ref=remit["check"], memo=f"Contractual adjustment {line.code} (CO-45)"))
        touched[cl.claim_no] = cl
    for d in res["denials"]:
        cl = mine[d["claim_id"]]
        c.add(m.WorkItem(kind="denial", mrn=cl.mrn, claim_id=cl.id, title=f"{cl.claim_no} {d['code']} denied: {d['reason']}", carc=str(d["carc"]), action=d["action"], how=d["how"],
                         amount=d["amount"], due=(dt.date.today() + dt.timedelta(days=7)).isoformat()))
        touched[cl.claim_no] = cl
    for e in res["exceptions"]:
        cl = mine.get(e["claim_id"])
        c.add(m.WorkItem(kind="exception", mrn=cl.mrn if cl else "", claim_id=cl.id if cl else None, title=f"ERA {remit['check']}: {e['reason']}", action="review",
                         how="Match the payment by hand, or call the payer.", amount=e["amount"]))
    for cl in touched.values():
        ls = lines[cl.claim_no]
        cl.paid = round(sum(l.paid for l in ls), 2)
        cl.write_off = round(sum(l.write_off for l in ls), 2)
        cl.patient_resp = round(sum(l.patient_resp for l in ls), 2)
        cl.checks = (cl.checks or []) + [remit["check"]]
        denied = [l for l in ls if l.status == "denied"]
        cl.status = "denied" if len(denied) == len(ls) else "partial" if denied else "paid"
        _hist_add(cl, f"ERA {remit['check']}: paid ${cl.paid:.2f}, patient ${cl.patient_resp:.2f}" + (f", {len(denied)} line(s) denied" if denied else ""))
    era.status = "exceptions" if res["exceptions"] else "posted"
    era.summary = {"claims": len(remit["claims"]), "posted": len(touched), "paid": res["paid"], "patient_resp": res["patient_resp"],
                   "denials": len(res["denials"]), "exceptions": [e["reason"] for e in res["exceptions"]]}
    c.db.commit()
    return dict(era.as_dict(skip=("raw_835",)))


@router.post("/era/fetch")
def era_fetch(c: C = Depends(need("billing"))):
    out = []
    raws = []
    ids = {(l.npi, l.tin) for l in c.q(m.Location).all() if l.npi or l.tin}    # each office's NPI and Tax ID: a shared account never mixes clinics
    for npi, tin in sorted(ids) or [("", "")]:
        try:
            raws += vendors.vendor("clearinghouse").fetch_remittances("", npi=npi, tin=tin)
        except vendors.VendorError as e:
            raise HTTPException(502, str(e))
    for raw in list(dict.fromkeys(raws)):
        try:
            out.append(post_835(c, raw))
        except HTTPException as e:
            out.append({"error": e.detail})
    return {"posted": out}


@router.post("/era/upload")
async def era_upload(request: Request, c: C = Depends(need("billing"))):
    b = await request.json()
    try:
        return post_835(c, b.get("x12", ""))
    except ValueError as e:
        raise HTTPException(400, str(e))


@router.get("/era")
def era_list(c: C = Depends(need("billing"))):
    return [x.as_dict(skip=("raw_835",)) for x in c.q(m.Remittance).order_by(m.Remittance.created.desc())]


# ── the work queue ──────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/work")
def work(status: str = "open,working", c: C = Depends(need("billing"))):
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    claims = {cl.id: cl for cl in c.q(m.Claim)}
    out = []
    for w in c.q(m.WorkItem, m.WorkItem.status.in_(status.split(","))).order_by(m.WorkItem.created.desc()):
        d = w.as_dict()
        cl = claims.get(w.claim_id)
        d.update({"patient": names.get(w.mrn, ""), "claim_no": cl.claim_no if cl else "", "payer": cl.payer if cl else "", "dos": cl.dos if cl else "",
                  "age": (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - w.created).days})
        out.append(d)
    # claims stuck with no answer after 30 days show up too, without a stored row
    for cl in claims.values():
        if cl.status == "submitted" and cl.kind == "claim" and cl.submitted_at and (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - cl.submitted_at).days >= 30:
            out.append({"id": None, "kind": "aging", "mrn": cl.mrn, "claim_id": cl.id, "claim_no": cl.claim_no, "patient": names.get(cl.mrn, ""), "payer": cl.payer, "dos": cl.dos,
                        "title": f"{cl.claim_no}: no payer response in {(dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - cl.submitted_at).days} days", "action": "review",
                        "how": "Check claim status (276/277) or call the payer.", "amount": cl.total, "status": "open", "age": (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - cl.submitted_at).days})
    return out


@router.patch("/work/{wid}")
async def work_edit(wid: int, request: Request, c: C = Depends(need("billing"))):
    w = c.get(m.WorkItem, wid)
    b = await request.json()
    for k in ("status", "assigned_to", "due"):
        if k in b:
            setattr(w, k, b[k])
    if b.get("note"):
        w.notes = (w.notes or []) + [[dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), c.email, b["note"][:500]]]
    c.db.commit()
    return w.as_dict()


@router.post("/work/{wid}/bill-patient")
def work_bill_patient(wid: int, c: C = Depends(need("billing"))):
    """Moves a denied line the plan won't pay (frequency, max, not covered) to the patient's balance."""
    w = c.get(m.WorkItem, wid)
    if not w.claim_id:
        raise HTTPException(400, "No claim on this item")
    cl = c.get(m.Claim, w.claim_id)
    amt = 0.0
    for l in claim_lines(c, cl):
        if l.status == "denied" and not l.patient_resp:
            l.patient_resp = l.fee
            amt += l.fee
    cl.patient_resp = round(sum(l.patient_resp for l in claim_lines(c, cl)), 2)
    w.status = "resolved"
    w.notes = (w.notes or []) + [[dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), c.email, f"Transferred ${amt:.2f} to patient responsibility"]]
    c.db.commit()
    return {"ok": True, "amount": amt}


@router.post("/work/{wid}/write-off")
async def work_write_off(wid: int, request: Request, c: C = Depends(need("billing"))):
    """A denial the office can't bill the patient for (contractual) and won't appeal: write it off."""
    w = c.get(m.WorkItem, wid)
    if not w.claim_id:
        raise HTTPException(400, "No claim on this item")
    cl = c.get(m.Claim, w.claim_id)
    b = await request.json()
    amt = 0.0
    for l in claim_lines(c, cl):
        if l.status == "denied" and not l.patient_resp and not l.write_off:
            l.write_off = l.fee
            amt += l.fee
    if amt:
        c.add(m.LedgerEntry(mrn=cl.mrn, date=today(), kind="write_off", amount=-amt, claim_id=cl.id, memo=f"Denial write-off {w.carc}: {(b.get('reason') or '')[:120]}"))
    cl.write_off = round(sum(l.write_off for l in claim_lines(c, cl)), 2)
    w.status = "resolved"
    w.notes = (w.notes or []) + [[dt.datetime.now(dt.timezone.utc).replace(tzinfo=None).isoformat(timespec="minutes"), c.email, f"Wrote off ${amt:.2f}"]]
    c.db.commit()
    return {"ok": True, "amount": amt}


@router.get("/ar")
def ar(c: C = Depends(need("billing"))):
    """Aging: insurance (unpaid claims by days since service) and patient (balances by days since last charge)."""
    buckets = ["0-30", "31-60", "61-90", "91-120", "120+"]

    def b_of(days):
        return buckets[0] if days <= 30 else buckets[1] if days <= 60 else buckets[2] if days <= 90 else buckets[3] if days <= 120 else buckets[4]
    ins = {k: 0.0 for k in buckets}
    for cl in c.q(m.Claim, m.Claim.kind == "claim", m.Claim.status.in_(("submitted", "accepted", "ready", "draft", "denied", "partial"))):
        out = cl.est_insurance if cl.status in ("submitted", "accepted", "ready", "draft") else 0
        if cl.status in ("denied", "partial"):
            out = round(sum(l.fee - l.paid - l.write_off - l.patient_resp for l in claim_lines(c, cl) if l.status == "denied"), 2)
        ins[b_of((dt.date.today() - dt.date.fromisoformat(cl.dos)).days if cl.dos else 0)] += out
    pt = {k: 0.0 for k in buckets}
    per = {}
    for e in c.q(m.LedgerEntry):
        x = per.setdefault(e.mrn, [0.0, ""])
        x[0] += e.amount
        if e.kind == "charge":
            x[1] = max(x[1], e.date)
    for mrn, (bal, last) in per.items():
        if bal > 0.005:
            pt[b_of((dt.date.today() - dt.date.fromisoformat(last)).days if last else 0)] += bal
    return {"insurance": {k: round(v, 2) for k, v in ins.items()}, "patient": {k: round(v, 2) for k, v in pt.items()}}


# ── patient billing ─────────────────────────────────────────────────────────────────────────────────────────────
def insurance_pending(c: C, mrn: str = None) -> dict:
    """{mrn: amount} still with insurance: the estimate on open claims, plus denied lines being worked (not the patient's
    to pay unless someone moves them to the patient)."""
    out = {}
    wh = [m.Claim.kind == "claim", m.Claim.status.in_(("draft", "ready", "submitted", "accepted", "denied", "partial", "rejected"))]
    if mrn:
        wh.append(m.Claim.mrn == mrn)
    for cl in c.q(m.Claim, *wh):
        if cl.status in ("denied", "partial"):
            amt = sum(l.fee for l in claim_lines(c, cl) if l.status == "denied" and not l.patient_resp)
        else:
            amt = cl.est_insurance
        out[cl.mrn] = out.get(cl.mrn, 0) + amt
    return out


def patient_portion_due(c: C, mrn: str) -> float:
    """What the patient owes now: the account balance minus what's still with insurance."""
    return round(max(0.0, balance(c, mrn) - insurance_pending(c, mrn).get(mrn, 0)), 2)


@router.post("/patients/{mrn}/payments")
async def take_payment(mrn: str, request: Request, c: C = Depends(need("front"))):
    """Cash, check, or card through the payment processor (a token from its card form; we never see card numbers)."""
    patient(c, mrn)
    b = await request.json()
    amt = round(float(b.get("amount") or 0), 2)
    if amt <= 0:
        raise HTTPException(400, "Enter an amount above $0")
    method = b.get("method") or "card"
    ref = b.get("ref", "")
    if method == "card":
        res = vendors.vendor("payments").charge(int(round(amt * 100)), b.get("token") or "tok_visa", f"Patient payment {mrn}")
        if not res.ok:
            raise HTTPException(402, res.message)
        ref = res.ref
    c.add(m.LedgerEntry(mrn=mrn, date=today(), kind="patient_payment", amount=-amt, ref=ref, memo=f"Patient payment ({method})", location_id=b.get("location_id")))
    c.db.commit()
    return {"ok": True, "ref": ref, "balance": balance(c, mrn)}


@router.post("/patients/{mrn}/adjustments")
async def adjust(mrn: str, request: Request, c: C = Depends(need("billing"))):
    patient(c, mrn)
    b = await request.json()
    amt = round(float(b.get("amount") or 0), 2)
    if not amt or not b.get("memo"):
        raise HTTPException(400, "Amount and reason are required")
    c.add(m.LedgerEntry(mrn=mrn, date=today(), kind="adjustment", amount=amt, memo=b["memo"][:200]))
    c.db.commit()
    return {"ok": True, "balance": balance(c, mrn)}


@router.post("/patients/{mrn}/text-to-pay")
async def text_to_pay(mrn: str, request: Request, c: C = Depends(need("front"))):
    p = patient_dict(patient(c, mrn))
    b = await request.json()
    amt = round(float(b.get("amount") or patient_portion_due(c, mrn)), 2)
    if amt <= 0:
        raise HTTPException(400, "Nothing is due")
    phone = b.get("phone") or p.get("phone", "")
    if p.get("sms_opt_out"):
        raise HTTPException(400, "This patient opted out of texts")
    req = c.add(m.PaymentRequest(mrn=mrn, amount=amt, token=secrets.token_urlsafe(24), sent_to=phone[-4:], plan_id=b.get("plan_id")))
    c.db.flush()
    loc = c.q(m.Location).first()
    first = (p.get("name", "").split(", ") + [""])[1].split(" ")[0] or "there"
    # PHI-light: no procedures or diagnoses in the text, just the amount and the link
    body = f"Hi {first}, your balance at {loc.name if loc else 'our office'} is ${amt:.2f}. Pay securely: {_base()}/dental/pay/{req.token} Reply STOP to opt out."
    res = vendors.vendor("sms").send(phone, body)
    req.status = "sent" if res.ok else "failed"
    c.add(m.Message(mrn=mrn, direction="out", phone=phone, body=body, kind="pay", status=res.status, ref=res.ref))
    c.db.commit()
    if not res.ok:
        raise HTTPException(400, res.message)
    return {"ok": True, "url": f"/dental/pay/{req.token}", "amount": amt}


def _base():
    import os
    return os.environ.get("APP_URL", "http://localhost:8000").rstrip("/")


def _pay_req(db, token):
    r = db.scalar(select(m.PaymentRequest).where(m.PaymentRequest.token == token)) if len(token) > 20 else None
    if not r or (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - r.created).days > 30:
        raise HTTPException(404, "This payment link has expired. Call the office for a new one.")
    return r


@public.get("/pay/{token}")
def pay_public(token: str, db: Session = Depends(auth.get_db)):
    r = _pay_req(db, token)
    loc = db.scalar(select(m.Location).where(m.Location.org_key == r.org_key))
    return {"amount": r.amount, "status": r.status, "practice": loc.name if loc else "", "phone": loc.phone if loc else ""}


@public.post("/pay/{token}")
async def pay_public_submit(token: str, request: Request, db: Session = Depends(auth.get_db)):
    r = _pay_req(db, token)
    if r.status == "paid":
        return {"ok": True, "already": True}
    b = await request.json()
    res = vendors.vendor("payments").charge(int(round(r.amount * 100)), b.get("token") or "", "Text-to-pay")
    if not res.ok:
        raise HTTPException(402, res.message)
    r.status, r.paid_at, r.ref = "paid", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None), res.ref
    db.add(m.LedgerEntry(org_key=r.org_key, mrn=r.mrn, date=today(), kind="patient_payment", amount=-r.amount, ref=res.ref, memo="Text-to-pay"))
    if r.plan_id:
        plan = db.get(m.PaymentPlan, r.plan_id)
        if plan:
            _mark_installment(plan, r.amount, res.ref)
    db.commit()
    audit.record(None, org_key=r.org_key, email="patient (pay link)", action="create", what="payment", record_id=r.mrn, method="POST", path="/api/public/dental/pay", ip=audit.client_ip(request))
    return {"ok": True, "ref": res.ref, "environment": res.environment}


def _mark_installment(plan, amount, ref):
    sched = [dict(s) for s in plan.schedule]
    for s in sched:
        if not s.get("paid_at"):
            s["paid_at"], s["ref"] = today(), ref
            break
    plan.schedule = sched
    if all(s.get("paid_at") for s in sched):
        plan.status = "completed"


@router.post("/patients/{mrn}/payment-plans")
async def plan_create(mrn: str, request: Request, c: C = Depends(need("front"))):
    patient(c, mrn)
    b = await request.json()
    total = round(float(b.get("total") or patient_portion_due(c, mrn)), 2)
    try:
        sched = rcm.payment_plan(total, float(b.get("down") or 0), int(b.get("months") or 6), b.get("start") or today(), b.get("day"))
    except ValueError as e:
        raise HTTPException(400, str(e))
    plan = c.add(m.PaymentPlan(mrn=mrn, total=total, down=float(b.get("down") or 0), months=int(b.get("months") or 6), schedule=sched, autopay=1 if b.get("autopay") else 0,
                               card_token=b.get("token", "") if b.get("autopay") else ""))
    c.db.commit()
    return plan.as_dict(skip=("card_token",))


@router.post("/payment-plans/{pid}/charge")
def plan_charge(pid: int, c: C = Depends(need("front"))):
    """Charges the next installment to the card on file (autopay)."""
    plan = c.get(m.PaymentPlan, pid)
    nxt = next((s for s in plan.schedule if not s.get("paid_at")), None)
    if not nxt:
        raise HTTPException(400, "This plan is paid off")
    if not plan.card_token:
        raise HTTPException(400, "No card on file; send a text-to-pay link instead")
    res = vendors.vendor("payments").charge(int(round(nxt["amount"] * 100)), plan.card_token, f"Payment plan {plan.id}")
    if not res.ok:
        raise HTTPException(402, res.message)
    c.add(m.LedgerEntry(mrn=plan.mrn, date=today(), kind="patient_payment", amount=-nxt["amount"], ref=res.ref, memo=f"Payment plan installment {nxt['n']}"))
    _mark_installment(plan, nxt["amount"], res.ref)
    c.db.commit()
    return plan.as_dict(skip=("card_token",))


@router.post("/patients/{mrn}/membership")
async def membership_join(mrn: str, request: Request, c: C = Depends(need("front"))):
    """In-house membership for uninsured patients: the fee goes on the ledger, included care is $0, the rest discounted."""
    patient(c, mrn)
    b = await request.json()
    plan = c.get(m.MembershipPlan, b["plan_id"])
    for old in c.q(m.Membership, m.Membership.mrn == mrn, m.Membership.status == "active"):
        old.status = "cancelled"
    billing = b.get("billing") or "monthly"
    start = dt.date.fromisoformat(b.get("start") or today())
    mem = c.add(m.Membership(mrn=mrn, plan_id=plan.id, start=start.isoformat(), renews=start.replace(year=start.year + 1).isoformat(), billing=billing, used={}))
    fee = plan.annual if billing == "annual" else plan.monthly
    c.add(m.LedgerEntry(mrn=mrn, date=start.isoformat(), kind="membership", amount=fee, memo=f"{plan.name} membership ({billing})"))
    c.db.commit()
    return mem.as_dict()


@router.get("/memberships")
def memberships(c: C = Depends(need("front"))):
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    plans = {p.id: p for p in c.q(m.MembershipPlan)}
    rows = []
    for x in c.q(m.Membership).order_by(m.Membership.renews):
        p = plans.get(x.plan_id)
        rows.append(dict(x.as_dict(), patient=names.get(x.mrn, ""), plan=p.name if p else "", monthly=p.monthly if p else 0))
    active = [r for r in rows if r["status"] == "active"]
    return {"members": rows, "plans": [p.as_dict() for p in plans.values()], "active": len(active),
            "mrr": round(sum(r["monthly"] for r in active if r["billing"] == "monthly") + sum((plans[r["plan_id"]].annual / 12) for r in active if r["billing"] == "annual" and r["plan_id"] in plans), 2)}


@router.get("/billing/summary")
def billing_summary(c: C = Depends(need("front"))):
    """For the Patient Billing screen: who owes what, open text-to-pay links, payment plans."""
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    per = {}
    for e in c.q(m.LedgerEntry):
        per[e.mrn] = per.get(e.mrn, 0) + e.amount
    pending = insurance_pending(c)
    owing = sorted(({"mrn": k, "patient": names.get(k, ""), "balance": round(v, 2), "pending_insurance": round(pending.get(k, 0), 2),
                     "due": round(max(0.0, v - pending.get(k, 0)), 2)} for k, v in per.items() if v > 0.005), key=lambda r: -r["due"])
    reqs = [dict(r.as_dict(skip=("token",)), patient=names.get(r.mrn, "")) for r in c.q(m.PaymentRequest).order_by(m.PaymentRequest.created.desc()).limit(50)]
    plans = [dict(p.as_dict(skip=("card_token",)), patient=names.get(p.mrn, ""), paid=round(sum(s["amount"] for s in p.schedule if s.get("paid_at")), 2),
                  next=next((s for s in p.schedule if not s.get("paid_at")), None)) for p in c.q(m.PaymentPlan)]
    return {"owing": owing, "requests": reqs, "plans": plans}
