"""NCCI bundling respects the modifier that answers it, and claims keep the place of service the EMR gave them."""
import json

import emr_connect as ec
import ncci_checker as n
from tests.test_extension_api import _hdr, client, real_user  # noqa: F401  (fixtures)

PAIR = ["96372", "99284"]          # injection + ER visit: an NCCI pair that a modifier can answer


def _pair_in_table():
    return any(c["modifier_allowed"] for c in n.check_claim_ncci(PAIR))


def test_modifier_on_the_visit_answers_the_pair():
    if not _pair_in_table():
        import pytest
        pytest.skip("this NCCI table doesn't carry 96372/99284")
    plain = n.check_claim_ncci(PAIR)
    assert plain and not plain[0]["bypassed"] and "Possible NCCI bundling" in plain[0]["message"]
    on_visit = n.check_claim_ncci(PAIR, None, {"99284": ["25"]})
    assert on_visit[0]["bypassed"] == "25" and "on 99284" in on_visit[0]["message"]
    on_claim = n.check_claim_ncci(PAIR, ["25"])                       # only claim-level modifiers known
    assert on_claim[0]["bypassed"] == "25" and "right line" in on_claim[0]["message"]
    wrong_line = n.check_claim_ncci(PAIR, None, {"96372": ["25"]})    # 25 on the injection doesn't count
    assert not wrong_line[0]["bypassed"] and "belongs on the visit" in wrong_line[0]["message"]
    assert n.check_claim_ncci_flags(PAIR, None, {"99284": ["25"]}) == []
    assert n.check_claim_ncci(PAIR, None, {"96372": ["59"]})[0]["bypassed"] == "59"


def test_import_and_extension_checks_use_line_modifiers(client):
    if not _pair_in_table():
        import pytest
        pytest.skip("this NCCI table doesn't carry 96372/99284")
    base = {"claim_id": "N1", "patient_name": "Testpatient, Delta", "mrn": "M1", "dob": "1968-03-15", "dos": "2026-10-20", "payer": "BCBS TX",
            "cpt": PAIR, "icd": ["M54.50"], "modifiers": ["25"], "amount": 510.0, "provider": "Test MD", "pos": "23", "rows": []}
    flags = ec.claim_check(dict(base, line_mods=[{"code": "99284", "mod": "25"}]))
    assert not [f for f in flags if f["code"] == "ncci"] and [f for f in flags if f["code"] == "ncci_mod" and f["severity"] == "info"]
    flags = ec.claim_check(dict(base, modifiers=[]))
    assert [f for f in flags if f["code"] == "ncci" and f["severity"] == "warn"]
    tok = client.post("/api/extension/token").json()["token"]
    claim = {"claim_id": "N2", "dos": "10/20/2026", "payer": "BCBS TX", "cpt": PAIR, "icd": ["M54.50"], "modifiers": ["25"], "amount": 510,
             "line_mods": [{"code": "99284", "mod": "25"}]}
    j = client.post("/api/extension/check", json={"claim": claim}, headers=_hdr(tok)).json()
    codes = {f["code"] for f in j["flags"]}
    assert "ncci" not in codes and "ncci_mod" in codes


def test_place_of_service_is_kept(real_user):
    c, user = real_user
    tok = c.post("/api/extension/token").json()["token"]
    claim = {"patient": "Testpatient, Charlie", "dob": "11/11/1990", "sex": "M", "payer": "UnitedHealthcare", "insuredId": "UHC555000111",
             "provider": "Test Provider MD", "svcDate": "10/23/2026", "pos": "23",
             "cpts": [{"code": "99283", "units": 1, "charge": "$280.00"}], "icds": ["R07.89"]}
    d = c.post("/api/extension/claim", json={"action": "draft", "claim": claim}, headers=_hdr(tok)).json()
    got = next(x for x in c.get("/api/claims").json() if x["id"] == d["claim"]["claimId"])
    assert got["pos"] == "23" and got["codes"][0].get("charge") == 280
    # the app saving the claim back keeps it, and a blank or junk value isn't stored as a code
    assert c.post("/api/claims", json={"id": got["id"], "pos": "20"}).json()["ok"]
    assert next(x for x in c.get("/api/claims").json() if x["id"] == got["id"])["pos"] == "20"
    c.post("/api/claims", json={"id": "POS-BLANK", "pos": "abc"})
    assert next(x for x in c.get("/api/claims").json() if x["id"] == "POS-BLANK")["pos"] == ""
    assert ec._pos2("23 - Emergency room") == "23" and ec._pos2("2") == "02" and ec._pos2("") == ""
