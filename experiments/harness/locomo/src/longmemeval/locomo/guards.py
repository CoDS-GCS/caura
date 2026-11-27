"""Runtime safeguards for the LoCoMo answer/judge loops (ours).

Two tripwires, each leaves the run resumable and writes ``STOPPED`` to the run
folder with the reason and the affected questions:

  * FailStreak   — stop after N (default 3) consecutive question failures
                   (any error: search, reader, judge), after their in-call retries.
  * ContainerWatch — before each question, DB + core-api must be healthy and must
                   not have restarted since the stage began.

Test hooks (free, no paid calls): LOCOMO_FORCE_FAIL, LOCOMO_FORCE_RESTART —
see run_locomo.sh self-test.
"""

from __future__ import annotations

import datetime
import json
import os
import subprocess
import threading
from pathlib import Path

GUARD_EXIT_CODE = 6  # bash recognises this + the STOPPED file as a clean, resumable stop
WATCH_CONTAINERS = ("caura-db-1", "caura-core-api-1")


class GuardStop(Exception):
    def __init__(self, reason: str, affected: list[str] | None = None):
        super().__init__(reason)
        self.reason = reason
        self.affected = affected or []


def _inspect(fmt: str, name: str) -> str:
    try:
        r = subprocess.run(["docker", "inspect", "-f", fmt, name],
                           capture_output=True, text=True, timeout=10)
        return r.stdout.strip()
    except Exception:
        return ""


class ContainerWatch:
    """Baseline the db + core-api container start time; detect restart / unhealth."""

    def __init__(self):
        self.baseline = {c: _inspect("{{.State.StartedAt}}", c) for c in WATCH_CONTAINERS}

    def restarted(self) -> list[str]:
        if os.environ.get("LOCOMO_FORCE_RESTART"):
            return ["caura-db-1 (LOCOMO_FORCE_RESTART test)"]
        out = []
        for c in WATCH_CONTAINERS:
            now = _inspect("{{.State.StartedAt}}", c)
            base = self.baseline.get(c)
            if now and base and now != base:
                out.append(f"{c} (was {base}, now {now})")
        return out

    def unhealthy(self) -> list[str]:
        bad = []
        for c in WATCH_CONTAINERS:
            status = _inspect("{{.State.Status}}", c)
            health = _inspect("{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}", c)
            if status != "running" or health not in ("healthy", "none"):
                bad.append(f"{c}:{status}/{health}")
        return bad

    def check(self) -> list[str]:
        return self.restarted() or self.unhealthy()


class FailStreak:
    def __init__(self, limit: int = 3):
        self.limit = limit
        self.streak = 0
        self.lock = threading.Lock()

    def ok(self) -> None:
        with self.lock:
            self.streak = 0

    def fail(self) -> int:
        with self.lock:
            self.streak += 1
            return self.streak

    def tripped(self) -> bool:
        return self.streak >= self.limit


def write_stopped(out: Path, reason: str, affected: list[str] | None = None) -> None:
    (out / "STOPPED").write_text(json.dumps({
        "reason": reason,
        "affected_questions": affected or [],
        "n_affected": len(affected or []),
        "at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
        "resume": "bash experiments/scripts/run_locomo.sh --resume",
    }, indent=2), encoding="utf-8")
