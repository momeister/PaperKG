"""Extraction: PDF-Library, Parse, LLM-Extraktion (auch abstract-only/grey),
Batch-Jobs, Historie, Vokabular.

Split out of api/product_main.py. Behaviour unchanged. Patchbare Singletons
laufen ueber pm.<name>: llm_router, parser_router, extraction_pipeline,
embedding_engine (geteilte Instanz), _slug (bleibt in product_main).
"""
from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons + geteilte Helfer
from api.routers.papers import (
    _clean_pdf_title,
    _latest_extraction_statuses,
    _maybe_fix_garbled_pdf_title,
    _paper_list_view,
    _paper_local_pdf_path,
)
from api.routers.projects import _is_reserved_project_id, _load_projects, _projects_path
from extraction.batch_processor import BatchProcessor
from extraction.entity_extractor import EntityExtractor, extraction_failure_reason
from extraction.vocabulary import VocabularyManager
from parsing.parser_router import ParserType
from query.source_verifier import build_pdf_index, find_pdf_path
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_PDF_BASE_DIR = "data/pdfs"
DEFAULT_VOCABULARY_PATH = "data/vocabulary.json"

logger = logging.getLogger(__name__)

router = APIRouter()


class ExtractionParseRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    pdf_path: str | None = Field(default=None, max_length=1000)
    parser: str | None = Field(default=None, max_length=80)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class ExtractionRunRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    text: str | None = Field(default=None, max_length=2_000_000)
    pdf_path: str | None = Field(default=None, max_length=1000)
    parser: str | None = Field(default=None, max_length=80)
    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=256, le=131072)
    context_size: int | None = Field(default=None, ge=1024, le=262144)
    extraction_mode: str | None = Field(default="quality", max_length=80)
    context_policy: str | None = Field(default="auto", pattern="^(auto|whole|chunk)$")
    allow_context_fallback: bool = False
    link_concepts: bool = True
    save: bool = True
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class ExtractionBatchItem(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    # Ohne pdf_path fällt der Batch auf Abstract-only-Extraktion zurück (wie /extraction/extract).
    pdf_path: str | None = Field(default=None, max_length=1000)


class ExtractionBatchRequest(BaseModel):
    items: list[ExtractionBatchItem]
    provider: str | None = None
    model: str | None = None
    temperature: float | None = Field(default=None, ge=0, le=2)
    top_p: float | None = Field(default=None, ge=0, le=1)
    max_tokens: int | None = Field(default=None, ge=256, le=131072)
    context_size: int | None = Field(default=None, ge=1024, le=262144)
    extraction_mode: str | None = Field(default="quality", max_length=80)
    context_policy: str | None = Field(default="auto", pattern="^(auto|whole|chunk)$")
    allow_context_fallback: bool = False
    link_concepts: bool = True
    resume: bool = True
    job_id: str | None = Field(default=None, max_length=120)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class VocabularyEntryRequest(BaseModel):
    canonical_label: str = Field(min_length=1, max_length=240)
    aliases: list[str] = []
    openalx_id: str | None = Field(default=None, max_length=240)
    domain: str | None = Field(default=None, max_length=240)
    vocabulary_path: str = DEFAULT_VOCABULARY_PATH



@router.get("/extraction/library")
def extraction_library(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    query: str = "",
    project_id: str | None = None,
    projects_path: str | None = None,
    limit: int | None = Query(default=None, ge=1, le=100000),
) -> dict[str, Any]:
    rows = _local_pdf_library(metadata_db_path, pdf_base_dir)
    # "Alle Papers" / no project = global union over every project; else scope to members.
    is_global = (not project_id) or _is_reserved_project_id(project_id)
    member_ids: set[str] | None
    if is_global:
        member_ids = None
    else:
        projects = _load_projects(_projects_path(projects_path))
        member_ids = set(projects.get(project_id or "", []))
        rows = [row for row in rows if str(row.get("paper_id") or "") in member_ids]
    degraded: str | None = None
    try:
        with MetadataDB(metadata_db_path) as _db:
            grey_list = _db.list_grey_sources(None if is_global else project_id, limit=50000)
            latest_by_paper = _latest_extraction_statuses(_db)
            found_ids = {str(row.get("paper_id") or "") for row in rows}
            nopdf_papers = [
                p for p in _db.list_papers(limit=50000)
                if str(p.get("id") or "") not in found_ids
                and (member_ids is None or str(p.get("id") or "") in member_ids)
            ]
        for grey in grey_list:
            if grey.get("injection_flags"):
                continue
            full_text = (grey.get("full_text") or "").strip()
            if not full_text:
                continue
            rows.append({
                "paper_id": f"grey::{grey['id']}",
                "title": grey.get("title") or grey.get("url") or grey["id"],
                "filename": "",
                "pdf_path": "",
                "pdf_available": True,
                "source_type": "grey",
                "text": full_text[:200000],
                "size_bytes": len(full_text.encode("utf-8")),
                "modified_timestamp": grey.get("created_timestamp"),
                "latest_extraction_status": None,
                "known_paper": False,
            })
        for paper in nopdf_papers:
            pid = str(paper.get("id") or "")
            rows.append({
                "paper_id": pid,
                "title": paper.get("title") or pid,
                "filename": "",
                "pdf_path": "",
                "pdf_available": False,
                "abstract_available": bool(str(paper.get("abstract") or "").strip()),
                "source_type": "pdf",
                "size_bytes": None,
                "modified_timestamp": None,
                "latest_extraction_status": latest_by_paper.get(pid),
                "known_paper": True,
            })
    except Exception as error:  # noqa: BLE001 - der Datei-Scan bleibt auch ohne DB nutzbar
        # Frueher ein stilles `pass`: bei gesperrter DB verschwanden dadurch saemtliche
        # Grauquellen und alle nur-Abstract-Paper aus der Liste, ohne jeden Hinweis.
        # Jetzt wird die Teilansicht als `degraded` markiert und die UI warnt.
        logger.warning("Extraktions-Library ohne Metadaten-DB ausgeliefert: %s", error)
        degraded = str(error)
    if query:
        query_lower = query.lower()
        rows = [
            row for row in rows
            if query_lower in str(row.get("paper_id") or "").lower()
            or query_lower in str(row.get("title") or "").lower()
            or query_lower in str(row.get("filename") or "").lower()
        ]
    items = rows if limit is None else rows[:limit]
    payload: dict[str, Any] = {"items": items, "total": len(rows)}
    if degraded:
        payload["degraded"] = degraded
    return payload


@router.post("/extraction/parse")
def parse_extraction_pdf(request: ExtractionParseRequest) -> dict[str, Any]:
    pdf_path = _resolve_extraction_pdf_path(
        request.paper_id,
        request.pdf_path,
        request.metadata_db_path,
        request.pdf_base_dir,
    )
    parsed = _parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
    with MetadataDB(request.metadata_db_path) as db:
        canonical_id = db.ensure_paper_record(
            request.paper_id,
            title=EntityExtractor._paper_title_from_text(parsed.text)[:240],
            pdf_path=str(pdf_path),
        )
    return {
        "paper_id": canonical_id,
        "pdf_path": str(pdf_path),
        "text": parsed.text,
        "page_count": parsed.page_count,
        "parser": str(parsed.parser.value if hasattr(parsed.parser, "value") else parsed.parser),
        "metadata": _parsed_document_metadata(parsed),
        "excerpt": parsed.text[:4000],
    }


@router.post("/extraction/extract")
def run_extraction(request: ExtractionRunRequest) -> dict[str, Any]:
    text = (request.text or "").strip()
    pdf_path: Path | None = None
    parse_payload: dict[str, Any] | None = None
    if not text and request.paper_id.startswith("grey::"):
        grey_id = request.paper_id.removeprefix("grey::")
        with MetadataDB(request.metadata_db_path) as db:
            grey = db.get_grey_source(grey_id)
        if not grey or not (grey.get("full_text") or "").strip():
            raise HTTPException(status_code=404, detail=f"Grey source has no text: {grey_id}")
        text = grey["full_text"].strip()
    elif not text:
        try:
            pdf_path = _resolve_extraction_pdf_path(
                request.paper_id,
                request.pdf_path,
                request.metadata_db_path,
                request.pdf_base_dir,
            )
        except HTTPException as pdf_error:
            # Kein lokales PDF: Abstract-only-Extraktion. Viele Paper haben zwar keinen
            # PDF-Download, aber Titel + Abstract in der papers-Tabelle — die reichen
            # für eine (dünnere) Aufnahme in den Knowledge Graph.
            abstract_text = _abstract_only_extraction_text(request.paper_id, request.metadata_db_path)
            if not abstract_text:
                raise pdf_error
            text = abstract_text
            parse_payload = {
                "pdf_path": None,
                "page_count": 0,
                "parser": "abstract-only",
                "metadata": {},
                "excerpt": text[:4000],
            }
        else:
            parsed = _parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
            text = parsed.text
            parse_payload = {
                "pdf_path": str(pdf_path),
                "page_count": parsed.page_count,
                "parser": str(parsed.parser.value if hasattr(parsed.parser, "value") else parsed.parser),
                "metadata": _parsed_document_metadata(parsed),
                "excerpt": parsed.text[:4000],
            }
            _maybe_fix_garbled_pdf_title(
                paper_id=request.paper_id,
                text=text,
                pdf_path=pdf_path,
                metadata_db_path=request.metadata_db_path,
            )
    if not text:
        raise HTTPException(status_code=400, detail="No paper text or parseable PDF text provided.")

    overrides = _extraction_overrides(request)
    start = time.monotonic()
    is_grey = request.paper_id.startswith("grey::")
    try:
        result = pm.extraction_pipeline.process(
            request.paper_id,
            text,
            provider=request.provider,
            overrides=overrides,
            link_concepts=request.link_concepts,
        )
    except Exception as exc:
        if request.save:
            with MetadataDB(request.metadata_db_path) as db:
                if is_grey:
                    canonical_id = request.paper_id
                else:
                    canonical_id = db.ensure_paper_record(
                        request.paper_id,
                        title=EntityExtractor._paper_title_from_text(text)[:240],
                        pdf_path=str(pdf_path) if pdf_path else request.pdf_path,
                    )
                db.save_extraction_result(
                    paper_id=canonical_id,
                    llm_provider=request.provider or getattr(pm.llm_router, "default_provider", "default"),
                    llm_model=_selected_model(request.provider, request.model),
                    error_message=str(exc),
                    duration_seconds=time.monotonic() - start,
                )
        raise HTTPException(status_code=500, detail=f"Extraction failed: {exc}") from exc

    duration = time.monotonic() - start
    failure_reason = extraction_failure_reason(result)
    canonical_id = request.paper_id
    result_id: int | None = None
    if request.save:
        with MetadataDB(request.metadata_db_path) as db:
            if is_grey:
                canonical_id = request.paper_id
            else:
                canonical_id = db.ensure_paper_record(
                    request.paper_id,
                    title=EntityExtractor._paper_title_from_text(text)[:240],
                    year=_year_from_extraction_result(result),
                    pdf_path=str(pdf_path) if pdf_path else request.pdf_path,
                )
            result_id = db.save_extraction_result(
                paper_id=canonical_id,
                llm_provider=request.provider or getattr(pm.llm_router, "default_provider", "default"),
                llm_model=_selected_model(request.provider, request.model),
                paper_type=result.paper_type,
                concepts=result.concepts,
                methods=result.methods,
                concept_candidates=result.concept_candidates,
                method_candidates=result.method_candidates,
                relations=result.relations,
                claims=result.claims,
                cross_domain_hints=result.cross_domain_hints,
                terminology_conflicts=result.terminology_conflicts,
                temporal_coverage=result.temporal_coverage,
                mathematical_content=result.mathematical_content,
                raw_response=result.raw_response,
                error_message=failure_reason,
                duration_seconds=duration,
            )
    return {
        "result_id": result_id,
        "paper_id": canonical_id,
        "status": "failed" if failure_reason else "success",
        "error_message": failure_reason,
        "duration_seconds": duration,
        "parse": parse_payload,
        "result": _extraction_result_payload(result),
    }


@router.post("/extraction/batch")
def run_extraction_batch(request: ExtractionBatchRequest) -> dict[str, Any]:
    if not request.items:
        raise HTTPException(status_code=400, detail="Select at least one PDF.")
    pdf_paths: dict[str, str] = {}
    abstract_texts: dict[str, str] = {}
    for item in request.items:
        try:
            pdf_paths[item.paper_id] = str(
                _resolve_extraction_pdf_path(
                    item.paper_id,
                    item.pdf_path,
                    request.metadata_db_path,
                    request.pdf_base_dir,
                )
            )
        except HTTPException as pdf_error:
            if pdf_error.status_code != 404:
                raise
            # Kein lokales PDF: Abstract-only wie bei /extraction/extract. Fehlt auch der
            # Abstract, läuft der Batch weiter und nur dieses Item schlägt fehl.
            abstract_text = _abstract_only_extraction_text(item.paper_id, request.metadata_db_path)
            if abstract_text:
                abstract_texts[item.paper_id] = abstract_text
    processor = BatchProcessor(
        pm.llm_router,
        pm.parser_router,
        pm.embedding_engine,
        metadata_db_factory=lambda: MetadataDB(request.metadata_db_path),
        link_concepts=request.link_concepts,
        quality_db_path=request.metadata_db_path,
        # Genau ein Wiederholungsversuch mit Pause: kurze Rate-Limit-Fenster und
        # Netzwerk-Aussetzer kosten damit nur Zeit statt eines verlorenen Papers.
        # Bei erschoepftem Kontingent oder ungueltigem Key wird nicht wiederholt,
        # sondern der Batch abgebrochen (siehe BatchProcessor.process_papers).
        max_retries=1,
        retry_delay_seconds=20.0,
    )
    status = processor.process_papers(
        [item.paper_id for item in request.items],
        pdf_paths,
        texts=abstract_texts,
        job_id=request.job_id,
        llm_provider=request.provider,
        llm_overrides=_extraction_overrides(request),
        resume=request.resume,
    )
    with MetadataDB(request.metadata_db_path) as db:
        items = db.get_batch_job_items(status.job_id)
    return {
        "job": {
            "job_id": status.job_id,
            "status": status.status,
            "papers_total": status.papers_total,
            "papers_processed": status.papers_processed,
            "papers_failed": status.papers_failed,
            "error_message": status.error_message,
            "superseded_by": status.superseded_by,
        },
        "items": items,
    }


@router.get("/extraction/batch/{job_id}/items")
def get_extraction_batch_items(job_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        job = db.get_batch_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        items = db.get_batch_job_items(job_id)
    return {"job_id": job_id, "items": items}


@router.post("/extraction/batch/{job_id}/cancel")
def cancel_extraction_batch(job_id: str, metadata_db_path: str = DEFAULT_METADATA_DB_PATH) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        job = db.get_batch_job(job_id)
        if not job:
            raise HTTPException(status_code=404, detail="Job not found")
        db.cancel_batch_job(job_id)
        updated = db.get_batch_job(job_id)
    return {"job_id": job_id, "status": (updated or {}).get("status")}


@router.get("/extraction/history")
def extraction_history(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    paper_id: str = "",
    limit: int = Query(default=50, ge=1, le=500),
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        items = db.get_paper_extractions(paper_id, limit=limit) if paper_id.strip() else db.list_extraction_results(limit=limit)
    return {"items": items, "total": len(items)}


@router.get("/extraction/vocabulary")
def extraction_vocabulary(vocabulary_path: str = DEFAULT_VOCABULARY_PATH) -> dict[str, Any]:
    vocabulary = _load_vocabulary(vocabulary_path)
    entries = [
        {
            "canonical_label": canonical,
            "aliases": entry.aliases,
            "openalx_id": entry.openalx_id,
            "domain": entry.domain,
            "confidence": entry.confidence,
            "custom_metadata": entry.custom_metadata,
        }
        for canonical, entry in sorted(vocabulary.entries.items())
    ]
    return {"items": entries, "total": len(entries)}


@router.post("/extraction/vocabulary")
def add_extraction_vocabulary_entry(request: VocabularyEntryRequest) -> dict[str, Any]:
    vocabulary = _load_vocabulary(request.vocabulary_path)
    vocabulary.register(
        request.canonical_label.strip(),
        aliases=[alias.strip() for alias in request.aliases if alias.strip()],
        openalx_id=request.openalx_id or None,
        domain=request.domain or None,
    )
    _save_vocabulary(vocabulary, request.vocabulary_path)
    return extraction_vocabulary(request.vocabulary_path)



def _latest_successful_extractions(extractions: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    latest: dict[str, dict[str, Any]] = {}
    for extraction in extractions:
        pid = str(extraction.get("paper_id") or "")
        if not pid or pid in latest or extraction.get("extraction_status") != "success":
            continue
        latest[pid] = extraction
    return latest


def _local_pdf_library(metadata_db_path: str, pdf_base_dir: str) -> list[dict[str, Any]]:
    """Alle Paper mit lokal aufloesbarem PDF, plus verwaiste PDFs auf der Platte.

    Schluessel ist die ``paper_id``, **nicht** der PDF-Pfad: teilen sich zwei Paper
    (etwa ein Preprint und die Journal-Version nach dem Dedup) dieselbe Datei, hat
    die pfad-basierte Variante eines davon ueberschrieben. Der Aufrufer leitet aus
    den ueberlebenden Zeilen ``found_ids`` ab und meldete das verdraengte Paper
    anschliessend faelschlich als "ohne PDF" — obwohl ein PDF existiert.
    """
    latest_by_paper: dict[str, str] = {}
    rows_by_id: dict[str, dict[str, Any]] = {}
    seen_paths: set[str] = set()
    pdf_index = build_pdf_index(pdf_base_dir)
    with MetadataDB(metadata_db_path) as db:
        latest_by_paper = _latest_extraction_statuses(db)
        for paper in db.list_papers(limit=50000):
            view = _paper_list_view(paper, pdf_base_dir, pdf_index=pdf_index)
            pdf_path = view.get("pdf_path")
            if not pdf_path:
                continue
            path = Path(str(pdf_path))
            pid = str(view.get("id") or view.get("paper_id") or "") or _default_paper_id_from_pdf(path.name)
            seen_paths.add(str(path.resolve()) if path.exists() else str(path))
            rows_by_id[pid] = {
                "paper_id": pid,
                "title": view.get("display_title") or view.get("title") or _clean_pdf_title(path.name),
                "filename": path.name,
                "pdf_path": str(path),
                "pdf_available": True,
                # Auch fuer PDF-Zeilen mitliefern: das Frontend kann damit vorhersagen,
                # ob der Abstract-Fallback greift, falls das PDF nicht parsebar ist.
                "abstract_available": bool(str(paper.get("abstract") or "").strip()),
                "size_bytes": path.stat().st_size if path.exists() else None,
                "modified_timestamp": datetime.fromtimestamp(path.stat().st_mtime).isoformat() if path.exists() else None,
                "latest_extraction_status": latest_by_paper.get(pid),
                "known_paper": True,
            }

    for path_str, _stem in pdf_index:
        path = Path(path_str)
        if str(path.resolve()) in seen_paths:
            continue
        paper_id_value = _default_paper_id_from_pdf(path.name)
        if paper_id_value in rows_by_id:
            continue
        rows_by_id[paper_id_value] = {
            "paper_id": paper_id_value,
            "title": _clean_pdf_title(path.name) or paper_id_value,
            "filename": path.name,
            "pdf_path": str(path),
            "pdf_available": True,
            "abstract_available": False,
            "size_bytes": path.stat().st_size,
            "modified_timestamp": datetime.fromtimestamp(path.stat().st_mtime).isoformat(),
            "latest_extraction_status": latest_by_paper.get(paper_id_value),
            "known_paper": False,
        }
    rows = list(rows_by_id.values())
    rows.sort(key=lambda row: str(row.get("modified_timestamp") or ""), reverse=True)
    return rows


def _default_paper_id_from_pdf(filename: str) -> str:
    stem = Path(str(filename).replace("\\", "/")).stem
    cleaned = re.sub(r"__+", "__", stem.strip())
    if cleaned:
        arxiv_match = re.search(r"(\d{4}\.\d{4,5}(?:v\d+)?)", cleaned)
        if arxiv_match:
            arxiv_id = re.sub(r"v\d+$", "", arxiv_match.group(1), flags=re.IGNORECASE)
            return f"arxiv:{arxiv_id}"
        legacy_arxiv = re.search(r"([a-z-]+_\d{7})", cleaned, flags=re.IGNORECASE)
        if legacy_arxiv:
            return "arxiv:" + legacy_arxiv.group(1).replace("_", "/")
    return pm._slug(cleaned or filename)


def _abstract_only_extraction_text(paper_id_value: str, metadata_db_path: str) -> str:
    """Titel + Abstract als Extraktionstext für Paper ohne lokales PDF.

    Damit landen auch PDF-lose Paper (nur Metadaten) im Knowledge Graph —
    die Extraktion ist dünner, aber Konzepte/Claims aus dem Abstract sind
    besser als gar keine Aufnahme.
    """
    try:
        with MetadataDB(metadata_db_path) as db:
            paper = db.resolve_paper(paper_id_value)
    except Exception:
        return ""
    if not paper:
        return ""
    title = str(paper.get("title") or "").strip()
    abstract = str(paper.get("abstract") or "").strip()
    if not abstract:
        return ""
    parts = [title, "", "Abstract:", abstract]
    return "\n".join(part for part in parts if part is not None).strip()


def _resolve_extraction_pdf_path(
    paper_id_value: str,
    pdf_path: str | None,
    metadata_db_path: str,
    pdf_base_dir: str,
) -> Path:
    candidate: str | None = pdf_path
    if not candidate:
        with MetadataDB(metadata_db_path) as db:
            paper = db.resolve_paper(paper_id_value)
            if paper:
                candidate = _paper_local_pdf_path(paper, pdf_base_dir)
    if not candidate:
        candidate = find_pdf_path(paper_id_value, "", pdf_base_dir)
    if not candidate:
        raise HTTPException(status_code=404, detail=f"PDF not found for {paper_id_value}")

    path = Path(candidate)
    if not path.is_absolute():
        direct = path
        under_base = Path(pdf_base_dir) / path
        path = direct if direct.exists() else under_base
    resolved = path.resolve()
    base = Path(pdf_base_dir).resolve()
    if base not in [resolved, *resolved.parents]:
        raise HTTPException(status_code=400, detail="PDF path must be inside the configured PDF library.")
    if not resolved.exists() or resolved.suffix.lower() != ".pdf":
        raise HTTPException(status_code=404, detail=f"PDF file not found: {path}")
    return resolved


def _parse_pdf_for_extraction(pdf_path: Path, paper_id_value: str, parser_name: str | None):
    requested_parser = parser_name or "auto"
    forced_parser: ParserType | None = None
    if parser_name:
        try:
            forced_parser = ParserType(parser_name)
        except ValueError as exc:
            raise HTTPException(
                status_code=400,
                detail={
                    "message": "Unknown parser",
                    "paper_id": paper_id_value,
                    "pdf_path": str(pdf_path),
                    "parser": parser_name,
                },
            ) from exc
    try:
        return pm.parser_router.parse(str(pdf_path), paper_id_value, force_parser=forced_parser)
    except Exception as exc:
        raise HTTPException(
            status_code=500,
            detail={
                "message": "PDF parsing failed",
                "paper_id": paper_id_value,
                "pdf_path": str(pdf_path),
                "parser": requested_parser,
                "error": str(exc),
            },
        ) from exc


def _parsed_document_metadata(parsed: Any) -> dict[str, Any]:
    metadata = getattr(parsed, "metadata", None)
    if metadata is None:
        metadata = getattr(parsed, "meta", None)
    return metadata if isinstance(metadata, dict) else {}


def _extraction_overrides(request: Any) -> dict[str, Any]:
    overrides: dict[str, Any] = {}
    for key in [
        "model",
        "temperature",
        "top_p",
        "max_tokens",
        "context_size",
        "extraction_mode",
        "context_policy",
        "allow_context_fallback",
    ]:
        value = getattr(request, key, None)
        if value is not None and value != "":
            overrides[key] = value
    return overrides


def _selected_model(provider: str | None, model: str | None) -> str:
    if model:
        return model
    try:
        return pm.llm_router.provider_default_model(provider)
    except Exception:
        return "unknown"


def _year_from_extraction_result(result: Any) -> int | None:
    coverage = getattr(result, "temporal_coverage", {}) or {}
    value = coverage.get("paper_year") if isinstance(coverage, dict) else None
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _extraction_result_payload(result: Any) -> dict[str, Any]:
    diagnostics = getattr(result, "extraction_diagnostics", {}) or {}
    return {
        "paper_id": getattr(result, "paper_id", ""),
        "paper_type": getattr(result, "paper_type", "unknown"),
        "concepts": getattr(result, "concepts", []) or [],
        "methods": getattr(result, "methods", []) or [],
        "concept_candidates": getattr(result, "concept_candidates", []) or [],
        "method_candidates": getattr(result, "method_candidates", []) or [],
        "relations": getattr(result, "relations", []) or [],
        "claims": getattr(result, "claims", []) or [],
        "cross_domain_hints": getattr(result, "cross_domain_hints", []) or [],
        "terminology_conflicts": getattr(result, "terminology_conflicts", []) or [],
        "temporal_coverage": getattr(result, "temporal_coverage", {}) or {},
        "mathematical_content": getattr(result, "mathematical_content", {}) or {},
        "language_detected": getattr(result, "language_detected", "unknown"),
        "quality_warnings": getattr(result, "quality_warnings", []) or [],
        "metadata_status": getattr(result, "metadata_status", "valid"),
        "blocking_errors": getattr(result, "blocking_errors", []) or [],
        "candidate_count": getattr(result, "candidate_count", 0) or 0,
        "extraction_diagnostics": diagnostics,
        "context_diagnostics": diagnostics.get("context_diagnostics") if isinstance(diagnostics, dict) else {},
        "raw_response": getattr(result, "raw_response", None),
    }


def _load_vocabulary(vocabulary_path: str) -> VocabularyManager:
    path = Path(vocabulary_path)
    if not path.exists():
        return VocabularyManager()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return VocabularyManager()
    return VocabularyManager.from_dict(data if isinstance(data, dict) else {})


def _save_vocabulary(vocabulary: VocabularyManager, vocabulary_path: str) -> None:
    path = Path(vocabulary_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(vocabulary.to_dict(), indent=2, ensure_ascii=False), encoding="utf-8")

