from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from parsing.marker_parser import MarkerParser

_PDF_TEXT_CACHE: dict[tuple[str, float, int], str] = {}
MAX_REFERENCE_CHARS = 220
DEFAULT_EXCERPT_CHARS = 260
# Window for the honest "approximate region" shown when a claim cannot be anchored to an
# exact sentence: better one larger region that contains the supporting text than a
# confident-looking single sentence that does not.
APPROX_REGION_CHARS = 700
# Exact substring matches shorter than this are expanded to their surrounding sentences —
# bare snippets like "prospectively collected and analysed." carry no context. Kept low on
# purpose: complete sentences (~70+ chars) must stay verbatim, only fragments expand.
_MIN_EXCERPT_CHARS = 60

# Characters that PDFs and LLM quotes render differently although they mean the same
# text: typographic quotes/apostrophes, dash variants, soft hyphens, ligatures (the
# NFKD fallback folds those plus accents). A model that "cleans up" such characters
# while quoting verbatim must still verify against the PDF text.
_MATCH_CHAR_EQUIV = {
    "­": "",  # soft hyphen
    "‘": "'",
    "’": "'",
    "‚": "'",
    "′": "'",
    "´": "'",
    "`": "'",
    "“": '"',
    "”": '"',
    "„": '"',
    "«": '"',
    "»": '"',
    "–": "-",
    "—": "-",
    "‐": "-",
    "‑": "-",
    "−": "-",
}

_NORMALIZED_TEXT_CACHE: dict[str, tuple[str, list[int]]] = {}


def _normalized_for_match(text: str) -> tuple[str, list[int]]:
    """Fold text for matching and keep a map from folded indices to original indices."""
    cached = _NORMALIZED_TEXT_CACHE.get(text) if len(text) > 4096 else None
    if cached is not None:
        return cached
    chars: list[str] = []
    index_map: list[int] = []
    for index, original in enumerate(text):
        replacement = _MATCH_CHAR_EQUIV.get(original)
        if replacement is None:
            decomposed = unicodedata.normalize("NFKD", original)
            replacement = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
        replacement = replacement.lower()
        for folded in replacement:
            chars.append(folded)
            index_map.append(index)
    result = ("".join(chars), index_map)
    if len(text) > 4096:
        if len(_NORMALIZED_TEXT_CACHE) >= 6:
            _NORMALIZED_TEXT_CACHE.clear()
        _NORMALIZED_TEXT_CACHE[text] = result
    return result


def _find_normalized(haystack: str, needle: str) -> tuple[int, int] | None:
    """Unicode-folded substring search; returns (start, length) in the original haystack."""
    needle_norm, _ = _normalized_for_match(needle)
    needle_norm = needle_norm.strip()
    if not needle_norm:
        return None
    hay_norm, hay_map = _normalized_for_match(haystack)
    position = hay_norm.find(needle_norm)
    if position < 0:
        return None
    start = hay_map[position]
    end = hay_map[position + len(needle_norm) - 1] + 1
    return start, end - start


def _normalized_contains(haystack: str, needle: str) -> bool:
    return _find_normalized(haystack, needle) is not None


@dataclass(frozen=True)
class EvidenceLocation:
    evidence_id: str
    paper_id: str
    kind: str
    field: str | None
    reference_text: str
    pdf_excerpt: str = ""
    matched_terms: list[str] = field(default_factory=list)
    found_in_pdf_text: bool = False
    source_evidence_index: int | None = None
    fragment_index: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "paper_id": self.paper_id,
            "kind": self.kind,
            "field": self.field,
            "reference_text": self.reference_text,
            "pdf_excerpt": self.pdf_excerpt,
            "matched_terms": self.matched_terms,
            "found_in_pdf_text": self.found_in_pdf_text,
            "source_evidence_index": self.source_evidence_index,
            "fragment_index": self.fragment_index,
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
    max_evidence_per_source: int = 15,
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
            (index, item) for index, item in enumerate(evidence)
            if str(item.get("paper_id") or "") == paper_id
        ]
        locations: list[EvidenceLocation] = []
        for source_evidence_index, item in source_evidence:
            remaining = max_evidence_per_source - len(locations)
            # Always emit at least one location per evidence item so every
            # evidence_id stays resolvable in the frontend, even when the
            # slot budget from earlier (multi-fragment) items is exhausted.
            locations.extend(
                locate_evidence_fragments(
                    item,
                    pdf_text,
                    max_fragments=max(1, remaining),
                    source_evidence_index=source_evidence_index,
                )
            )
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
    source_evidence_index: int | None = None,
) -> list[EvidenceLocation]:
    if _is_claim_excerpt_evidence(evidence):
        return [_claim_excerpt_location(evidence, pdf_text, source_evidence_index)]
    fragments = reference_fragments(evidence, max_fragments=max_fragments) or [reference_text(evidence)]
    locations: list[EvidenceLocation] = []
    for fragment_index, reference in enumerate(fragments[:max_fragments]):
        locations.append(
            _location_for_reference(
                evidence,
                reference,
                pdf_text,
                source_evidence_index=source_evidence_index,
                fragment_index=fragment_index,
            )
        )
    return locations or [
        _location_for_reference(
            evidence,
            reference_text(evidence),
            pdf_text,
            source_evidence_index=source_evidence_index,
            fragment_index=0,
        )
    ]


