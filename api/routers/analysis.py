"""Analyse-Werkstatt (WP1): reproduzierbare, provenance-tragende Skript-Laeufe.

Split out of api/product_main.py. Behaviour unchanged.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml
from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons (llm_router, workspace_manager)
from analysis import (
    runner as analysis_runner,
    service as analysis_service,
    verify as analysis_verify,
)
from storage.metadata_db import MetadataDB
from workspace.manager import WorkspaceError

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


class AnalysisRunRequest(BaseModel):
    """Start a reproducible analysis run (AI writes + executes a Python script)."""

    request: str = Field(min_length=1, max_length=4000)
    project_id: str | None = None
    provider: str | None = None
    model: str | None = None
    paper_ids: list[str] = Field(default_factory=list)
    dataset_ids: list[str] = Field(default_factory=list)
    context: str | None = Field(default=None, max_length=8000)
    #: Werkstatt-Projekt, dessen Code-Graph als Kontext dazukommt — für Fragen
    #: über ein Repository selbst („zeichne die Komplexitätsverteilung").
    code_project_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class AnalysisReviseRequest(BaseModel):
    """Revise an existing run in place (new instruction and/or figure annotation)."""

    request: str | None = Field(default=None, max_length=4000)
    annotation: str | None = Field(default=None, max_length=4000)
    provider: str | None = None
    model: str | None = None
    context: str | None = Field(default=None, max_length=8000)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


def _analysis_defaults() -> dict[str, Any]:
    """Read the ``analysis:`` block from config.yaml (with safe fallbacks)."""
    try:
        with open("config.yaml", "r", encoding="utf-8") as fh:
            section = (yaml.safe_load(fh) or {}).get("analysis", {}) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        section = {}
    return {
        "timeout_seconds": float(
            section.get("timeout_seconds", analysis_runner.DEFAULT_TIMEOUT_SECONDS)
        ),
        "seed": int(section.get("seed", analysis_runner.DEFAULT_SEED)),
    }


def _code_graph_context(db: MetadataDB, code_project_id: str | None) -> str | None:
    """Kennzahlen und wichtigste Symbole eines Repositories als Planer-Kontext.

    Damit wird das Repository selbst zum Gegenstand einer Analyse („zeichne die
    Komplexitätsverteilung dieses Projekts"). Bewusst nur der Überblick und
    nicht der ganze Graph: der Planer schreibt ein Skript, er soll nicht den
    Code lesen.

    Fail-soft: ohne gebautes ``cs``-Binary oder ohne Index gibt es hier eben
    keinen Zusatz, und der Lauf passiert trotzdem.
    """
    if not code_project_id:
        return None
    try:
        from codegraph import service as code_service

        project = db.get_code_project(str(code_project_id))
        if project is None:
            return None
        overview = code_service.query(project, "overview", {}) or {}
    except Exception:  # noqa: BLE001 — Zusatzkontext, kein Muss
        return None

    stats = overview.get("stats") or {}
    lines = [
        f"Code-Projekt: {project.get('name')} ({project.get('path')})",
        "Kennzahlen: "
        + ", ".join(
            f"{key}={stats.get(key, 0)}"
            for key in (
                "files",
                "parsed_files",
                "nodes",
                "edges",
                "guessed_edges",
                "dynamic_gaps",
            )
        ),
    ]
    important = overview.get("important") or []
    if important:
        lines.append(
            "Wichtigste Symbole: "
            + ", ".join(
                f"{item.get('qualified')} ({item.get('path')})"
                for item in important[:10]
            )
        )
    hot = overview.get("hot_files") or []
    if hot:
        lines.append(
            "Häufig geänderte Dateien: "
            + ", ".join(
                f"{item.get('path')} ({item.get('churn')}×)" for item in hot[:10]
            )
        )
    return "\n".join(lines)


def _analysis_context(
    db: MetadataDB,
    paper_ids: list[str],
    dataset_ids: list[str],
    extra: str | None,
    code_project_id: str | None = None,
) -> str | None:
    """Build a compact fachlicher-context block from cited papers, datasets + free text."""
    parts: list[str] = []
    code_block = _code_graph_context(db, code_project_id)
    if code_block:
        parts.append(code_block)
    for pid in (paper_ids or [])[:12]:
        try:
            paper = db.get_paper(pid)
        except Exception:
            paper = None
        if not paper:
            continue
        title = str(paper.get("title") or pid)
        year = paper.get("year") or ""
        abstract = str(paper.get("abstract") or "")[:400]
        parts.append(f"[{pid}] {title} ({year})\n{abstract}".strip())
    for did in (dataset_ids or [])[:12]:
        try:
            ds = db.get_dataset(did)
        except Exception:
            ds = None
        if not ds:
            continue
        title = str(ds.get("title") or did)
        url = ds.get("url") or ds.get("doi") or ""
        desc = str(ds.get("description") or "")[:300]
        parts.append(f"Datensatz: {title} ({ds.get('source')}) {url}\n{desc}".strip())
    if extra and extra.strip():
        parts.append(extra.strip())
    return "\n\n".join(parts) if parts else None


def _analysis_run_response(run: dict[str, Any]) -> dict[str, Any]:
    """Attach a download URL to each artifact so the frontend can render/link it."""
    out = dict(run)
    arts = []
    for art in run.get("artifacts") or []:
        art = dict(art)
        art["url"] = f"/analysis/artifacts/{art.get('id')}"
        arts.append(art)
    out["artifacts"] = arts
    return out


@router.post("/analysis/runs")
def create_analysis_run(request: AnalysisRunRequest) -> dict[str, Any]:
    """Plan, execute and persist a reproducible analysis run. Returns run + artifacts."""
    defaults = _analysis_defaults()
    with MetadataDB(request.metadata_db_path) as db:
        context = _analysis_context(
            db,
            request.paper_ids,
            request.dataset_ids,
            request.context,
            request.code_project_id,
        )
        try:
            run = analysis_service.create_run(
                db,
                pm.llm_router,
                request=request.request,
                project_id=request.project_id,
                provider=request.provider,
                model=request.model,
                seed=defaults["seed"],
                timeout=defaults["timeout_seconds"],
                context=context,
            )
        except WorkspaceError as exc:
            raise HTTPException(status_code=400, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"Planer-Fehler: {exc}")
    return {"run": _analysis_run_response(run)}


@router.get("/analysis/runs")
def list_analysis_runs(
    project_id: str | None = None, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        runs = db.list_analysis_runs(project_id)
    return {"runs": runs}


@router.get("/analysis/runs/{run_id}")
def get_analysis_run(
    run_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        run = db.get_analysis_run(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Analyse-Lauf nicht gefunden")
    return {"run": _analysis_run_response(run)}


@router.post("/analysis/runs/{run_id}/revise")
def revise_analysis_run(run_id: str, request: AnalysisReviseRequest) -> dict[str, Any]:
    """Revise a run in place (new instruction and/or annotation) → new git version."""
    defaults = _analysis_defaults()
    with MetadataDB(request.metadata_db_path) as db:
        try:
            run = analysis_service.revise_run(
                db,
                pm.llm_router,
                run_id,
                request=request.request,
                annotation=request.annotation,
                provider=request.provider,
                model=request.model,
                timeout=defaults["timeout_seconds"],
                context=request.context,
            )
        except ValueError as exc:
            raise HTTPException(status_code=502, detail=f"Planer-Fehler: {exc}")
    if run is None:
        raise HTTPException(status_code=404, detail="Analyse-Lauf nicht gefunden")
    return {"run": _analysis_run_response(run)}


@router.post("/analysis/runs/{run_id}/verify")
def verify_analysis_run(
    run_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Reproduzierbarkeits-Check: re-run the committed script and compare output hash."""
    with MetadataDB(metadata_db_path) as db:
        result = analysis_verify.verify_run(db, run_id)
    if result is None:
        raise HTTPException(status_code=404, detail="Analyse-Lauf nicht gefunden")
    return {"verification": result}


@router.delete("/analysis/runs/{run_id}")
def delete_analysis_run(
    run_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Unregister a run (DB rows only). The on-disk folder is left for the Werkstatt."""
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_analysis_run(run_id)
    return {"deleted": deleted, "id": run_id}


@router.get("/analysis/artifacts/{artifact_id}")
def get_analysis_artifact(
    artifact_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> FileResponse:
    """Serve one generated artifact file (figure/table/data/log), path-safety guarded."""
    with MetadataDB(metadata_db_path) as db:
        art = db.get_analysis_artifact(artifact_id)
        if art is None:
            raise HTTPException(status_code=404, detail="Artefakt nicht gefunden")
        run = db.get_analysis_run(str(art.get("run_id")))
    if run is None:
        raise HTTPException(status_code=404, detail="Analyse-Lauf nicht gefunden")
    run_dir = Path(str(run.get("run_dir")))
    # Containment against the run folder (the security boundary) — same model as the
    # Werkstatt file routes. NOT ensure_safe_path: run folders live under the managed
    # workspace base dir (~/Documents/PaperKG-Projekte), outside the project/data tree.
    try:
        target = pm.workspace_manager.resolve_within(
            run_dir, str(art.get("rel_path")), must_exist=True
        )
    except WorkspaceError:
        raise HTTPException(status_code=400, detail="Ungültiger Artefakt-Pfad")
    if not target.is_file():
        raise HTTPException(
            status_code=404, detail="Artefakt-Datei fehlt auf der Platte"
        )
    return FileResponse(str(target), filename=str(art.get("filename") or target.name))
