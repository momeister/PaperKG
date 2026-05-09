from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from graph.citation_analysis import build_co_citation_similarity
from graph.kuzu_schema import initialize_kuzu_schema
from graph.paper_ingestion import extract_citation_ids, ingest_from_metadata_db, paper_id
from storage.metadata_db import MetadataDB


app = FastAPI(title="ScienceKG API", version="0.2.0")


class BuildGraphRequest(BaseModel):
	metadata_db_path: str = "data/metadata.duckdb"
	graph_db_path: str = "data/graphs/global_kg"
	limit: int = 2000
	offset: int = 0
	min_shared_citations: int = 2
	min_similarity_score: float = 0.25
	include_extractions: bool = True


@app.get("/health")
def health() -> dict[str, str]:
	return {"status": "ok", "phase": "2"}


@app.get("/graph/summary")
def graph_summary(metadata_db_path: str = "data/metadata.duckdb") -> dict[str, int]:
	with MetadataDB(metadata_db_path) as db:
		paper_count = db.count_papers()
		dedup_count = db.count_dedup_events()
		return {"paper_count": paper_count, "dedup_events": int(dedup_count)}


@app.post("/graph/phase2/build")
def build_phase2_graph(req: BuildGraphRequest) -> dict[str, int | str]:
	with MetadataDB(req.metadata_db_path) as db:
		try:
			graph = initialize_kuzu_schema(req.graph_db_path)
		except RuntimeError as exc:
			raise HTTPException(status_code=503, detail=str(exc)) from exc

		records = db.list_papers(limit=req.limit, offset=req.offset)
		stats = ingest_from_metadata_db(
			graph,
			db,
			limit=req.limit,
			offset=req.offset,
			include_extractions=req.include_extractions,
		)
		citation_edges = _citation_edges_from_records(records)
		similarities = build_co_citation_similarity(
			citation_edges,
			min_shared=req.min_shared_citations,
			min_score=req.min_similarity_score,
		)
		for edge in similarities:
			graph.merge_similarity(
				edge.source_id,
				edge.target_id,
				edge.score,
				edge.similarity_type,
			)
		return {
			"message": "Phase 2 graph build finished",
			"papers_seen": stats.papers_seen,
			"papers_written": stats.papers_written,
			"citation_edges_written": stats.citation_edges_written,
			"similarity_edges_written": len(similarities),
			"concept_nodes_written": stats.concept_nodes_written,
			"method_nodes_written": stats.method_nodes_written,
			"concept_edges_written": stats.concept_edges_written,
			"method_edges_written": stats.method_edges_written,
		}


def _citation_edges_from_records(records: list[dict]) -> list[tuple[str, str]]:
	citation_edges: list[tuple[str, str]] = []
	for row in records:
		source = paper_id(row)
		for target in extract_citation_ids(row):
			if target != source:
				citation_edges.append((source, target))
	return citation_edges


@app.get("/graph/co-citation")
def co_citation_preview(
	metadata_db_path: str = "data/metadata.duckdb",
	min_shared: int = 2,
	min_score: float = 0.25,
	limit: int = 25,
) -> dict[str, object]:
	with MetadataDB(metadata_db_path) as db:
		rows = db.list_papers(limit=5000)
		citation_edges = _citation_edges_from_records(rows)

		similarities = build_co_citation_similarity(
			citation_edges,
			min_shared=min_shared,
			min_score=min_score,
		)

		return {
			"total_similarity_edges": len(similarities),
			"sample": [edge.__dict__ for edge in similarities[:limit]],
		}
