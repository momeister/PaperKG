from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

from storage.metadata_db import MetadataDB


def build_health_report(
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
    pdf_base_dir: str = "data/pdfs",
    extraction_coverage_gate: float = 0.8,
) -> dict[str, Any]:
    metadata_path = Path(metadata_db_path)
    graph_path = Path(graph_db_path)
    pdf_path = Path(pdf_base_dir)

    if not metadata_path.exists():
        return {
            "status": "error",
            "metadata_db": {"path": str(metadata_path), "exists": False},
            "graph_db": {"path": str(graph_path), "exists": graph_path.exists()},
            "pdf_library": {"path": str(pdf_path), "exists": pdf_path.exists(), "pdf_count": _pdf_count(pdf_path)},
            "warnings": [f"Metadata database not found: {metadata_path}"],
        }

    with MetadataDB(str(metadata_path)) as db:
        kuzu_available = _kuzu_available()
        report = {
            "status": "ok",
            "metadata_db": {
                "path": str(metadata_path),
                "exists": True,
                "paper_count": db.count_papers(),
            },
            "graph_db": {
                "path": str(graph_path),
                "exists": graph_path.exists(),
                "kuzu_available": kuzu_available,
                "backend": _graph_backend(graph_path, kuzu_available),
                "python_version": sys.version.split()[0],
            },
            "pdf_library": {
                "path": str(pdf_path),
                "exists": pdf_path.exists(),
                "pdf_count": _pdf_count(pdf_path),
            },
            "papers": _paper_metrics(db),
            "extractions": _extraction_metrics(db),
            "review_queue": _review_queue_metrics(db),
            "embeddings": _embedding_metrics(db),
            "batch_jobs": _batch_job_metrics(db),
            "quality_telemetry": _quality_telemetry_metrics(db),
        }

    report["warnings"] = _health_warnings(report, extraction_coverage_gate)
    report["action_items"] = _health_action_items(report)
    report["status"] = "warning" if report["warnings"] else "ok"
    return report


def _paper_metrics(db: MetadataDB) -> dict[str, Any]:
    row = _one(
        db,
        """
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN has_full_text THEN 1 ELSE 0 END) AS with_full_text,
            SUM(CASE WHEN retracted THEN 1 ELSE 0 END) AS retracted,
            SUM(CASE WHEN conflict_flag THEN 1 ELSE 0 END) AS conflicts,
            SUM(CASE WHEN title IS NULL OR title = '' THEN 1 ELSE 0 END) AS missing_title,
            SUM(CASE WHEN year IS NULL THEN 1 ELSE 0 END) AS missing_year,
            SUM(CASE WHEN obsolescence_score >= 0.6 THEN 1 ELSE 0 END) AS high_obsolescence
        FROM papers
        """,
    )
    duplicate_doi_groups = _scalar(
        db,
        """
        SELECT COUNT(*) FROM (
            SELECT doi FROM papers
            WHERE doi IS NOT NULL AND doi <> ''
            GROUP BY doi
            HAVING COUNT(*) > 1
        )
        """,
    )
    total = int(row.get("total") or 0)
    return {
        "total": total,
        "with_full_text": int(row.get("with_full_text") or 0),
        "full_text_coverage": _ratio(row.get("with_full_text"), total),
        "retracted": int(row.get("retracted") or 0),
        "conflicts": int(row.get("conflicts") or 0),
        "missing_title": int(row.get("missing_title") or 0),
        "missing_year": int(row.get("missing_year") or 0),
        "high_obsolescence": int(row.get("high_obsolescence") or 0),
        "duplicate_doi_groups": int(duplicate_doi_groups or 0),
    }


def _extraction_metrics(db: MetadataDB) -> dict[str, Any]:
    status_counts = _group_counts(db, "SELECT extraction_status, COUNT(*) FROM extraction_results GROUP BY extraction_status")
    latest = _one(
        db,
        """
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT CASE WHEN extraction_status = 'success' THEN paper_id END) AS papers_with_success,
            AVG(extraction_duration_seconds) AS avg_duration_seconds,
            MAX(extraction_timestamp) AS latest_extraction_timestamp
        FROM extraction_results
        """,
    )
    paper_total = db.count_papers()
    papers_with_success = int(latest.get("papers_with_success") or 0)
    return {
        "total": int(latest.get("total") or 0),
        "by_status": status_counts,
        "papers_with_success": papers_with_success,
        "paper_success_coverage": _ratio(papers_with_success, paper_total),
        "avg_duration_seconds": _round(latest.get("avg_duration_seconds")),
        "latest_extraction_timestamp": _string_or_none(latest.get("latest_extraction_timestamp")),
    }


def _review_queue_metrics(db: MetadataDB) -> dict[str, Any]:
    return {
        "pending": int(_scalar(db, "SELECT COUNT(*) FROM entity_review_queue WHERE review_status = 'pending'") or 0),
        "total": int(_scalar(db, "SELECT COUNT(*) FROM entity_review_queue") or 0),
    }


def _embedding_metrics(db: MetadataDB) -> dict[str, Any]:
    row = _one(
        db,
        """
        SELECT
            COUNT(*) AS total,
            COUNT(DISTINCT model) AS model_count,
            MAX(embedding_version) AS latest_version
        FROM entity_embeddings
        """,
    )
    return {
        "total": int(row.get("total") or 0),
        "model_count": int(row.get("model_count") or 0),
        "latest_version": int(row.get("latest_version") or 0),
    }


