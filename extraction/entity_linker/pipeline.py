"""ExtractionPipeline: parse -> extract -> link, end to end."""
from __future__ import annotations

from typing import Any

from extraction.embedding_engine import EmbeddingEngine
from extraction.ontology import CanonicalResolver, Ontology
from extraction.entity_extractor import (
    EntityExtractor,
    ExtractionResult,
    extraction_failure_reason,
)
from query.llm_router import LLMRouter
from extraction.entity_linker.linker import EntityLinker


class ExtractionPipeline:
    """
    End-to-end pipeline: parse -> extract -> link entities.
    Supports configurable LLM providers for extraction.
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        linker: EntityLinker | None = None,
        ontology: Ontology | None = None,
        embedding_engine: EmbeddingEngine | None = None,
        quality_db_path: str | None = None,
    ) -> None:
        """
        Initialize pipeline.

        Args:
            llm_router: Configured LLMRouter for extraction
            linker: Optional EntityLinker for knowledge base enrichment
        """
        self.extractor = EntityExtractor(llm_router, quality_db_path=quality_db_path)
        if linker is not None:
            self.linker = linker
        else:
            resolver = CanonicalResolver(ontology=ontology, embedding_engine=embedding_engine)
            self.linker = EntityLinker(resolver=resolver)

    def process(
        self,
        paper_id: str,
        paper_text: str,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
        link_concepts: bool = True,
    ) -> ExtractionResult:
        """
        Process paper: extract entities and optionally link to knowledge bases.

        Args:
            paper_id: Unique paper identifier
            paper_text: Full paper text
            provider: Optional LLM provider override
            overrides: Optional LLM settings overrides
            link_concepts: Whether to enrich with knowledge base links

        Returns:
            ExtractionResult with extracted and optionally linked entities
        """
        # Extract entities using LLM
        extraction = self.extractor.extract(
            paper_id, paper_text, provider=provider, overrides=overrides
        )

        extraction_failed = (
            extraction.raw_response.lower().startswith("extraction failed:")
            or extraction_failure_reason(extraction) is not None
        )

        # Link to knowledge bases if requested and extraction produced concepts.
        # Successful extractions keep raw_response for debugging, so raw_response
        # itself is not an error signal.
        if link_concepts and not extraction_failed:
            extraction = self.linker.enrich_extraction(extraction)

        return extraction
