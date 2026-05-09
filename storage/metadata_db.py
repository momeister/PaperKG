from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb


class MetadataDB:
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
                paper_type VARCHAR,
                concepts JSON,
                methods JSON,
                claims JSON,
                cross_domain_hints JSON,
                terminology_conflicts JSON,
                temporal_coverage JSON,
                mathematical_content JSON,
                raw_response VARCHAR,
                error_message VARCHAR,
                extraction_duration_seconds FLOAT
            )
        """)
        extraction_columns = {
            "paper_type": "VARCHAR",
            "concept_candidates": "JSON",
            "method_candidates": "JSON",
            "relations": "JSON",
            "terminology_conflicts": "JSON",
            "temporal_coverage": "JSON",
            "mathematical_content": "JSON",
        }
        existing_extraction = {
            row[1]
            for row in self._execute("PRAGMA table_info('extraction_results')").fetchall()
        }
        for name, column_type in extraction_columns.items():
            if name not in existing_extraction:
                self._execute(f"ALTER TABLE extraction_results ADD COLUMN {name} {column_type}")

        self._execute("""
            CREATE TABLE IF NOT EXISTS batch_jobs (
                job_id VARCHAR PRIMARY KEY,
                status VARCHAR NOT NULL,
                papers_total INTEGER DEFAULT 0,
                papers_processed INTEGER DEFAULT 0,
                papers_failed INTEGER DEFAULT 0,
                error_message VARCHAR,
                request_payload JSON,
                llm_provider VARCHAR,
                superseded_by VARCHAR,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS batch_job_items (
                job_id VARCHAR NOT NULL,
                paper_id VARCHAR NOT NULL,
                pdf_path VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                error_message VARCHAR,
                started_timestamp TIMESTAMP,
                completed_timestamp TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, paper_id)
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS entity_embeddings (
                label_norm VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                model VARCHAR NOT NULL,
                backend VARCHAR NOT NULL,
                dimension INTEGER NOT NULL,
                embedding_version INTEGER DEFAULT 1,
                vector JSON NOT NULL,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (label_norm, model, embedding_version)
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS extraction_quality (
                paper_id TEXT NOT NULL,
                concept_count INTEGER NOT NULL,
                method_count INTEGER NOT NULL,
                claim_count INTEGER NOT NULL,
                has_formulas BOOLEAN NOT NULL,
                auto_detected_concepts INTEGER NOT NULL,
                parse_quality TEXT NOT NULL,
                call_1_tokens_used INTEGER,
                call_2_tokens_used INTEGER,
                duration_seconds FLOAT,
                model TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS entity_review_queue (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_dedup_id'),
                paper_id VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                entity_type VARCHAR,
                canonical_id VARCHAR,
                suggested_canonical VARCHAR,
                review_status VARCHAR DEFAULT 'pending',
                evidence VARCHAR,
                merge_candidates JSON,
                source_field VARCHAR,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("CREATE INDEX IF NOT EXISTS idx_batch_jobs_status ON batch_jobs(status)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_batch_items_status ON batch_job_items(job_id, status)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_embeddings_label ON entity_embeddings(label_norm)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_extraction_quality_paper ON extraction_quality(paper_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_entity_review_status ON entity_review_queue(review_status)")

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

    def resolve_paper_id(self, identifier: str | None) -> str | None:
        """Resolve aliases such as PDF storage IDs, arXiv IDs, and DOI strings to a stored paper ID."""
        paper = self.resolve_paper(identifier)
        return str(paper.get("id")) if paper else None

    def resolve_paper(self, identifier: str | None) -> dict[str, Any] | None:
        """Return the stored paper matching an identifier or known local alias."""
        raw = str(identifier or "").strip()
        if not raw:
            return None

        direct = self.get_paper(raw)
        if direct is not None:
            return direct

        aliases = self._identifier_aliases(raw)
        for record in self.list_papers(limit=50000):
            record_aliases = self._paper_aliases(record)
            if aliases & record_aliases:
                return record
        return None

    def ensure_paper_record(
        self,
        paper_id: str,
        title: str | None = None,
        year: int | None = None,
        pdf_path: str | None = None,
        source: str | None = None,
        source_id: str | None = None,
    ) -> str:
        """
        Ensure a paper row exists and fill missing title/year/PDF metadata.

        Returns the canonical paper ID used for extraction history.
        """
        canonical_id = self.resolve_paper_id(paper_id) or self._canonical_from_identifier(paper_id) or paper_id
        existing = self.get_paper(canonical_id)
        if existing is not None:
            self.update_paper_metadata_if_missing(
                canonical_id,
                title=title,
                year=year,
                pdf_path=pdf_path,
            )
            return canonical_id

        inferred_source, inferred_source_id = self._infer_source(canonical_id)
        self.insert_paper(
            {
                "id": canonical_id,
                "source": source or inferred_source,
                "source_id": source_id or inferred_source_id,
                "title": title or "",
                "abstract": "",
                "authors": [],
                "year": year,
                "pdf_url": pdf_path,
                "landing_page_url": None,
                "has_full_text": bool(pdf_path),
            }
        )
        return canonical_id

    def update_paper_metadata_if_missing(
        self,
        paper_id: str,
        title: str | None = None,
        year: int | None = None,
        pdf_path: str | None = None,
    ) -> None:
        """Fill missing paper title/year/PDF path without overwriting existing metadata."""
        self._execute(
            """
            UPDATE papers
            SET
                title = CASE WHEN (title IS NULL OR title = '') AND ? IS NOT NULL THEN ? ELSE title END,
                year = CASE WHEN year IS NULL AND ? IS NOT NULL THEN ? ELSE year END,
                pdf_url = CASE WHEN (pdf_url IS NULL OR pdf_url = '') AND ? IS NOT NULL THEN ? ELSE pdf_url END,
                has_full_text = CASE WHEN ? IS NOT NULL THEN true ELSE has_full_text END,
                updated_timestamp = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            [title, title, year, year, pdf_path, pdf_path, pdf_path, paper_id],
        )

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

    @classmethod
    def _paper_aliases(cls, record: dict[str, Any]) -> set[str]:
        aliases: set[str] = set()
        for value in [
            record.get("id"),
            record.get("source_id"),
            record.get("doi"),
            record.get("pdf_url"),
            record.get("landing_page_url"),
        ]:
            aliases.update(cls._identifier_aliases(value))

        source = str(record.get("source") or "").strip()
        source_id = str(record.get("source_id") or "").strip()
        if source and source_id:
            aliases.update(cls._identifier_aliases(f"{source}:{source_id}"))
            title_slug = cls._slug(record.get("title") or "")
            if title_slug:
                aliases.update(cls._identifier_aliases(f"{source}__{title_slug}__{source_id}"))
        return aliases

    @classmethod
    def _identifier_aliases(cls, value: Any) -> set[str]:
        raw = str(value or "").strip()
        if not raw:
            return set()

        aliases = {cls._normalize_identifier(raw)}
        stem = Path(raw).stem.rsplit("_v", 1)[0]
        if stem and stem != raw:
            aliases.add(cls._normalize_identifier(stem))

        doi = raw.lower().removeprefix("https://doi.org/").removeprefix("doi:")
        if "/" in doi and not doi.startswith("http"):
            aliases.add(cls._normalize_identifier(doi))

        arxiv_id = cls._extract_arxiv_id(raw)
        if arxiv_id:
            bare_arxiv = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
            aliases.update(
                {
                    cls._normalize_identifier(arxiv_id),
                    cls._normalize_identifier(bare_arxiv),
                    cls._normalize_identifier(f"arxiv:{bare_arxiv}"),
                    cls._normalize_identifier(f"arxiv_{bare_arxiv}"),
                }
            )
        return {alias for alias in aliases if alias}

    @staticmethod
    def _normalize_identifier(value: str) -> str:
        return re.sub(r"[^a-z0-9]+", "", str(value or "").lower())

    @staticmethod
    def _extract_arxiv_id(value: str) -> str | None:
        match = re.search(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", str(value or ""), flags=re.IGNORECASE)
        return match.group(0) if match else None

    @staticmethod
    def _slug(value: str) -> str:
        text = str(value or "").strip().lower()
        text = text.replace("/", "_").replace("\\", "_").replace(":", " ")
        text = re.sub(r"\s+", "-", text)
        text = re.sub(r"[^a-z0-9._-]+", "-", text)
        text = re.sub(r"-{2,}", "-", text).strip("._-")
        return text[:120]

    @classmethod
    def _infer_source(cls, paper_id: str) -> tuple[str, str]:
        arxiv_id = cls._extract_arxiv_id(paper_id)
        if str(paper_id).startswith("arxiv:") or arxiv_id:
            return "arxiv", re.sub(r"v\d+$", "", arxiv_id or str(paper_id).split(":", 1)[-1])
        if "/" in str(paper_id) and str(paper_id).lower().startswith("10."):
            return "doi", str(paper_id)
        return "local", str(paper_id)

    @classmethod
    def _canonical_from_identifier(cls, paper_id: str) -> str | None:
        arxiv_id = cls._extract_arxiv_id(paper_id)
        if arxiv_id:
            bare_arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
            return f"arxiv:{bare_arxiv_id}"
        value = str(paper_id or "").strip()
        if value.lower().startswith("10.") and "/" in value:
            return value
        return None

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
        paper_type: str | None = None,
        concepts: list[dict[str, Any]] | None = None,
        methods: list[dict[str, Any]] | None = None,
        concept_candidates: list[dict[str, Any]] | None = None,
        method_candidates: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        claims: list[dict[str, Any]] | None = None,
        cross_domain_hints: list[dict[str, Any]] | None = None,
        terminology_conflicts: list[dict[str, Any]] | None = None,
        temporal_coverage: dict[str, Any] | None = None,
        mathematical_content: dict[str, Any] | None = None,
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
            (paper_id, llm_provider, llm_model, extraction_status, paper_type, concepts, methods,
             concept_candidates, method_candidates, relations, claims,
             cross_domain_hints, terminology_conflicts, temporal_coverage, mathematical_content,
             raw_response, error_message, extraction_duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, [
            paper_id,
            llm_provider,
            llm_model,
            status,
            paper_type,
            json.dumps(concepts or []),
            json.dumps(methods or []),
            json.dumps(concept_candidates or []),
            json.dumps(method_candidates or []),
            json.dumps(relations or []),
            json.dumps(claims or []),
            json.dumps(cross_domain_hints or []),
            json.dumps(terminology_conflicts or []),
            json.dumps(temporal_coverage or {}),
            json.dumps(mathematical_content or {}),
            raw_response,
            error_message,
            duration_seconds,
        ]).fetchone()

        if status == "success":
            self.enqueue_pending_entities(
                paper_id=paper_id,
                entities=list(concepts or []) + list(methods or []) + list(concept_candidates or []) + list(method_candidates or []),
            )

        return int(result_id[0]) if result_id else 0

    def enqueue_pending_entities(
        self,
        paper_id: str,
        entities: list[dict[str, Any]],
    ) -> int:
        """Persist pending entity review items for later approval/merge."""
        inserted = 0
        now = datetime.now()
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            if str(entity.get("review_status") or "").lower() != "pending":
                continue
            label = str(entity.get("label") or "").strip()
            if not label:
                continue
            self._execute("""
                INSERT INTO entity_review_queue
                (
                    paper_id, label, entity_type, canonical_id, suggested_canonical,
                    review_status, evidence, merge_candidates, source_field,
                    created_timestamp, updated_timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                paper_id,
                label,
                entity.get("entity_type"),
                entity.get("canonical_id"),
                entity.get("suggested_canonical") or entity.get("canonical_label") or label,
                "pending",
                entity.get("evidence") or entity.get("evidence_span") or entity.get("context") or entity.get("description") or "",
                json.dumps(entity.get("merge_candidates") or []),
                entity.get("candidate_reason") or entity.get("acceptance_reason") or "",
                now,
                now,
            ])
            inserted += 1
        return inserted

    def save_extraction_quality(
        self,
        paper_id: str,
        concept_count: int,
        method_count: int,
        claim_count: int,
        has_formulas: bool,
        auto_detected_concepts: int,
        parse_quality: str,
        call_1_tokens_used: int | None = None,
        call_2_tokens_used: int | None = None,
        duration_seconds: float | None = None,
        model: str | None = None,
    ) -> None:
        """
        Persist quality telemetry for one extraction run.

        This table is intentionally append-only so quality trends can be
        inspected after prompt, parser, or model changes.
        """
        self._execute("""
            INSERT INTO extraction_quality
            (
                paper_id, concept_count, method_count, claim_count, has_formulas,
                auto_detected_concepts, parse_quality, call_1_tokens_used,
                call_2_tokens_used, duration_seconds, model, timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            paper_id,
            int(concept_count),
            int(method_count),
            int(claim_count),
            bool(has_formulas),
            int(auto_detected_concepts),
            parse_quality,
            call_1_tokens_used,
            call_2_tokens_used,
            duration_seconds,
            model,
            datetime.now(),
        ])

    def list_extraction_quality(
        self,
        paper_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        List recent extraction quality telemetry rows.
        """
        if paper_id is None:
            rows = self._execute("""
                SELECT * FROM extraction_quality
                ORDER BY timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        else:
            rows = self._execute("""
                SELECT * FROM extraction_quality
                WHERE paper_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, [paper_id, limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

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
        for field in self.EXTRACTION_JSON_FIELDS:
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
        aliases = {paper_id}
        resolved = self.resolve_paper(paper_id)
        if resolved is not None:
            canonical_id = str(resolved.get("id") or paper_id)
            aliases.add(canonical_id)
            raw_aliases = {
                str(resolved.get("id") or ""),
                str(resolved.get("source_id") or ""),
                str(resolved.get("doi") or ""),
            }
            source = str(resolved.get("source") or "")
            source_id = str(resolved.get("source_id") or "")
            if source and source_id:
                raw_aliases.add(f"{source}:{source_id}")
                title_slug = self._slug(resolved.get("title") or "")
                if title_slug:
                    raw_aliases.add(f"{source}__{title_slug}__{source_id}")
            arxiv_id = self._extract_arxiv_id(" ".join(raw_aliases))
            if arxiv_id:
                bare_arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
                raw_aliases.add(f"arxiv:{bare_arxiv_id}")
            aliases.update(alias for alias in raw_aliases if alias)

        placeholders = ", ".join("?" for _ in aliases)
        results = self._execute(f"""
            SELECT * FROM extraction_results
            WHERE paper_id IN ({placeholders})
            ORDER BY extraction_timestamp DESC
            LIMIT ?
        """, [*aliases, limit]).fetchall()
        
        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        
        for row in results:
            data = dict(zip(cols, row))
            # Parse JSON fields
            for field in self.EXTRACTION_JSON_FIELDS:
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
            for field in self.EXTRACTION_JSON_FIELDS:
                if data.get(field):
                    try:
                        data[field] = json.loads(data[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            data_list.append(data)
        return data_list

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

    def list_entity_review_queue(
        self,
        status: str | None = "pending",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List entity review queue items for approval/merge workflows."""
        if status is None:
            rows = self._execute("""
                SELECT * FROM entity_review_queue
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        else:
            rows = self._execute("""
                SELECT * FROM entity_review_queue
                WHERE review_status = ?
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [status, limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        output = []
        for row in rows:
            item = dict(zip(cols, row))
            try:
                item["merge_candidates"] = json.loads(item.get("merge_candidates") or "[]")
            except (TypeError, json.JSONDecodeError):
                item["merge_candidates"] = []
            output.append(item)
        return output

    def mark_batch_job_superseded(self, job_id: str, superseded_by: str) -> None:
        now = datetime.now()
        self._execute("""
            UPDATE batch_jobs
            SET status = 'superseded',
                superseded_by = ?,
                updated_timestamp = ?
            WHERE job_id = ?
        """, [superseded_by, now, job_id])

    @staticmethod
    def _normalize_embedding_label(label: str) -> str:
        return " ".join(label.lower().split())

    def upsert_entity_embedding(
        self,
        label: str,
        vector: list[float],
        model: str,
        backend: str,
        dimension: int,
        embedding_version: int = 1,
    ) -> None:
        """
        Persist a normalized entity embedding for reuse across batch runs.
        """
        now = datetime.now()
        self._execute("""
            INSERT INTO entity_embeddings
            (label_norm, label, model, backend, dimension, embedding_version, vector, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (label_norm, model, embedding_version) DO UPDATE SET
                label = EXCLUDED.label,
                backend = EXCLUDED.backend,
                dimension = EXCLUDED.dimension,
                vector = EXCLUDED.vector,
                updated_timestamp = EXCLUDED.updated_timestamp
        """, [
            self._normalize_embedding_label(label),
            label,
            model,
            backend,
            int(dimension),
            int(embedding_version),
            json.dumps(vector),
            now,
        ])

    def get_entity_embedding(
        self,
        label: str,
        model: str,
        embedding_version: int = 1,
    ) -> dict[str, Any] | None:
        result = self._execute("""
            SELECT * FROM entity_embeddings
            WHERE label_norm = ? AND model = ? AND embedding_version = ?
        """, [self._normalize_embedding_label(label), model, int(embedding_version)]).fetchone()
        if result is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        data = dict(zip(cols, result))
        data["vector"] = json.loads(data["vector"])
        return data

    def list_entity_embeddings(self, model: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        if model is None:
            results = self._execute("""
                SELECT * FROM entity_embeddings
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        else:
            results = self._execute("""
                SELECT * FROM entity_embeddings
                WHERE model = ?
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [model, limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        for row in results:
            data = dict(zip(cols, row))
            data["vector"] = json.loads(data["vector"])
            data_list.append(data)
        return data_list

    def clear_extraction_results(self) -> None:
        """
        Delete stored extraction runs while keeping harvested paper metadata.
        """
        self._execute("DELETE FROM extraction_results")
        self._execute("DELETE FROM entity_review_queue")

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
