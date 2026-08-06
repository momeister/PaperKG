"""Task-Spec-Extraktion für den Task-Focused Mode.

Aus einer URL (z. B. Kaggle-Competition-Seite), einer PDF oder einem Freitext
extrahiert das LLM einen strukturierten Task-Spec:

    {
      "title": "...",
      "objective": "...",
      "evaluation": "...",
      "datasets": [{"name": "...", "install_url": "...", "size": "...", "license": "..."}],
      "timeline": {"start": "...", "end": "..."},
      "rules": ["..."],
      "constraints": ["..."],
      "suggested_directions": [{"label": "...", "rationale": "...", "keywords": ["..."]}]
    }

Das LLM geht **immer** über ``LLMRouter`` (siehe AGENTS.md). URL-Fetches nutzen
``httpx`` und werden vorab über ``harvester.url_guard.is_safe_public_url``
geprüft (SSRF-Schutz). PDFs laufen über den lokalen ``MarkerParser``
(``parsing/marker_parser.py``), der pdfplumber/pypdf in einem Schutzhülle-Prozess
ausführt — niemals ``multiprocessing`` direkt.
"""

from __future__ import annotations

import json
import re
from typing import Any

import httpx

from harvester.url_guard import is_safe_public_url
from parsing.marker_parser import MarkerParser
from query.llm_router import LLMRouter
from research.sanitize import FULL_TEXT_MAX_LEN, sanitize_web_text

#: Obergrenze für den Rohtext, der dem LLM vorgelegt wird (Tokens ~ chars/4).
_SOURCE_TEXT_MAX_LEN = 24000

_EXTRACTOR_SYSTEM = (
    "You are a competition analyst and research engineer. Given the raw text of a "
    "hackathon/competition page, ticket, PDF brief, or free-form task description, "
    "extract a structured task specification that a researcher can act on. "
    "Respond ONLY with JSON (no markdown, no commentary)."
)

_EXTRACTOR_SCHEMA = (
    'Return JSON of the form: {"title": "...", "objective": "...", '
    '"evaluation": "...", "datasets": [{"name": "...", "install_url": "...", '
    '"size": "...", "license": "..."}], "timeline": {"start": "...", "end": "..."}, '
    '"rules": ["..."], "constraints": ["..."], "suggested_directions": ['
    '{"label": "...", "rationale": "...", "keywords": ["..."]}]}.\n'
    "- objective: what must be built/predicted/answered (1-3 sentences).\n"
    "- evaluation: metric, leaderboard, judging criteria — quote if explicit.\n"
    "- datasets: every dataset mentioned with its install/download URL if given "
    "(Kaggle competition data, external links). Leave install_url null if absent.\n"
    "- timeline: start/end dates if stated, else empty strings.\n"
    "- rules: eligibility, submission format, team size, allowed resources.\n"
    "- constraints: compute, license, privacy, data-use restrictions.\n"
    "- suggested_directions: 3-6 concrete, distinct technical approaches a "
    "researcher could pursue — diverse, not all the mainstream path. Each with a "
    "short rationale and 2-5 keywords for literature search."
)


