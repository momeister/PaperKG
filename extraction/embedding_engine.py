from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass

import numpy as np


@dataclass
class EmbeddingResult:
    """Result of embedding computation."""

    entity_label: str
    vector: np.ndarray
    model: str
    dimension: int
    backend: str = "hash-fallback"


class EmbeddingEngine:
    """
    Generates embeddings for extracted entities.

    Uses BGE-M3 via sentence-transformers when available. If the optional
    dependency/model is not installed locally, it falls back to deterministic
    token-hash vectors with the same dimensionality so tests and offline UI
    workflows remain reproducible.
    """

    # BGE-M3 produces 1024-dimensional embeddings
    EMBEDDING_DIM = 1024
    MODEL_NAME = "BAAI/bge-m3"

    def __init__(self, model_name: str | None = None, backend: str = "hash-fallback") -> None:
        """Initialize embedding engine."""
        self.model_name = model_name or self.MODEL_NAME
        self.backend = "hash-fallback"
        self.model = None
        if backend == "sentence-transformers":
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore

                self.model = SentenceTransformer(self.model_name)
                self.backend = "sentence-transformers"
            except Exception:
                raise

    def embed(self, label: str) -> np.ndarray:
        """Return the embedding vector for a label."""
        return self.embed_entity(label).vector

    def embed_entity(self, label: str) -> EmbeddingResult:
        """
        Generate embedding for entity label.

        Args:
            label: Entity name/label

        Returns:
            EmbeddingResult with embedding vector

        """
        if self.model is not None:
            vector = np.asarray(
                self.model.encode(label, normalize_embeddings=True),
                dtype=np.float32,
            )
        else:
            vector = self._deterministic_embedding(label)

        return EmbeddingResult(
            entity_label=label,
            vector=vector,
            model=self.model_name,
            dimension=int(vector.shape[0]),
            backend=self.backend,
        )

    def embed_batch(self, labels: list[str]) -> list[EmbeddingResult]:
        """
        Generate embeddings for multiple labels efficiently.

        Args:
            labels: List of entity labels

        Returns:
            List of EmbeddingResult objects

        """
        if self.model is not None and labels:
            vectors = np.asarray(
                self.model.encode(labels, normalize_embeddings=True),
                dtype=np.float32,
            )
            return [
                EmbeddingResult(
                    entity_label=label,
                    vector=vector,
                    model=self.model_name,
                    dimension=int(vector.shape[0]),
                    backend=self.backend,
                )
                for label, vector in zip(labels, vectors)
            ]

        results = []
        for label in labels:
            results.append(self.embed_entity(label))

        return results

    def _deterministic_embedding(self, label: str) -> np.ndarray:
        """Create a stable fallback embedding from token hashes."""
        vector = np.zeros(self.EMBEDDING_DIM, dtype=np.float32)
        tokens = re.findall(r"[a-z0-9]+", label.lower()) or [label.lower().strip() or "<empty>"]

        for token in tokens:
            digest = hashlib.blake2b(token.encode("utf-8"), digest_size=16).digest()
            for index in range(0, len(digest), 2):
                bucket = ((digest[index] << 8) | digest[index + 1]) % self.EMBEDDING_DIM
                sign = 1.0 if digest[index] % 2 == 0 else -1.0
                vector[bucket] += sign / max(len(tokens), 1)

        norm = float(np.linalg.norm(vector))
        if norm > 0:
            vector /= norm
        return vector

    def similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """
        Compute cosine similarity between two embedding vectors.

        Args:
            vec1: First embedding vector
            vec2: Second embedding vector

        Returns:
            Similarity score in [0, 1]
        """
        if len(vec1) == 0 or len(vec2) == 0:
            return 0.0

        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)

        if norm1 == 0 or norm2 == 0:
            return 0.0

        return float(np.dot(vec1, vec2) / (norm1 * norm2))

    def find_similar_entities(
        self,
        query_vector: np.ndarray,
        entity_vectors: dict[str, np.ndarray],
        threshold: float = 0.7,
        top_k: int = 5,
    ) -> list[tuple[str, float]]:
        """
        Find similar entities by embedding similarity.

        Args:
            query_vector: Query embedding
            entity_vectors: Dict of {entity_label -> embedding_vector}
            threshold: Minimum similarity threshold
            top_k: Maximum results to return

        Returns:
            List of (entity_label, similarity_score) sorted by score descending
        """
        results = []

        for label, vector in entity_vectors.items():
            sim = self.similarity(query_vector, vector)

            if sim >= threshold:
                results.append((label, sim))

        results.sort(key=lambda x: x[1], reverse=True)
        return results[:top_k]
