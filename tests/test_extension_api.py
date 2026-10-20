"""Browser extension API: pairing token, check, revoke, and that nothing patient-identifying is needed."""
import json
import pytest

import emr_connect as ec
import extension_api as xa


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import auth
    import main
    user = auth.User(id=1, email="t@example.test", full_name="Test User", organization="Test Clinic Ext", role="admin",
                     claims_submitted=0, onboarding_complete=1, active=1)
    monkeypatch.setattr(ec, "_gate", lambda u, d: None)
    main.app.dependency_overrides[auth.require_user] = lambda: user
    main.app.dependency_overrides[auth.require_biller] = lambda: user
    main.app.dependency_overrides[auth.current_user] = lambda: user
    xa._hits.clear()
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def _hdr(tok): return {"Authorization": f"Bearer {tok}"}


def test_token_check_revoke(client):
    assert client.post("/api/extension/check", json={"claim": {"cpt": ["99284"]}}).status_code == 401
    assert client.get("/api/extension/ping", headers=_hdr("1.nope")).status_code == 401
    tok = client.post("/api/extension/token").json()["token"]
    assert client.get("/api/extension/ping", headers=_hdr(tok)).json()["ok"]
    good = {"claim": {"claim_id": "X1", "dos": "10/10/2026", "payer": "Aetna", "cpt": ["99284"], "icd": ["R07.9"], "modifiers": [], "amount": "$450.00"}}
    r = client.post("/api/extension/check", json=good, headers=_hdr(tok))
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["verdict"] in ("clean", "review", "fix") and "no_patient" not in {f["code"] for f in j["flags"]} and j["echo"]["amount"] == 450
    bad = {"claim": {"dos": "10/10/2026", "payer": "Aetna", "cpt": ["99284"], "icd": [], "amount": 0}}
    codes = {f["code"] for f in client.post("/api/extension/check", json=bad, headers=_hdr(tok)).json()["flags"]}
    assert codes                                                                    # missing dx / zero charge shows up
    assert client.post("/api/extension/check", json={"claim": {}}, headers=_hdr(tok)).status_code == 400
    assert client.post("/api/extension/check", json={"claim": {"cpt": ["<script>"]}}, headers=_hdr(tok)).status_code == 400   # junk codes dropped
    client.post("/api/extension/revoke")
    assert client.get("/api/extension/ping", headers=_hdr(tok)).status_code == 401


def test_token_cap_and_rate_limit(client):
    toks = [client.post("/api/extension/token").json()["token"] for _ in range(7)]
    assert client.get("/api/extension/ping", headers=_hdr(toks[0])).status_code == 401     # oldest dropped
    assert client.get("/api/extension/ping", headers=_hdr(toks[-1])).status_code == 200
    body = {"claim": {"cpt": ["99213"], "icd": ["J02.9"], "payer": "Aetna", "dos": "10/10/2026", "amount": 100}}
    codes = [client.post("/api/extension/check", json=body, headers=_hdr(toks[-1])).status_code for _ in range(62)]
    assert codes[0] == 200 and codes[-1] == 429


def test_lookup_by_claim_id(client, monkeypatch):
    import athenahealth as ah
    tok = client.post("/api/extension/token").json()["token"]
    body = {"claim_id": "28368"}
    assert client.post("/api/extension/lookup", json=body).status_code == 401
    assert client.post("/api/extension/lookup", json={"claim_id": "x; drop"}, headers=_hdr(tok)).status_code == 400
    monkeypatch.delenv("ATHENA_CLIENT_ID", raising=False)
    assert client.post("/api/extension/lookup", json=body, headers=_hdr(tok)).status_code == 409          # athenahealth not connected
    monkeypatch.setenv("ATHENA_CLIENT_ID", "cid"); monkeypatch.setenv("ATHENA_CLIENT_SECRET", "sec")
    monkeypatch.setattr(ah, "_load", lambda db, org: (None, {"practice_id": "195900"}))

    class R:
        status_code = 200
        def __init__(s, b): s._b = b
        def json(s): return s._b
    def fake_get(url, params=None, headers=None, **kw):
        if url.endswith("/claims/28368"):
            return R({"claimid": "28368", "patientid": 134, "billedservicedate": "10/12/2026", "procedures": [{"procedurecode": "99214", "chargeamount": "60"}],
                      "diagnoses": [{"diagnosisrawcode": "Z00.00"}], "primaryinsurancepayer": {"primarypatientinsuranceid": "9"}})
        if "/insurances" in url:
            return R([{"insuranceid": "9", "insuranceplanname": "Aetna PPO"}])
        return type("X", (), {"status_code": 404, "json": lambda s: {}})()
    monkeypatch.setattr(ah.requests, "get", fake_get)
    monkeypatch.setattr(ah, "get_token", lambda force=False: "AT")
    j = client.post("/api/extension/lookup", json={"claim_id": "AH-28368"}, headers=_hdr(tok)).json()
    assert j["fields"]["cpt"] == ["99214"] and j["fields"]["payer"] == "Aetna PPO" and j["verdict"] == "clean"
    assert "patient" not in str(j).lower() and "134" not in str(j["fields"])
    assert client.post("/api/extension/lookup", json={"claim_id": "999"}, headers=_hdr(tok)).status_code == 400   # fake 404 -> AthenaError


