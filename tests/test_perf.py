"""Page loads read the session, user and clinic entitlements once per request, and say how long the database took."""
from sqlalchemy import event

import auth


def _count(c, path):
    seen = []

    def on(conn, cursor, stmt, params, ctx, many):
        seen.append(stmt)
    event.listen(auth.engine, "before_cursor_execute", on)
    try:
        r = c.get(path, follow_redirects=False)
    finally:
        event.remove(auth.engine, "before_cursor_execute", on)
    return r, seen


def test_one_lookup_per_request(monkeypatch):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    import main
    import security
    monkeypatch.setattr(security, "mfa_required", lambda u, db: False)
    with auth.SessionLocal() as db:
        if not db.scalar(select(auth.User).where(auth.User.email == "perf@example.test")):
            db.add(auth.User(email="perf@example.test", password_hash=auth.hash_password("correct horse 1"), full_name="Perf", organization="Perf Clinic",
                             role="admin", claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
            db.commit()
    client = TestClient(main.app)
    assert client.post("/login", data={"email": "perf@example.test", "password": "correct horse 1"}, follow_redirects=False).status_code in (200, 302, 303)
    r, q = _count(client, "/v4/emr/patients")
    assert r.status_code == 200
    assert sum("FROM user_sessions" in s for s in q) == 1
    assert sum("FROM org_settings" in s for s in q) <= 1          # entitlements: once, not seven times
    assert "db;dur=" in r.headers.get("server-timing", "")
    r, q = _count(client, "/api/patients")
    assert r.status_code == 200 and sum("FROM user_sessions" in s for s in q) == 1


def test_memo_forgets_after_a_write():
    import perf
    tok = perf._memo.set({})
    try:
        with auth.SessionLocal() as db:
            u = auth.User(email="memo@example.test", password_hash="x", full_name="M", organization="Memo Clinic", role="admin")
            db.add(u)
            db.commit()
            assert auth.org_practice(u, db) == "medical"
            db.add(auth.OrgSettings(org_key="Memo Clinic", category=auth.ENTITLEMENTS_CATEGORY, data='{"practice": "dental"}'))
            db.commit()
            assert auth.org_practice(u, db) == "dental"            # the write cleared the memo
    finally:
        perf._memo.reset(tok)
