from __future__ import annotations

import os
from pathlib import Path

from quality import benchmark_suite
from quality.benchmark_suite import SuiteConfig, latest_suite_report, run_suite


def test_benchmark_suite_writes_reports_and_summary(monkeypatch, tmp_path: Path) -> None:
    class FakeLLMRouter:
        default_provider = "fake"

        def provider_default_model(self, provider: str) -> str:
            return "fake-model"

    def fake_extraction_policy(**kwargs):
        policy = kwargs["policy"]
        return {
            "policy": policy,
            "duration_seconds": 0.1,
            "summary": {"case_count": 1, "average_f1": 1.0 if policy == "auto" else 0.5},
            "cases": [],
            "pdf_provenance": [],
            "warnings": [],
        }

    def fake_answer_benchmark(**kwargs):
        return {
            "duration_seconds": 0.2,
            "summary": {"average_score": 0.75, "cross_paper_score": 0.5},
            "cases": [],
            "warnings": ["answer warning"],
        }

    monkeypatch.setattr(benchmark_suite.LLMRouter, "from_config_file", staticmethod(lambda path: FakeLLMRouter()))
    monkeypatch.setattr(benchmark_suite, "_run_extraction_policy", fake_extraction_policy)
    monkeypatch.setattr(benchmark_suite, "_run_answer_benchmark", fake_answer_benchmark)

    report = run_suite(
        SuiteConfig(
            suite="core",
            provider=None,
            model=None,
            context_policy="auto",
            compare_context_policies=["auto", "chunk"],
            answer_context_mode="kg",
            download_missing=False,
            metadata_db_path=str(tmp_path / "missing.duckdb"),
            graph_db_path=str(tmp_path / "graph"),
            pdf_base_dir=str(tmp_path / "pdfs"),
            output_dir=tmp_path / "benchmarks",
            isolated_db=True,
        )
    )

    run_dir = next((tmp_path / "benchmarks").iterdir())
    assert (run_dir / "report.json").exists()
    assert (run_dir / "report.md").exists()
    assert report["summary"]["provider"] == "fake"
    assert report["summary"]["model"] == "fake-model"
    assert report["summary"]["extraction_score"] == 1.0
    assert report["summary"]["answer_score"] == 0.75
    assert report["summary"]["cross_paper_score"] == 0.5
    assert report["summary"]["warning_count"] == 1


def test_latest_suite_report_loads_newest_report(tmp_path: Path) -> None:
    older = tmp_path / "20260101_000000"
    newer = tmp_path / "20260102_000000"
    older.mkdir()
    newer.mkdir()
    (older / "report.json").write_text('{"run_id": "old", "summary": {"answer_score": 0.1}}', encoding="utf-8")
    (newer / "report.json").write_text('{"run_id": "new", "summary": {"answer_score": 0.9}}', encoding="utf-8")
    os.utime(older / "report.json", (1000, 1000))
    os.utime(newer / "report.json", (2000, 2000))

    report = latest_suite_report(tmp_path)

    assert report["run_id"] == "new"
    assert report["summary"]["answer_score"] == 0.9
