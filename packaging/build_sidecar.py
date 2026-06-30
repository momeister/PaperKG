"""Build the bundled backend sidecar with PyInstaller (M2).

Run from the repo root:

    python packaging/build_sidecar.py

Produces ``src-tauri/sidecar/sciencekg-backend/`` (one-dir). It is built straight
into ``src-tauri/`` (not ``dist/``) so the Tauri bundle can ship it as a plain
resource glob without ``..`` path traversal. ``src-tauri/src/lib.rs`` spawns the
inner exe in release builds, and ``tauri.conf.json``'s ``beforeBuildCommand`` runs
this automatically, so ``npm run tauri build`` is a single command.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SPEC = PROJECT_ROOT / "packaging" / "sidecar.spec"
DIST = PROJECT_ROOT / "src-tauri" / "sidecar"

# Deps that must be importable to produce the FULL bundle (embeddings work out of
# the box). The spec guards collect_all() in a try/except, so a missing heavy dep
# would otherwise silently yield a LEAN bundle — we'd rather fail loudly here.
# Set SCIENCEKG_BUNDLE_LEAN=1 to intentionally skip this and ship the hash-fallback.
FULL_BUNDLE_DEPS = ["torch", "sentence_transformers", "transformers", "pdfplumber"]


def _missing(mods: list[str]) -> list[str]:
    out: list[str] = []
    for m in mods:
        try:
            found = importlib.util.find_spec(m) is not None
        except Exception:
            found = False
        if not found:
            out.append(m)
    return out


def _preflight() -> int:
    if os.environ.get("SCIENCEKG_BUNDLE_LEAN"):
        print("[build_sidecar] SCIENCEKG_BUNDLE_LEAN set -> lean bundle (hash-fallback embeddings).")
        return 0
    missing = _missing(FULL_BUNDLE_DEPS)
    if missing:
        print(
            "[build_sidecar] ERROR: full bundle requested but these runtime deps are "
            f"missing from the active environment: {', '.join(missing)}\n"
            "Install them (CPU torch keeps the bundle ~1-2 GB instead of larger CUDA):\n"
            "    pip install -r requirements.txt\n"
            "    pip install torch --index-url https://download.pytorch.org/whl/cpu\n"
            "Or build the lean hash-fallback bundle instead:\n"
            "    set SCIENCEKG_BUNDLE_LEAN=1  (PowerShell: $env:SCIENCEKG_BUNDLE_LEAN=1)",
            file=sys.stderr,
        )
        return 1
    if importlib.util.find_spec("kuzu") is None:
        print("[build_sidecar] note: kuzu not installed (no wheel on Python >= 3.14); "
              "graph build will use the non-Kuzu fallback in the bundle.")
    return 0


def main() -> int:
    rc = _preflight()
    if rc != 0:
        return rc

    try:
        import PyInstaller.__main__  # noqa: PLC0415
    except ImportError:
        print(
            "PyInstaller is not installed. Install the build deps:\n"
            "    pip install -r requirements-build.txt",
            file=sys.stderr,
        )
        return 1

    PyInstaller.__main__.run(
        [
            str(SPEC),
            "--noconfirm",
            "--distpath",
            str(DIST),
            "--workpath",
            str(PROJECT_ROOT / "build" / "pyinstaller"),
        ]
    )
    out = DIST / "sciencekg-backend"
    print(f"\n[build_sidecar] Done -> {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
