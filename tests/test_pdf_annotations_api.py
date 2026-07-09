from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from api import product_main
from storage.metadata_db import MetadataDB


def _fixture_db(path: Path) -> None:
    with MetadataDB(str(path)) as db:
        db.insert_paper(
            {
                "id": "paper-1",
                "source": "fixture",
                "source_id": "paper-1",
                "title": "Anchored Notes",
                "abstract": "A paper to annotate.",
                "year": 2024,
            }
        )


def test_pdf_annotation_crud_roundtrip(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    _fixture_db(db_path)
    client = TestClient(product_main.app)
    dbp = str(db_path)

    # Empty at first.
    listed = client.get("/papers/paper-1/annotations", params={"metadata_db_path": dbp})
    assert listed.status_code == 200
    assert listed.json()["annotations"] == []

    # Create a highlight annotation with two rects.
    create = client.post(
        "/papers/paper-1/annotations",
        json={
            "metadata_db_path": dbp,
            "page_number": 2,
            "kind": "highlight",
            "rects": [
                {"x": 0.1, "y": 0.2, "width": 0.3, "height": 0.02},
                {"x": 0.1, "y": 0.23, "width": 0.25, "height": 0.02},
            ],
            "quote": "GenAU vision-language framework",
            "body": "Kernbeitrag des Papers.",
        },
    )
    assert create.status_code == 200, create.text
    ann = create.json()["annotation"]
    assert ann["id"].startswith("pdfann_")
    assert ann["paper_id"] == "paper-1"
    assert ann["page_number"] == 2
    assert ann["kind"] == "highlight"
    assert len(ann["rects"]) == 2
    assert ann["rects"][0]["x"] == 0.1
    assert ann["color"]  # default amber applied
    annotation_id = ann["id"]

    # List now returns it.
    listed2 = client.get("/papers/paper-1/annotations", params={"metadata_db_path": dbp})
    assert len(listed2.json()["annotations"]) == 1

    # Patch the body.
    patched = client.patch(
        f"/pdf-annotations/{annotation_id}",
        json={"metadata_db_path": dbp, "body": "Aktualisierte Notiz."},
    )
    assert patched.status_code == 200
    assert patched.json()["annotation"]["body"] == "Aktualisierte Notiz."
    # Rects unchanged by a body-only patch.
    assert len(patched.json()["annotation"]["rects"]) == 2

    # Delete it.
    deleted = client.delete(
        f"/pdf-annotations/{annotation_id}", params={"metadata_db_path": dbp}
    )
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    final = client.get("/papers/paper-1/annotations", params={"metadata_db_path": dbp})
    assert final.json()["annotations"] == []


def test_pdf_annotation_point_kind_and_validation(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    _fixture_db(db_path)
    client = TestClient(product_main.app)
    dbp = str(db_path)

    # A point note carries a single tiny rect around the click.
    create = client.post(
        "/papers/paper-1/annotations",
        json={
            "metadata_db_path": dbp,
            "page_number": 1,
            "kind": "point",
            "rects": [{"x": 0.5, "y": 0.5, "width": 0.01, "height": 0.01}],
            "body": "Frage hier klären.",
        },
    )
    assert create.status_code == 200
    assert create.json()["annotation"]["kind"] == "point"

    # Unknown kind is rejected.
    bad = client.post(
        "/papers/paper-1/annotations",
        json={"metadata_db_path": dbp, "page_number": 1, "kind": "scribble", "body": "x"},
    )
    assert bad.status_code == 400

    # Patch/delete on a missing id → 404 / deleted=False.
    missing = client.patch(
        "/pdf-annotations/pdfann_missing",
        json={"metadata_db_path": dbp, "body": "y"},
    )
    assert missing.status_code == 404
    gone = client.delete(
        "/pdf-annotations/pdfann_missing", params={"metadata_db_path": dbp}
    )
    assert gone.json()["deleted"] is False
