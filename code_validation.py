"""
Code format validation for AI-suggested ICD-10/CPT codes.

IMPORTANT SCOPE LIMITATION — read before assuming this catches every bad
code: this module checks whether a code is *shaped* like a real code
(correct format/pattern), not whether it exists in the official current
CPT or ICD-10-CM code sets. Two real constraints make a full
existence-check impractical here:

1. CPT codes (and their descriptions) are copyrighted by the American
   Medical Association. Embedding a full CPT lookup table would mean
   reproducing their copyrighted database at scale — not something to
   do without a proper AMA license.
2. ICD-10-CM itself is public domain (CDC/NCHS-maintained), but the
   full current set is 70,000+ codes — impractical to embed and keep
   in sync here.

What this DOES catch: a hallucinated code with the wrong number of
digits, an impossible ICD-10 chapter letter, a CPT-shaped string that's
actually not a valid Category I/III or HCPCS Level II pattern, etc.
This is a real, meaningful safety net against malformed AI output, but
it is NOT the same as verifying the code exists and means what the AI
says it means — that still requires a licensed coding reference or a
human coder's review, same as the eval-set caveat elsewhere in this app.
"""

import re

# ICD-10-CM: letter (not U, reserved), 2 digits, optional decimal + up to
# 4 more alphanumeric characters. E.g. S61.409A, J06.9, A41.9, R07.9.
_ICD10_RE = re.compile(r"^[A-TV-Z][0-9]{2}(\.[0-9A-Z]{1,4})?$", re.IGNORECASE)

# CPT Category I: exactly 5 digits (e.g. 99284, 12001).
_CPT_CAT1_RE = re.compile(r"^\d{5}$")
# CPT Category III (emerging technology): 4 digits + literal "T" (e.g. 0510T).
_CPT_CAT3_RE = re.compile(r"^\d{4}T$", re.IGNORECASE)
# HCPCS Level II: one letter + 4 digits (e.g. G2212, J1100).
_HCPCS_RE = re.compile(r"^[A-Z]\d{4}$", re.IGNORECASE)


def check_code_format(code, code_type):
    """
    Returns (is_valid_format, warning_message_or_None).
    code_type: "ICD-10" or "CPT" (case-insensitive, tolerant of variants
    the model might use like "CPT-4" or "ICD10").
    """
    code = (code or "").strip()
    if not code:
        return False, "Empty code"

    t = (code_type or "").upper()
    is_icd = "ICD" in t
    is_cpt = "CPT" in t or "HCPCS" in t or not is_icd  # default to CPT-family check if ambiguous

    if is_icd:
        if _ICD10_RE.match(code):
            return True, None
        return False, f"'{code}' doesn't match the ICD-10-CM format (letter + 2 digits, optional decimal + up to 4 more characters) — verify this code before submitting."

    if is_cpt:
        if _CPT_CAT1_RE.match(code) or _CPT_CAT3_RE.match(code) or _HCPCS_RE.match(code):
            return True, None
        return False, f"'{code}' doesn't match a recognized CPT/HCPCS format (5 digits, 4 digits+T, or letter+4 digits) — verify this code before submitting."

    return True, None  # unknown type — don't block on something we can't classify


def validate_codes(codes):
    """
    Takes a list of code dicts (each with at least 'code' and 'type'),
    returns the same list with 'format_valid' and 'format_warning' added
    to each entry. Does not remove or alter anything — just annotates,
    so the frontend/biller decides what to do with a flagged code rather
    than having it silently dropped.
    """
    annotated = []
    for c in codes:
        if not isinstance(c, dict):
            continue
        valid, warning = check_code_format(c.get("code"), c.get("type"))
        c = dict(c)  # don't mutate the caller's dict
        c["format_valid"] = valid
        if warning:
            c["format_warning"] = warning
        annotated.append(c)
    return annotated
