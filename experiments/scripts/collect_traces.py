#!/usr/bin/env python3
"""Build per-question traces for a LongMemEval run: $OUT/traces/<qid>.json.

Pure post-processing — reads only files already produced by the run (no model
calls, no paid API). Inputs (best-effort; missing pieces degrade gracefully):

  harness outputs/<RUN>/ : hypotheses.jsonl, eval_results.json,
                           eval_results_gemini35flashlite.json, context_tokens.json
  $OUT/                  : coverage.json, ingest.log, memories.jsonl
  dataset                : longmemeval_s_cleaned.json (gold turns)

Enrichment tokens are DERIVED from the measured smoke rate (no server logging
available on the baked image): 2943 input / 142 output tokens per stored memory.

Usage: collect_traces.py --run RUN --out OUT --harness HARNESS [--data-path P]
"""
from __future__ import annotations
import argparse, json, re
from pathlib import Path

ENRICH_IN_PER_MEM = 2943   # measured (in-container replay, gemini-3.1-flash-lite)
ENRICH_OUT_PER_MEM = 142


def load_jsonl(p: Path):
    out = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if line.strip():
                d = json.loads(line)
                out[d["question_id"]] = d  # last write wins (resumed runs repeat)
    return out


def load_eval(p: Path):
    if not p.exists():
        return {}, None
    d = json.loads(p.read_text(encoding="utf-8"))
    return {r["question_id"]: r for r in d.get("results", [])}, d.get("judge_model_resolved")


def load_coverage(p: Path):
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    rows = d.get("per_question") or d.get("questions") or (d if isinstance(d, list) else [])
    return {r["question_id"]: r for r in rows if isinstance(r, dict) and "question_id" in r}


def load_context_tokens(p: Path):
    if not p.exists():
        return {}
    d = json.loads(p.read_text(encoding="utf-8"))
    return {r["question_id"]: r for r in d.get("questions", []) if "question_id" in r}


def parse_ingest_seconds(p: Path):
    """harness line: '  [i/n] #<id>: <s> sessions -> <m> chunks in <X.X>s'"""
    secs = {}
    if p.exists():
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            m = re.search(r"#(\S+?):.*?in ([\d.]+)s", line)
            if m:
                secs[m.group(1)] = float(m.group(2))
    return secs


