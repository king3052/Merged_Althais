"""CMS edit tables (NCCI PTP, MUE), age/sex edits, the payer history in claim building, and the coding test."""
import datetime as dt
import io
import json
import zipfile

import pytest


def _zip(name, text):
    b = io.BytesIO()
    with zipfile.ZipFile(b, "w") as z:
        z.writestr(name, text)
    return b.getvalue()


@pytest.fixture
def cms():
    import cms_edits
    from auth import SessionLocal
    yield cms_edits
    db = SessionLocal()
    db.query(cms_edits.CmsPtp).delete()
    db.query(cms_edits.CmsMue).delete()
    from auth import OrgSettings
    db.query(OrgSettings).filter(OrgSettings.org_key == cms_edits.META_ORG).delete()
    db.commit()
    db.close()
    cms_edits._cache.clear()
    cms_edits._status.clear()


def _ptp_file(extra=""):
    head = "CPT only copyright 2025 American Medical Association.\nColumn 1\tColumn 2\t*=in existence prior to 1996\tEffective Date\tDeletion Date\tModifier\tPTP Edit Rationale\n"
    filler = "".join(f"{10000 + i:05d}\t{20000 + i:05d}\t\t20200101\t*\t1\tMisuse of column two code\n" for i in range(1200))
    return head + filler + extra


def test_ptp_table_replaces_the_built_in_pairs(cms):
    from auth import SessionLocal
    import ncci_checker
    extra = ("12001\t99213\t\t20190101\t*\t1\tStandard preparation\n"
             "36415\t96372\t*\t19960101\t20240101\t0\tDeleted long ago\n"
             "96372\t99213\t\t20250101\t20991231\t1\tCurrent\n")
    db = SessionLocal()
    info = cms.load(db, "ptp", [("f1.zip", _zip("ccipra-v321r0-f1.txt", _ptp_file(extra)))], today=dt.date(2026, 11, 16))
    db.close()
    assert info["rows"] == 1203 - 1           # the edit deleted in 2024 is past the window and isn't kept
    assert cms.loaded("ptp")
    out = ncci_checker.check_claim_ncci(["99213", "12001"], line_mods={"99213": []}, dos="2026-11-10")
    assert len(out) == 1 and out[0]["col1"] == "12001" and out[0]["modifier_allowed"] and not out[0]["bypassed"]
    out = ncci_checker.check_claim_ncci(["99213", "12001"], line_mods={"99213": ["25"]}, dos="2026-11-10")
    assert out[0]["bypassed"] == "25"
    # the date of service decides: before the edit began, no conflict
    assert ncci_checker.check_claim_ncci(["99213", "96372"], dos="2024-06-01") == []
    assert ncci_checker.check_claim_ncci(["99213", "96372"], dos="2026-06-01")


def test_a_wrong_file_never_empties_a_table(cms):
    from auth import SessionLocal
    db = SessionLocal()
    cms.load(db, "ptp", [("f1.txt", _ptp_file().encode())])
    with pytest.raises(ValueError):
        cms.load(db, "ptp", [("readme.txt", b"nothing useful here")])
    db.close()
    db = SessionLocal()
    assert db.query(cms.CmsPtp).count() == 1200
    db.close()


def test_mue_limits_flag_too_many_units(cms):
    from auth import SessionLocal
    rows = ['"HCPCS/CPT Code","Practitioner Services MUE Values","MUE Adjudication Indicator","MUE Rationale"']
    rows += [f'"{30000 + i}","1","3 Date of Service Edit: Clinical","Clinical: Data"' for i in range(150)]
    rows += ['"J2405","8","3 Date of Service Edit: Clinical","Clinical: Data"', '"96372","4","3 Date of Service Edit: Clinical","Clinical: Data"',
             '"12001","1","2 Date of Service Edit: Policy","Anatomic Consideration"', '"J1885","8","1 Line Edit","Prescribing Information"']
    db = SessionLocal()
    info = cms.load(db, "mue", [("mue.zip", _zip("MCR_MUE_PractitionerServices_Eff_10-01-2026.csv", "\n".join(rows)))])
    db.close()
    assert info["rows"] == 154
    assert cms.mue_problems([{"code": "J2405", "units": 4}, {"code": "96372", "units": 1}]) == []
    p = cms.mue_problems([{"code": "J2405", "units": 6}, {"code": "J2405", "units": 4}, {"code": "12001", "units": 2}, {"code": "J1885", "units": 9}])
    assert len(p) == 3 and any(x.startswith("J2405: 10 units") for x in p) and any("no appeal" in x for x in p) and any("on one line" in x for x in p)


