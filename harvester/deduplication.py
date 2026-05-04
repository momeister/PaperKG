from __future__ import annotations

import hashlib
import re
import unicodedata
from dataclasses import dataclass
from typing import Any


_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_text(value: str) -> str:
    value = unicodedata.normalize("NFKD", value)
    value = value.encode("ascii", "ignore").decode("ascii")
    value = value.lower().strip()
    value = _NON_ALNUM.sub(" ", value)
    return " ".join(value.split())


def normalize_doi(doi: str | None) -> str | None:
    if not doi:
        return None
    cleaned = doi.strip().lower()
    cleaned = cleaned.removeprefix("https://doi.org/")
    cleaned = cleaned.removeprefix("http://doi.org/")
    return cleaned


def title_fingerprint(title: str) -> str:
    return hashlib.sha256(normalize_text(title).encode("utf-8")).hexdigest()


@dataclass
class DedupDecision:
    keep: dict[str, Any]
    dropped: list[dict[str, Any]]
    reason: str


def deduplicate_papers(records: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[DedupDecision]]:
    """
    Deduplicate by DOI first, then normalized title.

    If duplicates are found, keep the record with the highest version field (default 1).
    """
    by_doi: dict[str, dict[str, Any]] = {}
    by_title: dict[str, dict[str, Any]] = {}
    decisions: list[DedupDecision] = []

    def pick_better(existing: dict[str, Any], candidate: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        existing_version = int(existing.get("version") or 1)
        candidate_version = int(candidate.get("version") or 1)
        if candidate_version > existing_version:
            return candidate, existing
        return existing, candidate

    unique: list[dict[str, Any]] = []

    for record in records:
        doi = normalize_doi(record.get("doi"))
        title_key = normalize_text(record.get("title", ""))

        duplicate_of: dict[str, Any] | None = None
        duplicate_reason = ""

        if doi and doi in by_doi:
            duplicate_of = by_doi[doi]
            duplicate_reason = "same_doi"
        elif title_key and title_key in by_title:
            duplicate_of = by_title[title_key]
            duplicate_reason = "same_title"

        if duplicate_of is None:
            unique.append(record)
            if doi:
                by_doi[doi] = record
            if title_key:
                by_title[title_key] = record
            continue

        keep, dropped = pick_better(duplicate_of, record)
        decisions.append(DedupDecision(keep=keep, dropped=[dropped], reason=duplicate_reason))

        if keep is record:
            unique.remove(duplicate_of)
            unique.append(record)
            if doi:
                by_doi[doi] = record
            if title_key:
                by_title[title_key] = record

    return unique, decisions
