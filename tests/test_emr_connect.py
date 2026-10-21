"""Tests for emr_connect.py: the file reader, the pre-submission checks, and the import routes."""
import datetime as dt
import io

import emr_connect as ec

TODAY = dt.date(2026, 10, 17)


def table(csv_text, name="x.csv"):
    headers, rows = ec._read_table(name, csv_text.encode())
    return ec.to_canonical_rows(headers, rows)


def test_groups_line_items_into_one_claim():
    claims, unmapped, problems = table(
        "Claim #,Patient,DOS,Payer,CPT,Dx,Charge\n"
        "C1,Jane Doe,10/01/2026,Aetna,99284,R07.9,450\n"
        "C1,Jane Doe,10/01/2026,Aetna,93005,R07.9,85\n")
    assert not problems and len(claims) == 1
    c = claims[0]
    assert c["cpt"] == ["99284", "93005"] and c["icd"] == ["R07.9"] and c["amount"] == 535 and c["dos"] == "2026-10-01"


def test_splits_first_and_last_name_and_multiple_codes():
    claims, _, problems = table("First Name,Last Name,Date of Service,Insurance,CPT Codes,ICD-10,Charges\nJo,Lee,2026-10-02,BCBS,\"99283, 12001\",\"S61.409A;W26.0XXA\",$300.50\n")
    assert not problems
    c = claims[0]
    assert c["patient_name"] == "Jo Lee" and c["cpt"] == ["99283", "12001"] and c["icd"] == ["S61.409A", "W26.0XXA"] and c["amount"] == 300.5


def test_missing_required_columns_is_a_plain_error():
    claims, _, problems = table("Patient,Payer\nJane,Aetna\n")
    assert claims == [] and any("CPT" in p for p in problems)


def test_clean_claim_scores_100():
    c = {"claim_id": "A", "patient_name": "J", "dob": "1980-01-01", "dos": "2026-10-10", "payer": "Aetna", "cpt": ["99284"], "icd": ["R07.9"], "amount": 400.0}
    f = ec.claim_check(c, today=TODAY)
    assert ec.verdict_for(f) == "clean" and ec.score_for(f) == 100


def test_missing_dx_payer_and_bad_code_are_errors():
    c = {"claim_id": "B", "patient_name": "J", "dos": "2026-10-10", "payer": "", "cpt": ["9928"], "icd": [], "amount": 400.0}
    codes = {f["code"] for f in ec.claim_check(c, today=TODAY)}
    assert {"bad_cpt", "no_dx", "no_payer"} <= codes
    assert ec.verdict_for(ec.claim_check(c, today=TODAY)) == "fix"


def test_timely_filing_age():
    base = {"patient_name": "J", "payer": "Aetna", "cpt": ["99284"], "icd": ["R07.9"], "amount": 10.0}
    assert "aging" in {f["code"] for f in ec.claim_check({**base, "dos": "2026-07-01"}, today=TODAY)}
    assert "stale" in {f["code"] for f in ec.claim_check({**base, "dos": "2025-06-01"}, today=TODAY)}
    assert "future_dos" in {f["code"] for f in ec.claim_check({**base, "dos": "2026-12-01"}, today=TODAY)}


def test_ncci_bundling_flag():
    c = {"claim_id": "N", "patient_name": "J", "dos": "2026-10-10", "payer": "Aetna", "cpt": ["99291", "94760"], "icd": ["R07.9"], "amount": 10.0}
    assert "ncci" in {f["code"] for f in ec.claim_check(c, today=TODAY)}


def test_duplicate_in_file():
    seen = set()
    c = {"claim_id": "D", "patient_name": "J", "dos": "2026-10-10", "payer": "A", "cpt": ["99284"], "icd": ["R07.9"], "amount": 1.0}
    ec.claim_check(c, today=TODAY, seen_ids=seen)
    assert "dup_in_file" in {f["code"] for f in ec.claim_check(c, today=TODAY, seen_ids=seen)}


def test_no_emr_claims_to_be_live():
    # nothing is shown as a live connection until a connector class exists and is configured
    assert all(not e["live"] for e in ec.emr_cards())
    assert {e["key"] for e in ec.emr_cards()} >= {"experity", "drchrono", "athenahealth", "advancedmd"}


def test_template_round_trips():
    from fastapi.testclient import TestClient  # noqa: F401  (routes need a logged-in user; the file path is tested here)
    body = ("claim_id,patient_name,mrn,dob,date_of_service,payer,cpt,icd10,modifier,charge,provider\n"
            "SAMPLE-001,Sample Patient,MRN-0001,1980-01-31,2026-10-01,Sample Payer,99284,R07.9,25,450.00,Dr Example\n"
            "SAMPLE-001,Sample Patient,MRN-0001,1980-01-31,2026-10-01,Sample Payer,93005,R07.9,,85.00,Dr Example\n")
    claims, unmapped, problems = table(body)
    assert not problems and len(claims) == 1 and claims[0]["amount"] == 535 and claims[0]["modifiers"] == ["25"]
    assert unmapped == []


