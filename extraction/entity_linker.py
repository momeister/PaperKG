from __future__ import annotations

import json
import re
from typing import Any

from extraction.embedding_engine import EmbeddingEngine
from extraction.ontology import CanonicalResolver, Ontology, stable_canonical_id
from extraction.text_normalization import normalize_key, normalize_scientific_text

from extraction.entity_extractor import EntityExtractor, ExtractionResult
from query.llm_router import LLMRouter


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


class ConceptLinkageStrategy:
    """Base strategy for linking extracted concepts to knowledge base."""

    def link(self, concept: dict[str, str]) -> dict[str, Any] | None:
        """Link concept to knowledge base. Returns enriched concept or None."""
        raise NotImplementedError


class OpenAlexLinkageStrategy(ConceptLinkageStrategy):
    """
    Links extracted concepts to OpenAlex Concept IDs using similarity.
    Can be extended to query OpenAlex API or use local embeddings.
    """

    def __init__(
        self,
        concept_cache: dict[str, dict] | None = None,
        embedding_engine: EmbeddingEngine | None = None,
        similarity_threshold: float = 0.85,
    ) -> None:
        """
        Initialize with optional concept cache.

        Args:
            concept_cache: Pre-populated dict of {concept_label -> openalx_concept_data}
        """
        self.cache = concept_cache or {}
        self.embedding_engine = embedding_engine
        self.similarity_threshold = similarity_threshold

    def link(self, concept: dict[str, str]) -> dict[str, Any] | None:
        """
        Link concept to OpenAlex Concept ID.

        Args:
            concept: Dict with 'label', 'context', 'confidence' keys

        Returns:
            Enriched dict with 'openalx_id', 'openalx_label' or None if not found
        """
        label = normalize_key(concept.get("label", ""))

        cached = self._lookup_exact(label)
        if cached is None:
            cached = self._lookup_by_embedding(label)
        if cached is not None:
            return self._enrich(concept, cached)

        return None

    def _lookup_exact(self, normalized_label: str) -> dict[str, Any] | None:
        if normalized_label in self.cache:
            return self.cache[normalized_label]
        for key, item in self.cache.items():
            if normalize_key(key) == normalized_label:
                return item

        for item in self.cache.values():
            labels = [item.get("display_name", "")]
            labels.extend(item.get("aliases") or [])
            if normalized_label in {normalize_key(label) for label in labels}:
                return item
        return None

    def _lookup_by_embedding(self, normalized_label: str) -> dict[str, Any] | None:
        if self.embedding_engine is None or not self.cache:
            return None

        query_vector = self.embedding_engine.embed(normalized_label)
        best_item = None
        best_score = 0.0
        for item in self.cache.values():
            candidate_label = item.get("display_name")
            if not candidate_label:
                continue
            score = self.embedding_engine.similarity(
                query_vector,
                self.embedding_engine.embed(str(candidate_label)),
            )
            if score > best_score:
                best_score = score
                best_item = item

        if best_item is not None and best_score >= self.similarity_threshold:
            return {**best_item, "link_score": best_score}
        return None

    @staticmethod
    def _enrich(concept: dict[str, str], cached: dict[str, Any]) -> dict[str, Any]:
        enriched = {
            **concept,
            "openalx_id": cached.get("id"),
            "openalx_label": cached.get("display_name"),
            "canonical_id": str(cached.get("id") or stable_canonical_id(concept.get("label", ""))),
            "canonical_label": cached.get("display_name") or concept.get("label", ""),
            "review_status": concept.get("review_status") or "approved",
        }
        if "link_score" in cached:
            enriched["link_score"] = cached["link_score"]
        return enriched


