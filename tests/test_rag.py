"""The retrieval layer: what the AI reads before answering a billing question, coding a claim or drafting an appeal."""
import json
import os

import pytest

import billing_context as bc
import policy_knowledge as pk
import rag_corpus
import rag_embed

HERE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CHART = """PATIENT: TESTPATIENT, MARIA · Insurance: Blue Cross Blue Shield of Texas · Odessa, TX
ASSESSMENT AND PLAN
1. Strep throat, confirmed by rapid strep, with fever. Amoxicillin 500 mg twice daily for 10 days (prescription sent).
2. Laceration of left forearm, 3.5 cm, simple, repaired with 5 interrupted 4-0 nylon sutures.
3. Tetanus booster due: Tdap given today."""


@pytest.fixture(autouse=True)
def _fresh_embeddings():
    rag_embed.reset()
    yield
    rag_embed.reset()


# ── the corpus ──
def test_every_entry_is_complete_and_unique():
    ids = [d["id"] for d in rag_corpus.CORPUS]
    assert len(ids) == len(set(ids)) and len(ids) >= 50
    for d in rag_corpus.CORPUS:
        assert d["topic"] and len(d["text"]) > 80 and d["source"], d["id"]


# ── keyword search ──
def test_retrieval_test_set_finds_the_right_entry_in_the_top_3():
    cases = json.load(open(os.path.join(HERE, "evals", "rag_queries.json")))["cases"]
    misses = []
    for c in cases:
        got = [h.id for h in pk.search(c["q"], k=3, embed=False)]
        ok = (c["expect"] in got) if c["expect"] else got == []
        if not ok:
            misses.append((c["q"], c["expect"], got))
    assert len(misses) <= len(cases) // 20, misses          # at least 95% recall at 3


def test_everyday_words_reach_billing_terms():
    assert pk.search("my stitches visit got denied", embed=False)[0].id == "svc-laceration"
    assert "pay-recoupment" in [h.id for h in pk.search("insurance took back money", embed=False)]


def test_hits_carry_ids_and_sources_for_citing():
    h = pk.search("what does modifier 25 mean", k=1, embed=False)[0]
    assert h.id == "mod-25" and "[mod-25]" in h.cite() and "Source:" in h.cite()
    assert pk.sources([h]) == [{"id": "mod-25", "topic": h.topic, "source": h.source}]
    block = pk.format_context_block("what does modifier 25 mean")
    assert block.startswith("Reference material") and "[mod-25]" in block


def test_old_interface_still_works():
    topic, text, score = pk.retrieve("CO-97 bundled denial")[0]
    assert "97" in topic and score > 0
    assert len(pk.POLICY_CORPUS) == len(rag_corpus.CORPUS)


# ── meaning search ──
def _fake_embedder(vocab):
    """Vectors from concept words, so a paraphrase with no shared keyword can still match."""
    def emb(texts, task):
        out = []
        for t in texts:
            t = t.lower()
            out.append([1.0 if any(w in t for w in words) else 0.0 for words in vocab] + [0.01])
        return out
    return emb


def test_meaning_search_finds_a_paraphrase_keywords_miss():
    emb = _fake_embedder([("recoup", "took back", "clawed", "reclaimed"), ("bundl", "included in"), ("vaccin", "jab")])
    q = "the insurer clawed some cash"
    assert "pay-recoupment" not in [h.id for h in pk.search(q, embed=False)]
    hits = {h.id: h for h in pk.search(q, k=5, embedder=emb)}
    assert "pay-recoupment" in hits and hits["pay-recoupment"].meaning > 0.55 and hits["pay-recoupment"].keyword == 0


def test_meaning_and_keywords_merge():
    emb = _fake_embedder([("modifier 25", "separately identifiable", "separate e/m"), ("bundl",)])
    hits = pk.search("modifier 25 separately identifiable visit", k=3, embedder=emb)
    top = hits[0]
    assert top.id == "mod-25" and top.keyword > 0 and top.meaning > 0


def test_a_failing_embeddings_service_falls_back_to_keywords():
    def broken(texts, task):
        raise RuntimeError("vertex down")
    hits = pk.search("what does modifier 25 mean", k=3, embedder=broken)
    assert hits and hits[0].id == "mod-25" and hits[0].meaning == 0
    assert "vertex down" in rag_embed.status()["error"]


def test_embeddings_are_off_without_vertex(monkeypatch):
    import ai_client
    monkeypatch.setattr(ai_client, "PROVIDER", "groq")
    assert rag_embed.enabled() is False
    assert rag_embed.similar("modifier 25", rag_corpus.CORPUS) == []


