"""Clinic-ready fixes: the clinic's own appeal windows and contract rates, corrected claims under new numbers, insurer
takebacks (835 PLB), the by-site view, the AMA CPT notices, the setup checklist, data export and deletion, and alerts."""
import datetime as dt
import io
import json
import zipfile

import pytest
from sqlalchemy import select

import clinic_setup as cs
import cpt_notice
import lost_money as lm
import ops_alerts

TODAY = dt.date(2026, 11, 1)


def _line(code, paid, allowed, adjs, charge=150.0, mods=(), units=1):
    return (code, list(mods), charge, paid, allowed, adjs, units)


def _ok(code, amt, charge=150.0, mods=()):
    return _line(code, amt, amt, [("CO", "45", round(charge - amt, 2))] if charge > amt else [], charge, mods)


def _den(code, carc="16", charge=150.0, mods=()):
    return _line(code, 0.0, None, [("CO", carc, charge)], charge, mods)


def _claim(cid, dos, lines, prov="SMITH"):
    return {"id": cid, "dos": dos, "prov": prov, "charge": sum(l[2] for l in lines), "paid": sum(l[3] for l in lines), "pr": 0.0, "lines": lines}


def _era(payer, date, claims, ind="CI", name=None):
    return (name or f"{payer}-{date}.835", lm.build_835(payer, date, claims, ind).encode())


def _find(a, cat):
    return [f for f in a.findings if f.category == cat]


# ── appeal windows ──

def test_deadlines_are_estimates_until_the_clinic_sets_them():
    files = [_era("AETNA LIFE INSURANCE", "2026-10-01", [_claim("D1", "2026-09-20", [_den("99213")])])]
    a = lm.analyze(files, TODAY)
    d = _find(a, "denial")[0]
    assert d.deadline == "2027-03-30" and d.deadline_set is False            # commercial estimate: 180 days
    assert "estimated" in d.why and a.summary()["deadlines_estimated"] == 1
    html = lm.render_html(a)
    assert "· estimated" in html and "/clinic-setup" in html

    terms = lm.ClinicTerms(appeal_days={"Aetna": 90})                        # entered as the start of the name, any case
    d = _find(lm.analyze(files, TODAY, terms), "denial")[0]
    assert d.deadline == "2026-12-30" and d.deadline_set is True
    assert "your clinic's setting" in d.why and "· estimated" not in lm.render_html(lm.analyze(files, TODAY, terms))

    by_type = lm.ClinicTerms(appeal_days={"type:commercial": 60})
    assert _find(lm.analyze(files, TODAY, by_type), "denial")[0].deadline == "2026-11-30"
    # the payer's own setting beats the type's
    both = lm.ClinicTerms(appeal_days={"type:commercial": 60, "AETNA LIFE INSURANCE": 120})
    assert _find(lm.analyze(files, TODAY, both), "denial")[0].deadline == "2027-01-29"


def test_a_short_window_moves_a_denial_to_lost():
    files = [_era("CIGNA", "2026-08-01", [_claim("L1", "2026-07-25", [_den("99213")])])]
    assert _find(lm.analyze(files, TODAY), "denial")
    a = lm.analyze(files, TODAY, lm.ClinicTerms(appeal_days={"CIGNA": 60}))
    assert not _find(a, "denial") and _find(a, "lost")


def test_filing_limit_setting_for_claims_with_no_answer():
    claims = [{"id": "NR1", "dos": "2026-08-20", "payer": "AETNA", "lines": [("99213", [], 150.0, 1)]}]
    files = [("c.837", lm.build_837(claims).encode()), _era("AETNA", "2026-10-15", [_claim("OTHER", "2026-09-01", [_ok("99213", 90.0)])])]
    nr = _find(lm.analyze(files, TODAY), "no_response")
    assert nr and nr[0].deadline_set is False
    nr = _find(lm.analyze(files, TODAY, lm.ClinicTerms(filing_days={"AETNA": 95})), "no_response")
    assert nr[0].deadline == "2026-11-23" and nr[0].deadline_set is True


# ── contract rates ──

def _always_short():
    # Blue Cross pays 99213 at $80 every time: compared with itself, nothing looks wrong
    return [_era("BLUE CROSS", "2026-09-10", [_claim(f"B{i}", "2026-09-01", [_ok("99213", 80.0)]) for i in range(6)], "BL")]


