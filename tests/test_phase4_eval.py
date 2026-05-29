from __future__ import annotations

from quality.phase4_eval import Phase4Case, evaluate_answer


def test_phase4_eval_accepts_expected_sources_terms_and_citations() -> None:
    case = Phase4Case(
        id="clinical",
        question="How is AI used in clinics?",
        expected_sources=["arxiv:2507.16947"],
        required_terms=["AI Consult", "16%"],
        forbidden_terms=["no information"],
    )
    answer = {
        "answer": "AI Consult reduced errors by 16% [arxiv:2507.16947].",
        "sources": [{"paper_id": "arxiv:2507.16947"}],
    }

    report = evaluate_answer(case, answer)

    assert report["passes"] is True
    assert report["score"] == 1.0
    assert report["invalid_citations"] == []


def test_phase4_eval_flags_missing_terms_forbidden_text_and_numeric_citations() -> None:
    case = Phase4Case(
        id="validation",
        question="Which papers discuss validation?",
        expected_sources=["arxiv:2006.16189"],
        required_terms=["DOME", "validation"],
        forbidden_terms=["deployed clinical systems"],
    )
    answer = {
        "answer": "This discusses validation [1]. The provided evidence does not mention deployed clinical systems.",
        "sources": [],
    }

    report = evaluate_answer(case, answer)

    assert report["passes"] is False
    assert report["missing_sources"] == ["arxiv:2006.16189"]
    assert report["missing_terms"] == ["DOME"]
    assert report["forbidden_hits"] == ["deployed clinical systems"]
    assert report["invalid_citations"] == ["1"]


def test_phase4_eval_accepts_hyphenated_term_variants() -> None:
    case = Phase4Case(
        id="active_learning",
        question="What methods are used for active learning in data streams?",
        expected_sources=["arxiv:2302.08893"],
        required_terms=["Query by Committee"],
        forbidden_terms=[],
    )
    answer = {
        "answer": "A disagreement strategy such as query-by-committee is used [arxiv:2302.08893].",
        "sources": [{"paper_id": "arxiv:2302.08893"}],
    }

    report = evaluate_answer(case, answer)

    assert report["passes"] is True


def test_phase4_eval_accepts_alias_and_plural_term_variants() -> None:
    case = Phase4Case(
        id="robotics",
        question="How do robotics papers differ?",
        expected_sources=["arxiv:2502.04012"],
        required_terms=["Malleable Robots", "rater|grader|rating"],
        forbidden_terms=[],
    )
    answer = {
        "answer": "Malleable/Continuum Robot Control was compared with a grading setup [arxiv:2502.04012].",
        "sources": [{"paper_id": "arxiv:2502.04012"}],
    }

    report = evaluate_answer(case, answer)

    assert report["passes"] is True
