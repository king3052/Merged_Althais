"""Fix it (recovery.py): corrected claims rebuilt from the clinic's 837, letters, cases opened from a Lost Money Report,
sending through the clearinghouse, and cases closing themselves when the payment comes in."""
import datetime as dt
import json

import pytest
from sqlalchemy import select

import lost_money as lm
import recovery as rc

TODAY = dt.date(2026, 11, 1)


def _ok(code, amt, charge, mods=()):
    return (code, list(mods), charge, amt, amt, [("CO", "45", round(charge - amt, 2))] if charge > amt else [], 1)


def _denied(code, charge, carc, mods=()):
    return (code, list(mods), charge, 0.0, None, [("CO", carc, charge)], 1)


def _remit(cid, dos, lines):
    return {"id": cid, "dos": dos, "prov": "SMITH", "charge": sum(l[2] for l in lines), "paid": sum(l[3] for l in lines), "pr": 0.0, "lines": lines}


CLAIMS = [
    {"id": "BUN1", "payer": "BLUE CROSS", "ind": "BL", "dos": "2026-08-03", "lines": [("99213", [], 145.0, 1), ("96372", [], 45.0, 1), ("J1885", [], 25.0, 1)]},
    {"id": "QW1", "payer": "MEDICARE PART B", "ind": "MB", "dos": "2026-08-04", "lines": [("99213", [], 145.0, 1), ("87880", [], 45.0, 1)]},
    {"id": "NEB1", "payer": "BLUE CROSS", "ind": "BL", "dos": "2026-08-05", "lines": [("99214", ["25"], 210.0, 1), ("J7613", [], 15.0, 1)]},
    {"id": "MED1", "payer": "BLUE CROSS", "ind": "BL", "dos": "2026-08-06", "lines": [("99214", [], 210.0, 1)]},
    {"id": "GONE", "payer": "BLUE CROSS", "ind": "BL", "dos": "2026-07-01", "lines": [("99213", [], 145.0, 1)]},
] + [{"id": f"OK{i}", "payer": "BLUE CROSS", "ind": "BL", "dos": "2026-08-02", "lines": [("99214", [], 210.0, 1), ("94640", [], 60.0, 1)]} for i in range(3)]


def files(extra=()):
    bc = [_remit("BUN1", "2026-08-03", [_denied("99213", 145.0, "97"), _ok("96372", 25.0, 45.0), _ok("J1885", 5.8, 25.0)]),
          _remit("NEB1", "2026-08-05", [_ok("99214", 124.0, 210.0, ["25"]), _ok("J7613", 1.5, 15.0)]),
          _remit("MED1", "2026-08-06", [_denied("99214", 210.0, "50")])]
    bc += [_remit(f"OK{i}", "2026-08-02", [_ok("99214", 124.0, 210.0), _ok("94640", 20.0, 60.0)]) for i in range(3)]
    mc = [_remit("QW1", "2026-08-04", [_ok("99213", 89.5, 145.0), _denied("87880", 45.0, "4")])]
    out = [("claims.837", lm.build_837(CLAIMS).encode()),
           ("bc.835", lm.build_835("BLUE CROSS", "2026-08-20", bc, "BL").encode()),
           ("mc.835", lm.build_835("MEDICARE PART B", "2026-08-21", mc, "MB").encode())]
    return out + list(extra)


def analysis(extra=()):
    return lm.analyze(files(extra), TODAY)


def _case_for(a, cid, cat="denial"):
    return next(f for f in a.findings if f.claim_id == cid and f.category == cat)


# ── corrected claims ──
def test_findings_carry_the_fix():
    a = analysis()
    assert _case_for(a, "BUN1").fix == "add_modifier:99213:25" and _case_for(a, "BUN1").carc == "97"
    assert _case_for(a, "QW1").fix == "add_modifier:87880:QW"
    assert _case_for(a, "MED1").fix == "" and rc.kind_for(_case_for(a, "MED1")) == "appeal"
    neb = _case_for(a, "NEB1", "missed")
    assert neb.fix == "add_line:94640:60.0"
    assert _case_for(a, "GONE", "no_response").fix == "resend"


