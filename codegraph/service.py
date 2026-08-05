"""Indizieren, Abfragen, Buchführung — die Schicht zwischen Router und RPC.

Der Router soll weder wissen, wo ein Index liegt, noch wie ein Fortschritt
aussieht. Er ruft hier auf, bekommt fertige Dicts und reicht sie durch.

Arbeitsteilung mit :mod:`codegraph.rpc`: dort steckt das Protokoll, hier die
Frage, *was* man damit tut — inklusive der Buchführung in DuckDB, damit die
Oberfläche „ist dieses Projekt indiziert?" beantworten kann, ohne dafür einen
Prozess zu starten.
"""
from __future__ import annotations

import queue
import shutil
import threading
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from codegraph import binary
from codegraph.pool import POOL
from codegraph.rpc import CodeGraphError
from storage.metadata_db import MetadataDB
from workspace import manager as workspace_manager


def status(db: MetadataDB, project: dict[str, Any], config_path: str = "config.yaml") -> dict[str, Any]:
    """Indexzustand eines Code-Projekts, ohne den Index anzufassen.

    Bewusst billig: das ist der Aufruf, den die Werkstatt bei jedem Projektwechsel
    macht. Ein Prozessstart nur um „noch nicht indiziert" zu sagen wäre Verschwendung.
    """
    code_project_id = str(project.get("id"))
    record = db.get_code_index(code_project_id)
    db_path = binary.index_path(code_project_id, config_path)
    available = binary.binary_available(config_path)

    return {
        "code_project_id": code_project_id,
        "name": project.get("name"),
        "path": project.get("path"),
        "binary_available": available,
        "binary_hint": None if available else binary.BUILD_HINT,
        "index_exists": db_path.is_file(),
        "db_path": str(db_path),
        "status": (record or {}).get("status") or "none",
        "stats": _stats_of(record),
        "skipped": (record or {}).get("skipped") or {},
        "duration_ms": (record or {}).get("duration_ms"),
        "commits_walked": (record or {}).get("commits_walked"),
        "error_message": (record or {}).get("error_message"),
        "last_indexed_timestamp": (record or {}).get("last_indexed_timestamp"),
    }


def _stats_of(record: dict[str, Any] | None) -> dict[str, int]:
    record = record or {}
    return {
        key: int(record.get(key) or 0)
        for key in ("files", "parsed_files", "nodes", "edges", "guessed_edges", "dynamic_gaps")
    }


def client_for(project: dict[str, Any], config_path: str = "config.yaml"):
    """Offener RPC-Client für dieses Projekt (Ordner muss existieren)."""
    root = workspace_manager.ensure_exists(project)
    return POOL.get(str(project.get("id")), root)


def _timeout(name: str, fallback: float, config_path: str = "config.yaml") -> float:
    try:
        return float(binary.load_config(config_path).get(name) or fallback)
    except (TypeError, ValueError):
        return fallback


