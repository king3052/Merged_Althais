"""
practice_pages.py: the "For Your Practice" pages (How It Works, HIPAA Compliance, Security, Pricing, Compare Althais),
in the landing page's style. Rendered by platform_pages.render with templates/platform_page.html.

Copy matches what the site already says: the How It Works steps and screenshots, the Pricing tiers, and the security
wording (BAAs available on Enterprise plans, SOC 2 readiness rather than certification).
"""

from fastapi import APIRouter, Depends, Request

import platform_pages
from auth import current_user

router = APIRouter()

NAV = [("How It Works", "/how-it-works"), ("Compare Althais", "/compare"), ("Security", "/security"),
       ("Pricing", "/pricing"), ("HIPAA Compliance", "/hipaa-compliance")]


def _shot(name, alt, eyebrow):
    return {"type": "shot", "eyebrow": eyebrow, "src": f"/static/screenshots/{name}.png", "alt": alt}


PAGES = {
    "/how-it-works": {
        "ctas": [("Walk through the eight steps", "#rows"), ("Try the product demos", "/platform/ai-scribe#demo")],
        "card": "One encounter from the note to the payment, in eight steps.",
        "timeline_head": ("Going live", "Live in 30 to 60 days,", "with a pilot first."),
        "timeline": [("Week 1", "Connect", "Your EHR and clearinghouse are connected, and practice, provider and payer details are set up."),
                     ("Weeks 2 to 3", "Tune", "Coding is tuned to your specialty mix, roles are set, and your team is invited and trained."),
                     ("Pilot", "Prove it", "A small group of providers uses Althais on real documentation while you check the results."),
                     ("Rollout", "Go live", "Everyone moves over, with support included as you grow.")],
        "specs_label": "Who does what", "specs_title": "Everyone keeps their job. The busywork goes.",
        "specs": [("Providers", "Record the visit, review the drafted note, sign it, and approve or change the codes"),
                  ("Coders", "Review suggested codes with their confidence and reasons, and run the code check"),
                  ("Billers", "Work the claims queue, fix held claims, send appeals and post payments"),
                  ("Front desk", "Schedule and register patients so visits start with the right details"),
                  ("Managers", "Invite staff, set role access, and see the whole practice's claims"),
                  ("Althais", "Drafts notes, suggests codes, runs the checks, assembles claims and tracks them")],
        "nav": "How It Works", "eyebrow": "How It Works",
        "title": "From the visit", "em": "to the payment.",
        "lede": "Follow one encounter through Althais: the note, the codes, your review, the claim, the checks, and the money coming back.",
        "meta": "Follow a single encounter through Althais: the provider note, AI coding, provider review, claim assembly, payer checks, submission, tracking and payment.",
        "hero": [
            {"type": "rows", "eyebrow": "One encounter · example", "rows": [("Note", "Signed", "g"), ("Codes", "99285 · R07.9 · R06.00", ""), ("Review", "Approved by provider", "g"),
                                                                           ("Claim", "CMS-1500 assembled", ""), ("Status", "Paid", "g")]},
            {"type": "chips", "eyebrow": "Eight steps", "soft": True, "tags": [("Note", ""), ("Codes", ""), ("Review", ""), ("Claim", ""), ("Checks", ""), ("Submit", ""), ("Track", ""), ("Paid", "")]},
        ],
        "rows_head": ("Eight steps", "One encounter,", "start to finish."),
        "rows": [
            {"eyebrow": "Provider note", "title": "It starts with", "em": "the note.",
             "text": "Clinical documentation is captured as-is: SOAP notes, dictation, or text from your EHR.",
             "cards": [_shot("provider-note", "The SOAP note entry screen in Althais, with chief complaint, HPI, vitals and assessment and plan", "Write a note")]},
            {"eyebrow": "AI coding", "title": "Codes come from", "em": "the documentation.",
             "text": "Diagnoses and procedures are pulled from the note as ICD-10 and CPT codes, with the right specificity and modifiers, and a confidence score on each.",
             "cards": [_shot("ai-coding", "AI-suggested ICD-10 and CPT codes generated from the clinical note, with confidence scores", "Suggested codes")]},
            {"eyebrow": "Provider review", "title": "A person decides", "em": "on every code.",
             "text": "The rendering provider accepts, edits or rejects each suggested code. Nothing moves forward without that decision.",
             "cards": [_shot("provider-review", "A provider accepting AI-suggested codes in Althais", "Review codes")]},
            {"eyebrow": "Claim assembly", "title": "The claim", "em": "builds itself.",
             "text": "Approved codes, modifiers and encounter details are assembled into a CMS-1500 claim, ready to read once before it goes.",
             "cards": [_shot("claim-assembly", "A CMS-1500 claim form assembled from the approved codes", "Claim form")]},
            {"eyebrow": "Payer checks", "title": "Checked before", "em": "it leaves the building.",
             "text": "Each claim is checked against CMS edits and payer-specific rules, so problems are fixed before a denial instead of after.",
             "cards": [_shot("payer-intelligence", "Payer intelligence showing tracked payers, approval rates and payer-specific billing rules", "Payer intelligence")]},
            {"eyebrow": "Submission", "title": "Sent through", "em": "your clearinghouse.",
             "text": "The clean claim is transmitted electronically through the clearinghouse you already use.",
             "cards": [_shot("submission", "Claim transmission confirmation with the EDI transaction details and status", "Submitted")]},
            {"eyebrow": "Tracking", "title": "Every status,", "em": "as it changes.",
             "text": "Accepted, pending, denied: updates flow back as they happen. Denials arrive with the reason and an appeal letter ready to draft.",
             "cards": [_shot("claim-tracking", "Claims overview with live status: paid, submitted, denied and pending review", "Claims overview")]},
            {"eyebrow": "Payment", "title": "The loop closes", "em": "when it's paid.",
             "text": "Payments are reconciled and posted against each claim, so you can see what came in and what still needs attention.",
             "cards": [_shot("payment", "Claims filtered to paid, showing posted payment amounts", "Payments")]},
        ],
        "faq": [("How long does implementation take?", "Most organizations are live within 30 to 60 days: integrations first, then tuning, then a provider pilot before full rollout."),
                ("Who approves the codes?", "The rendering provider approves every code before it reaches a claim."),
                ("Do we need a new clearinghouse?", "No. Althais works with the clearinghouse you already use.")],
    },
    "/hipaa-compliance": {
        "ctas": [("Who handles what", "#compare-tiers"), ("Read the security details", "/security")],
        "card": "The safeguards behind Althais and what your clinic controls.",
        "matrix_head": ("Shared responsibility", "What Althais handles,", "and what stays with you."),
        "matrix": {"cols": ["Althais", "Your clinic"], "groups": [
            ("Protecting data", [("Encryption in transit and at rest", ["Handles", ""]), ("Secure infrastructure for PHI", ["Handles", ""]), ("Logging sign-ins and key actions", ["Handles", ""])]),
            ("Access", [("Role-based permissions", ["Provides", "Sets them"]), ("Inviting and removing staff", ["Provides", "Decides"]), ("Pausing a person's access", ["Provides", "Decides"])]),
            ("People and paperwork", [("Business Associate Agreement", ["Enterprise plans", "Signs"]), ("Compliance training", ["Provides", "Assigns"]), ("Patient consent to record", ["Asks every time", "Obtains"])]),
        ]},
        "nav": "HIPAA Compliance", "eyebrow": "HIPAA Compliance",
        "title": "Built for protected", "em": "health information.",
        "lede": "Althais is designed around HIPAA's safeguards: least-privilege access, encryption and audit trails, with Business Associate Agreements available.",
        "meta": "How Althais protects patient health information: HIPAA safeguards, Business Associate Agreements, encryption, access controls and audit logs.",
        "hero": [
            {"type": "checklist", "eyebrow": "HIPAA safeguards", "items": [("Access limited by role", True), ("Encrypted in transit and at rest", True), ("Actions logged", True), ("Staff trained on compliance", True)]},
            {"type": "rows", "eyebrow": "Business Associate Agreement", "soft": True, "rows": [("Availability", "Enterprise plans", ""), ("When", "During setup", "")]},
        ],
        "rows": [
            {"eyebrow": "Access", "title": "Each person sees", "em": "only what they need.",
             "text": "Providers, billers, front desk and managers each get the access their job calls for. Managers can change a role, pause a person, or remove them at any time.",
             "cards": [{"type": "rows", "eyebrow": "Roles · example", "rows": [("Provider", "Their patients and notes", ""), ("Biller", "Claims and payments", ""), ("Front Desk", "Schedule and intake", ""), ("Manager", "Everything + team", "g")]}]},
            {"eyebrow": "Accountability", "title": "Who did what,", "em": "and when.",
             "text": "Sign-ins and consequential actions are recorded, including any time the Althais team views an account to help you.",
             "cards": [{"type": "rows", "eyebrow": "Activity log · example", "rows": [("09:12", "Dr. Raman signed in", ""), ("09:40", "Note signed · visit 4471", ""), ("10:05", "Claim approved by biller", ""), ("11:20", "Support view, logged", "a")]}]},
            {"eyebrow": "People", "title": "Compliance is part", "em": "of onboarding.",
             "text": "Staff onboarding includes compliance training and policy acknowledgments, so everyone who touches patient information has done both.",
             "cards": [{"type": "checklist", "eyebrow": "New staff · example", "items": [("HIPAA training", True), ("Policy acknowledgment", True), ("Role-based access granted", True)]}]},
        ],
        "grid": [("Business Associate Agreements", "Available on Enterprise plans."), ("Encryption", "In transit (TLS 1.2+) and at rest."), ("Least privilege", "Access matched to each role."),
                 ("Audit trails", "Sign-ins and key actions are logged."), ("Your data stays yours", "Used to run your clinic's work, not sold."), ("Training built in", "Compliance training for every hire.")],
        "grid_label": "The safeguards",
        "links": [("Security", "/security"), ("Privacy Policy", "/legal/privacy"), ("Contact Us", "/contact")],
        "faq": [("Will Althais sign a BAA?", "Business Associate Agreements are available on Enterprise plans. Contact us to discuss your organization's requirements."),
                ("Who can see our patients' information?", "Only people in your clinic with a role that allows it. Althais staff access is limited and logged."),
                ("Where can I read more?", "See the Security page and our Privacy Policy, or contact us with your compliance team's questions.")],
    },
    "/security": {
        "ctas": [("See the details", "#specs"), ("HIPAA compliance", "/hipaa-compliance")],
        "card": "Encryption, access control, audit logs and SOC 2 readiness.",
        "specs_title": "How Althais is secured.",
        "specs": [("In transit", "TLS 1.2 or newer on every connection"), ("At rest", "Data encrypted where it is stored"),
                  ("Access", "Role-based; managers grant and remove access"), ("Sign-in", "Sessions time out; temporary passwords must be changed on first use"),
                  ("Paused accounts", "Can't sign in until a manager restores them"), ("Support access", "Althais staff viewing an account is time-limited and logged"),
                  ("Audit trail", "Sign-ins, consequential actions and access changes are recorded"), ("SOC 2", "Controls built to Trust Services criteria; formal audit in progress")],
        "nav": "Security", "eyebrow": "Security",
        "title": "Security built into", "em": "every layer.",
        "lede": "Patient information deserves care at every step. Here's how Althais protects it.",
        "meta": "Althais security: encryption in transit and at rest, role-based access, audit logs, HIPAA-eligible handling of PHI and SOC 2 readiness.",
        "hero": [
            {"type": "rows", "eyebrow": "Security posture", "rows": [("Encryption in transit", "TLS 1.2+", "g"), ("Encryption at rest", "On", "g"), ("Access", "Role-based", ""), ("Audit log", "On", "g"), ("SOC 2", "Audit in progress", "a")]},
        ],
        "rows": [
            {"eyebrow": "Data", "title": "Encrypted,", "em": "everywhere it goes.",
             "text": "Data is encrypted in transit with TLS 1.2 or newer and encrypted at rest, on infrastructure built for HIPAA-eligible handling of PHI.",
             "cards": [{"type": "rows", "eyebrow": "Data protection", "rows": [("In transit", "TLS 1.2+", "g"), ("At rest", "Encrypted", "g"), ("PHI handling", "HIPAA-eligible", "")]}]},
            {"eyebrow": "Sign-in", "title": "Sessions that expire,", "em": "passwords that change.",
             "text": "Sign-ins time out, temporary passwords must be replaced on first use, and paused accounts can't sign in at all.",
             "cards": [{"type": "checklist", "eyebrow": "Account safeguards", "items": [("Sessions time out", True), ("Temporary passwords replaced on first sign-in", True), ("Paused accounts blocked", True)]}]},
            {"eyebrow": "Controls", "title": "Built to SOC 2", "em": "Trust Services criteria.",
             "text": "Access controls, change management and monitoring are built to SOC 2 Trust Services criteria as we complete a formal audit.",
             "cards": [{"type": "rows", "eyebrow": "SOC 2 readiness", "rows": [("Access controls", "In place", "g"), ("Change management", "In place", "g"), ("Monitoring", "In place", "g"), ("Formal audit", "In progress", "a")]}]},
        ],
        "grid": [("HIPAA", "HIPAA-eligible handling of PHI, with BAAs on Enterprise plans."), ("SOC 2 ready", "Built to Trust Services criteria; formal audit in progress."), ("Encryption", "TLS 1.2+ in transit, encrypted at rest."),
                 ("Role-based access", "Set and adjusted by your managers."), ("Audit logs", "Including any Althais support view."), ("Session safety", "Timeouts and forced password changes.")],
        "grid_label": "How we protect your data",
        "links": [("HIPAA Compliance", "/hipaa-compliance"), ("Privacy Policy", "/legal/privacy"), ("Terms", "/legal/terms")],
        "faq": [("Does the Althais team see our patients' data?", "Only when needed to support you, and every such view is logged."),
                ("Can we control who sees what?", "Yes. Managers set each role's access and can pause or remove a person at any time."),
                ("Is Althais SOC 2 certified?", "Not yet. Our controls are built to SOC 2 Trust Services criteria and we're completing a formal audit.")],
    },
    "/pricing": {
        "ctas": [("Compare the tiers", "#compare-tiers"), ("Estimate your ROI", "/roi-calculator")],
        "card": "Three tiers priced around claim volume, not seats.",
        "matrix_head": ("Compare tiers", "Everything in each tier,", "side by side."),
        "matrix": {"cols": ["Starter", "Professional", "Enterprise"], "groups": [
            ("Coding and claims", [("AI medical coding (ICD-10 + CPT)", [True, True, True]), ("Claims builder", [True, True, True]), ("Prior authorization support", [False, True, True])]),
            ("Revenue intelligence", [("Core revenue analytics", [True, True, True]), ("Payer intelligence engine", [False, True, True]), ("Advanced analytics & benchmarking", [False, True, True])]),
            ("Team and scale", [("Multi-provider role management", [False, True, True]), ("Providers", ["", "", "Unlimited"]), ("SSO & API access", [False, False, True]), ("Custom EHR & clearinghouse integrations", [False, False, True]), ("Custom-tuned AI for your specialty mix", [False, False, True])]),
            ("Support and security", [("Implementation, training and support", [True, True, True]), ("Email + chat support", [True, True, True]), ("Dedicated customer success manager", [False, False, True]), ("HIPAA-eligible infrastructure & BAA", [False, False, True])]),
        ]},
        "nav": "Pricing", "eyebrow": "Pricing",
        "title": "Priced around", "em": "your claim volume.",
        "lede": "Pricing follows claim volume and provider count, not a flat per-seat fee. Every tier includes implementation, training and support.",
        "meta": "Althais pricing scales from small practices to full health systems: Starter, Professional and Enterprise tiers, priced around claim volume.",
        "hero": [
            {"type": "rows", "eyebrow": "Every tier includes", "rows": [("Implementation", "Included", "g"), ("Training", "Included", "g"), ("Support", "Included", "g"), ("Pilot before rollout", "Included", "g")]},
            {"type": "chips", "eyebrow": "Priced by", "soft": True, "chips": ["Claim volume", "Provider count"]},
        ],
        "tiers": [
            {"for": "Small Practices", "name": "Starter", "text": "For independent practices getting AI coding and claims in place for the first time.",
             "button": "Contact Sales", "href": "/demo", "features": ["AI medical coding (ICD-10 + CPT)", "Claims builder", "Core revenue analytics", "Email + chat support"]},
            {"for": "Growing Organizations", "name": "Professional", "featured": True, "text": "For multi-provider groups that need payer intelligence and prior auth support built in.",
             "button": "Book A Demo", "href": "/demo", "plus": "Everything in Starter, plus:",
             "features": ["Payer intelligence engine", "Prior authorization support", "Advanced analytics & benchmarking", "Multi-provider role management"]},
            {"for": "Health Systems", "name": "Enterprise", "text": "For hospital systems and large groups that need unlimited scale and dedicated support.",
             "button": "Get A Quote", "href": "/demo", "plus": "Everything in Professional, plus:",
             "features": ["Unlimited providers", "Custom EHR & clearinghouse integrations", "Dedicated customer success manager", "SSO & API access", "HIPAA-eligible infrastructure & BAA", "Custom-tuned AI models for your specialty mix"]},
        ],
        "tiers_note": 'Not sure where you fit? <a href="/roi-calculator" style="text-decoration:underline">Estimate what denials cost you</a>, then talk to us for a quote.',
        "faq": [("How is pricing structured?", "Pricing is based on claim volume and provider count, not a flat per-seat fee. Every tier includes implementation, training and support."),
                ("How long does implementation take?", "Most organizations are live within 30 to 60 days, with a provider pilot before full rollout."),
                ("Can we start with a pilot?", "Yes. Every implementation includes a pilot so your team can validate Althais on real documentation first."),
                ("Does Althais work with our EHR and clearinghouse?", "Yes. Althais connects to the clearinghouse you already use and integrates with major EHRs. Enterprise plans include custom integration work.")],
    },
    "/compare": {
        "ctas": [("See the reasons", "#rows"), ("Side by side", "#compare")],
        "card": "Why Althais beats the usual EMR setup.",
        "nav": "Compare Althais", "eyebrow": "Compare Althais",
        "title": "Built for the claim,", "em": "not just the chart.",
        "lede": "Most EMRs were built to store records. Althais was built to turn a visit into a clean, paid claim, with the note, the codes and the checks in one flow.",
        "meta": "How Althais compares with traditional EMRs: one flow from note to paid claim, codes with reasons, checks before sending, drafted appeals, and an assistant that scribes the visit.",
        "hero": [
            {"type": "rows", "eyebrow": "A traditional EMR, typically", "rows": [("Note", "Typed, or a separate scribe add-on", ""), ("Codes", "Picked by hand or a coding vendor", ""), ("Checks", "Often at the clearinghouse", "a"), ("Denials", "Worked in payer portals", "a")]},
            {"type": "rows", "eyebrow": "Althais", "soft": True, "rows": [("Note → codes → claim", "One flow", "g"), ("Checks", "Before it leaves", "g")]},
        ],
        "rows_head": ("The reasons", "Ten reasons practices", "choose Althais."),
        "rows": [
            {"eyebrow": "One flow", "title": "Note, codes and claim", "em": "in one place.",
             "text": "In many EMRs the scribe, the coding and the claim scrubber are separate modules or separate vendors. In Althais a signed note becomes suggested codes and then a claim, without retyping anything.",
             "cards": [{"type": "checklist", "eyebrow": "One visit in Althais", "items": [("Visit recorded, note drafted", True), ("Codes suggested from the note", True), ("Claim assembled and checked", True), ("Sent through your clearinghouse", True)]}]},
            {"eyebrow": "Explainable coding", "title": "Every code shows", "em": "its reasons.",
             "text": "Each suggested code comes with a confidence score and the words in the note that support it, so your team can defend it. Nothing is billed until a person approves it.",
             "cards": [{"type": "codes", "eyebrow": "Suggested · example", "codes": [("CPT", "99214", "Moderate MDM", "92%"), ("ICD-10", "J18.1", "Lobar pneumonia", "94%"), ("ICD-10", "E11.65", "T2DM, hyperglycemia", "93%")]}]},
            {"eyebrow": "Checked first", "title": "Problems caught", "em": "before the payer sees them.",
             "text": "Claims are checked against CMS NCCI bundling edits, code formats and required fields before sending. If the practice isn't set up to bill a payer yet, the claim is held with the exact reason instead of bouncing back weeks later.",
             "cards": [{"type": "rows", "eyebrow": "Before sending", "rows": [("NCCI bundling", "0 conflicts", "g"), ("Code format", "valid", "g"), ("Payer enrollment", "active", "g"), ("Missing items", "held with a reason", "a")]}]},
            {"eyebrow": "Althea", "title": "An assistant that", "em": "scribes the visit.",
             "text": "Say “scribe my visit” and Althea listens to the encounter, writes the note into the chart, gets the codes and takes your sign-off by voice. She also reads back allergies, medications and claim status on request.",
             "cards": [{"type": "chat", "eyebrow": "Althea", "chat": [("u", "Althea, scribe my visit with Maria Alvarez."), ("a", "Scribing the visit. I'll stay quiet and listen. Say <mark>end visit</mark> when it's over.")]}]},
        ],
        "grid_label": "And six more",
        "grid": [("Keep your EHR, or replace it", "Add Althais on top of the EHR you already use, or run everything on the Full Suite. No rip-and-replace required."),
                 ("Denials come back ready", "The payer's reason is shown and an appeal letter is drafted from it and the note."),
                 ("Time-based codes, exactly", "Critical care thresholds, office time ranges and prolonged services are computed, not guessed."),
                 ("Staff onboarding built in", "Invitations, credentials, Althais Training and role-based access, instead of separate HR tools."),
                 ("Live in weeks, with a pilot", "Most practices are live in 30 to 60 days, starting with a provider pilot on real visits."),
                 ("Priced by claim volume", "Not a flat fee per seat. Implementation, training and support are included.")],
        "table_head": ("Side by side", "Althais and", "a traditional EMR."),
        "table": {"cols": ["Althais", "Traditional EMR, typically"], "rows": [
            ("Writing the note", "AI Scribe or Althea drafts the SOAP note for you to sign", "Typed by the provider, or a scribe add-on bought separately"),
            ("Choosing codes", "Suggested from the note, with confidence and the supporting text", "Picked by hand, or sent to a coding team or vendor"),
            ("Time-based codes", "Computed from documented minutes (critical care, office time, prolonged services)", "Looked up and applied by hand"),
            ("Bundling checks", "CMS NCCI edits run before the claim leaves", "Often caught by the clearinghouse or after a denial"),
            ("Not ready to bill a payer", "Claim held with the exact reason and next step", "Rejected or denied later"),
            ("Denials and appeals", "Reason shown, appeal letter drafted", "Worked by hand in payer portals"),
            ("Help in the moment", "Althea answers about the visit on screen, by voice or text", "Help articles and support tickets"),
            ("Staff onboarding", "Invites, credentials, training and roles built in", "Separate HR and credentialing tools"),
            ("Switching", "Works on top of your EHR, or replaces it; live in 30 to 60 days", "Full EMR changes are often long, all-at-once projects"),
        ]},
        "timeline_head": ("Switching", "Moving over", "without starting over."),
        "timeline": [("Step 1", "Keep or replace", "Add Althais on top of your EHR, or move everything to the Full Suite."),
                     ("Step 2", "Connect", "Your EHR and clearinghouse are connected and your payers set up."),
                     ("Step 3", "Pilot", "A few providers use it on real visits while the old way keeps running."),
                     ("Step 4", "Retire the extras", "Drop the separate scribe, coding and denial tools you no longer need.")],
        "faq": [("Do we have to replace our EMR?", "No. Althais can sit on top of the EHR you already use and handle notes, coding and claims, or you can run the whole practice on the Full Suite."),
                ("Does Althais work with Epic, athenahealth, eClinicalWorks or NextGen?", "Althais connects to major EHRs and the clearinghouse you already use. Tell us your setup in a demo and we'll show you exactly how it fits."),
                ("Who is responsible for the codes?", "Your team. Althais suggests codes with reasons; a person approves every one before it reaches a claim."),
                ("Can we try it first?", "Yes. Start with a pilot on your own documentation before rolling it out.")],
    },
}

def _view(href):
    async def view(request: Request, user=Depends(current_user)):
        return platform_pages.render(request, user, PAGES[href], href, "For Your Practice", NAV)
    view.__name__ = "practice_" + href.strip("/").replace("-", "_")
    return view


for _href in PAGES:
    router.add_api_route(_href, _view(_href), methods=["GET"], include_in_schema=False)

platform_pages.register("For Your Practice", PAGES, NAV)
