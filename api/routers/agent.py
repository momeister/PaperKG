"""Desktop-Agent-Bridge (Kanal B): Config, Dispatch, Cancel, Observe-Relays.

Split out of api/product_main.py. Behaviour unchanged. PaperKG bleibt das Gehirn;
die Maschine steuert nur die externe Bridge. Patchbare Namen laufen ueber
pm.<name>: _load_agent_bridge_config (Cache _AGENT_BRIDGE_CONFIG_CACHE bleibt in
product_main), httpx.AsyncClient.
"""
from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons + Bridge-Config-Cache
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


class AgentDispatchRequest(BaseModel):
    """Forward a compiled task brief to the local desktop-agent bridge (Kanal B)."""
    task: str = Field(min_length=1, max_length=20000)
    variant_id: str | None = None
    bridge_url: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class AgentCancelRequest(BaseModel):
    """Gracefully abort an in-flight Selbst-Steuerung run on the bridge."""
    run_id: str = Field(min_length=1, max_length=200)
    bridge_base: str | None = None


class AgentObserveStartRequest(BaseModel):
    """Start an Assistent (helper) live screen-observation session on the bridge."""
    session_id: str | None = None
    interval_ms: int | None = None
    primer: str = Field(default="", max_length=20000)
    bridge_base: str | None = None


class AgentObserveAskRequest(BaseModel):
    """Ask a live question against an active Assistent observation session."""
    session_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    bridge_base: str | None = None


class AgentObservePointRequest(BaseModel):
    """Ask the bridge to locate a screen element ("zeig mir wo ich klicken kann") and
    return real screen coordinates. Pure lookup — never dispatches mouse/keyboard input."""
    session_id: str = Field(min_length=1, max_length=200)
    question: str = Field(min_length=1, max_length=4000)
    bridge_base: str | None = None


class AgentObserveStopRequest(BaseModel):
    """Stop an active Assistent observation session."""
    session_id: str = Field(min_length=1, max_length=200)
    bridge_base: str | None = None


_AGENT_BRIDGE_VLM_BASE_URL_CACHE: str | None = None


def _resolve_agent_bridge_vlm_base_url() -> str:
    """Resolve ``agent_bridge.vlm_provider`` to that provider's ``base_url`` under
    ``llm.providers`` in config.yaml, so it isn't duplicated in the agent_bridge block.
    Only ``base_url`` is read here, never ``api_key``/``api_key_env`` — the bridge
    screenshots the user's desktop, so only local providers (ollama/lm_studio, both
    keyless) are a sane choice; a keyed cloud provider isn't supported by this wiring."""
    global _AGENT_BRIDGE_VLM_BASE_URL_CACHE
    if _AGENT_BRIDGE_VLM_BASE_URL_CACHE is not None:
        return _AGENT_BRIDGE_VLM_BASE_URL_CACHE
    provider = str(pm._load_agent_bridge_config().get("vlm_provider") or "").strip()
    base_url = ""
    if provider:
        try:
            with open("config.yaml", "r", encoding="utf-8") as fh:
                providers = ((yaml.safe_load(fh) or {}).get("llm", {}) or {}).get("providers", {}) or {}
            base_url = str((providers.get(provider) or {}).get("base_url") or "")
        except FileNotFoundError:
            base_url = ""
    _AGENT_BRIDGE_VLM_BASE_URL_CACHE = base_url
    return base_url


def _resolve_bridge_origin(bridge_base: str | None) -> tuple[str, bool]:
    """Resolve the bridge's origin (``scheme://host:port``) for the cancel/observe
    relays. A client-supplied ``bridge_base`` (the port Tauri's sidecar just spawned
    on) takes precedence and is treated as untrusted (loopback-only, like
    ``bridge_url`` on /agent/dispatch); otherwise falls back to the origin of the
    configured Kanal-B ``url`` (the web-mode manual-bridge case). Returns
    ``(origin, from_config)`` for `_validate_bridge_url`."""
    base = (bridge_base or "").strip()
    if base:
        return base.rstrip("/"), False
    config_url = str(pm._load_agent_bridge_config().get("url") or "").strip()
    parsed = urlparse(config_url)
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme}://{parsed.netloc}", True
    return "", True


