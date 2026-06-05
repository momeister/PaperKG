from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from query.context_budget import decide_whole_context, effective_generation_limits
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import Evidence, SearchHit, Source
from query.llm_router import LLMRouter
from query.source_verifier import find_pdf_path, parse_pdf_text, verify_answer_sources


@dataclass
class GroundedAnswer:
    question: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    citation_links: list[dict[str, Any]] = field(default_factory=list)
    no_answer: bool = False
    model: str | None = None
    generation_error: str | None = None
    context_diagnostics: dict[str, Any] = field(default_factory=dict)
    source_verification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "sources": [source.to_dict() for source in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "citation_links": self.citation_links,
            "no_answer": self.no_answer,
            "model": self.model,
            "generation_error": self.generation_error,
            "context_diagnostics": self.context_diagnostics,
            "source_verification": self.source_verification,
        }


class GroundedResponder:
    """
    Answers questions from retrieved KG evidence only.
    """

    MIN_ANSWER_TOKENS = 1200
    MAX_ANSWER_TOKENS = 8192
    MAX_INVALID_CITATION_RETRIES = 1

    SYSTEM_PROMPT = """You are ScienceKG's grounded research assistant.

Use only the evidence provided by the local knowledge graph. Do not add facts
from model training data. If the evidence is insufficient, say that the local
KG does not contain enough evidence. Cite paper IDs in square brackets when
making claims."""

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        llm_router: LLMRouter | None = None,
    ) -> None:
        self.retriever = retriever or HybridRetriever()
        self.llm_router = llm_router

    def answer(
        self,
        question: str,
        limit: int = 8,
        provider: str | None = None,
        model: str | None = None,
        overrides: dict[str, Any] | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
        paper_ids: list[str] | set[str] | None = None,
        priority_paper_ids: list[str] | set[str] | None = None,
        answer_context_mode: str = "kg",
        pdf_base_dir: str = "data/pdfs",
    ) -> GroundedAnswer:
        context_diagnostics: dict[str, Any] = {
            "answer_context_mode": answer_context_mode or "kg",
        }
        if str(answer_context_mode or "kg").strip().lower() == "pdf_if_fits":
            pdf_answer, pdf_diagnostics = self._answer_from_pdf_context_if_fits(
                question=question,
                provider=provider,
                model=model,
                overrides=overrides,
                conversation_context=conversation_context,
                paper_ids=paper_ids,
                pdf_base_dir=pdf_base_dir,
            )
            context_diagnostics.update(pdf_diagnostics)
            if pdf_answer is not None:
                return pdf_answer

        hits = self.retriever.search(question, limit=limit, paper_ids=paper_ids)
        priority_set = {str(pid) for pid in (priority_paper_ids or []) if pid}
        hits = _prioritize_hits(hits, priority_set)
        evidence = self._evidence_for_answer(hits, max_items=_evidence_item_limit(limit, hits))
        sources = [hit.source for hit in hits if hit.evidence]

        if not evidence:
            return GroundedAnswer(
                question=question,
                answer=f"No matching evidence was found in the local KG for: {question}",
                sources=[],
                evidence=[],
                citation_links=[],
                no_answer=True,
                model=model,
                context_diagnostics={**context_diagnostics, "fallback_reason": "no_kg_evidence"},
            )

        answer_text, generation_error = self._generate_answer(
            question=question,
            hits=hits,
            evidence=evidence,
            provider=provider,
            model=model,
            overrides=overrides,
            conversation_context=conversation_context,
            priority_paper_ids=priority_set,
        )
        cited_ids = _cited_paper_ids(answer_text)
        if cited_ids:
            cited_sources = [source for source in sources if source.paper_id in cited_ids]
            cited_evidence = [item for item in evidence if item.paper_id in cited_ids]
            if cited_sources:
                sources = cited_sources
            if cited_evidence:
                evidence = cited_evidence
        citation_links = _citation_links_for_answer(answer_text, evidence)
        return GroundedAnswer(
            question=question,
            answer=answer_text,
            sources=sources,
            evidence=evidence,
            citation_links=citation_links,
            no_answer=False,
            model=model or self._default_model(provider),
            generation_error=generation_error,
            context_diagnostics={**context_diagnostics, "answer_context_mode": "kg"},
        )

    def _answer_from_pdf_context_if_fits(
        self,
        *,
        question: str,
        provider: str | None,
        model: str | None,
        overrides: dict[str, Any] | None,
        conversation_context: list[dict[str, Any]] | None,
        paper_ids: list[str] | set[str] | None,
        pdf_base_dir: str,
    ) -> tuple[GroundedAnswer | None, dict[str, Any]]:
        diagnostics: dict[str, Any] = {"answer_context_mode": "pdf_if_fits"}
        if self.llm_router is None:
            diagnostics["fallback_reason"] = "no_llm_router"
            return None, diagnostics

        requested_ids = [str(item) for item in (paper_ids or []) if str(item or "").strip()]
        requested_ids = list(dict.fromkeys(requested_ids))
        if not requested_ids:
            diagnostics["fallback_reason"] = "no_explicit_paper_scope"
            return None, diagnostics
        if len(requested_ids) > 3:
            diagnostics["fallback_reason"] = "too_many_papers_for_pdf_context"
            diagnostics["paper_count"] = len(requested_ids)
            return None, diagnostics

        sources: list[Source] = []
        parsed_texts: list[tuple[Source, str, str]] = []
        missing: list[str] = []
        for paper_id in requested_ids:
            detail = self.retriever.paper_detail(paper_id) or {}
            source_payload = detail.get("source") or {}
            source = Source(
                paper_id=str(source_payload.get("paper_id") or paper_id),
                title=str(source_payload.get("title") or paper_id),
                year=_coerce_int(source_payload.get("year")),
                doi=source_payload.get("doi"),
                url=source_payload.get("url"),
            )
            pdf_path = find_pdf_path(source.paper_id, source.title, pdf_base_dir)
            if pdf_path is None:
                missing.append(source.paper_id)
                continue
            try:
                pdf_text = parse_pdf_text(str(pdf_path), source.paper_id)
            except Exception as exc:
                diagnostics.setdefault("pdf_errors", {})[source.paper_id] = str(exc)
                continue
            if not pdf_text.strip():
                diagnostics.setdefault("pdf_errors", {})[source.paper_id] = "empty parsed PDF text"
                continue
            sources.append(source)
            parsed_texts.append((source, str(pdf_path), pdf_text))

        if missing:
            diagnostics["missing_pdf_ids"] = missing
        if not parsed_texts:
            diagnostics["fallback_reason"] = "no_parseable_pdf_text"
            return None, diagnostics

        sections = [
            f"[{source.paper_id}] {source.title}\nPDF path: {pdf_path}\n\n{pdf_text}"
            for source, pdf_path, pdf_text in parsed_texts
        ]
        combined_text = "\n\n--- PAPER TEXT ---\n\n".join(sections)
        merged_overrides = {
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": self._answer_max_tokens(provider),
            **(overrides or {}),
        }
        if model:
            merged_overrides["model"] = model
        context_size, max_tokens, resolved_model = effective_generation_limits(
            self.llm_router,
            provider,
            merged_overrides,
            default_max_tokens=self.MIN_ANSWER_TOKENS,
        )
        decision = decide_whole_context(
            text=combined_text,
            context_policy="whole",
            context_size=context_size,
            max_tokens=max_tokens,
            prompt_overhead_tokens=1800,
            output_reserve_tokens=max_tokens,
            chunk_count_if_fallback=len(parsed_texts),
            provider=provider,
            model=resolved_model,
        )
        diagnostics.update(decision.to_dict())
        diagnostics["paper_count"] = len(parsed_texts)
        if not decision.whole_context_used:
            diagnostics["fallback_reason"] = decision.fallback_reason or "context_budget_exceeded"
            return None, diagnostics

        evidence = [
            Evidence(
                paper_id=source.paper_id,
                kind="pdf",
                field="parsed_pdf_text",
                text=_best_pdf_context_snippet(pdf_text, question),
                score=10.0,
                metadata={"title": source.title, "pdf_path": pdf_path, "context_policy": "whole"},
            )
            for source, pdf_path, pdf_text in parsed_texts
        ]
        prompt = _build_pdf_context_prompt(question, combined_text, conversation_context=conversation_context)
        try:
            response = self._chat_with_transient_retry(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                provider=provider,
                overrides=merged_overrides,
            )
        except Exception as exc:
            diagnostics["fallback_reason"] = "pdf_context_generation_failed"
            diagnostics["generation_error"] = str(exc)
            return None, diagnostics

        answer_text = str(response or "").strip()
        answer_text = self._repair_invalid_citations(
            response=answer_text,
            prompt=prompt,
            provider=provider,
            overrides=merged_overrides,
        )
        if not answer_text:
            diagnostics["fallback_reason"] = "empty_pdf_context_answer"
            return None, diagnostics

        cited_ids = _cited_paper_ids(answer_text)
        filtered_sources = [source for source in sources if not cited_ids or source.paper_id in cited_ids]
        filtered_evidence = [item for item in evidence if not cited_ids or item.paper_id in cited_ids]
        answer = GroundedAnswer(
            question=question,
            answer=answer_text,
            sources=filtered_sources or sources,
            evidence=filtered_evidence or evidence,
            citation_links=_citation_links_for_answer(answer_text, filtered_evidence or evidence),
            no_answer=False,
            model=model or resolved_model or self._default_model(provider),
            generation_error=None,
            context_diagnostics=diagnostics,
        )
        try:
            answer.source_verification = verify_answer_sources(
                answer.to_dict(),
                pdf_base_dir=pdf_base_dir,
                parse_pdfs=True,
                max_sources=10,
                max_evidence_per_source=5,
            ).to_dict()
        except Exception as exc:
            answer.context_diagnostics["source_verification_error"] = str(exc)
        return answer, diagnostics

    def _generate_answer(
        self,
        question: str,
        hits: list[SearchHit],
        evidence: list[Evidence],
        provider: str | None,
        model: str | None,
        overrides: dict[str, Any] | None,
        conversation_context: list[dict[str, Any]] | None = None,
        priority_paper_ids: set[str] | None = None,
    ) -> tuple[str, str | None]:
        if self.llm_router is None:
            return _extractive_answer(question, hits, evidence), None

        prompt = _build_grounded_prompt(
            question,
            hits,
            evidence,
            conversation_context=conversation_context,
            priority_paper_ids=priority_paper_ids,
        )
        merged_overrides = {
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": self._answer_max_tokens(provider),
            **(overrides or {}),
        }
        if model:
            merged_overrides["model"] = model

        try:
            response = self._chat_with_transient_retry(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                provider=provider,
                overrides=merged_overrides,
            )
        except Exception as exc:
            fallback = _extractive_answer(question, hits, evidence)
            return (
                "I could not generate a synthesized answer because the configured LLM call failed. "
                "Evidence-only fallback:\n" + fallback,
                str(exc),
            )

        response = str(response or "").strip()
        if not response and self._should_retry_empty_response(merged_overrides):
            retry_overrides = dict(merged_overrides)
            current_tokens = int(retry_overrides.get("max_tokens") or self.MIN_ANSWER_TOKENS)
            retry_overrides["max_tokens"] = min(
                max(current_tokens * 2, 4096),
                self.MAX_ANSWER_TOKENS,
            )
            if retry_overrides["max_tokens"] > current_tokens:
                try:
                    response = self._chat_with_transient_retry(
                        [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        provider=provider,
                        overrides=retry_overrides,
                    )
                except Exception as exc:
                    fallback = _extractive_answer(question, hits, evidence)
                    return (
                        "I could not generate a synthesized answer because the configured LLM call failed. "
                        "Evidence-only fallback:\n" + fallback,
                        str(exc),
                    )
                response = str(response or "").strip()

        response = self._repair_invalid_citations(
            response=response,
            prompt=prompt,
            provider=provider,
            overrides=merged_overrides,
        )
        response = self._repair_sparse_citations(
            response=response,
            prompt=prompt,
            provider=provider,
            overrides=merged_overrides,
            evidence=evidence,
        )

        if response:
            return response, None
        return (
            "I could not generate a synthesized answer because the configured LLM returned an empty response. "
            "Evidence-only fallback:\n" + _extractive_answer(question, hits, evidence),
            "empty_response",
        )

    def _chat_with_transient_retry(
        self,
        messages: list[dict[str, str]],
        provider: str | None,
        overrides: dict[str, Any],
    ) -> str:
        assert self.llm_router is not None
        try:
            return self.llm_router.chat(messages, provider=provider, overrides=overrides)
        except Exception as exc:
            if not _is_transient_generation_error(str(exc)):
                raise
            return self.llm_router.chat(messages, provider=provider, overrides=overrides)

    def _default_model(self, provider: str | None) -> str | None:
        if self.llm_router is None:
            return None
        try:
            return self.llm_router.provider_settings(provider).model
        except Exception:
            return None

    def _answer_max_tokens(self, provider: str | None) -> int:
        if self.llm_router is None:
            return self.MIN_ANSWER_TOKENS
        try:
            configured = int(self.llm_router.provider_settings(provider).max_tokens)
        except Exception:
            configured = self.MIN_ANSWER_TOKENS
        return min(max(configured, self.MIN_ANSWER_TOKENS), self.MAX_ANSWER_TOKENS)

    def _should_retry_empty_response(self, overrides: dict[str, Any]) -> bool:
        if self.llm_router is None:
            return False
        metadata = getattr(self.llm_router, "last_response_metadata", {}) or {}
        if metadata.get("finish_reason") == "length":
            return True
        usage = metadata.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = int(completion_details.get("reasoning_tokens") or 0)
        max_tokens = int(overrides.get("max_tokens") or self.MIN_ANSWER_TOKENS)
        return reasoning_tokens > 0 and reasoning_tokens >= max_tokens - 1

    def _evidence_for_answer(self, hits: list[SearchHit], max_items: int) -> list[Evidence]:
        evidence = _flatten_evidence(hits, max_items=max_items)
        paper_ids = [hit.source.paper_id for hit in hits[:3]]
        existing = {(item.paper_id, item.kind, item.text) for item in evidence}

        for paper_id in paper_ids:
            detail = self.retriever.paper_detail(paper_id)
            latest = (detail or {}).get("latest_extraction") or {}
            for item in _supplemental_evidence_from_extraction(paper_id, latest):
                key = (item.paper_id, item.kind, item.text)
                if key in existing:
                    continue
                existing.add(key)
                evidence.append(item)

        evidence.sort(key=lambda item: _answer_evidence_rank(item), reverse=True)
        return evidence[:max_items]

    def _repair_invalid_citations(
        self,
        response: str,
        prompt: str,
        provider: str | None,
        overrides: dict[str, Any],
    ) -> str:
        if self.llm_router is None or not response:
            return response
        if not _invalid_citations(response):
            return response

        repair_prompt = (
            f"{prompt}\n\n"
            "Your previous answer used invalid citations. Rewrite the answer using only "
            "paper IDs exactly as shown in the evidence, for example [arxiv:2507.16947]. "
            "Do not cite evidence item numbers like [1] or [4].\n\n"
            f"Previous answer:\n{response}"
        )
        repair_overrides = dict(overrides)
        repair_overrides["temperature"] = min(float(repair_overrides.get("temperature", 0.1)), 0.05)
        try:
            repaired = self.llm_router.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": repair_prompt},
                ],
                provider=provider,
                overrides=repair_overrides,
            )
        except Exception:
            return response
        repaired = str(repaired or "").strip()
        if repaired and not _invalid_citations(repaired):
            return repaired
        return response

    def _repair_sparse_citations(
        self,
        response: str,
        prompt: str,
        provider: str | None,
        overrides: dict[str, Any],
        evidence: list[Evidence],
    ) -> str:
        if self.llm_router is None or not response or _invalid_citations(response):
            return response
        available_ids = {item.paper_id for item in evidence if item.paper_id}
        if len(available_ids) < 3:
            return response
        cited_ids = _cited_paper_ids(response)
        desired_count = min(3, len(available_ids))
        if len(cited_ids) >= desired_count:
            return response

        repair_prompt = (
            f"{prompt}\n\n"
            "Your previous answer cited too few different papers for the available evidence. "
            f"Rewrite the answer so that, when relevant, it cites at least {desired_count} distinct paper IDs "
            "from the evidence. Keep the answer concise and do not add unsupported facts.\n\n"
            f"Previous answer:\n{response}"
        )
        repair_overrides = dict(overrides)
        repair_overrides["temperature"] = min(float(repair_overrides.get("temperature", 0.1)), 0.05)
        try:
            repaired = self.llm_router.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": repair_prompt},
                ],
                provider=provider,
                overrides=repair_overrides,
            )
        except Exception:
            return response
        repaired = str(repaired or "").strip()
        if not repaired or _invalid_citations(repaired):
            return response
        repaired_ids = _cited_paper_ids(repaired)
        return repaired if len(repaired_ids) > len(cited_ids) else response


