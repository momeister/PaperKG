"""Unit tests for the auto-research answering orchestrator (``query/auto_answer.py``).

The heavy dependencies (grounded responder, related-topic LLM call, paper/grey harvest)
are replaced with light fakes so the test exercises the *control flow*: when does it
harvest, what events are emitted, and do the freshly harvested IDs flow into the re-answer.
"""
from __future__ import annotations

from typing import Any

import query.auto_answer as auto_answer


class _FakeAnswer:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def to_dict(self) -> dict[str, Any]:
        return dict(self._payload)


class _ScriptedResponder:
    """Returns scripted answers in order; records the kwargs of every call."""

    def __init__(self, scripts: list[dict[str, Any]]) -> None:
        self._scripts = scripts
        self.calls: list[dict[str, Any]] = []

    def answer(self, **kwargs: Any) -> _FakeAnswer:
        self.calls.append(kwargs)
        idx = min(len(self.calls) - 1, len(self._scripts) - 1)
        return _FakeAnswer(self._scripts[idx])


def _patch_common(monkeypatch, responder: _ScriptedResponder, *, related: list[str]) -> dict[str, list]:
    """Wire fakes into query.auto_answer and return a dict capturing harvest calls."""
    captured: dict[str, list] = {"papers_for": [], "grey_for": []}

    monkeypatch.setattr(auto_answer, "HybridRetriever", lambda *a, **k: object())
    monkeypatch.setattr(auto_answer, "GroundedResponder", lambda *a, **k: responder)
    monkeypatch.setattr(
        auto_answer, "analyze_topic", lambda *a, **k: {"related_topics": list(related)}
    )

    async def _fake_harvest_papers(*, question: str, **kwargs: Any) -> list[dict[str, str]]:
        captured["papers_for"].append(question)
        # Unique id per harvested topic so the merge/dedupe can be asserted.
        return [{"id": f"arxiv:{question}", "title": f"Paper for {question}"}]

    async def _fake_harvest_grey(*, question: str, **kwargs: Any) -> list[dict[str, str]]:
        captured["grey_for"].append(question)
        return [{"id": f"grey_{abs(hash(question)) % 1000}", "title": "G", "url": "http://x"}]

    monkeypatch.setattr(auto_answer, "harvest_for_question", _fake_harvest_papers)
    monkeypatch.setattr(auto_answer, "harvest_grey_sources_for_question", _fake_harvest_grey)
    return captured


async def _collect(gen) -> list[dict[str, Any]]:
    return [event async for event in gen]


async def test_strong_answer_skips_harvest(monkeypatch) -> None:
    # An answer that already cites a local paper is "strong" → no harvest.
    responder = _ScriptedResponder([{"answer": "See [arxiv:1234] for details.", "no_answer": False}])
    captured = _patch_common(monkeypatch, responder, related=["t1", "t2"])

    events = await _collect(
        auto_answer.auto_research_answer(question="What is X?", llm_router=object())
    )

    statuses = [e["status"] for e in events]
    assert statuses == ["answer", "done"]
    assert events[-1]["harvest_summary"]["harvested"] is False
    assert captured["papers_for"] == [] and captured["grey_for"] == []
    assert len(responder.calls) == 1  # answered exactly once


async def test_weak_answer_harvests_related_topics_and_reanswers(monkeypatch) -> None:
    # First answer has no traceable citation → weak → harvest main + each related topic,
    # then re-answer. Second answer is strong.
    responder = _ScriptedResponder(
        [
            {"answer": "No local evidence found.", "no_answer": True},
            {"answer": "Now grounded in [arxiv:What i].", "no_answer": False},
        ]
    )
    captured = _patch_common(monkeypatch, responder, related=["topic one", "topic two"])

    events = await _collect(
        auto_answer.auto_research_answer(
            question="What is X?",
            llm_router=object(),
            paper_ids=["existing:1"],
            max_related_topics=5,
        )
    )

    statuses = [e["status"] for e in events]
    assert statuses[0] == "answer"
    assert "planning" in statuses
    assert statuses.count("harvesting") == 3  # main question + 2 related topics
    assert "reanswering" in statuses
    assert statuses[-1] == "done"

    # Main question + both related topics were harvested for papers and grey sources.
    assert captured["papers_for"] == ["What is X?", "topic one", "topic two"]
    assert captured["grey_for"] == ["What is X?", "topic one", "topic two"]

    # The re-answer (2nd responder call) received the original scope ID plus the new ones.
    second_call = responder.calls[1]
    assert "existing:1" in second_call["paper_ids"]
    assert any(pid.startswith("arxiv:") for pid in second_call["paper_ids"])
    assert second_call["grey_source_ids"]  # harvested grey ids flowed in

    summary = events[-1]["harvest_summary"]
    assert summary["harvested"] is True
    assert summary["related_topics"] == ["topic one", "topic two"]
    assert len(summary["papers"]) == 3


async def test_force_harvests_even_for_strong_answer(monkeypatch) -> None:
    responder = _ScriptedResponder([{"answer": "Strong [arxiv:1].", "no_answer": False}])
    captured = _patch_common(monkeypatch, responder, related=[])

    events = await _collect(
        auto_answer.auto_research_answer(question="What is X?", llm_router=object(), force=True)
    )

    statuses = [e["status"] for e in events]
    assert "harvesting" in statuses  # forced despite a strong first answer
    assert captured["papers_for"] == ["What is X?"]  # no related topics → only the main one
    assert events[-1]["harvest_summary"]["harvested"] is True
