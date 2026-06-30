# -*- mode: python ; coding: utf-8 -*-
"""PyInstaller spec for the ScienceKG backend sidecar (one-dir).

Builds ``dist/sciencekg-backend/`` — a self-contained folder with
``sciencekg-backend.exe`` plus all Python libs. The Tauri bundle ships this
folder as a resource and the shell spawns the exe (M2). Build it with
``python packaging/build_sidecar.py`` (run from the repo root).

torch / sentence-transformers are the heavy, fragile parts: use the CPU wheel
and let ``collect_all`` pull their data files + dynamic submodules.
"""

import os

from PyInstaller.utils.hooks import collect_all, collect_submodules

# SPECPATH is injected by PyInstaller and points at this file's directory
# (``packaging/``); the repo root is its parent.
ROOT = os.path.dirname(SPECPATH)  # noqa: F821  (SPECPATH provided by PyInstaller)
ENTRY = os.path.join(ROOT, "packaging", "sidecar_entry.py")

datas: list = []
binaries: list = []
hiddenimports: list = []

# Third-party packages that ship data files and/or load submodules dynamically,
# so PyInstaller's import follower alone misses them. kuzu is optional (no wheel
# on Python >= 3.14) — collect_all raises if absent, so guard each one.
for _pkg in (
    "torch",
    "sentence_transformers",
    "transformers",
    "tokenizers",
    "safetensors",
    "huggingface_hub",
    "sklearn",
    "scipy",
    "duckdb",
    "kuzu",
    "pdfplumber",
    "pdfminer",
    "matplotlib",
):
    try:
        _d, _b, _h = collect_all(_pkg)
        datas += _d
        binaries += _b
        hiddenimports += _h
    except Exception as exc:  # noqa: BLE001 - optional/absent package
        print(f"[sidecar.spec] skip collect_all({_pkg!r}): {exc}")

# Our own packages: collect every submodule so lazily/dynamically imported ones
# (e.g. parser selection in parsing.parser_router, harvester clients) are frozen.
for _pkg in (
    "api",
    "query",
    "extraction",
    "graph",
    "harvester",
    "quality",
    "maintenance",
    "parsing",
    "storage",
    "research",
    "export",
    "scheduler",
):
    try:
        hiddenimports += collect_submodules(_pkg)
    except Exception as exc:  # noqa: BLE001
        print(f"[sidecar.spec] skip collect_submodules({_pkg!r}): {exc}")

# uvicorn loads its protocol/loop/lifespan implementations by string at runtime.
hiddenimports += collect_submodules("uvicorn")
hiddenimports += [
    "uvicorn.loops.auto",
    "uvicorn.lifespan.on",
    "uvicorn.lifespan.off",
    "uvicorn.protocols.http.auto",
    "uvicorn.protocols.websockets.auto",
    # httpx[http2] + assorted runtime-resolved deps that the follower can miss.
    "h2",
    "hpack",
    "hyperframe",
    "anyio",
    "multipart",
]

# Heavy, unused-in-the-product modules — excluding them trims the bundle and
# avoids dragging in extra toolchains. The product backend (api.product_main)
# uses none of these.
excludes = [
    "streamlit",
    "celery",
    "redis",
    "pyvis",
    "tkinter",
    "pytest",
    "_pytest",
    "mypy",
    "mypyc",
    "black",
    "isort",
    "flake8",
    "ruff",
    "IPython",
    "notebook",
]


a = Analysis(  # noqa: F821 - PyInstaller global
    [ENTRY],
    pathex=[ROOT],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=excludes,
    noarchive=False,
)

pyz = PYZ(a.pure)  # noqa: F821

exe = EXE(  # noqa: F821
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="sciencekg-backend",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)

coll = COLLECT(  # noqa: F821
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=False,
    upx_exclude=[],
    name="sciencekg-backend",
)
