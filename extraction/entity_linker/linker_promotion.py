"""EntityLinker: Kandidaten-Promotion (Review-Queue, Relations-Endpunkte). (Mixin)

Split out of extraction/entity_linker/linker.py. Behaviour unchanged.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from extraction.text_normalization import normalize_key, normalize_scientific_text
from extraction.entity_linker.relation_extractor import ControlledRelationExtractor
from extraction.entity_linker.strategies import _coerce_float

if TYPE_CHECKING:
    from extraction.entity_linker.linker import EntityLinker

    _Base = EntityLinker
else:
    _Base = object


class LinkerPromotionMixin(_Base):
    """Kandidaten-Promotion (Review-Queue, Relations-Endpunkte)."""

    @staticmethod
    def _repair_mention_count(entity: dict[str, Any]) -> dict[str, Any]:
        """Backfill alias-based mention counts for accepted LLM entities.

        Some local models emit `mention_count: 0` for a method even when the
        evidence span contains an ontology alias, e.g. "Model-based RL" for
        "Model-based reinforcement learning". Keep non-zero counts intact.
        """
        try:
            if int(_coerce_float(entity.get("mention_count"), 0.0)) > 0:
                return entity
        except (TypeError, ValueError):
            return entity

        evidence = " ".join(
            str(entity.get(key) or "")
            for key in ("evidence_span", "context", "description")
        )
        evidence = normalize_scientific_text(evidence).lower()
        if not evidence.strip():
            return entity

        labels = [
            str(entity.get("label") or ""),
            str(entity.get("canonical_label") or ""),
            *[str(alias) for alias in (entity.get("aliases") or []) if alias],
        ]
        for label in labels:
            normalized = normalize_scientific_text(label).lower().strip()
            if not normalized:
                continue
            if re.search(rf"(?<![a-z0-9]){re.escape(normalized)}(?![a-z0-9])", evidence):
                item = dict(entity)
                item["mention_count"] = 1
                return item
        return entity

    @staticmethod
    def _approve_accepted_method(method: dict[str, Any]) -> dict[str, Any]:
        """Allow precise accepted methods to become KG nodes even without ontology matches."""
        if method.get("accepted") is not True:
            return method
        if str(method.get("review_status") or "").lower() == "rejected":
            return method
        if str(method.get("candidate_reason") or ""):
            return method
        confidence = _coerce_float(method.get("confidence"), 0.0)
        if confidence < 0.70:
            return method
        source_type = str(method.get("source_type") or "reviewed_method")
        if source_type not in {"paper_contribution", "reviewed_method", "baseline"}:
            return method
        item = dict(method)
        item["review_status"] = "approved"
        item.setdefault("acceptance_reason", "accepted_method_high_precision")
        return item

    @staticmethod
    def _approve_accepted_central_concept(concept: dict[str, Any]) -> dict[str, Any]:
        """Allow high-confidence central systems/architectures introduced or used by a paper."""
        if concept.get("accepted") is not True:
            return concept
        if str(concept.get("review_status") or "").lower() in {"approved", "rejected"}:
            return concept
        if str(concept.get("candidate_reason") or ""):
            return concept

        confidence = _coerce_float(concept.get("confidence"), 0.0)
        salience = str(concept.get("salience") or "").lower()
        entity_type = str(concept.get("entity_type") or "")
        evidence_role = str(concept.get("evidence_role") or "").lower()
        source_type = str(concept.get("source_type") or "").lower()
        mention_count = int(_coerce_float(concept.get("mention_count"), 0.0))
        if confidence < 0.85 or salience != "central":
            return concept
        if entity_type not in {"System", "ModelArchitecture", "Benchmark", "DomainConcept", "MethodFamily"}:
            return concept
        if evidence_role not in {
            "method_family",
            "model_architecture",
            "system",
            "benchmark",
            "domain_concept",
        } and source_type not in {"paper_contribution", "reviewed_method"}:
            return concept
        if entity_type in {"DomainConcept", "MethodFamily"} and mention_count < 2 and source_type not in {
            "paper_contribution",
            "reviewed_method",
        }:
            return concept

        item = dict(concept)
        item["review_status"] = "approved"
        item.setdefault("acceptance_reason", "accepted_central_entity_high_precision")
        return item

    @classmethod
    def _promote_exact_review_candidates(
        cls,
        paper_type: str,
        concepts: list[dict[str, Any]],
        concept_candidates: list[dict[str, Any]],
        paper_node: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Promote high-confidence ontology-backed candidates lost to partial JSON."""
        existing_ids = {str(item.get("canonical_id")) for item in concepts if item.get("canonical_id")}
        paper_title = str((paper_node or {}).get("title") or "")
        is_survey = paper_type == "survey"
        is_framework_or_benchmark = paper_type in {"benchmark", "research"}
        promotable_types = {
            "Theory",
            "Metric",
            "DomainConcept",
            "ApplicationSetting",
            "ModelArchitecture",
            "System",
            "Phenomenon",
        }
        title_promotable_types = {*promotable_types, "MethodFamily"}
        promotable_method_family_keys = {
            "tdlearning",
            "motivatedreinforcementlearning",
            "modelbasedrl",
            "policysearch",
            "statemodification",
            "metalearning",
            "machinelearning",
        }
        promoted: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for candidate in concept_candidates:
            canonical_id = str(candidate.get("canonical_id") or "")
            match = candidate.get("canonical_match") or {}
            entity_type = str(candidate.get("entity_type") or "")
            canonical_key = normalize_key(candidate.get("canonical_label") or candidate.get("label"))
            confidence = _coerce_float(candidate.get("confidence"), 0.0)
            mention_count = int(_coerce_float(candidate.get("mention_count"), 0.0))
            salience = str(candidate.get("salience") or "").lower()
            candidate_source = str(candidate.get("candidate_source") or "").lower()
            exact_match = (
                canonical_id
                and canonical_id not in existing_ids
                and match.get("match_type") == "exact_alias"
            )
            standard_rescue = (
                is_survey
                and exact_match
                and entity_type in promotable_types
                and confidence >= 0.70
                and (mention_count >= 2 or salience in {"central", "supporting"})
            )
            method_family_rescue = (
                is_survey
                and exact_match
                and entity_type == "MethodFamily"
                and canonical_key in promotable_method_family_keys
                and confidence >= 0.60
                and (mention_count >= 1 or salience in {"central", "supporting"})
            )
            title_rescue = (
                exact_match
                and entity_type in title_promotable_types
                and confidence >= 0.50
                and cls._entity_appears_in_title(candidate, paper_title)
            )
            qml_rescue = (
                is_framework_or_benchmark
                and exact_match
                and canonical_key in cls.QML_CORE_KEYS
                and confidence >= 0.60
                and (mention_count >= 1 or salience in {"central", "supporting"} or title_rescue)
            )
            benchmark_rescue = (
                paper_type == "benchmark"
                and exact_match
                and canonical_key in cls.BENCHMARK_CORE_KEYS
                and confidence >= 0.60
                and (mention_count >= 1 or salience in {"central", "supporting"} or title_rescue)
            )
            theoretical_rescue = (
                paper_type == "theoretical"
                and exact_match
                and canonical_key in cls.THEORETICAL_CORE_KEYS
                and confidence >= 0.60
                and (
                    mention_count >= 2
                    or salience in {"central", "supporting"}
                    or title_rescue
                    or candidate_source == "deterministic_scan"
                )
            )
            medical_imaging_rescue = (
                paper_type in {"research", "benchmark"}
                and exact_match
                and canonical_key in cls.MEDICAL_IMAGING_CORE_KEYS
                and confidence >= 0.60
                and (
                    mention_count >= 1
                    or salience in {"central", "supporting"}
                    or title_rescue
                    or candidate_source == "deterministic_scan"
                )
            )
            should_promote = (
                canonical_key not in cls.DETAIL_ONLY_KEYS
                and (
                    standard_rescue
                    or method_family_rescue
                    or title_rescue
                    or qml_rescue
                    or benchmark_rescue
                    or theoretical_rescue
                    or medical_imaging_rescue
                )
            )
            if should_promote:
                item = dict(candidate)
                item["accepted"] = True
                item["review_status"] = "approved"
                item["acceptance_reason"] = "ontology_exact_candidate_rescue"
                item.pop("candidate_reason", None)
                promoted.append(item)
                existing_ids.add(canonical_id)
            else:
                remaining.append(candidate)
        return [*concepts, *promoted], remaining

    @staticmethod
    def _entity_appears_in_title(entity: dict[str, Any], title: str) -> bool:
        title_key = normalize_key(title)
        if not title_key:
            return False
        labels = [
            str(entity.get("canonical_label") or ""),
            str(entity.get("label") or ""),
            *[str(alias) for alias in (entity.get("aliases") or []) if alias],
        ]
        for label in labels:
            key = normalize_key(label)
            if len(key) >= 8 and key in title_key:
                return True
        return False

    @classmethod
    def _promote_exact_review_method_candidates(
        cls,
        paper_type: str,
        methods: list[dict[str, Any]],
        method_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Promote precise ontology-backed methods lost to candidate arrays."""
        if paper_type not in {"survey", "benchmark"}:
            return methods, method_candidates
        existing_ids = {str(item.get("canonical_id")) for item in methods if item.get("canonical_id")}
        promotable_types = {"Algorithm", "MethodFamily", "ModelArchitecture", "System", "Task"}
        promoted: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for candidate in method_candidates:
            canonical_id = str(candidate.get("canonical_id") or "")
            match = candidate.get("canonical_match") or {}
            entity_type = str(candidate.get("entity_type") or "")
            confidence = _coerce_float(candidate.get("confidence"), 0.0)
            mention_count = int(_coerce_float(candidate.get("mention_count"), 0.0))
            salience = str(candidate.get("salience") or "").lower()
            source_type = str(candidate.get("source_type") or "").lower()
            survey_promote = (
                paper_type == "survey"
                and canonical_id
                and canonical_id not in existing_ids
                and match.get("match_type") == "exact_alias"
                and entity_type in promotable_types
                and confidence >= 0.70
                and source_type in {"reviewed_method", "baseline"}
                and (mention_count >= 1 or salience in {"central", "supporting"})
            )
            benchmark_key = normalize_key(candidate.get("canonical_label") or candidate.get("label"))
            benchmark_promote = (
                paper_type == "benchmark"
                and canonical_id
                and canonical_id not in existing_ids
                and match.get("match_type") == "exact_alias"
                and entity_type in promotable_types
                and benchmark_key in cls.BENCHMARK_CORE_KEYS
                and confidence >= 0.70
                and source_type in {"reviewed_method", "baseline"}
                and (mention_count >= 1 or salience in {"central", "supporting"})
            )
            medical_imaging_promote = (
                paper_type in {"research", "benchmark"}
                and canonical_id
                and canonical_id not in existing_ids
                and match.get("match_type") == "exact_alias"
                and entity_type in promotable_types
                and benchmark_key in cls.MEDICAL_IMAGING_CORE_KEYS
                and confidence >= 0.70
                and source_type in {"reviewed_method", "baseline", "paper_contribution"}
                and (mention_count >= 1 or salience in {"central", "supporting"})
            )
            should_promote = survey_promote or benchmark_promote or medical_imaging_promote
            if should_promote:
                item = dict(candidate)
                item["accepted"] = True
                item["review_status"] = "approved"
                item["acceptance_reason"] = "ontology_exact_method_candidate_rescue"
                item.pop("candidate_reason", None)
                promoted.append(item)
                existing_ids.add(canonical_id)
            else:
                remaining.append(candidate)
        return [*methods, *promoted], remaining

    @classmethod
    def _promote_relation_endpoint_candidates(
        cls,
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
        concept_candidates: list[dict[str, Any]],
        method_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        """Rescue ontology-backed candidates needed for approved structural relations.

        This keeps important relation endpoints stable across LLM runs without
        promoting arbitrary background mentions. Correspondence edges remain
        review-only because they often encode cited speculative mappings.
        """
        approved_keys = {
            normalize_key(item.get("canonical_label") or item.get("label"))
            for item in [*concepts, *methods]
            if isinstance(item, dict) and str(item.get("review_status") or "").lower() == "approved"
        }
        approved_ids = {
            str(item.get("canonical_id"))
            for item in [*concepts, *methods]
            if isinstance(item, dict) and item.get("canonical_id")
        }
        promotable_relations = {
            "IS_A",
            "EXTENDS",
            "USES",
            "USED_IN",
            "USED_FOR",
            "CAUSES",
            "LEADS_TO",
            "GROUPED_WITH_IN_SURVEY",
            "MAPPED_TO_IN_TAXONOMY",
            "IMPLEMENTS",
            "ELICITS",
            "PART_OF",
            "IMPLIES",
            "MEASURES",
        }
        relation_endpoint_keys: set[str] = set()
        for subject_key, relation_type, object_key in ControlledRelationExtractor.KNOWN_RELATION_TEMPLATES:
            if relation_type not in promotable_relations:
                continue
            if subject_key in approved_keys:
                relation_endpoint_keys.add(object_key)
            if object_key in approved_keys:
                relation_endpoint_keys.add(subject_key)

        concept_like = {
            "Theory",
            "Metric",
            "System",
            "DomainConcept",
            "ApplicationSetting",
            "ModelArchitecture",
            "Phenomenon",
            "Benchmark",
            "Dataset",
        }

        def should_promote(item: dict[str, Any]) -> bool:
            canonical_id = str(item.get("canonical_id") or "")
            if not canonical_id or canonical_id in approved_ids:
                return False
            match = item.get("canonical_match") or {}
            if match.get("match_type") != "exact_alias":
                return False
            key = normalize_key(item.get("canonical_label") or item.get("label"))
            if key not in relation_endpoint_keys:
                return False
            if key in cls.DETAIL_ONLY_KEYS:
                return False
            confidence = _coerce_float(item.get("confidence"), 0.0)
            mention_count = int(_coerce_float(item.get("mention_count"), 0.0))
            salience = str(item.get("salience") or "").lower()
            return confidence >= 0.60 and (mention_count >= 1 or salience in {"central", "supporting"})

        promoted_concepts: list[dict[str, Any]] = []
        promoted_methods: list[dict[str, Any]] = []
        remaining_concepts: list[dict[str, Any]] = []
        remaining_methods: list[dict[str, Any]] = []

        for candidate in concept_candidates:
            if should_promote(candidate):
                item = cls._mark_relation_endpoint_promoted(candidate)
                promoted_concepts.append(item)
                approved_ids.add(str(item.get("canonical_id")))
                approved_keys.add(normalize_key(item.get("canonical_label") or item.get("label")))
            else:
                remaining_concepts.append(candidate)

        for candidate in method_candidates:
            if should_promote(candidate):
                item = cls._mark_relation_endpoint_promoted(candidate)
                if str(item.get("entity_type") or "") in concept_like:
                    promoted_concepts.append(item)
                else:
                    promoted_methods.append(item)
                approved_ids.add(str(item.get("canonical_id")))
                approved_keys.add(normalize_key(item.get("canonical_label") or item.get("label")))
            else:
                remaining_methods.append(candidate)

        return (
            [*concepts, *promoted_concepts],
            [*methods, *promoted_methods],
            remaining_concepts,
            remaining_methods,
        )

    @staticmethod
    def _mark_relation_endpoint_promoted(candidate: dict[str, Any]) -> dict[str, Any]:
        item = dict(candidate)
        item["accepted"] = True
        item["review_status"] = "approved"
        item["acceptance_reason"] = "ontology_relation_endpoint_rescue"
        item.pop("candidate_reason", None)
        return item
