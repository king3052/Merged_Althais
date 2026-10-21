"""Per-clinic version: medical (ER, urgent care, office) or dental, set in /admin. A clinic sees only its own version:
the app's codes, claim form, rules and EMR list, the coder, the claim check, and which pages it lands on."""
import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import auth
import emr_connect as ec
import main


def _user(db, email, org, role="admin"):
    u = db.scalar(select(auth.User).where(auth.User.email == email))
    if not u:
        u = auth.User(email=email, password_hash=auth.hash_password("x" * 12), full_name="Test " + org, organization=org, role=role,
                      claims_submitted=0, onboarding_complete=1, active=1)
        db.add(u); db.commit(); db.refresh(u)
    return u


def _doc(db, org):
    row = db.scalar(select(auth.OrgSettings).where(auth.OrgSettings.org_key == org, auth.OrgSettings.category == auth.ENTITLEMENTS_CATEGORY))
    return json.loads(row.data) if row else None


@pytest.fixture()
def admin():
    main.app.dependency_overrides[auth.require_admin] = lambda: True
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


def as_user(email, org):
    with auth.SessionLocal() as db:
        u = _user(db, email, org)
        db.expunge(u)
    for d in (auth.require_user, auth.require_biller, auth.current_user):
        main.app.dependency_overrides[d] = lambda: u
    return u


def test_new_clinics_are_medical_and_admin_can_switch(admin):
    with auth.SessionLocal() as db:
        u = _user(db, "pr-a@example.test", "Practice Clinic A")
        assert auth.org_practice(u, db) == "medical"                           # nothing set: the medical version, as before
    r = admin.post("/api/admin/orgs/practice", json={"org_key": "Practice Clinic A", "practice": "dental"})
    assert r.status_code == 200 and r.json()["practice"] == "dental"
    with auth.SessionLocal() as db:
        u = _user(db, "pr-a@example.test", "Practice Clinic A")
        assert auth.org_practice(u, db) == "dental"
        doc = _doc(db, "Practice Clinic A")
        assert doc["products"] == ["suite"]                                    # its plan didn't change
    org = next(o for o in admin.get("/api/admin/orgs").json() if o["org_key"] == "Practice Clinic A")
    assert org["practice"] == "dental"
    assert admin.post("/api/admin/orgs/practice", json={"org_key": "Practice Clinic A", "practice": "vet"}).status_code == 400
    assert admin.post("/api/admin/orgs/practice", json={"org_key": "No Such Clinic", "practice": "dental"}).status_code == 404
    # saving the plan later keeps the version
    r = admin.post("/api/admin/orgs/products", json={"org_key": "Practice Clinic A", "products": ["coding", "insurance"], "althea": True})
    assert r.status_code == 200
    with auth.SessionLocal() as db:
        assert _doc(db, "Practice Clinic A")["practice"] == "dental"
    # and back
    assert admin.post("/api/admin/orgs/practice", json={"org_key": "Practice Clinic A", "practice": "medical"}).json()["practice"] == "medical"
    with auth.SessionLocal() as db:
        d = _doc(db, "Practice Clinic A")
        assert d["practice"] == "medical" and d["products"] == ["coding", "insurance"] and d["althea"] is True


def test_add_clinic_with_a_version(admin):
    r = admin.post("/api/admin/clinics", json={"name": "Smile Dental Test", "products": ["suite"], "practice": "dental"})
    assert r.status_code == 200
    org = next(o for o in admin.get("/api/admin/orgs").json() if o["org_key"] == "Smile Dental Test")
    assert org["practice"] == "dental" and org["users"] == []
    assert admin.post("/api/admin/clinics", json={"name": "Odd Clinic Test", "products": ["suite"], "practice": "x"}).status_code == 400
    r = admin.post("/api/admin/clinics", json={"name": "Plain ER Test", "products": ["suite"]})
    assert next(o for o in admin.get("/api/admin/orgs").json() if o["org_key"] == "Plain ER Test")["practice"] == "medical"
    csv = admin.get("/api/admin/export/orgs.csv")
    assert csv.status_code == 200 and "practice" in csv.text.splitlines()[0]


def test_the_app_knows_its_version_and_lists_only_its_systems(admin):
    with auth.SessionLocal() as db:
        _user(db, "pr-d@example.test", "Practice Clinic D")
        _user(db, "pr-m@example.test", "Practice Clinic M")
    assert admin.post("/api/admin/orgs/practice", json={"org_key": "Practice Clinic D", "practice": "dental"}).status_code == 200
    c = TestClient(main.app)
    try:
        u = as_user("pr-d@example.test", "Practice Clinic D")
        assert json.loads(main._app_user_json(u))["practice"] == "dental"
        j = c.get("/api/integrations/emrs").json()
        segs = {e["segment"] for e in j["emrs"] if e["key"] != "other"}
        assert j["practice"] == "dental" and segs == {"Dental"}
        assert next(e for e in j["emrs"] if e["key"] == "other")["label"] == "Other dental software"
        assert "tooth" in c.get("/api/integrations/template.csv").text.splitlines()[0]
        # the medical live connections are off for a dental clinic
        assert c.post("/api/integrations/athenahealth/sync", json={}).status_code == 403
        assert c.post("/api/integrations/tebra/connect", json={}).status_code == 403
        m = as_user("pr-m@example.test", "Practice Clinic M")
        assert json.loads(main._app_user_json(m))["practice"] == "medical"
        j = c.get("/api/integrations/emrs").json()
        assert "Dental" not in {e["segment"] for e in j["emrs"]} and any(e["key"] == "athenahealth" for e in j["emrs"])
        assert "cpt" in c.get("/api/integrations/template.csv").text.splitlines()[0]
    finally:
        main.app.dependency_overrides.clear()