# --------------------------------------------------------------------------- #
# Desktop-agent hand-off (PaperKG = brain/context, external agent = eyes/hands) #
# --------------------------------------------------------------------------- #


def _validate_bridge_url(url: str, *, from_config: bool) -> bool:
    """Allow http(s) bridge URLs. A client-supplied override must be loopback (the bridge
    is local); the config URL is trusted as set by the operator."""
    try:
        parsed = urlparse(url)
    except (ValueError, TypeError):
        return False
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return False
    if from_config:
        return True
    return parsed.hostname.lower() in {"127.0.0.1", "localhost", "::1"}


@router.get("/agent/config")
def get_agent_config() -> dict[str, Any]:
    """Whether the desktop-agent bridge (Kanal B) is configured. Never exposes secrets."""
    bridge = pm._load_agent_bridge_config()
    return {
        "enabled": bool(bridge.get("enabled")),
        "type": str(bridge.get("type") or "ui_tars_desktop"),
        "has_url": bool(bridge.get("url")),
        "vlm_model": str(bridge.get("vlm_model") or ""),
        "vlm_provider": str(bridge.get("vlm_provider") or ""),
        "vlm_base_url": _resolve_agent_bridge_vlm_base_url(),
        # Assistent-only override (Selbst-Steuerung always uses vlm_model — it needs a
        # UI-TARS-family model for action grounding). Falls back to vlm_model if unset.
        "helper_vlm_model": str(bridge.get("helper_vlm_model") or bridge.get("vlm_model") or ""),
        # Native shell only: whether Tauri should spawn/manage the bridge sidecar
        # itself (agent_bridge_ensure/_stop) instead of relying on a manually
        # started one. Ignored in the web app (no sidecar manager there).
        "manage_sidecar": bool(bridge.get("manage_sidecar", True)),
        "helper_enabled": bool(bridge.get("helper_enabled", True)),
        "observe_interval_seconds": int(bridge.get("observe_interval_seconds") or 4),
        "observe_context_size": int(bridge.get("observe_context_size") or 8),
    }


