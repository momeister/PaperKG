from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb


class MetadataDB:
    """
    DuckDB-backed metadata storage for papers.
    """

    def __init__(self, db_path: str = "data/metadata.duckdb") -> None:
        self.db_path = db_path
        db_file = Path(db_path)
        db_file.parent.mkdir(parents=True, exist_ok=True)
        if db_file.exists() and db_file.stat().st_size == 0:
            db_file.unlink()
        self.conn = duckdb.connect(db_path)
        self._init_schema()

    def _init_schema(self) -> None:
        """
        Initialize all required tables if they don't exist.
        """
        self.conn.execute("CREATE SEQUENCE IF NOT EXISTS seq_dedup_id")

        self.conn.execute("""
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
                has_full_text BOOLEAN DEFAULT false,
                version INTEGER DEFAULT 1,
                added_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS paper_sources (
                paper_id VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                source_id VARCHAR,
                source_url VARCHAR,
                added_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (paper_id, source)
            )
        """)

        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS dedup_log (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_dedup_id'),
                kept_id VARCHAR NOT NULL,
                dropped_id VARCHAR NOT NULL,
                reason VARCHAR,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self.conn.execute("""
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
        self.conn.execute("""
            INSERT OR REPLACE INTO papers
            (id, source, source_id, title, abstract, authors, year, doi, pdf_url, landing_page_url, has_full_text, version, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
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
            bool(record.get("pdf_url")),
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
        result = self.conn.execute(
            "SELECT * FROM papers WHERE id = ?",
            [paper_id]
        ).fetchone()
        if result is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return dict(zip(cols, result))

    def search_by_title(self, title_query: str, limit: int = 50) -> list[dict[str, Any]]:
        """
        Search papers by title substring.
        """
        results = self.conn.execute("""
            SELECT * FROM papers
            WHERE title ILIKE ?
            LIMIT ?
        """, [f"%{title_query}%", limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in results]

    def list_papers(self, limit: int = 1000, offset: int = 0) -> list[dict[str, Any]]:
        """
        List all papers with pagination.
        """
        results = self.conn.execute("""
            SELECT * FROM papers
            ORDER BY added_timestamp DESC
            LIMIT ? OFFSET ?
        """, [limit, offset]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in results]

    def count_papers(self) -> int:
        """
        Count total papers in database.
        """
        result = self.conn.execute("SELECT COUNT(*) FROM papers").fetchone()
        return result[0] if result else 0

    def log_dedup(self, kept_id: str, dropped_id: str, reason: str) -> None:
        """
        Log a deduplication decision.
        """
        self.conn.execute("""
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
        
        result_id = self.conn.execute("""
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
        result = self.conn.execute(
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
        results = self.conn.execute("""
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

    def close(self) -> None:
        """
        Close database connection.
        """
        self.conn.close()
