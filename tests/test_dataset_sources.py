"""Tests für die neuen Dataset-Quellen (Session 2): Kaggle, HF, OpenML, UCI, Mendeley.

Diese Tests laufen offline — sie mocken ``httpx.AsyncClient`` und prüfen, dass
die Clients die richtige API-Form ansprechen und fail-soft auf Fehler reagieren.
Kein echter Netzwerk-Call.
"""

from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest


def _mock_response(
    status: int = 200, json_data: Any = None, text: str = ""
) -> MagicMock:
    resp = MagicMock(spec=httpx.Response)
    resp.status_code = status
    resp.json.return_value = json_data if json_data is not None else {}
    resp.text = text
    resp.raise_for_status = MagicMock()
    if status >= 400:
        resp.raise_for_status.side_effect = httpx.HTTPStatusError(
            "err", request=MagicMock(), response=resp
        )
    return resp


@pytest.mark.asyncio
async def test_kaggle_search_competitions_no_auth(monkeypatch):
    """Ohne Credentials: Warning, aber kein Wurf."""
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    from harvester import kaggle_client

    # Mock: erster Call 401, Retry ohne Auth liefert Liste.
    list_resp_401 = _mock_response(401)
    list_resp_ok = _mock_response(
        200,
        [
            {
                "ref": "rsna-knee",
                "title": "RSNA Knee",
                "description": "knee MRI",
                "deadline": "2024-01-01",
                "category": "Medical",
                "reward": "$10000",
                "evaluationMetric": "AUC",
                "isKernelsSubmittable": True,
                "id": 123,
            }
        ],
    )
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(side_effect=[list_resp_401, list_resp_ok])
    with patch("httpx.AsyncClient", return_value=_async_cm(client_mock)):
        result = await kaggle_client.search_competitions("knee")
    assert result["results"], "sollte Treffer haben"
    assert result["results"][0]["source"] == "kaggle_competition"
    assert result["results"][0]["external_id"] == "rsna-knee"
    assert (
        result["results"][0]["url"] == "https://www.kaggle.com/competitions/rsna-knee"
    )
    assert any("Login" in w or "Auth" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_kaggle_search_competitions_with_auth(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "tester")
    monkeypatch.setenv("KAGGLE_KEY", "secretkey")
    from harvester import kaggle_client

    list_resp = _mock_response(
        200,
        [{"ref": "comp1", "title": "Comp One", "description": "d", "id": 1}],
    )
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=list_resp)
    with patch("httpx.AsyncClient", return_value=_async_cm(client_mock)):
        result = await kaggle_client.search_competitions("comp")
    assert len(result["results"]) == 1
    assert not any("Login" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_kaggle_list_competition_files_no_auth(monkeypatch):
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    from harvester import kaggle_client

    result = await kaggle_client.list_competition_files("comp-ref")
    assert result["files"] == []
    assert "Login" in result["warning"]


@pytest.mark.asyncio
async def test_kaggle_search_datasets_with_auth(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "tester")
    monkeypatch.setenv("KAGGLE_KEY", "secretkey")
    from harvester import kaggle_client

    list_resp = _mock_response(
        200,
        [
            {
                "id": 42,
                "ownerRef": "user",
                "ref": "my-dataset",
                "title": "My Data",
                "description": "desc",
                "totalBytes": 1048576,
                "lastUpdated": "2023-05-01",
                "licenseName": "CC0",
                "downloadCount": 100,
            }
        ],
    )
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=list_resp)
    with patch("httpx.AsyncClient", return_value=_async_cm(client_mock)):
        result = await kaggle_client.search_datasets("data")
    assert result["results"][0]["source"] == "kaggle_dataset"
    assert result["results"][0]["external_id"] == "42"
    assert (
        result["results"][0]["url"] == "https://www.kaggle.com/datasets/user/my-dataset"
    )
    assert result["results"][0]["size"] == "1.0 MB"


@pytest.mark.asyncio
async def test_kaggle_status_no_auth(monkeypatch):
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    from harvester import kaggle_client

    status = await kaggle_client.kaggle_status()
    assert status["authenticated"] is False
    assert status["hint"] is not None


@pytest.mark.asyncio
async def test_kaggle_status_with_auth(monkeypatch):
    monkeypatch.setenv("KAGGLE_USERNAME", "tester")
    monkeypatch.setenv("KAGGLE_KEY", "secretkey")
    from harvester import kaggle_client

    status = await kaggle_client.kaggle_status()
    assert status["authenticated"] is True
    assert status["username"] == "tester"


