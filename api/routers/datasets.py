"""Datensätze (WP2): freie Registries durchsuchen + Referenzen sammeln.

Split out of api/product_main.py. Behaviour unchanged. Erweitert um Kaggle/HF/
OpenML/UCI/Mendeley-Quellen (Task-Focused Mode): Download-Endpoint für Kaggle
und Source-Status für die Login-Anzeige im UI.
"""

from __future__ import annotations

import os
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from harvester import dataset_clients
from harvester import (
    huggingface_datasets_client as _hf,
    kaggle_client as _kaggle,
    mendeley_client as _mendeley,
    openml_client as _openml,
    uci_client as _uci,
)
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


class DatasetSearchRequest(BaseModel):
    """Search free dataset registries (Zenodo, Figshare, Dryad, ClinicalTrials, PWC,
    Kaggle, HuggingFace, OpenML, UCI, Mendeley)."""

    query: str = Field(min_length=1, max_length=500)
    sources: list[str] = Field(
        default_factory=lambda: list(dataset_clients.DEFAULT_SOURCES)
    )
    per_source: int = Field(default=8, ge=1, le=25)


class DatasetImportRequest(BaseModel):
    """Persist selected dataset references into a project."""

    datasets: list[dict[str, Any]]
    project_id: str | None = None
    linked_paper_id: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class DatasetDownloadRequest(BaseModel):
    """Datensatz herunterladen (Kaggle auth-pflichtig)."""

    source: str = Field(..., description="z.B. kaggle_competition, kaggle_dataset")
    external_id: str = Field(..., description="Competition-ref oder owner/slug")
    file_name: str | None = Field(
        default=None, description="bei Competition-Einzeldatei"
    )
    dest_dir: str = Field(default="data/datasets", description="Zielordner")
    timeout: float = Field(default=600.0)


@router.get("/datasets/sources")
def dataset_sources() -> dict[str, Any]:
    """Available dataset registries for the search picker."""
    return {
        "sources": dataset_clients.DATASET_SOURCES,
        "default": list(dataset_clients.DEFAULT_SOURCES),
    }


@router.get("/datasets/sources/status")
async def dataset_sources_status() -> dict[str, Any]:
    """Login-Status pro Quelle (Kaggle/HF brauchen optional Auth). Für UI-Badges."""
    kaggle = await _kaggle.kaggle_status()
    hf = await _hf.hf_status()
    openml = await _openml.openml_status()
    uci = await _uci.uci_status()
    mendeley = await _mendeley.mendeley_status()
    return {
        "kaggle_competition": kaggle,
        "kaggle_dataset": kaggle,
        "huggingface": hf,
        "openml": openml,
        "uci": uci,
        "mendeley": mendeley,
    }


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


@router.post("/datasets/download")
async def download_dataset(request: DatasetDownloadRequest) -> dict[str, Any]:
    """Datensatz (oder Competition-Einzeldatei) herunterladen. Kaggle auth-pflichtig."""
    os.makedirs(request.dest_dir, exist_ok=True)
    # Dateiname aus external_id ableiten wenn nicht angegeben.
    safe = request.external_id.replace("/", "_").replace(":", "_")
    ext = ".zip"
    if request.source == "kaggle_competition" and request.file_name:
        ext = os.path.splitext(request.file_name)[1] or ""
    dest_path = os.path.join(request.dest_dir, f"{safe}{ext}")
    result = await dataset_clients.download_dataset(
        request.source,
        request.external_id,
        dest_path,
        file_name=request.file_name,
        timeout=request.timeout,
    )
    return result


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
def get_dataset(
    dataset_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        ds = db.get_dataset(dataset_id)
    if ds is None:
        raise HTTPException(status_code=404, detail="Datensatz nicht gefunden")
    return {"dataset": ds}


@router.delete("/datasets/{dataset_id}")
def delete_dataset(
    dataset_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_dataset(dataset_id)
    return {"deleted": deleted, "id": dataset_id}