def test_the_corpus_is_embedded_once():
    calls = []

    def counting(texts, task):
        calls.append((len(texts), task))
        return [[1.0, 0.0]] * len(texts)
    pk.search("modifier 25", embedder=counting)
    pk.search("bundling", embedder=counting)
    assert calls.count((len(rag_corpus.CORPUS), "RETRIEVAL_DOCUMENT")) == 1
    assert calls.count((1, "RETRIEVAL_QUERY")) == 2


# ── the claim builder's reference block ──
def test_a_visit_note_brings_the_rules_for_its_services():
    r = bc.for_claim(CHART, [{"code": "99214", "type": "CPT"}, {"code": "12002", "type": "CPT"}, {"code": "J02.0", "type": "ICD-10"}])
    assert r["ids"][:3] == ["mod-25", "svc-laceration", "minor-procedure-global"]
    assert {"svc-vaccines", "mod-qw"} <= set(r["ids"])
    assert "law-tx-prompt-pay" not in r["ids"]           # the address in the header isn't a billing topic
    assert r["block"].startswith("REFERENCE") and "[mod-25]" in r["block"]


def test_a_plain_visit_gets_nothing_forced():
    assert bc.for_claim("Assessment: viral URI. Plan: supportive care, fluids.")["ids"] == []


def test_ncci_edits_between_the_codes_are_spelled_out():
    lines = bc.ncci_lines([{"code": "99213", "type": "CPT"}, {"code": "96372", "type": "CPT"}])
    assert lines and "modifier indicator 1" in lines[0] and "96372" in lines[0]
    assert bc.ncci_lines(["99213"]) == []


def test_the_note_never_goes_to_the_embeddings_service(monkeypatch):
    seen = []
    monkeypatch.setattr(rag_embed, "similar", lambda q, corpus, k=8, embedder=None: seen.append(q) or [])
    bc.for_claim(CHART, [])
    assert seen == []


def test_the_claim_prompt_includes_the_references_only_when_switched_on(monkeypatch):
    import main
    prompts = []

    def fake_json(prompt, *a, **k):
        prompts.append(prompt)
        return {"codes": [{"code": "99214", "type": "CPT", "description": "", "modifier": "25", "units": 1, "reason": "[mod-25]"},
                          {"code": "J02.0", "type": "ICD-10", "description": "", "modifier": "", "units": 1, "reason": ""}],
                "note_additions": [], "unresolved": []}
    monkeypatch.setattr(main, "_fast_json", fake_json)
    monkeypatch.setenv("RAG_CODING", "0")
    off = main._claim_auto_work(CHART, "", "Payer: AETNA", "urgent care visit", [], 0.0)
    assert "REFERENCE" not in prompts[0] and off["references"] == []
    prompts.clear()
    monkeypatch.setenv("RAG_CODING", "1")
    on = main._claim_auto_work(CHART, "", "Payer: AETNA", "urgent care visit", [], 0.0)
    assert "REFERENCE (billing rules" in prompts[0] and "[svc-laceration]" in prompts[0]
    assert "mod-25" in on["references"]


# ── appeals ──
def test_denial_reasons_are_read_from_free_text():
    assert bc.denial_reasons("Denied CO-97 bundled; also PR 2 and co16") == [("CO", "97"), ("PR", "2"), ("CO", "16")]


def test_an_appeal_gets_the_reason_in_words_and_the_rule():
    r = bc.for_appeal("CO-97", "99214")
    assert "Reason code CO-97: Included in the payment for another service" in r["block"]
    assert "carc-97" in r["ids"] and "never invent policy" in r["block"]


def test_an_appeal_uses_the_clinics_own_window():
    import auth
    import clinic_setup as cs
    with auth.SessionLocal() as db:
        cs.save(db, "Rag Clinic", {"appeal": {"AETNA": 90}})
        r = bc.for_appeal("CO-50 not medically necessary", "99214", payer="AETNA LIFE", org="Rag Clinic", db=db)
        assert "appeal window with AETNA LIFE (from its contract, Clinic setup): 90 days" in r["block"]
        db.rollback()


# ── Althea ──
def test_althea_answers_show_their_sources(monkeypatch):
    from fastapi.testclient import TestClient
    import main
    from types import SimpleNamespace
    seen = {}

    def create(**kw):
        seen["messages"] = kw["messages"]
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="Modifier 25 marks a separate visit [mod-25]."))])
    monkeypatch.setattr(main, "client", SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create))))
    r = TestClient(main.app).post("/api/althea-public", json={"messages": [{"role": "user", "content": "what does modifier 25 mean"}]})
    assert r.status_code == 200
    assert r.json()["sources"][0]["id"] == "mod-25"
    assert any("[mod-25]" in m["content"] for m in seen["messages"] if m["role"] == "system")


def test_admin_status_reports_retrieval():
    import ai_client
    st = ai_client.status()["retrieval"]
    assert st["entries"] == len(rag_corpus.CORPUS) and st["coding"] in (True, False) and "embeddings" in st
