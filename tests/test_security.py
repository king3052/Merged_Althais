"""Sign-in security: sessions and the idle sign-out, two-step login, password rules, account locks, the admin console."""
import datetime as dt
import json
import time
from datetime import timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import auth
import main
import security

PW = "violet maple harbor 42"


def user(email, org="Security Clinic", role="admin"):
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == email))
        if not u:
            u = auth.User(email=email, password_hash=auth.hash_password(PW), full_name="Pat Test", organization=org, role=role,
                          claims_submitted=0, onboarding_complete=1, active=1, email_verified=1)
            db.add(u); db.commit()
        db.refresh(u)
        return u.id


def login(email, pw=PW):
    c = TestClient(main.app)
    return c, c.post("/login", data={"email": email, "password": pw})


def sid_of(c):
    return auth.decode_token(c.cookies.get(auth.COOKIE_NAME))["sid"]


def age_session(sid, minutes):
    with auth.SessionLocal() as db:
        r = db.get(security.UserSession, sid)
        r.last_seen = dt.datetime.now(timezone.utc) - dt.timedelta(minutes=minutes)
        db.commit()


@pytest.fixture()
def enforce(monkeypatch):
    monkeypatch.setenv("MFA_ENFORCE", "1")
    security._ip_fails.clear()
    yield
    security._ip_fails.clear()


# ── passwords ───────────────────────────────────────────────────────────────
def test_password_rules():
    assert security.password_problem("short") and "12" in security.password_problem("short")
    for bad in ("password1234", "Password123!", "qwertyuiop12", "aaaaaaaaaaaaaa", "123456789012"):
        assert security.password_problem(bad), bad
    assert security.password_problem("jordan.smith2026", email="jordan.smith@clinic.com")
    assert security.password_problem("althais2026!", email="x@y.com")
    assert not security.password_problem(PW, email="pat@clinic.com", name="Pat Test")
    assert not security.password_problem("correct horse battery staple")


def test_register_and_reset_use_the_rules():
    c = TestClient(main.app)
    r = c.post("/register", data={"email": "weak-pw@example.test", "password": "password1", "organization": "Weak Clinic"})
    assert r.status_code == 400 and "12" in r.json()["error"]


# ── TOTP ────────────────────────────────────────────────────────────────────
def test_totp_matches_rfc_6238():
    secret = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"          # "12345678901234567890"
    assert security.hotp(secret, 1) == "287082"          # RFC 6238, T = 59 s, last 6 digits of 94287082
    assert security.check_code(secret, "287082", 0, at=59) == 1
    assert security.check_code(secret, "287082", 1, at=59) is None   # a code can't be used twice
    assert security.check_code(secret, "000000", 0, at=59) is None


# ── sessions ────────────────────────────────────────────────────────────────
def test_sign_out_ends_the_session_on_the_server():
    user("sess-a@example.test")
    c, r = login("sess-a@example.test")
    assert r.json()["ok"] and c.get("/api/me").status_code == 200
    token = c.cookies.get(auth.COOKIE_NAME)
    c.post("/logout")
    other = TestClient(main.app)
    other.cookies.set(auth.COOKIE_NAME, token)                         # the old cookie, replayed
    assert other.get("/api/me").status_code == 401


def test_idle_sign_out_and_what_counts_as_activity():
    user("sess-b@example.test")
    c, _ = login("sess-b@example.test")
    sid = sid_of(c)
    st = c.get("/api/session/status").json()
    assert st["idle_seconds"] == 15 * 60 and st["remaining"] > 14 * 60
    # background API calls don't count as activity
    age_session(sid, 10)
    c.get("/api/me")
    with auth.SessionLocal() as db:
        seen = db.get(security.UserSession, sid).last_seen
    assert (dt.datetime.now(timezone.utc) - security._aware(seen)).total_seconds() > 9 * 60
    # the page's ping does
    assert c.post("/api/session/ping").json()["remaining"] >= 15 * 60 - 1
    # 16 minutes without activity: signed out, and it stays that way
    age_session(sid, 16)
    assert c.get("/api/me").status_code == 401
    assert c.post("/api/session/ping").status_code == 401
    with auth.SessionLocal() as db:
        assert db.get(security.UserSession, sid).ended_why == "idle"


