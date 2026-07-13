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
# R7 pipeline: expectation / verify / stall / refine / lookup / ask            #
# --------------------------------------------------------------------------- #

_VERIFY_CFG = {"enabled": True, "pixel_diff_threshold": 1.5, "max_tokens": 400}
_REFINE_CFG = {"enabled": True, "crop_px": 200, "zoom": 2.0, "max_tokens": 300}
_LOOKUP_CFG = {"enabled": True, "use_web": True, "max_per_session": 3}


def _plan_reply(action: dict, thought: str = "t", expectation: str = "Das Menü öffnet sich", done: bool = False) -> str:
    return json.dumps({"thought": thought, "action": action, "expectation": expectation, "done": done})


def test_expectation_recorded_as_pending() -> None:
    reply = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Startmenü"})
    session = _session()
    result = self_drive.plan_step(_FakeRouter(reply), session, _png_b64(1000, 1000), verify_cfg=_VERIFY_CFG)
    assert result["expectation"] == "Das Menü öffnet sich"
    assert session.pending_expectation == "Das Menü öffnet sich"
    assert session.pending_action["type"] == "click"
    assert session.last_image_thumb


def test_unchanged_screen_counts_as_failure_without_vlm_call() -> None:
    click = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Startmenü"})
    router = _FakeRouter(click)
    session = _session()
    image = _png_b64(600, 600)
    self_drive.plan_step(router, session, image, verify_cfg=_VERIFY_CFG)
    result = self_drive.plan_step(router, session, image, verify_cfg=_VERIFY_CFG)
    # Identical frame → pixel-diff shortcut, no verify VLM call (2 calls = 2 plans).
    assert len(router.calls) == 2
    assert result["verification"]["ok"] is False
    assert session.consecutive_failures == 1
    assert any("NICHT funktioniert" in m["content"] for m in session.history)


def test_verify_success_resets_failure_counter() -> None:
    click = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Startmenü"})
    verify_ok = json.dumps({"beobachtung": "Menü offen.", "erfuellt": True, "hinweis": ""})
    router = _FakeRouter(click, verify_ok, click)
    session = _session()
    session.consecutive_failures = 2
    self_drive.plan_step(router, session, _png_b64(600, 600, ), verify_cfg=_VERIFY_CFG)
    # Different frame → real verify call happens before the second plan.
    from PIL import Image
    import io as _io

    bright = Image.new("RGB", (600, 600), (220, 220, 220))
    buffer = _io.BytesIO()
    bright.save(buffer, format="PNG")
    bright_b64 = base64.b64encode(buffer.getvalue()).decode("ascii")
    result = self_drive.plan_step(router, session, bright_b64, verify_cfg=_VERIFY_CFG)
    assert result["verification"]["ok"] is True
    assert session.consecutive_failures == 0
    assert any("war erfolgreich" in m["content"] for m in session.history)


def test_forced_ask_after_consecutive_failures() -> None:
    click = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Startmenü"})
    router = _FakeRouter(click)
    session = _session()
    image = _png_b64(600, 600)
    self_drive.plan_step(router, session, image, verify_cfg=_VERIFY_CFG, max_consecutive_failures=1)
    result = self_drive.plan_step(router, session, image, verify_cfg=_VERIFY_CFG, max_consecutive_failures=1)
    # Second call: verify fails (unchanged screen) → threshold reached → forced ask.
    assert result["action"]["type"] == "ask"
    assert "Wie soll ich fortfahren" in result["action"]["question"]
    assert result["done"] is False
    assert session.help_requests == 1
    assert len(router.calls) == 1  # no planning call for the forced ask


def test_second_stall_fails_terminally() -> None:
    session = _session()
    session.help_requests = 1
    session.consecutive_failures = 99
    result = self_drive.plan_step(_FakeRouter("{}"), session, _png_b64(400, 400), verify_cfg=_VERIFY_CFG)
    assert result["action"]["type"] == "fail"
    assert result["done"] is True
    assert session.finished is True


def test_stall_on_repeated_identical_clicks() -> None:
    session = _session()
    session.recent_actions = [{"type": "click", "x": 100.0, "y": 100.0} for _ in range(3)]
    router = _FakeRouter("{}")
    result = self_drive.plan_step(router, session, _png_b64(400, 400))
    assert result["action"]["type"] == "ask"
    assert len(router.calls) == 0


def test_refine_overwrites_click_coords() -> None:
    plan = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Knopf"})
    refine = json.dumps({"x": 0, "y": 0})  # crop origin
    router = _FakeRouter(plan, refine)
    result = self_drive.plan_step(router, _session(), _png_b64(1000, 1000), refine_cfg=_REFINE_CFG)
    # Coarse point (500, 250), crop 200px → origin (400, 150); grid (0,0) = origin.
    assert (result["action"]["x"], result["action"]["y"]) == (400.0, 150.0)
    assert result["refined"] is True
    assert len(router.calls) == 2


def test_refine_skipped_without_label() -> None:
    plan = _plan_reply({"type": "click", "x": 500, "y": 250})
    router = _FakeRouter(plan)
    result = self_drive.plan_step(router, _session(), _png_b64(1000, 1000), refine_cfg=_REFINE_CFG)
    assert result["refined"] is False
    assert len(router.calls) == 1


def test_lookup_passes_through_when_enabled() -> None:
    plan = _plan_reply({"type": "lookup", "query": "Wie öffnet man in GIMP Ebenen?"}, expectation="")
    result = self_drive.plan_step(_FakeRouter(plan), _session(), _png_b64(400, 400), lookup_cfg=_LOOKUP_CFG)
    assert result["action"] == {"type": "lookup", "query": "Wie öffnet man in GIMP Ebenen?"}
    assert result["done"] is False


