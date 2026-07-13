import pytest
import json

from extraction.entity_extractor import (
    EntityExtractor,
    ExtractionResult,
)
from extraction.entity_linker import (
    EntityLinker,
    OpenAlexLinkageStrategy,
    ExtractionPipeline,
)
from extraction.embedding_engine import EmbeddingEngine
from extraction.ontology import CanonicalResolver, Ontology
from extraction.text_normalization import normalize_key, slugify_label

from tests.llm_fakes import FakeLLMRouter

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

    def test_entity_linker_builds_generic_context_relations(self):
        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {
                    "label": "AI Consult",
                    "entity_type": "System",
                    "context": "AI Consult is an LLM-based clinical decision support tool evaluated for reducing clinical errors.",
                    "evidence_span": "LLM-based clinical decision support tool",
                    "confidence": 0.95,
                    "salience": "central",
                    "evidence_role": "system",
                    "source_type": "paper_contribution",
                    "accepted": True,
                    "review_status": "approved",
                },
                {
                    "label": "Large Language Model",
                    "entity_type": "ModelArchitecture",
                    "context": "Large language models are used for clinical decision support.",
                    "evidence_span": "Large language models",
                    "confidence": 0.95,
                    "salience": "central",
                    "evidence_role": "model_architecture",
                    "source_type": "reviewed_method",
                    "accepted": True,
                    "review_status": "approved",
                },
                {
                    "label": "Clinical Error",
                    "entity_type": "DomainConcept",
                    "context": "diagnostic and treatment errors",
                    "evidence_span": "clinical errors",
                    "confidence": 0.95,
                    "salience": "central",
                    "evidence_role": "domain_concept",
                    "source_type": "paper_contribution",
                    "mention_count": 2,
                    "accepted": True,
                    "review_status": "approved",
                },
            ],
            methods=[],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"])
            for relation in result.relations
        }

        assert ("concept:ai-consult", "USES", "concept:large-language-model") in relation_triples
        assert ("concept:ai-consult", "USED_FOR", "concept:clinical-error") in relation_triples

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

    def test_entity_linker_keeps_detail_metrics_out_of_core_kg_layer(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[
                {
                    "label": "Temporal difference error",
                    "entity_type": "Metric",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "temporal difference error is used to derive emotions",
                },
                {
                    "label": "KL-divergence",
                    "entity_type": "Metric",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "derive model uncertainty from the KL-divergence",
                },
            ],
            concept_candidates=[
                {
                    "label": "L1 norm",
                    "entity_type": "Metric",
                    "confidence": 0.75,
                    "mention_count": 2,
                    "salience": "supporting",
                    "accepted": False,
                    "review_status": "pending",
                    "evidence_span": "absolute difference between current value and setpoint (i.e. the L1 norm)",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        concepts_by_id = {item["canonical_id"]: item for item in result.concepts}
        candidate_ids = {item["canonical_id"] for item in result.concept_candidates}

        assert concepts_by_id["concept:temporal-difference-error"]["accepted_for_kg_write"] is True
        assert concepts_by_id["concept:temporal-difference-error"]["kg_layer"] == "core"
        assert concepts_by_id["concept:kl-divergence"]["accepted_for_kg_write"] is False
        assert concepts_by_id["concept:kl-divergence"]["kg_layer"] == "detail"
        assert concepts_by_id["concept:kl-divergence"]["kg_block_reason"] == "detail_or_parameter_mention"
        assert "concept:l1-norm" in candidate_ids

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

    def test_canonical_resolver_approves_accepted_external_ids_over_llm_pending(self):
        resolver = CanonicalResolver(embedding_engine=EmbeddingEngine())

        result = resolver.resolve(
            {
                "label": "Fock space",
                "entity_type": "DomainConcept",
                "canonical_id": "concept:fock-space",
                "confidence": 0.95,
                "accepted": True,
                "review_status": "pending",
                "evidence_span": "operating directly in Fock space",
            }
        )
        rejected = resolver.resolve(
            {
                "label": "Noisy concept",
                "entity_type": "DomainConcept",
                "canonical_id": "concept:noisy-concept",
                "confidence": 0.95,
                "accepted": True,
                "review_status": "rejected",
            }
        )

        assert result["review_status"] == "approved"
        assert result["canonical_match"]["match_type"] in {"exact_alias", "external_id"}
        assert rejected["review_status"] == "rejected"

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

    def test_entity_linker_approves_accepted_custom_methods(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[],
            methods=[
                {
                    "label": "Doya neurotransmitter-RL mapping",
                    "entity_type": "MethodFamily",
                    "source_type": "reviewed_method",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "neurotransmitters and several reinforcement learning parameters",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        assert result.methods[0]["canonical_id"] == "method:doya-neurotransmitter-rl-mapping"
        assert result.methods[0]["review_status"] == "approved"
        assert result.methods[0]["acceptance_reason"] == "accepted_method_high_precision"

    def test_entity_linker_merges_survey_contribution_methods(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[],
            methods=[
                {
                    "label": "Survey Taxonomy",
                    "entity_type": "System",
                    "source_type": "paper_contribution",
                    "description": "Taxonomy categorizing emotion elicitation, type, and function",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "proposed taxonomy of emotion elicitation, type and function",
                },
                {
                    "label": "Emotion-RL Survey Framework",
                    "entity_type": "MethodFamily",
                    "source_type": "paper_contribution",
                    "description": "framework connecting emotion models and RL implementations",
                    "confidence": 0.78,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "establish such a framework",
                },
                {
                    "label": "Intrinsic motivation framework",
                    "entity_type": "MethodFamily",
                    "source_type": "paper_contribution",
                    "description": "structured according to the intrinsically motivated RL framework",
                    "confidence": 0.74,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "structured according to the intrinsically motivated RL framework",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        assert [method["label"] for method in result.methods] == ["Emotion in RL Survey Taxonomy"]
        assert result.methods[0]["canonical_id"] == "method:emotion-in-rl-survey-taxonomy"
        assert "Survey Taxonomy" in result.methods[0]["aliases"]
        assert result.methods[0]["review_status"] == "approved"

    def test_entity_linker_merges_author_year_method_duplicates(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[],
            methods=[
                {
                    "label": "Gadanho and Hallam (1998, 2001)",
                    "entity_type": "System",
                    "source_type": "reviewed_method",
                    "description": "Early RL system deriving emotions from homeostasis",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "One of the first RL systems deriving emotions from homeostasis",
                },
                {
                    "label": "Gadanho and Hallam emotion model",
                    "entity_type": "MethodFamily",
                    "source_type": "reviewed_method",
                    "description": "Homeostasis-based emotion elicitation modifying reward signals",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Homeostasis: hunger, pain, restlessness",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        assert [method["label"] for method in result.methods] == ["Gadanho and Hallam emotion model"]
        assert "Gadanho and Hallam (1998, 2001)" in result.methods[0]["aliases"]
        assert result.methods[0]["review_status"] == "approved"

    def test_entity_linker_normalizes_qml_aliases_and_builds_benchmark_relations(self):
        extraction = ExtractionResult(
            paper_id="arxiv:2602.11092",
            paper_type="benchmark",
            concepts=[
                {
                    "label": "MerLin",
                    "entity_type": "System",
                    "canonical_id": "method:merlin",
                    "confidence": 0.95,
                    "salience": "central",
                    "evidence_role": "method_family",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "We introduce MerLin, an open-source framework designed as a discovery engine.",
                },
                {
                    "label": "QuantumLayer",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.92,
                    "salience": "central",
                    "evidence_role": "method_family",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "MerLin's PyTorch integration is provided through QuantumLayer.",
                },
                {
                    "label": "QGANs",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.85,
                    "salience": "supporting",
                    "evidence_role": "method_family",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Quantum generative adversarial networks (QGANs) were among the first generative variational quantum algorithms.",
                },
                {
                    "label": "MNIST",
                    "entity_type": "Dataset",
                    "confidence": 0.9,
                    "salience": "supporting",
                    "evidence_role": "dataset",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "MNIST dataset",
                },
                {
                    "label": "Quantum Convolutional Neural Networks",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.88,
                    "salience": "supporting",
                    "evidence_role": "method_family",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "A photonic QCNN is reproduced using adaptive state injection for pooling.",
                },
                {
                    "label": "Adaptive state injection",
                    "entity_type": "MethodFamily",
                    "confidence": 0.86,
                    "salience": "supporting",
                    "evidence_role": "method_family",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "A photonic QCNN is reproduced using adaptive state injection for pooling.",
                },
            ],
            methods=[
                {
                    "label": "Strong Linear Optical Simulation",
                    "entity_type": "Algorithm",
                    "domain": "Photonic Quantum Computing",
                    "description": "Tool that performs strong simulation.",
                    "source_type": "reviewed_method",
                    "confidence": 0.9,
                    "salience": "central",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "SLOS framework, on which MerLin is built, performs strong simulation.",
                },
                {
                    "label": "Angle encoding",
                    "entity_type": "MethodFamily",
                    "domain": "Quantum Machine Learning",
                    "description": "Maps classical features to phase shifts.",
                    "source_type": "reviewed_method",
                    "confidence": 0.75,
                    "salience": "supporting",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Angle encoding proves substantially more robust than amplitude encoding.",
                },
                {
                    "label": "Amplitude encoding",
                    "entity_type": "MethodFamily",
                    "domain": "Quantum Machine Learning",
                    "description": "Initializes amplitudes to match input features.",
                    "source_type": "reviewed_method",
                    "confidence": 0.75,
                    "salience": "supporting",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Amplitude encoding is highly vulnerable to small adversarial perturbations.",
                },
                {
                    "label": "Quantum Generative Adversarial Network",
                    "entity_type": "ModelArchitecture",
                    "domain": "Quantum Machine Learning",
                    "description": "Generative variational quantum algorithm for image generation.",
                    "source_type": "reviewed_method",
                    "confidence": 0.75,
                    "salience": "supporting",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "QGAN reproduction on the MNIST dataset.",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        merlin = next(item for item in result.concepts if item["canonical_id"] == "concept:merlin")
        concepts_by_id = {
            item["canonical_id"]: item
            for item in result.concepts
            if item.get("canonical_id")
        }
        qgan_entities = [
            item
            for item in [*result.concepts, *result.methods]
            if item["canonical_id"] == "concept:quantum-generative-adversarial-network"
        ]
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"])
            for relation in result.relations
        }

        assert merlin["review_status"] == "approved"
        assert merlin["accepted_for_kg_write"] is True
        assert merlin["canonical_id"] == "concept:merlin"
        assert concepts_by_id["concept:adaptive-state-injection"]["accepted_for_kg_write"] is True
        assert len(qgan_entities) == 1
        assert "QGANs" in qgan_entities[0].get("aliases", [])
        assert ("concept:merlin", "BUILT_ON", "concept:strong-linear-optical-simulation") in relation_triples
        assert ("concept:merlin", "PROVIDES", "concept:quantumlayer") in relation_triples
        assert ("concept:quantumlayer", "SUPPORTS", "concept:angle-encoding") in relation_triples
        assert ("concept:quantumlayer", "SUPPORTS", "concept:amplitude-encoding") in relation_triples
        assert (
            "concept:merlin",
            "REPRODUCES",
            "concept:quantum-generative-adversarial-network",
        ) in relation_triples
        assert (
            "concept:quantum-generative-adversarial-network",
            "EVALUATED_ON",
            "concept:mnist",
        ) in relation_triples
        assert ("concept:angle-encoding", "MORE_ROBUST_THAN", "concept:amplitude-encoding") in relation_triples
        assert (
            "concept:merlin",
            "REPRODUCES",
            "concept:quantum-convolutional-neural-network",
        ) in relation_triples
        assert (
            "concept:quantum-convolutional-neural-network",
            "USES",
            "concept:adaptive-state-injection",
        ) in relation_triples

    def test_entity_linker_normalizes_medical_imaging_aliases_and_relations(self):
        extraction = ExtractionResult(
            paper_id="arxiv:2503.17786",
            paper_type="research",
            concepts=[
                {
                    "label": "Unruptured intracranial aneurysms",
                    "entity_type": "Phenomenon",
                    "confidence": 0.95,
                    "salience": "central",
                    "evidence_role": "domain_concept",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "unruptured intracranial aneurysms are detected in TOF-MRA scans",
                    "mention_count": 2,
                },
                {
                    "label": "Unruptured Intracranial Aneurysm",
                    "entity_type": "Phenomenon",
                    "confidence": 0.95,
                    "salience": "central",
                    "evidence_role": "domain_concept",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "UIAs detection in TOF-MRA scans",
                    "mention_count": 2,
                },
                {
                    "label": "Time-of-Flight Magnetic Resonance Angiography",
                    "entity_type": "Dataset",
                    "confidence": 0.9,
                    "salience": "central",
                    "evidence_role": "dataset",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Time-of-Flight Magnetic Resonance Angiography is used for UIA detection",
                    "mention_count": 1,
                },
                {
                    "label": "TOF-MRA",
                    "entity_type": "Dataset",
                    "confidence": 0.9,
                    "salience": "central",
                    "evidence_role": "dataset",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "TOF-MRA scans",
                    "mention_count": 12,
                },
                {
                    "label": "Satisfaction-of-search effect",
                    "entity_type": "Phenomenon",
                    "confidence": 0.9,
                    "salience": "supporting",
                    "evidence_role": "domain_concept",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "satisfaction-of-search effect",
                    "mention_count": 1,
                },
                {
                    "label": "Satisfaction of Search",
                    "entity_type": "Phenomenon",
                    "confidence": 0.9,
                    "salience": "supporting",
                    "evidence_role": "domain_concept",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "satisfaction of search effect",
                    "mention_count": 4,
                },
                {
                    "label": "ADAM dataset",
                    "entity_type": "Dataset",
                    "confidence": 0.9,
                    "salience": "supporting",
                    "evidence_role": "dataset",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Aneurysm Detection And segMentation Challenge (ADAM)",
                    "mention_count": 4,
                },
                {
                    "label": "Computer-aided detection",
                    "entity_type": "System",
                    "confidence": 0.95,
                    "salience": "central",
                    "evidence_role": "method_family",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "computer-aided detection (CAD) tool",
                    "mention_count": 1,
                },
                {
                    "label": "3D UNET",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.9,
                    "salience": "central",
                    "evidence_role": "method",
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "custom 3D UNET trained for UIA detection on the ADAM dataset",
                    "mention_count": 2,
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        concepts_by_id = {concept["canonical_id"]: concept for concept in result.concepts}
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"])
            for relation in result.relations
        }

        assert sum(1 for item in result.concepts if item["canonical_id"] == "concept:unruptured-intracranial-aneurysm") == 1
        assert sum(1 for item in result.concepts if item["canonical_id"] == "concept:tof-mra") == 1
        assert sum(1 for item in result.concepts if item["canonical_id"] == "concept:satisfaction-of-search") == 1
        assert concepts_by_id["concept:tof-mra"]["entity_type"] == "MethodFamily"
        assert "Time-of-Flight Magnetic Resonance Angiography" in concepts_by_id["concept:tof-mra"]["aliases"]
        assert ("concept:computer-aided-detection", "USES", "concept:3d-u-net") in relation_triples
        assert ("concept:3d-u-net", "EVALUATED_ON", "concept:adam-dataset") in relation_triples

    def test_qml_deterministic_scan_backfills_datasets_and_architecture_components(self):
        scan = EntityExtractor._scan_paper_text(
            """
            MerLin is a photonic quantum machine learning benchmark framework.
            Its photonic QCNN uses adaptive state injection and reports results
            on CIFAR-10, MNIST, and SST2 sentiment analysis.
            """
        )

        labels = {item["label"] for item in scan.concepts}

        assert "CIFAR-10" in labels
        assert "Adaptive state injection" in labels
        assert "Quantum Convolutional Neural Network" in labels

    def test_qml_exact_candidates_promote_for_benchmark_papers(self):
        extraction = ExtractionResult(
            paper_id="arxiv:2602.11092",
            paper_type="benchmark",
            paper_node={
                "title": "MerLin: A Discovery Engine for Photonic and Hybrid Quantum Machine Learning"
            },
            concepts=[],
            concept_candidates=[
                {
                    "label": "Linear-optical circuits",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.74,
                    "mention_count": 2,
                    "salience": "supporting",
                    "context": "linear-optical circuits into standard PyTorch",
                },
                {
                    "label": "Photonic Quantum Machine Learning",
                    "entity_type": "MethodFamily",
                    "confidence": 0.52,
                    "mention_count": 1,
                    "salience": "passing",
                    "context": "photonic QML exploits the bosonic nature of light",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        concept_ids = {item["canonical_id"] for item in result.concepts}
        candidate_ids = {item["canonical_id"] for item in result.concept_candidates}

        assert "concept:linear-optical-circuits" in concept_ids
        assert "concept:photonic-quantum-machine-learning" in concept_ids
        assert "concept:linear-optical-circuits" not in candidate_ids
        assert "concept:photonic-quantum-machine-learning" not in candidate_ids

    def test_approved_relations_to_detail_entities_are_downgraded(self):
        extraction = ExtractionResult(
            paper_id="paper_001",
            paper_type="research",
            concepts=[
                {
                    "label": "KL-divergence",
                    "entity_type": "Metric",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "approved",
                    "evidence_span": "KL-divergence measures model uncertainty",
                },
                {
                    "label": "Model uncertainty",
                    "entity_type": "DomainConcept",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "approved",
                    "evidence_span": "KL-divergence measures model uncertainty",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation = next(
            item
            for item in result.relations
            if item["subject_id"] == "concept:kl-divergence"
            and item["relation_type"] == "MEASURES"
            and item["object_id"] == "concept:model-uncertainty"
        )

        assert relation["review_status"] == "pending"
        assert relation["kg_block_reason"] == "relation_endpoint_not_kg_writeable"

    def test_entity_linker_builds_fake_news_benchmark_relations(self):
        extraction = ExtractionResult(
            paper_id="arxiv:1905.04749",
            paper_type="benchmark",
            concepts=[
                {
                    "label": "BERT",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "source_type": "reviewed_method",
                    "evidence_span": "BERT was evaluated on the LIAR and Combined Corpus datasets.",
                },
                {
                    "label": "RoBERTa",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "source_type": "reviewed_method",
                    "evidence_span": "RoBERTa achieved the best accuracy on the Combined Corpus.",
                },
                {
                    "label": "DistilBERT",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "source_type": "reviewed_method",
                    "evidence_span": "DistilBERT is a distilled version of BERT using knowledge distillation.",
                },
                {
                    "label": "Pre-trained Language Models",
                    "entity_type": "MethodFamily",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "Advanced pre-trained language models outperform traditional models.",
                },
                {
                    "label": "Knowledge Distillation",
                    "entity_type": "MethodFamily",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "DistilBERT uses the concept of knowledge distillation.",
                },
                {
                    "label": "LIAR",
                    "entity_type": "Dataset",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "The LIAR benchmark dataset was used for model evaluation.",
                },
                {
                    "label": "Combined Corpus",
                    "entity_type": "Dataset",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "The Combined Corpus dataset was used for model evaluation.",
                },
                {
                    "label": "Accuracy",
                    "entity_type": "Metric",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "Accuracy is the primary performance metric.",
                },
            ],
            method_candidates=[
                {
                    "label": "AdaBoost",
                    "entity_type": "Algorithm",
                    "confidence": 0.8,
                    "source_type": "reviewed_method",
                    "salience": "supporting",
                    "mention_count": 4,
                    "evidence_span": "We also evaluated ensemble learning method like AdaBoost.",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        concept_ids = {item["canonical_id"] for item in result.concepts}
        method_ids = {item["canonical_id"] for item in result.methods}
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"], relation["review_status"])
            for relation in result.relations
        }

        assert "concept:accuracy" in concept_ids
        assert "concept:data-accuracy" not in concept_ids
        assert "concept:adaboost" in method_ids
        assert ("concept:bert", "IS_A", "concept:pre-trained-language-models", "approved") in relation_triples
        assert ("concept:distilbert", "DERIVED_FROM", "concept:bert", "approved") in relation_triples
        assert ("concept:distilbert", "USES", "concept:knowledge-distillation", "approved") in relation_triples
        assert ("concept:bert", "EVALUATED_ON", "concept:liar", "approved") in relation_triples
        assert ("concept:roberta", "EVALUATED_ON", "concept:combined-corpus", "approved") in relation_triples
        assert ("concept:adaboost", "EVALUATED_ON", "concept:combined-corpus", "approved") in relation_triples

    def test_fake_news_deterministic_scan_backfills_clstm(self):
        scan = EntityExtractor._scan_paper_text(
            """
            A benchmark study of machine learning models for online fake news detection.
            The C-LSTM baseline and BERT were evaluated on the LIAR dataset.
            """
        )

        labels = {item["label"] for item in scan.concepts}

        assert "C-LSTM" in labels

    def test_theoretical_scan_backfills_synesthesia_mapping_terms(self):
        scan = EntityExtractor._scan_paper_text(
            """
            The neurobiology paper discusses synesthesia and cross-domain mappings
            as a possible explanation for unusual conceptual associations.
            """
        )
        labels = {item["label"] for item in scan.concepts}

        assert "Synesthesia" in labels
        assert "Cross-domain mapping" in labels

    def test_entity_linker_approves_gemini_merlin_core_entities(self):
        extraction = ExtractionResult(
            paper_id="arxiv:2602.11092",
            paper_type="benchmark",
            concepts=[
                {
                    "label": "Fock space",
                    "entity_type": "DomainConcept",
                    "canonical_id": "concept:fock-space",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_role": "domain_concept",
                    "mention_count": 9,
                    "evidence_span": "operating directly in Fock space",
                },
                {
                    "label": "QuantumLayer",
                    "entity_type": "ModelArchitecture",
                    "canonical_id": "concept:quantumlayer",
                    "confidence": 0.98,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_role": "model_architecture",
                    "mention_count": 5,
                    "evidence_span": "QuantumLayer, a torch.nn.Module that exposes trainable circuit parameters",
                },
                {
                    "label": "Angle encoding",
                    "entity_type": "MethodFamily",
                    "canonical_id": "concept:angle-encoding",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_role": "method_family",
                    "mention_count": 3,
                    "evidence_span": "angle encoding maps classical features to phase shifts",
                },
                {
                    "label": "Amplitude encoding",
                    "entity_type": "MethodFamily",
                    "canonical_id": "concept:amplitude-encoding",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_role": "method_family",
                    "mention_count": 7,
                    "evidence_span": "amplitude encoding initializes the quantum state amplitudes",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        concepts_by_id = {concept["canonical_id"]: concept for concept in result.concepts}

        assert concepts_by_id["concept:fock-space"]["review_status"] == "approved"
        assert concepts_by_id["concept:fock-space"]["accepted_for_kg_write"] is True
        assert concepts_by_id["concept:quantumlayer"]["review_status"] == "approved"
        assert concepts_by_id["concept:quantumlayer"]["accepted_for_kg_write"] is True
        assert concepts_by_id["concept:angle-encoding"]["review_status"] == "approved"
        assert concepts_by_id["concept:amplitude-encoding"]["review_status"] == "approved"

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
                {
                    "label": "Value function",
                    "entity_type": "DomainConcept",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Q-learning iteratively approximates the value-function",
                },
                {
                    "label": "Learning efficiency",
                    "entity_type": "Metric",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Most authors in emotion-RL research have focussed on learning efficiency",
                },
                {
                    "label": "Homeostasis",
                    "entity_type": "DomainConcept",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "homeostatic variables can elicit categorical emotions",
                },
                {
                    "label": "Categorical emotion",
                    "entity_type": "DomainConcept",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "homeostatic variables can elicit categorical emotions",
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
                {
                    "label": "Reward shaping",
                    "entity_type": "MethodFamily",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "reward shaping can improve learning efficiency",
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
        assert ("concept:q-learning", "IMPLEMENTS", "concept:value-function") in relation_triples
        assert ("concept:reward-shaping", "USED_FOR", "concept:learning-efficiency") in relation_triples
        assert ("concept:homeostasis", "ELICITS", "concept:categorical-emotion") in relation_triples
        assert ("concept:q-learning", "IS_A", "concept:reinforcement-learning") not in relation_triples
        assert (
            "concept:boltzmann-action-selection",
            "MODULATED_BY",
            "concept:valence",
        ) in relation_triples

    def test_entity_linker_builds_official_statistics_risk_relations(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[
                {
                    "label": "Official Statistics",
                    "entity_type": "ApplicationSetting",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "production of official statistics",
                },
                {
                    "label": "External Data Sources",
                    "entity_type": "DomainConcept",
                    "confidence": 0.92,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "exploiting external data sources to power novel statistics",
                },
                {
                    "label": "Concept drift",
                    "entity_type": "Phenomenon",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "Concept drift is related to changes in the data distribution between train and test time",
                },
                {
                    "label": "Feature Mismatch",
                    "entity_type": "Phenomenon",
                    "confidence": 0.88,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "induce feature mismatches between train and inference time",
                },
                {
                    "label": "Model staleness",
                    "entity_type": "Phenomenon",
                    "confidence": 0.92,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "When a model becomes outdated, it no longer reflects current trends",
                },
                {
                    "label": "Data Source Discontinuation",
                    "entity_type": "Phenomenon",
                    "confidence": 0.7,
                    "accepted": False,
                    "review_status": "pending",
                    "salience": "supporting",
                    "canonical_id": "concept:data-source-discontinuation",
                    "canonical_label": "Data Source Discontinuation",
                    "evidence_span": "discontinuation of the statistical offering",
                },
            ],
            methods=[
                {
                    "label": "Risk analysis",
                    "entity_type": "MethodFamily",
                    "domain": "Data Engineering",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "source_type": "paper_contribution",
                    "evidence_span": "Performing a risk analysis before incorporating a new data source is an essential step",
                },
                {
                    "label": "Monitoring",
                    "entity_type": "MethodFamily",
                    "domain": "Data Engineering",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "source_type": "paper_contribution",
                    "evidence_span": "Monitoring everything that is relevant is another crucial step in mitigating the impact",
                },
                {
                    "label": "Diversification",
                    "entity_type": "MethodFamily",
                    "domain": "Data Engineering",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "source_type": "paper_contribution",
                    "evidence_span": "Diversifying data sources is another important measure",
                },
            ],
            concept_candidates=[
                {
                    "label": "Data Source Changes",
                    "entity_type": "Phenomenon",
                    "confidence": 0.52,
                    "accepted": False,
                    "review_status": "pending",
                    "salience": "passing",
                    "evidence_span": "Changing data sources can impact official statistics",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"])
            for relation in result.relations
        }
        relation_status = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"]): relation["review_status"]
            for relation in result.relations
        }

        assert ("concept:external-data-sources", "LEADS_TO", "concept:concept-drift") in relation_triples
        assert ("concept:data-source-changes", "LEADS_TO", "concept:concept-drift") in relation_triples
        assert ("concept:concept-drift", "CAUSES", "concept:model-staleness") in relation_triples
        assert ("method:risk-analysis", "MITIGATES", "concept:feature-mismatch") in relation_triples
        assert ("method:monitoring", "MITIGATES", "concept:concept-drift") in relation_triples
        assert ("method:diversification", "PREVENTS", "concept:data-source-discontinuation") in relation_triples
        assert (
            relation_status[("method:diversification", "PREVENTS", "concept:data-source-discontinuation")]
            == "pending"
        )
        assert relation_status[("concept:data-source-changes", "LEADS_TO", "concept:concept-drift")] == "pending"

    def test_entity_linker_promotes_ontology_title_theme_candidate(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            paper_node={
                "title": "Changing Data Sources in the Age of Machine Learning for Official Statistics",
            },
            concept_candidates=[
                {
                    "label": "Data Source Changes",
                    "entity_type": "Phenomenon",
                    "confidence": 0.52,
                    "accepted": False,
                    "review_status": "pending",
                    "salience": "passing",
                    "evidence_span": "CHANGING DATA SOURCES IN THE AGE OF",
                    "mention_count": 1,
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        data_source_changes = next(
            concept for concept in result.concepts if concept["canonical_id"] == "concept:data-source-changes"
        )
        assert data_source_changes["review_status"] == "approved"
        assert data_source_changes["accepted_for_kg_write"] is True
        assert result.concept_candidates == []

    def test_entity_linker_promotes_central_machine_learning_survey_candidate(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concept_candidates=[
                {
                    "label": "Machine Learning",
                    "entity_type": "MethodFamily",
                    "context": "Machine Learning is central to the survey.",
                    "evidence_span": "rise of machine learning has transformed the field of statistics",
                    "confidence": 0.95,
                    "mention_count": 12,
                    "salience": "central",
                    "evidence_role": "method_family",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        machine_learning = next(concept for concept in result.concepts if concept["canonical_id"] == "concept:machine-learning")
        assert machine_learning["review_status"] == "approved"
        assert machine_learning["accepted_for_kg_write"] is True
        assert result.concept_candidates == []

    def test_entity_linker_filters_generic_background_algorithm_used_in_relations(self):
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
                }
            ],
            method_candidates=[
                {
                    "label": "Evolutionary Algorithms",
                    "entity_type": "Algorithm",
                    "domain": "Machine Learning",
                    "confidence": 0.62,
                    "salience": "passing",
                    "source_type": "background",
                    "accepted": False,
                    "review_status": "pending",
                    "canonical_id": "concept:evolutionary-algorithms",
                    "evidence_span": "biological principles, such as neural networks, evolutionary algorithms and swarm-based optimization",
                    "description": "Biologically inspired optimization method mentioned as advancement",
                    "section": "1 Introduction",
                },
                {
                    "label": "Swarm-based Optimization",
                    "entity_type": "Algorithm",
                    "domain": "Machine Learning",
                    "confidence": 0.62,
                    "salience": "passing",
                    "source_type": "background",
                    "accepted": False,
                    "review_status": "pending",
                    "canonical_id": "concept:swarm-based-optimization",
                    "evidence_span": "biological principles, such as evolutionary algorithms and swarm-based optimization",
                    "description": "Biologically inspired optimization method mentioned as advancement",
                    "section": "1 Introduction",
                },
                {
                    "label": "Tsankova (2002) frustration switching",
                    "entity_type": "Algorithm",
                    "domain": "navigation tasks",
                    "confidence": 0.65,
                    "salience": "background",
                    "source_type": "reviewed_method",
                    "accepted": False,
                    "review_status": "pending",
                    "canonical_id": "concept:tsankova-2002-frustration-switching",
                    "evidence_span": "Tsankova (2002) and Hasson et al (2011) use a high frustration to switch between behaviour",
                    "description": "Uses high frustration to trigger switching between behavioral sets",
                    "section": "6.4",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"])
            for relation in result.relations
        }

        assert (
            "concept:evolutionary-algorithms",
            "USED_IN",
            "concept:reinforcement-learning",
        ) not in relation_triples
        assert (
            "concept:swarm-based-optimization",
            "USED_IN",
            "concept:reinforcement-learning",
        ) not in relation_triples
        assert (
            "concept:tsankova-2002-frustration-switching",
            "USED_IN",
            "concept:reinforcement-learning",
        ) in relation_triples

    def test_entity_linker_builds_pending_relations_from_review_candidates(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[],
            methods=[
                {
                    "label": "Reward shaping",
                    "entity_type": "MethodFamily",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "reward shaping",
                }
            ],
            method_candidates=[
                {
                    "label": "Appraisal-based reward modulation",
                    "entity_type": "MethodFamily",
                    "confidence": 0.76,
                    "accepted": False,
                    "review_status": "pending",
                    "evidence_span": "Similar ideas are used for appraisal-based reward modifications.",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation = next(
            item
            for item in result.relations
            if item["subject_id"] == "concept:appraisal-based-reward-modification"
        )

        assert relation["relation_type"] == "IS_A"
        assert relation["object_id"] == "concept:reward-shaping"
        assert relation["review_status"] == "pending"
        assert relation["source"] == "candidate_relation"

    def test_entity_linker_filters_candidates_shadowed_by_accepted_canonical_entities(self):
        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {
                    "label": "Extrinsic reward",
                    "entity_type": "DomainConcept",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Extrinsic reward is related to external resources.",
                }
            ],
            concept_candidates=[
                {
                    "label": "Extrinsic motivation",
                    "entity_type": "DomainConcept",
                    "confidence": 0.86,
                    "accepted": False,
                    "review_status": "pending",
                    "evidence_span": "extrinsic motivation parallels homeostasis",
                },
                {
                    "label": "Human-robot interaction",
                    "entity_type": "ApplicationSetting",
                    "confidence": 0.9,
                    "accepted": False,
                    "review_status": "pending",
                    "evidence_span": "human-robot interaction community",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        candidate_ids = {item["canonical_id"] for item in result.concept_candidates}
        assert "concept:extrinsic-motivation" not in candidate_ids
        assert next(item for item in result.concept_candidates if item["canonical_id"] == "concept:human-robot-interaction")[
            "review_status"
        ] == "pending"

    def test_entity_linker_builds_neurotransmitter_parameter_relations(self):
        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {
                    "label": "Serotonin",
                    "entity_type": "Phenomenon",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "serotonin and the discount factor",
                },
                {
                    "label": "Discount factor",
                    "entity_type": "Metric",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "serotonin and the discount factor",
                },
                {
                    "label": "Noradrenaline",
                    "entity_type": "Phenomenon",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "noradrenaline and the Boltzmann action selection temperature",
                },
                {
                    "label": "Boltzmann action selection temperature",
                    "entity_type": "Metric",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "noradrenaline and the Boltzmann action selection temperature",
                },
                {
                    "label": "Acetylcholine",
                    "entity_type": "Phenomenon",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "acetylcholine and the learning rate",
                },
                {
                    "label": "Learning rate",
                    "entity_type": "Metric",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "acetylcholine and the learning rate",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"])
            for relation in result.relations
        }

        assert ("concept:serotonin", "CORRESPONDS_TO", "concept:discount-factor") in relation_triples
        assert (
            "concept:noradrenaline",
            "CORRESPONDS_TO",
            "concept:boltzmann-action-selection-temperature",
        ) in relation_triples
        assert ("concept:acetylcholine", "CORRESPONDS_TO", "concept:learning-rate") in relation_triples

    def test_entity_linker_promotes_relation_endpoint_candidates(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[
                {
                    "label": "Homeostasis",
                    "entity_type": "DomainConcept",
                    "confidence": 0.92,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "homeostasis derives categorical emotions from internal variables",
                },
                {
                    "label": "Bayesian Affect Control Theory",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.92,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Bayesian Affect Control Theory is a POMDP variant",
                },
                {
                    "label": "Markov Decision Process",
                    "entity_type": "DomainConcept",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Markov Decision Process formalization",
                },
                {
                    "label": "Reinforcement Learning",
                    "entity_type": "MethodFamily",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "reinforcement learning survey",
                },
                {
                    "label": "Transition model",
                    "entity_type": "DomainConcept",
                    "confidence": 0.8,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "transition model is learned in model-based RL",
                },
            ],
            concept_candidates=[
                {
                    "label": "Categorical emotion",
                    "entity_type": "DomainConcept",
                    "confidence": 0.62,
                    "salience": "supporting",
                    "accepted": False,
                    "review_status": "pending",
                    "evidence_span": "homeostasis derives categorical emotions",
                }
            ],
            method_candidates=[
                {
                    "label": "POMDP",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.60,
                    "mention_count": 2,
                    "accepted": False,
                    "review_status": "pending",
                    "source_type": "background",
                    "evidence_span": "POMDP variant called Bayesian Affect Control Theory",
                },
                {
                    "label": "Model-based RL",
                    "entity_type": "MethodFamily",
                    "confidence": 0.70,
                    "mention_count": 1,
                    "accepted": False,
                    "review_status": "pending",
                    "source_type": "reviewed_method",
                    "evidence_span": "Model-based RL approximates a transition model",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        concept_ids = {item["canonical_id"] for item in result.concepts}
        method_ids = {item["canonical_id"] for item in result.methods}
        candidate_ids = {
            item["canonical_id"]
            for item in [*result.concept_candidates, *result.method_candidates]
        }
        relation_triples = {
            (relation["subject_id"], relation["relation_type"], relation["object_id"], relation["review_status"])
            for relation in result.relations
        }

        assert "concept:categorical-emotion" in concept_ids
        assert "concept:pomdp" in concept_ids
        assert "concept:model-based-rl" in method_ids
        assert "concept:categorical-emotion" not in candidate_ids
        assert ("concept:homeostasis", "ELICITS", "concept:categorical-emotion", "approved") in relation_triples
        assert ("concept:bayesian-affect-control-theory", "EXTENDS", "concept:pomdp", "approved") in relation_triples
        assert ("concept:model-based-rl", "USES", "concept:transition-model", "approved") in relation_triples

    def test_categorical_emotions_alias_resolves_to_domain_concept(self):
        resolver = CanonicalResolver(embedding_engine=EmbeddingEngine())

        result = resolver.resolve(
            {
                "label": "Categorical emotions",
                "entity_type": "DomainConcept",
                "confidence": 0.9,
                "accepted": True,
            }
        )

        assert result["canonical_id"] == "concept:categorical-emotion"

    def test_entity_linker_repairs_zero_mention_count_from_alias_evidence(self):
        extraction = ExtractionResult(
            paper_id="p1",
            methods=[
                {
                    "label": "Model-based reinforcement learning",
                    "entity_type": "MethodFamily",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "mention_count": 0,
                    "evidence_span": "Model-based RL is a hybrid form of planning and sampling.",
                }
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)

        assert result.methods[0]["mention_count"] == 1

    def test_relation_evidence_avoids_generic_theory_word_matches(self):
        extraction = ExtractionResult(
            paper_id="p1",
            concepts=[
                {
                    "label": "Appraisal dimensions",
                    "entity_type": "DomainConcept",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "appraisal dimensions include novelty and valence",
                },
                {
                    "label": "Cognitive appraisal theory",
                    "entity_type": "Theory",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "cognitive appraisal theory evaluates incoming stimuli",
                },
                {
                    "label": "Dimensional emotion theory",
                    "entity_type": "Theory",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "Dimensional emotion theory assumes an affective space with dimensions",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation = next(
            item
            for item in result.relations
            if item["subject_id"] == "concept:appraisal-dimensions"
            and item["object_id"] == "concept:appraisal-theory"
        )

        assert "Dimensional emotion theory" not in relation["evidence_span"]

    def test_entity_linker_prefers_relation_specific_evidence(self):
        extraction = ExtractionResult(
            paper_id="p1",
            paper_type="survey",
            concepts=[
                {
                    "label": "Valence",
                    "entity_type": "DomainConcept",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "The most implemented dimension is valence.",
                }
            ],
            methods=[
                {
                    "label": "Boltzmann action selection",
                    "entity_type": "Algorithm",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "β parameter in a Boltzmann action selection mechanism",
                },
                {
                    "label": "Broekens valency-driven exploration",
                    "entity_type": "MethodFamily",
                    "source_type": "reviewed_method",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "evidence_span": "this valency directly influenced the β parameter in a Boltzmann action selection mechanism",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation = next(
            item
            for item in result.relations
            if item["relation_type"] == "MODULATED_BY"
        )

        assert "valency directly influenced" in relation["evidence_span"]

    def test_entity_linker_avoids_generic_quantum_network_evidence_for_qgan_relation(self):
        extraction = ExtractionResult(
            paper_id="arxiv:2602.11092",
            paper_type="benchmark",
            concepts=[
                {
                    "label": "MerLin",
                    "entity_type": "System",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_role": "method_family",
                    "evidence_span": "MerLin reproduces several photonic quantum machine learning benchmarks.",
                },
                {
                    "label": "Quantum Generative Adversarial Network",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.9,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_role": "method_family",
                    "evidence_span": "Quantum generative adversarial networks (QGANs) are reproduced on MNIST.",
                },
                {
                    "label": "Quantum Convolutional Neural Networks",
                    "entity_type": "ModelArchitecture",
                    "confidence": 0.7,
                    "accepted": False,
                    "review_status": "pending",
                    "salience": "background",
                    "evidence_role": "possible_concept",
                    "evidence_span": "quantum convolutional neural networks using QOptCraft were also discussed",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        relation = next(
            item
            for item in result.relations
            if item["subject_id"] == "concept:merlin"
            and item["relation_type"] == "REPRODUCES"
            and item["object_id"] == "concept:quantum-generative-adversarial-network"
        )

        assert "QGAN" in relation["evidence_span"]
        assert "QOptCraft" not in relation["evidence_span"]

    def test_entity_linker_builds_theoretical_cross_domain_relations(self):
        extraction = ExtractionResult(
            paper_id="arxiv:q-bio/0612009",
            paper_type="theoretical",
            concepts=[
                {
                    "label": "von Neumann entropy",
                    "entity_type": "Theory",
                    "confidence": 0.92,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "von Neumann entropy is the basis for defining quantum mutual information.",
                },
                {
                    "label": "Quantum mutual information",
                    "entity_type": "Metric",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "Quantum mutual information (QMI) measures quantum correlations.",
                },
                {
                    "label": "Envariance",
                    "entity_type": "Theory",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "Envariance is associated with phases of the Schmidt decomposition.",
                },
                {
                    "label": "Einselection",
                    "entity_type": "Phenomenon",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "Environment-induced superselection selects preferred pointer states.",
                },
                {
                    "label": "Schmidt decomposition",
                    "entity_type": "MethodFamily",
                    "confidence": 0.98,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "Schmidt decomposition phases are used in envariance.",
                },
                {
                    "label": "Two-photon vector soliton",
                    "entity_type": "Phenomenon",
                    "confidence": 0.95,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "A two-photon vector soliton generates temporal entanglement.",
                },
                {
                    "label": "Quantum temporal imaging",
                    "entity_type": "MethodFamily",
                    "confidence": 0.90,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "The formalism inspires quantum temporal imaging of temporal entanglement.",
                },
                {
                    "label": "Mirror neurons",
                    "entity_type": "Phenomenon",
                    "confidence": 0.98,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "central",
                    "evidence_span": "Mirror neurons are part of the mirror neuron system.",
                },
                {
                    "label": "Mirror neuron system",
                    "entity_type": "DomainConcept",
                    "confidence": 0.90,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "The mirror neuron system supports self-awareness.",
                },
                {
                    "label": "Reference picture selection",
                    "entity_type": "MethodFamily",
                    "confidence": 0.92,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "Reference picture selection uses a directed acyclic graph.",
                },
            ],
            methods=[
                {
                    "label": "Cross-phase modulation",
                    "entity_type": "Algorithm",
                    "source_type": "reviewed_method",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "Cross-phase modulation offers the possibility of temporal entanglement.",
                },
                {
                    "label": "Directed Acyclic Graph",
                    "entity_type": "Algorithm",
                    "source_type": "reviewed_method",
                    "confidence": 0.75,
                    "accepted": True,
                    "review_status": "pending",
                    "salience": "supporting",
                    "evidence_span": "A directed acyclic graph is used in reference picture selection.",
                },
            ],
            concept_candidates=[
                {
                    "label": "Temporal entanglement",
                    "entity_type": "Phenomenon",
                    "confidence": 0.85,
                    "review_status": "pending",
                    "accepted": False,
                    "mention_count": 6,
                    "salience": "supporting",
                    "evidence_span": "Temporal entanglement is defined as irreducibility of two-photon amplitudes.",
                },
                {
                    "label": "Pointer states",
                    "entity_type": "DomainConcept",
                    "confidence": 0.70,
                    "review_status": "pending",
                    "accepted": False,
                    "mention_count": 3,
                    "salience": "supporting",
                    "evidence_span": "Einselection selects a preferred set of pointer states.",
                },
            ],
        )

        result = EntityLinker(resolver=CanonicalResolver(embedding_engine=EmbeddingEngine())).enrich_extraction(extraction)
        concept_ids = {item["canonical_id"] for item in result.concepts}
        kg_write_ids = {
            item["canonical_id"]
            for item in result.concepts
            if item.get("accepted_for_kg_write") is True
        }
        relation_triples = {
            (item["subject_id"], item["relation_type"], item["object_id"], item["review_status"])
            for item in result.relations
        }

        assert "concept:temporal-entanglement" in concept_ids
        assert "concept:pointer-states" in kg_write_ids
        assert ("concept:von-neumann-entropy", "USED_IN", "concept:quantum-mutual-information", "approved") in relation_triples
        assert ("concept:schmidt-decomposition", "USED_IN", "concept:envariance", "approved") in relation_triples
        assert ("concept:einselection", "IMPLIES", "concept:pointer-states", "approved") in relation_triples
        assert ("concept:two-photon-vector-soliton", "CAUSES", "concept:temporal-entanglement", "approved") in relation_triples
        assert ("concept:quantum-temporal-imaging", "USES", "concept:temporal-entanglement", "approved") in relation_triples
        assert ("concept:cross-phase-modulation", "USED_FOR", "concept:temporal-entanglement", "approved") in relation_triples
        assert ("concept:mirror-neurons", "PART_OF", "concept:mirror-neuron-system", "approved") in relation_triples
        assert ("concept:directed-acyclic-graph", "USED_IN", "concept:reference-picture-selection", "approved") in relation_triples

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
        assert ontology.validate_relation_type("IMPLEMENTS") == "IMPLEMENTS"
        assert ontology.validate_relation_type("ELICITS") == "ELICITS"
        assert ontology.validate_relation_type("MITIGATES") == "MITIGATES"
        assert ontology.validate_relation_type("LEADS_TO") == "LEADS_TO"
        assert ontology.validate_relation_type("REPRODUCES") == "REPRODUCES"
        assert ontology.validate_relation_type("MORE_ROBUST_THAN") == "MORE_ROBUST_THAN"
        assert ontology.validate_relation_type("DERIVED_FROM") == "DERIVED_FROM"
        assert ontology.validate_relation_type("OUTPERFORMS") == "OUTPERFORMS"
        assert ontology.validate_relation_type("IMPLIES") == "IMPLIES"
        with pytest.raises(ValueError):
            ontology.validate_relation_type("MAKES_UP")


