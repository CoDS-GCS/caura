#!/usr/bin/env bash
#
# run_locomo.sh — run Caura (MemClaw) on LoCoMo conversation 0 (conv-26) as close
# to the authors' published LoCoMo run as the public code allows, with every
# Gemini call through Vertex AI (Google Cloud credits). Run it yourself in tmux;
# it never starts on its own.
#
#   experiments/scripts/run_locomo.sh --check                 # free: no paid calls
#   experiments/scripts/run_locomo.sh --stage ingest|answer|all
#   experiments/scripts/run_locomo.sh --resume                # continue after a pause/crash
#
# Published params (EVIDENCE.md locomo_caura_retrieval_accuracy_2026_09):
#   chars-4000 session memories, top_k 30, thinking_tokens 2048 (reader only),
#   judge_votes 3 (gemini-3.8-flash), agentic-v1 pipeline. min_similarity 0.0 and
#   write_mode=strong and one-agent-per-conversation from caura-locomo. Second
#   judge gpt-4o (ours). Reader + both Gemini calls via Vertex; entity extraction
#   via AI Studio (server has no Vertex option there).
#
# Credit pause/resume: on a Vertex RESOURCE_EXHAUSTED/429 or OpenAI
# insufficient_quota, it pauses, probes the model every 15 min for up to 12 h,
# and resumes automatically; the Python stages resume from disk.
set -uo pipefail

REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
HARNESS="$REPO/experiments/harness/locomo"
RUN="${RUN:-locomo_caura_conv0}"
STAGE="${STAGE:-all}"
RESUME=0; CHECK=0
OUT="$REPO/experiments/runs/$RUN"

# ---- config (exported ONLY here; never put LOCOMO_VERTEX in a shared .env) ----
export LOCOMO_VERTEX=1
export GOOGLE_CLOUD_PROJECT="${GOOGLE_CLOUD_PROJECT:-project-6daf51c7-b33c-415b-954}"
export GOOGLE_CLOUD_LOCATION="${GOOGLE_CLOUD_LOCATION:-us-central1}"
export LOCOMO_DATA_PATH="${LOCOMO_DATA_PATH:-$HARNESS/data/locomo10.json}"
export LOCOMO_READER_MODEL="${LOCOMO_READER_MODEL:-gemini-3.8-flash}"
export LOCOMO_JUDGE_MODEL="${LOCOMO_JUDGE_MODEL:-gemini-3.8-flash}"
export LOCOMO_SECOND_JUDGE_MODEL="${LOCOMO_SECOND_JUDGE_MODEL:-gpt-4o}"
export LOCOMO_MEMORY_MODEL="${LOCOMO_MEMORY_MODEL:-gemini-3.1-flash-lite}"   # stable name (REPRODUCE.md)
export LOCOMO_THINKING_BUDGET="${LOCOMO_THINKING_BUDGET:-2048}"
export LOCOMO_CHUNK_CHARS="${LOCOMO_CHUNK_CHARS:-4000}"
export LOCOMO_TOP_K="${LOCOMO_TOP_K:-30}"
export LOCOMO_MIN_SIMILARITY="${LOCOMO_MIN_SIMILARITY:-0.0}"
export LOCOMO_CONV_INDEX="${LOCOMO_CONV_INDEX:-0}"
export PYTHONUNBUFFERED=1

CAURA_BASE="${CAURA_BASE:-http://localhost:8000/api/v1}"
API_KEY="${CAURA_API_KEY:-standalone}"
AGENT="locomo-conv-${LOCOMO_CONV_INDEX}"
UPSTREAM_SHA="79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4"
POLL_SECS="${POLL_SECS:-900}"; MAX_POLLS="${MAX_POLLS:-48}"
CONCURRENCY="${CONCURRENCY:-8}"
TS="$(date +%Y%m%d_%H%M%S)"
CONSOLE_LOG="/tmp/${RUN}_${TS}.log"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2;;
    --run) RUN="$2"; OUT="$REPO/experiments/runs/$RUN"; shift 2;;
    --resume) RESUME=1; shift;;
    --check) CHECK=1; shift;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done

# Everything the run prints also lands in a /tmp console log.
exec > >(tee -a "$CONSOLE_LOG") 2>&1

log(){ echo "[$(date -Is)] $*"; }
die(){ echo "[$(date -Is)] FATAL: $*" >&2; exit 1; }
psqlc(){ docker exec caura-db-1 psql -U caura -d caura -tA "$@"; }
cenv(){ docker exec caura-core-api-1 printenv "$1" 2>/dev/null || true; }
locomo(){ ( cd "$HARNESS" && uv run python -m longmemeval.locomo.cli "$@" ); }

