"""Parallel-Research-Modus: Sessions, Varianten, Ergebnisse, Synthese, Agent-Handoff.

Split out of api/product_main.py. Behaviour unchanged.

Task-Focused mode (Session 3): when ``task_id`` is given, the session is bound to a
Task-Spec (Kaggle-Competition / Hackathon-Ticket / eigene Anweisung); ``propose_*``
injections the spec into their prompts and each variant carries ``implementation_steps``
(the interactive "Wie umsetzen"-Steps the user accepts/rejects). ``creativity_level``
(1-5 slider) steers mainstream vs cross-domain. Varianten können abgelehnt werden
("Weg nichts für mich" → ``rejected`` + Grund); der Implementationsplan ist die Summe
aller akzeptierten Schritte (exportierbar als Notiz).
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons (llm_router, _parallel_retriever)
from api.routers.projects import (
    _attach_papers_to_project,
    _is_reserved_project_id,
    _load_projects,
)
from query import agent_handoff, parallel_research
from storage.metadata_db import MetadataDB

DEFAULT_PDF_BASE_DIR = "data/pdfs"

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_GRAPH_DB_PATH = "data/graphs/global_kg"
DEFAULT_PROJECTS_PATH = "data/projects.json"

router = APIRouter()


def _resolve_project_paper_ids(
    project_id: str | None,
    paper_ids: list[str] | None,
    projects_path: str = DEFAULT_PROJECTS_PATH,
) -> list[str] | None:
    """Defense-in-depth scope resolution for parallel-research requests.

    Frontend sends ``paper_ids`` explicitly. Older clients or direct API calls
    may omit them. When the caller is a *real* project (not the global
    ``__all_papers__`` scope) we resolve the project's paper membership from
    ``projects.json`` so the retriever never silently falls back to the global
    KG. If the real project has no papers we return ``["__none__"]`` — the
    retriever treats this as an explicit empty scope (``_paper_id_allowed``
    returns False for every real paper) rather than ``None`` (which means
    "no filter → scan everything").

    Returns ``None`` for the global scope (reserved project id or no project)
    so the retriever behaves as before — global KG stays allowed there.
    """
    if not project_id or _is_reserved_project_id(project_id):
        return None
    if paper_ids:
        return paper_ids
    projects = _load_projects(projects_path)
    project_papers = projects.get(project_id)
    if project_papers:
        return list(project_papers)
    return ["__none__"]


def _load_task_spec(db_path: str, task_id: str | None) -> dict[str, Any] | None:
    """Load a Task-Spec dict from the tasks table. None if no task_id or not found."""
    if not task_id:
        return None
    with MetadataDB(db_path) as db:
        task = db.get_task(task_id)
    if task is None:
        return None
    spec = task.get("task_json") if isinstance(task, dict) else None
    return spec if isinstance(spec, dict) else None


def _collect_cited_paper_ids(
    *answers: dict[str, Any] | None,
) -> list[str]:
    """Collect cited paper ids from one or more GroundedAnswer dicts.

    Sources per answer: ``sources`` (list of Source dicts with ``paper_id``),
    ``citation_links`` (list of dicts with ``paper_id``), and regex over the
    ``answer`` text (``[...]`` tokens filtered to source:id labels). Returns a
    de-duplicated, order-preserving list with ``#N`` evidence fragments
    stripped. Empty answers / non-dict payloads contribute nothing.
    """
    from query.grounded_helpers import _cited_paper_ids

    seen: set[str] = set()
    out: list[str] = []
    for answer in answers:
        if not isinstance(answer, dict):
            continue
        ids: set[str] = set()
        for src in answer.get("sources") or []:
            pid = src.get("paper_id") if isinstance(src, dict) else None
            if pid:
                ids.add(str(pid))
        for link in answer.get("citation_links") or []:
            pid = link.get("paper_id") if isinstance(link, dict) else None
            if pid:
                ids.add(str(pid))
        ids |= _cited_paper_ids(str(answer.get("answer") or ""))
        for raw in sorted(ids):
            clean = _strip_citation_fragment(raw)
            if clean and clean not in seen:
                seen.add(clean)
                out.append(clean)
    return out


def _strip_citation_fragment(paper_id: str) -> str:
    """Strip a ``#N`` evidence-binding fragment (mirrors papers.py helper).

    Inlined here to avoid a cross-router import cycle; the logic is trivial and
    must stay byte-identical to ``api/routers/papers.py:_strip_citation_fragment``.
    """
    raw = str(paper_id or "").strip()
    if "#" not in raw:
        return raw
    return raw.split("#", 1)[0].strip() or raw


def _auto_attach_cited(
    project_id: str | None,
    cited_ids: list[str],
    metadata_db_path: str,
    projects_path: str = DEFAULT_PROJECTS_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
) -> dict[str, Any]:
    """Attach cited papers to the project and queue PDF downloads for the missing ones.

    Returns ``{"attached": N, "downloads_queued": M}``. No-op for the global scope
    (reserved/empty project) — papers stay unattached there. Attach is a sidecar
    write (no PDF download); downloads run asynchronously so this call stays fast.
    """
    if not project_id or _is_reserved_project_id(project_id) or not cited_ids:
        return {"attached": 0, "downloads_queued": 0}
    attached_after = _attach_papers_to_project(project_id, cited_ids, projects_path)
    attached = len(attached_after) if attached_after else 0
    # Queue a PDF download for cited papers that have no local PDF yet.
    downloads_queued = 0
    try:
        from api.routers.papers import _paper_local_pdf_path
        from storage.file_manager import FileManager

        storage = FileManager(pdf_base_dir)
        to_download: list[str] = []
        with MetadataDB(metadata_db_path) as db:
            for pid in cited_ids:
                paper = db.resolve_paper(pid) or db.get_paper(pid)
                if not paper:
                    continue
                if _paper_local_pdf_path(paper, pdf_base_dir) is None:
                    to_download.append(str(paper.get("id") or pid))
        if to_download:
            downloads_queued = len(to_download)
            # Fire-and-forget: the download/extraction runs detached so the answer
            # returns immediately. Errors are logged inside the task, never raised.
            asyncio.create_task(
                _download_cited_papers(
                    to_download, metadata_db_path, pdf_base_dir, storage
                )
            )
    except Exception:
        # Attach already happened; a download failure must not poison the response.
        pass
    return {"attached": attached, "downloads_queued": downloads_queued}


async def _download_cited_papers(
    paper_ids: list[str],
    metadata_db_path: str,
    pdf_base_dir: str,
    storage: Any,
) -> None:
    """Background: download + extract cited papers that have no local PDF yet.

    Detached via ``asyncio.create_task`` from ``_auto_attach_cited``. Each paper
    is looked up, run through ``ingest_paper_record`` (download → persist path →
    synthetic extraction), and attached to the project. Failures per-paper are
    swallowed so one bad URL never blocks the rest.
    """
    from query.auto_harvester import ingest_paper_record

    headers = {"User-Agent": "ScienceKG/parallel-auto-attach (local-development)"}
    async with pm.httpx.AsyncClient(
        timeout=60.0, follow_redirects=True, headers=headers
    ) as client:
        with MetadataDB(metadata_db_path) as db:
            for pid in paper_ids:
                paper = db.resolve_paper(pid) or db.get_paper(pid)
                if not paper:
                    continue
                try:
                    await ingest_paper_record(paper, db, storage, client, extract=True)
                except Exception:
                    pass


class ParallelStartRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    variant_count: int = Field(default=3, ge=1, le=6)
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    task_id: str | None = None
    creativity_level: int | None = Field(default=None, ge=1, le=5)


class ParallelVariantCreateRequest(BaseModel):
    name: str = Field(default="Variante", max_length=400)
    approach: str = ""
    rationale: str = ""
    suggested_prompt: str = ""
    origin: str = "manual"
    stage_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    user_steps: list[dict[str, Any]] | None = None


class ParallelVariantUpdateRequest(BaseModel):
    name: str | None = None
    approach: str | None = None
    rationale: str | None = None
    suggested_prompt: str | None = None
    status: str | None = None
    position: int | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    rejection_reason: str | None = None


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
    stage_id: str | None = None
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    task_id: str | None = None
    creativity_level: int | None = Field(default=None, ge=1, le=5)


class ParallelFollowupRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    variant_count: int = Field(default=1, ge=0, le=4)
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    task_id: str | None = None
    creativity_level: int | None = Field(default=None, ge=1, le=5)


class ParallelStageCreateRequest(BaseModel):
    """Add an Etappe — manually (name required) or AI-proposed (propose=True)."""

    name: str = Field(default="", max_length=400)
    goal: str = Field(default="", max_length=2000)
    propose: bool = False
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    task_id: str | None = None
    creativity_level: int | None = Field(default=None, ge=1, le=5)


class ParallelStageUpdateRequest(BaseModel):
    name: str | None = None
    goal: str | None = None
    status: str | None = None
    position: int | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelStageReviewRequest(BaseModel):
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


class ParallelStepCreateRequest(BaseModel):
    """Add a "Wie umsetzen"-Step to a variant (user- oder AI-origin)."""

    text: str = Field(min_length=1, max_length=2000)
    rationale: str = ""
    citation: str = ""
    origin: str = "user"
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelStepUpdateRequest(BaseModel):
    """Patch a step (status change = "Das probiere ich" / "Ergebnis zeigen" / ...)."""

    text: str | None = None
    status: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelStepResultRequest(BaseModel):
    """ "Ergebnis zeigen" — upload a result for a step (becomes a parallel_entry too)."""

    result: str = Field(min_length=1, max_length=20000)
    request_feedback: bool = True
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


class ParallelRejectRequest(BaseModel):
    """ "Weg nichts für mich" — reject a variant with a reason."""

    reason: str = Field(default="", max_length=2000)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelAskProfessorRequest(BaseModel):
    """ "Frage an Professor" — a follow-up scoped to a single step/variant."""

    question: str = Field(min_length=1, max_length=2000)
    step_id: str | None = None
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


def _resolve_stage(
    session: dict[str, Any], stage_id: str | None
) -> dict[str, Any] | None:
    """Explicit stage by id, else the active stage (first ``aktiv``, else last)."""
    stages = session.get("stages") or []
    if stage_id:
        for stage in stages:
            if str(stage.get("id")) == str(stage_id):
                return stage
        return None
    for stage in stages:
        if str(stage.get("status")) == "aktiv":
            return stage
    return stages[-1] if stages else None


def _steps_to_user_steps(
    implementation_steps: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Convert LLM ``implementation_steps`` (Session 3 schema) to stored ``user_steps``.

    Each stored step: ``{id, text, rationale, citation, status, origin, result,
    created_timestamp, updated_timestamp}``. AI-origin steps start ``vorgeschlagen``."""
    out: list[dict[str, Any]] = []
    if not isinstance(implementation_steps, list):
        return out
    for item in implementation_steps:
        out.append(
            _build_step_dict(
                str(item.get("text") or "").strip(),
                str(item.get("rationale") or "").strip(),
                str(item.get("citation") or "").strip(),
                "ai",
            )
        )
    return out


