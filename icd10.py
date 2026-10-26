"""ICD-10-CM 2026 (public domain, CMS/NCHS order file) for checking diagnosis codes on a claim.

data/icd10cm_2026.tsv.gz holds every code: code without the dot, 1 if billable / 0 if a category header, short description.
Used by Althea's claim builder to make sure every diagnosis is a real, billable code, to drop a less specific code
when a more specific one in the same category is on the claim, and to apply the common combination-code guidelines.
"""
import gzip
import os
import re

_CODES = {}   # "N1830" -> (billable, short description)
_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "icd10cm_2026.tsv.gz")
try:
    with gzip.open(_PATH, "rt", encoding="utf-8") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) == 3:
                _CODES[parts[0]] = (parts[1] == "1", parts[2])
except FileNotFoundError:
    pass

LOADED = bool(_CODES)


def _raw(code):
    return re.sub(r"[^A-Z0-9]", "", str(code or "").upper())


def dotted(raw):
    return raw if len(raw) <= 3 else raw[:3] + "." + raw[3:]


def lookup(code):
    """(exists, billable, description) for a code like 'N18.31' or 'n1831'."""
    hit = _CODES.get(_raw(code))
    return (True, hit[0], hit[1]) if hit else (False, False, "")


def billable_children(code, limit=8):
    """The billable codes under a non-billable header, e.g. N18.3 -> N18.30, N18.31, N18.32."""
    r = _raw(code)
    out = [(dotted(k), v[1]) for k, v in _CODES.items() if k.startswith(r) and k != r and v[0]]
    return sorted(out)[:limit]


def problems(codes):
    """Diagnosis codes that a payer would reject outright: not a real code, or a header that isn't billable."""
    out = []
    for c in codes:
        if str(c.get("type", "")).upper().startswith("CPT"):
            continue
        exists, billable, _ = lookup(c.get("code"))
        if not exists:
            out.append({"code": c.get("code"), "issue": "not a valid ICD-10-CM code", "options": []})
        elif not billable:
            out.append({"code": c.get("code"), "issue": "not billable, a more specific code is required", "options": billable_children(c.get("code"))})
    return out


def clean(codes):
    """Normalize diagnosis codes and apply the rules a reviewer checks first. Returns (codes, notes)."""
    notes, dx, other = [], [], []
    for c in codes:
        if str(c.get("type", "")).upper().startswith("CPT"):
            other.append(c)
            continue
        exists, billable, desc = lookup(c.get("code"))
        c = dict(c, code=dotted(_raw(c.get("code"))))
        if exists:
            c["description"] = desc
        dx.append(c)
    have = {d["code"] for d in dx}
    has_ckd = [d for d in dx if d["code"].startswith("N18.")]
    # hypertension with CKD is coded as hypertensive CKD (ICD-10-CM I.C.9.a.2)
    if has_ckd and "I10" in have:
        stage56 = any(d["code"] in ("N18.5", "N18.6") for d in has_ckd)
        new = "I12.0" if stage56 else "I12.9"
        dx = [dict(d, code=new, description=lookup(new)[2], reason="Hypertension with CKD is coded as hypertensive CKD (" + new + ")") if d["code"] == "I10" else d for d in dx]
        notes.append("I10 → " + new + ": hypertension with chronic kidney disease")
    # I12.0 / I13.1x are for stage 5 or ESRD only; I12.9 / I13.10 for stages 1-4 (and the reverse)
    if has_ckd:
        stage56 = any(d["code"] in ("N18.5", "N18.6") for d in has_ckd)
        fix = {"I12.0": "I12.9", "I13.11": "I13.10", "I13.2": "I13.0"} if not stage56 else {"I12.9": "I12.0", "I13.10": "I13.11", "I13.0": "I13.2"}
        for d in list(dx):
            if d["code"] in fix:
                new = fix[d["code"]]
                dx = [dict(x, code=new, description=lookup(new)[2]) if x is d else x for x in dx]
                notes.append(d["code"] + " → " + new + ": matches the documented CKD stage")
    # diabetes with CKD: the diabetic CKD code, with the CKD stage (I.C.4.a)
    if has_ckd:
        for dm in ("E11", "E10"):
            if any(d["code"] in (dm + ".9", dm + ".65") for d in dx) and not any(d["code"] == dm + ".22" for d in dx):
                dx = [dict(d, code=dm + ".22", description=lookup(dm + ".22")[2], reason="Diabetes with CKD is coded as diabetic CKD") if d["code"] == dm + ".9" else d for d in dx]
                if not any(d["code"] == dm + ".22" for d in dx):
                    dx.append({"code": dm + ".22", "type": "ICD-10", "description": lookup(dm + ".22")[2], "modifier": "", "units": 1, "reason": "Diabetes with CKD is coded as diabetic CKD"})
                notes.append(dm + ".22 added: diabetes with chronic kidney disease")
    # a "without complications" / unspecified code next to a more specific one in the same category is dropped
    keep = []
    for d in dx:
        cat, desc = d["code"][:3], (lookup(d["code"])[2] or d.get("description", "")).lower()
        vague = "without complication" in desc or "unspecified" in desc
        if vague and any(o is not d and o["code"][:3] == cat and "unspecified" not in (lookup(o["code"])[2] or "").lower() and "without complication" not in (lookup(o["code"])[2] or "").lower() for o in dx):
            notes.append(d["code"] + " removed: a more specific " + cat + " code is on the claim")
            continue
        if any(k["code"] == d["code"] for k in keep):
            continue
        keep.append(d)
    return other + keep, notes


