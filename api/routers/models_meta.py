"""LLM-Provider-Meta: Provider-Liste, Modell-Discovery, Auth-Check.

Split out of api/product_main.py. Behaviour unchanged. llm_router laeuft ueber
pm.llm_router (Test-Patch-Surface).
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

import api.product_main as pm  # patchable llm_router

router = APIRouter()


@router.get("/models/providers")
def model_providers() -> dict[str, Any]:
    return {
        "default_provider": pm.llm_router.default_provider,
        "providers": [_provider_view(provider) for provider in pm.llm_router.available_providers()],
    }


@router.post("/models/{provider}/discover")
def discover_models(provider: str) -> dict[str, Any]:
    _ensure_provider(provider)
    return {"provider": provider, "models": pm.llm_router.provider_model_options(provider, refresh=True)}


@router.post("/models/{provider}/check")
def check_model_provider(provider: str, model: str | None = None) -> dict[str, Any]:
    _ensure_provider(provider)
    cfg = pm.llm_router.provider_config(provider)
    ok, error = pm.llm_router.check_provider_auth(provider=provider, model=model, timeout_seconds=min(cfg.timeout_seconds, 30.0))
    return {"provider": provider, "model": model or pm.llm_router.provider_default_model(provider), "ok": ok, "error": error}


def _provider_view(provider: str) -> dict[str, Any]:
    cfg = pm.llm_router.provider_config(provider)
    settings = pm.llm_router.provider_settings(provider)
    return {
        "name": provider,
        "provider_type": cfg.provider_type,
        "base_url": cfg.base_url,
        "default_model": settings.model,
        "models": pm.llm_router.provider_model_options(provider, refresh=False),
        "settings": {
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_tokens,
            "context_size": settings.context_size,
        },
        "auth_configured": bool(cfg.api_key),
    }


def _ensure_provider(provider: str) -> None:
    if provider not in pm.llm_router.available_providers():
        raise HTTPException(status_code=404, detail=f"Unknown provider: {provider}")
