import pytest
import json
import httpx
from unittest.mock import MagicMock, patch
from dataclasses import dataclass
from typing import Any, Protocol

from extraction.entity_extractor import (
    CLAIMS_EXTRACTION_PROMPT,
    EntityExtractor,
    ExtractionResult,
    deduplicate_methods,
    enrich_method_domains,
    filter_concepts,
    safe_llm_extract,
)
from extraction.entity_linker import (
    EntityLinker,
    OpenAlexLinkageStrategy,
    ExtractionPipeline,
)
from extraction.vocabulary import VocabularyManager
from extraction.embedding_engine import EmbeddingEngine
from extraction.ontology import CanonicalResolver, Ontology
from extraction.text_normalization import normalize_key, slugify_label
from extraction.conflict_detector import ConflictDetector
from extraction.batch_processor import BatchProcessor
from parsing.parser_router import ParserRouter, ParserType, ParserCharacteristics
from parsing.marker_parser import MarkerParser
from query.llm_router import LLMRouter, ProviderConfig, GenerationSettings


class FakeLLMRouter:
    """Mock LLMRouter for testing."""

    def __init__(self, response_json: dict[str, Any] | None = None):
        self.response_json = response_json or {
            "paper_type": "research",
            "concepts": [{"label": "test", "context": "test context", "confidence": 0.9}],
            "methods": [],
            "claims": [],
            "cross_domain_hints": [],
            "terminology_conflicts": [],
            "temporal_coverage": {"paper_year": None, "reviewed_period": None},
            "mathematical_content": {"has_formulas": False, "formula_types": []},
            "language_detected": "en",
        }
        self.last_messages = None
        self.last_provider = None
        self.last_overrides = None
        self.chat_json_calls = 0

    def chat_json(self, messages, provider=None, overrides=None):
        self.chat_json_calls += 1
        self.last_messages = messages
        self.last_provider = provider
        self.last_overrides = overrides
        return self.response_json

    def chat(self, messages, provider=None, overrides=None):
        self.last_messages = messages
        self.last_provider = provider
        self.last_overrides = overrides
        return json.dumps(self.response_json)


