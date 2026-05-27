from __future__ import annotations

import json
import re
from dataclasses import dataclass, field as dataclass_field
from typing import Any, Iterable

from graph.paper_ingestion import extract_citation_ids, paper_id
from storage.metadata_db import MetadataDB


STOPWORDS = {
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "by",
    "for",
    "from",
    "how",
    "in",
    "is",
    "of",
    "on",
    "or",
    "paper",
    "papers",
    "show",
    "that",
    "the",
    "to",
    "use",
    "used",
    "uses",
    "using",
    "what",
    "which",
    "with",
}

LOW_SIGNAL_TERMS = {
    "ai",
    "algorithm",
    "algorithms",
    "artificial",
    "intelligence",
    "large",
    "language",
    "llm",
    "llms",
    "method",
    "methods",
    "model",
    "models",
    "system",
    "systems",
}

QUERY_SYNONYMS = {
    "clinic": ["clinical", "clinics", "clinician", "clinicians"],
    "clinics": ["clinical", "clinic", "clinician", "clinicians"],
    "clinical": ["clinic", "clinics", "clinician", "clinicians"],
    "doctor": ["clinical", "clinician", "physician"],
    "doctors": ["clinical", "clinicians", "physicians"],
    "physician": ["clinical", "clinician", "doctor"],
    "physicians": ["clinical", "clinicians", "doctors"],
}


@dataclass(frozen=True)
class Source:
    paper_id: str
    title: str = ""
    year: int | None = None
    doi: str | None = None
    url: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "year": self.year,
            "doi": self.doi,
            "url": self.url,
        }


@dataclass(frozen=True)
class Evidence:
    paper_id: str
    kind: str
    text: str
    score: float
    field: str | None = None
    metadata: dict[str, Any] = dataclass_field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "kind": self.kind,
            "text": self.text,
            "score": round(float(self.score), 4),
            "field": self.field,
            "metadata": self.metadata,
        }


@dataclass
class SearchHit:
    source: Source
    evidence: list[Evidence] = dataclass_field(default_factory=list)
    score: float = 0.0

    def add_evidence(self, evidence: Evidence) -> None:
        self.evidence.append(evidence)
        self.score += float(evidence.score)

    def to_dict(self) -> dict[str, Any]:
        ordered = sorted(self.evidence, key=lambda item: item.score, reverse=True)
        return {
            "source": self.source.to_dict(),
            "score": round(float(self.score), 4),
            "evidence": [item.to_dict() for item in ordered],
        }


