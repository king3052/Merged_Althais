"""The Lost Money Report: reading 835/837/CSV files, each detector, the totals, the report and the upload routes."""
import datetime as dt

import pytest

import lost_money as lm

TODAY = dt.date(2026, 11, 1)

# the 835 Claim.MD's test account returned for Althais's test claims (see test_claimmd_era.py)
CLAIMMD_ERA = ("ISA*00*          *00*          *ZZ*60054          *ZZ*34590          *261102*0257*^*00501*087513364*0*P*:~GS*HP*60054*34590*20261102*0257*1*X*005010X221A1~"
               "ST*835*0001~BPR*I*92.76*C*ACH*CCP*01*111111111*DA*700000000*1000000000*000060054*01*111111111*DA*111111111*20261102~"
               "TRN*1*897419465-1793653036*1000000000*000060054~N1*PR*AETNA*XV*60054~REF*2U*60054~N1*PE*SAMPLE PRACTICE*XX*1111111112~"
               "LX*1~CLP*ALT-TEST-CLEAN-1102*1*134.7*92.76*15*HM*TST897419465~NM1*QC*1*TESTPATIENT*ALPHA~NM1*IL*1*TESTPATIENT*ALPHA****MI*W100200300~"
               "SVC*HC:99213*118.2*79.56**1~DTM*472*20261030~CAS*CO*45*23.64~CAS*PR*2*10**3*5~REF*6R*1529382178~AMT*B6*94.56~"
               "SVC*HC:87880*16.5*13.2**1~DTM*472*20261030~CAS*CO*45*3.3~REF*6R*1529382179~AMT*B6*13.2~"
               "LX*3~CLP*ALT-TEST-DENY-1102*4*158.7*0*30*HM*TST897419942~NM1*QC*1*TESTPATIENT*CHARLIE~NM1*IL*1*TESTPATIENT*CHARLIE****MI*DENY~"
               "SVC*HC:99213*118.2*0**1~CAS*OA*109*118.2~SVC*HC:73610*40.5*0**1~CAS*OA*109*40.5~SE*30*0001~GE*1*1~IEA*1*087513364~")


def _line(code, paid, allowed, adjs, charge=150.0, mods=(), units=1):
    return (code, list(mods), charge, paid, allowed, adjs, units)


def _era(payer, date, claims, ind="CI"):
    return (f"{payer}-{date}.835", lm.build_835(payer, date, claims, ind).encode())


def _claim(cid, dos, lines, paid=None, pr=0.0, prov="SMITH"):
    return {"id": cid, "dos": dos, "prov": prov, "charge": sum(l[2] for l in lines),
            "paid": sum(l[3] for l in lines) if paid is None else paid, "pr": pr, "lines": lines}


def _ok(code, amt, charge=150.0, mods=()):
    return _line(code, amt, amt, [("CO", "45", round(charge - amt, 2))] if charge > amt else [], charge, mods)


def _find(a, cat):
    return [f for f in a.findings if f.category == cat]


# ── reading files ──
def test_reads_claimmd_835_payment_and_denial():
    a = lm.analyze([("claimmd.835", CLAIMMD_ERA.encode())], TODAY)
    assert {r.claim_id for r in a.remits} == {"ALT-TEST-CLEAN-1102", "ALT-TEST-DENY-1102"}
    clean = next(r for r in a.remits if r.claim_id == "ALT-TEST-CLEAN-1102")
    assert clean.paid == 92.76 and clean.date == "2026-11-02" and clean.lines[0].allowed == 94.56
    assert [(x.group, x.carc, x.amount) for x in clean.lines[0].adjustments] == [("CO", "45", 23.64), ("PR", "2", 10.0), ("PR", "3", 5.0)]
    denied = _find(a, "denial")
    assert len(denied) == 2 and all("109" in f.why for f in denied)
    assert all("right payer" in f.action for f in denied)
    assert a.summary()["patient_owed"] == 45.0


