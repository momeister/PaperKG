"""Vertrauensstufen fuer Webquellen.

Die Auto-Recherche eskaliert in drei Stufen: erst wissenschaftliche Quellen
(Paper), dann *vertrauenswuerdige* Webseiten, erst zuletzt beliebige Treffer.
Hier wird nur die zweite von der dritten Stufe unterschieden — anhand der
Domain, nicht des Inhalts. Das ist bewusst grob: es ersetzt keine inhaltliche
Pruefung, sortiert aber Behoerden, Universitaeten und Fachverlage vor Foren und
Content-Farmen.

Wichtig: Alle Webinhalte bleiben unabhaengig von der Stufe *untrusted data* im
Sinne der Prompt-Injection-Abwehr (siehe research/sanitize.py). ``trusted`` sagt
etwas ueber die inhaltliche Verlaesslichkeit, nichts ueber die Sicherheit.
"""
from __future__ import annotations

import re

TRUSTED = "trusted"
UNKNOWN = "unknown"

#: TLD-/Suffix-Muster, die auf Behoerden, Hochschulen und Institutionen zeigen.
DEFAULT_TRUSTED_SUFFIXES: tuple[str, ...] = (
    ".edu",
    ".gov",
    ".int",
    ".mil",
    ".ac.uk",
    ".ac.jp",
    ".ac.at",
    ".edu.au",
    ".gov.uk",
    ".gov.au",
    ".go.jp",
    ".bund.de",
)

#: Etablierte Verlage, Fachgesellschaften, Behoerden und Referenzwerke.
DEFAULT_TRUSTED_DOMAINS: tuple[str, ...] = (
    "who.int",
    "nih.gov",
    "ncbi.nlm.nih.gov",
    "cdc.gov",
    "fda.gov",
    "ema.europa.eu",
    "europa.eu",
    "rki.de",
    "pei.de",
    "bfr.bund.de",
    "destatis.de",
    "oecd.org",
    "worldbank.org",
    "un.org",
    "cochrane.org",
    "cochranelibrary.com",
    "nature.com",
    "science.org",
    "sciencedirect.com",
    "springer.com",
    "springeropen.com",
    "link.springer.com",
    "wiley.com",
    "onlinelibrary.wiley.com",
    "tandfonline.com",
    "sagepub.com",
    "cambridge.org",
    "oup.com",
    "academic.oup.com",
    "nejm.org",
    "thelancet.com",
    "bmj.com",
    "jamanetwork.com",
    "plos.org",
    "frontiersin.org",
    "mdpi.com",
    "elifesciences.org",
    "pnas.org",
    "ieee.org",
    "acm.org",
    "arxiv.org",
    "biorxiv.org",
    "medrxiv.org",
    "doaj.org",
    "openalex.org",
    "semanticscholar.org",
    "crossref.org",
    "zenodo.org",
    "gesetze-im-internet.de",
    "eur-lex.europa.eu",
)


def host_of(url: str) -> str:
    """Hostname einer URL in Kleinschreibung, ohne fuehrendes ``www.``."""
    host = re.sub(r"^[a-z][a-z0-9+.-]*://", "", str(url or "").strip(), flags=re.IGNORECASE)
    host = host.split("/", 1)[0].split("?", 1)[0].split("#", 1)[0]
    host = host.split("@")[-1].split(":", 1)[0].lower()
    return host[4:] if host.startswith("www.") else host


def classify_url(
    url: str,
    trusted_domains: list[str] | None = None,
    trusted_suffixes: list[str] | None = None,
) -> str:
    """``trusted`` fuer bekannte Institutionen/Verlage, sonst ``unknown``."""
    host = host_of(url)
    if not host:
        return UNKNOWN
    domains = [d.strip().lower().lstrip(".") for d in (trusted_domains or []) if str(d).strip()]
    domains.extend(DEFAULT_TRUSTED_DOMAINS)
    for domain in domains:
        if host == domain or host.endswith("." + domain):
            return TRUSTED
    suffixes = [s.strip().lower() for s in (trusted_suffixes or []) if str(s).strip()]
    suffixes.extend(DEFAULT_TRUSTED_SUFFIXES)
    for suffix in suffixes:
        if host.endswith(suffix if suffix.startswith(".") else "." + suffix):
            return TRUSTED
    return UNKNOWN


def tier_label(tier: str) -> str:
    return "vertrauenswürdig" if tier == TRUSTED else "ungeprüft"
