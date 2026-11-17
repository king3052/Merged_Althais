"""
coding_eval.py: score Althais's AI coder on a fixed set of labeled visit notes (evals/coding_cases.json), so a change of
model, prompt or rule can be measured instead of guessed.

Run it from the admin console (/admin/quality > Run the coding test). It runs every case through the same claim builder
the EMR uses (main._claim_auto_work), with whatever AI the deployment is set to (AI_PROVIDER), and keeps the scorecard.
To compare Groq and Gemini: run it on each, same cases, and read the two rows side by side.

The labels are a draft until a certified coder has checked them (the file says which). Scores against unchecked labels
show direction, not truth.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Dict, List, Optional

from sqlalchemy import select

CASES = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evals", "coding_cases.json")
ORG, CAT = "__evals__", "coding_eval"
KEEP = 20


def load_cases() -> dict:
    with open(CASES, encoding="utf-8") as f:
        return json.load(f)


def _is_cpt(c: dict) -> bool:
    t = str(c.get("type", "")).upper()
    code = str(c.get("code", "")).upper()
    if t.startswith("ICD"):
        return False
    return t.startswith("CPT") or t.startswith("HCPCS") or (len(code) == 5 and (code[:4].isdigit() or code[0] in "JQ" and code[1:].isdigit()))


def _is_em(code: str) -> bool:
    return len(code) == 5 and code.startswith("99") and code[2] in "2345"


def _norm(code) -> str:
    return str(code or "").strip().upper()


def score(case: dict, codes: List[dict]) -> dict:
    """How one answer compares with the case's labels."""
    exp = case["expect"]
    cpts = [c for c in codes if _is_cpt(c)]
    dxs = [c for c in codes if not _is_cpt(c)]
    got_cpt = [_norm(c.get("code")) for c in cpts]
    got_dx = [_norm(c.get("code")) for c in dxs]
    mods = {}
    for c in cpts:
        for m in str(c.get("modifier") or "").replace(",", " ").split():
            mods.setdefault(_norm(c.get("code")), set()).add(m.upper())

    cpt_slots = [[_norm(x) for x in s] for s in exp["cpt"]]
    cpt_hit = [any(x in got_cpt for x in s) for s in cpt_slots]
    allowed = {x for s in cpt_slots for x in s}
    cpt_extra = [x for x in got_cpt if x not in allowed]
    em_slot = next((s for s in cpt_slots if any(_is_em(x) for x in s)), None)
    got_em = [x for x in got_cpt if _is_em(x)]
    if em_slot:
        em = "best" if got_em[:1] == [em_slot[0]] and len(got_em) == 1 else ("ok" if len(got_em) == 1 and got_em[0] in em_slot else "wrong")
    else:
        em = "right" if not got_em else "wrong"        # a procedure-only visit: no E/M should be billed

    dx_slots = [[_norm(x) for x in s] for s in exp["icd10"]]
    dx_hit = [any(x in got_dx for x in s) for s in dx_slots]
    dx_cat = [any(x[:3] in {g[:3] for g in got_dx} for x in s) for s in dx_slots]

    want_mods = {_norm(k): {m.upper() for m in v} for k, v in (exp.get("modifiers") or {}).items()}
    mods_ok = all(want <= mods.get(code, set()) for code, want in want_mods.items())
    stray = [f"{code}-{m}" for code, ms in mods.items() for m in ms if m in ("25", "59", "XU", "XS") and m not in want_mods.get(code, set())]
    mods_ok = mods_ok and not stray

    cpt_exact = all(cpt_hit) and not cpt_extra
    return {"cpt_exact": cpt_exact, "cpt_hit": sum(cpt_hit), "cpt_slots": len(cpt_slots), "cpt_extra": cpt_extra,
            "em": em, "dx_hit": sum(dx_hit), "dx_slots": len(dx_slots), "dx_primary": bool(dx_hit[:1] and dx_hit[0]),
            "dx_category": sum(dx_cat), "dx_extra": len([x for x in got_dx if not any(x in s for s in dx_slots)]),
            "mods_ok": mods_ok, "stray_mods": stray,
            "correct": cpt_exact and all(dx_hit) and mods_ok and em != "wrong",
            "got": {"cpt": [c + ("-" + "-".join(sorted(mods[c])) if c in mods else "") for c in got_cpt], "icd10": got_dx}}


