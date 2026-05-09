from __future__ import annotations

import json
from typing import Any

from extraction.embedding_engine import EmbeddingEngine
from extraction.ontology import CanonicalResolver, Ontology, stable_canonical_id
from extraction.text_normalization import normalize_key

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
        enriched_concept_candidates = [
            self._enrich_entity(candidate, default_entity_type="DomainConcept")
            for candidate in extraction.concept_candidates
        ]
        enriched_method_candidates = [
            self._enrich_entity(candidate, default_entity_type="Algorithm")
            for candidate in extraction.method_candidates
        ]
        enriched_concepts, enriched_concept_candidates = self._promote_exact_review_candidates(
            extraction.paper_type,
            enriched_concepts,
            enriched_concept_candidates,
        )

        enriched_concepts, enriched_methods = self._dedupe_graph_entities(
            enriched_concepts,
            enriched_methods,
        )
        relations = ControlledRelationExtractor(self.resolver).extract(
            enriched_concepts,
            enriched_methods,
            extraction.relations,
        )

        enriched_result = ExtractionResult(
            paper_id=extraction.paper_id,
            paper_type=extraction.paper_type,
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
        linked = self.strategy.link(item) if default_entity_type == "DomainConcept" else None
        if linked:
            item = dict(linked)
        return self.resolver.resolve(item)

    @staticmethod
    def _promote_exact_review_candidates(
        paper_type: str,
        concepts: list[dict[str, Any]],
        concept_candidates: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        """Promote high-confidence ontology-backed survey candidates lost to partial JSON."""
        if paper_type != "survey":
            return concepts, concept_candidates
        existing_ids = {str(item.get("canonical_id")) for item in concepts if item.get("canonical_id")}
        promotable_types = {
            "Theory",
            "Metric",
            "DomainConcept",
            "ApplicationSetting",
            "ModelArchitecture",
            "System",
            "Phenomenon",
        }
        promoted: list[dict[str, Any]] = []
        remaining: list[dict[str, Any]] = []
        for candidate in concept_candidates:
            canonical_id = str(candidate.get("canonical_id") or "")
            match = candidate.get("canonical_match") or {}
            entity_type = str(candidate.get("entity_type") or "")
            confidence = _coerce_float(candidate.get("confidence"), 0.0)
            mention_count = int(_coerce_float(candidate.get("mention_count"), 0.0))
            salience = str(candidate.get("salience") or "").lower()
            should_promote = (
                canonical_id
                and canonical_id not in existing_ids
                and match.get("match_type") == "exact_alias"
                and entity_type in promotable_types
                and confidence >= 0.70
                and (mention_count >= 2 or salience in {"central", "supporting"})
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
    def _dedupe_graph_entities(
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        by_id: dict[str, tuple[str, dict[str, Any]]] = {}
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
                canonical_id = str(item.get("canonical_id") or "").strip()
                if not canonical_id:
                    continue
                current = by_id.get(canonical_id)
                if current is None:
                    enriched = dict(item)
                    enriched["extracted_roles"] = sorted({role, *(enriched.get("extracted_roles") or [])})
                    by_id[canonical_id] = (role, enriched)
                    continue
                existing_role, existing = current
                entity_type = str(item.get("entity_type") or "")
                preferred_role = "concept" if entity_type in concept_like else "method"
                if preferred_role == role and preferred_role != existing_role:
                    by_id[canonical_id] = (role, merge_entity(item, existing, existing_role))
                else:
                    by_id[canonical_id] = (existing_role, merge_entity(existing, item, role))

        kept_concepts = [item for role, item in by_id.values() if role == "concept"]
        kept_methods = [item for role, item in by_id.values() if role == "method"]
        return kept_concepts, kept_methods

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
            "extraction_mode": extraction.extraction_mode,
        }
        payload.update(extraction.extraction_diagnostics)
        return json.dumps(payload, indent=2, ensure_ascii=False)


class ControlledRelationExtractor:
    """Build controlled, evidence-carrying relations between canonical entities."""

    def __init__(self, resolver: CanonicalResolver) -> None:
        self.resolver = resolver

    def extract(
        self,
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
        existing_relations: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        entities = [item for item in [*concepts, *methods] if self._is_approved(item)]
        by_label = {self._key(item.get("canonical_label") or item.get("label")): item for item in entities}
        by_id = {str(item.get("canonical_id")): item for item in entities if item.get("canonical_id")}
        relations: list[dict[str, Any]] = []

        for relation in existing_relations or []:
            clean = self._validate_existing_relation(relation, by_id)
            if clean:
                relations.append(clean)

        self._add_known_relation(relations, by_label, "appraisaldimensions", "RELATED_TO", "appraisaltheory")
        self._add_known_relation(relations, by_label, "rewardshaping", "USES", "rewardfunction")
        self._add_known_relation(relations, by_label, "kldivergence", "MEASURES", "modeluncertainty")
        self._add_known_relation(relations, by_label, "deepreinforcementlearning", "IS_A", "reinforcementlearning")
        self._add_known_relation(relations, by_label, "motivatedreinforcementlearning", "IS_A", "reinforcementlearning")
        self._add_known_relation(relations, by_label, "homeostaticrewardmodification", "IS_A", "rewardshaping")
        self._add_known_relation(relations, by_label, "appraisalbasedrewardmodification", "IS_A", "rewardshaping")
        self._add_known_relation(relations, by_label, "modelbasedrl", "IS_A", "reinforcementlearning")
        self._add_known_relation(relations, by_label, "tdlearning", "IS_A", "reinforcementlearning")

        reinforcement_learning = by_label.get("reinforcementlearning")
        if reinforcement_learning:
            for item in entities:
                if str(item.get("entity_type")) == "Algorithm" and item is not reinforcement_learning:
                    self._append_relation(
                        relations,
                        item,
                        "IS_A",
                        reinforcement_learning,
                        self._evidence(item, reinforcement_learning),
                    )

        deduped: list[dict[str, Any]] = []
        seen: set[tuple[str, str, str]] = set()
        for relation in relations:
            key = (
                str(relation.get("subject_id")),
                str(relation.get("relation_type")),
                str(relation.get("object_id")),
            )
            if key in seen:
                continue
            seen.add(key)
            deduped.append(relation)
        return deduped

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
            "source": str(relation.get("source") or "llm_relation"),
            "review_status": "approved",
        }

    def _add_known_relation(
        self,
        relations: list[dict[str, Any]],
        by_label: dict[str, dict[str, Any]],
        subject_key: str,
        relation_type: str,
        object_key: str,
    ) -> None:
        subject = by_label.get(subject_key)
        object_entity = by_label.get(object_key)
        if subject and object_entity:
            self._append_relation(relations, subject, relation_type, object_entity, self._evidence(subject, object_entity))

    def _append_relation(
        self,
        relations: list[dict[str, Any]],
        subject: dict[str, Any],
        relation_type: str,
        object_entity: dict[str, Any],
        evidence: str,
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
                "confidence": 0.8,
                "source": "deterministic_relation",
                "review_status": "approved",
            }
        )

    @staticmethod
    def _is_approved(entity: dict[str, Any]) -> bool:
        return bool(entity.get("canonical_id")) and str(entity.get("review_status") or "").lower() == "approved"

    @staticmethod
    def _key(value: Any) -> str:
        return normalize_key(value)

    @staticmethod
    def _evidence(subject: dict[str, Any], object_entity: dict[str, Any]) -> str:
        for item in (subject, object_entity):
            evidence = str(item.get("evidence_span") or item.get("context") or item.get("description") or "").strip()
            if evidence:
                return evidence
        return f"{subject.get('label')} {object_entity.get('label')}"


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
