"""Zoom-refine grounding + action verification for the Desktop Companion (R7 rework).

Local VLMs (qwen3-vl-8b class) ground coarsely on a full downscaled monitor frame —
often 20-60 px off, enough to miss a toolbar button. Two primitives fix the two
failure modes:

- :func:`refine_point` — second grounding pass on a zoomed crop around the coarse
  point, cut from the **original full-resolution** screenshot (the full-frame pass
  only ever saw a downscaled image, so the crop genuinely adds detail).
- :func:`verify_expectation` — after an action executed, judge the *after* screenshot
  against the planner's stated expectation (describe-then-judge to counter yes-bias),
  so the planner learns it clicked the wrong element instead of wondering.
  :func:`screen_changed` is the cheap pixel-diff pre-check ("nothing happened at all")
  that costs no VLM call.

Everything degrades instead of raising: parse failures return the coarse point /
an inconclusive verdict. Only LLM/transport errors propagate (same contract as
``screen_companion.guide``); callers in the planning loop wrap those.
"""
from __future__ import annotations

import base64
import io
from typing import Any

from query.llm_router import LLMRouter
from query.screen_companion import (
    COORD_GRID,
    DEFAULT_MAX_PIXELS,
    _no_think_suffix,
    _raise_if_thinking_exhausted,
    _strip_data_url,
    _user_content,
    prepare_image,
    smart_resize_to_budget,
)

DEFAULT_CROP_PX = 448
DEFAULT_ZOOM = 3.0
DEFAULT_REFINE_MAX_TOKENS = 300
DEFAULT_VERIFY_MAX_TOKENS = 400
# Grayscale thumbnail edge for the pixel diff — coarse on purpose: it only has to
# distinguish "screen visibly changed" from "screen is static".
_DIFF_THUMB = 64


def decode_image(image_base64: str) -> Any:
    """Decode a (data-URL or bare) base64 image into a loaded PIL image.
    Raises ``ValueError`` on undecodable input (mirrors ``prepare_image``)."""
    from PIL import Image

    try:
        raw = base64.b64decode(_strip_data_url(image_base64), validate=False)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise ValueError(f"Bild konnte nicht dekodiert werden: {exc}") from exc
    return image


def crop_around(image: Any, x: float, y: float, crop_px: int) -> tuple[Any, int, int]:
    """Square crop of side ``crop_px`` centered on (x, y), clamped so it stays fully
    inside the frame. Returns ``(crop, origin_x, origin_y)`` in original pixels."""
    width, height = image.size
    side_w = min(int(crop_px), width)
    side_h = min(int(crop_px), height)
    ox = int(min(max(x - side_w / 2, 0), width - side_w))
    oy = int(min(max(y - side_h / 2, 0), height - side_h))
    return image.crop((ox, oy, ox + side_w, oy + side_h)), ox, oy


def _refine_system(target_label: str) -> str:
    label = " ".join(str(target_label or "").split()).strip()[:120] or "das Ziel-Element"
    return (
        "Du siehst einen vergrößerten Ausschnitt eines Bildschirms. "
        f"Wo genau liegt die Mitte von: {label}?\n"
        'Antworte AUSSCHLIESSLICH mit einem JSON-Objekt {"x": <int>, "y": <int>}. '
        "x/y liegen auf einem 0-1000-Raster über DIESEN Ausschnitt — "
        "(0,0) ist die linke obere Ecke, (1000,1000) die rechte untere. "
        "Kein Text außerhalb des JSON."
    )


