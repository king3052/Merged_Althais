"""
training_content.py: the parts of Althais Training that prove understanding, and the material Althea teaches from.

  LESSON_CHECKS   realistic situations at the end of each lesson. Every option says why it's right or wrong. Graded on
                  the server (althais_training.py); the right answers never reach the browser before an answer is sent.
  PRACTICE        a small practice copy of Althais with fictional patients. Each role gets its own tasks, and every
                  answer is checked here, on the server.
  TUTOR           per-lesson plain explanations, examples and walkthroughs for Althea's tutor buttons.
  HOWTOS          short task guides for the Althais Guide, which stays available after the course is finished.

Everything here is fictional demo data. No real patients.
"""

import re

# ──────────────────────────────────────────────────────────────────────────
#  Checks: two situations per attempt, both must be right to master a lesson
# ──────────────────────────────────────────────────────────────────────────
def _s(sid, q, right, *wrong):
    """right = (text, why); wrong = (text, why) pairs. Option 0 is right; the server shuffles."""
    return {"id": sid, "q": q, "options": [{"t": right[0], "why": right[1], "ok": True}] +
            [{"t": t, "why": w, "ok": False} for t, w in wrong]}


LESSON_CHECKS = {
    "workspace": [
        _s("w_two_smiths", "Your 10:30 is John Smith. You search and two John Smiths come up. What do you do?",
           ("Open the one whose date of birth matches the appointment", "Right. Names repeat. Date of birth (or MRN) is how you know it's the right chart."),
           ("Open the first result", "Search order says nothing about who your patient is. You could document in the wrong person's chart."),
           ("Open both and compare their histories", "Opening a chart you don't need is a privacy problem. Compare the date of birth in the results instead.")),
        _s("w_stale_sync", "The EHR sync indicator says data last synced 6 hours ago. You're about to rely on the medication list. What do you do?",
           ("Treat the chart as possibly out of date and confirm with the patient or the source", "Right. Stale data can be wrong, so verify what you rely on."),
           ("Assume it's current. Sync delays don't matter", "Medications change. Acting on an old list can hurt a patient."),
           ("Stop using Althais until IT fixes it", "You don't need to stop working. Just confirm what you rely on.")),
        _s("w_find_fast", "You need a claim from last week but don't know where it is. What's fastest?",
           ("Use search (⌘K or Ctrl+K), or ask Althea to find it", "Right. Search and Althea look across Althais, limited to what your role can open."),
           ("Scroll the Overview page until it shows up", "Overview shows today's highlights, not every claim."),
           ("Ask a coworker to send you a screenshot", "Screenshots move patient information outside Althais, where it isn't protected.")),
    ],
    "workflow": [
        _s("f_no_attest", "The provider hasn't attested the note yet, but billing wants the claim out today. What happens?",
           ("The claim waits. It can't be created until the provider approves the note and codes", "Right. Althais requires the provider's attestation before a claim exists."),
           ("Billing creates the claim and the provider attests later", "Althais blocks this on purpose. Claims must come from a note the provider stands behind."),
           ("Anyone can tick the attestation box to keep things moving", "The attestation is the provider's personal review. Ticking it for them falsifies the record.")),
        _s("f_new_card", "At check-in, the patient's new insurance card has a different member ID from the one on file. What do you do?",
           ("Update the member ID now, exactly as it appears on the card", "Right. It goes straight onto the claim, so it has to be exact."),
           ("Leave it. Billing will fix it later", "Nobody may notice until the payer rejects the claim, weeks later."),
           ("Write the new number on a sticky note for billing", "Patient information belongs in Althais, not on paper.")),
        _s("f_denied_next", "A claim comes back denied. Where does the work go next?",
           ("To Denials, with the payer's reason and the next step: correct and resubmit, or appeal", "Right. The reason code decides what happens next."),
           ("Nowhere. Denied claims are written off automatically", "Althais never writes off a claim on its own."),
           ("Back to the front desk to re-register the patient", "Only if the reason is a registration error. Start with the reason code.")),
    ],
    "privacy": [
        _s("p_share_login", "A coworker can't sign in and asks to use your account for a few minutes to finish a patient's chart. What do you do?",
           ("Say no, and help them reset their password or reach the admin", "Right. Everything done under your login is recorded as you. Sharing it breaks the audit trail and the law."),
           ("Let them, as long as you stay next to them", "Standing nearby doesn't change whose name is on every action they take."),
           ("Let them, because patient care is urgent", "Urgency is the most common excuse for sharing a login. An admin can restore their access quickly.")),
        _s("p_neighbor", "Your neighbor is a patient at the clinic. You wonder why she came in. What do you do?",
           ("Don't open her chart. You have no work reason to", "Right. Access is for the work in front of you, not curiosity."),
           ("Take a quick look, since your role can open charts", "Having access isn't a reason. Looking without a work reason is a violation even if you tell no one."),
           ("Ask Althea to summarize her visit", "Asking Althea is the same as opening the chart yourself, and it's recorded.")),
        _s("p_step_away", "You need to leave the front desk for five minutes with a chart open. What do you do?",
           ("Lock the screen or sign out first", "Right. Anyone who walks up could otherwise use your session."),
           ("Turn the monitor away from the waiting room", "Someone can still sit down and act under your name."),
           ("Nothing. Five minutes is too short to matter", "A few seconds is enough for someone to see or change a record.")),
        _s("p_phishing", "An email says \"Your Althais password expires today, sign in here\" with an unfamiliar link. What do you do?",
           ("Don't click. Report it to your manager", "Right. It's phishing. Althais never asks for your password by email."),
           ("Sign in quickly so you aren't locked out", "That's exactly how phishing steals passwords."),
           ("Forward it to coworkers as a warning", "Forwarding spreads the link. Report it so it's handled properly.")),
    ],
    "althea": [
        _s("a_confirm_patient", "You ask Althea to open John Smith's chart and it opens one. What do you do before documenting?",
           ("Confirm the name and date of birth match your patient", "Right. Names repeat. Althea tells you when it had to choose, and you confirm."),
           ("Start documenting. Althea picked it", "Althea can match the wrong person when names repeat."),
           ("Ask Althea again to be sure", "Asking again doesn't verify anything. Check the date of birth.")),
        _s("a_clinical", "You ask Althea whether a patient should get aspirin. What should you expect?",
           ("It shows what's documented, and the decision stays with the provider", "Right. Althea never makes clinical decisions."),
           ("A dose to follow", "Althea doesn't give clinical advice."),
           ("It places the order for you", "Althea doesn't place orders.")),
        _s("a_verify_summary", "Althea says a claim was denied for CO-50. You're about to write the appeal. What do you do first?",
           ("Check the claim and the payer's notice to confirm the reason", "Right. The appeal has to answer the payer's actual reason."),
           ("Write the appeal from Althea's summary alone", "Summaries can leave things out. Verify before you rely on it."),
           ("Ignore Althea and start from scratch", "The summary is a useful start. Just confirm it.")),
    ],
    "ai_safety": [
        _s("s_confidence", "A suggested code shows 95% confidence, but it's an emergency department code and the visit was in the office. What do you do?",
           ("Reject it. The code has to match the visit, whatever the confidence", "Right. Confidence is how sure the AI is, not proof it's right."),
           ("Accept it. 95% is very high", "High confidence can still be wrong. The setting doesn't match."),
           ("Accept it and let the payer decide", "Knowingly submitting a wrong code is a compliance problem, not the payer's job.")),
        _s("s_allergy_added", "An AI-drafted note lists a penicillin allergy, but you don't remember discussing allergies. What do you do?",
           ("Check the chart and ask the patient before signing. Keep only what's confirmed", "Right. Verify, then sign what's true."),
           ("Delete it because you don't remember it", "Removing a real allergy is dangerous. Verify first."),
           ("Sign it. The AI heard the recording", "Drafts can add or drop details. You're responsible for what you sign.")),
        _s("s_outside_ai", "An outside AI chatbot could summarize a patient's history faster. Can you paste the history into it?",
           ("No. Patient information stays in Althais", "Right. Outside tools aren't covered by the clinic's privacy agreements."),
           ("Yes, if you remove the name", "Dates, conditions and other details can still identify the patient."),
           ("Yes, if it helps the patient faster", "Speed doesn't make sharing patient data with an outside service allowed.")),
    ],
}

