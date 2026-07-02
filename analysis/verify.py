"""Reproduzierbarkeits-Check (WP4).

Führt das committete ``script.py`` eines Laufs in einem isolierten Temp-Ordner mit
demselben Seed erneut aus und vergleicht den Ausgabe-Fingerabdruck (``output_hash``)
mit dem gespeicherten. Stimmen sie überein, ist der Lauf **reproduzierbar** — die
Grundlage für das „nachvollziehbar/reproduzierbar"-Badge. Die Original-Ausgaben
werden dabei nicht angefasst (Verifikation läuft in einer Kopie).
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path
from typing import Any

from analysis import runner
from storage.metadata_db import MetadataDB


def verify_run(
    db: MetadataDB, run_id: str, *, timeout: float | None = None
) -> dict[str, Any] | None:
    """Re-run a stored analysis deterministically and compare its output hash.

    Returns ``{reproducible, expected, actual, ok, stderr}`` or ``None`` if the run
    (or its script) is gone. Persists the freshly computed hash as ``verified_hash``.
    """
    run = db.get_analysis_run(run_id)
    if run is None:
        return None
    run_dir = Path(str(run.get("run_dir"))).resolve()
    script = run_dir / runner.SCRIPT_FILENAME
    if not script.is_file():
        return {"reproducible": False, "expected": run.get("output_hash"), "actual": None,
                "ok": False, "stderr": "script.py fehlt auf der Platte."}

    seed = int(run.get("seed") or runner.DEFAULT_SEED)
    expected = str(run.get("output_hash") or "")

    tmp = Path(tempfile.mkdtemp(prefix="pkg_verify_"))
    try:
        shutil.copy2(script, tmp / runner.SCRIPT_FILENAME)
        inputs = run_dir / runner.INPUTS_DIRNAME
        if inputs.is_dir():
            shutil.copytree(inputs, tmp / runner.INPUTS_DIRNAME)
        result = runner.run_existing_script(
            tmp, seed=seed, timeout=timeout or runner.DEFAULT_TIMEOUT_SECONDS
        )
        actual = result.combined_hash
        reproducible = bool(expected) and result.ok and actual == expected
        db.update_analysis_run(run_id, verified_hash=actual)
        return {
            "reproducible": reproducible,
            "expected": expected,
            "actual": actual,
            "ok": result.ok,
            "stderr": result.stderr[-2000:],
        }
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
