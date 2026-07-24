from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harvester.http_client import HttpSourceConfig, ThrottledJsonClient


HAL_BASE = "https://api.archives-ouvertes.fr/search/"

HAL_FIELDS = (
    "docid,title_s,abstract_s,authFullName_s,producedDateY_i,doiId_s,uri_s,fileMain_s,openAccess_bool"
)


@dataclass
class HalConfig(HttpSourceConfig):
    """HAL (franzoesisches OA-Archiv), kein Key. Stark in Geistes-/Sozialwissenschaften."""

    base_url: str = HAL_BASE
    requests_per_second: float = 2.0
    timeout_seconds: float = 30.0


class HalClient(ThrottledJsonClient):
    """Async-Wrapper um die HAL-Solr-Suche."""

    def __init__(self, config: HalConfig | None = None) -> None:
        super().__init__(config or HalConfig())

    async def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        payload = await self.get_json(
            "",
            {"q": query, "wt": "json", "rows": min(max(limit, 1), 100), "fl": HAL_FIELDS},
        )
        return list(((payload or {}).get("response") or {}).get("docs") or [])
