"""A dental clinic running its billing through Althais: 837D claims, X-rays and narratives sent with them, and dental
eligibility (maximums, coverage by category, limits)."""
import io
import json
import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

import auth
import billing_activation as ba
import claim_attachments as ca
import clearinghouse as chx
import eligibility as elig
import main

PROFILE = {"legalName": "Bright Smile Dental PLLC", "npi": "1234567893", "tin": "12-3456789", "taxonomy": "1223G0001X",
           "address": {"line1": "100 Main St", "city": "Austin", "state": "TX", "zip": "78701"}, "contact": {"name": "Nia Okafor", "email": "billing@brightsmile.test"}}


def claim(**kw):
    c = {"claimId": "C-7001", "patient": "RIVERA, ANA", "dob": "1985-04-12", "sex": "F", "payer": "Delta Dental of Texas", "insuredId": "DDT123456",
         "provider": "Okafor, Nia", "npi": "1987654321", "svcDate": "2026-10-20", "pos": "11", "addrStreet": "12 Oak Ln, Austin, TX 78702",
         "cpts": [{"code": "D0120", "charge": 65, "units": 1}, {"code": "D2392", "tooth": "14", "surface": "MO", "charge": 210, "units": 1},
                  {"code": "D4341", "quad": "UR", "charge": 240, "units": 1}, {"code": "D2740", "tooth": "#30", "charge": 1150, "units": 1, "prosthesis": "I"}],
         "icds": [], "totalAmt": 1665, "billingProfile": PROFILE}
    c.update(kw)
    return c


def segments(edi):
    return [s.strip() for s in edi.split("~") if s.strip()]


def seg(edi, tag):
    return [s.split("*") for s in segments(edi) if s.split("*")[0] == tag]


# ── 837D ─────────────────────────────────────────────────────────────────────
def test_837d_envelope_and_counts():
    edi = chx.build_837d(claim(), "CDCA1", "DELTA DENTAL OF TEXAS")
    s = segments(edi)
    assert s[0].startswith("ISA*") and len(s[0]) == 105 and s[0][-1] == ":"           # fixed-width ISA, component separator ':'
    assert seg(edi, "GS")[0][8] == "005010X224A2" and seg(edi, "ST")[0] == ["ST", "837", "0001", "005010X224A2"]
    st, se = s.index(next(x for x in s if x.startswith("ST*"))), s.index(next(x for x in s if x.startswith("SE*")))
    assert int(seg(edi, "SE")[0][1]) == se - st + 1                                   # SE counts ST through SE
    assert seg(edi, "IEA")[0][2] == s[0].split("*")[13]                                # control numbers match
    assert not seg(edi, "SV1") and len(seg(edi, "LX")) == 4
    clm = seg(edi, "CLM")[0]
    assert clm[1] == "C-7001" and clm[2] == "1665" and clm[5] == "11:B:1"
    assert seg(edi, "NM1")[-1][:2] == ["NM1", "82"] and seg(edi, "NM1")[-1][-1] == "1987654321"   # treating dentist
    assert seg(edi, "SBR")[0][1:3] == ["P", "18"] and seg(edi, "SBR")[0][9] == "CI"
    pr = next(x for x in seg(edi, "NM1") if x[1] == "PR")
    assert pr[-2:] == ["PI", "CDCA1"]
    assert not any(re.search(r"[^\x20-\x7e]", x) for x in s)                          # plain ASCII only


