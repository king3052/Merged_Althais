"""Dental claim checks: the mistakes that get dental claims denied, checked before the claim goes out.

Dental claims differ from medical ones: procedures are CDT codes (D0120, D2392...), the claim goes on the ADA dental
claim form (837D electronically), most procedures need a tooth number and many need surfaces or a quadrant, and payers
deny on frequency limits and missing X-rays or narratives more than on diagnosis codes, which are usually optional.

This module is pure rules: no database, no network. It works on the canonical claim from emr_connect.py, plus optional
`lines` ([{code, tooth, surface, quad, units, charge}]) when the screen or file has tooth and surface detail.

Limits are the common plan limits (two cleanings a year, bitewings once a year and so on). Plans differ, so frequency
findings are "worth a look", never "fix first". CDT code descriptors belong to the ADA and are not reproduced here;
categories and requirements are described in our own words.

Code-number facts this relies on (tooth numbering, surface letters, which code ranges are per tooth, how many surfaces
a filling code covers) come from the structure of the code set and the ADA Universal numbering system.
"""
import datetime as dt
import re
from collections import defaultdict

CDT = re.compile(r"^D\d{4}$")

# ── teeth ────────────────────────────────────────────────────────────────────
PERM_MOLAR = {1, 2, 3, 14, 15, 16, 17, 18, 19, 30, 31, 32}
PERM_PREMOLAR = {4, 5, 12, 13, 20, 21, 28, 29}
PERM_ANTERIOR = set(range(6, 12)) | set(range(22, 28))
PRIM_ANTERIOR = set("CDEFGHMNOPQR")
PRIM_MOLAR = set("ABIJKLST")
QUADS = {"UR", "UL", "LR", "LL"}
ARCHES = {"UA", "LA"}
SURFACES = set("MODBLIF")


def norm_tooth(t) -> str:
    t = re.sub(r"[#\s]", "", str(t or "")).upper()
    return t


def tooth_kind(t: str):
    """('perm'|'prim'|'super', 'anterior'|'premolar'|'molar'|None) or None if it isn't a tooth number."""
    if not t:
        return None
    if t.isdigit():
        n = int(t)
        if 1 <= n <= 32:
            return ("perm", "anterior" if n in PERM_ANTERIOR else "premolar" if n in PERM_PREMOLAR else "molar")
        if 51 <= n <= 82:
            return ("super", None)
        return None
    if len(t) == 1 and t in PRIM_ANTERIOR | PRIM_MOLAR:
        return ("prim", "anterior" if t in PRIM_ANTERIOR else "molar")
    if len(t) == 2 and t[1] == "S" and t[0] in PRIM_ANTERIOR | PRIM_MOLAR:
        return ("super", None)
    return None


def norm_surface(s) -> str:
    s = re.sub(r"[^A-Za-z]", "", str(s or "")).upper()
    # keep order, drop repeats; B and F are both the cheek/lip side, payers accept either
    out = ""
    for ch in s:
        if ch not in out:
            out += ch
    return out


# ── code ranges ──────────────────────────────────────────────────────────────
def _n(code):
    return int(code[1:]) if CDT.match(code or "") else -1


def _in(code, *ranges):
    n = _n(code)
    return any(lo <= n <= hi for lo, hi in ranges)


CATEGORIES = [((100, 999), "diagnostic"), ((1000, 1999), "preventive"), ((2000, 2999), "restorative"), ((3000, 3999), "endodontics"),
              ((4000, 4999), "periodontics"), ((5000, 5899), "removable prosthodontics"), ((5900, 5999), "maxillofacial prosthetics"),
              ((6000, 6199), "implant services"), ((6200, 6999), "fixed prosthodontics"), ((7000, 7999), "oral surgery"),
              ((8000, 8999), "orthodontics"), ((9000, 9999), "adjunctive services")]


def category(code: str) -> str:
    n = _n(code)
    for (lo, hi), name in CATEGORIES:
        if lo <= n <= hi:
            return name
    return ""


EXAMS = {"D0120", "D0140", "D0145", "D0150", "D0160", "D0170", "D0180"}
ROUTINE_EXAMS = {"D0120", "D0150", "D0180"}
CLEANINGS = {"D1110", "D1120"}
PERIO_MAINT = {"D4910"}
SRP = {"D4341", "D4342"}
BITEWINGS = {"D0270", "D0272", "D0273", "D0274"}
FULL_SERIES = {"D0210", "D0330"}          # full-mouth series, panoramic image
FLUORIDE = {"D1206", "D1208"}
SEALANT = {"D1351"}


