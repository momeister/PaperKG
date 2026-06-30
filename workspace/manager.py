"""Code-Werkstatt: managed/external project folders + safe file & git access.

The Werkstatt lets the user — and the AI coding CLIs running in the embedded
terminal (``claude``/Claude Code, ``opencode``, ``codex``, ``git`` …) — work on
real coding-project folders that live on disk at a findable location, so other
editors can open them too. PaperKG only *registers* the folders (see the
``code_projects`` table in :mod:`storage.metadata_db`); the files stay on disk.

Two project kinds:
  * ``managed``  — created by us under the workspaces base dir and ``git init``'d.
  * ``external`` — an existing folder the user opened ("Ordner öffnen").

Everything here is pure filesystem/git logic and provider-agnostic, so it behaves
identically in the web app and the native shell. All file access is *contained*:
a caller-supplied relative path is resolved and rejected if it escapes the project
root (``..`` traversal, absolute paths, drive letters, symlinks pointing outside).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import yaml


class WorkspaceError(Exception):
    """Invalid workspace operation (bad path, missing folder, file too large…)."""


# Directories we never descend into when listing a project tree — noise/huge and
# never what the user wants to hand-edit. (``.git`` content is exposed via the
# git endpoints instead.)
IGNORED_DIR_NAMES = {
    ".git", "__pycache__", "node_modules", ".venv", "venv", "env",
    ".mypy_cache", ".pytest_cache", ".ruff_cache", ".idea", ".gradle",
    "dist", "build", ".next", ".turbo", ".cache", "target", ".tox",
}
# Hard cap so a pathological repo can't produce a huge tree response.
MAX_TREE_ENTRIES = 4000
# The Monaco editor is for source, not binaries / large data files.
MAX_FILE_BYTES = 2_000_000

DEFAULT_BASE_DIR = Path.home() / "Documents" / "PaperKG-Projekte"

_DEFAULT_GITIGNORE = """\
# Auto-seeded by PaperKG Code-Werkstatt — adjust freely.
__pycache__/
*.pyc
.venv/
node_modules/
dist/
build/
.DS_Store
"""


# --------------------------------------------------------------------------- #
# Base dir / project roots                                                     #
# --------------------------------------------------------------------------- #


def base_dir(config_path: str = "config.yaml") -> Path:
    """Managed-projects base dir.

    Read from ``code_workspaces.base_dir`` in ``config.yaml`` if present, else the
    default ``~/Documents/PaperKG-Projekte``. ``~`` and ``$ENV`` are expanded.
    """
    raw: str | None = None
    try:
        with open(config_path, "r", encoding="utf-8") as fh:
            section = (yaml.safe_load(fh) or {}).get("code_workspaces", {}) or {}
        if isinstance(section, dict):
            value = section.get("base_dir")
            raw = str(value) if value else None
    except (FileNotFoundError, OSError, yaml.YAMLError):
        raw = None
    if raw:
        return Path(os.path.expanduser(os.path.expandvars(raw))).resolve()
    return DEFAULT_BASE_DIR.resolve()


def project_root(project: dict[str, Any]) -> Path:
    """Resolved on-disk root of a registered project record."""
    return Path(str(project["path"])).resolve()


def ensure_exists(project: dict[str, Any]) -> Path:
    """Return the project root, raising if the folder is gone (deleted/moved)."""
    root = project_root(project)
    if not root.is_dir():
        raise WorkspaceError(f"Projektordner existiert nicht mehr: {root}")
    return root


def resolve_within(root: Path, relpath: str, *, must_exist: bool = False) -> Path:
    """Resolve ``relpath`` against ``root``, rejecting any escape from the root.

    The input is always treated as *relative* to the project root: leading
    slashes/backslashes are stripped and the result must stay inside the root.
    """
    root = root.resolve()
    rel = (relpath or "").replace("\\", "/").strip().lstrip("/")
    candidate = (root / rel).resolve()
    if candidate != root and root not in candidate.parents:
        raise WorkspaceError(f"Pfad verlässt das Projektverzeichnis: {relpath!r}")
    if must_exist and not candidate.exists():
        raise WorkspaceError(f"Datei/Ordner nicht gefunden: {relpath!r}")
    return candidate


def _require_subpath(root: Path, relpath: str) -> Path:
    """Like :func:`resolve_within` but also reject the project root itself."""
    target = resolve_within(root, relpath)
    if target == root.resolve():
        raise WorkspaceError("Es wurde kein Datei-/Ordnerpfad angegeben.")
    return target


# --------------------------------------------------------------------------- #
# Project creation / registration helpers                                      #
# --------------------------------------------------------------------------- #


def safe_folder_name(name: str) -> str:
    """Turn a free-text project name into a safe on-disk folder name."""
    cleaned = "".join(c for c in (name or "").strip() if c.isalnum() or c in "-_. ")
    cleaned = "-".join(cleaned.split())  # collapse whitespace → single dashes
    cleaned = cleaned.strip("-._")
    return cleaned[:80]


def init_managed_project(base: Path, name: str) -> Path:
    """Create a new managed project folder under ``base`` and ``git init`` it.

    Seeds a ``README.md`` + ``.gitignore`` and makes an initial commit (if git is
    available) so the Ergebnis-/Diff-Ansicht has a baseline to diff against.
    Returns the created root. Raises if the target already exists.
    """
    folder = safe_folder_name(name)
    if not folder:
        raise WorkspaceError("Ungültiger Projektname.")
    base = base.resolve()
    base.mkdir(parents=True, exist_ok=True)
    root = (base / folder).resolve()
    if base not in root.parents:
        raise WorkspaceError("Ungültiger Projektname.")
    if root.exists():
        raise WorkspaceError(f"Projektordner existiert bereits: {root}")
    root.mkdir(parents=True)
    (root / "README.md").write_text(f"# {name}\n", encoding="utf-8")
    (root / ".gitignore").write_text(_DEFAULT_GITIGNORE, encoding="utf-8")
    if git_available():
        _run_git(root, ["init"])
        _run_git(root, ["add", "-A"])
        # Don't fail project creation if user has no git identity configured.
        _run_git(
            root,
            ["-c", "user.email=werkstatt@paperkg.local", "-c", "user.name=PaperKG",
             "commit", "-m", "Initiales Projekt (PaperKG Code-Werkstatt)"],
        )
    return root


def validate_external_folder(path: str) -> Path:
    """Resolve + validate a folder the user wants to open as an external project."""
    if not path or not str(path).strip():
        raise WorkspaceError("Kein Ordnerpfad angegeben.")
    root = Path(os.path.expanduser(os.path.expandvars(str(path).strip()))).resolve()
    if not root.exists():
        raise WorkspaceError(f"Ordner existiert nicht: {root}")
    if not root.is_dir():
        raise WorkspaceError(f"Pfad ist kein Ordner: {root}")
    return root


# --------------------------------------------------------------------------- #
# File tree / read / write                                                     #
# --------------------------------------------------------------------------- #


def build_tree(root: Path) -> dict[str, Any]:
    """Nested file tree of the project, skipping noise dirs and capping entries."""
    root = root.resolve()
    counter = {"n": 0}

    def walk(dir_path: Path) -> list[dict[str, Any]]:
        nodes: list[dict[str, Any]] = []
        try:
            children = sorted(
                dir_path.iterdir(),
                key=lambda p: (not p.is_dir(), p.name.lower()),
            )
        except OSError:
            return nodes
        for child in children:
            if counter["n"] >= MAX_TREE_ENTRIES:
                break
            is_dir = child.is_dir()
            if is_dir and child.name in IGNORED_DIR_NAMES:
                continue
            counter["n"] += 1
            rel = child.relative_to(root).as_posix()
            node: dict[str, Any] = {
                "name": child.name,
                "path": rel,
                "type": "dir" if is_dir else "file",
            }
            if is_dir:
                # Don't descend into symlinked dirs — avoids loops and escapes.
                node["children"] = [] if child.is_symlink() else walk(child)
            else:
                try:
                    node["size"] = child.stat().st_size
                except OSError:
                    node["size"] = None
            nodes.append(node)
        return nodes

    return {
        "name": root.name,
        "path": "",
        "type": "dir",
        "children": walk(root),
        "truncated": counter["n"] >= MAX_TREE_ENTRIES,
    }


def read_file(root: Path, relpath: str) -> dict[str, Any]:
    """Read a UTF-8 text file. Binary/oversized files return a flag, not content."""
    target = resolve_within(root, relpath, must_exist=True)
    if target.is_dir():
        raise WorkspaceError(f"Ist ein Verzeichnis, keine Datei: {relpath!r}")
    size = target.stat().st_size
    if size > MAX_FILE_BYTES:
        return {"path": relpath, "content": None, "size": size,
                "too_large": True, "binary": False}
    raw = target.read_bytes()
    if b"\x00" in raw:
        return {"path": relpath, "content": None, "size": size,
                "too_large": False, "binary": True}
    return {"path": relpath, "content": raw.decode("utf-8", errors="replace"),
            "size": size, "too_large": False, "binary": False}


def write_file(root: Path, relpath: str, content: str) -> dict[str, Any]:
    """Write text to a file (creating parent dirs). Preserves newlines exactly."""
    target = _require_subpath(root, relpath)
    if target.is_dir():
        raise WorkspaceError(f"Ist ein Verzeichnis, keine Datei: {relpath!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    # newline="" → no translation; store exactly what the editor sent.
    target.write_text(content if content is not None else "", encoding="utf-8", newline="")
    return {"path": relpath, "size": target.stat().st_size}


def create_file(root: Path, relpath: str) -> dict[str, Any]:
    target = _require_subpath(root, relpath)
    if target.exists():
        raise WorkspaceError(f"Existiert bereits: {relpath!r}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("", encoding="utf-8")
    return {"path": relpath, "type": "file"}


def create_dir(root: Path, relpath: str) -> dict[str, Any]:
    target = _require_subpath(root, relpath)
    target.mkdir(parents=True, exist_ok=True)
    return {"path": relpath, "type": "dir"}


def delete_path(root: Path, relpath: str) -> dict[str, Any]:
    target = resolve_within(root, relpath, must_exist=True)
    if target == root.resolve():
        raise WorkspaceError("Der Projekt-Root kann nicht gelöscht werden.")
    if target.is_dir() and not target.is_symlink():
        shutil.rmtree(target)
    else:
        target.unlink()
    return {"path": relpath, "deleted": True}


# --------------------------------------------------------------------------- #
# Git (Ergebnis-/Diff-Ansicht: "was wurde gebaut")                            #
# --------------------------------------------------------------------------- #


def git_available() -> bool:
    return shutil.which("git") is not None


def _run_git(root: Path, args: list[str]) -> tuple[int, str, str]:
    try:
        proc = subprocess.run(
            ["git", *args],
            cwd=str(root),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return 1, "", str(exc)
    return proc.returncode, proc.stdout, proc.stderr


def is_git_repo(root: Path) -> bool:
    if not git_available():
        return False
    code, out, _ = _run_git(root, ["rev-parse", "--is-inside-work-tree"])
    return code == 0 and out.strip() == "true"


def git_status(root: Path) -> dict[str, Any]:
    """Porcelain status → list of changed/untracked files (the Ergebnis-Ansicht)."""
    if not git_available():
        return {"available": False, "is_repo": False, "files": []}
    if not is_git_repo(root):
        return {"available": True, "is_repo": False, "files": []}
    code, out, err = _run_git(root, ["status", "--porcelain=v1", "--untracked-files=all"])
    if code != 0:
        return {"available": True, "is_repo": True, "files": [], "error": err.strip()}
    files: list[dict[str, Any]] = []
    for line in out.splitlines():
        if len(line) < 4:
            continue
        x, y, path = line[0], line[1], line[3:]
        files.append({
            "x": x,
            "y": y,
            "path": path,
            "staged": x not in {" ", "?"},
            "untracked": x == "?" and y == "?",
            "code": (x + y).strip(),
        })
    return {"available": True, "is_repo": True, "files": files}


def git_diff(root: Path, relpath: str | None = None) -> dict[str, Any]:
    """Unified diff of working-tree changes (staged + unstaged) for the panel."""
    if not git_available():
        return {"available": False, "is_repo": False, "diff": ""}
    if not is_git_repo(root):
        return {"available": True, "is_repo": False, "diff": ""}
    path_args = ["--", relpath] if relpath else []
    # Prefer diff against HEAD (staged + unstaged). Fall back to the plain
    # working-tree diff when the repo has no commits yet (fresh managed project).
    code, out, err = _run_git(root, ["diff", "--no-color", "HEAD", *path_args])
    if code != 0:
        code, out, err = _run_git(root, ["diff", "--no-color", *path_args])
    return {
        "available": True,
        "is_repo": True,
        "diff": out,
        "error": err.strip() if code != 0 else None,
    }
