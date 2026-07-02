"""Tests for the LLMRouter vision/multimodal path + the new `anthropic` provider.

Fully offline: every provider call goes through an `httpx.MockTransport` injected via
the router's `client=` constructor arg, and the captured request payloads are asserted
against each provider's wire format (OpenAI parts passthrough, Ollama `images`,
Anthropic content blocks + headers)."""
from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from query.llm_router import GenerationSettings, LLMRouter, ProviderConfig
from query.screen_companion import smart_resize_to_budget

# "ABC" — content is irrelevant, only the plumbing is under test.
DATA_URL = "data:image/png;base64,QUJD"
PARTS_MESSAGE = {
    "role": "user",
    "content": [
        {"type": "image_url", "image_url": {"url": DATA_URL}},
        {"type": "text", "text": "Was ist das?"},
    ],
}


def _router(
    provider_type: str,
    handler,
    api_key: str | None = None,
) -> LLMRouter:
    cfg = ProviderConfig(
        provider_type=provider_type,
        base_url="http://test.local",
        api_key=api_key,
        settings=GenerationSettings(model="test-model", max_tokens=512),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LLMRouter(providers={"p": cfg}, default_provider="p", client=client)


# --------------------------------------------------------------------------- #
# OpenAI-compatible (LM Studio/Ollama-OpenAI): parts lists pass through as-is   #
# --------------------------------------------------------------------------- #

def test_openai_compatible_passes_parts_through() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
        )

    router = _router("lm_studio", handler)
    answer = router.chat([PARTS_MESSAGE])
    assert answer == "ok"
    # The parts list is the native OpenAI format — it must arrive unmodified.
    assert captured["payload"]["messages"][0]["content"] == PARTS_MESSAGE["content"]


def test_openai_compatible_flags_reasoning_fallback() -> None:
    # LM Studio reasoning builds can leave `content` empty and put everything into
    # `reasoning_content`; with finish_reason=length the model never produced an
    # answer — the metadata flag lets callers turn that into a clear error.
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {"content": "", "reasoning_content": "The user is asking…"},
                        "finish_reason": "length",
                    }
                ],
                "usage": {},
            },
        )

    router = _router("lm_studio", handler)
    assert router.chat([PARTS_MESSAGE]) == "The user is asking…"
    assert router.last_response_metadata["reasoning_fallback"] is True
    assert router.last_response_metadata["finish_reason"] == "length"

    def handler_normal(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200, json={"choices": [{"message": {"content": "ok"}, "finish_reason": "stop"}], "usage": {}}
        )

    router = _router("lm_studio", handler_normal)
    router.chat([PARTS_MESSAGE])
    assert router.last_response_metadata["reasoning_fallback"] is False


# --------------------------------------------------------------------------- #
# Ollama native API: text flattened, images moved to the per-message field      #
# --------------------------------------------------------------------------- #

def test_ollama_converts_parts_to_images_field() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "ok"}})

    router = _router("ollama", handler)
    assert router.chat([{"role": "system", "content": "Systemtext"}, PARTS_MESSAGE]) == "ok"

    messages = captured["payload"]["messages"]
    # Plain-string messages stay untouched.
    assert messages[0] == {"role": "system", "content": "Systemtext"}
    # Parts message: text flattened, image as bare base64 in `images`.
    assert messages[1]["content"] == "Was ist das?"
    assert messages[1]["images"] == ["QUJD"]


# --------------------------------------------------------------------------- #
# Anthropic Messages API                                                       #
# --------------------------------------------------------------------------- #

