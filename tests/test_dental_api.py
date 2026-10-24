"""The dental EMR's API end to end: access by role, encryption at rest, the access log, a claim from chart to payment
(837D out, 835 in), denials, patient billing, and the links patients open. Synthetic data only."""
import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select, text


def _clinic(practice="dental"):
    import auth
    org = "Dental Test " + uuid.uuid4().hex[:6]
    with auth.SessionLocal() as db:
        db.add(auth.OrgSettings(org_key=org, category=auth.ENTITLEMENTS_CATEGORY, data=json.dumps({"rev": 0, "practice": practice})))
        db.commit()
    return org


def _login(org, role="admin"):
    import auth, main
    email = f"{role}-{uuid.uuid4().hex[:8]}@example.test"
    with auth.SessionLocal() as db:
        db.add(auth.User(email=email, password_hash=auth.hash_password("correct horse 1"), full_name="Dr " + role.title(), organization=org, role=role,
                         claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
        db.commit()
    c = TestClient(main.app)
    r = c.post("/login", data={"email": email, "password": "correct horse 1"})
    assert r.status_code in (200, 302, 303), r.text[:200]
    c.email = email
    return c


def _wait_sample(c):
    """The synthetic practice is built in the background (dental_api.build_sample)."""
    import time
    for _ in range(600):
        s = c.get("/api/dental/sample/status").json()
        if s["status"] != "building":
            return s
        time.sleep(0.1)
    raise AssertionError("the sample practice never finished")


@pytest.fixture(scope="module")
def practice():
    org = _clinic()
    admin = _login(org)
    r = admin.post("/api/dental/sample")
    assert r.status_code == 200 and r.json()["status"] == "building", r.text
    s = _wait_sample(admin)
    assert s["status"] == "done", s
    claims = admin.get("/api/dental/claims?kind=").json()
    return {"org": org, "admin": admin, "sample": {"patients": s["patients"], "locations": len(admin.get("/api/dental/setup").json()["locations"]),
                                                   "appointments": 200, "claims": len(claims)}}


def test_sample_practice_loads(practice):
    s = practice["sample"]
    assert s["patients"] == 48 and s["locations"] == 2 and s["claims"] > 20
    a = practice["admin"]
    assert sum(len(a.get(f"/api/dental/schedule?days=7&location_id={l['id']}").json()["appointments"]) for l in a.get("/api/dental/setup").json()["locations"]) > 50
    assert len(a.get("/api/dental/patients").json()) == 48
    sched = a.get("/api/dental/schedule").json()
    assert sched["operatories"] and sched["providers"]
    assert a.post("/api/dental/sample").status_code == 409           # once only


def test_medical_clinics_cant_use_the_dental_api():
    c = _login(_clinic("medical"))
    assert c.get("/api/dental/patients").status_code == 403


def test_roles(practice):
    org = practice["org"]
    mrn = practice["admin"].get("/api/dental/patients").json()[0]["mrn"]
    front, viewer, biller, prov = _login(org, "front_desk"), _login(org, "viewer"), _login(org, "biller"), _login(org, "provider")
    assert front.get("/api/dental/schedule").status_code == 200
    assert front.get(f"/api/dental/patients/{mrn}").status_code == 403            # the clinical chart isn't the front desk's
    assert viewer.get("/api/dental/patients").status_code == 200
    assert viewer.post("/api/dental/appointments", json={"mrn": mrn, "start": "2030-01-07T09:00"}).status_code == 403
    assert biller.get(f"/api/dental/patients/{mrn}").status_code == 200           # billing reads the chart for narratives
    assert biller.post(f"/api/dental/patients/{mrn}/conditions", json={"tooth": "3", "condition": "caries", "surface": "O"}).status_code == 403
    assert prov.post(f"/api/dental/patients/{mrn}/conditions", json={"tooth": "3", "condition": "caries", "surface": "O"}).status_code == 200
    assert prov.get("/api/dental/era").status_code == 403                         # posting money is billing's
    assert front.post("/api/dental/sample").status_code == 403
    assert prov.post("/api/dental/claims/1/submit").status_code == 403


def test_other_clinics_cant_see_this_one(practice):
    other = _login(_clinic())
    mrn = practice["admin"].get("/api/dental/patients").json()[0]["mrn"]
    assert other.get(f"/api/dental/patients/{mrn}").status_code == 404
    cid = practice["admin"].get("/api/dental/claims").json()[0]["id"]
    assert other.get(f"/api/dental/claims/{cid}").status_code == 404


def test_phi_is_encrypted_at_rest(practice):
    import auth
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[1]["mrn"]
    assert a.post(f"/api/dental/patients/{mrn}/notes", json={"text": "Secret finding: caries #19 MOD"}).status_code == 200
    with auth.SessionLocal() as db:
        raw = db.execute(text("select text from dental_notes where mrn = :m order by id desc limit 1"), {"m": mrn}).scalar()
        perio = db.execute(text("select readings from dental_perio_exams where readings is not null limit 1")).scalar()
    assert raw.startswith("A2:") and "caries" not in raw
    assert perio.startswith("A2:")
    notes = a.get(f"/api/dental/patients/{mrn}").json()["notes"]
    assert any("caries #19" in n["text"] for n in notes)


def test_chart_views_and_changes_are_in_the_access_log(practice):
    import audit, auth
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[2]["mrn"]
    a.get(f"/api/dental/patients/{mrn}")
    a.post(f"/api/dental/patients/{mrn}/conditions", json={"tooth": "30", "condition": "fracture"})
    audit.drain()
    with auth.SessionLocal() as db:
        rows = db.scalars(select(audit.AccessLog).where(audit.AccessLog.record_id == mrn, audit.AccessLog.user_email == a.email)).all()
    assert {r.action for r in rows} >= {"view", "create"} and all(r.what == "dental chart" for r in rows)


def test_claim_from_chart_to_payment(practice):
    import dental_vendors as v
    a = practice["admin"]
    pts = [p for p in a.get("/api/dental/patients").json() if p["payer"] and p["payer"] != "Self-pay" and p["elig_active"]]
    mrn = pts[0]["mrn"]
    provs = a.get("/api/dental/setup").json()["providers"]
    dentist = next(p for p in provs if p["kind"] == "dentist")
    r = a.post(f"/api/dental/patients/{mrn}/procedures", json={"lines": [{"code": "D2392", "tooth": "14", "surface": "MO"}, {"code": "D0140"}], "provider_id": dentist["id"], "claim": True})
    assert r.status_code == 200, r.text
    claim = r.json()["claim"]
    assert claim["status"] == "ready", claim["edits"]
    assert {l["code"] for l in claim["lines"]} == {"D2392", "D0140"} and claim["total"] > 0
    chart = a.get(f"/api/dental/patients/{mrn}").json()
    assert any(c["tooth"] == "14" and c["condition"] == "composite" and c["status"] == "completed" for c in chart["conditions"])
    before = chart["balance"]
    v.MockClearinghouse._remits = []
    sent = a.post(f"/api/dental/claims/{claim['id']}/submit").json()
    assert sent["status"] == "submitted" and sent["result"]["environment"] == "mock"
    x = a.get(f"/api/dental/claims/{claim['id']}/837").text
    assert "SV3*AD:D2392" in x and "TOO*JP*14*M:O" in x
    posted = a.post("/api/dental/era/fetch").json()["posted"]
    assert len(posted) == 1 and "error" not in posted[0]
    after = a.get(f"/api/dental/claims/{claim['id']}").json()
    assert after["status"] in ("paid", "partial", "denied")
    bal = a.get(f"/api/dental/patients/{mrn}").json()["balance"]
    assert bal == pytest.approx(before - after["paid"] - after["write_off"], abs=0.01)
    assert a.post("/api/dental/era/upload", json={"x12": "garbage"}).status_code == 400


def test_red_edits_block_sending(practice):
    a = practice["admin"]
    held = [c for c in a.get("/api/dental/claims").json() if c["status"] == "draft"]
    assert held, "the sample has a claim held for a missing quadrant"
    r = a.post(f"/api/dental/claims/{held[0]['id']}/submit")
    assert r.status_code == 422 and "Fix first" in r.json()["detail"]


def test_denials_reach_the_work_queue(practice):
    a = practice["admin"]
    work = a.get("/api/dental/work").json()
    denials = [w for w in work if w["kind"] == "denial"]
    assert denials and all(w["action"] in ("fix", "attach", "appeal", "patient", "review") for w in denials)
    w = next(x for x in denials if x["id"])
    c = a.post(f"/api/dental/claims/{w['claim_id']}/corrected").json()
    assert c["frequency"] == "7" and c["original_claim_no"]


def test_code_check_flags_missed_and_needs_a_dentist(practice):
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[3]["mrn"]
    r = a.post(f"/api/dental/patients/{mrn}/code-check", json={"note": "#30 MOD composite restoration placed. Four bitewings taken today.", "lines": [{"code": "D2391", "tooth": "30", "surface": "MOD"}], "use_ai": False}).json()
    kinds = {(s["kind"], s["code"]) for s in r["suggestions"]}
    assert ("undercoded", "D2393") in kinds and ("missed", "D0274") in kinds
    biller = _login(practice["org"], "biller")
    sid = r["suggestions"][0]["id"]
    assert biller.post(f"/api/dental/suggestions/{sid}", json={"decision": "approved"}).status_code == 403
    assert a.post(f"/api/dental/suggestions/{sid}", json={"decision": "approved"}).json()["status"] == "approved"


def test_eligibility_and_estimates(practice):
    a = practice["admin"]
    p = next(p for p in a.get("/api/dental/patients").json() if p["payer"] not in ("", "Self-pay"))
    b = a.post(f"/api/dental/patients/{p['mrn']}/eligibility").json()
    assert "max_remaining" in b and b["frequency"]
    e = a.post(f"/api/dental/patients/{p['mrn']}/estimate", json={"lines": [{"code": "D2740", "tooth": "3"}]}).json()
    assert e["lines"][0]["fee"] == 1250


def test_treatment_plan_presentation_and_signature(practice):
    import main
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[5]["mrn"]
    pid = a.post(f"/api/dental/patients/{mrn}/plans", json={"name": "Crown", "items": [{"code": "D2950", "tooth": "19"}, {"code": "D2740", "tooth": "19"}]}).json()["id"]
    pres = a.post(f"/api/dental/plans/{pid}/present").json()
    token = pres["url"].rsplit("/", 1)[1]
    anon = TestClient(main.app)
    assert anon.get(pres["url"]).status_code == 200
    plan = anon.get(f"/api/public/dental/plan/{token}").json()
    assert len(plan["items"]) == 2 and "name" not in plan.get("patient", {})
    assert anon.post(f"/api/public/dental/plan/{token}/accept", json={"signature": "", "accepted": []}).status_code == 400
    keep = [plan["items"][0]["id"]]
    assert anon.post(f"/api/public/dental/plan/{token}/accept", json={"signature": "Pat Test", "accepted": keep}).json()["status"] == "partial"
    assert anon.get("/api/public/dental/plan/not-a-real-token-at-all-xx").status_code == 404


def test_online_booking_uses_provider_hours(practice):
    import main
    anon = TestClient(main.app)
    info = anon.get(f"/api/public/dental/book/{_org_key(practice)}").json()
    loc = info["locations"][0]["id"]
    slots = anon.get(f"/api/public/dental/book/{_org_key(practice)}/slots?location_id={loc}&kind=cleaning").json()
    assert slots
    s = slots[0]
    body = {"kind": "cleaning", "location_id": loc, "start": s["start"], "token": s["token"], "first": "Robin", "last": "Newpatient", "dob": "1991-02-03", "phone": "5125550188"}
    assert anon.post(f"/api/public/dental/book/{_org_key(practice)}", json=body).json()["ok"]
    assert anon.post(f"/api/public/dental/book/{_org_key(practice)}", json=body).status_code == 409    # that time is taken now
    a = practice["admin"]
    day = s["start"][:10]
    appt = next(x for x in a.get(f"/api/dental/schedule?date={day}&location_id={loc}").json()["appointments"] if x["source"] == "online" and not x["mrn"] and x["start"].startswith(s["start"]))
    assert a.post(f"/api/dental/appointments/{appt['id']}/accept-request", json={}).json()["mrn"].startswith("D")


def _org_key(practice):
    import auth
    return auth._org_namespace(type("U", (), {"organization": practice["org"], "email": ""})())


def test_forms_write_into_the_chart(practice):
    import main
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[7]["mrn"]
    forms = a.get("/api/dental/forms").json()["templates"]
    mh = next(t for t in forms if t["name"] == "Medical history")
    url = a.post(f"/api/dental/patients/{mrn}/forms", json={"template_ids": [mh["id"]]}).json()["sent"][0]["url"]
    token = url.rsplit("/", 1)[1]
    anon = TestClient(main.app)
    assert anon.post(f"/api/public/dental/form/{token}", json={"allergies": "Latex", "blood_thinner": "yes"}).status_code == 400   # unsigned
    assert anon.post(f"/api/public/dental/form/{token}", json={"allergies": "Latex", "blood_thinner": "yes", "signature": "Pat Test"}).json()["ok"]
    p = a.get(f"/api/dental/patients/{mrn}").json()["patient"]
    assert p["allergies"] == "Latex" and "Blood thinner" in p["medical_alerts"] and p["mh_review_pending"]


def test_text_to_pay_and_payment_plan(practice):
    import main
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[9]["mrn"]
    a.post(f"/api/dental/patients/{mrn}/adjustments", json={"amount": 120, "memo": "test charge"})
    r = a.post(f"/api/dental/patients/{mrn}/text-to-pay", json={"amount": 50}).json()
    token = r["url"].rsplit("/", 1)[1]
    anon = TestClient(main.app)
    assert anon.post(f"/api/public/dental/pay/{token}", json={"token": "tok_declined"}).status_code == 402
    bal0 = a.get(f"/api/dental/patients/{mrn}").json()["balance"]
    assert anon.post(f"/api/public/dental/pay/{token}", json={"token": "tok_visa"}).json()["ok"]
    assert a.get(f"/api/dental/patients/{mrn}").json()["balance"] == pytest.approx(bal0 - 50)
    plan = a.post(f"/api/dental/patients/{mrn}/payment-plans", json={"total": 300, "down": 0, "months": 3, "autopay": True, "token": "tok_visa"}).json()
    assert round(sum(s["amount"] for s in plan["schedule"]), 2) == 300 and "card_token" not in plan
    assert a.post(f"/api/dental/payment-plans/{plan['id']}/charge").json()["schedule"][0]["paid_at"]


def test_reminders_and_text_replies(practice):
    import dental_vendors as v
    a = practice["admin"]
    assert a.post("/api/dental/reminders/run").status_code == 200
    th = a.get("/api/dental/messages").json()[0]
    r = a.post("/api/dental/sms/simulate", json={"phone": th["phone"], "body": "STOP"}).json()
    if r["mrn"]:
        assert r["action"] == "opted_out"
        assert a.post(f"/api/dental/patients/{r['mrn']}/messages", json={"body": "hi"}).status_code == 400


def test_epcs_needs_two_step_login(practice):
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[11]["mrn"]
    dentist = next(p for p in a.get("/api/dental/setup").json()["providers"] if p["kind"] == "dentist")
    r = a.post(f"/api/dental/patients/{mrn}/rx", json={"drug": "Hydrocodone-acetaminophen 5/325 mg tablet", "prescriber_id": dentist["id"]})
    assert r.status_code == 403 and "two-step" in r.json()["detail"]
    ok = a.post(f"/api/dental/patients/{mrn}/rx", json={"drug": "Ibuprofen 600 mg tablet", "prescriber_id": dentist["id"], "override_allergy": True})
    assert ok.status_code == 200 and ok.json()["status"] == "sent"


def test_reports_and_huddle(practice):
    a = practice["admin"]
    r = a.get("/api/dental/reports/production").json()
    assert r["totals"]["gross"] > 0 and r["providers"] and r["by_location"]
    h = a.get("/api/dental/huddle").json()
    assert "scheduled_production" in h and "goal_day" in h


def test_import_open_dental_export(practice):
    a = practice["admin"]
    pts = "PatNum,LName,FName,Birthdate,Gender,WirelessPhone,CarrierName,SubscriberID\n9001,Rivera,Sam,1980-03-04,1,5125550177,Delta Dental PPO,SYN99\n9002,Bad,Row,,0,,,\n"
    pv = a.post("/api/dental/import/preview", json={"source": "opendental", "kind": "patients", "text": pts}).json()
    assert pv["count"] == 2 and pv["mapping"]["dob"] == "Birthdate" and pv["errors"]
    assert a.post("/api/dental/import/commit", json={"source": "opendental", "kind": "patients", "text": pts}).json()["summary"]["added"] == 2
    procs = "PatNum,ProcDate,ProcCode,ToothNum,Surf,ProcFee,ProcStatus\n9001,2025-05-01,D1110,,,120,2\n9001,2025-05-01,D2392,14,MO,225,2\n9001,2026-01-01,D2740,30,,1250,1\n9001,2025-05-01,X999,,,0,2\n"
    j = a.post("/api/dental/import/commit", json={"source": "opendental", "kind": "procedures", "text": procs}).json()
    assert j["summary"]["added"] == 2                                # the planned crown (status 1) and the bad code are skipped
    chart = a.get("/api/dental/patients/OD9001").json()
    assert {p["code"] for p in chart["procedures"]} == {"D1110", "D2392"}


def test_remove_sample(practice):
    org = _clinic()
    a = _login(org)
    a.post("/api/dental/sample")
    _wait_sample(a)
    assert a.delete("/api/dental/sample").json()["removed"] > 100
    assert a.get("/api/dental/patients").json() == []


def test_a_new_real_clinic_starts_with_a_starter_setup():
    a = _login(_clinic())
    s = a.get("/api/dental/setup").json()
    assert len(s["locations"]) == 1 and [o["name"] for o in s["operatories"]] == ["Op 1", "Op 2", "Hyg 1"]
    assert s["providers"][0]["user_email"] == a.email and s["providers"][0]["kind"] == "dentist"
    assert len(s["hours"]) == 5 and len(s["fees"]) > 50
    assert a.get("/api/dental/patients").json() == []                 # no invented patients in a real clinic
    sched = a.get("/api/dental/schedule").json()
    assert sched["operatories"] and sched["location"]
    a.post("/api/dental/sample")                                       # trying the sample later replaces the untouched starter
    _wait_sample(a)
    assert len(a.get("/api/dental/setup").json()["locations"]) == 2


def test_a_new_test_clinic_starts_with_the_synthetic_practice():
    import auth
    org = _clinic()
    with auth.SessionLocal() as db:
        row = db.scalar(select(auth.OrgSettings).where(auth.OrgSettings.org_key == org, auth.OrgSettings.category == auth.ENTITLEMENTS_CATEGORY))
        d = json.loads(row.data); d["test_clinic"] = True; row.data = json.dumps(d); db.commit()
    a = _login(org)
    assert a.get("/api/dental/setup").status_code == 200                # opens right away, with the starter setup
    assert _wait_sample(a)["status"] == "done"
    assert len(a.get("/api/dental/patients").json()) == 48
    assert len(a.get("/api/dental/setup").json()["locations"]) == 2


def test_coding_a_visit_note_like_the_medical_encounter(practice):
    a = practice["admin"]
    mrn = a.get("/api/dental/patients").json()[12]["mrn"]
    note = "Periodic exam. #30 MOD composite restoration placed. Four bitewings taken today. Adult prophylaxis completed."
    r = a.post(f"/api/dental/patients/{mrn}/code-note", json={"note": note}).json()
    got = {(c["code"], c["tooth"]) for c in r["codes"]}
    assert {("D2393", "30"), ("D0274", ""), ("D1110", ""), ("D0120", "")} <= got
    assert all(c["why"] and c["conf"] for c in r["codes"])
    assert r["check"]["total"] > 0 and isinstance(r["check"]["edits"], list)
    k = a.post(f"/api/dental/patients/{mrn}/claim-preview", json={"lines": [{"code": "D2392", "tooth": "8", "surface": "MO"}]}).json()
    assert any(e["lvl"] == "red" for e in k["edits"])                 # a back-tooth filling code on a front tooth
    assert a.post(f"/api/dental/patients/{mrn}/code-note", json={"note": "short"}).json()["codes"] == []
