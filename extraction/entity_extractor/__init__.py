"""LLM-basierte Entitaets-/Claim-Extraktion (Paket, vormals eine 4200-Zeilen-Datei).

Oeffentliche Oberflaeche unveraendert: EntityExtractor, ExtractionResult,
ParsedLLMResponse, CLAIMS_EXTRACTION_PROMPT, extraction_failure_reason,
filter_concepts, safe_llm_extract, deduplicate_methods, enrich_method_domains.
extraction_failure_reason bleibt Modul-Attribut (String-Pfad-Monkeypatch in Tests).
"""
from __future__ import annotations

from query.llm_router import LLMRouter  # noqa: F401  # Typ der Konstruktor-Signatur

from extraction.entity_extractor._shared import (  # noqa: F401  # oeffentliche Modul-Oberflaeche
    CLAIMS_EXTRACTION_PROMPT,
    DEFAULT_CONCEPT_BLOCKLIST,
    DEFAULT_DOMAIN_KEYWORD_MAP,
    DeterministicScanResult,
    ExtractionResult,
    ParsedLLMResponse,
    RegexValidationResult,
    _extract_first_json_value,
    _parse_llm_json_value,
    _strip_markdown_fences,
    deduplicate_methods,
    enrich_method_domains,
    extraction_failure_reason,
    filter_concepts,
    safe_llm_extract,
)
from extraction.entity_extractor._text_prep import TextPrepMixin
from extraction.entity_extractor._orchestration import OrchestrationMixin
from extraction.entity_extractor._llm_calls import LlmCallsMixin
from extraction.entity_extractor._json_parse import JsonParseMixin
from extraction.entity_extractor._claims import ClaimsMixin
from extraction.entity_extractor._concepts import ConceptsMixin
from extraction.entity_extractor._candidates import CandidatesMixin
from extraction.entity_extractor._paper_meta import PaperMetaMixin
from extraction.entity_extractor._quality import QualityMixin
from extraction.entity_extractor._scan import ScanMixin
from extraction.entity_extractor._prompts import PromptsMixin


class EntityExtractor(
    TextPrepMixin,
    OrchestrationMixin,
    LlmCallsMixin,
    JsonParseMixin,
    ClaimsMixin,
    ConceptsMixin,
    CandidatesMixin,
    PaperMetaMixin,
    QualityMixin,
    ScanMixin,
    PromptsMixin,
):
    """Extracts entities and claims from paper text (komponiert aus Domain-Mixins)."""

    def __init__(
        self,
        llm_router: LLMRouter,
        quality_db_path: str | None = None,
    ) -> None:
        """
        Initialize extractor with an LLM router and optional quality database.

        Args:
            llm_router: Configured LLMRouter instance for model calls.
            quality_db_path: DuckDB path for extraction_quality telemetry. Set
                to None to disable quality writes, for example in isolated tests.
        """
        self.llm = llm_router
        self.quality_db_path = quality_db_path