def test_reads_837_and_concatenated_files_and_zip():
    import io
    import zipfile
    x837 = lm.build_837([{"id": "C1", "payer": "AETNA", "dos": "2026-08-01", "lines": [("99214", ["25"], 210.0, 1), ("12002", [], 140.0, 1)]}])
    x835 = lm.build_835("AETNA", "2026-08-20", [_claim("C1", "2026-08-01", [_ok("99214", 131.0, 210.0, ["25"]), _ok("12002", 140.0, 140.0)])])
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("claims.txt", x837 + "\n" + x837.replace("C1", "C2"))       # two interchanges in one file
        z.writestr("era/aetna.835", x835)
    a = lm.analyze([("export.zip", buf.getvalue())], TODAY)
    assert sorted(a.claims) == ["C1", "C2"]
    c = a.claims["C1"]
    assert c.payer == "AETNA" and c.dos == "2026-08-01" and c.lines[0].mods == ["25"] and c.provider.startswith("Alex Smith")
    assert len(a.remits) == 1 and a.remits[0].lines[0].mods == ["25"]
    assert {f["kind"] for f in a.ds.files} == {"837 claims", "835 payments"}


def test_reads_a_csv_export_with_its_own_column_names():
    csv = ("Encounter ID,Service Date,Primary Insurance,CPT Code,Modifier,Units,Billed Amount,Insurance Paid,Allowed Amount,Denial Code\n"
           "E1,08/03/2026,Cigna,99213,,1,145.00,0,,CO-16\n"
           "E1,08/03/2026,Cigna,87880,QW,1,45.00,16.00,16.00,\n"
           "E2,08/04/2026,Cigna,99214,,1,210.00,,,\n")
    a = lm.analyze([("export.csv", csv.encode())], TODAY)
    assert set(a.claims) == {"E1", "E2"} and a.claims["E1"].dos == "2026-08-03"
    assert [r.claim_id for r in a.remits] == ["E1"]                     # E2 has no answer yet
    d = _find(a, "denial")
    assert len(d) == 1 and d[0].code == "99213" and "Fix and resubmit" in d[0].action


def test_bad_files_are_reported_not_fatal():
    a = lm.analyze([("notes.pdf", b"%PDF-1.7 ..."), ("x.835", b"ISA*broken")], TODAY)
    assert not a.remits and all(f["error"] for f in a.ds.files)


# ── detectors ──
def test_denial_uses_the_payers_own_rate_and_sets_a_deadline():
    claims = [_claim(f"P{i}", "2026-08-01", [_ok("99214", 131.0, 210.0)]) for i in range(4)]
    claims.append(_claim("D1", "2026-08-02", [_line("99214", 0.0, None, [("CO", "197", 210.0)], 210.0)]))
    a = lm.analyze([_era("AETNA", "2026-08-20", claims)], TODAY)
    d = _find(a, "denial")[0]
    assert d.dollars == 131.0 and "usual rate" in d.why and "authorization" in d.action
    assert d.deadline == "2027-02-16" and d.days_left == 107 and d.counted


def test_expired_and_timely_filing_denials_are_lost_not_found():
    claims = [_claim("T1", "2026-01-05", [_line("99213", 0.0, None, [("CO", "29", 145.0)], 145.0)]),
              _claim("X1", "2026-01-05", [_line("99213", 0.0, None, [("CO", "16", 145.0)], 145.0)])]
    a = lm.analyze([_era("MEDICARE PART B", "2026-02-01", claims, "MB")], TODAY)       # Medicare: 120 days, long gone
    assert not _find(a, "denial") and len(_find(a, "lost")) == 2
    s = a.summary()
    assert s["found"] == 0 and s["lost"] > 0


def test_denial_paid_later_by_another_payer_is_not_lost():
    first = _era("AETNA", "2026-08-10", [_claim("S1", "2026-08-01", [_line("99213", 0.0, None, [("OA", "22", 145.0)], 145.0)])])
    second = _era("CIGNA", "2026-08-30", [_claim("S1", "2026-08-01", [_ok("99213", 90.0, 145.0)])])
    a = lm.analyze([first, second], TODAY)
    assert not _find(a, "denial")


def test_reversed_payment_is_dropped():
    paid = _claim("R1", "2026-08-01", [_ok("99213", 90.0, 145.0)])
    a = lm.analyze([_era("AETNA", "2026-08-10", [paid])], TODAY)
    assert len(a.remits) == 1
    rev = lm.build_835("AETNA", "2026-08-20", [paid]).replace("CLP*R1*1*", "CLP*R1*22*")
    a = lm.analyze([_era("AETNA", "2026-08-10", [paid]), ("rev.835", rev.encode())], TODAY)
    assert not a.remits


