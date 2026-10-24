"""The dental EMR on Claim.MD (dental_claimmd.py): eligibility from the payer's 271, a claim with an X-ray, perio chart and
narrative going out as an 837D followed by one PDF, ERAs fetched for this clinic only, and synthetic patients refused.
Claim.MD itself is replaced by a fake that answers the way its API reference documents."""
import base64
import io
import json
import re
import uuid

import pytest
from fastapi.testclient import TestClient

import clearinghouse as chx
import dental_vendors as v
import dental_x12 as x12

NPI, TIN, DENTIST_NPI = "1234567893", "74-1234567", "1987654321"


class FakeClaimMD:
    def __init__(self):
        self.calls = []

    def __call__(self, cmd, path, fields=None, files=None, timeout=30):
        self.calls.append({"path": path, "fields": dict(fields or {}), "files": {k: (f[0], f[1]) for k, f in (files or {}).items()}})
        if path == "payerlist/":
            return 200, '<result><payer payerid="CDCA1" payer_name="Delta Dental of Texas"/></result>'
        if path == "elig/":
            body = files["File"][1].decode()
            req = {"payer": "Delta Dental of Texas", "payer_id": "CDCA1", "member_id": re.search(r"NM1\*IL\*1\*[^*]*\*[^*]*\*{4}MI\*([^~*]+)", body).group(1),
                   "last": "RIVERA", "first": "ANA"}
            x271 = v.MockClearinghouse().eligibility("", req)
            return 200, f'<result><eligibility data="{x271}" eligid="43830230"/></result>'
        if path == "upload/":
            name = files["File"][0]
            if name.endswith(".pdf"):
                return 200, "<result><file filename='" + name + "' status='received'/></result>"
            cid = re.search(r"CLM\*([^*]+)\*", files["File"][1].decode()).group(1)
            return 200, f'<result><claim claimid="{cid}" claimmd_id="CMD{len(self.calls)}" status="A"><message status="A" message="Accepted"/></claim></result>'
        if path == "eralist/":
            return 200, (f'<result last_eraid="12"><era eraid="11" prov_npi="{NPI}" prov_taxid="741234567" check_number="EFT1"/>'
                         f'<era eraid="12" prov_npi="1111111111" prov_taxid="999999999" check_number="OTHER"/></result>')
        if path == "era835/":
            return 200, f"<result eraid='{fields['eraid']}'><data>{self.era}</data></result>"
        return 404, "<result><error error_mesg='unknown'/></result>"


@pytest.fixture()
def claimmd(monkeypatch):
    fake = FakeClaimMD()
    monkeypatch.setattr(chx.ClaimMD, "_post", lambda self, path, fields=None, files=None, timeout=30: fake(self, path, fields, files, timeout))
    monkeypatch.setattr(chx, "load_connection", lambda org: {"partner": "claimmd", "key": chx.seal("ACCOUNTKEY")} if org == chx.PLATFORM else {})
    monkeypatch.setenv("DENTAL_VENDOR_CLEARINGHOUSE", "claimmd")
    monkeypatch.setenv("DENTAL_VENDOR_ATTACHMENTS", "claimmd")
    return fake


def _png():
    from PIL import Image, ImageDraw
    im = Image.new("L", (600, 400), 25)
    ImageDraw.Draw(im).rectangle((150, 100, 450, 300), fill=210)
    b = io.BytesIO(); im.save(b, "PNG")
    return "data:image/png;base64," + base64.b64encode(b.getvalue()).decode()


@pytest.fixture()
def office():
    from tests.test_dental_api import _clinic, _login
    org = _clinic()
    a = _login(org)
    locs = a.get("/api/dental/setup").json()["locations"]
    assert a.post("/api/dental/setup/locations", json={**({"id": locs[0]["id"]} if locs else {}), "name": "Bright Smile Main", "addr": "100 Main St", "city": "Austin", "state": "TX", "zip": "78701",
                                                       "phone": "5125550100", "npi": NPI, "tin": TIN}).status_code == 200
    dent = a.post("/api/dental/setup/providers", json={"name": "Nia Okafor DDS", "first": "Nia", "last": "Okafor", "kind": "dentist", "npi": DENTIST_NPI}).json()
    r = a.post("/api/dental/patients", json={"name": "Rivera, Ana", "dob": "1985-04-12", "sex": "F", "addr": "12 Oak Ln, Austin, TX 78702",
                                             "payer": "Delta Dental of Texas", "member": "DDT123456", "group": "G-77"})
    assert r.status_code == 200, r.text
    return {"a": a, "mrn": r.json()["mrn"], "dentist": dent["id"]}


def test_eligibility_comes_from_the_payers_271(office, claimmd):
    a, mrn = office["a"], office["mrn"]
    b = a.post(f"/api/dental/patients/{mrn}/eligibility").json()
    assert b["active"] and b["annual_max"] > 0 and b["frequency"], b
    call = next(c for c in claimmd.calls if c["path"] == "elig/")
    x270 = call["files"]["File"][1].decode()
    segs, _ = x12.segments(x270)
    assert segs[0][15] == "P" and segs[0][8].strip() == "CLAIMMD" and ["EQ", "35"] in segs
    assert any(s[0] == "NM1" and s[1] == "PR" and s[9] == "CDCA1" for s in segs)      # payer ID looked up by name


