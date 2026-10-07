"""
more_pages.py: the Solutions, Resources and Company pages from the site footer, in the landing page's style.
Rendered by platform_pages.render with templates/platform_page.html (same template as Platform and For Your Practice).

Copy comes from what the site already says: the Solutions segments and their stories (marketing_data.SOLUTION_SEGMENTS),
the resource library (marketing_data.RESOURCE_ARTICLES), and the About page (two co-founders). Book A Demo (/demo) and
Join The Pilot (/pilot) keep their own pages because they carry the sign-up forms.
"""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import RedirectResponse

import platform_pages
from auth import current_user
from marketing_data import RESOURCE_ARTICLES, RESOURCE_CATEGORIES, SOLUTION_SEGMENTS

router = APIRouter()
SEG = {s["id"]: s for s in SOLUTION_SEGMENTS}
OWN_PAGE = ("private-practices", "hospital-systems", "revenue-cycle-teams")

SECTIONS = {
    "Solutions": [("Private Practices", "/solutions/private-practices"), ("Hospital Systems", "/solutions/hospital-systems"),
                  ("Revenue Cycle Teams", "/solutions/revenue-cycle-teams"), ("All Solutions", "/solutions")],
    "Resources": [("Resources", "/resources"), ("Case Studies", "/case-studies"), ("Walkthrough", "/walkthrough"),
                  ("ROI Calculator", "/roi-calculator"), ("FAQs", "/faq")],
    "Company": [("Our Story", "/about"), ("Careers", "/careers"), ("Contact Us", "/contact"), ("Book A Demo", "/demo"), ("Join The Pilot", "/pilot")],
}


def _seg_href(seg_id):
    return f"/solutions/{seg_id}" if seg_id in OWN_PAGE else f"/solutions#{seg_id}"


FIT = {  # which tier and tools each kind of organization usually starts with (tiers as on /pricing)
    "private-practices": ("Starter", "AI coding and the claims builder, with Scribe to end after-hours notes.", ["AI Scribe", "Medical Coding", "Claims & Insurance"]),
    "hospital-systems": ("Enterprise", "Unlimited providers, custom EHR and clearinghouse integrations, SSO and a dedicated success manager.", ["Medical Coding", "Claims & Insurance", "Team & Onboarding"]),
    "multi-specialty-groups": ("Professional", "Specialty-aware coding, payer intelligence and multi-provider roles.", ["Medical Coding", "Claims & Insurance"]),
    "urgent-care": ("Professional", "Fast E/M leveling and same-day claims for high volume.", ["AI Scribe", "Medical Coding", "Claims & Insurance"]),
    "fqhc": ("Professional", "Payer intelligence tuned to Medicaid managed care, with audit-ready logs.", ["Medical Coding", "Claims & Insurance"]),
    "specialty-clinics": ("Professional", "Modifier checks and specialty coding logic.", ["Medical Coding", "Claims & Insurance"]),
    "revenue-cycle-teams": ("Professional", "Payer intelligence, prior auth support and analytics across the team.", ["Claims & Insurance", "Medical Coding", "Team & Onboarding"]),
    "medical-billers": ("Starter", "One claims queue, drafted appeals and payer rules in one place.", ["Claims & Insurance", "Althea Assistant"]),
}
TOOL_HREF = {"AI Scribe": "/platform/ai-scribe#demo", "Medical Coding": "/platform/medical-coding#demo", "Claims & Insurance": "/platform/claims-insurance#demo",
             "Team & Onboarding": "/platform/team-onboarding#demo", "Althea Assistant": "/platform/althea#demo"}


def _stat(r):
    v = r["value"]
    num = f"{v:.{r['decimals']}f}" if r.get("decimals") else f"{v:,.0f}"
    return (f"{r.get('prefix', '')}{num}{r.get('suffix', '')}", r["label"])


