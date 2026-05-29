from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from maintenance.embedding_reindex import reindex_entity_embeddings
from quality.kg_health import build_health_report


def repair_health_state(
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
    pdf_base_dir: str = "data/pdfs",
    *,
    initialize_graph_fallback: bool = True,
    reindex_embeddings: bool = True,
) -> dict[str, Any]:
    before = build_health_report(metadata_db_path, graph_db_path, pdf_base_dir)
    actions: list[dict[str, Any]] = []

    if initialize_graph_fallback:
        actions.append(initialize_graph_storage(graph_db_path))

    if reindex_embeddings:
        actions.append(
            {
                "kind": "entity_embeddings",
                **reindex_entity_embeddings(metadata_db_path=metadata_db_path),
            }
        )

    after = build_health_report(metadata_db_path, graph_db_path, pdf_base_dir)
    return {
        "status": "completed" if after.get("status") != "error" else "warning",
        "actions": actions,
        "before": before,
        "after": after,
    }


def initialize_graph_storage(graph_db_path: str = "data/graphs/global_kg") -> dict[str, Any]:
    graph_path = Path(graph_db_path)
    graph_path.mkdir(parents=True, exist_ok=True)
    kuzu_available, reason = _kuzu_available()
    manifest = {
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "backend": "kuzu" if kuzu_available else "duckdb-fallback",
        "kuzu_available": kuzu_available,
        "python_version": sys.version.split()[0],
        "note": (
            "Kuzu storage directory is initialized. The product graph explorer "
            "can use DuckDB extraction data when the kuzu package is unavailable."
        ),
    }
    if reason:
        manifest["reason"] = reason
    (graph_path / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return {
        "kind": "graph_storage",
        "status": "completed",
        "path": str(graph_path),
        "backend": manifest["backend"],
        "kuzu_available": kuzu_available,
        "reason": reason,
    }


def _kuzu_available() -> tuple[bool, str | None]:
    try:
        import kuzu  # noqa: F401
    except Exception as exc:
        return False, str(exc)
    return True, None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Repair local ScienceKG health prerequisites.")
    parser.add_argument("--metadata-db", default="data/metadata.duckdb")
    parser.add_argument("--graph-db", default="data/graphs/global_kg")
    parser.add_argument("--pdf-dir", default="data/pdfs")
    parser.add_argument("--skip-embeddings", action="store_true")
    parser.add_argument("--skip-graph-storage", action="store_true")
    args = parser.parse_args(argv)

    result = repair_health_state(
        metadata_db_path=args.metadata_db,
        graph_db_path=args.graph_db,
        pdf_base_dir=args.pdf_dir,
        initialize_graph_fallback=not args.skip_graph_storage,
        reindex_embeddings=not args.skip_embeddings,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
