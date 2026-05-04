from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


OPENALEX_BASE = "https://api.openalex.org"


@dataclass
class OpenAlexConfig:
    api_key: str | None = None
    requests_per_second: float = 10.0
    timeout_seconds: float = 30.0


class OpenAlexClient:
    def __init__(self, config: OpenAlexConfig | None = None) -> None:
        self.config = config or OpenAlexConfig()
        self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)
        self._lock = asyncio.Lock()
        self._last_request_ts = 0.0

    async def close(self) -> None:
        await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            min_interval = 1.0 / self.config.requests_per_second
            elapsed = now - self._last_request_ts
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_request_ts = time.monotonic()

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._throttle()
        merged = dict(params or {})
        if self.config.api_key:
            merged["api_key"] = self.config.api_key
        response = await self._client.get(f"{OPENALEX_BASE}{endpoint}", params=merged)
        response.raise_for_status()
        return response.json()

    async def list_works(
        self,
        search: str | None = None,
        filter_expr: str | None = None,
        per_page: int = 25,
        page: int = 1,
        select: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {
            "per_page": min(max(per_page, 1), 100),
            "page": max(page, 1),
        }
        if search:
            params["search"] = search
        if filter_expr:
            params["filter"] = filter_expr
        if select:
            params["select"] = select
        return await self._get("/works", params=params)

    async def get_work(self, work_id: str) -> dict[str, Any]:
        return await self._get(f"/works/{work_id}")

    async def list_topics(self, search: str | None = None, per_page: int = 25, page: int = 1) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": min(max(per_page, 1), 100), "page": max(page, 1)}
        if search:
            params["search"] = search
        return await self._get("/topics", params=params)

    async def list_concepts(self, search: str | None = None, per_page: int = 25, page: int = 1) -> dict[str, Any]:
        params: dict[str, Any] = {"per_page": min(max(per_page, 1), 100), "page": max(page, 1)}
        if search:
            params["search"] = search
        return await self._get("/concepts", params=params)