ROLE_CHECKS = {
    "front_desk": [
        _s("r_fd_name", "The insurance card reads SMITH, JOHN, but registration says Smith, Jon. What do you do?",
           ("Correct the name to match the card exactly", "Right. Payers match names exactly."),
           ("Leave it. It's close enough", "Jon vs John is enough for the payer to reject the claim."),
           ("Add a note in the appointment comments", "Comments never reach the claim. Fix the field itself.")),
        _s("r_fd_caller", "A caller says \"I'm her daughter, what time is her appointment?\" What do you do?",
           ("Verify who's calling, following clinic policy, before sharing anything", "Right. Even an appointment time is protected."),
           ("Share just the time. It's harmless", "Appointment details are patient information."),
           ("Hang up", "Be helpful: verify them, or take a message for the patient.")),
        _s("r_fd_results", "At check-out, a patient asks what their lab results mean. What do you do?",
           ("Connect them with the clinical team", "Right. Explaining results is clinical work."),
           ("Read the results to them and explain", "Interpreting results is outside your role, and easy to get wrong."),
           ("Tell them to look it up online", "Point them to someone who can explain it properly.")),
    ],
    "physician": [
        _s("r_md_allergy", "The AI draft says \"No known drug allergies\", but the chart shows Penicillin (rash). What do you do?",
           ("Correct the note before signing", "Right. The signed note is the record, and contradictions cause errors later."),
           ("Sign it. The chart already has the allergy", "A note that contradicts the chart is a safety risk."),
           ("Leave a comment for billing", "Billing doesn't fix clinical content.")),
        _s("r_md_consent", "Recording would help with this visit. What has to happen first?",
           ("Tell the patient, get their verbal agreement, then confirm it in Virtual SOAP", "Right. Recording stays locked until you confirm consent."),
           ("Nothing. Recording is standard", "Recording always needs the patient's agreement."),
           ("Start recording and ask at the end", "Consent comes before recording, never after.")),
        _s("r_md_level", "Coding suggests 99285 (emergency department, high complexity) for an office visit. What do you do?",
           ("Reject it and choose the office visit code your documentation supports", "Right. The code must match the setting and the work documented."),
           ("Accept it. A higher level pays more", "Billing a level or setting you didn't provide is upcoding."),
           ("Leave it for billing to catch", "You attest to the codes. Fix it before you approve.")),
    ],
    "clinical": [
        _s("r_cl_vitals", "You typed BP 1482/92 by mistake and saved it. What now?",
           ("Correct it right away so the provider sees the right value", "Right. Vitals drive decisions."),
           ("Leave it. The provider will notice", "They may not, and wrong vitals can lead to wrong decisions."),
           ("Delete the whole vitals entry", "Missing vitals are a problem too. Correct it instead.")),
        _s("r_cl_allergy", "While rooming, the patient says penicillin gives them a rash, but it isn't in the chart. What do you do?",
           ("Add it to the allergy list and tell the provider", "Right. Allergies you confirm are what the provider relies on."),
           ("Mention it to the provider only", "If it isn't in the chart, the next person won't know."),
           ("Wait until the patient brings it up again", "Allergies are safety information. Record them now.")),
        _s("r_cl_consent", "The provider wants to record the visit. What has to happen first?",
           ("The patient is told and verbally agrees, and the provider confirms it in Virtual SOAP", "Right. Recording stays locked until consent is confirmed."),
           ("Nothing. Recording is standard", "Recording always needs the patient's agreement."),
           ("The patient signs a form after the visit", "Consent comes before recording.")),
    ],
    "biller": [
        _s("r_b_pos", "A claim line has 99285 with place of service 11 (office). What do you do?",
           ("Hold the claim and send it back to the provider for a code that matches the setting", "Right. The code and setting disagree, and the provider picks the level."),
           ("Change it to 99214 yourself", "You can't pick a different level without the provider's documentation and review."),
           ("Submit it. The payer will sort it out", "Payers deny, they don't fix. And the claim is inaccurate.")),
        _s("r_b_pointer", "A line's diagnosis pointer is D, but Box 21 only lists A and B. What do you do?",
           ("Point it to the documented diagnosis that supports the service", "Right. Every line must point to a diagnosis on the claim."),
           ("Leave it. Pointers don't matter", "An invalid pointer gets the line rejected."),
           ("Add any diagnosis as D", "Diagnoses must come from the documentation.")),
        _s("r_b_mod25", "UnitedHealthcare requires modifier 25 on a same-day E/M plus procedure, and it's missing. What do you do?",
           ("Confirm the note supports a separate E/M, then add modifier 25", "Right. The modifier claims a separate service, so the note must support it."),
           ("Add 25 to every E/M just in case", "Using 25 without support is a compliance problem."),
           ("Drop the E/M line", "If the E/M is supported, dropping it loses legitimate payment.")),
    ],
    "manager": [
        _s("r_m_access", "Dana at the front desk asks for Claims access \"just for this week\". What do you do?",
           ("Ask what changed. If her job changed, change her role on Staff › Roles; otherwise billing does the work", "Right. Access follows the role, not one-off favors."),
           ("Add Claims access just for her", "One-off access becomes permanent, and nobody remembers why."),
           ("Let her use the billing team's login", "Shared logins destroy the audit trail.")),
        _s("r_m_mismatch", "A new hire's ID says Jennifer Smith, but her staff record says Emma Davis. What do you do?",
           ("Request a correction and ask her to explain, e.g. with a name-change document", "Right. She can fix it, and you keep a record of why."),
           ("Approve it. She uploaded it herself", "A name mismatch is exactly what review is for."),
           ("Reject it without saying why", "Tell her what's wrong so she can fix it.")),
        _s("r_m_offboard", "A medical assistant left the clinic yesterday. What do you do in Althais?",
           ("Offboard her, which removes her access and lists the close-out tasks", "Right. Access should end with employment."),
           ("Nothing until her last paycheck", "Her account stays usable the whole time."),
           ("Change her password and keep the account", "Offboarding is the recorded way to remove access.")),
    ],
    "general": [
        _s("r_g_blocked", "Althais won't open something you think you need for your job. What do you do?",
           ("Ask your manager whether your role should include it", "Right. Access comes from your role, and your manager can change it."),
           ("Ask a coworker with access to open it for you", "That works around the permission instead of fixing it."),
           ("Use a coworker's login", "Never. Everything under a login is recorded as that person.")),
        _s("r_g_caller", "A caller asks for a patient's appointment time and says they're family. What do you do?",
           ("Verify who's calling, following clinic policy, before sharing anything", "Right. Appointment details are protected."),
           ("Share just the time", "Even an appointment time is patient information."),
           ("Hang up", "Be helpful: verify them, or take a message.")),
        _s("r_g_results", "A patient asks you what their test results mean. What do you do?",
           ("Connect them with the clinical team", "Right. Explaining results is clinical work."),
           ("Explain them yourself", "It's outside your role and easy to get wrong."),
           ("Tell them to look it up online", "Point them to someone who can explain it properly.")),
    ],
}


