from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from harvester.deduplication import deduplicate_papers


class GraphMergeTarget(Protocol):
	def merge_paper(self, paper: dict[str, Any]) -> None:
		...

	def merge_citation(self, from_paper_id: str, to_paper_id: str) -> None:
		...

	def merge_similarity(self, from_paper_id: str, to_paper_id: str, score: float, similarity_type: str) -> None:
		...


@dataclass
class MergeReport:
	project_records: int = 0
	unique_records: int = 0
	dedup_drops: int = 0
	citations_merged: int = 0
	similarities_merged: int = 0


def _ensure_record_id(record: dict[str, Any]) -> dict[str, Any]:
	if record.get("id"):
		return record
	enriched = dict(record)
	enriched["id"] = f"{record['source']}:{record['source_id']}"
	return enriched


def merge_project_records_into_global(
	global_graph: GraphMergeTarget,
	project_records: list[dict[str, Any]],
	citation_edges: list[tuple[str, str]] | None = None,
	similarity_edges: list[tuple[str, str, float, str]] | None = None,
) -> MergeReport:
	"""
	Merge project-level harvested papers into the global KG.
	"""
	unique_records, decisions = deduplicate_papers(project_records)

	report = MergeReport(
		project_records=len(project_records),
		unique_records=len(unique_records),
		dedup_drops=len(decisions),
	)

	for record in unique_records:
		global_graph.merge_paper(_ensure_record_id(record))

	for from_id, to_id in citation_edges or []:
		global_graph.merge_citation(from_id, to_id)
		report.citations_merged += 1

	for from_id, to_id, score, similarity_type in similarity_edges or []:
		global_graph.merge_similarity(from_id, to_id, float(score), similarity_type)
		report.similarities_merged += 1

	return report
