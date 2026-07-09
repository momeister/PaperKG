"""PDF-Notizen: an einer Textstelle/Punkt im PDF verankerte Notizen (pro Paper).

Anker-Modell: page_number + rects (0..1 normalisiert zur Seiten-Oberfläche, zoom-
unabhängig) + quote (markierter Text) + body. kind = 'highlight' | 'point'.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"

router = APIRouter()


class PdfAnnotationRect(BaseModel):
    """A rectangle normalized 0..1 relative to the page surface."""
    x: float = Field(ge=0.0, le=1.0)
    y: float = Field(ge=0.0, le=1.0)
    width: float = Field(ge=0.0, le=1.0)
    height: float = Field(ge=0.0, le=1.0)


class PdfAnnotationCreate(BaseModel):
    page_number: int = Field(ge=1)
    kind: str = Field(default="highlight")
    rects: list[PdfAnnotationRect] = Field(default_factory=list)
    quote: str | None = None
    body: str = ""
    color: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


class PdfAnnotationUpdate(BaseModel):
    body: str | None = None
    color: str | None = None
    kind: str | None = None
    quote: str | None = None
    rects: list[PdfAnnotationRect] | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH


@router.get("/papers/{paper_id}/annotations")
def list_pdf_annotations(
    paper_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        return {"annotations": db.list_pdf_annotations(paper_id)}


@router.post("/papers/{paper_id}/annotations")
def create_pdf_annotation(paper_id: str, payload: PdfAnnotationCreate) -> dict[str, Any]:
    if payload.kind not in ("highlight", "point"):
        raise HTTPException(status_code=400, detail="kind muss 'highlight' oder 'point' sein")
    with MetadataDB(payload.metadata_db_path) as db:
        record = db.add_pdf_annotation({
            "paper_id": paper_id,
            "page_number": payload.page_number,
            "kind": payload.kind,
            "rects": [r.model_dump() for r in payload.rects],
            "quote": payload.quote,
            "body": payload.body,
            "color": payload.color,
        })
    return {"annotation": record}


@router.patch("/pdf-annotations/{annotation_id}")
def update_pdf_annotation(annotation_id: str, payload: PdfAnnotationUpdate) -> dict[str, Any]:
    fields: dict[str, Any] = {}
    if payload.body is not None:
        fields["body"] = payload.body
    if payload.color is not None:
        fields["color"] = payload.color
    if payload.kind is not None:
        fields["kind"] = payload.kind
    if payload.quote is not None:
        fields["quote"] = payload.quote
    if payload.rects is not None:
        fields["rects"] = [r.model_dump() for r in payload.rects]
    with MetadataDB(payload.metadata_db_path) as db:
        record = db.update_pdf_annotation(annotation_id, fields)
    if record is None:
        raise HTTPException(status_code=404, detail="Notiz nicht gefunden")
    return {"annotation": record}


@router.delete("/pdf-annotations/{annotation_id}")
def delete_pdf_annotation(
    annotation_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        deleted = db.delete_pdf_annotation(annotation_id)
    return {"deleted": deleted, "id": annotation_id}