def test_underpayment_against_the_payers_usual_rate():
    claims = [_claim(f"U{i}", "2026-08-0" + str(i + 1), [_ok("99214", 131.0, 210.0)]) for i in range(5)]
    claims.append(_claim("LOW", "2026-08-09", [_ok("99214", 111.35, 210.0)]))
    a = lm.analyze([_era("AETNA", "2026-08-20", claims)], TODAY)
    u = _find(a, "underpaid")
    assert len(u) == 1 and u[0].claim_id == "LOW" and u[0].dollars == 19.65 and u[0].confidence == "high"


def test_no_underpayment_without_a_settled_rate_or_for_multiple_procedure_cuts():
    claims = [_claim(f"V{i}", "2026-08-01", [_ok("99214", 120.0 + i * 3, 210.0)]) for i in range(5)]      # every amount different
    claims += [_claim(f"W{i}", "2026-08-01", [_ok("12002", 150.0, 200.0)]) for i in range(4)]
    claims.append(_claim("MP", "2026-08-01", [_line("12002", 75.0, 75.0, [("CO", "45", 50.0), ("CO", "59", 75.0)], 200.0)]))
    a = lm.analyze([_era("AETNA", "2026-08-20", claims)], TODAY)
    assert not _find(a, "underpaid")


def test_sequestration_is_not_an_underpayment():
    claims = [_claim(f"M{i}", "2026-08-01", [_line("99213", 87.71, 89.5, [("CO", "45", 55.5), ("CO", "253", 1.79)], 145.0)]) for i in range(5)]
    a = lm.analyze([_era("MEDICARE PART B", "2026-08-20", claims, "MB")], TODAY)
    assert not _find(a, "underpaid") and not _find(a, "denial")


def test_price_below_what_payers_allow():
    claims = [_claim(f"L{i}", "2026-08-01", [_line("12002", 120.0, 120.0, [], 120.0)]) for i in range(3)]        # paid the full charge
    claims.append(_claim("H1", "2026-08-02", [_ok("12002", 160.0, 200.0)]))                                     # charged more elsewhere: allowed 160
    a = lm.analyze([_era("AETNA", "2026-08-20", claims)], TODAY)
    lp = _find(a, "low_price")
    assert lp and lp[0].confidence == "medium" and lp[0].dollars == 120.0 and "lesser" in lp[0].action


def test_addon_flagged_only_when_that_payer_pays_it_and_only_on_weekends():
    paid_addon = [_claim(f"A{i}", "2026-08-01", [_ok("99213", 92.0, 145.0), _ok("99051", 24.0, 50.0)]) for i in range(2)]   # Saturday
    sat = _claim("SAT", "2026-08-08", [_ok("99213", 92.0, 145.0)])
    tue = _claim("TUE", "2026-08-04", [_ok("99213", 92.0, 145.0)])
    other = _claim("OTH", "2026-08-08", [_ok("99213", 90.0, 145.0)])
    a = lm.analyze([_era("AETNA", "2026-08-20", paid_addon + [sat, tue]), _era("CIGNA", "2026-08-20", [other])], TODAY)
    m = [f for f in _find(a, "missed") if f.code == "99051"]
    assert [f.claim_id for f in m] == ["SAT"] and m[0].dollars == 24.0


def test_drug_and_admin_pairs_and_venipuncture():
    claims = [_claim("INJ", "2026-08-01", [_ok("99213", 92.0, 145.0, ["25"]), _ok("96372", 28.0, 45.0)]),
              _claim("DRUG", "2026-08-01", [_ok("99213", 92.0, 145.0), _ok("J1885", 12.0, 50.0)]),
              _claim("NEB", "2026-08-01", [_ok("99213", 92.0, 145.0), _ok("J7613", 1.9, 15.0)]),
              _claim("LAB", "2026-08-01", [_ok("99213", 92.0, 145.0), _ok("85025", 9.0, 35.0)]),
              _claim("FINE", "2026-08-01", [_ok("99213", 92.0, 145.0, ["25"]), _ok("96372", 28.0, 45.0), _ok("J1885", 12.0, 50.0)])]
    a = lm.analyze([_era("AETNA", "2026-08-20", claims)], TODAY)
    got = {(f.claim_id, f.title) for f in _find(a, "missed")}
    assert ("INJ", "Injection given, no drug billed") in got
    assert ("DRUG", "Drug billed, no injection fee") in got
    assert ("NEB", "Nebulizer medicine billed, no treatment code") in got
    assert ("LAB", "Blood test billed, no blood draw") in got
    assert not any(c == "FINE" for c, _ in got)


