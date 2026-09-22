"""
policy_knowledge.py — a small, dependency-free retrieval layer that grounds
Althea's answers in real billing/coding reference knowledge instead of
letting the model answer purely from memory.

Design choices, deliberately:
  - No vector database, no embeddings API. The app has no OPENAI_API_KEY
    and no pgvector confirmed available on the production Postgres
    instance — building on either would be a real deploy risk with no
    way to verify it works before it's live. A pure-Python BM25-style
    keyword ranker needs zero new infrastructure and zero new secrets,
    and works well for this kind of terminology-dense reference text
    (CPT/ICD codes, exact billing terms) where keyword overlap is a
    strong relevance signal anyway.
  - The corpus below is written in our own words, grounded in general
    CMS/Medicare public guidance (which is public-domain U.S. government
    material) and well-established billing conventions — not copied text
    from any commercial payer's proprietary policy bulletins.
  - Swapping this for real vector search later (once pgvector or an
    embeddings provider is confirmed available) only means replacing
    `retrieve()` — the corpus format and the calling code don't change.
"""

import math
import re
from collections import Counter

# ── Knowledge corpus ─────────────────────────────────────────────────────
# Each entry: (topic, text). Kept to a few sentences each — enough for
# Althea to ground an answer and point the user in the right direction,
# not a full policy manual.
POLICY_CORPUS = [
    ("E/M coding — choosing a level by time",
     "Since 2021, CMS allows E/M office visit levels (99202-99215) to be selected by total time spent on the date of the encounter, "
     "including time reviewing records, counseling, and documentation — not just face-to-face time. Total time must be documented "
     "explicitly in the note; a vague note without a time statement can only be leveled by medical decision making instead."),

    ("E/M coding — choosing a level by medical decision making (MDM)",
     "MDM has three components: the number and complexity of problems addressed, the amount and complexity of data reviewed, "
     "and the risk of complications from the management plan. The overall level is set by whichever two of these three components "
     "are highest — not an average. A single high-risk element can be enough to justify a higher level even if the other components are lower."),

    ("Critical care time-based coding (99291/99292)",
     "CPT 99291 covers the first 30-74 minutes of critical care on a given date; 99292 is used for each additional 30 minutes beyond that. "
     "Critical care under 30 minutes total should be billed as a regular E/M visit instead, not 99291. Time must be documented as a total "
     "number of minutes, not just a start and end time, and must reflect only time devoted exclusively to that patient."),

    ("Modifier 25 — significant, separately identifiable E/M",
     "Modifier 25 is appended to an E/M code when a significant, separately identifiable evaluation and management service is performed "
     "on the same day as a procedure. The documentation must support that the E/M work went beyond what's normally bundled into the "
     "procedure itself — a note that only describes the procedure won't support modifier 25 if questioned."),

    ("Modifier 59 — distinct procedural service",
     "Modifier 59 indicates that two procedures, which would normally be considered bundled together under NCCI edits, were actually "
     "performed at different sites, different sessions, or represent genuinely distinct services on the same date. It should only be used "
     "when the documentation clearly supports the two services were truly separate — overuse of modifier 59 is a common audit trigger."),

    ("NCCI edits — what a Procedure-to-Procedure (PTP) edit means",
     "An NCCI PTP edit identifies pairs of CPT/HCPCS codes that Medicare considers improperly billed together on the same date for the "
     "same patient, because one is generally considered part of the other. A PTP edit with an indicator of '1' allows an appropriate "
     "modifier (like 59) to override the bundling if the documentation supports it; an indicator of '0' means the edit cannot be overridden at all."),

    ("Timely filing — general Medicare rule",
     "Medicare requires claims to be filed within 12 months (365 days) from the date of service, with no exceptions for late filing except "
     "in narrow, specifically defined circumstances. Commercial payers set their own timely filing windows, commonly 90 to 180 days, and "
     "these are stated in the specific payer contract — always confirm the payer's actual deadline rather than assuming Medicare's applies."),

    ("Prior authorization — what commonly requires it",
     "Advanced imaging (MRI, CT, PET scans), many specialty medications, non-emergency surgical procedures, and durable medical equipment "
     "commonly require prior authorization from commercial payers and Medicare Advantage plans — though routine office visits, basic labs, "
     "and most emergency care generally do not. Requirements vary meaningfully by specific plan, so this should always be verified directly "
     "with the payer rather than assumed from general patterns."),

    ("Incident-to billing",
     "Incident-to billing allows services provided by a non-physician practitioner (like an NP or PA) to be billed under the supervising "
     "physician's NPI at the full physician fee schedule rate, but only when the physician has personally performed the initial visit, "
     "established the plan of care, and remains actively involved with direct supervision on-site during the incident-to service."),

    ("Place of service (POS) codes — why they matter",
     "The Place of Service code on a claim affects the reimbursement rate — many payers pay a higher, non-facility rate for services "
     "performed in a physician's office (POS 11) than the facility rate for the same service performed in a hospital outpatient department "
     "(POS 22). Billing the wrong POS code is a common, preventable source of underpayment or claim rejection."),

    ("Denial reason — CO-97 (bundled service)",
     "A CO-97 denial means the payer determined the billed service is bundled into another service or procedure already paid for the same "
     "date, and is not separately reimbursable as billed. This is the denial code most often tied to an NCCI PTP edit conflict — checking "
     "whether an appropriate modifier like 59 applies is the first troubleshooting step."),

    ("Denial reason — CO-50 (not medically necessary)",
     "A CO-50 denial means the payer's clinical review determined the billed service was not medically necessary based on the diagnosis "
     "codes submitted with the claim. This often means the ICD-10 code on the claim doesn't clearly support the CPT code billed — reviewing "
     "whether a more specific or better-supporting diagnosis code exists is the usual first step before appealing."),

    ("Denial reason — CO-29 (timely filing)",
     "A CO-29 denial means the claim was submitted after the payer's timely filing deadline. These denials are generally not appealable "
     "unless the practice can document a valid reason the deadline was missed (such as a documented system outage or the payer's own "
     "processing delay), so the best prevention is submitting claims promptly rather than relying on a successful appeal after the fact."),

    ("Appeal letters — what makes them effective",
     "An effective appeal letter states the claim number and specific denial reason, cites the relevant clinical documentation that supports "
     "medical necessity or correct coding, and makes a direct, specific argument for why the original determination should be reversed — "
     "vague or generic appeals without reference to the specific denial reason are far less likely to succeed."),

    ("CMS-1500 form — what it's for",
     "The CMS-1500 is the standard paper and electronic claim form used by non-institutional providers (physician offices, urgent care, "
     "most outpatient settings) to bill Medicare, Medicaid, and the large majority of commercial payers. Hospitals and other institutional "
     "providers use the UB-04 form instead, which has different fields and different billing rules."),
]