def test_coding_and_claim_check_follow_the_version(monkeypatch):
    import dental_coding
    calls = []
    monkeypatch.setattr(dental_coding, "_ask_ai", lambda note: calls.append(note) or [
        {"code": "D2392", "type": "CDT", "description": "two-surface composite", "tooth": "14", "surface": "MO", "quad": "", "confidence": 90, "justification": "x", "documentation_gap": ""}])
    with auth.SessionLocal() as db:
        _user(db, "pr-d2@example.test", "Practice Clinic D2")
        from tests.test_dental import set_practice
        set_practice(db, "Practice Clinic D2", "dental")
    c = TestClient(main.app)
    try:
        as_user("pr-d2@example.test", "Practice Clinic D2")
        # a dental clinic's note goes to the dental coder even when it reads like a short medical note
        r = c.post("/api/code-note", json={"note": "Patient seen today. Filling placed upper left molar."})
        assert r.status_code == 200 and r.json()["dental"] is True and r.json()["codes"][0]["tooth"] == "14" and calls
        # the claim check scores dental claims by the dental rules, with no diagnosis needed
        body = {"codes": [{"code": "D0140", "type": "CDT"}, {"code": "D2391", "type": "CDT"}], "lines": [{"code": "D0140"}, {"code": "D2391", "tooth": "19", "surface": "MOD"}],
                "dob": "1990-01-01", "dos": "2026-10-01", "note": "CLAIM: patient P, DOB 1990-01-01, sex F, payer Cigna Dental, member ID X1, rendering provider Dr A, date of service 2026-10-01, place of service 11.\n\nx"}
        r = c.post("/api/validate-claim", json=body)
        assert r.status_code == 200
        j = r.json()
        assert any("D2393" in m for m in j["missing"]) and j["score"] <= 60
        assert not any("ICD" in m or "diagnos" in m.lower() for m in j["missing"])
        ok = c.post("/api/validate-claim", json=dict(body, codes=[{"code": "D0120", "type": "CDT"}, {"code": "D1110", "type": "CDT"}], lines=[{"code": "D0120"}, {"code": "D1110"}])).json()
        assert ok["score"] >= 90 and not ok["missing"]
        hist = [{"dos": "2026-03-01", "cpt": ["D1110"]}, {"dos": "2026-06-01", "cpt": ["D1110"]}]
        fr = c.post("/api/validate-claim", json=dict(body, codes=[{"code": "D1110", "type": "CDT"}], lines=[{"code": "D1110"}], history=hist)).json()
        assert any("12 months" in f for f in fr["flags"])
    finally:
        main.app.dependency_overrides.clear()


def test_dental_score_rules_alone():
    import dental_rules as dr
    clean = dr.score({"cpt": ["D0120", "D1110"], "lines": [{"code": "D0120"}, {"code": "D1110"}]})
    assert clean["score"] >= 90 and not clean["missing"]
    bad = dr.score({"lines": [{"code": "D2391", "tooth": "19", "surface": "MOD"}]})
    assert bad["score"] <= 60 and bad["missing"]
    none = dr.score({"cpt": ["99213"]})
    assert none["missing"] and none["score"] <= 60


def test_dental_clinics_always_get_the_new_app():
    from starlette.requests import Request
    with auth.SessionLocal() as db:
        d = _user(db, "pr-d3@example.test", "Practice Clinic D3")
        m = _user(db, "pr-m3@example.test", "Practice Clinic M3")
        from tests.test_dental import set_practice
        set_practice(db, "Practice Clinic D3", "dental")
        db.refresh(d); db.refresh(m); db.expunge(d); db.expunge(m)

    def req(path, cookie=""):
        return Request({"type": "http", "method": "GET", "path": path, "query_string": b"", "headers": [(b"cookie", cookie.encode())] if cookie else []})
    # the classic pages are medical: a dental clinic is sent to the new app, even with the classic cookie
    for path, to in (("/revenue/claims", "/v4/revenue/claims"), ("/coding", "/v4/revenue/code-a-note"), ("/scribe", "/v4/emr/encounter"), ("/emr/charting", "/v4/emr/patients")):
        r = main._v4_redirect(req(path, "althais_ui=classic"), d)
        assert r is not None and r.headers["location"] == to, path
    # medical: unchanged (the classic cookie keeps the classic pages)
    assert main._v4_redirect(req("/revenue/claims", "althais_ui=classic"), m) is None
    assert main._v4_redirect(req("/coding"), m) is None


def test_v4_pages_follow_the_plan():
    import v4_preview
    with auth.SessionLocal() as db:
        u = _user(db, "pr-p@example.test", "Practice Clinic P")
    assert set(v4_preview.allowed_pages(u, {"suite"})) == {k for k, _ in v4_preview.PAGES.values()}
    ins = v4_preview.allowed_pages(u, {"insurance"})
    assert "claims" in ins and "connect" in ins and "settings" in ins and "encounter" not in ins and "codenote" not in ins
    cod = v4_preview.allowed_pages(u, {"coding"})
    assert "codenote" in cod and "claims" not in cod
