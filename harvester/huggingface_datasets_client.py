"""Hugging Face Datasets-Connector — freies Hub, optional Token für höhere Limits.

Das HF Hub (https://huggingface.co/api/datasets) ist frei durchsuchbar; mit
``HF_TOKEN`` steigen die Rate-Limits und private/gated Datasets werden erreichbar
(gated erfordert zusätzlich die explizite Freischaltung auf der Hub-Seite).

Der Connector liefert Suchtreffer als einheitliche ``DatasetHit``-artige Dicts
(passend zum ``dataset_clients``-Schema). Downloads laufen über das bestehende
``download_url``-Feld — der Nutzer lädt auf der Hub-Seite oder via ``huggingface_hub``.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

HF_API = "https://huggingface.co/api"


def _token() -> str | None:
    raw = os.environ.get("HF_TOKEN") or os.environ.get("HUGGING_FACE_HUB_TOKEN")
    return raw.strip() if raw else None


async def search_datasets(
    query: str, limit: int = 10, timeout: float = 25.0
) -> dict[str, Any]:
    """Search the HF Hub datasets endpoint. Token optional (lifts rate limits)."""
    warnings: list[str] = []
    headers: dict[str, str] = {"Accept": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    params: dict[str, Any] = {"limit": min(max(limit, 1), 100)}
    if query.strip():
        params["search"] = query.strip()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(
                f"{HF_API}/datasets", params=params, headers=headers
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:  # noqa: BLE001
            return {"results": [], "warnings": [f"HuggingFace: {type(exc).__name__}"]}
    items = data if isinstance(data, list) else data.get("datasets", [])
    out: list[dict[str, Any]] = []
    for d in items[:limit]:
        did = d.get("id") or d.get("name") or ""
        if not did:
            continue
        out.append(
            {
                "source": "huggingface",
                "external_id": str(did),
                "title": str(did),
                "description": str(
                    d.get("description") or d.get("cardData", {}).get("description", "")
                    if isinstance(d.get("cardData"), dict)
                    else ""
                )[:600],
                "url": f"https://huggingface.co/datasets/{did}",
                "doi": None,
                "license": _license(d),
                "size": None,
                "year": _year_from(d.get("lastModified") or d.get("createdAt")),
                "metadata": {
                    "downloads": d.get("downloads"),
                    "likes": d.get("likes"),
                    "tags": d.get("tags"),
                    "private": d.get("private"),
                    "gated": d.get("gated"),
                    "dataset_type": "dataset",
                },
            }
        )
    if not token:
        warnings.append(
            "HuggingFace: ohne HF_TOKEN sind nur niedrige Rate-Limits aktiv."
        )
    return {"results": out, "warnings": warnings}


async def get_dataset_details(dataset_id: str, timeout: float = 25.0) -> dict[str, Any]:
    """Metadaten + Datei-Listing eines HF-Datasets."""
    headers: dict[str, str] = {"Accept": "application/json"}
    token = _token()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            meta = (
                await client.get(f"{HF_API}/datasets/{dataset_id}", headers=headers)
            ).json()
            siblings = (
                await client.get(
                    f"{HF_API}/datasets/{dataset_id}/tree/main", headers=headers
                )
            ).json()
        except Exception as exc:  # noqa: BLE001
            return {
                "description": None,
                "files": [],
                "warning": f"HuggingFace: {type(exc).__name__}",
            }
    files: list[dict[str, Any]] = []
    for s in siblings if isinstance(siblings, list) else []:
        if s.get("type") == "file":
            path = s.get("path") or ""
            files.append(
                {
                    "name": path,
                    "size": _human_size(s.get("size")),
                    "download_url": f"https://huggingface.co/datasets/{dataset_id}/resolve/main/{path}",
                }
            )
    return {
        "source": "huggingface",
        "external_id": dataset_id,
        "description": str(meta.get("description") or "") or None,
        "license": _license(meta),
        "files": files,
        "download_url": f"https://huggingface.co/datasets/{dataset_id}/resolve/main/",
    }


def _license(d: dict[str, Any]) -> str | None:
    card = d.get("cardData")
    if isinstance(card, dict):
        lic = card.get("license")
        if isinstance(lic, list):
            return ", ".join(str(x) for x in lic) if lic else None
        if lic:
            return str(lic)
    return d.get("license")


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


async def hf_status() -> dict[str, Any]:
    token = _token()
    return {
        "authenticated": bool(token),
        "hint": (
            None if token else "HF_TOKEN fehlt — Suche läuft mit niedrigen Rate-Limits."
        ),
    }