def _build_step_dict(
    text: str, rationale: str = "", citation: str = "", origin: str = "user"
) -> dict[str, Any]:
    """Build a single ``user_steps`` entry (used by add_parallel_step + _steps_to_user_steps)."""
    from datetime import datetime

    import uuid

    now = datetime.now().isoformat()
    return {
        "id": f"step_{uuid.uuid4().hex}",
        "text": str(text or "").strip(),
        "rationale": str(rationale or "").strip(),
        "citation": str(citation or "").strip(),
        "status": "vorgeschlagen",
        "origin": str(origin or "user"),
        "result": None,
        "created_timestamp": now,
        "updated_timestamp": now,
    }


def _ensure_stage(
    db: MetadataDB, session: dict[str, Any], stage_id: str | None
) -> dict[str, Any]:
    """Like ``_resolve_stage`` but creates the default "Etappe 1" for stage-less
    sessions (pre-migration sessions that never had variants). 404 on unknown ids."""
    stage = _resolve_stage(session, stage_id)
    if stage is None:
        if stage_id:
            raise HTTPException(status_code=404, detail="Stage not found")
        stage = db.add_parallel_stage(
            str(session["id"]),
            "Etappe 1",
            goal=str(session.get("question") or ""),
            status="aktiv",
        )
    return stage


