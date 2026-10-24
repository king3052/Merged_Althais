"""The dental revenue cycle's rules: X12 (270/271, 837D, 835), frequency limits, estimates, missed and undercoded
procedures, narratives, ERA posting, payment plans and memberships. Pure functions, no database."""
import datetime as dt

import pytest

import dental_clinical as clin
import dental_rcm as rcm
import dental_vendors as vendors
import dental_x12 as x12

REQ = {"payer": "Delta Dental PPO", "member_id": "SYN12345678", "last": "Test", "first": "Alpha", "dob": "1986-04-12", "sex": "F",
       "provider_name": "Bright Smiles", "npi": "1234567893"}
PRACTICE = {"name": "Bright Smiles", "npi": "1234567893", "tin": "74-0000001", "addr": "1 Main St", "city": "Austin", "state": "TX", "zip": "78701", "phone": "5125550100"}


def claim(**kw):
    c = {"id": "DC1", "dos": "2026-10-20", "payer": "Delta Dental PPO", "group": "G1",
         "patient": {"last": "Test", "first": "Alpha", "dob": "1986-04-12", "sex": "F", "member_id": "SYN12345678"},
         "provider": {"last": "Okafor", "first": "Maya", "npi": "1234567893"},
         "lines": [{"code": "D2392", "tooth": "14", "surface": "MO", "fee": 225}, {"code": "D4341", "quad": "UR", "fee": 275}, {"code": "D0120", "fee": 65}]}
    c.update(kw)
    return c


# ── X12 ─────────────────────────────────────────────────────────────────────────────────────────────────────────
def test_270_is_a_valid_interchange():
    x = x12.build_270(REQ)
    segs, comp = x12.segments(x)
    ids = [s[0] for s in segs]
    assert ids[:3] == ["ISA", "GS", "ST"] and ids[-3:] == ["SE", "GE", "IEA"]
    assert ["EQ", "35"] in segs                                   # dental care
    st, se = ids.index("ST"), ids.index("SE")
    assert int(segs[se][1]) == se - st + 1


def test_271_round_trip_benefits_and_frequencies():
    b = x12.parse_271(vendors.MockClearinghouse().eligibility("", REQ))
    assert b["active"] and b["annual_max"] > 0 and 0 <= b["max_remaining"] <= b["annual_max"]
    assert b["coinsurance"]["preventive"] == 100 and b["coinsurance"]["major"] == 50
    f = {x["code"]: x for x in b["frequencies"]}
    assert f["D1110"]["limit"] == 2 and f["D1110"]["per"] == "calendar_year"
    assert f["D0210"]["per_months"] == 60
    assert f["D2740"]["per_tooth"] and f["D4341"]["per_quadrant"] and f["D1206"]["age_max"] == 18


def test_271_inactive():
    b = x12.parse_271(vendors.MockClearinghouse().eligibility("", dict(REQ, member_id="TERM123")))
    assert b["active"] is False


def test_837d_lines_teeth_surfaces_quadrants():
    x = x12.build_837d(claim(), PRACTICE)
    assert x12.validate_837d(x) == []
    segs, _ = x12.segments(x)
    clm = next(s for s in segs if s[0] == "CLM")
    assert clm[2] == "565" and clm[5] == "11:B:1"
    sv3 = [s for s in segs if s[0] == "SV3"]
    assert [s[1] for s in sv3] == ["AD:D2392", "AD:D4341", "AD:D0120"]
    assert sv3[1][4] == "10"                                       # upper right quadrant
    assert ["TOO", "JP", "14", "M:O"] in segs
    assert any(s[0] == "PRV" and s[3] == "1223G0001X" for s in segs)


def test_837d_attachments_narrative_and_replacement():
    x = x12.build_837d(claim(attachments=[{"kind": "radiograph", "ref": "NEA123"}, {"kind": "perio_chart", "ref": "NEA123"}],
                             narrative="Crown #30: fractured cusp", frequency="7", original_ref="PAYER999"), PRACTICE)
    segs, _ = x12.segments(x)
    assert ["PWK", "RB", "EL", "", "", "AC", "NEA123"] in segs and ["PWK", "P6", "EL", "", "", "AC", "NEA123"] in segs
    assert ["NTE", "ADD", "CROWN #30 FRACTURED CUSP"] in segs or any(s[0] == "NTE" and "CROWN #30" in s[2] for s in segs)
    assert ["REF", "F8", "PAYER999"] in segs
    assert next(s for s in segs if s[0] == "CLM")[5] == "11:B:7"


