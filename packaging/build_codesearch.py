"""Build the vendored CodeSearch CLI for the desktop bundle.

Run from the repo root:

    python packaging/build_codesearch.py

Produces ``src-tauri/sidecar/codesearch/cs`` (``cs.exe`` on Windows). Built
straight into ``src-tauri/`` — like the backend sidecar — so the Tauri bundle can
ship it as a plain resource glob without ``..`` traversal, and
``codegraph/binary.py`` finds it there at runtime.

The binary is self-contained: the tree-sitter grammars and language packs are
``include_str!``-ed at compile time, and SQLite is the bundled amalgamation. It
links nothing but libc — in particular **no webkit**, because the only crate in
the CodeSearch workspace that depended on tauri was its own desktop shell, and
that one was not vendored.

Building it is optional. Without it, the Code-Graph tab in the Werkstatt shows a
hint saying how to build it, and everything else in the app works unchanged.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST = PROJECT_ROOT / "codesearch" / "Cargo.toml"
DIST = PROJECT_ROOT / "src-tauri" / "sidecar" / "codesearch"

EXE = "cs.exe" if os.name == "nt" else "cs"
BUILT = PROJECT_ROOT / "codesearch" / "target" / "release" / EXE


def _preflight() -> int:
    if not MANIFEST.is_file():
        print(
            f"[build_codesearch] FEHLER: {MANIFEST} fehlt.\n"
            "Der einvendorte CodeSearch-Workspace gehoert nach codesearch/.",
            file=sys.stderr,
        )
        return 1

    if shutil.which("cargo") is None:
        print(
            "[build_codesearch] FEHLER: 'cargo' ist nicht auf dem PATH.\n"
            "Rust >= 1.82 installieren: https://rustup.rs\n"
            "Danach eine neue Shell oeffnen (rustup ergaenzt den PATH).",
            file=sys.stderr,
        )
        return 1
    return 0


def main() -> int:
    failed = _preflight()
    if failed:
        return failed

    command = [
        "cargo",
        "build",
        "--release",
        "--bin",
        "cs",
        "--manifest-path",
        str(MANIFEST),
    ]
    print(f"[build_codesearch] {' '.join(command)}")
    # Kein shell=True, argv-Liste — wie ueberall sonst, wo dieses Projekt
    # Fremdprozesse startet.
    result = subprocess.run(command, cwd=str(PROJECT_ROOT), check=False)
    if result.returncode != 0:
        print(
            "[build_codesearch] FEHLER: cargo build fehlgeschlagen "
            f"(Code {result.returncode}).",
            file=sys.stderr,
        )
        return result.returncode

    if not BUILT.is_file():
        print(
            f"[build_codesearch] FEHLER: {BUILT} wurde nicht erzeugt.", file=sys.stderr
        )
        return 1

    DIST.mkdir(parents=True, exist_ok=True)
    target = DIST / EXE
    shutil.copy2(BUILT, target)
    # copy2 erhaelt das Ausfuehrbar-Bit nur, wenn die Quelle es hatte — auf
    # frisch ausgepackten CI-Checkouts ist das nicht garantiert.
    if os.name != "nt":
        target.chmod(target.stat().st_mode | 0o111)

    size_mb = target.stat().st_size / (1024 * 1024)
    print(f"[build_codesearch] fertig: {target} ({size_mb:.1f} MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
