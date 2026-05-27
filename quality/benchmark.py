from __future__ import annotations

import argparse
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from extraction.text_normalization import normalize_key


DEFAULT_GOLD_DIR = Path("quality/gold")


def _norm(label: Any) -> str:
    return normalize_key(label)


def _label_set(items: list[Any]) -> set[str]:
    return {
        _norm(item.get("label") if isinstance(item, dict) else item)
        for item in items
        if _norm(item.get("label") if isinstance(item, dict) else item)
    }


def _relation_set(items: list[Any]) -> set[tuple[str, str, str]]:
    relations: set[tuple[str, str, str]] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        subject = _norm(item.get("subject_id") or item.get("subject"))
        relation_type = str(item.get("relation_type") or item.get("type") or "").upper()
        obj = _norm(item.get("object_id") or item.get("object"))
        if subject and relation_type and obj:
            relations.add((subject, relation_type, obj))
    return relations


@dataclass(frozen=True)
class PRF:
    precision: float
    recall: float
    f1: float
    true_positive: int
    false_positive: int
    false_negative: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "f1": round(self.f1, 4),
            "true_positive": self.true_positive,
            "false_positive": self.false_positive,
            "false_negative": self.false_negative,
        }


def prf(expected: set[str], predicted: set[str]) -> PRF:
    tp = len(expected & predicted)
    fp = len(predicted - expected)
    fn = len(expected - predicted)
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return PRF(precision, recall, f1, tp, fp, fn)


def duplicate_canonical_rate(items: list[dict[str, Any]]) -> float:
    labels_by_canonical: dict[str, set[str]] = {}
    for item in items:
        canonical = str(item.get("canonical_id") or _norm(item.get("canonical_label") or item.get("label"))).strip()
        label = _norm(item.get("label"))
        if canonical and label:
            labels_by_canonical.setdefault(canonical, set()).add(label)
    if not labels_by_canonical:
        return 0.0
    duplicates = sum(1 for labels in labels_by_canonical.values() if len(labels) > 1)
    return duplicates / len(labels_by_canonical)


def field_accuracy(expected_items: list[Any], predicted_items: list[Any], field: str) -> float:
    expected_by_label = {
        _norm(item.get("label")): str(item.get(field) or "")
        for item in expected_items
        if isinstance(item, dict) and _norm(item.get("label")) and item.get(field)
    }
    if not expected_by_label:
        return 0.0
    predicted_by_label = {
        _norm(item.get("label")): str(item.get(field) or "")
        for item in predicted_items
        if isinstance(item, dict) and _norm(item.get("label"))
    }
    matches = sum(1 for label, value in expected_by_label.items() if predicted_by_label.get(label) == value)
    return matches / len(expected_by_label)


def _bool_value(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "y"}
    return bool(value)


def claim_negation_metrics(expected_items: list[Any], predicted_items: list[Any]) -> tuple[float, int, int]:
    expected_by_statement = {
        _norm(item.get("statement")): _bool_value(item.get("negated"))
        for item in expected_items
        if isinstance(item, dict) and _norm(item.get("statement")) and "negated" in item
    }
    if not expected_by_statement:
        return 1.0, 0, 0
    predicted_by_statement = {
        _norm(item.get("statement")): _bool_value(item.get("negated"))
        for item in predicted_items
        if isinstance(item, dict) and _norm(item.get("statement"))
    }
    matches = sum(
        1 for statement, negated in expected_by_statement.items() if predicted_by_statement.get(statement) == negated
    )
    total = len(expected_by_statement)
    errors = total - matches
    return matches / total, errors, total


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _prediction_for(gold_payload: dict[str, Any], pred_dir: Path | None) -> dict[str, Any]:
    if pred_dir is None:
        return {}
    paper_id = str(gold_payload.get("paper_id") or "").strip()
    candidates = []
    if paper_id:
        candidates.append(pred_dir / f"{paper_id}.json")
    source_name = str(gold_payload.get("source_file") or "").strip()
    if source_name:
        candidates.append(pred_dir / source_name)
    for path in candidates:
        if path.exists():
            return _load_json(path)
    return {}


