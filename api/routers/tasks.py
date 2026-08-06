"""Task-Focused Mode: Task-Specs pro Projekt (Kaggle / Hackathon / Anweisung).

Neue projektgebundene Ressource ``tasks`` (siehe ``storage/metadata_db/tasks.py``).
Ein Task-Spec kann aus URL/PDF/Freitext extrahiert und als Grey-Source zitierbar
gemacht werden (``grey::task_{id}``). Forschungsrichtungen und
Implementationspläne werden aus Spec + KG-Kontext vom LLM erzeugt — LLM-Zugriff
**immer** über ``pm.llm_router``.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons (llm_router)
from query.task_extractor import extract_task_spec
from query.task_implementation_planner import build_implementation_plan
from query.task_research_suggester import suggest_research_directions
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


# ---------------------------------------------------------------------------
# Request models
# ---------------------------------------------------------------------------


class TaskCreateRequest(BaseModel):
    title: str = Field(default="", max_length=400)
    task_json: dict[str, Any] = Field(default_factory=dict)
    source_kind: str = Field(default="text")
    source_url: str | None = None
    source_pdf_path: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class TaskExtractRequest(BaseModel):
    source_kind: str = Field(default="text")  # 'url' | 'pdf' | 'text'
    source_url: str | None = None
    source_text: str | None = None
    source_pdf_path: str | None = None
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class TaskExtractAndSaveRequest(BaseModel):
    source_kind: str = Field(default="text")  # 'url' | 'pdf' | 'text'
    source_url: str | None = None
    source_text: str | None = None
    source_pdf_path: str | None = None
    title: str | None = None
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class TaskUpdateRequest(BaseModel):
    title: str | None = None
    task_json: dict[str, Any] | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class TaskSuggestDirectionsRequest(BaseModel):
    creativity_level: int = Field(default=3, ge=1, le=5)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class TaskPlanRequest(BaseModel):
    direction: dict[str, Any] = Field(default_factory=dict)
    kg_context: str = ""
    creativity_level: int = Field(default=3, ge=1, le=5)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class TaskPublishAsGreySourceRequest(BaseModel):
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


@router.get("/projects/{project_id}/tasks")
def list_project_tasks(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        items = db.list_tasks(project_id, limit=100)
    return {"project_id": project_id, "tasks": items}


@router.get("/tasks/{task_id}")
def get_task(
    task_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task nicht gefunden: {task_id}")
    return task


@router.post("/projects/{project_id}/tasks")
def create_task(project_id: str, payload: TaskCreateRequest) -> dict[str, Any]:
    with MetadataDB(payload.metadata_db_path) as db:
        task = db.create_task(
            project_id=project_id,
            title=payload.title or "Aufgabe",
            task_json=payload.task_json or {},
            source_kind=payload.source_kind,
            source_url=payload.source_url,
            source_pdf_path=payload.source_pdf_path,
        )
    return task


@router.patch("/tasks/{task_id}")
def update_task(task_id: str, payload: TaskUpdateRequest) -> dict[str, Any]:
    with MetadataDB(payload.metadata_db_path) as db:
        task = db.update_task(task_id, title=payload.title, task_json=payload.task_json)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task nicht gefunden: {task_id}")
    return task


@router.delete("/tasks/{task_id}")
def delete_task(
    task_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_task(task_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Task nicht gefunden: {task_id}")
    return {"deleted": True, "id": task_id}


# ---------------------------------------------------------------------------
# LLM-Extraktion + Vorschläge + Plan
# ---------------------------------------------------------------------------


@router.post("/tasks/extract")
async def extract_task(payload: TaskExtractRequest) -> dict[str, Any]:
    """Task-Spec aus URL/PDF/Freitext extrahieren (ohne Speichern).

    Fetch + Parse + LLM laufen geblockt in ``asyncio.to_thread`` damit der
    Event-Loop nicht eingefroren wird. SSRF-Guard und PDF-Kind-Schutz passieren
    innerhalb ``extract_task_spec``.
    """
    spec = await asyncio.to_thread(
        extract_task_spec,
        pm.llm_router,
        payload.source_kind,
        payload.source_url,
        payload.source_text,
        payload.source_pdf_path,
        payload.provider,
        payload.model,
    )
    if payload.title and not spec.get("title"):
        spec["title"] = payload.title
    return spec


@router.post("/projects/{project_id}/tasks/ingest")
async def ingest_task(
    project_id: str, payload: TaskExtractAndSaveRequest
) -> dict[str, Any]:
    """Extrahieren + direkt in der DB speichern. Gibt den gespeicherten Task zurück."""
    spec = await asyncio.to_thread(
        extract_task_spec,
        pm.llm_router,
        payload.source_kind,
        payload.source_url,
        payload.source_text,
        payload.source_pdf_path,
        payload.provider,
        payload.model,
    )
    title = spec.get("title") or payload.title or "Aufgabe"
    with MetadataDB(payload.metadata_db_path) as db:
        task = db.create_task(
            project_id=project_id,
            title=title,
            task_json=spec,
            source_kind=payload.source_kind,
            source_url=spec.get("source_url") or payload.source_url,
            source_pdf_path=spec.get("source_pdf_path") or payload.source_pdf_path,
        )
    return task


@router.post("/tasks/{task_id}/suggest-directions")
def task_suggest_directions(
    task_id: str, payload: TaskSuggestDirectionsRequest
) -> dict[str, Any]:
    with MetadataDB(payload.metadata_db_path) as db:
        task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task nicht gefunden: {task_id}")
    spec = task.get("task_json") or {}
    return suggest_research_directions(
        llm_router=pm.llm_router,
        task_spec=spec,
        project_id=task.get("project_id"),
        metadata_db_path=payload.metadata_db_path,
        provider=payload.provider,
        model=payload.model,
        creativity_level=payload.creativity_level,
    )


@router.post("/tasks/{task_id}/plan")
def task_plan(task_id: str, payload: TaskPlanRequest) -> dict[str, Any]:
    with MetadataDB(payload.metadata_db_path) as db:
        task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task nicht gefunden: {task_id}")
    spec = task.get("task_json") or {}
    return build_implementation_plan(
        llm_router=pm.llm_router,
        task_spec=spec,
        direction=payload.direction,
        kg_context=payload.kg_context,
        provider=payload.provider,
        model=payload.model,
        creativity_level=payload.creativity_level,
    )


@router.post("/tasks/{task_id}/as-grey-source")
def publish_task_as_grey_source(
    task_id: str, payload: TaskPublishAsGreySourceRequest
) -> dict[str, Any]:
    """Macht den Task-Spec als Grey-Source zitierbar (``grey::task_{id}``).

    Die Grey-Source bekommt ``source_kind='task'`` und ``origin_id`` = task_id,
    so dass die Tiefenanalyse und der Assistant den Spec als Kontext heranziehen
    können, ohne ihn neu zu extrahieren.
    """
    with MetadataDB(payload.metadata_db_path) as db:
        task = db.get_task(task_id)
        if task is None:
            raise HTTPException(
                status_code=404, detail=f"Task nicht gefunden: {task_id}"
            )
        spec = task.get("task_json") or {}
        project_id = str(task.get("project_id") or "")
        title = str(task.get("title") or "Task-Spec")
        objective = str(spec.get("objective") or "")
        summary = objective or title
        full_text_parts = [f"# {title}"]
        if objective:
            full_text_parts.append(f"## Ziel\n{objective}")
        eval_text = str(spec.get("evaluation") or "")
        if eval_text:
            full_text_parts.append(f"## Bewertung\n{eval_text}")
        rules = spec.get("rules") or []
        if rules:
            full_text_parts.append("## Regeln\n" + "\n".join(f"- {r}" for r in rules))
        directions = spec.get("suggested_directions") or []
        if directions:
            lines = [
                f"- {d.get('label') or '?'}" for d in directions if isinstance(d, dict)
            ]
            full_text_parts.append("## Vorgeschlagene Richtungen\n" + "\n".join(lines))
        record = {
            "id": f"grey_{task_id}",
            "url": task.get("source_url") or "",
            "title": title,
            "summary": summary,
            "full_text": "\n\n".join(full_text_parts),
            "source_kind": "task",
            "origin_id": task_id,
            "status": "saved",
            "trust_tier": "trusted",
            "injection_flags": [],
            "evidence": [],
        }
        saved = db.add_grey_source(project_id, record)
    return {"task_id": task_id, "grey_source": saved, "citation": f"grey::{task_id}"}
