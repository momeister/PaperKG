from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


BIORXIV_BASE = "https://api.biorxiv.org"


@dataclass
class BiorxivConfig:
    """bioRxiv/medRxiv native API. No key.

    Note: the native API is date-/DOI-based and has no keyword search. Topic search
    for preprints is served via Europe PMC's preprint filter (see api/product_main.py).
    This client covers DOI->fulltext resolution and recent-preprint listing.
    """

    base_url: str = BIORXIV_BASE
    requests_per_second: float = 2.0
    timeout_seconds: float = 30.0


class BiorxivClient:
    """Async wrapper over the native bioRxiv/medRxiv API."""

    def __init__(self, config: BiorxivConfig | None = None) -> None:
        self.config = config or BiorxivConfig()
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

    async def content_detail(self, doi: str, server: str = "biorxiv") -> dict[str, Any] | None:
        """Resolve a bioRxiv/medRxiv DOI to its metadata record (latest version)."""
        await self._throttle()
        response = await self._client.get(f"{self.config.base_url}/details/{server}/{doi}")
        response.raise_for_status()
        collection = response.json().get("collection", [])
        return collection[-1] if collection else None

    async def recent(self, server: str = "biorxiv", days: int = 30, cursor: int = 0) -> list[dict[str, Any]]:
        """List recently posted preprints for the given server within the day window."""
        await self._throttle()
        interval = f"{max(days, 1)}d"
        response = await self._client.get(
            f"{self.config.base_url}/details/{server}/{interval}/{cursor}"
        )
        response.raise_for_status()
        return list(response.json().get("collection", []))

    @staticmethod
    def pdf_url(doi: str) -> str:
        return f"https://www.biorxiv.org/content/{doi}v1.full.pdf"
