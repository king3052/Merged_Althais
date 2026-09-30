"""
staff_assistant.py — Althea for staff questions.

Employees (Staff Portal): "what do I still need to do?", "when does my BLS expire?", "why wasn't my document
accepted?"... Answers are built only from the signed-in person's own staff record at the clinic they're signed in
to (the same data as /api/portal/me) — never anyone else's, and nothing is generated: the question is matched to a
fixed set of intents, and each intent's answer is assembled from the record.

Managers (main Althea, althea.js): organization questions — who's still onboarding, what needs my attention, who
hasn't finished Althais Training — through the same manager checks as the rest of Staff (staff_onboarding.manager_ctx).

Matching is by keywords first; when nothing matches and Groq is configured, the question is classified by the model
into the same intent list (it only picks an intent; it never writes the answer).
"""

import json
import os
import re

from fastapi import APIRouter, Depends, Request
from sqlalchemy.orm import Session

from auth import get_db, require_user, User
import staff_onboarding as so

router = APIRouter()

EMPLOYEE_INTENTS = ("remaining", "first_day", "expiry", "training", "where_upload", "why_rejected", "status", "access", "help")
SECTION = {"document": "documents", "form": "forms", "training": "training", "info": "info"}

_RULES = [
    ("why_rejected", r"\b(why|rejected|not accepted|wasn'?t accepted|declined|sent back|denied|problem with)\b"),
    ("expiry", r"\b(expire|expires|expiration|expiring|renew|renewal|valid until|good until)\b"),
    ("first_day", r"\b(first day|before (i|my) start|before starting|start date|when do i start)\b"),
    ("where_upload", r"\b(where|how) (do|can|should) i (upload|send|submit|put|add)\b|\bupload\b"),
    ("training", r"\b(training|course|hipaa|quiz|knowledge check)\b"),
    ("access", r"\b(access|permission|can i (see|use|open)|what can i)\b"),
    ("remaining", r"\b(still need|left|remaining|to do|todo|outstanding|missing|what do i need|finish|next)\b"),
    ("status", r"\b(status|progress|how am i doing|am i done|complete)\b"),
]


def _classify(q: str) -> str:
    s = q.lower()
    for intent, rx in _RULES:
        if re.search(rx, s):
            return intent
    key = os.environ.get("GROQ_API_KEY", "")
    if key.startswith("gsk_"):   # a real key: let the model pick one of the same intents
        try:
            from groq import Groq
            r = Groq(api_key=key).chat.completions.create(
                model="openai/gpt-oss-120b", temperature=0, max_completion_tokens=300, reasoning_effort="low",
                messages=[{"role": "user", "content": "Classify this question from a clinic employee about their own onboarding into exactly one of: "
                           + ", ".join(EMPLOYEE_INTENTS) + '. Reply with JSON only: {"intent": "..."}\nQuestion: ' + q}],
                response_format={"type": "json_object"})
            intent = json.loads(r.choices[0].message.content or "{}").get("intent")
            if intent in EMPLOYEE_INTENTS:
                return intent
        except Exception:
            pass
    return "help"


def _match(q: str, items: list, title=lambda x: x.get("title", "")) -> list:
    """Items whose title shares a meaningful word with the question (bls, license, hipaa, confidentiality...)."""
    words = {w for w in re.findall(r"[a-z]{3,}", q.lower())} - {"the", "and", "my", "when", "does", "what", "where", "why", "how", "upload",
                                                              "expire", "expires", "was", "wasn", "accepted", "document", "need", "still"}
    scored = []
    for x in items:
        tw = set(re.findall(r"[a-z]{3,}", title(x).lower()))
        if "bls" in words and "cpr" in tw or "cpr" in words and "bls" in tw:
            tw |= {"bls", "cpr"}
        hit = len(words & tw)
        if hit:
            scored.append((hit, x))
    return [x for _, x in sorted(scored, key=lambda s: -s[0])]


def _due(t):
    return f" (due {so._fmt_date(t['dueDate'])})" if t.get("dueDate") and t.get("status") != "COMPLETE" else ""


