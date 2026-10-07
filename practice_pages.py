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

NAV = [("How It Works", "/how-it-works"), ("HIPAA Compliance", "/hipaa-compliance"), ("Security", "/security"),
       ("Pricing", "/pricing"), ("Compare Althais", "/compare")]


def _shot(name, alt, eyebrow):
    return {"type": "shot", "eyebrow": eyebrow, "src": f"/static/screenshots/{name}.png", "alt": alt}


PAGES = {
    "/how-it-works": {
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
        "nav": "Compare Althais", "eyebrow": "Compare Althais",
        "title": "One platform instead", "em": "of five disconnected tools.",
        "lede": "Most practices stitch together a scribe, a coder, a billing service and a stack of payer portals. Here's how that compares with Althais.",
        "meta": "Compare Althais with a typical setup of separate scribe, coding, billing and onboarding tools.",
        "hero": [
            {"type": "rows", "eyebrow": "Typical setup", "rows": [("Notes", "Scribe tool", ""), ("Codes", "Coding service", ""), ("Claims", "Billing software", ""), ("Denials", "Payer portals", "a"), ("Staff", "HR binders", "")]},
            {"type": "rows", "eyebrow": "With Althais", "soft": True, "rows": [("All of it", "One platform", "g")]},
        ],
        "table_head": ("Side by side", "Althais and", "the usual patchwork."),
        "table": {"cols": ["Althais", "Typical setup"], "rows": [
            ("Note from the visit", "AI Scribe drafts the SOAP note for you to sign", "Typed after hours, or a separate scribe tool"),
            ("Coding", "Suggested from the note, with confidence and reasons", "Manual, or a separate coding service"),
            ("Check before sending", "CMS edits and payer rules on every claim", "Problems found after a denial"),
            ("Denials and appeals", "Reason shown, appeal letter drafted", "Spreadsheets and payer portals"),
            ("Staff onboarding and training", "Built in, by role", "Separate HR tools and binders"),
            ("Help when you're stuck", "Althea, inside the app", "Support tickets and manuals"),
            ("Who approves codes", "Your team, every time", "Varies by vendor"),
        ]},
        "faq": [("Do we have to replace our EHR?", "No. Althais integrates with major EHRs and works with the clearinghouse you already use."),
                ("Can we use just part of Althais?", "Yes. Clinics choose the tools they use, from Scribe or Coding alone to the full suite."),
                ("How do we try it?", "Book a demo, then start with a pilot on your own documentation.")],
    },
}

_ORDER = [href for _, href in NAV]


def _view(href):
    async def view(request: Request, user=Depends(current_user)):
        nxt = _ORDER[(_ORDER.index(href) + 1) % len(_ORDER)]
        return platform_pages.render(request, user, PAGES[href], href, "For Your Practice", NAV, nxt, PAGES[nxt]["nav"])
    view.__name__ = "practice_" + href.strip("/").replace("-", "_")
    return view


for _href in PAGES:
    router.add_api_route(_href, _view(_href), methods=["GET"], include_in_schema=False)
