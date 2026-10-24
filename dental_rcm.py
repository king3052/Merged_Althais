"""Dental revenue cycle logic: the rules that decide what gets billed, what insurance should pay, and how payments post.

Pure functions, no database or network (dental_api.py stores and sends; tests/test_dental_rcm.py covers each one):

  frequency_check(benefits, history, lines, on, age)  which planned procedures the plan won't pay for yet, and when it will
  estimate(benefits, lines, history, on, age)         insurance and patient portion per line, against the remaining max,
                                                      deductible and coinsurance
  find_missed(note, lines)                            procedures the note documents that the claim leaves out, or bills
                                                      at a lower level than documented (suggestions only)
  narrative(line, chart)                              the claim narrative payers want for crowns, SRP, buildups and more
  attachments_needed(lines)                           radiographs and perio charts the payer will ask for
  post_remittance(remit, claims)                      ERA (835) auto-posting: payments, write-offs, patient portions,
                                                      denials sent to the work queue, and exceptions for a person
  denial_action(carc)                                 what to do about a denial
  payment_plan(total, down, months, start)            an installment schedule
  membership_price(plan, lines)                       what an uninsured member pays

Money is handled in cents internally where it's summed, so a schedule always adds up to the total.
"""
import datetime as dt
import re
from collections import defaultdict

import dental_rules
import dental_x12

# Codes payers count against the same limit (an exam is an exam, whichever kind)
FREQ_GROUPS = [
    {"D0120", "D0150", "D0180"}, {"D1110", "D1120", "D4910"}, {"D0272", "D0274", "D0270", "D0273"}, {"D0210", "D0330"},
    {"D1206", "D1208"}, {"D4341", "D4342"}, {"D2740", "D2750", "D2790", "D2751", "D2752", "D2791", "D2792"}, {"D1351"},
]
# deductible normally doesn't apply to these
NO_DEDUCTIBLE = {"preventive", "diagnostic"}


def _date(s) -> dt.date:
    return s if isinstance(s, dt.date) else dt.date.fromisoformat(str(s)[:10])


def _add_months(d: dt.date, n: int) -> dt.date:
    y, m = divmod(d.month - 1 + n, 12)
    day = min(d.day, [31, 29 if (d.year + y) % 4 == 0 else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m])
    return dt.date(d.year + y, m + 1, day)


def _group_of(code):
    for g in FREQ_GROUPS:
        if code in g:
            return g
    return {code}


def _rule_for(benefits, code):
    for f in (benefits or {}).get("frequencies", []):
        if f.get("code") == code:
            return f
    return None


def frequency_check(benefits: dict, history: list, lines: list, on=None, age=None) -> list:
    """history: [{code, date, tooth, quad}] already done (any office, as far as we know); lines: planned [{code, tooth, quad}].
    Returns one entry per line: {code, ok, reason, next_eligible}. Planned lines count against each other in order."""
    on = _date(on or dt.date.today())
    done = [dict(h, date=_date(h["date"])) for h in history or []]
    out = []
    for l in lines:
        code = (l.get("code") or "").upper()
        rule = _rule_for(benefits, code)
        if not rule or not rule.get("limit"):
            out.append({"code": code, "ok": True, "reason": "", "next_eligible": None})
            continue
        if rule.get("age_max") and age is not None and age > rule["age_max"]:
            out.append({"code": code, "ok": False, "reason": f"Plan covers this through age {rule['age_max']}", "next_eligible": None})
            continue
        grp = _group_of(code)
        same = [h for h in done if h["code"] in grp]
        if rule.get("per_tooth"):
            same = [h for h in same if dental_rules.norm_tooth(h.get("tooth")) == dental_rules.norm_tooth(l.get("tooth"))]
        if rule.get("per_quadrant"):
            same = [h for h in same if (h.get("quad") or "").upper() == (l.get("quad") or "").upper()]
        if rule.get("per_months"):
            start = _add_months(on, -rule["per_months"])
            window = sorted(h["date"] for h in same if h["date"] > start)
            nxt = _add_months(window[0], rule["per_months"]) + dt.timedelta(days=1) if len(window) >= rule["limit"] else None
            per = f"{rule['limit']} per {rule['per_months'] // 12} years" if rule["per_months"] % 12 == 0 else f"{rule['limit']} per {rule['per_months']} months"
        else:
            window = sorted(h["date"] for h in same if h["date"].year == on.year)
            nxt = dt.date(on.year + 1, 1, 1) if len(window) >= rule["limit"] else None
            per = f"{rule['limit']} per calendar year"
        if len(window) >= rule["limit"]:
            out.append({"code": code, "ok": False, "reason": f"Frequency limit reached ({per}; last {window[-1].isoformat()})", "next_eligible": nxt.isoformat()})
        else:
            out.append({"code": code, "ok": True, "reason": "", "next_eligible": None})
            done.append({"code": code, "date": on, "tooth": l.get("tooth"), "quad": l.get("quad")})
    return out


