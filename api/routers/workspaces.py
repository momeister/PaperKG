"""Code-Werkstatt: Coding-Projektordner, Dateibaum, Editor, Git.

Split out of api/product_main.py. Behaviour unchanged. workspace_manager is a
module -> attribute access keeps test monkeypatches (base_dir) working.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from storage.metadata_db import MetadataDB
from workspace import manager as workspace_manager

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


class CreateWorkspaceRequest(BaseModel):
    name: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class OpenWorkspaceRequest(BaseModel):
    path: str
    name: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class WriteFileRequest(BaseModel):
    path: str
    content: str = ""
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class CreatePathRequest(BaseModel):
    path: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


def _require_code_project(db: MetadataDB, project_id: str) -> dict[str, Any]:
    proj = db.get_code_project(project_id)
    if proj is None:
        raise HTTPException(status_code=404, detail="Code-Projekt nicht gefunden")
    return proj


def _code_project_summary(project: dict[str, Any]) -> dict[str, Any]:
    """Project record + a cheap on-disk existence flag for the picker."""
    out = dict(project)
    try:
        out["exists"] = Path(str(project.get("path"))).is_dir()
    except OSError:
        out["exists"] = False
    return out


@router.get("/workspaces")
def list_workspaces(metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        projects = [_code_project_summary(p) for p in db.list_code_projects()]
    return {
        "projects": projects,
        "base_dir": str(workspace_manager.base_dir()),
        "git_available": workspace_manager.git_available(),
    }


@router.post("/workspaces")
def create_workspace(request: CreateWorkspaceRequest) -> dict[str, Any]:
    """Create a new *managed* project folder (mkdir + git init) and register it."""
    name = (request.name or "").strip()
    if not name:
        raise HTTPException(status_code=400, detail="Projektname fehlt")
    root = workspace_manager.init_managed_project(workspace_manager.base_dir(), name)
    with MetadataDB(request.metadata_db_path) as db:
        project = db.add_code_project(name=name, path=str(root), kind="managed")
    return _code_project_summary(project)


@router.post("/workspaces/open")
def open_workspace(request: OpenWorkspaceRequest) -> dict[str, Any]:
    """Register an existing folder as an *external* project ("Ordner öffnen")."""
    root = workspace_manager.validate_external_folder(request.path)
    name = (request.name or "").strip() or root.name
    with MetadataDB(request.metadata_db_path) as db:
        existing = db.get_code_project_by_path(str(root))
        if existing is not None:
            return _code_project_summary(existing)
        project = db.add_code_project(name=name, path=str(root), kind="external")
    return _code_project_summary(project)


@router.delete("/workspaces/{project_id}")
def delete_workspace(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Unregister a project. The folder on disk is left untouched."""
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_code_project(project_id)
    return {"deleted": deleted, "id": project_id}


@router.get("/workspaces/{project_id}/tree")
def workspace_tree(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.build_tree(root)


@router.get("/workspaces/{project_id}/file")
def workspace_read_file(
    project_id: str, path: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.read_file(root, path)


@router.put("/workspaces/{project_id}/file")
def workspace_write_file(project_id: str, request: WriteFileRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.write_file(root, request.path, request.content)


@router.post("/workspaces/{project_id}/file")
def workspace_create_file(project_id: str, request: CreatePathRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.create_file(root, request.path)


@router.post("/workspaces/{project_id}/dir")
def workspace_create_dir(project_id: str, request: CreatePathRequest) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.create_dir(root, request.path)


@router.delete("/workspaces/{project_id}/file")
def workspace_delete_file(
    project_id: str, path: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.delete_path(root, path)


@router.get("/workspaces/{project_id}/git/status")
def workspace_git_status(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.git_status(root)


@router.get("/workspaces/{project_id}/git/diff")
def workspace_git_diff(
    project_id: str,
    path: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    return workspace_manager.git_diff(root, path)


class WorkspaceSessionPayload(BaseModel):
    payload: dict[str, Any]
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    # Nur der ausdrueckliche "Session loeschen"-Pfad im Frontend darf eine
    # nicht-leere Unterhaltung durch eine leere ersetzen.
    force: bool = False


class WorkspaceSessionRestore(BaseModel):
    saved_at: datetime | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


@router.get("/workspace/sessions/{project_id}")
def get_workspace_session(project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    """Server-side workspace assistant session (chat history + verification payloads).

    Sessions used to live only in localStorage, where large verification payloads
    routinely blew the quota and the save silently failed — conversations vanished
    on reload. DuckDB has no such limit.
    """
    with MetadataDB(metadata_db_path) as db:
        session = db.get_workspace_session(project_id)
    return session or {"project_id": project_id, "payload": {}, "updated_timestamp": None}


@router.put("/workspace/sessions/{project_id}")
def save_workspace_session(project_id: str, request: WorkspaceSessionPayload) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        session = db.save_workspace_session(project_id, request.payload, force=request.force)
    return session


@router.get("/workspace/sessions/{project_id}/backups")
def list_workspace_session_backups(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    """Rolling backups of the previous session states, newest first."""
    with MetadataDB(metadata_db_path) as db:
        backups = db.list_workspace_session_backups(project_id)
    return {"project_id": project_id, "backups": backups}


@router.post("/workspace/sessions/{project_id}/restore")
def restore_workspace_session(project_id: str, request: WorkspaceSessionRestore) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        session = db.restore_workspace_session(project_id, request.saved_at)
    if session is None:
        raise HTTPException(status_code=404, detail="Keine Sicherung fuer diese Session vorhanden")
    return session
