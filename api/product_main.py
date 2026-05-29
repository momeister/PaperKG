from __future__ import annotations

import asyncio
import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api import phase4_main
from api.main import BuildGraphRequest, build_phase2_graph
from graph.paper_ingestion import extract_citation_ids, paper_id
from harvester.arxiv_client import ArxivClient
from harvester.openalex_client import OpenAlexClient
from harvester.semantic_scholar_client import SemanticScholarClient
from quality.benchmark import run_benchmark
from quality.kg_health import build_health_report
from quality.phase4_eval import run_eval
from maintenance.health_repair import repair_health_state
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB


PROJECTS_PATH = Path("data/projects.json")
RESERVED_PROJECT_IDS = {"__all_papers__", "alle papers", "all papers"}
DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_GRAPH_DB_PATH = "data/graphs/global_kg"
DEFAULT_PDF_BASE_DIR = "data/pdfs"
DEFAULT_NOTE_ASSET_DIR = "data/note_assets"

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
    ],
    allow_origin_regex=r"https?://(localhost|127\.0\.0\.1):\d+",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(phase4_main.app.router)

llm_router = LLMRouter.from_config_file("config.yaml")


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
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


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
    query: str = "",
    project_id: str | None = None,
    has_full_text: bool | None = None,
    extraction_status: str | None = None,
    sort: str = "added_desc",
    limit: int = Query(default=50, ge=1, le=500),
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
                **paper,
                "project_ids": sorted(memberships.get(pid, [])),
                "latest_extraction_status": latest_status,
            }
        )

    filtered.sort(key=_paper_sort_key(sort), reverse=sort.endswith("_desc"))
    page = filtered[offset : offset + limit]
    return {"items": page, "total": len(filtered), "limit": limit, "offset": offset}


@app.post("/papers/upload")
async def upload_paper_pdf(
    request: Request,
    paper_id: str | None = None,
    title: str | None = None,
    source: str = "upload",
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
) -> dict[str, Any]:
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Upload body is empty.")

    filename = request.headers.get("x-filename") or title or paper_id or "uploaded-paper.pdf"
    inferred_id = paper_id or Path(filename).stem
    storage = FileManager(pdf_base_dir)
    saved_path = storage.save_pdf(
        inferred_id,
        content,
        display_name=title or Path(filename).stem,
        source=source,
    )
    with MetadataDB(metadata_db_path) as db:
        canonical_id = db.ensure_paper_record(
            inferred_id,
            title=title or Path(filename).stem,
            pdf_path=str(saved_path),
            source=source,
            source_id=inferred_id,
        )
        paper = db.get_paper(canonical_id)
    return {"paper": paper, "pdf_path": str(saved_path)}


@app.post("/harvest/search")
async def harvest_search(request: HarvestSearchRequest) -> dict[str, Any]:
    results, warnings = await _run_harvest_search(request.query, request.sources, request.max_results)
    return {"query": request.query, "results": results, "warnings": warnings}


@app.post("/harvest/download")
async def harvest_download(request: HarvestDownloadRequest) -> dict[str, Any]:
    inserted = 0
    downloaded = 0
    failed_downloads: list[str] = []
    storage = FileManager(request.pdf_base_dir)

    async with httpx.AsyncClient(timeout=60.0) as client:
        with MetadataDB(request.metadata_db_path) as db:
            for paper in request.papers:
                db.insert_paper(paper)
                inserted += 1
                if not request.download_pdfs or not paper.get("pdf_url"):
                    continue
                try:
                    response = await client.get(str(paper["pdf_url"]))
                    response.raise_for_status()
                    canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
                    saved_path = storage.save_pdf(
                        canonical_id,
                        response.content,
                        version=int(paper.get("version") or 1),
                        display_name=str(paper.get("title") or canonical_id),
                        source=str(paper.get("source") or "paper"),
                    )
                    db.update_paper_metadata_if_missing(canonical_id, pdf_path=str(saved_path))
                    downloaded += 1
                except Exception as exc:
                    failed_downloads.append(f"{paper.get('title') or paper.get('id')}: {exc}")

    return {"inserted": inserted, "downloaded": downloaded, "failed_downloads": failed_downloads}


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
    target_dir = Path(note_asset_dir) / _slug(note_id)
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
    limit: int = Query(default=80, ge=5, le=500),
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
    papers = papers[:limit]
    paper_ids = {str(paper.get("id")) for paper in papers}

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

    extraction_by_paper = _latest_successful_extractions(extractions)
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
    report = run_benchmark(
        gold_dir=Path(request.gold_dir),
        pred_dir=Path(request.pred_dir) if request.pred_dir else None,
        allow_embedded_predictions=request.allow_embedded_predictions,
    )
    return {"status": "completed", "report": report}


@app.post("/jobs/eval")
def eval_job(request: EvalJobRequest) -> dict[str, Any]:
    report = run_eval(
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        limit=request.limit,
        timeout_seconds=request.timeout_seconds,
    )
    return {"status": "completed", "report": report}


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
                "Bearbeite nur den markierten Text und den bisherigen Verlauf zu dieser Auswahl. "
                "Gib direkt Markdown zurueck. Nutze ausschliesslich bereitgestellte KG-Evidenz, "
                "wenn du neue Belege ergaenzt, und zitiere dann mit den angegebenen Paper-IDs in eckigen Klammern."
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
                f"Markierter Text:\n{selected_text.strip()}\n\n"
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


def _note_ai_model(request: NoteAiEditRequest | NoteAiMessageRequest) -> str:
    return request.model or llm_router.provider_default_model(request.provider)


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
    return Path(value) if value else PROJECTS_PATH


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


def _project_view(project_id: str, paper_ids: list[str], papers: dict[str, dict[str, Any]]) -> dict[str, Any]:
    years = [int(papers[pid]["year"]) for pid in paper_ids if pid in papers and papers[pid].get("year")]
    return {
        "id": project_id,
        "name": project_id,
        "paper_ids": paper_ids,
        "paper_count": len(paper_ids),
        "year_min": min(years) if years else None,
        "year_max": max(years) if years else None,
    }


def _is_reserved_project_id(project_id: str) -> bool:
    return project_id.strip().lower() in RESERVED_PROJECT_IDS


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
