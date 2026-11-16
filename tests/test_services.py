"""Services the note documents as done in clinic, procedure-only visits, and the diagnosis fixes found by the coding test."""


def _cpt(codes):
    return sorted(str(c["code"]) + ("-" + c["modifier"] if c.get("modifier") else "") for c in codes if c["type"] == "CPT")


def test_documented_tests_xrays_injections_and_vaccines_are_added():
    import services
    note = ("OBJECTIVE: Rapid strep antigen performed in clinic: positive. Right ankle X-ray, 3 views, taken in clinic: no fracture.\n"
            "Urine dipstick, non-automated, without microscopy: positive nitrite. Albuterol 2.5 mg nebulizer treatment given.\n"
            "PROCEDURE: Ondansetron 4 mg IM injection given in clinic. Tdap vaccine 0.5 mL IM given.\n"
            "PLAN: Ondansetron 4 mg ODT as needed. Rapid influenza test sent out to the lab.")
    out, notes = services.add_documented([{"code": "99213", "type": "CPT"}], note)
    assert _cpt(out) == sorted(["99213", "87880", "73610", "81002", "J7613", "94640", "J2405", "96372", "90715", "90471"])
    assert next(c for c in out if c["code"] == "J7613")["units"] == 3 and next(c for c in out if c["code"] == "J2405")["units"] == 4
    assert "87804" not in _cpt(out)                        # sent out: not the clinic's to bill
    # a code from the same family already on the claim is left alone
    out, _ = services.add_documented([{"code": "87430", "type": "CPT"}], "Rapid strep antigen in clinic: positive.")
    assert _cpt(out) == ["87430"]


def test_laceration_repair_is_sized_from_the_note():
    import services
    assert services.repair_code("3.0 cm laceration of the right forearm. PROCEDURE: simple repair with sutures.")[0] == "12002"
    assert services.repair_code("2.0 cm laceration of the left palm, simple repair")[0] == "12001"
    assert services.repair_code("Laceration of the chin, 3 cm, sutured")[0] == "12013"
    assert services.repair_code("puncture wounds 0.5 cm laceration, not sutured") is None
    out, notes = services.add_documented([{"code": "12001", "type": "CPT"}], "3.0 cm laceration of the right forearm. PROCEDURE: simple repair with 5 sutures.")
    assert _cpt(out) == ["12002"] and "12001 → 12002" in notes[0]


def test_a_procedure_only_visit_has_no_visit_code_and_a_second_problem_gets_25():
    import services
    codes = [{"code": "99212", "type": "CPT"}, {"code": "12002", "type": "CPT"}, {"code": "S51.811A", "type": "ICD-10"}]
    out, notes = services.visit_with_minor_procedure(codes)
    assert _cpt(out) == ["12002"] and "global surgery" in notes[0]
    codes = [{"code": "99213", "type": "CPT"}, {"code": "12001", "type": "CPT"}, {"code": "S61.412A", "type": "ICD-10"}, {"code": "N39.0", "type": "ICD-10"}]
    out, _ = services.visit_with_minor_procedure(codes)
    assert _cpt(out) == ["12001", "99213-25"]
    # a vaccine diagnosis isn't a separate problem; a nebulizer isn't a minor procedure
    out, _ = services.visit_with_minor_procedure([{"code": "99213", "type": "CPT"}, {"code": "12002", "type": "CPT"}, {"code": "S61.210A", "type": "ICD-10"}, {"code": "Z23", "type": "ICD-10"}])
    assert _cpt(out) == ["12002"]
    neb = [{"code": "99214", "type": "CPT"}, {"code": "94640", "type": "CPT"}, {"code": "J45.41", "type": "ICD-10"}]
    assert _cpt(services.visit_with_minor_procedure(neb)[0]) == ["94640", "99214-25"]   # treatment the same day: visit keeps 25


def test_diagnosis_fixes_from_the_coding_test():
    import code_validation
    import icd10
    assert code_validation.check_code_format("U07.1", "ICD-10")[0]            # COVID-19 was being dropped
    out, _ = icd10.specify([{"code": "H60.92", "type": "ICD-10"}], "Acute otitis externa, left ear. Right ear canal occluded by cerumen.")
    assert out[0]["code"] == "H60.92"                                           # never swapped to the other ear
    out, n = icd10.side_and_foreign_body([{"code": "S51.821A", "type": "ICD-10"}], "3 cm laceration of the right forearm, no foreign body.")
    assert out[0]["code"] == "S51.811A" and n
    out, _ = icd10.side_and_foreign_body([{"code": "S61.411A", "type": "ICD-10"}], "2 cm laceration of the left palm")
    assert out[0]["code"] == "S61.412A"
    out, _ = icd10.side_and_foreign_body([{"code": "H60.91", "type": "ICD-10"}], "left ear pain; right ear cerumen")
    assert out[0]["code"] == "H60.91"                                           # both sides in the note: left alone


def test_a_prescription_without_a_verb_still_counts_for_the_visit_level():
    import main
    notes = []
    codes = [{"code": "99213", "type": "CPT"}, {"code": "N30.00", "type": "ICD-10"}]
    note = "SUBJECTIVE: dysuria.\nOBJECTIVE: no CVA tenderness.\nASSESSMENT: Acute cystitis.\nPLAN: Nitrofurantoin 100 mg twice daily for 5 days."
    assert main._em_floor(codes, note, "office visit, established patient", notes)[0]["code"] == "99213" and not notes
    note2 = "SUBJECTIVE: red eyes.\nOBJECTIVE: discharge.\nASSESSMENT: Conjunctivitis.\nPLAN: Erythromycin ophthalmic ointment four times daily."
    assert main._em_floor([{"code": "99213", "type": "CPT"}, {"code": "H10.023", "type": "ICD-10"}], note2, "office visit, established patient", notes)[0]["code"] == "99213"
    rest = "SUBJECTIVE: cold.\nOBJECTIVE: clear.\nASSESSMENT: Viral URI.\nPLAN: Rest and fluids."
    assert main._em_floor([{"code": "99213", "type": "CPT"}, {"code": "J06.9", "type": "ICD-10"}], rest, "office visit, established patient", notes)[0]["code"] == "99212"


def test_the_pipeline_fills_in_what_a_forgetful_ai_leaves_out(monkeypatch):
    """The coding test's cases, with an AI that answers only the visit code and diagnoses: the rules add every documented
    service, drop the visit code on procedure-only visits and add 25 where a second problem was seen."""
    import coding_eval
    import main
    cur = {}

    def fake(prompt, schema, name, **k):
        if name == "claim_build":
            e = cur["case"]["expect"]
            return {"codes": [{"code": s[0], "type": "CPT", "modifier": "", "units": 1} for s in e["cpt"] if s[0].startswith("992")]
                    + [{"code": s[0], "type": "ICD-10"} for s in e["icd10"]], "note_additions": [], "unresolved": []}
        return {"score": 95, "flags": [], "missing": []}
    monkeypatch.setattr(main, "_fast_json", fake)
    wrong = []
    for case in coding_eval.load_cases()["cases"]:
        cur["case"] = case
        r = main._claim_auto_work(case["note"], "", case["header"], case["visit"], [], 0, None)
        if not coding_eval.score(case, r["codes"])["correct"]:
            wrong.append(case["id"])
    assert wrong == []
