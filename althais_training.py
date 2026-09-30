"""
althais_training.py — the built-in Althais Training course (Staff Portal > Training).

Eight short modules (about 20 minutes), shaped to the person taking it: the workspace tour and the role module only
cover what their clinic role on Staff > Roles actually lets them use (role_access), so a front desk employee never
gets a physician's walkthrough. Module 7 is a knowledge check graded on the server: questions are drawn at random
from the bank with their options shuffled, and the answers never reach the browser. Passing records the completion
(course version, score, date) on the staff record and completes the "Althais Training" onboarding item.

Versions: COURSE_VERSION goes on every completion. When a new version matters (a material product or security
change), managers can require it from everyone (Staff > Onboarding Templates > Training); earlier completions move
to the record's history, never deleted.
"""

import datetime as dt
import json
import random
import secrets

from fastapi import APIRouter, Depends, Request, HTTPException
from sqlalchemy import String, Integer, DateTime, Text, select
from sqlalchemy.orm import Mapped, mapped_column, Session

from auth import Base, engine, get_db, require_user, User
import staff_onboarding as so

router = APIRouter()

COURSE_KEY = "althais_training"
COURSE_VERSION = so.ALTHAIS_COURSE_VERSION
QUIZ_SIZE = 7
DEFAULT_PASS_SCORE = 80


class TrainingAttempt(Base):
    """One knowledge-check attempt: which questions (and option order) were asked, the answers, and the score."""
    __tablename__ = "training_attempts"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    org_key: Mapped[str] = mapped_column(String(255), index=True)
    person_id: Mapped[str] = mapped_column(String(64), index=True)
    course: Mapped[str] = mapped_column(String(64), default=COURSE_KEY)
    version: Mapped[str] = mapped_column(String(16), default=COURSE_VERSION)
    token: Mapped[str] = mapped_column(String(32), index=True)
    questions: Mapped[str] = mapped_column(Text)              # [{id, order:[original option indexes]}]
    answers: Mapped[str] = mapped_column(Text, default="{}")
    score: Mapped[int] = mapped_column(Integer, nullable=True, default=None)
    passed: Mapped[int] = mapped_column(Integer, default=0)
    pass_score: Mapped[int] = mapped_column(Integer, default=DEFAULT_PASS_SCORE)
    started_at: Mapped[dt.datetime] = mapped_column(DateTime, default=so._now)
    submitted_at: Mapped[dt.datetime] = mapped_column(DateTime, nullable=True, default=None)


Base.metadata.create_all(engine)


# ──────────────────────────────────────────────────────────────────────────
#  Content
# ──────────────────────────────────────────────────────────────────────────
AREA_TOUR = {
    "overview": ("Overview", "Your home base: today's schedule, what needs attention, and quick actions."),
    "patients": ("Patients & Charts", "Find patients and open their charts. Open only the charts you need for the work in front of you."),
    "schedule": ("Schedule", "See and manage appointments for the day or week."),
    "scribe": ("Write A Note", "Draft visit notes, including from dictation. You review, correct and finish every note."),
    "code_a_note": ("Code A Note", "Get suggested billing codes for a finished note. A person confirms every code before it's used."),
    "coding_review": ("Coding Review", "Review coding across visits and catch problems before claims go out."),
    "claims": ("Claims", "Build, check and submit claims, then follow each one's status."),
    "denials": ("Denials", "See which claims were denied, why, and what to do next."),
    "appeals": ("Appeals", "Prepare and track appeals for denied claims."),
    "payments": ("Payments", "What's been collected and what's still outstanding."),
    "payer_intelligence": ("Payer Intelligence", "How each insurance payer is performing, and their rules."),
    "team": ("Staff", "Team records, onboarding, credentials, training and roles for your clinic."),
    "compliance": ("Compliance", "Expiring credentials, overdue training and other staff compliance items."),
    "settings": ("Settings", "Clinic settings. Changes here affect everyone, so only change what you're responsible for."),
    "althea": ("Althea", "Ask a question or give a command by typing or speaking."),
}
ALWAYS_TOUR = [
    ("Staff Portal", "Your own onboarding, documents, credentials and training. This is where you upload a renewed license or card."),
    ("Notifications", "Althais emails you about deadlines, renewals and anything that needs your attention."),
]


