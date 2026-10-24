"""X12 5010 for dental: eligibility (270 out, 271 in), claims and predeterminations (837D out), remittance (835 in).

Pure functions: no database, no network. dental_vendors.py sends what these build; dental_rcm.py stores what these parse.

  build_270(req)            -> X12 string            005010X279A1
  parse_271(x12)            -> benefits dict         active, annual max/used/remaining, deductible, coinsurance by
                                                     category, frequency limits (HSD) per CDT code group
  build_837d(claim, ...)    -> X12 string            005010X224A2, SV3 lines with TOO tooth/surfaces, PWK attachments,
                                                     NTE narrative, CLM19 "PB" for a predetermination
  validate_837d(x12)        -> [errors]              the structural checks a clearinghouse's 999 would make
  parse_835(x12)            -> remittance dict       per claim (CLP) and line (SVC) payments with CAS adjustments
  build_271 / build_835     -> X12 string            used by the mock clearinghouse and tests only

Payers differ in how they fill a 271 (some send frequencies as EB*F with HSD, some only as free text in MSG). The parser
reads the structured forms and keeps MSG text alongside, so the front desk still sees what the payer said.
"""
import datetime as dt
import re

import dental_rules

SEG, EL, COMP, REP = "~", "*", ":", "^"

# plan benefit categories, and the X12 service type codes (EB03) payers use for them
STC = {"dental": "35", "diagnostic": "23", "preventive": "41", "basic": "25", "endo": "26", "perio": "24", "major": "36",
       "prosth": "39", "oral_surgery": "40", "ortho": "38"}
STC_NAME = {v: k for k, v in STC.items()}
QUAD_CODE = {"UR": "10", "UL": "20", "LL": "30", "LR": "40", "UA": "01", "LA": "02"}
CODE_QUAD = {v: k for k, v in QUAD_CODE.items()}


def benefit_category(code: str) -> str:
    """Which coverage bucket a CDT code is usually paid under. Plans vary; this is the common PPO layout."""
    n = int(code[1:]) if re.match(r"^D\d{4}$", code or "") else -1
    if 100 <= n <= 999:
        return "diagnostic"
    if 1000 <= n <= 1999:
        return "preventive"
    if 2500 <= n <= 2799 or 2960 <= n <= 2962 or 5000 <= n <= 6999:
        return "major"
    if 2000 <= n <= 2999 or 9000 <= n <= 9999:
        return "basic"
    if 3000 <= n <= 3999:
        return "endo"
    if 4000 <= n <= 4999:
        return "perio"
    if 7000 <= n <= 7999:
        return "oral_surgery"
    if 8000 <= n <= 8999:
        return "ortho"
    return "basic"


# ── low level ───────────────────────────────────────────────────────────────────────────────────────────────────
def _clean(s, n=60) -> str:
    """X12 text: no delimiters, upper case, trimmed."""
    return re.sub(r"[~*:^\n\r]", " ", str(s or "")).strip().upper()[:n]


def _d8(d) -> str:
    return str(d or "").replace("-", "")[:8]


def _money(x) -> str:
    s = f"{float(x or 0):.2f}"
    return s[:-3] if s.endswith(".00") else s.rstrip("0").rstrip(".") if "." in s else s


def _seg(*els) -> str:
    out = list(els)
    while len(out) > 1 and out[-1] in ("", None):
        out.pop()
    return EL.join("" if e is None else str(e) for e in out) + SEG


def _envelope(func_id: str, version: str, body: list, ctrl: int, sender="ALTHAIS", receiver="CLEARINGHOUSE", test=True) -> str:
    now = dt.datetime.now(dt.timezone.utc)
    isa = ("ISA*00*" + " " * 10 + "*00*" + " " * 10 + "*ZZ*" + sender.ljust(15)[:15] + "*ZZ*" + receiver.ljust(15)[:15] + "*" +
           now.strftime("%y%m%d") + "*" + now.strftime("%H%M") + "*" + REP + "*00501*" + str(ctrl).zfill(9) + "*0*" + ("T" if test else "P") + "*" + COMP + SEG)
    st = [_seg("ST", {"HS": "270", "HB": "271", "HC": "837", "HP": "835"}[func_id], "0001", version)] + body
    st.append(_seg("SE", len(st) + 1, "0001"))
    return (isa + _seg("GS", func_id, sender, receiver, now.strftime("%Y%m%d"), now.strftime("%H%M"), ctrl, "X", version) +
            "".join(st) + _seg("GE", 1, ctrl) + _seg("IEA", 1, str(ctrl).zfill(9)))


