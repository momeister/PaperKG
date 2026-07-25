"""Projekt-Bundles: Round-Trip, Merge-Semantik und Abwehr praeparierter Archive."""
from __future__ import annotations

import json
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from api import product_main
from graph_bundle import BundleError, export_project, import_bundle, preview_bundle
from graph_bundle.schema import MANIFEST_NAME, PROJECT_NAME, TABLE_FILES
from storage.atomic_json import read_json_dict, write_json_atomic
from storage.metadata_db import MetadataDB


@pytest.fixture
def project_fixture(tmp_path: Path):
    """Ein kleines Projekt mit Papern, Extraktion, Grauquelle und Embedding."""
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    with MetadataDB(str(db_path)) as db:
        db.insert_paper({
            "id": "arxiv:2401.00001", "source": "arxiv", "source_id": "2401.00001",
            "title": "Attention und Gedaechtnis", "abstract": "Ein Abstract.",
            "authors": ["A. Autorin", "B. Autor"], "year": 2024, "doi": "10.1000/x",
            "references": ["arxiv:2301.00002"],
        })
        db.insert_paper({
            "id": "arxiv:2301.00002", "source": "arxiv", "source_id": "2301.00002",
            "title": "Vorlaeuferarbeit", "abstract": "Noch ein Abstract.", "year": 2023,
        })
        db.insert_paper({
            "id": "arxiv:9999.99999", "source": "arxiv", "source_id": "9999.99999",
            "title": "Fremdes Paper (nicht im Projekt)", "year": 2020,
        })
        db.save_extraction_result(
            paper_id="arxiv:2401.00001", llm_provider="fake", llm_model="fake-1",
            paper_type="empirical",
            concepts=[{"label": "Attention", "confidence": 0.9}],
            methods=[{"label": "Transformer", "confidence": 0.8}],
        )
        db.add_grey_source("Testprojekt", {
            "id": "g1", "url": "https://example.org/a", "title": "Webquelle",
            "summary": "Zusammenfassung", "full_text": "Volltext der Quelle",
        })
        db.upsert_entity_embedding(
            label="Attention", vector=[0.1, 0.2, 0.3], model="test", backend="hash", dimension=3
        )
    write_json_atomic(projects_path, {"Testprojekt": ["arxiv:2401.00001", "arxiv:2301.00002"]})
    return db_path, projects_path


def _export(tmp_path: Path, db_path: Path, projects_path: Path, **kwargs) -> Path:
    projects = read_json_dict(projects_path)
    return export_project(
        "Testprojekt",
        paper_ids=projects["Testprojekt"],
        metadata_db_path=str(db_path),
        output_dir=tmp_path / "exports",
        **kwargs,
    )


# --------------------------------------------------------------------------- #
# Export                                                                       #
# --------------------------------------------------------------------------- #


