"""Money checks before a claim goes out (money_checks.py): payer memory learned from Lost Money Reports, add-ons the
payer pays, pairs, payer denial patterns, the Althais network, and the extension panel's API."""
import datetime as dt

import pytest

import lost_money as lm
import money_checks as mc

TODAY = dt.date(2026, 11, 1)


def _ok(code, amt, charge, mods=()):
    return (code, list(mods), charge, amt, amt, [("CO", "45", round(charge - amt, 2))] if charge > amt else [], 1)


def _den(code, charge, carc, mods=()):
    return (code, list(mods), charge, 0.0, None, [("CO", carc, charge)], 1)


def _r(cid, dos, lines):
    return {"id": cid, "dos": dos, "charge": sum(l[2] for l in lines), "paid": sum(l[3] for l in lines), "pr": 0.0, "lines": lines}


def aetna_files():
    cl = [_r(f"A{i}", "2026-08-01", [_ok("99213", 92.0, 145.0), _ok("99051", 24.0, 50.0)]) for i in range(3)]          # pays 99051
    cl += [_r(f"B{i}", "2026-08-03", [_ok("99214", 131.0, 210.0), _ok("96372", 28.0, 45.0), _ok("J1885", 6.2, 25.0)]) for i in range(3)]
    cl += [_r(f"D{i}", "2026-08-04", [_den("99213", 145.0, "97"), _ok("96372", 28.0, 45.0)]) for i in range(3)]       # 99213 w/o 25 + injection: denied
    return [("aetna.835", lm.build_835("AETNA", "2026-08-20", cl, "CI").encode())]


@pytest.fixture()
def db():
    import auth
    with auth.SessionLocal() as s:
        s.query(auth.OrgSettings).filter(auth.OrgSettings.category == mc.CATEGORY).delete()
        s.commit()
        yield s


def claim(**kw):
    c = {"cpt": [], "modifiers": [], "line_mods": [], "payer": "Aetna", "dos": "2026-11-07", "amount": 200.0}
    c.update(kw)
    return c


def test_learns_rates_addons_and_denials(db):
    out = mc.learn(db, "Money Clinic", lm.analyze(aetna_files(), TODAY))
    db.commit()
    mem = mc._load(db, "Money Clinic")["payers"]["aetna"]
    assert out == {"payers": 1, "patterns": 1}
    assert mem["rates"]["99213"] == {"rate": 92.0, "n": 3} and mem["rates"]["99051"]["n"] == 3 and mem["denials"] == {"99213|97|-": 3}
    mc.learn(db, "Money Clinic", lm.analyze(aetna_files(), TODAY))                     # the same files again: nothing doubles
    assert mc._load(db, "Money Clinic")["payers"]["aetna"]["denials"] == {"99213|97|-": 3}


def test_checks_a_claim_against_the_clinics_payer_memory(db):
    mc.learn(db, "Money Clinic", lm.analyze(aetna_files(), TODAY))
    db.commit()
    # Saturday visit, 99213 + injection without 25 or a drug: every money check fires
    r = mc.check(db, "Money Clinic", claim(cpt=["99213", "96372"], dos="2026-11-07", payer="Aetna PPO"))
    got = {f["code"]: f for f in r["flags"]}
    assert {"money_mod25", "money_no_drug", "money_addon", "money_payer_pattern"} <= set(got)
    assert got["money_addon"]["dollars"] == 24.0 and "99051" in got["money_addon"]["message"] and got["money_addon"]["fix"] == "Add 99051"
    assert "denied 99213 with no modifier 3 times" in got["money_payer_pattern"]["message"]
    assert got["money_mod25"]["dollars"] == 92.0 and r["expected"] == 120.0 and r["at_stake"] >= 116.0
    # fixed: 25 on the visit, the drug added, a weekday: nothing left to say about money
    ok = mc.check(db, "Money Clinic", claim(cpt=["99213", "96372", "J1885"], line_mods=[{"code": "99213", "mod": "25"}], dos="2026-11-04"))
    assert ok["flags"] == []
    # a payer Althais has never seen: the rules still run, no memory-based checks
    new = mc.check(db, "Money Clinic", claim(cpt=["99201"], payer="Cigna"))
    assert [f["code"] for f in new["flags"]] == ["money_deleted"] and new["expected"] is None and not new["payer_known"]