# ---------------------------------------------------------------- credit pause/resume
probe_vertex(){ # model -> rc0 if it answers a tiny Vertex call
  ( cd "$HARNESS" && uv run python - "$1" <<'PY'
import os,sys
from longmemeval.llm import GeminiLLM
try:
    GeminiLLM(model_name=sys.argv[1]).generate("ok", max_tokens=1, temperature=0.0)
    sys.exit(0)
except Exception as e:
    s=str(e)
    sys.exit(1 if ("RESOURCE_EXHAUSTED" in s or "429" in s or "quota" in s.lower()) else 0)
PY
  ) >/dev/null 2>&1
}
probe_openai(){ # model -> rc0 if it answers
  local code; code=$(curl -s -o /dev/null -w '%{http_code}' https://api.openai.com/v1/chat/completions \
    -H "Authorization: Bearer $(grep -E '^OPENAI_API_KEY=' "$REPO/.env"|head -1|cut -d= -f2-)" \
    -H 'Content-Type: application/json' \
    -d "{\"model\":\"$1\",\"messages\":[{\"role\":\"user\",\"content\":\"ok\"}],\"max_completion_tokens\":1}")
  [[ "$code" == 200 ]]
}
wait_for_credit(){ # kind model phase -> rc0 restored, rc3 timed out
  local kind="$1" model="$2" phase="$3" i=0
  echo "$kind/$model exhausted during $phase" > "$OUT/PAUSED"
  log "PAUSED: $kind/$model exhausted during $phase — probing every $((POLL_SECS/60))min up to $((MAX_POLLS*POLL_SECS/3600))h"
  while (( i < MAX_POLLS )); do
    sleep "$POLL_SECS"; i=$((i+1))
    if { [[ "$kind" == vertex ]] && probe_vertex "$model"; } || { [[ "$kind" == openai ]] && probe_openai "$model"; }; then
      rm -f "$OUT/PAUSED"; log "RESUMED: $kind/$model back after ~$((i*POLL_SECS/60))min"; return 0
    fi
    log "  still exhausted ($kind/$model), poll $i/$MAX_POLLS"
  done
  log "credit not restored within 12h — exiting cleanly; rerun with: run_locomo.sh --resume"; return 3
}
run_resumable(){ # phase logfile -- cmd...   (Python stages resume from disk)
  local phase="$1" lf="$2"; shift 2; [[ "$1" == "--" ]] && shift
  while :; do
    ( "$@" ) 2>&1 | tee -a "$lf"; local rc=${PIPESTATUS[0]}
    [[ $rc -eq 0 ]] && return 0
    if grep -qiE 'RESOURCE_EXHAUSTED|429 .*quota|quota exceeded' "$lf"; then
      wait_for_credit vertex "$LOCOMO_READER_MODEL" "$phase"; case $? in 0) continue;; 3) exit 0;; esac
    elif grep -qiE 'insufficient_quota|exceeded your current quota' "$lf"; then
      wait_for_credit openai "$LOCOMO_SECOND_JUDGE_MODEL" "$phase"; case $? in 0) continue;; 3) exit 0;; esac
    fi
    die "$phase failed (rc=$rc), not a credit error — see $lf"
  done
}

