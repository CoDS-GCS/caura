"""LoCoMo runner CLI (ours).

Subcommands mirror run_lme.sh's phases but for one LoCoMo conversation:
  stats           dataset stats (free)
  probe-vertex    one tiny Vertex call per Gemini model; records model/version/
                  region/generation-settings to manifest.json
  ingest          build the conversation store (one agent), chars-4000 sessions
  settle          poll /memories/stats until settled, +60s, check stranded.embedding
  frozen-snapshot count + latest updated_at for the store (before/after answering)
  answer          search + agentic-v1 reader -> hypotheses.jsonl (resumable)
  judge           gemini 3-vote + gpt-4o -> eval files + traces (resumable)
  summary         summary.md + compare_authors.md

Reuses the LongMemEval harness unchanged for the reader pipeline and LLMs.
"""

from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv

# Load the harness .env (same convention as longmemeval.cli). Vertex flag is
# NOT read from here — run_locomo.sh exports LOCOMO_VERTEX only for this process.
load_dotenv(dotenv_path=Path(__file__).resolve().parents[3] / ".env", override=False)

from longmemeval.llm import get_llm, GeminiLLM, OpenAILLM
from longmemeval.runner import run_reader_pipeline
from .dataset import load_conversation, stats as conv_stats, CATEGORY_NAMES, SCORED_CATEGORIES
from .provider import LocomoCauraProvider, CauraRecallError
from .judge import LocomoJudge

READER_MODEL = os.environ.get("LOCOMO_READER_MODEL", "gemini-3.8-flash")
JUDGE_MODEL = os.environ.get("LOCOMO_JUDGE_MODEL", "gemini-3.8-flash")
SECOND_JUDGE_MODEL = os.environ.get("LOCOMO_SECOND_JUDGE_MODEL", "gpt-4o")
# Memory creation + entity extraction model (stable name, per REPRODUCE.md).
# Probed on Vertex too, to confirm us-central1 serves the stable name the server
# will use for memory creation — before any paid ingest, and before -preview.
MEMORY_MODEL = os.environ.get("LOCOMO_MEMORY_MODEL", "gemini-3.1-flash-lite")
THINKING_BUDGET = int(os.environ.get("LOCOMO_THINKING_BUDGET", "2048"))
CONV_INDEX = int(os.environ.get("LOCOMO_CONV_INDEX", "0"))
UNIT = str(CONV_INDEX)  # agent == locomo-conv-<UNIT>
CHUNK_CHARS = int(os.environ.get("LOCOMO_CHUNK_CHARS", "4000"))
TOP_K = int(os.environ.get("LOCOMO_TOP_K", "30"))
MIN_SIM = float(os.environ.get("LOCOMO_MIN_SIMILARITY", "0.0"))


def _log(msg: str) -> None:
    print(f"[{datetime.now(timezone.utc).isoformat()}] {msg}", flush=True)


def _data_path(args) -> str:
    return args.data or os.environ["LOCOMO_DATA_PATH"]


def _load(args):
    return load_conversation(_data_path(args), conv_index=CONV_INDEX, chunk_chars=CHUNK_CHARS)


def _provider() -> LocomoCauraProvider:
    return LocomoCauraProvider(top_k=TOP_K, min_similarity=MIN_SIM, write_mode="strong")


def _manifest_path(out: Path) -> Path:
    return out / "manifest.json"


def manifest_merge(out: Path, patch: dict) -> None:
    p = _manifest_path(out)
    data = {}
    if p.exists():
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            data = {}
    def deep(d, u):
        for k, v in u.items():
            if isinstance(v, dict) and isinstance(d.get(k), dict):
                deep(d[k], v)
            else:
                d[k] = v
    deep(data, patch)
    p.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


# ----------------------------------------------------------------- stats
def cmd_stats(args):
    print(json.dumps(conv_stats(_load(args)), indent=2))


