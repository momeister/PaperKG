import pytest
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Any, Protocol

from extraction.entity_extractor import EntityExtractor, ExtractionResult
from extraction.entity_linker import (
    EntityLinker,
    OpenAlexLinkageStrategy,
    ExtractionPipeline,
)
from extraction.vocabulary import VocabularyManager
from extraction.embedding_engine import EmbeddingEngine
from extraction.conflict_detector import ConflictDetector
from extraction.batch_processor import BatchProcessor
from parsing.parser_router import ParserRouter, ParserType, ParserCharacteristics
from parsing.marker_parser import MarkerParser
from query.llm_router import LLMRouter, ProviderConfig, GenerationSettings


class FakeLLMRouter:
    """Mock LLMRouter for testing."""

    def __init__(self, response_json: dict[str, Any] | None = None):
        self.response_json = response_json or {
            "concepts": [{"label": "test", "context": "test context", "confidence": 0.9}],
            "methods": [],
            "claims": [],
            "cross_domain_hints": [],
            "language_detected": "en",
        }
        self.last_messages = None
        self.last_provider = None
        self.last_overrides = None

    def chat_json(self, messages, provider=None, overrides=None):
        self.last_messages = messages
        self.last_provider = provider
        self.last_overrides = overrides
        return self.response_json

    def chat(self, messages, provider=None, overrides=None):
        return {"content": "response"}


