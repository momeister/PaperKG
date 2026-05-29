from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parsing.marker_parser import MarkerParser

_PDF_TEXT_CACHE: dict[tuple[str, float, int], str] = {}


@dataclass(frozen=True)
class EvidenceLocation:
    paper_id: str
    kind: str
    field: str | None
    reference_text: str
    pdf_excerpt: str = ""
    matched_terms: list[str] = field(default_factory=list)
    found_in_pdf_text: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "kind": self.kind,
            "field": self.field,
            "reference_text": self.reference_text,
            "pdf_excerpt": self.pdf_excerpt,
            "matched_terms": self.matched_terms,
            "found_in_pdf_text": self.found_in_pdf_text,
            "metadata": self.metadata,
        }


@dataclass(frozen=True)
class SourceVerification:
    paper_id: str
    title: str
    pdf_available: bool
    pdf_path: str | None = None
    pdf_filename: str | None = None
    pdf_error: str | None = None
    evidence: list[EvidenceLocation] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "paper_id": self.paper_id,
            "title": self.title,
            "pdf_available": self.pdf_available,
            "pdf_path": self.pdf_path,
            "pdf_filename": self.pdf_filename,
            "pdf_error": self.pdf_error,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class VerificationReport:
    sources: list[SourceVerification]
    cited_paper_ids: list[str]
    missing_source_ids: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "sources": [source.to_dict() for source in self.sources],
            "cited_paper_ids": self.cited_paper_ids,
            "missing_source_ids": self.missing_source_ids,
        }


def verify_answer_sources(
    answer_payload: dict[str, Any],
    pdf_base_dir: str = "data/pdfs",
    parse_pdfs: bool = True,
    max_sources: int = 10,
    max_evidence_per_source: int = 5,
) -> VerificationReport:
    sources = [
        source for source in (answer_payload.get("sources") or [])
        if isinstance(source, dict)
    ][:max_sources]
    evidence = [
        item for item in (answer_payload.get("evidence") or [])
        if isinstance(item, dict)
    ]
    cited_paper_ids = sorted(_cited_paper_ids(str(answer_payload.get("answer") or "")))
    source_ids = {str(source.get("paper_id") or "") for source in sources}
    missing_source_ids = [paper_id for paper_id in cited_paper_ids if paper_id not in source_ids]

    verifications: list[SourceVerification] = []
    for source in sources:
        paper_id = str(source.get("paper_id") or "")
        title = str(source.get("title") or paper_id)
        pdf_path = find_pdf_path(paper_id, title, pdf_base_dir)
        pdf_text = ""
        pdf_error = None
        if pdf_path and parse_pdfs:
            try:
                pdf_text = parse_pdf_text(pdf_path, paper_id)
            except Exception as exc:
                pdf_error = str(exc)

        source_evidence = [
            item for item in evidence
            if str(item.get("paper_id") or "") == paper_id
        ][:max_evidence_per_source]
        locations = [
            locate_evidence(item, pdf_text)
            for item in source_evidence
        ]
        verifications.append(
            SourceVerification(
                paper_id=paper_id,
                title=title,
                pdf_available=pdf_path is not None,
                pdf_path=pdf_path,
                pdf_filename=Path(pdf_path).name if pdf_path else None,
                pdf_error=pdf_error,
                evidence=locations,
            )
        )

    return VerificationReport(
        sources=verifications,
        cited_paper_ids=cited_paper_ids,
        missing_source_ids=missing_source_ids,
    )


def locate_evidence(evidence: dict[str, Any], pdf_text: str = "") -> EvidenceLocation:
    reference = reference_text(evidence)
    excerpt = best_excerpt(pdf_text, reference) if pdf_text else ""
    terms = highlightable_terms(reference)
    return EvidenceLocation(
        paper_id=str(evidence.get("paper_id") or ""),
        kind=str(evidence.get("kind") or "evidence"),
        field=str(evidence.get("field")) if evidence.get("field") else None,
        reference_text=reference,
        pdf_excerpt=excerpt,
        matched_terms=[term for term in terms if term in excerpt.lower()] if excerpt else [],
        found_in_pdf_text=bool(excerpt),
        metadata=evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {},
    )


def find_pdf_path(paper_id: str, title: str = "", pdf_base_dir: str = "data/pdfs") -> str | None:
    base = Path(pdf_base_dir)
    if not base.exists():
        return None

    candidates = sorted(base.rglob("*.pdf"))
    for token in pdf_lookup_tokens(paper_id, title):
        token_lower = token.lower()
        for candidate in candidates:
            if token_lower in str(candidate).lower():
                return str(candidate)
    return None


