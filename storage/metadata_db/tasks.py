"""Task-Focused Mode: projektgebundene Task-Specs (Kaggle / Hackathon / eigene Anweisung).

Eine ``tasks``-Zeile haelt den strukturierten Task-Spec (Ziel, Bewertung, Datasets,
Regeln, vorgeschlagene Forschungsrichtungen), der aus URL/PDF/Freitext extrahiert
wurde. Projekt-scoped (siehe ``PROJECT_SCOPED_TABLES``), damit Umbenennung des
Projekts den Task mitnimmt. Der Spec ist als Grey-Source zitierbar
(``grey::task_{id}``), siehe ``api/routers/grey_sources.py``.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


def _decode_task(row: dict[str, Any]) -> dict[str, Any]:
    """Wandle ``task_json`` JSON-Spalte in ein Python-dict um."""
    raw = row.get("task_json")
    if isinstance(raw, str):
        try:
            row["task_json"] = json.loads(raw)
        except (ValueError, TypeError):
            row["task_json"] = {}
    elif raw is None:
        row["task_json"] = {}
    return row


class TasksMixin(_Base):
    """MetadataDB tasks operations (mixin)."""

    def create_task(
        self,
        project_id: str,
        title: str,
        task_json: dict[str, Any],
        source_kind: str = "text",
        source_url: str | None = None,
        source_pdf_path: str | None = None,
        task_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now()
        task_id = task_id or f"task_{uuid.uuid4().hex}"
        self._execute(
            """
            INSERT INTO tasks
            (id, project_id, title, source_kind, source_url, source_pdf_path,
             task_json, created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            [
                task_id,
                str(project_id),
                title.strip() or "Aufgabe",
                str(source_kind or "text"),
                source_url,
                source_pdf_path,
                json.dumps(task_json, ensure_ascii=False),
                now,
                now,
            ],
        )
        task = self.get_task(task_id)
        if task is None:
            raise RuntimeError(f"Failed to create task: {task_id}")
        return task

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM tasks WHERE id = ?", [task_id]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return _decode_task(dict(zip(cols, row)))

    def list_tasks(
        self, project_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        if project_id:
            rows = self._execute(
                """
                SELECT * FROM tasks
                WHERE project_id = ?
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """,
                [str(project_id), limit],
            ).fetchall()
        else:
            rows = self._execute(
                """
                SELECT * FROM tasks
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """,
                [limit],
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [_decode_task(dict(zip(cols, row))) for row in rows]

    def update_task(
        self,
        task_id: str,
        title: str | None = None,
        task_json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_task(task_id)
        if current is None:
            return None
        next_title = (
            title.strip()
            if title is not None and title.strip()
            else str(current.get("title") or "Aufgabe")
        )
        next_json = (
            task_json if task_json is not None else (current.get("task_json") or {})
        )
        self._execute(
            """
            UPDATE tasks
            SET title = ?, task_json = ?, updated_timestamp = ?
            WHERE id = ?
        """,
            [
                next_title,
                json.dumps(next_json, ensure_ascii=False),
                datetime.now(),
                task_id,
            ],
        )
        return self.get_task(task_id)

    def delete_task(self, task_id: str) -> bool:
        if self.get_task(task_id) is None:
            return False
        self._execute("DELETE FROM tasks WHERE id = ?", [task_id])
        return True