def test_837d_service_lines_carry_teeth_surfaces_and_areas():
    edi = chx.build_837d(claim(), "CDCA1", "DELTA")
    sv3 = seg(edi, "SV3")
    assert sv3[0] == ["SV3", "AD:D0120", "65", "", "", "", "1"]
    assert sv3[1] == ["SV3", "AD:D2392", "210", "", "", "", "1"]
    assert sv3[2] == ["SV3", "AD:D4341", "240", "", "10", "", "1"]                       # upper right quadrant = 10
    assert sv3[3] == ["SV3", "AD:D2740", "1150", "", "", "I", "1"]                   # crown, initial placement
    too = seg(edi, "TOO")
    assert too == [["TOO", "JP", "14", "M:O"], ["TOO", "JP", "30"]]
    assert len(seg(edi, "DTP")) == 4 and all(d[1:] == ["472", "D8", "20261020"] for d in seg(edi, "DTP"))
    # each TOO follows its own SV3
    s = segments(edi)
    i = s.index("SV3*AD:D2392*210****1")
    assert s[i + 1] == "TOO*JP*14*M:O"


def test_837d_diagnosis_pointers_and_attachment():
    edi = chx.build_837d(claim(icds=[{"code": "K02.52"}, {"code": "K05.311"}], attachmentAcn="A1B2C3D4E5F6A7B", attachmentType="RB"), "X", "Y")
    assert seg(edi, "HI")[0] == ["HI", "ABK:K0252", "ABF:K05311"]
    assert seg(edi, "SV3")[0][-1] == "1:2"
    assert seg(edi, "PWK")[0] == ["PWK", "RB", "EL", "", "", "AC", "A1B2C3D4E5F6A7B"]
    s = segments(edi)
    assert s.index(next(x for x in s if x.startswith("PWK"))) < s.index(next(x for x in s if x.startswith("HI")))   # 2300 order: PWK before HI


def test_837d_refuses_what_a_payer_would_reject():
    with pytest.raises(ValueError, match="tooth 33"):
        chx.build_837d(claim(cpts=[{"code": "D2391", "tooth": "33", "surface": "O", "charge": 100}]), "X", "Y")
    with pytest.raises(ValueError, match="surfaces"):
        chx.build_837d(claim(cpts=[{"code": "D2391", "tooth": "3", "surface": "OX", "charge": 100}]), "X", "Y")
    with pytest.raises(ValueError, match="CDT"):
        chx.build_837d(claim(cpts=[{"code": "99213", "charge": 100}, {"code": "D0120", "charge": 50}]), "X", "Y")
    with pytest.raises(ValueError, match="area of the mouth"):
        chx.build_837d(claim(cpts=[{"code": "D4341", "quad": "XX", "charge": 100}]), "X", "Y")
    with pytest.raises(ValueError, match="treating dentist's NPI"):
        chx.build_837d(claim(npi=""), "X", "Y")
    # primary teeth, supernumerary teeth, facial written as V, arches
    edi = chx.build_837d(claim(cpts=[{"code": "D2391", "tooth": "K", "surface": "V", "charge": 90}, {"code": "D7140", "tooth": "51", "charge": 150},
                                     {"code": "D5110", "quad": "UA", "charge": 900}]), "X", "Y")
    assert seg(edi, "TOO") == [["TOO", "JP", "K", "F"], ["TOO", "JP", "51"]] and seg(edi, "SV3")[2][4] == "01"


def test_medical_claims_are_unchanged():
    c = claim(cpts=[{"code": "99213", "charge": 120}], icds=[{"code": "J02.0"}])
    assert not chx.is_dental(c)
    assert "005010X222A1" in chx.build_837p(c, "X", "Y")


class FakePost:
    def __init__(self, replies):
        self.replies, self.calls = list(replies), []

    def __call__(self, path, fields=None, files=None, timeout=30):
        self.calls.append((path, fields, files))
        return 200, self.replies.pop(0)


