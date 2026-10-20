"""Tebra (Kareo) connector: SOAP request shape, reply parsing, encounter grouping, and the connect/sync flow, with Tebra faked."""
import datetime as dt
import json
import xml.etree.ElementTree as ET

import pytest

import emr_connect as ec
import tebra as tb

NS = "http://www.kareo.com/api/schemas/"


def reply(rows=None, sec=None, err=None):
    sec = {"Authenticated": "true", "Authorized": "true", "CustomerKeyValid": "true", "SecurityResultSuccess": "true", "SecurityResult": "Success", **(sec or {})}
    s = "".join(f"<{k}>{v}</{k}>" for k, v in sec.items())
    e = f"<ErrorResponse><IsError>{'true' if err else 'false'}</IsError><ErrorMessage>{err or ''}</ErrorMessage></ErrorResponse>"
    ch = "".join("<ChargeData>" + "".join(f"<{k}>{v}</{k}>" for k, v in r.items()) + "</ChargeData>" for r in (rows or []))
    return (f'<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/"><s:Body><GetChargesResponse xmlns="{NS}"><GetChargesResult>'
            f"{e}<SecurityResponse>{s}</SecurityResponse><Charges>{ch}</Charges></GetChargesResult></GetChargesResponse></s:Body></s:Envelope>").encode()


def line(enc, cpt, dx=("J02.9",), amt="120.00", **kw):
    r = {"EncounterID": enc, "ID": f"{enc}{cpt}", "ServiceStartDate": "10/14/2026", "ProcedureCode": cpt, "TotalCharges": amt,
         "PrimaryInsuranceCompanyName": "Aetna", "PatientFirstName": "Test", "PatientLastName": "Patient", "PatientID": "77", "PatientDateOfBirth": "01/02/1990"}
    for i, d in enumerate(dx, 1):
        r[f"EncounterDiagnosisID{i}"] = d
    r.update(kw)
    return r


def test_envelope_is_wsdl_shaped_and_escaped():
    body = tb._envelope("GetCharges", {"user": "a&b", "password": "p<w>", "customer_key": "K", "practice": ""}, tb.FIELDS,
                        tb.OrderedDict([("FromServiceDate", "10/01/2026"), ("PracticeName", ""), ("ToServiceDate", "10/19/2026")]))
    root = ET.fromstring(body)
    req = root.find(f".//{{{NS}}}GetCharges/{{{NS}}}request")
    assert [c.tag.split("}")[1] for c in req] == ["RequestHeader", "Fields", "Filter"]
    hdr = req.find(f"{{{NS}}}RequestHeader")
    assert [c.tag.split("}")[1] for c in hdr] == ["ClientVersion", "CustomerKey", "Password", "User"]
    assert hdr.find(f"{{{NS}}}User").text == "a&b" and hdr.find(f"{{{NS}}}Password").text == "p<w>"
    assert [c.tag.split("}")[1] for c in req.find(f"{{{NS}}}Filter")] == ["FromServiceDate", "ToServiceDate"]       # blank practice left out
    flds = [c.tag.split("}")[1] for c in req.find(f"{{{NS}}}Fields")]
    assert flds == sorted(flds) and "PatientName" not in flds                                                   # WSDL order; minimum fields


def test_parse_errors_are_explained():
    with pytest.raises(tb.TebraError, match="Customer Key"):
        tb.parse_charges(reply(sec={"CustomerKeyValid": "false"}))
    with pytest.raises(tb.TebraError, match="username or password"):
        tb.parse_charges(reply(sec={"Authenticated": "false"}))
    with pytest.raises(tb.TebraError, match="permission"):
        tb.parse_charges(reply(sec={"Authorized": "false"}))
    with pytest.raises(tb.TebraError, match="Practice not found"):
        tb.parse_charges(reply(err="Practice not found"))
    with pytest.raises(tb.TebraError, match="couldn't read"):
        tb.parse_charges(b"not xml")


