"""athenahealth connector: sign-in, practice connect, pull, and the record reader, with athenahealth itself faked."""
import base64
import datetime as dt

import pytest

import emr_connect as ec
import athenahealth as ah

TODAY = dt.date(2026, 10, 12)


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import auth
    import main
    user = auth.User(id=1, email="t@example.test", full_name="Test User", organization="Test Clinic Athena", role="admin",
                     claims_submitted=0, onboarding_complete=1, active=1)
    monkeypatch.setattr(ec, "_gate", lambda u, d: None)
    main.app.dependency_overrides[auth.require_user] = lambda: user
    main.app.dependency_overrides[auth.require_biller] = lambda: user
    main.app.dependency_overrides[auth.current_user] = lambda: user
    ah._token.update(value="", expires_at=0.0, env="")
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


class Resp:
    def __init__(self, code, body): self.status_code, self._b = code, body
    def json(self): return self._b


def test_reader_tolerates_field_names():
    claim = {"claimid": "501", "patientid": 9, "billedservicedate": "10/10/2026", "chargeamount": "450.00", "supervisingprovidername": "Ann Smith",
             "diagnoses": [{"diagnosiscode": "R07.9"}, {"icd10code": "R06.00"}],
             "procedures": [{"procedurecode": "99284", "modifier1": "25"}, {"cptcode": "93005"}],
             "primaryinsurancepayer": {"payername": "Aetna"}}
    c = ah.to_canonical(claim, {"firstname": "Jane", "lastname": "Doe", "dob": "01/31/1980"})
    assert c["claim_id"] == "AH-501" and c["patient_name"] == "Jane Doe" and c["dos"] == "2026-10-10" and c["dob"] == "1980-01-31"
    assert c["payer"] == "Aetna" and c["cpt"] == ["99284", "93005"] and c["icd"] == ["R07.9", "R06.00"] and c["modifiers"] == ["25"] and c["amount"] == 450
    assert ec.verdict_for(ec.claim_check(c, today=TODAY)) == "clean"


def test_reader_matches_real_sandbox_shape():
    claim = {"claimid": "28368", "patientid": 134, "billedservicedate": "10/12/2026", "billedproviderid": 23, "status": "CLOSED",
             "transactiondetails": {"39404": "60"},
             "procedures": [{"procedurecode": "99214", "chargeamount": "60", "proceduredescription": "OFFICE OUTPATIENT VISIT 25 MINUTES", "transactionid": "39404"}],
             "diagnoses": [{"deletediagnosis": "false", "diagnosisid": "32616", "diagnosiscodeset": "ICD10", "diagnosisrawcode": "Z00.00"}],
             "primaryinsurancepayer": {"status": "HOLD", "primaryinsurancepackageid": "74", "primarypatientinsuranceid": "28986", "balance": "60"}}
    pat = [{"firstname": "Wbpatienttest9640", "lastname": "Smith", "dob": "10/23/1992"}]
    c = ah.to_canonical(claim, pat[0])
    assert c["icd"] == ["Z00.00"] and c["cpt"] == ["99214"] and c["amount"] == 60 and c["dos"] == "2026-10-12" and c["payer"] == "Insurance package 74"
    assert ah.to_canonical(claim, pat[0], {"28986": "Aetna PPO"})["payer"] == "Aetna PPO"      # a real name wins when the insurance list can be read
    assert ec.verdict_for(ec.claim_check(c, today=TODAY)) == "clean"


def test_reader_survives_missing_and_dict_shaped_fields():
    c = ah.to_canonical({"claimid": "7", "patientid": 3, "diagnoses": {"1": {"diagnosiscode": "J02.9"}}, "procedures": {"1": {"procedurecode": "99213"}}})
    assert c["icd"] == ["J02.9"] and c["cpt"] == ["99213"] and c["patient_name"] == "Patient 3" and c["payer"] == "" and c["dos"] == ""
    assert ah.to_canonical({"claimid": "8"})["cpt"] == []          # nothing readable: shows up as a failed check, never a crash


def test_off_until_configured(client, monkeypatch):
    monkeypatch.delenv("ATHENA_CLIENT_ID", raising=False)
    cards = {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}
    assert cards["athenahealth"]["live"] is False and "aren't set" in cards["athenahealth"]["live_state"] and cards["athenahealth"]["actions"] == []
    assert client.post("/api/integrations/athenahealth/connect", json={"practice_id": "1"}).status_code == 400