def test_claim_check_uses_mue_and_age_sex(cms, monkeypatch):
    import main
    from auth import SessionLocal
    rows = [f"{30000 + i},1,3 Date of Service Edit: Clinical,Clinical" for i in range(150)] + ["96372,4,3 Date of Service Edit: Clinical,Clinical"]
    db = SessionLocal()
    cms.load(db, "mue", [("mue.csv", "\n".join(rows).encode())])
    db.close()
    monkeypatch.setattr(main, "_fast_json", lambda *a, **k: {"score": 96, "flags": [], "missing": []})
    header = "patient X, DOB 1980-01-01, sex M, payer Aetna, member ID A1, rendering provider Morgan, date of service 2026-11-10"
    note = "SUBJECTIVE: Cough for three days.\nOBJECTIVE: Lungs clear, afebrile.\nASSESSMENT: Viral upper respiratory infection.\nPLAN: Fluids and rest, return if worse."
    r = main._claim_check(header, [{"code": "96372", "type": "CPT", "units": 6}, {"code": "J06.9", "type": "ICD-10"}], note)
    assert r["score"] == 81 and any("MUE" in f for f in r["flags"])
    r = main._claim_check(header, [{"code": "99213", "type": "CPT"}, {"code": "O21.0", "type": "ICD-10"}], note)
    assert r["score"] <= 40 and any("female-only" in f for f in r["flags"])


def test_age_and_sex_edits():
    import claim_edits
    h = "patient X, DOB 2015-05-01, sex F, payer Y, date of service 2026-11-10"
    assert claim_edits.patient_from_header(h) == (11, "F", dt.date(2026, 11, 10))
    assert [p["code"] for p in claim_edits.age_sex_problems([{"code": "O80", "type": "ICD-10"}, {"code": "N40.0", "type": "ICD-10"}], h)] == ["N40.0"]
    assert claim_edits.age_sex_problems([{"code": "P59.9", "type": "ICD-10"}], h)[0]["hard"]
    assert claim_edits.age_sex_problems([{"code": "O80", "type": "ICD-10"}], "DOB 1950-01-01, sex F, date of service 2026-11-10")
    assert claim_edits.age_sex_problems([{"code": "J06.9", "type": "ICD-10"}, {"code": "99213", "type": "CPT"}], h) == []
    assert claim_edits.age_sex_problems([{"code": "N40.0", "type": "ICD-10"}], "DOB not on file, sex not on file") == []


def test_claim_builder_gets_the_payers_denial_history(monkeypatch):
    """The clinic's repeat denials with this payer go into the coder's prompt, and money flags lower the check score."""
    import main
    import money_checks
    from auth import SessionLocal
    org = "History Clinic"
    db = SessionLocal()
    money_checks._store(db, org, {"payers": {"aetnappo": {"name": "Aetna Ppo", "rates": {"99213": {"rate": 92.0, "n": 5}},
                                                         "denials": {"12001|97|-": 3}}}})
    db.commit()
    db.close()
    prompts = []

    def fake(prompt, schema, name, **k):
        prompts.append((name, prompt))
        if name == "claim_build":
            return {"codes": [{"code": "99213", "type": "CPT", "modifier": ""}, {"code": "12001", "type": "CPT", "modifier": ""},
                              {"code": "S61.412A", "type": "ICD-10"}], "note_additions": [], "unresolved": []}
        return {"score": 95, "flags": [], "missing": []}
    monkeypatch.setattr(main, "_fast_json", fake)
    header = "patient X, DOB 1990-05-09, sex F, payer Aetna PPO, member ID A1, rendering provider Morgan, date of service 2026-11-10"
    note = "SUBJECTIVE: cut hand.\nOBJECTIVE: 2 cm laceration left palm.\nASSESSMENT: Laceration of left hand.\nPLAN: Simple repair."
    best = main._claim_auto_work(note, "", header, "office visit, established patient", [], 0, org)
    first_build = next(p for n, p in prompts if n == "claim_build")
    assert "denied 12001 with no modifier 3 times" in first_build
    assert best["payer_history"] and any(f["code"] == "money_payer_pattern" for f in best["check"].get("money", []))
    # without a clinic nothing changes
    prompts.clear()
    best = main._claim_auto_work(note, "", header, "office visit, established patient", [], 0, None)
    assert "denial history" not in next(p for n, p in prompts if n == "claim_build") and best["payer_history"] == []


