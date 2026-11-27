"""The AI never sees who the patient is (deid.py), and every AI-built claim passes deterministic rules with an audit trail
(claim_rules.py)."""
import os

import claim_rules as cr
import deid

CHART = open(os.path.join(os.path.dirname(__file__), "data", "test_chart.txt")).read() if os.path.exists(
    os.path.join(os.path.dirname(__file__), "data", "test_chart.txt")) else """PATIENT
Name: TESTPATIENT, MARIA
DOB: 03/14/1991 (35 y/o) · Sex: F
Address: 123 Test St, Odessa, TX 79761 · Phone: 432-555-0142
MRN: TEST-000142 · Established patient (last visit 04/2025)
Insurance: Blue Cross Blue Shield of Texas (commercial PPO)
Member ID: TST900000001 · Group: TEST0001 · Subscriber: self
Rendering / billing provider: Alex Smith, MD · NPI 1111111112
VISIT
Date of service: 11/24/2026
HISTORY: Maria cut her left forearm on a kitchen knife about 1 hour ago. Her daughter had strep last week.
ASSESSMENT AND PLAN
1. Strep throat, confirmed by rapid strep, with fever. Amoxicillin 500 mg twice daily for 10 days.
2. Laceration of left forearm, 3.5 cm, simple, repaired with 5 interrupted 4-0 nylon sutures.
3. Tetanus booster due: Tdap given today. Mrs. Testpatient tolerated it well."""
HEADER = ("patient TESTPATIENT, MARIA, DOB 1991-03-14, sex F, payer Blue Cross Blue Shield of Texas, member ID TST900000001, "
          "rendering provider Alex Smith, date of service 2026-11-24, place of service 11")
IDENTIFIERS = ["TESTPATIENT", "Testpatient", "MARIA", "Maria", "1991", "03/14", "TST900000001", "TEST-000142", "TEST0001",
               "432-555-0142", "123 Test St", "79761", "Odessa", "1111111112", "11/24/2026", "2026-11-24"]


# ── de-identification ──
def test_identity_is_removed_and_clinical_facts_stay():
    s = deid.scrubber(HEADER, CHART)
    out = s.scrub(HEADER) + "\n" + s.scrub(CHART)
    for x in IDENTIFIERS:
        assert x not in out, x
    for x in ("age 35", "Sex: F", "Blue Cross", "Strep throat", "3.5 cm", "left forearm", "Tdap", "[DATE 2025]", "[DATE 2026]",
              "Alex Smith, MD", "Subscriber: self"):
        assert x in out, x
    assert "[PATIENT]" in out and "[MEMBER ID]" in out
    assert s.summary().startswith("Removed before the AI saw the note:")


def test_restore_puts_the_real_values_back():
    s = deid.scrubber(HEADER, CHART)
    s.scrub(CHART)
    assert s.restore("[PATIENT] was counseled on wound care.") == "Maria Testpatient was counseled on wound care."
    assert deid.restore_obj(s, {"a": ["[PATIENT] ok"], "n": 3}) == {"a": ["Maria Testpatient ok"], "n": 3}


def test_very_old_ages_are_grouped():
    s = deid.scrubber("")
    assert s.scrub("DOB 01/02/1930, a 96 y/o man").count("90+") == 2


def test_more_identifiers():
    s = deid.scrubber("Mr. Okafor came in")
    t = s.scrub("Mr. Okafor, SSN 123-45-6789, email ok@x.com, see https://x.com/a, policy # AB12345. Okafor reports cough.")
    for x in ("Okafor", "123-45-6789", "ok@x.com", "https://x.com/a", "AB12345"):
        assert x not in t, x
    assert "reports cough" in t


def test_switch_off(monkeypatch):
    monkeypatch.setenv("DEID_NOTES", "0")
    assert deid.on() is False


def test_the_claim_builder_never_sends_identity(monkeypatch):
    import main
    prompts = []

    def fake_json(prompt, *a, **k):
        prompts.append(prompt)
        return {"codes": [{"code": "99214", "type": "CPT", "description": "", "modifier": "25", "units": 1, "reason": ""},
                          {"code": "12002", "type": "CPT", "description": "", "modifier": "", "units": 1, "reason": ""},
                          {"code": "J02.0", "type": "ICD-10", "description": "", "modifier": "", "units": 1, "reason": ""},
                          {"code": "S51.812A", "type": "ICD-10", "description": "", "modifier": "", "units": 1, "reason": ""}],
                "note_additions": [{"section": "Assessment", "text": "[PATIENT] was evaluated separately for strep.", "source_quote": "Strep throat"}],
                "unresolved": []}
    monkeypatch.setattr(main, "_fast_json", fake_json)
    monkeypatch.setattr(main, "_claim_check", lambda header, codes, note: prompts.append(header + note) or {"score": 95, "flags": [], "missing": []})
    best = main._claim_auto_work(CHART, "", HEADER, "urgent care visit", [], 0.0)
    sent = "\n".join(prompts)
    for x in IDENTIFIERS:
        assert x not in sent, x
    assert best["note_additions"][0]["text"] == "Maria Testpatient was evaluated separately for strep."
    assert best["privacy"].startswith("Removed before the AI saw the note:")
    assert isinstance(best["audit"], list)


