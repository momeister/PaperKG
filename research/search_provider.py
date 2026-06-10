"""Configurable web-search provider abstraction for the deep-research feature.

Mirrors the spirit of ``query.llm_router``: a single interface over several backends
selected/configured via the ``research:`` block of config.yaml. Supported providers:

- ``searxng``     — self-hosted metasearch (max privacy, no key; needs base_url)
- ``duckduckgo``  — keyless HTML endpoint
- ``tavily``      — research-oriented API (key via env)
- ``brave``       — Brave Search API (key via env)
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import httpx
import yaml


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str

    def to_dict(self) -> dict[str, str]:
        return {"url": self.url, "title": self.title, "snippet": self.snippet}


class SearchProviderError(RuntimeError):
    """Raised when a web-search provider cannot run (e.g. missing key/base_url)."""


@dataclass
class ResearchConfig:
    default_provider: str = "duckduckgo"
    providers: dict[str, dict[str, Any]] | None = None
    allowed_domains: list[str] | None = None
    blocked_domains: list[str] | None = None
    timeout_seconds: float = 30.0

    def provider_settings(self, name: str) -> dict[str, Any]:
        section = (self.providers or {}).get(name, {})
        return section if isinstance(section, dict) else {}


def load_research_config(config_path: str = "config.yaml") -> ResearchConfig:
    raw: dict[str, Any] = {}
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            raw = (yaml.safe_load(fh) or {}).get("research", {}) or {}
    except FileNotFoundError:
        raw = {}
    return ResearchConfig(
        default_provider=str(raw.get("default_provider") or "duckduckgo"),
        providers=raw.get("providers") if isinstance(raw.get("providers"), dict) else {},
        allowed_domains=raw.get("allowed_domains") or [],
        blocked_domains=raw.get("blocked_domains") or [],
        timeout_seconds=float(raw.get("timeout_seconds") or 30.0),
    )


def _resolve_secret(settings: dict[str, Any], env_default: str) -> str | None:
    env_name = settings.get("api_key_env") or env_default
    return os.getenv(env_name) or settings.get("api_key")


async def run_web_search(
    query: str,
    config: ResearchConfig,
    provider: str | None = None,
    max_results: int = 6,
) -> list[SearchHit]:
    """Run a single web search through the selected provider and apply domain filters."""
    name = (provider or config.default_provider or "duckduckgo").lower()
    settings = config.provider_settings(name)
    timeout = float(settings.get("timeout_seconds") or config.timeout_seconds)

    async with httpx.AsyncClient(
        timeout=timeout,
        headers={"User-Agent": "ScienceKG/Phase5 deep-research (local)"},
        follow_redirects=True,
    ) as client:
        if name == "searxng":
            hits = await _search_searxng(client, query, settings, max_results)
        elif name == "duckduckgo":
            hits = await _search_duckduckgo(query, max_results)
        elif name == "tavily":
            hits = await _search_tavily(client, query, settings, max_results)
        elif name == "brave":
            hits = await _search_brave(client, query, settings, max_results)
        else:
            raise SearchProviderError(f"Unknown web-search provider: {name}")

    hits = _filter_relevant_hits(hits, query)
    return _apply_domain_filters(hits, config)[:max_results]


def _apply_domain_filters(hits: list[SearchHit], config: ResearchConfig) -> list[SearchHit]:
    allowed = [d.lower() for d in (config.allowed_domains or [])]
    blocked = [d.lower() for d in (config.blocked_domains or [])]
    out: list[SearchHit] = []
    for hit in hits:
        host = (re.sub(r"^https?://", "", hit.url).split("/", 1)[0] or "").lower()
        if blocked and any(b in host for b in blocked):
            continue
        if allowed and not any(a in host for a in allowed):
            continue
        out.append(hit)
    return out


async def _search_searxng(
    client: httpx.AsyncClient, query: str, settings: dict[str, Any], max_results: int
) -> list[SearchHit]:
    base_url = settings.get("base_url") or os.getenv("SEARXNG_BASE_URL")
    if not base_url:
        raise SearchProviderError("SearXNG requires base_url (set SEARXNG_BASE_URL or research.providers.searxng.base_url).")
    response = await client.get(
        f"{base_url.rstrip('/')}/search", params={"q": query, "format": "json"}
    )
    response.raise_for_status()
    payload = response.json()
    hits = []
    for item in payload.get("results", [])[:max_results]:
        hits.append(SearchHit(url=item.get("url", ""), title=item.get("title", ""), snippet=item.get("content", "")))
    return hits


def _filter_relevant_hits(hits: list[SearchHit], query: str) -> list[SearchHit]:
    """Drop hits with zero query-term overlap to filter out rate-limit junk results."""
    query_terms = {w.lower() for w in re.split(r"\W+", query) if len(w) > 3}
    if not query_terms:
        return hits
    out = []
    for hit in hits:
        text = (hit.title + " " + hit.snippet).lower()
        if any(t in text for t in query_terms):
            out.append(hit)
    return out


async def _search_duckduckgo(query: str, max_results: int) -> list[SearchHit]:
    import asyncio
    try:
        from ddgs import DDGS
    except ImportError:
        try:
            from duckduckgo_search import DDGS  # type: ignore[no-redef]
        except ImportError:
            raise SearchProviderError(
                "Neither 'ddgs' nor 'duckduckgo_search' is installed. Run: pip install ddgs"
            )

    def _sync_search() -> list[dict[str, Any]]:
        ddgs = DDGS(timeout=15)
        return list(ddgs.text(query, max_results=max_results))

    results = await asyncio.get_event_loop().run_in_executor(None, _sync_search)
    return [
        SearchHit(
            url=str(r.get("href") or ""),
            title=str(r.get("title") or ""),
            snippet=str(r.get("body") or ""),
        )
        for r in results
    ]


async def _search_tavily(
    client: httpx.AsyncClient, query: str, settings: dict[str, Any], max_results: int
) -> list[SearchHit]:
    api_key = _resolve_secret(settings, "TAVILY_API_KEY")
    if not api_key:
        raise SearchProviderError("Tavily requires an API key (set TAVILY_API_KEY).")
    response = await client.post(
        "https://api.tavily.com/search",
        json={"api_key": api_key, "query": query, "max_results": max_results, "include_raw_content": False},
    )
    response.raise_for_status()
    payload = response.json()
    return [
        SearchHit(url=item.get("url", ""), title=item.get("title", ""), snippet=item.get("content", ""))
        for item in payload.get("results", [])[:max_results]
    ]


async def _search_brave(
    client: httpx.AsyncClient, query: str, settings: dict[str, Any], max_results: int
) -> list[SearchHit]:
    api_key = _resolve_secret(settings, "BRAVE_API_KEY")
    if not api_key:
        raise SearchProviderError("Brave requires an API key (set BRAVE_API_KEY).")
    response = await client.get(
        "https://api.search.brave.com/res/v1/web/search",
        params={"q": query, "count": max_results},
        headers={"X-Subscription-Token": api_key, "Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    return [
        SearchHit(url=item.get("url", ""), title=item.get("title", ""), snippet=item.get("description", ""))
        for item in payload.get("web", {}).get("results", [])[:max_results]
    ]
