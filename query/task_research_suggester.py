"""Forschungsrichtungen aus Task-Spec + KG-Kontext vorschlagen.

Im Task-Focused Mode schlägt das System dem User vor, *wohin* recherchiert
werden sollte, bevor die Tiefenanalyse losgeht. Die Vorschläge stützen sich auf
(a) die ``suggested_directions`` aus dem Task-Spec und (b) einen leichten KG-Scan,
der die bereits vorhandenen Paper-Themen im Projekt aggregiert, so dass die
Richtungen nicht das KG bereits abdecken.

Das LLM geht **immer** über ``LLMRouter``. KG-Zugriff via ``kg_retriever`` /
direkte DuckDB-Summe — bewusst leichtgewichtig, kein Full-Embedding-Scan.
"""

from __future__ import annotations

import json
from typing import Any

from query.llm_router import LLMRouter

_SUGGESTER_SYSTEM = (
    "You are a senior research strategist. Given a task specification and the "
    "themes already covered by papers in the project, propose 4-6 concrete, "
    "diverse research directions to pursue next. Each direction must be distinct "
    "and actionable. Bias toward non-obvious, cross-domain approaches a "
    "mainstream solution would miss. Respond ONLY with JSON."
)

_SUGGESTER_SCHEMA = (
    'Return JSON: {"directions": [{"label": "...", "rationale": "...", '
    '"keywords": ["..."], "novelty": "mainstream|established|exploratory|cross-domain"}]}.\n'
    "- label: 2-6 words, specific to the task.\n"
    "- rationale: 1-2 sentences why this is worth pursuing for THIS task.\n"
    "- keywords: 2-5 search terms for the literature harvest.\n"
    "- novelty: how far from the obvious mainstream path (cross-domain = most "
    "non-obvious, draws on a different field)."
)


def suggest_research_directions(
    llm_router: LLMRouter,
    task_spec: dict[str, Any],
    project_id: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
    provider: str | None = None,
    model: str | None = None,
    creativity_level: int = 3,
) -> dict[str, Any]:
    """Return 4-6 research directions tailored to the task + existing KG themes.

    ``creativity_level`` (1-5) steers the prompt toward mainstream (1) or
    aggressive cross-domain (5). See the Creativity-Slider design
    (TASK_FOCUSED_MODE_PLAN.md).
    """
    existing_themes = (
        _project_themes(metadata_db_path, project_id) if project_id else []
    )
    spec_directions = task_spec.get("suggested_directions") or []

    creativity_hint = _creativity_hint(creativity_level)
    context_block = _build_context_block(task_spec, spec_directions, existing_themes)

    messages = [
        {
            "role": "system",
            "content": _SUGGESTER_SYSTEM
            + " "
            + _SUGGESTER_SCHEMA
            + "\n"
            + creativity_hint,
        },
        {"role": "user", "content": context_block},
    ]
    overrides: dict[str, Any] = {
        "temperature": 0.4 + 0.1 * creativity_level,
        "max_tokens": 1000,
    }
    if model:
        overrides["model"] = model
    try:
        payload = llm_router.chat_json(messages, provider=provider, overrides=overrides)
    except Exception as exc:  # noqa: BLE001
        return {"directions": [], "error": f"LLM-Vorschlag fehlgeschlagen: {exc}"}

    return normalize_directions(payload)


def normalize_directions(payload: Any) -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    raw = payload.get("directions") or []
    out: list[dict[str, Any]] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict):
                keywords = item.get("keywords") or []
                if not isinstance(keywords, list):
                    keywords = []
                out.append(
                    {
                        "label": str(item.get("label") or "").strip(),
                        "rationale": str(item.get("rationale") or "").strip(),
                        "keywords": [
                            str(k).strip() for k in keywords if str(k).strip()
                        ][:8],
                        "novelty": str(item.get("novelty") or "exploratory").strip()
                        or "exploratory",
                    }
                )
            elif isinstance(item, str) and item.strip():
                out.append(
                    {
                        "label": item.strip(),
                        "rationale": "",
                        "keywords": [],
                        "novelty": "exploratory",
                    }
                )
    return {"directions": out[:8]}


