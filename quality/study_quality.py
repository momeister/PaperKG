"""Study-quality grading (GRADE-light) and quality-weighted reranking.

Computes a per-paper ``StudyQuality`` summary from the extraction result's
provenance block and claim-level critical fields, and exposes a retrieval
reranker that multiplies evidence scores by the quality score.

The grading is deliberately rule-based and conservative: it never invents
values, only down-grades what the extractor already found.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Iterable


logger = logging.getLogger(__name__)


_VALID_EVIDENCE_LEVELS = {"high", "moderate", "low", "very_low", "unknown"}

_DESIGN_BASE_SCORE = {
    "meta_analysis": 1.0,
    "systematic_review": 1.0,
    "rct": 1.0,
    "randomized_controlled": 0.95,
    "cohort": 0.7,
    "case_control": 0.6,
    "cross_sectional": 0.5,
    "observational": 0.5,
    "survey": 0.4,
    "case_series": 0.3,
    "case_report": 0.2,
    "benchmark": 0.65,
    "simulation": 0.6,
    "qualitative": 0.45,
    "theoretical": 0.6,
    "unknown": 0.5,
}

_LEVEL_TO_SCORE = {
    "high": 0.9,
    "moderate": 0.65,
    "low": 0.4,
    "very_low": 0.2,
    "unknown": 0.5,
}

_INDUSTRY_KEYWORDS = {
    "pharma": [
        "pharma",
        "pfizer",
        "novartis",
        "roche",
        "bayer",
        "merck",
        "glaxo",
        "johnson & johnson",
        "astrazeneca",
        "sanofi",
        "eli lilly",
        "bristol",
        "abbvie",
        "amgen",
        "gilead",
        "takeda",
        "boehringer",
    ],
    "tobacco": [
        "philip morris",
        "british american tobacco",
        "altria",
        "rj reynolds",
        "imperial tobacco",
        "japan tobacco",
        "lorillard",
        "tobacco",
    ],
    "oil": [
        "shell",
        "exxon",
        "exxonmobil",
        "chevron",
        "bp",
        "total",
        "conoco",
        "valero",
        " Marathon",
        "equinor",
        "arabian oil",
        "saudi aramco",
    ],
    "food": [
        "nestle",
        "kraft",
        "mondelez",
        "coca-cola",
        "pepsi",
        "pepsico",
        "unilever",
        "mars",
        "danone",
        "general mills",
        "kellogg",
    ],
    "tech": [
        "google",
        "alphabet",
        "microsoft",
        "meta",
        "facebook",
        "amazon",
        "apple",
        "nvidia",
        "openai",
        "deepmind",
        "anthropic",
        "bytedance",
        "tencent",
        "alibaba",
        "baidu",
        "samsung",
        "intel",
        "qualcomm",
    ],
    "alcohol": [
        "anheuser-busch",
        "heineken",
        "diageo",
        "pernod ricard",
        "carlsberg",
        "molson coors",
        " constellation brands",
        "brewery",
        "distillery",
    ],
    "firearms": [
        "smith & wesson",
        "glock",
        "remington",
        "beretta",
        "colt",
        "rug",
        "sturm ruger",
        "springfield armory",
        "nra",
        "firearm",
    ],
}


@dataclass
class StudyQuality:
    """Per-paper study-quality summary derived from the extraction result."""

    study_design: str = "unknown"
    sample_size_value: int | None = None
    sample_size_unit: str = "participants"
    evidence_level: str = "unknown"
    quality_score: float = 0.5
    flags: list[str] = field(default_factory=list)
    funding_sources: list[dict[str, Any]] = field(default_factory=list)
    coi_status: str = "unknown"
    limitations: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "study_design": self.study_design,
            "sample_size_value": self.sample_size_value,
            "sample_size_unit": self.sample_size_unit,
            "evidence_level": self.evidence_level,
            "quality_score": round(self.quality_score, 4),
            "flags": list(self.flags),
            "funding_sources": list(self.funding_sources),
            "coi_status": self.coi_status,
            "limitations": list(self.limitations),
        }


def _classify_industry_type(name: str) -> str:
    lowered = str(name or "").lower()
    for industry, keywords in _INDUSTRY_KEYWORDS.items():
        if any(keyword in lowered for keyword in keywords):
            return industry
    return "other"


def _industry_funded(
    funding_sources: Iterable[dict[str, Any]]
) -> tuple[bool, str | None]:
    """Return (is_industry_funded, industry_type_or_none)."""
    for source in funding_sources or []:
        if not isinstance(source, dict):
            continue
        industry_type = str(source.get("industry_type") or "").strip().lower()
        if industry_type in _INDUSTRY_KEYWORDS:
            return True, industry_type
        name = str(source.get("name") or "")
        if name:
            inferred = _classify_industry_type(name)
            if inferred != "other":
                return True, inferred
    return False, None


def _aggregate_coi_status(coi_list: Iterable[dict[str, Any]]) -> str:
    """Aggregate per-author COI declarations to a single paper-level status."""
    statuses = []
    for entry in coi_list or []:
        if not isinstance(entry, dict):
            continue
        declaration = str(entry.get("declaration") or "").strip().lower()
        if declaration:
            statuses.append(declaration)
    if not statuses:
        return "unknown"
    if any(status == "undisclosed" for status in statuses):
        return "undisclosed"
    if any(status == "declared" for status in statuses):
        return "declared"
    if all(status == "none" for status in statuses):
        return "none"
    return "unknown"


def _design_to_evidence_level(design: str, sample_size_value: int | None) -> str:
    base_map = {
        "meta_analysis": "high",
        "systematic_review": "high",
        "rct": "high",
        "randomized_controlled": "high",
        "cohort": "moderate",
        "case_control": "moderate",
        "cross_sectional": "low",
        "observational": "low",
        "survey": "low",
        "case_series": "low",
        "case_report": "very_low",
        "benchmark": "moderate",
        "simulation": "moderate",
        "qualitative": "low",
        "theoretical": "moderate",
        "unknown": "unknown",
    }
    base = base_map.get(str(design or "").strip().lower(), "unknown")
    if base == "unknown":
        return "unknown"
    if sample_size_value is not None:
        if sample_size_value < 10 and base in {"high", "moderate", "low"}:
            return "very_low"
        if sample_size_value < 30 and base in {"high", "moderate"}:
            return "low"
    return base


def compute_study_quality(
    extraction_result: Any,
    paper_meta: dict[str, Any] | None = None,
) -> StudyQuality:
    """Compute a ``StudyQuality`` summary from an extraction result and
    optional paper metadata (peer_reviewed, retracted, …).

    ``extraction_result`` may be an ``ExtractionResult`` dataclass (uses
    provenance/study_quality/claims) or a plain dict.
    """
    provenance: dict[str, Any] = {}
    claims: list[dict[str, Any]] = []
    result_evidence_level = "unknown"
    if extraction_result is None:
        pass
    elif isinstance(extraction_result, dict):
        provenance = extraction_result.get("provenance") or {}
        if not isinstance(provenance, dict):
            provenance = {}
        claims = extraction_result.get("claims") or []
        if not isinstance(claims, list):
            claims = []
        result_evidence_level = str(
            extraction_result.get("evidence_level") or "unknown"
        )
    else:
        provenance = getattr(extraction_result, "provenance", {}) or {}
        claims = getattr(extraction_result, "claims", []) or []
        result_evidence_level = str(
            getattr(extraction_result, "evidence_level", "unknown") or "unknown"
        )

    paper_meta = paper_meta or {}
    peer_reviewed = paper_meta.get("peer_reviewed")
    if peer_reviewed is None:
        peer_reviewed = True
    peer_reviewed = bool(peer_reviewed)
    retracted = bool(paper_meta.get("retracted"))

    study_design = str(provenance.get("study_design_primary") or "").strip().lower()
    if study_design not in _DESIGN_BASE_SCORE:
        study_design = "unknown"

    funding_sources = list(provenance.get("funding_sources") or [])
    coi_status = _aggregate_coi_status(provenance.get("conflicts_of_interest") or [])
    limitations = list(provenance.get("limitations") or [])

    # Pick the dominant sample size from claims when the paper-level one is unset.
    sample_size_value: int | None = None
    sample_size_unit = "participants"
    for claim in claims:
        if not isinstance(claim, dict):
            continue
        sample = claim.get("sample_size")
        if not isinstance(sample, dict):
            continue
        try:
            value = int(sample.get("value") or 0) or None
        except (TypeError, ValueError):
            value = None
        if value is None:
            continue
        if sample_size_value is None or value > sample_size_value:
            sample_size_value = value
            unit = str(sample.get("unit") or "").strip().lower()
            if unit:
                sample_size_unit = unit

    flags: list[str] = []
    if retracted:
        flags.append("retracted")
        return StudyQuality(
            study_design=study_design,
            sample_size_value=sample_size_value,
            sample_size_unit=sample_size_unit,
            evidence_level="very_low",
            quality_score=0.0,
            flags=flags,
            funding_sources=funding_sources,
            coi_status=coi_status,
            limitations=limitations,
        )

    score = _DESIGN_BASE_SCORE.get(study_design, 0.5)

    if sample_size_value is not None:
        if sample_size_value < 30:
            score *= 0.6
        elif sample_size_value > 1000:
            score *= 1.1
    else:
        flags.append("sample_size_not_extracted")

    is_industry, industry_type = _industry_funded(funding_sources)
    if is_industry:
        score *= 0.8
        flags.append(f"industry_funded:{industry_type or 'other'}")

    if coi_status == "undisclosed":
        score *= 0.9
        flags.append("coi_undeclared")
    elif coi_status == "unknown":
        flags.append("coi_unknown")

    if not peer_reviewed:
        score *= 0.85
        flags.append("non_peer_reviewed")

    evidence_level = (
        result_evidence_level
        if result_evidence_level in _VALID_EVIDENCE_LEVELS
        and result_evidence_level != "unknown"
        else _design_to_evidence_level(study_design, sample_size_value)
    )

    score = max(0.0, min(1.0, score))

    return StudyQuality(
        study_design=study_design,
        sample_size_value=sample_size_value,
        sample_size_unit=sample_size_unit,
        evidence_level=evidence_level,
        quality_score=score,
        flags=flags,
        funding_sources=funding_sources,
        coi_status=coi_status,
        limitations=limitations,
    )


def rerank_by_quality(
    hits: list[dict[str, Any]],
    study_quality_map: dict[str, StudyQuality] | dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rerank retrieval hits by multiplying their score with the paper's
    quality score. ``hits`` is mutated in place (score field) and returned.

    Each hit is expected to have a ``paper_id`` and a ``score``. Papers without
    a quality entry keep their original score.
    """
    for hit in hits or []:
        if not isinstance(hit, dict):
            continue
        paper_id = hit.get("paper_id")
        if not paper_id:
            continue
        quality = study_quality_map.get(paper_id)
        if quality is None:
            continue
        quality_score = (
            quality.quality_score
            if isinstance(quality, StudyQuality)
            else float(quality.get("quality_score", 0.5) or 0.5)
        )
        try:
            base = float(hit.get("score") or 0.0)
        except (TypeError, ValueError):
            base = 0.0
        hit["score"] = base * (0.5 + 0.5 * quality_score)
    return hits


def quality_flag_markers(quality: StudyQuality | dict[str, Any] | None) -> str:
    """Build a short German marker string for the evidence list, e.g.
    '(⚠ Industry-funded · COI undeclared · Preprint)'. Empty string when
    no flag applies."""
    if quality is None:
        return ""
    flags = (
        quality.flags
        if isinstance(quality, StudyQuality)
        else list(quality.get("flags") or [])
    )
    if not flags:
        return ""
    markers = []
    for flag in flags:
        if flag == "retracted":
            markers.append("Retracted")
        elif flag == "non_peer_reviewed":
            markers.append("Preprint")
        elif flag == "coi_undeclared":
            markers.append("COI undeclared")
        elif flag == "coi_unknown":
            markers.append("COI unknown")
        elif flag in ("sample_size_not_reported", "sample_size_not_extracted"):
            markers.append("Sample size not extracted")
        elif flag.startswith("industry_funded:"):
            industry = flag.split(":", 1)[1]
            markers.append(f"Industry-funded ({industry})")
    if not markers:
        return ""
    return "(⚠ " + " · ".join(markers) + ")"
