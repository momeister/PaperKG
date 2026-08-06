"""Ein ``cs serve``-Kindprozess, angesprochen über NDJSON auf stdin/stdout.

Eine Zeile rein, eine Zeile raus. Dazwischen können Ereigniszeilen liegen
(``{"event": "progress", …}``) — die gehören zur laufenden Anfrage und werden an
einen Callback gereicht, statt die Antwort zu verwirren.

Bewusst kein HTTP: ein Port müsste gewählt, bewacht und der Nutzerin erklärt
werden. Über stdio stirbt das Kind mit dem Eltern-Prozess, und niemand sonst
kommt daran.

Kein Sandbox — wie beim Werkstatt-Terminal und der Analyse-Werkstatt läuft das
Kind mit den Rechten des Backends. Es liest den Projektordner und schreibt genau
eine Datei: seinen Index.
"""

from __future__ import annotations

import json
import os
import queue
import subprocess
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from codegraph.binary import find_binary

#: stderr des Kindes (tracing-Logs) wird gedeckelt mitgeschrieben, damit ein
#: fehlgeschlagener Start eine echte Meldung hat und die Pipe nie vollläuft.
_STDERR_CAP = 16 * 1024

#: Wartezeit auf die ``ready``-Zeile. Das Öffnen einer 50-MB-SQLite ist schnell;
#: mehr als das bedeutet, dass etwas grundsätzlich nicht stimmt.
_STARTUP_TIMEOUT = 30.0

#: Voreinstellung für gewöhnliche Abfragen. Indizieren übergibt seinen eigenen.
DEFAULT_TIMEOUT = 120.0

ProgressHandler = Callable[[dict[str, Any]], None]


class CodeGraphError(RuntimeError):
    """Das Kind hat einen Fehler gemeldet oder ist nicht erreichbar."""


def _sanitized_env() -> dict[str, str]:
    """Geerbte Umgebung, aber ohne Log-Rauschen auf stderr."""
    env = dict(os.environ)
    env.setdefault("RUST_LOG", "warn")
    return env


