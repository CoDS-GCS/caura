"""LoCoMo loader (ours).

Loads ``locomo10.json`` and turns one conversation into:
  * a list of session-granularity memories chunked at ``chunk_characters`` (4000,
    the authors' published value), each under a dated ``[Session N — <date>]``
    header carrying the ``dia_id``s it contains (for evidence scoring), and
  * the scored category 1-4 questions (cat 5 excluded).

Field handling (``session_keys``, ``parse_session_datetime``, speaker names,
``[Session N — <date>]`` header, dia_id evidence map) is adapted from the authors'
public ``caura-ai/caura-locomo`` ``src/locomo/dataset.py``.
"""

from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

# LoCoMo category id -> name. Matches scripts/_locomo_bench.py CATEGORIES and the
# EVIDENCE.md scope (cats 1-4 scored; cat 5 adversarial excluded).
CATEGORY_NAMES = {1: "multi-hop", 2: "temporal", 3: "open-domain", 4: "single-hop"}
SCORED_CATEGORIES = tuple(CATEGORY_NAMES)  # (1, 2, 3, 4)
DEFAULT_CHUNK_CHARS = 4000


@dataclass
class SessionChunk:
    """One stored memory: a dated session slice <= chunk_chars."""
    session_idx: int
    session_date: str            # raw LoCoMo string, e.g. "1:56 pm on 8 May, 2023"
    session_date_iso: str | None  # ISO 8601 or None if unparseable
    part: int                    # 0-based part index within the session
    n_parts: int
    dia_ids: list[str]
    content: str                 # header + "speaker: text" lines


@dataclass
class LocomoQuestion:
    question_id: str
    question: str
    answer: str
    category: int
    category_name: str
    evidence: list[str]          # dia_ids present in the conversation


@dataclass
class LocomoConversation:
    sample_id: str               # e.g. "conv-26"
    conv_index: int              # index in locomo10.json (conv 0 == index 0)
    chunks: list[SessionChunk]
    questions: list[LocomoQuestion]
    # dia_id -> session index, and session index -> raw date, for scoring/traces.
    dia_to_session: dict[str, int] = field(default_factory=dict)
    session_dates: dict[int, str] = field(default_factory=dict)

    @property
    def n_sessions(self) -> int:
        return len(self.session_dates)

    @property
    def n_turns(self) -> int:
        return len(self.dia_to_session)


def parse_session_datetime(raw: str) -> str | None:
    """'1:56 pm on 8 May, 2023' -> ISO 8601 (adapted from caura-locomo)."""
    m = re.match(r"(\d{1,2}):(\d{2})\s*(am|pm)\s+on\s+(\d{1,2})\s+(\w+),?\s+(\d{4})", raw or "", re.I)
    if not m:
        return None
    hh, mm, ampm, dd, month_name, yyyy = m.groups()
    hh = int(hh)
    if ampm.lower() == "pm" and hh != 12:
        hh += 12
    elif ampm.lower() == "am" and hh == 12:
        hh = 0
    months = {n: i + 1 for i, n in enumerate(
        ["january", "february", "march", "april", "may", "june", "july",
         "august", "september", "october", "november", "december"])}
    mon = months.get(month_name.lower())
    return f"{yyyy}-{mon:02d}-{int(dd):02d}T{hh:02d}:{int(mm):02d}:00Z" if mon else None


def session_keys(conversation: dict) -> list[str]:
    """Ordered session_N keys (adapted from caura-locomo)."""
    return sorted(
        [k for k in conversation if k.startswith("session_") and "_date_time" not in k],
        key=lambda x: int(x.split("_")[1]),
    )


