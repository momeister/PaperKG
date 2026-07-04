"""Datensätze (WP2): freie Registries durchsuchen + Referenzen sammeln.

Split out of api/product_main.py. Behaviour unchanged.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from harvester import dataset_clients
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


class DatasetSearchRequest(BaseModel):
    """Search free dataset registries (Zenodo, Figshare, Dryad, ClinicalTrials, PWC)."""
    query: str = Field(min_length=1, max_length=500)
    sources: list[str] = Field(default_factory=lambda: list(dataset_clients.DEFAULT_SOURCES))
    per_source: int = Field(default=8, ge=1, le=25)


class DatasetImportRequest(BaseModel):
    """Persist selected dataset references into a project."""
    datasets: list[dict[str, Any]]
    project_id: str | None = None
    linked_paper_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


@router.get("/datasets/sources")
def dataset_sources() -> dict[str, Any]:
    """Available dataset registries for the search picker."""
    return {"sources": dataset_clients.DATASET_SOURCES, "default": list(dataset_clients.DEFAULT_SOURCES)}


@router.post("/datasets/search")
async def search_datasets(request: DatasetSearchRequest) -> dict[str, Any]:
    """Search the selected free dataset registries concurrently (fail-soft per source)."""
    result = await dataset_clients.search_datasets(
        request.query, request.sources, per_source=request.per_source
    )
    return result


@router.get("/datasets/details")
async def dataset_details(source: str, external_id: str) -> dict[str, Any]:
    """Datei-Liste, Beschreibung und Download-Links eines Datensatzes (on demand)."""
    return await dataset_clients.fetch_dataset_details(source, external_id)


@router.post("/datasets/import")
def import_datasets(request: DatasetImportRequest) -> dict[str, Any]:
    """Persist selected dataset references (de-duplicated) into a project."""
    imported: list[dict[str, Any]] = []
    with MetadataDB(request.metadata_db_path) as db:
        for ds in request.datasets:
            record = {**ds, "project_id": request.project_id}
            if request.linked_paper_id:
                record.setdefault("linked_paper_id", request.linked_paper_id)
            imported.append(db.add_dataset(record))
    return {"imported": imported, "count": len(imported)}


@router.get("/datasets")
def list_datasets(
    project_id: str | None = None, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"datasets": db.list_datasets(project_id)}


@router.get("/datasets/{dataset_id}")
def get_dataset(dataset_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="Datensatz nicht gefunden")
    return {"dataset": ds}


@router.delete("/datasets/{dataset_id}")
def delete_dataset(dataset_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_dataset(dataset_id)
    return {"deleted": deleted, "id": dataset_id}
