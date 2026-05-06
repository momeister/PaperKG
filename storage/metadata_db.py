from __future__ import annotations

import json
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb


class MetadataDB:
    """
    DuckDB-backed metadata storage for papers.
    """

    def __init__(self, db_path: str = "data/metadata.duckdb") -> None:
        self.db_path = str(Path(db_path).resolve())
        self._lock = threading.RLock()
        self._closed = False
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        if db_file.exists() and db_file.stat().st_size == 0:
            db_file.unlink()
        self.conn = duckdb.connect(str(db_file))
        self._init_schema()

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

    def _init_schema(self) -> None:
        """
        Initialize all required tables if they don't exist.
        """
        self._execute("CREATE SEQUENCE IF NOT EXISTS seq_dedup_id")

        self._execute("""
            CREATE TABLE IF NOT EXISTS papers (
                id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                title VARCHAR,
                abstract VARCHAR,
                authors JSON,
                year INTEGER,
                doi VARCHAR,
                pdf_url VARCHAR,
                landing_page_url VARCHAR,
                "references" JSON,
                citations JSON,
                citation_count INTEGER DEFAULT 0,
                superseded_by VARCHAR,
                peer_reviewed BOOLEAN DEFAULT false,
                retracted BOOLEAN DEFAULT false,
                language_original VARCHAR DEFAULT 'unknown',
                confidence_score FLOAT DEFAULT 0.5,
                obsolescence_score FLOAT DEFAULT 0.0,
                conflict_flag BOOLEAN DEFAULT false,
                embedding_model VARCHAR,
                embedding_version INTEGER DEFAULT 0,
                has_full_text BOOLEAN DEFAULT false,
                version INTEGER DEFAULT 1,
                added_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._migrate_schema()

        self._execute("""
            CREATE TABLE IF NOT EXISTS paper_sources (
                paper_id VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                source_id VARCHAR,
                source_url VARCHAR,
                added_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (paper_id, source)
            )
        """)

    def _migrate_schema(self) -> None:
        """
        Add Phase 2/3 columns when opening older DuckDB files.
        """
        columns = {
            "references": "JSON",
            "citations": "JSON",
            "citation_count": "INTEGER DEFAULT 0",
            "superseded_by": "VARCHAR",
            "peer_reviewed": "BOOLEAN DEFAULT false",
            "retracted": "BOOLEAN DEFAULT false",
            "language_original": "VARCHAR DEFAULT 'unknown'",
            "confidence_score": "FLOAT DEFAULT 0.5",
            "obsolescence_score": "FLOAT DEFAULT 0.0",
            "conflict_flag": "BOOLEAN DEFAULT false",
            "embedding_model": "VARCHAR",
            "embedding_version": "INTEGER DEFAULT 0",
        }
        existing = {
            row[1]
            for row in self._execute("PRAGMA table_info('papers')").fetchall()
        }
        for name, column_type in columns.items():
            if name not in existing:
                column_name = f'"{name}"' if name == "references" else name
                self._execute(f"ALTER TABLE papers ADD COLUMN {column_name} {column_type}")

        self._execute("""
            CREATE TABLE IF NOT EXISTS dedup_log (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_dedup_id'),
                kept_id VARCHAR NOT NULL,
                dropped_id VARCHAR NOT NULL,
                reason VARCHAR,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS extraction_results (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_dedup_id'),
                paper_id VARCHAR NOT NULL,
                extraction_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                llm_provider VARCHAR NOT NULL,
                llm_model VARCHAR NOT NULL,
                extraction_status VARCHAR DEFAULT 'pending',
                concepts JSON,
                methods JSON,
                claims JSON,
                cross_domain_hints JSON,
                raw_response VARCHAR,
                error_message VARCHAR,
                extraction_duration_seconds FLOAT
            )
        """)

    def insert_paper(self, record: dict[str, Any]) -> None:
        """
        Insert or update a paper record.
        """
        paper_id = record.get("id") or f"{record['source']}:{record['source_id']}"
        self._execute("""
            INSERT OR REPLACE INTO papers
            (
                id, source, source_id, title, abstract, authors, year, doi,
                pdf_url, landing_page_url, "references", citations, citation_count,
                superseded_by, peer_reviewed, retracted, language_original,
                confidence_score, obsolescence_score, conflict_flag,
                embedding_model, embedding_version, has_full_text, version,
                updated_timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
        """, [
            paper_id,
            record.get("source"),
            record.get("source_id"),
            record.get("title"),
            record.get("abstract"),
            json.dumps(record.get("authors", [])),
            record.get("year"),
            record.get("doi"),
            record.get("pdf_url"),
            record.get("landing_page_url"),
            json.dumps(record.get("references", [])),
            json.dumps(record.get("citations", [])),
            int(record.get("citation_count") or len(record.get("citations") or record.get("references") or [])),
            record.get("superseded_by"),
            bool(record.get("peer_reviewed", False)),
            bool(record.get("retracted", False)),
            record.get("language_original") or "unknown",
            float(record.get("confidence_score") or 0.5),
            float(record.get("obsolescence_score") or 0.0),
            bool(record.get("conflict_flag", False)),
            record.get("embedding_model"),
            int(record.get("embedding_version") or 0),
            bool(record.get("has_full_text", bool(record.get("pdf_url")))),
            record.get("version", 1),
        ])

    def batch_insert_papers(self, records: list[dict[str, Any]]) -> int:
        """
        Insert multiple paper records. Return count of inserted records.
        """
        for record in records:
            self.insert_paper(record)
        return len(records)

    def get_paper(self, paper_id: str) -> dict[str, Any] | None:
        """
        Retrieve a paper by ID.
        """
        result = self._execute(
            "SELECT * FROM papers WHERE id = ?",
            [paper_id]
        ).fetchone()
        if result is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return self._parse_paper_row(dict(zip(cols, result)))

    def search_by_title(self, title_query: str, limit: int = 50) -> list[dict[str, Any]]:
        """
        Search papers by title substring.
        """
        results = self._execute("""
            SELECT * FROM papers
            WHERE title ILIKE ?
            LIMIT ?
        """, [f"%{title_query}%", limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [self._parse_paper_row(dict(zip(cols, row))) for row in results]

    def list_papers(self, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        """
        List all papers with pagination.
        """
        results = self._execute("""
            SELECT * FROM papers
            ORDER BY added_timestamp DESC
            LIMIT ? OFFSET ?
        """, [limit, offset]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [self._parse_paper_row(dict(zip(cols, row))) for row in results]

    @staticmethod
    def _parse_paper_row(data: dict[str, Any]) -> dict[str, Any]:
        for field in ["authors", "references", "citations"]:
            if data.get(field):
                try:
                    data[field] = json.loads(data[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return data

    def count_papers(self) -> int:
        """
        Count total papers in database.
        """
        result = self._execute("SELECT COUNT(*) FROM papers").fetchone()
        return result[0] if result else 0

    def count_dedup_events(self) -> int:
        """
        Count deduplication decisions in the database.
        """
        result = self._execute("SELECT COUNT(*) FROM dedup_log").fetchone()
        return int(result[0]) if result else 0

    def log_dedup(self, kept_id: str, dropped_id: str, reason: str) -> None:
        """
        Log a deduplication decision.
        """
        self._execute("""
            INSERT INTO dedup_log (kept_id, dropped_id, reason)
            VALUES (?, ?, ?)
        """, [kept_id, dropped_id, reason])

    def save_extraction_result(
        self,
        paper_id: str,
        llm_provider: str,
        llm_model: str,
        concepts: list[dict[str, Any]] | None = None,
        methods: list[dict[str, Any]] | None = None,
        claims: list[dict[str, Any]] | None = None,
        cross_domain_hints: list[str] | None = None,
        raw_response: str | None = None,
        error_message: str | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        """
        Save extraction results to database. Returns the result ID.
        """
        status = "success" if error_message is None else "failed"
        
        result_id = self._execute("""
            INSERT INTO extraction_results
            (paper_id, llm_provider, llm_model, extraction_status, concepts, methods, claims, 
             cross_domain_hints, raw_response, error_message, extraction_duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, [
            paper_id,
            llm_provider,
            llm_model,
            status,
            json.dumps(concepts or []),
            json.dumps(methods or []),
            json.dumps(claims or []),
            json.dumps(cross_domain_hints or []),
            raw_response,
            error_message,
            duration_seconds,
        ]).fetchone()

        return int(result_id[0]) if result_id else 0

    def get_extraction_result(self, result_id: int) -> dict[str, Any] | None:
        """
        Retrieve an extraction result by ID.
        """
        result = self._execute(
            "SELECT * FROM extraction_results WHERE id = ?",
            [result_id]
        ).fetchone()
        
        if result is None:
            return None
        
        cols = [desc[0] for desc in self.conn.description]
        data = dict(zip(cols, result))
        
        # Parse JSON fields
        for field in ["concepts", "methods", "claims", "cross_domain_hints"]:
            if data.get(field):
                try:
                    data[field] = json.loads(data[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        
        return data

    def get_paper_extractions(self, paper_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Get all extraction results for a specific paper.
        """
        results = self._execute("""
            SELECT * FROM extraction_results
            WHERE paper_id = ?
            ORDER BY extraction_timestamp DESC
            LIMIT ?
        """, [paper_id, limit]).fetchall()
        
        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        
        for row in results:
            data = dict(zip(cols, row))
            # Parse JSON fields
            for field in ["concepts", "methods", "claims", "cross_domain_hints"]:
                if data.get(field):
                    try:
                        data[field] = json.loads(data[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            data_list.append(data)
        
        return data_list

    def list_extraction_results(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        List recent extraction results across all papers.
        """
        results = self._execute("""
            SELECT * FROM extraction_results
            ORDER BY extraction_timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()

        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        for row in results:
            data = dict(zip(cols, row))
            for field in ["concepts", "methods", "claims", "cross_domain_hints"]:
                if data.get(field):
                    try:
                        data[field] = json.loads(data[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            data_list.append(data)
        return data_list

    def clear_extraction_results(self) -> None:
        """
        Delete stored extraction runs while keeping harvested paper metadata.
        """
        self._execute("DELETE FROM extraction_results")

    def clear_all(self) -> None:
        """
        Delete all mutable metadata tables and keep the schema intact.
        """
        with self._lock:
            self._execute("BEGIN TRANSACTION")
            try:
                self._execute("DELETE FROM extraction_results")
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