def needs_tooth(code):
    return _in(code, (2000, 2999), (3000, 3999), (6000, 6199), (6200, 6999), (7111, 7250), (1351, 1355), (4249, 4249), (4381, 4381))


def needs_quad(code):
    return _in(code, (4210, 4212), (4240, 4241), (4260, 4261), (4341, 4342), (7310, 7321))


def needs_arch(code):
    return _in(code, (5110, 5226))


# filling and inlay/onlay codes by number of surfaces: (min, max)
SURF_COUNT = {}
for codes, rng in (
    (("D2140", "D2330", "D2391", "D2410", "D2510", "D2610", "D2650"), (1, 1)),
    (("D2150", "D2331", "D2392", "D2420", "D2520", "D2620", "D2651", "D2542", "D2642", "D2662"), (2, 2)),
    (("D2160", "D2332", "D2393", "D2430", "D2530", "D2630", "D2652", "D2543", "D2643", "D2663"), (3, 3)),
    (("D2161", "D2335", "D2394", "D2544", "D2644", "D2664"), (4, 5)),
):
    for c in codes:
        SURF_COUNT[c] = rng

# the same kind of filling, by number of surfaces (1, 2, 3, 4+)
FAMILIES = [("D2140", "D2150", "D2160", "D2161"), ("D2330", "D2331", "D2332", "D2335"), ("D2391", "D2392", "D2393", "D2394"),
            ("D2410", "D2420", "D2430", None), ("D2510", "D2520", "D2530", None), ("D2610", "D2620", "D2630", None)]


def _matching_code(code, n):
    for fam in FAMILIES:
        if code in fam:
            return fam[min(n, 4) - 1] if n >= 1 else None
    return None


ANTERIOR_ONLY = {"D2330", "D2331", "D2332", "D2335", "D3310"}
PREMOLAR_ONLY = {"D3320"}
MOLAR_ONLY = {"D3330"}
POSTERIOR_ONLY = {"D2391", "D2392", "D2393", "D2394"}
PERMANENT_ONLY = {"D3310", "D3320", "D3330"}

EXTRACTION = ((7111, 7250),)
CROWN = ((2710, 2799),)
DIRECT_FILL = ((2140, 2161), (2330, 2394))

# what payers usually ask for alongside the code (our wording)
ATTACH = [
    (((2710, 2799), (2950, 2950)), "a pre-op X-ray and a short narrative (why a crown, e.g. fracture or large failing filling)"),
    (((6010, 6199), (6200, 6999)), "X-rays and a narrative, and often a pre-authorization"),
    (((4341, 4342),), "a periodontal chart with pocket depths (usually 4 mm or more) and recent X-rays"),
    (((7210, 7250),), "an X-ray and a narrative for the surgical extraction"),
    (((3310, 3348),), "a pre-op X-ray"),
    (((8010, 8999),), "a pre-authorization and orthodontic records"),
    (((2999, 2999), (9999, 9999), (6999, 6999), (7999, 7999), (4999, 4999)), "a narrative describing the procedure (unspecified codes are always reviewed)"),
]
PREAUTH = ((2710, 2799), (6010, 6199), (6200, 6999), (5110, 5226), (8010, 8999))


def is_dental(c: dict) -> bool:
    codes = list(c.get("cpt") or []) + [str(l.get("code") or "") for l in c.get("lines") or []]
    return any(CDT.match(str(x).upper()) for x in codes)


def _age(dob: str, on: str):
    try:
        b, d = dt.date.fromisoformat(dob), dt.date.fromisoformat(on)
        return d.year - b.year - ((d.month, d.day) < (b.month, b.day))
    except (TypeError, ValueError):
        return None


