#!/usr/bin/env bash
#
# run_lme.sh — reproduce Caura's published LongMemEval_S headline configuration
# on the fixed 60-question Mem0 subset. Run it yourself in tmux; it does NOT
# start on its own and refuses to clobber an existing run directory.
#
#   experiments/scripts/run_lme.sh [--stage ingest|answer|all]
#                                  [--run NAME] [--ids FILE] [--mem0-judge]
#
# Stages:
#   ingest  pre-checks + manifest + ingest (published flags) + pg_dump
#   answer  pre-checks + run (published flags) + judges + collect outputs/logs
#   all     ingest then answer (default)
#
# Server must already be on the published embedder (bge-m3 via the GPU TEI
# sidecar caura-tei-gpu) and Gemini enrichment (gemini-3.1-flash-lite). See
# experiments/SETUP_NOTES.md. Ingesting the 60 questions takes ~5h on the shared
# GPU slice, so this script sets NO timeouts that could kill it early.
set -euo pipefail

# ---- config (env-overridable) ------------------------------------------------
REPO="$(git -C "$(dirname "${BASH_SOURCE[0]}")" rev-parse --show-toplevel)"
HARNESS="$REPO/experiments/harness/longmemeval"
RUN="${RUN:-lme_caura_default_60q}"
IDS="${IDS:-$REPO/experiments/configs/lme_subset60_from_mem0.txt}"
STAGE="${STAGE:-all}"
AGENT_PREFIX="lmeo"
MEM0_JUDGE="${MEM0_JUDGE:-0}"           # 1 = also judge with Mem0's gpt-5 + prompt
CAURA_BASE="${CAURA_BASE:-http://localhost:8000/api/v1}"
API_KEY="${CAURA_API_KEY:-standalone}"
TENANT="${CAURA_TENANT_ID:-default}"
EXPECT_EMBED_DIM=1024

while [[ $# -gt 0 ]]; do
  case "$1" in
    --stage) STAGE="$2"; shift 2;;
    --run)   RUN="$2"; shift 2;;
    --ids)   IDS="$2"; shift 2;;
    --mem0-judge) MEM0_JUDGE=1; shift;;
    -h|--help) grep '^#' "$0" | sed 's/^# \{0,1\}//'; exit 0;;
    *) echo "unknown arg: $1" >&2; exit 2;;
  esac
done
case "$STAGE" in ingest|answer|all) ;; *) echo "bad --stage: $STAGE" >&2; exit 2;; esac

OUT="$REPO/experiments/runs/$RUN"
mkdir -p "$(dirname "$OUT")"
export PYTHONUNBUFFERED=1               # stream per-question progress to the logs

log() { echo "[$(date -Is)] $*"; }
die() { echo "[$(date -Is)] FATAL: $*" >&2; exit 1; }

curlc() { curl -s -o /dev/null -w '%{http_code}' "$@"; }

