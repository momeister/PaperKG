from __future__ import annotations

from dataclasses import dataclass
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

            try:
                # Parse paper
                forced_parser = parser_selection.get(paper_id) if parser_selection else None
                parsed = self.parser_router.parse(pdf_path, paper_id, force_parser=forced_parser)

                # Extract entities
                extraction = self.extraction_pipeline.process(
                    paper_id,
                    parsed.text,
                    provider=llm_provider,
                    overrides=llm_overrides,
                    link_concepts=True,
                )

                # Generate embeddings for concepts
                concept_labels = [c.get("label", "") for c in extraction.concepts]
                embeddings = self.embedding_engine.embed_batch(concept_labels)

                # In production: store to database
                # await store_extraction_result(paper_id, extraction, embeddings)

                status.papers_processed += 1

            except Exception as exc:
                status.papers_failed += 1
                if status.error_message is None:
                    status.error_message = str(exc)

        status.status = "completed"
        return status

    def get_job_status(self, job_id: str) -> BatchJobStatus | None:
        """Get status of batch job."""
        return self._job_states.get(job_id)

    def list_jobs(self) -> list[BatchJobStatus]:
        """List all batch jobs."""
        return list(self._job_states.values())
