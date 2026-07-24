"""Harvest: Quellen-Suche, PDF-Downloads und Referenz-Extraktion + Normalizer.

Welche Quellen es gibt, steht in ``harvester/source_registry.py`` — diese Datei
faechert die Suche darueber auf und liefert den Katalog unter ``GET /harvest/sources``.

Split out of api/product_main.py. Behaviour unchanged. Patchbare Namen laufen
ueber pm.<name>: _run_harvest_search (Test-Patch), httpx.AsyncClient,
_resolve_extraction_pdf_path/_parse_pdf_for_extraction (Extraction-Helfer).
"""
from __future__ import annotations

import asyncio
import os
import re
from typing import Any

import httpx
import yaml
from fastapi import APIRouter
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable singletons + geteilte Helfer
from api.routers.projects import _attach_papers_to_project
from extraction.reference_parser import extract_reference_section, split_reference_entries
from harvester.ads_client import AdsApiKeyMissing, AdsClient, AdsConfig
from harvester.arxiv_client import ArxivClient
from harvester.core_client import CoreApiKeyMissing, CoreClient, CoreConfig
from harvester.crossref_client import CrossrefClient, CrossrefConfig
from harvester.dblp_client import DblpClient, DblpConfig
from harvester.doab_client import DoabClient, DoabConfig, doab_metadata
from harvester.doaj_client import DoajClient, DoajConfig
from harvester.eric_client import EricClient, EricConfig
from harvester.europepmc_client import EuropePMCClient, EuropePMCConfig
from harvester.hal_client import HalClient, HalConfig
from harvester.openaire_client import OpenAireClient, OpenAireConfig
from harvester.openalex_client import OpenAlexClient
from harvester.semantic_scholar_client import SemanticScholarClient
from harvester.source_registry import DEFAULT_SOURCES, catalog as harvest_source_catalog
from harvester.url_guard import is_safe_public_url
from quality.pdf_resolver import BenchmarkPdfResolver, _looks_like_pdf
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_PDF_BASE_DIR = "data/pdfs"

router = APIRouter()


class HarvestSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    sources: list[str] = list(DEFAULT_SOURCES)
    max_results: int = Field(default=10, ge=1, le=50)


class HarvestDownloadRequest(BaseModel):
    papers: list[dict[str, Any]]
    download_pdfs: bool = True
    project_id: str | None = None
    projects_path: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR


class ReferenceExtractRequest(BaseModel):
    paper_id: str = Field(min_length=1, max_length=240)
    pdf_path: str | None = Field(default=None, max_length=1000)
    parser: str | None = Field(default=None, max_length=80)
    max_references: int = Field(default=40, ge=1, le=200)
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR



@router.get("/harvest/sources")
def harvest_sources() -> dict[str, Any]:
    """Katalog der waehlbaren Suchquellen inkl. fachlicher Gruppen."""
    return harvest_source_catalog()


@router.post("/harvest/search")
async def harvest_search(request: HarvestSearchRequest) -> dict[str, Any]:
    results, warnings = await _run_harvest_search(request.query, request.sources, request.max_results)
    return {"query": request.query, "results": results, "warnings": warnings}


