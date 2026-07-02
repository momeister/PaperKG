"""Lokale, reproduzierbare Ausführung KI-geschriebener Analyse-Skripte.

Ein Lauf ist immer ein eigener Ordner innerhalb eines verwalteten
Code-Werkstatt-Projekts, damit das exakte Skript, seine Eingaben und Ausgaben auf
der Platte bleiben — git-versioniert und in jedem Editor öffnenbar. Die Ausführung
ist ein schlichter ``subprocess`` (kein Shell), mit dem Lauf-Ordner als ``cwd``,
einer bereinigten Umgebung, festem Seed und hartem Timeout — dieselben
Containment-Garantien, auf die sich die Werkstatt schon heute stützt.

Der Kontrakt mit dem Skript:
  * Ausgaben (Figuren/Tabellen/Daten) werden nach ``outputs/`` geschrieben.
  * Eingaben liegen (kopiert) unter ``inputs/`` und sind relativ erreichbar.
  * Determinismus über den injizierten Seed-Präambel-Block + ``PYTHONHASHSEED``.

**Kein Sandbox.** Der Subprozess läuft mit denselben Rechten wie das Backend (wie
die bestehende Werkstatt-Terminal-/Jupyter-Fläche). Echte Isolation (Docker,
``--network none``) ist ein optionaler späterer Modus; hier zählt Pfad-Containment,
Timeout und ein bereinigtes Environment, nicht Kernel-Isolation.
"""
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from workspace.manager import resolve_within

# Klassifikation der erzeugten Dateien für die UI (Figur inline rendern, Tabelle als
# CSV-Vorschau, Rest zum Download).
_FIGURE_EXT = {".png", ".jpg", ".jpeg", ".svg", ".pdf", ".webp", ".gif"}
_TABLE_EXT = {".csv", ".tsv", ".xlsx", ".parquet"}
# Alles andere (.json/.txt/.npy/…) fällt auf kind="data" — keine eigene Liste nötig.

# Obergrenzen, damit ein pathologisches Skript nicht die Platte/Response flutet.
MAX_ARTIFACTS = 60
MAX_ARTIFACT_BYTES = 25 * 1024 * 1024  # 25 MB pro Datei
DEFAULT_TIMEOUT_SECONDS = 120
DEFAULT_SEED = 42

# Name der Unterordner im Lauf-Verzeichnis.
OUTPUTS_DIRNAME = "outputs"
INPUTS_DIRNAME = "inputs"
SCRIPT_FILENAME = "script.py"
STDOUT_FILENAME = "stdout.log"


@dataclass
class ArtifactInfo:
    """Eine vom Skript erzeugte Ausgabedatei unter ``outputs/``."""

    filename: str
    rel_path: str  # relativ zum Lauf-Ordner, z.B. "outputs/chart.png"
    kind: str  # figure | table | data | log
    size: int
    sha256: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "filename": self.filename,
            "rel_path": self.rel_path,
            "kind": self.kind,
            "size": self.size,
            "sha256": self.sha256,
        }


@dataclass
class RunResult:
    """Ergebnis eines Skriptlaufs — vollständig serialisierbar für ``run.json``."""

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool
    duration_s: float
    artifacts: list[ArtifactInfo] = field(default_factory=list)
    combined_hash: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.timed_out

    def as_dict(self) -> dict[str, Any]:
        return {
            "returncode": self.returncode,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "timed_out": self.timed_out,
            "duration_s": round(self.duration_s, 3),
            "combined_hash": self.combined_hash,
            "artifacts": [a.as_dict() for a in self.artifacts],
        }


def classify_artifact(path: Path) -> str:
    """Grobklassifikation einer Ausgabedatei nach Endung."""
    ext = path.suffix.lower()
    if ext in _FIGURE_EXT:
        return "figure"
    if ext in _TABLE_EXT:
        return "table"
    if path.name == STDOUT_FILENAME or ext == ".log":
        return "log"
    return "data"


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def collect_artifacts(run_dir: Path) -> list[ArtifactInfo]:
    """Alle Dateien unter ``outputs/`` einsammeln, klassifizieren und hashen.

    Rekursiv, deterministisch sortiert. Zu große Dateien werden übersprungen (mit
    ``kind="log"``-Warnung im Aufrufer nicht nötig — hier still). Ergebnisliste ist
    stabil sortiert, damit der kombinierte Hash reproduzierbar ist.
    """
    outputs_dir = (run_dir / OUTPUTS_DIRNAME).resolve()
    if not outputs_dir.is_dir():
        return []
    found: list[ArtifactInfo] = []
    for path in sorted(outputs_dir.rglob("*"), key=lambda p: p.as_posix().lower()):
        if not path.is_file() or path.is_symlink():
            continue
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > MAX_ARTIFACT_BYTES:
            continue
        rel = path.relative_to(run_dir).as_posix()
        found.append(
            ArtifactInfo(
                filename=path.name,
                rel_path=rel,
                kind=classify_artifact(path),
                size=size,
                sha256=_sha256_file(path),
            )
        )
        if len(found) >= MAX_ARTIFACTS:
            break
    return found


def _combined_hash(artifacts: list[ArtifactInfo]) -> str:
    """Ein Hash über alle Artefakt-Hashes — die „Fingerabdruck"-Basis für WP4."""
    h = hashlib.sha256()
    for art in sorted(artifacts, key=lambda a: a.rel_path):
        h.update(art.rel_path.encode("utf-8"))
        h.update(b"\0")
        h.update(art.sha256.encode("utf-8"))
        h.update(b"\0")
    return h.hexdigest()


