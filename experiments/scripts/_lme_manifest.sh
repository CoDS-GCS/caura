#!/usr/bin/env bash
# _lme_manifest.sh PHASE RUN IDS OUT HARNESS REPO CAURA_BASE
# Writes/updates $OUT/manifest.json. Keys redacted. Called by run_lme.sh.
set -uo pipefail
PHASE="$1"; RUN="$2"; IDS="$3"; OUT="$4"; HARNESS="$5"; REPO="$6"; CAURA_BASE="$7"
PHASE="$PHASE" RUN="$RUN" IDS="$IDS" OUT="$OUT" HARNESS="$HARNESS" REPO="$REPO" CAURA_BASE="$CAURA_BASE" \
python3 - <<'PY'
import json,os,subprocess,urllib.request,datetime
REPO=os.environ["REPO"]; OUT=os.environ["OUT"]; HARNESS=os.environ["HARNESS"]
mp=os.path.join(OUT,"manifest.json"); m=json.load(open(mp)) if os.path.exists(mp) else {}
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
if os.environ["PHASE"]=="start" and not m.get("started_utc"):
    srv=None
    try: srv=json.load(urllib.request.urlopen(os.environ["CAURA_BASE"]+"/version",timeout=10)).get("version")
    except Exception: pass
    m.update({
      "run_name":os.environ["RUN"], "ids_file":os.environ["IDS"],
      "benchmark":"longmemeval_s_cleaned (60-question Mem0 subset)",
      "goal":"reproduce Caura published headline (caura-500-opaque-compact) on the 60 Q",
      "started_utc":datetime.datetime.utcnow().isoformat()+"Z",
      "caura_repo_commit":sh("git","rev-parse","HEAD"),
      "harness_subtree_ref":"3b1e293a (upstream caura-ai/caura-longmemeval)",
      "caura_server_version":srv,
      "caura_core_api_image":sh("docker","inspect","caura-core-api-1","--format","{{.Config.Image}}"),
      "caura_core_api_revision":sh("docker","inspect","caura-core-api-1","--format",'{{index .Config.Labels "org.opencontainers.image.revision"}}'),
      "models":{
        "reader":env(HARNESS+"/.env","READER_MODEL"),
        "judge":env(HARNESS+"/.env","JUDGE_MODEL"),
        "secondary_judge":env(HARNESS+"/.env","SECONDARY_JUDGE_MODEL"),
        "server_embedding_model":env(REPO+"/.env","OPENAI_EMBEDDING_MODEL"),
        "server_embedding_base_url":env(REPO+"/.env","OPENAI_EMBEDDING_BASE_URL"),
        "vector_dim":env(REPO+"/.env","VECTOR_DIM"),
        "enrichment_provider":env(REPO+"/.env","ENTITY_EXTRACTION_PROVIDER"),
        "enrichment_model":env(REPO+"/.env","ENTITY_EXTRACTION_MODEL"),
        "top_k":150,
      },
      "keys_redacted":{"OPENAI_API_KEY":redact(env(REPO+"/.env","OPENAI_API_KEY")),
                       "GEMINI_API_KEY":redact(env(REPO+"/.env","GEMINI_API_KEY"))},
      "source":"REPRODUCE.md (headline run caura-500-opaque-compact)",
      "published_flags":{
        "ingest":"--chunk-mode turns --agent-prefix lmeo --bulk-size 100 --concurrency 5 --question-ids <ids>",
        "run":"--skip-ingest --chunk-mode turns --agent-prefix lmeo --sibling-expansion --sibling-window 0 "
              "--context-budget 150000 --raw-turns-only --no-as-of-recall --context-format compact "
              "--pipeline agentic-v1 --reader gemini --reader-model gemini-3.8-flash --judge openai "
              "--judge-model gpt-4o --secondary-judge gemini --secondary-judge-model gemini-3.5-flash-lite "
              "--concurrency 5 --question-ids <ids>",
      },
      "embedder_serving":"bge-m3 via HF TEI image text-embeddings-inference:hopper-1.8 on a SHARED H100 MIG "
                         "slice next to Mem0's qwen-tei; compose project caura-tei-gpu, host port 127.0.0.1:8100, "
                         "reached by core-api as http://tei-gpu:80/v1",
      "enrichment_tokens_method":"derived from measured rate (2943 in / 142 out per memory); server image is "
                                 "baked (no per-call token logging available without a rebuild)",
      "server_tuning":{
        "rate_limit_write_bulk":env(REPO+"/.env","RATE_LIMIT_WRITE_BULK"),
        "rate_limit_write_bulk_tenant":env(REPO+"/.env","RATE_LIMIT_WRITE_BULK_TENANT"),
        "note":"raised from the 2/second default (core_api rate_limit_write_bulk) so 5 concurrent "
               "/memories/bulk writes pass at ingest concurrency 5; admission throttling only — "
               "no effect on stored memories or results",
      },
      "deviations_from_published":[
        "Embedder served on a GPU MIG slice SHARED with Mem0's running qwen-tei (published: dedicated GPU TEI). Same model/vectors.",
        "Enrichment via the Gemini API key (gemini-3.1-flash-lite) not the published Vertex endpoint; same model.",
        "60-question subset (not the full 500).",
      ],
    })
elif os.environ["PHASE"]=="end":
    m["ended_utc"]=datetime.datetime.utcnow().isoformat()+"Z"
os.makedirs(OUT,exist_ok=True); json.dump(m,open(mp,"w"),indent=2)
print("manifest",os.environ["PHASE"],"->",mp)
PY
