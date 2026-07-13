"""Incremental guided sequences (R7): step-by-step *user*-executed guidance.

The one-shot ``screen_companion.guide`` plans all steps from a single screenshot —
fine for "wo ist X?", useless for multi-step flows where each click changes the
screen. This module plans **one step per screenshot** instead: the overlay shows the
pointer ring, the native click watcher reports the user's real click, a fresh
capture comes in, and :func:`plan_next` verifies the click's effect (same
`screen_grounding` primitives as Selbst-Steuerung) and plans the next step.

The user is the "hands" here — PaperKG never drives input in this mode. Sequencing
lives in the overlay (``useGuideFlow``); this module is one round trip per call,
mirroring ``self_drive.plan_step``.
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from query import screen_grounding
from query.llm_router import LLMRouter
from query.screen_companion import (
    DEFAULT_HISTORY_TURNS,
    DEFAULT_MAX_PIXELS,
    _context_suffix,
    _no_think_suffix,
    _raise_if_thinking_exhausted,
    _user_content,
    prepare_image,
)
from query.self_drive import DEFAULT_PIXEL_DIFF_THRESHOLD, scale_point

DEFAULT_MAX_STEPS = 10
DEFAULT_MAX_TOKENS = 1200


def _system_prompt(goal: str, sent_width: int, sent_height: int) -> str:
    return (
        "Du bist der PaperKG Desktop-Companion und führst den Nutzer Schritt für Schritt "
        f"zu einem Ziel. Du siehst einen Screenshot ({sent_width}x{sent_height} px). "
        "Der NUTZER klickt selbst — du zeigst nur, wo. Gib IMMER genau EINEN nächsten "
        "Schritt und warte dann auf den nächsten Screenshot.\n"
        f"Ziel: {goal}\n"
        "Antworte AUSSCHLIESSLICH mit einem JSON-Objekt:\n"
        '{"instruction": "<1 kurzer deutscher Satz, was der Nutzer tun soll>", '
        '"step": {"x": <0-1000>, "y": <0-1000>, "label": "<Name des Elements>"} oder null, '
        '"expectation": "<was sich nach dem Klick sichtbar ändern soll>", "done": <bool>}\n'
        "x/y liegen auf einem 0-1000-Raster über das Bild (0,0 oben links). "
        '"step": null wenn kein Klickziel nötig ist (z.B. "Scrolle nach unten" oder '
        "eine reine Erklärung) — der Nutzer handelt dann frei und du siehst das Ergebnis. "
        'Setze "done": true, wenn das Ziel erreicht ist (dann "step": null).\n'
        "Wenn du Feedback bekommst, dass ein Klick nicht die erwartete Wirkung hatte, "
        "erkläre den Schritt neu oder wähle das richtige Element — wiederhole nicht "
        "einfach denselben Punkt. Kein Text außerhalb des JSON."
    )


@dataclass
class GuideSession:
    """In-memory state for one guided sequence (transcript persists in DuckDB)."""

    guide_id: str
    goal: str
    provider: str | None = None
    model: str | None = None
    monitor: int | None = None
    step_index: int = 0
    max_steps: int = DEFAULT_MAX_STEPS
    history: list[dict[str, Any]] = field(default_factory=list)
    finished: bool = False
    context_blocks: list[str] = field(default_factory=list)
    pending_step: dict[str, Any] | None = None
    pending_expectation: str | None = None
    last_image_thumb: bytes | None = None
    db_session_id: str | None = None


class GuideStore:
    """Process-local guide registry, keyed by guide_id."""

    def __init__(self) -> None:
        self._sessions: dict[str, GuideSession] = {}

    def create(self, **kwargs: Any) -> GuideSession:
        guide_id = f"gd-{int(time.time() * 1000):x}"
        session = GuideSession(guide_id=guide_id, **kwargs)
        self._sessions[guide_id] = session
        return session

    def get(self, guide_id: str) -> GuideSession | None:
        return self._sessions.get(guide_id)

    def drop(self, guide_id: str) -> bool:
        return self._sessions.pop(guide_id, None) is not None


def _verify_click(
    router: Any,
    session: GuideSession,
    image_base64: str,
    click_x: float | None,
    click_y: float | None,
    *,
    verify_cfg: dict[str, Any],
    max_pixels: int,
    disable_thinking: bool,
) -> dict[str, Any] | None:
    """Judge the screenshot after the user's click against the step's expectation.
    Appends guidance-flavored feedback to the history. Degrades to None on errors."""
    if not session.pending_step or not session.pending_expectation:
        session.pending_step = None
        session.pending_expectation = None
        return None
    step = session.pending_step
    expectation = session.pending_expectation
    session.pending_step = None
    session.pending_expectation = None

    label = str(step.get("label") or "das Element")
    where = (
        f"bei ({round(float(click_x), 1)}, {round(float(click_y), 1)})"
        if click_x is not None and click_y is not None
        else f"auf ‚{label}'"
    )
    action_desc = f"Klick des Nutzers {where} — Ziel war ‚{label}'"

    ok = True
    note = ""
    threshold = float(verify_cfg.get("pixel_diff_threshold", DEFAULT_PIXEL_DIFF_THRESHOLD))
    try:
        if session.last_image_thumb is not None:
            diff = screen_grounding.thumb_diff(
                session.last_image_thumb, screen_grounding.thumb_bytes(image_base64)
            )
            if diff < threshold:
                ok, note = False, "Der Bildschirm hat sich nicht sichtbar verändert."
        if ok:
            result = screen_grounding.verify_expectation(
                router,
                image_base64,
                action_desc,
                expectation,
                provider=session.provider,
                model=session.model,
                max_pixels=max_pixels,
                max_tokens=int(verify_cfg.get("max_tokens", screen_grounding.DEFAULT_VERIFY_MAX_TOKENS)),
                disable_thinking=disable_thinking,
            )
            ok, note = bool(result.get("matches")), str(result.get("note") or "")
    except Exception:
        return None

    if ok:
        session.history.append(
            {"role": "user", "content": f"Feedback: {action_desc} war erfolgreich."}
        )
    else:
        detail = f": {note}" if note else "."
        session.history.append(
            {
                "role": "user",
                "content": (
                    f"Feedback: {action_desc} hat nicht ‚{expectation}' bewirkt{detail} "
                    "Erkläre den Schritt neu oder wähle das richtige Element."
                ),
            }
        )
    return {"ok": ok, "note": note}


def plan_next(
    router: Any,
    session: GuideSession,
    image_base64: str,
    *,
    user_event: str = "start",
    click_x: float | None = None,
    click_y: float | None = None,
    max_pixels: int = DEFAULT_MAX_PIXELS,
    history_turns: int = DEFAULT_HISTORY_TURNS,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    disable_thinking: bool = True,
    refine_cfg: dict[str, Any] | None = None,
    verify_cfg: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify the user's last click (on ``user_event == "click"``) and plan the next
    guidance step from the current screenshot.

    Returns ``{"instruction", "step", "expectation", "verification", "done",
    "step_index", "max_steps"}`` — ``step`` in original-screenshot pixels or None
    for advice-only steps. Raises only on LLM/transport errors of the planning call."""
    refine_cfg = refine_cfg or {}
    verify_cfg = verify_cfg or {}

    if session.finished or session.step_index >= session.max_steps:
        session.finished = True
        return {
            "instruction": "Schrittbudget erreicht — bitte neu starten oder Ziel verfeinern.",
            "step": None,
            "expectation": "",
            "verification": None,
            "done": True,
            "step_index": session.step_index,
            "max_steps": session.max_steps,
        }

    prepared = prepare_image(image_base64, max_pixels=max_pixels)

    verification = None
    if user_event == "click" and verify_cfg.get("enabled", False):
        verification = _verify_click(
            router,
            session,
            image_base64,
            click_x,
            click_y,
            verify_cfg=verify_cfg,
            max_pixels=max_pixels,
            disable_thinking=disable_thinking,
        )
    elif user_event == "skip":
        session.pending_step = None
        session.pending_expectation = None
        session.history.append(
            {"role": "user", "content": "Der Nutzer hat den letzten Schritt übersprungen."}
        )
    else:
        session.pending_step = None
        session.pending_expectation = None

    system = (
        _system_prompt(session.goal, prepared.sent_width, prepared.sent_height)
        + _context_suffix(session.context_blocks or None)
        + _no_think_suffix(router, session.provider, session.model, disable_thinking)
    )
    messages: list[dict[str, Any]] = [{"role": "system", "content": system}]
    for turn in session.history[-(history_turns * 2):]:
        messages.append(turn)
    messages.append(
        {"role": "user", "content": _user_content("Nächster Schritt?", prepared.data_url)}
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
    instruction = str(data.get("instruction") or raw.strip())[:500]
    done = bool(data.get("done"))
    expectation = " ".join(str(data.get("expectation") or "").split()).strip()[:300]

    step: dict[str, Any] | None = None
    raw_step = data.get("step")
    if not done and isinstance(raw_step, dict):
        point = scale_point(raw_step.get("x"), raw_step.get("y"), prepared)
        if point is not None:
            label = " ".join(str(raw_step.get("label") or "").split()).strip()[:120]
            step = {"x": point[0], "y": point[1], "label": label}
            if refine_cfg.get("enabled", False) and label:
                try:
                    refined = screen_grounding.refine_point(
                        router,
                        image_base64,
                        float(step["x"]),
                        float(step["y"]),
                        label,
                        crop_px=int(refine_cfg.get("crop_px", screen_grounding.DEFAULT_CROP_PX)),
                        zoom=float(refine_cfg.get("zoom", screen_grounding.DEFAULT_ZOOM)),
                        provider=session.provider,
                        model=session.model,
                        max_tokens=int(
                            refine_cfg.get("max_tokens", screen_grounding.DEFAULT_REFINE_MAX_TOKENS)
                        ),
                        disable_thinking=disable_thinking,
                    )
                    step["x"], step["y"] = refined["x"], refined["y"]
                except Exception:
                    pass  # keep the coarse point

    session.step_index += 1
    session.finished = done
    summary = instruction[:300]
    if step:
        summary += f" → Zeiger auf ({step['x']}, {step['y']}) ‚{step.get('label')}'"
    if expectation:
        summary += f" | Erwartung: {expectation}"
    session.history.append({"role": "assistant", "content": summary})
    if step and expectation and verify_cfg.get("enabled", False):
        session.pending_step = dict(step)
        session.pending_expectation = expectation
    try:
        session.last_image_thumb = screen_grounding.thumb_bytes(image_base64)
    except Exception:
        session.last_image_thumb = None

    return {
        "instruction": instruction,
        "step": step,
        "expectation": expectation,
        "verification": verification,
        "done": done,
        "step_index": session.step_index,
        "max_steps": session.max_steps,
    }
