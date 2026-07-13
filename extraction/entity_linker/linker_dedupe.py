"""EntityLinker: Methoden-/Entitaeten-Dedupe und Shadow-Filter. (Mixin)

Split out of extraction/entity_linker/linker.py. Behaviour unchanged.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from extraction.ontology import stable_canonical_id
from extraction.text_normalization import normalize_key
from extraction.entity_linker.strategies import _coerce_float

if TYPE_CHECKING:
    from extraction.entity_linker.linker import EntityLinker

    _Base = EntityLinker
else:
    _Base = object


class LinkerDedupeMixin(_Base):
    """Methoden-/Entitaeten-Dedupe und Shadow-Filter."""

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

    @classmethod
    def _merge_method_entities(cls, 
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
        evidence = cls._best_evidence_span([output, secondary])
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