# ── one claim ────────────────────────────────────────────────────────────────
def check(c: dict) -> list:
    """Flags for one dental claim: [{severity, code, message, tooth?}]."""
    out = []

    def add(sev, code, msg, **extra):
        f = {"severity": sev, "code": code, "message": msg}
        f.update({k: v for k, v in extra.items() if v})
        out.append(f)

    lines = [dict(l) for l in (c.get("lines") or []) if str(l.get("code") or "").strip()]
    for l in lines:
        l["code"] = str(l["code"]).strip().upper()
        l["tooth"] = norm_tooth(l.get("tooth"))
        l["surface"] = norm_surface(l.get("surface"))
        l["quad"] = str(l.get("quad") or "").strip().upper()
    codes = [str(x).upper() for x in (c.get("cpt") or [])]
    for l in lines:
        if l["code"] not in codes:
            codes.append(l["code"])
    detail = bool(lines) and c.get("tooth_column", True)

    for code in codes:
        if code.startswith("D") and not CDT.match(code):
            add("error", "bad_cdt", f"{code} isn't a dental (CDT) code. CDT codes are D followed by four digits, e.g. D2392.")

    # per line: tooth, surfaces, quadrant
    missing_tooth_codes = []
    for l in lines:
        code, t, s, q = l["code"], l["tooth"], l["surface"], l["quad"]
        if not CDT.match(code):
            continue
        kind = tooth_kind(t) if t else None
        if t and not kind:
            add("error", "bad_tooth", f"Tooth {t} on {code} isn't a valid tooth number. Use 1–32 for permanent teeth or A–T for primary teeth.", tooth=t)
            continue
        if needs_tooth(code) and not t:
            missing_tooth_codes.append(code)
            if detail:
                add("error", "no_tooth", f"{code} needs a tooth number. Dental plans deny {category(code)} codes without one.")
        if needs_quad(code) and not (q in QUADS or (t and kind)):
            add("error", "no_quad", f"{code} needs a quadrant (UR, UL, LR or LL).")
        if needs_arch(code) and q not in ARCHES and q not in ("U", "L", "UPPER", "LOWER"):
            add("error", "no_arch", f"{code} needs the arch (upper or lower).")
        if code in SURF_COUNT:
            lo, hi = SURF_COUNT[code]
            if not s:
                add("error", "no_surface", f"{code} on tooth {t or '?'} needs its surfaces (M, O, D, B/F, L, I). It's a {lo}{'+' if hi > lo else ''}-surface code.", tooth=t)
            else:
                bad = [ch for ch in s if ch not in SURFACES]
                if bad:
                    add("error", "bad_surface", f"Surface {''.join(bad)} on tooth {t} ({code}) isn't a surface letter. Use M, O, D, B or F, L, I.", tooth=t)
                elif not (lo <= len(s) <= max(hi, lo)) and not (hi >= 4 and len(s) >= 4):
                    right = _matching_code(code, len(s))
                    add("error", "surface_count", f"{code} is a {lo}{'+' if hi > lo else ''}-surface code, but tooth {t} has {len(s)} surfaces ({s})." +
                        (f" Bill {right} instead." if right else " Bill the code that matches the number of surfaces."), tooth=t)
                if kind and kind[1] == "anterior" and "O" in s:
                    add("error", "bad_surface", f"Tooth {t} is a front tooth, which has no occlusal (O) surface. Use I (incisal) instead.", tooth=t)
                if kind and kind[1] in ("premolar", "molar") and "I" in s:
                    add("error", "bad_surface", f"Tooth {t} is a back tooth, which has no incisal (I) surface. Use O (occlusal) instead.", tooth=t)
        if kind:
            group = kind[1]
            if code in ANTERIOR_ONLY and group not in ("anterior",):
                add("warn", "tooth_type", f"{code} is for front teeth, but tooth {t} is a {group}. Check the code or the tooth number.", tooth=t)
            if code in POSTERIOR_ONLY and group == "anterior":
                add("warn", "tooth_type", f"{code} is for back teeth, but tooth {t} is a front tooth. Use the front-tooth composite code (D2330–D2335).", tooth=t)
            if code in PREMOLAR_ONLY and group != "premolar":
                add("error", "tooth_type", f"{code} is the premolar root canal code, but tooth {t} is a {group}.", tooth=t)
            if code in MOLAR_ONLY and group != "molar":
                add("error", "tooth_type", f"{code} is the molar root canal code, but tooth {t} is a {group}.", tooth=t)
            if code in PERMANENT_ONLY and kind[0] == "prim":
                add("error", "tooth_type", f"{code} is for permanent teeth; tooth {t} is a primary (baby) tooth. Primary root canal codes are different (D3230/D3240).", tooth=t)

    if not detail:
        missing_tooth_codes = [x for x in codes if CDT.match(x) and needs_tooth(x)]
    if missing_tooth_codes and not detail:
        add("info", "tooth_unseen", "Tooth numbers weren't found on this screen for " + ", ".join(sorted(set(missing_tooth_codes))) + ". Check them before sending; dental plans deny these without one.")

    # same tooth, same visit
    by_tooth = defaultdict(list)
    for l in lines:
        if l["tooth"]:
            by_tooth[l["tooth"]].append(l)
    for t, ls in by_tooth.items():
        cs = [l["code"] for l in ls]
        ext = [x for x in cs if _in(x, *EXTRACTION)]
        if ext and len(cs) > 1:
            others = [x for x in cs if x not in ext]
            if others:
                add("error", "same_tooth", f"Tooth {t} is extracted ({ext[0]}) on this claim but also has {', '.join(others)}. A tooth that's taken out can't be restored at the same visit.", tooth=t)
        if any(_in(x, *CROWN) for x in cs) and any(_in(x, *DIRECT_FILL) for x in cs):
            add("warn", "same_tooth", f"Tooth {t} has a crown and a filling at the same visit. Plans usually pay a core buildup (D2950) under a crown, not a filling.", tooth=t)
        seen = set()
        for l in ls:
            key = (l["code"], l["surface"])
            if key in seen:
                add("error", "dup_line", f"{l['code']} is billed twice on tooth {t}{(' (' + l['surface'] + ')') if l['surface'] else ''}.", tooth=t)
            seen.add(key)

    # same visit combinations
    cs = set(codes)
    if len(cs & ROUTINE_EXAMS) > 1:
        add("warn", "two_exams", f"{' and '.join(sorted(cs & ROUTINE_EXAMS))} are both exams. Plans pay one exam per visit.")
    if cs & CLEANINGS and cs & PERIO_MAINT:
        add("warn", "clean_and_maint", f"{', '.join(sorted(cs & CLEANINGS))} and D4910 at one visit. A patient gets either a regular cleaning or periodontal maintenance, not both.")
    if cs & CLEANINGS and cs & SRP:
        add("warn", "clean_and_srp", f"{', '.join(sorted(cs & CLEANINGS))} with scaling and root planing ({', '.join(sorted(cs & SRP))}) on the same day is usually denied or paid as one.")
    if cs & {"D0210"} and cs & BITEWINGS:
        add("warn", "fmx_and_bw", f"A full-mouth series (D0210) already includes bitewings, so {', '.join(sorted(cs & BITEWINGS))} at the same visit is usually denied.")
    age = _age(c.get("dob") or "", c.get("dos") or "")
    if age is not None:
        if cs & FLUORIDE and age >= 19:
            add("warn", "age_limit", f"Fluoride ({', '.join(sorted(cs & FLUORIDE))}) for a {age}-year-old. Many plans cover it only for children (often under 14 or 19).")
        if cs & SEALANT and age >= 16:
            add("warn", "age_limit", f"Sealants (D1351) for a {age}-year-old. Many plans cover them only for children and teens.")
        if cs & CLEANINGS and "D1120" in cs and age >= 14:
            add("warn", "age_limit", f"D1120 is the child cleaning code, but the patient is {age}. Adults use D1110.")
        if "D1110" in cs and age < 12:
            add("info", "age_limit", f"D1110 is the adult cleaning code and the patient is {age}. Many plans use D1120 for children under 14.")

    # documentation payers usually ask for
    asked = set()
    for code in sorted(cs):
        for ranges, what in ATTACH:
            if _in(code, *ranges) and what not in asked:
                asked.add(what)
                add("warn", "attach", f"{code}: send {what} with this claim, or it's likely to come back pending.")
                break
    major = sorted(x for x in cs if _in(x, *PREAUTH))
    if major:
        add("info", "preauth", f"{', '.join(major)}: many plans want a pre-authorization before this work. Check whether this plan needs one.")
    return out


