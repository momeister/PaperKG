"""ControlledRelationExtractor: derive controlled relations between linked entities."""
from __future__ import annotations

import re
from typing import Any

from extraction.ontology import CanonicalResolver
from extraction.text_normalization import normalize_key, normalize_scientific_text
from extraction.entity_linker.strategies import _coerce_float


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
        ("merlin", "REPRODUCES", "quantumconvolutionalneuralnetwork"),
        ("merlin", "REPRODUCES", "quantumgenerativeadversarialnetwork"),
        ("merlin", "REPRODUCES", "quantumrelationalknowledgedistillation"),
        ("merlin", "REPRODUCES", "quantumselfsupervisedlearning"),
        ("quantumconvolutionalneuralnetwork", "USES", "adaptivestateinjection"),
        ("quantumgenerativeadversarialnetwork", "EVALUATED_ON", "mnist"),
        ("quantumllmfinetuning", "EVALUATED_ON", "sst2"),
        ("angleencoding", "MORE_ROBUST_THAN", "amplitudeencoding"),
        ("bert", "IS_A", "pretrainedlanguagemodels"),
        ("roberta", "IS_A", "pretrainedlanguagemodels"),
        ("distilbert", "IS_A", "pretrainedlanguagemodels"),
        ("electra", "IS_A", "pretrainedlanguagemodels"),
        ("elmo", "IS_A", "pretrainedlanguagemodels"),
        ("distilbert", "DERIVED_FROM", "bert"),
        ("distilbert", "USES", "knowledgedistillation"),
        ("bilstm", "IS_A", "lstm"),
        ("han", "IS_A", "hierarchicalattentionnetwork"),
        ("convhan", "USES", "hierarchicalattentionnetwork"),
        ("bert", "EVALUATED_ON", "liar"),
        ("bert", "EVALUATED_ON", "fakeorrealnews"),
        ("bert", "EVALUATED_ON", "combinedcorpus"),
        ("roberta", "EVALUATED_ON", "liar"),
        ("roberta", "EVALUATED_ON", "fakeorrealnews"),
        ("roberta", "EVALUATED_ON", "combinedcorpus"),
        ("distilbert", "EVALUATED_ON", "liar"),
        ("distilbert", "EVALUATED_ON", "fakeorrealnews"),
        ("distilbert", "EVALUATED_ON", "combinedcorpus"),
        ("electra", "EVALUATED_ON", "liar"),
        ("electra", "EVALUATED_ON", "fakeorrealnews"),
        ("electra", "EVALUATED_ON", "combinedcorpus"),
        ("elmo", "EVALUATED_ON", "liar"),
        ("elmo", "EVALUATED_ON", "fakeorrealnews"),
        ("elmo", "EVALUATED_ON", "combinedcorpus"),
        ("bilstm", "EVALUATED_ON", "liar"),
        ("bilstm", "EVALUATED_ON", "fakeorrealnews"),
        ("bilstm", "EVALUATED_ON", "combinedcorpus"),
        ("clstm", "EVALUATED_ON", "liar"),
        ("clstm", "EVALUATED_ON", "fakeorrealnews"),
        ("clstm", "EVALUATED_ON", "combinedcorpus"),
        ("cnn", "EVALUATED_ON", "liar"),
        ("cnn", "EVALUATED_ON", "fakeorrealnews"),
        ("cnn", "EVALUATED_ON", "combinedcorpus"),
        ("lstm", "EVALUATED_ON", "liar"),
        ("lstm", "EVALUATED_ON", "fakeorrealnews"),
        ("lstm", "EVALUATED_ON", "combinedcorpus"),
        ("han", "EVALUATED_ON", "liar"),
        ("han", "EVALUATED_ON", "fakeorrealnews"),
        ("han", "EVALUATED_ON", "combinedcorpus"),
        ("convhan", "EVALUATED_ON", "liar"),
        ("convhan", "EVALUATED_ON", "fakeorrealnews"),
        ("convhan", "EVALUATED_ON", "combinedcorpus"),
        ("svm", "EVALUATED_ON", "liar"),
        ("svm", "EVALUATED_ON", "fakeorrealnews"),
        ("svm", "EVALUATED_ON", "combinedcorpus"),
        ("naivebayes", "EVALUATED_ON", "liar"),
        ("naivebayes", "EVALUATED_ON", "fakeorrealnews"),
        ("naivebayes", "EVALUATED_ON", "combinedcorpus"),
        ("adaboost", "EVALUATED_ON", "liar"),
        ("adaboost", "EVALUATED_ON", "fakeorrealnews"),
        ("adaboost", "EVALUATED_ON", "combinedcorpus"),
        ("vonneumannentropy", "USED_IN", "quantummutualinformation"),
        ("schmidtdecomposition", "USED_IN", "envariance"),
        ("envariance", "IMPLIES", "einselection"),
        ("einselection", "IMPLIES", "pointerstates"),
        ("crossphasemodulation", "USED_FOR", "temporalentanglement"),
        ("twophotonvectorsoliton", "CAUSES", "temporalentanglement"),
        ("quantumtemporalimaging", "USES", "temporalentanglement"),
        ("mirrorneurons", "PART_OF", "mirrorneuronsystem"),
        ("directedacyclicgraph", "USED_IN", "referencepictureselection"),
        ("synesthesia", "RELATED_TO", "crossdomainmapping"),
        ("computeraideddetection", "USES", "3dunet"),
        ("computeraideddetection", "USED_FOR", "unrupturedintracranialaneurysm"),
        ("computeraideddetection", "EVALUATED_ON", "tofmra"),
        ("3dunet", "EVALUATED_ON", "adamdataset"),
        ("3dunet", "USED_FOR", "unrupturedintracranialaneurysm"),
        ("tofmra", "USED_FOR", "unrupturedintracranialaneurysm"),
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
        "network",
        "networks",
        "neural",
        "quantum",
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

        self._add_generic_context_relations(relations, entities)

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

    GENERIC_RELATION_LIMIT = 48
    GENERIC_METHOD_SUBJECT_TYPES = {"Algorithm", "MethodFamily", "ModelArchitecture", "System", "Task"}
    GENERIC_DATA_OBJECT_TYPES = {"Dataset", "Benchmark"}
    GENERIC_USED_FOR_OBJECT_TYPES = {"Task", "Phenomenon", "ApplicationSetting", "DomainConcept"}
    GENERIC_USES_OBJECT_TYPES = {"Algorithm", "MethodFamily", "ModelArchitecture"}
    GENERIC_MEASURE_OBJECT_TYPES = {"Task", "Phenomenon", "ApplicationSetting", "DomainConcept"}
    GENERIC_EVALUATED_TERMS = (
        "evaluated",
        "evaluation",
        "benchmark",
        "benchmarked",
        "dataset",
        "trained",
        "training",
        "tested",
        "validation",
        "study",
    )
    GENERIC_USED_FOR_TERMS = (
        "for",
        "used for",
        "applied to",
        "detect",
        "detection",
        "classify",
        "classification",
        "predict",
        "prediction",
        "support",
        "decision",
        "control",
        "design",
        "validation",
        "monitoring",
        "generation",
        "estimate",
        "analyze",
        "analysis",
        "reduce",
        "reducing",
    )
    GENERIC_USES_TERMS = (
        "uses",
        "using",
        "based on",
        "built on",
        "powered by",
        "implemented with",
        "integrates",
        "applies",
        "leverages",
        "incorporates",
        "llm-based",
        "ai-based",
    )
    GENERIC_MEASURES_TERMS = (
        "measure",
        "measures",
        "metric",
        "score",
        "rate",
        "assess",
        "assesses",
        "evaluate",
        "quantify",
        "risk",
        "error",
    )

    def _add_generic_context_relations(
        self,
        relations: list[dict[str, Any]],
        entities: list[dict[str, Any]],
    ) -> None:
        """Infer conservative relations from entity-local evidence text."""
        added = 0
        for subject in entities:
            for object_entity in entities:
                if subject is object_entity:
                    continue
                if not (self._is_approved(subject) and self._is_approved(object_entity)):
                    continue
                relation_type = self._infer_generic_relation_type(subject, object_entity)
                if not relation_type:
                    continue
                evidence = self._generic_relation_evidence(subject, object_entity, relation_type)
                if not evidence:
                    continue
                review_status = "approved" if self._is_approved(subject) and self._is_approved(object_entity) else "pending"
                self._append_relation(
                    relations,
                    subject,
                    relation_type,
                    object_entity,
                    evidence,
                    review_status=review_status,
                )
                added += 1
                if added >= self.GENERIC_RELATION_LIMIT:
                    return

    @classmethod
    def _infer_generic_relation_type(
        cls,
        subject: dict[str, Any],
        object_entity: dict[str, Any],
    ) -> str | None:
        subject_type = str(subject.get("entity_type") or "")
        object_type = str(object_entity.get("entity_type") or "")
        if subject_type in cls.GENERIC_METHOD_SUBJECT_TYPES and object_type in cls.GENERIC_DATA_OBJECT_TYPES:
            if cls._context_matches_entity(subject, object_entity, cls.GENERIC_EVALUATED_TERMS) or cls._context_matches_entity(
                object_entity, subject, cls.GENERIC_EVALUATED_TERMS
            ):
                return "EVALUATED_ON"
        if subject_type in cls.GENERIC_METHOD_SUBJECT_TYPES and object_type in cls.GENERIC_USED_FOR_OBJECT_TYPES:
            if cls._context_matches_entity(subject, object_entity, cls.GENERIC_USED_FOR_TERMS):
                return "USED_FOR"
        if subject_type in {"System", "ModelArchitecture", "MethodFamily"} and object_type in cls.GENERIC_USES_OBJECT_TYPES:
            if cls._context_matches_entity(subject, object_entity, cls.GENERIC_USES_TERMS):
                return "USES"
        if subject_type == "Metric" and object_type in cls.GENERIC_MEASURE_OBJECT_TYPES:
            if cls._context_matches_entity(subject, object_entity, cls.GENERIC_MEASURES_TERMS):
                return "MEASURES"
        return None

    @classmethod
    def _generic_relation_evidence(
        cls,
        subject: dict[str, Any],
        object_entity: dict[str, Any],
        relation_type: str,
    ) -> str:
        terms = cls._relation_terms(relation_type, subject, object_entity)
        if relation_type == "EVALUATED_ON":
            terms = [*terms, *cls.GENERIC_EVALUATED_TERMS]
        elif relation_type == "USED_FOR":
            terms = [*terms, *cls.GENERIC_USED_FOR_TERMS]
        elif relation_type == "USES":
            terms = [*terms, *cls.GENERIC_USES_TERMS]
        elif relation_type == "MEASURES":
            terms = [*terms, *cls.GENERIC_MEASURES_TERMS]
        for carrier, target in ((subject, object_entity), (object_entity, subject)):
            evidence = cls._context_matches_entity(carrier, target, terms)
            if evidence:
                return evidence
        return ""

    @classmethod
    def _context_matches_entity(
        cls,
        carrier: dict[str, Any],
        target: dict[str, Any],
        relation_terms: tuple[str, ...] | list[str],
    ) -> str:
        evidence = cls._entity_text(carrier)
        text_key = normalize_scientific_text(evidence).lower()
        if not text_key or not cls._entity_match_score(text_key, target):
            return ""
        if relation_terms and not any(str(term).lower() in text_key for term in relation_terms):
            return ""
        return evidence[:360]

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

        fallback_items = (
            (object_entity, subject)
            if relation_type in {"REPRODUCES", "EVALUATED_ON"}
            else (subject, object_entity)
        )
        for item in fallback_items:
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
        if canonical_key == "largelanguagemodel":
            terms.extend(["large language model", "large language models", "llm", "llms", "llm-based"])
        if canonical_key == "clinicaldecisionsupport":
            terms.extend(["clinical decision support", "cds", "cds systems"])
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
        if canonical_key == "largelanguagemodel":
            tokens.extend(["llm", "llms"])
        if canonical_key == "clinicaldecisionsupport":
            tokens.append("cds")
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
        if relation_type == "IMPLIES":
            return ["implies", "imply", "entails", "enables", "therefore", "superselection", "pointer"]
        if relation_type == "ELICITS":
            return ["elicit", "elicits", "derive", "derives", "generate"]
        if relation_type == "USED_FOR":
            return ["used for", "improve", "improved", "learning efficiency", "drive", "guide"]
        if relation_type == "USED_IN":
            return ["used in", "basis", "defined", "definition", "compute", "computed", "framework"]
        if relation_type == "PART_OF":
            return ["part of", "located", "system", "component", "subdivision"]
        if relation_type in {"CAUSES", "LEADS_TO"}:
            return [
                "affect",
                "cause",
                "change",
                "degradation",
                "generate",
                "generates",
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
        if relation_type == "USES":
            return ["use", "uses", "using", "with", "based on"]
        if relation_type == "REPRODUCES":
            return ["reproduce", "reproduces", "replicate", "replicates", "benchmark", "implementation"]
        if relation_type == "EVALUATED_ON":
            return ["dataset", "evaluated", "trained", "training", "benchmark", "on"]
        if relation_type == "DERIVED_FROM":
            return ["derived", "distilled", "distillation", "version", "from"]
        if relation_type == "OUTPERFORMS":
            return ["outperform", "outperforms", "better", "best", "higher", "improve"]
        if relation_type == "MORE_ROBUST_THAN":
            return ["robust", "more robust", "vulnerable", "whereas", "than", "perturbation"]
        if relation_type == "RELATED_TO":
            return ["related", "mapping", "cross-domain", "cross domain", "cross-modal", "analogy"]
        return []


