from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import time
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx  # noqa: F401  # pm.httpx.AsyncClient (papers/harvest/agent/grey-Router) + Test-Patch-Surface
import yaml
from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api import phase4_main
from api.main import BuildGraphRequest, build_phase2_graph
from extraction.embedding_engine import EmbeddingEngine
from extraction.entity_linker import ExtractionPipeline
from graph.paper_ingestion import extract_citation_ids, paper_id
from quality.benchmark import run_benchmark
from quality.benchmark_suite import SuiteConfig, latest_suite_report, run_suite
from quality.phase4_eval import run_eval
from maintenance.health_repair import repair_health_state
from parsing.parser_router import ParserRouter
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter
from query.auto_answer import auto_research_answer  # noqa: F401  # pm.auto_research_answer (discovery-Router) + Test-Patch-Surface
from query.research_tree import ResearchTreeRunner  # noqa: F401  # pm.ResearchTreeRunner (discovery-Router) + Test-Patch-Surface
from query import guide_flow, screen_companion, self_drive
from storage.metadata_db import MetadataDB
from storage.path_safety import PathSafetyError, ensure_safe_path
from workspace import manager as workspace_manager  # noqa: F401  # pm.workspace_manager (routers) + test patch surface
from workspace.manager import WorkspaceError


DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_GRAPH_DB_PATH = "data/graphs/global_kg"
DEFAULT_PDF_BASE_DIR = "data/pdfs"
DEFAULT_NOTE_ASSET_DIR = "data/note_assets"
DEFAULT_VOCABULARY_PATH = "data/vocabulary.json"

app = FastAPI(
    title="ScienceKG Product API",
    description="Unified product API for the Phase 5 custom frontend.",
    version="5.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:5175",
        "http://127.0.0.1:5175",
        "http://localhost:4173",
        "http://127.0.0.1:4173",
        # Native (Tauri) shell: the webview serves the frontend from the Tauri
        # asset protocol, whose origin is tauri://localhost (macOS/Linux) or
        # http(s)://tauri.localhost (Windows WebView2). The page then calls this
        # API on the sidecar's localhost port, so these origins must be allowed.
        "tauri://localhost",
        "http://tauri.localhost",
        "https://tauri.localhost",
    ],
    allow_origin_regex=r"(https?://(localhost|127\.0\.0\.1):\d+|tauri://localhost)",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    # Let the frontend read the export filename/format/warnings off the response.
    expose_headers=["Content-Disposition", "X-Export-Format", "X-Export-Warnings"],
)

app.include_router(phase4_main.app.router)
from api.routers import datasets as _datasets_router  # noqa: E402
app.include_router(_datasets_router.router)
from api.routers import analysis as _analysis_router  # noqa: E402
app.include_router(_analysis_router.router)
from api.routers import workspaces as _workspaces_router  # noqa: E402
app.include_router(_workspaces_router.router)
from api.routers import pdf_annotations as _pdf_annotations_router  # noqa: E402
app.include_router(_pdf_annotations_router.router)
from api.routers import parallel as _parallel_router  # noqa: E402
app.include_router(_parallel_router.router)
from api.routers import companion as _companion_router  # noqa: E402
app.include_router(_companion_router.router)
from api.routers import projects as _projects_router  # noqa: E402
app.include_router(_projects_router.router)
from api.routers import papers as _papers_router  # noqa: E402
app.include_router(_papers_router.router)
from api.routers import harvest as _harvest_router  # noqa: E402
app.include_router(_harvest_router.router)
from api.routers import discovery as _discovery_router  # noqa: E402
app.include_router(_discovery_router.router)
from api.routers import agent as _agent_router  # noqa: E402
app.include_router(_agent_router.router)
from api.routers import grey_sources as _grey_sources_router  # noqa: E402
app.include_router(_grey_sources_router.router)
from api.routers import extraction as _extraction_router  # noqa: E402
app.include_router(_extraction_router.router)
from api.routers.extraction import (  # noqa: E402,F401  # pm-Surface fuer harvest/discovery + graph-explorer
    _latest_successful_extractions,
    _parse_pdf_for_extraction,
    _resolve_extraction_pdf_path,
)
from api.routers.harvest import (  # noqa: E402,F401  # Test-Patch-/Import-Surface + Discovery nutzt Suche/Normalizer
    _existing_library_keys,
    _normalize_core_work,
    _normalize_crossref_work,
    _normalize_doaj_article,
    _normalize_europepmc_result,
    _run_harvest_search,
)
from api.routers.papers import (  # noqa: E402,F401  # Papier-/Titel-Helfer, weiter genutzt von extraction/grey + Test-Patch-Surface
    _clean_display_text,
    _clean_pdf_title,
    _infer_pdf_title_from_bytes,
    _infer_title_from_html,
    _latest_extraction_statuses,
    _maybe_fix_garbled_pdf_title,
    _paper_list_view,
    _paper_local_pdf_path,
    _text_looks_garbled,
)
from api.routers.projects import (  # noqa: E402,F401  # Projekt-Helfer, weiter genutzt von papers/harvest/extraction/graph
    PROJECTS_PATH,
    PROJECT_PRIMARY_PATH,
    RESERVED_PROJECT_IDS,
    _attach_papers_to_project,
    _is_reserved_project_id,
    _load_primary_papers,
    _load_projects,
    _paper_matches_query,
    _project_memberships,
    _projects_path,
    _save_primary_papers,
    _save_projects,
    _unique_strings,
)


@app.exception_handler(PathSafetyError)
async def _path_safety_handler(request: Request, exc: PathSafetyError) -> Response:
    """Turn a rejected client-supplied path into a clean 400 instead of a 500."""
    return Response(content=str(exc), status_code=400, media_type="text/plain")


@app.exception_handler(WorkspaceError)
async def _workspace_error_handler(request: Request, exc: WorkspaceError) -> Response:
    """Code-Werkstatt: bad path / missing folder / oversized file → clean 400."""
    return Response(content=str(exc), status_code=400, media_type="text/plain")


