"""A clinic's own Claim.MD account: Althais pulls its payment files (payment_sources.py), plus deadlines on every dollar."""
import datetime as dt
import json
import types

import pytest
from sqlalchemy import select

import lost_money as lm
import monitor as mon
import payment_sources as ps
import recovery as rc

ORG = "Payment Source Clinic"
GOOD_KEY = "ABC123goodkey"


def _ok(code, amt, charge):
    return (code, [], charge, amt, amt, [("CO", "45", round(charge - amt, 2))] if charge > amt else [], 1)


def _den(code, charge, carc):
    return (code, [], charge, 0.0, None, [("CO", carc, charge)], 1)


def _r(cid, dos, lines):
    return {"id": cid, "dos": dos, "charge": sum(l[2] for l in lines), "paid": sum(l[3] for l in lines), "pr": 0.0, "lines": lines}


def era(date, claims):
    return lm.build_835("AETNA", date, claims, "CI")


class FakeClaimMD:
    """Stands in for clearinghouse.ClaimMD: a clinic's account with a list of ERAs."""
    eras = {}           # eraid -> (paid date as Claim.MD writes it, 835 text)
    fail_on = set()     # eraids whose download fails
    downloads = []

    def __init__(self, key, environment="production"):
        self.key = key

    accept = None

    def check_connection(self, account):
        ok = self.key in (GOOD_KEY, self.accept)
        return types.SimpleNamespace(ok=ok, message="" if ok else "Claim.MD didn't accept that account key.")

    def era_list(self, last_id="0"):
        assert self.key == GOOD_KEY
        rows = [{"eraid": k, "date": v[0], "payer": "AETNA"} for k, v in self.eras.items() if int(k) > int(last_id or 0)]
        return rows, str(max([int(k) for k in self.eras] or [0]))

    def era_835(self, eraid):
        if eraid in self.fail_on:
            raise RuntimeError("Claim.MD timed out")
        FakeClaimMD.downloads.append(eraid)
        return self.eras[eraid][1]


@pytest.fixture()
def db(monkeypatch):
    import auth
    monkeypatch.setattr(ps, "CONNECTOR", FakeClaimMD)
    FakeClaimMD.eras, FakeClaimMD.fail_on, FakeClaimMD.downloads, FakeClaimMD.accept = {}, set(), [], None
    with auth.SessionLocal() as s:
        s.query(mon.PaymentFile).filter(mon.PaymentFile.org_key == ORG).delete()
        s.query(rc.RecoveryCase).filter(rc.RecoveryCase.org_key == ORG).delete()
        s.query(auth.OrgSettings).filter(auth.OrgSettings.org_key == ORG).delete()
        s.commit()
        yield s


NOW = dt.datetime(2026, 11, 13, 15)


def test_a_key_is_checked_with_claimmd_and_kept_encrypted(db):
    assert ps.connect(db, ORG, "short")["ok"] is False                         # obvious mistakes: never sent anywhere
    assert ps.connect(db, ORG, "paste key here please")["ok"] is False         # passes the shape check; Claim.MD says no
    assert ps.connect(db, ORG, "abc\u00e9defghij")["ok"] is False
    FakeClaimMD.accept = "Ab12.cd+34/ef=56_gh-78"                               # real keys can hold dots, slashes, plus and equals signs
    assert ps.connect(db, ORG, FakeClaimMD.accept)["ok"] is True
    ps.disconnect(db, ORG)
    assert ps.connect(db, ORG, "WRONGkey1")["ok"] is False                     # Claim.MD said no
    assert ps.status(db, ORG)["connected"] is False
    assert ps.connect(db, ORG, f"  {GOOD_KEY}\n", by="biller@clinic.test")["ok"] is True
    db.commit()
    st = ps.status(db, ORG)
    assert st["connected"] and st["label"] == "Claim.MD" and st["connectedBy"] == "biller@clinic.test"
    assert GOOD_KEY not in json.dumps(st) and GOOD_KEY not in ps._row(db, ORG).data   # stored sealed, never shown


