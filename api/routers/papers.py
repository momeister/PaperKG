"""Papers: Library-Liste, Meta, On-Demand-Ingest, Upload, Delete + Titel/Garble-Helfer.

Split out of api/product_main.py. Behaviour unchanged. Die Titel-/Anzeige-Helfer
(_clean_display_text, _paper_list_view, _maybe_fix_garbled_pdf_title, ...) werden
auch von extraction/grey-sources genutzt und von dort importiert.
Patchbare Namen (Tests patchen sie auf product_main) laufen ueber pm.<name>:
extraction_pipeline, httpx.AsyncClient, _infer_pdf_title_from_bytes.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from fastapi import APIRouter, BackgroundTasks, HTTPException, Query, Request
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons + patch surface
from api.routers.projects import (
    _attach_papers_to_project,
    _load_primary_papers,
    _load_projects,
    _paper_matches_query,
    _project_memberships,
    _projects_path,
    _save_primary_papers,
    _save_projects,
)
from query.auto_harvester import ingest_paper_record
from query.source_verifier import build_pdf_index, find_pdf_path
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_PDF_BASE_DIR = "data/pdfs"

router = APIRouter()


class PaperIngestRequest(BaseModel):
    paper_id: str = Field(min_length=1)
    project_id: str | None = None
    provider: str | None = None
    model: str | None = None
    projects_path: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


@router.get("/papers")
def list_papers(
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    projects_path: str | None = None,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    query: str = "",
    project_id: str | None = None,
    has_full_text: bool | None = None,
    extraction_status: str | None = None,
    sort: str = "added_desc",
    limit: int = Query(default=50, ge=1, le=5000),
    offset: int = Query(default=0, ge=0),
) -> dict[str, Any]:
    projects = _load_projects(_projects_path(projects_path))
    selected_ids = set(projects.get(project_id, [])) if project_id else None
    memberships = _project_memberships(projects)

    with MetadataDB(metadata_db_path) as db:
        papers = db.list_papers(limit=50000)
        latest_by_paper = _latest_extraction_statuses(db)

    pdf_index = build_pdf_index(pdf_base_dir)
    filtered = []
    for paper in papers:
        pid = str(paper.get("id") or "")
        if selected_ids is not None and pid not in selected_ids:
            continue
        if has_full_text is not None and bool(paper.get("has_full_text")) != has_full_text:
            continue
        latest_status = latest_by_paper.get(pid)
        if extraction_status and latest_status != extraction_status:
            continue
        if query and not _paper_matches_query(paper, query):
            continue
        filtered.append(
            {
                **_paper_list_view(paper, pdf_base_dir, pdf_index=pdf_index),
                "project_ids": sorted(memberships.get(pid, [])),
                "latest_extraction_status": latest_status,
            }
        )

    filtered.sort(key=_paper_sort_key(sort), reverse=sort.endswith("_desc"))
    page = filtered[offset : offset + limit]
    return {"items": page, "total": len(filtered), "limit": limit, "offset": offset}


def _paper_list_view(
    paper: dict[str, Any],
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    pdf_index: list[tuple[str, str]] | None = None,
) -> dict[str, Any]:
    paper_id_value = _clean_display_text(paper.get("id") or paper.get("paper_id") or paper.get("source_id"))
    source_id = _clean_display_text(paper.get("source_id"))
    title = _clean_display_text(paper.get("title"))
    local_pdf_path = _paper_local_pdf_path(paper, pdf_base_dir, pdf_index=pdf_index)
    pdf_filename = Path(local_pdf_path).name if local_pdf_path else _paper_filename_from_value(paper.get("pdf_url"))
    display_title = title or _clean_pdf_title(pdf_filename) or paper_id_value or source_id or "Unbenanntes PDF"
    return {
        **paper,
        "display_title": display_title,
        "pdf_filename": pdf_filename or None,
        "pdf_path": local_pdf_path,
        "has_full_text": bool(local_pdf_path),
    }


def _paper_local_pdf_path(
    paper: dict[str, Any],
    pdf_base_dir: str,
    pdf_index: list[tuple[str, str]] | None = None,
) -> str | None:
    pdf_url = _clean_display_text(paper.get("pdf_url"))
    if pdf_url and not re.match(r"^[a-z][a-z0-9+.-]*://", pdf_url, flags=re.IGNORECASE):
        path = Path(str(pdf_url).replace("\\", "/"))
        try:
            if path.exists():
                return str(path)
        except OSError:
            pass
    paper_id_value = _clean_display_text(paper.get("id") or paper.get("paper_id") or paper.get("source_id"))
    title = _clean_display_text(paper.get("title"))
    return find_pdf_path(paper_id_value, title, pdf_base_dir, index=pdf_index) if paper_id_value else None


@router.get("/paper/meta")
def paper_meta(
    paper_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
) -> dict[str, Any]:
    """Return metadata for a cited paper that may have no local PDF, so the UI can show its
    abstract and a link to the original source for verification."""
    with MetadataDB(metadata_db_path) as db:
        paper = db.get_paper(paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
    doi = _clean_display_text(paper.get("doi"))
    landing = _clean_display_text(paper.get("landing_page_url"))
    pdf_url = _clean_display_text(paper.get("pdf_url"))
    remote_pdf = pdf_url if re.match(r"^https?://", pdf_url, flags=re.IGNORECASE) else ""
    external_url = landing or (f"https://doi.org/{doi}" if doi else "") or remote_pdf
    return {
        "paper_id": _clean_display_text(paper.get("id")) or paper_id,
        "title": _clean_display_text(paper.get("title")),
        "abstract": _clean_display_text(paper.get("abstract")),
        "doi": doi or None,
        "pdf_url": remote_pdf or None,
        "landing_page_url": landing or None,
        "has_local_pdf": _paper_local_pdf_path(paper, pdf_base_dir) is not None,
        "external_url": external_url or None,
    }


def _ingest_extract_background(
    paper_id: str,
    pdf_path: str,
    metadata_db_path: str,
    provider: str | None,
    model: str | None,
) -> None:
    """Run Phase-3 extraction for a freshly-ingested paper after the response is sent."""
    try:
        from parsing.parser_router import ParserRouter
        from query.auto_harvester import _extract_pdf_into_db

        parser_router = ParserRouter()
        with MetadataDB(metadata_db_path) as db:
            try:
                existing = db._execute(
                    "SELECT id FROM extraction_results WHERE paper_id = ? AND extraction_status = 'success' LIMIT 1",
                    [paper_id],
                ).fetchone()
            except Exception:
                existing = None
            if existing is None:
                _extract_pdf_into_db(
                    db, pm.extraction_pipeline, parser_router, paper_id, pdf_path, provider, model
                )
    except Exception:
        pass


@router.post("/paper/ingest")
async def paper_ingest(request: PaperIngestRequest, background_tasks: BackgroundTasks) -> dict[str, Any]:
    """On-demand download + extract for a cited paper that has no local PDF yet.

    Lets a citation click resolve to a real, project-local PDF instead of the "no PDF" limbo:
    downloads the PDF (resolving an open-access URL by DOI if needed), attaches it to the
    project, and kicks off Phase-3 extraction in the background so the click stays fast. When no
    PDF is downloadable the response carries ``external_url`` so the UI can fall back to the
    web/grey-source view.
    """
    with MetadataDB(request.metadata_db_path) as db:
        paper = db.get_paper(request.paper_id)
    if not paper:
        raise HTTPException(status_code=404, detail=f"Paper not found: {request.paper_id}")

    storage = FileManager(request.pdf_base_dir)
    headers = {"User-Agent": "ScienceKG/ingest (local-development)"}
    async with pm.httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=headers) as client:
        with MetadataDB(request.metadata_db_path) as db:
            result = await ingest_paper_record(
                paper, db, storage, client,
                provider=request.provider, model=request.model,
                extract=False,  # extraction is scheduled as a background task below
            )

    canonical_id = str(result.get("id") or request.paper_id)
    has_pdf = bool(result.get("has_local_pdf"))
    pdf_path = result.get("pdf_path")

    attached = False
    if has_pdf:
        attached = bool(_attach_papers_to_project(request.project_id, [canonical_id], request.projects_path))
        if pdf_path:
            background_tasks.add_task(
                _ingest_extract_background,
                canonical_id, str(pdf_path), request.metadata_db_path,
                request.provider, request.model,
            )

    # Pre-download metadata still holds the remote/landing URL for the grey fallback.
    doi = _clean_display_text(paper.get("doi"))
    landing = _clean_display_text(paper.get("landing_page_url"))
    remote_pdf_raw = _clean_display_text(paper.get("pdf_url"))
    remote_pdf = remote_pdf_raw if re.match(r"^https?://", remote_pdf_raw, flags=re.IGNORECASE) else ""
    external_url = landing or (f"https://doi.org/{doi}" if doi else "") or remote_pdf
    return {
        "paper_id": canonical_id,
        "title": result.get("title"),
        "has_local_pdf": has_pdf,
        "attached": attached,
        "external_url": external_url or None,
    }


def _clean_display_text(value: Any) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return "" if text.lower() in {"", "none", "null", "nan", "undefined"} else text


def _paper_filename_from_value(value: Any) -> str:
    raw = _clean_display_text(value)
    if not raw:
        return ""
    without_query = re.split(r"[?#]", raw, maxsplit=1)[0] or raw
    return Path(without_query.replace("\\", "/")).name


def _clean_pdf_title(value: Any) -> str:
    filename = _paper_filename_from_value(value)
    if not filename:
        return ""
    return _clean_display_text(re.sub(r"\.pdf$", "", filename, flags=re.IGNORECASE).replace("_", " ").replace("-", " "))


_TITLE_LINE_BLOCKLIST = (
    "arxiv:", "doi:", "abstract", "introduction", "keywords", "page ",
    "©", "http://", "https://", "preprint", "submitted", "draft",
    "running head", "downloaded from", "issn", "isbn", "vol.", "volume",
)

_GENERIC_TITLE_STEMS = frozenset(
    {"file", "document", "upload", "pdf", "paper", "doc", "untitled", "new", "temp", "tmp", "download"}
)

# Below this ratio of word-like tokens (>= 3 letters) to whitespace-split tokens, parsed
# text is treated as broken/garbled extraction (e.g. font-encoding issues, scanned noise).
_GARBLED_TEXT_WORD_RATIO_THRESHOLD = 0.4
_GARBLED_TEXT_SAMPLE_LEN = 2000


def _text_looks_garbled(text: str) -> bool:
    sample = text[:_GARBLED_TEXT_SAMPLE_LEN].strip()
    tokens = sample.split()
    if len(tokens) < 20:
        return False
    word_like = re.findall(r"[a-zA-Z]{3,}", sample)
    return (len(word_like) / len(tokens)) < _GARBLED_TEXT_WORD_RATIO_THRESHOLD


def _maybe_fix_garbled_pdf_title(*, paper_id: str, text: str, pdf_path: Path, metadata_db_path: str) -> None:
    """When extraction text looks garbled and the stored title is generic/filename-derived,
    overwrite it with a title inferred from the PDF itself (metadata or heading line) — that
    inference is far more likely correct than anything derived from broken extracted text."""
    if not _text_looks_garbled(text):
        return
    with MetadataDB(metadata_db_path) as db:
        paper = db.get_paper(paper_id)
        if not paper:
            return
        current_title = (paper.get("title") or "").strip()
        current_stem = current_title.lower().strip()
        filename_stem = pdf_path.stem.lower().strip()
        looks_generic = current_stem in _GENERIC_TITLE_STEMS or current_stem == filename_stem
        if not looks_generic:
            return
        try:
            content = pdf_path.read_bytes()
        except OSError:
            return
        inferred_title = pm._infer_pdf_title_from_bytes(content).strip()
        if inferred_title and inferred_title != current_title:
            db.update_paper_title(paper_id, inferred_title[:240])


def _looks_like_title_line(line: str) -> bool:
    text = _clean_display_text(line)
    if not (15 <= len(text) <= 200):
        return False
    if not re.search(r"[a-zA-Z]{3,}", text):
        return False
    lowered = text.lower()
    if any(lowered.startswith(prefix) for prefix in _TITLE_LINE_BLOCKLIST):
        return False
    return True


_HTML_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.IGNORECASE | re.DOTALL)
_HTML_H1_RE = re.compile(r"<h1[^>]*>(.*?)</h1>", re.IGNORECASE | re.DOTALL)
_HTML_TAG_RE = re.compile(r"<[^>]+>")


def _infer_title_from_html(html: str) -> str:
    """Cheap regex-based <title>/<h1> heuristic — no LLM call, keep URL ingest fast."""
    for pattern in (_HTML_TITLE_RE, _HTML_H1_RE):
        match = pattern.search(html)
        if not match:
            continue
        candidate = _clean_display_text(_HTML_TAG_RE.sub(" ", match.group(1)))
        if candidate:
            return candidate
    return ""


def _infer_pdf_title_from_bytes(content: bytes) -> str:
    """Best-effort title extraction from PDF metadata or the first page's heading."""
    try:
        from pypdf import PdfReader
    except Exception:
        return ""
    try:
        reader = PdfReader(io.BytesIO(content))
    except Exception:
        return ""

    try:
        meta_title = _clean_display_text(getattr(reader.metadata, "title", None) if reader.metadata else "")
    except Exception:
        meta_title = ""
    if meta_title and meta_title.lower() not in {"untitled", "untitled document"} and _looks_like_title_line(meta_title):
        return meta_title

    try:
        first_page_text = reader.pages[0].extract_text() or ""
    except Exception:
        first_page_text = ""
    for raw_line in first_page_text.splitlines():
        line = _clean_display_text(raw_line)
        if _looks_like_title_line(line):
            return line
    return ""


