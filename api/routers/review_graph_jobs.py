"""Review-Queue, Graph-Explorer, Jobs (Graph-Rebuild/Health-Repair) und
Benchmark/Eval-Qualitaetslaeufe.

Split out of api/product_main.py. Behaviour unchanged. Patchbare Namen laufen
ueber pm.<name>: run_suite (Test-Patch-Surface). _slug/_add_edge/... leben hier
und werden nach product_main re-importiert (extraction/notes nutzen pm._slug).
"""
from __future__ import annotations

import json
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable run_suite
from api.main import BuildGraphRequest, build_phase2_graph
from api.routers.extraction import _latest_successful_extractions
from api.routers.projects import _load_projects, _paper_matches_query, _projects_path
from graph.paper_ingestion import extract_citation_ids, paper_id
from maintenance.health_repair import repair_health_state
from quality.benchmark import run_benchmark
from quality.benchmark_suite import SuiteConfig, latest_suite_report
from quality.phase4_eval import run_eval
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_GRAPH_DB_PATH = "data/graphs/global_kg"
DEFAULT_PDF_BASE_DIR = "data/pdfs"

router = APIRouter()


class ReviewActionRequest(BaseModel):
    ids: list[int]
    action: str = Field(pattern="^(approve|reject)$")


class GraphExplorerResponse(BaseModel):
    nodes: list[dict[str, Any]]
    edges: list[dict[str, Any]]
    stats: dict[str, Any]


class BenchmarkJobRequest(BaseModel):
    gold_dir: str = "quality/gold"
    pred_dir: str | None = None
    allow_embedded_predictions: bool = True
    suite: str | None = Field(default=None, pattern="^(core|extended)$")
    context_policy: str = Field(default="auto", pattern="^(auto|whole|chunk)$")
    compare_context_policies: list[str] = []
    answer_context_mode: str = Field(default="kg", pattern="^(kg|pdf_if_fits)$")
    provider: str | None = None
    model: str | None = None
    download_missing: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    output_dir: str = "data/eval/benchmarks"
    isolated_db: bool = True


class BenchmarkSuiteJobRequest(BaseModel):
    suite: str = Field(default="core", pattern="^(core|extended)$")
    provider: str | None = None
    model: str | None = None
    context_policy: str = Field(default="auto", pattern="^(auto|whole|chunk)$")
    compare_context_policies: list[str] = []
    answer_context_mode: str = Field(default="kg", pattern="^(kg|pdf_if_fits)$")
    download_missing: bool = False
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    output_dir: str = "data/eval/benchmarks"
    isolated_db: bool = True


class EvalJobRequest(BaseModel):
    provider: str
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    limit: int = Field(default=8, ge=1, le=25)
    timeout_seconds: float | None = Field(default=None, ge=1)


class HealthRepairRequest(BaseModel):
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    initialize_graph_fallback: bool = True
    reindex_embeddings: bool = True


