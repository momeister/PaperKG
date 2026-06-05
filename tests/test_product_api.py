from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

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