def estimate(benefits: dict, lines: list, history: list = None, on=None, age=None) -> dict:
    """Insurance and patient portions per line. Uses the plan's coinsurance by category, then the deductible (not on
    preventive/diagnostic), then caps insurance at what's left of the annual max. Lines over a frequency limit are the
    patient's. Allowed amount = the office fee (in-network fee schedules can be loaded into fee later)."""
    b = benefits or {}
    if not b.get("active"):
        rows = [{"code": l.get("code"), "fee": float(l.get("fee") or 0), "insurance": 0.0, "patient": float(l.get("fee") or 0), "note": "No active coverage"} for l in lines]
        return {"lines": rows, "insurance": 0.0, "patient": round(sum(r["patient"] for r in rows), 2), "max_remaining_after": b.get("max_remaining")}
    freq = frequency_check(b, history or [], lines, on, age)
    max_left = b.get("max_remaining") if b.get("max_remaining") is not None else b.get("annual_max")
    max_left = float("inf") if max_left is None else float(max_left)
    ded_left = float(b.get("deductible_remaining") if b.get("deductible_remaining") is not None else b.get("deductible") or 0)
    waiting = {w.get("waiting") for w in b.get("waiting_periods", []) if w.get("months")}
    rows = []
    for l, f in zip(lines, freq):
        fee = round(float(l.get("fee") or 0) * int(l.get("units") or 1), 2)
        cat = dental_x12.benefit_category(l.get("code", ""))
        row = {"code": l.get("code"), "tooth": l.get("tooth", ""), "category": cat, "fee": fee, "insurance": 0.0, "patient": fee, "note": ""}
        pct = b.get("coinsurance", {}).get(cat)
        if not f["ok"]:
            row["note"] = f["reason"]
        elif cat in waiting:
            row["note"] = "Waiting period for " + cat
        elif pct is None or pct == 0:
            row["note"] = "Not covered (" + cat + ")"
        else:
            base = fee
            if cat not in NO_DEDUCTIBLE and ded_left > 0:
                take = min(ded_left, base)
                ded_left -= take
                base -= take
                row["note"] = f"Deductible ${take:.2f}"
            ins = round(base * pct / 100, 2)
            if ins > max_left:
                row["note"] = (row["note"] + "; " if row["note"] else "") + "Annual maximum reached"
                ins = round(max(0.0, max_left), 2)
            max_left -= ins
            row["insurance"], row["patient"] = ins, round(fee - ins, 2)
        rows.append(row)
    return {"lines": rows, "insurance": round(sum(r["insurance"] for r in rows), 2), "patient": round(sum(r["patient"] for r in rows), 2),
            "max_remaining_after": None if max_left == float("inf") else round(max_left, 2), "deductible_remaining_after": round(ded_left, 2)}


# ── missed and undercoded procedures ────────────────────────────────────────────────────────────────────────────
_WORD_NUM = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
             "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
             "nineteen": 19, "twenty": 20, "thirty": 30}
COMPOSITE_POST = {1: "D2391", 2: "D2392", 3: "D2393", 4: "D2394"}
COMPOSITE_ANT = {1: "D2330", 2: "D2331", 3: "D2332", 4: "D2335"}
AMALGAM = {1: "D2140", 2: "D2150", 3: "D2160", 4: "D2161"}
RCT = {"anterior": "D3310", "premolar": "D3320", "molar": "D3330"}


