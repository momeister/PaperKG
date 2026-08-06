"""Wo liegt das ``cs``-Binary?

Vier Fundstellen, in dieser Reihenfolge — die Umgebungsvariable schlägt alles,
damit ein Entwickler einen eigenen Build unterschieben kann, ohne die config.yaml
anzufassen (dasselbe Muster wie ``SCIENCEKG_NODE_PATH`` in
``src-tauri/src/agent_bridge.rs``):

1. ``SCIENCEKG_CODESEARCH_BIN``
2. ``codesearch.binary`` aus config.yaml
3. das gebündelte Sidecar (nur im PyInstaller-Build)
4. ``codesearch/target/release/cs`` bzw. ``.../debug/cs`` im Repo, sonst ``PATH``

Fehlt es überall, ist das **kein Absturz**: der Code-Graph ist ein Zusatz, und
der Rest des Programms funktioniert ohne ihn weiter. Der Fehler sagt stattdessen,
mit welchem Befehl man ihn baut.
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

#: Repo-Wurzel (dieses Paket liegt direkt darunter).
PROJECT_ROOT = Path(__file__).resolve().parent.parent

#: Standardablage der Indizes — eine SQLite-Datei je Code-Projekt.
DEFAULT_INDEX_DIR = "data/codegraph"

_EXE_SUFFIX = ".exe" if os.name == "nt" else ""
_BINARY_NAME = f"cs{_EXE_SUFFIX}"

#: Was die Nutzerin tun kann, wenn das Binary fehlt. Der Code-Graph ist ein
#: Zusatz — ohne ihn läuft alles andere weiter, also ist das ein Hinweis und
#: keine Fehlermeldung.
BUILD_HINT = (
    "CodeSearch-Binary nicht gefunden. Bauen mit:\n"
    "    python packaging/build_codesearch.py\n"
    "oder direkt:\n"
    "    cargo build --release --bin cs --manifest-path codesearch/Cargo.toml"
)


class CodeSearchMissingError(RuntimeError):
    """Das ``cs``-Binary ist nirgends auffindbar."""


def load_config(config_path: str = "config.yaml") -> dict[str, Any]:
    """Der ``codesearch:``-Block aus config.yaml (leer, wenn es ihn nicht gibt)."""
    try:
        with open(config_path, "r", encoding="utf-8") as handle:
            return (yaml.safe_load(handle) or {}).get("codesearch", {}) or {}
    except (FileNotFoundError, OSError, yaml.YAMLError):
        return {}


def _candidates(config_path: str) -> list[Path]:
    found: list[Path] = []

    from_env = os.environ.get("SCIENCEKG_CODESEARCH_BIN", "").strip()
    if from_env:
        found.append(Path(os.path.expandvars(from_env)).expanduser())

    from_config = str(load_config(config_path).get("binary") or "").strip()
    if from_config:
        found.append(Path(os.path.expandvars(from_config)).expanduser())

    if getattr(sys, "frozen", False):
        # Im Installer liegen beide Sidecars als Tauri-Resources nebeneinander:
        #   <resources>/sidecar/sciencekg-backend/sciencekg-backend[.exe]  ← sys.executable
        #   <resources>/sidecar/codesearch/cs[.exe]
        # Also ist es das Geschwister-Verzeichnis, nicht ein Unterordner.
        backend_dir = Path(sys.executable).resolve().parent
        found.append(backend_dir.parent / "codesearch" / _BINARY_NAME)
        found.append(backend_dir / "codesearch" / _BINARY_NAME)
        meipass = getattr(sys, "_MEIPASS", None)
        if meipass:
            found.append(Path(meipass) / "codesearch" / _BINARY_NAME)

    found.append(PROJECT_ROOT / "codesearch" / "target" / "release" / _BINARY_NAME)
    found.append(PROJECT_ROOT / "codesearch" / "target" / "debug" / _BINARY_NAME)

    on_path = shutil.which("cs")
    if on_path:
        found.append(Path(on_path))

    return found


def find_binary(config_path: str = "config.yaml") -> Path:
    """Erster brauchbarer Kandidat, sonst :class:`CodeSearchMissingError`."""
    for candidate in _candidates(config_path):
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise CodeSearchMissingError(BUILD_HINT)


def binary_available(config_path: str = "config.yaml") -> bool:
    """Ohne Ausnahme prüfen — für Statusanzeigen und ``GET /codegraph/…``."""
    try:
        find_binary(config_path)
    except CodeSearchMissingError:
        return False
    return True


def index_dir(config_path: str = "config.yaml") -> Path:
    """Verzeichnis für die Indizes, absolut."""
    configured = str(load_config(config_path).get("index_dir") or DEFAULT_INDEX_DIR)
    path = Path(os.path.expandvars(configured)).expanduser()
    if not path.is_absolute():
        path = PROJECT_ROOT / path
    return path


def index_path(code_project_id: str, config_path: str = "config.yaml") -> Path:
    """Indexdatei eines Code-Projekts.

    Bewusst **nicht** im Repository des Nutzers: der Index ist unser Cache, nicht
    dessen Daten. Ein ``.codesearch/`` in einem fremden Checkout wäre Müll, den
    wir dort hinterlassen — und in `data/` wird er ohnehin schon ignoriert.
    """
    safe = "".join(
        char if char.isalnum() or char in "-_" else "_" for char in code_project_id
    )
    return index_dir(config_path) / (safe or "unbenannt") / "index.csdb"
