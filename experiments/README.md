# Caura (MemClaw) memory-benchmark experiments

Independent runs of **Caura / MemClaw** on the two most-cited agent-memory
benchmarks — **LongMemEval_S** and **LoCoMo** — each following the authors'
published pipeline (retrieval-then-answer, `agentic-v1` reader, LLM judge) on a
local Caura stack with a GPU embedder.

## Headline results

| benchmark | scope | judge | ours | authors | source for authors |
|---|---|---|---|---|---|
| **LongMemEval_S** | 60-q subset (10/type, Mem0's subset) | gpt-4o | **53/60 (88.3%)** | **51/60** | `caura-500-opaque-compact` restricted to the same 60 ids |
| LongMemEval_S | same 60 | gemini-3.5-flash-lite | 52/60 | — | secondary judge |
| **LoCoMo** | conv 0 only (conv-26), 152 scored cat 1–4 | gemini-3.8-flash 3-vote | **138/152 (90.8%)** | **77.9%** (1,199/1,540, full set) | EVIDENCE.md `locomo_caura_retrieval_accuracy_2026_09` |
| LoCoMo | conv 0 only | gpt-4o (2nd judge) | 140/152 (92.1%) | — | added by us |

> These are **independent reruns of the authors' pipeline family, not bit-for-bit
> reproductions.** The LongMemEval 60 vs their 60 match within run-to-run noise;
> the LoCoMo number is on **one conversation of ten** with a store small enough
> that search returns the whole conversation (see the conv0 caveat below), so it
> is **not comparable to their 1,540-question headline**. Read the per-benchmark
> `comparison.md` before quoting anything.

## Results folders (read these first)

- **LongMemEval:** [`results/lme_caura_60q/`](results/lme_caura_60q/) —
  [`comparison.md`](results/lme_caura_60q/comparison.md) ·
  [`metrics.md`](results/lme_caura_60q/metrics.md) ·
  [`cost.md`](results/lme_caura_60q/cost.md) ·
  [`failures.md`](results/lme_caura_60q/failures.md) ·
  [`per_question.csv`](results/lme_caura_60q/per_question.csv) ·
  [`examples/`](results/lme_caura_60q/examples/) ·
  [`memories/`](results/lme_caura_60q/memories/) (52,921 stored memories).
- **LoCoMo:** [`results/locomo_caura_conv0/`](results/locomo_caura_conv0/) —
  [`comparison.md`](results/locomo_caura_conv0/comparison.md) ·
  [`metrics.md`](results/locomo_caura_conv0/metrics.md) ·
  [`cost.md`](results/locomo_caura_conv0/cost.md) ·
  [`failures.md`](results/locomo_caura_conv0/failures.md) ·
  [`per_question.csv`](results/locomo_caura_conv0/per_question.csv) ·
  [`examples/`](results/locomo_caura_conv0/examples/).

A single self-contained write-up for a paper section
(`memclaw_results_bundle.md`), booktabs tables (`tables.tex`), and a packaged
`memclaw_bundle.tar.gz` live **outside the repo** at
`/shared_mnt/loubna/memclaw_bundle/` (deliberately not committed).

---

## LongMemEval_S

Caura on **LongMemEval_S** following
[`harness/longmemeval/REPRODUCE.md`](harness/longmemeval/REPRODUCE.md) (turn
granularity, opaque labels, `agentic-v1`, raw-turns-only), on our 60-question
subset — 10 per type, the same questions Mem0 was evaluated on.

```bash
# from the repo root, inside tmux — full run (ingest + answer + judge)
bash experiments/scripts/run_lme.sh --stage all --run <name>

# answer only, reusing a store another run already ingested
bash experiments/scripts/run_lme.sh --run <name> --reuse-store <ingest-run>

# resume after a crash / credit pause
bash experiments/scripts/run_lme.sh --resume <name>

# free pre-check (no paid calls; verifies services, TEI dim, 60 ids)
bash experiments/scripts/run_lme.sh --check
```

**Models**

| role | model | provider | matches authors? |
|---|---|---|---|
| reader (answering) | gemini-3.8-flash | Gemini (AI Studio) | ✅ |
| primary judge | gpt-4o (`gpt-4o-2024-08-06`) | OpenAI | ✅ |
| secondary judge | gemini-3.5-flash-lite | Gemini (AI Studio) | ✅ |
| entity extraction | gemini-3.1-flash-lite | Gemini (AI Studio) | ✅ |
| embeddings | BAAI/bge-m3 (1024-d) | local TEI (GPU) | ✅ (same vectors) |
| **memory creation** (title/type/weight/summary) | **gpt-5.4-nano** | **OpenAI** | ❌ **deviation** |

**The one deviation:** memory creation ran on `gpt-5.4-nano` because
`PLATFORM_LLM_*` was left unset, so the write-path classifier fell back to its
OpenAI default; the authors route it through Vertex Gemini. It only sets
per-memory metadata (content and content-embeddings are identical, and derived
memories are dropped in turn mode), so retrieval is unaffected. To match exactly,
set `PLATFORM_LLM_PROVIDER=vertex` / `PLATFORM_LLM_MODEL=gemini-3.1-flash-lite-preview`
and re-ingest.

### Why there are two LongMemEval run folders

**Run 1 — `runs/lme_caura_default_60q_INVALID_retrieval0/`** ingested all 60
questions. **The memory store and its dump are valid.**
- Run 1's Gemini credit ran out during the *answer* step, *after* ingestion
  finished — so all 60 questions were already stored. It paused/resumed 16 times,
  and each question is written only after it is fully answered, so **none were
  skipped**.
- Run 1's *answers* were still invalid: DB crashes made `/search` fail, and the
  upstream harness **silently turned those errors into empty retrievals** (18/60
  retrieved zero candidates). We patched the harness to **raise**
  (`CauraRecallError`) and added a guard.

**Run 2 — `runs/lme_caura_default_60q_answer2/`** re-answered all 60 from scratch
on the same **frozen** store (`--reuse-store`). **This is the valid result
(53/60).**

---

## LoCoMo

Caura on **LoCoMo conversation 0 (conv-26)** following
[`harness/locomo/REPRODUCE.md`](harness/locomo/REPRODUCE.md) with the authors'
published parameters (`chunk_characters=4000`, `top_k=30`, `judge_votes=3`,
`thinking_tokens=2048` on the reader). Every Gemini call (server memory creation,
reader, 3-vote judge) goes through **Vertex AI**; entity extraction stays on AI
Studio; the gpt-4o second judge stays on OpenAI.

```bash
# from the repo root — full run (ingest + answer + 3-vote judge + gpt-4o judge)
bash experiments/scripts/run_locomo.sh --stage all

# free pre-check (no paid calls; a real tiny Vertex call per model to catch 404s)
bash experiments/scripts/run_locomo.sh --check
```

**The conv0 caveat.** The settled conv0 store holds **23 memories**, below
`top_k=30`, so `/search` returns the **whole conversation** for every question —
retrieval does no selection here. Evidence coverage is therefore trivially 100 %
per category, and the number reflects the reader + judge on full context, not
Caura's retrieval. conv0 is also **1 of the 10** conversations in the full set,
so it cannot be compared to the authors' 1,540-question 77.9 %. The authors do
not publish per-category figures for the headline, so per-category comparison is
**"not published"**.

**Vertex ADC.** Auth is **Application Default Credentials** (project + location,
no API key) because org policy blocks service-account keys. The operator's user
ADC (`~/.config/gcloud/application_default_credentials.json`) is read by the
harness and mounted read-only into `core-api`. Full procedure in
[`harness/locomo/CREDENTIALS.md`](harness/locomo/CREDENTIALS.md). The ADC file is
git-ignored and never enters the repo.

---

## Setup (shared)

- **Keys** (repo-root `.env`, git-ignored): `OPENAI_API_KEY` (gpt-4o judge,
  gpt-5.4-nano memory creation on the LME run) and `GEMINI_API_KEY` (AI-Studio
  reader/entity-extraction). The LoCoMo run additionally uses **Vertex ADC** for
  all Gemini traffic (see CREDENTIALS.md). The harness reads its reader/judge
  models from each `harness/*/.env`. All `.env*` and ADC files are git-ignored.
- **Start Caura + the GPU embedder:**
  ```bash
  docker compose up -d                              # core-api, core-storage-api, db (pgvector), redis, core-operations
  docker compose -f experiments/tei-gpu/docker-compose.yml up -d   # caura-tei-gpu: bge-m3 on the GPU, reached as http://tei-gpu:80/v1
  ```
  Embeddings are local (bge-m3, 1024-d) — free, no API key. A
  `docker-compose.override.yml` widens the db/storage healthcheck grace windows
  for the slow-disk VM (no-op elsewhere); the LoCoMo Vertex wiring is in
  `experiments/harness/locomo/docker-compose.vertex.yml`.

## Large files (not in git — only on the VM)

Files over 50 MB are git-ignored (see root `.gitignore`); everything else in both
run folders is committed.

| file | size | what |
|---|---|---|
| `runs/lme_…_INVALID_retrieval0/caura_logs.txt` | ~565 MB | full container logs (forensics) |
| `runs/lme_…_INVALID_retrieval0/memory_dump.sql.gz` | ~325 MB | `pg_dump` of the ingested store |
| `runs/lme_…_INVALID_retrieval0/memories.jsonl` | ~88 MB | per-memory export |

The LoCoMo run's `caura_logs.txt` (~47 MB) is under the limit and **is**
committed.
