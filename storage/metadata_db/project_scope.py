"""Projekt-weite Operationen ueber alle project_id-gebundenen Tabellen.

Die Projekt-ID ist zugleich der Projektname (siehe api/routers/projects.py), also
verschiebt eine Umbenennung die Zugehoerigkeit *aller* projektgebundenen Daten.
Ohne diese Migration wuerden Notizen, Web-Quellen, Sessions und Analysen nach
einer Umbenennung verwaisen.
"""
from __future__ import annotations

from typing import Any

#: Alle Tabellen mit einer ``project_id``-Spalte (siehe schema.py).
PROJECT_SCOPED_TABLES: tuple[str, ...] = (
    "notes",
    "grey_sources",
    "workspace_sessions",
    "workspace_session_backups",
    "research_sessions",
    "parallel_sessions",
    "analysis_runs",
    "datasets",
)


class ProjectScopeMixin:
    """Umbenennen/Aufraeumen ueber alle projektgebundenen Tabellen hinweg."""

    _execute: Any

    def rename_project(self, old_project_id: str, new_project_id: str) -> dict[str, int]:
        """Schreibe ``project_id`` in allen betroffenen Tabellen um.

        ``workspace_sessions.project_id`` ist PRIMARY KEY: existiert bereits eine
        Zeile fuer das Zielprojekt (kann bei einem Umbenennen auf einen freien,
        aber schon einmal benutzten Namen passieren), wird die alte Zeile
        verworfen statt den Schluessel zu verletzen.
        """
        if not old_project_id or not new_project_id or old_project_id == new_project_id:
            return {}

        moved: dict[str, int] = {}
        self._execute("BEGIN TRANSACTION")
        try:
            for table in PROJECT_SCOPED_TABLES:
                count = int(
                    self._execute(
                        f"SELECT COUNT(*) FROM {table} WHERE project_id = ?",  # noqa: S608 - fixed table list
                        [old_project_id],
                    ).fetchone()[0]
                )
                if not count:
                    continue
                if table == "workspace_sessions":
                    self._execute("DELETE FROM workspace_sessions WHERE project_id = ?", [new_project_id])
                self._execute(
                    f"UPDATE {table} SET project_id = ? WHERE project_id = ?",  # noqa: S608 - fixed table list
                    [new_project_id, old_project_id],
                )
                moved[table] = count
            self._execute("COMMIT")
        except Exception:
            self._execute("ROLLBACK")
            raise
        return moved
