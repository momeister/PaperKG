from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from extraction.embedding_engine import EmbeddingEngine
from extraction.text_normalization import normalize_key, normalize_scientific_text, slugify_label


DEFAULT_ONTOLOGY_PATH = Path("ontology.yaml")


def normalize_label(label: str) -> str:
    """Normalize labels for deterministic canonical lookup."""
    return normalize_key(label)


def stable_canonical_id(label: str, prefix: str = "concept") -> str:
    slug = slugify_label(label)
    if slug:
        return f"{prefix}:{slug[:96]}"
    digest = hashlib.sha1(normalize_scientific_text(label).encode("utf-8")).hexdigest()[:16]
    return f"{prefix}:{digest}"


@dataclass(frozen=True)
class OntologySeed:
    label: str
    entity_type: str
    aliases: tuple[str, ...] = ()
    domain: str = ""
    canonical_id: str = ""

    def all_labels(self) -> tuple[str, ...]:
        return (self.label, *self.aliases)


@dataclass
class Ontology:
    version: int = 1
    entity_types: set[str] = field(default_factory=set)
    relation_types: set[str] = field(default_factory=set)
    seeds: list[OntologySeed] = field(default_factory=list)
    policy: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_file(cls, path: str | Path = DEFAULT_ONTOLOGY_PATH) -> "Ontology":
        ontology_path = Path(path)
        if not ontology_path.exists():
            return cls.default()
        data = yaml.safe_load(ontology_path.read_text(encoding="utf-8")) or {}
        return cls.from_dict(data)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Ontology":
        entity_types = {str(item) for item in data.get("entity_types") or []}
        relation_types = {str(item) for item in data.get("relation_types") or []}
        seeds: list[OntologySeed] = []
        for raw in data.get("seed_concepts") or []:
            if not isinstance(raw, dict):
                continue
            label = normalize_scientific_text(raw.get("label") or "").strip()
            entity_type = str(raw.get("entity_type") or "DomainConcept").strip()
            if not label:
                continue
            aliases = tuple(
                normalize_scientific_text(alias).strip()
                for alias in raw.get("aliases") or []
                if str(alias).strip()
            )
            seeds.append(
                OntologySeed(
                    label=label,
                    entity_type=entity_type,
                    aliases=aliases,
                    domain=str(raw.get("domain") or "").strip(),
                    canonical_id=str(raw.get("canonical_id") or stable_canonical_id(label)),
                )
            )
        return cls(
            version=int(data.get("version") or 1),
            entity_types=entity_types,
            relation_types=relation_types,
            seeds=seeds,
            policy=dict(data.get("policy") or {}),
        )

    @classmethod
    def default(cls) -> "Ontology":
        entity_types = {
            "Algorithm",
            "Theory",
            "MethodFamily",
            "Metric",
            "Dataset",
            "Benchmark",
            "DomainConcept",
            "ApplicationSetting",
            "ModelArchitecture",
            "System",
            "Phenomenon",
            "Task",
        }
        relation_types = {
            "IS_A",
            "USES",
            "USED_IN",
            "USED_FOR",
            "AFFECTS",
            "CAUSES",
            "LEADS_TO",
            "MITIGATES",
            "PREVENTS",
            "GROUPED_WITH_IN_SURVEY",
            "MAPPED_TO_IN_TAXONOMY",
            "IMPLEMENTS",
            "ELICITS",
            "PART_OF",
            "EXTENDS",
            "MODULATED_BY",
            "CORRESPONDS_TO",
            "EVALUATED_ON",
            "OUTPERFORMS",
            "DERIVED_FROM",
            "IMPLIES",
            "BUILT_ON",
            "PROVIDES",
            "SUPPORTS",
            "REPRODUCES",
            "MORE_ROBUST_THAN",
            "MEASURES",
            "PROPOSED_BY",
            "ALIAS_OF",
            "RELATED_TO",
            "CONTRADICTS",
        }
        return cls(entity_types=entity_types, relation_types=relation_types)

    def validate_entity_type(self, value: str | None) -> str:
        candidate = str(value or "").strip()
        return candidate if candidate in self.entity_types else "DomainConcept"

    def validate_relation_type(self, value: str | None) -> str:
        candidate = str(value or "").strip().upper()
        if candidate not in self.relation_types:
            raise ValueError(f"Unsupported relation type: {value}")
        return candidate

    def seed_by_label(self) -> dict[str, OntologySeed]:
        index: dict[str, OntologySeed] = {}
        for seed in self.seeds:
            for label in seed.all_labels():
                index[normalize_label(label)] = seed
        return index