def test_claimmd_sends_dental_as_837d_and_then_the_pdf(monkeypatch):
    cmd = chx.ClaimMD("k")
    fake = FakePost(['<result><claim claimid="C-7001" claimmd_id="991" status="A"/></result>', "<result><file name='A1.pdf'/></result>"])
    monkeypatch.setattr(cmd, "_post", fake)
    prof = dict(PROFILE, payers=[{"name": "Delta Dental of Texas", "payerId": "CDCA1"}])
    r = cmd.submit_claim(claim(billingProfile=prof, attachmentAcn="A1", attachmentType="RB"), "k1")
    assert r.ok and r.reference == "991"
    path, fields, files = fake.calls[0]
    name, body, _ = files["File"]
    assert path == "upload/" and name == "C-7001.837d" and b"005010X224A2" in body and b"PWK*RB*EL***AC*A1" in body
    a = cmd.send_attachment("A1", b"%PDF-1.4 test")
    assert a.ok and fake.calls[1][2]["File"][0] == "A1.pdf"


def test_dental_enrollment_rule_is_never_guessed(monkeypatch):
    cmd = chx.ClaimMD("k")
    monkeypatch.setattr(cmd, "find_payer", lambda p: ("CDCA1", {"payer_name": "Delta Dental", "1500_claims": "yes"}))
    assert cmd.payer_requirement("Delta Dental", "837D").status == "UNKNOWN"
    monkeypatch.setattr(cmd, "find_payer", lambda p: ("CDCA1", {"payer_name": "Delta Dental", "dental_claims": "enroll"}))
    assert cmd.payer_requirement("Delta Dental", "837D").status == "REQUIRED"


# ── the app: attachments, sending, eligibility ───────────────────────────────
def _dental_user(email="dent-ready@example.test", org="Ready Dental Clinic"):
    with auth.SessionLocal() as db:
        u = db.scalar(select(auth.User).where(auth.User.email == email))
        if not u:
            u = auth.User(email=email, password_hash=auth.hash_password("x" * 12), full_name="Nia Okafor", organization=org, role="admin",
                          claims_submitted=0, onboarding_complete=1, active=1)
            db.add(u); db.commit(); db.refresh(u)
        row = db.scalar(select(auth.OrgSettings).where(auth.OrgSettings.org_key == org, auth.OrgSettings.category == auth.ENTITLEMENTS_CATEGORY))
        if not row:
            db.add(auth.OrgSettings(org_key=org, category=auth.ENTITLEMENTS_CATEGORY, data=json.dumps({"products": ["suite"], "practice": "dental"})))
            db.commit()
        db.refresh(u)
        db.expunge(u)
    return u


@pytest.fixture()
def dental():
    u = _dental_user()
    main.app.dependency_overrides[auth.require_user] = lambda: u
    main.app.dependency_overrides[auth.current_user] = lambda: u
    yield TestClient(main.app), u
    main.app.dependency_overrides.clear()


def _png(w=400, h=300):
    from PIL import Image, ImageDraw
    im = Image.new("L", (w, h), 20)
    ImageDraw.Draw(im).ellipse((100, 60, 300, 240), fill=200)
    b = io.BytesIO(); im.save(b, "PNG")
    return b.getvalue()


