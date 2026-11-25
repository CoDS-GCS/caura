#!/usr/bin/env bash
#
# run_lme.sh — reproduce Caura's published LongMemEval_S headline config on the
# fixed 60-question Mem0 subset, with pause/resume on credit exhaustion and full
# result collection. Run it yourself in tmux; it never starts on its own.
#
#   experiments/scripts/run_lme.sh [--stage ingest|answer|all] [--run NAME]
#                                  [--ids FILE] [--mem0-judge]
#   experiments/scripts/run_lme.sh --resume NAME        # continue after a crash/pause
#
# Robustness:
#   * On Gemini 402 RESOURCE_EXHAUSTED or OpenAI insufficient_quota, it stops
#     sending, writes $OUT/PAUSED + logs $OUT/pauses.log, then probes the failed
#     model with one tiny request every 15 min for up to 12 h and resumes
#     automatically. After 12 h it exits cleanly.
#   * The harness resumes both phases from disk (ingested.txt / hypotheses.jsonl),
#     so a killed/paused step never redoes finished questions and never leaves a
#     half-ingested or half-answered question (unfinished ones are reset+redone).
#   * No early-kill timeouts (60-question ingest ~5 h on the shared GPU).
#
# NOTE: the setup is final and the 2-question test passed, so the pre-checks here
# deliberately call NO paid models (only the local TEI embedder, which is free).
set -uo pipefail       # not -e: we handle failures explicitly for pause/resume

REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
HARNESS="$REPO/experiments/harness/longmemeval"
SCRIPTS="$REPO/experiments/scripts"
RUN="${RUN:-lme_caura_default_60q}"
IDS="${IDS:-$REPO/experiments/configs/lme_subset60_from_mem0.txt}"
STAGE="${STAGE:-all}"
AGENT_PREFIX="lmeo"
MEM0_JUDGE="${MEM0_JUDGE:-0}"
RESUME=0
CHECK=0
CAURA_BASE="${CAURA_BASE:-http://localhost:8000/api/v1}"
API_KEY="${CAURA_API_KEY:-standalone}"
TENANT="${CAURA_TENANT_ID:-default}"
POLL_SECS="${POLL_SECS:-900}"      # 15 min
MAX_POLLS="${MAX_POLLS:-48}"       # 48 x 15 min = 12 h

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2;;
    --run) RUN="$2"; shift 2;;
    --ids) IDS="$2"; shift 2;;
    --mem0-judge) MEM0_JUDGE=1; shift;;
    --check) CHECK=1; shift;;
    --resume) RESUME=1; RUN="$2"; shift 2;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
OUT="$REPO/experiments/runs/$RUN"
export PYTHONUNBUFFERED=1

log()  { echo "[$(date -Is)] $*"; }
die()  { echo "[$(date -Is)] FATAL: $*" >&2; exit 1; }
psqlc(){ docker exec caura-db-1 psql -U caura -d caura -tA "$@"; }

# ---------------------------------------------------------------- checkpoint
ckpt() { echo "$OUT/checkpoint.json"; }
ckpt_init() { [[ -f "$(ckpt)" ]] || echo "{\"run\":\"$RUN\",\"ids\":\"$IDS\",\"ingest_done\":false,\"judged\":false,\"finished\":false}" > "$(ckpt)"; }
ckpt_set() { # key (json value)
  python3 - "$(ckpt)" "$1" "$2" <<'PY'
import json,sys
p,k,v=sys.argv[1],sys.argv[2],sys.argv[3]
d=json.load(open(p));
try: v=json.loads(v)
except Exception: pass
d[k]=v; json.dump(d,open(p,"w"),indent=2)
PY
}
ckpt_get() { python3 -c "import json;print(json.load(open('$(ckpt)')).get('$1'))" 2>/dev/null || echo None; }