# Optional, opt-in API token. The product API has no user accounts — it is meant to
# run on localhost for a single user. If SCIENCEKG_API_TOKEN is set, every request
# (except CORS preflight and the health probe) must carry a matching bearer token;
# if it is unset (the default) the API stays open so local dev is unchanged.
_AUTH_EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def _optional_token_auth(request: Request, call_next):
    token = os.getenv("SCIENCEKG_API_TOKEN", "").strip()
    if token and request.method != "OPTIONS" and request.url.path not in _AUTH_EXEMPT_PATHS:
        header = request.headers.get("authorization", "")
        provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not provided or not secrets.compare_digest(provided, token):
            return Response(content="Unauthorized", status_code=401, media_type="text/plain")
    return await call_next(request)


llm_router = LLMRouter.from_config_file("config.yaml")
parser_router = ParserRouter()
embedding_engine = EmbeddingEngine()
extraction_pipeline = ExtractionPipeline(llm_router, embedding_engine=embedding_engine)


class ReviewActionRequest(BaseModel):
    ids: list[int]
    action: str = Field(pattern="^(approve|reject)$")


class GraphExplorerResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    stats: dict[str, Any]


class BenchmarkJobRequest(BaseModel):
    gold_dir: str = "quality/gold"
    pred_dir: str | None = None
    allow_embedded_predictions: bool = True
    suite: str | None = Field(default=None, pattern="^(core|extended)$")
    context_policy: str = Field(default="auto", pattern="^(auto|whole|chunk)$")
    compare_context_policies: list[str] = []
    answer_context_mode: str = Field(default="kg", pattern="^(kg|pdf_if_fits)$")
    provider: str | None = None
    model: str | None = None
    download_missing: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    output_dir: str = "data/eval/benchmarks"
    isolated_db: bool = True


class BenchmarkSuiteJobRequest(BaseModel):
    suite: str = Field(default="core", pattern="^(core|extended)$")
    provider: str | None = None
    model: str | None = None
    context_policy: str = Field(default="auto", pattern="^(auto|whole|chunk)$")
    compare_context_policies: list[str] = []
    answer_context_mode: str = Field(default="kg", pattern="^(kg|pdf_if_fits)$")
    download_missing: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    output_dir: str = "data/eval/benchmarks"
    isolated_db: bool = True


class EvalJobRequest(BaseModel):
    provider: str
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    limit: int = Field(default=8, ge=1, le=25)
    timeout_seconds: float | None = Field(default=None, ge=1)


class HealthRepairRequest(BaseModel):
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    initialize_graph_fallback: bool = True
    reindex_embeddings: bool = True


class RewriteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    instruction: str = Field(default="Schreibe den Text klarer und wissenschaftlich um.", min_length=1, max_length=500)
    provider: str | None = None
    model: str | None = None


class ClaimCheckRequest(BaseModel):
    """Nachcheck: stützt die zitierte Quelle diese konkrete Aussage wirklich?"""

    statement: str = Field(min_length=1, max_length=4000)
    paper_ids: list[str] = Field(min_length=1, max_length=4)
    titles: dict[str, str] = Field(default_factory=dict)
    evidence_texts: dict[str, str] = Field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    # Bei unsicherem Urteil das ganze Paper nachprüfen (statt nur die Belegstelle).
    escalate_whole_paper: bool = True


class NotePayload(BaseModel):
    title: str = Field(default="Neue Notiz", min_length=1, max_length=180)
    markdown: str = Field(default="", max_length=200000)


class NotePatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=180)
    markdown: str | None = Field(default=None, max_length=200000)


class NoteAppendRequest(BaseModel):
    markdown: str = Field(min_length=1, max_length=80000)
    title: str | None = Field(default=None, max_length=180)
    citations: list[dict[str, Any]] = []


class NoteAiEditRequest(BaseModel):
    selected_text: str = Field(min_length=1, max_length=16000)
    instruction: str = Field(min_length=1, max_length=800)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    use_kg_evidence: bool = True


class NoteAiThreadRequest(NoteAiEditRequest):
    anchor_start: int | None = Field(default=None, ge=0)
    anchor_end: int | None = Field(default=None, ge=0)
    anchor_quote: str | None = Field(default=None, max_length=2000)


class NoteAiMessageRequest(BaseModel):
    message: str = Field(min_length=1, max_length=1200)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    use_kg_evidence: bool = True


class NoteAskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=1200)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    use_kg_evidence: bool = True


class NoteAiThreadPatch(BaseModel):
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    ui_state: dict[str, Any] | None = None



# --------------------------------------------------------------------------- #
# Parallel Research mode                                                       #
# --------------------------------------------------------------------------- #


def _parallel_retriever(metadata_db_path: str, graph_db_path: str) -> HybridRetriever:
    return HybridRetriever(KGRetriever(metadata_db_path, graph_db_path))


def _parallel_project_filter(project_id: str | None) -> str | None:
    """Reserved global modes don't scope retrieval to a project."""
    if not project_id or str(project_id).strip().lower() in RESERVED_PROJECT_IDS:
        return None
    return project_id


_AGENT_BRIDGE_CONFIG_CACHE: dict[str, Any] | None = None


def _load_agent_bridge_config() -> dict[str, Any]:
    """Load and cache the ``agent_bridge:`` section of config.yaml (desktop-agent hand-off)."""
    global _AGENT_BRIDGE_CONFIG_CACHE
    if _AGENT_BRIDGE_CONFIG_CACHE is not None:
        return _AGENT_BRIDGE_CONFIG_CACHE
    cfg: dict[str, Any] = {}
    try:
        with open("config.yaml", "r", encoding="utf-8") as fh:
            section = (yaml.safe_load(fh) or {}).get("agent_bridge", {}) or {}
            cfg = section if isinstance(section, dict) else {}
    except FileNotFoundError:
        cfg = {}
    _AGENT_BRIDGE_CONFIG_CACHE = cfg
    return cfg


_COMPANION_CONFIG_CACHE: dict[str, Any] | None = None


