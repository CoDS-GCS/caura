# LongMemEval run: instrumentation, resumability & collected results

How `run_lme.sh` survives credit exhaustion and what it writes for the
60-question run. All collection is pure post-processing — no extra model calls.

## Pause / resume on credit exhaustion

- **Detection.** Every paid harness step runs through `run_resumable`, which tees
  to a log and, on non-zero exit, scans it for **Gemini 402 /
  `RESOURCE_EXHAUSTED` / "prepayment credits are depleted"** and **OpenAI
  `insufficient_quota`**.
- **Pause.** On a match it stops sending, writes **`$OUT/PAUSED`** (reason + the
  phase + the in-progress/remaining question ids) and appends to
  **`$OUT/pauses.log`**.
- **Probe & resume.** It then probes the *failed* model with **one tiny request
  every 15 min for up to 12 h** (`probe_model`). When the model answers, it logs
  the resume and re-runs the step; when a step re-runs, the harness **resumes
  from disk** (`ingested.txt` for ingest, `hypotheses.jsonl` for answer) and only
  does the unfinished questions. After 12 h it exits cleanly with the resume
  command.
- **Never half-done.** The harness appends a question's hypothesis only after its
  whole pipeline finishes, and resets a question's agent before re-ingesting it,
  so an interrupted question is simply redone — never left half-ingested or
  half-answered. For ingest specifically, embeddings are **local TEI (cannot
  exhaust credit)**; enrichment is **server-side + async (Gemini)**, so after
  ingest the script **drains enrichment**, pauses on a Gemini 402 seen in the
  core-api log, and **deletes + re-ingests** any question still showing
  `enrichment_pending`, guaranteeing every question is fully enriched.
- **Checkpoint.** `$OUT/checkpoint.json` records `ingest_done` / `judged` /
  `finished`; `run_lme.sh --resume <RUN>` reads it and continues (ingest done →
  jumps to answer). The harness's own on-disk resume handles per-question skipping.
- **Latency is pause-free by construction.** All reported latencies are the
  harness's per-call measurements (`retrieve_time_ms`, `generate_time_ms`,
  per-question ingest seconds), taken *around the operation*; pauses happen
  between questions, so pause time never enters a latency number.

## Enrichment tokens — derived (server image is baked)

`core-api` runs the published ghcr image with no source mount, so per-call
`usage_metadata` logging can't be added without rebuilding it (which would
disturb the final setup). Per the agreed fallback, enrichment tokens are
**derived from the measured rate** — **2,943 input / 142 output tokens per
stored memory** (measured by replaying the server's exact enrichment prompt
through `gemini-3.1-flash-lite` and reading `usage_metadata`) **× the actual
memory count** from the store. Traces and `summary.md` label this
"derived from measured rate".

## Collected per run (written automatically at the end)

- **`$OUT/traces/<question_id>.json`** — question, type, gold, prediction;
  per-judge verdicts (gpt-4o + gemini-3.5-flash-lite); memories for the question
  (count, by memory_type, by status, server-derived); enrichment tokens
  (derived); ingestion seconds; the search queries + retrieve latency + retrieved
  memory ids/scores/snippets (captured by an instrumentation hook in the Caura
  provider) + candidate counts; context chars and Gemini-tokenized context tokens
  (`context_tokens.py`); evidence coverage (`coverage.py`); reader tokens and
  per-stage calls (extract/answer/verify) + generate latency; and for wrong
  answers a **failure label**:
  - `not_stored` — gold evidence not in the store;
  - `stored_not_retrieved` — stored but absent from the reader context;
  - `retrieved_wrong_answer` — gold fully in context, reader still wrong;
  - `judge_disagreement` — primary (gpt-4o) wrong but secondary right.
- **`$OUT/memory_dump.sql.gz`** and **`$OUT/memories.jsonl`** — written right
  after ingestion (full store dump + one JSON per memory).
- **`$OUT/summary.md`** — accuracy overall + per type; latency p50/p95 for
  ingestion / retrieval / answering; tokens and $ for memory-building vs
  answering vs judging (prices from `experiments/configs/prices.json`); median
  context tokens vs full haystack; evidence coverage per type; failures per label
  with ids; total memories + store size.
- **`$OUT/compare_authors.md`** — the authors' `caura-500-opaque-compact`
  restricted to our 60 ids: accuracy per type (theirs 51/60), per-question
  agreement with ids, reader/context tokens (theirs vs ours), latency vs their
  published 7.6 s / 9.9 s medians, "not published" where they publish nothing,
  and the remaining differences from their run.
- Harness outputs are copied to `$OUT/outputs/`; server logs for the run window
  to `$OUT/caura_logs.txt`; a redacted `$OUT/manifest.json`.

## Commands

```bash
# start (console log to /tmp so it survives the run dir):
cd /shared_mnt/loubna/caura
tmux new -s lme 'experiments/scripts/run_lme.sh --stage all 2>&1 | tee /tmp/lme_caura_default_60q.console.log'

# resume after a crash / 12h-pause exit (continues, no redo of finished questions):
tmux new -s lme 'experiments/scripts/run_lme.sh --resume lme_caura_default_60q 2>&1 | tee -a /tmp/lme_caura_default_60q.console.log'
```

Optional Mem0 parity judge (gpt-5 + Mem0 prompt): add `--mem0-judge`.
