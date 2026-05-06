from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

from extraction.entity_linker import ExtractionPipeline
from extraction.embedding_engine import EmbeddingEngine
from parsing.parser_router import ParserRouter, ParserType
from query.llm_router import LLMRouter


@dataclass
class BatchJobStatus:
    """Status of batch processing job."""

    job_id: str
    status: str  # "pending", "processing", "completed", "failed"
    papers_total: int
    papers_processed: int
    papers_failed: int
    error_message: str | None = None


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
        self.extraction_pipeline = ExtractionPipeline(llm_router)
        self._job_states: dict[str, BatchJobStatus] = {}

    def process_papers(
        self,
        paper_ids: list[str],
        pdf_paths: dict[str, str],
        job_id: str | None = None,
        llm_provider: str | None = None,
        llm_overrides: dict[str, Any] | None = None,
        parser_selection: dict[str, ParserType] | None = None,
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

        for paper_id in paper_ids:
            pdf_path = pdf_paths.get(paper_id)
            if not pdf_path:
                status.papers_failed += 1
                continue

            last_error: Exception | None = None
            for attempt in range(self.max_retries + 1):
                try:
                    forced_parser = parser_selection.get(paper_id) if parser_selection else None
                    parsed = self.parser_router.parse(pdf_path, paper_id, force_parser=forced_parser)

                    extraction = self.extraction_pipeline.process(
                        paper_id,
                        parsed.text,
                        provider=llm_provider,
                        overrides=llm_overrides,
                        link_concepts=self.link_concepts,
                    )

                    if self.embed_concepts:
                        concept_labels = [c.get("label", "") for c in extraction.concepts]
                        self.embedding_engine.embed_batch(concept_labels)

                    if self.metadata_db is not None or self.metadata_db_factory is not None:
                        if self.metadata_db is not None:
                            metadata_db = self.metadata_db
                            close_metadata_db = False
                        else:
                            metadata_db = self.metadata_db_factory()
                            close_metadata_db = True
                        try:
                            metadata_db.save_extraction_result(
                                paper_id=paper_id,
                                llm_provider=llm_provider or "default",
                                llm_model=getattr(
                                    self.llm_router.provider_settings(llm_provider),
                                    "model",
                                    "unknown",
                                ) if hasattr(self.llm_router, "provider_settings") else "unknown",
                                concepts=extraction.concepts,
                                methods=extraction.methods,
                                claims=extraction.claims,
                                cross_domain_hints=extraction.cross_domain_hints,
                                raw_response=extraction.raw_response,
                            )
                        finally:
                            if close_metadata_db:
                                metadata_db.close()

                    status.papers_processed += 1
                    last_error = None
                    break
                except Exception as exc:
                    last_error = exc
                    if attempt < self.max_retries and self.retry_delay_seconds:
                        time.sleep(self.retry_delay_seconds)

            if last_error is not None:
                status.papers_failed += 1
                if status.error_message is None:
                    status.error_message = str(last_error)

        status.status = "completed"
        return status

    def get_job_status(self, job_id: str) -> BatchJobStatus | None:
        """Get status of batch job."""
        return self._job_states.get(job_id)

    def list_jobs(self) -> list[BatchJobStatus]:
        """List all batch jobs."""
        return list(self._job_states.values())
