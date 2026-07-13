"""Desktop-Companion + native Selbst-Steuerung: Screen-Chat, Zeiger-Guides,
Session-Persistenz und der /selfdrive-Planungs-Loop (R7).

Split out of api/product_main.py (pattern: api/routers/parallel.py). Patchable
singletons (``llm_router``, ``_COMPANION_CONFIG_CACHE``, ``_companion_context``,
``_SELF_DRIVE_STORE``) stay in product_main and are referenced as ``pm.<name>``
at call time so existing monkeypatch-based tests keep working.
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons
from query import guide_flow, screen_companion, self_drive
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


# --------------------------------------------------------------------------- #
# Request models                                                               #
# --------------------------------------------------------------------------- #


class CompanionTurn(BaseModel):
    """One prior Desktop-Companion chat turn (text only — screenshots are never replayed)."""
    role: str = Field(min_length=1, max_length=20)
    content: str = Field(default="", max_length=8000)


class CompanionAskRequest(BaseModel):
    """Free-form question about the screen or a snipped region — answer only, no pointing."""
    question: str = Field(min_length=1, max_length=4000)
    image_base64: str | None = None
    history: list[CompanionTurn] = Field(default_factory=list)
    region: bool = False
    provider: str | None = None
    model: str | None = None
    # Quellen-Modus: ground the answer in local paper hits and/or a web search.
    use_papers: bool = False
    use_web: bool = False
    # Optional durable chat: persist question + answer into this companion session.
    session_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class CompanionGuideRequest(BaseModel):
    """Question about a full screenshot; the model may return click-guidance steps."""
    question: str = Field(min_length=1, max_length=4000)
    image_base64: str = Field(min_length=1)
    history: list[CompanionTurn] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    use_papers: bool = False
    use_web: bool = False
    session_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class CompanionSessionCreateRequest(BaseModel):
    kind: str = Field(default="companion", pattern="^(companion|selfdrive)$")
    title: str = Field(default="", max_length=400)
    goal: str = Field(default="", max_length=2000)
    provider: str | None = None
    model: str | None = None
    monitor: int | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class CompanionSessionUpdateRequest(BaseModel):
    title: str | None = Field(default=None, max_length=400)
    status: str | None = Field(default=None, max_length=40)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class SelfDriveStartRequest(BaseModel):
    """Begin a native Selbst-Steuerung session (R7)."""
    goal: str = Field(min_length=1, max_length=2000)
    monitor: int | None = None
    provider: str | None = None
    model: str | None = None
    # Optional durable log: companion_sessions id (kind 'selfdrive') to append to.
    session_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class SelfDriveStepRequest(BaseModel):
    """One planning round: current screenshot → next action."""
    session_id: str = Field(min_length=1, max_length=64)
    image_base64: str = Field(min_length=1)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class SelfDriveStopRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


class SelfDriveAnswerRequest(BaseModel):
    """The user's reply to an ``ask`` action — the loop resumes with the next /step."""
    session_id: str = Field(min_length=1, max_length=64)
    answer: str = Field(min_length=1, max_length=4000)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class GuideStartRequest(BaseModel):
    """Begin an incremental guided sequence (auto-advance via native click watcher)."""
    goal: str = Field(min_length=1, max_length=2000)
    provider: str | None = None
    model: str | None = None
    monitor: int | None = None
    use_papers: bool = False
    use_web: bool = False
    session_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class GuideStepRequest(BaseModel):
    """One guidance round: fresh screenshot (+ what the user just did) → next step."""
    guide_id: str = Field(min_length=1, max_length=64)
    image_base64: str = Field(min_length=1)
    event: str = Field(default="start", pattern="^(start|click|skip)$")
    # The user's click in original-screenshot pixels (capture origin already subtracted).
    click_x: float | None = None
    click_y: float | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class GuideStopRequest(BaseModel):
    guide_id: str = Field(min_length=1, max_length=64)


# --------------------------------------------------------------------------- #
# Config helpers (companion: sub-blocks)                                       #
# --------------------------------------------------------------------------- #


def _companion_sub_config(name: str) -> dict[str, Any]:
    section = pm._load_companion_config().get(name)
    return section if isinstance(section, dict) else {}


def _self_drive_config() -> dict[str, Any]:
    return _companion_sub_config("self_drive")


def _persist_message(
    db_path: str,
    session_id: str | None,
    role: str,
    content: str,
    payload: dict[str, Any] | None = None,
) -> None:
    """Best-effort transcript write — a DB hiccup must never block the answer."""
    if not session_id:
        return
    try:
        with MetadataDB(db_path) as db:
            db.add_companion_message(session_id, role, content, payload)
    except Exception:  # noqa: BLE001 - persistence is best-effort
        pass


