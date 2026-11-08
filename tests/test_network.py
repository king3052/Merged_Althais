"""The Althais network (network.py): what fix works, learned from how cases end; payer change alerts from weekly
paid/denied counts; and where both show up (Recovery, the extension's checks, the Monday email)."""
import datetime as dt
import json

import pytest
from sqlalchemy import select

import lost_money as lm
import network as nw
import recovery as rc

TODAY = dt.date(2026, 11, 8)


@pytest.fixture()
def db():
    import auth
    with auth.SessionLocal() as s:
        s.query(nw.FixOutcome).delete()
        s.query(nw.PayerSignal).delete()
        s.query(auth.OrgSettings).filter(auth.OrgSettings.org_key == nw.NETWORK).delete()
        s.commit()
        yield s


def _case(s, org, i, fix="add_modifier:99213:25", kind="corrected", payer="AETNA", code="99213", carc="97"):
    c = rc.RecoveryCase(org_key=org, dedupe=f"nw-{org}-{i}-{fix}", category="denial", kind=kind, claim_id=f"N{i}", status="SENT", history="[]",
                        recovered=0.0, basis_date="", payer=payer, code=code, carc=carc, fix=fix, expected=92.0,
                        sent_at=dt.datetime.utcnow() - dt.timedelta(days=10 + i))
    s.add(c)
    s.flush()
    return c


def test_what_works_ranks_fixes_by_their_real_record(db):
    for i in range(5):
        nw.record_outcome(db, _case(db, "Clinic A", i), won=i != 4)              # modifier 25: paid 4 of 5
    for i in range(3):
        nw.record_outcome(db, _case(db, "Clinic B", 10 + i, fix="", kind="appeal"), won=i == 0)   # appeal: 1 of 3
    c = _case(db, "Clinic A", 99)
    nw.record_outcome(db, c, True)
    nw.record_outcome(db, c, True)                                               # one case counts once
    db.commit()
    ww = nw.what_works(db, "Aetna", "99213", "97", org="Clinic A")
    assert [w["fix"] for w in ww] == ["corrected:mod25", "appeal:bundle"]
    top = ww[0]
    assert top["tried"] == 6 and top["paid"] == 5 and top["rate"] == 0.83 and top["clinics"] == 1 and top["yours"] == 6
    assert top["medianDays"] >= 10 and top["label"] == "Corrected claim adding modifier 25"
    assert nw.odds(db, c) == 0.83
    assert nw.what_works(db, "Cigna", "99213", "97")[0]["scope"] == "this reason, all payers"     # falls back
    assert nw.what_works(db, "Aetna", "99213", "50") == []                                        # never seen: nothing shown


def test_cases_feed_outcomes_when_they_end(db):
    case = _case(db, "Clinic C", 1)
    db.commit()
    era = lm.build_835("AETNA", "2026-12-01", [{"id": "N1", "dos": "2026-10-01", "charge": 145.0, "paid": 88.0, "pr": 0.0,
                       "lines": [("99213", ["25"], 145.0, 88.0, 88.0, [("CO", "45", 57.0)], 1)]}])
    rc.match(db, [r for r in lm.parse_835(era)], org="Clinic C")
    db.commit()
    o = db.scalar(select(nw.FixOutcome))
    assert o.won == 1 and o.fix == "corrected:mod25" and o.payer == "aetna" and o.clinic == nw.clinic("Clinic C") and o.clinic != "Clinic C"


def _signals(db, org, weeks):
    """weeks: list of (monday, lines, denied)"""
    remits = []
    for n, (wk, lines, denied) in enumerate(weeks):
        for i in range(lines):
            d = i < denied
            ln = lm.Line(code="99214", mods=["25"], charge=210.0, paid=0.0 if d else 124.0, allowed=None if d else 124.0, dos=wk,
                         adjustments=[lm.Adj("CO", "97", 210.0)] if d else [lm.Adj("CO", "45", 86.0)])
            remits.append(lm.Remit(claim_id=f"{org}{n}-{i}", payer="BLUE CROSS BLUE SHIELD OF TEXAS", dos=wk, date=wk, lines=[ln]))
    a = lm.Analysis(lm.Dataset(remits=remits), TODAY)
    nw.record_signals(db, org, a)


