from __future__ import annotations

from quality import kg_health
from quality.kg_health import build_health_report
from storage.metadata_db import MetadataDB


def test_build_health_report_summarizes_core_tables(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    graph_dir = tmp_path / "global_kg"
    pdf_dir.mkdir()
    graph_dir.mkdir()
    (pdf_dir / "paper.pdf").write_bytes(b"%PDF-1.4\n")

    with MetadataDB(str(db_path)) as db:
        db.insert_paper(
            {
                "id": "paper",
                "source": "fixture",
                "source_id": "paper",
                "title": "A Paper",
                "has_full_text": True,
                "doi": "10.1/example",
            }
        )
        db.save_extraction_result(
            paper_id="paper",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[{"label": "Pending", "review_status": "pending"}],
            methods=[],
        )
        db.save_extraction_quality(
            paper_id="paper",
            concept_count=1,
            method_count=0,
            claim_count=0,
            has_formulas=False,
            auto_detected_concepts=0,
            parse_quality="clean",
            duration_seconds=1.25,
            model="fake-model",
        )
        db.upsert_entity_embedding(
            label="Pending",
            vector=[0.1, 0.2],
            model="fake-embedding",
            backend="test",
            dimension=2,
        )

    report = build_health_report(
        metadata_db_path=str(db_path),
        graph_db_path=str(graph_dir),
        pdf_base_dir=str(pdf_dir),
    )

    assert report["metadata_db"]["paper_count"] == 1
    assert report["papers"]["full_text_coverage"] == 1.0
    assert report["extractions"]["paper_success_coverage"] == 1.0
    assert report["review_queue"]["pending"] == 1
    assert report["embeddings"]["total"] == 1
    assert report["quality_telemetry"]["by_parse_quality"] == {"clean": 1}
    assert not report["warnings"]
    assert report["action_items"][0]["kind"] == "review_queue"


def test_build_health_report_uses_duckdb_fallback_when_kuzu_is_unavailable(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    with MetadataDB(str(db_path)) as db:
        db.insert_paper(
            {
                "id": "paper",
                "source": "fixture",
                "source_id": "paper",
                "title": "A Paper",
            }
        )
        db.upsert_entity_embedding(
            label="Fallback",
            vector=[0.1, 0.2],
            model="fake-embedding",
            backend="test",
            dimension=2,
        )

    monkeypatch.setattr(kg_health, "_kuzu_available", lambda: False)
    report = build_health_report(
        metadata_db_path=str(db_path),
        graph_db_path=str(tmp_path / "missing-graph"),
        pdf_base_dir=str(tmp_path / "missing-pdfs"),
    )

    assert report["graph_db"]["backend"] == "duckdb-fallback"
    assert "Kuzu graph path does not exist; graph-only features may be unavailable." not in report["warnings"]
    assert report["action_items"][0]["kind"] == "graph_backend"


def test_build_health_report_handles_missing_metadata_db(tmp_path) -> None:
    report = build_health_report(metadata_db_path=str(tmp_path / "missing.duckdb"))

    assert report["status"] == "error"
    assert report["metadata_db"]["exists"] is False
