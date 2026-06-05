from __future__ import annotations

import argparse
import json
import shutil
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from extraction.entity_linker import ExtractionPipeline
from parsing.parser_router import ParserRouter
from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter
from quality.benchmark import DEFAULT_GOLD_DIR, evaluate_case
from quality.pdf_resolver import BenchmarkPdfResolver
from quality.phase4_eval import DEFAULT_CASES_PATH, evaluate_answer, load_cases, summarize


@dataclass(frozen=True)
class SuiteConfig:
    suite: str
    provider: str | None
    model: str | None
    context_policy: str
    compare_context_policies: list[str]
    answer_context_mode: str
    download_missing: bool
    metadata_db_path: str
    graph_db_path: str
    pdf_base_dir: str
    output_dir: Path
    isolated_db: bool


def run_suite(config: SuiteConfig) -> dict[str, Any]:
    started = time.perf_counter()
    run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = config.output_dir / run_id
    predictions_dir = run_dir / "predictions"
    run_dir.mkdir(parents=True, exist_ok=True)
    predictions_dir.mkdir(parents=True, exist_ok=True)

    metadata_db_path = config.metadata_db_path
    if config.isolated_db:
        source = Path(config.metadata_db_path)
        target = run_dir / "metadata.duckdb"
        if source.exists():
            shutil.copy2(source, target)
            metadata_db_path = str(target)

    llm_router = LLMRouter.from_config_file("config.yaml")
    provider = config.provider or llm_router.default_provider
    model = config.model or llm_router.provider_default_model(provider)
    resolver = BenchmarkPdfResolver(config.pdf_base_dir)

    policy_reports = []
    for policy in config.compare_context_policies or [config.context_policy]:
        policy_reports.append(
            _run_extraction_policy(
                policy=policy,
                provider=provider,
                model=model,
                llm_router=llm_router,
                resolver=resolver,
                config=config,
                predictions_dir=predictions_dir / policy,
            )
        )

    answer_report = _run_answer_benchmark(
        provider=provider,
        model=model,
        llm_router=llm_router,
        metadata_db_path=metadata_db_path,
        graph_db_path=config.graph_db_path,
        answer_context_mode=config.answer_context_mode,
        pdf_base_dir=config.pdf_base_dir,
    )

    duration = time.perf_counter() - started
    report = {
        "run_id": run_id,
        "suite": config.suite,
        "provider": provider,
        "model": model,
        "context_policy": config.context_policy,
        "compare_context_policies": config.compare_context_policies or [config.context_policy],
        "answer_context_mode": config.answer_context_mode,
        "metadata_db_path": metadata_db_path,
        "pdf_base_dir": config.pdf_base_dir,
        "duration_seconds": round(duration, 4),
        "extraction": _summarize_policy_reports(policy_reports),
        "answering": answer_report,
        "warnings": _collect_warnings(policy_reports, answer_report),
    }
    report["summary"] = _suite_summary(report)

    (run_dir / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (run_dir / "report.md").write_text(_render_markdown_report(report), encoding="utf-8")
    return report


def latest_suite_report(output_dir: Path | str = Path("data/eval/benchmarks")) -> dict[str, Any]:
    """Return the newest persisted benchmark-suite report, if one exists."""
    base = Path(output_dir)
    if not base.exists():
        return {}
    reports = sorted(
        (path for path in base.glob("*/report.json") if path.is_file()),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if not reports:
        return {}
    try:
        return json.loads(reports[0].read_text(encoding="utf-8"))
    except Exception as exc:
        return {"warnings": [f"Could not load latest benchmark suite report: {exc}"]}


def _run_extraction_policy(
    *,
    policy: str,
    provider: str,
    model: str,
    llm_router: LLMRouter,
    resolver: BenchmarkPdfResolver,
    config: SuiteConfig,
    predictions_dir: Path,
) -> dict[str, Any]:
    started = time.perf_counter()
    predictions_dir.mkdir(parents=True, exist_ok=True)
    parser_router = ParserRouter()
    pipeline = ExtractionPipeline(llm_router, quality_db_path=None)
    cases = []
    warnings: list[str] = []
    provenance: list[dict[str, Any]] = []

    for gold_file in sorted(DEFAULT_GOLD_DIR.glob("*.json")):
        gold = json.loads(gold_file.read_text(encoding="utf-8"))
        paper_id = str(gold.get("paper_id") or gold_file.stem)
        title = _case_title(gold)
        doi = _case_doi(gold)
        resolution = resolver.resolve(
            paper_id=paper_id,
            title=title,
            doi=doi,
            download_missing=config.download_missing,
        )
        provenance.append({"paper_id": paper_id, **resolution.provenance, "warnings": resolution.warnings})
        warnings.extend(f"{paper_id}: {warning}" for warning in resolution.warnings)
        parsed_text = ""
        prediction: dict[str, Any] = {}
        extraction_duration: float | None = None
        if resolution.pdf_path:
            try:
                parse_started = time.perf_counter()
                parsed = parser_router.parse(resolution.pdf_path, paper_id)
                parsed_text = parsed.text
                extraction_started = time.perf_counter()
                result = pipeline.process(
                    paper_id,
                    parsed_text,
                    provider=provider,
                    overrides={
                        "model": model,
                        "context_policy": policy,
                        "allow_context_fallback": True,
                    },
                    link_concepts=True,
                )
                extraction_duration = time.perf_counter() - extraction_started
                prediction = _prediction_from_result(result)
                prediction["parsed_text"] = parsed_text
                prediction["parse_duration_seconds"] = round(time.perf_counter() - parse_started, 4)
                prediction["extraction_duration_seconds"] = round(extraction_duration, 4)
            except Exception as exc:
                warnings.append(f"{paper_id}: extraction failed for policy={policy}: {exc}")
        if not prediction:
            prediction = dict(gold.get("prediction") or {})
            if prediction:
                prediction["benchmark_prediction_source"] = "embedded_gold_prediction"
                warnings.append(f"{paper_id}: used embedded gold prediction for policy={policy}.")
            else:
                warnings.append(f"{paper_id}: missing prediction for policy={policy}.")
        prediction.setdefault("paper_id", paper_id)
        if parsed_text and "parsed_text" not in prediction:
            prediction["parsed_text"] = parsed_text

        pred_path = predictions_dir / f"{_safe_name(paper_id)}.json"
        pred_path.write_text(json.dumps(prediction, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        case_report = evaluate_case({**gold, "parsed_text": parsed_text}, prediction)
        case_report["prediction_path"] = str(pred_path)
        case_report["pdf_path"] = resolution.pdf_path
        case_report["extraction_duration_seconds"] = extraction_duration
        case_report["context_diagnostics"] = _context_diagnostics_from_prediction(prediction)
        cases.append(case_report)

    return {
        "policy": policy,
        "duration_seconds": round(time.perf_counter() - started, 4),
        "summary": _aggregate_extraction_cases(cases),
        "cases": cases,
        "pdf_provenance": provenance,
        "warnings": warnings,
    }


def _run_answer_benchmark(
    *,
    provider: str,
    model: str,
    llm_router: LLMRouter,
    metadata_db_path: str,
    graph_db_path: str,
    answer_context_mode: str,
    pdf_base_dir: str,
) -> dict[str, Any]:
    started = time.perf_counter()
    retriever = HybridRetriever(KGRetriever(metadata_db_path=metadata_db_path, graph_db_path=graph_db_path))
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    case_reports = []
    warnings: list[str] = []
    for case in load_cases(DEFAULT_CASES_PATH):
        try:
            answer = responder.answer(
                case.question,
                limit=8,
                provider=provider,
                model=model,
                paper_ids=case.expected_sources if answer_context_mode == "pdf_if_fits" else None,
                answer_context_mode=answer_context_mode,
                pdf_base_dir=pdf_base_dir,
            )
            payload = answer.to_dict()
        except Exception as exc:
            payload = {
                "question": case.question,
                "answer": "",
                "sources": [],
                "evidence": [],
                "no_answer": True,
                "generation_error": str(exc),
            }
            warnings.append(f"{case.id}: answer generation failed: {exc}")
        report = evaluate_answer(case, payload)
        report["context_diagnostics"] = payload.get("context_diagnostics") or {}
        report["source_verification"] = payload.get("source_verification")
        case_reports.append(report)
    summary = summarize(case_reports)
    summary["cross_paper_score"] = _cross_paper_score(case_reports)
    return {
        "duration_seconds": round(time.perf_counter() - started, 4),
        "summary": summary,
        "cases": case_reports,
        "warnings": warnings,
    }


def _prediction_from_result(result: Any) -> dict[str, Any]:
    try:
        payload = json.loads(result.raw_response)
    except Exception:
        payload = {}
    payload.update(
        {
            "paper_id": result.paper_id,
            "concepts": result.concepts,
            "methods": result.methods,
            "concept_candidates": result.concept_candidates,
            "method_candidates": result.method_candidates,
            "relations": result.relations,
            "claims": result.claims,
            "cross_domain_hints": result.cross_domain_hints,
            "quality_warnings": result.quality_warnings,
            "extraction_diagnostics": result.extraction_diagnostics,
        }
    )
    return payload


def _aggregate_extraction_cases(cases: list[dict[str, Any]]) -> dict[str, Any]:
    if not cases:
        return {"case_count": 0, "average_f1": 0.0}
    concept_f1 = sum(float(case["concepts"]["f1"]) for case in cases) / len(cases)
    method_f1 = sum(float(case["methods"]["f1"]) for case in cases) / len(cases)
    relation_f1 = sum(float(case["relations"]["f1"]) for case in cases) / len(cases)
    return {
        "case_count": len(cases),
        "concept_f1": round(concept_f1, 4),
        "method_f1": round(method_f1, 4),
        "relation_f1": round(relation_f1, 4),
        "average_f1": round((concept_f1 + method_f1 + relation_f1) / 3, 4),
        "supported_extra_count": sum(int(case.get("supported_extra_count") or 0) for case in cases),
        "unsupported_extra_count": sum(int(case.get("unsupported_extra_count") or 0) for case in cases),
    }


def _summarize_policy_reports(policy_reports: list[dict[str, Any]]) -> dict[str, Any]:
    best = None
    if policy_reports:
        best = max(policy_reports, key=lambda report: float(report.get("summary", {}).get("average_f1") or 0.0))
    return {
        "best_policy": best.get("policy") if best else None,
        "policies": policy_reports,
    }


def _suite_summary(report: dict[str, Any]) -> dict[str, Any]:
    policies = report.get("extraction", {}).get("policies") or []
    selected_policy = next(
        (item for item in policies if item.get("policy") == report.get("context_policy")),
        policies[0] if policies else {},
    )
    extraction_score = float((selected_policy.get("summary") or {}).get("average_f1") or 0.0)
    answer_score = float((report.get("answering", {}).get("summary") or {}).get("average_score") or 0.0)
    cross_paper_score = float((report.get("answering", {}).get("summary") or {}).get("cross_paper_score") or 0.0)
    return {
        "model": report.get("model"),
        "provider": report.get("provider"),
        "context_policy": report.get("context_policy"),
        "best_extraction_policy": report.get("extraction", {}).get("best_policy"),
        "extraction_score": round(extraction_score, 4),
        "answer_score": round(answer_score, 4),
        "cross_paper_score": round(cross_paper_score, 4),
        "duration_seconds": report.get("duration_seconds"),
        "warning_count": len(report.get("warnings") or []),
    }


def _cross_paper_score(case_reports: list[dict[str, Any]]) -> float:
    cross_cases = [
        case for case in case_reports
        if len(case.get("expected_sources") or []) > 1 or "cross" in str(case.get("id") or "").lower()
    ]
    if not cross_cases:
        return 0.0
    return round(sum(float(case.get("score") or 0.0) for case in cross_cases) / len(cross_cases), 4)


def _collect_warnings(policy_reports: list[dict[str, Any]], answer_report: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    for report in policy_reports:
        warnings.extend(str(item) for item in report.get("warnings") or [])
    warnings.extend(str(item) for item in answer_report.get("warnings") or [])
    return warnings


def _render_markdown_report(report: dict[str, Any]) -> str:
    summary = report.get("summary") or {}
    lines = [
        "# ScienceKG Benchmark Suite",
        "",
        f"- Run: `{report.get('run_id')}`",
        f"- Provider/model: `{summary.get('provider')}` / `{summary.get('model')}`",
        f"- Context policy: `{summary.get('context_policy')}`",
        f"- Best extraction policy: `{summary.get('best_extraction_policy')}`",
        f"- Extraction score: `{summary.get('extraction_score')}`",
        f"- Answer score: `{summary.get('answer_score')}`",
        f"- Cross-paper score: `{summary.get('cross_paper_score')}`",
        f"- Duration seconds: `{summary.get('duration_seconds')}`",
        f"- Warnings: `{summary.get('warning_count')}`",
        "",
        "## Extraction Policies",
    ]
    for policy in report.get("extraction", {}).get("policies") or []:
        lines.append(f"- `{policy.get('policy')}`: {policy.get('summary')}")
    lines.extend(["", "## Answering", json.dumps(report.get("answering", {}).get("summary") or {}, indent=2)])
    if report.get("warnings"):
        lines.extend(["", "## Warnings"])
        lines.extend(f"- {warning}" for warning in report["warnings"][:80])
    return "\n".join(lines) + "\n"


def _case_title(gold: dict[str, Any]) -> str:
    for source in [gold.get("expected"), gold.get("prediction"), gold]:
        if not isinstance(source, dict):
            continue
        paper_node = source.get("paper_node") if isinstance(source.get("paper_node"), dict) else {}
        title = paper_node.get("title") or source.get("title")
        if title:
            return str(title)
    return str(gold.get("paper_id") or "")


def _case_doi(gold: dict[str, Any]) -> str | None:
    for source in [gold.get("expected"), gold.get("prediction"), gold]:
        if not isinstance(source, dict):
            continue
        paper_node = source.get("paper_node") if isinstance(source.get("paper_node"), dict) else {}
        doi = paper_node.get("doi") or source.get("doi")
        if doi:
            return str(doi).removeprefix("https://doi.org/")
    return None


def _context_diagnostics_from_prediction(prediction: dict[str, Any]) -> dict[str, Any]:
    diagnostics = prediction.get("context_diagnostics")
    if isinstance(diagnostics, dict):
        return diagnostics
    extraction_diagnostics = prediction.get("extraction_diagnostics")
    if isinstance(extraction_diagnostics, dict) and isinstance(extraction_diagnostics.get("context_diagnostics"), dict):
        return extraction_diagnostics["context_diagnostics"]
    return {}


def _safe_name(value: str) -> str:
    return "".join(char if char.isalnum() or char in "._-" else "_" for char in value)[:160] or "case"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the ScienceKG local LLM benchmark suite.")
    parser.add_argument("--suite", choices=["core", "extended"], default="core")
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--context-policy", choices=["auto", "whole", "chunk"], default="auto")
    parser.add_argument("--compare-context-policies", nargs="*", choices=["auto", "whole", "chunk"], default=None)
    parser.add_argument("--answer-context-mode", choices=["kg", "pdf_if_fits"], default="kg")
    parser.add_argument("--download-missing", action="store_true")
    parser.add_argument("--metadata-db", default="data/metadata.duckdb")
    parser.add_argument("--graph-db", default="data/graphs/global_kg")
    parser.add_argument("--pdf-base-dir", default="data/pdfs")
    parser.add_argument("--output", default="data/eval/benchmarks")
    parser.add_argument("--isolated-db", dest="isolated_db", action="store_true", default=True)
    parser.add_argument("--no-isolated-db", dest="isolated_db", action="store_false")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    policies = args.compare_context_policies or [args.context_policy]
    report = run_suite(
        SuiteConfig(
            suite=args.suite,
            provider=args.provider,
            model=args.model,
            context_policy=args.context_policy,
            compare_context_policies=policies,
            answer_context_mode=args.answer_context_mode,
            download_missing=bool(args.download_missing or args.suite == "extended" and args.download_missing),
            metadata_db_path=args.metadata_db,
            graph_db_path=args.graph_db,
            pdf_base_dir=args.pdf_base_dir,
            output_dir=Path(args.output),
            isolated_db=bool(args.isolated_db),
        )
    )
    print(json.dumps(report["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
