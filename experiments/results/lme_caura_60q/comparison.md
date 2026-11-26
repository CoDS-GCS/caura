# answer2 vs authors — same 60 questions

Authors = `caura-500-opaque-compact` restricted to our 60 ids (gpt-4o judge, `gpt-4o-2024-08-06`). Ours = `lme_caura_default_60q_answer2`.

## Accuracy (gpt-4o)

| type | authors | ours |
|---|---|---|
| knowledge-update | 9/10 | 10/10 |
| multi-session | 8/10 | 7/10 |
| single-session-assistant | 9/10 | 10/10 |
| single-session-preference | 6/10 | 7/10 |
| single-session-user | 10/10 | 10/10 |
| temporal-reasoning | 9/10 | 9/10 |
| **overall** | **51/60** | **53/60** |

Secondary judge (Flash-Lite): ours **52/60**.

> **This is a match within normal run-to-run variation, not a 'better' result.** A 2-question gap on 60 (53 vs 51) is well inside the noise of judge non-determinism and the deviations listed below; 50/60 questions land identically.

## Per-question agreement

- **both right (50):** 06db6396, 0f05491a, 184da446, 18bc8abd, 2133c1b5_abs, 50635ada, 5a4f22c0, a1eacc2a, dfde3500, 0ea62687, 2ce6a0f2, 6cb6f249, 77eafa52, 81507db6, edced276_abs, gpt4_d12ceb0e, 0e5e2d1a, 3249768e, 4c36ccef, 58470ed2, 71a3fd6b, a40e080f, ceb54acb, e48988bc, e982271f, 07b6f563, 0a34ad58, 195a1a1b, 1da05512, 32260d93, b0479f84, 15745da0_abs, 21436231, 36580ce8, 3d86fd0a, 726462e0, 75499fd8, 94f70d80, ad7109d1, bc8a6e93_abs, c960da58, 4dfccbf8, 982b5123, a3045048, gpt4_1d4ab0c9, gpt4_2c50253f, gpt4_2d58bcd6, gpt4_59149c77, gpt4_7ddcf75f, gpt4_85da3956
- **both wrong (6):** 0a995998, 10d9b85a, 09d032c9, 1c0ddc50, 35a27287, 71017277
- **only authors right (1):** 7405e8b1
- **only ours right (3):** 618f13b2, 778164c6, caf03d32

## Tokens & latency

| metric | authors (published) | ours (median) |
|---|---|---|
| retrieval latency | 7.6 s | 3.03 s |
| answering latency | 9.9 s | 15.85 s |
| context tokens | not published | 22,599 |
| reader tokens/question | not published | 26,565 |

_Retrieval is faster than theirs; answering is slower (agentic-v1 multi-call on a GPU shared with Mem0's TEI)._

## Differences from the authors' run

- **Scale** — 60-question Mem0 subset (10/type) vs their 500; their 60 figures are a restriction of the 500 run.
- **Memory-creation LLM** — ours **gpt-5.4-nano** (OpenAI, unpinned `PLATFORM_LLM_*` fallback) vs their **gemini-3.1-flash-lite via Vertex**. Sets per-memory title/type/weight/summary only; raw-turn content and content-embeddings are identical, and `--raw-turns-only` drops derived memories either way.
- **Gemini endpoint** — AI Studio (API key) vs their Vertex.
- **Embedder host** — bge-m3 on a GPU MIG slice shared with Mem0's qwen-tei vs their dedicated GPU TEI (same model, 1024-d vectors).
- **Server version** — ours `3.56.2` (REPRODUCE.md does not pin one).
- **Harness reliability patch** — our recall retries/raises instead of upstream's silent `[]`; the 200-path is unchanged, so answer2 accuracy is unaffected.
- **Store build** — ingested once by run 1 during DB instability, but ended complete (52,921 memories) and was **frozen** during answer2; the entity graph was still back-filling (a slight disadvantage to us, never an inflation).