def _load_companion_config() -> dict[str, Any]:
    """Load and cache the ``companion:`` section of config.yaml (Desktop Companion)."""
    global _COMPANION_CONFIG_CACHE
    if _COMPANION_CONFIG_CACHE is not None:
        return _COMPANION_CONFIG_CACHE
    cfg: dict[str, Any] = {}
    try:
        with open("config.yaml", "r", encoding="utf-8") as fh:
            section = (yaml.safe_load(fh) or {}).get("companion", {}) or {}
            cfg = section if isinstance(section, dict) else {}
    except FileNotFoundError:
        cfg = {}
    _COMPANION_CONFIG_CACHE = cfg
    return cfg


def _companion_llm_params(provider: str | None, model: str | None) -> dict[str, Any]:
    """Merge request overrides with ``companion:`` defaults into screen_companion kwargs."""
    cfg = _load_companion_config()
    return {
        "provider": provider or (str(cfg.get("provider") or "").strip() or None),
        "model": model or (str(cfg.get("model") or "").strip() or None),
        "max_pixels": int(cfg.get("grounding_max_pixels") or screen_companion.DEFAULT_MAX_PIXELS),
        "history_turns": int(cfg.get("history_turns") or screen_companion.DEFAULT_HISTORY_TURNS),
        # None lets ask/guide fall back to their per-call defaults (ask < guide).
        "max_tokens": int(cfg.get("max_tokens") or 0) or None,
        "disable_thinking": bool(cfg.get("disable_thinking", True)),
    }


async def _companion_context(
    question: str, use_papers: bool, use_web: bool
) -> tuple[list[str], list[dict[str, Any]]]:
    """Optional grounding for companion answers (Quellen-Modus): local paper hits
    (KG + embeddings via HybridRetriever) and/or web-search results (titles +
    snippets only — no page fetches, latency-friendly). Best-effort on both paths:
    any failure yields an empty context so the screen answer still happens."""
    blocks: list[str] = []
    sources: list[dict[str, Any]] = []
    if use_papers:
        try:
            def _paper_hits():
                retriever = _parallel_retriever(DEFAULT_METADATA_DB_PATH, DEFAULT_GRAPH_DB_PATH)
                return retriever.search(question, limit=5)

            for hit in await asyncio.to_thread(_paper_hits):
                source = hit.source
                snippet = ""
                if hit.evidence:
                    snippet = max(hit.evidence, key=lambda item: item.score).text
                snippet = " ".join(str(snippet).split())[:400]
                blocks.append(
                    f"[{source.paper_id}] {source.title}" + (f" — {snippet}" if snippet else "")
                )
                sources.append({"type": "paper", "id": source.paper_id, "title": source.title})
        except Exception:  # noqa: BLE001 - grounding is best-effort
            pass
    if use_web:
        try:
            from research.sanitize import sanitize_web_text
            from research.search_provider import load_research_config, run_web_search

            for hit in await run_web_search(question, load_research_config(), max_results=5):
                clean_snippet, _flags = sanitize_web_text(hit.snippet or hit.title, max_len=400)
                blocks.append(f"(Web: {hit.url}) {hit.title} — {clean_snippet}")
                sources.append({"type": "web", "url": hit.url, "title": hit.title})
        except Exception:  # noqa: BLE001 - grounding is best-effort
            pass
    return blocks, sources


# --------------------------------------------------------------------------- #
# Native Selbst-Steuerung (R7) — planner store; endpoints live in               #
# api/routers/companion.py (referenced as pm._SELF_DRIVE_STORE at call time).   #
# --------------------------------------------------------------------------- #

_SELF_DRIVE_STORE = self_drive.SelfDriveStore()
_GUIDE_STORE = guide_flow.GuideStore()



@app.get("/models/providers")
def model_providers() -> dict[str, Any]:
    return {
        "default_provider": llm_router.default_provider,
        "providers": [_provider_view(provider) for provider in llm_router.available_providers()],
    }


@app.post("/models/{provider}/discover")
def discover_models(provider: str) -> dict[str, Any]:
    _ensure_provider(provider)
    return {"provider": provider, "models": llm_router.provider_model_options(provider, refresh=True)}


@app.post("/models/{provider}/check")
def check_model_provider(provider: str, model: str | None = None) -> dict[str, Any]:
    _ensure_provider(provider)
    cfg = llm_router.provider_config(provider)
    ok, error = llm_router.check_provider_auth(provider=provider, model=model, timeout_seconds=min(cfg.timeout_seconds, 30.0))
    return {"provider": provider, "model": model or llm_router.provider_default_model(provider), "ok": ok, "error": error}


@app.post("/tools/rewrite")
def rewrite_text(request: RewriteRequest) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "temperature": 0.15,
        "top_p": 0.9,
        "max_tokens": min(1800, max(300, len(request.text) // 2 + 300)),
    }
    if request.model:
        overrides["model"] = request.model
    try:
        text = llm_router.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Du bist ein praeziser wissenschaftlicher Schreibassistent. "
                        "Schreibe nur den gegebenen Text um, fuege keine neuen Fakten, "
                        "Quellen oder Zitate hinzu und erhalte vorhandene Zitationsmarker."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Aufgabe: {request.instruction}\n\nText:\n{request.text}",
                },
            ],
            provider=request.provider,
            overrides=overrides,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Rewrite failed: {exc}") from exc
    return {"text": str(text or "").strip(), "model": overrides.get("model") or llm_router.provider_default_model(request.provider)}


