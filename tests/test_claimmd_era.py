"""Claim.MD payments (ERAs) post to the claims Althais sent, and eligibility reads copays and deductibles whichever way
Claim.MD lays out its benefit rows. The 835 below is the one Claim.MD's test account returned for Althais's test claims."""
import json

import billing_activation as ba
import clearinghouse as chx
import eligibility as el
from dental_x12 import parse_835

ERA = ("ISA*00*          *00*          *ZZ*60054          *ZZ*34590          *261102*0257*^*00501*087513364*0*P*:~GS*HP*60054*34590*20261102*0257*1*X*005010X221A1~"
       "ST*835*0001~BPR*I*92.76*C*ACH*CCP*01*111111111*DA*700000000*1000000000*000060054*01*111111111*DA*111111111*20261102~"
       "TRN*1*897419465-1793653036*1000000000*000060054~N1*PR*AETNA*XV*60054~REF*2U*60054~N1*PE*SAMPLE PRACTICE*XX*1111111112~"
       "LX*1~CLP*ALT-TEST-CLEAN-1102*1*134.7*92.76*15*HM*TST897419465~NM1*QC*1*TESTPATIENT*ALPHA~NM1*IL*1*TESTPATIENT*ALPHA****MI*W100200300~"
       "SVC*HC:99213*118.2*79.56**1~DTM*472*20261030~CAS*CO*45*23.64~CAS*PR*2*10**3*5~REF*6R*1529382178~AMT*B6*94.56~"
       "SVC*HC:87880*16.5*13.2**1~DTM*472*20261030~CAS*CO*45*3.3~REF*6R*1529382179~AMT*B6*13.2~"
       "LX*3~CLP*ALT-TEST-DENY-1102*4*158.7*0*30*HM*TST897419942~NM1*QC*1*TESTPATIENT*CHARLIE~NM1*IL*1*TESTPATIENT*CHARLIE****MI*DENY~"
       "SVC*HC:99213*118.2*0**1~CAS*OA*109*118.2~SVC*HC:73610*40.5*0**1~CAS*OA*109*40.5~SE*30*0001~GE*1*1~IEA*1*087513364~")


def test_835_reads_payment_and_member_id():
    era = parse_835(ERA)
    clean, deny = era["claims"]
    assert era["total"] == 92.76 and clean["paid"] == 92.76 and clean["patient"]["member_id"] == "W100200300"
    assert deny["status"] == "denied" and deny["patient"]["member_id"] == "DENY"
    note = ba.remit_note(clean)
    assert "Paid $92.76 of $134.70" in note and "coinsurance $10.00" in note and "copay $5.00" in note and "CO-45" in note
    assert ba.remit_note(deny).startswith("Denied: $158.70 not paid") and "OA-109" in ba.remit_note(deny)


def _sent(db, cid):
    tx = ba.ClaimTransmission(org_key="ERA Clinic", claim_id=cid, content_hash="x", idempotency_key="era-" + cid, payer="Aetna", user_id=1,
                              delivery="SENT", environment="sandbox")
    db.add(tx)
    db.commit()
    return tx


def test_post_era_marks_paid_and_denied_once():
    with ba.SessionLocal() as db:
        for cid in ("ALT-TEST-CLEAN-1102", "ALT-TEST-DENY-1102"):
            db.query(ba.BillingEvent).filter(ba.BillingEvent.claim_id == cid).delete()
            db.query(ba.ClaimTransmission).filter(ba.ClaimTransmission.claim_id == cid).delete()
        db.commit()
        a, b = _sent(db, "ALT-TEST-CLEAN-1102"), _sent(db, "ALT-TEST-DENY-1102")
        assert ba.post_era(db, parse_835(ERA), "87513364") == 2
        db.commit()
        db.refresh(a), db.refresh(b)
        assert a.payment == "PAID" and a.paid_at and a.payer_status == "ACCEPTED"
        assert b.payment == "DENIED" and "109" in json.loads(b.blockers)[0]["reason"]
        assert ba.post_era(db, parse_835(ERA), "87513364") == 0          # the same ERA twice changes nothing
        ev = db.query(ba.BillingEvent).filter(ba.BillingEvent.claim_id == "ALT-TEST-CLEAN-1102", ba.BillingEvent.kind == "era").all()
        assert len(ev) == 1 and ev[0].to_status == "PAID" and "Check/EFT 897419465-1793653036" in ev[0].note