@router.post("/projects/{project_id}/parallel")
async def create_parallel_session(
    project_id: str, request: ParallelStartRequest
) -> dict[str, Any]:
    """Start a Parallel-Research session: persist it, plan the Etappen roadmap, generate
    the grounded overview (task explanation + how-to) and the initial AI variants for
    the first stage (the methods to try).

    Task-Focused mode: when ``task_id`` is given, the Task-Spec is loaded and injected
    into the overview/stages/variants prompts; each variant carries
    ``implementation_steps`` (persisted as ``user_steps``). ``creativity_level`` (1-5)
    steers mainstream vs cross-domain."""
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_filter = pm._parallel_project_filter(project_id)
    task_spec = _load_task_spec(request.metadata_db_path, request.task_id)
    if request.task_id and task_spec is None:
        raise HTTPException(
            status_code=404, detail=f"Task nicht gefunden: {request.task_id}"
        )
    creativity = request.creativity_level
    # Defense-in-depth: resolve paper_ids from the project when the caller
    # omitted them. Prevents a real project from silently leaking into the
    # global KG (``None`` paper_ids ⇒ retriever scans everything).
    resolved_paper_ids = _resolve_project_paper_ids(
        project_id, request.paper_ids or None
    )
    # The stage roadmap is best-effort: on LLM failure the session still starts with a
    # single default stage carrying the question as its goal.
    stages: list[dict[str, str]] = []
    try:
        stages = await asyncio.to_thread(
            parallel_research.propose_stages,
            retriever,
            pm.llm_router,
            request.question,
            existing_stages=None,
            paper_ids=resolved_paper_ids,
            provider=request.provider,
            model=request.model,
            task_spec=task_spec,
            creativity_level=creativity,
        )
    except Exception:
        stages = []
    if not stages:
        stages = [{"name": "Etappe 1", "goal": request.question}]
    # Variants (the methods to try) are the core deliverable; generate them for stage 1.
    # The overview (task explanation + how-to) is best-effort and must never block or
    # break the variants. Run sequentially — both share the same retrieval/DuckDB state.
    variants = await asyncio.to_thread(
        parallel_research.propose_variants,
        retriever,
        pm.llm_router,
        request.question,
        n=request.variant_count,
        paper_ids=resolved_paper_ids,
        provider=request.provider,
        model=request.model,
        stage=stages[0],
        task_spec=task_spec,
        creativity_level=creativity,
    )
    overview: dict[str, Any] | None = None
    try:
        overview = await asyncio.to_thread(
            parallel_research.propose_overview,
            retriever,
            pm.llm_router,
            request.question,
            paper_ids=resolved_paper_ids,
            project_id=project_filter,
            provider=request.provider,
            model=request.model,
            metadata_db_path=request.metadata_db_path,
            task_spec=task_spec,
            creativity_level=creativity,
        )
    except Exception:
        overview = None
    with MetadataDB(request.metadata_db_path) as db:
        session = db.create_parallel_session(
            project_id,
            request.question,
            task_id=request.task_id,
            creativity_level=creativity,
        )
        if overview is not None:
            db.update_parallel_session(
                session["id"],
                overview_markdown=str(overview.get("answer") or ""),
                overview_payload=overview,
            )
        first_stage_id: str | None = None
        for idx, stage in enumerate(stages):
            created = db.add_parallel_stage(
                session["id"],
                stage["name"],
                goal=stage.get("goal") or "",
                status="aktiv" if idx == 0 else "offen",
            )
            if idx == 0 and created is not None:
                first_stage_id = str(created["id"])
        for variant in variants:
            user_steps = _steps_to_user_steps(variant.get("implementation_steps"))
            db.add_parallel_variant(
                session["id"],
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
                stage_id=first_stage_id,
                user_steps=user_steps,
            )
        session = db.get_parallel_session(session["id"])
    # Auto-attach cited papers to the project + queue downloads for the missing PDFs.
    # Variants carry no sources list — collect from their text fields via regex.
    variant_texts = [
        {
            "answer": " ".join(
                str(v.get(k) or "")
                for k in ("approach", "rationale", "suggested_prompt")
            )
        }
        for v in variants
    ]
    cited_ids = _collect_cited_paper_ids(overview, *variant_texts)
    auto_attach = _auto_attach_cited(project_id, cited_ids, request.metadata_db_path)
    return {"session": session, "auto_attach": auto_attach}


