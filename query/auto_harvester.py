"""Lightweight auto-harvester for the research tree.

Searches for papers related to a question and inserts them into the project
so subsequent answers can use them. Deliberately minimal dependencies — no
api/ imports to avoid circular imports with product_main.
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import httpx

from harvester.arxiv_client import ArxivClient
from harvester.semantic_scholar_client import SemanticScholarClient
from storage.file_manager import FileManager
from storage.metadata_db import MetadataDB

_PROJECTS_PATH = Path("data/projects.json")
_USER_AGENT = "ScienceKG/auto-harvest (local)"


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
) -> bool:
    pdf_url = paper.get("pdf_url")
    if not pdf_url:
        return False
    canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
    title = str(paper.get("title") or canonical_id)
    try:
        response = await client.get(str(pdf_url), follow_redirects=True, timeout=20.0)
        response.raise_for_status()
        if _looks_like_pdf(response.content, response.headers.get("content-type", "")):
            storage.save_pdf(
                canonical_id,
                response.content,
                version=int(paper.get("version") or 1),
                display_name=title,
                source=str(paper.get("source") or "auto-harvest"),
            )
            return True
    except Exception:
        pass
    return False


async def harvest_for_question(
    question: str,
    project_id: str | None,
    db_path: str = "data/metadata.duckdb",
    pdf_base_dir: str = "data/pdfs",
    projects_path: str = "data/projects.json",
    sources: list[str] | None = None,
    max_papers: int = 3,
) -> list[dict[str, Any]]:
    """Search for papers relevant to *question*, insert into DB, attach to project.

    Returns list of dicts with at least ``{"id": str, "title": str}`` for each inserted paper.
    """
    sources = sources or ["arxiv", "semantic_scholar"]
    results: list[dict[str, Any]] = []

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

    tasks = []
    if "arxiv" in sources:
        tasks.append(_search_arxiv())
    if "semantic_scholar" in sources:
        tasks.append(_search_ss())
    if tasks:
        await asyncio.gather(*tasks)

    if not results:
        return []

    # Dedupe by title/doi, take top max_papers
    seen: set[str] = set()
    unique: list[dict[str, Any]] = []
    for r in results:
        key = str(r.get("doi") or r.get("title") or "").lower().strip()
        if key and key not in seen:
            seen.add(key)
            unique.append(r)
    unique = unique[:max_papers]

    storage = FileManager(pdf_base_dir)
    inserted: list[dict[str, Any]] = []

    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=20.0) as client:
        with MetadataDB(db_path) as db:
            for paper in unique:
                canonical_id = str(paper.get("id") or f"{paper.get('source')}:{paper.get('source_id')}")
                title = str(paper.get("title") or canonical_id)
                abstract = str(paper.get("abstract") or "")
                try:
                    db.insert_paper(paper)
                    inserted.append({"id": canonical_id, "title": title})
                except Exception:
                    continue
                has_pdf = await _download_pdf_if_available(paper, storage, client)
                # Store a synthetic extraction so the KG retriever can find this paper
                # immediately via entity/claim search (title as concept, abstract as claim).
                # This is superseded once the user runs a full Phase-3 extraction.
                try:
                    existing = db._execute(
                        "SELECT id FROM extraction_results WHERE paper_id = ? AND extraction_status = 'success' LIMIT 1",
                        [canonical_id],
                    ).fetchone()
                    if existing is None:
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

    inserted_ids = [r["id"] for r in inserted]
    if project_id and inserted_ids:
        proj_path = Path(projects_path)
        projects = _load_projects(proj_path)
        if project_id in projects:
            existing = set(projects[project_id])
            projects[project_id] = list(existing | set(inserted_ids))
            _save_projects(projects, proj_path)

    return inserted


async def harvest_grey_sources_for_question(
    question: str,
    project_id: str | None,
    db_path: str = "data/metadata.duckdb",
    max_sources: int = 3,
) -> list[dict[str, Any]]:
    """Search the web for *question*, fetch + sanitize pages, save as grey sources.

    Returns list of saved grey source records (each with at least id, title, url).
    """
    from research.sanitize import sanitize_web_text
    from research.search_provider import load_research_config, run_web_search

    try:
        config = load_research_config()
        hits = await run_web_search(question, config, max_results=max_sources + 2)
    except Exception:
        return []

    results: list[dict[str, Any]] = []
    async with httpx.AsyncClient(headers={"User-Agent": _USER_AGENT}, timeout=20.0) as client:
        with MetadataDB(db_path) as db:
            for hit in hits[:max_sources]:
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
                        },
                    )
                    results.append(record)
                except Exception:
                    continue
    return results
