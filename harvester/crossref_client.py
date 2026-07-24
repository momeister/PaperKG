from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


CROSSREF_BASE = "https://api.crossref.org"


@dataclass
class CrossrefConfig:
    """Crossref REST API needs no key. Supplying a contact mail joins the polite pool."""

    mailto: str | None = None
    base_url: str = CROSSREF_BASE
    requests_per_second: float = 5.0
    timeout_seconds: float = 30.0


class CrossrefClient:
    """Thin async wrapper over the Crossref REST API.

    Covers every discipline (incl. law/economics) via DOI metadata and supports
    bibliographic reference matching used by the reference-download workflow.
    """

    def __init__(self, config: CrossrefConfig | None = None) -> None:
        self.config = config or CrossrefConfig()
        user_agent = "ScienceKG/Phase5 (https://github.com/local; mailto:%s)" % (
            self.config.mailto or "local-development"
        )
        self._client = httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": user_agent, "Accept": "application/json"},
        )
        self._lock = asyncio.Lock()
        self._last_request_ts = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            min_interval = 1.0 / max(self.config.requests_per_second, 0.1)
            elapsed = now - self._last_request_ts
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_request_ts = time.monotonic()

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._throttle()
        merged = dict(params or {})
        if self.config.mailto:
            merged.setdefault("mailto", self.config.mailto)
        response = await self._client.get(f"{self.config.base_url}{endpoint}", params=merged)
        response.raise_for_status()
        return response.json()

    async def search_works(self, query: str, rows: int = 20, filters: str | None = None) -> list[dict[str, Any]]:
        """Bibliografische Suche; ``filters`` ist Crossrefs ``filter``-Parameter
        (z. B. ``prefix:10.31219``, um auf OSF-Preprints einzugrenzen)."""
        params: dict[str, Any] = {"query.bibliographic": query, "rows": min(max(rows, 1), 100)}
        if filters:
            params["filter"] = filters
        payload = await self._get("/works", params=params)
        return list(payload.get("message", {}).get("items", []))

    async def match_reference(self, reference: str) -> dict[str, Any] | None:
        """Return the single best Crossref work for a free-text reference string."""
        if not reference.strip():
            return None
        params = {"query.bibliographic": reference, "rows": 1}
        payload = await self._get("/works", params=params)
        items = payload.get("message", {}).get("items", [])
        return items[0] if items else None
