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
  * ``kaggle_competition`` — Kaggle Competitions (Login für Download nötig)
  * ``kaggle_dataset``    — Kaggle Datasets (Login für Download nötig)
  * ``huggingface``    — Hugging Face Hub (frei, optional HF_TOKEN)
  * ``openml``          — OpenML (frei, ML-Benchmark-Daten)
  * ``uci``            — UCI ML Repository (frei, klassische ML-Daten)
  * ``mendeley``        — Mendeley Data (freie Katalogsuche)
"""

from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any

import httpx

from harvester import (
    huggingface_datasets_client as _hf,
    kaggle_client as _kaggle,
    mendeley_client as _mendeley,
    openml_client as _openml,
    uci_client as _uci,
)

# Reihenfolge = Standard-Auswahl in der UI.
DATASET_SOURCES: list[dict[str, str]] = [
    {"id": "zenodo", "label": "Zenodo", "domain": "alle Fächer"},
    {"id": "figshare", "label": "Figshare", "domain": "alle Fächer"},
    {"id": "dryad", "label": "Dryad", "domain": "Forschungsdaten"},
    {
        "id": "clinicaltrials",
        "label": "ClinicalTrials.gov",
        "domain": "Medizin/Studien",
    },
    {"id": "papers_with_code", "label": "Papers with Code", "domain": "ML"},
    {
        "id": "kaggle_competition",
        "label": "Kaggle Competitions",
        "domain": "Wettbewerbe",
        "needs_key": True,
        "note": "Login (KAGGLE_USERNAME/KAGGLE_KEY) nötig für Download",
    },
    {
        "id": "kaggle_dataset",
        "label": "Kaggle Datasets",
        "domain": "ML/Daten",
        "needs_key": True,
        "note": "Login (KAGGLE_USERNAME/KAGGLE_KEY) nötig für Download",
    },
    {
        "id": "huggingface",
        "label": "Hugging Face",
        "domain": "ML/NLP",
        "needs_key": False,
        "note": "frei; HF_TOKEN hebt Rate-Limits",
    },
    {"id": "openml", "label": "OpenML", "domain": "ML"},
    {"id": "uci", "label": "UCI Repository", "domain": "ML (klassisch)"},
    {"id": "mendeley", "label": "Mendeley Data", "domain": "alle Fächer"},
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


async def _zenodo(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[DatasetHit]:
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
        out.append(
            DatasetHit(
                source="zenodo",
                external_id=str(h.get("id") or doi or ""),
                title=str(meta.get("title") or "").strip(),
                description=str(meta.get("description") or "")[:600],
                url=url,
                doi=doi,
                license=str(license_id) if license_id else None,
                year=_year_from(meta.get("publication_date")),
                metadata={
                    "resource_type": (meta.get("resource_type") or {}).get("type")
                },
            )
        )
    return out


async def _figshare(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[DatasetHit]:
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
        out.append(
            DatasetHit(
                source="figshare",
                external_id=str(it.get("id") or doi or ""),
                title=str(it.get("title") or "").strip(),
                description="",  # not in search payload; user opens the landing page
                url=_doi_url(doi) or str(it.get("url") or ""),
                doi=doi,
                license=None,
                year=_year_from(it.get("published_date")),
                metadata={"defined_type": it.get("defined_type_name")},
            )
        )
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
        out.append(
            DatasetHit(
                source="dryad",
                external_id=str(d.get("identifier") or d.get("id") or ""),
                title=str(d.get("title") or "").strip(),
                description=str(d.get("abstract") or "")[:600],
                url=_doi_url(doi),
                doi=doi,
                license=None,
                size=f"{size} bytes" if isinstance(size, int) else None,
                year=_year_from(
                    d.get("publicationDate") or d.get("lastModificationDate")
                ),
                metadata={},
            )
        )
    return out


async def _clinicaltrials(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[DatasetHit]:
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
        status = proto.get("statusModule", {}) or {}
        out.append(
            DatasetHit(
                source="clinicaltrials",
                external_id=str(nct),
                title=str(
                    ident.get("briefTitle") or ident.get("officialTitle") or nct
                ).strip(),
                description=str(
                    (proto.get("descriptionModule", {}) or {}).get("briefSummary") or ""
                )[:600],
                url=f"https://clinicaltrials.gov/study/{nct}",
                doi=None,
                license=None,
                year=_year_from(
                    status.get("startDateStruct", {}).get("date")
                    if isinstance(status.get("startDateStruct"), dict)
                    else None
                ),
                metadata={"overall_status": status.get("overallStatus")},
            )
        )
    return out


async def _papers_with_code(
    client: httpx.AsyncClient, query: str, limit: int
) -> list[DatasetHit]:
    resp = await client.get(
        "https://paperswithcode.com/api/v1/datasets/", params={"q": query}
    )
    resp.raise_for_status()
    results = resp.json().get("results", []) or []
    out: list[DatasetHit] = []
    for d in results[:limit]:
        slug = d.get("id") or d.get("name")
        out.append(
            DatasetHit(
                source="papers_with_code",
                external_id=str(slug or ""),
                title=str(d.get("full_name") or d.get("name") or "").strip(),
                description=str(d.get("description") or "")[:600],
                url=str(
                    d.get("url")
                    or (f"https://paperswithcode.com/dataset/{slug}" if slug else "")
                ),
                doi=None,
                license=None,
                metadata={},
            )
        )
    return out


_FETCHERS = {
    "zenodo": _zenodo,
    "figshare": _figshare,
    "dryad": _dryad,
    "clinicaltrials": _clinicaltrials,
    "papers_with_code": _papers_with_code,
}

# Neue Quellen mit eigenem Modul (eigener httpx-Client, eigene Auth-Logik).
# Diese Funktionen haben die Signatur ``async fn(query, limit) -> dict[str, Any]``
# und liefern ``{"results": [dict], "warnings": [str]}``.
_MODULE_FETCHERS: dict[str, Any] = {
    "kaggle_competition": _kaggle.search_competitions,
    "kaggle_dataset": _kaggle.search_datasets,
    "huggingface": _hf.search_datasets,
    "openml": _openml.search_datasets,
    "uci": _uci.search_datasets,
    "mendeley": _mendeley.search_datasets,
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

    Quellen teilen sich in zwei Gruppen: die klassischen Fetcher (bekommen den
    geteilten httpx-Client übergeben) und die Modul-Fetcher (Kaggle/HF/OpenML/UCI/
    Mendeley) mit eigener Auth-Logik und eigenem Client. Beide Gruppen laufen
    parallel.
    """
    chosen = [
        s
        for s in (sources or DEFAULT_SOURCES)
        if s in _FETCHERS or s in _MODULE_FETCHERS
    ]
    warnings: list[str] = []
    results: list[dict[str, Any]] = []
    if not query.strip() or not chosen:
        return {"results": results, "warnings": ["Keine gültige Suche/Quelle."]}

    classic = [s for s in chosen if s in _FETCHERS]
    modular = [s for s in chosen if s in _MODULE_FETCHERS]

    async def run_classic(name: str) -> tuple[str, list[DatasetHit] | Exception]:
        try:
            async with httpx.AsyncClient(
                timeout=timeout, headers={"User-Agent": "PaperKG/1.0 (dataset search)"}
            ) as client:
                return name, await _FETCHERS[name](client, query, per_source)
        except Exception as exc:  # noqa: BLE001
            return name, exc

    async def run_modular(name: str) -> tuple[str, dict[str, Any] | Exception]:
        try:
            return name, await _MODULE_FETCHERS[name](query, per_source)
        except Exception as exc:  # noqa: BLE001
            return name, exc

    classic_tasks = [run_classic(s) for s in classic]
    modular_tasks = [run_modular(s) for s in modular]
    all_results = await asyncio.gather(*(classic_tasks + modular_tasks))
    for name, res in all_results:
        if name in _MODULE_FETCHERS:
            if isinstance(res, Exception):
                warnings.append(f"{name}: {type(res).__name__}")
            else:
                warnings.extend(res.get("warnings", []))
                results.extend(res.get("results", []))
        else:
            if isinstance(res, Exception):
                warnings.append(f"{name}: {type(res).__name__}")
            else:
                results.extend(h.as_dict() for h in res)
    return {"results": results, "warnings": warnings}