ids_list() { grep -vE '^\s*#|^\s*$' "$IDS"; }
answered_ids() { [[ -f "$HARNESS/outputs/$RUN/hypotheses.jsonl" ]] && python3 -c "import json,sys;[print(json.loads(l)['question_id']) for l in open('$HARNESS/outputs/$RUN/hypotheses.jsonl') if l.strip()]" | sort -u || true; }
ingested_ids() { [[ -f "$HARNESS/outputs/$RUN/ingested.txt" ]] && sort -u "$HARNESS/outputs/$RUN/ingested.txt" || true; }

# ---------------------------------------------------------------- credit pause/resume
probe_model() { # provider model  -> rc 0 if the model answers a tiny request
  local prov="$1" model="$2"
  if [[ "$prov" == openai ]]; then
    local code
    code=$(curl -s -o /dev/null -w '%{http_code}' https://api.openai.com/v1/chat/completions \
      -H "Authorization: Bearer $(grep -E '^OPENAI_API_KEY=' "$REPO/.env"|head -1|cut -d= -f2-)" \
      -H 'Content-Type: application/json' \
      -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_completion_tokens\":1}")
    [[ "$code" == 200 ]]
  else
    ( cd "$HARNESS" && uv run python - "$model" <<'PY'
import os,sys
from google import genai
from google.genai import types
try:
    c=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    c.models.generate_content(model=sys.argv[1],contents="ok",
        config=types.GenerateContentConfig(max_output_tokens=1,temperature=0.0))
    sys.exit(0)
except Exception as e:
    sys.exit(1 if ("402" in str(e) or "RESOURCE_EXHAUSTED" in str(e)) else 0)  # non-credit error: treat as "up"
PY
    ) >/dev/null 2>&1
  fi
}

wait_for_credit() { # provider model reason_phase  -> rc 0 restored, 3 timed out
  local prov="$1" model="$2" phase="$3"
  local inprog; inprog="$(comm -23 <(ids_list|sort -u) <( { answered_ids; ingested_ids; } |sort -u) | tr '\n' ' ')"
  local reason="$prov/$model exhausted during $phase; in-progress/remaining: $inprog"
  echo "$reason" > "$OUT/PAUSED"
  echo "[$(date -Is)] PAUSE  $reason" >> "$OUT/pauses.log"
  log "PAUSED: $reason  — probing $model every $((POLL_SECS/60))min for up to $((MAX_POLLS*POLL_SECS/3600))h"
  local i=0
  while (( i < MAX_POLLS )); do
    sleep "$POLL_SECS"
    i=$((i+1))
    if probe_model "$prov" "$model"; then
      echo "[$(date -Is)] RESUME after $((i*POLL_SECS/60))min  $prov/$model back" >> "$OUT/pauses.log"
      rm -f "$OUT/PAUSED"
      log "RESUMED: $prov/$model is back after ~$((i*POLL_SECS/60))min"
      return 0
    fi
    log "  still exhausted ($prov/$model), poll $i/$MAX_POLLS"
  done
  echo "[$(date -Is)] GAVE UP after 12h waiting on $prov/$model" >> "$OUT/pauses.log"
  log "credit not restored within 12h — exiting cleanly; rerun with: run_lme.sh --resume $RUN"
  return 3
}

detect_and_wait() { # logfile phase  -> rc 0 resume, 1 non-credit, 3 timed-out
  local lf="$1" phase="$2"
  if grep -qiE 'insufficient_quota|exceeded your current quota' "$lf"; then
    wait_for_credit openai gpt-4o "$phase"; return $?
  elif grep -qiE '402|RESOURCE_EXHAUSTED|prepayment credits are depleted' "$lf"; then
    wait_for_credit gemini gemini-3.8-flash "$phase"; return $?
  fi
  return 1
}

