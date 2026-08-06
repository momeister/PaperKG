"""Mendeley Data-Connector — Katalogsuche (niedrig priorisiert).

Mendeley Data (https://data.mendeley.com) bietet eine öffentliche Suche via
/v1/datasets. Kein Key nötig für die Suche; der Download läuft über die
Landing-Page. Nur Katalogsuche — kein Reference-Manager-Import.
"""

from __future__ import annotations

from typing import Any

import httpx

MENDELEY_API = "https://api.mendeley.com"


async def search_datasets(
    query: str, limit: int = 10, timeout: float = 25.0
) -> dict[str, Any]:
    """Search Mendeley Data catalog. Fail-soft."""
    params: dict[str, Any] = {"limit": min(max(limit, 1), 100)}
    if query.strip():
        params["search"] = query.strip()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(f"{MENDELEY_API}/datasets", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"results": [], "warnings": [f"Mendeley: {type(exc).__name__}"]}
    items = (
        data
        if isinstance(data, list)
        else data.get("datasets") or data.get("results", [])
    )
    out: list[dict[str, Any]] = []
    for d in items[:limit]:
        did = d.get("id") or d.get("dataset_id")
        if not did:
            continue
        doi = d.get("doi")
        out.append(
            {
                "source": "mendeley",
                "external_id": str(did),
                "title": str(d.get("title") or "").strip(),
                "description": str(d.get("description") or "")[:600],
                "url": str(
                    d.get("link")
                    or (
                        f"https://doi.org/{doi}"
                        if doi
                        else f"https://data.mendeley.com/datasets/{did}"
                    )
                ),
                "doi": doi,
                "license": d.get("license"),
                "size": None,
                "year": _year_from(d.get("created") or d.get("published")),
                "metadata": {
                    "publisher": d.get("publisher"),
                    "categories": d.get("categories"),
                    "dataset_type": "dataset",
                },
            }
        )
    return {"results": out, "warnings": []}


async def get_dataset_details(dataset_id: str, timeout: float = 25.0) -> dict[str, Any]:
    """Details + Datei-Listing eines Mendeley-Datasets."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(f"{MENDELEY_API}/datasets/{dataset_id}")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {
                "description": None,
                "files": [],
                "warning": f"Mendeley: {type(exc).__name__}",
            }
    files: list[dict[str, Any]] = []
    for f in data.get("files", []) or []:
        files.append(
            {
                "name": f.get("filename") or f.get("name") or "",
                "size": _human_size(
                    f.get("content_details", {}).get("size")
                    if isinstance(f.get("content_details"), dict)
                    else f.get("size")
                ),
                "download_url": (
                    f.get("content_details", {}).get("download_url")
                    if isinstance(f.get("content_details"), dict)
                    else f.get("download_url")
                ),
            }
        )
    return {
        "source": "mendeley",
        "external_id": str(dataset_id),
        "description": str(data.get("description") or "") or None,
        "license": data.get("license"),
        "files": files,
        "download_url": f"https://data.mendeley.com/datasets/{dataset_id}",
    }


def _year_from(text: Any) -> int | None:
    import re

    match = re.search(r"(19|20)\d{2}", str(text or ""))
    return int(match.group()) if match else None


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


async def mendeley_status() -> dict[str, Any]:
    return {"authenticated": True, "hint": None}  # free catalog search
