"""Tests for the Desktop Companion: screen_companion module + /companion/* endpoints.

Fully offline and deterministic — the LLM router is faked (`product_main.llm_router`
monkeypatched, style of test_agent_handoff.py); only Pillow does real work (the
screenshots are synthetic in-memory PNGs)."""
from __future__ import annotations

import base64
import io
import json
from typing import Any

import api.product_main as product_main
from fastapi.testclient import TestClient

from query import screen_companion


def _png_b64(width: int, height: int) -> str:
    from PIL import Image

    image = Image.new("RGB", (width, height), (30, 30, 30))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class _FakeRouter:
    """Returns a canned guide JSON and records the calls it received."""

    default_provider = "lm_studio"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, provider=None, overrides=None):
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides or {}})
        return self.reply

    def available_providers(self):
        return ["anthropic", "lm_studio"]

    def provider_model_options(self, provider=None, refresh=False):  # noqa: ARG002
        return ["qwen/qwen3-vl-8b"] if provider == "lm_studio" else ["claude-sonnet-5"]


class _RaisingRouter:
    default_provider = "lm_studio"

    def chat(self, messages, provider=None, overrides=None):  # noqa: ARG002
        raise RuntimeError("vlm down")


# --------------------------------------------------------------------------- #
# screen_companion module                                                      #
# --------------------------------------------------------------------------- #

def test_prepare_image_resizes_into_budget() -> None:
    prepared = screen_companion.prepare_image(_png_b64(2800, 1400))
    assert (prepared.width, prepared.height) == (2800, 1400)
    # 2800×1400 is ~3.9 MP → halved into the ~1.0 MP budget (28-multiples).
    assert (prepared.sent_width, prepared.sent_height) == (1400, 700)
    assert prepared.data_url.startswith("data:image/png;base64,")


def test_prepare_image_accepts_data_url_and_rejects_garbage() -> None:
    prepared = screen_companion.prepare_image("data:image/png;base64," + _png_b64(280, 280))
    assert (prepared.width, prepared.height) == (280, 280)
    try:
        screen_companion.prepare_image("%%%kein-bild%%%")
    except ValueError as exc:
        assert "dekodiert" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")


def test_guide_scales_grid_steps_and_clamps() -> None:
    # Contract: coordinates on the 0-1000 grid over the whole image (Qwen-VL native
    # grounding space) → x/1000·width, y/1000·height in original pixels.
    reply = json.dumps(
        {
            "answer": "Klicke dort.",
            "steps": [
                {"x": 100, "y": 50, "label": "Erster Schritt"},
                {"x": 99999, "y": 99999, "label": "außerhalb"},  # clamped into the frame
                {"x": "kein-int", "y": 5, "label": "kaputt"},  # dropped
            ],
        }
    )
    router = _FakeRouter(reply)
    result = screen_companion.guide(router, "wo?", _png_b64(2800, 1400))
    assert result["answer"] == "Klicke dort."
    assert result["found"] is True
    assert result["steps"][0] == {"x": 280.0, "y": 70.0, "label": "Erster Schritt"}
    assert result["steps"][1]["x"] == 2799.0  # width - 1
    assert result["steps"][1]["y"] == 1399.0
    assert len(result["steps"]) == 2

    # The system prompt announces the grid contract, the frame dims and the
    # point-vs-answer decision rule.
    system = router.calls[0]["messages"][0]["content"]
    assert "1400x700" in system
    assert "0-1000" in system
    assert "Wissens" in system  # decision rule: knowledge questions → steps: []
    # Vision part goes to the model as an image_url data URL.
    user_content = router.calls[0]["messages"][-1]["content"]
    assert user_content[0]["type"] == "image_url"


def test_guide_pixel_answers_use_sent_frame_fallback() -> None:
    # Values beyond the 0-1000 grid mean the model answered in sent-frame pixels
    # (1400×700 here) despite the contract → old sent→original scaling (×2).
    reply = json.dumps({"answer": "Da.", "steps": [{"x": 1200, "y": 400, "label": "Ziel"}]})
    router = _FakeRouter(reply)
    result = screen_companion.guide(router, "wo?", _png_b64(2800, 1400))
    assert result["steps"][0] == {"x": 2400.0, "y": 800.0, "label": "Ziel"}