run_resumable() { # phase logfile -- cmd...   (harness resumes finished questions itself)
  local phase="$1" lf="$2"; shift 2; [[ "$1" == "--" ]] && shift
  while :; do
    ( cd "$HARNESS" && "$@" ) 2>&1 | tee -a "$lf"
    local rc=${PIPESTATUS[0]}
    [[ $rc -eq 0 ]] && return 0
    detect_and_wait "$lf" "$phase"; local d=$?
    case $d in
      0) log "resuming $phase after credit restored"; continue;;
      3) exit 0;;                         # clean 12h exit
      *) die "$phase failed (rc=$rc), not a credit error — see $lf";;
    esac
  done
}

# ---------------------------------------------------------------- pre-checks (FREE only)
prechecks() {
  log "pre-checks (free only; no paid model calls)"
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "$CAURA_BASE/health" -H "X-API-Key: $API_KEY")" == 200 ]] \
    || die "Caura /health not 200"
  local unhealthy; unhealthy="$(cd "$REPO" && docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$2!="healthy"{print}')"
  [[ -z "$unhealthy" ]] || die "unhealthy caura services:\n$unhealthy"
  docker inspect caura-tei-gpu --format '{{.State.Health.Status}}' 2>/dev/null | grep -qx healthy \
    || die "caura-tei-gpu (bge-m3 GPU sidecar) not healthy"
  local env_pref cont_pref
  env_pref="$(grep -E '^OPENAI_API_KEY=' "$REPO/.env"|head -1|cut -d= -f2-|cut -c1-6)"
  cont_pref="$(docker exec caura-core-api-1 sh -c 'printenv OPENAI_API_KEY'|cut -c1-6)"
  [[ -n "$env_pref" && "$env_pref" == "$cont_pref" ]] || die "core-api OpenAI key prefix ($cont_pref) != .env ($env_pref)"
  # TEI dim check — LOCAL embedder on the host GPU port 8100, free (no paid model).
  # Uses TEI's native /embed (returns [[...1024...]]), parsed with host python3 —
  # no `docker exec` (its stdin isn't forwarded without -i), no jq, no in-container DNS.
  local tei_port="${TEI_GPU_PORT:-8100}" dim
  dim="$(curl -s -X POST "http://127.0.0.1:${tei_port}/embed" \
          -H 'Content-Type: application/json' -d '{"inputs":"precheck"}' \
        | python3 -c "import json,sys
try:
    v=json.load(sys.stdin)
    print(len(v[0]) if isinstance(v,list) and v and isinstance(v[0],list) else 0)
except Exception:
    print(0)")"
  [[ "$dim" == 1024 ]] || die "TEI dim='$dim' != 1024 (GPU TEI at 127.0.0.1:${tei_port}/embed)"
  ( cd "$HARNESS" && uv run python - "$IDS" <<'PY'
import sys,collections
from longmemeval.dataset import LongMemEvalDataset, read_question_id_list
ids=read_question_id_list(sys.argv[1]); assert len(ids)==60,len(ids)
ds=LongMemEvalDataset(data_path="data/longmemeval_s_cleaned.json")
items=ds.load_items(include_ids=ids); assert len(items)==60
c=collections.Counter(i.question_type for i in items); assert set(c.values())=={10} and len(c)==6,dict(c)
PY
) || die "IDS check failed (expected 60, 10/type)"
  local agents; agents="$(curl -s "$CAURA_BASE/memories/stats" -H "X-API-Key: $API_KEY" | python3 -c "import json,sys;print(sum(1 for a in json.load(sys.stdin).get('by_agent',{}) if a.startswith('$AGENT_PREFIX-')))")"
  if [[ "$STAGE" == answer && "$RESUME" == 0 ]]; then
    [[ "$agents" == 60 ]] || die "answer stage needs 60 ingested agents, found $agents"
  elif [[ "$STAGE" != answer && "$RESUME" == 0 ]]; then
    [[ "$agents" == 0 ]] || die "store not empty for '$AGENT_PREFIX-' ($agents); clean up first"
  fi
  log "pre-checks passed (TEI 1024-dim, key prefix $cont_pref, 60 ids 10/type)"
}