# --------------------------------------------------------------------------- #
# Companion: screen Q&A + one-shot guide + config                              #
# --------------------------------------------------------------------------- #


@router.post("/companion/guide")
async def companion_guide(request: CompanionGuideRequest) -> dict[str, Any]:
    """Desktop-Companion: answer a question about the screenshot and optionally return
    ordered click-guidance steps in **original screenshot pixels** (= physical monitor
    pixels for full captures). The companion only points — it never drives input."""
    params = pm._companion_llm_params(request.provider, request.model)
    cfg = pm._load_companion_config()
    if cfg.get("debug_capture"):
        params["debug_dir"] = str(cfg.get("debug_dir") or "data/companion_debug")
    history = [turn.model_dump() for turn in request.history]
    context_blocks, sources = await pm._companion_context(
        request.question, request.use_papers, request.use_web
    )
    try:
        result = await asyncio.to_thread(
            screen_companion.guide,
            pm.llm_router,
            request.question,
            request.image_base64,
            history=history,
            context_blocks=context_blocks or None,
            **params,
        )
        result["sources"] = sources
        _persist_message(request.metadata_db_path, request.session_id, "user", request.question)
        _persist_message(
            request.metadata_db_path,
            request.session_id,
            "assistant",
            str(result.get("answer") or ""),
            {"steps": result.get("steps") or [], "sources": sources},
        )
        return result
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"answer": "", "found": False, "steps": [], "sources": [], "error": str(exc)}


@router.post("/companion/ask")
async def companion_ask(request: CompanionAskRequest) -> dict[str, Any]:
    """Desktop-Companion: free-form screen Q&A without pointing — used for the
    Bereich-erklären snips (``region=true``) and text-only follow-up questions."""
    params = pm._companion_llm_params(request.provider, request.model)
    history = [turn.model_dump() for turn in request.history]
    context_blocks, sources = await pm._companion_context(
        request.question, request.use_papers, request.use_web
    )
    try:
        answer = await asyncio.to_thread(
            screen_companion.ask,
            pm.llm_router,
            request.question,
            image_base64=request.image_base64,
            history=history,
            region=request.region,
            context_blocks=context_blocks or None,
            **params,
        )
        _persist_message(request.metadata_db_path, request.session_id, "user", request.question)
        _persist_message(
            request.metadata_db_path, request.session_id, "assistant", answer, {"sources": sources}
        )
        return {"answer": answer, "sources": sources}
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"answer": "", "sources": [], "error": str(exc)}


@router.get("/companion/config")
def companion_config() -> dict[str, Any]:
    """Companion defaults plus the selectable providers/models for the overlay picker.

    Uses cached model options (``refresh=False``) so opening the overlay stays instant;
    the picker can call ``POST /models/{provider}/discover`` for a live refresh."""
    cfg = pm._load_companion_config()
    sd_cfg = _self_drive_config()
    verify_cfg = _companion_sub_config("verify")
    guide_cfg = _companion_sub_config("guide")
    return {
        "provider": str(cfg.get("provider") or "").strip() or pm.llm_router.default_provider,
        "model": str(cfg.get("model") or "").strip(),
        "language": str(cfg.get("language") or "de"),
        "default_provider": pm.llm_router.default_provider,
        "providers": [
            {"name": name, "models": pm.llm_router.provider_model_options(name, refresh=False)}
            for name in pm.llm_router.available_providers()
        ],
        # Everything the overlay needs to run its loops — no second config fetch.
        "self_drive": {
            "enabled": bool(sd_cfg.get("enabled", False)),
            "autopilot": bool(sd_cfg.get("autopilot", True)),
            "max_steps": int(sd_cfg.get("max_steps") or self_drive.DEFAULT_MAX_STEPS),
            "settle_ms": int(verify_cfg.get("settle_ms") or 800),
            "mouse_abort_px": int(sd_cfg.get("mouse_abort_px") or 150),
            "action_timeout_ms": int(sd_cfg.get("action_timeout_ms") or 5000),
            "step_timeout_ms": int(sd_cfg.get("step_timeout_ms") or 120000),
        },
        "guide": {
            "max_steps": int(guide_cfg.get("max_steps") or 10),
            "click_settle_ms": int(guide_cfg.get("click_settle_ms") or 700),
        },
    }


# --------------------------------------------------------------------------- #
# Companion sessions (durable chat/step log, DuckDB)                           #
# --------------------------------------------------------------------------- #