_STOP = {"of", "and", "with", "w", "the", "or", "unspecified", "unsp", "type", "disease", "a", "in", "to", "other", "due"}

def resolve_headers(codes, note):
    """Replace a non-billable header (e.g. N18.3) with the billable code the note describes (e.g. 'stage 3a' -> N18.31);
    if the note doesn't say which, the header's unspecified code. Returns (codes, notes)."""
    text = re.sub(r"[^a-z0-9]+", " ", str(note or "").lower())
    words = set(text.split())
    out, notes = [], []
    for c in codes:
        exists, billable, _ = lookup(c.get("code"))
        if str(c.get("type", "")).upper().startswith("CPT") or not exists or billable:
            out.append(c)
            continue
        opts = billable_children(c.get("code"), limit=40)
        padded = " " + text + " "
        def score(desc):
            toks = [t for t in re.sub(r"[^a-z0-9]+", " ", desc.lower()).split() if t not in _STOP]
            pairs = sum(3 for x, y in zip(toks, toks[1:]) if " " + x + " " + y + " " in padded)   # "stage 3a" beats a stray "3"
            return pairs + sum(1 for t in toks if t in words and not t.isdigit())
        ranked = sorted(opts, key=lambda o: -score(o[1]))
        best = None
        if ranked and score(ranked[0][1]) > 0 and (len(ranked) == 1 or score(ranked[0][1]) > score(ranked[1][1])):
            best = ranked[0]
        else:
            uns = [o for o in opts if "unspecified" in o[1].lower()]
            best = uns[0] if uns else None
        if best:
            out.append(dict(c, code=best[0], description=best[1], reason=(c.get("reason") or "") + " (" + best[0] + ": billable code for what the note documents)"))
            notes.append(c.get("code") + " → " + best[0] + " (" + best[1] + ")")
        else:
            out.append(c)
    return out, notes


_NEG = re.compile(r"\b(?:no|denies|denied|without|negative for|not)\s+(?:\w+\s+){0,2}$")

def _present(word, text):
    """word appears in the note and not right after a negation ("no hypoglycemia")."""
    for m in re.finditer(r"\b" + re.escape(word) + r"\b", text):
        if not _NEG.search(text[max(0, m.start() - 40):m.start()]):
            return True
    return False

_EXTRA_STOP = _STOP | {"w", "without", "complications", "complication", "mellitus", "specified", "site", "nos", "elsewhere", "classified"}