# ---------------------------------------------------------------- DB snapshots
write_mem_counts() { # -> $OUT/mem_counts.json
  psqlc -F$'\t' -c "SELECT agent_id, memory_type, status, (metadata->>'doc_id' IS NULL) AS derived, count(*) FROM memories WHERE agent_id LIKE '${AGENT_PREFIX}-%' AND deleted_at IS NULL GROUP BY 1,2,3,4" \
  | python3 - "$OUT/mem_counts.json" <<'PY'
import json,sys,collections
out=collections.defaultdict(lambda:{"total":0,"by_type":collections.Counter(),"by_status":collections.Counter(),"derived":0})
for line in sys.stdin:
    line=line.rstrip("\n")
    if not line: continue
    a,mt,st,der,n=line.split("\t"); n=int(n)
    o=out[a]; o["total"]+=n; o["by_type"][mt]+=n; o["by_status"][st]+=n
    if der=="t": o["derived"]+=n
for a in out: out[a]["by_type"]=dict(out[a]["by_type"]); out[a]["by_status"]=dict(out[a]["by_status"])
json.dump(out, open(sys.argv[1],"w"), indent=2)
print("mem_counts ->", sys.argv[1])
PY
}
enrichment_pending_agents() { # prints agents still pending enrichment or empty
  psqlc -F$'\t' -c "SELECT agent_id, count(*) FILTER (WHERE COALESCE(metadata->'_system'->>'enrichment_pending', metadata->>'enrichment_pending')='true') FROM memories WHERE agent_id LIKE '${AGENT_PREFIX}-%' AND deleted_at IS NULL GROUP BY 1" \
  | awk -F'\t' '$2>0{print $1}'
}

