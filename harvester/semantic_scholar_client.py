from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


S2_GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
S2_RECOMMEND_BASE = "https://api.semanticscholar.org/recommendations/v1"


@dataclass
class SemanticScholarConfig:
    api_key: str | None = None
    requests_per_second: float = 10.0
    timeout_seconds: float = 30.0


class SemanticScholarClient:
    def __init__(self, config: SemanticScholarConfig | None = None) -> None:
        self.config = config or SemanticScholarConfig()
        headers: dict[str, str] = {}
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
        self._client = httpx.AsyncClient(headers=headers, timeout=self.config.timeout_seconds)
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

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._throttle()
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def _post(self, url: str, payload: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
        await self._throttle()
        response = await self._client.post(url, json=payload, params=params)
        response.raise_for_status()
        return response.json()

    async def search_papers(
        self,
        query: str,
        limit: int = 50,
        offset: int = 0,
        fields: str | None = None,
        year: str | None = None,
        open_access_pdf: bool = False,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query, "limit": min(limit, 100), "offset": offset}
        if fields:
            params["fields"] = fields
        if year:
            params["year"] = year
        if open_access_pdf:
            params["openAccessPdf"] = ""
        return await self._get(f"{S2_GRAPH_BASE}/paper/search", params=params)

    async def get_paper(self, paper_id: str, fields: str | None = None) -> dict[str, Any]:
        params = {"fields": fields} if fields else None
        return await self._get(f"{S2_GRAPH_BASE}/paper/{paper_id}", params=params)

    async def get_recommendations_for_paper(
        self,
        paper_id: str,
        limit: int = 50,
        from_pool: str = "recent",
        fields: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": min(limit, 500), "from": from_pool}
        if fields:
            params["fields"] = fields
        return await self._get(f"{S2_RECOMMEND_BASE}/papers/forpaper/{paper_id}", params=params)

    async def get_recommendations(
        self,
        positive_paper_ids: list[str],
        negative_paper_ids: list[str] | None = None,
        limit: int = 50,
        fields: str | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"limit": min(limit, 500)}
        if fields:
            params["fields"] = fields
        payload = {
            "positivePaperIds": positive_paper_ids,
            "negativePaperIds": negative_paper_ids or [],
        }
        return await self._post(f"{S2_RECOMMEND_BASE}/papers", payload=payload, params=params)
