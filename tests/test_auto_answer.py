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
    captured: dict[str, list] = {"papers_for": [], "grey_for": [], "grey_tiers": []}

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
        tier = (kwargs.get("tiers") or ("unknown",))[0]
        captured["grey_for"].append(question)
        captured["grey_tiers"].append(tier)
        return [
            {
                "id": f"grey_{tier}_{abs(hash(question)) % 1000}",
                "title": "G",
                "url": "http://x",
                "trust_tier": tier,
            }
        ]

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
    # First answer has no traceable citation → weak → stage 1 harvests papers for the
    # main question + each related topic, then re-answers. The re-answer is strong, so
    # the ladder stops before touching the web at all.
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

    # Stage 1 is scientific-only: papers for every topic, no web sources yet.
    assert captured["papers_for"] == ["What is X?", "topic one", "topic two"]
    assert captured["grey_for"] == []

    # The re-answer (2nd responder call) received the original scope ID plus the new ones.
    second_call = responder.calls[1]
    assert "existing:1" in second_call["paper_ids"]
    assert any(pid.startswith("arxiv:") for pid in second_call["paper_ids"])

    summary = events[-1]["harvest_summary"]
    assert summary["harvested"] is True
    assert summary["related_topics"] == ["topic one", "topic two"]
    assert len(summary["papers"]) == 3
    assert [stage["stage"] for stage in summary["stages"]] == ["scientific"]
    assert summary["stages"][0]["sufficient"] is True


async def test_escalates_to_trusted_web_then_unverified(monkeypatch) -> None:
    # Every answer stays weak → the ladder walks all three stages, and the web stages
    # ask for their tier explicitly: trusted before unknown.
    responder = _ScriptedResponder([{"answer": "Nothing conclusive.", "no_answer": True}])
    captured = _patch_common(monkeypatch, responder, related=[])

    events = await _collect(
        auto_answer.auto_research_answer(question="What is X?", llm_router=object())
    )

    summary = events[-1]["harvest_summary"]
    assert [stage["stage"] for stage in summary["stages"]] == ["scientific", "trusted", "unverified"]
    assert all(stage["sufficient"] is False for stage in summary["stages"])
    assert captured["grey_tiers"] == ["trusted", "unknown"]
    assert [source["trust_tier"] for source in summary["grey"]] == ["trusted", "unknown"]
    assert {event.get("stage") for event in events if event["status"] == "harvesting"} == {
        "scientific",
        "trusted",
        "unverified",
    }


async def test_trusted_stage_stops_before_unverified_web(monkeypatch) -> None:
    # Papers do not help, but a trustworthy web source does → the unverified stage
    # never runs, so no unchecked page can reach the answer.
    responder = _ScriptedResponder(
        [
            {"answer": "Nothing local.", "no_answer": True},
            {"answer": "Still nothing.", "no_answer": True},
            {"answer": "Now grounded in [grey::abc].", "no_answer": False},
        ]
    )
    captured = _patch_common(monkeypatch, responder, related=[])

    events = await _collect(
        auto_answer.auto_research_answer(question="What is X?", llm_router=object())
    )

    summary = events[-1]["harvest_summary"]
    assert [stage["stage"] for stage in summary["stages"]] == ["scientific", "trusted"]
    assert summary["stages"][-1]["sufficient"] is True
    assert captured["grey_tiers"] == ["trusted"]


def test_is_weak_answer_reads_responder_verdict() -> None:
    strong = {"answer": "ok [arxiv:1]", "no_answer": False, "context_diagnostics": {}}
    assert auto_answer._is_weak_answer(strong) is False

    # Antwort zitiert zwar, aber der Responder meldet selbst eine Evidenz-Lücke
    # (Sentinel/Prosa-Erkennung) → weak. Vorher der "reichte aus"-Widerspruch.
    flagged = {
        "answer": "X ist Y [arxiv:1]. Zu Z liegen keine Informationen vor.",
        "no_answer": False,
        "context_diagnostics": {"insufficient_evidence": True},
    }
    assert auto_answer._is_weak_answer(flagged) is True

    fallback = {
        "answer": "Hinweis: Zusammenfassung [arxiv:1]",
        "no_answer": False,
        "context_diagnostics": {"fallback_reason": "no_traceable_citations"},
    }
    assert auto_answer._is_weak_answer(fallback) is True


async def test_cited_but_insufficient_answer_triggers_harvest(monkeypatch) -> None:
    # Erste Antwort zitiert, meldet aber insufficient_evidence → Harvest + Re-Answer.
    responder = _ScriptedResponder(
        [
            {
                "answer": "X ist Y [arxiv:1]. Keine Informationen zu Z.",
                "no_answer": False,
                "context_diagnostics": {"insufficient_evidence": True},
            },
            {"answer": "Jetzt vollständig [arxiv:2].", "no_answer": False},
        ]
    )
    captured = _patch_common(monkeypatch, responder, related=[])

    events = await _collect(
        auto_answer.auto_research_answer(question="What is X?", llm_router=object())
    )

    statuses = [e["status"] for e in events]
    assert "harvesting" in statuses
    assert events[-1]["harvest_summary"]["harvested"] is True
    assert captured["papers_for"] == ["What is X?"]
    assert len(responder.calls) == 2


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
