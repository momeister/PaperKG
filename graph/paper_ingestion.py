from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from graph.citation_analysis import compute_obsolescence_score
from extraction.text_normalization import normalize_scientific_text, slugify_label


class GraphWriter(Protocol):
	def merge_paper(self, paper: dict[str, Any]) -> None:
		...

	def merge_citation(self, from_paper_id: str, to_paper_id: str) -> None:
		...

	def merge_concept(self, concept: dict[str, Any]) -> None:
		...

	def merge_method(self, method: dict[str, Any]) -> None:
		...

	def merge_has_concept(
		self,
		paper_id: str,
		concept_id: str,
		weight: float,
		relation: str = "MENTIONS",
		evidence_span: str = "",
		confidence: float = 0.0,
		source: str = "",
	) -> None:
		...

	def merge_has_method(
		self,
		paper_id: str,
		method_id: str,
		weight: float,
		relation: str = "USES",
		evidence_span: str = "",
		confidence: float = 0.0,
		source: str = "",
	) -> None:
		...

	def merge_related_concept(
		self,
		subject_id: str,
		object_id: str,
		relation_type: str,
		evidence_span: str = "",
		confidence: float = 0.0,
		source: str = "",
	) -> None:
		...


@dataclass
class IngestionStats:
	papers_seen: int = 0
	papers_written: int = 0
	citation_edges_written: int = 0
	concept_nodes_written: int = 0
	method_nodes_written: int = 0
	concept_edges_written: int = 0
	method_edges_written: int = 0
	relation_edges_written: int = 0


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


def ingest_extractions_from_metadata_db(
	graph: GraphWriter,
	metadata_db: Any,
	limit: int = 5000,
	create_missing_paper_stubs: bool = True,
) -> IngestionStats:
	"""Ingest extracted concepts/methods into Kuzu semantic KG edges."""
	stats = IngestionStats()
	seen_per_paper: set[tuple[str, str, str]] = set()
	seen_entity_ids_per_paper: set[tuple[str, str]] = set()

	for extraction in metadata_db.list_extraction_results(limit=limit):
		if extraction.get("extraction_status") != "success":
			continue
		if _metadata_blocks_graph_write(extraction):
			continue
		raw_paper_id = str(extraction.get("paper_id") or "")
		if not raw_paper_id:
			continue

		record = None
		if hasattr(metadata_db, "resolve_paper"):
			record = metadata_db.resolve_paper(raw_paper_id)

		if record is None:
			if not create_missing_paper_stubs:
				continue
			record = _extraction_stub_paper(raw_paper_id, extraction)
			graph.merge_paper(record)
			stats.papers_written += 1

		canonical_id = paper_id(record)
		current_concept_ids: set[str] = set()
		for concept in _accepted_entities(extraction, "concepts", legacy_threshold=0.75):
			if not isinstance(concept, dict):
				continue
			node = to_concept_node(concept)
			if not node:
				continue
			key = (canonical_id, "concept", node["id"])
			entity_key = (canonical_id, node["id"])
			if key in seen_per_paper or entity_key in seen_entity_ids_per_paper:
				continue
			seen_per_paper.add(key)
			seen_entity_ids_per_paper.add(entity_key)
			graph.merge_concept(node)
			_merge_has_concept(graph, canonical_id, node["id"], concept, extraction)
			current_concept_ids.add(node["id"])
			stats.concept_nodes_written += 1
			stats.concept_edges_written += 1

		for method in _accepted_entities(extraction, "methods", legacy_threshold=0.70):
			if not isinstance(method, dict):
				continue
			node = to_method_node(method)
			if not node:
				continue
			key = (canonical_id, "method", node["id"])
			entity_key = (canonical_id, node["id"])
			if key in seen_per_paper or entity_key in seen_entity_ids_per_paper:
				continue
			seen_per_paper.add(key)
			seen_entity_ids_per_paper.add(entity_key)
			graph.merge_method(node)
			_merge_has_method(graph, canonical_id, node["id"], method, extraction)
			stats.method_nodes_written += 1
			stats.method_edges_written += 1

		for relation in _accepted_relations(extraction):
			subject_id = str(relation.get("subject_id") or "")
			object_id = str(relation.get("object_id") or "")
			if not subject_id or not object_id or subject_id == object_id:
				continue
			if subject_id not in current_concept_ids or object_id not in current_concept_ids:
				continue
			if hasattr(graph, "merge_related_concept"):
				_merge_related_concept(graph, relation)
				stats.relation_edges_written += 1

	return stats


