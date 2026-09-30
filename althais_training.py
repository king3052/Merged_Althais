"""
althais_training.py — the built-in Althais Training course (Staff Portal > Training).

Seven lessons and a knowledge check (about 40 minutes), built on the real software: a chaptered demo video of a visit going
from note to transmitted claim (public/videos/althais-in-use.mp4), screen tours with numbered hotspots over real Althais
screens (static/screenshots), and hands-on practice (spot the errors, put the workflow in order, branching privacy
scenarios, a practice Althea). All of it is shaped to the person taking it: the tours, "Your Part" markers and the role
lesson only cover what their clinic role on Staff > Roles actually lets them use (role_access), so a front desk employee
never gets a physician's walkthrough.

The knowledge check is graded on the server: questions are drawn at random from the bank with their options shuffled, and
the answers never reach the browser. (The practice exercises carry their answers; they're for learning, not grading.)
Passing records the completion (course version, score, date) on the staff record and completes the "Althais Training"
onboarding item.

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
QUIZ_SIZE = 8
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
    "overview": ("Overview", "Your home base. It shows today's schedule, what needs attention, and quick actions. Start here each morning to see what's waiting for you."),
    "patients": ("Patients & Charts", "Find a patient by name, MRN or date of birth, then open their chart. A chart holds demographics, insurance, notes, labs, medications, allergies and billing history. Open only the charts you need for the work in front of you."),
    "schedule": ("Schedule", "See the day or week, then book, move, check in or cancel appointments. Each appointment links to the patient and the visit, so check-in details carry through to the note and the claim."),
    "scribe": ("Virtual SOAP (Write A Note)", "A four-step visit flow: Patient, Note, AI Coding, Review. You can type or, with the patient's consent, record the encounter. You review, correct and approve every note."),
    "code_a_note": ("Code A Note", "Suggests ICD-10 and CPT codes from a finished note, each with a confidence score. A person accepts or rejects every code before it reaches a claim."),
    "coding_review": ("Coding Review", "Review coding across visits. It flags unspecified diagnoses, level-of-service mismatches and missing modifiers before claims go out."),
    "claims": ("Claims", "Build, check and submit claims, then follow each one. Claims move through Ready To Submit, Submitted, Paid, Denied and Appealed, with a risk rating and an AI confidence score."),
    "denials": ("Denials", "Denied claims with the payer's reason code, what it means and the recommended next step: correct and resubmit, or appeal."),
    "appeals": ("Appeals", "Prepare and track appeals. Althais can draft the letter from the chart and the denial; a person reviews it before it's sent."),
    "payments": ("Payments", "What's been collected, what's outstanding and from whom. Includes approval rate and net collections."),
    "payer_intelligence": ("Payer Intelligence", "Each payer's rules and policy changes (prior authorizations, modifiers, bundling) plus approval rates and denial patterns. Check it before you submit an unfamiliar service."),
    "team": ("Staff", "Your clinic's team: records, onboarding, credentials, training and roles."),
    "compliance": ("Compliance", "Expiring credentials, overdue training and other staff compliance items, soonest first."),
    "settings": ("Settings", "Clinic-wide settings, including colors and theme. Changes here affect everyone, so only change what you're responsible for."),
    "althea": ("Althea", "The assistant in the bottom-right corner. Type or speak a question or a command. It only uses what your role can see."),
}
ALWAYS_TOUR = [
    ("Staff Portal", "Your own onboarding, documents, credentials and training. Open it from your name in the top-right corner. This is where you upload a renewed license or card, and Althais checks it within seconds."),
    ("Notifications", "Althais emails you before deadlines and renewals (90, 60, 30 and 7 days before a credential expires) and whenever something needs your attention."),
]

SHOT = "/static/screenshots/"
VIDEO = "/videos/althais-in-use.mp4"
DEMO_NOTE = "Demo data. These aren't real patients."

# The demo video (34s, no audio). Each chapter's text is shown as a caption while it plays. `areas` marks whose work it is.
VIDEO_CHAPTERS = [
    (0, "The EMR workspace", "The EMR opens on the patient in context (Smith, John), with Claims, Payer Intelligence and Denials as tabs, "
        "the Risk Panel on the right and Althea in the corner.", {"patients", "overview"}),
    (1.5, "Start the visit", "Virtual SOAP step 1 (Patient): the patient, date, provider, visit type and place of service. "
        "Place of service matters later: it has to agree with the codes on the claim.", {"scribe", "schedule"}),
    (3, "Document the encounter", "Step 2 (Note): chief complaint, HPI, vitals, history, medications, allergies, exam, assessment and plan. "
        "Recording is optional and stays locked until the provider confirms the patient agreed.", {"scribe"}),
    (14, "AI-suggested codes", "Step 3 (AI Coding): Althais reads the note and suggests ICD-10 and CPT codes with a confidence score. "
         "Each code is accepted or rejected by a person. Here every code is accepted one at a time.", {"scribe", "code_a_note", "coding_review"}),
    (18, "Review and attest", "Step 4 (Review): a summary of the note and accepted codes. The provider checks \"I have reviewed and approve "
         "this note and codes\". A claim can't be created without it.", {"scribe", "code_a_note"}),
    (20.4, "The claim is built", "Althais fills in a CMS-1500 from the note, the codes and the patient's insurance: patient and insured "
           "details, diagnoses in Box 21 and service lines in Box 24.", {"claims"}),
    (22.6, "Transmitted to the payer", "The claim is sent electronically as an 837P transaction through the clearinghouse, "
           "and Althais waits for the payer's acknowledgment.", {"claims"}),
    (31.5, "Tracked in Claims", "Back in Claims, the new claim appears with its status, risk and AI confidence. From here it's followed "
           "until it's paid, or worked as a denial.", {"claims", "denials", "payments"}),
]

# Screen tours: numbered hotspots over real Althais screens (x, y are % of the image). A spot's `areas` limits it to roles that
# can open that part of Althais; `tags` lets a role module pick a subset.
EMR_TOUR = [
    (16, 2.2, "Apps", "Overview, EMR, Revenue and Staff. You only see the apps your role opens on Staff › Roles.", None),
    (26, 7.2, "The patient in context", "Whose chart you're in: name, MRN, sex, age and date of birth. Check it before you change anything. "
        "Switch Patient changes the chart everything on the page applies to.", {"patients"}),
    (55, 7.2, "Search (⌘K)", "Search patients, claims and codes from anywhere. Results only include what your role can open.", None),
    (78, 7.2, "Scheduler", "The day's appointments. Checking a patient in here starts the visit.", {"schedule"}),
    (90.6, 7.2, "Virtual SOAP", "Starts a visit note: Patient, Note, AI Coding, Review.", {"scribe"}),
    (16.5, 12, "Workspace tabs", "Patients, Claims, Payer Intelligence, Denials, Documents and Settings. The red and grey badges count what "
        "needs attention: 4 denials here.", None),
    (86, 12, "EHR sync", "When data last synced from the EHR (12 seconds ago here). If it's been a long time, the chart may not be current.", None),
    (7.6, 34.5, "The patient record", "Demographics, insurance, clinical notes, the revenue cycle (claims, prior auth, appeals), clinical data "
        "(labs, medications, allergies) and workflow (documents, referrals, care team).", {"patients"}),
    (64, 23, "Risk and AI confidence", "Every claim has a risk rating (Low, Medium, High) and the AI's confidence in its coding. "
        "High risk or low confidence means look harder before submitting.", {"claims", "coding_review", "denials"}),
    (82.6, 16.4, "Risk Panel", "Warnings for the open patient. Here, an ICD Specificity Warning: E11.9 may have a more specific code. "
        "Fix the cause in the note or the code, not just the flag.", {"claims", "coding_review", "code_a_note"}),
    (85, 31, "Payer updates", "Recent payer policy changes, e.g. new documentation criteria for 99214 or a prior authorization rule "
        "for MRIs. View all opens Payer Intelligence.", {"payer_intelligence", "claims"}),
    (96.4, 94, "Althea", "Ask a question or give a command by typing or with the mic. The small timer above it shows a recording in progress.", None),
]
NOTE_TOUR = [
    (18.5, 2.6, "Four steps", "Patient → Note → AI Coding → Review. You can go back to any step until the claim is created."),
    (2.8, 12.9, "Recording consent", "Recording is optional. Start Recording stays locked until you confirm the patient was told and verbally "
        "agreed. Never tick it without asking."),
    (5, 26.6, "Chief complaint", "Why the patient came in, in their words. It frames the HPI, the assessment and ultimately the codes."),
    (82, 30.8, "Vitals", "BP 148/92, HR 88, SpO2 97. Check values and units. A typo (1482 instead of 148) becomes part of the record."),
    (78, 58, "Allergies", "Penicillin (rash). Confirm allergies at every visit. An allergy dropped from a note is a patient-safety error."),
    (4.3, 80, "Assessment", "Your clinical reasoning. Codes are suggested from what's written here, so a vague assessment leads to "
        "unspecified codes and a specific one supports the level of service."),
    (91, 90.4, "Generate AI Code", "Sends the note to AI coding. It suggests codes; it doesn't decide anything."),
]
CODING_TOUR = [
    (7.8, 15, "A diagnosis code", "ICD-10 R07.9, Chest pain, unspecified. Unspecified codes are allowed, but when the note supports "
        "something more specific, payers expect it."),
    (47.2, 14.7, "Confidence", "90%: how strongly the AI thinks the code fits the note. It is not a guarantee. A 95% suggestion can still "
        "be wrong."),
    (6.7, 18.7, "Accept or Reject", "Every code needs a person's decision. Nothing goes on the claim until it's accepted."),
    (7, 34.7, "Level of service", "CPT 99285, Emergency department visit, high complexity. The code must match where the visit happened and "
        "how complex it was. An ED code for an office visit is wrong, whatever the confidence."),
    (7, 45, "Add your own code", "When something documented was missed, add the code yourself with a short description."),
    (44, 9.2, "Re-run AI Coding", "After you edit the note, run coding again so the suggestions match the final note."),
    (42.6, 60.3, "Review & Create Claim", "Goes to Review, where the provider attests to the note and codes before a claim is built."),
]
CLAIM_TOUR = [
    (78.7, 14.5, "Box 1a · Insured's ID", "Comes from the insurance entered at check-in. One wrong character and the payer rejects the claim.", "demo"),
    (7.4, 18.5, "Box 2 · Patient's name", "Last, First, exactly as on the insurance card. \"Jon\" instead of \"John\" is enough for a rejection.", "demo"),
    (7.4, 26, "Box 5 · Address and phone", "From registration. Outdated addresses send statements and payer letters to the wrong place.", "demo"),
    (6.5, 62, "Box 21 · Diagnoses", "The ICD-10 codes from the note, lettered A to L. They must be supported by the documentation.", "billing"),
    (62.6, 72, "Box 24 · Service lines", "Date of service, place of service (11 is the office), CPT code, modifier, charges and units.", "billing"),
    (79.4, 72, "Dx pointer", "Links each service line to the diagnoses in Box 21 that justify it. A wrong pointer is a common cause of "
        "medical-necessity denials.", "billing"),
    (5.6, 96, "Box 31 · Signature", "The rendering provider certifies the claim. That's why the Review attestation comes first.", "billing"),
    (78.7, 96.6, "Box 33 · Billing provider NPI", "The clinic's billing NPI. It has to match what the payer has on file.", "billing"),
    (94, 2.4, "Next", "Transmits the claim to the payer as an 837P and starts tracking it.", "billing"),
]
TRACK_TOUR = [
    (43.6, 2.9, "Status filters", "All, Pending, Denied, Paid and Appeals. Start each day with Denied and Pending."),
    (70, 2.9, "Filter", "Find claims by patient, payer or code."),
    (43, 35, "A denied claim", "Johnson, Maria: BlueCross BlueShield, $312. Open it for the denial reason; Denials shows the next step."),
    (75, 35, "Risk", "High: this claim was likely to have problems before it went out. Risk comes from payer rules, history and coding."),
    (83, 35, "AI confidence", "72%, lower than the others. Low confidence is a prompt to check the coding against the note."),
    (43, 86.6, "Ready to Submit", "Built and checked, waiting to be sent. Review it before you submit."),
]
PAYER_TOUR = [
    (37.6, 20, "Approval rate", "Across tracked payers. A low rate means denials to work and patterns to learn from."),
    (64, 58, "A payer rule", "UnitedHealthcare: E/M the same day as a procedure needs modifier 25. Rules like this prevent avoidable denials."),
    (85, 59, "Impact", "High-impact rules are the ones most likely to cause a denial if missed."),
    (86, 30, "Filter", "Filter rules by payer, CPT code or specialty before you bill something unfamiliar."),
    (93, 5.5, "Add payer note", "Learned something from a payer call? Add it here so the whole team benefits."),
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


def _shot(title, body, image, w, h, spots, caption=DEMO_NOTE):
    return _card(title, body, kind="hotspots", image=SHOT + image, w=w, h=h, caption=caption,
                 spots=[{"x": s[0], "y": s[1], "title": s[2], "text": s[3]} for s in spots])


def _field(label, value, issue=None, ok=""):
    return {"label": label, "value": value, "issue": issue or "", "ok": ok}


def _spot(title, body, doc_title, fields):
    return _card(title, body, kind="spot", doc=doc_title, fields=fields)


NOTE_DRAFT = _spot("Find the problems in this AI draft", "This note was drafted from a recording. The chart says the patient denies prior cardiac "
                   "history and is allergic to penicillin. Tap every line that's wrong. Some are fine.", "Draft visit note · Smith, John", [
    _field("Chief complaint", "Chest pain and shortness of breath for 2 days", ok="Matches what the patient said."),
    _field("HPI", "58-year-old with substernal chest pressure radiating to the left arm. History of MI in 2019.",
           issue="The patient denied prior cardiac history. Drafts can include things that were never said. Remove it before you sign."),
    _field("Vitals", "BP 148/92 · HR 88 · RR 18 · SpO2 97%", ok="Plausible and matches the vitals taken."),
    _field("Allergies", "No known drug allergies",
           issue="The chart lists Penicillin (rash). An allergy dropped from a note is a patient-safety error."),
    _field("Medications", "Metformin 500mg BID, Lisinopril 10mg daily", ok="Matches the medication list."),
    _field("Plan", "Serial troponins, EKG monitoring, cardiology consult, aspirin 81mg", ok="What the provider documented."),
])
CLAIM_DRAFT = _spot("Find the problems on this claim", "An office visit (place of service 11). Tap every line that would get this claim "
                    "rejected or denied.", "CMS-1500 · Smith, John · 06/26/2026", [
    _field("Box 1a · Insured's ID", "UHC-2026-4471", ok="Matches the insurance card on file."),
    _field("Box 2 · Patient's name", "Smith, John", ok="Last, First, as on the card."),
    _field("Box 21 · Diagnoses", "A. R07.9 Chest pain, unspecified  B. I10 Hypertension", ok="Both are documented in the note."),
    _field("Box 24 · Line 1", "99285 · POS 11 · $142.50",
           issue="99285 is an emergency department code, but place of service 11 is the office. The code and the setting have to agree."),
    _field("Box 24 · Line 1 Dx pointer", "D",
           issue="Box 21 only has A and B. Every line must point to a diagnosis that's actually on the claim."),
    _field("Box 33 · Billing NPI", "1234567890", ok="The clinic's billing NPI."),
])
CHECKIN_DRAFT = _spot("Find the problems at check-in", "The patient's insurance card reads SMITH, JOHN · Member ID UHC4471098 · valid through "
                      "12/31/2026. Tap every registration detail that's wrong.", "Registration · Smith, John", [
    _field("Name", "Smith, Jon", issue="The card says John. The name has to match the insurance exactly, or the claim is rejected."),
    _field("Date of birth", "04/12/1968", ok="Matches the patient's ID."),
    _field("Member ID", "UHC4471089", issue="Two digits are swapped (…098 on the card). Enter it exactly; this goes straight into Box 1a."),
    _field("Phone", "(617) 555-0199", ok="Confirmed with the patient today."),
    _field("Coverage dates", "Through 12/31/2026", ok="Matches the card."),
])
ACCESS_REVIEW = _spot("Find the risks in this access review", "Your quarterly look at who can open what. Tap every line that needs action.",
                      "Access Review · Q3", [
    _field("Dr. R. Patel · Physician", "Charts, Virtual SOAP, Code A Note", ok="What the Physician role needs."),
    _field("Billing team", "One shared login, \"billing@\"",
           issue="Shared logins make it impossible to tell who did what. Everyone needs their own account."),
    _field("Dana · Front Desk", "Claims access added \"just for this week\" in March",
           issue="Workarounds become permanent. If the job changed, change the role on Staff › Roles; otherwise remove it."),
    _field("Former MA (left in August)", "Status: Active",
           issue="Offboard people on their last day. Offboarding in Althais removes their access."),
    _field("New hire", "Invited, onboarding in progress", ok="Access starts when you activate them."),
])
WORKSTATION = _spot("Spot the privacy risks", "You walk past a shared workstation at the front desk. Tap everything that's a problem.",
                    "Front desk workstation", [
    _field("Screen", "A patient's chart is open; nobody is at the desk", issue="Lock the screen (or sign out) whenever you step away."),
    _field("Monitor", "A sticky note: \"althais pw: Clinic2026!\"", issue="Passwords are never written down or shared. Report it so it's changed."),
    _field("Printer tray", "Empty. Today's schedule was picked up", ok="Printed patient information was collected."),
    _field("Who's signed in", "Each person's own account", ok="Everyone works under their own login."),
    _field("Phone call", "\"I'm her daughter, what time is her appointment?\" Answered right away",
           issue="Verify who's calling (per clinic policy) before sharing anything, even an appointment time."),
])
CONFIDENCE_DRAFT = _spot("Confidence isn't correctness", "The visit was in the office (place of service 11) and the note documents chest pain, "
                         "shortness of breath and a penicillin allergy. Tap every suggestion you would reject.", "AI-Suggested Codes", [
    _field("ICD-10 R07.9 · 90%", "Chest pain, unspecified", ok="Supported by the note."),
    _field("ICD-10 R06.02 · 85%", "Shortness of breath", ok="Supported by the note."),
    _field("CPT 99285 · 95%", "Emergency department visit, high complexity",
           issue="The highest confidence here, and still wrong: this was an office visit. Confidence says how sure the AI is, not that it's right."),
    _field("ICD-10 Z88.0 · 61%", "Allergy status to penicillin", ok="Lower confidence, but documented in the note, so it's supported."),
    _field("ICD-10 I21.9 · 58%", "Acute myocardial infarction",
           issue="The note says rule out ACS, not a diagnosed heart attack. Don't code a diagnosis that wasn't made."),
])

WORKFLOW_STEPS = [
    ("checkin", "Check in the patient and verify insurance", {"schedule", "patients"}),
    ("note", "Document the visit in Virtual SOAP", {"scribe"}),
    ("codes", "Review the AI-suggested codes", {"scribe", "code_a_note", "coding_review"}),
    ("attest", "Attest: review and approve the note and codes", {"scribe"}),
    ("claim", "Build the CMS-1500 claim", {"claims"}),
    ("send", "Transmit to the payer (837P)", {"claims"}),
    ("track", "Track status, work denials, post payment", {"claims", "denials", "appeals", "payments"}),
]

ALTHEA_SIM = [
    # (question, keywords, answer, areas that must be open or None)
    ("What's on my schedule today?", ["schedule", "today", "appointment"], "You have 9 appointments today. Next: Smith, John at 10:30 "
     "(follow-up, Dr. R. Patel). Want me to open the Scheduler?", {"schedule"}),
    ("Open John Smith's chart", ["open", "chart", "smith"], "Opening Smith, John (JS10023, DOB 04/12/1968). Two people named John Smith are in "
     "the system, so I matched the date of birth. Check it's the right patient.", {"patients"}),
    ("Which claims are at risk of denial?", ["risk", "at risk", "flag"], "3 claims are flagged High risk. The largest is Johnson, Maria "
     "($312, BCBS): I48.91 needs supporting documentation. Open Claims filtered to High risk?", {"claims", "coding_review"}),
    ("Why was Maria Johnson's claim denied?", ["denied", "denial", "why"], "BCBS denied CL-2026-003 with CO-50 (not medically necessary). "
     "The note doesn't document why the ECG was needed. Next step: add the documentation and resubmit, or appeal.", {"denials", "claims"}),
    ("Does UnitedHealthcare need modifier 25?", ["modifier", "payer", "united", "uhc"], "Yes. UnitedHealthcare requires modifier 25 on "
     "the E/M code (99213–99215) when billed the same day as a procedure (CMS Modifier Guidelines, effective 2025-01-01).", {"payer_intelligence", "claims"}),
    ("When does my BLS expire?", ["bls", "expire", "license", "credential", "cpr"], "Your BLS card on file expires June 18, 2027. "
     "I'll remind you 90, 60, 30 and 7 days before.", None),
    ("What do I still need to do?", ["need to do", "still need", "remaining", "onboarding", "todo", "to do"], "You have 2 items left: "
     "upload your TB test result and finish Althais Training (this course).", None),
    ("Should this patient get aspirin?", ["should", "aspirin", "dose", "prescribe", "diagnos", "treat", "give"],
     "I can't make clinical decisions. I can show what's documented (aspirin 81mg is in Dr. Patel's plan), but whether to give it "
     "is the provider's call.", None),
    ("How much does my coworker make?", ["coworker", "salary", "pay", "make", "someone else", "ssn"],
     "I can't share that. I only use information your role is allowed to see, and other people's personal details aren't part of it.", None),
]


def _tour(spots, areas):
    return [s[:4] for s in spots if s[4] is None or s[4] & areas]


def _role_module(family: str, areas: set, role: str) -> list:
    has = lambda *a: any(x in areas for x in a)
    cards = []
    if family in ("physician", "clinical"):
        if family == "physician":
            cards.append(_card("Your day in Althais", "A typical visit: open the patient from the Scheduler or search, start Virtual SOAP, "
                               "document, review the AI-suggested codes and attest. Billing takes it from there.",
                               ["The chart brings allergies, medications, labs and history into one place, so check them before you document.",
                                "Everything you open or change is part of the chart's audit trail.",
                                "You're responsible for what you sign, not for what the draft meant to say."]))
        else:
            cards.append(_card("Your part of the visit", "You room the patient, confirm allergies and medications, take vitals, and hand off "
                               "to the provider. Your entries flow into the provider's note.",
                               ["Vitals and allergies you enter are what the provider sees. Double-check them.",
                                "Use Tasks and the AI Inbox for work assigned to you, and close items out when done.",
                                f"As a {role or 'team member'}, you only see what your role needs. Ask your manager if your work needs more."]))
        if has("scribe"):
            cards.append(_shot("Tour: the Note step", "This is Virtual SOAP's Note step, the screen where the visit is documented. "
                               "Tap each number.", "provider-note.png", 1408, 868, NOTE_TOUR))
        if has("scribe", "code_a_note", "coding_review") and family == "physician":
            cards.append(_shot("Tour: AI Coding", "After Generate AI Code, Althais suggests codes from the note. This is where you accept, "
                               "reject or add codes.", "ai-coding.png", 1408, 868, CODING_TOUR))
        if has("scribe", "patients"):
            cards.append(NOTE_DRAFT)
        cards.append(_card("What only people approve", "Althais prepares work. Clinical decisions and signatures stay with people.",
                           ["Diagnoses, orders and treatment decisions are never made by Althais or Althea.",
                            "The Review attestation (\"I have reviewed and approve this note and codes\") means you did review them.",
                            "If the draft and your memory disagree, the chart must reflect what actually happened."]))
    elif family == "biller":
        cards.append(_card("Your day in Althais", "Start in Claims with the Denied and Pending filters, then work the Risk Panel's "
                           "warnings, then submit what's Ready To Submit.",
                           ["Codes come from a note the provider has attested. If a code isn't supported by the note, send it back; "
                            "don't bill it.", "Every claim, code change and appeal is recorded under your login."]))
        if has("code_a_note", "coding_review"):
            cards.append(_shot("Tour: AI Coding", "The provider's coding step. As a biller you'll see the same codes and confidence scores in "
                               "Coding Review.", "ai-coding.png", 1408, 868, CODING_TOUR))
        if has("claims"):
            cards.append(_shot("Tour: the CMS-1500 claim", "Althais builds this from the note, the codes and the patient's insurance. "
                               "Tap each box.", "claim-assembly.png", 1080, 1311, [s[:4] for s in CLAIM_TOUR]))
            cards.append(_shot("Tour: Claims Overview", "Where every claim is followed after it's sent.", "claim-tracking.png", 894, 792, TRACK_TOUR))
        if has("payer_intelligence"):
            cards.append(_shot("Tour: Payer Intelligence", "Payer rules and policy changes, and how each payer is performing.",
                               "payer-intelligence.png", 1214, 792, PAYER_TOUR))
        cards.append(CLAIM_DRAFT)
        if has("denials", "appeals"):
            cards.append(_card("Denials and appeals", "Denials lists each denied claim with the payer's reason code in plain language and the "
                               "next step.", ["Correct and resubmit when the claim had an error.",
                                              "Appeal when the claim was right. Althais can draft the letter from the chart; you review and edit it before it's sent.",
                                              "Add what you learn to Payer Intelligence so the same denial doesn't repeat."]))
    elif family == "front_desk":
        cards.append(_card("Why check-in matters so much", "What you enter at check-in (name, date of birth, insurance, address) is copied "
                           "straight onto the claim. Most claim rejections start with a registration error.",
                           ["Scan or check the insurance card at every visit. Coverage changes.",
                            "Enter names exactly as on the card, and member IDs character by character.",
                            "Confirm the phone number and address with the patient."]))
        cards.append(_shot("Where your details end up", "This is the claim Althais builds after the visit. The boxes numbered here come "
                           "from check-in.", "claim-assembly.png", 1080, 1311, [s[:4] for s in CLAIM_TOUR if s[4] == "demo"]))
        cards.append(CHECKIN_DRAFT)
        cards.append(_card("What you won't see", "Your role doesn't include clinical notes or billing work, so those areas aren't shown to you.",
                           ["If a patient asks about results or a diagnosis, connect them with the clinical team rather than explaining it yourself.",
                            "If a caller asks for patient information, verify who they are first, following your clinic's policy."]))
    elif family == "manager":
        cards.append(_card("Your day in Althais", "Start with Staff › Needs Attention. Althais handles the routine work (checking documents, "
                           "reminders, renewals, training) and only sends you what needs a decision.",
                           ["Overview and Analytics show how the clinic is running and where work is stuck.",
                            "Hiring, employment and access decisions are always yours."]))
        cards.append(_card("Your staff tools", "Tap each one to see what it's for.", kind="workspace", tour=[
            {"name": "Staff Onboarding", "text": "Add someone with their role and Althais builds their onboarding: forms, documents, training "
                                                 "and your manager tasks. It invites them with a temporary password."},
            {"name": "Needs Attention", "text": "Only what needs a person: uncertain or conflicting documents, people ready to activate, "
                                                "offboarding tasks and overdue items."},
            {"name": "Staff › Roles", "text": "Each role's permissions decide what a person can open. Change the role, not individual workarounds."},
            {"name": "Onboarding Templates", "text": "What each role must complete, the forms and training, document automation settings "
                                                     "and reminder schedules."},
            {"name": "Document automation", "text": "Clean documents are approved automatically; anything unclear comes to you. You can "
                                                    "turn automated review off at any time."}]))
        cards.append(ACCESS_REVIEW)
        cards.append(_card("Offboarding is a security step", "When someone leaves, offboard them in Althais on their last day. It removes "
                           "their access and lists the tasks to close out (equipment, accounts, final documents)."))
    if family == "general" or not cards:
        cards.append(_card("Your work in Althais", "Your manager will show you the parts of Althais your role uses.",
                           ["Until then, your Staff Portal is where you keep your onboarding, documents and training.",
                            "If you think your role needs something you can't open, ask your manager rather than working around it."]))
        cards.append(CHECKIN_DRAFT if has("patients", "schedule") else CONFIDENCE_DRAFT)
    return cards


def build_course(role: str, areas: set, first_name: str) -> list:
    family = _role_family(role)
    tour = [{"name": AREA_TOUR[a][0], "text": AREA_TOUR[a][1]} for a in AREA_TOUR if a in areas] + \
           [{"name": n, "text": t} for n, t in ALWAYS_TOUR]
    chapters = [{"t": t, "title": ti, "text": tx, "yours": bool(a & areas)} for t, ti, tx, a in VIDEO_CHAPTERS]
    steps = [{"key": k, "label": l, "yours": bool(a & areas)} for k, l, a in WORKFLOW_STEPS]
    yours = [s["label"] for s in steps if s["yours"]]
    sim = [{"q": q, "keys": k, "a": a} for q, k, a, ar in ALTHEA_SIM if ar is None or ar & areas]
    emr = _tour(EMR_TOUR, areas)
    return [
        {"key": "welcome", "title": "Welcome To Althais", "minutes": 4, "cards": [
            _card(f"Hi {first_name or 'there'}", "Althais is the system your clinic uses for patient care, billing and running the practice. "
                  "This course shows you the real software, shaped to your role.",
                  ["It keeps patient records, visit notes, claims and payments in one place.",
                   "It automates routine work (drafting notes, suggesting codes, checking documents, reminders) so people can focus on patients.",
                   "Althea is the assistant built into Althais. You can type or talk to it."]),
            _card("How this course works", f"Seven short lessons and a knowledge check. You'll watch Althais in use, tour its real screens, practice with "
                  f"hands-on exercises and finish with a knowledge check. As a {role or 'team member'}, you'll see the parts of Althais "
                  "your role uses. Your progress saves as you go.", kind="recap",
                  bullets=["Watch: a visit from note to paid claim", "Tour: real Althais screens, one hotspot at a time",
                           "Practice: find the errors, put the steps in order, talk to Althea", "Check: a short knowledge check"]),
            _card("Watch: a visit, start to finish", "A real run through Althais: one visit, documented, coded, turned into a claim and sent "
                  "to the payer. Chapters marked Your Part involve your role. Tap a chapter to jump to it. Watch it through, or open "
                  "every chapter, to continue.", kind="video", src=VIDEO, chapters=chapters, caption=DEMO_NOTE),
        ]},
        {"key": "workspace", "title": "Your Althais Workspace", "minutes": 6, "cards": [
            _shot("Tour: the EMR", "This is the main Althais workspace. Tap each number to learn what it is. Only the parts your role "
                  "uses are marked.", "payment.png", 1440, 900, emr),
            _card("Your navigation", "These are the areas your role opens. Tap each one to see what it's for.", kind="workspace", tour=tour),
            _card("Finding things fast", "Three ways to get somewhere:",
                  ["Search (⌘K on a Mac, Ctrl+K on Windows) finds patients, claims and codes from anywhere.",
                   "Althea: \"open John Smith's chart\" or \"show today's schedule\".",
                   "Your name in the top-right corner: My Profile and What To Do Today, from any page."]),
        ]},
        {"key": "workflow", "title": "From Visit To Paid Claim", "minutes": 5, "cards": [
            _card("One visit, many hands", "Every visit follows the same path through Althais. Each step feeds the next, so a mistake early "
                  "on (a mistyped member ID, a dropped allergy, a wrong code) travels all the way to the claim.",
                  [f"Your part: {', '.join(yours).lower()}." if yours else
                   "Your role isn't directly in this path, but it helps to know how the clinic's work fits together."]),
            _card("Put the visit in order", "Tap the steps in the order they happen. Steps marked Your Part involve your role.",
                  kind="sequence", steps=steps),
            _card("Why the order matters", "Some steps can't happen until the one before is done, on purpose:",
                  ["Codes are suggested from the finished note, so the note comes first.",
                   "A claim can't be created until the provider attests to the note and codes.",
                   "Claims are tracked after they're sent. Denials come back into Althais with a reason and a next step."], kind="recap"),
        ]},
        {"key": "privacy", "title": "Patient Information & Privacy", "minutes": 6, "cards": [
            _card("Only what you need", "Open only the records you need for the work in front of you, even when your role lets you open more. "
                  "This is the \"minimum necessary\" rule, and it applies to everyone, including managers and providers.",
                  ["Curiosity isn't a reason: not a neighbor's chart, a coworker's, a celebrity's, or your own family's.",
                   "Looking without a work reason is a privacy violation even if you never tell anyone what you saw."]),
            _card("Your login is yours", "Never share your Althais password or let someone work under your sign-in.",
                  ["Everything done under your login is recorded as you: every chart opened, every change, every claim.",
                   "If a coworker can't sign in, they reset their password or ask your admin.",
                   "Althais will never email you a link asking for your password. Treat those emails as phishing."]),
            _card("Activity is recorded", "Althais records activity such as opening charts, changing records and submitting claims, for "
                  "security and audits. Privacy officers review it, and patients can ask who looked at their record."),
            WORKSTATION,
            _card("What would you do?", kind="scenario", steps=[
                {"situation": "A patient checks in and you recognize her: she's your neighbor. Later you wonder why she came in.",
                 "options": ["Leave it. You have no work reason to open her chart", "Take a quick look, since your role can open charts",
                             "Ask Althea to summarize her visit"], "answer": 0,
                 "explain": "Having access isn't a reason. Asking Althea is the same as opening the chart yourself."},
                {"situation": "A coworker asks you to look up when their ex-partner's next appointment is.",
                 "options": ["Decline, and explain you can only open records for your work", "Look it up but only tell them the date",
                             "Tell them to ask Althea"], "answer": 0,
                 "explain": "Even an appointment date is protected information. If they keep asking, tell your manager."},
                {"situation": "An email says \"Your Althais password expires today, sign in here\" with an unfamiliar link.",
                 "options": ["Don't click. Report it to your manager", "Sign in quickly so you aren't locked out", "Forward it to coworkers as a warning"],
                 "answer": 0, "explain": "It's phishing. Althais never asks for your password by email. Reporting it protects everyone."},
                {"situation": "You come back to your desk and find your screen unlocked with a chart open. You were gone 10 minutes.",
                 "options": ["Lock it now, and tell your manager if you think someone viewed it", "Probably nothing happened, so carry on",
                             "Close the chart and forget about it"], "answer": 0,
                 "explain": "Reporting a possible exposure is never wrong. From now on, lock your screen every time you step away."},
            ]),
            _card("Speak up", "If you see access that doesn't look right, or think your account or a device was compromised, tell your manager "
                  "right away. Reporting quickly limits the damage.",
                  ["Follow your clinic's privacy and security policies; your clinic may have more specific rules."]),
        ]},
        {"key": "althea", "title": "Working With Althea", "minutes": 4, "cards": [
            _card("What Althea does", "Althea answers questions and carries out commands using the clinic information you're allowed to see.",
                  ["It can read back schedules, open charts, summarize claims, explain denials and look up payer rules, within your role.",
                   "It can't see anything your role can't, and it won't share other people's personal details.",
                   "Tap the A button in the bottom-right corner. Type, or tap the mic and talk. It keeps listening until you tap again."]),
            _card("Try it: talk to Althea", "This is a practice Althea with sample answers, shaped to your role. Ask at least three questions. "
                  "Try one it should refuse.", kind="althea_sim", prompts=sim,
                  fallback="In the real Althais I'd look that up using only what your role can see. For this practice, try one of the suggestions."),
            _card("When to double-check", "Althea helps you move faster, but it can be wrong, out of date or match the wrong record.",
                  ["Check the chart or source before anything that affects patient care, billing or an official record.",
                   "When it opens a patient, confirm the name and date of birth.",
                   "Althea doesn't make clinical decisions. People do."]),
        ]},
        {"key": "role", "title": "Your Role In Althais", "minutes": 8, "cards": _role_module(family, areas, role)},
        {"key": "ai_safety", "title": "Using AI Safely", "minutes": 4, "cards": [
            _card("Althais assists; you decide", "Drafted notes, suggested codes, summaries, appeal letters and document checks are starting "
                  "points, not final answers.",
                  ["Review AI output before it's used for patient care, billing or anything official.",
                   "AI can add things that weren't said, leave out things that were, or pick a plausible but wrong code.",
                   "If something looks wrong, correct it or flag it. Don't pass it along."]),
            CONFIDENCE_DRAFT,
            _card("Stay inside your permissions", "Don't try to get around what your role can see or do, and don't enter information where "
                  "it doesn't belong.",
                  ["Patient details go in the chart or claim, not in personal notes, messages or unrelated fields.",
                   "Don't paste patient information into outside AI tools or websites.",
                   "If your work needs more access, ask your manager."]),
            _card("Key takeaways", kind="recap", bullets=[
                "Open only what you need, and lock your screen when you step away.",
                "Your login is yours alone. Everything under it is recorded as you.",
                "AI output is a draft. Confidence isn't correctness.",
                "A claim follows a reviewed, attested note. Errors early travel all the way to the payer.",
                "Report anything that doesn't look right, right away."]),
        ]},
        {"key": "quiz", "title": "Knowledge Check", "minutes": 5, "cards": []},
        {"key": "complete", "title": "Complete", "minutes": 0, "cards": []},
    ]


LESSON_KEYS = ["welcome", "workspace", "workflow", "privacy", "althea", "role", "ai_safety"]


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
                 "In a personal notes app for convenience", "In an outside AI tool to summarize it"], "answer": 0,
     "concept": "Keep patient information in the places meant for it, and never paste it into outside tools."},
    {"id": "q_renewal", "q": "Your certification was renewed. Where do you upload the new card?",
     "options": ["Staff Portal › Credentials", "Email it to a coworker", "Patient documents", "You don't need to — it renews itself"], "answer": 0,
     "concept": "Renewed credentials go in your Staff Portal, where Althais checks them and updates your record."},
    {"id": "q_phone_request", "q": "Someone calls asking for a patient's information and you can't confirm who they are. What do you do?",
     "options": ["Follow your clinic's policy for verifying identity before sharing anything", "Share basic details only",
                 "Share it if they sound like family", "Read them what Althea says"], "answer": 0,
     "concept": "Don't share patient information until you've confirmed who's asking, following clinic policy."},
    {"id": "q_consent", "q": "Before a visit is recorded to help with documentation, what has to happen?",
     "options": ["The patient is told and verbally agrees, and the provider confirms it in Virtual SOAP", "Nothing — recording is automatic",
                 "The patient signs a form after the visit", "The manager approves it"], "answer": 0,
     "concept": "Recording stays locked until the provider confirms the patient was told and agreed."},
    {"id": "q_confidence", "q": "An AI-suggested code shows 95% confidence. What does that mean?",
     "options": ["The AI thinks it fits the note; a person still checks it against the documentation", "It's guaranteed to be correct",
                 "The payer has already approved it", "It can skip review"], "answer": 0,
     "concept": "Confidence is how sure the AI is, not proof it's right. A 95% code can still be wrong."},
    {"id": "q_attest", "q": "In Virtual SOAP's Review step, what does checking \"I have reviewed and approve this note and codes\" mean?",
     "options": ["The provider personally reviewed them and stands behind them", "The AI has reviewed them",
                 "A manager will check them later", "It's a formality to get to the claim"], "answer": 0,
     "concept": "The attestation is a person's review. A claim can't be created without it."},
    {"id": "q_draft_added", "q": "An AI-drafted note includes history the patient never mentioned. What should happen?",
     "options": ["Remove or correct it before the note is signed", "Leave it — it might be true", "Sign it and add a comment later",
                 "Ask Althea whether it's true"], "answer": 0,
     "concept": "Drafts can include things that weren't said. The signed note must reflect what actually happened."},
    {"id": "q_before_claim", "q": "What has to happen before Althais builds a claim from a visit?",
     "options": ["The note and codes are reviewed and approved by the provider", "The payer approves it",
                 "The patient pays their copay", "Nothing — claims build as you type"], "answer": 0,
     "concept": "Visit → note → codes → attestation → claim. Each step feeds the next."},
    {"id": "q_checkin", "q": "Why must the insurance member ID be entered exactly at check-in?",
     "options": ["It goes straight onto the claim; one wrong character can get it rejected", "It doesn't matter — billing fixes it",
                 "Only Medicare checks it", "It's only used for reports"], "answer": 0,
     "concept": "Registration details flow onto the claim (Box 1a, 2, 5). Most rejections start at registration."},
    {"id": "q_althea_clinical", "q": "You ask Althea whether a patient should get a medication. What should you expect?",
     "options": ["It shows what's documented; the clinical decision is the provider's", "A dosage recommendation to follow",
                 "It places the order for you", "It asks the patient"], "answer": 0,
     "concept": "Althea doesn't make clinical decisions. People do."},
    {"id": "q_stale_sync", "q": "The EHR sync indicator shows data hasn't synced in hours. What should you assume?",
     "options": ["The chart may not be current — check before relying on it", "Nothing has changed", "Althais is down, so stop working",
                 "It's only a display issue"], "answer": 0,
     "concept": "Stale data can be wrong. Check the source before acting on it."},
    {"id": "q_phishing", "q": "An email says your Althais password expires today and to sign in through its link. What do you do?",
     "options": ["Don't click; report it to your manager", "Sign in quickly so you aren't locked out", "Forward it to coworkers",
                 "Reply to ask if it's real"], "answer": 0,
     "concept": "Althais never asks for your password by email. Report phishing."},
    {"id": "q_right_patient", "q": "Althea opens a chart when you ask for \"John Smith\". What should you do before making changes?",
     "options": ["Confirm the name and date of birth match your patient", "Nothing — Althea picked the right one", "Ask a coworker",
                 "Start documenting right away"], "answer": 0,
     "concept": "Always confirm you're in the right patient's chart. Names can match."},
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
    if key not in LESSON_KEYS:
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