def checks_for(lesson: str, family: str) -> list:
    if lesson == "role":
        return ROLE_CHECKS.get(family) or ROLE_CHECKS["general"]
    return LESSON_CHECKS.get(lesson, [])


ALL_CHECKS = {q["id"]: q for qs in list(LESSON_CHECKS.values()) + list(ROLE_CHECKS.values()) for q in qs}


# ──────────────────────────────────────────────────────────────────────────
#  Practice: a small practice copy of Althais (fictional data only)
# ──────────────────────────────────────────────────────────────────────────
PATIENTS = [
    {"mrn": "JS10023", "name": "Smith, John", "dob": "1968-04-12", "sex": "M", "phone": "(617) 555-0199", "payer": "UnitedHealthcare",
     "member": "UHC4471098", "pcp": "Dr. R. Patel", "allergies": ["Penicillin (rash)"], "meds": ["Metformin 500mg twice daily", "Lisinopril 10mg daily"],
     "history": "Type 2 diabetes, hypertension. Denies prior cardiac history."},
    {"mrn": "JS20417", "name": "Smith, John", "dob": "1981-09-03", "sex": "M", "phone": "(617) 555-0142", "payer": "Aetna",
     "member": "AET0093312", "pcp": "Dr. A. Chen", "allergies": ["None known"], "meds": ["None"], "history": "Seasonal allergies."},
    {"mrn": "JS30552", "name": "Smith, Joan", "dob": "1968-04-21", "sex": "F", "phone": "(617) 555-0177", "payer": "Medicare",
     "member": "1EG4TE5MK72", "pcp": "Dr. R. Patel", "allergies": ["Sulfa"], "meds": ["Levothyroxine 50mcg daily"], "history": "Hypothyroidism."},
    {"mrn": "ML40019", "name": "Lopez, Maria", "dob": "1990-02-14", "sex": "F", "phone": "(617) 555-0110", "payer": "Cigna",
     "member": "CIG5520981", "pcp": "Dr. K. Nguyen", "allergies": ["Latex"], "meds": ["None"], "history": "Asthma, well controlled."},
    {"mrn": "MJ20041", "name": "Johnson, Maria", "dob": "1975-11-30", "sex": "F", "phone": "(617) 555-0163", "payer": "BlueCross BlueShield",
     "member": "BCB7730042", "pcp": "Dr. A. Chen", "allergies": ["None known"], "meds": ["Apixaban 5mg twice daily"], "history": "Atrial fibrillation."},
]
ASSIGNMENT = "Your 10:30 patient is John Smith, born April 12, 1968."
NOTE_DRAFT = {
    "cc": "Chest pain and shortness of breath for 2 days",
    "hpi": "58-year-old with intermittent substernal chest pressure radiating to the left arm, worse with exertion, with mild shortness of breath. History of MI in 2019.",
    "vitals": "BP 148/92 · HR 88 · RR 18 · SpO2 97%",
    "allergies": "No known drug allergies",
    "assessment": "Chest pain, likely cardiac given risk factors. Rule out acute coronary syndrome.",
    "plan": "Serial troponins, EKG monitoring, cardiology consult, aspirin 81mg.",
}
CODES = [
    {"code": "R07.9", "type": "ICD-10", "label": "Chest pain, unspecified", "conf": 90, "right": "accept"},
    {"code": "R06.02", "type": "ICD-10", "label": "Shortness of breath", "conf": 85, "right": "accept"},
    {"code": "99285", "type": "CPT", "label": "Emergency department visit, high complexity", "conf": 95, "right": "reject"},
    {"code": "Z88.0", "type": "ICD-10", "label": "Allergy status to penicillin", "conf": 61, "right": "accept"},
    {"code": "I21.9", "type": "ICD-10", "label": "Acute myocardial infarction, unspecified", "conf": 58, "right": "reject"},
]
CLAIMS = [
    {"id": "CL-2026-001", "patient": "Smith, John", "payer": "UnitedHealthcare", "cpt": "99213", "amount": 285.00, "status": "Paid"},
    {"id": "CL-2026-003", "patient": "Johnson, Maria", "payer": "BlueCross BlueShield", "cpt": "93000", "amount": 312.00, "status": "Denied",
     "denial": "CO-50: Not medically necessary. The note doesn't document why the ECG (93000) was needed."},
    {"id": "CL-2026-007", "patient": "Johnson, Maria", "payer": "BlueCross BlueShield", "cpt": "99214", "amount": 198.00, "status": "Paid"},
    {"id": "CL-2026-009", "patient": "Garcia, Elena", "payer": "Medicare", "cpt": "99215", "amount": 312.00, "status": "Denied",
     "denial": "CO-16: Claim lacks information. Missing referring provider."},
    {"id": "CL-2026-011", "patient": "Lopez, Maria", "payer": "Cigna", "cpt": "99213", "amount": 265.00, "status": "Submitted"},
]
FLAG_CLAIM = {"id": "CL-2026-014", "patient": "Smith, John", "payer": "UnitedHealthcare", "member": "UHC4471098", "pos": "11 (office)",
              "dx": [["A", "R07.9", "Chest pain, unspecified"], ["B", "I10", "Essential hypertension"]],
              "lines": [{"key": "L1", "cpt": "99285", "label": "Emergency department visit, high complexity", "pointer": "A,B", "charge": 312.00},
                        {"key": "L2", "cpt": "93000", "label": "Electrocardiogram, complete", "pointer": "D", "charge": 142.50},
                        {"key": "L3", "cpt": "81003", "label": "Urinalysis, automated", "pointer": "B", "charge": 18.00}]}
