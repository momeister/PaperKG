"""Tests for zoom-refine grounding + verification primitives (R7 rework).

Offline — the LLM router is faked; only Pillow does real work (synthetic PNGs)."""
from __future__ import annotations

import base64
import io
import json
from typing import Any

import pytest

from query import screen_grounding


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


# --------------------------------------------------------------------------- #
# crop_around                                                                  #
# --------------------------------------------------------------------------- #

def test_crop_around_centers_on_point() -> None:
    from PIL import Image

    image = Image.new("RGB", (1000, 800))
    crop, ox, oy = screen_grounding.crop_around(image, 500, 400, 200)
    assert crop.size == (200, 200)
    assert (ox, oy) == (400, 300)


def test_crop_around_clamps_at_edges() -> None:
    from PIL import Image

    image = Image.new("RGB", (1000, 800))
    crop, ox, oy = screen_grounding.crop_around(image, 10, 790, 200)
    assert crop.size == (200, 200)
    assert (ox, oy) == (0, 600)  # pinned to top-left x, bottom y


def test_crop_around_small_image_uses_full_frame() -> None:
    from PIL import Image

    image = Image.new("RGB", (120, 90))
    crop, ox, oy = screen_grounding.crop_around(image, 60, 45, 448)
    assert crop.size == (120, 90)
    assert (ox, oy) == (0, 0)


# --------------------------------------------------------------------------- #
# refine_point                                                                 #
# --------------------------------------------------------------------------- #

def test_refine_point_maps_grid_back_to_original_pixels() -> None:
    # Crop is 400px centered at (500, 400) → origin (300, 200). Grid (500, 500)
    # is the crop center → original (500.0, 400.0).
    router = _FakeRouter(json.dumps({"x": 500, "y": 500}))
    result = screen_grounding.refine_point(
        router, _png_b64(1000, 800), 500, 400, "Speichern-Knopf", crop_px=400
    )
    assert result["refined"] is True
    assert result["x"] == 500.0
    assert result["y"] == 400.0


def test_refine_point_grid_corner_maps_to_crop_origin() -> None:
    router = _FakeRouter(json.dumps({"x": 0, "y": 0}))
    result = screen_grounding.refine_point(
        router, _png_b64(1000, 800), 500, 400, "Ziel", crop_px=400
    )
    assert (result["x"], result["y"]) == (300.0, 200.0)


def test_refine_point_garbage_reply_falls_back_to_coarse() -> None:
    router = _FakeRouter("kein json")
    result = screen_grounding.refine_point(router, _png_b64(600, 600), 123.4, 234.5, "Ziel")
    assert result == {"x": 123.4, "y": 234.5, "refined": False}


def test_refine_point_out_of_range_falls_back_to_coarse() -> None:
    router = _FakeRouter(json.dumps({"x": 5000, "y": 20}))
    result = screen_grounding.refine_point(router, _png_b64(600, 600), 100, 100, "Ziel")
    assert result["refined"] is False


def test_refine_point_label_lands_in_prompt() -> None:
    router = _FakeRouter(json.dumps({"x": 500, "y": 500}))
    screen_grounding.refine_point(router, _png_b64(600, 600), 100, 100, "Datei-Menü")
    system = router.calls[0]["messages"][0]["content"]
    assert "Datei-Menü" in system


# --------------------------------------------------------------------------- #
# screen_changed                                                               #
# --------------------------------------------------------------------------- #

def test_screen_changed_identical_frames_near_zero() -> None:
    b64 = _png_b64(300, 300)
    assert screen_grounding.screen_changed(b64, b64) == pytest.approx(0.0)


def test_screen_changed_different_frames_positive() -> None:
    before = _png_b64(300, 300, color=(20, 20, 20))
    after = _png_b64(300, 300, color=(200, 200, 200))
    assert screen_grounding.screen_changed(before, after) > 50


# --------------------------------------------------------------------------- #
# verify_expectation                                                           #
# --------------------------------------------------------------------------- #

def test_verify_expectation_reports_mismatch_with_note() -> None:
    reply = json.dumps(
        {"beobachtung": "Der Desktop ist unverändert.", "erfuellt": False, "hinweis": "Kein Menü offen."}
    )
    router = _FakeRouter(reply)
    result = screen_grounding.verify_expectation(
        router, _png_b64(400, 400), "Klick auf (100, 100) — 'Startmenü'", "Das Startmenü öffnet sich"
    )
    assert result["matches"] is False
    assert "Kein Menü" in result["note"]


def test_verify_expectation_success() -> None:
    reply = json.dumps({"beobachtung": "Menü ist offen.", "erfuellt": True, "hinweis": ""})
    router = _FakeRouter(reply)
    result = screen_grounding.verify_expectation(router, _png_b64(400, 400), "Klick", "Menü offen")
    assert result["matches"] is True


def test_verify_expectation_unparseable_is_inconclusive() -> None:
    router = _FakeRouter("???")
    result = screen_grounding.verify_expectation(router, _png_b64(400, 400), "Klick", "Menü offen")
    assert result == {"matches": True, "note": ""}


def test_verify_expectation_prompt_contains_action_and_expectation() -> None:
    router = _FakeRouter(json.dumps({"beobachtung": "x", "erfuellt": True, "hinweis": ""}))
    screen_grounding.verify_expectation(router, _png_b64(400, 400), "Klick auf 'Speichern'", "Dialog offen")
    system = router.calls[0]["messages"][0]["content"]
    assert "Klick auf 'Speichern'" in system
    assert "Dialog offen" in system
