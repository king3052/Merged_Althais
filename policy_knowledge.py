"""
policy_knowledge.py: the retrieval layer that grounds Althais's AI in billing and coding reference knowledge
(rag_corpus.py) instead of the model's memory.

Hybrid search, so it works with or without an embeddings service:
  - Keyword: BM25 over each entry's topic, text and tags. Billing questions are full of exact terms (codes, "CO-97",
    "modifier 25") where keyword overlap is the strongest signal, and it needs no infrastructure.
  - Meaning: when rag_embed.py can embed (Gemini embeddings on Vertex AI, same project and BAA), the corpus vectors are
    computed once per process in the background and each query is compared by cosine similarity, which catches
    questions worded differently from the entry ("my stitches visit got denied" -> laceration, modifier 25).
  - The two rankings are merged with reciprocal rank fusion (RRF). If embeddings are off, slow or failing, keyword
    results come back alone: search never fails because of them.

Every hit carries the entry's id and source, so prompts can ask the model to cite [ids] and answers can show where
they came from. retrieve() and format_context_block() keep their old shape for existing callers.
"""
from __future__ import annotations

import math
import re
from collections import Counter
from dataclasses import dataclass
from typing import List, Optional

from rag_corpus import CORPUS

# the old (topic, text) list, kept for anything that imported it
POLICY_CORPUS = [(d["topic"], d["text"]) for d in CORPUS]
BY_ID = {d["id"]: d for d in CORPUS}

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being", "of", "in", "on", "at", "to", "for",
    "and", "or", "but", "with", "that", "this", "it", "as", "by", "from", "what", "how", "why", "does", "do",
    "did", "can", "could", "would", "should", "will", "my", "i", "you", "your", "we", "our", "claim", "claims",
    "like", "today", "get", "got", "just", "really", "please", "help", "tell", "know", "think", "when", "which",
    "there", "their", "they", "them", "if", "so", "not", "no", "about", "need", "use", "used", "code", "codes",
}


