from __future__ import annotations

import json
import re
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class PapersMixin(_Base):
    """MetadataDB papers operations (mixin)."""

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

    def delete_paper(self, paper_id: str) -> bool:
        if self.get_paper(paper_id) is None:
            return False
        self._execute("DELETE FROM papers WHERE id = ?", [paper_id])
        self._execute("DELETE FROM paper_sources WHERE paper_id = ?", [paper_id])
        self._execute("DELETE FROM extraction_results WHERE paper_id = ?", [paper_id])
        try:
            self._execute("DELETE FROM entity_embeddings WHERE paper_id = ?", [paper_id])
        except Exception:
            pass
        try:
            self._execute("DELETE FROM batch_job_items WHERE paper_id = ?", [paper_id])
        except Exception:
            pass
        return True

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
                pdf_url = CASE WHEN ? IS NOT NULL THEN ? ELSE pdf_url END,
                has_full_text = CASE WHEN ? IS NOT NULL THEN true ELSE has_full_text END,
                updated_timestamp = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            [title, title, year, year, pdf_path, pdf_path, pdf_path, paper_id],
        )

    def update_paper_title(self, paper_id: str, title: str) -> None:
        """Overwrite a paper's title outright (e.g. replacing a garbled-extraction
        filename-derived title with one inferred from the PDF itself)."""
        self._execute(
            "UPDATE papers SET title = ?, updated_timestamp = CURRENT_TIMESTAMP WHERE id = ?",
            [title, paper_id],
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
    def _legacy_arxiv_category_re() -> str:
        return (
            r"(?:astro-ph|cond-mat|cs|gr-qc|hep-ex|hep-lat|hep-ph|hep-th|"
            r"math-ph|math|nlin|nucl-ex|nucl-th|physics|q-bio|q-fin|quant-ph|stat)"
        )

    @classmethod
    def _extract_arxiv_id(cls, value: str) -> str | None:
        text = str(value or "")
        match = re.search(r"(?<!\d)(\d{4}\.\d{4,5})(?:v\d+)?(?!\d)", text, flags=re.IGNORECASE)
        if match:
            return match.group(0)
        legacy = re.search(
            rf"(?<![A-Za-z0-9])({cls._legacy_arxiv_category_re()})\s*[/_:.-]\s*(\d{{7}})(?:v\d+)?(?!\d)",
            text,
            flags=re.IGNORECASE,
        )
        if legacy:
            return f"{legacy.group(1).lower()}/{legacy.group(2)}"
        return None

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
