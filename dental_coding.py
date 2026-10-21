"""Coding a dental visit note: CDT procedure codes with tooth, surfaces and quadrant, checked against the dental rules.

Used by the browser extension's Code note (extension_api.py) when the note is a dental one. Same AI client and cache as
the medical coder in main.py, with a dental prompt. Suggestions only: a person reviews every code before it's used.
"""
import json
import re

from fastapi import HTTPException

import dental_rules

_DENTAL_WORDS = re.compile(
    r"\b(tooth|teeth|#\s?\d{1,2}\b|occlusal|mesial|distal|buccal|lingual|incisal|caries|carious|prophy|prophylaxis|bitewing|"
    r"periapical|periodontal|perio|scaling|root planing|gingiv\w*|crown|composite|amalgam|extraction|endodontic|pulp\w*|"
    r"denture|implant|sealant|fluoride varnish|hygienist|dds|dmd|cdt)\b", re.I)


def is_dental_note(note: str, specialty=None) -> bool:
    """A dental note: the page said so, or the note reads like one (several dental terms)."""
    if specialty and re.search(r"dent|ortho|perio|endo|oral", str(specialty), re.I):
        return True
    return len(set(m.group(0).lower() for m in _DENTAL_WORDS.finditer(note or ""))) >= 3


PROMPT = """You are an expert dental coder (CDT procedure codes, ADA claim form). Code this dental visit note.

Return a JSON object shaped exactly like this:
{{"codes": [
  {{"code": "D2392", "type": "CDT", "description": "short plain-English description in your own words",
    "tooth": "14", "surface": "MO", "quad": "", "confidence": 90,
    "justification": "one sentence quoting what in the note supports this code",
    "documentation_gap": "what the note is missing for this code, or empty string"}}
]}}

Rules:
1. One entry per procedure per tooth. Two fillings on two teeth are two entries.
2. Tooth numbers use the ADA Universal system: 1-32 permanent, A-T primary. Surfaces use M, O, D, B (or F), L, I.
   Front teeth have I, not O; back teeth have O, not I.
3. Filling codes depend on the number of surfaces (e.g. a 2-surface posterior composite is D2392). Pick the code that matches the surfaces.
4. Scaling and root planing and other per-quadrant procedures need "quad": UR, UL, LR or LL. Dentures need the arch: UA or LA.
5. Exams, cleanings and X-rays have no tooth. Bitewings, full-mouth series and panoramic images are separate codes.
6. Only code what was done at this visit, not what was planned for later. If treatment is only planned, leave it out.
7. If the note doesn't say which tooth, surfaces or quadrant, keep the code, leave the field empty, lower the confidence and say what's missing in documentation_gap.
8. Diagnosis codes are usually not needed on dental claims. Add an ICD-10 entry (type "ICD-10", no tooth) only when the note documents a clear diagnosis.

Respond with the JSON object only.

Dental visit note:
{note}"""

SCHEMA = {
    "type": "object",
    "properties": {"codes": {"type": "array", "items": {"type": "object", "properties": {
        "code": {"type": "string"}, "type": {"type": "string", "enum": ["CDT", "ICD-10"]}, "description": {"type": "string"},
        "tooth": {"type": "string"}, "surface": {"type": "string"}, "quad": {"type": "string"}, "confidence": {"type": "integer"},
        "justification": {"type": "string"}, "documentation_gap": {"type": "string"}},
        "required": ["code", "type", "description", "tooth", "surface", "quad", "confidence", "justification", "documentation_gap"],
        "additionalProperties": False}}},
    "required": ["codes"], "additionalProperties": False,
}


def _ask_ai(note: str) -> list:
    import main
    msgs = [{"role": "user", "content": PROMPT.format(note=note)}]
    try:
        r = main.client.chat.completions.create(model=main.GROQ_MODEL, messages=msgs, temperature=0.2,
                                                response_format={"type": "json_schema", "json_schema": {"name": "dental_codes", "strict": True, "schema": SCHEMA}})
    except Exception as e:
        if main._is_rate_limited(e):
            raise HTTPException(status_code=429, detail="Althais's coder is busy. Try again in a minute.")
        r = main.client.chat.completions.create(model=main.GROQ_MODEL, messages=msgs, temperature=0.2)
    text = r.choices[0].message.content.strip()
    m = re.search(r"\{.*\}", text, re.S)
    data = json.loads(m.group(0) if m else text)
    return data.get("codes", []) if isinstance(data, dict) else (data if isinstance(data, list) else [])


def clean(codes: list) -> list:
    """Normalize the AI's answer, keep only real-looking codes, and add what the dental rules would flag to each code."""
    out = []
    for x in codes[:30]:
        if not isinstance(x, dict):
            continue
        code = re.sub(r"\s", "", str(x.get("code") or "")).upper()
        typ = "ICD-10" if str(x.get("type") or "").upper().startswith("ICD") else "CDT"
        if typ == "CDT" and not dental_rules.CDT.match(code):
            continue
        if typ == "ICD-10" and not re.fullmatch(r"[A-Z][0-9][0-9A-Z](\.[0-9A-Z]{1,4})?", code):
            continue
        try:
            conf = max(0, min(100, int(x.get("confidence") or 0)))
        except (TypeError, ValueError):
            conf = 50
        row = {"code": code, "type": typ, "description": str(x.get("description") or "")[:120], "confidence": conf,
               "justification": str(x.get("justification") or "")[:300], "documentation_gap": str(x.get("documentation_gap") or "")[:300],
               "modifier": "", "units": 1}
        if typ == "CDT":
            row.update(tooth=dental_rules.norm_tooth(x.get("tooth"))[:4], surface=dental_rules.norm_surface(x.get("surface"))[:7],
                       quad=re.sub(r"[^A-Za-z]", "", str(x.get("quad") or "")).upper()[:4])
        out.append(row)
    # the rules have the last word on tooth/surface mistakes: attach them to the code they're about
    lines = [{"code": r["code"], "tooth": r.get("tooth", ""), "surface": r.get("surface", ""), "quad": r.get("quad", "")} for r in out if r["type"] == "CDT"]
    flags = dental_rules.check({"cpt": [l["code"] for l in lines], "lines": lines, "tooth_column": True})
    for f in flags:
        if f["severity"] != "error":
            continue
        for r in out:
            if r["code"] in f["message"] and (not f.get("tooth") or f.get("tooth") == r.get("tooth")):
                r["documentation_gap"] = (r["documentation_gap"] + " " if r["documentation_gap"] else "") + f["message"]
                r["confidence"] = min(r["confidence"], 55)
                break
    return out


def code_note(note: str, user, db) -> list:
    from auth import ensure_area, ensure_product
    if (user.role or "") not in ("admin", "biller", "provider", "coder"):
        raise HTTPException(status_code=403, detail="Only providers, billers and admins can code notes.")
    ensure_product(user, db, "coding")
    ensure_area(user, "code_a_note")
    import main
    key, hit = main._ai_cache_get("dental_code", {"note": note})
    if hit is not None:
        return hit
    codes = clean(_ask_ai(note))
    if codes:
        from auth import record_activity
        record_activity(db, user, "codes", len(codes))
        db.commit()
    main._ai_cache_put(key, codes)
    return codes
