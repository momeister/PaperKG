from __future__ import annotations

import argparse
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.llm_router import LLMRouter


DEFAULT_CASES_PATH = Path("quality/phase4_questions.json")


@dataclass(frozen=True)
class Phase4Case:
    id: str
    question: str
    expected_sources: list[str]
    required_terms: list[str]
    forbidden_terms: list[str]


def load_cases(path: Path = DEFAULT_CASES_PATH) -> list[Phase4Case]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    cases: list[Phase4Case] = []
    for item in payload:
        cases.append(
            Phase4Case(
                id=str(item["id"]),
                question=str(item["question"]),
                expected_sources=[str(value) for value in item.get("expected_sources", [])],
                required_terms=[str(value) for value in item.get("required_terms", [])],
                forbidden_terms=[str(value) for value in item.get("forbidden_terms", [])],
            )
        )
    return cases


def evaluate_answer(case: Phase4Case, answer_payload: dict[str, Any]) -> dict[str, Any]:
    answer_text = str(answer_payload.get("answer") or "")
    returned_sources = {
        str(source.get("paper_id") or "")
        for source in answer_payload.get("sources") or []
        if isinstance(source, dict)
    }
    cited_sources = _cited_paper_ids(answer_text)
    all_sources = returned_sources | cited_sources

    missing_sources = [source for source in case.expected_sources if source not in all_sources]
    missing_terms = [term for term in case.required_terms if not _required_term_present(term, answer_text)]
    forbidden_hits = [term for term in case.forbidden_terms if _norm(term) in _norm(answer_text)]
    invalid_citations = _invalid_citations(answer_text)
    generation_error = answer_payload.get("generation_error")
    no_answer = bool(answer_payload.get("no_answer"))

    score_parts = {
        "sources": 0.0 if missing_sources else 1.0,
        "required_terms": _coverage_score(case.required_terms, missing_terms),
        "forbidden_terms": 0.0 if forbidden_hits else 1.0,
        "citations": 0.0 if invalid_citations else 1.0,
        "generation": 0.0 if generation_error or no_answer else 1.0,
    }
    score = sum(score_parts.values()) / len(score_parts)
    return {
        "id": case.id,
        "question": case.question,
        "score": round(score, 4),
        "passes": score == 1.0,
        "score_parts": score_parts,
        "expected_sources": case.expected_sources,
        "returned_sources": sorted(returned_sources),
        "cited_sources": sorted(cited_sources),
        "missing_sources": missing_sources,
        "required_terms": case.required_terms,
        "missing_terms": missing_terms,
        "forbidden_hits": forbidden_hits,
        "invalid_citations": invalid_citations,
        "generation_error": generation_error,
        "no_answer": no_answer,
        "answer": answer_text,
    }


def run_eval(
    provider: str,
    model: str | None = None,
    cases_path: Path = DEFAULT_CASES_PATH,
    metadata_db_path: str = "data/metadata.duckdb",
    graph_db_path: str = "data/graphs/global_kg",
    limit: int = 8,
    timeout_seconds: float | None = None,
) -> dict[str, Any]:
    cases = load_cases(cases_path)
    llm_router = LLMRouter.from_config_file("config.yaml")
    retriever = HybridRetriever(
        KGRetriever(
            metadata_db_path=metadata_db_path,
            graph_db_path=graph_db_path,
        )
    )
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)

    case_reports = []
    for case in cases:
        overrides: dict[str, Any] = {}
        if timeout_seconds is not None:
            overrides["timeout_seconds"] = timeout_seconds
        answer = responder.answer(
            case.question,
            limit=limit,
            provider=provider,
            model=model,
            overrides=overrides or None,
        )
        case_reports.append(evaluate_answer(case, answer.to_dict()))

    return {
        "provider": provider,
        "model": model or llm_router.provider_default_model(provider),
        "case_count": len(case_reports),
        "summary": summarize(case_reports),
        "cases": case_reports,
    }


def summarize(case_reports: list[dict[str, Any]]) -> dict[str, Any]:
    if not case_reports:
        return {"average_score": 0.0, "pass_count": 0, "case_count": 0}
    average = sum(float(case["score"]) for case in case_reports) / len(case_reports)
    return {
        "average_score": round(average, 4),
        "pass_count": sum(1 for case in case_reports if case.get("passes")),
        "case_count": len(case_reports),
        "invalid_citation_cases": [
            case["id"] for case in case_reports if case.get("invalid_citations")
        ],
        "missing_source_cases": [
            case["id"] for case in case_reports if case.get("missing_sources")
        ],
        "missing_term_cases": [
            case["id"] for case in case_reports if case.get("missing_terms")
        ],
        "forbidden_hit_cases": [
            case["id"] for case in case_reports if case.get("forbidden_hits")
        ],
        "generation_error_cases": [
            case["id"] for case in case_reports if case.get("generation_error") or case.get("no_answer")
        ],
    }


