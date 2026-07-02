"""Native Selbst-Steuerung (roadmap R7) — the *brain*.

A VLM plans ONE action per screenshot; the native shell (`src-tauri/src/control.rs`,
enigo) executes it and the overlay loops. PaperKG never touches the mouse itself —
it only decides what should happen next, exactly like `screen_companion` only points.

Skeleton scope: the planner + action grammar + an in-memory session store are real;
sequencing and per-action consent live in the overlay ("Bestätigungsmodus"). There
is no autonomous loop here yet — each `plan_step` call is one round trip.

Coordinates follow the companion contract: the VLM answers on a 0-1000 grid over the
sent frame; :func:`scale_point` maps that into original-screenshot pixels, which the
frontend turns into physical desktop pixels via the capture's monitor origin.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from query.llm_router import LLMRouter
from query.screen_companion import (
    COORD_GRID,
    DEFAULT_HISTORY_TURNS,
    DEFAULT_MAX_PIXELS,
    PreparedImage,
    _no_think_suffix,
    _raise_if_thinking_exhausted,
    _user_content,
    prepare_image,
)

DEFAULT_MAX_STEPS = 15
DEFAULT_MAX_TOKENS = 1200

# The action verbs the frontend knows how to execute via control.rs commands.
ACTION_TYPES = {"click", "double_click", "type", "key", "scroll", "move", "wait", "done", "fail"}


def _system_prompt(goal: str, sent_width: int, sent_height: int) -> str:
    return (
        "Du steuerst einen Windows-Rechner, um ein Ziel des Nutzers zu erreichen. Du siehst "
        f"einen Screenshot ({sent_width}x{sent_height} px). Plane IMMER nur EINE nächste Aktion "
        "und beobachte danach das Ergebnis im nächsten Screenshot.\n"
        f"Ziel: {goal}\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:\n"
        '{"thought": "<kurze Begründung>", "action": {"type": "<verb>", "x": <0-1000>, '
        '"y": <0-1000>, "text": "<für type>", "keys": "<für key, z.B. ctrl+s>", '
        '"dx": <int>, "dy": <int>}, "done": <bool>}\n'
        "Verben: click, double_click, type, key, scroll, move, wait, done, fail. "
        "x/y liegen auf einem 0-1000-Raster über das Bild (0,0 oben links). "
        'Setze "done": true und type "done", wenn das Ziel erreicht ist; type "fail", wenn '
        "es unmöglich ist. Kein Text außerhalb des JSON."
    )


def scale_point(x: Any, y: Any, prepared: PreparedImage) -> tuple[float, float] | None:
    """Map a 0-1000 grid point (or, as a fallback, sent-frame pixels) into original-
    screenshot pixels, clamped to the frame. Returns None for non-numeric input."""
    try:
        fx, fy = float(x), float(y)
    except (TypeError, ValueError):
        return None
    if fx <= COORD_GRID and fy <= COORD_GRID:
        ox = fx / COORD_GRID * prepared.width
        oy = fy / COORD_GRID * prepared.height
    else:
        ox = fx * prepared.width / prepared.sent_width
        oy = fy * prepared.height / prepared.sent_height
    ox = min(max(ox, 0.0), prepared.width - 1.0)
    oy = min(max(oy, 0.0), prepared.height - 1.0)
    return round(ox, 1), round(oy, 1)


def _normalize_action(raw: Any, prepared: PreparedImage) -> dict[str, Any]:
    """Validate the model's action object and rewrite its coordinates into original
    pixels. Unknown/invalid actions degrade to a no-op ``wait`` so the loop never
    executes something unintended."""
    if not isinstance(raw, dict):
        return {"type": "wait"}
    action_type = str(raw.get("type") or "").strip().lower()
    if action_type not in ACTION_TYPES:
        return {"type": "wait"}
    action: dict[str, Any] = {"type": action_type}
    if action_type in {"click", "double_click", "move"}:
        point = scale_point(raw.get("x"), raw.get("y"), prepared)
        if point is None:
            return {"type": "wait"}
        action["x"], action["y"] = point
    if action_type == "type":
        action["text"] = str(raw.get("text") or "")
    if action_type == "key":
        action["keys"] = str(raw.get("keys") or "").strip()
    if action_type == "scroll":
        try:
            action["dx"] = int(raw.get("dx") or 0)
            action["dy"] = int(raw.get("dy") or 0)
        except (TypeError, ValueError):
            action["dx"], action["dy"] = 0, 0
    return action


@dataclass
class SelfDriveSession:
    """In-memory state for one Selbst-Steuerung run (skeleton — not persisted)."""

    session_id: str
    goal: str
    provider: str | None = None
    model: str | None = None
    monitor: int | None = None
    step_count: int = 0
    max_steps: int = DEFAULT_MAX_STEPS
    history: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False


class SelfDriveStore:
    """Process-local session registry, keyed by session_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, SelfDriveSession] = {}

    def create(self, **kwargs: Any) -> SelfDriveSession:
        session_id = f"sd-{int(time.time() * 1000):x}"
        session = SelfDriveSession(session_id=session_id, **kwargs)
        self._sessions[session_id] = session
        return session

    def get(self, session_id: str) -> SelfDriveSession | None:
        return self._sessions.get(session_id)

    def drop(self, session_id: str) -> bool:
        return self._sessions.pop(session_id, None) is not None


def plan_step(
    router: Any,
    session: SelfDriveSession,
    image_base64: str,
    *,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    history_turns: int = DEFAULT_HISTORY_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    disable_thinking: bool = True,
) -> dict[str, Any]:
    """Ask the VLM for the next action given the current screenshot.

    Returns ``{"thought", "action", "done", "step", "max_steps"}`` with the action's
    coordinates already in original-screenshot pixels. Enforces the step budget:
    once exhausted it returns a terminal ``fail`` action instead of calling the model.
    Raises only on LLM/transport errors."""
    if session.finished or session.step_count >= session.max_steps:
        session.finished = True
        return {
            "thought": "Schrittbudget erreicht.",
            "action": {"type": "fail"},
            "done": True,
            "step": session.step_count,
            "max_steps": session.max_steps,
        }

    prepared = prepare_image(image_base64, max_pixels=max_pixels)
    system = _system_prompt(session.goal, prepared.sent_width, prepared.sent_height) + _no_think_suffix(
        router, session.provider, session.model, disable_thinking
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in session.history[-(history_turns * 2):]:
        messages.append(turn)
    messages.append(
        {"role": "user", "content": _user_content("Nächste Aktion?", prepared.data_url)}
    )

    overrides: dict[str, Any] = {
        "temperature": 0.1,
        "max_tokens": max_tokens,
        "extra": {"json_mode": True},
    }
    if session.model:
        overrides["model"] = session.model
    raw = router.chat(messages, provider=session.provider, overrides=overrides)
    _raise_if_thinking_exhausted(router)

    try:
        data = LLMRouter._extract_json(raw)
    except Exception:
        data = {}
    thought = str(data.get("thought") or raw.strip())[:500]
    action = _normalize_action(data.get("action"), prepared)
    done = bool(data.get("done")) or action["type"] in {"done", "fail"}

    session.step_count += 1
    session.finished = done
    session.history.append({"role": "assistant", "content": thought[:300]})

    return {
        "thought": thought,
        "action": action,
        "done": done,
        "step": session.step_count,
        "max_steps": session.max_steps,
    }
