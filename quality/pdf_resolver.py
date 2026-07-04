from __future__ import annotations

import hashlib
import os
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from harvester.arxiv_client import ArxivClient
from query.source_verifier import find_pdf_path
from storage.file_manager import FileManager


@dataclass
class PdfResolution:
    paper_id: str
    pdf_path: str | None
    provenance: dict[str, Any] = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)


class BenchmarkPdfResolver:
    """
    Resolve benchmark PDFs locally first, then optionally via free/open sources.

    The network path is intentionally conservative and records provenance for
    every downloaded file. It is only used when callers pass download_missing.
    """

    def __init__(
        self,
        pdf_base_dir: str = "data/pdfs",
        *,
        contact_email: str | None = None,
        timeout_seconds: float = 60.0,
    ) -> None:
        self.pdf_base_dir = pdf_base_dir
        self.file_manager = FileManager(pdf_base_dir)
        self.contact_email = contact_email or os.getenv("UNPAYWALL_EMAIL") or os.getenv("CROSSREF_MAILTO")
        self.timeout_seconds = float(timeout_seconds)
        self._last_request_by_source: dict[str, float] = {}

    def resolve(
        self,
        *,
        paper_id: str,
        title: str = "",
        doi: str | None = None,
        download_missing: bool = False,
    ) -> PdfResolution:
        local = find_pdf_path(paper_id, title, self.pdf_base_dir)
        if local is not None:
            return PdfResolution(
                paper_id=paper_id,
                pdf_path=str(local),
                provenance={"source": "local", "pdf_path": str(local)},
            )
        if not download_missing:
            return PdfResolution(paper_id=paper_id, pdf_path=None, warnings=["PDF not found locally."])

        candidates, source_warnings = self._candidate_urls(paper_id=paper_id, title=title, doi=doi)
        warnings: list[str] = []
        warnings.extend(source_warnings)
        for candidate in candidates:
            url = str(candidate.get("pdf_url") or "")
            if not url:
                continue
            source = str(candidate.get("source") or "unknown")
            try:
                self._throttle(source)
                pdf_bytes, content_type = self._download_pdf(url)
                if not _looks_like_pdf(pdf_bytes, content_type):
                    warnings.append(f"{source}: URL did not return a PDF")
                    continue
                version = _arxiv_version_from_url(url) or 1
                path = self.file_manager.save_pdf(
                    paper_id,
                    pdf_bytes,
                    version=version,
                    display_name=title or paper_id,
                    source=source,
                )
                provenance = {
                    **candidate,
                    "download_timestamp": datetime.now().isoformat(timespec="seconds"),
                    "sha256": hashlib.sha256(pdf_bytes).hexdigest(),
                    "size_bytes": len(pdf_bytes),
                    "content_type": content_type,
                    "pdf_path": str(path),
                }
                return PdfResolution(paper_id=paper_id, pdf_path=str(path), provenance=provenance, warnings=warnings)
            except Exception as exc:
                warnings.append(f"{source}: {exc}")
        return PdfResolution(paper_id=paper_id, pdf_path=None, warnings=warnings or ["No downloadable OA PDF found."])

    def _candidate_urls(self, *, paper_id: str, title: str, doi: str | None) -> tuple[list[dict[str, Any]], list[str]]:
        candidates: list[dict[str, Any]] = []
        warnings: list[str] = []
        arxiv_id = _extract_arxiv_id(paper_id) or _extract_arxiv_id(title)
        if arxiv_id:
            candidates.append(
                {
                    "source": "arxiv",
                    "pdf_url": ArxivClient.build_pdf_url(arxiv_id),
                    "landing_url": f"https://arxiv.org/abs/{arxiv_id}",
                    "license": None,
                }
            )
        if doi:
            for source_name, fn in [
                ("semantic_scholar", self._semantic_scholar_candidates),
                ("openalex", self._openalex_candidates),
                ("unpaywall", self._unpaywall_candidates),
                ("crossref", self._crossref_candidates),
                ("europe_pmc", self._europe_pmc_candidates),
            ]:
                try:
                    candidates.extend(fn(doi))
                except Exception as exc:
                    warnings.append(f"{source_name}: {exc}")
        core_key = os.getenv("CORE_API_KEY")
        if core_key and (doi or title):
            try:
                candidates.extend(self._core_candidates(doi=doi, title=title, api_key=core_key))
            except Exception as exc:
                warnings.append(f"core: {exc}")
        return _dedupe_candidates(candidates), warnings

    def _download_pdf(self, url: str) -> tuple[bytes, str]:
        from harvester.url_guard import assert_safe_public_url

        assert_safe_public_url(url)  # SSRF guard: candidate URLs come from external APIs
        headers = {"User-Agent": "ScienceKG-Benchmark/1.0"}
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True, headers=headers) as client:
            response = client.get(url)
            response.raise_for_status()
            return response.content, response.headers.get("content-type", "")

    def _json_get(self, source: str, url: str, params: dict[str, Any] | None = None, headers: dict[str, str] | None = None) -> dict[str, Any]:
        self._throttle(source)
        with httpx.Client(timeout=self.timeout_seconds, follow_redirects=True) as client:
            response = client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()

    def _semantic_scholar_candidates(self, doi: str) -> list[dict[str, Any]]:
        payload = self._json_get(
            "semantic_scholar",
            f"https://api.semanticscholar.org/graph/v1/paper/DOI:{doi}",
            params={"fields": "paperId,title,url,openAccessPdf,externalIds"},
        )
        oa_pdf = payload.get("openAccessPdf") if isinstance(payload.get("openAccessPdf"), dict) else {}
        return [
            {
                "source": "semantic_scholar",
                "pdf_url": oa_pdf.get("url"),
                "landing_url": payload.get("url"),
                "license": None,
            }
        ]

    def _openalex_candidates(self, doi: str) -> list[dict[str, Any]]:
        payload = self._json_get("openalex", f"https://api.openalex.org/works/doi:{doi}")
        locations = []
        for key in ["best_oa_location", "primary_location"]:
            if isinstance(payload.get(key), dict):
                locations.append(payload[key])
        locations.extend(location for location in payload.get("locations") or [] if isinstance(location, dict))
        return [
            {
                "source": "openalex",
                "pdf_url": location.get("pdf_url"),
                "landing_url": location.get("landing_page_url") or payload.get("id"),
                "license": location.get("license"),
            }
            for location in locations
            if location.get("pdf_url")
        ]

    def _unpaywall_candidates(self, doi: str) -> list[dict[str, Any]]:
        if not self.contact_email:
            return []
        payload = self._json_get(
            "unpaywall",
            f"https://api.unpaywall.org/v2/{doi}",
            params={"email": self.contact_email},
        )
        locations = []
        if isinstance(payload.get("best_oa_location"), dict):
            locations.append(payload["best_oa_location"])
        locations.extend(location for location in payload.get("oa_locations") or [] if isinstance(location, dict))
        return [
            {
                "source": "unpaywall",
                "pdf_url": location.get("url_for_pdf"),
                "landing_url": location.get("url"),
                "license": location.get("license"),
            }
            for location in locations
            if location.get("url_for_pdf")
        ]

    def _crossref_candidates(self, doi: str) -> list[dict[str, Any]]:
        payload = self._json_get("crossref", f"https://api.crossref.org/works/{doi}")
        message = payload.get("message") if isinstance(payload.get("message"), dict) else {}
        links = message.get("link") or []
        return [
            {
                "source": "crossref",
                "pdf_url": link.get("URL"),
                "landing_url": message.get("URL"),
                "license": (message.get("license") or [{}])[0].get("URL") if message.get("license") else None,
            }
            for link in links
            if isinstance(link, dict) and str(link.get("content-type") or "").lower() == "application/pdf"
        ]

    def _europe_pmc_candidates(self, doi: str) -> list[dict[str, Any]]:
        payload = self._json_get(
            "europe_pmc",
            "https://www.ebi.ac.uk/europepmc/webservices/rest/search",
            params={"query": f'DOI:"{doi}"', "format": "json", "resultType": "core", "pageSize": 1},
        )
        results = ((payload.get("resultList") or {}).get("result") or [])
        candidates: list[dict[str, Any]] = []
        for item in results:
            if not isinstance(item, dict):
                continue
            for url in item.get("fullTextUrlList", {}).get("fullTextUrl", []) or []:
                if isinstance(url, dict) and str(url.get("documentStyle") or "").lower() == "pdf":
                    candidates.append(
                        {
                            "source": "europe_pmc",
                            "pdf_url": url.get("url"),
                            "landing_url": item.get("pmcid") or item.get("id"),
                            "license": item.get("license"),
                        }
                    )
        return candidates

    def _core_candidates(self, *, doi: str | None, title: str, api_key: str) -> list[dict[str, Any]]:
        query = f'doi:"{doi}"' if doi else title
        payload = self._json_get(
            "core",
            "https://api.core.ac.uk/v3/search/works",
            params={"q": query, "limit": 5},
            headers={"Authorization": f"Bearer {api_key}"},
        )
        return [
            {
                "source": "core",
                "pdf_url": item.get("downloadUrl"),
                "landing_url": item.get("oai") or item.get("id"),
                "license": item.get("license"),
            }
            for item in payload.get("results") or []
            if isinstance(item, dict) and item.get("downloadUrl")
        ]

    def _throttle(self, source: str) -> None:
        intervals = {
            "arxiv": 3.0,
            "semantic_scholar": 1.0,
            "openalex": 0.1,
            "unpaywall": 0.1,
            "crossref": 0.1,
            "europe_pmc": 0.1,
            "core": 2.0,
        }
        interval = intervals.get(source, 0.25)
        last = self._last_request_by_source.get(source)
        if last is not None:
            elapsed = time.monotonic() - last
            if elapsed < interval:
                time.sleep(interval - elapsed)
        self._last_request_by_source[source] = time.monotonic()


def _extract_arxiv_id(value: str) -> str | None:
    match = re.search(
        r"(?:arxiv[:/_\s-]*|arxiv\.org/(?:abs|pdf)/)?([a-z-]+/\d{7}|\d{4}\.\d{4,5})(?:v\d+)?",
        str(value or ""),
        flags=re.IGNORECASE,
    )
    return match.group(1) if match else None


def _arxiv_version_from_url(url: str) -> int | None:
    match = re.search(r"v(\d+)\.pdf(?:$|\?)", url or "", flags=re.IGNORECASE)
    return int(match.group(1)) if match else None


def _looks_like_pdf(content: bytes, content_type: str) -> bool:
    return content.startswith(b"%PDF") or "pdf" in str(content_type or "").lower()


def _dedupe_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    output: list[dict[str, Any]] = []
    for candidate in candidates:
        url = str(candidate.get("pdf_url") or "").strip()
        if not url or url in seen:
            continue
        seen.add(url)
        output.append(candidate)
    return output
