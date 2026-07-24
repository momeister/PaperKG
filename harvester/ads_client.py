from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from harvester.http_client import HttpSourceConfig, ThrottledJsonClient


ADS_BASE = "https://api.adsabs.harvard.edu/v1"

ADS_FIELDS = "bibcode,title,abstract,author,year,doi,identifier,esources"


class AdsApiKeyMissing(RuntimeError):
    """Raised when an ADS search is attempted without an API token configured."""


@dataclass
class AdsConfig(HttpSourceConfig):
    """NASA ADS braucht einen kostenlosen Token (env ADS_API_KEY)."""

    base_url: str = ADS_BASE
    requests_per_second: float = 1.0
    timeout_seconds: float = 45.0


class AdsClient(ThrottledJsonClient):
    """Async-Wrapper um die ADS-Suche (Astronomie/Astrophysik/Physik)."""

    def __init__(self, config: AdsConfig | None = None) -> None:
        config = config or AdsConfig()
        headers = {"Authorization": f"Bearer {config.api_key}"} if config.api_key else {}
        super().__init__(config, extra_headers=headers)

    async def search_documents(self, query: str, limit: int = 20) -> list[dict[str, Any]]:
        if not self.config.api_key:
            raise AdsApiKeyMissing(
                "NASA ADS benötigt einen kostenlosen Token. ADS_API_KEY in der Umgebung / .env setzen."
            )
        payload = await self.get_json(
            "/search/query",
            {"q": query, "rows": min(max(limit, 1), 100), "fl": ADS_FIELDS},
        )
        return list(((payload or {}).get("response") or {}).get("docs") or [])
