"""Test clinics (set in /admin): two-step login is optional for their people, every change is in the admin audit log,
and switching it off brings the requirement back."""
import json
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select


def _clinic_admin():
    import auth
    org, email = "TC " + uuid.uuid4().hex[:6], f"tc-{uuid.uuid4().hex[:8]}@example.test"
    with auth.SessionLocal() as db:
        db.add(auth.User(email=email, password_hash=auth.hash_password("correct horse 1"), full_name="Test Admin", organization=org, role="admin",
                         claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
        db.add(auth.OrgSettings(org_key=org, category=auth.ENTITLEMENTS_CATEGORY, data=json.dumps({"rev": 0, "products": ["suite"]})))
        db.commit()
    return org, email


def _signs_in_straight_to_the_app(email):
    import main
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "correct horse 1"})
    r = c.get("/api/patients")   # any app API: blocked until two-step login is set up when it is required
    return r.status_code == 200


def test_test_clinic_makes_two_step_optional(monkeypatch):
    import auth, main, security
    monkeypatch.setenv("MFA_ENFORCE", "1")
    org, email = _clinic_admin()
    assert not _signs_in_straight_to_the_app(email)                  # admins must set up two-step login first

    main.app.dependency_overrides[auth.require_admin] = lambda: True
    try:
        admin = TestClient(main.app)
        r = admin.post("/api/admin/orgs/test-clinic", json={"org_key": org, "on": True})
        assert r.status_code == 200 and r.json()["test_clinic"] is True
        assert any(o["org_key"] == org and o["test_clinic"] for o in admin.get("/api/admin/orgs").json())
        assert _signs_in_straight_to_the_app(email)
        with auth.SessionLocal() as db:
            u = db.scalar(select(auth.User).where(auth.User.email == email))
            assert security.policy_for(u, db)["mfa"] == "optional"
            assert db.scalar(select(auth.AdminAuditLog).where(auth.AdminAuditLog.target == org))

        # a plan change keeps the flag
        admin.post("/api/admin/orgs/products", json={"org_key": org, "products": ["suite"]})
        with auth.SessionLocal() as db:
            assert auth.org_test_clinic(db.scalar(select(auth.User).where(auth.User.email == email)), db)

        assert admin.post("/api/admin/orgs/test-clinic", json={"org_key": org, "on": False}).json()["test_clinic"] is False
        assert not _signs_in_straight_to_the_app(email)
        assert admin.post("/api/admin/orgs/test-clinic", json={"org_key": "no such clinic", "on": True}).status_code == 404
        assert admin.post("/api/admin/orgs/test-clinic", json={"org_key": org, "on": "yes"}).status_code == 400
    finally:
        main.app.dependency_overrides.clear()


def test_only_althais_admins_can_set_it():
    import main
    org, _ = _clinic_admin()
    assert TestClient(main.app).post("/api/admin/orgs/test-clinic", json={"org_key": org, "on": True}).status_code in (401, 403)


def test_test_clinic_skips_the_code_for_people_who_turned_it_on(monkeypatch):
    import auth, main
    monkeypatch.setenv("MFA_ENFORCE", "1")
    org, email = _clinic_admin()
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == email))
        u.mfa_enabled = 1
        db.commit()
    asks = lambda: TestClient(main.app).post("/login", data={"email": email, "password": "correct horse 1"}).json().get("mfa") is True
    assert asks()                                                    # a normal clinic: the code from the app
    main.app.dependency_overrides[auth.require_admin] = lambda: True
    try:
        TestClient(main.app).post("/api/admin/orgs/test-clinic", json={"org_key": org, "on": True})
    finally:
        main.app.dependency_overrides.clear()
    assert not asks() and _signs_in_straight_to_the_app(email)       # a test clinic: password only
