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


def test_parallel_stage_crud(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "stg.duckdb"))
    session = db.create_parallel_session("proj", "Frage?")
    sid = session["id"]

    s1 = db.add_parallel_stage(sid, "Etappe 1", goal="G1", status="aktiv")
    s2 = db.add_parallel_stage(sid, "Etappe 2", goal="G2")
    assert [s["position"] for s in db.list_parallel_stages(sid)] == [0, 1]
    assert db.get_parallel_session(sid)["stages"][0]["name"] == "Etappe 1"

    v = db.add_parallel_variant(sid, "V1", stage_id=s2["id"])
    assert db.get_parallel_variant(v["id"])["stage_id"] == s2["id"]

    updated = db.update_parallel_stage(
        s2["id"], status="aktiv", review_markdown="### Verständnis\nok",
        review_payload={"answer": "a"},
    )
    assert updated["status"] == "aktiv"
    assert updated["review_payload"]["answer"] == "a"

    assert db.list_parallel_sessions("proj")[0]["stage_count"] == 2

    # Deleting a stage moves its variants to the first remaining stage.
    assert db.delete_parallel_stage(s2["id"]) is True
    assert db.get_parallel_variant(v["id"])["stage_id"] == s1["id"]
    with pytest.raises(ValueError):
        db.delete_parallel_stage(s1["id"])

    db.delete_parallel_session(sid)
    assert db.list_parallel_stages(sid) == []


def test_parallel_stage_backfill_adopts_legacy_variants(tmp_path) -> None:
    """Pre-stage sessions get a default "Etappe 1" adopting their variants on DB open."""
    path = str(tmp_path / "bf.duckdb")
    with MetadataDB(path) as db:
        session = db.create_parallel_session("proj", "Frage?")
        sid = session["id"]
        db.add_parallel_variant(sid, "Alt")  # legacy shape: no stage_id

    with MetadataDB(path) as db:  # reopen → backfill runs
        full = db.get_parallel_session(sid)
        assert len(full["stages"]) == 1
        assert full["stages"][0]["name"] == "Etappe 1"
        assert full["stages"][0]["status"] == "aktiv"
        assert full["variants"][0]["stage_id"] == full["stages"][0]["id"]

    with MetadataDB(path) as db:  # second open is a no-op
        assert len(db.get_parallel_session(sid)["stages"]) == 1


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
# Stage roadmap + professor reviews (LLM mocked)
# --------------------------------------------------------------------------- #

def test_propose_stages_parses_and_caps() -> None:
    retriever = _FakeRetriever([_hit()])
    router = _FakeRouter({"stages": [{"name": f"E{i}", "goal": f"G{i}"} for i in range(8)]})
    stages = parallel_research.propose_stages(retriever, router, "Frage", max_n=3)
    assert [s["name"] for s in stages] == ["E0", "E1", "E2"]
    assert stages[0]["goal"] == "G0"


def test_propose_stages_tolerates_bad_llm_output() -> None:
    retriever = _FakeRetriever([_hit()])
    assert parallel_research.propose_stages(retriever, _FakeRouter({"nope": 1}), "Frage") == []

    class _Boom:
        def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
            raise RuntimeError("down")

    assert parallel_research.propose_stages(retriever, _Boom(), "Frage") == []


def test_professor_review_entry_structured_payload() -> None:
    retriever = _FakeRetriever([_hit()])
    router = _FakeRouter({
        "verstaendnis": "Verstanden [arxiv:1].",
        "staerken": ["gut"],
        "probleme": ["p1"],
        "ideen": [],
        "naechste_schritte": ["weiter"],
    })
    payload = parallel_research.professor_review_entry(
        retriever, router, question="F", variant={"name": "V1", "approach": "A"}, user_result="R",
    )
    pr = payload["professor_review"]
    assert pr["kind"] == "entry"
    assert pr["schema_version"] == parallel_research.PROFESSOR_SCHEMA_VERSION
    assert pr["probleme"] == ["p1"]
    assert "### Verständnis" in payload["answer"]
    assert "### Fehler & Probleme" in payload["answer"]
    assert any(link["paper_id"] == "arxiv:1" for link in payload["citation_links"])