def test_corrected_claim_rebuilt_from_the_original():
    a = analysis()
    raw = a.claims["BUN1"].raw
    edi = rc.build_corrected(raw, [{"op": "add_modifier", "code": "99213", "mod": "25"}], "ICNBUN1", "7")
    assert "CLM*BUN1*215.00***20:B:7*" in edi and "REF*F8*ICNBUN1" in edi
    assert edi.index("REF*F8") > edi.index("CLM*BUN1") and edi.index("REF*F8") < edi.index("HI*")
    back = lm.parse_837p(edi.replace("\n", ""))
    assert len(back) == 1 and back[0].claim_id == "BUN1" and back[0].payer == "BLUE CROSS"
    assert [(l.code, l.mods) for l in back[0].lines] == [("99213", ["25"]), ("96372", []), ("J1885", [])]
    segs = [s for s in edi.replace("\n", "").split("~") if s]
    st, se = next(i for i, s in enumerate(segs) if s.startswith("ST*")), next(i for i, s in enumerate(segs) if s.startswith("SE*"))
    assert segs[se].split("*")[1] == str(se - st + 1)                 # SE counts every segment from ST to SE
    assert [s for s in segs if s.startswith("HL*")] == ["HL*1**20*1", "HL*2*1*22*0"]
    assert edi.startswith("ISA*00*") and len(segs[0]) == 105            # the ISA is fixed width (106 with its terminator)


def test_add_line_member_id_and_resend():
    raw = analysis().claims["NEB1"].raw
    edi = rc.build_corrected(raw, [{"op": "add_line", "code": "94640", "charge": 60.0, "units": 1},
                                   {"op": "set_member_id", "value": "xyz-123"}], "ICNNEB1", "7")
    c = lm.parse_837p(edi.replace("\n", ""))[0]
    assert [l.code for l in c.lines] == ["99214", "J7613", "94640"] and c.lines[2].dos == "2026-08-05" and c.charge == 285.0
    assert c.member_id == "XYZ-123" and "LX*3" in edi
    again = rc.build_corrected(raw, [], "", "1")
    assert ":B:1*" in again and "REF*F8" not in again
    with pytest.raises(ValueError):
        rc.build_corrected(raw, [], "", "7")                         # a replacement needs the payer's claim number
    with pytest.raises(ValueError):
        rc.build_corrected(raw, [{"op": "add_modifier", "code": "99999", "mod": "25"}], "X", "7")
    with pytest.raises(ValueError):
        rc.build_corrected({}, [], "X", "7")


def test_claimmd_envelope_for_sending():
    raw = analysis().claims["BUN1"].raw
    edi = rc.build_corrected(raw, [], "ICNBUN1", "7", for_claimmd=True)
    assert "*ZZ*ALTHAIS        *ZZ*CLAIMMD        *" in edi and "GS*HC*ALTHAIS*CLAIMMD*" in edi and "NM1*40*2*CLAIMMD" in edi
    mine = rc.build_corrected(raw, [], "ICNBUN1", "7")
    assert "*ZZ*SENDER         *ZZ*RECEIVER       *" in mine           # the clinic's own clearinghouse IDs kept


# ── letters ──
def test_letters_leave_only_what_the_note_must_say():
    a = analysis()
    case = rc.RecoveryCase(org_key="x", dedupe="d", category="denial", kind="appeal", claim_id="MED1", payer="BLUE CROSS", dos="2026-08-06",
                           code="99214", carc="50", payer_claim_id="ICNMED1", expected=124.0)
    prof = {"legalName": "Sample Urgent Care", "npi": "1111111112", "address": {"line1": "1 Main", "city": "Austin", "state": "TX", "zip": "78701"},
            "contact": {"name": "Pat Biller", "email": "billing@example.com"}}
    t = rc.draft_letter(case, {"patient": "DOE, PAT", "member_id": "M1"}, prof)
    assert "DOE, PAT" in t and "ICNMED1" in t and "reason code 50 (Not deemed medically necessary)" in t and "Sample Urgent Care, NPI 1111111112" in t
    left = rc.placeholders(t)
    assert len(left) == 2 and any("visit note" in x for x in left)      # the payer's address and the clinical summary
    rec = rc.RecoveryCase(org_key="x", dedupe="e", category="underpaid", kind="reconsideration", claim_id="U1", payer="AETNA", code="99214",
                          expected=19.65, why="Allowed $111.35; AETNA allowed $131.00 per unit on 5 of 6 other 99214 lines that year.")
    t2 = rc.draft_letter(rec, {"evidence": ["ICN1 (2026-08-01)", "ICN2 (2026-08-02)"]}, prof)
    assert "ICN1 (2026-08-01); ICN2 (2026-08-02)" in t2 and "$19.65" in t2
    pdf = rc.letter_pdf(t2.replace("[", "(").replace("]", ")") + "\n“quotes” — dash")
    assert pdf[:5] == b"%PDF-"


# ── cases ──
@pytest.fixture()
def db():
    with rc.SessionLocal() as s:
        s.query(rc.RecoveryCase).filter(rc.RecoveryCase.org_key.like("Recovery Test%")).delete(synchronize_session=False)
        s.commit()
        yield s
        s.rollback()


