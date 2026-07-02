"""Datensatz-Connectoren: freie Forschungs-Datensatz-Registries (alle Domänen).

Anders als die Paper-Clients liefern diese Quellen **Datensätze** (mit Link/DOI/
Lizenz), die neben den Papern gesammelt und als Eingabe für die Analyse-Werkstatt
genutzt werden können. Jede Quelle wird auf ein einheitliches ``DatasetHit``-Schema
normalisiert. Alles ist **fail-soft**: eine nicht erreichbare/umgebaute Quelle gibt
``[]`` zurück statt zu werfen, damit die Suche über die restlichen Quellen
weiterläuft.

Nachvollziehbarkeit: die ``url`` zeigt bevorzugt auf die DOI-Landing-Page bzw. die
offizielle Datensatz-Seite — der Nutzer kann die Rohdaten/Lizenz immer selbst
einsehen (kein „Blackbox"-Datenbezug).

Quellen (alle ohne Pflicht-Key):
  * ``zenodo``          — CERN Zenodo, domänenübergreifend
  * ``figshare``        — Figshare, domänenübergreifend
  * ``dryad``           — Dryad, Forschungsdaten (v.a. Life Sciences, aber offen)
  * ``clinicaltrials``  — ClinicalTrials.gov (klinische Studien, Medizin)
  * ``papers_with_code``— PapersWithCode-Datasets (ML; Legacy-API, oft leer)
"""
from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

# Reihenfolge = Standard-Auswahl in der UI.
DATASET_SOURCES: list[dict[str, str]] = [
    {"id": "zenodo", "label": "Zenodo", "domain": "alle Fächer"},
    {"id": "figshare", "label": "Figshare", "domain": "alle Fächer"},
    {"id": "dryad", "label": "Dryad", "domain": "Forschungsdaten"},
    {"id": "clinicaltrials", "label": "ClinicalTrials.gov", "domain": "Medizin/Studien"},
    {"id": "papers_with_code", "label": "Papers with Code", "domain": "ML"},
]
DEFAULT_SOURCES = ["zenodo", "figshare", "dryad", "clinicaltrials"]


@dataclass
class DatasetHit:
    source: str
    external_id: str
    title: str
    description: str = ""
    url: str = ""
    doi: str | None = None
    license: str | None = None
    size: str | None = None
    year: int | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "external_id": self.external_id,
            "title": self.title,
            "description": self.description,
            "url": self.url,
            "doi": self.doi,
            "license": self.license,
            "size": self.size,
            "year": self.year,
            "metadata": self.metadata,
        }


def _year_from(text: Any) -> int | None:
    match = re.search(r"(19|20)\d{2}", str(text or ""))
    return int(match.group()) if match else None


def _clean_doi(doi: Any) -> str | None:
    if not doi:
        return None
    d = str(doi).strip()
    d = re.sub(r"^doi:", "", d, flags=re.IGNORECASE)
    d = re.sub(r"^https?://(dx\.)?doi\.org/", "", d, flags=re.IGNORECASE)
    return d or None


def _doi_url(doi: str | None) -> str:
    return f"https://doi.org/{doi}" if doi else ""


async def _zenodo(client: httpx.AsyncClient, query: str, limit: int) -> list[DatasetHit]:
    resp = await client.get(
        "https://zenodo.org/api/records",
        params={"q": query, "size": limit, "type": "dataset", "sort": "bestmatch"},
    )
    resp.raise_for_status()
    hits = resp.json().get("hits", {}).get("hits", []) or []
    out: list[DatasetHit] = []
    for h in hits[:limit]:
        meta = h.get("metadata", {}) or {}
        lic = meta.get("license")
        license_id = lic.get("id") if isinstance(lic, dict) else lic
        doi = _clean_doi(h.get("doi") or meta.get("doi"))
        url = (h.get("links", {}) or {}).get("self_html") or _doi_url(doi)
        out.append(DatasetHit(
            source="zenodo",
            external_id=str(h.get("id") or doi or ""),
            title=str(meta.get("title") or "").strip(),
            description=str(meta.get("description") or "")[:600],
            url=url,
            doi=doi,
            license=str(license_id) if license_id else None,
            year=_year_from(meta.get("publication_date")),
            metadata={"resource_type": (meta.get("resource_type") or {}).get("type")},
        ))
    return out


async def _figshare(client: httpx.AsyncClient, query: str, limit: int) -> list[DatasetHit]:
    resp = await client.post(
        "https://api.figshare.com/v2/articles/search",
        json={"search_for": query, "item_type": 3, "page_size": limit},
    )
    resp.raise_for_status()
    items = resp.json()
    if not isinstance(items, list):
        return []
    out: list[DatasetHit] = []
    for it in items[:limit]:
        doi = _clean_doi(it.get("doi"))
        out.append(DatasetHit(
            source="figshare",
            external_id=str(it.get("id") or doi or ""),
            title=str(it.get("title") or "").strip(),
            description="",  # not in search payload; user opens the landing page
            url=_doi_url(doi) or str(it.get("url") or ""),
            doi=doi,
            license=None,
            year=_year_from(it.get("published_date")),
            metadata={"defined_type": it.get("defined_type_name")},
        ))
    return out