def test_attachments_are_pdf_encrypted_and_locked_once_sent(dental, monkeypatch):
    c, u = dental
    cid = "DX-ATT-1"
    r = c.post(f"/api/claims/{cid}/attachments", files={"file": ("bwx-14.png", _png(), "image/png")}, data={"kind": "xray", "caption": "Bitewing, #14", "patient": "Rivera, Ana"})
    assert r.status_code == 200, r.text
    j = r.json()
    assert j["attachments"][0]["label"] == "X-ray" and j["attachments"][0]["pages"] == 1 and j["reportType"] == "RB" and re.fullmatch(r"A[0-9A-F]{14}", j["acn"])
    r = c.post(f"/api/claims/{cid}/attachments/narrative", json={"text": "Tooth #30 has a fractured cusp and recurrent decay under the old MOD amalgam; a crown is needed.", "patient": "Rivera, Ana"})
    assert r.status_code == 200 and len(r.json()["attachments"]) == 2 and r.json()["acn"] == j["acn"]   # one ACN per claim
    # a PDF is kept as it is
    from fpdf import FPDF
    p = FPDF(); p.add_page(); p.add_page()
    r = c.post(f"/api/claims/{cid}/attachments", files={"file": ("perio.pdf", bytes(p.output()), "application/pdf")}, data={"kind": "perio"})
    assert r.status_code == 200 and r.json()["attachments"][2]["pages"] == 2
    # files that can't go to a payer are refused with a reason
    bad = c.post(f"/api/claims/{cid}/attachments", files={"file": ("ct.dcm", b"DICM" + b"0" * 200, "application/dicom")}, data={"kind": "xray"})
    assert bad.status_code == 400 and "DICOM" in bad.json()["detail"]
    assert c.post(f"/api/claims/{cid}/attachments", files={"file": ("x.png", b"not an image", "image/png")}, data={"kind": "xray"}).status_code == 400
    # stored encrypted, names too
    with auth.SessionLocal() as db:
        rows = ca.rows_for(db, ba.org_of(u), cid)
        assert all(r.data.startswith(b"A2:") and r.name.startswith("A2:") for r in rows)
        acn, rtype, pdf, _ = ca.bundle(db, ba.org_of(u), cid)
    from pypdf import PdfReader
    assert rtype == "RB" and acn == j["acn"] and len(PdfReader(io.BytesIO(pdf)).pages) == 4
    v = c.get(f"/api/claims/{cid}/attachments/{j['attachments'][0]['id']}")
    assert v.status_code == 200 and v.content.startswith(b"%PDF") and v.headers["cache-control"] == "no-store"
    # sending: the claim carries the ACN, then the PDF follows; afterwards the documents are locked
    sent = {}

    class Conn(chx.Sandbox):
        def submit_claim(self, claim_, idempotency_key):
            sent["edi"] = chx.build_837d(claim_, "CDCA1", "DELTA")
            return super().submit_claim(claim_, idempotency_key)

        def send_attachment(self, acn_, pdf_):
            sent["acn"], sent["pdf"] = acn_, pdf_
            return super().send_attachment(acn_, pdf_)

    monkeypatch.setattr(chx, "connector_for", lambda org: Conn())
    with auth.SessionLocal() as db:
        _, data = ba.load_profile(db, ba.org_of(u))
        data.update(PROFILE)
        data["providers"] = [{"id": "prv_1", "name": "Okafor, Nia", "npi": "1987654321", "taxonomy": "1223G0001X"}]
        orig = ba.claim_decision
        ba.claim_decision = lambda *a, **k: {"ready": True, "blockers": [], "payer": "delta", "providerId": "prv_1"}
        try:
            res = ba.transmit(db, ba.org_of(u), "sandbox", data, db.get(auth.User, u.id), claim(claimId=cid, billingProfile=None), )
        finally:
            ba.claim_decision = orig
        db.commit()
    assert res["status"] == "sent" and res["attachments"]["ok"] and res["attachments"]["count"] == 3
    assert f"PWK*RB*EL***AC*{j['acn']}" in sent["edi"] and "PRV*PE*PXC*1223G0001X" in sent["edi"] and sent["acn"] == j["acn"] and sent["pdf"].startswith(b"%PDF")
    after = c.get(f"/api/claims/{cid}/attachments").json()
    assert after["locked"] and all(a["status"] == "sent" for a in after["attachments"])
    assert c.post(f"/api/claims/{cid}/attachments/narrative", json={"text": "One more document after sending."}).status_code == 409
    assert c.delete(f"/api/claims/{cid}/attachments/{j['attachments'][0]['id']}").status_code == 409
    assert c.post(f"/api/claims/{cid}/attachments/send").status_code == 200       # resend works once sent


def test_dental_clinics_route_837d():
    u = _dental_user()
    with auth.SessionLocal() as db:
        assert ba.claim_txn(db, ba.org_of(u)) == "837D"
        assert ba.claim_txn(db, "Some Medical Clinic") == "837P"


