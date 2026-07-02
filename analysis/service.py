"""Orchestrierung der Analyse-Werkstatt: Planen → Ausführen → Provenance → Persistenz.

Bindet :mod:`analysis.planner`, :mod:`analysis.runner`, :mod:`workspace.manager`
(verwaltete git-Projekte) und :class:`storage.metadata_db.MetadataDB` zusammen. Ein
Lauf erzeugt einen echten Ordner unter einem verwalteten Code-Werkstatt-Projekt mit:

    <projekt>/<run_slug>/
        script.py       — Präambel (Seed/Agg) + KI-Code
        inputs/         — kopierte Eingabedateien (Datensätze/Uploads)
        outputs/        — erzeugte Figuren/Tabellen/Daten + stdout.log
        run.json        — vollständige Provenance (Env, Seed, Provider, Modell,
                          Planungs-Verlauf, Ergebnis, Hashes, Zeitstempel)
        README.md       — Klartext: was macht der Lauf, wie reproduziere ich ihn

Nach jedem Lauf wird der Projektordner committet (git), sodass „was wurde gebaut"
als Diff sichtbar ist und Revisionen eine echte Versionshistorie bilden.
"""
from __future__ import annotations

import json
import platform
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from analysis import planner, runner
from storage.metadata_db import MetadataDB
from workspace import manager as workspace_manager

# Name des gemeinsamen, verwalteten Werkstatt-Projekts, in dem Analyse-Läufe als
# Unterordner liegen. Bleibt in der Code-Werkstatt öffenbar/editierbar.
ANALYSIS_PROJECT_NAME = "PaperKG-Analysen"


def _slug(text: str, limit: int = 40) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9]+", "-", (text or "").strip().lower()).strip("-")
    return cleaned[:limit] or "analyse"


def ensure_analysis_project(db: MetadataDB, config_path: str = "config.yaml") -> dict[str, Any]:
    """Registriertes, verwaltetes Werkstatt-Projekt für Analyse-Läufe (anlegen falls nötig)."""
    base = workspace_manager.base_dir(config_path)
    folder = workspace_manager.safe_folder_name(ANALYSIS_PROJECT_NAME)
    root = (base / folder).resolve()

    existing = db.get_code_project_by_path(str(root))
    if existing is not None:
        return existing
    if root.is_dir():
        # Ordner existiert schon auf der Platte (z.B. aus früherer Session) — nur registrieren.
        return db.add_code_project(name=ANALYSIS_PROJECT_NAME, path=str(root), kind="managed")
    root = workspace_manager.init_managed_project(base, ANALYSIS_PROJECT_NAME)
    return db.add_code_project(name=ANALYSIS_PROJECT_NAME, path=str(root), kind="managed")


def _package_versions() -> dict[str, str]:
    """Best-effort Versionsstände der Kern-Analyse-Libs für run.json."""
    versions: dict[str, str] = {"python": platform.python_version()}
    for mod in ("pandas", "numpy", "matplotlib"):
        try:
            versions[mod] = __import__(mod).__version__
        except Exception:
            versions[mod] = "not installed"
    return versions


def _write_readme(run_dir: Path, *, title: str, description: str, request: str,
                  provider: str | None, model: str | None, seed: int) -> None:
    lines = [
        f"# {title}",
        "",
        "> Automatisch erzeugt von der PaperKG Analyse-Werkstatt — vollständig",
        "> nachvollziehbar und anpassbar. Bearbeite `script.py` und starte den Lauf neu.",
        "",
        "## Aufgabe",
        request.strip() or "(keine)",
        "",
        "## Was dieses Skript tut",
        description.strip() or "(keine Beschreibung)",
        "",
        "## Reproduzieren",
        "```bash",
        "python script.py",
        "```",
        "Ausgaben landen in `outputs/`. Die vollständige Provenance (Environment, Seed,",
        "Modell, Verlauf, Datei-Hashes) steht in `run.json`.",
        "",
        "## Herkunft",
        f"- Provider/Modell: `{provider or 'default'}` / `{model or 'default'}`",
        f"- Seed: `{seed}`",
        f"- Erzeugt: {datetime.now().isoformat(timespec='seconds')}",
    ]
    (run_dir / "README.md").write_text("\n".join(lines) + "\n", encoding="utf-8", newline="\n")


def _write_run_json(run_dir: Path, payload: dict[str, Any]) -> None:
    (run_dir / "run.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8", newline="\n"
    )


def _git_commit(project_root: Path, message: str) -> None:
    if not workspace_manager.git_available():
        return
    workspace_manager._run_git(project_root, ["add", "-A"])
    workspace_manager._run_git(
        project_root,
        ["-c", "user.email=werkstatt@paperkg.local", "-c", "user.name=PaperKG",
         "commit", "-m", message],
    )


def _stage_inputs(run_dir: Path, input_specs: list[dict[str, Any]] | None) -> list[str]:
    """Kopiere Eingabedateien nach inputs/. ``input_specs`` = [{path, name}]."""
    staged: list[str] = []
    for spec in input_specs or []:
        src = spec.get("path")
        name = spec.get("name") or (Path(str(src)).name if src else None)
        if not src or not name or not Path(str(src)).is_file():
            continue
        try:
            rel = runner.stage_input(run_dir, str(src), str(name))
            staged.append(Path(rel).name)
        except Exception:
            continue
    return staged


def _status_of(result: runner.RunResult) -> str:
    if result.timed_out:
        return "timeout"
    return "ok" if result.ok else "error"