def _batch_job_metrics(db: MetadataDB) -> dict[str, Any]:
    return {
        "by_status": _group_counts(db, "SELECT status, COUNT(*) FROM batch_jobs GROUP BY status"),
        "latest": db.list_batch_jobs(limit=5),
    }


def _quality_telemetry_metrics(db: MetadataDB) -> dict[str, Any]:
    row = _one(
        db,
        """
        SELECT
            COUNT(*) AS total,
            AVG(duration_seconds) AS avg_duration_seconds,
            MAX(timestamp) AS latest_timestamp
        FROM extraction_quality
        """,
    )
    return {
        "total": int(row.get("total") or 0),
        "by_parse_quality": _group_counts(db, "SELECT parse_quality, COUNT(*) FROM extraction_quality GROUP BY parse_quality"),
        "avg_duration_seconds": _round(row.get("avg_duration_seconds")),
        "latest_timestamp": _string_or_none(row.get("latest_timestamp")),
    }


def _health_warnings(report: dict[str, Any], extraction_coverage_gate: float) -> list[str]:
    warnings: list[str] = []
    paper_count = int(report.get("metadata_db", {}).get("paper_count") or 0)
    if paper_count == 0:
        warnings.append("No papers are stored in the metadata database.")
    graph = report.get("graph_db", {})
    if not graph.get("exists") and graph.get("kuzu_available"):
        warnings.append("Kuzu graph path does not exist; graph-only features may be unavailable.")
    if not report.get("pdf_library", {}).get("exists"):
        warnings.append("PDF library path does not exist.")

    coverage = float(report.get("extractions", {}).get("paper_success_coverage") or 0.0)
    if paper_count and coverage < extraction_coverage_gate:
        warnings.append(
            f"Successful extraction coverage is {coverage:.0%}, below the {extraction_coverage_gate:.0%} gate."
        )

    if int(report.get("extractions", {}).get("by_status", {}).get("failed") or 0):
        warnings.append("Failed extraction runs are present.")
    if paper_count and int(report.get("embeddings", {}).get("total") or 0) == 0:
        warnings.append("No entity embeddings are stored; hybrid semantic retrieval may be weaker.")
    if int(report.get("papers", {}).get("retracted") or 0):
        warnings.append("Retracted papers are present and should be flagged in answers.")
    if int(report.get("papers", {}).get("duplicate_doi_groups") or 0):
        warnings.append("Duplicate DOI groups are present.")
    return warnings


def _health_action_items(report: dict[str, Any]) -> list[dict[str, Any]]:
    action_items: list[dict[str, Any]] = []
    pending = int(report.get("review_queue", {}).get("pending") or 0)
    if pending:
        action_items.append(
            {
                "kind": "review_queue",
                "severity": "attention",
                "message": f"{pending} entity review items are waiting for approve/reject decisions.",
            }
        )
    graph = report.get("graph_db", {})
    if not graph.get("kuzu_available"):
        action_items.append(
            {
                "kind": "graph_backend",
                "severity": "info",
                "message": "Kuzu is unavailable in this Python environment; DuckDB-backed graph and assistant features remain usable.",
            }
        )
    return action_items


def _kuzu_available() -> bool:
    return importlib.util.find_spec("kuzu") is not None


def _graph_backend(graph_path: Path, kuzu_available: bool) -> str:
    if kuzu_available and graph_path.exists():
        return "kuzu"
    return "duckdb-fallback"


def _one(db: MetadataDB, query: str) -> dict[str, Any]:
    row = db._execute(query).fetchone()
    if row is None:
        return {}
    cols = [desc[0] for desc in db.conn.description]
    return dict(zip(cols, row))


def _scalar(db: MetadataDB, query: str) -> Any:
    row = db._execute(query).fetchone()
    return row[0] if row else None


def _group_counts(db: MetadataDB, query: str) -> dict[str, int]:
    rows = db._execute(query).fetchall()
    return {str(key or "unknown"): int(value or 0) for key, value in rows}


def _ratio(numerator: Any, denominator: Any) -> float:
    numerator_value = float(numerator or 0)
    denominator_value = float(denominator or 0)
    return round(numerator_value / denominator_value, 4) if denominator_value else 0.0


def _round(value: Any) -> float | None:
    return round(float(value), 4) if value is not None else None


def _string_or_none(value: Any) -> str | None:
    return str(value) if value is not None else None


def _pdf_count(path: Path) -> int:
    return len(list(path.rglob("*.pdf"))) if path.exists() else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Report local ScienceKG health metrics.")
    parser.add_argument("--metadata-db", default="data/metadata.duckdb", help="DuckDB metadata path.")
    parser.add_argument("--graph-db", default="data/graphs/global_kg", help="Kuzu graph path.")
    parser.add_argument("--pdf-dir", default="data/pdfs", help="Local PDF library path.")
    parser.add_argument("--output", default=None, help="Optional path to write JSON report.")
    args = parser.parse_args(argv)

    report = build_health_report(
        metadata_db_path=args.metadata_db,
        graph_db_path=args.graph_db,
        pdf_base_dir=args.pdf_dir,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