def test_contract_rates_catch_a_payer_that_always_underpays():
    assert not _find(lm.analyze(_always_short(), TODAY), "underpaid")
    terms = lm.ClinicTerms(rates={("BLUE CROSS", "99213", ()): 95.0})
    a = lm.analyze(_always_short(), TODAY, terms)
    u = _find(a, "underpaid")
    assert len(u) == 6 and all(f.dollars == 15.0 and f.confidence == "high" for f in u)
    assert "contracted rate" in u[0].title and a.summary()["found"] >= 90
    assert "contracted rates (1 code rates)" in lm.render_html(a)


def test_contract_rate_is_the_expected_value_for_denials():
    files = [_era("AETNA", "2026-10-01", [_claim("V1", "2026-09-20", [_den("99214")])])]
    a = lm.analyze(files, TODAY, lm.ClinicTerms(rates={("AETNA", "99214", ()): 133.33}))
    assert _find(a, "denial")[0].dollars == 133.33 and "your contracted rate" in _find(a, "denial")[0].why


def test_price_modifiers_keep_their_own_rate():
    files = [_era("AETNA", "2026-09-10", [_claim("X1", "2026-09-01", [_ok("71046", 20.0, mods=("26",))])])]
    a = lm.analyze(files, TODAY, lm.ClinicTerms(rates={("AETNA", "71046", ()): 60.0}))
    assert not _find(a, "underpaid")                                         # the global rate isn't the -26 rate
    a = lm.analyze(files, TODAY, lm.ClinicTerms(rates={("AETNA", "71046", ("26",)): 25.0}))
    assert _find(a, "underpaid")[0].dollars == 5.0


def test_reading_a_fee_schedule():
    rates, problems = cs.parse_contracts("CPT Code,Modifier,Contracted Rate\n99213,,$95.00\n71046,26,25\n99214,25,130\nABC,,5\n87880,,0\n", "Blue Cross")
    assert rates == {"BLUE CROSS": {"99213|": 95.0, "71046|26": 25.0, "99214|": 130.0}}      # 25 doesn't change the price
    assert len(problems) == 2
    rates, _ = cs.parse_contracts("payer\tcode\tallowed\nAetna\t99213\t88.5\nCigna\t99213\t91\n")
    assert rates == {"AETNA": {"99213|": 88.5}, "CIGNA": {"99213|": 91.0}}
    assert cs.parse_contracts("code,rate\n99213,90\n")[1] == ["The file has no payer column: choose which payer this fee schedule is for."]
    assert "column names" in cs.parse_contracts("99213,90\n", "Aetna")[1][0]


# ── a corrected claim under a new number ──

def test_a_denial_paid_later_under_a_new_claim_number_is_not_lost():
    first = _era("AETNA", "2026-09-10", [_claim("ORIG1", "2026-09-01", [_den("99213")])])
    resent = lm.build_835("AETNA", "2026-10-05", [_claim("RESENT1", "2026-09-01", [_ok("99213", 90.0)])], "CI").replace("MI*MRESENT1", "MI*MORIG1")
    a = lm.analyze([first, ("r.835", resent.encode())], TODAY)
    assert not _find(a, "denial")
    # a different patient on the same day isn't the same visit
    other = lm.build_835("AETNA", "2026-10-05", [_claim("RESENT1", "2026-09-01", [_ok("99213", 90.0)])], "CI").replace("DOE*PAT", "ROE*SAM")
    assert _find(lm.analyze([first, ("r.835", other.encode())], TODAY), "denial")


def test_family_members_sharing_a_member_id_are_not_mixed_up():
    kid1 = lm.build_835("AETNA", "2026-09-10", [_claim("K1", "2026-09-01", [_den("87880", charge=30.0)])], "CI").replace("MI*MK1", "MI*SUB9")
    kid2 = lm.build_835("AETNA", "2026-09-10", [_claim("K2", "2026-09-01", [_ok("87880", 20.0, charge=30.0)])], "CI").replace("MI*MK2", "MI*SUB9").replace("DOE*PAT", "DOE*SIS")
    assert _find(lm.analyze([("a.835", kid1.encode()), ("b.835", kid2.encode())], TODAY), "denial")


