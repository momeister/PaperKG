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


class GreySourcesMixin(_Base):
    """MetadataDB grey_sources operations (mixin)."""

    def add_grey_source(self, project_id: str, source: dict[str, Any]) -> dict[str, Any]:
        """Persist a grey (web) source for a project. Never added to the KG."""
        grey_id = str(source.get("id") or f"grey_{uuid.uuid4().hex}")
        now = datetime.now()
        flags = source.get("injection_flags") or []
        evidence = source.get("evidence") or []
        self._execute("""
            INSERT INTO grey_sources
            (id, project_id, query, url, title, summary, raw_excerpt, full_text, evidence,
             injection_flags, status, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                query = EXCLUDED.query,
                url = EXCLUDED.url,
                title = EXCLUDED.title,
                summary = EXCLUDED.summary,
                raw_excerpt = EXCLUDED.raw_excerpt,
                full_text = EXCLUDED.full_text,
                evidence = EXCLUDED.evidence,
                injection_flags = EXCLUDED.injection_flags,
                status = EXCLUDED.status
        """, [
            grey_id,
            str(project_id),
            source.get("query"),
            str(source.get("url") or ""),
            source.get("title"),
            source.get("summary"),
            source.get("raw_excerpt"),
            source.get("full_text"),
            json.dumps(evidence),
            json.dumps(flags),
            str(source.get("status") or "saved"),
            now,
        ])
        return self.get_grey_source(grey_id) or {"id": grey_id}

    def get_grey_source(self, grey_id: str) -> dict[str, Any] | None:
        rows = self._execute("SELECT * FROM grey_sources WHERE id = ?", [grey_id]).fetchall()
        if not rows:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return self._decode_grey_source(dict(zip(cols, rows[0])))

    def list_grey_sources(self, project_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if project_id is None:
            rows = self._execute("""
                SELECT * FROM grey_sources
                ORDER BY created_timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        else:
            rows = self._execute("""
                SELECT * FROM grey_sources
                WHERE project_id = ?
                ORDER BY created_timestamp DESC
                LIMIT ?
            """, [str(project_id), limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [self._decode_grey_source(dict(zip(cols, row))) for row in rows]

    def delete_grey_source(self, grey_id: str) -> bool:
        if self.get_grey_source(grey_id) is None:
            return False
        self._execute("DELETE FROM grey_sources WHERE id = ?", [grey_id])
        return True

    @staticmethod
    def _decode_grey_source(row: dict[str, Any]) -> dict[str, Any]:
        for field in ("injection_flags", "evidence"):
            value = row.get(field)
            if isinstance(value, str):
                try:
                    row[field] = json.loads(value)
                except (ValueError, TypeError):
                    row[field] = []
            elif value is None:
                row[field] = []
        return row