async def _fetch_one_pdf(
    paper: dict[str, Any],
    client: httpx.AsyncClient,
    storage: FileManager,
    resolver: BenchmarkPdfResolver,
    semaphore: asyncio.Semaphore,
) -> dict[str, Any]:
    """Download (or attempt to locate) the PDF for a single paper. Returns a result dict."""
    async with semaphore:
        canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
        title = str(paper.get("title") or paper.get("id") or canonical_id)
        doi = paper.get("doi")
        saved_path: str | None = None
        detail: str | None = None

        direct_url = paper.get("pdf_url")
        if direct_url and not await asyncio.to_thread(is_safe_public_url, str(direct_url)):
            detail = "Direkt-Link verweist nicht auf eine öffentliche Adresse"
            direct_url = None
        if direct_url:
            try:
                response = await client.get(str(direct_url))
                response.raise_for_status()
                if _looks_like_pdf(response.content, response.headers.get("content-type", "")):
                    saved_path = str(storage.save_pdf(
                        canonical_id,
                        response.content,
                        version=int(paper.get("version") or 1),
                        display_name=str(paper.get("title") or canonical_id),
                        source=str(paper.get("source") or "paper"),
                    ))
                else:
                    detail = "Direkt-Link lieferte kein PDF"
            except Exception as exc:  # noqa: BLE001
                detail = f"Direkt-Link fehlgeschlagen: {exc}"

        if not saved_path and (doi or title):
            try:
                resolution = await asyncio.to_thread(
                    resolver.resolve,
                    paper_id=canonical_id,
                    title=title,
                    doi=str(doi) if doi else None,
                    download_missing=True,
                )
                if resolution.pdf_path:
                    saved_path = resolution.pdf_path
                elif resolution.warnings:
                    detail = "; ".join(resolution.warnings[:2])
            except Exception as exc:  # noqa: BLE001
                detail = f"OA-Suche fehlgeschlagen: {exc}"

        return {
            "canonical_id": canonical_id,
            "title": title,
            "doi": doi,
            "landing_page_url": paper.get("landing_page_url"),
            "remote_pdf_url": paper.get("pdf_url"),
            "saved_path": saved_path,
            "detail": detail,
        }


def _external_paper_url(landing: Any, doi: Any, remote_pdf: Any) -> str | None:
    """Dauerhafter Link zum Original — dieselbe Reihenfolge wie ``GET /paper/meta``
    und ``externalPaperUrl`` im Frontend: Landing-Page, sonst DOI, sonst PDF-Link.

    Wichtig fuer Paper ohne freien Volltext: darueber kommt man spaeter noch an
    das Paper (Bibliothek, Kauf), auch wenn nie ein PDF geladen wurde.
    """
    landing_text = str(landing or "").strip()
    if landing_text.startswith("http"):
        return landing_text
    doi_text = str(doi or "").strip().replace("https://doi.org/", "")
    if doi_text:
        return f"https://doi.org/{doi_text}"
    remote_text = str(remote_pdf or "").strip()
    return remote_text if remote_text.startswith("http") else None


@router.post("/harvest/download")
async def harvest_download(request: HarvestDownloadRequest) -> dict[str, Any]:
    storage = FileManager(request.pdf_base_dir)
    resolver = BenchmarkPdfResolver(
        request.pdf_base_dir,
        contact_email=_unpaywall_email() or _crossref_mailto(),
    )
    download_headers = {"User-Agent": "ScienceKG/harvest (local-development)"}

    # Insert all paper metadata first (fast, no external calls).
    attached_ids: list[str] = []
    with MetadataDB(request.metadata_db_path) as db:
        for paper in request.papers:
            db.insert_paper(paper)
            canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
            attached_ids.append(canonical_id)

    # If PDF download not requested, return immediately.
    if not request.download_pdfs:
        results = [
            {
                "paper_id": str(p.get("id") or f"{p.get('source')}:{p.get('source_id')}"),
                "title": str(p.get("title") or p.get("id") or ""),
                "status": "inserted",
            }
            for p in request.papers
        ]
        project_paper_ids = _attach_papers_to_project(request.project_id, attached_ids, request.projects_path)
        return {
            "inserted": len(results),
            "downloaded": 0,
            "failed_downloads": [],
            "results": results,
            "project_id": request.project_id,
            "attached": bool(project_paper_ids),
        }

    # Download PDFs concurrently (semaphore limits parallel external requests).
    semaphore = asyncio.Semaphore(8)
    async with pm.httpx.AsyncClient(timeout=60.0, follow_redirects=True, headers=download_headers) as client:
        fetch_results = await asyncio.gather(
            *[_fetch_one_pdf(paper, client, storage, resolver, semaphore) for paper in request.papers],
            return_exceptions=True,
        )

    # Persist PDF paths and assemble response.
    inserted = len(request.papers)
    downloaded = 0
    failed_downloads: list[str] = []
    results: list[dict[str, Any]] = []

    with MetadataDB(request.metadata_db_path) as db:
        for fetch_result in fetch_results:
            if isinstance(fetch_result, BaseException):
                results.append({"paper_id": "unknown", "title": "unknown", "status": "failed", "detail": str(fetch_result)})
                failed_downloads.append(str(fetch_result))
                continue
            canonical_id = fetch_result["canonical_id"]
            title = fetch_result["title"]
            doi = fetch_result["doi"]
            saved_path = fetch_result["saved_path"]
            detail = fetch_result["detail"]
            if saved_path:
                db.update_paper_metadata_if_missing(canonical_id, pdf_path=str(saved_path))
                downloaded += 1
                results.append({"paper_id": canonical_id, "title": title, "status": "downloaded"})
            else:
                landing_url = _external_paper_url(
                    fetch_result.get("landing_page_url"),
                    doi,
                    fetch_result.get("remote_pdf_url"),
                )
                status = "failed" if detail and "fehlgeschlagen" in detail else "no_pdf"
                if status == "failed":
                    failed_downloads.append(f"{title}: {detail}")
                results.append({
                    "paper_id": canonical_id,
                    "title": title,
                    "status": status,
                    "detail": detail,
                    "landing_url": landing_url,
                })

    project_paper_ids = _attach_papers_to_project(request.project_id, attached_ids, request.projects_path)
    return {
        "inserted": inserted,
        "downloaded": downloaded,
        "failed_downloads": failed_downloads,
        "results": results,
        "project_id": request.project_id,
        "attached": bool(project_paper_ids),
    }