def test_guide_debug_capture_writes_dump(tmp_path) -> None:
    reply = json.dumps({"answer": "Da.", "steps": [{"x": 500, "y": 500, "label": "Mitte"}]})
    router = _FakeRouter(reply)
    result = screen_companion.guide(router, "wo?", _png_b64(560, 560), debug_dir=str(tmp_path))
    assert result["found"] is True
    pngs = list(tmp_path.glob("*.png"))
    jsons = list(tmp_path.glob("*.json"))
    assert len(pngs) == 1 and len(jsons) == 1
    record = json.loads(jsons[0].read_text(encoding="utf-8"))
    assert record["question"] == "wo?"
    assert record["frame"]["width"] == 560
    assert record["steps_original_px"][0]["label"] == "Mitte"


def test_guide_degrades_to_text_when_json_missing() -> None:
    router = _FakeRouter("Nur Fließtext, kein JSON.")
    result = screen_companion.guide(router, "wo?", _png_b64(560, 560))
    assert result == {"answer": "Nur Fließtext, kein JSON.", "found": False, "steps": []}


def test_guide_empty_steps_means_not_found() -> None:
    router = _FakeRouter(json.dumps({"answer": "Nicht sichtbar.", "steps": []}))
    result = screen_companion.guide(router, "wo?", _png_b64(560, 560))
    assert result["found"] is False
    assert result["steps"] == []


def test_ask_includes_history_and_region_hint() -> None:
    router = _FakeRouter("Das ist ein Dialogfenster.")
    answer = screen_companion.ask(
        router,
        "Was ist das?",
        image_base64=_png_b64(280, 280),
        history=[
            {"role": "user", "content": "Frage 1"},
            {"role": "assistant", "content": "Antwort 1"},
            {"role": "banana", "content": "ignoriert"},
        ],
        region=True,
    )
    assert answer == "Das ist ein Dialogfenster."
    messages = router.calls[0]["messages"]
    assert "Ausschnitt" in messages[0]["content"]  # region hint in the system prompt
    assert {"role": "user", "content": "Frage 1"} in messages
    assert {"role": "assistant", "content": "Antwort 1"} in messages
    assert all(m.get("content") != "ignoriert" for m in messages)


class _ThinkingExhaustedRouter(_FakeRouter):
    """Simulates a reasoning model that burned the whole max_tokens budget inside its
    thinking channel: the router falls back to reasoning_content + finish_reason=length."""

    def chat(self, messages, provider=None, overrides=None):
        result = super().chat(messages, provider, overrides)
        self.last_response_metadata = {"reasoning_fallback": True, "finish_reason": "length"}
        return result


def test_thinking_budget_exhausted_raises_clear_error() -> None:
    router = _ThinkingExhaustedRouter("The user is asking what they can imagine…")
    try:
        screen_companion.ask(router, "Was ist das?", image_base64=_png_b64(280, 280))
    except RuntimeError as exc:
        assert "Token-Budget" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected RuntimeError")


def test_no_think_suffix_only_for_qwen_models() -> None:
    router = _FakeRouter("ok")
    screen_companion.ask(router, "Frage", model="qwen/qwen3-vl-8b")
    assert router.calls[0]["messages"][0]["content"].endswith("/no_think")

    router = _FakeRouter("ok")
    screen_companion.ask(router, "Frage", model="gemma-3-27b-it")
    assert "/no_think" not in router.calls[0]["messages"][0]["content"]

    router = _FakeRouter("ok")
    screen_companion.ask(router, "Frage", model="qwen/qwen3-vl-8b", disable_thinking=False)
    assert "/no_think" not in router.calls[0]["messages"][0]["content"]


def test_max_tokens_defaults_and_override() -> None:
    router = _FakeRouter("ok")
    screen_companion.ask(router, "Frage")
    assert router.calls[0]["overrides"]["max_tokens"] == screen_companion.DEFAULT_MAX_TOKENS_ASK

    router = _FakeRouter(json.dumps({"answer": "a", "steps": []}))
    screen_companion.guide(router, "wo?", _png_b64(280, 280))
    assert router.calls[0]["overrides"]["max_tokens"] == screen_companion.DEFAULT_MAX_TOKENS_GUIDE

    router = _FakeRouter(json.dumps({"answer": "a", "steps": []}))
    screen_companion.guide(router, "wo?", _png_b64(280, 280), max_tokens=3200)
    assert router.calls[0]["overrides"]["max_tokens"] == 3200