@router.get("/review/entities")
def review_entities(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    status: str | None = "pending",
    query: str = "",
    limit: int = Query(default=100, ge=1, le=1000),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        items = db.list_entity_review_queue(status=status, limit=limit)
    if query:
        query_lower = query.lower()
        items = [
            item for item in items
            if query_lower in str(item.get("label") or "").lower()
            or query_lower in str(item.get("suggested_canonical") or "").lower()
            or query_lower in str(item.get("paper_id") or "").lower()
        ]
    return {"items": items, "total": len(items)}


@router.post("/review/entities/actions")
def review_entity_actions(
    request: ReviewActionRequest,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
) -> dict[str, Any]:
    status = "approved" if request.action == "approve" else "rejected"
    ids = [int(item) for item in request.ids]
    if not ids:
        return {"updated": 0, "status": status}
    placeholders = ", ".join("?" for _ in ids)
    with MetadataDB(metadata_db_path) as db:
        db._execute(
            f"""
            UPDATE entity_review_queue
            SET review_status = ?, updated_timestamp = ?
            WHERE id IN ({placeholders})
            """,
            [status, datetime.now(), *ids],
        )
    return {"updated": len(ids), "status": status}


@router.get("/graph/explorer", response_model=GraphExplorerResponse)
def graph_explorer(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    projects_path: str | None = None,
    project_id: str | None = None,
    query: str = "",
    edge_types: list[str] | None = Query(default=None),
    limit: int = Query(default=1200, ge=5, le=5000),
) -> GraphExplorerResponse:
    projects = _load_projects(_projects_path(projects_path))
    selected_ids = set(projects.get(project_id, [])) if project_id else None
    requested_edges = set(_split_query_values(edge_types) or ["cites", "concept", "method", "similar"])

    with MetadataDB(metadata_db_path) as db:
        papers = db.list_papers(limit=50000)
        extractions = db.list_extraction_results(limit=50000)

    if selected_ids is not None:
        papers = [paper for paper in papers if str(paper.get("id")) in selected_ids]
    if query:
        papers = [paper for paper in papers if _paper_matches_query(paper, query)]
    total_matching = len(papers)

    # Papers with a successful extraction are the actual knowledge graph content
    # and must never be pushed out by `limit` — `limit` only bounds how many
    # additional non-extracted papers are pulled in for citation/similarity context.
    extraction_by_paper = _latest_successful_extractions(extractions)
    extracted_papers = [paper for paper in papers if str(paper.get("id")) in extraction_by_paper]
    other_papers = [paper for paper in papers if str(paper.get("id")) not in extraction_by_paper]
    extracted_count = len(extracted_papers)
    effective_limit = max(limit, extracted_count)
    papers = (extracted_papers + other_papers)[:effective_limit]
    paper_ids = {str(paper.get("id")) for paper in papers}
    truncated = len(papers) < total_matching

    nodes: dict[str, dict[str, Any]] = {}
    edges: dict[str, dict[str, Any]] = {}
    for paper in papers:
        pid = str(paper.get("id") or paper_id(paper))
        nodes[pid] = {
            "id": pid,
            "label": str(paper.get("title") or pid)[:120],
            "type": "paper",
            "year": paper.get("year"),
            "metadata": {"source": paper.get("source"), "source_id": paper.get("source_id")},
        }

    if "cites" in requested_edges or "similar" in requested_edges:
        refs_by_paper = {str(paper.get("id")): set(extract_citation_ids(paper)) for paper in papers}
        if "cites" in requested_edges:
            for source_id, refs in refs_by_paper.items():
                for ref in refs:
                    if ref in paper_ids:
                        _add_edge(edges, source_id, ref, "cites", "CITES")
        if "similar" in requested_edges:
            ids = list(refs_by_paper)
            for index, source_id in enumerate(ids):
                for target_id in ids[index + 1 :]:
                    shared = refs_by_paper[source_id] & refs_by_paper[target_id]
                    union = refs_by_paper[source_id] | refs_by_paper[target_id]
                    if shared and union:
                        score = len(shared) / len(union)
                        if score >= 0.1:
                            _add_edge(edges, source_id, target_id, "similar", "SIMILAR", score=round(score, 4))

    for pid in paper_ids:
        extraction = extraction_by_paper.get(pid)
        if not extraction:
            continue
        if "concept" in requested_edges:
            for concept in _iter_labeled_items(extraction.get("concepts"))[:12]:
                node_id = str(concept.get("canonical_id") or f"concept:{_slug(concept.get('label'))}")
                nodes.setdefault(
                    node_id,
                    {"id": node_id, "label": concept.get("canonical_label") or concept.get("label"), "type": "concept", "metadata": concept},
                )
                _add_edge(edges, pid, node_id, "concept", "HAS_CONCEPT", score=concept.get("confidence"))
        if "method" in requested_edges:
            for method in _iter_labeled_items(extraction.get("methods"))[:12]:
                node_id = str(method.get("canonical_id") or f"method:{_slug(method.get('label'))}")
                nodes.setdefault(
                    node_id,
                    {"id": node_id, "label": method.get("canonical_label") or method.get("label"), "type": "method", "metadata": method},
                )
                _add_edge(edges, pid, node_id, "method", "HAS_METHOD", score=method.get("confidence"))

    return GraphExplorerResponse(
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        stats={
            "paper_count": len(papers),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "edge_types": sorted({edge["type"] for edge in edges.values()}),
            "total_paper_count": total_matching,
            "extracted_paper_count": extracted_count,
            "truncated": truncated,
        },
    )


@router.get("/jobs")
def jobs(metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"jobs": db.list_batch_jobs(limit=100)}


@router.post("/jobs/graph-rebuild")
def graph_rebuild_job(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    graph_db_path: str = DEFAULT_GRAPH_DB_PATH,
    include_extractions: bool = True,
) -> dict[str, Any]:
    result = build_phase2_graph(
        BuildGraphRequest(
            metadata_db_path=metadata_db_path,
            graph_db_path=graph_db_path,
            include_extractions=include_extractions,
        )
    )
    return {"status": "completed", "result": result}


@router.post("/jobs/health-repair")
def health_repair_job(request: HealthRepairRequest) -> dict[str, Any]:
    return repair_health_state(
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        pdf_base_dir=request.pdf_base_dir,
        initialize_graph_fallback=request.initialize_graph_fallback,
        reindex_embeddings=request.reindex_embeddings,
    )


@router.post("/jobs/benchmark")
def benchmark_job(request: BenchmarkJobRequest) -> dict[str, Any]:
    if request.suite:
        report = _run_benchmark_suite_job(request)
        return {"status": "completed", "report": report}
    started = time.perf_counter()
    report = run_benchmark(
        gold_dir=Path(request.gold_dir),
        pred_dir=Path(request.pred_dir) if request.pred_dir else None,
        allow_embedded_predictions=request.allow_embedded_predictions,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    run = _persist_benchmark_run(
        kind="extraction",
        provider=None,
        model=None,
        summary=report.get("summary") or {},
        report=report,
        duration_ms=duration_ms,
        metadata_db_path=request.metadata_db_path,
    )
    return {"status": "completed", "report": report, "run": run}


@router.post("/jobs/benchmark-suite")
def benchmark_suite_job(request: BenchmarkSuiteJobRequest) -> dict[str, Any]:
    report = _run_benchmark_suite_job(request)
    return {"status": "completed", "report": report}


@router.get("/quality/benchmark-suite/latest")
def benchmark_suite_latest(output_dir: str = "data/eval/benchmarks") -> dict[str, Any]:
    report = latest_suite_report(Path(output_dir))
    return {"status": "ok" if report else "empty", "report": report}


@router.post("/jobs/eval")
def eval_job(request: EvalJobRequest) -> dict[str, Any]:
    started = time.perf_counter()
    report = run_eval(
        provider=request.provider,
        model=request.model,
        metadata_db_path=request.metadata_db_path,
        graph_db_path=request.graph_db_path,
        limit=request.limit,
        timeout_seconds=request.timeout_seconds,
    )
    duration_ms = int((time.perf_counter() - started) * 1000)
    run = _persist_benchmark_run(
        kind="qa",
        provider=report.get("provider") or request.provider,
        model=report.get("model") or request.model,
        summary=report.get("summary") or {},
        report=report,
        duration_ms=duration_ms,
        metadata_db_path=request.metadata_db_path,
    )
    return {"status": "completed", "report": report, "run": run}


def _persist_benchmark_run(
    *,
    kind: str,
    provider: str | None,
    model: str | None,
    summary: dict[str, Any],
    report: dict[str, Any],
    duration_ms: int,
    metadata_db_path: str,
) -> dict[str, Any]:
    try:
        with MetadataDB(metadata_db_path) as db:
            return db.add_benchmark_run(
                {
                    "kind": kind,
                    "provider": provider,
                    "model": model,
                    "summary": summary,
                    "report": report,
                    "duration_ms": duration_ms,
                }
            )
    except Exception:
        return {}


@router.get("/benchmark/runs")
def list_benchmark_runs(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    kind: str | None = None,
    limit: int = Query(default=100, ge=1, le=500),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        runs = db.list_benchmark_runs(kind=kind, limit=limit)
    return {"items": runs, "total": len(runs)}


@router.delete("/benchmark/runs/{run_id}")
def delete_benchmark_run(run_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_benchmark_run(run_id)
    return {"deleted": deleted, "id": run_id}


def _run_benchmark_suite_job(request: BenchmarkJobRequest | BenchmarkSuiteJobRequest) -> dict[str, Any]:
    policies = list(getattr(request, "compare_context_policies", None) or []) or [request.context_policy]
    return pm.run_suite(
        SuiteConfig(
            suite=getattr(request, "suite", None) or "core",
            provider=request.provider,
            model=request.model,
            context_policy=request.context_policy,
            compare_context_policies=policies,
            answer_context_mode=request.answer_context_mode,
            download_missing=bool(request.download_missing),
            metadata_db_path=request.metadata_db_path,
            graph_db_path=request.graph_db_path,
            pdf_base_dir=request.pdf_base_dir,
            output_dir=Path(request.output_dir),
            isolated_db=bool(request.isolated_db),
        )
    )


def _iter_labeled_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except json.JSONDecodeError:
            return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict) and item.get("label")]


def _add_edge(
    edges: dict[str, dict[str, Any]],
    source: str,
    target: str,
    edge_type: str,
    label: str,
    score: Any | None = None,
) -> None:
    edge_id = f"{source}->{edge_type}->{target}"
    edges.setdefault(
        edge_id,
        {"id": edge_id, "source": source, "target": target, "type": edge_type, "label": label, "score": score},
    )


def _slug(value: Any) -> str:
    text = re.sub(r"[^a-z0-9]+", "-", str(value or "").lower()).strip("-")
    return text or "item"


def _split_query_values(values: list[str] | None) -> list[str]:
    output: list[str] = []
    for value in values or []:
        output.extend(item.strip() for item in value.split(",") if item.strip())
    return output
