"""EntityExtractor: Haupt-Orchestrierung: extract() und Kontextbudget/Modellwahl. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from extraction.entity_extractor._shared import (
    DeterministicScanResult,
    ExtractionResult,
    ParsedLLMResponse,
    deduplicate_methods,
    enrich_method_domains,
    filter_concepts,
)
from query.context_budget import (
    ContextBudgetDecision,
    decide_whole_context,
    effective_generation_limits,
    normalize_context_policy,
)

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class OrchestrationMixin(_Base):
    """Haupt-Orchestrierung: extract() und Kontextbudget/Modellwahl."""

    def extract(
        self,
        paper_id: str,
        paper_text: str,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> ExtractionResult:
        """
        Extract entities from paper text using two sequential LLM calls.

        Args:
            paper_id: Unique paper identifier.
            paper_text: Full paper text or significant parsed portion.
            provider: Optional LLM provider override.
            overrides: Optional base settings such as model, context_size, and
                timeout_seconds. Per-call extraction settings override
                temperature, top_p, and max_tokens.

        Returns:
            ExtractionResult with merged structural and semantic outputs.
            Model or parsing failures return partial results rather than
            raising, with parse quality details in raw_response.
        """
        started = time.perf_counter()
        source_text = self._clean_extraction_source_text(paper_text)
        extraction_text = self._text_before_references(source_text)
        scan = self._scan_paper_text(extraction_text)
        semantic_text = self._build_extraction_text(extraction_text, max_chars=30000)
        base_overrides = dict(overrides or {})
        extraction_mode = self._normalize_extraction_mode(base_overrides.pop("extraction_mode", None))
        context_policy = normalize_context_policy(base_overrides.pop("context_policy", None))
        allow_context_fallback = self._coerce_bool(base_overrides.pop("allow_context_fallback", False))
        base_overrides["context_size"] = self._effective_context_size(provider, base_overrides)

        fallback_chunks = self._build_extraction_chunks(
            extraction_text,
            context_size=int(base_overrides["context_size"]),
        )
        structural_max_tokens = max(5000, min(int(base_overrides.get("max_tokens") or 10000), 12000))
        context_size, max_tokens, resolved_model = effective_generation_limits(
            self.llm,
            provider,
            {**base_overrides, "max_tokens": structural_max_tokens},
        )
        context_decision = decide_whole_context(
            text=extraction_text,
            context_policy=context_policy,
            context_size=context_size,
            max_tokens=max_tokens,
            prompt_overhead_tokens=5200,
            output_reserve_tokens=max_tokens,
            chunk_count_if_fallback=len(fallback_chunks),
            provider=provider,
            model=resolved_model,
        )
        if (
            context_policy == "whole"
            and not context_decision.whole_context_used
            and not allow_context_fallback
        ):
            return self._context_budget_failure_result(
                paper_id=paper_id,
                source_text=source_text,
                extraction_text=extraction_text,
                scan=scan,
                extraction_mode=extraction_mode,
                context_decision=context_decision,
                provider=provider,
                overrides=base_overrides,
                started=started,
            )

        chunks = [extraction_text] if context_decision.whole_context_used else fallback_chunks
        if not context_decision.whole_context_used and context_decision.chunk_count != len(chunks):
            context_decision = ContextBudgetDecision(
                **{**context_decision.to_dict(), "chunk_count": len(chunks)}
            )
        structural_calls = [
            self._run_structural_call(
                chunk,
                provider,
                base_overrides,
                scan=scan,
                chunk_index=index,
                chunk_count=len(chunks),
            )
            for index, chunk in enumerate(chunks, start=1)
        ]
        concepts = self._merge_entity_lists(
            *[self._coerce_list(call.data.get("concepts")) for call in structural_calls],
        )
        methods = self._merge_entity_lists(
            *[self._coerce_list(call.data.get("methods")) for call in structural_calls],
        )
        concept_candidates = self._merge_entity_lists(
            scan.concepts,
            *[self._coerce_list(call.data.get("concept_candidates")) for call in structural_calls],
        )
        method_candidates = self._merge_entity_lists(
            scan.methods,
            *[self._coerce_list(call.data.get("method_candidates")) for call in structural_calls],
        )
        concepts_retry: ParsedLLMResponse | None = None
        if self._should_retry_concepts(concepts, structural_calls, scan):
            logger.warning("Structural concept extraction failed or was too thin; running concepts-only retry")
            concepts_retry = self._run_concepts_only_retry(extraction_text, provider, base_overrides, scan)
            retry_concepts = self._coerce_list(concepts_retry.data.get("concepts"))
            if retry_concepts:
                concepts = self._merge_entity_lists(concepts, retry_concepts)
            else:
                logger.error(
                    "Concepts-only retry failed for paper_id=%s; keeping deterministic candidates only",
                    paper_id,
                )
        methods_retry: ParsedLLMResponse | None = None
        if self._should_retry_methods(methods, structural_calls, concepts, scan):
            logger.warning("Methods lost in partial recovery — running methods-only retry")
            methods_retry = self._run_methods_only_retry(extraction_text, provider, base_overrides)
            retry_methods = self._coerce_list(methods_retry.data.get("methods"))
            if retry_methods:
                methods = self._merge_entity_lists(retry_methods)
            else:
                methods = []
                logger.error(
                    "Methods-only retry failed for paper_id=%s after partial recovery; setting methods to []",
                    paper_id,
                )

        regex_result = self._validate_concepts_with_regex(extraction_text, concept_candidates)
        concept_candidates = regex_result.concepts
        concepts = filter_concepts(
            concepts,
            title=self._paper_title_from_text(extraction_text),
        )
        concepts = self._post_process_concepts(concepts)
        concepts = self._calibrate_concept_confidences(extraction_text, concepts)
        concept_candidates = filter_concepts(
            concept_candidates,
            title=self._paper_title_from_text(extraction_text),
        )
        concept_candidates = self._post_process_concepts(concept_candidates)
        concept_candidates = self._calibrate_concept_confidences(extraction_text, concept_candidates)
        methods = enrich_method_domains(deduplicate_methods(methods))
        method_candidates = enrich_method_domains(deduplicate_methods(method_candidates))

        detected_paper_type = self._detect_paper_type(extraction_text)
        raw_concepts = concepts
        raw_methods = methods
        concepts = self._accept_concepts(extraction_text, raw_concepts, detected_paper_type)
        methods = self._accept_methods(extraction_text, raw_methods, detected_paper_type)
        concept_candidates = self._merge_entity_lists(
            concept_candidates,
            self._rejected_as_candidates(raw_concepts, concepts, "not_accepted_for_auto_kg"),
        )
        method_candidates = self._merge_entity_lists(
            method_candidates,
            self._rejected_as_candidates(raw_methods, methods, "not_accepted_for_auto_kg"),
        )
        concept_candidates = self._candidate_only(extraction_text, concept_candidates, concepts, default_role="possible_concept")
        method_candidates = self._candidate_only(extraction_text, method_candidates, methods, default_role="method_candidate")

        claims_pass: ParsedLLMResponse | None = None
        semantic_retry: ParsedLLMResponse | None = None
        if extraction_mode == "quick":
            semantic = ParsedLLMResponse(
                data={
                    "paper_type": detected_paper_type or "research",
                    "paper_node": {},
                    "claims": [],
                    "cross_domain_hints": [],
                    "terminology_conflicts": [],
                    "temporal_coverage": {},
                    "mathematical_content": {"has_formulas": False, "formula_types": []},
                    "language_detected": "en",
                },
                parse_quality="skipped",
                raw_text="semantic extraction skipped in quick mode",
                tokens_used=0,
            )
            semantic_data = semantic.data
            claims: list[dict[str, Any]] = []
            cross_domain_hints: list[dict[str, Any]] = []
            terminology_conflicts: list[dict[str, Any]] = []
        else:
            semantic = self._run_semantic_call(
                text_summary=semantic_text,
                structural_data={"concepts": concepts, "methods": methods},
                provider=provider,
                base_overrides=base_overrides,
            )
            semantic_data = semantic.data
            claims = self._merge_claim_lists(self._coerce_list(semantic_data.get("claims")))
            cross_domain_hints = self._coerce_list(semantic_data.get("cross_domain_hints"))
            terminology_conflicts = self._coerce_list(semantic_data.get("terminology_conflicts"))
            if self._should_retry_semantic_lists(
                semantic,
                claims,
                cross_domain_hints,
                terminology_conflicts,
                concepts,
                extraction_text,
            ):
                logger.warning("Semantic extraction too thin — running claims/hints retry")
                semantic_retry = self._run_semantic_lists_retry(semantic_text, provider, base_overrides)
                retry_data = semantic_retry.data
                claims = self._coerce_list(retry_data.get("claims")) or claims
                cross_domain_hints = self._coerce_list(retry_data.get("cross_domain_hints")) or cross_domain_hints
                terminology_conflicts = self._coerce_list(retry_data.get("terminology_conflicts")) or terminology_conflicts
                if not claims and semantic.parse_quality == "partial":
                    logger.error(
                        "Claims retry failed for paper_id=%s after partial semantic recovery; setting claims to []",
                        paper_id,
                    )
            if self._should_run_dedicated_claims_pass(extraction_text, claims):
                claims_pass = self._run_claims_call(semantic_text, provider, base_overrides)
                claims = self._merge_claim_lists(claims, self._coerce_list(claims_pass.data.get("claims")))
            claims = claims or self._fallback_claims_from_text(extraction_text, paper_type_hint=detected_paper_type)
            cross_domain_hints = cross_domain_hints or self._fallback_cross_domain_hints(concepts)
            terminology_conflicts = self._merge_terminology_conflicts(
                terminology_conflicts,
                self._fallback_terminology_conflicts([*concepts, *concept_candidates]),
            )
            terminology_conflicts = self._filter_terminology_conflicts(
                terminology_conflicts,
                [*concepts, *methods, *concept_candidates, *method_candidates],
            )

        mathematical_content = self._coerce_dict(semantic_data.get("mathematical_content"))
        if regex_result.has_formulas:
            formula_types = {
                str(item)
                for item in self._coerce_list(mathematical_content.get("formula_types"))
                if item
            }
            formula_types.update(regex_result.formula_types)
            mathematical_content["has_formulas"] = True
            mathematical_content["formula_types"] = sorted(formula_types)
        else:
            mathematical_content.setdefault("has_formulas", False)
            mathematical_content.setdefault("formula_types", [])

        temporal_coverage = self._coerce_dict(semantic_data.get("temporal_coverage"))
        if scan.paper_year and not temporal_coverage.get("paper_year"):
            temporal_coverage["paper_year"] = scan.paper_year

        paper_type = self._resolve_paper_type(semantic_data.get("paper_type"), detected_paper_type, extraction_text)
        paper_node = self._build_paper_node(
            paper_id=paper_id,
            paper_text=source_text,
            paper_type=paper_type,
            semantic_paper_node=self._coerce_dict(semantic_data.get("paper_node")),
            temporal_coverage=temporal_coverage,
            language_detected=str(semantic_data.get("language_detected") or "en"),
        )
        if paper_node.get("paper_year"):
            temporal_coverage["paper_year"] = paper_node.get("paper_year")
        result_paper_id = str(paper_node.get("paper_id") or paper_id)
        structural_parse_quality = self._chunked_parse_quality(structural_calls)
        parse_quality = self._combined_parse_quality(
            structural_parse_quality,
            "clean" if extraction_mode == "quick" else self._worst_parse_quality(
                [semantic.parse_quality]
                + ([claims_pass.parse_quality] if claims_pass is not None else [])
            ),
        )
        duration = time.perf_counter() - started
        metadata_validation = self._metadata_validation(
            paper_id=paper_id,
            paper_node=paper_node,
        )
        warnings = self._quality_warnings(
            paper_type=paper_type,
            concept_count=len(concepts),
            method_count=len(methods),
            text_length=len(extraction_text or ""),
            parse_quality=parse_quality,
            paper_id=paper_id,
            paper_node=paper_node,
        )

        call_diagnostics = self._call_diagnostics(
            structural_calls,
            semantic,
            claims_pass,
            concepts_retry=concepts_retry,
            methods_retry=methods_retry,
            semantic_retry=semantic_retry,
        )
        fatal_failure_reason = self._fatal_extraction_failure_reason(
            parse_quality=parse_quality,
            concepts=concepts,
            methods=methods,
            call_diagnostics=call_diagnostics,
        )

        result_payload = {
            "paper_type": paper_type,
            "paper_node": paper_node,
            "concepts": concepts,
            "methods": methods,
            "concept_candidates": concept_candidates,
            "method_candidates": method_candidates,
            "relations": [],
            "claims": claims,
            "cross_domain_hints": cross_domain_hints,
            "terminology_conflicts": terminology_conflicts,
            "temporal_coverage": temporal_coverage,
            "mathematical_content": mathematical_content,
            "language_detected": str(semantic_data.get("language_detected") or "en"),
            "extraction_parse_quality": parse_quality,
            "auto_detected_concepts": regex_result.auto_detected_count,
            "deterministic_candidate_count": len(concept_candidates) + len(method_candidates),
            "quality_warnings": warnings,
            "metadata_status": metadata_validation["metadata_status"],
            "blocking_errors": metadata_validation["blocking_errors"],
            "chunk_count": len(chunks),
            "extraction_mode": extraction_mode,
            "context_diagnostics": context_decision.to_dict(),
            "context_policy": context_decision.context_policy,
            "whole_context_used": context_decision.whole_context_used,
            "context_margin_tokens": context_decision.context_margin_tokens,
            "call_1_parse_quality": structural_parse_quality,
            "call_2_parse_quality": semantic.parse_quality,
            "concepts_retry_parse_quality": concepts_retry.parse_quality if concepts_retry else None,
            "methods_retry_parse_quality": methods_retry.parse_quality if methods_retry else None,
            "semantic_retry_parse_quality": semantic_retry.parse_quality if semantic_retry else None,
            "claims_pass_parse_quality": claims_pass.parse_quality if claims_pass else None,
            "fatal_llm_error": bool(fatal_failure_reason),
            "failure_reason": fatal_failure_reason,
            "call_diagnostics": call_diagnostics,
        }

        self._log_count_warnings(paper_type, len(concepts), warnings)
        self._write_quality_record(
            paper_id=result_paper_id,
            payload=result_payload,
            duration_seconds=duration,
            provider=provider,
            overrides=base_overrides,
            call_1_tokens_used=sum(
                call.tokens_used or self._estimate_tokens(call.raw_text)
                for call in structural_calls
            ),
            call_2_tokens_used=semantic.tokens_used or self._estimate_tokens(semantic.raw_text),
        )

        return ExtractionResult(
            paper_id=result_paper_id,
            paper_type=paper_type,
            paper_node=paper_node,
            concepts=concepts,
            methods=methods,
            concept_candidates=concept_candidates,
            method_candidates=method_candidates,
            relations=[],
            claims=result_payload["claims"],
            cross_domain_hints=result_payload["cross_domain_hints"],
            terminology_conflicts=result_payload["terminology_conflicts"],
            temporal_coverage=result_payload["temporal_coverage"],
            mathematical_content=mathematical_content,
            language_detected=result_payload["language_detected"],
            quality_warnings=warnings,
            metadata_status=result_payload["metadata_status"],
            blocking_errors=result_payload["blocking_errors"],
            candidate_count=int(result_payload["deterministic_candidate_count"]),
            extraction_diagnostics={
                "chunk_count": len(chunks),
                "parse_quality": parse_quality,
                "call_1_parse_quality": result_payload["call_1_parse_quality"],
                "call_2_parse_quality": semantic.parse_quality,
                "concepts_retry_parse_quality": result_payload["concepts_retry_parse_quality"],
                "methods_retry_parse_quality": result_payload["methods_retry_parse_quality"],
                "semantic_retry_parse_quality": result_payload["semantic_retry_parse_quality"],
                "claims_pass_parse_quality": result_payload["claims_pass_parse_quality"],
                "fatal_llm_error": bool(fatal_failure_reason),
                "failure_reason": fatal_failure_reason,
                "context_diagnostics": context_decision.to_dict(),
                "calls": result_payload["call_diagnostics"],
            },
            raw_response=json.dumps(result_payload, indent=2, ensure_ascii=False),
            extraction_mode=extraction_mode,
        )

    def _context_budget_failure_result(
        self,
        *,
        paper_id: str,
        source_text: str,
        extraction_text: str,
        scan: DeterministicScanResult,
        extraction_mode: str,
        context_decision: ContextBudgetDecision,
        provider: str | None,
        overrides: dict[str, Any],
        started: float,
    ) -> ExtractionResult:
        failure_reason = (
            "Whole-paper context does not fit the configured model context. "
            "Use context_policy='auto' or 'chunk', increase context_size, reduce max_tokens, "
            "or pass allow_context_fallback=true."
        )
        detected_paper_type = self._detect_paper_type(extraction_text)
        temporal_coverage: dict[str, Any] = {}
        if scan.paper_year:
            temporal_coverage["paper_year"] = scan.paper_year
        paper_type = self._resolve_paper_type(None, detected_paper_type, extraction_text)
        paper_node = self._build_paper_node(
            paper_id=paper_id,
            paper_text=source_text,
            paper_type=paper_type,
            semantic_paper_node={},
            temporal_coverage=temporal_coverage,
            language_detected="en",
        )
        result_paper_id = str(paper_node.get("paper_id") or paper_id)
        duration = time.perf_counter() - started
        warnings = [
            "Whole-paper context budget exceeded before the LLM call.",
            f"Context margin tokens: {context_decision.context_margin_tokens}",
        ]
        result_payload = {
            "paper_type": paper_type,
            "paper_node": paper_node,
            "concepts": [],
            "methods": [],
            "concept_candidates": scan.concepts,
            "method_candidates": scan.methods,
            "relations": [],
            "claims": [],
            "cross_domain_hints": [],
            "terminology_conflicts": [],
            "temporal_coverage": temporal_coverage,
            "mathematical_content": {"has_formulas": False, "formula_types": []},
            "language_detected": "en",
            "extraction_parse_quality": "failed",
            "auto_detected_concepts": len(scan.concepts),
            "deterministic_candidate_count": len(scan.concepts) + len(scan.methods),
            "quality_warnings": warnings,
            "metadata_status": "valid",
            "blocking_errors": [],
            "chunk_count": context_decision.chunk_count,
            "extraction_mode": extraction_mode,
            "context_diagnostics": context_decision.to_dict(),
            "context_policy": context_decision.context_policy,
            "whole_context_used": context_decision.whole_context_used,
            "context_margin_tokens": context_decision.context_margin_tokens,
            "call_1_parse_quality": "failed",
            "call_2_parse_quality": "skipped",
            "concepts_retry_parse_quality": None,
            "methods_retry_parse_quality": None,
            "semantic_retry_parse_quality": None,
            "claims_pass_parse_quality": None,
            "fatal_llm_error": True,
            "failure_reason": failure_reason,
            "call_diagnostics": [],
        }
        self._write_quality_record(
            paper_id=result_paper_id,
            payload=result_payload,
            duration_seconds=duration,
            provider=provider,
            overrides=overrides,
            call_1_tokens_used=0,
            call_2_tokens_used=0,
        )
        return ExtractionResult(
            paper_id=result_paper_id,
            paper_type=paper_type,
            paper_node=paper_node,
            concepts=[],
            methods=[],
            concept_candidates=scan.concepts,
            method_candidates=scan.methods,
            relations=[],
            claims=[],
            cross_domain_hints=[],
            terminology_conflicts=[],
            temporal_coverage=temporal_coverage,
            mathematical_content=result_payload["mathematical_content"],
            language_detected="en",
            quality_warnings=warnings,
            metadata_status="valid",
            blocking_errors=[],
            candidate_count=len(scan.concepts) + len(scan.methods),
            extraction_diagnostics={
                "chunk_count": context_decision.chunk_count,
                "parse_quality": "failed",
                "call_1_parse_quality": "failed",
                "call_2_parse_quality": "skipped",
                "fatal_llm_error": True,
                "failure_reason": failure_reason,
                "context_diagnostics": context_decision.to_dict(),
                "calls": [],
            },
            raw_response=json.dumps(result_payload, indent=2, ensure_ascii=False),
            extraction_mode=extraction_mode,
        )

    @staticmethod
    def _call_overrides(
        base: dict[str, Any],
        max_tokens: int,
        temperature: float,
        top_p: float,
        json_object: bool = True,
    ) -> dict[str, Any]:
        """Merge per-call Ollama/OpenAI-compatible generation settings."""
        overrides = dict(base)
        overrides["max_tokens"] = max_tokens
        overrides["temperature"] = temperature
        overrides["top_p"] = top_p
        extra = dict(overrides.get("extra") or {})
        extra["json_mode"] = True
        extra["format"] = "json"
        if json_object:
            extra.setdefault("response_format", {"type": "json_object"})
        else:
            extra.pop("response_format", None)
        chat_template_kwargs = dict(extra.get("chat_template_kwargs") or {})
        chat_template_kwargs.setdefault("enable_thinking", False)
        extra["chat_template_kwargs"] = chat_template_kwargs
        overrides["extra"] = extra
        return overrides

    def _effective_context_size(self, provider: str | None, overrides: dict[str, Any]) -> int:
        """Cap UI overrides to the selected provider's configured context when available."""
        requested = overrides.get("context_size")
        try:
            context_size = int(requested) if requested is not None else 32768
        except (TypeError, ValueError):
            context_size = 32768

        try:
            provider_settings = self.llm.provider_settings(provider)  # type: ignore[attr-defined]
        except Exception:
            return context_size

        configured = getattr(provider_settings, "context_size", None)
        try:
            configured_context = int(configured) if configured is not None else context_size
        except (TypeError, ValueError):
            configured_context = context_size
        return max(1024, min(context_size, configured_context))

    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """
        Estimate output tokens when provider usage metadata is unavailable.

        LLMRouter currently returns only content text, so this is a stable local
        estimate rather than Ollama's eval_count.
        """
        if not text:
            return 0
        return max(1, len(text) // 4)

    def _last_tokens_used(self) -> int | None:
        """Read provider token usage captured by LLMRouter, when available."""
        metadata = getattr(self.llm, "last_response_metadata", {}) or {}
        eval_count = metadata.get("eval_count")
        if eval_count is not None:
            try:
                return int(eval_count)
            except (TypeError, ValueError):
                return None

        usage = metadata.get("usage") or {}
        completion_tokens = usage.get("completion_tokens")
        if completion_tokens is not None:
            try:
                return int(completion_tokens)
            except (TypeError, ValueError):
                return None
        return None

    def _model_name(self, provider: str | None, overrides: dict[str, Any]) -> str:
        """Resolve model name for quality telemetry."""
        if overrides.get("model"):
            return str(overrides["model"])
        try:
            return str(self.llm.provider_settings(provider).model)
        except Exception:
            return "unknown"

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None and value != "" else None
        except (TypeError, ValueError):
            return None