@pytest.mark.asyncio
async def test_hf_search_datasets(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    from harvester import huggingface_datasets_client as hf

    resp = _mock_response(
        200,
        [
            {
                "id": "imdb",
                "description": "movie reviews",
                "cardData": {"license": "MIT"},
                "lastModified": "2023-01-01",
                "downloads": 1000,
                "likes": 50,
                "tags": ["nlp"],
                "private": False,
                "gated": False,
            }
        ],
    )
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp)
    with patch("httpx.AsyncClient", return_value=_async_cm(client_mock)):
        result = await hf.search_datasets("imdb")
    assert result["results"][0]["source"] == "huggingface"
    assert result["results"][0]["external_id"] == "imdb"
    assert result["results"][0]["url"] == "https://huggingface.co/datasets/imdb"
    assert result["results"][0]["license"] == "MIT"
    assert any("HF_TOKEN" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_hf_status_with_token(monkeypatch):
    monkeypatch.setenv("HF_TOKEN", "hf_xyz")
    from harvester import huggingface_datasets_client as hf

    status = await hf.hf_status()
    assert status["authenticated"] is True


@pytest.mark.asyncio
async def test_openml_search(monkeypatch):
    from harvester import openml_client

    resp = _mock_response(
        200,
        {
            "data": {
                "dataset": [
                    {
                        "did": 1,
                        "name": "iris",
                        "description": "iris set",
                        "format": "ARFF",
                        "size": 1024,
                        "upload_date": "2020-01-01",
                    }
                ]
            }
        },
    )
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp)
    with patch("httpx.AsyncClient", return_value=_async_cm(client_mock)):
        result = await openml_client.search_datasets("iris")
    assert result["results"][0]["source"] == "openml"
    assert result["results"][0]["external_id"] == "1"


@pytest.mark.asyncio
async def test_uci_search(monkeypatch):
    from harvester import uci_client

    resp = _mock_response(
        200,
        {
            "datasets": [
                {
                    "uci_id": 53,
                    "name": "Iris",
                    "slug": "iris",
                    "abstract": "iris flowers",
                    "size_kb": 5,
                    "instances": 150,
                    "features": 4,
                    "area": "Biology",
                    "creators_donated": "1988-01-01",
                }
            ]
        },
    )
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp)
    with patch("httpx.AsyncClient", return_value=_async_cm(client_mock)):
        result = await uci_client.search_datasets("iris")
    assert result["results"][0]["source"] == "uci"
    assert result["results"][0]["external_id"] == "53"


@pytest.mark.asyncio
async def test_mendeley_search(monkeypatch):
    from harvester import mendeley_client

    resp = _mock_response(
        200,
        [
            {
                "id": "abc123",
                "title": "Data Set",
                "description": "desc",
                "doi": "10.1234/abc",
                "created": "2022-03-01",
                "license": "CC-BY",
            }
        ],
    )
    client_mock = AsyncMock()
    client_mock.get = AsyncMock(return_value=resp)
    with patch("httpx.AsyncClient", return_value=_async_cm(client_mock)):
        result = await mendeley_client.search_datasets("data")
    assert result["results"][0]["source"] == "mendeley"
    assert result["results"][0]["doi"] == "10.1234/abc"


def test_dataset_sources_catalog_has_new_entries():
    """DATASET_SOURCES enthält alle neuen Quellen."""
    from harvester import dataset_clients

    ids = {s["id"] for s in dataset_clients.DATASET_SOURCES}
    for expected in (
        "kaggle_competition",
        "kaggle_dataset",
        "huggingface",
        "openml",
        "uci",
        "mendeley",
    ):
        assert expected in ids, f"{expected} fehlt in DATASET_SOURCES"


@pytest.mark.asyncio
async def test_dataset_clients_search_dispatches_modular(monkeypatch):
    """search_datasets ruft die Modul-Fetcher auf und sammelt Ergebnisse."""
    from harvester import dataset_clients

    async def fake_hf(query, limit):
        return {
            "results": [{"source": "huggingface", "external_id": "x", "title": "T"}],
            "warnings": [],
        }

    async def fake_openml(query, limit):
        return {
            "results": [{"source": "openml", "external_id": "y", "title": "U"}],
            "warnings": [],
        }

    monkeypatch.setitem(dataset_clients._MODULE_FETCHERS, "huggingface", fake_hf)
    monkeypatch.setitem(dataset_clients._MODULE_FETCHERS, "openml", fake_openml)
    result = await dataset_clients.search_datasets(
        "test", sources=["huggingface", "openml"]
    )
    sources = {r["source"] for r in result["results"]}
    assert "huggingface" in sources
    assert "openml" in sources


