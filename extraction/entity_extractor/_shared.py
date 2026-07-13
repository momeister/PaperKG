"""Klassen-unabhaengige Modul-Ebene des EntityExtractor-Pakets.

Konstanten, JSON-Helfer, Dataclasses und die oeffentlichen Modul-Funktionen.
Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from difflib import SequenceMatcher
from typing import Any, Callable


logger = logging.getLogger(__name__)


DEFAULT_CONCEPT_BLOCKLIST = {
    "Questionnaire",
    "Empirical data",
    "Notational conventions",
    "Psychological theory",
    "Real robot",
    "Simulated robot",
    "Gridworld",
    "Navigation tasks",
}

DEFAULT_DOMAIN_KEYWORD_MAP = {
    "Machine Learning": [
        "neural network",
        "learning rate",
        "gradient",
        "training",
        "supervised",
        "unsupervised",
    ],
    "Reinforcement Learning": [
        "reward",
        "policy",
        "q-value",
        "temporal difference",
        "mdp",
        "agent",
        "state-action",
    ],
    "Computational Neuroscience": [
        "dopamine",
        "amygdala",
        "cortex",
        "neurotransmitter",
        "brain",
    ],
    "Robotics": ["robot", "navigation", "actuator", "sensor", "embodiment"],
    "Psychology": [
        "appraisal",
        "emotion",
        "homeostasis",
        "drive",
        "affect",
        "cognitive",
    ],
    "Human-Robot Interaction": [
        "user",
        "questionnaire",
        "empathy",
        "social",
        "dialogue",
        "interaction",
    ],
}

CLAIMS_EXTRACTION_PROMPT = """Extract concrete scientific claims from this paper.
Return a valid JSON array only, no preamble, no markdown.

Extract distinct claim types. Include at least 2 per type when present in the paper:
1. Contribution claims - what the paper itself contributes or is the first to do
2. Empirical findings - concrete results with numbers, comparisons, tasks, datasets, or named systems
3. Methodological recommendations - explicit advice to practitioners or researchers
4. Negative findings - limitations, failures, or things that did not work
5. Comparative claims - one approach, method, system, or condition outperforms or differs from another

For each claim, output exactly these fields:
{
  "statement": "quote or close paraphrase from the source text",
  "claim_type": "contribution|finding|limitation|negative_result|comparison|recommendation",
  "evidence_type": "empirical|theoretical|review|recommendation",
  "negated": false,
  "attributed_to": "this_paper|cited_work"
}

Rules:
- Quote or closely paraphrase the paper text; do not abstract into vague summaries.
- Prefer specific findings, recommendations, comparisons, named systems, and quantified results.
- Do not include vague meta-statements like "this paper provides an overview" unless the paper states that as its explicit contribution.
- Use claim_type="limitation" or "negative_result" for weak/null/insufficient results. Set "negated": true only for explicit logical negation such as "does not", "no evidence", or "fails to".
- Use attributed_to="this_paper" for the authors' own contribution, recommendation, result, or review-level synthesis.
- Use attributed_to="cited_work" only when the paper clearly attributes the claim to another named work.

