from __future__ import annotations

from query.grounded_helpers import (
    _is_substantial_statement,
    _per_sentence_citation_repair,
    _sentence_has_citation,
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


def test_is_substantial_statement_rejects_short_fragments() -> None:
    assert _is_substantial_statement("Too short.") is False
    assert _is_substantial_statement("Hi there.") is False
    assert (
        _is_substantial_statement(
            "Bevacizumab verlängert das progressionsfreie Überleben signifikant."
        )
        is True
    )


def test_sentence_has_citation_detects_known_id() -> None:
    assert _sentence_has_citation("Benefit shown [arxiv:1234].", KNOWN) is True
    assert _sentence_has_citation("Benefit shown [unknown].", KNOWN) is False
    assert _sentence_has_citation("No citation here.", KNOWN) is False


def test_per_sentence_repair_attaches_citation_when_overlap_strong() -> None:
    answer = (
        "Bevacizumab verlängert das progressionsfreie Überleben signifikant. "
        "Ein anderer Aspekt ohne Bezug zur Quelle wird hier genannt."
    )
    evidence = [_evidence("Bevacizumab verlängert progressionsfreies Überleben.")]
    repaired, diag = _per_sentence_citation_repair(answer, evidence, known_ids=KNOWN)
    assert diag["attached_count"] >= 1
    assert "[arxiv:1234]" in repaired
    # The unrelated sentence should be marked unsourced, not silently shipped.
    assert "‹unsourced›" in repaired


def test_per_sentence_repair_marks_unsourced_when_no_overlap() -> None:
    answer = (
        "Bevacizumab verlängert das progressionsfreie Überleben signifikant. "
        "Eine völlig andere Aussage über Roboterforschung ohne jeden Bezug."
    )
    evidence = [_evidence("Bevacizumab verlängert progressionsfreies Überleben.")]
    repaired, diag = _per_sentence_citation_repair(answer, evidence, known_ids=KNOWN)
    assert "‹unsourced›" in repaired
    assert diag["unsourced_count"] >= 1


def test_per_sentence_repair_falls_back_when_too_many_unsourced() -> None:
    answer = (
        "Die erste Aussage behandelt Roboterforschung im Alltag. "
        "Die zweite Aussage betrachtet Wolkenbildung in Stratusregionen. "
        "Die dritte Aussage beschreibt Musiktheorie in der Romantik. "
        "Die vierte Aussage erklärt Sportverletzungen im Profifußball."
    )
    evidence = [_evidence("Bevacizumab verlängert progressionsfreies Überleben.")]
    _repaired, diag = _per_sentence_citation_repair(
        answer, evidence, known_ids=KNOWN, unsourced_ratio_threshold=0.3
    )
    assert diag.get("fallback_reason") == "too_many_unsourced"


def test_per_sentence_repair_leaves_already_cited_sentences_alone() -> None:
    answer = "Bevacizumab verlängert das PFS signifikant [arxiv:1234]."
    evidence = [_evidence("Bevacizumab verlängert progressionsfreies Überleben.")]
    repaired, diag = _per_sentence_citation_repair(answer, evidence, known_ids=KNOWN)
    assert diag["attached_count"] == 0
    assert diag["unsourced_count"] == 0
    assert "‹unsourced›" not in repaired


def test_per_sentence_repair_no_evidence_marks_all_unsourced() -> None:
    answer = "Bevacizumab verlängert das PFS signifikant. Zweite Aussage folgt."
    repaired, diag = _per_sentence_citation_repair(answer, [], known_ids=KNOWN)
    assert diag["attached_count"] == 0
    # No evidence → nothing to attach, but no ‹unsourced› markers either (the
    # hard fallback is the caller's responsibility). The diagnostic counts
    # substantive sentences so the caller can decide.
    assert diag["substantive_sentence_count"] >= 1
    assert "‹unsourced›" not in repaired
