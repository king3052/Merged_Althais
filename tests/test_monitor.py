"""Watching payments (monitor.py) and AI-drafted appeal letters (recovery.fill_from_note)."""
import datetime as dt
import json

import pytest
from sqlalchemy import select

import lost_money as lm
import monitor as mon
import recovery as rc

ORG = "Monitor Test Clinic"


def _ok(code, amt, charge, mods=()):
    return (code, list(mods), charge, amt, amt, [("CO", "45", round(charge - amt, 2))] if charge > amt else [], 1)


def _den(code, charge, carc):
    return (code, [], charge, 0.0, None, [("CO", carc, charge)], 1)


def _r(cid, dos, lines):
    return {"id": cid, "dos": dos, "charge": sum(l[2] for l in lines), "paid": sum(l[3] for l in lines), "pr": 0.0, "lines": lines}


def era(date, claims, npi="1234567893"):
    return lm.build_835("AETNA", date, claims, "CI").replace("XX*1111111112", f"XX*{npi}")


@pytest.fixture()
def db():
    import auth
    import billing_activation as ba
    with auth.SessionLocal() as s:
        for org in (ORG, "Other Monitor Clinic"):
            s.query(mon.PaymentFile).filter(mon.PaymentFile.org_key == org).delete()
            s.query(rc.RecoveryCase).filter(rc.RecoveryCase.org_key == org).delete()
            s.query(auth.OrgSettings).filter(auth.OrgSettings.org_key == org).delete()
            s.query(ba.BillingProfile).filter(ba.BillingProfile.org_key == org).delete()
        s.add(ba.BillingProfile(org_key=ORG, data=json.dumps({"npi": "1234567893", "legalName": "Monitor Clinic"})))
        s.add(ba.BillingProfile(org_key="Other Monitor Clinic", data=json.dumps({"npi": "1987654321"})))
        s.commit()
        yield s


def test_files_are_kept_once_and_encrypted(db):
    text = era("2026-09-10", [_r("M1", "2026-09-01", [_ok("99213", 92.0, 145.0)])])
    assert mon.add_file(db, ORG, "a.835", text, "upload") is True
    assert mon.add_file(db, ORG, "again.835", text, "upload") is False           # the same file twice is kept once
    assert mon.add_file(db, ORG, "notes.txt", "hello there", "upload") is False
    f = db.scalar(select(mon.PaymentFile).where(mon.PaymentFile.org_key == ORG))
    assert f.kind == "835" and "AETNA" not in f.sealed


def test_claimmd_eras_go_to_the_clinic_they_pay(db):
    assert mon.route_era(db, era("2026-09-10", [_r("M1", "2026-09-01", [_ok("99213", 92.0, 145.0)])]), "77") == [ORG]
    assert mon.route_era(db, era("2026-09-11", [_r("X1", "2026-09-01", [_ok("99213", 92.0, 145.0)])], npi="1555555555"), "78") == []   # no clinic has that NPI
    assert mon.status(db, ORG)["fromClaimmd"] == 1 and mon.status(db, "Other Monitor Clinic")["filesKept"] == 0


def test_runs_open_and_close_cases_and_skip_when_nothing_is_new(db):
    rates = [_r(f"P{i}", "2026-09-01", [_ok("99214", 131.0, 210.0)]) for i in range(4)]
    mon.route_era(db, era("2026-09-10", rates + [_r("DEN", "2026-09-02", [_den("99214", 210.0, "16")])]), "1")
    db.commit()
    assert mon.run_due(dt.datetime(2026, 9, 12, 15)) >= 1
    case = db.scalar(select(rc.RecoveryCase).where(rc.RecoveryCase.org_key == ORG, rc.RecoveryCase.claim_id == "DEN"))
    assert case.status == "OPEN" and case.created_by == "Althais (watching payments)" and case.expected == 131.0
    st = mon.status(db, ORG)
    assert st["newCases"] == 1 and st["files"] == 1 and st["lastRun"]
    before = mon.status(db, ORG)["lastRun"]
    mon.run_due(dt.datetime(2026, 9, 12, 16))
    assert mon.status(db, ORG)["lastRun"] == before                              # no new files: not run again
    # the payer pays the resubmitted claim; the next ERA closes the case on its own
    mon.route_era(db, era("2026-10-01", [_r("DEN", "2026-09-02", [_ok("99214", 131.0, 210.0)])]), "2")
    db.commit()
    mon.run_due(dt.datetime(2026, 10, 2, 15))
    db.refresh(case)
    assert case.status == "RECOVERED" and case.recovered == 131.0
    assert mon.status(db, ORG)["recovered"] == 1


