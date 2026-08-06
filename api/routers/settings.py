"""Settings-Router — Provider-Credentials sicher in ``.env`` ablegen.

Heute: Kaggle-Login (KAGGLE_USERNAME/KAGGLE_KEY) für den Task-Focused Mode.
Schreibt die Credentials **ausschließlich** in die gitignored ``.env`` und lädt
sie via ``dotenv`` in den aktuellen Prozess, sodass der laufende Backend ohne
Neustart sofort Zugriff hat. Nie inline in config.yaml, nie geloggt.

Der Endpunkt validiert nicht gegen die echte Kaggle-API (kein Test-Login, um
Rate-Limits/Account-Sperren zu vermeiden) — der Client merkt beim ersten echten
Call, ob die Credentials stimmen und meldet das als Warning zurück.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

router = APIRouter()

_ENV_PATH = Path(".env")


class KaggleLoginRequest(BaseModel):
    """Kaggle-Credentials schreiben. Entwender username+key oder kaggle.json-Inhalt."""

    username: str | None = Field(default=None, min_length=1, max_length=100)
    key: str | None = Field(default=None, min_length=1, max_length=200)
    kaggle_json: str | None = Field(
        default=None,
        description="Inhalt einer kaggle.json (Username + Key als JSON)",
    )


def _parse_kaggle_json(raw: str) -> tuple[str, str]:
    """kaggle.json-Inhalt -> (username, key). Wirft bei ungültigem JSON."""
    import json

    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"kaggle.json ist kein gültiges JSON: {exc}") from exc
    username = str(data.get("username") or "").strip()
    key = str(data.get("key") or "").strip()
    if not username or not key:
        raise ValueError("kaggle.json fehlt username oder key.")
    return username, key


def _upsert_env_entry(env_path: Path, key: str, value: str) -> None:
    """Schreibe/ersetze einen KEY=value-Eintrag in .env, ohne den Rest zu verändern."""
    lines: list[str] = []
    found = False
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if line.strip().startswith(f"{key}="):
                lines.append(f"{key}={value}")
                found = True
            else:
                lines.append(line)
    if not found:
        lines.append(f"{key}={value}")
    env_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _apply_to_process_env(username: str, key: str) -> None:
    """Damit der laufende Backend sofort Zugriff hat, ohne Neustart."""
    os.environ["KAGGLE_USERNAME"] = username
    os.environ["KAGGLE_KEY"] = key


@router.get("/settings/kaggle")
async def kaggle_login_status() -> dict[str, Any]:
    """Aktueller Kaggle-Login-Status (ohne die Credentials zu leaken)."""
    from harvester import kaggle_client

    return await kaggle_client.kaggle_status()


@router.post("/settings/kaggle")
async def kaggle_login(request: KaggleLoginRequest) -> dict[str, Any]:
    """Kaggle-Credentials in .env ablegen + in den laufenden Prozess laden.

    Nimmt entweder username+key direkt oder den Inhalt einer kaggle.json.
    Bestehende .env-Einträge werden ersetzt, der Rest der Datei bleibt unangetastet.
    """
    username = (request.username or "").strip()
    key = (request.key or "").strip()
    if request.kaggle_json:
        try:
            username, key = _parse_kaggle_json(request.kaggle_json)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc))
    if not username or not key:
        raise HTTPException(
            status_code=422,
            detail="Brauche entweder username+key oder kaggle_json.",
        )
    # In .env schreiben (gitignored).
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _upsert_env_entry(_ENV_PATH, "KAGGLE_USERNAME", username)
    _upsert_env_entry(_ENV_PATH, "KAGGLE_KEY", key)
    # In den laufenden Prozess laden.
    _apply_to_process_env(username, key)
    # Optional dotenv neu laden, damit Sub-Prozesse die Werte auch sehen.
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=_ENV_PATH, override=True)
    except Exception:
        pass  # noqa: BLE001 — dotenv optional
    return {
        "ok": True,
        "username": username,
        "hint": "Credentials in .env gespeichert. Kaggle-Download jetzt möglich.",
    }


@router.delete("/settings/kaggle")
async def kaggle_logout() -> dict[str, Any]:
    """Kaggle-Credentials aus .env entfernen + aus dem Prozess löschen."""
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
        kept = [
            line
            for line in lines
            if not line.strip().startswith(("KAGGLE_USERNAME=", "KAGGLE_KEY="))
        ]
        _ENV_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.environ.pop("KAGGLE_USERNAME", None)
    os.environ.pop("KAGGLE_KEY", None)
    return {"ok": True, "hint": "Kaggle-Credentials entfernt."}


@router.get("/settings/hf")
async def hf_status() -> dict[str, Any]:
    """Hugging Face Token-Status."""
    from harvester import huggingface_datasets_client as hf

    return await hf.hf_status()


class HfTokenRequest(BaseModel):
    token: str = Field(min_length=1, max_length=200)


@router.post("/settings/hf")
async def hf_login(request: HfTokenRequest) -> dict[str, Any]:
    """HF_TOKEN in .env ablegen + in den Prozess laden."""
    _ENV_PATH.parent.mkdir(parents=True, exist_ok=True)
    _upsert_env_entry(_ENV_PATH, "HF_TOKEN", request.token.strip())
    os.environ["HF_TOKEN"] = request.token.strip()
    try:
        from dotenv import load_dotenv

        load_dotenv(dotenv_path=_ENV_PATH, override=True)
    except Exception:
        pass  # noqa: BLE001
    return {"ok": True, "hint": "HF_TOKEN in .env gespeichert."}


@router.delete("/settings/hf")
async def hf_logout() -> dict[str, Any]:
    if _ENV_PATH.exists():
        lines = _ENV_PATH.read_text(encoding="utf-8").splitlines()
        kept = [line for line in lines if not line.strip().startswith("HF_TOKEN=")]
        _ENV_PATH.write_text("\n".join(kept) + "\n", encoding="utf-8")
    os.environ.pop("HF_TOKEN", None)
    return {"ok": True, "hint": "HF_TOKEN entfernt."}
