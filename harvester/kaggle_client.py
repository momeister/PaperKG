"""Kaggle-Connector — Competitions + Datasets suchen und herunterladen.

Kaggle nutzt Basic-Auth (username:key). Die Credentials liegen in ``.env`` als
``KAGGLE_USERNAME`` und ``KAGGLE_KEY`` (wie vom offiziellen ``kaggle``-CLI auch).
Ohne Auth ist nur eine eingeschränkte Public-Scrape-Suche möglich; der Client
meldet das als Warning und liefert was geht — fail-soft wie die anderen Quellen.

Verwendete Endpunkte (https://www.kaggle.com/api/v1):
  * ``/competitions/list``            — Auflistung/Suche laufender Competitions
  * ``/competitions/data/list/{ref}`` — Datei-Liste einer Competition
  * ``/competitions/data/download/{ref}/{file}`` — Datei-Download (auth-pflichtig)
  * ``/datasets/list``                — Datasets (Suche optional)
  * ``/datasets/view/{owner}/{slug}`` — Details zu einem Dataset
  * ``/datasets/download/{owner}/{slug}`` — Download (auth-pflichtig)

Wichtig: Competition-Daten sind competition-gebunden und dürfen nur von
Teilnehmern genutzt werden — der Client prüft Auth, weist im UI aber extra
darauf hin.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

KAGGLE_API = "https://www.kaggle.com/api/v1"


@dataclass
class KaggleCredentials:
    username: str
    key: str

    @property
    def available(self) -> bool:
        return bool(self.username and self.key)

    def auth(self) -> tuple[str, str] | None:
        return (self.username, self.key) if self.available else None


def load_credentials() -> KaggleCredentials:
    """Read ``KAGGLE_USERNAME`` + ``KAGGLE_KEY`` from env (or .env loaded by caller)."""
    return KaggleCredentials(
        username=str(os.environ.get("KAGGLE_USERNAME") or "").strip(),
        key=str(os.environ.get("KAGGLE_KEY") or "").strip(),
    )


async def search_competitions(
    query: str, limit: int = 10, timeout: float = 25.0
) -> dict[str, Any]:
    """Search Kaggle competitions (public list; auth lifts some limits)."""
    creds = load_credentials()
    warnings: list[str] = []
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, Any] = {"page": 1, "pageSize": min(max(limit, 1), 50)}
    if query.strip():
        params["search"] = query.strip()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(
                f"{KAGGLE_API}/competitions/list",
                params=params,
                headers=headers,
                auth=creds.auth(),
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                warnings.append(
                    "Kaggle: Auth fehlt — nur öffentliche Competitions sichtbar."
                )
                # Retry unauthenticated (Kaggle erlaubt die Liste ohne Auth eingeschränkt)
                resp = await client.get(
                    f"{KAGGLE_API}/competitions/list", params=params, headers=headers
                )
                if resp.status_code >= 400:
                    return {
                        "results": [],
                        "warnings": warnings + [f"Kaggle HTTP {resp.status_code}"],
                    }
                data = resp.json()
            else:
                return {
                    "results": [],
                    "warnings": [f"Kaggle HTTP {exc.response.status_code}"],
                }
        except Exception as exc:  # noqa: BLE001 — fail-soft
            return {"results": [], "warnings": [f"Kaggle: {type(exc).__name__}"]}
    comps = data if isinstance(data, list) else data.get("competitions", [])
    out: list[dict[str, Any]] = []
    for c in comps[:limit]:
        ref = c.get("ref") or ""
        out.append(
            {
                "source": "kaggle_competition",
                "external_id": str(ref or c.get("id") or ""),
                "title": str(c.get("title") or "").strip(),
                "description": str(c.get("description") or "")[:600],
                "url": f"https://www.kaggle.com/competitions/{ref}" if ref else "",
                "doi": None,
                "license": None,
                "size": None,
                "year": _year_from(c.get("deadline") or c.get("enabledDate")),
                "metadata": {
                    "category": c.get("category"),
                    "reward": c.get("reward"),
                    "deadline": c.get("deadline"),
                    "evaluationMetric": c.get("evaluationMetric"),
                    "isKernelsSubmittable": c.get("isKernelsSubmittable"),
                    "competition_type": "competition",
                },
            }
        )
    if not creds.available:
        warnings.append(
            "Kaggle: ohne Login sind Competition-Daten nicht herunterladbar."
        )
    return {"results": out, "warnings": warnings}


async def list_competition_files(
    competition_ref: str, timeout: float = 25.0
) -> dict[str, Any]:
    """Datei-Listing einer Competition (auth-pflichtig)."""
    creds = load_credentials()
    if not creds.available:
        return {
            "files": [],
            "warning": "Kaggle-Login nötig — Competition-Daten erfordern Auth.",
        }
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(
                f"{KAGGLE_API}/competitions/data/list/{competition_ref}",
                auth=creds.auth(),
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            return {"files": [], "warning": f"Kaggle HTTP {exc.response.status_code}"}
        except Exception as exc:  # noqa: BLE001
            return {"files": [], "warning": f"Kaggle: {type(exc).__name__}"}
    files = []
    for f in (data if isinstance(data, list) else data.get("files", [])) or []:
        files.append(
            {
                "name": f.get("name") or "",
                "size": _human_size(f.get("totalBytes") or f.get("size")),
                "description": str(f.get("description") or "")[:300],
                "download_url": f.get("url") or "",
            }
        )
    return {"files": files, "competition_ref": competition_ref}


async def download_competition_file(
    competition_ref: str, file_name: str, dest_path: str, timeout: float = 300.0
) -> dict[str, Any]:
    """Eine einzelne Competition-Datei herunterladen (auth-pflichtig)."""
    creds = load_credentials()
    if not creds.available:
        return {"ok": False, "warning": "Kaggle-Login nötig für Competition-Download."}
    url = f"{KAGGLE_API}/competitions/data/download/{competition_ref}/{file_name}"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, auth=creds.auth()) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
        return {"ok": True, "path": dest_path, "size_bytes": os.path.getsize(dest_path)}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "warning": f"Kaggle HTTP {exc.response.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "warning": f"Kaggle: {type(exc).__name__}"}


async def search_datasets(
    query: str, limit: int = 10, timeout: float = 25.0
) -> dict[str, Any]:
    """Search Kaggle datasets (list endpoint; search field optional)."""
    creds = load_credentials()
    warnings: list[str] = []
    headers: dict[str, str] = {"Accept": "application/json"}
    params: dict[str, Any] = {"page": 1, "pageSize": min(max(limit, 1), 50)}
    if query.strip():
        params["search"] = query.strip()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(
                f"{KAGGLE_API}/datasets/list",
                params=params,
                headers=headers,
                auth=creds.auth(),
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 401:
                warnings.append(
                    "Kaggle: Auth fehlt — nur eingeschränkte Dataset-Suche."
                )
                resp = await client.get(
                    f"{KAGGLE_API}/datasets/list", params=params, headers=headers
                )
                if resp.status_code >= 400:
                    return {
                        "results": [],
                        "warnings": warnings + [f"Kaggle HTTP {resp.status_code}"],
                    }
                data = resp.json()
            else:
                return {
                    "results": [],
                    "warnings": [f"Kaggle HTTP {exc.response.status_code}"],
                }
        except Exception as exc:  # noqa: BLE001
            return {"results": [], "warnings": [f"Kaggle: {type(exc).__name__}"]}
    items = data if isinstance(data, list) else data.get("datasets", [])
    out: list[dict[str, Any]] = []
    for d in items[:limit]:
        owner = d.get("ownerRef") or d.get("owner") or ""
        slug = d.get("ref") or d.get("slug") or ""
        ref = f"{owner}/{slug}" if owner and slug else str(d.get("id") or slug)
        out.append(
            {
                "source": "kaggle_dataset",
                "external_id": str(d.get("id") or ref),
                "title": str(d.get("title") or "").strip(),
                "description": str(d.get("description") or "")[:600],
                "url": f"https://www.kaggle.com/datasets/{ref}" if ref else "",
                "doi": None,
                "license": d.get("licenseName"),
                "size": _human_size(d.get("totalBytes") or d.get("size")),
                "year": _year_from(d.get("lastUpdated") or d.get("created")),
                "metadata": {
                    "owner": owner,
                    "slug": slug,
                    "ref": ref,
                    "views": d.get("viewCount"),
                    "downloads": d.get("downloadCount"),
                    "votes": d.get("voteCount"),
                    "usability": d.get("usabilityRating"),
                    "tags": d.get("tags"),
                    "dataset_type": "dataset",
                },
            }
        )
    return {"results": out, "warnings": warnings}


async def get_dataset_details(
    owner: str, slug: str, timeout: float = 25.0
) -> dict[str, Any]:
    """Details + Datei-Listing eines Kaggle-Datasets (auth optional)."""
    creds = load_credentials()
    async with httpx.AsyncClient(timeout=timeout) as client:
        try:
            resp = await client.get(
                f"{KAGGLE_API}/datasets/view/{owner}/{slug}",
                auth=creds.auth(),
                headers={"Accept": "application/json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except httpx.HTTPStatusError as exc:
            return {
                "description": None,
                "files": [],
                "warning": f"Kaggle HTTP {exc.response.status_code}",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "description": None,
                "files": [],
                "warning": f"Kaggle: {type(exc).__name__}",
            }
    files: list[dict[str, Any]] = []
    for f in data.get("files", []) or []:
        files.append(
            {
                "name": f.get("name") or "",
                "size": _human_size(f.get("totalBytes") or f.get("size")),
                "download_url": f.get("url")
                or f"https://www.kaggle.com/datasets/{owner}/{slug}/download?file={f.get('name','')}",
            }
        )
    return {
        "source": "kaggle_dataset",
        "external_id": str(data.get("id") or f"{owner}/{slug}"),
        "description": str(data.get("description") or "") or None,
        "license": data.get("licenseName"),
        "files": files,
        "download_url": f"https://www.kaggle.com/datasets/{owner}/{slug}/download",
    }


async def download_dataset(
    owner: str, slug: str, dest_path: str, timeout: float = 600.0
) -> dict[str, Any]:
    """Ganzes Kaggle-Dataset als zip herunterladen (auth-pflichtig)."""
    creds = load_credentials()
    if not creds.available:
        return {"ok": False, "warning": "Kaggle-Login nötig für Dataset-Download."}
    url = f"{KAGGLE_API}/datasets/download/{owner}/{slug}"
    try:
        async with httpx.AsyncClient(timeout=timeout, follow_redirects=True) as client:
            async with client.stream("GET", url, auth=creds.auth()) as resp:
                resp.raise_for_status()
                with open(dest_path, "wb") as fh:
                    async for chunk in resp.aiter_bytes():
                        fh.write(chunk)
        return {"ok": True, "path": dest_path, "size_bytes": os.path.getsize(dest_path)}
    except httpx.HTTPStatusError as exc:
        return {"ok": False, "warning": f"Kaggle HTTP {exc.response.status_code}"}
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "warning": f"Kaggle: {type(exc).__name__}"}


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


async def kaggle_status() -> dict[str, Any]:
    """Login-Status für das UI: authentifiziert? + Hinweis."""
    creds = load_credentials()
    return {
        "authenticated": creds.available,
        "username": creds.username or None,
        "hint": (
            None
            if creds.available
            else "Kaggle-Login fehlt — Competitions/Datasets ohne Download nur eingeschränkt."
        ),
    }