def to_concept_node(concept: dict[str, Any]) -> dict[str, Any] | None:
	label = _clean_label(concept.get("label"))
	if not label:
		return None
	openalex_id = concept.get("openAlex_id") or concept.get("openalx_id") or concept.get("openalex_id")
	concept_id = str(concept.get("canonical_id") or f"concept:{_stable_id(str(openalex_id or label))}")
	return {
		"id": concept_id,
		"label": _clean_label(concept.get("canonical_label") or label),
		"aliases": _aliases(concept, label),
		"domain": str(concept.get("domain") or ""),
		"openAlex_id": str(openalex_id) if openalex_id else "",
		"custom": not bool(openalex_id),
	}


def to_method_node(method: dict[str, Any]) -> dict[str, Any] | None:
	label = _clean_label(method.get("label"))
	if not label:
		return None
	return {
		"id": str(method.get("canonical_id") or f"method:{_stable_id(label)}"),
		"label": _clean_label(method.get("canonical_label") or label),
		"domain": str(method.get("domain") or ""),
		"description": str(method.get("description") or method.get("context") or ""),
	}


def _extraction_stub_paper(raw_paper_id: str, extraction: dict[str, Any]) -> dict[str, Any]:
	coverage = extraction.get("temporal_coverage") or {}
	year = coverage.get("paper_year") if isinstance(coverage, dict) else None
	node = to_reference_stub_paper_node(raw_paper_id)
	node["source"] = "extraction_stub"
	try:
		node["year"] = int(year) if year is not None else None
	except (TypeError, ValueError):
		node["year"] = None
	return node


def _clean_label(value: Any) -> str:
	return re.sub(r"\s+", " ", normalize_scientific_text(value)).strip(" .,:;()[]{}")[:160]


def _stable_id(value: str) -> str:
	slug = slugify_label(value)
	if slug:
		return slug[:96]
	return hashlib.sha1(normalize_scientific_text(value).encode("utf-8")).hexdigest()[:16]


def _aliases(entity: dict[str, Any], label: str) -> list[str]:
	aliases = [label]
	for key in ("aliases", "alias", "openalx_label", "openAlex_label"):
		value = entity.get(key)
		if isinstance(value, list):
			aliases.extend(str(item) for item in value if item)
		elif value:
			aliases.append(str(value))
	seen: set[str] = set()
	output: list[str] = []
	for alias in aliases:
		clean = _clean_label(alias)
		key = clean.lower()
		if clean and key not in seen:
			seen.add(key)
			output.append(clean)
	return output


def _weight(entity: dict[str, Any]) -> float:
	try:
		return float(entity.get("confidence"))
	except (TypeError, ValueError):
		return 1.0


def _entity_evidence(entity: dict[str, Any]) -> str:
	return str(entity.get("evidence_span") or entity.get("evidence") or entity.get("context") or "")[:360]


def _entity_source(entity: dict[str, Any]) -> str:
	return str(entity.get("source_type") or entity.get("evidence_role") or entity.get("source") or "")


def _paper_concept_relation(extraction: dict[str, Any], concept: dict[str, Any]) -> str:
	paper_type = str(extraction.get("paper_type") or "").lower()
	evidence_role = str(concept.get("evidence_role") or "").lower()
	salience = str(concept.get("salience") or "").lower()
	if paper_type == "survey" and salience in {"central", "supporting"}:
		return "REVIEWS"
	if evidence_role in {"metric", "theory", "domain_concept", "method_family"}:
		return "DISCUSSES"
	return "MENTIONS"


def _paper_method_relation(extraction: dict[str, Any], method: dict[str, Any]) -> str:
	paper_type = str(extraction.get("paper_type") or "").lower()
	source_type = str(method.get("source_type") or "").lower()
	if source_type == "paper_contribution":
		return "INTRODUCES"
	if paper_type == "survey" or source_type == "reviewed_method":
		return "REVIEWS"
	if source_type in {"baseline", "background"}:
		return "MENTIONS"
	return "USES"


def _merge_has_concept(
	graph: GraphWriter,
	paper_id_value: str,
	concept_id: str,
	concept: dict[str, Any],
	extraction: dict[str, Any],
) -> None:
	try:
		graph.merge_has_concept(
			paper_id_value,
			concept_id,
			_weight(concept),
			_paper_concept_relation(extraction, concept),
			_entity_evidence(concept),
			_weight(concept),
			_entity_source(concept),
		)
	except TypeError:
		graph.merge_has_concept(paper_id_value, concept_id, _weight(concept))


