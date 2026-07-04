from __future__ import annotations

import asyncio
import io
import json
import os
import re
import secrets
import time
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx
import yaml
from fastapi import BackgroundTasks, FastAPI, HTTPException, Query, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field

from api import phase4_main
from api.main import BuildGraphRequest, build_phase2_graph
from extraction.batch_processor import BatchProcessor
from extraction.embedding_engine import EmbeddingEngine
from extraction.entity_extractor import EntityExtractor, extraction_failure_reason
from extraction.entity_linker import ExtractionPipeline
from extraction.reference_parser import extract_reference_section, split_reference_entries
from extraction.vocabulary import VocabularyManager
from graph.paper_ingestion import extract_citation_ids, paper_id
from harvester.arxiv_client import ArxivClient
from harvester.core_client import CoreApiKeyMissing, CoreClient, CoreConfig
from harvester.crossref_client import CrossrefClient, CrossrefConfig
from harvester.doaj_client import DoajClient, DoajConfig
from harvester.europepmc_client import EuropePMCClient, EuropePMCConfig
from harvester.oa_resolver import resolve_oa_pdf_url
from harvester.url_guard import is_safe_public_url
from harvester.openalex_client import OpenAlexClient
from harvester.semantic_scholar_client import SemanticScholarClient
from quality.benchmark import run_benchmark
from quality.benchmark_suite import SuiteConfig, latest_suite_report, run_suite
from quality.kg_health import build_health_report
from quality.pdf_resolver import BenchmarkPdfResolver, _looks_like_pdf
from quality.phase4_eval import run_eval
from maintenance.health_repair import repair_health_state
from parsing.parser_router import ParserRouter, ParserType
from query.discovery import analyze_paper, analyze_topic
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter
from query.auto_harvester import ingest_paper_record
from query.auto_answer import auto_research_answer
from query.research_tree import ResearchTreeRunner, _extract_questions
from query import parallel_research
from query import agent_handoff
from query import screen_companion, self_drive
from query.web_research import run_deep_research
from export import ExportOptions, build_export
from query.source_verifier import find_pdf_path
from research.sanitize import FULL_TEXT_MAX_LEN, sanitize_web_text
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB
from storage.path_safety import PathSafetyError, ensure_safe_path
from workspace import manager as workspace_manager
from workspace.manager import WorkspaceError


PROJECTS_PATH = Path("data/projects.json")
PROJECT_PRIMARY_PATH = Path("data/project_primary.json")
RESERVED_PROJECT_IDS = {"__all_papers__", "alle papers", "all papers"}
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


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    paper_ids: list[str] = []


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    paper_ids: list[str] | None = None


class ProjectPaperPayload(BaseModel):
    paper_ids: list[str]


class HarvestSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    sources: list[str] = ["arxiv"]
    max_results: int = Field(default=10, ge=1, le=50)


class HarvestDownloadRequest(BaseModel):
    papers: list[dict[str, Any]]
    download_pdfs: bool = True
    project_id: str | None = None
    projects_path: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class PaperIngestRequest(BaseModel):
    paper_id: str = Field(min_length=1)
    project_id: str | None = None
    provider: str | None = None
    model: str | None = None
    projects_path: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class ReferenceExtractRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    pdf_path: str | None = Field(default=None, max_length=1000)
    parser: str | None = Field(default=None, max_length=80)
    max_references: int = Field(default=40, ge=1, le=200)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class DiscoveryTopicRequest(BaseModel):
    topic: str = Field(min_length=1, max_length=2000)
    sources: list[str] = ["arxiv", "openalex"]
    provider: str | None = None
    max_per_query: int = Field(default=5, ge=1, le=20)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class DiscoveryPaperRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    pdf_path: str | None = Field(default=None, max_length=1000)
    parser: str | None = Field(default=None, max_length=80)
    sources: list[str] = ["arxiv", "openalex"]
    provider: str | None = None
    max_per_query: int = Field(default=5, ge=1, le=20)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class DeepResearchRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = None
    search_provider: str | None = None
    max_queries: int = Field(default=5, ge=1, le=12)
    results_per_query: int = Field(default=6, ge=1, le=15)
    max_sources: int = Field(default=12, ge=1, le=30)


class AutoAnswerRequest(BaseModel):
    """Answer a workspace question, auto-harvesting papers + web sources if it is weak."""
    question: str = Field(min_length=1, max_length=4000)
    # Clean question for paper/web search + related-topic analysis (the answered question
    # may carry an answer-style hint like a verbosity instruction). Falls back to question.
    search_question: str | None = None
    project_id: str | None = None
    provider: str | None = None
    model: str | None = None
    limit: int = Field(default=12, ge=1, le=25)
    paper_ids: list[str] = Field(default_factory=list)
    priority_paper_ids: list[str] = Field(default_factory=list)
    answer_context_mode: str = Field(default="kg", pattern="^(kg|pdf_if_fits)$")
    conversation_context: list[dict[str, Any]] = Field(default_factory=list)
    grey_source_ids: list[str] = Field(default_factory=list)
    include_project_grey: bool = False
    llm_overrides: dict[str, Any] = Field(default_factory=dict)
    force: bool = False
    max_related_topics: int = Field(default=5, ge=0, le=8)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    projects_path: str | None = None


class ResearchTreeRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    project_id: str | None = None
    depth: int = Field(default=3, ge=1, le=6)
    branches: int = Field(default=4, ge=2, le=8)
    max_nodes: int = Field(default=50, ge=5, le=100)
    provider: str | None = None
    model: str | None = None
    paper_ids: list[str] = Field(default_factory=list)
    grey_source_ids: list[str] = Field(default_factory=list)
    include_project_grey: bool = False
    auto_harvest: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    initial_nodes: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = None


class ResearchSessionUpsertRequest(BaseModel):
    """Authoritative overwrite of a persisted deep-research tree from the frontend.

    Sent on completion so the server copy carries the verification-enriched nodes the
    client computed (the streaming persistence only has the bare answers)."""
    project_id: str | None = None
    question: str = ""
    status: str = "done"
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelStartRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    variant_count: int = Field(default=3, ge=1, le=6)
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


class ParallelVariantCreateRequest(BaseModel):
    name: str = Field(default="Variante", max_length=400)
    approach: str = ""
    rationale: str = ""
    suggested_prompt: str = ""
    origin: str = "manual"
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelVariantUpdateRequest(BaseModel):
    name: str | None = None
    approach: str | None = None
    rationale: str | None = None
    suggested_prompt: str | None = None
    status: str | None = None
    position: int | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelEntryRequest(BaseModel):
    content: str = Field(min_length=1)
    request_feedback: bool = True
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


class ParallelSynthesizeRequest(BaseModel):
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


class ParallelGenerateRequest(BaseModel):
    variant_count: int = Field(default=3, ge=1, le=6)
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


class ParallelFollowupRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    variant_count: int = Field(default=1, ge=0, le=4)
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


class AgentHandoffRequest(BaseModel):
    """Compile a Parallel-Research variant into a computer-use task brief."""
    with_research_context: bool = True
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


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


class CompanionGuideRequest(BaseModel):
    """Question about a full screenshot; the model may return click-guidance steps."""
    question: str = Field(min_length=1, max_length=4000)
    image_base64: str = Field(min_length=1)
    history: list[CompanionTurn] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    # Quellen-Modus: ground the answer in local paper hits and/or a web search.
    use_papers: bool = False
    use_web: bool = False


class SelfDriveStartRequest(BaseModel):
    """Begin a native Selbst-Steuerung session (R7 skeleton)."""
    goal: str = Field(min_length=1, max_length=2000)
    monitor: int | None = None
    provider: str | None = None
    model: str | None = None


class SelfDriveStepRequest(BaseModel):
    """One planning round: current screenshot → next action."""
    session_id: str = Field(min_length=1, max_length=64)
    image_base64: str = Field(min_length=1)


class SelfDriveStopRequest(BaseModel):
    session_id: str = Field(min_length=1, max_length=64)


class ResearchTreeExportOptions(BaseModel):
    tikz_tree: bool = True
    charts: bool = True
    tables: bool = True
    comfyui_images: bool = False


class ResearchTreeExportRequest(BaseModel):
    """Export the Tiefenanalyse synthesis to a LaTeX PDF / .tex / .zip.

    The tree ``nodes`` and aggregated ``sources`` are sent straight from the frontend
    state — no DB round-trip — and ``document`` is the synthesis Markdown.
    """
    root_question: str = Field(min_length=1, max_length=2000)
    document: str = Field(min_length=1)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    format: str = Field(default="pdf", pattern="^(pdf|zip|tex)$")
    options: ResearchTreeExportOptions = Field(default_factory=ResearchTreeExportOptions)
    provider: str | None = None
    model: str | None = None


class ResearchClarifyRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = None
    model: str | None = None


class GreySourcePayload(BaseModel):
    sources: list[dict[str, Any]]
    query: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class GreySourceFromUrlPayload(BaseModel):
    url: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class WorkspaceSessionPayload(BaseModel):
    payload: dict[str, Any]
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ExtractionParseRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    pdf_path: str | None = Field(default=None, max_length=1000)
    parser: str | None = Field(default=None, max_length=80)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class ExtractionRunRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    text: str | None = Field(default=None, max_length=2_000_000)
    pdf_path: str | None = Field(default=None, max_length=1000)
    parser: str | None = Field(default=None, max_length=80)
    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=256, le=131072)
    context_size: int | None = Field(default=None, ge=1024, le=262144)
    extraction_mode: str | None = Field(default="quality", max_length=80)
    context_policy: str | None = Field(default="auto", pattern="^(auto|whole|chunk)$")
    allow_context_fallback: bool = False
    link_concepts: bool = True
    save: bool = True
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class ExtractionBatchItem(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    pdf_path: str = Field(min_length=1, max_length=1000)


class ExtractionBatchRequest(BaseModel):
    items: list[ExtractionBatchItem]
    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=256, le=131072)
    context_size: int | None = Field(default=None, ge=1024, le=262144)
    extraction_mode: str | None = Field(default="quality", max_length=80)
    context_policy: str | None = Field(default="auto", pattern="^(auto|whole|chunk)$")
    allow_context_fallback: bool = False
    link_concepts: bool = True
    resume: bool = True
    job_id: str | None = Field(default=None, max_length=120)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class VocabularyEntryRequest(BaseModel):
    canonical_label: str = Field(min_length=1, max_length=240)
    aliases: list[str] = []
    openalx_id: str | None = Field(default=None, max_length=240)
    domain: str | None = Field(default=None, max_length=240)
    vocabulary_path: str = DEFAULT_VOCABULARY_PATH


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


@app.get("/projects")
def list_projects(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    projects_path: str | None = None,
) -> dict[str, Any]:
    projects = _load_projects(_projects_path(projects_path))
    with MetadataDB(metadata_db_path) as db:
        papers = {str(paper.get("id")): paper for paper in db.list_papers(limit=50000)}
    return {
        "projects": [_project_view(project_id, paper_ids, papers) for project_id, paper_ids in sorted(projects.items())]
    }


@app.post("/projects")
def create_project(payload: ProjectPayload, projects_path: str | None = None) -> dict[str, Any]:
    path = _projects_path(projects_path)
    projects = _load_projects(path)
    project_id = payload.name.strip()
    if _is_reserved_project_id(project_id):
        raise HTTPException(status_code=400, detail=f"Reserved project name: {project_id}")
    if project_id in projects:
        raise HTTPException(status_code=409, detail=f"Project already exists: {project_id}")
    projects[project_id] = _unique_strings(payload.paper_ids)
    _save_projects(projects, path)
    return {"project": _project_view(project_id, projects[project_id], {})}


@app.patch("/projects/{project_id}")
def patch_project(project_id: str, payload: ProjectPatch, projects_path: str | None = None) -> dict[str, Any]:
    path = _projects_path(projects_path)
    projects = _load_projects(path)
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    target_id = payload.name.strip() if payload.name else project_id
    if _is_reserved_project_id(target_id):
        raise HTTPException(status_code=400, detail=f"Reserved project name: {target_id}")
    if target_id != project_id and target_id in projects:
        raise HTTPException(status_code=409, detail=f"Project already exists: {target_id}")

    paper_ids = _unique_strings(payload.paper_ids) if payload.paper_ids is not None else projects[project_id]
    if target_id != project_id:
        projects.pop(project_id)
    projects[target_id] = paper_ids
    _save_projects(projects, path)
    return {"project": _project_view(target_id, paper_ids, {})}


@app.delete("/projects/{project_id}")
def delete_project(project_id: str, projects_path: str | None = None) -> dict[str, Any]:
    if _is_reserved_project_id(project_id):
        raise HTTPException(status_code=400, detail="Alle Papers is the global library mode and cannot be deleted.")
    path = _projects_path(projects_path)
    projects = _load_projects(path)
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    paper_ids = projects.pop(project_id)
    _save_projects(projects, path)
    return {"deleted": True, "project": _project_view(project_id, paper_ids, {})}


@app.post("/projects/{project_id}/papers")
def add_project_papers(
    project_id: str,
    payload: ProjectPaperPayload,
    projects_path: str | None = None,
) -> dict[str, Any]:
    path = _projects_path(projects_path)
    projects = _load_projects(path)
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    projects[project_id] = _unique_strings([*projects[project_id], *payload.paper_ids])
    _save_projects(projects, path)
    return {"project": _project_view(project_id, projects[project_id], {})}


