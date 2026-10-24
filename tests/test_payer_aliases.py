"""A claim's payer as the EMR lists it ("UHC", "Aetna PPO") reaches the payer the clinic set up in billing by its full
name, without changing the key of a payer a clinic set up under the short name."""
import billing_registry as reg


def test_short_names_reach_the_configured_payer():
    configured = {"unitedhealthcare", "aetna", "medicare"}
    assert reg.payer_key_for("UHC", configured) == "unitedhealthcare"
    assert reg.payer_key_for("Aetna PPO", configured) == "aetna"
    assert reg.payer_key_for("Medicare B", configured) == "medicare"


def test_a_payer_set_up_under_its_short_name_keeps_its_key():
    assert reg.payer_key_for("UHC", {"uhc", "unitedhealthcare"}) == "uhc"
    assert reg.payer_key("UHC") == "uhc"


def test_unknown_payers_are_unchanged():
    assert reg.payer_key_for("Acme Health", {"aetna"}) == "acme_health"
    assert reg.payer_key_for("UHC", set()) == "uhc"


def test_providers_match_by_npi_or_by_name_in_either_order():
    import billing_activation as ba
    data = {"providers": [{"id": "p1", "name": "Jordan Lee", "npi": "1500000025"}, {"id": "p2", "name": "Elena Rivera", "npi": ""}]}
    assert ba.provider_for(data, "Lee, Jordan")["id"] == "p1"
    assert ba.provider_for(data, "Dr. Jordan Lee, MD")["id"] == "p1"
    assert ba.provider_for(data, "Rivera, Elena", "")["id"] == "p2"
    assert ba.provider_for(data, "Someone Else", "1500000025")["id"] == "p1"    # the NPI decides
    assert ba.provider_for(data, "Morgan, Alex") is None
