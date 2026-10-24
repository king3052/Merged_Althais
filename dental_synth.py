"""A synthetic dental practice for development, demos and tests. No real patients, ever.

load(c) builds a two-location practice for the signed-in clinic: chairs, dentists and hygienists with hours, a fee
schedule, membership plans, ~48 patients with two years of history, perio charts, treatment plans, a full schedule
this week and next, and a revenue cycle that went through the real pipeline (eligibility 270/271, claims built as
837D and sent to the mock clearinghouse, ERAs posted from 835s), with denials in the work queue.

Everything is marked: rows have sample=1, patients have "sample": true and SYN- member IDs, phone numbers are in the
555-01xx range reserved for fiction, NPIs are made-up numbers that pass the check digit, and images are labeled
SYNTHETIC. remove(c) deletes exactly those rows.
"""
import datetime as dt
import random

from fastapi import HTTPException
from sqlalchemy import event, select, delete
from sqlalchemy.orm import Session

import auth
import dental_clinical as clin
import dental_db as m
import dental_rcm as rcm
import dental_x12 as x12

# (code, our short label, office fee, minutes)
FEES = [
    ("D0120", "Periodic exam", 65, 15), ("D0140", "Limited exam, problem focused", 85, 20), ("D0150", "Comprehensive exam, new patient", 110, 30),
    ("D0180", "Comprehensive periodontal exam", 120, 30), ("D0210", "Full mouth X-rays", 165, 20), ("D0220", "Periapical X-ray, first", 35, 5),
    ("D0230", "Periapical X-ray, each additional", 28, 5), ("D0272", "Bitewings, two", 55, 10), ("D0274", "Bitewings, four", 75, 10),
    ("D0330", "Panoramic X-ray", 130, 10), ("D0431", "Oral cancer screening adjunct", 60, 10), ("D1110", "Adult cleaning", 120, 45),
    ("D1120", "Child cleaning", 85, 30), ("D1206", "Fluoride varnish", 45, 5), ("D1208", "Fluoride", 40, 5), ("D1351", "Sealant, per tooth", 60, 10),
    ("D1516", "Space maintainer, bilateral", 420, 30), ("D2140", "Amalgam, one surface", 140, 30), ("D2150", "Amalgam, two surfaces", 175, 40),
    ("D2160", "Amalgam, three surfaces", 210, 45), ("D2330", "Composite, front, one surface", 160, 30), ("D2331", "Composite, front, two surfaces", 195, 40),
    ("D2332", "Composite, front, three surfaces", 235, 45), ("D2391", "Composite, back, one surface", 175, 30), ("D2392", "Composite, back, two surfaces", 225, 40),
    ("D2393", "Composite, back, three surfaces", 275, 50), ("D2394", "Composite, back, four+ surfaces", 320, 60), ("D2740", "Ceramic crown", 1250, 90),
    ("D2750", "Porcelain-fused-to-metal crown", 1150, 90), ("D2790", "Full gold crown", 1300, 90), ("D2950", "Core buildup", 285, 20),
    ("D2954", "Post and core", 395, 30), ("D2962", "Porcelain veneer", 1350, 90), ("D3220", "Pulpotomy", 220, 45), ("D3310", "Root canal, front tooth", 850, 75),
    ("D3320", "Root canal, premolar", 990, 90), ("D3330", "Root canal, molar", 1250, 120), ("D4341", "Scaling and root planing, 4+ teeth per quad", 275, 60),
    ("D4342", "Scaling and root planing, 1-3 teeth per quad", 185, 40), ("D4346", "Scaling with gingival inflammation", 155, 60),
    ("D4355", "Full mouth debridement", 190, 60), ("D4381", "Local antimicrobial, per tooth", 85, 10), ("D4910", "Periodontal maintenance", 165, 60),
    ("D5110", "Complete denture, upper", 1850, 60), ("D5120", "Complete denture, lower", 1850, 60), ("D5213", "Partial denture, upper, metal", 1950, 60),
    ("D6010", "Implant body, surgical", 2350, 90), ("D6057", "Implant abutment, custom", 750, 30), ("D6058", "Implant crown, ceramic, on abutment", 1450, 60),
    ("D6065", "Implant crown, ceramic, screw retained", 1550, 60), ("D6240", "Bridge pontic, porcelain", 1150, 60), ("D6750", "Bridge retainer crown", 1150, 60),
    ("D7140", "Extraction, erupted tooth", 195, 30), ("D7210", "Surgical extraction", 325, 45), ("D7220", "Impacted tooth, soft tissue", 375, 45),
    ("D7230", "Impacted tooth, partial bony", 450, 60), ("D7240", "Impacted tooth, full bony", 525, 60), ("D7953", "Bone graft at extraction site", 450, 20),
    ("D8090", "Comprehensive ortho, adult", 5800, 60), ("D9110", "Palliative emergency treatment", 125, 30), ("D9230", "Nitrous oxide", 75, 0),
    ("D9944", "Occlusal guard, hard, full arch", 525, 30), ("D9972", "External bleaching, per arch", 325, 30),
]
FEE = {f[0]: f[2] for f in FEES}

FIRST_F = ["Ava", "Sofia", "Mia", "Emma", "Isabella", "Camila", "Harper", "Aaliyah", "Nora", "Grace", "Elena", "Priya", "Zoe", "Leah", "Maria", "Hannah",
           "Chloe", "Layla", "Rosa", "Imani", "Keiko", "Fatima", "Ruth", "Beatriz"]
FIRST_M = ["Liam", "Noah", "Mateo", "Elijah", "James", "Lucas", "Ethan", "Jamal", "Diego", "Omar", "Henry", "Wei", "Samuel", "Andre", "Tomas", "Caleb",
           "Isaac", "Marcus", "Raj", "Felix", "Hector", "Kofi", "Arjun", "Walter"]
LAST = ["Alvarez", "Nguyen", "Patel", "Johnson", "Kim", "Okonkwo", "Garcia", "Brooks", "Haddad", "Castillo", "Sato", "Morales", "Bennett", "Rahman", "Ortiz",
        "Fischer", "Delgado", "Mensah", "Romero", "Kowalski", "Tran", "Greene", "Silva", "Abbott", "Reddy", "Navarro", "Lindqvist", "Chen", "Ibarra", "Foster"]