def test_a_claim_answered_under_another_number_has_an_answer():
    claims = [{"id": "C9", "dos": "2026-08-20", "payer": "AETNA", "lines": [("99213", [], 150.0, 1)]}]
    x837 = lm.build_837(claims)                                                  # patient DOE, PAT; member ID MC9
    paid = lm.build_835("AETNA", "2026-10-15", [_claim("NEWNUM", "2026-08-20", [_ok("99213", 90.0)])], "CI").replace("MI*MNEWNUM", "MI*MC9")
    a = lm.analyze([("c.837", x837.encode()), ("p.835", paid.encode())], TODAY)
    assert not _find(a, "no_response")
    # same name and date, but a different member ID: another patient, so the claim still has no answer
    other = lm.build_835("AETNA", "2026-10-15", [_claim("NEWNUM", "2026-08-20", [_ok("99213", 90.0)])], "CI")
    assert _find(lm.analyze([("c.837", x837.encode()), ("p.835", other.encode())], TODAY), "no_response")


# ── takebacks (835 PLB) ──

def _with_plb(text, plb):
    return text.replace("~SE*", "~" + plb + "~SE*")


def test_insurer_takebacks_are_read_and_not_counted_as_found_money():
    base = lm.build_835("AETNA", "2026-10-01", [_claim("P1", "2026-09-20", [_ok("99213", 90.0)])], "CI")
    era = _with_plb(base, "PLB*1111111112*20261231*WO:ICN555*45.00*L6*-2.10")
    a = lm.analyze([("a.835", era.encode()), ("again.835", era.encode())], TODAY)     # the same file twice: counted once
    t = _find(a, "takeback")
    assert len(t) == 1 and t[0].dollars == 45.0 and t[0].payer_claim_id == "ICN555" and not t[0].counted
    s = a.summary()
    assert s["takebacks"] == 45.0 and s["plb_added"] == 2.10 and s["paid_after_takebacks"] == round(s["paid"] - 45 + 2.10, 2)
    assert s["found"] == 0 and s["possible"] == 0
    assert "Money insurers took back" in lm.render_html(a)


# ── sites ──

def test_findings_by_site_and_provider():
    a1 = lm.build_835("AETNA", "2026-10-01", [_claim("S1", "2026-09-20", [_den("99213")])], "CI")
    a2 = lm.build_835("AETNA", "2026-10-01", [_claim("S2", "2026-09-20", [_den("99214")], prov="PATEL")], "CI").replace("N1*PE*SAMPLE URGENT CARE*XX*1111111112", "N1*PE*SAMPLE URGENT CARE*XX*1999999999")
    terms = lm.ClinicTerms(sites={"1111111112": "Kent Street", "1999999999": "42nd Street"})
    a = lm.analyze([("a.835", a1.encode()), ("b.835", a2.encode())], TODAY, terms)
    sites = {r["name"]: r for r in a.summary()["by_site"] if r["kind"] == "site"}
    assert set(sites) == {"Kent Street", "42nd Street"} and sites["Kent Street"]["denials"] == 1
    assert "By site and provider" in lm.render_html(a)
    csv = lm.worklist_csv(a)
    assert "Kent Street" in csv and "deadline_estimated" in csv and "American Medical Association" in csv


# ── the app: clinic setup, the CPT notice, deletion ──

def _client(email, role="biller", org="Ready Clinic"):
    from fastapi.testclient import TestClient
    import auth
    import main
    with auth.SessionLocal() as s:
        if not s.scalar(select(auth.User).where(auth.User.email == email)):
            s.add(auth.User(email=email, password_hash=auth.hash_password("violet maple harbor 42"), full_name="Pat Test", organization=org,
                            role=role, claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
            s.commit()
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "violet maple harbor 42"})
    return c


