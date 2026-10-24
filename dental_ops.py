"""Operations: lab cases, e-prescribing (EPCS for controlled substances), imaging devices, production and collections
dashboards with provider goals, and imports from Dentrix, Eaglesoft and Open Dental exports.

Multi-location (DSO): every report takes a location_id (0 = all locations) and breaks results out by location and by
provider, so a group can compare offices.
"""
import csv
import datetime as dt
import io
import re
from collections import defaultdict

from fastapi import Depends, HTTPException, Request
from sqlalchemy import select

import auth
import dental_db as m
import dental_rules
import dental_vendors as vendors
import dental_x12 as x12
from dental_api import router, need, C, patient, patient_dict, save_patient, providers, today


# ── lab cases ───────────────────────────────────────────────────────────────────────────────────────────────────
@router.get("/labs")
def labs(c: C = Depends(need("clinical_read"))):
    names = {r.mrn: r.name for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    appts = {a.id: a for a in c.q(m.Appointment, m.Appointment.start >= dt.datetime.now() - dt.timedelta(days=60))}
    out = []
    for x in c.q(m.LabCase).order_by(m.LabCase.due):
        d = dict(x.as_dict(), patient=names.get(x.mrn, ""))
        seat = appts.get(x.seat_appt_id)
        d["seat"] = seat.start.isoformat(timespec="minutes") if seat else ""
        d["late"] = bool(x.status == "sent" and x.due and x.due < today())
        d["risk"] = bool(seat and x.status in ("to_send", "sent") and x.due and x.due >= seat.start.date().isoformat())
        out.append(d)
    return out


@router.post("/labs")
async def lab_save(request: Request, c: C = Depends(need("clinical_write"))):
    b = await request.json()
    x = c.get(m.LabCase, b["id"]) if b.get("id") else c.add(m.LabCase(status="to_send"))
    if b.get("mrn"):
        patient(c, b["mrn"])
    for k in ("mrn", "lab", "item", "tooth", "shade", "status", "sent", "due", "seat_appt_id", "tracking", "cost"):
        if k in b:
            setattr(x, k, b[k])
    if b.get("status") == "sent" and not x.sent:
        x.sent = today()
    c.db.commit()
    return x.as_dict()


# ── e-prescribing ───────────────────────────────────────────────────────────────────────────────────────────────
FORMULARY = [
    {"drug": "Amoxicillin 500 mg capsule", "sig": "1 capsule by mouth three times a day for 7 days", "qty": "21", "schedule": "", "class": "penicillin"},
    {"drug": "Amoxicillin 2 g (premedication)", "sig": "4 x 500 mg capsules by mouth 1 hour before the appointment", "qty": "4", "schedule": "", "class": "penicillin"},
    {"drug": "Clindamycin 300 mg capsule", "sig": "1 capsule by mouth four times a day for 7 days", "qty": "28", "schedule": "", "class": "lincosamide"},
    {"drug": "Azithromycin 250 mg tablet", "sig": "2 tablets day 1, then 1 tablet daily for 4 days", "qty": "6", "schedule": "", "class": "macrolide"},
    {"drug": "Ibuprofen 600 mg tablet", "sig": "1 tablet by mouth every 6 hours as needed for pain, with food", "qty": "20", "schedule": "", "class": "nsaid"},
    {"drug": "Chlorhexidine 0.12% oral rinse", "sig": "Rinse with 15 mL for 30 seconds twice a day, do not swallow", "qty": "473 mL", "schedule": "", "class": ""},
    {"drug": "Acetaminophen-codeine 300/30 mg tablet", "sig": "1 tablet by mouth every 6 hours as needed for severe pain", "qty": "12", "schedule": "III", "class": "opioid"},
    {"drug": "Hydrocodone-acetaminophen 5/325 mg tablet", "sig": "1 tablet by mouth every 6 hours as needed for severe pain", "qty": "10", "schedule": "II", "class": "opioid"},
    {"drug": "Fluoride 1.1% toothpaste (prescription)", "sig": "Brush twice daily in place of regular toothpaste; spit, do not rinse", "qty": "1 tube", "schedule": "", "class": ""},
]
ALLERGY_CLASS = {"penicillin": r"penicillin|amoxicillin|pcn|augmentin", "nsaid": r"nsaid|ibuprofen|aspirin|naproxen", "opioid": r"codeine|hydrocodone|opioid|morphine",
                 "macrolide": r"azithromycin|erythromycin|macrolide", "lincosamide": r"clindamycin"}


@router.get("/rx/formulary")
def formulary(c: C = Depends(need("clinical_read"))):
    return FORMULARY


@router.post("/patients/{mrn}/rx")
async def prescribe(mrn: str, request: Request, c: C = Depends(need("clinical_write"))):
    """Checks allergies, then sends through the e-prescribing vendor. Controlled substances (EPCS) need the prescriber's
    DEA number and a fresh code from their two-step login app, as DEA rules require two-factor signing."""
    p = patient_dict(patient(c, mrn))
    b = await request.json()
    drug = (b.get("drug") or "").strip()
    f = next((x for x in FORMULARY if x["drug"] == drug), {})
    cls = f.get("class", "")
    if cls and re.search(ALLERGY_CLASS.get(cls, "^$"), p.get("allergies", "") or "", re.I) and not b.get("override_allergy"):
        raise HTTPException(409, f"Allergy alert: the chart lists \"{p.get('allergies')}\". Choose another drug, or override with a reason.")
    controlled = bool(b.get("schedule") or f.get("schedule"))
    prov = c.db.get(m.Provider, int(b["prescriber_id"])) if b.get("prescriber_id") else None
    if not prov or prov.kind != "dentist":
        raise HTTPException(400, "Choose the prescribing dentist")
    two_factor_ok = False
    if controlled:
        if not prov.dea:
            raise HTTPException(400, f"{prov.name} has no DEA number on file; controlled substances can't be sent")
        import security
        me = c.db.get(auth.User, c.user.id)
        if not me.mfa_enabled:
            raise HTTPException(403, "EPCS needs two-step login. Set it up in Settings > Security first.")
        step = security.check_code(security._open(me.mfa_secret), b.get("code", ""), me.mfa_last_step or 0)
        if not step:
            raise HTTPException(403, "That two-step code didn't match. EPCS signing needs a fresh code.")
        me.mfa_last_step = step
        two_factor_ok = True
    if (b.get("schedule") or f.get("schedule")) == "II" and int(b.get("refills") or 0):
        raise HTTPException(400, "Schedule II prescriptions can't have refills")
    rx = c.add(m.Prescription(mrn=mrn, drug=drug, sig=b.get("sig") or f.get("sig", ""), qty=b.get("qty") or f.get("qty", ""), refills=int(b.get("refills") or 0),
                              controlled=1 if controlled else 0, schedule=b.get("schedule") or f.get("schedule", ""), pharmacy=b.get("pharmacy", ""), prescriber_id=prov.id))
    res = vendors.vendor("erx").send({"drug": rx.drug, "sig": rx.sig, "controlled": controlled, "two_factor_ok": two_factor_ok})
    rx.status, rx.ref = res.status, res.ref
    c.db.commit()
    if not res.ok:
        raise HTTPException(400, res.message)
    return dict(rx.as_dict(), message=res.message)


# ── imaging devices and integrations ────────────────────────────────────────────────────────────────────────────
@router.get("/devices")
def devices(c: C = Depends(need("clinical_read"))):
    v = vendors.vendor("imaging")
    return getattr(v, "DEVICES", [])


@router.get("/integrations")
def integrations(c: C = Depends(need("read"))):
    return vendors.status()


# ── dashboards ──────────────────────────────────────────────────────────────────────────────────────────────────
def _range(frm, to):
    t = dt.date.fromisoformat(to) if to else dt.date.today()
    f = dt.date.fromisoformat(frm) if frm else t.replace(day=1)
    return f.isoformat(), t.isoformat()


@router.get("/reports/production")
def production(frm: str = "", to: str = "", location_id: int = 0, c: C = Depends(need("billing"))):
    """Gross and net production, collections, and the numbers a dental office manager watches: collection rate, case
    acceptance, new patients, hygiene reappointment, clean-claim rate and days to pay, by provider and location."""
    f, t = _range(frm, to)
    provs = providers(c)
    locs = {l.id: l.name for l in c.q(m.Location)}
    loc_ok = (lambda x: True) if not location_id else (lambda x: x == location_id)
    procs = [p for p in c.q(m.Procedure, m.Procedure.date >= f, m.Procedure.date <= t) if loc_ok(p.location_id) and p.source not in ("outside", "import")]
    ledger = [e for e in c.q(m.LedgerEntry, m.LedgerEntry.date >= f, m.LedgerEntry.date <= t) if loc_ok(e.location_id) or not location_id]
    by_prov = defaultdict(lambda: {"gross": 0.0, "adjust": 0.0, "collections": 0.0, "procedures": 0, "patients": set()})
    by_day = defaultdict(lambda: {"production": 0.0, "collections": 0.0})
    by_cat = defaultdict(float)
    by_loc = defaultdict(lambda: {"production": 0.0, "collections": 0.0})
    for p in procs:
        r = by_prov[p.provider_id]
        r["gross"] += p.fee
        r["procedures"] += 1
        r["patients"].add(p.mrn)
        by_day[p.date]["production"] += p.fee
        by_cat[x12.benefit_category(p.code)] += p.fee
        by_loc[p.location_id]["production"] += p.fee
    for e in ledger:
        if e.kind in ("insurance_payment", "patient_payment"):
            by_prov[e.provider_id]["collections"] += -e.amount
            by_day[e.date]["collections"] += -e.amount
            by_loc[e.location_id]["collections"] += -e.amount
        elif e.kind in ("write_off", "adjustment"):
            by_prov[e.provider_id]["adjust"] += e.amount
    month = t[:7]
    goals = {g.provider_id: g for g in c.q(m.ProviderGoal, m.ProviderGoal.month == month)}
    days_in = (dt.date.fromisoformat(t) - dt.date.fromisoformat(f)).days + 1
    rows = []
    for pid, r in by_prov.items():
        if pid is None and not r["gross"] and not r["collections"]:
            continue
        pv, g = provs.get(pid), goals.get(pid)
        net = r["gross"] + r["adjust"]
        rows.append({"provider_id": pid, "provider": pv.name if pv else "Patient payments, not split by provider", "kind": pv.kind if pv else "", "color": pv.color if pv else "#8a94a3",
                     "gross": round(r["gross"], 2), "net": round(net, 2), "collections": round(r["collections"], 2), "procedures": r["procedures"], "patients": len(r["patients"]),
                     "collection_rate": round(100 * r["collections"] / net) if net > 0 else None,
                     "goal_production": g.production if g else None, "goal_collections": g.collections if g else None,
                     "goal_pct": round(100 * r["gross"] / g.production) if g and g.production else None})
    rows.sort(key=lambda r: -r["gross"])
    # case acceptance: plan items presented in the range
    items = c.db.scalars(select(m.TreatmentItem).join(m.TreatmentPlan, m.TreatmentPlan.id == m.TreatmentItem.plan_id).where(
        m.TreatmentPlan.org_key == c.org, m.TreatmentPlan.presented_at.isnot(None), m.TreatmentPlan.presented_at >= dt.datetime.fromisoformat(f))).all()
    presented = sum(i.fee for i in items)
    accepted = sum(i.fee for i in items if i.status in ("accepted", "scheduled", "completed"))
    # new patients: first procedure in range
    first = {}
    for p in c.q(m.Procedure):
        first[p.mrn] = min(first.get(p.mrn, p.date), p.date)
    new_pts = sum(1 for d in first.values() if f <= d <= t)
    # hygiene reappointment: hygiene visits done in range whose patient has a future hygiene appointment
    hyg_ids = {pid for pid, pv in provs.items() if pv.kind == "hygienist"}
    hyg_pts = {p.mrn for p in procs if p.code in ("D1110", "D1120", "D4910")}
    future = {a.mrn for a in c.q(m.Appointment, m.Appointment.start > dt.datetime.now(), m.Appointment.status.in_(("scheduled", "confirmed"))) if a.provider_id in hyg_ids}
    claims = c.q(m.Claim, m.Claim.kind == "claim", m.Claim.dos >= f, m.Claim.dos <= t).all()
    sent = [x for x in claims if x.status not in ("draft", "ready")]
    clean = [x for x in sent if x.status in ("paid", "submitted", "accepted") and not any(h[1].startswith("Corrected") for h in (x.history or []))]
    paid = [x for x in sent if x.status in ("paid", "partial") and x.submitted_at]
    era_dates = {}
    for x in paid:
        last = next((h[0] for h in reversed(x.history or []) if h[1].startswith("ERA")), None)
        if last:
            era_dates[x.id] = (dt.datetime.fromisoformat(last) - x.submitted_at).days
    gross = sum(r["gross"] for r in rows)
    coll = sum(r["collections"] for r in rows)
    net = sum(r["net"] for r in rows)
    return {"from": f, "to": t, "providers": rows,
            "totals": {"gross": round(gross, 2), "net": round(net, 2), "collections": round(coll, 2), "collection_rate": round(100 * coll / net) if net > 0 else None,
                       "per_day": round(gross / max(1, len([d for d in by_day if by_day[d]["production"]])), 2), "new_patients": new_pts,
                       "case_acceptance": round(100 * accepted / presented) if presented else None, "presented": round(presented, 2), "accepted": round(accepted, 2),
                       "hygiene_reappointment": round(100 * len(hyg_pts & future) / len(hyg_pts)) if hyg_pts else None,
                       "clean_claim_rate": round(100 * len(clean) / len(sent)) if sent else None, "days_to_pay": round(sum(era_dates.values()) / len(era_dates), 1) if era_dates else None,
                       "denial_rate": round(100 * sum(1 for x in sent if x.status in ("denied", "partial")) / len(sent)) if sent else None,
                       "goal_production": round(sum(r["goal_production"] or 0 for r in rows), 2), "days": days_in},
            "by_day": [{"date": d, **{k: round(v, 2) for k, v in by_day[d].items()}} for d in sorted(by_day)],
            "by_category": {k: round(v, 2) for k, v in sorted(by_cat.items(), key=lambda kv: -kv[1])},
            "by_location": [{"location_id": k, "location": locs.get(k, "Not tied to an office"), **{kk: round(vv, 2) for kk, vv in v.items()}} for k, v in by_loc.items()]}


@router.post("/goals")
async def goals_save(request: Request, c: C = Depends(need("admin"))):
    b = await request.json()
    month = b.get("month") or today()[:7]
    g = c.db.scalar(select(m.ProviderGoal).where(m.ProviderGoal.org_key == c.org, m.ProviderGoal.provider_id == int(b["provider_id"]), m.ProviderGoal.month == month))
    g = g or c.add(m.ProviderGoal(provider_id=int(b["provider_id"]), month=month))
    g.production, g.collections = float(b.get("production") or 0), float(b.get("collections") or 0)
    c.db.commit()
    return g.as_dict()


# ── imports ─────────────────────────────────────────────────────────────────────────────────────────────────────
# Header names each system's standard exports use (case and spacing ignored). Unknown columns are reported, not guessed.
MAPS = {
    "patients": {
        "id": ["patnum", "chart number", "chartnumber", "patient id", "patientid", "chart #", "account"],
        "last": ["lname", "last name", "lastname", "patient last name"], "first": ["fname", "first name", "firstname", "patient first name"],
        "dob": ["birthdate", "birth date", "dob", "date of birth"], "sex": ["gender", "sex"],
        "phone": ["wirelessphone", "cell phone", "mobile", "mobile phone", "hmphone", "home phone", "phone"], "email": ["email", "e-mail"],
        "addr": ["address", "address 1", "street"], "city": ["city"], "state": ["state", "st"], "zip": ["zip", "zip code", "postal code"],
        "payer": ["carrier", "carriername", "insurance", "primary insurance", "prim ins carrier", "insurance company"],
        "member": ["subscriberid", "subscriber id", "member id", "insurance id", "prim ins id"],
    },
    "procedures": {
        "id": ["patnum", "chart number", "chartnumber", "patient id", "patientid", "chart #"],
        "date": ["procdate", "procedure date", "date", "service date", "date of service"],
        "code": ["proccode", "ada code", "adacode", "code", "cdt", "procedure code"],
        "tooth": ["toothnum", "tooth", "tooth #", "tth"], "surface": ["surf", "surface", "surfaces", "surfs"],
        "fee": ["procfee", "amount", "fee", "charge"], "status": ["procstatus", "status"],
    },
}
SOURCES = {"opendental": "OD", "dentrix": "DX", "eaglesoft": "ES", "csv": "IM"}


def _norm(h):
    return re.sub(r"\s+", " ", (h or "").strip().lower().replace("_", " "))


def _date(s):
    s = (s or "").strip()
    for fmt in ("%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y", "%Y%m%d", "%m-%d-%Y"):
        try:
            return dt.datetime.strptime(s[:10], fmt).date().isoformat()
        except ValueError:
            continue
    return ""


def parse_import(source: str, kind: str, text: str) -> dict:
    """Maps an export onto Althais fields. Returns {mapping, rows, errors, unmapped}. Pure: nothing is saved."""
    if source not in SOURCES or kind not in MAPS:
        raise ValueError("Unknown source or kind")
    rdr = csv.DictReader(io.StringIO(text.lstrip("﻿")), dialect=csv.Sniffer().sniff(text[:2000], delimiters=",\t;|") if text.strip() else "excel")
    heads = rdr.fieldnames or []
    mapping = {}
    for field, names in MAPS[kind].items():
        h = next((h for h in heads if _norm(h) in names), None)
        if h:
            mapping[field] = h
    unmapped = [h for h in heads if h not in mapping.values()]
    rows, errors = [], []
    for i, r in enumerate(rdr, 2):
        g = {k: (r.get(h) or "").strip() for k, h in mapping.items()}
        if kind == "patients":
            if not g.get("id") or not (g.get("last") or g.get("first")):
                errors.append(f"Row {i}: missing patient ID or name")
                continue
            sex = g.get("sex", "").upper()
            sex = {"0": "M", "1": "F", "MALE": "M", "FEMALE": "F"}.get(sex, sex[:1])
            rows.append({"src_id": g["id"], "name": f"{g.get('last', '').title()}, {g.get('first', '').title()}".strip(", "), "dob": _date(g.get("dob")), "sex": sex,
                         "phone": re.sub(r"\D", "", g.get("phone", "")), "email": g.get("email", ""),
                         "addr": ", ".join(x for x in (g.get("addr"), g.get("city"), (g.get("state", "") + " " + g.get("zip", "")).strip()) if x),
                         "payer": g.get("payer", ""), "member": g.get("member", "")})
            if not rows[-1]["dob"]:
                errors.append(f"Row {i}: date of birth '{g.get('dob', '')}' not understood")
        else:
            code = g.get("code", "").upper()
            if code and re.fullmatch(r"\d{4}", code):
                code = "D" + code
            status = g.get("status", "")
            if status and status not in ("2", "C", "Complete", "Completed", "complete", "completed"):
                continue   # only completed work counts as history (Open Dental ProcStatus 2 = complete)
            if not dental_rules.CDT.match(code):
                errors.append(f"Row {i}: '{g.get('code', '')}' isn't a CDT code")
                continue
            d = _date(g.get("date"))
            if not d or not g.get("id"):
                errors.append(f"Row {i}: missing patient ID or date")
                continue
            try:
                fee = float(re.sub(r"[^\d.\-]", "", g.get("fee", "") or "0") or 0)
            except ValueError:
                fee = 0.0
            rows.append({"src_id": g["id"], "date": d, "code": code, "tooth": dental_rules.norm_tooth(g.get("tooth")), "surface": dental_rules.norm_surface(g.get("surface")), "fee": fee})
    return {"mapping": mapping, "rows": rows, "errors": errors[:200], "unmapped": unmapped, "headers": heads}


@router.post("/import/preview")
async def import_preview(request: Request, c: C = Depends(need("admin"))):
    b = await request.json()
    try:
        res = parse_import(b.get("source", "csv"), b.get("kind", "patients"), b.get("text", ""))
    except (ValueError, csv.Error) as e:
        raise HTTPException(400, f"Couldn't read that file: {e}")
    return dict(res, rows=res["rows"][:50], count=len(res["rows"]))


@router.post("/import/commit")
async def import_commit(request: Request, c: C = Depends(need("admin"))):
    b = await request.json()
    source, kind = b.get("source", "csv"), b.get("kind", "patients")
    res = parse_import(source, kind, b.get("text", ""))
    summary = commit_rows(c, source, kind, res["rows"])
    job = c.add(m.ImportJob(source=source, kind=kind, status="imported", rows=len(res["rows"]), by=c.email, summary=dict(summary, errors=len(res["errors"]))))
    c.db.commit()
    return job.as_dict()


def commit_rows(c: C, source: str, kind: str, rows: list) -> dict:
    """Saves imported patients (with their plan) or procedure history. Shared by export uploads and the live Open
    Dental pull, so both give a patient the same MRN (prefix + the other system's ID). Doesn't commit."""
    prefix = SOURCES[source]
    added = updated = skipped = 0
    existing = {r.mrn: r for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org))}
    if kind == "patients":
        for r in rows:
            mrn = f"{prefix}{r['src_id']}"
            data = {k: r.get(k, "") for k in ("name", "dob", "sex", "phone", "email", "addr", "payer", "member")}
            data.update({"mrn": mrn, "imported_from": source, "status": "Active"})
            if mrn not in existing:
                data["created"] = today()
            save_patient(c, data, existing.get(mrn))
            if r.get("payer"):
                cov = c.q(m.Coverage, m.Coverage.mrn == mrn, m.Coverage.rank == "primary").first()
                if not cov:
                    c.add(m.Coverage(mrn=mrn, payer=r["payer"], payer_id=r.get("payer_id", ""), member_id=r.get("member", ""), group=r.get("group", "")))
                else:      # fill in what's missing; a plan the office already verified here isn't overwritten
                    for k, v in (("payer_id", r.get("payer_id")), ("member_id", r.get("member")), ("group", r.get("group"))):
                        if v and not getattr(cov, k):
                            setattr(cov, k, v)
            updated += 1 if mrn in existing else 0
            added += 0 if mrn in existing else 1
    else:
        seen = {(p.mrn, p.date, p.code, p.tooth, p.surface) for p in c.q(m.Procedure, m.Procedure.source == "import")}
        for r in rows:
            mrn = f"{prefix}{r['src_id']}"
            if mrn not in existing:
                skipped += 1
                continue
            tooth, surf = dental_rules.norm_tooth(r.get("tooth")), dental_rules.norm_surface(r.get("surface"))
            key = (mrn, r["date"], r["code"], tooth, surf)
            if key in seen:
                skipped += 1
                continue
            seen.add(key)
            c.add(m.Procedure(mrn=mrn, date=r["date"], code=r["code"], tooth=tooth, surface=surf, quad=r.get("quad", "") or "", fee=r.get("fee") or 0, source="import"))
            added += 1
    return {"added": added, "updated": updated, "skipped": skipped}


