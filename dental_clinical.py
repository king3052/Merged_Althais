"""Clinical helpers for the dental chart. Pure functions; tests/test_dental_clinical.py covers them.

  parse_perio_speech(text, start)   hands-free perio charting: "three two three, four three five, bleeding, next..."
                                    walks the standard probing path and fills six sites per tooth
  perio_summary(readings)           sites at 4/5/6+ mm, bleeding %, and a *suggested* periodontal stage and procedure,
                                    for the dentist to confirm (we don't diagnose)
  draft_note(transcript)            an ambient visit transcript -> a draft progress note and the procedures it mentions
  synthetic_radiograph(kind, teeth, conditions, seed)
                                    a stand-in X-ray (SVG) for development and demos, drawn from the chart. Never shown as
                                    a real image: it's labeled SYNTHETIC.

Perio site order, per tooth: readings[0:3] are the facial sites and [3:6] the lingual, each left to right as the tooth is
drawn on the chart (patient's right on the left of the screen), so the chart and the voice path agree.
"""
import hashlib
import random
import re

import dental_rules
import dental_rcm

NUM = {"zero": 0, "oh": 0, "one": 1, "won": 1, "two": 2, "to": 2, "too": 2, "three": 3, "four": 4, "for": 4, "five": 5, "six": 6,
       "seven": 7, "eight": 8, "ate": 8, "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
       "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18, "nineteen": 19, "twenty": 20, "thirty": 30}

# the probing path: upper facial 1->16, upper lingual 16->1, lower lingual 17->32, lower facial 32->17
PATH = ([(t, "F") for t in range(1, 17)] + [(t, "L") for t in range(16, 0, -1)] +
        [(t, "L") for t in range(17, 33)] + [(t, "F") for t in range(32, 16, -1)])


def _slots(tooth: int, side: str):
    """The three reading indexes for one side of a tooth, in probing order. Facial legs of the path run left to right on the
    chart and lingual legs right to left, on both arches."""
    return [0, 1, 2] if side == "F" else [5, 4, 3]


def _tokens(text: str) -> list:
    words = re.findall(r"[a-z]+|\d+", (text or "").lower().replace("-", " "))
    out, i = [], 0
    while i < len(words):
        w = words[i]
        if w == "twenty" and i + 1 < len(words) and words[i + 1] in NUM and NUM[words[i + 1]] < 10:
            out.append(str(20 + NUM[words[i + 1]])); i += 2; continue
        if w == "thirty" and i + 1 < len(words) and words[i + 1] in ("one", "two"):
            out.append(str(30 + NUM[words[i + 1]])); i += 2; continue
        out.append(str(NUM[w]) if w in NUM else w)
        i += 1
    return out


def empty_readings() -> dict:
    return {str(t): {"pd": [None] * 6, "rec": [None] * 6, "bop": [0] * 6, "sup": [0] * 6, "mob": None, "furc": None, "missing": False} for t in range(1, 33)}


def parse_perio_speech(text: str, readings: dict = None, start: int = 0, missing=()) -> dict:
    """Commands: numbers (three per tooth side), "tooth 14", "facial"/"buccal", "lingual"/"palatal", "bleeding"/"bleed"
    (marks the last three entered), "bleeding mesial", "pus"/"suppuration", "mobility 2", "furcation 1", "recession 2 1 2",
    "missing"/"skip" (the current tooth), "back"/"undo" (the last group). Missing teeth are skipped on the path.
    Returns {readings, position, log}."""
    r = readings or empty_readings()
    miss = {str(m) for m in missing} | {t for t, v in r.items() if v.get("missing")}
    pos = start
    log, last, history = [], None, []

    def advance():
        nonlocal pos
        pos += 1
        while pos < len(PATH) and str(PATH[pos][0]) in miss:
            pos += 1

    if pos < len(PATH) and str(PATH[pos][0]) in miss:
        pos -= 1
        advance()
    toks = _tokens(text)
    i = 0
    buf = []
    while i < len(toks):
        t = toks[i]
        if t.isdigit() and pos < len(PATH):
            buf.append(min(int(t), 15))
            if len(buf) == 3:
                tooth, side = PATH[pos]
                idx = _slots(tooth, side)
                prev = [r[str(tooth)]["pd"][k] for k in idx]
                for k, v in zip(idx, buf):
                    r[str(tooth)]["pd"][k] = v
                history.append((pos, tooth, idx, prev))
                last = (tooth, idx)
                log.append(f"#{tooth} {'facial' if side == 'F' else 'lingual'} {buf[0]} {buf[1]} {buf[2]}")
                buf = []
                advance()
        elif t in ("tooth", "number") and i + 1 < len(toks) and toks[i + 1].isdigit():
            n = int(toks[i + 1])
            side = PATH[pos][1] if pos < len(PATH) else "F"
            hit = next((k for k, (tt, s) in enumerate(PATH) if tt == n and s == side), None)
            if hit is not None:
                pos, buf = hit, []
                log.append(f"go to #{n}")
            i += 1
        elif t in ("facial", "buccal", "lingual", "palatal") and pos < len(PATH):
            side = "F" if t in ("facial", "buccal") else "L"
            hit = next((k for k, (tt, s) in enumerate(PATH) if tt == PATH[pos][0] and s == side), None)
            if hit is not None:
                pos, buf = hit, []
        elif t in ("bleeding", "bleed", "bop", "pus", "suppuration") and last:
            key = "bop" if t in ("bleeding", "bleed", "bop") else "sup"
            tooth, idx = last
            site = toks[i + 1] if i + 1 < len(toks) else ""
            which = {"distal": [idx[0]], "middle": [idx[1]], "mid": [idx[1]], "mesial": [idx[2]], "all": idx}.get(site)
            if which:
                i += 1
            for k in which or idx:
                r[str(tooth)][key][k] = 1
            log.append(f"#{tooth} {key}")
        elif t in ("mobility", "furcation") and i + 1 < len(toks) and toks[i + 1].isdigit():
            tooth = last[0] if last else (PATH[pos][0] if pos < len(PATH) else None)
            if tooth:
                r[str(tooth)]["mob" if t == "mobility" else "furc"] = int(toks[i + 1])
                log.append(f"#{tooth} {t} {toks[i + 1]}")
            i += 1
        elif t == "recession" and last:
            vals = []
            while i + 1 < len(toks) and toks[i + 1].isdigit() and len(vals) < 3:
                vals.append(int(toks[i + 1])); i += 1
            tooth, idx = last
            for k, v in zip(idx, vals):
                r[str(tooth)]["rec"][k] = v
            log.append(f"#{tooth} recession {' '.join(map(str, vals))}")
        elif t in ("missing", "skip") and pos < len(PATH):
            tooth = PATH[pos][0]
            r[str(tooth)]["missing"] = True
            miss.add(str(tooth))
            log.append(f"#{tooth} missing")
            buf = []
            advance()
        elif t in ("back", "undo") and history:
            p, tooth, idx, prev = history.pop()
            for k, v in zip(idx, prev):
                r[str(tooth)]["pd"][k] = v
            pos, buf, last = p, [], (history[-1][1], history[-1][2]) if history else None
            log.append(f"undo #{tooth}")
        i += 1
    return {"readings": r, "position": pos, "next": (PATH[pos] if pos < len(PATH) else None), "log": log}


QUAD_OF = lambda t: "UR" if t <= 8 else "UL" if t <= 16 else "LL" if t <= 24 else "LR"


def perio_summary(readings: dict) -> dict:
    sites = bop = 0
    over = {4: 0, 5: 0, 6: 0}
    quad = {q: {"teeth4": set(), "max": 0} for q in ("UR", "UL", "LL", "LR")}
    max_cal = 0
    for t, v in (readings or {}).items():
        if v.get("missing"):
            continue
        n = int(t)
        for k, d in enumerate(v.get("pd") or []):
            if d is None:
                continue
            sites += 1
            bop += 1 if (v.get("bop") or [0] * 6)[k] else 0
            for lim in over:
                if d >= lim:
                    over[lim] += 1
            if d >= 4:
                quad[QUAD_OF(n)]["teeth4"].add(n)
            quad[QUAD_OF(n)]["max"] = max(quad[QUAD_OF(n)]["max"], d)
            rec = (v.get("rec") or [None] * 6)[k] or 0
            max_cal = max(max_cal, max(0, d + rec - 3))   # attachment loss, roughly: depth plus recession beyond a healthy 3 mm sulcus
    bop_pct = round(100 * bop / sites) if sites else 0
    srp = []
    for q, x in quad.items():
        if len(x["teeth4"]) >= 4:
            srp.append({"quad": q, "code": "D4341", "teeth": sorted(x["teeth4"])})
        elif x["teeth4"]:
            srp.append({"quad": q, "code": "D4342", "teeth": sorted(x["teeth4"])})
    stage = "" if not sites else "Healthy / gingivitis" if max_cal <= 0 else "Stage I" if max_cal <= 2 else "Stage II" if max_cal <= 4 else "Stage III-IV"
    if srp:
        suggestion = "Scaling and root planing: " + ", ".join(f"{s['quad']} {s['code']}" for s in srp)
    elif bop_pct >= 30:
        suggestion = "Generalized gingival inflammation without pockets: consider D4346"
    else:
        suggestion = "Prophylaxis (D1110)"
    return {"sites": sites, "sites_4": over[4], "sites_5": over[5], "sites_6": over[6], "bop_pct": bop_pct, "max_cal": max_cal,
            "stage_suggested": stage, "srp": srp, "suggestion": suggestion, "note": "Suggested from the readings; the dentist confirms the diagnosis."}


# ── ambient notes ───────────────────────────────────────────────────────────────────────────────────────────────
def _sentences(text):
    return [s.strip() for s in re.split(r"(?<=[.?!])\s+", text or "") if s.strip()]


_SPOKEN = r"(twenty[\s-]?(?:one|two|three|four|five|six|seven|eight|nine)?|thirty[\s-]?(?:one|two)?|one|two|three|four|five|six|seven|eight|nine|ten|eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|\d{1,2})"


def _spoken_num(w: str):
    w = w.lower().replace("-", " ").split()
    n = sum(NUM.get(x, int(x) if x.isdigit() else 0) for x in w)
    return n if 1 <= n <= 32 else None


def _numify(text):
    """'fourteen MO composite' -> '#14 MO composite', 'decay on three occlusal' -> 'decay on #3 O', so spoken tooth numbers
    read like charted ones."""
    surf = {"mesial": "M", "occlusal": "O", "distal": "D", "buccal": "B", "lingual": "L", "incisal": "I", "facial": "F"}

    def tooth_then_surface(m):
        n = _spoken_num(m.group(1))
        return f"#{n} {m.group(2)}" if n else m.group(0)
    text = re.sub(r"\b(?:mesial and occlusal)\b", "MO", text, flags=re.I)
    text = re.sub(r"\b(?:mesial,? occlusal,? and distal|mesial occlusal distal)\b", "MOD", text, flags=re.I)
    text = re.sub(r"\b(" + "|".join(surf) + r")\b", lambda m: surf[m.group(1).lower()], text, flags=re.I)
    text = re.sub(r"(?<!#)\b" + _SPOKEN + r"\s+([MODBLIF]{1,5})\b", tooth_then_surface, text, flags=re.I)
    text = re.sub(r"\b(on|tooth|number)\s+" + _SPOKEN + r"\b", lambda m: m.group(1) + (" #" + str(_spoken_num(m.group(2))) if _spoken_num(m.group(2)) else " " + m.group(2)), text, flags=re.I)
    return text


def draft_note(transcript: str) -> dict:
    """Builds the note from what was said; the dentist reviews and signs. Speaker labels ("Doctor:", "Patient:") help."""
    t = _numify(transcript or "")
    sents = _sentences(t)
    patient = [re.sub(r"^patient:\s*", "", s, flags=re.I) for s in sents if re.match(r"^patient:", s, re.I)]
    staff = [re.sub(r"^(doctor|dentist|hygienist|assistant|dr\.?\s*\w+):\s*", "", s, flags=re.I) for s in sents if not re.match(r"^patient:", s, re.I)]
    find = [s for s in staff if re.search(r"\b(decay|caries|fracture|crack|abscess|bone loss|calculus|recession|watch|#\d)", s, re.I) and not re.search(r"\b(placed|completed|done|taken)\b", s, re.I)]
    done = [s for s in staff if re.search(r"\b(placed|completed|performed|done|taken|delivered|seated|extracted|cemented)\b", s, re.I)]
    anesth = [s for s in staff if re.search(r"lidocaine|articaine|carbocaine|mepivacaine|septocaine|carpule|anesthe", s, re.I)]
    plan = [s for s in staff if re.search(r"\b(next|recall|return|follow[- ]up|schedule|refer)\b", s, re.I)]
    note = ("S: " + (" ".join(patient) or "No complaints reported.") + "\n" +
            "O: " + (" ".join(find) or "Exam completed.") + "\n" +
            ("Anesthesia: " + " ".join(anesth) + "\n" if anesth else "") +
            "Treatment: " + (" ".join(s for s in done if s not in anesth) or "None today.") + "\n" +
            "P: " + (" ".join(plan) or "Continue routine care."))
    procedures = dental_rcm.find_missed(t, [])
    return {"note": note, "procedures": [{"code": p["code"], "tooth": p["tooth"], "surface": p["surface"], "quad": p["quad"], "why": p["message"]} for p in procedures],
            "status": "draft", "review": "Draft from the visit recording. Review every line before signing."}


# ── synthetic radiographs ───────────────────────────────────────────────────────────────────────────────────────
def synthetic_radiograph(kind: str, teeth: list, conditions: list = None, seed: str = "") -> str:
    """A grayscale SVG that looks like a dental X-ray: teeth with roots, enamel brighter than dentin, restorations and
    crowns bright white, caries as dark shadows, missing teeth as gaps. For development and demos only."""
    rnd = random.Random(int(hashlib.md5((seed + kind + ",".join(map(str, teeth))).encode()).hexdigest()[:8], 16))
    conds = {}
    for c in conditions or []:
        conds.setdefault(str(c.get("tooth")), []).append(c)
    teeth = [str(t) for t in teeth] or ["2", "3", "4", "5"]
    w, h = (900, 420) if kind == "pano" else (520, 380)
    out = [f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">',
           '<defs><radialGradient id="bg" cx="50%" cy="50%" r="70%"><stop offset="0" stop-color="#3a3a3a"/><stop offset="1" stop-color="#0c0c0c"/></radialGradient>',
           '<filter id="blur"><feGaussianBlur stdDeviation="1.4"/></filter><filter id="grain"><feTurbulence baseFrequency=".9" numOctaves="2" seed="' + str(rnd.randint(1, 99)) + '"/>'
           '<feColorMatrix values="0 0 0 0 1  0 0 0 0 1  0 0 0 0 1  0 0 0 .07 0"/></filter></defs>',
           f'<rect width="{w}" height="{h}" fill="url(#bg)"/>']

    def tooth(x, y, tw, th, upper, t):
        kind_t = (dental_rules.tooth_kind(t) or ("perm", "molar"))[1] or "molar"
        roots = 1 if kind_t == "anterior" else 2 if kind_t == "premolar" else 3
        c = conds.get(t, [])
        if any(k["condition"] in ("missing", "extracted") for k in c):
            return ""
        crown_h, root_h = th * 0.42, th * 0.58
        # upper teeth: roots at the top, crowns toward the bite in the middle; lower teeth the other way round
        cy = y + root_h if upper else y
        cerv = cy if upper else y + crown_h
        apex = y if upper else y + crown_h + root_h
        occ = cy + crown_h if upper else cy            # the biting edge
        g = ['<g filter="url(#blur)">']
        rw = tw / roots
        for r_i in range(roots):
            rx = x + r_i * rw + rw * 0.18
            g.append(f'<path d="M{rx:.0f},{cerv:.0f} Q{rx + rw * .32:.0f},{apex:.0f} {rx + rw * .64:.0f},{cerv:.0f} Z" fill="#9c9c9c" opacity=".8"/>')
            if any(k["condition"] == "rct" for k in c):
                g.append(f'<path d="M{rx + rw * .32:.0f},{cerv:.0f} L{rx + rw * .32:.0f},{apex + (6 if upper else -6):.0f}" stroke="#fff" stroke-width="4"/>')
        g.append(f'<rect x="{x:.0f}" y="{cy:.0f}" width="{tw:.0f}" height="{crown_h:.0f}" rx="{tw * .28:.0f}" fill="#d9d9d9"/>')
        pulp_y = cy + crown_h * (.08 if upper else .37)
        g.append(f'<rect x="{x + tw * .2:.0f}" y="{pulp_y:.0f}" width="{tw * .6:.0f}" height="{crown_h * .55:.0f}" rx="{tw * .2:.0f}" fill="#7a7a7a" opacity=".55"/>')
        for k in c:
            cond = k["condition"]
            if k.get("status") == "planned":
                continue
            if cond in ("crown", "implant_crown", "bridge"):
                g.append(f'<rect x="{x - 2:.0f}" y="{cy - 2:.0f}" width="{tw + 4:.0f}" height="{crown_h + 4:.0f}" rx="{tw * .3:.0f}" fill="#fafafa"/>')
            if cond == "implant":
                g.append(f'<rect x="{x + tw * .3:.0f}" y="{min(cerv, apex):.0f}" width="{tw * .4:.0f}" height="{root_h:.0f}" fill="#f4f4f4"/>')
            if cond in ("composite", "amalgam", "filling", "sealant"):
                surf = k.get("surface") or "O"
                sx = x + (0 if ("M" in surf) == (int(t) in range(1, 9) or int(t) in range(25, 33)) and len(surf) == 1 else tw * .2)
                fy = occ - crown_h * .45 if upper else occ
                g.append(f'<rect x="{sx:.0f}" y="{fy:.0f}" width="{tw * (.5 if len(surf) == 1 else .7):.0f}" height="{crown_h * .45:.0f}" rx="4" fill="{"#ffffff" if cond == "amalgam" else "#efefef"}"/>')
            if cond in ("caries", "decay"):
                surf = k.get("surface") or "M"
                side = x + (tw * .05 if "M" in surf else tw * .7)
                g.append(f'<ellipse cx="{side + tw * .12:.0f}" cy="{cy + crown_h * .5:.0f}" rx="{tw * .13:.0f}" ry="{crown_h * .16:.0f}" fill="#2a2a2a" opacity=".85"/>')
            if cond == "periapical":
                g.append(f'<circle cx="{x + tw / 2:.0f}" cy="{apex:.0f}" r="{tw * .22:.0f}" fill="#202020" opacity=".7"/>')
        g.append("</g>")
        g.append(f'<text x="{x + tw / 2:.0f}" y="{(y - 6) if upper else (y + th + 16):.0f}" fill="#bdbdbd" font-size="12" font-family="monospace" text-anchor="middle">{t}</text>')
        return "".join(g)

    upper = [t for t in teeth if t.isdigit() and int(t) <= 16]
    lower = [t for t in teeth if t.isdigit() and int(t) > 16]
    for row, is_up in ((upper, True), (lower, False)):
        if not row:
            continue
        n = len(row)
        tw = min(70, (w - 60) / max(n, 1) - 8)
        th = (h - 80) / (2 if upper and lower else 1.3)
        x0 = (w - n * (tw + 8)) / 2
        ordered = sorted(row, key=int) if is_up else sorted(row, key=int, reverse=True)
        y = 30 if is_up else (h / 2 + 8 if upper else 40)
        for i, t in enumerate(ordered):
            out.append(tooth(x0 + i * (tw + 8), y, tw, th, is_up, t))
    out.append(f'<rect width="{w}" height="{h}" filter="url(#grain)"/>')
    out.append(f'<text x="{w - 10}" y="{h - 10}" fill="#e0a000" font-size="13" font-family="sans-serif" text-anchor="end" font-weight="700">SYNTHETIC: NOT A REAL IMAGE</text>')
    out.append("</svg>")
    return "".join(out)
