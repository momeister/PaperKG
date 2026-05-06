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