@router.post("/companion/sessions")
def create_companion_session(request: CompanionSessionCreateRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        return db.create_companion_session(
            kind=request.kind,
            title=request.title,
            goal=request.goal,
            provider=request.provider,
            model=request.model,
            monitor=request.monitor,
        )


@router.get("/companion/sessions")
def list_companion_sessions(
    kind: str | None = None, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"sessions": db.list_companion_sessions(kind=kind)}


@router.get("/companion/sessions/{session_id}")
def get_companion_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        session = db.get_companion_session(session_id)
    if session is None:
        return {"error": "Unbekannte Sitzung."}
    return session


@router.patch("/companion/sessions/{session_id}")
def update_companion_session(
    session_id: str, request: CompanionSessionUpdateRequest
) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        session = db.update_companion_session(
            session_id, title=request.title, status=request.status
        )
    if session is None:
        return {"error": "Unbekannte Sitzung."}
    return session


@router.delete("/companion/sessions/{session_id}")
def delete_companion_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"deleted": db.delete_companion_session(session_id)}


# --------------------------------------------------------------------------- #
# Native Selbst-Steuerung (R7) — the brain; enigo (control.rs) = hands          #
# --------------------------------------------------------------------------- #


@router.post("/selfdrive/start")
def self_drive_start(request: SelfDriveStartRequest) -> dict[str, Any]:
    """Open a Selbst-Steuerung session. Gated on ``companion.self_drive.enabled``;
    the native shell still requires an explicit arm (autopilot or per-action confirm)."""
    cfg = _self_drive_config()
    if not bool(cfg.get("enabled", False)):
        return {"error": "Selbst-Steuerung ist deaktiviert (companion.self_drive.enabled)."}
    params = pm._companion_llm_params(request.provider, request.model)
    session = pm._SELF_DRIVE_STORE.create(
        goal=request.goal,
        provider=params["provider"],
        model=params["model"],
        monitor=request.monitor,
        max_steps=int(cfg.get("max_steps") or self_drive.DEFAULT_MAX_STEPS),
        db_session_id=request.session_id,
    )
    _persist_message(request.metadata_db_path, request.session_id, "user", request.goal)
    return {
        "session_id": session.session_id,
        "goal": session.goal,
        "max_steps": session.max_steps,
        "autopilot": bool(cfg.get("autopilot", True)),
    }


@router.post("/selfdrive/step")
async def self_drive_step(request: SelfDriveStepRequest) -> dict[str, Any]:
    """Plan the next action for a session from the current screenshot. The frontend
    executes the returned action (control.rs) and calls back with the next frame.

    Runs the verify → stall → plan → refine pipeline; a ``lookup`` action is resolved
    here (web/paper research via ``pm._companion_context``), injected into the session
    history and re-planned on the same screenshot."""
    sd_cfg = _self_drive_config()
    if not bool(sd_cfg.get("enabled", False)):
        return {"error": "Selbst-Steuerung ist deaktiviert (companion.self_drive.enabled)."}
    session = pm._SELF_DRIVE_STORE.get(request.session_id)
    if session is None:
        return {"error": "Unbekannte Sitzung."}
    params = pm._companion_llm_params(session.provider, session.model)
    lookup_cfg = sd_cfg.get("lookup")
    if not isinstance(lookup_cfg, dict):
        lookup_cfg = {}
    sensitive_cfg = sd_cfg.get("sensitive")
    if not isinstance(sensitive_cfg, dict):
        sensitive_cfg = {}
    plan_kwargs: dict[str, Any] = {
        "max_pixels": params["max_pixels"],
        "history_turns": params["history_turns"],
        "max_tokens": params["max_tokens"] or self_drive.DEFAULT_MAX_TOKENS,
        "disable_thinking": params["disable_thinking"],
        "refine_cfg": _companion_sub_config("refine"),
        "verify_cfg": _companion_sub_config("verify"),
        "lookup_cfg": lookup_cfg,
        "sensitive_cfg": sensitive_cfg,
        "max_consecutive_failures": int(
            sd_cfg.get("max_consecutive_failures") or self_drive.DEFAULT_MAX_CONSECUTIVE_FAILURES
        ),
    }
    try:
        result = await asyncio.to_thread(
            self_drive.plan_step, pm.llm_router, session, request.image_base64, **plan_kwargs
        )
        if result.get("action", {}).get("type") == "lookup":
            query = str(result["action"].get("query") or "")
            blocks, sources = await pm._companion_context(
                query,
                bool(lookup_cfg.get("use_papers", False)),
                bool(lookup_cfg.get("use_web", True)),
            )
            self_drive.inject_lookup_result(session, query, blocks)
            _persist_message(
                request.metadata_db_path,
                getattr(session, "db_session_id", None),
                "system",
                f"Recherche: {query}",
                {"sources": sources},
            )
            # Re-plan on the same frame, now with the research in the history.
            result = await asyncio.to_thread(
                self_drive.plan_step, pm.llm_router, session, request.image_base64, **plan_kwargs
            )
        _persist_message(
            request.metadata_db_path,
            getattr(session, "db_session_id", None),
            "action",
            str(result.get("thought") or ""),
            {
                "action": result.get("action"),
                "expectation": result.get("expectation"),
                "verification": result.get("verification"),
                "step": result.get("step"),
                "done": result.get("done"),
                "refined": result.get("refined"),
            },
        )
        return result
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"error": str(exc)}


