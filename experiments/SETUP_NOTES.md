# Caura local stack — setup notes

## 2026-11-25 — `docker compose up -d` failed: `caura-db-1 is unhealthy`

### Symptom
`docker compose up -d` aborted with:
```
dependency failed to start: container caura-db-1 is unhealthy
```

### Root cause
A **slow-first-boot vs. short healthcheck window** race — not a config,
credential, port, or disk-space problem.

The Docker data dir lives on a slow disk (`Docker Root Dir:
/shared_mnt/loubna/docker-data`). On the **first** `up` with empty volumes,
startup steps that only happen once take far longer than the stock healthcheck
grace windows, so Docker flips a container to `unhealthy` mid-startup and every
service that waits on it via `depends_on: condition: service_healthy` aborts the
whole `up`. Two layers were affected:

1. **`db`** — the healthcheck in `docker-compose.yml` has **no `start_period`**
   (it is the only service without one; interval 5s, retries 5 ≈ 25s window).
   On an empty `pgdata` volume Postgres runs its `initdb` bootstrap, which
   starts a temporary server and then **shuts it down for ~40s** to finalize.
   On this disk the shutdown checkpoint alone logged `total=35.680 s,
   sync=24.909 s`. `pg_isready` fails throughout that window, the 5 retries
   expire, and `caura-db-1` is marked unhealthy → `core-storage-api`
   (`depends_on: db: service_healthy`) aborts. This is the exact error reported.

2. **`core-storage-api`** — surfaced once the db race was fixed. It runs the
   full Alembic migration chain (001 → 0xx) on first boot. On this disk that
   took **~3 minutes**, well past its stock `start_period: 10s`, so it too was
   marked unhealthy mid-migration and `core-api` aborted. It recovers on its own
   once migrations finish, but `up` had already given up.

Both are first-boot-only: on later boots the DB is initialized and migrated, so
startup is fast and the stock windows are fine. That matches what was observed —
re-running `up` found `db` already healthy.

### Ruled out (all clean)
- **Port clash with Mem0:** none. Mem0 uses 8890 / 6340 / 8099 on `0.0.0.0`;
  Caura uses 5432 / 6379 bound to `127.0.0.1`. No overlap. No Mem0 container or
  volume was touched.
- **Disk space / Docker location:** `/shared_mnt` has ~934G free (3% used).
  Not full — just slow.
- **`.env` vs `.env.example`:** database settings are identical
  (`POSTGRES_HOST/PORT/USER=caura/PASSWORD/DB=caura`, `POSTGRES_REQUIRE_SSL=false`).
  Nothing missing or differing except inside commented example lines.
- Embedding / LLM settings (`text-embedding-3-small`, `VECTOR_DIM=1024`,
  `gpt-5.4-nano`) were **not** changed.

### Fix (least invasive — no edits to `docker-compose.yml`)
Added `docker-compose.override.yml` (repo root, auto-loaded by Compose) that
only extends the healthcheck grace windows so Docker tolerates the slow first
boot. `start_period` only suppresses *failures* during the window; a passing
check still marks healthy immediately, so there is no downside after first boot.

```yaml
services:
  db:
    healthcheck:
      start_period: 90s
      retries: 10
  core-storage-api:
    healthcheck:
      start_period: 300s
```

No volumes were removed — the data initialized and migrated correctly; the issue
was purely healthcheck timing. The override is committed to the repo: it only
widens healthcheck grace windows (a no-op on fast machines, since the window is
never consumed there), so the lab can rerun this setup from a clean checkout on
the slow-disk VM without reapplying the fix by hand.

### Verification
```
docker compose ps        # db, redis, core-storage-api, core-api, core-operations → all healthy

curl -s -X POST http://localhost:8000/api/v1/search \
  -H "X-API-Key: standalone" -H "Content-Type: application/json" \
  -d '{"tenant_id":"default","query":"test"}'
# → HTTP 200  {"items":[],"recall_tracked":false,"diagnostic":null,"warnings":null,"empty_hint":null}
```

## 2026-11-25 — LongMemEval smoke test setup (Part 1)

### API keys
The server `.env` had an old `OPENAI_API_KEY` and `GEMINI_API_KEY` that both
returned **401** (the reported `token_invalidated`). The harness
`experiments/harness/longmemeval/.env` held newer keys that authenticate (OpenAI
**200** on text-embedding-3-small / gpt-5.4-nano / gpt-4o; Gemini valid). With
the user's confirmation, the server `.env` was updated to the harness's working
OpenAI **and** Gemini keys (exactly one `OPENAI_API_KEY` line in each file).

