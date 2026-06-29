"""Tests for server-side persistence of deep-research (Tiefensuche) trees: DB CRUD,
streaming persistence during ``/research/tree``, and the session endpoints."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

import api.product_main as product_main
from storage.metadata_db import MetadataDB


def test_research_session_db_crud(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "t.duckdb"))
    db.upsert_research_session("s1", "proj", "F?", "running", [{"id": "n1", "status": "running"}])
    db.upsert_research_session("s1", "proj", "F?", "done", [
        {"id": "n1", "status": "done", "answer": {"answer": "a"}},
        {"id": "synthesis", "status": "synthesis", "document": "doc"},
    ])
    got = db.get_research_session("s1")
    assert len(got["nodes"]) == 2
    assert got["status"] == "done"

    summary = db.list_research_sessions("proj")[0]
    assert summary["node_count"] == 2
    assert summary["done_count"] == 1
    assert summary["has_synthesis"] is True
    assert "payload" not in summary  # list omits the heavy node payload

    assert db.delete_research_session("s1") is True
    assert db.get_research_session("s1") is None


class _FakeRunner:
    def __init__(self, router):  # noqa: ARG002
        pass

    async def stream_events(self, **kwargs):  # noqa: ARG002
        yield f"data: {json.dumps({'id': 'n1', 'status': 'running', 'question': 'q'})}\n\n"
        yield f"data: {json.dumps({'id': 'n1', 'status': 'done', 'question': 'q', 'answer': {'answer': 'a'}})}\n\n"
        yield f"data: {json.dumps({'id': 'synthesis', 'status': 'synthesis', 'document': 'doc'})}\n\n"


@pytest.fixture()
def client(monkeypatch):
    monkeypatch.setattr(product_main, "ResearchTreeRunner", _FakeRunner)
    return TestClient(product_main.app)


def test_research_tree_stream_persists_session(client, tmp_path) -> None:
    db_path = str(tmp_path / "stream.duckdb")
    res = client.post("/research/tree", json={
        "question": "Frage?",
        "project_id": "proj",
        "session_id": "sess-123",
        "metadata_db_path": db_path,
    })
    assert res.status_code == 200
    # node 'n1' is updated in place (running -> done), plus the synthesis node => 2 nodes.
    assert "data:" in res.text

    with MetadataDB(db_path) as db:
        session = db.get_research_session("sess-123")
    assert session is not None
    assert session["status"] == "done"
    ids = [n["id"] for n in session["nodes"]]
    assert ids == ["n1", "synthesis"]
    n1 = session["nodes"][0]
    assert n1["status"] == "done" and n1["answer"]["answer"] == "a"


def test_research_session_endpoints(client, tmp_path) -> None:
    db_path = str(tmp_path / "ep.duckdb")
    with MetadataDB(db_path) as db:
        db.upsert_research_session("rs1", "proj", "F?", "running", [{"id": "n1", "status": "done"}])

    res = client.get("/research/sessions/proj", params={"metadata_db_path": db_path})
    assert res.json()["sessions"][0]["node_count"] == 1

    res = client.get("/research/session/rs1", params={"metadata_db_path": db_path})
    assert len(res.json()["session"]["nodes"]) == 1

    res = client.put("/research/session/rs1", json={
        "project_id": "proj", "question": "F?", "status": "done",
        "nodes": [{"id": "n1"}, {"id": "n2"}], "metadata_db_path": db_path,
    })
    assert len(res.json()["session"]["nodes"]) == 2

    res = client.get("/research/session/missing", params={"metadata_db_path": db_path})
    assert res.status_code == 404

    res = client.delete("/research/session/rs1", params={"metadata_db_path": db_path})
    assert res.json()["deleted"] is True