def _restorations(note: str) -> list:
    """#14 MO composite / composite on #3 O / 14-MOD amalgam -> [{tooth, surface, material}]"""
    out = []
    pat = re.compile(r"#\s?(\d{1,2}|[A-T])\s*[- ]?\s*([MODBLIF]{1,5})\b[^.;\n]{0,40}?\b(composite|amalgam|resin|filling|restoration)", re.I)
    pat2 = re.compile(r"\b(composite|amalgam|resin)\b[^.;\n]{0,25}?#\s?(\d{1,2}|[A-T])\s*[- ]?\s*([MODBLIF]{1,5})\b", re.I)
    for m in pat.finditer(note):
        out.append({"tooth": m.group(1).upper(), "surface": dental_rules.norm_surface(m.group(2)), "material": m.group(3).lower(), "evidence": m.group(0)})
    for m in pat2.finditer(note):
        out.append({"tooth": m.group(2).upper(), "surface": dental_rules.norm_surface(m.group(3)), "material": m.group(1).lower(), "evidence": m.group(0)})
    seen, uniq = set(), []
    for r in out:
        if (r["tooth"], r["surface"]) not in seen and not _not_today(note, r["evidence"]):
            seen.add((r["tooth"], r["surface"]))
            uniq.append(r)
    return uniq


_HISTORY = re.compile(r"\b(existing|intact|old|previous|prior|watch|monitor|planned?|recommend\w*|next visit|will need|treatment plan|tx plan|in the future)\b", re.I)
_TODAY = re.compile(r"\b(placed|completed|done|performed|restored today|today)\b", re.I)


def _not_today(note: str, evidence: str) -> bool:
    """The sentence describes existing or future work, not something done at this visit."""
    i = note.find(evidence)
    if i < 0:
        return bool(_HISTORY.search(evidence))
    start = max(note.rfind(".", 0, i), note.rfind("\n", 0, i)) + 1
    end = min([x for x in (note.find(".", i + len(evidence)), note.find("\n", i + len(evidence))) if x >= 0] or [len(note)])
    sent = note[start:end]
    return bool(_HISTORY.search(sent)) and not (_TODAY.search(sent) and not re.search(r"\b(recommend\w*|next visit|planned?|will need)\b", sent, re.I))


def _filling_code(tooth: str, surface: str, material: str) -> str:
    n = min(4, max(1, len(surface)))
    kind = (dental_rules.tooth_kind(tooth) or ("", ""))[1]
    if "amalgam" in material:
        return AMALGAM[n]
    return (COMPOSITE_ANT if kind == "anterior" else COMPOSITE_POST)[n]