Security scan: **no real API key is in git history** — a pickaxe search for all
four key bodies returned 0 commits; the only `sk-` matches in history are test
fixtures, regex patterns and PII-detection docs. Both `.env.example` files are
clean (no keys). `.env` files are gitignored and never committed.

Direct key tests (all pass): `text-embedding-3-small` embeddings, `gpt-5.4-nano`,
`gpt-4o`, `gemini-3.8-flash`, `gemini-3.5-flash-lite`.

### Server configuration (kept at REPRODUCE.md-compatible defaults)
- Embeddings: hosted OpenAI `text-embedding-3-small`, `VECTOR_DIM=1024`
  (`EMBEDDING_PROVIDER=openai`, no TEI base-url override).
- Enrichment / server-derived memories: `gpt-5.4-nano`
  (`ENTITY_EXTRACTION_PROVIDER=openai`, `ENTITY_EXTRACTION_MODEL=gpt-5.4-nano`).
- `IS_STANDALONE=true`, `CAURA_TELEMETRY=off`.

### Deviations from the published headline run (REPRODUCE.md)
These are intentional local choices; REPRODUCE.md's published numbers used:
- **Embeddings:** we use OpenAI **`text-embedding-3-small`** (1024-dim) instead
  of the published **`BAAI/bge-m3`** local TEI embedder.
- **Server-side derivation LLM:** we use **`gpt-5.4-nano`** instead of the
  published **`gemini-3.1-flash-lite`** (via Vertex).
Everything else (reader `gemini-3.8-flash`, judges `gpt-4o` +
`gemini-3.5-flash-lite`, and all retrieval flags) matches the headline run.
These two deviations are also recorded in each run's `manifest.json`
(`deviations_from_published`).

### Smoke test result (2 questions, headline flags)
Ingest (turns mode, `lmeo` prefix) OK via OpenAI embeddings. Answer step +
`evaluate-hypotheses`: **2/2 correct** under both `gpt-4o`
(resolved `gpt-4o-2024-08-06`) and `gemini-3.5-flash-lite`, 2/2 agreement.
Reader (`gemini-3.8-flash`) used ≈9,344 tokens/question (prompt ≈8,749,
output ≈595). Smoke memories deleted afterwards; store confirmed empty.

> Transient note: during setup the Gemini key hit `503 high demand` and later a
> `402 RESOURCE_EXHAUSTED` (prepayment credits depleted); after a top-up both
> `gemini-3.8-flash` and `gemini-3.5-flash-lite` responded and the smoke passed.

## 2026-11-25 — Server switched to authors' published settings (variant 1)

The LongMemEval reproduction uses the **authors' published** server config
(REPRODUCE.md), not `.env.example` defaults:

- **Embedder:** `BAAI/bge-m3` (1024-dim) via HuggingFace TEI. Because the H100
  MIG slice is already used by Mem0's `qwen-tei` (which must keep running), our
  bge-m3 TEI runs as a **separate GPU container** sharing the slice: compose
  project `caura-tei-gpu` (`experiments/tei-gpu/docker-compose.yml`), image
  `text-embeddings-inference:hopper-1.8`, host port `127.0.0.1:8100`, joined to
  `caura_default` as alias `tei-gpu`. `core-api` reaches it via
  `OPENAI_EMBEDDING_BASE_URL=http://tei-gpu:80/v1`
  (`OPENAI_EMBEDDING_SEND_DIMENSIONS=false`, `VECTOR_DIM=1024`). The CPU `tei`
  container is stopped. nvidia-smi shows both embedders resident (~1.3 GB each,
  ~18.8 GB free); Mem0 was never touched.
- **Enrichment:** `ENTITY_EXTRACTION_PROVIDER=gemini`,
  `ENTITY_EXTRACTION_MODEL=gemini-3.1-flash-lite` (published used the same model
  via Vertex; we use the Gemini API key). `IS_STANDALONE=true`,
  `CAURA_TELEMETRY=off`.
- The store was wiped before switching (only `caura_pgdata` removed; Mem0
  volumes untouched) because earlier memories were embedded with
  text-embedding-3-small.

**Variant 2 (planned later):** OpenAI hosted `text-embedding-3-small` embeddings
+ `gpt-5.4-nano` enrichment (the config from the earlier Part-1 smoke). Kept as a
documented alternative to run after the published-settings reproduction; switch
by unsetting the `OPENAI_EMBEDDING_BASE_URL`/`_MODEL`/`_SEND_DIMENSIONS` block and
setting `ENTITY_EXTRACTION_PROVIDER=openai` / `ENTITY_EXTRACTION_MODEL=gpt-5.4-nano`,
then wipe `caura_pgdata` and re-ingest.

## 2026-11-25 — Raised bulk-write rate limit (speed-only)

