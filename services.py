"""
services.py: the billable services a visit note documents as done in the clinic, found by rules (not the AI), so a test,
X-ray, injection or procedure the note shows is never left off the claim.

Each rule needs the service AND words saying it was done here ("in clinic", "performed", "given", "taken", "point-of-care",
a PROCEDURE section). A test sent out to a lab isn't the clinic's to bill. When the claim already has a code from the
same family (another strep test code, another X-ray of the same body part), the rule leaves it alone: the coder chose.

Laceration repairs are worked out from the documented length and site (CPT's simple repair ranges), and replace a simple
repair code of the wrong size.
"""
from __future__ import annotations

import math
import re
from typing import List, Optional, Tuple

DONE = r"(in[- ]clinic|in office|point[- ]of[- ]care|\bpoc\b|performed|given|administered|obtained|taken|done here|in the clinic|placed)"
SENT_OUT = r"(sent out|send out|sent to (the )?lab|reference lab|ordered for|will order|recommend(ed)? (an? )?(x-?ray|test)|outpatient)"


def _sentences(note: str) -> List[str]:
    text = (note or "").replace("\r", "")
    parts = re.split(r"(?<=[.;])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


RESULT = r"(:\s*)?\b(positive|negative|no fracture|no acute|normal|abnormal|\d+(\.\d+)?\s*%)"


def _done(sent: str, in_procedure: bool) -> bool:
    """Done here: a procedure section, words saying so, or a result written in the note (a result means it was run)."""
    s = sent.lower()
    return (in_procedure or re.search(DONE, s) is not None or re.search(RESULT, s) is not None) and not re.search(SENT_OUT, s)


# (code, what the sentence must mention, codes of the same family, extra condition)
TESTS = [
    ("87880", r"rapid strep|strep(tococcal)? (a )?antigen|rapid streptococcal", {"87880", "87430", "87651", "87081"}),
    ("87804", r"rapid (influenza|flu)|influenza (a/b |a and b )?(antigen|rapid)|flu (antigen|swab)", {"87804", "87400", "87502", "87501", "87631"}),
    ("87811", r"sars-?cov-?2 (rapid )?antigen|covid(-19)? (rapid )?antigen|rapid covid|covid(-19)? rapid test", {"87811", "87426", "87635", "87637"}),
    ("81025", r"urine (pregnancy|hcg)|pregnancy test,? urine", {"81025", "84703"}),
    ("83036", r"(hemoglobin |hb)?a1c", {"83036", "83037"}),
    ("82962", r"finger ?stick glucose|glucose by (glucometer|meter)|point-of-care glucose", {"82962", "82947"}),
    ("94640", r"nebuli[sz](er|ed) (treatment|albuterol|duoneb|ipratropium)|neb(ulizer)? treatment", {"94640", "94644"}),
]

XRAYS = [   # body part, [(min views, code)] ascending, family
    (r"ankle", [(1, "73600"), (3, "73610")], {"73600", "73610"}),
    (r"knee", [(1, "73560"), (3, "73562"), (4, "73564")], {"73560", "73562", "73564", "73565"}),
    (r"wrist", [(1, "73100"), (3, "73110")], {"73100", "73110"}),
    (r"hand", [(1, "73120"), (3, "73130")], {"73120", "73130"}),
    (r"foot", [(1, "73620"), (3, "73630")], {"73620", "73630"}),
    (r"finger|thumb", [(2, "73140")], {"73140"}),
    (r"toe", [(2, "73660")], {"73660"}),
    (r"elbow", [(1, "73070"), (3, "73080")], {"73070", "73080"}),
    (r"shoulder", [(1, "73020"), (2, "73030")], {"73020", "73030"}),
    (r"chest", [(1, "71045"), (2, "71046"), (3, "71047")], {"71045", "71046", "71047", "71048"}),
    (r"forearm", [(2, "73090")], {"73090"}),
    (r"tibia|fibula|lower leg", [(2, "73590")], {"73590"}),
]

DRUGS = [   # (drug, HCPCS, mg per unit) for drugs given in clinic
    (r"ondansetron", "J2405", 1.0),
    (r"ketorolac|toradol", "J1885", 15.0),
    (r"ceftriaxone|rocephin", "J0696", 250.0),
    (r"dexamethasone", "J1100", 1.0),
    (r"methylprednisolone|solu-?medrol|depo-?medrol", "J1030", 40.0),
    (r"triamcinolone|kenalog", "J3301", 10.0),
    (r"promethazine|phenergan", "J2550", 50.0),
]
INHALED = [(r"albuterol", "J7613", 1.0), (r"ipratropium", "J7644", 1.0)]
VACCINES = [(r"\btdap\b|boostrix|adacel", "90715"), (r"\btd\b vaccine|tenivac", "90714")]

PROCS = [
    ("69210", r"(remov\w+|extract\w+)[^.]{0,40}(impacted )?cerumen[^.]{0,80}(curette|forceps|instrument|suction|loop)|(curette|forceps|instrument)[^.]{0,60}cerumen", {"69209", "69210"}),
    ("69209", r"(irrigat|lavage)[^.]{0,40}cerumen|cerumen[^.]{0,40}(irrigat|lavage)", {"69209", "69210"}),
    ("10060", r"incision and drainage|\bi ?& ?d\b|i and d", {"10060", "10061", "10140", "10160", "10180"}),
    ("10120", r"(removal|removed|remove)[^.]{0,40}(foreign body|splinter)|(foreign body|splinter)[^.]{0,40}(removal|removed)", {"10120", "10121"}),
    ("17110", r"cryotherapy|liquid nitrogen|destruction[^.]{0,30}(wart|verruca)", {"17110", "17111", "17000", "17003"}),
]

FACE = r"\b(face|facial|cheek|forehead|chin|nose|lips?|eyelids?|ears?|eyebrows?|mucous membranes?)\b"
REPAIR_BODY = [(2.5, "12001"), (7.5, "12002"), (12.5, "12004"), (20.0, "12005"), (30.0, "12006"), (999, "12007")]
REPAIR_FACE = [(2.5, "12011"), (5.0, "12013"), (7.5, "12014"), (12.5, "12015"), (20.0, "12016"), (30.0, "12017"), (999, "12018")]
SIMPLE_REPAIRS = {c for _, c in REPAIR_BODY + REPAIR_FACE}


def _has(codes, family) -> bool:
    return any(str(c.get("code", "")).upper() in family for c in codes)


def _views(sent: str) -> Optional[int]:
    m = re.search(r"\b(\d|one|two|three|four)\s*(-\s*)?views?\b", sent)
    if not m:
        return None
    w = m.group(1)
    return int(w) if w.isdigit() else {"one": 1, "two": 2, "three": 3, "four": 4}[w]


def _dose_mg(sent: str, drug: str) -> Optional[float]:
    m = re.search("(?:" + drug + ")" + r"[^.;]{0,30}?(\d+(?:\.\d+)?)\s*(mg|milligrams?|g\b|grams?)", sent)
    if not m:
        return None
    v = float(m.group(1))
    return v * 1000 if m.group(2).startswith("g") else v


def repair_code(note: str) -> Optional[Tuple[str, str]]:
    """(code, why) for a documented simple repair: length in cm and site from the note."""
    t = (note or "").lower()
    if not re.search(r"simple repair|sutur|repaired|staples|stapled|dermabond|tissue adhesive", t) or re.search(r"not (sutured|repaired|closed)", t) and not re.search(r"simple repair", t):
        return None
    if re.search(r"intermediate|layered|complex repair", t):
        return None
    m = re.search(r"(\d+(?:\.\d+)?)\s*(cm|centimet\w+|mm)\b[^.]{0,60}lacerat|lacerat\w*[^.]{0,40}?(\d+(?:\.\d+)?)\s*(cm|mm)\b", t)
    if not m:
        return None
    num, unit = (m.group(1), m.group(2)) if m.group(1) else (m.group(3), m.group(4))
    cm = float(num) / (10 if unit == "mm" else 1)
    seg = t[max(0, m.start() - 20): m.end() + 80]
    table = REPAIR_FACE if re.search(FACE, seg) else REPAIR_BODY
    code = next(c for top, c in table if cm <= top + 1e-9)
    return code, f"simple repair, {cm:g} cm, {'face' if table is REPAIR_FACE else 'trunk/extremities/scalp/neck'}"


def add_documented(codes: List[dict], note: str) -> Tuple[List[dict], List[str]]:
    """The claim's codes plus any service the note documents as done in clinic and the claim is missing. Returns (codes, notes)."""
    out = [dict(c) for c in codes]
    notes: List[str] = []
    sents = _sentences(note)
    in_proc = False
    seen_drugs = set()

    def add(code, why, units=1):
        if any(str(c.get("code", "")).upper() == code for c in out):
            return
        out.append({"code": code, "type": "CPT", "description": why, "modifier": "", "units": units, "reason": "Documented in the note: " + why})
        notes.append(f"Added {code}: the note documents {why}")

    for raw in sents:
        s = raw.lower()
        if re.match(r"(procedure|procedures)\s*:", s):
            in_proc = True
        elif re.match(r"(subjective|objective|assessment|plan|hpi|exam)\s*:", s):
            in_proc = False
        done = _done(s, in_proc)
        if not done:
            continue
        for code, pat, fam in TESTS:
            if re.search(pat, s) and not _has(out, fam):
                if code == "83036" and not re.search(r"point[- ]of[- ]care|in[- ]clinic|\bpoc\b", s):
                    continue
                add(code, re.search(pat, s).group(0))
        if re.search(r"dipstick|urinalysis|\bua\b", s) and not _has(out, {"81000", "81001", "81002", "81003"}):
            micro = re.search(r"micro", s) and not re.search(r"(no|without) micro", s)
            auto = re.search(r"(?<!non-)(?<!non )automated", s)
            add("81001" if micro and auto else "81000" if micro else "81003" if auto else "81002", "urinalysis by dipstick" + (", automated" if auto else ", non-automated") + (", with microscopy" if micro else ", without microscopy"))
        if re.search(r"x-?ray|radiograph", s):
            n = _views(s)
            for part, steps, fam in XRAYS:
                if re.search(r"\b(" + part + r")\b", s) and not _has(out, fam):
                    if n is None:
                        break
                    code = [c for v, c in steps if n >= v]
                    if code:
                        add(code[-1], f"{part.split('|')[0]} X-ray, {n} view{'s' if n != 1 else ''}")
                    break
        if re.search(r"\b(12|twelve)[- ]lead\b|\b(ecg|ekg)\b", s) and re.search(r"interpret|performed|obtained", s) and not _has(out, {"93000", "93005", "93010"}):
            add("93000", "12-lead ECG with interpretation")
        if re.search(r"neb", s):
            for drug, code, per in INHALED:
                mg = _dose_mg(s, drug)
                if re.search(drug, s) and not _has(out, {code}):
                    add(code, f"{drug} inhalation solution{f', {mg:g} mg' if mg else ''}", max(1, math.ceil((mg or per) / per)))
                    if not _has(out, {"94640", "94644"}):
                        add("94640", "nebulizer treatment")
        if re.search(r"\b(im|intramuscular|subcutaneous|subq|sq|injection|injected|iv push)\b", s):
            for drug, code, per in DRUGS:
                if re.search(drug, s) and code not in seen_drugs and not _has(out, {code}):
                    mg = _dose_mg(s, drug)
                    if mg:
                        seen_drugs.add(code)
                        add(code, f"{drug.split('|')[0]} {mg:g} mg given", max(1, math.ceil(mg / per)))
                        if re.search(r"\biv push\b", s):
                            if not _has(out, {"96374", "96375"}):
                                add("96374", "IV push of a drug")
                        elif not _has(out, {"96372"}):
                            add("96372", "injection of a drug (IM/subcutaneous)")
        for pat, code in VACCINES:
            if re.search(pat, s) and not _has(out, {code}):
                add(code, re.search(pat, s).group(0) + " vaccine")
                if not _has(out, {"90471", "90460"}):
                    add("90471", "vaccine administration")
        if in_proc or re.search(r"procedure", s):
            for code, pat, fam in PROCS:
                if re.search(pat, s) and not _has(out, fam):
                    add(code, re.search(pat, s).group(0))
    rep = repair_code(note)
    if rep:
        code, why = rep
        have = [c for c in out if str(c.get("code", "")).upper() in SIMPLE_REPAIRS]
        if not have:
            add(code, why)
        elif all(str(c.get("code")).upper() != code for c in have):
            old = ", ".join(str(c.get("code")) for c in have)
            out = [c for c in out if str(c.get("code", "")).upper() not in SIMPLE_REPAIRS]
            out.append({"code": code, "type": "CPT", "description": why, "modifier": have[0].get("modifier", ""), "units": 1, "reason": "Documented in the note: " + why})
            notes.append(f"{old} → {code}: {why}")
    return out, notes


# minor procedures (0- or 10-day global): the visit that decides to do them is part of the procedure
MINOR = SIMPLE_REPAIRS | {"10060", "10061", "10120", "10121", "17110", "17111", "69209", "69210", "11055", "11056", "11057", "11720", "11721", "11730", "11740", "20550", "20551", "20600", "20604", "20605", "20606", "20610", "20611", "29125", "29126", "29130", "29515", "29540"}


WITH_25 = {"96372", "96374", "96375", "94640", "94644"}   # same-day treatments that need 25 on the visit


def _em(code: str) -> bool:
    return bool(re.fullmatch(r"99(20[2-5]|21[1-5]|28[1-5])", code))


def visit_with_minor_procedure(codes: List[dict]) -> Tuple[List[dict], List[str]]:
    """CMS global surgery rule for a same-day E/M and a minor procedure: one problem (the one treated by the procedure) means no
    E/M; a separate, significant problem also evaluated means the E/M stays, with modifier 25. Problems are counted from the
    claim's diagnoses: Z codes (vaccines, screening) don't count, and skin infections L02/L03 count as one."""
    cpts = [str(c.get("code", "")).upper() for c in codes if str(c.get("type", "")).upper().startswith(("CPT", "HCPCS"))]
    if not any(_em(c) for c in cpts):
        return codes, []
    if not any(c in MINOR for c in cpts):
        given = next((c for c in cpts if c in WITH_25), None)
        if not given:
            return codes, []
        out, notes = [], []
        for c in codes:     # an injection or nebulizer the same day: payers fold the visit in unless it carries 25
            mods = str(c.get("modifier") or "").replace(",", " ").split()
            if _em(str(c.get("code", "")).upper()) and "25" not in mods:
                c = dict(c, modifier=" ".join(mods + ["25"]).strip())
                notes.append(f"Modifier 25 added to {c['code']}: the visit was evaluated and managed separately from {given}.")
            out.append(c)
        return out, notes
    cats = set()
    for c in codes:
        if str(c.get("type", "")).upper().startswith(("CPT", "HCPCS")):
            continue
        k = str(c.get("code", "")).upper().replace(".", "")[:3]
        if not k or k.startswith("Z") or k[0] in "VWXY":
            continue
        cats.add("L0x" if k in ("L02", "L03") else k[0] if k[0] in "ST" else k)
    proc = next(c for c in cpts if c in MINOR)
    if len(cats) <= 1:
        em = [c for c in cpts if _em(c)]
        return ([c for c in codes if str(c.get("code", "")).upper() not in em],
                [f"{', '.join(em)} removed: the visit was only for the {proc} procedure, which includes the decision to do it (global surgery rule). Add it back with modifier 25 only if a separate problem was evaluated."])
    out, notes = [], []
    for c in codes:
        code = str(c.get("code", "")).upper()
        mods = str(c.get("modifier") or "").replace(",", " ").split()
        if _em(code) and "25" not in mods:
            c = dict(c, modifier=" ".join(mods + ["25"]).strip())
            notes.append(f"Modifier 25 added to {code}: a separate problem was evaluated the same day as {proc}.")
        out.append(c)
    return out, notes