def pdf_lookup_tokens(paper_id: str, title: str = "") -> list[str]:
    values = [paper_id]
    if ":" in paper_id:
        values.append(paper_id.split(":", 1)[1])
    if title:
        words = re.findall(r"[a-z0-9]+", title.lower())
        if words:
            values.append("-".join(words[:8]))
            values.append("_".join(words[:8]))

    tokens: list[str] = []
    for value in values:
        clean = re.sub(r"[^A-Za-z0-9._-]+", "-", str(value)).strip("-").lower()
        if len(clean) >= 4 and clean not in tokens:
            tokens.append(clean)
    return tokens


def parse_pdf_text(pdf_path: str, paper_id: str) -> str:
    path = Path(pdf_path)
    stat = path.stat()
    cache_key = (str(path.resolve()), stat.st_mtime, stat.st_size)
    cached = _PDF_TEXT_CACHE.get(cache_key)
    if cached is not None:
        return cached
    parsed = MarkerParser().parse(pdf_path, paper_id)
    if len(_PDF_TEXT_CACHE) >= 8:
        _PDF_TEXT_CACHE.clear()
    _PDF_TEXT_CACHE[cache_key] = parsed.text
    return parsed.text


def reference_text(evidence: dict[str, Any]) -> str:
    metadata = evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {}
    authors = metadata.get("authors")
    author_text = ""
    if isinstance(authors, list):
        author_text = ", ".join(str(author) for author in authors[:12] if author)
    elif authors:
        author_text = str(authors)
    preferred = [
        "title",
        "abstract",
        "evidence_span",
        "context",
        "statement",
        "description",
        "why_applicable",
        "label",
    ]
    parts = [str(metadata.get(key) or "") for key in preferred]
    if author_text:
        parts.insert(1, author_text)
    parts.append(str(evidence.get("text") or ""))
    return re.sub(r"\s+", " ", " ".join(part for part in parts if part)).strip()


def best_excerpt(pdf_text: str, reference: str, window_chars: int = 1000) -> str:
    clean = re.sub(r"\s+", " ", pdf_text or "").strip()
    reference_clean = re.sub(r"\s+", " ", reference or "").strip()
    if not clean or not reference_clean:
        return ""

    exact = _find_longest_substring(clean, reference_clean)
    if exact >= 0:
        start = max(0, exact - window_chars // 3)
        end = min(len(clean), exact + window_chars)
        return clean[start:end].strip()

    tokens = highlightable_terms(reference_clean)
    if not tokens:
        return clean[:window_chars]

    best_start = 0
    best_score = -1
    step = max(window_chars // 3, 200)
    lower = clean.lower()
    for start in range(0, max(len(clean) - window_chars, 1), step):
        window = lower[start : start + window_chars]
        score = sum(1 for token in tokens if token in window)
        if score > best_score:
            best_score = score
            best_start = start
    if best_score <= 0:
        return ""
    return clean[best_start : best_start + window_chars].strip()


def highlightable_terms(text: str) -> list[str]:
    stopwords = {
        "about",
        "also",
        "and",
        "are",
        "for",
        "from",
        "into",
        "that",
        "the",
        "this",
        "used",
        "using",
        "with",
    }
    terms = re.findall(r"[A-Za-z0-9][A-Za-z0-9-]{2,}", text or "")
    unique: list[str] = []
    for term in sorted(terms, key=len, reverse=True):
        lower = term.lower()
        if lower in stopwords or lower in unique:
            continue
        unique.append(lower)
    return unique


def _find_longest_substring(text: str, reference: str) -> int:
    lower = text.lower()
    reference_lower = reference.lower()
    chunks = [
        reference_lower[index : index + 120]
        for index in range(0, max(len(reference_lower) - 120, 1), 80)
    ]
    chunks.append(reference_lower[:120])
    for chunk in sorted(set(chunks), key=len, reverse=True):
        chunk = chunk.strip()
        if len(chunk) < 30:
            continue
        position = lower.find(chunk)
        if position >= 0:
            return position
    return -1


def _cited_paper_ids(answer_text: str) -> set[str]:
    ids: set[str] = set()
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        for value in re.split(r"[,;]\s*", bracketed):
            value = value.strip()
            if value.startswith("arxiv:") or value.startswith("doi:") or value.startswith("p"):
                ids.add(value)
    return ids
