"""Lightweight auto-harvester for the research tree.

Searches for papers related to a question and inserts them into the project
so subsequent answers can use them. Deliberately minimal dependencies — no
api/ imports at module level to avoid circular imports with product_main
(die Quellen-Aufaecherung wird zur Laufzeit importiert, mit Fallback).
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from harvester.arxiv_client import ArxivClient
from harvester.oa_resolver import resolve_oa_pdf_url
from harvester.semantic_scholar_client import SemanticScholarClient
from harvester.source_registry import TIER_RANK, source_tier
from harvester.url_guard import is_safe_public_url
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB

if TYPE_CHECKING:
    from query.llm_router import LLMRouter

_PROJECTS_PATH = Path("data/projects.json")
_USER_AGENT = "ScienceKG/auto-harvest (local)"

#: Stufe 1 der Auto-Recherche: breiter als arXiv+S2, aber ohne Key-pflichtige Quellen.
DEFAULT_SCIENTIFIC_SOURCES: tuple[str, ...] = (
    "arxiv",
    "semantic_scholar",
    "openalex",
    "crossref",
    "europepmc",
)


def _load_projects(path: Path) -> dict[str, list[str]]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return {str(k): [str(v) for v in vs] if isinstance(vs, list) else [] for k, vs in data.items() if isinstance(data, dict)}


def _save_projects(projects: dict[str, list[str]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(projects, indent=2, sort_keys=True), encoding="utf-8")


def _looks_like_pdf(content: bytes, content_type: str) -> bool:
    return content[:4] == b"%PDF" or "pdf" in content_type.lower()


async def _download_pdf_if_available(
    paper: dict[str, Any],
    storage: FileManager,
    client: httpx.AsyncClient,
) -> Path | None:
    """Download the paper's PDF if one can be located. Returns the saved path, else None.

    Tries the paper's own ``pdf_url`` first, then falls back to resolving an open-access PDF
    by DOI via Unpaywall — so harvested papers that only expose a landing page (e.g. many
    open-access journal articles) still end up with a real local PDF instead of metadata only.
    """
    canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
    title = str(paper.get("title") or canonical_id)

    async def _try(url: str) -> Path | None:
        if not await asyncio.to_thread(is_safe_public_url, str(url)):
            return None
        try:
            response = await client.get(str(url), follow_redirects=True, timeout=30.0)
            response.raise_for_status()
        except Exception:
            return None
        if not _looks_like_pdf(response.content, response.headers.get("content-type", "")):
            return None
        return storage.save_pdf(
            canonical_id,
            response.content,
            version=int(paper.get("version") or 1),
            display_name=title,
            source=str(paper.get("source") or "auto-harvest"),
        )

    candidates: list[str] = []
    if paper.get("pdf_url"):
        candidates.append(str(paper["pdf_url"]))
    oa_url = await resolve_oa_pdf_url(paper.get("doi"))
    if oa_url and oa_url not in candidates:
        candidates.append(oa_url)

    for url in candidates:
        saved = await _try(url)
        if saved is not None:
            return saved
    return None


async def ingest_paper_record(
    paper: dict[str, Any],
    db: MetadataDB,
    storage: FileManager,
    client: httpx.AsyncClient,
    *,
    extraction_pipeline: Any = None,
    parser_router: Any = None,
    provider: str | None = None,
    model: str | None = None,
    extract: bool = True,
) -> dict[str, Any]:
    """Download a paper's PDF, record the local path on its DB row, and run extraction.

    Shared by the bulk auto-harvest loop and the on-demand ``/paper/ingest`` endpoint so a
    cited-but-not-yet-downloaded paper can be turned into a fully local, extracted paper.
    Returns ``{"id", "title", "has_local_pdf", "pdf_path"}``. The paper is assumed to already
    exist in ``db`` (the caller inserts it).
    """
    canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
    title = str(paper.get("title") or canonical_id)
    abstract = str(paper.get("abstract") or "")

    pdf_path = await _download_pdf_if_available(paper, storage, client)
    if pdf_path is not None:
        # Persist the local path so has_local_pdf resolves without a directory scan.
        try:
            db.update_paper_metadata_if_missing(canonical_id, pdf_path=str(pdf_path))
        except Exception:
            pass

    if extract:
        # Skip if a successful extraction already exists for this paper.
        try:
            existing = db._execute(
                "SELECT id FROM extraction_results WHERE paper_id = ? AND extraction_status = 'success' LIMIT 1",
                [canonical_id],
            ).fetchone()
        except Exception:
            existing = None
        if existing is None:
            # Prefer a real Phase-3 extraction of the downloaded PDF; fall back to a synthetic
            # title+abstract extraction so the KG retriever can still find this paper.
            extracted = False
            if pdf_path is not None and extraction_pipeline is not None and parser_router is not None:
                try:
                    extracted = await asyncio.to_thread(
                        _extract_pdf_into_db, db, extraction_pipeline, parser_router,
                        canonical_id, str(pdf_path), provider, model,
                    )
                except Exception:
                    extracted = False
            if not extracted:
                try:
                    concepts = [{"label": title, "description": abstract[:400]}] if title else []
                    claims = [{"text": abstract[:600]}] if abstract else []
                    db.save_extraction_result(
                        paper_id=canonical_id,
                        llm_provider="auto-harvest",
                        llm_model="metadata",
                        concepts=concepts,
                        claims=claims,
                        raw_response=None,
                        error_message=None,
                    )
                except Exception:
                    pass

    return {
        "id": canonical_id,
        "title": title,
        "has_local_pdf": pdf_path is not None,
        "pdf_path": str(pdf_path) if pdf_path else None,
    }


def _extract_pdf_into_db(
    db: MetadataDB,
    pipeline: Any,
    parser_router: Any,
    canonical_id: str,
    pdf_path: str,
    provider: str | None,
    model: str | None,
) -> bool:
    """Run the full Phase-3 extraction on a downloaded PDF and persist the result.

    Returns True only when extraction produced a usable (non-failed) result. Any error
    is swallowed by the caller, which then falls back to the synthetic extraction.
    """
    from extraction.entity_extractor import extraction_failure_reason

    parsed = parser_router.parse(pdf_path, canonical_id)
    text = (getattr(parsed, "text", "") or "").strip()
    if not text:
        return False
    overrides = {"model": model} if model else None
    result = pipeline.process(canonical_id, text, provider=provider, overrides=overrides, link_concepts=False)
    failure = extraction_failure_reason(result)
    db.save_extraction_result(
        paper_id=canonical_id,
        llm_provider=provider or getattr(pipeline, "default_provider", "extraction") or "extraction",
        llm_model=model or "default",
        paper_type=getattr(result, "paper_type", None),
        concepts=result.concepts,
        methods=result.methods,
        concept_candidates=getattr(result, "concept_candidates", None),
        method_candidates=getattr(result, "method_candidates", None),
        relations=result.relations,
        claims=result.claims,
        cross_domain_hints=getattr(result, "cross_domain_hints", None),
        terminology_conflicts=getattr(result, "terminology_conflicts", None),
        temporal_coverage=getattr(result, "temporal_coverage", None),
        mathematical_content=getattr(result, "mathematical_content", None),
        raw_response=result.raw_response,
        error_message=failure,
    )
    return failure is None


async def _search_scientific_sources(question: str, sources: list[str], max_papers: int) -> list[dict[str, Any]]:
    """Dieselbe Quellen-Aufaecherung wie der Import (api/routers/harvest.py).

    Ohne sie kaeme die Auto-Recherche nur an arXiv und Semantic Scholar. Der Import
    liegt erst zur Laufzeit im Graph, damit query/ nicht am Modul-Import auf api/
    zeigt; schlaegt er fehl, faellt ``harvest_for_question`` auf den schlanken
    arXiv/S2-Pfad zurueck.
    """
    try:
        from api.routers.harvest import _run_harvest_search

        found, _warnings = await _run_harvest_search(question, sources, max_papers)
        return list(found)
    except Exception:
        return []


async def harvest_for_question(
    question: str,
    project_id: str | None,
    db_path: str = "data/metadata.duckdb",
    pdf_base_dir: str = "data/pdfs",
    projects_path: str = "data/projects.json",
    sources: list[str] | None = None,
    max_papers: int = 3,
    llm_router: "LLMRouter | None" = None,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict[str, Any]]:
    """Search for papers relevant to *question*, insert into DB, attach to project.

    When an ``llm_router`` is supplied, downloaded PDFs are run through the full Phase-3
    extraction pipeline (entities/claims), falling back to a synthetic title+abstract
    extraction only when there is no PDF or extraction fails.

    Returns list of dicts with at least ``{"id": str, "title": str}`` for each inserted paper.
    """
    sources = sources or list(DEFAULT_SCIENTIFIC_SOURCES)
    results: list[dict[str, Any]] = list(await _search_scientific_sources(question, list(sources), max_papers))

    async def _search_arxiv() -> None:
        client = ArxivClient()
        try:
            found = await client.search(question, max_results=max_papers)
            results.extend(found)
        except Exception:
            pass
        finally:
            await client.close()

    async def _search_ss() -> None:
        client = SemanticScholarClient()
        try:
            payload = await client.search_papers(
                question,
                limit=max_papers,
                fields="paperId,corpusId,title,abstract,authors,year,externalIds,openAccessPdf,url",
            )
            for item in payload.get("data", []):
                external_ids = item.get("externalIds") or {}
                open_access_pdf = item.get("openAccessPdf") or {}
                results.append({
                    "source": "semantic_scholar",
                    "source_id": str(item.get("paperId") or item.get("corpusId") or "unknown"),
                    "version": 1,
                    "title": item.get("title") or "",
                    "abstract": item.get("abstract") or "",
                    "authors": [a.get("name", "") for a in (item.get("authors") or []) if isinstance(a, dict)],
                    "year": item.get("year"),
                    "doi": external_ids.get("DOI") or item.get("doi"),
                    "pdf_url": open_access_pdf.get("url") if isinstance(open_access_pdf, dict) else None,
                })
        except Exception:
            pass
        finally:
            await client.close()

    if not results:
        tasks = []
        if "arxiv" in sources:
            tasks.append(_search_arxiv())
        if "semantic_scholar" in sources:
            tasks.append(_search_ss())
        if tasks:
            await asyncio.gather(*tasks)

    if not results:
        return []

    # Dedupe by title/doi, take top max_papers. Begutachtete Quellen zuerst,
    # Preprints zuletzt — bei knappem Budget zaehlt die belastbarere Fundstelle.
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in sorted(results, key=lambda item: TIER_RANK.get(source_tier(str(item.get("source") or "")), 1)):
        key = str(r.get("doi") or r.get("title") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    unique = unique[:max_papers]

    storage = FileManager(pdf_base_dir)
    inserted: list[dict[str, Any]] = []

    # Build the full extraction pipeline once. Lazy import keeps api/ out of the import
    # graph (and the cost off module load); extraction/ + parsing/ are safe, non-circular.
    extraction_pipeline = None
    parser_router = None
    if llm_router is not None:
        try:
            from extraction.entity_linker import ExtractionPipeline
            from parsing.parser_router import ParserRouter

            extraction_pipeline = ExtractionPipeline(llm_router)
            parser_router = ParserRouter()
        except Exception:
            extraction_pipeline = None
            parser_router = None

    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=30.0) as client:
        with MetadataDB(db_path) as db:
            for paper in unique:
                canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
                title = str(paper.get("title") or canonical_id)
                try:
                    db.insert_paper(paper)
                except Exception:
                    continue
                inserted.append({"id": canonical_id, "title": title})
                # Download the PDF (resolving an OA URL by DOI if needed), record its local
                # path, and run Phase-3 (or synthetic) extraction — see ingest_paper_record.
                await ingest_paper_record(
                    paper, db, storage, client,
                    extraction_pipeline=extraction_pipeline,
                    parser_router=parser_router,
                    provider=provider,
                    model=model,
                    extract=True,
                )

    inserted_ids = [r["id"] for r in inserted]
    # Attach to the active project (creating its membership list if needed). Global mode
    # (empty id / "__all_papers__") is a no-op since those papers are globally visible.
    if project_id and project_id != "__all_papers__" and inserted_ids:
        proj_path = Path(projects_path)
        projects = _load_projects(proj_path)
        existing_members = list(projects.get(project_id, []))
        existing_set = set(existing_members)
        projects[project_id] = existing_members + [pid for pid in inserted_ids if pid not in existing_set]
        _save_projects(projects, proj_path)

    return inserted


async def harvest_grey_sources_for_question(
    question: str,
    project_id: str | None,
    db_path: str = "data/metadata.duckdb",
    max_sources: int = 3,
    tiers: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """Search the web for *question*, fetch + sanitize pages, save as grey sources.

    ``tiers`` grenzt auf Vertrauensstufen ein (``("trusted",)`` / ``("unknown",)``,
    siehe research/source_tiers.py) — so kann die Auto-Recherche erst
    vertrauenswuerdige Domains und erst danach den Rest des Webs heranziehen.
    ``None`` heisst: alle Stufen, wie bisher.

    Returns list of saved grey source records (each with at least id, title, url).
    """
    from research.sanitize import sanitize_web_text
    from research.search_provider import load_research_config, run_web_search

    try:
        config = load_research_config()
        # Mehr holen als noetig: der Tier-Filter siebt danach einen Teil weg.
        hits = await run_web_search(question, config, max_results=max_sources * 3 + 2)
    except Exception:
        return []

    if tiers:
        hits = [hit for hit in hits if getattr(hit, "tier", "unknown") in tiers]

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=20.0) as client:
        with MetadataDB(db_path) as db:
            for hit in hits[:max_sources]:
                if not await asyncio.to_thread(is_safe_public_url, str(hit.url)):
                    continue
                try:
                    resp = await client.get(str(hit.url), follow_redirects=True, timeout=15.0)
                    resp.raise_for_status()
                    full_text, _ = sanitize_web_text(resp.text)
                    record = db.add_grey_source(
                        str(project_id or "__all_papers__"),
                        {
                            "query": question,
                            "url": str(hit.url),
                            "title": hit.title or str(hit.url),
                            "summary": hit.snippet or "",
                            "full_text": full_text,
                            "status": "saved",
                            "trust_tier": getattr(hit, "tier", "unknown"),
                        },
                    )
                    results.append(record)
                except Exception:
                    continue
    return results
