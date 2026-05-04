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
        "version": 1,
    }

    db.insert_paper(record)
    assert db.count_papers() == 1

    loaded = db.get_paper("arxiv:1234.5678")
    assert loaded is not None
    assert loaded["title"] == "Test Paper"

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