def segments(x: str) -> list:
    """Split an interchange into [[id, el1, el2...], ...], reading the delimiters from the ISA header."""
    x = (x or "").strip()
    if not x.startswith("ISA") or len(x) < 106:
        raise ValueError("Not an X12 interchange (no ISA header)")
    el, comp, term = x[3], x[104], x[105]
    out = []
    for raw in x.split(term):
        raw = raw.strip("\r\n ")
        if raw:
            out.append(raw.split(el))
    return out, comp


def _comp(v: str, comp: str) -> list:
    return (v or "").split(comp)


# ── 270 / 271 ───────────────────────────────────────────────────────────────────────────────────────────────────
def build_270(req: dict, ctrl: int = 1) -> str:
    """req: payer, payer_id, provider_name, npi, last, first, member_id, dob (YYYY-MM-DD), sex, date."""
    today = req.get("date") or dt.date.today().isoformat()
    body = [
        _seg("BHT", "0022", "13", req.get("trace") or "E" + str(ctrl), _d8(today), "1200"),
        _seg("HL", 1, "", 20, 1), _seg("NM1", "PR", 2, _clean(req.get("payer")), "", "", "", "", "PI", _clean(req.get("payer_id") or "MOCK")),
        _seg("HL", 2, 1, 21, 1), _seg("NM1", "1P", 2, _clean(req.get("provider_name")), "", "", "", "", "XX", req.get("npi") or ""),
        _seg("HL", 3, 2, 22, 0), _seg("TRN", 1, req.get("trace") or "E" + str(ctrl), "9ALTHAIS"),
        _seg("NM1", "IL", 1, _clean(req.get("last")), _clean(req.get("first")), "", "", "", "MI", _clean(req.get("member_id"))),
        _seg("DMG", "D8", _d8(req.get("dob")), (req.get("sex") or "U")[:1].upper()),
        _seg("DTP", "291", "D8", _d8(today)),
        _seg("EQ", "35"),
    ]
    return _envelope("HS", "005010X279A1", body, ctrl)


def build_271(req: dict, active: bool, plan: dict = None, ctrl: int = 1) -> str:
    """A payer's answer, used by the mock clearinghouse. Shaped like real dental 271s (EB loops with HSD frequencies)."""
    plan = plan or {}
    body = [
        _seg("BHT", "0022", "11", req.get("trace") or "E1", _d8(dt.date.today().isoformat()), "1200"),
        _seg("HL", 1, "", 20, 1), _seg("NM1", "PR", 2, _clean(req.get("payer")), "", "", "", "", "PI", _clean(req.get("payer_id") or "MOCK")),
        _seg("HL", 2, 1, 21, 1), _seg("NM1", "1P", 2, _clean(req.get("provider_name")), "", "", "", "", "XX", req.get("npi") or ""),
        _seg("HL", 3, 2, 22, 0), _seg("TRN", 2, req.get("trace") or "E1", "9ALTHAIS"),
        _seg("NM1", "IL", 1, _clean(req.get("last")), _clean(req.get("first")), "", "", "", "MI", _clean(req.get("member_id"))),
    ]
    if plan.get("group"):
        body.append(_seg("REF", "6P", plan["group"], _clean(plan.get("plan_name"))))
    body.append(_seg("DMG", "D8", _d8(req.get("dob")), (req.get("sex") or "U")[:1].upper()))
    if not active:
        body.append(_seg("EB", "6", "", "35"))
        body.append(_seg("MSG", "COVERAGE TERMINATED"))
        return _envelope("HB", "005010X279A1", body, ctrl)
    body.append(_seg("EB", "1", "IND", "35", "", _clean(plan.get("plan_name"))))
    body.append(_seg("DTP", "346", "D8", _d8(dt.date(dt.date.today().year, 1, 1).isoformat())))
    body.append(_seg("EB", "C", "IND", "35", "", "", "23", _money(plan.get("deductible", 0))))
    body.append(_seg("EB", "C", "IND", "35", "", "", "29", _money(max(0, plan.get("deductible", 0) - plan.get("deductible_met", 0)))))
    body.append(_seg("EB", "F", "IND", "35", "", "", "23", _money(plan.get("annual_max", 0))))
    body.append(_seg("EB", "F", "IND", "35", "", "", "29", _money(max(0, plan.get("annual_max", 0) - plan.get("max_used", 0)))))
    if plan.get("ortho_lifetime_max"):
        body.append(_seg("EB", "F", "IND", "38", "", "", "32", _money(plan["ortho_lifetime_max"])))
    for cat, pct in (plan.get("coinsurance") or {}).items():
        if cat in STC:
            # EB08 is the patient's share
            body.append(_seg("EB", "A", "IND", STC[cat], "", "", "27", "", f"{(100 - pct) / 100:.2f}".rstrip("0").rstrip(".") if pct < 100 else "0"))
    if plan.get("waiting_period_major_months"):
        body.append(_seg("EB", "D", "IND", STC["major"]))
        body.append(_seg("MSG", f"WAITING PERIOD {plan['waiting_period_major_months']} MONTHS FOR MAJOR SERVICES"))
    for f in plan.get("frequencies") or []:
        for code in f["codes"]:
            body.append(_seg("EB", "F", "IND", "35", "", "", "", "", "", "", "", "", "", COMP.join(["AD", code])))
            if f.get("per_months"):
                body.append(_seg("HSD", "VS", f["limit"], "", "", "34", f["per_months"]))
            else:
                body.append(_seg("HSD", "VS", f["limit"], "", "", "23" if f.get("per") == "calendar_year" else "22", 1))
            if f.get("per_tooth"):
                body.append(_seg("MSG", "PER TOOTH"))
            if f.get("per_quadrant"):
                body.append(_seg("MSG", "PER QUADRANT"))
            if f.get("age_max"):
                body.append(_seg("MSG", f"AGE LIMIT {f['age_max']}"))
        for h in f.get("history", []):
            body.append(_seg("DTP", "304", "D8", _d8(h)))
    return _envelope("HB", "005010X279A1", body, ctrl)