def test_lookup_degrades_to_wait_when_disabled() -> None:
    plan = _plan_reply({"type": "lookup", "query": "irgendwas"}, expectation="")
    result = self_drive.plan_step(_FakeRouter(plan), _session(), _png_b64(400, 400))
    assert result["action"] == {"type": "wait"}


def test_lookup_budget_exhausted_degrades_to_wait() -> None:
    plan = _plan_reply({"type": "lookup", "query": "irgendwas"}, expectation="")
    session = _session()
    session.lookup_count = 3
    result = self_drive.plan_step(_FakeRouter(plan), session, _png_b64(400, 400), lookup_cfg=_LOOKUP_CFG)
    assert result["action"] == {"type": "wait"}


def test_ask_action_passes_through() -> None:
    plan = _plan_reply({"type": "ask", "question": "Welche Datei meinst du?"}, expectation="")
    result = self_drive.plan_step(_FakeRouter(plan), _session(), _png_b64(400, 400))
    assert result["action"] == {"type": "ask", "question": "Welche Datei meinst du?"}


def test_inject_lookup_result_and_user_answer() -> None:
    session = _session()
    self_drive.inject_lookup_result(session, "GIMP Ebenen", ["Treffer 1", "Treffer 2"])
    assert session.lookup_count == 1
    assert "GIMP Ebenen" in session.history[-1]["content"]
    assert "Treffer 1" in session.history[-1]["content"]
    self_drive.inject_user_answer(session, "Nimm die zweite Datei.")
    assert session.history[-1]["content"] == "Antwort des Nutzers: Nimm die zweite Datei."


# --------------------------------------------------------------------------- #
# Sensitive-target safeguard                                                   #
# --------------------------------------------------------------------------- #

def test_classify_sensitive_german_password_label() -> None:
    sensitive, reason = self_drive.classify_sensitive(
        {"type": "click", "label": "Passwort-Eingabefeld"}
    )
    assert sensitive is True
    assert "passwort" in reason.lower()
    assert "Ziel-Element" in reason


def test_classify_sensitive_english_buy_button() -> None:
    sensitive, reason = self_drive.classify_sensitive({"type": "click", "label": "Buy now button"})
    assert sensitive is True
    assert "buy now" in reason.lower()


def test_classify_sensitive_keyword_in_thought_for_type_action() -> None:
    sensitive, reason = self_drive.classify_sensitive(
        {"type": "type", "text": "hunter2"}, thought="Ich gebe das Passwort ein."
    )
    assert sensitive is True
    assert "Begründung" in reason


def test_classify_sensitive_respects_word_boundaries() -> None:
    # "buy" inside "buyer's guide" must not trip the safeguard.
    sensitive, _ = self_drive.classify_sensitive({"type": "click", "label": "Buyers Guide öffnen"})
    assert sensitive is False


def test_classify_sensitive_extra_keywords() -> None:
    sensitive, reason = self_drive.classify_sensitive(
        {"type": "click", "label": "Produktionsdatenbank"},
        extra_keywords=["produktionsdatenbank"],
    )
    assert sensitive is True
    assert "produktionsdatenbank" in reason.lower()


def test_classify_sensitive_harmless_action() -> None:
    sensitive, reason = self_drive.classify_sensitive(
        {"type": "click", "label": "Startmenü"},
        thought="Ich öffne das Startmenü.",
        expectation="Das Startmenü öffnet sich.",
    )
    assert sensitive is False
    assert reason is None


def test_plan_step_flags_sensitive_action() -> None:
    plan = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Jetzt kaufen"})
    result = self_drive.plan_step(
        _FakeRouter(plan), _session(), _png_b64(1000, 1000), sensitive_cfg={"enabled": True}
    )
    assert result["action"]["sensitive"] is True
    assert "kaufen" in result["action"]["sensitive_reason"].lower()


def test_plan_step_sensitive_disabled_leaves_action_unflagged() -> None:
    plan = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Jetzt kaufen"})
    result = self_drive.plan_step(
        _FakeRouter(plan), _session(), _png_b64(1000, 1000), sensitive_cfg={"enabled": False}
    )
    assert "sensitive" not in result["action"]


def test_plan_step_harmless_action_unflagged() -> None:
    plan = _plan_reply({"type": "click", "x": 500, "y": 250, "label": "Startmenü"}, thought="klick")
    result = self_drive.plan_step(_FakeRouter(plan), _session(), _png_b64(1000, 1000))
    assert "sensitive" not in result["action"]


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


def test_selfdrive_step_returns_sensitive_flag(monkeypatch) -> None:
    reply = json.dumps(
        {
            "thought": "klick",
            "action": {"type": "click", "x": 100, "y": 100, "label": "Jetzt kaufen"},
            "done": False,
        }
    )
    client = _client(monkeypatch, _FakeRouter(reply), enabled=True)
    started = client.post("/selfdrive/start", json={"goal": "Bestelle das Buch"}).json()
    step = client.post(
        "/selfdrive/step",
        json={"session_id": started["session_id"], "image_base64": _png_b64(500, 500)},
    ).json()
    assert step["action"]["sensitive"] is True
    assert "kaufen" in step["action"]["sensitive_reason"].lower()


def test_companion_config_exposes_safety_settings(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeRouter("{}"), enabled=True)
    cfg = client.get("/companion/config").json()["self_drive"]
    # Defaults apply when the keys are absent from the config cache.
    assert cfg["mouse_abort_px"] == 150
    assert cfg["action_timeout_ms"] == 5000
    assert cfg["step_timeout_ms"] == 120000