def _flatten_evidence(hits: list[SearchHit], max_items: int) -> list[Evidence]:
    evidence: list[Evidence] = []
    for hit in hits:
        evidence.extend(hit.evidence)
    evidence.sort(key=lambda item: item.score, reverse=True)
    return evidence[:max_items]


def _evidence_item_limit(limit: int, hits: list[SearchHit]) -> int:
    hit_count = max(1, len(hits))
    requested = max(1, int(limit))
    return min(80, max(24, requested * 4, hit_count * 5))


def _supplemental_evidence_from_extraction(
    paper_id: str,
    extraction: dict[str, Any],
) -> list[Evidence]:
    evidence: list[Evidence] = []
    for field_name, kind, base_score in [
        ("claims", "claim", 7.0),
        ("methods", "method", 5.0),
        ("concepts", "concept", 4.0),
        ("relations", "relation", 4.5),
    ]:
        for item in extraction.get(field_name) or []:
            text = _evidence_item_text(item)
            if not text:
                continue
            score = base_score + _evidence_specificity_bonus(text)
            evidence.append(
                Evidence(
                    paper_id=paper_id,
                    kind=kind,
                    field=field_name,
                    text=text,
                    score=score,
                    metadata=item if isinstance(item, dict) else {},
                )
            )
    return evidence


