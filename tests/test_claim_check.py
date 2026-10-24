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


def test_a_note_with_only_a_subjective_cannot_score_high(monkeypatch):
    """Even when the reviewer says 95, a note with no exam, assessment or plan is capped low and says what's missing."""
    import main
    monkeypatch.setattr(main, "_fast_json", lambda *a, **k: {"score": 95, "flags": [], "missing": []})
    codes = [{"code": "99214", "type": "CPT"}, {"code": "I10", "type": "ICD-10"}]
    thin = "SUBJECTIVE: Headaches for two weeks, worse in the morning.\nOBJECTIVE: \nASSESSMENT: \nPLAN: "
    r = main._claim_check("", codes, thin)
    assert r["score"] <= 20 and any("objective, assessment, plan" in m for m in r["missing"])
    part = "SUBJECTIVE: Headaches.\nOBJECTIVE: BP 152/94, neuro exam normal.\nASSESSMENT: Hypertension, uncontrolled.\nPLAN: "
    r = main._claim_check("", codes, part)
    assert r["score"] <= 40 and any("no plan" in m for m in r["missing"])
    full = part + "Start lisinopril 10 mg daily, recheck in 4 weeks."
    r = main._claim_check("", codes, full)
    assert r["score"] == 95 and not any("incomplete" in m.lower() for m in r["missing"])
    assert main._note_gaps("Free-text note with no headings") == []


def test_intractable_headache_codes_need_the_note_to_say_so():
    """ICD-10 codes a headache or migraine as not intractable unless the note documents it."""
    import icd10
    codes = [{"code": "G44.211", "type": "ICD-10"}, {"code": "G43.919", "type": "ICD-10"}]
    out, notes = icd10.supported(codes, "Assessment: Episodic tension-type headaches, not migraine. Not intractable.")
    assert [c["code"] for c in out] == ["G44.219", "G43.909"] and len(notes) == 2
    out, _ = icd10.supported(codes, "Intractable episodic tension-type headache despite two preventives")
    assert [c["code"] for c in out] == ["G44.211", "G43.919"]
    # a vague code made more specific keeps "not intractable"
    out, _ = icd10.specify([{"code": "G44.209", "type": "ICD-10"}], "Assessment: Episodic tension-type headaches, not migraines")
    assert out[0]["code"] == "G44.219"
    # episodic and chronic tension headaches follow the note
    out, _ = icd10.supported([{"code": "G44.229", "type": "ICD-10"}], "Assessment: Episodic tension-type headaches, not migraines")
    assert out[0]["code"] == "G44.219"