def test_export_contains_only_the_projects_papers(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    bundle = _export(tmp_path, db_path, projects_path)

    with zipfile.ZipFile(bundle) as archive:
        names = set(archive.namelist())
        assert MANIFEST_NAME in names and PROJECT_NAME in names
        manifest = json.loads(archive.read(MANIFEST_NAME))
        papers = [json.loads(line) for line in archive.read(TABLE_FILES["papers"]).decode().splitlines() if line]
        greys = [json.loads(line) for line in archive.read(TABLE_FILES["grey_sources"]).decode().splitlines() if line]
        embeds = [json.loads(line) for line in archive.read(TABLE_FILES["entity_embeddings"]).decode().splitlines() if line]

    assert {p["id"] for p in papers} == {"arxiv:2401.00001", "arxiv:2301.00002"}
    assert manifest["counts"]["papers"] == 2
    assert manifest["counts"]["extraction_results"] == 1
    assert [g["id"] for g in greys] == ["g1"]
    # Nur Embeddings zu Labels aus diesem Projekt — sonst waere jedes Bundle so
    # gross wie die gesamte Bibliothek.
    assert [e["label"] for e in embeds] == ["Attention"]
    # Jede Datei ist mit ihrem sha256 im Manifest verzeichnet.
    assert set(manifest["files"]) >= set(TABLE_FILES.values())


def test_export_of_an_empty_project_is_refused(tmp_path: Path, project_fixture) -> None:
    db_path, _ = project_fixture
    with pytest.raises(BundleError, match="keine Paper"):
        export_project("Leer", paper_ids=[], metadata_db_path=str(db_path), output_dir=tmp_path / "e")


def test_export_can_include_pdfs(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf = pdf_dir / "arxiv_2401.00001.pdf"
    pdf.write_bytes(b"%PDF-1.4 inhalt")
    with MetadataDB(str(db_path)) as db:
        db.insert_paper({
            "id": "arxiv:2401.00001", "source": "arxiv", "source_id": "2401.00001",
            "title": "Attention und Gedaechtnis", "year": 2024, "pdf_url": str(pdf),
        })

    bundle = _export(tmp_path, db_path, projects_path, include_pdfs=True, pdf_base_dir=str(pdf_dir))
    with zipfile.ZipFile(bundle) as archive:
        pdfs = [n for n in archive.namelist() if n.startswith("pdfs/")]
    assert len(pdfs) == 1
    assert pdfs[0].endswith(".pdf")


# --------------------------------------------------------------------------- #
# Round-Trip                                                                   #
# --------------------------------------------------------------------------- #


def test_roundtrip_into_a_fresh_database(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    bundle = _export(tmp_path, db_path, projects_path)

    fresh_db = tmp_path / "fresh.duckdb"
    with MetadataDB(str(fresh_db)):
        pass

    report, projects, sidecars = import_bundle(
        bundle, metadata_db_path=str(fresh_db), existing_projects={}, mode="merge"
    )

    assert report.papers_imported == 2
    assert report.extractions_imported == 1
    assert report.grey_sources_imported == 1
    assert report.embeddings_imported == 1
    assert projects["Testprojekt"] == ["arxiv:2401.00001", "arxiv:2301.00002"]
    assert sidecars["pinned"] is False

    with MetadataDB(str(fresh_db)) as db:
        assert {p["id"] for p in db.list_papers(limit=100)} == {"arxiv:2401.00001", "arxiv:2301.00002"}
        paper = db.get_paper("arxiv:2401.00001")
        assert paper["title"] == "Attention und Gedaechtnis"
        assert paper["authors"] == ["A. Autorin", "B. Autor"]
        assert paper["references"] == ["arxiv:2301.00002"]
        extractions = db.list_extraction_results(limit=10)
        assert [c["label"] for c in extractions[0]["concepts"]] == ["Attention"]
        assert [g["url"] for g in db.list_grey_sources("Testprojekt")] == ["https://example.org/a"]


def test_importing_twice_creates_no_duplicates(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    bundle = _export(tmp_path, db_path, projects_path)
    fresh_db = tmp_path / "fresh.duckdb"
    with MetadataDB(str(fresh_db)):
        pass

    _report, projects, _ = import_bundle(bundle, metadata_db_path=str(fresh_db), existing_projects={})
    second, projects, _ = import_bundle(bundle, metadata_db_path=str(fresh_db), existing_projects=projects)

    # Beim zweiten Mal wird nichts mehr eingefuegt, nur uebersprungen.
    assert second.papers_imported == 0
    assert second.papers_skipped == 2
    assert second.extractions_imported == 0
    assert projects["Testprojekt"] == ["arxiv:2401.00001", "arxiv:2301.00002"]
    with MetadataDB(str(fresh_db)) as db:
        assert len(db.list_papers(limit=100)) == 2
        assert len(db.list_extraction_results(limit=100)) == 1
        assert len(db.list_grey_sources("Testprojekt")) == 1


def test_merge_keeps_local_papers_and_adds_the_new_ones(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    bundle = _export(tmp_path, db_path, projects_path)

    target_db = tmp_path / "ziel.duckdb"
    with MetadataDB(str(target_db)) as db:
        # Lokal reparierter Titel darf beim Merge nicht ueberschrieben werden.
        db.insert_paper({
            "id": "arxiv:2401.00001", "source": "arxiv", "source_id": "2401.00001",
            "title": "Lokal korrigierter Titel", "year": 2024,
        })

    report, projects, _ = import_bundle(
        bundle,
        metadata_db_path=str(target_db),
        existing_projects={"Testprojekt": ["lokal:1"]},
        mode="merge",
    )
    assert report.papers_imported == 1
    assert report.papers_skipped == 1
    assert projects["Testprojekt"] == ["lokal:1", "arxiv:2401.00001", "arxiv:2301.00002"]
    with MetadataDB(str(target_db)) as db:
        assert db.get_paper("arxiv:2401.00001")["title"] == "Lokal korrigierter Titel"


def test_replace_resets_the_member_list(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    bundle = _export(tmp_path, db_path, projects_path)
    target_db = tmp_path / "ziel.duckdb"
    with MetadataDB(str(target_db)):
        pass

    _report, projects, _ = import_bundle(
        bundle,
        metadata_db_path=str(target_db),
        existing_projects={"Testprojekt": ["alt:1", "alt:2"]},
        mode="replace",
    )
    assert projects["Testprojekt"] == ["arxiv:2401.00001", "arxiv:2301.00002"]


def test_import_into_a_differently_named_project(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    bundle = _export(tmp_path, db_path, projects_path)
    target_db = tmp_path / "ziel.duckdb"
    with MetadataDB(str(target_db)):
        pass

    report, projects, _ = import_bundle(
        bundle, metadata_db_path=str(target_db), existing_projects={}, target_project="Kopie"
    )
    assert report.project == "Kopie"
    assert "Kopie" in projects and "Testprojekt" not in projects
    with MetadataDB(str(target_db)) as db:
        # Grauquellen haengen am Zielprojekt, nicht am Namen aus dem Bundle.
        assert len(db.list_grey_sources("Kopie")) == 1
        assert len(db.list_grey_sources("Testprojekt")) == 0


# --------------------------------------------------------------------------- #
# Vorschau                                                                     #
# --------------------------------------------------------------------------- #


def test_preview_reports_what_would_change(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    bundle = _export(tmp_path, db_path, projects_path)

    target_db = tmp_path / "ziel.duckdb"
    with MetadataDB(str(target_db)) as db:
        db.insert_paper({"id": "arxiv:2401.00001", "source": "arxiv", "source_id": "x", "title": "Da"})

    preview = preview_bundle(
        bundle, metadata_db_path=str(target_db), existing_projects={"Testprojekt": []}
    )
    assert preview.project == "Testprojekt"
    assert preview.papers_existing == 1
    assert preview.papers_new == 1
    assert preview.project_exists is True
    assert any("existiert bereits" in w for w in preview.warnings)


# --------------------------------------------------------------------------- #
# Abwehr praeparierter Archive                                                 #
# --------------------------------------------------------------------------- #


def _malicious_zip(path: Path, member: str) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(MANIFEST_NAME, json.dumps({"bundle_version": 1, "project": "X"}))
        archive.writestr(member, "geklaut")
    return path


@pytest.mark.parametrize("member", ["../../etc/passwd", "/etc/passwd", "pdfs/../../../boese.pdf"])
def test_path_traversal_is_rejected(tmp_path: Path, member: str) -> None:
    bundle = _malicious_zip(tmp_path / "boese.zip", member)
    with pytest.raises(BundleError):
        preview_bundle(bundle, metadata_db_path=str(tmp_path / "db.duckdb"), existing_projects={})


def test_a_non_zip_file_is_rejected(tmp_path: Path) -> None:
    fake = tmp_path / "kein.zip"
    fake.write_bytes(b"das ist kein zip")
    with pytest.raises(BundleError, match="kein gueltiges ZIP"):
        preview_bundle(fake, metadata_db_path=str(tmp_path / "db.duckdb"), existing_projects={})


def test_a_zip_without_manifest_is_rejected(tmp_path: Path) -> None:
    plain = tmp_path / "ohne.zip"
    with zipfile.ZipFile(plain, "w") as archive:
        archive.writestr("irgendwas.txt", "hallo")
    with pytest.raises(BundleError, match="Kein manifest.json"):
        preview_bundle(plain, metadata_db_path=str(tmp_path / "db.duckdb"), existing_projects={})


def test_a_newer_bundle_version_is_refused(tmp_path: Path) -> None:
    future = tmp_path / "zukunft.zip"
    with zipfile.ZipFile(future, "w") as archive:
        archive.writestr(MANIFEST_NAME, json.dumps({"bundle_version": 99, "project": "X"}))
    with pytest.raises(BundleError, match="neueren Version"):
        preview_bundle(future, metadata_db_path=str(tmp_path / "db.duckdb"), existing_projects={})


# --------------------------------------------------------------------------- #
# HTTP-Schicht                                                                 #
# --------------------------------------------------------------------------- #


def test_export_and_import_over_http(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    common = {"metadata_db_path": str(db_path), "projects_path": str(projects_path)}

    with TestClient(product_main.app) as client:
        response = client.get(
            "/projects/Testprojekt/export",
            params={**common, "export_dir": str(tmp_path / "exports")},
        )
        assert response.status_code == 200, response.text
        assert response.headers["content-type"] == "application/zip"
        assert "paperkg-export_Testprojekt" in response.headers["content-disposition"]
        payload = response.content

        # Vorschau gegen eine leere Zieldatenbank.
        target_db = tmp_path / "ziel.duckdb"
        target_projects = tmp_path / "ziel-projects.json"
        write_json_atomic(target_projects, {})
        target = {"metadata_db_path": str(target_db), "projects_path": str(target_projects)}

        preview = client.post("/bundles/preview", params=target, content=payload)
        assert preview.status_code == 200, preview.text
        assert preview.json()["preview"]["papers_new"] == 2

        imported = client.post("/bundles/import", params={**target, "mode": "merge"}, content=payload)
        assert imported.status_code == 200, imported.text
        report = imported.json()["report"]
        assert report["papers_imported"] == 2
        assert report["paper_count"] == 2

    assert read_json_dict(target_projects)["Testprojekt"] == ["arxiv:2401.00001", "arxiv:2301.00002"]


def test_export_of_an_unknown_project_is_404(tmp_path: Path, project_fixture) -> None:
    db_path, projects_path = project_fixture
    with TestClient(product_main.app) as client:
        response = client.get(
            "/projects/GibtEsNicht/export",
            params={"metadata_db_path": str(db_path), "projects_path": str(projects_path)},
        )
    assert response.status_code == 404


def test_import_rejects_a_broken_upload(tmp_path: Path) -> None:
    with TestClient(product_main.app) as client:
        response = client.post(
            "/bundles/preview",
            params={"metadata_db_path": str(tmp_path / "db.duckdb")},
            content=b"kein zip",
        )
    assert response.status_code == 400
    assert "ZIP" in response.json()["detail"]