def test_an_unsupported_clearinghouse_holds_a_claim_with_documents():
    u = _dental_user()

    class NoAttach(chx.Sandbox):
        capabilities = dict(chx.Sandbox.capabilities, attachments=False)

    with auth.SessionLocal() as db:
        org = ba.org_of(u)
        db.add(ca.ClaimAttachment(org_key=org, claim_id="DX-HOLD-1", acn="AHOLD", kind="xray", data=ca._seal(b"%PDF-1.4"), size=8))
        db.commit()
        _, data = ba.load_profile(db, org)
        dec = ba.claim_decision(db, org, "sandbox", data, db.get(auth.User, u.id), claim(claimId="DX-HOLD-1"), NoAttach(), by_althais=True)
    assert any(b["requirement"] == "attachments" for b in dec["blockers"])


# ── dental eligibility ───────────────────────────────────────────────────────
DENTAL_271 = """<result><elig plan_number="DDTX-PPO">
<benefit benefit_code="1" benefit_coverage_code="35"/>
<benefit benefit_code="F" benefit_coverage_code="35" benefit_period_code="23" benefit_amount="1500" inplan_network="Y"/>
<benefit benefit_code="F" benefit_coverage_code="35" benefit_period_code="29" benefit_amount="1120.50" inplan_network="Y"/>
<benefit benefit_code="C" benefit_level_code="IND" benefit_period_code="23" benefit_amount="50" inplan_network="Y"/>
<benefit benefit_code="C" benefit_level_code="IND" benefit_period_code="29" benefit_amount="0" inplan_network="Y"/>
<benefit benefit_code="C" benefit_level_code="FAM" benefit_period_code="23" benefit_amount="150" inplan_network="Y"/>
<benefit benefit_code="A" benefit_coverage_code="41" benefit_percent="0" inplan_network="Y"/>
<benefit benefit_code="A" benefit_coverage_code="25" benefit_percent="0.2" inplan_network="Y"/>
<benefit benefit_code="A" benefit_coverage_code="36" benefit_percent="60" inplan_network="N"/>
<benefit benefit_code="A" benefit_coverage_code="36" benefit_percent="50" inplan_network="Y"/>
<benefit benefit_code="F" benefit_coverage_code="38" benefit_period_code="32" benefit_amount="1000"/>
<benefit benefit_code="F" benefit_coverage_code="41" benefit_notes="D1110 limited to 2 per calendar year"/>
<benefit benefit_code="D" benefit_coverage_code="36" benefit_notes="12 month waiting period for major services"/>
<benefit benefit_code="I" benefit_coverage_code="38" benefit_notes="Adult orthodontics not covered"/>
</elig></result>"""


def test_dental_benefits_summary():
    r = elig.parse_claimmd_271(200, DENTAL_271, dental=True)
    d = r.dental
    assert r.status == "active" and r.plan == "DDTX-PPO"
    assert d["annual_max"] == 1500 and d["annual_max_remaining"] == 1120.5 and d["deductible"] == 50 and d["deductible_remaining"] == 0
    assert d["family_deductible"] == 150 and d["ortho_lifetime_max"] == 1000
    cov = {x["category"]: x["plan_pays"] for x in d["coverage"]}
    assert cov == {"Preventive": 100, "Restorative (basic)": 80, "Crowns": 50}          # in-network wins over out-of-network
    assert [x["category"] for x in d["coverage"]] == ["Preventive", "Restorative (basic)", "Crowns"]
    assert d["limits"][0] == {"category": "Preventive", "text": "D1110 limited to 2 per calendar year"}
    assert d["waiting_periods"][0]["category"] == "Crowns" and "12 month" in d["waiting_periods"][0]["text"]
    assert d["not_covered"][0]["category"] == "Orthodontics"
    assert elig.parse_claimmd_271(200, DENTAL_271).dental is None                       # medical checks don't get a dental summary


