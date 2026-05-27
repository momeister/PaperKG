from __future__ import annotations

import asyncio
import email.utils
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import httpx


S2_GRAPH_BASE = "https://api.semanticscholar.org/graph/v1"
S2_RECOMMEND_BASE = "https://api.semanticscholar.org/recommendations/v1"


@dataclass
class SemanticScholarConfig:
    api_key: str | None = None
    requests_per_second: float = 1.0
    timeout_seconds: float = 30.0
    max_retries: int = 3
    initial_retry_delay_seconds: float = 2.0


class SemanticScholarRateLimitError(RuntimeError):
    """Raised when Semantic Scholar keeps returning HTTP 429 after retries."""

    def __init__(self, retry_after_seconds: float | None = None) -> None:
        wait_hint = (
            f" Wait about {int(retry_after_seconds)} seconds and retry."
            if retry_after_seconds
            else " Wait a bit and retry."
        )
        super().__init__(
            "Semantic Scholar rate limit reached (HTTP 429)."
            f"{wait_hint} Add SEMANTIC_SCHOLAR_API_KEY or S2_API_KEY to .env/config.yaml "
            "for a higher quota, or use ArXiv/OpenAlex meanwhile."
        )
        self.retry_after_seconds = retry_after_seconds


class SemanticScholarClient:
    def __init__(
        self,
        config: SemanticScholarConfig | None = None,
        client: httpx.AsyncClient | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.config = config or SemanticScholarConfig()
        headers: dict[str, str] = {
            "Accept": "application/json",
            "User-Agent": "ScienceKG/Phase1 (contact: local-development)",
        }
        if self.config.api_key:
            headers["x-api-key"] = self.config.api_key
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            headers=headers,
            timeout=self.config.timeout_seconds,
            transport=transport,
        )
        self._lock = asyncio.Lock()
        self._last_request_ts = 0.0

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def _throttle(self) -> None:
        async with self._lock:
            now = time.monotonic()
            requests_per_second = max(float(self.config.requests_per_second or 1.0), 0.01)
            min_interval = 1.0 / requests_per_second
            elapsed = now - self._last_request_ts
            if elapsed < min_interval:
                await asyncio.sleep(min_interval - elapsed)
            self._last_request_ts = time.monotonic()

    @staticmethod
    def _retry_after_seconds(value: str | None) -> float | None:
        if not value:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            pass
        try:
            parsed = email.utils.parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max((parsed - datetime.now(timezone.utc)).total_seconds(), 0.0)

    async def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
        json_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        retry_delay = max(float(self.config.initial_retry_delay_seconds or 1.0), 0.25)
        max_retries = max(int(self.config.max_retries), 0)
        last_retry_after: float | None = None

        for attempt in range(max_retries + 1):
            await self._throttle()
            try:
                response = await self._client.request(method, url, params=params, json=json_payload)
            except httpx.TimeoutException:
                if attempt >= max_retries:
                    raise
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)
                continue

            if response.status_code == 429:
                retry_after = self._retry_after_seconds(response.headers.get("Retry-After"))
                last_retry_after = retry_after
                if attempt >= max_retries:
                    raise SemanticScholarRateLimitError(retry_after)
                await asyncio.sleep(max(retry_after or 0.0, retry_delay))
                retry_delay = min(retry_delay * 2, 60.0)
                continue

            if response.status_code in {500, 502, 503, 504} and attempt < max_retries:
                retry_after = self._retry_after_seconds(response.headers.get("Retry-After"))
                await asyncio.sleep(max(retry_after or 0.0, retry_delay))
                retry_delay = min(retry_delay * 2, 60.0)
                continue

            response.raise_for_status()
            return response.json()

        raise SemanticScholarRateLimitError(last_retry_after)

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("GET", url, params=params)

    async def _post(self, url: str, payload: dict[str, Any], params: dict[str, Any] | None = None) -> dict[str, Any]:
        return await self._request("POST", url, params=params, json_payload=payload)

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
