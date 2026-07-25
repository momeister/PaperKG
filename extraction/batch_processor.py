from __future__ import annotations

from dataclasses import dataclass
import json
import hashlib
import logging
import time
from typing import Any

from extraction.entity_extractor import EntityExtractor, extraction_failure_reason
from extraction.entity_linker import ExtractionPipeline
from extraction.embedding_engine import EmbeddingEngine
from parsing.parser_router import ParserRouter, ParserType
from query.llm_errors import classify_llm_error, is_provider_limit, parse_tagged_error, tag_error
from query.llm_router import LLMRouter

logger = logging.getLogger(__name__)


@dataclass
class BatchJobStatus:
    """Status of batch processing job."""

    job_id: str
    status: str  # "pending", "processing", "completed", "failed"
    papers_total: int
    papers_processed: int
    papers_failed: int
    error_message: str | None = None
    superseded_by: str | None = None


class BatchProcessor:
    """
    Orchestrates batch extraction of entities from multiple papers.
    Supports configurable LLM providers, parser selection, and error handling.

    Production implementation would integrate:
    - Celery for distributed job scheduling
    - Redis for job state management
    - Progress tracking and resumable jobs
    - Error recovery and retry logic
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        parser_router: ParserRouter,
        embedding_engine: EmbeddingEngine | None = None,
        metadata_db: Any | None = None,
        metadata_db_factory: Any | None = None,
        link_concepts: bool = True,
        embed_concepts: bool = True,
        max_retries: int = 0,
        retry_delay_seconds: float = 0.0,
        quality_db_path: str | None = None,
    ) -> None:
        """
        Initialize batch processor.

        Args:
            llm_router: Configured LLMRouter for extraction
            parser_router: Configured ParserRouter for document parsing
            embedding_engine: Optional EmbeddingEngine for entity embeddings
        """
        self.llm_router = llm_router
        self.parser_router = parser_router
        self.embedding_engine = embedding_engine or EmbeddingEngine()
        self.metadata_db = metadata_db
        self.metadata_db_factory = metadata_db_factory
        self.link_concepts = link_concepts
        self.embed_concepts = embed_concepts
        self.max_retries = max(0, int(max_retries))
        self.retry_delay_seconds = max(0.0, float(retry_delay_seconds))
        self.extraction_pipeline = ExtractionPipeline(
            llm_router,
            embedding_engine=self.embedding_engine,
            quality_db_path=quality_db_path,
        )
        self._job_states: dict[str, BatchJobStatus] = {}

    def process_papers(
        self,
        paper_ids: list[str],
        pdf_paths: dict[str, str],
        job_id: str | None = None,
        llm_provider: str | None = None,
        llm_overrides: dict[str, Any] | None = None,
        parser_selection: dict[str, ParserType] | None = None,
        resume: bool = True,
        supersede_job_id: str | None = None,
        texts: dict[str, str] | None = None,
    ) -> BatchJobStatus:
        """
        Process batch of papers: parse, extract, embed.

        Args:
            paper_ids: List of paper identifiers
            pdf_paths: Dict of {paper_id -> pdf_file_path}
            job_id: Optional job identifier (generated if not provided)
            llm_provider: Optional LLM provider override
            llm_overrides: Optional LLM settings overrides
            parser_selection: Optional {paper_id -> ParserType} overrides
            resume: Skip papers already completed in persistent job state
            supersede_job_id: Existing job to mark as superseded by this run
            texts: Optional {paper_id -> text} for papers without a PDF
                (abstract-only extraction); used when pdf_paths has no entry

        Returns:
            BatchJobStatus with processing results
        """
        import uuid

        if job_id is None:
            job_id = str(uuid.uuid4())

        status = BatchJobStatus(
            job_id=job_id,
            status="processing",
            papers_total=len(paper_ids),
            papers_processed=0,
            papers_failed=0,
        )

        self._job_states[job_id] = status
        metadata_db, close_metadata_db = self._metadata_db()
        completed_items: set[str] = set()
        request_payload = {
            "paper_ids": paper_ids,
            "pdf_paths": pdf_paths,
            "llm_provider": llm_provider,
            "llm_overrides": llm_overrides or {},
            "parser_selection": {
                paper_id: str(parser_type.value if hasattr(parser_type, "value") else parser_type)
                for paper_id, parser_type in (parser_selection or {}).items()
            },
            "request_hash": self._request_hash(paper_ids, pdf_paths, llm_provider, llm_overrides),
        }

        try:
            if metadata_db is not None:
                if supersede_job_id:
                    metadata_db.mark_batch_job_superseded(supersede_job_id, job_id)

                previous_job = metadata_db.get_batch_job(job_id)
                if previous_job and previous_job.get("status") == "superseded":
                    return self._status_from_record(previous_job)

                if resume:
                    completed_items = {
                        str(item["paper_id"])
                        for item in metadata_db.get_batch_job_items(job_id)
                        if item.get("status") == "completed"
                    }
                    status.papers_processed = len(completed_items)

                metadata_db.upsert_batch_job(
                    job_id=job_id,
                    status=status.status,
                    papers_total=status.papers_total,
                    papers_processed=status.papers_processed,
                    papers_failed=status.papers_failed,
                    request_payload=request_payload,
                    llm_provider=llm_provider,
                )

                for paper_id in paper_ids:
                    if paper_id not in completed_items:
                        metadata_db.upsert_batch_job_item(
                            job_id,
                            paper_id,
                            pdf_paths.get(paper_id),
                            "pending",
                        )

            for paper_id in paper_ids:
                if paper_id in completed_items:
                    continue

                if metadata_db is not None:
                    current_job = metadata_db.get_batch_job(job_id)
                    if current_job and current_job.get("status") in {"cancelled", "superseded"}:
                        return self._status_from_record(current_job)

                pdf_path = pdf_paths.get(paper_id)
                inline_text = str((texts or {}).get(paper_id) or "").strip()
                if not pdf_path and not inline_text:
                    missing_message = f"Missing PDF path and abstract text for {paper_id}"
                    status.papers_failed += 1
                    if status.error_message is None:
                        status.error_message = missing_message
                    if metadata_db is not None:
                        metadata_db.upsert_batch_job_item(
                            job_id,
                            paper_id,
                            None,
                            "failed",
                            error_message=missing_message,
                        )
                        self._persist_job_status(metadata_db, status, request_payload, llm_provider)
                    continue

                last_error: Exception | None = None
                for attempt in range(self.max_retries + 1):
                    if metadata_db is not None:
                        metadata_db.upsert_batch_job_item(
                            job_id,
                            paper_id,
                            pdf_path,
                            "processing",
                            attempts=attempt + 1,
                        )
                        self._persist_job_status(metadata_db, status, request_payload, llm_provider)
                    try:
                        if pdf_path:
                            forced_parser = parser_selection.get(paper_id) if parser_selection else None
                            parsed = self.parser_router.parse(pdf_path, paper_id, force_parser=forced_parser)
                            paper_text = parsed.text
                        else:
                            paper_text = inline_text

                        extraction = self.extraction_pipeline.process(
                            paper_id,
                            paper_text,
                            provider=llm_provider,
                            overrides=llm_overrides,
                            link_concepts=self.link_concepts,
                        )
                        failure_reason = extraction_failure_reason(extraction)

                        if failure_reason is not None and metadata_db is None:
                            raise RuntimeError(failure_reason)

                        if self.embed_concepts and failure_reason is None:
                            concept_labels = [c.get("label", "") for c in extraction.concepts]
                            embedding_results = self.embedding_engine.embed_batch(concept_labels)
                            if metadata_db is not None:
                                for embedding in embedding_results:
                                    metadata_db.upsert_entity_embedding(
                                        label=embedding.entity_label,
                                        vector=embedding.vector.astype(float).tolist(),
                                        model=embedding.model,
                                        backend=embedding.backend,
                                        dimension=embedding.dimension,
                                    )

                        if metadata_db is not None:
                            canonical_paper_id = metadata_db.ensure_paper_record(
                                paper_id,
                                title=self._title_from_text(paper_text),
                                year=self._year_from_extraction(extraction),
                                pdf_path=pdf_path,
                            )
                            metadata_db.save_extraction_result(
                                paper_id=canonical_paper_id,
                                llm_provider=llm_provider or "default",
                                llm_model=getattr(
                                    self.llm_router.provider_settings(llm_provider),
                                    "model",
                                    "unknown",
                                ) if hasattr(self.llm_router, "provider_settings") else "unknown",
                                paper_type=extraction.paper_type,
                                concepts=extraction.concepts,
                                methods=extraction.methods,
                                concept_candidates=extraction.concept_candidates,
                                method_candidates=extraction.method_candidates,
                                relations=extraction.relations,
                                claims=extraction.claims,
                                cross_domain_hints=extraction.cross_domain_hints,
                                terminology_conflicts=extraction.terminology_conflicts,
                                temporal_coverage=extraction.temporal_coverage,
                                mathematical_content=extraction.mathematical_content,
                                raw_response=extraction.raw_response,
                                error_message=failure_reason,
                            )

                        if failure_reason is not None:
                            raise RuntimeError(failure_reason)

                        status.papers_processed += 1
                        last_error = None
                        if metadata_db is not None:
                            metadata_db.upsert_batch_job_item(
                                job_id,
                                paper_id,
                                pdf_path,
                                "completed",
                                attempts=attempt + 1,
                            )
                            self._persist_job_status(metadata_db, status, request_payload, llm_provider)
                        break
                    except Exception as exc:
                        last_error = exc
                        error_kind = parse_tagged_error(str(exc))[0] or classify_llm_error(str(exc))[0]
                        # Bei einem Rate-Limit lohnt genau ein Wiederholungsversuch;
                        # bei erschoepftem Kontingent oder ungueltigem Key nicht.
                        retriable = attempt < self.max_retries and error_kind != "quota" and error_kind != "auth"
                        if metadata_db is not None:
                            metadata_db.upsert_batch_job_item(
                                job_id,
                                paper_id,
                                pdf_path,
                                "pending" if retriable else "failed",
                                attempts=attempt + 1,
                                error_message=str(exc),
                            )
                        if not retriable:
                            break
                        if self.retry_delay_seconds:
                            time.sleep(self.retry_delay_seconds)

                if last_error is not None:
                    status.papers_failed += 1
                    if status.error_message is None:
                        status.error_message = str(last_error)
                    if metadata_db is not None:
                        self._persist_job_status(metadata_db, status, request_payload, llm_provider)

                    # Anbieter-Limit erreicht (Kontingent/429/Auth): jeder weitere
                    # Aufruf scheitert genauso. Frueher lief der Batch stur weiter und
                    # verbrannte hunderte Paper an derselben Absage — die restlichen
                    # bleiben jetzt `pending` und sind mit einem neuen Lauf nachholbar.
                    limit_kind = parse_tagged_error(str(last_error))[0] or classify_llm_error(str(last_error))[0]
                    if is_provider_limit(limit_kind):
                        _kind, human = classify_llm_error(str(last_error))
                        status.status = "failed"
                        status.error_message = tag_error(
                            limit_kind,
                            f"{human} Batch nach {status.papers_processed + status.papers_failed} von "
                            f"{status.papers_total} Papern gestoppt; der Rest bleibt offen und kann "
                            "nach dem Zurücksetzen des Limits erneut gestartet werden.",
                        )
                        logger.warning("Batch %s gestoppt: %s", job_id, status.error_message)
                        if metadata_db is not None:
                            self._persist_job_status(metadata_db, status, request_payload, llm_provider)
                        return status

            status.status = "completed_with_errors" if status.papers_failed else "completed"
            if metadata_db is not None:
                self._persist_job_status(metadata_db, status, request_payload, llm_provider)
            return status
        finally:
            if close_metadata_db and metadata_db is not None:
                metadata_db.close()

    def get_job_status(self, job_id: str) -> BatchJobStatus | None:
        """Get status of batch job."""
        metadata_db, close_metadata_db = self._metadata_db()
        try:
            if metadata_db is not None:
                record = metadata_db.get_batch_job(job_id)
                if record is not None:
                    return self._status_from_record(record)
        finally:
            if close_metadata_db and metadata_db is not None:
                metadata_db.close()
        return self._job_states.get(job_id)

    def list_jobs(self) -> list[BatchJobStatus]:
        """List all batch jobs."""
        metadata_db, close_metadata_db = self._metadata_db()
        try:
            if metadata_db is not None:
                return [
                    self._status_from_record(record)
                    for record in metadata_db.list_batch_jobs()
                ]
        finally:
            if close_metadata_db and metadata_db is not None:
                metadata_db.close()
        return list(self._job_states.values())

    def _metadata_db(self) -> tuple[Any | None, bool]:
        if self.metadata_db is not None:
            return self.metadata_db, False
        if self.metadata_db_factory is not None:
            return self.metadata_db_factory(), True
        return None, False

    @staticmethod
    def _request_hash(
        paper_ids: list[str],
        pdf_paths: dict[str, str],
        llm_provider: str | None,
        llm_overrides: dict[str, Any] | None,
    ) -> str:
        payload = {
            "paper_ids": paper_ids,
            "pdf_paths": pdf_paths,
            "llm_provider": llm_provider,
            "llm_overrides": llm_overrides or {},
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _status_from_record(record: dict[str, Any]) -> BatchJobStatus:
        return BatchJobStatus(
            job_id=str(record["job_id"]),
            status=str(record["status"]),
            papers_total=int(record.get("papers_total") or 0),
            papers_processed=int(record.get("papers_processed") or 0),
            papers_failed=int(record.get("papers_failed") or 0),
            error_message=record.get("error_message"),
            superseded_by=record.get("superseded_by"),
        )

    @staticmethod
    def _persist_job_status(
        metadata_db: Any,
        status: BatchJobStatus,
        request_payload: dict[str, Any],
        llm_provider: str | None,
    ) -> None:
        metadata_db.upsert_batch_job(
            job_id=status.job_id,
            status=status.status,
            papers_total=status.papers_total,
            papers_processed=status.papers_processed,
            papers_failed=status.papers_failed,
            error_message=status.error_message,
            request_payload=request_payload,
            llm_provider=llm_provider,
            superseded_by=status.superseded_by,
        )

    @staticmethod
    def _year_from_extraction(extraction: Any) -> int | None:
        coverage = getattr(extraction, "temporal_coverage", {}) or {}
        value = coverage.get("paper_year") if isinstance(coverage, dict) else None
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _title_from_text(text: str, max_lines: int = 5) -> str:
        return EntityExtractor._paper_title_from_text(text)[:240]
