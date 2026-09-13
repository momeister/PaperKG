"""Shared sub-question decomposition helpers.

Used by both :mod:`query.research_tree` (Tiefenanalyse) and
:mod:`query.direction_deep_search` (Task-Tiefensuche) so both runners share
the same sub-question generation, parsing and dedup semantics.
"""

from __future__ import annotations

import json
import re
from typing import Any

_DECOMPOSE_SYSTEM = (
    "You are a research assistant. "
    "You break complex questions into focused sub-questions that can each be answered from scientific literature."
)

_DECOMPOSE_USER = (
    'Research question: "{question}"\n\n'
    "Generate exactly {n} focused sub-questions that together give a comprehensive answer to the main question. "
    "Each sub-question must be independently answerable from scientific papers.\n"
    "Return ONLY a valid JSON array of strings, nothing else:\n"
    '["sub-question 1", "sub-question 2", ...]'
)


def _extract_questions(text: str, max_n: int) -> list[str]:
    """Parse sub-questions from LLM output; falls back to newline splitting."""
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            items = json.loads(match.group())
            if isinstance(items, list):
                return [str(q).strip() for q in items[:max_n] if str(q).strip()]
        except (json.JSONDecodeError, ValueError):
            pass
    lines = [
        re.sub(r"^[\s\d.\-)\]]+", "", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    return [line for line in lines if len(line) > 10][:max_n]


def _normalize_question(question: str) -> str:
    """Normalize a question for duplicate detection.

    Lowercases, collapses whitespace and strips surrounding punctuation so that
    re-phrasings that differ only in casing/spacing/trailing '?' collapse to the
    same key.
    """
    norm = re.sub(r"\s+", " ", str(question or "")).strip().lower()
    return norm.strip(" .,;:!?“”«»'\"")


def decompose_sync(
    llm_router: Any,
    question: str,
    n: int,
    provider: str | None,
    model: str | None,
) -> list[str]:
    """Synchronous LLM call that returns ``n`` sub-questions for ``question``."""
    overrides: dict[str, Any] = {"max_tokens": 512, "temperature": 0.3}
    if model:
        overrides["model"] = model
    text = llm_router.chat(
        messages=[
            {"role": "system", "content": _DECOMPOSE_SYSTEM},
            {
                "role": "user",
                "content": _DECOMPOSE_USER.format(question=question, n=n),
            },
        ],
        provider=provider,
        overrides=overrides,
    )
    return _extract_questions(text or "", n)


def dedup_subquestions(
    sub_questions: list[str], seen_questions: set[str] | None
) -> list[str]:
    """Keep only sub-questions not already asked, recording survivors in ``seen_questions``."""
    if seen_questions is None:
        return sub_questions
    unique: list[str] = []
    for sub_q in sub_questions:
        key = _normalize_question(sub_q)
        if not key or key in seen_questions:
            continue
        seen_questions.add(key)
        unique.append(sub_q)
    return unique