# ---------------------------------------------------------------- pre-checks (FREE)
prechecks(){
  log "pre-checks (free; no paid model calls) — console log: $CONSOLE_LOG"
  [[ "$(curl -s -o /dev/null -w '%{http_code}' "$CAURA_BASE/health" -H "X-API-Key: $API_KEY")" == 200 ]] || die "Caura /health not 200"
  local unhealthy; unhealthy="$(cd "$REPO" && docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null | awk '$2!="healthy"{print}')"
  [[ -z "$unhealthy" ]] || die "unhealthy caura services: $unhealthy"
  docker inspect caura-tei-gpu --format '{{.State.Health.Status}}' 2>/dev/null | grep -qx healthy || die "caura-tei-gpu (bge-m3) not healthy"
  local tei_port="${TEI_GPU_PORT:-8100}" dim
  dim="$(curl -s -X POST "http://127.0.0.1:${tei_port}/embed" -H 'Content-Type: application/json' -d '{"inputs":"precheck"}' \
        | python3 -c "import json,sys
try:
 v=json.load(sys.stdin); print(len(v[0]) if isinstance(v,list) and v and isinstance(v[0],list) else 0)
except Exception: print(0)")"
  [[ "$dim" == 1024 ]] || die "TEI dim='$dim' != 1024"

  # --- memory creation must be Vertex, never OpenAI (the OpenAI-fallback guard) ---
  local prov proj mcm ee gac
  prov="$(cenv PLATFORM_LLM_PROVIDER)"; proj="$(cenv PLATFORM_LLM_GCP_PROJECT_ID)"
  mcm="$(cenv PLATFORM_LLM_MODEL)"; ee="$(cenv ENTITY_EXTRACTION_PROVIDER)"; gac="$(cenv GOOGLE_APPLICATION_CREDENTIALS)"
  [[ "$prov" == vertex ]] || die "memory creation not on Vertex: PLATFORM_LLM_PROVIDER='$prov' (set it to vertex, or the server could use OpenAI for memory creation) — see CREDENTIALS.md"
  [[ -n "$proj" ]] || die "PLATFORM_LLM_GCP_PROJECT_ID unset in core-api"
  [[ "$mcm" == gemini-3.1-flash-lite* ]] || die "PLATFORM_LLM_MODEL='$mcm' (expected gemini-3.1-flash-lite, stable)"
  [[ "$ee" == gemini ]] || die "ENTITY_EXTRACTION_PROVIDER='$ee' (expected gemini / AI Studio)"
  [[ -n "$gac" ]] || die "core-api GOOGLE_APPLICATION_CREDENTIALS unset (mount the SA key — CREDENTIALS.md)"
  docker exec caura-core-api-1 test -f "$gac" || die "core-api SA key not found at $gac (not mounted)"
  # confirm the platform LLM actually initialised as Vertex (not silently fell back)
  if (cd "$REPO" && docker compose logs core-api --since 72h 2>&1) | grep -qiE 'vertex-llm-config|Failed to initialize platform Vertex'; then
    die "core-api logged a Vertex platform-LLM init error — memory creation would fall back. Fix creds and restart core-api."
  fi
  (cd "$REPO" && docker compose logs core-api --since 72h 2>&1) | grep -qi 'Platform LLM: vertex' \
    || log "WARN: did not see 'Platform LLM: vertex' in recent core-api logs (restart core-api after setting PLATFORM_LLM_*)"

  # harness ADC present (for reader/judge Vertex)
  if [[ -n "${GOOGLE_APPLICATION_CREDENTIALS:-}" ]]; then
    [[ -f "$GOOGLE_APPLICATION_CREDENTIALS" ]] || die "GOOGLE_APPLICATION_CREDENTIALS set but file missing: $GOOGLE_APPLICATION_CREDENTIALS"
  else
    gcloud auth application-default print-access-token >/dev/null 2>&1 \
      || die "no harness ADC: run 'gcloud auth application-default login' or set GOOGLE_APPLICATION_CREDENTIALS (CREDENTIALS.md)"
  fi

  # dataset: digest + counts (free)
  [[ -f "$LOCOMO_DATA_PATH" ]] || die "dataset missing: $LOCOMO_DATA_PATH"
  local sha; sha="$(sha256sum "$LOCOMO_DATA_PATH" | cut -d' ' -f1)"
  [[ "$sha" == "$UPSTREAM_SHA" ]] || die "locomo10.json sha256 mismatch: $sha != $UPSTREAM_SHA"
  locomo stats | python3 -c "import json,sys
d=json.load(sys.stdin); assert d['scored_questions']==152, d; assert d['sessions']==19 and d['turns']==419, d
assert d['by_category']=={'multi-hop':32,'temporal':37,'open-domain':13,'single-hop':70}, d['by_category']
print('dataset ok:', d['sample_id'], d['scored_questions'],'scored Q')" || die "dataset counts check failed"

  # Vertex serves the stable gemini-3.1-flash-lite name in us-central1 (free list; authoritative
  # check is the tiny probe at ingest start, which fails loudly before -preview).
  ( cd "$HARNESS" && uv run python - <<'PY'
import os
from google import genai
try:
    c=genai.Client(vertexai=True, project=os.environ["GOOGLE_CLOUD_PROJECT"], location=os.environ["GOOGLE_CLOUD_LOCATION"])
    names=[m.name for m in c.models.list()]
    hit=[n for n in names if "gemini-3.1-flash-lite" in n]
    print(f"vertex models.list: {len(names)} models; gemini-3.1-flash-lite match: {hit[:2] or 'none in list (publisher models may not list; probe at ingest is authoritative)'}")
except Exception as e:
    print(f"vertex models.list not conclusive ({str(e)[:140]}); the tiny probe at ingest start is authoritative")
PY
  ) || true

  # store emptiness vs presence for the stage
  local n; n="$(curl -s "$CAURA_BASE/memories/stats" -H "X-API-Key: $API_KEY" \
      | python3 -c "import json,sys;print(json.load(sys.stdin).get('by_agent',{}).get('$AGENT',0))" 2>/dev/null || echo 0)"
  if [[ "$STAGE" == answer && "$RESUME" == 0 ]]; then
    [[ "$n" -gt 0 ]] || die "answer stage needs an ingested store for $AGENT, found $n memories"
  elif [[ "$STAGE" != answer && "$RESUME" == 0 ]]; then
    [[ "$n" == 0 ]] || die "store not empty for $AGENT ($n memories); clean up first (ingest resets it)"
  fi
  log "pre-checks passed (TEI 1024-d, memory-creation=vertex, entity=gemini, dataset sha ok, 152 scored Q)"
}

