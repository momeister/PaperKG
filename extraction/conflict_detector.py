from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from query.llm_router import LLMRouter


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

        try:
            data = self.llm.chat_json(
                messages, provider=provider, overrides=overrides
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
    ) -> list[ConflictAnalysis]:
        """
        Analyze all pairs of claims for conflicts.

        Args:
            claims: List of claim statements
            provider: Optional LLM provider override
            overrides: Optional LLM settings overrides

        Returns:
            List of ConflictAnalysis for each pair
        """
        results = []

        for i in range(len(claims)):
            for j in range(i + 1, len(claims)):
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