def _is_claim_excerpt_evidence(evidence: dict[str, Any]) -> bool:
    metadata = evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {}
    if (metadata or {}).get("context_policy") in ("claim_excerpt", "approx_region"):
        return True
    return str(evidence.get("kind") or "") == "pdf" and str(evidence.get("field") or "") in (
        "answer_claim_excerpt",
        "answer_claim_region",
    )


def _claim_excerpt_location(
    evidence: dict[str, Any],
    pdf_text: str = "",
    source_evidence_index: int | None = None,
) -> EvidenceLocation:
    """Pass the excerpt located at answer time through verbatim.

    Claim-excerpt evidence already carries the precise PDF passage in its text
    (located with strict matching, including translation of cross-language claims).
    Re-deriving it from metadata["context"] — the paraphrased answer sentence —
    re-locates non-strict and without translation, which is how wrong passages
    ended up displayed as "Aktive Textstelle".
    """
    metadata = evidence.get("metadata") if isinstance(evidence.get("metadata"), dict) else {}
    excerpt = re.sub(r"\s+", " ", str(evidence.get("text") or "")).strip()
    reference = re.sub(r"\s+", " ", str((metadata or {}).get("context") or "")).strip() or excerpt
    reference = _truncate_at_sentence(reference, MAX_REFERENCE_CHARS)
    normalized_pdf = re.sub(r"\s+", " ", pdf_text or "").lower()
    found = bool(excerpt) and bool(normalized_pdf) and (
        excerpt.lower() in normalized_pdf or _normalized_contains(normalized_pdf, excerpt)
    )
    terms = highlightable_terms(excerpt)
    return EvidenceLocation(
        evidence_id=str(evidence.get("evidence_id") or ""),
        paper_id=str(evidence.get("paper_id") or ""),
        kind=str(evidence.get("kind") or "evidence"),
        field=str(evidence.get("field")) if evidence.get("field") else None,
        reference_text=reference,
        pdf_excerpt=excerpt,
        matched_terms=[term for term in terms if term in excerpt.lower()] if excerpt else [],
        found_in_pdf_text=found,
        source_evidence_index=source_evidence_index,
        fragment_index=0,
        metadata=metadata or {},
    )


def _location_for_reference(
    evidence: dict[str, Any],
    reference: str,
    pdf_text: str = "",
    source_evidence_index: int | None = None,
    fragment_index: int | None = None,
) -> EvidenceLocation:
    # Prefer a confidently anchored excerpt; when none exists, show a LARGER approximate
    # region (flagged for the UI) instead of a confident-looking but wrong single sentence.
    excerpt = ""
    approximate = False
    if pdf_text:
        excerpt = best_excerpt(pdf_text, reference, strict=True)
        if not excerpt:
            region = best_excerpt(pdf_text, reference, window_chars=APPROX_REGION_CHARS)
            if region:
                excerpt = region
                approximate = True
    terms = highlightable_terms(reference)
    raw_metadata = evidence.get("metadata")
    metadata: dict[str, Any] = dict(raw_metadata) if isinstance(raw_metadata, dict) else {}
    if approximate:
        metadata["located"] = "approx_region"
    return EvidenceLocation(
        evidence_id=str(evidence.get("evidence_id") or ""),
        paper_id=str(evidence.get("paper_id") or ""),
        kind=str(evidence.get("kind") or "evidence"),
        field=str(evidence.get("field")) if evidence.get("field") else None,
        reference_text=reference,
        pdf_excerpt=excerpt,
        matched_terms=[term for term in terms if term in excerpt.lower()] if excerpt else [],
        found_in_pdf_text=bool(excerpt),
        source_evidence_index=source_evidence_index,
        fragment_index=fragment_index,
        metadata=metadata,
    )


