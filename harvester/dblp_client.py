from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harvester.http_client import HttpSourceConfig, ThrottledJsonClient


DBLP_BASE = "https://dblp.org/search/publ/api"


@dataclass
class DblpConfig(HttpSourceConfig):
    """DBLP-Publikationssuche, kein Key. Informatik-Bibliografie ohne Abstracts."""

    base_url: str = DBLP_BASE
    requests_per_second: float = 1.0
    timeout_seconds: float = 30.0


class DblpClient(ThrottledJsonClient):
    """Async-Wrapper um die DBLP-Publikationssuche."""

    def __init__(self, config: DblpConfig | None = None) -> None:
        super().__init__(config or DblpConfig())

    async def search_publications(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self.get_json("", {"q": query, "format": "json", "h": min(max(limit, 1), 100)})
        hits = ((payload or {}).get("result") or {}).get("hits") or {}
        raw = hits.get("hit") or []
        # DBLP liefert bei genau einem Treffer ein Objekt statt einer Liste.
        if isinstance(raw, dict):
            raw = [raw]
        return [item.get("info") or {} for item in raw if isinstance(item, dict)]
