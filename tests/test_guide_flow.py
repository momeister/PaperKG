"""Tests for incremental guided sequences (R7): guide_flow module + /companion/guide/*.

Offline — the LLM router is faked; only Pillow does real work (synthetic PNGs)."""
from __future__ import annotations

import base64
import io
import json
from typing import Any

import api.product_main as product_main
from fastapi.testclient import TestClient

from query import guide_flow


def _png_b64(width: int, height: int, color=(20, 20, 20)) -> str:
    from PIL import Image

    image = Image.new("RGB", (width, height), color)
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class _FakeRouter:
    """Scripted router: pops one reply per chat() call (last reply repeats)."""

    default_provider = "lm_studio"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, provider=None, overrides=None):
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides or {}})
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]

    def provider_default_model(self, provider=None):  # noqa: ARG002
        return "qwen/qwen3-vl-8b"

    def available_providers(self):
        return ["lm_studio"]

    def provider_model_options(self, provider=None, refresh=False):  # noqa: ARG002
        return ["qwen/qwen3-vl-8b"]


def _guide_reply(
    instruction: str = "Klicke auf das Datei-Menü.",
    step: dict | None = None,
    expectation: str = "Das Menü öffnet sich",
    done: bool = False,
) -> str:
    return json.dumps(
        {"instruction": instruction, "step": step, "expectation": expectation, "done": done}
    )


def _session(**kwargs: Any) -> guide_flow.GuideSession:
    return guide_flow.GuideSession(guide_id="g1", goal="Öffne die Einstellungen", **kwargs)


_VERIFY_CFG = {"enabled": True, "pixel_diff_threshold": 1.5, "max_tokens": 400}


# --------------------------------------------------------------------------- #
# guide_flow module                                                            #
# --------------------------------------------------------------------------- #

def test_plan_next_scales_step_and_records_pending() -> None:
    reply = _guide_reply(step={"x": 500, "y": 250, "label": "Datei-Menü"})
    session = _session()
    result = guide_flow.plan_next(
        _FakeRouter(reply), session, _png_b64(1000, 1000), verify_cfg=_VERIFY_CFG
    )
    assert result["instruction"] == "Klicke auf das Datei-Menü."
    assert result["step"] == {"x": 500.0, "y": 250.0, "label": "Datei-Menü"}
    assert result["done"] is False
    assert session.pending_expectation == "Das Menü öffnet sich"
    assert session.step_index == 1


def test_plan_next_advice_only_step_is_null() -> None:
    reply = _guide_reply(instruction="Scrolle nach unten.", step=None, expectation="")
    result = guide_flow.plan_next(_FakeRouter(reply), _session(), _png_b64(400, 400))
    assert result["step"] is None
    assert result["done"] is False


def test_plan_next_done_finishes_session() -> None:
    reply = _guide_reply(instruction="Fertig!", step=None, expectation="", done=True)
    session = _session()
    result = guide_flow.plan_next(_FakeRouter(reply), session, _png_b64(400, 400))
    assert result["done"] is True
    assert session.finished is True


def test_plan_next_click_verify_failure_feedback() -> None:
    plan1 = _guide_reply(step={"x": 500, "y": 250, "label": "Datei-Menü"})
    plan2 = _guide_reply(instruction="Versuch es weiter links.", step={"x": 300, "y": 250, "label": "Datei"})
    router = _FakeRouter(plan1, plan2)
    session = _session()
    image = _png_b64(600, 600)
    guide_flow.plan_next(router, session, image, verify_cfg=_VERIFY_CFG)
    # Same frame after the click → pixel-diff shortcut says "nothing changed".
    result = guide_flow.plan_next(
        router, session, image, user_event="click", click_x=500, click_y=250, verify_cfg=_VERIFY_CFG
    )
    assert result["verification"]["ok"] is False
    assert len(router.calls) == 2  # no verify VLM call, diff shortcut fired
    assert any("hat nicht" in m["content"] for m in session.history)