def _evidence_item_text(item: Any) -> str:
    if isinstance(item, dict):
        preferred = [
            "statement",
            "evidence_span",
            "label",
            "context",
            "description",
            "relation_type",
            "subject_id",
            "object_id",
        ]
        parts = [str(item.get(key) or "") for key in preferred]
        return " ".join(part for part in parts if part).strip()
    return str(item or "").strip()


def _evidence_specificity_bonus(text: str) -> float:
    bonus = 0.0
    if re.search(r"\d", text or ""):
        bonus += 2.0
    if re.search(r"\b(ai consult|clinical|clinician|physician|patient|diagnos|treatment)\b", text or "", re.I):
        bonus += 1.5
    return bonus


def _answer_evidence_rank(item: Evidence) -> float:
    kind_bonus = {
        "claim": 3.0,
        "relation": 2.0,
        "method": 1.0,
        "concept": 0.5,
        "paper": 0.25,
    }.get(item.kind, 0.0)
    return float(item.score) + kind_bonus + _evidence_specificity_bonus(item.text)


def _prioritize_hits(hits: list[SearchHit], priority_paper_ids: set[str]) -> list[SearchHit]:
    """Stable-sort hits so evidence from the main source(s) ranks first."""
    if not priority_paper_ids:
        return hits
    return sorted(
        hits,
        key=lambda hit: 0 if str(getattr(hit.source, "paper_id", "")) in priority_paper_ids else 1,
    )