def test_claim_with_xray_perio_and_narrative_goes_as_837d_then_one_pdf(office, claimmd):
    from pypdf import PdfReader
    a, mrn = office["a"], office["mrn"]
    r = a.post(f"/api/dental/patients/{mrn}/procedures", json={"lines": [{"code": "D2740", "tooth": "30"}, {"code": "D4341", "quad": "UR"}],
                                                                "provider_id": office["dentist"], "claim": True})
    assert r.status_code == 200, r.text
    claim = r.json()["claim"]
    img = a.post(f"/api/dental/patients/{mrn}/images", json={"kind": "pa", "teeth": ["30"], "data": _png()}).json()
    pe = a.post(f"/api/dental/patients/{mrn}/perio", json={"readings": {"2": {"pd": [5, 6, 5, 4, 5, 6], "bop": [1, 1, 0, 1, 0, 1], "rec": [1, 0, 0, 0, 0, 1]},
                                                                         "3": {"pd": [3, 3, 3, 3, 3, 3]}}})
    assert pe.status_code == 200, pe.text
    assert a.post(f"/api/dental/claims/{claim['id']}/attach", json={"image_ids": [img["id"]], "perio_latest": True}).status_code == 200
    assert a.patch(f"/api/dental/claims/{claim['id']}", json={"narrative": "#30 fractured MB cusp with recurrent decay; crown needed. UR 5-6 mm pockets with bleeding."}).status_code == 200
    sent = a.post(f"/api/dental/claims/{claim['id']}/submit")
    assert sent.status_code == 200, sent.text
    j = sent.json()
    assert j["status"] == "submitted" and j["result"]["environment"] == "claimmd", j["result"]
    ups = [c for c in claimmd.calls if c["path"] == "upload/"]
    assert [u["files"]["File"][0].rsplit(".", 1)[1] for u in ups] == ["837d", "pdf"]                   # the claim first, then its documents
    x837 = ups[0]["files"]["File"][1].decode()
    segs, _ = x12.segments(x837)
    assert x12.validate_837d(x837) == [] and segs[0][15] == "P"
    pwk = [s for s in segs if s[0] == "PWK"]
    acn = pwk[0][6]
    assert {s[1] for s in pwk} == {"RB", "P6"} and all(s[6] == acn for s in pwk)                     # one control number for the claim
    assert any(s[0] == "NM1" and s[1] == "40" and s[3] == "CLAIMMD" for s in segs)
    pdf_name, pdf = ups[1]["files"]["File"]
    assert pdf_name == acn + ".pdf"
    assert len(PdfReader(io.BytesIO(pdf)).pages) == 3                                                 # X-ray, perio chart, narrative
    after = a.get(f"/api/dental/claims/{claim['id']}").json()
    assert all(x["sent"] for x in after["attachments"])


def test_svg_uploads_stop_the_claim_with_a_reason(office, claimmd):
    a, mrn = office["a"], office["mrn"]
    claim = a.post(f"/api/dental/patients/{mrn}/procedures", json={"lines": [{"code": "D2740", "tooth": "19"}], "provider_id": office["dentist"], "claim": True}).json()["claim"]
    svg = "data:image/svg+xml;base64," + base64.b64encode(b"<svg xmlns='http://www.w3.org/2000/svg'/>").decode()
    img = a.post(f"/api/dental/patients/{mrn}/images", json={"kind": "pa", "teeth": ["19"], "data": svg}).json()
    a.post(f"/api/dental/claims/{claim['id']}/attach", json={"image_ids": [img["id"]]})
    r = a.post(f"/api/dental/claims/{claim['id']}/submit")
    assert r.status_code == 422 and "SVG" in r.json()["detail"]
    assert not [c for c in claimmd.calls if c["path"] == "upload/"]                                 # nothing left Althais


def test_eras_are_fetched_for_this_clinic_only(office, claimmd):
    a = office["a"]
    claimmd.era = x12.build_835({"payer": "Delta Dental of Texas", "check": "EFT1", "date": "2026-10-23", "claims": []})
    r = a.post("/api/dental/era/fetch")
    assert r.status_code == 200, r.text
    lst = next(c for c in claimmd.calls if c["path"] == "eralist/")
    assert lst["fields"]["NPI"] == NPI and lst["fields"]["TaxID"] == "741234567"
    assert [c["fields"]["eraid"] for c in claimmd.calls if c["path"] == "era835/"] == ["11"]         # the other clinic's ERA is never opened


def test_synthetic_patients_never_leave(claimmd):
    ch = v.vendor("clearinghouse")
    with pytest.raises(v.VendorError, match="Synthetic"):
        ch.eligibility("", {"payer": "Delta", "member_id": "SYN12345678"})
    res = ch.submit_claim("ISA", {"id": "C1", "payer": "Delta", "patient": {"member_id": "SYN1"}})
    assert not res.ok and res.status == "refused" and not claimmd.calls


def test_not_connected_says_so(monkeypatch):
    monkeypatch.setattr(chx, "load_connection", lambda org: {})
    monkeypatch.setenv("DENTAL_VENDOR_CLEARINGHOUSE", "claimmd")
    with pytest.raises(v.VendorError, match="isn't connected"):
        v.vendor("clearinghouse").eligibility("", {"payer": "Delta", "member_id": "M1"})
    assert {s["kind"]: s["available"] for s in v.status()}["clearinghouse"] == ["claimmd", "mock"]


def test_for_receiver_keeps_the_isa_fixed_width():
    x = x12.build_270({"payer": "Delta", "member_id": "M1", "last": "A", "first": "B", "dob": "1980-01-01"})
    y = x12.for_receiver(x, "CLAIMMD", "CLAIMMD", True)
    isa = y.split("~")[0]
    assert len(isa) == 105 and isa.split("*")[15] == "P" and isa.split("*")[8] == "CLAIMMD        "
    assert x12.segments(y)[0][1][3] == "CLAIMMD"
