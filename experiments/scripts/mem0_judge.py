#!/usr/bin/env python3
"""Judge a LongMemEval hypotheses.jsonl with Mem0's reference judge, for an
apples-to-apples Mem0 comparison.

Mem0's judge differs from the harness's gpt-4o judge in BOTH model (gpt-5) and
prompt (a semantic-equivalence yes/no prompt with a <judge_thinking> block). We
replicate it exactly here: the prompt is the verbatim copy committed at
experiments/configs/mem0_judge_prompt.txt, and the model/temperature match
Mem0's answerer/judge path (gpt-5, temperature param omitted — gpt-5 only
accepts its default).

Usage:
  uv run python experiments/scripts/mem0_judge.py <hypotheses.jsonl> \
      -o <eval_mem0judge.json> [--model gpt-5] [--concurrency 5]

Reads OPENAI_API_KEY from the environment (the harness .env is already loaded by
the caller / run_lme.sh). Prints the accuracy and per-type breakdown.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

PROMPT_PATH = Path(__file__).resolve().parents[1] / "configs" / "mem0_judge_prompt.txt"


def build_prompt(template: str, question: str, answer: str, response: str) -> str:
    # Mem0 formats with {question} {answer} {response}; question_date is unused here.
    return template.format(question=question, answer=str(answer), response=response)


def parse_verdict(text: str) -> bool:
    """Mem0 reads the final 'yes'/'no' after the <judge_thinking> block."""
    t = text.strip()
    # drop the thinking block if present
    t = re.sub(r"<judge_thinking>.*?</judge_thinking>", "", t, flags=re.DOTALL | re.IGNORECASE)
    tail = t.strip().lower()
    # last non-empty line decides
    for line in reversed([l.strip() for l in tail.splitlines() if l.strip()]):
        if line.startswith("yes"):
            return True
        if line.startswith("no"):
            return False
    return "yes" in tail and "no" not in tail


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("hypotheses", type=Path)
    ap.add_argument("-o", "--output", type=Path, required=True)
    ap.add_argument("--model", default="gpt-5")
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        print("OPENAI_API_KEY not set", file=sys.stderr)
        return 2

    from openai import OpenAI

    client = OpenAI(api_key=api_key)
    template = PROMPT_PATH.read_text(encoding="utf-8")

    rows = [json.loads(l) for l in args.hypotheses.read_text(encoding="utf-8").splitlines() if l.strip()]

    def judge(row: dict) -> dict:
        prompt = build_prompt(template, row.get("question", ""), row.get("answer", ""), row.get("hypothesis", ""))
        # gpt-5 only accepts the default temperature (1) -> omit the param (matches Mem0's llm_client).
        resp = client.chat.completions.create(
            model=args.model,
            messages=[{"role": "user", "content": prompt}],
        )
        raw = resp.choices[0].message.content or ""
        return {
            "question_id": row.get("question_id"),
            "question_type": row.get("question_type"),
            "correct": parse_verdict(raw),
            "judge_raw": raw[:500],
        }

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        results = list(ex.map(judge, rows))

    by_type: dict[str, list[int]] = {}
    correct = 0
    for r in results:
        qt = r["question_type"]
        by_type.setdefault(qt, [0, 0])
        by_type[qt][1] += 1
        if r["correct"]:
            by_type[qt][0] += 1
            correct += 1
    n = len(results)
    out = {
        "judge": "mem0-reference",
        "judge_model": args.model,
        "judge_prompt_file": str(PROMPT_PATH),
        "n": n,
        "accuracy": (correct / n) if n else 0.0,
        "by_type": {k: {"correct": v[0], "total": v[1]} for k, v in sorted(by_type.items())},
        "results": results,
    }
    args.output.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"Mem0-reference judge ({args.model}): {correct}/{n} = {correct/n*100:.1f}%" if n else "no rows")
    for k, v in sorted(by_type.items()):
        print(f"  {k}: {v[0]}/{v[1]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