LINE_ISSUES = [["none", "No issue"], ["pos_mismatch", "Code doesn't match the place of service"], ["bad_pointer", "Diagnosis pointer is invalid"],
               ["missing_modifier", "Missing modifier"]]
LINE_RIGHT = {"L1": "pos_mismatch", "L2": "bad_pointer", "L3": "none"}
DENIAL_ACTIONS = [["provider_docs", "Ask the provider to document why the ECG was needed, then resubmit or appeal"],
                  ["write_off", "Write it off"], ["resubmit", "Resubmit it unchanged"], ["bill_patient", "Bill the patient instead"]]
SLOTS = [{"t": "09:00", "provider": "Dr. R. Patel", "taken": "Lopez, Maria"}, {"t": "09:20", "provider": "Dr. R. Patel", "taken": "Johnson, Maria"},
         {"t": "09:40", "provider": "Dr. R. Patel", "taken": ""}, {"t": "10:00", "provider": "Dr. R. Patel", "taken": ""},
         {"t": "09:00", "provider": "Dr. A. Chen", "taken": ""}, {"t": "09:20", "provider": "Dr. A. Chen", "taken": ""}]
REGISTRATION = {"name": "Smith, Jon", "dob": "04/12/1968", "phone": "(617) 555-0199", "payer": "UnitedHealthcare", "member": "UHC4471089"}
CARD = {"name": "SMITH, JOHN", "member": "UHC4471098", "group": "22014A", "payer": "UnitedHealthcare", "valid": "Through 12/31/2026"}
VITALS = {"bp": "1482/92", "hr": "88", "temp": "98.4", "spo2": "97"}
TODO = [{"key": "exp_invite", "name": "Sam Ortiz", "text": "Invitation expired"},
        {"key": "doc_emma", "name": "Emma Davis", "text": "Government ID: the name doesn't match the staff record"},
        {"key": "ehr_priya", "name": "Priya Rao", "text": "EHR Access needed"},
        {"key": "doc_jordan", "name": "Jordan Lee", "text": "BLS card awaiting review"}]
