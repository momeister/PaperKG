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


class DatasetsMixin(_Base):
    """MetadataDB datasets operations (mixin)."""

    def _dataset_row(self, row: Any) -> dict[str, Any] | None:
        rec = self._row_to_dict(row)
        if rec is None:
            return None
        meta = rec.get("metadata")
        if isinstance(meta, str):
            try:
                rec["metadata"] = json.loads(meta)
            except (json.JSONDecodeError, ValueError):
                rec["metadata"] = {}
        return rec

    def get_dataset_by_source(
        self, project_id: str | None, source: str, external_id: str
    ) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM datasets WHERE source = ? AND external_id = ? "
            "AND ((project_id IS NULL AND ? IS NULL) OR project_id = ?) LIMIT 1",
            [str(source), str(external_id), project_id, project_id],
        ).fetchone()
        return self._dataset_row(row)

    def add_dataset(self, ds: dict[str, Any]) -> dict[str, Any]:
        """Persist a dataset reference. De-dups on (project_id, source, external_id)."""
        source = str(ds.get("source") or "")
        external_id = str(ds.get("external_id") or "")
        project_id = ds.get("project_id")
        existing = self.get_dataset_by_source(project_id, source, external_id) if external_id else None
        if existing is not None:
            return existing
        dataset_id = str(ds.get("id") or f"ds_{uuid.uuid4().hex}")
        self._execute("""
            INSERT INTO datasets
            (id, project_id, source, external_id, title, description, url, doi, license,
             size, year, linked_paper_id, metadata, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            dataset_id,
            project_id,
            source,
            external_id,
            ds.get("title"),
            ds.get("description"),
            ds.get("url"),
            ds.get("doi"),
            ds.get("license"),
            ds.get("size"),
            int(ds["year"]) if ds.get("year") is not None else None,
            ds.get("linked_paper_id"),
            json.dumps(ds.get("metadata") or {}, ensure_ascii=False),
            datetime.now(),
        ])
        return self.get_dataset(dataset_id)  # type: ignore[return-value]

    def get_dataset(self, dataset_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM datasets WHERE id = ?", [str(dataset_id)]
        ).fetchone()
        return self._dataset_row(row)

    def list_datasets(self, project_id: str | None = None) -> list[dict[str, Any]]:
        if project_id:
            rows = self._execute(
                "SELECT * FROM datasets WHERE project_id = ? ORDER BY created_timestamp DESC",
                [str(project_id)],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM datasets ORDER BY created_timestamp DESC"
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            meta = rec.get("metadata")
            if isinstance(meta, str):
                try:
                    rec["metadata"] = json.loads(meta)
                except (json.JSONDecodeError, ValueError):
                    rec["metadata"] = {}
            out.append(rec)
        return out

    def delete_dataset(self, dataset_id: str) -> bool:
        if self.get_dataset(dataset_id) is None:
            return False
        self._execute("DELETE FROM datasets WHERE id = ?", [str(dataset_id)])
        return True
