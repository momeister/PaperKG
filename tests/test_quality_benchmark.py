from __future__ import annotations

import json

from quality.benchmark import duplicate_canonical_rate, evaluate_case, run_benchmark


def test_benchmark_evaluates_precision_recall_and_duplicates(tmp_path):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "paper.json").write_text(
        json.dumps(
            {
                "paper_id": "paper",
                "expected": {
                    "concepts": [{"label": "Concept A"}, {"label": "Concept B"}],
                    "concept_candidates": [{"label": "Candidate A"}],
                    "methods": [{"label": "Method A"}],
                    "relations": [
                        {
                            "subject_id": "concept:a",
                            "relation_type": "RELATED_TO",
                            "object_id": "concept:b",
                        }
                    ],
                },
                "prediction": {
                    "concepts": [
                        {"label": "Concept A", "canonical_id": "concept:a"},
                        {"label": "Extra", "canonical_id": "concept:extra"},
                    ],
                    "concept_candidates": [{"label": "Candidate A"}],
                    "methods": [{"label": "Method A"}],
                    "relations": [
                        {
                            "subject_id": "concept:a",
                            "relation_type": "RELATED_TO",
                            "object_id": "concept:b",
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )

    report = run_benchmark(gold_dir=gold_dir)

    assert report["summary"]["case_count"] == 1
    assert report["cases"][0]["concepts"]["precision"] == 0.5
    assert report["cases"][0]["concepts"]["recall"] == 0.5
    assert report["cases"][0]["methods"]["f1"] == 1.0
    assert report["cases"][0]["relations"]["f1"] == 1.0
    assert report["warnings"] == ["Used embedded demo predictions for 1 gold case(s)."]


def test_benchmark_ci_mode_reports_missing_predictions(tmp_path):
    gold_dir = tmp_path / "gold"
    gold_dir.mkdir()
    (gold_dir / "paper.json").write_text(
        json.dumps({"paper_id": "paper", "expected": {"concepts": [], "methods": []}}),
        encoding="utf-8",
    )

    report = run_benchmark(gold_dir=gold_dir, allow_embedded_predictions=False)

    assert report["warnings"] == ["Missing prediction JSON for 1 gold case(s)."]


def test_duplicate_canonical_rate_counts_alias_collisions():
    rate = duplicate_canonical_rate(
        [
            {"label": "Appraisal theory", "canonical_id": "concept:appraisal"},
            {"label": "Cognitive appraisal theory", "canonical_id": "concept:appraisal"},
            {"label": "Appraisal dimensions", "canonical_id": "concept:appraisal-dimensions"},
        ]
    )

    assert rate == 0.5


def test_benchmark_flags_claim_attribution_errors():
    report = evaluate_case(
        {"paper_id": "paper", "expected": {"concepts": [], "methods": []}},
        {"claims": [{"statement": "x", "attributed_to": "someone_else"}]},
    )

    assert report["claim_attribution_error_count"] == 1