DOC_REVIEW = {"staff": "Emma Davis", "role": "Medical Assistant", "document": "Government ID (driver's license)", "nameOnId": "Jennifer Smith",
              "dob": "1994-03-08", "expires": "2029-03-08"}
ACCESS_CHOICES = [["ask_role", "Ask what changed. If her job changed, change her role on Staff › Roles; otherwise the billing team does the work"],
                  ["add_access", "Add Claims access just for her, for this week"], ["share_login", "Let her use the billing team's login"],
                  ["ignore", "Ignore it until she asks again"]]

TASKS = {
    "find_patient": ("Find the right patient", ASSIGNMENT + " Search for him and open his chart."),
    "fix_registration": ("Fix the registration", "Compare registration with his insurance card and correct anything that doesn't match exactly. Then save."),
    "book_followup": ("Book the follow-up", "Dr. Patel wants to see him again on Tuesday, October 20. Book the first open slot with Dr. R. Patel."),
    "fix_note": ("Correct the AI draft", "This note was drafted from the visit recording. Compare it with his chart and fix what the AI got wrong. Then save."),
    "review_codes": ("Review the suggested codes", "The visit was in the office. Accept the codes the note supports and reject the rest."),
    "fix_vitals": ("Fix the vitals", "The device read BP 148/92, HR 88, Temp 98.4, SpO2 97%. One value was typed wrong. Fix it and save."),
    "find_claim": ("Find the denied claim", "Find Maria Johnson's denied BlueCross claim and open it."),
    "denial_step": ("Choose the next step", "Read the denial reason and pick what should happen next."),
    "flag_claim": ("Flag the claim problems", "Before this claim goes out, mark each line: is anything wrong with it?"),
    "find_item": ("Find the document decision", "Open the To-Do item for Emma Davis's document."),
    "decide_doc": ("Decide on the document", "Review Emma's ID against her staff record and make a decision. If you send it back, tell her why."),
    "access_request": ("Handle an access request", "Dana (Front Desk) writes: \"Can I get Claims access just for this week? Billing is behind.\" What do you do?"),
}
PATHS = {
    "front_desk": ["find_patient", "fix_registration", "book_followup"],
    "general": ["find_patient", "fix_registration", "book_followup"],
    "physician": ["find_patient", "fix_note", "review_codes"],
    "clinical": ["find_patient", "fix_vitals", "fix_note"],
    "biller": ["find_claim", "denial_step", "flag_claim"],
    "manager": ["find_item", "decide_doc", "access_request"],
}