def specify(codes, note):
    """Swap a vague diagnosis ("without complications" / "unspecified") for the sibling code whose extra detail the note
    states outright, e.g. E11.9 + "type 2 diabetes with hyperglycemia" -> E11.65. Returns (codes, notes)."""
    text = re.sub(r"[^a-z0-9.]+", " ", str(note or "").lower())
    out, notes = [], []
    for c in codes:
        exists, billable, desc = lookup(c.get("code"))
        d = desc.lower()
        if str(c.get("type", "")).upper().startswith("CPT") or not exists or not ("without complication" in d or "unspecified" in d):
            out.append(c)
            continue
        base = set(re.sub(r"[^a-z0-9]+", " ", d).split())
        cat = _raw(c.get("code"))[:3]
        best, best_n = None, 0
        for k, (bill, sdesc) in _CODES.items():
            if not bill or not k.startswith(cat) or k == _raw(c.get("code")):
                continue
            sd = sdesc.lower()
            if "unspecified" in sd or "without complication" in sd:
                continue
            if "not intractable" in d and "not intractable" not in sd:   # more specific, never more severe
                continue
            extra = [t for t in set(re.sub(r"[^a-z0-9]+", " ", sd).split()) - base if t not in _EXTRA_STOP and len(t) > 3]
            if extra and all(_present(t, text) for t in extra) and len(extra) > best_n:
                best, best_n = (dotted(k), sdesc), len(extra)
        if best:
            out.append(dict(c, code=best[0], description=best[1], reason="The note documents it: " + best[1]))
            notes.append(c.get("code") + " → " + best[0] + " (" + best[1] + ")")
        else:
            out.append(c)
    return out, notes


# a combination code claims a second condition; the note has to say so (otherwise it's an upcode a payer will deny)
_NEEDS = [
    (r"^E(08|09|10|11|13)\.2", r"ckd|chronic kidney|kidney disease|nephropath|renal|egfr|albuminuria|microalbumin|\bacr\b"),
    (r"^E(08|09|10|11|13)\.3", r"retinopath|macular|cataract|eye disease"),
    (r"^E(08|09|10|11|13)\.4", r"neuropath|gastroparesis|amyotroph|mononeuro"),
    (r"^E(08|09|10|11|13)\.5", r"angiopath|peripheral (arterial|vascular)|\bpad\b|\bpvd\b|gangrene|claudication"),
    (r"^E(08|09|10|11|13)\.62", r"ulcer|dermatitis|skin"),
    (r"^E(08|09|10|11|13)\.63", r"periodont|oral|dental"),
    (r"^E(08|09|10|11|13)\.64", r"hypoglyc"),
    (r"^E(08|09|10|11|13)\.65", r"hyperglyc|uncontrolled|poorly controlled|out of control|not at goal|above goal|a1c"),
    (r"^E(08|09|10|11|13)\.1", r"ketoacidosis|\bdka\b"),
    (r"^I1[23]", r"ckd|chronic kidney|kidney disease|renal|egfr|esrd|dialysis"),
    (r"^I1[13]\.0|^I13\.[12]", r"heart failure|\bchf\b|hfref|hfpef"),
    (r"^N18", r"ckd|chronic kidney|kidney disease|renal|egfr|esrd|dialysis"),
    (r"^T78\.0", r"anaphyla"),
]

# status codes the note documents outright: (code, what the note must say, what it must not)
_STATUS = [
    ("Z88.0", r"(penicillin|amoxicillin|pcn)\W+(allerg|causes|gives|reaction)|allerg\w*\W+(to\W+)?(penicillin|amoxicillin|pcn)", r"\b(no|denies|denied|without|not)\b[^.\n]{0,25}(allerg\w*\W+(to\W+)?)?(penicillin|amoxicillin|pcn)"),
    ("Z87.891", r"former smoker|quit smoking|ex-?smoker|smoked\b[^.\n]{0,60}\bquit|history of (smoking|tobacco|nicotine)|quit (in )?\d{4}", r"current(ly)? smok|still smok|smokes \d"),
]

def status(codes, note):
    """Add a status code (penicillin allergy, history of smoking) the note documents and the claim doesn't have yet."""
    text, have = str(note or "").lower(), {dotted(_raw(c.get("code"))) for c in codes}
    out, notes = list(codes), []
    for code, need, unless in _STATUS:
        if code in have or not re.search(need, text) or (unless and re.search(unless, text)):
            continue
        out.append({"code": code, "type": "ICD-10", "description": lookup(code)[2], "reason": "The note documents it: " + lookup(code)[2].lower()})
        notes.append(code + " added: " + lookup(code)[2])
    return out, notes


_INTRACTABLE = r"intractable|refractory|pharmacoresistant|treatment[- ]resistant|medically resistant"

