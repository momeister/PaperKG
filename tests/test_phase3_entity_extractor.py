import pytest
import json
from unittest.mock import MagicMock

from extraction.entity_extractor import (
    CLAIMS_EXTRACTION_PROMPT,
    EntityExtractor,
    ParsedLLMResponse,
    deduplicate_methods,
    enrich_method_domains,
    extraction_failure_reason,
    filter_concepts,
    safe_llm_extract,
)
from extraction.entity_linker import (
    ExtractionPipeline,
)

from tests.llm_fakes import FakeLLMRouter, SequenceLLMRouter

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
        assert result.paper_node["paper_id"] == "paper_001"
        assert result.paper_node["node_type"] == "Paper"

    def test_build_paper_node_materializes_extraction_anchor(self):
        node = EntityExtractor._build_paper_node(
            paper_id="arxiv:1705.05172",
            paper_text="Emotion in Reinforcement Learning Agents and Robots: A Survey\n\nAbstract\n...",
            paper_type="survey",
            semantic_paper_node={},
            temporal_coverage={"paper_year": 2017, "reviewed_period": "1997-2017"},
            language_detected="en",
        )

        assert node["node_type"] == "Paper"
        assert node["paper_id"] == "arxiv:1705.05172"
        assert node["title"] == "Emotion in Reinforcement Learning Agents and Robots: A Survey"
        assert node["paper_type"] == "survey"
        assert node["paper_year"] == 2017

    def test_build_paper_node_blocks_conflicting_title_metadata(self):
        node = EntityExtractor._build_paper_node(
            paper_id="arxiv:2507.16947",
            paper_text=(
                "Assessing workflow impact and clinical utility of AI-assisted brain aneurysm "
                "detection: a multi-reader study\n\nAbstract\n..."
            ),
            paper_type="benchmark",
            semantic_paper_node={
                "title": "AI-based Clinical Decision Support for Primary Care: A Real-World Study",
                "paper_year": 2025,
            },
            temporal_coverage={"paper_year": 2025},
            language_detected="en",
        )
        warnings = EntityExtractor._quality_warnings(
            paper_type="benchmark",
            concept_count=20,
            method_count=6,
            text_length=30000,
            parse_quality="clean",
            paper_id="arxiv:2507.16947",
            paper_node=node,
        )
        validation = EntityExtractor._metadata_validation("arxiv:2507.16947", node)

        assert node["title"].startswith("Assessing workflow impact")
        assert node["detected_title"].startswith("Assessing workflow impact")
        assert node["llm_paper_title"] == "AI-based Clinical Decision Support for Primary Care: A Real-World Study"
        assert any("title conflicts" in warning for warning in warnings)
        assert validation["metadata_status"] == "invalid"
        assert any(error.startswith("paper_title_mismatch") for error in validation["blocking_errors"])

    def test_build_paper_node_prefers_arxiv_year_over_reference_year(self):
        node = EntityExtractor._build_paper_node(
            paper_id="arxiv:2503.17786",
            paper_text=(
                "Assessing workflow impact and clinical utility of AI-assisted brain aneurysm "
                "detection: a multi-reader study\n\nAbstract\n..."
            ),
            paper_type="research",
            semantic_paper_node={
                "title": (
                    "Assessing workflow impact and clinical utility of AI-assisted brain aneurysm "
                    "detection: a multi-reader study"
                ),
                "paper_year": 2016,
            },
            temporal_coverage={"paper_year": 2016},
            language_detected="en",
        )
        validation = EntityExtractor._metadata_validation("arxiv:2503.17786", node)
        warnings = EntityExtractor._quality_warnings(
            paper_type="research",
            concept_count=20,
            method_count=6,
            text_length=30000,
            parse_quality="clean",
            paper_id="arxiv:2503.17786",
            paper_node=node,
        )

        assert node["paper_year"] == 2025
        assert node["llm_paper_year"] == 2016
        assert node["paper_year_source"] == "arxiv_id"
        assert validation["metadata_status"] == "valid"
        assert not validation["blocking_errors"]
        assert any("using arXiv metadata year" in warning for warning in warnings)

    def test_quality_warnings_flag_paper_id_metadata_mismatch(self):
        node = EntityExtractor._build_paper_node(
            paper_id="arxiv:2509.08759",
            paper_text="arXiv:1705.05172\nEmotion in Reinforcement Learning Agents and Robots: A Survey",
            paper_type="survey",
            semantic_paper_node={
                "title": "Emotion in Reinforcement Learning Agents and Robots: A Survey",
            },
            temporal_coverage={"paper_year": 2017},
            language_detected="en",
        )

        warnings = EntityExtractor._quality_warnings(
            paper_type="survey",
            concept_count=40,
            method_count=10,
            text_length=30000,
            parse_quality="clean",
            paper_id="arxiv:2509.08759",
            paper_node=node,
        )
        validation = EntityExtractor._metadata_validation(
            paper_id="arxiv:2509.08759",
            paper_node=node,
        )

        assert node["detected_source_id"] == "arxiv:1705.05172"
        assert any("implies year 2025" in warning for warning in warnings)
        assert any("text contains arxiv:1705.05172" in warning.lower() for warning in warnings)
        assert validation["metadata_status"] == "invalid"
        assert "paper_id_mismatch: supplied arxiv:2509.08759, extracted arxiv:1705.05172" in validation[
            "blocking_errors"
        ]
        assert any(error.startswith("paper_id_year_mismatch") for error in validation["blocking_errors"])

    def test_front_matter_arxiv_identifier_detects_late_explicit_id(self):
        paper_text = (
            "MerLin: A Discovery Engine for Photonic and Hybrid Quantum Machine Learning\n"
            "Authors and affiliations\n"
            + ("Long affiliation line. " * 300)
            + "arXiv:2602.11092v2\n"
            "Abstract\nWe introduce MerLin."
        )

        node = EntityExtractor._build_paper_node(
            paper_id="arxiv:2509.08759",
            paper_text=paper_text,
            paper_type="benchmark",
            semantic_paper_node={
                "title": "MerLin: A Discovery Engine for Photonic and Hybrid Quantum Machine Learning",
                "paper_year": 2026,
            },
            temporal_coverage={"paper_year": 2026},
            language_detected="en",
        )
        validation = EntityExtractor._metadata_validation("arxiv:2509.08759", node)

        assert node["detected_source_id"] == "arxiv:2602.11092"
        assert validation["metadata_status"] == "invalid"
        assert "paper_id_mismatch: supplied arxiv:2509.08759, extracted arxiv:2602.11092" in validation[
            "blocking_errors"
        ]

    def test_legacy_arxiv_identifier_normalizes_from_storage_filename(self):
        node = EntityExtractor._build_paper_node(
            paper_id="arxiv__the-neurobiology-of-thinking-identity-and-geniality__q-bio_0612009",
            paper_text="arXiv:q-bio/0612009\nThe Neurobiology Of Thinking, Identity, And Geniality\nAbstract\n...",
            paper_type="theoretical",
            semantic_paper_node={
                "title": "The Neurobiology Of Thinking, Identity, And Geniality",
            },
            temporal_coverage={"paper_year": 2006},
            language_detected="en",
        )
        validation = EntityExtractor._metadata_validation(
            "arxiv__the-neurobiology-of-thinking-identity-and-geniality__q-bio_0612009",
            node,
        )

        assert node["paper_id"] == "arxiv:q-bio/0612009"
        assert EntityExtractor._arxiv_publication_year("arxiv:q-bio/0612009") == 2006
        assert validation["metadata_status"] == "valid"

    def test_merlin_framework_text_overrides_semantic_survey_type_to_benchmark(self):
        text = (
            "MerLin: A Discovery Engine for Photonic and Hybrid Quantum Machine Learning\n"
            "Abstract\n"
            "We introduce MerLin, an open-source framework designed as a discovery engine. "
            "The paper evaluates benchmark tasks on MNIST and SST2 datasets and reproduces QGANs."
        )

        assert EntityExtractor._detect_paper_type(text) == "benchmark"
        assert EntityExtractor._resolve_paper_type("survey", "benchmark", text) == "benchmark"
        assert EntityExtractor._resolve_paper_type("research", "survey", "This survey reviews RL.") == "survey"

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

        extractor.extract(
            "paper_001", "Sample text", provider="openai"
        )

        assert mock_router.last_provider == "openai"

    def test_extract_with_settings_overrides(self):
        """Test extraction with LLM setting overrides."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        overrides = {"model": "qwen3.6:35b", "context_size": 32768}
        extractor.extract(
            "paper_001", "Sample text", overrides=overrides
        )

        assert mock_router.last_overrides["model"] == "qwen3.6:35b"
        assert mock_router.last_overrides["context_size"] == 32768
        assert mock_router.last_overrides["max_tokens"] == 8000
        assert mock_router.last_overrides["temperature"] == 0.1
        assert mock_router.last_overrides["top_p"] == 0.85
        assert mock_router.last_overrides["extra"]["json_mode"] is True

    def test_failed_structural_calls_trigger_small_retries(self):
        scan = type("Scan", (), {"concepts": [{"label": "MerLin"}], "methods": []})()
        failed_call = ParsedLLMResponse(data={}, parse_quality="failed", raw_text="LLM call failed: 422 bad request")

        assert EntityExtractor._should_retry_concepts([], [failed_call], scan)
        assert EntityExtractor._should_retry_methods([], [failed_call], [{"label": "MerLin"}], scan)

    def test_call_diagnostics_include_failed_excerpt(self):
        failed_call = ParsedLLMResponse(
            data={"concepts": [], "methods": []},
            parse_quality="failed",
            raw_text="LLM call failed: 422 bad request response_body={\"detail\":\"unknown field\"}",
        )
        semantic = ParsedLLMResponse(data={}, parse_quality="failed", raw_text="empty response")

        diagnostics = EntityExtractor._call_diagnostics([failed_call], semantic, claims_pass=None)

        assert diagnostics[0]["raw_excerpt"].startswith("LLM call failed")
        assert diagnostics[-1]["raw_excerpt"] == "empty response"

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
        assert extraction_failure_reason(result)

    def test_failed_llm_result_is_not_linker_enriched(self):
        """Catastrophic model failures should not be rescued into KG concepts."""
        mock_router = FakeLLMRouter()
        mock_router.chat = MagicMock(side_effect=RuntimeError("No models loaded"))
        pipeline = ExtractionPipeline(mock_router)

        result = pipeline.process(
            "paper_001",
            "Machine Learning\n\nAbstract\nThis paper discusses machine learning.",
            link_concepts=True,
        )

        assert extraction_failure_reason(result)
        assert result.concepts == []

    def test_paper_title_from_text_skips_pdf_header_noise(self):
        text = "\n".join(
            [
                "Preprint",
                "Draft version February 20, 2026",
                "AI-Based Clinical Decision Support for Primary Care: A Real-World Study",
                "Abstract",
            ]
        )

        assert EntityExtractor._paper_title_from_text(text) == (
            "AI-Based Clinical Decision Support for Primary Care: A Real-World Study"
        )

    def test_paper_title_from_text_skips_template_and_conference_headers(self):
        text = "\n".join(
            [
                "Springer Nature 2021 LATEX template",
                "Learning Curves for Decision Making in Supervised Machine Learning: A Survey",
                "Abstract",
            ]
        )
        assert EntityExtractor._paper_title_from_text(text) == (
            "Learning Curves for Decision Making in Supervised Machine Learning: A Survey"
        )

        conference_text = "\n".join(
            [
                "18th CONTECSI - INTERNATIONAL CONFERENCE ON INFORMATION SYSTEMS AND TECHNOLOGY MANAGEMENT",
                "IT Enabling Factors in a New Industry Design: Open Banking and Digital Economy",
                "Abstract",
            ]
        )
        assert EntityExtractor._paper_title_from_text(conference_text) == (
            "IT Enabling Factors in a New Industry Design: Open Banking and Digital Economy"
        )

    def test_chunked_parse_quality_treats_mixed_chunk_failure_as_partial(self):
        calls = [
            ParsedLLMResponse(data={"concepts": []}, parse_quality="clean", raw_text="{}"),
            ParsedLLMResponse(data={}, parse_quality="failed", raw_text="LLM call failed"),
        ]

        assert EntityExtractor._chunked_parse_quality(calls) == "partial"

    def test_extract_truncates_long_text(self):
        """Test extraction truncates very long paper text."""
        mock_router = FakeLLMRouter()
        extractor = EntityExtractor(mock_router, quality_db_path=None)

        # Create text > extractor text budget
        long_text = "x" * 90000

        extractor.extract("paper_001", long_text)

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
                {
                    "label": "Data Sources",
                    "context": "Repeated phrase in parsed paper text (53 mentions).",
                    "confidence": 0.9,
                    "auto_detected": True,
                },
                {
                    "label": "For Official Statistics",
                    "context": "context of the paper | Section or heading: FOR OFFICIAL STATISTICS",
                    "confidence": 0.9,
                    "auto_detected": True,
                },
                {
                    "label": "Statistics Flanders",
                    "context": "Section or heading: Statistics Flanders",
                    "confidence": 0.74,
                    "auto_detected": True,
                },
                {
                    "label": "Concept Drift",
                    "context": "Concept drift is related to changes in the data distribution.",
                    "confidence": 0.72,
                    "auto_detected": True,
                },
            ],
            title="Emotion in Reinforcement Learning Agents and Robots",
        )

        labels = [concept["label"] for concept in concepts]
        assert "Learning Agents And Robots" not in labels
        assert "Reward Modi" not in labels
        assert labels.count("Questionnaire") == 1
        assert "Low Signal Concept" not in labels
        assert "Reward Shaping" in labels
        assert "Data Sources" not in labels
        assert "For Official Statistics" not in labels
        assert "Statistics Flanders" not in labels
        assert "Concept Drift" in labels

    def test_candidate_only_rejects_merged_heading_and_repeated_phrase_artifacts(self):
        candidates = EntityExtractor._candidate_only(
            "Changing Data Sources in the Age of Machine Learning for Official Statistics\n"
            "Concept drift is related to changes in the data distribution.",
            [
                {
                    "label": "Data Sources",
                    "context": "Repeated phrase in parsed paper text (53 mentions).",
                    "confidence": 0.9,
                    "auto_detected": True,
                },
                {
                    "label": "For Official Statistics",
                    "context": "context of the paper | Section or heading: FOR OFFICIAL STATISTICS",
                    "confidence": 0.9,
                    "auto_detected": True,
                    "evidence_role": "environment",
                },
                {
                    "label": "Statistics Flanders",
                    "context": "Section or heading: Statistics Flanders",
                    "confidence": 0.74,
                    "auto_detected": True,
                },
                {
                    "label": "Concept Drift",
                    "context": "Concept drift is related to changes in the data distribution.",
                    "confidence": 0.9,
                },
            ],
            [],
            default_role="possible_concept",
        )

        labels = {candidate["label"] for candidate in candidates}
        assert labels == {"Concept Drift"}

    def test_candidate_only_rejects_zero_mention_deterministic_method_candidates(self):
        candidates = EntityExtractor._candidate_only(
            "Risk Analysis is discussed as a mitigation step. Monitoring is also important.",
            [
                {
                    "label": "Data Pipeline Monitoring",
                    "description": "To mitigate these risks, data pipelines should monitor incoming data frequency.",
                    "confidence": 0.74,
                    "candidate_source": "deterministic_scan",
                    "auto_detected": True,
                },
                {
                    "label": "Risk Analysis",
                    "description": "Risk analysis is discussed as a mitigation step.",
                    "confidence": 0.74,
                    "candidate_source": "deterministic_scan",
                    "auto_detected": True,
                },
            ],
            [],
            default_role="method_candidate",
        )

        labels = {candidate["label"] for candidate in candidates}
        assert labels == {"Risk Analysis"}

    def test_candidate_only_keeps_zero_mention_deterministic_concept_candidates(self):
        candidates = EntityExtractor._candidate_only(
            "The statistical offering can be discontinued when data sources disappear.",
            [
                {
                    "label": "Data Source Discontinuation",
                    "context": "discontinuation of the statistical offering.",
                    "confidence": 0.52,
                    "candidate_source": "deterministic_scan",
                    "auto_detected": True,
                }
            ],
            [],
            default_role="possible_concept",
        )

        labels = {candidate["label"] for candidate in candidates}
        assert labels == {"Data Source Discontinuation"}

    def test_reference_section_candidates_need_body_support(self):
        text = """
        A survey of model monitoring

        Concept drift affects deployed systems. Concept drift changes error rates.

        # References
        Benchmark study for Flemish Twitter sentiment analysis.
        Stop explaining black box machine learning models.
        """.strip()

        candidates = EntityExtractor._candidate_only(
            text,
            [
                {
                    "label": "Twitter Sentiment Analysis",
                    "section": "References",
                    "evidence_span": "Benchmark study for Flemish Twitter sentiment analysis",
                    "confidence": 0.8,
                    "salience": "background",
                },
                {
                    "label": "Concept Drift",
                    "section": "References",
                    "evidence_span": "performance-aware drift detectors for concept drift",
                    "confidence": 0.8,
                    "salience": "supporting",
                },
            ],
            [],
            default_role="possible_concept",
        )

        labels = {candidate["label"] for candidate in candidates}
        assert labels == {"Concept Drift"}

    def test_accept_methods_rejects_bibliography_only_methods(self):
        text = """
        A survey of robust statistics

        We discuss monitoring and risk analysis in official statistics.

        References
        Topic modelling applied on innovation studies.
        """.strip()

        accepted = EntityExtractor._accept_methods(
            text,
            [
                {
                    "label": "Topic Modelling",
                    "section": "References",
                    "evidence_span": "Topic modelling applied on innovation studies",
                    "confidence": 0.9,
                    "salience": "supporting",
                    "source_type": "reviewed_method",
                }
            ],
            paper_type_hint="survey",
        )

        assert accepted == []

    def test_text_before_references_handles_markdown_reference_heading(self):
        text = "Title\n\nBody concept.\n\n# References\nReference title concept."

        body = EntityExtractor._text_before_references(text)

        assert "Body concept" in body
        assert "Reference title concept" not in body

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
        assert "claim_type" in CLAIMS_EXTRACTION_PROMPT

    def test_claim_merge_marks_limitations_without_logical_negation(self):
        claims = EntityExtractor._merge_claim_lists(
            [
                {
                    "statement": "The binary classification setting on SST2 appears too simple to draw definitive conclusions about quantum utility.",
                    "evidence_type": "experimental",
                    "negated": True,
                    "attributed_to": "this_paper",
                },
                {
                    "statement": "The model does not improve over the baseline.",
                    "evidence_type": "experimental",
                    "negated": False,
                    "attributed_to": "this_paper",
                },
                {
                    "statement": "Photon-native models can substitute gate-based models without significant loss in learning performance.",
                    "claim_type": "finding",
                    "evidence_type": "experimental",
                    "negated": True,
                    "attributed_to": "this_paper",
                },
            ]
        )

        assert claims[0]["claim_type"] == "limitation"
        assert claims[0]["negated"] is False
        assert claims[1]["claim_type"] == "negative_result"
        assert claims[1]["negated"] is True
        assert claims[2]["claim_type"] == "finding"
        assert claims[2]["negated"] is False

    def test_terminology_conflicts_drop_unanchored_generic_fillers(self):
        conflicts = EntityExtractor._merge_terminology_conflicts(
            [
                {
                    "term": "policy",
                    "this_field": "Not explicitly defined in this paper",
                    "other_field": "Reinforcement learning action-selection rule",
                },
                {
                    "term": "model",
                    "this_field": "A photonic quantum circuit or hybrid architecture",
                    "other_field": "Classical statistical model",
                },
                {
                    "term": "value",
                    "this_field": "Expected return estimate",
                    "other_field": "Normative worth",
                },
            ],
            [],
        )

        filtered = EntityExtractor._filter_terminology_conflicts(
            conflicts,
            [{"label": "Variational Quantum Circuit"}],
        )
        anchored = EntityExtractor._filter_terminology_conflicts(
            conflicts,
            [{"label": "Value function", "canonical_label": "Value function"}],
        )

        assert filtered == []
        assert [item["term"] for item in anchored] == ["value"]

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
        assert "Data Pipeline Monitoring" not in method_labels
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

    def test_repeated_rl_emotion_phrase_filter_blocks_generic_learning_agents(self):
        text = (
            "Learning agents can be useful. Learning agents are discussed. "
            "Reward shaping improves learning. Reward shaping appears again."
        )

        phrases = EntityExtractor._repeated_rl_emotion_phrases(text)

        assert "Learning Agents" not in phrases
        assert "Reward Shaping" in phrases


def test_entity_extractor_auto_policy_uses_whole_context_when_it_fits():
    response = {
        "paper_type": "research",
        "concepts": [
            {
                "label": "Adaptive Control",
                "entity_type": "Algorithm",
                "context": "robotics control loop",
                "confidence": 0.92,
            }
        ],
        "methods": [
            {
                "label": "Adaptive Control",
                "entity_type": "Algorithm",
                "domain": "robotics",
                "description": "adapts a controller online",
                "source_type": "paper_contribution",
            }
        ],
        "concept_candidates": [],
        "method_candidates": [],
        "relations": [],
        "claims": [],
        "cross_domain_hints": [],
        "terminology_conflicts": [],
        "temporal_coverage": {},
        "mathematical_content": {"has_formulas": False, "formula_types": []},
        "language_detected": "en",
    }
    llm = FakeLLMRouter(response_json=response)
    extractor = EntityExtractor(llm, quality_db_path=None)

    result = extractor.extract(
        "paper-context-auto",
        "Title: Adaptive Control\n\nAdaptive Control improves robotics systems.",
        overrides={"context_policy": "auto", "context_size": 20000, "max_tokens": 1000, "extraction_mode": "quick"},
    )

    diagnostics = result.extraction_diagnostics["context_diagnostics"]
    assert diagnostics["context_policy"] == "auto"
    assert diagnostics["whole_context_used"] is True
    assert diagnostics["chunk_count"] == 1


def test_entity_extractor_whole_policy_fails_clearly_without_fallback():
    llm = FakeLLMRouter()
    extractor = EntityExtractor(llm, quality_db_path=None)
    text = "Title: Large Paper\n\n" + ("Adaptive Control is discussed in detail. " * 1500)

    result = extractor.extract(
        "paper-context-too-small",
        text,
        overrides={"context_policy": "whole", "context_size": 9000, "max_tokens": 1000, "extraction_mode": "quick"},
    )

    diagnostics = result.extraction_diagnostics["context_diagnostics"]
    assert result.extraction_diagnostics["fatal_llm_error"] is True
    assert "Whole-paper context does not fit" in result.extraction_diagnostics["failure_reason"]
    assert diagnostics["context_policy"] == "whole"
    assert diagnostics["whole_context_used"] is False
    assert diagnostics["fallback_reason"] == "context_budget_exceeded"
    assert llm.last_messages is None


# Run tests
if __name__ == "__main__":
    pytest.main([__file__, "-v"])