_STOPWORDS = {
    "a","an","the","is","are","was","were","be","been","being","of","in","on","at","to","for",
    "and","or","but","with","that","this","it","as","by","from","what","how","why","does","do",
    "did","can","could","would","should","will","my","i","you","your","we","our","claim","claims",
    "like","today","get","got","just","really","please","help","tell","know","think",
}

def _stem(word: str) -> str:
    """Crude suffix stripping — enough to match 'bundling'/'bundled'/'bundle' or
    'denies'/'denied' as the same term without pulling in a stemming
    library. Not linguistically rigorous, just good enough for this corpus."""
    for suffix in ("ing", "edly", "ed", "es", "ies", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word

# A handful of important billing-term variants that generic suffix-stripping
# doesn't unify on its own (different word forms, not just different
# suffixes) — e.g. "denied" -> "deni" but "denial" stays "denial" under
# plain stemming. Explicit and small on purpose: this is easier to reason
# about and extend than tuning a generic stemmer for a narrow domain.
_SYNONYMS = {
    "denial": "deni", "denials": "deni", "deny": "deni", "denies": "deni",
    "bundle": "bundl", "bundled": "bundl", "bundling": "bundl", "bundles": "bundl",
    "auth": "authorization", "authorizations": "authorization", "preauth": "authorization",
    "reimbursement": "reimburs", "reimburse": "reimburs", "reimbursed": "reimburs",
}

def _tokenize(text: str):
    words = re.findall(r"[a-z0-9]+", text.lower())
    out = []
    for w in words:
        if w in _STOPWORDS:
            continue
        if len(w) <= 1:
            continue
        w = _SYNONYMS.get(w, w)
        out.append(_stem(w) if w not in _SYNONYMS.values() else w)
    return out

# Precompute term frequencies for the corpus once at import time.
_DOC_TOKENS = [_tokenize(topic + " " + text) for topic, text in POLICY_CORPUS]
_DOC_LENS = [len(toks) for toks in _DOC_TOKENS]
_AVG_DOC_LEN = sum(_DOC_LENS) / len(_DOC_LENS) if _DOC_LENS else 1
_DF = Counter()
for toks in _DOC_TOKENS:
    for term in set(toks):
        _DF[term] += 1
_N_DOCS = len(POLICY_CORPUS)

def _bm25_score(query_tokens, doc_tokens, doc_len, k1=1.5, b=0.75):
    tf = Counter(doc_tokens)
    score = 0.0
    for term in query_tokens:
        if term not in tf:
            continue
        df = _DF.get(term, 0)
        if df == 0:
            continue
        idf = math.log(1 + (_N_DOCS - df + 0.5) / (df + 0.5))
        freq = tf[term]
        denom = freq + k1 * (1 - b + b * doc_len / _AVG_DOC_LEN)
        score += idf * (freq * (k1 + 1)) / denom
    return score

def retrieve(query: str, k: int = 3, min_score: float = 1.2):
    """
    Return up to `k` corpus entries most relevant to `query`, ranked by
    BM25 score, as a list of (topic, text, score) tuples. Entries below
    `min_score` are dropped — an unrelated question (e.g. "what's the
    weather") should retrieve nothing rather than a weak forced match.
    """
    q_tokens = _tokenize(query)
    if not q_tokens:
        return []
    scored = []
    for i, (topic, text) in enumerate(POLICY_CORPUS):
        s = _bm25_score(q_tokens, _DOC_TOKENS[i], _DOC_LENS[i])
        if s >= min_score:
            scored.append((s, topic, text))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [(topic, text, round(s, 3)) for s, topic, text in scored[:k]]


def format_context_block(query: str, k: int = 3) -> str:
    """
    Retrieve relevant corpus entries and format them as a block ready to
    inject into an LLM prompt as grounding context. Returns an empty
    string if nothing relevant was found, so callers can skip adding an
    empty "Reference material:" section to the prompt.
    """
    hits = retrieve(query, k=k)
    if not hits:
        return ""
    lines = ["Reference material (use this to ground your answer if relevant; ignore anything not applicable to the question):"]
    for topic, text, _score in hits:
        lines.append(f"- {topic}: {text}")
    return "\n".join(lines)
