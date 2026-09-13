import uuid

import pytest
from storage.metadata_db import MetadataDB


def test_pdf_citation_survives_reopen_with_complete_quote(tmp_path):
    path = str(tmp_path / "notes.duckdb")
    anchors = [
        {
            "page_number": p,
            "rects": [{"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.04}],
        }
        for p in [1, 2]
    ]
    quote = "Originaltext mit Ligatur ﬁ und Zeilenumbruch\n" * 40
    with MetadataDB(path) as db:
        note = db.create_note("project", "Test")
        citation = db.add_note_citation(
            note["id"],
            {
                "paper_id": "arxiv:123",
                "pdf_excerpt": quote,
                "reference_text": quote,
                "pdf_anchors": anchors,
            },
        )
    with MetadataDB(path) as db:
        loaded = db.get_note(note["id"])["citations"][0]
        assert loaded["id"] == citation["id"]
        assert loaded["pdf_anchors"] == anchors
        assert loaded["pdf_excerpt"] == quote
        assert db.get_note_citation(citation["id"])["pdf_anchors"] == anchors


def test_legacy_citation_ids_unchanged_and_distinct_positions_get_new_ids(tmp_path):
    with MetadataDB(str(tmp_path / "notes.duckdb")) as db:
        citation = {
            "paper_id": "arxiv:123",
            "reference_text": "Same text",
            "pdf_excerpt": "same text",
        }
        expected = (
            "cite_"
            + uuid.uuid5(
                uuid.NAMESPACE_URL, "note|arxiv:123||same text|same text|0"
            ).hex
        )
        assert db._stable_note_citation_id("note", citation) == expected
        a = {**citation, "pdf_anchors": [{"page_number": 1, "rects": []}]}
        b = {**citation, "pdf_anchors": [{"page_number": 2, "rects": []}]}
        assert db._stable_note_citation_id("note", a) != db._stable_note_citation_id(
            "note", b
        )


def test_atomic_citation_save_rolls_back_markdown_and_new_note(tmp_path, monkeypatch):
    with MetadataDB(str(tmp_path / "notes.duckdb")) as db:
        note = db.create_note("project", "Old", "Original")

        def fail(*args):
            raise RuntimeError("disk error")

        monkeypatch.setattr(db, "add_note_citation", fail)
        with pytest.raises(RuntimeError):
            db.save_note_with_citations(
                note_id=note["id"], markdown="New citation", citations=[{}]
            )
        assert db.get_note(note["id"])["markdown"] == "Original"
        with pytest.raises(RuntimeError):
            db.save_note_with_citations(
                project_id="project", markdown="New citation", citations=[{}]
            )
        assert len(db.list_notes()) == 1


def test_api_rejects_invalid_pdf_anchor():
    from api.product_main import app
    from fastapi.testclient import TestClient

    response = TestClient(app).patch(
        "/notes/fixture",
        json={
            "citations": [
                {
                    "pdf_anchors": [
                        {
                            "page_number": 0,
                            "rects": [{"x": 2, "y": 0, "width": 1, "height": 1}],
                        }
                    ]
                }
            ]
        },
    )
    assert response.status_code == 422


def test_additive_migration_preserves_existing_citations(tmp_path):
    path = str(tmp_path / "old.duckdb")
    with MetadataDB(path) as db:
        note = db.create_note("project", "Old", "Original")
        citation = db.add_note_citation(
            note["id"], {"paper_id": "arxiv:123", "pdf_excerpt": "Old quote"}
        )
        db.conn.execute("DROP INDEX idx_note_citations_note")
        db.conn.execute("ALTER TABLE note_citations DROP COLUMN pdf_anchors")
        db.conn.execute("CREATE INDEX idx_note_citations_note ON note_citations(note_id)")
    with MetadataDB(path) as db:
        loaded = db.get_note(note["id"])["citations"][0]
        assert loaded["id"] == citation["id"]
        assert loaded["pdf_excerpt"] == "Old quote"
        assert loaded["pdf_anchors"] is None
