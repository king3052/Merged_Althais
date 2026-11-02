"""A claim that failed at the clearinghouse can be sent again (no duplicate rows), a missing clinic name is caught before
sending, Claim.MD's rejection reasons always reach the biller, and the calendar-year in-network deductible is the one shown."""
import xml.etree.ElementTree as ET

import auth
import billing_activation as ba
import clearinghouse as chx
import eligibility as el

CLAIM = {"claimId": "RETRY-1", "patient": "TESTPATIENT, ECHO", "dob": "1990-01-01", "sex": "F", "payer": "Aetna", "insuredId": "W1",
         "provider": "TESTPROVIDER, ALEX", "npi": "1111111112", "svcDate": "2026-11-02", "pos": "20", "totalAmt": 100,
         "cpts": [{"code": "99213", "charge": 100, "units": 1}], "icds": [{"code": "J02.0"}]}


def _user():
    with auth.SessionLocal() as db:
        u = db.query(auth.User).filter_by(email="retry@example.test").first()
        if not u:
            u = auth.User(email="retry@example.test", password_hash="x", full_name="Retry", organization="Retry Clinic", role="admin")
            db.add(u)
            db.commit()
        return u.id


def test_failed_claim_can_be_sent_again(monkeypatch):
    uid = _user()
    answers = iter([chx.Result(ok=False, environment="sandbox", status="REJECTED", message="Billing Provider Name is required."),
                    chx.Result(ok=False, environment="sandbox", status="REJECTED", message="Still wrong."),
                    chx.Result(ok=True, environment="sandbox", status="SENT", reference="CMD-1")])

    class Conn(chx.Sandbox):
        def submit_claim(self, claim, idempotency_key):
            return next(answers)
    monkeypatch.setattr(chx, "connector_for", lambda org: Conn())
    monkeypatch.setattr(ba, "claim_decision", lambda *a, **k: {"ready": True, "blockers": [], "payer": "aetna", "providerId": ""})
    with auth.SessionLocal() as db:
        u = db.get(auth.User, uid)
        org = ba.org_of(u)
        db.query(ba.ClaimTransmission).filter_by(org_key=org, claim_id="RETRY-1").delete()
        db.commit()
        _, data = ba.load_profile(db, org)
        for want in ("failed", "failed", "sent"):          # the same claim, unchanged, three times
            assert ba.transmit(db, org, "sandbox", data, u, dict(CLAIM))["status"] == want
            db.commit()
        rows = db.query(ba.ClaimTransmission).filter_by(org_key=org, claim_id="RETRY-1").all()
        assert len(rows) == 1 and rows[0].delivery == "SENT" and rows[0].reference == "CMD-1"
        assert ba.transmit(db, org, "sandbox", data, u, dict(CLAIM))["status"] == "already_sent"


def test_837p_needs_the_clinic_name():
    prof = {"legalName": "", "npi": "1111111112", "tin": "999999999", "address": {"line1": "1 A St", "city": "Austin", "state": "TX", "zip": "78701"}}
    try:
        chx.build_837p(dict(CLAIM, billingProfile=prof), "60054", "AETNA")
        assert False, "should have asked for the clinic's legal name"
    except ValueError as e:
        assert "legal name" in str(e)


def test_rejection_reason_is_read_whatever_the_field():
    for xml in ("<claim status='R'><messages><message mesg='Billing Provider Name is required.'/></messages></claim>",
                "<claim status='R'><message message='Billing Provider Name is required.'/></claim>",
                "<claim status='R'><error>Billing Provider Name is required.</error></claim>"):
        assert chx.claim_messages(ET.fromstring(xml)) == "Billing Provider Name is required."


def test_deductible_prefers_calendar_year_in_network():
    rows = [("1", "30", {}), ("C", "30", {"benefit_level_code": "IND", "inplan_network": "W", "benefit_period_code": "24", "benefit_amount": "200"}),
            ("C", "30", {"benefit_level_code": "IND", "inplan_network": "N", "benefit_period_code": "23", "benefit_amount": "3000"}),
            ("C", "30", {"benefit_level_code": "IND", "inplan_network": "Y", "benefit_period_code": "23", "benefit_amount": "1500"}),
            ("C", "30", {"benefit_level_code": "IND", "inplan_network": "N", "benefit_period_code": "29", "benefit_amount": "2800"}),
            ("C", "30", {"benefit_level_code": "IND", "inplan_network": "Y", "benefit_period_code": "29", "benefit_amount": "1300"})]
    x = "<result><elig>" + "".join(
        "<benefit benefit_coverage_code='%s' benefit_code='%s' benefit_coverage_description='Deductible' %s/>" % (a, b, " ".join(f"{k}='{v}'" for k, v in o.items()))
        for a, b, o in rows) + "</elig></result>"
    r = el.parse_claimmd_271(200, x)
    assert (r.status, r.deductible, r.deductible_remaining) == ("active", 1500.0, 1300.0)