@pytest.mark.asyncio
async def test_dataset_clients_download_kaggle_competition(monkeypatch):
    """download_dataset dispatcht an kaggle_client.download_competition_file."""
    from harvester import dataset_clients, kaggle_client

    async def fake_download(ref, fname, dest, timeout):
        return {"ok": True, "path": dest, "size_bytes": 100}

    monkeypatch.setattr(kaggle_client, "download_competition_file", fake_download)
    result = await dataset_clients.download_dataset(
        "kaggle_competition", "comp-ref", "/tmp/out.zip", file_name="train.csv"
    )
    assert result["ok"] is True


@pytest.mark.asyncio
async def test_dataset_clients_download_unsupported(monkeypatch):
    from harvester import dataset_clients

    result = await dataset_clients.download_dataset("zenodo", "123", "/tmp/x")
    assert result["ok"] is False
    assert "nicht unterstützt" in result["warning"]


def test_settings_kaggle_login_status_no_credentials(monkeypatch):
    """GET /settings/kaggle ohne Credentials → authenticated False."""
    from api.routers import settings

    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)
    # asyncio.run weil Endpoint async
    result = asyncio.run(settings.kaggle_login_status())
    assert result["authenticated"] is False


def test_settings_kaggle_login_writes_env(tmp_path, monkeypatch):
    """POST /settings/kaggle schreibt username+key in .env."""
    from api.routers import settings

    env_file = tmp_path / ".env"
    env_file.write_text("EXISTING=1\n", encoding="utf-8")
    monkeypatch.setattr(settings, "_ENV_PATH", env_file)
    monkeypatch.delenv("KAGGLE_USERNAME", raising=False)
    monkeypatch.delenv("KAGGLE_KEY", raising=False)

    result = asyncio.run(
        settings.kaggle_login(
            settings.KaggleLoginRequest(username="tester", key="secretkey")
        )
    )
    assert result["ok"] is True
    content = env_file.read_text(encoding="utf-8")
    assert "KAGGLE_USERNAME=tester" in content
    assert "KAGGLE_KEY=secretkey" in content
    assert "EXISTING=1" in content  # bestehende Einträge bleiben
    assert os.environ.get("KAGGLE_USERNAME") == "tester"
    assert os.environ.get("KAGGLE_KEY") == "secretkey"


def test_settings_kaggle_login_from_json(tmp_path, monkeypatch):
    from api.routers import settings

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "_ENV_PATH", env_file)
    json_content = json.dumps({"username": "jsonuser", "key": "jsonkey"})

    result = asyncio.run(
        settings.kaggle_login(settings.KaggleLoginRequest(kaggle_json=json_content))
    )
    assert result["ok"] is True
    assert result["username"] == "jsonuser"
    assert "KAGGLE_USERNAME=jsonuser" in env_file.read_text(encoding="utf-8")


def test_settings_kaggle_login_invalid_json(monkeypatch, tmp_path):
    from api.routers import settings

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "_ENV_PATH", env_file)
    with pytest.raises(Exception):
        asyncio.run(
            settings.kaggle_login(settings.KaggleLoginRequest(kaggle_json="not json"))
        )


def test_settings_kaggle_login_missing_both(monkeypatch, tmp_path):
    from api.routers import settings

    env_file = tmp_path / ".env"
    env_file.write_text("", encoding="utf-8")
    monkeypatch.setattr(settings, "_ENV_PATH", env_file)
    with pytest.raises(Exception):
        asyncio.run(settings.kaggle_login(settings.KaggleLoginRequest()))


def test_settings_kaggle_logout_removes_env(tmp_path, monkeypatch):
    from api.routers import settings

    env_file = tmp_path / ".env"
    env_file.write_text(
        "KAGGLE_USERNAME=tester\nKAGGLE_KEY=secret\nOTHER=1\n", encoding="utf-8"
    )
    monkeypatch.setattr(settings, "_ENV_PATH", env_file)
    monkeypatch.setenv("KAGGLE_USERNAME", "tester")
    monkeypatch.setenv("KAGGLE_KEY", "secret")

    result = asyncio.run(settings.kaggle_logout())
    assert result["ok"] is True
    content = env_file.read_text(encoding="utf-8")
    assert "KAGGLE_USERNAME" not in content
    assert "KAGGLE_KEY" not in content
    assert "OTHER=1" in content
    assert "KAGGLE_USERNAME" not in os.environ
    assert "KAGGLE_KEY" not in os.environ


def _async_cm(client_mock: AsyncMock) -> Any:
    """Baut einen async-context-manager Mock für ``httpx.AsyncClient``."""
    cm = AsyncMock()
    cm.__aenter__ = AsyncMock(return_value=client_mock)
    cm.__aexit__ = AsyncMock(return_value=None)
    return cm
