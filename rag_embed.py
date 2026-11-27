"""Meaning search for policy_knowledge.py: Gemini embeddings on Vertex AI (same Google Cloud project and BAA as the rest
of Althais's AI), compared by cosine similarity.

  RAG_EMBEDDINGS=1 (default)   on when AI_PROVIDER=vertex; off otherwise (Groq has no embeddings, and no patient data
                               goes to a service without a BAA)
  RAG_EMBED_MODEL               default gemini-embedding-001
  RAG_EMBED_DIM                 default 768
  RAG_MIN_COSINE                default 0.55: weaker matches are dropped

The corpus is small (dozens of entries), so its vectors live in memory: computed once per process, in a background
thread the first time a search asks, and recomputed only if the corpus changes. Until they're ready, and whenever
embedding fails or is slow, similar() returns [] and the search uses keywords alone. A query is embedded with a short
timeout so a slow call can't hold up an answer. Only questions and reference text are embedded here, never a note.
"""
from __future__ import annotations

import hashlib
import math
import os
import threading
import time
from typing import Callable, List, Optional

MODEL = os.environ.get("RAG_EMBED_MODEL") or "gemini-embedding-001"
DIM = int(os.environ.get("RAG_EMBED_DIM") or 768)
MIN_COSINE = float(os.environ.get("RAG_MIN_COSINE") or 0.55)
QUERY_TIMEOUT_S = 3.0

_lock = threading.Lock()
_state = {"hash": None, "vectors": None, "building": False, "error": "", "built_at": 0.0}


def enabled() -> bool:
    import ai_client
    return os.environ.get("RAG_EMBEDDINGS", "1") != "0" and ai_client.PROVIDER == "vertex"


def _vertex_embed(texts: List[str], task: str, timeout_s: Optional[float] = None) -> List[List[float]]:
    """Embed texts with Gemini on Vertex, one request per text (some embedding models take one input per call)."""
    import ai_client
    from google.genai import types
    client = ai_client.VertexClient()._client()
    out = []
    for t in texts:
        cfg = {"task_type": task, "output_dimensionality": DIM}
        if timeout_s:
            cfg["http_options"] = types.HttpOptions(timeout=int(timeout_s * 1000))
        r = client.models.embed_content(model=MODEL, contents=[t], config=types.EmbedContentConfig(**cfg))
        out.append(list(r.embeddings[0].values))
    return out


def _norm(v: List[float]) -> List[float]:
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


def _corpus_hash(corpus: list) -> str:
    return hashlib.sha256("\n".join(d["id"] + "|" + d["text"] for d in corpus).encode()).hexdigest()[:16]


def _build(corpus: list, embedder: Callable) -> None:
    try:
        vecs = embedder([f"{d['topic']}. {d['text']}" for d in corpus], "RETRIEVAL_DOCUMENT")
        with _lock:
            _state.update(vectors=[_norm(v) for v in vecs], hash=_corpus_hash(corpus), error="", built_at=time.time())
        print(f"[RAG] embedded {len(corpus)} reference entries with {MODEL}", flush=True)
    except Exception as e:
        with _lock:
            _state["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        print(f"[RAG] embedding the reference entries failed, keyword search only: {_state['error']}", flush=True)
    finally:
        with _lock:
            _state["building"] = False


def warm(corpus: list, embedder: Optional[Callable] = None, wait: bool = False) -> None:
    """Start embedding the corpus if it isn't done (or has changed). wait=True blocks until it's finished (tests)."""
    emb = embedder or _vertex_embed
    h = _corpus_hash(corpus)
    with _lock:
        if (_state["hash"] == h and _state["vectors"]) or _state["building"]:
            fresh = False
        elif _state["error"] and time.time() - _state["built_at"] < 600 and embedder is None:
            fresh = False                       # failed recently: don't hammer the service
        else:
            _state["building"], fresh = True, True
            _state["built_at"] = time.time()
    if not fresh:
        return
    if wait:
        _build(corpus, emb)
    else:
        threading.Thread(target=_build, args=(corpus, emb), daemon=True).start()


def similar(query: str, corpus: list, k: int = 8, embedder: Optional[Callable] = None) -> List[tuple]:
    """[(index, cosine)] best first, at or above MIN_COSINE. [] when embeddings are off or not ready."""
    if embedder is None and not enabled():
        return []
    warm(corpus, embedder, wait=embedder is not None)
    with _lock:
        vecs = _state["vectors"] if _state["hash"] == _corpus_hash(corpus) else None
    if not vecs:
        return []
    emb = embedder or (lambda texts, task: _vertex_embed(texts, task, timeout_s=QUERY_TIMEOUT_S))
    q = _norm(emb([query[:2000]], "RETRIEVAL_QUERY")[0])
    scored = [(i, sum(a * b for a, b in zip(q, v))) for i, v in enumerate(vecs)]
    scored = [(i, c) for i, c in scored if c >= MIN_COSINE]
    scored.sort(key=lambda x: -x[1])
    return scored[:k]


def status() -> dict:
    with _lock:
        return {"enabled": enabled(), "model": MODEL, "ready": bool(_state["vectors"]), "entries": len(_state["vectors"] or []),
                "error": _state["error"]}


def reset() -> None:
    with _lock:
        _state.update(hash=None, vectors=None, building=False, error="", built_at=0.0)
