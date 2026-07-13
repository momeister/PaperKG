"""EntityExtractor: Qualitaets-Warnungen, Diagnostik, Paper-Node und Quality-Records. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from extraction.entity_extractor._shared import (
    ParsedLLMResponse,
)

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class QualityMixin(_Base):
    """Qualitaets-Warnungen, Diagnostik, Paper-Node und Quality-Records."""

    @staticmethod
    def _combined_parse_quality(call_1_quality: str, call_2_quality: str) -> str:
        """Combine per-call parse quality into one quality label."""
        order = {"clean": 0, "trimmed": 1, "partial": 2, "failed": 3}
        worst = max((call_1_quality, call_2_quality), key=lambda item: order.get(item, 2))
        return worst if worst in order else "partial"

    @staticmethod
    def _diagnostic_excerpt(raw_text: str, max_chars: int = 360) -> str:
        """Return a compact diagnostic excerpt without flooding stored payloads."""
        excerpt = re.sub(r"\s+", " ", str(raw_text or "")).strip()
        if len(excerpt) > max_chars:
            excerpt = excerpt[:max_chars] + "..."
        return excerpt

    @classmethod
    def _fatal_extraction_failure_reason(
        cls,
        parse_quality: str,
        concepts: list[dict[str, Any]],
        methods: list[dict[str, Any]],
        call_diagnostics: list[dict[str, Any]],
    ) -> str | None:
        """Detect model-level failures that should not be written as successes."""
        if parse_quality != "failed" or concepts or methods:
            return None
        calls = [
            call
            for call in call_diagnostics
            if isinstance(call, dict) and str(call.get("call_type") or "") != "claims_retry"
        ]
        if not calls:
            return "LLM extraction failed before usable JSON could be produced."
        if any(str(call.get("parse_quality") or "") not in {"failed", "skipped"} for call in calls):
            return None
        failed_calls = [call for call in calls if str(call.get("parse_quality") or "") == "failed"]
        if not failed_calls:
            return None
        excerpts = " ".join(str(call.get("raw_excerpt") or "") for call in failed_calls)
        if "No models loaded" in excerpts:
            return "LLM extraction failed: LM Studio has no model loaded."
        if "LLM call failed" in excerpts or "retry failed" in excerpts:
            return "LLM extraction failed for every extraction call; no KG-safe entities were produced."
        return "LLM extraction failed before usable JSON could be produced."

    @classmethod
    def _call_diagnostics(cls, 
        structural_calls: list[ParsedLLMResponse],
        semantic: ParsedLLMResponse,
        claims_pass: ParsedLLMResponse | None,
        concepts_retry: ParsedLLMResponse | None = None,
        methods_retry: ParsedLLMResponse | None = None,
        semantic_retry: ParsedLLMResponse | None = None,
    ) -> list[dict[str, Any]]:
        """Return per-call parse diagnostics for review and benchmark gates."""
        diagnostics: list[dict[str, Any]] = []
        structural_keys = {"concepts", "methods", "concept_candidates", "method_candidates"}
        for index, call in enumerate(structural_calls, start=1):
            data_keys = set(call.data.keys())
            row = {
                "call_type": "structural",
                "chunk_index": index,
                "parse_quality": call.parse_quality,
                "missing_keys": sorted(structural_keys - data_keys),
                "tokens_used": call.tokens_used,
                "recovery_strategy": "split_retry"
                if "--- SPLIT STRUCTURAL RETRY ---" in call.raw_text
                else None,
            }
            if call.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = cls._diagnostic_excerpt(call.raw_text)
            diagnostics.append(row)
        if concepts_retry is not None:
            row = {
                "call_type": "concepts_retry",
                "chunk_index": None,
                "parse_quality": concepts_retry.parse_quality,
                "missing_keys": [] if "concepts" in concepts_retry.data else ["concepts"],
                "tokens_used": concepts_retry.tokens_used,
            }
            if concepts_retry.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = cls._diagnostic_excerpt(concepts_retry.raw_text)
            diagnostics.append(row)
        if methods_retry is not None:
            row = {
                "call_type": "methods_retry",
                "chunk_index": None,
                "parse_quality": methods_retry.parse_quality,
                "missing_keys": [] if "methods" in methods_retry.data else ["methods"],
                "tokens_used": methods_retry.tokens_used,
            }
            if methods_retry.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = cls._diagnostic_excerpt(methods_retry.raw_text)
            diagnostics.append(row)
        if semantic_retry is not None:
            row = {
                "call_type": "semantic_retry",
                "chunk_index": None,
                "parse_quality": semantic_retry.parse_quality,
                "missing_keys": [],
                "tokens_used": semantic_retry.tokens_used,
            }
            if semantic_retry.parse_quality in {"partial", "failed"}:
                row["raw_excerpt"] = cls._diagnostic_excerpt(semantic_retry.raw_text)
            diagnostics.append(row)
        semantic_keys = {
            "paper_type",
            "paper_node",
            "claims",
            "cross_domain_hints",
            "terminology_conflicts",
            "temporal_coverage",
            "mathematical_content",
            "language_detected",
        }
        semantic_row = {
            "call_type": "semantic",
            "chunk_index": None,
            "parse_quality": semantic.parse_quality,
            "missing_keys": sorted(semantic_keys - set(semantic.data.keys())),
            "tokens_used": semantic.tokens_used,
        }
        if semantic.parse_quality in {"partial", "failed"}:
            semantic_row["raw_excerpt"] = cls._diagnostic_excerpt(semantic.raw_text)
        diagnostics.append(semantic_row)
        if claims_pass is not None:
            diagnostics.append(
                {
                    "call_type": "claims_retry",
                    "chunk_index": None,
                    "parse_quality": claims_pass.parse_quality,
                    "missing_keys": [] if "claims" in claims_pass.data else ["claims"],
                    "tokens_used": claims_pass.tokens_used,
                }
            )
        return diagnostics

    @classmethod
    def _build_paper_node(
        cls,
        paper_id: str,
        paper_text: str,
        paper_type: str,
        semantic_paper_node: dict[str, Any],
        temporal_coverage: dict[str, Any],
        language_detected: str,
    ) -> dict[str, Any]:
        """Materialize the extraction's Paper node anchor independent of LLM recall."""
        semantic_title = str(semantic_paper_node.get("title") or "").strip()
        text_title = cls._paper_title_from_text(paper_text)
        title = semantic_title or text_title
        title_conflict = bool(semantic_title and text_title and cls._titles_conflict(semantic_title, text_title))
        if title_conflict:
            title = text_title
        paper_year = semantic_paper_node.get("paper_year") or temporal_coverage.get("paper_year")
        reviewed_period = semantic_paper_node.get("reviewed_period") or temporal_coverage.get("reviewed_period")
        detected_source_id = cls._extract_front_matter_arxiv_identifier(paper_text)
        requested_arxiv_id = cls._extract_arxiv_identifier(str(paper_id))
        canonical_paper_id = requested_arxiv_id or detected_source_id or str(paper_id)
        authoritative_arxiv_id = ""
        if requested_arxiv_id and (not detected_source_id or detected_source_id == requested_arxiv_id):
            authoritative_arxiv_id = requested_arxiv_id
        elif detected_source_id and not requested_arxiv_id:
            authoritative_arxiv_id = detected_source_id
        arxiv_year = cls._arxiv_publication_year(authoritative_arxiv_id)
        llm_paper_year = cls._coerce_year(paper_year)
        year_conflict = bool(arxiv_year and llm_paper_year and abs(arxiv_year - llm_paper_year) > 2)
        if arxiv_year and (paper_year in (None, "") or year_conflict):
            paper_year = arxiv_year
        node = {
            "node_type": "Paper",
            "paper_id": canonical_paper_id,
            "title": title,
            "paper_type": cls._normalize_paper_type(paper_type),
            "paper_year": paper_year,
            "reviewed_period": reviewed_period,
            "language_detected": language_detected or "en",
            "source": "extraction",
        }
        if detected_source_id and requested_arxiv_id and detected_source_id != requested_arxiv_id:
            node["detected_source_id"] = detected_source_id
        if title_conflict:
            node["detected_title"] = text_title
            node["llm_paper_title"] = semantic_title
        if year_conflict:
            node["llm_paper_year"] = llm_paper_year
            node["paper_year_source"] = "arxiv_id"
        return {key: value for key, value in node.items() if value not in (None, "", [], {})}

    @staticmethod
    def _worst_parse_quality(qualities: list[str]) -> str:
        if not qualities:
            return "partial"
        order = {"clean": 0, "trimmed": 1, "partial": 2, "failed": 3}
        worst = max(qualities, key=lambda item: order.get(item, 2))
        return worst if worst in order else "partial"

    @classmethod
    def _chunked_parse_quality(cls, calls: list[ParsedLLMResponse]) -> str:
        """Aggregate chunked structural calls without letting one failed chunk erase useful chunks."""
        if not calls:
            return "partial"
        qualities = [str(call.parse_quality or "partial") for call in calls]
        if any(quality == "failed" for quality in qualities) and any(
            quality in {"clean", "trimmed"} for quality in qualities
        ):
            return "partial"
        return cls._worst_parse_quality(qualities)

    @classmethod
    def _quality_warnings(
        cls,
        paper_type: str,
        concept_count: int,
        method_count: int,
        text_length: int,
        parse_quality: str,
        paper_id: str | None = None,
        paper_node: dict[str, Any] | None = None,
    ) -> list[str]:
        warnings: list[str] = []
        if parse_quality == "partial":
            warnings.append("One or more LLM JSON responses required partial recovery.")
        elif parse_quality == "failed":
            warnings.append("One or more LLM extraction calls failed; deterministic fallbacks may be incomplete.")
        if text_length >= 20000 and concept_count < 12:
            warnings.append(
                "Full-length paper produced fewer than 12 concepts; review extraction coverage."
            )
        if paper_type == "survey" and concept_count < 30:
            warnings.append(
                "Survey paper produced fewer than 30 concepts; reviewed methods may be under-extracted."
            )
        if text_length >= 20000 and method_count == 0:
            warnings.append("Full-length paper produced no methods; review method extraction.")
        warnings.extend(cls._paper_identity_warnings(paper_id, paper_node or {}))
        return warnings

    @classmethod
    def _paper_identity_warnings(
        cls,
        paper_id: str | None,
        paper_node: dict[str, Any],
    ) -> list[str]:
        warnings: list[str] = []
        node_paper_id = str(paper_node.get("paper_id") or paper_id or "")
        arxiv_id = cls._extract_arxiv_identifier(node_paper_id)
        arxiv_year = cls._arxiv_publication_year(arxiv_id)
        paper_year = cls._coerce_year(paper_node.get("paper_year"))
        if arxiv_year and paper_year and abs(arxiv_year - paper_year) > 2:
            warnings.append(
                f"Paper id {arxiv_id} implies year {arxiv_year}, "
                f"but extracted paper_year is {paper_year}; verify paper metadata."
            )
        llm_paper_year = cls._coerce_year(paper_node.get("llm_paper_year"))
        if arxiv_year and llm_paper_year and abs(arxiv_year - llm_paper_year) > 2:
            warnings.append(
                f"LLM extracted paper_year={llm_paper_year}, but paper id {arxiv_id} "
                f"implies {arxiv_year}; using arXiv metadata year."
            )

        detected_source_id = cls._extract_arxiv_identifier(str(paper_node.get("detected_source_id") or ""))
        if arxiv_id and detected_source_id and detected_source_id != arxiv_id:
            warnings.append(
                f"Paper text contains {detected_source_id}, "
                f"but extraction paper_id is {arxiv_id}; verify source identity."
            )
        detected_title = str(paper_node.get("detected_title") or "")
        llm_title = str(paper_node.get("llm_paper_title") or "")
        if detected_title and llm_title and cls._titles_conflict(detected_title, llm_title):
            warnings.append(
                "Parsed paper title conflicts with LLM/external title; verify the selected paper text."
            )
        return warnings

    @classmethod
    def _metadata_validation(
        cls,
        paper_id: str | None,
        paper_node: dict[str, Any],
    ) -> dict[str, Any]:
        blocking_errors: list[str] = []
        node_paper_id = str(paper_node.get("paper_id") or paper_id or "")
        supplied_arxiv_id = cls._extract_arxiv_identifier(node_paper_id)
        detected_source_id = cls._extract_arxiv_identifier(str(paper_node.get("detected_source_id") or ""))
        if supplied_arxiv_id and detected_source_id and detected_source_id != supplied_arxiv_id:
            blocking_errors.append(
                f"paper_id_mismatch: supplied {supplied_arxiv_id}, extracted {detected_source_id}"
            )

        arxiv_year = cls._arxiv_publication_year(supplied_arxiv_id)
        paper_year = cls._coerce_year(paper_node.get("paper_year"))
        if arxiv_year and paper_year and abs(arxiv_year - paper_year) > 2:
            blocking_errors.append(
                f"paper_id_year_mismatch: supplied {supplied_arxiv_id} implies {arxiv_year}, "
                f"extracted paper_year={paper_year}"
            )

        detected_title = str(paper_node.get("detected_title") or "")
        llm_title = str(paper_node.get("llm_paper_title") or "")
        if detected_title and llm_title and cls._titles_conflict(detected_title, llm_title):
            blocking_errors.append(
                "paper_title_mismatch: parsed title "
                f"'{detected_title[:120]}' conflicts with LLM/external title '{llm_title[:120]}'"
            )

        return {
            "metadata_status": "invalid" if blocking_errors else "valid",
            "blocking_errors": blocking_errors,
        }

    @staticmethod
    def _log_count_warnings(
        paper_type: str,
        concept_count: int,
        warnings: list[str] | None = None,
    ) -> None:
        """Log paper-type-aware concept count warnings for manual review."""
        if paper_type == "survey" and concept_count < 30:
            logger.warning(
                "Survey paper yielded only %s concepts - possible truncation. Consider manual review.",
                concept_count,
            )
        elif paper_type == "research" and concept_count < 8:
            logger.warning("Research paper yielded only %s concepts.", concept_count)
        for warning in warnings or []:
            logger.warning("Extraction quality warning: %s", warning)

    def _write_quality_record(
        self,
        paper_id: str,
        payload: dict[str, Any],
        duration_seconds: float,
        provider: str | None,
        overrides: dict[str, Any],
        call_1_tokens_used: int,
        call_2_tokens_used: int,
    ) -> None:
        """Persist quality telemetry without affecting extraction success."""
        if not self.quality_db_path:
            return
        try:
            from storage.metadata_db import MetadataDB

            with MetadataDB(self.quality_db_path) as db:
                context_diagnostics = payload.get("context_diagnostics") if isinstance(payload.get("context_diagnostics"), dict) else {}
                db.save_extraction_quality(
                    paper_id=paper_id,
                    concept_count=len(payload.get("concepts") or []),
                    method_count=len(payload.get("methods") or []),
                    claim_count=len(payload.get("claims") or []),
                    has_formulas=bool((payload.get("mathematical_content") or {}).get("has_formulas")),
                    auto_detected_concepts=int(payload.get("auto_detected_concepts") or 0),
                    parse_quality=str(payload.get("extraction_parse_quality") or "partial"),
                    call_1_tokens_used=call_1_tokens_used,
                    call_2_tokens_used=call_2_tokens_used,
                    duration_seconds=duration_seconds,
                    model=self._model_name(provider, overrides),
                    provider=provider,
                    context_policy=str(context_diagnostics.get("context_policy") or payload.get("context_policy") or ""),
                    whole_context_used=bool(context_diagnostics.get("whole_context_used") or payload.get("whole_context_used")),
                    chunk_count=self._optional_int(context_diagnostics.get("chunk_count") or payload.get("chunk_count")),
                    estimated_prompt_tokens=self._optional_int(context_diagnostics.get("estimated_prompt_tokens")),
                    context_margin_tokens=self._optional_int(context_diagnostics.get("context_margin_tokens") or payload.get("context_margin_tokens")),
                    context_fallback_reason=str(context_diagnostics.get("fallback_reason") or ""),
                )
        except Exception:
            logger.exception("Failed to persist extraction quality for paper_id=%s", paper_id)