Paper text:
{paper_text}
"""


def _strip_markdown_fences(raw_text: str) -> str:
    raw = (raw_text or "").strip()
    fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
    return fenced.group(1).strip() if fenced else raw


def _extract_first_json_value(raw_text: str) -> Any | None:
    decoder = json.JSONDecoder()
    raw = raw_text or ""
    for index, char in enumerate(raw):
        if char not in "[{":
            continue
        try:
            parsed, _ = decoder.raw_decode(raw[index:])
            return parsed
        except json.JSONDecodeError:
            continue
    return None


def _parse_llm_json_value(raw_text: str) -> Any | None:
    raw = (raw_text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    stripped = _strip_markdown_fences(raw)
    if stripped != raw:
        try:
            return json.loads(stripped)
        except json.JSONDecodeError:
            pass

    return _extract_first_json_value(stripped)


def safe_llm_extract(
    prompt: str,
    llm_call_fn: Callable[[str], Any],
    field_name: str,
    retries: int = 3,
) -> list[Any]:
    """
    Call an LLM for list extraction and recover common malformed JSON responses.

    The wrapper accepts either a direct JSON array or an object containing
    field_name. It retries malformed responses, and retries one additional time
    when a syntactically valid empty list is returned.
    """
    current_prompt = prompt
    empty_retry_used = False
    last_raw = ""

    for attempt in range(1, max(1, retries) + 1):
        try:
            raw_response = llm_call_fn(current_prompt)
        except Exception:
            logger.exception("LLM extraction call for %s failed", field_name)
            continue

        last_raw = str(raw_response.get("content") or raw_response) if isinstance(raw_response, dict) else str(raw_response or "")
        parsed = _parse_llm_json_value(last_raw)
        values: list[Any] | None = None
        if isinstance(parsed, list):
            values = parsed
        elif isinstance(parsed, dict) and isinstance(parsed.get(field_name), list):
            values = parsed[field_name]

        if values is None:
            logger.warning(
                "Could not parse %s JSON on attempt %s/%s",
                field_name,
                attempt,
                retries,
            )
            continue

        if values or empty_retry_used:
            return values

        empty_retry_used = True
        current_prompt = (
            prompt
            + "\n\nYour previous response returned an empty list. "
            + "The paper definitely contains content for this field. Please try again."
        )

    logger.warning("Returning empty %s after malformed LLM JSON. Raw response: %s", field_name, last_raw[:2000])
    return []


def filter_concepts(
    concepts: list[Any],
    title: str | None = None,
    blocklist: set[str] | list[str] | tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Filter deterministic-scan concept artifacts without dropping LLM concepts."""
    # Lazy import: die komponierte Klasse lebt im Paket-__init__ (zyklusfrei).
    from extraction.entity_extractor import EntityExtractor

    blocked = {EntityExtractor._normalize_label(item) for item in (blocklist or DEFAULT_CONCEPT_BLOCKLIST)}
    normalized_title = EntityExtractor._normalize_label(title or "")
    output: list[dict[str, Any]] = []
    for concept in concepts:
        if not isinstance(concept, dict):
            continue
        item = dict(concept)
        label = EntityExtractor._clean_label(str(item.get("label") or ""))
        normalized = EntityExtractor._normalize_label(label)
        if not label or not normalized:
            continue
        is_deterministic = item.get("candidate_source") == "deterministic_scan"
        lowered = label.lower()
        if "---" in label or "page break" in lowered or "break---" in lowered:
            continue
        if EntityExtractor._is_candidate_noise_artifact(item, label, title=title):
            continue
        if is_deterministic and normalized in blocked:
            continue
        if (
            is_deterministic
            and normalized_title
            and normalized in normalized_title
            and normalized != normalized_title
            and len(label.split()) >= 3
        ):
            continue
        confidence = EntityExtractor._coerce_float(item.get("confidence"), 1.0)
        if is_deterministic and confidence < 0.65:
            continue
        if is_deterministic and EntityExtractor._looks_like_truncated_label(label):
            continue
        item["label"] = label
        output.append(item)
    return output


def deduplicate_methods(methods: list[Any]) -> list[dict[str, Any]]:
    """Merge near-duplicate method labels while respecting distinct source types."""
    # Lazy import: die komponierte Klasse lebt im Paket-__init__ (zyklusfrei).
    from extraction.entity_extractor import EntityExtractor

    output: list[dict[str, Any]] = []
    for method in methods:
        if not isinstance(method, dict):
            continue
        candidate = dict(method)
        candidate["label"] = EntityExtractor._clean_label(str(candidate.get("label") or ""))
        if not candidate["label"]:
            continue

        merged = False
        for index, existing in enumerate(output):
            similarity = SequenceMatcher(
                None,
                candidate["label"].lower(),
                str(existing.get("label") or "").lower(),
            ).ratio()
            existing_source = str(existing.get("source_type") or "")
            candidate_source = str(candidate.get("source_type") or "")
            source_types_differ = bool(existing_source and candidate_source and existing_source != candidate_source)
            if similarity <= 0.75:
                continue
            if source_types_differ and similarity <= 0.9:
                continue

            existing_description = str(existing.get("description") or "")
            candidate_description = str(candidate.get("description") or "")
            keep = candidate if len(candidate_description) > len(existing_description) else existing
            merge_from = existing if keep is candidate else candidate
            for key, value in merge_from.items():
                if key not in keep or keep.get(key) in (None, "", [], {}):
                    keep[key] = value
            logger.info(
                "Merged duplicate method '%s' into '%s' (similarity %.2f)",
                merge_from.get("label"),
                keep.get("label"),
                similarity,
            )
            output[index] = keep
            merged = True
            break

        if not merged:
            output.append(candidate)
    return output


