"""Containment guard for client-supplied filesystem paths.

Several HTTP endpoints accept path overrides as query/body parameters
(``metadata_db_path``, ``pdf_base_dir``, ``note_asset_dir``, ``projects_path`` …).
They default to locations under ``data/`` but, being request parameters, a caller
could otherwise point them at arbitrary locations (``/etc/passwd``,
``C:\\Windows\\...``, a sibling user's home) — an arbitrary file read/write/DB-open
vector if the API is ever reachable beyond localhost.

This module enforces that any such path resolves inside an allowed root:

* the project root (where ``data/`` lives),
* the current working directory,
* the OS temp dir (so pytest ``tmp_path`` / ``--basetemp`` scratch dirs work),
* any path in the ``SCIENCEKG_DATA_ROOT`` env var (``os.pathsep``-separated).

Set ``SCIENCEKG_DISABLE_PATH_GUARD=1`` to bypass entirely (escape hatch for unusual
deployments). The check is intentionally lenient toward the dev/test tree and strict
only about escaping it — appropriate for a local single-user tool.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path

__all__ = ["PathSafetyError", "ensure_safe_path", "is_within_allowed", "allowed_roots"]

_PROJECT_ROOT = Path(__file__).resolve().parents[1]


class PathSafetyError(ValueError):
    """Raised when a client-supplied path resolves outside the allowed roots."""


def _guard_disabled() -> bool:
    return os.getenv("SCIENCEKG_DISABLE_PATH_GUARD", "").strip().lower() in {"1", "true", "yes"}


def allowed_roots() -> list[Path]:
    roots = [_PROJECT_ROOT, Path.cwd().resolve(), Path(tempfile.gettempdir()).resolve()]
    extra = os.getenv("SCIENCEKG_DATA_ROOT", "")
    for part in extra.split(os.pathsep):
        part = part.strip()
        if part:
            try:
                roots.append(Path(part).resolve())
            except (OSError, ValueError):
                continue
    return roots


def is_within_allowed(path: str | os.PathLike[str]) -> bool:
    try:
        resolved = Path(path).resolve()
    except (OSError, ValueError):
        return False
    for root in allowed_roots():
        if resolved == root or root in resolved.parents:
            return True
    return False


def ensure_safe_path(path: str | os.PathLike[str], *, what: str = "path") -> Path:
    """Return the resolved path if it is inside an allowed root, else raise.

    When the guard is disabled via env, the path is returned resolved without checks.
    """
    resolved = Path(path).resolve()
    if _guard_disabled() or is_within_allowed(resolved):
        return resolved
    raise PathSafetyError(
        f"Refusing to use {what} outside the project/data directory: {path!r}"
    )