def find_missed(note: str, lines: list, age: int = None) -> list:
    """Compares the note with the claim lines. Each finding: {kind: missed|undercoded, code, tooth, surface, quad,
    billed, evidence, message}. These are suggestions for the dentist to approve; nothing is added automatically."""
    note = note or ""
    low = note.lower()
    billed = [dict(l, code=(l.get("code") or "").upper(), tooth=dental_rules.norm_tooth(l.get("tooth")), surface=dental_rules.norm_surface(l.get("surface"))) for l in lines or []]
    codes = {l["code"] for l in billed}
    out = []

    def has(*cs):
        return any(c in codes for c in cs)

    def add(kind, code, msg, evidence, **kw):
        out.append({"kind": kind, "code": code, "tooth": kw.get("tooth", ""), "surface": kw.get("surface", ""), "quad": kw.get("quad", ""),
                    "billed": kw.get("billed", ""), "evidence": evidence.strip()[:160], "message": msg})

    # fillings, per tooth: missing, or fewer surfaces billed than documented
    for r in _restorations(note):
        want = _filling_code(r["tooth"], r["surface"], r["material"])
        on_tooth = [l for l in billed if l["tooth"] == r["tooth"] and 2000 <= int(l["code"][1:] or 0) < 2400] if r["tooth"] else []
        if not on_tooth:
            add("missed", want, f"Filling on #{r['tooth']} {r['surface']} is in the note but not on the claim", r["evidence"], tooth=r["tooth"], surface=r["surface"])
        else:
            l = on_tooth[0]
            surf_n = (dental_rules.SURF_COUNT.get(l["code"]) or (0, 0))[1]   # what the billed code pays for, whatever the surface field says
            billed_n = surf_n or len(l["surface"])
            if len(r["surface"]) > billed_n:
                add("undercoded", want, f"#{r['tooth']}: note documents {r['surface']} ({len(r['surface'])} surfaces), claim bills {l['code']} {l['surface'] or ''}".strip(),
                    r["evidence"], tooth=r["tooth"], surface=r["surface"], billed=l["code"])

    # exams
    if re.search(r"comprehensive (oral )?exam", low) and not has("D0150", "D0180") and has("D0120", "D0140"):
        add("undercoded", "D0150", "Comprehensive exam documented, a lower-level exam billed", "comprehensive exam", billed=",".join(c for c in ("D0120", "D0140") if c in codes))
    elif re.search(r"comprehensive (oral )?exam", low) and not has("D0150", "D0180", "D0120", "D0140"):
        add("missed", "D0150", "Comprehensive exam documented but not billed", "comprehensive exam")
    elif re.search(r"periodic (oral )?exam", low) and not has("D0120", "D0150", "D0180", "D0140"):
        add("missed", "D0120", "Periodic exam documented but not billed", "periodic exam")
    elif re.search(r"limited (oral )?exam|problem[- ]focused", low) and not has("D0140", "D0170", "D0120", "D0150"):
        add("missed", "D0140", "Limited exam documented but not billed", "limited exam")
    if re.search(r"perio(dontal)? (comprehensive )?(exam|evaluation)|comprehensive periodontal", low) and not has("D0180", "D0150"):
        if has("D0120"):
            add("undercoded", "D0180", "Comprehensive periodontal evaluation documented, periodic exam billed", "periodontal evaluation", billed="D0120")

    # images
    # the first bitewing mention that's about today, not "existing bitewings from March"
    bw = next((m for m in re.finditer(r"\b(four|4|two|2|three|3)\s*(bitewings?|bw'?s?|bwx)\b|\b(bitewings?|bwx)\b[^.]{0,20}?\b(four|4|two|2)\b", low)
               if not re.search(r"(existing|previous|prior|old|from)\s*$", low[max(0, m.start() - 12):m.start()]) and not re.match(r"\s*from\b", low[m.end():m.end() + 6])), None)
    if bw:
        n_word = bw.group(1) or bw.group(4)
        n = _WORD_NUM.get(n_word, None) or int(n_word) if n_word else 0
        want = {2: "D0272", 3: "D0273", 4: "D0274"}.get(n, "D0274")
        if not has("D0270", "D0272", "D0273", "D0274"):
            add("missed", want, f"{n or 'Bitewing'} bitewing images documented but not billed", bw.group(0))
        elif want == "D0274" and has("D0272") and not has("D0274"):
            add("undercoded", "D0274", "Four bitewings documented, two billed", bw.group(0), billed="D0272")
    if re.search(r"\b(pano(ramic)?|pan)\b[^.]{0,20}\b(taken|exposed|image)", low) and not has("D0330", "D0210"):
        add("missed", "D0330", "Panoramic image documented but not billed", "panoramic")
    if re.search(r"\b(fmx|full[- ]mouth (series|x-?rays|radiographs))\b", low) and not has("D0210"):
        add("missed", "D0210", "Full mouth series documented but not billed", "full mouth series")
    m = re.search(r"\b(pa|periapical)\b[^.]{0,15}#\s?(\d{1,2})", note, re.I)
    if m and not has("D0220", "D0230", "D0210") and not re.search(r"existing|previous|from \w+ (pa|periapical)", m.group(0), re.I):
        add("missed", "D0220", f"Periapical image of #{m.group(2)} documented but not billed", m.group(0), tooth=m.group(2))

    # preventive / perio
    if re.search(r"\b(adult )?(prophy(laxis)?|cleaning)\b[^.]{0,20}(completed|done|performed)|\b(completed|performed)\b[^.]{0,20}\b(prophy(laxis)?|cleaning)", low) and not has("D1110", "D1120", "D4910", "D4341", "D4342", "D4346"):
        add("missed", "D1120" if age is not None and age < 14 else "D1110", "Prophylaxis documented but not billed", "prophylaxis")
    if re.search(r"fluoride varnish", low) and not has("D1206", "D1208"):
        add("missed", "D1206", "Fluoride varnish documented but not billed", "fluoride varnish")
    for sent in re.split(r"(?<=[.\n])", low):
        if not re.search(r"scaling and root planing|\bsrp\b", sent) or _HISTORY.search(sent) and not _TODAY.search(sent):
            continue
        for qm in re.finditer(r"\b(upper right|upper left|lower right|lower left|ur|ul|lr|ll)\b", sent):
            quad = {"upper right": "UR", "upper left": "UL", "lower right": "LR", "lower left": "LL"}.get(qm.group(1), qm.group(1).upper())
            if not any(l["code"] in ("D4341", "D4342") and (l.get("quad") or "").upper() == quad for l in billed) and not any(f["code"] == "D4341" and f["quad"] == quad for f in out):
                add("missed", "D4341", f"Scaling and root planing, {quad} quadrant, documented but not billed for that quadrant", sent.strip(), quad=quad)
    if re.search(r"perio(dontal)? maintenance", low) and not has("D4910"):
        add("missed", "D4910", "Periodontal maintenance documented but not billed", "periodontal maintenance")

    # restorative / surgery extras
    for bm in re.finditer(r"(core )?build-?up[^.]{0,20}?#\s?(\d{1,2})|#\s?(\d{1,2})[^.]{0,30}?build-?up", note, re.I):
        t = bm.group(2) or bm.group(3)
        if not any(l["code"] in ("D2950", "D2952", "D2954") and l["tooth"] == t for l in billed):
            add("missed", "D2950", f"Core buildup on #{t} documented but not billed", bm.group(0), tooth=t)
    if re.search(r"build-?up placed", low) and not has("D2950", "D2952", "D2954") and not any(f["code"] == "D2950" for f in out):
        t = next((l["tooth"] for l in billed if 2700 <= int(l["code"][1:] or 0) <= 2799), "")
        add("missed", "D2950", "Core buildup documented but not billed" + (f" (#{t})" if t else ""), "buildup placed", tooth=t)
    for rm in re.finditer(r"(root canal|rct|endodontic therapy)[^.]{0,25}?#\s?(\d{1,2})|#\s?(\d{1,2})[^.]{0,25}?(root canal|rct)", note, re.I):
        t = rm.group(2) or rm.group(3)
        kind = (dental_rules.tooth_kind(t) or ("", "molar"))[1] or "molar"
        if not any(l["code"].startswith("D33") and l["tooth"] == t for l in billed):
            add("missed", RCT[kind], f"Root canal on #{t} documented but not billed", rm.group(0), tooth=t)
    for xm in re.finditer(r"(surgical|simple)? ?extraction[^.]{0,20}?#\s?(\d{1,2}|[A-T])|#\s?(\d{1,2}|[A-T])[^.]{0,20}?extract", note, re.I):
        t = (xm.group(2) or xm.group(3)).upper()
        surgical = bool(re.search(r"surgical|flap|sectioned|bone removal", xm.group(0) + low[max(0, xm.start() - 60): xm.end() + 60]))
        ext = [l for l in billed if l["code"] in ("D7140", "D7210", "D7220", "D7230", "D7240", "D7250") and l["tooth"] == t]
        if not ext:
            add("missed", "D7210" if surgical else "D7140", f"Extraction of #{t} documented but not billed", xm.group(0), tooth=t)
        elif surgical and ext[0]["code"] == "D7140":
            add("undercoded", "D7210", f"#{t}: surgical extraction documented (flap/sectioning/bone removal), simple extraction billed", xm.group(0), tooth=t, billed="D7140")
    if re.search(r"\bnitrous|n2o\b", low) and not has("D9230"):
        add("missed", "D9230", "Nitrous oxide documented but not billed", "nitrous")
    if re.search(r"occlusal guard|night guard", low) and re.search(r"deliver|seat|impression", low) and not has("D9944", "D9945", "D9946"):
        add("missed", "D9944", "Occlusal guard documented but not billed", "occlusal guard")
    sm = re.findall(r"sealants?[^.]{0,30}?((?:#\s?\d{1,2}[ ,and]*)+)", note, re.I)
    for group in sm:
        for t in re.findall(r"#\s?(\d{1,2})", group):
            if not any(l["code"] == "D1351" and l["tooth"] == t for l in billed):
                add("missed", "D1351", f"Sealant on #{t} documented but not billed", "sealant #" + t, tooth=t)
    return out