def seed_preamble(seed: int) -> str:
    """Deterministik-Präambel, die vor den KI-Code gesetzt wird.

    Fixiert ``random``/``numpy``-Seeds, erzwingt das ``Agg``-Matplotlib-Backend
    (headless) und legt den ``outputs/``-Ordner an, sodass das Skript einfach dorthin
    schreiben kann. Bewusst defensiv (``try/except``), damit fehlende optionale Libs
    das Skript nicht schon in der Präambel abbrechen.
    """
    return (
        "# --- PaperKG Analyse-Werkstatt: Reproduzierbarkeits-Präambel (auto) ---\n"
        "import os as _os, random as _random\n"
        f"_SEED = {int(seed)}\n"
        '_os.environ.setdefault("PYTHONHASHSEED", str(_SEED))\n'
        "_random.seed(_SEED)\n"
        "try:\n"
        "    import numpy as _np\n"
        "    _np.random.seed(_SEED)\n"
        "except Exception:\n"
        "    pass\n"
        "try:\n"
        "    import matplotlib as _mpl\n"
        '    _mpl.use("Agg")\n'
        "except Exception:\n"
        "    pass\n"
        'OUTPUT_DIR = "outputs"\n'
        "_os.makedirs(OUTPUT_DIR, exist_ok=True)\n"
        "# --- Ende Präambel; ab hier der von der KI generierte Code ---\n\n"
    )


def build_script(code: str, seed: int) -> str:
    """Vollständiger Skript-Text = Präambel + KI-Code."""
    return seed_preamble(seed) + (code or "").replace("\r\n", "\n")


def _sanitized_env(seed: int) -> dict[str, str]:
    """Umgebung für den Subprozess: geerbt, aber deterministisch + headless.

    Wir erben die volle Umgebung (Windows braucht ``SystemRoot``/``PATH`` schon zum
    Python-Start), setzen aber Seed/Backend und schalten ``__pycache__``-Schreiben ab,
    damit der Lauf-Ordner sauber bleibt.
    """
    env = dict(os.environ)
    env["PYTHONHASHSEED"] = str(int(seed))
    env["MPLBACKEND"] = "Agg"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    # Best-effort Hinweis für Bibliotheken, die Proxy-Umgebungsvariablen respektieren.
    # (Kein echter Netz-Cutoff — das leistet erst der optionale Docker-Modus.)
    env.setdefault("NO_PROXY", "*")
    return env


def run_script(
    run_dir: str | os.PathLike[str],
    code: str,
    *,
    python_executable: str | None = None,
    seed: int = DEFAULT_SEED,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> RunResult:
    """Schreibe ``script.py`` in ``run_dir`` und führe es reproduzierbar aus.

    ``run_dir`` muss ein existierendes Verzeichnis sein (typischerweise unterhalb
    eines verwalteten Werkstatt-Projekts). Ausgaben werden aus ``run_dir/outputs``
    eingesammelt. Ein ``stdout.log`` mit stdout+stderr wird immer geschrieben, damit
    auch ein Lauf ohne erzeugte Datei nachvollziehbar ist.
    """
    run_path = Path(run_dir).resolve()
    if not run_path.is_dir():
        raise ValueError(f"Lauf-Ordner existiert nicht: {run_path}")

    outputs_dir = run_path / OUTPUTS_DIRNAME
    outputs_dir.mkdir(parents=True, exist_ok=True)

    script_path = run_path / SCRIPT_FILENAME
    script_path.write_text(build_script(code, seed), encoding="utf-8", newline="\n")

    python = python_executable or sys.executable or "python"
    env = _sanitized_env(seed)

    start = time.monotonic()
    timed_out = False
    try:
        proc = subprocess.run(
            [python, SCRIPT_FILENAME],
            cwd=str(run_path),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            timeout=timeout,
            # kein shell=True, argv-Liste — keine Shell-Injektion.
        )
        returncode, stdout, stderr = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        returncode = -1
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", errors="replace")
        stderr = (exc.stderr or "")
        if isinstance(stderr, bytes):
            stderr = stderr.decode("utf-8", errors="replace")
        stderr = (stderr + f"\n[Timeout nach {timeout:.0f}s abgebrochen]").strip()
    except OSError as exc:
        returncode = -1
        stdout = ""
        stderr = f"Skript konnte nicht gestartet werden: {exc}"
    duration = time.monotonic() - start

    # stdout+stderr immer als log-Artefakt festhalten.
    log_text = f"$ {python} {SCRIPT_FILENAME}\n\n[stdout]\n{stdout}\n\n[stderr]\n{stderr}\n"
    (outputs_dir / STDOUT_FILENAME).write_text(log_text, encoding="utf-8", newline="\n")

    artifacts = collect_artifacts(run_path)
    return RunResult(
        returncode=returncode,
        stdout=stdout,
        stderr=stderr,
        timed_out=timed_out,
        duration_s=duration,
        artifacts=artifacts,
        combined_hash=_combined_hash(artifacts),
    )


def stage_input(run_dir: str | os.PathLike[str], src: str | os.PathLike[str], name: str) -> str:
    """Eine Eingabedatei sicher nach ``inputs/<name>`` im Lauf-Ordner kopieren.

    Der Zielname wird über :func:`workspace.manager.resolve_within` gegen einen
    Ausbruch aus dem Lauf-Ordner geprüft. Gibt den relativen Pfad zurück, den das
    Skript nutzen kann (z.B. ``"inputs/daten.csv"``).
    """
    run_path = Path(run_dir).resolve()
    rel = f"{INPUTS_DIRNAME}/{Path(name).name}"
    target = resolve_within(run_path, rel)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(Path(src).read_bytes())
    return rel
