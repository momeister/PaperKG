from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from api import product_main
from storage.metadata_db import MetadataDB


def _fixture_db(path: Path) -> None:
    with MetadataDB(str(path)) as db:
        db.insert_paper(
            {
                "id": "p1",
                "source": "fixture",
                "source_id": "p1",
                "title": "Graph Transformer for Science",
                "abstract": "Graph transformer methods for paper linking.",
                "year": 2024,
                "has_full_text": True,
                "references": ["p2"],
            }
        )
        db.insert_paper(
            {
                "id": "p2",
                "source": "fixture",
                "source_id": "p2",
                "title": "Citation Networks",
                "abstract": "Network analysis for citations.",
                "year": 2023,
                "has_full_text": False,
                "references": ["p1"],
            }
        )
        db.save_extraction_result(
            paper_id="p1",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[
                {
                    "label": "Graph Transformer",
                    "confidence": 0.95,
                    "canonical_id": "concept:graph-transformer",
                    "review_status": "approved",
                },
                {
                    "label": "Pending Concept",
                    "confidence": 0.62,
                    "review_status": "pending",
                    "evidence": "Needs review.",
                },
            ],
            methods=[
                {
                    "label": "Attention",
                    "confidence": 0.9,
                    "canonical_id": "method:attention",
                    "review_status": "approved",
                }
            ],
        )
        db.upsert_batch_job(
            "job-1",
            status="completed",
            papers_total=1,
            papers_processed=1,
        )


def test_product_projects_papers_dashboard_review_and_graph(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    pdf_dir = tmp_path / "pdfs"
    graph_dir = tmp_path / "graph"
    pdf_dir.mkdir()
    graph_dir.mkdir()
    _fixture_db(db_path)

    client = TestClient(product_main.app)
    common = {
        "metadata_db_path": str(db_path),
        "projects_path": str(projects_path),
    }

    created = client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "demo", "paper_ids": ["p1"]})
    assert created.status_code == 200
    assert created.json()["project"]["paper_count"] == 1
    reserved = client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "Alle Papers"})
    assert reserved.status_code == 400

    projects = client.get("/projects", params=common)
    assert projects.status_code == 200
    assert projects.json()["projects"][0]["id"] == "demo"

    dashboard = client.get(
        "/projects/demo/dashboard",
        params={
            **common,
            "graph_db_path": str(graph_dir),
            "pdf_base_dir": str(pdf_dir),
        },
    )
    assert dashboard.status_code == 200
    assert dashboard.json()["metrics"]["papers"] == 1
    assert dashboard.json()["metrics"]["extraction_coverage"] == 1.0

    papers = client.get("/papers", params={**common, "query": "transformer"})
    assert papers.status_code == 200
    assert papers.json()["total"] == 1
    assert papers.json()["items"][0]["latest_extraction_status"] == "success"

    review = client.get("/review/entities", params={"metadata_db_path": str(db_path)})
    assert review.status_code == 200
    item_id = review.json()["items"][0]["id"]
    action = client.post(
        "/review/entities/actions",
        params={"metadata_db_path": str(db_path)},
        json={"ids": [item_id], "action": "approve"},
    )
    assert action.status_code == 200
    assert action.json()["status"] == "approved"

    graph = client.get("/graph/explorer", params={**common, "project_id": "demo"})
    assert graph.status_code == 200
    node_types = {node["type"] for node in graph.json()["nodes"]}
    assert {"paper", "concept", "method"} <= node_types

    delete_reserved = client.delete("/projects/__all_papers__", params={"projects_path": str(projects_path)})
    assert delete_reserved.status_code == 400
    deleted = client.delete("/projects/demo", params={"projects_path": str(projects_path)})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True
    projects_after_delete = client.get("/projects", params=common)
    assert projects_after_delete.json()["projects"] == []


def test_product_upload_models_jobs_and_harvest(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    pdf_dir = tmp_path / "pdfs"
    _fixture_db(db_path)

    async def fake_harvest_search(query: str, sources: list[str], max_results: int):
        return (
            [
                {
                    "source": "arxiv",
                    "source_id": "1234.56789",
                    "title": f"{query} Paper",
                    "year": 2026,
                    "pdf_url": None,
                }
            ],
            [],
        )

    monkeypatch.setattr(product_main, "_run_harvest_search", fake_harvest_search)

    client = TestClient(product_main.app)
    upload = client.post(
        "/papers/upload",
        params={
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
            "paper_id": "uploaded",
            "title": "Uploaded PDF",
        },
        headers={"x-filename": "uploaded.pdf", "content-type": "application/pdf"},
        content=b"%PDF-1.4\n",
    )
    assert upload.status_code == 200
    assert Path(upload.json()["pdf_path"]).exists()

    providers = client.get("/models/providers")
    assert providers.status_code == 200
    assert providers.json()["providers"]

    jobs = client.get("/jobs", params={"metadata_db_path": str(db_path)})
    assert jobs.status_code == 200
    assert jobs.json()["jobs"][0]["job_id"] == "job-1"

    harvest = client.post("/harvest/search", json={"query": "graph", "sources": ["arxiv"], "max_results": 1})
    assert harvest.status_code == 200
    assert harvest.json()["results"][0]["source"] == "arxiv"

    # Keeps the legacy project file shape used by the Streamlit project workbench.
    client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "compat", "paper_ids": ["p1"]})
    assert json.loads(projects_path.read_text(encoding="utf-8")) == {"compat": ["p1"]}