# ── narratives and attachments ──────────────────────────────────────────────────────────────────────────────────
def _quad_teeth(q):
    return {"UR": range(1, 9), "UL": range(9, 17), "LL": range(17, 25), "LR": range(25, 33)}.get((q or "").upper(), [])


def narrative(line: dict, chart: dict) -> str:
    """chart: {conditions: [{tooth, condition, surface}], perio: {tooth: {pd: [6], bop: [6]}}, findings: [str], dos, bone_loss}
    Returns '' when the code doesn't need one. Plain facts from the chart; the dentist edits before it goes out."""
    code = (line.get("code") or "").upper()
    tooth = dental_rules.norm_tooth(line.get("tooth"))
    conds = [c for c in chart.get("conditions", []) if dental_rules.norm_tooth(c.get("tooth")) == tooth] if tooth else []
    words = ", ".join(sorted({c["condition"].replace("_", " ") for c in conds})) or "the documented condition"
    n = int(code[1:]) if re.match(r"^D\d{4}$", code) else 0
    if 2700 <= n <= 2799 or code in ("D6058", "D6059", "D6065", "D6066"):
        why = words if conds else "a fractured cusp and a large failing restoration"
        return (f"Crown #{tooth}: {why}. Remaining tooth structure insufficient to retain a direct restoration; full coverage "
                f"needed to prevent fracture. Pre-operative radiograph attached.")
    if code in ("D2950", "D2952", "D2954"):
        return f"Buildup #{tooth}: after caries and failing restoration removal, insufficient structure remains to retain the crown. Pre-op radiograph attached."
    if code in ("D4341", "D4342"):
        teeth = list(_quad_teeth(line.get("quad")))
        perio = chart.get("perio") or {}
        deep = sorted({int(t) for t in teeth if any((d or 0) >= 4 for d in (perio.get(str(t)) or {}).get("pd", []))})
        maxd = max([d or 0 for t in teeth for d in (perio.get(str(t)) or {}).get("pd", [])] or [0])
        bop = sum(1 for t in teeth for b in (perio.get(str(t)) or {}).get("bop", []) if b)
        t_txt = ", ".join("#" + str(t) for t in deep) or "multiple teeth"
        return (f"SRP {line.get('quad', '').upper()} quadrant: probing depths of 4 mm or more on {t_txt} (deepest {maxd} mm), "
                f"{bop} bleeding sites, {chart.get('bone_loss') or 'radiographic bone loss'}. Periodontal chart and radiographs attached.")
    if code == "D4910":
        return "Periodontal maintenance following completed active periodontal therapy (scaling and root planing). Periodontal chart attached."
    if code in ("D7210", "D7220", "D7230", "D7240", "D7250"):
        return f"Surgical removal #{tooth}: required flap elevation and/or bone removal or sectioning; {words}. Pre-op radiograph attached."
    if 6000 <= n <= 6199:
        return f"Implant services #{tooth}: tooth missing ({words}); adjacent teeth healthy, implant chosen over a fixed bridge. Radiographs attached."
    if code in ("D3310", "D3320", "D3330"):
        return f"Endodontic therapy #{tooth}: {words if conds else 'irreversible pulpitis'}. Pre-op radiograph attached."
    if code == "D9110":
        return f"Palliative treatment of dental pain{(' #' + tooth) if tooth else ''}: emergency visit, minor procedure to relieve pain."
    return ""


