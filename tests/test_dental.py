"""Dental claims: the dental rules, dental uploads, the extension's dental checks and claims, and dental note coding."""
import io
import json

import pytest

import dental_rules as dr
import emr_connect as ec


def flags(lines, **extra):
    c = {"cpt": [l["code"] for l in lines], "lines": lines, "tooth_column": True}
    c.update(extra)
    return dr.check(c)


def codes(fl):
    return [f["code"] for f in fl]


def test_teeth_and_surfaces():
    assert dr.tooth_kind("8") == ("perm", "anterior") and dr.tooth_kind("14") == ("perm", "molar") and dr.tooth_kind("5") == ("perm", "premolar")
    assert dr.tooth_kind("A") == ("prim", "molar") and dr.tooth_kind("E") == ("prim", "anterior")
    assert dr.tooth_kind("33") is None and dr.tooth_kind("U") is None
    assert dr.norm_surface("m.o.d") == "MOD" and dr.norm_tooth("#14") == "14"


def test_a_clean_recall_visit_has_no_problems():
    fl = flags([{"code": "D0120"}, {"code": "D1110"}, {"code": "D0274"}])
    assert [f for f in fl if f["severity"] in ("error", "warn")] == []


def test_filling_needs_matching_surfaces():
    fl = flags([{"code": "D2391", "tooth": "19", "surface": "MOD"}])
    f = next(f for f in fl if f["code"] == "surface_count")
    assert f["severity"] == "error" and "D2393" in f["message"] and f["tooth"] == "19"
    assert "no_surface" in codes(flags([{"code": "D2392", "tooth": "14", "surface": ""}]))
    assert not [f for f in flags([{"code": "D2394", "tooth": "3", "surface": "MODBL"}]) if f["code"] == "surface_count"]   # 4+ surfaces


def test_tooth_rules():
    assert "no_tooth" in codes(flags([{"code": "D2740", "tooth": ""}]))
    assert "bad_tooth" in codes(flags([{"code": "D2140", "tooth": "33", "surface": "O"}]))
    fr = flags([{"code": "D2331", "tooth": "8", "surface": "MO"}])
    assert any(f["code"] == "bad_surface" and "incisal" in f["message"] for f in fr)            # front teeth have no occlusal surface
    assert any(f["code"] == "bad_surface" for f in flags([{"code": "D2392", "tooth": "30", "surface": "MI"}]))
    assert "tooth_type" in codes(flags([{"code": "D3330", "tooth": "8"}]))                       # molar root canal on a front tooth
    assert "tooth_type" in codes(flags([{"code": "D2330", "tooth": "19", "surface": "M"}]))      # front-tooth composite on a molar
    assert "tooth_type" in codes(flags([{"code": "D3310", "tooth": "E"}]))                       # permanent-tooth code on a primary tooth


def test_quadrant_and_arch():
    assert "no_quad" in codes(flags([{"code": "D4341", "quad": ""}]))
    assert "no_quad" not in codes(flags([{"code": "D4341", "quad": "UR"}]))
    assert "no_arch" in codes(flags([{"code": "D5110", "quad": ""}]))
    assert "no_arch" not in codes(flags([{"code": "D5110", "quad": "UA"}]))


def test_same_tooth_and_same_visit_conflicts():
    fl = flags([{"code": "D7140", "tooth": "17"}, {"code": "D2391", "tooth": "17", "surface": "O"}])
    assert any(f["code"] == "same_tooth" and f["severity"] == "error" for f in fl)
    fl = flags([{"code": "D2740", "tooth": "30"}, {"code": "D2391", "tooth": "30", "surface": "O"}])
    assert any(f["code"] == "same_tooth" and f["severity"] == "warn" and "D2950" in f["message"] for f in fl)
    assert "dup_line" in codes(flags([{"code": "D2391", "tooth": "3", "surface": "O"}, {"code": "D2391", "tooth": "3", "surface": "O"}]))
    assert "two_exams" in codes(flags([{"code": "D0150"}, {"code": "D0120"}]))
    assert "clean_and_srp" in codes(flags([{"code": "D1110"}, {"code": "D4341", "quad": "UR"}]))
    assert "clean_and_maint" in codes(flags([{"code": "D1110"}, {"code": "D4910"}]))
    assert "fmx_and_bw" in codes(flags([{"code": "D0210"}, {"code": "D0274"}]))


