# LongMemEval_S reproduction — status & handover

_Last updated 2026-11-25._

**Goal:** reproduce Caura's **published** LongMemEval_S headline result
(`caura-500-opaque-compact`) on a fixed **60-question** subset. Comparison with
Mem0 is secondary and comes later.

## What's ready

- **Server = authors' published settings** (REPRODUCE.md "variant 1"): embedder
  `BAAI/bge-m3` (1024-dim) via a GPU TEI sidecar; enrichment
  `gemini-3.1-flash-lite`; `IS_STANDALONE=true`, `CAURA_TELEMETRY=off`. Caura
  server **v3.56.2**. Details in `experiments/SETUP_NOTES.md`.
- **GPU TEI** (`caura-tei-gpu`, port 8100) runs on the H100 MIG slice **shared
  with Mem0's `qwen-tei`**, which keeps running (both ~1.3 GB resident, ~18.8 GB
  free). Verified 1024-dim; write→search round-trip OK; Gemini enrichment OK.
- **The 60 questions** are pinned in `experiments/configs/lme_subset60_from_mem0.txt`
  (same 60 as Mem0's finished `lme_A_default_oss120b_60q`; 10 per type; verified
  three ways). The harness gained `--question-ids` (committed separately as an
  upstream-harness change).
- **Smoke test passed** on this exact setup: 2/2 under gpt-4o and
  gemini-3.5-flash-lite.
- **`experiments/scripts/run_lme.sh`** is written and syntax-checked (not run).
- Store is **empty** (`lmeo-*` cleaned); ready to ingest.

## Exact command to start the run

Run it yourself in tmux (ingest ≈ **5 h** on the shared GPU; the script sets no
early-kill timeouts):

```bash
cd /shared_mnt/loubna/caura
tmux new -s lme 'experiments/scripts/run_lme.sh --stage all 2>&1 | tee experiments/runs/lme_caura_default_60q/console.log'
```

Or split the stages (same run dir):

```bash
tmux new -s lme_ingest 'experiments/scripts/run_lme.sh --stage ingest'
# when ingest is done:
tmux new -s lme_answer 'experiments/scripts/run_lme.sh --stage answer'
```

Watch progress: `tail -f experiments/runs/lme_caura_default_60q/ingest.log`
(per-question lines: `#<id>: <n> sessions -> <m> chunks in <s>s`).

Artifacts land in `experiments/runs/lme_caura_default_60q/`: `manifest.json`,
`ingest.log`, `memory_dump.sql.gz`, `run.log`, `eval.log`, `outputs/`,
`caura_logs.txt`.

## Primary comparison — authors (published) vs our reproduction, same 60 Q

Judge: **gpt-4o** (`gpt-4o-2024-08-06`), the LongMemEval reference judge. Authors'
numbers are extracted from the committed `caura-500-opaque-compact` run
(`experiments/configs/authors_headline_60q_verdicts.json`). "Ours" fills in after
the run.

| question type | authors (published, 60 Q) | ours (60 Q) |
|---|---|---|
| knowledge-update | 9/10 (90%) | — pending |
| multi-session | 8/10 (80%) | — pending |
| single-session-assistant | 9/10 (90%) | — pending |
| single-session-preference | 6/10 (60%) | — pending |
| single-session-user | 10/10 (100%) | — pending |
| temporal-reasoning | 9/10 (90%) | — pending |
| **overall** | **51/60 (85.0%)** | **— pending** |

**Per-question agreement** (fill after the run): both right / both wrong / only
authors right / only ours right. The script saves our per-question verdicts to
`outputs/.../eval_results.json`; diff against the authors' verdicts file.

### Spot-check: the two smoke questions (not in the 60; single-session-user)

| QID | gold | authors gpt-4o / flash-lite | ours (smoke) gpt-4o / flash-lite |
|---|---|---|---|
| `118b2229` | 45 minutes each way | ✅ / ✅ | ✅ / ✅ |
| `e47becba` | Business Administration | ✅ / ✅ | ✅ / ✅ |