def attachments_needed(lines: list) -> list:
    """[{code, tooth, kind, why}] what payers usually ask for before paying."""
    out = []
    for l in lines:
        code = (l.get("code") or "").upper()
        n = int(code[1:]) if re.match(r"^D\d{4}$", code) else 0
        if 2700 <= n <= 2799 or code in ("D2950", "D2952", "D2954") or 3300 <= n <= 3348 or 6000 <= n <= 6199 or code in ("D7210", "D7220", "D7230", "D7240"):
            out.append({"code": code, "tooth": l.get("tooth", ""), "kind": "radiograph", "why": "pre-op radiograph"})
        if code in ("D4341", "D4342", "D4910", "D4260", "D4261"):
            out.append({"code": code, "quad": l.get("quad", ""), "kind": "perio_chart", "why": "periodontal chart within the last 12 months"})
            if code in ("D4341", "D4342"):
                out.append({"code": code, "quad": l.get("quad", ""), "kind": "radiograph", "why": "radiographs showing bone loss"})
        if narrative(l, {}):
            out.append({"code": code, "tooth": l.get("tooth", ""), "kind": "narrative", "why": "narrative"})
    return out


# ── denials and ERA posting ─────────────────────────────────────────────────────────────────────────────────────
DENIALS = {
    "16": ("Missing or invalid information", "fix", "Correct the claim (tooth, surface, quadrant, subscriber info) and resubmit as a replacement (frequency 7)."),
    "252": ("Attachment required", "attach", "Attach the radiograph/perio chart and narrative, then resubmit."),
    "96": ("Not covered / frequency", "patient", "Check the frequency history. If the limit was really met, bill the patient per the financial agreement."),
    "119": ("Annual maximum reached", "patient", "Bill the patient; schedule remaining treatment for the new benefit year."),
    "197": ("Pre-authorization absent", "appeal", "Request a retro-authorization or appeal with the narrative and radiographs."),
    "29": ("Timely filing", "appeal", "Appeal with proof of the original submission date (999/277CA)."),
    "22": ("Coordination of benefits", "fix", "Update the primary/secondary order and resubmit to the right payer."),
    "27": ("Coverage terminated", "patient", "Re-verify eligibility; bill the patient or the new plan."),
    "50": ("Not medically necessary", "appeal", "Appeal with the narrative, perio chart and radiographs."),
    "18": ("Duplicate claim", "review", "Check whether the first claim paid; void the duplicate if so."),
    "4": ("Code inconsistent with modifier/surface", "fix", "Fix the code/surface combination and resubmit."),
    "11": ("Diagnosis inconsistent with procedure", "fix", "Add or correct the diagnosis code and resubmit."),
    "B7": ("Provider not certified", "review", "Check the dentist's enrollment with this payer."),
}


