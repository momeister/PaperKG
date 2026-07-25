"""Was die Extraktions-Library meldet — und was der Batch daraus wirklich macht.

Zwei Zahlen standen auf derselben Seite nebeneinander: "285 extrahierbar" (nur
Paper mit lokalem PDF) und "Nicht extrahiert (480)" (zusaetzlich Paper, die nur
einen Abstract haben). Beide stimmten, aber nichts sagte das. Diese Tests halten
fest, welche Felder die Zaehlung traegt und dass Abstract-only wirklich laeuft.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from api import product_main
from storage.metadata_db import MetadataDB


def _library(client: TestClient, db_path: Path, pdf_dir: Path, project_id: str | None = None) -> dict[str, Any]:
    params: dict[str, Any] = {"metadata_db_path": str(db_path), "pdf_base_dir": str(pdf_dir)}
    if project_id:
        params["project_id"] = project_id
    response = client.get("/extraction/library", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def test_library_marks_abstract_only_papers_as_extractable(tmp_path: Path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    with MetadataDB(str(db_path)) as db:
        db.insert_paper({
            "id": "mit-abstract",
            "source": "fixture", "source_id": "a", "title": "Hat Abstract",
            "abstract": "Ein aussagekraeftiger Abstract ueber Graphen.", "year": 2024,
        })
        db.insert_paper({
            "id": "ohne-alles",
            "source": "fixture", "source_id": "b", "title": "Nur Titel", "year": 2024,
        })

    with TestClient(product_main.app) as client:
        items = {i["paper_id"]: i for i in _library(client, db_path, pdf_dir)["items"]}

    # Kein PDF, aber ein Abstract -> extrahierbar (Abstract-only-Fallback).
    assert items["mit-abstract"]["pdf_available"] is False
    assert items["mit-abstract"]["abstract_available"] is True
    # Weder PDF noch Abstract -> in der Liste, aber nicht extrahierbar.
    assert items["ohne-alles"]["pdf_available"] is False
    assert items["ohne-alles"]["abstract_available"] is False


def test_two_papers_sharing_one_pdf_both_keep_it(tmp_path: Path) -> None:
    """Regression: die pfad-basierte Dedup hat eines der beiden verschluckt.

    Es tauchte danach als "ohne PDF" wieder auf, obwohl die Datei existiert —
    genau die Diskrepanz zwischen der Vorschau-Zahl und dem Batch-Knopf.
    """
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    shared = pdf_dir / "geteiltes-paper.pdf"
    shared.write_bytes(b"%PDF-1.4 fixture")

    with MetadataDB(str(db_path)) as db:
        # pdf_url traegt nach einem Download den *lokalen* Pfad (siehe
        # _paper_local_pdf_path); beide Paper zeigen hier auf dieselbe Datei.
        for pid in ("preprint:1", "journal:1"):
            db.insert_paper({
                "id": pid, "source": "fixture", "source_id": pid,
                "title": pid, "year": 2024, "pdf_url": str(shared),
            })

    with TestClient(product_main.app) as client:
        items = {i["paper_id"]: i for i in _library(client, db_path, pdf_dir)["items"]}

    assert items["preprint:1"]["pdf_available"] is True
    assert items["journal:1"]["pdf_available"] is True
    assert items["preprint:1"]["pdf_path"] == items["journal:1"]["pdf_path"]
    # Und keine Karteileiche: jede paper_id genau einmal.
    assert len(items) == 2


def test_abstract_only_paper_completes_a_batch(tmp_path: Path, monkeypatch) -> None:
    """Der Weg, den der Nutzer ausdruecklich wollte: Paper ohne PDF extrahieren."""
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    with MetadataDB(str(db_path)) as db:
        db.insert_paper({
            "id": "nur-abstract", "source": "fixture", "source_id": "c",
            "title": "Aufmerksamkeit in neuronalen Netzen",
            "abstract": "Wir untersuchen Attention-Mechanismen und ihre Wirkung.",
            "year": 2024,
        })

    captured: dict[str, Any] = {}

    class _Pipeline:
        def process(self, paper_id: str, text: str, **_kwargs: Any) -> Any:
            captured["paper_id"] = paper_id
            captured["text"] = text
            from extraction.entity_extractor import ExtractionResult

            return ExtractionResult(
                paper_id=paper_id, paper_type="empirical",
                concepts=[{"label": "Attention", "confidence": 0.9}], methods=[],
                concept_candidates=[], method_candidates=[], relations=[], claims=[],
                cross_domain_hints=[], terminology_conflicts=[], temporal_coverage={},
                mathematical_content={}, raw_response={},
            )

    monkeypatch.setattr("extraction.batch_processor.ExtractionPipeline", lambda *a, **k: _Pipeline())

    with TestClient(product_main.app) as client:
        response = client.post("/extraction/batch", json={
            "items": [{"paper_id": "nur-abstract"}],
            "job_id": "job-abstract",
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
        })
    assert response.status_code == 200, response.text
    job = response.json()["job"]
    assert job["papers_processed"] == 1
    assert job["papers_failed"] == 0

    # Der Extraktionstext ist Titel + Abstract, nicht leer.
    assert "Abstract:" in captured["text"]
    assert "Attention-Mechanismen" in captured["text"]

    with MetadataDB(str(db_path)) as db:
        items = db.get_batch_job_items("job-abstract")
    assert [i["status"] for i in items] == ["completed"]
