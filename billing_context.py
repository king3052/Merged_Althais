"""What the AI is given to read before it codes a claim or drafts an appeal: the retrieval layer's reference entries
(policy_knowledge.py) plus facts looked up exactly for this claim, so the model works from rules instead of memory.

  for_claim()   the claim builder (main._build_claim): reference entries that match the note's services and the codes,
                and the NCCI pair edits between those codes with what each edit's modifier indicator allows.
                Switched on with RAG_CODING=1. It's off by default until the coding test (admin > Quality) has been run
                with it on and off, so a change to how claims are coded is measured, not guessed.
  for_appeal()  appeal letters: what each denial reason code means, the reference entries for that denial and the codes,
                and the clinic's own appeal window with that payer from Clinic setup. On unless RAG_APPEALS=0.

A note is searched by keywords only (never sent to the embeddings service); appeals search by meaning too, because
their query holds only codes and reason codes. Every block lists entry ids so the result can say what it relied on.
"""
from __future__ import annotations

import os
import re
from typing import List, Optional

import policy_knowledge as pk

_CPT = re.compile(r"^(?:\d{4}[0-9A-Z]|[A-Z]\d{4})$")


def coding_on() -> bool:
    return os.environ.get("RAG_CODING", "0") == "1"


def appeals_on() -> bool:
    return os.environ.get("RAG_APPEALS", "1") != "0"


def _procedure_codes(codes) -> List[str]:
    out = []
    for c in codes or []:
        code = str((c.get("code") if isinstance(c, dict) else c) or "").strip().upper()
        kind = str(c.get("type", "CPT") if isinstance(c, dict) else "CPT").upper()
        if kind.startswith("CPT") and _CPT.match(code) and code not in out:
            out.append(code)
    return out


_NCCI_MEANING = {"0": "never billed together; no modifier bypasses it",
                 "1": "a modifier (25, 59, XE/XS/XP/XU) bypasses it only if the note shows the services were separate",
                 "9": "the edit no longer applies"}


def ncci_lines(codes, dos: Optional[str] = None) -> List[str]:
    """The NCCI procedure-to-procedure edits between these codes, in words."""
    cpts = _procedure_codes(codes)
    if len(cpts) < 2:
        return []
    try:
        import ncci_checker
        pairs = ncci_checker._edits(cpts, dos)
    except Exception as e:
        print(f"[RAG] NCCI lookup skipped: {type(e).__name__}", flush=True)
        return []
    return [f"NCCI edit: {a} (column 1) with {b} (column 2), modifier indicator {ind}: {_NCCI_MEANING.get(str(ind), 'see the NCCI table')}."
            for (a, b), ind in sorted(pairs.items())]


# Services a note shows that each have a billing rule worth reading: matched exactly, so they're never missed the way a
# long note can dilute a keyword search. (pattern, entry ids)
TRIGGERS = [
    (r"lacerat|sutur|stitch|steri.?strip|dermabond|wound (repair|closure)", ["svc-laceration", "minor-procedure-global"]),
    (r"incision and drainage|\bI&D\b|abscess drain|foreign body remov|cerumen|ear wax remov", ["minor-procedure-global"]),
    (r"vaccin|immuniz|tdap|\btd\b|flu shot|influenza vaccine|boostrix|adacel", ["svc-vaccines"]),
    (r"rapid (strep|flu|covid|antigen)|strep test|influenza test|covid.{0,10}test|urine (dip|hcg)|pregnancy test|point.of.care|glucose finger", ["mod-qw", "svc-labs"]),
    (r"sent to (the )?lab|send.out|reference lab|venipuncture|blood draw", ["svc-labs"]),
    (r"nebuli[sz]|breathing treatment|duoneb|albuterol neb", ["svc-nebulizer-ecg", "mod-76-77-91"]),
    (r"\b(ekg|ecg|electrocardiogram)\b", ["svc-nebulizer-ecg"]),
    (r"injection|\bIM\b|intramuscular|subcutaneous|toradol|ketorolac|ceftriaxone|rocephin|dexamethasone|decadron|depo.medrol|kenalog", ["svc-injections", "ncci-mue"]),
    (r"x.?ray|radiograph|\bxr\b|views? of", ["mod-26-tc"]),
    (r"telehealth|telemedicine|video visit|virtual visit|audio.only|by phone", ["mod-telehealth"]),
    (r"at work|work.related|on the job|workers.? comp|employer", ["svc-workers-comp"]),
    (r"critical care|unstable|resuscitat", ["em-critical-care"]),
    (r"(total|spent)( time)?( of| was|:)? ?\d+ ?min|\d+ ?min(ute)?s? (total|spent|face)", ["em-time"]),
]
_PROCEDURE_IDS = {"svc-laceration", "minor-procedure-global", "svc-injections", "svc-nebulizer-ecg"}


