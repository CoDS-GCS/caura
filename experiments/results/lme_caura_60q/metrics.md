# Metrics — LongMemEval_S 60-question subset (`answer2`)

All percentiles are computed directly from [`per_question.csv`](per_question.csv)
(n=60), so they match the per-question rows exactly. Latency figures are the
harness's per-call measurements taken around the operation itself; pause/resume
wall-time (16 credit pauses during the run) is **excluded**. "total" = retrieval +
answer per question (the judge is a separate offline scoring pass and is not in
the serving path).

## Latency (ms)

| phase | p50 | p95 | mean | min | max |
|---|---|---|---|---|---|
| retrieval (`POST /search`) | 3,031 | 11,451 | 4,246 | 369 | 14,865 |
| answer (agentic-v1 reader) | 15,848 | 34,811 | 18,851 | 7,265 | 65,339 |
| **total (retrieval + answer)** | **20,274** | **38,269** | 23,098 | 8,528 | 66,698 |

Answering dominates: the agentic-v1 reader issues multiple Gemini calls per
question, and the reader ran against an AI-Studio endpoint while sharing the GPU
with Mem0's TEI. Retrieval is faster than the authors' published 7.6 s median.

## Tokens per phase

| phase | model | in | out | total | note |
|---|---|---|---|---|---|
| retrieval context (into reader) | — | median **22,599** / Σ 1,351,781 | — | — | compact retrieved context; p95 23,724, range 20,685–24,325 |
| answer (reader, prompt+output) | gemini-3.8-flash | — | — | median **26,565** / Σ 1,843,869 | p95 51,969; max 102,621 (agentic-v1 multi-call) |
| memory building (ingest, one-time) | gpt-5.4-nano | derived ≈1,200/mem | derived ≈72/mem | — | per-memory metadata only; billed $29.76 |
| entity extraction (ingest, one-time) | gemini-3.1-flash-lite | — | — | — | graph back-fill; billed $33.33 |
| embeddings (ingest) | bge-m3 (local GPU) | — | — | — | free |

- **Candidates retrieved:** median 150 per question (search returns `top_k×2`
  overfetch; `search_candidates=150`).
- **Context savings:** median retrieved context 22,599 tok vs a ~122,382-tok full
  haystack (chars/4 estimate) — the retrieval layer carries ~18 % of the full
  conversation into the reader.
- Reader-token totals are **harness-metered**; ingest tokens are **derived**
  (the server does not expose per-memory LLM usage) and the dollars are from the
  provider billing pages. See [`cost.md`](cost.md) for the authoritative spend.
