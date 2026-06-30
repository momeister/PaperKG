"""Compile a Parallel-Research *Variante* into an actionable **task brief** that an
external, local computer-use agent (e.g. UI-TARS-Desktop) can execute.

PaperKG stays the *brain/context*: it knows the research question, the chosen approach
and the grounded literature. The external agent is the *eyes/hands*: it sees the screen
and drives mouse/keyboard. This module produces the hand-off in between — a crisp,
self-contained instruction (goal, context, numbered steps, constraints, success
criteria, artifacts) plus a single copy-/POST-ready plain-text rendering.

It is **text-in / text-out only** — no image/vision plumbing here. The VLM that sees the
screen lives *inside* the external agent, never in PaperKG. The compiler reuses the
grounded evidence helpers from :mod:`query.parallel_research` so the brief's context is
backed by the same local papers, and degrades to a deterministic brief built straight
from the variant when the LLM is unavailable (keeps the hand-off robust + unit-testable).
"""
from __future__ import annotations

from typing import Any

from query.parallel_research import _evidence_block, _gather_evidence

# Keep the brief tight: a GUI agent loop degrades with long, rambling instructions.
MAX_STEPS = 12
MAX_LIST_ITEMS = 8


def _clean_lines(value: Any, *, limit: int) -> list[str]:
    """Coerce an LLM list field into a deduped list of non-empty one-line strings."""
    out: list[str] = []
    seen: set[str] = set()
    items = value if isinstance(value, list) else [value]
    for item in items:
        text = " ".join(str(item or "").split()).strip()
        if not text or text.lower() in seen:
            continue
        seen.add(text.lower())
        out.append(text)
        if len(out) >= limit:
            break
    return out


def _steps_from_prompt(prompt: str) -> list[str]:
    """Best-effort extraction of numbered/bulleted steps from a suggested_prompt.

    Used only for the deterministic fallback (no/failed LLM). Returns the prompt as a
    single step when no list structure is found, so the agent still gets something."""
    steps: list[str] = []
    for raw in str(prompt or "").splitlines():
        line = raw.strip()
        if not line:
            continue
        stripped = line.lstrip("0123456789.)-*•› \t")
        # A line counts as a step only if it actually started with a list marker.
        if stripped != line and stripped:
            steps.append(stripped)
            if len(steps) >= MAX_STEPS:
                break
    if steps:
        return steps
    text = " ".join(str(prompt or "").split()).strip()
    return [text] if text else []


def _fallback_brief(variant: dict[str, Any], question: str) -> dict[str, Any]:
    """Deterministic brief straight from the variant — no LLM. Always succeeds."""
    name = str(variant.get("name") or "Variante").strip()
    approach = str(variant.get("approach") or "").strip()
    prompt = str(variant.get("suggested_prompt") or "").strip()
    goal = name if not approach else f"{name} — {approach}"
    return {
        "goal": goal[:600] or "Variante umsetzen",
        "context": (f"Ausgangsfrage: {question}\n\n{approach}".strip())[:1500],
        "steps": _steps_from_prompt(prompt) or [approach or name],
        "constraints": [],
        "success_criteria": [],
        "artifacts": [],
        "raw_prompt": prompt,
    }


_SYSTEM = (
    "Du wandelst einen Forschungs-/Umsetzungsplan in eine präzise Aufgabenanweisung für "
    "einen Computer-Use-Agenten um, der den Bildschirm des Nutzers sieht und Maus/Tastatur "
    "steuert. Die Anweisung muss konkret, schrittweise und eigenständig ausführbar sein. "
    "Erfinde keine Dateien/URLs, die nicht genannt wurden. Antworte ausschließlich als JSON."
)