# ── Open Dental, live (opendental.py) ───────────────────────────────────────────────────────────────────────────
@router.get("/import/opendental")
def opendental_status(c: C = Depends(need("admin"))):
    import opendental as od
    return od.connection(c.db, c.org)


@router.post("/import/opendental/connect")
async def opendental_connect(request: Request, c: C = Depends(need("admin"))):
    import opendental as od
    b = await request.json()
    try:
        od.connect(c.db, c.org, b.get("customer_key"), b.get("clinic_num"))
    except od.OpenDentalError as e:
        raise HTTPException(400, str(e))
    return od.connection(c.db, c.org)


@router.post("/import/opendental/disconnect")
def opendental_disconnect(c: C = Depends(need("admin"))):
    import opendental as od
    od.disconnect(c.db, c.org)
    return od.connection(c.db, c.org)


@router.post("/import/opendental/pull")
async def opendental_pull(request: Request, c: C = Depends(need("admin"))):
    """Patients, their dental plans and completed procedures from Open Dental, straight into the chart and history."""
    import opendental as od
    from starlette.concurrency import run_in_threadpool
    b = await request.json()
    try:
        days = max(1, min(od.MAX_DAYS, int(b.get("days") or 30)))
        key, clinic = od.stored_key(c.db, c.org)
        pts, procs, notes = await run_in_threadpool(od.pull, key, clinic, days)
    except od.OpenDentalError as e:
        raise HTTPException(400, str(e))
    except ValueError:
        raise HTTPException(400, "Days must be a number")
    sp = commit_rows(c, "opendental", "patients", pts)
    c.db.flush()
    sr = commit_rows(c, "opendental", "procedures", procs)
    summary = {"patients": sp, "procedures": sr, "notes": notes, "days": days}
    c.add(m.ImportJob(source="opendental", kind="live pull", status="imported", rows=len(pts) + len(procs), by=c.email,
                      summary={"added": sp["added"] + sr["added"], "updated": sp["updated"], "skipped": sr["skipped"], "errors": 0}))
    od.record_pull(c.db, c.org, {"patients": len(pts), "procedures": sr["added"], "notes": notes})
    c.db.commit()
    return summary