def parse_271(x: str) -> dict:
    segs, comp = segments(x)
    out = {"active": False, "plan_name": "", "group": "", "annual_max": None, "max_remaining": None, "deductible": None,
           "deductible_remaining": None, "ortho_lifetime_max": None, "coinsurance": {}, "frequencies": [], "messages": [],
           "waiting_periods": [], "subscriber": {}}
    cur = None   # the EB being described by following HSD/MSG/DTP
    for s in segs:
        sid = s[0]
        g = lambda i: s[i] if len(s) > i else ""
        if sid == "NM1" and g(1) == "IL":
            out["subscriber"] = {"last": g(3), "first": g(4), "member_id": g(9)}
        elif sid == "REF" and g(1) == "6P":
            out["group"], out["plan_name"] = g(2), out["plan_name"] or g(3)
        elif sid == "EB":
            code, stc, period, amount, pct = g(1), g(3), g(6), g(7), g(8)
            proc = _comp(g(13), comp)
            cur = None
            if code == "1":
                out["active"] = True
                out["plan_name"] = out["plan_name"] or g(5)
            elif code == "6":
                out["active"] = False
            elif code == "C" and amount:
                out["deductible_remaining" if period == "29" else "deductible"] = float(amount)
            elif code == "F" and len(proc) >= 2 and proc[0] == "AD":
                cur = {"code": proc[1], "limit": None, "per": None, "per_months": None, "per_tooth": False, "per_quadrant": False, "age_max": None, "last": []}
                out["frequencies"].append(cur)
            elif code == "F" and amount:
                if stc == STC["ortho"]:
                    out["ortho_lifetime_max"] = float(amount)
                elif period == "29":
                    out["max_remaining"] = float(amount)
                else:
                    out["annual_max"] = float(amount)
            elif code == "A" and stc in STC_NAME:
                out["coinsurance"][STC_NAME[stc]] = round(100 - float(pct or 0) * 100)
            elif code == "D":
                cur = {"waiting": STC_NAME.get(stc, stc)}
                out["waiting_periods"].append(cur)
        elif sid == "HSD" and cur is not None and "code" in cur:
            cur["limit"] = int(float(g(2) or 0))
            if g(5) == "34":
                cur["per_months"] = int(float(g(6) or 0))
            else:
                cur["per"] = "calendar_year" if g(5) == "23" else "benefit_year"
        elif sid == "MSG":
            text = g(1)
            out["messages"].append(text)
            if cur is not None and "code" in cur:
                if text == "PER TOOTH":
                    cur["per_tooth"] = True
                elif text == "PER QUADRANT":
                    cur["per_quadrant"] = True
                elif text.startswith("AGE LIMIT"):
                    cur["age_max"] = int(re.sub(r"\D", "", text) or 0) or None
            elif cur is not None and "waiting" in cur:
                m = re.search(r"(\d+)\s*MONTH", text)
                cur["months"] = int(m.group(1)) if m else None
        elif sid == "DTP" and g(1) == "304" and cur is not None and "code" in cur:
            d = g(3)
            cur["last"].append(f"{d[:4]}-{d[4:6]}-{d[6:8]}")
    if out["annual_max"] is not None and out["max_remaining"] is not None:
        out["max_used"] = round(out["annual_max"] - out["max_remaining"], 2)
    return out