def test_837d_predetermination_marks_clm19():
    segs, _ = x12.segments(x12.build_837d(claim(), PRACTICE, predetermination=True))
    clm = next(s for s in segs if s[0] == "CLM")
    assert clm[19] == "PB"
    assert not any(s[0] == "DTP" and s[1] == "472" for s in segs)   # no service date on a predetermination


def test_837d_validation_catches_what_a_999_would():
    bad = claim(provider={"last": "X", "first": "Y", "npi": "123"}, lines=[{"code": "D2392", "fee": 225}, {"code": "D4341", "fee": 275}])
    bad["patient"]["member_id"] = ""
    errs = x12.validate_837d(x12.build_837d(bad, PRACTICE))
    assert any("NPI" in e for e in errs)
    assert any("Subscriber ID" in e for e in errs)
    assert any("D2392 needs a tooth" in e for e in errs)
    assert any("D4341 needs a quadrant" in e for e in errs)


def test_835_parse_payments_and_adjustments():
    x = x12.build_835({"payer": "Delta", "check": "EFT1", "date": "2026-10-23", "claims": [{
        "id": "DC1", "payer_claim_id": "P1", "patient": {"last": "T", "first": "A", "member_id": "M"},
        "lines": [{"code": "D2392", "fee": 225, "paid": 140, "allowed": 175, "adj": [["CO", "45", 50], ["PR", "2", 35]]},
                  {"code": "D0274", "fee": 75, "paid": 0, "allowed": 75, "adj": [["CO", "96", 75]]}]}]})
    r = x12.parse_835(x)
    assert r["check"] == "EFT1" and r["total"] == 140 and r["date"] == "2026-10-23"
    c = r["claims"][0]
    assert c["claim_id"] == "DC1" and c["paid"] == 140 and c["patient_resp"] == 35
    assert c["lines"][0]["adjustments"] == [{"group": "CO", "carc": "45", "amount": 50.0}, {"group": "PR", "carc": "2", "amount": 35.0}]
    assert c["lines"][0]["allowed"] == 175


def test_mock_clearinghouse_answers_a_sent_claim_with_an_835():
    ch = vendors.MockClearinghouse()
    vendors.MockClearinghouse._remits = []
    c = claim()
    res = ch.submit_claim(x12.build_837d(c, PRACTICE), c)
    assert res.ok and res.environment == "mock"
    remits = ch.fetch_remittances("")
    assert len(remits) == 1 and x12.parse_835(remits[0])["claims"][0]["claim_id"] == "DC1"


def test_mock_clearinghouse_rejects_a_broken_claim():
    c = claim(lines=[{"code": "D2392", "fee": 225}])
    res = vendors.MockClearinghouse().submit_claim(x12.build_837d(c, PRACTICE), c)
    assert not res.ok and res.status == "rejected"


# ── frequency limits and estimates ──────────────────────────────────────────────────────────────────────────────
BEN = {"active": True, "annual_max": 1500, "max_remaining": 400, "deductible": 50, "deductible_remaining": 50,
       "coinsurance": {"preventive": 100, "diagnostic": 100, "basic": 80, "major": 50, "perio": 80, "endo": 80, "oral_surgery": 80},
       "frequencies": [{"code": c, "limit": 2, "per": "calendar_year"} for c in ("D1110", "D0120", "D0150")] +
                      [{"code": "D0274", "limit": 1, "per": "calendar_year"}, {"code": "D2740", "limit": 1, "per_months": 60, "per_tooth": True},
                       {"code": "D4341", "limit": 1, "per_months": 24, "per_quadrant": True}, {"code": "D1206", "limit": 2, "per": "calendar_year", "age_max": 18}],
       "waiting_periods": []}


def test_frequency_cleanings_twice_a_year():
    hist = [{"code": "D1110", "date": "2026-02-01"}, {"code": "D1110", "date": "2026-08-01"}]
    f = rcm.frequency_check(BEN, hist, [{"code": "D1110"}], "2026-10-20")[0]
    assert not f["ok"] and f["next_eligible"] == "2027-01-01"
    assert rcm.frequency_check(BEN, hist[:1], [{"code": "D1110"}], "2026-10-20")[0]["ok"]
    assert rcm.frequency_check(BEN, hist, [{"code": "D1110"}], "2027-01-03")[0]["ok"]     # new year


def test_frequency_shared_exam_group_and_planned_lines_count():
    hist = [{"code": "D0150", "date": "2026-01-10"}]
    out = rcm.frequency_check(BEN, hist, [{"code": "D0120"}, {"code": "D0120"}], "2026-10-20")
    assert out[0]["ok"] and not out[1]["ok"]                     # comprehensive + periodic share the 2/year limit


