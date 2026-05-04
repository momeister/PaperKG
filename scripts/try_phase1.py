from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path

import httpx
import yaml


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from harvester.arxiv_client import ArxivClient, ArxivClientConfig
from harvester.deduplication import deduplicate_papers
from harvester.openalex_client import OpenAlexClient, OpenAlexConfig
from harvester.papers_with_code_client import PapersWithCodeClient, PapersWithCodeConfig
from harvester.semantic_scholar_client import SemanticScholarClient, SemanticScholarConfig
from harvester.unpaywall_client import UnpaywallClient, UnpaywallConfig
from storage.metadata_db import MetadataDB
from storage.file_manager import FileManager


def _load_config(config_path: Path) -> dict:
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _normalize_s2_paper(paper: dict) -> dict:
    external_ids = paper.get("externalIds") or {}
    open_access_pdf = paper.get("openAccessPdf") or {}
    return {
        "source": "semantic_scholar",
        "source_id": str(paper.get("paperId") or paper.get("corpusId") or "unknown"),
        "version": 1,
        "title": paper.get("title") or "",
        "abstract": paper.get("abstract") or "",
        "authors": [a.get("name", "") for a in paper.get("authors", [])],
        "year": paper.get("year"),
        "doi": external_ids.get("DOI") or paper.get("doi"),
        "pdf_url": open_access_pdf.get("url"),
        "landing_page_url": paper.get("url"),
        "raw": paper,
    }


def _normalize_openalex_work(work: dict) -> dict:
    ids = work.get("ids") or {}
    doi = ids.get("doi") or work.get("doi")
    if isinstance(doi, str):
        doi = doi.removeprefix("https://doi.org/")
    oa_location = work.get("best_oa_location") or {}
    authorships = work.get("authorships") or []
    return {
        "source": "openalex",
        "source_id": str(work.get("id") or "unknown"),
        "version": 1,
        "title": work.get("title") or "",
        "abstract": "",
        "authors": [
            (authorship.get("author") or {}).get("display_name", "")
            for authorship in authorships
        ],
        "year": work.get("publication_year"),
        "doi": doi,
        "pdf_url": oa_location.get("pdf_url"),
        "landing_page_url": work.get("id"),
        "raw": work,
    }


def _extract_first_doi(records: list[dict]) -> str | None:
    for record in records:
        doi = record.get("doi")
        if doi:
            return str(doi)
    return None


async def _download_pdfs(file_manager: FileManager, records: list[dict]) -> tuple[int, int, int]:
    downloaded = 0
    skipped = 0
    failed = 0
    print("\nDownloading PDFs...")
    async with httpx.AsyncClient(timeout=60.0) as client:
        for paper in records:
            url = paper.get("pdf_url")
            pid = paper.get("id") or f"{paper['source']}:{paper['source_id']}"
            version = paper.get("version") or 1
            if not url:
                print(f" - skipped (no pdf_url): {pid}")
                skipped += 1
                continue
            if file_manager.exists(pid, version):
                print(f" - skipped (already exists): {pid} v{version}")
                skipped += 1
                continue
            try:
                resp = await client.get(url)
                resp.raise_for_status()
                file_manager.save_pdf(pid, resp.content, version)
                print(f" - downloaded: {pid} v{version}")
                downloaded += 1
            except Exception as exc:
                print(f" - failed to download {pid}: {exc}")
                failed += 1
    return downloaded, skipped, failed


