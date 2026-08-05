from __future__ import annotations

from query.grounded_helpers import (
    _directional_consistency_check,
    _extract_substantive_claims,
)
from query.kg_retriever import Evidence

KNOWN = frozenset({"arxiv:1234"})


def _evidence(text: str, pid: str = "arxiv:1234") -> Evidence:
    return Evidence(
        paper_id=pid,
        kind="claim",
        text=text,
        score=1.0,
        evidence_id=f"ev-{abs(hash(text)) % 10000}",
    )


def test_extract_substantive_claims_strips_citations() -> None:
    answer = "Bevacizumab verlängert das PFS signifikant [arxiv:1234]. " "Kurz."
    claims = _extract_substantive_claims(answer)
    assert len(claims) == 1
    assert "[arxiv:1234]" not in claims[0]
    assert "PFS" in claims[0]


def test_directional_check_flags_evidence_contradiction() -> None:
    # Sentence claims a positive (verlängert), cited evidence says no benefit.
    answer = "Bevacizumab verlängert das Gesamtüberleben signifikant [arxiv:1234]."
    evidence = [_evidence("Bevacizumab zeigte keinen Vorteil im Gesamtüberleben.")]
    issues = _directional_consistency_check(answer, evidence, known_ids=KNOWN)
    assert issues
    assert any(issue["conflict_with"] == "evidence" for issue in issues)


def test_directional_check_flags_earlier_sentence_contradiction() -> None:
    # Two sentences in one answer: first says kein VorteilOS, second says longer OS.
    answer = (
        "Bevacizumab zeigt keinen Vorteil im Gesamtüberleben [arxiv:1234]. "
        "Patienten leben länger unter Bevacizumab [arxiv:1234]."
    )
    evidence = [_evidence("Bevacizumab Gesamtüberleben Gesamtüberleben.")]
    issues = _directional_consistency_check(answer, evidence, known_ids=KNOWN)
    # The earlier-sentence check should flag the second sentence.
    assert any(issue["conflict_with"] == "earlier_sentence" for issue in issues)


def test_directional_check_no_issue_when_directions_agree() -> None:
    answer = (
        "Bevacizumab verlängert das PFS signifikant [arxiv:1234]. "
        "Das progressionsfreie Überleben ist verbessert [arxiv:1234]."
    )
    evidence = [_evidence("Bevacizumab verlängert progressionsfreies Überleben.")]
    issues = _directional_consistency_check(answer, evidence, known_ids=KNOWN)
    assert issues == []


def test_directional_check_skips_non_substantive_sentences() -> None:
    answer = "Kurz. Kurz. Noch kurz [arxiv:1234]."
    evidence = [_evidence("Bevacizumab kein VorteilOS.")]
    issues = _directional_consistency_check(answer, evidence, known_ids=KNOWN)
    assert issues == []


def test_confidence_for_score_levels() -> None:
    from query.grounded_helpers import _CONTEXT_MATCH_SCORE, _confidence_for_score

    assert _confidence_for_score(_CONTEXT_MATCH_SCORE, True) == "high"
    assert _confidence_for_score(20.0, True) == "high"
    assert _confidence_for_score(8.0, True) == "medium"
    assert _confidence_for_score(2.0, True) == "low"
    assert _confidence_for_score(20.0, False) == "low"