def practice_for(family: str) -> dict:
    keys = PATHS.get(family) or PATHS["general"]
    data = {"assignment": ASSIGNMENT}
    if {"find_patient", "fix_note", "fix_vitals", "fix_registration"} & set(keys):
        data["patients"] = PATIENTS
    if "fix_registration" in keys:
        data.update(registration=REGISTRATION, card=CARD)
    if "book_followup" in keys:
        data.update(slots=SLOTS, day="Tuesday, October 20")
    if "fix_note" in keys:
        data["note"] = NOTE_DRAFT
    if "review_codes" in keys:
        data["codes"] = [{k: v for k, v in c.items() if k != "right"} for c in CODES]
    if "fix_vitals" in keys:
        data["vitals"] = VITALS
    if {"find_claim", "denial_step"} & set(keys):
        data["claims"] = [{k: v for k, v in c.items()} for c in CLAIMS]
        data["denialActions"] = DENIAL_ACTIONS
    if "flag_claim" in keys:
        data.update(flagClaim=FLAG_CLAIM, lineIssues=LINE_ISSUES)
    if "find_item" in keys:
        data.update(todo=TODO, docReview=DOC_REVIEW, accessChoices=ACCESS_CHOICES)
    return {"tasks": [{"key": k, "title": TASKS[k][0], "instruction": TASKS[k][1]} for k in keys], "data": data}


def _norm(s) -> str:
    return re.sub(r"[^a-z0-9,/]", "", str(s or "").lower())


def check_task(key: str, a: dict) -> tuple:
    """(ok, message). The message explains what's still wrong, without giving away the answer outright."""
    a = a if isinstance(a, dict) else {}
    if key == "find_patient":
        m = a.get("mrn")
        if m == "JS10023":
            return True, "That's him: John Smith, born 04/12/1968, MRN JS10023."
        if m == "JS20417":
            return False, "Same name, different person. This John Smith was born in 1981. Check the date of birth."
        if m == "JS30552":
            return False, "Close, but that's Joan Smith. Check the name and the date of birth."
        return False, "That isn't your 10:30. Match the name and the date of birth."
    if key == "fix_registration":
        bad = []
        if _norm(a.get("name")).replace(" ", "") != "smith,john":
            bad.append("the name doesn't match the card exactly")
        if _norm(a.get("member")).upper() != "UHC4471098":
            bad.append("the member ID doesn't match the card character for character")
        if _norm(a.get("dob")) != _norm("04/12/1968"):
            bad.append("the date of birth changed, but it was already right")
        return (True, "Saved. Registration now matches the card, so the claim will too.") if not bad else (False, "Not yet: " + "; ".join(bad) + ".")
    if key == "book_followup":
        if a.get("provider") == "Dr. R. Patel" and a.get("t") == "09:40":
            return True, "Booked: Tuesday, October 20 at 9:40 with Dr. R. Patel."
        if a.get("provider") != "Dr. R. Patel":
            return False, "That's the wrong provider. The follow-up is with Dr. R. Patel."
        return False, "That isn't the first open slot with Dr. Patel. Check which slots are already taken."
    if key == "fix_note":
        hpi, alg = str(a.get("hpi") or ""), str(a.get("allergies") or "")
        bad = []
        if re.search(r"\bmi\b|myocardial|heart attack", hpi, re.I):
            bad.append("the HPI still says he had an MI, but his chart says he denies prior cardiac history")
        if len(hpi.strip()) < 30 or "chest" not in hpi.lower():
            bad.append("the HPI lost the real history of his chest pain. Only remove what's wrong")
        if "penicillin" not in alg.lower():
            bad.append("the allergies don't match his chart")
        if re.search(r"no known", alg, re.I):
            bad.append("the allergies still say no known allergies")
        return (True, "Saved. The note now matches what happened and what's in his chart.") if not bad else (False, "Not yet: " + "; ".join(bad) + ".")
    if key == "review_codes":
        d = a.get("decisions") if isinstance(a.get("decisions"), dict) else {}
        wrong = [c["code"] for c in CODES if d.get(c["code"]) != c["right"]]
        if not wrong:
            return True, "Right. 99285 is an emergency department code (this was an office visit) and I21.9 codes a heart attack nobody diagnosed."
        undecided = [c["code"] for c in CODES if d.get(c["code"]) not in ("accept", "reject")]
        if undecided:
            return False, f"Decide on every code first ({', '.join(undecided)} still open)."
        return False, f"{len(wrong)} decision{'s' if len(wrong) > 1 else ''} to rethink. Ask: is it documented, and does it match an office visit?"
    if key == "fix_vitals":
        bp = str(a.get("bp") or "").replace(" ", "")
        others = (str(a.get("hr")).strip(), str(a.get("temp")).strip(), str(a.get("spo2")).strip().rstrip("%"))
        if bp == "148/92" and others == ("88", "98.4", "97"):
            return True, "Saved. BP 148/92 is what the device read."
        if bp != "148/92":
            return False, "The blood pressure still doesn't match the device reading."
        return False, "You changed a value that was already right. Only fix the wrong one."
    if key == "find_claim":
        if a.get("claim") == "CL-2026-003":
            return True, "Found it: CL-2026-003, denied by BlueCross BlueShield."
        if a.get("claim") == "CL-2026-007":
            return False, "Right patient and payer, but that claim was paid. You want the denied one."
        return False, "That isn't Maria Johnson's denied BlueCross claim. Try the Denied filter or search her name."
    if key == "denial_step":
        if a.get("action") == "provider_docs":
            return True, "Right. CO-50 means the documentation didn't show why the ECG was needed. Fix that first."
        return False, {"write_off": "Writing it off gives up payment the clinic may be owed.",
                       "resubmit": "Nothing changed, so it would be denied again.",
                       "bill_patient": "Patients usually can't be billed for a medical-necessity denial like this."}.get(a.get("action"), "Pick an action.")
    if key == "flag_claim":
        lines = a.get("lines") if isinstance(a.get("lines"), dict) else {}
        wrong = [k for k, v in LINE_RIGHT.items() if lines.get(k) != v]
        if not wrong:
            return True, "Right. Line 1 is an emergency department code on an office claim, and line 2 points to diagnosis D, which isn't on the claim."
        return False, f"Look again at line{'s' if len(wrong) > 1 else ''} {', '.join(w[1:] for w in wrong)}. Check the place of service and Box 21."
    if key == "find_item":
        if a.get("item") == "doc_emma":
            return True, "That's the one: Emma's ID needs your decision."
        return False, "That item isn't Emma Davis's document."
    if key == "decide_doc":
        act, reason = a.get("action"), str(a.get("reason") or "").strip()
        if act == "request_correction" and len(reason) >= 10:
            return True, "Sent back. Emma sees your reason and can upload the right ID or explain the name change."
        if act == "request_correction":
            return False, "Tell her what's wrong, in a sentence, so she can fix it."
        if act == "approve":
            return False, "The name on the ID doesn't match her record. Approving it skips the one check that matters."
        if act == "reject":
            return False, "Rejecting ends it. Requesting a correction lets her fix it or explain a name change."
        return False, "Choose a decision."
    if key == "access_request":
        if a.get("choice") == "ask_role":
            return True, "Right. Access follows the role. If her job changed, change the role; otherwise billing does it."
        return False, {"add_access": "One-off access tends to become permanent, and nobody remembers why she has it.",
                       "share_login": "Never. Shared logins make it impossible to tell who did what.",
                       "ignore": "She needs an answer, and billing is still behind."}.get(a.get("choice"), "Pick an option.")
    return False, "Unknown task."