async def run_demo(query: str, max_results: int, download: bool = False, full_phase1: bool = False) -> None:
    db_path = PROJECT_ROOT / "data" / "metadata.duckdb"
    pdf_dir = PROJECT_ROOT / "data" / "pdfs"
    config = _load_config(PROJECT_ROOT / "config.yaml")
    harvester_cfg = config.get("harvester", {})

    metadata_db = MetadataDB(str(db_path))
    file_manager = FileManager(pdf_dir)
    arxiv = ArxivClient(ArxivClientConfig())
    s2_cfg = harvester_cfg.get("semantic_scholar", {})
    semantic_scholar = SemanticScholarClient(
        SemanticScholarConfig(api_key=s2_cfg.get("api_key") or os.getenv("SEMANTIC_SCHOLAR_API_KEY"))
    )
    oa_cfg = harvester_cfg.get("openalex", {})
    openalex = OpenAlexClient(OpenAlexConfig(api_key=oa_cfg.get("api_key") or os.getenv("OPENALEX_API_KEY")))
    pwc_cfg = harvester_cfg.get("papers_with_code", {})
    papers_with_code = PapersWithCodeClient(PapersWithCodeConfig(token=pwc_cfg.get("token") or os.getenv("PAPERS_WITH_CODE_TOKEN")))
    unpaywall = None

    up_cfg = harvester_cfg.get("unpaywall", {})
    unpaywall_email = os.getenv("UNPAYWALL_EMAIL") or up_cfg.get("email")
    if unpaywall_email and unpaywall_email != "your-email@example.com":
        unpaywall = UnpaywallClient(UnpaywallConfig(email=unpaywall_email))

    try:
        print(f"Query: {query}")
        print(f"Full Phase 1 Demo: {full_phase1}")

        combined_records: list[dict] = []

        arxiv_records = await arxiv.search(query, max_results=max_results)
        combined_records.extend(arxiv_records)
        print(f"[OK] arXiv fetched: {len(arxiv_records)}")

        if full_phase1:
            try:
                s2_data = await semantic_scholar.search_papers(
                    query,
                    limit=min(max_results, 10),
                    fields="paperId,title,abstract,authors,year,externalIds,openAccessPdf,url",
                )
                s2_records = [_normalize_s2_paper(p) for p in s2_data.get("data", [])]
                combined_records.extend(s2_records)
                print(f"[OK] Semantic Scholar fetched: {len(s2_records)}")
            except Exception as exc:
                print(f"[WARN] Semantic Scholar unavailable: {exc}")

            try:
                oa_data = await openalex.list_works(search=query, per_page=min(max_results, 10), page=1)
                oa_records = [_normalize_openalex_work(w) for w in oa_data.get("results", [])]
                combined_records.extend(oa_records)
                print(f"[OK] OpenAlex fetched: {len(oa_records)}")
            except Exception as exc:
                print(f"[WARN] OpenAlex unavailable: {exc}")

            try:
                pwc_data = await papers_with_code.search_papers(query, page=1, items_per_page=min(max_results, 10))
                pwc_count = len(pwc_data.get("results", []))
                print(f"[OK] PapersWithCode responded: {pwc_count} results")
            except Exception as exc:
                print(f"[WARN] PapersWithCode unavailable: {exc}")

            doi_candidate = _extract_first_doi(combined_records)
            if unpaywall and doi_candidate:
                try:
                    oa_url = await unpaywall.best_oa_url(doi_candidate)
                    print(f"[OK] Unpaywall checked DOI {doi_candidate}: {oa_url}")
                except Exception as exc:
                    print(f"[WARN] Unpaywall unavailable: {exc}")
            elif not unpaywall:
                print("[INFO] Unpaywall skipped (set UNPAYWALL_EMAIL or config.harvester.unpaywall.email)")
            else:
                print("[INFO] Unpaywall skipped (no DOI found in fetched records)")

        unique_papers, decisions = deduplicate_papers(combined_records)

        inserted = metadata_db.batch_insert_papers(unique_papers)
        for decision in decisions:
            keep_id = decision.keep.get("id") or f"{decision.keep['source']}:{decision.keep['source_id']}"
            for dropped in decision.dropped:
                dropped_id = dropped.get("id") or f"{dropped['source']}:{dropped['source_id']}"
                metadata_db.log_dedup(keep_id, dropped_id, decision.reason)

        print(f"Fetched total (all sources): {len(combined_records)}")
        print(f"Unique: {len(unique_papers)}")
        print(f"Inserted into DuckDB: {inserted}")
        print(f"Dedup decisions: {len(decisions)}")
        print(f"Metadata DB: {db_path}")
        print(f"PDF directory: {pdf_dir}")
        print(f"Stored papers in DB now: {metadata_db.count_papers()}")
        print()

        for index, paper in enumerate(unique_papers[:5], start=1):
            paper_id = paper.get("id") or f"{paper['source']}:{paper['source_id']}"
            storage_path = file_manager.get_storage_path(paper_id, paper.get("version"))
            print(f"{index}. {paper['title']}")
            print(f"   id: {paper_id}")
            print(f"   year: {paper.get('year')}")
            print(f"   doi: {paper.get('doi')}")
            print(f"   pdf_url: {paper.get('pdf_url')}")
            print(f"   storage_path: {storage_path}")

        if len(unique_papers) > 5:
            print(f"\n... {len(unique_papers) - 5} weitere Einträge wurden gespeichert.")

        if download:
            downloaded, skipped, failed = await _download_pdfs(file_manager, unique_papers)
            print(f"Download summary: downloaded={downloaded}, skipped={skipped}, failed={failed}")

        print("\nPhase 1 summary:")
        print("[OK] Harvester APIs queried (optional APIs fail-soft)")
        print("[OK] Deduplication executed")
        print("[OK] Metadata persisted in DuckDB")
        print("[OK] FileManager paths generated")
        if download:
            print("[OK] PDF download attempted")
    finally:
        await arxiv.close()
        await semantic_scholar.close()
        await openalex.close()
        await papers_with_code.close()
        if unpaywall is not None:
            await unpaywall.close()
        metadata_db.close()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a Phase 1 ScienceKG demo")
    parser.add_argument("query", nargs="?", default="machine learning")
    parser.add_argument("--max-results", type=int, default=10)
    parser.add_argument("--download", action="store_true", help="Download PDFs for fetched papers")
    parser.add_argument(
        "--full-phase1",
        action="store_true",
        help="Also query Semantic Scholar, OpenAlex, PapersWithCode and Unpaywall checks",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    asyncio.run(run_demo(args.query, args.max_results, download=args.download, full_phase1=args.full_phase1))


if __name__ == "__main__":
    main()
