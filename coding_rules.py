"""
Deterministic, rule-based CPT code selection for time-based billing.

Some CPT codes are billed strictly by documented minutes, with hard
numeric thresholds set by CMS/AMA. These should NEVER be left purely to
an LLM's judgment — an LLM can misread or approximate a number, and a
payer audit checks the literal minute count on the note. This module
encodes those specific, official rules as plain arithmetic instead.

Important, deliberate exception: emergency department E/M codes
(99281-99285) are NOT time-based. AMA CPT guidance is explicit that ED
E/M level is determined by Medical Decision Making only, precisely
because ED time is unpredictable and often interrupted. Applying a time
threshold to an ED visit would be *incorrect* coding, not more accurate
coding — so this module deliberately declines to touch ED E/M codes and
says why, rather than silently guessing.

Sources for the thresholds below: AMA CPT critical care guidelines
(99291/99292) and the CPT 2021+ time-based E/M table for office/
outpatient visits. These are current as of this writing but CPT/CMS
values are revised periodically — worth re-verifying against the
current-year CPT manual on a regular cadence, not treating this file as
permanently correct.
"""


def critical_care_codes(minutes):
    """
    99291 covers the first 30-74 minutes of critical care on a given date.
    Each additional full 30-minute block beyond the first 74 adds one unit
    of 99292 (CMS rounds up once at least half — 15+ minutes — into the
    next block). Under 30 minutes, critical care cannot be billed at all;
    the correct fallback is the appropriate standard E/M level.

    Returns a list of code dicts, or None if the 30-minute floor isn't met.
    """
    if minutes is None or minutes < 30:
        return None

    codes = [{
        "code": "99291", "type": "CPT", "units": 1,
        "description": "Critical care, evaluation and management, first 30-74 minutes",
        "justification": f"{minutes} total minutes of documented critical care meets the 99291 threshold (30-74 min).",
        "confidence": 99, "modifier": "", "documentation_gap": "",
        "time_verified": True,
    }]

    remaining = minutes - 74
    if remaining > 0:
        # Verified against the official bracket table (30-74:0, 75-104:1,
        # 105-134:2, 135-164:3, 165-194:4, 195-224:5, ...): once total time
        # reaches 75 minutes, each additional full 30-minute block adds one
        # more 99292 unit.
        extra_units = 1 + (minutes - 75) // 30 if minutes >= 75 else 0
        if extra_units > 0:
            codes.append({
                "code": "99292", "type": "CPT", "units": extra_units,
                "description": "Critical care, each additional 30 minutes",
                "justification": f"{minutes} total minutes documented — {extra_units} additional 30-minute increment(s) beyond the first 74 minutes.",
                "confidence": 99, "modifier": "", "documentation_gap": "",
                "time_verified": True,
            })
    return codes


# CPT 2021+ time-based table for office/outpatient E/M — an alternative
# basis to MDM-based leveling. Applies to office visits and urgent-care
# encounters billed as office E/M, NOT to true emergency department visits.
_NEW_PATIENT_TIME = [
    (15, 29, "99202"), (30, 44, "99203"), (45, 59, "99204"), (60, 74, "99205"),
]
_EST_PATIENT_TIME = [
    (10, 19, "99212"), (20, 29, "99213"), (30, 39, "99214"), (40, 54, "99215"),
]


def office_visit_time_code(minutes, is_new_patient):
    table = _NEW_PATIENT_TIME if is_new_patient else _EST_PATIENT_TIME
    for lo, hi, code in table:
        if lo <= minutes <= hi:
            return code, lo, hi
    if minutes > table[-1][1]:
        lo, hi, code = table[-1]
        return code, lo, hi
    return None, None, None


