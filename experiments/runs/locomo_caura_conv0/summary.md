# Caura (MemClaw) on LoCoMo — conversation 0 (conv-26)

Store configuration not published for the authors' headline; built from conv0 with the published parameters (chunk_characters=4000, top_k=30, judge_votes=3, thinking_tokens=2048 on the reader). Agent `conv-26`→`locomo-conv-0`, min_similarity=0.0, write_mode=strong.

## Accuracy (categories 1–4)

| judge | overall | multi-hop | temporal | open-domain | single-hop |
|---|---|---|---|---|---|
| **gemini-3.8-flash 3-vote (headline)** | **90.8%** (138/152) | 90.6% | 94.6% | 84.6% | 90.0% |
| gpt-4o (second judge) | 92.1% (140/152) | 90.6% | 97.3% | 92.3% | 90.0% |

Judge agreement (3-vote vs gpt-4o): **144/152** (94.7%).

## Latency (ms)
| phase | p50 | p95 |
|---|---|---|
| search | 67 | 1161 |
| answer (agentic-v1) | 8879 | 35212 |
| total (search+answer+judge) | 17679 | 56037 |

## Tokens & cost per phase

Gemini rates are the placeholder values in `prices.json`; ingest tokens are **derived** (see `ingest_trace.json`), not server-metered. `cost.md` in the results folder is the authoritative cost view.

| phase | model | provider | in | out | thinking | $ |
|---|---|---|---|---|---|---|
| memory building (derived) | gemini-3.1-flash-lite | Vertex | 25040 | 3450 | — | 0.0114 |
| entity extraction (derived) | gemini-3.1-flash-lite | AI Studio | 25040 | 3450 | — | 0.0114 |
| retrieval context | — | — | 2505643 | — | — | incl. in reader input |
| reader | gemini-3.8-flash | Vertex | 2906906 | 45207 | 10703 | 0.8106 |
| judge (headline 3-vote) | gemini-3.8-flash | Vertex | 206514 | 15441 | — | 0.0748 |
| judge (second) | gpt-4o | OpenAI | 67062 | 1070 | — | 0.1784 |

## Evidence coverage per category

Share of questions (that carry a resolvable evidence id) whose gold evidence turns were in the retrieved context.

| category | covered |
|---|---|
| multi-hop | 100.0% (31/31) |
| temporal | 100.0% (37/37) |
| open-domain | 100.0% (11/11) |
| single-hop | 100.0% (70/70) |

## Failure labels (wrong under headline 3-vote judge)

| label | count | question_ids |
|---|---|---|
| not_stored | 0 | — |
| stored_not_retrieved | 0 | — |
| retrieved_wrong_answer | 9 | conv0_q005_cat2, conv0_q059_cat3, conv0_q066_cat1, conv0_q112_cat4, conv0_q133_cat4, conv0_q140_cat4, conv0_q138_cat4, conv0_q144_cat4, conv0_q151_cat4 |
| judge_disagreement | 5 | conv0_q018_cat1, conv0_q028_cat2, conv0_q076_cat1, conv0_q077_cat3, conv0_q085_cat4 |

## Settled store
```json
{
  "total": 23,
  "by_type": {
    "episode": 6,
    "plan": 1,
    "preference": 4,
    "fact": 6,
    "task": 4,
    "decision": 2
  },
  "settled": true,
  "stranded": {
    "embedding": 0
  },
  "settle_time_s": 0.0
}
```

Frozen-store check during answering: **unchanged**.