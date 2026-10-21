"""Access log (audit.py): what gets recorded, who can read it, export, and the integrity check."""
import uuid

import pytest
from sqlalchemy import select, update


def _user(org, role="admin"):
    import auth
    email = f"{role}-{uuid.uuid4().hex[:8]}@example.test"
    with auth.SessionLocal() as db:
        u = auth.User(email=email, password_hash=auth.hash_password("correct horse 1"), full_name="Audit " + role.title(),
                      organization=org, role=role, claims_submitted=0, onboarding_complete=1, active=1, email_verified=1)
        db.add(u)
        db.commit()
        db.refresh(u)
        return u.id, email


def _login(email, password="correct horse 1"):
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    r = c.post("/login", data={"email": email, "password": password})
    return c, r


def _rows(**where):
    import audit, auth
    with auth.SessionLocal() as db:
        q = select(audit.AccessLog)
        for k, v in where.items():
            q = q.where(getattr(audit.AccessLog, k) == v)
        return db.scalars(q.order_by(audit.AccessLog.id)).all()


@pytest.fixture(autouse=True)
def _fresh():
    import audit
    audit._seen.clear()
    audit._users.clear()
    yield


def test_sign_in_failed_sign_in_and_sign_out_are_recorded():
    org = "Audit Clinic " + uuid.uuid4().hex[:6]
    _, email = _user(org)
    c, r = _login(email, "wrong password")
    assert r.status_code == 401
    c, r = _login(email)
    assert r.status_code == 200, r.text
    c.get("/logout")
    acts = [x.action for x in _rows(user_email=email)]
    assert acts[:3] == ["sign_in_failed", "sign_in", "sign_out"]
    failed = _rows(user_email=email, action="sign_in_failed")[0]
    assert failed.detail == "wrong password" and failed.org_key and failed.ip is not None


def test_api_calls_recorded_reads_deduplicated_changes_always():
    org = "Audit Clinic " + uuid.uuid4().hex[:6]
    _, email = _user(org)
    c, _ = _login(email)
    for _ in range(3):
        c.get("/api/patients")                                   # the app polls this every 30 seconds
    views = _rows(user_email=email, path="/api/patients", method="GET")
    assert len(views) == 1 and views[0].action == "view" and views[0].what == "patients"
    for i in range(2):
        c.post("/api/claims", json={"id": f"AUD-{i}", "patientName": "Test Patient", "mrn": "M1", "payer": "Aetna", "amount": 100, "status": "Draft", "codes": []})
    writes = _rows(user_email=email, path="/api/claims", method="POST")
    assert len(writes) == 2 and {w.action for w in writes} == {"create"}
    assert all(w.user_id and w.role == "admin" and w.sig for w in writes)


def test_opening_a_record_is_recorded_once_per_window():
    org = "Audit Clinic " + uuid.uuid4().hex[:6]
    _, email = _user(org)
    c, _ = _login(email)
    for _ in range(3):
        assert c.post("/api/access-log/view", json={"what": "patient", "id": "MRN-777"}).status_code == 200
    c.post("/api/access-log/view", json={"what": "claim", "id": "CLM-9", "patient": "MRN-777"})
    assert c.post("/api/access-log/view", json={"what": "notes", "id": "x"}).status_code == 400
    opened = _rows(user_email=email, action="view", path="/api/access-log/view")
    assert [(o.what, o.record_id) for o in opened] == [("patient", "MRN-777"), ("claim", "CLM-9")]
    assert opened[1].detail == "patient MRN-777"


def test_only_admins_read_their_own_clinic():
    org_a = "Audit Clinic " + uuid.uuid4().hex[:6]
    org_b = "Audit Clinic " + uuid.uuid4().hex[:6]
    _, admin_a = _user(org_a)
    _, biller_a = _user(org_a, "biller")
    _, admin_b = _user(org_b)
    cb, _ = _login(admin_b)
    cb.post("/api/access-log/view", json={"what": "patient", "id": "OTHER-CLINIC"})
    cbill, _ = _login(biller_a)
    assert cbill.get("/api/access-log").status_code == 403
    ca, _ = _login(admin_a)
    j = ca.get("/api/access-log").json()
    seen = {r["user"] for r in j["rows"]}
    assert admin_a in seen and biller_a in seen and admin_b not in seen
    assert all(r["record"] != "OTHER-CLINIC" for r in j["rows"])
    only = ca.get("/api/access-log", params={"user": biller_a, "action": "sign_in"}).json()["rows"]
    assert only and all(r["user"] == biller_a and r["action"] == "sign_in" for r in only)


def test_export_is_csv_and_is_itself_recorded():
    org = "Audit Clinic " + uuid.uuid4().hex[:6]
    _, email = _user(org)
    c, _ = _login(email)
    r = c.get("/api/access-log.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.text.splitlines()[0].startswith("Time (UTC),Person,Role")
    assert email in r.text
    assert _rows(user_email=email, action="export", path="/api/access-log.csv")


def test_integrity_check_finds_rows_changed_in_the_database():
    import audit, auth
    org = "Audit Clinic " + uuid.uuid4().hex[:6]
    _, email = _user(org)
    c, _ = _login(email)
    c.post("/api/access-log/view", json={"what": "patient", "id": "MRN-1"})
    first = c.get("/api/access-log/verify").json()
    assert first["checked"] >= 2 and first["altered_count"] == 0
    target = _rows(user_email=email, record_id="MRN-1")[0]
    with auth.SessionLocal() as db:
        db.execute(update(audit.AccessLog).where(audit.AccessLog.id == target.id).values(record_id="MRN-2"))
        db.commit()
    after = c.get("/api/access-log/verify").json()
    assert after["altered"] == [target.id]


def test_extension_calls_are_attributed_to_the_person_who_connected_it(monkeypatch):
    import emr_connect as ec
    import extension_api as xa
    monkeypatch.setattr(ec, "_gate", lambda u, d: None)
    xa._hits.clear()
    org = "Audit Clinic " + uuid.uuid4().hex[:6]
    _, email = _user(org)
    c, _ = _login(email)
    tok = c.post("/api/extension/token").json()["token"]
    from fastapi.testclient import TestClient
    import main
    bare = TestClient(main.app)                 # the extension has no session cookie, only its token
    bare.post("/api/extension/check", headers={"Authorization": "Bearer " + tok},
              json={"claim": {"claim_id": "X1", "dos": "10/10/2026", "payer": "Aetna", "cpt": ["99284"], "icd": ["R07.9"], "amount": 450}})
    rows = _rows(user_email=email, path="/api/extension/check")
    assert rows and rows[0].on_behalf == "browser extension" and rows[0].what == "extension"
