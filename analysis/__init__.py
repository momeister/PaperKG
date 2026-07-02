"""Reproduzierbare Analyse-Werkstatt.

Die KI schreibt ein Python-Skript, PaperKG führt es lokal in einem verwalteten
Code-Werkstatt-Projektordner aus und erzeugt Figuren/Tabellen. Jeder Lauf ist ein
echter, git-versionierter Ordner mit ``script.py``, ``inputs/``, ``outputs/``,
``run.json`` (Environment/Seed/Provider/Verlauf/Hashes) und ``README.md`` — nichts
entsteht ohne diese nachvollziehbare Historie.

Öffentliche Bausteine:
  * :mod:`analysis.runner`  — Skript-Ausführung (subprocess, Seed, Timeout, Containment)
  * :mod:`analysis.planner` — NL-Anfrage → ``script.py`` + Klartext-Beschreibung (LLMRouter)
  * :mod:`analysis.store`   — DuckDB-Persistenz der Läufe + Artefakte
  * :mod:`analysis.verify`  — deterministischer Reproduzierbarkeits-Check (WP4)
"""
from __future__ import annotations

from analysis.runner import (
    ArtifactInfo,
    RunResult,
    classify_artifact,
    collect_artifacts,
    run_script,
)

__all__ = [
    "ArtifactInfo",
    "RunResult",
    "classify_artifact",
    "collect_artifacts",
    "run_script",
]
