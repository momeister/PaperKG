"""EntityLinker: KG-Layer-Annotation und Relations-Ausrichtung. (Mixin)

Split out of extraction/entity_linker/linker.py. Behaviour unchanged.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from extraction.text_normalization import normalize_key
from extraction.entity_linker.strategies import _coerce_float

if TYPE_CHECKING:
    from extraction.entity_linker.linker import EntityLinker

    _Base = EntityLinker
else:
    _Base = object


class LinkerKgLayersMixin(_Base):
    """KG-Layer-Annotation und Relations-Ausrichtung."""

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