# ----------------------------------------------------------------- probe vertex
def cmd_probe_vertex(args):
    """One tiny Vertex call per Gemini model; record exact model/version/region/
    generation-settings to manifest.json. Exits nonzero if any model fails."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    # Probe the reader/judge model AND the memory-creation model (gemini-3.1-flash-lite)
    # so a Vertex rejection of the stable name surfaces here, before any paid ingest.
    models = sorted({READER_MODEL, JUDGE_MODEL, MEMORY_MODEL})
    rec: dict = {}
    ok = True
    for m in models:
        try:
            llm = GeminiLLM(model_name=m, thinking_budget=(THINKING_BUDGET if m == READER_MODEL else None))
            t0 = time.perf_counter()
            text = llm.generate("Reply with the single word: ok", max_tokens=16, temperature=0.0)
            dt = (time.perf_counter() - t0) * 1000
            rec[m] = {
                "requested_model": m,
                "resolved_model_version": getattr(llm, "resolved_model", None),
                "region": llm.location,
                "project": llm.project,
                "transport": "vertex" if llm.use_vertex else "ai_studio",
                "generation_settings": llm.generation_settings(),
                "reply_preview": (text or "")[:60],
                "latency_ms": round(dt, 1),
                "ok": True,
            }
            _log(f"probe {m}: ok ({llm.location}) version={getattr(llm,'resolved_model',None)} {dt:.0f}ms")
        except Exception as e:
            ok = False
            rec[m] = {"requested_model": m, "ok": False, "error": str(e)[:300]}
            _log(f"probe {m}: FAILED: {str(e)[:200]}")
    manifest_merge(out, {"vertex_probe": {"at": datetime.now(timezone.utc).isoformat(), "models": rec}})
    if not ok:
        sys.exit(1)


# ----------------------------------------------------------------- ingest
def cmd_ingest(args):
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    conv = _load(args)
    prov = _provider()
    try:
        if not args.no_reset:
            prov.reset(UNIT)
            _log(f"reset agent {prov.agent_id(UNIT)}")
        created = prov.ingest_chunks(UNIT, conv.chunks)
        _log(f"ingested {created} memories into {prov.agent_id(UNIT)} "
             f"({len(conv.chunks)} session chunks, chunk_chars={CHUNK_CHARS}, write_mode=strong)")
        st = prov.stats(UNIT)
        manifest_merge(out, {"ingest": {
            "agent_id": prov.agent_id(UNIT), "memories_created": created,
            "chunks": len(conv.chunks), "chunk_chars": CHUNK_CHARS, "write_mode": "strong",
            "post_ingest_stats": st,
        }})
        (out / "ingested.txt").write_text(f"{UNIT}\n", encoding="utf-8")
    finally:
        prov.close()


# ----------------------------------------------------------------- settle
def cmd_settle(args):
    """Poll until settled, wait 60s, check stranded.embedding. Exit 2 if stranded."""
    out = Path(args.out)
    prov = _provider()
    try:
        deadline = time.time() + args.timeout
        st = {}
        while time.time() < deadline:
            st = prov.stats(UNIT)
            if st.get("settled") is True:
                _log(f"settled: total={st.get('total')} pending={st.get('pending')}")
                break
            _log(f"not settled yet: pending={st.get('pending')} — waiting")
            time.sleep(args.interval)
        else:
            _log("settle timed out")
            (out / "store_stats.json").write_text(json.dumps(st, indent=2), encoding="utf-8")
            sys.exit(3)
        _log("settled flipped; waiting 60s grace then checking stranded.embedding")
        time.sleep(60)
        st = prov.stats(UNIT)
        (out / "store_stats.json").write_text(json.dumps(st, indent=2), encoding="utf-8")
        manifest_merge(out, {"settle": {"at": datetime.now(timezone.utc).isoformat(), "stats": st}})
        stranded = ((st.get("stranded") or {}).get("embedding") or 0)
        if stranded:
            _log(f"stranded.embedding={stranded} (>0) — run backfill_embeddings and settle again")
            sys.exit(2)
        _log(f"stranded.embedding=0; store settled. total={st.get('total')}")
    finally:
        prov.close()


# ----------------------------------------------------------------- frozen snapshot
def cmd_frozen(args):
    out = Path(args.out)
    prov = _provider()
    try:
        snap = prov.frozen_snapshot(UNIT)
        (out / f"frozen_{args.phase}.json").write_text(json.dumps(snap, indent=2), encoding="utf-8")
        _log(f"frozen[{args.phase}]: count={snap['count']} latest_updated_at={snap['latest_updated_at']}")
        if args.phase == "after":
            before_p = out / "frozen_before.json"
            if before_p.exists():
                before = json.loads(before_p.read_text())
                changed = (before["count"] != snap["count"]) or (before["latest_updated_at"] != snap["latest_updated_at"])
                manifest_merge(out, {"frozen_check": {"before": before, "after": snap, "changed": changed}})
                if changed:
                    _log("STORE CHANGED DURING ANSWERING — before != after")
                    sys.exit(4)
                _log("store unchanged during answering (count + latest_updated_at match)")
    finally:
        prov.close()


# ----------------------------------------------------------------- answer
def _answered_ids(hyp: Path) -> set[str]:
    done = set()
    if hyp.exists():
        for ln in hyp.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                try:
                    done.add(json.loads(ln)["question_id"])
                except Exception:
                    pass
    return done


def cmd_answer(args):
    import threading
    from .guards import ContainerWatch, FailStreak, GuardStop, GUARD_EXIT_CODE, write_stopped
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    conv = _load(args)
    prov = _provider()
    reader = get_llm("gemini", READER_MODEL, thinking_budget=THINKING_BUDGET)
    hyp_path = out / "hypotheses.jsonl"
    done = _answered_ids(hyp_path)
    todo = [q for q in conv.questions if q.question_id not in done]
    _log(f"answering {len(todo)} question(s) ({len(done)} already done), concurrency={args.concurrency}")

    lock = threading.Lock()
    watch = ContainerWatch()
    streak = FailStreak(limit=3)
    stop = {"reason": None, "affected": None}
    stop_lock = threading.Lock()

    def _remaining():
        answered = _answered_ids(hyp_path)
        return [q.question_id for q in conv.questions if q.question_id not in answered]

    def _trip(reason):
        with stop_lock:
            if stop["reason"] is None:
                stop["reason"] = reason
                stop["affected"] = _remaining()
                _log(f"GUARD STOP: {reason}")

    def one(q):
        if stop["reason"]:
            return
        # DB + core-api health before each question (and restart detection).
        bad = watch.check()
        if bad:
            _trip(f"DB/core-api health changed during run: {', '.join(bad)}")
            return
        try:
            if os.environ.get("LOCOMO_FORCE_FAIL"):
                raise RuntimeError("forced failure (LOCOMO_FORCE_FAIL test; no paid call)")
            t0 = time.perf_counter()
            items, rstats = prov.search(UNIT, q.question)  # raises CauraRecallError on server failure
            retrieve_ms = (time.perf_counter() - t0) * 1000
            context = LocomoCauraProvider.build_context(items)
            hypothesis, gen_ms, trace, usage = run_reader_pipeline(
                reader_llm=reader, question=q.question, context=context,
                question_date=None, pipeline="agentic-v1",
            )
            retrieved_dia = sorted({d for it in items for d in ((it.get("metadata") or {}).get("dia_ids") or [])})
            evidence_hit = bool(set(retrieved_dia) & set(q.evidence)) if q.evidence else None
            entry = {
                "question_id": q.question_id, "question": q.question, "answer": q.answer,
                "category": q.category, "category_name": q.category_name, "evidence": q.evidence,
                "hypothesis": hypothesis, "context": context,
                "retrieve_time_ms": retrieve_ms, "generate_time_ms": gen_ms,
                "pipeline": "agentic-v1", "pipeline_trace": trace,
                "retrieval_stats": rstats, "reader_usage": usage,
                "retrieved_dia_ids": retrieved_dia, "evidence_hit": evidence_hit,
                "retrieved_preview": [
                    {"content": str(it.get("content") or "")[:200],
                     "similarity": it.get("similarity"),
                     "dia_ids": (it.get("metadata") or {}).get("dia_ids")}
                    for it in items[:TOP_K]
                ],
            }
            with lock:
                with open(hyp_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(entry, ensure_ascii=False) + "\n")
            streak.ok()
            return ("ok", q.question_id, rstats.get("search_candidates", 0))
        except Exception as e:
            n = streak.fail()
            _log(f"  FAIL {q.question_id} ({n} in a row): {type(e).__name__}: {str(e)[:200]}")
            if streak.tripped():
                _trip(f"3 consecutive question failures; last error: {type(e).__name__}: {str(e)[:200]}")
            return ("fail", q.question_id, str(e))

    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=args.concurrency) as pool:
        futs = {pool.submit(one, q): q for q in todo}
        n = len(done)
        for fut in concurrent.futures.as_completed(futs):
            r = fut.result()
            if r and r[0] == "ok":
                n += 1
                _log(f"  [{n}/{len(conv.questions)}] {r[1]} search_candidates={r[2]}")
            if r:
                results.append(r)
    prov.close()

    if stop["reason"]:
        write_stopped(out, stop["reason"], stop["affected"])
        _log(f"STOPPED written ({len(stop['affected'])} affected) — resume with --resume")
        sys.exit(GUARD_EXIT_CODE)
    fails = [r for r in results if r and r[0] == "fail"]
    if fails:
        # Non-consecutive failures: leave them unanswered (resumable) and abort before
        # the paid judge rather than score an incomplete set.
        write_stopped(out, f"{len(fails)} question(s) failed (not 3-in-a-row); last: {fails[-1][2][:200]}",
                      [f[1] for f in fails])
        _log(f"{len(fails)} question(s) failed — STOPPED written; re-run with --resume")
        sys.exit(GUARD_EXIT_CODE)


# ----------------------------------------------------------------- judge
def _read_hyps(hyp_path: Path) -> list[dict]:
    out = []
    for ln in hyp_path.read_text(encoding="utf-8").splitlines():
        if ln.strip():
            out.append(json.loads(ln))
    return out


def cmd_judge(args):
    out = Path(args.out)
    hyp_path = out / "hypotheses.jsonl"
    hyps = _read_hyps(hyp_path)
    traces_dir = out / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    from .guards import ContainerWatch, FailStreak, GUARD_EXIT_CODE, write_stopped
    gemini = GeminiLLM(model_name=JUDGE_MODEL)   # no thinking budget on judge
    gpt4o = OpenAILLM(model_name=SECOND_JUDGE_MODEL)
    judge = LocomoJudge(gemini, gpt4o)

    g_path = out / "eval_gemini3vote.json"
    o_path = out / "eval_gpt4o.json"
    g_prev = json.loads(g_path.read_text()) if g_path.exists() else {"verdicts": {}}
    o_prev = json.loads(o_path.read_text()) if o_path.exists() else {"verdicts": {}}
    g_verdicts = g_prev.get("verdicts", {})
    o_verdicts = o_prev.get("verdicts", {})

    watch = ContainerWatch()
    streak = FailStreak(limit=3)

    def _judged_ids():
        return {q for q in g_verdicts if q in o_verdicts}

    def _stop(reason):
        affected = [h["question_id"] for h in hyps if h["question_id"] not in _judged_ids()]
        write_stopped(out, reason, affected)
        _log(f"GUARD STOP (judge): {reason} — STOPPED written ({len(affected)} affected); resume with --resume")
        _flush_eval()
        sys.exit(GUARD_EXIT_CODE)

    def _flush_eval():
        def _summ(v):
            n = len(v); c = sum(1 for x in v.values() if x["correct"])
            return {"total": n, "correct": c, "accuracy": (c / n if n else 0.0), "verdicts": v}
        g_path.write_text(json.dumps({"judge_model": JUDGE_MODEL, "judge_votes": 3, **_summ(g_verdicts)}, indent=2, ensure_ascii=False), encoding="utf-8")
        o_path.write_text(json.dumps({"judge_model": SECOND_JUDGE_MODEL, "judge_votes": 1, **_summ(o_verdicts)}, indent=2, ensure_ascii=False), encoding="utf-8")

    for i, h in enumerate(hyps, 1):
        qid = h["question_id"]
        if qid in g_verdicts and qid in o_verdicts and not args.rejudge:
            continue
        bad = watch.check()  # DB + core-api health before each question
        if bad:
            _stop(f"DB/core-api health changed during judging: {', '.join(bad)}")
        try:
            if os.environ.get("LOCOMO_FORCE_FAIL"):
                raise RuntimeError("forced failure (LOCOMO_FORCE_FAIL test; no paid call)")
            gv = judge.judge_gemini(h["question"], h["answer"], h["hypothesis"])
            ov = judge.judge_gpt4o(h["question"], h["answer"], h["hypothesis"])
            streak.ok()
        except Exception as e:
            n = streak.fail()
            _log(f"  JUDGE FAIL {qid} ({n} in a row): {type(e).__name__}: {str(e)[:200]}")
            if streak.tripped():
                _stop(f"3 consecutive judge failures; last error: {type(e).__name__}: {str(e)[:200]}")
            continue
        g_verdicts[qid] = {"category": h["category"], "category_name": h["category_name"], **gv}
        o_verdicts[qid] = {"category": h["category"], "category_name": h["category_name"], **ov}
        # trace per question
        (traces_dir / f"{qid}.json").write_text(json.dumps({
            "question_id": qid, "question": h["question"], "gold_answer": h["answer"],
            "category": h["category"], "category_name": h["category_name"],
            "evidence": h["evidence"], "evidence_hit": h.get("evidence_hit"),
            "hypothesis": h["hypothesis"], "retrieval_stats": h["retrieval_stats"],
            "retrieved_preview": h.get("retrieved_preview"),
            "pipeline_trace": h.get("pipeline_trace"), "reader_usage": h.get("reader_usage"),
            "judge_gemini3vote": gv, "judge_gpt4o": ov,
        }, indent=2, ensure_ascii=False), encoding="utf-8")
        _log(f"  [{i}/{len(hyps)}] {qid} gemini={gv['label']}({gv['n_correct']}/3) gpt4o={ov['label']}")

    _flush_eval()
    manifest_merge(out, {"judge_usage": judge.usage})
    gc = sum(1 for v in g_verdicts.values() if v["correct"])
    oc = sum(1 for v in o_verdicts.values() if v["correct"])
    _log(f"gemini 3-vote: {gc}/{len(g_verdicts)}  |  gpt-4o: {oc}/{len(o_verdicts)}")


# ----------------------------------------------------------------- summary
def cmd_summary(args):
    from .summary import build_summary
    build_summary(Path(args.out), _load(args), prices_path=args.prices)


def main():
    ap = argparse.ArgumentParser(prog="locomo")
    ap.add_argument("--data", default=None, help="path to locomo10.json (else $LOCOMO_DATA_PATH)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("stats")
    p = sub.add_parser("probe-vertex"); p.add_argument("--out", required=True)
    p = sub.add_parser("ingest"); p.add_argument("--out", required=True); p.add_argument("--no-reset", action="store_true")
    p = sub.add_parser("settle"); p.add_argument("--out", required=True); p.add_argument("--timeout", type=int, default=3600); p.add_argument("--interval", type=int, default=15)
    p = sub.add_parser("frozen-snapshot"); p.add_argument("--out", required=True); p.add_argument("--phase", choices=["before", "after"], required=True)
    p = sub.add_parser("answer"); p.add_argument("--out", required=True); p.add_argument("--concurrency", type=int, default=8)
    p = sub.add_parser("judge"); p.add_argument("--out", required=True); p.add_argument("--rejudge", action="store_true")
    p = sub.add_parser("summary"); p.add_argument("--out", required=True); p.add_argument("--prices", default=None)
    args = ap.parse_args()

    dispatch = {
        "stats": cmd_stats, "probe-vertex": cmd_probe_vertex, "ingest": cmd_ingest,
        "settle": cmd_settle, "frozen-snapshot": cmd_frozen, "answer": cmd_answer,
        "judge": cmd_judge, "summary": cmd_summary,
    }
    dispatch[args.cmd](args)


if __name__ == "__main__":
    main()
