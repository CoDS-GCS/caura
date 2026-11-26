# Summary — lme_caura_default_60q

## Accuracy

| type | gpt-4o | gemini-3.5-flash-lite |
|---|---|---|
| knowledge-update | 0/0 | 0/0 |
| multi-session | 0/0 | 0/0 |
| single-session-assistant | 0/0 | 0/0 |
| single-session-preference | 0/0 | 0/0 |
| single-session-user | 0/0 | 0/0 |
| temporal-reasoning | 0/0 | 0/0 |
| **overall** | **0/0** | **0/0** |

## Latency (p50 / p95)

| phase | p50 | p95 | unit |
|---|---|---|---|
| ingestion | None | None | s/question |
| retrieval | None | None | ms |
| answering | None | None | ms |

_Pause time is excluded: these are the harness's per-call measurements, taken around the operation itself._

## Tokens & cost (prices from prices.json)

| phase | model | in tok | out tok | $ |
|---|---|---|---|---|
| memory building (enrichment) | gemini-3.1-flash-lite | 0 | 0 | 0.00 |
| answering (reader) | gemini-3.8-flash | 0 | 0 | 0.00 |
| judging (gpt-4o + flash-lite, est.) | gpt-4o + gemini-3.5-flash-lite | 0 | 0 | 0.00 |
| **total** | | | | **0.00** |

_Enrichment tokens are derived from the measured rate (2943 in / 142 out per memory); embeddings are local TEI (free); judge tokens are estimated from text sizes (harness does not record them)._

## Context vs full haystack

- median retrieved-context tokens: **None** (p95 None)
- median full-haystack tokens (chars/4 est.): **None**

## Evidence coverage (fully-covered, per type)

| type | fully covered |
|---|---|
| knowledge-update | 0/0 |
| multi-session | 0/0 |
| single-session-assistant | 0/0 |
| single-session-preference | 0/0 |
| single-session-user | 0/0 |
| temporal-reasoning | 0/0 |

## Failures by label

- none

## Store
- total memories: **0**
- memory_dump.sql.gz: 341.0 MB

