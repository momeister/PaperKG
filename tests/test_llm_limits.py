"""Anbieter-Limits waehrend der Extraktion: einordnen, anzeigen, abbrechen.

Vorher war ein HTTP 429 am Ende nicht mehr von einem Parser-Fehler zu
unterscheiden, und der Batch feuerte stur weiter — bei 480 ausgewaehlten Papern
also 480 Absagen gegen dasselbe erschoepfte Kontingent.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from extraction.batch_processor import BatchProcessor
from query.llm_errors import classify_llm_error, is_provider_limit, parse_tagged_error, tag_error
from storage.metadata_db import MetadataDB


# --------------------------------------------------------------------------- #
# Einordnung                                                                   #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("HTTP 429; Client error '429 Too Many Requests'; retry_after=30s", "rate_limit"),
        ("Rate limit reached for gpt-4", "rate_limit"),
        ("HTTP 429; insufficient_quota: You exceeded your current quota", "quota"),
        ("RESOURCE_EXHAUSTED: billing account has no credit", "quota"),
        ("HTTP 401; Incorrect API key provided", "auth"),
        ("This model's maximum context length is 8192 tokens", "context_length"),
        ("httpx.ConnectError: [Errno 111] Connection refused", "connection"),
        ("LLM call failed: ValueError('kaputtes JSON')", "unknown"),
    ],
)
def test_classify_llm_error(raw: str, expected: str) -> None:
    kind, human = classify_llm_error(raw)
    assert kind == expected
    assert human.strip()


def test_only_terminal_kinds_stop_a_batch() -> None:
    assert is_provider_limit("quota")
    assert is_provider_limit("rate_limit")
    assert is_provider_limit("auth")
    # Voruebergehende/technische Fehler betreffen nur das einzelne Paper.
    assert not is_provider_limit("connection")
    assert not is_provider_limit("context_length")
    assert not is_provider_limit("unknown")
    assert not is_provider_limit(None)


def test_tag_and_parse_roundtrip() -> None:
    tagged = tag_error("quota", "Kontingent aufgebraucht.")
    assert parse_tagged_error(tagged) == ("quota", "Kontingent aufgebraucht.")
    # Untagged bleibt untagged — alte Meldungen in der DB brechen nicht.
    assert parse_tagged_error("irgendein alter Fehler") == (None, "irgendein alter Fehler")
    assert parse_tagged_error(None) == (None, "")


def test_router_error_carries_status_and_retry_after() -> None:
    """Ohne den Statuscode im Text kann die Heuristik spaeter nichts erkennen."""
    import httpx

    from query.llm_router import LLMRouter

    request = httpx.Request("POST", "https://api.example.com/v1/chat/completions")
    response = httpx.Response(429, headers={"retry-after": "30"}, text="Too Many Requests", request=request)
    error = LLMRouter._http_status_runtime_error(
        httpx.HTTPStatusError("429 error", request=request, response=response)
    )
    message = str(error)
    assert "HTTP 429" in message
    assert "retry_after=30s" in message
    assert classify_llm_error(message)[0] == "rate_limit"


# --------------------------------------------------------------------------- #
# Batch-Abbruch                                                                #
# --------------------------------------------------------------------------- #


class _QuotaPipeline:
    """Extraktions-Pipeline, die jeden Aufruf mit erschoepftem Kontingent quittiert."""

    def __init__(self) -> None:
        self.calls = 0

    def process(self, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        raise RuntimeError(tag_error("quota", "LLM-Kontingent aufgebraucht."))


def _processor(tmp_path: Path, pipeline: Any, *, max_retries: int = 1) -> BatchProcessor:
    processor = BatchProcessor.__new__(BatchProcessor)
    processor.llm_router = None
    processor.parser_router = None
    processor.embedding_engine = None
    processor.metadata_db = None
    processor.metadata_db_factory = lambda: MetadataDB(str(tmp_path / "metadata.duckdb"))
    processor.link_concepts = False
    processor.embed_concepts = False
    processor.max_retries = max_retries
    processor.retry_delay_seconds = 0.0
    processor.extraction_pipeline = pipeline
    processor._job_states = {}
    return processor


def test_batch_stops_at_the_first_quota_error(tmp_path: Path) -> None:
    pipeline = _QuotaPipeline()
    processor = _processor(tmp_path, pipeline)
    paper_ids = [f"p{i}" for i in range(50)]

    status = processor.process_papers(
        paper_ids,
        pdf_paths={},
        texts={pid: "Titel\n\nAbstract:\nText" for pid in paper_ids},
        job_id="job-quota",
    )

    # Genau ein Paper wird versucht, dann ist Schluss — nicht 50.
    assert pipeline.calls == 1
    assert status.status == "failed"
    assert status.papers_failed == 1
    kind, message = parse_tagged_error(status.error_message)
    assert kind == "quota"
    assert "Batch nach 1 von 50 Papern gestoppt" in message

    # Die uebrigen Paper bleiben offen und sind nachholbar.
    with MetadataDB(str(tmp_path / "metadata.duckdb")) as db:
        items = db.get_batch_job_items("job-quota")
    by_status: dict[str, int] = {}
    for item in items:
        by_status[item["status"]] = by_status.get(item["status"], 0) + 1
    assert by_status["failed"] == 1
    assert by_status["pending"] == 49


def test_quota_error_is_not_retried(tmp_path: Path) -> None:
    """Ein aufgebrauchtes Kontingent erholt sich nicht in 20 Sekunden."""
    pipeline = _QuotaPipeline()
    processor = _processor(tmp_path, pipeline, max_retries=3)
    processor.process_papers(["p1"], pdf_paths={}, texts={"p1": "Text"}, job_id="job-noretry")
    assert pipeline.calls == 1


class _FlakyPipeline:
    """Scheitert einmal mit einem Rate-Limit, danach laeuft es."""

    def __init__(self) -> None:
        self.calls = 0

    def process(self, paper_id: str, *_args: Any, **_kwargs: Any) -> Any:
        self.calls += 1
        if self.calls == 1:
            raise RuntimeError("HTTP 429; Too Many Requests; retry_after=1s")
        from extraction.entity_extractor import ExtractionResult

        return ExtractionResult(
            paper_id=paper_id,
            paper_type="empirical",
            concepts=[{"label": "Konzept", "confidence": 0.9}],
            methods=[],
            concept_candidates=[],
            method_candidates=[],
            relations=[],
            claims=[],
            cross_domain_hints=[],
            terminology_conflicts=[],
            temporal_coverage={},
            mathematical_content={},
            raw_response={},
        )


def test_rate_limit_is_retried_once_before_giving_up(tmp_path: Path) -> None:
    pipeline = _FlakyPipeline()
    processor = _processor(tmp_path, pipeline, max_retries=1)
    status = processor.process_papers(["p1"], pdf_paths={}, texts={"p1": "Text"}, job_id="job-flaky")
    assert pipeline.calls == 2
    assert status.papers_processed == 1
    assert status.papers_failed == 0
