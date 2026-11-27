"""Build summary.md and compare_authors.md for the LoCoMo conv0 run (ours)."""

from __future__ import annotations

import json
import statistics
from pathlib import Path

from .dataset import CATEGORY_NAMES, SCORED_CATEGORIES


def _pct(c, n):
    return f"{100*c/n:.1f}%" if n else "n/a"


def _pctile(vals, q):
    if not vals:
        return 0.0
    s = sorted(vals)
    return s[min(len(s) - 1, int(q * len(s)))]


def _load_json(p: Path):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None


def _read_hyps(out: Path):
    hyp = out / "hypotheses.jsonl"
    rows = []
    if hyp.exists():
        for ln in hyp.read_text(encoding="utf-8").splitlines():
            if ln.strip():
                rows.append(json.loads(ln))
    return rows


def _acc_by_cat(verdicts: dict):
    """verdicts: qid -> {category, correct}. Returns (overall c,n, {cat:(c,n)})."""
    by = {c: [0, 0] for c in SCORED_CATEGORIES}
    oc = on = 0
    for v in verdicts.values():
        cat = v.get("category")
        by.setdefault(cat, [0, 0])
        by[cat][1] += 1
        on += 1
        if v.get("correct"):
            by[cat][0] += 1
            oc += 1
    return oc, on, by


def _price(prices, model):
    return (prices or {}).get(model, {})


