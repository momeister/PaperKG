from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np

from extraction.embedding_engine import EmbeddingEngine
from query.kg_retriever import Evidence, KGRetriever, SearchHit
from storage.metadata_db import MetadataDB


@dataclass(frozen=True)
class EmbeddingMatch:
    label: str
    score: float
    model: str
    embedding_version: int


class HybridRetriever:
    """
    Combines lexical KG retrieval with stored entity-embedding matches.

    The embedding path is deliberately additive: lexical retrieval remains the
    primary source of paper evidence, while embedding matches expand the query
    through similar stored labels and then map those labels back to papers.
    """

    def __init__(
        self,
        kg_retriever: KGRetriever | None = None,
        embedding_engine: EmbeddingEngine | None = None,
        embedding_threshold: float = 0.7,
        embedding_top_k: int = 5,
    ) -> None:
        self.kg_retriever = kg_retriever or KGRetriever()
        self.embedding_engine = embedding_engine or EmbeddingEngine()
        self.embedding_threshold = float(embedding_threshold)
        self.embedding_top_k = int(embedding_top_k)

    @property
    def metadata_db_path(self) -> str:
        return self.kg_retriever.metadata_db_path

    def search(
        self,
        query: str,
        limit: int = 10,
        include_extractions: bool = True,
        include_embeddings: bool = True,
    ) -> list[SearchHit]:
        merged = {
            hit.source.paper_id: hit
            for hit in self.kg_retriever.search(
                query,
                limit=limit,
                include_extractions=include_extractions,
            )
        }

        if include_embeddings:
            for match in self.find_embedding_matches(query):
                for label_hit in self.kg_retriever.search(match.label, limit=limit, include_extractions=True):
                    target = merged.get(label_hit.source.paper_id)
                    if target is None:
                        target = SearchHit(source=label_hit.source)
                        merged[label_hit.source.paper_id] = target
                    for evidence in label_hit.evidence:
                        target.add_evidence(evidence)
                    target.add_evidence(
                        Evidence(
                            paper_id=label_hit.source.paper_id,
                            kind="embedding",
                            field="entity_embeddings",
                            text=f"Query is similar to stored entity label: {match.label}",
                            score=match.score,
                            metadata={
                                "label": match.label,
                                "model": match.model,
                                "embedding_version": match.embedding_version,
                            },
                        )
                    )

        ordered = sorted(merged.values(), key=lambda item: item.score, reverse=True)
        return ordered[: max(0, int(limit))]

    def find_embedding_matches(self, query: str) -> list[EmbeddingMatch]:
        try:
            query_vector = self.embedding_engine.embed(query)
        except Exception:
            return []

        matches: list[EmbeddingMatch] = []
        with MetadataDB(self.metadata_db_path) as db:
            rows = db.list_entity_embeddings(limit=5000)

        for row in rows:
            vector = _as_vector(row.get("vector"))
            if vector is None:
                continue
            score = self.embedding_engine.similarity(query_vector, vector)
            if score < self.embedding_threshold:
                continue
            matches.append(
                EmbeddingMatch(
                    label=str(row.get("label") or row.get("label_norm") or ""),
                    score=float(score),
                    model=str(row.get("model") or ""),
                    embedding_version=int(row.get("embedding_version") or 0),
                )
            )

        matches.sort(key=lambda item: item.score, reverse=True)
        return matches[: self.embedding_top_k]

    def paper_detail(self, paper_id: str) -> dict[str, Any] | None:
        return self.kg_retriever.paper_detail(paper_id)

    def paper_neighborhood(self, paper_id: str, limit: int = 20) -> dict[str, Any] | None:
        return self.kg_retriever.paper_neighborhood(paper_id, limit=limit)


def _as_vector(value: Any) -> np.ndarray | None:
    if value is None:
        return None
    try:
        return np.asarray(value, dtype=np.float32)
    except (TypeError, ValueError):
        return None
