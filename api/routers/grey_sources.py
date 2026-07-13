"""Graue Quellen: kuratierte Web-Funde je Projekt (nie im KG), URL-Import.

Split out of api/product_main.py. Behaviour unchanged. httpx laeuft ueber
pm.httpx (Test patcht product_main.httpx.AsyncClient fuer from-url).
"""
from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

import api.product_main as pm  # patchable httpx surface
from api.routers.papers import _infer_title_from_html
from research.sanitize import FULL_TEXT_MAX_LEN, sanitize_web_text
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


class GreySourcePayload(BaseModel):
    sources: list[dict[str, Any]]
    query: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class GreySourceFromUrlPayload(BaseModel):
    url: str
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


@router.get("/projects/{project_id}/grey-sources")
def list_project_grey_sources(
    project_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        items = db.list_grey_sources(project_id)
    return {"project_id": project_id, "grey_sources": items}


@router.post("/projects/{project_id}/grey-sources")
def add_project_grey_sources(project_id: str, payload: GreySourcePayload) -> dict[str, Any]:
    """Persist user-confirmed grey sources for a project (not added to the KG)."""
    saved: list[dict[str, Any]] = []
    with MetadataDB(payload.metadata_db_path) as db:
        for source in payload.sources:
            record = dict(source)
            record.setdefault("query", payload.query)
            saved.append(db.add_grey_source(project_id, record))
    return {"project_id": project_id, "saved": saved}


@router.post("/projects/{project_id}/grey-sources/from-url")
async def add_grey_source_from_url(project_id: str, payload: GreySourceFromUrlPayload) -> dict[str, Any]:
    """Fetch, sanitize, and save a single user-pasted URL as a grey source.

    Mirrors the deep-research fetch path (sanitize_web_text) but skips the LLM
    summarization step — title comes from a cheap <title>/<h1> regex heuristic so
    pasting a link stays fast.
    """
    url = payload.url.strip()
    if not re.match(r"^https?://", url):
        raise HTTPException(status_code=400, detail="Nur http(s)-URLs werden unterstützt")
    try:
        async with pm.httpx.AsyncClient(
            timeout=20.0,
            headers={"User-Agent": "ScienceKG/Phase5 (local)"},
            follow_redirects=True,
        ) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=502, detail=f"Konnte URL nicht laden: {exc}") from exc

    raw_html = response.text
    clean, flags = sanitize_web_text(raw_html, max_len=FULL_TEXT_MAX_LEN)
    title = _infer_title_from_html(raw_html) or url
    record = {
        "url": url,
        "title": title,
        "summary": clean[:400],
        "full_text": clean,
        "injection_flags": flags,
    }
    with MetadataDB(payload.metadata_db_path) as db:
        saved = db.add_grey_source(project_id, record)
    return {"project_id": project_id, "saved": saved}


@router.delete("/grey-sources/{grey_id}")
def delete_grey_source(grey_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_grey_source(grey_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Grey source not found: {grey_id}")
    return {"deleted": True, "id": grey_id}
