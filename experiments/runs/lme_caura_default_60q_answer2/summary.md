# Summary — lme_caura_default_60q_answer2

## Accuracy

| type | gpt-4o | gemini-3.5-flash-lite |
|---|---|---|
| knowledge-update | 10/10 | 10/10 |
| multi-session | 7/10 | 7/10 |
| single-session-assistant | 10/10 | 9/10 |
| single-session-preference | 7/10 | 7/10 |
| single-session-user | 10/10 | 10/10 |
| temporal-reasoning | 9/10 | 9/10 |
| **overall** | **53/60** | **52/60** |

## Latency (p50 / p95)

| phase | p50 | p95 | unit |
|---|---|---|---|
| ingestion | None | None | s/question |
| retrieval | 3063.6142670409754 | 11992.847883026116 | ms |
| answering | 15932.048249058425 | 35499.0652090637 | ms |

_Pause time is excluded: these are the harness's per-call measurements, taken around the operation itself._

## Tokens & cost

> **The auto-generated table below uses PLACEHOLDER per-token rates and a token estimate
> that is ~2.4x too high, AND it omits the gpt-5.4-nano memory-creation LLM entirely.
> Do not use it. See "Actual cost (corrected, from billing)" immediately after.**

| phase | model | in tok | out tok | $ (placeholder — ignore) |
|---|---|---|---|---|
| memory building (enrichment) | gemini-3.1-flash-lite | 155,746,503 | 7,514,782 | 50.21 |
| answering (reader) | gemini-3.8-flash | 1,725,011 | 118,858 | 0.61 |
| judging (gpt-4o + flash-lite, est.) | gpt-4o + gemini-3.5-flash-lite | 38,454 | 600 | 0.11 |
| **total** | | | | **50.93** |

### Actual cost (corrected, from billing)

Every LLM that actually ran, by phase. **Dollars are from the provider billing pages**
(Google AI Studio per-model spend; OpenAI Usage per-model); **per-run splits use the
harness's exact recorded reader-token shares**; judge $ is estimated (harness doesn't
record judge tokens). Mem0 never used the Gemini key, so Gemini spend is all-Caura.

| phase | model | role | $ | source |
|---|---|---|---|---|
| ingest (one-time) | **gpt-5.4-nano** (OpenAI) | memory creation (type/weight/title/summary) | **29.76** | billing |
| ingest (one-time) | gemini-3.1-flash-lite | entity extraction / graph | **33.33** | billing |
| ingest (one-time) | bge-m3 (local GPU) | embeddings | 0.00 | local, free |
| **ingest subtotal** | | | **≈ 63.1** | |
| answer2 | gemini-3.8-flash | reader | ~2.1 | billing (62% of $3.43 reader total; answer2 = 1.725M of 2.81M in-tok) |
| answer2 | gpt-4o | primary judge | ~0.07 | billing (of $0.13 gpt-4o total) · est. |
| answer2 | gemini-3.5-flash-lite | secondary judge | ~0.01 | billing ($0.02 total) · est. |
| **answer2 subtotal** | | | **≈ 2.2** | |
| **full pipeline** | | | **≈ 65.3** | |

- answer2 **reused** the store ingested by `lme_caura_default_60q_INVALID_retrieval0`
  (`--skip-ingest`), so answer2's **own** spend is only ~$2.2; the ~$63 ingest is one-time.
- **Nov 26 00:00–05:53 UTC gpt-5.4-nano (~$6.70)** was **background re-processing** of
  memory-creation tasks the Nov-25 **DB crashes** left pending — caused by the crashes,
  not the experiment design. It is ingest work, not answering, and ran almost entirely
  **outside** the answer2 window (0 gpt-5.4-nano calls inside 04:17–04:23).
- `text-embedding-3-small $0.0076` and most of the OpenAI bill are **not** this run
  (Mem0 / old variant-2); our Caura embeddings were local bge-m3.

_Placeholder-table caption (obsolete): enrichment tokens were derived at 2943/142 per
memory — billing shows ~1200/72 per memory, and it missed gpt-5.4-nano. Embeddings are
local TEI (free); judge tokens are estimated from text sizes._

## Context vs full haystack

- median retrieved-context tokens: **None** (p95 None)
- median full-haystack tokens (chars/4 est.): **122382**

## Evidence coverage (fully-covered, per type)

| type | fully covered |
|---|---|
| knowledge-update | 9/9 |
| multi-session | 9/10 |
| single-session-assistant | 10/10 |
| single-session-preference | 10/10 |
| single-session-user | 8/8 |
| temporal-reasoning | 10/10 |

## Failures by label

- **retrieved_wrong_answer** (5): 1c0ddc50, 71017277, 0a995998, 7405e8b1, 09d032c9
- **judge_disagreement** (1): 35a27287
- **not_stored** (1): 10d9b85a

## Store
- total memories: **52,921**
- memory_dump.sql.gz: n/a