def test_the_validate_check_never_sends_identity(monkeypatch):
    import main
    from test_clinic_ready import _client
    seen = []
    monkeypatch.setattr(main, "_claim_check", lambda header, codes, note: seen.append(note) or {"score": 90, "flags": ["[PATIENT] ok"], "missing": []})
    c = _client("deid-provider@example.com", role="provider", org="Deid Clinic")
    r = c.post("/api/validate-claim", json={"codes": [{"code": "99213", "type": "CPT"}], "note": CHART})
    assert r.status_code == 200
    assert seen and all(x not in seen[0] for x in IDENTIFIERS)
    assert r.json()["flags"] == ["Maria Testpatient ok"]


# ── deterministic rules ──
def _flags(codes, note="", header="payer Aetna"):
    return cr.apply(codes, note, header)[1]


def test_qw_is_added_for_medicare_and_flagged_for_others():
    codes, r = cr.apply([{"code": "87880", "type": "CPT"}], "", "payer Medicare Part B")
    assert codes[0]["modifier"] == "QW" and r.notes[0].startswith("[QW] Added modifier QW to 87880")
    codes, r = cr.apply([{"code": "87880", "type": "CPT"}], "", "payer Aetna")
    assert not codes[0].get("modifier") and r.flags[0].startswith("[QW]")


def test_vaccines_need_an_administration_code_and_z23():
    r = _flags([{"code": "90715", "type": "CPT"}, {"code": "S51.812A", "type": "ICD-10"}])
    assert any("without an administration code: add 90471" in f for f in r.flags)
    assert any("Z23" in f for f in r.flags)
    r = _flags([{"code": "90686", "type": "CPT"}, {"code": "Z23", "type": "ICD-10"}], header="payer Medicare Part B")
    assert any("G0008" in f for f in r.flags)
    assert not _flags([{"code": "90715", "type": "CPT"}, {"code": "90471", "type": "CPT"}, {"code": "Z23", "type": "ICD-10"}]).flags


def test_new_vs_established_and_time():
    r = _flags([{"code": "99204", "type": "CPT"}], "Established patient, last seen in 2025.")
    assert r.flags[0].startswith("[NEW-EST] 99204 is a new-patient code")
    r = _flags([{"code": "99214", "type": "CPT"}], "Total time 25 minutes.")
    assert "supports 99213 by time" in r.flags[0]
    r = _flags([{"code": "99213", "type": "CPT"}], "I spent 35 minutes total.")
    assert "supports 99214 by time (billed 99213)" in r.flags[0]


def test_external_cause_codes_move_after_the_injury():
    codes, r = cr.apply([{"code": "12002", "type": "CPT"}, {"code": "W26.0XXA", "type": "ICD-10"}, {"code": "S51.812A", "type": "ICD-10"}])
    assert [c["code"] for c in codes] == ["12002", "S51.812A", "W26.0XXA"]
    assert r.audit[0]["rule"] == "EXT-CAUSE" and r.audit[0]["action"] == "changed" and r.audit[0]["ref"] == "dx-outpatient"


def test_injury_seventh_character_against_the_note():
    assert _flags([{"code": "S51.812D", "type": "ICD-10"}], "Laceration repaired today with sutures. Suture removal in 10 days.").flags
    assert _flags([{"code": "S51.812A", "type": "ICD-10"}], "Patient here for suture removal. Healing well.").flags
    assert not _flags([{"code": "S51.812A", "type": "ICD-10"}], "Laceration repaired today. Suture removal in 10 days.").flags


def test_lacerations_and_duplicates():
    r = _flags([{"code": "12002", "type": "CPT"}, {"code": "12004", "type": "CPT"}])
    assert r.flags[0].startswith("[LACERATION] 12002 and 12004")
    assert _flags([{"code": "94640", "type": "CPT"}, {"code": "94640", "type": "CPT"}]).flags[0].startswith("[DUPLICATE]")
    assert not _flags([{"code": "94640", "type": "CPT"}, {"code": "94640", "type": "CPT", "modifier": "76"}]).flags


def test_same_input_same_output():
    codes = [{"code": "99214", "type": "CPT"}, {"code": "87880", "type": "CPT"}, {"code": "W26.0XXA", "type": "ICD-10"}, {"code": "J02.0", "type": "ICD-10"}]
    a = cr.apply(codes, CHART, "payer Medicare")
    b = cr.apply(codes, CHART, "payer Medicare")
    assert a[0] == b[0] and a[1].audit == b[1].audit
    assert codes[1].get("modifier") is None              # the input isn't modified


def test_every_rule_cites_a_real_reference_entry():
    import rag_corpus
    ids = {d["id"] for d in rag_corpus.CORPUS}
    src = open(cr.__file__).read()
    import re
    for ref in re.findall(r'r\.(?:fix|warn)\([^)]*?"([a-z0-9\-]+)"\)', src, re.S):
        assert ref in ids, ref
