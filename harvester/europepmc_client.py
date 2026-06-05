from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


EUROPEPMC_BASE = "https://www.ebi.ac.uk/europepmc/webservices/rest"


@dataclass
class EuropePMCConfig:
    """Europe PMC REST API needs no key. Covers PubMed/PMC + agricola/preprints."""

    base_url: str = EUROPEPMC_BASE
    requests_per_second: float = 5.0
    timeout_seconds: float = 30.0


class EuropePMCClient:
    """Async wrapper over the Europe PMC search API for biomedical literature."""

    def __init__(self, config: EuropePMCConfig | None = None) -> None:
        self.config = config or EuropePMCConfig()
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

    async def search(self, query: str, page_size: int = 20) -> list[dict[str, Any]]:
        await self._throttle()
        params = {
            "query": query,
            "format": "json",
            "pageSize": min(max(page_size, 1), 100),
            "resultType": "core",
        }
        response = await self._client.get(f"{self.config.base_url}/search", params=params)
        response.raise_for_status()
        payload = response.json()
        return list(payload.get("resultList", {}).get("result", []))
