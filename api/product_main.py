from __future__ import annotations

import asyncio
import os
import secrets
from contextlib import asynccontextmanager
from typing import Any

import httpx  # noqa: F401  # pm.httpx.AsyncClient (papers/harvest/agent/grey-Router) + Test-Patch-Surface
import yaml
from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api import phase4_main
from extraction.embedding_engine import EmbeddingEngine
from extraction.entity_linker import ExtractionPipeline
from quality.benchmark_suite import (  # noqa: F401  # pm.run_suite (review_graph_jobs-Router) + Test-Patch-Surface
    run_suite,
)
from parsing.parser_router import ParserRouter
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter
from query.auto_answer import (  # noqa: F401  # pm.auto_research_answer (discovery-Router) + Test-Patch-Surface
    auto_research_answer,
)
from query.research_tree import (  # noqa: F401  # pm.ResearchTreeRunner (discovery-Router) + Test-Patch-Surface
    ResearchTreeRunner,
)
from query import guide_flow, screen_companion, self_drive
from storage.atomic_json import CorruptJsonError
from storage.instance_lock import InstanceLock, InstanceLockError
from storage.metadata_db import MetadataDBLockedError
from storage.path_safety import PathSafetyError
from workspace import (  # noqa: F401  # pm.workspace_manager (routers) + test patch surface
    manager as workspace_manager,
)
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
from api.routers import codegraph as _codegraph_router  # noqa: E402

app.include_router(_codegraph_router.router)
from api.routers import pdf_annotations as _pdf_annotations_router  # noqa: E402

app.include_router(_pdf_annotations_router.router)
from api.routers import parallel as _parallel_router  # noqa: E402

app.include_router(_parallel_router.router)
from api.routers import companion as _companion_router  # noqa: E402

app.include_router(_companion_router.router)
from api.routers import projects as _projects_router  # noqa: E402

app.include_router(_projects_router.router)
from api.routers import graph_bundle as _graph_bundle_router  # noqa: E402

app.include_router(_graph_bundle_router.router)
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
from api.routers import models_meta as _models_meta_router  # noqa: E402

app.include_router(_models_meta_router.router)
from api.routers import tools as _tools_router  # noqa: E402

app.include_router(_tools_router.router)
from api.routers import notes as _notes_router  # noqa: E402

app.include_router(_notes_router.router)
from api.routers import review_graph_jobs as _review_graph_jobs_router  # noqa: E402

app.include_router(_review_graph_jobs_router.router)
from api.routers.review_graph_jobs import (  # noqa: E402,F401  # pm._slug (extraction/notes-Router)
    _slug,
)
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


@app.exception_handler(MetadataDBLockedError)
async def _metadata_db_locked_handler(
    request: Request, exc: MetadataDBLockedError
) -> Response:
    """Zweites Backend auf derselben DuckDB → 503 mit erklaerender Meldung.

    Vorher schlug das als 500 durch, und weil das Frontend Fehler als leere Listen
    rendert, sah ein Lock-Konflikt aus wie ein Totalverlust aller Projekte.
    """
    return Response(content=str(exc), status_code=503, media_type="text/plain")


@app.exception_handler(CorruptJsonError)
async def _corrupt_json_handler(request: Request, exc: CorruptJsonError) -> Response:
    """Beschaedigte projects.json & Co. laut melden statt still als leer behandeln."""
    return Response(content=str(exc), status_code=500, media_type="text/plain")


# Ein-Instanz-Guard: verhindert, dass ein zweites Backend (manuelles uvicorn neben
# dem Tauri-Sidecar, vergessener Docker-Stack) dieselbe DuckDB greift und die UI
# dadurch leer aussehen laesst. Siehe storage/instance_lock.py.
_instance_lock = InstanceLock(DEFAULT_METADATA_DB_PATH)