@app.delete("/projects/{project_id}/papers/{paper_id:path}")
def remove_project_paper(
    project_id: str,
    paper_id: str,
    projects_path: str | None = None,
) -> dict[str, Any]:
    """Detach a paper from a project (the paper itself stays in the library)."""
    path = _projects_path(projects_path)
    projects = _load_projects(path)
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if paper_id not in projects[project_id]:
        raise HTTPException(status_code=404, detail=f"Paper not in project: {paper_id}")
    projects[project_id] = [pid for pid in projects[project_id] if pid != paper_id]
    _save_projects(projects, path)
    primaries = _load_primary_papers()
    if primaries.get(project_id) == paper_id:
        primaries.pop(project_id, None)
        _save_primary_papers(primaries)
    return {"project": _project_view(project_id, projects[project_id], {}), "removed": paper_id}


class PrimaryPaperPayload(BaseModel):
    paper_id: str | None = None


@app.put("/projects/{project_id}/primary-paper")
def set_project_primary_paper(project_id: str, payload: PrimaryPaperPayload) -> dict[str, Any]:
    """Mark (or clear) the project's main source. Answers will prioritize it."""
    mapping = _load_primary_papers()
    if payload.paper_id:
        mapping[project_id] = str(payload.paper_id)
    else:
        mapping.pop(project_id, None)
    _save_primary_papers(mapping)
    return {"project_id": project_id, "primary_paper_id": mapping.get(project_id)}


@app.get("/projects/{project_id}/dashboard")
def project_dashboard(
    project_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    projects_path: str | None = None,
) -> dict[str, Any]:
    projects = _load_projects(_projects_path(projects_path))
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")

    paper_ids = set(projects[project_id])
    health = build_health_report(metadata_db_path, graph_db_path, pdf_base_dir)
    with MetadataDB(metadata_db_path) as db:
        papers = [paper for paper in db.list_papers(limit=50000) if str(paper.get("id")) in paper_ids]
        extractions = [item for item in db.list_extraction_results(limit=50000) if str(item.get("paper_id")) in paper_ids]
        latest_jobs = db.list_batch_jobs(limit=5)
        review_items = [item for item in db.list_entity_review_queue(status="pending", limit=10000) if str(item.get("paper_id")) in paper_ids]

    successful_papers = {str(item.get("paper_id")) for item in extractions if item.get("extraction_status") == "success"}
    return {
        "project": _project_view(project_id, list(paper_ids), {str(paper.get("id")): paper for paper in papers}),
        "metrics": {
            "papers": len(papers),
            "pdfs": sum(1 for paper in papers if paper.get("has_full_text")),
            "extraction_coverage": _ratio(len(successful_papers), len(papers)),
            "pending_review": len(review_items),
            "embeddings": health.get("embeddings", {}).get("total", 0),
            "warnings": len(health.get("warnings") or []),
        },
        "health": health,
        "latest_jobs": latest_jobs,
    }


