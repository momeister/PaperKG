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


# Canonical study-design labels and the regex cues that map to them. Order
# matters: more specific designs (RCT, meta-analysis) are tested before more
# generic ones (observational, survey). Used by ``_infer_study_design`` and
# ``_infer_provenance`` when the LLM did not emit a design.
_STUDY_DESIGN_CUES: tuple[tuple[str, str], ...] = (
    (
        "meta_analysis",
        r"\bmeta[-\s]?analysis\b|\bsystematic review\b|\bPRISMA\b|\bpooled analysis\b",
    ),
    ("systematic_review", r"\bsystematic review\b"),
    (
        "rct",
        r"\brandomi[sz]ed controlled trial\b|\bRCT\b|\bcontrolled clinical trial\b",
    ),
    (
        "randomized_controlled",
        r"\brandomi[sz]ed\b|\bdouble[-\s]?blind\b|\bplacebo[-\s]?controlled\b",
    ),
    (
        "cohort",
        r"\bcohort (?:study|design)?\b|\bprospective (?:cohort )?study\b|\bretrospective (?:cohort )?study\b|\blongitudinal (?:cohort )?study\b",
    ),
    ("case_control", r"\bcase[-\s]?control\b|\bcase control study\b"),
    ("cross_sectional", r"\bcross[-\s]?sectional\b"),
    ("observational", r"\bobservational (?:study|design)?\b"),
    ("survey", r"\bsurvey (?:study|design)?\b|\bquestionnaire (?:study|survey)\b"),
    ("case_series", r"\bcase series\b"),
    ("case_report", r"\bcase report\b"),
    ("benchmark", r"\bbenchmark(?:ing|s)?\b"),
    (
        "simulation",
        r"\bsimulation(?:s| study)?\b|\bsimulated\b|\bsynthetic(?:al)? (?:data|environment)\b",
    ),
    (
        "qualitative",
        r"\bqualitative (?:study|analysis|research)\b|\binterviews?\b|\bfocus groups?\b|\bgrounded theory\b",
    ),
    (
        "theoretical",
        r"\btheorem\b|\bproof\b|\bformal (?:framework|model|analysis)\b|\baxiom",
    ),
)

_VALID_STUDY_DESIGNS = {
    "rct",
    "randomized_controlled",
    "cohort",
    "case_control",
    "observational",
    "cross_sectional",
    "case_series",
    "case_report",
    "survey",
    "meta_analysis",
    "systematic_review",
    "theoretical",
    "simulation",
    "benchmark",
    "qualitative",
    "unknown",
}

_VALID_EVIDENCE_LEVELS = {"high", "moderate", "low", "very_low", "unknown"}