async def _match_references_to_crossref(
    reference_strings: list[str], max_references: int
) -> list[dict[str, Any]]:
    """Match free-text reference strings to Crossref works (best effort, sequential)."""
    client = CrossrefClient(CrossrefConfig(mailto=_crossref_mailto()))
    matched: list[dict[str, Any]] = []
    seen_dois: set[str] = set()
    try:
        for reference in reference_strings[:max_references]:
            try:
                work = await client.match_reference(reference)
            except Exception:
                work = None
            if not work:
                continue
            normalized = _normalize_crossref_work(work)
            doi_key = str(normalized.get("doi") or normalized.get("source_id") or "").lower()
            if not doi_key or doi_key in seen_dois:
                continue
            seen_dois.add(doi_key)
            normalized["reference_string"] = reference
            matched.append(normalized)
    finally:
        await client.close()
    return matched


@router.post("/papers/references/extract")
async def extract_paper_references(request: ReferenceExtractRequest) -> dict[str, Any]:
    """Parse an uploaded/known PDF, detect its cited references and match them to
    Crossref so the user can choose which referenced papers to download.

    Does NOT download anything; the frontend sends the chosen subset to
    /harvest/download.
    """
    pdf_path = pm._resolve_extraction_pdf_path(
        request.paper_id, request.pdf_path, request.metadata_db_path, request.pdf_base_dir
    )
    parsed = pm._parse_pdf_for_extraction(pdf_path, request.paper_id, request.parser)
    section = extract_reference_section(parsed.text)
    reference_strings = split_reference_entries(section)
    references = await _match_references_to_crossref(reference_strings, request.max_references)
    return {
        "paper_id": request.paper_id,
        "references_detected": len(reference_strings),
        "references_matched": len(references),
        "references": references,
    }


def _existing_library_keys(metadata_db_path: str) -> set[str]:
    keys: set[str] = set()
    try:
        with MetadataDB(metadata_db_path) as db:
            for paper in db.list_papers(limit=50000):
                if paper.get("doi"):
                    keys.add(str(paper["doi"]).lower())
                if paper.get("id"):
                    keys.add(str(paper["id"]).lower())
                if paper.get("title"):
                    keys.add(re.sub(r"\s+", " ", str(paper["title"]).lower()).strip())
    except Exception:
        return keys
    return keys



_HARVESTER_CONFIG_CACHE: dict[str, Any] | None = None