def test_claims_with_no_answer():
    x837 = lm.build_837([{"id": "SENT1", "payer": "AETNA", "dos": "2026-07-10", "lines": [("99213", [], 145.0, 1)]},
                         {"id": "SENT2", "payer": "AETNA", "dos": "2026-08-01", "lines": [("99213", [], 145.0, 1)]},
                         {"id": "NEW", "payer": "AETNA", "dos": "2026-09-01", "lines": [("99213", [], 145.0, 1)]}])
    era = _era("AETNA", "2026-09-10", [_claim("SENT2", "2026-08-01", [_ok("99213", 92.0, 145.0)])])
    july = _era("AETNA", "2026-07-20", [_claim("OTHER", "2026-07-01", [_ok("99213", 92.0, 145.0)])])   # the files run from July
    a = lm.analyze([("c.837", x837.encode()), era, july], TODAY)
    nr = _find(a, "no_response")
    assert [f.claim_id for f in nr] == ["SENT1"] and nr[0].dollars == 92.0


def test_coding_risks():
    x837 = lm.build_837([
        {"id": "OLD", "payer": "AETNA", "dos": "2026-08-01", "lines": [("99201", [], 100.0, 1)]},
        {"id": "CONS", "payer": "MEDICARE PART B", "ind": "MB", "dos": "2026-08-01", "lines": [("99243", [], 200.0, 1)]},
        {"id": "QW", "payer": "MEDICARE PART B", "ind": "MB", "dos": "2026-08-01", "lines": [("99213", [], 145.0, 1), ("87880", [], 45.0, 1)]},
        {"id": "QWOK", "payer": "AETNA", "dos": "2026-08-01", "lines": [("99213", [], 145.0, 1), ("87880", [], 45.0, 1)]},
        {"id": "NO25", "payer": "AETNA", "dos": "2026-08-01", "lines": [("99213", [], 145.0, 1), ("12002", [], 140.0, 1)]},
        {"id": "HAS25", "payer": "AETNA", "dos": "2026-08-01", "lines": [("99213", ["25"], 145.0, 1), ("12002", [], 140.0, 1)]},
    ])
    a = lm.analyze([("c.837", x837.encode())], TODAY)
    got = {f.claim_id for f in _find(a, "coding")}
    assert got == {"OLD", "CONS", "QW", "NO25"}
    assert all(not f.counted for f in _find(a, "coding"))


def test_visit_levels_against_the_benchmark_and_by_provider():
    claims = []
    for i in range(40):
        prov = "PATEL" if i < 15 else "SMITH"
        code = "99213" if (prov == "PATEL" or i % 2) else "99214"
        claims.append({"id": f"V{i}", "payer": "AETNA", "dos": "2026-08-01", "prov": prov, "lines": [(code, [], 150.0, 1)]})
    pay = [_claim(f"V{i}", "2026-08-01", [_ok(c["lines"][0][0], 92.0 if c["lines"][0][0] == "99213" else 131.0, 150.0)])
           for i, c in enumerate(claims)]
    a = lm.analyze([("c.837", lm.build_837(claims).encode()), _era("AETNA", "2026-08-20", pay)], TODAY)
    lv = _find(a, "levels")
    clinic = next(f for f in lv if "benchmark" in f.title)
    assert a.levels["total"] == 40 and clinic.confidence == "low" and not clinic.counted
    excess = round(a.levels["counts"]["99213"] - lm.LEVEL3_BENCHMARK * 40)
    assert clinic.dollars == excess * 39.0                                   # the clinic's own 99214 - 99213 difference
    assert any(f.title.startswith("Provider Riya Patel") for f in lv)


