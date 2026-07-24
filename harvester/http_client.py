"""Gemeinsame Basis fuer die Harvester-Clients der zweiten Generation.

Die aelteren Clients (arxiv, crossref, doaj, ...) bringen ihre Throttle-Logik
jeweils selbst mit. Fuer die neu hinzugekommenen Quellen liegt sie hier einmal,
damit ein weiterer Client nur noch Basis-URL und Antwort-Parsing beschreibt.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import Any

import httpx


USER_AGENT = "ScienceKG/Phase5 (local-development)"


@dataclass
class HttpSourceConfig:
    """Basis-Konfiguration: Endpunkt, Rate-Limit, Timeout, optionaler Key."""

    base_url: str = ""
    requests_per_second: float = 2.0
    timeout_seconds: float = 30.0
    api_key: str | None = None


class ThrottledJsonClient:
    """Async-JSON-Client mit einfachem Rate-Limit pro Instanz."""

    config: HttpSourceConfig

    def __init__(self, config: HttpSourceConfig, extra_headers: dict[str, str] | None = None) -> None:
        self.config = config
        headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
        headers.update(extra_headers or {})
        self._client = httpx.AsyncClient(timeout=config.timeout_seconds, headers=headers)
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

    async def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        await self._throttle()
        url = path if path.startswith("http") else f"{self.config.base_url}{path}"
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.json()

    async def get_text(self, path: str, params: dict[str, Any] | None = None) -> str:
        await self._throttle()
        url = path if path.startswith("http") else f"{self.config.base_url}{path}"
        response = await self._client.get(url, params=params)
        response.raise_for_status()
        return response.text