def test_plan_next_skip_clears_pending() -> None:
    plan1 = _guide_reply(step={"x": 500, "y": 250, "label": "Menü"})
    router = _FakeRouter(plan1, _guide_reply(instruction="Weiter.", step=None, expectation=""))
    session = _session()
    guide_flow.plan_next(router, session, _png_b64(400, 400), verify_cfg=_VERIFY_CFG)
    result = guide_flow.plan_next(
        router, session, _png_b64(400, 400), user_event="skip", verify_cfg=_VERIFY_CFG
    )
    assert result["verification"] is None
    assert session.pending_expectation is None
    assert any("übersprungen" in m["content"] for m in session.history)


def test_plan_next_step_budget() -> None:
    session = _session(max_steps=1)
    router = _FakeRouter(_guide_reply(step=None, expectation=""))
    guide_flow.plan_next(router, session, _png_b64(400, 400))
    result = guide_flow.plan_next(router, session, _png_b64(400, 400))
    assert result["done"] is True
    assert "Schrittbudget" in result["instruction"]
    assert len(router.calls) == 1


def test_plan_next_refines_step_point() -> None:
    plan = _guide_reply(step={"x": 500, "y": 250, "label": "Knopf"})
    refine = json.dumps({"x": 0, "y": 0})
    router = _FakeRouter(plan, refine)
    result = guide_flow.plan_next(
        router,
        _session(),
        _png_b64(1000, 1000),
        refine_cfg={"enabled": True, "crop_px": 400, "zoom": 2.0},
    )
    # Coarse (500, 250), crop 400px → origin (300, 50); grid (0,0) = origin.
    assert (result["step"]["x"], result["step"]["y"]) == (300.0, 50.0)


def test_plan_next_context_blocks_land_in_prompt() -> None:
    router = _FakeRouter(_guide_reply(step=None, expectation=""))
    session = _session(context_blocks=["(Web: example.org) Anleitung — Schritt 1"])
    guide_flow.plan_next(router, session, _png_b64(400, 400))
    system = router.calls[0]["messages"][0]["content"]
    assert "example.org" in system
    assert "DATEN" in system  # untrusted-data rule from _SOURCES_HINT


# --------------------------------------------------------------------------- #
# /companion/guide/* endpoints                                                 #
# --------------------------------------------------------------------------- #

def _client(monkeypatch, router) -> TestClient:
    monkeypatch.setattr(product_main, "llm_router", router)
    monkeypatch.setattr(
        product_main,
        "_COMPANION_CONFIG_CACHE",
        {
            "provider": "lm_studio",
            "guide": {"max_steps": 4, "click_settle_ms": 500},
            "verify": {"enabled": True, "settle_ms": 800},
        },
    )
    monkeypatch.setattr(product_main, "_GUIDE_STORE", guide_flow.GuideStore())
    return TestClient(product_main.app)


def test_guide_start_step_stop_flow(monkeypatch) -> None:
    reply = _guide_reply(step={"x": 100, "y": 100, "label": "Start"})
    client = _client(monkeypatch, _FakeRouter(reply))

    started = client.post("/companion/guide/start", json={"goal": "Öffne Einstellungen"}).json()
    assert started["max_steps"] == 4
    assert started["click_settle_ms"] == 500
    guide_id = started["guide_id"]

    step = client.post(
        "/companion/guide/step",
        json={"guide_id": guide_id, "image_base64": _png_b64(500, 500), "event": "start"},
    ).json()
    assert step["instruction"]
    assert step["step"]["label"] == "Start"

    stopped = client.post("/companion/guide/stop", json={"guide_id": guide_id}).json()
    assert stopped["stopped"] is True


def test_guide_step_unknown_session(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeRouter("{}"))
    res = client.post(
        "/companion/guide/step",
        json={"guide_id": "nope", "image_base64": _png_b64(100, 100), "event": "start"},
    ).json()
    assert "Unbekannte" in res["error"]
