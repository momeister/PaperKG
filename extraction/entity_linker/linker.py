"""EntityLinker: enrich extracted entities and align them with the KG layers."""
from __future__ import annotations

import json
from typing import Any

from extraction.ontology import CanonicalResolver, stable_canonical_id
from extraction.entity_extractor import ExtractionResult
from extraction.entity_linker.relation_extractor import ControlledRelationExtractor
from extraction.entity_linker.linker_dedupe import LinkerDedupeMixin
from extraction.entity_linker.linker_kg_layers import LinkerKgLayersMixin
from extraction.entity_linker.linker_promotion import LinkerPromotionMixin
from extraction.entity_linker.strategies import (
    ConceptLinkageStrategy,
    OpenAlexLinkageStrategy,
)


class EntityLinker(LinkerDedupeMixin, LinkerKgLayersMixin, LinkerPromotionMixin):
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
