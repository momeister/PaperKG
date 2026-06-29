"""Tests for the optional bearer-token auth middleware on the product API."""
from __future__ import annotations

from fastapi.testclient import TestClient

from api import product_main


def test_no_token_means_open(monkeypatch) -> None:
    monkeypatch.delenv("SCIENCEKG_API_TOKEN", raising=False)
    client = TestClient(product_main.app)
    assert client.get("/health").status_code == 200
    # An arbitrary GET should not be blocked when no token is configured.
    assert client.get("/models/providers").status_code == 200


def test_token_required_when_set(monkeypatch) -> None:
    monkeypatch.setenv("SCIENCEKG_API_TOKEN", "s3cret-token")
    client = TestClient(product_main.app)

    # Health stays open so container/orchestrator probes keep working.
    assert client.get("/health").status_code == 200

    # No header -> 401.
    assert client.get("/models/providers").status_code == 401
    # Wrong token -> 401.
    assert client.get("/models/providers", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # Correct token -> passes auth (status is whatever the route returns, not 401).
    ok = client.get("/models/providers", headers={"Authorization": "Bearer s3cret-token"})
    assert ok.status_code != 401