def summarize(results: List[dict]) -> dict:
    ok = [r for r in results if not r.get("error")]
    n = len(ok) or 1
    pct = lambda k: round(100 * sum(1 for r in ok if r["score"][k]) / n)
    em_cases = [r for r in ok if r["score"]["em"] in ("best", "ok", "wrong", "right")]
    return {"cases": len(results), "errors": len(results) - len(ok),
            "fully_correct": pct("correct"), "cpt_exact": pct("cpt_exact"),
            "em_right": round(100 * sum(1 for r in em_cases if r["score"]["em"] != "wrong") / (len(em_cases) or 1)),
            "em_best": round(100 * sum(1 for r in em_cases if r["score"]["em"] in ("best", "right")) / (len(em_cases) or 1)),
            "dx_recall": round(100 * sum(r["score"]["dx_hit"] for r in ok) / (sum(r["score"]["dx_slots"] for r in ok) or 1)),
            "dx_primary": pct("dx_primary"), "modifiers": pct("mods_ok"),
            "ai_answered": round(100 * sum(1 for r in ok if r.get("source") in ("ai", "ai_fast")) / n),
            "avg_check_score": round(sum(r.get("check_score") or 0 for r in ok) / n),
            "avg_seconds": round(sum(r.get("ms") or 0 for r in ok) / n / 1000, 1)}


def run(cases: List[dict], build: Callable[[dict], dict], workers: int = 3, progress: Optional[Callable[[int], None]] = None) -> dict:
    """build(case) -> {"codes": [...], "check": {...}, "ms": ...} (the claim builder's answer)."""
    done = [0]

    def one(case):
        t0 = time.time()
        try:
            res = build(case)
            r = {"id": case["id"], "title": case["title"], "score": score(case, res.get("codes") or []),
                 "check_score": (res.get("check") or {}).get("score"), "ms": res.get("ms") or int((time.time() - t0) * 1000),
                 "source": res.get("source") or ""}
        except Exception as e:
            r = {"id": case["id"], "title": case["title"], "error": f"{type(e).__name__}: {str(e)[:200]}"}
        done[0] += 1
        if progress:
            progress(done[0])
        return r
    with ThreadPoolExecutor(max_workers=workers) as ex:
        results = list(ex.map(one, cases))
    return {"summary": summarize(results), "results": results}


# ── kept runs ──────────────────────────────────────────────────────────────────────────────────────────────────────────
_job: Dict[str, object] = {}


def runs(db) -> List[dict]:
    from auth import OrgSettings
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == ORG, OrgSettings.category == CAT))
    try:
        return json.loads(row.data).get("runs", []) if row and row.data else []
    except ValueError:
        return []


def _keep(db, run_doc: dict):
    from auth import OrgSettings
    row = db.scalar(select(OrgSettings).where(OrgSettings.org_key == ORG, OrgSettings.category == CAT))
    if row is None:
        row = OrgSettings(org_key=ORG, category=CAT, data="{}")
        db.add(row)
    doc = json.loads(row.data or "{}")
    doc["runs"] = ([run_doc] + doc.get("runs", []))[:KEEP]
    row.data = json.dumps(doc)
    db.commit()


def ai_label() -> str:
    import ai_client
    if ai_client.PROVIDER == "vertex":
        return f"Gemini on Vertex ({ai_client.VERTEX_MODEL} / {ai_client.VERTEX_FAST_MODEL}, thinking {ai_client.THINKING})"
    return "Groq (" + os.environ.get("ALTHAIS_BUILD_MODEL", "gpt-oss-120b") + " / " + os.environ.get("ALTHAIS_FAST_MODEL", "gpt-oss-20b") + ")"


def start(build: Callable[[dict], dict], note: str = "") -> bool:
    """Run every case on a thread and keep the scorecard. False if a run is already going."""
    if _job.get("state") == "running":
        return False
    data = load_cases()
    cases = data["cases"]
    _job.clear()
    _job.update({"state": "running", "done": 0, "total": len(cases), "started": time.time()})

    def work():
        from auth import SessionLocal
        try:
            out = run(cases, build, progress=lambda n: _job.update(done=n))
            doc = {"at": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "ai": ai_label(), "note": note[:200],
                   "labels": data.get("label_status", ""), **out}
            db = SessionLocal()
            try:
                _keep(db, doc)
            finally:
                db.close()
            _job.update(state="done", summary=out["summary"])
        except Exception as e:
            _job.update(state="error", error=f"{type(e).__name__}: {str(e)[:200]}")
    threading.Thread(target=work, daemon=True).start()
    return True


def job() -> dict:
    return dict(_job)