def enrich_method_domains(
    methods: list[Any],
    domain_keyword_map: dict[str, list[str]] | None = None,
) -> list[dict[str, Any]]:
    """Infer domains for methods whose domain is unset or unknown."""
    keyword_map = domain_keyword_map or DEFAULT_DOMAIN_KEYWORD_MAP
    enriched: list[dict[str, Any]] = []
    for method in methods:
        if not isinstance(method, dict):
            continue
        item = dict(method)
        domain = str(item.get("domain") or "").strip()
        if domain and domain.lower() != "unknown":
            enriched.append(item)
            continue

        text = f"{item.get('label') or ''} {item.get('description') or ''}".lower()
        best_domain = "Interdisciplinary"
        best_score = 0
        for candidate_domain, keywords in keyword_map.items():
            score = sum(1 for keyword in keywords if keyword.lower() in text)
            if score > best_score:
                best_score = score
                best_domain = candidate_domain
        item["domain"] = best_domain
        enriched.append(item)
    return enriched


@dataclass
class ExtractionResult:
    paper_id: str
    paper_type: str = "research"
    paper_node: dict[str, Any] = field(default_factory=dict)
    concepts: list[dict[str, Any]] = field(default_factory=list)
    methods: list[dict[str, Any]] = field(default_factory=list)
    concept_candidates: list[dict[str, Any]] = field(default_factory=list)
    method_candidates: list[dict[str, Any]] = field(default_factory=list)
    relations: list[dict[str, Any]] = field(default_factory=list)
    claims: list[dict[str, Any]] = field(default_factory=list)
    cross_domain_hints: list[dict[str, Any]] = field(default_factory=list)
    terminology_conflicts: list[dict[str, Any]] = field(default_factory=list)
    temporal_coverage: dict[str, Any] = field(default_factory=dict)
    mathematical_content: dict[str, Any] = field(default_factory=dict)
    language_detected: str = "en"
    quality_warnings: list[str] = field(default_factory=list)
    metadata_status: str = "valid"
    blocking_errors: list[str] = field(default_factory=list)
    candidate_count: int = 0
    extraction_diagnostics: dict[str, Any] = field(default_factory=dict)
    raw_response: str = ""
    extraction_mode: str = "quality"


def extraction_failure_reason(result: ExtractionResult | object) -> str | None:
    """Return a storage/UI failure reason for catastrophic extraction failures."""
    diagnostics = getattr(result, "extraction_diagnostics", {}) or {}
    if not isinstance(diagnostics, dict):
        return None
    reason = str(diagnostics.get("failure_reason") or "").strip()
    if diagnostics.get("fatal_llm_error"):
        return reason or "LLM extraction failed before usable JSON could be produced."
    if diagnostics.get("parse_quality") != "failed":
        return None
    calls = [
        call
        for call in (diagnostics.get("calls") or [])
        if isinstance(call, dict) and str(call.get("call_type") or "") != "claims_retry"
    ]
    failed_calls = [
        call
        for call in calls
        if str(call.get("parse_quality") or "") == "failed"
    ]
    if failed_calls and len(failed_calls) == len(calls):
        excerpts = " ".join(str(call.get("raw_excerpt") or "") for call in failed_calls)
        if "No models loaded" in excerpts:
            return "LLM extraction failed: LM Studio has no model loaded."
        concepts = getattr(result, "concepts", None) or []
        methods = getattr(result, "methods", None) or []
        if concepts or methods:
            return None
        return "LLM extraction failed for every extraction call; no KG-safe entities were produced."
    return None


@dataclass(frozen=True)
class ParsedLLMResponse:
    """Parsed model response with the quality of the JSON recovery path."""

    data: dict[str, Any]
    parse_quality: str
    raw_text: str
    tokens_used: int | None = None


@dataclass(frozen=True)
class RegexValidationResult:
    """Deterministic validation metadata from scanning the source paper text."""

    concepts: list[dict[str, Any]]
    auto_detected_count: int
    has_formulas: bool
    formula_types: list[str]


@dataclass(frozen=True)
class DeterministicScanResult:
    """High-recall local scan used to keep the LLM from missing obvious entities."""

    concepts: list[dict[str, Any]]
    methods: list[dict[str, Any]]
    paper_year: int | None = None


