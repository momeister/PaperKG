import pytest

from extraction.vocabulary import VocabularyManager
from extraction.embedding_engine import EmbeddingEngine
from extraction.conflict_detector import ConflictDetector

from tests.llm_fakes import FakeLLMRouter

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


