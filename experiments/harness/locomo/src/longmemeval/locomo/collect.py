"""Post-processing / collection for the LoCoMo conv0 run (ours).

Runs at the end of run_locomo.sh with NO paid calls — reads the run folder's
hypotheses.jsonl, eval_*.json, ingest_trace.json, manifest.json and the dataset,
and writes:

  * traces/<question_id>.json  — full per-question trace (retrieval, evidence,
    per-stage reader tokens, latency breakdown, 3 judge votes + majority + gpt-4o,
    4-way failure label);
  * summary.md, compare_authors.md  — in the run folder;
  * experiments/results/locomo_caura_conv0/  — per_question.csv/.json, correct.json,
    failed.json, failures.md, comparison.md, cost.md, examples/<category>/.

Everything is derived from already-recorded artifacts; it is safe to re-run.
"""

from __future__ import annotations

import csv
import json
import statistics
from pathlib import Path

from .dataset import CATEGORY_NAMES, SCORED_CATEGORIES

FAILURE_LABELS = ("not_stored", "stored_not_retrieved", "retrieved_wrong_answer", "judge_disagreement")


# --------------------------------------------------------------------- helpers
def _pct(c, n):
    return f"{100*c/n:.1f}%" if n else "n/a"


def _pctile(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def _load(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _read_hyps(out: Path):
    rows = []
    p = out / "hypotheses.jsonl"
    if p.exists():
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    return rows


def _stage_rollup(reader_usage) -> dict:
    """Per-stage reader tokens: {stage: {input, output, thinking}}."""
    out: dict = {}
    for c in (reader_usage or {}).get("calls", []) or []:
        s = c.get("stage") or "?"
        d = out.setdefault(s, {"input": 0, "output": 0, "thinking": 0, "n_calls": 0})
        d["input"] += int(c.get("prompt_tokens", 0) or 0)
        d["output"] += int(c.get("completion_tokens", 0) or 0)
        d["thinking"] += int(c.get("reasoning_tokens", 0) or 0)
        d["n_calls"] += 1
    return out


def _context_tokens(h, stages) -> dict:
    """Measured from the extract stage's prompt tokens (it reads the full context);
    fall back to chars/4 if the extract stage didn't record usage."""
    ex = stages.get("extract")
    if ex and ex.get("input"):
        return {"tokens": ex["input"], "source": "measured (extract-stage prompt tokens, incl. prompt overhead)"}
    return {"tokens": (h.get("context_chars", 0) // 4), "source": "estimated (chars/4)"}


def _failure_label(headline_ok, second_ok, evidence, stored_dia, retrieved_dia):
    if headline_ok:
        return None
    ev = set(evidence or [])
    if second_ok:  # headline says wrong but the second judge says right
        return "judge_disagreement"
    if ev and not (ev & stored_dia):
        return "not_stored"
    if ev and not (ev & set(retrieved_dia or [])):
        return "stored_not_retrieved"
    return "retrieved_wrong_answer"


def _price(prices, model):
    return (prices or {}).get(model, {})


def _cost(prices, model, tin, tout):
    pr = _price(prices, model)
    return round(tin / 1e6 * pr.get("in", 0) + tout / 1e6 * pr.get("out", 0), 4)


# --------------------------------------------------------------------- main
def build_outputs(out: Path, conv, prices_path: str | None = None, results_dir: Path | None = None):
    prices = _load(Path(prices_path)) if prices_path else None
    manifest = _load(out / "manifest.json") or {}
    g = _load(out / "eval_gemini3vote.json") or {"verdicts": {}}
    o = _load(out / "eval_gpt4o.json") or {"verdicts": {}}
    ingest = _load(out / "ingest_trace.json") or {}
    store_stats = _load(out / "store_stats.json") or manifest.get("settle", {}).get("stats") or {}
    hyps = _read_hyps(out)
    gv, ov = g.get("verdicts", {}), o.get("verdicts", {})

    stored_dia = {d for c in conv.chunks for d in c.dia_ids}

    traces_dir = out / "traces"
    traces_dir.mkdir(parents=True, exist_ok=True)

    rows = []           # one per question, for csv/json/results
    for h in hyps:
        qid = h["question_id"]
        gverd = gv.get(qid, {})
        overd = ov.get(qid, {})
        headline_ok = bool(gverd.get("correct"))
        second_ok = bool(overd.get("correct"))
        evidence = h.get("evidence") or []
        retrieved_dia = h.get("retrieved_dia_ids") or []
        label = _failure_label(headline_ok, second_ok, evidence, stored_dia, retrieved_dia)
        stages = _stage_rollup(h.get("reader_usage"))
        ctx = _context_tokens(h, stages)
        search_ms = h.get("retrieve_time_ms") or 0.0
        answer_ms = h.get("generate_time_ms") or 0.0
        judge_ms = (gverd.get("judge_latency_ms") or 0.0) + (overd.get("judge_latency_ms") or 0.0)
        total_ms = search_ms + answer_ms + judge_ms
        ru = h.get("reader_usage") or {}
        ev_stored = bool(set(evidence) & stored_dia) if evidence else None
        ev_retrieved = h.get("evidence_hit")
        agreement = "agree" if headline_ok == second_ok else "disagree"

        trace = {
            "question_id": qid,
            "question": h["question"],
            "category": h["category"], "category_name": h["category_name"],
            "gold_answer": h["answer"], "prediction": h["hypothesis"],
            "judge": {
                "gemini3vote": {
                    "label": gverd.get("label"), "correct": headline_ok,
                    "n_correct_votes": gverd.get("n_correct"), "votes": gverd.get("votes"),
                    "seeds": gverd.get("seeds"), "explanations": gverd.get("explanations"),
                    "latency_ms": gverd.get("judge_latency_ms"),
                },
                "gpt4o": {
                    "label": overd.get("label"), "correct": second_ok,
                    "explanation": overd.get("explanation"), "latency_ms": overd.get("judge_latency_ms"),
                },
                "agreement": agreement,
            },
            "retrieval": {
                **(h.get("retrieval") or {}),
                "candidates_retrieved": (h.get("retrieval_stats") or {}).get("search_candidates"),
                "context_tokens": ctx,
            },
            "evidence": {
                "evidence_dia_ids": evidence,
                "stored": ev_stored,
                "retrieved_in_context": ev_retrieved,
                "retrieved_dia_ids": retrieved_dia,
            },
            "reader_tokens_by_stage": stages,
            "reader_tokens_total": {
                "input": ru.get("prompt_tokens"), "output": ru.get("completion_tokens"),
                "thinking": ru.get("reasoning_tokens"), "n_calls": ru.get("n_calls"),
            },
            "latency_ms": {
                "search": round(search_ms, 1), "answer": round(answer_ms, 1),
                "judge": round(judge_ms, 1), "total": round(total_ms, 1),
            },
            "failure_label": label,
            "pipeline_trace": h.get("pipeline_trace"),
        }
        (traces_dir / f"{qid}.json").write_text(json.dumps(trace, indent=2, ensure_ascii=False), encoding="utf-8")

        rows.append({
            "question_id": qid, "category": h["category_name"], "question": h["question"],
            "gold_answer": h["answer"], "prediction": h["hypothesis"],
            "verdict_gemini3vote": "correct" if headline_ok else "wrong",
            "n_correct_votes": gverd.get("n_correct"),
            "verdict_gpt4o": "correct" if second_ok else "wrong",
            "agreement": agreement, "failure_label": label or "",
            "evidence_stored": ev_stored, "evidence_retrieved": ev_retrieved,
            "candidates_retrieved": (h.get("retrieval_stats") or {}).get("search_candidates"),
            "context_tokens": ctx["tokens"],
            "reader_input": ru.get("prompt_tokens"), "reader_output": ru.get("completion_tokens"),
            "reader_thinking": ru.get("reasoning_tokens"),
            "search_ms": round(search_ms, 1), "answer_ms": round(answer_ms, 1),
            "judge_ms": round(judge_ms, 1), "total_ms": round(total_ms, 1),
            "trace_file": f"../../runs/{out.name}/traces/{qid}.json",
        })

    _write_summary(out, conv, rows, gv, ov, ingest, store_stats, manifest, prices)
    _write_compare(out, conv, gv)
    if results_dir is not None:
        _write_results(results_dir, out, conv, rows, gv, ov, ingest, prices)
    print(f"collect: wrote {len(rows)} traces + summary.md/compare_authors.md"
          + (f" + {results_dir}" if results_dir else ""))


# --------------------------------------------------------------------- summary.md
def _acc(rows, key):
    oc = sum(1 for r in rows if r[key] == "correct"); on = len(rows)
    by = {}
    for c in SCORED_CATEGORIES:
        name = CATEGORY_NAMES[c]
        rs = [r for r in rows if r["category"] == name]
        by[name] = (sum(1 for r in rs if r[key] == "correct"), len(rs))
    return oc, on, by


def _write_summary(out, conv, rows, gv, ov, ingest, store_stats, manifest, prices):
    goc, gon, gby = _acc(rows, "verdict_gemini3vote")
    ooc, oon, oby = _acc(rows, "verdict_gpt4o")
    search = [r["search_ms"] for r in rows]
    answer = [r["answer_ms"] for r in rows]
    total = [r["total_ms"] for r in rows]

    r_in = sum(r["reader_input"] or 0 for r in rows)
    r_out = sum(r["reader_output"] or 0 for r in rows)
    r_think = sum(r["reader_thinking"] or 0 for r in rows)
    ctx_tok = sum(r["context_tokens"] or 0 for r in rows)
    ju = manifest.get("judge_usage", {})
    jg, jo = ju.get("gemini", {}), ju.get("gpt4o", {})
    it = ingest.get("totals", {})
    mc, ee = it.get("memory_creation", {}), it.get("entity_extraction", {})

    # costs
    reader_cost = _cost(prices, "gemini-3.8-flash", r_in, r_out + r_think)  # thinking billed as output
    jg_cost = _cost(prices, "gemini-3.8-flash", jg.get("prompt_tokens", 0), jg.get("completion_tokens", 0))
    jo_cost = _cost(prices, "gpt-4o", jo.get("prompt_tokens", 0), jo.get("completion_tokens", 0))
    mc_cost = _cost(prices, "gemini-3.1-flash-lite", mc.get("in", 0), mc.get("out", 0))
    ee_cost = _cost(prices, "gemini-3.1-flash-lite", ee.get("in", 0), ee.get("out", 0))

    # evidence coverage per category (of questions that HAVE evidence)
    cov = {}
    for c in SCORED_CATEGORIES:
        name = CATEGORY_NAMES[c]
        rs = [r for r in rows if r["category"] == name and r["evidence_retrieved"] is not None]
        hit = sum(1 for r in rs if r["evidence_retrieved"])
        cov[name] = (hit, len(rs))

    # failure counts per label
    fails = {lab: [r["question_id"] for r in rows if r["failure_label"] == lab] for lab in FAILURE_LABELS}
    agree = sum(1 for r in rows if r["agreement"] == "agree")

    L = []
    L.append("# Caura (MemClaw) on LoCoMo — conversation 0 (conv-26)\n")
    L.append("Store configuration not published for the authors' headline; built from conv0 with the "
             "published parameters (chunk_characters=4000, top_k=30, judge_votes=3, thinking_tokens=2048 "
             f"on the reader). Agent `{conv.sample_id}`→`locomo-conv-{conv.conv_index}`, min_similarity=0.0, write_mode=strong.\n")

    L.append("## Accuracy (categories 1–4)\n")
    L.append("| judge | overall | multi-hop | temporal | open-domain | single-hop |")
    L.append("|---|---|---|---|---|---|")
    def cells(by): return " | ".join(_pct(by[CATEGORY_NAMES[c]][0], by[CATEGORY_NAMES[c]][1]) for c in SCORED_CATEGORIES)
    L.append(f"| **gemini-3.8-flash 3-vote (headline)** | **{_pct(goc,gon)}** ({goc}/{gon}) | {cells(gby)} |")
    L.append(f"| gpt-4o (second judge) | {_pct(ooc,oon)} ({ooc}/{oon}) | {cells(oby)} |")
    L.append(f"\nJudge agreement (3-vote vs gpt-4o): **{agree}/{len(rows)}** ({_pct(agree,len(rows))}).\n")

    L.append("## Latency (ms)\n| phase | p50 | p95 |\n|---|---|---|")
    L.append(f"| search | {_pctile(search,0.5):.0f} | {_pctile(search,0.95):.0f} |")
    L.append(f"| answer (agentic-v1) | {_pctile(answer,0.5):.0f} | {_pctile(answer,0.95):.0f} |")
    L.append(f"| total (search+answer+judge) | {_pctile(total,0.5):.0f} | {_pctile(total,0.95):.0f} |\n")

    L.append("## Tokens & cost per phase\n")
    L.append("Gemini rates are the placeholder values in `prices.json`; ingest tokens are **derived** "
             "(see `ingest_trace.json`), not server-metered. `cost.md` in the results folder is the "
             "authoritative cost view.\n")
    L.append("| phase | model | provider | in | out | thinking | $ |")
    L.append("|---|---|---|---|---|---|---|")
    L.append(f"| memory building (derived) | gemini-3.1-flash-lite | Vertex | {mc.get('in',0)} | {mc.get('out',0)} | — | {mc_cost} |")
    L.append(f"| entity extraction (derived) | gemini-3.1-flash-lite | AI Studio | {ee.get('in',0)} | {ee.get('out',0)} | — | {ee_cost} |")
    L.append(f"| retrieval context | — | — | {ctx_tok} | — | — | incl. in reader input |")
    L.append(f"| reader | gemini-3.8-flash | Vertex | {r_in} | {r_out} | {r_think} | {reader_cost} |")
    L.append(f"| judge (headline 3-vote) | gemini-3.8-flash | Vertex | {jg.get('prompt_tokens',0)} | {jg.get('completion_tokens',0)} | — | {jg_cost} |")
    L.append(f"| judge (second) | gpt-4o | OpenAI | {jo.get('prompt_tokens',0)} | {jo.get('completion_tokens',0)} | — | {jo_cost} |\n")

    L.append("## Evidence coverage per category\n")
    L.append("Share of questions (that carry a resolvable evidence id) whose gold evidence turns were in the retrieved context.\n")
    L.append("| category | covered |\n|---|---|")
    for c in SCORED_CATEGORIES:
        name = CATEGORY_NAMES[c]; hit, n = cov[name]
        L.append(f"| {name} | {_pct(hit,n)} ({hit}/{n}) |")
    L.append("")

    L.append("## Failure labels (wrong under headline 3-vote judge)\n")
    L.append("| label | count | question_ids |\n|---|---|---|")
    for lab in FAILURE_LABELS:
        ids = fails[lab]
        L.append(f"| {lab} | {len(ids)} | {', '.join(ids) or '—'} |")
    L.append("")

    L.append("## Settled store\n```json")
    L.append(json.dumps({"total": store_stats.get("total"), "by_type": store_stats.get("by_type"),
                         "settled": store_stats.get("settled"), "stranded": store_stats.get("stranded"),
                         "settle_time_s": ingest.get("settle_time_s")}, indent=2))
    L.append("```")
    fc = manifest.get("frozen_check")
    if fc:
        L.append(f"\nFrozen-store check during answering: **{'CHANGED' if fc.get('changed') else 'unchanged'}**.")
    (out / "summary.md").write_text("\n".join(L), encoding="utf-8")


# --------------------------------------------------------------------- compare_authors.md
def _compare_text(conv, gv):
    goc = sum(1 for v in gv.values() if v.get("correct")); gon = len(gv)
    C = []
    C.append("# conv0 (152 Q) vs the authors' full-set overall — LoCoMo\n")
    C.append(f"**Ours:** gemini-3.8-flash 3-vote judge **{_pct(goc,gon)} ({goc}/{gon})** on "
             f"**conv 0 only (conv-26, {gon} scored cat 1–4 questions)**.\n")
    C.append("**Authors (EVIDENCE.md `locomo_caura_retrieval_accuracy_2026_09`):** "
             "**77.9% (1,199/1,540)** over all 1,540 scored cat 1–4 questions across all 10 conversations.\n")
    C.append("Independent **rerun of the authors' pipeline family, not a reproduction**: their headline "
             "harness (`caura-ai/yanki-locomo`) and per-question results are not public, so the numbers are "
             "not directly comparable. Every difference:\n")
    C.append("| dimension | authors | ours |")
    C.append("|---|---|---|")
    C.append("| scope | 10 convs, 1,540 Q | **conv 0 only, 152 Q** |")
    C.append("| harness | yanki-locomo (private, no per-Q artifact) | our longmemeval-derived locomo harness |")
    C.append("| store config | `caura-balanced-50-v3-20260910` (not published) | built from conv0 with published params |")
    C.append("| chunk_characters | 4000 | 4000 |")
    C.append("| top_k | 30 | 30 |")
    C.append("| judge_votes | 3 (gemini-3.8-flash) | 3 (gemini-3.8-flash, seeds recorded) |")
    C.append("| thinking_tokens | 2048 (scope not published) | 2048 on **reader only** (recorded) |")
    C.append("| min_similarity | not published (caura-locomo used 0.0) | 0.0 per request |")
    C.append("| reader/judge transport | not published | **Vertex AI** (Google Cloud credits) |")
    C.append("| second judge | none | gpt-4o single pass (added, reported beside) |")
    C.append("| embedder | not published | BAAI/bge-m3 local GPU (1024-d) |")
    C.append("| reader / judge temperature | not published | 0.0 / 0.0 |")
    return "\n".join(C) + "\n"


def _write_compare(out, conv, gv):
    (out / "compare_authors.md").write_text(_compare_text(conv, gv), encoding="utf-8")


# --------------------------------------------------------------------- results folder
def _write_results(rdir, out, conv, rows, gv, ov, ingest, prices):
    rdir.mkdir(parents=True, exist_ok=True)
    cols = ["question_id", "category", "question", "gold_answer", "prediction",
            "verdict_gemini3vote", "n_correct_votes", "verdict_gpt4o", "agreement",
            "failure_label", "evidence_stored", "evidence_retrieved", "candidates_retrieved",
            "context_tokens", "reader_input", "reader_output", "reader_thinking",
            "search_ms", "answer_ms", "judge_ms", "total_ms", "trace_file"]
    with open(rdir / "per_question.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols); w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k) for k in cols})
    (rdir / "per_question.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False), encoding="utf-8")

    correct = [r["question_id"] for r in rows if r["verdict_gemini3vote"] == "correct"]
    failed = [{"question_id": r["question_id"], "category": r["category"], "failure_label": r["failure_label"],
               "gold_answer": r["gold_answer"], "prediction": r["prediction"],
               "verdict_gpt4o": r["verdict_gpt4o"]} for r in rows if r["verdict_gemini3vote"] == "wrong"]
    (rdir / "correct.json").write_text(json.dumps(correct, indent=2), encoding="utf-8")
    (rdir / "failed.json").write_text(json.dumps(failed, indent=2, ensure_ascii=False), encoding="utf-8")

    # failures.md grouped by label
    F = ["# Failures (wrong under the headline gemini-3.8-flash 3-vote judge)\n"]
    for lab in FAILURE_LABELS:
        items = [r for r in rows if r["failure_label"] == lab]
        F.append(f"## {lab} ({len(items)})\n")
        for r in items:
            F.append(f"- **{r['question_id']}** ({r['category']}) — Q: {r['question']}\n"
                     f"  - gold: `{r['gold_answer']}`  ·  pred: `{r['prediction'][:160]}`  ·  gpt-4o: {r['verdict_gpt4o']}")
        F.append("")
    (rdir / "failures.md").write_text("\n".join(F), encoding="utf-8")

    # comparison.md (same substance as compare_authors.md)
    (rdir / "comparison.md").write_text(_compare_text(conv, gv), encoding="utf-8")

    # cost.md
    r_in = sum(r["reader_input"] or 0 for r in rows); r_out = sum(r["reader_output"] or 0 for r in rows)
    r_think = sum(r["reader_thinking"] or 0 for r in rows)
    it = ingest.get("totals", {}); mc, ee = it.get("memory_creation", {}), it.get("entity_extraction", {})
    reader_cost = _cost(prices, "gemini-3.8-flash", r_in, r_out + r_think)
    mc_cost = _cost(prices, "gemini-3.1-flash-lite", mc.get("in", 0), mc.get("out", 0))
    ee_cost = _cost(prices, "gemini-3.1-flash-lite", ee.get("in", 0), ee.get("out", 0))
    C = ["# Cost — conv0\n",
         "Gemini rates are `prices.json` placeholders; Vertex ≈ AI Studio per-token. Ingest tokens are "
         "**derived** (server doesn't expose per-memory usage); reader/judge tokens are **measured** from "
         "provider usage. Authoritative $ should come from the Google Cloud / OpenAI billing pages.\n",
         "| phase | model | provider | source | $ |", "|---|---|---|---|---|",
         f"| memory creation | gemini-3.1-flash-lite | Vertex | derived | {mc_cost} |",
         f"| entity extraction | gemini-3.1-flash-lite | AI Studio | derived | {ee_cost} |",
         f"| embeddings | bge-m3 | local GPU | — | 0.00 |",
         f"| reader | gemini-3.8-flash | Vertex | measured | {reader_cost} |"]
    (rdir / "cost.md").write_text("\n".join(C) + "\n", encoding="utf-8")

    # examples/<category>/<qid>.md — one readable example per category
    ex = rdir / "examples"; ex.mkdir(exist_ok=True)
    (ex / "README.md").write_text("One readable trace per LoCoMo category (first question of each).\n", encoding="utf-8")
    by_cat = {}
    hyp_by_id = {h["question_id"]: h for h in _read_hyps(out)}
    for r in rows:
        by_cat.setdefault(r["category"], r)
    for name, r in by_cat.items():
        d = ex / name; d.mkdir(exist_ok=True)
        h = hyp_by_id.get(r["question_id"], {})
        M = [f"# {r['question_id']} — {name}\n",
             f"**Question:** {r['question']}\n", f"**Gold:** {r['gold_answer']}\n",
             f"**Prediction:** {r['prediction']}\n",
             f"**Headline (gemini 3-vote):** {r['verdict_gemini3vote']} ({r['n_correct_votes']}/3)  ·  "
             f"**gpt-4o:** {r['verdict_gpt4o']}  ·  **failure:** {r['failure_label'] or '—'}\n",
             f"**Evidence:** stored={r['evidence_stored']} retrieved={r['evidence_retrieved']}\n",
             "## Retrieved memories (top, by rank)\n"]
        for cand in (h.get("retrieval") or {}).get("candidates", [])[:6]:
            M.append(f"- `{cand.get('memory_id')}` sim={cand.get('similarity')} score={cand.get('score')} "
                     f"dia={cand.get('dia_ids')}\n  > {str(cand.get('text',''))[:240].replace(chr(10),' ')}")
        (d / f"{r['question_id']}.md").write_text("\n".join(M), encoding="utf-8")
