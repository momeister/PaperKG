import json
import shutil
from pathlib import Path
from uuid import uuid4

from storage.metadata_db import MetadataDB


def test_metadata_db_insert_and_query(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    db = MetadataDB(str(db_path))

    record = {
        "source": "arxiv",
        "source_id": "1234.5678",
        "title": "Test Paper",
        "abstract": "Abstract",
        "authors": ["Alice", "Bob"],
        "year": 2024,
        "doi": "10.1000/test",
        "pdf_url": "https://example.org/test.pdf",
        "landing_page_url": "https://arxiv.org/abs/1234.5678",
        "references": ["arxiv:1111.1111"],
        "citations": [{"paperId": "S2:2222"}],
        "version": 1,
    }

    db.insert_paper(record)
    assert db.count_papers() == 1

    loaded = db.get_paper("arxiv:1234.5678")
    assert loaded is not None
    assert loaded["title"] == "Test Paper"
    assert loaded["references"] == ["arxiv:1111.1111"]
    assert loaded["citations"] == [{"paperId": "S2:2222"}]
    assert loaded["citation_count"] == 1

    result = db.search_by_title("Test")
    assert len(result) == 1

    db.log_dedup("arxiv:1234.5678", "arxiv:1111.1111", "same_doi")
    dedup_count = db.conn.execute("SELECT COUNT(*) FROM dedup_log").fetchone()[0]
    assert dedup_count == 1

    db.close()


def test_metadata_db_recreates_empty_placeholder_file(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    db_path.write_bytes(b"")

    db = MetadataDB(str(db_path))
    assert db.count_papers() == 0
    db.close()


def test_metadata_db_resolves_pdf_storage_id_to_arxiv_metadata() -> None:
    root = Path("test-output") / f"metadata-alias-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db = MetadataDB(str(root / "metadata.duckdb"))
    try:
        db.insert_paper(
            {
                "source": "arxiv",
                "source_id": "2509.08759",
                "title": "A Very Real Paper Title With Punctuation",
                "year": 2025,
            }
        )

        resolved = db.resolve_paper("arxiv__a-very-real-paper-title-with-punctuation__2509.08759")

        assert resolved is not None
        assert resolved["id"] == "arxiv:2509.08759"
        assert resolved["year"] == 2025
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_metadata_db_resolves_legacy_arxiv_storage_id() -> None:
    root = Path("test-output") / f"metadata-legacy-arxiv-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db = MetadataDB(str(root / "metadata.duckdb"))
    try:
        db.insert_paper(
            {
                "source": "arxiv",
                "source_id": "q-bio/0612009",
                "title": "The Neurobiology Of Thinking, Identity, And Geniality",
                "year": 2006,
            }
        )

        resolved = db.resolve_paper("arxiv__the-neurobiology-of-thinking-identity-and-geniality__q-bio_0612009")
        canonical = db.ensure_paper_record(
            "arxiv__the-neurobiology-of-thinking-identity-and-geniality__q-bio_0612009",
            title="The Neurobiology Of Thinking, Identity, And Geniality",
            year=2006,
        )

        assert resolved is not None
        assert resolved["id"] == "arxiv:q-bio/0612009"
        assert canonical == "arxiv:q-bio/0612009"
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


def test_clear_extraction_results_keeps_papers(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))
    db.insert_paper({
        "id": "paper_001",
        "source": "arxiv",
        "source_id": "1234.5678",
        "title": "A Paper",
    })
    db.save_extraction_result(
        paper_id="paper_001",
        llm_provider="fake",
        llm_model="fake-model",
        concepts=[{"label": "concept"}],
    )

    db.clear_extraction_results()

    assert db.get_paper("paper_001") is not None
    assert db.get_paper_extractions("paper_001") == []
    db.close()


def test_metadata_db_persists_extended_extraction_metadata(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))

    result_id = db.save_extraction_result(
        paper_id="paper_001",
        llm_provider="fake",
        llm_model="fake-model",
        paper_type="survey",
        concepts=[{"label": "Q-learning", "confidence": 0.82}],
        methods=[
            {
                "label": "Emotion-Modulated Q-learning Taxonomy",
                "domain": "reinforcement learning",
                "source_type": "paper_contribution",
            }
        ],
        concept_candidates=[{"label": "Machine Learning", "confidence": 0.62}],
        method_candidates=[{"label": "Q-learning", "confidence": 0.62}],
        claims=[
            {
                "statement": "The field lacks reproduced experimental scenarios.",
                "evidence_type": "review",
                "negated": False,
                "attributed_to": "this_paper",
            }
        ],
        cross_domain_hints=[
            {
                "field": "Developmental Robotics",
                "why_applicable": "Reward shaping transfers to energy management.",
            }
        ],
        terminology_conflicts=[
            {
                "term": "valence",
                "this_field": "emotion polarity",
                "other_field": "chemistry - electron affinity",
            }
        ],
        temporal_coverage={"paper_year": 2024, "reviewed_period": "2007-2023"},
        mathematical_content={
            "has_formulas": True,
            "formula_types": ["reward_function"],
        },
    )

    loaded = db.get_extraction_result(result_id)

    assert loaded is not None
    assert loaded["paper_type"] == "survey"
    assert loaded["methods"][0]["source_type"] == "paper_contribution"
    assert loaded["concept_candidates"][0]["label"] == "Machine Learning"
    assert loaded["method_candidates"][0]["label"] == "Q-learning"
    assert loaded["cross_domain_hints"][0]["field"] == "Developmental Robotics"
    assert loaded["terminology_conflicts"][0]["term"] == "valence"
    assert loaded["temporal_coverage"]["reviewed_period"] == "2007-2023"
    assert loaded["mathematical_content"]["formula_types"] == ["reward_function"]
    db.close()


