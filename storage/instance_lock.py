"""Ein-Instanz-Guard fuer das Backend.

DuckDB laesst nur *einen* schreibenden Prozess pro Datenbankdatei zu. Laufen aus
Versehen zwei Backends auf demselben ``data/`` — z.B. ein manuelles ``uvicorn``
neben dem Tauri-Sidecar, oder ein vergessener ``docker compose``-Stack neben einem
Host-Start — bekam bisher jeder Request eine ``duckdb.IOException``. Das Frontend
rendert Fehler als leere Listen, also sah der Konflikt aus wie "alle Projekte und
Paper sind geloescht".

Der Guard verlagert diesen Konflikt an den *Start*: das zweite Backend beendet
sich sofort mit einer klaren Meldung, statt eine kaputte UI zu bedienen.

**Zwei Mechanismen, weil einer nicht reicht:**

1. **OS-Advisory-Lock** (``fcntl.flock`` / ``msvcrt.locking``) auf
   ``data/.backend.lock``. Praezise und sofort, und der Kernel gibt ihn auch bei
   SIGKILL frei — es gibt keine "stale locks", die man aufraeumen muesste.
   Er greift aber nur zwischen Prozessen **auf demselben Kernel**.

2. **Heartbeat im Dateiinhalt.** Docker Desktop (Linux/macOS/Windows) laesst den
   Daemon in einer VM laufen; ein Bind-Mount geht dann durch virtiofs/gRPC-FUSE,
   und POSIX-Locks werden **nicht** durchgereicht. Nachgemessen: bei laufendem
   Container konnte der Host dieselbe ``metadata.duckdb`` schreibend oeffnen —
   *auch DuckDBs eigener Lock greift dort nicht*. Zwei Schreiber auf einer Datei
   sind schlimmer als ein sichtbarer Fehler, deshalb traegt der Halter zusaetzlich
   alle ``HEARTBEAT_SECONDS`` einen Zeitstempel in die Datei. Ein Starter, der
   einen frischen fremden Heartbeat sieht, tritt zurueck. Dateiinhalt wird auch
   ueber die VM-Grenze sichtbar.

Ein Heartbeat aelter als ``STALE_AFTER_SECONDS`` gilt als verwaist (hart
beendeter Prozess) und wird uebernommen.

Abschaltbar mit ``SCIENCEKG_DISABLE_INSTANCE_LOCK=1``.
"""
from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

__all__ = [
    "InstanceLock",
    "InstanceLockError",
    "instance_lock_path",
    "HEARTBEAT_SECONDS",
    "STALE_AFTER_SECONDS",
]

_LOCK_FILENAME = ".backend.lock"

#: Wie oft der Halter seinen Zeitstempel erneuert.
HEARTBEAT_SECONDS = 10.0
#: Ab wann ein Heartbeat als verwaist gilt. Grosszuegiges Vielfaches, damit ein
#: kurz haengender Prozess (GC-Pause, langsame VM-FS-Schicht) nicht verdraengt wird.
STALE_AFTER_SECONDS = 45.0


class InstanceLockError(RuntimeError):
    """Ein anderes Backend arbeitet bereits auf diesem Datenverzeichnis."""


def instance_lock_path(metadata_db_path: str | os.PathLike[str]) -> Path:
    """Lockdatei neben der Metadaten-DB (also im gemounteten ``data/``)."""
    return Path(metadata_db_path).resolve().parent / _LOCK_FILENAME


def _disabled() -> bool:
    if os.getenv("SCIENCEKG_DISABLE_INSTANCE_LOCK", "").strip().lower() in {"1", "true", "yes"}:
        return True
    # Die Testsuite startet die App vielfach parallel gegen tmp-Verzeichnisse und
    # soll sich nicht selbst aussperren.
    return "pytest" in sys.modules


def _in_container() -> bool:
    if os.path.exists("/.dockerenv"):
        return True
    try:
        return "docker" in Path("/proc/1/cgroup").read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return False


def _read_record(path: Path) -> dict[str, Any] | None:
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    if not raw.strip():
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def describe_holder(record: dict[str, Any] | None) -> str:
    """Menschenlesbare Beschreibung des haltenden Prozesses."""
    if not record:
        return "unbekannter Prozess"
    where = "in einem Docker-Container" if record.get("container") else f"auf {record.get('hostname', '?')}"
    return (
        f"PID {record.get('pid', '?')} {where}, gestartet {record.get('started_at', '?')}"
        f" ({record.get('cmdline', '?')})"
    )


def _try_flock(fd: int) -> bool:
    """Nicht-blockierender exklusiver Lock; ``False`` wenn schon vergeben.

    Greift nur zwischen Prozessen auf demselben Kernel — ueber eine Docker-Desktop-
    VM-Grenze hinweg gibt er immer ``True`` zurueck. Dafuer gibt es den Heartbeat.
    """
    try:
        import fcntl
    except ImportError:
        pass
    else:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return True
        except OSError:
            return False
    try:
        import msvcrt
    except ImportError:
        return True  # Weder fcntl noch msvcrt: nur der Heartbeat schuetzt.
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