# ---------------------------------------------------------------- settle + backfill
backfill(){ docker exec caura-core-storage-api-1 python -m core_storage_api.scripts.backfill_embeddings 2>&1 | tee -a "$OUT/ingest.log" || true; }
settle_gate(){
  local tries=0
  while :; do
    set +e; locomo settle --out "$OUT" 2>&1 | tee -a "$OUT/ingest.log"; local rc=${PIPESTATUS[0]}; set -e 2>/dev/null || true
    case $rc in
      0) log "store settled, stranded.embedding=0"; return 0;;
      2) tries=$((tries+1)); (( tries>3 )) && die "stranded embeddings persist after $tries backfills"
         log "stranded embeddings — running backfill (attempt $tries)"; backfill;;
      *) die "settle failed (rc=$rc) — see $OUT/ingest.log";;
    esac
  done
}

# ---------------------------------------------------------------- zero-retrieval guard
assert_retrieval_nonempty(){
  local hyp="$OUT/hypotheses.jsonl"; [[ -f "$hyp" ]] || return 0
  local bad; bad="$(python3 - "$hyp" <<'PY'
import json,sys
bad=[json.loads(l).get("question_id") for l in open(sys.argv[1]) if l.strip() and not (json.loads(l).get("retrieval_stats") or {}).get("search_candidates")]
print(" ".join(str(b) for b in bad))
PY
)"
  [[ -z "$bad" ]] || die "retrieval returned 0 candidates for: $bad — refusing to judge an invalid run (fix recall, re-run answer; store is intact)."
}

# ---------------------------------------------------------------- manifest
write_manifest_start(){
  local srv; srv="$(curl -s "$CAURA_BASE/version" -H "X-API-Key: $API_KEY" 2>/dev/null || true)"
  python3 - "$OUT/manifest.json" "$RUN" "$AGENT" "$GOOGLE_CLOUD_PROJECT" "$GOOGLE_CLOUD_LOCATION" "$srv" "$(git -C "$REPO" rev-parse HEAD)" <<'PY'
import json,sys,os,datetime
p,run,agent,proj,loc,srv,commit=sys.argv[1:8]
d=json.load(open(p)) if os.path.exists(p) else {}
d.setdefault("run",run); d["agent_id"]=agent
d["started_utc"]=d.get("started_utc") or datetime.datetime.now(datetime.timezone.utc).isoformat()
d["repo_commit"]=commit
try: d["caura_server_version"]=json.loads(srv)
except Exception: d["caura_server_version"]=srv or None
d["config"]={"pipeline":"agentic-v1","chunk_characters":int(os.environ.get("LOCOMO_CHUNK_CHARS",4000)),
  "top_k":int(os.environ.get("LOCOMO_TOP_K",30)),"judge_votes":3,
  "thinking_tokens":{"value":int(os.environ.get("LOCOMO_THINKING_BUDGET",2048)),"applied_to":"reader","scope":"applied to reader; scope not published"},
  "min_similarity":{"value":float(os.environ.get("LOCOMO_MIN_SIMILARITY",0.0)),"source":"caura-locomo provider (tenant default 0.3 discards LoCoMo paraphrases)"},
  "write_mode":"strong","reader_model":os.environ.get("LOCOMO_READER_MODEL"),
  "judge_model":os.environ.get("LOCOMO_JUDGE_MODEL"),"second_judge_model":os.environ.get("LOCOMO_SECOND_JUDGE_MODEL"),
  "memory_creation_model":os.environ.get("LOCOMO_MEMORY_MODEL"),"entity_extraction":"gemini-3.1-flash-lite (AI Studio; server has no Vertex option)",
  "embedder":"BAAI/bge-m3 local GPU TEI (1024-d)","reader_temperature":"0.0 (not published)","judge_temperature":"0.0 (not published)",
  "store_name_note":"store configuration not published; we build our own store from conv0 with the published parameters",
  "dataset_sha256":"79fa87e90f04081343b8c8debecb80a9a6842b76a7aa537dc9fdf651ea698ff4","conv":"conv-26 (index 0)","scored_questions":152,"categories":"1-4 (cat5 excluded)"}
json.dump(d,open(p,"w"),indent=2,ensure_ascii=False)
print("manifest start ->",p)
PY
}
write_manifest_end(){
  python3 - "$OUT/manifest.json" <<'PY'
import json,sys,datetime
p=sys.argv[1]; d=json.load(open(p)); d["finished_utc"]=datetime.datetime.now(datetime.timezone.utc).isoformat()
json.dump(d,open(p,"w"),indent=2,ensure_ascii=False)
PY
}