@router.post("/papers/upload")
async def upload_paper_pdf(
    request: Request,
    paper_id: str | None = None,
    title: str | None = None,
    source: str = "upload",
    project_id: str | None = None,
    projects_path: str | None = None,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
) -> dict[str, Any]:
    content = await request.body()
    if not content:
        raise HTTPException(status_code=400, detail="Upload body is empty.")

    filename = request.headers.get("x-filename") or title or paper_id or "uploaded-paper.pdf"
    stem = Path(filename).stem.lower().strip()
    inferred_id = paper_id or (f"upload-{__import__('uuid').uuid4().hex[:8]}" if stem in _GENERIC_TITLE_STEMS else Path(filename).stem)
    resolved_title = title or pm._infer_pdf_title_from_bytes(content) or Path(filename).stem
    storage = FileManager(pdf_base_dir)
    saved_path = storage.save_pdf(
        inferred_id,
        content,
        display_name=resolved_title,
        source=source,
    )
    with MetadataDB(metadata_db_path) as db:
        canonical_id = db.ensure_paper_record(
            inferred_id,
            title=resolved_title,
            pdf_path=str(saved_path),
            source=source,
            source_id=inferred_id,
        )
        paper = db.get_paper(canonical_id)
    project_paper_ids = _attach_papers_to_project(project_id, [canonical_id], projects_path)
    return {
        "paper": paper,
        "pdf_path": str(saved_path),
        "project_id": project_id,
        "attached": bool(project_paper_ids),
    }


