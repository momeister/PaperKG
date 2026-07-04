"""Concept-linkage strategies (base + OpenAlex) and shared coercion helper."""
from __future__ import annotations

from typing import Any

from extraction.embedding_engine import EmbeddingEngine
from extraction.ontology import stable_canonical_id
from extraction.text_normalization import normalize_key


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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

    def __init__(
        self,
        concept_cache: dict[str, dict] | None = None,
        embedding_engine: EmbeddingEngine | None = None,
        similarity_threshold: float = 0.85,
    ) -> None:
        """
        Initialize with optional concept cache.

        Args:
            concept_cache: Pre-populated dict of {concept_label -> openalx_concept_data}
        """
        self.cache = concept_cache or {}
        self.embedding_engine = embedding_engine
        self.similarity_threshold = similarity_threshold

    def link(self, concept: dict[str, str]) -> dict[str, Any] | None:
        """
        Link concept to OpenAlex Concept ID.

        Args:
            concept: Dict with 'label', 'context', 'confidence' keys

        Returns:
            Enriched dict with 'openalx_id', 'openalx_label' or None if not found
        """
        label = normalize_key(concept.get("label", ""))

        cached = self._lookup_exact(label)
        if cached is None:
            cached = self._lookup_by_embedding(label)
        if cached is not None:
            return self._enrich(concept, cached)

        return None

    def _lookup_exact(self, normalized_label: str) -> dict[str, Any] | None:
        if normalized_label in self.cache:
            return self.cache[normalized_label]
        for key, item in self.cache.items():
            if normalize_key(key) == normalized_label:
                return item

        for item in self.cache.values():
            labels = [item.get("display_name", "")]
            labels.extend(item.get("aliases") or [])
            if normalized_label in {normalize_key(label) for label in labels}:
                return item
        return None

    def _lookup_by_embedding(self, normalized_label: str) -> dict[str, Any] | None:
        if self.embedding_engine is None or not self.cache:
            return None

        query_vector = self.embedding_engine.embed(normalized_label)
        best_item = None
        best_score = 0.0
        for item in self.cache.values():
            candidate_label = item.get("display_name")
            if not candidate_label:
                continue
            score = self.embedding_engine.similarity(
                query_vector,
                self.embedding_engine.embed(str(candidate_label)),
            )
            if score > best_score:
                best_score = score
                best_item = item

        if best_item is not None and best_score >= self.similarity_threshold:
            return {**best_item, "link_score": best_score}
        return None

    @staticmethod
    def _enrich(concept: dict[str, str], cached: dict[str, Any]) -> dict[str, Any]:
        enriched = {
            **concept,
            "openalx_id": cached.get("id"),
            "openalx_label": cached.get("display_name"),
            "canonical_id": str(cached.get("id") or stable_canonical_id(concept.get("label", ""))),
            "canonical_label": cached.get("display_name") or concept.get("label", ""),
            "review_status": concept.get("review_status") or "approved",
        }
        if "link_score" in cached:
            enriched["link_score"] = cached["link_score"]
        return enriched