def _note_query(note: str) -> str:
    """The parts of a note that say what was done and why (assessment, plan, procedures), without the header (names,
    addresses, insurance), which only adds noise to a keyword search."""
    note = note or ""
    m = re.search(r"(?im)^\s*(assessment|impression|a/p|plan|procedure)", note)
    return (note[m.start():] if m else note)[:6000]


# words every note has, which say nothing about which billing rule applies
_NOTE_WORDS = re.compile(r"(?i)\b(assessment|impression|plan|care|patient|pt|treatment|treated|history|exam|negative|positive|given|"
                         r"daily|mg|ml|follow|return|acute|chronic|left|right|normal|reports|denies|states)\b")


def triggered(note: str) -> List[str]:
    ids: List[str] = []
    for pat, hit in TRIGGERS:
        if re.search(pat, note or "", re.I):
            ids += [i for i in hit if i not in ids]
    # a procedure plus a separately evaluated problem is the modifier-25 situation
    if any(i in _PROCEDURE_IDS for i in ids) and re.search(r"(?i)assessment|diagnos|prescri|evaluat", note or ""):
        ids.insert(0, "mod-25")
    return ids


def for_claim(note: str, codes=None, dos: Optional[str] = None, k: int = 6) -> dict:
    """{"block": prompt text or "", "ids": [entry ids], "ncci": [lines]} for the claim builder: the rules for the services
    the note shows, then keyword matches on the assessment and plan, then the NCCI edits between the codes."""
    ids = triggered(note)[:k]
    if len(ids) < k:
        query = _note_query(note) + " " + " ".join(_procedure_codes(codes))
        query = _NOTE_WORDS.sub(" ", query)
        found = pk.search(query, k=k, min_score=4.0, embed=False, min_terms=2)   # keywords only: the note stays out of the embeddings call
        top = max((h.keyword for h in found), default=0)
        for h in found:
            if h.id not in ids and len(ids) < k and h.keyword >= 0.6 * top:     # only matches close to the best one
                ids.append(h.id)
    hits = [pk.hit(i) for i in ids if i in pk.BY_ID]
    ncci = ncci_lines(codes, dos)
    if not hits and not ncci:
        return {"block": "", "ids": [], "ncci": []}
    lines = ["REFERENCE (billing rules that match this visit; follow them where they apply, ignore what doesn't; when one decides "
             "a code or modifier, put its id in that code's reason, e.g. [mod-25]):"]
    lines += ["- " + h.cite() for h in hits]
    lines += ["- " + x for x in ncci]
    return {"block": "\n".join(lines), "ids": [h.id for h in hits], "ncci": ncci}


_CARC_RE = re.compile(r"\b(CO|PR|OA|PI|CR)\s*-?\s*([A-Z]?\d{1,3})\b", re.I)


def denial_reasons(text: str) -> List[tuple]:
    """[(group, code)] from text like 'CO-97 bundled' or 'denied CO 16 / N290'."""
    seen, out = set(), []
    for g, c in _CARC_RE.findall(text or ""):
        key = (g.upper(), c.upper())
        if key not in seen:
            seen.add(key)
            out.append(key)
    return out


def for_appeal(status: str, cpt: str = "", icd: str = "", payer: str = "", org: Optional[str] = None, db=None, k: int = 4) -> dict:
    """{"block", "ids"} for an appeal letter: the reason codes in words, the matching reference entries, and the clinic's
    appeal window with this payer when Clinic setup has one."""
    lines: List[str] = []
    reasons = denial_reasons(status)
    try:
        import lost_money as lm
        for g, c in reasons:
            if c in lm.CARC:
                lines.append(f"Reason code {g}-{c}: {lm.CARC[c]}.")
    except Exception:
        pass
    query = " ".join([status or "", cpt or "", " ".join(f"{g}-{c} denial" for g, c in reasons)])
    hits = pk.search(query, k=k, min_score=1.5)
    lines += [h.cite() for h in hits]
    if org and payer and db is not None:
        try:
            import clinic_setup
            terms = clinic_setup.terms(db, org)
            days = terms._days(terms.appeal_days, payer, "") if terms.appeal_days else None
            if days:
                lines.append(f"This clinic's appeal window with {payer} (from its contract, Clinic setup): {days} days from the payment.")
        except Exception as e:
            print(f"[RAG] clinic appeal window skipped: {type(e).__name__}", flush=True)
    if not lines:
        return {"block": "", "ids": []}
    return {"block": "REFERENCE (rules for this denial; base the argument on them and on the claim facts, never invent policy "
                      "names, numbers or quotes):\n" + "\n".join("- " + x for x in lines),
            "ids": [h.id for h in hits]}