# ---------------------------------------------------------------- stages
do_ingest() {
  log "=== INGEST (published flags) -> $OUT/ingest.log ==="
  run_resumable ingest "$OUT/ingest.log" -- \
    uv run longmemeval ingest --name "$RUN" --provider caura \
      --chunk-mode turns --agent-prefix "$AGENT_PREFIX" \
      --bulk-size 100 --concurrency 5 --question-ids "$IDS"

  # The bulk pass exits 0 even if some questions failed (429 rate-limit or
  # other) — they are simply absent from ingested.txt. Retry each such question
  # up to 5 times with exponential backoff. `ingest --question-id` resets
  # (DELETES) that question's partial memories before re-ingesting from scratch,
  # so a question is never left half-ingested and never silently skipped. If one
  # still fails after 5 tries, stop the whole run with a clear error.
  local qid n backoff ok; local -a missing
  mapfile -t missing < <(comm -23 <(ids_list | sort -u) <(ingested_ids))
  if (( ${#missing[@]} )); then
    log "bulk pass left ${#missing[@]} question(s) unfinished; retrying each (<=5x, reset before each): ${missing[*]}"
  fi
  for qid in "${missing[@]}"; do
    [[ -z "$qid" ]] && continue
    ok=0; backoff=5
    for n in 1 2 3 4 5; do
      log "ingest retry $qid (attempt $n/5; deletes its memories, then re-ingests)"
      ( cd "$HARNESS" && uv run longmemeval ingest --name "$RUN" --provider caura \
          --chunk-mode turns --agent-prefix "$AGENT_PREFIX" --bulk-size 100 \
          --concurrency 1 --question-id "$qid" ) 2>&1 | tee -a "$OUT/ingest.log"
      if ingested_ids | grep -qxF "$qid"; then ok=1; break; fi
      log "  $qid attempt $n/5 did not complete; backing off ${backoff}s"
      sleep "$backoff"; backoff=$((backoff*2))
    done
    (( ok )) || die "question $qid failed to ingest after 5 retries — stopping run (see $OUT/ingest.log)"
  done

  # Enrichment is server-side + async (embeddings are local TEI, which cannot
  # exhaust credit). Drain it, pausing on a Gemini 402 in the core-api log, and
  # re-ingest any question left un-enriched so none is half-done.
  log "draining server-side enrichment (gemini-3.1-flash-lite)…"
  local tries=0
  while :; do
    local pend; pend="$(enrichment_pending_agents)"
    local since2; since2="2m"
    if [[ -z "$pend" ]]; then log "enrichment drained"; break; fi
    if (cd "$REPO" && docker compose logs core-api --since 3m 2>&1) | grep -qiE '402|RESOURCE_EXHAUSTED|prepayment credits are depleted'; then
      wait_for_credit gemini gemini-3.1-flash-lite "ingest-enrichment" || exit 0
    fi
    tries=$((tries+1)); (( tries > 240 )) && { log "enrichment still pending after long drain; re-ingesting pending questions"; break; }
    sleep 30
  done
  # guarantee completeness: reset + re-ingest any agent still pending / empty
  local bad; bad="$(enrichment_pending_agents)"
  if [[ -n "$bad" ]]; then
    for a in $bad; do
      local qid="${a#${AGENT_PREFIX}-}"
      log "re-ingesting $qid (incomplete enrichment)"
      curl -s -o /dev/null -X DELETE "$CAURA_BASE/memories?tenant_id=$TENANT&agent_id=$a&fleet_id=$a" -H "X-API-Key: $API_KEY"
      run_resumable ingest "$OUT/ingest.log" -- \
        uv run longmemeval ingest --name "$RUN" --provider caura --chunk-mode turns \
          --agent-prefix "$AGENT_PREFIX" --bulk-size 100 --concurrency 1 --question-id "$qid"
    done
  fi

  { echo "---- per-agent memory counts after ingest (agent | memories) ----"
    curl -s "$CAURA_BASE/memories/stats" -H "X-API-Key: $API_KEY" | python3 -c "import json,sys;[print(f'{a} | {n}') for a,n in sorted(json.load(sys.stdin).get('by_agent',{}).items()) if a.startswith('$AGENT_PREFIX-')]"
  } | tee -a "$OUT/ingest.log"

  log "=== DB snapshots right after ingest ==="
  write_mem_counts
  docker exec caura-db-1 pg_dump -U caura -d caura | gzip > "$OUT/memory_dump.sql.gz"
  psqlc -c "SELECT json_build_object('id',id,'agent_id',agent_id,'memory_type',memory_type,'status',status,'title',title,'content',content,'metadata',metadata) FROM memories WHERE agent_id LIKE '${AGENT_PREFIX}-%' AND deleted_at IS NULL" > "$OUT/memories.jsonl"
  log "memory_dump.sql.gz=$(du -h "$OUT/memory_dump.sql.gz"|cut -f1), memories.jsonl=$(wc -l < "$OUT/memories.jsonl") rows"
  ckpt_set ingest_done true
}

do_answer() {
  log "=== RUN (published flags) -> $OUT/run.log ==="
  run_resumable answer "$OUT/run.log" -- \
    uv run longmemeval run --name "$RUN" --provider caura --skip-ingest \
      --chunk-mode turns --agent-prefix "$AGENT_PREFIX" \
      --sibling-expansion --sibling-window 0 --context-budget 150000 \
      --raw-turns-only --no-as-of-recall --context-format compact \
      --pipeline agentic-v1 \
      --reader gemini --reader-model gemini-3.8-flash \
      --judge openai --judge-model gpt-4o \
      --secondary-judge gemini --secondary-judge-model gemini-3.5-flash-lite \
      --concurrency 5 --question-ids "$IDS"

  log "=== JUDGE: evaluate-hypotheses -> $OUT/eval.log ==="
  run_resumable judge "$OUT/eval.log" -- \
    uv run longmemeval evaluate-hypotheses "outputs/$RUN/hypotheses.jsonl" \
      --judge openai --judge-model gpt-4o \
      --secondary-judge gemini --secondary-judge-model gemini-3.5-flash-lite --no-html
  ckpt_set judged true

  if [[ "$MEM0_JUDGE" == 1 ]]; then
    log "=== Mem0 parity judge (gpt-5 + Mem0 prompt) -> $OUT/eval_mem0judge.json ==="
    run_resumable mem0judge "$OUT/eval.log" -- \
      uv run python "$SCRIPTS/mem0_judge.py" "outputs/$RUN/hypotheses.jsonl" -o "$OUT/eval_mem0judge.json" --model gpt-5
  fi

  log "=== analysis: context tokens + coverage (authors' scripts) ==="
  ( cd "$HARNESS" && uv run python scripts/context_tokens.py "outputs/$RUN" ) 2>&1 | tee -a "$OUT/eval.log" || log "context_tokens.py failed (non-fatal)"
  ( cd "$HARNESS" && uv run python scripts/coverage.py "outputs/$RUN" --json "$OUT/coverage.json" ) 2>&1 | tee -a "$OUT/eval.log" || log "coverage.py failed (non-fatal)"

  log "=== collection: traces / summary / compare_authors ==="
  write_mem_counts
  uv run --project "$HARNESS" python "$SCRIPTS/collect_traces.py" --run "$RUN" --out "$OUT" --harness "$HARNESS" --mem-counts "$OUT/mem_counts.json"
  uv run --project "$HARNESS" python "$SCRIPTS/build_summary.py" --run "$RUN" --out "$OUT" --harness "$HARNESS" --prices "$REPO/experiments/configs/prices.json"
  uv run --project "$HARNESS" python "$SCRIPTS/compare_authors.py" --run "$RUN" --out "$OUT" --harness "$HARNESS" \
      --authors-verdicts "$REPO/experiments/configs/authors_headline_60q_verdicts.json"

  mkdir -p "$OUT/outputs"; cp -a "$HARNESS/outputs/$RUN/." "$OUT/outputs/" 2>/dev/null || true
  local since; since="$(python3 -c "import json;print(json.load(open('$OUT/manifest.json'))['started_utc'])" 2>/dev/null || echo 12h)"
  ( cd "$REPO" && docker compose logs --since "$since" > "$OUT/caura_logs.txt" 2>&1 ) || true
  ckpt_set finished true
}

# ---------------------------------------------------------------- manifest (reuse from prior version, redacted)
write_manifest() { "$SCRIPTS/_lme_manifest.sh" "$1" "$RUN" "$IDS" "$OUT" "$HARNESS" "$REPO" "$CAURA_BASE" 2>/dev/null || true; }

# ---------------------------------------------------------------- main
if [[ "$RESUME" == 1 ]]; then
  [[ -d "$OUT" ]] || die "--resume: $OUT does not exist"
  log "RESUMING $RUN (ingest_done=$(ckpt_get ingest_done), judged=$(ckpt_get judged))"
  [[ "$(ckpt_get ingest_done)" == True ]] && STAGE=answer || STAGE=all
elif [[ "$CHECK" == 0 ]]; then
  if [[ "$STAGE" == ingest || "$STAGE" == all ]]; then
    [[ -e "$OUT" ]] && die "run dir exists: $OUT (use --resume $RUN to continue)"
  fi
  [[ "$STAGE" == answer && ! -d "$OUT" ]] && die "answer stage: $OUT missing (run --stage ingest first)"
fi
# Pre-checks run BEFORE the run dir is created, so a failed pre-check never
# leaves a folder behind (next start won't refuse with "run dir exists").
prechecks
if [[ "$CHECK" == 1 ]]; then
  log "pre-checks passed (--check: no run dir created, no ingestion, no paid calls)"
  exit 0
fi
mkdir -p "$OUT"; ckpt_init
write_manifest start
[[ "$STAGE" == ingest || "$STAGE" == all ]] && do_ingest
[[ "$STAGE" == answer  || "$STAGE" == all ]] && do_answer
write_manifest end
log "DONE (stage=$STAGE run=$RUN). Artifacts: $OUT  (summary.md, compare_authors.md, traces/)"
