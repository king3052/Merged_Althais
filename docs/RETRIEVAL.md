# Retrieval (RAG) in Althais

What the AI reads before it answers, codes or drafts, so it works from billing rules instead of memory.

## Pieces

| File | What it does |
|---|---|
| `rag_corpus.py` | 53 reference entries in our own words from public guidance (CMS manuals, NCCI, ICD-10-CM guidelines, X12, Texas law). Each has an id, a source and search tags. No AMA CPT descriptor text. |
| `policy_knowledge.py` | Hybrid search: BM25 keywords (topic weighted 3x) plus Gemini embeddings when available, merged with reciprocal rank fusion. Returns hits with ids and sources. |
| `rag_embed.py` | Gemini embeddings on Vertex AI (same project, same BAA). Corpus vectors in memory, built in the background at startup; queries time out after 3 s. Any failure falls back to keywords. Off unless `AI_PROVIDER=vertex`. |
| `billing_context.py` | Builds what the coding and appeal prompts get: exact service triggers (laceration, vaccine, waived test...) and the modifier-25 situation, keyword matches on the assessment and plan, NCCI edits between the codes, denial reason codes in words, and the clinic's own appeal window. |
| `evals/rag_queries.json` | Retrieval test set: 35 questions and the entry that must come back in the top 3 (100% today on keywords alone). |

## Where it's used

| Feature | Setting | Default |
|---|---|---|
| Althea billing questions (in the app and on the website) | always | on; answers now return `sources` |
| Appeal letters (`/api/appeal-letter`) | `RAG_APPEALS` | on (`0` turns it off) |
| Claim builder / Code A Note (`_build_claim`) | `RAG_CODING` | **off** until the coding test has been run with it on and off |
| Meaning search (embeddings) | `RAG_EMBEDDINGS`, `RAG_EMBED_MODEL`, `RAG_EMBED_DIM`, `RAG_MIN_COSINE` | on with Vertex; `gemini-embedding-001`, 768, 0.55 |

## Privacy

A visit note is searched by keywords only and is never sent to the embeddings service. Only questions, codes and reason
codes are embedded.

## Turning it on for coding

1. Admin console, Quality: run the coding test and note the score.
2. Add a Secret Manager setting `RAG_CODING` = `1` (label `app=althais`), deploy, run the test again.
3. Keep it on only if the score is the same or better. Each claim's log line shows `refs=[...]`, the entries it used.

## Checking it's working

- Logs after a deploy: `[RAG] embedded 53 reference entries with gemini-embedding-001`, or a line saying it fell back to keywords and why.
- `ai_client.status()["retrieval"]` (admin console): entries, embeddings ready or not, coding and appeals on or off.

## Next steps

- More entries: payer-specific rules as clinics share their provider manuals, and AMA coding updates once the CPT license is signed.
- Retrieval scores in the admin Quality page next to the coding score.
- If the corpus grows past a few thousand entries: pgvector on Cloud SQL or Vertex AI Vector Search instead of in-memory vectors.
