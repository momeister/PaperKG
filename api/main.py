from __future__ import annotations

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from graph.citation_analysis import build_co_citation_similarity
from graph.kuzu_schema import initialize_kuzu_schema
from graph.paper_ingestion import ingest_from_metadata_db
from storage.metadata_db import MetadataDB


app = FastAPI(title="ScienceKG API", version="0.2.0")


class BuildGraphRequest(BaseModel):
	metadata_db_path: str = "data/metadata.duckdb"
	graph_db_path: str = "data/graphs/global_kg"
	limit: int = 2000
	offset: int = 0


@app.get("/health")
def health() -> dict[str, str]:
	return {"status": "ok", "phase": "2"}


@app.get("/graph/summary")
def graph_summary(metadata_db_path: str = "data/metadata.duckdb") -> dict[str, int]:
	db = MetadataDB(metadata_db_path)
	try:
		paper_count = db.count_papers()
		dedup_count = db.conn.execute("SELECT COUNT(*) FROM dedup_log").fetchone()[0]
		return {"paper_count": paper_count, "dedup_events": int(dedup_count)}
	finally:
		db.close()


@app.post("/graph/phase2/build")
def build_phase2_graph(req: BuildGraphRequest) -> dict[str, int | str]:
	db = MetadataDB(req.metadata_db_path)
	try:
		try:
			graph = initialize_kuzu_schema(req.graph_db_path)
		except RuntimeError as exc:
			raise HTTPException(status_code=503, detail=str(exc)) from exc

		stats = ingest_from_metadata_db(graph, db, limit=req.limit, offset=req.offset)
		return {
			"message": "Phase 2 graph build finished",
			"papers_seen": stats.papers_seen,
			"papers_written": stats.papers_written,
			"citation_edges_written": stats.citation_edges_written,
		}
	finally:
		db.close()


@app.get("/graph/co-citation")
def co_citation_preview(
	metadata_db_path: str = "data/metadata.duckdb",
	min_shared: int = 2,
	min_score: float = 0.25,
	limit: int = 25,
) -> dict[str, object]:
	db = MetadataDB(metadata_db_path)
	try:
		rows = db.list_papers(limit=5000)
		citation_edges: list[tuple[str, str]] = []
		for row in rows:
			source = str(row.get("id"))
			for target in row.get("citations") or row.get("references") or []:
				citation_edges.append((source, str(target)))

		similarities = build_co_citation_similarity(
			citation_edges,
			min_shared=min_shared,
			min_score=min_score,
		)

		return {
			"total_similarity_edges": len(similarities),
			"sample": [edge.__dict__ for edge in similarities[:limit]],
		}
	finally:
		db.close()