def test_dental_check_asks_for_dental_care(dental, monkeypatch):
    c, u = dental
    seen = {}

    class P(elig.EligibilityProvider):
        def check(self, req):
            seen["req"] = req
            return elig.parse_claimmd_271(200, DENTAL_271, dental=req.service_code == "35")
    monkeypatch.setattr(elig, "provider", lambda: P())
    r = c.post("/api/eligibility/check", json={"payer": "Delta Dental of Texas", "member_id": "DDT123456", "patient_dob": "1985-04-12",
                                               "subscriber_first": "Ana", "subscriber_last": "Rivera", "relationship": "self"})
    assert r.status_code == 200 and r.json()["dental"]["annual_max_remaining"] == 1120.5
    assert seen["req"].service_code == "35" and seen["req"].member_id == "DDT123456"
    assert c.post("/api/eligibility/check", json={"payer": "Delta"}).status_code == 400


def test_claimmd_eligibility_sends_the_service_code(monkeypatch):
    p = elig.ClaimMDEligibility("k")
    fake = FakePost([DENTAL_271])
    monkeypatch.setattr(p.cmd, "_post", fake)
    r = p.check(elig.EligibilityRequest(payer="Delta", payer_id="CDCA1", member_id="M1", patient_dob="1985-04-12", subscriber_first="Ana",
                                        subscriber_last="Rivera", provider_npi="1234567893", provider_tin="123456789", service_code="35"))
    assert fake.calls[0][0] == "eligdata/" and fake.calls[0][1]["service_code"] == "35" and r.dental["annual_max"] == 1500


# ── appointments and visit notes on the server ───────────────────────────────
def test_records_round_trip_encrypted_with_deletions(dental):
    import clinic_records as cr
    c, u = dental
    appt = {"id": "A101-TEST", "mrn": "AR1001", "date": "2026-10-23", "time": "09:00", "visit": "Scheduled", "reason": "Crown seat #30"}
    enc = {"id": "E102-TEST", "mrn": "AR1001", "date": "2026-10-23", "status": "Draft", "soap": {"a": "Fractured cusp #30"}}
    r = c.post("/api/records", json={"records": [{"kind": "appt", "id": appt["id"], "data": appt}, {"kind": "enc", "id": enc["id"], "data": enc},
                                                 {"kind": "nope", "id": "x", "data": {}}, {"kind": "appt", "id": "A1", "data": {"id": "other"}}]})
    j = r.json()
    assert r.status_code == 200 and len(j["saved"]) == 2 and len(j["refused"]) == 2
    t0 = j["server_time"]
    with auth.SessionLocal() as db:
        row = db.scalar(select(cr.ClinicRecord).where(cr.ClinicRecord.rec_id == "E102-TEST"))
        assert row.data.startswith("A2:") and "Fractured" not in row.data
    full = c.get("/api/records").json()["records"]
    assert {x["id"]: x["data"] for x in full}["E102-TEST"]["soap"]["a"] == "Fractured cusp #30"
    # an edit, then a deletion, show up for everyone who asks since t0
    c.post("/api/records", json={"records": [{"kind": "appt", "id": appt["id"], "data": dict(appt, visit="Checked in")}, {"kind": "enc", "id": enc["id"], "deleted": True}]})
    ch = {x["id"]: x for x in c.get("/api/records", params={"since": t0}).json()["records"]}
    assert ch["A101-TEST"]["data"]["visit"] == "Checked in" and ch["A101-TEST"]["version"] == 2
    assert ch["E102-TEST"]["deleted"] is True and "data" not in ch["E102-TEST"]
    assert "E102-TEST" not in {x["id"] for x in c.get("/api/records").json()["records"]}       # a full pull leaves deleted records out
    # another clinic sees nothing of this one
    other = _dental_user("dent-other@example.test", "Another Dental Clinic")
    main.app.dependency_overrides[auth.require_user] = lambda: other
    assert c.get("/api/records").json()["records"] == []


