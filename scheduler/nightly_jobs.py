from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class NightlyJobReport:
    papers_seen: int = 0
    papers_written: int = 0
    citation_edges_written: int = 0
    similarity_edges_written: int = 0
    error: str | None = None


def rebuild_phase2_graph(
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
    limit: int = 5000,
    min_shared_citations: int = 2,
    min_similarity_score: float = 0.25,
) -> NightlyJobReport:
    """
    Rebuild Phase 2 graph data from DuckDB metadata.

    This function is intentionally callable without Celery/Redis so local
    debugging works. When Celery is installed and a broker is configured, the
    task wrapper below can schedule the same work.
    """
    from api.main import BuildGraphRequest, build_phase2_graph

    try:
        payload = build_phase2_graph(
            BuildGraphRequest(
                metadata_db_path=metadata_db_path,
                graph_db_path=graph_db_path,
                limit=limit,
                min_shared_citations=min_shared_citations,
                min_similarity_score=min_similarity_score,
            )
        )
        return NightlyJobReport(
            papers_seen=int(payload.get("papers_seen", 0)),
            papers_written=int(payload.get("papers_written", 0)),
            citation_edges_written=int(payload.get("citation_edges_written", 0)),
            similarity_edges_written=int(payload.get("similarity_edges_written", 0)),
        )
    except Exception as exc:
        return NightlyJobReport(error=str(exc))


def create_celery_app(config: dict[str, Any] | None = None):
    """
    Create an optional Celery app for scheduled jobs.

    Redis/Celery are optional runtime infrastructure. Importing this module does
    not require either service to be running.
    """
    try:
        from celery import Celery
    except ImportError as exc:
        raise RuntimeError("Install celery and redis extras to use scheduled jobs.") from exc

    cfg = config or {}
    broker_url = cfg.get("broker_url", "redis://localhost:6379/0")
    result_backend = cfg.get("result_backend", "redis://localhost:6379/1")
    app = Celery("sciencekg", broker=broker_url, backend=result_backend)

    @app.task(name="sciencekg.rebuild_phase2_graph")
    def rebuild_phase2_graph_task(**kwargs):
        return rebuild_phase2_graph(**kwargs).__dict__

    return app


if __name__ == "__main__":
    report = rebuild_phase2_graph()
    print(report)