def test_coding_test_scores_answers():
    import coding_eval
    case = {"id": "x", "title": "x", "expect": {"cpt": [["99213"], ["12001"]], "icd10": [["S61.412A"], ["N39.0", "N30.00"]], "modifiers": {"99213": ["25"]}}}
    right = [{"code": "99213", "type": "CPT", "modifier": "25"}, {"code": "12001", "type": "CPT"}, {"code": "S61.412A", "type": "ICD-10"}, {"code": "N30.00", "type": "ICD-10"}]
    s = coding_eval.score(case, right)
    assert s["correct"] and s["em"] == "best" and s["dx_hit"] == 2
    s = coding_eval.score(case, [{"code": "99214", "type": "CPT"}, {"code": "12001", "type": "CPT", "modifier": "59"}, {"code": "S61.412A", "type": "ICD-10"}])
    assert not s["correct"] and s["em"] == "wrong" and s["cpt_extra"] == ["99214"] and not s["mods_ok"] and s["stray_mods"] == ["12001-59"] and s["dx_hit"] == 1
    # a procedure-only visit must not bill an E/M
    s = coding_eval.score({"expect": {"cpt": [["10060"]], "icd10": [["L02.412"]], "modifiers": {}}},
                          [{"code": "10060", "type": "CPT"}, {"code": "99213", "type": "CPT"}, {"code": "L02.412", "type": "ICD-10"}])
    assert s["em"] == "wrong" and not s["correct"]
    out = coding_eval.run([dict(case, id="a"), dict(case, id="b")], lambda c: {"codes": right, "check": {"score": 93}, "ms": 1200})
    assert out["summary"]["fully_correct"] == 100 and out["summary"]["avg_seconds"] == 1.2


def test_the_case_file_is_well_formed():
    import coding_eval
    import icd10
    data = coding_eval.load_cases()
    assert len(data["cases"]) >= 30 and "DRAFT" in data["label_status"]
    ids = [c["id"] for c in data["cases"]]
    assert len(ids) == len(set(ids))
    for c in data["cases"]:
        assert c["expect"]["cpt"] and c["expect"]["icd10"]
        for slot in c["expect"]["icd10"]:
            for code in slot:
                assert not icd10.problems([{"code": code, "type": "ICD-10"}]), (c["id"], code)


def test_quality_admin_needs_an_admin(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    import coding_eval
    c = TestClient(main.app)
    assert c.get("/admin/api/cms").status_code == 401
    assert c.post("/admin/api/coding-eval", json={}).status_code == 401
    assert c.get("/admin/quality", follow_redirects=False).status_code == 302
    import quality_admin
    import auth
    main.app.dependency_overrides[auth.require_admin] = lambda: True
    try:
        monkeypatch.setattr(quality_admin, "_build", lambda case: {"codes": [], "check": {"score": 50}, "ms": 5})
        assert c.post("/admin/api/coding-eval", json={"note": "unit test"}).json() == {"ok": True}
        import time
        for _ in range(100):
            if coding_eval.job().get("state") != "running":
                break
            time.sleep(0.05)
        d = c.get("/admin/api/coding-eval").json()
        assert d["runs"][0]["note"] == "unit test" and d["runs"][0]["summary"]["cases"] == d["cases"]
        assert c.get("/admin/api/cms").json()["ptp"] == {}
    finally:
        main.app.dependency_overrides.clear()
