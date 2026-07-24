from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from fastapi.testclient import TestClient

from api import product_main
from api.routers import projects as projects_router
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


def test_project_rename_migrates_project_scoped_data(tmp_path, monkeypatch) -> None:
    """Die Projekt-ID *ist* der Name — Umbenennen muss alles Projektgebundene mitziehen."""
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    _fixture_db(db_path)
    monkeypatch.setattr(projects_router, "PROJECT_PRIMARY_PATH", tmp_path / "project_primary.json")
    monkeypatch.setattr(projects_router, "PROJECT_META_PATH", tmp_path / "project_meta.json")

    client = TestClient(product_main.app)
    common = {"metadata_db_path": str(db_path), "projects_path": str(projects_path)}
    client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "alt", "paper_ids": ["p1"]})
    client.put("/projects/alt/primary-paper", json={"paper_id": "p1"})

    note = client.post(
        "/projects/alt/notes",
        params={"metadata_db_path": str(db_path)},
        json={"title": "Notiz", "markdown": "# Inhalt"},
    )
    assert note.status_code == 200
    note_id = note.json()["note"]["id"]
    with MetadataDB(str(db_path)) as db:
        grey = db.add_grey_source("alt", {"url": "https://example.org", "title": "Web", "summary": "s"})
    grey_id = grey["id"]

    renamed = client.patch("/projects/alt", params=common, json={"name": "neu"})
    assert renamed.status_code == 200
    assert renamed.json()["project"]["id"] == "neu"

    saved = json.loads(projects_path.read_text(encoding="utf-8"))
    assert saved == {"neu": ["p1"]}

    # Notiz, Web-Quelle und Hauptquelle haengen jetzt am neuen Projekt …
    moved_notes = client.get("/projects/neu/notes", params={"metadata_db_path": str(db_path)})
    assert [item["id"] for item in moved_notes.json()["items"]] == [note_id]
    with MetadataDB(str(db_path)) as db:
        assert [record["id"] for record in db.list_grey_sources("neu")] == [grey_id]
        assert db.list_grey_sources("alt") == []
    assert renamed.json()["project"]["primary_paper_id"] == "p1"

    # … und das alte Projekt existiert nicht mehr.
    missing = client.patch("/projects/alt", params=common, json={"name": "wieder"})
    assert missing.status_code == 404


def test_project_rename_rejects_reserved_and_existing_names(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    _fixture_db(db_path)
    monkeypatch.setattr(projects_router, "PROJECT_META_PATH", tmp_path / "project_meta.json")

    client = TestClient(product_main.app)
    common = {"metadata_db_path": str(db_path), "projects_path": str(projects_path)}
    client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "a"})
    client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "b"})

    assert client.patch("/projects/a", params=common, json={"name": "Alle Papers"}).status_code == 400
    assert client.patch("/projects/a", params=common, json={"name": "b"}).status_code == 409
    # Der globale Modus steht nicht in projects.json — er ist damit gar nicht erst umbenennbar.
    assert client.patch("/projects/__all_papers__", params=common, json={"name": "x"}).status_code == 404


