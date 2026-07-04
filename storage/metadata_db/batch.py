from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class BatchMixin(_Base):
    """MetadataDB batch operations (mixin)."""

    def upsert_batch_job(
        self,
        job_id: str,
        status: str,
        papers_total: int,
        papers_processed: int = 0,
        papers_failed: int = 0,
        error_message: str | None = None,
        request_payload: dict[str, Any] | None = None,
        llm_provider: str | None = None,
        superseded_by: str | None = None,
    ) -> None:
        """
        Persist the current aggregate state for a batch job.
        """
        now = datetime.now()
        self._execute("""
            INSERT INTO batch_jobs
            (job_id, status, papers_total, papers_processed, papers_failed, error_message,
             request_payload, llm_provider, superseded_by, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (job_id) DO UPDATE SET
                status = EXCLUDED.status,
                papers_total = EXCLUDED.papers_total,
                papers_processed = EXCLUDED.papers_processed,
                papers_failed = EXCLUDED.papers_failed,
                error_message = EXCLUDED.error_message,
                request_payload = EXCLUDED.request_payload,
                llm_provider = EXCLUDED.llm_provider,
                superseded_by = EXCLUDED.superseded_by,
                updated_timestamp = EXCLUDED.updated_timestamp
        """, [
            job_id,
            status,
            int(papers_total),
            int(papers_processed),
            int(papers_failed),
            error_message,
            json.dumps(request_payload or {}),
            llm_provider,
            superseded_by,
            now,
        ])

    def get_batch_job(self, job_id: str) -> dict[str, Any] | None:
        result = self._execute(
            "SELECT * FROM batch_jobs WHERE job_id = ?",
            [job_id],
        ).fetchone()
        if result is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        data = dict(zip(cols, result))
        if data.get("request_payload"):
            try:
                data["request_payload"] = json.loads(data["request_payload"])
            except (json.JSONDecodeError, TypeError):
                pass
        return data

    def list_batch_jobs(self, limit: int = 100) -> list[dict[str, Any]]:
        results = self._execute("""
            SELECT * FROM batch_jobs
            ORDER BY updated_timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        for row in results:
            data = dict(zip(cols, row))
            if data.get("request_payload"):
                try:
                    data["request_payload"] = json.loads(data["request_payload"])
                except (json.JSONDecodeError, TypeError):
                    pass
            data_list.append(data)
        return data_list

    def upsert_batch_job_item(
        self,
        job_id: str,
        paper_id: str,
        pdf_path: str | None,
        status: str,
        attempts: int = 0,
        error_message: str | None = None,
    ) -> None:
        now = datetime.now()
        started_timestamp = now if status == "processing" else None
        completed_timestamp = now if status in {"completed", "failed", "skipped"} else None
        self._execute("""
            INSERT INTO batch_job_items
            (job_id, paper_id, pdf_path, status, attempts, error_message, started_timestamp,
             completed_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (job_id, paper_id) DO UPDATE SET
                pdf_path = EXCLUDED.pdf_path,
                status = EXCLUDED.status,
                attempts = EXCLUDED.attempts,
                error_message = EXCLUDED.error_message,
                started_timestamp = CASE
                    WHEN EXCLUDED.status = 'processing' AND batch_job_items.started_timestamp IS NULL
                    THEN EXCLUDED.started_timestamp
                    ELSE batch_job_items.started_timestamp
                END,
                completed_timestamp = CASE
                    WHEN EXCLUDED.status IN ('completed', 'failed', 'skipped') THEN EXCLUDED.completed_timestamp
                    ELSE batch_job_items.completed_timestamp
                END,
                updated_timestamp = EXCLUDED.updated_timestamp
        """, [
            job_id,
            paper_id,
            pdf_path,
            status,
            int(attempts),
            error_message,
            started_timestamp,
            completed_timestamp,
            now,
        ])

    def get_batch_job_items(self, job_id: str) -> list[dict[str, Any]]:
        results = self._execute("""
            SELECT * FROM batch_job_items
            WHERE job_id = ?
            ORDER BY paper_id
        """, [job_id]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in results]

    def mark_batch_job_superseded(self, job_id: str, superseded_by: str) -> None:
        now = datetime.now()
        self._execute("""
            UPDATE batch_jobs
            SET status = 'superseded',
                superseded_by = ?,
                updated_timestamp = ?
            WHERE job_id = ?
        """, [superseded_by, now, job_id])

    def cancel_batch_job(self, job_id: str) -> None:
        now = datetime.now()
        self._execute("""
            UPDATE batch_jobs
            SET status = 'cancelled', updated_timestamp = ?
            WHERE job_id = ? AND status NOT IN ('completed', 'failed', 'superseded', 'cancelled')
        """, [now, job_id])