Authors' and our predictions were nearly identical (e.g. "…45 minutes each way
(≈90 min round trip)"; "…degree in Business Administration").

## Cost estimate for the 60-question run

Derived from the 2-question smoke (GPU bge-m3, gemini enrichment, gemini-3.8-flash
reader, gpt-4o + gemini-3.5-flash-lite judges). Token counts are **measured**
for the reader/judges and **estimated** for enrichment (the server logs
enrichment latency, not tokens). Apply current AI-Studio / OpenAI rates for $.

| component | model | calls (60 Q) | tokens (60 Q) | basis |
|---|---|---|---|---|
| enrichment | gemini-3.1-flash-lite | ≈59,000 (≈987/Q) | ≈45–55 M (est.) | smoke: 1,974 calls for 2 Q (~1 per stored memory; avg memory ≈810 chars ≈200 tok; +extraction prompt/JSON ≈700–900 tok/call) |
| reader | gemini-3.8-flash | ≈180 (3/Q: extract/answer/verify) | ≈1.6 M | authors' published compact median ≈26.5k tok/Q × 60 (smoke measured ≈24.4k/Q on 2 single-session Qs; larger types run higher) |
| primary judge | gpt-4o | 60 | ≈60 k in, tiny out | ≈1k tok/Q; gpt-4o ≈ $0.15 total at historical $2.50/1M in |
| secondary judge | gemini-3.5-flash-lite | 60 | ≈60 k | ≈1k tok/Q |

**Enrichment dominates** (server-side, per memory) and is the long pole in both
tokens and wall-time. Reader is the main answer-side cost. Judges are negligible.
Wall-time: ingest ≈5 h (shared GPU); answer+judge ≈30–60 min.

## Open issues / caveats

- **LoCoMo harness is private.** `yanki-locomo` is not accessible, so the LoCoMo
  leg of the comparison is blocked until it's shared/open-sourced.
- **Answering model differs from Mem0.** We answer with `gemini-3.8-flash`
  (Caura's published reader); Mem0's reference run answers with `gpt-5` (its
  extraction LLM is `gpt-oss-120b`, after which that run is named). So the Mem0
  table is not a like-for-like answerer comparison.
- **Embedder matches the authors but differs from Mem0.** We use `bge-m3`
  (1024-dim) as published; Mem0 uses `Qwen/Qwen3-Embedding-0.6B` (768-dim). Fine
  for the authors-reproduction goal; relevant when comparing to Mem0 later.
- **Shared GPU.** The bge-m3 TEI time-shares the MIG slice with Mem0's active
  run, so ingest is slower (~5 h) than on a dedicated GPU. It completes fine; do
  not stop Mem0's `qwen-tei`.
- **Variant 2 (later).** An OpenAI `text-embedding-3-small` + `gpt-5.4-nano`
  config is documented in SETUP_NOTES.md to run after this one.
- **Run B / Mem0 gpt6luna.** Mem0's `gpt6luna` 60-Q run is still ingesting; it
  gets added as a second Mem0 column when it finishes. A later Caura variant that
  only changes the reader/judge can reuse this ingested store via
  `run_lme.sh --stage answer` (no re-ingest).

## Mem0 comparison (separate, later)

Reference run `lme_A_default_oss120b_60q` (finished): extraction `gpt-oss-120b`
(Parasail/OpenRouter), answerer+judge `gpt-5`, embedder `Qwen3-Embedding-0.6B`
(768d), top_k 200, published 56/60 (93.3%) under its gpt-5 judge. To judge our
hypotheses with Mem0's exact judge (gpt-5 + its prompt) for parity, run
`run_lme.sh --mem0-judge` (uses `experiments/scripts/mem0_judge.py` +
`experiments/configs/mem0_judge_prompt.txt`). This table is built later.
