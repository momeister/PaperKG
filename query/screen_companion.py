"""Screen-aware **Desktop Companion**: answer questions about a screenshot and ground
"zeig mir" guidance points — without UI-TARS.

The native shell (src-tauri/src/capture.rs) captures the primary monitor and POSTs it
to `/companion/*`; this module prepares the image, talks to a vision-capable model via
the normal :class:`~query.llm_router.LLMRouter` (local LM Studio/Ollama VLMs or the
`anthropic` provider), and returns a German answer plus optional ordered click-guidance
steps in **original screenshot pixels**. The companion only *points* — it never drives
mouse or keyboard (that stays with the legacy UI-TARS bridge, `query/agent_handoff.py`).

Coordinate correctness hinges on resizing the screenshot *ourselves* before the VLM
sees it: OpenAI-compatible servers (LM Studio, Ollama) silently downscale big images to
their own vision budget, so a model's pixel coordinates would be in that unknown smaller
frame (the old wrong-pointer bug, docs/NATIVE_APP.md R5.3). :func:`smart_resize_to_budget`
is a 1:1 port of the fix in ``bridge/uitars/server.mjs``.
"""
from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass
from typing import Any

from query.llm_router import LLMRouter

# Qwen-VL image-processor convention: dimensions are multiples of 28 and the total
# area stays inside a pixel budget the serving stack won't shrink further.
IMAGE_FACTOR = 28
DEFAULT_MAX_PIXELS = 1280 * IMAGE_FACTOR * IMAGE_FACTOR
DEFAULT_MIN_PIXELS = 100 * IMAGE_FACTOR * IMAGE_FACTOR

# A guidance answer degrades past a handful of steps — the pointer shows one at a time.
MAX_GUIDE_STEPS = 6
DEFAULT_HISTORY_TURNS = 8

# Token budgets with headroom for reasoning models (Qwen3.x "thinking" builds burn
# hundreds of tokens before the first answer token — the old 900 cap meant the
# whole budget went into <think> and the user saw raw chain-of-thought).
DEFAULT_MAX_TOKENS_GUIDE = 2000
DEFAULT_MAX_TOKENS_ASK = 1500


def smart_resize_to_budget(
    width: int,
    height: int,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    min_pixels: int = DEFAULT_MIN_PIXELS,
) -> tuple[int, int]:
    """Smart-resize (Qwen2.5-VL convention) to 28-multiples within [min, max] pixels.

    Port of ``smartResizeToBudget`` in ``bridge/uitars/server.mjs`` (uses JS
    ``Math.round`` semantics, i.e. halves round up)."""

    def _round(n: float) -> int:
        return int(math.floor(n / IMAGE_FACTOR + 0.5)) * IMAGE_FACTOR

    def _floor(n: float) -> int:
        return int(math.floor(n / IMAGE_FACTOR)) * IMAGE_FACTOR

    def _ceil(n: float) -> int:
        return int(math.ceil(n / IMAGE_FACTOR)) * IMAGE_FACTOR

    w_bar = max(IMAGE_FACTOR, _round(width))
    h_bar = max(IMAGE_FACTOR, _round(height))
    if w_bar * h_bar > max_pixels:
        beta = math.sqrt((width * height) / max_pixels)
        w_bar = max(IMAGE_FACTOR, _floor(width / beta))
        h_bar = max(IMAGE_FACTOR, _floor(height / beta))
    elif w_bar * h_bar < min_pixels:
        beta = math.sqrt(min_pixels / (width * height))
        w_bar = _ceil(width * beta)
        h_bar = _ceil(height * beta)
    return w_bar, h_bar


@dataclass
class PreparedImage:
    """A screenshot ready for the VLM: re-encoded PNG data URL in a known frame."""

    data_url: str
    width: int  # original pixels (= physical monitor pixels for full captures)
    height: int
    sent_width: int  # the frame the VLM actually sees; its coordinates live here
    sent_height: int


def _strip_data_url(image_base64: str) -> str:
    if image_base64.startswith("data:"):
        return image_base64.partition(",")[2]
    return image_base64


