from __future__ import annotations

import argparse
import json
from typing import Any, Iterable

from extraction.embedding_engine import EmbeddingEngine
from storage.metadata_db import MetadataDB


ENTITY_FIELDS = ("concepts", "methods", "concept_candidates", "method_candidates")


def reindex_entity_embeddings(
    metadata_db_path: str = "data/metadata.duckdb",
    *,
    include_review_queue: bool = True,
    batch_size: int = 64,
    limit: int = 50000,
) -> dict[str, Any]:
    """
    Build reusable entity-label embeddings from the local extraction history.

    The engine uses sentence-transformers only when explicitly configured in the
    engine; the default path is deterministic and offline, so this job is safe
    to run in the local product UI.
    """
    engine = EmbeddingEngine()
    labels = collect_entity_labels(
        metadata_db_path=metadata_db_path,
        include_review_queue=include_review_queue,
        limit=limit,
    )

    indexed = 0
    with MetadataDB(metadata_db_path) as db:
        for start in range(0, len(labels), max(1, int(batch_size))):
            chunk = labels[start : start + max(1, int(batch_size))]
            for result in engine.embed_batch(chunk):
                db.upsert_entity_embedding(
                    label=result.entity_label,
                    vector=result.vector.astype(float).tolist(),
                    model=result.model,
                    backend=result.backend,
                    dimension=result.dimension,
                )
                indexed += 1

    return {
        "status": "completed",
        "labels": len(labels),
        "indexed": indexed,
        "model": engine.model_name,
        "backend": engine.backend,
        "dimension": engine.EMBEDDING_DIM,
    }


def collect_entity_labels(
    metadata_db_path: str = "data/metadata.duckdb",
    *,
    include_review_queue: bool = True,
    limit: int = 50000,
) -> list[str]:
    labels: list[str] = []
    seen: set[str] = set()

    def add(value: Any) -> None:
        label = " ".join(str(value or "").split())
        key = label.lower()
        if len(label) < 2 or key in seen:
            return
        seen.add(key)
        labels.append(label)

    with MetadataDB(metadata_db_path) as db:
        for extraction in db.list_extraction_results(limit=limit):
            if extraction.get("extraction_status") != "success":
                continue
            for field in ENTITY_FIELDS:
                for item in _iter_entity_items(extraction.get(field)):
                    add(item.get("canonical_label") or item.get("suggested_canonical") or item.get("label"))
                    add(item.get("label"))

        if include_review_queue:
            for item in db.list_entity_review_queue(status=None, limit=limit):
                add(item.get("suggested_canonical") or item.get("canonical_id") or item.get("label"))
                add(item.get("label"))

    return labels


def _iter_entity_items(value: Any) -> Iterable[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Rebuild local entity embeddings from extraction history.")
    parser.add_argument("--metadata-db", default="data/metadata.duckdb")
    parser.add_argument("--no-review-queue", action="store_true")
    parser.add_argument("--limit", type=int, default=50000)
    args = parser.parse_args(argv)

    result = reindex_entity_embeddings(
        metadata_db_path=args.metadata_db,
        include_review_queue=not args.no_review_queue,
        limit=args.limit,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
