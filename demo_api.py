"""
demo_api.py: public endpoints behind the interactive demos on the marketing pages (templates/demos/).

They run the real Althais engines that need no AI and no patient data: code format checks (code_validation),
NCCI procedure-to-procedure bundling edits (ncci_checker), and time-based E/M rules (coding_rules). Inputs are
codes and numbers only, capped in size, and rate-limited per visitor.
"""

import time
from collections import defaultdict, deque

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from code_validation import validate_codes
from coding_rules import resolve_time_based_codes
from ncci_checker import check_claim_ncci

router = APIRouter()

_HITS = defaultdict(deque)
LIMIT, WINDOW = 40, 60   # requests per visitor per minute


def _limited(request: Request) -> bool:
    ip = (request.headers.get("cf-connecting-ip") or request.headers.get("x-forwarded-for", "").split(",")[0].strip()
          or (request.client.host if request.client else "?"))
    now, q = time.time(), _HITS[ip]
    while q and now - q[0] > WINDOW:
        q.popleft()
    if len(q) >= LIMIT:
        return True
    q.append(now)
    if len(_HITS) > 5000:   # keep memory bounded
        for k in list(_HITS)[:1000]:
            _HITS.pop(k, None)
    return False


def _codes(body, key, kind):
    out = []
    for c in (body.get(key) or [])[:12]:
        c = str(c).strip().upper()[:10]
        if c:
            out.append({"code": c, "type": kind})
    return out


@router.post("/api/public/demo/check-codes")
async def demo_check_codes(request: Request):
    """Format check on every code, then NCCI bundling edits across the CPT codes, exactly as Check Codes does in Althais."""
    if _limited(request):
        return JSONResponse({"error": "Too many checks. Try again in a minute."}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = {}
    cpt, icd = _codes(body, "cpt", "CPT"), _codes(body, "icd", "ICD-10")
    if not cpt and not icd:
        return JSONResponse({"error": "Add at least one code."}, status_code=400)
    checked = validate_codes(cpt + icd)
    conflicts = check_claim_ncci([c["code"] for c in cpt if any(x["code"] == c["code"] and x["format_valid"] for x in checked)])
    return {"codes": checked, "conflicts": conflicts, "engine": "Althais NCCI PTP edits + code format rules"}


@router.post("/api/public/demo/time-codes")
async def demo_time_codes(request: Request):
    """Time-based E/M selection: office visits by total time, critical care thresholds, prolonged services, ED by MDM."""
    if _limited(request):
        return JSONResponse({"error": "Too many checks. Try again in a minute."}, status_code=429)
    try:
        body = await request.json()
    except Exception:
        body = {}
    try:
        minutes = max(0, min(600, int(body.get("minutes") or 0)))
    except (TypeError, ValueError):
        return JSONResponse({"error": "Minutes must be a number."}, status_code=400)
    encounter = body.get("encounter") if body.get("encounter") in ("office", "urgent", "emergency") else "office"
    codes, note = resolve_time_based_codes(minutes, "emergency" if encounter == "emergency" else "office",
                                           bool(body.get("new_patient")), bool(body.get("critical_care")))
    return {"codes": codes or [], "note": note or "", "engine": "Althais time-based coding rules"}