def _release_lock_safe(*_exc: object) -> None:
    """Lock freigeben, ohne je zu werfen — Backstop fuer atexit/SIGTERM.

    uvicorn's Lifespan-finally deckt den sauberen Shutdown ab, aber ein
    SIGTERM (z.B. Tauri-Sidecar-Stopp, ``kill <pid>``) oder ein harter
    Interpreter-Exit kann das finally ueberspringen. atexit + SIGTERM-Handler
    sorgen, dass der Heartbeat-Eintrag trotzdem aufgeraeumt wird, damit der
    naechste Start nicht bis zu 45s auf einen verwaisten Heartbeat wartet
    (siehe PID-Liveness-Fast-Path in instance_lock._foreign_live_holder).
    """
    try:
        _instance_lock.release()
    except Exception:
        pass


def _release_lock_and_exit(signum: int, _frame: object) -> None:
    _release_lock_safe()
    # uvicorn's eigenes SIGINT-Handler bleibt unangetastet; fuer SIGTERM
    # rufen wir nach dem Aufräumen sys.exit auf, damit der Prozess endet.
    import sys

    sys.exit(128 + signum)


@asynccontextmanager
async def _lifespan(_app: FastAPI):
    try:
        _instance_lock.acquire()
    except InstanceLockError as error:
        # Ohne diese Zeile sieht der Nutzer nur einen Python-Traceback und
        # uebersieht die eigentliche Erklaerung darin.
        print(f"\n  ScienceKG kann nicht starten\n\n  {error}\n", flush=True)
        raise
    # Backstop: atexit (sauberer Exit) + SIGTERM (kill, Sidecar-Stopp). SIGINT
    # bleibt bei uvicorn's Handler, der den Lifespan-finally sauber ausfuehrt.
    import atexit
    import signal

    atexit.register(_release_lock_safe)
    _prev_sigterm = signal.getsignal(signal.SIGTERM)
    try:
        signal.signal(signal.SIGTERM, _release_lock_and_exit)
    except (ValueError, OSError):
        # Im Hauptthread funktioniert signal.signal; in Nebenthreads (z.B.
        # TestClient, der die Lifespan in einem Thread ausfuehrt) wirft es
        # ValueError. atexit greift auch dann, der SIGTERM-Handler fehlt nur
        # in diesem nicht-produktiven Kontext.
        _prev_sigterm = None
    try:
        yield
    finally:
        _instance_lock.release()
        atexit.unregister(_release_lock_safe)
        if _prev_sigterm is not None:
            try:
                signal.signal(signal.SIGTERM, _prev_sigterm)
            except (ValueError, OSError):
                # Im Hauptthread funktioniert signal.signal; in Nebenthreads
                # war auch vorher kein Handler gesetzt.
                pass


app.router.lifespan_context = _lifespan


# Optional, opt-in API token. The product API has no user accounts — it is meant to
# run on localhost for a single user. If SCIENCEKG_API_TOKEN is set, every request
# (except CORS preflight and the health probe) must carry a matching bearer token;
# if it is unset (the default) the API stays open so local dev is unchanged.
_AUTH_EXEMPT_PATHS = {"/health", "/docs", "/openapi.json", "/redoc"}


@app.middleware("http")
async def _optional_token_auth(request: Request, call_next):
    token = os.getenv("SCIENCEKG_API_TOKEN", "").strip()
    if (
        token
        and request.method != "OPTIONS"
        and request.url.path not in _AUTH_EXEMPT_PATHS
    ):
        header = request.headers.get("authorization", "")
        provided = header[7:].strip() if header.lower().startswith("bearer ") else ""
        if not provided or not secrets.compare_digest(provided, token):
            return Response(
                content="Unauthorized", status_code=401, media_type="text/plain"
            )
    return await call_next(request)


llm_router = LLMRouter.from_config_file("config.yaml")
parser_router = ParserRouter()
embedding_engine = EmbeddingEngine()
extraction_pipeline = ExtractionPipeline(llm_router, embedding_engine=embedding_engine)


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
        "max_pixels": int(
            cfg.get("grounding_max_pixels") or screen_companion.DEFAULT_MAX_PIXELS
        ),
        "history_turns": int(
            cfg.get("history_turns") or screen_companion.DEFAULT_HISTORY_TURNS
        ),
        # None lets ask/guide fall back to their per-call defaults (ask < guide).
        "max_tokens": int(cfg.get("max_tokens") or 0) or None,
        "disable_thinking": bool(cfg.get("disable_thinking", True)),
    }