# ── frequency across visits ─────────────────────────────────────────────────
FREQ = [
    ("exam", ROUTINE_EXAMS, 2, 365, "exams"),
    ("cleaning", CLEANINGS, 2, 365, "regular cleanings"),
    ("bitewings", BITEWINGS, 1, 365, "sets of bitewing X-rays"),
    ("fullseries", FULL_SERIES, 1, 3 * 365, "full-mouth or panoramic X-rays"),
    ("fluoride", FLUORIDE, 2, 365, "fluoride treatments"),
    ("maint", PERIO_MAINT, 4, 365, "periodontal maintenance visits"),
]


def history_flags(claims: list) -> dict:
    """Frequency limits across the claims in one batch (an upload). Returns {claim index: [flags]}."""
    by_pt = defaultdict(list)
    for i, c in enumerate(claims):
        if not is_dental(c) or not c.get("dos"):
            continue
        who = (c.get("mrn") or "").strip().lower() or (c.get("patient_name") or "").strip().lower()
        if who:
            by_pt[who].append(i)
    out = defaultdict(list)
    for who, idx in by_pt.items():
        visits = []
        for i in idx:
            try:
                d = dt.date.fromisoformat(claims[i]["dos"])
            except ValueError:
                continue
            codes = {str(x).upper() for x in claims[i].get("cpt") or []} | {str(l.get("code") or "").upper() for l in claims[i].get("lines") or []}
            visits.append((d, i, codes))
        visits.sort()
        for key, group, limit, days, label in FREQ:
            hits = [(d, i) for d, i, codes in visits if codes & group]
            for k, (d, i) in enumerate(hits):
                window = [h for h in hits[:k + 1] if (d - h[0]).days < days]
                if len(window) > limit:
                    first = window[0][0]
                    span = "12 months" if days == 365 else "3 years"
                    out[i].append({"severity": "warn", "code": "frequency",
                                   "message": f"This is the patient's {len(window)}{_th(len(window))} visit with {label} within {span} (first on {first.isoformat()}). "
                                              f"Most plans cover {limit} per {span}, so this one may be denied."})
        # crowns on the same tooth within 5 years, SRP in the same quadrant within 2 years
        seen_crown, seen_srp = {}, {}
        for d, i, _ in visits:
            for l in claims[i].get("lines") or []:
                code, t, q = str(l.get("code") or "").upper(), norm_tooth(l.get("tooth")), str(l.get("quad") or "").upper()
                if _in(code, *CROWN) and t:
                    if t in seen_crown and (d - seen_crown[t]).days < 5 * 365:
                        out[i].append({"severity": "warn", "code": "frequency", "message": f"Tooth {t} already had a crown on {seen_crown[t].isoformat()}. Most plans replace a crown only after 5 years.", "tooth": t})
                    seen_crown[t] = d
                if code in SRP and q:
                    if q in seen_srp and (d - seen_srp[q]).days < 2 * 365:
                        out[i].append({"severity": "warn", "code": "frequency", "message": f"Quadrant {q} already had scaling and root planing on {seen_srp[q].isoformat()}. Most plans allow it once every 2 years."})
                    seen_srp[q] = d
    return dict(out)