def test_connect_sync_disconnect(client, monkeypatch):
    monkeypatch.setenv("ATHENA_CLIENT_ID", "cid"); monkeypatch.setenv("ATHENA_CLIENT_SECRET", "sec"); monkeypatch.delenv("ATHENA_ENV", raising=False)
    posted = {}

    def fake_post(url, data=None, headers=None, **kw):
        posted.update(url=url, data=data, auth=headers["Authorization"])
        return Resp(200, {"access_token": "AT", "expires_in": "300"})

    today = dt.datetime.now(dt.timezone.utc).date().strftime("%m/%d/%Y")
    seen = []

    def fake_get(url, params=None, headers=None, **kw):
        assert headers["Authorization"] == "Bearer AT"
        seen.append(url)
        if url.endswith("/ping"):
            return Resp(200, {"ping": "pong"})
        if url.endswith("/departments"):
            return Resp(200, {"departments": [{"departmentid": "1"}, {"departmentid": "2"}]})
        if url.endswith("/claims"):
            assert params["servicestartdate"] and params["serviceenddate"] == today
            if params["departmentid"] != "1":
                return Resp(200, {"claims": [], "totalcount": 0, "next": None})
            return Resp(200, {"claims": [{"claimid": "11", "patientid": 5}], "totalcount": 1, "next": None})
        if url.endswith("/claims/11"):
            return Resp(200, {"claimid": "11", "patientid": 5, "billedservicedate": today, "chargeamount": "400", "diagnoses": [{"diagnosiscode": "R07.9"}],
                              "procedures": [{"procedurecode": "99284"}], "primaryinsurancepayer": {"payername": "Aetna"}})
        if "/patients/5" in url:
            return Resp(200, [{"firstname": "Jane", "lastname": "Doe", "dob": "01/31/1980"}])
        return Resp(404, {})

    monkeypatch.setattr(ah.requests, "post", fake_post)
    monkeypatch.setattr(ah.requests, "get", fake_get)

    assert client.post("/api/integrations/athenahealth/connect", json={"practice_id": "abc"}).status_code == 400
    ok = client.post("/api/integrations/athenahealth/connect", json={"practice_id": "195900"})
    assert ok.status_code == 200, ok.text
    assert posted["url"] == "https://api.preview.platform.athenahealth.com/oauth2/v1/token"
    assert posted["data"] == {"grant_type": "client_credentials", "scope": "athena/service/Athenanet.MDP.*"}
    assert posted["auth"] == "Basic " + base64.b64encode(b"cid:sec").decode()
    cards = {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}
    assert cards["athenahealth"]["live"] is True and {a["kind"] for a in cards["athenahealth"]["actions"]} == {"sync", "disconnect"}

    s = client.post("/api/integrations/athenahealth/sync", json={"days": 7})
    assert s.status_code == 200, s.text
    j = s.json()
    assert j["summary"]["total"] == 1 and j["claims"][0]["claim_id"] == "AH-11" and j["claims"][0]["patient_name"] == "Jane Doe" and j["claims"][0]["verdict"] == "clean"
    assert all("/v1/195900/" in u for u in seen)
    smp = client.get("/api/integrations/athenahealth/sample")
    assert smp.status_code == 200 and smp.json()["list_row"]["claimid"] == "11" and smp.json()["detail"]["claimid"] == "11"
    monkeypatch.setenv("ATHENA_ENV", "production")
    assert client.get("/api/integrations/athenahealth/sample").status_code == 403         # never shows raw records outside the test environment
    monkeypatch.delenv("ATHENA_ENV")
    assert all(c["id"] != "AH-11" for c in client.get("/api/integrations/worklist").json()["claims"])     # a sync only previews
    client.post("/api/integrations/import/commit", json={"emr": "athenahealth", "claims": j["claims"]})
    assert any(c["id"] == "AH-11" for c in client.get("/api/integrations/worklist").json()["claims"])
    assert client.post("/api/integrations/athenahealth/disconnect").json()["ok"]
    assert {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}["athenahealth"]["live"] is False


def test_error_reason_is_shown():
    r = Resp(400, {"error": "The departmentid is required."})
    assert "departmentid is required" in ah._why(r)
    assert ah._why(Resp(500, ["x"])) == ""


def test_bad_credentials_and_production_url(client, monkeypatch):
    monkeypatch.setenv("ATHENA_CLIENT_ID", "cid"); monkeypatch.setenv("ATHENA_CLIENT_SECRET", "bad")
    monkeypatch.setattr(ah.requests, "post", lambda *a, **k: Resp(401, {"error": "invalid_client"}))
    r = client.post("/api/integrations/athenahealth/connect", json={"practice_id": "195900"})
    assert r.status_code == 400 and "credentials" in r.json()["error"]
    monkeypatch.setenv("ATHENA_ENV", "production")
    assert ah.base_url() == "https://api.platform.athenahealth.com"


def test_unreadable_detail_does_not_fail_the_pull(monkeypatch):
    def fake_get(url, params=None, headers=None, **kw):
        if url.endswith("/departments"):
            return Resp(200, {"departments": [{"departmentid": "1"}]})
        if url.endswith("/claims"):
            return Resp(200, {"claims": [{"claimid": "5", "patientid": 2, "billedservicedate": "10/10/2026", "procedures": [{"procedurecode": "99213", "chargeamount": "90"}]}], "totalcount": 1, "next": None})
        return Resp(404, {})
    monkeypatch.setattr(ah.requests, "get", fake_get)
    monkeypatch.setattr(ah, "get_token", lambda force=False: "AT")
    claims, notes = ah.fetch_claims(ah.Client("1"), 7)
    assert claims[0]["claim_id"] == "AH-5" and claims[0]["icd"] == [] and any("couldn't be read for 1 claim" in n for n in notes)