# ── 837D ────────────────────────────────────────────────────────────────────────────────────────────────────────
PWK_TYPE = {"radiograph": "RB", "perio_chart": "P6", "narrative": "OZ", "photo": "OZ", "model": "DA"}


def build_837d(claim: dict, practice: dict, ctrl: int = 1, predetermination: bool = False) -> str:
    """claim: id, dos, payer, payer_id, group, frequency ("1" original, "7" replacement, "8" void), original_ref,
       preauth_ref, narrative, attachments [{kind, ref}], provider {last, first, npi},
       patient {last, first, dob, sex, member_id, addr, city, state, zip},
       lines [{code, tooth, surface, quad, fee, units, dos}]
    practice: name, npi, tin, addr, city, state, zip, phone, taxonomy"""
    pt, prov = claim.get("patient") or {}, claim.get("provider") or {}
    taxonomy = practice.get("taxonomy") or "1223G0001X"   # general practice dentistry
    total = sum(float(l.get("fee") or 0) * int(l.get("units") or 1) for l in claim.get("lines", []))
    body = [
        _seg("BHT", "0019", "00", _clean(claim.get("id"), 30), _d8(dt.date.today().isoformat()), "1200", "CH"),
        _seg("NM1", "41", 2, _clean(practice.get("name")), "", "", "", "", "46", practice.get("tin") or ""),
        _seg("PER", "IC", _clean(practice.get("name"), 30), "TE", re.sub(r"\D", "", practice.get("phone") or "") or "0000000000"),
        _seg("NM1", "40", 2, _clean(claim.get("payer")), "", "", "", "", "46", _clean(claim.get("payer_id") or "MOCK")),
        _seg("HL", 1, "", 20, 1), _seg("PRV", "BI", "PXC", taxonomy),
        _seg("NM1", "85", 2, _clean(practice.get("name")), "", "", "", "", "XX", practice.get("npi") or ""),
        _seg("N3", _clean(practice.get("addr"), 55)), _seg("N4", _clean(practice.get("city"), 30), _clean(practice.get("state"), 2), re.sub(r"\D", "", practice.get("zip") or "")),
        _seg("REF", "EI", re.sub(r"\D", "", practice.get("tin") or "")),
        _seg("HL", 2, 1, 22, 0), _seg("SBR", "P", "18", _clean(claim.get("group"), 30), "", "", "", "", "", "CI"),
        _seg("NM1", "IL", 1, _clean(pt.get("last")), _clean(pt.get("first")), "", "", "", "MI", _clean(pt.get("member_id"))),
        _seg("N3", _clean(pt.get("addr") or "UNKNOWN", 55)), _seg("N4", _clean(pt.get("city") or "UNKNOWN", 30), _clean(pt.get("state") or "TX", 2), re.sub(r"\D", "", pt.get("zip") or "") or "00000"),
        _seg("DMG", "D8", _d8(pt.get("dob")), (pt.get("sex") or "U")[:1].upper()),
        _seg("NM1", "PR", 2, _clean(claim.get("payer")), "", "", "", "", "PI", _clean(claim.get("payer_id") or "MOCK")),
        _seg("CLM", _clean(claim.get("id"), 38), _money(total), "", "", COMP.join(["11", "B", str(claim.get("frequency") or "1")]), "Y", "A", "Y", "Y",
             "", "", "", "", "", "", "", "", "", "PB" if predetermination else ""),
    ]
    if claim.get("original_ref") and str(claim.get("frequency")) in ("7", "8"):
        body.append(_seg("REF", "F8", _clean(claim["original_ref"], 50)))
    if claim.get("preauth_ref") and not predetermination:
        body.append(_seg("REF", "G3", _clean(claim["preauth_ref"], 50)))
    for a in claim.get("attachments") or []:
        body.append(_seg("PWK", PWK_TYPE.get(a.get("kind"), "OZ"), "EL", "", "", "AC", _clean(a.get("ref") or claim.get("id"), 50)))
    if claim.get("narrative"):
        body.append(_seg("NTE", "ADD", _clean(claim["narrative"], 80)))
    body += [_seg("NM1", "82", 1, _clean(prov.get("last")), _clean(prov.get("first")), "", "", "", "XX", prov.get("npi") or ""),
             _seg("PRV", "PE", "PXC", taxonomy)]
    for i, l in enumerate(claim.get("lines", []), 1):
        code = (l.get("code") or "").upper()
        area = QUAD_CODE.get((l.get("quad") or "").upper(), "")
        body.append(_seg("LX", i))
        body.append(_seg("SV3", COMP.join(["AD", code]), _money(float(l.get("fee") or 0) * int(l.get("units") or 1)), "", area, "", int(l.get("units") or 1)))
        tooth = dental_rules.norm_tooth(l.get("tooth"))
        if tooth:
            surf = dental_rules.norm_surface(l.get("surface"))
            body.append(_seg("TOO", "JP", tooth, COMP.join(list(surf)) if surf else ""))
        if not predetermination:
            body.append(_seg("DTP", "472", "D8", _d8(l.get("dos") or claim.get("dos"))))
        body.append(_seg("REF", "6R", f"{_clean(claim.get('id'), 20)}-{i}"))
    return _envelope("HC", "005010X224A2", body, ctrl)


