"""EntityLinker: enrich extracted entities and align them with the KG layers."""
from __future__ import annotations

import json
import re
from typing import Any

from extraction.ontology import CanonicalResolver, stable_canonical_id
from extraction.text_normalization import normalize_key, normalize_scientific_text
from extraction.entity_extractor import ExtractionResult
from extraction.entity_linker.relation_extractor import ControlledRelationExtractor
from extraction.entity_linker.strategies import (
    ConceptLinkageStrategy,
    OpenAlexLinkageStrategy,
    _coerce_float,
)


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
    QML_CORE_KEYS = {
        "adaptivestateinjection",
        "amplitudeencoding",
        "angleencoding",
        "distributedquantumneuralnetwork",
        "fidelitykernel",
        "fockspace",
        "linearopticalcircuits",
        "merlin",
        "photonicquantumcomputing",
        "photonicquantummachinelearning",
        "quantumbridge",
        "quantumconvolutionalneuralnetwork",
        "quantumgenerativeadversarialnetwork",
        "quantumlayer",
        "quantumlongshorttermmemory",
        "quantummachinelearning",
        "quantummemristor",
        "quantumrelationalknowledgedistillation",
        "quantumselfsupervisedlearning",
        "reservoircomputing",
        "stronglinearopticalsimulation",
        "variationalquantumcircuit",
    }
    BENCHMARK_CORE_KEYS = {
        "accuracy",
        "adaboost",
        "bert",
        "bilstm",
        "clstm",
        "cnn",
        "combinedcorpus",
        "convhan",
        "datasetbias",
        "decisiontree",
        "distilbert",
        "electra",
        "elmo",
        "fakeorrealnews",
        "fakenewsdetection",
        "f1score",
        "han",
        "hierarchicalattentionnetwork",
        "knn",
        "liar",
        "logisticregression",
        "lstm",
        "multinomialnaivebayes",
        "naivebayes",
        "precision",
        "pretrainedlanguagemodels",
        "recall",
        "roberta",
        "svm",
    }
    THEORETICAL_CORE_KEYS = {
        "bodyschema",
        "cartesiantheater",
        "carthesiantheater",
        "crossdomainmapping",
        "crossphasemodulation",
        "directedacyclicgraph",
        "einselection",
        "envariance",
        "featurebindinghypothesis",
        "gainfields",
        "inversekinematicalgorithm",
        "mirrorneurons",
        "mirrorneuronsystem",
        "neuroimaging",
        "paralleldistributedprocessing",
        "pointerstates",
        "populationvector",
        "quantummutualinformation",
        "quantumtemporalimaging",
        "referencepictureselection",
        "schmidtdecomposition",
        "schrdingerpicture",
        "schrodingerpicture",
        "synesthesia",
        "temporalentanglement",
        "tensorialscheme",
        "thalamocorticalsystem",
        "twophotonvectorsoliton",
        "vonneumannentropy",
        "wignerfunction",
    }
    MEDICAL_IMAGING_CORE_KEYS = {
        "3dunet",
        "adamchallenge",
        "adamdataset",
        "computeraideddetection",
        "falsepositiverate",
        "mcnemarstest",
        "n4biasfieldcorrection",
        "phasesscore",
        "satisfactionofsearch",
        "skullstripping",
        "tofmra",
        "unrupturedintracranialaneurysm",
        "wilcoxonsignedranktest",
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
        relations = self._align_relations_with_kg_layers(
            relations,
            enriched_concepts,
            enriched_methods,
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
            is_core_key = (
                key in cls.CORE_KG_KEYS
                or key in cls.QML_CORE_KEYS
                or key in cls.BENCHMARK_CORE_KEYS
                or key in cls.THEORETICAL_CORE_KEYS
                or key in cls.MEDICAL_IMAGING_CORE_KEYS
            )
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
                canonical_key not in EntityLinker.DETAIL_ONLY_KEYS
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

    @staticmethod
    def _align_relations_with_kg_layers(
        relations: list[dict[str, Any]],
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        kg_entity_ids = {
            str(item.get("canonical_id"))
            for item in [*concepts, *methods]
            if isinstance(item, dict)
            and item.get("canonical_id")
            and item.get("accepted_for_kg_write") is True
            and str(item.get("review_status") or "").lower() == "approved"
        }
        output: list[dict[str, Any]] = []
        for relation in relations:
            if not isinstance(relation, dict):
                continue
            item = dict(relation)
            subject_id = str(item.get("subject_id") or "")
            object_id = str(item.get("object_id") or "")
            if (
                str(item.get("review_status") or "").lower() == "approved"
                and (subject_id not in kg_entity_ids or object_id not in kg_entity_ids)
            ):
                item["review_status"] = "pending"
                item["source"] = "candidate_relation"
                item["kg_block_reason"] = "relation_endpoint_not_kg_writeable"
                item["confidence"] = min(_coerce_float(item.get("confidence"), 0.65), 0.65)
            output.append(item)
        return output

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