def test_cases_open_once_and_close_when_paid(db):
    a = analysis()
    out = rc.save_cases(db, "Recovery Test", "pat@example.com", a)
    db.commit()
    cases = {(c.claim_id, c.category): c for c in db.scalars(select(rc.RecoveryCase).where(rc.RecoveryCase.org_key == "Recovery Test"))}
    assert out["created"] == len(cases) and {("BUN1", "denial"), ("QW1", "denial"), ("MED1", "denial"), ("NEB1", "missed"), ("GONE", "no_response")} <= set(cases)
    assert cases[("BUN1", "denial")].status == "READY" and cases[("BUN1", "denial")].kind == "corrected"
    assert cases[("MED1", "denial")].status == "OPEN" and cases[("MED1", "denial")].kind == "appeal"
    assert cases[("GONE", "no_response")].kind == "resend" and cases[("GONE", "no_response")].status == "READY"
    doc = rc._open(cases[("BUN1", "denial")])
    assert doc["patient"] == "DOE, PAT" and doc["raw"]["clm"][0][1] == "BUN1"
    assert "DOE" not in cases[("BUN1", "denial")].sealed                # stored encrypted
    again = rc.save_cases(db, "Recovery Test", "pat@example.com", analysis())
    assert again["created"] == 0 and again["recovered"] == 0             # the same files change nothing
    # a month later: the corrected claim is paid, the appeal is denied again, the resent claim is paid
    later = lm.build_835("BLUE CROSS", "2026-09-20", [_remit("BUN1", "2026-08-03", [_ok("99213", 88.0, 145.0, ["25"]), _ok("96372", 25.0, 45.0)]),
                                                      _remit("MED1", "2026-08-06", [_denied("99214", 210.0, "50")]),
                                                      _remit("GONE", "2026-07-01", [_ok("99213", 88.0, 145.0)])], "BL")
    out = rc.save_cases(db, "Recovery Test", "pat@example.com", analysis([("later.835", later.encode())]))
    db.commit()
    for c in cases.values():
        db.refresh(c)
    assert cases[("BUN1", "denial")].status == "RECOVERED" and cases[("BUN1", "denial")].recovered == 88.0
    assert cases[("GONE", "no_response")].status == "RECOVERED"
    assert cases[("MED1", "denial")].status == "DENIED_AGAIN"
    assert out["recovered"] == 2 and out["recovered_dollars"] == 176.0
    assert "Paid on 2026-09-20" in cases[("BUN1", "denial")].history


def test_era_through_althais_closes_a_sent_case(db):
    rc.save_cases(db, "Recovery Test ERA", "pat@example.com", analysis())
    case = db.scalar(select(rc.RecoveryCase).where(rc.RecoveryCase.org_key == "Recovery Test ERA", rc.RecoveryCase.claim_id == "QW1"))
    case.status, case.basis_date, case.sent_via = "SENT", "2026-09-01", "althais"
    db.commit()
    era = lm.build_835("MEDICARE PART B", "2026-09-15", [_remit("QW1", "2026-08-04", [_ok("87880", 16.0, 45.0, ["QW"])])], "MB")
    assert rc.on_era(db, era)["recovered"] == 1
    db.commit()
    db.refresh(case)
    assert case.status == "RECOVERED" and case.recovered == 16.0
    assert rc.on_era(db, "not an era") == {"recovered": 0, "denied_again": 0, "dollars": 0.0}
    # the same claim number at another clinic (a different billing NPI) is left alone
    other = db.scalar(select(rc.RecoveryCase).where(rc.RecoveryCase.org_key == "Recovery Test ERA", rc.RecoveryCase.claim_id == "BUN1"))
    other.status, other.basis_date, other.sent_via = "SENT", "2026-09-01", "althais"
    db.commit()
    era2 = lm.build_835("BLUE CROSS", "2026-09-16", [_remit("BUN1", "2026-08-03", [_ok("99213", 88.0, 145.0, ["25"])])], "BL").replace("XX*1111111112", "XX*1999999999")
    assert rc.on_era(db, era2)["recovered"] == 0


# ── the app ──
def _client(email, role="biller", org="Recovery Test Clinic"):
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


