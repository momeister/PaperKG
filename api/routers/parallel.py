"""Parallel-Research-Modus: Sessions, Varianten, Ergebnisse, Synthese, Agent-Handoff.

Split out of api/product_main.py. Behaviour unchanged.
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
    stage_id: str | None = None
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
    stage_id: str | None = None
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
    the first stage (the methods to try)."""
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_filter = pm._parallel_project_filter(project_id)
    # The stage roadmap is best-effort: on LLM failure the session still starts with a
    # single default stage carrying the question as its goal.
    stages: list[dict[str, str]] = []
    try:
        stages = await asyncio.to_thread(
            parallel_research.propose_stages,
            retriever,
            pm.llm_router,
            request.question,
            paper_ids=request.paper_ids or None,
            provider=request.provider,
            model=request.model,
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
            db.add_parallel_variant(
                session["id"],
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
                stage_id=first_stage_id,
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
    """Regenerate / add more AI variants for an existing session."""
    with MetadataDB(request.metadata_db_path) as db:
        session = db.get_parallel_session(session_id)
    if session is None:
        raise HTTPException(status_code=404, detail="Parallel session not found")
    with MetadataDB(request.metadata_db_path) as db:
        stage = _ensure_stage(db, session, request.stage_id)
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
                stage_id=str(stage["id"]),
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
        proposed = await asyncio.to_thread(
            parallel_research.propose_stages,
            retriever,
            pm.llm_router,
            str(session.get("question") or ""),
            existing_stages=session.get("stages") or [],
            paper_ids=request.paper_ids or None,
            provider=request.provider,
            model=request.model,
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
        variants = await asyncio.to_thread(
            parallel_research.propose_variants,
            retriever,
            pm.llm_router,
            composed,
            n=request.variant_count,
            paper_ids=request.paper_ids or None,
            provider=request.provider,
            model=request.model,
        )

    with MetadataDB(request.metadata_db_path) as db:
        db.add_parallel_followup(session_id, request.question, answer_payload=answer)
        stage = _ensure_stage(db, session, None) if variants else None
        for variant in variants:
            db.add_parallel_variant(
                session_id,
                name=variant["name"],
                approach=variant["approach"],
                rationale=variant["rationale"],
                suggested_prompt=variant["suggested_prompt"],
                origin="ai",
                status="vorgeschlagen",
                stage_id=str(stage["id"]) if stage else None,
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