def index(
    db: MetadataDB,
    project: dict[str, Any],
    *,
    config_path: str = "config.yaml",
) -> Iterator[dict[str, Any]]:
    """Indiziert und liefert Fortschritt als Strom von Ereignis-Dicts.

    Generator statt Callback, weil der Router daraus direkt SSE macht. Die
    Fortschrittsmeldungen der Rust-Seite werden dabei nicht umbenannt — was dort
    ``phase``/``done``/``total`` heißt, heißt hier genauso.
    """
    code_project_id = str(project.get("id"))
    db_path = binary.index_path(code_project_id, config_path)

    try:
        client = client_for(project, config_path)
    except Exception as error:  # noqa: BLE001 — jeder Fehler muss als Ereignis ankommen
        db.upsert_code_index(code_project_id, str(db_path), status="failed", error_message=str(error))
        yield {"event": "failed", "error": str(error)}
        return

    db.upsert_code_index(code_project_id, str(db_path), status="indexing", error_message=None)
    yield {"event": "started", "code_project_id": code_project_id}

    # Der eigentliche Lauf blockiert, bis er fertig ist. Damit die Oberfläche in
    # der Zwischenzeit etwas anzeigen kann, läuft er in einem Thread und schiebt
    # seine Meldungen über eine Queue hierher, wo sie zu SSE-Ereignissen werden.
    progress: queue.Queue[dict[str, Any] | None] = queue.Queue()
    result: dict[str, Any] = {}

    index_timeout = _timeout("index_timeout_seconds", 3600.0, config_path)

    def run() -> None:
        try:
            result["report"] = client.index(on_progress=progress.put, timeout=index_timeout)
        except Exception as error:  # noqa: BLE001 — jeder Fehler muss den Strom erreichen
            result["error"] = str(error)
        finally:
            progress.put(None)

    worker = threading.Thread(target=run, daemon=True)
    worker.start()

    while True:
        item = progress.get()
        if item is None:
            break
        yield {"event": "progress", **item}
    worker.join(timeout=5)

    if "error" in result:
        db.upsert_code_index(
            code_project_id, str(db_path), status="failed", error_message=result["error"]
        )
        yield {"event": "failed", "error": result["error"]}
        return

    report = result.get("report") or {}
    try:
        stats = client.stats()
    except CodeGraphError as error:
        db.upsert_code_index(code_project_id, str(db_path), status="failed", error_message=str(error))
        yield {"event": "failed", "error": str(error)}
        return

    record = db.upsert_code_index(
        code_project_id, str(db_path), status="ready", stats=stats, report=report
    )
    yield {"event": "done", "report": report, "stats": stats, "index": record}


def drop_index(db: MetadataDB, code_project_id: str, config_path: str = "config.yaml") -> bool:
    """Index löschen: erst den Prozess, der ihn offen hält, dann die Dateien."""
    POOL.drop(str(code_project_id))
    db.delete_code_index(str(code_project_id))

    db_path = binary.index_path(str(code_project_id), config_path)
    removed = False
    # WAL und SHM liegen daneben; einzeln entfernen, damit ein Rest nicht als
    # halber Index wieder aufgemacht wird.
    for path in (db_path, Path(f"{db_path}-wal"), Path(f"{db_path}-shm")):
        if path.is_file():
            path.unlink()
            removed = True
    parent = db_path.parent
    if parent.is_dir() and not any(parent.iterdir()):
        shutil.rmtree(parent, ignore_errors=True)
    return removed


def query(
    project: dict[str, Any],
    method: str,
    params: dict[str, Any] | None = None,
    *,
    config_path: str = "config.yaml",
    timeout: float | None = None,
) -> Any:
    """Eine Abfrage durchreichen. Fehler bleiben :class:`CodeGraphError`."""
    client = client_for(project, config_path)
    if timeout is None:
        timeout = _timeout("rpc_timeout_seconds", 120.0, config_path)
    return client.call(method, params or {}, timeout=timeout)


def resolve_positions(
    project: dict[str, Any],
    text: str,
    *,
    limit: int = 8,
    config_path: str = "config.yaml",
) -> list[dict[str, Any]]:
    """Terminal-Ausgabe → anklickbare Sprungmarken.

    Das Finden macht Python (:mod:`codegraph.positions`), das Auflösen auf ein
    Symbol der Graph. Stellen ohne Symbol bleiben drin: eine Datei mit Zeile ist
    auch ohne Symbol ein sinnvolles Sprungziel.
    """
    from codegraph.positions import find_positions_in_text

    found = find_positions_in_text(text, limit=limit)
    if not found:
        return []
    payload = [{"path": position.path, "line": position.line} for position in found]
    try:
        return query(project, "resolve_positions", {"positions": payload}, config_path=config_path)
    except (CodeGraphError, binary.CodeSearchMissingError):
        # Ohne Index gibt es keine Symbole, aber die Sprungmarke selbst steht.
        return [{**item, "node_id": None, "qualified": None} for item in payload]
