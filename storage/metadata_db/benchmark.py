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


class BenchmarkMixin(_Base):
    """MetadataDB benchmark operations (mixin)."""

    def add_benchmark_run(self, run: dict[str, Any]) -> dict[str, Any]:
        """Persist a benchmark/eval run so past runs and their metadata stay visible."""
        run_id = str(run.get("id") or f"bench_{uuid.uuid4().hex}")
        self._execute("""
            INSERT INTO benchmark_runs
            (id, kind, provider, model, summary, report, duration_ms, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            run_id,
            str(run.get("kind") or "extraction"),
            run.get("provider"),
            run.get("model"),
            json.dumps(run.get("summary") or {}),
            json.dumps(run.get("report") or {}),
            int(run.get("duration_ms") or 0),
            datetime.now(),
        ])
        return self.get_benchmark_run(run_id) or {"id": run_id}

    def get_benchmark_run(self, run_id: str) -> dict[str, Any] | None:
        rows = self._execute("SELECT * FROM benchmark_runs WHERE id = ?", [run_id]).fetchall()
        if not rows:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return self._decode_benchmark_run(dict(zip(cols, rows[0])))

    def list_benchmark_runs(self, kind: str | None = None, limit: int = 100) -> list[dict[str, Any]]:
        if kind:
            rows = self._execute(
                "SELECT * FROM benchmark_runs WHERE kind = ? ORDER BY created_timestamp DESC LIMIT ?",
                [kind, limit],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM benchmark_runs ORDER BY created_timestamp DESC LIMIT ?",
                [limit],
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [self._decode_benchmark_run(dict(zip(cols, row))) for row in rows]

    def delete_benchmark_run(self, run_id: str) -> bool:
        if self.get_benchmark_run(run_id) is None:
            return False
        self._execute("DELETE FROM benchmark_runs WHERE id = ?", [run_id])
        return True

    @staticmethod
    def _decode_benchmark_run(row: dict[str, Any]) -> dict[str, Any]:
        for field in ("summary", "report"):
            value = row.get(field)
            if isinstance(value, str):
                try:
                    row[field] = json.loads(value)
                except (ValueError, TypeError):
                    row[field] = {}
            elif value is None:
                row[field] = {}
        return row