def test_save_extraction_result_infers_failed_status_from_fatal_payload(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))

    result_id = db.save_extraction_result(
        paper_id="paper_001",
        llm_provider="lm_studio",
        llm_model="qwen",
        raw_response=json.dumps(
            {
                "extraction_parse_quality": "failed",
                "fatal_llm_error": True,
                "failure_reason": "LLM extraction failed: LM Studio has no model loaded.",
                "concepts": [],
                "methods": [],
                "call_diagnostics": [
                    {
                        "call_type": "structural",
                        "parse_quality": "failed",
                        "raw_excerpt": "LLM call failed: No models loaded",
                    }
                ],
            }
        ),
    )

    row = db.get_extraction_result(result_id)
    assert row is not None
    assert row["extraction_status"] == "failed"
    assert "no model loaded" in row["error_message"].lower()
    assert db.list_entity_review_queue() == []
    db.close()


def test_metadata_db_enqueues_pending_entities_for_review(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))

    db.save_extraction_result(
        paper_id="paper_001",
        llm_provider="fake",
        llm_model="fake-model",
        concepts=[
            {
                "label": "Unseen Concept",
                "entity_type": "DomainConcept",
                "confidence": 0.92,
                "review_status": "pending",
                "suggested_canonical": "Unseen Concept",
                "merge_candidates": [{"label": "Known Concept", "score": 0.81}],
                "evidence_span": "Unseen Concept is discussed.",
            },
            {
                "label": "Approved Concept",
                "review_status": "approved",
            },
        ],
    )

    pending = db.list_entity_review_queue()

    assert len(pending) == 1
    assert pending[0]["label"] == "Unseen Concept"
    assert pending[0]["merge_candidates"][0]["label"] == "Known Concept"
    db.close()


def test_metadata_db_persists_extraction_quality(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))

    db.save_extraction_quality(
        paper_id="paper_001",
        concept_count=42,
        method_count=8,
        claim_count=9,
        has_formulas=True,
        auto_detected_concepts=4,
        parse_quality="trimmed",
        call_1_tokens_used=5500,
        call_2_tokens_used=3100,
        duration_seconds=12.5,
        model="qwen3.6:35b",
    )

    rows = db.list_extraction_quality("paper_001")

    assert len(rows) == 1
    assert rows[0]["concept_count"] == 42
    assert rows[0]["has_formulas"] is True
    assert rows[0]["auto_detected_concepts"] == 4
    assert rows[0]["parse_quality"] == "trimmed"
    assert rows[0]["model"] == "qwen3.6:35b"
    db.close()


def test_clear_all_removes_mutable_metadata(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))
    db.insert_paper({
        "id": "paper_001",
        "source": "arxiv",
        "source_id": "1234.5678",
        "title": "A Paper",
    })
    db.save_extraction_result(
        paper_id="paper_001",
        llm_provider="fake",
        llm_model="fake-model",
    )

    db.clear_all()

    assert db.count_papers() == 0
    assert db.get_paper_extractions("paper_001") == []
    db.close()


def test_metadata_db_persists_batch_jobs_and_items(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))

    db.upsert_batch_job(
        job_id="job_001",
        status="processing",
        papers_total=2,
        request_payload={"paper_ids": ["p1", "p2"]},
    )
    db.upsert_batch_job_item("job_001", "p1", "/tmp/p1.pdf", "completed", attempts=1)
    db.upsert_batch_job_item("job_001", "p2", "/tmp/p2.pdf", "failed", attempts=2, error_message="parse")

    job = db.get_batch_job("job_001")
    assert job is not None
    assert job["status"] == "processing"
    assert job["request_payload"]["paper_ids"] == ["p1", "p2"]

    items = db.get_batch_job_items("job_001")
    assert [item["status"] for item in items] == ["completed", "failed"]
    db.close()


def test_metadata_db_persists_entity_embeddings(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "metadata.duckdb"))

    db.upsert_entity_embedding(
        label="Neural Network",
        vector=[1.0, 0.0],
        model="test-model",
        backend="test",
        dimension=2,
    )

    loaded = db.get_entity_embedding("neural   network", "test-model")
    assert loaded is not None
    assert loaded["label"] == "Neural Network"
    assert loaded["vector"] == [1.0, 0.0]
    assert len(db.list_entity_embeddings("test-model")) == 1
    db.close()
