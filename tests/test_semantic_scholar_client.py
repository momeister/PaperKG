import pytest
import httpx

from harvester.semantic_scholar_client import (
    SemanticScholarClient,
    SemanticScholarConfig,
    SemanticScholarRateLimitError,
)


async def _no_sleep(*_args, **_kwargs):
    return None


@pytest.mark.asyncio
async def test_semantic_scholar_retries_429_then_succeeds(monkeypatch):
    calls = 0

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, request=request)
        return httpx.Response(200, json={"data": [{"paperId": "p1"}]}, request=request)

    monkeypatch.setattr("harvester.semantic_scholar_client.asyncio.sleep", _no_sleep)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SemanticScholarClient(
            SemanticScholarConfig(requests_per_second=1000.0, max_retries=1),
            client=http_client,
        )
        payload = await client.search_papers("Clinical-AI", limit=5)

    assert calls == 2
    assert payload["data"][0]["paperId"] == "p1"


@pytest.mark.asyncio
async def test_semantic_scholar_rate_limit_error_is_actionable(monkeypatch):
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, headers={"Retry-After": "12"}, request=request)

    monkeypatch.setattr("harvester.semantic_scholar_client.asyncio.sleep", _no_sleep)
    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport) as http_client:
        client = SemanticScholarClient(
            SemanticScholarConfig(requests_per_second=1000.0, max_retries=0),
            client=http_client,
        )
        with pytest.raises(SemanticScholarRateLimitError) as excinfo:
            await client.search_papers("Clinical-AI", limit=5)

    message = str(excinfo.value)
    assert "Semantic Scholar rate limit reached" in message
    assert "SEMANTIC_SCHOLAR_API_KEY" in message
    assert excinfo.value.retry_after_seconds == 12


@pytest.mark.asyncio
async def test_semantic_scholar_uses_api_key_header():
    seen_header = None

    async def handler(request: httpx.Request) -> httpx.Response:
        nonlocal seen_header
        seen_header = request.headers.get("x-api-key")
        return httpx.Response(200, json={"data": []}, request=request)

    transport = httpx.MockTransport(handler)
    client = SemanticScholarClient(
        SemanticScholarConfig(api_key="s2-test", requests_per_second=1000.0),
        transport=transport,
    )
    try:
        await client.search_papers("Clinical-AI", limit=5)
    finally:
        await client.close()

    assert seen_header == "s2-test"