@router.get("/projects/{project_id}/parallel")
def list_parallel_sessions(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"sessions": db.list_parallel_sessions(project_id)}


@router.get("/parallel/{session_id}")
def get_parallel_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    return {"session": session}


@router.delete("/parallel/{session_id}")
def delete_parallel_session(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_session(session_id)
    return {"deleted": deleted}


@router.post("/parallel/{session_id}/generate")
async def generate_parallel_variants(
    session_id: str, request: ParallelGenerateRequest
) -> dict[str, Any]:
    """Regenerate / add more AI variants for an existing session.

    Task-Focused: when ``task_id`` is given (or the session already has one), the
    Task-Spec is injected and variants carry ``implementation_steps`` → ``user_steps``.
    ``creativity_level`` falls back to the session's stored value when not supplied."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    with MetadataDB(request.metadata_db_path) as db:
        stage = _ensure_stage(db, session, request.stage_id)
    task_id = request.task_id or (session.get("task_id") if session else None)
    creativity = (
        request.creativity_level
        if request.creativity_level is not None
        else (session.get("creativity_level") if session else None)
    )
    task_spec = _load_task_spec(request.metadata_db_path, task_id)
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    resolved_paper_ids = _resolve_project_paper_ids(
        session.get("project_id") if session else None, request.paper_ids or None
    )
    variants = await asyncio.to_thread(
        parallel_research.propose_variants,
        retriever,
        pm.llm_router,
        str(session.get("question") or ""),
        n=request.variant_count,
        paper_ids=resolved_paper_ids,
        provider=request.provider,
        model=request.model,
        stage=stage,
        task_spec=task_spec,
        creativity_level=creativity,
    )
    with MetadataDB(request.metadata_db_path) as db:
        for variant in variants:
            user_steps = _steps_to_user_steps(variant.get("implementation_steps"))
            db.add_parallel_variant(
                session_id,
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
                stage_id=str(stage["id"]),
                user_steps=user_steps,
            )
        session = db.get_parallel_session(session_id)
    # Auto-attach cited papers (variants have no sources list — regex on their text).
    variant_texts = [
        {
            "answer": " ".join(
                str(v.get(k) or "")
                for k in ("approach", "rationale", "suggested_prompt")
            )
        }
        for v in variants
    ]
    cited_ids = _collect_cited_paper_ids(*variant_texts)
    auto_attach = _auto_attach_cited(
        session.get("project_id") if session else None,
        cited_ids,
        request.metadata_db_path,
    )
    return {"session": session, "auto_attach": auto_attach}


@router.post("/parallel/{session_id}/variants")
def add_parallel_variant(
    session_id: str, request: ParallelVariantCreateRequest
) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Parallel session not found")
        stage = _ensure_stage(db, session, request.stage_id)
        variant = db.add_parallel_variant(
            session_id,
            name=request.name,
            approach=request.approach,
            rationale=request.rationale,
            suggested_prompt=request.suggested_prompt,
            origin=request.origin or "manual",
            status="vorgeschlagen",
            stage_id=str(stage["id"]),
            user_steps=request.user_steps,
        )
    return {"variant": variant}


@router.post("/parallel/{session_id}/stages")
async def add_parallel_stage(
    session_id: str, request: ParallelStageCreateRequest
) -> dict[str, Any]:
    """Add Etappen: manually (name required) or AI-proposed (``propose=True``, avoids
    duplicating the already-planned stages). New stages start ``offen``."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    if request.propose:
        retriever = pm._parallel_retriever(
            request.metadata_db_path, request.graph_db_path
        )
        task_id = request.task_id or (session.get("task_id") if session else None)
        creativity = (
            request.creativity_level
            if request.creativity_level is not None
            else (session.get("creativity_level") if session else None)
        )
        task_spec = _load_task_spec(request.metadata_db_path, task_id)
        resolved_paper_ids = _resolve_project_paper_ids(
            session.get("project_id") if session else None, request.paper_ids or None
        )
        proposed = await asyncio.to_thread(
            parallel_research.propose_stages,
            retriever,
            pm.llm_router,
            str(session.get("question") or ""),
            existing_stages=session.get("stages") or [],
            paper_ids=resolved_paper_ids,
            provider=request.provider,
            model=request.model,
            task_spec=task_spec,
            creativity_level=creativity,
        )
        if not proposed:
            raise HTTPException(
                status_code=502, detail="Keine Etappen-Vorschläge erhalten"
            )
        with MetadataDB(request.metadata_db_path) as db:
            for stage in proposed:
                db.add_parallel_stage(
                    session_id,
                    stage["name"],
                    goal=stage.get("goal") or "",
                    status="offen",
                )
            session = db.get_parallel_session(session_id)
        return {"session": session}
    name = request.name.strip()
    if not name:
        raise HTTPException(status_code=400, detail="Name der Etappe fehlt")
    with MetadataDB(request.metadata_db_path) as db:
        db.add_parallel_stage(session_id, name, goal=request.goal, status="offen")
        session = db.get_parallel_session(session_id)
    return {"session": session}


@router.patch("/parallel/stages/{stage_id}")
def update_parallel_stage(
    stage_id: str, request: ParallelStageUpdateRequest
) -> dict[str, Any]:
    """Edit an Etappe. Completing one (status ``abgeschlossen``) auto-activates the
    session's next ``offen`` stage so the workflow moves forward in one call."""
    with MetadataDB(request.metadata_db_path) as db:
        stage = db.update_parallel_stage(
            stage_id,
            name=request.name,
            goal=request.goal,
            status=request.status,
            position=request.position,
        )
        if stage is None:
            raise HTTPException(status_code=404, detail="Stage not found")
        if request.status == "abgeschlossen":
            stages = db.list_parallel_stages(str(stage.get("session_id")))
            if not any(str(s.get("status")) == "aktiv" for s in stages):
                next_open = next(
                    (s for s in stages if str(s.get("status")) == "offen"), None
                )
                if next_open is not None:
                    db.update_parallel_stage(str(next_open["id"]), status="aktiv")
        session = db.get_parallel_session(str(stage.get("session_id")))
    return {"stage": stage, "session": session}


@router.delete("/parallel/stages/{stage_id}")
def delete_parallel_stage(
    stage_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        try:
            deleted = db.delete_parallel_stage(stage_id)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"deleted": deleted}


@router.post("/parallel/stages/{stage_id}/review")
async def review_parallel_stage(
    stage_id: str, request: ParallelStageReviewRequest
) -> dict[str, Any]:
    """Professor-Etappen-Review: structured critique over the stage's variants +
    results (incl. per-variant verdicts), persisted on the stage."""
    with MetadataDB(request.metadata_db_path) as db:
        stage = db.get_parallel_stage(stage_id)
        if stage is None:
            raise HTTPException(status_code=404, detail="Stage not found")
        session = db.get_parallel_session(str(stage.get("session_id")))
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    variants = [
        v
        for v in session.get("variants", [])
        if str(v.get("stage_id")) == str(stage_id)
    ]
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = pm._parallel_project_filter(session.get("project_id"))
    resolved_paper_ids = _resolve_project_paper_ids(
        session.get("project_id") if session else None, request.paper_ids or None
    )
    answer = await asyncio.to_thread(
        parallel_research.professor_review_stage,
        retriever,
        pm.llm_router,
        question=str(session.get("question") or ""),
        stage=stage,
        variants=variants,
        paper_ids=resolved_paper_ids,
        project_id=project_id,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
    )
    with MetadataDB(request.metadata_db_path) as db:
        db.update_parallel_stage(
            stage_id,
            review_markdown=str(answer.get("answer") or ""),
            review_payload=answer,
        )
        session = db.get_parallel_session(str(stage.get("session_id")))
    # Auto-attach cited papers to the project + queue downloads for the missing PDFs.
    cited_ids = _collect_cited_paper_ids(answer)
    auto_attach = _auto_attach_cited(
        session.get("project_id") if session else None,
        cited_ids,
        request.metadata_db_path,
    )
    return {"session": session, "answer": answer, "auto_attach": auto_attach}


@router.patch("/parallel/variants/{variant_id}")
def update_parallel_variant(
    variant_id: str, request: ParallelVariantUpdateRequest
) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        # "Weg nichts für mich" — rejecting a variant sets status to "abgelehnt"
        # and records the reason. A non-empty reason with status=None implies rejection.
        status = request.status
        if request.rejection_reason is not None and request.rejection_reason.strip():
            if status is None:
                status = "abgelehnt"
            else:
                # If the caller set a different status, still record the reason but
                # keep the caller's status (e.g. a manual status change unrelated to
                # rejection).
                pass
        variant = db.update_parallel_variant(
            variant_id,
            name=request.name,
            approach=request.approach,
            rationale=request.rationale,
            suggested_prompt=request.suggested_prompt,
            status=status,
            position=request.position,
            rejection_reason=(
                request.rejection_reason
                if request.rejection_reason is not None
                else None
            ),
        )
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    return {"variant": variant}


@router.post("/parallel/variants/{variant_id}/reject")
def reject_parallel_variant(
    variant_id: str, request: ParallelRejectRequest
) -> dict[str, Any]:
    """Explicit "Weg nichts für mich" endpoint: sets status ``abgelehnt`` + reason."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.update_parallel_variant(
            variant_id, status="abgelehnt", rejection_reason=request.reason or ""
        )
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    return {"variant": variant}


# --------------------------------------------------------------------------- #
# Task-Focused mode: interactive "Wie umsetzen"-Steps (Professor-Metapher)  #
# --------------------------------------------------------------------------- #


@router.post("/parallel/variants/{variant_id}/steps")
def add_parallel_step(
    variant_id: str, request: ParallelStepCreateRequest
) -> dict[str, Any]:
    """Add a step to a variant's ``user_steps``. ``origin`` is "user" (eigener Schritt)
    or "ai" (AI-vorgeschlagen). The rationale/citation fields are optional context."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        step = _build_step_dict(
            request.text, request.rationale, request.citation, request.origin
        )
        steps = list(variant.get("user_steps") or [])
        steps.append(step)
        variant = db.update_parallel_variant(variant_id, user_steps=steps)
    return {"variant": variant, "step": step}


