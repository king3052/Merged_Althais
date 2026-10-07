"""
platform_pages.py: the five Platform pages (AI Scribe, Medical Coding, Claims & Insurance, Althea, Team & Onboarding),
built in the landing page's style (templates/platform_page.html, static/althais-landing.css).

Each page: a hero with a product card, three alternating feature rows each with its own mock card, a capability grid,
FAQs, and the dark call to action. Mock cards use example data only (labeled "Example" where it could read as real).
Card types (rendered by the `mock` macro): note, rows, codes, chips, claims, chat, people, checklist.
"""

from fastapi import APIRouter, Depends, Request

from auth import current_user

router = APIRouter()
_templates = None   # set by main.py

NAV = [("AI Scribe", "/platform/ai-scribe"), ("Medical Coding", "/platform/medical-coding"), ("Claims & Insurance", "/platform/claims-insurance"),
       ("Althea", "/platform/althea"), ("Team & Onboarding", "/platform/team-onboarding")]


REGISTRY = {}   # href -> {"section", "name", "text", "demo"}: every page built on this template, for "Keep exploring"
CROSS = {"Platform": "/how-it-works", "For Your Practice": "/platform/ai-scribe", "Solutions": "/platform/medical-coding",
         "Resources": "/platform/ai-scribe", "Company": "/how-it-works"}


def register(section: str, pages: dict, nav: list):
    names = {h: n for n, h in nav}
    for href, p in pages.items():
        REGISTRY[href] = {"section": section, "name": names.get(href, p["nav"]), "text": p.get("card") or p["lede"], "demo": bool(p.get("demo")), "href": href}


def related(here: str, section: str, nav: list) -> list:
    """Three neighbours from the same section, then one page from elsewhere on the site."""
    own = [REGISTRY[h] for _, h in nav if h != here and h in REGISTRY]
    i = [h for _, h in nav].index(here) if here in [h for _, h in nav] else 0
    own = own[i:] + own[:i]
    cross = REGISTRY.get(CROSS.get(section, ""))
    out = own[:3] + ([cross] if cross and cross["href"] != here else own[3:4])
    return out


def render(request: Request, user, page: dict, here: str, section: str, nav: list, ctas=None):
    """Shared by every page built on templates/platform_page.html (Platform, For Your Practice, Solutions, Resources, Company)."""
    return _templates.TemplateResponse(request, "platform_page.html", {
        "user": user, "page": page, "here": here, "section": section, "nav_items": nav,
        "ctas": page.get("ctas", ctas) or [], "related": related(here, section, nav)})

