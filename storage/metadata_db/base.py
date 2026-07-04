from __future__ import annotations

import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import duckdb

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB



class MetadataDBBase:
    """
    DuckDB-backed metadata storage for papers.
    """

    EXTRACTION_JSON_FIELDS = [
        "concepts",
        "methods",
        "concept_candidates",
        "method_candidates",
        "relations",
        "claims",
        "cross_domain_hints",
        "terminology_conflicts",
        "temporal_coverage",
        "mathematical_content",
    ]

    if TYPE_CHECKING:  # provided by SchemaMixin at composition time
        def _init_schema(self) -> None: ...

    def __init__(self, db_path: str = "data/metadata.duckdb") -> None:
        from storage.path_safety import ensure_safe_path

        db_file = ensure_safe_path(db_path, what="metadata database path")
        self.db_path = str(db_file)
        self._lock = threading.RLock()
        self._closed = False
        db_file.parent.mkdir(parents=True, exist_ok=True)
        if db_file.exists() and db_file.stat().st_size == 0:
            db_file.unlink()
        self.conn = self._connect_with_retry(db_file)
        self._init_schema()

    @staticmethod
    def _connect_with_retry(db_file: Path, attempts: int = 12, delay: float = 0.25):
        # Windows file locks are transient when several processes (API worker,
        # reload child, batch scripts) touch the DB at the same moment; a short
        # retry turns those races into a brief wait instead of a 500.
        last_error: Exception | None = None
        for attempt in range(attempts):
            try:
                return duckdb.connect(str(db_file))
            except duckdb.IOException as error:
                last_error = error
                if attempt == attempts - 1:
                    break
                time.sleep(delay)
        raise last_error  # type: ignore[misc]

    def __enter__(self) -> "MetadataDB":
        return self

    def __exit__(self, exc_type: object, exc: object, traceback: object) -> None:
        self.close()

    @property
    def is_closed(self) -> bool:
        return self._closed

    def _execute(self, query: str, parameters: list[Any] | tuple[Any, ...] | None = None):
        if self._closed:
            raise RuntimeError(f"MetadataDB connection is closed: {self.db_path}")
        with self._lock:
            if parameters is None:
                return self.conn.execute(query)
            return self.conn.execute(query, parameters)

    def clear_all(self) -> None:
        """
        Delete all mutable metadata tables and keep the schema intact.
        """
        with self._lock:
            self._execute("BEGIN TRANSACTION")
            try:
                self._execute("DELETE FROM extraction_results")
                self._execute("DELETE FROM extraction_quality")
                self._execute("DELETE FROM entity_review_queue")
                self._execute("DELETE FROM batch_job_items")
                self._execute("DELETE FROM batch_jobs")
                self._execute("DELETE FROM entity_embeddings")
                self._execute("DELETE FROM note_ai_messages")
                self._execute("DELETE FROM note_ai_threads")
                self._execute("DELETE FROM note_versions")
                self._execute("DELETE FROM note_assets")
                self._execute("DELETE FROM note_citations")
                self._execute("DELETE FROM notes")
                self._execute("DELETE FROM dedup_log")
                self._execute("DELETE FROM paper_sources")
                self._execute("DELETE FROM papers")
                self._execute("COMMIT")
            except Exception:
                self._execute("ROLLBACK")
                raise

    def close(self) -> None:
        """
        Close database connection.
        """
        if self._closed:
            return
        with self._lock:
            if not self._closed:
                self.conn.close()
                self._closed = True