def _not_intractable(code, text):
    """The 'not intractable' sibling of an intractable migraine or headache code (G43.919 -> G43.909, G44.211 -> G44.219)
    when the note doesn't say intractable, else None."""
    desc = lookup(code)[2].lower()
    if not code.startswith(("G43", "G44")) or "intractable" not in desc or "not intractable" in desc:
        return None
    if any(_present(m.group(0), text) for m in re.finditer(_INTRACTABLE, text)):
        return None
    r = _raw(code)
    for alt in ((r[:4] + "0" + r[5:]) if r.startswith("G43") and len(r) > 4 and r[4] == "1" else "", (r[:-1] + "9") if r.startswith("G44") and r.endswith("1") else ""):
        if alt and lookup(alt)[1] and "not intractable" in lookup(alt)[2].lower():
            return dotted(alt)
    return None


def _course(code, text):
    """G44.01x/G44.21x (episodic) and G44.02x/G44.22x (chronic): the sibling the note documents, when it says the other one."""
    r = _raw(code)
    if not re.match(r"G44[02][12]", r):
        return None
    epi, chron = _present("episodic", text), _present("chronic", text)
    want = "1" if epi and not chron else "2" if chron and not epi else r[4]
    alt = r[:4] + want + r[5:]
    return dotted(alt) if alt != r and lookup(alt)[1] else None


def supported(codes, note):
    """Drop a combination code the note doesn't support, falling back to the plain code for the main condition
    (E11.22 with no kidney disease documented -> E11.9). Returns (codes, notes)."""
    text = str(note or "").lower()
    out, notes = [], []
    for c in codes:
        code = dotted(_raw(c.get("code")))
        if str(c.get("type", "")).upper().startswith("CPT"):
            out.append(c)
            continue
        bad = next((need for pat, need in _NEEDS if re.search(pat, code) and not any(_present(m.group(0), text) for m in re.finditer(need, text))), None)
        course = _course(code, text)
        if course:   # episodic and chronic headaches are different codes: use the one the note says
            notes.append(code + " → " + course + ": the note says " + ("episodic" if course[5] == "1" else "chronic"))
            code = course; c = dict(c, code=course, description=lookup(course)[2], reason="The note documents " + lookup(course)[2].lower())
        calm = _not_intractable(code, text)
        if calm:   # "intractable" has to be documented; by default a headache or migraine is coded not intractable
            out.append(dict(c, code=calm, description=lookup(calm)[2], reason="The note documents " + lookup(calm)[2].lower()))
            notes.append(code + " → " + calm + ": the note doesn't say the headache is intractable")
            continue
        if not bad:
            out.append(c)
            continue
        cat = code[:3]
        plain = {"E08": "E08.9", "E09": "E09.9", "E10": "E10.9", "E11": "E11.9", "E13": "E13.9", "I12": "I10", "I13": "I10", "I11": "I10"}.get(cat)
        if plain and not any(dotted(_raw(o.get("code")))[:3] == cat and o is not c for o in codes):
            out.append(dict(c, code=plain, description=lookup(plain)[2], reason="The note documents " + lookup(plain)[2].lower()))
            notes.append(code + " → " + plain + ": the note doesn't document the complication " + code + " claims")
        else:
            notes.append(code + " removed: the note doesn't document it")
    return out, notes


_ABBR = {"ckd": "chronic kidney disease", "htn": "hypertension", "t2dm": "type 2 diabetes mellitus", "dm2": "type 2 diabetes mellitus",
         "t1dm": "type 1 diabetes mellitus", "uri": "acute upper respiratory infection", "copd": "chronic obstructive pulmonary disease",
         "gerd": "gastro-esophageal reflux disease", "hld": "hyperlipidemia", "chf": "heart failure", "afib": "atrial fibrillation",
         "uti": "urinary tract infection", "oa": "osteoarthritis", "mdd": "major depressive disorder", "gad": "generalized anxiety disorder",
         "strep": "streptococcal", "diabetes": "diabetes mellitus", "dm": "diabetes mellitus", "lumbar": "lower back", "lbp": "low back pain",
         "cp": "chest pain", "sob": "shortness of breath", "hld": "hyperlipidemia", "ha": "headache", "cad": "atherosclerotic heart disease", "bph": "benign prostatic hyperplasia", "osa": "obstructive sleep apnea"}
_SHORT = {"w": "with", "chr": "chronic", "kdny": "kidney", "dis": "disease", "stg": "stage", "hyp": "hypertensive", "hrt": "heart",
          "unsp": "unspecified", "diab": "diabetes", "mellitus": "mellitus", "ckd": "chronic kidney disease", "esrd": "end stage renal disease"}