def test_lines_group_into_encounters():
    rows = tb.parse_charges(reply([line("501", "99284", ("R07.9", "R06.00"), "450.00", ProcedureModifier1="25"), line("501", "93005", ("R07.9",), "75.00"),
                                    line("502", "99213", ("5512",), "120.00")]))
    claims, notes = tb.to_claims(rows)
    a, b = claims
    assert a["claim_id"] == "TB-501" and a["cpt"] == ["99284", "93005"] and a["icd"] == ["R07.9", "R06.00"] and a["modifiers"] == ["25"]
    assert a["amount"] == 525 and a["dos"] == "2026-10-14" and a["payer"] == "Aetna" and a["patient_name"] == "Test Patient"
    assert b["icd"] == [] and any("diagnosis IDs" in n for n in notes)          # internal IDs are never treated as codes
    assert ec.verdict_for(ec.claim_check(a, today=dt.date(2026, 10, 19))) == "clean"


@pytest.fixture()
def client(monkeypatch):
    from fastapi.testclient import TestClient
    import auth
    import main
    user = auth.User(id=1, email="t@example.test", full_name="Test User", organization="Test Clinic Tebra", role="admin",
                     claims_submitted=0, onboarding_complete=1, active=1)
    monkeypatch.setattr(ec, "_gate", lambda u, d: None)
    for d in (auth.require_user, auth.require_biller, auth.current_user):
        main.app.dependency_overrides[d] = lambda: user
    yield TestClient(main.app)
    main.app.dependency_overrides.clear()


class R:
    def __init__(self, body, code=200): self.content, self.status_code = body, code


def test_connect_sync_disconnect(client, monkeypatch):
    sent = []
    def fake_post(url, data=None, headers=None, **kw):
        sent.append((url, data.decode(), headers))
        if b"<k:Password>wrong</k:Password>" in data:
            return R(reply(sec={"Authenticated": "false"}))
        return R(reply([line("900", "99214", ("J20.9",), "160.00"), line("901", "99213", (), "120.00")]))
    monkeypatch.setattr(tb.requests, "post", fake_post)

    cards = {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}
    assert cards["tebra"]["live"] is False and cards["tebra"]["actions"][0]["kind"] == "tebra"
    assert client.post("/api/integrations/tebra/connect", json={"user": "u"}).status_code == 400
    bad = client.post("/api/integrations/tebra/connect", json={"user": "api@clinic", "password": "wrong", "customer_key": "KEY123"})
    assert bad.status_code == 400 and "username or password" in bad.json()["error"]
    ok = client.post("/api/integrations/tebra/connect", json={"user": "api@clinic", "password": "s3cret!", "customer_key": "KEY123", "practice": "Test Clinic"})
    assert ok.status_code == 200, ok.text
    url, body, headers = sent[-1]
    assert url == tb.ENDPOINT and headers["SOAPAction"] == '"http://www.kareo.com/api/schemas/KareoServices/GetCharges"' and "<k:PracticeName>Test Clinic</k:PracticeName>" in body

    import auth
    with auth.SessionLocal() as db:
        row = db.scalar(tb.sa_select(auth.OrgSettings).where(auth.OrgSettings.category == "tebra"))
        assert "s3cret" not in row.data and "KEY123" not in row.data                     # stored encrypted
    cards = {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}
    assert cards["tebra"]["live"] is True and "s3cret" not in json.dumps(cards) and "KEY123" not in json.dumps(cards)

    s = client.post("/api/integrations/tebra/sync", json={"days": 500})
    assert s.status_code == 200, s.text
    j = s.json()
    assert j["summary"]["total"] == 2 and {c["claim_id"] for c in j["claims"]} == {"TB-900", "TB-901"}
    assert {c["claim_id"]: c["verdict"] for c in j["claims"]}["TB-901"] == "fix"                       # no diagnosis
    frm = [x for x in sent[-1][1].split("<k:FromServiceDate>")[1].split("<")[:1]][0]
    assert (dt.date.today() - dt.datetime.strptime(frm, "%m/%d/%Y").date()).days <= tb.MAX_DAYS + 1      # range capped
    assert all(c["id"] != "TB-900" for c in client.get("/api/integrations/worklist").json()["claims"])  # a sync only previews
    assert client.post("/api/integrations/tebra/disconnect").json()["ok"]
    assert {e["key"]: e for e in client.get("/api/integrations/emrs").json()["emrs"]}["tebra"]["live"] is False
    assert client.post("/api/integrations/tebra/sync", json={}).status_code == 400