class CodeGraphClient:
    """Ein offener Arbeitsbereich. Nicht thread-frei — ein Lock serialisiert alles.

    Das Protokoll ist streng abwechselnd (eine Anfrage, dann Zeilen bis zur
    Antwort). Zwei gleichzeitige Aufrufe würden sich die Antworten gegenseitig
    wegnehmen, deshalb wartet der zweite.
    """

    def __init__(
        self,
        root: str | os.PathLike[str],
        db_path: str | os.PathLike[str],
        *,
        config_path: str = "config.yaml",
        binary: str | os.PathLike[str] | None = None,
    ) -> None:
        self.root = Path(root).resolve()
        self.db_path = Path(db_path)
        self._lock = threading.Lock()
        self._stderr: list[str] = []
        self._stderr_len = 0
        self._lines: queue.Queue[str | None] = queue.Queue()
        self.last_used = time.monotonic()

        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        executable = Path(binary) if binary else find_binary(config_path)

        self._process = subprocess.Popen(  # noqa: S603 — argv-Liste, keine Shell
            [str(executable), "serve", str(self.root), "--db", str(self.db_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            cwd=str(self.root),
            env=_sanitized_env(),
            text=True,
            encoding="utf-8",
            errors="replace",
            bufsize=1,
        )

        self._drain = threading.Thread(target=self._drain_stderr, daemon=True)
        self._drain.start()
        self._reader = threading.Thread(target=self._read_stdout, daemon=True)
        self._reader.start()
        self._await_ready()

    # --- Lebenszyklus ------------------------------------------------------

    def _drain_stderr(self) -> None:
        """stderr leerlesen, sonst blockiert das Kind an einer vollen Pipe."""
        stream = self._process.stderr
        if stream is None:
            return
        for line in stream:
            if self._stderr_len < _STDERR_CAP:
                self._stderr.append(line)
                self._stderr_len += len(line)

    def _read_stdout(self) -> None:
        """stdout in eine Queue umfüllen.

        Eine Pipe lässt sich nicht mit Zeitlimit lesen; ein blockierendes
        ``readline`` im Aufruf-Thread hieße, dass ein hängendes Kind das Backend
        mitnimmt — genau der Keil, der das PDF-Parsen schon einmal in den
        Kindprozess gezwungen hat. Über die Queue kostet ein Steckenbleiben nur
        einen Timeout.
        """
        stream = self._process.stdout
        if stream is not None:
            for line in stream:
                self._lines.put(line)
        self._lines.put(None)  # Sentinel: die Pipe ist zu.

    def _await_ready(self) -> None:
        deadline = time.monotonic() + _STARTUP_TIMEOUT
        while time.monotonic() < deadline:
            line = self._readline(deadline - time.monotonic())
            if line is None:
                break
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if message.get("event") == "ready":
                return
        self.close()
        raise CodeGraphError(
            f"CodeSearch startete nicht für {self.root}.\n{self.stderr_tail()}".strip()
        )

    @property
    def alive(self) -> bool:
        return self._process.poll() is None

    def stderr_tail(self, limit: int = 2000) -> str:
        return "".join(self._stderr)[-limit:]

    def close(self) -> None:
        if self._process.poll() is None:
            try:
                if self._process.stdin is not None:
                    self._process.stdin.close()
                self._process.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self._process.kill()
                try:
                    self._process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
        for stream in (self._process.stdout, self._process.stderr):
            try:
                if stream is not None:
                    stream.close()
            except OSError:
                pass

    def __enter__(self) -> CodeGraphClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    # --- Protokoll ---------------------------------------------------------

    def _readline(self, timeout: float) -> str | None:
        """Nächste Zeile, oder ``None`` bei Zeitablauf/geschlossener Pipe."""
        try:
            return self._lines.get(timeout=max(timeout, 0.0))
        except queue.Empty:
            return None

    def call(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT,
        on_progress: ProgressHandler | None = None,
    ) -> Any:
        """Eine Methode aufrufen und ihr Ergebnis zurückgeben.

        ``on_progress`` bekommt jede Fortschrittsmeldung, die vor der Antwort
        eintrifft. Fehler der Gegenseite werden zu :class:`CodeGraphError`.
        """
        with self._lock:
            self.last_used = time.monotonic()
            if not self.alive:
                raise CodeGraphError(
                    f"CodeSearch-Prozess für {self.root} ist beendet.\n{self.stderr_tail()}".strip()
                )

            request = {"id": method, "method": method, "params": params or {}}
            try:
                assert self._process.stdin is not None
                self._process.stdin.write(
                    json.dumps(request, ensure_ascii=False) + "\n"
                )
                self._process.stdin.flush()
            except (OSError, ValueError, AssertionError) as error:
                raise CodeGraphError(f"CodeSearch nicht erreichbar: {error}") from error

            deadline = time.monotonic() + timeout
            while True:
                line = self._readline(deadline - time.monotonic())
                if line is None:
                    # Zeitablauf und Prozessende sehen hier gleich aus, sind aber
                    # verschiedene Fehler — und nach einem Zeitablauf ist die
                    # Verbindung unbrauchbar: käme die Antwort später doch noch,
                    # würde sie dem *nächsten* Aufruf zugeordnet. Also beenden;
                    # der Pool startet beim nächsten Zugriff ein frisches Kind.
                    crashed = not self.alive
                    self.close()
                    detail = self.stderr_tail()
                    reason = (
                        f"CodeSearch-Prozess endete während '{method}'"
                        if crashed
                        else f"CodeSearch antwortete nicht innerhalb von {timeout:.0f}s auf '{method}'"
                    )
                    raise CodeGraphError(f"{reason}.\n{detail}".strip())
                line = line.strip()
                if not line:
                    continue
                try:
                    message = json.loads(line)
                except json.JSONDecodeError:
                    # Fremde Zeile auf stdout — ignorieren statt daran zu sterben.
                    continue

                event = message.get("event")
                if event == "progress":
                    if on_progress is not None:
                        on_progress(message.get("data") or {})
                    continue
                if event is not None:
                    continue

                if message.get("ok"):
                    return message.get("result")
                raise CodeGraphError(str(message.get("error") or "Unbekannter Fehler"))

    # --- Bequemlichkeiten --------------------------------------------------

    def ping(self) -> bool:
        try:
            return bool(self.call("ping", timeout=10).get("pong"))
        except (CodeGraphError, AttributeError):
            return False

    def stats(self) -> dict[str, Any]:
        return self.call("stats")

    def index(
        self, *, on_progress: ProgressHandler | None = None, timeout: float = 3600.0
    ) -> dict[str, Any]:
        return self.call("index", timeout=timeout, on_progress=on_progress)
