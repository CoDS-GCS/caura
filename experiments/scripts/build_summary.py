#!/usr/bin/env python3
"""Write $OUT/summary.md for a LongMemEval run. Pure post-processing (no model
calls). Reads the harness outputs, the per-question traces, coverage.json and
prices.json.

Usage: build_summary.py --run RUN --out OUT --harness HARNESS --prices prices.json
"""
from __future__ import annotations
import argparse, json, glob, os
from pathlib import Path
from collections import defaultdict

TYPES = ["knowledge-update","multi-session","single-session-assistant",
         "single-session-preference","single-session-user","temporal-reasoning"]
ENRICH_IN, ENRICH_OUT = 2943, 142  # measured per-memory


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    return vals[min(len(vals)-1, int(p*len(vals)))]


def load_eval(p):
    if not Path(p).exists():
        return {}
    return {r["question_id"]: bool(r["correct"]) for r in json.loads(Path(p).read_text())["results"]}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--harness", required=True, type=Path)
    ap.add_argument("--prices", required=True, type=Path)
    ap.add_argument("--data-path", type=Path, default=None)
    args = ap.parse_args()

    odir = args.harness / "outputs" / args.run
    prices = json.loads(args.prices.read_text())
    traces = [json.loads(Path(f).read_text()) for f in glob.glob(str(args.out/"traces"/"*.json"))]
    primary = load_eval(odir/"eval_results.json")
    secondary = load_eval(odir/"eval_results_gemini35flashlite.json")

    # accuracy
    by_type_p = defaultdict(lambda:[0,0]); by_type_s = defaultdict(lambda:[0,0])
    for t in traces:
        qt = t["question_type"]
        if t["question_id"] in primary:
            by_type_p[qt][1]+=1; by_type_p[qt][0]+=int(primary[t["question_id"]])
        if t["question_id"] in secondary:
            by_type_s[qt][1]+=1; by_type_s[qt][0]+=int(secondary[t["question_id"]])
    tot_p = [sum(v[0] for v in by_type_p.values()), sum(v[1] for v in by_type_p.values())]
    tot_s = [sum(v[0] for v in by_type_s.values()), sum(v[1] for v in by_type_s.values())]

    # latency
    ing = [t.get("ingestion_seconds") for t in traces]
    ret = [t["search"].get("retrieve_time_ms") for t in traces]
    ans = [t["reader"].get("generate_time_ms") for t in traces]

    # tokens & $
    def price(model, tin, tout):
        pr = prices.get(model, {"in":0,"out":0})
        return tin*pr["in"]/1e6 + tout*pr["out"]/1e6
    total_mem = sum((t["memories"].get("count") or 0) for t in traces)
    enr_in, enr_out = total_mem*ENRICH_IN, total_mem*ENRICH_OUT
    rd_in = sum((t["reader"].get("prompt_tokens") or 0) for t in traces)
    rd_out = sum(((t["reader"].get("completion_tokens") or 0)+(t["reader"].get("reasoning_tokens") or 0)) for t in traces)
    # judge tokens: not recorded by the harness -> estimate from text sizes
    j_in = 0; j_out = 0
    for t in traces:
        chars = len(t.get("question") or "")+len(str(t.get("gold_answer") or ""))+len(t.get("prediction") or "")
        j_in += chars//4 + 500  # +prompt overhead
        j_out += 10
    cost_mem = price("gemini-3.1-flash-lite", enr_in, enr_out)
    cost_ans = price("gemini-3.8-flash", rd_in, rd_out)
    cost_judge = price("gpt-4o", j_in, j_out) + price("gemini-3.5-flash-lite", j_in, j_out)

    # context vs haystack
    ctx = [t["context"].get("tokens") for t in traces if t["context"].get("tokens")]
    hay = []
    dp = args.data_path or (args.harness/"data"/"longmemeval_s_cleaned.json")
    if dp.exists():
        ds = {q["question_id"]: q for q in json.loads(dp.read_text())}
        for t in traces:
            q = ds.get(t["question_id"].replace("_abs",""))
            if q:
                c = sum(len(x.get("content","")) for s in q.get("haystack_sessions",[]) for x in s if isinstance(x,dict))
                hay.append(c//4)

    # coverage per type
    cov_type = defaultdict(lambda:[0,0])
    for t in traces:
        ec = t.get("evidence_coverage")
        if ec and ec.get("fully_covered") is not None:
            cov_type[t["question_type"]][1]+=1
            cov_type[t["question_type"]][0]+=int(bool(ec["fully_covered"]))

    # failures
    fails = defaultdict(list)
    for t in traces:
        if t.get("failure_label"):
            fails[t["failure_label"]].append(t["question_id"])

    # store size
    dump = args.out/"memory_dump.sql.gz"
    dump_size = f"{dump.stat().st_size/1e6:.1f} MB" if dump.exists() else "n/a"

    L = []
    L.append(f"# Summary — {args.run}\n")
    L.append("## Accuracy\n")
    L.append("| type | gpt-4o | gemini-3.5-flash-lite |")
    L.append("|---|---|---|")
    for qt in TYPES:
        p=by_type_p.get(qt,[0,0]); s=by_type_s.get(qt,[0,0])
        L.append(f"| {qt} | {p[0]}/{p[1]} | {s[0]}/{s[1]} |")
    L.append(f"| **overall** | **{tot_p[0]}/{tot_p[1]}** | **{tot_s[0]}/{tot_s[1]}** |\n")

    L.append("## Latency (p50 / p95)\n")
    L.append("| phase | p50 | p95 | unit |")
    L.append("|---|---|---|---|")
    L.append(f"| ingestion | {pct(ing,.5)} | {pct(ing,.95)} | s/question |")
    L.append(f"| retrieval | {pct(ret,.5)} | {pct(ret,.95)} | ms |")
    L.append(f"| answering | {pct(ans,.5)} | {pct(ans,.95)} | ms |")
    L.append("\n_Pause time is excluded: these are the harness's per-call measurements, taken around the operation itself._\n")

    L.append("## Tokens & cost (prices from prices.json)\n")
    L.append("| phase | model | in tok | out tok | $ |")
    L.append("|---|---|---|---|---|")
    L.append(f"| memory building (enrichment) | gemini-3.1-flash-lite | {enr_in:,} | {enr_out:,} | {cost_mem:.2f} |")
    L.append(f"| answering (reader) | gemini-3.8-flash | {rd_in:,} | {rd_out:,} | {cost_ans:.2f} |")
    L.append(f"| judging (gpt-4o + flash-lite, est.) | gpt-4o + gemini-3.5-flash-lite | {j_in:,} | {j_out:,} | {cost_judge:.2f} |")
    L.append(f"| **total** | | | | **{cost_mem+cost_ans+cost_judge:.2f}** |")
    L.append("\n_Enrichment tokens are derived from the measured rate (2943 in / 142 out per memory); "
             "embeddings are local TEI (free); judge tokens are estimated from text sizes (harness does not record them)._\n")

    L.append("## Context vs full haystack\n")
    L.append(f"- median retrieved-context tokens: **{pct(ctx,.5)}** (p95 {pct(ctx,.95)})")
    L.append(f"- median full-haystack tokens (chars/4 est.): **{pct(hay,.5)}**")
    if ctx and hay and pct(hay,.5):
        L.append(f"- retrieved context is ~{pct(ctx,.5)/pct(hay,.5)*100:.0f}% of the haystack\n")

    L.append("\n## Evidence coverage (fully-covered, per type)\n")
    L.append("| type | fully covered |")
    L.append("|---|---|")
    for qt in TYPES:
        c=cov_type.get(qt,[0,0]); L.append(f"| {qt} | {c[0]}/{c[1]} |")

    L.append("\n## Failures by label\n")
    if fails:
        for lab,ids in sorted(fails.items(), key=lambda x:-len(x[1])):
            L.append(f"- **{lab}** ({len(ids)}): {', '.join(ids)}")
    else:
        L.append("- none")

    L.append(f"\n## Store\n- total memories: **{total_mem:,}**\n- memory_dump.sql.gz: {dump_size}\n")

    (args.out/"summary.md").write_text("\n".join(L)+"\n", encoding="utf-8")
    print(f"wrote {args.out/'summary.md'}  (overall gpt-4o {tot_p[0]}/{tot_p[1]})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