class SequenceLLMRouter:
    """Mock router that returns one raw chat response per call."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.last_response_metadata = {}

    def chat(self, messages, provider=None, overrides=None):
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        if not self.responses:
            raise AssertionError("No fake LLM responses left")
        return self.responses.pop(0)


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

    def test_recommended_settings_reads_ollama_parameters(self):
        tags_response = MagicMock()
        tags_response.json.return_value = {"models": [{"name": "qwen3.6-35b"}]}
        tags_response.raise_for_status.return_value = None
        show_response = MagicMock()
        show_response.json.return_value = {
            "parameters": "temperature 0.1\ntop_p 0.8\nnum_ctx 65536\nnum_predict 4096\nrepeat_penalty 1.1"
        }
        show_response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = show_response

        router = LLMRouter(
            providers={
                "ollama": ProviderConfig(
                    provider_type="ollama",
                    base_url="http://localhost:11434",
                    settings=GenerationSettings(model="qwen3.6-35b", max_tokens=2048),
                )
            },
            default_provider="ollama",
            client=client,
        )

        settings = router.recommended_settings("ollama", "qwen3.6-35b")

        assert settings.temperature == 0.1
        assert settings.top_p == 0.8
        assert settings.context_size == 65536
        assert settings.max_tokens == 16384
        assert settings.repeat_penalty == 1.1

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

    def test_ollama_json_mode_uses_top_level_format(self):
        response = MagicMock()
        response.json.return_value = {
            "message": {"content": '{"ok": true}'},
            "done_reason": "stop",
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
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

        text = router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={
                "extra": {
                    "json_mode": True,
                    "format": "json",
                    "response_format": {"type": "json_object"},
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            },
        )

        payload = client.post.call_args.kwargs["json"]
        assert text == '{"ok": true}'
        assert payload["format"] == "json"
        assert "response_format" not in payload["options"]
        assert "chat_template_kwargs" not in payload["options"]
        assert router.last_response_metadata["done_reason"] == "stop"

    def test_lm_studio_json_mode_does_not_force_response_format(self):
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "lm": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="http://localhost:1234/v1",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="lm",
            client=client,
        )

        text = router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={"extra": {"json_mode": True, "format": "json"}},
        )

        payload = client.post.call_args.kwargs["json"]
        assert text == '{"ok": true}'
        assert "response_format" not in payload
        assert "format" not in payload["extra_body"]
        assert router.last_response_metadata["finish_reason"] == "stop"

    def test_lm_studio_response_format_falls_back_when_server_rejects_it(self):
        request = httpx.Request("POST", "http://localhost:1234/v1/chat/completions")
        rejected_response = httpx.Response(400, request=request, json={"error": "unsupported"})
        accepted = MagicMock()
        accepted.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        accepted.raise_for_status.return_value = None
        client = MagicMock()
        client.post.side_effect = [
            rejected_response,
            accepted,
        ]
        router = LLMRouter(
            providers={
                "lm": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="http://localhost:1234/v1",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="lm",
            client=client,
        )

        text = router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={"extra": {"json_mode": True, "force_response_format": True}},
        )

        first_payload = client.post.call_args_list[0].kwargs["json"]
        second_payload = client.post.call_args_list[1].kwargs["json"]
        assert first_payload["response_format"] == {"type": "json_object"}
        assert "response_format" not in second_payload
        assert text == '{"ok": true}'
        assert router.last_response_metadata["response_format_fallback"] is True

    def test_openai_endpoint_json_mode_uses_response_format(self):
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "openai": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    settings=GenerationSettings(model="gpt-4o"),
                )
            },
            default_provider="openai",
            client=client,
        )

        router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={"extra": {"json_mode": True}},
        )

        payload = client.post.call_args.kwargs["json"]
        assert payload["response_format"] == {"type": "json_object"}


class TestEntityExtractor:
    """Test entity extraction with configurable LLM providers."""

    def test_extract_returns_extraction_result(self):
        """Test basic extraction returns properly structured result."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract("paper_001", "Sample paper text about neural networks")

        assert result.paper_id == "paper_001"
        assert isinstance(result.concepts, list)
        assert isinstance(result.methods, list)
        assert isinstance(result.claims, list)
        assert result.paper_type == "research"
        assert result.language_detected == "en"

    def test_extract_preserves_extended_scientific_metadata(self):
        """Test extraction keeps paper type, attribution, and formula metadata."""
        mock_router = FakeLLMRouter(
            response_json={
                "paper_type": "survey",
                "concepts": [
                    {"label": "Q-learning", "context": "reviewed RL method", "confidence": 0.82}
                ],
                "methods": [
                    {
                        "label": "Emotion-Modulated Q-learning Taxonomy",
                        "domain": "reinforcement learning",
                        "description": "Organizes emotion signals used in RL updates.",
                        "source_type": "paper_contribution",
                    }
                ],
                "claims": [
                    {
                        "statement": "Emotion-RL studies lack reproduced experimental scenarios.",
                        "evidence_type": "review",
                        "negated": False,
                        "attributed_to": "this_paper",
                    }
                ],
                "cross_domain_hints": [
                    {
                        "field": "Developmental Robotics",
                        "why_applicable": "Reward shaping transfers to energy management.",
                    }
                ],
                "terminology_conflicts": [
                    {
                        "term": "valence",
                        "this_field": "emotion polarity",
                        "other_field": "chemistry - electron affinity",
                    }
                ],
                "temporal_coverage": {"paper_year": 2024, "reviewed_period": "2007-2023"},
                "mathematical_content": {
                    "has_formulas": True,
                    "formula_types": ["reward_function", "value_function"],
                },
                "language_detected": "en",
            }
        )
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract("paper_001", "Survey text")

        assert result.paper_type == "survey"
        assert result.methods[0]["source_type"] == "paper_contribution"
        assert result.claims[0]["attributed_to"] == "this_paper"
        assert result.terminology_conflicts[0]["term"] == "valence"
        assert result.temporal_coverage["reviewed_period"] == "2007-2023"
        assert result.mathematical_content["formula_types"] == ["reward_function", "value_function"]

    def test_extract_with_provider_override(self):
        """Test extraction with specific LLM provider override."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract(
            "paper_001", "Sample text", provider="openai"
        )

        assert mock_router.last_provider == "openai"

    def test_extract_with_settings_overrides(self):
        """Test extraction with LLM setting overrides."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        overrides = {"model": "qwen3.6:35b", "context_size": 32768}
        result = extractor.extract(
            "paper_001", "Sample text", overrides=overrides
        )

        assert mock_router.last_overrides["model"] == "qwen3.6:35b"
        assert mock_router.last_overrides["context_size"] == 32768
        assert mock_router.last_overrides["max_tokens"] == 8000
        assert mock_router.last_overrides["temperature"] == 0.1
        assert mock_router.last_overrides["top_p"] == 0.85
        assert mock_router.last_overrides["extra"]["json_mode"] is True

    def test_extract_handles_llm_errors(self):
        """Test extraction gracefully handles LLM errors."""
        mock_router = FakeLLMRouter()
        mock_router.chat = MagicMock(side_effect=RuntimeError("LLM timeout"))

        extractor = EntityExtractor(mock_router, quality_db_path=None)
        result = extractor.extract("paper_001", "Sample text")

        assert result.paper_id == "paper_001"
        assert "failed" in result.raw_response.lower()
        assert "partial recovery" not in result.raw_response.lower()
        assert len(result.concepts) == 0

    def test_extract_truncates_long_text(self):
        """Test extraction truncates very long paper text."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        # Create text > extractor text budget
        long_text = "x" * 90000

        result = extractor.extract("paper_001", long_text)

        # Check that chat was called with bounded text.
        assert mock_router.last_messages is not None
        message_text = mock_router.last_messages[-1]["content"]
        assert len(message_text) < len(long_text)
        assert "terminology_conflicts" in message_text

    def test_quick_mode_skips_semantic_calls(self):
        structural = json.dumps(
            {
                "concepts": [
                    {
                        "label": "Reinforcement Learning",
                        "context": "central method family",
                        "confidence": 0.91,
                        "salience": "central",
                    }
                ],
                "methods": [],
                "concept_candidates": [],
                "method_candidates": [],
            }
        )
        mock_router = SequenceLLMRouter([structural])
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract(
            "paper_001",
            "This paper studies reinforcement learning.",
            overrides={"extraction_mode": "quick"},
        )

        assert len(mock_router.calls) == 1
        assert result.extraction_mode == "quick"
        assert result.extraction_diagnostics["call_2_parse_quality"] == "skipped"
        assert result.claims == []

    def test_quality_mode_uses_semantic_claims_without_extra_claims_pass(self):
        structural = json.dumps(
            {
                "concepts": [
                    {
                        "label": "Reinforcement Learning",
                        "context": "central method family",
                        "confidence": 0.91,
                        "salience": "central",
                    }
                ],
                "methods": [],
                "concept_candidates": [],
                "method_candidates": [],
            }
        )
        semantic = json.dumps(
            {
                "paper_type": "research",
                "claims": [
                    {"statement": "Claim A", "evidence_type": "theoretical", "negated": False, "attributed_to": "this_paper"},
                    {"statement": "Claim B", "evidence_type": "theoretical", "negated": False, "attributed_to": "this_paper"},
                    {"statement": "Claim C", "evidence_type": "theoretical", "negated": False, "attributed_to": "this_paper"},
                ],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        mock_router = SequenceLLMRouter([structural, semantic])
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract("paper_001", "This paper studies reinforcement learning.")

        assert len(mock_router.calls) == 2
        assert [claim["statement"] for claim in result.claims] == ["Claim A", "Claim B", "Claim C"]

    def test_extraction_chunks_fit_16k_local_context(self):
        """Test local LM Studio-sized contexts produce smaller extraction chunks."""
        long_text = ("A section about neural networks.\n\n" * 2000).strip()

        chunks = EntityExtractor._build_extraction_chunks(long_text, context_size=16384)

        assert len(chunks) > 1
        assert max(len(chunk) for chunk in chunks) <= EntityExtractor._chunk_char_budget(16384)
        assert EntityExtractor._chunk_char_budget(32768) <= 18000
        assert EntityExtractor._chunk_char_budget(16384) < 30000

    def test_deterministic_scan_filters_reference_and_domain_noise(self):
        """Test scan avoids bibliography acronyms and official-statistics false positives."""
        mock_router = FakeLLMRouter(
            response_json={
                "paper_type": "survey",
                "concepts": [],
                "methods": [],
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        extractor = EntityExtractor(mock_router, quality_db_path=None)
        text = """
        Emotion in Reinforcement Learning Agents and Robots: A Survey
        Well-known algorithms are Q-learning, SARSA and TD(lambda).
        We adopt a Markov Decision Process (MDP) formulation.

        References
        Rummery GA, Niranjan M (1994) Evidence of convergent validity.
        In: Advanced Computer Control (ICACC), IEEE.
        The regulation of homeostatic behaviour is mentioned in a citation title.
        """

        result = extractor.extract("paper_001", text)

        labels = {concept["label"] for concept in result.concept_candidates}
        assert "TD(lambda)" in labels
        assert "Markov Decision Process" in labels
        mdp_concept = next(concept for concept in result.concept_candidates if concept["label"] == "Markov Decision Process")
        assert "MDP" in mdp_concept.get("aliases", [])
        assert "ICACC" not in labels
        assert "Advanced Computer Control" not in labels
        assert "Data Validity" not in labels
        assert "Regulation" not in labels
        assert result.paper_type == "survey"

    def test_regex_backfills_known_algorithms_and_math(self):
        """Test deterministic scan adds missed known algorithms and formula metadata."""
        mock_router = FakeLLMRouter(
            response_json={
                "paper_type": "survey",
                "concepts": [],
                "methods": [],
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {"paper_year": 2017, "reviewed_period": "1997-2017"},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract(
            "paper_001",
            "The survey compares Q-learning, SARSA, and Actor-Critic. Table 1 reports results. "
            "The value function Q(s, a) is discussed.",
        )

        labels = {concept["label"] for concept in result.concept_candidates}
        assert {"Q-learning", "SARSA", "Actor-Critic architecture"}.issubset(labels)
        assert any(concept.get("auto_detected") for concept in result.concept_candidates)
        assert not result.concepts
        assert result.mathematical_content["has_formulas"] is True
        assert "value_function" in result.mathematical_content["formula_types"]

    def test_rl_emotion_scan_adds_broader_concepts_and_varied_confidence(self):
        """Regression: RL/emotion survey fallback should not be a flat 0.74 list."""
        mock_router = FakeLLMRouter(
            response_json={
                "paper_type": "survey",
                "concepts": [],
                "methods": [],
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        text = """
        Emotion in Reinforcement Learning Agents and Robots: A Survey
        This survey reviews computational emotion models for reinforcement learning.
        Reinforcement learning agents use reward shaping, policy gradient methods, value iteration,
        cognitive appraisal, affective computing, emotional agents, and human feedback.
        Reward shaping and affective computing are discussed across multiple emotional agents.
        """

        result = extractor.extract("paper_001", text)

        labels = {concept["label"] for concept in result.concept_candidates}
        assert {"Reward shaping", "Policy gradient", "Value iteration", "Affective Computing", "Emotion modelling"}.issubset(labels)
        confidences = {concept["confidence"] for concept in result.concept_candidates if concept.get("candidate_source") == "deterministic_scan"}
        assert len(confidences) > 2
        assert confidences != {0.74}
        method_labels = {method["label"] for method in result.method_candidates}
        assert {"Reward shaping", "Policy gradient", "Value iteration"}.issubset(method_labels)

    def test_page_break_artifacts_are_cleaned_before_concept_post_processing(self):
        concepts = EntityExtractor._post_process_concepts(
            [
                {"label": "Reward Modi", "context": "Reward Modi ---PAGE BREAK--- Cation", "confidence": 0.7},
                {"label": "Reward Modi Cation", "context": "Reward Modi ---PAGE BREAK--- Cation", "confidence": 0.7},
                {"label": "Break--- Emotion", "context": "---PAGE BREAK--- Emotion", "confidence": 0.7},
                {"label": "---Page Break--- Emotion Reinforcement", "context": "---PAGE BREAK--- Emotion Reinforcement", "confidence": 0.7},
                {"label": "Reward Shaping", "context": "Reward shaping is used.", "confidence": 0.8},
            ]
        )

        labels = {concept["label"] for concept in concepts}
        assert "Reward Modi" not in labels
        assert "Break--- Emotion" not in labels
        assert "---Page Break--- Emotion Reinforcement" not in labels
        assert "Reward Modification" in labels
        assert "Reward Shaping" in labels

    def test_filter_concepts_removes_deterministic_artifacts_preserves_llm(self):
        concepts = filter_concepts(
            [
                {"label": "Learning Agents And Robots", "confidence": 0.8, "candidate_source": "deterministic_scan"},
                {"label": "Reward Modi", "confidence": 0.8, "candidate_source": "deterministic_scan"},
                {"label": "Questionnaire", "confidence": 0.8, "candidate_source": "deterministic_scan"},
                {"label": "Low Signal Concept", "confidence": 0.5, "candidate_source": "deterministic_scan"},
                {"label": "Questionnaire", "confidence": 0.9},
                {"label": "Reward Shaping", "confidence": 0.72, "candidate_source": "deterministic_scan"},
            ],
            title="Emotion in Reinforcement Learning Agents and Robots",
        )

        labels = [concept["label"] for concept in concepts]
        assert "Learning Agents And Robots" not in labels
        assert "Reward Modi" not in labels
        assert labels.count("Questionnaire") == 1
        assert "Low Signal Concept" not in labels
        assert "Reward Shaping" in labels

    def test_deduplicate_methods_merges_similar_labels_by_description(self, caplog):
        methods = [
            {
                "label": "Homeostasis-Based Elicitation",
                "domain": "unknown",
                "description": "Short.",
                "source_type": "reviewed_method",
            },
            {
                "label": "Homeostasis-based emotion elicitation",
                "domain": "Psychology",
                "description": "Uses homeostatic drives to elicit emotion-like signals in agents.",
                "source_type": "reviewed_method",
            },
            {
                "label": "Survey taxonomy of RL",
                "domain": "Reinforcement Learning",
                "description": "This paper contribution.",
                "source_type": "paper_contribution",
            },
            {
                "label": "Survey taxonomy of emotion RL",
                "domain": "Reinforcement Learning",
                "description": "Reviewed method with distinct source type.",
                "source_type": "reviewed_method",
            },
        ]

        with caplog.at_level("INFO"):
            deduped = deduplicate_methods(methods)

        labels = [method["label"] for method in deduped]
        assert "Homeostasis-based emotion elicitation" in labels
        assert len([label for label in labels if label.lower().startswith("homeostasis")]) == 1
        assert "Survey taxonomy of RL" in labels
        assert "Survey taxonomy of emotion RL" in labels
        assert "Merged duplicate method" in caplog.text

    def test_enrich_method_domains_infers_unknown_domains_only(self):
        methods = enrich_method_domains(
            [
                {
                    "label": "Reward shaping",
                    "domain": "unknown",
                    "description": "Modifies reward signals for an agent policy.",
                },
                {
                    "label": "Gradient clipping",
                    "domain": "Optimization",
                    "description": "Uses gradients during training.",
                },
                {
                    "label": "Custom framework",
                    "domain": "unknown",
                    "description": "Combines multiple research traditions.",
                },
            ]
        )

        assert methods[0]["domain"] == "Reinforcement Learning"
        assert methods[1]["domain"] == "Optimization"
        assert methods[2]["domain"] == "Interdisciplinary"

    def test_safe_llm_extract_recovers_fenced_json_and_empty_retry(self):
        responses = iter(["```json\n[]\n```", "prefix [{\"statement\":\"Concrete claim\"}] suffix"])

        values = safe_llm_extract(
            "Extract claims",
            lambda prompt: next(responses),
            field_name="claims",
        )

        assert values == [{"statement": "Concrete claim"}]

    def test_claims_prompt_requires_json_array_and_claim_types(self):
        assert "valid JSON array only" in CLAIMS_EXTRACTION_PROMPT
        assert "Contribution claims" in CLAIMS_EXTRACTION_PROMPT
        assert "Empirical findings" in CLAIMS_EXTRACTION_PROMPT
        assert "Methodological recommendations" in CLAIMS_EXTRACTION_PROMPT
        assert "Negative findings" in CLAIMS_EXTRACTION_PROMPT
        assert "Comparative claims" in CLAIMS_EXTRACTION_PROMPT

    def test_extraction_source_text_repairs_page_break_word_fragments(self):
        text = EntityExtractor._clean_extraction_source_text(
            "Reward Modi\n\n---PAGE BREAK---\n\nCation and ---PAGE BREAK--- Emotion Reinforcement"
        )

        assert "Reward Modification" in text
        assert "PAGE BREAK" not in text
        assert "---" not in text

    def test_official_statistics_paper_gets_domain_entities_when_llm_under_extracts(self):
        """Regression for changing data sources paper: deterministic layer prevents empty KG payloads."""
        mock_router = FakeLLMRouter(
            response_json={
                "paper_type": "research",
                "concepts": [],
                "methods": [],
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {"paper_year": None, "reviewed_period": None},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        extractor = EntityExtractor(mock_router, quality_db_path=None)
        text = """
        CHANGING DATA SOURCES IN THE AGE OF MACHINE LEARNING FOR OFFICIAL STATISTICS
        June 6, 2023
        ABSTRACT
        Changes in data sources pose risks for machine learning for official statistics.
        The repercussions include concept drift, bias, availability, validity, accuracy,
        completeness, neutrality, privacy, ownership, ethics, regulation, and public perception.
        Data pipelines should monitor changes in incoming data frequency and model retraining
        can mitigate distribution changes in derived data fields.
        """

        result = extractor.extract("arxiv:2306.04338", text)

        labels = {concept["label"] for concept in result.concept_candidates}
        assert {
            "Official Statistics",
            "Machine Learning",
            "Concept Drift",
            "Bias",
            "Data Availability",
            "Data Validity",
            "Data Accuracy",
            "Data Completeness",
            "Privacy",
            "Regulation",
        }.issubset(labels)
        method_labels = {method["label"] for method in result.method_candidates}
        assert "Data Pipeline Monitoring" in method_labels
        assert result.temporal_coverage["paper_year"] == 2023
        assert result.candidate_count >= 10

    def test_partial_structural_recovery_retries_methods_only(self, caplog):
        """Regression: partial Call 1 recovery must not silently lose methods."""
        structural_partial = (
            '{"concepts": ['
            '{"label": "Reinforcement Learning", "context": "Reinforcement Learning (RL)", "confidence": 0.91}'
            '], "paper_type": "research"'
        )
        methods_retry = json.dumps(
            [
                {
                    "label": "Q-learning",
                    "domain": "reinforcement learning",
                    "description": "Learns state-action values from temporal difference updates.",
                    "source_type": "reviewed_method",
                }
            ]
        )
        semantic = json.dumps(
            {
                "paper_type": "research",
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        mock_router = SequenceLLMRouter([structural_partial, structural_partial, methods_retry, semantic])
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        with caplog.at_level("WARNING"):
            result = extractor.extract(
                "paper_001",
                "This paper studies Reinforcement Learning (RL) with Q-learning.",
            )

        assert [method["label"] for method in result.methods] == ["Q-learning"]
        assert "Methods lost in partial recovery — running methods-only retry" in caplog.text
        assert mock_router.calls[2]["overrides"]["max_tokens"] == 12000
        assert mock_router.calls[2]["overrides"]["temperature"] == 0.1
        assert mock_router.calls[2]["messages"] == [
            {
                "role": "user",
                "content": EntityExtractor.METHODS_ONLY_PROMPT.replace(
                    "{paper_text}",
                    "This paper studies Reinforcement Learning (RL) with Q-learning.",
                ),
            }
        ]

    def test_partial_structural_recovery_retries_when_methods_key_is_empty(self):
        """Regression: partial Call 1 with methods: [] still needs a retry."""
        structural_partial = (
            '{"concepts": ['
            '{"label": "Reinforcement Learning", "context": "Reinforcement Learning (RL)", "confidence": 0.91}'
            '], "methods": []'
        )
        methods_retry = json.dumps(
            [
                {
                    "label": "SARSA",
                    "domain": "reinforcement learning",
                    "description": "Temporal-difference control method.",
                    "source_type": "reviewed_method",
                }
            ]
        )
        semantic = json.dumps(
            {
                "paper_type": "survey",
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        mock_router = SequenceLLMRouter([structural_partial, structural_partial, methods_retry, semantic])
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract("paper_001", "A survey of Reinforcement Learning (RL) and SARSA.")

        assert [method["label"] for method in result.methods] == ["SARSA"]
        assert result.extraction_diagnostics["methods_retry_parse_quality"] == "clean"

    def test_partial_oversized_structural_chunk_is_split_and_merged(self):
        """Regression: malformed large structural chunks should be split before accepting loss."""
        structural_partial = (
            '{"concepts": ['
            '{"label": "Homeostasis", "context": "homeostatic variables", "confidence": 0.91}'
            '], "methods": []'
        )
        split_one = json.dumps(
            {
                "concepts": [
                    {
                        "label": "Homeostasis",
                        "context": "homeostatic variables derive internal drives",
                        "evidence_span": "homeostatic variables derive internal drives",
                        "confidence": 0.91,
                        "salience": "central",
                    }
                ],
                "methods": [],
                "concept_candidates": [],
                "method_candidates": [],
            }
        )
        split_two = json.dumps(
            {
                "concepts": [
                    {
                        "label": "Appraisal dimensions",
                        "context": "appraisal dimensions include novelty and valence",
                        "evidence_span": "appraisal dimensions include novelty and valence",
                        "confidence": 0.9,
                        "salience": "central",
                    }
                ],
                "methods": [],
                "concept_candidates": [],
                "method_candidates": [],
            }
        )
        semantic = json.dumps(
            {
                "paper_type": "survey",
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        long_text = (
            ("Homeostasis controls internal drives and intrinsic motivation. " * 95)
            + "\n\n"
            + ("Appraisal dimensions include novelty, valence, and control. " * 95)
        )
        mock_router = SequenceLLMRouter([structural_partial, structural_partial, split_one, split_two, semantic])
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract("paper_001", long_text)

        labels = {concept["label"] for concept in result.concepts}
        assert {"Homeostasis", "Appraisal dimensions"}.issubset(labels)
        assert result.extraction_diagnostics["call_1_parse_quality"] == "clean"
        assert result.extraction_diagnostics["calls"][0]["recovery_strategy"] == "split_retry"

    def test_partial_semantic_recovery_retries_claims(self, caplog):
        """Regression: partial Call 2 recovery must not silently lose claims."""
        structural = json.dumps(
            {
                "concepts": [{"label": "Reinforcement Learning", "context": "RL survey", "confidence": 0.9}],
                "methods": [
                    {
                        "label": "Q-learning",
                        "domain": "reinforcement learning",
                        "description": "Learns action values.",
                        "source_type": "reviewed_method",
                    }
                ],
            }
        )
        semantic_partial = (
            '{"paper_type":"survey","claims":[],"temporal_coverage":{"paper_year":2017},'
            '"mathematical_content":{"has_formulas":true}'
        )
        semantic_retry = json.dumps(
            {
                "claims": [
                    {
                        "statement": "A unified framework connecting emotion implementations in RL is lacking.",
                        "evidence_type": "review",
                        "negated": False,
                        "attributed_to": "this_paper",
                    }
                ],
                "cross_domain_hints": [
                    {
                        "field": "human-robot interaction",
                        "why_applicable": "Emotion-conditioned reward signals can transfer to interactive agents.",
                    }
                ],
                "terminology_conflicts": [],
            }
        )
        mock_router = SequenceLLMRouter([structural, semantic_partial, semantic_retry])
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        with caplog.at_level("WARNING"):
            result = extractor.extract("paper_001", "This survey analyzes Reinforcement Learning and emotion models.")

        assert result.claims[0]["statement"].startswith("A unified framework")
        assert result.cross_domain_hints[0]["field"] == "human-robot interaction"
        assert result.extraction_diagnostics["call_2_parse_quality"] == "clean"
        assert len(mock_router.calls) == 3

    def test_concept_post_processing_resolves_abbreviations_and_compounds(self):
        """Regression: abbreviations should become aliases, not false KG nodes."""
        structural = json.dumps(
            {
                "concepts": [
                    {
                        "label": "Reinforcement Learning",
                        "context": "Reinforcement Learning (RL) is used.",
                        "confidence": 0.94,
                    },
                    {"label": "RL", "context": "Reinforcement Learning (RL) is used.", "confidence": 0.65},
                    {
                        "label": "Machine Learning",
                        "context": "Machine Learning (ML) supports automation.",
                        "confidence": 0.92,
                    },
                    {"label": "ML", "context": "Machine Learning (ML) supports automation.", "confidence": 0.64},
                    {
                        "label": "Human-Robot Interaction",
                        "context": "Human-Robot Interaction (HRI) is evaluated.",
                        "confidence": 0.9,
                    },
                    {"label": "HRI", "context": "Human-Robot Interaction (HRI) is evaluated.", "confidence": 0.63},
                    {
                        "label": "Dynamic Programming",
                        "context": "Dynamic Programming (DP) is a baseline.",
                        "confidence": 0.88,
                    },
                    {"label": "DP", "context": "Dynamic Programming (DP) is a baseline.", "confidence": 0.62},
                    {
                        "label": "ML and human-robot interaction",
                        "context": "ML and human-robot interaction are related.",
                        "confidence": 0.55,
                    },
                ],
                "methods": [],
            }
        )
        semantic = json.dumps(
            {
                "paper_type": "research",
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
        mock_router = SequenceLLMRouter([structural, semantic])
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        result = extractor.extract(
            "paper_001",
            "Reinforcement Learning (RL), Machine Learning (ML), Human-Robot Interaction (HRI), "
            "and Dynamic Programming (DP) are discussed.",
        )

        labels = {concept["label"] for concept in result.concepts}
        assert {"Reinforcement Learning", "Human-Robot Interaction", "Dynamic Programming"}.issubset(labels)
        assert "Machine Learning" not in labels
        candidate_labels = {concept["label"] for concept in result.concept_candidates}
        assert "Machine Learning" in candidate_labels
        assert {"RL", "ML", "HRI", "DP", "ML and human-robot interaction"}.isdisjoint(labels)
        aliases_by_label = {concept["label"]: set(concept.get("aliases", [])) for concept in result.concepts}
        assert "RL" in aliases_by_label["Reinforcement Learning"]
        assert "HRI" in aliases_by_label["Human-Robot Interaction"]
        assert "DP" in aliases_by_label["Dynamic Programming"]
        candidate_aliases_by_label = {
            concept["label"]: set(concept.get("aliases", []))
            for concept in result.concept_candidates
        }
        assert "ML" in candidate_aliases_by_label["Machine Learning"]


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

    def test_openalx_linkage_strategy_matches_alias(self):
        """Test strategy matches cached aliases."""
        cache = {
            "gradient descent": {
                "id": "C456",
                "display_name": "Gradient Descent",
                "aliases": ["GD"],
            }
        }
        strategy = OpenAlexLinkageStrategy(concept_cache=cache)

        result = strategy.link({"label": "gd", "context": "...", "confidence": 0.9})

        assert result is not None
        assert result["openalx_id"] == "C456"

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
        mock_router = FakeLLMRouter(
            response_json={
                "paper_type": "research",
                "concepts": [{"label": "neural network", "context": "model", "confidence": 0.9}],
                "methods": [],
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {"paper_year": None, "reviewed_period": None},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            }
        )
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
        assert result.concepts[0]["openalx_id"] == "C123"

    def test_canonical_resolver_maps_ontology_aliases_and_marks_approved(self):
        resolver = CanonicalResolver(embedding_engine=EmbeddingEngine())

        result = resolver.resolve(
            {
                "label": "Cognitive appraisal theory",
                "context": "componential emotion theory",
                "confidence": 0.9,
                "accepted": True,
                "review_status": "pending",
            }
        )

        assert result["canonical_label"] == "Appraisal theory"
        assert result["entity_type"] == "Theory"
        assert result["review_status"] == "approved"
        assert result["canonical_match"]["match_type"] == "exact_alias"

    def test_scientific_normalization_repairs_ligatures_and_lambda_slugs(self):
        assert normalize_key("Aﬀective modelling") == "affectivemodelling"
        assert slugify_label("TD(λ)") == "td-lambda"

    def test_entity_linker_merges_aliases_and_builds_controlled_relations(self):
        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {
                    "label": "Cognitive appraisal theory",
                    "confidence": 0.92,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "cognitive appraisal theory",
                },
                {
                    "label": "Appraisal dimensions",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "appraisal dimensions are valence and novelty",
                },
            ],
            methods=[],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        assert [concept["canonical_id"] for concept in result.concepts] == [
            "concept:appraisal-theory",
            "concept:appraisal-dimensions",
        ]
        assert all(concept["review_status"] == "approved" for concept in result.concepts)
        assert result.relations[0]["relation_type"] == "PART_OF"
        assert result.relations[0]["subject_id"] == "concept:appraisal-dimensions"
        rendered = json.loads(result.raw_response)
        assert rendered["concepts"][0]["canonical_id"] == "concept:appraisal-theory"
        assert rendered["concepts"][0]["review_status"] == "approved"
        assert rendered["relations"][0]["subject_id"] == "concept:appraisal-dimensions"

    def test_entity_linker_rescues_exact_ontology_candidates_from_partial_chunks(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[
                {
                    "label": "Reward function",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "reward function",
                }
            ],
            methods=[],
            concept_candidates=[
                {
                    "label": "OCC model",
                    "entity_type": "Theory",
                    "context": "well-known appraisal theories include the OCC model",
                    "evidence_span": "well-known appraisal theories include the OCC model",
                    "confidence": 0.72,
                    "salience": "supporting",
                    "mention_count": 2,
                    "accepted": False,
                    "review_status": "pending",
                },
                {
                    "label": "Q-learning",
                    "entity_type": "Algorithm",
                    "context": "background RL algorithm",
                    "evidence_span": "Well-known algorithms are Q-learning",
                    "confidence": 0.8,
                    "salience": "supporting",
                    "mention_count": 3,
                    "accepted": False,
                    "review_status": "pending",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        concept_ids = {concept["canonical_id"] for concept in result.concepts}
        candidate_ids = {concept["canonical_id"] for concept in result.concept_candidates}
        assert "concept:occ-model" in concept_ids
        assert "concept:q-learning" not in concept_ids
        assert "concept:q-learning" in candidate_ids
        occ_model = next(concept for concept in result.concepts if concept["canonical_id"] == "concept:occ-model")
        assert occ_model["review_status"] == "approved"
        assert occ_model["acceptance_reason"] == "ontology_exact_candidate_rescue"

    def test_canonical_resolver_exact_alias_overrides_llm_type(self):
        resolver = CanonicalResolver(embedding_engine=EmbeddingEngine())

        result = resolver.resolve(
            {
                "label": "POMDP",
                "entity_type": "Algorithm",
                "confidence": 0.85,
                "accepted": True,
                "review_status": "pending",
                "evidence_span": "POMDP variant called Bayesian Affect Control Theory",
            }
        )

        assert result["canonical_id"] == "concept:pomdp"
        assert result["canonical_label"] == "POMDP"
        assert result["entity_type"] == "ModelArchitecture"
        assert result["review_status"] == "approved"
        assert result["canonical_match"]["match_type"] == "exact_alias"

    def test_entity_linker_dedupes_cross_prefix_concept_method_labels(self):
        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {
                    "label": "approach and avoidance behaviour",
                    "entity_type": "Phenomenon",
                    "canonical_id": "concept:approach-and-avoidance-behaviour",
                    "canonical_label": "approach and avoidance behaviour",
                    "confidence": 0.8,
                    "accepted": True,
                    "review_status": "approved",
                    "evidence_span": "observed approach and avoidance behaviour",
                }
            ],
            methods=[
                {
                    "label": "approach and avoidance behaviour",
                    "entity_type": "Phenomenon",
                    "canonical_id": "method:approach-and-avoidance-behaviour",
                    "canonical_label": "approach and avoidance behaviour",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "approved",
                    "evidence_span": "approach and avoidance behaviour in their emotional agent",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        assert len(result.concepts) == 1
        assert result.concepts[0]["canonical_id"] == "concept:approach-and-avoidance-behaviour"
        assert result.concepts[0]["extracted_roles"] == ["concept", "method"]
        assert result.methods == []

    def test_entity_linker_builds_specific_relation_types(self):
        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {
                    "label": "Reinforcement Learning",
                    "entity_type": "MethodFamily",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "reinforcement learning agents",
                },
                {
                    "label": "Temporal difference error",
                    "entity_type": "Metric",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "connection between dopamine and the TD",
                },
                {
                    "label": "Dopamine",
                    "entity_type": "Phenomenon",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "connection between dopamine and the TD",
                },
                {
                    "label": "OCC model",
                    "entity_type": "Theory",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "OCC model named after Ortony Clore and Collins",
                },
                {
                    "label": "Cognitive appraisal theory",
                    "entity_type": "Theory",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "componential emotion theory, best known as cognitive appraisal theory",
                },
                {
                    "label": "Valence",
                    "entity_type": "DomainConcept",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "the most implemented dimension is valence",
                },
                {
                    "label": "Dimensional emotion theory",
                    "entity_type": "Theory",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Dimensional emotion theory assumes an affective space",
                },
                {
                    "label": "POMDP",
                    "entity_type": "Algorithm",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "POMDP variant called Bayesian Affect Control Theory",
                },
                {
                    "label": "Bayesian Affect Control Theory",
                    "entity_type": "Theory",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "POMDP variant called Bayesian Affect Control Theory",
                },
            ],
            methods=[
                {
                    "label": "Q-learning",
                    "entity_type": "Algorithm",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Well-known algorithms are Q-learning",
                },
                {
                    "label": "Boltzmann action selection",
                    "entity_type": "Algorithm",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Boltzmann action selection mechanism",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"])
            for relation in result.relations
        }

        assert (
            "concept:temporal-difference-error",
            "CORRESPONDS_TO",
            "concept:dopamine",
        ) in relation_triples
        assert ("concept:occ-model", "IS_A", "concept:appraisal-theory") in relation_triples
        assert ("concept:valence", "PART_OF", "concept:dimensional-emotion-theory") in relation_triples
        assert ("concept:bayesian-affect-control-theory", "EXTENDS", "concept:pomdp") in relation_triples
        assert ("concept:q-learning", "USED_IN", "concept:reinforcement-learning") in relation_triples
        assert ("concept:q-learning", "IS_A", "concept:reinforcement-learning") not in relation_triples
        assert (
            "concept:boltzmann-action-selection",
            "MODULATED_BY",
            "concept:valence",
        ) in relation_triples

    def test_canonical_resolver_degrades_hash_embeddings_without_auto_merge(self):
        resolver = CanonicalResolver(embedding_engine=EmbeddingEngine())

        result = resolver.resolve(
            {"label": "Unseen Appraisal Variant", "context": "novel phrase", "confidence": 0.95}
        )

        assert result["review_status"] == "pending"
        assert result["canonical_match"]["match_type"] == "none"
        assert result["canonical_match"]["degraded_similarity"] is True
        assert result["merge_candidates"] == []

    def test_ontology_rejects_unknown_relation_types(self):
        ontology = Ontology.from_file()

        assert ontology.validate_relation_type("USES") == "USES"
        assert ontology.validate_relation_type("MODULATED_BY") == "MODULATED_BY"
        with pytest.raises(ValueError):
            ontology.validate_relation_type("MAKES_UP")


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
        assert result.backend == "hash-fallback"

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

    def test_conflict_detector_uses_lightweight_overrides(self):
        """Regression: pairwise conflict calls must not inherit extraction token budget."""
        mock_router = FakeLLMRouter(
            response_json={"conflict_type": "irrelevant", "confidence": 0.1}
        )

        detector = ConflictDetector(mock_router)
        detector.analyze_claim_pair(
            "Emotion models improve reinforcement learning.",
            "Reward shaping improves reinforcement learning.",
            overrides={
                "model": "qwen3.6-35b",
                "max_tokens": 65536,
                "temperature": 0.8,
                "extra": {"chat_template_kwargs": {"enable_thinking": True}},
            },
        )

        assert mock_router.last_overrides["model"] == "qwen3.6-35b"
        assert mock_router.last_overrides["max_tokens"] == 800
        assert mock_router.last_overrides["temperature"] == 0.1
        assert mock_router.last_overrides["extra"]["json_mode"] is True
        assert mock_router.last_overrides["extra"]["chat_template_kwargs"]["enable_thinking"] is False

    def test_conflict_detector_caps_large_batches_to_related_pairs(self):
        """Regression: large claim sets should not trigger O(n^2) LLM calls."""
        mock_router = FakeLLMRouter(
            response_json={"conflict_type": "supporting", "confidence": 0.7}
        )

        detector = ConflictDetector(mock_router)
        claims = [
            "Q-learning improves reward shaping in reinforcement learning.",
            "Reward shaping improves exploration in reinforcement learning agents.",
            "Survey papers discuss taxonomy design.",
            "Datasets require careful annotation.",
            "Human robot interaction uses social signals.",
            "Bayesian models estimate uncertainty.",
            "Optimization objectives can be non-convex.",
            "Sensor calibration affects robotics experiments.",
            "User studies measure engagement.",
            "Formal proofs establish convergence.",
            "Affective computing represents valence.",
            "Policy gradients optimize expected return.",
        ]

        analyses = detector.analyze_claims_batch(claims, max_pairs=5)

        assert len(analyses) <= 5
        assert mock_router.chat_json_calls <= 5
        assert analyses
        assert any("reinforcement learning" in " ".join(a.claim_pair).lower() for a in analyses)

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

    def test_parser_router_parse_uses_marker_preview_for_selection(self):
        """Test automatic parse selection uses a Marker text probe."""
        router = ParserRouter()
        marker_result = MagicMock(
            text="The equation $$E=mc^2$$ and \\alpha appear repeatedly.",
            parser=ParserType.MARKER,
        )
        nougat_result = MagicMock(text="nougat output", parser=ParserType.NOUGAT)
        mock_marker = MagicMock()
        mock_marker.parse.return_value = marker_result
        mock_nougat = MagicMock()
        mock_nougat.parse.return_value = nougat_result

        router.register_parser(ParserType.MARKER, mock_marker)
        router.register_parser(ParserType.NOUGAT, mock_nougat)

        result = router.parse("/fake.pdf", "paper_001")

        assert result is nougat_result
        assert result.parser == ParserType.NOUGAT
        assert mock_marker.parse.called
        assert mock_nougat.parse.called

    def test_parser_router_falls_back_when_selected_parser_raises(self):
        """Harvested PDFs should still parse when a specialized parser fails at runtime."""
        router = ParserRouter()
        marker_result = MagicMock(
            text="The equation $$E=mc^2$$ and \\alpha appear repeatedly.",
            parser=ParserType.MARKER,
            metadata={},
        )
        mock_marker = MagicMock()
        mock_marker.parse.return_value = marker_result
        mock_nougat = MagicMock()
        mock_nougat.parse.side_effect = RuntimeError("nougat service unavailable")

        router.register_parser(ParserType.MARKER, mock_marker)
        router.register_parser(ParserType.NOUGAT, mock_nougat)

        result = router.parse("/fake.pdf", "paper_001")

        assert result is marker_result
        assert result.parser == ParserType.MARKER
        assert result.metadata["parser_fallback_from"] == "nougat"
        assert "nougat service unavailable" in result.metadata["parser_fallback_error"]


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

    def test_batch_processor_persists_extraction_results(self, tmp_path):
        """Test batch processor can persist successful extraction results."""
        from storage.metadata_db import MetadataDB

        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        parser_router.parse = MagicMock(
            return_value=MagicMock(text="Paper text about neural networks")
        )
        db = MetadataDB(str(tmp_path / "metadata.duckdb"))
        processor = BatchProcessor(mock_llm, parser_router, metadata_db=db)

        status = processor.process_papers(
            ["paper_001"],
            {"paper_001": str(tmp_path / "paper.pdf")},
            job_id="persist_job",
        )

        assert status.status == "completed"
        assert status.papers_processed == 1
        assert len(db.get_paper_extractions("paper_001")) == 1
        job = db.get_batch_job("persist_job")
        assert job is not None
        assert job["status"] == "completed"
        items = db.get_batch_job_items("persist_job")
        assert items[0]["status"] == "completed"
        assert len(db.list_entity_embeddings()) == 1
        db.close()

    def test_batch_processor_resumes_completed_items_from_storage(self, tmp_path):
        """Test completed items are skipped when a persistent job is resumed."""
        from storage.metadata_db import MetadataDB

        db_path = tmp_path / "metadata.duckdb"
        db = MetadataDB(str(db_path))
        db.upsert_batch_job("resume_job", "processing", papers_total=1, papers_processed=1)
        db.upsert_batch_job_item("resume_job", "paper_001", str(tmp_path / "paper.pdf"), "completed")
        db.close()

        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        parser_router.parse = MagicMock(side_effect=AssertionError("should not reparse"))
        processor = BatchProcessor(
            mock_llm,
            parser_router,
            metadata_db_factory=lambda: MetadataDB(str(db_path)),
        )

        status = processor.process_papers(
            ["paper_001"],
            {"paper_001": str(tmp_path / "paper.pdf")},
            job_id="resume_job",
        )

        assert status.status == "completed"
        assert status.papers_processed == 1
        assert parser_router.parse.call_count == 0

    def test_batch_processor_marks_completed_with_errors(self, tmp_path):
        """Test partial failures are visible in durable aggregate status."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        processor = BatchProcessor(mock_llm, parser_router)

        status = processor.process_papers(
            ["paper_001"],
            {},
            job_id="missing_pdf_job",
        )

        assert status.status == "completed_with_errors"
        assert status.papers_failed == 1

    def test_batch_processor_retries_failed_parse(self, tmp_path):
        """Test batch processor retries transient parser failures."""
        mock_llm = FakeLLMRouter()
        parser_router = ParserRouter()
        parser_router.parse = MagicMock(
            side_effect=[
                RuntimeError("temporary parse failure"),
                MagicMock(text="Paper text about neural networks"),
            ]
        )
        processor = BatchProcessor(mock_llm, parser_router, max_retries=1)

        status = processor.process_papers(
            ["paper_001"],
            {"paper_001": str(tmp_path / "paper.pdf")},
            job_id="retry_job",
        )

        assert status.papers_processed == 1
        assert status.papers_failed == 0
        assert parser_router.parse.call_count == 2


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