def test_claims_althais_sent_are_rebuilt_for_the_analysis(db):
    import billing_activation as ba
    prof = {"npi": "1234567893", "tin": "999999999", "legalName": "Monitor Clinic", "address": {"line1": "1 Main", "city": "Austin", "state": "TX", "zip": "78701"},
            "payers": [{"name": "Aetna", "payerId": "60054"}], "providers": [{"name": "Lee, Jordan", "npi": "1234567893"}]}
    row = db.scalar(select(ba.BillingProfile).where(ba.BillingProfile.org_key == ORG))
    row.data = json.dumps(prof)
    snap = {"claimId": "SENT1", "patient": "DOE, PAT", "provider": "Lee, Jordan", "npi": "1234567893", "insuredId": "W1", "dob": "1990-01-01", "sex": "F",
            "svcDate": "2026-09-03", "payer": "Aetna", "cpts": [{"code": "99213", "charge": 145, "units": 1}], "icds": [{"code": "J02.9"}]}
    db.add(ba.ClaimTransmission(org_key=ORG, claim_id="SENT1", content_hash="h", idempotency_key="mon-sent-1", payer="Aetna", user_id=1,
                                delivery="SENT", snapshot=json.dumps(snap), sent_at=dt.datetime(2026, 9, 3)))
    db.commit()
    pairs = mon._sent_claims(db, ORG, dt.datetime(2026, 1, 1))
    assert len(pairs) == 1 and b"CLM*SENT1*145.00" in pairs[0][1]
    c = lm.parse_837p(pairs[0][1].decode())[0]
    assert c.raw and c.payer == "AETNA" and c.patient == "DOE, PAT"


def test_report_uploads_are_kept_for_watching():
    from fastapi.testclient import TestClient
    import auth
    import main
    email = "monitor-biller@example.com"
    with auth.SessionLocal() as s:
        if not s.scalar(select(auth.User).where(auth.User.email == email)):
            s.add(auth.User(email=email, password_hash=auth.hash_password("violet maple harbor 42"), full_name="P", organization="Monitor Upload Clinic",
                            role="biller", claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
            s.commit()
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "violet maple harbor 42"})
    files = [("files", ("a.835", era("2026-09-10", [_r("U1", "2026-09-01", [_ok("99213", 92.0, 145.0)])]).encode()))]
    assert c.post("/api/lost-money/report", data={"save": "1"}, files=files).status_code == 200
    st = c.get("/api/recovery/monitor").json()
    assert st["filesKept"] == 1 and st["fromClaimmd"] == 0


# ── AI-drafted appeal letters ──
NOTE = ("CC: sore throat x3 days, fever 101.4. Exam: tonsillar exudate, tender anterior cervical nodes. Rapid strep positive. "
        "Separately evaluated new-onset wheezing; albuterol neb given with improvement. Assessment: strep pharyngitis; acute bronchospasm. "
        "Plan: amoxicillin 500 mg BID x10 days; ceftriaxone IM given in clinic due to vomiting.")
LETTER = ("Re: Appeal\n\nThe evaluation was separate. [From the note: the problem evaluated beyond the procedure.] Please reprocess.\n"
          "[Payer appeals address or fax]\n")


def test_fill_from_note_keeps_only_what_the_note_says():
    ph = "[From the note: the problem evaluated beyond the procedure.]"
    def ask(prompt):
        assert ph in prompt and "VISIT NOTE" in prompt
        return {"fills": [
            {"placeholder": ph, "text": "We separately evaluated new-onset wheezing and treated it with an albuterol nebulizer.",
             "quote": "Separately evaluated new-onset wheezing; albuterol neb given with improvement."},
            {"placeholder": "[Payer appeals address or fax]", "text": "PO Box 14079, Lexington KY", "quote": "PO Box 14079"},          # not in the note
        ]}
    out = rc.fill_from_note(LETTER, NOTE, ask=ask)
    assert "new-onset wheezing" in out["letter"] and ph not in out["letter"]
    assert out["left"] == ["[Payer appeals address or fax]"] and len(out["filled"]) == 1


def test_fill_from_note_throws_out_invented_facts():
    ph = "[From the note: the problem evaluated beyond the procedure.]"
    made_up = {"fills": [{"placeholder": ph, "text": "We treated a fractured wrist.", "quote": "X-ray showed a distal radius fracture."}]}
    out = rc.fill_from_note(LETTER, NOTE, ask=lambda p: made_up)
    assert out["filled"] == [] and ph in out["letter"]
    sneaky = {"fills": [{"placeholder": ph, "text": "See [attached].", "quote": "Rapid strep positive."},
                        {"placeholder": "[not in the letter]", "text": "x", "quote": "Rapid strep positive."}]}
    assert rc.fill_from_note(LETTER, NOTE, ask=lambda p: sneaky)["filled"] == []
    assert rc.fill_from_note("No brackets here.", NOTE, ask=lambda p: 1 / 0)["filled"] == []      # nothing to fill: no AI call


def test_draft_endpoint(monkeypatch):
    from fastapi.testclient import TestClient
    import auth
    import main
    email = "monitor-biller@example.com"
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "violet maple harbor 42"})
    with auth.SessionLocal() as s:
        case = rc.RecoveryCase(org_key="Monitor Upload Clinic", dedupe="draft-test", category="denial", kind="appeal", claim_id="D1", status="OPEN",
                               history="[]", recovered=0.0, basis_date="", payer="AETNA", code="99213", carc="50", expected=92.0)
        s.add(case)
        s.commit()
        cid = case.id
    monkeypatch.setattr(main, "_fast_json", lambda *a, **k: {"fills": []})
    assert c.post(f"/api/recovery/cases/{cid}/draft", json={"note": "short"}).status_code == 400
    r = c.post(f"/api/recovery/cases/{cid}/draft", json={"note": NOTE})
    assert r.status_code == 200 and r.json()["left"] and r.json()["filled"] == []
    def boom(*a, **k):
        raise TimeoutError()
    monkeypatch.setattr(main, "_fast_json", boom)
    assert c.post(f"/api/recovery/cases/{cid}/draft", json={"note": NOTE}).status_code == 502
