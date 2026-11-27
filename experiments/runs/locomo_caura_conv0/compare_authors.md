# conv0 (152 Q) vs the authors' full-set overall — LoCoMo

**Ours:** gemini-3.8-flash 3-vote judge **90.8% (138/152)** on **conv 0 only (conv-26, 152 scored cat 1–4 questions)**.

**Authors (EVIDENCE.md `locomo_caura_retrieval_accuracy_2026_09`):** **77.9% (1,199/1,540)** over all 1,540 scored cat 1–4 questions across all 10 conversations.

Independent **rerun of the authors' pipeline family, not a reproduction**: their headline harness (`caura-ai/yanki-locomo`) and per-question results are not public, so the numbers are not directly comparable. Every difference:

| dimension | authors | ours |
|---|---|---|
| scope | 10 convs, 1,540 Q | **conv 0 only, 152 Q** |
| harness | yanki-locomo (private, no per-Q artifact) | our longmemeval-derived locomo harness |
| store config | `caura-balanced-50-v3-20260910` (not published) | built from conv0 with published params |
| chunk_characters | 4000 | 4000 |
| top_k | 30 | 30 |
| judge_votes | 3 (gemini-3.8-flash) | 3 (gemini-3.8-flash, seeds recorded) |
| thinking_tokens | 2048 (scope not published) | 2048 on **reader only** (recorded) |
| min_similarity | not published (caura-locomo used 0.0) | 0.0 per request |
| reader/judge transport | not published | **Vertex AI** (Google Cloud credits) |
| second judge | none | gpt-4o single pass (added, reported beside) |
| embedder | not published | BAAI/bge-m3 local GPU (1024-d) |
| reader / judge temperature | not published | 0.0 / 0.0 |
