"""Tests for companion/selfdrive session persistence (DuckDB) + session endpoints
+ the /selfdrive lookup/answer loop. Offline: LLM router faked, DB on tmp_path."""
from __future__ import annotations

import base64
import io
import json
from typing import Any

import api.product_main as product_main
from fastapi.testclient import TestClient

from query import self_drive
from storage.metadata_db import MetadataDB


def _png_b64(width: int, height: int) -> str:
    from PIL import Image

    image = Image.new("RGB", (width, height), (20, 20, 20))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return base64.b64encode(buffer.getvalue()).decode("ascii")


class _FakeRouter:
    """Scripted router: pops one reply per chat() call (last reply repeats)."""

    default_provider = "lm_studio"

    def __init__(self, *replies: str) -> None:
        self.replies = list(replies)
        self.calls: list[dict[str, Any]] = []

    def chat(self, messages, provider=None, overrides=None):
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides or {}})
        if len(self.replies) > 1:
            return self.replies.pop(0)
        return self.replies[0]

    def available_providers(self):
        return ["lm_studio"]

    def provider_default_model(self, provider=None):  # noqa: ARG002
        return "qwen/qwen3-vl-8b"

    def provider_model_options(self, provider=None, refresh=False):  # noqa: ARG002
        return ["qwen/qwen3-vl-8b"]


# --------------------------------------------------------------------------- #
# CompanionMixin CRUD                                                          #
# --------------------------------------------------------------------------- #

def test_companion_session_crud_roundtrip(tmp_path) -> None:
    db_path = str(tmp_path / "meta.duckdb")
    with MetadataDB(db_path) as db:
        session = db.create_companion_session("companion", title="Erster Chat")
        sid = session["id"]
        assert session["kind"] == "companion"
        assert session["status"] == "active"
        assert session["messages"] == []

        db.add_companion_message(sid, "user", "Wo ist der Speichern-Knopf?")
        db.add_companion_message(
            sid, "assistant", "Oben links.", {"steps": [{"x": 10, "y": 20, "label": "Speichern"}]}
        )
        detail = db.get_companion_session(sid)
        assert len(detail["messages"]) == 2
        assert detail["messages"][1]["payload"]["steps"][0]["label"] == "Speichern"

        summaries = db.list_companion_sessions(kind="companion")
        assert summaries[0]["id"] == sid
        assert summaries[0]["message_count"] == 2
        assert "messages" not in summaries[0]

        renamed = db.update_companion_session(sid, title="Umbenannt", status="done")
        assert renamed["title"] == "Umbenannt"
        assert renamed["status"] == "done"

        assert db.delete_companion_session(sid) is True
        assert db.get_companion_session(sid) is None
        assert db.list_companion_messages(sid) == []


def test_companion_session_kind_filter(tmp_path) -> None:
    db_path = str(tmp_path / "meta.duckdb")
    with MetadataDB(db_path) as db:
        db.create_companion_session("companion", title="Chat")
        db.create_companion_session("selfdrive", goal="Öffne Editor")
        assert len(db.list_companion_sessions()) == 2
        assert len(db.list_companion_sessions(kind="selfdrive")) == 1
        assert db.list_companion_sessions(kind="selfdrive")[0]["goal"] == "Öffne Editor"


# --------------------------------------------------------------------------- #
# Session CRUD endpoints                                                       #
# --------------------------------------------------------------------------- #

def _client(monkeypatch, router, companion_cfg: dict | None = None) -> TestClient:
    monkeypatch.setattr(product_main, "llm_router", router)
    monkeypatch.setattr(
        product_main,
        "_COMPANION_CONFIG_CACHE",
        companion_cfg
        or {"provider": "lm_studio", "model": "qwen/qwen3-vl-8b", "history_turns": 8},
    )
    monkeypatch.setattr(product_main, "_SELF_DRIVE_STORE", self_drive.SelfDriveStore())
    return TestClient(product_main.app)


def test_session_endpoints_crud(monkeypatch, tmp_path) -> None:
    db_path = str(tmp_path / "meta.duckdb")
    client = _client(monkeypatch, _FakeRouter("{}"))

    created = client.post(
        "/companion/sessions",
        json={"kind": "companion", "title": "Chat 1", "metadata_db_path": db_path},
    ).json()
    sid = created["id"]
    assert created["kind"] == "companion"

    listed = client.get("/companion/sessions", params={"metadata_db_path": db_path}).json()
    assert [s["id"] for s in listed["sessions"]] == [sid]

    detail = client.get(f"/companion/sessions/{sid}", params={"metadata_db_path": db_path}).json()
    assert detail["id"] == sid

    patched = client.patch(
        f"/companion/sessions/{sid}",
        json={"title": "Neu", "metadata_db_path": db_path},
    ).json()
    assert patched["title"] == "Neu"

    # Maus-Ruck-Not-Aus / Loop-Stop persistiert den Status auf der Session.
    stopped = client.patch(
        f"/companion/sessions/{sid}",
        json={"status": "stopped", "metadata_db_path": db_path},
    ).json()
    assert stopped["status"] == "stopped"
    detail = client.get(f"/companion/sessions/{sid}", params={"metadata_db_path": db_path}).json()
    assert detail["status"] == "stopped"

    deleted = client.delete(
        f"/companion/sessions/{sid}", params={"metadata_db_path": db_path}
    ).json()
    assert deleted["deleted"] is True
    missing = client.get(f"/companion/sessions/{sid}", params={"metadata_db_path": db_path}).json()
    assert "error" in missing


