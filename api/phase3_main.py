"""
Phase 3 API: PDF Parsing, Entity Extraction, and LLM-based Knowledge Discovery

Endpoints:
- POST /extraction/extract - Extract entities from paper text
- GET /extraction/providers - List available LLM providers
- POST /extraction/batch - Batch process papers
- GET /extraction/batch/{job_id} - Get batch job status
- GET /health - Health check
"""

from __future__ import annotations

from typing import Any

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from extraction.entity_extractor import EntityExtractor
from extraction.entity_linker import ExtractionPipeline
from extraction.embedding_engine import EmbeddingEngine
from extraction.batch_processor import BatchProcessor
from parsing.parser_router import ParserRouter
from query.llm_router import LLMRouter
from storage.metadata_db import MetadataDB

app = FastAPI(
    title="ScienceKG Phase 3 API",
    description="PDF Parsing, Entity Extraction, and LLM-based Knowledge Discovery",
    version="3.0.0",
)


# Initialize core components
llm_router = LLMRouter.from_config_file("config.yaml")
parser_router = ParserRouter()
embedding_engine = EmbeddingEngine()
extraction_pipeline = ExtractionPipeline(llm_router, embedding_engine=embedding_engine)
batch_processor = BatchProcessor(
    llm_router,
    parser_router,
    embedding_engine,
    metadata_db_factory=lambda: MetadataDB("data/metadata.duckdb"),
)


# Request/Response models
class ExtractionRequest(BaseModel):
    """Request for entity extraction."""

    paper_id: str
    text: str
    provider: str | None = None
    temperature: float | None = None
    max_tokens: int | None = None
    top_p: float | None = None
    context_size: int | None = None
    extraction_mode: str | None = None


class ConceptItem(BaseModel):
    """Extracted concept."""

    label: str
    context: str
    confidence: float
    openalx_id: str | None = None


class ExtractionResponse(BaseModel):
    """Response from entity extraction."""

    paper_id: str
    paper_type: str
    concepts: list[ConceptItem]
    methods: list[dict[str, Any]]
    concept_candidates: list[dict[str, Any]] = []
    method_candidates: list[dict[str, Any]] = []
    relations: list[dict[str, Any]] = []
    claims: list[dict[str, Any]]
    cross_domain_hints: list[dict[str, Any]]
    terminology_conflicts: list[dict[str, Any]]
    temporal_coverage: dict[str, Any]
    mathematical_content: dict[str, Any]
    language_detected: str
    quality_warnings: list[str] = []
    candidate_count: int = 0
    extraction_diagnostics: dict[str, Any] = {}


class BatchJobResponse(BaseModel):
    """Response with batch job info."""

    job_id: str
    status: str
    papers_total: int
    papers_processed: int
    papers_failed: int
    error_message: str | None = None
    superseded_by: str | None = None


class BatchJobRequest(BaseModel):
    """Request for resumable batch extraction."""

    paper_ids: list[str]
    pdf_paths: dict[str, str]
    provider: str | None = None
    llm_overrides: dict[str, Any] | None = None
    job_id: str | None = None
    resume: bool = True
    supersede_job_id: str | None = None


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    phase: str
    default_provider: str
    available_providers: list[str]


@app.get("/health", response_model=None)
async def health_check() -> JSONResponse:
    """Health check endpoint."""
    payload = HealthResponse(
        status="ok",
        phase="3",
        default_provider=str(llm_router.default_provider),
        available_providers=llm_router.available_providers(),
    )
    return JSONResponse(content=payload.model_dump())


@app.get("/extraction/providers")
async def list_providers() -> dict[str, list[str]]:
    """List available LLM providers."""
    return {"providers": llm_router.available_providers()}