def test_clinic_setup_saves_windows_rates_sites_and_the_report_uses_them():
    import auth
    import monitor
    c = _client("ready-biller@example.com")
    assert c.get("/clinic-setup").status_code == 200
    with auth.SessionLocal() as db:
        monitor.add_file(db, "Ready Clinic", "a.835", lm.build_835("BLUE CROSS", "2026-09-10",
                         [_claim(f"R{i}", "2026-09-01", [_ok("99213", 80.0)]) for i in range(4)], "BL"), "upload")
        db.commit()
    v = c.get("/api/clinic-setup").json()
    assert v["payersSeen"][0]["name"] == "BLUE CROSS" and v["npisSeen"][0]["npi"] == "1111111112"
    assert {s["key"]: s["done"] for s in v["checklist"]}["files"] is True
    assert {s["key"]: s["done"] for s in v["checklist"]}["baa"] is False

    bad = c.post("/api/clinic-setup/deadlines", json={"appeal": {"BLUE CROSS": "9000"}})
    assert bad.status_code == 400
    v = c.post("/api/clinic-setup/deadlines", json={"appeal": {"Blue Cross": "90", "type:medicaid": "95"}, "filing": {"BLUE CROSS": ""}}).json()
    assert v["appeal"] == {"BLUE CROSS": 90, "type:medicaid": 95}
    assert {s["key"]: s["done"] for s in v["checklist"]}["deadlines"] is True

    r = c.post("/api/clinic-setup/contracts", files={"file": ("bc.csv", b"code,rate\n99213,95\n")}, data={"payer": "BLUE CROSS", "replace": "1"})
    assert r.status_code == 200 and r.json()["contracts"] == [{"payer": "BLUE CROSS", "rates": 1}]
    c.post("/api/clinic-setup/sites", json={"sites": {"1111111112": "Kent Street", "123": "bad"}})
    with auth.SessionLocal() as db:
        t = cs.terms(db, "Ready Clinic")
        assert t.appeal_days["BLUE CROSS"] == 90 and t.rates[("BLUE CROSS", "99213", ())] == 95.0 and t.sites == {"1111111112": "Kent Street"}
        a, _ = monitor.analyse(db, "Ready Clinic")
        assert len(_find(a, "underpaid")) == 4 and all(f.deadline_set and f.site == "Kent Street" for f in _find(a, "underpaid"))
        db.rollback()

    # the BAA is the clinic admin's to record
    assert c.post("/api/clinic-setup/baa", json={"signedAt": "2026-11-20", "signer": "Dr. A"}).status_code == 403
    admin = _client("ready-admin@example.com", role="admin")
    assert admin.post("/api/clinic-setup/baa", json={"signedAt": "2099-01-01", "signer": "Dr. A"}).status_code == 400
    v = admin.post("/api/clinic-setup/baa", json={"signedAt": "2026-11-20", "signer": "Dr. A, Owner"}).json()
    assert v["baa"]["signer"] == "Dr. A, Owner" and {s["key"]: s["done"] for s in v["checklist"]}["baa"] is True


def test_clinic_setup_is_for_billers_and_admins():
    from fastapi.testclient import TestClient
    import main
    assert TestClient(main.app).get("/api/clinic-setup").status_code == 401
    viewer = _client("ready-viewer@example.com", role="viewer")
    assert viewer.get("/api/clinic-setup").status_code == 403
    assert viewer.post("/api/clinic-setup/deadlines", json={}).status_code == 403


