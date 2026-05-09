from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from query.llm_router import LLMRouter

logger = logging.getLogger(__name__)


STOPWORDS = {
    "about",
    "across",
    "after",
    "also",
    "among",
    "and",
    "are",
    "between",
    "both",
    "but",
    "can",
    "claim",
    "could",
    "does",
    "from",
    "have",
    "into",
    "more",
    "paper",
    "research",
    "show",
    "shows",
    "study",
    "than",
    "that",
    "the",
    "their",
    "these",
    "this",
    "through",
    "using",
    "with",
    "would",
}


@dataclass
class ConflictAnalysis:
    """Result of conflict detection analysis."""

    claim_pair: tuple[str, str]
    conflict_type: str  # "contradictory", "complementary", "irrelevant", "supporting"
    confidence: float
    reasoning: str
    resolution: str | None = None


class ConflictDetector:
    """
    Detects conflicts, contradictions, or inconsistencies in extracted claims.
    Uses LLM to analyze semantic relationships between claims.

    Supports:
    - Claim-to-claim contradiction detection
    - Method inconsistencies
    - Temporal/versioning conflicts
    """

    DEFAULT_MAX_PAIRS = 20
    CONFLICT_MAX_TOKENS = 800

    CONFLICT_ANALYSIS_PROMPT = """Analyze the relationship between these two research claims:

Claim 1: {claim1}
Claim 2: {claim2}

Determine if they are:
- "contradictory": directly contradict each other
- "complementary": support/extend each other
- "supporting": one supports/validates the other
- "irrelevant": unrelated claims

Respond in JSON:
{{
  "conflict_type": "contradictory|complementary|supporting|irrelevant",
  "confidence": 0.95,
  "reasoning": "Why do you classify them this way?",
  "resolution": "If contradictory, which is more likely correct and why?"
}}
"""

    def __init__(self, llm_router: LLMRouter) -> None:
        """
        Initialize conflict detector with LLM router.

        Args:
            llm_router: Configured LLMRouter for analysis
        """
        self.llm = llm_router

    @staticmethod
    def _merge_conflict_overrides(overrides: dict[str, Any] | None) -> dict[str, Any]:
        """Use the selected model, but keep pairwise conflict calls lightweight."""
        merged = dict(overrides or {})
        extra = dict(merged.get("extra") or {})
        chat_template_kwargs = dict(extra.get("chat_template_kwargs") or {})
        chat_template_kwargs["enable_thinking"] = False
        extra["chat_template_kwargs"] = chat_template_kwargs
        extra["json_mode"] = True

        merged.update(
            {
                "temperature": 0.1,
                "top_p": 0.85,
                "max_tokens": ConflictDetector.CONFLICT_MAX_TOKENS,
                "extra": extra,
            }
        )
        return merged

    @staticmethod
    def _claim_terms(claim: str) -> set[str]:
        terms = set()
        for token in re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}", claim.lower()):
            if token in STOPWORDS or len(token) < 4:
                continue
            terms.add(token[:-1] if token.endswith("s") and len(token) > 5 else token)
        return terms

    @classmethod
    def _claim_pair_score(cls, claim1: str, claim2: str) -> float:
        terms1 = cls._claim_terms(claim1)
        terms2 = cls._claim_terms(claim2)
        if not terms1 or not terms2:
            return 0.0

        overlap = terms1 & terms2
        if not overlap:
            return 0.0

        denominator = max(1, min(len(terms1), len(terms2)))
        score = len(overlap) / denominator
        conflict_markers = {
            "increase",
            "decrease",
            "improve",
            "reduce",
            "higher",
            "lower",
            "support",
            "contradict",
            "not",
            "lack",
            "fail",
        }
        if (terms1 | terms2) & conflict_markers:
            score += 0.1
        return score

    def analyze_claim_pair(
        self,
        claim1: str,
        claim2: str,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> ConflictAnalysis:
        """
        Analyze relationship between two claims.

        Args:
            claim1: First claim statement
            claim2: Second claim statement
            provider: Optional LLM provider override
            overrides: Optional LLM settings overrides

        Returns:
            ConflictAnalysis with classification and reasoning
        """
        prompt = self.CONFLICT_ANALYSIS_PROMPT.format(claim1=claim1, claim2=claim2)
        messages = [{"role": "user", "content": prompt}]
        conflict_overrides = self._merge_conflict_overrides(overrides)

        try:
            data = self.llm.chat_json(
                messages, provider=provider, overrides=conflict_overrides
            )
        except Exception as exc:
            return ConflictAnalysis(
                claim_pair=(claim1, claim2),
                conflict_type="irrelevant",
                confidence=0.0,
                reasoning=f"Analysis failed: {str(exc)}",
            )

        return ConflictAnalysis(
            claim_pair=(claim1, claim2),
            conflict_type=data.get("conflict_type", "irrelevant"),
            confidence=float(data.get("confidence", 0.0)),
            reasoning=data.get("reasoning", ""),
            resolution=data.get("resolution"),
        )

    def analyze_claims_batch(
        self,
        claims: list[str],
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
        max_pairs: int | None = DEFAULT_MAX_PAIRS,
    ) -> list[ConflictAnalysis]:
        """
        Analyze likely-relevant pairs of claims for conflicts.

        Args:
            claims: List of claim statements
            provider: Optional LLM provider override
            overrides: Optional LLM settings overrides
            max_pairs: Maximum LLM pair calls. None analyzes all pairs.

        Returns:
            List of ConflictAnalysis for each pair
        """
        results = []
        claim_pairs = []

        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
                score = self._claim_pair_score(claims[i], claims[j])
                claim_pairs.append((score, i, j))

        total_pairs = len(claim_pairs)
        if max_pairs is not None and total_pairs > max_pairs:
            related_pairs = [pair for pair in claim_pairs if pair[0] > 0.0]
            related_pairs.sort(key=lambda pair: pair[0], reverse=True)
            claim_pairs = related_pairs[:max_pairs]
            skipped = total_pairs - len(claim_pairs)
            logger.warning(
                "Conflict detection capped at %s likely claim pairs; skipped %s/%s pairs.",
                max_pairs,
                skipped,
                total_pairs,
            )

        for _, i, j in claim_pairs:
            analysis = self.analyze_claim_pair(
                claims[i], claims[j], provider=provider, overrides=overrides
            )
            results.append(analysis)

        return results

    def find_contradictions(
        self,
        analyses: list[ConflictAnalysis],
        confidence_threshold: float = 0.7,
    ) -> list[ConflictAnalysis]:
        """
        Filter analyses to find high-confidence contradictions.

        Args:
            analyses: List of ConflictAnalysis results
            confidence_threshold: Minimum confidence to report

        Returns:
            Contradictory claims with high confidence
        """
        contradictions = [
            a
            for a in analyses
            if a.conflict_type == "contradictory"
            and a.confidence >= confidence_threshold
        ]

        return sorted(contradictions, key=lambda a: a.confidence, reverse=True)