@router.post("/agent/dispatch")
async def dispatch_agent(request: AgentDispatchRequest) -> StreamingResponse:
    """Forward a task brief to the local desktop-agent bridge and stream progress (SSE).

    Best-effort: if the bridge is disabled or unreachable a single terminal ``error``
    event is emitted — nothing crashes, and PaperKG itself never controls the machine.
    On completion the run transcript is appended to the variant as an assistant entry."""
    bridge = pm._load_agent_bridge_config()
    config_url = str(bridge.get("url") or "").strip()
    url = (request.bridge_url or config_url).strip()
    from_config = not request.bridge_url
    enabled = bool(bridge.get("enabled")) or bool(request.bridge_url)
    timeout = float(bridge.get("timeout_seconds") or 600)

    async def stream() -> AsyncIterator[str]:
        def emit(payload: dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        if not enabled or not url:
            yield emit({"status": "error", "error": "agent_bridge disabled or no url configured"})
            return
        if not _validate_bridge_url(url, from_config=from_config):
            yield emit({"status": "error", "error": "bridge url rejected"})
            return
        transcript: list[str] = []
        try:
            async with pm.httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", url, json={"task": request.task}) as resp:
                    if resp.status_code >= 400:
                        yield emit({"status": "error", "error": f"bridge returned {resp.status_code}"})
                        return
                    yield emit({"status": "started"})
                    async for raw in resp.aiter_lines():
                        line = raw.strip()
                        if not line.startswith("data:"):
                            continue
                        body = line[len("data:"):].strip()
                        if body:
                            transcript.append(body)
                            yield f"data: {body}\n\n"
        except Exception as exc:  # noqa: BLE001 - surface as terminal SSE event
            yield emit({"status": "error", "error": str(exc)})
            return
        yield emit({"status": "done"})
        if request.variant_id and transcript:
            try:
                summary = "Desktop-Agent-Lauf:\n" + "\n".join(transcript[-40:])
                with MetadataDB(request.metadata_db_path) as db:
                    variant = db.get_parallel_variant(request.variant_id)
                    if variant is not None:
                        db.add_parallel_entry(
                            request.variant_id, str(variant.get("session_id")),
                            "assistant", summary,
                        )
            except Exception:
                pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/cancel")
async def cancel_agent(request: AgentCancelRequest) -> dict[str, Any]:
    """Gracefully abort an in-flight Selbst-Steuerung run (the bridge's own
    ``AbortSignal``). Tauri hard-killing the bridge sidecar is the guaranteed
    fallback if a single step doesn't honor this in time."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"ok": False, "error": "bridge url rejected or not configured"}
    try:
        async with pm.httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{origin}/cancel", json={"runId": request.run_id})
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"ok": False, "error": str(exc)}


@router.post("/agent/observe/start")
async def observe_agent_start(request: AgentObserveStartRequest) -> StreamingResponse:
    """Start an Assistent (helper) session: relay the bridge's periodic screen
    observations as SSE. Screenshots never leave the bridge process — only short
    text descriptions are forwarded, and nothing is persisted here."""
    bridge = pm._load_agent_bridge_config()
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    timeout = float(bridge.get("timeout_seconds") or 600)

    async def stream() -> AsyncIterator[str]:
        def emit(payload: dict[str, Any]) -> str:
            return f"data: {json.dumps(payload, ensure_ascii=False)}\n\n"

        if not origin or not _validate_bridge_url(origin, from_config=from_config):
            yield emit({"status": "error", "error": "bridge url rejected or not configured"})
            return
        body = {
            "sessionId": request.session_id,
            "intervalMs": request.interval_ms,
            "primer": request.primer,
        }
        try:
            async with pm.httpx.AsyncClient(timeout=timeout) as client:
                async with client.stream("POST", f"{origin}/observe/start", json=body) as resp:
                    if resp.status_code >= 400:
                        yield emit({"status": "error", "error": f"bridge returned {resp.status_code}"})
                        return
                    async for raw in resp.aiter_lines():
                        line = raw.strip()
                        if not line.startswith("data:"):
                            continue
                        chunk = line[len("data:"):].strip()
                        if chunk:
                            yield f"data: {chunk}\n\n"
        except Exception as exc:  # noqa: BLE001 - surface as terminal SSE event
            yield emit({"status": "error", "error": str(exc)})

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/agent/observe/ask")
async def observe_agent_ask(request: AgentObserveAskRequest) -> dict[str, Any]:
    """Ask a live question against an active Assistent observation session,
    answered against a fresh screenshot plus the session's rolling context."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"answer": "", "error": "bridge url rejected or not configured"}
    timeout = float(pm._load_agent_bridge_config().get("timeout_seconds") or 600)
    try:
        async with pm.httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{origin}/observe/ask",
                json={"sessionId": request.session_id, "question": request.question},
            )
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"answer": "", "error": str(exc)}


@router.post("/agent/observe/point")
async def observe_agent_point(request: AgentObservePointRequest) -> dict[str, Any]:
    """Locate a UI element for the Assistent's pointer overlay (the "zeig mir"-Funktion):
    relays the bridge's grounding call and returns real screen coordinates. Never
    dispatches input — the overlay only draws a highlight at the returned point."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"error": "bridge url rejected or not configured"}
    timeout = float(pm._load_agent_bridge_config().get("timeout_seconds") or 600)
    try:
        async with pm.httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{origin}/observe/point",
                json={"sessionId": request.session_id, "question": request.question},
            )
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"error": str(exc)}


@router.post("/agent/observe/stop")
async def observe_agent_stop(request: AgentObserveStopRequest) -> dict[str, Any]:
    """Stop an active Assistent observation session."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"ok": False, "error": "bridge url rejected or not configured"}
    try:
        async with pm.httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{origin}/observe/stop", json={"sessionId": request.session_id})
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"ok": False, "error": str(exc)}