def validate_837d(x: str) -> list:
    """What a clearinghouse's 999/277CA would reject before the payer ever sees it."""
    errs = []
    try:
        segs, comp = segments(x)
    except ValueError as e:
        return [str(e)]
    ids = [s[0] for s in segs]
    for need in ("ISA", "GS", "ST", "BHT", "CLM", "SE", "GE", "IEA"):
        if need not in ids:
            errs.append(f"Missing {need} segment")
    if errs:
        return errs
    st, se = ids.index("ST"), ids.index("SE")
    if int(segs[se][1]) != se - st + 1:
        errs.append(f"SE count {segs[se][1]} doesn't match {se - st + 1} segments")
    clm = segs[ids.index("CLM")]
    lines = [s for s in segs if s[0] == "SV3"]
    if not lines:
        errs.append("No service lines (SV3)")
    total = round(sum(float(s[2] or 0) for s in lines), 2)
    if abs(total - float(clm[2] or 0)) > 0.005:
        errs.append(f"Claim total {clm[2]} doesn't equal the lines ({total:.2f})")
    for s in segs:
        if s[0] == "NM1" and s[1] in ("85", "82") and not re.fullmatch(r"\d{10}", s[9] if len(s) > 9 else ""):
            errs.append(("Billing" if s[1] == "85" else "Rendering") + " provider NPI must be 10 digits")
        if s[0] == "NM1" and s[1] == "IL" and not (s[9] if len(s) > 9 else ""):
            errs.append("Subscriber ID is missing")
    # tooth numbers for codes that need them (SV3 followed by TOO)
    for i, s in enumerate(segs):
        if s[0] == "SV3":
            code = _comp(s[1], comp)[1] if comp in s[1] else s[1]
            nxt = segs[i + 1] if i + 1 < len(segs) else []
            if dental_rules.needs_tooth(code) and (not nxt or nxt[0] != "TOO"):
                errs.append(f"{code} needs a tooth number (TOO)")
            if dental_rules.needs_quad(code) and not (s[4] if len(s) > 4 else ""):
                errs.append(f"{code} needs a quadrant (SV304)")
    return errs


# ── 835 ─────────────────────────────────────────────────────────────────────────────────────────────────────────
CLP_STATUS = {"1": "processed_primary", "2": "processed_secondary", "3": "processed_tertiary", "4": "denied", "19": "processed_primary_forwarded",
              "22": "reversal", "23": "not_our_claim"}


