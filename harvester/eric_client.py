from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from harvester.http_client import HttpSourceConfig, ThrottledJsonClient


ERIC_BASE = "https://api.ies.ed.gov/eric/"

ERIC_FIELDS = "id,title,author,description,publicationdateyear,source,peerreviewed,e_fulltextauth"


@dataclass
class EricConfig(HttpSourceConfig):
    """ERIC (US Dept. of Education), kein Key. Bildungsforschung."""

    base_url: str = ERIC_BASE
    requests_per_second: float = 2.0
    timeout_seconds: float = 30.0


class EricClient(ThrottledJsonClient):
    """Async-Wrapper um die ERIC-Suche."""

    def __init__(self, config: EricConfig | None = None) -> None:
        super().__init__(config or EricConfig())

    async def search_records(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        # ERIC antwortet mit JSON, deklariert aber text/plain — daher selbst parsen.
        text = await self.get_text(
            "",
            {"search": query, "format": "json", "rows": min(max(limit, 1), 200), "fields": ERIC_FIELDS},
        )
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise ValueError("ERIC lieferte keine gültige JSON-Antwort") from exc
        return list(((payload or {}).get("response") or {}).get("docs") or [])