def _stem(word: str) -> str:
    """Crude suffix stripping: enough to match bundled/bundling/bundle or denied/denies."""
    for suffix in ("ing", "edly", "ed", "ies", "es", "ly", "s"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


# billing word forms that suffix stripping doesn't unify, and everyday words for billing terms
_SYNONYMS = {
    "denial": "deni", "denials": "deni", "deny": "deni", "denies": "deni", "denied": "deni",
    "bundle": "bundl", "bundled": "bundl", "bundling": "bundl", "bundles": "bundl", "unbundle": "bundl",
    "auth": "authorization", "authorizations": "authorization", "preauth": "authorization", "precert": "authorization",
    "precertification": "authorization",
    "reimbursement": "reimburs", "reimburse": "reimburs", "reimbursed": "reimburs",
    "stitches": "laceration", "stitch": "laceration", "sutures": "laceration", "suture": "laceration",
    "shot": "vaccine", "shots": "vaccine", "immunization": "vaccine", "immunizations": "vaccine", "vaccination": "vaccine",
    "underpaid": "underpayment", "shortpaid": "underpayment",
    "late": "timely", "deadline": "timely",
    "ekg": "ecg", "xray": "ray", "radiograph": "ray",
    "takeback": "recoupment", "takebacks": "recoupment", "clawback": "recoupment", "offset": "recoupment",
    "eob": "835", "era": "835", "remittance": "835",
}


def _tokenize(text: str) -> List[str]:
    out = []
    for w in re.findall(r"[a-z0-9]+", (text or "").lower()):
        if w in _STOPWORDS or len(w) <= 1:
            continue
        if w in _SYNONYMS:
            out.append(_SYNONYMS[w])
        else:
            out.append(_stem(w))
    return out


def _doc_text(d: dict) -> str:
    # the topic counts three times: a question about "modifier 25" should rank the modifier 25 entry above entries
    # that only mention it (field boosting)
    return f"{d['topic']} {d['topic']} {d['topic']} {d['text']} {d.get('tags', '')}"


_DOC_TOKENS = [_tokenize(_doc_text(d)) for d in CORPUS]
_DOC_TF = [Counter(t) for t in _DOC_TOKENS]
_DOC_LENS = [len(t) for t in _DOC_TOKENS]
_AVG_DOC_LEN = (sum(_DOC_LENS) / len(_DOC_LENS)) if _DOC_LENS else 1.0
_DF: Counter = Counter()
for _toks in _DOC_TOKENS:
    _DF.update(set(_toks))
_N_DOCS = len(CORPUS)


def _bm25(query_tokens: List[str], i: int, k1: float = 1.5, b: float = 0.75) -> float:
    tf, score = _DOC_TF[i], 0.0
    for term in set(query_tokens):
        f = tf.get(term)
        if not f:
            continue
        df = _DF[term]
        idf = math.log(1 + (_N_DOCS - df + 0.5) / (df + 0.5))
        score += idf * (f * (k1 + 1)) / (f + k1 * (1 - b + b * _DOC_LENS[i] / _AVG_DOC_LEN))
    return score


@dataclass
class Hit:
    id: str
    topic: str
    text: str
    source: str
    score: float          # fused score (higher is better)
    keyword: float        # BM25 score, 0 when only the meaning search found it
    meaning: float        # cosine similarity, 0 when embeddings weren't used

    def cite(self) -> str:
        return f"[{self.id}] {self.topic}: {self.text} (Source: {self.source})"


def hit(entry_id: str) -> Hit:
    """One entry by id, as a Hit (for exact lookups such as billing_context's triggers)."""
    d = BY_ID[entry_id]
    return Hit(d["id"], d["topic"], d["text"], d.get("source", ""), 1.0, 0.0, 0.0)


def keyword_search(query: str, k: int = 5, min_score: float = 1.2, min_terms: int = 1) -> List[tuple]:
    """[(index, bm25)] best first, only above min_score and matching at least min_terms different query words."""
    q = _tokenize(query)
    if not q:
        return []
    qs = set(q)
    scored = [(i, _bm25(q, i)) for i in range(_N_DOCS) if len(qs & _DOC_TF[i].keys()) >= min_terms]
    scored = [x for x in scored if x[1] >= min_score]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def search(query: str, k: int = 3, min_score: float = 1.2, embed: bool = True, embedder=None, min_terms: int = 1) -> List[Hit]:
    """The best k entries for query: BM25 and (when available) embeddings, merged by reciprocal rank fusion. A query that
    matches nothing well returns [] rather than a forced match."""
    query = (query or "").strip()
    if not query:
        return []
    kw = keyword_search(query, k=max(k * 3, 8), min_score=min_score, min_terms=min_terms)
    sem: List[tuple] = []
    if embed:
        try:
            import rag_embed
            sem = rag_embed.similar(query, CORPUS, k=max(k * 3, 8), embedder=embedder)
        except Exception as e:                     # never let the meaning search break the answer
            print(f"[RAG] meaning search skipped: {type(e).__name__}", flush=True)
            sem = []
    fused: dict = {}
    for rank, (i, s) in enumerate(kw):
        fused.setdefault(i, [0.0, 0.0, 0.0])
        fused[i][0] += 1.0 / (60 + rank)
        fused[i][1] = s
    for rank, (i, c) in enumerate(sem):
        fused.setdefault(i, [0.0, 0.0, 0.0])
        fused[i][0] += 1.0 / (60 + rank)
        fused[i][2] = c
    order = sorted(fused.items(), key=lambda kv: (-kv[1][0], -kv[1][1]))[:k]
    return [Hit(CORPUS[i]["id"], CORPUS[i]["topic"], CORPUS[i]["text"], CORPUS[i].get("source", ""),
                round(f, 5), round(s, 3), round(c, 3)) for i, (f, s, c) in order]


def retrieve(query: str, k: int = 3, min_score: float = 1.2):
    """Old interface: [(topic, text, score)]."""
    return [(h.topic, h.text, h.keyword or h.score) for h in search(query, k=k, min_score=min_score)]


def context_block(hits: List[Hit], heading: Optional[str] = None) -> str:
    if not hits:
        return ""
    lines = [heading or ("Reference material (use it to ground your answer when it applies; ignore what doesn't; when you rely on "
                         "an entry, cite its id in square brackets, e.g. [mod-25]):")]
    lines += ["- " + h.cite() for h in hits]
    return "\n".join(lines)


def format_context_block(query: str, k: int = 3) -> str:
    """Relevant entries formatted for a prompt, or "" when nothing relevant was found."""
    return context_block(search(query, k=k))


def sources(hits: List[Hit]) -> List[dict]:
    """What an answer shows under 'Sources'."""
    return [{"id": h.id, "topic": h.topic, "source": h.source} for h in hits]