def evaluate_case(gold_payload: dict[str, Any], prediction: dict[str, Any]) -> dict[str, Any]:
    expected = gold_payload.get("expected") or gold_payload
    concepts = prf(_label_set(expected.get("concepts") or []), _label_set(prediction.get("concepts") or []))
    concept_candidates = prf(
        _label_set(expected.get("concept_candidates") or []),
        _label_set(prediction.get("concept_candidates") or []),
    )
    methods = prf(_label_set(expected.get("methods") or []), _label_set(prediction.get("methods") or []))
    relations = prf(_relation_set(expected.get("relations") or []), _relation_set(prediction.get("relations") or []))
    claim_attribution_errors = [
        claim
        for claim in prediction.get("claims") or []
        if isinstance(claim, dict)
        and claim.get("attributed_to") not in {None, "this_paper", "cited_work"}
    ]
    parser_warnings = list(prediction.get("parser_warnings") or prediction.get("quality_warnings") or [])
    all_entities = list(prediction.get("concepts") or []) + list(prediction.get("methods") or [])
    pending_count = sum(
        1
        for item in all_entities
        if isinstance(item, dict) and str(item.get("review_status") or "").lower() == "pending"
    )
    claim_negation_accuracy, claim_negation_errors, claim_negation_total = claim_negation_metrics(
        expected.get("claims") or [],
        prediction.get("claims") or [],
    )
    return {
        "paper_id": gold_payload.get("paper_id") or prediction.get("paper_id") or "",
        "concepts": concepts.to_dict(),
        "concept_candidates": concept_candidates.to_dict(),
        "methods": methods.to_dict(),
        "relations": relations.to_dict(),
        "entity_type_accuracy": round(
            field_accuracy(expected.get("concepts") or [], prediction.get("concepts") or [], "entity_type"),
            4,
        ),
        "method_source_type_accuracy": round(
            field_accuracy(expected.get("methods") or [], prediction.get("methods") or [], "source_type"),
            4,
        ),
        "duplicate_canonical_rate": round(duplicate_canonical_rate(prediction.get("concepts") or []), 4),
        "pending_rate": round(pending_count / len(all_entities), 4) if all_entities else 0.0,
        "claim_attribution_error_count": len(claim_attribution_errors),
        "claim_negation_accuracy": round(claim_negation_accuracy, 4),
        "claim_negation_error_count": claim_negation_errors,
        "claim_negation_expected_count": claim_negation_total,
        "parser_warning_count": len(parser_warnings),
        "accepted_concept_count": len(prediction.get("concepts") or []),
        "candidate_concept_count": len(prediction.get("concept_candidates") or []),
    }


