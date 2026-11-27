# Metrics — LoCoMo conversation 0 (conv-26)

All percentiles are computed directly from [`per_question.csv`](per_question.csv)
(n=152 scored category-1–4 questions), so they match the per-question rows
exactly. "total" = search + answer + judge (the headline 3-vote judge is included
here because it was run inline per question). Thinking tokens are reported
separately from reader input/output.

## Latency (ms)

| phase | p50 | p95 | mean |
|---|---|---|---|
| search (`POST /search`) | 67 | 747 | 139 |
| answer (agentic-v1 reader) | 8,867 | 34,401 | 12,610 |
| judge (gemini-3.8-flash 3-vote) | 6,599 | 29,341 | 10,715 |
| **total (search + answer + judge)** | **17,679** | **54,885** | 23,465 |

Search is near-instant (p50 67 ms): with only **23 settled memories** in the
conv0 store, every query returns the whole conversation well under `top_k=30`, so
there is almost no ANN work to do (see the conv0 caveat in
[`comparison.md`](comparison.md)). Answering and 3-vote judging are the cost.

## Tokens per phase

| phase | model | provider | in | out | thinking |
|---|---|---|---|---|---|
| retrieval context (into reader) | — | — | median **16,270** / Σ 2,505,643 | — | — |
| reader (answer) | gemini-3.8-flash | Vertex | median 17,594 / Σ 2,906,906 | median 230 / Σ 45,207 | median 0 / Σ 10,703 |
| judge (headline 3-vote) | gemini-3.8-flash | Vertex | Σ 206,514 | Σ 15,441 | — |
| judge (second) | gpt-4o | OpenAI | Σ 67,062 | Σ 1,070 | — |
| memory building (ingest, derived) | gemini-3.1-flash-lite | Vertex | Σ 25,040 | Σ 3,450 | — |
| entity extraction (ingest, derived) | gemini-3.1-flash-lite | AI Studio | Σ 25,040 | Σ 3,450 | — |

- **Candidates retrieved:** 23 for every question (the full settled store —
  fewer than `top_k=30`).
- Reader/judge tokens are **measured** from provider usage; ingest tokens are
  **derived** from `ingest_trace.json` (the server does not expose per-memory
  usage). See [`cost.md`](cost.md) for the authoritative cost view.
- Reader thinking budget was 2,048 tokens but the median question used 0 and p95
  only 283 — the reader rarely needed extended thinking on this store.
