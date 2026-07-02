"""Tests for native Selbst-Steuerung (R7 skeleton): action planner + /selfdrive/* stubs.

Offline — the LLM router is faked; only Pillow does real work (synthetic PNGs)."""
from __future__ import annotations

import base64
import io
import json
from typing import Any

import api.product_main as product_main
from fastapi.testclient import TestClient

from query import self_drive


def _png_b64(width: int, height: int) -> str:
    from PIL import Image

    image = Image.new("RGB", (width, height), (20, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class _FakeRouter:
    default_provider = "lm_studio"

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, provider=None, overrides=None):
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides or {}})
        return self.reply

    def provider_default_model(self, provider=None):  # noqa: ARG002
        return "qwen/qwen3-vl-8b"

    def available_providers(self):
        return ["lm_studio"]

    def provider_model_options(self, provider=None, refresh=False):  # noqa: ARG002
        return ["qwen/qwen3-vl-8b"]


# --------------------------------------------------------------------------- #
# planner                                                                      #
# --------------------------------------------------------------------------- #

def _session(**kwargs: Any) -> self_drive.SelfDriveSession:
    return self_drive.SelfDriveSession(session_id="s1", goal="Öffne die Einstellungen", **kwargs)


def test_plan_step_scales_grid_action_to_pixels() -> None:
    reply = json.dumps(
        {"thought": "Ich klicke das Menü.", "action": {"type": "click", "x": 500, "y": 250}, "done": False}
    )
    router = _FakeRouter(reply)
    result = self_drive.plan_step(router, _session(), _png_b64(1000, 1000))
    assert result["action"]["type"] == "click"
    # 0-1000 grid over a 1000×1000 image → 500/1000·1000 = 500.
    assert result["action"]["x"] == 500.0
    assert result["action"]["y"] == 250.0
    assert result["done"] is False
    assert result["step"] == 1


def test_plan_step_unknown_action_degrades_to_wait() -> None:
    router = _FakeRouter(json.dumps({"thought": "?", "action": {"type": "launch_nukes"}, "done": False}))
    result = self_drive.plan_step(router, _session(), _png_b64(400, 400))
    assert result["action"] == {"type": "wait"}


def test_plan_step_broken_json_degrades_to_wait() -> None:
    router = _FakeRouter("kein json hier")
    result = self_drive.plan_step(router, _session(), _png_b64(400, 400))
    assert result["action"]["type"] == "wait"
    assert result["thought"]  # falls back to the raw text


def test_plan_step_done_marks_session_finished() -> None:
    router = _FakeRouter(json.dumps({"thought": "fertig", "action": {"type": "done"}, "done": True}))
    session = _session()
    result = self_drive.plan_step(router, session, _png_b64(400, 400))
    assert result["done"] is True
    assert session.finished is True


def test_plan_step_enforces_step_budget() -> None:
    router = _FakeRouter(json.dumps({"thought": "x", "action": {"type": "wait"}, "done": False}))
    session = _session(max_steps=2)
    self_drive.plan_step(router, session, _png_b64(400, 400))
    self_drive.plan_step(router, session, _png_b64(400, 400))
    # Third call is over budget → terminal fail without touching the model.
    calls_before = len(router.calls)
    result = self_drive.plan_step(router, session, _png_b64(400, 400))
    assert result["action"]["type"] == "fail"
    assert result["done"] is True
    assert len(router.calls) == calls_before  # model not called again


def test_type_and_key_actions_pass_through() -> None:
    reply = json.dumps({"thought": "tippe", "action": {"type": "type", "text": "hallo"}, "done": False})
    result = self_drive.plan_step(_FakeRouter(reply), _session(), _png_b64(400, 400))
    assert result["action"] == {"type": "type", "text": "hallo"}

    reply = json.dumps({"thought": "speichern", "action": {"type": "key", "keys": "ctrl+s"}, "done": False})
    result = self_drive.plan_step(_FakeRouter(reply), _session(), _png_b64(400, 400))
    assert result["action"] == {"type": "key", "keys": "ctrl+s"}


# --------------------------------------------------------------------------- #
# /selfdrive/* endpoints                                                       #
# --------------------------------------------------------------------------- #

def _client(monkeypatch, router, enabled: bool) -> TestClient:
    monkeypatch.setattr(product_main, "llm_router", router)
    monkeypatch.setattr(
        product_main,
        "_COMPANION_CONFIG_CACHE",
        {"provider": "lm_studio", "self_drive": {"enabled": enabled, "max_steps": 5}},
    )
    # Fresh store so sessions don't leak between tests.
    monkeypatch.setattr(product_main, "_SELF_DRIVE_STORE", self_drive.SelfDriveStore())
    return TestClient(product_main.app)


def test_selfdrive_disabled_by_default(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeRouter("{}"), enabled=False)
    res = client.post("/selfdrive/start", json={"goal": "tu was"})
    assert res.status_code == 200
    assert "deaktiviert" in res.json()["error"]


def test_selfdrive_start_step_stop_flow(monkeypatch) -> None:
    reply = json.dumps({"thought": "klick", "action": {"type": "click", "x": 100, "y": 100}, "done": False})
    client = _client(monkeypatch, _FakeRouter(reply), enabled=True)

    started = client.post("/selfdrive/start", json={"goal": "Öffne Datei"}).json()
    session_id = started["session_id"]
    assert session_id
    assert started["max_steps"] == 5

    step = client.post(
        "/selfdrive/step", json={"session_id": session_id, "image_base64": _png_b64(500, 500)}
    ).json()
    assert step["action"]["type"] == "click"

    stopped = client.post("/selfdrive/stop", json={"session_id": session_id}).json()
    assert stopped["stopped"] is True


def test_selfdrive_step_unknown_session(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeRouter("{}"), enabled=True)
    res = client.post("/selfdrive/step", json={"session_id": "nope", "image_base64": _png_b64(100, 100)})
    assert "Unbekannte" in res.json()["error"]