def _creativity_hint(level: int) -> str:
    level = max(1, min(5, int(level)))
    if level <= 1:
        return "Kreativitätslevel 1: konservativ — Mainstream-Ansätze zuerst, bewährte Pfade."
    if level == 2:
        return (
            "Kreativitätslevel 2: leicht variierend — Mainstream plus eine Alternative."
        )
    if level == 3:
        return "Kreativitätslevel 3: mittig — bewährte und exploratorische Ansätze gemischt."
    if level == 4:
        return "Kreativitätslevel 4: explorativ — nicht offensichtliche, aber plausible Ansätze."
    return (
        "Kreativitätslevel 5: aggressiv — cross-domain, unkonventionell. "
        "Ziehe Methoden aus fremden Fachgebieten heran, auch wenn der Bezug erst "
        "über Metapher/Analogie erkennbar wird. Klare Begründung obligatorisch."
    )


def _build_context_block(
    task_spec: dict[str, Any],
    spec_directions: list[dict[str, Any]],
    existing_themes: list[str],
) -> str:
    title = str(task_spec.get("title") or "Aufgabe")
    objective = str(task_spec.get("objective") or "")
    eval_text = str(task_spec.get("evaluation") or "")
    rules = task_spec.get("rules") or []
    constraints = task_spec.get("constraints") or []

    parts: list[str] = [f"# Aufgabe: {title}"]
    if objective:
        parts.append(f"## Ziel\n{objective}")
    if eval_text:
        parts.append(f"## Bewertung\n{eval_text}")
    if rules:
        parts.append("## Regeln\n" + "\n".join(f"- {r}" for r in rules))
    if constraints:
        parts.append("## Constraints\n" + "\n".join(f"- {c}" for c in constraints))
    if spec_directions:
        lines = []
        for d in spec_directions:
            label = d.get("label") or "?"
            kw = ", ".join(d.get("keywords") or [])
            lines.append(f"- {label} (keywords: {kw})" if kw else f"- {label}")
        parts.append("## Erste Vorschläge aus dem Task-Spec\n" + "\n".join(lines))
    if existing_themes:
        parts.append(
            "## Bereits im Projekt vertretene Themen\n"
            + ", ".join(existing_themes[:30])
        )
    parts.append(
        "Schlage jetzt 4-6 FORSCHUNGSRICHTUNGEN vor, die über die oben genannten hinausgehen und das KG ergänzen."
    )
    return "\n\n".join(parts)


def _project_themes(metadata_db_path: str, project_id: str | None) -> list[str]:
    """Lightweight aggregation: titles of recent papers in the project.

    Paper→project mapping lives in ``projects.json`` (paper_ids list per
    project); titles come from the DuckDB ``papers`` table. Intentionally cheap
    — we only want a flavour of what is already covered, not a full embedding
    scan. Errors are swallowed (KG empty / DB missing → []).
    """
    if not project_id:
        return []
    try:
        import json as _json
        from pathlib import Path

        from storage.metadata_db import MetadataDB as _MDB

        projects_path = Path("data/projects.json")
        paper_ids: list[str] = []
        if projects_path.exists():
            try:
                data = _json.loads(projects_path.read_text(encoding="utf-8"))
                ids = data.get(project_id) or []
                if isinstance(ids, list):
                    paper_ids = [str(pid).strip() for pid in ids if str(pid).strip()]
            except (ValueError, OSError):
                paper_ids = []
        if not paper_ids:
            return []
        with _MDB(metadata_db_path) as db:
            papers = db.list_papers(limit=len(paper_ids) * 2 + 50)
        title_by_id = {
            str(p.get("id")): str(p.get("title") or "").strip() for p in papers
        }
        themes = [title_by_id[pid] for pid in paper_ids if title_by_id.get(pid)]
        return themes[:40]
    except Exception:  # noqa: BLE001
        return []