def test_professor_review_entry_falls_back_to_freetext(monkeypatch) -> None:
    """Unusable LLM JSON → plain grounded free-text, no professor_review key."""
    class _Answer:
        def to_dict(self):
            return {"answer": "freitext", "citation_links": []}

    class _FakeResponder:
        def __init__(self, retriever=None, llm_router=None):  # noqa: ARG002
            pass

        def answer(self, prompt, **kwargs):  # noqa: ARG002
            return _Answer()

    monkeypatch.setattr(parallel_research, "GroundedResponder", _FakeResponder)
    retriever = _FakeRetriever([_hit()])
    payload = parallel_research.professor_review_entry(
        retriever, _FakeRouter({"quatsch": True}), question="F",
        variant={"name": "V"}, user_result="R",
    )
    assert payload["answer"] == "freitext"
    assert "professor_review" not in payload


def test_professor_review_stage_maps_verdicts() -> None:
    retriever = _FakeRetriever([_hit()])
    variants = [{"id": "v1", "name": "Alpha", "approach": "A", "entries": []}]
    router = _FakeRouter({
        "verstaendnis": "V [arxiv:1].",
        "staerken": [], "probleme": [], "ideen": [], "naechste_schritte": [],
        "varianten_bewertung": [
            {"variant_id": "unbekannt", "name": "Alpha", "urteil": "SUPER", "begruendung": "läuft"},
        ],
    })
    payload = parallel_research.professor_review_stage(
        retriever, router, question="F",
        stage={"id": "s1", "name": "Etappe 1", "goal": "G"}, variants=variants,
    )
    pr = payload["professor_review"]
    assert pr["kind"] == "stage"
    # Unknown variant_id is mapped by name; unknown verdicts coerce to "anpassen".
    assert pr["varianten_bewertung"][0]["variant_id"] == "v1"
    assert pr["varianten_bewertung"][0]["urteil"] == "anpassen"
    assert "### Varianten-Bewertung" in payload["answer"]


def test_synthesize_structured_final_review() -> None:
    retriever = _FakeRetriever([_hit()])
    stages = [{"id": "s1", "name": "Etappe 1", "goal": "G", "status": "abgeschlossen"}]
    variants = [{
        "id": "v1", "name": "Alpha", "approach": "A", "stage_id": "s1",
        "entries": [{"role": "user", "content": "Ergebnis 1"}],
    }]
    router = _FakeRouter({
        "gesamtverstaendnis": "Gesamt [arxiv:1].",
        "etappen_zusammenfassung": [{"stage_id": "s1", "fazit": "Ziel erreicht."}],
        "staerken": ["s"], "probleme": [], "ideen": [], "offene_punkte": ["o"],
        "finale_antwort": "Finale Antwort [arxiv:1].",
    })
    payload = parallel_research.synthesize(
        retriever, router, question="F", variants=variants, stages=stages,
    )
    pr = payload["professor_review"]
    assert pr["kind"] == "final"
    # Missing name in etappen_zusammenfassung is filled from the stage map.
    assert pr["etappen_zusammenfassung"][0]["name"] == "Etappe 1"
    assert "### Finale Antwort" in payload["answer"]
    assert "### Offene Punkte" in payload["answer"]


# --------------------------------------------------------------------------- #
# API endpoints (LLM + retrieval mocked)
# --------------------------------------------------------------------------- #

