"""PyInstaller entry point for the bundled FastAPI backend (M2 native sidecar).

The native Tauri shell launches this program instead of ``python -m uvicorn``
(PyInstaller cannot run ``-m``). It is a thin launcher: it imports the unchanged
product app (``api.product_main:app``) and serves it with uvicorn.

The backend reads ``config.yaml`` / ``ontology.yaml`` and the ``data/`` directory
**relative to the current working directory**. The Tauri shell sets the CWD to the
per-user data dir (``%APPDATA%/ScienceKG``) before spawning us; for standalone
testing you can pass ``--data-dir`` to chdir there yourself.
"""

from __future__ import annotations

import argparse
import os
import sys


def _parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="ScienceKG bundled backend sidecar.")
    parser.add_argument("--port", type=int, required=True, help="Localhost port to bind.")
    parser.add_argument("--host", default="127.0.0.1", help="Host to bind (default 127.0.0.1).")
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Optional working directory holding config.yaml/ontology.yaml/data/. "
        "Normally set by the launcher via the process CWD; pass it to run standalone.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = _parse_args(sys.argv[1:] if argv is None else argv)

    if args.data_dir:
        os.makedirs(args.data_dir, exist_ok=True)
        os.chdir(args.data_dir)

    # Import after the chdir so any module-level relative reads (config.yaml,
    # ontology.yaml) resolve against the data dir.
    import uvicorn

    from api.product_main import app

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