PAGES = {
    "ai-scribe": {
        "demo": "ai-scribe", "card": 'Record or paste a visit; get a SOAP note to review and sign.',
        "specs": [('Capture', 'Live recording in the browser, an uploaded recording, or a pasted transcript'), ('Consent', 'A patient-consent check must be ticked before recording starts'), ('Note sections', 'Chief complaint, HPI, review of systems, vitals, exam, assessment and plan'), ('More history', 'Current medications, allergies, past medical, surgical and family history'), ('Visit types', 'Office visit, follow-up, new patient, telehealth, urgent care, emergency and procedures'), ('Review', 'Every field stays editable; the draft is labeled until you sign it'), ('Sign', 'Signed notes are saved with your name and the time'), ('After signing', 'Copy, download, add to the chart, or send straight to Coding')],
        "nav": "AI Scribe", "eyebrow": "AI Scribe",
        "title": "Say the visit.", "em": "Get the note.",
        "lede": "Record the encounter or paste a transcript. Althais drafts a structured SOAP note in the way you'd write it, ready for you to review and sign.",
        "meta": "Althais AI Scribe turns a recorded visit or transcript into a structured SOAP note you review, edit and sign.",
        "hero": [
            {"type": "note", "eyebrow": "You say", "text": "58-year-old with chest pain and shortness of breath for two hours. BP 148/92, HR 88. EKG normal sinus rhythm, troponin pending. Rule out ACS, serial troponins, cardiology consult."},
            {"type": "rows", "eyebrow": "SOAP note · draft", "soft": True, "rows": [("Subjective", "Chest pain, SOB × 2 hr", ""), ("Objective", "BP 148/92 · HR 88 · NSR", ""),
                                                                                  ("Assessment", "Rule out ACS", ""), ("Plan", "Serial troponins, cardiology", ""), ("Status", "Ready to sign", "g")]},
        ],
        "rows": [
            {"eyebrow": "Capture", "title": "However you already work.", "em": "Spoken or typed.",
             "text": "Record the visit live, upload a recording, or paste a transcript from the tool you already use. Say it the way you'd tell a colleague. There's nothing to memorize.",
             "cards": [{"type": "chips", "eyebrow": "Bring in the visit", "chips": ["Record live", "Upload audio", "Paste transcript", "Type it"]},
                       {"type": "rows", "eyebrow": "Example visit", "soft": True, "rows": [("Visit type", "Office visit · established", ""), ("Length", "18 min", ""), ("Captured", "Transcript", "g")]}]},
            {"eyebrow": "Structure", "title": "A real SOAP note,", "em": "not a text box to fix.",
             "text": "Althais organizes what was said into Subjective, Objective, Assessment and Plan, including duration and the details a chart reviewer expects to find.",
             "cards": [{"type": "rows", "eyebrow": "Structured note", "rows": [("Chief complaint", "Chest pain, SOB", ""), ("History", "2 hr onset, at rest", ""), ("Exam", "Lungs clear, no edema", ""),
                                                                             ("Assessment", "Rule out ACS", ""), ("Plan", "Troponins, cardiology", ""), ("Provider time", "47 min · documented", "g")]}]},
            {"eyebrow": "Sign", "title": "Nothing is final", "em": "until you sign it.",
             "text": "Every note starts as a draft. Change anything, then sign when it says what you meant. Signed notes go to the chart, to your clipboard or to a download, and straight into coding.",
             "cards": [{"type": "checklist", "eyebrow": "Before it's signed", "items": [("Review the draft", True), ("Edit anything that needs your judgment", True), ("Sign the note", True), ("Send to Medical Coding", False)]},
                       {"type": "chips", "eyebrow": "After signing", "soft": True, "chips": ["Copy", "Download", "Add to chart", "Code this note"]}]},
        ],
        "grid": [("Record or paste", "Live recording, uploaded audio, or a transcript from another tool."), ("Structured SOAP", "Organized the way reviewers and payers expect."),
                 ("Drafts you control", "Edit freely. Nothing is final until you sign."), ("Into the chart", "Copy, download or chart the signed note."),
                 ("Straight to coding", "A signed note flows into Medical Coding."), ("Private by design", "Handled under HIPAA safeguards, inside your clinic.")],
        "faq": [("Does the scribe replace my judgment?", "No. It writes a draft. Nothing becomes part of the record until you review and sign it."),
                ("Can I use transcripts from another tool?", "Yes. Paste a transcript and Althais builds the note from it."),
                ("What happens after I sign?", "The note is ready for Medical Coding, which suggests CPT and ICD-10 codes with the reasons behind each one.")],
        "next": "medical-coding",
    },
    "medical-coding": {
        "demo": "medical-coding", "card": 'CPT and ICD-10 codes with confidence and reasons, checked before billing.',
        "specs": [('Code sets', 'CPT and HCPCS procedures, ICD-10-CM diagnoses'), ('Inputs', 'A note from Scribe or the EMR, or any pasted note, plus encounter type and visit minutes'), ('Encounter types', 'Office / outpatient, urgent care and emergency department'), ('Time-based rules', 'Office E/M by total time, critical care 99291 and 99292 thresholds, prolonged services'), ('Emergency visits', 'ED levels 99281 to 99285 follow medical decision making, not time'), ('Bundling', 'Every claim is checked against CMS NCCI procedure-to-procedure edits'), ('Format checks', 'Malformed CPT, HCPCS and ICD-10 codes are flagged before they reach a claim'), ('Confidence floor', "Suggestions under your clinic's confidence setting are set aside until you ask for them"), ('Export', 'Copy the codes or export them to CSV')],
        "nav": "Medical Coding", "eyebrow": "Medical Coding",
        "title": "Codes you can defend,", "em": "with the reasons attached.",
        "lede": "Althais reads the note and suggests ICD-10 and CPT codes, each with a confidence score and the documentation behind it. You decide on every one.",
        "meta": "Althais Medical Coding suggests CPT and ICD-10 codes from the clinical note with confidence and reasons, and checks them against CMS edits before a claim goes out.",
        "hero": [
            {"type": "codes", "eyebrow": "Suggested codes", "codes": [("CPT", "99285", "ED visit, high complexity", "95%"), ("ICD-10", "R07.9", "Chest pain, unspecified", "90%"), ("ICD-10", "R06.02", "Shortness of breath", "85%")]},
            {"type": "chips", "eyebrow": "Your call on every one", "soft": True, "tags": [("Accept", ""), ("Reject", "n"), ("Add your own", "n")]},
        ],
        "rows": [
            {"eyebrow": "Confidence", "title": "See what's solid,", "em": "and what needs a look.",
             "text": "Every suggestion carries a confidence score, so you know at a glance where to spend your attention. High-confidence codes move quickly; the rest get a second look.",
             "cards": [{"type": "codes", "eyebrow": "Example · office visit", "codes": [("CPT", "99214", "Established patient, moderate", "93%"), ("ICD-10", "E11.9", "Type 2 diabetes", "96%"), ("ICD-10", "I10", "Essential hypertension", "94%"), ("CPT", "83036", "Hemoglobin A1c", "71%")]}]},
            {"eyebrow": "Reasons", "title": "Every code points", "em": "back to the note.",
             "text": "Each suggestion shows the words in the documentation that support it, so you can defend it to a payer, an auditor or yourself.",
             "cards": [{"type": "rows", "eyebrow": "Why 99214", "rows": [("Problems addressed", "2 chronic, 1 worsening", ""), ("Data reviewed", "A1c, BMP ordered", ""), ("Risk", "Prescription drug management", ""), ("MDM level", "Moderate", "g")]},
                       {"type": "rows", "eyebrow": "From the note", "soft": True, "rows": [("“A1c up to 8.4…”", "E11.9", ""), ("“BP 148/92 on lisinopril…”", "I10", "")]}]},
            {"eyebrow": "Check", "title": "Checked before", "em": "it ever leaves.",
             "text": "Codes are compared against the CMS procedure-to-procedure edit table, and procedures are linked to the diagnoses that justify them, so bundling and pointer errors are caught before a denial.",
             "cards": [{"type": "rows", "eyebrow": "The check", "rows": [("NCCI bundling conflicts", "0 found", "g"), ("CMS PTP edit table", "checked", ""), ("Diagnosis pointers", "linked", ""), ("Modifier review", "flagged if needed", "a")]}]},
        ],
        "grid": [("Code A Note", "CPT and ICD-10 suggestions from a finished note."), ("Confidence scores", "Know where to look closer."), ("Reasons attached", "Each code cites the documentation."),
                 ("Check Codes", "Paste codes you chose and check them against the note."), ("Bundling checks", "CMS PTP edits run on every claim."), ("Human approval", "Nothing bills without your decision.")],
        "faq": [("Who is responsible for the final codes?", "Your team. Althais suggests and explains; a person approves every code before it reaches a claim."),
                ("Can it check codes I picked myself?", "Yes. Check Codes compares your codes with the note and flags anything the documentation doesn't support."),
                ("Which code sets does it use?", "CPT for procedures and ICD-10-CM for diagnoses, checked against CMS NCCI procedure-to-procedure edits.")],
        "next": "claims-insurance",
    },
    "claims-insurance": {
        "demo": "claims-insurance", "card": 'Build, check, send and follow claims, including denials.',
        "specs": [('Claims queue', 'Every claim in one list with status, payer, amount, risk and AI confidence'), ('Statuses', 'Pending, submitted, held, denied, appealed and paid'), ('Import', 'Bring in existing claims from a CSV file'), ('Before sending', "Bundling, code format, required fields and the practice's billing setup are checked first"), ('Held claims', 'Anything missing holds the claim with the reason instead of sending it to be rejected'), ('Submission', 'Electronic claims through your clearinghouse connection'), ('Denials', "The payer's reason, plus an appeal letter drafted from it and the note"), ('Payments', "Posted payments per claim, so you can see what's still outstanding"), ('Billing activation', 'A guided setup of practice, provider and payer details before the first claim')],
        "nav": "Claims & Insurance", "eyebrow": "Claims & Insurance",
        "title": "From approved codes", "em": "to a clean claim.",
        "lede": "Althais assembles the claim, checks it before it leaves, sends it electronically and follows it to payment, including the denials.",
        "meta": "Althais Claims & Insurance builds and checks claims, submits them electronically, and tracks payments, denials and appeals.",
        "hero": [
            {"type": "claims", "eyebrow": "Claims overview · example", "claims": [("CHC-2026-00412", "Smith, John · 99291", "Submitted", ""), ("CHC-2026-00411", "Patel, R. · 99285", "Paid", ""),
                                                                                 ("CHC-2026-00409", "Torres, M. · 99283", "Denied", "w"), ("CHC-2026-00408", "Kim, S. · 99284", "Paid", "")]},
            {"type": "rows", "eyebrow": "Denied claims", "soft": True, "rows": [("Appeal letter for CHC-2026-00409", "ready", "g")]},
        ],
        "rows": [
            {"eyebrow": "Assemble", "title": "Everything in one claim,", "em": "ready to review.",
             "text": "Patient, payer, codes, diagnosis pointers and provider come together on a CMS-1500 claim. You read it once before it goes.",
             "cards": [{"type": "rows", "eyebrow": "CMS-1500 · example", "rows": [("Patient", "Demo Patient, 54", ""), ("Payer", "Verified at filing", ""), ("Lines", "99285 · R07.9, R06.00", ""), ("Rendering provider", "NPI on file", ""), ("Status", "Ready for review", "g")]}]},
            {"eyebrow": "Gate", "title": "Held, not bounced,", "em": "when something's missing.",
             "text": "Before a claim is sent, Althais checks the practice, provider and payer setup behind it. If something isn't ready, the claim is held with the reason, so your team fixes it instead of waiting for a rejection.",
             "cards": [{"type": "rows", "eyebrow": "Before sending", "rows": [("Required fields", "present", "g"), ("Provider enrollment", "active", "g"), ("Payer rules", "applied", ""), ("Timely filing", "on track", "g")]},
                       {"type": "rows", "eyebrow": "Held claim · example", "soft": True, "rows": [("Reason", "Missing referring NPI", "a"), ("Next step", "Add it, then resend", "")]}]},
            {"eyebrow": "Track", "title": "Followed to payment.", "em": "Denials come back ready.",
             "text": "Every claim is tracked from submission to payment. When one is denied, you see why, and an appeal letter is drafted from the denial reason and the clinical note.",
             "cards": [{"type": "checklist", "eyebrow": "CHC-2026-00409 · example", "items": [("Submitted electronically", True), ("Payer response received", True), ("Denial reason explained", True), ("Appeal letter drafted", True), ("Resubmitted", False)]}]},
        ],
        "grid": [("Claims builder", "CMS-1500 claims assembled from approved codes."), ("Checked first", "Problems caught before the payer sees them."),
                 ("Electronic submission", "Sent through the clearinghouse you already use."), ("Denials and appeals", "The reason shown, the letter drafted."),
                 ("Payments", "What's paid, pending and needs a look."), ("Billing activation", "Guided setup before the first claim.")],
        "faq": [("Do we need a new clearinghouse?", "No. Althais connects to the clearinghouse you already use."),
                ("What happens when a claim is denied?", "You see the reason and can draft an appeal letter from it and the clinical note."),
                ("Can a claim be held instead of sent?", "Yes. If something needs attention, the claim is held with the reason so your team can fix it first.")],
        "next": "althea",
    },
    "althea": {
        "demo": "althea", "card": 'An assistant that has read the visit you are looking at.',
        "specs": [('Where', 'One click away on every Althais page'), ('Context', 'Answers about the note, codes and claim on your screen'), ('Product help', 'How to do anything in Althais, in plain language'), ('Clinic control', 'Switch Althea on or off for the clinic, and hide her in chosen areas'), ('Plans', 'Available on any plan'), ('Themes', 'Follows light and dark mode')],
        "nav": "Althea Assistant", "eyebrow": "Althea Assistant",
        "title": "Ask Althea.", "em": "Get back to patients.",
        "lede": "Althea is the AI inside Althais. Ask how to do something, what a screen means, or what's at risk today, and get a plain answer in seconds.",
        "meta": "Althea is the AI assistant inside Althais: plain-language answers about your notes, codes, claims and the product itself.",
        "hero": [
            {"type": "chat", "eyebrow": "Althea", "chat": [("u", "Why was 99285 suggested for this visit?"),
                                                          ("a", "The note documents a high-complexity ED visit: chest pain with possible ACS, an EKG, labs and a cardiology consult. That meets the level for <mark>99285</mark>.")]},
        ],
        "rows": [
            {"eyebrow": "Ask anything", "title": "Ask the way you'd ask", "em": "a colleague.",
             "text": "No manuals to search and no tickets to file. Type your question in your own words and Althea answers in plain language, with where to go next.",
             "cards": [{"type": "chat", "eyebrow": "Althea", "chat": [("u", "How do I send a claim that was held?"), ("a", "Open <mark>Claims</mark>, fix the item listed in the hold reason, then press Resend. It goes out with the next batch.")]}]},
            {"eyebrow": "In context", "title": "It already read", "em": "the chart.",
             "text": "Althea reads the note, the codes and the claim you're looking at, so its answers are about this visit, not a generic help article.",
             "cards": [{"type": "rows", "eyebrow": "What Althea read · example", "rows": [("Note", "Chest pain visit, signed", ""), ("Codes", "99285 · R07.9 · R06.00", ""), ("Claim", "Ready for review", "g")]},
                       {"type": "chat", "eyebrow": "Althea", "soft": True, "chat": [("u", "Anything missing before I file?"), ("a", "Documentation is complete and there are <mark>0 bundling conflicts</mark>. It's ready.")]}]},
            {"eyebrow": "Your clinic decides", "title": "On where you want her,", "em": "off where you don't.",
             "text": "Althea can be switched on for any clinic, whatever plan it has. Managers can hide her in parts of Althais where they'd rather she not appear.",
             "cards": [{"type": "rows", "eyebrow": "Clinic settings · example", "rows": [("Althea", "On", "g"), ("In the EMR", "Shown", ""), ("In Revenue", "Shown", ""), ("In Staff Portal", "Hidden", "a")]}]},
        ],
        "grid": [("Plain answers", "No jargon, no manuals."), ("Knows Althais", "Every step, from note to claim."), ("Reads the visit", "Answers about what's on your screen."),
                 ("Always one click away", "Open her from any page."), ("Helps new staff", "Learn by asking."), ("Clinic controls", "On, off, or hidden by area.")],
        "faq": [("Is Althea available on every plan?", "Althea can be switched on for any clinic, whatever plan it has."),
                ("Can we turn her off?", "Yes. Each clinic controls whether Althea appears, and where."),
                ("Can I try her now?", "Yes. Open Althea from the bottom corner of the Althais home page.")],
        "next": "team-onboarding",
    },
    "team-onboarding": {
        "demo": "team-onboarding", "card": 'Invite staff, collect documents, train them and set access by role.',
        "specs": [('Invitations', 'Each invite creates a sign-in and a guided Staff Portal setup'), ('Documents', 'Licenses, certifications and IDs, with expiration dates tracked'), ('Forms', 'Policies and onboarding forms signed inside the portal'), ('Training', 'Althais Training: guided lessons, interactive practice and a quiz, by role'), ('Billing readiness', 'Billers finish billing training and forms before they can send claims'), ('Roles', 'Provider, biller, front desk and manager, each with adjustable access'), ('Portal lock', "Send someone back to their portal to finish training; access returns when they're done"), ('Audit', 'Onboarding steps and access changes are recorded')],
        "nav": "Team & Onboarding", "eyebrow": "Team & Onboarding",
        "title": "Your team,", "em": "set up right from day one.",
        "lede": "Invite staff, collect their documents, assign training, and give each person exactly the access their role needs, all in one place.",
        "meta": "Althais Team & Onboarding handles staff invitations, documents, credentials, training and role-based access for your clinic.",
        "hero": [
            {"type": "people", "eyebrow": "Team · example", "people": [("PR", "Priya Raman", "Provider", "Active", ""), ("BL", "Bea Lee", "Biller", "Training", "w"), ("FO", "Fay Ortiz", "Front Desk", "Onboarding", "w")]},
            {"type": "rows", "eyebrow": "Bea Lee · onboarding", "soft": True, "rows": [("Documents", "3 of 3", "g"), ("Billing training", "in progress", "a"), ("Access", "Staff Portal only", "")]},
        ],
        "rows": [
            {"eyebrow": "Invite", "title": "One invitation,", "em": "and they take it from there.",
             "text": "Add a person and choose their role. They get a sign-in and finish onboarding in their own Staff Portal: forms, documents and training, at their own pace.",
             "cards": [{"type": "checklist", "eyebrow": "Fay Ortiz · Staff Portal", "items": [("Accept the invitation", True), ("Upload ID and certifications", True), ("Sign policies", True), ("Complete Althais Training", False)]}]},
            {"eyebrow": "Train", "title": "Training that teaches", "em": "the real thing.",
             "text": "Althais Training walks each role through the product with guided lessons, interactive practice and a short quiz. Billers complete billing training before they can send claims.",
             "cards": [{"type": "rows", "eyebrow": "Althais Training · example", "rows": [("Tour the EMR", "done", "g"), ("Write and sign a note", "done", "g"), ("Practice a claim", "in progress", "a"), ("Final quiz", "not started", "")]}]},
            {"eyebrow": "Access", "title": "Exactly the access", "em": "each role needs.",
             "text": "Providers, billers, front desk and managers each get the permissions their job calls for. Managers can adjust roles, and can send someone back to their portal to finish training. Access returns automatically when they're done.",
             "cards": [{"type": "rows", "eyebrow": "Roles · example", "rows": [("Provider", "EMR, Scribe, Coding", ""), ("Biller", "Claims, Payments", ""), ("Front Desk", "Schedule, Patients", ""), ("Manager", "Everything + Team", "g")]}]},
        ],
        "grid": [("Invitations", "A sign-in and a guided setup for each person."), ("Documents", "Licenses and certifications, with expirations tracked."),
                 ("Althais Training", "Lessons, practice and a quiz by role."), ("Roles & permissions", "Access matched to each job."),
                 ("Billing readiness", "Training and forms before the first claim."), ("Pause access", "Send someone back to finish training.")],
        "faq": [("Do staff need an Althais account first?", "No. The invitation creates their sign-in and walks them through setup."),
                ("Can access be limited by role?", "Yes. Each role has its own permissions, and managers can adjust them."),
                ("What if someone hasn't finished training?", "A manager can lock them to their Staff Portal until it's done; access returns automatically when they finish.")],
        "next": "ai-scribe",
    },
}


def _view(slug):
    async def view(request: Request, user=Depends(current_user)):
        return render(request, user, PAGES[slug], f"/platform/{slug}", "Platform", NAV,
                      ctas=[("Try the demo", "#demo"), ("See the details", "#specs")])
    view.__name__ = "platform_" + slug.replace("-", "_")
    return view


for _slug in PAGES:
    router.add_api_route(f"/platform/{_slug}", _view(_slug), methods=["GET"], include_in_schema=False)

register("Platform", {f"/platform/{k}": v for k, v in PAGES.items()}, NAV)
