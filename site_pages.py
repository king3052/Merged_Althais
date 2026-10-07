"""
site_pages.py: the marketing pages linked from the site footer (Platform, For Your Practice, Solutions, Resources, Company).

Every page renders through templates/site_page.html from the PAGES entry below, so copy lives here and layout lives in one place.
Copy describes what Althais does today; the solution and case study pages reuse the stories already in marketing_data.py.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

from auth import current_user
from marketing_data import SOLUTION_SEGMENTS

router = APIRouter()
_templates = None   # set by main.py (shares its Jinja environment and globals)

SEGMENTS = {s["id"]: s for s in SOLUTION_SEGMENTS}
PILOT_CTA = {"title": "See it on your own visits.", "text": "Book a demo and we'll walk through Althais with your specialty, your payers and your workflow.",
             "primary": ("Book A Demo", "/demo"), "secondary": ("Join The Pilot", "/pilot")}

PAGES = {
    # ── Platform ──────────────────────────────────────────────────────────────
    "platform/ai-scribe": {
        "nav": "AI Scribe", "eyebrow": "Platform · AI Scribe",
        "title": "Say the visit.<br>Get the note.",
        "lede": "Record the encounter or paste a transcript. Althais drafts a structured SOAP note in your voice, ready for you to review and sign.",
        "meta": "Althais AI Scribe turns a recorded visit or transcript into a structured SOAP note you review, edit and sign.",
        "features": [
            ("Record or paste", "Capture the visit live, upload a recording, or paste a transcript from the tool you already use."),
            ("Structured SOAP notes", "Subjective, Objective, Assessment and Plan, organized the way a chart reviewer expects to read them."),
            ("Drafts you control", "Every note starts as a draft. Edit anything, then sign when it says what you meant."),
            ("Copy, download, chart", "Send the signed note to the chart, copy it into your EHR, or download it."),
            ("Straight into coding", "A finished note flows into Medical Coding, so codes are suggested the moment you sign."),
            ("Private by design", "Visit audio and notes are handled under HIPAA safeguards and stay within your clinic."),
        ],
        "steps": [("Capture", "Record the visit or bring in a transcript."), ("Draft", "Althais writes the SOAP note."),
                  ("Review", "Edit anything that needs your judgment."), ("Sign", "Sign it, and it's ready for coding and the chart.")],
        "faq": [("Does the scribe replace my judgment?", "No. It writes a draft. Nothing becomes part of the record until you review and sign it."),
                ("Can I use transcripts from another tool?", "Yes. Paste a transcript and Althais builds the note from it."),
                ("What happens after I sign?", "The note is ready for Medical Coding, which suggests CPT and ICD-10 codes with the reasons behind each one.")],
    },
    "platform/medical-coding": {
        "nav": "Medical Coding", "eyebrow": "Platform · Medical Coding",
        "title": "Codes you can defend,<br>with the reasons attached.",
        "lede": "Althais reads the note and suggests CPT and ICD-10 codes, each with a confidence level and the documentation that supports it. You approve every code.",
        "meta": "Althais Medical Coding suggests CPT and ICD-10 codes from the clinical note with confidence and source reasons, and checks codes before a claim goes out.",
        "features": [
            ("Code A Note", "Turn a finished note into CPT and ICD-10 suggestions in seconds."),
            ("Confidence on every code", "See how sure Althais is, so you know where to look closer."),
            ("Reasons, not guesses", "Each suggestion points to the words in the note that support it."),
            ("Check Codes", "Paste codes you already chose and Althais checks them against the documentation before the claim goes out."),
            ("Diagnosis pointers", "Procedures are linked to the diagnoses that justify them, the way payers expect."),
            ("A human approves every code", "Nothing is billed without an explicit decision from your team."),
        ],
        "steps": [("Note in", "A signed note arrives from AI Scribe or is pasted in."), ("Suggest", "CPT and ICD-10 codes with confidence and reasons."),
                  ("Review", "Accept, change or remove each code."), ("Send", "Approved codes move into the claim.")],
        "faq": [("Who is responsible for the final codes?", "Your team. Althais suggests and explains; a person approves every code before it reaches a claim."),
                ("Can it check codes I picked myself?", "Yes. Check Codes compares your codes with the note and flags anything the documentation doesn't support."),
                ("Which code sets does it use?", "CPT for procedures and ICD-10-CM for diagnoses.")],
    },
    "platform/claims-insurance": {
        "nav": "Claims & Insurance", "eyebrow": "Platform · Claims & Insurance",
        "title": "From approved codes<br>to a clean claim.",
        "lede": "Althais assembles the claim, checks it before it leaves, submits it electronically and keeps track of what comes back, including denials and appeals.",
        "meta": "Althais Claims & Insurance builds and checks claims, submits them electronically, and tracks denials, appeals and payments.",
        "features": [
            ("Claims builder", "Patient, payer, codes and diagnosis pointers come together in one claim, ready to review."),
            ("Checked before it's sent", "Each claim is checked for missing or mismatched details before it goes to the payer."),
            ("Electronic submission", "Claims are sent electronically through the clearinghouse connection your clinic sets up."),
            ("Denials and appeals", "See why a claim was denied and draft an appeal letter from the denial reason and the note."),
            ("Payments", "Follow what has been paid, what's pending and what needs a second look."),
            ("Billing activation", "A guided setup gets your practice, providers and payer enrollments ready before the first claim."),
        ],
        "steps": [("Assemble", "Codes and patient details become a claim."), ("Check", "Problems are caught before the payer sees them."),
                  ("Submit", "The claim goes out electronically."), ("Track", "Payments, denials and appeals in one place.")],
        "faq": [("Do we need a new clearinghouse?", "No. Althais connects to the clearinghouse you already use."),
                ("What happens when a claim is denied?", "You see the reason and can draft an appeal letter from it and the clinical note."),
                ("Can a claim be held instead of sent?", "Yes. If something needs attention, the claim is held with the reason so your team can fix it first.")],
    },
    "platform/althea": {
        "nav": "Althea Assistant", "eyebrow": "Platform · Althea",
        "title": "Ask Althea.<br>Get back to patients.",
        "lede": "Althea is the assistant built into Althais. Ask how to do something, where to find it, or what a screen means, and get a plain answer in seconds.",
        "meta": "Althea is the Althais assistant: ask questions about Althais and your workflow and get plain answers inside the app.",
        "features": [
            ("Answers in plain language", "Ask the way you'd ask a colleague. No manuals to search."),
            ("Knows Althais", "From writing a note to sending a claim, Althea explains each step of the product."),
            ("Always one click away", "Open Althea from any page without losing your place."),
            ("Helps new staff", "New team members get up to speed by asking instead of waiting for training."),
            ("Your clinic decides", "Clinics can turn Althea on or off and hide her where they'd rather she not appear."),
            ("Light or dark", "Althea follows your theme, so she fits in wherever you work."),
        ],
        "steps": [("Open", "Click Althea from any page."), ("Ask", "Type your question in your own words."),
                  ("Answer", "Get a clear answer and where to go next."), ("Keep going", "Follow up until it's done.")],
        "faq": [("Is Althea available on every plan?", "Althea can be switched on for any clinic, whatever plan it has."),
                ("Can we turn her off?", "Yes. Each clinic controls whether Althea appears."),
                ("Can I try her now?", "Yes. Open Althea from the bottom corner of the Althais home page.")],
    },
    "platform/team-onboarding": {
        "nav": "Team & Onboarding", "eyebrow": "Platform · Team & Onboarding",
        "title": "Your team, set up right<br>from day one.",
        "lede": "Invite staff, collect their documents, assign training and give each person the access their role needs, all from one place.",
        "meta": "Althais Team & Onboarding handles staff invitations, documents, credentials, training and role-based access for your clinic.",
        "features": [
            ("Invite your team", "Send each person an invitation. They finish onboarding in their own Staff Portal."),
            ("Documents and credentials", "Collect licenses, certifications and forms, with expiration dates tracked for you."),
            ("Althais Training", "Guided lessons, interactive practice and a quiz teach each role how to use Althais."),
            ("Roles and permissions", "Give providers, billers, front desk and managers exactly the access their job needs."),
            ("Billing readiness", "Billers complete billing training and forms before they can send claims."),
            ("Pause access when needed", "Managers can send someone back to their portal to finish training before returning to work."),
        ],
        "steps": [("Invite", "Add a person and choose their role."), ("Onboard", "They upload documents and sign forms."),
                  ("Train", "They complete Althais Training."), ("Activate", "Access opens for exactly what their role allows.")],
        "faq": [("Do staff need an Althais account first?", "No. The invitation creates their sign-in and walks them through setup."),
                ("Can access be limited by role?", "Yes. Each role has its own permissions, and managers can adjust them."),
                ("What if someone hasn't finished training?", "A manager can lock them to their Staff Portal until it's done; access returns automatically when they finish.")],
    },

    # ── For Your Practice ─────────────────────────────────────────────────────
    "hipaa-compliance": {
        "nav": "HIPAA Compliance", "features_title": "The safeguards behind Althais", "eyebrow": "For Your Practice · HIPAA",
        "title": "Built for protected<br>health information.",
        "lede": "Althais is designed around HIPAA's safeguards for protected health information: limited access, encryption and audit trails, with Business Associate Agreements available.",
        "meta": "How Althais protects patient health information: HIPAA safeguards, Business Associate Agreements, encryption, access controls and audit logs.",
        "features": [
            ("Business Associate Agreements", "BAAs are available on Enterprise plans. Ask us about yours during setup."),
            ("Encryption", "Data is encrypted in transit (TLS 1.2+) and at rest."),
            ("Least-privilege access", "Each person sees only what their role needs, and managers control who has access."),
            ("Audit trails", "Sign-ins and important actions are logged so you can see who did what and when."),
            ("Your data stays yours", "Patient information is used to run your clinic's work, not sold or shared."),
            ("Training built in", "Staff onboarding includes compliance training and acknowledgments."),
        ],
        "faq": [("Will Althais sign a BAA?", "Business Associate Agreements are available on Enterprise plans. Contact us to discuss your organization's requirements."),
                ("Who can see our patients' information?", "Only people in your clinic with a role that allows it. Althais staff access is limited and logged."),
                ("Where can I read more?", "See the Security page and our Privacy Policy, or contact us with your compliance team's questions.")],
        "links": [("Security", "/security"), ("Privacy Policy", "/legal/privacy"), ("Contact Us", "/contact")],
    },
    "compare": {
        "nav": "Compare Althais", "eyebrow": "For Your Practice · Compare",
        "title": "One platform instead of<br>five disconnected tools.",
        "lede": "Most practices stitch together a scribe, a coder, a billing service and a stack of payer portals. Here's how Althais compares.",
        "meta": "Compare Althais with a typical setup of separate scribe, coding, billing and onboarding tools.",
        "compare": {
            "cols": ["Althais", "Typical setup"],
            "rows": [
                ("Note from the visit", "AI Scribe drafts the SOAP note", "Typed after hours or a separate scribe tool"),
                ("Coding", "Suggested from the note, with reasons", "Manual, or a separate coding service"),
                ("Check before sending", "Every claim checked first", "Problems found after a denial"),
                ("Denials and appeals", "Reason shown, appeal drafted", "Spreadsheet and payer portals"),
                ("Staff onboarding and training", "Built in, by role", "Separate HR tools and binders"),
                ("Help when you're stuck", "Althea, inside the app", "Support tickets and manuals"),
                ("Who approves codes", "Your team, every time", "Varies"),
            ],
        },
    },

    "security": {
        "nav": "Security", "features_title": "How we protect your data", "eyebrow": "For Your Practice · Security",
        "title": "Security built into<br>every layer.",
        "lede": "Patient information deserves care at every step. Here's how Althais protects it.",
        "meta": "Althais security: encryption in transit and at rest, role-based access, audit logs, HIPAA-eligible handling of PHI and SOC 2 readiness.",
        "features": [
            ("HIPAA", "Infrastructure and workflows built around HIPAA-eligible handling of PHI, with BAAs available on Enterprise plans."),
            ("SOC 2 ready", "Access controls, change management and monitoring built to SOC 2 Trust Services criteria as we complete a formal audit."),
            ("Encryption", "Data is encrypted in transit (TLS 1.2+) and at rest."),
            ("Role-based access", "Every person gets only the access their role needs, set and adjusted by your clinic's managers."),
            ("Audit logs", "Sign-ins and consequential actions are recorded, including any time the Althais team views an account to help."),
            ("Sessions that expire", "Sign-ins time out, and temporary passwords must be changed on first use."),
        ],
        "faq": [("Does the Althais team see our patients' data?", "Only when needed to support you, and every such view is logged."),
                ("Can we control who sees what?", "Yes. Managers set each role's access and can pause or remove a person at any time."),
                ("Where can I read the legal details?", "See our Privacy Policy and Terms of Service, or contact us with your security team's questions.")],
        "links": [("HIPAA Compliance", "/hipaa-compliance"), ("Privacy Policy", "/legal/privacy"), ("Terms", "/legal/terms")],
    },

    # ── Resources ─────────────────────────────────────────────────────────────
    "case-studies": {
        "nav": "Case Studies", "eyebrow": "Resources · Case Studies",
        "title": "Practices that put<br>Althais to work.",
        "lede": "Stories from the kinds of organizations Althais is built for, in their own words.",
        "meta": "Case studies from practices, groups and health systems using Althais for coding and claims.",
        "case_studies": True,
    },
    "roi-calculator": {
        "nav": "ROI Calculator", "eyebrow": "Resources · ROI Calculator",
        "title": "What are denials<br>costing you?",
        "lede": "Enter your own numbers to see what denied claims and billing hours cost your practice today, and what you'd keep if you cut them.",
        "meta": "Estimate what claim denials and billing admin time cost your practice with the Althais ROI calculator.",
        "calculator": True,
    },
    "walkthrough": {
        "nav": "Walkthrough", "features_title": "Six stops, one visit", "eyebrow": "Resources · Walkthrough",
        "title": "One visit, start<br>to finish.",
        "lede": "Follow a single encounter through Althais, from the conversation in the exam room to the payment on the claim.",
        "meta": "A walkthrough of Althais: one patient visit from the recorded conversation to the SOAP note, codes, claim, submission and payment.",
        "features": [
            ("The visit", "The provider records the conversation or brings in a transcript. No typing during the visit."),
            ("The note", "AI Scribe drafts the SOAP note. The provider edits anything and signs it."),
            ("The codes", "Medical Coding suggests CPT and ICD-10 codes, each with confidence and the words in the note that support it."),
            ("The review", "The provider or coder accepts, changes or removes each code. Nothing moves on without that approval."),
            ("The claim", "The claim is assembled and checked before it leaves, then sent electronically through your clearinghouse."),
            ("The follow-through", "Payments are tracked; denials come back with the reason and an appeal letter ready to draft."),
        ],
        "links": [("AI Scribe", "/platform/ai-scribe"), ("Medical Coding", "/platform/medical-coding"), ("Claims & Insurance", "/platform/claims-insurance")],
        "faq": [("How long does the whole flow take?", "Most of it happens as soon as the note is signed; review fits between patients."),
                ("Who approves what?", "Providers sign notes and your team approves every code before a claim goes out."),
                ("Can I see it live?", "Yes. Book a demo and we'll walk through it with your own specialty and payers.")],
    },
    "faq": {
        "nav": "FAQs", "eyebrow": "Resources · FAQs",
        "title": "Questions,<br>answered.",
        "lede": "The things practices ask most before getting started with Althais.",
        "meta": "Frequently asked questions about Althais: pricing, implementation, integrations, security and how coding approval works.",
        "faq_groups": [
            ("Getting started", [
                ("How long does implementation take?", "Most organizations are live within 30 to 60 days: integrations first, then tuning, then a provider pilot before full rollout."),
                ("Can we start with a pilot?", "Yes. Every implementation includes a pilot so your team can validate Althais on real documentation first."),
                ("Do we need a new clearinghouse?", "No. Althais connects to the clearinghouse you already use."),
            ]),
            ("Coding and claims", [
                ("Who's responsible if a suggested code is wrong?", "The rendering provider approves every code before it reaches a claim. Althais shows confidence and reasons, but nothing bills without a human decision."),
                ("What happens to denied claims?", "You see the denial reason and can draft an appeal letter from it and the note."),
                ("Can Althais check codes we chose ourselves?", "Yes. Check Codes compares them against the documentation and flags gaps."),
            ]),
            ("Security and pricing", [
                ("Is my data secure?", "Althais is built around HIPAA-eligible handling of PHI: data is encrypted in transit and at rest, access is role-based, and important actions are logged. BAAs are available on Enterprise plans."),
                ("How is pricing structured?", "Pricing follows claim volume and provider count, not a flat per-seat fee. Implementation, training and support are included."),
                ("Can we turn features on and off?", "Yes. Clinics choose the tools they use, and managers control what each role can access."),
            ]),
        ],
    },

    # ── Company ───────────────────────────────────────────────────────────────
    "careers": {
        "nav": "Careers", "features_title": "Why Althais", "eyebrow": "Company · Careers",
        "title": "Help clinicians spend<br>more time with patients.",
        "lede": "We're a small team building software that takes paperwork off clinicians' plates. If that's work you want to do, we'd like to hear from you.",
        "meta": "Careers at Althais: help build software that gives clinicians time back for patient care.",
        "features": [
            ("Work that matters", "Every hour Althais saves goes back to patient care."),
            ("Small team, real ownership", "You'll ship things clinicians use the same week."),
            ("Built with clinicians", "We design alongside the providers and billers who use Althais every day."),
        ],
        "cta": {"title": "Don't see a listing?", "text": "Tell us what you'd like to work on. We read every note.",
                "primary": ("Get In Touch", "/contact"), "secondary": ("Our Story", "/about")},
    },
}

# Solutions: one page per segment, built from the Solutions page copy
for _slug in ("private-practices", "hospital-systems", "revenue-cycle-teams"):
    _s = SEGMENTS[_slug]
    PAGES[f"solutions/{_slug}"] = {
        "nav": _s["label"], "eyebrow": f"Solutions · {_s['label']}", "title": _s["headline"], "lede": "",
        "meta": f"Althais for {_s['label']}: {_s['headline']}", "segment": _s, "title_plain": True,
    }


def _case_studies():
    return [{"label": s["label"], "id": s["id"], **s["case_study"], "stats": s.get("before_after", [])[:3]}
            for s in SOLUTION_SEGMENTS if s.get("case_study")]


def _render(request: Request, slug: str, user):
    page = PAGES[slug]
    ctx = {"user": user, "active_page": slug, "page": page, "slug": slug, "cta": page.get("cta", PILOT_CTA)}
    if page.get("case_studies"):
        ctx["cases"] = _case_studies()
    return _templates.TemplateResponse(request, "site_page.html", ctx)


def _route(slug):
    async def view(request: Request, user=Depends(current_user)):
        return _render(request, slug, user)
    view.__name__ = "site_page_" + slug.replace("/", "_").replace("-", "_")
    return view


for _slug in PAGES:
    router.add_api_route("/" + _slug, _route(_slug), methods=["GET"], include_in_schema=False)


@router.get("/our-story", include_in_schema=False)
async def our_story():
    return RedirectResponse(url="/about", status_code=302)