def _th(n):
    return "th" if 10 <= n % 100 <= 20 else {1: "st", 2: "nd", 3: "rd"}.get(n % 10, "th")


# ── a likelihood score, for the claim check in the app ──────────────────────
def score(c: dict, history: list = None) -> dict:
    """{score 0-100, flags, missing} for one dental claim, from these rules alone (no AI). Errors are what a plan
    denies outright, warnings are common reasons for a pend or a denial. `history`: the patient's other claims
    ({dos, cpt, lines}), for frequency limits."""
    found = check(c)
    if history:
        both = [dict(h, mrn="p") for h in history if h.get("dos")] + [dict(c, mrn="p")]
        found += history_flags(both).get(len(both) - 1, [])
    errors = [f["message"] for f in found if f["severity"] == "error"]
    warns = [f["message"] for f in found if f["severity"] == "warn"]
    codes = [x for x in (c.get("cpt") or []) if CDT.match(str(x).upper())] or [l.get("code") for l in c.get("lines") or [] if CDT.match(str(l.get("code") or "").upper())]
    if not codes:
        errors.append("At least one CDT procedure code (D followed by four digits).")
    s = 96 - 22 * len(errors) - 6 * len(warns)
    if errors:
        s = min(s, 60)
    return {"score": max(5, min(100, s)), "flags": warns, "missing": errors, "by": "dental rules"}
