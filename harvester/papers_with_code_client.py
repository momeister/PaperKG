from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


PWC_BASE = "https://paperswithcode.com/api/v1"


@dataclass
class PapersWithCodeConfig:
    timeout_seconds: float = 30.0
    token: str | None = None


class PapersWithCodeClient:
    """
    Minimal wrapper around the legacy Papers with Code API.

    Note: Some deployments now redirect away from this API. Keep this client optional and fail-soft.
    """

    def __init__(self, config: PapersWithCodeConfig | None = None) -> None:
        self.config = config or PapersWithCodeConfig()
        headers: dict[str, str] = {}
        if self.config.token:
            headers["Authorization"] = f"Token {self.config.token}"
        self._client = httpx.AsyncClient(headers=headers, timeout=self.config.timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def _get(self, endpoint: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        response = await self._client.get(f"{PWC_BASE}{endpoint}", params=params)
        response.raise_for_status()
        return response.json()

    async def search_papers(self, q: str, page: int = 1, items_per_page: int = 50) -> dict[str, Any]:
        return await self._get("/papers/", params={"q": q, "page": page, "items_per_page": items_per_page})

    async def get_paper(self, paper_id: str) -> dict[str, Any]:
        return await self._get(f"/papers/{paper_id}/")

    async def list_repositories(self, paper_id: str) -> dict[str, Any]:
        return await self._get(f"/papers/{paper_id}/repositories/")
