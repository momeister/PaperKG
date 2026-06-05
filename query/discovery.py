"""LLM-assisted context discovery.

Given either a topic/question or the text of an uploaded paper, the LLM produces a
short topic/method analysis plus a set of search queries that can be run against the
harvest sources to propose additional, contextually-related papers. The LLM only
suggests *search queries* — it never invents paper metadata; the actual papers come
from the real harvest APIs.
"""
from __future__ import annotations

import json
from typing import Any

from query.llm_router import LLMRouter


_FROM_TOPIC_SYSTEM = (
    "You are a research librarian. Given a topic or question, identify the key concepts and "
    "propose diverse search queries to find closely related scientific literature across "
    "disciplines. Respond ONLY with JSON."
)

_FROM_PAPER_SYSTEM = (
    "You are a research librarian. Given the text of a scientific paper, summarize what it is "
    "about and which methods it uses, then propose search queries to find papers that provide "
    "more context on the same topic and methods. Respond ONLY with JSON."
)

_SCHEMA_HINT = (
    'Return JSON of the form: {"topic_summary": "...", "methods": ["..."], '
    '"queries": [{"query": "...", "reason": "..."}]}. '
    "Provide between 3 and 8 queries. Keep each query concise (2-8 words)."
)


def analyze_topic(llm_router: LLMRouter, topic: str, provider: str | None = None) -> dict[str, Any]:
    messages = [
        {"role": "system", "content": _FROM_TOPIC_SYSTEM + " " + _SCHEMA_HINT},
        {"role": "user", "content": f"Topic or question:\n{topic.strip()}"},
    ]
    return _safe_chat_json(llm_router, messages, provider)


def analyze_paper(llm_router: LLMRouter, paper_text: str, provider: str | None = None) -> dict[str, Any]:
    excerpt = (paper_text or "")[:12000]
    messages = [
        {"role": "system", "content": _FROM_PAPER_SYSTEM + " " + _SCHEMA_HINT},
        {"role": "user", "content": f"Paper text (excerpt):\n{excerpt}"},
    ]
    return _safe_chat_json(llm_router, messages, provider)


def _safe_chat_json(
    llm_router: LLMRouter, messages: list[dict[str, str]], provider: str | None
) -> dict[str, Any]:
    overrides = {"temperature": 0.2, "max_tokens": 800}
    try:
        payload = llm_router.chat_json(messages, provider=provider, overrides=overrides)
    except Exception as exc:  # noqa: BLE001 - surface as structured error to the caller
        return {"topic_summary": "", "methods": [], "queries": [], "error": str(exc)}
    return normalize_analysis(payload)


def normalize_analysis(payload: Any) -> dict[str, Any]:
    """Coerce a raw LLM payload into the canonical discovery shape."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    raw_queries = payload.get("queries") or []
    queries: list[dict[str, str]] = []
    for item in raw_queries:
        if isinstance(item, dict) and item.get("query"):
            queries.append({"query": str(item["query"]).strip(), "reason": str(item.get("reason") or "").strip()})
        elif isinstance(item, str) and item.strip():
            queries.append({"query": item.strip(), "reason": ""})
    methods = [str(method).strip() for method in (payload.get("methods") or []) if str(method).strip()]
    return {
        "topic_summary": str(payload.get("topic_summary") or "").strip(),
        "methods": methods,
        "queries": queries[:8],
    }