def test_old_tokens_without_a_session_are_refused():
    uid = user("sess-c@example.test")
    c = TestClient(main.app)
    c.cookies.set(auth.COOKIE_NAME, auth.jwt.encode({"sub": str(uid), "exp": time.time() + 3600}, auth.SECRET_KEY, algorithm="HS256"))
    assert c.get("/api/me").status_code == 401


def test_the_guard_script_is_on_signed_in_pages():
    user("sess-d@example.test")
    c, _ = login("sess-d@example.test")
    html = c.get("/v4/overview").text
    assert "session-guard.js" in html
    assert "session-guard.js" not in TestClient(main.app).get("/login").text
    r = c.get("/v4/overview")
    assert r.headers["x-content-type-options"] == "nosniff" and r.headers["x-frame-options"] == "SAMEORIGIN"


def test_changing_the_password_signs_out_everywhere_else():
    user("sess-e@example.test")
    a, _ = login("sess-e@example.test")
    b, _ = login("sess-e@example.test")
    new = "quiet river lantern 77"
    r = a.post("/api/change-password", data={"current_password": PW, "new_password": new})
    assert r.status_code == 200
    assert a.get("/api/me").status_code == 200 and b.get("/api/me").status_code == 401
    assert a.post("/api/change-password", data={"current_password": new, "new_password": "short"}).status_code == 400
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == "sess-e@example.test"))
        u.password_hash = auth.hash_password(PW); db.commit()


# ── locks ───────────────────────────────────────────────────────────────────
def test_five_wrong_passwords_lock_the_account():
    security._ip_fails.clear()
    user("lock-a@example.test")
    for _ in range(4):
        assert login("lock-a@example.test", "wrong password!!")[1].status_code == 401
    r = login("lock-a@example.test", "wrong password!!")[1]
    assert r.status_code == 429 and "locked" in r.json()["error"]
    assert login("lock-a@example.test")[1].status_code == 429                 # even the right password, until it ends
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == "lock-a@example.test"))
        u.locked_until = dt.datetime.now(timezone.utc) - dt.timedelta(seconds=1); db.commit()
    assert login("lock-a@example.test")[1].json()["ok"]
    security._ip_fails.clear()


