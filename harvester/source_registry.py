"""Katalog aller Such-Quellen fuer den Import.

Eine Liste, zwei Konsumenten: ``api/routers/harvest.py`` faechert die Suche
darueber auf und liefert sie unter ``GET /harvest/sources`` an das Frontend.
Vorher stand die Liste doppelt (Frontend-Konstante + if/elif im Backend) und ist
auseinandergelaufen.

``tier`` beschreibt die Art der Quelle und wird an zwei Stellen benutzt: als
Badge-Farbe im Import und als Rangfolge in der Auto-Recherche (peer-reviewed vor
Preprint).
"""
from __future__ import annotations

from typing import Any


#: Fachliche Gruppen — die Achse, entlang derer man "alles auf einmal" waehlt.
HARVEST_GROUPS: list[dict[str, str]] = [
    {"id": "aggregator", "label": "Alle Fächer (Aggregatoren)"},
    {"id": "mint", "label": "MINT & Informatik"},
    {"id": "life", "label": "Medizin & Biologie"},
    {"id": "social", "label": "Sozial-, Geistes- & Bildungswissenschaften"},
]

#: tier: journal = begutachtet/verlagsseitig, preprint = unbegutachtet, index = Nachweissystem
HARVEST_SOURCES: list[dict[str, Any]] = [
    {"id": "openalex", "label": "OpenAlex", "group": "aggregator", "tier": "index", "needs_key": False},
    {"id": "crossref", "label": "Crossref", "group": "aggregator", "tier": "journal", "needs_key": False},
    {"id": "semantic_scholar", "label": "Semantic Scholar", "group": "aggregator", "tier": "index", "needs_key": False},
    {"id": "openaire", "label": "OpenAIRE", "group": "aggregator", "tier": "index", "needs_key": False,
     "note": "EU-Aggregator, viele Repositorien"},
    {"id": "doaj", "label": "DOAJ", "group": "aggregator", "tier": "journal", "needs_key": False,
     "note": "Open-Access-Journale"},
    {"id": "core", "label": "CORE", "group": "aggregator", "tier": "journal", "needs_key": True,
     "note": "API-Key (CORE_API_KEY) nötig"},
    {"id": "arxiv", "label": "arXiv", "group": "mint", "tier": "preprint", "needs_key": False},
    {"id": "dblp", "label": "DBLP", "group": "mint", "tier": "index", "needs_key": False,
     "note": "Informatik-Bibliografie, ohne Abstracts"},
    {"id": "ads", "label": "NASA ADS", "group": "mint", "tier": "index", "needs_key": True,
     "note": "API-Key (ADS_API_KEY) nötig · Astronomie/Physik"},
    {"id": "europepmc", "label": "Europe PMC / PubMed", "group": "life", "tier": "journal", "needs_key": False},
    {"id": "biorxiv", "label": "bioRxiv / medRxiv", "group": "life", "tier": "preprint", "needs_key": False},
    {"id": "osf", "label": "OSF Preprints", "group": "social", "tier": "preprint", "needs_key": False,
     "note": "PsyArXiv, SocArXiv, EdArXiv … (über Crossref-Präfix)"},
    {"id": "eric", "label": "ERIC", "group": "social", "tier": "journal", "needs_key": False,
     "note": "Bildungsforschung"},
    {"id": "doab", "label": "DOAB", "group": "social", "tier": "journal", "needs_key": False,
     "note": "Open-Access-Bücher"},
    {"id": "hal", "label": "HAL", "group": "social", "tier": "journal", "needs_key": False,
     "note": "Französisches OA-Archiv, stark in Geistes-/Sozialwissenschaften"},
]

#: Vorauswahl im Import: ein Preprint-Server plus ein breiter Aggregator.
DEFAULT_SOURCES: list[str] = ["arxiv", "openalex"]

SOURCE_IDS: frozenset[str] = frozenset(source["id"] for source in HARVEST_SOURCES)

#: Rangfolge fuer die wissenschaftliche Stufe der Auto-Recherche.
TIER_RANK: dict[str, int] = {"journal": 0, "index": 1, "preprint": 2}


def source_tier(source_id: str) -> str:
    """Art der Quelle (journal | index | preprint); unbekannt -> ``index``."""
    for source in HARVEST_SOURCES:
        if source["id"] == source_id:
            return str(source["tier"])
    return "index"


def catalog() -> dict[str, Any]:
    """Nutzlast fuer ``GET /harvest/sources``."""
    return {"sources": HARVEST_SOURCES, "groups": HARVEST_GROUPS, "default": DEFAULT_SOURCES}