# ── Open Dental ──────────────────────────────────────────────────────────────
class FakeOD:
    """Open Dental's API as its reference documents it, for one office."""
    def __init__(self):
        self.calls = []
        procs = [{"ProcNum": i, "PatNum": 48, "ProcDate": "2026-10-20", "ProcFee": f, "Surf": s, "ToothNum": t, "ProcStatus": "C", "ProvNum": 2,
                  "procCode": c, "UnitQty": 1, "DiagnosticCode": "", "PlaceService": "Office"}
                 for i, (c, t, s, f) in enumerate([("D0120", "", "", "65.00"), ("D2392", "14", "MO", "210.00"), ("D4341", "", "UR", "240.00"), ("BRKAPT", "", "", "0")], 1)]
        procs.append({"ProcNum": 9, "PatNum": 51, "ProcDate": "2026-10-21", "ProcFee": "1150", "Surf": "", "ToothNum": "30", "ProcStatus": "C", "ProvNum": 2, "procCode": "D2740", "UnitQty": 1})
        procs.append({"ProcNum": 10, "PatNum": 51, "ProcDate": "2020-01-01", "ProcFee": "90", "Surf": "O", "ToothNum": "3", "ProcStatus": "C", "ProvNum": 2, "procCode": "D2391"})   # edited recently, done long ago
        procs += [{"ProcNum": 100 + i, "PatNum": 60, "ProcDate": "2026-10-22", "ProcFee": "0", "ProcStatus": "TP", "procCode": "D1110"} for i in range(150)]   # planned, not done
        self.data = {"/procedurelogs": procs, "/providers": [{"ProvNum": 2, "LName": "Okafor", "FName": "Nia", "Abbr": "DOK", "NationalProvID": "1987654321"}],
                     "/patients/48": {"PatNum": 48, "LName": "Rivera", "FName": "Ana", "Birthdate": "1985-04-12", "WirelessPhone": "(512)555-0101", "Address": "12 Oak Ln", "City": "Austin", "State": "TX", "Zip": "78702"},
                     "/patients/51": {"PatNum": 51, "LName": "Chen", "FName": "Wei", "Birthdate": "1972-09-30"},
                     "/patplans?48": [{"PatPlanNum": 1, "PatNum": 48, "Ordinal": 1, "IsPending": "false", "Relationship": "Self", "PatID": "", "InsSubNum": 7}],
                     "/patplans?51": [], "/inssubs/7": {"InsSubNum": 7, "PlanNum": 3, "SubscriberID": "DDT123456"},
                     "/insplans/3": {"PlanNum": 3, "CarrierNum": 9, "GroupNum": "G-77", "GroupName": "Acme Corp"}, "/carriers/9": {"CarrierNum": 9, "CarrierName": "Delta Dental of Texas"}}

    def get(self, url, params=None, headers=None, timeout=None):
        import opendental as od
        path = url[len(od.BASE):]
        self.calls.append((path, dict(params or {}), headers))

        class R:
            def __init__(s, code, body):
                s.status_code, s._b, s.text = code, body, json.dumps(body)

            def json(s):
                return s._b
        if headers.get("Authorization") != "ODFHIR devkey/CUSTKEY123":
            return R(401, {"message": "bad key"})
        if path == "/procedurelogs":
            off = int((params or {}).get("Offset", 0))
            return R(200, self.data[path][off:off + 100])
        if path == "/patplans":
            return R(200, self.data[f"/patplans?{params['PatNum']}"])
        return R(200, self.data[path]) if path in self.data else R(404, {})


def _fixed_today(monkeypatch, od):
    real_now = od.dt.datetime

    class Now(real_now):
        @classmethod
        def now(cls, tz=None):
            return real_now(2026, 10, 23, 12, tzinfo=tz)
    monkeypatch.setattr(od.dt, "datetime", Now)