def test_frequency_crown_per_tooth_and_srp_per_quadrant():
    hist = [{"code": "D2740", "date": "2023-03-01", "tooth": "30"}, {"code": "D4341", "date": "2026-01-01", "quad": "UR"}]
    out = rcm.frequency_check(BEN, hist, [{"code": "D2740", "tooth": "30"}, {"code": "D2740", "tooth": "19"}, {"code": "D4341", "quad": "UR"}, {"code": "D4341", "quad": "LL"}], "2026-10-20")
    assert [o["ok"] for o in out] == [False, True, False, True]
    assert out[0]["next_eligible"] == "2028-03-02"


def test_frequency_age_limit():
    assert not rcm.frequency_check(BEN, [], [{"code": "D1206"}], "2026-10-20", age=30)[0]["ok"]
    assert rcm.frequency_check(BEN, [], [{"code": "D1206"}], "2026-10-20", age=12)[0]["ok"]


def test_estimate_coinsurance_deductible_and_max():
    e = rcm.estimate(BEN, [{"code": "D1110", "fee": 120}, {"code": "D2392", "fee": 250}, {"code": "D2740", "tooth": "3", "fee": 1250}], [], "2026-10-20")
    l = e["lines"]
    assert l[0]["insurance"] == 120 and l[0]["patient"] == 0                        # preventive 100%, no deductible
    assert l[1]["insurance"] == 160 and l[1]["patient"] == 90                       # (250 - 50 deductible) * 80%
    assert l[2]["insurance"] == 120 and "maximum" in l[2]["note"]                   # $400 left: 120 + 160 already used above
    assert e["insurance"] == 400 and e["max_remaining_after"] == 0
    assert round(e["insurance"] + e["patient"], 2) == 1620


def test_estimate_frequency_failure_is_patient_pay():
    e = rcm.estimate(BEN, [{"code": "D0274", "fee": 75}], [{"code": "D0274", "date": "2026-03-01"}], "2026-10-20")
    assert e["lines"][0]["insurance"] == 0 and e["lines"][0]["patient"] == 75 and "Frequency" in e["lines"][0]["note"]


def test_estimate_no_coverage():
    e = rcm.estimate({"active": False}, [{"code": "D1110", "fee": 120}])
    assert e["insurance"] == 0 and e["patient"] == 120


# ── coding: missed and undercoded ───────────────────────────────────────────────────────────────────────────────
def codes(found):
    return {(f["kind"], f["code"], f["tooth"]) for f in found}


def test_fillings_in_the_note_but_not_on_the_claim():
    note = "Periodic exam. #14 MO composite restoration placed. #3 O composite placed. Adult prophylaxis completed."
    found = codes(rcm.find_missed(note, [{"code": "D0120"}, {"code": "D1110"}]))
    assert ("missed", "D2392", "14") in found and ("missed", "D2391", "3") in found


def test_undercoded_filling_surfaces():
    found = rcm.find_missed("#19 MOD composite restoration placed.", [{"code": "D2391", "tooth": "19", "surface": "MOD"}])
    assert [(f["kind"], f["code"], f["billed"]) for f in found] == [("undercoded", "D2393", "D2391")]


def test_anterior_and_amalgam_codes():
    found = codes(rcm.find_missed("#8 MI composite placed. #30 MO amalgam placed.", []))
    assert ("missed", "D2331", "8") in found and ("missed", "D2150", "30") in found


def test_existing_and_planned_work_isnt_flagged():
    note = "Existing #14 MO composite intact. Recommend #3 O composite next visit. Caries on #3 confirmed on existing bitewings from March."
    assert rcm.find_missed(note, []) == []


def test_bitewings_buildup_surgical_extraction():
    note = "Four bitewings taken today. #30 prepared for crown, buildup placed. Surgical extraction #17 with flap and sectioning."
    found = codes(rcm.find_missed(note, [{"code": "D2740", "tooth": "30"}, {"code": "D7140", "tooth": "17"}]))
    assert ("missed", "D0274", "") in found and ("missed", "D2950", "30") in found and ("undercoded", "D7210", "17") in found


def test_srp_by_quadrant_and_exam_level():
    note = "Comprehensive exam. Scaling and root planing upper right quadrant and lower left quadrant."
    found = rcm.find_missed(note, [{"code": "D0120"}, {"code": "D4341", "quad": "UR"}])
    assert {(f["code"], f["quad"]) for f in found if f["code"] == "D4341"} == {("D4341", "LL")}
    assert any(f["kind"] == "undercoded" and f["code"] == "D0150" for f in found)


def test_child_prophy():
    assert ("missed", "D1120", "") in codes(rcm.find_missed("Prophylaxis completed.", [], age=9))