# ──────────────────────────────────────────────────────────────────────────
#  Tutor material (Althea teaches only from this and the lessons themselves)
# ──────────────────────────────────────────────────────────────────────────
TUTOR = {
    "welcome": {"simple": "Althais is the one place your clinic keeps patient records, visit notes, claims and payments. It does the routine work, and people make the decisions.",
                "example": "A patient is seen, the provider documents the visit, Althais suggests codes, the provider approves them, and Althais builds and sends the claim.",
                "steps": ["Watch the video to see one visit from start to finish.", "Tour the screens your role uses.", "Practice, then prove it with short checks."]},
    "workspace": {"simple": "The top bar has the apps you can use. Inside each app, tabs take you to each area. Search finds anything, and Althea answers questions.",
                  "example": "To find last week's claim for Maria Johnson, press Ctrl+K (⌘K on a Mac), type her name, and pick the claim from the results.",
                  "steps": ["Check the patient in context (name and date of birth) before you change anything.", "Use the tabs to move between areas.",
                            "Use search or Althea to jump straight to a patient, claim or code.", "Watch the EHR sync time; if it's old, confirm before relying on data."]},
    "workflow": {"simple": "Every visit follows the same path: check-in, note, codes, the provider's approval, claim, payer, payment. A mistake early on travels all the way to the claim.",
                 "example": "A member ID typed wrong at check-in ends up in Box 1a of the claim, and the payer rejects it weeks later.",
                 "steps": ["Check in the patient and verify insurance.", "Document the visit.", "Review the suggested codes.", "The provider attests.",
                           "Althais builds the claim and sends it.", "Track it; work any denial from its reason code."]},
    "privacy": {"simple": "Only open what you need for the work in front of you, keep your login to yourself, and lock your screen when you step away. Everything is recorded under your name.",
                "example": "Your neighbor comes in for a visit. You don't open her chart, even though you could, because you have no work reason to.",
                "steps": ["Before opening a record, ask: do I need this for my work right now?", "Never share your password or let someone use your session.",
                          "Lock your screen every time you step away.", "Report anything that looks wrong right away."]},
    "althea": {"simple": "Althea is the assistant in the corner. It answers questions and does tasks using only what your role can see. It never makes clinical decisions.",
               "example": "Ask \"open John Smith's chart\". If there are two, Althea tells you which one it chose, and you check the date of birth.",
               "steps": ["Tap the A button in the bottom-right corner.", "Type or tap the mic and talk.", "Check its answer against the source before anything official."]},
    "ai_safety": {"simple": "AI output is a draft. It can add things nobody said, drop things that matter, or pick a wrong code with high confidence. You check it before it's used.",
                  "example": "Coding suggests 99285 at 95% confidence. The visit was in the office, so it's wrong anyway, and you reject it.",
                  "steps": ["Read the AI output as a draft.", "Check it against the chart and what actually happened.", "Correct or reject what's wrong.",
                            "Never paste patient information into outside AI tools."]},
    "role": {"simple": "Your role decides which parts of Althais you use. This lesson walks through your part of the work and the mistakes that matter most in it.",
             "example": "Front desk: an exact member ID. Clinicians: a correct note and codes. Billers: a clean claim. Managers: the right access for each role.",
             "steps": ["Learn the screens your role uses.", "Practice the tasks you'll do most.", "Know what to hand to someone else."]},
    "practice": {"simple": "This is a practice copy of Althais with made-up patients. Do the tasks the way you would at work. Althais checks each one and tells you what to fix.",
                 "example": "For \"Find the right patient\": search Smith, look at the dates of birth, and open the one born April 12, 1968.",
                 "steps": ["Read the task on the left.", "Do it in the practice screen.", "Save or submit; fix anything Althais points out.", "Move on when it's checked off."]},
}

