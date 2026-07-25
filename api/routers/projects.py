"""Projekte: CRUD, Papier-Zuordnung, Primary-Paper, Dashboard.

Split out of api/product_main.py. Behaviour unchanged. Die Projekt-Helfer
(_load_projects, _attach_papers_to_project, ...) sind die gemeinsame Basis
fuer papers/harvest/extraction und werden von dort importiert.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from quality.kg_health import build_health_report
from storage.atomic_json import read_json_dict, write_json_atomic
from storage.metadata_db import MetadataDB
from storage.path_safety import ensure_safe_path

logger = logging.getLogger(__name__)

PROJECTS_PATH = Path("data/projects.json")
PROJECT_PRIMARY_PATH = Path("data/project_primary.json")
PROJECT_META_PATH = Path("data/project_meta.json")
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
    pinned: bool | None = None


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
    # Die Projektliste steht vollstaendig in projects.json; die DB liefert nur die
    # Jahresspanne fuers Label. Ist sie gesperrt (zweites Backend) oder kaputt, waere
    # es fatal, deshalb die ganze Liste zu verweigern — das sah frueher aus, als
    # waeren alle Projekte geloescht. Also: degradiert ausliefern statt 500.
    papers: dict[str, dict[str, Any]] = {}
    degraded: str | None = None
    try:
        with MetadataDB(metadata_db_path) as db:
            papers = {str(paper.get("id")): paper for paper in db.list_papers(limit=50000)}
    except Exception as error:  # noqa: BLE001 - Projektliste darf daran nicht scheitern
        logger.warning("Projektliste ohne Metadaten-DB ausgeliefert: %s", error)
        degraded = str(error)
    views = [_project_view(project_id, paper_ids, papers) for project_id, paper_ids in sorted(projects.items())]
    # Angeheftete Projekte zuerst, sonst alphabetisch (die Liste ist bereits sortiert).
    views.sort(key=lambda view: not view["pinned"])
    payload: dict[str, Any] = {"projects": views}
    if degraded:
        payload["degraded"] = degraded
    return payload


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
def patch_project(
    project_id: str,
    payload: ProjectPatch,
    projects_path: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    path = _projects_path(projects_path)
    projects = _load_projects(path)
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    if _is_reserved_project_id(project_id):
        raise HTTPException(status_code=400, detail="Alle Papers is the global library mode and cannot be renamed.")

    target_id = payload.name.strip() if payload.name else project_id
    if _is_reserved_project_id(target_id):
        raise HTTPException(status_code=400, detail=f"Reserved project name: {target_id}")
    if target_id != project_id and target_id in projects:
        raise HTTPException(status_code=409, detail=f"Project already exists: {target_id}")

    paper_ids = _unique_strings(payload.paper_ids) if payload.paper_ids is not None else projects[project_id]

    if target_id != project_id:
        # Die Projekt-ID ist der Name: ohne diese Migration verlieren Notizen, Web-Quellen,
        # Sessions und Analysen beim Umbenennen ihre Projektzuordnung.
        #
        # Reihenfolge ist kritisch: erst die DB und die Sidecars migrieren, dann
        # projects.json schreiben. Andersherum hinterlaesst ein Fehler beim
        # DB-Zugriff (z.B. Lock durch ein zweites Backend) ein umbenanntes Projekt,
        # dessen grey_sources/notes noch an der alten ID haengen — dauerhaft verwaist.
        with MetadataDB(metadata_db_path) as db:
            db.rename_project(project_id, target_id)
        _migrate_project_sidecars(project_id, target_id)
        projects.pop(project_id)

    projects[target_id] = paper_ids
    _save_projects(projects, path)

    if payload.pinned is not None:
        meta = _load_project_meta()
        entry = dict(meta.get(target_id) or {})
        entry["pinned"] = bool(payload.pinned)
        meta[target_id] = entry
        _save_project_meta(meta)

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
    view = _project_view(project_id, paper_ids, {})
    meta = _load_project_meta()
    if meta.pop(project_id, None) is not None:
        _save_project_meta(meta)
    return {"deleted": True, "project": view}


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
    # read_json_dict wirft bei kaputtem JSON (CorruptJsonError) statt still {} zu
    # liefern: ein leeres Dict wuerde beim naechsten Speichern die echte Zuordnung
    # ueberschreiben und die Projekte tatsaechlich vernichten.
    data = read_json_dict(path)
    return {
        str(project_id): _unique_strings(paper_ids if isinstance(paper_ids, list) else [])
        for project_id, paper_ids in data.items()
    }


def _save_projects(projects: dict[str, list[str]], path: Path = PROJECTS_PATH) -> None:
    write_json_atomic(path, projects)


def _load_primary_papers(path: Path | None = None) -> dict[str, str]:
    # Pfad erst beim Aufruf aufloesen (nicht als Default-Argument), damit Tests
    # PROJECT_PRIMARY_PATH umbiegen koennen, statt in data/ zu schreiben.
    data = read_json_dict(path or PROJECT_PRIMARY_PATH)
    return {str(pid): str(value) for pid, value in data.items() if value}


def _save_primary_papers(mapping: dict[str, str], path: Path | None = None) -> None:
    write_json_atomic(path or PROJECT_PRIMARY_PATH, mapping)


def _load_project_meta(path: Path | None = None) -> dict[str, dict[str, Any]]:
    """Per-Projekt-UI-Metadaten (aktuell nur ``pinned``) neben projects.json."""
    data = read_json_dict(path or PROJECT_META_PATH)
    return {str(pid): dict(value) for pid, value in data.items() if isinstance(value, dict)}


def _save_project_meta(meta: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    write_json_atomic(path or PROJECT_META_PATH, meta)


def _migrate_project_sidecars(old_project_id: str, new_project_id: str) -> None:
    """Ziehe Primary-Paper und UI-Metadaten auf die neue Projekt-ID um."""
    primaries = _load_primary_papers()
    if old_project_id in primaries:
        primaries[new_project_id] = primaries.pop(old_project_id)
        _save_primary_papers(primaries)
    meta = _load_project_meta()
    if old_project_id in meta:
        meta[new_project_id] = meta.pop(old_project_id)
        _save_project_meta(meta)


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
        "pinned": bool((_load_project_meta().get(project_id) or {}).get("pinned")),
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
