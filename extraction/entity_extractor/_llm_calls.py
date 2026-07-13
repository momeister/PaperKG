"""EntityExtractor: LLM-Aufrufe, Retries und Retry-Entscheidungen. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from extraction.entity_extractor._shared import (
    CLAIMS_EXTRACTION_PROMPT,
    DeterministicScanResult,
    ParsedLLMResponse,
    safe_llm_extract,
)

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class LlmCallsMixin(_Base):
    """LLM-Aufrufe, Retries und Retry-Entscheidungen."""

    def _run_structural_call(
        self,
        text_summary: str,
        provider: str | None,
        base_overrides: dict[str, Any],
        scan: DeterministicScanResult | None = None,
        chunk_index: int = 1,
        chunk_count: int = 1,
        retry_split: bool = True,
    ) -> ParsedLLMResponse:
        """Run call 1 for concepts and methods with deterministic settings."""
        candidate_json = "[]"
        if scan is not None:
            hints = self._hints_for_chunk(scan, text_summary, limit=24)
            candidate_json = json.dumps(hints, ensure_ascii=False)
        prompt = (
            self.STRUCTURAL_PROMPT
            .replace("{candidate_json}", candidate_json)
            .replace("{paper_text}", f"[Chunk {chunk_index}/{chunk_count}]\n\n{text_summary}")
        )
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(5000, min(int(base_overrides.get("max_tokens") or 10000), 12000)),
            temperature=0.1,
            top_p=0.85,
        )
        parsed = self._call_and_parse_json(
            [
                {"role": "system", "content": "Return complete JSON only. Do not include markdown, prose, or hidden reasoning. /no_think"},
                {"role": "user", "content": prompt},
            ],
            provider=provider,
            overrides=overrides,
            default={
                "concepts": [],
                "methods": [],
                "concept_candidates": [],
                "method_candidates": [],
            },
        )
        if parsed.parse_quality in {"partial", "failed"} and retry_split and len(text_summary) > 9000:
            split_calls = [
                self._run_structural_call(
                    part,
                    provider,
                    base_overrides,
                    scan=scan,
                    chunk_index=chunk_index,
                    chunk_count=chunk_count,
                    retry_split=False,
                )
                for part in self._split_text_for_structural_retry(text_summary)
            ]
            merged = {
                "concepts": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("concepts")) for call in split_calls]
                ),
                "methods": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("methods")) for call in split_calls]
                ),
                "concept_candidates": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("concept_candidates")) for call in split_calls]
                ),
                "method_candidates": self._merge_entity_lists(
                    *[self._coerce_list(call.data.get("method_candidates")) for call in split_calls]
                ),
            }
            split_quality = self._worst_parse_quality([call.parse_quality for call in split_calls])
            if split_quality in {"clean", "trimmed"} or self._parsed_payload_score(merged) > self._parsed_payload_score(parsed.data):
                return ParsedLLMResponse(
                    data=merged,
                    parse_quality=split_quality,
                    raw_text="\n\n--- SPLIT STRUCTURAL RETRY ---\n\n".join(call.raw_text for call in split_calls),
                    tokens_used=sum(call.tokens_used or self._estimate_tokens(call.raw_text) for call in split_calls),
                )
        return parsed

    @staticmethod
    def _split_text_for_structural_retry(text: str) -> list[str]:
        """Split an oversized malformed structural chunk on paragraph boundaries."""
        cleaned = text.strip()
        if not cleaned:
            return [""]
        midpoint = len(cleaned) // 2
        candidates = [match.start() for match in re.finditer(r"\n\s*\n", cleaned)]
        split_at = min(candidates, key=lambda index: abs(index - midpoint)) if candidates else midpoint
        return [part for part in (cleaned[:split_at].strip(), cleaned[split_at:].strip()) if part]

    def _run_semantic_call(
        self,
        text_summary: str,
        structural_data: dict[str, Any],
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Run call 2 for claims, metadata, and cross-domain analysis."""
        structural_json = json.dumps(self._compact_structural_context(structural_data), ensure_ascii=False)
        prompt = (
            self.SEMANTIC_PROMPT
            .replace("{structural_json}", structural_json)
            .replace("{paper_text}", text_summary)
        )
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(5000, min(int(base_overrides.get("max_tokens") or 8000), 10000)),
            temperature=0.1,
            top_p=0.85,
        )
        return self._call_and_parse_json(
            [
                {"role": "system", "content": "Return complete JSON only. Do not include markdown, prose, or hidden reasoning. /no_think"},
                {"role": "user", "content": prompt},
            ],
            provider=provider,
            overrides=overrides,
            default={
                "paper_type": "research",
                "claims": [],
                "cross_domain_hints": [],
                "terminology_conflicts": [],
                "temporal_coverage": {},
                "mathematical_content": {"has_formulas": False, "formula_types": []},
                "language_detected": "en",
            },
        )

    def _run_claims_call(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Run a dedicated high-recall claims extraction pass."""
        prompt = CLAIMS_EXTRACTION_PROMPT.replace("{paper_text}", paper_text or "")
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(4000, min(int(base_overrides.get("max_tokens") or 8000), 10000)),
            temperature=0.1,
            top_p=0.85,
            json_object=False,
        )

        def llm_call_fn(current_prompt: str) -> Any:
            return self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "Return a valid JSON array only. No markdown, no commentary, no hidden reasoning. /no_think",
                    },
                    {"role": "user", "content": current_prompt},
                ],
                provider=provider,
                overrides=overrides,
            )

        claims = safe_llm_extract(prompt, llm_call_fn, field_name="claims", retries=3)
        return ParsedLLMResponse(
            data={"claims": claims},
            parse_quality="clean",
            raw_text=json.dumps(claims, ensure_ascii=False),
            tokens_used=self._last_tokens_used(),
        )

    def _run_methods_only_retry(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Retry method extraction when partial Call 1 recovery lost the methods key."""
        prompt = self.METHODS_ONLY_PROMPT.replace("{paper_text}", paper_text or "")
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=12000,
            temperature=0.1,
            top_p=0.85,
            json_object=False,
        )
        try:
            raw_response = self.llm.chat(
                [{"role": "user", "content": prompt}],
                provider=provider,
                overrides=overrides,
            )
            if isinstance(raw_response, dict):
                raw_text = str(raw_response.get("content") or raw_response)
            else:
                raw_text = str(raw_response or "")
        except Exception as exc:
            logger.exception("Methods-only retry failed")
            return ParsedLLMResponse(
                data={"methods": []},
                parse_quality="failed",
                raw_text=f"LLM methods-only retry failed: {exc}",
                tokens_used=None,
            )

        parsed = self._parse_json_array_robust(raw_text)
        return ParsedLLMResponse(
            data={"methods": parsed.data},
            parse_quality=parsed.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )

    def _run_concepts_only_retry(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
        scan: DeterministicScanResult,
    ) -> ParsedLLMResponse:
        """Retry concept extraction with a smaller array-only prompt."""
        candidate_json = json.dumps((scan.concepts + scan.methods)[:32], ensure_ascii=False)
        prompt = (
            self.CONCEPTS_ONLY_PROMPT
            .replace("{candidate_json}", candidate_json)
            .replace("{paper_text}", paper_text or "")
        )
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=10000,
            temperature=0.1,
            top_p=0.85,
            json_object=False,
        )
        try:
            raw_response = self.llm.chat(
                [
                    {
                        "role": "system",
                        "content": "Return a valid JSON array only. No markdown, no commentary, no hidden reasoning. /no_think",
                    },
                    {"role": "user", "content": prompt},
                ],
                provider=provider,
                overrides=overrides,
            )
            raw_text = str(raw_response.get("content") or raw_response) if isinstance(raw_response, dict) else str(raw_response or "")
        except Exception as exc:
            logger.exception("Concepts-only retry failed")
            return ParsedLLMResponse(
                data={"concepts": []},
                parse_quality="failed",
                raw_text=f"LLM concepts-only retry failed: {exc}",
                tokens_used=None,
            )

        parsed_array = self._parse_json_array_robust(raw_text)
        if parsed_array.data:
            return ParsedLLMResponse(
                data={"concepts": parsed_array.data},
                parse_quality=parsed_array.parse_quality,
                raw_text=raw_text,
                tokens_used=self._last_tokens_used(),
            )
        parsed_object = self._parse_json_robust(raw_text, default={"concepts": []})
        return ParsedLLMResponse(
            data={"concepts": self._coerce_list(parsed_object.data.get("concepts"))},
            parse_quality=parsed_object.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )

    def _run_semantic_lists_retry(
        self,
        paper_text: str,
        provider: str | None,
        base_overrides: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Retry semantic list extraction when Call 2 partial recovery loses claims."""
        prompt = self.SEMANTIC_LISTS_RETRY_PROMPT.replace("{paper_text}", paper_text or "")
        overrides = self._call_overrides(
            base_overrides,
            max_tokens=max(3000, min(int(base_overrides.get("max_tokens") or 6000), 8000)),
            temperature=0.1,
            top_p=0.85,
        )
        return self._call_and_parse_json(
            [
                {"role": "system", "content": "Return complete JSON only. Do not include markdown, prose, or hidden reasoning. /no_think"},
                {"role": "user", "content": prompt},
            ],
            provider=provider,
            overrides=overrides,
            default={"claims": [], "cross_domain_hints": [], "terminology_conflicts": []},
        )

    @classmethod
    def _should_retry_concepts(
        cls,
        concepts: list[dict[str, Any]],
        structural_calls: list[ParsedLLMResponse],
        scan: DeterministicScanResult,
    ) -> bool:
        """Return true when the main structural prompt produced no concepts."""
        if concepts:
            return False
        structural_quality = cls._worst_parse_quality([call.parse_quality for call in structural_calls])
        if structural_quality not in {"partial", "failed"}:
            return False
        return bool(structural_calls or scan.concepts or scan.methods)

    @classmethod
    def _should_retry_methods(
        cls,
        methods: list[dict[str, Any]],
        structural_calls: list[ParsedLLMResponse],
        concepts: list[dict[str, Any]],
        scan: DeterministicScanResult,
    ) -> bool:
        """Return true when partial Call 1 likely lost method extraction."""
        if methods:
            return False
        if cls._worst_parse_quality([call.parse_quality for call in structural_calls]) not in {"partial", "failed"}:
            return False
        if not (concepts or scan.concepts or scan.methods):
            return False
        for call in structural_calls:
            if call.parse_quality in {"partial", "failed"}:
                return True
        return False

    @staticmethod
    def _should_retry_semantic_lists(
        semantic: ParsedLLMResponse,
        claims: list[Any],
        cross_domain_hints: list[Any],
        terminology_conflicts: list[Any],
        concepts: list[dict[str, Any]],
        paper_text: str,
    ) -> bool:
        """Return true when semantic extraction is too thin for a meaningful KG."""
        is_long_or_rich = bool(concepts or len(paper_text or "") >= 5000)
        if not is_long_or_rich:
            return False
        if semantic.parse_quality == "partial" and not claims:
            return True
        if semantic.parse_quality == "failed":
            return False
        if len(claims) < 3 and len(paper_text or "") >= 12000 and re.search(
            r"\b(we|this paper|this article|this survey|our|results?|findings?|show|shows|provide|provides|propose|presents?|demonstrate|suggest|challenge|taxonomy|framework)\b",
            paper_text or "",
            flags=re.IGNORECASE,
        ):
            return True
        return False

    @staticmethod
    def _should_run_dedicated_claims_pass(paper_text: str, claims: list[Any]) -> bool:
        """Use the extra claims pass for full papers where shallow claims are likely."""
        text_length = len(paper_text or "")
        if text_length < 12000:
            return False
        if not re.search(
            r"\b(we|this paper|this article|this survey|our|results?|findings?|show|shows|provide|provides|propose|presents?|demonstrate|suggest|challenge|taxonomy|framework)\b",
            paper_text or "",
            flags=re.IGNORECASE,
        ):
            return False
        return len(claims) < 3

    def _call_and_parse_json(
        self,
        messages: list[dict[str, str]],
        provider: str | None,
        overrides: dict[str, Any],
        default: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Call the model and parse JSON without aborting the pipeline."""
        try:
            raw_response = self.llm.chat(messages, provider=provider, overrides=overrides)
            if isinstance(raw_response, dict):
                raw_text = str(raw_response.get("content") or raw_response)
            else:
                raw_text = str(raw_response or "")
        except Exception as exc:
            logger.exception("LLM extraction call failed")
            return ParsedLLMResponse(
                data=dict(default),
                parse_quality="failed",
                raw_text=f"LLM call failed: {exc}",
                tokens_used=None,
            )

        parsed = self._parse_json_robust(raw_text, default=default)
        if parsed.parse_quality == "partial":
            retry = self._retry_strict_json(messages, provider, overrides, default)
            if retry.parse_quality in {"clean", "trimmed"} or self._parsed_payload_score(retry.data) > self._parsed_payload_score(parsed.data):
                return retry
        return ParsedLLMResponse(
            data=parsed.data,
            parse_quality=parsed.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )

    def _retry_strict_json(
        self,
        messages: list[dict[str, str]],
        provider: str | None,
        overrides: dict[str, Any],
        default: dict[str, Any],
    ) -> ParsedLLMResponse:
        """Retry a malformed JSON call once with stricter decoding instructions."""
        retry_overrides = dict(overrides)
        retry_overrides["temperature"] = 0.05
        retry_overrides["top_p"] = min(float(retry_overrides.get("top_p") or 0.85), 0.8)
        retry_overrides["max_tokens"] = min(16000, max(int(retry_overrides.get("max_tokens") or 8000), 10000))
        retry_messages = [
            {
                "role": "system",
                "content": (
                    "The previous response was invalid or incomplete. "
                    "Return one complete valid JSON object only. No markdown, no commentary, no hidden reasoning. /no_think"
                ),
            },
            *messages,
        ]
        try:
            raw_response = self.llm.chat(retry_messages, provider=provider, overrides=retry_overrides)
            raw_text = str(raw_response.get("content") or raw_response) if isinstance(raw_response, dict) else str(raw_response or "")
        except Exception as exc:
            logger.exception("Strict JSON retry failed")
            return ParsedLLMResponse(
                data=dict(default),
                parse_quality="failed",
                raw_text=f"Strict JSON retry failed: {exc}",
                tokens_used=None,
            )
        parsed = self._parse_json_robust(raw_text, default=default)
        return ParsedLLMResponse(
            data=parsed.data,
            parse_quality=parsed.parse_quality,
            raw_text=raw_text,
            tokens_used=self._last_tokens_used(),
        )