def _build_grounded_prompt(
    question: str,
    hits: list[SearchHit],
    evidence: list[Evidence],
    conversation_context: list[dict[str, Any]] | None = None,
    priority_paper_ids: set[str] | None = None,
) -> str:
    source_titles = {
        hit.source.paper_id: hit.source.title or hit.source.paper_id
        for hit in hits
    }
    lines = [f"Question: {question}"]
    context_lines = _conversation_context_lines(conversation_context)
    if context_lines:
        lines.extend(["", "Previous conversation context:", *context_lines])
    present_priority = [pid for pid in (priority_paper_ids or set()) if pid in source_titles]
    if present_priority:
        joined = ", ".join(f"[{pid}]" for pid in present_priority)
        lines.extend(
            [
                "",
                f"Primary source(s) / Hauptquelle: {joined}.",
                "Answer primarily from the primary source(s). Use the other evidence only to "
                "support, contradict, or deepen points the primary source does not fully explain, "
                "and make clear which role each supporting source plays.",
            ]
        )
    lines.extend(["", "Evidence:"])
    for index, item in enumerate(evidence, start=1):
        title = source_titles.get(item.paper_id, item.paper_id)
        lines.append(
            f"{index}. [{item.paper_id}] {title} | {item.kind} | {_sanitize_evidence_text(item.text)}"
        )
    lines.extend(
        [
            "",
            "Answer concisely using only this evidence.",
            "Include source paper IDs in square brackets for each substantive claim.",
            "When multiple papers support different parts of the answer, cite multiple distinct paper IDs instead of reusing only one source.",
            "When one claim is supported by multiple papers, cite the supporting paper IDs together in one bracket, separated by commas, for example [p1, p2].",
            "Use only paper IDs shown in the evidence as citations; never cite evidence item numbers like [1] or [4].",
            "When quantitative findings or metrics are present, include the most important numbers.",
        ]
    )
    if _needs_clinical_model_role_instruction(question, evidence):
        lines.append(
            "Distinguish deployed clinical systems from models used only for evaluation, rating, or robustness checks."
        )
    return "\n".join(lines)


