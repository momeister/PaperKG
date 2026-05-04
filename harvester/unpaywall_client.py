from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx


UNPAYWALL_BASE = "https://api.unpaywall.org/v2"


@dataclass
class UnpaywallConfig:
    email: str
    timeout_seconds: float = 30.0


class UnpaywallClient:
    def __init__(self, config: UnpaywallConfig) -> None:
        if not config.email:
            raise ValueError("Unpaywall API requires a contact email.")
        self.config = config
        self._client = httpx.AsyncClient(timeout=self.config.timeout_seconds)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_doi_record(self, doi: str) -> dict[str, Any]:
        response = await self._client.get(f"{UNPAYWALL_BASE}/{doi}", params={"email": self.config.email})
        response.raise_for_status()
        return response.json()

    async def best_oa_url(self, doi: str) -> str | None:
        data = await self.get_doi_record(doi)
        location = data.get("best_oa_location") or {}
        return location.get("url_for_pdf") or location.get("url")