PAYERS = [("Delta Dental PPO", "SYNDD1"), ("MetLife Dental", "SYNMET"), ("Cigna Dental", "SYNCIG"), ("Aetna Dental", "SYNAET"), ("Guardian Dental", "SYNGRD"),
          ("UnitedHealthcare Dental", "SYNUHC"), ("Humana Dental", "SYNHUM")]
STREETS = ["Oak Hollow Dr", "Barton Springs Rd", "Cedar Bend Ln", "Mesa Verde Ct", "Pecan Grove Way", "Riverside Blvd", "Lantana Trl", "Bluebonnet Ave"]


def npi(base9: str) -> str:
    """A made-up NPI with a valid check digit (Luhn over 80840 + the first nine digits)."""
    digits = [int(d) for d in "80840" + base9]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            d = d - 9 if d > 9 else d
        total += d
    return base9 + str((10 - total % 10) % 10)


@event.listens_for(Session, "before_flush")
def _mark_sample(session, ctx, instances):
    if session.info.get("dental_sample"):
        for o in session.new:
            if isinstance(o, m._Row):
                o.sample = 1


STARTER = 2   # `sample` value for the starter setup a new real clinic gets (dental_api.ready)


def starter(c) -> dict:
    """A real clinic's first setup, to edit in Practice setup: no patients, nothing invented about the practice beyond
    common defaults."""
    name = (getattr(c.user, "organization", "") or "").strip() or "My dental office"
    loc = c.add(m.Location(name=name, sample=STARTER))
    c.db.flush()
    ops = [c.add(m.Operatory(location_id=loc.id, name=n, kind=k, sort=i, sample=STARTER)) for i, (n, k) in enumerate([("Op 1", "restorative"), ("Op 2", "restorative"), ("Hyg 1", "hygiene")])]
    who = (getattr(c.user, "provider_name", "") or getattr(c.user, "full_name", "") or c.email.split("@")[0]).strip()
    parts = who.replace("Dr.", "").replace("Dr ", "").split()
    prov = c.add(m.Provider(name=who, first=parts[0] if parts else "", last=parts[-1] if len(parts) > 1 else "",
                            cred="DDS", kind="dentist", color="#1d5bb8", user_email=c.email, sample=STARTER))
    c.db.flush()
    for wd in range(5):
        c.add(m.ProviderHours(provider_id=prov.id, location_id=loc.id, operatory_id=ops[0].id, weekday=wd, start="08:00", end="17:00", sample=STARTER))
    if not c.db.scalar(select(m.FeeSchedule.id).where(m.FeeSchedule.org_key == c.org)):
        for code, desc, fee, mins in FEES:
            c.add(m.FeeSchedule(code=code, desc=desc, fee=fee, minutes=mins, sample=STARTER))
    c.db.commit()
    return {"ok": True, "location": loc.id}


def _drop_starter(c) -> None:
    """The synthetic practice replaces an untouched starter setup (nothing booked, no patients)."""
    if c.db.scalar(select(m.Appointment.id).where(m.Appointment.org_key == c.org)) or c.db.scalar(select(m.Procedure.id).where(m.Procedure.org_key == c.org)):
        return
    for t in (m.ProviderHours, m.Operatory, m.Provider, m.Location):
        c.db.execute(delete(t).where(t.org_key == c.org, t.sample == STARTER))
    c.db.commit()


def remove(c) -> dict:
    n = 0
    for t in m.TABLES:
        n += c.db.execute(delete(t).where(t.org_key == c.org, t.sample == 1)).rowcount or 0
    for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org)).all():
        if '"sample": true' in (r.data or ""):
            c.db.delete(r)
            n += 1
    c.db.commit()
    return {"removed": n}