# --------------------------------------------------------------------------- #
# /companion/* endpoints                                                       #
# --------------------------------------------------------------------------- #

def _client(monkeypatch, router) -> TestClient:
    monkeypatch.setattr(product_main, "llm_router", router)
    monkeypatch.setattr(
        product_main,
        "_COMPANION_CONFIG_CACHE",
        {"provider": "lm_studio", "model": "qwen/qwen3-vl-8b", "language": "de", "history_turns": 8},
    )
    return TestClient(product_main.app)


def test_companion_guide_endpoint_scales_back(monkeypatch) -> None:
    router = _FakeRouter(json.dumps({"answer": "Hier.", "steps": [{"x": 100, "y": 50, "label": "Ziel"}]}))
    client = _client(monkeypatch, router)
    res = client.post("/companion/guide", json={"question": "wo?", "image_base64": _png_b64(2800, 1400)})
    assert res.status_code == 200
    body = res.json()
    assert body["found"] is True
    assert body["steps"] == [{"x": 280.0, "y": 70.0, "label": "Ziel"}]
    # Config defaults flow into the router call.
    assert router.calls[0]["provider"] == "lm_studio"
    assert router.calls[0]["overrides"]["model"] == "qwen/qwen3-vl-8b"


def test_companion_guide_endpoint_reports_errors_in_body(monkeypatch) -> None:
    client = _client(monkeypatch, _RaisingRouter())
    res = client.post("/companion/guide", json={"question": "wo?", "image_base64": _png_b64(280, 280)})
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == ""
    assert body["found"] is False
    assert "vlm down" in body["error"]


def test_companion_guide_endpoint_thinking_budget_error_in_body(monkeypatch) -> None:
    client = _client(monkeypatch, _ThinkingExhaustedRouter("Denkprotokoll…"))
    res = client.post("/companion/guide", json={"question": "wo?", "image_base64": _png_b64(280, 280)})
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == ""
    assert "Token-Budget" in body["error"]


def test_companion_guide_endpoint_bad_image_in_body(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeRouter("{}"))
    res = client.post("/companion/guide", json={"question": "wo?", "image_base64": "%%%"})
    assert res.status_code == 200
    assert "dekodiert" in res.json()["error"]


def test_companion_ask_endpoint_happy_and_request_overrides(monkeypatch) -> None:
    router = _FakeRouter("Antwort.")
    client = _client(monkeypatch, router)
    res = client.post(
        "/companion/ask",
        json={
            "question": "Was ist das?",
            "image_base64": _png_b64(280, 280),
            "region": True,
            "provider": "anthropic",
            "model": "claude-sonnet-5",
            "history": [{"role": "user", "content": "vorher"}],
        },
    )
    assert res.status_code == 200
    assert res.json() == {"answer": "Antwort."}
    # Request-level provider/model beat the companion config defaults.
    assert router.calls[0]["provider"] == "anthropic"
    assert router.calls[0]["overrides"]["model"] == "claude-sonnet-5"


def test_companion_ask_endpoint_error_in_body(monkeypatch) -> None:
    client = _client(monkeypatch, _RaisingRouter())
    res = client.post("/companion/ask", json={"question": "hm?"})
    assert res.status_code == 200
    body = res.json()
    assert body["answer"] == ""
    assert "vlm down" in body["error"]


def test_companion_config_shape(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeRouter("{}"))
    cfg = client.get("/companion/config").json()
    assert cfg["provider"] == "lm_studio"
    assert cfg["model"] == "qwen/qwen3-vl-8b"
    assert cfg["language"] == "de"
    assert cfg["default_provider"] == "lm_studio"
    assert {"name": "lm_studio", "models": ["qwen/qwen3-vl-8b"]} in cfg["providers"]
    assert {"name": "anthropic", "models": ["claude-sonnet-5"]} in cfg["providers"]
