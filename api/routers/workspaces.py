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
from workspace import checkpoints as workspace_checkpoints
from workspace import sandbox as workspace_sandbox

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
    # Vor dem Speichern sichern — fail-soft. Ein Rücksprung, den man suchen
    # muss, ist keiner; deshalb ist der Checkpoint in der Antwort.
    with MetadataDB(request.metadata_db_path) as db:
        checkpoint = workspace_checkpoints.auto_checkpoint(
            db,
            project["id"],
            root,
            reason="auto_file_write",
            label=f"vor Speichern: {request.path}",
        )
    result = workspace_manager.write_file(root, request.path, request.content)
    return {**result, "checkpoint": checkpoint}


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


# --------------------------------------------------------------------------- #
# Git-Checkpoints (Stufe 2)                                                    #
# --------------------------------------------------------------------------- #


class CheckpointCreateRequest(BaseModel):
    label: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class CheckpointRestoreRequest(BaseModel):
    plan_hash: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


def _checkpoint_to_dict(db: MetadataDB, project_id: str, result: Any, reason: str) -> dict[str, Any]:
	"""Aus einem :class:`CheckpointResult` (ggf. None) einen DB-Eintrag + Antwort machen.

	Fail-soft: ohne git/Repo wird kein Eintrag angelegt; die Antwort traegt
	``checkpoint: null`` und ``reason``, damit die UI es sagen kann statt
	zu schweigen (gleiche Konvention wie ``git_log_for_lines``).
	"""
	if not result.ref_name or not result.commit_sha:
		return {"checkpoint": None, "reason": result.reason, "error": result.error}
	record = db.add_code_checkpoint(
		project_id,
		ref_name=result.ref_name,
		commit_sha=result.commit_sha,
		tree_sha=result.tree_sha,
		parent_sha=result.parent_sha,
		label=result.label,
		reason=reason,
		file_count=result.file_count,
	)
	return {"checkpoint": record, "reason": reason}