def test_pulls_in_steps_skips_old_files_and_then_only_new_ones(db):
    ps.connect(db, ORG, GOOD_KEY)
    FakeClaimMD.eras = {
        "1": ("01/15/2026", era("2026-01-15", [_r("OLD", "2026-01-05", [_ok("99213", 92.0, 145.0)])])),     # older than 200 days
        **{str(i): (f"2026-10-{i:02d}", era(f"2026-10-{i:02d}", [_r(f"C{i}", "2026-10-01", [_ok("99213", 92.0, 145.0)])])) for i in range(2, 6)},
    }
    got = ps.sync(db, ORG, limit=3, now=NOW)
    assert got == {"read": 2, "kept": 2, "skipped": 1, "more": True, "error": ""}
    assert "1" not in FakeClaimMD.downloads                                     # skipped without downloading
    got = ps.sync(db, ORG, limit=3, now=NOW)
    assert got["read"] == 2 and got["more"] is False
    st = ps.status(db, ORG)
    assert st["filesRead"] == 4 and st["skippedOld"] == 1 and st["backfillDone"]
    assert mon.status(db, ORG)["fromClaimmd"] == 4
    FakeClaimMD.eras["6"] = ("2026-11-10", era("2026-11-10", [_r("C6", "2026-11-01", [_ok("99213", 92.0, 145.0)])]))
    got = ps.sync(db, ORG, now=NOW)
    assert got["read"] == 1 and FakeClaimMD.downloads[-1] == "6"


def test_a_failed_download_keeps_what_came_before_and_is_tried_again(db):
    ps.connect(db, ORG, GOOD_KEY)
    FakeClaimMD.eras = {str(i): (f"2026-10-{i:02d}", era(f"2026-10-{i:02d}", [_r(f"C{i}", "2026-10-01", [_ok("99213", 92.0, 145.0)])])) for i in range(1, 4)}
    FakeClaimMD.fail_on = {"2"}
    got = ps.sync(db, ORG, now=NOW)
    assert got["kept"] == 1 and "timed out" in got["error"] and got["more"] is False
    assert ps.load(db, ORG)["lastEraId"] == "1" and not ps.status(db, ORG)["backfillDone"]
    FakeClaimMD.fail_on = set()
    got = ps.sync(db, ORG, now=NOW)
    assert got["kept"] == 2 and got["error"] == "" and ps.status(db, ORG)["error"] == ""


def test_report_from_pulled_files_opens_cases_and_a_payment_closes_them(db):
    ps.connect(db, ORG, GOOD_KEY)
    rates = [_r(f"P{i}", "2026-10-01", [_ok("99214", 131.0, 210.0)]) for i in range(4)]
    FakeClaimMD.eras = {"1": ("2026-10-10", era("2026-10-10", rates + [_r("DEN", "2026-10-02", [_den("99214", 210.0, "16")])]))}
    ps.sync(db, ORG, now=NOW)
    db.commit()
    a, saved = mon.analyse(db, ORG, NOW, by="biller@clinic.test")
    db.commit()
    assert saved["created"] == 1 and a.summary()["found"] == 131.0
    case = db.scalar(select(rc.RecoveryCase).where(rc.RecoveryCase.org_key == ORG, rc.RecoveryCase.claim_id == "DEN"))
    assert case.status == "OPEN" and case.created_by == "biller@clinic.test"
    # the clinic resubmits; the payer pays; the next pull closes the case right away, not at the hourly run
    FakeClaimMD.eras["2"] = ("2026-11-05", era("2026-11-05", [_r("DEN", "2026-10-02", [_ok("99214", 131.0, 210.0)])]))
    ps.sync(db, ORG, now=NOW)
    db.commit()
    db.refresh(case)
    assert case.status == "RECOVERED" and case.recovered == 131.0


def test_disconnect_forgets_the_key_and_hourly_runs_skip_the_clinic(db):
    ps.connect(db, ORG, GOOD_KEY)
    db.commit()
    FakeClaimMD.eras = {"1": ("2026-10-10", era("2026-10-10", [_r("C1", "2026-10-01", [_ok("99213", 92.0, 145.0)])]))}
    assert ps.sync_due(NOW) >= 1
    ps.disconnect(db, ORG)
    db.commit()
    assert ps.status(db, ORG)["connected"] is False and ps._row(db, ORG) is None
    assert ps.sync(db, ORG, now=NOW)["error"] == "No clearinghouse account connected."
    assert mon.status(db, ORG)["filesKept"] == 1                                # files already read stay for the report


def test_routes_need_a_signed_in_biller():
    from fastapi.testclient import TestClient
    import main
    c = TestClient(main.app)
    for path, method in (("/api/payment-source", "get"), ("/api/payment-source/connect", "post"), ("/api/payment-source/sync", "post"),
                         ("/api/payment-source/report", "post"), ("/api/payment-source/disconnect", "post")):
        assert getattr(c, method)(path).status_code == 401