@router.get("/import/jobs")
def import_jobs(c: C = Depends(need("admin"))):
    return [j.as_dict() for j in c.q(m.ImportJob).order_by(m.ImportJob.created.desc())]


# ── the morning huddle ──────────────────────────────────────────────────────────────────────────────────────────
@router.get("/huddle")
def huddle(date: str = "", c: C = Depends(need("read"))):
    """What a dental team goes over before the first patient: today's production against goal, who's coming and what
    they still need (unscheduled treatment, balances, insurance, medical alerts), and what's waiting in billing."""
    import calendar
    from dental_api import schedule
    day = date or today()
    sch = schedule(date=day, c=c)
    appts = [a for a in sch["appointments"] if a["status"] not in ("cancelled",)]
    mrns = {a["mrn"] for a in appts if a["mrn"]}
    fees = {f.code: f.fee for f in c.q(m.FeeSchedule, m.FeeSchedule.payer == "")}
    sched_prod = sum(fees.get(p.get("code"), 0) for a in appts for p in (a.get("procedures") or []))
    plans = {p.id: p for p in c.q(m.TreatmentPlan, m.TreatmentPlan.mrn.in_(list(mrns) or [""]))}
    unsched = defaultdict(float)
    if plans:
        for it in c.db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.plan_id.in_(list(plans)), m.TreatmentItem.status.in_(("proposed", "accepted")))):
            unsched[plans[it.plan_id].mrn] += it.fee
    month = day[:7]
    goals = c.q(m.ProviderGoal, m.ProviderGoal.month == month).all()
    y, mo = int(month[:4]), int(month[5:])
    workdays = sum(1 for d in range(1, calendar.monthrange(y, mo)[1] + 1) if dt.date(y, mo, d).weekday() < 5)
    goal_day = round(sum(g.production for g in goals) / max(1, workdays), 2)
    mtd = sum(p.fee for p in c.q(m.Procedure, m.Procedure.date >= month + "-01", m.Procedure.date <= day) if p.source not in ("outside", "import"))
    produced_today = sum(p.fee for p in c.q(m.Procedure, m.Procedure.date == day))
    for a in appts:
        a["unscheduled"] = round(unsched.get(a["mrn"], 0), 2)
    claims = c.q(m.Claim, m.Claim.kind == "claim").all()
    labs_due = [x for x in c.q(m.LabCase, m.LabCase.status.in_(("to_send", "sent"))) if x.due and x.due <= (dt.date.fromisoformat(day) + dt.timedelta(days=3)).isoformat()]
    return {"date": day, "appointments": appts, "location": sch["location"], "scheduled_production": round(sched_prod, 2), "produced_today": round(produced_today, 2),
            "goal_day": goal_day, "goal_month": round(sum(g.production for g in goals), 2), "mtd": round(mtd, 2),
            "unconfirmed": sum(1 for a in appts if a["status"] == "scheduled" and a["mrn"]), "online_requests": sum(1 for a in appts if not a["mrn"]),
            "unverified": sum(1 for a in appts if a["mrn"] and a["flags"].get("payer") and not a["flags"].get("elig_active")),
            "claims_ready": sum(1 for x in claims if x.status == "ready"), "claims_attention": sum(1 for x in claims if x.status in ("draft", "rejected")),
            "claims_waiting": sum(1 for x in claims if x.status == "submitted"), "denied_dollars": round(sum(x.total - x.paid for x in claims if x.status in ("denied", "partial")), 2),
            "work_open": c.db.query(m.WorkItem).filter(m.WorkItem.org_key == c.org, m.WorkItem.status.in_(("open", "working"))).count(),
            "preauth_pending": c.db.query(m.PreAuth).filter(m.PreAuth.org_key == c.org, m.PreAuth.status.in_(("submitted", "pending"))).count(),
            "unread": c.db.query(m.Message).filter(m.Message.org_key == c.org, m.Message.direction == "in", m.Message.read == 0).count(),
            "labs_due": [dict(x.as_dict(), patient="") for x in labs_due]}