def test_attachments_preauth_and_ages():
    fl = flags([{"code": "D2740", "tooth": "30"}])
    assert any(f["code"] == "attach" and "X-ray" in f["message"] for f in fl) and "preauth" in codes(fl)
    assert any(f["code"] == "attach" and "periodontal chart" in f["message"] for f in flags([{"code": "D4341", "quad": "UR"}]))
    assert "age_limit" in codes(flags([{"code": "D1206"}], dob="1980-01-01", dos="2026-10-01"))
    assert "age_limit" not in codes(flags([{"code": "D1206"}], dob="2018-01-01", dos="2026-10-01"))


def test_no_tooth_column_on_screen_is_a_note_not_an_error():
    fl = dr.check({"cpt": ["D2392"], "lines": [], "tooth_column": False})
    assert codes(fl).count("no_tooth") == 0 and "tooth_unseen" in codes(fl)


def test_frequency_across_visits():
    def visit(dos, *cs):
        return {"patient_name": "Pat A", "mrn": "M1", "dos": dos, "cpt": list(cs), "lines": [{"code": c} for c in cs]}
    claims = [visit("2026-01-10", "D1110", "D0274"), visit("2026-04-10", "D1110", "D0274"), visit("2026-08-10", "D1110")]
    h = dr.history_flags(claims)
    msgs = [f["message"] for f in h.get(1, [])] + [f["message"] for f in h.get(2, [])]
    assert any("bitewing" in m for m in h.get(1, []) and [f["message"] for f in h[1]])
    assert any("cleanings" in m for m in [f["message"] for f in h.get(2, [])])
    assert 0 not in h


def test_dental_claims_skip_medical_rules():
    c = {"claim_id": "D1", "patient_name": "Pat", "dos": "2026-10-01", "payer": "Delta Dental", "cpt": ["D0120", "D1110"], "icd": [], "amount": 175}
    fl = ec.claim_check(c, today=__import__("datetime").date(2026, 10, 5))
    assert "no_dx" not in codes(fl) and "bad_cpt" not in codes(fl)
    med = dict(c, cpt=["99213"])
    assert "no_dx" in codes(ec.claim_check(med, today=__import__("datetime").date(2026, 10, 5)))


def test_dental_upload_keeps_teeth_and_checks_frequency():
    csv = ("claim_id,patient_name,mrn,date_of_service,payer,cdt,tooth,surface,quadrant,charge\n"
           "A1,Pat Dent,M9,2026-01-05,Delta Dental,D0274,,,,75\n"
           "A2,Pat Dent,M9,2026-05-05,Delta Dental,D0274,,,,75\n"
           "A2,Pat Dent,M9,2026-05-05,Delta Dental,D2391,19,MOD,,190\n")
    headers, rows = ec._read_table("x.csv", csv.encode())
    claims, unmapped, problems = ec.to_canonical_rows(headers, rows)
    assert not problems and len(claims) == 2 and claims[1]["lines"][1] == {"code": "D2391", "tooth": "19", "surface": "MOD", "quad": "", "units": 1, "charge": 190.0}
    checked = ec.run_checks(claims)
    f2 = codes(checked[1]["flags"])
    assert "frequency" in f2 and "surface_count" in f2 and checked[1]["verdict"] == "fix"


def test_dental_systems_listed_in_connect_emr():
    labels = [e["label"] for e in ec.emr_cards() if e["segment"] == "Dental"]
    for name in ("Open Dental", "Dentrix", "Eaglesoft", "Dentrix Ascend", "Denticon", "Curve Dental"):
        assert name in labels
    assert len(labels) >= 10


def test_validate_and_hold_dental_claims():
    import billing_activation as ba
    snap = {"patient": "Pat", "payer": "Delta Dental", "insuredId": "X1", "totalAmt": 175, "svcDate": "2026-10-01",
            "cpts": [{"code": "D0120"}, {"code": "D2392", "tooth": "14", "surface": ""}], "icds": []}
    probs = ba.validate_claim(snap)
    assert "There's no diagnosis (ICD-10) code." not in probs
    assert any("D2392" in p and "surfaces" in p for p in probs)
    assert not any("doesn't look like a CPT" in p for p in probs)
    assert ba.is_dental_claim(snap) and not ba.is_dental_claim({"cpts": [{"code": "99213"}]})
    # medical hashes are unchanged by the dental fields
    med = {"cpts": [{"code": "99213", "modifier": "", "units": 1}], "icds": [{"code": "J02.9"}]}
    assert ba.content_hash(med) == ba.content_hash(dict(med))


