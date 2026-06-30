"""Tests for the desktop-agent hand-off: task-brief compiler + product API endpoints.

The compiler is pure text-in/text-out (no vision); the LLM and retrieval are mocked, so
these run fully offline and deterministically."""
from __future__ import annotations

import api.product_main as product_main
from fastapi.testclient import TestClient

from query import agent_handoff
from storage.metadata_db import MetadataDB


# --------------------------------------------------------------------------- #
# Task-brief compiler                                                          #
# --------------------------------------------------------------------------- #

class _BriefRouter:
    default_provider = "fake"

    def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
        return {
            "goal": "Eine App bauen",
            "context": "Hintergrund.",
            "steps": ["Editor öffnen", "Code schreiben", "Editor öffnen"],  # dup is dropped
            "constraints": ["nichts löschen"],
            "success_criteria": ["App startet"],
            "artifacts": ["app.py"],
        }


class _RaisingRouter:
    def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
        raise RuntimeError("llm down")


def _variant(**over):
    base = {
        "name": "Variante 1",
        "approach": "Tu A",
        "rationale": "Weil A",
        "suggested_prompt": "1. Schritt eins\n2. Schritt zwei",
    }
    base.update(over)
    return base


def test_build_task_brief_uses_llm_output() -> None:
    brief = agent_handoff.build_task_brief(_variant(), question="Frage", llm_router=_BriefRouter())
    assert brief["goal"] == "Eine App bauen"
    # Steps are cleaned + deduped.
    assert brief["steps"] == ["Editor öffnen", "Code schreiben"]
    assert brief["constraints"] == ["nichts löschen"]
    assert brief["artifacts"] == ["app.py"]
    assert brief["raw_prompt"].startswith("1. Schritt eins")


def test_build_task_brief_fallback_without_router() -> None:
    brief = agent_handoff.build_task_brief(_variant(), question="Frage", llm_router=None)
    # Deterministic fallback parses numbered steps from the suggested_prompt.
    assert brief["steps"] == ["Schritt eins", "Schritt zwei"]
    assert "Variante 1" in brief["goal"]


def test_build_task_brief_falls_back_on_llm_error() -> None:
    brief = agent_handoff.build_task_brief(_variant(), question="Frage", llm_router=_RaisingRouter())
    assert brief["steps"] == ["Schritt eins", "Schritt zwei"]


def test_build_task_brief_falls_back_on_empty_llm() -> None:
    class _Empty:
        def chat_json(self, messages, provider=None, overrides=None):  # noqa: ARG002
            return {"goal": "", "steps": []}

    brief = agent_handoff.build_task_brief(_variant(), question="Frage", llm_router=_Empty())
    assert brief["steps"] == ["Schritt eins", "Schritt zwei"]


def test_steps_from_prompt_handles_unstructured_text() -> None:
    assert agent_handoff._steps_from_prompt("nur ein fließtext ohne liste") == [
        "nur ein fließtext ohne liste"
    ]
    assert agent_handoff._steps_from_prompt("") == []


def test_render_task_brief_text_structure() -> None:
    brief = agent_handoff.build_task_brief(_variant(), question="Frage", llm_router=_BriefRouter())
    text = agent_handoff.render_task_brief_text(brief)
    assert text.startswith("Ziel: Eine App bauen")
    assert "Schritte:" in text
    assert "1. Editor öffnen" in text
    assert "Rahmenbedingungen:" in text
    assert "- nichts löschen" in text
    assert "Erfolgskriterien:" in text


# --------------------------------------------------------------------------- #
# Product API endpoints                                                        #
# --------------------------------------------------------------------------- #

def test_handoff_endpoint_and_agent_config(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(product_main, "llm_router", _BriefRouter())
    client = TestClient(product_main.app)
    db_path = str(tmp_path / "h.duckdb")

    with MetadataDB(db_path) as db:
        session = db.create_parallel_session("proj", "Wie X bauen?")
        variant = db.add_parallel_variant(
            session["id"], "Variante 1", approach="A", rationale="R",
            suggested_prompt="1. tu dies\n2. tu das", origin="ai",
        )

    res = client.post(
        f"/parallel/variants/{variant['id']}/handoff",
        json={"with_research_context": False, "metadata_db_path": db_path,
              "graph_db_path": str(tmp_path / "g")},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["brief"]["goal"] == "Eine App bauen"
    assert body["text"].startswith("Ziel: Eine App bauen")
    assert "enabled" in body["bridge"]

    # Unknown variant → 404.
    assert client.post(
        "/parallel/variants/does-not-exist/handoff",
        json={"with_research_context": False, "metadata_db_path": db_path},
    ).status_code == 404

    # Agent config reads config.yaml (bridge ships disabled by default).
    cfg = client.get("/agent/config").json()
    assert cfg["enabled"] is False
    assert "type" in cfg


def test_dispatch_disabled_emits_error_event(tmp_path, monkeypatch) -> None:
    """With the bridge off and no override, dispatch must degrade to a single error event."""
    monkeypatch.setattr(product_main, "_AGENT_BRIDGE_CONFIG_CACHE", {"enabled": False})
    client = TestClient(product_main.app)
    res = client.post("/agent/dispatch", json={"task": "tu etwas"})
    assert res.status_code == 200
    assert "data:" in res.text
    assert "error" in res.text