class TestLLMRouter:
    """Test LLM router configuration helpers."""

    def test_from_config_file_parses_model_lists(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
llm:
  default_provider: ollama
  providers:
    ollama:
      provider_type: ollama
      base_url: http://localhost:11434
      model: qwen3.6-35b
      models:
        - qwen3.6-35b
        - llama3.1:8b
""".strip(),
            encoding="utf-8",
        )

        router = LLMRouter.from_config_file(config_path)

        assert router.provider_model_options("ollama") == ["qwen3.6-35b", "llama3.1:8b"]
        assert router.provider_default_model("ollama") == "qwen3.6-35b"

    def test_discover_provider_models_from_ollama(self):
        response = MagicMock()
        response.json.return_value = {"models": [{"name": "qwen3.6-35b"}, {"name": "llama3.1:8b"}]}
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.get.return_value = response

        router = LLMRouter(
            providers={
                "ollama": ProviderConfig(
                    provider_type="ollama",
                    base_url="http://localhost:11434",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="ollama",
            client=client,
        )

        assert router.discover_provider_models("ollama") == ["qwen3.6-35b", "llama3.1:8b"]

    def test_discover_provider_models_from_openai_compatible(self):
        response = MagicMock()
        response.json.return_value = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4.1-mini"}]}
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.get.return_value = response

        router = LLMRouter(
            providers={
                "lm_studio": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="http://localhost:1234/v1",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="lm_studio",
            client=client,
        )

        assert router.discover_provider_models("lm_studio") == ["gpt-4o", "gpt-4.1-mini"]

    def test_merged_settings_keeps_none_repeat_penalty(self):
        base = GenerationSettings(model="gpt-4o", repeat_penalty=None)

        merged = LLMRouter._merged_settings(base, {"temperature": 0.4})

        assert merged.repeat_penalty is None
        assert merged.temperature == 0.4


class TestEntityExtractor:
    """Test entity extraction with configurable LLM providers."""

    def test_extract_returns_extraction_result(self):
        """Test basic extraction returns properly structured result."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router)

        result = extractor.extract("paper_001", "Sample paper text about neural networks")

        assert result.paper_id == "paper_001"
        assert isinstance(result.concepts, list)
        assert isinstance(result.methods, list)
        assert isinstance(result.claims, list)
        assert result.language_detected == "en"

    def test_extract_with_provider_override(self):
        """Test extraction with specific LLM provider override."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router)

        result = extractor.extract(
            "paper_001", "Sample text", provider="openai"
        )

        assert mock_router.last_provider == "openai"

    def test_extract_with_settings_overrides(self):
        """Test extraction with LLM setting overrides."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router)

        overrides = {"temperature": 0.1, "max_tokens": 1024}
        result = extractor.extract(
            "paper_001", "Sample text", overrides=overrides
        )

        assert mock_router.last_overrides == overrides

    def test_extract_handles_llm_errors(self):
        """Test extraction gracefully handles LLM errors."""
        mock_router = FakeLLMRouter()
        mock_router.chat_json = MagicMock(side_effect=RuntimeError("LLM timeout"))

        extractor = EntityExtractor(mock_router)
        result = extractor.extract("paper_001", "Sample text")

        assert result.paper_id == "paper_001"
        assert "failed" in result.raw_response.lower() or "error" in result.raw_response.lower()
        assert len(result.concepts) == 0

    def test_extract_truncates_long_text(self):
        """Test extraction truncates very long paper text."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router)

        # Create text > 24k chars
        long_text = "x" * 30000

        result = extractor.extract("paper_001", long_text)

        # Check that chat_json was called (implying truncation happened)
        assert mock_router.last_messages is not None
        message_text = mock_router.last_messages[0]["content"]
        assert len(message_text) < len(long_text)


class TestEntityLinker:
    """Test entity linking to knowledge bases."""

    def test_openalx_linkage_strategy_finds_cached_concepts(self):
        """Test OpenAlex strategy matches cached concepts."""
        cache = {
            "neural network": {"id": "C123", "display_name": "Neural Network"}
        }
        strategy = OpenAlexLinkageStrategy(concept_cache=cache)

        result = strategy.link(
            {"label": "neural network", "context": "...", "confidence": 0.9}
        )

        assert result is not None
        assert result["openalx_id"] == "C123"
        assert result["openalx_label"] == "Neural Network"

    def test_openalx_linkage_strategy_returns_none_for_unknown(self):
        """Test strategy returns None for unknown concepts."""
        strategy = OpenAlexLinkageStrategy(concept_cache={})

        result = strategy.link(
            {"label": "unknown_concept", "context": "...", "confidence": 0.9}
        )

        assert result is None

    def test_entity_linker_enriches_extraction(self):
        """Test linker enriches extraction results."""
        cache = {"neural network": {"id": "C123", "display_name": "Neural Network"}}
        strategy = OpenAlexLinkageStrategy(concept_cache=cache)
        linker = EntityLinker(strategy=strategy)

        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {"label": "neural network", "context": "...", "confidence": 0.9}
            ],
        )

        enriched = linker.enrich_extraction(extraction)

        assert enriched.paper_id == extraction.paper_id
        assert len(enriched.concepts) == 1
        assert enriched.concepts[0].get("openalx_id") == "C123"

    def test_extraction_pipeline_extract_and_link(self):
        """Test full extraction pipeline."""
        mock_router = FakeLLMRouter()
        cache = {"neural network": {"id": "C123", "display_name": "Neural Network"}}
        strategy = OpenAlexLinkageStrategy(concept_cache=cache)
        linker = EntityLinker(strategy=strategy)

        pipeline = ExtractionPipeline(mock_router, linker=linker)
        result = pipeline.process(
            "p1",
            "Paper about neural networks",
            link_concepts=True,
        )

        assert result.paper_id == "p1"
        assert mock_router.last_messages is not None


class TestVocabularyManager:
    """Test vocabulary normalization."""

    def test_vocabulary_register_and_normalize(self):
        """Test registering and normalizing vocabulary entries."""
        vocab = VocabularyManager()
        vocab.register(
            "Neural Network",
            aliases=["NN", "neural net"],
            openalx_id="C123",
        )

        assert vocab.normalize("neural network") == "Neural Network"
        assert vocab.normalize("nn") == "Neural Network"
        assert vocab.normalize("neural net") == "Neural Network"

    def test_vocabulary_merge_entries(self):
        """Test merging duplicate vocabulary entries."""
        vocab = VocabularyManager()
        vocab.register("Neural Network", aliases=["NN"])
        vocab.register("Deep Learning", aliases=["DL"])

        assert vocab.merge_entries("Deep Learning", "Neural Network")

        # Source should be gone
        assert vocab.get_entry("Deep Learning") is None

        # Source canonical should now be an alias of target
        result = vocab.normalize("deep learning")
        assert result == "Neural Network"

    def test_vocabulary_import_export(self):
        """Test vocabulary serialization and deserialization."""
        vocab1 = VocabularyManager()
        vocab1.register("Concept A", aliases=["CA"], openalx_id="ID_A")

        data = vocab1.to_dict()
        vocab2 = VocabularyManager.from_dict(data)

        assert vocab2.normalize("concept a") == "Concept A"
        assert vocab2.normalize("ca") == "Concept A"


class TestEmbeddingEngine:
    """Test entity embedding generation."""

    def test_embedding_engine_embed_entity(self):
        """Test single entity embedding."""
        engine = EmbeddingEngine()
        result = engine.embed_entity("neural network")

        assert result.entity_label == "neural network"
        assert len(result.vector) == engine.EMBEDDING_DIM
        assert result.dimension == engine.EMBEDDING_DIM

    def test_embedding_engine_batch_embed(self):
        """Test batch embedding."""
        engine = EmbeddingEngine()
        labels = ["neural network", "deep learning", "transformer"]

        results = engine.embed_batch(labels)

        assert len(results) == 3
        assert all(r.dimension == engine.EMBEDDING_DIM for r in results)

    def test_embedding_engine_similarity(self):
        """Test cosine similarity computation."""
        import numpy as np

        engine = EmbeddingEngine()

        vec1 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        vec2 = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        vec3 = np.array([0.0, 1.0, 0.0], dtype=np.float32)

        # Same vectors should have similarity 1.0
        assert engine.similarity(vec1, vec2) == pytest.approx(1.0)

        # Orthogonal vectors should have similarity 0.0
        assert engine.similarity(vec1, vec3) == pytest.approx(0.0)

    def test_embedding_engine_find_similar(self):
        """Test finding similar entities by embedding."""
        import numpy as np

        engine = EmbeddingEngine()

        query_vec = np.array([1.0, 0.0, 0.0], dtype=np.float32)
        entity_vecs = {
            "similar": np.array([1.0, 0.0, 0.0], dtype=np.float32),
            "dissimilar": np.array([0.0, 1.0, 0.0], dtype=np.float32),
        }

        results = engine.find_similar_entities(query_vec, entity_vecs, threshold=0.5)

        assert len(results) == 1
        assert results[0][0] == "similar"


class TestConflictDetector:
    """Test claim conflict detection."""

    def test_conflict_detector_analyze_pair(self):
        """Test analyzing pair of claims."""
        mock_router = FakeLLMRouter(
            response_json={
                "conflict_type": "contradictory",
                "confidence": 0.95,
                "reasoning": "Claims directly contradict",
                "resolution": "Claim 1 is more likely",
            }
        )

        detector = ConflictDetector(mock_router)
        analysis = detector.analyze_claim_pair(
            "Climate change is accelerating",
            "Climate change is slowing",
        )

        assert analysis.conflict_type == "contradictory"
        assert analysis.confidence == 0.95

    def test_conflict_detector_batch_analysis(self):
        """Test analyzing batch of claims."""
        mock_router = FakeLLMRouter(
            response_json={"conflict_type": "irrelevant", "confidence": 0.1}
        )

        detector = ConflictDetector(mock_router)
        claims = ["Claim A", "Claim B", "Claim C"]

        analyses = detector.analyze_claims_batch(claims)

        # Should analyze all pairs: (A,B), (A,C), (B,C)
        assert len(analyses) == 3

    def test_conflict_detector_find_contradictions(self):
        """Test filtering high-confidence contradictions."""
        from extraction.conflict_detector import ConflictAnalysis

        analyses = [
            ConflictAnalysis(
                claim_pair=("A", "B"),
                conflict_type="contradictory",
                confidence=0.95,
                reasoning="High confidence",
            ),
            ConflictAnalysis(
                claim_pair=("C", "D"),
                conflict_type="contradictory",
                confidence=0.5,
                reasoning="Low confidence",
            ),
            ConflictAnalysis(
                claim_pair=("E", "F"),
                conflict_type="supporting",
                confidence=0.9,
                reasoning="Not a contradiction",
            ),
        ]

        detector = ConflictDetector(FakeLLMRouter())
        contradictions = detector.find_contradictions(analyses, confidence_threshold=0.7)

        assert len(contradictions) == 1
        assert contradictions[0].claim_pair == ("A", "B")


class TestParserRouter:
    """Test intelligent parser selection."""

    def test_parser_characteristics_detect_formulas(self):
        """Test formula detection."""
        text_with_formulas = "The equation $$E=mc^2$$ shows energy equivalence. Also \\alpha and \\beta parameters."
        text_without = "Neural networks are good. They work well."

        assert ParserCharacteristics.has_heavy_formulas(text_with_formulas)
        assert not ParserCharacteristics.has_heavy_formulas(text_without)

    def test_parser_characteristics_detect_tables(self):
        """Test table detection."""
        text_with_tables = "Results:\nKey | Value | Count\n---|---|---\nA | 1 | 100\nB | 2 | 200"
        text_without = "The results show that the method works well."

        assert ParserCharacteristics.has_complex_tables(text_with_tables)
        assert not ParserCharacteristics.has_complex_tables(text_without)

    def test_parser_characteristics_detect_diagrams(self):
        """Test diagram detection."""
        text_with_diagrams = "Figure 1 shows the architecture. Figure 2 displays the workflow. Figure 3 presents the diagram."
        text_without = "The results indicate improvement."

        assert ParserCharacteristics.has_diagrams(text_with_diagrams)
        assert not ParserCharacteristics.has_diagrams(text_without)

    def test_parser_router_select_parser(self):
        """Test parser selection based on characteristics."""
        router = ParserRouter()

        # Mock parsers
        mock_marker = MagicMock()
        mock_nougat = MagicMock()

        router.register_parser(ParserType.MARKER, mock_marker)
        router.register_parser(ParserType.NOUGAT, mock_nougat)

        # Formula-heavy text should select Nougat
        preview = "The equation $$E=mc^2$$ is fundamental."
        selected = router.select_parser("/fake.pdf", preview)

        assert selected == ParserType.NOUGAT

    def test_parser_router_fallback_to_marker(self):
        """Test fallback to Marker when Nougat is not available."""
        router = ParserRouter()

        mock_marker = MagicMock()
        router.register_parser(ParserType.MARKER, mock_marker)
        router.available_parsers[ParserType.NOUGAT] = None

        # Formula-heavy text should still fall back to Marker when Nougat is unavailable
        preview = "The equation $$E=mc^2$$ is fundamental."
        selected = router.select_parser("/fake.pdf", preview)

        assert selected == ParserType.MARKER


class TestParserImplementations:
    """Test actual parser fallbacks."""

    def test_marker_parser_reads_pdf_text_or_falls_back(self, tmp_path):
        from pypdf import PdfWriter

        pdf_path = tmp_path / "blank.pdf"
        writer = PdfWriter()
        writer.add_blank_page(width=72, height=72)
        with pdf_path.open("wb") as handle:
            writer.write(handle)

        parser = MarkerParser()
        result = parser.parse(pdf_path, "paper_001")

        assert result.paper_id == "paper_001"
        assert result.page_count == 1
        assert result.meta.get("extraction_method") == "pypdf"

    def test_nougat_parser_returns_real_fallback_text(self, tmp_path):
        pdf_path = tmp_path / "input.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")

        from parsing.nougat_parser import NougatParser

        parser = NougatParser()
        result = parser.parse(str(pdf_path), "paper_002")

        assert result.paper_id == "paper_002"
        assert "not yet implemented" not in result.text.lower()
        assert result.metadata.get("status") in {"fallback", "remote"}


class TestBatchProcessor:
    """Test batch processing of papers."""

    def test_batch_processor_initialization(self):
        """Test batch processor can be initialized."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()

        processor = BatchProcessor(mock_llm, parser_router)

        assert processor.llm_router == mock_llm
        assert processor.parser_router == parser_router

    def test_batch_processor_get_job_status(self):
        """Test retrieving batch job status."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()

        processor = BatchProcessor(mock_llm, parser_router)

        # Process with empty list should return status
        status = processor.process_papers([], {}, job_id="test_job")

        assert status.job_id == "test_job"
        assert status.papers_total == 0
        assert status.status == "completed"

        # Should be retrievable
        retrieved = processor.get_job_status("test_job")
        assert retrieved is not None
        assert retrieved.job_id == "test_job"


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