def employee_answer(q: str, me: dict, role_areas: list) -> dict:
    intent = _classify(q)
    tasks = me["tasks"]
    rank = {"WAITING_ON_EMPLOYEE": 0, "OVERDUE": 1}
    open_tasks = sorted((t for t in tasks if t.get("status") not in ("COMPLETE", "NEEDS_REVIEW")),   # fixes first, then by deadline
                        key=lambda t: (rank.get(t.get("effectiveStatus"), 2), t.get("dueDate") or "9999"))
    link = lambda label, href: {"label": label, "href": href}
    if intent == "remaining":
        if not open_tasks:
            waiting = [t["title"] for t in me.get("managerTasks", []) if t["status"] != "COMPLETE"]
            return {"intent": intent, "answer": "You've done everything on your list." + (f" Your clinic is finishing: {', '.join(waiting)}." if waiting else ""), "links": []}
        lines = [f"• {t['title']}{_due(t)}" + (" — needs a fix" if t["effectiveStatus"] == "WAITING_ON_EMPLOYEE" else "") for t in open_tasks[:8]]
        return {"intent": intent, "answer": f"You have {len(open_tasks)} thing{'s' if len(open_tasks) != 1 else ''} left:\n" + "\n".join(lines),
                "links": [link(t["title"], "#" + SECTION.get(t["type"], "tasks")) for t in open_tasks[:3]]}
    if intent == "first_day":
        start = me["person"].get("start")
        before = [t for t in open_tasks if t.get("dueDate") and (not start or t["dueDate"] <= start)]
        head = f"You start {so._fmt_date(start)}" + (f" at {me['person']['location']}" if me["person"].get("location") else "") + "." if start else "Your clinic hasn't set your start date yet."
        body = ("\nBefore then, finish:\n" + "\n".join(f"• {t['title']}{_due(t)}" for t in before[:8])) if before else "\nNothing on your list is due before then."
        return {"intent": intent, "answer": head + body, "links": [link(t["title"], "#" + SECTION.get(t["type"], "tasks")) for t in before[:3]]}
    if intent == "expiry":
        creds = me.get("credentials", [])
        hits = _match(q, creds, lambda c: c.get("label", "")) or creds
        if not creds:
            return {"intent": intent, "answer": "You don't have any verified credentials on file yet. Upload them in Credentials.", "links": [link("Credentials", "#credentials")]}
        lines = []
        for c in hits[:5]:
            d = so._today()
            days = (so.dt.date.fromisoformat(c["expires"]) - d).days if c.get("expires") else None
            lines.append(f"• {c['label']}: " + ("no expiration on file" if days is None else f"expired {so._fmt_date(c['expires'])}" if days < 0
                                               else f"expires {so._fmt_date(c['expires'])} ({days} days)"))
        return {"intent": intent, "answer": "\n".join(lines) + "\nWhen you renew, upload the new one in Credentials and Althais updates your record.",
                "links": [link("Credentials", "#credentials")]}
    if intent == "training":
        tr = [t for t in tasks if t["type"] == "training"]
        if not tr:
            return {"intent": intent, "answer": "No training is assigned to you.", "links": []}
        lines = [f"• {t['title']}: " + ("done" if t["status"] == "COMPLETE" else ("in progress" if t["status"] == "IN_PROGRESS" else "not started") + _due(t)) for t in tr]
        return {"intent": intent, "answer": "Your training:\n" + "\n".join(lines), "links": [link("Training", "#training")] +
                ([link("Althais Training course", "#course")] if any(t.get("trainingKey") == "althais_training" and t["status"] != "COMPLETE" for t in tr) else [])}
    if intent == "where_upload":
        docs = [t for t in tasks if t["type"] in ("document", "training")]
        hits = _match(q, docs)
        if hits:
            t = hits[0]
            sec = "credentials" if t.get("credentialType") else SECTION.get(t["type"], "documents")
            return {"intent": intent, "answer": f"Upload your {t['title']} in {sec.title()}: open it there and choose Upload. A PDF or a clear photo works, and Althais checks it right away.",
                    "links": [link(f"Go to {sec.title()}", f"#{sec}")]}
        return {"intent": intent, "answer": "Licenses and certifications go in Credentials; other documents (like your ID) in Documents. Training certificates go on the training itself.",
                "links": [link("Credentials", "#credentials"), link("Documents", "#documents")]}
    if intent == "why_rejected":
        back = [t for t in tasks if t.get("effectiveStatus") == "WAITING_ON_EMPLOYEE" and t.get("reviewNote")]
        hits = _match(q, back) or back
        if not hits:
            reviewing = [t["title"] for t in tasks if t.get("status") == "NEEDS_REVIEW"]
            return {"intent": intent, "answer": "Nothing of yours has been sent back." + (f" Your clinic is reviewing: {', '.join(reviewing)}." if reviewing else ""), "links": []}
        return {"intent": intent, "answer": "\n".join(f"• {t['title']}: {t['reviewNote']}" for t in hits[:4]),
                "links": [link(f"Fix {hits[0]['title']}", "#" + ("credentials" if hits[0].get("credentialType") else SECTION.get(hits[0]["type"], "tasks")))]}
    if intent == "status":
        groups = "\n".join(f"• {g['label']}: {g['done']}/{g['total']}" for g in me.get("groups", []))
        head = "Your onboarding is complete." if me["person"]["lifecycle"] == "ACTIVE" else f"You're {me['progress']}% through onboarding."
        return {"intent": intent, "answer": head + ("\n" + groups if groups else ""), "links": [link("Home", "#home")]}
    if intent == "access":
        from althais_training import AREA_TOUR
        have = [AREA_TOUR[a][0] for a in role_areas if a in AREA_TOUR]
        if not me.get("appAccess"):
            return {"intent": intent, "answer": "Right now you have the Staff Portal. Your clinic turns on the rest of your access when your onboarding is approved"
                    + (f" — as a {me['person']['role']} you'll get: {', '.join(have)}." if have else "."), "links": []}
        return {"intent": intent, "answer": f"As a {me['person']['role']}, you can use: {', '.join(have) or 'the Staff Portal'}. If your work needs more, ask your manager.", "links": []}
    return {"intent": "help", "answer": "I can help with your onboarding and staff records. Try: \"What do I still need to do?\", \"When does my BLS expire?\", "
            "\"Which training do I need?\", \"Where do I upload my license?\" or \"Why wasn't my document accepted?\"", "links": []}


