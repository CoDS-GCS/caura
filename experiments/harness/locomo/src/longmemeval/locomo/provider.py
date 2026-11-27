"""Conversation-scoped Caura provider for LoCoMo (ours).

Differs from the LongMemEval ``providers/caura.py`` in exactly one way: the
ingest AND search unit is the whole CONVERSATION (one agent, ``locomo-conv-0``),
not one agent per question — so every question of a conversation searches only
that conversation's sessions. Agent-scoping idea and ``min_similarity=0.0``
floor are taken from the authors' public ``caura-ai/caura-locomo`` provider.

HTTP robustness (retry/backoff, raise-not-empty on recall failure) mirrors the
LongMemEval provider's reliability patch: a ``/search`` 5xx/429 that survives
retries raises :class:`CauraRecallError` instead of returning ``[]`` (an empty
recall would make the reader answer blind and be scored wrong).
"""

from __future__ import annotations

import os
import threading
import time
import uuid

import httpx


class CauraRecallError(RuntimeError):
    """A /search call failed with a server error that survived retries."""


DEFAULT_BASE_URL = "http://localhost:8000/api/v1"
MAX_SEARCH_TOP_K = 200
BULK_MAX_ITEMS = 100
_BULK_MIN_INTERVAL_S = 0.55


class LocomoCauraProvider:
    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str | None = None,
        tenant_id: str | None = None,
        agent_prefix: str = "locomo-conv",
        top_k: int = 30,
        min_similarity: float = 0.0,   # authors: tenant default 0.3 discards LoCoMo paraphrases
        write_mode: str = "strong",    # authors' public provider default; embeds inline
        raw_only: bool = False,        # chars mode allows server-derived memories in context
        bulk_size: int = 50,
    ):
        self.base_url = (base_url or os.environ.get("CAURA_BASE_URL", DEFAULT_BASE_URL)).rstrip("/")
        self.api_key = api_key or os.environ.get("CAURA_API_KEY", "standalone")
        self._tenant_id = tenant_id or os.environ.get("CAURA_TENANT_ID", "default")
        self.agent_prefix = agent_prefix
        self.top_k = min(int(top_k), MAX_SEARCH_TOP_K)
        self.min_similarity = float(min_similarity)
        self.write_mode = write_mode
        self.raw_only = raw_only
        self.bulk_size = min(int(bulk_size), BULK_MAX_ITEMS)
        self._client: httpx.Client | None = None
        self._lock = threading.Lock()
        self._last_bulk_at = 0.0
        self.run_id = f"locomo-{uuid.uuid4().hex[:8]}"

    # ---------------------------------------------------------------- http
    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(
                base_url=self.base_url,
                headers={"X-API-Key": self.api_key, "Content-Type": "application/json"},
                timeout=httpx.Timeout(180.0, connect=30.0),
            )
        return self._client

    def _request(self, method: str, path: str, *, retries: int = 4, max_delay: float = 30.0, **kwargs) -> httpx.Response:
        delay = 1.0
        last: httpx.Response | None = None
        for attempt in range(retries + 1):
            try:
                resp = self._http().request(method, path, **kwargs)
            except httpx.RequestError:
                if attempt == retries:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, max_delay)
                continue
            if resp.status_code == 429 or resp.status_code >= 500:
                last = resp
                if attempt == retries:
                    break
                ra = resp.headers.get("Retry-After")
                wait = float(ra) if ra and ra.isdigit() else delay
                time.sleep(min(wait, max_delay))
                delay = min(delay * 2, max_delay)
                continue
            return resp
        assert last is not None
        return last

    @property
    def tenant_id(self) -> str:
        return self._tenant_id

    def agent_id(self, unit_id: str) -> str:
        return f"{self.agent_prefix}-{unit_id}"

    # ---------------------------------------------------------------- ingest
    def reset(self, unit_id: str) -> None:
        agent = self.agent_id(unit_id)
        resp = self._request("DELETE", "/memories", params={
            "tenant_id": self.tenant_id, "agent_id": agent, "fleet_id": agent,
        })
        if resp.status_code not in (200, 204, 404):
            raise RuntimeError(f"reset {agent} -> {resp.status_code}: {resp.text[:200]}")

    def _bulk_write(self, agent: str, items: list[dict]) -> int:
        with self._lock:
            gap = time.monotonic() - self._last_bulk_at
            if gap < _BULK_MIN_INTERVAL_S:
                time.sleep(_BULK_MIN_INTERVAL_S - gap)
            self._last_bulk_at = time.monotonic()
        resp = self._request(
            "POST", "/memories/bulk", retries=6,
            params={"mode": self.write_mode},
            json={"tenant_id": self.tenant_id, "agent_id": agent, "fleet_id": agent, "items": items},
            headers={"X-Bulk-Attempt-Id": f"{self.run_id}-{uuid.uuid4().hex[:8]}"},
        )
        if resp.status_code not in (200, 207):
            raise RuntimeError(f"bulk write failed [{resp.status_code}]: {resp.text[:400]}")
        return resp.json().get("created", 0)

    def ingest_chunks(self, unit_id: str, chunks: list) -> int:
        """Store the conversation's session chunks under one agent. chunks: list[SessionChunk]."""
        agent = self.agent_id(unit_id)
        created = 0
        batch: list[dict] = []
        for i, c in enumerate(chunks):
            batch.append({
                "content": c.content,
                "ts_valid_start": c.session_date_iso,
                "write_mode": self.write_mode,   # per-item: embed inline (searchable at write time)
                "metadata": {
                    "doc_id": f"{agent}_s{c.session_idx}_p{c.part}",
                    "session_idx": c.session_idx,
                    "session_date": c.session_date,
                    "part": c.part,
                    "dia_ids": c.dia_ids,
                },
            })
            if len(batch) >= self.bulk_size:
                created += self._bulk_write(agent, batch)
                batch = []
        if batch:
            created += self._bulk_write(agent, batch)
        return created

    # ---------------------------------------------------------------- search
    def search(self, unit_id: str, query: str) -> tuple[list[dict], dict]:
        """Return (ranked items, retrieval_stats). Raises CauraRecallError on failure."""
        agent = self.agent_id(unit_id)
        resp = self._request(
            "POST", "/search", retries=8, max_delay=30.0,
            json={
                "tenant_id": self.tenant_id,
                "query": query[:5000],
                "filter_agent_id": agent,
                "top_k": self.top_k,
                "min_similarity": self.min_similarity,
            },
        )
        if resp.status_code != 200:
            raise CauraRecallError(f"/search {agent} -> {resp.status_code}: {resp.text[:300]}")
        body = resp.json()
        items = body.get("items") or []
        # Exclude server-injected successor rows (score null) and, if raw_only,
        # server-derived memories (no metadata.doc_id).
        kept: list[dict] = []
        derived = 0
        for it in items:
            if it.get("injected"):
                continue
            has_doc = bool((it.get("metadata") or {}).get("doc_id"))
            if not has_doc:
                derived += 1
                if self.raw_only:
                    continue
            kept.append(it)
        stats = {
            "search_candidates": len(kept),
            "returned": len(items),
            "derived_in_pool": derived,
            "top_k": self.top_k,
            "min_similarity": self.min_similarity,
        }
        return kept, stats

    @staticmethod
    def build_context(items: list[dict]) -> str:
        """Retrieved memory contents in rank order, separated for the agentic reader.

        The agentic pipeline's map-reduce splitter breaks on ``\\n---\\n``, so we
        use the same separator. Content already carries ``[Session N — <date>]``.
        """
        blocks = [str(it.get("content") or "").strip() for it in items]
        return "\n---\n".join(b for b in blocks if b)

    # ---------------------------------------------------------------- stats / settle / frozen
    def stats(self, unit_id: str) -> dict:
        agent = self.agent_id(unit_id)
        resp = self._request("GET", "/memories/stats", params={"tenant_id": self.tenant_id, "agent_id": agent})
        resp.raise_for_status()
        return resp.json()

    def frozen_snapshot(self, unit_id: str) -> dict:
        """Count + latest updated_at for this agent — compared before/after answering."""
        agent = self.agent_id(unit_id)
        total = 0
        latest = ""
        cursor = None
        while True:
            params = {"tenant_id": self.tenant_id, "agent_id": agent, "limit": 200}
            if cursor:
                params["cursor"] = cursor
            resp = self._request("GET", "/memories", params=params)
            resp.raise_for_status()
            body = resp.json()
            items = body.get("items") or []
            total += len(items)
            for it in items:
                u = str(it.get("updated_at") or it.get("created_at") or "")
                if u > latest:
                    latest = u
            cursor = body.get("next_cursor") or body.get("cursor")
            if not cursor or not items:
                break
        return {"agent_id": agent, "count": total, "latest_updated_at": latest}

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None