def test_xlsx_upload():
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active
    ws.append(["Patient", "Date of Service", "Payer", "CPT", "ICD-10", "Charge"])
    ws.append(["Jane Doe", dt.date(2026, 10, 1), "Aetna", "99284", "R07.9", 450])
    buf = io.BytesIO(); wb.save(buf)
    headers, rows = ec._read_table("a.xlsx", buf.getvalue())
    claims, _, problems = ec.to_canonical_rows(headers, rows)
    assert not problems and claims[0]["dos"] == "2026-10-01" and claims[0]["amount"] == 450


# ---- routes, with the login and plan checks stubbed so only this module's behavior is under test ----
import pytest


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import auth
    import main
    user = auth.User(id=1, email="t@example.test", full_name="Test User", organization="Test Clinic EMR Import", role="admin",
                     claims_submitted=0, onboarding_complete=1, active=1)
    monkeypatch.setattr(ec, "_gate", lambda u, d: None)
    main.app.dependency_overrides[auth.require_user] = lambda: user
    main.app.dependency_overrides[auth.require_biller] = lambda: user
    main.app.dependency_overrides[auth.current_user] = lambda: user
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


CSV = ("Claim #,Patient,DOS,Payer,CPT,Dx,Charge\n"
       "T1,Jane Doe,2026-10-10,Aetna,99284,R07.9,450\n"
       "T2,John Roe,2026-10-10,,99284,,300\n")


def test_preview_commit_worklist_summary(client):
    r = client.post("/api/integrations/import/preview", files={"file": ("claims.csv", CSV, "text/csv")}, data={"emr": "drchrono"})
    assert r.status_code == 200, r.text
    p = r.json()
    assert p["summary"]["total"] == 2 and p["summary"]["clean"] == 1 and p["summary"]["fix"] == 1
    r = client.post("/api/integrations/import/commit", json={"emr": "drchrono", "claims": p["claims"]})
    assert r.status_code == 200 and r.json()["created"] == 2
    again = client.post("/api/integrations/import/commit", json={"emr": "drchrono", "claims": p["claims"]}).json()
    assert again["created"] == 0 and again["updated"] == 2          # importing twice updates, never duplicates
    w = client.get("/api/integrations/worklist").json()["claims"]
    assert {c["id"] for c in w} == {"T1", "T2"} and w[0]["id"] == "T2"    # worst first
    s = client.get("/api/integrations/summary").json()
    assert s["flagged"] == 1 and s["at_risk"] == 300


def test_commit_recomputes_scores(client):
    forged = [{"claim_id": "F1", "patient_name": "X", "payer": "", "cpt": ["99284"], "icd": [], "amount": 10, "score": 100, "verdict": "clean"}]
    client.post("/api/integrations/import/commit", json={"claims": forged})
    w = {c["id"]: c for c in client.get("/api/integrations/worklist").json()["claims"]}
    assert w["F1"]["status"] == "Draft" and w["F1"]["score"] < 100


def test_bad_upload_messages(client):
    assert client.post("/api/integrations/import/preview", files={"file": ("a.pdf", b"x", "application/pdf")}).status_code == 400
    r = client.post("/api/integrations/import/preview", files={"file": ("a.csv", "Patient,Payer\nJ,A\n", "text/csv")})
    assert r.status_code == 400 and "CPT" in r.json()["error"]


def test_page_and_emr_list(client):
    assert client.get("/api/integrations/emrs").json()["emrs"][0]["live"] is False
    r = client.get("/revenue/connect", headers={"cookie": "althais_ui=classic"})      # the old layout is still there for anyone who switches back
    assert r.status_code == 200 and "Connect your EMR" in r.text
    n = client.get("/v4/revenue/connect")                                            # the new EMR page
    assert n.status_code == 200 and 'data-page="connect"' in n.text and "app8.js" in n.text
    assert "/v4/revenue/connect" in open("static/v4/ui.js").read()                  # in the Revenue menu next to Billing activation


