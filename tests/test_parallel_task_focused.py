"""Tests for the Task-Focused Parallel-Research mode (Session 3).

Covers:
- DB CRUD for ``user_steps`` (add/update/delete step) + ``task_id``/``creativity_level``
  on the session + ``rejection_reason`` on a variant.
- ``parallel_research`` task-spec injection (variants carry ``implementation_steps``,
  creativity hint lands in the prompt, creativity_level steers temperature).
- ``build_implementation_plan`` sums accepted steps into markdown.
- API endpoints: create with task_id + creativity_level, step lifecycle (add →
  in_progress → result+feedback → delete), variant reject with reason, implementation
  plan export, follow-up with task_id, generate with task_id.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.product_main as product_main
from query import parallel_research
from query.kg_retriever import Evidence, SearchHit, Source
from storage.metadata_db import MetadataDB


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #


def _hit() -> SearchHit:
    src = Source(paper_id="arxiv:1", title="T")
    hit = SearchHit(source=src)
    hit.add_evidence(
        Evidence(paper_id="arxiv:1", kind="abstract", text="relevant text", score=5.0)
    )
    return hit


class _FakeRetriever:
    def __init__(self, hits):
        self._hits = hits

    def search(self, query, limit=10, paper_ids=None):  # noqa: ARG002
        return self._hits


class _FakeRouter:
    """Returns canned JSON keyed off the last user-message content."""

    def __init__(self, json_payload):
        self._payload = json_payload
        self.last_overrides: dict[str, Any] | None = None
        self.last_system: str = ""

    def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
        self.last_overrides = dict(overrides or {})
        self.last_system = str(messages[0]["content"]) if messages else ""
        return self._payload

    def chat(self, messages, provider=None, overrides=None):  # noqa: ARG002
        return "Einordnung [arxiv:1]."


def _task_spec() -> dict[str, Any]:
    return {
        "title": "RSNA Knee Abnormality Detection",
        "objective": "Detect knee abnormalities from MRI volumes.",
        "evaluation": "Weighted multi-label ROC AUC across diagnoses.",
        "rules": ["No external data", "Submission via notebook"],
        "constraints": ["GPU < 16GB", "Inference time < 10min"],
        "datasets": [
            {"name": "RSNA Train", "url": "kaggle.com/competitions/rsna-knee/train"},
        ],
        "timeline": "2 months",
        "suggested_directions": ["3D CNN", "slice-wise 2D + aggregation"],
    }


def _seed_task(db: MetadataDB, project_id: str = "proj") -> str:
    task = db.create_task(
        project_id,
        title="RSNA Knee Abnormality Detection",
        task_json=_task_spec(),
    )
    return str(task["id"])


# --------------------------------------------------------------------------- #
# DB CRUD: user_steps + task_id/creativity_level + rejection_reason
# --------------------------------------------------------------------------- #


def test_parallel_session_stores_task_id_and_creativity(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "t.duckdb"))
    session = db.create_parallel_session(
        "proj", "Wie?", task_id="task_abc", creativity_level=4
    )
    sid = session["id"]
    assert session["task_id"] == "task_abc"
    assert session["creativity_level"] == 4
    # Reload from a fresh DB to verify persistence + migration safety.
    with MetadataDB(str(tmp_path / "t.duckdb")) as db2:
        loaded = db2.get_parallel_session(sid)
        assert loaded["task_id"] == "task_abc"
        assert loaded["creativity_level"] == 4


def test_parallel_variant_user_steps_roundtrip(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "st.duckdb"))
    sid = db.create_parallel_session("proj", "Wie?")["id"]
    v = db.add_parallel_variant(sid, "V1", origin="ai")

    # AI steps via add_parallel_variant_step.
    v = db.add_parallel_variant_step(v["id"], "Schritt 1", origin="ai")
    v = db.add_parallel_variant_step(v["id"], "Schritt 2", origin="ai")
    steps = v["user_steps"]
    assert len(steps) == 2
    assert all(s["status"] == "vorgeschlagen" for s in steps)
    assert steps[0]["text"] == "Schritt 1"
    assert steps[0]["origin"] == "ai"

    # Mark step 1 "in_progress" + attach a rationale-style patch.
    sid_step1 = steps[0]["id"]
    v = db.update_parallel_variant_step(v["id"], sid_step1, status="in_progress")
    assert (
        next(s for s in v["user_steps"] if s["id"] == sid_step1)["status"]
        == "in_progress"
    )

    # Submit a result for step 2 → marks done + stores result.
    sid_step2 = steps[1]["id"]
    v = db.update_parallel_variant_step(
        v["id"], sid_step2, status="done", result="AUC 0.71"
    )
    step2 = next(s for s in v["user_steps"] if s["id"] == sid_step2)
    assert step2["status"] == "done"
    assert step2["result"] == "AUC 0.71"

    # Delete step 1.
    assert db.delete_parallel_variant_step(v["id"], sid_step1) is True
    v = db.get_parallel_variant(v["id"])
    assert len(v["user_steps"]) == 1
    # Deleting a non-existent step is a no-op (False).
    assert db.delete_parallel_variant_step(v["id"], "nope") is False


def test_parallel_variant_rejection_reason(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "r.duckdb"))
    sid = db.create_parallel_session("proj", "Wie?")["id"]
    v = db.add_parallel_variant(sid, "V1", origin="ai")
    v = db.update_parallel_variant(
        v["id"], status="abgelehnt", rejection_reason="zu rechenintensiv"
    )
    assert v["status"] == "abgelehnt"
    assert v["rejection_reason"] == "zu rechenintensiv"
    # Reload to confirm persistence.
    with MetadataDB(str(tmp_path / "r.duckdb")) as db2:
        v2 = db2.get_parallel_variant(v["id"])
        assert v2["rejection_reason"] == "zu rechenintensiv"


def test_parallel_variant_backfill_user_steps_column(tmp_path) -> None:
    """Legacy variants (pre-migration, no user_steps column) open with [] user_steps."""
    path = str(tmp_path / "bf.duckdb")
    with MetadataDB(path) as db:
        sid = db.create_parallel_session("proj", "Wie?")["id"]
        db.add_parallel_variant(sid, "Alt")
    with MetadataDB(path) as db:
        v = db.get_parallel_variant(db.get_parallel_session(sid)["variants"][0]["id"])
        assert v["user_steps"] == []


# --------------------------------------------------------------------------- #
# parallel_research: task-spec injection + creativity
# --------------------------------------------------------------------------- #


def test_propose_variants_with_task_spec_extracts_implementation_steps() -> None:
    retriever = _FakeRetriever([_hit()])
    router = _FakeRouter(
        {
            "variants": [
                {
                    "name": "3D CNN",
                    "approach": "3D ResNet auf Volumen [arxiv:1]",
                    "rationale": "räumliche Struktur [arxiv:1]",
                    "prompt": "baue 3D ResNet",
                    "implementation_steps": [
                        {
                            "text": "Lade DICOM",
                            "rationale": "Rohdaten [arxiv:1]",
                            "citation": "[arxiv:1]",
                        },
                        {
                            "text": "Window/Normalize",
                            "rationale": "HU-Skala",
                            "citation": "",
                        },
                        "plain string step",
                    ],
                },
            ]
        }
    )
    variants = parallel_research.propose_variants(
        retriever,
        router,
        "Wie?",
        n=1,
        task_spec=_task_spec(),
        creativity_level=4,
    )
    assert len(variants) == 1
    v = variants[0]
    assert v["name"] == "3D CNN"
    assert v["implementation_steps"][0]["text"] == "Lade DICOM"
    assert v["implementation_steps"][0]["citation"] == "[arxiv:1]"
    # Plain-string step coerced to {text, rationale:"", citation:""}.
    assert v["implementation_steps"][2]["text"] == "plain string step"
    # Creativity hint landed in the system message.
    assert "Kreativitätslevel 4" in router.last_system
    # Temperature override scaled by creativity_level.
    assert (
        pytest.approx(router.last_overrides["temperature"], rel=1e-6) == 0.4 + 0.1 * 4
    )


def test_propose_variants_task_spec_injects_block_into_prompt() -> None:
    retriever = _FakeRetriever([_hit()])
    router = _FakeRouter({"variants": []})
    parallel_research.propose_variants(
        retriever,
        router,
        "Wie?",
        n=1,
        task_spec=_task_spec(),
    )
    # The user message is the second one; assert the task block is present.
    # _FakeRouter stores only system; rebuild a capturing router instead.
    captured = {}

    class _Cap:
        def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
            captured["user"] = messages[-1]["content"]
            return {"variants": []}

    parallel_research.propose_variants(
        retriever,
        _Cap(),
        "Wie?",
        n=1,
        task_spec=_task_spec(),
    )
    assert "## Aufgabe (Task-Spec)" in captured["user"]
    assert "RSNA Knee Abnormality Detection" in captured["user"]
    assert "Weighted multi-label ROC AUC" in captured["user"]
    # suggested_directions is NOT part of _task_spec_block (handled separately by the
    # research-suggester); confirm it is intentionally absent from the variant prompt.
    assert "3D CNN" not in captured["user"]


def test_propose_variants_without_task_spec_has_no_implementation_steps() -> None:
    retriever = _FakeRetriever([_hit()])
    router = _FakeRouter(
        {
            "variants": [
                {
                    "name": "V1",
                    "approach": "A",
                    "rationale": "R",
                    "prompt": "p",
                    "implementation_steps": [{"text": "sollte ignoriert werden"}],
                },
            ]
        }
    )
    variants = parallel_research.propose_variants(retriever, router, "Wie?", n=1)
    assert "implementation_steps" not in variants[0]


def test_propose_stages_with_task_spec_injects_block() -> None:
    retriever = _FakeRetriever([_hit()])
    captured = {}

    class _Cap:
        def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
            captured["user"] = messages[-1]["content"]
            return {"stages": [{"name": "E1", "goal": "G1"}]}

    stages = parallel_research.propose_stages(
        retriever,
        _Cap(),
        "Wie?",
        max_n=3,
        task_spec=_task_spec(),
        creativity_level=2,
    )
    assert stages[0]["name"] == "E1"
    assert "## Aufgabe (Task-Spec)" in captured["user"]
    assert "RSNA Knee Abnormality Detection" in captured["user"]


def test_propose_overview_with_task_spec_includes_block(monkeypatch) -> None:
    """``propose_overview`` runs the prompt through ``GroundedResponder``; we capture
    the user message and assert the Task-Spec block + creativity hint landed there."""
    retriever = _FakeRetriever([_hit()])
    captured: dict[str, str] = {}

    class _Answer:
        def to_dict(self):
            return {"answer": "Overview [arxiv:1].", "citation_links": []}

    class _FakeResponder:
        def __init__(self, retriever=None, llm_router=None):  # noqa: ARG002
            pass

        def answer(self, prompt, **kwargs):  # noqa: ARG002
            captured["user"] = str(prompt)
            return _Answer()

    monkeypatch.setattr(parallel_research, "GroundedResponder", _FakeResponder)
    payload = parallel_research.propose_overview(
        retriever,
        _FakeRouter({}),
        "Wie?",
        task_spec=_task_spec(),
        creativity_level=3,
    )
    assert payload["answer"] == "Overview [arxiv:1]."
    assert "## Aufgabe (Task-Spec)" in captured["user"]
    assert "RSNA Knee Abnormality Detection" in captured["user"]
    assert "Zeitplan: 2 months" in captured["user"]
    # Creativity hint appended to the "Wie gehst du ran?" section.
    assert "Kreativitätslevel 3" in captured["user"]


# --------------------------------------------------------------------------- #
# build_implementation_plan
# --------------------------------------------------------------------------- #


def test_build_implementation_plan_collects_accepted_steps() -> None:
    session = {
        "variants": [
            {
                "name": "V1",
                "approach": "3D CNN [arxiv:1]",
                "user_steps": [
                    {
                        "id": "s1",
                        "text": "Lade DICOM",
                        "status": "done",
                        "rationale": "Rohdaten",
                        "citation": "[arxiv:1]",
                        "result": "100 Fälle",
                    },
                    {"id": "s2", "text": "Augmentieren", "status": "in_progress"},
                    {
                        "id": "s3",
                        "text": "Verworfen",
                        "status": "vorgeschlagen",
                        "rejected": True,
                    },
                ],
            },
            {"name": "V2", "approach": "", "user_steps": []},
        ],
    }
    plan = parallel_research.build_implementation_plan(session, task_spec=_task_spec())
    assert plan["title"] == "RSNA Knee Abnormality Detection"
    assert plan["objective"].startswith("Detect knee abnormalities")
    assert len(plan["variants"]) == 1
    v = plan["variants"][0]
    assert v["name"] == "V1"
    # done + in_progress kept; rejected dropped.
    assert len(v["steps"]) == 2
    assert v["steps"][0]["text"] == "Lade DICOM"
    assert v["steps"][0]["result"] == "100 Fälle"
    assert v["steps"][1]["status"] == "in_progress"
    assert "## V1" in plan["plan_markdown"]
    assert "Ergebnis: 100 Fälle" in plan["plan_markdown"]


def test_build_implementation_plan_empty_when_no_accepted_steps() -> None:
    session = {
        "variants": [
            {
                "name": "V",
                "approach": "",
                "user_steps": [
                    {
                        "id": "s",
                        "text": "x",
                        "status": "vorgeschlagen",
                        "rejected": True,
                    },
                ],
            }
        ]
    }
    plan = parallel_research.build_implementation_plan(session)
    assert plan["variants"] == []
    assert "Noch keine akzeptierten Schritte" in plan["plan_markdown"]


# --------------------------------------------------------------------------- #
# API endpoints (LLM + retrieval mocked)
# --------------------------------------------------------------------------- #


@pytest.fixture()
def client(monkeypatch):
    class FakeRouter:
        default_provider = "fake"

        def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
            user = str(messages[-1]["content"])
            if '"gesamtverstaendnis"' in user:  # synthesize
                return {
                    "gesamtverstaendnis": "Gesamt [arxiv:1].",
                    "etappen_zusammenfassung": [],
                    "staerken": ["s"],
                    "probleme": [],
                    "ideen": [],
                    "offene_punkte": [],
                    "finale_antwort": "Finale [arxiv:1].",
                }
            if "varianten_bewertung" in user:  # stage review
                return {
                    "verstaendnis": "Etappe [arxiv:1].",
                    "staerken": [],
                    "probleme": [],
                    "ideen": [],
                    "naechste_schritte": [],
                    "varianten_bewertung": [],
                }
            if '"verstaendnis"' in user:  # entry review (professor_review_entry)
                return {
                    "verstaendnis": "Ergebnis [arxiv:1].",
                    "staerken": ["g"],
                    "probleme": [],
                    "ideen": [],
                    "naechste_schritte": ["n"],
                }
            if '"stages"' in user:  # stage roadmap
                return {"stages": [{"name": "E1", "goal": "G1"}]}
            # variants — include implementation_steps when the prompt asks for them.
            has_task = "## Aufgabe (Task-Spec)" in user
            v = {
                "name": "3D CNN",
                "approach": "A [arxiv:1]",
                "rationale": "R",
                "prompt": "p",
            }
            if has_task:
                v["implementation_steps"] = [
                    {
                        "text": "Lade DICOM [arxiv:1]",
                        "rationale": "Rohdaten",
                        "citation": "[arxiv:1]",
                    },
                    {"text": "Baue Modell", "rationale": "Architektur", "citation": ""},
                ]
            return {"variants": [v]}

        def chat(self, messages, provider=None, overrides=None):  # noqa: ARG002
            return "Einordnung [arxiv:1]."

    monkeypatch.setattr(product_main, "llm_router", FakeRouter())
    return TestClient(product_main.app)


def test_task_focused_parallel_create_injects_steps_and_binds_task(
    client, tmp_path
) -> None:
    db_path = str(tmp_path / "tf.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    with MetadataDB(db_path) as db:
        task_id = _seed_task(db)

    res = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie löse ich RSNA?",
            "variant_count": 1,
            "task_id": task_id,
            "creativity_level": 4,
            **base,
        },
    )
    assert res.status_code == 200, res.text
    session = res.json()["session"]
    assert session["task_id"] == task_id
    assert session["creativity_level"] == 4
    assert len(session["variants"]) == 1
    v = session["variants"][0]
    assert v["origin"] == "ai"
    # AI-proposed steps persisted as user_steps with status "vorgeschlagen".
    assert len(v["user_steps"]) == 2
    assert all(s["status"] == "vorgeschlagen" for s in v["user_steps"])
    assert all(s["origin"] == "ai" for s in v["user_steps"])
    assert v["user_steps"][0]["text"] == "Lade DICOM [arxiv:1]"

    # 404 when the task_id does not exist.
    res = client.post(
        "/projects/proj/parallel",
        json={
            "question": "x",
            "task_id": "task_missing",
            **base,
        },
    )
    assert res.status_code == 404
    assert "task_missing" in res.json()["detail"]


def test_parallel_step_lifecycle_add_update_delete(client, tmp_path) -> None:
    db_path = str(tmp_path / "life.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            **base,
        },
    ).json()["session"]
    vid = session["variants"][0]["id"]

    # Add a user step.
    res = client.post(
        f"/parallel/variants/{vid}/steps",
        json={
            "text": "Eigener Schritt",
            "rationale": "weil",
            "citation": "[arxiv:1]",
            "origin": "user",
            **base,
        },
    )
    assert res.status_code == 200
    step = res.json()["step"]
    assert step["text"] == "Eigener Schritt"
    assert step["origin"] == "user"
    assert step["status"] == "vorgeschlagen"
    vid_step = step["id"]
    variant = res.json()["variant"]
    assert len(variant["user_steps"]) == 1

    # Mark "Das probiere ich" = in_progress.
    res = client.patch(
        f"/parallel/variants/{vid}/steps/{vid_step}",
        json={
            "status": "in_progress",
            **base,
        },
    )
    assert res.status_code == 200
    v = res.json()["variant"]
    assert (
        next(s for s in v["user_steps"] if s["id"] == vid_step)["status"]
        == "in_progress"
    )

    # Delete the step.
    res = client.delete(
        f"/parallel/variants/{vid}/steps/{vid_step}",
        params={"metadata_db_path": db_path},
    )
    assert res.status_code == 200
    assert res.json()["deleted"] is True
    assert len(res.json()["variant"]["user_steps"]) == 0

    # Deleting a missing step 404s.
    res = client.delete(
        f"/parallel/variants/{vid}/steps/nope", params={"metadata_db_path": db_path}
    )
    assert res.status_code == 404

    # Adding a step to a missing variant 404s.
    res = client.post("/parallel/variants/nope/steps", json={"text": "x", **base})
    assert res.status_code == 404


def test_parallel_step_result_creates_entry_and_feedback(client, tmp_path) -> None:
    db_path = str(tmp_path / "res.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    with MetadataDB(db_path) as db:
        task_id = _seed_task(db)
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            "task_id": task_id,
            **base,
        },
    ).json()["session"]
    vid = session["variants"][0]["id"]
    # The FakeRouter seeds implementation_steps when a Task-Spec is in the prompt.
    assert len(session["variants"][0]["user_steps"]) >= 1
    step_id = session["variants"][0]["user_steps"][0]["id"]

    res = client.post(
        f"/parallel/variants/{vid}/steps/{step_id}/result",
        json={
            "result": "AUC 0.72 erreicht",
            "request_feedback": True,
            **base,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    # Step marked done.
    step = next(
        s for s in body["session"]["variants"][0]["user_steps"] if s["id"] == step_id
    )
    assert step["status"] == "done"
    assert step["result"] == "AUC 0.72 erreicht"
    # A user entry was recorded ...
    assert body["user_entry"]["role"] == "user"
    assert body["user_entry"]["content"] == "AUC 0.72 erreicht"
    # ... and a grounded professor feedback entry was appended.
    assert body["feedback_entry"] is not None
    assert body["feedback_entry"]["role"] == "assistant"
    pr = body["feedback_entry"]["answer_payload"]["professor_review"]
    assert pr["kind"] == "entry"
    assert "### Verständnis" in body["feedback_entry"]["content"]


def test_parallel_step_result_without_feedback_records_only_entry(
    client, tmp_path
) -> None:
    db_path = str(tmp_path / "res2.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    with MetadataDB(db_path) as db:
        task_id = _seed_task(db)
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            "task_id": task_id,
            **base,
        },
    ).json()["session"]
    vid = session["variants"][0]["id"]
    assert len(session["variants"][0]["user_steps"]) >= 1
    step_id = session["variants"][0]["user_steps"][0]["id"]

    res = client.post(
        f"/parallel/variants/{vid}/steps/{step_id}/result",
        json={
            "result": "Ergebnis nur",
            "request_feedback": False,
            **base,
        },
    )
    assert res.status_code == 200
    assert res.json()["feedback_entry"] is None
    assert res.json()["user_entry"]["content"] == "Ergebnis nur"


def test_parallel_variant_reject_with_reason(client, tmp_path) -> None:
    db_path = str(tmp_path / "rej.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            **base,
        },
    ).json()["session"]
    vid = session["variants"][0]["id"]

    # Explicit reject endpoint.
    res = client.post(
        f"/parallel/variants/{vid}/reject",
        json={
            "reason": "zu rechenintensiv",
            **base,
        },
    )
    assert res.status_code == 200
    v = res.json()["variant"]
    assert v["status"] == "abgelehnt"
    assert v["rejection_reason"] == "zu rechenintensiv"

    # Rejecting a missing variant 404s.
    res = client.post("/parallel/variants/nope/reject", json={"reason": "x", **base})
    assert res.status_code == 404


def test_parallel_variant_patch_rejects_via_reason(client, tmp_path) -> None:
    db_path = str(tmp_path / "rej2.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            **base,
        },
    ).json()["session"]
    vid = session["variants"][0]["id"]

    res = client.patch(
        f"/parallel/variants/{vid}",
        json={
            "rejection_reason": "nicht kompatibel",
            **base,
        },
    )
    assert res.status_code == 200
    v = res.json()["variant"]
    assert v["status"] == "abgelehnt"
    assert v["rejection_reason"] == "nicht kompatibel"


def test_parallel_implementation_plan_export(client, tmp_path) -> None:
    db_path = str(tmp_path / "plan.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    with MetadataDB(db_path) as db:
        task_id = _seed_task(db)
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            "task_id": task_id,
            **base,
        },
    ).json()["session"]
    sid = session["id"]
    vid = session["variants"][0]["id"]
    # Accept the seeded AI step (mark in_progress).
    step_id = session["variants"][0]["user_steps"][0]["id"]
    client.patch(
        f"/parallel/variants/{vid}/steps/{step_id}",
        json={
            "status": "in_progress",
            **base,
        },
    )

    res = client.post(
        f"/parallel/{sid}/implementation-plan", params={"metadata_db_path": db_path}
    )
    assert res.status_code == 200
    plan = res.json()["plan"]
    assert plan["title"] == "RSNA Knee Abnormality Detection"
    assert len(plan["variants"]) == 1
    assert plan["variants"][0]["steps"][0]["status"] == "in_progress"
    assert "## 3D CNN" in plan["plan_markdown"]

    # Missing session 404s.
    res = client.post(
        "/parallel/nope/implementation-plan", params={"metadata_db_path": db_path}
    )
    assert res.status_code == 404


def test_parallel_followup_with_task_id(client, tmp_path) -> None:
    db_path = str(tmp_path / "fu.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    with MetadataDB(db_path) as db:
        task_id = _seed_task(db)
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            "task_id": task_id,
            **base,
        },
    ).json()["session"]
    sid = session["id"]

    res = client.post(
        f"/parallel/{sid}/ask",
        json={
            "question": "Andere Architektur?",
            "variant_count": 1,
            "task_id": task_id,
            **base,
        },
    )
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["answer"]["answer"]  # grounded chat answer present
    # Same session, follow-up recorded, one more variant appended.
    assert body["session"]["id"] == sid
    assert len(body["session"]["followups"]) == 1
    assert len(body["session"]["variants"]) == 2


def test_parallel_generate_with_task_id(client, tmp_path) -> None:
    db_path = str(tmp_path / "gen.duckdb")
    base = {"metadata_db_path": db_path, "graph_db_path": str(tmp_path / "g")}
    with MetadataDB(db_path) as db:
        task_id = _seed_task(db)
    session = client.post(
        "/projects/proj/parallel",
        json={
            "question": "Wie?",
            "variant_count": 1,
            "task_id": task_id,
            **base,
        },
    ).json()["session"]
    sid = session["id"]
    stage_id = session["stages"][0]["id"]

    res = client.post(
        f"/parallel/{sid}/generate",
        json={
            "variant_count": 1,
            "stage_id": stage_id,
            "task_id": task_id,
            **base,
        },
    )
    assert res.status_code == 200
    new_variant = res.json()["session"]["variants"][-1]
    assert new_variant["stage_id"] == stage_id
    # Task-focused generate seeds user_steps too.
    assert len(new_variant["user_steps"]) >= 1
    assert all(s["origin"] == "ai" for s in new_variant["user_steps"])