def _toks(t, table):
    out = []
    for w in re.sub(r"[^a-z0-9]+", " ", t.lower()).split():
        out += re.sub(r"[^a-z0-9]+", " ", table.get(w, w)).split()
    return [w for w in out if w not in _STOP and w not in ("with", "without", "mellitus")]

_INDEX = None
# the usual code when a note names the condition plainly
_COMMON = {"I10", "K219", "E785", "E119", "E109", "J069", "J029", "J209", "N390", "M179", "F411", "F32A", "E039", "E669", "I4891", "I509", "J45909", "J449", "M5450", "R059", "G43909", "E559", "D649", "F419", "J329", "J0190", "H6690", "L0390", "B349", "R509", "R519", "K5900", "F17210", "G4700", "E782", "E780"}

_PLANNY = re.compile(r"\b\d+\s*(mg|mcg|ml|units?)\b|\b(start|add|begin|increase|decrease|continue|stop|refer|order|recheck|return|follow[- ]?up|supportive|care|fluids|rest|heat|ice|counsel|educat|advis|prn|bid|tid|qid|daily)\b", re.I)

def from_assessment(note):
    """Diagnosis codes read straight off the note, for when the AI coder is unavailable. Uses the Assessment /
    Impression / A/P / Diagnosis section when there is one, otherwise every sentence (strict matching). Each item maps
    to the billable code whose description covers its words (ties go to the usual code for a plainly named condition)."""
    text = str(note or "")
    m = re.search(r"(?:^|\n|\.\s)\s*(?:assessment(?:\s*(?:and|&|/)\s*plan)?|a\s*/\s*p|impression|diagnos[ie]s|dx)\s*[:\-]?(.*?)(?:\n\s*(?:plan|p)\s*[:\-]|\n\s*(?:orders?|follow[- ]?up|disposition)\s*[:\-]|$)", text, re.I | re.S)
    strict = not m
    body = m.group(1) if m else text
    items = [x.strip() for x in re.split(r"\n|;|,|\.\s|\s+-\s+|\s\d+[.)]\s|^\s*\d+[.)]\s", body) if len(x.strip()) > 2]
    global _INDEX
    if _INDEX is None:
        _INDEX = [(k, v[1], set(_toks(v[1], _SHORT))) for k, v in _CODES.items() if v[0]]
    low = text.lower()
    skip = tuple(ch for ch, words in (("P", "neonat|newborn|perinatal"), ("O", "pregnan|gestation|postpartum|trimester"), ("Q", "congenital"), ("V", "accident|collision"), ("W", "fall|struck"), ("X", "exposure"), ("Y", "place of"), ("S", "injur|fracture|sprain|strain|laceration|wound|contusion"), ("T", "poison|burn|adverse|injur")) if not re.search(words, low))
    index = [x for x in _INDEX if not x[0].startswith(skip)]
    out, seen = [], set()
    for it in items:
        if _PLANNY.search(it) and not re.search(r"\b(diabetes|hypertension|htn|ckd|copd|asthma|pharyngitis|bronchitis|infection)\b", it, re.I):
            continue   # a plan item (drug, dose, follow-up), not a diagnosis
        want = set(_toks(it, _ABBR)) - {"worsening", "uncontrolled", "stable", "controlled", "follow", "up", "agents", "two", "on", "new", "chronic", "likely", "probable", "possible", "mild", "moderate", "severe", "patient", "pt", "here", "for", "after", "days", "day", "ago", "no", "red", "flags"} | ({"chronic"} if "chronic" in it.lower() or "ckd" in it.lower() else set())
        if not want:
            continue
        # injuries: the initial-encounter code (7th character A), not subsequent (D) or sequela (S)
        best = max(((len(want & d), k in _COMMON, not (k[0] in "ST" and len(k) == 7 and k[-1] != "A"), -len(d - want), k, desc) for k, desc, d in index if want & d), default=None)
        best = best and (best[0], 0) + best[4:]
        need = len(want) if strict else max(1, -(-len(want) * 2 // 3))   # outside an Assessment section, every word must match
        if best and best[0] >= need and best[2] not in seen:
            seen.add(best[2])
            out.append({"code": dotted(best[2]), "type": "ICD-10", "description": best[3], "modifier": "", "units": 1, "reason": "From the note: " + it[:80]})
    return out
