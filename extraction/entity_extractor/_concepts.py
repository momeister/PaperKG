"""EntityExtractor: Konzept-Validierung, Kalibrierung, Terminologie-Konflikte. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from extraction.entity_extractor._shared import (
    DeterministicScanResult,
    RegexValidationResult,
)

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class ConceptsMixin(_Base):
    """Konzept-Validierung, Kalibrierung, Terminologie-Konflikte."""

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
