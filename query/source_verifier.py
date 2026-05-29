from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parsing.marker_parser import MarkerParser

_PDF_TEXT_CACHE: dict[tuple[str, float, int], str] = {}
MAX_REFERENCE_CHARS = 220
DEFAULT_EXCERPT_CHARS = 260


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
        ]
        locations: list[EvidenceLocation] = []
        for item in source_evidence:
            remaining = max_evidence_per_source - len(locations)
            if remaining <= 0:
                break
            locations.extend(locate_evidence_fragments(item, pdf_text, max_fragments=remaining))
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
    return locate_evidence_fragments(evidence, pdf_text, max_fragments=1)[0]


def locate_evidence_fragments(
    evidence: dict[str, Any],
    pdf_text: str = "",
    max_fragments: int = 3,
) -> list[EvidenceLocation]:
    fragments = reference_fragments(evidence, max_fragments=max_fragments) or [reference_text(evidence)]
    locations: list[EvidenceLocation] = []
    for reference in fragments[:max_fragments]:
        locations.append(_location_for_reference(evidence, reference, pdf_text))
    return locations or [_location_for_reference(evidence, reference_text(evidence), pdf_text)]


def _location_for_reference(evidence: dict[str, Any], reference: str, pdf_text: str = "") -> EvidenceLocation:
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
    fragments = reference_fragments(evidence, max_fragments=1)
    return fragments[0] if fragments else ""


def reference_fragments(evidence: dict[str, Any], max_fragments: int = 3) -> list[str]:
    metadata = evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {}
    kind = str(evidence.get("kind") or "").lower()
    title = str(metadata.get("title") or "")
    preferred_anchor_keys = [
        "evidence_span",
        "statement",
        "context",
        "description",
        "why_applicable",
    ]
    anchor_parts: list[str] = []
    if kind == "paper":
        anchor_parts.append(str(metadata.get("abstract") or ""))
    anchor_parts.extend(str(metadata.get(key) or "") for key in preferred_anchor_keys)

    fragments: list[str] = []
    for part in anchor_parts:
        for fragment in _short_reference_fragments(_remove_title_prefix(part, title)):
            if _is_duplicate_fragment(fragment, fragments):
                continue
            fragments.append(fragment)
            if len(fragments) >= max_fragments:
                return fragments
    if fragments:
        return fragments

    direct_parts = [str(evidence.get("text") or ""), str(metadata.get("label") or "")]
    for part in direct_parts:
        for fragment in _short_reference_fragments(_remove_title_prefix(part, title)):
            if _is_duplicate_fragment(fragment, fragments):
                continue
            fragments.append(fragment)
            if len(fragments) >= max_fragments:
                return fragments
    if fragments:
        return fragments

    # Metadata-only evidence can still be useful, but keep title-only anchors
    # as the absolute last resort; they highlight title pages instead of the
    # sentence that carries the actual claim.
    fallback_parts = [str(metadata.get("abstract") or ""), title]
    for part in fallback_parts:
        for fragment in _short_reference_fragments(_remove_title_prefix(part, title)):
            if _is_duplicate_fragment(fragment, fragments):
                continue
            fragments.append(fragment)
            if len(fragments) >= max_fragments:
                return fragments
    return fragments