def extract_task_spec(
    llm_router: LLMRouter,
    source_kind: str,
    source_url: str | None = None,
    source_text: str | None = None,
    source_pdf_path: str | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Extract a normalized TaskSpec from one of three input kinds.

    ``source_kind`` ∈ {'url', 'pdf', 'text'}. For 'url' the page is fetched
    server-side (SSRF-guarded) and sanitized; for 'pdf' the file is parsed via
    ``MarkerParser``; for 'text' the raw string is used directly. The LLM then
    produces the structured spec.

    Returns the normalized TaskSpec dict (see ``normalize_task_spec``). On any
    fetch/parse failure the spec is returned with an ``error`` field and empty
    fields — the caller (router) decides on the HTTP status.
    """
    raw_text, resolved_url, resolved_pdf_path = _gather_source(
        source_kind=source_kind,
        source_url=source_url,
        source_text=source_text,
        source_pdf_path=source_pdf_path,
    )
    if not raw_text.strip():
        return {
            "title": _fallback_title(source_kind, source_url, source_text),
            "objective": "",
            "evaluation": "",
            "datasets": [],
            "timeline": {"start": "", "end": ""},
            "rules": [],
            "constraints": [],
            "suggested_directions": [],
            "error": "Keine verwertbare Quelle extrahiert (leerer Text).",
        }

    excerpt = raw_text[:_SOURCE_TEXT_MAX_LEN]
    messages = [
        {"role": "system", "content": _EXTRACTOR_SYSTEM + " " + _EXTRACTOR_SCHEMA},
        {"role": "user", "content": f"Source text:\n{excerpt}"},
    ]
    overrides: dict[str, Any] = {"temperature": 0.2, "max_tokens": 1600}
    if model:
        overrides["model"] = model
    try:
        payload = llm_router.chat_json(messages, provider=provider, overrides=overrides)
    except Exception as exc:  # noqa: BLE001 — surface as structured error
        spec = _empty_spec()
        spec["error"] = f"LLM-Extraktion fehlgeschlagen: {exc}"
        spec["title"] = _fallback_title(source_kind, source_url, source_text)
        return spec

    spec = normalize_task_spec(payload)
    spec["source_url"] = resolved_url
    spec["source_pdf_path"] = resolved_pdf_path
    if not spec.get("title"):
        spec["title"] = _fallback_title(source_kind, source_url, source_text)
    return spec


def _gather_source(
    source_kind: str,
    source_url: str | None,
    source_text: str | None,
    source_pdf_path: str | None,
) -> tuple[str, str | None, str | None]:
    """Resolve the raw source text. Returns (text, resolved_url, resolved_pdf_path)."""
    kind = (source_kind or "text").strip().lower()
    if kind == "text":
        return str(source_text or "").strip(), None, None
    if kind == "url":
        url = (source_url or "").strip()
        if not url:
            return "", None, None
        text = _fetch_url_text(url)
        return text, url, None
    if kind == "pdf":
        path = (source_pdf_path or "").strip()
        if not path:
            return "", None, None
        text = _parse_pdf_text(path)
        return text, None, path
    # Unknown kind → treat as text
    return str(source_text or "").strip(), None, None


def _fetch_url_text(url: str) -> str:
    """SSRF-guarded synchronous URL fetch. Returns sanitized text (empty on failure)."""
    if not is_safe_public_url(url):
        return ""
    try:
        with httpx.Client(
            timeout=30.0,
            headers={"User-Agent": "ScienceKG/TaskFocused (local)"},
            follow_redirects=True,
        ) as client:
            response = client.get(url)
            response.raise_for_status()
    except Exception:  # noqa: BLE001
        return ""
    raw_html = response.text or ""
    clean, _flags = sanitize_web_text(raw_html, max_len=FULL_TEXT_MAX_LEN)
    return clean


def _parse_pdf_text(path: str) -> str:
    """Parse a local PDF via the guarded MarkerParser. Returns text (empty on failure)."""
    try:
        parsed = MarkerParser().parse(path, paper_id="task_ingest")
        return str(parsed.text or "")
    except Exception:  # noqa: BLE001
        return ""


def _empty_spec() -> dict[str, Any]:
    return {
        "title": "",
        "objective": "",
        "evaluation": "",
        "datasets": [],
        "timeline": {"start": "", "end": ""},
        "rules": [],
        "constraints": [],
        "suggested_directions": [],
    }


def _fallback_title(
    source_kind: str, source_url: str | None, source_text: str | None
) -> str:
    if source_kind == "url" and source_url:
        # Letzte Pfadkomponente, Menschen-lesbar gemacht.
        slug = source_url.rstrip("/").rsplit("/", 1)[-1]
        slug = re.sub(r"[-_]+", " ", slug).strip()
        return slug[:120] or "Aufgabe"
    if source_text:
        first_line = (
            str(source_text).strip().splitlines()[0] if source_text.strip() else ""
        )
        return (first_line or "Aufgabe")[:120]
    return "Aufgabe"


def normalize_task_spec(payload: Any) -> dict[str, Any]:
    """Coerce a raw LLM payload into the canonical TaskSpec shape."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}

    def _str(value: Any) -> str:
        return str(value or "").strip()

    raw_datasets = payload.get("datasets") or []
    datasets: list[dict[str, Any]] = []
    if isinstance(raw_datasets, list):
        for item in raw_datasets:
            if isinstance(item, dict):
                datasets.append(
                    {
                        "name": _str(item.get("name")),
                        "install_url": _str(item.get("install_url")) or None,
                        "size": _str(item.get("size")) or None,
                        "license": _str(item.get("license")) or None,
                    }
                )
            elif isinstance(item, str) and item.strip():
                datasets.append(
                    {
                        "name": item.strip(),
                        "install_url": None,
                        "size": None,
                        "license": None,
                    }
                )

    raw_timeline = payload.get("timeline") or {}
    timeline = {
        "start": (
            _str(raw_timeline.get("start")) if isinstance(raw_timeline, dict) else ""
        ),
        "end": _str(raw_timeline.get("end")) if isinstance(raw_timeline, dict) else "",
    }

    raw_directions = payload.get("suggested_directions") or []
    directions: list[dict[str, Any]] = []
    if isinstance(raw_directions, list):
        for item in raw_directions:
            if isinstance(item, dict):
                keywords_raw = item.get("keywords") or []
                keywords = (
                    [str(k).strip() for k in keywords_raw if str(k).strip()]
                    if isinstance(keywords_raw, list)
                    else []
                )
                directions.append(
                    {
                        "label": _str(item.get("label")),
                        "rationale": _str(item.get("rationale")),
                        "keywords": keywords[:8],
                    }
                )
            elif isinstance(item, str) and item.strip():
                directions.append(
                    {"label": item.strip(), "rationale": "", "keywords": []}
                )

    return {
        "title": _str(payload.get("title")),
        "objective": _str(payload.get("objective")),
        "evaluation": _str(payload.get("evaluation")),
        "datasets": datasets,
        "timeline": timeline,
        "rules": [_str(r) for r in (payload.get("rules") or []) if _str(r)],
        "constraints": [_str(c) for c in (payload.get("constraints") or []) if _str(c)],
        "suggested_directions": directions[:8],
    }
