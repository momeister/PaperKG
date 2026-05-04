from __future__ import annotations

from typing import Any

from extraction.entity_extractor import EntityExtractor, ExtractionResult
from query.llm_router import LLMRouter


class ConceptLinkageStrategy:
    """Base strategy for linking extracted concepts to knowledge base."""

    def link(self, concept: dict[str, str]) -> dict[str, Any] | None:
        """Link concept to knowledge base. Returns enriched concept or None."""
        raise NotImplementedError


class OpenAlexLinkageStrategy(ConceptLinkageStrategy):
    """
    Links extracted concepts to OpenAlex Concept IDs using similarity.
    Can be extended to query OpenAlex API or use local embeddings.
    """

    def __init__(self, concept_cache: dict[str, dict] | None = None) -> None:
        """
        Initialize with optional concept cache.

        Args:
            concept_cache: Pre-populated dict of {concept_label -> openalx_concept_data}
        """
        self.cache = concept_cache or {}

    def link(self, concept: dict[str, str]) -> dict[str, Any] | None:
        """
        Link concept to OpenAlex Concept ID.

        Args:
            concept: Dict with 'label', 'context', 'confidence' keys

        Returns:
            Enriched dict with 'openalx_id', 'openalx_label' or None if not found
        """
        label = concept.get("label", "").lower()

        if label in self.cache:
            cached = self.cache[label]
            return {
                **concept,
                "openalx_id": cached.get("id"),
                "openalx_label": cached.get("display_name"),
            }

        # In production: query OpenAlex API or embedding similarity
        return None


class EntityLinker:
    """
    Links extracted entities to external knowledge bases (OpenAlex, Wikidata, etc).
    Enriches extraction results with authoritative IDs and metadata.
    """

    def __init__(
        self, strategy: ConceptLinkageStrategy | None = None
    ) -> None:
        """
        Initialize linker with optional linkage strategy.

        Args:
            strategy: ConceptLinkageStrategy for concept linking (uses OpenAlex by default)
        """
        self.strategy = strategy or OpenAlexLinkageStrategy()

    def enrich_extraction(
        self, extraction: ExtractionResult
    ) -> ExtractionResult:
        """
        Enrich extraction result with external knowledge base links.

        Args:
            extraction: ExtractionResult from EntityExtractor

        Returns:
            New ExtractionResult with enriched concepts containing IDs
        """
        enriched_concepts = []

        for concept in extraction.concepts:
            linked = self.strategy.link(concept)
            if linked:
                enriched_concepts.append(linked)
            else:
                enriched_concepts.append(concept)

        return ExtractionResult(
            paper_id=extraction.paper_id,
            concepts=enriched_concepts,
            methods=extraction.methods,
            claims=extraction.claims,
            cross_domain_hints=extraction.cross_domain_hints,
            language_detected=extraction.language_detected,
            raw_response=extraction.raw_response,
        )


class ExtractionPipeline:
    """
    End-to-end pipeline: parse -> extract -> link entities.
    Supports configurable LLM providers for extraction.
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        linker: EntityLinker | None = None,
    ) -> None:
        """
        Initialize pipeline.

        Args:
            llm_router: Configured LLMRouter for extraction
            linker: Optional EntityLinker for knowledge base enrichment
        """
        self.extractor = EntityExtractor(llm_router)
        self.linker = linker or EntityLinker()

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

        # Link to knowledge bases if requested and extraction successful
        if link_concepts and not extraction.raw_response:
            extraction = self.linker.enrich_extraction(extraction)

        return extraction