# ---- pre-checks --------------------------------------------------------------
prechecks() {
  log "pre-checks (stage=$STAGE)"

  # 1. Caura server healthy
  [[ "$(curlc "$CAURA_BASE/health" -H "X-API-Key: $API_KEY")" == 200 ]] \
    || die "Caura /health not 200"
  # every compose service healthy
  local unhealthy
  unhealthy="$(cd "$REPO" && docker compose ps --format '{{.Service}} {{.Health}}' 2>/dev/null \
                | awk '$2!="healthy"{print}' || true)"
  [[ -z "$unhealthy" ]] || die "unhealthy caura services:\n$unhealthy"

  # 2. search endpoint returns 200
  [[ "$(curlc -X POST "$CAURA_BASE/search" -H "X-API-Key: $API_KEY" \
        -H 'Content-Type: application/json' \
        -d "{\"tenant_id\":\"$TENANT\",\"query\":\"healthcheck\"}")" == 200 ]] \
    || die "search endpoint not 200"

  # 3. container OPENAI_API_KEY prefix matches .env
  local env_pref cont_pref
  env_pref="$(grep -E '^OPENAI_API_KEY=' "$REPO/.env" | head -1 | cut -d= -f2- | cut -c1-6)"
  cont_pref="$(docker exec caura-core-api-1 sh -c 'printenv OPENAI_API_KEY' | cut -c1-6)"
  [[ -n "$env_pref" && "$env_pref" == "$cont_pref" ]] \
    || die "core-api OPENAI key prefix ($cont_pref) != .env ($env_pref)"
  log "  OpenAI key prefix matches: $cont_pref…"

  # 4. GPU TEI embedder serves EXPECT_EMBED_DIM
  docker inspect caura-tei-gpu --format '{{.State.Health.Status}}' 2>/dev/null | grep -qx healthy \
    || die "caura-tei-gpu not healthy (the bge-m3 GPU sidecar)"
  local dim
  dim="$(docker exec caura-core-api-1 python - <<'PY'
import urllib.request,json
req=urllib.request.Request("http://tei-gpu:80/v1/embeddings",
  data=json.dumps({"model":"BAAI/bge-m3","input":"precheck"}).encode(),
  headers={"Content-Type":"application/json"})
print(len(json.load(urllib.request.urlopen(req,timeout=30))["data"][0]["embedding"]))
PY
)"
  [[ "$dim" == "$EXPECT_EMBED_DIM" ]] || die "TEI embedding dim=$dim, expected $EXPECT_EMBED_DIM"
  log "  TEI bge-m3 returns ${dim}-dim"

  # 5. Gemini enrichment model reachable (one tiny call)
  ( cd "$HARNESS" && uv run python - <<'PY'
import os
from google import genai
from google.genai import types
c=genai.Client(api_key=os.environ["GEMINI_API_KEY"])
r=c.models.generate_content(model="gemini-3.1-flash-lite",contents="ok",
    config=types.GenerateContentConfig(max_output_tokens=5,temperature=0.0))
print("gemini-3.1-flash-lite reachable")
PY
) || die "gemini-3.1-flash-lite not reachable (enrichment model)"
  log "  gemini-3.1-flash-lite reachable"

  # 6. IDS file: exactly 60, 10 per type
  ( cd "$HARNESS" && uv run python - "$IDS" <<'PY'
import sys
from longmemeval.dataset import LongMemEvalDataset, read_question_id_list
ids=read_question_id_list(sys.argv[1])
assert len(ids)==60, f"expected 60 ids, got {len(ids)}"
ds=LongMemEvalDataset(data_path="data/longmemeval_s_cleaned.json")
items=ds.load_items(include_ids=ids)
assert len(items)==60, f"only {len(items)}/60 ids found in dataset"
import collections
c=collections.Counter(i.question_type for i in items)
assert set(c.values())=={10} and len(c)==6, f"not 10/type: {dict(c)}"
print("  IDS ok: 60 questions, 10 per type")
PY
) || die "IDS check failed ($IDS)"

  # 7. memory store state for the agent prefix
  local agents
  agents="$(curl -s "$CAURA_BASE/memories/stats" -H "X-API-Key: $API_KEY" \
            | python3 -c "import json,sys;d=json.load(sys.stdin);print(sum(1 for a in d.get('by_agent',{}) if a.startswith('$AGENT_PREFIX-')))")"
  if [[ "$STAGE" == "answer" ]]; then
    [[ "$agents" == 60 ]] || die "answer stage needs 60 ingested '$AGENT_PREFIX-' agents, found $agents"
    log "  store has 60 '$AGENT_PREFIX-' agents (ingest complete)"
  else
    [[ "$agents" == 0 ]] || die "store not empty for '$AGENT_PREFIX-' ($agents agents); clean up first"
    log "  store empty for '$AGENT_PREFIX-'"
  fi
  log "pre-checks passed"
}

# ---- manifest ----------------------------------------------------------------
write_manifest() {  # $1 = phase (start|end)
  ( cd "$REPO" && STAGE="$STAGE" RUN="$RUN" IDS="$IDS" OUT="$OUT" HARNESS="$HARNESS" \
       CAURA_BASE="$CAURA_BASE" API_KEY="$API_KEY" PHASE="$1" python3 - <<'PY'
import json,os,subprocess,urllib.request,datetime
REPO=os.getcwd(); OUT=os.environ["OUT"]; HARNESS=os.environ["HARNESS"]
mp=os.path.join(OUT,"manifest.json")
m=json.load(open(mp)) if os.path.exists(mp) else {}
def sh(*a):
    try: return subprocess.check_output(a,cwd=REPO,text=True).strip()
    except Exception: return None
def env(path,key):
    try:
        for ln in open(path):
            if ln.startswith(key+"="): return ln.split("=",1)[1].strip()
    except Exception: pass
    return None
def redact(v): return (v[:6]+"…") if v else None
if os.environ["PHASE"]=="start":
    srv=None
    try:
        srv=json.load(urllib.request.urlopen(os.environ["CAURA_BASE"]+"/version",timeout=10)).get("version")
    except Exception: pass
    m.update({
      "run_name": os.environ["RUN"],
      "benchmark": "longmemeval_s_cleaned (60-question Mem0 subset)",
      "goal": "reproduce Caura published headline (caura-500-opaque-compact) on the 60 Q",
      "stage": os.environ["STAGE"],
      "ids_file": os.environ["IDS"],
      "started_utc": datetime.datetime.utcnow().isoformat()+"Z",
      "caura_repo_commit": sh("git","rev-parse","HEAD"),
      "harness_subtree_ref": "3b1e293a (upstream caura-ai/caura-longmemeval)",
      "caura_server_version": srv,
      "caura_core_api_image": sh("docker","inspect","caura-core-api-1","--format","{{.Config.Image}}"),
      "caura_core_api_revision": sh("docker","inspect","caura-core-api-1","--format",
                                    '{{index .Config.Labels "org.opencontainers.image.revision"}}'),
      "models": {
        "reader":          env(HARNESS+"/.env","READER_MODEL"),
        "reader_provider": env(HARNESS+"/.env","READER_LLM"),
        "judge":           env(HARNESS+"/.env","JUDGE_MODEL"),
        "secondary_judge": env(HARNESS+"/.env","SECONDARY_JUDGE_MODEL"),
        "server_embedding_model": env(REPO+"/.env","OPENAI_EMBEDDING_MODEL"),
        "server_embedding_base_url": env(REPO+"/.env","OPENAI_EMBEDDING_BASE_URL"),
        "server_embedding_send_dimensions": env(REPO+"/.env","OPENAI_EMBEDDING_SEND_DIMENSIONS"),
        "vector_dim": env(REPO+"/.env","VECTOR_DIM"),
        "enrichment_provider": env(REPO+"/.env","ENTITY_EXTRACTION_PROVIDER"),
        "enrichment_model": env(REPO+"/.env","ENTITY_EXTRACTION_MODEL"),
        "top_k": 150,
      },
      "keys_redacted": {
        "OPENAI_API_KEY": redact(env(REPO+"/.env","OPENAI_API_KEY")),
        "GEMINI_API_KEY": redact(env(REPO+"/.env","GEMINI_API_KEY")),
      },
      "source": "REPRODUCE.md (headline run caura-500-opaque-compact)",
      "published_flags": {
        "ingest": "--chunk-mode turns --agent-prefix lmeo --bulk-size 100 --concurrency 5",
        "run": "--skip-ingest --chunk-mode turns --agent-prefix lmeo --sibling-expansion "
               "--sibling-window 0 --context-budget 150000 --raw-turns-only --no-as-of-recall "
               "--context-format compact --pipeline agentic-v1 --reader gemini "
               "--reader-model gemini-3.8-flash --judge openai --judge-model gpt-4o "
               "--secondary-judge gemini --secondary-judge-model gemini-3.5-flash-lite --concurrency 5",
        "plus": "--question-ids <ids_file> (our harness addition)",
      },
      "embedder_serving": ("bge-m3 via HuggingFace TEI (image "
        "ghcr.io/huggingface/text-embeddings-inference:hopper-1.8) on a SHARED H100 MIG slice "
        "next to Mem0's qwen-tei embedder; separate compose project 'caura-tei-gpu', "
        "host port 127.0.0.1:8100, reached by core-api as http://tei-gpu:80/v1"),
      "deviations_from_published": [
        "Embedder served on a GPU MIG slice SHARED with Mem0's running qwen-tei (published run used a dedicated GPU TEI). Same model (bge-m3, 1024d); only the serving host is shared.",
        "Enrichment via the Gemini API key (gemini-3.1-flash-lite) rather than the published Vertex endpoint; same model.",
        "A second config (OpenAI text-embedding-3-small + gpt-5.4-nano enrichment) is planned later as 'variant 2'; this run is variant 1 (authors' published settings).",
      ],
    })
else:
    m["ended_utc"]=datetime.datetime.utcnow().isoformat()+"Z"
os.makedirs(OUT,exist_ok=True)
json.dump(m,open(mp,"w"),indent=2)
print("manifest",os.environ["PHASE"],"->",mp)
PY
)
}

# ---- stages ------------------------------------------------------------------
do_ingest() {
  log "=== INGEST (published flags) -> $OUT/ingest.log ==="
  local t0=$SECONDS
  ( cd "$HARNESS" && uv run longmemeval ingest \
      --name "$RUN" --provider caura \
      --chunk-mode turns --agent-prefix "$AGENT_PREFIX" \
      --bulk-size 100 --concurrency 5 \
      --question-ids "$IDS" ) 2>&1 | tee "$OUT/ingest.log"
  log "ingest wall-clock: $((SECONDS - t0))s"
  # per-question tally (id, memories written) appended so ingest.log shows it moved
  { echo "---- per-agent memory counts after ingest (id | memories) ----"
    curl -s "$CAURA_BASE/memories/stats" -H "X-API-Key: $API_KEY" \
      | python3 -c "import json,sys;d=json.load(sys.stdin);[print(f'{a} | {n}') for a,n in sorted(d.get('by_agent',{}).items()) if a.startswith('$AGENT_PREFIX-')]"
  } | tee -a "$OUT/ingest.log"

  log "=== memory dump -> $OUT/memory_dump.sql.gz ==="
  docker exec caura-db-1 pg_dump -U caura -d caura | gzip > "$OUT/memory_dump.sql.gz"
  log "memory dump: $(du -h "$OUT/memory_dump.sql.gz" | cut -f1)"
}

do_answer() {
  log "=== RUN (published flags) -> $OUT/run.log ==="
  local attempt=1 max=5 backoff=30
  while :; do
    set +e
    ( cd "$HARNESS" && uv run longmemeval run \
        --name "$RUN" --provider caura --skip-ingest \
        --chunk-mode turns --agent-prefix "$AGENT_PREFIX" \
        --sibling-expansion --sibling-window 0 --context-budget 150000 \
        --raw-turns-only --no-as-of-recall --context-format compact \
        --pipeline agentic-v1 \
        --reader gemini --reader-model gemini-3.8-flash \
        --judge openai --judge-model gpt-4o \
        --secondary-judge gemini --secondary-judge-model gemini-3.5-flash-lite \
        --concurrency 5 \
        --question-ids "$IDS" ) 2>&1 | tee -a "$OUT/run.log"
    local rc=${PIPESTATUS[0]}
    set -e
    [[ $rc -eq 0 ]] && break
    # Gemini 429 → backoff and retry; anything else → fail.
    if grep -qiE '429|RESOURCE_EXHAUSTED|rate.?limit' "$OUT/run.log" && [[ $attempt -lt $max ]]; then
      log "run hit Gemini 429 (attempt $attempt/$max); sleeping ${backoff}s then retrying" | tee -a "$OUT/run.log"
      sleep "$backoff"; attempt=$((attempt+1)); backoff=$((backoff*2)); continue
    fi
    die "run failed (rc=$rc); see $OUT/run.log"
  done

  log "=== JUDGE: evaluate-hypotheses -> $OUT/eval.log ==="
  ( cd "$HARNESS" && uv run longmemeval evaluate-hypotheses \
      "outputs/$RUN/hypotheses.jsonl" \
      --judge openai --judge-model gpt-4o \
      --secondary-judge gemini --secondary-judge-model gemini-3.5-flash-lite \
      --no-html ) 2>&1 | tee "$OUT/eval.log"

  # Mem0's judge differs from gpt-4o (gpt-5 + custom prompt). Optional parity judge.
  if [[ "$MEM0_JUDGE" == 1 ]]; then
    log "=== JUDGE (Mem0 parity: gpt-5 + Mem0 prompt) -> $OUT/eval_mem0judge.json ==="
    ( cd "$HARNESS" && uv run python "$REPO/experiments/scripts/mem0_judge.py" \
        "outputs/$RUN/hypotheses.jsonl" -o "$OUT/eval_mem0judge.json" --model gpt-5 ) \
        2>&1 | tee -a "$OUT/eval.log" || log "mem0 parity judge failed (non-fatal)"
  fi

  log "=== collect harness outputs + server logs ==="
  mkdir -p "$OUT/outputs"
  cp -a "$HARNESS/outputs/$RUN/." "$OUT/outputs/" 2>/dev/null || true
  local since
  since="$(python3 -c "import json;print(json.load(open('$OUT/manifest.json'))['started_utc'])" 2>/dev/null || echo 6h)"
  ( cd "$REPO" && docker compose logs --since "$since" > "$OUT/caura_logs.txt" 2>&1 ) || true
  log "outputs -> $OUT/outputs ; server logs -> $OUT/caura_logs.txt"
}

# ---- main --------------------------------------------------------------------
if [[ "$STAGE" == "ingest" || "$STAGE" == "all" ]]; then
  [[ -e "$OUT" ]] && die "run dir already exists: $OUT (refusing to overwrite)"
  mkdir -p "$OUT"
fi
[[ "$STAGE" == "answer" && ! -d "$OUT" ]] && die "answer stage: $OUT does not exist (run --stage ingest first)"

prechecks
write_manifest start
[[ "$STAGE" == "ingest" || "$STAGE" == "all" ]] && do_ingest
[[ "$STAGE" == "answer" || "$STAGE" == "all" ]] && do_answer
write_manifest end
log "DONE (stage=$STAGE, run=$RUN). Artifacts in $OUT"