def _human_size(size: Any) -> str | None:
    try:
        value = float(size)
    except (TypeError, ValueError):
        return None
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return None


async def fetch_dataset_details(
    source: str, external_id: str, timeout: float = 25.0
) -> dict[str, Any]:
    """Detail-Ansicht eines Datensatzes: Datei-Liste + Download-Links + Volltext-Beschreibung.

    Nur Metadaten/Links — heruntergeladen wird beim Registry-Anbieter, nicht von uns.
    Fail-soft: nicht unterstützte Quellen oder API-Fehler liefern ``files: []`` mit Warnung.
    """
    # Neue Modul-Quellen mit eigenem Details-Endpoint.
    if source == "huggingface":
        return await _hf.get_dataset_details(external_id, timeout)
    if source == "openml":
        return await _openml.get_dataset_details(external_id, timeout)
    if source == "uci":
        return await _uci.get_dataset_details(external_id, timeout)
    if source == "mendeley":
        return await _mendeley.get_dataset_details(external_id, timeout)
    if source == "kaggle_dataset":
        # external_id kann "owner/slug" oder eine numerische ID sein — wir erwarten "owner/slug".
        if "/" in external_id:
            owner, slug = external_id.split("/", 1)
            return await _kaggle.get_dataset_details(owner, slug, timeout)
        return {
            "description": None,
            "files": [],
            "warning": "Kaggle-Dataset-ID muss owner/slug sein.",
        }
    if source == "kaggle_competition":
        return await _kaggle.list_competition_files(external_id, timeout)

    files: list[dict[str, Any]] = []
    description: str | None = None
    license_name: str | None = None
    download_url: str | None = None
    warning: str | None = None
    try:
        async with httpx.AsyncClient(
            timeout=timeout, headers={"User-Agent": "PaperKG/1.0 (dataset details)"}
        ) as client:
            if source == "zenodo":
                resp = await client.get(f"https://zenodo.org/api/records/{external_id}")
                resp.raise_for_status()
                data = resp.json()
                meta = data.get("metadata", {}) or {}
                description = str(meta.get("description") or "") or None
                lic = meta.get("license")
                license_name = (lic.get("id") if isinstance(lic, dict) else lic) or None
                for f in data.get("files", []) or []:
                    files.append(
                        {
                            "name": f.get("key") or f.get("filename") or "",
                            "size": _human_size(f.get("size")),
                            "download_url": ((f.get("links", {}) or {}).get("self"))
                            or None,
                        }
                    )
            elif source == "figshare":
                resp = await client.get(
                    f"https://api.figshare.com/v2/articles/{external_id}"
                )
                resp.raise_for_status()
                data = resp.json()
                description = str(data.get("description") or "") or None
                lic = data.get("license")
                license_name = (
                    lic.get("name") if isinstance(lic, dict) else lic
                ) or None
                for f in data.get("files", []) or []:
                    files.append(
                        {
                            "name": f.get("name") or "",
                            "size": _human_size(f.get("size")),
                            "download_url": f.get("download_url") or None,
                        }
                    )
            elif source == "dryad":
                encoded = external_id.replace("/", "%2F")
                resp = await client.get(
                    f"https://datadryad.org/api/v2/datasets/{encoded}",
                    headers={"Accept": "application/json"},
                )
                resp.raise_for_status()
                data = resp.json()
                description = str(data.get("abstract") or "") or None
                license_name = str(data.get("license") or "") or None
                download_url = (
                    f"https://datadryad.org/api/v2/datasets/{encoded}/download"
                )
            elif source == "clinicaltrials":
                resp = await client.get(
                    f"https://clinicaltrials.gov/api/v2/studies/{external_id}"
                )
                resp.raise_for_status()
                proto = resp.json().get("protocolSection", {}) or {}
                desc_module = proto.get("descriptionModule", {}) or {}
                description = (
                    str(
                        desc_module.get("detailedDescription")
                        or desc_module.get("briefSummary")
                        or ""
                    )
                    or None
                )
                design = proto.get("designModule", {}) or {}
                enrollment = (design.get("enrollmentInfo", {}) or {}).get("count")
                if enrollment:
                    description = f"Teilnehmer (geplant/ist): {enrollment}\n\n{description or ''}".strip()
            elif source == "papers_with_code":
                resp = await client.get(
                    f"https://paperswithcode.com/api/v1/datasets/{external_id}/"
                )
                resp.raise_for_status()
                data = resp.json()
                description = str(data.get("description") or "") or None
                download_url = str(data.get("url") or "") or None
            else:
                warning = f"Quelle {source} hat keine Detail-API."
    except Exception as exc:  # noqa: BLE001 — fail-soft
        warning = f"Details nicht abrufbar: {type(exc).__name__}"
    return {
        "source": source,
        "external_id": external_id,
        "description": description,
        "license": license_name,
        "files": files,
        "download_url": download_url,
        "warning": warning,
    }