def test_product_health_repair_and_rewrite(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    graph_dir = tmp_path / "global_kg"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    _fixture_db(db_path)

    class FakeRouter:
        def chat(self, messages, provider=None, overrides=None):
            return "Klarer Text [p1]"

        def provider_default_model(self, provider=None):
            return "fake-model"

    monkeypatch.setattr(product_main, "llm_router", FakeRouter())
    client = TestClient(product_main.app)

    repair = client.post(
        "/jobs/health-repair",
        json={
            "metadata_db_path": str(db_path),
            "graph_db_path": str(graph_dir),
            "pdf_base_dir": str(pdf_dir),
        },
    )
    assert repair.status_code == 200
    assert graph_dir.exists()
    assert repair.json()["after"]["embeddings"]["total"] > 0

    rewrite = client.post(
        "/tools/rewrite",
        json={"text": "Rohtext [p1]", "instruction": "klarer"},
    )
    assert rewrite.status_code == 200
    assert rewrite.json()["text"] == "Klarer Text [p1]"


def test_product_notes_crud_append_assets_ai_and_restore(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    asset_dir = tmp_path / "note_assets"
    _fixture_db(db_path)
    client = TestClient(product_main.app)
    client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "demo", "paper_ids": ["p1"]})

    created = client.post(
        "/projects/demo/notes",
        params={"metadata_db_path": str(db_path)},
        json={"title": "Vorbereitung", "markdown": "# Start"},
    )
    assert created.status_code == 200
    note_id = created.json()["note"]["id"]

    citation_id = "cite_test"
    appended = client.post(
        f"/notes/{note_id}/append",
        params={"metadata_db_path": str(db_path)},
        json={
            "markdown": f"> Beleg\n\nQuelle: [Z1](sciencekg://citation/{citation_id})",
            "citations": [
                {
                    "id": citation_id,
                    "paper_id": "p1",
                    "title": "Graph Transformer for Science",
                    "kind": "concept",
                    "reference_text": "Graph Transformer evidence",
                    "pdf_excerpt": "Graph Transformer evidence in the parsed PDF text.",
                    "evidence_index": 0,
                }
            ],
        },
    )
    assert appended.status_code == 200
    assert appended.json()["note"]["citation_count"] == 1

    repeated_citation = {
        "paper_id": "p1",
        "title": "Graph Transformer for Science",
        "kind": "concept",
        "reference_text": "Repeated Graph Transformer evidence",
        "pdf_excerpt": "Repeated Graph Transformer evidence in the parsed PDF text.",
        "evidence_index": 0,
    }
    first_repeat = client.post(
        f"/notes/{note_id}/append",
        params={"metadata_db_path": str(db_path)},
        json={"markdown": "Noch ein Beleg", "citations": [repeated_citation]},
    )
    second_repeat = client.post(
        f"/notes/{note_id}/append",
        params={"metadata_db_path": str(db_path)},
        json={"markdown": "Derselbe Beleg", "citations": [repeated_citation]},
    )
    assert first_repeat.status_code == 200
    assert second_repeat.status_code == 200
    assert second_repeat.json()["note"]["citation_count"] == 2

    listed = client.get("/projects/demo/notes", params={"metadata_db_path": str(db_path)})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["citation_count"] == 2

    patched = client.patch(
        f"/notes/{note_id}",
        params={"metadata_db_path": str(db_path)},
        json={"markdown": "# Geaendert"},
    )
    assert patched.status_code == 200
    restored = client.post(f"/notes/{note_id}/versions/restore-latest", params={"metadata_db_path": str(db_path)})
    assert restored.status_code == 200
    assert "Beleg" in restored.json()["note"]["markdown"]

    asset = client.post(
        f"/notes/{note_id}/assets",
        params={"metadata_db_path": str(db_path), "note_asset_dir": str(asset_dir)},
        headers={"content-type": "image/png", "x-filename": "plot.png"},
        content=b"png-bytes",
    )
    assert asset.status_code == 200
    asset_id = asset.json()["asset"]["id"]
    loaded_asset = client.get(
        f"/notes/assets/{asset_id}",
        params={"metadata_db_path": str(db_path), "note_asset_dir": str(asset_dir)},
    )
    assert loaded_asset.status_code == 200
    assert loaded_asset.content == b"png-bytes"

    class FakeRouter:
        default_provider = "fake"

        def chat(self, messages, provider=None, overrides=None):
            assert "Markierter Text" in messages[-1]["content"]
            return "Verbesserter Abschnitt [p1]"

        def provider_default_model(self, provider=None):
            return "fake-model"

    monkeypatch.setattr(product_main, "llm_router", FakeRouter())
    ai = client.post(
        f"/notes/{note_id}/ai-edit",
        json={
            "selected_text": "Graph Transformer",
            "instruction": "Formuliere besser",
            "metadata_db_path": str(db_path),
        },
    )
    assert ai.status_code == 200
    assert ai.json()["replacement_text"] == "Verbesserter Abschnitt [p1]"

    thread = client.post(
        f"/notes/{note_id}/ai-threads",
        json={
            "selected_text": "Graph Transformer",
            "instruction": "Erklaere kurz",
            "metadata_db_path": str(db_path),
            "anchor_start": 3,
            "anchor_end": 20,
        },
    )
    assert thread.status_code == 200
    thread_id = thread.json()["thread"]["id"]
    assert thread.json()["thread"]["messages"][0]["role"] == "user"
    followup = client.post(
        f"/notes/{note_id}/ai-threads/{thread_id}/messages",
        json={
            "message": "Noch genauer",
            "metadata_db_path": str(db_path),
        },
    )
    assert followup.status_code == 200
    assert followup.json()["assistant_message"]["content"] == "Verbesserter Abschnitt [p1]"

    threads = client.get(f"/notes/{note_id}/ai-threads", params={"metadata_db_path": str(db_path)})
    assert threads.status_code == 200
    assert threads.json()["total"] == 2