def refine_point(
    router: Any,
    image_base64: str,
    x: float,
    y: float,
    target_label: str,
    *,
    crop_px: int = DEFAULT_CROP_PX,
    zoom: float = DEFAULT_ZOOM,
    provider: str | None = None,
    model: str | None = None,
    max_tokens: int = DEFAULT_REFINE_MAX_TOKENS,
    disable_thinking: bool = True,
) -> dict[str, Any]:
    """Second grounding pass: zoom into the coarse point and re-ask for the target.

    Returns ``{"x", "y", "refined"}`` in original-screenshot pixels. Any parse
    failure or out-of-range reply falls back to the coarse point (``refined: False``).
    Raises only on decode/LLM/transport errors."""
    from PIL import Image

    image = decode_image(image_base64)
    crop, crop_ox, crop_oy = crop_around(image, x, y, crop_px)
    crop_w, crop_h = crop.size

    # Upscale so small UI elements become legible, then re-apply the Qwen pixel
    # budget — a 448px crop at 3x would otherwise exceed what the server accepts.
    zoomed_w, zoomed_h = max(1, int(crop_w * zoom)), max(1, int(crop_h * zoom))
    sent_w, sent_h = smart_resize_to_budget(zoomed_w, zoomed_h)
    zoomed = crop.resize((sent_w, sent_h), Image.Resampling.LANCZOS)
    if zoomed.mode not in ("RGB", "RGBA"):
        zoomed = zoomed.convert("RGB")
    buffer = io.BytesIO()
    zoomed.save(buffer, format="PNG")
    data_url = "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")

    system = _refine_system(target_label) + _no_think_suffix(router, provider, model, disable_thinking)
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": _user_content("Wo genau?", data_url)},
    ]
    overrides: dict[str, Any] = {
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "extra": {"json_mode": True},
    }
    if model:
        overrides["model"] = model
    raw = router.chat(messages, provider=provider, overrides=overrides)
    _raise_if_thinking_exhausted(router)

    coarse = {"x": round(float(x), 1), "y": round(float(y), 1), "refined": False}
    try:
        data = LLMRouter._extract_json(raw)
        gx, gy = float(data.get("x")), float(data.get("y"))
    except Exception:
        return coarse
    if not (0 <= gx <= COORD_GRID and 0 <= gy <= COORD_GRID):
        return coarse
    rx = crop_ox + gx / COORD_GRID * crop_w
    ry = crop_oy + gy / COORD_GRID * crop_h
    width, height = image.size
    rx = min(max(rx, 0.0), width - 1.0)
    ry = min(max(ry, 0.0), height - 1.0)
    return {"x": round(rx, 1), "y": round(ry, 1), "refined": True}


def thumb_bytes(image_base64: str) -> bytes:
    """Tiny grayscale thumbnail of a screenshot — cheap to keep per session as the
    "before" frame for :func:`thumb_diff` (full screenshots would be megabytes)."""
    from PIL import Image

    return (
        decode_image(image_base64)
        .convert("L")
        .resize((_DIFF_THUMB, _DIFF_THUMB), Image.Resampling.BILINEAR)
        .tobytes()
    )


def thumb_diff(before: bytes, after: bytes) -> float:
    """Mean absolute grayscale difference (0-255) between two thumbnails."""
    if not before or not after or len(before) != len(after):
        return 255.0
    return sum(abs(pa - pb) for pa, pb in zip(before, after)) / len(before)


def screen_changed(before_base64: str, after_base64: str) -> float:
    """Mean absolute grayscale difference (0-255) between two screenshots, computed
    on tiny thumbnails. Near zero ⇒ the screen did not visibly change."""
    return thumb_diff(thumb_bytes(before_base64), thumb_bytes(after_base64))


def _verify_system(action_desc: str, expectation: str) -> str:
    return (
        "Du prüfst, ob eine Computer-Aktion die erwartete Wirkung hatte. "
        "Du siehst den Bildschirm NACH der Aktion.\n"
        f"Ausgeführte Aktion: {action_desc}\n"
        f"Erwartete sichtbare Wirkung: {expectation}\n"
        "Beschreibe ZUERST neutral, was im Bild zu sehen ist, und urteile DANACH. "
        "Sei kritisch: wenn die erwartete Wirkung nicht klar erkennbar ist, ist sie "
        "NICHT erfüllt.\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:\n"
        '{"beobachtung": "<was sichtbar ist>", "erfuellt": <bool>, '
        '"hinweis": "<falls nicht erfüllt: was stattdessen passiert ist>"}\n'
        "Kein Text außerhalb des JSON."
    )


def verify_expectation(
    router: Any,
    after_base64: str,
    action_desc: str,
    expectation: str,
    *,
    provider: str | None = None,
    model: str | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    max_tokens: int = DEFAULT_VERIFY_MAX_TOKENS,
    disable_thinking: bool = True,
) -> dict[str, Any]:
    """Judge the after-screenshot against the planner's expectation.

    Returns ``{"matches": bool, "note": str}``. The verdict is advisory feedback
    for the planner, never a hard gate; an unparseable reply counts as inconclusive
    (``matches: True``, empty note) so no false failure is injected."""
    prepared = prepare_image(after_base64, max_pixels=max_pixels)
    system = _verify_system(action_desc, expectation) + _no_think_suffix(
        router, provider, model, disable_thinking
    )
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": _user_content("Wurde die Erwartung erfüllt?", prepared.data_url)},
    ]
    overrides: dict[str, Any] = {
        "temperature": 0.0,
        "max_tokens": max_tokens,
        "extra": {"json_mode": True},
    }
    if model:
        overrides["model"] = model
    raw = router.chat(messages, provider=provider, overrides=overrides)
    _raise_if_thinking_exhausted(router)

    try:
        data = LLMRouter._extract_json(raw)
    except Exception:
        return {"matches": True, "note": ""}
    if "erfuellt" not in data:
        return {"matches": True, "note": ""}
    matches = bool(data.get("erfuellt"))
    note = " ".join(str(data.get("hinweis") or data.get("beobachtung") or "").split()).strip()[:300]
    return {"matches": matches, "note": note}