def test_open_dental_pull_reads_patients_plans_and_procedures(monkeypatch):
    import opendental as od
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "devkey")
    _fixed_today(monkeypatch, od)
    fake = FakeOD()
    pts, procs, notes = od.pull("CUSTKEY123", "", 30, session=fake)
    assert [p["src_id"] for p in pts] == ["48", "51"]
    a = pts[0]
    assert a["name"] == "Rivera, Ana" and a["dob"] == "1985-04-12" and a["payer"] == "Delta Dental of Texas" and a["member"] == "DDT123456"
    assert a["group"] == "G-77" and a["phone"] == "5125550101" and a["addr"] == "12 Oak Ln, Austin, TX 78702"
    assert pts[1]["payer"] == "" and any("no primary dental plan" in n for n in notes) and any("office-only codes" in n for n in notes)
    assert [(p["src_id"], p["code"], p["tooth"], p["surface"], p["quad"], p["fee"]) for p in procs] == [
        ("48", "D0120", "", "", "", 65.0), ("48", "D2392", "14", "MO", "", 210.0), ("48", "D4341", "", "", "UR", 240.0), ("51", "D2740", "30", "", "", 1150.0)]
    pages = [prm for path, prm, h in fake.calls if path == "/procedurelogs"]
    assert len(pages) == 2 and pages[0]["ProcStatus"] == "C" and pages[0]["DateTStamp"] == "2026-09-23 00:00:00" and pages[1]["Offset"] == 100
    with pytest.raises(od.OpenDentalError, match="didn't accept the key"):
        od.pull("WRONGKEY99", "", 30, session=fake)


def test_open_dental_live_pull_lands_in_the_dental_chart(monkeypatch):
    import opendental as od
    from tests.test_dental_api import _clinic, _login
    a = _login(_clinic())
    assert a.get("/api/dental/import/opendental").json() == {"available": False, "connected": False, "clinic_num": "", "connected_at": "", "last_pull": "", "last_summary": {}}
    monkeypatch.setenv("OPENDENTAL_DEVELOPER_KEY", "devkey")
    _fixed_today(monkeypatch, od)
    fake = FakeOD()
    real = od.Client
    monkeypatch.setattr(od, "Client", lambda key, clinic="", session=None: real(key, clinic, fake))
    assert a.post("/api/dental/import/opendental/connect", json={"customer_key": "WRONGKEY99"}).status_code == 400
    r = a.post("/api/dental/import/opendental/connect", json={"customer_key": "CUSTKEY123"})
    assert r.status_code == 200 and r.json()["connected"] and "CUSTKEY" not in r.text
    j = a.post("/api/dental/import/opendental/pull", json={"days": 30}).json()
    assert j["patients"]["added"] == 2 and j["procedures"]["added"] == 4
    chart = a.get("/api/dental/patients/OD48").json()
    assert chart["patient"]["name"] == "Rivera, Ana" and chart["patient"]["dob"] == "1985-04-12"
    assert {(p["code"], p["tooth"]) for p in chart["procedures"]} >= {("D2392", "14"), ("D0120", "")}
    again = a.post("/api/dental/import/opendental/pull", json={"days": 30}).json()
    assert again["patients"]["updated"] == 2 and again["procedures"]["added"] == 0 and again["procedures"]["skipped"] == 4
    with auth.SessionLocal() as db:
        import dental_db as m
        row, doc = od._load(db, auth._org_namespace(db.scalar(select(auth.User).where(auth.User.email == a.email))))
        assert doc["secret"].startswith("A2:")
        c = db.scalar(select(m.Coverage).where(m.Coverage.mrn == "OD48", m.Coverage.org_key == row.org_key))
        assert c.member_id == "DDT123456" and c.group == "G-77" and c.payer == "Delta Dental of Texas"
        procs = db.scalars(select(m.Procedure).where(m.Procedure.mrn == "OD48", m.Procedure.org_key == row.org_key)).all()
        assert {(p.code, p.tooth, p.surface, p.quad) for p in procs} == {("D0120", "", "", ""), ("D2392", "14", "MO", ""), ("D4341", "", "", "UR")}