def prepare_image(image_base64: str, max_pixels: int = DEFAULT_MAX_PIXELS) -> PreparedImage:
    """Decode a (data-URL or bare) base64 image, smart-resize into the pixel budget and
    re-encode as a PNG data URL. Raises ``ValueError`` on undecodable input."""
    from PIL import Image

    try:
        raw = base64.b64decode(_strip_data_url(image_base64), validate=False)
        image = Image.open(io.BytesIO(raw))
        image.load()
    except Exception as exc:
        raise ValueError(f"Bild konnte nicht dekodiert werden: {exc}") from exc

    width, height = image.size
    sent_width, sent_height = smart_resize_to_budget(width, height, max_pixels=max_pixels)
    if (sent_width, sent_height) != (width, height):
        image = image.resize((sent_width, sent_height), Image.Resampling.LANCZOS)
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")

    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    encoded = base64.b64encode(buffer.getvalue()).decode("ascii")
    return PreparedImage(
        data_url=f"data:image/png;base64,{encoded}",
        width=width,
        height=height,
        sent_width=sent_width,
        sent_height=sent_height,
    )


_ASK_SYSTEM = (
    "Du bist der PaperKG Desktop-Companion — ein Assistent, der den Bildschirm des Nutzers "
    "sieht und Fragen dazu beantwortet. Du steuerst NIEMALS Maus oder Tastatur; du erklärst "
    "und führst nur. Antworte auf Deutsch, kurz und konkret, bezogen auf das, was im Bild "
    "tatsächlich sichtbar ist. Wenn etwas nicht sichtbar oder nicht eindeutig ist, sag das offen."
)

_REGION_HINT = (
    "Das Bild ist ein vom Nutzer ausgewählter Ausschnitt seines Bildschirms — "
    "erkläre genau diesen Ausschnitt."
)


def _guide_system(sent_width: int, sent_height: int) -> str:
    return (
        "Du bist der PaperKG Desktop-Companion. Du siehst einen Screenshot des primären "
        f"Bildschirms ({sent_width}x{sent_height} Pixel, Ursprung oben links). "
        "Beantworte die Frage des Nutzers auf Deutsch, kurz und konkret. Wenn es hilft, dem "
        "Nutzer Stellen zum Klicken zu ZEIGEN, gib sie als geordnete Schritte mit absoluten "
        "Pixelkoordinaten im gezeigten Bild an. Du klickst NIEMALS selbst — du zeigst nur.\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt in dieser Form:\n"
        '{"answer": "<deutsche Antwort>", "steps": [{"x": <int>, "y": <int>, "label": "<kurz>"}]}\n'
        'Regeln: "steps": [] wenn Zeigen nicht sinnvoll oder das Ziel nicht sichtbar ist. '
        f"x/y sind Pixel im mitgeschickten Bild ({sent_width}x{sent_height}) — keine Prozente, "
        "kein 0-1000-Raster. Kein Text außerhalb des JSON."
    )


def _no_think_suffix(router: Any, provider: str | None, model: str | None, disable_thinking: bool) -> str:
    """Qwen3's soft switch: a trailing ``/no_think`` in the system prompt disables
    thinking for the turn. Only appended for Qwen models — other models would just
    see a stray token in their instructions."""
    if not disable_thinking:
        return ""
    name = model or ""
    if not name:
        try:
            name = str(router.provider_default_model(provider) or "")
        except Exception:
            name = ""
    return "\n/no_think" if "qwen" in name.lower() else ""


def _raise_if_thinking_exhausted(router: Any) -> None:
    """A reasoning model that spent its entire token budget inside its thinking
    channel has produced no answer — surface that clearly instead of showing the
    user raw chain-of-thought (see LLMRouter reasoning_fallback metadata)."""
    meta = getattr(router, "last_response_metadata", None) or {}
    if meta.get("reasoning_fallback") and meta.get("finish_reason") == "length":
        raise RuntimeError(
            "Das Modell hat sein Token-Budget beim Nachdenken aufgebraucht — "
            "`companion.max_tokens` in config.yaml erhöhen oder ein Nicht-Thinking-Modell wählen."
        )


def _history_messages(history: Any, limit_turns: int) -> list[dict[str, Any]]:
    """Prior chat turns as plain text messages (images are never replayed)."""
    messages: list[dict[str, Any]] = []
    if not isinstance(history, list):
        return messages
    for item in history[-(limit_turns * 2):]:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role") or "")
        content = " ".join(str(item.get("content") or "").split()).strip()
        if role in ("user", "assistant") and content:
            messages.append({"role": role, "content": content})
    return messages


