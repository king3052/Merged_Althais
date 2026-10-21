"""The claim check (/api/validate-claim) when the AI reviewer is unavailable."""
def test_rules_fallback_reads_the_claim_header_in_the_note(monkeypatch):
    """When the AI reviewer is unavailable, the rules check must still see the DOB, member ID, provider and date on the claim."""
    import main

    def boom(*a, **k):
        raise RuntimeError("AI busy")
    monkeypatch.setattr(main, "_fast_json", boom)
    note = "CLAIM: patient Sample, Ann, DOB 1961-11-22, sex F, payer Aetna PPO, member ID W1, rendering provider Morgan, A, date of service 2026-10-21\nVisit note..."
    r = main._claim_check("", [{"code": "99213", "type": "CPT"}, {"code": "J06.9", "type": "ICD-10"}], note)
    assert r["missing"] == [] and r["score"] >= 90
    r2 = main._claim_check("", [{"code": "99213", "type": "CPT"}, {"code": "J06.9", "type": "ICD-10"}], "CLAIM: patient X, DOB not on file, member ID not on file")
    assert "patient DOB" in r2["missing"] and "member ID" in r2["missing"]
