from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import Evidence, SearchHit, Source
from query.llm_router import LLMRouter


@dataclass
class GroundedAnswer:
    question: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    no_answer: bool = False
    model: str | None = None
    generation_error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "sources": [source.to_dict() for source in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "no_answer": self.no_answer,
            "model": self.model,
            "generation_error": self.generation_error,
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
    ) -> GroundedAnswer:
        hits = self.retriever.search(question, limit=limit)
        evidence = self._evidence_for_answer(hits, max_items=24)
        sources = [hit.source for hit in hits if hit.evidence]

        if not evidence:
            return GroundedAnswer(
                question=question,
                answer=f"No matching evidence was found in the local KG for: {question}",
                sources=[],
                evidence=[],
                no_answer=True,
                model=model,
            )

        answer_text, generation_error = self._generate_answer(
            question=question,
            hits=hits,
            evidence=evidence,
            provider=provider,
            model=model,
            overrides=overrides,
            conversation_context=conversation_context,
        )
        cited_ids = _cited_paper_ids(answer_text)
        if cited_ids:
            cited_sources = [source for source in sources if source.paper_id in cited_ids]
            cited_evidence = [item for item in evidence if item.paper_id in cited_ids]
            if cited_sources:
                sources = cited_sources
            if cited_evidence:
                evidence = cited_evidence
        return GroundedAnswer(
            question=question,
            answer=answer_text,
            sources=sources,
            evidence=evidence,
            no_answer=False,
            model=model or self._default_model(provider),
            generation_error=generation_error,
        )

    def _generate_answer(
        self,
        question: str,
        hits: list[SearchHit],
        evidence: list[Evidence],
        provider: str | None,
        model: str | None,
        overrides: dict[str, Any] | None,
        conversation_context: list[dict[str, Any]] | None = None,
    ) -> tuple[str, str | None]:
        if self.llm_router is None:
            return _extractive_answer(question, hits, evidence), None

        prompt = _build_grounded_prompt(question, hits, evidence, conversation_context=conversation_context)
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


def _flatten_evidence(hits: list[SearchHit], max_items: int) -> list[Evidence]:
    evidence: list[Evidence] = []
    for hit in hits:
        evidence.extend(hit.evidence)
    evidence.sort(key=lambda item: item.score, reverse=True)
    return evidence[:max_items]


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


def _build_grounded_prompt(
    question: str,
    hits: list[SearchHit],
    evidence: list[Evidence],
    conversation_context: list[dict[str, Any]] | None = None,
) -> str:
    source_titles = {
        hit.source.paper_id: hit.source.title or hit.source.paper_id
        for hit in hits
    }
    lines = [f"Question: {question}"]
    context_lines = _conversation_context_lines(conversation_context)
    if context_lines:
        lines.extend(["", "Previous conversation context:", *context_lines])
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
