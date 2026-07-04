from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class CodeProjectsMixin(_Base):
    """MetadataDB code_projects operations (mixin)."""

    def _code_project_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return dict(zip(cols, row))

    def add_code_project(
        self,
        name: str,
        path: str,
        kind: str = "managed",
        project_id: str | None = None,
    ) -> dict[str, Any]:
        pid = project_id or f"cp_{uuid.uuid4().hex}"
        now = datetime.now()
        self._execute("""
            INSERT INTO code_projects (id, name, path, kind, created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [pid, str(name), str(path), str(kind or "managed"), now, now])
        return self.get_code_project(pid)  # type: ignore[return-value]

    def get_code_project(self, project_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM code_projects WHERE id = ?", [str(project_id)]
        ).fetchone()
        return self._code_project_row(row)

    def get_code_project_by_path(self, path: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM code_projects WHERE path = ?", [str(path)]
        ).fetchone()
        return self._code_project_row(row)

    def list_code_projects(self) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM code_projects ORDER BY created_timestamp DESC"
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def delete_code_project(self, project_id: str) -> bool:
        if self.get_code_project(project_id) is None:
            return False
        self._execute("DELETE FROM code_projects WHERE id = ?", [str(project_id)])
        return True

    # ------------------------------------------------------------------ #
    # Analyse-Werkstatt (WP1): reproduzierbare Skript-Läufe + Artefakte   #
    # ------------------------------------------------------------------ #
