"""Schutzhülle fürs PDF-Parsen: eigener Prozess, dynamisches RAM-Limit, langer Timeout.

Warum das existiert: eine einzige pathologische PDF-Seite (grosse Vektor-Grafik,
z.B. ein scRNA-seq-Scatterplot) bringt pdfminer/pdfplumber *und* pypdf dazu,
unbegrenzt Speicher zu allokieren, ohne je fertig zu werden. Gemessen an
`openalex__…europepmc_42260142_v1.pdf` Seite 22: ~11 MB/s Zuwachs, kein Ende.
Im Backend-Prozess bedeutet das: RSS läuft auf zweistellige GB, uvicorn hört auf
zu lauschen, der OOM-Killer räumt den Server ab — und der Nutzer sieht nur, dass
`GET /extraction/batch/{id}/items` 404 liefert, weil die Job-Zeile nie geschrieben
wurde.

Ein Timeout allein reicht nicht (der Speicher ist vorher weg), und eine
In-Process-Grenze auch nicht (ein MemoryError im Backend-Thread hilft nichts,
wenn schon 10 GB belegt sind). Deshalb: Parsen im Kindprozess. Wird er gekillt,
gibt das Betriebssystem den Speicher sofort komplett zurück.

Qualität bleibt gleich: der Kindprozess führt exakt denselben Parser-Code aus.
Zusätzlich schreibt er jede fertige Seite sofort weg — wird er auf Seite 22
abgeschossen, bleiben die Seiten 0–21 erhalten statt alles zu verlieren.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

_CHILD_ENV_FLAG = "SCIENCEKG_PDF_CHILD"

# Defaults — bewusst grosszügig beim Timeout: auf langsamer Hardware darf ein
# dickes PDF echt lange brauchen; abgebrochen werden soll nur, was nie fertig wird.
_DEFAULTS: dict[str, Any] = {
    "guard_enabled": True,
    "pdf_timeout_seconds": 2400.0,  # 40 min
    "memory_fraction": 0.6,  # Anteil des *freien* RAM, den das Kind nutzen darf
    "memory_min_mb": 1024,
    "memory_max_mb": 16384,
    "poll_interval_seconds": 0.5,
}

_config_cache: dict[str, Any] | None = None


def _load_config() -> dict[str, Any]:
    """`parsing:`-Block aus config.yaml, mit Env-Overrides. Einmal gelesen, dann gecacht."""
    global _config_cache
    if _config_cache is not None:
        return _config_cache

    values = dict(_DEFAULTS)
    try:
        import yaml

        config_path = Path(os.environ.get("SCIENCEKG_CONFIG", "config.yaml"))
        if config_path.exists():
            raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
            block = raw.get("parsing") or {}
            for key in _DEFAULTS:
                if key in block and block[key] is not None:
                    values[key] = block[key]
    except Exception:
        # Config kaputt/fehlt: Defaults sind sicher, das Parsen darf daran nicht scheitern.
        pass

    env_overrides = {
        "guard_enabled": "SCIENCEKG_PDF_GUARD",
        "pdf_timeout_seconds": "SCIENCEKG_PDF_TIMEOUT",
        "memory_fraction": "SCIENCEKG_PDF_MEMORY_FRACTION",
        "memory_max_mb": "SCIENCEKG_PDF_MEMORY_MAX_MB",
    }
    for key, env_name in env_overrides.items():
        raw_value = os.environ.get(env_name)
        if raw_value is None:
            continue
        try:
            if key == "guard_enabled":
                values[key] = raw_value.strip().lower() not in {"0", "false", "no"}
            else:
                values[key] = float(raw_value)
        except Exception:
            pass

    values["guard_enabled"] = bool(values["guard_enabled"])
    values["pdf_timeout_seconds"] = max(30.0, float(values["pdf_timeout_seconds"]))
    values["memory_fraction"] = min(0.9, max(0.05, float(values["memory_fraction"])))
    _config_cache = values
    return values


def available_memory_bytes() -> int | None:
    """Aktuell *verfügbarer* Arbeitsspeicher, oder None wenn nicht ermittelbar."""
    try:
        import psutil

        return int(psutil.virtual_memory().available)
    except Exception:
        pass
    try:  # Linux ohne psutil
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) * 1024
    except Exception:
        pass
    try:  # POSIX-Fallback: gesamter RAM, nicht der freie
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    except Exception:
        return None


def memory_limit_bytes() -> int:
    """RAM-Budget fürs Kind — richtet sich nach dem, was der Rechner gerade frei hat.

    Ein fester Wert wäre auf einem 8-GB-Laptop zu gross und auf einer 64-GB-Kiste
    unnötig knauserig; deshalb ein Anteil des freien Speichers, gedeckelt nach
    unten (sonst scheitern normale PDFs) und nach oben (sonst zieht ein Ausreisser
    die Maschine wieder ins Swappen).
    """
    config = _load_config()
    floor_bytes = int(float(config["memory_min_mb"]) * 1024 * 1024)
    ceiling_bytes = int(float(config["memory_max_mb"]) * 1024 * 1024)
    available = available_memory_bytes()
    if not available:
        return floor_bytes
    budget = int(available * float(config["memory_fraction"]))
    return max(floor_bytes, min(ceiling_bytes, budget))


def _process_rss_bytes(pid: int) -> int | None:
    try:
        import psutil

        return int(psutil.Process(pid).memory_info().rss)
    except Exception:
        pass
    try:
        status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        for line in status.splitlines():
            if line.startswith("VmRSS:"):
                return int(line.split()[1]) * 1024
    except Exception:
        return None
    return None


def _spawn_child(file_path: str, paper_id: str, progress_path: str, result_path: str, memory_cap: int):
    """`python -m parsing.pdf_child …` starten. None, wenn kein Subprozess möglich ist."""
    import subprocess

    if getattr(sys, "frozen", False):
        # PyInstaller-Sidecar: `sys.executable` ist die App selbst, `-m` gibt es dort nicht.
        return None
    repo_root = str(Path(__file__).resolve().parent.parent)
    env = dict(os.environ)
    env[_CHILD_ENV_FLAG] = "1"
    env["PYTHONPATH"] = os.pathsep.join(filter(None, [repo_root, env.get("PYTHONPATH", "")]))
    try:
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "parsing.pdf_child",
                str(file_path),
                paper_id,
                progress_path,
                result_path,
                str(memory_cap),
            ],
            cwd=repo_root,
            env=env,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
    except Exception:
        return None


def _pages_from_progress(progress_path: str) -> list[str]:
    """Seiten einsammeln, die das Kind vor dem Abschuss noch fertig bekommen hat."""
    # Nach Seitenindex, letzter Eintrag gewinnt: fällt pdfplumber auf halber Strecke
    # aus und pypdf parst dieselben Seiten erneut, sollen die neueren Texte zählen.
    pages: dict[int, str] = {}
    try:
        with open(progress_path, encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except Exception:
                    continue  # abgeschnittene letzte Zeile: Kind starb mitten im Schreiben
                pages[int(entry.get("page", len(pages)))] = str(entry.get("text", ""))
    except FileNotFoundError:
        return []
    except Exception:
        return []
    return [pages[index] for index in sorted(pages)]


def guarded_parse(file_path: str | Path, paper_id: str):
    """`MarkerParser.parse_direct` im Kindprozess, mit RAM- und Zeitgrenze.

    Läuft alles normal, ist das Ergebnis Byte-für-Byte dasselbe wie ohne Wächter.
    Reisst das Kind die Grenze, kommen die bis dahin fertigen Seiten zurück und
    `meta` erklärt, was passiert ist.
    """
    from parsing.marker_parser import PAGE_BREAK, MarkerParser

    config = _load_config()
    if not config["guard_enabled"] or os.environ.get(_CHILD_ENV_FLAG) == "1":
        return MarkerParser().parse_direct(file_path, paper_id)

    memory_cap = memory_limit_bytes()
    timeout_seconds = float(config["pdf_timeout_seconds"])
    poll_interval = float(config["poll_interval_seconds"])

    tmp_dir = tempfile.mkdtemp(prefix="sciencekg-pdf-")
    progress_path = os.path.join(tmp_dir, "pages.jsonl")
    result_path = os.path.join(tmp_dir, "result.json")

    process = _spawn_child(str(file_path), paper_id, progress_path, result_path, memory_cap)
    if process is None:
        # Kein Subprozess möglich (eingefrorene Builds, exotische Plattform):
        # lieber ungeschützt parsen als gar nicht.
        return MarkerParser().parse_direct(file_path, paper_id)

    try:
        started = time.monotonic()
        reason: str | None = None
        peak_rss = 0
        while True:
            if process.poll() is not None:
                break
            elapsed = time.monotonic() - started
            if elapsed > timeout_seconds:
                reason = f"Timeout nach {int(elapsed)}s"
                break
            rss = _process_rss_bytes(process.pid)
            if rss:
                peak_rss = max(peak_rss, rss)
                if rss > memory_cap:
                    reason = f"RAM-Limit überschritten ({rss // (1024 * 1024)} MB > {memory_cap // (1024 * 1024)} MB)"
                    break
            time.sleep(poll_interval)

        if reason is not None:
            process.kill()
            try:
                process.wait(timeout=10)
            except Exception:
                pass

        if reason is None and os.path.exists(result_path):
            try:
                payload = json.loads(Path(result_path).read_text(encoding="utf-8"))
                meta = dict(payload.get("meta") or {})
                meta["guard_memory_limit_mb"] = memory_cap // (1024 * 1024)
                meta["guard_peak_rss_mb"] = peak_rss // (1024 * 1024)
                return MarkerParser.build_document(
                    paper_id=payload.get("paper_id", paper_id),
                    text=payload.get("text", ""),
                    page_count=int(payload.get("page_count", 0)),
                    meta=meta,
                )
            except Exception as exc:
                reason = reason or f"Ergebnis unlesbar: {exc}"

        if reason is None:
            # Kind ist gestorben, ohne ein Ergebnis zu hinterlassen (z.B. RLIMIT_AS
            # hat mitten in der Allokation zugeschlagen).
            stderr_tail = ""
            try:
                if process.stderr is not None:
                    stderr_tail = process.stderr.read().decode("utf-8", errors="replace")[-400:]
            except Exception:
                pass
            reason = f"Kindprozess beendet mit exitcode={process.returncode}"
            if stderr_tail.strip():
                reason = f"{reason}: {stderr_tail.strip()}"

        pages = _pages_from_progress(progress_path)
        text = PAGE_BREAK.join(pages).strip()
        return MarkerParser.build_document(
            paper_id=paper_id,
            text=text,
            page_count=len(pages),
            meta={
                "source_path": str(file_path),
                "extraction_method": "pdfplumber_partial" if pages else "guard_aborted",
                "chars_extracted": len(text),
                "guard_aborted": True,
                "guard_reason": reason,
                "guard_pages_recovered": len(pages),
                "guard_memory_limit_mb": memory_cap // (1024 * 1024),
                "guard_timeout_seconds": int(timeout_seconds),
            },
        )
    finally:
        if process.poll() is None:
            try:
                process.kill()
                process.wait(timeout=5)
            except Exception:
                pass
        for stream in (process.stderr,):
            try:
                if stream is not None:
                    stream.close()
            except Exception:
                pass
        for path in (progress_path, result_path):
            try:
                os.unlink(path)
            except OSError:
                pass
        try:
            os.rmdir(tmp_dir)
        except OSError:
            pass


__all__ = [
    "available_memory_bytes",
    "guarded_parse",
    "memory_limit_bytes",
]
