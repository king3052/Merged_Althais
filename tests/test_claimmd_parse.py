"""Claim.MD's real responses aren't always strict XML: the account-key check must still see the payer list."""
import clearinghouse as chx


def _with(code, text):
    c = chx.ClaimMD("ACCOUNTKEY1")
    c._post = lambda path, fields=None, files=None, timeout=30: (code, text)
    return c


def test_payer_list_with_bare_ampersand_or_namespace_connects():
    for txt in ('<?xml version="1.0" encoding="UTF-8"?><result><payer payerid="60054" payer_name="Aetna & Affiliates" 1500_claims="yes"/>'
                '<payer_alt_names alt_payer_name="AETNA"/></result>',
                '<result xmlns="http://www.claim.md/schema"><payer payerid="60054" payer_name="Aetna"/></result>',
                '<result><payer payerid="60054" payer_name="Aetna\x01"/></result>'):
        r = _with(200, txt).check_connection({})
        assert r.ok and r.status == "CONNECTED", txt
    assert _with(200, '<result xmlns="x"><payer payerid="1" payer_name="Aetna & Co"/></result>').find_payer("Aetna & Co")[0] == "1"


def test_bad_key_and_outage_say_so_plainly():
    r = _with(200, '<result><error error_mesg="Invalid AccountKey"/></result>').check_connection({})
    assert not r.ok and r.message == "Invalid AccountKey"
    r = _with(503, "<html>down</html>").check_connection({})
    assert not r.ok and r.message == "Claim.MD answered 503."


def test_find_payer_picks_the_main_listing_when_a_payer_appears_more_than_once():
    rows = ('<result><payer payerid="60054" payer_name="Aetna" payer_state="" 1500_claims="yes" eligibility="yes"/>'
            '<payer payerid="AETTX" payer_name="Aetna" payer_state="TX" 1500_claims="yes" eligibility="yes"/>'
            '<payer payerid="128TX" payer_name="Aetna Better Health of Texas" payer_state="TX"/></result>')
    assert _with(200, rows).find_payer("Aetna")[0] == "60054"
    same = '<result><payer payerid="62308" payer_name="Cigna"/><payer payerid="62308" payer_name="Cigna" payer_state="TX"/></result>'
    assert _with(200, same).find_payer("Cigna")[0] == "62308"
    two = '<result><payer payerid="A1" payer_name="Acme Health"/><payer payerid="B2" payer_name="Acme Health"/></result>'
    assert _with(200, two).find_payer("Acme Health")[0] is None          # two different nationwide listings: ask, don't guess


def test_real_payer_row_shape_parses():
    """The row Claim.MD actually returned for Aetna (attribute names starting with a digit)."""
    row = ('<result><payer 1500_claims="yes" attachment="yes" auto="no" avg_era_enroll_days="32" dent_claims="no" eligibility="yes" era="enrollment" '
           'payer_name="Aetna" payer_state="" payer_type="commercial" payerid="60054" secondary_support="yes" ub_claims="yes" workers_comp="no">'
           '<payer_alt_names alt_payer_name="AETNA HEALTH"/></payer>'
           '<payer 1500_claims="no" dent_claims="yes" payer_name="Aetna" payer_state="TX" payerid="AETDN"/></result>')
    c = _with(200, row)
    assert c.check_connection({}).ok
    pid, attrs = c.find_payer("Aetna")
    assert pid == "60054" and attrs["_1500_claims"] == "yes" and attrs["eligibility"] == "yes"


def test_eligibility_uses_the_payer_id_saved_in_billing_activation(monkeypatch):
    import billing_activation as ba
    import eligibility
    monkeypatch.setattr(ba, "org_of", lambda user: "org_x")
    monkeypatch.setattr(ba, "load_profile", lambda db, org: (None, {"payers": [{"key": "aetna", "name": "Aetna", "payerId": "60054"}]}))
    assert eligibility.saved_payer_id(None, object(), "Aetna") == "60054"
    assert eligibility.saved_payer_id(None, object(), "Cigna") == ""