@router.patch("/parallel/variants/{variant_id}/steps/{step_id}")
def update_parallel_step(
    variant_id: str, step_id: str, request: ParallelStepUpdateRequest
) -> dict[str, Any]:
    """Patch a step. ``status`` values: ``vorgeschlagen`` → ``in_progress`` ("Das probiere
    ich") → ``done`` (after "Ergebnis zeigen"); ``rejected`` ("dieser Schritt weg")."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.update_parallel_variant_step(
            variant_id, step_id, text=request.text, status=request.status
        )
    if variant is None:
        raise HTTPException(status_code=404, detail="Step not found")
    return {"variant": variant}


@router.delete("/parallel/variants/{variant_id}/steps/{step_id}")
def delete_parallel_step(
    variant_id: str, step_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_variant_step(variant_id, step_id)
    if not deleted:
        raise HTTPException(status_code=404, detail="Step not found")
    with MetadataDB(metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
    return {"deleted": True, "variant": variant}


@router.post("/parallel/variants/{variant_id}/steps/{step_id}/result")
async def submit_step_result(
    variant_id: str, step_id: str, request: ParallelStepResultRequest
) -> dict[str, Any]:
    """ "Ergebnis zeigen" — attach a result to a step (marks it ``done``) AND posts it as
    a ``parallel_entries`` row so it appears in the variant's Ergebnis-Thread. When
    ``request_feedback`` is set, the professor reviews the result (grounded)."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        session_id = str(variant.get("session_id"))
        session = db.get_parallel_session(session_id)
        # 1) Mark the step done + store the result text on the step.
        variant = db.update_parallel_variant_step(
            variant_id, step_id, status="done", result=request.result
        )
        if variant is None:
            raise HTTPException(status_code=404, detail="Step not found")
        # 2) Record the result as a parallel_entry (role=user).
        user_entry = db.add_parallel_entry(
            variant_id, session_id, "user", request.result
        )

    feedback_entry: dict[str, Any] | None = None
    if request.request_feedback:
        stage = None
        if variant.get("stage_id"):
            with MetadataDB(request.metadata_db_path) as db:
                stage = db.get_parallel_stage(str(variant["stage_id"]))
        retriever = pm._parallel_retriever(
            request.metadata_db_path, request.graph_db_path
        )
        project_id = pm._parallel_project_filter(
            session.get("project_id") if session else None
        )
        resolved_paper_ids = _resolve_project_paper_ids(
            session.get("project_id") if session else None, request.paper_ids or None
        )
        answer = await asyncio.to_thread(
            parallel_research.professor_review_entry,
            retriever,
            pm.llm_router,
            question=str(session.get("question") if session else ""),
            variant=variant,
            user_result=request.result,
            stage=stage,
            paper_ids=resolved_paper_ids,
            project_id=project_id,
            provider=request.provider,
            model=request.model,
            metadata_db_path=request.metadata_db_path,
        )
        with MetadataDB(request.metadata_db_path) as db:
            feedback_entry = db.add_parallel_entry(
                variant_id,
                session_id,
                "assistant",
                str(answer.get("answer") or ""),
                answer_payload=answer,
            )

    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    # Auto-attach cited papers to the project + queue downloads for the missing PDFs.
    cited_ids = _collect_cited_paper_ids(answer) if feedback_entry else []
    auto_attach = (
        _auto_attach_cited(
            session.get("project_id") if session else None,
            cited_ids,
            request.metadata_db_path,
        )
        if feedback_entry
        else {"attached": 0, "downloads_queued": 0}
    )
    return {
        "session": session,
        "user_entry": user_entry,
        "feedback_entry": feedback_entry,
        "auto_attach": auto_attach,
    }


