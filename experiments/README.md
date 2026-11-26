# Caura (MemClaw) on LongMemEval_S — 60-question subset

**What this is.** Caura / MemClaw run on **LongMemEval_S** following the authors'
`harness/longmemeval/REPRODUCE.md` pipeline (turn granularity, opaque labels,
`agentic-v1`, raw-turns-only), on our **60-question subset** — 10 per type, the
same questions Mem0 was evaluated on.

**Headline result:** **53/60 (88.3%)** under the gpt-4o judge (52/60 Flash-Lite),
vs the authors' **51/60** on the same 60 — a match within normal variation.
➡️ Read **[`results/lme_caura_60q/`](results/lme_caura_60q/)** first
(`comparison.md`, `per_question.csv`, `failures.md`, `cost.md`).

## Run it (from the repo root, inside tmux)

```bash
# full run (ingest + answer + judge)
bash experiments/scripts/run_lme.sh --stage all --run <name>

# answer only, reusing a store another run already ingested
bash experiments/scripts/run_lme.sh --run <name> --reuse-store <ingest-run>

# resume after a crash/credit pause
bash experiments/scripts/run_lme.sh --resume <name>

# free pre-check (no paid calls; verifies services, TEI dim, 60 ids)
bash experiments/scripts/run_lme.sh --check
```

## Setup

- **Keys** (in the repo-root `.env`, git-ignored): `OPENAI_API_KEY` (gpt-4o judge)
  and `GEMINI_API_KEY` (reader, secondary judge, entity extraction). The harness
  reads reader/judge models from `harness/longmemeval/.env`.
- **Start Caura + the GPU embedder:**
  ```bash
  docker compose up -d                 # core-api, core-storage-api, db (pgvector), redis, core-operations
  docker compose -f <gpu-tei>.yml up -d # caura-tei-gpu: bge-m3 on the GPU, reached as http://tei-gpu:80/v1
  ```
  Embeddings are local (bge-m3, 1024-d) — free, no API key.

## Models

| role | model | provider | matches authors? |
|---|---|---|---|
| reader (answering) | gemini-3.8-flash | Gemini (AI Studio) | ✅ |
| primary judge | gpt-4o | OpenAI | ✅ |
| secondary judge | gemini-3.5-flash-lite | Gemini (AI Studio) | ✅ |
| entity extraction | gemini-3.1-flash-lite | Gemini (AI Studio) | ✅ |
| embeddings | BAAI/bge-m3 (1024-d) | local TEI (GPU) | ✅ (same vectors) |
| **memory creation** (title/type/weight/summary) | **gpt-5.4-nano** | **OpenAI** | ❌ **deviation** |

**The one deviation:** memory creation ran on `gpt-5.4-nano` because `PLATFORM_LLM_*`
was left unset, so the server's write-path classifier fell back to its OpenAI default.
The authors route it through Vertex Gemini. It only sets per-memory metadata
(content and content-embeddings are identical, and derived memories are dropped in
turn mode), so retrieval is unaffected. To match the authors exactly, set and re-ingest:

```bash
PLATFORM_LLM_PROVIDER=vertex
PLATFORM_LLM_MODEL=gemini-3.1-flash-lite-preview
```

## Why there are two run folders

**Run 1 — `runs/lme_caura_default_60q_INVALID_retrieval0/`** ingested all 60
questions. **The memory store and its dump are valid.**
- Run 1's Gemini credit ran out during the *answer* step, *after* ingestion had
  finished — so all 60 questions were already stored. It paused/resumed 16 times, and
  each question is written only after it is fully answered, so **none were skipped**.
- Run 1's *answers* were still invalid: DB crashes made `/search` fail, and the
  upstream harness **silently turned those errors into empty retrievals** (18/60). We
  patched the harness to **raise** instead (`CauraRecallError`) and added a guard.

**Run 2 — `runs/lme_caura_default_60q_answer2/`** re-answered all 60 from scratch on
the same **frozen** store (`--reuse-store`). **This is the valid result (53/60).**

## Large files (not in git — only on the VM)

Under `/shared_mnt/loubna/caura/experiments/runs/lme_caura_default_60q_INVALID_retrieval0/`:

| file | size | what |
|---|---|---|
| `caura_logs.txt` | ~564 MB | full container logs (forensics) |
| `memory_dump.sql.gz` | ~325 MB | `pg_dump` of the ingested store |
| `memories.jsonl` | ~88 MB | per-memory export |

These exceed 50 MB and are git-ignored; everything else in both run folders is committed.