def test_government_payer_rules(db):
    r = mc.check(db, "Money Clinic", claim(cpt=["99243", "87880", "J7613", "85025"], payer="Medicare Part B"))
    codes = {f["code"] for f in r["flags"]}
    assert {"money_consult", "money_qw", "money_no_neb", "money_no_draw"} <= codes
    r2 = mc.check(db, "Money Clinic", claim(cpt=["99213", "87880"], modifiers=["QW"], payer="Medicare Part B"))
    assert "money_qw" not in {f["code"] for f in r2["flags"]}                       # claim-level QW lands on the test
    r3 = mc.check(db, "Money Clinic", claim(cpt=["99213", "87880"], payer="Blue Cross"))
    assert "money_qw" not in {f["code"] for f in r3["flags"]}                       # commercial: not flagged


def test_network_patterns_need_two_clinics_and_carry_no_numbers(db):
    mc.learn(db, "Clinic One", lm.analyze(aetna_files(), TODAY))
    db.commit()
    c = claim(cpt=["99213", "96372"], dos="2026-11-04")
    assert "money_network" not in {f["code"] for f in mc.check(db, "Clinic Three", c)["flags"]}     # one clinic isn't a pattern
    mc.learn(db, "Clinic Two", lm.analyze(aetna_files(), TODAY))
    db.commit()
    net = [f for f in mc.check(db, "Clinic Three", c)["flags"] if f["code"] == "money_network"]
    assert len(net) == 1 and "Across 2 Althais clinics" in net[0]["message"] and "$" not in net[0]["message"]
    assert "Clinic One" not in str(mc._load(db, mc.NETWORK))                       # clinics are hashed
    assert "money_network" not in {f["code"] for f in mc.check(db, "Clinic One", c)["flags"]}       # its own pattern says it already


def test_extension_check_returns_money(monkeypatch, db):
    from fastapi.testclient import TestClient
    import auth
    import emr_connect as ec
    import extension_api as xa
    import main
    from sqlalchemy import select
    user = db.scalar(select(auth.User).where(auth.User.email == "money-ext@example.test"))
    if not user:
        user = auth.User(email="money-ext@example.test", password_hash="x", full_name="Test User", organization="Money Ext Clinic", role="admin",
                         claims_submitted=0, onboarding_complete=1, active=1)
        db.add(user)
        db.commit()
        db.refresh(user)
    monkeypatch.setattr(ec, "_gate", lambda u, d: None)
    main.app.dependency_overrides[auth.require_user] = lambda: user
    main.app.dependency_overrides[auth.require_biller] = lambda: user
    xa._hits.clear()
    try:
        c = TestClient(main.app)
        mc.learn(db, "Money Ext Clinic", lm.analyze(aetna_files(), TODAY))
        db.commit()
        tok = c.post("/api/extension/token").json()["token"]
        body = {"claim": {"claim_id": "X1", "dos": "11/07/2026", "payer": "Aetna", "cpt": ["99213", "96372"], "icd": ["J02.9"], "modifiers": [], "amount": "190"}}
        j = c.post("/api/extension/check", json=body, headers={"Authorization": f"Bearer {tok}"}).json()
        money = [f for f in j["flags"] if f["code"].startswith("money_")]
        assert money and j["money"]["atStake"] > 0 and j["money"]["expected"] == 120.0 and j["money"]["known"]
        assert all("dollars" in f for f in money) and j["verdict"] in ("review", "fix")
    finally:
        main.app.dependency_overrides.clear()


def test_running_a_report_teaches_the_payer_memory():
    from sqlalchemy import select
    from fastapi.testclient import TestClient
    import auth
    import main
    email = "money-learn@example.com"
    with auth.SessionLocal() as s:
        if not s.scalar(select(auth.User).where(auth.User.email == email)):
            s.add(auth.User(email=email, password_hash=auth.hash_password("violet maple harbor 42"), full_name="P", organization="Money Learn Clinic",
                            role="biller", claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
            s.commit()
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "violet maple harbor 42"})
    assert c.post("/api/lost-money/report", data={"save": "0"}, files=[("files", f) for f in aetna_files()]).status_code == 200
    with auth.SessionLocal() as s:
        assert mc._load(s, "Money Learn Clinic")["payers"]["aetna"]["rates"]["99214"]["rate"] == 131.0