# Prolonged service add-on codes (99417 for commercial payers, G2212 for
# Medicare) apply on top of the two highest office/outpatient E/M codes
# (99205 new patient, 99215 established) *only* when the level was selected
# by time, not MDM.
#
# Threshold used here — corroborated by two independent Medicare
# Administrative Contractor sources (Noridian JE and JF) plus a third payer
# guide, all stating the same number: the primary code's max time must be
# exceeded by at least 15 full minutes before the first unit is billable,
# with no midpoint rounding (a full 15 minutes is required per unit, not a
# partial credit at 8+ minutes like some other time-based codes use).
#
# Honest caveat: sources genuinely disagree on whether commercial-payer
# 99417 uses this same "+15 min" threshold or a looser "+1 min" threshold —
# this module uses the more conservative, better-corroborated "+15 min"
# rule for both 99417 and G2212, and flags the discrepancy via
# documentation_gap so a biller knows to verify the specific payer's policy
# rather than treating this as settled the way the critical care brackets are.
_TOP_TIER_MAX_TIME = {"99205": 74, "99215": 54}


def prolonged_service_code(minutes, base_code, payer):
    """
    Returns a prolonged-service code dict, or None if the base code isn't
    eligible (not 99205/99215) or minutes don't clear the threshold.
    """
    max_time = _TOP_TIER_MAX_TIME.get(base_code)
    if max_time is None:
        return None
    threshold = max_time + 15
    if minutes < threshold:
        return None

    extra_units = 1 + (minutes - threshold) // 15
    is_medicare = (payer or '').strip().lower() == 'medicare'
    code = "G2212" if is_medicare else "99417"
    return {
        "code": code, "type": "CPT", "units": extra_units,
        "description": f"Prolonged office/outpatient E/M service, each additional 15 minutes beyond {base_code}",
        "justification": f"{minutes} total minutes documented exceeds {base_code}'s {max_time}-minute maximum by at least 15 minutes ({extra_units} unit(s) of prolonged service).",
        "confidence": 90,  # lower than the base E/M code — see module note on payer variance
        "modifier": "", "time_verified": True,
        "documentation_gap": (
            "Prolonged-service billing rules vary by payer (some Medicare Advantage "
            "and Medicaid plans follow different thresholds than traditional Medicare "
            "or commercial guidance) — verify this specific payer's current policy "
            "before submitting."
        ),
    }


# Recognize CPT codes this module governs, so the caller can remove any
# conflicting AI-guessed E/M code before inserting the deterministic one —
# otherwise a biller could see two different E/M levels suggested side by
# side with no reconciliation.
_OFFICE_EM_CODES = {c for _, _, c in _NEW_PATIENT_TIME + _EST_PATIENT_TIME}
_CRITICAL_CARE_CODES = {"99291", "99292"}
_PROLONGED_SERVICE_CODES = {"99417", "G2212"}


def is_governed_code(code):
    return code in _OFFICE_EM_CODES or code in _CRITICAL_CARE_CODES or code in _PROLONGED_SERVICE_CODES


def resolve_time_based_codes(minutes, encounter_type="emergency", is_new_patient=True, is_critical_care=False, payer=None):
    """
    Returns (codes, note):
      codes - a list of deterministic CPT code dicts, or None if no rule applies
      note  - a plain-English explanation when duration deliberately wasn't
              used to pick a code (e.g. below threshold, or ED visit where
              time isn't the correct basis at all) — shown to the biller so
              the system's reasoning is visible, not a silent no-op.
    """
    if minutes is None or minutes <= 0:
        return None, None

    if is_critical_care:
        codes = critical_care_codes(minutes)
        if codes is None:
            return None, (
                f"{minutes} minutes documented is below the 30-minute critical care "
                f"threshold — bill the appropriate E/M level instead of a critical care code."
            )
        return codes, None

    if encounter_type == "emergency":
        return None, (
            "Emergency department E/M levels (99281-99285) are based on Medical "
            "Decision Making, not visit duration, per current AMA CPT guidance — "
            "duration alone won't override the E/M level for this encounter type."
        )

    code, lo, hi = office_visit_time_code(minutes, is_new_patient)
    if not code:
        return None, None
    codes = [{
        "code": code, "type": "CPT", "units": 1,
        "description": f"Office/outpatient E/M selected by time ({lo}-{hi} min)",
        "justification": f"{minutes} minutes of total time documented falls in the {lo}-{hi} minute range for this code.",
        "confidence": 97, "modifier": "", "documentation_gap": "",
        "time_verified": True,
    }]
    prolonged = prolonged_service_code(minutes, code, payer)
    if prolonged:
        codes.append(prolonged)
    return codes, None