HOWTOS = [
    {"id": "upload_renewal", "q": "Upload a renewed license or certification", "areas": None, "lesson": "welcome",
     "steps": ["Open the Staff Portal from your name in the top-right corner.", "Go to Credentials.", "Choose the credential and upload a PDF or a clear photo.",
               "Althais reads it in a few seconds. Confirm the details it found."]},
    {"id": "find_patient", "q": "Find a patient and open the right chart", "areas": {"patients"}, "lesson": "workspace",
     "steps": ["Press Ctrl+K (⌘K on a Mac) or use Patients.", "Type the name.", "Match the date of birth or MRN before you open it.",
               "Check the patient in context at the top before changing anything."]},
    {"id": "verify_insurance", "q": "Update insurance at check-in", "areas": {"patients", "schedule"}, "lesson": "workflow",
     "steps": ["Open the patient's record and go to Insurance.", "Compare every field with the card.", "Enter the name and member ID exactly as printed.", "Save."]},
    {"id": "book_appt", "q": "Book or move an appointment", "areas": {"schedule"}, "lesson": "workspace",
     "steps": ["Open the Scheduler.", "Pick the provider and the day.", "Choose an open slot and the patient.", "Save, and confirm with the patient."]},
    {"id": "write_note", "q": "Document a visit in Virtual SOAP", "areas": {"scribe"}, "lesson": "role",
     "steps": ["Open Virtual SOAP from the top bar.", "Step 1: pick the patient, visit type and place of service.",
               "Step 2: document the visit. To record, get the patient's consent first and tick the box.",
               "Step 3: review the suggested codes.", "Step 4: review everything and attest."]},
    {"id": "review_codes", "q": "Review AI-suggested codes", "areas": {"scribe", "code_a_note", "coding_review"}, "lesson": "ai_safety",
     "steps": ["Read each code with the note next to it.", "Accept what the note supports and matches the setting.", "Reject the rest, whatever the confidence.",
               "Add anything documented that was missed, then re-run coding if you edited the note."]},
    {"id": "check_claim", "q": "Check a claim's status", "areas": {"claims"}, "lesson": "workflow",
     "steps": ["Open Claims.", "Use the status filters or search by patient, payer or code.", "Open the claim for its history and any risk flags."]},
    {"id": "work_denial", "q": "Work a denied claim", "areas": {"denials", "claims", "appeals"}, "lesson": "role",
     "steps": ["Open Denials.", "Read the reason code in plain language.", "Correct and resubmit if the claim had an error.",
               "Appeal if it was right; review any drafted letter before sending."]},
    {"id": "payer_rules", "q": "Look up a payer's rules", "areas": {"payer_intelligence", "claims"}, "lesson": "role",
     "steps": ["Open Payer Intelligence.", "Filter by payer, CPT code or specialty.", "Check high-impact rules before you bill something unfamiliar."]},
    {"id": "add_staff", "q": "Add a new staff member", "areas": {"team", "onboarding"}, "lesson": "role",
     "steps": ["Open Staff › Onboarding and choose Add Staff Member.", "Pick their role; their onboarding is built from it.",
               "Send the invitation. They get a temporary password by email."]},
    {"id": "change_access", "q": "Change what a role can open", "areas": {"team", "settings"}, "lesson": "role",
     "steps": ["Open Staff › Roles.", "Tick or untick a permission for the role.", "Everyone activated with that role gets the change right away."]},
    {"id": "report_problem", "q": "Report a privacy or security problem", "areas": None, "lesson": "privacy",
     "steps": ["Stop what you're doing and don't forward anything suspicious.", "Tell your manager right away.", "Write down what you saw and when."]},
    {"id": "ask_althea", "q": "Ask Althea for help", "areas": None, "lesson": "althea",
     "steps": ["Tap the A button in the bottom-right corner.", "Type or tap the mic and speak.", "Check the answer against the source before relying on it."]},
    {"id": "lock_screen", "q": "Lock your screen", "areas": None, "lesson": "privacy",
     "steps": ["Windows: press Windows key + L.", "Mac: press Control + Command + Q.", "Do it every time you step away."]},
]


def howtos_for(areas: set) -> list:
    return [dict(h, areas=None) for h in HOWTOS if h["areas"] is None or h["areas"] & areas]