def _signed_in(role):
    from fastapi.testclient import TestClient
    import auth
    import main
    email = f"paysource-{role}@example.com"
    with auth.SessionLocal() as s:
        if not s.scalar(select(auth.User).where(auth.User.email == email)):
            s.add(auth.User(email=email, password_hash=auth.hash_password("violet maple harbor 42"), full_name="Pat Test",
                            organization=ORG, role=role, claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
            s.commit()
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "violet maple harbor 42"})
    return c


def test_a_biller_connects_pulls_and_builds_the_report_in_the_app(db):
    import billing_activation as ba
    import auth
    c = _signed_in("biller")
    with auth.SessionLocal() as s:
        org = ba.org_of(s.scalar(select(auth.User).where(auth.User.email == "paysource-biller@example.com")))
        s.query(auth.OrgSettings).filter(auth.OrgSettings.org_key == org, auth.OrgSettings.category == ps.CATEGORY).delete()
        s.query(mon.PaymentFile).filter(mon.PaymentFile.org_key == org).delete()
        s.commit()
    assert c.get("/api/payment-source").json()["connected"] is False
    bad = c.post("/api/payment-source/connect", json={"partner": "claimmd", "key": "WRONGkey1"})
    assert bad.status_code == 400 and "didn't accept" in bad.json()["error"]
    assert c.post("/api/payment-source/connect", json={"partner": "availity", "key": GOOD_KEY}).status_code == 400
    ok = c.post("/api/payment-source/connect", json={"partner": "claimmd", "key": GOOD_KEY})
    assert ok.status_code == 200 and ok.json()["connected"] and GOOD_KEY not in ok.text
    assert c.post("/api/payment-source/report").status_code == 400                # nothing pulled yet: says so
    rates = [_r(f"P{i}", "2026-10-01", [_ok("99214", 131.0, 210.0)]) for i in range(4)]
    FakeClaimMD.eras = {"1": ("2026-10-10", era("2026-10-10", rates + [_r("DEN", "2026-10-02", [_den("99214", 210.0, "16")])]))}
    got = c.post("/api/payment-source/sync").json()
    assert got["kept"] == 1 and got["status"]["filesRead"] == 1
    r = c.post("/api/payment-source/report")
    assert r.status_code == 200 and "Lost Money Report" in r.text and "Recovery" in r.text and "no-store" in r.headers["cache-control"]
    assert _signed_in("viewer").post("/api/payment-source/connect", json={"key": GOOD_KEY}).status_code == 403
    assert c.post("/api/payment-source/disconnect").json()["connected"] is False


def test_the_lost_money_page_offers_the_connection_only_to_clinics():
    assert "connect your Claim.MD account" in lm._page("/api/lost-money/report", "x", saving=True)
    assert "Claim.MD account" not in lm._page("/api/admin/lost-money/report", "staff", saving=False)


# ── deadlines on every dollar ──

def test_every_dollar_has_an_act_by_date_and_the_soonest_are_flagged():
    rates = [_r(f"P{i}", "2026-10-01", [_ok("99214", 131.0, 210.0)]) for i in range(5)]
    short_new = _r("SHORT", "2026-10-03", [_ok("99214", 100.0, 210.0)])
    files = [("a.835", era("2026-10-20", rates + [short_new, _r("DEN", "2026-10-02", [_den("99214", 210.0, "16")])]).encode())]
    a = lm.analyze(files, dt.date(2026, 11, 13))
    under = next(f for f in a.findings if f.category == "underpaid")
    assert under.deadline == "2027-04-18" and under.days_left == 156                # 180 days from the payment, commercial
    den = next(f for f in a.findings if f.category == "denial")
    assert den.deadline and den.days_left > 0
    # the same files read a year later: the underpayment is past its window -> money lost, not money found
    late = lm.analyze(files, dt.date(2027, 11, 13))
    assert not [f for f in late.findings if f.category == "underpaid"]
    assert any(f.category == "lost" and "reconsideration window" in f.title and f.counted is False for f in late.findings)
    # inside 30 days -> flagged at the top of the report
    soon = lm.analyze(files, dt.date(2027, 4, 1)).summary()["act_soon"]
    assert soon["count"] >= 1 and soon["first"] and soon["dollars"] > 0
    html = lm.render_html(lm.analyze(files, dt.date(2027, 4, 1)))
    assert "has to be acted on within 30 days" in html and "Act by" in html