def _role_family(role: str) -> str:
    r = (role or "").lower()
    if "physician" in r and "assistant" not in r or r in ("np / pa", "np", "pa") or "practitioner" in r:
        return "physician"
    if "bill" in r or "cod" in r:
        return "biller"
    if "front" in r or "reception" in r or "desk" in r:
        return "front_desk"
    if "manager" in r or "admin" in r:
        return "manager"
    if "nurse" in r or "assistant" in r:
        return "clinical"
    return "general"


def _card(title, body="", bullets=None, kind="text", **extra):
    return {"kind": kind, "title": title, "body": body, "bullets": bullets or [], **extra}


def _role_module(family: str, areas: set, role: str) -> list:
    has = lambda *a: any(x in areas for x in a)
    cards = []
    if family == "physician":
        if has("patients"):
            cards.append(_card("Your patient workflow", "Open the chart from the schedule or a search, review what's new, and see the visit.",
                               ["Charts show allergies, medications, labs and history in one place.",
                                "Everything you open or change is part of the chart's record."]))
        if has("scribe"):
            cards.append(_card("Documentation, including dictation", "Write A Note drafts your visit note, from dictation where your clinic uses it.",
                               ["Read every generated note before you sign it. You're signing what it says, not what you meant.",
                                "Fix anything wrong or missing in the note itself, so the record is right."]))
        if has("code_a_note", "coding_review"):
            cards.append(_card("Coding suggestions", "Althais suggests codes from the note. They're suggestions: you or your billing team confirm each one.",
                               ["If a code doesn't match what you documented, change the code or the note — don't submit it as-is."]))
        cards.append(_card("What only you can approve", "Althais prepares work; clinical decisions and signatures stay yours.",
                           ["Diagnoses, orders and treatment decisions are never made by Althais.", "Approve only work you've actually reviewed."]))
    elif family == "biller":
        if has("code_a_note", "coding_review"):
            cards.append(_card("Coding workflow", "Suggested codes come from the documented visit. Check them against the note before they go on a claim.",
                               ["A suggestion that isn't supported by the documentation shouldn't be billed."]))
        if has("claims"):
            cards.append(_card("Claims and claim status", "Build and check claims before submitting, then follow their status.",
                               ["Althais flags claims at risk of denial and what's missing. Fix the cause, not just the flag."]))
        if has("denials", "appeals"):
            cards.append(_card("Denials and appeals", "See why a claim was denied and prepare an appeal. Review any drafted appeal letter before it's sent."))
        if has("payments", "payer_intelligence"):
            cards.append(_card("Revenue", "Payments shows what's collected and outstanding; Payer Intelligence shows how each payer behaves."))
        cards.append(_card("Human review", "Every code, claim and appeal Althais prepares is reviewed by a person before it's submitted."))
    elif family == "front_desk":
        if has("schedule"):
            cards.append(_card("Scheduling", "Book, move and check in appointments from the Schedule."))
        if has("patients"):
            cards.append(_card("Patient information you'll use", "Demographics, insurance and contact details for check-in and scheduling.",
                               ["Keep patient details accurate: a wrong phone number or insurance ID causes problems later."]))
        cards.append(_card("What you won't see", "Your role doesn't include clinical notes or billing work, so those areas aren't shown to you.",
                           ["If a patient asks about results or a diagnosis, connect them with the clinical team rather than explaining it yourself."]))
    elif family == "manager":
        if has("team", "onboarding"):
            cards.append(_card("Staff and onboarding", "Add a staff member with their role; Althais builds their onboarding, checks their documents and reminds them.",
                               ["You only see what needs a human decision in Needs Attention."]))
        if has("team", "compliance", "credentials"):
            cards.append(_card("Credential monitoring", "Althais tracks expirations and renewals and flags anything expired or uncertain."))
        if has("settings", "roles", "team"):
            cards.append(_card("Permissions", "Access comes from each person's role on Staff › Roles. Change the role, not individual workarounds."))
        if has("overview"):
            cards.append(_card("Operations and analytics", "Overview and Analytics show how the clinic is running and where work is stuck."))
        cards.append(_card("Exceptions are yours", "Anything unclear, conflicting or unusual comes to you. Hiring and employment decisions are always yours."))
    elif family == "clinical":
        if has("patients", "schedule"):
            cards.append(_card("Your patient workflow", "Room patients from the schedule, update their chart, and hand off to the provider."))
        if has("scribe"):
            cards.append(_card("Documentation help", "You can draft and update notes for your part of the visit. Review what's generated before saving it."))
        cards.append(_card("Tasks and messages", "Check Tasks and the AI Inbox for work assigned to you, and close them out when done."))
        cards.append(_card("Stay in your lane", f"As a {role or 'team member'}, you only see what your role needs. Ask your manager if your work needs more."))
    if not cards:
        cards.append(_card("Your work in Althais", "Your manager will show you the parts of Althais your role uses.",
                           ["Until then, your Staff Portal is where you keep your onboarding, documents and training."]))
    return cards


