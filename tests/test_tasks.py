"""Tests for the Task-Focused Mode (Session 1: Backend Kern).

Covers: tasks DB CRUD + project-scope migration, Task-Spec extraction
normalization, research-direction suggester, implementation planner (with
bare-citation stripping), and the product API endpoints under
``/projects/{id}/tasks`` and ``/tasks/...`` (LLM faked). Also checks that the
Task-Spec is injectable as a grey source (``grey::task_{id}``) and that
``AnswerRequest.task_id`` injects the spec into /query/answer context.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

import api.product_main as product_main
from query.task_extractor import (
    normalize_task_spec,
    _fallback_title,
    _extract_meta_fallback,
    _extract_html_title,
)
from query.task_implementation_planner import normalize_plan
from query.task_research_suggester import normalize_directions
from storage.metadata_db import MetadataDB


# --------------------------------------------------------------------------- #
# DB CRUD + project-scope migration
# --------------------------------------------------------------------------- #


def test_task_crud(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "t.duckdb"))
    task = db.create_task(
        "proj", "Kaggle Test", {"objective": "predict knee"}, source_kind="text"
    )
    tid = task["id"]
    assert task["title"] == "Kaggle Test"
    assert task["task_json"] == {"objective": "predict knee"}
    assert task["source_kind"] == "text"

    got = db.get_task(tid)
    assert got is not None and got["title"] == "Kaggle Test"

    lst = db.list_tasks("proj")
    assert len(lst) == 1
    # Andere Projekte sind leer
    assert db.list_tasks("other") == []

    upd = db.update_task(tid, title="Renamed", task_json={"objective": "x2"})
    assert upd["title"] == "Renamed" and upd["task_json"] == {"objective": "x2"}

    assert db.delete_task(tid) is True
    assert db.get_task(tid) is None
    assert db.delete_task(tid) is False


def test_task_project_scope_rename_migrates_rows(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "scope.duckdb"))
    db.create_task("old_proj", "T1", {"objective": "a"})
    db.create_task("old_proj", "T2", {"objective": "b"})
    db.create_task("keep_proj", "T3", {"objective": "c"})

    moved = db.rename_project("old_proj", "new_proj")
    assert moved.get("tasks") == 2

    assert db.list_tasks("old_proj") == []
    new = db.list_tasks("new_proj")
    assert {t["title"] for t in new} == {"T1", "T2"}
    # Andere Projekte unberührt
    assert len(db.list_tasks("keep_proj")) == 1


def test_task_json_round_trips_through_duckdb(tmp_path) -> None:
    db = MetadataDB(str(tmp_path / "json.duckdb"))
    spec = {
        "title": "X",
        "datasets": [{"name": "d", "install_url": "http://u"}],
        "timeline": {"start": "2024"},
    }
    task = db.create_task(
        "p", "X", spec, source_kind="url", source_url="http://kaggle.com/c/x"
    )
    got = db.get_task(task["id"])
    assert got is not None
    assert got["task_json"] == spec
    assert got["source_url"] == "http://kaggle.com/c/x"


# --------------------------------------------------------------------------- #
# Normalization helpers
# --------------------------------------------------------------------------- #


def test_normalize_task_spec_handles_partial_payload() -> None:
    spec = normalize_task_spec(
        {
            "title": " T ",
            "objective": " O ",
            "background": " Bg ",
            "deadline": "2025-12-31",
            "methodology": ["CNN", "  ", "Transformer"],
            "datasets": [{"name": "d1", "install_url": "http://x"}, "bare string"],
            "suggested_directions": [{"label": "L", "keywords": ["k"]}, "str-dir"],
            "timeline": {"start": "2024"},
            "rules": ["r1", ""],
        }
    )
    assert spec["title"] == "T"
    assert spec["background"] == "Bg"
    assert spec["deadline"] == "2025-12-31"
    assert spec["methodology"] == ["CNN", "Transformer"]  # empty dropped
    assert spec["datasets"][0]["install_url"] == "http://x"
    assert spec["datasets"][1] == {
        "name": "bare string",
        "install_url": None,
        "size": None,
        "license": None,
    }
    assert spec["suggested_directions"][1]["label"] == "str-dir"
    assert spec["timeline"] == {"start": "2024", "end": ""}
    assert spec["rules"] == ["r1"]  # empty dropped


def test_normalize_task_spec_string_payload() -> None:
    spec = normalize_task_spec('{"title":"from string"}')
    assert spec["title"] == "from string"
    assert spec["datasets"] == []
    # New fields default to empty
    assert spec["background"] == ""
    assert spec["deadline"] == ""
    assert spec["methodology"] == []


def test_normalize_task_spec_non_dict() -> None:
    spec = normalize_task_spec(42)
    assert spec == {
        "title": "",
        "objective": "",
        "background": "",
        "evaluation": "",
        "datasets": [],
        "timeline": {"start": "", "end": ""},
        "deadline": "",
        "methodology": [],
        "rules": [],
        "constraints": [],
        "inclusion_criteria": [],
        "exclusion_criteria": [],
        "suggested_directions": [],
        "extra_sections": [],
        "source_raw_text": "",
    }


def test_normalize_task_spec_missing_new_fields_default_empty() -> None:
    """A payload from an older LLM run (no background/deadline/methodology)
    must normalize cleanly — new fields default to empty, not KeyError."""
    spec = normalize_task_spec({"title": "legacy", "objective": "o"})
    assert spec["title"] == "legacy"
    assert spec["background"] == ""
    assert spec["deadline"] == ""
    assert spec["methodology"] == []


def test_extract_prompt_schema_mentions_new_fields() -> None:
    """The extractor prompt must instruct the LLM about background, deadline,
    and methodology — otherwise the new fields will never be populated."""
    from query.task_extractor import _EXTRACTOR_SCHEMA

    assert "background" in _EXTRACTOR_SCHEMA
    assert "deadline" in _EXTRACTOR_SCHEMA
    assert "methodology" in _EXTRACTOR_SCHEMA
    # Fallback instruction: if no concrete task, return empty shape
    assert "empty" in _EXTRACTOR_SCHEMA.lower()


def test_extract_prompt_schema_mentions_extra_sections() -> None:
    """The prompt must instruct the LLM to capture ALL sections via
    extra_sections — otherwise Prizes/Score/Submission File/Code-Requirements/
    Efficiency Prize etc. get silently dropped."""
    from query.task_extractor import _EXTRACTOR_SCHEMA

    assert "extra_sections" in _EXTRACTOR_SCHEMA
    # Concrete examples named so the LLM knows what belongs there
    assert "Prizes" in _EXTRACTOR_SCHEMA
    assert "Submission File" in _EXTRACTOR_SCHEMA


def test_normalize_task_spec_extra_sections_structured() -> None:
    """extra_sections: list of {label, body}. Empty label OR body dropped."""
    spec = normalize_task_spec(
        {
            "extra_sections": [
                {"label": "Prizes", "body": "$5000 for first place"},
                {"label": "Score", "body": "F1 weighted"},
                {"label": "Empty Body", "body": ""},  # dropped
                {"label": "", "body": "no label"},  # dropped
                "bare string",  # dropped (label required)
                {"label": "Code Requirements", "body": "CPU inference < 200ms"},
            ]
        }
    )
    assert spec["extra_sections"] == [
        {"label": "Prizes", "body": "$5000 for first place"},
        {"label": "Score", "body": "F1 weighted"},
        {"label": "Code Requirements", "body": "CPU inference < 200ms"},
    ]


def test_normalize_task_spec_extra_sections_missing_defaults_empty() -> None:
    """Older payloads without extra_sections must normalize to []."""
    spec = normalize_task_spec({"title": "legacy"})
    assert spec["extra_sections"] == []


def test_fallback_title_from_url() -> None:
    assert (
        _fallback_title(
            "url",
            "https://kaggle.com/competitions/rsna-knee-abnormality-detection/overview",
            None,
        )
        == "overview"
    )


def test_extract_meta_fallback_salvages_js_spa_html() -> None:
    """Kaggle/Hackathon-Seiten sind JS-SPAs: <body> ist nur <div id="root">,
    der echte Inhalt steckt in <meta>-Tags. _extract_meta_fallback muss daraus
    einen nicht-leeren Text liefern, damit der LLM etwas zum Extrahieren hat."""
    raw_html = (
        "<!DOCTYPE html><html><head>"
        '<meta name="description" content="Create a model that can detect knee abnormalities based on multimodal imaging data" />'
        '<meta property="og:title" content="RSNA Knee Abnormality Detection" />'
        '<meta property="og:description" content="og-desc fallback" />'
        "<title>RSNA Knee Abnormality Detection | Kaggle</title>"
        '</head><body><div id="root"></div></body></html>'
    )
    text = _extract_meta_fallback(raw_html)
    assert text  # not empty
    assert "RSNA Knee Abnormality Detection" in text
    # description preferred over og:description (first match wins)
    assert "knee abnormalities" in text


def test_extract_html_title_prefers_og_title() -> None:
    raw_html = (
        '<meta property="og:title" content="OG Title" />' "<title>HTML Title</title>"
    )
    assert _extract_html_title(raw_html) == "OG Title"
    # Fallback auf <title> wenn og:title fehlt
    assert _extract_html_title("<title>Only HTML Title</title>") == "Only HTML Title"
    assert _extract_html_title("") == ""


def test_fetch_url_text_uses_meta_fallback_for_empty_body(monkeypatch) -> None:
    """Wenn sanitize_web_text leer liefert (JS-SPA), muss _fetch_url_text den
    Meta-Fallback nutzen, statt stillschweigend einen leeren String zurückzugeben.
    Das war der Bug, der die Extraktion bei Kaggle-URLs immer scheitern ließ."""
    from query import task_extractor

    kaggle_html = (
        "<!DOCTYPE html><html><head>"
        '<meta name="description" content="Detect knee abnormalities from MRI scans" />'
        '<meta property="og:title" content="RSNA Knee Abnormality Detection" />'
        "<title>RSNA Knee Abnormality Detection | Kaggle</title>"
        '</head><body><div id="root"></div></body></html>'
    )

    class _FakeResponse:
        text = kaggle_html

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(task_extractor.httpx, "Client", _FakeClient)
    # is_safe_public_url muss True zurückgeben, sonst passiert gar kein Fetch
    monkeypatch.setattr(task_extractor, "is_safe_public_url", lambda url: True)
    # Dieser Test prüft den Meta-Fallback-Pfad; Headless-Render muss hier weg
    # (sonst versucht Playwright die Fake-URL wirklich zu laden).
    monkeypatch.setattr(task_extractor, "_headless_render", lambda url: "")

    text, raw_html = task_extractor._fetch_url_text(
        "https://www.kaggle.com/competitions/example/overview"
    )
    assert text  # wäre vor dem Fix leer gewesen
    assert "RSNA Knee Abnormality Detection" in text
    assert "Detect knee abnormalities" in text
    assert raw_html == kaggle_html


def test_fetch_url_text_uses_headless_render_for_js_spa(monkeypatch) -> None:
    """Bei JS-SPAs mit nur kurzen Meta-Tags muss _fetch_url_text den Headless-
    Renderer (Playwright) bemühen. Das war der Bug, der Kaggle-Competition-URLs
    auf ein leeres "Ziel" reduzierte: Server-HTML ist nur ein leeres
    ``<div id="root">`` + Meta-Tags, der Inhalt wird via XHR nachgeladen."""
    from query import task_extractor

    kaggle_html = (
        "<!DOCTYPE html><html><head>"
        '<meta name="description" content="Detect knee abnormalities" />'
        '<meta property="og:title" content="RSNA Knee Abnormality Detection" />'
        "<title>RSNA Knee Abnormality Detection | Kaggle</title>"
        '</head><body><div id="root"></div></body></html>'
    )

    rendered_text = (
        "Overview\nCreate a model that can detect knee abnormalities based on "
        "multimodal imaging data.\n\nEvaluation\nSubmissions are scored based on "
        "the mean AUC across the abnormality detection tasks.\n\nTimeline\n"
        "Start: October 1, 2020\nEnd: November 15, 2020\n\n"
        "Requirements\nEach team may submit up to 2 submissions per day."
    )

    class _FakeResponse:
        text = kaggle_html

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(task_extractor.httpx, "Client", _FakeClient)
    monkeypatch.setattr(task_extractor, "is_safe_public_url", lambda url: True)
    monkeypatch.setattr(task_extractor, "_headless_render", lambda url: rendered_text)

    text, raw_html = task_extractor._fetch_url_text(
        "https://www.kaggle.com/competitions/rsna-knee-abnormality-detection/overview"
    )
    assert "Evaluation" in text
    assert "Timeline" in text
    assert "Requirements" in text
    assert raw_html == kaggle_html


def test_fetch_url_text_skips_headless_when_static_text_substantial(
    monkeypatch,
) -> None:
    """Wenn der statische HTML-Body schon Substanz hat (>= 300 Zeichen), darf
    der Headless-Renderer nicht einmal bemüht werden."""
    from query import task_extractor

    rich_html = (
        "<!DOCTYPE html><html><head>"
        '<meta name="description" content="Detect knee abnormalities" />'
        "<title>RSNA Knee Abnormality Detection | Kaggle</title>"
        "</head><body>"
        + ("<p>Some real body content here. </p>" * 60)
        + "</body></html>"
    )

    class _FakeResponse:
        text = rich_html

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _FakeResponse()

    called = {"n": 0}

    def _never(url):
        called["n"] += 1
        return "SHOULD-NOT-BE-USED"

    monkeypatch.setattr(task_extractor.httpx, "Client", _FakeClient)
    monkeypatch.setattr(task_extractor, "is_safe_public_url", lambda url: True)
    monkeypatch.setattr(task_extractor, "_headless_render", _never)

    text, _raw = task_extractor._fetch_url_text("https://example.org/static-page")
    assert called["n"] == 0
    assert "real body content" in text


def test_fetch_url_text_falls_back_to_meta_when_headless_disabled(monkeypatch) -> None:
    """SCIENCEKG_DISABLE_HEADLESS_RENDER=1 + JS-SPA → nur Meta-Fallback,
    kein Crash."""
    from query import task_extractor

    kaggle_html = (
        "<!DOCTYPE html><html><head>"
        '<meta name="description" content="Detect knee abnormalities" />'
        '<meta property="og:title" content="RSNA Knee Abnormality Detection" />'
        "<title>RSNA Knee Abnormality Detection | Kaggle</title>"
        '</head><body><div id="root"></div></body></html>'
    )

    class _FakeResponse:
        text = kaggle_html

        def raise_for_status(self):
            pass

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, url):
            return _FakeResponse()

    monkeypatch.setattr(task_extractor.httpx, "Client", _FakeClient)
    monkeypatch.setattr(task_extractor, "is_safe_public_url", lambda url: True)
    monkeypatch.setenv("SCIENCEKG_DISABLE_HEADLESS_RENDER", "1")

    text, _raw = task_extractor._fetch_url_text(
        "https://www.kaggle.com/competitions/example/overview"
    )
    assert "RSNA Knee Abnormality Detection" in text
    assert "Detect knee abnormalities" in text


def test_normalize_directions_structured_and_bare() -> None:
    d = normalize_directions(
        {
            "directions": [
                {"label": "A", "keywords": ["k1", "k2"], "novelty": "cross-domain"},
                "B",
            ]
        }
    )
    assert d["directions"][0]["novelty"] == "cross-domain"
    assert d["directions"][1] == {
        "label": "B",
        "rationale": "",
        "keywords": [],
        "novelty": "exploratory",
    }


def test_normalize_plan_strips_bare_citations() -> None:
    plan = normalize_plan(
        {
            "variant_label": "V1",
            "steps": [
                {
                    "label": "s1",
                    "citations": [
                        "[arxiv:1234.5]",
                        "[1]",
                        "grey::task_abc",
                        "bare text",
                    ],
                }
            ],
            "risks": ["r1"],
        }
    )
    cites = plan["steps"][0]["citations"]
    assert cites == [
        "[arxiv:1234.5]",
        "grey::task_abc",
    ]  # bare [1] and "bare text" dropped
    assert plan["variant_label"] == "V1"


# --------------------------------------------------------------------------- #
# Fake LLM + API client
# --------------------------------------------------------------------------- #


class _FakeLLM:
    """Returns a fixed JSON dict for chat_json; chat returns it as a string."""

    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, messages, provider=None, overrides=None):
        self.calls.append(
            {"messages": messages, "provider": provider, "overrides": overrides}
        )
        return self._payload

    def chat(self, messages, provider=None, overrides=None):
        return json.dumps(self._payload)


@pytest.fixture()
def client(monkeypatch, tmp_path) -> TestClient:
    monkeypatch.setattr(
        product_main, "llm_router", _FakeLLM({"title": "X", "objective": "o"})
    )
    # Use a temp DB so tests don't touch real data.
    db_path = str(tmp_path / "api.duckdb")
    return TestClient(product_main.app), db_path


def test_tasks_crud_api(client) -> None:
    api, db_path = client
    r = api.post(
        "/projects/proj1/tasks",
        json={
            "title": "Kaggle Test",
            "task_json": {"objective": "predict knee"},
            "source_kind": "text",
            "metadata_db_path": db_path,
        },
    )
    assert r.status_code == 200, r.text
    tid = r.json()["id"]
    assert r.json()["title"] == "Kaggle Test"

    r = api.get(f"/projects/proj1/tasks?metadata_db_path={db_path}")
    assert r.status_code == 200
    assert len(r.json()["tasks"]) == 1

    r = api.get(f"/tasks/{tid}?metadata_db_path={db_path}")
    assert r.status_code == 200 and r.json()["id"] == tid

    r = api.patch(
        f"/tasks/{tid}",
        json={
            "title": "Renamed",
            "task_json": {"objective": "x2"},
            "metadata_db_path": db_path,
        },
    )
    assert r.status_code == 200 and r.json()["title"] == "Renamed"

    r = api.delete(f"/tasks/{tid}?metadata_db_path={db_path}")
    assert r.status_code == 200 and r.json()["deleted"] is True

    r = api.get(f"/tasks/{tid}?metadata_db_path={db_path}")
    assert r.status_code == 404


def test_extract_endpoint_uses_llm(client) -> None:
    api, db_path = client
    # FakeLLM returns {"title":"X","objective":"o"} → normalize to spec
    r = api.post(
        "/tasks/extract",
        json={"source_kind": "text", "source_text": "Some competition"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "X"
    assert body["objective"] == "o"


def test_ingest_endpoint_extracts_and_saves(client, monkeypatch, tmp_path) -> None:
    api, db_path = client
    # Replace LLM with a richer spec for this test
    monkeypatch.setattr(
        product_main,
        "llm_router",
        _FakeLLM(
            {
                "title": "RSNA Knee",
                "objective": "Detect abnormalities",
                "evaluation": "mAP",
                "datasets": [{"name": "train", "install_url": "http://kaggle.com/d"}],
                "suggested_directions": [{"label": "CNN", "keywords": ["cnn", "mri"]}],
                "timeline": {"start": "2024"},
            }
        ),
    )
    r = api.post(
        f"/projects/proj1/tasks/ingest?metadata_db_path={db_path}",
        json={
            "source_kind": "text",
            "source_text": "Kaggle text",
            "title": "Custom",
            "metadata_db_path": db_path,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    tid = body["id"]
    assert body["title"] == "RSNA Knee"  # LLM-extracted title wins over supplied
    assert len(body["task_json"]["suggested_directions"]) == 1

    # as-grey-source
    r = api.post(f"/tasks/{tid}/as-grey-source", json={"metadata_db_path": db_path})
    assert r.status_code == 200
    assert r.json()["citation"] == f"grey::{tid}"
    assert r.json()["grey_source"]["source_kind"] == "task"

    # grey-source is listable
    r = api.get(f"/projects/proj1/grey-sources?metadata_db_path={db_path}")
    gs = r.json()["grey_sources"]
    assert any(g.get("source_kind") == "task" for g in gs)

    # cleanup
    api.delete(f"/tasks/{tid}?metadata_db_path={db_path}")
    for g in gs:
        api.delete(f"/grey-sources/{g['id']}?metadata_db_path={db_path}")


def test_suggest_directions_endpoint(client, monkeypatch) -> None:
    api, db_path = client
    # First create a task to operate on
    api.post(
        "/projects/proj1/tasks",
        json={
            "title": "T",
            "task_json": {"objective": "o"},
            "metadata_db_path": db_path,
        },
    )
    tasks = api.get(f"/projects/proj1/tasks?metadata_db_path={db_path}").json()["tasks"]
    tid = tasks[0]["id"]
    # FakeLLM returns structured directions
    monkeypatch.setattr(
        product_main,
        "llm_router",
        _FakeLLM(
            {
                "directions": [
                    {"label": "MRI CNN", "keywords": ["cnn"], "novelty": "exploratory"}
                ],
            }
        ),
    )
    r = api.post(
        f"/tasks/{tid}/suggest-directions",
        json={"creativity_level": 4, "metadata_db_path": db_path},
    )
    assert r.status_code == 200, r.text
    dirs = r.json()["directions"]
    assert len(dirs) == 1 and dirs[0]["label"] == "MRI CNN"
    api.delete(f"/tasks/{tid}?metadata_db_path={db_path}")


def test_plan_endpoint_strips_bare_citations(client, monkeypatch) -> None:
    api, db_path = client
    api.post(
        "/projects/proj1/tasks",
        json={
            "title": "T",
            "task_json": {"objective": "o"},
            "metadata_db_path": db_path,
        },
    )
    tasks = api.get(f"/projects/proj1/tasks?metadata_db_path={db_path}").json()["tasks"]
    tid = tasks[0]["id"]
    monkeypatch.setattr(
        product_main,
        "llm_router",
        _FakeLLM(
            {
                "variant_label": "V1",
                "steps": [
                    {
                        "label": "s1",
                        "citations": ["[arxiv:1234.5]", "[1]"],
                        "effort": "medium",
                    }
                ],
                "risks": ["r1"],
                "differentiator": "diff",
            }
        ),
    )
    r = api.post(
        f"/tasks/{tid}/plan",
        json={
            "direction": {"label": "D"},
            "kg_context": "snippet",
            "metadata_db_path": db_path,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["steps"][0]["citations"] == ["[arxiv:1234.5]"]
    api.delete(f"/tasks/{tid}?metadata_db_path={db_path}")


def test_research_clarify_with_task_id_returns_structured(client, monkeypatch) -> None:
    api, db_path = client
    api.post(
        "/projects/proj1/tasks",
        json={
            "title": "T",
            "task_json": {"objective": "o", "suggested_directions": [{"label": "CNN"}]},
            "metadata_db_path": db_path,
        },
    )
    tasks = api.get(f"/projects/proj1/tasks?metadata_db_path={db_path}").json()["tasks"]
    tid = tasks[0]["id"]
    monkeypatch.setattr(
        product_main,
        "llm_router",
        _FakeLLM(
            {
                "directions": [
                    {"label": "MRI CNN", "keywords": ["cnn"], "novelty": "cross-domain"}
                ],
            }
        ),
    )
    r = api.post(
        "/research/clarify",
        json={
            "question": "how to detect knee?",
            "task_id": tid,
            "metadata_db_path": db_path,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body.get("task_id") == tid
    assert "directions_structured" in body
    assert body["directions"][0] == "MRI CNN"
    api.delete(f"/tasks/{tid}?metadata_db_path={db_path}")