class KGRetriever:
    """
    Deterministic local retrieval over the ScienceKG DuckDB/Kuzu state.

    DuckDB is the source of truth for Phase 1-3 metadata and extraction
    history. Kuzu is intentionally optional so query features remain usable
    on Python versions or machines where the Kuzu wheel is unavailable.
    """

    def __init__(
        self,
        metadata_db_path: str = "data/metadata.duckdb",
        graph_db_path: str = "data/graphs/global_kg",
        max_papers: int = 5000,
        max_extractions: int = 5000,
    ) -> None:
        self.metadata_db_path = metadata_db_path
        self.graph_db_path = graph_db_path
        self.max_papers = int(max_papers)
        self.max_extractions = int(max_extractions)

    def search(
        self,
        query: str,
        limit: int = 10,
        include_extractions: bool = True,
    ) -> list[SearchHit]:
        tokens = _query_tokens(query)
        if not tokens:
            return []

        hits: dict[str, SearchHit] = {}
        paper_cache: dict[str, dict[str, Any]] = {}

        with MetadataDB(self.metadata_db_path) as db:
            papers = db.list_papers(limit=self.max_papers)
            for record in papers:
                pid = paper_id(record)
                paper_cache[pid] = record
                self._add_paper_evidence(hits, query, tokens, record)

            if include_extractions:
                for extraction in db.list_extraction_results(limit=self.max_extractions):
                    raw_pid = str(extraction.get("paper_id") or "")
                    if not raw_pid:
                        continue
                    resolved = db.resolve_paper(raw_pid) if hasattr(db, "resolve_paper") else None
                    pid = paper_id(resolved) if resolved is not None else raw_pid
                    record = paper_cache.get(pid) or resolved or db.get_paper(pid) or {"id": pid}
                    paper_cache[pid] = record
                    for evidence in _evidence_from_extraction(extraction, query, tokens):
                        evidence = Evidence(
                            paper_id=pid,
                            kind=evidence.kind,
                            text=evidence.text,
                            score=evidence.score,
                            field=evidence.field,
                            metadata={**evidence.metadata, "raw_extraction_paper_id": raw_pid},
                        )
                        self._hit_for(hits, record, pid).add_evidence(evidence)

        ordered = sorted(hits.values(), key=lambda item: item.score, reverse=True)
        return ordered[: max(0, int(limit))]

    def paper_detail(self, paper_id_value: str) -> dict[str, Any] | None:
        with MetadataDB(self.metadata_db_path) as db:
            record = _find_paper(db, paper_id_value)
            if record is None:
                return None
            extractions = db.get_paper_extractions(paper_id(record), limit=50)

        latest = _latest_successful_extraction(extractions)
        return {
            "paper": record,
            "source": _source_from_paper(record).to_dict(),
            "latest_extraction": latest,
            "extractions": extractions,
        }

    def paper_neighborhood(self, paper_id_value: str, limit: int = 20) -> dict[str, Any] | None:
        with MetadataDB(self.metadata_db_path) as db:
            record = _find_paper(db, paper_id_value)
            if record is None:
                return None
            papers = db.list_papers(limit=self.max_papers)

        pid = paper_id(record)
        by_id = {paper_id(item): item for item in papers}
        source_refs = set(extract_citation_ids(record))
        citations = [
            _paper_stub_or_source(ref_id, by_id)
            for ref_id in sorted(source_refs)
        ][:limit]

        cited_by = []
        similar_candidates = []
        for other in papers:
            other_id = paper_id(other)
            if other_id == pid:
                continue
            other_refs = set(extract_citation_ids(other))
            if pid in other_refs:
                cited_by.append(_source_from_paper(other).to_dict())
            if source_refs and other_refs:
                shared = source_refs & other_refs
                if shared:
                    score = len(shared) / max(len(source_refs | other_refs), 1)
                    similar_candidates.append(
                        {
                            "source": _source_from_paper(other).to_dict(),
                            "score": round(score, 4),
                            "shared_references": sorted(shared),
                            "type": "citation_overlap",
                        }
                    )

        similar_candidates.sort(key=lambda item: item["score"], reverse=True)
        return {
            "paper_id": pid,
            "citations": citations[:limit],
            "cited_by": cited_by[:limit],
            "similar": similar_candidates[:limit],
        }

    def cypher(
        self,
        query: str,
        parameters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """
        Run an explicit Kuzu Cypher query when Kuzu is installed.

        This is not used for generated Cypher. It is a small escape hatch for
        callers that already know the graph query they want to run.
        """
        try:
            import kuzu  # type: ignore
        except ImportError:
            return []

        database = kuzu.Database(self.graph_db_path)
        connection = kuzu.Connection(database)
        result = connection.execute(query, parameters or {})
        if hasattr(result, "get_as_df"):
            return result.get_as_df().to_dict("records")
        if hasattr(result, "fetchall"):
            rows = result.fetchall()
            return [{"row": row} for row in rows]
        return []

    def _add_paper_evidence(
        self,
        hits: dict[str, SearchHit],
        query: str,
        tokens: list[str],
        record: dict[str, Any],
    ) -> None:
        pid = paper_id(record)
        title = str(record.get("title") or "")
        abstract = str(record.get("abstract") or "")
        doi = str(record.get("doi") or "")
        score = (
            _score_text(query, tokens, title, weight=4.0)
            + _score_text(query, tokens, abstract, weight=1.5)
            + _score_text(query, tokens, doi, weight=2.0)
        )
        if score <= 0:
            return
        text = _snippet(" ".join(item for item in [title, abstract] if item), tokens)
        self._hit_for(hits, record, pid).add_evidence(
            Evidence(
                paper_id=pid,
                kind="paper",
                field="metadata",
                text=text or title or pid,
                score=score,
            )
        )

    def _hit_for(
        self,
        hits: dict[str, SearchHit],
        record: dict[str, Any],
        fallback_id: str,
    ) -> SearchHit:
        pid = paper_id(record) if record.get("source") and record.get("source_id") else str(record.get("id") or fallback_id)
        if pid not in hits:
            hits[pid] = SearchHit(source=_source_from_paper({**record, "id": pid}))
        return hits[pid]


def _find_paper(db: MetadataDB, paper_id_value: str) -> dict[str, Any] | None:
    if hasattr(db, "resolve_paper"):
        resolved = db.resolve_paper(paper_id_value)
        if resolved is not None:
            return resolved
    direct = db.get_paper(paper_id_value)
    if direct is not None:
        return direct
    for record in db.list_papers(limit=10000):
        if paper_id(record) == paper_id_value:
            return record
    return None


def _latest_successful_extraction(extractions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for extraction in extractions:
        if extraction.get("extraction_status") == "success":
            return extraction
    return extractions[0] if extractions else None


def _paper_stub_or_source(ref_id: str, papers_by_id: dict[str, dict[str, Any]]) -> dict[str, Any]:
    record = papers_by_id.get(ref_id)
    if record is not None:
        return _source_from_paper(record).to_dict()
    return {"paper_id": ref_id, "title": "", "year": None, "doi": None, "url": None}


def _source_from_paper(record: dict[str, Any]) -> Source:
    pid = str(record.get("id") or paper_id(record) if record.get("source") and record.get("source_id") else record.get("id") or "")
    return Source(
        paper_id=pid,
        title=str(record.get("title") or ""),
        year=_coerce_int(record.get("year")),
        doi=str(record.get("doi")) if record.get("doi") else None,
        url=str(record.get("landing_page_url") or record.get("pdf_url") or "") or None,
    )


def _evidence_from_extraction(
    extraction: dict[str, Any],
    query: str,
    tokens: list[str],
) -> list[Evidence]:
    pid = str(extraction.get("paper_id") or "")
    evidence: list[Evidence] = []
    fields = [
        ("concepts", "concept", 3.0),
        ("methods", "method", 3.0),
        ("claims", "claim", 2.5),
        ("cross_domain_hints", "cross_domain_hint", 2.0),
        ("terminology_conflicts", "terminology_conflict", 1.5),
    ]

    for field_name, kind, weight in fields:
        for item in _iter_items(extraction.get(field_name)):
            text = _item_text(item)
            score = _score_text(query, tokens, text, weight=weight)
            if score <= 0:
                continue
            evidence.append(
                Evidence(
                    paper_id=pid,
                    kind=kind,
                    field=field_name,
                    text=_snippet(text, tokens) or text,
                    score=score,
                    metadata=item if isinstance(item, dict) else {},
                )
            )
    return evidence


def _iter_items(value: Any) -> Iterable[Any]:
    if isinstance(value, str):
        parsed = _parse_json(value)
        if parsed is not None:
            value = parsed
    if isinstance(value, list):
        return value
    if isinstance(value, dict):
        return [value]
    return []


def _item_text(item: Any) -> str:
    if isinstance(item, dict):
        preferred = [
            "label",
            "statement",
            "context",
            "description",
            "domain",
            "field",
            "why_applicable",
            "term",
            "this_field",
            "other_field",
            "evidence_type",
        ]
        parts = [str(item.get(key) or "") for key in preferred]
        parts.extend(
            str(value)
            for key, value in item.items()
            if key not in preferred and isinstance(value, (str, int, float, bool))
        )
        return " ".join(part for part in parts if part).strip()
    return str(item or "").strip()


def _parse_json(value: str) -> Any | None:
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return None


def _tokenize(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9_-]*", (text or "").lower())
    return [token for token in tokens if token not in STOPWORDS and len(token) > 1]


def _query_tokens(text: str) -> list[str]:
    tokens = _tokenize(text)
    expanded: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        for item in [token, *QUERY_SYNONYMS.get(token, [])]:
            if item not in seen:
                seen.add(item)
                expanded.append(item)
    return expanded


def _normalize(text: str) -> str:
    return " ".join(_tokenize(text))


def _score_text(query: str, tokens: list[str], text: str, weight: float) -> float:
    if not text:
        return 0.0
    text_tokens = set(_tokenize(text))
    if not text_tokens:
        return 0.0

    matched = [token for token in tokens if token in text_tokens]
    if not matched:
        return 0.0

    query_has_specific_terms = any(token not in LOW_SIGNAL_TERMS for token in tokens)
    matched_specific_terms = [token for token in matched if token not in LOW_SIGNAL_TERMS]
    if query_has_specific_terms and not matched_specific_terms:
        return 0.0

    score = weight * (
        len(matched_specific_terms)
        + 0.25 * (len(matched) - len(matched_specific_terms))
    )
    query_norm = _normalize(query)
    text_norm = _normalize(text)
    if query_norm and query_norm in text_norm:
        score += weight * max(2, len(tokens))
    return float(score)


def _snippet(text: str, tokens: list[str], max_chars: int = 360) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if len(clean) <= max_chars:
        return clean

    lower = clean.lower()
    position = -1
    for token in tokens:
        position = lower.find(token.lower())
        if position != -1:
            break
    if position == -1:
        return clean[: max_chars - 3].rstrip() + "..."

    start = max(0, position - max_chars // 3)
    end = min(len(clean), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix


def _coerce_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