def test_report_to_recovery_to_send_to_paid(monkeypatch):
    import billing_activation as ba
    import auth
    c = _client("recovery-biller@example.com")
    with auth.SessionLocal() as s:
        org = ba.org_of(s.scalar(select(auth.User).where(auth.User.email == "recovery-biller@example.com")))
        s.query(rc.RecoveryCase).filter(rc.RecoveryCase.org_key == org).delete()
        s.commit()
    monkeypatch.setenv("BILLING_SANDBOX_ORGS", org)                      # test mode: the sandbox clearinghouse
    r = c.post("/api/lost-money/report", data={"save": "1"}, files=[("files", (n, b)) for n, b in files()])
    assert r.status_code == 200 and "saved to Recovery" in r.text
    j = c.get("/api/recovery/cases").json()
    assert j["totals"]["openCount"] >= 5 and j["totals"]["recovered"] == 0
    bun = next(x for x in j["cases"] if x["claimId"] == "BUN1")
    d = c.get(f"/api/recovery/cases/{bun['id']}").json()
    assert d["hasClaim"] and d["edits"] == [{"op": "add_modifier", "code": "99213", "mod": "25"}] and d["send"]["ok"] and d["send"].get("test")
    assert "letter" not in d                                              # a corrected claim with the fix applied needs no letter
    neb = next(x for x in j["cases"] if x["claimId"] == "NEB1")
    assert neb["payerClaimId"] == "ICNNEB1" and neb["status"] == "READY"   # a missed charge takes the payer's claim number from the payment
    f = c.post(f"/api/recovery/cases/{bun['id']}/claim", json={})
    assert f.status_code == 200 and "REF*F8*ICNBUN1" in f.text and "99213:25" in f.text
    assert c.post(f"/api/recovery/cases/{bun['id']}/send", json={}).status_code == 400            # must confirm
    s = c.post(f"/api/recovery/cases/{bun['id']}/send", json={"confirm": True}).json()
    assert s["status"] == "sent" and s["test"]
    assert c.post(f"/api/recovery/cases/{bun['id']}/send", json={"confirm": True}).json()["status"] == "already_sent"
    med = next(x for x in j["cases"] if x["claimId"] == "MED1")
    assert c.get(f"/api/recovery/cases/{med['id']}/letter.pdf").status_code == 400                 # [brackets] left
    md = c.get(f"/api/recovery/cases/{med['id']}").json()
    letter = md["letter"]
    assert "Sample Urgent Care, NPI 1111111112" in letter and not md["send"]["ok"]                 # the claim's billing provider fills the profile
    filled = letter.replace("[Payer appeals address or fax]", "PO Box 1, Dallas TX")
    for p in rc.placeholders(filled):
        filled = filled.replace(p, "Documented in the note.")
    assert c.post(f"/api/recovery/cases/{med['id']}/letter", json={"text": filled}).json()["placeholders"] == []
    pdf = c.get(f"/api/recovery/cases/{med['id']}/letter.pdf")
    assert pdf.status_code == 200 and pdf.content[:5] == b"%PDF-"
    st = c.post(f"/api/recovery/cases/{med['id']}/status", json={"status": "SENT"}).json()
    assert st["status"] == "SENT" and st["sentVia"] == "clinic"
    # weeks later, the next report's payment files close what got paid
    later = lm.build_835("BLUE CROSS", "2030-01-10", [_remit("BUN1", "2026-08-03", [_ok("99213", 88.0, 145.0, ["25"])])], "BL")
    r2 = c.post("/api/lost-money/report", data={"save": "1"}, files=[("files", ("later.835", later.encode()))])
    assert r2.status_code == 200 and "now paid" in r2.text
    j2 = c.get("/api/recovery/cases").json()
    assert j2["totals"]["recovered"] == 88.0
    assert c.get("/recovery").status_code == 200


def test_cases_stay_inside_their_clinic():
    c = _client("recovery-biller@example.com")
    j = c.get("/api/recovery/cases").json()
    if not j["cases"]:
        c.post("/api/lost-money/report", data={"save": "1"}, files=[("files", (n, b)) for n, b in files()])
        j = c.get("/api/recovery/cases").json()
    cid = j["cases"][0]["id"]
    other = _client("recovery-other@example.com", org="Another Clinic")
    assert other.get(f"/api/recovery/cases/{cid}").status_code == 404
    assert other.post(f"/api/recovery/cases/{cid}/status", json={"status": "CLOSED"}).status_code == 404
    viewer = _client("recovery-viewer@example.com", role="viewer")
    assert viewer.get("/api/recovery/cases").status_code == 403
    from fastapi.testclient import TestClient
    import main
    assert TestClient(main.app).get("/recovery", follow_redirects=False).status_code in (302, 303)


def test_send_refused_when_the_claim_isnt_the_clinics(db, monkeypatch):
    import clearinghouse as chx
    rc.save_cases(db, "Recovery Test NPI", "pat@example.com", analysis())
    case = db.scalar(select(rc.RecoveryCase).where(rc.RecoveryCase.org_key == "Recovery Test NPI", rc.RecoveryCase.claim_id == "BUN1"))

    class FakeClaimMD(chx.ClaimMD):
        def __init__(self):
            super().__init__("k")
    monkeypatch.setattr(chx, "connector_for", lambda org: FakeClaimMD())
    res = rc.can_send(db, "Recovery Test NPI", case, rc._open(case))
    assert not res["ok"] and "billing NPI" in res["why"]
    monkeypatch.setattr(chx, "connector_for", lambda org: chx.NotConnected())
    assert not rc.can_send(db, "Recovery Test NPI", case, rc._open(case))["ok"]