# Mapping study_design → base evidence_level. Used by ``_infer_evidence_level``
# when the LLM did not emit a level. Sample size, peer-review status, and
# funding flags modulate this in ``quality/study_quality.py``.
_DESIGN_TO_BASE_LEVEL = {
    "meta_analysis": "high",
    "systematic_review": "high",
    "rct": "high",
    "randomized_controlled": "high",
    "cohort": "moderate",
    "case_control": "moderate",
    "cross_sectional": "low",
    "observational": "low",
    "survey": "low",
    "case_series": "low",
    "case_report": "very_low",
    "benchmark": "moderate",
    "simulation": "moderate",
    "qualitative": "low",
    "theoretical": "moderate",
    "unknown": "unknown",
}


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
                statement = re.sub(
                    r"\s+", " ", str(claim.get("statement") or "")
                ).strip()
                normalized = cls._normalize_label(statement)
                if not normalized:
                    continue
                item = dict(claim)
                item["statement"] = statement
                item.setdefault("evidence_type", "theoretical")
                item["claim_type"] = cls._infer_claim_type(item)
                item["negated"] = cls._normalize_claim_negation(item)
                item.setdefault("attributed_to", "this_paper")
                cls._enrich_critical_fields(item)
                if normalized not in merged:
                    merged[normalized] = item
                    order.append(normalized)
        return [merged[key] for key in order]

    @classmethod
    def _enrich_critical_fields(cls, claim: dict[str, Any]) -> None:
        """Fill study_design, sample_size, statistical_confidence, and
        evidence_level on a claim dict when the LLM did not emit them, using
        regex cues from the claim statement. Never overwrites a valid value.
        """
        text = str(claim.get("statement") or "")
        # study_design
        existing_design = str(claim.get("study_design") or "").strip().lower()
        if existing_design not in _VALID_STUDY_DESIGNS:
            inferred_design = cls._infer_study_design(text)
            if inferred_design != "unknown":
                claim["study_design"] = inferred_design
            elif existing_design:
                # Keep an unknown-ish but valid label, normalize to "unknown".
                claim["study_design"] = "unknown"
            else:
                claim.setdefault("study_design", "unknown")
        # sample_size
        sample = claim.get("sample_size")
        if not isinstance(sample, dict) or sample.get("value") is None:
            inferred_sample = cls._infer_sample_size(text)
            if inferred_sample is not None:
                if not isinstance(sample, dict):
                    sample = {}
                sample.setdefault("value", inferred_sample["value"])
                sample.setdefault("unit", inferred_sample["unit"])
                sample.setdefault("power_reported", False)
                claim["sample_size"] = sample
            elif not isinstance(sample, dict):
                claim["sample_size"] = {
                    "value": None,
                    "unit": "participants",
                    "power_reported": False,
                }
        # statistical_confidence
        stats = claim.get("statistical_confidence")
        if not isinstance(stats, dict):
            stats = {}
        if not stats.get("p_value"):
            p = cls._infer_p_value(text)
            if p:
                stats["p_value"] = p
        if not stats.get("confidence_interval"):
            ci = cls._infer_confidence_interval(text)
            if ci:
                stats["confidence_interval"] = ci
        if not stats.get("effect_size"):
            es = cls._infer_effect_size(text)
            if es:
                stats["effect_size"] = es
        if "significance_reported" not in stats:
            stats["significance_reported"] = bool(
                re.search(
                    r"\bstatistically significant\b|\bp\s*[<=>]\s*0\.\d+\b", text, re.I
                )
            )
        claim["statistical_confidence"] = stats
        # evidence_level
        existing_level = str(claim.get("evidence_level") or "").strip().lower()
        if existing_level not in _VALID_EVIDENCE_LEVELS:
            claim["evidence_level"] = cls._infer_evidence_level(
                claim.get("study_design"),
                claim.get("sample_size"),
            )
        else:
            claim["evidence_level"] = existing_level

    @staticmethod
    def _infer_study_design(text: str) -> str:
        """Map free-text study-design cues to a canonical label. Returns
        'unknown' when no cue matches. Order matters (specific → generic)."""
        lowered = str(text or "").lower()
        for label, pattern in _STUDY_DESIGN_CUES:
            if re.search(pattern, lowered, re.I):
                return label
        return "unknown"

    @staticmethod
    def _infer_sample_size(text: str) -> dict[str, Any] | None:
        """Extract a numeric sample size from text. Returns {value, unit} or None.

        Recognizes 'n = 42', 'N = 1234', '42 participants/patients/subjects/
        studies/samples/papers/documents', and 'a total of 200 ...'. Prefers
        the first explicit n=/N= match; falls back to the first count + unit.
        """
        lowered = str(text or "")
        # Explicit n=/N= form — most reliable.
        explicit = re.search(
            r"\b[nN]\s*=\s*(\d+(?:[.,]\d+)?)",
            lowered,
        )
        if explicit:
            try:
                value = int(float(explicit.group(1).replace(",", ".")))
                return {"value": value, "unit": "participants"}
            except (TypeError, ValueError):
                pass
        # Count + unit form.
        unit_match = re.search(
            r"\b(\d+(?:[.,]\d+)?)\s+"
            r"(participants|patients|subjects|studies|samples|papers|documents|individuals|"
            r"respondents|articles|controls|volunteers)\b",
            lowered,
            re.I,
        )
        if unit_match:
            try:
                value = int(float(unit_match.group(1).replace(",", ".")))
                unit = unit_match.group(2).lower()
                # Normalize a few synonyms.
                if unit in {"individuals", "respondents", "volunteers", "controls"}:
                    unit = "participants"
                return {"value": value, "unit": unit}
            except (TypeError, ValueError):
                pass
        # "a total of N ..."
        total_match = re.search(
            r"\b(?:a total of|total of|enrolled)\s+(\d+(?:[.,]\d+)?)\b",
            lowered,
            re.I,
        )
        if total_match:
            try:
                value = int(float(total_match.group(1).replace(",", ".")))
                return {"value": value, "unit": "participants"}
            except (TypeError, ValueError):
                pass
        return None

    @staticmethod
    def _infer_p_value(text: str) -> str | None:
        """Extract a p-value string like 'p<0.05' or 'p = 0.03' from text."""
        match = re.search(
            r"\bp\s*[<>=]\s*0?\.\d+\b|\bp\s*[<>=]\s*\.\d+\b",
            str(text or ""),
            re.I,
        )
        if match:
            return re.sub(r"\s+", "", match.group(0).lower())
        # 'statistically significant (p < 0.01)' form, already covered by the
        # first pattern; 'significance level alpha = 0.05' is not a p-value.
        return None

    @staticmethod
    def _infer_confidence_interval(text: str) -> str | None:
        """Extract a confidence interval string like '95% CI 0.2-0.8'."""
        match = re.search(
            r"\b(?:95|99|90)%?\s*CI\s*:?\s*[-\d.,\s]+",
            str(text or ""),
            re.I,
        )
        if match:
            return match.group(0).strip()
        # 'confidence interval [0.2, 0.8]' form
        match = re.search(
            r"\bconfidence interval\s*(?:\w+\s*)?[\[\(][-\d.,\s]+[\]\)]",
            str(text or ""),
            re.I,
        )
        if match:
            return match.group(0).strip()
        return None

    @staticmethod
    def _infer_effect_size(text: str) -> str | None:
        """Extract an effect-size string (Cohen's d, odds ratio, hazard ratio,
        risk ratio, relative risk)."""
        lowered = str(text or "")
        for pattern in (
            r"\bCohen'?s\s*d\s*=?\s*[-\d.]+",
            r"\bodds ratio\s*=?\s*[-\d.]+|\bOR\s*=?\s*[-\d.]+",
            r"\bhazard ratio\s*=?\s*[-\d.]+|\bHR\s*=?\s*[-\d.]+",
            r"\brisk ratio\s*=?\s*[-\d.]+|\bRR\s*=?\s*[-\d.]+",
            r"\brelative risk\s*=?\s*[-\d.]+",
        ):
            match = re.search(pattern, lowered, re.I)
            if match:
                return match.group(0).strip()
        return None

    @classmethod
    def _infer_evidence_level(
        cls,
        study_design: Any,
        sample_size: Any,
    ) -> str:
        """GRADE-light: map study_design + sample_size to high/moderate/low/
        very_low/unknown. Used when the LLM did not emit an evidence_level."""
        design = str(study_design or "unknown").strip().lower()
        base = _DESIGN_TO_BASE_LEVEL.get(design, "unknown")
        if base == "unknown":
            return "unknown"
        # Down-grade by small sample when known.
        size_value = None
        if isinstance(sample_size, dict):
            try:
                size_value = int(sample_size.get("value") or 0) or None
            except (TypeError, ValueError):
                size_value = None
        if size_value is not None:
            if size_value < 10 and base in {"high", "moderate", "low"}:
                return "very_low"
            if size_value < 30 and base in {"high", "moderate"}:
                return "low"
        return base

    @staticmethod
    def _infer_claim_type(claim: dict[str, Any]) -> str:
        existing = str(claim.get("claim_type") or "").strip().lower()
        allowed = {
            "contribution",
            "finding",
            "limitation",
            "negative_result",
            "comparison",
            "recommendation",
        }
        if existing in allowed:
            return existing

        statement = str(claim.get("statement") or "").lower()
        if re.search(
            r"\b(too simple|insufficient|limited|limitation|cannot draw|unable to draw|hard to draw|not enough to)\b",
            statement,
        ):
            return "limitation"
        if re.search(
            r"\b(no evidence|does not|do not|did not|failed to|fails to|cannot|unable to|no significant)\b",
            statement,
        ):
            return "negative_result"
        if re.search(
            r"\b(outperform|outperforms|more robust|less robust|more accurate|less accurate|compared|whereas|than)\b",
            statement,
        ):
            return "comparison"
        if re.search(
            r"\b(should|recommend|requires?|must|need to|necessary)\b", statement
        ):
            return "recommendation"
        if re.search(
            r"\b(introduce|introduces|propose|proposes|present|presents|provide|provides|contribute|contributes)\b",
            statement,
        ):
            return "contribution"
        return "finding"

    @classmethod
    def _normalize_claim_negation(cls, claim: dict[str, Any]) -> bool:
        statement = str(claim.get("statement") or "").lower()
        if re.search(
            r"\bwithout\s+(?:a\s+)?(?:significant\s+|substantial\s+|meaningful\s+)?"
            r"(?:loss|degradation|performance loss|drop|reduction)\b",
            statement,
        ) or re.search(
            r"\bwithout\s+(?:sacrificing|compromising|hurting)\b", statement
        ):
            return False
        explicit_negation = bool(
            re.search(
                r"\b(no evidence|no significant|does not|do not|did not|cannot|can not|unable to|failed to|fails to)\b",
                statement,
            )
        )
        if explicit_negation:
            return True
        if str(claim.get("claim_type") or "").lower() in {
            "limitation",
            "negative_result",
        }:
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
        abstract = re.search(
            r"\babstract\b\s*([\s\S]{200,2500}?)(?:\n\s*(?:keywords|introduction|1\.?\s+introduction)\b)",
            text,
            flags=re.IGNORECASE,
        )
        if abstract:
            windows.append(abstract.group(1))
        for match in re.finditer(
            r"\b(?:conclusion|conclusions|discussion)\b\s*([\s\S]{200,2500})",
            text,
            flags=re.IGNORECASE,
        ):
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
            for sentence in re.split(
                r"(?<=[.!?])\s+", re.sub(r"\s+", " ", window.strip())
            ):
                clean = sentence.strip(" .")
                if not (70 <= len(clean) <= 320):
                    continue
                if not claim_markers.search(clean):
                    continue
                key = cls._normalize_label(clean[:120])
                if key in seen:
                    continue
                seen.add(key)
                claim = {
                    "statement": clean,
                    "evidence_type": (
                        "review" if paper_type_hint == "survey" else "theoretical"
                    ),
                    "negated": bool(
                        re.search(
                            r"\b(no|not|lack|lacking|limited|without)\b",
                            clean,
                            flags=re.IGNORECASE,
                        )
                    ),
                    "attributed_to": "this_paper",
                    "auto_detected": True,
                    "candidate_source": "text_claim_fallback",
                }
                cls._enrich_critical_fields(claim)
                candidates.append(claim)
                if len(candidates) >= limit:
                    return candidates
        return candidates