def _segment_page(seg_id, title, em):
    s = SEG[seg_id]
    tier, why, tools = FIT.get(seg_id, ("Professional", "", []))
    page = {
        "ctas": [("See what changes", "#rows"), ("Try the " + tools[0] + " demo", TOOL_HREF[tools[0]])] if tools else [("See what changes", "#rows")],
        "card": s["headline"],
        "stats_head": ("By the numbers", "What " + s["case_study"]["org"].lower(), "saw with Althais."),
        "stats": [_stat(r) for r in s.get("roi", [])],
        "stats_note": "Results from one organization; yours will depend on your volume, payers and specialties.",
        "recommend": {"tier": tier + " tier", "text": why, "tools": [(t, TOOL_HREF[t]) for t in tools]},
        "nav": s["label"], "eyebrow": s["label"], "title": title, "em": em, "lede": s["headline"],
        "meta": f"Althais for {s['label']}: {s['headline']}",
        "hero": [{"type": "rows", "eyebrow": "Before and after · " + s["case_study"]["org"].lower(),
                  "rows": [(m, f"{b} → {a}", "g") for m, b, a in s.get("before_after", [])]}],
        "rows": [
            {"eyebrow": "The problem", "title": "What gets", "em": "in the way.", "text": "The pressures we hear about most from " + s["label"].lower() + ".",
             "cards": [{"type": "list", "eyebrow": "The problem", "list": s["problems"]}]},
            {"eyebrow": "Today", "title": "How it's", "em": "usually done.", "text": "The workarounds teams fall back on when coding and claims are done by hand.",
             "cards": [{"type": "list", "eyebrow": "How it's done today", "list": s["workflow"]}]},
            {"eyebrow": "With Althais", "title": "What", "em": "changes.", "text": "The same work, with Althais doing the busywork and your team making the decisions.",
             "cards": [{"type": "list", "eyebrow": "With Althais", "good": True, "list": s["solution"]}]},
        ],
        "quote": {"text": s["case_study"]["quote"], "role": s["case_study"]["role"], "org": s["case_study"]["org"],
                  "initials": "".join(w[0] for w in s["case_study"]["role"].split()[:2]).upper()},
        "links_head": ("Also built for", "Every kind of", "care setting."),
        "links_grid": [{"eyebrow": o["label"], "title": o["label"], "text": o["headline"], "href": _seg_href(o["id"])}
                       for o in SOLUTION_SEGMENTS if o["id"] != seg_id][:3],
        "faq": [("How long does implementation take?", "Most organizations are live within 30 to 60 days, with a provider pilot before full rollout."),
                ("Who approves the codes?", "Your team. A person approves every code before it reaches a claim."),
                ("Can we start with a pilot?", "Yes. Every implementation includes a pilot on your own documentation.")],
    }
    return page


