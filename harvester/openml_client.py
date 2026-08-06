"""OpenML-Connector — freie ML-Datensätze (https://openml.org/api/v1).

OpenML ist komplett frei und ohne Key. Die Such-API liefert Datensätze mit
Metadaten; der Download läuft über die ``parquet_url`` bzw. die
``features``/``qualities``-Endpunkte.
"""

from __future__ import annotations

from typing import Any

import httpx

OPENML_API = "https://openml.org/api/v1"


async def search_datasets(
    query: str, limit: int = 10, timeout: float = 25.0
) -> dict[str, Any]:
    """Search OpenML datasets by tag/name. Fail-soft."""
    params: dict[str, Any] = {"limit": min(max(limit, 1), 100)}
    if query.strip():
        params["tag"] = query.strip()  # OpenML tag filter; broad search uses /data/list
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            if query.strip():
                resp = await client.get(
                    f"{OPENML_API}/data/list/tag/{query.strip()}",
                    params={"limit": params["limit"]},
                )
            else:
                resp = await client.get(
                    f"{OPENML_API}/data/list/limit/0/offset/0",
                    params={"limit": params["limit"]},
                )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"results": [], "warnings": [f"OpenML: {type(exc).__name__}"]}
    items = (
        data.get("data", {}).get("dataset", [])
        if isinstance(data, dict)
        else data.get("datasets", [])
    )
    out: list[dict[str, Any]] = []
    for d in items[:limit]:
        did = d.get("did") or d.get("id")
        if did is None:
            continue
        out.append(
            {
                "source": "openml",
                "external_id": str(did),
                "title": str(d.get("name") or "").strip(),
                "description": str(d.get("description") or "")[:600],
                "url": str(d.get("url") or f"https://www.openml.org/d/{did}"),
                "doi": None,
                "license": d.get("licence"),
                "size": _human_size(d.get("size") or d.get("NumberOfInstances")),
                "year": _year_from(d.get("upload_date") or d.get("creation_date")),
                "metadata": {
                    "format": d.get("format"),
                    "tag": d.get("tag"),
                    "version": d.get("version"),
                    "version_label": d.get("version_label"),
                    "dataset_type": "dataset",
                },
            }
        )
    return {"results": out, "warnings": []}


async def get_dataset_details(dataset_id: str, timeout: float = 25.0) -> dict[str, Any]:
    """Details eines OpenML-Datasets: Qualitäten + Download-URL."""
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(f"{OPENML_API}/data/{dataset_id}")
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {
                "description": None,
                "files": [],
                "warning": f"OpenML: {type(exc).__name__}",
            }
    ds = data.get("data_set_description", data) if isinstance(data, dict) else data
    url = (
        ds.get("url")
        or f"https://www.openml.org/data/download/{dataset_id}/dataset.arff"
    )
    files = [
        {
            "name": ds.get("name", "") + ".arff",
            "size": None,
            "download_url": url,
        }
    ]
    return {
        "source": "openml",
        "external_id": str(dataset_id),
        "description": str(ds.get("description") or "") or None,
        "license": ds.get("licence"),
        "files": files,
        "download_url": url,
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


async def openml_status() -> dict[str, Any]:
    return {"authenticated": True, "hint": None}  # free, no auth
