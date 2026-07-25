"""Projekt-Bundles: Export als ZIP-Download, Import mit Vorschau.

Ein Bundle nimmt ein Projekt samt Papern, Extraktionsergebnissen, Grauquellen und
Embeddings mit — auf einen anderen Rechner oder als Sicherung. Der Kuzu-Graph wird
bewusst *nicht* mitexportiert: er ist ein Cache und wird nach dem Import mit
``POST /jobs/graph-rebuild`` neu erzeugt.
"""
from __future__ import annotations

import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from api.routers.projects import (
    _is_reserved_project_id,
    _load_project_meta,
    _load_primary_papers,
    _load_projects,
    _projects_path,
    _save_project_meta,
    _save_primary_papers,
    _save_projects,
)
from graph_bundle import BundleError, export_project, import_bundle, preview_bundle
from storage.path_safety import ensure_safe_path

logger = logging.getLogger(__name__)

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_PDF_BASE_DIR = "data/pdfs"
DEFAULT_EXPORT_DIR = "data/exports"

#: Ein Bundle mit PDFs kann gross werden; oberhalb davon ist etwas faul.
MAX_UPLOAD_BYTES = 4 * 1024 * 1024 * 1024

router = APIRouter()


class BundleImportRequest(BaseModel):
    mode: str = Field(default="merge", pattern="^(merge|replace)$")
    target_project: str | None = Field(default=None, max_length=120)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


def _project_members(project_id: str, projects_path: str | None) -> tuple[dict[str, list[str]], list[str]]:
    projects = _load_projects(_projects_path(projects_path))
    if _is_reserved_project_id(project_id):
        # "Alle Papers" ist kein echtes Projekt: die Vereinigung aller Mitglieder.
        members: list[str] = []
        for ids in projects.values():
            for pid in ids:
                if pid not in members:
                    members.append(pid)
        return projects, members
    if project_id not in projects:
        raise HTTPException(status_code=404, detail=f"Project not found: {project_id}")
    return projects, projects[project_id]


@router.get("/projects/{project_id}/export")
def export_project_bundle(
    project_id: str,
    include_pdfs: bool = False,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    export_dir: str = DEFAULT_EXPORT_DIR,
    projects_path: str | None = None,
) -> FileResponse:
    _projects, member_ids = _project_members(project_id, projects_path)
    try:
        bundle = export_project(
            project_id,
            paper_ids=member_ids,
            metadata_db_path=metadata_db_path,
            output_dir=ensure_safe_path(export_dir, what="export directory"),
            primary_paper_id=_load_primary_papers().get(project_id),
            pinned=bool((_load_project_meta().get(project_id) or {}).get("pinned")),
            include_pdfs=include_pdfs,
            pdf_base_dir=pdf_base_dir,
        )
    except BundleError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    return FileResponse(
        path=bundle,
        media_type="application/zip",
        filename=bundle.name,
        headers={"X-Export-Format": "paperkg-bundle"},
    )


async def _store_upload(request: Request) -> Path:
    """Nimm den ZIP-Rohkoerper entgegen und lege ihn in einen Temp-Ordner."""
    temp_dir = Path(tempfile.mkdtemp(prefix="paperkg-bundle-"))
    target = temp_dir / "bundle.zip"
    total = 0
    with open(target, "wb") as sink:
        async for chunk in request.stream():
            total += len(chunk)
            if total > MAX_UPLOAD_BYTES:
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise HTTPException(status_code=413, detail="Bundle ist groesser als 4 GB.")
            sink.write(chunk)
    if not total:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise HTTPException(status_code=400, detail="Leerer Upload — bitte eine ZIP-Datei senden.")
    return target


@router.post("/bundles/preview")
async def preview_bundle_upload(
    request: Request,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    projects_path: str | None = None,
) -> dict[str, Any]:
    """Dry-Run: sagen, was drin ist und was ein Import aendern wuerde."""
    bundle = await _store_upload(request)
    try:
        preview = preview_bundle(
            bundle,
            metadata_db_path=metadata_db_path,
            existing_projects=_load_projects(_projects_path(projects_path)),
        )
    except BundleError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        shutil.rmtree(bundle.parent, ignore_errors=True)
    return {"preview": preview.as_dict()}


@router.post("/bundles/import")
async def import_bundle_upload(
    request: Request,
    mode: str = "merge",
    target_project: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    projects_path: str | None = None,
) -> dict[str, Any]:
    if mode not in {"merge", "replace"}:
        raise HTTPException(status_code=400, detail="mode muss 'merge' oder 'replace' sein.")
    if target_project and _is_reserved_project_id(target_project):
        raise HTTPException(status_code=400, detail="„Alle Papers“ ist kein echtes Projekt und kann kein Ziel sein.")

    bundle = await _store_upload(request)
    path = _projects_path(projects_path)
    try:
        report, projects, sidecars = import_bundle(
            bundle,
            metadata_db_path=metadata_db_path,
            existing_projects=_load_projects(path),
            mode=mode,  # type: ignore[arg-type]
            target_project=target_project,
            pdf_base_dir=pdf_base_dir,
        )
    except BundleError as error:
        raise HTTPException(status_code=400, detail=str(error)) from error
    finally:
        shutil.rmtree(bundle.parent, ignore_errors=True)

    # Erst jetzt projects.json anfassen — und ueber denselben atomaren Helfer wie
    # jede andere Projektaenderung (siehe storage/atomic_json.py).
    _save_projects(projects, path)
    if sidecars.get("primary_paper_id"):
        primaries = _load_primary_papers()
        primaries.setdefault(report.project, str(sidecars["primary_paper_id"]))
        _save_primary_papers(primaries)
    if sidecars.get("pinned"):
        meta = _load_project_meta()
        entry = dict(meta.get(report.project) or {})
        entry["pinned"] = True
        meta[report.project] = entry
        _save_project_meta(meta)

    logger.info("Bundle importiert: %s", report.as_dict())
    return {"report": report.as_dict()}