def denial_action(carc: str) -> dict:
    reason, action, how = DENIALS.get(str(carc).upper().replace("CO-", "").replace("PR-", ""), ("Denied", "review", "Review the EOB and decide: fix and resubmit, appeal, or bill the patient."))
    return {"carc": carc, "reason": reason, "action": action, "how": how}


def post_remittance(remit: dict, claims: dict) -> dict:
    """remit: parse_835 output. claims: {claim_id: {lines: [{code, fee, ...}], posted_checks: [...]}}.
    Returns {postings: [...], denials: [...], exceptions: [...]}. Nothing is written here; dental_api applies the result.
    Each posting: {claim_id, line_index, code, charge, paid, write_off, patient_resp, adjustments}"""
    postings, denials, exceptions = [], [], []
    for c in remit.get("claims", []):
        cid = c["claim_id"]
        mine = claims.get(cid)
        if not mine:
            exceptions.append({"claim_id": cid, "reason": "No claim with this number in Althais", "amount": c["paid"]})
            continue
        if remit.get("check") and remit["check"] in (mine.get("posted_checks") or []):
            exceptions.append({"claim_id": cid, "reason": f"Check {remit['check']} was already posted to this claim", "amount": c["paid"]})
            continue
        if c["status"] == "reversal":
            exceptions.append({"claim_id": cid, "reason": "Payer reversal: a person must review the takeback", "amount": c["paid"]})
            continue
        used = set()
        for svc in c["lines"]:
            idx = next((i for i, l in enumerate(mine["lines"]) if i not in used and (l.get("code") or "").upper() == svc["code"].upper()), None)
            if idx is None:
                exceptions.append({"claim_id": cid, "reason": f"Payer paid {svc['code']}, which isn't on our claim", "amount": svc["paid"]})
                continue
            used.add(idx)
            co = round(sum(a["amount"] for a in svc["adjustments"] if a["group"] in ("CO", "PI", "OA") and a["carc"] == "45"), 2)
            pr = round(sum(a["amount"] for a in svc["adjustments"] if a["group"] == "PR"), 2)
            other_co = [a for a in svc["adjustments"] if a["group"] in ("CO", "PI", "OA") and a["carc"] != "45"]
            postings.append({"claim_id": cid, "line_index": idx, "code": svc["code"], "charge": svc["charge"], "paid": svc["paid"],
                             "write_off": co, "patient_resp": pr, "adjustments": svc["adjustments"]})
            if svc["paid"] == 0 and other_co:
                a = other_co[0]
                denials.append({"claim_id": cid, "line_index": idx, "code": svc["code"], "amount": svc["charge"], **denial_action(a["carc"])})
            balance = round(svc["charge"] - svc["paid"] - co - pr - sum(a["amount"] for a in other_co), 2)
            if abs(balance) > 0.01:
                exceptions.append({"claim_id": cid, "reason": f"{svc['code']}: payment and adjustments leave {balance:+.2f} unexplained", "amount": balance})
        for i, l in enumerate(mine["lines"]):
            if i not in used and c["status"] != "denied":
                exceptions.append({"claim_id": cid, "reason": f"{l.get('code')} wasn't on the remittance", "amount": 0})
        if c["status"] == "denied" and not c["lines"]:
            denials.append({"claim_id": cid, "line_index": None, "code": "", "amount": c["charge"],
                            **denial_action(next((a["carc"] for a in c["adjustments"]), ""))})
    return {"postings": postings, "denials": denials, "exceptions": exceptions,
            "paid": round(sum(p["paid"] for p in postings), 2), "patient_resp": round(sum(p["patient_resp"] for p in postings), 2)}


