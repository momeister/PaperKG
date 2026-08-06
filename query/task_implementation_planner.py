"""Implementationsplan aus Task-Spec + KG + Tiefenanalyse-Synthese.

Der User möchte im Task-Focused Mode nicht nur wissen, *was* zu tun ist, sondern
*wie*. Der Planner baut pro Variante (bzw. pro Richtung) eine Karte mit konkreten
Schritten, die der User abarbeiten kann. Jeder Schritt zitiert Paper-IDs
(``[arxiv:...]``) oder Grey-Sources (``grey::...``) — keine bloßen ``[1]``
(siehe AGENTS.md Konvention).

Im Session-3-Ausbau wird der Planner auch die interaktiven User-Schritte
anfordern; hier in Session 1 liefert er nur den initialen Plan.
"""

from __future__ import annotations

import json
from typing import Any

from query.llm_router import LLMRouter

_PLANNER_SYSTEM = (
    "You are a pragmatic research engineer and mentor. Given a task spec, a "
    "research direction, and context from the knowledge graph (papers + grey "
    "sources), produce a concrete, stepwise implementation plan a researcher can "
    "follow. Each step must be actionable and cite the source it draws on. "
    "Respond ONLY with JSON."
)

_PLANNER_SCHEMA = (
    'Return JSON: {"variant_label": "...", "steps": [{"label": "...", '
    '"detail": "...", "citations": ["[arxiv:...]", "grey::..."], '
    '"expected_outcome": "...", "effort": "low|medium|high"}], '
    '"risks": ["..."], "differentiator": "..."}.\n'
    "- variant_label: 2-6 words naming this approach.\n"
    "- steps: ordered, 3-7 steps. Cite local paper IDs or grey:: sources only; "
    "NEVER bare [1]. If no direct citation, leave citations empty.\n"
    "- expected_outcome: what a successful step produces (data, model, metric).\n"
    "- effort: rough sizing.\n"
    "- risks: 1-3 pitfalls specific to this approach.\n"
    "- differentiator: what makes this approach stand out vs the mainstream."
)


def build_implementation_plan(
    llm_router: LLMRouter,
    task_spec: dict[str, Any],
    direction: dict[str, Any],
    kg_context: str = "",
    provider: str | None = None,
    model: str | None = None,
    creativity_level: int = 3,
) -> dict[str, Any]:
    """Produce one implementation-plan card for a single research direction.

    ``kg_context`` is a pre-formatted string of relevant paper snippets / grey
    source excerpts the caller assembled (keeps this function free of DB
    access — testable without a DB).
    """
    creativity_hint = _creativity_hint(creativity_level)
    context_block = _build_context_block(task_spec, direction, kg_context)

    messages = [
        {
            "role": "system",
            "content": _PLANNER_SYSTEM + " " + _PLANNER_SCHEMA + "\n" + creativity_hint,
        },
        {"role": "user", "content": context_block},
    ]
    overrides: dict[str, Any] = {
        "temperature": 0.35 + 0.1 * creativity_level,
        "max_tokens": 1400,
    }
    if model:
        overrides["model"] = model
    try:
        payload = llm_router.chat_json(messages, provider=provider, overrides=overrides)
    except Exception as exc:  # noqa: BLE001
        return {
            "variant_label": direction.get("label") or "Variante",
            "steps": [],
            "risks": [],
            "differentiator": "",
            "plan_markdown": "",
            "creativity_level": int(creativity_level),
            "error": f"Planer fehlgeschlagen: {exc}",
        }
    plan = normalize_plan(payload, fallback_label=direction.get("label") or "Variante")
    # creativity_level vom Aufrufer durchreichen (das LLM kennt es nicht),
    # damit das Frontend es anzeigen kann.
    plan["creativity_level"] = int(creativity_level)
    return plan