@router.post("/parallel/variants/{variant_id}/ask")
async def ask_step_professor(
    variant_id: str, request: ParallelAskProfessorRequest
) -> dict[str, Any]:
    """ "Frage an Professor" — a follow-up scoped to a single variant (and optionally a
    single step). Uses the parallel followup path so the answer is grounded and threaded
    under the variant's entries."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        session_id = str(variant.get("session_id"))
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = pm._parallel_project_filter(session.get("project_id"))
    original_question = str(session.get("question") or "")
    step_context = ""
    if request.step_id:
        steps = list(variant.get("user_steps") or [])
        step = next(
            (s for s in steps if str(s.get("id")) == str(request.step_id)), None
        )
        if step:
            step_context = (
                f"\n\nBezieht sich auf den Schritt: {step.get('text') or ''}"
                + (
                    f"\nSchritt-Status: {step.get('status') or ''}"
                    if step.get("status")
                    else ""
                )
                + (
                    f"\nBisheriges Ergebnis: {step.get('result') or '(keins)'}"
                    if step.get("result")
                    else ""
                )
            )
    composed = (
        f"{original_question}\n\nVariante: {variant.get('name') or ''}"
        f"\nAnsatz: {variant.get('approach') or ''}{step_context}\n\nFrage: {request.question}"
    )
    resolved_paper_ids = _resolve_project_paper_ids(
        session.get("project_id"), request.paper_ids or None
    )
    answer = await asyncio.to_thread(
        parallel_research.followup_answer,
        retriever,
        pm.llm_router,
        question=request.question,
        original_question=composed,
        paper_ids=resolved_paper_ids,
        project_id=project_id,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
    )
    with MetadataDB(request.metadata_db_path) as db:
        entry = db.add_parallel_entry(
            variant_id,
            session_id,
            "assistant",
            str(answer.get("answer") or ""),
            answer_payload=answer,
        )
        session = db.get_parallel_session(session_id)
    # Auto-attach cited papers to the project + queue downloads for the missing PDFs.
    cited_ids = _collect_cited_paper_ids(answer)
    auto_attach = _auto_attach_cited(
        session.get("project_id") if session else None,
        cited_ids,
        request.metadata_db_path,
    )
    return {
        "session": session,
        "answer": answer,
        "entry": entry,
        "auto_attach": auto_attach,
    }


@router.post("/parallel/{session_id}/implementation-plan")
def export_implementation_plan(
    session_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Sum of all accepted steps across the session's variants → exportable plan
    (Session 4: export-as-note). Accepts steps with status ``in_progress``/``done``/
    ``vorgeschlagen`` (not ``rejected``)."""
    with MetadataDB(metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
        task_id = str(session.get("task_id") or "") if session else None
        task_spec = _load_task_spec(metadata_db_path, task_id) if task_id else None
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    plan = parallel_research.build_implementation_plan(session, task_spec=task_spec)
    return {"plan": plan}


@router.delete("/parallel/variants/{variant_id}")
def delete_parallel_variant(
    variant_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_variant(variant_id)
    return {"deleted": deleted}


@router.post("/parallel/variants/{variant_id}/entries")
async def add_parallel_entry(
    variant_id: str, request: ParallelEntryRequest
) -> dict[str, Any]:
    """Submit a result for a variant; optionally returns an immediate grounded assessment."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        session_id = str(variant.get("session_id"))
        session = db.get_parallel_session(session_id)
        user_entry = db.add_parallel_entry(
            variant_id, session_id, "user", request.content
        )
        db.update_parallel_variant(variant_id, status="ergebnis")

    feedback_entry: dict[str, Any] | None = None
    if request.request_feedback:
        stage = None
        if variant.get("stage_id"):
            with MetadataDB(request.metadata_db_path) as db:
                stage = db.get_parallel_stage(str(variant["stage_id"]))
        retriever = pm._parallel_retriever(
            request.metadata_db_path, request.graph_db_path
        )
        project_id = pm._parallel_project_filter(
            session.get("project_id") if session else None
        )
        resolved_paper_ids = _resolve_project_paper_ids(
            session.get("project_id") if session else None, request.paper_ids or None
        )
        answer = await asyncio.to_thread(
            parallel_research.professor_review_entry,
            retriever,
            pm.llm_router,
            question=str(session.get("question") if session else ""),
            variant=variant,
            user_result=request.content,
            stage=stage,
            paper_ids=resolved_paper_ids,
            project_id=project_id,
            provider=request.provider,
            model=request.model,
            metadata_db_path=request.metadata_db_path,
        )
        with MetadataDB(request.metadata_db_path) as db:
            feedback_entry = db.add_parallel_entry(
                variant_id,
                session_id,
                "assistant",
                str(answer.get("answer") or ""),
                answer_payload=answer,
            )

    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    # Auto-attach cited papers to the project + queue downloads for the missing PDFs.
    cited_ids = _collect_cited_paper_ids(answer) if feedback_entry else []
    auto_attach = (
        _auto_attach_cited(
            session.get("project_id") if session else None,
            cited_ids,
            request.metadata_db_path,
        )
        if feedback_entry
        else {"attached": 0, "downloads_queued": 0}
    )
    return {
        "session": session,
        "user_entry": user_entry,
        "feedback_entry": feedback_entry,
        "auto_attach": auto_attach,
    }


@router.delete("/parallel/entries/{entry_id}")
def delete_parallel_entry(
    entry_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_entry(entry_id)
    return {"deleted": deleted}


@router.post("/parallel/{session_id}/synthesize")
async def synthesize_parallel_session(
    session_id: str, request: ParallelSynthesizeRequest
) -> dict[str, Any]:
    """Cross-variant analysis → ranking + reshaped final answer, persisted on the session."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = pm._parallel_project_filter(session.get("project_id"))
    resolved_paper_ids = _resolve_project_paper_ids(
        session.get("project_id") if session else None, request.paper_ids or None
    )
    answer = await asyncio.to_thread(
        parallel_research.synthesize,
        retriever,
        pm.llm_router,
        question=str(session.get("question") or ""),
        variants=session.get("variants", []),
        stages=session.get("stages", []),
        paper_ids=resolved_paper_ids,
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
    # Auto-attach cited papers to the project + queue downloads for the missing PDFs.
    cited_ids = _collect_cited_paper_ids(answer)
    auto_attach = _auto_attach_cited(
        session.get("project_id") if session else None,
        cited_ids,
        request.metadata_db_path,
    )
    return {"session": session, "answer": answer, "auto_attach": auto_attach}


@router.post("/parallel/{session_id}/ask")
async def ask_parallel_followup(
    session_id: str, request: ParallelFollowupRequest
) -> dict[str, Any]:
    """Ask a follow-up while a parallel session is open: keep weiterfragen in the same session.

    Produces a grounded chat answer (shown threaded under the overview) AND a structured
    variant (a new "Vorschlag" appended to the Ergebnisse) — never a new session."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = pm._parallel_project_filter(session.get("project_id"))
    original_question = str(session.get("question") or "")
    resolved_paper_ids = _resolve_project_paper_ids(
        session.get("project_id") if session else None, request.paper_ids or None
    )

    answer = await asyncio.to_thread(
        parallel_research.followup_answer,
        retriever,
        pm.llm_router,
        question=request.question,
        original_question=original_question,
        paper_ids=resolved_paper_ids,
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
        task_id = request.task_id or (session.get("task_id") if session else None)
        creativity = (
            request.creativity_level
            if request.creativity_level is not None
            else (session.get("creativity_level") if session else None)
        )
        task_spec = _load_task_spec(request.metadata_db_path, task_id)
        variants = await asyncio.to_thread(
            parallel_research.propose_variants,
            retriever,
            pm.llm_router,
            composed,
            n=request.variant_count,
            paper_ids=resolved_paper_ids,
            provider=request.provider,
            model=request.model,
            task_spec=task_spec,
            creativity_level=creativity,
        )

    with MetadataDB(request.metadata_db_path) as db:
        db.add_parallel_followup(session_id, request.question, answer_payload=answer)
        stage = _ensure_stage(db, session, None) if variants else None
        for variant in variants:
            user_steps = _steps_to_user_steps(variant.get("implementation_steps"))
            db.add_parallel_variant(
                session_id,
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
                stage_id=str(stage["id"]) if stage else None,
                user_steps=user_steps,
            )
        session = db.get_parallel_session(session_id)
    # Auto-attach cited papers to the project + queue downloads for the missing PDFs.
    variant_texts = [
        {
            "answer": " ".join(
                str(v.get(k) or "")
                for k in ("approach", "rationale", "suggested_prompt")
            )
        }
        for v in variants
    ]
    cited_ids = _collect_cited_paper_ids(answer, *variant_texts)
    auto_attach = _auto_attach_cited(
        session.get("project_id") if session else None,
        cited_ids,
        request.metadata_db_path,
    )
    return {"session": session, "answer": answer, "auto_attach": auto_attach}


@router.post("/parallel/variants/{variant_id}/handoff")
async def parallel_variant_handoff(
    variant_id: str, request: AgentHandoffRequest
) -> dict[str, Any]:
    """Compile a variant into a computer-use task brief for an external desktop agent.

    Returns ``{brief, text, bridge}``. ``text`` is the copy-/POST-ready instruction —
    Kanal A: paste into UI-TARS-Desktop; Kanal B: POST to /agent/dispatch. Pure text-out;
    PaperKG never drives the machine here."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        session = db.get_parallel_session(str(variant.get("session_id")))
        stage = (
            db.get_parallel_stage(str(variant["stage_id"]))
            if variant.get("stage_id")
            else None
        )
    question = str(session.get("question") if session else "")
    retriever = None
    if request.with_research_context:
        try:
            retriever = pm._parallel_retriever(
                request.metadata_db_path, request.graph_db_path
            )
        except Exception:
            retriever = None
    resolved_paper_ids = _resolve_project_paper_ids(
        session.get("project_id") if session else None, request.paper_ids or None
    )
    brief = await asyncio.to_thread(
        agent_handoff.build_task_brief,
        variant,
        question=question,
        retriever=retriever,
        llm_router=pm.llm_router,
        paper_ids=resolved_paper_ids,
        provider=request.provider,
        model=request.model,
        stage=stage,
    )
    text = agent_handoff.render_task_brief_text(brief)
    bridge = pm._load_agent_bridge_config()
    return {
        "brief": brief,
        "text": text,
        "bridge": {
            "enabled": bool(bridge.get("enabled")),
            "type": str(bridge.get("type") or "ui_tars_desktop"),
        },
    }
