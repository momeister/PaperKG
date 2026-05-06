from __future__ import annotations

import json
import re
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

Extraction rules:
- Extract EVERY named algorithm, model, architecture, dataset, metric, and technique as a separate concept node.
- Include baselines and comparison models, not only the paper's main contribution.
- Never group multiple models into one concept label; split them into separate concepts.
- For a full-text paper, return about 8-25 concepts when the paper names them; never return fewer than 5 unless the paper truly contains fewer named entities.
- Do not emit umbrella or summary labels such as "Pre-trained Language Models", "Transformer Models", "Deep Learning Models", or "Fine-tuning" as concept nodes unless the paper explicitly treats that phrase as a named technique.
- Keep concept labels specific and directly linkable. If the paper names BERT, RoBERTa, DistilBERT, ELECTRA, ELMo, CNN, LSTM, Bi-LSTM, C-LSTM, HAN, Conv-HAN, Naive Bayes, GloVe, TF-IDF, LDA, or MDS, each one must appear as its own concept when present.
- Methods must be specific enough to distinguish this paper from similar papers. Use labels like "BERT Fine-tuning for Text Classification" or "Naive Bayes with TF-IDF n-gram", not generic labels like "Transformer-based Fine-tuning" or "Benchmark Comparative Analysis".
- Confidence must reflect actual extraction certainty, not extraction order.
- Use high confidence (0.90-0.98) for concepts explicitly named and defined in the paper.
- Use medium confidence (0.65-0.85) for concepts inferred from context.
- Use lower confidence (0.50-0.70) for concepts mentioned only once in passing.
- Confidence values must vary according to evidence; do not assign a monotonically decreasing sequence by position.
- Preserve the exact JSON schema and do not add extra keys.
"""

    def __init__(self, llm_router: LLMRouter) -> None:
        """
        Initialize extractor with LLM router.

        Args:
            llm_router: Configured LLMRouter instance for LLM calls
        """
        self.llm = llm_router

    @staticmethod
    def _build_extraction_text(paper_text: str, max_chars: int = 12000) -> str:
        if len(paper_text) <= max_chars:
            return paper_text

        keywords = [
            r"BERT",
            r"RoBERTa",
            r"DistilBERT",
            r"ELECTRA",
            r"ELMo",
            r"CNN",
            r"LSTM",
            r"Bi-LSTM",
            r"C-LSTM",
            r"HAN",
            r"Conv-HAN",
            r"Naive Bayes",
            r"GloVe",
            r"TF-IDF",
            r"LDA",
            r"MDS",
            r"Transfer Learning",
            r"Knowledge Distillation",
            r"fine-tun",
            r"baseline",
            r"comparison",
            r"dataset",
            r"experiment",
            r"results?",
            r"table",
            r"figure",
        ]

        excerpts: list[str] = []
        seen_spans: set[tuple[int, int]] = set()

        head = paper_text[:8000].strip()
        if head:
            excerpts.append(head)

        for pattern in keywords:
            matches_found = 0
            for match in re.finditer(pattern, paper_text, flags=re.IGNORECASE):
                if matches_found >= 2:
                    break
                start = max(0, match.start() - 500)
                end = min(len(paper_text), match.end() + 900)
                span = (start, end)
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                snippet = paper_text[start:end].strip()
                if snippet:
                    excerpts.append(snippet)
                    matches_found += 1

        combined = "\n\n---\n\n".join(excerpts)
        return combined[:max_chars]

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
        # Prefer salient sections when the paper is long, so comparisons and baselines stay visible.
        text_summary = self._build_extraction_text(paper_text)

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
            raw_response=json.dumps(data, indent=2, ensure_ascii=False),
        )
