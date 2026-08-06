"""Task-Focused Mode: Task-Specs pro Projekt (Kaggle / Hackathon / Anweisung).

Neue projektgebundene Ressource ``tasks`` (siehe ``storage/metadata_db/tasks.py``).
Ein Task-Spec kann aus URL/PDF/Freitext extrahiert und als Grey-Source zitierbar
gemacht werden (``grey::task_{id}``). Forschungsrichtungen und
Implementationspläne werden aus Spec + KG-Kontext vom LLM erzeugt — LLM-Zugriff
**immer** über ``pm.llm_router``.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons (llm_router)
from query.direction_deep_search import DirectionDeepSearchRunner
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


class TaskDeepSearchRequest(BaseModel):
    """Per-research-direction deep search — see ``POST /tasks/{id}/deep-search``."""

    direction: dict[str, Any] = Field(default_factory=dict)
    max_papers: int = Field(default=20, ge=1, le=50)
    max_web_sources: int = Field(default=10, ge=0, le=30)
    provider: str | None = None
    model: str | None = None
    creativity_level: int | None = Field(default=None, ge=1, le=5)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = "data/pdfs"
    projects_path: str = "data/projects.json"


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


# ---------------------------------------------------------------------------
# Per-direction deep search (streaming)
# ---------------------------------------------------------------------------


@router.post("/tasks/{task_id}/deep-search")
async def task_deep_search(
    task_id: str, payload: TaskDeepSearchRequest
) -> StreamingResponse:
    """Tiefensuche für eine Forschungsrichtung — streamt Fortschritt über SSE.

    Für die gegebene ``direction`` (``{label, rationale, keywords}``) werden viele
    Paper (``harvest_for_question``) und Web-Quellen (``harvest_grey_sources_for_question``
    ) geerntet, ins Projekt eingepflegt, und ein geerdetes Möglichkeitsprinzip
    (Machbarkeit / Ansätze / Risiken / Fazit) synthetisiert. Streams ``data: {json}\\n\\n``
    im selben Format wie der Research Tree, mit ``status``-Feldern pro Phase.

    Das Ergebnis (Summary + Counts + Source-IDs) wird nach Stream-Ende best-effort
    in ``task_json.deep_searches[<direction-label>]`` persistiert.
    """
    with MetadataDB(payload.metadata_db_path) as db:
        task = db.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task nicht gefunden: {task_id}")

    direction = payload.direction or {}
    label = str(direction.get("label") or "").strip()
    if not label:
        raise HTTPException(status_code=422, detail="direction.label ist erforderlich.")

    project_id = task.get("project_id")
    task_spec = task.get("task_json") or {}
    db_path = payload.metadata_db_path
    projects_path = payload.projects_path
    pdf_base_dir = payload.pdf_base_dir

    runner = DirectionDeepSearchRunner(pm.llm_router)

    async def stream() -> Any:
        final_summary: dict[str, Any] | None = None
        papers_count = 0
        grey_count = 0
        paper_ids: list[str] = []
        grey_ids: list[str] = []
        try:
            async for event in runner.stream(
                direction=direction,
                project_id=project_id,
                task_spec=task_spec,
                max_papers=payload.max_papers,
                max_web_sources=payload.max_web_sources,
                provider=payload.provider,
                model=payload.model,
                creativity_level=payload.creativity_level,
                metadata_db_path=db_path,
                pdf_base_dir=pdf_base_dir,
                projects_path=projects_path,
            ):
                yield event
                # Parse our own events to capture the final summary for persistence.
                if event.startswith("data: "):
                    try:
                        payload_evt = json.loads(event[len("data: ") :])
                    except Exception:
                        continue
                    if payload_evt.get("status") == "done":
                        final_summary = payload_evt.get("summary")
                        papers_count = int(payload_evt.get("papers_count", 0))
                        grey_count = int(payload_evt.get("grey_count", 0))
                        paper_ids = list(payload_evt.get("paper_ids") or [])
                        grey_ids = list(payload_evt.get("grey_ids") or [])
        except Exception as exc:  # noqa: BLE001 — terminal error event
            yield f"data: {json.dumps({'status': 'error', 'error': str(exc)}, ensure_ascii=False)}\n\n"
            return

        # Best-effort persist the deep-search result into task_json.deep_searches.
        if final_summary is not None:
            try:
                with MetadataDB(db_path) as db:
                    fresh = db.get_task(task_id) or {}
                    spec = dict(fresh.get("task_json") or {})
                    deep = dict(spec.get("deep_searches") or {})
                    deep[label] = {
                        "summary": final_summary,
                        "papers_count": papers_count,
                        "grey_count": grey_count,
                        "paper_ids": paper_ids,
                        "grey_ids": grey_ids,
                        "direction": direction,
                    }
                    spec["deep_searches"] = deep
                    db.update_task(task_id, task_json=spec)
            except Exception:
                # Persistence is best-effort; the client already has the result.
                pass

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "X-Task-Id": task_id,
        },
    )