@app.post("/extraction/extract")
async def extract_entities(request: ExtractionRequest) -> ExtractionResponse:
    """
    Extract entities (concepts, methods, claims) from paper text.

    Supports configurable LLM providers for flexible model switching.
    """
    try:
        overrides = {}

        if request.temperature is not None:
            overrides["temperature"] = request.temperature

        if request.max_tokens is not None:
            overrides["max_tokens"] = request.max_tokens

        if request.top_p is not None:
            overrides["top_p"] = request.top_p

        if request.context_size is not None:
            overrides["context_size"] = request.context_size

        if request.extraction_mode is not None:
            overrides["extraction_mode"] = request.extraction_mode

        # Extract entities
        result = extraction_pipeline.process(
            request.paper_id,
            request.text,
            provider=request.provider,
            overrides=overrides if overrides else None,
            link_concepts=True,
        )

        # Convert to response model
        concepts = [
            ConceptItem(
                label=c.get("label", ""),
                context=c.get("context", ""),
                confidence=c.get("confidence", 0.0),
                openalx_id=c.get("openalx_id"),
            )
            for c in result.concepts
        ]

        return ExtractionResponse(
            paper_id=result.paper_id,
            paper_type=result.paper_type,
            concepts=concepts,
            methods=result.methods,
            concept_candidates=result.concept_candidates,
            method_candidates=result.method_candidates,
            relations=result.relations,
            claims=result.claims,
            cross_domain_hints=result.cross_domain_hints,
            terminology_conflicts=result.terminology_conflicts,
            temporal_coverage=result.temporal_coverage,
            mathematical_content=result.mathematical_content,
            language_detected=result.language_detected,
            quality_warnings=result.quality_warnings,
            candidate_count=result.candidate_count,
            extraction_diagnostics=result.extraction_diagnostics,
        )

    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Extraction failed: {str(exc)}")


@app.post("/extraction/batch")
async def start_batch_job(
    request: BatchJobRequest,
) -> BatchJobResponse:
    """
    Start batch extraction job for multiple papers.

    Returns job_id for status tracking.
    """
    try:
        status = batch_processor.process_papers(
            request.paper_ids,
            request.pdf_paths,
            job_id=request.job_id,
            llm_provider=request.provider,
            llm_overrides=request.llm_overrides,
            resume=request.resume,
            supersede_job_id=request.supersede_job_id,
        )

        return BatchJobResponse(
            job_id=status.job_id,
            status=status.status,
            papers_total=status.papers_total,
            papers_processed=status.papers_processed,
            papers_failed=status.papers_failed,
            error_message=status.error_message,
            superseded_by=status.superseded_by,
        )

    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Batch job failed: {str(exc)}")


@app.get("/extraction/batch/{job_id}")
async def get_batch_status(job_id: str) -> BatchJobResponse:
    """Get status of batch extraction job."""
    status = batch_processor.get_job_status(job_id)

    if status is None:
        raise HTTPException(status_code=404, detail=f"Job {job_id} not found")

    return BatchJobResponse(
        job_id=status.job_id,
        status=status.status,
        papers_total=status.papers_total,
        papers_processed=status.papers_processed,
        papers_failed=status.papers_failed,
        error_message=status.error_message,
        superseded_by=status.superseded_by,
    )


@app.get("/extraction/jobs")
async def list_batch_jobs() -> dict[str, list[BatchJobResponse]]:
    """List all batch extraction jobs."""
    jobs = batch_processor.list_jobs()

    return {
        "jobs": [
            BatchJobResponse(
                job_id=j.job_id,
                status=j.status,
                papers_total=j.papers_total,
                papers_processed=j.papers_processed,
                papers_failed=j.papers_failed,
                error_message=j.error_message,
                superseded_by=j.superseded_by,
            )
            for j in jobs
        ]
    }


@app.get("/extraction/batch/{job_id}/items")
async def get_batch_items(job_id: str) -> dict[str, list[dict[str, Any]]]:
    """Get durable per-paper state for a batch extraction job."""
    with MetadataDB("data/metadata.duckdb") as db:
        if db.get_batch_job(job_id) is None:
            raise HTTPException(status_code=404, detail=f"Job {job_id} not found")
        return {"items": db.get_batch_job_items(job_id)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=8000)