@router.post("/api/portal/althea")
async def portal_althea(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    body = await request.json()
    q = str(body.get("question") or "").strip()[:500]
    if not q:
        return so._err("Ask a question.")
    me = so.portal_me(user, db)   # only this person's own record, with the portal's own checks
    org_key, m, row, doc, p = so.portal_ctx(user, db)
    return employee_answer(q, me, so.role_access(doc, p.get("role", ""))["areas"])


# ──────────────────────────────────────────────────────────────────────────
#  Managers (from main Althea)
# ──────────────────────────────────────────────────────────────────────────
def _esc(s):
    return so._html.escape(str(s or ""))


@router.post("/api/staff/onboarding/althea")
async def manager_althea(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc = so.manager_ctx(user, db)
    body = await request.json()
    intent = body.get("intent")
    people = [p for p in doc["people"] if p.get("lifecycle") != "OFFBOARDED"]
    more = lambda href, label: f'<div style="margin-top:4px"><a href="{href}" style="font-weight:600">{label} →</a></div>'
    if intent == "staff_onboarding_status":
        rows = [p for p in people if p.get("lifecycle") in so.ONBOARDING_STATES]
        if not rows:
            return {"spoken": "No one is onboarding right now.", "html": "No one is onboarding right now."}
        invites = {p["id"]: so._invite_json(so.latest_invite(db, org_key, p["id"])) for p in rows}
        return {"spoken": f"{len(rows)} {'person is' if len(rows) == 1 else 'people are'} onboarding. " + ". ".join(
                    f"{p['name']} is {so.progress(p)} percent done" for p in rows[:3]) + ".",
                "html": "".join(f"<div>{_esc(p['name'])}: <b>{so.progress(p)}%</b> <span style=\"color:#9aa0ac\">{_esc(so.next_action(p, invites[p['id']]))}</span></div>" for p in rows)
                        + more("/staff/onboarding", "Open Onboarding")}
    if intent == "staff_needs_attention":
        items = so.attention_items(db, org_key, doc, {p["id"]: so._invite_json(so.latest_invite(db, org_key, p["id"])) for p in doc["people"]})
        if not items:
            return {"spoken": "Nothing needs your attention right now.", "html": "Nothing needs your attention right now."}
        return {"spoken": f"{len(items)} {'item needs' if len(items) == 1 else 'items need'} your attention. " + ". ".join(f"{i['name']}: {i['text']}" for i in items[:3]) + ".",
                "html": "".join(f"<div>{_esc(i['name'])}: {_esc(i['text'])}</div>" for i in items[:8]) + more("/staff/needs-attention", "Open Needs Attention")}
    if intent == "staff_althais_training":
        rows = [p for p in people if any(t.get("trainingKey") == "althais_training" and t.get("status") != "COMPLETE" for t in p["requirements"])]
        if not rows:
            return {"spoken": "Everyone has completed Althais Training.", "html": "Everyone has completed Althais Training."}
        return {"spoken": f"{len(rows)} {'person hasn’t' if len(rows) == 1 else 'people haven’t'} completed Althais Training: " + ", ".join(p["name"] for p in rows[:5]) + ".",
                "html": "".join(f"<div>{_esc(p['name'])}</div>" for p in rows) + more("/staff/training", "Open Training")}
    return so._err("Unknown question.", 400)