def build_835(remit: dict, ctrl: int = 1) -> str:
    """remit: payer, check, date, claims [{id, payer_claim_id, patient {last, first, member_id}, lines [{code, tooth, fee, paid, allowed, adj [[group, carc, amt]]}]}]"""
    claims = remit.get("claims", [])
    total = round(sum(sum(float(l.get("paid") or 0) for l in c.get("lines", [])) for c in claims), 2)
    body = [_seg("BPR", "I", _money(total), "C", "ACH", "CCP", "", "", "", "", "", "", "", "", "", "", _d8(remit.get("date"))),
            _seg("TRN", 1, _clean(remit.get("check"), 30), "1MOCKPAYER"),
            _seg("DTM", "405", _d8(remit.get("date"))),
            _seg("N1", "PR", _clean(remit.get("payer"))), _seg("N1", "PE", "ALTHAIS DENTAL", "XX", "1234567893")]
    for c in claims:
        charge = sum(float(l.get("fee") or 0) for l in c.get("lines", []))
        paid = sum(float(l.get("paid") or 0) for l in c.get("lines", []))
        pr = sum(a[2] for l in c.get("lines", []) for a in l.get("adj", []) if a[0] == "PR")
        status = "4" if paid == 0 and charge > 0 else "1"
        pt = c.get("patient") or {}
        body.append(_seg("CLP", _clean(c.get("id"), 38), status, _money(charge), _money(paid), _money(pr), "12", c.get("payer_claim_id", ""), "11"))
        body.append(_seg("NM1", "QC", 1, _clean(pt.get("last")), _clean(pt.get("first")), "", "", "", "MI", _clean(pt.get("member_id"))))
        for i, l in enumerate(c.get("lines", []), 1):
            body.append(_seg("SVC", COMP.join(["AD", l.get("code", "")]), _money(l.get("fee")), _money(l.get("paid")), "", 1))
            body.append(_seg("DTM", "472", _d8(l.get("dos") or c.get("dos"))))
            for grp in ("CO", "PR", "OA", "PI"):
                adj = [a for a in l.get("adj", []) if a[0] == grp]
                if adj:
                    flat = []
                    for a in adj[:6]:
                        flat += [a[1], _money(a[2]), ""]
                    body.append(_seg("CAS", grp, *flat))
            if l.get("allowed") is not None:
                body.append(_seg("AMT", "B6", _money(l["allowed"])))
            body.append(_seg("REF", "6R", f"{_clean(c.get('id'), 20)}-{i}"))
    return _envelope("HP", "005010X221A1", body, ctrl)


def parse_835(x: str) -> dict:
    segs, comp = segments(x)
    out = {"payer": "", "check": "", "date": "", "total": 0.0, "claims": [], "provider_adjustments": []}
    claim = line = None
    for s in segs:
        sid = s[0]
        g = lambda i: s[i] if len(s) > i else ""
        if sid == "BPR":
            out["total"] = float(g(2) or 0)
            d = g(16)
            out["date"] = f"{d[:4]}-{d[4:6]}-{d[6:8]}" if len(d) == 8 else ""
        elif sid == "TRN":
            out["check"] = g(2)
        elif sid == "N1" and g(1) == "PR":
            out["payer"] = g(2)
        elif sid == "CLP":
            claim = {"claim_id": g(1), "status": CLP_STATUS.get(g(2), g(2)), "charge": float(g(3) or 0), "paid": float(g(4) or 0),
                     "patient_resp": float(g(5) or 0), "payer_claim_id": g(7), "patient": {}, "adjustments": [], "lines": []}
            out["claims"].append(claim)
            line = None
        elif sid == "NM1" and g(1) == "QC" and claim is not None:
            claim["patient"] = {"last": g(3), "first": g(4), "member_id": g(9)}
        elif sid == "SVC" and claim is not None:
            code = _comp(g(1), comp)
            line = {"code": code[1] if len(code) > 1 else code[0], "charge": float(g(2) or 0), "paid": float(g(3) or 0), "allowed": None, "adjustments": [], "ref": ""}
            claim["lines"].append(line)
        elif sid == "CAS":
            target = line if line is not None else claim
            if target is None:
                continue
            grp = g(1)
            for i in range(2, len(s), 3):
                if s[i]:
                    target["adjustments"].append({"group": grp, "carc": s[i], "amount": float(s[i + 1] if i + 1 < len(s) and s[i + 1] else 0)})
        elif sid == "AMT" and g(1) == "B6" and line is not None:
            line["allowed"] = float(g(2) or 0)
        elif sid == "REF" and g(1) == "6R" and line is not None:
            line["ref"] = g(2)
        elif sid == "PLB":
            for i in range(3, len(s), 2):
                if s[i]:
                    out["provider_adjustments"].append({"reason": _comp(s[i], comp)[0], "amount": float(s[i + 1] if i + 1 < len(s) else 0)})
    return out
