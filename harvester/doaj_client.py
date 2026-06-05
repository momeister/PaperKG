from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx


DOAJ_BASE = "https://doaj.org/api/v2"


@dataclass
class DoajConfig:
    """DOAJ search API needs no key. Open-access journals across all disciplines."""

    base_url: str = DOAJ_BASE
    requests_per_second: float = 2.0
    timeout_seconds: float = 30.0


class DoajClient:
    """Async wrapper over the DOAJ article search API."""

    def __init__(self, config: DoajConfig | None = None) -> None:
        self.config = config or DoajConfig()
        self._client = httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            headers={"User-Agent": "ScienceKG/Phase5 (local-development)", "Accept": "application/json"},
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

    async def search_articles(self, query: str, page_size: int = 20) -> list[dict[str, Any]]:
        await self._throttle()
        # DOAJ takes the search term as a path segment; it must be URL-encoded.
        encoded = quote(query, safe="")
        params = {"pageSize": min(max(page_size, 1), 100)}
        response = await self._client.get(
            f"{self.config.base_url}/search/articles/{encoded}", params=params
        )
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("results", []))
