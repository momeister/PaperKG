from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harvester.http_client import HttpSourceConfig, ThrottledJsonClient


DOAB_BASE = "https://directory.doabooks.org/rest"


@dataclass
class DoabConfig(HttpSourceConfig):
    """Directory of Open Access Books, kein Key. Buecher statt Artikel."""

    base_url: str = DOAB_BASE
    requests_per_second: float = 1.0
    timeout_seconds: float = 45.0


class DoabClient(ThrottledJsonClient):
    """Async-Wrapper um die DOAB-Suche (DSpace-REST)."""

    def __init__(self, config: DoabConfig | None = None) -> None:
        super().__init__(config or DoabConfig())

    async def search_books(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self.get_json(
            "/search",
            {"query": query, "expand": "metadata", "limit": min(max(limit, 1), 100)},
        )
        return [item for item in (payload or []) if isinstance(item, dict)]


def doab_metadata(item: dict[str, Any]) -> dict[str, list[str]]:
    """DSpace-Metadaten (Liste aus key/value) in ein Mapping key -> Werte drehen."""
    output: dict[str, list[str]] = {}
    for entry in item.get("metadata") or []:
        if not isinstance(entry, dict):
            continue
        key = str(entry.get("key") or "")
        value = entry.get("value")
        if key and value:
            output.setdefault(key, []).append(str(value))
    return output