@app.get("/papers")
def list_papers(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    projects_path: str | None = None,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    query: str = "",
    project_id: str | None = None,
    has_full_text: bool | None = None,
    extraction_status: str | None = None,
    sort: str = "added_desc",
    limit: int = Query(default=50, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    projects = _load_projects(_projects_path(projects_path))
    selected_ids = set(projects.get(project_id, [])) if project_id else None
    memberships = _project_memberships(projects)

    with MetadataDB(metadata_db_path) as db:
        papers = db.list_papers(limit=50000)
        latest_by_paper = _latest_extraction_statuses(db)

    filtered = []
    for paper in papers:
        pid = str(paper.get("id") or "")
        if selected_ids is not None and pid not in selected_ids:
            continue
        if has_full_text is not None and bool(paper.get("has_full_text")) != has_full_text:
            continue
        latest_status = latest_by_paper.get(pid)
        if extraction_status and latest_status != extraction_status:
            continue
        if query and not _paper_matches_query(paper, query):
            continue
        filtered.append(
            {
                **_paper_list_view(paper, pdf_base_dir),
                "project_ids": sorted(memberships.get(pid, [])),
                "latest_extraction_status": latest_status,
            }
        )

    filtered.sort(key=_paper_sort_key(sort), reverse=sort.endswith("_desc"))
    page = filtered[offset : offset + limit]
    return {"items": page, "total": len(filtered), "limit": limit, "offset": offset}


def _paper_list_view(paper: dict[str, Any], pdf_base_dir: str = DEFAULT_PDF_BASE_DIR) -> dict[str, Any]:
    paper_id_value = _clean_display_text(paper.get("id") or paper.get("paper_id") or paper.get("source_id"))
    source_id = _clean_display_text(paper.get("source_id"))
    title = _clean_display_text(paper.get("title"))
    local_pdf_path = _paper_local_pdf_path(paper, pdf_base_dir)
    pdf_filename = Path(local_pdf_path).name if local_pdf_path else _paper_filename_from_value(paper.get("pdf_url"))
    display_title = title or _clean_pdf_title(pdf_filename) or paper_id_value or source_id or "Unbenanntes PDF"
    return {
        **paper,
        "display_title": display_title,
        "pdf_filename": pdf_filename or None,
        "pdf_path": local_pdf_path,
        "has_full_text": bool(local_pdf_path),
    }


def _paper_local_pdf_path(paper: dict[str, Any], pdf_base_dir: str) -> str | None:
    pdf_url = _clean_display_text(paper.get("pdf_url"))
    if pdf_url and not re.match(r"^[a-z][a-z0-9+.-]*://", pdf_url, flags=re.IGNORECASE):
        path = Path(str(pdf_url).replace("\\", "/"))
        try:
            if path.exists():
                return str(path)
        except OSError:
            pass
    paper_id_value = _clean_display_text(paper.get("id") or paper.get("paper_id") or paper.get("source_id"))
    title = _clean_display_text(paper.get("title"))
    return find_pdf_path(paper_id_value, title, pdf_base_dir) if paper_id_value else None


@app.get("/paper/meta")
def paper_meta(
    paper_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
) -> dict[str, Any]:
    """Return metadata for a cited paper that may have no local PDF, so the UI can show its
    abstract and a link to the original source for verification."""
    with MetadataDB(metadata_db_path) as db:
        paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
    doi = _clean_display_text(paper.get("doi"))
    landing = _clean_display_text(paper.get("landing_page_url"))
    pdf_url = _clean_display_text(paper.get("pdf_url"))
    remote_pdf = pdf_url if re.match(r"^https?://", pdf_url, flags=re.IGNORECASE) else ""
    external_url = landing or (f"https://doi.org/{doi}" if doi else "") or remote_pdf
    return {
        "paper_id": _clean_display_text(paper.get("id")) or paper_id,
        "title": _clean_display_text(paper.get("title")),
        "abstract": _clean_display_text(paper.get("abstract")),
        "doi": doi or None,
        "pdf_url": remote_pdf or None,
        "landing_page_url": landing or None,
        "has_local_pdf": _paper_local_pdf_path(paper, pdf_base_dir) is not None,
        "external_url": external_url or None,
    }


def _ingest_extract_background(
    paper_id: str,
    pdf_path: str,
    metadata_db_path: str,
    provider: str | None,
    model: str | None,
) -> None:
    """Run Phase-3 extraction for a freshly-ingested paper after the response is sent."""
    try:
        from parsing.parser_router import ParserRouter
        from query.auto_harvester import _extract_pdf_into_db

        parser_router = ParserRouter()
        with MetadataDB(metadata_db_path) as db:
            try:
                existing = db._execute(
                    "SELECT id FROM extraction_results WHERE paper_id = ? AND extraction_status = 'success' LIMIT 1",
                    [paper_id],
                ).fetchone()
            except Exception:
                existing = None
            if existing is None:
                _extract_pdf_into_db(
                    db, extraction_pipeline, parser_router, paper_id, pdf_path, provider, model
                )
    except Exception:
        pass


@app.post("/paper/ingest")
async def paper_ingest(request: PaperIngestRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """On-demand download + extract for a cited paper that has no local PDF yet.

    Lets a citation click resolve to a real, project-local PDF instead of the "no PDF" limbo:
    downloads the PDF (resolving an open-access URL by DOI if needed), attaches it to the
    project, and kicks off Phase-3 extraction in the background so the click stays fast. When no
    PDF is downloadable the response carries ``external_url`` so the UI can fall back to the
    web/grey-source view.
    """
    with MetadataDB(request.metadata_db_path) as db:
        paper = db.get_paper(request.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper not found: {request.paper_id}")

    storage = FileManager(request.pdf_base_dir)
    headers = {"User-Agent": "ScienceKG/ingest (local-development)"}
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=headers) as client:
        with MetadataDB(request.metadata_db_path) as db:
            result = await ingest_paper_record(
                paper, db, storage, client,
                provider=request.provider, model=request.model,
                extract=False,  # extraction is scheduled as a background task below
            )

    canonical_id = str(result.get("id") or request.paper_id)
    has_pdf = bool(result.get("has_local_pdf"))
    pdf_path = result.get("pdf_path")

    attached = False
    if has_pdf:
        attached = bool(_attach_papers_to_project(request.project_id, [canonical_id], request.projects_path))
        if pdf_path:
            background_tasks.add_task(
                _ingest_extract_background,
                canonical_id, str(pdf_path), request.metadata_db_path,
                request.provider, request.model,
            )

    # Pre-download metadata still holds the remote/landing URL for the grey fallback.
    doi = _clean_display_text(paper.get("doi"))
    landing = _clean_display_text(paper.get("landing_page_url"))
    remote_pdf_raw = _clean_display_text(paper.get("pdf_url"))
    remote_pdf = remote_pdf_raw if re.match(r"^https?://", remote_pdf_raw, flags=re.IGNORECASE) else ""
    external_url = landing or (f"https://doi.org/{doi}" if doi else "") or remote_pdf
    return {
        "paper_id": canonical_id,
        "title": result.get("title"),
        "has_local_pdf": has_pdf,
        "attached": attached,
        "external_url": external_url or None,
    }


def _clean_display_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return "" if text.lower() in {"", "none", "null", "nan", "undefined"} else text


def _paper_filename_from_value(value: Any) -> str:
    raw = _clean_display_text(value)
    if not raw:
        return ""
    without_query = re.split(r"[?#]", raw, maxsplit=1)[0] or raw
    return Path(without_query.replace("\\", "/")).name


def _clean_pdf_title(value: Any) -> str:
    filename = _paper_filename_from_value(value)
    if not filename:
        return ""
    return _clean_display_text(re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE).replace("_", " ").replace("-", " "))


_TITLE_LINE_BLOCKLIST = (
    "arxiv:", "doi:", "abstract", "introduction", "keywords", "page ",
    "©", "http://", "https://", "preprint", "submitted", "draft",
    "running head", "downloaded from", "issn", "isbn", "vol.", "volume",
)

_GENERIC_TITLE_STEMS = frozenset(
    {"file", "document", "upload", "pdf", "paper", "doc", "untitled", "new", "temp", "tmp", "download"}
)

# Below this ratio of word-like tokens (>= 3 letters) to whitespace-split tokens, parsed
# text is treated as broken/garbled extraction (e.g. font-encoding issues, scanned noise).
_GARBLED_TEXT_WORD_RATIO_THRESHOLD = 0.4
_GARBLED_TEXT_SAMPLE_LEN = 2000


def _text_looks_garbled(text: str) -> bool:
    sample = text[:_GARBLED_TEXT_SAMPLE_LEN].strip()
    tokens = sample.split()
    if len(tokens) < 20:
        return False
    word_like = re.findall(r"[a-zA-Z]{3,}", sample)
    return (len(word_like) / len(tokens)) < _GARBLED_TEXT_WORD_RATIO_THRESHOLD


def _maybe_fix_garbled_pdf_title(*, paper_id: str, text: str, pdf_path: Path, metadata_db_path: str) -> None:
    """When extraction text looks garbled and the stored title is generic/filename-derived,
    overwrite it with a title inferred from the PDF itself (metadata or heading line) — that
    inference is far more likely correct than anything derived from broken extracted text."""
    if not _text_looks_garbled(text):
        return
    with MetadataDB(metadata_db_path) as db:
        paper = db.get_paper(paper_id)
        if not paper:
            return
        current_title = (paper.get("title") or "").strip()
        current_stem = current_title.lower().strip()
        filename_stem = pdf_path.stem.lower().strip()
        looks_generic = current_stem in _GENERIC_TITLE_STEMS or current_stem == filename_stem
        if not looks_generic:
            return
        try:
            content = pdf_path.read_bytes()
        except OSError:
            return
        inferred_title = _infer_pdf_title_from_bytes(content).strip()
        if inferred_title and inferred_title != current_title:
            db.update_paper_title(paper_id, inferred_title[:240])


def _looks_like_title_line(line: str) -> bool:
    text = _clean_display_text(line)
    if not (15 <= len(text) <= 200):
        return False
    if not re.search(r"[a-zA-Z]{3,}", text):
        return False
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in _TITLE_LINE_BLOCKLIST):
        return False
    return True


_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _infer_title_from_html(html: str) -> str:
    """Cheap regex-based <title>/<h1> heuristic — no LLM call, keep URL ingest fast."""
    for pattern in (_HTML_TITLE_RE, _HTML_H1_RE):
        match = pattern.search(html)
        if not match:
            continue
        candidate = _clean_display_text(_HTML_TAG_RE.sub(" ", match.group(1)))
        if candidate:
            return candidate
    return ""


def _infer_pdf_title_from_bytes(content: bytes) -> str:
    """Best-effort title extraction from PDF metadata or the first page's heading."""
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception:
        return ""

    try:
        meta_title = _clean_display_text(getattr(reader.metadata, "title", None) if reader.metadata else "")
    except Exception:
        meta_title = ""
    if meta_title and meta_title.lower() not in {"untitled", "untitled document"} and _looks_like_title_line(meta_title):
        return meta_title

    try:
        first_page_text = reader.pages[0].extract_text() or ""
    except Exception:
        first_page_text = ""
    for raw_line in first_page_text.splitlines():
        line = _clean_display_text(raw_line)
        if _looks_like_title_line(line):
            return line
    return ""


@app.post("/papers/upload")
async def upload_paper_pdf(
    request: Request,
    paper_id: str | None = None,
    title: str | None = None,
    source: str = "upload",
    project_id: str | None = None,
    projects_path: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
) -> dict[str, Any]:
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Upload body is empty.")

    filename = request.headers.get("x-filename") or title or paper_id or "uploaded-paper.pdf"
    stem = Path(filename).stem.lower().strip()
    inferred_id = paper_id or (f"upload-{__import__('uuid').uuid4().hex[:8]}" if stem in _GENERIC_TITLE_STEMS else Path(filename).stem)
    resolved_title = title or _infer_pdf_title_from_bytes(content) or Path(filename).stem
    storage = FileManager(pdf_base_dir)
    saved_path = storage.save_pdf(
        inferred_id,
        content,
        display_name=resolved_title,
        source=source,
    )
    with MetadataDB(metadata_db_path) as db:
        canonical_id = db.ensure_paper_record(
            inferred_id,
            title=resolved_title,
            pdf_path=str(saved_path),
            source=source,
            source_id=inferred_id,
        )
        paper = db.get_paper(canonical_id)
    project_paper_ids = _attach_papers_to_project(project_id, [canonical_id], projects_path)
    return {
        "paper": paper,
        "pdf_path": str(saved_path),
        "project_id": project_id,
        "attached": bool(project_paper_ids),
    }


@app.delete("/papers/{paper_id:path}")
def delete_paper(
    paper_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    projects_path: str | None = None,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        paper = db.get_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
        pdf_url = paper.get("pdf_url") or paper.get("pdf_path")
        deleted = db.delete_paper(paper_id)
    file_deleted = False
    if deleted and pdf_url:
        storage = FileManager(pdf_base_dir)
        file_deleted = storage.delete_by_path(str(pdf_url))
    if deleted:
        all_projects = _load_projects(_projects_path(projects_path))
        changed = False
        for pid, members in all_projects.items():
            if paper_id in members:
                all_projects[pid] = [m for m in members if m != paper_id]
                changed = True
        if changed:
            _save_projects(all_projects, _projects_path(projects_path))
        primary = _load_primary_papers()
        if any(v == paper_id for v in primary.values()):
            updated = {k: v for k, v in primary.items() if v != paper_id}
            _save_primary_papers(updated)
    return {"deleted": deleted, "file_deleted": file_deleted, "id": paper_id}


@app.post("/harvest/search")
async def harvest_search(request: HarvestSearchRequest) -> dict[str, Any]:
    results, warnings = await _run_harvest_search(request.query, request.sources, request.max_results)
    return {"query": request.query, "results": results, "warnings": warnings}


async def _fetch_one_pdf(
    paper: dict[str, Any],
    client: httpx.AsyncClient,
    storage: FileManager,
    resolver: BenchmarkPdfResolver,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Download (or attempt to locate) the PDF for a single paper. Returns a result dict."""
    async with semaphore:
        canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
        title = str(paper.get("title") or paper.get("id") or canonical_id)
        doi = paper.get("doi")
        saved_path: str | None = None
        detail: str | None = None

        direct_url = paper.get("pdf_url")
        if direct_url and not await asyncio.to_thread(is_safe_public_url, str(direct_url)):
            detail = "Direkt-Link verweist nicht auf eine öffentliche Adresse"
            direct_url = None
        if direct_url:
            try:
                response = await client.get(str(direct_url))
                response.raise_for_status()
                if _looks_like_pdf(response.content, response.headers.get("content-type", "")):
                    saved_path = str(storage.save_pdf(
                        canonical_id,
                        response.content,
                        version=int(paper.get("version") or 1),
                        display_name=str(paper.get("title") or canonical_id),
                        source=str(paper.get("source") or "paper"),
                    ))
                else:
                    detail = "Direkt-Link lieferte kein PDF"
            except Exception as exc:  # noqa: BLE001
                detail = f"Direkt-Link fehlgeschlagen: {exc}"

        if not saved_path and (doi or title):
            try:
                resolution = await asyncio.to_thread(
                    resolver.resolve,
                    paper_id=canonical_id,
                    title=title,
                    doi=str(doi) if doi else None,
                    download_missing=True,
                )
                if resolution.pdf_path:
                    saved_path = resolution.pdf_path
                elif resolution.warnings:
                    detail = "; ".join(resolution.warnings[:2])
            except Exception as exc:  # noqa: BLE001
                detail = f"OA-Suche fehlgeschlagen: {exc}"

        return {
            "canonical_id": canonical_id,
            "title": title,
            "doi": doi,
            "saved_path": saved_path,
            "detail": detail,
        }


@app.post("/harvest/download")
async def harvest_download(request: HarvestDownloadRequest) -> dict[str, Any]:
    storage = FileManager(request.pdf_base_dir)
    resolver = BenchmarkPdfResolver(
        request.pdf_base_dir,
        contact_email=_unpaywall_email() or _crossref_mailto(),
    )
    download_headers = {"User-Agent": "ScienceKG/harvest (local-development)"}

    # Insert all paper metadata first (fast, no external calls).
    attached_ids: list[str] = []
    with MetadataDB(request.metadata_db_path) as db:
        for paper in request.papers:
            db.insert_paper(paper)
            canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
            attached_ids.append(canonical_id)

    # If PDF download not requested, return immediately.
    if not request.download_pdfs:
        results = [
            {
                "paper_id": str(p.get("id") or f"{p.get('source')}:{p.get('source_id')}"),
                "title": str(p.get("title") or p.get("id") or ""),
                "status": "inserted",
            }
            for p in request.papers
        ]
        project_paper_ids = _attach_papers_to_project(request.project_id, attached_ids, request.projects_path)
        return {
            "inserted": len(results),
            "downloaded": 0,
            "failed_downloads": [],
            "results": results,
            "project_id": request.project_id,
            "attached": bool(project_paper_ids),
        }

    # Download PDFs concurrently (semaphore limits parallel external requests).
    semaphore = asyncio.Semaphore(8)
    async with httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=download_headers) as client:
        fetch_results = await asyncio.gather(
            *[_fetch_one_pdf(paper, client, storage, resolver, semaphore) for paper in request.papers],
            return_exceptions=True,
        )

    # Persist PDF paths and assemble response.
    inserted = len(request.papers)
    downloaded = 0
    failed_downloads: list[str] = []
    results: list[dict[str, Any]] = []

    with MetadataDB(request.metadata_db_path) as db:
        for fetch_result in fetch_results:
            if isinstance(fetch_result, BaseException):
                results.append({"paper_id": "unknown", "title": "unknown", "status": "failed", "detail": str(fetch_result)})
                failed_downloads.append(str(fetch_result))
                continue
            canonical_id = fetch_result["canonical_id"]
            title = fetch_result["title"]
            doi = fetch_result["doi"]
            saved_path = fetch_result["saved_path"]
            detail = fetch_result["detail"]
            if saved_path:
                db.update_paper_metadata_if_missing(canonical_id, pdf_path=str(saved_path))
                downloaded += 1
                results.append({"paper_id": canonical_id, "title": title, "status": "downloaded"})
            else:
                landing_url = f"https://doi.org/{str(doi).replace('https://doi.org/', '').strip()}" if doi else None
                status = "failed" if detail and "fehlgeschlagen" in detail else "no_pdf"
                if status == "failed":
                    failed_downloads.append(f"{title}: {detail}")
                results.append({
                    "paper_id": canonical_id,
                    "title": title,
                    "status": status,
                    "detail": detail,
                    "landing_url": landing_url,
                })

    project_paper_ids = _attach_papers_to_project(request.project_id, attached_ids, request.projects_path)
    return {
        "inserted": inserted,
        "downloaded": downloaded,
        "failed_downloads": failed_downloads,
        "results": results,
        "project_id": request.project_id,
        "attached": bool(project_paper_ids),
    }


async def _match_references_to_crossref(
    reference_strings: list[str], max_references: int
) -> list[dict[str, Any]]:
    """Match free-text reference strings to Crossref works (best effort, sequential)."""
    client = CrossrefClient(CrossrefConfig(mailto=_crossref_mailto()))
    matched: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    try:
        for reference in reference_strings[:max_references]:
            try:
                work = await client.match_reference(reference)
            except Exception:
                work = None
            if not work:
                continue
            normalized = _normalize_crossref_work(work)
            doi_key = str(normalized.get("doi") or normalized.get("source_id") or "").lower()
            if not doi_key or doi_key in seen_dois:
                continue
            seen_dois.add(doi_key)
            normalized["reference_string"] = reference
            matched.append(normalized)
    finally:
        await client.close()
    return matched


@app.post("/papers/references/extract")
async def extract_paper_references(request: ReferenceExtractRequest) -> dict[str, Any]:
    """Parse an uploaded/known PDF, detect its cited references and match them to
    Crossref so the user can choose which referenced papers to download.

    Does NOT download anything; the frontend sends the chosen subset to
    /harvest/download.
    """
    pdf_path = _resolve_extraction_pdf_path(
        request.paper_id, request.pdf_path, request.metadata_db_path, request.pdf_base_dir
    )
    parsed = _parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
    section = extract_reference_section(parsed.text)
    reference_strings = split_reference_entries(section)
    references = await _match_references_to_crossref(reference_strings, request.max_references)
    return {
        "paper_id": request.paper_id,
        "references_detected": len(reference_strings),
        "references_matched": len(references),
        "references": references,
    }


def _existing_library_keys(metadata_db_path: str) -> set[str]:
    keys: set[str] = set()
    try:
        with MetadataDB(metadata_db_path) as db:
            for paper in db.list_papers(limit=50000):
                if paper.get("doi"):
                    keys.add(str(paper["doi"]).lower())
                if paper.get("id"):
                    keys.add(str(paper["id"]).lower())
                if paper.get("title"):
                    keys.add(re.sub(r"\s+", " ", str(paper["title"]).lower()).strip())
    except Exception:
        return keys
    return keys


async def _run_discovery_queries(
    analysis: dict[str, Any], sources: list[str], max_per_query: int, metadata_db_path: str
) -> list[dict[str, Any]]:
    """Run each suggested query through the harvest sources and aggregate novel papers."""
    existing = _existing_library_keys(metadata_db_path)
    aggregated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for entry in analysis.get("queries", []):
        query = entry.get("query")
        if not query:
            continue
        results, _ = await _run_harvest_search(query, sources, max_per_query)
        for paper in results:
            doi_key = str(paper.get("doi") or "").lower()
            id_key = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}").lower()
            title_key = re.sub(r"\s+", " ", str(paper.get("title") or "").lower()).strip()
            if doi_key and doi_key in existing:
                continue
            if title_key and title_key in existing:
                continue
            dedupe_key = doi_key or id_key
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            paper["discovery_reason"] = entry.get("reason") or ""
            paper["matched_query"] = query
            aggregated.append(paper)
    return aggregated


@app.post("/discovery/from-topic")
async def discovery_from_topic(request: DiscoveryTopicRequest) -> dict[str, Any]:
    """AI suggests topic-near papers to download for more context (suggest only)."""
    analysis = await asyncio.to_thread(analyze_topic, llm_router, request.topic, request.provider)
    candidates = await _run_discovery_queries(
        analysis, request.sources, request.max_per_query, request.metadata_db_path
    )
    return {"analysis": analysis, "candidates": candidates}


@app.post("/discovery/from-paper")
async def discovery_from_paper(request: DiscoveryPaperRequest) -> dict[str, Any]:
    """AI analyzes an uploaded paper (topic + methods) and suggests related papers."""
    pdf_path = _resolve_extraction_pdf_path(
        request.paper_id, request.pdf_path, request.metadata_db_path, request.pdf_base_dir
    )
    parsed = _parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
    analysis = await asyncio.to_thread(analyze_paper, llm_router, parsed.text, request.provider)
    candidates = await _run_discovery_queries(
        analysis, request.sources, request.max_per_query, request.metadata_db_path
    )
    return {"analysis": analysis, "candidates": candidates}


@app.post("/research/deep")
async def research_deep(request: DeepResearchRequest) -> dict[str, Any]:
    """Run a guarded web deep-research pass. Returns findings for review; nothing is
    saved until the user confirms via POST /projects/{id}/grey-sources.

    Web content is sanitized and treated as untrusted data (prompt-injection hardened).
    Results are grey sources and are never written to the knowledge graph.
    """
    return await run_deep_research(
        llm_router,
        request.question,
        provider=request.provider,
        search_provider=request.search_provider,
        max_queries=request.max_queries,
        results_per_query=request.results_per_query,
        max_sources=request.max_sources,
    )


@app.post("/query/auto-answer")
async def query_auto_answer(request: AutoAnswerRequest) -> StreamingResponse:
    """Answer a workspace question, streaming progress while auto-harvesting.

    When the first grounded answer cannot be supported locally (or ``force`` is set),
    an LLM derives related topics and the system harvests papers (downloaded + Phase-3
    extracted) and grey web sources for the question and those topics, then re-answers.

    Each step is sent as ``data: <json>\\n\\n`` (SSE). Event ``status`` is one of
    ``answer | planning | harvesting | reanswering | harvest_error | done``.
    """
    overrides = phase4_main._sanitized_llm_overrides(request.llm_overrides)

    async def stream() -> AsyncIterator[str]:
        try:
            async for event in auto_research_answer(
                question=request.question,
                llm_router=llm_router,
                search_question=request.search_question,
                project_id=request.project_id,
                provider=request.provider,
                model=request.model,
                overrides=overrides,
                conversation_context=request.conversation_context or None,
                paper_ids=request.paper_ids or None,
                priority_paper_ids=request.priority_paper_ids or None,
                answer_context_mode=request.answer_context_mode,
                limit=request.limit,
                grey_source_ids=request.grey_source_ids or None,
                include_project_grey=request.include_project_grey,
                force=request.force,
                max_related_topics=request.max_related_topics,
                metadata_db_path=request.metadata_db_path,
                pdf_base_dir=request.pdf_base_dir,
                projects_path=str(_projects_path(request.projects_path)),
            ):
                yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
        except Exception as exc:  # noqa: BLE001 - surface as a terminal SSE event
            yield f"data: {json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


def _parse_sse_node(event: str) -> dict[str, Any] | None:
    """Extract the node dict from one ``data: <json>\\n\\n`` SSE message."""
    line = event.strip()
    if not line.startswith("data:"):
        return None
    try:
        node = json.loads(line[len("data:"):].strip())
    except (ValueError, TypeError):
        return None
    return node if isinstance(node, dict) and node.get("id") else None


@app.post("/research/tree")
async def research_tree(request: ResearchTreeRequest) -> StreamingResponse:
    """Stream a Research Tree via Server-Sent Events, persisting it server-side.

    Each node is sent as ``data: <json>\\n\\n`` when it completes.
    Node payload: {id, parent_id, question, depth, status, answer, child_count?}.

    The tree is upserted into ``research_sessions`` *as it streams* (throttled, plus
    immediately on important nodes), so a reload mid-run — or a closed browser — never
    loses the progress. The frontend later overwrites with a verification-enriched copy
    via ``PUT /research/session/{id}``.
    """
    runner = ResearchTreeRunner(llm_router)
    session_id = request.session_id or uuid.uuid4().hex
    db_path = request.metadata_db_path

    async def persist_stream() -> AsyncIterator[str]:
        nodes_by_id: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for seed in request.initial_nodes or []:
            nid = str(seed.get("id") or "")
            if nid and nid not in nodes_by_id:
                order.append(nid)
            if nid:
                nodes_by_id[nid] = seed

        def save(status: str) -> None:
            try:
                with MetadataDB(db_path) as db:
                    db.upsert_research_session(
                        session_id, request.project_id, request.question, status,
                        [nodes_by_id[i] for i in order],
                    )
            except Exception:
                pass  # persistence is best-effort; never break the live stream

        save("running")  # show the session immediately, even before the first node
        last_save = time.monotonic()
        try:
            async for event in runner.stream_events(
                question=request.question,
                depth=request.depth,
                branches=request.branches,
                provider=request.provider,
                model=request.model,
                paper_ids=request.paper_ids or None,
                project_id=request.project_id,
                grey_source_ids=request.grey_source_ids or None,
                include_project_grey=request.include_project_grey,
                auto_harvest=request.auto_harvest,
                metadata_db_path=request.metadata_db_path,
                pdf_base_dir=request.pdf_base_dir,
                max_nodes=request.max_nodes,
                initial_nodes=request.initial_nodes,
            ):
                yield event
                node = _parse_sse_node(event)
                if node is not None:
                    nid = str(node["id"])
                    if nid not in nodes_by_id:
                        order.append(nid)
                    nodes_by_id[nid] = node
                    now = time.monotonic()
                    important = node.get("status") in ("done", "synthesis", "llm_error")
                    if important or (now - last_save) > 1.5:
                        last_save = now
                        await asyncio.to_thread(save, "running")
        finally:
            await asyncio.to_thread(save, "done")

    return StreamingResponse(
        persist_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Research-Session-Id": session_id,
        },
    )


@app.get("/research/sessions/{project_id}")
def list_research_sessions(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """List persisted deep-research sessions for a project (counts only, no payload)."""
    with MetadataDB(metadata_db_path) as db:
        return {"sessions": db.list_research_sessions(project_id)}


@app.get("/research/session/{session_id}")
def get_research_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Fetch the full node tree of one persisted deep-research session."""
    with MetadataDB(metadata_db_path) as db:
        session = db.get_research_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Research session not found")
    return {"session": session}


@app.put("/research/session/{session_id}")
def upsert_research_session(
    session_id: str, request: ResearchSessionUpsertRequest
) -> dict[str, Any]:
    """Authoritative overwrite of a session (verification-enriched copy from the client)."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.upsert_research_session(
            session_id, request.project_id, request.question, request.status, request.nodes
        )
    return {"session": session}


@app.delete("/research/session/{session_id}")
def delete_research_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_research_session(session_id)
    return {"deleted": deleted}


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
    provider = str(_load_agent_bridge_config().get("vlm_provider") or "").strip()
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
    config_url = str(_load_agent_bridge_config().get("url") or "").strip()
    parsed = urlparse(config_url)
    if parsed.scheme and parsed.hostname:
        return f"{parsed.scheme}://{parsed.netloc}", True
    return "", True


@app.post("/projects/{project_id}/parallel")
async def create_parallel_session(project_id: str, request: ParallelStartRequest) -> dict[str, Any]:
    """Start a Parallel-Research session: persist it, generate the grounded overview
    (task explanation + how-to) and the initial AI variants (the methods to try)."""
    retriever = _parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_filter = _parallel_project_filter(project_id)
    # Variants (the methods to try) are the core deliverable; generate them first. The overview
    # (task explanation + how-to) is best-effort and must never block or break the variants.
    # Run sequentially — both share the same retrieval/DuckDB state.
    variants = await asyncio.to_thread(
        parallel_research.propose_variants,
        retriever,
        llm_router,
        request.question,
        n=request.variant_count,
        paper_ids=request.paper_ids or None,
        provider=request.provider,
        model=request.model,
    )
    overview: dict[str, Any] | None = None
    try:
        overview = await asyncio.to_thread(
            parallel_research.propose_overview,
            retriever,
            llm_router,
            request.question,
            paper_ids=request.paper_ids or None,
            project_id=project_filter,
            provider=request.provider,
            model=request.model,
            metadata_db_path=request.metadata_db_path,
        )
    except Exception:
        overview = None
    with MetadataDB(request.metadata_db_path) as db:
        session = db.create_parallel_session(project_id, request.question)
        if overview is not None:
            db.update_parallel_session(
                session["id"],
                overview_markdown=str(overview.get("answer") or ""),
                overview_payload=overview,
            )
        for variant in variants:
            db.add_parallel_variant(
                session["id"],
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
            )
        session = db.get_parallel_session(session["id"])
    return {"session": session}


@app.get("/projects/{project_id}/parallel")
def list_parallel_sessions(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"sessions": db.list_parallel_sessions(project_id)}


@app.get("/parallel/{session_id}")
def get_parallel_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    return {"session": session}


@app.delete("/parallel/{session_id}")
def delete_parallel_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_session(session_id)
    return {"deleted": deleted}


@app.post("/parallel/{session_id}/generate")
async def generate_parallel_variants(session_id: str, request: ParallelGenerateRequest) -> dict[str, Any]:
    """Regenerate / add more AI variants for an existing session."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    retriever = _parallel_retriever(request.metadata_db_path, request.graph_db_path)
    variants = await asyncio.to_thread(
        parallel_research.propose_variants,
        retriever,
        llm_router,
        str(session.get("question") or ""),
        n=request.variant_count,
        paper_ids=request.paper_ids or None,
        provider=request.provider,
        model=request.model,
    )
    with MetadataDB(request.metadata_db_path) as db:
        for variant in variants:
            db.add_parallel_variant(
                session_id,
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
            )
        session = db.get_parallel_session(session_id)
    return {"session": session}


@app.post("/parallel/{session_id}/variants")
def add_parallel_variant(session_id: str, request: ParallelVariantCreateRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        if db.get_parallel_session(session_id) is None:
            raise HTTPException(status_code=404, detail="Parallel session not found")
        variant = db.add_parallel_variant(
            session_id,
            name=request.name,
            approach=request.approach,
            rationale=request.rationale,
            suggested_prompt=request.suggested_prompt,
            origin=request.origin or "manual",
            status="vorgeschlagen",
        )
    return {"variant": variant}


@app.patch("/parallel/variants/{variant_id}")
def update_parallel_variant(variant_id: str, request: ParallelVariantUpdateRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.update_parallel_variant(
            variant_id,
            name=request.name,
            approach=request.approach,
            rationale=request.rationale,
            suggested_prompt=request.suggested_prompt,
            status=request.status,
            position=request.position,
        )
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    return {"variant": variant}


@app.delete("/parallel/variants/{variant_id}")
def delete_parallel_variant(
    variant_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_variant(variant_id)
    return {"deleted": deleted}


@app.post("/parallel/variants/{variant_id}/entries")
async def add_parallel_entry(variant_id: str, request: ParallelEntryRequest) -> dict[str, Any]:
    """Submit a result for a variant; optionally returns an immediate grounded assessment."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        session_id = str(variant.get("session_id"))
        session = db.get_parallel_session(session_id)
        user_entry = db.add_parallel_entry(variant_id, session_id, "user", request.content)
        db.update_parallel_variant(variant_id, status="ergebnis")

    feedback_entry: dict[str, Any] | None = None
    if request.request_feedback:
        retriever = _parallel_retriever(request.metadata_db_path, request.graph_db_path)
        project_id = _parallel_project_filter(session.get("project_id") if session else None)
        answer = await asyncio.to_thread(
            parallel_research.feedback_for_entry,
            retriever,
            llm_router,
            question=str(session.get("question") if session else ""),
            variant=variant,
            user_result=request.content,
            paper_ids=request.paper_ids or None,
            project_id=project_id,
            provider=request.provider,
            model=request.model,
            metadata_db_path=request.metadata_db_path,
        )
        with MetadataDB(request.metadata_db_path) as db:
            feedback_entry = db.add_parallel_entry(
                variant_id, session_id, "assistant",
                str(answer.get("answer") or ""), answer_payload=answer,
            )

    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    return {"session": session, "user_entry": user_entry, "feedback_entry": feedback_entry}


@app.delete("/parallel/entries/{entry_id}")
def delete_parallel_entry(
    entry_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_entry(entry_id)
    return {"deleted": deleted}


@app.post("/parallel/{session_id}/synthesize")
async def synthesize_parallel_session(session_id: str, request: ParallelSynthesizeRequest) -> dict[str, Any]:
    """Cross-variant analysis → ranking + reshaped final answer, persisted on the session."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    retriever = _parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = _parallel_project_filter(session.get("project_id"))
    answer = await asyncio.to_thread(
        parallel_research.synthesize,
        retriever,
        llm_router,
        question=str(session.get("question") or ""),
        variants=session.get("variants", []),
        paper_ids=request.paper_ids or None,
        project_id=project_id,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
    )
    with MetadataDB(request.metadata_db_path) as db:
        session = db.update_parallel_session(
            session_id,
            status="synthesized",
            synthesis_markdown=str(answer.get("answer") or ""),
            synthesis_payload=answer,
        )
    return {"session": session, "answer": answer}


@app.post("/parallel/{session_id}/ask")
async def ask_parallel_followup(session_id: str, request: ParallelFollowupRequest) -> dict[str, Any]:
    """Ask a follow-up while a parallel session is open: keep weiterfragen in the same session.

    Produces a grounded chat answer (shown threaded under the overview) AND a structured
    variant (a new "Vorschlag" appended to the Ergebnisse) — never a new session."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    retriever = _parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = _parallel_project_filter(session.get("project_id"))
    original_question = str(session.get("question") or "")

    answer = await asyncio.to_thread(
        parallel_research.followup_answer,
        retriever,
        llm_router,
        question=request.question,
        original_question=original_question,
        paper_ids=request.paper_ids or None,
        project_id=project_id,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
    )

    variants: list[dict[str, str]] = []
    if request.variant_count > 0:
        composed = (
            f"{original_question}\n\nVertiefende Folgefrage: {request.question}"
            if original_question
            else request.question
        )
        variants = await asyncio.to_thread(
            parallel_research.propose_variants,
            retriever,
            llm_router,
            composed,
            n=request.variant_count,
            paper_ids=request.paper_ids or None,
            provider=request.provider,
            model=request.model,
        )

    with MetadataDB(request.metadata_db_path) as db:
        db.add_parallel_followup(session_id, request.question, answer_payload=answer)
        for variant in variants:
            db.add_parallel_variant(
                session_id,
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
            )
        session = db.get_parallel_session(session_id)
    return {"session": session, "answer": answer}


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


@app.post("/parallel/variants/{variant_id}/handoff")
async def parallel_variant_handoff(variant_id: str, request: AgentHandoffRequest) -> dict[str, Any]:
    """Compile a variant into a computer-use task brief for an external desktop agent.

    Returns ``{brief, text, bridge}``. ``text`` is the copy-/POST-ready instruction —
    Kanal A: paste into UI-TARS-Desktop; Kanal B: POST to /agent/dispatch. Pure text-out;
    PaperKG never drives the machine here."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        session = db.get_parallel_session(str(variant.get("session_id")))
    question = str(session.get("question") if session else "")
    retriever = None
    if request.with_research_context:
        try:
            retriever = _parallel_retriever(request.metadata_db_path, request.graph_db_path)
        except Exception:
            retriever = None
    brief = await asyncio.to_thread(
        agent_handoff.build_task_brief,
        variant,
        question=question,
        retriever=retriever,
        llm_router=llm_router,
        paper_ids=request.paper_ids or None,
        provider=request.provider,
        model=request.model,
    )
    text = agent_handoff.render_task_brief_text(brief)
    bridge = _load_agent_bridge_config()
    return {
        "brief": brief,
        "text": text,
        "bridge": {
            "enabled": bool(bridge.get("enabled")),
            "type": str(bridge.get("type") or "ui_tars_desktop"),
        },
    }


@app.get("/agent/config")
def get_agent_config() -> dict[str, Any]:
    """Whether the desktop-agent bridge (Kanal B) is configured. Never exposes secrets."""
    bridge = _load_agent_bridge_config()
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


@app.post("/agent/dispatch")
async def dispatch_agent(request: AgentDispatchRequest) -> StreamingResponse:
    """Forward a task brief to the local desktop-agent bridge and stream progress (SSE).

    Best-effort: if the bridge is disabled or unreachable a single terminal ``error``
    event is emitted — nothing crashes, and PaperKG itself never controls the machine.
    On completion the run transcript is appended to the variant as an assistant entry."""
    bridge = _load_agent_bridge_config()
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
            async with httpx.AsyncClient(timeout=timeout) as client:
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


@app.post("/agent/cancel")
async def cancel_agent(request: AgentCancelRequest) -> dict[str, Any]:
    """Gracefully abort an in-flight Selbst-Steuerung run (the bridge's own
    ``AbortSignal``). Tauri hard-killing the bridge sidecar is the guaranteed
    fallback if a single step doesn't honor this in time."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"ok": False, "error": "bridge url rejected or not configured"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{origin}/cancel", json={"runId": request.run_id})
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"ok": False, "error": str(exc)}


@app.post("/agent/observe/start")
async def observe_agent_start(request: AgentObserveStartRequest) -> StreamingResponse:
    """Start an Assistent (helper) session: relay the bridge's periodic screen
    observations as SSE. Screenshots never leave the bridge process — only short
    text descriptions are forwarded, and nothing is persisted here."""
    bridge = _load_agent_bridge_config()
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
            async with httpx.AsyncClient(timeout=timeout) as client:
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


@app.post("/agent/observe/ask")
async def observe_agent_ask(request: AgentObserveAskRequest) -> dict[str, Any]:
    """Ask a live question against an active Assistent observation session,
    answered against a fresh screenshot plus the session's rolling context."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"answer": "", "error": "bridge url rejected or not configured"}
    timeout = float(_load_agent_bridge_config().get("timeout_seconds") or 600)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{origin}/observe/ask",
                json={"sessionId": request.session_id, "question": request.question},
            )
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"answer": "", "error": str(exc)}


@app.post("/agent/observe/point")
async def observe_agent_point(request: AgentObservePointRequest) -> dict[str, Any]:
    """Locate a UI element for the Assistent's pointer overlay (the "zeig mir"-Funktion):
    relays the bridge's grounding call and returns real screen coordinates. Never
    dispatches input — the overlay only draws a highlight at the returned point."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"error": "bridge url rejected or not configured"}
    timeout = float(_load_agent_bridge_config().get("timeout_seconds") or 600)
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            resp = await client.post(
                f"{origin}/observe/point",
                json={"sessionId": request.session_id, "question": request.question},
            )
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"error": str(exc)}


@app.post("/agent/observe/stop")
async def observe_agent_stop(request: AgentObserveStopRequest) -> dict[str, Any]:
    """Stop an active Assistent observation session."""
    origin, from_config = _resolve_bridge_origin(request.bridge_base)
    if not origin or not _validate_bridge_url(origin, from_config=from_config):
        return {"ok": False, "error": "bridge url rejected or not configured"}
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(f"{origin}/observe/stop", json={"sessionId": request.session_id})
            return resp.json()
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"ok": False, "error": str(exc)}


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


@app.post("/companion/guide")
async def companion_guide(request: CompanionGuideRequest) -> dict[str, Any]:
    """Desktop-Companion: answer a question about the screenshot and optionally return
    ordered click-guidance steps in **original screenshot pixels** (= physical monitor
    pixels for full captures). The companion only points — it never drives input."""
    params = _companion_llm_params(request.provider, request.model)
    cfg = _load_companion_config()
    if cfg.get("debug_capture"):
        params["debug_dir"] = str(cfg.get("debug_dir") or "data/companion_debug")
    history = [turn.model_dump() for turn in request.history]
    context_blocks, sources = await _companion_context(
        request.question, request.use_papers, request.use_web
    )
    try:
        result = await asyncio.to_thread(
            screen_companion.guide,
            llm_router,
            request.question,
            request.image_base64,
            history=history,
            context_blocks=context_blocks or None,
            **params,
        )
        result["sources"] = sources
        return result
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"answer": "", "found": False, "steps": [], "sources": [], "error": str(exc)}


@app.post("/companion/ask")
async def companion_ask(request: CompanionAskRequest) -> dict[str, Any]:
    """Desktop-Companion: free-form screen Q&A without pointing — used for the
    Bereich-erklären snips (``region=true``) and text-only follow-up questions."""
    params = _companion_llm_params(request.provider, request.model)
    history = [turn.model_dump() for turn in request.history]
    context_blocks, sources = await _companion_context(
        request.question, request.use_papers, request.use_web
    )
    try:
        answer = await asyncio.to_thread(
            screen_companion.ask,
            llm_router,
            request.question,
            image_base64=request.image_base64,
            history=history,
            region=request.region,
            context_blocks=context_blocks or None,
            **params,
        )
        return {"answer": answer, "sources": sources}
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"answer": "", "sources": [], "error": str(exc)}


@app.get("/companion/config")
def companion_config() -> dict[str, Any]:
    """Companion defaults plus the selectable providers/models for the overlay picker.

    Uses cached model options (``refresh=False``) so opening the overlay stays instant;
    the picker can call ``POST /models/{provider}/discover`` for a live refresh."""
    cfg = _load_companion_config()
    return {
        "provider": str(cfg.get("provider") or "").strip() or llm_router.default_provider,
        "model": str(cfg.get("model") or "").strip(),
        "language": str(cfg.get("language") or "de"),
        "default_provider": llm_router.default_provider,
        "providers": [
            {"name": name, "models": llm_router.provider_model_options(name, refresh=False)}
            for name in llm_router.available_providers()
        ],
    }


# --------------------------------------------------------------------------- #
# Native Selbst-Steuerung (R7 skeleton) — the brain; enigo (control.rs) = hands  #
# --------------------------------------------------------------------------- #

_SELF_DRIVE_STORE = self_drive.SelfDriveStore()


def _self_drive_config() -> dict[str, Any]:
    """The ``companion.self_drive`` sub-block (enabled gate + step budget)."""
    section = _load_companion_config().get("self_drive")
    return section if isinstance(section, dict) else {}


@app.post("/selfdrive/start")
def self_drive_start(request: SelfDriveStartRequest) -> dict[str, Any]:
    """Open a Selbst-Steuerung session. Gated on ``companion.self_drive.enabled``;
    the native shell still requires an explicit arm + per-action confirmation."""
    cfg = _self_drive_config()
    if not bool(cfg.get("enabled", False)):
        return {"error": "Selbst-Steuerung ist deaktiviert (companion.self_drive.enabled)."}
    params = _companion_llm_params(request.provider, request.model)
    session = _SELF_DRIVE_STORE.create(
        goal=request.goal,
        provider=params["provider"],
        model=params["model"],
        monitor=request.monitor,
        max_steps=int(cfg.get("max_steps") or self_drive.DEFAULT_MAX_STEPS),
    )
    return {"session_id": session.session_id, "goal": session.goal, "max_steps": session.max_steps}


@app.post("/selfdrive/step")
async def self_drive_step(request: SelfDriveStepRequest) -> dict[str, Any]:
    """Plan the next action for a session from the current screenshot. The frontend
    executes the returned action (control.rs) and calls back with the next frame."""
    if not bool(_self_drive_config().get("enabled", False)):
        return {"error": "Selbst-Steuerung ist deaktiviert (companion.self_drive.enabled)."}
    session = _SELF_DRIVE_STORE.get(request.session_id)
    if session is None:
        return {"error": "Unbekannte Sitzung."}
    params = _companion_llm_params(session.provider, session.model)
    try:
        return await asyncio.to_thread(
            self_drive.plan_step,
            llm_router,
            session,
            request.image_base64,
            max_pixels=params["max_pixels"],
            history_turns=params["history_turns"],
            max_tokens=params["max_tokens"] or self_drive.DEFAULT_MAX_TOKENS,
            disable_thinking=params["disable_thinking"],
        )
    except Exception as exc:  # noqa: BLE001 - surface as a normal JSON error
        return {"error": str(exc)}


@app.post("/selfdrive/stop")
def self_drive_stop(request: SelfDriveStopRequest) -> dict[str, Any]:
    """Drop a session (idempotent)."""
    return {"stopped": _SELF_DRIVE_STORE.drop(request.session_id)}


@app.post("/research/tree/export")
async def research_tree_export(request: ResearchTreeExportRequest) -> Response:
    """Render the Tiefenanalyse synthesis as a LaTeX PDF (or .tex/.zip).

    Builds the document (with optional TikZ tree, charts, tables, ComfyUI images and a
    BibTeX bibliography) and compiles it via MiKTeX/latexmk. If no LaTeX engine is
    found or compilation fails, a ZIP with the .tex/.bib sources is returned instead
    (the actual format is reported in the ``X-Export-Format`` header).
    """
    try:
        result = await asyncio.to_thread(
            build_export,
            root_question=request.root_question,
            document=request.document,
            nodes=request.nodes,
            sources=request.sources,
            options=ExportOptions(
                tikz_tree=request.options.tikz_tree,
                charts=request.options.charts,
                tables=request.options.tables,
                comfyui_images=request.options.comfyui_images,
            ),
            export_format=request.format,
            exports_dir="data/exports",
            llm_router=llm_router,
            provider=request.provider,
            model=request.model,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    actual_format = result.filename.rsplit(".", 1)[-1]
    return Response(
        content=result.content,
        media_type=result.media_type,
        headers={
            "Content-Disposition": f'attachment; filename="{result.filename}"',
            "X-Export-Format": actual_format,
            # HTTP headers are latin-1 only; ensure_ascii escapes umlauts/dashes,
            # which the frontend's JSON.parse decodes back transparently.
            "X-Export-Warnings": json.dumps(result.warnings),
        },
    )


_CLARIFY_SYSTEM = (
    "Du bist ein wissenschaftlicher Forschungsassistent. "
    "Du überlegst, in welche thematischen Richtungen sich eine Tiefenanalyse einer "
    "Forschungsfrage entwickeln könnte, damit der Nutzer den Schwerpunkt wählen kann."
)

_CLARIFY_USER = (
    'Forschungsfrage: "{question}"\n\n'
    "Überlege, in welche Richtungen diese Frage vertieft werden könnte, und schlage "
    "4-6 konkrete thematische Schwerpunkte vor, aus denen der Nutzer auswählen kann.\n"
    "Beispiele für Richtungen: 'Verhaltensweisen beim Menschen', 'Biologischer Hintergrund', "
    "'Klinische Anwendungen', 'Methodischer Vergleich', 'Entwicklung seit 2020', "
    "'Gesellschaftliche Auswirkungen'.\n"
    "Jede Richtung ist ein kurzer, prägnanter Titel (max. 5 Wörter), spezifisch zur Frage.\n"
    "Antworte NUR mit einem JSON-Objekt (kein Markdown, kein Text davor/danach):\n"
    '{"directions": ["Richtung 1", "Richtung 2", "Richtung 3", "Richtung 4"]}'
)


@app.post("/research/clarify")
async def research_clarify(request: ResearchClarifyRequest) -> dict[str, Any]:
    """Suggest 4-6 thematic focus directions to steer a deep analysis."""
    overrides: dict[str, Any] = {"max_tokens": 400, "temperature": 0.5}
    if request.model:
        overrides["model"] = request.model
    directions: list[str] = []
    try:
        text = await asyncio.to_thread(
            llm_router.chat,
            messages=[
                {"role": "system", "content": _CLARIFY_SYSTEM},
                {"role": "user", "content": _CLARIFY_USER.format(question=request.question)},
            ],
            provider=request.provider,
            overrides=overrides,
        )
        raw = (text or "").strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = re.sub(r"^```[a-z]*\n?", "", raw)
            raw = re.sub(r"\n?```$", "", raw)
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                ds = parsed.get("directions") or parsed.get("questions") or []
                directions = [str(d).strip() for d in ds if str(d).strip()][:6]
            elif isinstance(parsed, list):
                directions = [str(d).strip() for d in parsed if str(d).strip()][:6]
        except (json.JSONDecodeError, ValueError):
            directions = _extract_questions(raw, 6)
    except Exception:
        directions = []
    return {"directions": directions}


@app.get("/projects/{project_id}/grey-sources")
def list_project_grey_sources(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        items = db.list_grey_sources(project_id)
    return {"project_id": project_id, "grey_sources": items}


@app.post("/projects/{project_id}/grey-sources")
def add_project_grey_sources(project_id: str, payload: GreySourcePayload) -> dict[str, Any]:
    """Persist user-confirmed grey sources for a project (not added to the KG)."""
    saved: list[dict[str, Any]] = []
    with MetadataDB(payload.metadata_db_path) as db:
        for source in payload.sources:
            record = dict(source)
            record.setdefault("query", payload.query)
            saved.append(db.add_grey_source(project_id, record))
    return {"project_id": project_id, "saved": saved}


@app.post("/projects/{project_id}/grey-sources/from-url")
async def add_grey_source_from_url(project_id: str, payload: GreySourceFromUrlPayload) -> dict[str, Any]:
    """Fetch, sanitize, and save a single user-pasted URL as a grey source.

    Mirrors the deep-research fetch path (sanitize_web_text) but skips the LLM
    summarization step — title comes from a cheap <title>/<h1> regex heuristic so
    pasting a link stays fast.
    """
    url = payload.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="Nur http(s)-URLs werden unterstützt")
    try:
        async with httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": "ScienceKG/Phase5 (local)"},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Konnte URL nicht laden: {exc}") from exc

    raw_html = response.text
    clean, flags = sanitize_web_text(raw_html, max_len=FULL_TEXT_MAX_LEN)
    title = _infer_title_from_html(raw_html) or url
    record = {
        "url": url,
        "title": title,
        "summary": clean[:400],
        "full_text": clean,
        "injection_flags": flags,
    }
    with MetadataDB(payload.metadata_db_path) as db:
        saved = db.add_grey_source(project_id, record)
    return {"project_id": project_id, "saved": saved}


@app.get("/workspace/sessions/{project_id}")
def get_workspace_session(project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    """Server-side workspace assistant session (chat history + verification payloads).

    Sessions used to live only in localStorage, where large verification payloads
    routinely blew the quota and the save silently failed — conversations vanished
    on reload. DuckDB has no such limit.
    """
    with MetadataDB(metadata_db_path) as db:
        session = db.get_workspace_session(project_id)
    return session or {"project_id": project_id, "payload": {}, "updated_timestamp": None}


@app.put("/workspace/sessions/{project_id}")
def save_workspace_session(project_id: str, request: WorkspaceSessionPayload) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        session = db.save_workspace_session(project_id, request.payload)
    return session


@app.delete("/grey-sources/{grey_id}")
def delete_grey_source(grey_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_grey_source(grey_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Grey source not found: {grey_id}")
    return {"deleted": True, "id": grey_id}


@app.get("/extraction/library")
def extraction_library(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    query: str = "",
    project_id: str | None = None,
    projects_path: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100000),
) -> dict[str, Any]:
    rows = _local_pdf_library(metadata_db_path, pdf_base_dir)
    # "Alle Papers" / no project = global union over every project; else scope to members.
    is_global = (not project_id) or _is_reserved_project_id(project_id)
    member_ids: set[str] | None
    if is_global:
        member_ids = None
    else:
        projects = _load_projects(_projects_path(projects_path))
        member_ids = set(projects.get(project_id or "", []))
        rows = [row for row in rows if str(row.get("paper_id") or "") in member_ids]
    try:
        with MetadataDB(metadata_db_path) as _db:
            grey_list = _db.list_grey_sources(None if is_global else project_id, limit=50000)
            latest_by_paper = _latest_extraction_statuses(_db)
            found_ids = {str(row.get("paper_id") or "") for row in rows}
            nopdf_papers = [
                p for p in _db.list_papers(limit=50000)
                if str(p.get("id") or "") not in found_ids
                and (member_ids is None or str(p.get("id") or "") in member_ids)
            ]
        for grey in grey_list:
            if grey.get("injection_flags"):
                continue
            full_text = (grey.get("full_text") or "").strip()
            if not full_text:
                continue
            rows.append({
                "paper_id": f"grey::{grey['id']}",
                "title": grey.get("title") or grey.get("url") or grey["id"],
                "filename": "",
                "pdf_path": "",
                "pdf_available": True,
                "source_type": "grey",
                "text": full_text[:200000],
                "size_bytes": len(full_text.encode("utf-8")),
                "modified_timestamp": grey.get("created_timestamp"),
                "latest_extraction_status": None,
                "known_paper": False,
            })
        for paper in nopdf_papers:
            pid = str(paper.get("id") or "")
            rows.append({
                "paper_id": pid,
                "title": paper.get("title") or pid,
                "filename": "",
                "pdf_path": "",
                "pdf_available": False,
                "source_type": "pdf",
                "size_bytes": None,
                "modified_timestamp": None,
                "latest_extraction_status": latest_by_paper.get(pid),
                "known_paper": True,
            })
    except Exception:
        pass
    if query:
        query_lower = query.lower()
        rows = [
            row for row in rows
            if query_lower in str(row.get("paper_id") or "").lower()
            or query_lower in str(row.get("title") or "").lower()
            or query_lower in str(row.get("filename") or "").lower()
        ]
    items = rows if limit is None else rows[:limit]
    return {"items": items, "total": len(rows)}


@app.post("/extraction/parse")
def parse_extraction_pdf(request: ExtractionParseRequest) -> dict[str, Any]:
    pdf_path = _resolve_extraction_pdf_path(
        request.paper_id,
        request.pdf_path,
        request.metadata_db_path,
        request.pdf_base_dir,
    )
    parsed = _parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
    with MetadataDB(request.metadata_db_path) as db:
        canonical_id = db.ensure_paper_record(
            request.paper_id,
            title=EntityExtractor._paper_title_from_text(parsed.text)[:240],
            pdf_path=str(pdf_path),
        )
    return {
        "paper_id": canonical_id,
        "pdf_path": str(pdf_path),
        "text": parsed.text,
        "page_count": parsed.page_count,
        "parser": str(parsed.parser.value if hasattr(parsed.parser, "value") else parsed.parser),
        "metadata": _parsed_document_metadata(parsed),
        "excerpt": parsed.text[:4000],
    }


@app.post("/extraction/extract")
def run_extraction(request: ExtractionRunRequest) -> dict[str, Any]:
    text = (request.text or "").strip()
    pdf_path: Path | None = None
    parse_payload: dict[str, Any] | None = None
    if not text and request.paper_id.startswith("grey::"):
        grey_id = request.paper_id.removeprefix("grey::")
        with MetadataDB(request.metadata_db_path) as db:
            grey = db.get_grey_source(grey_id)
        if not grey or not (grey.get("full_text") or "").strip():
            raise HTTPException(status_code=404, detail=f"Grey source has no text: {grey_id}")
        text = grey["full_text"].strip()
    elif not text:
        try:
            pdf_path = _resolve_extraction_pdf_path(
                request.paper_id,
                request.pdf_path,
                request.metadata_db_path,
                request.pdf_base_dir,
            )
        except HTTPException as pdf_error:
            # Kein lokales PDF: Abstract-only-Extraktion. Viele Paper haben zwar keinen
            # PDF-Download, aber Titel + Abstract in der papers-Tabelle — die reichen
            # für eine (dünnere) Aufnahme in den Knowledge Graph.
            abstract_text = _abstract_only_extraction_text(request.paper_id, request.metadata_db_path)
            if not abstract_text:
                raise pdf_error
            text = abstract_text
            parse_payload = {
                "pdf_path": None,
                "page_count": 0,
                "parser": "abstract-only",
                "metadata": {},
                "excerpt": text[:4000],
            }
        else:
            parsed = _parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
            text = parsed.text
            parse_payload = {
                "pdf_path": str(pdf_path),
                "page_count": parsed.page_count,
                "parser": str(parsed.parser.value if hasattr(parsed.parser, "value") else parsed.parser),
                "metadata": _parsed_document_metadata(parsed),
                "excerpt": parsed.text[:4000],
            }
            _maybe_fix_garbled_pdf_title(
                paper_id=request.paper_id,
                text=text,
                pdf_path=pdf_path,
                metadata_db_path=request.metadata_db_path,
            )
    if not text:
        raise HTTPException(status_code=400, detail="No paper text or parseable PDF text provided.")

    overrides = _extraction_overrides(request)
    start = time.monotonic()
    is_grey = request.paper_id.startswith("grey::")
    try:
        result = extraction_pipeline.process(
            request.paper_id,
            text,
            provider=request.provider,
            overrides=overrides,
            link_concepts=request.link_concepts,
        )
    except Exception as exc:
        if request.save:
            with MetadataDB(request.metadata_db_path) as db:
                if is_grey:
                    canonical_id = request.paper_id
                else:
                    canonical_id = db.ensure_paper_record(
                        request.paper_id,
                        title=EntityExtractor._paper_title_from_text(text)[:240],
                        pdf_path=str(pdf_path) if pdf_path else request.pdf_path,
                    )
                db.save_extraction_result(
                    paper_id=canonical_id,
                    llm_provider=request.provider or getattr(llm_router, "default_provider", "default"),
                    llm_model=_selected_model(request.provider, request.model),
                    error_message=str(exc),
                    duration_seconds=time.monotonic() - start,
                )
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}") from exc

    duration = time.monotonic() - start
    failure_reason = extraction_failure_reason(result)
    canonical_id = request.paper_id
    result_id: int | None = None
    if request.save:
        with MetadataDB(request.metadata_db_path) as db:
            if is_grey:
                canonical_id = request.paper_id
            else:
                canonical_id = db.ensure_paper_record(
                    request.paper_id,
                    title=EntityExtractor._paper_title_from_text(text)[:240],
                    year=_year_from_extraction_result(result),
                    pdf_path=str(pdf_path) if pdf_path else request.pdf_path,
                )
            result_id = db.save_extraction_result(
                paper_id=canonical_id,
                llm_provider=request.provider or getattr(llm_router, "default_provider", "default"),
                llm_model=_selected_model(request.provider, request.model),
                paper_type=result.paper_type,
                concepts=result.concepts,
                methods=result.methods,
                concept_candidates=result.concept_candidates,
                method_candidates=result.method_candidates,
                relations=result.relations,
                claims=result.claims,
                cross_domain_hints=result.cross_domain_hints,
                terminology_conflicts=result.terminology_conflicts,
                temporal_coverage=result.temporal_coverage,
                mathematical_content=result.mathematical_content,
                raw_response=result.raw_response,
                error_message=failure_reason,
                duration_seconds=duration,
            )
    return {
        "result_id": result_id,
        "paper_id": canonical_id,
        "status": "failed" if failure_reason else "success",
        "error_message": failure_reason,
        "duration_seconds": duration,
        "parse": parse_payload,
        "result": _extraction_result_payload(result),
    }


@app.post("/extraction/batch")
def run_extraction_batch(request: ExtractionBatchRequest) -> dict[str, Any]:
    if not request.items:
        raise HTTPException(status_code=400, detail="Select at least one PDF.")
    pdf_paths: dict[str, str] = {}
    for item in request.items:
        pdf_paths[item.paper_id] = str(
            _resolve_extraction_pdf_path(
                item.paper_id,
                item.pdf_path,
                request.metadata_db_path,
                request.pdf_base_dir,
            )
        )
    processor = BatchProcessor(
        llm_router,
        parser_router,
        embedding_engine,
        metadata_db_factory=lambda: MetadataDB(request.metadata_db_path),
        link_concepts=request.link_concepts,
        quality_db_path=request.metadata_db_path,
    )
    status = processor.process_papers(
        list(pdf_paths),
        pdf_paths,
        job_id=request.job_id,
        llm_provider=request.provider,
        llm_overrides=_extraction_overrides(request),
        resume=request.resume,
    )
    with MetadataDB(request.metadata_db_path) as db:
        items = db.get_batch_job_items(status.job_id)
    return {
        "job": {
            "job_id": status.job_id,
            "status": status.status,
            "papers_total": status.papers_total,
            "papers_processed": status.papers_processed,
            "papers_failed": status.papers_failed,
            "error_message": status.error_message,
            "superseded_by": status.superseded_by,
        },
        "items": items,
    }


@app.get("/extraction/batch/{job_id}/items")
def get_extraction_batch_items(job_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        job = db.get_batch_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        items = db.get_batch_job_items(job_id)
    return {"job_id": job_id, "items": items}


@app.post("/extraction/batch/{job_id}/cancel")
def cancel_extraction_batch(job_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        job = db.get_batch_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        db.cancel_batch_job(job_id)
        updated = db.get_batch_job(job_id)
    return {"job_id": job_id, "status": (updated or {}).get("status")}


@app.get("/extraction/history")
def extraction_history(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    paper_id: str = "",
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        items = db.get_paper_extractions(paper_id, limit=limit) if paper_id.strip() else db.list_extraction_results(limit=limit)
    return {"items": items, "total": len(items)}


@app.get("/extraction/vocabulary")
def extraction_vocabulary(vocabulary_path: str = DEFAULT_VOCABULARY_PATH) -> dict[str, Any]:
    vocabulary = _load_vocabulary(vocabulary_path)
    entries = [
        {
            "canonical_label": canonical,
            "aliases": entry.aliases,
            "openalx_id": entry.openalx_id,
            "domain": entry.domain,
            "confidence": entry.confidence,
            "custom_metadata": entry.custom_metadata,
        }
        for canonical, entry in sorted(vocabulary.entries.items())
    ]
    return {"items": entries, "total": len(entries)}


@app.post("/extraction/vocabulary")
def add_extraction_vocabulary_entry(request: VocabularyEntryRequest) -> dict[str, Any]:
    vocabulary = _load_vocabulary(request.vocabulary_path)
    vocabulary.register(
        request.canonical_label.strip(),
        aliases=[alias.strip() for alias in request.aliases if alias.strip()],
        openalx_id=request.openalx_id or None,
        domain=request.domain or None,
    )
    _save_vocabulary(vocabulary, request.vocabulary_path)
    return extraction_vocabulary(request.vocabulary_path)


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


class CreateWorkspaceRequest(BaseModel):
    name: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class OpenWorkspaceRequest(BaseModel):
    path: str
    name: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class WriteFileRequest(BaseModel):
    path: str
    content: str = ""
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class CreatePathRequest(BaseModel):
    path: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH






def _require_code_project(db: MetadataDB, project_id: str) -> dict[str, Any]:
    proj = db.get_code_project(project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Code-Projekt nicht gefunden")
    return proj


def _code_project_summary(project: dict[str, Any]) -> dict[str, Any]:
    """Project record + a cheap on-disk existence flag for the picker."""
    out = dict(project)
    try:
        out["exists"] = Path(str(project.get("path"))).is_dir()
    except OSError:
        out["exists"] = False
    return out


@app.get("/workspaces")
def list_workspaces(metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        projects = [_code_project_summary(p) for p in db.list_code_projects()]
    return {
        "projects": projects,
        "base_dir": str(workspace_manager.base_dir()),
        "git_available": workspace_manager.git_available(),
    }


@app.post("/workspaces")
def create_workspace(request: CreateWorkspaceRequest) -> dict[str, Any]:
    """Create a new *managed* project folder (mkdir + git init) and register it."""
    name = (request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Projektname fehlt")
    root = workspace_manager.init_managed_project(workspace_manager.base_dir(), name)
    with MetadataDB(request.metadata_db_path) as db:
        project = db.add_code_project(name=name, path=str(root), kind="managed")
    return _code_project_summary(project)


@app.post("/workspaces/open")
def open_workspace(request: OpenWorkspaceRequest) -> dict[str, Any]:
    """Register an existing folder as an *external* project ("Ordner öffnen")."""
    root = workspace_manager.validate_external_folder(request.path)
    name = (request.name or "").strip() or root.name
    with MetadataDB(request.metadata_db_path) as db:
        existing = db.get_code_project_by_path(str(root))
        if existing is not None:
            return _code_project_summary(existing)
        project = db.add_code_project(name=name, path=str(root), kind="external")
    return _code_project_summary(project)


@app.delete("/workspaces/{project_id}")
def delete_workspace(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Unregister a project. The folder on disk is left untouched."""
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_code_project(project_id)
    return {"deleted": deleted, "id": project_id}


@app.get("/workspaces/{project_id}/tree")
def workspace_tree(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.build_tree(root)


@app.get("/workspaces/{project_id}/file")
def workspace_read_file(
    project_id: str, path: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.read_file(root, path)


@app.put("/workspaces/{project_id}/file")
def workspace_write_file(project_id: str, request: WriteFileRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.write_file(root, request.path, request.content)


@app.post("/workspaces/{project_id}/file")
def workspace_create_file(project_id: str, request: CreatePathRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.create_file(root, request.path)


@app.post("/workspaces/{project_id}/dir")
def workspace_create_dir(project_id: str, request: CreatePathRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.create_dir(root, request.path)


@app.delete("/workspaces/{project_id}/file")
def workspace_delete_file(
    project_id: str, path: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.delete_path(root, path)


@app.get("/workspaces/{project_id}/git/status")
def workspace_git_status(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.git_status(root)


@app.get("/workspaces/{project_id}/git/diff")
def workspace_git_diff(
    project_id: str,
    path: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.git_diff(root, path)


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


def _projects_path(value: str | None = None) -> Path:
    if not value:
        return PROJECTS_PATH
    return ensure_safe_path(value, what="projects path")


def _load_projects(path: Path = PROJECTS_PATH) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {
        str(project_id): _unique_strings(paper_ids if isinstance(paper_ids, list) else [])
        for project_id, paper_ids in data.items()
    }


def _save_projects(projects: dict[str, list[str]], path: Path = PROJECTS_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(projects, indent=2, sort_keys=True), encoding="utf-8")


def _load_primary_papers(path: Path = PROJECT_PRIMARY_PATH) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(pid): str(value) for pid, value in data.items() if value}


def _save_primary_papers(mapping: dict[str, str], path: Path = PROJECT_PRIMARY_PATH) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(mapping, indent=2, sort_keys=True), encoding="utf-8")


def _project_view(project_id: str, paper_ids: list[str], papers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    years = [int(papers[pid]["year"]) for pid in paper_ids if pid in papers and papers[pid].get("year")]
    return {
        "id": project_id,
        "name": project_id,
        "paper_ids": paper_ids,
        "paper_count": len(paper_ids),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
        "primary_paper_id": _load_primary_papers().get(project_id),
    }


def _is_reserved_project_id(project_id: str) -> bool:
    return project_id.strip().lower() in RESERVED_PROJECT_IDS


def _attach_papers_to_project(
    project_id: str | None,
    paper_ids: list[str],
    projects_path: str | None = None,
) -> list[str]:
    """Add freshly downloaded/uploaded papers to a real project's membership.

    Returns the project's paper ids after attach, or [] when no real project is
    targeted. Downloads in the global ``Alle Papers`` scope (no/reserved project)
    stay unattached and remain assignable later.
    """
    if not project_id or _is_reserved_project_id(project_id):
        return []
    clean_ids = _unique_strings(paper_ids)
    if not clean_ids:
        return []
    path = _projects_path(projects_path)
    projects = _load_projects(path)
    if project_id not in projects:
        return []
    projects[project_id] = _unique_strings([*projects[project_id], *clean_ids])
    _save_projects(projects, path)
    return projects[project_id]


def _project_memberships(projects: dict[str, list[str]]) -> dict[str, set[str]]:
    memberships: dict[str, set[str]] = {}
    for project_id, paper_ids in projects.items():
        for pid in paper_ids:
            memberships.setdefault(pid, set()).add(project_id)
    return memberships


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for value in values:
        item = str(value or "").strip()
        if item and item not in seen:
            seen.add(item)
            output.append(item)
    return output


def _paper_matches_query(paper: dict[str, Any], query: str) -> bool:
    haystack = " ".join(str(paper.get(key) or "") for key in ["id", "source_id", "title", "abstract", "doi"]).lower()
    return all(token in haystack for token in re.findall(r"[a-z0-9._:-]+", query.lower()))


def _latest_extraction_statuses(db: MetadataDB) -> dict[str, str]:
    latest_by_paper: dict[str, str] = {}
    for extraction in db.list_extraction_results(limit=50000):
        pid = str(extraction.get("paper_id") or "")
        if pid and pid not in latest_by_paper:
            latest_by_paper[pid] = str(extraction.get("extraction_status") or "unknown")
    return latest_by_paper


def _latest_successful_extractions(extractions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for extraction in extractions:
        pid = str(extraction.get("paper_id") or "")
        if not pid or pid in latest or extraction.get("extraction_status") != "success":
            continue
        latest[pid] = extraction
    return latest


def _local_pdf_library(metadata_db_path: str, pdf_base_dir: str) -> list[dict[str, Any]]:
    base = Path(pdf_base_dir)
    latest_by_paper: dict[str, str] = {}
    rows_by_path: dict[str, dict[str, Any]] = {}
    with MetadataDB(metadata_db_path) as db:
        latest_by_paper = _latest_extraction_statuses(db)
        for paper in db.list_papers(limit=50000):
            view = _paper_list_view(paper, pdf_base_dir)
            pdf_path = view.get("pdf_path")
            if not pdf_path:
                continue
            path = Path(str(pdf_path))
            key = str(path.resolve()) if path.exists() else str(path)
            pid = str(view.get("id") or view.get("paper_id") or "")
            rows_by_path[key] = {
                "paper_id": pid or _default_paper_id_from_pdf(path.name),
                "title": view.get("display_title") or view.get("title") or _clean_pdf_title(path.name),
                "filename": path.name,
                "pdf_path": str(path),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "modified_timestamp": datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None,
                "latest_extraction_status": latest_by_paper.get(pid),
                "known_paper": True,
            }

    if base.exists():
        for path in sorted(base.rglob("*.pdf")):
            key = str(path.resolve())
            if key in rows_by_path:
                continue
            paper_id_value = _default_paper_id_from_pdf(path.name)
            rows_by_path[key] = {
                "paper_id": paper_id_value,
                "title": _clean_pdf_title(path.name) or paper_id_value,
                "filename": path.name,
                "pdf_path": str(path),
                "size_bytes": path.stat().st_size,
                "modified_timestamp": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
                "latest_extraction_status": latest_by_paper.get(paper_id_value),
                "known_paper": False,
            }
    rows = list(rows_by_path.values())
    rows.sort(key=lambda row: str(row.get("modified_timestamp") or ""), reverse=True)
    return rows


def _default_paper_id_from_pdf(filename: str) -> str:
    stem = Path(str(filename).replace("\\", "/")).stem
    cleaned = re.sub(r"__+", "__", stem.strip())
    if cleaned:
        arxiv_match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", cleaned)
        if arxiv_match:
            arxiv_id = re.sub(r"v\d+$", "", arxiv_match.group(1), flags=re.IGNORECASE)
            return f"arxiv:{arxiv_id}"
        legacy_arxiv = re.search(r"([a-z-]+_\d{7})", cleaned, flags=re.IGNORECASE)
        if legacy_arxiv:
            return "arxiv:" + legacy_arxiv.group(1).replace("_", "/")
    return _slug(cleaned or filename)


def _abstract_only_extraction_text(paper_id_value: str, metadata_db_path: str) -> str:
    """Titel + Abstract als Extraktionstext für Paper ohne lokales PDF.

    Damit landen auch PDF-lose Paper (nur Metadaten) im Knowledge Graph —
    die Extraktion ist dünner, aber Konzepte/Claims aus dem Abstract sind
    besser als gar keine Aufnahme.
    """
    try:
        with MetadataDB(metadata_db_path) as db:
            paper = db.resolve_paper(paper_id_value)
    except Exception:
        return ""
    if not paper:
        return ""
    title = str(paper.get("title") or "").strip()
    abstract = str(paper.get("abstract") or "").strip()
    if not abstract:
        return ""
    parts = [title, "", "Abstract:", abstract]
    return "\n".join(part for part in parts if part is not None).strip()


def _resolve_extraction_pdf_path(
    paper_id_value: str,
    pdf_path: str | None,
    metadata_db_path: str,
    pdf_base_dir: str,
) -> Path:
    candidate: str | None = pdf_path
    if not candidate:
        with MetadataDB(metadata_db_path) as db:
            paper = db.resolve_paper(paper_id_value)
            if paper:
                candidate = _paper_local_pdf_path(paper, pdf_base_dir)
    if not candidate:
        candidate = find_pdf_path(paper_id_value, "", pdf_base_dir)
    if not candidate:
        raise HTTPException(status_code=404, detail=f"PDF not found for {paper_id_value}")

    path = Path(candidate)
    if not path.is_absolute():
        direct = path
        under_base = Path(pdf_base_dir) / path
        path = direct if direct.exists() else under_base
    resolved = path.resolve()
    base = Path(pdf_base_dir).resolve()
    if base not in [resolved, *resolved.parents]:
        raise HTTPException(status_code=400, detail="PDF path must be inside the configured PDF library.")
    if not resolved.exists() or resolved.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail=f"PDF file not found: {path}")
    return resolved


def _parse_pdf_for_extraction(pdf_path: Path, paper_id_value: str, parser_name: str | None):
    requested_parser = parser_name or "auto"
    forced_parser: ParserType | None = None
    if parser_name:
        try:
            forced_parser = ParserType(parser_name)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Unknown parser",
                    "paper_id": paper_id_value,
                    "pdf_path": str(pdf_path),
                    "parser": parser_name,
                },
            ) from exc
    try:
        return parser_router.parse(str(pdf_path), paper_id_value, force_parser=forced_parser)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "PDF parsing failed",
                "paper_id": paper_id_value,
                "pdf_path": str(pdf_path),
                "parser": requested_parser,
                "error": str(exc),
            },
        ) from exc


def _parsed_document_metadata(parsed: Any) -> dict[str, Any]:
    metadata = getattr(parsed, "metadata", None)
    if metadata is None:
        metadata = getattr(parsed, "meta", None)
    return metadata if isinstance(metadata, dict) else {}


def _extraction_overrides(request: Any) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key in [
        "model",
        "temperature",
        "top_p",
        "max_tokens",
        "context_size",
        "extraction_mode",
        "context_policy",
        "allow_context_fallback",
    ]:
        value = getattr(request, key, None)
        if value is not None and value != "":
            overrides[key] = value
    return overrides


def _selected_model(provider: str | None, model: str | None) -> str:
    if model:
        return model
    try:
        return llm_router.provider_default_model(provider)
    except Exception:
        return "unknown"


def _year_from_extraction_result(result: Any) -> int | None:
    coverage = getattr(result, "temporal_coverage", {}) or {}
    value = coverage.get("paper_year") if isinstance(coverage, dict) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extraction_result_payload(result: Any) -> dict[str, Any]:
    diagnostics = getattr(result, "extraction_diagnostics", {}) or {}
    return {
        "paper_id": getattr(result, "paper_id", ""),
        "paper_type": getattr(result, "paper_type", "unknown"),
        "concepts": getattr(result, "concepts", []) or [],
        "methods": getattr(result, "methods", []) or [],
        "concept_candidates": getattr(result, "concept_candidates", []) or [],
        "method_candidates": getattr(result, "method_candidates", []) or [],
        "relations": getattr(result, "relations", []) or [],
        "claims": getattr(result, "claims", []) or [],
        "cross_domain_hints": getattr(result, "cross_domain_hints", []) or [],
        "terminology_conflicts": getattr(result, "terminology_conflicts", []) or [],
        "temporal_coverage": getattr(result, "temporal_coverage", {}) or {},
        "mathematical_content": getattr(result, "mathematical_content", {}) or {},
        "language_detected": getattr(result, "language_detected", "unknown"),
        "quality_warnings": getattr(result, "quality_warnings", []) or [],
        "metadata_status": getattr(result, "metadata_status", "valid"),
        "blocking_errors": getattr(result, "blocking_errors", []) or [],
        "candidate_count": getattr(result, "candidate_count", 0) or 0,
        "extraction_diagnostics": diagnostics,
        "context_diagnostics": diagnostics.get("context_diagnostics") if isinstance(diagnostics, dict) else {},
        "raw_response": getattr(result, "raw_response", None),
    }


def _load_vocabulary(vocabulary_path: str) -> VocabularyManager:
    path = Path(vocabulary_path)
    if not path.exists():
        return VocabularyManager()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return VocabularyManager()
    return VocabularyManager.from_dict(data if isinstance(data, dict) else {})


def _save_vocabulary(vocabulary: VocabularyManager, vocabulary_path: str) -> None:
    path = Path(vocabulary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vocabulary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")


def _paper_sort_key(sort: str):
    key_name = {
        "title_asc": "title",
        "title_desc": "title",
        "year_asc": "year",
        "year_desc": "year",
        "added_asc": "added_timestamp",
        "added_desc": "added_timestamp",
    }.get(sort, "added_timestamp")

    def key(paper: dict[str, Any]) -> Any:
        value = paper.get(key_name)
        if key_name == "year":
            return int(value or 0)
        return str(value or "").lower()

    return key


_HARVESTER_CONFIG_CACHE: dict[str, Any] | None = None


def _load_harvester_config() -> dict[str, Any]:
    """Load and cache the `harvester:` section of config.yaml (env-resolved keys).

    .env has already been loaded into os.environ by the module-level LLMRouter, so
    `*_env` references resolve via os.getenv here.
    """
    global _HARVESTER_CONFIG_CACHE
    if _HARVESTER_CONFIG_CACHE is not None:
        return _HARVESTER_CONFIG_CACHE
    cfg: dict[str, Any] = {}
    try:
        with open("config.yaml", "r", encoding="utf-8") as fh:
            cfg = (yaml.safe_load(fh) or {}).get("harvester", {}) or {}
    except FileNotFoundError:
        cfg = {}
    _HARVESTER_CONFIG_CACHE = cfg
    return cfg


def _harvester_section(name: str) -> dict[str, Any]:
    section = _load_harvester_config().get(name, {})
    return section if isinstance(section, dict) else {}


def _resolved_key(section: dict[str, Any], env_default: str) -> str | None:
    env_name = section.get("api_key_env") or env_default
    return os.getenv(env_name) or section.get("api_key")


def _unpaywall_email() -> str | None:
    section = _harvester_section("unpaywall")
    env_name = section.get("email_env") or "UNPAYWALL_EMAIL"
    email = os.getenv(env_name) or section.get("email")
    if not email or "@example.com" in str(email):
        return None
    return str(email)


def _crossref_mailto() -> str | None:
    section = _harvester_section("crossref")
    env_name = section.get("mailto_env") or "CROSSREF_MAILTO"
    return os.getenv(env_name) or section.get("mailto") or _unpaywall_email()


async def _resolve_oa_pdf_url(doi: str | None, client: httpx.AsyncClient | None = None) -> str | None:
    """Resolve an open-access PDF URL for a DOI via Unpaywall (best-effort).

    Delegates to the shared ``harvester.oa_resolver`` so the same logic backs the
    auto-harvester's on-demand PDF acquisition.
    """
    return await resolve_oa_pdf_url(doi)


async def _run_harvest_search(query: str, sources: list[str], max_results: int) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_sources = {source.lower() for source in sources}
    results: list[dict[str, Any]] = []
    warnings: list[str] = []

    async def run_source(source: str) -> None:
        try:
            if source == "arxiv":
                client = ArxivClient()
                try:
                    results.extend(await client.search(query, max_results=max_results))
                finally:
                    await client.close()
            elif source == "semantic_scholar":
                client = SemanticScholarClient()
                try:
                    payload = await client.search_papers(
                        query,
                        limit=max_results,
                        fields="paperId,corpusId,title,abstract,authors,year,externalIds,openAccessPdf,url",
                    )
                    results.extend(_normalize_semantic_scholar_paper(item) for item in payload.get("data", []))
                finally:
                    await client.close()
            elif source == "openalex":
                client = OpenAlexClient()
                try:
                    payload = await client.list_works(search=query, per_page=max_results)
                    results.extend(_normalize_openalex_work(item) for item in payload.get("results", []))
                finally:
                    await client.close()
            elif source == "crossref":
                client = CrossrefClient(CrossrefConfig(mailto=_crossref_mailto()))
                try:
                    items = await client.search_works(query, rows=max_results)
                    results.extend(_normalize_crossref_work(item) for item in items)
                finally:
                    await client.close()
            elif source == "europepmc":
                client = EuropePMCClient(EuropePMCConfig())
                try:
                    items = await client.search(query, page_size=max_results)
                    results.extend(_normalize_europepmc_result(item) for item in items)
                finally:
                    await client.close()
            elif source == "biorxiv":
                # Native bioRxiv API has no keyword search; use Europe PMC preprint filter.
                client = EuropePMCClient(EuropePMCConfig())
                try:
                    preprint_query = f'({query}) AND SRC:PPR AND (PUBLISHER:"bioRxiv" OR PUBLISHER:"medRxiv")'
                    items = await client.search(preprint_query, page_size=max_results)
                    results.extend(_normalize_europepmc_result(item, source="biorxiv") for item in items)
                finally:
                    await client.close()
            elif source == "core":
                section = _harvester_section("core")
                client = CoreClient(CoreConfig(api_key=_resolved_key(section, "CORE_API_KEY")))
                try:
                    items = await client.search_works(query, limit=max_results)
                    results.extend(_normalize_core_work(item) for item in items)
                finally:
                    await client.close()
            elif source == "doaj":
                client = DoajClient(DoajConfig())
                try:
                    items = await client.search_articles(query, page_size=max_results)
                    results.extend(_normalize_doaj_article(item) for item in items)
                finally:
                    await client.close()
        except CoreApiKeyMissing as exc:
            warnings.append(f"{source}: {exc}")
        except Exception as exc:
            warnings.append(f"{source}: {exc}")

    await asyncio.gather(*(run_source(source) for source in sorted(normalized_sources)))
    return _dedupe_harvest_results(results), warnings


def _dedupe_harvest_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for result in results:
        key = str(result.get("doi") or result.get("id") or f"{result.get('source')}:{result.get('source_id')}").lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output


def _normalize_openalex_work(work: dict[str, Any]) -> dict[str, Any]:
    openalex_id = str(work.get("id") or "").rsplit("/", 1)[-1]
    return {
        "source": "openalex",
        "source_id": openalex_id,
        "title": work.get("title") or "",
        "abstract": work.get("abstract") or "",
        "authors": [
            item.get("author", {}).get("display_name")
            for item in work.get("authorships", [])
            if isinstance(item, dict)
        ],
        "year": work.get("publication_year"),
        "doi": work.get("doi"),
        "pdf_url": ((work.get("best_oa_location") or {}).get("pdf_url") if isinstance(work.get("best_oa_location"), dict) else None),
        "landing_page_url": work.get("doi") or work.get("id"),
        "has_full_text": bool((work.get("best_oa_location") or {}).get("pdf_url")) if isinstance(work.get("best_oa_location"), dict) else False,
    }


def _normalize_semantic_scholar_paper(paper: dict[str, Any]) -> dict[str, Any]:
    external_ids = paper.get("externalIds") or {}
    open_access_pdf = paper.get("openAccessPdf") or {}
    return {
        "source": "semantic_scholar",
        "source_id": str(paper.get("paperId") or paper.get("corpusId") or "unknown"),
        "version": 1,
        "title": paper.get("title") or "",
        "abstract": paper.get("abstract") or "",
        "authors": [author.get("name", "") for author in paper.get("authors", []) if isinstance(author, dict)],
        "year": paper.get("year"),
        "doi": external_ids.get("DOI") or paper.get("doi"),
        "pdf_url": open_access_pdf.get("url") if isinstance(open_access_pdf, dict) else None,
        "landing_page_url": paper.get("url"),
        "has_full_text": bool(open_access_pdf.get("url")) if isinstance(open_access_pdf, dict) else False,
        "raw": paper,
    }


def _crossref_title(work: dict[str, Any]) -> str:
    title = work.get("title")
    if isinstance(title, list):
        return (title[0] if title else "") or ""
    return str(title or "")


def _crossref_year(work: dict[str, Any]) -> int | None:
    for key in ("published", "published-print", "published-online", "issued", "created"):
        parts = (work.get(key) or {}).get("date-parts") if isinstance(work.get(key), dict) else None
        if parts and isinstance(parts, list) and parts[0]:
            try:
                return int(parts[0][0])
            except (ValueError, TypeError, IndexError):
                continue
    return None


def _normalize_crossref_work(work: dict[str, Any]) -> dict[str, Any]:
    doi = work.get("DOI")
    authors = []
    for author in work.get("author", []) or []:
        if isinstance(author, dict):
            name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
            if name:
                authors.append(name)
    pdf_url = None
    for link in work.get("link", []) or []:
        if isinstance(link, dict) and link.get("content-type") == "application/pdf":
            pdf_url = link.get("URL")
            break
    abstract = re.sub(r"<[^>]+>", "", str(work.get("abstract") or "")).strip()
    return {
        "source": "crossref",
        "source_id": str(doi or work.get("URL") or "unknown"),
        "title": _crossref_title(work),
        "abstract": abstract,
        "authors": authors,
        "year": _crossref_year(work),
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else work.get("URL")),
        "has_full_text": bool(pdf_url),
    }


def _normalize_europepmc_result(item: dict[str, Any], source: str = "europepmc") -> dict[str, Any]:
    doi = item.get("doi")
    pdf_url = None
    for url_item in (item.get("fullTextUrlList") or {}).get("fullTextUrl", []) or []:
        if not isinstance(url_item, dict):
            continue
        if url_item.get("documentStyle") == "pdf" or str(url_item.get("url", "")).lower().endswith(".pdf"):
            pdf_url = url_item.get("url")
            break
    pmcid = item.get("pmcid")
    if not pdf_url and pmcid:
        pdf_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    authors = []
    author_string = item.get("authorString")
    if author_string:
        authors = [name.strip() for name in str(author_string).split(",") if name.strip()]
    return {
        "source": source,
        "source_id": str(item.get("id") or doi or pmcid or "unknown"),
        "title": str(item.get("title") or ""),
        "abstract": str(item.get("abstractText") or ""),
        "authors": authors,
        "year": int(item["pubYear"]) if str(item.get("pubYear") or "").isdigit() else None,
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else None),
        "has_full_text": str(item.get("isOpenAccess") or "").upper() == "Y" or bool(pdf_url),
    }


def _normalize_core_work(work: dict[str, Any]) -> dict[str, Any]:
    doi = work.get("doi")
    authors = [
        author.get("name", "")
        for author in (work.get("authors") or [])
        if isinstance(author, dict) and author.get("name")
    ]
    pdf_url = work.get("downloadUrl") or work.get("fullTextLink")
    year = work.get("yearPublished") or work.get("publishedDate")
    try:
        year_int = int(str(year)[:4]) if year else None
    except (ValueError, TypeError):
        year_int = None
    return {
        "source": "core",
        "source_id": str(work.get("id") or doi or "unknown"),
        "title": str(work.get("title") or ""),
        "abstract": str(work.get("abstract") or ""),
        "authors": authors,
        "year": year_int,
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else work.get("sourceFulltextUrls")),
        "has_full_text": bool(pdf_url),
    }


def _normalize_doaj_article(article: dict[str, Any]) -> dict[str, Any]:
    bib = article.get("bibjson", {}) if isinstance(article.get("bibjson"), dict) else {}
    doi = None
    pdf_url = None
    landing = None
    for ident in bib.get("identifier", []) or []:
        if isinstance(ident, dict) and ident.get("type") == "doi":
            doi = ident.get("id")
    for link in bib.get("link", []) or []:
        if not isinstance(link, dict):
            continue
        if link.get("type") == "fulltext":
            landing = link.get("url")
            if str(link.get("content_type", "")).lower() == "pdf" or str(link.get("url", "")).lower().endswith(".pdf"):
                pdf_url = link.get("url")
    authors = [a.get("name", "") for a in bib.get("author", []) or [] if isinstance(a, dict) and a.get("name")]
    return {
        "source": "doaj",
        "source_id": str(article.get("id") or doi or "unknown"),
        "title": str(bib.get("title") or ""),
        "abstract": str(bib.get("abstract") or ""),
        "authors": authors,
        "year": int(bib["year"]) if str(bib.get("year") or "").isdigit() else None,
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": landing or (f"https://doi.org/{doi}" if doi else None),
        "has_full_text": bool(pdf_url or landing),
    }


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


def _ratio(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0


def _split_query_values(values: list[str] | None) -> list[str]:
    output: list[str] = []
    for value in values or []:
        output.extend(item.strip() for item in value.split(",") if item.strip())
    return output


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
