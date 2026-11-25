#!/usr/bin/env python3
"""Write $OUT/compare_authors.md — our run vs the authors' published headline
(caura-500-opaque-compact) restricted to our 60 question_ids. Pure
post-processing (no model calls).

Authors' per-question verdicts come from experiments/configs/authors_headline_60q_verdicts.json
(extracted earlier from the committed run). Their published latency medians are
7.6 s retrieval / 9.9 s answering (REPRODUCE/report).

Usage: compare_authors.py --run RUN --out OUT --harness HARNESS
                          --authors-verdicts FILE [--authors-run DIR]
"""
from __future__ import annotations
import argparse, json, glob
from pathlib import Path
from collections import defaultdict

TYPES = ["knowledge-update","multi-session","single-session-assistant",
         "single-session-preference","single-session-user","temporal-reasoning"]
PUB_RET_MS, PUB_ANS_MS = 7600, 9900  # authors' published medians


def pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    return vals[min(len(vals)-1, int(p*len(vals)))] if vals else None


def load_eval(p):
    return {r["question_id"]: bool(r["correct"]) for r in json.loads(Path(p).read_text())["results"]} if Path(p).exists() else {}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run", required=True)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--harness", required=True, type=Path)
    ap.add_argument("--authors-verdicts", required=True, type=Path)
    ap.add_argument("--authors-run", type=Path,
                    default=Path("experiments/harness/longmemeval/outputs/caura-500-opaque-compact"))
    args = ap.parse_args()

    odir = args.harness / "outputs" / args.run
    av = json.loads(args.authors_verdicts.read_text())
    authors = av["verdicts"]  # {qid: {question_type, gpt4o_correct, ...}}
    ours = load_eval(odir / "eval_results.json")
    traces = {json.loads(Path(f).read_text())["question_id"]: json.loads(Path(f).read_text())
              for f in glob.glob(str(args.out/"traces"/"*.json"))}
    ids = list(authors.keys())

    # per-type accuracy (authors vs ours)
    at = defaultdict(lambda:[0,0]); ot = defaultdict(lambda:[0,0])
    agree = {"both_right":[], "both_wrong":[], "only_authors":[], "only_ours":[]}
    for qid in ids:
        qt = authors[qid]["question_type"]
        a = bool(authors[qid]["gpt4o_correct"])
        at[qt][1]+=1; at[qt][0]+=int(a)
        if qid in ours:
            o = ours[qid]
            ot[qt][1]+=1; ot[qt][0]+=int(o)
            key = ("both_right" if a and o else "both_wrong" if not a and not o
                   else "only_authors" if a else "only_ours")
            agree[key].append(qid)
    aT=[sum(v[0] for v in at.values()), sum(v[1] for v in at.values())]
    oT=[sum(v[0] for v in ot.values()), sum(v[1] for v in ot.values())]

    # tokens / latency (ours)
    ctx = [t["context"].get("tokens") for t in traces.values() if t["context"].get("tokens")]
    rd  = [t["reader"].get("total_tokens") for t in traces.values()]
    ret = [t["search"].get("retrieve_time_ms") for t in traces.values()]
    ans = [t["reader"].get("generate_time_ms") for t in traces.values()]

    # authors' context/reader tokens restricted to the 60 (if their context_tokens.json present)
    a_ctx = a_rd = None
    actp = args.authors_run / "context_tokens.json"
    if actp.exists():
        rows = {r["question_id"]: r for r in json.loads(actp.read_text()).get("questions", [])}
        a_ctx = pct([rows[q].get("context_tokens") for q in ids if q in rows], .5)
        a_rd  = pct([rows[q].get("reader_total_tokens") for q in ids if q in rows], .5)

    have_ours = bool(ours)
    L = []
    L.append(f"# Authors (published) vs ours — same 60 questions\n")
    L.append("Authors = `caura-500-opaque-compact` restricted to our 60 ids (gpt-4o judge, "
             "`gpt-4o-2024-08-06`). Ours = this run.\n")
    L.append("## Accuracy per type (gpt-4o)\n")
    L.append("| type | authors | ours |")
    L.append("|---|---|---|")
    for qt in TYPES:
        a=at.get(qt,[0,0]); o=ot.get(qt,[0,0])
        L.append(f"| {qt} | {a[0]}/{a[1]} | {(str(o[0])+'/'+str(o[1])) if have_ours else '— pending'} |")
    L.append(f"| **overall** | **{aT[0]}/{aT[1]}** | **{(str(oT[0])+'/'+str(oT[1])) if have_ours else '— pending'}** |\n")

    L.append("## Per-question agreement\n")
    if have_ours:
        for k in ("both_right","both_wrong","only_authors","only_ours"):
            L.append(f"- **{k.replace('_',' ')}** ({len(agree[k])}): {', '.join(agree[k]) or '—'}")
    else:
        L.append("- pending our run")
    L.append("")

    L.append("## Reader & context tokens (median)\n")
    L.append("| metric | authors | ours |")
    L.append("|---|---|---|")
    L.append(f"| context tokens | {a_ctx if a_ctx is not None else 'not published'} | {pct(ctx,.5) if ctx else '— pending'} |")
    L.append(f"| reader total tokens | {a_rd if a_rd is not None else 'not published'} | {pct(rd,.5) if any(rd) else '— pending'} |\n")

    L.append("## Latency vs authors' published medians\n")
    L.append("| phase | authors (published) | ours (p50) |")
    L.append("|---|---|---|")
    L.append(f"| retrieval | {PUB_RET_MS/1000:.1f} s | {(pct(ret,.5)/1000 if pct(ret,.5) else None)} s |")
    L.append(f"| answering | {PUB_ANS_MS/1000:.1f} s | {(pct(ans,.5)/1000 if pct(ans,.5) else None)} s |\n")

    L.append("## Remaining differences from the authors' run\n")
    L.append("- **Embedder host:** same model (bge-m3, 1024d) but served on a GPU MIG slice SHARED with Mem0's qwen-tei (authors: dedicated GPU TEI). Vectors identical; only serving host differs.")
    L.append("- **Enrichment endpoint:** gemini-3.1-flash-lite via the Gemini API key (authors: same model via Vertex).")
    L.append("- **Scale:** 60 questions (the Mem0 subset) vs the authors' 500; authors' 60-subset numbers are a restriction of their published 500 run, not a fresh 60-only run.")
    L.append("- Everything else (reader gemini-3.8-flash, judges gpt-4o + gemini-3.5-flash-lite, all retrieval flags) matches the published headline.")

    (args.out/"compare_authors.md").write_text("\n".join(L)+"\n", encoding="utf-8")
    print(f"wrote {args.out/'compare_authors.md'}  (authors {aT[0]}/{aT[1]}, ours {'pending' if not have_ours else str(oT[0])+'/'+str(oT[1])})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