Ingestion at `--concurrency 5` hit Caura's bulk-write rate limit:
`#ad7109d1 failed: Caura bulk write failed [429]: Rate limit exceeded: 2 per 1
second`. The cap is a config value, `core_api.config.rate_limit_write_bulk`
(default **`2/second`** per credential, env `RATE_LIMIT_WRITE_BULK`; tenant cap
`rate_limit_write_bulk_tenant` default `10/second`, env
`RATE_LIMIT_WRITE_BULK_TENANT`). Each bulk call fans out to 100 memories, so the
default throttles 5 concurrent writers.

In standalone single-user mode this is pure admission throttling, so raised it in
`.env` (restart core-api only):

```ini
RATE_LIMIT_WRITE_BULK=50/second
RATE_LIMIT_WRITE_BULK_TENANT=100/second
```

**Speed-only — no effect on what is stored or on results.** It only lets the
published `--concurrency 5` ingest proceed without 429s. Also recorded in each
run's `manifest.json` under `server_tuning`. `run_lme.sh` additionally retries
any still-unfinished question (429 or other) up to 5× — resetting its memories
before each attempt — and stops the run if one cannot complete.

## 2026-11-25 — Recall silently answered from empty context (reliability fix)

### Symptom
The run `lme_caura_default_60q` "finished" at **18/60** (gpt-4o) vs the authors'
**51/60** on the same questions. **39–40 of the 60 questions retrieved zero
candidates** (`search_candidates=0`, empty context, flat ~15.1 s), while the
store was fully populated (~900 memories/agent, 52,921 total).

### Root cause
Two layers, both triggered by the end-of-run burst landing on a Postgres crash:

1. **Infra (transient):** the Gemini reader key was credit-exhausted for most of
   the run (16 × 15-min pause/resume), so the bulk of answering happened in a
   late burst — exactly when Postgres crashed and restarted (backend `exit code
   2` at 15:56, 16:47, 16:51; a `/search` 503 storm at 16:40–16:49 while
   core-storage reported "every pooled connection is serving a request"). TEI
   (local, free) was healthy throughout; the live path works now.
2. **Harness (the bug that made it *invalid* instead of merely slow):**
   `providers/caura.py::_search_once` did `if resp.status_code != 200: return []`
   — so a `/search` 5xx (surfaced to the client as 503 after the harness retried
   1+2+4+8 = ~15 s) was **silently turned into an empty candidate list**. The
   reader then answered from an empty context and was scored wrong, with nothing
   raised to stop the run.

### Fix (speed/reliability only — no effect on healthy-run results)
- `providers/caura.py`: `/search` retry budget raised to **8 attempts with a 30 s
  backoff cap** (`_request` gained `max_delay`) so a brief DB restart is ridden
  out; a 5xx/429 that still survives now raises **`CauraRecallError`** instead of
  returning `[]`. The 200 path is unchanged, so a healthy run scores identically.
- `run_lme.sh`: new `assert_retrieval_nonempty()` aborts the run **before the paid
  judge** if any answered question retrieved 0 candidates.
- Recorded in each run's `manifest.json` under `harness_reliability_patches`.

### Collection bugs fixed at the same time (pure post-processing)
- `collect_traces.py` ran `load_jsonl(memories.jsonl)` whose rows have no
  `question_id` → **`KeyError: 'question_id'`**, crashing before any trace was
  written → empty `traces/` → `build_summary.py` reported **gpt-4o 0/0**. Removed
  the dead call.
- `run_lme.sh::write_mem_counts` piped SQL into `python3 - <<'PY'`; the heredoc
  **is** stdin, so the rows never reached `sys.stdin` and `mem_counts.json` was
  **always `{}`** (not a DB-recovery artifact). Now reads the rows from a temp
  file passed by argv.
- `build_summary.py` now derives accuracy from `eval_results.json` (+ question
  types from `hypotheses.jsonl`), not from `traces/`, so the headline can't be
  silently zeroed by a half-failed collector.
- `context_tokens.py` guarded the denominator (`median(ctx_chars)` can be 0 when
  most contexts are empty) → no more `ZeroDivisionError`.

All four collection scripts were re-run on the first run's data (free): they now
produce gpt-4o **18/60** with the correct per-type split and 52,921 stored
memories — confirming the fixes without touching the preserved run.

### Rerunning
The store is intact, so only answer+judge need redoing (~$0.76, see below):
```
experiments/scripts/run_lme.sh --run lme_caura_default_60q_answer2 \
  --reuse-store lme_caura_default_60q_INVALID_retrieval0
```
`--reuse-store` answers against the already-ingested `lmeo-*` store (no
re-ingest, no copy); the source run's `ingest.log`/`memory_dump.sql.gz` are
referenced in the new run's `manifest.json` under `reuses_artifacts`.
