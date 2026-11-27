"""LoCoMo judges (ours).

Headline judge: ``gemini-3.8-flash`` with the mem0 prompt, **3 seeded votes,
majority** (authors' published ``judge_votes: 3`` / "three seeded votes per
frozen answer"). Judge temperature is NOT published; we use 0.0 and record the
three seeds, so the vote is reproducible and the manifest states the gap.

Second judge: ``gpt-4o``, single pass, same mem0 prompt — for consistency with
our LongMemEval and Mem0 runs; reported beside (never instead of) the headline.
"""

from __future__ import annotations

import json
import re
from typing import Any

from .prompts import JUDGE_PROMPT

# Default seeds for the three gemini votes. Fixed so a rerun reproduces them.
DEFAULT_SEEDS = (7, 13, 42)


def _parse_label(raw: str) -> str:
    """Extract CORRECT / WRONG from the judge's reply (JSON {"label": ...} first)."""
    if not raw:
        return "WRONG"
    m = re.search(r"\{.*\}", raw, flags=re.DOTALL)
    if m:
        try:
            v = json.loads(m.group(0)).get("label", "")
            if isinstance(v, str) and v.strip().upper() in ("CORRECT", "WRONG"):
                return v.strip().upper()
        except json.JSONDecodeError:
            pass
    up = raw.upper()
    has_c, has_w = "CORRECT" in up, "WRONG" in up
    if has_c and not has_w:
        return "CORRECT"
    if has_w and not has_c:
        return "WRONG"
    return "WRONG"


class LocomoJudge:
    def __init__(self, gemini_llm, openai_llm, seeds: tuple[int, ...] = DEFAULT_SEEDS):
        self.g = gemini_llm       # GeminiLLM (Vertex), model gemini-3.8-flash
        self.o = openai_llm       # OpenAILLM, model gpt-4o
        self.seeds = seeds
        # token accounting per judge, summed across all calls
        self.usage = {
            "gemini": {"model": getattr(gemini_llm, "model_name", None), "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "n_calls": 0},
            "gpt4o": {"model": getattr(openai_llm, "model_name", None), "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "n_calls": 0},
        }

    # ------------------------------------------------------------ gemini 3-vote
    def _gemini_vote(self, prompt: str, seed: int) -> tuple[str, str]:
        types = self.g.types
        cfg_kwargs: dict = dict(
            temperature=0.0,
            max_output_tokens=256,
            seed=seed,
            response_mime_type="application/json",
            automatic_function_calling=types.AutomaticFunctionCallingConfig(disable=True),
        )
        resp = self.g.client.models.generate_content(
            model=self.g.model_name, contents=prompt,
            config=types.GenerateContentConfig(**cfg_kwargs),
        )
        resolved = getattr(resp, "model_version", None)
        if resolved:
            self.g.resolved_model = resolved
        um = getattr(resp, "usage_metadata", None)
        if um is not None:
            u = self.usage["gemini"]
            u["prompt_tokens"] += int(getattr(um, "prompt_token_count", 0) or 0)
            u["completion_tokens"] += int(getattr(um, "candidates_token_count", 0) or 0)
            u["total_tokens"] += int(getattr(um, "total_token_count", 0) or 0)
            u["n_calls"] += 1
        raw = resp.text or ""
        return _parse_label(raw), raw

    def judge_gemini(self, question: str, gold: str, generated: str) -> dict[str, Any]:
        prompt = JUDGE_PROMPT.format(question=question, gold_answer=gold, generated_answer=generated)
        votes: list[str] = []
        raws: list[str] = []
        for s in self.seeds:
            label, raw = self._gemini_vote(prompt, s)
            votes.append(label)
            raws.append(raw)
        n_correct = votes.count("CORRECT")
        majority = "CORRECT" if n_correct >= (len(self.seeds) // 2 + 1) else "WRONG"
        return {
            "label": majority,
            "correct": majority == "CORRECT",
            "votes": votes,
            "seeds": list(self.seeds),
            "n_correct": n_correct,
            "explanations": raws,
        }

    # ------------------------------------------------------------ gpt-4o single
    def judge_gpt4o(self, question: str, gold: str, generated: str) -> dict[str, Any]:
        prompt = JUDGE_PROMPT.format(question=question, gold_answer=gold, generated_answer=generated)
        resp = self.o.client.chat.completions.create(
            model=self.o.model_name,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.0,
            max_tokens=256,
            response_format={"type": "json_object"},
        )
        resolved = getattr(resp, "model", None)
        if resolved:
            self.o.resolved_model = resolved
        usage = getattr(resp, "usage", None)
        if usage is not None:
            u = self.usage["gpt4o"]
            u["prompt_tokens"] += int(getattr(usage, "prompt_tokens", 0) or 0)
            u["completion_tokens"] += int(getattr(usage, "completion_tokens", 0) or 0)
            u["total_tokens"] += int(getattr(usage, "total_tokens", 0) or 0)
            u["n_calls"] += 1
        raw = resp.choices[0].message.content or ""
        label = _parse_label(raw)
        return {"label": label, "correct": label == "CORRECT", "explanation": raw}