def test_payer_rules_from_repeated_denials():
    claims = [_claim(f"B{i}", "2026-08-01", [_line("99213", 0.0, None, [("CO", "97", 145.0)], 145.0), _ok("96372", 28.0, 45.0)]) for i in range(3)]
    a = lm.analyze([_era("BLUE CROSS", "2026-08-20", claims)], TODAY)
    rule = a.rules[0]
    assert rule["code"] == "99213" and rule["carc"] == "97" and rule["count"] == 3 and "modifier 25" in rule["check"]


# ── the whole report ──
def test_sample_report_end_to_end():
    a = lm.analyze(lm.sample_files(), TODAY)
    s = a.summary()
    assert s["visits"] == 240 and s["period"]["days"] == 90
    for cat in ("denial", "underpaid", "missed", "no_response", "coding", "levels"):
        assert s["categories"].get(cat, {}).get("count"), cat
    assert s["found"] > 1000 and s["found_annual"] == round(s["found"] * 365 / 90, 2)
    counted = sum(f.dollars for f in a.findings if f.counted and f.confidence in ("high", "medium"))
    assert abs(counted - s["found"]) < 0.01
    page = lm.render_html(a, clinic="Sample Urgent Care", sample=True)
    assert "Lost Money Report" in page and "SAMPLE DATA" in page and "TESTPATIENT" not in page and "DOE" not in page
    assert lm.worklist_csv(a).splitlines()[0].startswith("section,item,confidence,dollars")


def test_sample_is_deterministic():
    assert lm.analyze(lm.sample_files(), TODAY).summary()["found"] == lm.analyze(lm.sample_files(), TODAY).summary()["found"]


def test_command_line(tmp_path):
    out = tmp_path / "r.html"
    assert lm.main(["--sample", "-o", str(out)]) == 0
    assert out.exists() and (tmp_path / "r-worklist.csv").exists()


# ── in the app ──
@pytest.fixture()
def app_client():
    from fastapi.testclient import TestClient
    import main
    return TestClient(main.app)


def _signed_in(role):
    from fastapi.testclient import TestClient
    from sqlalchemy import select
    import auth
    import main
    email = f"lostmoney-{role}@example.com"
    with auth.SessionLocal() as db:
        if not db.scalar(select(auth.User).where(auth.User.email == email)):
            db.add(auth.User(email=email, password_hash=auth.hash_password("violet maple harbor 42"), full_name="Pat Test",
                             organization="Lost Money Clinic", role=role, claims_submitted=0, onboarding_complete=1, active=1, email_verified=1))
            db.commit()
    c = TestClient(main.app)
    c.post("/login", data={"email": email, "password": "violet maple harbor 42"})
    return c


def test_routes_need_sign_in(app_client):
    r = app_client.get("/lost-money", follow_redirects=False)
    assert r.status_code in (302, 303) and "/login" in r.headers["location"]
    assert app_client.post("/api/lost-money/report", files=[("files", ("a.835", CLAIMMD_ERA.encode()))]).status_code == 401
    assert app_client.post("/api/admin/lost-money/report", files=[("files", ("a.835", CLAIMMD_ERA.encode()))]).status_code == 401
    assert app_client.get("/admin/lost-money", follow_redirects=False).status_code in (302, 303)


def test_biller_builds_a_report_and_viewer_cannot():
    c = _signed_in("biller")
    assert c.get("/lost-money").status_code == 200
    r = c.post("/api/lost-money/report", data={"clinic": "Test"}, files=[("files", ("a.835", CLAIMMD_ERA.encode()))])
    assert r.status_code == 200 and "Lost Money Report" in r.text and "no-store" in r.headers.get("cache-control", "")
    j = c.post("/api/lost-money/report", data={"fmt": "json"}, files=[("files", ("a.835", CLAIMMD_ERA.encode()))]).json()
    assert j["summary"]["payments"] == 2 and len([f for f in j["findings"] if f["category"] == "denial"]) == 2
    bad = c.post("/api/lost-money/report", files=[("files", ("a.txt", b"hello"))])
    assert bad.status_code == 400 and "Nothing to analyse" in bad.json()["error"]
    v = _signed_in("viewer")
    assert v.post("/api/lost-money/report", files=[("files", ("a.835", CLAIMMD_ERA.encode()))]).status_code == 403
