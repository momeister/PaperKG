"""EntityExtractor: Kandidaten-/Noise-Filter, Akzeptanz und Coercion-Helfer. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from extraction.ontology import stable_canonical_id
from extraction.text_normalization import normalize_key

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class CandidatesMixin(_Base):
    """Kandidaten-/Noise-Filter, Akzeptanz und Coercion-Helfer."""

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

    @classmethod
    def _append_alias(cls, concept: dict[str, Any], alias: str) -> None:
        aliases = concept.get("aliases")
        if not isinstance(aliases, list):
            aliases = []
            concept["aliases"] = aliases
        clean_alias = cls._clean_label(alias)
        seen = {cls._normalize_label(str(item)) for item in aliases}
        if clean_alias and cls._normalize_label(clean_alias) not in seen:
            aliases.append(clean_alias)

    @staticmethod
    def _normalize_label(label: str) -> str:
        """Normalize concept labels for duplicate checks."""
        return normalize_key(label)

    @staticmethod
    def _normalize_extraction_mode(value: Any) -> str:
        mode = str(value or "quality").strip().lower()
        return mode if mode in {"quality", "quick"} else "quality"

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

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