def test_payer_change_alert_needs_a_real_jump_at_two_clinics(db):
    old = [("2026-07-27", 5, 0), ("2026-08-17", 5, 0), ("2026-09-07", 5, 1)]
    new = [("2026-10-19", 4, 3), ("2026-11-02", 4, 2)]
    _signals(db, "Clinic One", old + new)
    db.commit()
    assert nw.detect(db, TODAY) == []                                            # one clinic isn't a network pattern
    mine = nw.detect(db, TODAY, min_clinics=1, only_clinic=nw.clinic("Clinic One"))
    assert len(mine) == 1 and mine[0]["code"] == "99214" and mine[0]["carc"] == "97"
    _signals(db, "Clinic Two", old + new)
    _signals(db, "Clinic One", old + new)                                        # re-running the same files doesn't double
    db.commit()
    al = nw.detect(db, TODAY)
    assert len(al) == 1 and al[0]["clinics"] == 2 and al[0]["denied"] == 10 and al[0]["rate"] == 0.62
    assert "started denying 99214 with 25" in al[0]["message"] and "at 2 Althais clinics" in al[0]["message"] and "$" not in al[0]["message"]
    steady = [("2026-08-03", 5, 2), ("2026-08-24", 5, 2), ("2026-10-19", 4, 2), ("2026-11-02", 4, 2)]
    _signals(db, "Clinic Three", steady)
    db.commit()
    assert nw.detect(db, TODAY, min_clinics=1, only_clinic=nw.clinic("Clinic Three")) == []   # always denied there: not a change


def test_alerts_reach_the_clinics_that_bill_that_payer(db, monkeypatch):
    old = [("2026-07-27", 5, 0), ("2026-08-17", 5, 0), ("2026-09-07", 5, 0)]
    new = [("2026-10-19", 4, 3), ("2026-11-02", 4, 3)]
    _signals(db, "Clinic One", old + new)
    _signals(db, "Clinic Two", old + new)
    db.commit()
    assert nw.refresh_alerts(TODAY) == 1
    assert nw.alerts_for(db, "Clinic Nine") == []                               # doesn't bill that payer
    got = nw.alerts_for(db, "Clinic Nine", payers=["Blue Cross Blue Shield of Texas"])
    assert len(got) == 1 and got[0]["scope"] == "network"
    # the extension's money check warns on a claim with that payer and code
    import money_checks as mc
    r = mc.check(db, "Clinic Nine", {"cpt": ["99214", "12002"], "modifiers": [], "line_mods": [{"code": "99214", "mod": "25"}],
                                     "payer": "Blue Cross Blue Shield of Texas", "dos": "2026-11-09"})
    pc = [f for f in r["flags"] if f["code"] == "money_payer_change"]
    assert len(pc) == 1 and pc[0]["message"].startswith("Heads up:")
    # and the Monday email lists it
    with rc.SessionLocal() as s2:
        s2.add(rc.RecoveryCase(org_key="Clinic One", dedupe="nw-digest", category="denial", kind="appeal", claim_id="Z", status="OPEN",
                               history="[]", recovered=0.0, basis_date="", payer="BCBS", code="99214", expected=10.0))
        s2.commit()
        d = rc.digest(s2, "Clinic One")
        assert d["alerts"] and "Payer changes to watch" in rc.digest_html(d, "Clinic One", "#")


def test_recovery_api_shows_what_works_and_alerts():
    from fastapi.testclient import TestClient
    import auth
    import main
    email = "network-biller@example.com"
    with auth.SessionLocal() as s:
        if not s.scalar(select(auth.User).where(auth.User.email == email)):
            s.add(auth.User(email=email, password_hash=auth.hash_password("violet maple harbor 42"), full_name="P", organization="Network Clinic",
                            role="biller", claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
            s.commit()
        for i in range(3):
            nw.record_outcome(s, _case(s, "Network Peer", 50 + i), won=True)
        mine = _case(s, "Network Clinic", 60)
        s.commit()
        cid = mine.id
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "violet maple harbor 42"})
    d = c.get(f"/api/recovery/cases/{cid}").json()
    assert d["whatWorks"][0]["paid"] == 3 and d["myFix"] == "corrected:mod25"
    assert c.get("/api/recovery/alerts").json() == {"alerts": []}
    st = c.post(f"/api/recovery/cases/{cid}/status", json={"status": "RECOVERED", "recovered": 90})
    assert st.status_code == 200
    with auth.SessionLocal() as s:
        assert s.scalar(select(nw.FixOutcome).where(nw.FixOutcome.clinic == nw.clinic("Network Clinic"))).won == 1