@pytest.fixture()
def client(monkeypatch):
    class FakeRouter:
        default_provider = "fake"

        def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
            user = str(messages[-1]["content"])
            if '"gesamtverstaendnis"' in user:  # synthesize (final review)
                return {
                    "gesamtverstaendnis": "Gesamt gut [arxiv:1].",
                    "etappen_zusammenfassung": [{"stage_id": "?", "name": "Etappe A", "fazit": "Ziel erreicht."}],
                    "staerken": ["solide"], "probleme": ["offen"], "ideen": ["mehr"],
                    "offene_punkte": ["x"], "finale_antwort": "Finale Antwort [arxiv:1].",
                }
            if "varianten_bewertung" in user:  # stage review
                return {
                    "verstaendnis": "Etappe verstanden [arxiv:1].",
                    "staerken": ["gut"], "probleme": ["p"], "ideen": ["i"], "naechste_schritte": ["n"],
                    "varianten_bewertung": [
                        {"variant_id": "", "name": "Variante 1", "urteil": "weiterverfolgen", "begruendung": "läuft"},
                    ],
                }
            if '"verstaendnis"' in user:  # entry review
                return {
                    "verstaendnis": "Ergebnis verstanden [arxiv:1].",
                    "staerken": ["gut"], "probleme": [], "ideen": [], "naechste_schritte": ["weiter"],
                }
            if '"stages"' in user:  # stage roadmap
                return {"stages": [
                    {"name": "Etappe A", "goal": "Grundlagen"},
                    {"name": "Etappe B", "goal": "Umsetzung"},
                ]}
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
    # The Etappen roadmap is planned on create: stage 1 aktiv, variants attached to it.
    assert [s["name"] for s in session["stages"]] == ["Etappe A", "Etappe B"]
    assert [s["status"] for s in session["stages"]] == ["aktiv", "offen"]
    assert all(v["stage_id"] == session["stages"][0]["id"] for v in session["variants"])

    res = client.post(f"/parallel/{sid}/variants", json={"name": "Eigene", **base})
    assert res.json()["variant"]["origin"] == "manual"
    assert res.json()["variant"]["stage_id"] == session["stages"][0]["id"]

    vid = session["variants"][0]["id"]
    res = client.post(f"/parallel/variants/{vid}/entries", json={"content": "80% erreicht", "request_feedback": True, **base})
    assert res.status_code == 200
    body = res.json()
    assert body["feedback_entry"] is not None
    # Structured professor review replaces the old free-text assessment.
    review = body["feedback_entry"]["answer_payload"]["professor_review"]
    assert review["kind"] == "entry"
    assert "### Verständnis" in body["feedback_entry"]["content"]
    variant = next(v for v in body["session"]["variants"] if v["id"] == vid)
    assert variant["status"] == "ergebnis"
    assert len(variant["entries"]) == 2

    res = client.post(f"/parallel/{sid}/synthesize", json=base)
    assert res.status_code == 200
    assert res.json()["session"]["status"] == "synthesized"
    assert res.json()["answer"]["professor_review"]["kind"] == "final"

    assert client.get("/projects/proj/parallel", params={"metadata_db_path": db_path}).json()["sessions"][0]["stage_count"] == 2

    res = client.delete(f"/parallel/{sid}", params={"metadata_db_path": db_path})
    assert res.json()["deleted"] is True


def test_parallel_stage_endpoints(client, tmp_path) -> None:
    db_path = str(tmp_path / "stages.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    session = client.post("/projects/proj/parallel", json={"question": "Wie X bauen?", "variant_count": 1, **base}).json()["session"]
    sid = session["id"]
    s1, s2 = session["stages"]

    # Manual stage (name required).
    res = client.post(f"/parallel/{sid}/stages", json={"name": "Etappe C", "goal": "Z", **base})
    assert [s["name"] for s in res.json()["session"]["stages"]] == ["Etappe A", "Etappe B", "Etappe C"]
    assert client.post(f"/parallel/{sid}/stages", json={**base}).status_code == 400

    # AI-proposed stages append as "offen".
    res = client.post(f"/parallel/{sid}/stages", json={"propose": True, **base})
    stages = res.json()["session"]["stages"]
    assert len(stages) == 5
    assert all(s["status"] == "offen" for s in stages[1:])

    # Completing the active stage auto-activates the next open one.
    res = client.patch(f"/parallel/stages/{s1['id']}", json={"status": "abgeschlossen", "metadata_db_path": db_path})
    stages = res.json()["session"]["stages"]
    assert stages[0]["status"] == "abgeschlossen"
    assert stages[1]["status"] == "aktiv"
    assert client.patch("/parallel/stages/nope", json={"name": "x", "metadata_db_path": db_path}).status_code == 404

    # Generate more variants into an explicit stage.
    res = client.post(f"/parallel/{sid}/generate", json={"variant_count": 2, "stage_id": s2["id"], **base})
    assert [v for v in res.json()["session"]["variants"] if v["stage_id"] == s2["id"]]

    # Professor stage review is persisted on the stage.
    res = client.post(f"/parallel/stages/{s2['id']}/review", json=base)
    assert res.status_code == 200
    assert res.json()["answer"]["professor_review"]["kind"] == "stage"
    stage2 = next(s for s in res.json()["session"]["stages"] if s["id"] == s2["id"])
    assert "### Verständnis" in stage2["review_markdown"]
    assert stage2["review_payload"]["professor_review"]["kind"] == "stage"

    # Deleting works down to the last stage, which is protected (400).
    stages = client.get(f"/parallel/{sid}", params={"metadata_db_path": db_path}).json()["session"]["stages"]
    for stage in stages[:-1]:
        assert client.delete(f"/parallel/stages/{stage['id']}", params={"metadata_db_path": db_path}).json()["deleted"] is True
    res = client.delete(f"/parallel/stages/{stages[-1]['id']}", params={"metadata_db_path": db_path})
    assert res.status_code == 400
    assert "Letzte Etappe" in res.json()["detail"]


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
    # The follow-up Vorschlag lands in the active stage.
    active = next(s for s in updated["stages"] if s["status"] == "aktiv")
    new_variant = updated["variants"][-1]
    assert new_variant["stage_id"] == active["id"]
