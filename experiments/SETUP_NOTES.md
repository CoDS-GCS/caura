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
