"""Discovery & Tiefenanalyse: Themen-/Paper-Discovery, Deep-Research (Web),
Auto-Answer-Stream, Research-Tree (SSE + Sessions), LaTeX-Export, Clarify.

Split out of api/product_main.py. Behaviour unchanged. Patchbare Namen laufen
ueber pm.<name>: llm_router, auto_research_answer, ResearchTreeRunner,
_run_harvest_search, _resolve_extraction_pdf_path/_parse_pdf_for_extraction.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
import uuid
from collections.abc import AsyncIterator
from typing import Any

from fastapi import APIRouter, HTTPException, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons + geteilte Helfer
from api import phase4_main
from api.routers.harvest import _existing_library_keys
from api.routers.projects import _projects_path
from export import ExportOptions, aggregate_sources, build_export
from query.discovery import analyze_paper, analyze_topic
from research.sanitize import FULL_TEXT_MAX_LEN
from query.research_tree import _extract_questions
from query.web_research import run_deep_research
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_PDF_BASE_DIR = "data/pdfs"

router = APIRouter()


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


class ResearchTreeAsSourceRequest(BaseModel):
    """Store a finished Tiefenanalyse as a citable project source."""
    project_id: str = Field(min_length=1, max_length=200)
    root_question: str = Field(min_length=1, max_length=2000)
    document: str = Field(min_length=1)
    nodes: list[dict[str, Any]] = Field(default_factory=list)
    sources: list[dict[str, Any]] = Field(default_factory=list)
    session_id: str | None = Field(default=None, max_length=200)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ResearchClarifyRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    provider: str | None = None
    model: str | None = None



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
        results, _ = await pm._run_harvest_search(query, sources, max_per_query)
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


@router.post("/discovery/from-topic")
async def discovery_from_topic(request: DiscoveryTopicRequest) -> dict[str, Any]:
    """AI suggests topic-near papers to download for more context (suggest only)."""
    analysis = await asyncio.to_thread(analyze_topic, pm.llm_router, request.topic, request.provider)
    candidates = await _run_discovery_queries(
        analysis, request.sources, request.max_per_query, request.metadata_db_path
    )
    return {"analysis": analysis, "candidates": candidates}


@router.post("/discovery/from-paper")
async def discovery_from_paper(request: DiscoveryPaperRequest) -> dict[str, Any]:
    """AI analyzes an uploaded paper (topic + methods) and suggests related papers."""
    pdf_path = pm._resolve_extraction_pdf_path(
        request.paper_id, request.pdf_path, request.metadata_db_path, request.pdf_base_dir
    )
    parsed = pm._parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
    analysis = await asyncio.to_thread(analyze_paper, pm.llm_router, parsed.text, request.provider)
    candidates = await _run_discovery_queries(
        analysis, request.sources, request.max_per_query, request.metadata_db_path
    )
    return {"analysis": analysis, "candidates": candidates}


@router.post("/research/deep")
async def research_deep(request: DeepResearchRequest) -> dict[str, Any]:
    """Run a guarded web deep-research pass. Returns findings for review; nothing is
    saved until the user confirms via POST /projects/{id}/grey-sources.

    Web content is sanitized and treated as untrusted data (prompt-injection hardened).
    Results are grey sources and are never written to the knowledge graph.
    """
    return await run_deep_research(
        pm.llm_router,
        request.question,
        provider=request.provider,
        search_provider=request.search_provider,
        max_queries=request.max_queries,
        results_per_query=request.results_per_query,
        max_sources=request.max_sources,
    )


@router.post("/query/auto-answer")
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
            async for event in pm.auto_research_answer(
                question=request.question,
                llm_router=pm.llm_router,
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


@router.post("/research/tree")
async def research_tree(request: ResearchTreeRequest) -> StreamingResponse:
    """Stream a Research Tree via Server-Sent Events, persisting it server-side.

    Each node is sent as ``data: <json>\\n\\n`` when it completes.
    Node payload: {id, parent_id, question, depth, status, answer, child_count?}.

    The tree is upserted into ``research_sessions`` *as it streams* (throttled, plus
    immediately on important nodes), so a reload mid-run — or a closed browser — never
    loses the progress. The frontend later overwrites with a verification-enriched copy
    via ``PUT /research/session/{id}``.
    """
    runner = pm.ResearchTreeRunner(pm.llm_router)
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


@router.get("/research/sessions/{project_id}")
def list_research_sessions(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """List persisted deep-research sessions for a project (counts only, no payload)."""
    with MetadataDB(metadata_db_path) as db:
        return {"sessions": db.list_research_sessions(project_id)}


@router.get("/research/session/{session_id}")
def get_research_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Fetch the full node tree of one persisted deep-research session."""
    with MetadataDB(metadata_db_path) as db:
        session = db.get_research_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Research session not found")
    return {"session": session}


@router.put("/research/session/{session_id}")
def upsert_research_session(
    session_id: str, request: ResearchSessionUpsertRequest
) -> dict[str, Any]:
    """Authoritative overwrite of a session (verification-enriched copy from the client)."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.upsert_research_session(
            session_id, request.project_id, request.question, request.status, request.nodes
        )
    return {"session": session}


@router.delete("/research/session/{session_id}")
def delete_research_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_research_session(session_id)
    return {"deleted": deleted}


@router.post("/research/tree/export")
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
            llm_router=pm.llm_router,
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


@router.post("/research/tree/as-source")
def research_tree_as_source(request: ResearchTreeAsSourceRequest) -> dict[str, Any]:
    """Persist a finished Tiefenanalyse as a citable project source.

    Stored like a grey source (``source_kind="analysis"``) so the synthesis text is
    retrievable and citable as ``grey::…`` everywhere. ``source_paper_ids`` keeps the
    papers the analysis was built from, so selecting the analysis can pull its own
    sources into the context as well.
    """
    document = request.document.strip()
    if not document:
        raise HTTPException(status_code=400, detail="Keine Gesamtantwort zum Speichern vorhanden.")
    used = aggregate_sources(request.nodes, request.sources)
    paper_ids = [str(src.get("paper_id")) for src in used if src.get("paper_id")]
    source_id = f"grey_analysis_{re.sub(r'[^A-Za-z0-9_-]+', '', request.session_id or uuid.uuid4().hex)[:60]}"
    with MetadataDB(request.metadata_db_path) as db:
        saved = db.add_grey_source(request.project_id, {
            "id": source_id,
            "url": "",
            "title": f"Tiefenanalyse: {request.root_question.strip()[:160]}",
            "summary": _plain_summary(document),
            "full_text": document[:FULL_TEXT_MAX_LEN],
            "query": request.root_question.strip()[:400],
            "source_kind": "analysis",
            "origin_id": request.session_id or "",
            "source_paper_ids": paper_ids,
        })
    return {"saved": saved, "paper_count": len(paper_ids)}


def _plain_summary(document: str, limit: int = 400) -> str:
    """First readable paragraph of the synthesis, without Markdown noise/citations."""
    text = re.sub(r"\[[^\]]+\]", " ", document)
    text = re.sub(r"[#*_`>|]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:limit]


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


@router.post("/research/clarify")
async def research_clarify(request: ResearchClarifyRequest) -> dict[str, Any]:
    """Suggest 4-6 thematic focus directions to steer a deep analysis."""
    overrides: dict[str, Any] = {"max_tokens": 400, "temperature": 0.5}
    if request.model:
        overrides["model"] = request.model
    directions: list[str] = []
    try:
        text = await asyncio.to_thread(
            pm.llm_router.chat,
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
