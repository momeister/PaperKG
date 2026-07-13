"""EntityExtractor: Claim-Merge, -Typen und Text-Fallbacks. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any


if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class ClaimsMixin(_Base):
    """Claim-Merge, -Typen und Text-Fallbacks."""

    @classmethod
    def _merge_claim_lists(cls, *claim_lists: list[Any]) -> list[dict[str, Any]]:
        """Merge claim lists by normalized statement, preserving first-seen order."""
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for claim_list in claim_lists:
            for claim in claim_list:
                if not isinstance(claim, dict):
                    continue
                statement = re.sub(r"\s+", " ", str(claim.get("statement") or "")).strip()
                normalized = cls._normalize_label(statement)
                if not normalized:
                    continue
                item = dict(claim)
                item["statement"] = statement
                item.setdefault("evidence_type", "theoretical")
                item["claim_type"] = cls._infer_claim_type(item)
                item["negated"] = cls._normalize_claim_negation(item)
                item.setdefault("attributed_to", "this_paper")
                if normalized not in merged:
                    merged[normalized] = item
                    order.append(normalized)
        return [merged[key] for key in order]

    @staticmethod
    def _infer_claim_type(claim: dict[str, Any]) -> str:
        existing = str(claim.get("claim_type") or "").strip().lower()
        allowed = {"contribution", "finding", "limitation", "negative_result", "comparison", "recommendation"}
        if existing in allowed:
            return existing

        statement = str(claim.get("statement") or "").lower()
        if re.search(r"\b(too simple|insufficient|limited|limitation|cannot draw|unable to draw|hard to draw|not enough to)\b", statement):
            return "limitation"
        if re.search(r"\b(no evidence|does not|do not|did not|failed to|fails to|cannot|unable to|no significant)\b", statement):
            return "negative_result"
        if re.search(r"\b(outperform|outperforms|more robust|less robust|more accurate|less accurate|compared|whereas|than)\b", statement):
            return "comparison"
        if re.search(r"\b(should|recommend|requires?|must|need to|necessary)\b", statement):
            return "recommendation"
        if re.search(r"\b(introduce|introduces|propose|proposes|present|presents|provide|provides|contribute|contributes)\b", statement):
            return "contribution"
        return "finding"

    @classmethod
    def _normalize_claim_negation(cls, claim: dict[str, Any]) -> bool:
        statement = str(claim.get("statement") or "").lower()
        if re.search(
            r"\bwithout\s+(?:a\s+)?(?:significant\s+|substantial\s+|meaningful\s+)?"
            r"(?:loss|degradation|performance loss|drop|reduction)\b",
            statement,
        ) or re.search(r"\bwithout\s+(?:sacrificing|compromising|hurting)\b", statement):
            return False
        explicit_negation = bool(
            re.search(
                r"\b(no evidence|no significant|does not|do not|did not|cannot|can not|unable to|failed to|fails to)\b",
                statement,
            )
        )
        if explicit_negation:
            return True
        if str(claim.get("claim_type") or "").lower() in {"limitation", "negative_result"}:
            return False
        return bool(claim.get("negated"))

    @classmethod
    def _fallback_claims_from_text(
        cls,
        paper_text: str,
        paper_type_hint: str | None = None,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Extract conservative claim candidates from abstract/conclusion sentences."""
        text = cls._text_before_references(paper_text or "")
        if not text:
            return []
        windows: list[str] = []
        abstract = re.search(r"\babstract\b\s*([\s\S]{200,2500}?)(?:\n\s*(?:keywords|introduction|1\.?\s+introduction)\b)", text, flags=re.IGNORECASE)
        if abstract:
            windows.append(abstract.group(1))
        for match in re.finditer(r"\b(?:conclusion|conclusions|discussion)\b\s*([\s\S]{200,2500})", text, flags=re.IGNORECASE):
            windows.append(match.group(1))
            if len(windows) >= 3:
                break
        if not windows:
            windows.append(text[:3500])

        candidates: list[dict[str, Any]] = []
        seen: set[str] = set()
        claim_markers = re.compile(
            r"\b(we|this paper|this article|this survey|our|results?|findings?|show|shows|provide|provides|propose|presents?|demonstrate|suggest|lack|lacking|challenge|challenges|framework|taxonomy)\b",
            flags=re.IGNORECASE,
        )
        for window in windows:
            for sentence in re.split(r"(?<=[.!?])\s+", re.sub(r"\s+", " ", window.strip())):
                clean = sentence.strip(" .")
                if not (70 <= len(clean) <= 320):
                    continue
                if not claim_markers.search(clean):
                    continue
                key = cls._normalize_label(clean[:120])
                if key in seen:
                    continue
                seen.add(key)
                candidates.append(
                    {
                        "statement": clean,
                        "evidence_type": "review" if paper_type_hint == "survey" else "theoretical",
                        "negated": bool(re.search(r"\b(no|not|lack|lacking|limited|without)\b", clean, flags=re.IGNORECASE)),
                        "attributed_to": "this_paper",
                        "auto_detected": True,
                        "candidate_source": "text_claim_fallback",
                    }
                )
                if len(candidates) >= limit:
                    return candidates
        return candidates
