# Cost — real spend (from billing)

Dollars are from the provider **billing pages** (Google AI Studio per-model spend; OpenAI Usage per-model). Per-run splits use the harness's **exact recorded reader-token shares**; judge $ is estimated (harness doesn't record judge tokens). Mem0 never used the Gemini key, so all Gemini spend is Caura.

| phase | model | role | $ | source |
|---|---|---|---|---|
| ingest (one-time) | **gpt-5.4-nano** (OpenAI) | memory creation (type/weight/title/summary) | **29.76** | billing |
| ingest (one-time) | gemini-3.1-flash-lite | entity extraction / graph | **33.33** | billing |
| ingest (one-time) | bge-m3 (local GPU) | embeddings | 0.00 | local, free |
| **ingest subtotal** | | | **≈ 63.1** | |
| answer2 | gemini-3.8-flash | reader | ~2.1 | billing (62% of $3.43 reader total) |
| answer2 | gpt-4o | primary judge | ~0.07 | billing (of $0.13) · est. |
| answer2 | gemini-3.5-flash-lite | secondary judge | ~0.01 | billing ($0.02) · est. |
| **answer2 subtotal** | | | **≈ 2.2** | |
| **full pipeline** | | | **≈ 65.3** | |

- answer2 **reused** run 1's store (`--skip-ingest`), so its **own** spend is only **~$2**; the **~$63** ingest is one-time.
- **Nov 26 00:00–05:53 UTC gpt-5.4-nano (~$6.70)** was background re-processing of ingest tasks the Nov-25 **DB crashes** left pending — caused by the crashes, not the experiment; it is ingest work and ran outside the answer2 window.
- Not this run: `text-embedding-3-small $0.0076` and most of the OpenAI bill (Mem0 / old variant-2); our embeddings were local bge-m3.
- The `$` column in `summary.md`'s auto table uses placeholder rates and is superseded by this file.