def _user_content(question: str, data_url: str | None) -> Any:
    if not data_url:
        return question
    return [
        {"type": "image_url", "image_url": {"url": data_url}},
        {"type": "text", "text": question},
    ]


def ask(
    router: Any,
    question: str,
    *,
    image_base64: str | None = None,
    history: list[dict[str, Any]] | None = None,
    region: bool = False,
    provider: str | None = None,
    model: str | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    history_turns: int = DEFAULT_HISTORY_TURNS,
    max_tokens: int | None = None,
    disable_thinking: bool = True,
) -> str:
    """Free-form German screen Q&A (no pointing). Raises on LLM/transport failure —
    the endpoint layer reports errors in-body."""
    system = (
        _ASK_SYSTEM
        + (f"\n{_REGION_HINT}" if region else "")
        + _no_think_suffix(router, provider, model, disable_thinking)
    )
    data_url = prepare_image(image_base64, max_pixels=max_pixels).data_url if image_base64 else None
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(_history_messages(history, history_turns))
    messages.append({"role": "user", "content": _user_content(f"Frage: {question}", data_url)})

    overrides: dict[str, Any] = {"temperature": 0.3, "max_tokens": max_tokens or DEFAULT_MAX_TOKENS_ASK}
    if model:
        overrides["model"] = model
    answer = router.chat(messages, provider=provider, overrides=overrides).strip()
    _raise_if_thinking_exhausted(router)
    return answer


def guide(
    router: Any,
    question: str,
    image_base64: str,
    *,
    history: list[dict[str, Any]] | None = None,
    provider: str | None = None,
    model: str | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    history_turns: int = DEFAULT_HISTORY_TURNS,
    max_tokens: int | None = None,
    disable_thinking: bool = True,
) -> dict[str, Any]:
    """Answer + optional click-guidance steps, one vision round trip.

    Returns ``{"answer": str, "found": bool, "steps": [{"x", "y", "label"}]}`` with
    coordinates scaled back to **original screenshot pixels** and clamped to the frame.
    If the model ignores the JSON contract, degrades to a text-only answer instead of
    failing. Raises only on LLM/transport errors."""
    prepared = prepare_image(image_base64, max_pixels=max_pixels)
    system = _guide_system(prepared.sent_width, prepared.sent_height) + _no_think_suffix(
        router, provider, model, disable_thinking
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    messages.extend(_history_messages(history, history_turns))
    messages.append({"role": "user", "content": _user_content(f"Frage: {question}", prepared.data_url)})

    overrides: dict[str, Any] = {
        "temperature": 0.1,
        "max_tokens": max_tokens or DEFAULT_MAX_TOKENS_GUIDE,
        "extra": {"json_mode": True},
    }
    if model:
        overrides["model"] = model
    raw = router.chat(messages, provider=provider, overrides=overrides)
    _raise_if_thinking_exhausted(router)

    try:
        data = LLMRouter._extract_json(raw)
    except Exception:
        return {"answer": raw.strip(), "found": False, "steps": []}

    answer = str(data.get("answer") or "").strip() or raw.strip()
    steps = _scale_steps(data.get("steps"), prepared)
    return {"answer": answer, "found": bool(steps), "steps": steps}


def _scale_steps(raw_steps: Any, prepared: PreparedImage) -> list[dict[str, Any]]:
    """Validate model steps and scale sent-frame pixels back to original pixels."""
    if not isinstance(raw_steps, list):
        return []
    scale_x = prepared.width / prepared.sent_width
    scale_y = prepared.height / prepared.sent_height
    steps: list[dict[str, Any]] = []
    for item in raw_steps:
        if not isinstance(item, dict):
            continue
        raw_x, raw_y = item.get("x"), item.get("y")
        if not isinstance(raw_x, (int, float, str)) or not isinstance(raw_y, (int, float, str)):
            continue
        try:
            x, y = float(raw_x), float(raw_y)
        except ValueError:
            continue
        x_orig = min(max(x * scale_x, 0.0), prepared.width - 1.0)
        y_orig = min(max(y * scale_y, 0.0), prepared.height - 1.0)
        label = " ".join(str(item.get("label") or "").split()).strip()[:120]
        steps.append({"x": round(x_orig, 1), "y": round(y_orig, 1), "label": label})
        if len(steps) >= MAX_GUIDE_STEPS:
            break
    return steps