def _coverage_score(required_terms: list[str], missing_terms: list[str]) -> float:
    if not required_terms:
        return 1.0
    return (len(required_terms) - len(missing_terms)) / len(required_terms)


def _norm(text: str) -> str:
    normalized = re.sub(r"[-_/]+", " ", str(text or "").lower())
    return re.sub(r"\s+", " ", normalized).strip()


def _required_term_present(term: str, text: str) -> bool:
    return any(_term_present(option, text) for option in _term_options(term))


def _term_options(term: str) -> list[str]:
    return [option.strip() for option in str(term or "").split("|") if option.strip()]


def _term_present(term: str, text: str) -> bool:
    term_norm = _norm(term)
    text_norm = _norm(text)
    if term_norm in text_norm:
        return True
    compact_term = re.sub(r"\s+", "", term_norm)
    compact_text = re.sub(r"\s+", "", text_norm)
    if compact_term and compact_term in compact_text:
        return True

    term_tokens = {_soft_stem(token) for token in re.findall(r"[a-z0-9]+", term_norm)}
    text_tokens = {_soft_stem(token) for token in re.findall(r"[a-z0-9]+", text_norm)}
    return bool(term_tokens) and term_tokens <= text_tokens


def _soft_stem(token: str) -> str:
    if token in {"grade", "graded", "grader", "graders", "grading"}:
        return "grade"
    if token in {"rate", "rated", "rates", "rater", "raters", "rating", "ratings"}:
        return "rate"
    if token.endswith("ies") and len(token) > 4:
        return token[:-3] + "y"
    if token.endswith("s") and len(token) > 3:
        return token[:-1]
    return token


def _cited_paper_ids(answer_text: str) -> set[str]:
    ids: set[str] = set()
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        for value in re.split(r"[,;]\s*", bracketed):
            value = value.strip()
            if value.startswith("arxiv:") or value.startswith("doi:") or value.startswith("p"):
                ids.add(value)
    return ids


def _invalid_citations(answer_text: str) -> list[str]:
    invalid: list[str] = []
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        parts = [part.strip() for part in re.split(r"[,;]\s*", bracketed) if part.strip()]
        for part in parts:
            if re.fullmatch(r"\d+", part):
                invalid.append(part)
            elif re.fullmatch(r"\d+(?:\s*[-,]\s*\d+)+", part):
                invalid.append(part)
            elif not (
                part.startswith("arxiv:")
                or part.startswith("doi:")
                or part.startswith("p")
            ):
                invalid.append(part)
    return invalid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Evaluate Phase 4 grounded answers against a small gold question set.")
    parser.add_argument("--provider", action="append", required=True, help="Provider to evaluate. Can be passed multiple times.")
    parser.add_argument("--model", default=None, help="Optional model override. Applied to all providers.")
    parser.add_argument("--cases", default=str(DEFAULT_CASES_PATH), help="Path to Phase 4 eval cases JSON.")
    parser.add_argument("--metadata-db", default="data/metadata.duckdb", help="DuckDB metadata path.")
    parser.add_argument("--graph-db", default="data/graphs/global_kg", help="Kuzu graph path.")
    parser.add_argument("--limit", type=int, default=8, help="Retrieval limit.")
    parser.add_argument("--timeout-seconds", type=float, default=None, help="Optional LLM timeout override.")
    parser.add_argument("--output", default=None, help="Optional path to write JSON report.")
    args = parser.parse_args(argv)

    reports = []
    for provider in args.provider:
        reports.append(
            run_eval(
                provider=provider,
                model=args.model,
                cases_path=Path(args.cases),
                metadata_db_path=args.metadata_db,
                graph_db_path=args.graph_db,
                limit=args.limit,
                timeout_seconds=args.timeout_seconds,
            )
        )

    report = {"providers": reports}
    rendered = json.dumps(report, indent=2, ensure_ascii=False)
    if args.output:
        Path(args.output).parent.mkdir(parents=True, exist_ok=True)
        Path(args.output).write_text(rendered + "\n", encoding="utf-8")
    print(rendered.encode("ascii", "backslashreplace").decode("ascii"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