# ── patient billing ─────────────────────────────────────────────────────────────────────────────────────────────
def payment_plan(total: float, down: float, months: int, start=None, day: int = None) -> list:
    """Zero-interest installments. The last payment absorbs rounding so the plan adds up to the cent."""
    if months < 1 or total <= 0 or down < 0 or down > total:
        raise ValueError("Check the total, down payment and number of months")
    start = _date(start or dt.date.today())
    rest = round((total - down) * 100)
    each = rest // months
    sched = []
    if down > 0:
        sched.append({"n": 0, "due": start.isoformat(), "amount": round(down, 2), "kind": "down payment"})
    for i in range(months):
        cents = each if i < months - 1 else rest - each * (months - 1)
        due = _add_months(start, i + 1)
        if day:
            due = due.replace(day=min(day, 28))
        sched.append({"n": i + 1, "due": due.isoformat(), "amount": cents / 100, "kind": "installment"})
    return sched


def membership_price(plan: dict, lines: list, used: dict = None) -> dict:
    """plan: {included: {code: per_year}, discount_pct}; used: {code: times this membership year}.
    Included services are $0 up to their yearly count; everything else gets the member discount."""
    used = dict(used or {})
    rows = []
    for l in lines:
        code = (l.get("code") or "").upper()
        fee = float(l.get("fee") or 0)
        allow = (plan.get("included") or {}).get(code, 0)
        if allow and used.get(code, 0) < allow:
            used[code] = used.get(code, 0) + 1
            rows.append({"code": code, "fee": fee, "price": 0.0, "note": "Included in membership"})
        else:
            pct = float(plan.get("discount_pct") or 0)
            rows.append({"code": code, "fee": fee, "price": round(fee * (100 - pct) / 100, 2), "note": f"Member discount {pct:g}%" if pct else ""})
    return {"lines": rows, "total": round(sum(r["price"] for r in rows), 2), "savings": round(sum(r["fee"] - r["price"] for r in rows), 2)}


# ── recall ──────────────────────────────────────────────────────────────────────────────────────────────────────
RECALL_CODES = {"D1110": 6, "D1120": 6, "D4910": 3}


def recall_due(history: list, on=None) -> dict:
    """Last hygiene visit and when the next is due: perio maintenance every 3 months, prophy every 6."""
    on = _date(on or dt.date.today())
    hyg = sorted((h for h in history if h.get("code") in RECALL_CODES), key=lambda h: h["date"])
    if not hyg:
        return {"last": None, "due": on.isoformat(), "overdue_days": 0, "interval_months": 6, "type": "New patient / no hygiene history"}
    last = hyg[-1]
    months = RECALL_CODES[last["code"]]
    due = _add_months(_date(last["date"]), months)
    return {"last": str(last["date"])[:10], "due": due.isoformat(), "overdue_days": max(0, (on - due).days), "interval_months": months,
            "type": "Perio maintenance" if last["code"] == "D4910" else "Prophy"}
