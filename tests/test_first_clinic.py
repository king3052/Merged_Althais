"""Before the first real clinic: payment files as real clearinghouses write them, the guide for getting them, and the setup
checklist telling a report the clinic built from one Althais built on its own."""
import datetime as dt

from sqlalchemy import select

import clinic_setup as cs
import lost_money as lm
from test_clinic_ready import _claim, _client, _ok
from test_lost_money import CLAIMMD_ERA

TODAY = dt.date(2026, 11, 1)
IDS = ["ALT-TEST-CLEAN-1102", "ALT-TEST-DENY-1102"]


def _read(text, name="era.835"):
    a = lm.analyze([(name, text if isinstance(text, bytes) else text.encode())], TODAY)
    return sorted(r.claim_id for r in a.remits), a


def _wrap(text, width=80):
    return "\r\n".join(text[i:i + width] for i in range(0, len(text), width))


# ── payment files as they come from real systems ──
def test_lines_wrapped_at_80_columns():
    ids, a = _read(_wrap(CLAIMMD_ERA))
    assert ids == IDS
    clean = next(r for r in a.remits if r.claim_id == IDS[0])
    assert clean.lines[0].code == "99213" and clean.lines[0].allowed == 94.56 and clean.paid == 92.76


def test_wrapped_right_after_the_isa_header():
    i = CLAIMMD_ERA.index(":~GS") + 1          # the line break falls between ISA16 and the segment terminator
    ids, _ = _read(CLAIMMD_ERA[:i] + "\n" + CLAIMMD_ERA[i:])
    assert ids == IDS


def test_one_segment_per_line_and_newline_terminators():
    assert _read(CLAIMMD_ERA.replace("~", "~\r\n"))[0] == IDS
    assert _read(CLAIMMD_ERA.replace("~", "\n"))[0] == IDS


def test_other_separators():
    x = CLAIMMD_ERA.replace("*", "|").replace(":", ">")
    assert _read(x)[0] == IDS


def test_isa_header_not_padded_to_fixed_width():
    short = CLAIMMD_ERA.replace("*ZZ*60054          *ZZ*34590          *", "*ZZ*60054*ZZ*34590*", 1)
    assert _read(short)[0] == IDS


def test_byte_order_marks_trailing_nulls_and_utf16():
    assert _read(b"\xef\xbb\xbf" + CLAIMMD_ERA.encode() + b"\x00\x00\r\n")[0] == IDS
    assert _read(CLAIMMD_ERA.encode("utf-16"))[0] == IDS
    assert _read(CLAIMMD_ERA.encode("utf-16-le"))[0] == IDS


def test_several_payers_in_one_interchange():
    second = ("ST*835*0002~BPR*I*50*C*ACH*CCP*01*1*DA*1*1*1*01*1*DA*1*20261103~TRN*1*CHK2*1~N1*PR*CIGNA~N1*PE*SAMPLE*XX*1111111112~"
              "LX*1~CLP*C2*1*100*50*0*12*PX2~NM1*QC*1*DOE*PAT~SVC*HC:99212*100*50**1~DTM*472*20261020~CAS*CO*45*50~SE*10*0002~")
    _, a = _read(CLAIMMD_ERA.replace("SE*30*0001~", "SE*30*0001~" + second))
    assert {(r.claim_id, r.payer) for r in a.remits} == {(IDS[0], "AETNA"), (IDS[1], "AETNA"), ("C2", "CIGNA")}
    assert next(r for r in a.remits if r.claim_id == "C2").date == "2026-11-03"


def test_wrapped_837_claim_file():
    x837 = lm.build_837([{"id": "W1", "payer": "AETNA", "dos": "2026-08-01", "lines": [("99214", ["25"], 210.0, 1)]}])
    ds = lm.Dataset()
    ds.add("claims.837", _wrap(x837).encode())
    assert [c.claim_id for c in ds.claims] == ["W1"] and ds.claims[0].lines[0].mods == ["25"]


def test_a_cut_off_header_is_reported_not_fatal():
    _, a = _read("ISA*00*  *00*  *ZZ*1*ZZ*2*261102*0257*^*00501")
    assert a.remits == [] and a.ds.files[0]["error"]


def test_kind_comes_from_the_st_segment():
    assert lm.x12_kind(_wrap(CLAIMMD_ERA)) == "835"
    assert lm.x12_kind("not x12") == ""


# ── the guide ──
def test_the_payment_file_guide_is_public_and_links_from_the_app():
    from fastapi.testclient import TestClient
    import main
    r = TestClient(main.app).get("/help/payment-files")
    assert r.status_code == 200 and "835" in r.text and "Don't email these files" in r.text
    assert "[START DATE]" in r.text       # the request to send the vendor
    c = _client("guide-biller@example.com", org="Guide Clinic")
    steps = {s["key"]: s for s in c.get("/api/clinic-setup").json()["checklist"]}
    assert steps["files"]["link"]["href"] == "/help/payment-files"


# ── the checklist ──
def test_checklist_shows_the_report_the_clinic_built():
    import auth
    c = _client("built-biller@example.com", org="Built Clinic")
    steps = {s["key"]: s for s in c.get("/api/clinic-setup").json()["checklist"]}
    assert steps["report"]["done"] is False
    era = lm.build_835("AETNA", "2026-09-10", [_claim("B1", "2026-09-01", [_ok("99213", 80.0)])])
    r = c.post("/api/lost-money/report", files={"files": ("a.835", era.encode())}, data={"fmt": "json", "save": "1"})
    assert r.status_code == 200
    steps = {s["key"]: s for s in c.get("/api/clinic-setup").json()["checklist"]}
    assert steps["report"]["done"] is True and "by built-biller@example.com" in steps["report"]["how"]
    with auth.SessionLocal() as db:
        assert cs.load(db, "Built Clinic")["lastReport"]["by"] == "built-biller@example.com"


def test_checklist_names_an_automatic_check_as_one(monkeypatch):
    import auth
    import monitor
    monkeypatch.setattr(monitor, "status", lambda db, org: {"lastRun": "2026-11-13T08:00:00Z"})
    with auth.SessionLocal() as db:
        steps = {s["key"]: s for s in cs.checklist(db, "Auto Clinic")}
        assert steps["report"]["done"] is True and steps["report"]["how"].startswith("Althais checked your payment files Nov 13, 2026")
        cs.report_built(db, "Auto Clinic", "pat@example.com", now=dt.datetime(2026, 11, 20, 9))
        steps = {s["key"]: s for s in cs.checklist(db, "Auto Clinic")}
        assert steps["report"]["how"] == "Last built Nov 20, 2026 by pat@example.com."
        db.rollback()