# ── two-step login ──────────────────────────────────────────────────────────
def _enroll(c):
    j = c.post("/api/me/2fa/start").json()
    secret = j["secret"].replace(" ", "")
    assert j["qr"].startswith("data:image/svg+xml") and j["uri"].startswith("otpauth://totp/")
    code = security.hotp(secret, int(time.time() // 30))
    r = c.post("/api/me/2fa/enable", json={"code": code})
    assert r.status_code == 200
    return secret, r.json()["recovery_codes"]


def test_admins_must_set_up_two_step_login(enforce):
    user("mfa-a@example.test", org="MFA Clinic A")
    c, r = login("mfa-a@example.test")
    assert r.json()["redirect"] == "/setup-2fa"
    # held on the setup page until it's done
    assert c.get("/api/patients").status_code == 403
    assert c.get("/v4/overview", follow_redirects=False).headers["location"] == "/setup-2fa"
    assert c.get("/setup-2fa").status_code == 200
    secret, codes = _enroll(c)
    assert len(codes) == 10
    assert c.get("/api/patients").status_code == 200
    # next sign-in: the password alone isn't enough
    c2, r2 = login("mfa-a@example.test")
    j = r2.json()
    assert j.get("mfa") and not j.get("ok") and auth.COOKIE_NAME not in c2.cookies
    bad = c2.post("/login/mfa", data={"challenge": j["challenge"], "code": "000000"})
    assert bad.status_code == 401
    # the code from the app (the next time step, since the enrolment used this one)
    step = int(time.time() // 30) + 1
    ok = c2.post("/login/mfa", data={"challenge": j["challenge"], "code": security.hotp(secret, step)})
    assert ok.json()["ok"] and c2.get("/api/me").status_code == 200
    # the same code can't be used again
    c3, r3 = login("mfa-a@example.test")
    assert c3.post("/login/mfa", data={"challenge": r3.json()["challenge"], "code": security.hotp(secret, step)}).status_code == 401
    # a recovery code works once
    rec = c3.post("/login/mfa", data={"challenge": r3.json()["challenge"], "code": codes[0]})
    assert rec.json()["ok"] and "9 left" in rec.json()["notice"]
    c4, r4 = login("mfa-a@example.test")
    assert c4.post("/login/mfa", data={"challenge": r4.json()["challenge"], "code": codes[0]}).status_code == 401
    # required for an admin: can't be turned off
    assert c3.post("/api/me/2fa/disable", json={"password": PW, "code": codes[1]}).status_code == 403


def test_providers_are_optional_and_the_clinic_decides(enforce):
    user("mfa-adm@example.test", org="MFA Clinic B")
    user("mfa-prov@example.test", org="MFA Clinic B", role="provider")
    p, r = login("mfa-prov@example.test")
    assert r.json()["redirect"] == "/overview" and p.get("/api/me").status_code == 200
    a, _ = login("mfa-adm@example.test")
    _enroll(a)
    assert p.put("/api/clinic/security", json={"idle_minutes": 5, "mfa": "everyone"}).status_code == 403   # admins only
    r = a.put("/api/clinic/security", json={"idle_minutes": 5, "mfa": "everyone"})
    assert r.status_code == 200 and r.json()["policy"] == {"idle_minutes": 5, "mfa": "everyone"}
    # the provider's open session now has the new time and must set up two-step login
    assert p.get("/api/session/status").json()["idle_seconds"] == 300
    assert p.get("/api/patients").status_code == 403
    assert a.put("/api/clinic/security", json={"idle_minutes": 7, "mfa": "everyone"}).status_code == 400
    members = {m["email"]: m for m in a.get("/api/clinic/security").json()["members"]}
    assert members["mfa-adm@example.test"]["mfa"] and members["mfa-prov@example.test"]["required"]
    a.put("/api/clinic/security", json={"idle_minutes": 15, "mfa": "admins_billers"})


def test_admin_resets_a_lost_phone(enforce):
    user("mfa-boss@example.test", org="MFA Clinic C")
    user("mfa-billr@example.test", org="MFA Clinic C", role="biller")
    boss, _ = login("mfa-boss@example.test"); _enroll(boss)
    bill, _ = login("mfa-billr@example.test"); _enroll(bill)
    with auth.SessionLocal() as db:
        bid = db.scalar(select(auth.User.id).where(auth.User.email == "mfa-billr@example.test"))
    assert boss.post(f"/api/clinic/members/{bid}/reset-2fa").json()["ok"]
    assert bill.get("/api/me").status_code == 401                              # signed out
    c, r = login("mfa-billr@example.test")
    assert r.json()["redirect"] == "/setup-2fa"                                # sets it up again
    other = user("mfa-else@example.test", org="Another Clinic")
    assert boss.post(f"/api/clinic/members/{other}/reset-2fa").status_code == 404


# ── the admin console ───────────────────────────────────────────────────────
def test_admin_console_code_and_idle(monkeypatch):
    security._admin_fails.clear()
    c = TestClient(main.app)
    assert c.post("/admin/login", data={"username": "admin", "password": "x"}).json()["ok"]
    assert c.get("/api/admin/orgs").status_code == 200
    row, _ = security.session_row(type("R", (), {"cookies": c.cookies})(), admin=True)
    age_session(row.sid, 31)
    assert c.get("/api/admin/orgs").status_code == 401                         # 30 minutes idle
    secret = security.new_secret()
    monkeypatch.setenv("ADMIN_TOTP_SECRET", secret)
    security._admin_last_step["step"] = 0
    c2 = TestClient(main.app)
    assert c2.post("/admin/login", data={"username": "admin", "password": "x"}).json() == {"ok": False, "mfa": True}
    assert c2.post("/admin/login", data={"username": "admin", "password": "x", "code": "000000"}).status_code == 401
    ok = c2.post("/admin/login", data={"username": "admin", "password": "x", "code": security.hotp(secret, int(time.time() // 30))})
    assert ok.json()["ok"] and c2.get("/api/admin/orgs").status_code == 200
    # wrong passwords from one place are slowed down
    for _ in range(5):
        c.post("/admin/login", data={"username": "admin", "password": "nope"})
    assert c.post("/admin/login", data={"username": "admin", "password": "x"}).status_code == 429
    security._admin_fails.clear()
