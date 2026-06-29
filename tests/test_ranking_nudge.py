"""Tests for the citation/recency ranking nudge in ``query/kg_retriever.py``.

The nudge must be *light*: it breaks ties among comparably relevant papers in favour of
well-cited/recent ones, but never overrides a clearly better lexical match — and an old
paper is never penalised ("new ≠ better, just up to date").
"""
from __future__ import annotations

from query.kg_retriever import (
    Evidence,
    SearchHit,
    Source,
    _citation_recency_bonus,
    _rank_hits,
    effective_hit_score,
)

NOW = 2026


def _hit(pid: str, score: float, citations: int, year: int) -> SearchHit:
    hit = SearchHit(source=Source(paper_id=pid, title=pid, year=year, citation_count=citations))
    hit.add_evidence(Evidence(paper_id=pid, kind="paper", text="x", score=score))
    return hit


def test_comparable_relevance_prefers_cited_and_recent() -> None:
    cited_recent = _hit("A", 5.0, 2000, 2025)
    rare_old = _hit("B", 5.0, 3, 2010)
    assert effective_hit_score(cited_recent, now_year=NOW) > effective_hit_score(rare_old, now_year=NOW)


def test_relevance_still_dominates_the_nudge() -> None:
    # A much better lexical match that is barely cited and old must still win.
    better_match = _hit("C", 7.5, 1, 2009)
    cited_recent = _hit("D", 5.0, 5000, 2025)
    assert effective_hit_score(better_match, now_year=NOW) > effective_hit_score(cited_recent, now_year=NOW)


def test_bonus_is_capped_and_old_papers_are_not_penalised() -> None:
    weights_cap = 1.5  # config default max_bonus
    huge = _citation_recency_bonus(Source(paper_id="x", citation_count=10**6, year=2026), now_year=NOW)
    assert huge <= weights_cap + 1e-9

    old_unc = _citation_recency_bonus(Source(paper_id="y", citation_count=0, year=1990), now_year=NOW)
    assert old_unc == 0.0  # no recency bonus, and crucially no penalty


def test_rank_hits_orders_by_effective_score() -> None:
    rare_old = _hit("B", 5.0, 3, 2010)
    cited_recent = _hit("A", 5.0, 2000, 2025)
    # Fewer than two specific tokens → pure effective-score ordering (no diversity rerank).
    ordered = _rank_hits([rare_old, cited_recent], tokens=[])
    assert [hit.source.paper_id for hit in ordered] == ["A", "B"]