def aggregate(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not case_reports:
        return {
            "case_count": 0,
            "concept_precision": 0.0,
            "concept_recall": 0.0,
            "method_precision": 0.0,
            "method_recall": 0.0,
            "relation_precision": 0.0,
            "relation_recall": 0.0,
            "duplicate_canonical_rate": 0.0,
            "pending_rate": 0.0,
            "claim_negation_accuracy": 1.0,
            "claim_negation_error_count": 0,
            "claim_attribution_error_count": 0,
            "passes_precision_gate": False,
            "passes_duplicate_gate": True,
            "passes_parser_gate": True,
            "passes_claim_gate": True,
        }
    concept_precision = sum(case["concepts"]["precision"] for case in case_reports) / len(case_reports)
    concept_recall = sum(case["concepts"]["recall"] for case in case_reports) / len(case_reports)
    method_precision = sum(case["methods"]["precision"] for case in case_reports) / len(case_reports)
    method_recall = sum(case["methods"]["recall"] for case in case_reports) / len(case_reports)
    relation_precision = sum(case["relations"]["precision"] for case in case_reports) / len(case_reports)
    relation_recall = sum(case["relations"]["recall"] for case in case_reports) / len(case_reports)
    dup_rate = sum(case["duplicate_canonical_rate"] for case in case_reports) / len(case_reports)
    pending_rate = sum(case["pending_rate"] for case in case_reports) / len(case_reports)
    parser_warning_count = sum(case["parser_warning_count"] for case in case_reports)
    claim_negation_accuracy = sum(case["claim_negation_accuracy"] for case in case_reports) / len(case_reports)
    claim_negation_error_count = sum(case["claim_negation_error_count"] for case in case_reports)
    claim_attribution_error_count = sum(case["claim_attribution_error_count"] for case in case_reports)
    return {
        "case_count": len(case_reports),
        "concept_precision": round(concept_precision, 4),
        "concept_recall": round(concept_recall, 4),
        "method_precision": round(method_precision, 4),
        "method_recall": round(method_recall, 4),
        "relation_precision": round(relation_precision, 4),
        "relation_recall": round(relation_recall, 4),
        "duplicate_canonical_rate": round(dup_rate, 4),
        "pending_rate": round(pending_rate, 4),
        "claim_negation_accuracy": round(claim_negation_accuracy, 4),
        "claim_negation_error_count": claim_negation_error_count,
        "claim_attribution_error_count": claim_attribution_error_count,
        "passes_precision_gate": concept_precision >= 0.85,
        "passes_duplicate_gate": dup_rate <= 0.05,
        "passes_parser_gate": parser_warning_count == 0,
        "passes_claim_gate": claim_negation_error_count == 0 and claim_attribution_error_count == 0,
    }


def run_benchmark(
    gold_dir: Path = DEFAULT_GOLD_DIR,
    pred_dir: Path | None = None,
    allow_embedded_predictions: bool = True,
) -> dict[str, Any]:
    gold_files = sorted(gold_dir.glob("*.json")) if gold_dir.exists() else []
    cases = []
    demo_predictions_used = 0
    missing_predictions = 0
    for gold_file in gold_files:
        gold_payload = _load_json(gold_file)
        gold_payload.setdefault("source_file", gold_file.name)
        prediction = _prediction_for(gold_payload, pred_dir)
        if not prediction and allow_embedded_predictions:
            prediction = dict(gold_payload.get("prediction") or {})
            if prediction:
                demo_predictions_used += 1
        if not prediction:
            missing_predictions += 1
        cases.append(evaluate_case(gold_payload, prediction))
    return {
        "summary": aggregate(cases),
        "cases": cases,
        "warnings": _benchmark_warnings(demo_predictions_used, missing_predictions),
    }


def _benchmark_warnings(demo_predictions_used: int, missing_predictions: int) -> list[str]:
    warnings: list[str] = []
    if demo_predictions_used:
        warnings.append(f"Used embedded demo predictions for {demo_predictions_used} gold case(s).")
    if missing_predictions:
        warnings.append(f"Missing prediction JSON for {missing_predictions} gold case(s).")
    return warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate extraction quality against curated gold JSON files.")
    parser.add_argument("--run", action="store_true", help="Run the benchmark.")
    parser.add_argument("--gold-dir", default=str(DEFAULT_GOLD_DIR), help="Directory containing gold JSON files.")
    parser.add_argument("--pred-dir", default=None, help="Optional directory containing prediction JSON files.")
    parser.add_argument("--output", default=None, help="Optional path to write the JSON report.")
    parser.add_argument("--ci", action="store_true", help="Fail when predictions are missing or only demo predictions are available.")
    args = parser.parse_args(argv)

    if not args.run:
        parser.print_help()
        return 0

    report = run_benchmark(
        gold_dir=Path(args.gold_dir),
        pred_dir=Path(args.pred_dir) if args.pred_dir else None,
        allow_embedded_predictions=not args.ci,
    )
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if args.ci and report.get("warnings"):
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