# ── narratives and attachments ──────────────────────────────────────────────────────────────────────────────────
def test_srp_narrative_uses_the_perio_chart():
    perio = {"2": {"pd": [5, 4, 6, 3, 3, 3], "bop": [1, 0, 1, 0, 0, 0]}, "3": {"pd": [5, 5, 4, 3, 3, 3], "bop": [1, 1, 0, 0, 0, 0]}, "9": {"pd": [6, 6, 6, 6, 6, 6]}}
    t = rcm.narrative({"code": "D4341", "quad": "UR"}, {"perio": perio})
    assert "#2, #3" in t and "deepest 6 mm" in t and "4 bleeding sites" in t and "#9" not in t


def test_crown_narrative_and_attachments():
    t = rcm.narrative({"code": "D2740", "tooth": "30"}, {"conditions": [{"tooth": "30", "condition": "fracture"}]})
    assert t.startswith("Crown #30: fracture")
    kinds = {(a["code"], a["kind"]) for a in rcm.attachments_needed([{"code": "D2740", "tooth": "30"}, {"code": "D4341", "quad": "UR"}, {"code": "D1110"}])}
    assert ("D2740", "radiograph") in kinds and ("D4341", "perio_chart") in kinds and ("D4341", "radiograph") in kinds
    assert not any(c == "D1110" for c, _ in kinds)
    assert rcm.narrative({"code": "D1110"}, {}) == ""


# ── ERA posting ─────────────────────────────────────────────────────────────────────────────────────────────────
def remit(lines, check="EFT9", status_claim="DC1"):
    return x12.parse_835(x12.build_835({"payer": "Delta", "check": check, "date": "2026-10-23", "claims": [{"id": status_claim, "payer_claim_id": "P", "patient": {}, "lines": lines}]}))


MINE = {"DC1": {"lines": [{"code": "D2392", "fee": 225}, {"code": "D0274", "fee": 75}], "posted_checks": []}}


def test_post_remittance_payment_writeoff_patient_portion():
    r = rcm.post_remittance(remit([{"code": "D2392", "fee": 225, "paid": 140, "adj": [["CO", "45", 50], ["PR", "2", 35]]},
                                   {"code": "D0274", "fee": 75, "paid": 60, "adj": [["CO", "45", 15]]}]), MINE)
    assert r["exceptions"] == [] and r["denials"] == []
    p0, p1 = r["postings"]
    assert (p0["paid"], p0["write_off"], p0["patient_resp"]) == (140, 50, 35)
    assert (p1["paid"], p1["write_off"], p1["patient_resp"]) == (60, 15, 0)
    assert r["paid"] == 200 and r["patient_resp"] == 35


def test_post_remittance_denial_goes_to_the_queue():
    r = rcm.post_remittance(remit([{"code": "D2392", "fee": 225, "paid": 140, "adj": [["CO", "45", 50], ["PR", "2", 35]]},
                                   {"code": "D0274", "fee": 75, "paid": 0, "adj": [["CO", "96", 75]]}]), MINE)
    assert len(r["denials"]) == 1 and r["denials"][0]["code"] == "D0274" and r["denials"][0]["action"] == "patient"
    assert r["exceptions"] == []


def test_post_remittance_exceptions():
    unknown = rcm.post_remittance(remit([{"code": "D2392", "fee": 225, "paid": 100, "adj": []}], status_claim="NOPE"), MINE)
    assert unknown["exceptions"][0]["reason"].startswith("No claim")
    dup = rcm.post_remittance(remit([{"code": "D2392", "fee": 225, "paid": 140, "adj": [["CO", "45", 85]]}]), {"DC1": dict(MINE["DC1"], posted_checks=["EFT9"])})
    assert "already posted" in dup["exceptions"][0]["reason"]
    off = rcm.post_remittance(remit([{"code": "D2392", "fee": 225, "paid": 140, "adj": [["CO", "45", 50]]}, {"code": "D0274", "fee": 75, "paid": 75, "adj": []}]), MINE)
    assert any("unexplained" in e["reason"] for e in off["exceptions"])
    extra = rcm.post_remittance(remit([{"code": "D2392", "fee": 225, "paid": 225, "adj": []}, {"code": "D0274", "fee": 75, "paid": 75, "adj": []}, {"code": "D1110", "fee": 100, "paid": 100, "adj": []}]), MINE)
    assert any("isn't on our claim" in e["reason"] for e in extra["exceptions"])


def test_denial_actions():
    assert rcm.denial_action("252")["action"] == "attach"
    assert rcm.denial_action("CO-16")["action"] == "fix"
    assert rcm.denial_action("119")["action"] == "patient"
    assert rcm.denial_action("XYZ")["action"] == "review"