def _load_harvester_config() -> dict[str, Any]:
    """Load and cache the `harvester:` section of config.yaml (env-resolved keys).

    .env has already been loaded into os.environ by the module-level LLMRouter, so
    `*_env` references resolve via os.getenv here.
    """
    global _HARVESTER_CONFIG_CACHE
    if _HARVESTER_CONFIG_CACHE is not None:
        return _HARVESTER_CONFIG_CACHE
    cfg: dict[str, Any] = {}
    try:
        with open("config.yaml", "r", encoding="utf-8") as fh:
            cfg = (yaml.safe_load(fh) or {}).get("harvester", {}) or {}
    except FileNotFoundError:
        cfg = {}
    _HARVESTER_CONFIG_CACHE = cfg
    return cfg


def _harvester_section(name: str) -> dict[str, Any]:
    section = _load_harvester_config().get(name, {})
    return section if isinstance(section, dict) else {}


def _resolved_key(section: dict[str, Any], env_default: str) -> str | None:
    env_name = section.get("api_key_env") or env_default
    return os.getenv(env_name) or section.get("api_key")


def _unpaywall_email() -> str | None:
    section = _harvester_section("unpaywall")
    env_name = section.get("email_env") or "UNPAYWALL_EMAIL"
    email = os.getenv(env_name) or section.get("email")
    if not email or "@example.com" in str(email):
        return None
    return str(email)


def _crossref_mailto() -> str | None:
    section = _harvester_section("crossref")
    env_name = section.get("mailto_env") or "CROSSREF_MAILTO"
    return os.getenv(env_name) or section.get("mailto") or _unpaywall_email()


async def _run_harvest_search(query: str, sources: list[str], max_results: int) -> tuple[list[dict[str, Any]], list[str]]:
    normalized_sources = {source.lower() for source in sources}
    results: list[dict[str, Any]] = []
    warnings: list[str] = []

    async def run_source(source: str) -> None:
        try:
            if source == "arxiv":
                client = ArxivClient()
                try:
                    results.extend(await client.search(query, max_results=max_results))
                finally:
                    await client.close()
            elif source == "semantic_scholar":
                client = SemanticScholarClient()
                try:
                    payload = await client.search_papers(
                        query,
                        limit=max_results,
                        fields="paperId,corpusId,title,abstract,authors,year,externalIds,openAccessPdf,url",
                    )
                    results.extend(_normalize_semantic_scholar_paper(item) for item in payload.get("data", []))
                finally:
                    await client.close()
            elif source == "openalex":
                client = OpenAlexClient()
                try:
                    payload = await client.list_works(search=query, per_page=max_results)
                    results.extend(_normalize_openalex_work(item) for item in payload.get("results", []))
                finally:
                    await client.close()
            elif source == "crossref":
                client = CrossrefClient(CrossrefConfig(mailto=_crossref_mailto()))
                try:
                    items = await client.search_works(query, rows=max_results)
                    results.extend(_normalize_crossref_work(item) for item in items)
                finally:
                    await client.close()
            elif source == "europepmc":
                client = EuropePMCClient(EuropePMCConfig())
                try:
                    items = await client.search(query, page_size=max_results)
                    results.extend(_normalize_europepmc_result(item) for item in items)
                finally:
                    await client.close()
            elif source == "biorxiv":
                # Native bioRxiv API has no keyword search; use Europe PMC preprint filter.
                client = EuropePMCClient(EuropePMCConfig())
                try:
                    preprint_query = f'({query}) AND SRC:PPR AND (PUBLISHER:"bioRxiv" OR PUBLISHER:"medRxiv")'
                    items = await client.search(preprint_query, page_size=max_results)
                    results.extend(_normalize_europepmc_result(item, source="biorxiv") for item in items)
                finally:
                    await client.close()
            elif source == "core":
                section = _harvester_section("core")
                client = CoreClient(CoreConfig(api_key=_resolved_key(section, "CORE_API_KEY")))
                try:
                    items = await client.search_works(query, limit=max_results)
                    results.extend(_normalize_core_work(item) for item in items)
                finally:
                    await client.close()
            elif source == "doaj":
                client = DoajClient(DoajConfig())
                try:
                    items = await client.search_articles(query, page_size=max_results)
                    results.extend(_normalize_doaj_article(item) for item in items)
                finally:
                    await client.close()
            elif source == "openaire":
                client = OpenAireClient(OpenAireConfig())
                try:
                    items = await client.search_publications(query, limit=max_results)
                    results.extend(_normalize_openaire_product(item) for item in items)
                finally:
                    await client.close()
            elif source == "dblp":
                client = DblpClient(DblpConfig())
                try:
                    items = await client.search_publications(query, limit=max_results)
                    results.extend(_normalize_dblp_publication(item) for item in items)
                finally:
                    await client.close()
            elif source == "ads":
                section = _harvester_section("ads")
                client = AdsClient(AdsConfig(api_key=_resolved_key(section, "ADS_API_KEY")))
                try:
                    items = await client.search_documents(query, limit=max_results)
                    results.extend(_normalize_ads_document(item) for item in items)
                finally:
                    await client.close()
            elif source == "osf":
                # Die OSF-API kennt nur Titel-Teilstringsuche; ueber Crossref, auf den
                # OSF-DOI-Praefix eingegrenzt, gibt es echte Relevanz und Abstracts.
                client = CrossrefClient(CrossrefConfig(mailto=_crossref_mailto()))
                try:
                    items = await client.search_works(query, rows=max_results, filters="prefix:10.31219")
                    results.extend(_normalize_crossref_work(item, source="osf") for item in items)
                finally:
                    await client.close()
            elif source == "eric":
                client = EricClient(EricConfig())
                try:
                    items = await client.search_records(query, limit=max_results)
                    results.extend(_normalize_eric_record(item) for item in items)
                finally:
                    await client.close()
            elif source == "doab":
                client = DoabClient(DoabConfig())
                try:
                    items = await client.search_books(query, limit=max_results)
                    results.extend(_normalize_doab_book(item) for item in items)
                finally:
                    await client.close()
            elif source == "hal":
                client = HalClient(HalConfig())
                try:
                    items = await client.search_documents(query, limit=max_results)
                    results.extend(_normalize_hal_document(item) for item in items)
                finally:
                    await client.close()
            else:
                # Macht Drift zwischen source_registry und dieser Verzweigung sichtbar,
                # statt die Quelle stillschweigend zu verschlucken.
                warnings.append(f"{source}: unbekannte Quelle")
        except (CoreApiKeyMissing, AdsApiKeyMissing) as exc:
            warnings.append(f"{source}: {exc}")
        except Exception as exc:
            warnings.append(f"{source}: {exc}")

    await asyncio.gather(*(run_source(source) for source in sorted(normalized_sources)))
    return _dedupe_harvest_results(results), warnings