PAGES = {
    # ── Solutions ─────────────────────────────────────────────────────────────
    "/solutions/private-practices": _segment_page("private-practices", "A full billing team,", "without hiring one."),
    "/solutions/hospital-systems": _segment_page("hospital-systems", "One standard,", "across every facility."),
    "/solutions/revenue-cycle-teams": _segment_page("revenue-cycle-teams", "Your team,", "at full throughput."),
    "/solutions": {
        "nav": "All Solutions", "eyebrow": "All Solutions",
        "title": "Built for every", "em": "care setting.",
        "lede": "From a three-provider practice to a multi-hospital system, Althais fits the way your organization codes and bills.",
        "meta": "Althais solutions for hospital systems, multi-specialty groups, private practices, urgent care, FQHCs, specialty clinics, revenue cycle teams and medical billers.",
        "hero": [{"type": "chips", "eyebrow": "Who it's for", "chips": [s["label"] for s in SOLUTION_SEGMENTS]}],
        "links_head": ("Solutions", "Find your", "starting point."),
        "links_grid": [{"eyebrow": s["label"], "title": s["label"], "text": s["headline"], "href": _seg_href(s["id"]),
                        "go": "See how" if s["id"] in OWN_PAGE else "Learn more"} for s in SOLUTION_SEGMENTS],
        "ctas": [("Find your setup", "#compare-tiers"), ("Compare tiers", "/pricing#compare-tiers")],
        "card": "Every care setting Althais is built for.",
        "matrix_head": ("Which setup fits", "Where each kind of team", "usually starts."),
        "matrix": {"cols": ["Usually starts on", "Most-used tools"], "groups": [("By organization", [
            (o["label"], [FIT[o["id"]][0], ", ".join(FIT[o["id"]][2])]) for o in SOLUTION_SEGMENTS if o["id"] in FIT])]},
        "faq": [("Does Althais work for small practices?", "Yes. The Starter tier is built for independent practices getting AI coding and claims in place for the first time."),
                ("Can large systems standardize on it?", "Yes. Specialty-aware coding and one claims pipeline can roll out across facilities and departments."),
                ("Which care settings does it support?", "Hospital systems, multi-specialty groups, private practices, urgent care, FQHCs, specialty clinics, revenue cycle teams and billing services.")],
    },

    # ── Resources ─────────────────────────────────────────────────────────────
    "/resources": {
        "nav": "Resources", "eyebrow": "Resources",
        "title": "Guides, updates", "em": "and insights.",
        "lede": "Coding changes, revenue cycle guides and thinking on where AI helps in medical billing, and where it doesn't.",
        "meta": "Althais resources: CMS and coding updates, revenue cycle guides, case studies and perspectives on AI in healthcare billing.",
        "hero": [{"type": "chips", "eyebrow": "Topics", "chips": [label for key, label in RESOURCE_CATEGORIES if key != "all"]}],
        "articles": RESOURCE_ARTICLES,
        "categories": [(key, label) for key, label in RESOURCE_CATEGORIES if key != "all"],
        "faq": [("Can I get the full articles?", "Yes. Contact us and we'll send any of them over."),
                ("How often is this updated?", "We add coding and payer updates as CMS and the code sets change."),
                ("Can I suggest a topic?", "Please do. Tell us what your team is wrestling with.")],
    },
    "/case-studies": {
        "nav": "Case Studies", "eyebrow": "Case Studies",
        "title": "Practices that put", "em": "Althais to work.",
        "lede": "Stories from the kinds of organizations Althais is built for, in their own words.",
        "meta": "Case studies from practices, groups and health systems using Althais for coding and claims.",
        "cases": [{"label": s["label"], "quote": s["case_study"]["quote"], "role": s["case_study"]["role"], "org": s["case_study"]["org"],
                   "stats": s.get("before_after", [])[:3], "href": _seg_href(s["id"])} for s in SOLUTION_SEGMENTS if s.get("case_study")],
        "faq": [("Can I talk to a customer?", "Ask us during your demo and we'll connect you with a team like yours."),
                ("Are results the same everywhere?", "No. Every practice is different; we'll look at your own claims in a demo."),
                ("Where do I start?", "Book a demo, then pilot Althais on your own documentation.")],
    },
    "/walkthrough": {
        "nav": "Walkthrough", "eyebrow": "Walkthrough",
        "title": "One visit,", "em": "start to finish.",
        "lede": "Follow a single encounter through Althais, from the conversation in the exam room to the payment on the claim.",
        "meta": "A walkthrough of Althais: one patient visit from the recorded conversation to the SOAP note, codes, claim, submission and payment.",
        "hero": [{"type": "checklist", "eyebrow": "Six stops · example", "items": [("The visit", True), ("The note", True), ("The codes", True), ("The review", True), ("The claim", True), ("The follow-through", False)]}],
        "rows": [
            {"eyebrow": "The visit", "title": "No typing", "em": "during the visit.", "text": "The provider records the conversation or brings in a transcript, and stays with the patient.",
             "cards": [{"type": "note", "eyebrow": "You say", "text": "58-year-old with chest pain and shortness of breath for two hours. EKG normal sinus rhythm, troponin pending. Rule out ACS."}]},
            {"eyebrow": "The note", "title": "A SOAP draft,", "em": "ready to sign.", "text": "AI Scribe drafts the note. The provider edits anything and signs it.",
             "cards": [{"type": "shot", "eyebrow": "Write a note", "src": "/static/screenshots/provider-note.png", "alt": "The SOAP note screen in Althais"}]},
            {"eyebrow": "The codes", "title": "Codes with", "em": "their reasons.", "text": "Medical Coding suggests CPT and ICD-10 codes, each with confidence and the words in the note that support it.",
             "cards": [{"type": "shot", "eyebrow": "Suggested codes", "src": "/static/screenshots/ai-coding.png", "alt": "AI-suggested codes with confidence scores"}]},
            {"eyebrow": "The review", "title": "A person", "em": "approves each one.", "text": "The provider or coder accepts, changes or removes each code. Nothing moves on without that approval.",
             "cards": [{"type": "shot", "eyebrow": "Review codes", "src": "/static/screenshots/provider-review.png", "alt": "A provider accepting suggested codes"}]},
            {"eyebrow": "The claim", "title": "Checked,", "em": "then sent.", "text": "The claim is assembled and checked before it leaves, then sent electronically through your clearinghouse.",
             "cards": [{"type": "shot", "eyebrow": "Claim form", "src": "/static/screenshots/claim-assembly.png", "alt": "A CMS-1500 claim assembled from approved codes"}]},
            {"eyebrow": "The follow-through", "title": "Tracked", "em": "to payment.", "text": "Payments are tracked; denials come back with the reason and an appeal letter ready to draft.",
             "cards": [{"type": "shot", "eyebrow": "Claims overview", "src": "/static/screenshots/claim-tracking.png", "alt": "Claims overview with live status"}]},
        ],
        "faq": [("How long does the whole flow take?", "Most of it happens as soon as the note is signed; review fits between patients."),
                ("Who approves what?", "Providers sign notes and your team approves every code before a claim goes out."),
                ("Can I see it live?", "Yes. Book a demo and we'll walk through it with your own specialty and payers.")],
    },
    "/roi-calculator": {
        "nav": "ROI Calculator", "eyebrow": "ROI Calculator",
        "title": "What are denials", "em": "costing you?",
        "lede": "Enter your own numbers to see what denied claims and billing hours cost your practice today, and what you'd keep if you cut them.",
        "meta": "Estimate what claim denials and billing admin time cost your practice with the Althais ROI calculator.",
        "calculator": True,
        "faq": [("Where do these numbers come from?", "From you. The calculator only uses what you enter."),
                ("Is this a quote?", "No. It's an estimate to size the problem. A demo looks at your real claims."),
                ("What counts as an unrecovered denial?", "A denied claim that's never appealed or resubmitted, so the revenue is written off.")],
    },
    "/faq": {
        "nav": "FAQs", "eyebrow": "FAQs",
        "title": "Questions,", "em": "answered.",
        "lede": "The things practices ask most before getting started with Althais.",
        "meta": "Frequently asked questions about Althais: pricing, implementation, integrations, security and how coding approval works.",
        "faq_groups": [
            ("Getting started", [
                ("How long does implementation take?", "Most organizations are live within 30 to 60 days: integrations first, then tuning, then a provider pilot before full rollout."),
                ("Can we start with a pilot?", "Yes. Every implementation includes a pilot so your team can validate Althais on real documentation first."),
                ("Do we need a new clearinghouse?", "No. Althais connects to the clearinghouse you already use.")]),
            ("Coding and claims", [
                ("Who's responsible if a suggested code is wrong?", "The rendering provider approves every code before it reaches a claim. Althais shows confidence and reasons, but nothing bills without a human decision."),
                ("What happens to denied claims?", "You see the denial reason and can draft an appeal letter from it and the note."),
                ("Can Althais check codes we chose ourselves?", "Yes. Check Codes compares them against the documentation and flags gaps.")]),
            ("Security and pricing", [
                ("Is my data secure?", "Althais is built around HIPAA-eligible handling of PHI: encryption in transit and at rest, role-based access, and logged actions. BAAs are available on Enterprise plans."),
                ("How is pricing structured?", "Pricing follows claim volume and provider count, not a flat per-seat fee. Implementation, training and support are included."),
                ("Can we turn features on and off?", "Yes. Clinics choose the tools they use, and managers control what each role can access.")]),
        ],
    },

    # ── Company ───────────────────────────────────────────────────────────────
    "/about": {
        "nav": "Our Story", "eyebrow": "Our Story",
        "title": "Built by two people", "em": "tired of denied claims.",
        "lede": "Althais is a two-person team building the billing layer we wished existed: one that turns the visit into a clean claim, so clinicians get their evenings back.",
        "meta": "The Althais story: a two-person team building AI that turns patient visits into clean, checked claims.",
        "story": ("Why we're building Althais",
                  "Clinicians didn't train for years to spend their evenings typing notes and fixing claims.",
                  "Althais takes the paperwork between the visit and the payment, so the people who care for patients can spend their time doing it."),
        "people_head": ("The founders", "Two co-founders,", "one problem."),
        "people": [("KQ", "Kevin Qu", "Co-founder & CEO", ""), ("KY", "Kanishk Yankarla", "Co-founder & CTO", "")],
        "faq": [("How big is the team?", "Two co-founders, and we read every message."),
                ("How do I reach you?", "Email hello@althais.com or use the Contact page."),
                ("Are you hiring?", "See our Careers page, or tell us what you'd like to work on.")],
    },
    "/careers": {
        "nav": "Careers", "eyebrow": "Careers",
        "title": "Help clinicians spend", "em": "more time with patients.",
        "lede": "We're a small team building software that takes paperwork off clinicians' plates. If that's work you want to do, we'd like to hear from you.",
        "meta": "Careers at Althais: help build software that gives clinicians time back for patient care.",
        "grid": [("Work that matters", "Every hour Althais saves goes back to patient care."), ("Real ownership", "Ship things clinicians use the same week."),
                 ("Built with clinicians", "Design alongside the providers and billers who use it.")],
        "grid_label": "Why Althais",
        "links": [("Get In Touch", "/contact"), ("Our Story", "/about")],
        "faq": [("Do you have open roles?", "We don't have listings posted right now, but we'd still like to hear from you."),
                ("How do I apply?", "Email hello@althais.com with what you'd like to work on and a link to something you've built."),
                ("Is the team remote?", "Ask us. We'll tell you how we work today.")],
    },
    "/contact": {
        "nav": "Contact Us", "eyebrow": "Contact Us",
        "title": "Ready", "em": "to start?",
        "lede": "Tell us about your practice and we'll be in touch, with no commitment required. We're a small team and read every message.",
        "meta": "Contact Althais: book a demo, join the pilot, or email hello@althais.com. We read every message.",
        "hero": [{"type": "rows", "eyebrow": "Reach us", "rows": [("Email", "hello@althais.com", "g"), ("Reply", "Within one business day", "")]}],
        "links_head": ("Ways to reach us", "Pick what", "fits you best."),
        "links_grid": [{"eyebrow": "Email", "title": "hello@althais.com", "text": "Questions, partnerships or anything else. Expect a reply within one business day.", "href": "mailto:hello@althais.com", "go": "Email us"},
                       {"eyebrow": "Demo", "title": "Book A Demo", "text": "See Althais on visits like yours, with your specialty and payers.", "href": "/demo", "go": "Book a demo"},
                       {"eyebrow": "Pilot", "title": "Join The Pilot", "text": "Try Althais on your own documentation before rolling it out.", "href": "/pilot", "go": "Join the pilot"}],
        "faq": [("How fast will you reply?", "Within one business day."),
                ("Is there a commitment?", "No. A demo or a conversation commits you to nothing."),
                ("Who will I talk to?", "One of our two co-founders.")],
    },
}


def _view(href, section):
    nav = SECTIONS[section]

    async def view(request: Request, user=Depends(current_user)):
        return platform_pages.render(request, user, PAGES[href], href, section, nav)
    view.__name__ = "more_" + href.strip("/").replace("/", "_").replace("-", "_")
    return view


for _section, _nav in SECTIONS.items():
    for _name, _href in _nav:
        if _href in PAGES:
            router.add_api_route(_href, _view(_href, _section), methods=["GET"], include_in_schema=False)


for _section, _nav in SECTIONS.items():
    platform_pages.register(_section, {h: PAGES[h] for _, h in _nav if h in PAGES}, _nav)


@router.get("/our-story", include_in_schema=False)
async def our_story():
    return RedirectResponse(url="/about", status_code=302)