def test_pinned_projects_sort_first_and_survive_reload(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    _fixture_db(db_path)
    monkeypatch.setattr(projects_router, "PROJECT_META_PATH", tmp_path / "project_meta.json")

    client = TestClient(product_main.app)
    common = {"metadata_db_path": str(db_path), "projects_path": str(projects_path)}
    for name in ["alpha", "beta", "gamma"]:
        client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": name})

    assert [p["id"] for p in client.get("/projects", params=common).json()["projects"]] == ["alpha", "beta", "gamma"]

    pinned = client.patch("/projects/gamma", params=common, json={"pinned": True})
    assert pinned.json()["project"]["pinned"] is True
    listed = client.get("/projects", params=common).json()["projects"]
    assert [p["id"] for p in listed] == ["gamma", "alpha", "beta"]

    client.patch("/projects/gamma", params=common, json={"pinned": False})
    assert [p["id"] for p in client.get("/projects", params=common).json()["projects"]] == ["alpha", "beta", "gamma"]


def test_pin_survives_rename_and_is_dropped_on_delete(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    meta_path = tmp_path / "project_meta.json"
    _fixture_db(db_path)
    monkeypatch.setattr(projects_router, "PROJECT_META_PATH", meta_path)

    client = TestClient(product_main.app)
    common = {"metadata_db_path": str(db_path), "projects_path": str(projects_path)}
    client.post("/projects", params={"projects_path": str(projects_path)}, json={"name": "alt"})
    client.patch("/projects/alt", params=common, json={"pinned": True})

    renamed = client.patch("/projects/alt", params=common, json={"name": "neu"})
    assert renamed.json()["project"]["pinned"] is True

    client.delete("/projects/neu", params={"projects_path": str(projects_path)})
    assert json.loads(meta_path.read_text(encoding="utf-8")) == {}


def test_graph_explorer_never_truncates_extracted_papers(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    with MetadataDB(str(db_path)) as db:
        db.insert_paper(
            {
                "id": "extracted",
                "source": "fixture",
                "source_id": "extracted",
                "title": "Extracted Paper",
                "abstract": "Has a successful extraction.",
                "year": 2024,
                "has_full_text": True,
            }
        )
        db.save_extraction_result(
            paper_id="extracted",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[{"label": "Concept", "confidence": 0.9, "canonical_id": "concept:c"}],
        )
        for index in range(10):
            pid = f"recent{index}"
            db.insert_paper(
                {
                    "id": pid,
                    "source": "fixture",
                    "source_id": pid,
                    "title": f"Unextracted Paper {pid}",
                    "abstract": "No extraction yet.",
                    "year": 2025,
                    "has_full_text": True,
                }
            )

    client = TestClient(product_main.app)
    graph = client.get(
        "/graph/explorer",
        params={"metadata_db_path": str(db_path), "limit": 5},
    )
    assert graph.status_code == 200
    body = graph.json()
    assert body["stats"]["extracted_paper_count"] == 1
    assert body["stats"]["total_paper_count"] == 11
    assert body["stats"]["truncated"] is True
    paper_nodes = [node for node in body["nodes"] if node["type"] == "paper"]
    assert len(paper_nodes) == 5
    assert "extracted" in {node["id"] for node in paper_nodes}


def test_harvest_download_attaches_papers_to_project(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    projects_path.write_text(json.dumps({"demo": []}), encoding="utf-8")

    client = TestClient(product_main.app)
    response = client.post(
        "/harvest/download",
        json={
            "papers": [
                {"id": "arxiv:1234.5678", "source": "arxiv", "source_id": "1234.5678", "title": "A"},
                {"id": "arxiv:2222.0001", "source": "arxiv", "source_id": "2222.0001", "title": "B"},
            ],
            "download_pdfs": False,
            "project_id": "demo",
            "projects_path": str(projects_path),
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["inserted"] == 2
    assert payload["attached"] is True
    assert len(payload["results"]) == 2
    saved = json.loads(projects_path.read_text(encoding="utf-8"))
    assert set(saved["demo"]) == {"arxiv:1234.5678", "arxiv:2222.0001"}


def test_harvest_download_to_all_papers_does_not_attach(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    projects_path.write_text(json.dumps({"demo": []}), encoding="utf-8")

    client = TestClient(product_main.app)
    response = client.post(
        "/harvest/download",
        json={
            "papers": [{"id": "arxiv:9999.0001", "source": "arxiv", "source_id": "9999.0001", "title": "X"}],
            "download_pdfs": False,
            "project_id": "__all_papers__",
            "projects_path": str(projects_path),
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(tmp_path / "pdfs"),
        },
    )

    assert response.status_code == 200
    assert response.json()["attached"] is False
    saved = json.loads(projects_path.read_text(encoding="utf-8"))
    assert saved["demo"] == []


def test_extraction_library_filters_by_project(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    projects_path = tmp_path / "projects.json"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    member_pdf = pdf_dir / "member.pdf"
    member_pdf.write_bytes(b"%PDF-1.4\n")
    other_pdf = pdf_dir / "other.pdf"
    other_pdf.write_bytes(b"%PDF-1.4\n")

    with MetadataDB(str(db_path)) as db:
        db.insert_paper({"id": "member", "source": "f", "source_id": "member", "title": "Member", "pdf_url": str(member_pdf)})
        db.insert_paper({"id": "other", "source": "f", "source_id": "other", "title": "Other", "pdf_url": str(other_pdf)})
    projects_path.write_text(json.dumps({"demo": ["member"]}), encoding="utf-8")

    client = TestClient(product_main.app)
    scoped = client.get(
        "/extraction/library",
        params={
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
            "project_id": "demo",
            "projects_path": str(projects_path),
        },
    )
    assert scoped.status_code == 200
    ids = {row["paper_id"] for row in scoped.json()["items"]}
    assert ids == {"member"}


def test_benchmark_job_persists_and_lists_runs(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    with MetadataDB(str(db_path)):
        pass  # initialize schema

    client = TestClient(product_main.app)
    run = client.post(
        "/jobs/benchmark",
        json={"metadata_db_path": str(db_path), "allow_embedded_predictions": True},
    )
    assert run.status_code == 200
    body = run.json()
    assert body["status"] == "completed"
    run_id = body["run"]["id"]
    assert body["run"]["kind"] == "extraction"

    listed = client.get("/benchmark/runs", params={"metadata_db_path": str(db_path)})
    assert listed.status_code == 200
    ids = {item["id"] for item in listed.json()["items"]}
    assert run_id in ids

    deleted = client.delete(f"/benchmark/runs/{run_id}", params={"metadata_db_path": str(db_path)})
    assert deleted.status_code == 200
    assert deleted.json()["deleted"] is True

    after = client.get("/benchmark/runs", params={"metadata_db_path": str(db_path)})
    assert run_id not in {item["id"] for item in after.json()["items"]}


def test_product_papers_include_pdf_display_fallbacks(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "Sparse_Science_Paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    with MetadataDB(str(db_path)) as db:
        db.insert_paper(
            {
                "id": "sparse-paper",
                "source": "fixture",
                "source_id": "sparse-paper",
                "title": "   ",
                "year": 2026,
                "pdf_url": str(pdf_path),
                "has_full_text": True,
            }
        )

    client = TestClient(product_main.app)
    response = client.get(
        "/papers",
        params={
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
            "has_full_text": True,
        },
    )

    assert response.status_code == 200
    paper = response.json()["items"][0]
    assert paper["display_title"] == "Sparse Science Paper"
    assert paper["pdf_filename"] == "Sparse_Science_Paper.pdf"
    assert paper["pdf_path"] == str(pdf_path)


def test_grey_source_from_url_fetches_sanitizes_and_infers_title(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"

    class FakeResponse:
        def __init__(self, text: str) -> None:
            self.text = text

        def raise_for_status(self) -> None:
            return None

    class FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args) -> bool:
            return False

        async def get(self, url):
            assert url == "https://example.com/article"
            return FakeResponse(
                "<html><head><title>Widgets Outperform Gadgets</title></head>"
                "<body><script>ignored()</script><p>Widgets reduced latency by 20%.</p></body></html>"
            )

    monkeypatch.setattr(product_main.httpx, "AsyncClient", FakeAsyncClient)
    client = TestClient(product_main.app)

    response = client.post(
        "/projects/demo/grey-sources/from-url",
        json={"url": "https://example.com/article", "metadata_db_path": str(db_path)},
    )

    assert response.status_code == 200
    saved = response.json()["saved"]
    assert saved["title"] == "Widgets Outperform Gadgets"
    assert "Widgets reduced latency by 20%" in saved["full_text"]
    assert "ignored()" not in saved["full_text"]

    with MetadataDB(str(db_path)) as db:
        stored = db.list_grey_sources("demo")
    assert stored[0]["url"] == "https://example.com/article"


def test_grey_source_from_url_rejects_non_http_scheme(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    client = TestClient(product_main.app)

    response = client.post(
        "/projects/demo/grey-sources/from-url",
        json={"url": "javascript:alert(1)", "metadata_db_path": str(db_path)},
    )

    assert response.status_code == 400


def test_note_as_source_publishes_citable_snapshot(tmp_path) -> None:
    # Eine Notiz wird als grey_source (source_kind=note) abgelegt und ist danach wie jede
    # andere Quelle ueber [grey::<id>] zitierbar — inklusive der Paper ihrer Zitate.
    db_path = tmp_path / "metadata.duckdb"
    with MetadataDB(str(db_path)) as db:
        note = db.create_note(project_id="demo", title="Meine Notiz", markdown="# Befund\n\nWichtiger Text.")
        db.add_note_citation(str(note["id"]), {"paper_id": "arxiv:1234.5678", "title": "Paper"})
    client = TestClient(product_main.app)

    response = client.post(
        f"/notes/{note['id']}/as-source",
        json={"metadata_db_path": str(db_path)},
    )

    assert response.status_code == 200
    saved = response.json()["saved"]
    assert saved["id"] == f"grey_note_{note['id']}"
    assert saved["source_kind"] == "note"
    assert saved["source_paper_ids"] == ["arxiv:1234.5678"]
    assert "Wichtiger Text" in saved["full_text"]

    # Erneutes Speichern aktualisiert den Snapshot, statt eine zweite Quelle anzulegen.
    with MetadataDB(str(db_path)) as db:
        db.update_note(str(note["id"]), markdown="# Befund\n\nAktualisierter Text.")
    again = client.post(f"/notes/{note['id']}/as-source", json={"metadata_db_path": str(db_path)})
    assert again.status_code == 200
    with MetadataDB(str(db_path)) as db:
        stored = db.list_grey_sources("demo", kind="note")
    assert len(stored) == 1
    assert "Aktualisierter Text" in stored[0]["full_text"]


def test_research_tree_as_source_keeps_used_papers(tmp_path) -> None:
    db_path = tmp_path / "metadata.duckdb"
    client = TestClient(product_main.app)

    response = client.post(
        "/research/tree/as-source",
        json={
            "project_id": "demo",
            "root_question": "Wie lernt das Gehirn?",
            "document": "## Kapitel\n\nBefund [arxiv:1].",
            "nodes": [
                {"id": "r", "parent_id": None, "depth": 0, "question": "Wie lernt das Gehirn?",
                 "answer": {"sources": [{"paper_id": "arxiv:1", "title": "A"}]}},
                {"id": "c", "parent_id": "r", "depth": 1, "question": "Kapitel",
                 "answer": {"sources": [{"paper_id": "arxiv:2", "title": "B"}]}},
            ],
            "sources": [{"paper_id": "arxiv:1", "title": "A"}],
            "session_id": "sess-1",
            "metadata_db_path": str(db_path),
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["paper_count"] == 2
    saved = payload["saved"]
    assert saved["source_kind"] == "analysis"
    assert sorted(saved["source_paper_ids"]) == ["arxiv:1", "arxiv:2"]
    assert saved["title"].startswith("Tiefenanalyse:")
    with MetadataDB(str(db_path)) as db:
        assert len(db.list_grey_sources("demo", kind="analysis")) == 1


def test_product_extraction_library_parse_and_extract(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    with MetadataDB(str(db_path)) as db:
        db.ensure_paper_record("paper-1", title="Phase Three Paper", pdf_path=str(pdf_path))

    class FakeParser:
        def parse(self, file_path, paper_id, force_parser=None):
            assert Path(file_path) == pdf_path
            return SimpleNamespace(
                paper_id=paper_id,
                parser="marker",
                text="Phase Three Paper\n\nGraph Transformer methods produce claims.",
                page_count=2,
                meta={"extraction_method": "fake"},
            )

    class FakePipeline:
        def process(self, paper_id, text, provider=None, overrides=None, link_concepts=True):
            assert paper_id == "paper-1"
            assert "Graph Transformer" in text
            assert overrides["model"] == "fake-model"
            return SimpleNamespace(
                paper_id=paper_id,
                paper_type="research",
                concepts=[{"label": "Graph Transformer", "confidence": 0.95, "review_status": "approved"}],
                methods=[{"label": "Attention", "confidence": 0.9, "review_status": "approved"}],
                concept_candidates=[],
                method_candidates=[],
                relations=[],
                claims=[{"statement": "Graph Transformer methods produce claims."}],
                cross_domain_hints=[],
                terminology_conflicts=[],
                temporal_coverage={"paper_year": 2026},
                mathematical_content={"has_formulas": False},
                language_detected="en",
                quality_warnings=[],
                metadata_status="valid",
                blocking_errors=[],
                candidate_count=0,
                extraction_diagnostics={"mode": "fake"},
                raw_response="{}",
            )

    monkeypatch.setattr(product_main, "parser_router", FakeParser())
    monkeypatch.setattr(product_main, "extraction_pipeline", FakePipeline())
    client = TestClient(product_main.app)

    library = client.get("/extraction/library", params={"metadata_db_path": str(db_path), "pdf_base_dir": str(pdf_dir)})
    assert library.status_code == 200
    assert library.json()["items"][0]["paper_id"] == "paper-1"

    parsed = client.post(
        "/extraction/parse",
        json={
            "paper_id": "paper-1",
            "pdf_path": str(pdf_path),
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
        },
    )
    assert parsed.status_code == 200
    assert parsed.json()["page_count"] == 2
    assert parsed.json()["metadata"]["extraction_method"] == "fake"
    assert "Graph Transformer" in parsed.json()["text"]

    extracted = client.post(
        "/extraction/extract",
        json={
            "paper_id": "paper-1",
            "pdf_path": str(pdf_path),
            "provider": "fake",
            "model": "fake-model",
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
        },
    )
    assert extracted.status_code == 200
    payload = extracted.json()
    assert payload["status"] == "success"
    assert payload["parse"]["metadata"]["extraction_method"] == "fake"
    assert payload["result"]["concepts"][0]["label"] == "Graph Transformer"

    history = client.get("/extraction/history", params={"metadata_db_path": str(db_path), "paper_id": "paper-1"})
    assert history.status_code == 200
    assert history.json()["items"][0]["concepts"][0]["label"] == "Graph Transformer"


def test_product_extraction_parse_returns_structured_errors(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "paper.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    with MetadataDB(str(db_path)) as db:
        db.ensure_paper_record("paper-1", title="Parser Error Paper", pdf_path=str(pdf_path))

    client = TestClient(product_main.app)
    invalid_parser = client.post(
        "/extraction/parse",
        json={
            "paper_id": "paper-1",
            "pdf_path": str(pdf_path),
            "parser": "missing-parser",
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
        },
    )

    assert invalid_parser.status_code == 400
    assert invalid_parser.json()["detail"] == {
        "message": "Unknown parser",
        "paper_id": "paper-1",
        "pdf_path": str(pdf_path.resolve()),
        "parser": "missing-parser",
    }

    class FailingParser:
        def parse(self, file_path, paper_id, force_parser=None):
            raise RuntimeError("parser backend unavailable")

    monkeypatch.setattr(product_main, "parser_router", FailingParser())
    parse_failure = client.post(
        "/extraction/parse",
        json={
            "paper_id": "paper-1",
            "pdf_path": str(pdf_path),
            "parser": "marker",
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
        },
    )

    assert parse_failure.status_code == 500
    detail = parse_failure.json()["detail"]
    assert detail["message"] == "PDF parsing failed"
    assert detail["paper_id"] == "paper-1"
    assert detail["pdf_path"] == str(pdf_path.resolve())
    assert detail["parser"] == "marker"
    assert detail["error"] == "parser backend unavailable"


def test_text_looks_garbled_distinguishes_clean_and_broken_extraction() -> None:
    clean = "Graph transformers improve link prediction across large citation networks. " * 4
    garbled = "x7$ q@9 ##z 1!a 0)( *&^ %$# qq1 zz9 ;;: ,,, ... !!! ??? @@@ ### $$$ %%% &&& " * 4

    assert product_main._text_looks_garbled(clean) is False
    assert product_main._text_looks_garbled(garbled) is True
    assert product_main._text_looks_garbled("too short") is False


def _run_garbled_title_extraction(tmp_path, monkeypatch, *, stored_title: str, garbled: bool):
    db_path = tmp_path / "metadata.duckdb"
    pdf_dir = tmp_path / "pdfs"
    pdf_dir.mkdir()
    pdf_path = pdf_dir / "files.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n")

    with MetadataDB(str(db_path)) as db:
        db.ensure_paper_record("paper-1", title=stored_title, pdf_path=str(pdf_path))

    extracted_text = (
        "x7$ q@9 ##z 1!a 0)( *&^ %$# qq1 zz9 ;;: ,,, ... !!! ??? @@@ ### $$$ %%% &&& " * 4
        if garbled
        else "Graph transformers improve link prediction across large citation networks. " * 4
    )

    class FakeParser:
        def parse(self, file_path, paper_id, force_parser=None):
            return SimpleNamespace(
                paper_id=paper_id,
                parser="marker",
                text=extracted_text,
                page_count=1,
                meta={"extraction_method": "fake"},
            )

    class FakePipeline:
        def process(self, paper_id, text, provider=None, overrides=None, link_concepts=True):
            return SimpleNamespace(
                paper_id=paper_id,
                paper_type="research",
                concepts=[],
                methods=[],
                concept_candidates=[],
                method_candidates=[],
                relations=[],
                claims=[],
                cross_domain_hints=[],
                terminology_conflicts=[],
                temporal_coverage={"paper_year": 2026},
                mathematical_content={"has_formulas": False},
                language_detected="en",
                quality_warnings=[],
                metadata_status="valid",
                blocking_errors=[],
                candidate_count=0,
                extraction_diagnostics={"mode": "fake"},
                raw_response="{}",
            )

    monkeypatch.setattr(product_main, "parser_router", FakeParser())
    monkeypatch.setattr(product_main, "extraction_pipeline", FakePipeline())
    monkeypatch.setattr(product_main, "_infer_pdf_title_from_bytes", lambda content: "Inferred Real Title From PDF")

    client = TestClient(product_main.app)
    response = client.post(
        "/extraction/extract",
        json={
            "paper_id": "paper-1",
            "pdf_path": str(pdf_path),
            "provider": "fake",
            "model": "fake-model",
            "metadata_db_path": str(db_path),
            "pdf_base_dir": str(pdf_dir),
        },
    )
    assert response.status_code == 200

    with MetadataDB(str(db_path)) as db:
        return db.get_paper("paper-1")


def test_garbled_extraction_overwrites_generic_title_with_inferred_pdf_title(tmp_path, monkeypatch) -> None:
    paper = _run_garbled_title_extraction(tmp_path, monkeypatch, stored_title="files", garbled=True)
    assert paper["title"] == "Inferred Real Title From PDF"


def test_clean_extraction_does_not_touch_a_fine_title(tmp_path, monkeypatch) -> None:
    paper = _run_garbled_title_extraction(tmp_path, monkeypatch, stored_title="A Perfectly Fine Title", garbled=False)
    assert paper["title"] == "A Perfectly Fine Title"


def test_garbled_extraction_does_not_overwrite_a_specific_title(tmp_path, monkeypatch) -> None:
    paper = _run_garbled_title_extraction(tmp_path, monkeypatch, stored_title="A Perfectly Fine Title", garbled=True)
    assert paper["title"] == "A Perfectly Fine Title"


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


def test_product_benchmark_suite_job_accepts_context_options(tmp_path, monkeypatch) -> None:
    captured = {}

    def fake_run_suite(config):
        captured["config"] = config
        return {
            "run_id": "test-run",
            "summary": {
                "provider": config.provider,
                "model": config.model,
                "context_policy": config.context_policy,
                "answer_score": 1.0,
            },
        }

    monkeypatch.setattr(product_main, "run_suite", fake_run_suite)
    client = TestClient(product_main.app)

    response = client.post(
        "/jobs/benchmark-suite",
        json={
            "suite": "core",
            "provider": "lm_studio",
            "model": "local-model",
            "context_policy": "whole",
            "compare_context_policies": ["auto", "whole", "chunk"],
            "answer_context_mode": "pdf_if_fits",
            "metadata_db_path": str(tmp_path / "metadata.duckdb"),
            "graph_db_path": str(tmp_path / "graph"),
            "pdf_base_dir": str(tmp_path / "pdfs"),
            "output_dir": str(tmp_path / "benchmarks"),
            "isolated_db": True,
        },
    )

    assert response.status_code == 200
    assert response.json()["status"] == "completed"
    config = captured["config"]
    assert config.context_policy == "whole"
    assert config.compare_context_policies == ["auto", "whole", "chunk"]
    assert config.answer_context_mode == "pdf_if_fits"
    assert config.provider == "lm_studio"
    assert config.model == "local-model"


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
    assert thread.json()["thread"]["ui_state"]["collapsed"] is True
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
    thread_ids = [item["id"] for item in threads.json()["items"]]

    ui_patch = client.patch(
        f"/notes/{note_id}/ai-threads/{thread_ids[-1]}",
        json={"metadata_db_path": str(db_path), "ui_state": {"collapsed": False}},
    )
    assert ui_patch.status_code == 200
    after_ui_patch = client.get(f"/notes/{note_id}/ai-threads", params={"metadata_db_path": str(db_path)})
    assert [item["id"] for item in after_ui_patch.json()["items"]] == thread_ids

    delete_one = client.post(f"/notes/{note_id}/ai-threads/{thread_id}/delete", params={"metadata_db_path": str(db_path)})
    assert delete_one.status_code == 200
    after_delete_one = client.get(f"/notes/{note_id}/ai-threads", params={"metadata_db_path": str(db_path)})
    assert after_delete_one.json()["total"] == 1

    delete_all = client.post(f"/notes/{note_id}/ai-threads/delete-all", params={"metadata_db_path": str(db_path)})
    assert delete_all.status_code == 200
    assert delete_all.json()["deleted"] == 1
    after_delete_all = client.get(f"/notes/{note_id}/ai-threads", params={"metadata_db_path": str(db_path)})
    assert after_delete_all.json()["total"] == 0


def test_note_ai_retries_empty_response_before_storing_thread(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    _fixture_db(db_path)
    client = TestClient(product_main.app)
    created = client.post(
        "/projects/demo/notes",
        params={"metadata_db_path": str(db_path)},
        json={"title": "Vorbereitung", "markdown": "# Start"},
    )
    assert created.status_code == 200
    note_id = created.json()["note"]["id"]

    class BlankThenUsefulRouter:
        default_provider = "fake"

        def __init__(self) -> None:
            self.calls = 0
            self.overrides: list[dict[str, object]] = []

        def chat(self, messages, provider=None, overrides=None):
            self.calls += 1
            self.overrides.append(dict(overrides or {}))
            assert "Markierter Text" in messages[-1]["content"] or "vorige Antwort" in messages[-1]["content"]
            if self.calls == 1:
                return ""
            return "Der Abschnitt sagt: Lernen ist wichtiger als starres Befolgen eines Protokolls."

        def provider_default_model(self, provider=None):
            return "fake-model"

    router = BlankThenUsefulRouter()
    monkeypatch.setattr(product_main, "llm_router", router)
    response = client.post(
        f"/notes/{note_id}/ai-threads",
        json={
            "selected_text": "learning rather than narrow protocol adherence. These findings show that people used the tool as a learning aid.",
            "instruction": "Fasse mir das in einfacher Sprache zusammen",
            "metadata_db_path": str(db_path),
        },
    )
    assert response.status_code == 200
    assert router.calls == 2
    assert int(router.overrides[1]["max_tokens"]) >= 2048
    assert router.overrides[1]["extra"]["include_reasoning"] is False
    assert router.overrides[1]["extra"]["chat_template_kwargs"]["thinking"] is False
    assert response.json()["replacement_text"].startswith("Der Abschnitt sagt")
    assert response.json()["thread"]["messages"][1]["content"].startswith("Der Abschnitt sagt")


def test_note_ask_stores_whole_note_thread_without_anchor(tmp_path, monkeypatch) -> None:
    db_path = tmp_path / "metadata.duckdb"
    _fixture_db(db_path)
    client = TestClient(product_main.app)
    created = client.post(
        "/projects/demo/notes",
        params={"metadata_db_path": str(db_path)},
        json={"title": "Vorbereitung", "markdown": "# Vorbereitung\n\nGraph Transformer und Citation Networks."},
    )
    assert created.status_code == 200
    note_id = created.json()["note"]["id"]

    class NoteAskRouter:
        default_provider = "fake"

        def chat(self, messages, provider=None, overrides=None):
            assert "Ganze Notiz" in messages[-1]["content"]
            assert "Graph Transformer" in messages[-1]["content"]
            return "Die Notiz verbindet Graph Transformer mit Citation Networks [p1]."

        def provider_default_model(self, provider=None):
            return "fake-model"

    monkeypatch.setattr(product_main, "llm_router", NoteAskRouter())
    response = client.post(
        f"/notes/{note_id}/ask",
        json={
            "question": "Was ist der Kern der Notiz?",
            "metadata_db_path": str(db_path),
            "use_kg_evidence": False,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["replacement_text"].startswith("Die Notiz verbindet")
    assert payload["thread"]["selected_text"] == ""
    assert payload["thread"]["anchor_quote"] == ""
    assert payload["thread"]["ui_state"]["scope"] == "note"


def test_auto_answer_streams_sse_events(tmp_path, monkeypatch) -> None:
    """POST /query/auto-answer streams the orchestrator's events as SSE."""
    captured: dict[str, object] = {}

    async def _fake_auto(*, question, llm_router, **kwargs):  # noqa: ANN001 - test fake
        captured["question"] = question
        captured["force"] = kwargs.get("force")
        captured["max_related_topics"] = kwargs.get("max_related_topics")
        yield {"status": "answer", "answer": {"answer": "weak", "no_answer": True}}
        yield {"status": "planning", "related_topics": ["t1"]}
        yield {"status": "harvesting", "scope": "main", "topic": question, "papers": [{"id": "arxiv:1", "title": "P"}], "grey": []}
        yield {"status": "done", "answer": {"answer": "strong [arxiv:1]"},
               "harvest_summary": {"harvested": True, "papers": [{"id": "arxiv:1", "title": "P"}], "grey": [], "related_topics": ["t1"]}}

    monkeypatch.setattr(product_main, "auto_research_answer", _fake_auto)
    client = TestClient(product_main.app)

    response = client.post(
        "/query/auto-answer",
        json={"question": "What is X?", "force": True, "max_related_topics": 3},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")

    events = [json.loads(line[len("data: "):]) for line in response.text.splitlines() if line.startswith("data: ")]
    statuses = [event["status"] for event in events]
    assert statuses == ["answer", "planning", "harvesting", "done"]
    assert events[-1]["harvest_summary"]["harvested"] is True
    # The endpoint forwarded the request fields to the orchestrator.
    assert captured["question"] == "What is X?"
    assert captured["force"] is True
    assert captured["max_related_topics"] == 3


def test_workspace_session_survives_an_empty_overwrite(tmp_path) -> None:
    """An empty history must never replace a real conversation.

    A client that booted before the backend was reachable used to fall back to an
    empty localStorage session and PUT that over the server copy, destroying it.
    """
    db_path = tmp_path / "sessions.duckdb"
    with MetadataDB(str(db_path)):
        pass
    client = TestClient(product_main.app)
    project = "Hirn und LLM"
    payload = {
        "history": [{"id": "t1", "question": "Wie lernt das Gehirn?"}, {"id": "t2", "question": "Und LLMs?"}],
        "activeTurnId": "t2",
        "savedAt": 1,
    }

    saved = client.put(
        f"/workspace/sessions/{project}",
        json={"payload": payload, "metadata_db_path": str(db_path)},
    )
    assert saved.status_code == 200

    wiped = client.put(
        f"/workspace/sessions/{project}",
        json={"payload": {"history": [], "activeTurnId": "", "savedAt": 2}, "metadata_db_path": str(db_path)},
    )
    assert wiped.status_code == 200
    assert len(wiped.json()["payload"]["history"]) == 2

    still_there = client.get(f"/workspace/sessions/{project}", params={"metadata_db_path": str(db_path)})
    assert len(still_there.json()["payload"]["history"]) == 2


def test_workspace_session_backups_can_be_listed_and_restored(tmp_path) -> None:
    db_path = tmp_path / "sessions.duckdb"
    with MetadataDB(str(db_path)):
        pass
    client = TestClient(product_main.app)
    project = "Backup-Projekt"

    def put(history, force=False):
        return client.put(
            f"/workspace/sessions/{project}",
            json={
                "payload": {"history": history, "activeTurnId": "", "savedAt": 1},
                "metadata_db_path": str(db_path),
                "force": force,
            },
        )

    assert put([{"id": "a"}, {"id": "b"}]).status_code == 200
    assert put([{"id": "c"}]).status_code == 200

    backups = client.get(
        f"/workspace/sessions/{project}/backups", params={"metadata_db_path": str(db_path)}
    ).json()["backups"]
    assert [entry["turn_count"] for entry in backups] == [2]

    # The explicit delete path may empty the session — and is itself backed up.
    assert put([], force=True).status_code == 200
    assert client.get(f"/workspace/sessions/{project}", params={"metadata_db_path": str(db_path)}).json()["payload"]["history"] == []

    restored = client.post(
        f"/workspace/sessions/{project}/restore",
        json={"saved_at": None, "metadata_db_path": str(db_path)},
    )
    assert restored.status_code == 200
    assert [turn["id"] for turn in restored.json()["payload"]["history"]] == ["c"]