def _dedupe_harvest_results(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for result in results:
        key = str(result.get("doi") or result.get("id") or f"{result.get('source')}:{result.get('source_id')}").lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output


def _openalex_abstract(work: dict[str, Any]) -> str:
    """OpenAlex liefert den Abstract nur als invertierten Index (Wort -> Positionen).

    Ohne diese Rueckwandlung haetten OpenAlex-Paper immer einen leeren Abstract —
    und damit weder eine Vorschau im Import noch eine Abstract-only-Extraktion,
    wenn kein PDF verfuegbar ist.
    """
    direct = str(work.get("abstract") or "").strip()
    if direct:
        return direct
    inverted = work.get("abstract_inverted_index")
    if not isinstance(inverted, dict) or not inverted:
        return ""
    positions: list[tuple[int, str]] = []
    for word, indexes in inverted.items():
        if not isinstance(indexes, list):
            continue
        for index in indexes:
            try:
                positions.append((int(index), str(word)))
            except (TypeError, ValueError):
                continue
    if not positions:
        return ""
    positions.sort(key=lambda item: item[0])
    return " ".join(word for _, word in positions)


def _normalize_openalex_work(work: dict[str, Any]) -> dict[str, Any]:
    openalex_id = str(work.get("id") or "").rsplit("/", 1)[-1]
    return {
        "source": "openalex",
        "source_id": openalex_id,
        "title": work.get("title") or "",
        "abstract": _openalex_abstract(work),
        "authors": [
            item.get("author", {}).get("display_name")
            for item in work.get("authorships", [])
            if isinstance(item, dict)
        ],
        "year": work.get("publication_year"),
        "doi": work.get("doi"),
        "pdf_url": ((work.get("best_oa_location") or {}).get("pdf_url") if isinstance(work.get("best_oa_location"), dict) else None),
        "landing_page_url": work.get("doi") or work.get("id"),
        "has_full_text": bool((work.get("best_oa_location") or {}).get("pdf_url")) if isinstance(work.get("best_oa_location"), dict) else False,
    }


def _normalize_semantic_scholar_paper(paper: dict[str, Any]) -> dict[str, Any]:
    external_ids = paper.get("externalIds") or {}
    open_access_pdf = paper.get("openAccessPdf") or {}
    return {
        "source": "semantic_scholar",
        "source_id": str(paper.get("paperId") or paper.get("corpusId") or "unknown"),
        "version": 1,
        "title": paper.get("title") or "",
        "abstract": paper.get("abstract") or "",
        "authors": [author.get("name", "") for author in paper.get("authors", []) if isinstance(author, dict)],
        "year": paper.get("year"),
        "doi": external_ids.get("DOI") or paper.get("doi"),
        "pdf_url": open_access_pdf.get("url") if isinstance(open_access_pdf, dict) else None,
        "landing_page_url": paper.get("url"),
        "has_full_text": bool(open_access_pdf.get("url")) if isinstance(open_access_pdf, dict) else False,
        "raw": paper,
    }


def _crossref_title(work: dict[str, Any]) -> str:
    title = work.get("title")
    if isinstance(title, list):
        return (title[0] if title else "") or ""
    return str(title or "")


def _crossref_year(work: dict[str, Any]) -> int | None:
    for key in ("published", "published-print", "published-online", "issued", "created"):
        parts = (work.get(key) or {}).get("date-parts") if isinstance(work.get(key), dict) else None
        if parts and isinstance(parts, list) and parts[0]:
            try:
                return int(parts[0][0])
            except (ValueError, TypeError, IndexError):
                continue
    return None


def _normalize_crossref_work(work: dict[str, Any], source: str = "crossref") -> dict[str, Any]:
    doi = work.get("DOI")
    authors = []
    for author in work.get("author", []) or []:
        if isinstance(author, dict):
            name = " ".join(part for part in [author.get("given"), author.get("family")] if part)
            if name:
                authors.append(name)
    pdf_url = None
    for link in work.get("link", []) or []:
        if isinstance(link, dict) and link.get("content-type") == "application/pdf":
            pdf_url = link.get("URL")
            break
    abstract = re.sub(r"<[^>]+>", "", str(work.get("abstract") or "")).strip()
    return {
        "source": source,
        "source_id": str(doi or work.get("URL") or "unknown"),
        "title": _crossref_title(work),
        "abstract": abstract,
        "authors": authors,
        "year": _crossref_year(work),
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else work.get("URL")),
        "has_full_text": bool(pdf_url),
    }