@router.delete("/papers/{paper_id:path}")
def delete_paper(
    paper_id: str,
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH,
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR,
    projects_path: str | None = None,
) -> dict[str, Any]:
    with MetadataDB(metadata_db_path) as db:
        paper = db.get_paper(paper_id)
        if not paper:
            raise HTTPException(status_code=404, detail=f"Paper not found: {paper_id}")
        pdf_url = paper.get("pdf_url") or paper.get("pdf_path")
        deleted = db.delete_paper(paper_id)
    file_deleted = False
    if deleted and pdf_url:
        storage = FileManager(pdf_base_dir)
        file_deleted = storage.delete_by_path(str(pdf_url))
    if deleted:
        all_projects = _load_projects(_projects_path(projects_path))
        changed = False
        for pid, members in all_projects.items():
            if paper_id in members:
                all_projects[pid] = [m for m in members if m != paper_id]
                changed = True
        if changed:
            _save_projects(all_projects, _projects_path(projects_path))
        primary = _load_primary_papers()
        if any(v == paper_id for v in primary.values()):
            updated = {k: v for k, v in primary.items() if v != paper_id}
            _save_primary_papers(updated)
    return {"deleted": deleted, "file_deleted": file_deleted, "id": paper_id}


def _latest_extraction_statuses(db: MetadataDB) -> dict[str, str]:
    latest_by_paper: dict[str, str] = {}
    for extraction in db.list_extraction_statuses(limit=50000):
        pid = str(extraction.get("paper_id") or "")
        if pid and pid not in latest_by_paper:
            latest_by_paper[pid] = str(extraction.get("extraction_status") or "unknown")
    return latest_by_paper


def _paper_sort_key(sort: str):
    key_name = {
        "title_asc": "title",
        "title_desc": "title",
        "year_asc": "year",
        "year_desc": "year",
        "added_asc": "added_timestamp",
        "added_desc": "added_timestamp",
    }.get(sort, "added_timestamp")

    def key(paper: dict[str, Any]) -> Any:
        value = paper.get(key_name)
        if key_name == "year":
            return int(value or 0)
        return str(value or "").lower()

    return key