def test_the_cpt_notice_is_accepted_once_before_cpt_pages(monkeypatch):
    monkeypatch.setenv("CPT_NOTICE_ENFORCE", "1")
    c = _client("ready-cpt@example.com", org="Notice Clinic")
    r = c.get("/recovery", follow_redirects=False)
    assert r.status_code == 302 and r.headers["location"].startswith("/cpt-notice?next=/recovery")
    page = c.get("/cpt-notice?next=/recovery")
    assert "American Medical Association" in page.text
    r = c.post("/cpt-notice", data={"next": "/recovery"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/recovery"
    assert c.get("/recovery", follow_redirects=False).status_code == 200
    assert cpt_notice.SHORT in c.get("/recovery").text
    # an outside address can't be the next page
    assert c.post("/cpt-notice", data={"next": "//evil.example"}, follow_redirects=False).headers["location"] == "/v4/overview"
    assert c.get("/api/cpt-notice").json()["final"] is False              # the license's own text isn't installed yet


def test_export_then_delete_a_clinic_with_a_certificate():
    from fastapi.testclient import TestClient
    import auth
    import main
    import monitor
    import recovery
    org = "Leaving Clinic"
    _client("leaving-admin@example.com", role="admin", org=org)
    with auth.SessionLocal() as db:
        monitor.add_file(db, org, "pay.835", lm.build_835("AETNA", "2026-09-10", [_claim("Z1", "2026-09-01", [_den("99213")])], "CI"), "upload")
        db.add(main.OrgPatient(org_key="org_leaving_clinic", mrn="M1", name="Doe, Pat", data="{}")) if hasattr(main, "OrgPatient") else None
        cs.save(db, org, {"appeal": {"AETNA": 90}})
        db.commit()
    adm = TestClient(main.app)
    import os
    adm.post("/admin/login", data={"username": os.environ.get("ADMIN_USERNAME", "admin"), "password": os.environ["ADMIN_PASSWORD"]})
    pv = adm.get(f"/api/admin/clinics/{org}/deletion-preview").json()
    assert pv["rows"]["payment_files"] == 1 and pv["rows"]["org_settings"] >= 1 and "org_leaving_clinic" in pv["keys"]
    z = zipfile.ZipFile(io.BytesIO(adm.get(f"/api/admin/clinics/{org}/export").content))
    names = z.namelist()
    assert "README.txt" in names and "tables/payment_files.json" in names and "tables/users.json" in names
    raw = [n for n in names if n.startswith("files/")]
    assert raw and z.read(raw[0]).startswith(b"ISA")                   # decrypted back to the original 835
    r = adm.request("DELETE", f"/api/admin/clinics/{org}", json={"confirm": org})
    assert r.status_code == 200, r.text
    cert = r.json()["certificate"]
    assert cert["accounts"] == 1 and cert["rows"]["payment_files"] == 1 and "backups" in cert["statement"]
    page = adm.get(r.json()["certificateUrl"])
    assert page.status_code == 200 and "Certificate of data deletion" in page.text
    with auth.SessionLocal() as db:
        assert not db.scalars(select(monitor.PaymentFile).where(monitor.PaymentFile.org_key == org)).all()
        if hasattr(main, "OrgPatient"):
            assert not db.scalars(select(main.OrgPatient).where(main.OrgPatient.org_key == "org_leaving_clinic")).all()
        assert cs.load(db, org) == {}


# ── alerts ──

def test_alerts_are_scrubbed_rate_limited_and_emailed(monkeypatch):
    sent = []
    import auth
    monkeypatch.setattr(auth, "send_email", lambda to, subj, body: sent.append((to, subj, body)) or True)
    monkeypatch.setenv("ALERT_EMAIL", "ops@althais.com")
    ops_alerts._last.clear()
    now = dt.datetime(2026, 11, 24, 12)
    assert ops_alerts.notify("Claim.MD payment pull failed", "Vital Care", RuntimeError("member W123456789 pat@x.com denied"), now=now)
    assert "W[n]" in sent[0][2] and "pat@x.com" not in sent[0][2] and "Vital Care" in sent[0][1]
    assert not ops_alerts.notify("Claim.MD payment pull failed", "Vital Care", RuntimeError("again"), now=now + dt.timedelta(hours=1))
    assert ops_alerts.notify("Claim.MD payment pull failed", "Vital Care", RuntimeError("later"), now=now + dt.timedelta(hours=7))
    assert len(sent) == 2 and ops_alerts.RECENT[0]["severity"] == "ERROR"


def test_a_failed_claimmd_pull_raises_an_alert(monkeypatch):
    import payment_sources as ps
    got = []
    monkeypatch.setattr(ops_alerts, "notify", lambda kind, org="", error=None, **k: got.append((kind, org)) or True)

    class Broken:
        def __init__(self, key, environment="production"):
            pass

        def era_list(self, last):
            raise RuntimeError("Claim.MD is down")
    import auth
    import clearinghouse as chx
    monkeypatch.setattr(ps, "CONNECTOR", Broken)
    with auth.SessionLocal() as db:
        ps._save(db, "Alert Clinic", {"partner": "claimmd", "key": chx.seal("k")})
        assert ps.sync(db, "Alert Clinic")["error"]
        db.rollback()
    assert got == [("Claim.MD payment pull failed", "Alert Clinic")]