def build_course(role: str, areas: set, first_name: str) -> list:
    family = _role_family(role)
    tour = [{"name": AREA_TOUR[a][0], "text": AREA_TOUR[a][1]} for a in AREA_TOUR if a in areas] + \
           [{"name": n, "text": t} for n, t in ALWAYS_TOUR]
    return [
        {"key": "welcome", "title": "Welcome To Althais", "minutes": 2, "cards": [
            _card(f"Hi {first_name or 'there'}", "Althais is the system your clinic uses for patient care, billing and running the practice.",
                  ["It keeps patient records, visit notes, claims and payments in one place.",
                   "It automates routine work — reminders, document checks, drafting — so people can focus on patients.",
                   "Althea is the assistant built into Althais. You can ask it questions or give it commands."]),
            _card("What you'll use it for", f"As a {role or 'team member'}, you'll mostly use the parts shown in the next module. "
                  "This course takes about 20 minutes and ends with a short knowledge check."),
        ]},
        {"key": "workspace", "title": "Your Althais Workspace", "minutes": 4, "cards": [
            _card("Your navigation", "These are the areas your role gives you. Tap each one to see what it's for.", kind="workspace", tour=tour),
            _card("Tasks and search", "Work assigned to you appears in Tasks and your inbox. Use Althea to find things quickly — "
                  "\"show me today's schedule\" or \"open Maria Lopez's chart\"."),
        ]},
        {"key": "privacy", "title": "Patient Information & Privacy", "minutes": 4, "cards": [
            _card("Only what you need", "Open only the records you need for the work in front of you — even when you're able to open more.",
                  ["Curiosity isn't a reason: not a neighbor's chart, a coworker's, or your own family's."]),
            _card("Your login is yours", "Never share your Althais password or let someone work under your sign-in.",
                  ["Everything done under your login is recorded as you.", "If a coworker can't sign in, they reset their password or ask your admin."]),
            _card("Keep screens private", "Lock your screen or sign out when you step away, and don't leave patient information where others can see it."),
            _card("Activity is recorded", "Althais records activity such as opening charts and changing records, for security and audits."),
            _card("Speak up", "If you see access that doesn't look right, or think your account or a device was compromised, tell your manager right away.",
                  ["Follow your clinic's privacy and security policies; your clinic may have more specific rules."]),
            _card("Quick check", kind="check", question="You open a chart and realize it's not a patient you're working with. What now?",
                  options=["Close it and carry on with your work", "Read it — you're already in it", "Mention what you saw to a coworker"],
                  answer=0, explain="Close it. If you opened it by mistake, that's fine; if you think you shouldn't have access, tell your manager."),
        ]},
        {"key": "althea", "title": "Althea", "minutes": 3, "cards": [
            _card("What Althea does", "Althea answers questions and carries out commands using the clinic information you're allowed to see.",
                  ["It can read back schedules, open charts, summarize claims and more — within your role.",
                   "It can't see anything your role can't."]),
            _card("Asking Althea", "Type or speak naturally.", kind="althea", exchanges=[
                ["What's on my schedule today?", "You have 9 appointments today. The next one is at 10:30."],
                ["Which claims are at risk of denial?", "Three claims are flagged. The biggest is missing documentation for 99214."],
                ["When does my BLS expire?", "Your BLS card on file expires June 18, 2027."]]),
            _card("When to double-check", "Althea helps you move faster, but it can be wrong or out of date.",
                  ["Check the chart or source before anything that affects patient care, billing or an official record.",
                   "Althea doesn't make clinical decisions — people do."]),
        ]},
        {"key": "role", "title": "Your Role In Althais", "minutes": 4, "cards": _role_module(family, areas, role)},
        {"key": "ai_safety", "title": "Using AI Safely", "minutes": 2, "cards": [
            _card("Althais assists; you decide", "Drafted notes, suggested codes, summaries and document checks are starting points, not final answers.",
                  ["Review AI output before it's used for patient care, billing or anything official.",
                   "If something looks wrong, correct it or flag it — don't pass it along."]),
            _card("Stay inside your permissions", "Don't try to get around what your role can see or do, and don't enter information where it doesn't belong.",
                  ["Patient details go in the chart or claim, not in personal notes, messages or unrelated fields.",
                   "If your work needs more access, ask your manager."]),
            _card("Quick check", kind="check", question="A suggested code doesn't match what's in the note. What do you do?",
                  options=["Submit it — the system suggested it", "Fix the code or the note, or flag it for review", "Delete the note"],
                  answer=1, explain="Suggestions are drafts. Make the record right before anything is submitted."),
        ]},
        {"key": "quiz", "title": "Knowledge Check", "minutes": 4, "cards": []},
        {"key": "complete", "title": "Complete", "minutes": 0, "cards": []},
    ]