def _chunk_session(idx: int, raw_date: str, turns: list[dict], chunk_chars: int) -> list[SessionChunk]:
    """Split one session into <= chunk_chars parts without cutting a turn.

    Every part repeats the dated header so a retrieved slice is self-describing
    and temporal questions can resolve relative dates against it.
    """
    header = f"[Session {idx} — {raw_date}]"
    iso = parse_session_datetime(raw_date)
    lines = [(t["dia_id"], f"{t.get('speaker', '?')}: {t['text']}") for t in turns if t.get("dia_id") and t.get("text")]
    parts: list[tuple[list[str], list[str]]] = []  # (dia_ids, text_lines)
    cur_ids: list[str] = []
    cur_lines: list[str] = []
    cur_len = len(header)
    for dia_id, line in lines:
        add = len(line) + 2  # "\n\n"
        if cur_lines and cur_len + add > chunk_chars:
            parts.append((cur_ids, cur_lines))
            cur_ids, cur_lines, cur_len = [], [], len(header)
        cur_ids.append(dia_id)
        cur_lines.append(line)
        cur_len += add
    if cur_lines:
        parts.append((cur_ids, cur_lines))
    if not parts:  # empty session (shouldn't happen) — keep a header-only marker out
        return []
    out: list[SessionChunk] = []
    n = len(parts)
    for p, (ids, body_lines) in enumerate(parts):
        content = header + "\n" + "\n\n".join(body_lines)
        out.append(SessionChunk(
            session_idx=idx, session_date=raw_date, session_date_iso=iso,
            part=p, n_parts=n, dia_ids=ids, content=content[:chunk_chars + len(header) + 2],
        ))
    return out


def _resolve_evidence(raw_evidence: object, present: set[str]) -> list[str]:
    """LoCoMo stores evidence as a str repr of a list; keep only ids that exist."""
    ev = raw_evidence
    if isinstance(ev, str):
        try:
            ev = ast.literal_eval(ev)
        except (ValueError, SyntaxError):
            ev = None
    if not isinstance(ev, (list, tuple)):
        ev = []
    return [e for e in ev if e in present]


def load_conversation(
    data_path: str | Path,
    conv_index: int = 0,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
) -> LocomoConversation:
    """Load one LoCoMo conversation (default conv 0 == index 0 == conv-26)."""
    raw = json.loads(Path(data_path).read_text(encoding="utf-8"))
    if conv_index >= len(raw):
        raise IndexError(f"conv_index {conv_index} out of range ({len(raw)} conversations)")
    sample = raw[conv_index]
    conv = sample["conversation"]

    chunks: list[SessionChunk] = []
    dia_to_session: dict[str, int] = {}
    session_dates: dict[int, str] = {}
    for key in session_keys(conv):
        idx = int(key.split("_")[1])
        raw_date = conv.get(f"{key}_date_time", "")
        session_dates[idx] = raw_date
        turns = conv[key]
        for t in turns:
            if t.get("dia_id"):
                dia_to_session[t["dia_id"]] = idx
        chunks.extend(_chunk_session(idx, raw_date, turns, chunk_chars))

    present = set(dia_to_session)
    questions: list[LocomoQuestion] = []
    q_idx = 0
    for qa in sample["qa"]:
        try:
            cat = int(qa.get("category"))
        except (TypeError, ValueError):
            continue
        if cat not in CATEGORY_NAMES:   # cat 5 (adversarial) and any stray excluded
            continue
        ans = qa.get("answer", "")
        questions.append(LocomoQuestion(
            question_id=f"conv{conv_index}_q{q_idx:03d}_cat{cat}",
            question=str(qa.get("question", "")),
            answer="" if ans is None else str(ans),
            category=cat,
            category_name=CATEGORY_NAMES[cat],
            evidence=_resolve_evidence(qa.get("evidence"), present),
        ))
        q_idx += 1

    return LocomoConversation(
        sample_id=sample.get("sample_id", f"index-{conv_index}"),
        conv_index=conv_index,
        chunks=chunks,
        questions=questions,
        dia_to_session=dia_to_session,
        session_dates=session_dates,
    )


def stats(conv: LocomoConversation) -> dict:
    from collections import Counter
    by_cat = Counter(q.category for q in conv.questions)
    return {
        "sample_id": conv.sample_id,
        "conv_index": conv.conv_index,
        "sessions": conv.n_sessions,
        "turns": conv.n_turns,
        "memories": len(conv.chunks),
        "scored_questions": len(conv.questions),
        "by_category": {CATEGORY_NAMES[c]: by_cat.get(c, 0) for c in SCORED_CATEGORIES},
    }