def test_poll_eras_reads_new_eras_and_remembers_the_last(monkeypatch):
    calls = {"list": [], "835": []}

    class Fake:
        def __init__(self, key):
            pass

        def era_list(self, last):
            calls["list"].append(last)
            return ([{"eraid": "87513364"}], "87513364") if last == "0" else ([], last)

        def era_835(self, eraid):
            calls["835"].append(eraid)
            return ERA
    store = {"partner": "claimmd", "key": "sealed"}
    monkeypatch.setattr(chx, "ClaimMD", Fake)
    monkeypatch.setattr(chx, "unseal", lambda k: "k")
    monkeypatch.setattr(chx, "load_connection", lambda org: dict(store))
    monkeypatch.setattr(chx, "save_connection", lambda db, org, data: store.update(data))
    ba.poll_eras()
    assert calls == {"list": ["0"], "835": ["87513364"]} and store["lastEraId"] == "87513364" and not store.get("eraError")
    ba.poll_eras()
    assert calls["list"][-1] == "87513364" and len(calls["835"]) == 1


def test_era_835_is_taken_out_of_claimmd_xml(monkeypatch):
    c = chx.ClaimMD("k")
    monkeypatch.setattr(c, "_post", lambda path, fields=None, **kw: (200, f"<result><era eraid='1'><data>{ERA}</data></era></result>"))
    assert c.era_835("1").startswith("ISA*") and c.era_835("1").endswith("IEA*1*087513364~")
    import base64
    monkeypatch.setattr(c, "_post", lambda path, fields=None, **kw: (200, f"<result><era835>{base64.b64encode(ERA.encode()).decode()}</era835></result>"))
    assert c.era_835("1").startswith("ISA*")
    monkeypatch.setattr(c, "_post", lambda path, fields=None, **kw: (200, "<result><era eraid='5' payer_name='AETNA' paid_amount='92.76'/>"
                                                                              "<era eraid='7' payer_name='UHC'/></result>"))
    rows, last = c.era_list("3")
    assert [r["eraid"] for r in rows] == ["5", "7"] and last == "7" and rows[0]["amount"] == "92.76"


def _x(rows):
    return "<result><elig plan_number='PLAN101'>" + "".join("<benefit " + " ".join(f"{k}='{v}'" for k, v in r.items()) + "/>" for r in rows) + "</elig></result>"


def test_271_with_coverage_code_as_eb01():
    """Claim.MD's layout where benefit_coverage_code is EB01 (with its description) and benefit_code is the service type."""
    x = _x([{"benefit_code": "30", "benefit_coverage_code": "1", "benefit_coverage_description": "Active Coverage", "benefit_description": "Health Benefit Plan Coverage"},
            {"benefit_code": "98", "benefit_coverage_code": "B", "benefit_coverage_description": "Co-Payment", "benefit_amount": "25", "inplan_network": "Y"},
            {"benefit_code": "30", "benefit_coverage_code": "B", "benefit_coverage_description": "Co-Payment", "benefit_amount": "40", "inplan_network": "Y"},
            {"benefit_code": "30", "benefit_coverage_code": "C", "benefit_coverage_description": "Deductible", "benefit_level_code": "IND", "benefit_period_code": "23", "benefit_amount": "1500", "inplan_network": "Y"},
            {"benefit_code": "30", "benefit_coverage_code": "C", "benefit_coverage_description": "Deductible", "benefit_level_code": "IND", "benefit_period_code": "29", "benefit_amount": "640.25", "inplan_network": "Y"},
            {"benefit_code": "30", "benefit_coverage_code": "C", "benefit_coverage_description": "Deductible", "benefit_level_code": "FAM", "benefit_period_code": "23", "benefit_amount": "3000", "inplan_network": "Y"}])
    r = el.parse_claimmd_271(200, x)
    assert (r.status, r.plan, r.copay, r.deductible, r.deductible_remaining) == ("active", "PLAN101", 25.0, 1500.0, 640.25)
    assert r.benefits[1]["kind"] == "B" and r.benefits[1]["service"] == "98"


def test_271_with_benefit_code_as_eb01_still_works():
    x = _x([{"benefit_code": "1", "benefit_coverage_code": "30"},
            {"benefit_code": "B", "benefit_coverage_code": "30", "benefit_amount": "35", "inplan_network": "Y"},
            {"benefit_code": "C", "benefit_coverage_code": "30", "benefit_period_code": "23", "benefit_amount": "500"}])
    r = el.parse_claimmd_271(200, x)
    assert (r.status, r.copay, r.deductible) == ("active", 35.0, 500.0)
