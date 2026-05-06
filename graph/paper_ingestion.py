from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from graph.citation_analysis import compute_obsolescence_score


class GraphWriter(Protocol):
	def merge_paper(self, paper: dict[str, Any]) -> None:
		...

	def merge_citation(self, from_paper_id: str, to_paper_id: str) -> None:
		...


@dataclass
class IngestionStats:
	papers_seen: int = 0
	papers_written: int = 0
	citation_edges_written: int = 0


def paper_id(record: dict[str, Any]) -> str:
	return str(record.get("id") or f"{record['source']}:{record['source_id']}")


def extract_citation_ids(record: dict[str, Any]) -> list[str]:
	"""
	Extract citation ids from multiple known metadata formats.
	"""
	raw = record.get("citations") or record.get("references") or []
	citations: list[str] = []

	for item in raw:
		if isinstance(item, str):
			citations.append(item)
			continue
		if not isinstance(item, dict):
			continue

		candidate = (
			item.get("paper_id")
			or item.get("paperId")
			or item.get("id")
			or item.get("doi")
			or item.get("arxivId")
		)
		if candidate:
			citations.append(str(candidate))

	# Stable de-duplication while preserving source order.
	seen: set[str] = set()
	unique = []
	for citation in citations:
		if citation not in seen:
			seen.add(citation)
			unique.append(citation)
	return unique


def to_phase2_paper_node(record: dict[str, Any]) -> dict[str, Any]:
	now = datetime.now(UTC).isoformat(sep=" ", timespec="seconds")
	citations = extract_citation_ids(record)

	return {
		"id": paper_id(record),
		"title": record.get("title") or "",
		"year": record.get("year"),
		"version": int(record.get("version") or 1),
		"superseded_by": record.get("superseded_by"),
		"has_full_text": bool(record.get("has_full_text") or record.get("pdf_url")),
		"peer_reviewed": bool(record.get("peer_reviewed", False)),
		"retracted": bool(record.get("retracted", False)),
		"language_original": record.get("language_original") or "unknown",
		"citation_count": int(record.get("citation_count") or len(citations)),
		"confidence_score": float(record.get("confidence_score") or 0.5),
		"obsolescence_score": float(
			record.get("obsolescence_score")
			if record.get("obsolescence_score") is not None
			else compute_obsolescence_score(record.get("year"), int(record.get("citation_count") or len(citations)))
		),
		"conflict_flag": bool(record.get("conflict_flag", False)),
		"embedding_model": record.get("embedding_model") or "",
		"embedding_version": int(record.get("embedding_version") or 0),
		"source": record.get("source") or "unknown",
		"added_to_graph": record.get("added_to_graph") or now,
		"last_updated": now,
	}


def to_reference_stub_paper_node(reference_id: str) -> dict[str, Any]:
	now = datetime.now(UTC).isoformat(sep=" ", timespec="seconds")
	return {
		"id": reference_id,
		"title": "",
		"year": None,
		"version": 1,
		"superseded_by": None,
		"has_full_text": False,
		"peer_reviewed": False,
		"retracted": False,
		"language_original": "unknown",
		"citation_count": 0,
		"confidence_score": 0.5,
		"obsolescence_score": 0.0,
		"conflict_flag": False,
		"embedding_model": "",
		"embedding_version": 0,
		"source": "citation_reference",
		"added_to_graph": now,
		"last_updated": now,
	}


def ingest_records(graph: GraphWriter, records: list[dict[str, Any]]) -> IngestionStats:
	stats = IngestionStats()

	for record in records:
		stats.papers_seen += 1
		source_id = paper_id(record)
		node = to_phase2_paper_node(record)
		graph.merge_paper(node)
		stats.papers_written += 1

		for target_id in extract_citation_ids(record):
			if target_id == source_id:
				continue
			graph.merge_paper(to_reference_stub_paper_node(target_id))
			graph.merge_citation(source_id, target_id)
			stats.citation_edges_written += 1

	return stats


def ingest_from_metadata_db(
	graph: GraphWriter,
	metadata_db: Any,
	limit: int = 1000,
	offset: int = 0,
) -> IngestionStats:
	records = metadata_db.list_papers(limit=limit, offset=offset)
	return ingest_records(graph, records)