def _conversation_context_lines(conversation_context: list[dict[str, Any]] | None) -> list[str]:
    lines: list[str] = []
    for item in (conversation_context or [])[-6:]:
        if not isinstance(item, dict):
            continue
        role = "Assistant" if item.get("role") == "assistant" else "User"
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if content:
            lines.append(f"- {role}: {content[:900]}")
    return lines


def _build_pdf_context_prompt(
    question: str,
    combined_text: str,
    conversation_context: list[dict[str, Any]] | None = None,
) -> str:
    lines = [f"Question: {question}"]
    context_lines = _conversation_context_lines(conversation_context)
    if context_lines:
        lines.extend(["", "Previous conversation context:", *context_lines])
    lines.extend(
        [
            "",
            "Whole parsed PDF context:",
            combined_text,
            "",
            "Answer using only the PDF context above.",
            "Cite the exact paper IDs shown in square brackets for every substantive claim.",
            "When multiple papers support one claim, cite them together, for example [p1, p2].",
            "If the PDF context is insufficient, say that the local PDF context does not contain enough evidence.",
        ]
    )
    return "\n".join(lines)


def _best_pdf_context_snippet(pdf_text: str, question: str, max_chars: int = 900) -> str:
    clean = re.sub(r"\s+", " ", str(pdf_text or "")).strip()
    if len(clean) <= max_chars:
        return clean
    terms = _match_terms(question)
    lower = clean.lower()
    best_pos = -1
    for term in terms:
        best_pos = lower.find(term.lower())
        if best_pos >= 0:
            break
    if best_pos < 0:
        return clean[: max_chars - 3].rstrip() + "..."
    start = max(0, best_pos - max_chars // 3)
    end = min(len(clean), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix


def _extractive_answer(
    question: str,
    hits: list[SearchHit],
    evidence: list[Evidence],
) -> str:
    source_titles = {
        hit.source.paper_id: hit.source.title or hit.source.paper_id
        for hit in hits
    }
    lines = [f"Local KG evidence for '{question}':"]
    for item in evidence[:5]:
        title = source_titles.get(item.paper_id, item.paper_id)
        lines.append(f"- [{item.paper_id}] {title}: {_sanitize_evidence_text(item.text)}")
    return "\n".join(lines)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _sanitize_evidence_text(text: str) -> str:
    return re.sub(
        r"\[([^\]]+)\]",
        lambda match: match.group(0)
        if _is_allowed_citation_label(match.group(1).strip())
        else f"({match.group(1).strip()})",
        str(text or ""),
    )


def _cited_paper_ids(answer_text: str) -> set[str]:
    ids: set[str] = set()
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        for value in re.split(r"[,;]\s*", bracketed):
            value = value.strip()
            if value.startswith("arxiv:") or value.startswith("doi:") or value.startswith("p"):
                ids.add(value)
    return ids


def _citation_links_for_answer(answer_text: str, evidence: list[Evidence]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for match in re.finditer(r"\[([^\]]+)\]", answer_text or ""):
        raw_citation = match.group(1).strip()
        context = _citation_context(answer_text, match.start(), match.end())
        for paper_id_value in _citation_paper_ids(raw_citation):
            best_index, best_evidence, score = _best_citation_evidence(paper_id_value, context, evidence)
            if best_evidence is None:
                continue
            links.append(
                {
                    "citation": raw_citation,
                    "citation_start": match.start(),
                    "citation_end": match.end(),
                    "paper_id": paper_id_value,
                    "evidence_id": best_evidence.evidence_id,
                    "evidence_index": best_index,
                    "score": round(score, 4),
                    "context": context,
                }
            )
    return links


def _citation_paper_ids(citation: str) -> list[str]:
    ids: list[str] = []
    for value in re.split(r"[,;]\s*|\s+(?:and|und)\s+", citation or ""):
        value = value.strip()
        if value and _is_allowed_citation_label(value):
            ids.append(value)
    return ids


def _citation_context(answer_text: str, start: int, end: int, window: int = 500) -> str:
    before = str(answer_text or "")[:start]
    after = str(answer_text or "")[end:]
    left_boundary = max(before.rfind(". "), before.rfind("! "), before.rfind("? "), before.rfind("\n\n"))
    right_candidates = [idx for idx in [after.find(". "), after.find("! "), after.find("? "), after.find("\n\n")] if idx >= 0]
    left = left_boundary + 2 if left_boundary >= 0 else max(0, start - window // 2)
    right = end + (min(right_candidates) + 1 if right_candidates else min(len(after), window // 2))
    context = str(answer_text or "")[left:right]
    context = re.sub(r"\[[^\]]+\]", " ", context)
    return re.sub(r"\s+", " ", context).strip()


def _best_citation_evidence(
    paper_id_value: str,
    context: str,
    evidence: list[Evidence],
) -> tuple[int, Evidence | None, float]:
    candidates = [(index, item) for index, item in enumerate(evidence) if _same_paper_id(item.paper_id, paper_id_value)]
    if not candidates:
        return -1, None, 0.0

    scored = [
        (index, item, _citation_evidence_score(context, item))
        for index, item in candidates
    ]
    scored.sort(key=lambda row: (row[2], row[1].score, -row[0]), reverse=True)
    index, item, score = scored[0]
    return index, item, score


def _citation_evidence_score(context: str, evidence: Evidence) -> float:
    target = _evidence_match_text(evidence)
    context_norm = _match_normalize(context)
    target_norm = _match_normalize(target)
    if not context_norm or not target_norm:
        return 0.0

    score = 0.0
    context_terms = _match_terms(context)
    target_terms = set(_match_terms(target))
    if context_terms:
        score += sum(1.0 for term in context_terms if term in target_terms)

    quantitative = _quantitative_tokens(context)
    if quantitative:
        matched_numbers = sum(1 for token in quantitative if token in _quantitative_tokens(target))
        score += matched_numbers * 18.0
        if matched_numbers == 0:
            score -= 30.0

    if evidence.kind == "claim":
        score += 8.0
    elif evidence.kind == "relation":
        score += 3.0
    elif evidence.kind == "paper":
        score -= 5.0

    if context_norm and context_norm in target_norm:
        score += 20.0
    for phrase in _distinctive_phrases(context_norm):
        if phrase in target_norm:
            score += 8.0
    return score


def _evidence_match_text(evidence: Evidence) -> str:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    parts = [
        evidence.text,
        str(metadata.get("statement") or ""),
        str(metadata.get("evidence_span") or ""),
        str(metadata.get("context") or ""),
        str(metadata.get("description") or ""),
        str(metadata.get("label") or ""),
    ]
    return " ".join(part for part in parts if part)


def _same_paper_id(left: str, right: str) -> bool:
    left_norm = _normalize_citation_id(left)
    right_norm = _normalize_citation_id(right)
    return left_norm == right_norm or left_norm.endswith(right_norm) or right_norm.endswith(left_norm)


def _normalize_citation_id(value: str) -> str:
    normalized = re.sub(r"^https?://arxiv\.org/abs/", "arxiv:", str(value or "").lower())
    return re.sub(r"\s+", "", normalized)


def _match_normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w.%+-]+", " ", str(value or "").lower())).strip()


def _match_terms(value: str) -> list[str]:
    stopwords = {
        "about",
        "also",
        "and",
        "are",
        "based",
        "from",
        "have",
        "into",
        "paper",
        "that",
        "the",
        "their",
        "this",
        "used",
        "with",
    }
    terms = re.findall(r"[a-z0-9][a-z0-9-]{3,}", _match_normalize(value))
    return [term for term in terms if term not in stopwords]


def _quantitative_tokens(value: str) -> set[str]:
    tokens = set()
    for match in re.findall(r"\d+(?:\.\d+)?\s*%?", str(value or "")):
        clean = re.sub(r"\s+", "", match)
        if clean:
            tokens.add(clean)
            tokens.add(clean.rstrip("%"))
    return tokens


def _distinctive_phrases(value: str) -> list[str]:
    words = [word for word in value.split() if len(word) >= 4]
    phrases: list[str] = []
    for size in (5, 4, 3):
        for index in range(0, max(len(words) - size + 1, 0)):
            phrase = " ".join(words[index:index + size])
            if any(char.isdigit() for char in phrase) or len(phrase) >= 28:
                phrases.append(phrase)
    return phrases[:12]


def _invalid_citations(answer_text: str) -> list[str]:
    invalid: list[str] = []
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        parts = [part.strip() for part in re.split(r"[,;]\s*", bracketed) if part.strip()]
        if not parts:
            continue
        for part in parts:
            if re.fullmatch(r"\d+", part):
                invalid.append(part)
            elif re.fullmatch(r"\d+(?:\s*[-,]\s*\d+)+", part):
                invalid.append(part)
            elif not _is_allowed_citation_label(part):
                invalid.append(part)
    return invalid


def _is_allowed_citation_label(value: str) -> bool:
    return value.startswith("arxiv:") or value.startswith("doi:") or value.startswith("p")


def _is_transient_generation_error(error: str) -> bool:
    error_lower = str(error or "").lower()
    return any(
        marker in error_lower
        for marker in (
            "429",
            "503",
            "502",
            "504",
            "service unavailable",
            "temporarily",
            "timeout",
            "timed out",
            "rate limit",
            "high demand",
        )
    )


def _needs_clinical_model_role_instruction(question: str, evidence: list[Evidence]) -> bool:
    text = " ".join([question, *(item.text for item in evidence[:8])]).lower()
    has_clinical = any(term in text for term in ("clinical", "clinic", "clinician", "patient", "physician"))
    has_model_role = any(
        term in text
        for term in ("deployed", "deployment", "evaluation", "rating", "rater", "grader", "gpt-4", "o3", "ai consult")
    )
    return has_clinical and has_model_role
