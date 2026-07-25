"""Crash-sichere JSON-Sidecars (``data/projects.json`` & Co.).

Die Projektzuordnung lebt nicht in DuckDB, sondern in einer Handvoll JSON-Dateien
neben der Datenbank. Sie wurden bisher mit ``path.write_text(...)`` geschrieben —
das kuerzt die Datei auf 0 Bytes und schreibt dann neu. Wird der Prozess in genau
diesem Moment hart beendet (die Tauri-Shell nutzt ``child.kill()`` = SIGKILL,
``src-tauri/src/lib.rs``), bleibt halbes JSON zurueck. Der Leser hat das frueher
still als ``{}`` interpretiert und der naechste Schreibvorgang hat dieses ``{}``
dann dauerhaft festgeschrieben: aus einem Absturz wurde echter Datenverlust.

Dieses Modul dreht beides um:

* ``write_json_atomic`` schreibt in eine Temp-Datei im *selben* Verzeichnis,
  ``fsync``t sie und ersetzt das Original per ``os.replace`` — auf POSIX und
  Windows ein atomarer Rename. Ein Absturz laesst damit immer entweder die alte
  oder die neue Datei vollstaendig zurueck, nie etwas dazwischen.
* ``read_json_dict`` meldet kaputtes JSON *laut* (``CorruptJsonError``) und legt
  die defekte Datei als ``<name>.corrupt-<ts>`` beiseite, statt sie zu verschweigen.
  Ein sichtbarer Fehler ist immer besser als eine still geleerte Bibliothek.
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from pathlib import Path
from typing import Any

__all__ = ["CorruptJsonError", "write_json_atomic", "read_json_dict", "quarantine_file"]


class CorruptJsonError(RuntimeError):
    """Eine JSON-Sidecar-Datei ist unlesbar; das Original wurde beiseitegelegt."""

    def __init__(self, path: Path, quarantined: Path | None, reason: str) -> None:
        self.path = path
        self.quarantined = quarantined
        self.reason = reason
        hint = f" Die defekte Datei liegt jetzt unter {quarantined.name}." if quarantined else ""
        super().__init__(
            f"{path.name} ist beschaedigt und konnte nicht gelesen werden ({reason})."
            f"{hint} Stelle sie aus einem Backup wieder her, bevor du weiterarbeitest — "
            "sonst ueberschreibt der naechste Schreibvorgang den Rest."
        )


def write_json_atomic(path: Path, data: Any, *, indent: int = 2, sort_keys: bool = True) -> None:
    """Schreibe ``data`` als JSON nach ``path`` — ganz oder gar nicht."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(data, indent=indent, sort_keys=sort_keys, ensure_ascii=False)

    # Temp-Datei muss im selben Verzeichnis liegen: os.replace ist nur innerhalb
    # eines Dateisystems atomar, und data/ kann ein eigener Mount sein (Docker).
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise

    # Den Verzeichniseintrag selbst durablen — ohne das kann der Rename bei einem
    # Stromausfall verloren gehen. Auf Windows nicht unterstuetzt, daher best effort.
    try:
        dir_fd = os.open(str(path.parent), os.O_RDONLY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def quarantine_file(path: Path) -> Path | None:
    """Verschiebe eine defekte Datei nach ``<name>.corrupt-<zeitstempel>``."""
    path = Path(path)
    target = path.with_name(f"{path.name}.corrupt-{time.strftime('%Y%m%d-%H%M%S')}")
    try:
        os.replace(path, target)
    except OSError:
        return None
    return target


def read_json_dict(path: Path) -> dict[str, Any]:
    """Lies ein JSON-Objekt. Fehlende Datei -> ``{}``, kaputte -> ``CorruptJsonError``."""
    path = Path(path)
    if not path.exists():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as error:
        raise CorruptJsonError(path, None, str(error)) from error
    if not raw.strip():
        # Leere Datei: von einem abgebrochenen truncate-then-write. Nicht als
        # "keine Projekte" durchwinken.
        raise CorruptJsonError(path, quarantine_file(path), "Datei ist leer")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as error:
        raise CorruptJsonError(path, quarantine_file(path), f"ungueltiges JSON: {error}") from error
    if not isinstance(data, dict):
        raise CorruptJsonError(path, quarantine_file(path), f"erwartet wurde ein Objekt, gefunden {type(data).__name__}")
    return data
