"""Extract the reference/bibliography section of a parsed paper and split it into
individual reference strings.

This is the counterpart to ``EntityExtractor._text_before_references``: instead of
the body before the references, it returns the references themselves so they can be
matched against Crossref to discover and (on consent) download the cited papers.
"""
from __future__ import annotations

import re


_REFERENCE_HEADING = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:\d+\.?\s*)?(?:references|bibliography|works cited|literature cited|"
    r"reference list)\s*:?\s*$"
)
_REFERENCE_HEADING_INLINE = re.compile(r"\n\s*(?:references|bibliography)\s*\n", re.IGNORECASE)

# A trailing section that sometimes follows references and should be cut off.
_TRAILING_HEADING = re.compile(
    r"(?im)^\s*(?:#+\s*)?(?:\d+\.?\s*)?(?:appendix|appendices|supplementary|author contributions|"
    r"acknowledg(?:e?ments)?|about the authors?|biograph)\b"
)

_BRACKET_MARKER = re.compile(r"(?m)^\s*\[\s*\d{1,4}\s*\]\s+")
_NUMBER_DOT_MARKER = re.compile(r"(?m)^\s*\d{1,4}\.\s+")
_YEAR = re.compile(r"(?:19|20)\d{2}")


def extract_reference_section(text: str) -> str:
    """Return the references block of a paper, or an empty string if none is found."""
    raw = text or ""
    match = _REFERENCE_HEADING.search(raw)
    if match:
        section = raw[match.end():]
    else:
        match = _REFERENCE_HEADING_INLINE.search(raw)
        if not match:
            return ""
        section = raw[match.end():]

    trailing = _TRAILING_HEADING.search(section)
    if trailing:
        section = section[: trailing.start()]
    return section.strip()


def split_reference_entries(section: str, max_entries: int = 200) -> list[str]:
    """Split a references block into individual reference strings.

    Tries explicit ``[n]`` and ``n.`` markers first, then falls back to a
    blank-line / year-boundary heuristic.
    """
    section = (section or "").strip()
    if not section:
        return []

    entries = _split_on_marker(section, _BRACKET_MARKER)
    if len(entries) < 2:
        entries = _split_on_marker(section, _NUMBER_DOT_MARKER)
    if len(entries) < 2:
        entries = _split_on_blank_or_year(section)

    cleaned: list[str] = []
    for entry in entries:
        normalized = re.sub(r"\s+", " ", entry).strip(" .;")
        # Keep only plausible references: long enough and carrying a year.
        if len(normalized) >= 20 and _YEAR.search(normalized):
            cleaned.append(normalized[:600])
        if len(cleaned) >= max_entries:
            break
    return cleaned


def _split_on_marker(section: str, marker: re.Pattern[str]) -> list[str]:
    matches = list(marker.finditer(section))
    if len(matches) < 2:
        return [section]
    entries: list[str] = []
    for index, match in enumerate(matches):
        start = match.end()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(section)
        entries.append(section[start:end])
    return entries


def _split_on_blank_or_year(section: str) -> list[str]:
    blocks = re.split(r"\n\s*\n", section)
    if len(blocks) >= 3:
        return blocks
    # Fall back to line-based splitting where each line carrying a year is a reference.
    lines = [line for line in section.splitlines() if line.strip()]
    grouped: list[str] = []
    buffer: list[str] = []
    for line in lines:
        buffer.append(line.strip())
        if _YEAR.search(line):
            grouped.append(" ".join(buffer))
            buffer = []
    if buffer:
        grouped.append(" ".join(buffer))
    return grouped or [section]
