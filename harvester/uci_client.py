"""UCI ML Repository-Connector — freie klassische ML-Datensätze.

UCI bietet eine JSON-API unter https://archive.ics.uci.edu/api/v1/. Der
Repository-Katalog kann durchsucht werden (search-Feld), Details liefern
Metadaten + Download-Link. Komplett frei, kein Key.
"""

from __future__ import annotations

from typing import Any

import httpx

UCI_API = "https://archive.ics.uci.edu/api/v1"


async def search_datasets(
    query: str, limit: int = 10, timeout: float = 25.0
) -> dict[str, Any]:
    """Search the UCI catalog. Fail-soft."""
    params: dict[str, Any] = {"limit": min(max(limit, 1), 100)}
    if query.strip():
        params["search"] = query.strip()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(f"{UCI_API}/datasets", params=params)
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"results": [], "warnings": [f"UCI: {type(exc).__name__}"]}
    # API shape: {"data": {...}, payload varies; tolerate both.
    items = (
        data
        if isinstance(data, list)
        else data.get("datasets") or data.get("data", {}).get("datasets", [])
    )
    out: list[dict[str, Any]] = []
    for d in items[:limit]:
        slug = d.get("slug") or d.get("name")
        if not slug:
            continue
        out.append(
            {
                "source": "uci",
                "external_id": str(d.get("uci_id") or d.get("id") or slug),
                "title": str(d.get("name") or slug).strip(),
                "description": str(d.get("abstract") or d.get("description") or "")[
                    :600
                ],
                "url": f"https://archive.ics.uci.edu/dataset/{d.get('uci_id', '')}/{slug}",
                "doi": d.get("doi"),
                "license": d.get("creators_licences"),
                "size": _human_size(d.get("size_kb") and d.get("size_kb") * 1024),
                "year": _year_from(d.get("creators_donated")),
                "metadata": {
                    "instances": d.get("instances"),
                    "features": d.get("features"),
                    "area": d.get("area"),
                    "types": d.get("types"),
                    "dataset_type": "dataset",
                },
            }
        )
    return {"results": out, "warnings": []}


async def get_dataset_details(dataset_id: str, timeout: float = 25.0) -> dict[str, Any]:
    """Details + Download-Link eines UCI-Datasets."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(f"{UCI_API}/datasets/{dataset_id}")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {
                "description": None,
                "files": [],
                "warning": f"UCI: {type(exc).__name__}",
            }
    data = data.get("data", data) if isinstance(data, dict) else data
    files: list[dict[str, Any]] = []
    # UCI keeps the actual download zip under a predictable path.
    did = data.get("uci_id") or dataset_id
    name = data.get("name") or ""
    files.append(
        {
            "name": f"{name}.zip",
            "size": _human_size(data.get("size_kb") and data.get("size_kb") * 1024),
            "download_url": f"https://archive.ics.uci.edu/static/public/{did}/{name.replace(' ', '+')}.zip",
        }
    )
    return {
        "source": "uci",
        "external_id": str(dataset_id),
        "description": str(data.get("abstract") or "") or None,
        "license": data.get("creators_licences"),
        "files": files,
        "download_url": files[0]["download_url"] if files else None,
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


async def uci_status() -> dict[str, Any]:
    return {"authenticated": True, "hint": None}  # free, no auth
