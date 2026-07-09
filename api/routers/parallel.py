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


@router.post("/projects/{project_id}/parallel")
async def create_parallel_session(project_id: str, request: ParallelStartRequest) -> dict[str, Any]:
    """Start a Parallel-Research session: persist it, generate the grounded overview
    (task explanation + how-to) and the initial AI variants (the methods to try)."""
    retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
    project_filter = pm._parallel_project_filter(project_id)
    # Variants (the methods to try) are the core deliverable; generate them first. The overview
    # (task explanation + how-to) is best-effort and must never block or break the variants.
    # Run sequentially — both share the same retrieval/DuckDB state.
    variants = await asyncio.to_thread(
        parallel_research.propose_variants,
        retriever,
        pm.llm_router,
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


@router.post("/parallel/{session_id}/variants")
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
        retriever = pm._parallel_retriever(request.metadata_db_path, request.graph_db_path)
        project_id = pm._parallel_project_filter(session.get("project_id") if session else None)
        answer = await asyncio.to_thread(
            parallel_research.feedback_for_entry,
            retriever,
            pm.llm_router,
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