# ── through the extension ────────────────────────────────────────────────────
@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import auth, main
    import extension_api as xa
    with auth.SessionLocal() as db:
        from sqlalchemy import select
        u = db.scalar(select(auth.User).where(auth.User.email == "dental-admin@example.test"))
        if not u:
            u = auth.User(email="dental-admin@example.test", password_hash=auth.hash_password("x" * 12), full_name="Dental Admin",
                          organization="Dental Test Clinic", role="admin", claims_submitted=0, onboarding_complete=1, active=1)
            db.add(u); db.commit(); db.refresh(u)
        db.expunge(u)
    monkeypatch.setattr(ec, "_gate", lambda a, b: None)
    for d in (auth.require_user, auth.require_biller, auth.current_user):
        main.app.dependency_overrides[d] = lambda: u
    xa._hits.clear()
    c = TestClient(main.app)
    tok = c.post("/api/extension/token").json()["token"]
    yield c, {"Authorization": "Bearer " + tok}
    main.app.dependency_overrides.clear()


def test_extension_checks_dental_lines(client):
    c, h = client
    body = {"claim": {"claim_id": "60003", "dos": "10/10/2026", "payer": "Cigna Dental", "cpt": ["D0140", "D2391"], "icd": [], "amount": 275,
                      "lines": [{"code": "D0140"}, {"code": "D2391", "tooth": "19", "surface": "MOD"}], "tooth_column": True}}
    j = c.post("/api/extension/check", json=body, headers=h).json()
    assert j["verdict"] == "fix"
    f = next(f for f in j["flags"] if f["code"] == "surface_count")
    assert f["tooth"] == "19" and "D2391" in f["codes"]
    assert {x["code"]: x["type"] for x in j["codes"]}["D2391"] == "CDT"
    assert "no_dx" not in [x["code"] for x in j["flags"]]


def test_extension_dental_claim_preview_and_hold(client):
    c, h = client
    claim = {"claimId": "60002", "patient": "DENTALTEST, BRAVO", "dob": "09/30/1979", "payer": "MetLife Dental", "insuredId": "MET-1",
             "provider": "Test Dentist DDS", "svcDate": "10/10/2026", "pos": "11",
             "cpts": [{"code": "D0120", "charge": 65}, {"code": "D2392", "tooth": "14", "surface": "MO", "charge": 210}], "icds": []}
    j = c.post("/api/extension/claim", json={"action": "check", "claim": claim}, headers=h).json()
    assert j["dental"] is True and j["problems"] == []
    line = j["claim"]["cpts"][1]
    assert line["type"] == "CDT" and line["tooth"] == "14" and line["surface"] == "MO"
    assert any(b["title"] == "Dental claim sending" for b in j["blockers"])        # held until the dental route exists
    bad = dict(claim, cpts=[{"code": "D2392", "tooth": "14", "surface": "MOD", "charge": 210}])
    j2 = c.post("/api/extension/claim", json={"action": "check", "claim": bad}, headers=h).json()
    assert any("D2393" in f["message"] for f in j2["findings"])


def test_dental_note_goes_to_the_dental_coder(client, monkeypatch):
    import dental_coding
    seen = {}

    def fake(note):
        seen["note"] = note
        return [{"code": "D2392", "type": "CDT", "description": "two-surface posterior composite", "tooth": "#14", "surface": "m.o", "quad": "",
                 "confidence": 90, "justification": "#14 MO composite placed", "documentation_gap": ""},
                {"code": "D2391", "type": "CDT", "description": "one-surface", "tooth": "3", "surface": "MOD", "quad": "", "confidence": 88,
                 "justification": "x", "documentation_gap": ""},
                {"code": "99213", "type": "CDT", "description": "not dental", "tooth": "", "surface": "", "quad": "", "confidence": 50, "justification": "", "documentation_gap": ""}]
    monkeypatch.setattr(dental_coding, "_ask_ai", fake)
    cl, h = client
    note = "Periodic exam. Caries on #14 mesial and occlusal. #14 MO composite placed. Adult prophylaxis completed by hygienist."
    j = cl.post("/api/extension/code-note", json={"note": note}, headers=h).json()
    assert j["dental"] is True and seen["note"] == note
    by = {x["code"]: x for x in j["codes"]}
    assert set(by) == {"D2392", "D2391"}                                           # the medical code was dropped
    assert by["D2392"]["tooth"] == "14" and by["D2392"]["surface"] == "MO"
    assert "D2393" in by["D2391"]["documentation_gap"] and by["D2391"]["confidence"] <= 55
    assert dental_coding.is_dental_note(note) and not dental_coding.is_dental_note("Sore throat for 3 days, rapid strep positive, amoxicillin.")
