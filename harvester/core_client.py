from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


CORE_BASE = "https://api.core.ac.uk/v3"


class CoreApiKeyMissing(RuntimeError):
    """Raised when a CORE search is attempted without an API key configured."""


@dataclass
class CoreConfig:
    """CORE v3 API requires a free API key (env CORE_API_KEY)."""

    api_key: str | None = None
    base_url: str = CORE_BASE
    requests_per_second: float = 1.0
    timeout_seconds: float = 45.0


class CoreClient:
    """Async wrapper over the CORE v3 search API (260M+ open-access full texts)."""

    def __init__(self, config: CoreConfig | None = None) -> None:
        self.config = config or CoreConfig()
        headers = {"User-Agent": "ScienceKG/Phase5 (local-development)", "Accept": "application/json"}
        if self.config.api_key:
            headers["Authorization"] = f"Bearer {self.config.api_key}"
        self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds, headers=headers)
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

    async def search_works(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not self.config.api_key:
            raise CoreApiKeyMissing(
                "CORE requires a free API key. Set CORE_API_KEY in your environment / .env."
            )
        await self._throttle()
        params = {"q": query, "limit": min(max(limit, 1), 100)}
        response = await self._client.get(f"{self.config.base_url}/search/works", params=params)
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("results", []))
