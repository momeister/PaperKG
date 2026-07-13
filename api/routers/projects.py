"""Projekte: CRUD, Papier-Zuordnung, Primary-Paper, Dashboard.

Split out of api/product_main.py. Behaviour unchanged. Die Projekt-Helfer
(_load_projects, _attach_papers_to_project, ...) sind die gemeinsame Basis
fuer papers/harvest/extraction und werden von dort importiert.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from quality.kg_health import build_health_report
from storage.metadata_db import MetadataDB
from storage.path_safety import ensure_safe_path

PROJECTS_PATH = Path("data/projects.json")
PROJECT_PRIMARY_PATH = Path("data/project_primary.json")
RESERVED_PROJECT_IDS = {"__all_papers__", "alle papers", "all papers"}
DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_GRAPH_DB_PATH = "data/graphs/global_kg"
DEFAULT_PDF_BASE_DIR = "data/pdfs"

router = APIRouter()


class ProjectPayload(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    paper_ids: list[str] = []


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    paper_ids: list[str] | None = None


class ProjectPaperPayload(BaseModel):
    paper_ids: list[str]


class PrimaryPaperPayload(BaseModel):
    paper_id: str | None = None


@router.get("/projects")
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


@router.post("/projects")
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


@router.patch("/projects/{project_id}")
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


@router.delete("/projects/{project_id}")
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


@router.post("/projects/{project_id}/papers")
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


@router.delete("/projects/{project_id}/papers/{paper_id:path}")
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


@router.put("/projects/{project_id}/primary-paper")
def set_project_primary_paper(project_id: str, payload: PrimaryPaperPayload) -> dict[str, Any]:
    """Mark (or clear) the project's main source. Answers will prioritize it."""
    mapping = _load_primary_papers()
    if payload.paper_id:
        mapping[project_id] = str(payload.paper_id)
    else:
        mapping.pop(project_id, None)
    _save_primary_papers(mapping)
    return {"project_id": project_id, "primary_paper_id": mapping.get(project_id)}


@router.get("/projects/{project_id}/dashboard")
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


def _ratio(numerator: int, denominator: int) -> float:
    return round(float(numerator) / float(denominator), 4) if denominator else 0.0
