from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class AnalysisMixin(_Base):
    """MetadataDB analysis operations (mixin)."""

    def _row_to_dict(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return dict(zip(cols, row))

    def add_analysis_run(self, run: dict[str, Any]) -> dict[str, Any]:
        """Persist a run's metadata. Files live on disk in the managed run folder."""
        run_id = str(run.get("id") or f"an_{uuid.uuid4().hex}")
        now = datetime.now()
        self._execute("""
            INSERT INTO analysis_runs
            (id, project_id, code_project_id, run_dir, rel_dir, title, description,
             request, script_rel, status, provider, model, seed, output_hash,
             verified_hash, stdout, stderr, duration_s, created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            run_id,
            run.get("project_id"),
            run.get("code_project_id"),
            str(run.get("run_dir") or ""),
            run.get("rel_dir"),
            run.get("title"),
            run.get("description"),
            run.get("request"),
            run.get("script_rel"),
            str(run.get("status") or "ok"),
            run.get("provider"),
            run.get("model"),
            int(run["seed"]) if run.get("seed") is not None else None,
            run.get("output_hash"),
            run.get("verified_hash"),
            run.get("stdout"),
            run.get("stderr"),
            float(run["duration_s"]) if run.get("duration_s") is not None else None,
            now,
            now,
        ])
        return self.get_analysis_run(run_id)  # type: ignore[return-value]

    def update_analysis_run(self, run_id: str, **fields: Any) -> dict[str, Any] | None:
        """Update selected columns of a run (e.g. after a revise re-run or verify)."""
        allowed = {
            "title", "description", "status", "output_hash", "verified_hash",
            "stdout", "stderr", "duration_s", "provider", "model", "seed", "request",
        }
        sets = {k: v for k, v in fields.items() if k in allowed}
        if not sets:
            return self.get_analysis_run(run_id)
        assignments = ", ".join(f"{k} = ?" for k in sets)
        params = list(sets.values()) + [datetime.now(), str(run_id)]
        self._execute(
            f"UPDATE analysis_runs SET {assignments}, updated_timestamp = ? WHERE id = ?",
            params,
        )
        return self.get_analysis_run(run_id)

    def replace_analysis_artifacts(
        self, run_id: str, artifacts: list[dict[str, Any]]
    ) -> list[dict[str, Any]]:
        """Replace a run's artifact rows (revise re-runs overwrite the outputs)."""
        self._execute("DELETE FROM analysis_artifacts WHERE run_id = ?", [str(run_id)])
        for art in artifacts:
            self._execute("""
                INSERT INTO analysis_artifacts
                (id, run_id, kind, filename, rel_path, caption, size, sha256, created_timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                str(art.get("id") or f"art_{uuid.uuid4().hex}"),
                str(run_id),
                art.get("kind"),
                art.get("filename"),
                art.get("rel_path"),
                art.get("caption"),
                int(art["size"]) if art.get("size") is not None else None,
                art.get("sha256"),
                datetime.now(),
            ])
        return self.list_analysis_artifacts(run_id)

    def list_analysis_artifacts(self, run_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM analysis_artifacts WHERE run_id = ? ORDER BY rel_path",
            [str(run_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_analysis_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM analysis_artifacts WHERE id = ?", [str(artifact_id)]
        ).fetchone()
        return self._row_to_dict(row)

    def get_analysis_run(self, run_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM analysis_runs WHERE id = ?", [str(run_id)]
        ).fetchone()
        run = self._row_to_dict(row)
        if run is None:
            return None
        run["artifacts"] = self.list_analysis_artifacts(run_id)
        return run

    def list_analysis_runs(
        self, project_id: str | None = None, limit: int = 100
    ) -> list[dict[str, Any]]:
        """List runs (newest first), optionally scoped to a project. No artifacts."""
        if project_id:
            rows = self._execute(
                "SELECT * FROM analysis_runs WHERE project_id = ? "
                "ORDER BY created_timestamp DESC LIMIT ?",
                [str(project_id), int(limit)],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM analysis_runs ORDER BY created_timestamp DESC LIMIT ?",
                [int(limit)],
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def delete_analysis_run(self, run_id: str) -> bool:
        if self.get_analysis_run(run_id) is None:
            return False
        self._execute("DELETE FROM analysis_artifacts WHERE run_id = ?", [str(run_id)])
        self._execute("DELETE FROM analysis_runs WHERE id = ?", [str(run_id)])
        return True

    # ------------------------------------------------------------------ #
    # Datensätze (WP2): gesammelte Forschungs-Datensätze (Referenzen)     #
    # ------------------------------------------------------------------ #