@router.get("/workspaces/{project_id}/checkpoints")
def list_checkpoints(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        checkpoints = db.list_code_checkpoints(project["id"])
    return {"project_id": project_id, "checkpoints": checkpoints}


@router.post("/workspaces/{project_id}/checkpoints")
def create_checkpoint(
    project_id: str, request: CheckpointCreateRequest
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path := request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
    root = workspace_manager.ensure_exists(project)
    result = workspace_checkpoints.create(
        root, label=request.label, reason="manual"
    )
    with MetadataDB(metadata_db_path) as db:
        return _checkpoint_to_dict(db, project_id, result, "manual")


@router.get("/workspaces/{project_id}/checkpoints/{checkpoint_id}/restore/preview")
def checkpoint_restore_preview(
    project_id: str,
    checkpoint_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        checkpoint = db.get_code_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint nicht gefunden")
    root = workspace_manager.ensure_exists(project)
    plan = workspace_checkpoints.restore_plan(root, checkpoint["commit_sha"])
    return {"project_id": project_id, "checkpoint": checkpoint, "plan": plan}


@router.post("/workspaces/{project_id}/checkpoints/{checkpoint_id}/restore")
def checkpoint_restore(
    project_id: str, checkpoint_id: str, request: CheckpointRestoreRequest
) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        checkpoint = db.get_code_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint nicht gefunden")
    root = workspace_manager.ensure_exists(project)
    result = workspace_checkpoints.restore(
        root,
        checkpoint["commit_sha"],
        request.plan_hash,
        label=f"vor Rücksprung auf {checkpoint.get('label') or checkpoint_id}",
    )
    if not result.get("applied"):
        if result.get("reason") == "stale":
            raise HTTPException(
                status_code=409,
                detail="Der Arbeitsbaum hat sich seit der Vorschau geändert. Bitte neu laden.",
            )
        return result
    # Die automatische Sicherung vor dem Rücksprung buchen, damit sie in der
    # Liste auftaucht und selbst zurueckrollbar ist.
    backup_ref = result.get("backup_ref")
    backup_sha = result.get("backup_sha")
    if backup_ref and backup_sha:
        with MetadataDB(request.metadata_db_path) as db:
            db.add_code_checkpoint(
                project["id"],
                ref_name=backup_ref,
                commit_sha=backup_sha,
                label=f"vor Rücksprung auf {checkpoint.get('label') or checkpoint_id}",
                reason="pre_restore",
            )
    return result


@router.get("/workspaces/{project_id}/checkpoints/{checkpoint_id}/diff")
def checkpoint_diff(
    project_id: str,
    checkpoint_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        checkpoint = db.get_code_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint nicht gefunden")
    root = workspace_manager.ensure_exists(project)
    return workspace_checkpoints.diff(root, checkpoint["commit_sha"])


@router.delete("/workspaces/{project_id}/checkpoints/{checkpoint_id}")
def delete_checkpoint(
    project_id: str,
    checkpoint_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        checkpoint = db.get_code_checkpoint(checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint nicht gefunden")
    root = workspace_manager.ensure_exists(project)
    removed = workspace_checkpoints.drop(root, checkpoint["ref_name"])
    with MetadataDB(metadata_db_path) as db:
        db.delete_code_checkpoint(checkpoint_id)
    return {"project_id": project_id, "checkpoint_id": checkpoint_id, "removed": removed}


# --------------------------------------------------------------------------- #
# Was-wäre-wenn-Sandbox (Stufe 2, git worktree)                                #
# --------------------------------------------------------------------------- #


class SandboxCreateRequest(BaseModel):
    checkpoint_id: str
    test_command: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class SandboxRunRequest(BaseModel):
    command: str | None = None
    timeout: int = 300
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


@router.get("/workspaces/{project_id}/sandboxes")
def list_sandboxes(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, project_id)
        sandboxes = db.list_code_sandboxes(project_id)
    return {"project_id": project_id, "sandboxes": sandboxes}


@router.post("/workspaces/{project_id}/sandboxes")
def create_sandbox(
    project_id: str, request: SandboxCreateRequest
) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        checkpoint = db.get_code_checkpoint(request.checkpoint_id)
    if checkpoint is None:
        raise HTTPException(status_code=404, detail="Checkpoint nicht gefunden")
    root = workspace_manager.ensure_exists(project)
    # Erst die DB-Zeile (liefert die id), dann den Worktree darunter anlegen.
    with MetadataDB(request.metadata_db_path) as db:
        record = db.add_code_sandbox(
            project_id,
            checkpoint_id=checkpoint["id"],
            base_sha=checkpoint["commit_sha"],
            status="creating",
            test_command=request.test_command,
        )
    created = workspace_sandbox.create_worktree(root, checkpoint["commit_sha"], record["id"])
    with MetadataDB(request.metadata_db_path) as db:
        if created.get("created"):
            db.update_code_sandbox(
                record["id"],
                status="created",
                test_command=request.test_command,
            )
            record = db.get_code_sandbox(record["id"]) or record
            record["path"] = created["path"]
        else:
            db.update_code_sandbox(record["id"], status="failed")
            record = db.get_code_sandbox(record["id"]) or record
            record["error"] = created.get("error") or created.get("reason")
    return {"project_id": project_id, "sandbox": record}


@router.post("/workspaces/{project_id}/sandboxes/{sandbox_id}/run")
def run_sandbox(
    project_id: str, sandbox_id: str, request: SandboxRunRequest
) -> dict[str, Any]:
    with MetadataDB(request.metadata_db_path) as db:
        _require_code_project(db, project_id)
        sandbox = db.get_code_sandbox(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox nicht gefunden")
    worktree = Path(sandbox["path"])
    if not worktree.is_dir():
        raise HTTPException(status_code=404, detail="Sandbox-Verzeichnis nicht mehr vorhanden")
    # Befehl: explizit, sonst der hinterlegte, sonst erkannt. Erstmal-Lauf
    # ohne hinterlegten Befehl ist bestätigungspflichtig — die UI fragt.
    if request.command:
        import shlex

        command = shlex.split(request.command)
    elif sandbox.get("test_command"):
        import shlex

        command = shlex.split(sandbox["test_command"])
    else:
        command = workspace_sandbox.detect_test_command(worktree)
    if not command:
        raise HTTPException(
            status_code=400,
            detail="Kein Testbefehl erkannt (pytest.ini/pyproject.toml/package.json/Cargo.toml). Bitte explizit angeben.",
        )
    result = workspace_sandbox.run_tests(worktree, command, timeout=max(10, min(int(request.timeout), 1800)))
    with MetadataDB(request.metadata_db_path) as db:
        db.update_code_sandbox(
            sandbox_id,
            status="passed" if result.returncode == 0 else "failed",
            last_exit_code=result.returncode,
            test_command=" ".join(command),
        )
    return {"project_id": project_id, "sandbox_id": sandbox_id, "run": result.as_dict()}


@router.get("/workspaces/{project_id}/sandboxes/{sandbox_id}/diff")
def sandbox_diff(
    project_id: str,
    sandbox_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        _require_code_project(db, project_id)
        sandbox = db.get_code_sandbox(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox nicht gefunden")
    worktree = Path(sandbox["path"])
    if not worktree.is_dir():
        raise HTTPException(status_code=404, detail="Sandbox-Verzeichnis nicht mehr vorhanden")
    return workspace_sandbox.diff_worktree(Path(sandbox["path"]), sandbox["base_sha"])


@router.post("/workspaces/{project_id}/sandboxes/{sandbox_id}/apply")
def apply_sandbox(
    project_id: str, sandbox_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        sandbox = db.get_code_sandbox(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox nicht gefunden")
    root = workspace_manager.ensure_exists(project)
    worktree = Path(sandbox["path"])
    if not worktree.is_dir():
        raise HTTPException(status_code=404, detail="Sandbox-Verzeichnis nicht mehr vorhanden")
    result = workspace_sandbox.apply_to_main(root, worktree, sandbox["base_sha"])
    with MetadataDB(metadata_db_path) as db:
        if result.get("applied"):
            db.update_code_sandbox(sandbox_id, status="applied")
    return result


@router.delete("/workspaces/{project_id}/sandboxes/{sandbox_id}")
def delete_sandbox(
    project_id: str,
    sandbox_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        project = _require_code_project(db, project_id)
        sandbox = db.get_code_sandbox(sandbox_id)
    if sandbox is None:
        raise HTTPException(status_code=404, detail="Sandbox nicht gefunden")
    root = workspace_manager.ensure_exists(project)
    workspace_sandbox.remove_worktree(root, sandbox_id)
    with MetadataDB(metadata_db_path) as db:
        db.delete_code_sandbox(sandbox_id)
    return {"project_id": project_id, "sandbox_id": sandbox_id, "removed": True}


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
