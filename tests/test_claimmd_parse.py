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