# ── patient billing ─────────────────────────────────────────────────────────────────────────────────────────────
def test_payment_plan_adds_up_to_the_cent():
    s = rcm.payment_plan(1000.00, 100, 7, "2026-01-31")
    assert s[0] == {"n": 0, "due": "2026-01-31", "amount": 100.0, "kind": "down payment"}
    assert round(sum(x["amount"] for x in s), 2) == 1000.00
    assert s[1]["due"] == "2026-02-28" and s[-1]["due"] == "2026-08-31"
    with pytest.raises(ValueError):
        rcm.payment_plan(100, 200, 3)


def test_membership_included_then_discounted():
    plan = {"included": {"D1110": 2, "D0120": 2}, "discount_pct": 20}
    r = rcm.membership_price(plan, [{"code": "D1110", "fee": 120}, {"code": "D2392", "fee": 250}], used={"D1110": 1})
    assert r["lines"][0]["price"] == 0 and r["lines"][1]["price"] == 200 and r["total"] == 200 and r["savings"] == 170
    r2 = rcm.membership_price(plan, [{"code": "D1110", "fee": 120}], used={"D1110": 2})
    assert r2["lines"][0]["price"] == 96                         # third cleaning: discounted, not free


def test_recall_due():
    r = rcm.recall_due([{"code": "D1110", "date": "2026-01-15"}], "2026-10-20")
    assert r["due"] == "2026-07-15" and r["overdue_days"] == 97
    r2 = rcm.recall_due([{"code": "D1110", "date": "2025-06-01"}, {"code": "D4910", "date": "2026-09-01"}], "2026-10-20")
    assert r2["interval_months"] == 3 and r2["due"] == "2026-12-01" and r2["overdue_days"] == 0


# ── clinical helpers ────────────────────────────────────────────────────────────────────────────────────────────
def test_voice_perio_walks_the_path_and_skips_missing_teeth():
    r = clin.parse_perio_speech("three two three, four three five bleeding, two two two", missing=[1])
    rd = r["readings"]
    assert rd["2"]["pd"][:3] == [3, 2, 3] and rd["3"]["pd"][:3] == [4, 3, 5] and rd["4"]["pd"][:3] == [2, 2, 2]
    assert rd["3"]["bop"][:3] == [1, 1, 1] and rd["2"]["bop"] == [0] * 6
    assert r["next"] == (5, "F")


def test_voice_perio_commands():
    r = clin.parse_perio_speech("tooth 14 lingual 5 6 5 mobility 2 back 4 4 4 recession 1 2 1 furcation 1")
    v = r["readings"]["14"]
    assert v["pd"][3:] == [4, 4, 4]                               # lingual sites, after the undo
    assert v["rec"][3:] == [1, 2, 1] and v["mob"] == 2 and v["furc"] == 1


def test_perio_summary_suggests_srp_by_quadrant():
    r = clin.empty_readings()
    for t in (2, 3, 4, 5):
        r[str(t)]["pd"] = [5, 4, 5, 3, 3, 3]
        r[str(t)]["bop"] = [1, 0, 1, 0, 0, 0]
    r["19"]["pd"] = [4, 3, 3, 3, 3, 3]
    s = clin.perio_summary(r)
    assert s["sites_4"] == 13 and s["sites_5"] == 8
    assert {(x["quad"], x["code"]) for x in s["srp"]} == {("UR", "D4341"), ("LL", "D4342")}
    assert "dentist confirms" in s["note"]


def test_ambient_note_extracts_procedures():
    d = clin.draft_note(vendors.MockTranscriber.SAMPLE)
    got = {(p["code"], p["tooth"]) for p in d["procedures"]}
    assert {("D2392", "14"), ("D2391", "3"), ("D0274", ""), ("D1110", "")} <= got
    assert d["note"].startswith("S: Cold hurts") and "lidocaine" in d["note"] and d["status"] == "draft"


def test_synthetic_radiograph_is_labeled():
    svg = clin.synthetic_radiograph("bitewing", ["2", "3", "30"], [{"tooth": "3", "condition": "caries", "surface": "M"}], "x")
    assert svg.startswith("<svg") and "SYNTHETIC" in svg


def test_vendors_default_to_mocks():
    for v in vendors.status():
        assert v["mock"]
    assert vendors.MockPayments().charge(100, "tok_declined", "x").status == "declined"
    assert not vendors.MockEPrescriber().send({"drug": "Hydrocodone", "sig": "x", "controlled": True}).ok
    assert vendors.MockSms().send("123", "x").status == "invalid_number"