def _normalize_europepmc_result(item: dict[str, Any], source: str = "europepmc") -> dict[str, Any]:
    doi = item.get("doi")
    pdf_url = None
    for url_item in (item.get("fullTextUrlList") or {}).get("fullTextUrl", []) or []:
        if not isinstance(url_item, dict):
            continue
        if url_item.get("documentStyle") == "pdf" or str(url_item.get("url", "")).lower().endswith(".pdf"):
            pdf_url = url_item.get("url")
            break
    pmcid = item.get("pmcid")
    if not pdf_url and pmcid:
        pdf_url = f"https://www.ebi.ac.uk/europepmc/webservices/rest/{pmcid}/fullTextXML"
    authors = []
    author_string = item.get("authorString")
    if author_string:
        authors = [name.strip() for name in str(author_string).split(",") if name.strip()]
    return {
        "source": source,
        "source_id": str(item.get("id") or doi or pmcid or "unknown"),
        "title": str(item.get("title") or ""),
        "abstract": str(item.get("abstractText") or ""),
        "authors": authors,
        "year": int(item["pubYear"]) if str(item.get("pubYear") or "").isdigit() else None,
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else None),
        "has_full_text": str(item.get("isOpenAccess") or "").upper() == "Y" or bool(pdf_url),
    }


