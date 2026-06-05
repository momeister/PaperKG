"""Tests for main-source prioritization in the grounded responder (pure helpers)."""
from __future__ import annotations

from types import SimpleNamespace

from query.grounded_responder import _build_grounded_prompt, _prioritize_hits


def _hit(paper_id: str, title: str = "") -> SimpleNamespace:
    return SimpleNamespace(source=SimpleNamespace(paper_id=paper_id, title=title or paper_id), evidence=True)


def _evidence(paper_id: str, text: str) -> SimpleNamespace:
    return SimpleNamespace(paper_id=paper_id, kind="claim", text=text)


def test_prioritize_hits_moves_primary_first():
    hits = [_hit("arxiv:b"), _hit("arxiv:a"), _hit("arxiv:c")]
    ordered = _prioritize_hits(hits, {"arxiv:c"})
    assert ordered[0].source.paper_id == "arxiv:c"
    # remaining order is stable
    assert [h.source.paper_id for h in ordered[1:]] == ["arxiv:b", "arxiv:a"]


def test_prioritize_hits_noop_without_priority():
    hits = [_hit("arxiv:b"), _hit("arxiv:a")]
    assert _prioritize_hits(hits, set()) is hits


def test_prompt_includes_primary_source_directive():
    hits = [_hit("arxiv:a", "Main"), _hit("arxiv:b", "Other")]
    evidence = [_evidence("arxiv:a", "primary claim"), _evidence("arxiv:b", "secondary claim")]
    prompt = _build_grounded_prompt(
        "What is X?", hits, evidence, priority_paper_ids={"arxiv:a"}
    )
    assert "Primary source(s)" in prompt
    assert "[arxiv:a]" in prompt
    assert "support, contradict, or deepen" in prompt


def test_prompt_omits_directive_when_no_priority():
    hits = [_hit("arxiv:a", "Main")]
    evidence = [_evidence("arxiv:a", "claim")]
    prompt = _build_grounded_prompt("What is X?", hits, evidence)
    assert "Primary source(s)" not in prompt