class EntityLinker:
    """
    Links extracted entities to external knowledge bases (OpenAlex, Wikidata, etc).
    Enriches extraction results with authoritative IDs and metadata.
    """
    CORE_KG_KEYS = {
        "actionselection",
        "affectivecomputing",
        "appraisaldimensions",
        "appraisaltheory",
        "arousal",
        "bayesianaffectcontroltheory",
        "boltzmannactionselection",
        "categoricalemotion",
        "categoricalemotiontheory",
        "conceptdrift",
        "datapipeline",
        "datasourcechanges",
        "datavalidation",
        "deepreinforcementlearning",
        "dimensionalemotion",
        "dimensionalemotiontheory",
        "externaldatasources",
        "featuremismatch",
        "dynamicprogramming",
        "emotionelicitation",
        "emotionfunction",
        "emotiontype",
        "epiphenomenon",
        "extrinsicmotivation",
        "homeostasis",
        "humanrobotinteraction",
        "intrinsicmotivation",
        "learningefficiency",
        "machinelearning",
        "markovdecisionprocess",
        "metalearning",
        "modelstaleness",
        "modelbasedrl",
        "motivatedreinforcementlearning",
        "navigationtask",
        "officialstatistics",
        "operationalizationbias",
        "policysearch",
        "pomdp",
        "qlearning",
        "qualitymetrics",
        "reinforcementlearning",
        "rewardfunction",
        "rewardshaping",
        "sarsa",
        "selectionbias",
        "socialinteraction",
        "statemodification",
        "tdlambda",
        "tdlearning",
        "temporaldifferenceerror",
        "valence",
        "valuefunction",
    }
    DETAIL_ONLY_KEYS = {
        "acetylcholine",
        "averagereward",
        "boltzmannactionselectiontemperature",
        "discountfactor",
        "dopamine",
        "euclideandistance",
        "kldivergence",
        "l1norm",
        "learningrate",
        "modeluncertainty",
        "noradrenaline",
        "serotonin",
        "stateactionvalue",
    }
    TAXONOMY_AXIS_KEYS = {"emotionelicitation", "emotiontype", "emotionfunction"}
    EMOTION_FUNCTION_CATEGORY_KEYS = {
        "actionselection",
        "epiphenomenon",
        "metalearning",
        "rewardshaping",
        "statemodification",
    }
    EMOTION_TYPE_CATEGORY_KEYS = {"categoricalemotion", "dimensionalemotion"}

    def __init__(
        self,
        strategy: ConceptLinkageStrategy | None = None,
        resolver: CanonicalResolver | None = None,
    ) -> None:
        """
        Initialize linker with optional linkage strategy.

        Args:
            strategy: ConceptLinkageStrategy for concept linking (uses OpenAlex by default)
        """
        self.strategy = strategy or OpenAlexLinkageStrategy()
        self.resolver = resolver or CanonicalResolver()

    def enrich_extraction(
        self, extraction: ExtractionResult
    ) -> ExtractionResult:
        """
        Enrich extraction result with external knowledge base links.

        Args:
            extraction: ExtractionResult from EntityExtractor

        Returns:
            New ExtractionResult with enriched concepts containing IDs
        """
        enriched_concepts = []

        for concept in extraction.concepts:
            enriched_concepts.append(self._enrich_entity(concept, default_entity_type="DomainConcept"))

        enriched_methods = [
            self._enrich_entity(method, default_entity_type="Algorithm")
            for method in extraction.methods
        ]
        enriched_methods = self._dedupe_methods(
            extraction.paper_type,
            enriched_methods,
        )
        enriched_concept_candidates = [
            self._enrich_candidate_entity(candidate, default_entity_type="DomainConcept")
            for candidate in extraction.concept_candidates
        ]
        enriched_method_candidates = [
            self._enrich_candidate_entity(candidate, default_entity_type="Algorithm")
            for candidate in extraction.method_candidates
        ]
        enriched_concepts, enriched_concept_candidates = self._promote_exact_review_candidates(
            extraction.paper_type,
            enriched_concepts,
            enriched_concept_candidates,
            extraction.paper_node,
        )
        enriched_methods, enriched_method_candidates = self._promote_exact_review_method_candidates(
            extraction.paper_type,
            enriched_methods,
            enriched_method_candidates,
        )
        (
            enriched_concepts,
            enriched_methods,
            enriched_concept_candidates,
            enriched_method_candidates,
        ) = self._promote_relation_endpoint_candidates(
            enriched_concepts,
            enriched_methods,
            enriched_concept_candidates,
            enriched_method_candidates,
        )

        enriched_concepts, enriched_methods = self._dedupe_graph_entities(
            enriched_concepts,
            enriched_methods,
        )
        enriched_concepts = self._annotate_kg_layers(enriched_concepts, extraction.paper_type, "concept")
        enriched_methods = self._annotate_kg_layers(enriched_methods, extraction.paper_type, "method")
        enriched_concept_candidates, enriched_method_candidates = self._filter_shadowed_candidates(
            enriched_concept_candidates,
            enriched_method_candidates,
            enriched_concepts,
            enriched_methods,
        )
        relations = ControlledRelationExtractor(self.resolver).extract(
            enriched_concepts,
            enriched_methods,
            extraction.relations,
            enriched_concept_candidates,
            enriched_method_candidates,
        )

        enriched_result = ExtractionResult(
            paper_id=extraction.paper_id,
            paper_type=extraction.paper_type,
            paper_node=extraction.paper_node,
            concepts=enriched_concepts,
            methods=enriched_methods,
            concept_candidates=enriched_concept_candidates,
            method_candidates=enriched_method_candidates,
            relations=relations,
            claims=extraction.claims,
            cross_domain_hints=extraction.cross_domain_hints,
            terminology_conflicts=extraction.terminology_conflicts,
            temporal_coverage=extraction.temporal_coverage,
            mathematical_content=extraction.mathematical_content,
            language_detected=extraction.language_detected,
            quality_warnings=extraction.quality_warnings,
            metadata_status=extraction.metadata_status,
            blocking_errors=extraction.blocking_errors,
            candidate_count=extraction.candidate_count,
            extraction_diagnostics=extraction.extraction_diagnostics,
            raw_response=extraction.raw_response,
            extraction_mode=extraction.extraction_mode,
        )
        enriched_result.raw_response = self._render_raw_response(enriched_result, extraction.raw_response)
        return enriched_result

    def _enrich_entity(
        self,
        entity: dict[str, Any],
        default_entity_type: str,
    ) -> dict[str, Any]:
        item = dict(entity)
        item.setdefault("entity_type", default_entity_type)
        if default_entity_type == "Algorithm" and not item.get("canonical_id"):
            item["canonical_id"] = stable_canonical_id(item.get("label", ""), prefix="method")
        linked = self.strategy.link(item) if default_entity_type == "DomainConcept" else None
        if linked:
            item = dict(linked)
        resolved = self.resolver.resolve(item)
        if default_entity_type == "Algorithm":
            resolved = self._approve_accepted_method(resolved)
        else:
            resolved = self._approve_accepted_central_concept(resolved)
        resolved = self._repair_mention_count(resolved)
        return resolved

    def _enrich_candidate_entity(
        self,
        entity: dict[str, Any],
        default_entity_type: str,
    ) -> dict[str, Any]:
        item = self._enrich_entity(entity, default_entity_type=default_entity_type)
        if str(item.get("review_status") or "").lower() != "rejected":
            item["review_status"] = "pending"
        item["accepted"] = False
        item["accepted_for_kg_write"] = False
        item["kg_layer"] = "candidate_review"
        item.setdefault("candidate_reason", item.get("candidate_source") or "needs_review")
        return item

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
        if confidence < 0.85 or salience != "central":
            return concept
        if entity_type not in {"System", "ModelArchitecture", "Benchmark"}:
            return concept
        if evidence_role not in {"method_family", "system", "benchmark", "domain_concept"} and source_type not in {
            "paper_contribution",
            "reviewed_method",
        }:
            return concept

        item = dict(concept)
        item["review_status"] = "approved"
        item.setdefault("acceptance_reason", "accepted_central_entity_high_precision")
        return item

    @classmethod
    def _dedupe_methods(
        cls,
        paper_type: str,
        methods: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        merged: list[dict[str, Any]] = []
        survey_contribution: dict[str, Any] | None = None
        author_year: dict[str, dict[str, Any]] = {}

        for method in methods:
            if not isinstance(method, dict):
                continue
            item = dict(method)
            if paper_type == "survey" and cls._is_survey_contribution_method(item):
                canonical = cls._canonical_survey_contribution(item)
                survey_contribution = canonical if survey_contribution is None else cls._merge_method_entities(
                    survey_contribution,
                    canonical,
                    prefer_source=True,
                )
                continue

            key = cls._author_year_method_key(item)
            if key:
                existing = author_year.get(key)
                author_year[key] = item if existing is None else cls._merge_method_entities(
                    existing,
                    item,
                    prefer_source=cls._prefer_method(item, existing),
                )
                continue

            merged.append(item)

        if survey_contribution is not None:
            merged.append(survey_contribution)
        merged.extend(author_year.values())
        return cls._dedupe_methods_by_label(merged)

    @staticmethod
    def _is_survey_contribution_method(method: dict[str, Any]) -> bool:
        if str(method.get("source_type") or "") != "paper_contribution":
            return False
        text = " ".join(
            str(method.get(key) or "")
            for key in ("label", "canonical_label", "description", "evidence_span")
        ).lower()
        return bool(
            re.search(r"\b(taxonom\w*|framework|categorization|categorisation|overview)\b", text)
            and re.search(r"\b(emotion|affect|rl|reinforcement|intrinsic)\b", text)
        )

    @staticmethod
    def _canonical_survey_contribution(method: dict[str, Any]) -> dict[str, Any]:
        item = dict(method)
        original_label = str(item.get("label") or "")
        aliases = list(item.get("aliases") or [])
        if original_label and original_label != "Emotion in RL Survey Taxonomy" and original_label not in aliases:
            aliases.append(original_label)
        item["label"] = "Emotion in RL Survey Taxonomy"
        item["canonical_label"] = "Emotion in RL Survey Taxonomy"
        item["canonical_id"] = stable_canonical_id("Emotion in RL Survey Taxonomy", prefix="method")
        item["entity_type"] = "MethodFamily"
        item["source_type"] = "paper_contribution"
        if aliases:
            item["aliases"] = aliases
        item["review_status"] = "approved" if item.get("accepted") is True else item.get("review_status", "pending")
        item.setdefault("acceptance_reason", "survey_contribution_canonicalized")
        return item

    @staticmethod
    def _author_year_method_key(method: dict[str, Any]) -> str:
        label = str(method.get("canonical_label") or method.get("label") or "")
        normalized = normalize_key(label)
        if not normalized:
            return ""
        base = re.sub(r"\s*\((?:19|20)\d{2}(?:\s*,\s*(?:19|20)\d{2})*\)\s*", " ", label)
        base = re.sub(r"\b(emotion|affective)\s+model\b", " ", base, flags=re.IGNORECASE)
        base_key = normalize_key(base)
        if not base_key or base_key == normalized:
            return ""
        if not re.search(r"\b(?:and|et\s+al\.?)\b", label, flags=re.IGNORECASE):
            return ""
        return f"author_method:{base_key}"

    @classmethod
    def _dedupe_methods_by_label(cls, methods: list[dict[str, Any]]) -> list[dict[str, Any]]:
        output: dict[str, dict[str, Any]] = {}
        for method in methods:
            key = normalize_key(method.get("canonical_label") or method.get("label"))
            if not key:
                continue
            existing = output.get(key)
            output[key] = method if existing is None else cls._merge_method_entities(
                existing,
                method,
                prefer_source=cls._prefer_method(method, existing),
            )
        return list(output.values())

    @staticmethod
    def _prefer_method(candidate: dict[str, Any], current: dict[str, Any]) -> bool:
        candidate_label = str(candidate.get("label") or "")
        current_label = str(current.get("label") or "")
        candidate_has_year = bool(re.search(r"\((?:19|20)\d{2}", candidate_label))
        current_has_year = bool(re.search(r"\((?:19|20)\d{2}", current_label))
        if candidate_has_year != current_has_year:
            return not candidate_has_year
        return len(candidate_label) > len(current_label)

    @staticmethod
    def _merge_method_entities(
        target: dict[str, Any],
        source: dict[str, Any],
        prefer_source: bool = False,
    ) -> dict[str, Any]:
        primary, secondary = (source, target) if prefer_source else (target, source)
        output = dict(primary)
        aliases = list(output.get("aliases") or [])
        for alias in secondary.get("aliases") or []:
            if alias and str(alias) not in aliases and str(alias) != output.get("label"):
                aliases.append(str(alias))
        for alias in (
            secondary.get("label"),
            secondary.get("canonical_label"),
            primary.get("label"),
            primary.get("canonical_label"),
        ):
            if alias and str(alias) not in aliases and str(alias) != output.get("label"):
                aliases.append(str(alias))
        if aliases:
            output["aliases"] = aliases

        descriptions = [
            str(item).strip()
            for item in (output.get("description"), secondary.get("description"))
            if str(item or "").strip()
        ]
        if descriptions:
            output["description"] = " | ".join(dict.fromkeys(descriptions))[:1000]
        evidence = EntityLinker._best_evidence_span([output, secondary])
        if evidence:
            output["evidence_span"] = evidence
        output["confidence"] = max(_coerce_float(output.get("confidence"), 0.0), _coerce_float(secondary.get("confidence"), 0.0))
        if str(secondary.get("review_status") or "").lower() == "approved":
            output["review_status"] = "approved"
        return output

    @staticmethod
    def _best_evidence_span(items: list[dict[str, Any]]) -> str:
        spans = [
            str(item.get("evidence_span") or item.get("context") or item.get("description") or "").strip()
            for item in items
        ]
        spans = [re.sub(r"\s+", " ", span) for span in spans if span]
        if not spans:
            return ""
        spans.sort(key=len, reverse=True)
        return spans[0][:360]

    @classmethod
    def _annotate_kg_layers(
        cls,
        entities: list[dict[str, Any]],
        paper_type: str,
        role: str,
    ) -> list[dict[str, Any]]:
        return [cls._annotate_kg_layer(entity, paper_type, role) for entity in entities]

    @classmethod
    def _annotate_kg_layer(
        cls,
        entity: dict[str, Any],
        paper_type: str,
        role: str,
    ) -> dict[str, Any]:
        item = dict(entity)
        key = normalize_key(item.get("canonical_label") or item.get("label"))
        paper_role = cls._paper_role_for_key(key)
        if paper_role:
            item["paper_role"] = paper_role

        status = str(item.get("review_status") or "").lower()
        eligible = status == "approved"
        block_reason = ""
        if not eligible:
            block_reason = "not_approved"
        elif key in cls.DETAIL_ONLY_KEYS:
            eligible = False
            block_reason = "detail_or_parameter_mention"
        else:
            source_type = str(item.get("source_type") or "").lower()
            salience = str(item.get("salience") or "").lower()
            entity_type = str(item.get("entity_type") or "")
            evidence_role = str(item.get("evidence_role") or "").lower()
            acceptance_reason = str(item.get("acceptance_reason") or "").lower()
            is_core_key = key in cls.CORE_KG_KEYS
            if source_type in {"background", "generic_field"} and salience != "central" and not is_core_key:
                eligible = False
                block_reason = "background_detail"
            elif evidence_role in {"background", "generic_field", "possible_concept"} and salience not in {"central"} and not is_core_key:
                eligible = False
                block_reason = "review_detail"
            elif entity_type == "System" and source_type == "reviewed_method" and salience != "central" and paper_type == "survey":
                eligible = False
                block_reason = "reviewed_system_detail"
            elif acceptance_reason == "ontology_relation_endpoint_rescue" and not is_core_key:
                eligible = False
                block_reason = "relation_endpoint_detail"

        item["accepted_for_kg_write"] = eligible
        item["kg_layer"] = "core" if eligible else "detail"
        if block_reason:
            item["kg_block_reason"] = block_reason
        return item

    @classmethod
    def _paper_role_for_key(cls, key: str) -> str:
        if key in cls.TAXONOMY_AXIS_KEYS:
            return "taxonomy_axis"
        if key in cls.EMOTION_FUNCTION_CATEGORY_KEYS:
            return "emotion_function_category"
        if key in cls.EMOTION_TYPE_CATEGORY_KEYS:
            return "emotion_type_category"
        if key in {"homeostasis", "appraisaldimensions", "rewardshaping"}:
            return "emotion_elicitation_category"
        return ""

    @classmethod
    def _promote_exact_review_candidates(
        cls,
        paper_type: str,
        concepts: list[dict[str, Any]],
        concept_candidates: list[dict[str, Any]],
        paper_node: dict[str, Any] | None = None,
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Promote high-confidence ontology-backed survey candidates lost to partial JSON."""
        if paper_type != "survey":
            return concepts, concept_candidates
        existing_ids = {str(item.get("canonical_id")) for item in concepts if item.get("canonical_id")}
        paper_title = str((paper_node or {}).get("title") or "")
        promotable_types = {
            "Theory",
            "Metric",
            "DomainConcept",
            "ApplicationSetting",
            "ModelArchitecture",
            "System",
            "Phenomenon",
        }
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
            exact_match = (
                canonical_id
                and canonical_id not in existing_ids
                and match.get("match_type") == "exact_alias"
            )
            standard_rescue = (
                exact_match
                and entity_type in promotable_types
                and confidence >= 0.70
                and (mention_count >= 2 or salience in {"central", "supporting"})
            )
            method_family_rescue = (
                exact_match
                and entity_type == "MethodFamily"
                and canonical_key in promotable_method_family_keys
                and confidence >= 0.60
                and (mention_count >= 1 or salience in {"central", "supporting"})
            )
            title_rescue = (
                exact_match
                and entity_type in promotable_types
                and confidence >= 0.50
                and cls._entity_appears_in_title(candidate, paper_title)
            )
            should_promote = (
                canonical_key not in EntityLinker.DETAIL_ONLY_KEYS
                and (standard_rescue or method_family_rescue or title_rescue)
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

    @staticmethod
    def _promote_exact_review_method_candidates(
        paper_type: str,
        methods: list[dict[str, Any]],
        method_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Promote precise ontology-backed survey methods lost to candidate arrays."""
        if paper_type != "survey":
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
            should_promote = (
                canonical_id
                and canonical_id not in existing_ids
                and match.get("match_type") == "exact_alias"
                and entity_type in promotable_types
                and confidence >= 0.70
                and source_type in {"reviewed_method", "baseline"}
                and (mention_count >= 1 or salience in {"central", "supporting"})
            )
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
            "USED_FOR",
            "GROUPED_WITH_IN_SURVEY",
            "MAPPED_TO_IN_TAXONOMY",
            "IMPLEMENTS",
            "ELICITS",
            "PART_OF",
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

    @staticmethod
    def _dedupe_graph_entities(
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        by_key: dict[str, tuple[str, dict[str, Any]]] = {}
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
        method_like = {"Algorithm", "Task"}

        def entity_key(item: dict[str, Any]) -> str:
            label_key = normalize_key(item.get("canonical_label") or item.get("label"))
            if label_key:
                return f"label:{label_key}"
            canonical_id = str(item.get("canonical_id") or "").strip()
            return f"id:{canonical_id}" if canonical_id else ""

        def preferred_role(item: dict[str, Any], extracted_role: str) -> str:
            entity_type = str(item.get("entity_type") or "")
            if entity_type in concept_like:
                return "concept"
            if entity_type in method_like:
                return "method"
            return extracted_role

        def merge_entity(target: dict[str, Any], source: dict[str, Any], role: str) -> dict[str, Any]:
            output = dict(target)
            aliases = list(output.get("aliases") or [])
            for alias in (source.get("label"), source.get("canonical_label")):
                if alias and str(alias) not in aliases and str(alias) != output.get("label"):
                    aliases.append(str(alias))
            if aliases:
                output["aliases"] = aliases
            roles = set(output.get("extracted_roles") or [])
            roles.add(role)
            output["extracted_roles"] = sorted(roles)
            for key, value in source.items():
                if output.get(key) in (None, "", [], {}) and value not in (None, "", [], {}):
                    output[key] = value
            return output

        for role, items in (("concept", concepts), ("method", methods)):
            for item in items:
                key = entity_key(item)
                if not key:
                    continue
                item_role = preferred_role(item, role)
                current = by_key.get(key)
                if current is None:
                    enriched = dict(item)
                    enriched["extracted_roles"] = sorted({role, *(enriched.get("extracted_roles") or [])})
                    by_key[key] = (item_role, enriched)
                    continue
                existing_role, existing = current
                if item_role != existing_role:
                    by_key[key] = (item_role, merge_entity(item, existing, existing_role))
                else:
                    by_key[key] = (existing_role, merge_entity(existing, item, role))

        kept_concepts = [item for role, item in by_key.values() if role == "concept"]
        kept_methods = [item for role, item in by_key.values() if role == "method"]
        return kept_concepts, kept_methods

    @staticmethod
    def _filter_shadowed_candidates(
        concept_candidates: list[dict[str, Any]],
        method_candidates: list[dict[str, Any]],
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        accepted_ids = {
            str(item.get("canonical_id"))
            for item in [*concepts, *methods]
            if isinstance(item, dict) and item.get("canonical_id")
        }
        accepted_labels = {
            normalize_key(item.get("canonical_label") or item.get("label"))
            for item in [*concepts, *methods]
            if isinstance(item, dict)
        }
        seen_candidates: set[str] = set()

        def keep(items: list[dict[str, Any]], role: str) -> list[dict[str, Any]]:
            output: list[dict[str, Any]] = []
            for item in items:
                if not isinstance(item, dict):
                    continue
                canonical_id = str(item.get("canonical_id") or "")
                label_key = normalize_key(item.get("canonical_label") or item.get("label"))
                if canonical_id and canonical_id in accepted_ids:
                    continue
                if label_key and label_key in accepted_labels:
                    continue
                candidate_key = canonical_id or label_key
                if not candidate_key:
                    continue
                scoped_key = f"{role}:{candidate_key}"
                cross_key = f"any:{candidate_key}"
                if scoped_key in seen_candidates or cross_key in seen_candidates:
                    continue
                seen_candidates.add(scoped_key)
                seen_candidates.add(cross_key)
                output.append(item)
            return output

        return keep(concept_candidates, "concept"), keep(method_candidates, "method")

    @staticmethod
    def _render_raw_response(extraction: ExtractionResult, previous_raw: str) -> str:
        previous: dict[str, Any] = {}
        try:
            parsed = json.loads(previous_raw) if previous_raw else {}
            if isinstance(parsed, dict):
                previous = parsed
        except json.JSONDecodeError:
            previous = {}
        payload = {
            **previous,
            "paper_type": extraction.paper_type,
            "paper_node": extraction.paper_node,
            "concepts": extraction.concepts,
            "methods": extraction.methods,
            "concept_candidates": extraction.concept_candidates,
            "method_candidates": extraction.method_candidates,
            "relations": extraction.relations,
            "claims": extraction.claims,
            "cross_domain_hints": extraction.cross_domain_hints,
            "terminology_conflicts": extraction.terminology_conflicts,
            "temporal_coverage": extraction.temporal_coverage,
            "mathematical_content": extraction.mathematical_content,
            "language_detected": extraction.language_detected,
            "quality_warnings": extraction.quality_warnings,
            "metadata_status": extraction.metadata_status,
            "blocking_errors": extraction.blocking_errors,
            "extraction_mode": extraction.extraction_mode,
        }
        payload.update(extraction.extraction_diagnostics)
        return json.dumps(payload, indent=2, ensure_ascii=False)


class ControlledRelationExtractor:
    """Build controlled, evidence-carrying relations between canonical entities."""

    KNOWN_RELATION_TEMPLATES: tuple[tuple[str, str, str], ...] = (
        ("appraisaldimensions", "PART_OF", "appraisaltheory"),
        ("occmodel", "IS_A", "appraisaltheory"),
        ("componentprocesstheoryofemotions", "IS_A", "appraisaltheory"),
        ("beliefdesiretheoryofemotions", "IS_A", "appraisaltheory"),
        ("valence", "PART_OF", "dimensionalemotiontheory"),
        ("arousal", "PART_OF", "dimensionalemotiontheory"),
        ("novelty", "PART_OF", "appraisaldimensions"),
        ("recency", "PART_OF", "appraisaldimensions"),
        ("temporaldifferenceerror", "CORRESPONDS_TO", "dopamine"),
        ("rewardshaping", "USES", "rewardfunction"),
        ("rewardfunction", "PART_OF", "markovdecisionprocess"),
        ("transitionmodel", "PART_OF", "markovdecisionprocess"),
        ("valuefunction", "PART_OF", "reinforcementlearning"),
        ("kldivergence", "MEASURES", "modeluncertainty"),
        ("pomdp", "IS_A", "markovdecisionprocess"),
        ("bayesianaffectcontroltheory", "EXTENDS", "pomdp"),
        ("deepreinforcementlearning", "IS_A", "reinforcementlearning"),
        ("motivatedreinforcementlearning", "IS_A", "reinforcementlearning"),
        ("homeostaticrewardmodification", "IS_A", "rewardshaping"),
        ("appraisalbasedrewardmodification", "IS_A", "rewardshaping"),
        ("homeostaticrewardmodification", "USES", "homeostasis"),
        ("appraisalbasedrewardmodification", "USES", "appraisaldimensions"),
        ("homeostasis", "GROUPED_WITH_IN_SURVEY", "extrinsicmotivation"),
        ("appraisaldimensions", "MAPPED_TO_IN_TAXONOMY", "intrinsicmotivation"),
        ("modelbasedrl", "IS_A", "reinforcementlearning"),
        ("modelbasedrl", "USES", "transitionmodel"),
        ("tdlearning", "IS_A", "reinforcementlearning"),
        ("tdlearning", "USES", "temporaldifferenceerror"),
        ("qlearning", "IMPLEMENTS", "valuefunction"),
        ("qlearning", "USES", "temporaldifferenceerror"),
        ("rewardshaping", "USED_FOR", "learningefficiency"),
        ("homeostasis", "ELICITS", "categoricalemotion"),
        ("serotonin", "CORRESPONDS_TO", "discountfactor"),
        ("noradrenaline", "CORRESPONDS_TO", "boltzmannactionselectiontemperature"),
        ("acetylcholine", "CORRESPONDS_TO", "learningrate"),
        ("boltzmannactionselection", "MODULATED_BY", "valence"),
        ("boltzmannactionselection", "USED_FOR", "explorationexploitationtradeoff"),
        ("machinelearning", "USED_IN", "officialstatistics"),
        ("datasourcechanges", "LEADS_TO", "conceptdrift"),
        ("datasourcechanges", "AFFECTS", "qualitymetrics"),
        ("datasourcechanges", "CAUSES", "modelstaleness"),
        ("datasourcechanges", "AFFECTS", "statisticalreporting"),
        ("externaldatasources", "LEADS_TO", "conceptdrift"),
        ("externaldatasources", "LEADS_TO", "selectionbias"),
        ("externaldatasources", "LEADS_TO", "operationalizationbias"),
        ("externaldatasources", "AFFECTS", "qualitymetrics"),
        ("conceptdrift", "CAUSES", "modelstaleness"),
        ("conceptdrift", "AFFECTS", "qualitymetrics"),
        ("featuremismatch", "CAUSES", "modelstaleness"),
        ("datafrequency", "LEADS_TO", "featuremismatch"),
        ("datafrequency", "LEADS_TO", "conceptdrift"),
        ("datasourcediscontinuation", "AFFECTS", "statisticalreporting"),
        ("riskanalysis", "MITIGATES", "featuremismatch"),
        ("riskanalysis", "MITIGATES", "conceptdrift"),
        ("monitoring", "MITIGATES", "conceptdrift"),
        ("monitoring", "MITIGATES", "modelstaleness"),
        ("datavalidation", "MITIGATES", "featuremismatch"),
        ("datanormalization", "MITIGATES", "featuremismatch"),
        ("automatedfeatureanalysis", "MITIGATES", "featuremismatch"),
        ("outlierdetection", "MITIGATES", "selectionbias"),
        ("diversification", "PREVENTS", "datasourcediscontinuation"),
        ("technicalrobustness", "MITIGATES", "datasourcediscontinuation"),
        ("legalguidelines", "MITIGATES", "legalrobustness"),
        ("merlin", "BUILT_ON", "stronglinearopticalsimulation"),
        ("merlin", "PROVIDES", "quantumlayer"),
        ("quantumlayer", "SUPPORTS", "angleencoding"),
        ("quantumlayer", "SUPPORTS", "amplitudeencoding"),
        ("merlin", "REPRODUCES", "quantumlongshorttermmemory"),
        ("merlin", "REPRODUCES", "quantumrecurrentneuralnetwork"),
        ("merlin", "REPRODUCES", "distributedquantumneuralnetwork"),
        ("merlin", "REPRODUCES", "quantumgenerativeadversarialnetwork"),
        ("merlin", "REPRODUCES", "quantumrelationalknowledgedistillation"),
        ("merlin", "REPRODUCES", "quantumselfsupervisedlearning"),
        ("quantumgenerativeadversarialnetwork", "EVALUATED_ON", "mnist"),
        ("quantumllmfinetuning", "EVALUATED_ON", "sst2"),
        ("angleencoding", "MORE_ROBUST_THAN", "amplitudeencoding"),
    )

    GENERIC_EVIDENCE_WORDS = {
        "action",
        "agent",
        "agents",
        "based",
        "concept",
        "emotion",
        "emotional",
        "function",
        "functions",
        "learning",
        "method",
        "methods",
        "model",
        "models",
        "reinforcement",
        "state",
        "states",
        "system",
        "systems",
        "theory",
        "used",
    }
    RL_AUTO_LINK_CORE_KEYS = {
        "boltzmannactionselection",
        "dynamicprogramming",
        "modelbasedrl",
        "policysearch",
        "pomdp",
        "qlearning",
        "sarsa",
        "tdlambda",
        "tdlearning",
        "temporaldifferencelearning",
    }
    RL_AUTO_LINK_ANCHOR_RE = re.compile(
        r"\b("
        r"rl|reinforcement learning|q-learning|q learning|sarsa|td learning|"
        r"temporal difference|value function|reward function|policy search|"
        r"markov decision|mdp|pomdp|model-based rl|model based rl|"
        r"boltzmann action selection|exploration"
        r")\b",
        flags=re.IGNORECASE,
    )
    RL_CONTEXT_TERMS = {
        "agent",
        "agents",
        "appraisal",
        "emotion",
        "emotional",
        "homeostasis",
        "navigation",
        "robot",
    }
    BIO_INSPIRATION_BACKGROUND_TERMS = {
        "bio-inspiration",
        "biologically inspired",
        "evolutionary algorithms",
        "mentioned as advancement",
        "neural networks",
        "swarm-based optimization",
    }

    def __init__(self, resolver: CanonicalResolver) -> None:
        self.resolver = resolver

    def extract(
        self,
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
        existing_relations: list[dict[str, Any]] | None = None,
        concept_candidates: list[dict[str, Any]] | None = None,
        method_candidates: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        structural_entities = [*concepts, *methods]
        approved_entities = [item for item in structural_entities if self._is_approved(item)]
        reviewable_structural_entities = [
            item for item in structural_entities if self._is_reviewable_candidate(item)
        ]
        candidate_entities = [
            item
            for item in [*(concept_candidates or []), *(method_candidates or [])]
            if self._is_reviewable_candidate(item)
        ]
        entities = self._dedupe_relation_entities(
            [*approved_entities, *reviewable_structural_entities, *candidate_entities]
        )
        by_label = self._index_by_label(entities)
        by_id = {str(item.get("canonical_id")): item for item in entities if item.get("canonical_id")}
        relations: list[dict[str, Any]] = []

        for relation in existing_relations or []:
            clean = self._validate_existing_relation(relation, by_id)
            if clean:
                relations.append(clean)

        for subject_key, relation_type, object_key in self.KNOWN_RELATION_TEMPLATES:
            self._add_known_relation(relations, by_label, entities, subject_key, relation_type, object_key)

        reinforcement_learning = by_label.get("reinforcementlearning")
        if reinforcement_learning:
            for item in entities:
                if (
                    str(item.get("entity_type")) == "Algorithm"
                    and item is not reinforcement_learning
                    and self._should_auto_link_algorithm_to_rl(item)
                ):
                    review_status = "approved" if self._is_approved(item) and self._is_approved(reinforcement_learning) else "pending"
                    self._append_relation(
                        relations,
                        item,
                        "USED_IN",
                        reinforcement_learning,
                        self._evidence(item, reinforcement_learning, "USED_IN", entities),
                        review_status=review_status,
                    )

        by_relation_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        for relation in relations:
            key = (
                str(relation.get("subject_id")),
                str(relation.get("relation_type")),
                str(relation.get("object_id")),
            )
            current = by_relation_key.get(key)
            if current is None or self._relation_rank(relation) > self._relation_rank(current):
                by_relation_key[key] = relation
        return list(by_relation_key.values())

    @classmethod
    def _should_auto_link_algorithm_to_rl(cls, item: dict[str, Any]) -> bool:
        """Avoid linking generic background algorithms as RL techniques."""
        canonical_key = normalize_key(item.get("canonical_label") or item.get("label"))
        if canonical_key in cls.RL_AUTO_LINK_CORE_KEYS:
            return True

        confidence = _coerce_float(item.get("confidence"), 0.0)
        if confidence < 0.60:
            return False

        source_type = str(item.get("source_type") or "").lower()
        salience = str(item.get("salience") or "").lower()
        section = str(item.get("section") or "").lower()
        domain = str(item.get("domain") or "").lower()
        text = normalize_scientific_text(
            " ".join(
                str(item.get(key) or "")
                for key in ("label", "canonical_label", "domain", "description", "evidence_span", "context")
            )
        ).lower()

        has_rl_anchor = bool(cls.RL_AUTO_LINK_ANCHOR_RE.search(text))
        has_rl_domain = "reinforcement learning" in domain or domain in {"rl", "navigation tasks"}
        has_context = any(term in text for term in cls.RL_CONTEXT_TERMS)
        is_background = (
            source_type in {"background", "generic_field"}
            or salience == "passing"
            or "introduction" in section
        )
        has_bio_background = any(term in text for term in cls.BIO_INSPIRATION_BACKGROUND_TERMS)

        if is_background and has_bio_background and not has_rl_anchor:
            return False
        if source_type == "background" and not (has_rl_anchor or has_rl_domain):
            return False

        return has_rl_anchor or (has_rl_domain and has_context) or (
            source_type in {"reviewed_method", "baseline"} and has_context
        )

    def _validate_existing_relation(
        self,
        relation: dict[str, Any],
        by_id: dict[str, dict[str, Any]],
    ) -> dict[str, Any] | None:
        if not isinstance(relation, dict):
            return None
        subject_id = str(relation.get("subject_id") or relation.get("subject") or "").strip()
        object_id = str(relation.get("object_id") or relation.get("object") or "").strip()
        if subject_id not in by_id or object_id not in by_id or subject_id == object_id:
            return None
        try:
            relation_type = self.resolver.ontology.validate_relation_type(relation.get("relation_type") or relation.get("type"))
        except ValueError:
            return None
        evidence = str(relation.get("evidence_span") or "").strip()
        if not evidence:
            return None
        return {
            "subject_id": subject_id,
            "relation_type": relation_type,
            "object_id": object_id,
            "evidence_span": evidence[:360],
            "section": str(relation.get("section") or ""),
            "confidence": float(relation.get("confidence") or 0.75),
            "source": str(
                relation.get("source")
                or (
                    "llm_relation"
                    if self._is_approved(by_id[subject_id]) and self._is_approved(by_id[object_id])
                    else "candidate_relation"
                )
            ),
            "review_status": (
                "approved"
                if self._is_approved(by_id[subject_id]) and self._is_approved(by_id[object_id])
                else "pending"
            ),
        }

    def _add_known_relation(
        self,
        relations: list[dict[str, Any]],
        by_label: dict[str, dict[str, Any]],
        entities: list[dict[str, Any]],
        subject_key: str,
        relation_type: str,
        object_key: str,
    ) -> None:
        subject = by_label.get(subject_key)
        object_entity = by_label.get(object_key)
        if subject and object_entity:
            review_status = "approved" if self._is_approved(subject) and self._is_approved(object_entity) else "pending"
            self._append_relation(
                relations,
                subject,
                relation_type,
                object_entity,
                self._evidence(subject, object_entity, relation_type, entities),
                review_status=review_status,
            )

    def _append_relation(
        self,
        relations: list[dict[str, Any]],
        subject: dict[str, Any],
        relation_type: str,
        object_entity: dict[str, Any],
        evidence: str,
        review_status: str = "approved",
    ) -> None:
        if subject.get("canonical_id") == object_entity.get("canonical_id"):
            return
        try:
            checked_type = self.resolver.ontology.validate_relation_type(relation_type)
        except ValueError:
            return
        if not evidence:
            return
        relations.append(
            {
                "subject_id": str(subject.get("canonical_id")),
                "relation_type": checked_type,
                "object_id": str(object_entity.get("canonical_id")),
                "evidence_span": evidence[:360],
                "section": str(subject.get("section") or object_entity.get("section") or ""),
                "confidence": 0.8 if review_status == "approved" else 0.65,
                "source": "deterministic_relation" if review_status == "approved" else "candidate_relation",
                "review_status": review_status,
            }
        )

    @staticmethod
    def _is_approved(entity: dict[str, Any]) -> bool:
        return bool(entity.get("canonical_id")) and str(entity.get("review_status") or "").lower() == "approved"

    @staticmethod
    def _is_reviewable_candidate(entity: dict[str, Any]) -> bool:
        if not bool(entity.get("canonical_id")):
            return False
        return str(entity.get("review_status") or "").lower() not in {"approved", "rejected"}

    @classmethod
    def _dedupe_relation_entities(cls, entities: list[dict[str, Any]]) -> list[dict[str, Any]]:
        by_id: dict[str, dict[str, Any]] = {}
        no_id: list[dict[str, Any]] = []
        for item in entities:
            canonical_id = str(item.get("canonical_id") or "")
            if not canonical_id:
                no_id.append(item)
                continue
            current = by_id.get(canonical_id)
            if current is None:
                by_id[canonical_id] = item
                continue
            if cls._is_approved(item) and not cls._is_approved(current):
                by_id[canonical_id] = item
                continue
            if cls._is_approved(item) == cls._is_approved(current):
                item_text = cls._entity_text(item)
                current_text = cls._entity_text(current)
                if len(item_text) > len(current_text):
                    by_id[canonical_id] = item
        return [*by_id.values(), *no_id]

    @classmethod
    def _index_by_label(cls, entities: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
        index: dict[str, dict[str, Any]] = {}
        for item in entities:
            for label in cls._entity_labels(item):
                key = cls._key(label)
                if not key:
                    continue
                current = index.get(key)
                if current is None or (not cls._is_approved(current) and cls._is_approved(item)):
                    index[key] = item
        return index

    @staticmethod
    def _relation_rank(relation: dict[str, Any]) -> tuple[int, int, float, int]:
        source_priority = {
            "deterministic_relation": 3,
            "llm_relation": 2,
            "candidate_relation": 1,
        }
        return (
            1 if str(relation.get("review_status") or "").lower() == "approved" else 0,
            source_priority.get(str(relation.get("source") or ""), 0),
            _coerce_float(relation.get("confidence"), 0.0),
            len(str(relation.get("evidence_span") or "")),
        )

    @staticmethod
    def _key(value: Any) -> str:
        return normalize_key(value)

    @classmethod
    def _evidence(
        cls,
        subject: dict[str, Any],
        object_entity: dict[str, Any],
        relation_type: str,
        entities: list[dict[str, Any]],
    ) -> str:
        candidates = [cls._entity_text(item) for item in entities]
        relation_terms = cls._relation_terms(relation_type, subject, object_entity)

        ranked: list[tuple[int, int, str]] = []
        for text in candidates:
            text_key = normalize_scientific_text(text).lower()
            subject_score = cls._entity_match_score(text_key, subject)
            object_score = cls._entity_match_score(text_key, object_entity)
            if not subject_score or not object_score:
                continue
            relation_score = 0
            if relation_terms:
                relation_score = 3 if any(term in text_key for term in relation_terms) else 0
            ranked.append((subject_score + object_score + relation_score, len(text), text))
        if ranked:
            ranked.sort(reverse=True)
            return ranked[0][2][:360]

        for item in (subject, object_entity):
            evidence = cls._entity_text(item)
            if evidence:
                return evidence[:360]
        return f"{subject.get('label')} {object_entity.get('label')}"

    @staticmethod
    def _entity_text(entity: dict[str, Any]) -> str:
        text = " ".join(
            str(entity.get(key) or "")
            for key in ("evidence_span", "context", "description")
        )
        return re.sub(r"\s+", " ", text).strip()

    @classmethod
    def _entity_match_score(cls, text_key: str, entity: dict[str, Any]) -> int:
        phrases = cls._evidence_phrase_terms(entity)
        if any(phrase in text_key for phrase in phrases):
            return 4
        canonical_key = normalize_key(entity.get("canonical_label") or entity.get("label"))
        if canonical_key in {"rewardshaping", "tdlearning", "modelbasedrl"}:
            return 0
        tokens = cls._evidence_token_terms(entity)
        if not tokens:
            return 0
        matches = sum(1 for token in tokens if token in text_key)
        required = 1 if len(tokens) == 1 else min(2, len(tokens))
        anchors = cls._evidence_anchor_terms(entity)
        if anchors and not any(anchor in text_key for anchor in anchors):
            return 0
        return 2 if matches >= required else 0

    @staticmethod
    def _entity_labels(entity: dict[str, Any]) -> list[str]:
        return [
            str(entity.get("canonical_label") or ""),
            str(entity.get("label") or ""),
            *[str(alias) for alias in (entity.get("aliases") or []) if alias],
        ]

    @classmethod
    def _evidence_phrase_terms(cls, entity: dict[str, Any]) -> list[str]:
        terms: list[str] = []
        for label in cls._entity_labels(entity):
            normalized = normalize_scientific_text(label).lower().strip()
            if not normalized:
                continue
            variants = {
                normalized,
                normalized.replace("-", " "),
                normalized.replace(" ", "-"),
            }
            terms.extend(term for term in variants if len(term) >= 4)
        canonical_key = normalize_key(entity.get("canonical_label") or entity.get("label"))
        if canonical_key == "valence":
            terms.extend(["valency", "valence"])
        if canonical_key == "boltzmannactionselection":
            terms.extend(["boltzmann action selection", "boltzmann", "beta", "β", "Î²"])
        if canonical_key == "rewardshaping":
            terms.extend(
                [
                    "reward shaping",
                    "reward modification",
                    "reward modulation",
                    "modify the reward",
                    "modify reward",
                    "modified reward",
                    "emotions to modify the reward",
                ]
            )
        if canonical_key == "tdlearning":
            terms.extend(["td learning", "temporal difference learning", "model-free rl", "model free rl"])
        if canonical_key == "modelbasedrl":
            terms.extend(["model-based rl", "model based rl", "model-based reinforcement learning"])
        return list(dict.fromkeys(terms))

    @classmethod
    def _evidence_token_terms(cls, entity: dict[str, Any]) -> list[str]:
        tokens: list[str] = []
        for label in cls._entity_labels(entity):
            words = [
                word.lower()
                for word in re.findall(r"[A-Za-z][A-Za-z0-9-]+", normalize_scientific_text(label))
            ]
            tokens.extend(
                word
                for word in words
                if len(word) >= 4 and word not in cls.GENERIC_EVIDENCE_WORDS
            )
        canonical_key = normalize_key(entity.get("canonical_label") or entity.get("label"))
        if canonical_key == "valence":
            tokens.append("valency")
        if canonical_key == "boltzmannactionselection":
            tokens.extend(["boltzmann", "beta"])
        return list(dict.fromkeys(tokens))

    @classmethod
    def _evidence_anchor_terms(cls, entity: dict[str, Any]) -> list[str]:
        anchors: list[str] = []
        for label in cls._entity_labels(entity):
            words = [
                word.lower()
                for word in re.findall(r"[A-Za-z][A-Za-z0-9-]+", normalize_scientific_text(label))
            ]
            for word in words:
                if len(word) >= 4 and word not in cls.GENERIC_EVIDENCE_WORDS:
                    anchors.append(word)
                    break
        return list(dict.fromkeys(anchors))

    @staticmethod
    def _evidence_terms(entity: dict[str, Any]) -> list[str]:
        labels = [
            str(entity.get("canonical_label") or ""),
            str(entity.get("label") or ""),
            *[str(alias) for alias in (entity.get("aliases") or []) if alias],
        ]
        terms: list[str] = []
        for label in labels:
            normalized = label.lower()
            if normalized:
                terms.append(normalized)
            words = [word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9-]+", label)]
            terms.extend(word for word in words if len(word) >= 4)
        canonical_key = normalize_key(entity.get("canonical_label") or entity.get("label"))
        if canonical_key == "valence":
            terms.append("valency")
        if canonical_key == "boltzmannactionselection":
            terms.extend(["boltzmann", "beta", "β"])
        return list(dict.fromkeys(terms))

    @staticmethod
    def _relation_terms(
        relation_type: str,
        subject: dict[str, Any],
        object_entity: dict[str, Any],
    ) -> list[str]:
        subject_key = normalize_key(subject.get("canonical_label") or subject.get("label"))
        object_key = normalize_key(object_entity.get("canonical_label") or object_entity.get("label"))
        if relation_type == "MODULATED_BY" and subject_key == "boltzmannactionselection" and object_key == "valence":
            return ["influenced", "modulated", "valency", "valence", "beta", "β"]
        if relation_type == "CORRESPONDS_TO":
            return ["connection", "correspond", "maps", "mapped"]
        if relation_type == "MEASURES":
            return ["measure", "derive", "distance"]
        if relation_type == "IMPLEMENTS":
            return ["implements", "update", "approximate", "value-function", "value function"]
        if relation_type == "ELICITS":
            return ["elicit", "elicits", "derive", "derives", "generate"]
        if relation_type == "USED_FOR":
            return ["used for", "improve", "improved", "learning efficiency", "drive", "guide"]
        if relation_type in {"CAUSES", "LEADS_TO"}:
            return [
                "affect",
                "cause",
                "change",
                "degradation",
                "impact",
                "induce",
                "lead",
                "risk",
                "shift",
            ]
        if relation_type == "MITIGATES":
            return [
                "counter",
                "ensure",
                "essential",
                "mitigate",
                "monitor",
                "prevent",
                "robust",
                "strategy",
                "validate",
            ]
        if relation_type == "PREVENTS":
            return ["avoid", "discontinuation", "failure", "prevent", "robust", "single point"]
        if relation_type == "AFFECTS":
            return ["affect", "consequence", "impact", "quality", "repercussion", "risk"]
        if relation_type == "BUILT_ON":
            return ["built", "built on", "based on", "framework", "simulation"]
        if relation_type == "PROVIDES":
            return ["provide", "provided", "integration", "module", "exposes", "interface"]
        if relation_type == "SUPPORTS":
            return ["support", "supports", "encoding", "exposes", "strategy"]
        if relation_type == "REPRODUCES":
            return ["reproduce", "reproduces", "replicate", "replicates", "benchmark", "implementation"]
        if relation_type == "EVALUATED_ON":
            return ["dataset", "evaluated", "trained", "training", "benchmark", "on"]
        if relation_type == "MORE_ROBUST_THAN":
            return ["robust", "more robust", "vulnerable", "whereas", "than", "perturbation"]
        return []


class ExtractionPipeline:
    """
    End-to-end pipeline: parse -> extract -> link entities.
    Supports configurable LLM providers for extraction.
    """

    def __init__(
        self,
        llm_router: LLMRouter,
        linker: EntityLinker | None = None,
        ontology: Ontology | None = None,
        embedding_engine: EmbeddingEngine | None = None,
    ) -> None:
        """
        Initialize pipeline.

        Args:
            llm_router: Configured LLMRouter for extraction
            linker: Optional EntityLinker for knowledge base enrichment
        """
        self.extractor = EntityExtractor(llm_router)
        if linker is not None:
            self.linker = linker
        else:
            resolver = CanonicalResolver(ontology=ontology, embedding_engine=embedding_engine)
            self.linker = EntityLinker(resolver=resolver)

    def process(
        self,
        paper_id: str,
        paper_text: str,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
        link_concepts: bool = True,
    ) -> ExtractionResult:
        """
        Process paper: extract entities and optionally link to knowledge bases.

        Args:
            paper_id: Unique paper identifier
            paper_text: Full paper text
            provider: Optional LLM provider override
            overrides: Optional LLM settings overrides
            link_concepts: Whether to enrich with knowledge base links

        Returns:
            ExtractionResult with extracted and optionally linked entities
        """
        # Extract entities using LLM
        extraction = self.extractor.extract(
            paper_id, paper_text, provider=provider, overrides=overrides
        )

        extraction_failed = extraction.raw_response.lower().startswith("extraction failed:")

        # Link to knowledge bases if requested and extraction produced concepts.
        # Successful extractions keep raw_response for debugging, so raw_response
        # itself is not an error signal.
        if link_concepts and not extraction_failed:
            extraction = self.linker.enrich_extraction(extraction)

        return extraction