def _normalize_core_work(work: dict[str, Any]) -> dict[str, Any]:
    doi = work.get("doi")
    authors = [
        author.get("name", "")
        for author in (work.get("authors") or [])
        if isinstance(author, dict) and author.get("name")
    ]
    pdf_url = work.get("downloadUrl") or work.get("fullTextLink")
    year = work.get("yearPublished") or work.get("publishedDate")
    try:
        year_int = int(str(year)[:4]) if year else None
    except (ValueError, TypeError):
        year_int = None
    return {
        "source": "core",
        "source_id": str(work.get("id") or doi or "unknown"),
        "title": str(work.get("title") or ""),
        "abstract": str(work.get("abstract") or ""),
        "authors": authors,
        "year": year_int,
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else work.get("sourceFulltextUrls")),
        "has_full_text": bool(pdf_url),
    }


def _first_str(value: Any) -> str:
    """Solr-/DSpace-Felder liefern oft Listen — nimm den ersten sinnvollen Wert."""
    if isinstance(value, list):
        for item in value:
            text = str(item or "").strip()
            if text:
                return text
        return ""
    return str(value or "").strip()


def _as_year(value: Any) -> int | None:
    text = str(value or "").strip()[:4]
    return int(text) if text.isdigit() else None


def _normalize_openaire_product(product: dict[str, Any]) -> dict[str, Any]:
    doi = None
    for pid in product.get("pids") or []:
        if isinstance(pid, dict) and str(pid.get("scheme") or "").lower() == "doi":
            doi = pid.get("value")
            break
    pdf_url = None
    landing = None
    for instance in product.get("instances") or []:
        if not isinstance(instance, dict):
            continue
        for url in instance.get("urls") or []:
            text = str(url or "")
            if not landing:
                landing = text
            if text.lower().endswith(".pdf"):
                pdf_url = text
                break
    year = None
    published = str(product.get("publicationDate") or "")[:4]
    if published.isdigit():
        year = int(published)
    return {
        "source": "openaire",
        "source_id": str(product.get("id") or doi or "unknown"),
        "title": str(product.get("mainTitle") or ""),
        "abstract": _first_str(product.get("descriptions")),
        "authors": [
            str(author.get("fullName") or "")
            for author in product.get("authors") or []
            if isinstance(author, dict) and author.get("fullName")
        ],
        "year": year,
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else landing),
        "has_full_text": bool(pdf_url),
    }


def _normalize_dblp_publication(info: dict[str, Any]) -> dict[str, Any]:
    authors = info.get("authors") or {}
    raw_authors = authors.get("author") if isinstance(authors, dict) else None
    if isinstance(raw_authors, dict):
        raw_authors = [raw_authors]
    doi = info.get("doi")
    landing = info.get("ee") or info.get("url")
    return {
        "source": "dblp",
        "source_id": str(info.get("key") or doi or info.get("url") or "unknown"),
        "title": str(info.get("title") or "").rstrip("."),
        # DBLP ist eine Bibliografie ohne Abstracts; der Volltext-Resolver holt
        # spaeter ueber die DOI, was frei verfuegbar ist.
        "abstract": "",
        "authors": [str(item.get("text") or "") for item in raw_authors or [] if isinstance(item, dict)],
        "year": int(info["year"]) if str(info.get("year") or "").isdigit() else None,
        "doi": doi,
        "pdf_url": None,
        "landing_page_url": (f"https://doi.org/{doi}" if doi else landing),
        "has_full_text": False,
    }


def _normalize_ads_document(doc: dict[str, Any]) -> dict[str, Any]:
    bibcode = str(doc.get("bibcode") or "")
    doi = _first_str(doc.get("doi")) or None
    return {
        "source": "ads",
        "source_id": bibcode or (doi or "unknown"),
        "title": _first_str(doc.get("title")),
        "abstract": str(doc.get("abstract") or ""),
        "authors": [str(name) for name in doc.get("author") or []],
        "year": _as_year(doc.get("year")),
        "doi": doi,
        "pdf_url": None,
        "landing_page_url": (
            f"https://ui.adsabs.harvard.edu/abs/{bibcode}/abstract" if bibcode else (f"https://doi.org/{doi}" if doi else None)
        ),
        "has_full_text": False,
    }