def test_companion_ask_persists_transcript(monkeypatch, tmp_path) -> None:
    db_path = str(tmp_path / "meta.duckdb")
    client = _client(monkeypatch, _FakeRouter("Die Antwort."))
    sid = client.post(
        "/companion/sessions", json={"kind": "companion", "metadata_db_path": db_path}
    ).json()["id"]

    res = client.post(
        "/companion/ask",
        json={"question": "Was ist das?", "session_id": sid, "metadata_db_path": db_path},
    )
    assert res.json()["answer"] == "Die Antwort."
    with MetadataDB(db_path) as db:
        messages = db.list_companion_messages(sid)
    assert [(m["role"], m["content"]) for m in messages] == [
        ("user", "Was ist das?"),
        ("assistant", "Die Antwort."),
    ]


# --------------------------------------------------------------------------- #
# /selfdrive: lookup loop, answer, persistence                                 #
# --------------------------------------------------------------------------- #

_SD_CFG = {
    "provider": "lm_studio",
    "self_drive": {
        "enabled": True,
        "max_steps": 5,
        "autopilot": True,
        "lookup": {"enabled": True, "use_web": True, "max_per_session": 3},
    },
}


def test_selfdrive_lookup_loop_resolves_and_replans(monkeypatch, tmp_path) -> None:
    db_path = str(tmp_path / "meta.duckdb")
    lookup_reply = json.dumps(
        {"thought": "Mir fehlt Wissen.", "action": {"type": "lookup", "query": "GIMP Ebenen öffnen"}, "done": False}
    )
    click_reply = json.dumps(
        {
            "thought": "Jetzt weiß ich es.",
            "action": {"type": "click", "x": 100, "y": 100, "label": "Fenster-Menü"},
            "expectation": "Menü öffnet sich",
            "done": False,
        }
    )
    router = _FakeRouter(lookup_reply, click_reply)
    client = _client(monkeypatch, router, _SD_CFG)

    async def _fake_context(question, use_papers, use_web):  # noqa: ARG001
        return ["(Web: example.org) Anleitung — Fenster > Ebenen"], [
            {"type": "web", "url": "https://example.org", "title": "Anleitung"}
        ]

    monkeypatch.setattr(product_main, "_companion_context", _fake_context)

    sid = client.post(
        "/companion/sessions", json={"kind": "selfdrive", "metadata_db_path": db_path}
    ).json()["id"]
    started = client.post(
        "/selfdrive/start",
        json={"goal": "Öffne die Ebenen", "session_id": sid, "metadata_db_path": db_path},
    ).json()
    assert started["autopilot"] is True

    step = client.post(
        "/selfdrive/step",
        json={
            "session_id": started["session_id"],
            "image_base64": _png_b64(500, 500),
            "metadata_db_path": db_path,
        },
    ).json()
    # The lookup was resolved server-side; the client only sees the re-planned click.
    assert step["action"]["type"] == "click"
    assert len(router.calls) == 2

    # History got the research injected between the two plans.
    session = product_main._SELF_DRIVE_STORE.get(started["session_id"])
    assert any("Rechercheergebnis" in m["content"] for m in session.history)
    assert session.lookup_count == 1

    # Durable transcript: goal + research + step.
    with MetadataDB(db_path) as db:
        messages = db.list_companion_messages(sid)
    roles = [m["role"] for m in messages]
    assert roles == ["user", "system", "action"]
    assert messages[2]["payload"]["action"]["type"] == "click"


def test_selfdrive_answer_endpoint(monkeypatch, tmp_path) -> None:
    db_path = str(tmp_path / "meta.duckdb")
    click_reply = json.dumps(
        {"thought": "ok", "action": {"type": "click", "x": 10, "y": 10, "label": "X"}, "done": False}
    )
    client = _client(monkeypatch, _FakeRouter(click_reply), _SD_CFG)
    started = client.post("/selfdrive/start", json={"goal": "Ziel"}).json()

    res = client.post(
        "/selfdrive/answer",
        json={"session_id": started["session_id"], "answer": "Nimm den zweiten Eintrag."},
    ).json()
    assert res == {"ok": True}
    session = product_main._SELF_DRIVE_STORE.get(started["session_id"])
    assert session.history[-1]["content"] == "Antwort des Nutzers: Nimm den zweiten Eintrag."

    unknown = client.post("/selfdrive/answer", json={"session_id": "nope", "answer": "x"}).json()
    assert "error" in unknown


def test_companion_config_exposes_loop_settings(monkeypatch) -> None:
    client = _client(monkeypatch, _FakeRouter("{}"), _SD_CFG)
    cfg = client.get("/companion/config").json()
    assert cfg["self_drive"]["enabled"] is True
    assert cfg["self_drive"]["autopilot"] is True
    assert cfg["self_drive"]["max_steps"] == 5
    assert cfg["guide"]["max_steps"] == 10
    assert cfg["guide"]["click_settle_ms"] == 700
