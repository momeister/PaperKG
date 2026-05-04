from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from query.llm_router import LLMRouter


@dataclass
class ExtractionResult:
    paper_id: str
    concepts: list[dict[str, str]] = field(default_factory=list)
    methods: list[dict[str, str]] = field(default_factory=list)
    claims: list[dict[str, str]] = field(default_factory=list)
    cross_domain_hints: list[str] = field(default_factory=list)
    language_detected: str = "en"
    raw_response: str = ""


class EntityExtractor:
    """
    Extracts concepts, methods, claims from paper text using LLM.
    Supports configurable LLM providers (Ollama, LM Studio, OpenAI).
    """

    EXTRACTION_PROMPT = """Given the following research paper text, extract structured information in JSON format:

```json
{{
  "concepts": [
    {{"label": "concept_name", "context": "where_it_appears", "confidence": 0.9}}
  ],
  "methods": [
    {{"label": "method_name", "domain": "field", "description": "brief_description"}}
  ],
  "claims": [
    {{"statement": "main_claim", "evidence_type": "experimental|theoretical|review"}}
  ],
  "cross_domain_hints": ["potential_application_area_1", "potential_application_area_2"],
  "language_detected": "en"
}}
```

Focus on:
- Main concepts and terminology
- Methodologies and approaches
- Key claims and findings
- Potential applications outside the primary domain

Paper text (truncated to 24k chars):
{paper_text}
"""

    def __init__(self, llm_router: LLMRouter) -> None:
        """
        Initialize extractor with LLM router.

        Args:
            llm_router: Configured LLMRouter instance for LLM calls
        """
        self.llm = llm_router

    def extract(
        self,
        paper_id: str,
        paper_text: str,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        """
        Extract entities from paper text using LLM.

        Args:
            paper_id: Unique paper identifier
            paper_text: Full paper text or significant portion
            provider: Optional LLM provider override (uses config default if None)
            overrides: Optional settings overrides (temperature, max_tokens, etc)

        Returns:
            ExtractionResult with structured entities or error fallback
        """
        # Truncate to prevent excessive token usage
        text_summary = paper_text[: 8000 * 3] if len(paper_text) > 24000 else paper_text

        prompt = self.EXTRACTION_PROMPT.format(paper_text=text_summary)
        messages = [{"role": "user", "content": prompt}]

        try:
            data = self.llm.chat_json(
                messages, provider=provider, overrides=overrides
            )
        except Exception as exc:
            # Return error result with empty entities
            return ExtractionResult(
                paper_id=paper_id,
                raw_response=f"Extraction failed: {str(exc)}",
                concepts=[],
                methods=[],
                claims=[],
            )

        return ExtractionResult(
            paper_id=paper_id,
            concepts=data.get("concepts", []),
            methods=data.get("methods", []),
            claims=data.get("claims", []),
            cross_domain_hints=data.get("cross_domain_hints", []),
            language_detected=data.get("language_detected", "en"),
            raw_response="",
        )