def _normalize_eric_record(doc: dict[str, Any]) -> dict[str, Any]:
    record_id = str(doc.get("id") or "")
    has_fulltext = bool(doc.get("e_fulltextauth"))
    year = doc.get("publicationdateyear")
    return {
        "source": "eric",
        "source_id": record_id or "unknown",
        "title": _first_str(doc.get("title")),
        "abstract": str(doc.get("description") or ""),
        "authors": [str(name) for name in doc.get("author") or []],
        "year": _as_year(year),
        "doi": None,
        "pdf_url": (f"https://files.eric.ed.gov/fulltext/{record_id}.pdf" if has_fulltext and record_id else None),
        "landing_page_url": (f"https://eric.ed.gov/?id={record_id}" if record_id else None),
        "has_full_text": has_fulltext,
    }


def _normalize_doab_book(item: dict[str, Any]) -> dict[str, Any]:
    meta = doab_metadata(item)
    doi = None
    for key in ("oapen.identifier.doi", "dc.identifier.doi"):
        if meta.get(key):
            doi = meta[key][0].replace("https://doi.org/", "")
            break
    landing = (meta.get("dc.identifier.uri") or [None])[0]
    handle = str(item.get("handle") or "")
    year = ""
    for key in ("dc.date.issued", "dc.date.available", "dc.date.accessioned"):
        if meta.get(key):
            year = meta[key][0][:4]
            break
    return {
        "source": "doab",
        "source_id": str(item.get("uuid") or handle or "unknown"),
        "title": (meta.get("dc.title") or [str(item.get("name") or "")])[0],
        "abstract": (meta.get("dc.description.abstract") or [""])[0],
        "authors": meta.get("dc.contributor.author") or [],
        "year": _as_year(year),
        "doi": doi,
        "pdf_url": None,
        "landing_page_url": landing or (f"https://directory.doabooks.org/handle/{handle}" if handle else None),
        "has_full_text": False,
    }


def _normalize_hal_document(doc: dict[str, Any]) -> dict[str, Any]:
    doi = _first_str(doc.get("doiId_s")) or None
    pdf_url = _first_str(doc.get("fileMain_s")) or None
    landing = _first_str(doc.get("uri_s")) or None
    year = doc.get("producedDateY_i")
    return {
        "source": "hal",
        "source_id": str(doc.get("docid") or doi or "unknown"),
        "title": _first_str(doc.get("title_s")),
        "abstract": _first_str(doc.get("abstract_s")),
        "authors": [str(name) for name in doc.get("authFullName_s") or []],
        "year": _as_year(year),
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": landing or (f"https://doi.org/{doi}" if doi else None),
        "has_full_text": bool(pdf_url),
    }


def _normalize_doaj_article(article: dict[str, Any]) -> dict[str, Any]:
    bib = article.get("bibjson", {}) if isinstance(article.get("bibjson"), dict) else {}
    doi = None
    pdf_url = None
    landing = None
    for ident in bib.get("identifier", []) or []:
        if isinstance(ident, dict) and ident.get("type") == "doi":
            doi = ident.get("id")
    for link in bib.get("link", []) or []:
        if not isinstance(link, dict):
            continue
        if link.get("type") == "fulltext":
            landing = link.get("url")
            if str(link.get("content_type", "")).lower() == "pdf" or str(link.get("url", "")).lower().endswith(".pdf"):
                pdf_url = link.get("url")
    authors = [a.get("name", "") for a in bib.get("author", []) or [] if isinstance(a, dict) and a.get("name")]
    return {
        "source": "doaj",
        "source_id": str(article.get("id") or doi or "unknown"),
        "title": str(bib.get("title") or ""),
        "abstract": str(bib.get("abstract") or ""),
        "authors": authors,
        "year": int(bib["year"]) if str(bib.get("year") or "").isdigit() else None,
        "doi": doi,
        "pdf_url": pdf_url,
        "landing_page_url": landing or (f"https://doi.org/{doi}" if doi else None),
        "has_full_text": bool(pdf_url or landing),
    }
