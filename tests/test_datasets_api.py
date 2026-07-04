from __future__ import annotations

import asyncio

from fastapi.testclient import TestClient

from api import product_main
from harvester import dataset_clients
from harvester.dataset_clients import DatasetHit, search_datasets
from storage.metadata_db import MetadataDB


def _hit(source: str, ext: str) -> DatasetHit:
    return DatasetHit(source=source, external_id=ext, title=f"{source} {ext}", url=f"https://x/{ext}")


def test_search_datasets_aggregates_and_is_failsoft(monkeypatch):
    async def ok_zenodo(client, query, limit):  # noqa: ANN001
        return [_hit("zenodo", "z1"), _hit("zenodo", "z2")]

    async def boom(client, query, limit):  # noqa: ANN001
        raise RuntimeError("down")

    monkeypatch.setitem(dataset_clients._FETCHERS, "zenodo", ok_zenodo)
    monkeypatch.setitem(dataset_clients._FETCHERS, "dryad", boom)

    result = asyncio.run(search_datasets("diabetes", ["zenodo", "dryad"], per_source=5))
    assert len(result["results"]) == 2
    assert result["results"][0]["source"] == "zenodo"
    # The failing source is reported as a warning, not an exception.
    assert any("dryad" in w for w in result["warnings"])


def test_dataset_db_dedup_list_delete(tmp_path):
    db_path = tmp_path / "metadata.duckdb"
    with MetadataDB(str(db_path)) as db:
        a = db.add_dataset({"project_id": "proj", "source": "zenodo", "external_id": "z1", "title": "A", "year": 2022})
        again = db.add_dataset({"project_id": "proj", "source": "zenodo", "external_id": "z1", "title": "A dup"})
        assert again["id"] == a["id"]  # de-duplicated on (project, source, external_id)
        db.add_dataset({"project_id": "proj", "source": "dryad", "external_id": "d1", "title": "B"})
        listed = db.list_datasets("proj")
        assert len(listed) == 2
        assert db.delete_dataset(a["id"]) is True
        assert len(db.list_datasets("proj")) == 1


def test_datasets_api_flow(tmp_path, monkeypatch):
    db_path = tmp_path / "metadata.duckdb"
    common = {"metadata_db_path": str(db_path)}

    async def fake_search(query, sources=None, per_source=8, timeout=25.0):  # noqa: ANN001
        return {"results": [_hit("zenodo", "z9").as_dict()], "warnings": []}

    monkeypatch.setattr(dataset_clients, "search_datasets", fake_search)
    client = TestClient(product_main.app)

    sources = client.get("/datasets/sources")
    assert sources.status_code == 200 and sources.json()["default"]

    search = client.post("/datasets/search", json={"query": "diabetes", "sources": ["zenodo"]})
    assert search.status_code == 200
    hit = search.json()["results"][0]
    assert hit["source"] == "zenodo"

    imported = client.post(
        "/datasets/import",
        json={"datasets": [hit], "project_id": "proj", **common},
    )
    assert imported.status_code == 200 and imported.json()["count"] == 1
    ds_id = imported.json()["imported"][0]["id"]

    listed = client.get("/datasets", params={"project_id": "proj", **common})
    assert any(d["id"] == ds_id for d in listed.json()["datasets"])

    detail = client.get(f"/datasets/{ds_id}", params=common)
    assert detail.status_code == 200

    deleted = client.delete(f"/datasets/{ds_id}", params=common)
    assert deleted.status_code == 200 and deleted.json()["deleted"] is True