def load(c) -> dict:
    import dental_api as api
    import dental_api_rcm as rcmapi
    import dental_engage as eng
    import dental_vendors as vendors
    if c.db.scalar(select(m.Location.id).where(m.Location.org_key == c.org, m.Location.sample == 1)):
        raise HTTPException(409, "The sample practice is already loaded. Remove it first to reload with today's dates.")
    _drop_starter(c)
    rnd = random.Random(20261023)
    today = dt.date.today()
    c.db.info["dental_sample"] = True
    vendors.MockClearinghouse._remits = []
    try:
        # ── practice ──
        loc1 = c.add(m.Location(name="Bright Smiles Dental, South Lamar", addr="2100 S Lamar Blvd Ste 200", city="Austin", state="TX", zip="78704",
                                phone="(512) 555-0100", npi=npi("190000001"), tin="74-0000001", region="Central Texas"))
        loc2 = c.add(m.Location(name="Bright Smiles Dental, Round Rock", addr="401 E Main St", city="Round Rock", state="TX", zip="78664",
                                phone="(512) 555-0150", npi=npi("190000002"), tin="74-0000001", region="Central Texas"))
        c.db.flush()
        ops1 = [c.add(m.Operatory(location_id=loc1.id, name=n, kind=k, sort=i)) for i, (n, k) in enumerate([("Op 1", "restorative"), ("Op 2", "restorative"), ("Hyg 1", "hygiene"), ("Hyg 2", "hygiene"), ("Consult", "consult")])]
        ops2 = [c.add(m.Operatory(location_id=loc2.id, name=n, kind=k, sort=i)) for i, (n, k) in enumerate([("Op 1", "restorative"), ("Hyg 1", "hygiene")])]
        provs = [c.add(m.Provider(name="Dr. Maya Okafor", first="Maya", last="Okafor", cred="DDS", kind="dentist", npi=npi("190000101"), color="#1d5bb8", dea="FO0000001", user_email=c.email)),
                 c.add(m.Provider(name="Dr. Daniel Reyes", first="Daniel", last="Reyes", cred="DMD", kind="dentist", npi=npi("190000102"), color="#7b3fb8", dea="FR0000002")),
                 c.add(m.Provider(name="Priya Shah, RDH", first="Priya", last="Shah", cred="RDH", kind="hygienist", npi=npi("190000103"), color="#0f8a7e")),
                 c.add(m.Provider(name="Jordan Lee, RDH", first="Jordan", last="Lee", cred="RDH", kind="hygienist", npi=npi("190000104"), color="#3a8a1f"))]
        c.db.flush()
        okafor, reyes, shah, lee = provs
        for wd in range(5):
            if wd < 4:
                c.add(m.ProviderHours(provider_id=okafor.id, location_id=loc1.id, operatory_id=ops1[0].id, weekday=wd, start="07:30", end="16:30"))
                c.add(m.ProviderHours(provider_id=shah.id, location_id=loc1.id, operatory_id=ops1[2].id, weekday=wd, start="07:00", end="16:00"))
            else:   # Friday half day
                c.add(m.ProviderHours(provider_id=okafor.id, location_id=loc1.id, operatory_id=ops1[0].id, weekday=wd, start="07:30", end="13:30", lunch_start="", lunch_end=""))
                c.add(m.ProviderHours(provider_id=shah.id, location_id=loc1.id, operatory_id=ops1[2].id, weekday=wd, start="07:00", end="13:00", lunch_start="", lunch_end=""))
            if wd in (0, 2, 4):
                c.add(m.ProviderHours(provider_id=reyes.id, location_id=loc1.id, operatory_id=ops1[1].id, weekday=wd, start="08:00", end="17:00"))
            else:
                c.add(m.ProviderHours(provider_id=reyes.id, location_id=loc2.id, operatory_id=ops2[0].id, weekday=wd, start="08:00", end="17:00"))
            if wd < 4:
                c.add(m.ProviderHours(provider_id=lee.id, location_id=loc1.id, operatory_id=ops1[3].id, weekday=wd, start="08:00", end="17:00"))
            else:
                c.add(m.ProviderHours(provider_id=lee.id, location_id=loc2.id, operatory_id=ops2[1].id, weekday=wd, start="08:00", end="15:00"))
        if not c.db.scalar(select(m.FeeSchedule.id).where(m.FeeSchedule.org_key == c.org)):
            for code, desc, fee, mins in FEES:
                c.add(m.FeeSchedule(code=code, desc=desc, fee=fee, minutes=mins))
        mp_adult = c.add(m.MembershipPlan(name="Adult Smile Plan", monthly=29, annual=319, discount_pct=20, included={"D0120": 2, "D1110": 2, "D0274": 1, "D0140": 1, "D0330": 1}))
        mp_child = c.add(m.MembershipPlan(name="Kids Smile Plan", monthly=22, annual=239, discount_pct=15, included={"D0120": 2, "D1120": 2, "D1206": 2, "D0272": 1, "D1351": 4}))
        mp_perio = c.add(m.MembershipPlan(name="Gum Health Plan", monthly=39, annual=429, discount_pct=20, included={"D0120": 2, "D4910": 4, "D0274": 1}))
        eng.ensure_forms(c.db, c.org)
        c.db.flush()
        for g_p, prod in ((okafor, 72000), (reyes, 64000), (shah, 26000), (lee, 24000)):
            c.add(m.ProviderGoal(provider_id=g_p.id, month=today.strftime("%Y-%m"), production=prod, collections=round(prod * .97)))

        # ── patients ──
        patients = []
        used_names = set()
        for i in range(48):
            female = rnd.random() < .54
            first = rnd.choice(FIRST_F if female else FIRST_M)
            last = rnd.choice(LAST)
            while (first, last) in used_names:
                last = rnd.choice(LAST)
            used_names.add((first, last))
            age = rnd.choice([7, 9, 12, 15] if i % 9 == 0 else [24, 29, 33, 38, 41, 45, 52, 57, 61, 66, 71, 78])
            dob = dt.date(today.year - age, rnd.randint(1, 12), rnd.randint(1, 28))
            insured = rnd.random() > .16
            payer, pid = rnd.choice(PAYERS) if insured else ("Self-pay", "")
            mrn = f"S{20001 + i}"
            perio = age >= 40 and rnd.random() < .3
            allergies = rnd.choice(["NKDA"] * 7 + ["Penicillin (hives)", "Codeine (nausea)", "Latex", "Sulfa (rash)"])
            problems = "; ".join(rnd.sample(["High blood pressure", "Type 2 diabetes", "Asthma", "Hypothyroidism", "Acid reflux", "Joint replacement (knee, 2021)",
                                             "On blood thinner (apixaban)", "Osteoporosis (alendronate)"], k=rnd.choice([0, 0, 0, 1, 1, 2])))
            meds = "\n".join(rnd.sample(["Lisinopril 10 mg daily", "Metformin 500 mg twice daily", "Levothyroxine 50 mcg", "Atorvastatin 20 mg", "Omeprazole 20 mg"], k=rnd.choice([0, 0, 1, 2])))
            data = {"mrn": mrn, "name": f"{last}, {first}", "dob": dob.isoformat(), "sex": "F" if female else "M", "phone": f"(512) 555-01{i:02d}",
                    "email": f"{first.lower()}.{last.lower()}@example.test", "addr": f"{rnd.randint(100, 9800)} {rnd.choice(STREETS)}, Austin, TX 787{rnd.randint(1, 49):02d}",
                    "payer": payer, "member": f"SYN{rnd.randint(10000000, 99999999)}" if insured else "", "allergies": allergies, "problems": problems, "meds": meds,
                    "status": "Active", "created": (today - dt.timedelta(days=rnd.randint(200, 1500))).isoformat(), "balance": 0, "sample": True,
                    "preferred_location": loc2.id if i % 5 == 0 else loc1.id}
            if "blood thinner" in problems:
                data["medical_alerts"] = ["Blood thinner"]
            if "Joint replacement" in problems:
                data["medical_alerts"] = ["Cardiac/joint: check premedication"]
            if "alendronate" in problems:
                data["medical_alerts"] = ["Bisphosphonates: extraction risk"]
            api.save_patient(c, data)
            if insured:
                c.add(m.Coverage(mrn=mrn, payer=payer, payer_id=pid, member_id=data["member"]))
            patients.append({"mrn": mrn, "age": age, "insured": insured, "perio": perio, "loc": data["preferred_location"], "i": i, "first": first})
        c.db.flush()

        def weekday_before(d, days):
            x = d - dt.timedelta(days=days)
            while x.weekday() > 4:
                x -= dt.timedelta(days=1)
            return x

        def chart(mrn, tooth, cond, surface="", status="existing", code="", date=""):
            c.add(m.ToothCondition(mrn=mrn, tooth=str(tooth), surface=surface, condition=cond, status=status, code=code, date=date or today.isoformat()))

        procs_by = {}

        def done(p, date, code, tooth="", surface="", quad="", prov=None, source="chart"):
            pr = c.add(m.Procedure(mrn=p["mrn"], date=date.isoformat(), code=code, tooth=str(tooth), surface=surface, quad=quad, fee=FEE.get(code, 100),
                                   provider_id=(prov or okafor).id, location_id=loc1.id if p["loc"] == loc1.id else loc2.id, source=source))
            c.add(m.LedgerEntry(mrn=p["mrn"], date=date.isoformat(), kind="charge", amount=pr.fee, provider_id=pr.provider_id, location_id=pr.location_id,
                                memo=code + (f" #{tooth}" if tooth else "")))
            procs_by.setdefault((p["mrn"], date.isoformat()), []).append(pr)
            return pr

        # ── history and chart ──
        recent_claim_visits = []
        for p in patients:
            mrn, kid = p["mrn"], p["age"] < 14
            hyg = shah if p["i"] % 2 else lee
            # existing dentistry
            for _ in range(rnd.choice([0, 1, 2, 3, 4]) if not kid else 0):
                t = rnd.choice([2, 3, 14, 15, 18, 19, 30, 31, 4, 5, 12, 13, 20, 21, 28, 29])
                kind = rnd.choice(["composite", "composite", "amalgam", "crown"])
                chart(mrn, t, kind, "" if kind == "crown" else rnd.choice(["O", "MO", "DO", "MOD"]), date=(today - dt.timedelta(days=rnd.randint(800, 3000))).isoformat())
            if p["age"] > 45 and rnd.random() < .35:
                chart(mrn, rnd.choice([19, 30, 3, 14]), "missing")
            if p["age"] > 20 and rnd.random() < .25:
                for t in (1, 16, 17, 32):
                    chart(mrn, t, "missing")
            if rnd.random() < .12:
                t = rnd.choice([3, 14, 19, 30])
                chart(mrn, t, "rct")
                chart(mrn, t, "crown")
            # hygiene history: the last two years
            interval = 90 if p["perio"] else 182
            overdue = p["i"] % 6 == 3
            last = today - dt.timedelta(days=(rnd.randint(250, 420) if overdue else rnd.randint(20, interval - 10)))
            visits = []
            d = last
            while d > today - dt.timedelta(days=730):
                visits.append(weekday_before(d, 0))
                d -= dt.timedelta(days=interval + rnd.randint(-10, 15))
            p["last_hyg"] = max(visits) if visits else None
            for k, v in enumerate(sorted(visits)):
                if p["perio"] and k == 0:
                    done(p, v, "D0180")
                    for q in ("UR", "UL", "LL", "LR"):
                        done(p, v + dt.timedelta(days=7 if q in ("UR", "LR") else 14), "D4341", quad=q, prov=hyg)
                    continue
                if p["perio"]:
                    done(p, v, "D4910", prov=hyg)
                else:
                    done(p, v, "D1120" if kid else "D1110", prov=hyg)
                done(p, v, "D0120")
                if k % 2 == 1 or v == visits[0]:
                    done(p, v, "D0272" if kid else "D0274", prov=hyg)
                if kid:
                    done(p, v, "D1206", prov=hyg)
                if (today - v).days < 75 and p["insured"]:
                    recent_claim_visits.append((p, v))
            # perio charts
            if p["perio"] or rnd.random() < .5:
                r = clin.empty_readings()
                missing = {x.tooth for x in c.db.new if isinstance(x, m.ToothCondition) and x.mrn == mrn and x.condition == "missing"}
                for t in range(1, 33):
                    v = r[str(t)]
                    if str(t) in missing:
                        v["missing"] = True
                        continue
                    deep = p["perio"] and (t in range(2, 6) or t in range(18, 22) or rnd.random() < .2)
                    v["pd"] = [rnd.choice([4, 5, 5, 6] if deep and k in (0, 2, 3, 5) else [2, 3, 3]) for k in range(6)]
                    v["bop"] = [1 if (deep and rnd.random() < .6) or rnd.random() < .06 else 0 for _ in range(6)]
                    v["rec"] = [rnd.choice([0, 0, 1, 2]) if p["age"] > 50 else 0 for _ in range(6)]
                    if deep and t in (3, 14, 19, 30) and rnd.random() < .4:
                        v["furc"] = 1
                    if deep and rnd.random() < .1:
                        v["mob"] = 1
                ex = c.add(m.PerioExam(mrn=mrn, date=visits[-1].isoformat() if visits else today.isoformat(), provider_id=hyg.id, readings=r, summary=clin.perio_summary(r)))
                if p["perio"]:
                    chart(mrn, "", "bone_loss")
        c.db.flush()

        # ── open treatment and plans ──
        plan_specs = [
            ("Crown and buildup", [(1, "D2950", "30", "", ""), (1, "D2740", "30", "", "")], "accepted"),
            ("Fillings", [(1, "D2392", "14", "MO", ""), (1, "D2391", "3", "O", ""), (2, "D2393", "19", "MOD", "")], "presented"),
            ("Periodontal therapy", [(1, "D4341", "", "", "UR"), (1, "D4341", "", "", "LR"), (2, "D4341", "", "", "UL"), (2, "D4342", "", "", "LL"), (3, "D4910", "", "", "")], "presented"),
            ("Implant #19", [(1, "D7210", "19", "", ""), (1, "D7953", "19", "", ""), (2, "D6010", "19", "", ""), (3, "D6057", "19", "", ""), (3, "D6058", "19", "", "")], "presented"),
            ("Root canal and crown", [(1, "D3330", "3", "", ""), (2, "D2950", "3", "", ""), (2, "D2740", "3", "", "")], "accepted"),
            ("Replace failing fillings", [(1, "D2394", "30", "MODB", ""), (1, "D2392", "31", "DO", "")], "draft"),
            ("Night guard", [(1, "D9944", "", "", "")], "presented"),
            ("Wisdom teeth", [(1, "D7240", "17", "", ""), (1, "D7230", "32", "", ""), (1, "D9230", "", "", "")], "declined"),
            ("Veneers #7-#10", [(1, "D2962", "7", "", ""), (1, "D2962", "8", "", ""), (1, "D2962", "9", "", ""), (1, "D2962", "10", "", "")], "presented"),
            ("Crown #14", [(1, "D2740", "14", "", "")], "draft"),
        ]
        adults = [p for p in patients if p["age"] >= 18]
        plans = []
        for k, (name, items, status) in enumerate(plan_specs):
            p = adults[(k * 3 + 1) % len(adults)]
            if "Perio" in name:
                p = next((x for x in adults if x["perio"]), p)
            plan = c.add(m.TreatmentPlan(mrn=p["mrn"], name=name, provider_id=okafor.id if k % 2 == 0 else reyes.id, location_id=p["loc"], status="draft"))
            c.db.flush()
            for ph, code, tooth, surf, quad in items:
                api._add_item(c, plan, {"phase": ph, "code": code, "tooth": tooth, "surface": surf, "quad": quad, "fee": FEE.get(code), "priority": "urgent" if code.startswith("D3") else "needed"})
                if code.startswith("D23") and tooth:
                    chart(p["mrn"], tooth, "caries", surf)
            if "Crown" in name and "14" not in name:
                chart(p["mrn"], items[-1][2], "fracture")
            if "Implant" in name:
                chart(p["mrn"], "19", "fracture")
                chart(p["mrn"], "19", "periapical")
            if "Root canal" in name:
                chart(p["mrn"], "3", "periapical")
            c.db.flush()
            if status != "draft":
                plan.status, plan.presented_at, plan.token = "presented", dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=rnd.randint(2, 30)), f"sample{rnd.getrandbits(96):024x}"
            if status in ("accepted", "declined"):
                for it in c.db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.plan_id == plan.id)):
                    it.status = "accepted" if status == "accepted" else "declined"
                plan.status, plan.decided_at, plan.signature = status, dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=1), "Patient signature (synthetic)"
            plans.append((plan, p))
        for t, why in ((3, "watch"), (12, "watch"), (20, "watch")):
            chart(adults[5]["mrn"], t, why, "O")
        c.db.flush()

        # ── coverage: benefits for most insured patients ──
        for p in patients:
            if p["insured"] and p["i"] % 7 != 4:
                try:
                    rcmapi.check_eligibility(c, p["mrn"])
                    cov = api.coverage(c, p["mrn"])
                    cov.checked_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=rnd.randint(0, 20))
                except HTTPException:
                    pass
        cov_term = api.coverage(c, patients[10]["mrn"]) if patients[10]["insured"] else None
        if cov_term:
            cov_term.member_id = "TERM" + cov_term.member_id[4:]
            rcmapi.check_eligibility(c, patients[10]["mrn"])
        for plan, p in plans:
            api.plan_estimate(c, plan)
        c.db.commit()

        # ── claims through the real pipeline ──
        made = []
        for p, v in sorted(recent_claim_visits, key=lambda x: x[1]):
            prs = [x for x in procs_by.get((p["mrn"], v.isoformat()), []) if not x.claim_id]
            if prs:
                made.append((rcmapi.make_claim(c, p["mrn"], prs), v))
        # restorative claims that need attachments and narratives
        special = []
        for k, (code, tooth, surf, quad, attach) in enumerate([("D2740", "30", "", "", False), ("D2950", "30", "", "", False), ("D4341", "", "", "UR", True), ("D2392", "", "MO", "", False),
                                                               ("D3330", "14", "", "", True), ("D7210", "1", "", "", True), ("D2393", "18", "MOD", "", True)]):
            p = [x for x in adults if x["insured"]][k + 2]
            dday = weekday_before(today, 6 + k * 4)
            pr = done(p, dday, code, tooth, surf, quad, prov=reyes if k % 2 else okafor)
            if code == "D2740":
                pr2 = done(p, dday, "D2950", "30", prov=okafor)
                cd = rcmapi.make_claim(c, p["mrn"], [pr, pr2])
            else:
                cd = rcmapi.make_claim(c, p["mrn"], [pr])
            cl = c.get(m.Claim, cd["id"])
            if attach:
                img = c.add(m.Image(mrn=p["mrn"], kind="pa", teeth=[tooth or "3"], taken=dday.isoformat(), device="sensor-1", synthetic=1,
                                    data=clin.synthetic_radiograph("pa", [tooth or "3", str(int(tooth or 3) + 1)], [], mrn_seed(p["mrn"]))))
                c.db.flush()
                cl.attachments = [{"kind": "radiograph", "image_id": img.id, "label": f"pa {dday}", "ref": ""}]
                if quad:
                    ex = c.q(m.PerioExam, m.PerioExam.mrn == p["mrn"]).first()
                    if ex:
                        cl.attachments = cl.attachments + [{"kind": "perio_chart", "perio_id": ex.id, "label": f"Perio chart {ex.date}", "ref": ""}]
            c.db.commit()
            rcmapi.run_check(c, cl)
            special.append(cl)
        # send everything older than 4 days; keep the newest as ready / draft for the Claims screen
        for cd, v in made:
            cl = c.get(m.Claim, cd["id"])
            if (today - v).days > 4 and cl.status == "ready":
                rcmapi.claim_submit(cl.id, c)
                cl.submitted_at = dt.datetime.combine(v + dt.timedelta(days=1), dt.time(18))
        for cl in special[:5]:
            if cl.status == "ready":
                rcmapi.claim_submit(cl.id, c)
        c.db.commit()
        # payers answer: post ERAs for claims sent more than 12 days ago; newer ones are still waiting
        pending = list(vendors.MockClearinghouse._remits)
        vendors.MockClearinghouse._remits = []
        for raw in pending:
            r = x12.parse_835(raw)
            cl = c.db.scalar(select(m.Claim).where(m.Claim.org_key == c.org, m.Claim.claim_no == r["claims"][0]["claim_id"]))
            if cl and cl.submitted_at and (dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - cl.submitted_at).days > 12:
                paid_on = min(today, cl.submitted_at.date() + dt.timedelta(days=rnd.randint(9, 21)))
                raw = raw.replace(f"*{today:%Y%m%d}~", f"*{paid_on:%Y%m%d}~", 2)   # the payer paid a couple of weeks after the claim went out
                try:
                    rcmapi.post_835(c, raw)
                except HTTPException:
                    pass
        # specific denials the queue should show
        deny = [(special[0], "252", "CO"), (special[2], "50", "CO")]
        for cl, carc, grp in deny:
            if cl.status == "submitted":
                lines = rcmapi.claim_lines(c, cl)
                p = api.patient_dict(api.patient(c, cl.mrn))
                raw = x12.build_835({"payer": cl.payer, "check": f"EFT{rnd.randint(100000, 999999)}", "date": today.isoformat(), "claims": [{
                    "id": cl.claim_no, "payer_claim_id": cl.payer_claim_id, "patient": {"last": p["name"].split(", ")[0], "first": p["name"].split(", ")[1], "member_id": cl.member_id},
                    "lines": [{"code": l.code, "fee": l.fee, "paid": 0, "allowed": l.fee, "adj": [[grp, carc, l.fee]]} for l in lines]}]})
                rcmapi.post_835(c, raw)
        # a claim with a missing quadrant, held before it goes out
        p = [x for x in adults if x["insured"]][1]
        pr = done(p, weekday_before(today, 1), "D4342", quad="", prov=shah)
        rcmapi.make_claim(c, p["mrn"], [pr])
        c.db.commit()

        # ── pre-authorizations ──
        for k, (plan, p) in enumerate([x for x in plans if x[0].name in ("Crown and buildup", "Implant #19", "Periodontal therapy")]):
            if not api.coverage(c, p["mrn"]) or not api.coverage(c, p["mrn"]).payer:
                continue
            items = c.db.scalars(select(m.TreatmentItem).where(m.TreatmentItem.plan_id == plan.id)).all()
            pa = _preauth(c, rcmapi, plan, [i for i in items if i.phase == 1 or plan.name.startswith("Implant")])
            pa.submitted_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=[9, 16, 4][k % 3])
            if k == 0:
                rcmapi._preauth_decide(c, pa, "approved", round(pa.amount * .5, 2), (today + dt.timedelta(days=170)).isoformat(), "Approved at 50% for major services")
            elif k == 1:
                pa.status = "pending"
        c.db.commit()

        # ── schedule: this week and next ──
        week0 = today - dt.timedelta(days=today.weekday())
        reasons_h = ["Recall: cleaning, exam", "Recall: perio maintenance", "Cleaning, exam, bitewings", "Child cleaning, fluoride"]
        reasons_r = [("Crown prep #30", 90, "D2740"), ("Fillings #14 MO, #3 O", 60, "D2392"), ("Root canal #3", 90, "D3330"), ("Emergency: toothache", 30, "D0140"),
                     ("Crown seat #19", 30, ""), ("SRP UR, LR", 90, "D4341"), ("New patient exam", 60, "D0150"), ("Night guard impressions", 30, "D9944"),
                     ("Extraction #1", 45, "D7210"), ("Consult: implant #19", 30, "D0140")]
        appts_made = 0
        for d in range(10):
            day = week0 + dt.timedelta(days=d + (2 if d >= 5 else 0))
            if day.weekday() > 4:
                continue
            booked_today = set()
            for hrow in c.q(m.ProviderHours, m.ProviderHours.weekday == day.weekday()).all():
                pv = next(x for x in provs if x.id == hrow.provider_id)
                t = dt.datetime.combine(day, dt.time(*map(int, hrow.start.split(":"))))
                end = dt.datetime.combine(day, dt.time(*map(int, hrow.end.split(":"))))
                while t < end - dt.timedelta(minutes=30):
                    if hrow.lunch_start and dt.time(12) <= t.time() < dt.time(13):
                        t = dt.datetime.combine(day, dt.time(13))
                        continue
                    if rnd.random() < (.18 if day >= today else .1):
                        t += dt.timedelta(minutes=30)
                        continue
                    pool = [x for x in patients if x["mrn"] not in booked_today]
                    if pv.kind == "hygienist":   # hygiene goes to patients due for it (the plan would pay)
                        pool = [x for x in pool if not x.get("last_hyg") or (day - x["last_hyg"]).days > (80 if x["perio"] else 165)] or pool
                    p = rnd.choice(pool or patients)
                    booked_today.add(p["mrn"])
                    if pv.kind == "hygienist":
                        mins, reason = 60, ("Child cleaning, fluoride" if p["age"] < 14 else "Recall: perio maintenance" if p["perio"] else rnd.choice(reasons_h[:3]))
                        prc = [{"code": "D4910" if p["perio"] else "D1120" if p["age"] < 14 else "D1110"}, {"code": "D0120"}]
                    else:
                        reason, mins, code = rnd.choice(reasons_r)
                        prc = [{"code": code}] if code else []
                    if t + dt.timedelta(minutes=mins) > end:
                        break
                    if day < today:
                        st = "complete" if rnd.random() > .06 else "no_show"
                    elif day == today:
                        now = dt.datetime.now()
                        st = "complete" if t + dt.timedelta(minutes=mins) < now else "seated" if t <= now else rnd.choice(["confirmed", "confirmed", "scheduled", "arrived"]) if t < now + dt.timedelta(hours=1) else rnd.choice(["confirmed", "scheduled"])
                    else:
                        st = rnd.choice(["confirmed", "scheduled", "scheduled"]) if (day - today).days <= 2 else "scheduled"
                    c.add(m.Appointment(location_id=hrow.location_id, mrn=p["mrn"], provider_id=pv.id, operatory_id=hrow.operatory_id, start=t, minutes=mins,
                                        reason=reason, procedures=prc, status=st, source=rnd.choice(["staff"] * 6 + ["online", "recall"]),
                                        confirmed_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) if st == "confirmed" else None, reminders=["72h"] if day > today else []))
                    appts_made += 1
                    t += dt.timedelta(minutes=mins)
        c.db.flush()
        by_mrn = {p["mrn"]: p for p in patients}
        for a in c.q(m.Appointment, m.Appointment.status == "complete").all():
            p = by_mrn.get(a.mrn)
            prs = []
            for x in a.procedures or []:
                if not x.get("code"):
                    continue
                tooth = {"D2740": "30", "D2392": "14", "D3330": "3", "D7210": "1", "D9944": ""}.get(x["code"], "")
                surf = "MO" if x["code"] == "D2392" else ""
                quad = "UR" if x["code"] == "D4341" else ""
                prs.append(done(p, a.start.date(), x["code"], tooth, surf, quad, prov=next(v for v in provs if v.id == a.provider_id)))
            for pr in prs:
                pr.location_id = a.location_id
            if prs and p["insured"]:
                cd = rcmapi.make_claim(c, p["mrn"], prs)
                if a.start.date() < today and cd["status"] == "ready":   # earlier days' claims went out that evening; today's wait for the biller
                    rcmapi.claim_submit(cd["id"], c)
                    c.get(m.Claim, cd["id"]).submitted_at = dt.datetime.combine(a.start.date(), dt.time(18))
        c.db.commit()
        # goals a little above this month's pace, so the dashboard has something to chase
        mtd = {}
        for pr in c.q(m.Procedure, m.Procedure.date >= today.replace(day=1).isoformat()).all():
            mtd[pr.provider_id] = mtd.get(pr.provider_id, 0) + pr.fee
        import calendar
        days_month = calendar.monthrange(today.year, today.month)[1]
        for g in c.q(m.ProviderGoal).all():
            pace = mtd.get(g.provider_id, 0) / max(1, today.day) * days_month
            g.production = round(max(2000, pace * 1.08), -2)
            g.collections = round(g.production * .97, -2)
        # an online request from someone new, for tomorrow
        tmr = today + dt.timedelta(days=1)
        while tmr.weekday() > 4:
            tmr += dt.timedelta(days=1)
        c.add(m.Appointment(location_id=loc1.id, mrn="", provider_id=okafor.id, operatory_id=ops1[4].id, start=dt.datetime.combine(tmr, dt.time(16)), minutes=30,
                            reason="Toothache or emergency", status="scheduled", source="online",
                            request={"name": "Quinn, Avery", "dob": "1990-05-14", "phone": "5125550199", "email": "avery.quinn@example.test", "insurance": "Cigna Dental", "member": "SYN55501234",
                                     "note": "Upper right molar hurts when I bite"}))
        c.db.commit()

        # ── patient billing ──
        owing = [(r.mrn, r) for r in c.db.scalars(select(auth.OrgPatient).where(auth.OrgPatient.org_key == c.org)) if '"sample": true' in (r.data or "")]
        for k, (mrn, r) in enumerate(owing[:30]):
            bal = api.balance(c, mrn)
            due = rcmapi.patient_portion_due(c, mrn)
            if due > 0 and k % 3 == 0:
                c.add(m.LedgerEntry(mrn=mrn, date=(today - dt.timedelta(days=rnd.randint(1, 40))).isoformat(), kind="patient_payment", amount=-round(due, 2),
                                    ref="ch_mock_sample", memo="Patient payment (card)"))
        c.db.flush()
        selfpay = [p for p in patients if not p["insured"]]
        for k, p in enumerate(selfpay[:5]):
            plan_ = mp_child if p["age"] < 14 else mp_perio if p["perio"] else mp_adult
            start = today - dt.timedelta(days=rnd.randint(20, 300))
            c.add(m.Membership(mrn=p["mrn"], plan_id=plan_.id, start=start.isoformat(), renews=start.replace(year=start.year + 1).isoformat(), billing="monthly" if k % 2 else "annual",
                               used={"D0120": 1, "D1110": 1}))
            c.add(m.LedgerEntry(mrn=p["mrn"], date=start.isoformat(), kind="membership", amount=plan_.annual if k % 2 == 0 else plan_.monthly, memo=f"{plan_.name} membership"))
            c.add(m.LedgerEntry(mrn=p["mrn"], date=start.isoformat(), kind="patient_payment", amount=-(plan_.annual if k % 2 == 0 else plan_.monthly), ref="ch_mock_sample", memo="Membership fee"))
        # balances to collect: a payment plan, text-to-pay links
        big = sorted(((api.balance(c, mrn), mrn) for mrn, _ in owing), reverse=True)
        if big and big[0][0] > 0:
            mrn = big[0][1]
            amt = round(big[0][0], 2)
            sched = rcm.payment_plan(amt, round(amt * .2, 2), 6, (today - dt.timedelta(days=45)).isoformat())
            for s in sched[:2]:
                s["paid_at"], s["ref"] = s["due"], "ch_mock_sample"
            c.add(m.PaymentPlan(mrn=mrn, total=amt, down=round(amt * .2, 2), months=6, schedule=sched, autopay=1, card_token="tok_visa"))
            for s in sched[:2]:
                c.add(m.LedgerEntry(mrn=mrn, date=s["due"], kind="patient_payment", amount=-s["amount"], ref="ch_mock_sample", memo=f"Payment plan installment {s['n']}"))
        for k, (bal, mrn) in enumerate(big[1:5]):
            if bal > 0:
                c.add(m.PaymentRequest(mrn=mrn, amount=round(bal, 2), token=f"samplepay{rnd.getrandbits(96):024x}", status="paid" if k == 0 else "sent", sent_to="01" + str(k),
                                       paid_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) if k == 0 else None, ref="ch_mock_sample" if k == 0 else ""))
                if k == 0:
                    c.add(m.LedgerEntry(mrn=mrn, date=today.isoformat(), kind="patient_payment", amount=-round(bal, 2), ref="ch_mock_sample", memo="Text-to-pay"))

        # ── engagement: messages, campaigns, forms, reviews ──
        for k, p in enumerate(patients[:14]):
            ph = f"512555{'01'}{p['i']:02d}"
            c.add(m.Message(mrn=p["mrn"], direction="out", phone=ph, body=f"Hi {p['first']}, reminder: Bright Smiles Dental on your upcoming visit. Reply C to confirm.", kind="reminder", status="delivered"))
            if k % 3 == 0:
                c.add(m.Message(mrn=p["mrn"], direction="in", phone=ph, body="C", kind="reply", status="received", read=1))
            if k in (4, 8):
                c.add(m.Message(mrn=p["mrn"], direction="in", phone=ph, body=["Is there anything earlier in the morning? I work at 9.", "Do you take my new insurance? I switched to Guardian."][k == 8],
                                kind="reply", status="received", read=0))
        c.add(m.Campaign(name="Overdue recall, September", kind="recall", template="Hi {first}, it's time for your {type}...", sent=23, booked=7,
                         created=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=28)))
        tpls = c.q(m.FormTemplate).all()
        for k, p in enumerate(patients[20:26]):
            sub = c.add(m.FormSubmission(mrn=p["mrn"], template_id=tpls[k % len(tpls)].id, token=f"sampleform{rnd.getrandbits(96):024x}", status="sent" if k % 2 else "completed"))
            if k % 2 == 0:
                sub.answers = {"allergies": "NKDA", "meds": "", "heart": "no", "blood_thinner": "no", "diabetes": "no", "signature": "Patient (synthetic)"}
                sub.submitted_at = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None) - dt.timedelta(days=k)
                c.db.flush()
                eng.apply_form(c.db, sub, tpls[k % len(tpls)], "patient")
        for k, p in enumerate(patients[30:40]):
            c.add(m.ReviewRequest(mrn=p["mrn"], token=f"samplerev{rnd.getrandbits(96):024x}", status="rated" if k < 7 else "sent", rating=[5, 5, 4, 5, 3, 5, 4][k] if k < 7 else None))

        # ── images, labs, prescriptions, notes ──
        for p in patients[:16]:
            conds = [x.as_dict() for x in c.q(m.ToothCondition, m.ToothCondition.mrn == p["mrn"]) if x.status != "planned"]
            for kind, teeth in (("bitewing", ["2", "3", "4", "5", "28", "29", "30", "31"]), ("bitewing", ["12", "13", "14", "15", "18", "19", "20", "21"]),
                                ("pano", [str(t) for t in range(1, 33)])):
                img = c.add(m.Image(mrn=p["mrn"], kind=kind, teeth=teeth, taken=(today - dt.timedelta(days=rnd.randint(10, 160))).isoformat(), device="sensor-1" if kind == "bitewing" else "pano-1",
                                    synthetic=1, data=clin.synthetic_radiograph(kind, teeth, conds, p["mrn"] + kind + teeth[0])))
                if p["i"] % 3 == 0 and kind == "bitewing":
                    res = vendors.vendor("radiograph_ai").analyze(f"{c.org}:{p['mrn']}:{teeth[0]}", kind, teeth)
                    img.ai, img.ai_status = res.data, "complete"
        lab_rows = [("Crown, e.max", "30", "A2", "sent", 5), ("Crown, zirconia", "19", "A3", "sent", -2), ("Night guard, hard", "", "", "received", -6), ("Implant crown", "19", "B1", "to_send", 18)]
        for k, (item, tooth, shade, st, due) in enumerate(lab_rows):
            p = adults[k + 1]
            c.add(m.LabCase(mrn=p["mrn"], lab=["Austin Dental Lab (synthetic)", "Hill Country Ceramics (synthetic)"][k % 2], item=item, tooth=tooth, shade=shade, status=st,
                            sent=(today - dt.timedelta(days=9)).isoformat() if st != "to_send" else "", due=(today + dt.timedelta(days=due)).isoformat(), cost=[189, 165, 95, 310][k]))
        for k, p in enumerate(adults[3:6]):
            c.add(m.Prescription(mrn=p["mrn"], drug=["Amoxicillin 500 mg capsule", "Ibuprofen 600 mg tablet", "Chlorhexidine 0.12% oral rinse"][k],
                                 sig="As directed", qty="21", status="sent", ref=f"RXSAMPLE{k}", prescriber_id=okafor.id))
        for (mrn, date), prs in list(procs_by.items())[-40:]:
            codes = ", ".join(x.code + (f" #{x.tooth}" if x.tooth else "") + (f" {x.quad}" if x.quad else "") for x in prs)
            c.add(m.ClinicalNote(mrn=mrn, date=date, kind="progress", status="signed", signed_by=c.email, signed_at=dt.datetime.now(dt.timezone.utc).replace(tzinfo=None),
                                 text=f"Procedures: {codes}.\nPatient tolerated well. Post-op instructions given. (Synthetic note.)"))
        c.db.commit()
        n_claims = c.db.query(m.Claim).filter(m.Claim.org_key == c.org).count()
        return {"ok": True, "patients": len(patients), "appointments": appts_made, "claims": n_claims, "locations": 2}
    finally:
        c.db.info.pop("dental_sample", None)


def mrn_seed(mrn):
    return "seed" + mrn


def _preauth(c, rcmapi, plan, items):
    """The same steps as POST /preauths, without the request object."""
    import dental_api as api

    class P:
        def __init__(s, it):
            s.code, s.tooth, s.surface, s.quad, s.fee, s.date, s.provider_id, s.location_id, s.id = it.code, it.tooth, it.surface, it.quad, it.fee, dt.date.today().isoformat(), plan.provider_id, plan.location_id, None
    cd = rcmapi.make_claim(c, plan.mrn, [P(i) for i in items], kind="predetermination")
    claim = c.get(m.Claim, cd["id"])
    pa = c.add(m.PreAuth(mrn=plan.mrn, plan_id=plan.id, claim_id=claim.id, payer=claim.payer, amount=claim.total, status="draft",
                         follow_up=(dt.date.today() + dt.timedelta(days=7)).isoformat()))
    c.db.flush()
    for i in items:
        i.preauth_id = pa.id
    c.db.commit()
    rcmapi.preauth_send(pa.id, c)
    return pa