async def _companion_context(
    question: str,
    use_papers: bool,
    use_web: bool,
    use_code: bool = False,
    code_project_id: str | None = None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Optional grounding for companion answers (Quellen-Modus): local paper hits
    (KG + embeddings via HybridRetriever), web-search results (titles + snippets
    only — no page fetches, latency-friendly) and/or the code graph of a selected
    Werkstatt project. Best-effort on every path: any failure yields an empty
    context so the screen answer still happens."""
    blocks: list[str] = []
    sources: list[dict[str, Any]] = []
    if use_papers:
        try:

            def _paper_hits():
                retriever = _parallel_retriever(
                    DEFAULT_METADATA_DB_PATH, DEFAULT_GRAPH_DB_PATH
                )
                return retriever.search(question, limit=5)

            for hit in await asyncio.to_thread(_paper_hits):
                source = hit.source
                snippet = ""
                if hit.evidence:
                    snippet = max(hit.evidence, key=lambda item: item.score).text
                snippet = " ".join(str(snippet).split())[:400]
                blocks.append(
                    f"[{source.paper_id}] {source.title}"
                    + (f" — {snippet}" if snippet else "")
                )
                sources.append(
                    {"type": "paper", "id": source.paper_id, "title": source.title}
                )
        except Exception:  # noqa: BLE001 - grounding is best-effort
            pass
    if use_web:
        try:
            from research.sanitize import sanitize_web_text
            from research.search_provider import load_research_config, run_web_search

            for hit in await run_web_search(
                question, load_research_config(), max_results=5
            ):
                clean_snippet, _flags = sanitize_web_text(
                    hit.snippet or hit.title, max_len=400
                )
                blocks.append(f"(Web: {hit.url}) {hit.title} — {clean_snippet}")
                sources.append({"type": "web", "url": hit.url, "title": hit.title})
        except Exception:  # noqa: BLE001 - grounding is best-effort
            pass
    if use_code and code_project_id:
        try:

            def _code_context() -> str:
                from codegraph import service
                from storage.metadata_db import MetadataDB

                with MetadataDB(DEFAULT_METADATA_DB_PATH) as db:
                    project = db.get_code_project(str(code_project_id))
                if project is None:
                    return ""
                # Dieselbe deterministische Vorab-Suche wie im Code-Begleiter —
                # die wahrscheinlichen Symbole samt Quelltext, in einem Aufruf.
                retrieval = (
                    service.query(
                        project,
                        "context_build",
                        {"question": question, "session": "companion"},
                    )
                    or {}
                )
                return str(retrieval.get("context") or "")

            context = await asyncio.to_thread(_code_context)
            if context:
                # Gedeckelt: der Companion-Prompt trägt schon ein Bildschirmfoto.
                blocks.append(f"(Code-Graph) {context[:4000]}")
                sources.append(
                    {"type": "code", "id": str(code_project_id), "title": "Code-Graph"}
                )
        except Exception:  # noqa: BLE001 - grounding is best-effort
            pass
    return blocks, sources


# --------------------------------------------------------------------------- #
# Native Selbst-Steuerung (R7) — planner store; endpoints live in               #
# api/routers/companion.py (referenced as pm._SELF_DRIVE_STORE at call time).   #
# --------------------------------------------------------------------------- #

_SELF_DRIVE_STORE = self_drive.SelfDriveStore()
_GUIDE_STORE = guide_flow.GuideStore()


if __name__ == "__main__":
    import uvicorn

    # No built-in auth unless SCIENCEKG_API_TOKEN is set — default to loopback so a
    # direct `python api/product_main.py` run doesn't expose it to the whole LAN.
    uvicorn.run(app, host="127.0.0.1", port=8000)
