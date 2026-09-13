from unittest.mock import Mock

import pytest
from fastapi.testclient import TestClient

from api import product_main
from api.routers import glossary
from storage.metadata_db import MetadataDB
from storage.metadata_db.glossary import GlossaryDuplicateError
from storage.metadata_db.project_scope import PROJECT_SCOPED_TABLES


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(
        glossary, "DEFAULT_METADATA_DB_PATH", str(tmp_path / "glossary.duckdb")
    )
    router = Mock()
    router.chat.return_value = "Eine kurze Erklärung."
    monkeypatch.setattr(product_main, "llm_router", router)
    return TestClient(product_main.app), router


def test_crud_duplicates_and_persistence(client):
    api, llm = client
    assert api.get("/glossary").json() == {"items": []}
    entry = api.post(
        "/glossary",
        json={"term": "  Neural   Network ", "explanation": " Ein Modell. "},
    ).json()
    assert entry["term"] == "Neural Network"
    assert entry["explanation"] == "Ein Modell."
    assert entry["created_at"] == entry["updated_at"]
    duplicate = api.post(
        "/glossary", json={"term": "NEURAL network", "explanation": "Other"}
    )
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["entry"]["id"] == entry["id"]
    patch = api.patch(f'/glossary/{entry["id"]}', json={"explanation": "Bearbeitet."})
    assert patch.status_code == 200
    assert patch.json()["term"] == entry["term"]
    assert patch.json()["created_at"] == entry["created_at"]
    assert patch.json()["updated_at"] >= entry["updated_at"]
    with MetadataDB(glossary.DEFAULT_METADATA_DB_PATH) as db:
        assert db.list_glossary()[0]["explanation"] == "Bearbeitet."
        db.rename_project("A", "B")
        assert len(db.list_glossary()) == 1
        assert "glossary" not in PROJECT_SCOPED_TABLES
        db.save_glossary("Other", "Another entry")
    assert (
        api.patch(f'/glossary/{entry["id"]}', json={"term": "OTHER"}).status_code == 409
    )
    assert api.delete(f'/glossary/{entry["id"]}').status_code == 200
    assert api.delete(f'/glossary/{entry["id"]}').status_code == 404
    assert api.patch("/glossary/missing", json={"term": "x"}).status_code == 404
    llm.chat.assert_not_called()


@pytest.mark.parametrize("value", ["", "  ", None])
def test_reject_empty(client, value):
    api, _ = client
    assert (
        api.post(
            "/glossary", json={"term": value, "explanation": "Meaning"}
        ).status_code
        == 422
    )
    assert (
        api.post("/glossary", json={"term": "Term", "explanation": value}).status_code
        == 422
    )
    entry = api.post(
        "/glossary", json={"term": "Term", "explanation": "Meaning"}
    ).json()
    assert (
        api.patch(f'/glossary/{entry["id"]}', json={"explanation": value}).status_code
        == 422
    )
    assert (
        api.patch(f'/glossary/{entry["id"]}', json={"term": value}).status_code == 422
    )


def test_suggestion_is_explicit_and_never_saves(client):
    api, router = client
    result = api.post(
        "/glossary/suggest",
        json={
            "term": "Network",
            "selected_text": "Small selected passage",
            "provider": "test-provider",
            "model": "test-model",
        },
    )
    assert result.status_code == 200
    assert result.json() == {"suggestion": "Eine kurze Erklärung."}
    args, kwargs = router.chat.call_args
    assert kwargs["provider"] == "test-provider"
    assert kwargs["overrides"]["model"] == "test-model"
    assert len(args[0]) == 2
    assert '"selected_text": "Small selected passage"' in args[0][1]["content"]
    assert api.get("/glossary").json()["items"] == []
    router.chat.side_effect = RuntimeError("HTTP 429 rate limit")
    assert api.post("/glossary/suggest", json={"term": "Network"}).status_code == 502
    assert api.get("/glossary").json()["items"] == []


def test_unicode_normalization_and_storage_validation(tmp_path):
    with MetadataDB(str(tmp_path / "global.duckdb")) as db:
        db.save_glossary("Straße", "Meaning")
        with pytest.raises(GlossaryDuplicateError):
            db.save_glossary("STRASSE", "Other")
        with pytest.raises(ValueError):
            db.save_glossary(" ", "Meaning")
        with pytest.raises(KeyError):
            db.save_glossary("Missing", "Meaning", "missing")


def test_project_rename_delete_and_bundle_do_not_change_dictionary(
    client, tmp_path, monkeypatch
):
    import zipfile
    from api.routers import projects
    from graph_bundle import export_project
    from storage.atomic_json import write_json_atomic

    api, _ = client
    projects_path = tmp_path / "projects.json"
    write_json_atomic(projects_path, {"A": ["fixture:p1"]})
    monkeypatch.setattr(projects, "PROJECT_PRIMARY_PATH", tmp_path / "primary.json")
    monkeypatch.setattr(projects, "PROJECT_META_PATH", tmp_path / "meta.json")
    entry = api.post(
        "/glossary", json={"term": "AI", "explanation": "Global meaning"}
    ).json()
    with MetadataDB(glossary.DEFAULT_METADATA_DB_PATH) as db:
        db.insert_paper(
            {
                "id": "fixture:p1",
                "source": "fixture",
                "source_id": "p1",
                "title": "Test paper",
            }
        )
        note = db.create_note("A", "Test", "AI stays unchanged.")
    response = api.patch(
        "/projects/A",
        params={
            "projects_path": str(projects_path),
            "metadata_db_path": glossary.DEFAULT_METADATA_DB_PATH,
        },
        json={"name": "B"},
    )
    assert response.status_code == 200
    with MetadataDB(glossary.DEFAULT_METADATA_DB_PATH) as db:
        assert db.get_note(note["id"])["project_id"] == "B"
        assert db.get_note(note["id"])["markdown"] == "AI stays unchanged."
    bundle = export_project(
        "B",
        paper_ids=["fixture:p1"],
        metadata_db_path=glossary.DEFAULT_METADATA_DB_PATH,
        output_dir=tmp_path / "export",
    )
    with zipfile.ZipFile(bundle) as archive:
        assert not any("glossary" in name for name in archive.namelist())
        assert all(
            b"Global meaning" not in archive.read(name) for name in archive.namelist()
        )
    assert (
        api.delete(
            "/projects/B", params={"projects_path": str(projects_path)}
        ).status_code
        == 200
    )
    assert api.get("/glossary").json()["items"] == [entry]