@dataclass(frozen=True)
class CanonicalMatch:
    canonical_id: str
    canonical_label: str
    entity_type: str
    domain: str = ""
    match_type: str = "none"
    score: float = 0.0
    aliases: tuple[str, ...] = ()


class CanonicalResolver:
    """Resolve extracted labels to controlled ontology/vocabulary concepts."""

    def __init__(
        self,
        ontology: Ontology | None = None,
        embedding_engine: EmbeddingEngine | None = None,
        vocabulary_entries: dict[str, Any] | None = None,
    ) -> None:
        self.ontology = ontology or Ontology.from_file()
        self.embedding_engine = embedding_engine
        self.vocabulary_entries = vocabulary_entries or {}
        self._seed_index = self.ontology.seed_by_label()
        self._canonical_records = self._build_canonical_records()

    @property
    def degraded_similarity(self) -> bool:
        return bool(
            self.embedding_engine is None
            or getattr(self.embedding_engine, "backend", "hash-fallback") != "sentence-transformers"
        )

    def resolve(self, entity: dict[str, Any]) -> dict[str, Any]:
        item = dict(entity)
        label = normalize_scientific_text(item.get("label") or "").strip()
        item["label"] = label
        entity_type = self.ontology.validate_entity_type(item.get("entity_type"))
        item["entity_type"] = entity_type
        item.setdefault("evidence_span", self._evidence_span(item))
        item.setdefault("section", self._section_from_context(str(item.get("context") or item.get("description") or "")))

        match = self.find_match(label, entity_type=entity_type)
        confidence = self._coerce_float(item.get("confidence"), 0.0)
        if match is not None:
            item["canonical_id"] = match.canonical_id
            item["canonical_label"] = match.canonical_label
            item["entity_type"] = self.ontology.validate_entity_type(match.entity_type)
            item["domain"] = item.get("domain") or match.domain
            item["aliases"] = self._merge_aliases(item.get("aliases"), match.aliases)
            item["review_status"] = self._review_status_for_match(match, confidence, item)
            item["canonical_match"] = {
                "match_type": match.match_type,
                "score": match.score,
                "degraded_similarity": self.degraded_similarity,
            }
        elif item.get("canonical_id") or item.get("openalx_id") or item.get("openalex_id"):
            item.setdefault("canonical_id", str(item.get("canonical_id") or item.get("openalx_id") or item.get("openalex_id")))
            item.setdefault("canonical_label", item.get("openalx_label") or label)
            requested_status = str(item.get("review_status") or "").lower()
            if requested_status == "rejected":
                item["review_status"] = "rejected"
            elif item.get("accepted") is True and confidence >= 0.70:
                item["review_status"] = "approved"
            elif requested_status == "approved":
                item["review_status"] = "approved"
            else:
                item["review_status"] = "approved" if confidence >= 0.85 else "pending"
            item["canonical_match"] = {
                "match_type": "external_id",
                "score": 1.0,
                "degraded_similarity": self.degraded_similarity,
            }
        else:
            item.setdefault("canonical_id", stable_canonical_id(label))
            item.setdefault("canonical_label", label)
            item["review_status"] = "pending"
            item["canonical_match"] = {
                "match_type": "none",
                "score": 0.0,
                "degraded_similarity": self.degraded_similarity,
            }
        if item["review_status"] == "pending":
            item.setdefault("suggested_canonical", item.get("canonical_label") or label)
            item.setdefault("merge_candidates", self.merge_candidates(label, entity_type=entity_type))
            item.setdefault("evidence", item.get("evidence_span") or item.get("context") or item.get("description") or "")
        return item

    def validate_relation(self, relation: dict[str, Any]) -> dict[str, Any]:
        item = dict(relation)
        item["relation_type"] = self.ontology.validate_relation_type(item.get("relation_type") or item.get("type"))
        if not item.get("subject") or not item.get("object"):
            raise ValueError("Relation requires subject and object")
        return item

    def find_match(self, label: str, entity_type: str | None = None) -> CanonicalMatch | None:
        normalized = normalize_label(label)
        if not normalized:
            return None
        seed = self._seed_index.get(normalized)
        if seed is not None:
            return CanonicalMatch(
                canonical_id=seed.canonical_id,
                canonical_label=seed.label,
                entity_type=seed.entity_type,
                domain=seed.domain,
                match_type="exact_alias",
                score=1.0,
                aliases=seed.aliases,
            )
        if self.degraded_similarity:
            return None
        return self._embedding_match(label, entity_type=entity_type)

    def merge_candidates(self, label: str, entity_type: str | None = None, top_k: int = 5) -> list[dict[str, Any]]:
        if self.degraded_similarity or self.embedding_engine is None:
            return []
        query_vector = self.embedding_engine.embed(label)
        rows: list[dict[str, Any]] = []
        for record in self._canonical_records:
            if not self._type_compatible(str(record["entity_type"]), entity_type):
                continue
            score = self.embedding_engine.similarity(query_vector, record["vector"])
            if score >= float(self.ontology.policy.get("candidate_similarity_threshold", 0.78)):
                rows.append(
                    {
                        "canonical_id": record["canonical_id"],
                        "label": record["label"],
                        "entity_type": record["entity_type"],
                        "score": round(score, 4),
                    }
                )
        rows.sort(key=lambda row: row["score"], reverse=True)
        return rows[:top_k]

    def _embedding_match(self, label: str, entity_type: str | None = None) -> CanonicalMatch | None:
        candidates = self.merge_candidates(label, entity_type=entity_type, top_k=1)
        if not candidates:
            return None
        best = candidates[0]
        threshold = float(self.ontology.policy.get("auto_merge_similarity_threshold", 0.92))
        if float(best["score"]) < threshold:
            return None
        seed = next((item for item in self.ontology.seeds if item.canonical_id == best["canonical_id"]), None)
        if seed is None:
            return None
        return CanonicalMatch(
            canonical_id=seed.canonical_id,
            canonical_label=seed.label,
            entity_type=seed.entity_type,
            domain=seed.domain,
            match_type="embedding",
            score=float(best["score"]),
            aliases=seed.aliases,
        )

    def _build_canonical_records(self) -> list[dict[str, Any]]:
        if self.embedding_engine is None or self.degraded_similarity:
            return []
        records = []
        for seed in self.ontology.seeds:
            records.append(
                {
                    "canonical_id": seed.canonical_id,
                    "label": seed.label,
                    "entity_type": seed.entity_type,
                    "vector": self.embedding_engine.embed(seed.label),
                }
            )
        return records

    @staticmethod
    def _type_compatible(candidate_type: str, requested_type: str | None) -> bool:
        if not requested_type or requested_type == "DomainConcept":
            return True
        if candidate_type == requested_type:
            return True
        compatible = {
            ("Algorithm", "MethodFamily"),
            ("MethodFamily", "Algorithm"),
            ("Theory", "DomainConcept"),
            ("Metric", "DomainConcept"),
            ("ModelArchitecture", "Algorithm"),
            ("ModelArchitecture", "MethodFamily"),
            ("System", "MethodFamily"),
            ("System", "ModelArchitecture"),
            ("MethodFamily", "Theory"),
        }
        return (candidate_type, requested_type) in compatible

    @staticmethod
    def _coerce_float(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @staticmethod
    def _evidence_span(item: dict[str, Any]) -> str:
        text = str(item.get("evidence_span") or item.get("context") or item.get("description") or "").strip()
        return re.sub(r"\s+", " ", text)[:360]

    @staticmethod
    def _section_from_context(context: str) -> str:
        match = re.search(r"(?:section|heading):\s*([^|.;]{2,80})", context or "", flags=re.IGNORECASE)
        return re.sub(r"\s+", " ", match.group(1)).strip()[:80] if match else ""

    @staticmethod
    def _merge_aliases(current: Any, aliases: tuple[str, ...]) -> list[str]:
        values: list[str] = []
        if isinstance(current, list):
            values.extend(str(item) for item in current if item)
        elif current:
            values.append(str(current))
        values.extend(aliases)
        seen: set[str] = set()
        output: list[str] = []
        for value in values:
            key = value.lower().strip()
            if key and key not in seen:
                seen.add(key)
                output.append(value)
        return output

    @staticmethod
    def _review_status_for_match(match: CanonicalMatch, confidence: float, item: dict[str, Any]) -> str:
        if str(item.get("review_status") or "").lower() == "rejected":
            return "rejected"
        if item.get("accepted") is True and match.match_type in {"exact_alias", "embedding"} and confidence >= 0.70:
            return "approved"
        if match.match_type == "exact_alias" and confidence >= 0.85:
            return "approved"
        if str(item.get("review_status") or "").lower() == "approved":
            return "approved"
        return "pending"


def ontology_snapshot(path: str | Path = DEFAULT_ONTOLOGY_PATH) -> str:
    ontology = Ontology.from_file(path)
    return json.dumps(
        {
            "version": ontology.version,
            "entity_types": sorted(ontology.entity_types),
            "relation_types": sorted(ontology.relation_types),
            "seed_count": len(ontology.seeds),
        },
        sort_keys=True,
    )
