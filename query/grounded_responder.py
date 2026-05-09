from __future__ import annotations

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
    ) -> GroundedAnswer:
        hits = self.retriever.search(question, limit=limit)
        evidence = _flatten_evidence(hits, max_items=16)
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
        )
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
    ) -> tuple[str, str | None]:
        if self.llm_router is None:
            return _extractive_answer(question, hits, evidence), None

        prompt = _build_grounded_prompt(question, hits, evidence)
        merged_overrides = {
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": 1200,
            **(overrides or {}),
        }
        if model:
            merged_overrides["model"] = model

        try:
            response = self.llm_router.chat(
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
        if response:
            return response, None
        return (
            "I could not generate a synthesized answer because the configured LLM returned an empty response. "
            "Evidence-only fallback:\n" + _extractive_answer(question, hits, evidence),
            "empty_response",
        )

    def _default_model(self, provider: str | None) -> str | None:
        if self.llm_router is None:
            return None
        try:
            return self.llm_router.provider_settings(provider).model
        except Exception:
            return None


def _flatten_evidence(hits: list[SearchHit], max_items: int) -> list[Evidence]:
    evidence: list[Evidence] = []
    for hit in hits:
        evidence.extend(hit.evidence)
    evidence.sort(key=lambda item: item.score, reverse=True)
    return evidence[:max_items]


def _build_grounded_prompt(
    question: str,
    hits: list[SearchHit],
    evidence: list[Evidence],
) -> str:
    source_titles = {
        hit.source.paper_id: hit.source.title or hit.source.paper_id
        for hit in hits
    }
    lines = [f"Question: {question}", "", "Evidence:"]
    for index, item in enumerate(evidence, start=1):
        title = source_titles.get(item.paper_id, item.paper_id)
        lines.append(
            f"{index}. [{item.paper_id}] {title} | {item.kind} | {item.text}"
        )
    lines.extend(
        [
            "",
            "Answer concisely using only this evidence.",
            "Include source paper IDs in square brackets for each substantive claim.",
        ]
    )
    return "\n".join(lines)


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
        lines.append(f"- [{item.paper_id}] {title}: {item.text}")
    return "\n".join(lines)