def build_summary(out: Path, conv, prices_path: str | None = None):
    prices = _load_json(Path(prices_path)) if prices_path else None
    manifest = _load_json(out / "manifest.json") or {}
    g = _load_json(out / "eval_gemini3vote.json") or {"verdicts": {}}
    o = _load_json(out / "eval_gpt4o.json") or {"verdicts": {}}
    hyps = _read_hyps(out)
    store_stats = _load_json(out / "store_stats.json") or manifest.get("settle", {}).get("stats") or {}

    goc, gon, gby = _acc_by_cat(g["verdicts"])
    ooc, oon, oby = _acc_by_cat(o["verdicts"])

    # latency
    retr = [h["retrieve_time_ms"] for h in hyps if h.get("retrieve_time_ms") is not None]
    gen = [h["generate_time_ms"] for h in hyps if h.get("generate_time_ms") is not None]
    end2end = [a + b for a, b in zip(retr, gen)]

    # reader tokens (from hypotheses reader_usage)
    r_in = sum((h.get("reader_usage") or {}).get("prompt_tokens", 0) for h in hyps)
    r_out = sum((h.get("reader_usage") or {}).get("completion_tokens", 0) for h in hyps)
    ju = manifest.get("judge_usage", {})
    jg = ju.get("gemini", {}); jo = ju.get("gpt4o", {})

    def cost(model, tin, tout):
        pr = _price(prices, model)
        if not pr:
            return None
        return round(tin / 1e6 * pr.get("in", 0) + tout / 1e6 * pr.get("out", 0), 4)

    reader_cost = cost("gemini-3.8-flash", r_in, r_out)
    jg_cost = cost("gemini-3.8-flash", jg.get("prompt_tokens", 0), jg.get("completion_tokens", 0))
    jo_cost = cost("gpt-4o", jo.get("prompt_tokens", 0), jo.get("completion_tokens", 0))

    # failure labels (under headline gemini judge)
    hyp_by_id = {h["question_id"]: h for h in hyps}
    retrieval_bound, reader_bound, no_evidence = [], [], []
    for qid, v in g["verdicts"].items():
        if v.get("correct"):
            continue
        h = hyp_by_id.get(qid, {})
        ev = h.get("evidence") or []
        hit = h.get("evidence_hit")
        if not ev:
            no_evidence.append(qid)
        elif hit is False:
            retrieval_bound.append(qid)
        else:
            reader_bound.append(qid)

    L = []
    L.append("# Caura (MemClaw) on LoCoMo — conversation 0 (conv-26)\n")
    L.append(f"Store configuration not published for the authors' headline; we build our own store "
             f"from conv0 with the published parameters (chunk_characters=4000, top_k=30, judge_votes=3, "
             f"thinking_tokens=2048 on the reader). Agent `locomo-conv-{conv.conv_index}`, "
             f"min_similarity=0.0 (authors' caura-locomo provider), write_mode=strong.\n")

    L.append("## Accuracy\n")
    L.append("| judge | overall | multi-hop | temporal | open-domain | single-hop |")
    L.append("|---|---|---|---|---|---|")
    def cat_cells(by):
        return " | ".join(_pct(by[c][0], by[c][1]) for c in SCORED_CATEGORIES)
    L.append(f"| gemini-3.8-flash (3-vote, **headline**) | **{_pct(goc,gon)}** ({goc}/{gon}) | {cat_cells(gby)} |")
    L.append(f"| gpt-4o (single pass, second judge) | {_pct(ooc,oon)} ({ooc}/{oon}) | {cat_cells(oby)} |")
    L.append("")

    L.append("## Latency (ms)\n")
    L.append("| phase | p50 | p95 |")
    L.append("|---|---|---|")
    L.append(f"| retrieval | {_pctile(retr,0.5):.0f} | {_pctile(retr,0.95):.0f} |")
    L.append(f"| reader (agentic-v1) | {_pctile(gen,0.5):.0f} | {_pctile(gen,0.95):.0f} |")
    L.append(f"| end-to-end | {_pctile(end2end,0.5):.0f} | {_pctile(end2end,0.95):.0f} |")
    L.append("")

    L.append("## Tokens & cost per model / phase\n")
    L.append("Prices from `experiments/configs/prices.json` (placeholder Gemini rates — see note there). "
             "Server-side ingest LLMs (memory creation + entity extraction) are not token-metered by the "
             "harness; their cost is estimated separately in the handover.\n")
    L.append("| phase | model | provider | in tok | out tok | $ |")
    L.append("|---|---|---|---|---|---|")
    L.append(f"| reader | gemini-3.8-flash | Vertex | {r_in} | {r_out} | {reader_cost if reader_cost is not None else 'n/a'} |")
    L.append(f"| judge (headline, 3-vote) | gemini-3.8-flash | Vertex | {jg.get('prompt_tokens',0)} | {jg.get('completion_tokens',0)} | {jg_cost if jg_cost is not None else 'n/a'} |")
    L.append(f"| judge (second) | gpt-4o | OpenAI | {jo.get('prompt_tokens',0)} | {jo.get('completion_tokens',0)} | {jo_cost if jo_cost is not None else 'n/a'} |")
    L.append(f"| memory creation | gemini-3.1-flash-lite | Vertex | server-side | server-side | not metered |")
    L.append(f"| entity extraction | gemini-3.1-flash-lite | AI Studio | server-side | server-side | not metered |")
    L.append("")

    L.append("## Settled store stats\n")
    L.append("```json")
    L.append(json.dumps({
        "total": store_stats.get("total"),
        "by_type": store_stats.get("by_type"),
        "settled": store_stats.get("settled"),
        "pending": store_stats.get("pending"),
        "stranded": store_stats.get("stranded"),
        "embedding_configured": store_stats.get("embedding_configured"),
    }, indent=2))
    L.append("```")
    fc = manifest.get("frozen_check")
    if fc:
        L.append(f"\nFrozen-store check during answering: **{'CHANGED' if fc.get('changed') else 'unchanged'}** "
                 f"(before {fc['before']['count']}@{fc['before']['latest_updated_at']} / "
                 f"after {fc['after']['count']}@{fc['after']['latest_updated_at']}).")
    L.append("")

    L.append("## Failure labels (wrong under headline gemini 3-vote judge)\n")
    L.append(f"- **retrieval-bound** (gold evidence not in retrieved sessions): {len(retrieval_bound)} — {', '.join(retrieval_bound) or '—'}")
    L.append(f"- **reader-bound** (evidence retrieved but answer wrong): {len(reader_bound)} — {', '.join(reader_bound) or '—'}")
    L.append(f"- **no-annotated-evidence** (question has no resolvable evidence id): {len(no_evidence)} — {', '.join(no_evidence) or '—'}")
    L.append("")

    (out / "summary.md").write_text("\n".join(L), encoding="utf-8")

    # ---------------------------------------------------------------- compare_authors.md
    C = []
    C.append("# conv0 vs authors' full-set overall — LoCoMo\n")
    C.append(f"**Ours (this run):** gemini-3.8-flash 3-vote judge **{_pct(goc,gon)} ({goc}/{gon})** on "
             f"**conv 0 only (conv-26, {gon} scored questions, categories 1–4)**.\n")
    C.append("**Authors (EVIDENCE.md `locomo_caura_retrieval_accuracy_2026_09`):** "
             "**77.9% (1,199/1,540)** over **all 1,540 scored category 1–4 questions across all 10 conversations**.\n")
    C.append("This is an **independent rerun of the authors' pipeline family, not a reproduction**: their "
             "headline LoCoMo harness (`caura-ai/yanki-locomo`) and per-question results are **not public**, "
             "so the numbers are not directly comparable. Differences:\n")
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
    C.append("| reader/judge transport | not published | **Vertex AI** (our Google Cloud credits) |")
    C.append("| second judge | none | gpt-4o single pass (added, reported beside) |")
    C.append("| embedder | not published | BAAI/bge-m3 local GPU (1024-d) |")
    C.append("| reader temperature | not published | 0.0 |")
    C.append("| judge temperature | not published | 0.0 |")
    C.append("")
    (out / "compare_authors.md").write_text("\n".join(C), encoding="utf-8")
    print(f"wrote {out/'summary.md'} and {out/'compare_authors.md'}")