def gold_turn_texts(item: dict) -> list[str]:
    gold_sids = set(item.get("answer_session_ids") or [])
    sids = item.get("haystack_session_ids") or []
    sessions = item.get("haystack_sessions") or []
    out = []
    for sid, sess in zip(sids, sessions):
        if sid in gold_sids:
            for t in sess:
                if isinstance(t, dict) and t.get("has_answer") and t.get("content"):
                    out.append(t["content"].strip())
    # fall back: all turns of gold sessions if none flagged has_answer
    if not out:
        for sid, sess in zip(sids, sessions):
            if sid in gold_sids:
                out += [t["content"].strip() for t in sess if isinstance(t, dict) and t.get("content")]
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--harness", required=True, type=Path)
    ap.add_argument("--data-path", type=Path, default=None)
    ap.add_argument("--mem-counts", type=Path, default=None,
                    help="JSON {agent_id: {total, by_type, by_status, derived}} from the DB")
    args = ap.parse_args()

    odir = args.harness / "outputs" / args.run
    hyps = load_jsonl(odir / "hypotheses.jsonl")
    primary, primary_resolved = load_eval(odir / "eval_results.json")
    secondary, _ = load_eval(odir / "eval_results_gemini35flashlite.json")
    coverage = load_coverage(args.out / "coverage.json")
    ctx_tokens = load_context_tokens(odir / "context_tokens.json")
    ingest_secs = parse_ingest_seconds(args.out / "ingest.log")
    mem_counts = json.loads(args.mem_counts.read_text()) if args.mem_counts and args.mem_counts.exists() else {}
    memories = load_jsonl(args.out / "memories.jsonl")  # {qid-agent...}: unused keying; see below

    # memories.jsonl is keyed per memory; build per-agent stored text for the store check
    stored_text_by_agent: dict[str, str] = {}
    mp = args.out / "memories.jsonl"
    if mp.exists():
        for line in mp.read_text(encoding="utf-8", errors="ignore").splitlines():
            if not line.strip():
                continue
            try:
                m = json.loads(line)
            except Exception:
                continue
            a = m.get("agent_id", "")
            stored_text_by_agent[a] = stored_text_by_agent.get(a, "") + "\n" + (m.get("content") or "")

    dataset = {}
    dp = args.data_path or (args.harness / "data" / "longmemeval_s_cleaned.json")
    if dp.exists():
        dataset = {q["question_id"]: q for q in json.loads(dp.read_text(encoding="utf-8"))}

    tdir = args.out / "traces"
    tdir.mkdir(parents=True, exist_ok=True)

    agent_prefix = "lmeo"
    written = 0
    for qid, h in hyps.items():
        pj = primary.get(qid, {})
        sj = secondary.get(qid, {})
        p_correct = bool(pj.get("correct")) if pj else None
        s_correct = bool(sj.get("correct")) if sj else None
        cov = coverage.get(qid, {})
        ct = ctx_tokens.get(qid, {})
        ds_item = dataset.get(qid.replace("_abs", ""), {})
        agent_id = f"{agent_prefix}-{re.sub(r'[^a-zA-Z0-9_-]', '_', qid)}"
        mc = mem_counts.get(agent_id, {})

        n_mem = mc.get("total")
        enrich = None
        if n_mem is not None:
            enrich = {
                "input_tokens": n_mem * ENRICH_IN_PER_MEM,
                "output_tokens": n_mem * ENRICH_OUT_PER_MEM,
                "basis": f"derived from measured rate ({ENRICH_IN_PER_MEM} in / {ENRICH_OUT_PER_MEM} out per memory)",
            }

        ru = h.get("reader_usage", {}) or {}
        rstats = h.get("retrieval_stats", {}) or {}

        # failure label (only for wrong primary answers)
        failure = None
        if p_correct is False:
            if s_correct is True:
                failure = "judge_disagreement"
            elif cov.get("fully_covered") is True:
                failure = "retrieved_wrong_answer"
            elif cov:  # gold turns missing from the context
                stored = stored_text_by_agent.get(agent_id, "")
                golds = gold_turn_texts(ds_item)
                in_store = stored and golds and all(
                    any(g[:120] in stored for g in golds[:1] if g) for _ in [0]
                )
                failure = "stored_not_retrieved" if (stored and in_store) else "not_stored"
            else:
                failure = "unknown"

        trace = {
            "question_id": qid,
            "question_type": h.get("question_type"),
            "question": h.get("question"),
            "gold_answer": h.get("answer"),
            "prediction": h.get("hypothesis"),
            "verdicts": {
                "gpt-4o": {"correct": p_correct, "reason": pj.get("judge_reason"),
                           "model_resolved": primary_resolved},
                "gemini-3.5-flash-lite": {"correct": s_correct, "reason": sj.get("judge_reason")},
            },
            "memories": {
                "count": n_mem,
                "by_type": mc.get("by_type"),
                "by_status": mc.get("by_status"),
                "server_derived": mc.get("derived"),
            } if mc else {"count": None, "note": "DB mem-counts not provided"},
            "enrichment_tokens": enrich,
            "ingestion_seconds": ingest_secs.get(qid),
            "search": {
                "queries": rstats.get("queries"),
                "retrieve_time_ms": h.get("retrieve_time_ms"),
                "candidates": rstats.get("search_candidates"),
                "server_derived_dropped": rstats.get("server_derived_dropped"),
                "retrieved": rstats.get("retrieved"),  # [{id, score, memory_type, title, text}]
            },
            "context": {
                "chars": len(h.get("context", "") or ""),
                "tokens": ct.get("context_tokens"),
                "tokens_source": "context_tokens.py (Gemini tokenizer)" if ct else "not computed (chars/4 ~= %d)" % (len(h.get("context","") or "")//4),
            },
            "evidence_coverage": {
                "gold_turns": cov.get("gold_turns"),
                "gold_turns_present": cov.get("gold_turns_present"),
                "fully_covered": cov.get("fully_covered"),
            } if cov else None,
            "reader": {
                "model": ru.get("model"),
                "total_tokens": ru.get("total_tokens"),
                "prompt_tokens": ru.get("prompt_tokens"),
                "completion_tokens": ru.get("completion_tokens"),
                "reasoning_tokens": ru.get("reasoning_tokens"),
                "generate_time_ms": h.get("generate_time_ms"),
                "by_stage": ru.get("calls"),
            },
            "failure_label": failure,
        }
        (tdir / f"{qid}.json").write_text(json.dumps(trace, indent=2), encoding="utf-8")
        written += 1

    print(f"wrote {written} traces to {tdir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