# ---- code a note, and create / approve a claim, from the extension ----
@pytest.fixture()
def real_user(monkeypatch):
    """A user that exists in the database, because these actions act as the person who connected the browser."""
    from fastapi.testclient import TestClient
    import auth
    import main
    with auth.SessionLocal() as db:
        from sqlalchemy import select
        u = db.scalar(select(auth.User).where(auth.User.email == "ext-biller@example.test"))
        if not u:
            u = auth.User(email="ext-biller@example.test", password_hash=auth.hash_password("x" * 12), full_name="Ext Biller",
                          organization="Ext Claim Clinic", role="admin", claims_submitted=0, onboarding_complete=1, active=1)
            db.add(u); db.commit(); db.refresh(u)
        uid = u.id
    with auth.SessionLocal() as db:
        user = db.get(auth.User, uid)
        db.expunge(user)
    monkeypatch.setattr(ec, "_gate", lambda u, d: None)
    for d in (auth.require_user, auth.require_biller, auth.current_user):
        main.app.dependency_overrides[d] = lambda: user
    xa._hits.clear()
    yield TestClient(main.app), user
    main.app.dependency_overrides.clear()


def test_code_note_acts_as_the_token_owner(real_user, monkeypatch):
    import main
    from fastapi.responses import JSONResponse
    client, user = real_user
    seen = {}
    def fake(payload, user=None, db=None):
        seen.update(note=payload["note"], uid=user.id)
        return JSONResponse({"codes": [{"code": "J02.9", "type": "ICD-10", "description": "Pharyngitis", "confidence": 90, "justification": "sore throat", "extra": "x"},
                                       {"code": "99213", "type": "CPT", "confidence": 85}]})
    monkeypatch.setattr(main, "code_note", fake)
    tok = client.post("/api/extension/token").json()["token"]
    assert client.post("/api/extension/code-note", json={"note": "too short"}, headers=_hdr(tok)).status_code == 400
    note = "S: 3 days of sore throat, no cough. O: tonsillar exudate, tender anterior nodes. A: acute pharyngitis. P: supportive care."
    j = client.post("/api/extension/code-note", json={"note": note}, headers=_hdr(tok)).json()
    assert seen == {"note": note, "uid": user.id} and [c["code"] for c in j["codes"]] == ["J02.9", "99213"] and "extra" not in j["codes"][0]


def test_old_tokens_cant_act_as_a_person(real_user):
    client, user = real_user
    import auth
    tok = client.post("/api/extension/token").json()["token"]
    rid = int(tok.split(".")[0])
    with auth.SessionLocal() as db:                       # a token made before tokens remembered their owner
        row = db.get(auth.OrgSettings, rid); d = json.loads(row.data); d["tokens"][-1].pop("uid", None); row.data = json.dumps(d); db.commit()
    r = client.post("/api/extension/claim", json={"action": "check", "claim": {}}, headers=_hdr(tok))
    assert r.status_code == 409 and "Connect it again" in r.json()["error"]
    assert client.get("/api/extension/ping", headers=_hdr(tok)).status_code == 200     # checking claims still works


def test_claim_check_draft_and_queue(real_user):
    client, user = real_user
    import auth
    import billing_activation as ba
    tok = client.post("/api/extension/token").json()["token"]
    claim = {"patient": "Testpatient, Bravo", "dob": "06/02/1975", "sex": "F", "payer": "Cigna", "insuredId": "CIG123456",
             "provider": "Test Provider MD", "svcDate": "10/14/2026", "pos": "20",
             "cpts": [{"code": "99213", "units": 1, "charge": "$120.00"}], "icds": []}
    j = client.post("/api/extension/claim", json={"action": "check", "claim": claim}, headers=_hdr(tok)).json()
    assert any("diagnosis" in p for p in j["problems"]) and j["claim"]["totalAmt"] == 120 and j["claim"]["svcDate"] == "2026-10-14"
    r = client.post("/api/extension/claim", json={"action": "queue", "claim": claim}, headers=_hdr(tok))
    assert r.status_code == 400                                                     # can't approve a claim with problems
    claim["icds"] = ["J02.9"]
    d = client.post("/api/extension/claim", json={"action": "draft", "claim": claim}, headers=_hdr(tok)).json()
    assert d["status"] == "draft" and d["claim"]["claimId"].startswith("EXT-")
    claim["claimId"] = d["claim"]["claimId"]
    q = client.post("/api/extension/claim", json={"action": "queue", "claim": claim}, headers=_hdr(tok)).json()
    assert q["status"] == "queued" and q["submitAt"]
    with auth.SessionLocal() as db:
        from sqlalchemy import select
        oc = db.scalar(select(auth.OrgClaim).where(auth.OrgClaim.claim_id == claim["claimId"]))
        tx = db.scalar(select(ba.ClaimTransmission).where(ba.ClaimTransmission.claim_id == claim["claimId"]))
        assert oc.status == "Queued" and oc.source == "extension" and oc.payer == "Cigna"
        assert tx.delivery == "QUEUED" and json.loads(tx.snapshot)["insuredId"] == "CIG123456"
