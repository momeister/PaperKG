"""Regressionstests fuer den "alle Projekte sind weg"-Vorfall.

Ausloeser war ein zweiter Prozess auf derselben DuckDB: jeder Request lief auf
``duckdb.IOException``, und weil das Frontend Fehler als leere Listen rendert,
sah der Lock-Konflikt aus wie ein Totalverlust. Zusaetzlich schrieb
``_save_projects`` nicht-atomar, sodass ein SIGKILL mitten im Schreibvorgang
halbes JSON hinterliess — das wurde still als ``{}`` gelesen und beim naechsten
Speichern dauerhaft festgeschrieben.

Die Tests decken beide Pfade ab: eine gesperrte DB darf die Projektliste nicht
verstecken, und beschaedigte Sidecars muessen laut scheitern statt leer zu wirken.
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import product_main
from api.routers import projects as projects_router
from storage.atomic_json import CorruptJsonError, read_json_dict, write_json_atomic
from storage.instance_lock import (
    STALE_AFTER_SECONDS,
    InstanceLock,
    InstanceLockError,
    instance_lock_path,
)
from storage.metadata_db import MetadataDB, MetadataDBLockedError


# --------------------------------------------------------------------------- #
# storage/atomic_json.py                                                       #
# --------------------------------------------------------------------------- #


def test_write_json_atomic_roundtrip(tmp_path: Path) -> None:
    target = tmp_path / "projects.json"
    write_json_atomic(target, {"Projekt A": ["p1", "p2"]})
    assert read_json_dict(target) == {"Projekt A": ["p1", "p2"]}


def test_write_json_atomic_leaves_no_temp_files(tmp_path: Path) -> None:
    target = tmp_path / "projects.json"
    write_json_atomic(target, {"a": [1]})
    write_json_atomic(target, {"a": [1, 2]})
    assert [p.name for p in tmp_path.iterdir()] == ["projects.json"]


def test_write_json_atomic_keeps_old_file_when_serialisation_fails(tmp_path: Path) -> None:
    target = tmp_path / "projects.json"
    write_json_atomic(target, {"Projekt A": ["p1"]})
    with pytest.raises(TypeError):
        write_json_atomic(target, {"Projekt A": {object()}})
    # Die alte Datei muss unveraendert und vor allem *vollstaendig* dastehen.
    assert read_json_dict(target) == {"Projekt A": ["p1"]}
    assert [p.name for p in tmp_path.iterdir()] == ["projects.json"]


def test_read_json_dict_missing_file_is_empty(tmp_path: Path) -> None:
    assert read_json_dict(tmp_path / "nope.json") == {}


@pytest.mark.parametrize("content", ['{"Projekt A": ["p1", "p', "", "   ", "[1, 2, 3]"])
def test_read_json_dict_quarantines_corrupt_file(tmp_path: Path, content: str) -> None:
    target = tmp_path / "projects.json"
    target.write_text(content, encoding="utf-8")
    with pytest.raises(CorruptJsonError):
        read_json_dict(target)
    # Die kaputte Datei liegt beiseite, damit der naechste Schreibvorgang sie nicht
    # ueberschreibt — und ist damit noch von Hand rettbar.
    assert not target.exists()
    assert [p for p in tmp_path.iterdir() if ".corrupt-" in p.name]


# --------------------------------------------------------------------------- #
# Gesperrte DuckDB                                                             #
# --------------------------------------------------------------------------- #


@contextmanager
def _foreign_lock_holder(db_path: Path):
    """Halte den DuckDB-Lock aus einem *zweiten Prozess* — genau der Vorfall.

    Zwei Feinheiten, ohne die der Test nichts pruefen wuerde:

    * Innerhalb *eines* Prozesses reicht ``duckdb.connect`` nicht — DuckDB gibt
      dieselbe gecachte Datenbank-Instanz zurueck, statt zu kollidieren.
    * Ein blosses ``connect`` nimmt den Schreib-Lock noch nicht; der Halter muss
      einmal schreiben. Deshalb legt der Kindprozess eine Sondier-Tabelle an.
    """
    with MetadataDB(str(db_path)):  # Datei + Schema anlegen, dann wieder freigeben
        pass
    holder_code = (
        "import sys, duckdb; d = duckdb.connect(sys.argv[1]); "
        "d.execute('CREATE TABLE IF NOT EXISTS _lock_probe(a INTEGER)'); "
        "print('ready', flush=True); sys.stdin.readline()"
    )
    holder = subprocess.Popen(
        [sys.executable, "-c", holder_code, str(db_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout is not None
        if (holder.stdout.readline() or "").strip() != "ready":
            pytest.skip("Lock-Halter-Prozess konnte die DB nicht oeffnen")
        yield
    finally:
        holder.stdin.close()  # type: ignore[union-attr]
        holder.wait(timeout=30)


def test_locked_database_raises_typed_error(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    with _foreign_lock_holder(db_path):
        with pytest.raises(MetadataDBLockedError) as excinfo:
            MetadataDB(str(db_path))
    message = str(excinfo.value)
    assert "anderen Prozess" in message
    assert "keine Daten verloren" in message
    # Der blockierende Prozess wird benannt, damit man ihn finden kann.
    assert "PID" in message


def test_project_list_survives_a_locked_database(tmp_path: Path) -> None:
    """Der eigentliche Vorfall: DB gesperrt, Projekte muessen trotzdem erscheinen."""
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    with MetadataDB(str(db_path)) as db:
        db.insert_paper({"id": "p1", "source": "fixture", "source_id": "p1", "title": "T", "year": 2024})
    write_json_atomic(projects_path, {"Mein Projekt": ["p1"]})

    with _foreign_lock_holder(db_path):
        with TestClient(product_main.app) as client:
            response = client.get(
                "/projects",
                params={"metadata_db_path": str(db_path), "projects_path": str(projects_path)},
            )

    assert response.status_code == 200, response.text
    payload = response.json()
    assert [p["id"] for p in payload["projects"]] == ["Mein Projekt"]
    assert payload["projects"][0]["paper_count"] == 1
    # Der Ausfall wird gemeldet, nicht verschwiegen — die UI zeigt dafuer einen Hinweis.
    assert "degraded" in payload


def test_locked_database_yields_503_not_500(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    with _foreign_lock_holder(db_path):
        with TestClient(product_main.app, raise_server_exceptions=False) as client:
            response = client.get("/papers", params={"metadata_db_path": str(db_path)})
    assert response.status_code == 503
    assert "anderen Prozess" in response.text


# --------------------------------------------------------------------------- #
# Beschaedigte projects.json                                                   #
# --------------------------------------------------------------------------- #


def test_corrupt_projects_json_is_reported_not_silently_emptied(tmp_path: Path) -> None:
    projects_path = tmp_path / "projects.json"
    projects_path.write_text('{"Projekt A": ["p1", "p', encoding="utf-8")
    with pytest.raises(CorruptJsonError):
        projects_router._load_projects(projects_path)


def test_rename_migrates_database_before_touching_projects_json(tmp_path: Path, monkeypatch) -> None:
    """Schlaegt die DB-Migration fehl, darf projects.json nicht schon umbenannt sein.

    Sonst traegt das Projekt den neuen Namen, waehrend grey_sources/notes noch an
    der alten ID haengen — die Web-Quellen waeren dauerhaft verwaist.
    """
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    with MetadataDB(str(db_path)) as db:
        db.add_grey_source("Alt", {"id": "g1", "url": "https://example.org", "title": "Q"})
    write_json_atomic(projects_path, {"Alt": ["p1"]})

    def _boom(self, *args, **kwargs):
        raise RuntimeError("DB nicht erreichbar")

    monkeypatch.setattr(MetadataDB, "rename_project", _boom)

    with TestClient(product_main.app, raise_server_exceptions=False) as client:
        response = client.patch(
            "/projects/Alt",
            json={"name": "Neu"},
            params={"metadata_db_path": str(db_path), "projects_path": str(projects_path)},
        )
    assert response.status_code == 500

    # projects.json ist unangetastet: Projekt und Web-Quellen gehoeren weiter zusammen.
    assert json.loads(projects_path.read_text(encoding="utf-8")) == {"Alt": ["p1"]}
    with MetadataDB(str(db_path)) as db:
        assert [g["id"] for g in db.list_grey_sources("Alt")] == ["g1"]


# --------------------------------------------------------------------------- #
# Ein-Instanz-Guard                                                            #
# --------------------------------------------------------------------------- #


def test_instance_lock_blocks_a_second_backend(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("storage.instance_lock._disabled", lambda: False)
    db_path = tmp_path / "metadata.duckdb"

    first = InstanceLock(str(db_path))
    assert first.acquire() is True
    try:
        with pytest.raises(InstanceLockError) as excinfo:
            InstanceLock(str(db_path)).acquire()
        assert "bereits" in str(excinfo.value)
    finally:
        first.release()

    # Nach der Freigabe darf der naechste Start wieder durch.
    second = InstanceLock(str(db_path))
    assert second.acquire() is True
    second.release()


def test_instance_lock_blocks_across_a_container_boundary(tmp_path: Path, monkeypatch) -> None:
    """Docker Desktop reicht POSIX-Locks nicht durch — nur der Heartbeat greift.

    Nachgemessen: bei laufendem Container konnte der Host dieselbe DuckDB
    schreibend oeffnen. Hier wird genau dieser Fall simuliert, indem der
    Advisory-Lock (wie ueber die VM-Grenze) immer Erfolg meldet.
    """
    monkeypatch.setattr("storage.instance_lock._disabled", lambda: False)
    monkeypatch.setattr("storage.instance_lock._try_flock", lambda _fd: True)
    db_path = tmp_path / "metadata.duckdb"

    container = InstanceLock(str(db_path))
    assert container.acquire() is True
    try:
        with pytest.raises(InstanceLockError) as excinfo:
            InstanceLock(str(db_path)).acquire()
        assert "bereits" in str(excinfo.value)
    finally:
        container.release()


def test_a_stale_heartbeat_is_taken_over(tmp_path: Path, monkeypatch) -> None:
    """Ein hart beendeter Prozess darf den Platz nicht dauerhaft blockieren."""
    monkeypatch.setattr("storage.instance_lock._disabled", lambda: False)
    monkeypatch.setattr("storage.instance_lock._try_flock", lambda _fd: True)
    db_path = tmp_path / "metadata.duckdb"
    lock_file = instance_lock_path(str(db_path))
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    lock_file.write_text(
        json.dumps({
            "owner_id": "toter-prozess",
            "pid": 999999,
            "hostname": "irgendwo",
            "container": True,
            "started_at": "2020-01-01 00:00:00",
            "heartbeat": time.time() - (STALE_AFTER_SECONDS + 60),
            "cmdline": "uvicorn",
        }),
        encoding="utf-8",
    )

    lock = InstanceLock(str(db_path))
    assert lock.acquire() is True
    lock.release()


def test_the_holder_keeps_its_heartbeat_fresh(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("storage.instance_lock._disabled", lambda: False)
    monkeypatch.setattr("storage.instance_lock.HEARTBEAT_SECONDS", 0.05)
    db_path = tmp_path / "metadata.duckdb"

    lock = InstanceLock(str(db_path))
    lock.acquire()
    try:
        first = json.loads(instance_lock_path(str(db_path)).read_text(encoding="utf-8"))["heartbeat"]
        deadline = time.time() + 3
        while time.time() < deadline:
            time.sleep(0.1)
            later = json.loads(instance_lock_path(str(db_path)).read_text(encoding="utf-8"))["heartbeat"]
            if later > first:
                break
        else:
            pytest.fail("Heartbeat wurde nicht erneuert")
    finally:
        lock.release()


def test_release_frees_the_slot_immediately(tmp_path: Path, monkeypatch) -> None:
    """Sauberes Beenden darf keine 45 Sekunden Wartezeit hinterlassen."""
    monkeypatch.setattr("storage.instance_lock._disabled", lambda: False)
    monkeypatch.setattr("storage.instance_lock._try_flock", lambda _fd: True)
    db_path = tmp_path / "metadata.duckdb"

    first = InstanceLock(str(db_path))
    first.acquire()
    first.release()

    second = InstanceLock(str(db_path))
    assert second.acquire() is True
    second.release()


def test_instance_lock_is_disabled_under_pytest(tmp_path: Path) -> None:
    """Ohne diesen Ausstieg wuerde die Testsuite sich selbst aussperren."""
    lock = InstanceLock(str(tmp_path / "metadata.duckdb"))
    assert lock.acquire() is False