# the knowledge check: practical, one clearly right answer, never trick questions
BANK = [
    {"id": "q_wrong_suggestion", "q": "You receive an Althais suggestion that looks incorrect. What should you do?",
     "options": ["Correct it or flag it, and don't use it as-is", "Accept it — the system is usually right", "Ask a coworker to approve it for you",
                 "Ignore the feature from now on"], "answer": 0,
     "concept": "AI suggestions are drafts. You review them and correct or flag anything that looks wrong."},
    {"id": "q_unneeded_info", "q": "You accidentally see patient information you don't need for your role. What should you do?",
     "options": ["Close it; if you think you shouldn't have access, tell your manager", "Finish reading it since it's already open",
                 "Take a screenshot in case it's useful later", "Tell a coworker what you saw"], "answer": 0,
     "concept": "Only use information you need for your work. Close it, and report access that doesn't look right."},
    {"id": "q_share_login", "q": "A coworker can't sign in and asks to use your Althais login for a few minutes. Should you let them?",
     "options": ["No — they should reset their password or ask the admin", "Yes, if you stay with them", "Yes, for urgent patient work",
                 "Yes, if they're in the same role"], "answer": 0,
     "concept": "Never share your login. Everything done under it is recorded as you."},
    {"id": "q_ai_review", "q": "When should an AI-generated result get human review?",
     "options": ["Before it's used for patient care, billing or anything official", "Only when Althais flags it",
                 "Never — that's the point of automation", "Only during your first month"], "answer": 0,
     "concept": "People review AI output before it's used for anything that matters."},
    {"id": "q_step_away", "q": "You need to step away with a patient chart open on your screen. What should you do?",
     "options": ["Lock your screen or sign out", "Turn the monitor slightly away", "Leave it — you'll be quick", "Minimize the window"], "answer": 0,
     "concept": "Lock your screen or sign out whenever you step away."},
    {"id": "q_althea_meds", "q": "Althea reads back a patient's medication list. How should you use it?",
     "options": ["As a quick reference, checking the chart before any clinical decision", "As the final word on what the patient takes",
                 "To update the chart without looking", "To answer the patient's questions about side effects"], "answer": 0,
     "concept": "Althea helps you move faster, but check the source before anything that affects care."},
    {"id": "q_report", "q": "You suspect someone is looking at records they don't need, or that your account was compromised. What do you do?",
     "options": ["Tell your manager or security contact right away", "Wait to see if it happens again", "Change your password and say nothing",
                 "Post about it in a team chat"], "answer": 0,
     "concept": "Report suspected inappropriate access or security problems right away."},
    {"id": "q_blocked", "q": "Althais won't let you open something because of your role, but you think you need it. What's right?",
     "options": ["Ask your manager whether your role should include it", "Ask a coworker with access to open it for you",
                 "Use a coworker's login", "Look for another way in"], "answer": 0,
     "concept": "Don't work around permissions. Access comes from your role, and your manager can change it."},
    {"id": "q_audit", "q": "Does Althais keep a record of activity like opening charts and changing records?",
     "options": ["Yes, for security and audits", "No, only billing actions are recorded", "Only for managers", "Only if a patient asks"], "answer": 0,
     "concept": "Activity is recorded for security and audit purposes."},
    {"id": "q_where_info", "q": "Where should patient information be entered?",
     "options": ["Only in the places meant for it, like the chart or the claim", "Anywhere you can find it again later",
                 "In a personal notes app for convenience", "In messages to whoever might need it"], "answer": 0,
     "concept": "Keep patient information in the places meant for it."},
    {"id": "q_renewal", "q": "Your certification was renewed. Where do you upload the new card?",
     "options": ["Staff Portal › Credentials", "Email it to a coworker", "Patient documents", "You don't need to — it renews itself"], "answer": 0,
     "concept": "Renewed credentials go in your Staff Portal, where Althais checks them and updates your record."},
    {"id": "q_phone_request", "q": "Someone calls asking for a patient's information and you can't confirm who they are. What do you do?",
     "options": ["Follow your clinic's policy for verifying identity before sharing anything", "Share basic details only",
                 "Share it if they sound like family", "Read them what Althea says"], "answer": 0,
     "concept": "Don't share patient information until you've confirmed who's asking, following clinic policy."},
]
BANK_BY_ID = {q["id"]: q for q in BANK}


