# Authors (published) vs ours — same 60 questions

Authors = `caura-500-opaque-compact` restricted to our 60 ids (gpt-4o judge, `gpt-4o-2024-08-06`). Ours = this run.

## Accuracy per type (gpt-4o)

| type | authors | ours |
|---|---|---|
| knowledge-update | 9/10 | 10/10 |
| multi-session | 8/10 | 7/10 |
| single-session-assistant | 9/10 | 10/10 |
| single-session-preference | 6/10 | 7/10 |
| single-session-user | 10/10 | 10/10 |
| temporal-reasoning | 9/10 | 9/10 |
| **overall** | **51/60** | **53/60** |

## Per-question agreement

- **both right** (50): 06db6396, 07b6f563, 0a34ad58, 0e5e2d1a, 0ea62687, 0f05491a, 15745da0_abs, 184da446, 18bc8abd, 195a1a1b, 1da05512, 2133c1b5_abs, 21436231, 2ce6a0f2, 32260d93, 3249768e, 36580ce8, 3d86fd0a, 4c36ccef, 4dfccbf8, 50635ada, 58470ed2, 5a4f22c0, 6cb6f249, 71a3fd6b, 726462e0, 75499fd8, 77eafa52, 81507db6, 94f70d80, 982b5123, a1eacc2a, a3045048, a40e080f, ad7109d1, b0479f84, bc8a6e93_abs, c960da58, ceb54acb, dfde3500, e48988bc, e982271f, edced276_abs, gpt4_1d4ab0c9, gpt4_2c50253f, gpt4_2d58bcd6, gpt4_59149c77, gpt4_7ddcf75f, gpt4_85da3956, gpt4_d12ceb0e
- **both wrong** (6): 09d032c9, 0a995998, 10d9b85a, 1c0ddc50, 35a27287, 71017277
- **only authors** (1): 7405e8b1
- **only ours** (3): 618f13b2, 778164c6, caf03d32

## Reader & context tokens (median)

| metric | authors | ours |
|---|---|---|
| context tokens | not published | — pending |
| reader total tokens | not published | 26578 |

## Latency vs authors' published medians

| phase | authors (published) | ours (p50) |
|---|---|---|
| retrieval | 7.6 s | 3.0636142670409754 s |
| answering | 9.9 s | 15.932048249058425 s |

## Remaining differences from the authors' run

- **Embedder host:** same model (bge-m3, 1024d) but served on a GPU MIG slice SHARED with Mem0's qwen-tei (authors: dedicated GPU TEI). Vectors identical; only serving host differs.
- **Enrichment endpoint:** gemini-3.1-flash-lite via the Gemini API key (authors: same model via Vertex).
- **Scale:** 60 questions (the Mem0 subset) vs the authors' 500; authors' 60-subset numbers are a restriction of their published 500 run, not a fresh 60-only run.
- Everything else (reader gemini-3.8-flash, judges gpt-4o + gemini-3.5-flash-lite, all retrieval flags) matches the published headline.