def build_task_brief(
    variant: dict[str, Any],
    *,
    question: str,
    retriever: Any | None = None,
    llm_router: Any | None = None,
    paper_ids: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Turn a variant into a ``TaskBrief`` dict.

    Shape: ``{goal, context, steps[], constraints[], success_criteria[], artifacts[],
    raw_prompt}``. Uses ``llm_router`` to reshape the variant (optionally enriched with
    grounded evidence from ``retriever``) into an agent-ready instruction; on any failure
    it falls back to :func:`_fallback_brief`. The hand-off must never break the workspace,
    so this function never raises.
    """
    fallback = _fallback_brief(variant, question)
    if llm_router is None:
        return fallback

    evidence_block = ""
    if retriever is not None:
        try:
            evidence, _ = _gather_evidence(
                retriever, question, paper_ids=paper_ids or None, limit=6
            )
            evidence_block = _evidence_block(evidence)
        except Exception:
            evidence_block = ""

    user = (
        f"Ausgangsfrage/Problem:\n{question}\n\n"
        f"Gewählte Variante: {variant.get('name') or 'Variante'}\n"
        f"Ansatz: {variant.get('approach') or ''}\n"
        f"Begründung: {variant.get('rationale') or ''}\n\n"
        f"Vorhandener Umsetzungs-Prompt:\n{variant.get('suggested_prompt') or '(keiner)'}\n\n"
        + (f"Lokale Evidenz (zur Kontextualisierung):\n{evidence_block}\n\n" if evidence_block else "")
        + "Formuliere daraus eine Aufgabenanweisung für einen Desktop-Agenten. Antworte NUR als "
        "JSON in dieser Form:\n"
        '{"goal": "ein Satz: was am Ende erreicht sein soll", '
        '"context": "2-4 Sätze Hintergrund, damit der Agent die Aufgabe versteht", '
        '"steps": ["konkrete, nummerierbare Einzelschritte am Rechner"], '
        '"constraints": ["Rahmenbedingungen/Grenzen, z. B. nichts löschen, nur in Ordner X"], '
        '"success_criteria": ["woran man erkennt, dass es geklappt hat"], '
        '"artifacts": ["nur explizit genannte Dateien/URLs/Programme"]}\n'
        "Nur das JSON, kein Text davor oder danach."
    )
    overrides: dict[str, Any] = {"temperature": 0.2}
    if model:
        overrides["model"] = model
    try:
        data = llm_router.chat_json(
            [{"role": "system", "content": _SYSTEM}, {"role": "user", "content": user}],
            provider=provider,
            overrides=overrides,
        )
    except Exception:
        return fallback
    if not isinstance(data, dict):
        return fallback

    goal = " ".join(str(data.get("goal") or "").split()).strip()
    context = str(data.get("context") or "").strip()
    steps = _clean_lines(data.get("steps"), limit=MAX_STEPS)
    if not goal and not steps:
        return fallback
    return {
        "goal": (goal or fallback["goal"])[:600],
        "context": (context or fallback["context"])[:1500],
        "steps": steps or fallback["steps"],
        "constraints": _clean_lines(data.get("constraints"), limit=MAX_LIST_ITEMS),
        "success_criteria": _clean_lines(data.get("success_criteria"), limit=MAX_LIST_ITEMS),
        "artifacts": _clean_lines(data.get("artifacts"), limit=MAX_LIST_ITEMS),
        "raw_prompt": str(variant.get("suggested_prompt") or "").strip(),
    }


def render_task_brief_text(brief: dict[str, Any]) -> str:
    """Render a ``TaskBrief`` as one copy-/POST-ready plain-text instruction.

    This is the exact string handed to the external agent — pasted into UI-TARS-Desktop
    (Kanal A) or POSTed to the local bridge as the ``task`` (Kanal B)."""
    lines: list[str] = []
    goal = str(brief.get("goal") or "").strip()
    if goal:
        lines.append(f"Ziel: {goal}")
    context = str(brief.get("context") or "").strip()
    if context:
        lines.append("")
        lines.append(f"Kontext: {context}")

    def _section(title: str, key: str, *, numbered: bool = False) -> None:
        items = brief.get(key) or []
        if not isinstance(items, list) or not items:
            return
        lines.append("")
        lines.append(f"{title}:")
        for i, item in enumerate(items, 1):
            text = str(item or "").strip()
            if text:
                lines.append(f"{i}. {text}" if numbered else f"- {text}")

    _section("Schritte", "steps", numbered=True)
    _section("Rahmenbedingungen", "constraints")
    _section("Erfolgskriterien", "success_criteria")
    _section("Artefakte/Bezüge", "artifacts")
    return "\n".join(lines).strip()
