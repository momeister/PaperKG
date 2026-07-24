from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harvester.http_client import HttpSourceConfig, ThrottledJsonClient


OPENAIRE_BASE = "https://api.openaire.eu/graph/v1"


@dataclass
class OpenAireConfig(HttpSourceConfig):
    """OpenAIRE Graph API v1, kein Key. EU-weiter Aggregator ueber alle Faecher."""

    base_url: str = OPENAIRE_BASE
    requests_per_second: float = 1.0
    timeout_seconds: float = 45.0


class OpenAireClient(ThrottledJsonClient):
    """Async-Wrapper um die OpenAIRE-Graph-Suche nach Publikationen."""

    def __init__(self, config: OpenAireConfig | None = None) -> None:
        super().__init__(config or OpenAireConfig())

    async def search_publications(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self.get_json(
            "/researchProducts",
            {"search": query, "pageSize": min(max(limit, 1), 100), "type": "publication"},
        )
        return list((payload or {}).get("results") or [])
