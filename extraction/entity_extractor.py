from __future__ import annotations

import json
import logging
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any, Callable

from extraction.ontology import stable_canonical_id
from extraction.text_normalization import normalize_key, normalize_scientific_text
from query.llm_router import LLMRouter

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


class EntityExtractor:
    """
    Two-stage scientific KG extractor with deterministic validation.

    Stage 1 asks the model to enumerate accepted concepts and methods plus
    lower-confidence review candidates. Stage 2
    receives the stage-1 output and performs semantic analysis for claims,
    metadata, cross-domain hints, and terminology conflicts. A regex validation
    pass then backfills candidate algorithm/model/theory names that local LLMs
    commonly omit under token pressure, without automatically promoting them
    into KG nodes.
    """

    STRUCTURAL_PROMPT = """You are the STRUCTURAL extractor for a scientific knowledge graph.

Your task is precise extraction for automatic KG insertion plus separate review candidates.
Do not reason aloud. Do not include markdown.
Return only one complete valid JSON object with exactly these top-level keys:
{
  "concepts": [
    {"label": "canonical named concept", "entity_type": "Algorithm|Theory|MethodFamily|Metric|Dataset|Benchmark|DomainConcept|ApplicationSetting|ModelArchitecture|System|Phenomenon|Task", "context": "where it is materially discussed", "evidence_span": "short source phrase", "section": "paper section if known", "confidence": 0.90, "salience": "central|supporting", "evidence_role": "theory|method_family|metric|dataset|domain_concept"}
  ],
  "methods": [
    {"label": "specific method name", "entity_type": "Algorithm|MethodFamily|ModelArchitecture|System|Task", "domain": "scientific field", "description": "what it does", "evidence_span": "short source phrase", "section": "paper section if known", "source_type": "paper_contribution|reviewed_method|baseline", "salience": "central|supporting"}
  ],
  "concept_candidates": [
    {"label": "candidate concept", "entity_type": "Algorithm|Theory|MethodFamily|Metric|Dataset|Benchmark|DomainConcept|ApplicationSetting|ModelArchitecture|System|Phenomenon|Task", "context": "why it may matter", "evidence_span": "short source phrase", "section": "paper section if known", "confidence": 0.60, "salience": "background|passing", "evidence_role": "background|generic_field|environment|possible_concept"}
  ],
  "method_candidates": [
    {"label": "candidate method", "entity_type": "Algorithm|MethodFamily|ModelArchitecture|System|Task", "domain": "scientific field", "description": "why it may matter", "evidence_span": "short source phrase", "section": "paper section if known", "source_type": "reviewed_method|baseline|background", "confidence": 0.60, "salience": "background|passing"}
  ]
}

Rules:
- Use only the listed entity_type values; do not invent new entity types.
- evidence_span must be a concise phrase or sentence copied/closely paraphrased from this chunk.
- Accepted concepts/methods must be precise enough for automatic KG insertion; uncertain items belong in candidates.
- Return up to 10 accepted concepts and up to 8 accepted methods for this chunk.
- Return up to 8 concept candidates and up to 6 method candidates. If output may become long, omit candidates first.
- Never truncate JSON. A short complete object is better than a long malformed one.
- Put lower-confidence, generic, background, or passing mentions into candidate arrays instead of accepted arrays.
- Prefer named algorithms, model architectures, mathematical frameworks, scientific theories, metrics, datasets, benchmarks, and domain-specific concepts.
- Imaging modalities or acquisition techniques are MethodFamily, not Dataset; reserve Dataset for named corpora, cohorts, collections, or benchmarks.
- Include only items materially discussed in this chunk. Ignore conference names, journal names, section-heading fragments, author lists, and bibliography-only mentions.
- Never group named items under umbrella labels.
- For survey papers, extract the reviewed methods as methods with source_type "reviewed_method"; extract the survey taxonomy/framework as source_type "paper_contribution".
- In survey papers, background algorithms like Q-learning, SARSA, TD(lambda), Dynamic Programming, or generic RL basics are reviewed/background items, not this paper's contribution.
- Method labels must be specific and linkable, not generic labels like "Comparative Analysis" or "Survey Taxonomy".
- Scores must vary by textual certainty: central named items 0.90-0.98, discussed items 0.75-0.89, contextual mentions 0.60-0.74, passing mentions 0.45-0.59.
- Accepted arrays should be compact and high precision. Do not emit duplicate aliases as concepts.
- Generic fields or environments such as Machine Learning, Gridworld, State, or Action Selection should be candidates unless the paper specifically contributes to them.
- Deterministic candidate hints are only hints. Promote a hint only when this chunk materially discusses it.

Deterministic candidate hints:
{candidate_json}

Paper text:
{paper_text}
"""

    SEMANTIC_PROMPT = """You are the SEMANTIC extractor for a scientific knowledge graph.

Use the paper text and the structural extraction context. Do not reason aloud. Do not include markdown.
Your task is meta-level analysis, not additional broad enumeration.
Return only one complete valid JSON object with exactly these top-level keys:
{
  "paper_type": "research|survey|theoretical|benchmark",
  "paper_node": {"title": "paper title if clear", "paper_year": null, "reviewed_period": null},
  "claims": [
    {"statement": "claim made by this paper", "claim_type": "contribution|finding|limitation|negative_result|comparison|recommendation", "evidence_type": "experimental|theoretical|review", "negated": false, "attributed_to": "this_paper"}
  ],
  "cross_domain_hints": [
    {"field": "specific target field", "why_applicable": "method-level transfer reason"}
  ],
  "terminology_conflicts": [
    {"term": "shared term", "this_field": "meaning in this paper", "other_field": "different meaning elsewhere"}
  ],
  "temporal_coverage": {"paper_year": null, "reviewed_period": null},
  "mathematical_content": {"has_formulas": false, "formula_types": []},
  "language_detected": "en"
}

Rules:
- Classify paper_type first: research, survey, theoretical, or benchmark.
- Fill paper_node with title/year/reviewed_period when explicit. The pipeline will add the stable paper_id.
- For research papers, extract claims about this paper's own results only.
- For survey papers, extract field-level meta-claims made by this survey. Do not attribute cited paper results to this paper.
- Use claim_type="limitation" or "negative_result" for weak/null/insufficient results. Set "negated": true only for explicit logical negation such as "does not", "no evidence", or "fails to".
- Cross-domain hints must transfer methods, not just topics.
- Return 3-8 cross-domain hints for survey or theoretical papers when methods could plausibly transfer.
- Terminology conflicts prevent false graph links; include them when a term has materially different meanings across fields.
- Return terminology conflicts for overloaded terms such as reward, value, drive, valence, policy, model, bias, or control when they appear in this paper.
- Detect paper_year and reviewed_period when possible.
- Mark mathematical_content.has_formulas true if the paper contains equations, formal objectives, value functions, reward functions, theorems, proofs, or substantial tables.

Structural extraction context:
{structural_json}

Paper text:
{paper_text}
"""

    METHODS_ONLY_PROMPT = """Extract all named scientific methods from this paper as JSON array.
Each entry: {label, domain, description, source_type}
Paper text: {paper_text}
Respond with only the JSON array, no other text."""

    CONCEPTS_ONLY_PROMPT = """Extract named scientific concepts from this paper as JSON array.
Each entry: {label, entity_type, context, evidence_span, confidence, salience, evidence_role}
Use only these entity_type values: Algorithm, Theory, MethodFamily, Metric, Dataset, Benchmark, DomainConcept, ApplicationSetting, ModelArchitecture, System, Phenomenon, Task.
Prefer paper-specific systems, model architectures, algorithms, datasets, benchmarks, metrics, and scientific concepts.
Treat imaging modalities or acquisition techniques as MethodFamily, not Dataset.
Return complete valid JSON only. A short complete array is better than a malformed long array.
Deterministic candidate hints: {candidate_json}
Paper text: {paper_text}"""

    SEMANTIC_LISTS_RETRY_PROMPT = """Extract scientific claims, cross-domain hints, and terminology conflicts from this paper.
Return only one complete valid JSON object with exactly these keys:
{
  "claims": [
    {"statement": "claim made by this paper", "claim_type": "contribution|finding|limitation|negative_result|comparison|recommendation", "evidence_type": "experimental|theoretical|review", "negated": false, "attributed_to": "this_paper"}
  ],
  "cross_domain_hints": [
    {"field": "specific target field", "why_applicable": "method-level transfer reason"}
  ],
  "terminology_conflicts": [
    {"term": "shared term", "this_field": "meaning in this paper", "other_field": "different meaning elsewhere"}
  ]
}
For survey papers, extract 4-8 field-level meta-claims made by the survey, not individual cited-paper results.
Use claim_type="limitation" or "negative_result" for weak/null/insufficient results. Set "negated": true only for explicit logical negation such as "does not", "no evidence", or "fails to".
Return 3-8 cross-domain hints when methods could plausibly transfer.
Return terminology conflicts for overloaded terms such as reward, value, drive, valence, policy, model, bias, or control when they appear.
Paper text: {paper_text}"""

    KNOWN_CONCEPT_PATTERNS: tuple[tuple[str, str], ...] = (
        ("Q-learning", r"\bQ[\s-]?learning\b"),
        ("SARSA", r"\bSARSA\b"),
        ("TD(lambda)", r"\bTD\s*\(?\s*(?:lambda|\\lambda|λ)\s*\)?|\bTD\s*\(\s*λ\s*\)"),
        ("Reinforcement Learning", r"\breinforcement learning\b"),
        ("TD learning", r"\btemporal difference\s*\(?\s*TD\s*\)?\s+learning\b|\bTD learning\b"),
        ("Temporal difference error", r"\btemporal difference error\b|\bTD error\b"),
        ("Markov Decision Process", r"\bMarkov Decision Process\b"),
        ("MDP", r"\bMDP\b"),
        ("Value function", r"\bvalue function(?:s)?\b"),
        ("Reward function", r"\breward function(?:s)?\b"),
        ("State-action value", r"\bstate-action value\b|\bQ\s*\(\s*s\s*,\s*a\s*\)"),
        ("PPO", r"\bPPO\b|\bProximal Policy Optimization\b"),
        ("A3C", r"\bA3C\b|\bAsynchronous Advantage Actor[-\s]?Critic\b"),
        ("DQN", r"\bDQN\b|\bDeep Q[-\s]?Network(?:s)?\b"),
        ("REINFORCE", r"\bREINFORCE\b"),
        ("Actor-Critic architecture", r"\bActor[-\s]?Critic(?: architecture)?\b"),
        ("Dynamic Programming", r"\bDynamic Programming\b"),
        ("Homeostasis", r"\bhomeostasis\b|\bhomeostatic\b"),
        ("Extrinsic motivation", r"\bextrinsic motivation\b|\bextrinsic/homeostatic\b"),
        ("Intrinsic motivation", r"\bintrinsic motivation\b|\bintrinsic/appraisal\b"),
        ("Motivated reinforcement learning", r"\bmotivated reinforcement learning\b"),
        ("Appraisal theory", r"\bappraisal theor(?:y|ies)\b"),
        ("Prospect Theory", r"\bProspect Theory\b"),
        ("Average reward", r"\baverage reward\b"),
        ("Categorical emotion", r"\bcategorical emotions?\b"),
        ("Dimensional emotion", r"\bdimensional emotions?\b"),
        ("Model-based RL", r"\bmodel[- ]based\s+RL\b|\bmodel[- ]based reinforcement learning\b"),
        ("POMDP", r"\bPOMDP\b|\bPartially Observable Markov Decision Process\b"),
        ("Well-being", r"\bwell[- ]being\b"),
        ("Model uncertainty", r"\bmodel uncertainty\b"),
        ("Novelty", r"\bnovelty\b"),
        ("Recency", r"\brecency\b"),
        ("Control/Power", r"\bcontrol and power\b|\bcontrol/power\b"),
        ("Motivational relevance", r"\bmotivational relevance\b"),
        ("Intrinsic pleasantness", r"\bintrinsic pleasantness\b"),
        ("Social fairness", r"\bsocial fairness\b"),
        ("Social accountability", r"\bsocial accountability\b"),
        ("Valence", r"\bvalence\b|\bvalency\b"),
        ("Arousal", r"\barousal\b"),
        ("Dopamine", r"\bdopamine\b"),
        ("Serotonin", r"\bserotonin\b"),
        ("Noradrenaline", r"\bnoradrenaline\b|\bnorepinephrine\b"),
        ("Acetylcholine", r"\bacetylcholine\b"),
        ("Learning rate", r"\blearning rate\b"),
        ("Discount factor", r"\bdiscount factor\b"),
        ("Boltzmann action selection temperature", r"\bBoltzmann action selection temperature\b"),
        ("Fuzzy logic", r"\bfuzzy logic\b"),
        ("Transition model", r"\btransition models?\b"),
        ("Forward simulation", r"\bforward simulation\b"),
        ("Goal-oriented action planning", r"\bgoal[- ]oriented action planning\b"),
        ("Bio-inspiration", r"\bbio[- ]inspiration\b|\bbio[- ]inspired\b"),
        ("Developmental robotics", r"\bdevelopmental robotics\b"),
        ("Planning community heuristics", r"\bplanning community\b"),
        ("Emotional feedback", r"\bemotional feedback\b"),
        ("Human-robot interaction", r"\bhuman[- ]robot interaction\b"),
        ("Affective modelling", r"\baffective modelling\b|\baffective modeling\b"),
        ("Affective Computing", r"\baffective computing\b"),
        ("Emotion modelling", r"\bemotion(?:al)? model(?:l)?ing\b|\bcomputational emotion models?\b"),
        ("Emotional agents", r"\bemotional agents?\b|\bagents? with emotions?\b"),
        ("Reward shaping", r"\breward shaping\b|\bshap(?:e|ed|ing)\s+rewards?\b"),
        ("Policy gradient", r"\bpolicy gradient(?:s)?\b"),
        ("Value iteration", r"\bvalue iteration\b"),
        ("Multi-agent reinforcement learning", r"\bmulti[- ]agent reinforcement learning\b|\bMARL\b"),
        ("Intrinsic reward", r"\bintrinsic rewards?\b"),
        ("Extrinsic reward", r"\bextrinsic rewards?\b"),
        ("Cognitive appraisal", r"\bcognitive appraisal\b"),
        ("Appraisal dimensions", r"\bappraisal dimensions?\b|\bappraisal variables?\b"),
        ("Human feedback", r"\bhuman feedback\b|\bsocial feedback\b"),
        ("Homeostatic reinforcement learning", r"\bhomeostatic reinforcement learning\b"),
        ("KL-divergence", r"\bKL[- ]divergence\b"),
        ("L1 norm", r"\bL1 norm\b"),
        ("Euclidean distance", r"\bEuclidean distance\b"),
        ("Set point", r"\bset point\b"),
        ("Drive", r"\bdrives?\b"),
        ("Primary reinforcers", r"\bprimary reinforcers\b"),
        ("Emotion elicitation categories", r"\bemotion elicitation categor(?:y|ies)\b"),
        ("Emotion type classification", r"\bemotion type classification\b"),
        ("BERT", r"\bBERT\b"),
        ("RoBERTa", r"\bRoBERTa\b"),
        ("DistilBERT", r"\bDistilBERT\b"),
        ("ELECTRA", r"\bELECTRA\b"),
        ("ELMo", r"\bELMo\b"),
        ("Bi-LSTM", r"\bBi[-\s]?LSTM\b|\bBidirectional LSTM\b|\bBidirectional Long Short[-\s]?Term Memory\b"),
        ("C-LSTM", r"\bC[-\s]?LSTM\b|\bConvolutional LSTM\b"),
        ("Conv-HAN", r"\bConv[-\s]?HAN\b|\bConvolutional Hierarchical Attention Network\b"),
        ("HAN", r"\bHAN\b|\bHierarchical Attention Network\b"),
        ("LSTM", r"\bLSTM\b"),
        ("CNN", r"\bCNN\b|\bConvolutional Neural Network(?:s)?\b"),
        ("Transformer", r"\bTransformer(?:s)?\b"),
        ("GPT", r"\bGPT(?:-\d+(?:\.\d+)?)?\b"),
        ("OCC Model", r"\bOCC\s+model\b|\bOrtony,\s*Clore,\s*(?:and|&)\s*Collins\b"),
        ("PAD Model", r"\bPAD\s+model\b|\bPleasure[-\s]Arousal[-\s]Dominance\b"),
        ("Somatic Marker Hypothesis", r"\bsomatic marker(?: hypothesis)?\b"),
        ("Drive Reduction Theory", r"\bdrive reduction(?: theory)?\b"),
        ("Official Statistics", r"\bofficial statistics\b"),
        ("Data Science", r"\bdata science\b"),
        ("Machine Learning", r"\bmachine learning\b"),
        ("Data Source Changes", r"\bchang(?:e|es|ing)\s+(?:in\s+)?data sources?\b|\bdata sources?\s+chang(?:e|es|ing)\b"),
        ("External Data Sources", r"\bexternal data sources?\b|\balternative data sources?\b"),
        ("Concept Drift", r"\bconcept drift\b"),
        ("Bias", r"\bbias(?:es|ed)?\b"),
        ("Data Availability", r"\bdata availability\b|\bavailability\b"),
        ("Data Validity", r"\bdata validity\b|\bvalidity\b"),
        ("Data Accuracy", r"\bdata accuracy\b|\baccuracy\b"),
        ("Data Completeness", r"\bdata completeness\b|\bcompleteness\b"),
        ("Statistical Neutrality", r"\bstatistical neutrality\b|\bneutrality\b"),
        ("Statistical Reporting", r"\bstatistical reporting\b|\breporting\b"),
        ("Data Source Ownership", r"\bownership\b|\bdata source ownership\b"),
        ("Ethics", r"\bethics?\b|ethical"),
        ("Regulation", r"\bregulation\b|\bregulatory\b"),
        ("Public Perception", r"\bpublic perception\b"),
        ("Privacy", r"\bprivacy\b"),
        ("Robustness", r"\brobustness\b|\brobust\b"),
        ("Monitoring", r"\bmonitoring\b|\bmonitor\b"),
        ("Model Retraining", r"\bretrain(?:ing)?\b|\bmodel retraining\b"),
        ("Data Pipeline", r"\bdata pipelines?\b"),
        ("Data Distribution", r"\bdata distribution\b"),
        ("Derived Data Fields", r"\bderived data fields?\b"),
        ("Data Frequency", r"\bdata frequency\b"),
        ("Data Source Discontinuation", r"\bdiscontinuation\b|\bdiscontinued\b"),
        ("Quantum Machine Learning", r"\bquantum machine learning\b|\bQML\b"),
        ("Photonic Quantum Machine Learning", r"\bphotonic (?:and hybrid )?quantum machine learning\b|\bphotonic QML\b"),
        ("MerLin", r"\bMerLin\b"),
        ("Fock space", r"\bFock[-\s]?space\b|\bFock space\b"),
        ("Linear-optical circuits", r"\blinear[-\s]?optical circuits?\b"),
        ("QuantumLayer", r"\bQuantumLayer\b|\bQuantum Layer\b"),
        ("Angle encoding", r"\bangle encoding\b|\bphase encoding\b"),
        ("Amplitude encoding", r"\bamplitude encoding\b|\bamplitude embedding\b"),
        ("Quantum memristor", r"\bquantum memristors?\b|\bphotonic quantum memristors?\b"),
        ("Fidelity Kernel", r"\bfidelity[-\s]?based kernel\b|\bfidelity kernel\b|\bquantum fidelity kernel\b"),
        ("Adaptive state injection", r"\badaptive state injection\b"),
        ("Quantum Convolutional Neural Network", r"\bQCNNs?\b|\bquantum convolutional neural networks?\b"),
        ("Quantum Generative Adversarial Network", r"\bQGANs?\b|\bquantum generative adversarial networks?\b"),
        ("Quantum Long Short-Term Memory", r"\bQLSTM\b|\bQuantum LSTM\b|\bQuantum Long Short[-\s]?Term Memory\b"),
        ("Quantum Relational Knowledge Distillation", r"\bQRKD\b|\bQuantum Relational Knowledge Distillation\b"),
        ("Strong Linear Optical Simulation", r"\bSLOS\b|\bStrong Linear Optical Simulation\b"),
        ("Quantum Reservoir Computing", r"\bquantum reservoir computing\b|\bquantum optical reservoir computing\b"),
        ("QLOQ", r"\bQLOQ\b"),
        ("MNIST", r"\bMNIST\b"),
        ("CIFAR-10", r"\bCIFAR[-\s]?10\b|\bCIFAR10\b"),
        ("SST2", r"\bSST[-\s]?2\b|\bSST2\b|Stanford Sentiment Treebank 2"),
        ("Temporal entanglement", r"\btemporal entanglement\b"),
        ("Pointer states", r"\bpointer states?\b"),
        ("Synesthesia", r"\bsyn(?:a)?esthesia\b"),
        ("Cross-domain mapping", r"\bcross[-\s]?domain mappings?\b|\bcross[-\s]?modal mappings?\b"),
        ("Unruptured Intracranial Aneurysm", r"\bUIAs?\b|\bunruptured intracranial aneurysms?\b"),
        ("TOF-MRA", r"\bTOF[-\s]?MRA\b|\btime[-\s]?of[-\s]?flight magnetic resonance angiography\b"),
        ("ADAM dataset", r"\bAneurysm Detection And segMentation\b|\bADAM dataset\b"),
        ("ADAM challenge", r"\bADAM challenge\b"),
        ("PHASES score", r"\bPHASES score\b"),
        ("Computer-aided detection", r"\bcomputer[-\s]?aided detection\b|\bCAD system\b|\bCAD tool\b"),
        ("3D U-Net", r"\b3D[-\s]?U[-\s]?Net\b|\b3D UNET\b"),
        ("Satisfaction of Search", r"\bsatisfaction[-\s]?of[-\s]?search(?: effect)?\b"),
        ("McNemar's test", r"\bMcNemar[’']?s test\b"),
        ("Wilcoxon signed-rank test", r"\bWilcoxon signed[-\s]?rank tests?\b"),
    )

    RL_EMOTION_LABELS = {
        "Homeostasis",
        "Extrinsic motivation",
        "Intrinsic motivation",
        "Motivated reinforcement learning",
        "Appraisal theory",
        "Prospect Theory",
        "Average reward",
        "Categorical emotion",
        "Dimensional emotion",
        "Model-based RL",
        "POMDP",
        "Well-being",
        "Model uncertainty",
        "Novelty",
        "Recency",
        "Control/Power",
        "Motivational relevance",
        "Intrinsic pleasantness",
        "Social fairness",
        "Social accountability",
        "Valence",
        "Arousal",
        "Dopamine",
        "Serotonin",
        "Noradrenaline",
        "Acetylcholine",
        "Learning rate",
        "Discount factor",
        "Boltzmann action selection temperature",
        "Fuzzy logic",
        "Transition model",
        "Forward simulation",
        "Goal-oriented action planning",
        "Bio-inspiration",
        "Developmental robotics",
        "Planning community heuristics",
        "Emotional feedback",
        "Human-robot interaction",
        "Affective modelling",
        "Affective Computing",
        "Emotion modelling",
        "Emotional agents",
        "Reward shaping",
        "Policy gradient",
        "Value iteration",
        "Multi-agent reinforcement learning",
        "Intrinsic reward",
        "Extrinsic reward",
        "Cognitive appraisal",
        "Appraisal dimensions",
        "Human feedback",
        "Homeostatic reinforcement learning",
        "KL-divergence",
        "L1 norm",
        "Euclidean distance",
        "Set point",
        "Drive",
        "Primary reinforcers",
        "Emotion elicitation categories",
        "Emotion type classification",
    }

    OFFICIAL_STATISTICS_LABELS = {
        "Official Statistics",
        "Data Source Changes",
        "External Data Sources",
        "Concept Drift",
        "Bias",
        "Data Availability",
        "Data Validity",
        "Data Accuracy",
        "Data Completeness",
        "Statistical Neutrality",
        "Statistical Reporting",
        "Data Source Ownership",
        "Ethics",
        "Regulation",
        "Public Perception",
        "Privacy",
        "Robustness",
        "Monitoring",
        "Model Retraining",
        "Data Pipeline",
        "Data Distribution",
        "Derived Data Fields",
        "Data Frequency",
        "Data Source Discontinuation",
    }
    QML_LABELS = {
        "Quantum Machine Learning",
        "Photonic Quantum Machine Learning",
        "MerLin",
        "Fock space",
        "Linear-optical circuits",
        "QuantumLayer",
        "Angle encoding",
        "Amplitude encoding",
        "Quantum memristor",
        "Fidelity Kernel",
        "Adaptive state injection",
        "Quantum Convolutional Neural Network",
        "Quantum Generative Adversarial Network",
        "Quantum Long Short-Term Memory",
        "Quantum Relational Knowledge Distillation",
        "Strong Linear Optical Simulation",
        "Quantum Reservoir Computing",
        "QLOQ",
        "MNIST",
        "CIFAR-10",
        "SST2",
    }

    GENERIC_ACCEPTED_CONCEPT_BLOCKLIST = {
        "Machine Learning",
        "Gridworld",
        "Prey and predators",
        "Mazes",
        "State",
        "Action selection",
        "Exploration",
        "Transparency",
    }

    SURVEY_BACKGROUND_METHODS = {
        "Q-learning",
        "SARSA",
        "TD(lambda)",
        "Dynamic Programming",
        "TD learning",
        "Value iteration",
        "Policy gradient",
        "Reward shaping",
        "Actor-Critic",
    }

    TITLE_STOPWORDS = {
        "a",
        "an",
        "and",
        "are",
        "as",
        "at",
        "based",
        "by",
        "for",
        "from",
        "in",
        "into",
        "is",
        "its",
        "of",
        "on",
        "or",
        "our",
        "the",
        "to",
        "using",
        "via",
        "with",
    }

    MATH_PATTERNS: tuple[tuple[str, str], ...] = (
        ("formula", r"\$[^$]{1,300}\$|\\\[[\s\S]{1,600}?\\\]|\\begin\{equation\}"),
        ("formula", r"\bequation\b|\bformula\b"),
        ("theorem", r"\btheorem\b|\bproof\b"),
        ("table", r"\bTable\s+\d+\b"),
        ("reward_function", r"\breward function\b|\bR\s*\(\s*s\s*,\s*a"),
        ("value_function", r"\bvalue function\b|\bV\s*\(\s*s\s*\)|\bQ\s*\(\s*s\s*,\s*a\s*\)"),
        ("optimization_objective", r"\bloss function\b|\bobjective function\b|\barg\s*max\b|\barg\s*min\b"),
        ("probabilistic_model", r"\bBayesian\b|\bprobabilistic\b|\bp\s*\(\s*[^)]+\s*\)"),
    )
    LEGACY_ARXIV_CATEGORY_RE = (
        r"(?:astro-ph|cond-mat|cs|gr-qc|hep-ex|hep-lat|hep-ph|hep-th|"
        r"math-ph|math|nlin|nucl-ex|nucl-th|physics|q-bio|q-fin|quant-ph|stat)"
    )

    def __init__(
        self,
        llm_router: LLMRouter,
        quality_db_path: str | None = None,
    ) -> None:
        """
        Initialize extractor with an LLM router and optional quality database.

        Args:
            llm_router: Configured LLMRouter instance for model calls.
            quality_db_path: DuckDB path for extraction_quality telemetry. Set
                to None to disable quality writes, for example in isolated tests.
        """
        self.llm = llm_router
        self.quality_db_path = quality_db_path

    @staticmethod
    def _build_extraction_text(paper_text: str, max_chars: int = 60000) -> str:
        """
        Build a bounded paper text that preserves full-paper coverage.

        The previous extractor capped input at 12k characters, which can remove
        algorithm mentions from long surveys. This keeps much more text while
        still staying below a 32k-token context for typical parsed papers.
        """
        text = EntityExtractor._clean_extraction_source_text(paper_text)
        if len(text) <= max_chars:
            return text

        head_chars = max_chars // 3
        tail_chars = max_chars // 6
        middle_budget = max_chars - head_chars - tail_chars
        keywords = [
            r"Q[\s-]?learning",
            r"SARSA",
            r"TD\s*\(?\s*(?:lambda|\\lambda|λ)\s*\)?",
            r"REINFORCE",
            r"Actor[-\s]?Critic",
            r"Dynamic Programming",
            r"baseline",
            r"taxonomy",
            r"survey",
            r"method",
            r"Table\s+\d+",
            r"equation|formula|theorem|proof",
        ]

        excerpts: list[str] = [text[:head_chars].strip()]
        seen_spans: set[tuple[int, int]] = set()
        window = 1800

        for pattern in keywords:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                start = max(0, match.start() - window // 2)
                end = min(len(text), match.end() + window // 2)
                span = (start, end)
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                excerpts.append(text[start:end].strip())
                if sum(len(item) for item in excerpts) >= head_chars + middle_budget:
                    break
            if sum(len(item) for item in excerpts) >= head_chars + middle_budget:
                break

        excerpts.append(text[-tail_chars:].strip())
        return "\n\n---\n\n".join(item for item in excerpts if item)[:max_chars]

    @staticmethod
    def _build_extraction_chunks(
        paper_text: str,
        context_size: int = 32768,
        max_chunk_chars: int | None = None,
        overlap_chars: int = 900,
        max_chunks: int = 8,
    ) -> list[str]:
        """
        Split long papers into independent extraction windows.

        Local models often under-enumerate when a whole paper is supplied even
        when it fits into context. Chunking structural extraction keeps each
        call focused and guarantees a fresh message list per chunk/paper.
        """
        text = EntityExtractor._clean_extraction_source_text(paper_text)
        if not text:
            return [""]

        budget = max_chunk_chars or EntityExtractor._chunk_char_budget(context_size)
        if len(text) <= budget:
            return [text]

        raw_units = re.split(r"\n\s*(?:---PAGE BREAK---|\f)\s*\n|\n(?=\d+(?:\.\d+)*\s+[A-Z][^\n]{3,100}\n)", text)
        units = [unit.strip() for unit in raw_units if unit and unit.strip()]
        if not units:
            units = [text]

        chunks: list[str] = []
        current = ""
        for unit in units:
            if len(unit) > budget:
                if current:
                    chunks.append(current.strip())
                    current = ""
                for start in range(0, len(unit), budget - overlap_chars):
                    part = unit[start : start + budget]
                    if part.strip():
                        chunks.append(part.strip())
                continue

            candidate = f"{current}\n\n{unit}".strip() if current else unit
            if len(candidate) > budget and current:
                chunks.append(current.strip())
                overlap = current[-overlap_chars:] if overlap_chars else ""
                current = f"{overlap}\n\n{unit}".strip()
            else:
                current = candidate

        if current.strip():
            chunks.append(current.strip())
        chunks = chunks or [text[:budget]]
        if len(chunks) <= max_chunks:
            return chunks

        selected_indexes = [
            round(index * (len(chunks) - 1) / (max_chunks - 1))
            for index in range(max_chunks)
        ]
        return [chunks[index] for index in selected_indexes]

    @staticmethod
    def _chunk_char_budget(context_size: int) -> int:
        """
        Estimate a safe per-call paper-text budget for local OpenAI-compatible servers.

        LM Studio/llama.cpp rejects requests when the prompt alone exceeds the
        loaded model context. The configured context can be larger than the
        server's actual slot, so keep a conservative prompt reserve for the
        structural instructions, candidate hints, and chat template overhead.
        """
        try:
            ctx = max(4096, int(context_size))
        except (TypeError, ValueError):
            ctx = 32768

        prompt_overhead_tokens = 5200
        usable_prompt_tokens = max(1200, ctx - prompt_overhead_tokens)
        estimated_chars_per_token = 1.25
        return max(6000, min(18000, int(usable_prompt_tokens * estimated_chars_per_token)))

    @staticmethod
    def _clean_extraction_source_text(paper_text: str) -> str:
        """Remove parser page-break artifacts before LLM and deterministic extraction."""
        text = normalize_scientific_text(paper_text)
        page_break = r"(?:---\s*PAGE\s*BREAK\s*---|---\s*Page\s*Break\s*---|\f)"
        text = re.sub(
            rf"\bModi\s*{page_break}\s*Cation\b",
            "Modification",
            text,
            flags=re.IGNORECASE,
        )

        def join_suffix(match: re.Match[str]) -> str:
            prefix = match.group(1)
            suffix = match.group(2)
            return prefix + suffix.lower()

        text = re.sub(
            rf"\b([A-Za-z]{{3,}})\s*{page_break}\s*(Cation|Fication|Tion|Zation|Sation|Ment|Ness|Able|Ible|Ing|Ed|Al|Ity)\b",
            join_suffix,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(page_break, "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"\bModi\s+Cation\b", "Modification", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\b([A-Za-z]{3,})\s+(Cation|Fication|Tion|Zation|Sation)\b",
            lambda match: match.group(1) + match.group(2).lower(),
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()

    def extract(
        self,
        paper_id: str,
        paper_text: str,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        """
        Extract entities from paper text using two sequential LLM calls.

        Args:
            paper_id: Unique paper identifier.
            paper_text: Full paper text or significant parsed portion.
            provider: Optional LLM provider override.
            overrides: Optional base settings such as model, context_size, and
                timeout_seconds. Per-call extraction settings override
                temperature, top_p, and max_tokens.

        Returns:
            ExtractionResult with merged structural and semantic outputs.
            Model or parsing failures return partial results rather than
            raising, with parse quality details in raw_response.
        """
        started = time.perf_counter()
        source_text = self._clean_extraction_source_text(paper_text)
        extraction_text = self._text_before_references(source_text)
        scan = self._scan_paper_text(extraction_text)
        semantic_text = self._build_extraction_text(extraction_text, max_chars=30000)
        base_overrides = dict(overrides or {})
        extraction_mode = self._normalize_extraction_mode(base_overrides.pop("extraction_mode", None))
        base_overrides["context_size"] = self._effective_context_size(provider, base_overrides)

        chunks = self._build_extraction_chunks(
            extraction_text,
            context_size=int(base_overrides["context_size"]),
        )
        structural_calls = [
            self._run_structural_call(
                chunk,
                provider,
                base_overrides,
                scan=scan,
                chunk_index=index,
                chunk_count=len(chunks),
            )
            for index, chunk in enumerate(chunks, start=1)
        ]
        concepts = self._merge_entity_lists(
            *[self._coerce_list(call.data.get("concepts")) for call in structural_calls],
        )
        methods = self._merge_entity_lists(
            *[self._coerce_list(call.data.get("methods")) for call in structural_calls],
        )
        concept_candidates = self._merge_entity_lists(
            scan.concepts,
            *[self._coerce_list(call.data.get("concept_candidates")) for call in structural_calls],
        )
        method_candidates = self._merge_entity_lists(
            scan.methods,
            *[self._coerce_list(call.data.get("method_candidates")) for call in structural_calls],
        )
        concepts_retry: ParsedLLMResponse | None = None
        if self._should_retry_concepts(concepts, structural_calls, scan):
            logger.warning("Structural concept extraction failed or was too thin; running concepts-only retry")
            concepts_retry = self._run_concepts_only_retry(extraction_text, provider, base_overrides, scan)
            retry_concepts = self._coerce_list(concepts_retry.data.get("concepts"))
            if retry_concepts:
                concepts = self._merge_entity_lists(concepts, retry_concepts)
            else:
                logger.error(
                    "Concepts-only retry failed for paper_id=%s; keeping deterministic candidates only",
                    paper_id,
                )
        methods_retry: ParsedLLMResponse | None = None
        if self._should_retry_methods(methods, structural_calls, concepts, scan):
            logger.warning("Methods lost in partial recovery — running methods-only retry")
            methods_retry = self._run_methods_only_retry(extraction_text, provider, base_overrides)
            retry_methods = self._coerce_list(methods_retry.data.get("methods"))
            if retry_methods:
                methods = self._merge_entity_lists(retry_methods)
            else:
                methods = []
                logger.error(
                    "Methods-only retry failed for paper_id=%s after partial recovery; setting methods to []",
                    paper_id,
                )

        regex_result = self._validate_concepts_with_regex(extraction_text, concept_candidates)
        concept_candidates = regex_result.concepts
        concepts = filter_concepts(
            concepts,
            title=self._paper_title_from_text(extraction_text),
        )
        concepts = self._post_process_concepts(concepts)
        concepts = self._calibrate_concept_confidences(extraction_text, concepts)
        concept_candidates = filter_concepts(
            concept_candidates,
            title=self._paper_title_from_text(extraction_text),
        )
        concept_candidates = self._post_process_concepts(concept_candidates)
        concept_candidates = self._calibrate_concept_confidences(extraction_text, concept_candidates)
        methods = enrich_method_domains(deduplicate_methods(methods))
        method_candidates = enrich_method_domains(deduplicate_methods(method_candidates))

        detected_paper_type = self._detect_paper_type(extraction_text)
        raw_concepts = concepts
        raw_methods = methods
        concepts = self._accept_concepts(extraction_text, raw_concepts, detected_paper_type)
        methods = self._accept_methods(extraction_text, raw_methods, detected_paper_type)
        concept_candidates = self._merge_entity_lists(
            concept_candidates,
            self._rejected_as_candidates(raw_concepts, concepts, "not_accepted_for_auto_kg"),
        )
        method_candidates = self._merge_entity_lists(
            method_candidates,
            self._rejected_as_candidates(raw_methods, methods, "not_accepted_for_auto_kg"),
        )
        concept_candidates = self._candidate_only(extraction_text, concept_candidates, concepts, default_role="possible_concept")
        method_candidates = self._candidate_only(extraction_text, method_candidates, methods, default_role="method_candidate")

        claims_pass: ParsedLLMResponse | None = None
        semantic_retry: ParsedLLMResponse | None = None
        if extraction_mode == "quick":
            semantic = ParsedLLMResponse(
                data={
                    "paper_type": detected_paper_type or "research",
                    "paper_node": {},
                    "claims": [],
                    "cross_domain_hints": [],
                    "terminology_conflicts": [],
                    "temporal_coverage": {},
                    "mathematical_content": {"has_formulas": False, "formula_types": []},
                    "language_detected": "en",
                },
                parse_quality="skipped",
                raw_text="semantic extraction skipped in quick mode",
                tokens_used=0,
            )
            semantic_data = semantic.data
            claims: list[dict[str, Any]] = []
            cross_domain_hints: list[dict[str, Any]] = []
            terminology_conflicts: list[dict[str, Any]] = []
        else:
            semantic = self._run_semantic_call(
                text_summary=semantic_text,
                structural_data={"concepts": concepts, "methods": methods},
                provider=provider,
                base_overrides=base_overrides,
            )
            semantic_data = semantic.data
            claims = self._merge_claim_lists(self._coerce_list(semantic_data.get("claims")))
            cross_domain_hints = self._coerce_list(semantic_data.get("cross_domain_hints"))
            terminology_conflicts = self._coerce_list(semantic_data.get("terminology_conflicts"))
            if self._should_retry_semantic_lists(
                semantic,
                claims,
                cross_domain_hints,
                terminology_conflicts,
                concepts,
                extraction_text,
            ):
                logger.warning("Semantic extraction too thin — running claims/hints retry")
                semantic_retry = self._run_semantic_lists_retry(semantic_text, provider, base_overrides)
                retry_data = semantic_retry.data
                claims = self._coerce_list(retry_data.get("claims")) or claims
                cross_domain_hints = self._coerce_list(retry_data.get("cross_domain_hints")) or cross_domain_hints
                terminology_conflicts = self._coerce_list(retry_data.get("terminology_conflicts")) or terminology_conflicts
                if not claims and semantic.parse_quality == "partial":
                    logger.error(
                        "Claims retry failed for paper_id=%s after partial semantic recovery; setting claims to []",
                        paper_id,
                    )
            if self._should_run_dedicated_claims_pass(extraction_text, claims):
                claims_pass = self._run_claims_call(semantic_text, provider, base_overrides)
                claims = self._merge_claim_lists(claims, self._coerce_list(claims_pass.data.get("claims")))
            claims = claims or self._fallback_claims_from_text(extraction_text, paper_type_hint=detected_paper_type)
            cross_domain_hints = cross_domain_hints or self._fallback_cross_domain_hints(concepts)
            terminology_conflicts = self._merge_terminology_conflicts(
                terminology_conflicts,
                self._fallback_terminology_conflicts([*concepts, *concept_candidates]),
            )
            terminology_conflicts = self._filter_terminology_conflicts(
                terminology_conflicts,
                [*concepts, *methods, *concept_candidates, *method_candidates],
            )

        mathematical_content = self._coerce_dict(semantic_data.get("mathematical_content"))
        if regex_result.has_formulas:
            formula_types = {
                str(item)
                for item in self._coerce_list(mathematical_content.get("formula_types"))
                if item
            }
            formula_types.update(regex_result.formula_types)
            mathematical_content["has_formulas"] = True
            mathematical_content["formula_types"] = sorted(formula_types)
        else:
            mathematical_content.setdefault("has_formulas", False)
            mathematical_content.setdefault("formula_types", [])

        temporal_coverage = self._coerce_dict(semantic_data.get("temporal_coverage"))
        if scan.paper_year and not temporal_coverage.get("paper_year"):
            temporal_coverage["paper_year"] = scan.paper_year

        paper_type = self._resolve_paper_type(semantic_data.get("paper_type"), detected_paper_type, extraction_text)
        paper_node = self._build_paper_node(
            paper_id=paper_id,
            paper_text=source_text,
            paper_type=paper_type,
            semantic_paper_node=self._coerce_dict(semantic_data.get("paper_node")),
            temporal_coverage=temporal_coverage,
            language_detected=str(semantic_data.get("language_detected") or "en"),
        )
        if paper_node.get("paper_year"):
            temporal_coverage["paper_year"] = paper_node.get("paper_year")
        result_paper_id = str(paper_node.get("paper_id") or paper_id)
        structural_parse_quality = self._chunked_parse_quality(structural_calls)
        parse_quality = self._combined_parse_quality(
            structural_parse_quality,
            "clean" if extraction_mode == "quick" else self._worst_parse_quality(
                [semantic.parse_quality]
                + ([claims_pass.parse_quality] if claims_pass is not None else [])
            ),
        )
        duration = time.perf_counter() - started
        metadata_validation = self._metadata_validation(
            paper_id=paper_id,
            paper_node=paper_node,
        )
        warnings = self._quality_warnings(
            paper_type=paper_type,
            concept_count=len(concepts),
            method_count=len(methods),
            text_length=len(extraction_text or ""),
            parse_quality=parse_quality,
            paper_id=paper_id,
            paper_node=paper_node,
        )

        call_diagnostics = self._call_diagnostics(
            structural_calls,
            semantic,
            claims_pass,
            concepts_retry=concepts_retry,
            methods_retry=methods_retry,
            semantic_retry=semantic_retry,
        )
        fatal_failure_reason = self._fatal_extraction_failure_reason(
            parse_quality=parse_quality,
            concepts=concepts,
            methods=methods,
            call_diagnostics=call_diagnostics,
        )

        result_payload = {
            "paper_type": paper_type,
            "paper_node": paper_node,
            "concepts": concepts,
            "methods": methods,
            "concept_candidates": concept_candidates,
            "method_candidates": method_candidates,
            "relations": [],
            "claims": claims,
            "cross_domain_hints": cross_domain_hints,
            "terminology_conflicts": terminology_conflicts,
            "temporal_coverage": temporal_coverage,
            "mathematical_content": mathematical_content,
            "language_detected": str(semantic_data.get("language_detected") or "en"),
            "extraction_parse_quality": parse_quality,
            "auto_detected_concepts": regex_result.auto_detected_count,
            "deterministic_candidate_count": len(concept_candidates) + len(method_candidates),
            "quality_warnings": warnings,
            "metadata_status": metadata_validation["metadata_status"],
            "blocking_errors": metadata_validation["blocking_errors"],
            "chunk_count": len(chunks),
            "extraction_mode": extraction_mode,
            "call_1_parse_quality": structural_parse_quality,
            "call_2_parse_quality": semantic.parse_quality,
            "concepts_retry_parse_quality": concepts_retry.parse_quality if concepts_retry else None,
            "methods_retry_parse_quality": methods_retry.parse_quality if methods_retry else None,
            "semantic_retry_parse_quality": semantic_retry.parse_quality if semantic_retry else None,
            "claims_pass_parse_quality": claims_pass.parse_quality if claims_pass else None,
            "fatal_llm_error": bool(fatal_failure_reason),
            "failure_reason": fatal_failure_reason,
            "call_diagnostics": call_diagnostics,
        }

        self._log_count_warnings(paper_type, len(concepts), warnings)
        self._write_quality_record(
            paper_id=result_paper_id,
            payload=result_payload,
            duration_seconds=duration,
            provider=provider,
            overrides=base_overrides,
            call_1_tokens_used=sum(
                call.tokens_used or self._estimate_tokens(call.raw_text)
                for call in structural_calls
            ),
            call_2_tokens_used=semantic.tokens_used or self._estimate_tokens(semantic.raw_text),
        )

        return ExtractionResult(
            paper_id=result_paper_id,
            paper_type=paper_type,
            paper_node=paper_node,
            concepts=concepts,
            methods=methods,
            concept_candidates=concept_candidates,
            method_candidates=method_candidates,
            relations=[],
            claims=result_payload["claims"],
            cross_domain_hints=result_payload["cross_domain_hints"],
            terminology_conflicts=result_payload["terminology_conflicts"],
            temporal_coverage=result_payload["temporal_coverage"],
            mathematical_content=mathematical_content,
            language_detected=result_payload["language_detected"],
            quality_warnings=warnings,
            metadata_status=result_payload["metadata_status"],
            blocking_errors=result_payload["blocking_errors"],
            candidate_count=int(result_payload["deterministic_candidate_count"]),
            extraction_diagnostics={
                "chunk_count": len(chunks),
                "parse_quality": parse_quality,
                "call_1_parse_quality": result_payload["call_1_parse_quality"],
                "call_2_parse_quality": semantic.parse_quality,
                "concepts_retry_parse_quality": result_payload["concepts_retry_parse_quality"],
                "methods_retry_parse_quality": result_payload["methods_retry_parse_quality"],
                "semantic_retry_parse_quality": result_payload["semantic_retry_parse_quality"],
                "claims_pass_parse_quality": result_payload["claims_pass_parse_quality"],
                "fatal_llm_error": bool(fatal_failure_reason),
                "failure_reason": fatal_failure_reason,
                "calls": result_payload["call_diagnostics"],
            },
            raw_response=json.dumps(result_payload, indent=2, ensure_ascii=False),
            extraction_mode=extraction_mode,
        )

    def _run_structural_call(
        self,
        text_summary: str,
        provider: str | None,
        base_overrides: dict[str, Any],
        scan: DeterministicScanResult | None = None,
        chunk_index: int = 1,
        chunk_count: int = 1,
        retry_split: bool = True,
    ) -> ParsedLLMResponse:
        """Run call 1 for concepts and methods with deterministic settings."""
        candidate_json = "[]"
        if scan is not None:
            hints = self._hints_for_chunk(scan, text_summary, limit=24)
            candidate_json = json.dumps(hints, ensure_ascii=False)
        prompt = (
            self.STRUCTURAL_PROMPT
            .replace("{candidate_json}", candidate_json)
            .replace("{paper_text}", f"[Chunk {chunk_index}/{chunk_count}]\n\n{text_summary}")
        )
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(5000, min(int(base_overrides.get("max_tokens") or 10000), 12000)),
            temperature=0.1,
            top_p=0.85,
        )
        parsed = self._call_and_parse_json(
            [
                {"role": "system", "content": "Return complete JSON only. Do not include markdown, prose, or hidden reasoning. /no_think"},
                {"role": "user", "content": prompt},
            ],
            provider=provider,
            overrides=overrides,
            default={
                "concepts": [],
                "methods": [],
                "concept_candidates": [],
                "method_candidates": [],
            },
        )
        if parsed.parse_quality in {"partial", "failed"} and retry_split and len(text_summary) > 9000:
            split_calls = [
                self._run_structural_call(
                    part,
                    provider,
                    base_overrides,
                    scan=scan,
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                    retry_split=False,
                )
                for part in self._split_text_for_structural_retry(text_summary)
            ]
            merged = {
                "concepts": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("concepts")) for call in split_calls]
                ),
                "methods": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("methods")) for call in split_calls]
                ),
                "concept_candidates": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("concept_candidates")) for call in split_calls]
                ),
                "method_candidates": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("method_candidates")) for call in split_calls]
                ),
            }
            split_quality = self._worst_parse_quality([call.parse_quality for call in split_calls])
            if split_quality in {"clean", "trimmed"} or self._parsed_payload_score(merged) > self._parsed_payload_score(parsed.data):
                return ParsedLLMResponse(
                    data=merged,
                    parse_quality=split_quality,
                    raw_text="\n\n--- SPLIT STRUCTURAL RETRY ---\n\n".join(call.raw_text for call in split_calls),
                    tokens_used=sum(call.tokens_used or self._estimate_tokens(call.raw_text) for call in split_calls),
                )
        return parsed

    @staticmethod
    def _split_text_for_structural_retry(text: str) -> list[str]:
        """Split an oversized malformed structural chunk on paragraph boundaries."""
        cleaned = text.strip()
        if not cleaned:
            return [""]
        midpoint = len(cleaned) // 2
        candidates = [match.start() for match in re.finditer(r"\n\s*\n", cleaned)]
        split_at = min(candidates, key=lambda index: abs(index - midpoint)) if candidates else midpoint
        return [part for part in (cleaned[:split_at].strip(), cleaned[split_at:].strip()) if part]

    def _run_semantic_call(
        self,
        text_summary: str,
        structural_data: dict[str, Any],
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Run call 2 for claims, metadata, and cross-domain analysis."""
        structural_json = json.dumps(self._compact_structural_context(structural_data), ensure_ascii=False)
        prompt = (
            self.SEMANTIC_PROMPT
            .replace("{structural_json}", structural_json)
            .replace("{paper_text}", text_summary)
        )
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(5000, min(int(base_overrides.get("max_tokens") or 8000), 10000)),
            temperature=0.1,
            top_p=0.85,
        )
        return self._call_and_parse_json(
            [
                {"role": "system", "content": "Return complete JSON only. Do not include markdown, prose, or hidden reasoning. /no_think"},
                {"role": "user", "content": prompt},
            ],
            provider=provider,
            overrides=overrides,
            default={
                "paper_type": "research",
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            },
        )

    def _run_claims_call(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Run a dedicated high-recall claims extraction pass."""
        prompt = CLAIMS_EXTRACTION_PROMPT.replace("{paper_text}", paper_text or "")
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(4000, min(int(base_overrides.get("max_tokens") or 8000), 10000)),
            temperature=0.1,
            top_p=0.85,
            json_object=False,
        )

        def llm_call_fn(current_prompt: str) -> Any:
            return self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "Return a valid JSON array only. No markdown, no commentary, no hidden reasoning. /no_think",
                    },
                    {"role": "user", "content": current_prompt},
                ],
                provider=provider,
                overrides=overrides,
            )

        claims = safe_llm_extract(prompt, llm_call_fn, field_name="claims", retries=3)
        return ParsedLLMResponse(
            data={"claims": claims},
            parse_quality="clean",
            raw_text=json.dumps(claims, ensure_ascii=False),
            tokens_used=self._last_tokens_used(),
        )

    def _run_methods_only_retry(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Retry method extraction when partial Call 1 recovery lost the methods key."""
        prompt = self.METHODS_ONLY_PROMPT.replace("{paper_text}", paper_text or "")
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=12000,
            temperature=0.1,
            top_p=0.85,
            json_object=False,
        )
        try:
            raw_response = self.llm.chat(
                [{"role": "user", "content": prompt}],
                provider=provider,
                overrides=overrides,
            )
            if isinstance(raw_response, dict):
                raw_text = str(raw_response.get("content") or raw_response)
            else:
                raw_text = str(raw_response or "")
        except Exception as exc:
            logger.exception("Methods-only retry failed")
            return ParsedLLMResponse(
                data={"methods": []},
                parse_quality="failed",
                raw_text=f"LLM methods-only retry failed: {exc}",
                tokens_used=None,
            )

        parsed = self._parse_json_array_robust(raw_text)
        return ParsedLLMResponse(
            data={"methods": parsed.data},
            parse_quality=parsed.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )

    def _run_concepts_only_retry(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
        scan: DeterministicScanResult,
    ) -> ParsedLLMResponse:
        """Retry concept extraction with a smaller array-only prompt."""
        candidate_json = json.dumps((scan.concepts + scan.methods)[:32], ensure_ascii=False)
        prompt = (
            self.CONCEPTS_ONLY_PROMPT
            .replace("{candidate_json}", candidate_json)
            .replace("{paper_text}", paper_text or "")
        )
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=10000,
            temperature=0.1,
            top_p=0.85,
            json_object=False,
        )
        try:
            raw_response = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "Return a valid JSON array only. No markdown, no commentary, no hidden reasoning. /no_think",
                    },
                    {"role": "user", "content": prompt},
                ],
                provider=provider,
                overrides=overrides,
            )
            raw_text = str(raw_response.get("content") or raw_response) if isinstance(raw_response, dict) else str(raw_response or "")
        except Exception as exc:
            logger.exception("Concepts-only retry failed")
            return ParsedLLMResponse(
                data={"concepts": []},
                parse_quality="failed",
                raw_text=f"LLM concepts-only retry failed: {exc}",
                tokens_used=None,
            )

        parsed_array = self._parse_json_array_robust(raw_text)
        if parsed_array.data:
            return ParsedLLMResponse(
                data={"concepts": parsed_array.data},
                parse_quality=parsed_array.parse_quality,
                raw_text=raw_text,
                tokens_used=self._last_tokens_used(),
            )
        parsed_object = self._parse_json_robust(raw_text, default={"concepts": []})
        return ParsedLLMResponse(
            data={"concepts": self._coerce_list(parsed_object.data.get("concepts"))},
            parse_quality=parsed_object.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )

    def _run_semantic_lists_retry(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Retry semantic list extraction when Call 2 partial recovery loses claims."""
        prompt = self.SEMANTIC_LISTS_RETRY_PROMPT.replace("{paper_text}", paper_text or "")
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(3000, min(int(base_overrides.get("max_tokens") or 6000), 8000)),
            temperature=0.1,
            top_p=0.85,
        )
        return self._call_and_parse_json(
            [
                {"role": "system", "content": "Return complete JSON only. Do not include markdown, prose, or hidden reasoning. /no_think"},
                {"role": "user", "content": prompt},
            ],
            provider=provider,
            overrides=overrides,
            default={"claims": [], "cross_domain_hints": [], "terminology_conflicts": []},
        )

    @classmethod
    def _should_retry_concepts(
        cls,
        concepts: list[dict[str, Any]],
        structural_calls: list[ParsedLLMResponse],
        scan: DeterministicScanResult,
    ) -> bool:
        """Return true when the main structural prompt produced no concepts."""
        if concepts:
            return False
        structural_quality = cls._worst_parse_quality([call.parse_quality for call in structural_calls])
        if structural_quality not in {"partial", "failed"}:
            return False
        return bool(structural_calls or scan.concepts or scan.methods)

    @classmethod
    def _should_retry_methods(
        cls,
        methods: list[dict[str, Any]],
        structural_calls: list[ParsedLLMResponse],
        concepts: list[dict[str, Any]],
        scan: DeterministicScanResult,
    ) -> bool:
        """Return true when partial Call 1 likely lost method extraction."""
        if methods:
            return False
        if cls._worst_parse_quality([call.parse_quality for call in structural_calls]) not in {"partial", "failed"}:
            return False
        if not (concepts or scan.concepts or scan.methods):
            return False
        for call in structural_calls:
            if call.parse_quality in {"partial", "failed"}:
                return True
        return False

    @staticmethod
    def _should_retry_semantic_lists(
        semantic: ParsedLLMResponse,
        claims: list[Any],
        cross_domain_hints: list[Any],
        terminology_conflicts: list[Any],
        concepts: list[dict[str, Any]],
        paper_text: str,
    ) -> bool:
        """Return true when semantic extraction is too thin for a meaningful KG."""
        is_long_or_rich = bool(concepts or len(paper_text or "") >= 5000)
        if not is_long_or_rich:
            return False
        if semantic.parse_quality == "partial" and not claims:
            return True
        if semantic.parse_quality == "failed":
            return False
        if len(claims) < 3 and len(paper_text or "") >= 12000 and re.search(
            r"\b(we|this paper|this article|this survey|our|results?|findings?|show|shows|provide|provides|propose|presents?|demonstrate|suggest|challenge|taxonomy|framework)\b",
            paper_text or "",
            flags=re.IGNORECASE,
        ):
            return True
        return False

    @staticmethod
    def _should_run_dedicated_claims_pass(paper_text: str, claims: list[Any]) -> bool:
        """Use the extra claims pass for full papers where shallow claims are likely."""
        text_length = len(paper_text or "")
        if text_length < 12000:
            return False
        if not re.search(
            r"\b(we|this paper|this article|this survey|our|results?|findings?|show|shows|provide|provides|propose|presents?|demonstrate|suggest|challenge|taxonomy|framework)\b",
            paper_text or "",
            flags=re.IGNORECASE,
        ):
            return False
        return len(claims) < 3

    @classmethod
    def _merge_claim_lists(cls, *claim_lists: list[Any]) -> list[dict[str, Any]]:
        """Merge claim lists by normalized statement, preserving first-seen order."""
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for claim_list in claim_lists:
            for claim in claim_list:
                if not isinstance(claim, dict):
                    continue
                statement = re.sub(r"\s+", " ", str(claim.get("statement") or "")).strip()
                normalized = cls._normalize_label(statement)
                if not normalized:
                    continue
                item = dict(claim)
                item["statement"] = statement
                item.setdefault("evidence_type", "theoretical")
                item["claim_type"] = cls._infer_claim_type(item)
                item["negated"] = cls._normalize_claim_negation(item)
                item.setdefault("attributed_to", "this_paper")
                if normalized not in merged:
                    merged[normalized] = item
                    order.append(normalized)
        return [merged[key] for key in order]

    @staticmethod
    def _infer_claim_type(claim: dict[str, Any]) -> str:
        existing = str(claim.get("claim_type") or "").strip().lower()
        allowed = {"contribution", "finding", "limitation", "negative_result", "comparison", "recommendation"}
        if existing in allowed:
            return existing

        statement = str(claim.get("statement") or "").lower()
        if re.search(r"\b(too simple|insufficient|limited|limitation|cannot draw|unable to draw|hard to draw|not enough to)\b", statement):
            return "limitation"
        if re.search(r"\b(no evidence|does not|do not|did not|failed to|fails to|cannot|unable to|no significant)\b", statement):
            return "negative_result"
        if re.search(r"\b(outperform|outperforms|more robust|less robust|more accurate|less accurate|compared|whereas|than)\b", statement):
            return "comparison"
        if re.search(r"\b(should|recommend|requires?|must|need to|necessary)\b", statement):
            return "recommendation"
        if re.search(r"\b(introduce|introduces|propose|proposes|present|presents|provide|provides|contribute|contributes)\b", statement):
            return "contribution"
        return "finding"

    @classmethod
    def _normalize_claim_negation(cls, claim: dict[str, Any]) -> bool:
        statement = str(claim.get("statement") or "").lower()
        if re.search(
            r"\bwithout\s+(?:a\s+)?(?:significant\s+|substantial\s+|meaningful\s+)?"
            r"(?:loss|degradation|performance loss|drop|reduction)\b",
            statement,
        ) or re.search(r"\bwithout\s+(?:sacrificing|compromising|hurting)\b", statement):
            return False
        explicit_negation = bool(
            re.search(
                r"\b(no evidence|no significant|does not|do not|did not|cannot|can not|unable to|failed to|fails to)\b",
                statement,
            )
        )
        if explicit_negation:
            return True
        if str(claim.get("claim_type") or "").lower() in {"limitation", "negative_result"}:
            return False
        return bool(claim.get("negated"))

    @staticmethod
    def _hints_for_chunk(
        scan: DeterministicScanResult,
        chunk_text: str,
        limit: int = 24,
    ) -> list[dict[str, Any]]:
        chunk_lower = (chunk_text or "").lower()
        hints: list[dict[str, Any]] = []
        for item in scan.concepts + scan.methods:
            label = str(item.get("label") or "")
            if label and label.lower() in chunk_lower:
                hints.append(item)
            if len(hints) >= limit:
                return hints
        return (scan.concepts + scan.methods)[:limit]

    @staticmethod
    def _compact_structural_context(structural_data: dict[str, Any]) -> dict[str, Any]:
        def compact(items: Any, limit: int) -> list[dict[str, Any]]:
            rows: list[dict[str, Any]] = []
            if not isinstance(items, list):
                return rows
            for item in items[:limit]:
                if not isinstance(item, dict):
                    continue
                rows.append(
                    {
                        "label": item.get("label"),
                        "context": str(item.get("context") or item.get("description") or "")[:160],
                    }
                )
            return rows

        return {
            "concepts": compact(structural_data.get("concepts"), 60),
            "methods": compact(structural_data.get("methods"), 40),
        }

    def _call_and_parse_json(
        self,
        messages: list[dict[str, str]],
        provider: str | None,
        overrides: dict[str, Any],
        default: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Call the model and parse JSON without aborting the pipeline."""
        try:
            raw_response = self.llm.chat(messages, provider=provider, overrides=overrides)
            if isinstance(raw_response, dict):
                raw_text = str(raw_response.get("content") or raw_response)
            else:
                raw_text = str(raw_response or "")
        except Exception as exc:
            logger.exception("LLM extraction call failed")
            return ParsedLLMResponse(
                data=dict(default),
                parse_quality="failed",
                raw_text=f"LLM call failed: {exc}",
                tokens_used=None,
            )

        parsed = self._parse_json_robust(raw_text, default=default)
        if parsed.parse_quality == "partial":
            retry = self._retry_strict_json(messages, provider, overrides, default)
            if retry.parse_quality in {"clean", "trimmed"} or self._parsed_payload_score(retry.data) > self._parsed_payload_score(parsed.data):
                return retry
        return ParsedLLMResponse(
            data=parsed.data,
            parse_quality=parsed.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )

    def _retry_strict_json(
        self,
        messages: list[dict[str, str]],
        provider: str | None,
        overrides: dict[str, Any],
        default: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Retry a malformed JSON call once with stricter decoding instructions."""
        retry_overrides = dict(overrides)
        retry_overrides["temperature"] = 0.05
        retry_overrides["top_p"] = min(float(retry_overrides.get("top_p") or 0.85), 0.8)
        retry_overrides["max_tokens"] = min(16000, max(int(retry_overrides.get("max_tokens") or 8000), 10000))
        retry_messages = [
            {
                "role": "system",
                "content": (
                    "The previous response was invalid or incomplete. "
                    "Return one complete valid JSON object only. No markdown, no commentary, no hidden reasoning. /no_think"
                ),
            },
            *messages,
        ]
        try:
            raw_response = self.llm.chat(retry_messages, provider=provider, overrides=retry_overrides)
            raw_text = str(raw_response.get("content") or raw_response) if isinstance(raw_response, dict) else str(raw_response or "")
        except Exception as exc:
            logger.exception("Strict JSON retry failed")
            return ParsedLLMResponse(
                data=dict(default),
                parse_quality="failed",
                raw_text=f"Strict JSON retry failed: {exc}",
                tokens_used=None,
            )
        parsed = self._parse_json_robust(raw_text, default=default)
        return ParsedLLMResponse(
            data=parsed.data,
            parse_quality=parsed.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )

    @staticmethod
    def _parsed_payload_score(data: dict[str, Any]) -> int:
        score = 0
        for value in data.values():
            if isinstance(value, list):
                score += len(value)
            elif isinstance(value, dict):
                score += len(value)
            elif value:
                score += 1
        return score

    @staticmethod
    def _parse_json_robust(raw_text: str, default: dict[str, Any]) -> ParsedLLMResponse:
        """
        Parse model JSON with clean, trimmed, and partial fallbacks.

        Fallback order:
        1. Direct json.loads.
        2. Trim from first "{" to last "}".
        3. Partial reconstruction from obvious top-level scalar and array keys.
        """
        raw = EntityExtractor._sanitize_json_text(raw_text)
        if not raw:
            return ParsedLLMResponse(data=dict(default), parse_quality="partial", raw_text=raw_text)

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return ParsedLLMResponse(data=parsed, parse_quality="clean", raw_text=raw_text)
        except json.JSONDecodeError:
            pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            trimmed = raw[start : end + 1]
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, dict):
                    return ParsedLLMResponse(data=parsed, parse_quality="trimmed", raw_text=raw_text)
            except json.JSONDecodeError:
                pass

        partial = dict(default)
        for key in partial:
            value = EntityExtractor._extract_partial_json_value(raw, key)
            if value is not None:
                partial[key] = value
        for key in ("paper_type", "language_detected"):
            match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', raw)
            if match:
                partial[key] = match.group(1)
        return ParsedLLMResponse(data=partial, parse_quality="partial", raw_text=raw_text)

    @staticmethod
    def _sanitize_json_text(raw_text: str) -> str:
        """Remove common local-model wrappers before JSON parsing."""
        raw = (raw_text or "").strip()
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()
        return raw

    @staticmethod
    def _parse_json_array_robust(raw_text: str) -> ParsedLLMResponse:
        """Parse a model response expected to be a JSON array."""
        raw = EntityExtractor._sanitize_json_text(raw_text)
        if not raw:
            return ParsedLLMResponse(data=[], parse_quality="partial", raw_text=raw_text)

        try:
            parsed = json.loads(raw)
            return ParsedLLMResponse(
                data=parsed if isinstance(parsed, list) else [],
                parse_quality="clean" if isinstance(parsed, list) else "partial",
                raw_text=raw_text,
            )
        except json.JSONDecodeError:
            pass

        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            trimmed = raw[start : end + 1]
            try:
                parsed = json.loads(trimmed)
                return ParsedLLMResponse(
                    data=parsed if isinstance(parsed, list) else [],
                    parse_quality="trimmed" if isinstance(parsed, list) else "partial",
                    raw_text=raw_text,
                )
            except json.JSONDecodeError:
                pass
        return ParsedLLMResponse(data=[], parse_quality="partial", raw_text=raw_text)

    @staticmethod
    def _raw_has_json_key(raw_text: str, key: str) -> bool:
        """Check whether a key appears in the raw JSON-ish response text."""
        return re.search(rf'"{re.escape(key)}"\s*:', raw_text or "") is not None

    @staticmethod
    def _extract_partial_json_value(raw: str, key: str) -> Any | None:
        """Extract a single top-level JSON-ish value for partial recovery."""
        key_match = re.search(rf'"{re.escape(key)}"\s*:\s*', raw)
        if not key_match:
            return None

        value_start = key_match.end()
        opener = raw[value_start : value_start + 1]
        if opener not in {"[", "{"}:
            scalar = re.match(r'"([^"]*)"|true|false|null|-?\d+(?:\.\d+)?', raw[value_start:])
            if not scalar:
                return None
            text = scalar.group(0)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None

        closer = "]" if opener == "[" else "}"
        depth = 0
        in_string = False
        escaped = False
        for index in range(value_start, len(raw)):
            char = raw[index]
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_string:
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    candidate = raw[value_start : index + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        return None
        return None

    @classmethod
    def _validate_concepts_with_regex(
        cls,
        paper_text: str,
        concepts: list[dict[str, Any]],
    ) -> RegexValidationResult:
        """
        Add high-value concepts that are explicitly present but absent from LLM output.

        The backfilled concepts are marked with auto_detected=true so downstream
        UI and review workflows can distinguish deterministic catches from model
        extraction.
        """
        output = [dict(item) for item in concepts if isinstance(item, dict)]
        seen = {cls._normalize_label(str(item.get("label", ""))) for item in output}
        auto_count = 0
        body_text = cls._text_before_references(paper_text or "")
        is_official_statistics = cls._looks_like_official_statistics(body_text)
        is_rl_emotion = cls._looks_like_rl_emotion_paper(body_text)
        is_qml = cls._looks_like_quantum_ml_paper(body_text)

        for label, pattern in cls.KNOWN_CONCEPT_PATTERNS:
            if label in cls.OFFICIAL_STATISTICS_LABELS and not is_official_statistics:
                continue
            if label in cls.RL_EMOTION_LABELS and not is_rl_emotion:
                continue
            if label in cls.QML_LABELS and not is_qml:
                continue
            if cls._normalize_label(label) in seen:
                continue
            if re.search(pattern, body_text, flags=re.IGNORECASE):
                output.append(
                    {
                        "label": label,
                        "context": "auto-detected via regex scan, verify manually",
                        "confidence": 0.70,
                        "auto_detected": True,
                    }
                )
                seen.add(cls._normalize_label(label))
                auto_count += 1

        has_formulas = False
        formula_types: set[str] = set()
        for formula_type, pattern in cls.MATH_PATTERNS:
            if re.search(pattern, body_text, flags=re.IGNORECASE):
                has_formulas = True
                formula_types.add(formula_type)

        return RegexValidationResult(
            concepts=output,
            auto_detected_count=auto_count,
            has_formulas=has_formulas,
            formula_types=sorted(formula_types),
        )

    @classmethod
    def _calibrate_concept_confidences(
        cls,
        paper_text: str,
        concepts: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Replace flat fallback confidence values with text-evidence scores."""
        body_text = cls._text_before_references(paper_text or "")
        output: list[dict[str, Any]] = []
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            item = dict(concept)
            label = cls._clean_label(str(item.get("label") or ""))
            if not label:
                continue
            current = cls._coerce_float(item.get("confidence"), 0.0)
            is_fallback = bool(item.get("auto_detected")) or item.get("candidate_source") == "deterministic_scan"
            if is_fallback or current in {0.74, 0.70, 0.68, 0.64, 0.62, 0.60}:
                item["confidence"] = cls._confidence_from_text_evidence(label, body_text)
                item["confidence_source"] = "text_evidence"
            output.append(item)
        return output

    @classmethod
    def _confidence_from_text_evidence(cls, label: str, text: str) -> float:
        escaped = re.escape(label)
        label_pattern = escaped.replace(r"\ ", r"[\s-]+")
        matches = list(re.finditer(rf"\b{label_pattern}\b", text or "", flags=re.IGNORECASE))
        count = len(matches)
        if count == 0 and " " in label:
            initials = "".join(word[0] for word in re.findall(r"[A-Za-z]+", label)).upper()
            if 2 <= len(initials) <= 8:
                count = len(re.findall(rf"\b{re.escape(initials)}\b", text or ""))

        score = 0.52
        if count >= 1:
            score = 0.62
        if count >= 2:
            score = 0.70
        if count >= 4:
            score = 0.78
        if count >= 8:
            score = 0.86

        header = (text or "")[:5000]
        if re.search(rf"\b{label_pattern}\b", header, flags=re.IGNORECASE):
            score += 0.04
        if re.search(rf"\b{label_pattern}\b\s*\([A-Z0-9-]{{2,8}}\)", text or "", flags=re.IGNORECASE):
            score += 0.05
        return round(min(score, 0.93), 2)

    @classmethod
    def _fallback_claims_from_text(
        cls,
        paper_text: str,
        paper_type_hint: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Extract conservative claim candidates from abstract/conclusion sentences."""
        text = cls._text_before_references(paper_text or "")
        if not text:
            return []
        windows: list[str] = []
        abstract = re.search(r"\babstract\b\s*([\s\S]{200,2500}?)(?:\n\s*(?:keywords|introduction|1\.?\s+introduction)\b)", text, flags=re.IGNORECASE)
        if abstract:
            windows.append(abstract.group(1))
        for match in re.finditer(r"\b(?:conclusion|conclusions|discussion)\b\s*([\s\S]{200,2500})", text, flags=re.IGNORECASE):
            windows.append(match.group(1))
            if len(windows) >= 3:
                break
        if not windows:
            windows.append(text[:3500])

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        claim_markers = re.compile(
            r"\b(we|this paper|this article|this survey|our|results?|findings?|show|shows|provide|provides|propose|presents?|demonstrate|suggest|lack|lacking|challenge|challenges|framework|taxonomy)\b",
            flags=re.IGNORECASE,
        )
        for window in windows:
            for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", window.strip())):
                clean = sentence.strip(" .")
                if not (70 <= len(clean) <= 320):
                    continue
                if not claim_markers.search(clean):
                    continue
                key = cls._normalize_label(clean[:120])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "statement": clean,
                        "evidence_type": "review" if paper_type_hint == "survey" else "theoretical",
                        "negated": bool(re.search(r"\b(no|not|lack|lacking|limited|without)\b", clean, flags=re.IGNORECASE)),
                        "attributed_to": "this_paper",
                        "auto_detected": True,
                        "candidate_source": "text_claim_fallback",
                    }
                )
                if len(candidates) >= limit:
                    return candidates
        return candidates

    @classmethod
    def _fallback_cross_domain_hints(cls, concepts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        labels = {cls._normalize_label(str(concept.get("label") or "")) for concept in concepts if isinstance(concept, dict)}
        hints: list[dict[str, Any]] = []
        has_rl = cls._normalize_label("Reinforcement Learning") in labels
        has_emotion = any(
            cls._normalize_label(term) in labels
            for term in ("OCC Model", "Somatic Marker Hypothesis", "Affective Computing", "Valence", "Arousal", "Appraisal theory")
        )
        if has_rl and has_emotion:
            hints.extend(
                [
                    {
                        "field": "human-robot interaction",
                        "why_applicable": "Emotion-conditioned reinforcement signals can support socially legible robot adaptation.",
                        "auto_detected": True,
                    },
                    {
                        "field": "affective computing",
                        "why_applicable": "Appraisal and valence models provide reusable state features for adaptive affective systems.",
                        "auto_detected": True,
                    },
                ]
            )
        if cls._normalize_label("Machine Learning") in labels and cls._normalize_label("Official Statistics") in labels:
            hints.append(
                {
                    "field": "data governance",
                    "why_applicable": "Monitoring and drift-detection methods transfer to institutional data quality workflows.",
                    "auto_detected": True,
                }
            )
        return hints

    @classmethod
    def _fallback_terminology_conflicts(cls, concepts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        labels = {cls._normalize_label(str(concept.get("label") or "")) for concept in concepts if isinstance(concept, dict)}
        conflicts: list[dict[str, Any]] = []
        templates = {
            "Reward function": ("reward", "reinforcement signal or objective term", "psychology/economics - subjective or extrinsic incentive"),
            "Value function": ("value", "expected return estimate", "ethics/statistics - normative worth or measured quantity"),
            "Drive": ("drive", "internal motivational variable in an RL/control loop", "psychology/physiology - homeostatic need state or drive reduction construct"),
            "Valence": ("valence", "affective polarity", "chemistry/linguistics - bonding capacity or argument structure"),
            "Policy": ("policy", "action-selection rule", "governance - institutional rule or regulation"),
            "Bias": ("bias", "statistical or model distortion", "social science - systematic unfairness or prejudice"),
        }
        for label, (term, this_field, other_field) in templates.items():
            if cls._normalize_label(label) in labels:
                conflicts.append(
                    {
                        "term": term,
                        "this_field": this_field,
                        "other_field": other_field,
                        "auto_detected": True,
                    }
                )
        return conflicts[:5]

    @classmethod
    def _merge_terminology_conflicts(
        cls,
        primary: list[dict[str, Any]],
        fallback: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Preserve LLM conflicts while backfilling stable overloaded terms."""
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        for source in (primary, fallback):
            for item in source or []:
                if not isinstance(item, dict):
                    continue
                term = str(item.get("term") or "").strip().lower()
                if not term or term in seen:
                    continue
                seen.add(term)
                output.append(dict(item))
        return output[:8]

    @classmethod
    def _filter_terminology_conflicts(
        cls,
        conflicts: list[dict[str, Any]],
        entities: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Drop generic conflict filler unless the term is anchored by extracted entities."""
        entity_keys: set[str] = set()
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            labels = [
                entity.get("label"),
                entity.get("canonical_label"),
                *list(entity.get("aliases") or []),
            ]
            for label in labels:
                key = cls._normalize_label(str(label or ""))
                if key:
                    entity_keys.add(key)

        generic_allowed_anchors = {
            "bias": {"bias", "selectionbias", "operationalizationbias", "databias"},
            "model": {"modelbasedrl", "modeluncertainty", "transitionmodel"},
            "policy": {"policysearch", "policygradient"},
            "value": {"valuefunction", "statevalue", "stateactionvalue"},
        }
        generic_terms = set(generic_allowed_anchors)
        filtered: list[dict[str, Any]] = []
        for conflict in conflicts:
            if not isinstance(conflict, dict):
                continue
            term = re.sub(r"\s+", " ", str(conflict.get("term") or "")).strip()
            key = cls._normalize_label(term)
            if not key:
                continue
            if key in generic_terms:
                allowed = generic_allowed_anchors.get(key, {key})
                if key not in entity_keys and not (entity_keys & allowed):
                    continue
                this_field = str(conflict.get("this_field") or "").lower()
                if re.search(r"\bnot explicitly\b|\bnot defined\b", this_field):
                    continue
            item = dict(conflict)
            item["term"] = term
            filtered.append(item)
        return filtered[:8]

    @classmethod
    def _has_overloaded_terms(cls, concepts: list[dict[str, Any]]) -> bool:
        overloaded = {"rewardfunction", "valuefunction", "drive", "valence", "policy", "bias"}
        labels = {cls._normalize_label(str(concept.get("label") or "")) for concept in concepts if isinstance(concept, dict)}
        return bool(labels & overloaded)

    @classmethod
    def _post_process_concepts(cls, concepts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Resolve abbreviation nodes and drop noisy compound concepts."""
        concepts = cls._filter_and_repair_concepts(concepts)
        abbreviation_map = cls._abbreviation_map_from_contexts(concepts)
        if not abbreviation_map:
            return cls._drop_compound_concepts(concepts, {})

        abbreviation_keys = {cls._normalize_label(key) for key in abbreviation_map}
        resolved: list[dict[str, Any]] = []
        skipped_abbreviations: list[tuple[str, str, dict[str, Any]]] = []

        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            label = cls._clean_label(str(concept.get("label") or ""))
            if cls._is_noisy_concept_label(label):
                continue
            label = cls._repair_label_fragments(label)
            if cls._is_noisy_concept_label(label):
                continue
            normalized = cls._normalize_label(label)
            if normalized in abbreviation_keys:
                full_label = abbreviation_map.get(label) or abbreviation_map.get(label.upper())
                if not full_label:
                    for abbr, candidate_full in abbreviation_map.items():
                        if cls._normalize_label(abbr) == normalized:
                            full_label = candidate_full
                            break
                if full_label:
                    skipped_abbreviations.append((label, full_label, concept))
                    continue

            candidate = dict(concept)
            candidate["label"] = label
            resolved.append(candidate)

        for abbreviation, full_label, source in skipped_abbreviations:
            target = cls._find_concept_by_label(resolved, full_label)
            if target is None:
                target = dict(source)
                target["label"] = full_label
                resolved.append(target)
            cls._append_alias(target, abbreviation)

        return cls._drop_compound_concepts(resolved, abbreviation_map)

    @classmethod
    def _filter_and_repair_concepts(cls, concepts: list[dict[str, Any]]) -> list[dict[str, Any]]:
        repaired: list[dict[str, Any]] = []
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            label = cls._clean_label(str(concept.get("label") or ""))
            if cls._is_noisy_concept_label(label):
                continue
            label = cls._repair_label_fragments(label)
            if cls._is_noisy_concept_label(label):
                continue
            item = dict(concept)
            item["label"] = label
            repaired.append(item)
        return repaired

    @classmethod
    def _abbreviation_map_from_contexts(cls, concepts: list[dict[str, Any]]) -> dict[str, str]:
        abbreviation_map: dict[str, str] = {}
        pattern = re.compile(r"\b([A-Z][A-Za-z]+(?:[\s-]+[A-Z]?[A-Za-z]+){1,8})\s*\(([A-Z]{2,6})\)")
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            context = str(concept.get("context") or concept.get("description") or "")
            for match in pattern.finditer(context):
                full_name = cls._trim_acronym_long_form(match.group(1).strip(), match.group(2).strip())
                abbr = match.group(2).strip()
                if cls._is_good_acronym_pair(full_name, abbr):
                    abbreviation_map[abbr] = full_name
        return abbreviation_map

    @classmethod
    def _drop_compound_concepts(
        cls,
        concepts: list[dict[str, Any]],
        abbreviation_map: dict[str, str],
    ) -> list[dict[str, Any]]:
        standalone_labels: set[str] = set()
        for concept in concepts:
            label = cls._clean_label(str(concept.get("label") or ""))
            if " and " in label.lower():
                continue
            standalone_labels.add(cls._normalize_label(label))
            for alias in cls._coerce_list(concept.get("aliases")):
                standalone_labels.add(cls._normalize_label(str(alias)))

        output: list[dict[str, Any]] = []
        for concept in concepts:
            label = cls._clean_label(str(concept.get("label") or ""))
            if " and " not in label.lower():
                output.append(concept)
                continue
            parts = [part.strip(" .,:;()[]{}") for part in re.split(r"\s+and\s+", label, flags=re.IGNORECASE)]
            if len(parts) < 2:
                output.append(concept)
                continue
            resolved_parts = [abbreviation_map.get(part) or abbreviation_map.get(part.upper()) or part for part in parts]
            if all(cls._normalize_label(part) in standalone_labels for part in resolved_parts):
                continue
            output.append(concept)
        return output

    @staticmethod
    def _repair_label_fragments(label: str) -> str:
        label = re.sub(r"\bModi\s+Cation\b", "Modification", label, flags=re.IGNORECASE)
        repaired = re.sub(
            r"\b([A-Za-z]{3,})\s+(Cation|Fication|Tion|Zation|Sation)\b",
            lambda match: match.group(1) + match.group(2).lower(),
            label,
            flags=re.IGNORECASE,
        )
        return re.sub(r"\s+", " ", repaired).strip()

    @staticmethod
    def _is_noisy_concept_label(label: str) -> bool:
        normalized = str(label or "").strip()
        lowered = normalized.lower()
        if not normalized:
            return True
        if "---" in normalized or "page break" in lowered or "break---" in lowered:
            return True
        if lowered.startswith("break ") or lowered.startswith("break-"):
            return True
        if re.search(r"\b(?:page|break)\b", lowered) and len(normalized.split()) <= 5:
            return True
        if re.search(r"\bmodi$", lowered):
            return True
        if re.search(r"\b[a-z]{1,2}$", lowered) and len(normalized.split()) > 1:
            return True
        return False

    @classmethod
    def _is_candidate_noise_artifact(
        cls,
        item: dict[str, Any],
        label: str,
        title: str | None = None,
    ) -> bool:
        """Reject parser/chunking artifacts before they enter review queues."""
        clean_label = cls._clean_label(label)
        normalized = cls._normalize_label(clean_label)
        if not normalized:
            return True

        evidence_text = cls._candidate_evidence_text(item)
        if re.search(r"(?:^|\|\s*)repeated phrase in parsed paper text", evidence_text.lower()):
            return True

        if cls._looks_like_heading_artifact(item, clean_label):
            return True

        if bool(item.get("auto_detected")) and normalized in {"datasource", "datasources", "changingdata"}:
            return True

        normalized_title = cls._normalize_label(title or "")
        if (
            bool(item.get("auto_detected"))
            and normalized_title
            and normalized in normalized_title
            and normalized != normalized_title
            and cls._starts_with_fragment_preposition(clean_label)
        ):
            return True
        return False

    @staticmethod
    def _candidate_evidence_text(item: dict[str, Any]) -> str:
        text = " ".join(
            str(item.get(key) or "")
            for key in ("evidence_span", "context", "description", "section")
        )
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _looks_like_heading_artifact(cls, item: dict[str, Any], label: str) -> bool:
        evidence_text = cls._candidate_evidence_text(item)
        is_heading = bool(re.search(r"(?:^|\|\s*)section or heading:", evidence_text.lower()))
        if not is_heading and str(item.get("evidence_role") or "").lower() != "environment":
            return False

        if cls._starts_with_fragment_preposition(label):
            return True
        if cls._looks_like_affiliation_label(label):
            return True
        if cls._normalize_label(label) in {"datasource", "datasources", "changingdata"}:
            return True
        return False

    @staticmethod
    def _starts_with_fragment_preposition(label: str) -> bool:
        return bool(re.match(r"^(?:and|by|for|from|in|of|on|or|to|with)\b", str(label or "").strip(), flags=re.IGNORECASE))

    @staticmethod
    def _looks_like_affiliation_label(label: str) -> bool:
        clean = re.sub(r"\s+", " ", str(label or "")).strip()
        lowered = clean.lower()
        if not clean:
            return False
        if re.search(
            r"\b(?:affiliation|author|centre|center|college|department|faculty|institute|laborator(?:y|ies)|school|university)\b",
            lowered,
        ):
            return True
        if re.match(r"^statistics\s+[A-Z][A-Za-z-]+(?:\s+[A-Z][A-Za-z-]+)?$", clean, flags=re.IGNORECASE):
            return True
        return False

    @classmethod
    def _is_reference_only_entity(
        cls,
        item: dict[str, Any],
        label: str,
        body_text: str,
    ) -> bool:
        """Drop entities whose only support is a bibliography/citation title."""
        if not cls._looks_like_reference_section(item):
            return False
        body_mentions = cls._mention_count(label, body_text)
        return body_mentions < 2

    @classmethod
    def _looks_like_reference_section(cls, item: dict[str, Any]) -> bool:
        section = re.sub(r"\s+", " ", str(item.get("section") or "")).strip().lower()
        if cls._is_reference_heading(section):
            return True

        evidence_text = cls._candidate_evidence_text(item).lower()
        if re.search(r"(?:^|\|\s*)section or heading:\s*(?:\d+\.?\s*)?(?:references|bibliography|works cited|literature cited)\b", evidence_text):
            return True
        return False

    @staticmethod
    def _is_reference_heading(value: str) -> bool:
        text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
        return bool(
            re.fullmatch(
                r"(?:#+\s*)?(?:\d+\.?\s*)?(?:references|bibliography|works cited|literature cited)",
                text,
            )
        )

    @classmethod
    def _is_zero_mention_deterministic_method_candidate(
        cls,
        item: dict[str, Any],
        default_role: str,
    ) -> bool:
        """Drop phrase-engineered method candidates whose label never appears."""
        if default_role != "method_candidate":
            return False
        if str(item.get("candidate_source") or "").lower() != "deterministic_scan":
            return False
        return cls._coerce_float(item.get("mention_count"), 0.0) <= 0.0

    @staticmethod
    def _looks_like_truncated_label(label: str) -> bool:
        """Detect common PDF page-break fragments in deterministic labels."""
        normalized = re.sub(r"\s+", " ", str(label or "")).strip()
        if not normalized:
            return True
        if re.search(r"\b[A-Z][a-z]{2,}\s+[A-Z][a-z]{2,4}\b", normalized):
            fragments = {"Cation", "Fication", "Tion", "Zation", "Sation", "Modi"}
            if any(token in fragments for token in normalized.split()):
                return True
        if re.search(r"\b(?:modi|fication|cation|tion|zation|sation)$", normalized, flags=re.IGNORECASE):
            words = normalized.split()
            return len(words) > 1 and words[-1].lower() in {"modi", "cation", "tion", "zation", "sation"}
        return False

    @classmethod
    def _find_concept_by_label(
        cls,
        concepts: list[dict[str, Any]],
        label: str,
    ) -> dict[str, Any] | None:
        normalized = cls._normalize_label(label)
        for concept in concepts:
            if cls._normalize_label(str(concept.get("label") or "")) == normalized:
                return concept
        return None

    @staticmethod
    def _append_alias(concept: dict[str, Any], alias: str) -> None:
        aliases = concept.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
            concept["aliases"] = aliases
        clean_alias = EntityExtractor._clean_label(alias)
        seen = {EntityExtractor._normalize_label(str(item)) for item in aliases}
        if clean_alias and EntityExtractor._normalize_label(clean_alias) not in seen:
            aliases.append(clean_alias)

    @staticmethod
    def _normalize_label(label: str) -> str:
        """Normalize concept labels for duplicate checks."""
        return normalize_key(label)

    @staticmethod
    def _normalize_extraction_mode(value: Any) -> str:
        mode = str(value or "quality").strip().lower()
        return mode if mode in {"quality", "quick"} else "quality"

    @classmethod
    def _accept_concepts(
        cls,
        paper_text: str,
        concepts: list[dict[str, Any]],
        paper_type_hint: str | None = None,
    ) -> list[dict[str, Any]]:
        """Keep only high-precision concepts for automatic KG insertion."""
        body_text = cls._text_before_references(paper_text or "")
        title = cls._paper_title_from_text(body_text)
        accepted: list[dict[str, Any]] = []
        blocked = {cls._normalize_label(label) for label in cls.GENERIC_ACCEPTED_CONCEPT_BLOCKLIST}
        for concept in concepts:
            if not isinstance(concept, dict):
                continue
            item = cls._annotate_entity_for_acceptance(concept, body_text, default_role="domain_concept")
            label = str(item.get("label") or "")
            normalized = cls._normalize_label(label)
            if not normalized or normalized in blocked:
                continue
            if cls._is_reference_only_entity(item, label, body_text):
                continue
            if item.get("candidate_source") == "deterministic_scan" or item.get("auto_detected"):
                continue
            if (
                title
                and normalized in cls._normalize_label(title)
                and normalized != cls._normalize_label(title)
                and len(label.split()) >= 3
            ):
                continue
            confidence = cls._coerce_float(item.get("confidence"), 0.75)
            salience = str(item.get("salience") or "background").lower()
            evidence_role = str(item.get("evidence_role") or "").lower()
            if confidence < 0.70:
                continue
            if salience not in {"central", "supporting"}:
                continue
            if evidence_role in {"generic_field", "environment", "background"}:
                continue
            item["accepted"] = True
            item["acceptance_reason"] = item.get("acceptance_reason") or "llm_supported_high_precision"
            accepted.append(item)
        return accepted

    @classmethod
    def _accept_methods(
        cls,
        paper_text: str,
        methods: list[dict[str, Any]],
        paper_type_hint: str | None = None,
    ) -> list[dict[str, Any]]:
        """Keep only high-precision methods for automatic KG insertion."""
        body_text = cls._text_before_references(paper_text or "")
        accepted: list[dict[str, Any]] = []
        for method in methods:
            if not isinstance(method, dict):
                continue
            item = cls._annotate_entity_for_acceptance(method, body_text, default_role="method")
            label = str(item.get("label") or "")
            if not cls._normalize_label(label):
                continue
            if cls._is_reference_only_entity(item, label, body_text):
                continue
            if item.get("candidate_source") == "deterministic_scan" or item.get("auto_detected"):
                continue
            confidence = cls._coerce_float(item.get("confidence"), 0.75)
            salience = str(item.get("salience") or "background").lower()
            if confidence < 0.65 or salience not in {"central", "supporting"}:
                continue
            if paper_type_hint == "survey":
                item["source_type"] = cls._survey_safe_method_source_type(item)
            item["accepted"] = True
            item["acceptance_reason"] = item.get("acceptance_reason") or "llm_supported_high_precision"
            accepted.append(item)
        return accepted

    @classmethod
    def _candidate_only(
        cls,
        paper_text: str,
        candidates: list[dict[str, Any]],
        accepted_entities: list[dict[str, Any]],
        default_role: str,
    ) -> list[dict[str, Any]]:
        accepted = {
            cls._normalize_label(str(entity.get("label") or ""))
            for entity in accepted_entities
            if isinstance(entity, dict)
        }
        output: list[dict[str, Any]] = []
        seen: set[str] = set()
        body_text = cls._text_before_references(paper_text or "")
        for candidate in candidates:
            if not isinstance(candidate, dict):
                continue
            item = cls._annotate_entity_for_acceptance(candidate, body_text, default_role=default_role)
            label = cls._clean_label(str(item.get("label") or ""))
            normalized = cls._normalize_label(label)
            if not normalized or normalized in accepted or normalized in seen:
                continue
            if cls._is_zero_mention_deterministic_method_candidate(item, default_role):
                continue
            if cls._is_reference_only_entity(item, label, body_text):
                continue
            if cls._is_candidate_noise_artifact(item, label, title=cls._paper_title_from_text(body_text)):
                continue
            seen.add(normalized)
            item["label"] = label
            item["accepted"] = False
            item.setdefault("candidate_reason", item.get("candidate_source") or "needs_review")
            output.append(item)
        return output

    @classmethod
    def _rejected_as_candidates(
        cls,
        proposed: list[dict[str, Any]],
        accepted_entities: list[dict[str, Any]],
        reason: str,
    ) -> list[dict[str, Any]]:
        accepted = {
            cls._normalize_label(str(entity.get("label") or ""))
            for entity in accepted_entities
            if isinstance(entity, dict)
        }
        candidates: list[dict[str, Any]] = []
        for item in proposed:
            if not isinstance(item, dict):
                continue
            normalized = cls._normalize_label(str(item.get("label") or ""))
            if not normalized or normalized in accepted:
                continue
            candidate = dict(item)
            candidate["accepted"] = False
            candidate["candidate_reason"] = reason
            candidates.append(candidate)
        return candidates

    @classmethod
    def _annotate_entity_for_acceptance(
        cls,
        entity: dict[str, Any],
        text: str,
        default_role: str,
    ) -> dict[str, Any]:
        item = dict(entity)
        label = cls._clean_label(str(item.get("label") or ""))
        item["label"] = label
        count = cls._mention_count(label, text)
        item["mention_count"] = count
        confidence = cls._coerce_float(item.get("confidence"), 0.75)
        item["confidence"] = confidence
        salience = str(item.get("salience") or "").strip().lower()
        if salience not in {"central", "supporting", "background", "passing"}:
            salience = cls._derive_salience(confidence, count)
        item["salience"] = salience
        item.setdefault("evidence_role", default_role)
        item.setdefault("entity_type", cls._entity_type_from_role(item.get("evidence_role"), label))
        item.setdefault("evidence_span", cls._evidence_span_for_entity(item))
        item.setdefault("section", cls._section_from_entity_context(item))
        item.setdefault("canonical_id", stable_canonical_id(label, prefix="method" if default_role == "method" else "concept"))
        item.setdefault("review_status", "pending")
        return item

    @staticmethod
    def _entity_type_from_role(role: Any, label: str) -> str:
        role_text = str(role or "").lower()
        label_text = str(label or "").lower()
        if role_text in {"metric", "benchmark", "dataset"}:
            return role_text.title()
        if role_text in {"theory", "method_family", "domain_concept"}:
            return {
                "theory": "Theory",
                "method_family": "MethodFamily",
                "domain_concept": "DomainConcept",
            }[role_text]
        if "dataset" in label_text:
            return "Dataset"
        if "benchmark" in label_text:
            return "Benchmark"
        if any(term in label_text for term in ("theory", "hypothesis", "model")):
            return "Theory"
        return "Algorithm" if role_text == "method" else "DomainConcept"

    @staticmethod
    def _evidence_span_for_entity(entity: dict[str, Any]) -> str:
        text = str(entity.get("evidence_span") or entity.get("context") or entity.get("description") or "").strip()
        return re.sub(r"\s+", " ", text)[:360]

    @staticmethod
    def _section_from_entity_context(entity: dict[str, Any]) -> str:
        context = str(entity.get("context") or entity.get("description") or "")
        match = re.search(r"(?:section|heading):\s*([^|.;]{2,80})", context, flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", match.group(1)).strip()[:80] if match else ""

    @staticmethod
    def _mention_count(label: str, text: str) -> int:
        if not label:
            return 0
        label_pattern = re.escape(label).replace(r"\ ", r"[\s-]+")
        count = len(re.findall(rf"\b{label_pattern}\b", text or "", flags=re.IGNORECASE))
        if count == 0 and " " in label:
            initials = "".join(word[0] for word in re.findall(r"[A-Za-z]+", label)).upper()
            if 2 <= len(initials) <= 8:
                count = len(re.findall(rf"\b{re.escape(initials)}\b", text or ""))
        return count

    @staticmethod
    def _derive_salience(confidence: float, mention_count: int) -> str:
        if confidence >= 0.88:
            return "central"
        if confidence >= 0.70:
            return "supporting"
        if confidence >= 0.55:
            return "background"
        return "passing"

    @classmethod
    def _survey_safe_method_source_type(cls, method: dict[str, Any]) -> str:
        label = str(method.get("label") or "")
        normalized = cls._normalize_label(label)
        background = {cls._normalize_label(item) for item in cls.SURVEY_BACKGROUND_METHODS}
        if normalized in background:
            return "reviewed_method"
        source_type = str(method.get("source_type") or "reviewed_method")
        if source_type == "paper_contribution" and not re.search(r"\b(taxonom|framework|survey)\b", label, flags=re.IGNORECASE):
            return "reviewed_method"
        return source_type

    @staticmethod
    def _call_overrides(
        base: dict[str, Any],
        max_tokens: int,
        temperature: float,
        top_p: float,
        json_object: bool = True,
    ) -> dict[str, Any]:
        """Merge per-call Ollama/OpenAI-compatible generation settings."""
        overrides = dict(base)
        overrides["max_tokens"] = max_tokens
        overrides["temperature"] = temperature
        overrides["top_p"] = top_p
        extra = dict(overrides.get("extra") or {})
        extra["json_mode"] = True
        extra["format"] = "json"
        if json_object:
            extra.setdefault("response_format", {"type": "json_object"})
        else:
            extra.pop("response_format", None)
        chat_template_kwargs = dict(extra.get("chat_template_kwargs") or {})
        chat_template_kwargs.setdefault("enable_thinking", False)
        extra["chat_template_kwargs"] = chat_template_kwargs
        overrides["extra"] = extra
        return overrides

    def _effective_context_size(self, provider: str | None, overrides: dict[str, Any]) -> int:
        """Cap UI overrides to the selected provider's configured context when available."""
        requested = overrides.get("context_size")
        try:
            context_size = int(requested) if requested is not None else 32768
        except (TypeError, ValueError):
            context_size = 32768

        try:
            provider_settings = self.llm.provider_settings(provider)  # type: ignore[attr-defined]
        except Exception:
            return context_size

        configured = getattr(provider_settings, "context_size", None)
        try:
            configured_context = int(configured) if configured is not None else context_size
        except (TypeError, ValueError):
            configured_context = context_size
        return max(1024, min(context_size, configured_context))

    @staticmethod
    def _coerce_list(value: Any) -> list[Any]:
        """Return value as a list, discarding malformed non-list values."""
        return value if isinstance(value, list) else []

    @staticmethod
    def _coerce_dict(value: Any) -> dict[str, Any]:
        """Return value as a dictionary, discarding malformed non-dict values."""
        return value if isinstance(value, dict) else {}

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _normalize_paper_type(value: Any) -> str:
        """Normalize paper type to the supported controlled vocabulary."""
        paper_type = str(value or "research").strip().lower()
        return paper_type if paper_type in {"research", "survey", "theoretical", "benchmark"} else "research"

    @staticmethod
    def _detect_paper_type(text: str) -> str | None:
        sample = (text or "")[:12000].lower()
        title = EntityExtractor._paper_title_from_text(text).lower()
        benchmark_markers = (
            "benchmark",
            "benchmark task",
            "benchmark suite",
            "discovery engine",
            "open-source framework",
        )
        framework_intro = bool(
            re.search(r"\b(we|this paper|we introduce|we present|we propose)\b", sample)
            and re.search(r"\b(framework|engine|toolkit|library|platform)\b", sample)
            and re.search(r"\b(benchmark|dataset|task|evaluate|evaluation|reproduce|reproduces|mnist|sst-?2)\b", sample)
        )
        if any(marker in title for marker in benchmark_markers) or framework_intro:
            return "benchmark"
        survey_markers = (
            r"\ba survey\b",
            r"\bthis survey\b",
            r"\bsurvey of\b",
            r"\breview paper\b",
            r"\bsystematic review\b",
            r"\bliterature review\b",
            r"\bwe survey\b",
            r"\bwe review the literature\b",
        )
        if any(re.search(marker, title) for marker in survey_markers):
            return "survey"
        if any(re.search(marker, sample) for marker in survey_markers):
            return "survey"
        return None

    @classmethod
    def _resolve_paper_type(cls, semantic_type: Any, detected_type: str | None, text: str) -> str:
        """Combine model paper-type output with deterministic safeguards."""
        paper_type = cls._normalize_paper_type(semantic_type)
        detected = cls._normalize_paper_type(detected_type) if detected_type else None
        if detected == "benchmark" and paper_type in {"research", "survey", "benchmark"}:
            return "benchmark"
        if detected == "survey" and paper_type == "research":
            return "survey"
        return paper_type

    @staticmethod
    def _paper_title_from_text(text: str) -> str:
        """Best-effort title extraction from the parsed paper header."""
        for raw_line in (text or "").splitlines()[:30]:
            line = re.sub(r"\s+", " ", raw_line).strip(" #\t")
            if not line:
                continue
            lowered = line.lower()
            if EntityExtractor._is_header_noise_title_line(line):
                continue
            if lowered in {"abstract", "introduction"}:
                continue
            if lowered in {"article", "research article", "original article", "original research", "open access"}:
                continue
            if re.match(r"^(?:arxiv|doi|http|www\.|journal|conference)\b", lowered):
                continue
            if re.match(r"^(?:downloaded from|published by|available online)\b", lowered):
                continue
            if len(line) < 6 or len(line) > 180:
                continue
            return line.title() if line.isupper() else line
        return ""

    @staticmethod
    def _is_header_noise_title_line(line: str) -> bool:
        """Reject common PDF header/footer lines that precede the real title."""
        cleaned = re.sub(r"\s+", " ", line or "").strip()
        lowered = cleaned.lower()
        if not lowered:
            return True
        exact_noise = {
            "preprint",
            "draft",
            "accepted manuscript",
            "noname manuscript no.",
            "noname manuscript no",
            "science china",
            "conference paper",
            "technical report",
            "springer nature latex template",
        }
        if lowered in exact_noise:
            return True
        noisy_patterns = [
            r"^draft version\b",
            r"^accepted for publication\b",
            r"^accepted manuscript\b",
            r"^arxiv preprint\b",
            r"^preprint submitted\b",
            r"^submitted to\b",
            r"^published in\b",
            r"^proceedings of\b",
            r"^copyright\b",
            r"^©",
            r"^cern[-\s]open[-\s]\d",
            r"^european organization for nuclear research",
            r"^journal of\b",
            r"^springer nature\b.*latex template",
            r"^information research\s*-\s*vol\.",
            r"^\d+(?:st|nd|rd|th)\s+conte?csi\b",
            r"^\d+(?:st|nd|rd|th)\s+.*international conference on\b",
            r"^draft version\s*(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
            r"^\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}$",
        ]
        return any(re.search(pattern, lowered) for pattern in noisy_patterns)

    @classmethod
    def _title_tokens(cls, title: str) -> set[str]:
        normalized = normalize_scientific_text(title).lower()
        tokens = re.findall(r"[a-z0-9]+", normalized)
        return {
            token
            for token in tokens
            if len(token) >= 3 and token not in cls.TITLE_STOPWORDS and not token.isdigit()
        }

    @classmethod
    def _titles_conflict(cls, first: str, second: str) -> bool:
        """Return true only for strong title conflicts, not small formatting drift."""
        first_clean = re.sub(r"\s+", " ", normalize_scientific_text(first)).strip().lower()
        second_clean = re.sub(r"\s+", " ", normalize_scientific_text(second)).strip().lower()
        if not first_clean or not second_clean:
            return False
        if cls._normalize_label(first_clean) == cls._normalize_label(second_clean):
            return False

        first_tokens = cls._title_tokens(first_clean)
        second_tokens = cls._title_tokens(second_clean)
        if len(first_tokens) < 3 or len(second_tokens) < 3:
            return False

        shared = first_tokens & second_tokens
        overlap = len(shared) / max(1, min(len(first_tokens), len(second_tokens)))
        jaccard = len(shared) / max(1, len(first_tokens | second_tokens))
        sequence_similarity = SequenceMatcher(None, first_clean, second_clean).ratio()
        return overlap < 0.35 and jaccard < 0.25 and sequence_similarity < 0.55

    @staticmethod
    def _combined_parse_quality(call_1_quality: str, call_2_quality: str) -> str:
        """Combine per-call parse quality into one quality label."""
        order = {"clean": 0, "trimmed": 1, "partial": 2, "failed": 3}
        worst = max((call_1_quality, call_2_quality), key=lambda item: order.get(item, 2))
        return worst if worst in order else "partial"

    @staticmethod
    def _diagnostic_excerpt(raw_text: str, max_chars: int = 360) -> str:
        """Return a compact diagnostic excerpt without flooding stored payloads."""
        excerpt = re.sub(r"\s+", " ", str(raw_text or "")).strip()
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars] + "..."
        return excerpt

    @classmethod
    def _fatal_extraction_failure_reason(
        cls,
        parse_quality: str,
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
        call_diagnostics: list[dict[str, Any]],
    ) -> str | None:
        """Detect model-level failures that should not be written as successes."""
        if parse_quality != "failed" or concepts or methods:
            return None
        calls = [
            call
            for call in call_diagnostics
            if isinstance(call, dict) and str(call.get("call_type") or "") != "claims_retry"
        ]
        if not calls:
            return "LLM extraction failed before usable JSON could be produced."
        if any(str(call.get("parse_quality") or "") not in {"failed", "skipped"} for call in calls):
            return None
        failed_calls = [call for call in calls if str(call.get("parse_quality") or "") == "failed"]
        if not failed_calls:
            return None
        excerpts = " ".join(str(call.get("raw_excerpt") or "") for call in failed_calls)
        if "No models loaded" in excerpts:
            return "LLM extraction failed: LM Studio has no model loaded."
        if "LLM call failed" in excerpts or "retry failed" in excerpts:
            return "LLM extraction failed for every extraction call; no KG-safe entities were produced."
        return "LLM extraction failed before usable JSON could be produced."

    @staticmethod
    def _call_diagnostics(
        structural_calls: list[ParsedLLMResponse],
        semantic: ParsedLLMResponse,
        claims_pass: ParsedLLMResponse | None,
        concepts_retry: ParsedLLMResponse | None = None,
        methods_retry: ParsedLLMResponse | None = None,
        semantic_retry: ParsedLLMResponse | None = None,
    ) -> list[dict[str, Any]]:
        """Return per-call parse diagnostics for review and benchmark gates."""
        diagnostics: list[dict[str, Any]] = []
        structural_keys = {"concepts", "methods", "concept_candidates", "method_candidates"}
        for index, call in enumerate(structural_calls, start=1):
            data_keys = set(call.data.keys())
            row = {
                "call_type": "structural",
                "chunk_index": index,
                "parse_quality": call.parse_quality,
                "missing_keys": sorted(structural_keys - data_keys),
                "tokens_used": call.tokens_used,
                "recovery_strategy": "split_retry"
                if "--- SPLIT STRUCTURAL RETRY ---" in call.raw_text
                else None,
            }
            if call.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = EntityExtractor._diagnostic_excerpt(call.raw_text)
            diagnostics.append(row)
        if concepts_retry is not None:
            row = {
                "call_type": "concepts_retry",
                "chunk_index": None,
                "parse_quality": concepts_retry.parse_quality,
                "missing_keys": [] if "concepts" in concepts_retry.data else ["concepts"],
                "tokens_used": concepts_retry.tokens_used,
            }
            if concepts_retry.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = EntityExtractor._diagnostic_excerpt(concepts_retry.raw_text)
            diagnostics.append(row)
        if methods_retry is not None:
            row = {
                "call_type": "methods_retry",
                "chunk_index": None,
                "parse_quality": methods_retry.parse_quality,
                "missing_keys": [] if "methods" in methods_retry.data else ["methods"],
                "tokens_used": methods_retry.tokens_used,
            }
            if methods_retry.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = EntityExtractor._diagnostic_excerpt(methods_retry.raw_text)
            diagnostics.append(row)
        if semantic_retry is not None:
            row = {
                "call_type": "semantic_retry",
                "chunk_index": None,
                "parse_quality": semantic_retry.parse_quality,
                "missing_keys": [],
                "tokens_used": semantic_retry.tokens_used,
            }
            if semantic_retry.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = EntityExtractor._diagnostic_excerpt(semantic_retry.raw_text)
            diagnostics.append(row)
        semantic_keys = {
            "paper_type",
            "paper_node",
            "claims",
            "cross_domain_hints",
            "terminology_conflicts",
            "temporal_coverage",
            "mathematical_content",
            "language_detected",
        }
        semantic_row = {
            "call_type": "semantic",
            "chunk_index": None,
            "parse_quality": semantic.parse_quality,
            "missing_keys": sorted(semantic_keys - set(semantic.data.keys())),
            "tokens_used": semantic.tokens_used,
        }
        if semantic.parse_quality in {"partial", "failed"}:
            semantic_row["raw_excerpt"] = EntityExtractor._diagnostic_excerpt(semantic.raw_text)
        diagnostics.append(semantic_row)
        if claims_pass is not None:
            diagnostics.append(
                {
                    "call_type": "claims_retry",
                    "chunk_index": None,
                    "parse_quality": claims_pass.parse_quality,
                    "missing_keys": [] if "claims" in claims_pass.data else ["claims"],
                    "tokens_used": claims_pass.tokens_used,
                }
            )
        return diagnostics

    @classmethod
    def _build_paper_node(
        cls,
        paper_id: str,
        paper_text: str,
        paper_type: str,
        semantic_paper_node: dict[str, Any],
        temporal_coverage: dict[str, Any],
        language_detected: str,
    ) -> dict[str, Any]:
        """Materialize the extraction's Paper node anchor independent of LLM recall."""
        semantic_title = str(semantic_paper_node.get("title") or "").strip()
        text_title = cls._paper_title_from_text(paper_text)
        title = semantic_title or text_title
        title_conflict = bool(semantic_title and text_title and cls._titles_conflict(semantic_title, text_title))
        if title_conflict:
            title = text_title
        paper_year = semantic_paper_node.get("paper_year") or temporal_coverage.get("paper_year")
        reviewed_period = semantic_paper_node.get("reviewed_period") or temporal_coverage.get("reviewed_period")
        detected_source_id = cls._extract_front_matter_arxiv_identifier(paper_text)
        requested_arxiv_id = cls._extract_arxiv_identifier(str(paper_id))
        canonical_paper_id = requested_arxiv_id or detected_source_id or str(paper_id)
        authoritative_arxiv_id = ""
        if requested_arxiv_id and (not detected_source_id or detected_source_id == requested_arxiv_id):
            authoritative_arxiv_id = requested_arxiv_id
        elif detected_source_id and not requested_arxiv_id:
            authoritative_arxiv_id = detected_source_id
        arxiv_year = cls._arxiv_publication_year(authoritative_arxiv_id)
        llm_paper_year = cls._coerce_year(paper_year)
        year_conflict = bool(arxiv_year and llm_paper_year and abs(arxiv_year - llm_paper_year) > 2)
        if arxiv_year and (paper_year in (None, "") or year_conflict):
            paper_year = arxiv_year
        node = {
            "node_type": "Paper",
            "paper_id": canonical_paper_id,
            "title": title,
            "paper_type": cls._normalize_paper_type(paper_type),
            "paper_year": paper_year,
            "reviewed_period": reviewed_period,
            "language_detected": language_detected or "en",
            "source": "extraction",
        }
        if detected_source_id and requested_arxiv_id and detected_source_id != requested_arxiv_id:
            node["detected_source_id"] = detected_source_id
        if title_conflict:
            node["detected_title"] = text_title
            node["llm_paper_title"] = semantic_title
        if year_conflict:
            node["llm_paper_year"] = llm_paper_year
            node["paper_year_source"] = "arxiv_id"
        return {key: value for key, value in node.items() if value not in (None, "", [], {})}

    @staticmethod
    def _worst_parse_quality(qualities: list[str]) -> str:
        if not qualities:
            return "partial"
        order = {"clean": 0, "trimmed": 1, "partial": 2, "failed": 3}
        worst = max(qualities, key=lambda item: order.get(item, 2))
        return worst if worst in order else "partial"

    @classmethod
    def _chunked_parse_quality(cls, calls: list[ParsedLLMResponse]) -> str:
        """Aggregate chunked structural calls without letting one failed chunk erase useful chunks."""
        if not calls:
            return "partial"
        qualities = [str(call.parse_quality or "partial") for call in calls]
        if any(quality == "failed" for quality in qualities) and any(
            quality in {"clean", "trimmed"} for quality in qualities
        ):
            return "partial"
        return cls._worst_parse_quality(qualities)

    @classmethod
    def _quality_warnings(
        cls,
        paper_type: str,
        concept_count: int,
        method_count: int,
        text_length: int,
        parse_quality: str,
        paper_id: str | None = None,
        paper_node: dict[str, Any] | None = None,
    ) -> list[str]:
        warnings: list[str] = []
        if parse_quality == "partial":
            warnings.append("One or more LLM JSON responses required partial recovery.")
        elif parse_quality == "failed":
            warnings.append("One or more LLM extraction calls failed; deterministic fallbacks may be incomplete.")
        if text_length >= 20000 and concept_count < 12:
            warnings.append(
                "Full-length paper produced fewer than 12 concepts; review extraction coverage."
            )
        if paper_type == "survey" and concept_count < 30:
            warnings.append(
                "Survey paper produced fewer than 30 concepts; reviewed methods may be under-extracted."
            )
        if text_length >= 20000 and method_count == 0:
            warnings.append("Full-length paper produced no methods; review method extraction.")
        warnings.extend(cls._paper_identity_warnings(paper_id, paper_node or {}))
        return warnings

    @classmethod
    def _paper_identity_warnings(
        cls,
        paper_id: str | None,
        paper_node: dict[str, Any],
    ) -> list[str]:
        warnings: list[str] = []
        node_paper_id = str(paper_node.get("paper_id") or paper_id or "")
        arxiv_id = cls._extract_arxiv_identifier(node_paper_id)
        arxiv_year = cls._arxiv_publication_year(arxiv_id)
        paper_year = cls._coerce_year(paper_node.get("paper_year"))
        if arxiv_year and paper_year and abs(arxiv_year - paper_year) > 2:
            warnings.append(
                f"Paper id {arxiv_id} implies year {arxiv_year}, "
                f"but extracted paper_year is {paper_year}; verify paper metadata."
            )
        llm_paper_year = cls._coerce_year(paper_node.get("llm_paper_year"))
        if arxiv_year and llm_paper_year and abs(arxiv_year - llm_paper_year) > 2:
            warnings.append(
                f"LLM extracted paper_year={llm_paper_year}, but paper id {arxiv_id} "
                f"implies {arxiv_year}; using arXiv metadata year."
            )

        detected_source_id = cls._extract_arxiv_identifier(str(paper_node.get("detected_source_id") or ""))
        if arxiv_id and detected_source_id and detected_source_id != arxiv_id:
            warnings.append(
                f"Paper text contains {detected_source_id}, "
                f"but extraction paper_id is {arxiv_id}; verify source identity."
            )
        detected_title = str(paper_node.get("detected_title") or "")
        llm_title = str(paper_node.get("llm_paper_title") or "")
        if detected_title and llm_title and cls._titles_conflict(detected_title, llm_title):
            warnings.append(
                "Parsed paper title conflicts with LLM/external title; verify the selected paper text."
            )
        return warnings

    @classmethod
    def _metadata_validation(
        cls,
        paper_id: str | None,
        paper_node: dict[str, Any],
    ) -> dict[str, Any]:
        blocking_errors: list[str] = []
        node_paper_id = str(paper_node.get("paper_id") or paper_id or "")
        supplied_arxiv_id = cls._extract_arxiv_identifier(node_paper_id)
        detected_source_id = cls._extract_arxiv_identifier(str(paper_node.get("detected_source_id") or ""))
        if supplied_arxiv_id and detected_source_id and detected_source_id != supplied_arxiv_id:
            blocking_errors.append(
                f"paper_id_mismatch: supplied {supplied_arxiv_id}, extracted {detected_source_id}"
            )

        arxiv_year = cls._arxiv_publication_year(supplied_arxiv_id)
        paper_year = cls._coerce_year(paper_node.get("paper_year"))
        if arxiv_year and paper_year and abs(arxiv_year - paper_year) > 2:
            blocking_errors.append(
                f"paper_id_year_mismatch: supplied {supplied_arxiv_id} implies {arxiv_year}, "
                f"extracted paper_year={paper_year}"
            )

        detected_title = str(paper_node.get("detected_title") or "")
        llm_title = str(paper_node.get("llm_paper_title") or "")
        if detected_title and llm_title and cls._titles_conflict(detected_title, llm_title):
            blocking_errors.append(
                "paper_title_mismatch: parsed title "
                f"'{detected_title[:120]}' conflicts with LLM/external title '{llm_title[:120]}'"
            )

        return {
            "metadata_status": "invalid" if blocking_errors else "valid",
            "blocking_errors": blocking_errors,
        }

    @staticmethod
    def _coerce_year(value: Any) -> int | None:
        try:
            year = int(value)
        except (TypeError, ValueError):
            return None
        return year if 1500 <= year <= 3000 else None

    @classmethod
    def _extract_arxiv_identifier(cls, value: str) -> str:
        match = re.search(
            r"\b(?:arxiv:\s*)?(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?\b",
            value,
            re.IGNORECASE,
        )
        if match:
            return f"arxiv:{match.group(1)}{match.group(2)}.{match.group(3)}"
        return cls._extract_legacy_arxiv_identifier(value)

    @classmethod
    def _extract_legacy_arxiv_identifier(cls, value: str) -> str:
        match = re.search(
            rf"(?<![A-Za-z0-9])({cls.LEGACY_ARXIV_CATEGORY_RE})\s*[/_:.-]\s*(\d{{7}})(?:v\d+)?(?!\d)",
            value or "",
            re.IGNORECASE,
        )
        if not match:
            return ""
        return f"arxiv:{match.group(1).lower()}/{match.group(2)}"

    @classmethod
    def _extract_front_matter_arxiv_identifier(cls, paper_text: str) -> str:
        body_text = cls._text_before_references(paper_text or "")
        front_matter = body_text[:20000]
        explicit = cls._extract_explicit_arxiv_identifier(front_matter[:8000])
        if explicit:
            return explicit

        introduction_match = re.search(
            r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\d+\.?\s*)?(?:introduction|i\.\s+introduction)\b",
            front_matter,
            re.IGNORECASE,
        )
        if introduction_match:
            explicit = cls._extract_explicit_arxiv_identifier(front_matter[: introduction_match.start()])
            if explicit:
                return explicit

        explicit = cls._extract_explicit_arxiv_identifier(front_matter)
        if explicit:
            return explicit

        abstract_match = re.search(r"\babstract\b", front_matter, re.IGNORECASE)
        fallback_window = front_matter[: abstract_match.end() + 250] if abstract_match else front_matter[:2500]
        return cls._extract_arxiv_identifier(fallback_window)

    @classmethod
    def _extract_explicit_arxiv_identifier(cls, value: str) -> str:
        match = re.search(
            r"\barxiv\s*:\s*(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?\b",
            value or "",
            re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r"\barxiv\.org/(?:abs|pdf)/(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?\b",
                value or "",
                re.IGNORECASE,
            )
        if match:
            return f"arxiv:{match.group(1)}{match.group(2)}.{match.group(3)}"
        return cls._extract_legacy_arxiv_identifier(value or "")

    @classmethod
    def _arxiv_publication_year(cls, arxiv_id: str) -> int | None:
        match = re.search(r"\b(?:arxiv:\s*)?(\d{2})(\d{2})\.\d{4,5}", arxiv_id, re.IGNORECASE)
        if not match:
            match = re.search(
                rf"(?<![A-Za-z0-9])(?:arxiv:\s*)?{cls.LEGACY_ARXIV_CATEGORY_RE}\s*/\s*(\d{{2}})(\d{{2}})\d{{3}}",
                arxiv_id or "",
                re.IGNORECASE,
            )
        if not match:
            return None
        year = int(match.group(1))
        month = int(match.group(2))
        if not 1 <= month <= 12:
            return None
        return 2000 + year if year < 90 else 1900 + year

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        Estimate output tokens when provider usage metadata is unavailable.

        LLMRouter currently returns only content text, so this is a stable local
        estimate rather than Ollama's eval_count.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _last_tokens_used(self) -> int | None:
        """Read provider token usage captured by LLMRouter, when available."""
        metadata = getattr(self.llm, "last_response_metadata", {}) or {}
        eval_count = metadata.get("eval_count")
        if eval_count is not None:
            try:
                return int(eval_count)
            except (TypeError, ValueError):
                return None

        usage = metadata.get("usage") or {}
        completion_tokens = usage.get("completion_tokens")
        if completion_tokens is not None:
            try:
                return int(completion_tokens)
            except (TypeError, ValueError):
                return None
        return None

    @classmethod
    def _scan_paper_text(cls, paper_text: str) -> DeterministicScanResult:
        """
        Mine obvious entities locally before asking the LLM.

        This is deliberately high-recall. Items are marked as auto-detected so
        downstream review can distinguish deterministic catches from model
        judgments.
        """
        text = paper_text or ""
        body_text = cls._text_before_references(text)
        is_official_statistics = cls._looks_like_official_statistics(body_text)
        is_rl_emotion = cls._looks_like_rl_emotion_paper(body_text)
        is_qml = cls._looks_like_quantum_ml_paper(body_text)
        concepts: list[dict[str, Any]] = []
        methods: list[dict[str, Any]] = []
        seen_concepts: set[str] = set()
        seen_methods: set[str] = set()

        def add_concept(label: str, context: str, confidence: float = 0.72) -> None:
            normalized = cls._normalize_label(label)
            if not normalized or normalized in seen_concepts:
                return
            seen_concepts.add(normalized)
            concepts.append(
                {
                    "label": cls._clean_label(label),
                    "context": context[:360] or "auto-detected from paper text",
                    "confidence": confidence,
                    "auto_detected": True,
                    "candidate_source": "deterministic_scan",
                }
            )

        def add_method(label: str, description: str, confidence: float = 0.70) -> None:
            normalized = cls._normalize_label(label)
            if not normalized or normalized in seen_methods:
                return
            seen_methods.add(normalized)
            methods.append(
                {
                    "label": cls._clean_label(label),
                    "domain": "unknown",
                    "description": description[:360] or "auto-detected from paper text",
                    "source_type": "background",
                    "confidence": confidence,
                    "auto_detected": True,
                    "candidate_source": "deterministic_scan",
                }
            )

        for label, pattern in cls.KNOWN_CONCEPT_PATTERNS:
            if label in cls.OFFICIAL_STATISTICS_LABELS and not is_official_statistics:
                continue
            if label in cls.RL_EMOTION_LABELS and not is_rl_emotion:
                continue
            if label in cls.QML_LABELS and not is_qml:
                continue
            match = re.search(pattern, body_text, flags=re.IGNORECASE)
            if match:
                add_concept(label, cls._context_for_match(body_text, match), 0.74)

        method_patterns = (
            ("Checklist for Changing Data Sources", r"\bchecklist\b"),
            ("Data Source Monitoring", r"\bmonitor(?:ing)? changes? in (?:incoming )?data\b|\bmonitoring\b"),
            ("Robust Data Sourcing", r"\brobust(?:ness)? in (?:both )?data sourcing\b|\brobust data sourcing\b"),
            ("Robust Statistical Techniques", r"\brobust(?:ness)? in .*statistical techniques\b|\brobust statistical techniques\b"),
            ("Model Retraining", r"\bperiodically reevaluate and retrain models\b|\bretrain(?:ing)? models\b"),
            ("Data Pipeline Monitoring", r"\bdata pipelines? should (?:be designed to )?monitor\b|\bpipeline monitoring\b"),
            ("Precautionary Measures", r"\bprecautionary measures\b"),
        )
        for label, pattern in method_patterns:
            if not is_official_statistics:
                continue
            match = re.search(pattern, body_text, flags=re.IGNORECASE)
            if match:
                add_method(label, cls._context_for_match(body_text, match), 0.74)

        rl_method_patterns = (
            ("Q-learning", r"\bQ[\s-]?learning\b"),
            ("SARSA", r"\bSARSA\b"),
            ("TD(lambda)", r"\bTD\s*\(?\s*(?:lambda|\\lambda|Î»|λ)\s*\)?|\bTD\s*\(\s*(?:Î»|λ)\s*\)"),
            ("Actor-Critic", r"\bActor[-\s]?Critic\b"),
            ("Dynamic Programming", r"\bDynamic Programming\b"),
            ("Reward shaping", r"\breward shaping\b|\bshap(?:e|ed|ing)\s+rewards?\b"),
            ("Policy gradient", r"\bpolicy gradient(?:s)?\b"),
            ("Value iteration", r"\bvalue iteration\b"),
            ("Homeostatic reinforcement learning", r"\bhomeostatic reinforcement learning\b"),
            ("Appraisal-based reward modulation", r"\bappraisal\b.{0,80}\breward\b|\breward\b.{0,80}\bappraisal\b"),
        )
        for label, pattern in rl_method_patterns:
            if not is_rl_emotion:
                continue
            match = re.search(pattern, body_text, flags=re.IGNORECASE)
            if match:
                add_method(label, cls._context_for_match(body_text, match), 0.76)

        for match in re.finditer(r"\b([A-Z][A-Za-z][A-Za-z0-9 /-]{2,80}?)\s+\(([A-Z][A-Z0-9-]{1,12})\)", body_text):
            long_form, short_form = match.group(1).strip(), match.group(2).strip()
            long_form = cls._trim_acronym_long_form(long_form, short_form)
            if cls._is_good_acronym_pair(long_form, short_form):
                add_concept(long_form, cls._context_for_match(body_text, match), 0.68)
                add_concept(short_form, cls._context_for_match(body_text, match), 0.62)

        heading_candidates = cls._heading_candidates(body_text)
        for label, context in heading_candidates:
            add_concept(label, context, 0.64)

        if is_official_statistics:
            for phrase, count in cls._repeated_domain_phrases(body_text).most_common(20):
                if count >= 2:
                    add_concept(phrase, f"Repeated phrase in parsed paper text ({count} mentions).", 0.60)
        if is_rl_emotion:
            for phrase, count in cls._repeated_rl_emotion_phrases(body_text).most_common(20):
                if count >= 2:
                    add_concept(phrase, f"Repeated phrase in parsed paper text ({count} mentions).", 0.60)

        return DeterministicScanResult(
            concepts=concepts,
            methods=methods,
            paper_year=cls._detect_paper_year(text),
        )

    @classmethod
    def _heading_candidates(cls, text: str) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for raw_line in (text or "").splitlines()[:500]:
            numbered_heading = re.match(r"^\s*\d+(?:\.\d+)*\s+([A-Z][^.!?]{3,70})\s*$", raw_line)
            if numbered_heading:
                line = numbered_heading.group(1).strip()
            else:
                line = raw_line.strip()
            line = re.sub(r"\s+", " ", line)
            words = line.split()
            if not (4 <= len(line) <= 70 and 1 <= len(words) <= 8):
                continue
            if raw_line.rstrip().endswith((".", ",", ";", ":")) and not numbered_heading:
                continue
            if len(words) > 3 and sum(1 for word in words if word[:1].isupper()) < max(2, len(words) // 2):
                continue
            if not re.search(
                r"\b(data|source|statistics|machine learning|bias|validity|accuracy|availability|ownership|ethics|regulation|privacy|monitoring|robustness|concept drift|frequency|completeness|neutrality)\b",
                line,
                flags=re.IGNORECASE,
            ):
                continue
            rows.append((line.title() if line.isupper() else line, f"Section or heading: {line}"))
        return rows

    @staticmethod
    def _text_before_references(text: str) -> str:
        raw = text or ""
        match = re.search(
            r"(?im)^\s*(?:#+\s*)?(?:\d+\.?\s*)?(?:references|bibliography|works cited|literature cited)\s*$",
            raw,
        )
        if match:
            return raw[: match.start()]
        match = re.search(r"\n\s*(?:references|bibliography)\s*\n", raw, flags=re.IGNORECASE)
        if not match:
            return raw
        return raw[: match.start()]

    @classmethod
    def _looks_like_official_statistics(cls, text: str) -> bool:
        normalized = (text or "").lower()
        return (
            "official statistics" in normalized
            or ("data source" in normalized and "statistical" in normalized)
            or ("data sources" in normalized and "statistics" in normalized)
        )

    @classmethod
    def _looks_like_rl_emotion_paper(cls, text: str) -> bool:
        normalized = (text or "").lower()
        has_rl = "reinforcement learning" in normalized or re.search(r"\bRL\b", text or "") is not None
        has_emotion = (
            "emotion" in normalized
            or "affective" in normalized
            or "valence" in normalized
            or "appraisal" in normalized
        )
        return bool(has_rl and has_emotion)

    @classmethod
    def _looks_like_quantum_ml_paper(cls, text: str) -> bool:
        normalized = (text or "").lower()
        has_quantum_context = (
            "quantum" in normalized
            or "photonic" in normalized
            or "fock space" in normalized
            or "linear-optical" in normalized
            or re.search(r"\bQML\b", text or "") is not None
        )
        has_ml_context = (
            "machine learning" in normalized
            or "neural network" in normalized
            or "neural networks" in normalized
            or "classification" in normalized
            or "benchmark" in normalized
            or "dataset" in normalized
        )
        return bool(has_quantum_context and has_ml_context)

    @classmethod
    def _is_good_acronym_pair(cls, long_form: str, short_form: str) -> bool:
        if not (2 <= len(short_form) <= 8 and 2 <= len(long_form.split()) <= 8):
            return False
        lowered = long_form.lower()
        reject_terms = {
            "conference",
            "proceedings",
            "journal",
            "transactions",
            "symposium",
            "congress",
            "workshop",
            "vol",
            "pp",
        }
        if any(term in lowered for term in reject_terms):
            return False
        first_word = re.match(r"[A-Za-z]+", long_form)
        if first_word and first_word.group(0).lower() in {"for", "in", "the", "this", "these", "those", "a", "an", "we"}:
            return False
        if len(long_form) > 70:
            return False
        initials = "".join(word[0] for word in re.findall(r"[A-Za-z]+", long_form)).upper()
        return short_form.upper() == initials[: len(short_form)] or short_form.upper() in initials

    @staticmethod
    def _trim_acronym_long_form(long_form: str, short_form: str) -> str:
        words = re.findall(r"[A-Za-z][A-Za-z0-9-]*", long_form or "")
        acronym = (short_form or "").upper()
        for index in range(len(words)):
            suffix = words[index:]
            initials = "".join(word[0] for word in suffix).upper()
            if initials == acronym:
                return " ".join(suffix)
        return long_form

    @staticmethod
    def _repeated_domain_phrases(text: str) -> Counter[str]:
        normalized = re.sub(r"[^A-Za-z0-9\s-]", " ", text or "").lower()
        words = [word for word in normalized.split() if len(word) > 2]
        domain_heads = {
            "data",
            "statistical",
            "statistics",
            "machine",
            "model",
            "source",
            "quality",
            "concept",
            "public",
            "privacy",
            "regulation",
            "ownership",
        }
        stop = {"the", "and", "for", "with", "from", "that", "this", "are", "can", "will", "have", "has"}
        counts: Counter[str] = Counter()
        for size in (2, 3, 4):
            for index in range(0, max(0, len(words) - size + 1)):
                phrase_words = words[index : index + size]
                if phrase_words[0] not in domain_heads and not any(word in domain_heads for word in phrase_words):
                    continue
                if any(word in stop for word in (phrase_words[0], phrase_words[-1])):
                    continue
                phrase = " ".join(phrase_words)
                if len(phrase) >= 8:
                    counts[phrase.title()] += 1
        return counts

    @staticmethod
    def _repeated_rl_emotion_phrases(text: str) -> Counter[str]:
        normalized = re.sub(r"[^A-Za-z0-9\s-]", " ", text or "").lower()
        words = [word for word in normalized.split() if len(word) > 2]
        domain_heads = {
            "reinforcement",
            "learning",
            "emotion",
            "emotional",
            "affective",
            "appraisal",
            "reward",
            "policy",
            "value",
            "agent",
            "robot",
            "human",
            "motivation",
            "homeostatic",
            "drive",
        }
        stop = {"the", "and", "for", "with", "from", "that", "this", "are", "can", "will", "have", "has", "paper", "article"}
        counts: Counter[str] = Counter()
        for size in (2, 3, 4):
            for index in range(0, max(0, len(words) - size + 1)):
                phrase_words = words[index : index + size]
                if not any(word in domain_heads for word in phrase_words):
                    continue
                if phrase_words[0] in stop or phrase_words[-1] in stop:
                    continue
                phrase = " ".join(phrase_words)
                generic_words = {
                    "reinforcement",
                    "learning",
                    "agent",
                    "agents",
                    "robot",
                    "robots",
                    "human",
                    "humans",
                }
                if all(word in generic_words for word in phrase_words):
                    continue
                if 10 <= len(phrase) <= 70:
                    counts[phrase.title()] += 1
        return counts

    @staticmethod
    def _context_for_match(text: str, match: re.Match[str], window: int = 180) -> str:
        start_floor = max(0, match.start() - window)
        end_ceiling = min(len(text), match.end() + window)
        prefix = text[start_floor: match.start()]
        suffix = text[match.end(): end_ceiling]

        start = start_floor
        sentence_start = max(prefix.rfind(". "), prefix.rfind("! "), prefix.rfind("? "), prefix.rfind("\n"))
        if sentence_start >= 0:
            start = start_floor + sentence_start + 1

        end = end_ceiling
        suffix_boundary = re.search(r"(?:[.!?]\s+|\n)", suffix)
        if suffix_boundary:
            end = match.end() + suffix_boundary.end()

        context = re.sub(r"\s+", " ", text[start:end]).strip()
        if context and context[0].islower() and match.start() > start_floor:
            fallback = re.sub(r"\s+", " ", text[match.start():end]).strip()
            if fallback:
                context = fallback
        return context

    @staticmethod
    def _clean_label(label: str) -> str:
        cleaned = re.sub(r"\s+", " ", normalize_scientific_text(label)).strip(" .,:;[]{}")
        cleaned = cleaned.replace("---PAGE BREAK---", " ").replace("---Page Break---", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;[]{}")
        cleaned = EntityExtractor._repair_label_fragments(cleaned)
        if cleaned.count("(") < cleaned.count(")"):
            cleaned = cleaned.rstrip(")")
        if cleaned.count(")") < cleaned.count("(") and ")" in str(label or ""):
            cleaned = f"{cleaned})"
        return cleaned[:120]

    @staticmethod
    def _detect_paper_year(text: str) -> int | None:
        header = (text or "")[:6000]
        current_year = datetime.now().year + 1
        candidates = [
            int(match.group(0))
            for match in re.finditer(r"\b(?:19|20)\d{2}\b", header)
            if 1900 <= int(match.group(0)) <= current_year
        ]
        if not candidates:
            return None

        dated = re.search(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+((?:19|20)\d{2})\b",
            header,
            flags=re.IGNORECASE,
        )
        if dated:
            return int(dated.group(1))
        return candidates[0]

    @classmethod
    def _merge_entity_lists(cls, *entity_lists: list[Any]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for entity_list in entity_lists:
            for item in entity_list:
                if not isinstance(item, dict):
                    continue
                label = cls._clean_label(str(item.get("label") or item.get("term") or ""))
                normalized = cls._normalize_label(label)
                if not normalized:
                    continue
                candidate = dict(item)
                candidate["label"] = label
                if normalized not in merged:
                    merged[normalized] = candidate
                    order.append(normalized)
                    continue

                existing = merged[normalized]
                existing_conf = cls._coerce_float(existing.get("confidence"), 0.0)
                candidate_conf = cls._coerce_float(candidate.get("confidence"), 0.0)
                if candidate_conf > existing_conf:
                    candidate, existing = existing, candidate
                    merged[normalized] = existing
                if (
                    existing.get("candidate_source") == "deterministic_scan"
                    and candidate.get("candidate_source") != "deterministic_scan"
                ):
                    existing.pop("candidate_source", None)
                for key in ("context", "description"):
                    extra = str(candidate.get(key) or "").strip()
                    current = str(existing.get(key) or "").strip()
                    if extra and extra not in current:
                        existing[key] = (current + " | " + extra).strip(" |")[:700]
                if candidate.get("auto_detected"):
                    existing["auto_detected"] = existing.get("auto_detected", False) or candidate.get("auto_detected")
        return [merged[key] for key in order]

    @staticmethod
    def _log_count_warnings(
        paper_type: str,
        concept_count: int,
        warnings: list[str] | None = None,
    ) -> None:
        """Log paper-type-aware concept count warnings for manual review."""
        if paper_type == "survey" and concept_count < 30:
            logger.warning(
                "Survey paper yielded only %s concepts - possible truncation. Consider manual review.",
                concept_count,
            )
        elif paper_type == "research" and concept_count < 8:
            logger.warning("Research paper yielded only %s concepts.", concept_count)
        for warning in warnings or []:
            logger.warning("Extraction quality warning: %s", warning)

    def _write_quality_record(
        self,
        paper_id: str,
        payload: dict[str, Any],
        duration_seconds: float,
        provider: str | None,
        overrides: dict[str, Any],
        call_1_tokens_used: int,
        call_2_tokens_used: int,
    ) -> None:
        """Persist quality telemetry without affecting extraction success."""
        if not self.quality_db_path:
            return
        try:
            from storage.metadata_db import MetadataDB

            with MetadataDB(self.quality_db_path) as db:
                db.save_extraction_quality(
                    paper_id=paper_id,
                    concept_count=len(payload.get("concepts") or []),
                    method_count=len(payload.get("methods") or []),
                    claim_count=len(payload.get("claims") or []),
                    has_formulas=bool((payload.get("mathematical_content") or {}).get("has_formulas")),
                    auto_detected_concepts=int(payload.get("auto_detected_concepts") or 0),
                    parse_quality=str(payload.get("extraction_parse_quality") or "partial"),
                    call_1_tokens_used=call_1_tokens_used,
                    call_2_tokens_used=call_2_tokens_used,
                    duration_seconds=duration_seconds,
                    model=self._model_name(provider, overrides),
                )
        except Exception:
            logger.exception("Failed to persist extraction quality for paper_id=%s", paper_id)

    def _model_name(self, provider: str | None, overrides: dict[str, Any]) -> str:
        """Resolve model name for quality telemetry."""
        if overrides.get("model"):
            return str(overrides["model"])
        try:
            return str(self.llm.provider_settings(provider).model)
        except Exception:
            return "unknown"