def best_excerpt(pdf_text: str, reference: str, window_chars: int = DEFAULT_EXCERPT_CHARS) -> str:
    clean = re.sub(r"\s+", " ", pdf_text or "").strip()
    reference_clean = re.sub(r"\s+", " ", reference or "").strip()
    if not clean or not reference_clean:
        return ""

    exact = _find_longest_substring(clean, reference_clean)
    if exact is not None:
        position, length = exact
        matched = clean[position : position + length].strip()
        if _is_complete_sentence(matched):
            return matched
        return _excerpt_around(clean, position, length, window_chars)

    tokens = highlightable_terms(reference_clean)
    if not tokens:
        return _truncate_at_sentence(clean, window_chars)

    best_start = 0
    best_score = -1
    step = max(window_chars // 2, 120)
    lower = clean.lower()
    for start in range(0, max(len(clean) - window_chars, 1), step):
        window = lower[start : start + window_chars]
        score = sum(1 for token in tokens if token in window)
        if score > best_score:
            best_score = score
            best_start = start
    if best_score <= 0:
        return ""
    return _excerpt_around(clean, best_start, min(window_chars, len(clean) - best_start), window_chars)


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


def _short_reference_fragments(text: str, max_chars: int = MAX_REFERENCE_CHARS) -> list[str]:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    if not clean:
        return []

    fragments: list[str] = []
    for sentence in _sentences(clean):
        for fragment in _split_long_sentence(sentence, max_chars):
            if fragment:
                fragments.append(fragment)
    return [fragment for fragment in fragments if fragment]


def _sentences(text: str) -> list[str]:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if not clean:
        return []
    matches = re.findall(r"[^.!?]+(?:[.!?]+(?=\s|$)|$)", clean)
    return [match.strip() for match in matches if match.strip()] or [clean]


def _truncate_at_sentence(text: str, max_chars: int) -> str:
    clean = re.sub(r"\s+", " ", text or "").strip()
    if len(clean) <= max_chars:
        return clean
    boundary = max(clean.rfind(". ", 0, max_chars), clean.rfind("! ", 0, max_chars), clean.rfind("? ", 0, max_chars))
    if boundary >= max(80, max_chars // 2):
        return clean[: boundary + 1].strip()
    return clean[: max_chars - 3].rstrip() + "..."


def _split_long_sentence(sentence: str, max_chars: int) -> list[str]:
    clean = re.sub(r"\s+", " ", sentence or "").strip()
    if not clean:
        return []
    if len(clean) <= max_chars:
        return [clean]
    clauses = [
        clause.strip(" ;:,")
        for clause in re.split(r"(?:;\s+|:\s+|\s+-\s+|\s+\u2013\s+|\s+\u2014\s+)", clean)
        if clause.strip(" ;:,")
    ]
    if len(clauses) > 1:
        output: list[str] = []
        for clause in clauses:
            if len(clause) <= max_chars:
                output.append(clause)
            else:
                output.append(_truncate_at_sentence(clause, max_chars))
        return output
    return [_truncate_at_sentence(clean, max_chars)]


def _is_complete_sentence(text: str) -> bool:
    clean = re.sub(r"\s+", " ", text or "").strip()
    return bool(clean) and clean[-1:] in {".", "!", "?"}


def _remove_title_prefix(text: str, title: str) -> str:
    clean = re.sub(r"\s+", " ", str(text or "")).strip()
    title_clean = re.sub(r"\s+", " ", title or "").strip()
    if not clean or not title_clean:
        return clean
    if clean.lower() == title_clean.lower():
        return ""
    if clean.lower().startswith(title_clean.lower()):
        return clean[len(title_clean):].lstrip(" .:-")
    return clean


def _excerpt_around(text: str, position: int, match_length: int, window_chars: int) -> str:
    half_context = max(40, (window_chars - match_length) // 2)
    raw_start = max(0, position - half_context)
    raw_end = min(len(text), position + match_length + half_context)
    start = _nearest_sentence_start(text, raw_start, position)
    end = _nearest_sentence_end(text, raw_end)
    excerpt = text[start:end].strip()
    if len(excerpt) > window_chars:
        excerpt = _truncate_at_sentence(excerpt, window_chars)
    return excerpt


def _nearest_sentence_start(text: str, raw_start: int, match_start: int) -> int:
    if raw_start <= 0:
        return 0
    candidates = [text.rfind(". ", raw_start, match_start), text.rfind("! ", raw_start, match_start), text.rfind("? ", raw_start, match_start)]
    candidate = max(candidates)
    return candidate + 2 if candidate >= 0 else raw_start


def _nearest_sentence_end(text: str, raw_end: int) -> int:
    if raw_end >= len(text):
        return len(text)
    candidates = [text.find(". ", raw_end), text.find("! ", raw_end), text.find("? ", raw_end)]
    candidates = [candidate + 1 for candidate in candidates if candidate >= 0]
    return min(candidates) if candidates else raw_end


def _is_duplicate_fragment(fragment: str, existing: list[str]) -> bool:
    normalized = re.sub(r"\W+", " ", fragment).strip().lower()
    return any(normalized == re.sub(r"\W+", " ", item).strip().lower() for item in existing)


def _find_longest_substring(text: str, reference: str) -> tuple[int, int] | None:
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
            return position, len(chunk)
    return None


def _cited_paper_ids(answer_text: str) -> set[str]:
    ids: set[str] = set()
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        for value in re.split(r"[,;]\s*", bracketed):
            value = value.strip()
            if value.startswith("arxiv:") or value.startswith("doi:") or value.startswith("p"):
                ids.add(value)
    return ids
