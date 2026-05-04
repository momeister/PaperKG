from __future__ import annotations

import asyncio
import re
import time
from dataclasses import dataclass
from typing import Any

import feedparser
import httpx


ARXIV_API_URL = "https://export.arxiv.org/api/query"
_ARXIV_ID_PATTERN = re.compile(r"(?:https?://arxiv.org/abs/)?(?P<base>[a-z\-]+/\d{7}|\d{4}\.\d{4,5})(?:v(?P<version>\d+))?", re.IGNORECASE)


@dataclass
class ArxivClientConfig:
    base_url: str = ARXIV_API_URL
    requests_per_second: float = 1 / 3  # arXiv recommends a 3 second pause between requests.
    timeout_seconds: float = 60.0


class ArxivClient:
    def __init__(self, config: ArxivClientConfig | None = None) -> None:
        self.config = config or ArxivClientConfig()
        self._client = httpx.AsyncClient(
            timeout=self.config.timeout_seconds,
            headers={
                "User-Agent": "ScienceKG/Phase1 (contact: local-development)",
                "Accept": "application/atom+xml,application/xml;q=0.9,*/*;q=0.8",
            },
        )
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

    async def _query(self, params: dict[str, Any]) -> feedparser.FeedParserDict:
        retry_delay = 10.0
        for attempt in range(6):
            await self._throttle()
            try:
                response = await self._client.get(self.config.base_url, params=params)
            except httpx.TimeoutException:
                if attempt == 5:
                    raise
                await asyncio.sleep(retry_delay)
                retry_delay = min(retry_delay * 2, 60.0)
                continue

            if response.status_code not in {429, 503}:
                response.raise_for_status()
                return feedparser.parse(response.text)

            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    retry_delay = max(retry_delay, float(retry_after))
                except ValueError:
                    pass

            if attempt == 5:
                response.raise_for_status()

            await asyncio.sleep(retry_delay)
            retry_delay = min(retry_delay * 2, 60.0)

        raise RuntimeError("arXiv request failed after retries")

    @staticmethod
    def _extract_arxiv_id_and_version(raw_id: str) -> tuple[str, int]:
        match = _ARXIV_ID_PATTERN.search(raw_id)
        if not match:
            return raw_id, 1
        base = match.group("base")
        version = int(match.group("version") or 1)
        return base, version

    @staticmethod
    def _extract_pdf_url(entry: feedparser.FeedParserDict) -> str | None:
        for link in entry.get("links", []):
            if link.get("title") == "pdf":
                return link.get("href")
            if link.get("type") == "application/pdf":
                return link.get("href")
        return None

    @staticmethod
    def _normalize_entry(entry: feedparser.FeedParserDict) -> dict[str, Any]:
        raw_id = entry.get("id", "")
        arxiv_id, version = ArxivClient._extract_arxiv_id_and_version(raw_id)
        doi = entry.get("arxiv_doi")
        return {
            "source": "arxiv",
            "source_id": arxiv_id,
            "version": version,
            "title": (entry.get("title") or "").strip().replace("\n", " "),
            "abstract": (entry.get("summary") or "").strip(),
            "authors": [author.get("name", "") for author in entry.get("authors", [])],
            "year": int((entry.get("published") or "0000")[:4]) if entry.get("published") else None,
            "doi": doi,
            "pdf_url": ArxivClient._extract_pdf_url(entry),
            "landing_page_url": raw_id,
            "raw": dict(entry),
        }

    async def search(
        self,
        query: str,
        max_results: int = 100,
        start: int = 0,
        sort_by: str = "relevance",
        sort_order: str = "descending",
    ) -> list[dict[str, Any]]:
        params = {
            "search_query": query if ":" in query else f"all:{query}",
            "start": start,
            "max_results": max_results,
            "sortBy": sort_by,
            "sortOrder": sort_order,
        }
        feed = await self._query(params)
        return [self._normalize_entry(entry) for entry in feed.entries]

    async def get_latest_version(self, arxiv_id: str) -> dict[str, Any] | None:
        feed = await self._query({"id_list": arxiv_id, "max_results": 1})
        if not feed.entries:
            return None
        return self._normalize_entry(feed.entries[0])

    @staticmethod
    def build_pdf_url(arxiv_id: str, version: int | None = None) -> str:
        if version is None:
            return f"https://arxiv.org/pdf/{arxiv_id}.pdf"
        return f"https://arxiv.org/pdf/{arxiv_id}v{version}.pdf"