class InstanceLock:
    """Haelt den Lock fuer die Lebensdauer des Prozesses."""

    def __init__(self, metadata_db_path: str | os.PathLike[str]) -> None:
        self.path = instance_lock_path(metadata_db_path)
        self.owner_id = uuid.uuid4().hex
        self._fd: int | None = None
        self._stop = threading.Event()
        self._heartbeat: threading.Thread | None = None

    # ------------------------------------------------------------------ #

    def _record(self) -> dict[str, Any]:
        return {
            "owner_id": self.owner_id,
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "container": _in_container(),
            "started_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            "heartbeat": time.time(),
            "cmdline": " ".join(sys.argv)[:400],
        }

    def _write_record(self) -> None:
        if self._fd is None:
            return
        payload = json.dumps(self._record(), indent=2).encode("utf-8")
        os.ftruncate(self._fd, 0)
        os.lseek(self._fd, 0, os.SEEK_SET)
        os.write(self._fd, payload)
        os.fsync(self._fd)

    def _foreign_live_holder(self) -> dict[str, Any] | None:
        """Fremder Halter mit frischem Heartbeat, sonst ``None``."""
        record = _read_record(self.path)
        if not record or record.get("owner_id") == self.owner_id:
            return None
        try:
            age = time.time() - float(record.get("heartbeat") or 0)
        except (TypeError, ValueError):
            return None
        return record if age < STALE_AFTER_SECONDS else None

    def _run_heartbeat(self) -> None:
        while not self._stop.wait(HEARTBEAT_SECONDS):
            try:
                self._write_record()
            except OSError:
                # Verzeichnis weg oder FS voll — kein Grund, das Backend zu killen.
                return

    # ------------------------------------------------------------------ #

    def acquire(self) -> bool:
        """``True`` wenn gehalten, ``False`` wenn deaktiviert. Sonst ``InstanceLockError``."""
        if _disabled():
            return False
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(self.path), os.O_RDWR | os.O_CREAT, 0o644)

        # 1. Selber Kernel: der Advisory-Lock entscheidet sofort und eindeutig.
        if not _try_flock(fd):
            holder = describe_holder(_read_record(self.path))
            os.close(fd)
            raise InstanceLockError(self._conflict_message(holder))

        # 2. Ueber eine Container-/VM-Grenze hinweg greift der Lock nicht — dort
        #    zaehlt nur, ob jemand anderes gerade noch Herzschlaege schreibt.
        foreign = self._foreign_live_holder()
        if foreign is not None:
            os.close(fd)
            raise InstanceLockError(self._conflict_message(describe_holder(foreign)))

        self._fd = fd
        self._write_record()

        # 3. Startet ein zweites Backend im selben Moment, haben beide Schritt 2
        #    bestanden. Kurz warten und nachsehen, wessen Eintrag ueberlebt hat.
        time.sleep(0.35)
        current = _read_record(self.path)
        if current and current.get("owner_id") != self.owner_id:
            self.release()
            raise InstanceLockError(self._conflict_message(describe_holder(current)))

        self._stop.clear()
        self._heartbeat = threading.Thread(target=self._run_heartbeat, name="instance-lock", daemon=True)
        self._heartbeat.start()
        return True

    def _conflict_message(self, holder: str) -> str:
        return (
            f"Ein anderes ScienceKG-Backend arbeitet bereits auf {self.path.parent} ({holder}).\n"
            "DuckDB erlaubt nur eine schreibende Instanz. Beende die andere "
            "(uvicorn, Tauri-Sidecar, Streamlit oder 'docker compose down') und starte neu.\n"
            f"Wurde sie hart beendet, ist der Platz nach spaetestens {int(STALE_AFTER_SECONDS)} Sekunden wieder frei.\n"
            "Es gehen keine Daten verloren. Notfalls mit SCIENCEKG_DISABLE_INSTANCE_LOCK=1 uebergehen."
        )

    def release(self) -> None:
        self._stop.set()
        if self._heartbeat is not None:
            self._heartbeat.join(timeout=2)
            self._heartbeat = None
        if self._fd is None:
            return
        # Datei leeren statt loeschen: ein gleichzeitig startender Prozess haelt
        # dann einen leeren Eintrag in der Hand (= frei) statt eines veralteten.
        try:
            os.ftruncate(self._fd, 0)
            os.fsync(self._fd)
        except OSError:
            pass
        try:
            os.close(self._fd)  # gibt den flock implizit frei
        except OSError:
            pass
        self._fd = None

    def __enter__(self) -> "InstanceLock":
        self.acquire()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.release()
