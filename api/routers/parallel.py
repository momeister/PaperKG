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
from query import agent_handoff, parallel_research
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_GRAPH_DB_PATH = "data/graphs/global_kg"

router = APIRouter()


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
    """"Ergebnis zeigen" — upload a result for a step (becomes a parallel_entry too)."""
    result: str = Field(min_length=1, max_length=20000)
    request_feedback: bool = True
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


class ParallelRejectRequest(BaseModel):
    """"Weg nichts für mich" — reject a variant with a reason."""
    reason: str = Field(default="", max_length=2000)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class ParallelAskProfessorRequest(BaseModel):
    """"Frage an Professor" — a follow-up scoped to a single step/variant."""
    question: str = Field(min_length=1, max_length=2000)
    step_id: str | None = None
    paper_ids: list[str] = Field(default_factory=list)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH


def _resolve_stage(session: dict[str, Any], stage_id: str | None) -> dict[str, Any] | None:
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
        out.append(_build_step_dict(
            str(item.get("text") or "").strip(),
            str(item.get("rationale") or "").strip(),
            str(item.get("citation") or "").strip(),
            "ai",
        ))
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
            str(session["id"]), "Etappe 1",
            goal=str(session.get("question") or ""), status="aktiv",
        )
    return stage


@router.post("/projects/{project_id}/parallel")
async def create_parallel_session(project_id: str, request: ParallelStartRequest) -> dict[str, Any]:
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
        raise HTTPException(status_code=404, detail=f"Task nicht gefunden: {request.task_id}")
    creativity = request.creativity_level
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
            paper_ids=request.paper_ids or None,
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
        paper_ids=request.paper_ids or None,
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
            paper_ids=request.paper_ids or None,
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
            project_id, request.question,
            task_id=request.task_id, creativity_level=creativity,
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
    return {"session": session}


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
async def generate_parallel_variants(session_id: str, request: ParallelGenerateRequest) -> dict[str, Any]:
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
    variants = await asyncio.to_thread(
        parallel_research.propose_variants,
        retriever,
        pm.llm_router,
        str(session.get("question") or ""),
        n=request.variant_count,
        paper_ids=request.paper_ids or None,
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
    return {"session": session}


@router.post("/parallel/{session_id}/variants")
def add_parallel_variant(session_id: str, request: ParallelVariantCreateRequest) -> dict[str, Any]:
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
async def add_parallel_stage(session_id: str, request: ParallelStageCreateRequest) -> dict[str, Any]:
    """Add Etappen: manually (name required) or AI-proposed (``propose=True``, avoids
    duplicating the already-planned stages). New stages start ``offen``."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    if request.propose:
        retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
        task_id = request.task_id or (session.get("task_id") if session else None)
        creativity = (
            request.creativity_level
            if request.creativity_level is not None
            else (session.get("creativity_level") if session else None)
        )
        task_spec = _load_task_spec(request.metadata_db_path, task_id)
        proposed = await asyncio.to_thread(
            parallel_research.propose_stages,
            retriever,
            pm.llm_router,
            str(session.get("question") or ""),
            existing_stages=session.get("stages") or [],
            paper_ids=request.paper_ids or None,
            provider=request.provider,
            model=request.model,
            task_spec=task_spec,
            creativity_level=creativity,
        )
        if not proposed:
            raise HTTPException(status_code=502, detail="Keine Etappen-Vorschläge erhalten")
        with MetadataDB(request.metadata_db_path) as db:
            for stage in proposed:
                db.add_parallel_stage(
                    session_id, stage["name"], goal=stage.get("goal") or "", status="offen"
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
def update_parallel_stage(stage_id: str, request: ParallelStageUpdateRequest) -> dict[str, Any]:
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
                next_open = next((s for s in stages if str(s.get("status")) == "offen"), None)
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
async def review_parallel_stage(stage_id: str, request: ParallelStageReviewRequest) -> dict[str, Any]:
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
        v for v in session.get("variants", []) if str(v.get("stage_id")) == str(stage_id)
    ]
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = pm._parallel_project_filter(session.get("project_id"))
    answer = await asyncio.to_thread(
        parallel_research.professor_review_stage,
        retriever,
        pm.llm_router,
        question=str(session.get("question") or ""),
        stage=stage,
        variants=variants,
        paper_ids=request.paper_ids or None,
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
    return {"session": session, "answer": answer}


@router.patch("/parallel/variants/{variant_id}")
def update_parallel_variant(variant_id: str, request: ParallelVariantUpdateRequest) -> dict[str, Any]:
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
            rejection_reason=request.rejection_reason if request.rejection_reason is not None else None,
        )
    if variant is None:
        raise HTTPException(status_code=404, detail="Variant not found")
    return {"variant": variant}


@router.post("/parallel/variants/{variant_id}/reject")
def reject_parallel_variant(variant_id: str, request: ParallelRejectRequest) -> dict[str, Any]:
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
def add_parallel_step(variant_id: str, request: ParallelStepCreateRequest) -> dict[str, Any]:
    """Add a step to a variant's ``user_steps``. ``origin`` is "user" (eigener Schritt)
    or "ai" (AI-vorgeschlagen). The rationale/citation fields are optional context."""
    with MetadataDB(request.metadata_db_path) as db:
        variant = db.get_parallel_variant(variant_id)
        if variant is None:
            raise HTTPException(status_code=404, detail="Variant not found")
        step = _build_step_dict(request.text, request.rationale, request.citation, request.origin)
        steps = list(variant.get("user_steps") or [])
        steps.append(step)
        variant = db.update_parallel_variant(variant_id, user_steps=steps)
    return {"variant": variant, "step": step}


@router.patch("/parallel/variants/{variant_id}/steps/{step_id}")
def update_parallel_step(variant_id: str, step_id: str, request: ParallelStepUpdateRequest) -> dict[str, Any]:
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
    """"Ergebnis zeigen" — attach a result to a step (marks it ``done``) AND posts it as
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
        user_entry = db.add_parallel_entry(variant_id, session_id, "user", request.result)

    feedback_entry: dict[str, Any] | None = None
    if request.request_feedback:
        stage = None
        if variant.get("stage_id"):
            with MetadataDB(request.metadata_db_path) as db:
                stage = db.get_parallel_stage(str(variant["stage_id"]))
        retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
        project_id = pm._parallel_project_filter(session.get("project_id") if session else None)
        answer = await asyncio.to_thread(
            parallel_research.professor_review_entry,
            retriever,
            pm.llm_router,
            question=str(session.get("question") if session else ""),
            variant=variant,
            user_result=request.result,
            stage=stage,
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


@router.post("/parallel/variants/{variant_id}/ask")
async def ask_step_professor(
    variant_id: str, request: ParallelAskProfessorRequest
) -> dict[str, Any]:
    """"Frage an Professor" — a follow-up scoped to a single variant (and optionally a
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
        step = next((s for s in steps if str(s.get("id")) == str(request.step_id)), None)
        if step:
            step_context = (
                f"\n\nBezieht sich auf den Schritt: {step.get('text') or ''}"
                + (f"\nSchritt-Status: {step.get('status') or ''}" if step.get("status") else "")
                + (f"\nBisheriges Ergebnis: {step.get('result') or '(keins)'}" if step.get("result") else "")
            )
    composed = (
        f"{original_question}\n\nVariante: {variant.get('name') or ''}"
        f"\nAnsatz: {variant.get('approach') or ''}{step_context}\n\nFrage: {request.question}"
    )
    answer = await asyncio.to_thread(
        parallel_research.followup_answer,
        retriever,
        pm.llm_router,
        question=request.question,
        original_question=composed,
        paper_ids=request.paper_ids or None,
        project_id=project_id,
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
    )
    with MetadataDB(request.metadata_db_path) as db:
        entry = db.add_parallel_entry(
            variant_id, session_id, "assistant",
            str(answer.get("answer") or ""), answer_payload=answer,
        )
        session = db.get_parallel_session(session_id)
    return {"session": session, "answer": answer, "entry": entry}


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
        stage = None
        if variant.get("stage_id"):
            with MetadataDB(request.metadata_db_path) as db:
                stage = db.get_parallel_stage(str(variant["stage_id"]))
        retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
        project_id = pm._parallel_project_filter(session.get("project_id") if session else None)
        answer = await asyncio.to_thread(
            parallel_research.professor_review_entry,
            retriever,
            pm.llm_router,
            question=str(session.get("question") if session else ""),
            variant=variant,
            user_result=request.content,
            stage=stage,
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


@router.delete("/parallel/entries/{entry_id}")
def delete_parallel_entry(
    entry_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_parallel_entry(entry_id)
    return {"deleted": deleted}


@router.post("/parallel/{session_id}/synthesize")
async def synthesize_parallel_session(session_id: str, request: ParallelSynthesizeRequest) -> dict[str, Any]:
    """Cross-variant analysis → ranking + reshaped final answer, persisted on the session."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_id = pm._parallel_project_filter(session.get("project_id"))
    answer = await asyncio.to_thread(
        parallel_research.synthesize,
        retriever,
        pm.llm_router,
        question=str(session.get("question") or ""),
        variants=session.get("variants", []),
        stages=session.get("stages", []),
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


@router.post("/parallel/{session_id}/ask")
async def ask_parallel_followup(session_id: str, request: ParallelFollowupRequest) -> dict[str, Any]:
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

    answer = await asyncio.to_thread(
        parallel_research.followup_answer,
        retriever,
        pm.llm_router,
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
            paper_ids=request.paper_ids or None,
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
    return {"session": session, "answer": answer}


@router.post("/parallel/variants/{variant_id}/handoff")
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
        stage = (
            db.get_parallel_stage(str(variant["stage_id"]))
            if variant.get("stage_id")
            else None
        )
    question = str(session.get("question") if session else "")
    retriever = None
    if request.with_research_context:
        try:
            retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
        except Exception:
            retriever = None
    brief = await asyncio.to_thread(
        agent_handoff.build_task_brief,
        variant,
        question=question,
        retriever=retriever,
        llm_router=pm.llm_router,
        paper_ids=request.paper_ids or None,
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