@app.post("/assistant/claim-check")
async def assistant_claim_check(request: ClaimCheckRequest) -> dict[str, Any]:
    """Prüft eine markierte/zitierte Aussage gegen ihre Quelle(n) (PDF → Abstract → Grau).

    Pro Quelle ein Urteil (gestützt / teilweise / nicht gestützt / nicht beurteilbar)
    mit wörtlichen Belegzitaten. LLM-Aufruf läuft blockierend → Thread.
    """
    from query.claim_checker import check_claim

    seen: set[str] = set()
    checks: list[dict[str, Any]] = []
    for raw_paper_id in request.paper_ids:
        pid = str(raw_paper_id or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        checks.append(
            await asyncio.to_thread(
                check_claim,
                llm_router,
                statement=request.statement,
                paper_id=pid,
                title=request.titles.get(pid, ""),
                evidence_text=request.evidence_texts.get(pid, ""),
                provider=request.provider,
                model=request.model,
                pdf_base_dir=request.pdf_base_dir,
                metadata_db_path=request.metadata_db_path,
                escalate_whole_paper=request.escalate_whole_paper,
            )
        )
    return {"statement": request.statement, "checks": checks}


@app.get("/projects/{project_id}/notes")
def list_project_notes(
    project_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        notes = [db.get_note(str(note.get("id"))) or note for note in db.list_notes(project_id=project_id, limit=1000)]
    return {"items": [_note_summary(note) for note in notes], "total": len(notes)}


@app.post("/projects/{project_id}/notes")
def create_project_note(
    project_id: str,
    payload: NotePayload,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.create_note(project_id=project_id, title=payload.title, markdown=payload.markdown)
    return {"note": _note_view(note)}


@app.get("/notes/{note_id}")
def get_note(note_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@app.patch("/notes/{note_id}")
def patch_note(
    note_id: str,
    payload: NotePatch,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.update_note(note_id, title=payload.title, markdown=payload.markdown)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@app.delete("/notes/{note_id}")
def delete_note(note_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_note(note_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"deleted": True}


@app.post("/notes/{note_id}/append")
def append_note(
    note_id: str,
    payload: NoteAppendRequest,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.append_note_markdown(
            note_id,
            markdown=payload.markdown,
            title=payload.title,
            citations=payload.citations,
        )
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@app.delete("/notes/{note_id}/citations/{citation_id}")
def delete_note_citation(
    note_id: str,
    citation_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_note_citation(note_id, citation_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Citation not found: {citation_id}")
    return {"deleted": True, "id": citation_id}


@app.post("/notes/{note_id}/versions/restore-latest")
def restore_latest_note_version(note_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        note = db.restore_latest_note_version(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
    return {"note": _note_view(note)}


@app.get("/notes/{note_id}/ai-threads")
def list_note_ai_threads(
    note_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    limit: int = Query(default=50, ge=1, le=200),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
        threads = db.list_note_ai_threads(note_id, limit=limit)
    return {"items": threads, "total": len(threads)}


@app.post("/notes/{note_id}/ai-threads")
def create_note_ai_thread(note_id: str, request: NoteAiThreadRequest) -> dict[str, Any]:
    thread = _create_note_ai_thread(note_id, request)
    return {
        "thread": thread,
        "replacement_text": thread.get("replacement_text") or thread.get("response_text") or "",
        "answer": thread.get("answer_payload") or {},
        "model": _note_ai_model(request),
    }


@app.patch("/notes/{note_id}/ai-threads/{thread_id}")
def patch_note_ai_thread(note_id: str, thread_id: str, request: NoteAiThreadPatch) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.get_note_ai_thread(thread_id)
        if thread is None or str(thread.get("note_id")) != note_id:
            raise HTTPException(status_code=404, detail=f"AI thread not found: {thread_id}")
        updated = db.update_note_ai_thread(thread_id, ui_state=request.ui_state or {})
    return {"thread": updated}


@app.delete("/notes/{note_id}/ai-threads/{thread_id}")
def delete_note_ai_thread(
    note_id: str,
    thread_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_thread(note_id, thread_id, metadata_db_path)


@app.post("/notes/{note_id}/ai-threads/{thread_id}/delete")
def delete_note_ai_thread_action(
    note_id: str,
    thread_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_thread(note_id, thread_id, metadata_db_path)


def _delete_note_ai_thread(note_id: str, thread_id: str, metadata_db_path: str) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        thread = db.get_note_ai_thread(thread_id)
        if thread is None or str(thread.get("note_id")) != note_id:
            raise HTTPException(status_code=404, detail=f"AI thread not found: {thread_id}")
        db.delete_note_ai_thread(thread_id)
    return {"deleted": True}


@app.delete("/notes/{note_id}/ai-threads")
def delete_note_ai_threads(
    note_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_threads(note_id, metadata_db_path)


@app.post("/notes/{note_id}/ai-threads/delete-all")
def delete_note_ai_threads_action(
    note_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    return _delete_note_ai_threads(note_id, metadata_db_path)


def _delete_note_ai_threads(note_id: str, metadata_db_path: str) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")
        deleted = db.delete_note_ai_threads(note_id)
    return {"deleted": deleted}


@app.post("/notes/{note_id}/ai-threads/{thread_id}/messages")
def append_note_ai_message(note_id: str, thread_id: str, request: NoteAiMessageRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.get_note_ai_thread(thread_id)
        if thread is None or str(thread.get("note_id")) != note_id:
            raise HTTPException(status_code=404, detail=f"AI thread not found: {thread_id}")

    selected = str(thread.get("selected_text") or "").strip()
    evidence_request = NoteAiEditRequest(
        selected_text=selected or str(thread.get("anchor_quote") or "Auswahl"),
        instruction=request.message,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        use_kg_evidence=request.use_kg_evidence,
    )
    answer_payload = _note_evidence_payload(evidence_request) if request.use_kg_evidence else {}
    response = _run_note_ai_chat(
        selected_text=selected,
        instruction=request.message,
        evidence_block=_note_evidence_prompt(answer_payload),
        provider=request.provider,
        model=request.model,
        prior_messages=thread.get("messages") if isinstance(thread.get("messages"), list) else [],
    )
    with MetadataDB(request.metadata_db_path) as db:
        user_message = db.add_note_ai_message(thread_id, note_id, "user", request.message.strip())
        assistant_message = db.add_note_ai_message(thread_id, note_id, "assistant", response)
        updated = db.update_note_ai_thread(thread_id, response_text=response, replacement_text=response)
        thread = updated or db.get_note_ai_thread(thread_id)
    return {
        "thread": thread,
        "user_message": user_message,
        "assistant_message": assistant_message,
        "replacement_text": response,
        "answer": answer_payload,
        "model": _note_ai_model(request),
    }


@app.post("/notes/{note_id}/ai-edit")
def note_ai_edit(note_id: str, request: NoteAiEditRequest) -> dict[str, Any]:
    thread = _create_note_ai_thread(
        note_id,
        NoteAiThreadRequest(
            selected_text=request.selected_text,
            instruction=request.instruction,
            provider=request.provider,
            model=request.model,
            metadata_db_path=request.metadata_db_path,
            graph_db_path=request.graph_db_path,
            use_kg_evidence=request.use_kg_evidence,
        ),
    )
    return {
        "thread": thread,
        "replacement_text": thread.get("replacement_text") or thread.get("response_text") or "",
        "answer": thread.get("answer_payload") or {},
        "model": _note_ai_model(request),
    }


@app.post("/notes/{note_id}/ask")
def ask_note(note_id: str, request: NoteAskRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        note = db.get_note(note_id)
    if note is None:
        raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")

    markdown = str(note.get("markdown") or "").strip()
    if not markdown:
        raise HTTPException(status_code=400, detail="Note is empty.")

    evidence_request = NoteAiEditRequest(
        selected_text=_note_ai_context(markdown),
        instruction=request.question,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        use_kg_evidence=request.use_kg_evidence,
    )
    answer_payload = _note_evidence_payload(evidence_request) if request.use_kg_evidence else {}
    response = _run_note_ai_chat(
        selected_text=_note_ai_context(markdown),
        instruction=request.question,
        evidence_block=_note_evidence_prompt(answer_payload),
        provider=request.provider,
        model=request.model,
        subject_label="Ganze Notiz",
    )
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.add_note_ai_thread(
            note_id=note_id,
            selected_text="",
            instruction=request.question.strip(),
            response_text=response,
            replacement_text=response,
            answer_payload=answer_payload,
            anchor_quote="",
            ui_state={"collapsed": True, "scope": "note"},
        )
    return {
        "thread": thread,
        "replacement_text": response,
        "answer": answer_payload,
        "model": _note_ai_model(request),
    }


def _create_note_ai_thread(note_id: str, request: NoteAiThreadRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")

    answer_payload = _note_evidence_payload(request) if request.use_kg_evidence else {}
    evidence_block = _note_evidence_prompt(answer_payload)
    instruction = request.instruction.strip()
    selected = request.selected_text.strip()
    replacement = _run_note_ai_chat(
        selected_text=selected,
        instruction=instruction,
        evidence_block=evidence_block,
        provider=request.provider,
        model=request.model,
    )
    with MetadataDB(request.metadata_db_path) as db:
        thread = db.add_note_ai_thread(
            note_id=note_id,
            selected_text=selected,
            instruction=instruction,
            response_text=replacement,
            replacement_text=replacement,
            answer_payload=answer_payload,
            anchor_start=request.anchor_start,
            anchor_end=request.anchor_end,
            anchor_quote=request.anchor_quote or selected[:2000],
            ui_state={"collapsed": True},
        )
    return thread


@app.post("/notes/{note_id}/assets")
async def upload_note_asset(
    note_id: str,
    request: Request,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    note_asset_dir: str = DEFAULT_NOTE_ASSET_DIR,
) -> dict[str, Any]:
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Upload body is empty.")
    content_type = request.headers.get("content-type") or "application/octet-stream"
    if not content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Only image assets are supported for notes.")

    with MetadataDB(metadata_db_path) as db:
        if db.get_note(note_id) is None:
            raise HTTPException(status_code=404, detail=f"Note not found: {note_id}")

    filename = _safe_asset_filename(request.headers.get("x-filename") or "note-image")
    target_dir = ensure_safe_path(note_asset_dir, what="note asset dir") / _slug(note_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    target_path = target_dir / f"{datetime.now().strftime('%Y%m%d%H%M%S%f')}_{filename}"
    target_path.write_bytes(content)

    with MetadataDB(metadata_db_path) as db:
        asset = db.add_note_asset(note_id, filename=filename, content_type=content_type, asset_path=str(target_path))
    return {"asset": {**asset, "url": f"/notes/assets/{asset['id']}"}}


@app.get("/notes/assets/{asset_id}")
def note_asset(
    asset_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    note_asset_dir: str = DEFAULT_NOTE_ASSET_DIR,
):
    with MetadataDB(metadata_db_path) as db:
        asset = db.get_note_asset(asset_id)
    if asset is None:
        raise HTTPException(status_code=404, detail=f"Asset not found: {asset_id}")

    asset_path = Path(str(asset.get("asset_path") or "")).resolve()
    base_path = Path(note_asset_dir).resolve()
    if base_path not in [asset_path, *asset_path.parents] or not asset_path.exists():
        raise HTTPException(status_code=404, detail=f"Asset file not found: {asset_id}")
    return FileResponse(
        path=str(asset_path),
        media_type=str(asset.get("content_type") or "application/octet-stream"),
        filename=str(asset.get("filename") or asset_path.name),
    )


@app.get("/review/entities")
def review_entities(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    status: str | None = "pending",
    query: str = "",
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        items = db.list_entity_review_queue(status=status, limit=limit)
    if query:
        query_lower = query.lower()
        items = [
            item for item in items
            if query_lower in str(item.get("label") or "").lower()
            or query_lower in str(item.get("suggested_canonical") or "").lower()
            or query_lower in str(item.get("paper_id") or "").lower()
        ]
    return {"items": items, "total": len(items)}


@app.post("/review/entities/actions")
def review_entity_actions(
    request: ReviewActionRequest,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    status = "approved" if request.action == "approve" else "rejected"
    ids = [int(item) for item in request.ids]
    if not ids:
        return {"updated": 0, "status": status}
    placeholders = ", ".join("?" for _ in ids)
    with MetadataDB(metadata_db_path) as db:
        db._execute(
            f"""
            UPDATE entity_review_queue
            SET review_status = ?, updated_timestamp = ?
            WHERE id IN ({placeholders})
            """,
            [status, datetime.now(), *ids],
        )
    return {"updated": len(ids), "status": status}


@app.get("/graph/explorer", response_model=GraphExplorerResponse)
def graph_explorer(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    projects_path: str | None = None,
    project_id: str | None = None,
    query: str = "",
    edge_types: list[str] | None = Query(default=None),
    limit: int = Query(default=1200, ge=5, le=5000),
) -> GraphExplorerResponse:
    projects = _load_projects(_projects_path(projects_path))
    selected_ids = set(projects.get(project_id, [])) if project_id else None
    requested_edges = set(_split_query_values(edge_types) or ["cites", "concept", "method", "similar"])

    with MetadataDB(metadata_db_path) as db:
        papers = db.list_papers(limit=50000)
        extractions = db.list_extraction_results(limit=50000)

    if selected_ids is not None:
        papers = [paper for paper in papers if str(paper.get("id")) in selected_ids]
    if query:
        papers = [paper for paper in papers if _paper_matches_query(paper, query)]
    total_matching = len(papers)

    # Papers with a successful extraction are the actual knowledge graph content
    # and must never be pushed out by `limit` — `limit` only bounds how many
    # additional non-extracted papers are pulled in for citation/similarity context.
    extraction_by_paper = _latest_successful_extractions(extractions)
    extracted_papers = [paper for paper in papers if str(paper.get("id")) in extraction_by_paper]
    other_papers = [paper for paper in papers if str(paper.get("id")) not in extraction_by_paper]
    extracted_count = len(extracted_papers)
    effective_limit = max(limit, extracted_count)
    papers = (extracted_papers + other_papers)[:effective_limit]
    paper_ids = {str(paper.get("id")) for paper in papers}
    truncated = len(papers) < total_matching

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    for paper in papers:
        pid = str(paper.get("id") or paper_id(paper))
        nodes[pid] = {
            "id": pid,
            "label": str(paper.get("title") or pid)[:120],
            "type": "paper",
            "year": paper.get("year"),
            "metadata": {"source": paper.get("source"), "source_id": paper.get("source_id")},
        }

    if "cites" in requested_edges or "similar" in requested_edges:
        refs_by_paper = {str(paper.get("id")): set(extract_citation_ids(paper)) for paper in papers}
        if "cites" in requested_edges:
            for source_id, refs in refs_by_paper.items():
                for ref in refs:
                    if ref in paper_ids:
                        _add_edge(edges, source_id, ref, "cites", "CITES")
        if "similar" in requested_edges:
            ids = list(refs_by_paper)
            for index, source_id in enumerate(ids):
                for target_id in ids[index + 1 :]:
                    shared = refs_by_paper[source_id] & refs_by_paper[target_id]
                    union = refs_by_paper[source_id] | refs_by_paper[target_id]
                    if shared and union:
                        score = len(shared) / len(union)
                        if score >= 0.1:
                            _add_edge(edges, source_id, target_id, "similar", "SIMILAR", score=round(score, 4))

    for pid in paper_ids:
        extraction = extraction_by_paper.get(pid)
        if not extraction:
            continue
        if "concept" in requested_edges:
            for concept in _iter_labeled_items(extraction.get("concepts"))[:12]:
                node_id = str(concept.get("canonical_id") or f"concept:{_slug(concept.get('label'))}")
                nodes.setdefault(
                    node_id,
                    {"id": node_id, "label": concept.get("canonical_label") or concept.get("label"), "type": "concept", "metadata": concept},
                )
                _add_edge(edges, pid, node_id, "concept", "HAS_CONCEPT", score=concept.get("confidence"))
        if "method" in requested_edges:
            for method in _iter_labeled_items(extraction.get("methods"))[:12]:
                node_id = str(method.get("canonical_id") or f"method:{_slug(method.get('label'))}")
                nodes.setdefault(
                    node_id,
                    {"id": node_id, "label": method.get("canonical_label") or method.get("label"), "type": "method", "metadata": method},
                )
                _add_edge(edges, pid, node_id, "method", "HAS_METHOD", score=method.get("confidence"))

    return GraphExplorerResponse(
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        stats={
            "paper_count": len(papers),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "edge_types": sorted({edge["type"] for edge in edges.values()}),
            "total_paper_count": total_matching,
            "extracted_paper_count": extracted_count,
            "truncated": truncated,
        },
    )


@app.get("/jobs")
def jobs(metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"jobs": db.list_batch_jobs(limit=100)}


@app.post("/jobs/graph-rebuild")
def graph_rebuild_job(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH,
    include_extractions: bool = True,
) -> dict[str, Any]:
    result = build_phase2_graph(
        BuildGraphRequest(
            metadata_db_path=metadata_db_path,
            graph_db_path=graph_db_path,
            include_extractions=include_extractions,
        )
    )
    return {"status": "completed", "result": result}


@app.post("/jobs/health-repair")
def health_repair_job(request: HealthRepairRequest) -> dict[str, Any]:
    return repair_health_state(
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        pdf_base_dir=request.pdf_base_dir,
        initialize_graph_fallback=request.initialize_graph_fallback,
        reindex_embeddings=request.reindex_embeddings,
    )


@app.post("/jobs/benchmark")
def benchmark_job(request: BenchmarkJobRequest) -> dict[str, Any]:
    if request.suite:
        report = _run_benchmark_suite_job(request)
        return {"status": "completed", "report": report}
    started = time.perf_counter()
    report = run_benchmark(
        gold_dir=Path(request.gold_dir),
        pred_dir=Path(request.pred_dir) if request.pred_dir else None,
        allow_embedded_predictions=request.allow_embedded_predictions,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    run = _persist_benchmark_run(
        kind="extraction",
        provider=None,
        model=None,
        summary=report.get("summary") or {},
        report=report,
        duration_ms=duration_ms,
        metadata_db_path=request.metadata_db_path,
    )
    return {"status": "completed", "report": report, "run": run}


@app.post("/jobs/benchmark-suite")
def benchmark_suite_job(request: BenchmarkSuiteJobRequest) -> dict[str, Any]:
    report = _run_benchmark_suite_job(request)
    return {"status": "completed", "report": report}


@app.get("/quality/benchmark-suite/latest")
def benchmark_suite_latest(output_dir: str = "data/eval/benchmarks") -> dict[str, Any]:
    report = latest_suite_report(Path(output_dir))
    return {"status": "ok" if report else "empty", "report": report}


@app.post("/jobs/eval")
def eval_job(request: EvalJobRequest) -> dict[str, Any]:
    started = time.perf_counter()
    report = run_eval(
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        limit=request.limit,
        timeout_seconds=request.timeout_seconds,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    run = _persist_benchmark_run(
        kind="qa",
        provider=report.get("provider") or request.provider,
        model=report.get("model") or request.model,
        summary=report.get("summary") or {},
        report=report,
        duration_ms=duration_ms,
        metadata_db_path=request.metadata_db_path,
    )
    return {"status": "completed", "report": report, "run": run}


def _persist_benchmark_run(
    *,
    kind: str,
    provider: str | None,
    model: str | None,
    summary: dict[str, Any],
    report: dict[str, Any],
    duration_ms: int,
    metadata_db_path: str,
) -> dict[str, Any]:
    try:
        with MetadataDB(metadata_db_path) as db:
            return db.add_benchmark_run(
                {
                    "kind": kind,
                    "provider": provider,
                    "model": model,
                    "summary": summary,
                    "report": report,
                    "duration_ms": duration_ms,
                }
            )
    except Exception:
        return {}


@app.get("/benchmark/runs")
def list_benchmark_runs(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        runs = db.list_benchmark_runs(kind=kind, limit=limit)
    return {"items": runs, "total": len(runs)}


@app.delete("/benchmark/runs/{run_id}")
def delete_benchmark_run(run_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_benchmark_run(run_id)
    return {"deleted": deleted, "id": run_id}


# --------------------------------------------------------------------------- #
# Code-Werkstatt (coding-project folders, file tree, editor, git)             #
# --------------------------------------------------------------------------- #












# --------------------------------------------------------------------------- #
# Analyse-Werkstatt (WP1): reproduzierbare, provenance-tragende Skript-Läufe    #
# --------------------------------------------------------------------------- #








def _run_benchmark_suite_job(request: BenchmarkJobRequest | BenchmarkSuiteJobRequest) -> dict[str, Any]:
    policies = list(getattr(request, "compare_context_policies", None) or []) or [request.context_policy]
    return run_suite(
        SuiteConfig(
            suite=getattr(request, "suite", None) or "core",
            provider=request.provider,
            model=request.model,
            context_policy=request.context_policy,
            compare_context_policies=policies,
            answer_context_mode=request.answer_context_mode,
            download_missing=bool(request.download_missing),
            metadata_db_path=request.metadata_db_path,
            graph_db_path=request.graph_db_path,
            pdf_base_dir=request.pdf_base_dir,
            output_dir=Path(request.output_dir),
            isolated_db=bool(request.isolated_db),
        )
    )


def _note_summary(note: dict[str, Any]) -> dict[str, Any]:
    markdown = str(note.get("markdown") or "")
    return {
        "id": note.get("id"),
        "project_id": note.get("project_id"),
        "title": note.get("title") or "Neue Notiz",
        "markdown": markdown,
        "excerpt": _note_excerpt(markdown),
        "citation_count": len(note.get("citations") or []),
        "asset_count": len(note.get("assets") or []),
        "created_timestamp": note.get("created_timestamp"),
        "updated_timestamp": note.get("updated_timestamp"),
    }


def _note_view(note: dict[str, Any]) -> dict[str, Any]:
    citations = [dict(item) for item in note.get("citations") or []]
    assets = [{**dict(item), "url": f"/notes/assets/{item.get('id')}"} for item in note.get("assets") or []]
    return {
        **_note_summary({**note, "citations": citations, "assets": assets}),
        "citations": citations,
        "assets": assets,
    }


def _note_excerpt(markdown: str) -> str:
    text = re.sub(r"!\[[^\]]*\]\([^)]+\)", " ", markdown)
    text = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", text)
    text = re.sub(r"[#>*_`|~-]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:180]


def _note_evidence_payload(request: NoteAiEditRequest) -> dict[str, Any]:
    if not _instruction_needs_evidence(request.instruction):
        return {}
    retriever = HybridRetriever(KGRetriever(metadata_db_path=request.metadata_db_path, graph_db_path=request.graph_db_path))
    hits = retriever.search(f"{request.selected_text} {request.instruction}", limit=6)
    sources: dict[str, dict[str, Any]] = {}
    evidence: list[dict[str, Any]] = []
    for hit in hits:
        source = hit.source.to_dict()
        sources[source["paper_id"]] = source
        for item in hit.evidence[:3]:
            evidence.append(item.to_dict())
            if len(evidence) >= 12:
                break
        if len(evidence) >= 12:
            break
    return {"sources": list(sources.values()), "evidence": evidence}


def _run_note_ai_chat(
    selected_text: str,
    instruction: str,
    evidence_block: str,
    provider: str | None = None,
    model: str | None = None,
    prior_messages: list[dict[str, Any]] | None = None,
    subject_label: str = "Markierter Text",
) -> str:
    overrides: dict[str, Any] = {
        "temperature": 0.18,
        "top_p": 0.9,
        "max_tokens": min(2400, max(450, len(selected_text) // 2 + 500)),
    }
    if model:
        overrides["model"] = model
    messages: list[dict[str, str]] = [
        {
            "role": "system",
            "content": (
                "Du bist ein lokaler wissenschaftlicher Markdown-Schreibassistent. "
                "Bearbeite nur den bereitgestellten Text und den bisherigen Verlauf zu diesem Kontext. "
                "Gib direkt Markdown zurueck. Nutze ausschliesslich bereitgestellte KG-Evidenz, "
                "wenn du neue Belege ergaenzt, und zitiere dann mit den angegebenen Paper-IDs in eckigen Klammern. "
                "WICHTIG: Markdown-Links der Form [Zx - Titel](sciencekg://citation/...) sind Quellenanker "
                "und duerfen niemals entfernt, gekuerzt oder umformuliert werden. Wenn du einen Satz oder ein "
                "Zitat behaeltst, kuerzt oder umschreibst, uebernimm seinen sciencekg://-Link zeichengenau "
                "(Linktext UND URL) an der passenden Stelle deiner Antwort. Auch Blockzitate (> ...) mit "
                "solchen Links behalten ihre Quellenzeile."
            ),
        }
    ]
    for item in (prior_messages or [])[-8:]:
        role = "assistant" if item.get("role") == "assistant" else "user"
        content = str(item.get("content") or "").strip()
        if content:
            messages.append({"role": role, "content": content})
    messages.append(
        {
            "role": "user",
            "content": (
                f"Aufgabe: {instruction.strip()}\n\n"
                f"{subject_label}:\n{selected_text.strip()}\n\n"
                f"{evidence_block}"
            ),
        }
    )
    try:
        response = llm_router.chat(messages, provider=provider, overrides=overrides)
        if _note_ai_response_needs_retry(response, selected_text):
            retry_overrides = _note_ai_retry_overrides(overrides)
            retry_messages = [
                *messages,
                {
                    "role": "user",
                    "content": (
                        "Die vorige Antwort war leer oder hat nur den markierten Text wiederholt. "
                        "Antworte jetzt direkt auf die Aufgabe. Wiederhole den markierten Text nicht. "
                        "Denke nicht lange intern nach. Gib sofort die finale Antwort aus. "
                        "Wenn eine Zusammenfassung verlangt wird, schreibe 2-4 kurze Saetze in einfacher Sprache."
                    ),
                },
            ]
            response = llm_router.chat(retry_messages, provider=provider, overrides=retry_overrides)
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"AI edit failed: {exc}") from exc
    response_text = str(response or "").strip()
    if _note_ai_response_needs_retry(response_text, selected_text):
        raise HTTPException(
            status_code=502,
            detail="AI edit failed: provider returned an empty or unchanged answer.",
        )
    return response_text


def _note_ai_response_needs_retry(response: Any, selected_text: str) -> bool:
    response_text = str(response or "").strip()
    if not response_text:
        return True
    selected = _normalize_note_ai_echo_text(selected_text)
    answer = _normalize_note_ai_echo_text(response_text)
    if not selected or len(selected) < 24:
        return False
    if answer == selected:
        return True
    if len(answer) >= int(len(selected) * 0.9) and (answer in selected or selected in answer):
        return True
    return False


def _note_ai_retry_overrides(overrides: dict[str, Any]) -> dict[str, Any]:
    retry = dict(overrides)
    retry["temperature"] = min(float(retry.get("temperature", 0.18)), 0.08)
    retry["max_tokens"] = max(int(retry.get("max_tokens") or 0) * 4, 2048)
    extra = dict(retry.get("extra") or {})
    extra["include_reasoning"] = False
    extra["chat_template_kwargs"] = {"enable_thinking": False, "thinking": False}
    retry["extra"] = extra
    return retry


def _normalize_note_ai_echo_text(value: str) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    text = re.sub(r"^[>*\-\s]+", "", text)
    return text


def _note_ai_model(request: NoteAiEditRequest | NoteAiMessageRequest | NoteAskRequest) -> str:
    return request.model or llm_router.provider_default_model(request.provider)


def _note_ai_context(markdown: str, max_chars: int = 16000) -> str:
    text = re.sub(r"\s+", " ", str(markdown or "")).strip()
    return text[:max_chars]


def _instruction_needs_evidence(instruction: str) -> bool:
    text = instruction.lower()
    return any(token in text for token in ["beleg", "beweis", "evidence", "quelle", "zitat", "citation", "argument"])


def _note_evidence_prompt(answer_payload: dict[str, Any]) -> str:
    evidence = answer_payload.get("evidence") if isinstance(answer_payload, dict) else None
    sources = answer_payload.get("sources") if isinstance(answer_payload, dict) else None
    if not evidence:
        return "Keine zusaetzliche KG-Evidenz bereitgestellt."
    titles = {
        str(source.get("paper_id")): str(source.get("title") or source.get("paper_id"))
        for source in (sources or [])
        if isinstance(source, dict)
    }
    lines = ["Lokale KG-Evidenz, die du verwenden darfst:"]
    for index, item in enumerate(evidence[:12], start=1):
        if not isinstance(item, dict):
            continue
        paper_id = str(item.get("paper_id") or "")
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        lines.append(f"{index}. [{paper_id}] {titles.get(paper_id, paper_id)} | {item.get('kind')}: {text}")
    return "\n".join(lines)


def _safe_asset_filename(filename: str) -> str:
    raw = Path(filename).name.strip() or "note-image"
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", Path(raw).stem).strip("-") or "note-image"
    suffix = Path(raw).suffix.lower()
    if suffix not in {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".svg"}:
        suffix = ".png"
    return f"{stem[:80]}{suffix}"


def _provider_view(provider: str) -> dict[str, Any]:
    cfg = llm_router.provider_config(provider)
    settings = llm_router.provider_settings(provider)
    return {
        "name": provider,
        "provider_type": cfg.provider_type,
        "base_url": cfg.base_url,
        "default_model": settings.model,
        "models": llm_router.provider_model_options(provider, refresh=False),
        "settings": {
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_tokens,
            "context_size": settings.context_size,
        },
        "auth_configured": bool(cfg.api_key),
    }


def _ensure_provider(provider: str) -> None:
    if provider not in llm_router.available_providers():
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")


def _iter_labeled_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("label")]


def _add_edge(
    edges: dict[str, dict[str, Any]],
    source: str,
    target: str,
    edge_type: str,
    label: str,
    score: Any | None = None,
) -> None:
    edge_id = f"{source}->{edge_type}->{target}"
    edges.setdefault(
        edge_id,
        {"id": edge_id, "source": source, "target": target, "type": edge_type, "label": label, "score": score},
    )


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or "item"


def _split_query_values(values: list[str] | None) -> list[str]:
    output: list[str] = []
    for value in values or []:
        output.extend(item.strip() for item in value.split(",") if item.strip())
    return output


if __name__ == "__main__":
    import uvicorn

    # No built-in auth unless SCIENCEKG_API_TOKEN is set — default to loopback so a
    # direct `python api/product_main.py` run doesn't expose it to the whole LAN.
    uvicorn.run(app, host="127.0.0.1", port=8000)
