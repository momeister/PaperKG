"""Tests for the Parallel-Research mode: DB CRUD, grounded variant proposals, and the
product API endpoints (LLM + retrieval mocked)."""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

import api.product_main as product_main
from query import parallel_research
from query.kg_retriever import Evidence, SearchHit, Source
from storage.metadata_db import MetadataDB


# --------------------------------------------------------------------------- #
# DB CRUD
# --------------------------------------------------------------------------- #

def test_parallel_session_crud(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "t.duckdb"))
    session = db.create_parallel_session("proj", "Wie X bauen?")
    sid = session["id"]
    assert session["variants"] == []

    v1 = db.add_parallel_variant(sid, "Variante 1", approach="A", rationale="R", suggested_prompt="P", origin="ai")
    v2 = db.add_parallel_variant(sid, "Variante 2", origin="manual")
    assert (v1["position"], v2["position"]) == (0, 1)

    db.add_parallel_entry(v1["id"], sid, "user", "ergebnis text")
    db.add_parallel_entry(v1["id"], sid, "assistant", "einordnung", answer_payload={"answer": "a", "citation_links": []})
    full = db.get_parallel_session(sid)
    assert len(full["variants"]) == 2
    assert len(full["variants"][0]["entries"]) == 2
    assert full["variants"][0]["entries"][1]["answer_payload"]["answer"] == "a"

    db.update_parallel_variant(v1["id"], status="ergebnis", name="Variante 1 - neu")
    assert db.get_parallel_variant(v1["id"])["name"] == "Variante 1 - neu"

    db.update_parallel_session(sid, status="synthesized", synthesis_markdown="## Beste", synthesis_payload={"answer": "final"})
    refreshed = db.get_parallel_session(sid)
    assert refreshed["status"] == "synthesized"
    assert refreshed["synthesis_payload"]["answer"] == "final"

    assert db.list_parallel_sessions("proj")[0]["variant_count"] == 2
    assert db.delete_parallel_variant(v2["id"]) is True
    assert len(db.get_parallel_session(sid)["variants"]) == 1
    assert db.delete_parallel_session(sid) is True
    assert db.get_parallel_session(sid) is None


# --------------------------------------------------------------------------- #
# Grounded variant proposals
# --------------------------------------------------------------------------- #

class _FakeRetriever:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, limit=10, paper_ids=None):  # noqa: ARG002
        return self._hits


class _FakeRouter:
    def __init__(self, json_payload):
        self._payload = json_payload

    def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
        return self._payload


def _hit() -> SearchHit:
    src = Source(paper_id="arxiv:1", title="T")
    hit = SearchHit(source=src)
    hit.add_evidence(Evidence(paper_id="arxiv:1", kind="abstract", text="relevant text", score=5.0))
    return hit


def test_propose_variants_parses_json_and_caps() -> None:
    retriever = _FakeRetriever([_hit()])
    router = _FakeRouter({
        "variants": [
            {"name": "V1", "approach": "tu A [arxiv:1]", "rationale": "weil [arxiv:1]", "prompt": "mach A"},
            {"name": "V2", "approach": "tu B", "rationale": "weil B", "prompt": "mach B"},
            {"name": "V3", "approach": "tu C", "rationale": "weil C", "prompt": "mach C"},
        ]
    })
    variants = parallel_research.propose_variants(retriever, router, "Frage", n=2)
    assert len(variants) == 2
    assert variants[0]["name"] == "V1"
    assert variants[0]["suggested_prompt"] == "mach A"
    assert "[arxiv:1]" in variants[0]["approach"]


def test_propose_variants_tolerates_bad_llm_output() -> None:
    retriever = _FakeRetriever([_hit()])
    router = _FakeRouter({"not_variants": 123})
    assert parallel_research.propose_variants(retriever, router, "Frage", n=3) == []


# --------------------------------------------------------------------------- #
# API endpoints (LLM + retrieval mocked)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(monkeypatch):
    class FakeRouter:
        default_provider = "fake"

        def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
            return {"variants": [
                {"name": "Variante 1", "approach": "A [arxiv:1]", "rationale": "R", "prompt": "p1"},
                {"name": "Variante 2", "approach": "B", "rationale": "R2", "prompt": "p2"},
            ]}

        def chat(self, messages, provider=None, overrides=None):  # noqa: ARG002
            return "Einordnung [arxiv:1]."

    monkeypatch.setattr(product_main, "llm_router", FakeRouter())
    return TestClient(product_main.app)


def test_parallel_endpoints_full_flow(client, tmp_path) -> None:
    db_path = str(tmp_path / "api.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}

    res = client.post("/projects/proj/parallel", json={"question": "Wie X bauen?", "variant_count": 2, **base})
    assert res.status_code == 200
    session = res.json()["session"]
    sid = session["id"]
    assert [v["name"] for v in session["variants"]] == ["Variante 1", "Variante 2"]
    assert session["variants"][0]["origin"] == "ai"

    res = client.post(f"/parallel/{sid}/variants", json={"name": "Eigene", **base})
    assert res.json()["variant"]["origin"] == "manual"

    vid = session["variants"][0]["id"]
    res = client.post(f"/parallel/variants/{vid}/entries", json={"content": "80% erreicht", "request_feedback": True, **base})
    assert res.status_code == 200
    body = res.json()
    assert body["feedback_entry"] is not None
    variant = next(v for v in body["session"]["variants"] if v["id"] == vid)
    assert variant["status"] == "ergebnis"
    assert len(variant["entries"]) == 2

    res = client.post(f"/parallel/{sid}/synthesize", json=base)
    assert res.status_code == 200
    assert res.json()["session"]["status"] == "synthesized"

    res = client.delete(f"/parallel/{sid}", params={"metadata_db_path": db_path})
    assert res.json()["deleted"] is True


def test_parallel_followup_keeps_session_and_appends(client, tmp_path) -> None:
    """A follow-up stays in the session: grounded answer + a new Vorschlag, never a new session."""
    db_path = str(tmp_path / "ask.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}

    session = client.post("/projects/proj/parallel", json={"question": "Wie X bauen?", "variant_count": 2, **base}).json()["session"]
    sid = session["id"]
    assert len(session["variants"]) == 2
    assert session.get("followups", []) == []

    res = client.post(f"/parallel/{sid}/ask", json={"question": "Unkonventionelle Wege?", "variant_count": 1, **base})
    assert res.status_code == 200
    body = res.json()
    assert body["answer"]["answer"]  # grounded chat answer present
    updated = body["session"]
    # Same session id, follow-up persisted, and one extra Vorschlag appended.
    assert updated["id"] == sid
    assert len(updated["followups"]) == 1
    assert updated["followups"][0]["question"] == "Unkonventionelle Wege?"
    assert len(updated["variants"]) == 3
