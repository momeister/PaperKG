from __future__ import annotations

from fastapi.testclient import TestClient

import api.product_main as pm
from api.product_main import app


class _FakeRouter:
    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.calls: list[dict] = []

    def chat(self, messages, provider=None, overrides=None) -> str:
        self.calls.append(
            {"messages": messages, "provider": provider, "overrides": overrides}
        )
        return self.reply

    def provider_default_model(self, provider=None) -> str:
        return "fake-model"


def _client_with(reply: str) -> tuple[TestClient, _FakeRouter]:
    fake = _FakeRouter(reply)
    pm.llm_router = fake  # type: ignore[assignment]
    return TestClient(app), fake


def test_rewrite_strips_chain_of_thought_preamble() -> None:
    reply = (
        "Vielleicht ist die Quelle eine andere? Die Quelle ist crossref:10.2147/cmar.s39306. "
        "Ich kenne diese Quelle nicht. Die umformulierte Aussage lautet: "
        "Bevacizumab kann das Überleben bei Glioblastom verbessern."
    )
    client, fake = _client_with(reply)
    resp = client.post(
        "/tools/rewrite",
        json={
            "text": "Bevacizumab hilft bei Glioblastom.",
            "instruction": "Umformulieren.",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    text = body["text"]
    # Preamble (Vielleicht…Ich kenne…) must be gone; the actual rewrite stays.
    assert "Vielleicht" not in text
    assert "Ich kenne" not in text
    assert "Bevacizumab kann das Überleben" in text


def test_rewrite_keeps_clean_response_without_preamble() -> None:
    reply = "Bevacizumab kann das Überleben bei Glioblastom verbessern."
    client, _ = _client_with(reply)
    resp = client.post(
        "/tools/rewrite",
        json={"text": "Bevacizumab hilft.", "instruction": "Umformulieren."},
    )
    assert resp.status_code == 200
    assert resp.json()["text"] == reply


def test_rewrite_hardened_system_prompt_forbids_preamble() -> None:
    client, fake = _client_with("ok")
    client.post(
        "/tools/rewrite",
        json={"text": "Test.", "instruction": "Umformulieren."},
    )
    system = fake.calls[0]["messages"][0]["content"]
    assert "AUSSCHLIESSLICH" in system
    assert "kein Einleitungssatz" in system.lower() or "kein" in system.lower()