async def download_dataset(
    source: str,
    external_id: str,
    dest_path: str,
    file_name: str | None = None,
    timeout: float = 600.0,
) -> dict[str, Any]:
    """Datensatz (oder einzelne Datei) herunterladen in ``dest_path``.

    Unterstützt heute: Kaggle (Competitions + Datasets, auth-pflichtig). Für
    die klassischen Quellen (Zenodo/Figshare/Dryad/PWC) gibt es keinen
    einheitlichen Download-Pfad — dort liefert ``fetch_dataset_details`` die
    direkten ``download_url``s, die der Nutzer im Browser öffnet. Fail-soft:
    nicht unterstützte Quellen melden das als Warning statt zu werfen.
    """
    if source == "kaggle_competition":
        if not file_name:
            return {
                "ok": False,
                "warning": "Kaggle-Competition-Download braucht file_name.",
            }
        return await _kaggle.download_competition_file(
            external_id, file_name, dest_path, timeout
        )
    if source == "kaggle_dataset":
        if "/" not in external_id:
            return {"ok": False, "warning": "Kaggle-Dataset-ID muss owner/slug sein."}
        owner, slug = external_id.split("/", 1)
        return await _kaggle.download_dataset(owner, slug, dest_path, timeout)
    return {
        "ok": False,
        "warning": f"Download für Quelle {source} nicht unterstützt — Details-URL im Browser öffnen.",
    }
