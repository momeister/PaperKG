"""Shared open-access PDF resolver (Unpaywall).

Lives in ``harvester/`` so both the FastAPI backend (``api/product_main.py``) and the
auto-harvester (``query/auto_harvester.py``) can resolve a downloadable PDF for a DOI
without importing ``api/`` (which would create a circular import).

The contact email required by the Unpaywall API is read from the ``UNPAYWALL_EMAIL`` env
var or the ``harvester.unpaywall`` section of ``config.yaml``.
"""
from __future__ import annotations

import os
from functools import lru_cache
from typing import Any

from harvester.unpaywall_client import UnpaywallClient, UnpaywallConfig


@lru_cache(maxsize=1)
def _unpaywall_section() -> dict[str, Any]:
    try:
        import yaml

        with open("config.yaml", "r", encoding="utf-8") as fh:
            cfg = (yaml.safe_load(fh) or {}).get("harvester", {}) or {}
    except Exception:
        return {}
    section = cfg.get("unpaywall", {}) if isinstance(cfg, dict) else {}
    return section if isinstance(section, dict) else {}


def unpaywall_email() -> str | None:
    """Return the Unpaywall contact email from env/config, or None if unset/placeholder."""
    section = _unpaywall_section()
    env_name = section.get("email_env") or "UNPAYWALL_EMAIL"
    email = os.getenv(env_name) or section.get("email")
    if not email or "@example.com" in str(email):
        return None
    return str(email)


async def resolve_oa_pdf_url(doi: str | None) -> str | None:
    """Resolve an open-access PDF URL for *doi* via Unpaywall (best-effort, never raises)."""
    if not doi:
        return None
    email = unpaywall_email()
    if not email:
        return None
    unpaywall = UnpaywallClient(UnpaywallConfig(email=email))
    try:
        return await unpaywall.best_oa_url(str(doi).replace("https://doi.org/", "").strip())
    except Exception:
        return None
    finally:
        await unpaywall.close()