def find_pdf_path(paper_id: str, title: str = "", pdf_base_dir: str = "data/pdfs") -> str | None:
    import re as _re
    base = Path(pdf_base_dir)
    if not base.exists():
        return None

    candidates = sorted(base.rglob("*.pdf"))
    for token in pdf_lookup_tokens(paper_id, title):
        token_escaped = _re.escape(token.lower())
        pattern = _re.compile(r"(?<![a-z0-9])" + token_escaped + r"(?![a-z0-9])")
        for candidate in candidates:
            stem = Path(candidate).stem.lower()
            if pattern.search(stem):
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
    title = str(metadata.get("title") or "")
    preferred_anchor_keys = [
        "evidence_span",
        "statement",
        "context",
        "description",
        "why_applicable",
    ]
    anchor_parts = [str(metadata.get(key) or "") for key in preferred_anchor_keys]

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


def best_excerpt(
    pdf_text: str,
    reference: str,
    window_chars: int = DEFAULT_EXCERPT_CHARS,
    strict: bool = False,
) -> str:
    clean = re.sub(r"\s+", " ", pdf_text or "").strip()
    reference_clean = re.sub(r"\s+", " ", reference or "").strip()
    if not clean or not reference_clean:
        return ""
    quantitative = _quantitative_tokens(reference_clean)

    exact = _find_longest_substring(clean, reference_clean)
    if exact is not None:
        position, length = exact
        matched = clean[position : position + length].strip()
        if (
            len(matched) >= min(window_chars, _MIN_EXCERPT_CHARS)
            and _is_complete_sentence(matched)
            and _contains_quantitative_tokens(matched, quantitative)
        ):
            return matched
        excerpt = _excerpt_around(clean, position, length, window_chars)
        if _contains_quantitative_tokens(excerpt, quantitative):
            return excerpt

    tokens = highlightable_terms(reference_clean)
    if not tokens:
        return _truncate_at_sentence(clean, window_chars)

    lower = clean.lower()
    if quantitative:
        required_numbers = {token for token in quantitative if token.endswith("%")} or quantitative
        number_candidates: list[tuple[int, str]] = []
        for token in required_numbers:
            token_pattern = r"(?<![a-z0-9.])" + re.escape(token) + r"(?![a-z0-9.%])"
            for match in re.finditer(token_pattern, lower):
                excerpt = _excerpt_around(clean, match.start(), len(match.group(0)), window_chars)
                if not _contains_quantitative_tokens(excerpt, quantitative):
                    continue
                score = sum(1 for term in tokens if term in excerpt.lower()) + len(required_numbers) * 20
                number_candidates.append((score, excerpt))
        if number_candidates:
            number_candidates.sort(key=lambda item: (item[0], len(item[1])), reverse=True)
            return number_candidates[0][1]

    best_start = 0
    best_score = -1
    fallback_start = 0
    fallback_score = -1
    step = max(window_chars // 2, 120)
    starts = list(range(0, max(len(clean) - window_chars, 1), step))
    # The stepped range never reaches the document tail, so passages near the end of the
    # text were unfindable — always scan the final window too.
    final_start = max(len(clean) - window_chars, 0)
    if final_start not in starts:
        starts.append(final_start)
    for start in starts:
        window = lower[start : start + window_chars]
        overlap_score = sum(1 for token in tokens if token in window)
        if overlap_score > fallback_score:
            fallback_score = overlap_score
            fallback_start = start
        if quantitative and not _contains_quantitative_tokens(window, quantitative):
            continue
        if overlap_score > best_score:
            best_score = overlap_score
            best_start = start
    # When `quantitative` is empty, `_contains_quantitative_tokens` is vacuously true below,
    # so this tier would otherwise accept the best-overlap window on however little overlap it
    # found (`best_score > 0`) — including a couple of recurring proper nouns plus accidental
    # short-token substring hits, the same "no concrete anchor" failure mode `strict` exists to
    # reject (e.g. a German claim against an English PDF: only "bevacizumab"/"glioblastom" and
    # noise like "der" inside "order" overlap, scoring ~2, yet this tier would confidently return
    # a wrong window). A *strong* same-language overlap (several distinctive content terms, e.g.
    # "fatigue"+"headaches"+"placebo"+… scoring 8) is still a legitimate anchor — so in strict
    # mode without quantitative tokens, require the same "meaningfully strong" bar the honest
    # fallback below uses, instead of skipping this tier outright.
    minimum_overlap = 3 if (strict and not quantitative) else 1
    if best_score >= minimum_overlap:
        excerpt = _term_centered_excerpt(clean, lower, best_start, window_chars, tokens)
        if _contains_quantitative_tokens(excerpt, quantitative):
            return excerpt
    # Nothing matched the (stricter) quantitative requirement — fall back to the window
    # with the best plain textual overlap, but only if it's a meaningfully strong match.
    # This covers paraphrased/computed claims whose numbers don't appear verbatim in the PDF.
    # `strict` callers (e.g. anchoring a specific claim's citation) skip this tier: plain term
    # overlap has no concrete anchor (no verbatim phrase, no matching number) and is easily
    # dominated by names that recur throughout the whole paper — better to report no match than
    # a confident-looking excerpt that doesn't actually support the claim.
    if not strict and fallback_score >= 3:
        return _term_centered_excerpt(clean, lower, fallback_start, window_chars, tokens)
    return ""


def _term_centered_excerpt(clean: str, lower: str, start: int, window_chars: int, tokens: list[str]) -> str:
    """Centre the excerpt on the matched terms inside the winning window.

    Anchoring on the raw window start instead caused two real bugs: sentence-truncation
    could cut the actual match off the excerpt's tail, and matches near a window edge were
    surrounded by mostly irrelevant text.
    """
    window = lower[start : start + window_chars]
    positions = [(window.find(token), len(token)) for token in tokens if token in window]
    if not positions:
        return _excerpt_around(clean, start, min(window_chars, len(clean) - start), window_chars)
    first = min(position for position, _ in positions)
    last = max(position + length for position, length in positions)
    return _excerpt_around(clean, start + first, last - first, window_chars)


def verbatim_excerpt(
    pdf_text: str,
    quote: str,
    window_chars: int = DEFAULT_EXCERPT_CHARS,
) -> str:
    """Locate a model-provided verbatim quote in the PDF text (whitespace-insensitive).

    Returns the surrounding sentence-aligned excerpt (always containing the full quote),
    or "" when the quote does not occur verbatim — the caller then falls back to fuzzy
    anchoring. This is the reliable path: the model tells us which passage it used and we
    only trust it after exact verification.
    """
    clean = re.sub(r"\s+", " ", pdf_text or "").strip()
    quote_clean = re.sub(r"\s+", " ", quote or "").strip().strip('"„“«»').strip()
    if not clean or len(quote_clean) < 15:
        return ""
    position = clean.lower().find(quote_clean.lower())
    length = len(quote_clean)
    if position < 0:
        # PDFs and models render ligatures, dash variants, and typographic quotes
        # differently; fold both sides before declaring the quote a misquote.
        located = _find_normalized(clean, quote_clean)
        if located is None:
            return ""
        position, length = located
    return _excerpt_around(clean, position, length, max(window_chars, length + 80))


def best_excerpts(
    pdf_text: str,
    reference: str,
    max_excerpts: int = 3,
    window_chars: int = DEFAULT_EXCERPT_CHARS,
    strict: bool = False,
    merge_gap_chars: int = 240,
) -> list[str]:
    """Locate one excerpt per clause of `reference` and merge neighbouring matches.

    A claim whose facts are scattered across the PDF yields several distinct excerpts;
    facts that sit next to each other yield one longer, merged excerpt instead of
    multiple overlapping snippets.
    """
    clean = re.sub(r"\s+", " ", pdf_text or "").strip()
    reference_clean = re.sub(r"\s+", " ", reference or "").strip()
    if not clean or not reference_clean:
        return []
    # Split into clause-level fragments unconditionally (not only past MAX_REFERENCE_CHARS):
    # a claim like "survival was 16.8 months; patients reported more fatigue" carries two
    # separately locatable facts even though the whole sentence is short. The sentence split
    # requires whitespace after the terminator so decimals like "16.8" stay intact
    # (`_sentences` would break the clause apart at the decimal point).
    fragments: list[str] = []
    for sentence in re.split(r"[.!?]+\s+", reference_clean):
        for clause in re.split(r"(?:;\s+|:\s+|\s+-\s+|\s+–\s+|\s+—\s+)", sentence):
            clause = clause.strip(" ;:,.")
            if len(clause) >= 25 and clause not in fragments:
                fragments.append(clause)
    if not fragments:
        fragments = [reference_clean]

    spans: list[tuple[int, int]] = []
    unplaced: list[str] = []
    for fragment in fragments[: max_excerpts * 2]:
        excerpt = best_excerpt(clean, fragment, window_chars=window_chars, strict=strict)
        if not excerpt:
            continue
        position = clean.lower().find(excerpt.lower())
        if position < 0:
            if excerpt not in unplaced:
                unplaced.append(excerpt)
            continue
        spans.append((position, position + len(excerpt)))

    if not spans and not unplaced:
        # Individual clauses may lack anchors even when the whole reference has one.
        whole = best_excerpt(clean, reference_clean, window_chars=window_chars, strict=strict)
        return [whole] if whole else []

    spans.sort()
    merged: list[list[int]] = []
    for start, end in spans:
        if merged and start - merged[-1][1] <= merge_gap_chars:
            merged[-1][1] = max(merged[-1][1], end)
        else:
            merged.append([start, end])

    excerpts: list[str] = []
    for start, end in merged[:max_excerpts]:
        text = clean[start:end].strip()
        if text and text not in excerpts:
            excerpts.append(text)
    for extra in unplaced:
        if len(excerpts) >= max_excerpts:
            break
        if extra not in excerpts:
            excerpts.append(extra)
    return excerpts[:max_excerpts]


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
    # Split only on terminators followed by whitespace so decimal numbers stay intact —
    # the old character-class scan broke "0.55" into "0" / "55"-fragments.
    parts = [part.strip() for part in re.split(r"(?<=[.!?])\s+", clean) if part.strip()]
    return parts or [clean]


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
    if len(excerpt) <= window_chars:
        return excerpt
    truncated = _truncate_at_sentence(excerpt, window_chars)
    if start + len(truncated) >= position + match_length:
        return truncated
    # Truncation would cut the match itself away (e.g. a number near the end of a long
    # sentence shown as "0.55..."): return the complete sentence(s) carrying the match
    # instead, even when slightly longer than the window.
    sentence_start = _nearest_sentence_start(text, max(0, position - window_chars), position)
    sentence_end = _nearest_sentence_end(text, position + match_length)
    return text[sentence_start:sentence_end].strip()


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
        # Retry with unicode folding: extraction-time text and the current PDF parse can
        # disagree on ligatures, dash variants, and typographic quotes only.
        located = _find_normalized(text, chunk)
        if located is not None:
            return located
    return None


def _quantitative_tokens(value: str) -> set[str]:
    tokens: set[str] = set()
    normalized = re.sub(r"(\d+),(\d+)", r"\1.\2", str(value or ""))
    for match in re.findall(r"\d+(?:\.\d+)?\s*%?", normalized):
        clean = re.sub(r"\s+", "", match)
        if clean:
            tokens.add(clean.lower())
            tokens.add(clean.rstrip("%").lower())
    return tokens


def _contains_quantitative_tokens(text: str, tokens: set[str]) -> bool:
    if not tokens:
        return True
    haystack = _quantitative_tokens(text)
    required = {token for token in tokens if token.endswith("%")} or tokens
    return all(token in haystack for token in required)


def _cited_paper_ids(answer_text: str) -> set[str]:
    ids: set[str] = set()
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        for value in re.split(r"[,;]\s*", bracketed):
            value = value.strip()
            if value.startswith("arxiv:") or value.startswith("doi:") or value.startswith("p"):
                ids.add(value)
    return ids