def create_run(
    db: MetadataDB,
    router: Any,
    *,
    request: str,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    seed: int = runner.DEFAULT_SEED,
    timeout: float = runner.DEFAULT_TIMEOUT_SECONDS,
    input_specs: list[dict[str, Any]] | None = None,
    context: str | None = None,
    config_path: str = "config.yaml",
) -> dict[str, Any]:
    """Plane, führe aus und persistiere einen neuen Analyse-Lauf. Gibt den DB-Datensatz zurück."""
    project = ensure_analysis_project(db, config_path)
    project_root = workspace_manager.ensure_exists(project)

    rel_dir = f"{datetime.now():%Y%m%d-%H%M%S}-{_slug(request)}"
    run_dir = (project_root / rel_dir).resolve()
    run_dir.mkdir(parents=True, exist_ok=True)

    staged = _stage_inputs(run_dir, input_specs)

    plan = planner.plan_script(
        router, request, context=context, input_files=staged,
        provider=provider, model=model,
    )
    result = runner.run_script(
        run_dir, plan.code, python_executable=sys.executable, seed=seed, timeout=timeout,
    )

    _write_readme(
        run_dir, title=plan.title, description=plan.description, request=request,
        provider=provider, model=model, seed=seed,
    )
    _write_run_json(run_dir, {
        "title": plan.title,
        "description": plan.description,
        "request": request,
        "provider": provider,
        "model": model,
        "seed": seed,
        "environment": _package_versions(),
        "planning": {
            "system": planner._SYSTEM,
            "response": plan.raw,
        },
        "result": result.as_dict(),
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    _git_commit(project_root, f"Analyse: {plan.title[:60]}")

    run = db.add_analysis_run({
        "project_id": project_id,
        "code_project_id": project.get("id"),
        "run_dir": str(run_dir),
        "rel_dir": rel_dir,
        "title": plan.title,
        "description": plan.description,
        "request": request,
        "script_rel": f"{rel_dir}/{runner.SCRIPT_FILENAME}",
        "status": _status_of(result),
        "provider": provider,
        "model": model,
        "seed": seed,
        "output_hash": result.combined_hash,
        "stdout": result.stdout[-8000:],
        "stderr": result.stderr[-8000:],
        "duration_s": result.duration_s,
    })
    db.replace_analysis_artifacts(run["id"], [a.as_dict() for a in result.artifacts])
    return db.get_analysis_run(run["id"])  # type: ignore[return-value]


def revise_run(
    db: MetadataDB,
    router: Any,
    run_id: str,
    *,
    request: str | None = None,
    annotation: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    seed: int | None = None,
    timeout: float = runner.DEFAULT_TIMEOUT_SECONDS,
    context: str | None = None,
) -> dict[str, Any] | None:
    """Überarbeite einen bestehenden Lauf im selben Ordner (neue git-Version).

    Nutzt das bisherige Skript als Basis; ``request`` (neue Anweisung) und/oder
    ``annotation`` (markierter Figur-Bereich + Kommentar, WP3) steuern die Änderung.
    Der letzte stderr wird als Fehlerkontext mitgegeben, wenn der Lauf fehlschlug.
    """
    run = db.get_analysis_run(run_id)
    if run is None:
        return None
    run_dir = Path(str(run["run_dir"])).resolve()
    if not run_dir.is_dir():
        return None

    script_path = run_dir / runner.SCRIPT_FILENAME
    previous_code = script_path.read_text(encoding="utf-8", errors="replace") if script_path.is_file() else None
    prior_error = str(run.get("stderr") or "").strip() if run.get("status") != "ok" else None
    seed_val = int(seed if seed is not None else (run.get("seed") or runner.DEFAULT_SEED))
    prov = provider if provider is not None else run.get("provider")
    mdl = model if model is not None else run.get("model")
    staged = [Path(p).name for p in sorted((run_dir / runner.INPUTS_DIRNAME).glob("*"))] \
        if (run_dir / runner.INPUTS_DIRNAME).is_dir() else []

    plan = planner.plan_script(
        router,
        request or str(run.get("request") or ""),
        context=context,
        input_files=staged,
        previous_code=previous_code,
        error=prior_error,
        annotation=annotation,
        provider=prov,
        model=mdl,
    )
    result = runner.run_script(
        run_dir, plan.code, python_executable=sys.executable, seed=seed_val, timeout=timeout,
    )

    _write_readme(
        run_dir, title=plan.title, description=plan.description,
        request=request or str(run.get("request") or ""), provider=prov, model=mdl, seed=seed_val,
    )
    _write_run_json(run_dir, {
        "title": plan.title,
        "description": plan.description,
        "request": request or run.get("request"),
        "revision_of": run_id,
        "annotation": annotation,
        "provider": prov,
        "model": mdl,
        "seed": seed_val,
        "environment": _package_versions(),
        "planning": {"system": planner._SYSTEM, "response": plan.raw},
        "result": result.as_dict(),
        "created": datetime.now().isoformat(timespec="seconds"),
    })
    project = db.get_code_project(str(run.get("code_project_id"))) if run.get("code_project_id") else None
    if project is not None:
        _git_commit(workspace_manager.project_root(project), f"Revision: {plan.title[:56]}")

    db.update_analysis_run(
        run_id,
        title=plan.title,
        description=plan.description,
        status=_status_of(result),
        output_hash=result.combined_hash,
        stdout=result.stdout[-8000:],
        stderr=result.stderr[-8000:],
        duration_s=result.duration_s,
        provider=prov,
        model=mdl,
        seed=seed_val,
        request=request or run.get("request"),
    )
    db.replace_analysis_artifacts(run_id, [a.as_dict() for a in result.artifacts])
    return db.get_analysis_run(run_id)