def test_anthropic_payload_shape_and_headers() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["headers"] = request.headers
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [{"type": "text", "text": "Antwort"}],
                "usage": {"input_tokens": 10, "output_tokens": 5},
                "stop_reason": "end_turn",
                "model": "test-model",
            },
        )

    router = _router("anthropic", handler, api_key="sk-test")
    answer = router.chat(
        [{"role": "system", "content": "Du bist hilfreich."}, PARTS_MESSAGE],
        overrides={"temperature": 0.1, "top_p": 0.5, "seed": 7},
    )
    assert answer == "Antwort"
    assert captured["url"] == "http://test.local/v1/messages"
    assert captured["headers"]["x-api-key"] == "sk-test"
    assert captured["headers"]["anthropic-version"] == "2023-06-01"

    payload = captured["payload"]
    # System messages move to the top-level `system` string.
    assert payload["system"] == "Du bist hilfreich."
    assert all(msg["role"] != "system" for msg in payload["messages"])
    # max_tokens is mandatory; sampling params are deliberately omitted even when
    # overridden (newer Claude models reject non-default combinations with 400).
    assert payload["max_tokens"] == 512
    for forbidden in ("temperature", "top_p", "seed"):
        assert forbidden not in payload
    # The image part becomes a base64 source block with the data-URL's media type.
    blocks = payload["messages"][0]["content"]
    assert blocks[0] == {
        "type": "image",
        "source": {"type": "base64", "media_type": "image/png", "data": "QUJD"},
    }
    assert blocks[1] == {"type": "text", "text": "Was ist das?"}

    assert router.last_response_metadata["provider_type"] == "anthropic"
    assert router.last_response_metadata["stop_reason"] == "end_turn"


def test_anthropic_joins_text_blocks_and_skips_non_text() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "thinking", "thinking": "geheim"},
                    {"type": "text", "text": "Hallo "},
                    {"type": "text", "text": "Welt"},
                ],
                "usage": {},
            },
        )

    router = _router("anthropic", handler, api_key="sk-test")
    # Never index content[0]: a leading non-text block must not break parsing.
    assert router.chat([{"role": "user", "content": "hi"}]) == "Hallo Welt"


def test_anthropic_http_error_surfaces_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:  # noqa: ARG001
        return httpx.Response(400, json={"error": {"message": "bad sampling combo"}})

    router = _router("anthropic", handler, api_key="sk-test")
    with pytest.raises(RuntimeError, match="bad sampling combo"):
        router.chat([{"role": "user", "content": "hi"}])


def test_anthropic_model_discovery() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/models"
        assert request.headers["x-api-key"] == "sk-test"
        return httpx.Response(200, json={"data": [{"id": "claude-sonnet-5"}, {"id": "claude-haiku-4-5-20251001"}]})

    router = _router("anthropic", handler, api_key="sk-test")
    assert router.discover_provider_models() == ["claude-sonnet-5", "claude-haiku-4-5-20251001"]


# --------------------------------------------------------------------------- #
# Smart-resize maths (port of bridge/uitars/server.mjs smartResizeToBudget)     #
# --------------------------------------------------------------------------- #

def test_smart_resize_keeps_in_budget_dimensions() -> None:
    # Already 28-multiples and inside the budget → unchanged.
    assert smart_resize_to_budget(1400, 700) == (1400, 700)


def test_smart_resize_downscales_to_budget() -> None:
    width, height = smart_resize_to_budget(3440, 1440)
    assert width % 28 == 0 and height % 28 == 0
    assert width * height <= 1280 * 28 * 28
    # Aspect ratio roughly preserved.
    assert abs((width / height) - (3440 / 1440)) < 0.1


def test_smart_resize_upscales_tiny_images_to_min_pixels() -> None:
    width, height = smart_resize_to_budget(100, 100)
    assert width % 28 == 0 and height % 28 == 0
    assert width * height >= 100 * 28 * 28


def test_smart_resize_rounds_halves_up_like_js() -> None:
    # 714/28 = 25.5 → JS Math.round gives 26 (Python's banker's rounding would give 25);
    # 728×420 stays inside [min, max], so no rescale branch masks the rounding.
    assert smart_resize_to_budget(714, 420) == (728, 420)
