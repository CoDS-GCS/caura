# Per-question memories

One gzipped JSON-Lines file per question — `<question_id>.jsonl.gz` — holding
**every memory stored for agent `lmeo-<question_id>`**. One JSON object per line.

**Source & run.** These are the **frozen store** that the valid run
(`runs/lme_caura_default_60q_answer2/`) read. They were ingested once by
**`runs/lme_caura_default_60q_INVALID_retrieval0/`** (all memories created by
12:11 UTC on 25 Nov 2026; nothing changed during or after answering). Exported
from the live Postgres store (`deleted_at IS NULL`), which is byte-identical to
that snapshot.

**Total: 52,921 memories across 60 questions** — the per-file counts match
`../mem_counts.json` exactly. Largest file ~260 KB (all well under 20 MB).

## Fields

| field | meaning |
|---|---|
| `id` | memory UUID (matches the IDs in the retrieval traces) |
| `type` | memory type — `fact` / `episode` / `task` / `plan` / `preference` / `decision` / `action` (LLM-classified) |
| `title` | short LLM-written title |
| `summary` | short LLM-written summary (`metadata.summary`) |
| `tags` | `null` — this store has no tags (the classifier produced type/weight/title/summary, not tags) |
| `status` | `active` / `outdated` / `conflicted` |
| `weight` | importance weight assigned on write |
| `content` | the memory text — here the **verbatim raw conversation turn** (`[date … | turn i/n] User/Assistant: …`) |
| `created_at`, `observed_at` | when the memory row was written / when the event was observed |
| `ts_valid_start`, `ts_valid_end` | validity interval (temporal reasoning) |

## Deviation note

`type`, `weight`, `title`, and `summary` were produced by the server's
memory-creation classifier, which in this run ran on **`gpt-5.4-nano` (OpenAI)** —
the one deviation from the authors, who route it through Vertex Gemini
(`PLATFORM_LLM_*` was unset, so it fell to the OpenAI default). The **`content`
(raw turns) and the retrieval embeddings are unaffected**; `--raw-turns-only`
retrieval reads the raw-turn content, so this metadata choice does not change which
memories are retrieved. See `../../../README.md` → Models.
