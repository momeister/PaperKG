"""Werkzeuge: Text-Rewrite und Zitat-Nachcheck (Claim-Check).

Split out of api/product_main.py. Behaviour unchanged. llm_router laeuft ueber
pm.llm_router (Test-Patch-Surface).
"""
from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import api.product_main as pm  # patchable llm_router

DEFAULT_METADATA_DB_PATH = "data/metadata.duckdb"
DEFAULT_PDF_BASE_DIR = "data/pdfs"

router = APIRouter()


class RewriteRequest(BaseModel):
    text: str = Field(min_length=1, max_length=20000)
    instruction: str = Field(default="Schreibe den Text klarer und wissenschaftlich um.", min_length=1, max_length=500)
    provider: str | None = None
    model: str | None = None


class ClaimCheckRequest(BaseModel):
    """Nachcheck: stützt die zitierte Quelle diese konkrete Aussage wirklich?"""

    statement: str = Field(min_length=1, max_length=4000)
    paper_ids: list[str] = Field(min_length=1, max_length=4)
    titles: dict[str, str] = Field(default_factory=dict)
    evidence_texts: dict[str, str] = Field(default_factory=dict)
    provider: str | None = None
    model: str | None = None
    metadata_db_path: str = DEFAULT_METADATA_DB_PATH
    pdf_base_dir: str = DEFAULT_PDF_BASE_DIR
    # Bei unsicherem Urteil das ganze Paper nachprüfen (statt nur die Belegstelle).
    escalate_whole_paper: bool = True


@router.post("/tools/rewrite")
def rewrite_text(request: RewriteRequest) -> dict[str, Any]:
    overrides: dict[str, Any] = {
        "temperature": 0.15,
        "top_p": 0.9,
        "max_tokens": min(1800, max(300, len(request.text) // 2 + 300)),
    }
    if request.model:
        overrides["model"] = request.model
    try:
        text = pm.llm_router.chat(
            [
                {
                    "role": "system",
                    "content": (
                        "Du bist ein praeziser wissenschaftlicher Schreibassistent. "
                        "Schreibe nur den gegebenen Text um, fuege keine neuen Fakten, "
                        "Quellen oder Zitate hinzu und erhalte vorhandene Zitationsmarker."
                    ),
                },
                {
                    "role": "user",
                    "content": f"Aufgabe: {request.instruction}\n\nText:\n{request.text}",
                },
            ],
            provider=request.provider,
            overrides=overrides,
        )
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Rewrite failed: {exc}") from exc
    return {"text": str(text or "").strip(), "model": overrides.get("model") or pm.llm_router.provider_default_model(request.provider)}


@router.post("/assistant/claim-check")
async def assistant_claim_check(request: ClaimCheckRequest) -> dict[str, Any]:
    """Prüft eine markierte/zitierte Aussage gegen ihre Quelle(n) (PDF → Abstract → Grau).

    Pro Quelle ein Urteil (gestützt / teilweise / nicht gestützt / nicht beurteilbar)
    mit wörtlichen Belegzitaten. LLM-Aufruf läuft blockierend → Thread.
    """
    from query.claim_checker import check_claim

    seen: set[str] = set()
    checks: list[dict[str, Any]] = []
    for raw_paper_id in request.paper_ids:
        pid = str(raw_paper_id or "").strip()
        if not pid or pid in seen:
            continue
        seen.add(pid)
        checks.append(
            await asyncio.to_thread(
                check_claim,
                pm.llm_router,
                statement=request.statement,
                paper_id=pid,
                title=request.titles.get(pid, ""),
                evidence_text=request.evidence_texts.get(pid, ""),
                provider=request.provider,
                model=request.model,
                pdf_base_dir=request.pdf_base_dir,
                metadata_db_path=request.metadata_db_path,
                escalate_whole_paper=request.escalate_whole_paper,
            )
        )
    return {"statement": request.statement, "checks": checks}
