from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from itertools import combinations
from math import log10
from typing import Any


@dataclass
class SimilarityEdge:
	source_id: str
	target_id: str
	score: float
	shared_citations: int
	similarity_type: str = "citation_overlap"


def compute_obsolescence_score(year: int | None, citation_count: int, current_year: int | None = None) -> float:
	if year is None:
		return 0.5
	now = current_year or datetime.now(timezone.utc).year
	age = max(0, now - year)
	citation_term = log10(max(1, citation_count) + 1)
	raw = age / (1.0 + citation_term)
	return round(min(1.0, raw / 10.0), 4)


def build_co_citation_similarity(
	citation_edges: list[tuple[str, str]],
	min_shared: int = 2,
	min_score: float = 0.25,
) -> list[SimilarityEdge]:
	"""
	Build SIMILAR_TO edges using overlap coefficient on outgoing references.
	"""
	references_by_paper: dict[str, set[str]] = {}
	for source_id, target_id in citation_edges:
		references_by_paper.setdefault(source_id, set()).add(target_id)

	edges: list[SimilarityEdge] = []
	papers = sorted(references_by_paper.keys())
	for left, right in combinations(papers, 2):
		left_refs = references_by_paper[left]
		right_refs = references_by_paper[right]
		shared = left_refs & right_refs
		shared_count = len(shared)
		if shared_count < min_shared:
			continue

		denom = max(1, min(len(left_refs), len(right_refs)))
		score = shared_count / denom
		if score < min_score:
			continue

		score = round(float(score), 4)
		edges.append(SimilarityEdge(left, right, score, shared_count))
		edges.append(SimilarityEdge(right, left, score, shared_count))

	return edges


def prepare_obsolescence_updates(papers: list[dict[str, Any]]) -> list[tuple[str, float]]:
	updates: list[tuple[str, float]] = []
	for paper in papers:
		pid = str(paper.get("id"))
		score = compute_obsolescence_score(
			year=paper.get("year"),
			citation_count=int(paper.get("citation_count") or 0),
		)
		updates.append((pid, score))
	return updates