def _merge_has_method(
	graph: GraphWriter,
	paper_id_value: str,
	method_id: str,
	method: dict[str, Any],
	extraction: dict[str, Any],
) -> None:
	try:
		graph.merge_has_method(
			paper_id_value,
			method_id,
			_weight(method),
			_paper_method_relation(extraction, method),
			_entity_evidence(method),
			_weight(method),
			_entity_source(method),
		)
	except TypeError:
		graph.merge_has_method(paper_id_value, method_id, _weight(method))


def _accepted_entities(
	extraction: dict[str, Any],
	field: str,
	legacy_threshold: float,
) -> list[dict[str, Any]]:
	"""Return KG-safe extraction entities.

	New rows store only accepted entities in concepts/methods. Legacy rows may
	contain deterministic high-recall scan results, so filter those
	conservatively at graph-ingest time.
	"""
	items = extraction.get(field) or []
	if not isinstance(items, list):
		return []
	candidate_field = "concept_candidates" if field == "concepts" else "method_candidates"
	has_candidate_split = candidate_field in extraction and extraction.get(candidate_field) is not None
	if has_candidate_split:
		return [
			item
			for item in items
			if isinstance(item, dict)
			and not item.get("auto_detected")
			and item.get("candidate_source") != "deterministic_scan"
			and _review_allows_graph_write(item)
		]

	output: list[dict[str, Any]] = []
	for item in items:
		if not isinstance(item, dict):
			continue
		if item.get("auto_detected") or item.get("candidate_source") == "deterministic_scan":
			continue
		try:
			confidence = float(item.get("confidence"))
		except (TypeError, ValueError):
			confidence = 1.0
		if confidence < legacy_threshold:
			continue
		output.append(item)
	return output


def _review_allows_graph_write(entity: dict[str, Any]) -> bool:
	"""Production KG writes only explicitly approved entities."""
	if entity.get("accepted_for_kg_write") is False:
		return False
	status = entity.get("review_status")
	status_text = str(status).strip().lower()
	return status_text == "approved"


def _metadata_blocks_graph_write(extraction: dict[str, Any]) -> bool:
	"""Skip semantic KG writes when the paper identity is known-invalid."""
	payload = _raw_payload(extraction)
	status = str(
		extraction.get("metadata_status")
		or payload.get("metadata_status")
		or ""
	).lower()
	if status == "invalid":
		return True
	blocking_errors = extraction.get("blocking_errors") or payload.get("blocking_errors") or []
	return bool(blocking_errors)


def _raw_payload(extraction: dict[str, Any]) -> dict[str, Any]:
	raw = extraction.get("raw_response")
	if not raw:
		return {}
	try:
		payload = json.loads(str(raw))
	except (TypeError, json.JSONDecodeError):
		return {}
	return payload if isinstance(payload, dict) else {}


def _accepted_relations(extraction: dict[str, Any]) -> list[dict[str, Any]]:
	relations = extraction.get("relations") or []
	if not isinstance(relations, list):
		return []
	output: list[dict[str, Any]] = []
	for relation in relations:
		if not isinstance(relation, dict):
			continue
		if str(relation.get("review_status") or "").lower() != "approved":
			continue
		if not relation.get("evidence_span"):
			continue
		output.append(relation)
	return output


def _merge_related_concept(graph: GraphWriter, relation: dict[str, Any]) -> None:
	subject_id = str(relation.get("subject_id") or "")
	object_id = str(relation.get("object_id") or "")
	relation_type = str(relation.get("relation_type") or "RELATED_TO")
	evidence_span = str(relation.get("evidence_span") or "")
	source = str(relation.get("source") or "")
	try:
		confidence = float(relation.get("confidence") or 0.0)
	except (TypeError, ValueError):
		confidence = 0.0
	try:
		graph.merge_related_concept(
			subject_id,
			object_id,
			relation_type,
			evidence_span,
			confidence,
			source,
		)
	except TypeError:
		graph.merge_related_concept(subject_id, object_id, relation_type)


def ingest_from_metadata_db(
	graph: GraphWriter,
	metadata_db: Any,
	limit: int = 1000,
	offset: int = 0,
	include_extractions: bool = False,
) -> IngestionStats:
	records = metadata_db.list_papers(limit=limit, offset=offset)
	stats = ingest_records(graph, records)
	if include_extractions:
		semantic_stats = ingest_extractions_from_metadata_db(graph, metadata_db, limit=limit)
		stats.concept_nodes_written += semantic_stats.concept_nodes_written
		stats.method_nodes_written += semantic_stats.method_nodes_written
		stats.concept_edges_written += semantic_stats.concept_edges_written
		stats.method_edges_written += semantic_stats.method_edges_written
		stats.relation_edges_written += semantic_stats.relation_edges_written
	return stats