# ---- DrChrono: OAuth plumbing and the record reader, with DrChrono itself faked ----
def test_drchrono_reader_tolerates_field_names():
    import drchrono as dc
    appt = {"id": 77, "scheduled_time": "2026-10-10T09:30:00", "status": "Complete", "icd10_codes": ["R07.9", {"code": "R06.00"}]}
    items = [{"code": "99284", "price": "400.00", "quantity": 1, "modifier1": "25"}, {"code": "93005", "price": "50", "quantity": 1}]
    pat = {"id": 5, "first_name": "Jane", "last_name": "Doe", "date_of_birth": "1980-01-31", "chart_id": "MRN5", "primary_insurance": {"insurance_company": "Aetna"}}
    c = dc.to_canonical(appt, items, pat, {"first_name": "Ann", "last_name": "Smith"})
    assert c["claim_id"] == "DC-77" and c["patient_name"] == "Jane Doe" and c["dos"] == "2026-10-10" and c["payer"] == "Aetna"
    assert c["cpt"] == ["99284", "93005"] and c["icd"] == ["R07.9", "R06.00"] and c["modifiers"] == ["25"] and c["amount"] == 450 and c["provider"] == "Ann Smith"
    assert ec.verdict_for(ec.claim_check(c, today=TODAY)) == "clean"


def test_drchrono_off_until_configured(client, monkeypatch):
    monkeypatch.delenv("DRCHRONO_CLIENT_ID", raising=False)
    cards = {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}
    assert cards["drchrono"]["live"] is False and "aren't set" in cards["drchrono"]["live_state"] and cards["drchrono"]["actions"] == []
    assert client.get("/api/integrations/drchrono/connect", follow_redirects=False).status_code == 400


def test_drchrono_connect_callback_sync_disconnect(client, monkeypatch):
    import drchrono as dc
    monkeypatch.setenv("DRCHRONO_CLIENT_ID", "cid"); monkeypatch.setenv("DRCHRONO_CLIENT_SECRET", "sec")
    r = client.get("/api/integrations/drchrono/connect", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("https://app.drchrono.com/o/authorize/") and "client_id=cid" in r.headers["location"]
    state = client.cookies.get("drc_state")
    assert state and f"state={state}" in r.headers["location"]

    class Resp:
        def __init__(self, code, body): self.status_code, self._b = code, body
        def json(self): return self._b

    posted = {}
    def fake_post(url, data=None, **kw):
        posted.update(data); return Resp(200, {"access_token": "AT", "refresh_token": "RT", "expires_in": 172800})
    monkeypatch.setattr(dc.requests, "post", fake_post)
    bad = client.get("/api/integrations/drchrono/callback?code=abc&state=wrong", follow_redirects=False)
    assert "failed" in bad.headers["location"] and not posted                       # a wrong state never reaches DrChrono
    ok = client.get(f"/api/integrations/drchrono/callback?code=abc&state={state}", follow_redirects=False)
    assert "connected" in ok.headers["location"] and posted["grant_type"] == "authorization_code" and posted["code"] == "abc"
    cards = {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}
    assert cards["drchrono"]["live"] is True and {a["kind"] for a in cards["drchrono"]["actions"]} == {"sync", "disconnect"}

    today = dt.datetime.now(dt.timezone.utc).date().isoformat()
    def fake_get(url, params=None, headers=None, **kw):
        assert headers["Authorization"] == "Bearer AT"
        if url.endswith("/appointments"):
            return Resp(200, {"results": [{"id": 1, "patient": 5, "doctor": 9, "status": "Complete", "scheduled_time": today + "T09:00:00", "icd10_codes": ["R07.9"]}], "next": None})
        if url.endswith("/line_items"):
            return Resp(200, {"results": [{"code": "99284", "price": "400", "quantity": 1}], "next": None})
        if "/patients/" in url:
            return Resp(200, {"id": 5, "first_name": "Jane", "last_name": "Doe", "chart_id": "M5", "primary_insurance": {"insurance_company": "Aetna"}})
        if "/doctors/" in url:
            return Resp(200, {"first_name": "Ann", "last_name": "Smith"})
        return Resp(404, {})
    monkeypatch.setattr(dc.requests, "get", fake_get)
    s = client.post("/api/integrations/drchrono/sync", json={"days": 7})
    assert s.status_code == 200, s.text
    j = s.json()
    assert j["summary"]["total"] == 1 and j["claims"][0]["claim_id"] == "DC-1" and j["claims"][0]["verdict"] == "clean"
    # a sync only previews: nothing is in the worklist until the person saves it
    assert client.get("/api/integrations/worklist").json()["claims"] == [] or all(c["id"] != "DC-1" for c in client.get("/api/integrations/worklist").json()["claims"])
    client.post("/api/integrations/import/commit", json={"emr": "drchrono", "claims": j["claims"]})
    assert any(c["id"] == "DC-1" for c in client.get("/api/integrations/worklist").json()["claims"])
    assert client.post("/api/integrations/drchrono/disconnect").json()["ok"]
    assert client.get("/api/integrations/emrs").json()["emrs"][1]["live"] is False