# ---------------------------------------------------------------- stages
do_ingest(){
  log "=== Vertex model probe (tiny calls; records versions to manifest) ==="
  run_resumable probe "$OUT/ingest.log" -- locomo probe-vertex --out "$OUT"
  log "=== INGEST (chars-4000 session memories, write_mode=strong) -> $OUT/ingest.log ==="
  run_resumable ingest "$OUT/ingest.log" -- locomo ingest --out "$OUT"
  log "=== SETTLE gate (poll settled, +60s, stranded.embedding) ==="
  settle_gate
}
do_answer(){
  log "=== frozen snapshot BEFORE answering ==="
  locomo frozen-snapshot --out "$OUT" --phase before 2>&1 | tee -a "$OUT/run.log"
  log "=== ANSWER (search top_k=30 + agentic-v1 reader) -> $OUT/run.log ==="
  CONC_ARGS=(--concurrency "$CONCURRENCY")
  run_resumable answer "$OUT/run.log" -- locomo answer --out "$OUT" "${CONC_ARGS[@]}"
  assert_retrieval_nonempty
  log "=== frozen snapshot AFTER answering (must match before) ==="
  set +e; locomo frozen-snapshot --out "$OUT" --phase after 2>&1 | tee -a "$OUT/run.log"; local fz=${PIPESTATUS[0]}; set -e 2>/dev/null || true
  [[ $fz -eq 0 ]] || die "store changed during answering (see frozen_before/after.json) — stopping."
  log "=== JUDGE (gemini-3.8-flash 3-vote + gpt-4o) -> $OUT/eval.log ==="
  run_resumable judge "$OUT/eval.log" -- locomo judge --out "$OUT"
  log "=== SUMMARY (summary.md + compare_authors.md) ==="
  locomo summary --out "$OUT" --prices "$REPO/experiments/configs/prices.json" 2>&1 | tee -a "$OUT/eval.log"
  ( cd "$REPO" && docker compose logs --since "$(python3 -c "import json;print(json.load(open('$OUT/manifest.json')).get('started_utc','24h'))" 2>/dev/null || echo 24h)" > "$OUT/caura_logs.txt" 2>&1 ) || true
}

# ---------------------------------------------------------------- main
if [[ "$RESUME" == 1 ]]; then
  [[ -d "$OUT" ]] || die "--resume: $OUT does not exist"
  [[ -f "$OUT/manifest.json" ]] && grep -q '"memories_created"' "$OUT/manifest.json" && STAGE=answer || STAGE=all
  log "RESUMING $RUN (stage=$STAGE)"
elif [[ "$CHECK" == 0 ]]; then
  if [[ "$STAGE" == ingest || "$STAGE" == all ]]; then
    [[ -e "$OUT" ]] && die "run dir exists: $OUT (use --resume to continue)"
  fi
  [[ "$STAGE" == answer && ! -d "$OUT" ]] && die "answer stage: $OUT missing (run --stage ingest first)"
fi

prechecks
if [[ "$CHECK" == 1 ]]; then
  log "pre-checks passed (--check: no run dir created, no paid calls)"; exit 0
fi
mkdir -p "$OUT"
write_manifest_start
[[ "$STAGE" == ingest || "$STAGE" == all ]] && do_ingest
[[ "$STAGE" == answer || "$STAGE" == all ]] && do_answer
write_manifest_end
log "DONE (stage=$STAGE run=$RUN). Artifacts: $OUT (summary.md, compare_authors.md, traces/, manifest.json). Console log: $CONSOLE_LOG"