@router.post("/selfdrive/answer")
def self_drive_answer(request: SelfDriveAnswerRequest) -> dict[str, Any]:
    """Feed the user's reply to an ``ask`` action into the session; the frontend
    resumes the loop by calling /selfdrive/step with a fresh screenshot."""
    session = pm._SELF_DRIVE_STORE.get(request.session_id)
    if session is None:
        return {"error": "Unbekannte Sitzung."}
    self_drive.inject_user_answer(session, request.answer)
    _persist_message(
        request.metadata_db_path, getattr(session, "db_session_id", None), "user", request.answer
    )
    return {"ok": True}


@router.post("/selfdrive/stop")
def self_drive_stop(request: SelfDriveStopRequest) -> dict[str, Any]:
    """Drop a session (idempotent)."""
    return {"stopped": pm._SELF_DRIVE_STORE.drop(request.session_id)}


# --------------------------------------------------------------------------- #
# Incremental guided sequences (auto-advance; user = hands)                    #
# --------------------------------------------------------------------------- #


@router.post("/companion/guide/start")
async def guide_start(request: GuideStartRequest) -> dict[str, Any]:
    """Open a guided sequence. Optional Quellen-Modus context (papers/web) is fetched
    once here and grounds every subsequent step prompt."""
    guide_cfg = _companion_sub_config("guide")
    verify_cfg = _companion_sub_config("verify")
    params = pm._companion_llm_params(request.provider, request.model)
    context_blocks: list[str] = []
    if request.use_papers or request.use_web:
        context_blocks, _sources = await pm._companion_context(
            request.goal, request.use_papers, request.use_web
        )
    session = pm._GUIDE_STORE.create(
        goal=request.goal,
        provider=params["provider"],
        model=params["model"],
        monitor=request.monitor,
        max_steps=int(guide_cfg.get("max_steps") or guide_flow.DEFAULT_MAX_STEPS),
        context_blocks=context_blocks,
        db_session_id=request.session_id,
    )
    _persist_message(request.metadata_db_path, request.session_id, "user", request.goal)
    return {
        "guide_id": session.guide_id,
        "max_steps": session.max_steps,
        "click_settle_ms": int(guide_cfg.get("click_settle_ms") or 700),
        "settle_ms": int(verify_cfg.get("settle_ms") or 800),
    }


@router.post("/companion/guide/step")
async def guide_step(request: GuideStepRequest) -> dict[str, Any]:
    """One guidance round trip: verify the user's click (event 'click') against the
    previous step's expectation, then plan the next pointer step from the fresh frame."""
    session = pm._GUIDE_STORE.get(request.guide_id)
    if session is None:
        return {"error": "Unbekannte Sitzung."}
    params = pm._companion_llm_params(session.provider, session.model)
    try:
        result = await asyncio.to_thread(
            guide_flow.plan_next,
            pm.llm_router,
            session,
            request.image_base64,
            user_event=request.event,
            click_x=request.click_x,
            click_y=request.click_y,
            max_pixels=params["max_pixels"],
            history_turns=params["history_turns"],
            max_tokens=params["max_tokens"] or guide_flow.DEFAULT_MAX_TOKENS,
            disable_thinking=params["disable_thinking"],
            refine_cfg=_companion_sub_config("refine"),
            verify_cfg=_companion_sub_config("verify"),
        )
        _persist_message(
            request.metadata_db_path,
            getattr(session, "db_session_id", None),
            "assistant",
            str(result.get("instruction") or ""),
            {
                "step": result.get("step"),
                "expectation": result.get("expectation"),
                "verification": result.get("verification"),
                "step_index": result.get("step_index"),
                "done": result.get("done"),
            },
        )
        return result
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"error": str(exc)}


@router.post("/companion/guide/stop")
def guide_stop(request: GuideStopRequest) -> dict[str, Any]:
    """Drop a guided sequence (idempotent)."""
    return {"stopped": pm._GUIDE_STORE.drop(request.guide_id)}