async def _dryad(client: httpx.AsyncClient, query: str, limit: int) -> list[DatasetHit]:
    resp = await client.get(
        "https://datadryad.org/api/v2/search",
        params={"q": query, "per_page": limit},
        headers={"Accept": "application/json"},
    )
    resp.raise_for_status()
    datasets = resp.json().get("_embedded", {}).get("stash:datasets", []) or []
    out: list[DatasetHit] = []
    for d in datasets[:limit]:
        doi = _clean_doi(d.get("identifier"))
        size = d.get("storageSize")
        out.append(DatasetHit(
            source="dryad",
            external_id=str(d.get("identifier") or d.get("id") or ""),
            title=str(d.get("title") or "").strip(),
            description=str(d.get("abstract") or "")[:600],
            url=_doi_url(doi),
            doi=doi,
            license=None,
            size=f"{size} bytes" if isinstance(size, int) else None,
            year=_year_from(d.get("publicationDate") or d.get("lastModificationDate")),
            metadata={},
        ))
    return out


async def _clinicaltrials(client: httpx.AsyncClient, query: str, limit: int) -> list[DatasetHit]:
    resp = await client.get(
        "https://clinicaltrials.gov/api/v2/studies",
        params={"query.term": query, "pageSize": limit},
    )
    resp.raise_for_status()
    studies = resp.json().get("studies", []) or []
    out: list[DatasetHit] = []
    for s in studies[:limit]:
        proto = s.get("protocolSection", {}) or {}
        ident = proto.get("identificationModule", {}) or {}
        nct = ident.get("nctId")
        if not nct:
            continue
        status = (proto.get("statusModule", {}) or {})
        out.append(DatasetHit(
            source="clinicaltrials",
            external_id=str(nct),
            title=str(ident.get("briefTitle") or ident.get("officialTitle") or nct).strip(),
            description=str((proto.get("descriptionModule", {}) or {}).get("briefSummary") or "")[:600],
            url=f"https://clinicaltrials.gov/study/{nct}",
            doi=None,
            license=None,
            year=_year_from(status.get("startDateStruct", {}).get("date") if isinstance(status.get("startDateStruct"), dict) else None),
            metadata={"overall_status": status.get("overallStatus")},
        ))
    return out


async def _papers_with_code(client: httpx.AsyncClient, query: str, limit: int) -> list[DatasetHit]:
    resp = await client.get(
        "https://paperswithcode.com/api/v1/datasets/", params={"q": query}
    )
    resp.raise_for_status()
    results = resp.json().get("results", []) or []
    out: list[DatasetHit] = []
    for d in results[:limit]:
        slug = d.get("id") or d.get("name")
        out.append(DatasetHit(
            source="papers_with_code",
            external_id=str(slug or ""),
            title=str(d.get("full_name") or d.get("name") or "").strip(),
            description=str(d.get("description") or "")[:600],
            url=str(d.get("url") or (f"https://paperswithcode.com/dataset/{slug}" if slug else "")),
            doi=None,
            license=None,
            metadata={},
        ))
    return out


_FETCHERS = {
    "zenodo": _zenodo,
    "figshare": _figshare,
    "dryad": _dryad,
    "clinicaltrials": _clinicaltrials,
    "papers_with_code": _papers_with_code,
}


async def search_datasets(
    query: str,
    sources: list[str] | None = None,
    per_source: int = 8,
    timeout: float = 25.0,
) -> dict[str, Any]:
    """Search dataset registries concurrently. Returns hits + per-source warnings.

    Fail-soft: an unreachable/changed source contributes a warning, never an
    exception. Result shape: ``{"results": [dict], "warnings": [str]}``.
    """
    chosen = [s for s in (sources or DEFAULT_SOURCES) if s in _FETCHERS]
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    if not query.strip() or not chosen:
        return {"results": results, "warnings": ["Keine gültige Suche/Quelle."]}

    async with httpx.AsyncClient(
        timeout=timeout, headers={"User-Agent": "PaperKG/1.0 (dataset search)"}
    ) as client:
        async def run(name: str) -> tuple[str, list[DatasetHit] | Exception]:
            try:
                return name, await _FETCHERS[name](client, query, per_source)
            except Exception as exc:  # noqa: BLE001 — fail-soft per source
                return name, exc

        for name, res in await asyncio.gather(*(run(s) for s in chosen)):
            if isinstance(res, Exception):
                warnings.append(f"{name}: {type(res).__name__}")
                continue
            results.extend(h.as_dict() for h in res)
    return {"results": results, "warnings": warnings}