# ──────────────────────────────────────────────────────────────────────────
#  Record keeping
# ──────────────────────────────────────────────────────────────────────────
def pass_score(doc: dict) -> int:
    lib = so._find(so.trainings_for(doc), COURSE_KEY) or {}
    try:
        return max(50, min(100, int(lib.get("passScore") or DEFAULT_PASS_SCORE)))
    except (TypeError, ValueError):
        return DEFAULT_PASS_SCORE


def _ctx(user, db):
    org_key, m, row, doc, p = so.portal_ctx(user, db)
    t = next((t for t in p["requirements"] if t.get("type") == "training" and t.get("trainingKey") == COURSE_KEY), None)
    if not t:
        raise HTTPException(status_code=404, detail="Althais Training isn't assigned to you.")
    return org_key, row, doc, p, t


def _areas(doc, p) -> set:
    return set(so.role_access(doc, p.get("role", ""))["areas"])


@router.get("/api/portal/course/althais")
def course(user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p, t = _ctx(user, db)
    r = so.training_record(doc, p, t)
    first = (p.get("info", {}).get("personal", {}).get("preferred") or p.get("firstName") or p.get("name", "").split(" ")[0])
    return {"version": COURSE_VERSION, "role": p.get("role", ""), "modules": build_course(p.get("role", ""), _areas(doc, p), first),
            "progress": r.get("modulesDone", []), "record": {k: r.get(k) for k in ("completed", "score", "version", "startedAt", "expires")},
            "status": so.effective_status(t), "passScore": pass_score(doc), "quizSize": QUIZ_SIZE, "name": p.get("name", "")}


@router.post("/api/portal/course/althais/progress")
async def course_progress(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p, t = _ctx(user, db)
    body = await request.json()
    key = str(body.get("module") or "")
    if key not in {"welcome", "workspace", "privacy", "althea", "role", "ai_safety"}:
        raise HTTPException(status_code=400, detail="Unknown module.")
    r = so.training_record(doc, p, t)
    done = r.setdefault("modulesDone", [])
    if key not in done:
        done.append(key)
    r.setdefault("startedAt", so._today().isoformat())
    if t.get("status") in ("NOT_STARTED", "WAITING_ON_EMPLOYEE"):
        so.set_task(p, t["key"], "IN_PROGRESS")
    so.refresh(p)
    so.save_staff(db, org_key, row, doc)
    return {"ok": True, "progress": done}


@router.post("/api/portal/course/althais/quiz")
def quiz_start(user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p, t = _ctx(user, db)
    picks = random.sample(BANK, min(QUIZ_SIZE, len(BANK)))
    asked = []
    for q in picks:
        order = list(range(len(q["options"])))
        random.shuffle(order)
        asked.append({"id": q["id"], "order": order})
    a = TrainingAttempt(org_key=org_key, person_id=p["id"], token=secrets.token_hex(12), questions=json.dumps(asked), pass_score=pass_score(doc))
    db.add(a)
    db.commit()
    return {"attempt": a.token, "passScore": a.pass_score,
            "questions": [{"id": x["id"], "q": BANK_BY_ID[x["id"]]["q"], "options": [BANK_BY_ID[x["id"]]["options"][i] for i in x["order"]]} for x in asked]}


@router.post("/api/portal/course/althais/quiz/submit")
async def quiz_submit(request: Request, user: User = Depends(require_user), db: Session = Depends(get_db)):
    org_key, row, doc, p, t = _ctx(user, db)
    body = await request.json()
    a = db.scalar(select(TrainingAttempt).where(TrainingAttempt.token == str(body.get("attempt") or ""), TrainingAttempt.org_key == org_key,
                                                TrainingAttempt.person_id == p["id"]))
    if not a or a.submitted_at:
        raise HTTPException(status_code=400, detail="Start the knowledge check again.")
    answers = body.get("answers") if isinstance(body.get("answers"), dict) else {}
    asked = json.loads(a.questions)
    right, missed = 0, []
    for x in asked:
        q = BANK_BY_ID[x["id"]]
        pick = answers.get(x["id"])
        chosen = x["order"][pick] if isinstance(pick, int) and 0 <= pick < len(x["order"]) else None
        if chosen == q["answer"]:
            right += 1
        else:
            missed.append({"q": q["q"], "concept": q["concept"], "correct": q["options"][q["answer"]]})
    score = round(100 * right / len(asked))
    a.answers, a.score, a.passed, a.submitted_at = json.dumps(answers), score, int(score >= a.pass_score), so._now()
    result = {"score": score, "passScore": a.pass_score, "passed": score >= a.pass_score, "right": right, "total": len(asked), "missed": missed}
    if score >= a.pass_score:
        r = so.training_record(doc, p, t)
        lib = so._find(so.trainings_for(doc), COURSE_KEY) or {}
        months = int(lib.get("validMonths") or 0)
        today = so._today()
        r.update(completed=today.isoformat(), score=score, version=COURSE_VERSION, startedAt=r.get("startedAt") or today.isoformat(),
                 certificate=f"Althais Training v{COURSE_VERSION} knowledge check: {score}% (attempt {a.id})",
                 expires=(today + dt.timedelta(days=round(months * 30.44))).isoformat() if months else "")
        so.set_task(p, t["key"], "COMPLETE", note="")
        so.audit(db, org_key, user, "training_completed", p, "training", r["id"], f"Completed Althais Training v{COURSE_VERSION} ({score}%)")
        so.refresh(p)
        so.save_staff(db, org_key, row, doc)
        result.update(completed=today.isoformat(), version=COURSE_VERSION, name=p.get("name", ""))
    else:
        so.audit(db, org_key, user, "training_attempt", p, "training", COURSE_KEY, f"Althais Training knowledge check attempt: {score}%")
        db.commit()
    return result