def normalize_plan(payload: Any, fallback_label: str = "Variante") -> dict[str, Any]:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            payload = {}
    if not isinstance(payload, dict):
        payload = {}
    raw_steps = payload.get("steps") or []
    steps: list[dict[str, Any]] = []
    if isinstance(raw_steps, list):
        for item in raw_steps:
            if isinstance(item, dict):
                citations = item.get("citations") or []
                if not isinstance(citations, list):
                    citations = []
                # Sanitize: drop bare numeric citations, keep [arxiv:...] and grey::...
                clean_cites = []
                for c in citations:
                    cs = str(c or "").strip()
                    if not cs:
                        continue
                    if (
                        cs.startswith("[arxiv:")
                        or cs.startswith("[doi:")
                        or cs.startswith("grey::")
                        or cs.startswith("[grey::")
                    ):
                        clean_cites.append(cs)
                    # silently drop bare [1]/[2]/... per AGENTS.md convention
                label = str(item.get("label") or "").strip()
                detail = str(item.get("detail") or "").strip()
                expected_outcome = str(item.get("expected_outcome") or "").strip()
                steps.append(
                    {
                        "label": label,
                        "detail": detail,
                        # Frontend-Aliase (TaskFocusedPane liest text/rationale/
                        # citation).citation ist der erste saubere Verweis als
                        # String, da die UI einen Einzel-String anzeigt.
                        "text": label,
                        "rationale": detail or expected_outcome,
                        "citation": clean_cites[0] if clean_cites else "",
                        "citations": clean_cites,
                        "expected_outcome": expected_outcome,
                        "effort": str(item.get("effort") or "medium").strip().lower()
                        or "medium",
                    }
                )
    raw_risks = payload.get("risks") or []
    risks = (
        [str(r).strip() for r in raw_risks if str(r).strip()]
        if isinstance(raw_risks, list)
        else []
    )
    variant_label = (
        str(payload.get("variant_label") or fallback_label).strip() or fallback_label
    )
    differentiator = str(payload.get("differentiator") or "").strip()
    # plan_markdown: vom Frontend (TaskFocusedPane, ParallelResultsTab) als
    # ``plan.plan_markdown.trim()`` verwendet — muss immer ein String sein,
    # sonst TypeError → weißer Screen. Wir bauen ein kompaktes Markdown-Dump
    # der Schritte, wenn das LLM kein explizites Feld lieferte.
    plan_markdown = str(payload.get("plan_markdown") or "").strip()
    if not plan_markdown and steps:
        md_lines: list[str] = [f"## {variant_label}"]
        if differentiator:
            md_lines.append(f"\n_Differenzierer:_ {differentiator}")
        for i, s in enumerate(steps, 1):
            line = f"{i}. {s['label']}"
            if s["detail"]:
                line += f" — {s['detail']}"
            if s["expected_outcome"]:
                line += f"\n   _Erwartet:_ {s['expected_outcome']}"
            if s["citations"]:
                line += "\n   _Quellen:_ " + ", ".join(s["citations"])
            md_lines.append(line)
        if risks:
            md_lines.append("\n_Risiken:_")
            for r in risks:
                md_lines.append(f"- {r}")
        plan_markdown = "\n".join(md_lines)
    return {
        "variant_label": variant_label,
        "steps": steps[:10],
        "risks": risks[:6],
        "differentiator": differentiator,
        # Frontend-Felder — siehe Kommentar oben.
        "plan_markdown": plan_markdown,
        "creativity_level": int(payload.get("creativity_level") or 3),
    }


def _creativity_hint(level: int) -> str:
    level = max(1, min(5, int(level)))
    if level <= 1:
        return "Kreativitätslevel 1: konservativ — bewährte Pipeline, reproduzierbar."
    if level == 2:
        return "Kreativitätslevel 2: leicht erweitert — bewährte Pipeline + eine Modifikation."
    if level == 3:
        return (
            "Kreativitätslevel 3: mittig — bewährt mit klarem Differenzierungs-Schritt."
        )
    if level == 4:
        return "Kreativitätslevel 4: explorativ — mindestens ein Schritt zieht eine Methode aus einem Nachbargebiet."
    return (
        "Kreativitätslevel 5: aggressiv cross-domain — mindestens ein Schritt adaptiert "
        "eine Methode aus einem fremden Fachgebiet mit klarer Begründung, warum sie hier greift."
    )


def _build_context_block(
    task_spec: dict[str, Any],
    direction: dict[str, Any],
    kg_context: str,
) -> str:
    title = str(task_spec.get("title") or "Aufgabe")
    objective = str(task_spec.get("objective") or "")
    eval_text = str(task_spec.get("evaluation") or "")

    parts: list[str] = [f"# Aufgabe: {title}"]
    if objective:
        parts.append(f"## Ziel\n{objective}")
    if eval_text:
        parts.append(f"## Bewertung\n{eval_text}")
    parts.append("## Verfolgte Forschungsrichtung")
    parts.append(f"- Richtung: {direction.get('label') or '?'}")
    if direction.get("rationale"):
        parts.append(f"- Begründung: {direction['rationale']}")
    kw = ", ".join(direction.get("keywords") or [])
    if kw:
        parts.append(f"- Keywords: {kw}")
    if kg_context.strip():
        parts.append(
            "## Kontext aus Knowledge Graph + Tiefenanalyse\n"
            + kg_context.strip()[:8000]
        )
    parts.append(
        "Erstelle jetzt einen konkreten, schrittweisen Implementationsplan für diese Richtung."
    )
    return "\n\n".join(parts)
