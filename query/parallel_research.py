"""Parallel-Research mode: grounded variant proposals, per-result feedback, and a
final cross-variant synthesis.

The user poses a problem; the assistant proposes several concrete *Varianten* (each with
an approach, a rationale and a ready-to-copy prompt for an external coding AI), grounded
on the local papers. The user tries the variants, feeds results back per variant, the
assistant comments on each, and a final synthesis ranks them and reshapes the answer.

Everything reuses the grounded answer pipeline (``GroundedResponder`` +
``_citation_links_for_answer``) so the outputs carry ``[arxiv:...]`` citations and the
same ``citation_links``/``evidence`` shape the frontend already renders.
"""
from __future__ import annotations

from typing import Any

from query.grounded_responder import GroundedResponder, _citation_links_for_answer
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import Evidence, Source

MAX_EVIDENCE = 24
EVIDENCE_SNIPPET_CHARS = 500


def _gather_evidence(
    retriever: HybridRetriever,
    query: str,
    paper_ids: list[str] | None = None,
    limit: int = 10,
) -> tuple[list[Evidence], list[Source]]:
    """Top evidence + sources for a query, flattened and capped for prompting."""
    hits = retriever.search(query, limit=limit, paper_ids=paper_ids or None)
    sources: dict[str, Source] = {}
    evidence: list[Evidence] = []
    for hit in hits:
        sources.setdefault(hit.source.paper_id, hit.source)
        evidence.extend(hit.evidence)
    evidence.sort(key=lambda item: float(item.score), reverse=True)
    return evidence[:MAX_EVIDENCE], list(sources.values())


def _evidence_block(evidence: list[Evidence]) -> str:
    lines: list[str] = []
    for item in evidence:
        text = " ".join(str(item.text or "").split())[:EVIDENCE_SNIPPET_CHARS]
        if text:
            lines.append(f"[{item.paper_id}] ({item.kind}) {text}")
    return "\n".join(lines)


def propose_variants(
    retriever: HybridRetriever,
    llm_router: Any,
    question: str,
    *,
    n: int = 3,
    paper_ids: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict[str, str]]:
    """Propose ``n`` grounded variants for a problem. Each is a plain-field dict:
    ``{name, approach, rationale, suggested_prompt}`` (texts may contain [arxiv:...])."""
    evidence, _ = _gather_evidence(retriever, question, paper_ids=paper_ids, limit=10)
    block = _evidence_block(evidence) or "(keine lokale Evidenz gefunden)"
    system = (
        "Du bist ein Forschungsassistent, der für ein Software-/Forschungsproblem mehrere "
        "konkrete, klar unterscheidbare Lösungs-VARIANTEN vorschlägt. Stütze dich auf die "
        "gegebene Evidenz und zitiere Paper-IDs in eckigen Klammern, z. B. [arxiv:1234.5678]. "
        "Erfinde keine Quellen."
    )
    user = (
        f"Problem/Frage:\n{question}\n\n"
        f"Lokale Evidenz (Paper-Auszüge):\n{block}\n\n"
        f"Erzeuge genau {n} Varianten, wie man das angehen könnte. Antworte ausschließlich als "
        "JSON-Objekt in dieser Form:\n"
        '{"variants": [{'
        '"name": "kurzer prägnanter Titel der Variante", '
        '"approach": "1-3 Sätze: was konkret gemacht wird (mit [Zitaten])", '
        '"rationale": "warum das laut Evidenz sinnvoll ist (mit [Zitaten])", '
        '"prompt": "ein ausführlicher, fertig kopierbarer Prompt für ein Coding-KI-Tool, das '
        "diese Variante umsetzt. Strukturiere ihn klar mit Abschnitten: Kontext (worum geht es), "
        "Ziel, konkrete Schritte (nummeriert), Rahmenbedingungen/Einschränkungen und Was "
        'zurückgemeldet werden soll. Sei spezifisch und umsetzbar."'
        "}]}\n"
        "Nur das JSON, kein Text davor oder danach."
    )
    overrides: dict[str, Any] = {"temperature": 0.4}
    if model:
        overrides["model"] = model
    try:
        data = llm_router.chat_json(
            [{"role": "system", "content": system}, {"role": "user", "content": user}],
            provider=provider,
            overrides=overrides,
        )
    except Exception:
        data = {}
    raw = data.get("variants") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raw = []
    variants: list[dict[str, str]] = []
    for item in raw[:n]:
        if not isinstance(item, dict):
            continue
        variants.append({
            "name": str(item.get("name") or "").strip() or f"Variante {len(variants) + 1}",
            "approach": str(item.get("approach") or "").strip(),
            "rationale": str(item.get("rationale") or "").strip(),
            "suggested_prompt": str(item.get("prompt") or item.get("suggested_prompt") or "").strip(),
        })
    return variants


def propose_overview(
    retriever: HybridRetriever,
    llm_router: Any,
    question: str,
    *,
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
) -> dict[str, Any]:
    """Grounded *explanation* of the problem before the concrete variants.

    Returns an ``Answer``-shaped dict (with ``[arxiv:...]`` citations + ``source_verification``,
    so the frontend renders clickable, openable sources). The answer is structured in two
    sections — what the task is / what to understand, and how to approach it."""
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    prompt = (
        f"Frage/Problem: {question}\n\n"
        "Erkläre dem Nutzer dieses Vorhaben gegroundet auf die lokalen Paper. Gliedere deine "
        "Antwort in genau zwei Markdown-Abschnitte mit diesen Überschriften:\n"
        "## Worum geht es?\n"
        "Erkläre, worum es bei der Aufgabe genau geht und was man dafür verstehen sollte "
        "(zentrale Begriffe, bisheriger Stand, was bereits bekannt ist).\n"
        "## Wie gehst du ran?\n"
        "Erkläre Schritt für Schritt, wie man methodisch an die Aufgabe herangeht.\n\n"
        "Belege beide Abschnitte mit Paper-IDs in eckigen Klammern (z. B. [arxiv:1234.5678]). "
        "Schreibe verständlich und konkret, erfinde keine Quellen."
    )
    answer = responder.answer(
        prompt,
        limit=10,
        provider=provider,
        model=model,
        paper_ids=paper_ids or None,
        project_id=project_id,
        metadata_db_path=metadata_db_path,
    )
    return answer.to_dict()


def feedback_for_entry(
    retriever: HybridRetriever,
    llm_router: Any,
    *,
    question: str,
    variant: dict[str, Any],
    user_result: str,
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
) -> dict[str, Any]:
    """Grounded short assessment of a result the user submitted for one variant."""
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    prompt = (
        f"Ausgangsfrage: {question}\n\n"
        f"Variante \"{variant.get('name') or 'Variante'}\": {variant.get('approach') or ''}\n\n"
        f"Der Nutzer hat diese Variante ausprobiert und folgendes Ergebnis erhalten:\n"
        f"{user_result}\n\n"
        "Ordne dieses Ergebnis kurz und konkret ein (gegroundet auf die lokalen Paper): Was "
        "bedeutet es, passt es zur Literatur, was sind sinnvolle nächste Schritte? Zitiere "
        "Paper-IDs in eckigen Klammern."
    )
    answer = responder.answer(
        prompt,
        limit=8,
        provider=provider,
        model=model,
        paper_ids=paper_ids or None,
        project_id=project_id,
        metadata_db_path=metadata_db_path,
    )
    return answer.to_dict()


def followup_answer(
    retriever: HybridRetriever,
    llm_router: Any,
    *,
    question: str,
    original_question: str,
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
) -> dict[str, Any]:
    """Grounded answer to a follow-up question asked while a parallel session is open.

    Embeds the session's original question as context so it reads as a real follow-up, and
    returns an ``Answer``-shaped dict with ``[arxiv:...]`` citations + ``source_verification``."""
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    prompt = (
        f"Ausgangsfrage der Recherche: {original_question}\n\n"
        f"Folgefrage des Nutzers: {question}\n\n"
        "Beantworte die Folgefrage gegroundet auf die lokalen Paper, im Kontext der Ausgangsfrage. "
        "Belege deine Aussagen mit Paper-IDs in eckigen Klammern (z. B. [arxiv:1234.5678]). "
        "Schreibe konkret und verständlich, erfinde keine Quellen."
    )
    answer = responder.answer(
        prompt,
        limit=10,
        provider=provider,
        model=model,
        paper_ids=paper_ids or None,
        project_id=project_id,
        metadata_db_path=metadata_db_path,
    )
    return answer.to_dict()


def synthesize(
    retriever: HybridRetriever,
    llm_router: Any,
    *,
    question: str,
    variants: list[dict[str, Any]],
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
) -> dict[str, Any]:
    """Cross-variant analysis: rank the variants from the submitted results + literature
    and produce a final, reshaped answer to the original question."""
    blocks: list[str] = []
    for variant in variants:
        results = [
            str(entry.get("content") or "").strip()
            for entry in variant.get("entries", [])
            if entry.get("role") == "user" and str(entry.get("content") or "").strip()
        ]
        body = "\n".join(f"- {r}" for r in results) or "(keine Ergebnisse eingesendet)"
        blocks.append(
            f"Variante \"{variant.get('name') or 'Variante'}\": {variant.get('approach') or ''}\n"
            f"Eingesendete Ergebnisse:\n{body}"
        )
    joined = "\n\n".join(blocks) or "(keine Varianten)"
    prompt = (
        f"Ausgangsfrage: {question}\n\n"
        f"Es wurden mehrere Varianten ausprobiert:\n\n{joined}\n\n"
        "Analysiere gegroundet auf die lokalen Paper, welche Variante am besten funktioniert hat "
        "und warum. Gib ein klares Ranking, begründe es mit den eingesendeten Ergebnissen UND der "
        "Literatur (Paper-IDs in eckigen Klammern), und formuliere am Ende eine finale, neu "
        "gestaltete Empfehlung bzw. Antwort auf die Ausgangsfrage."
    )
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    answer = responder.answer(
        prompt,
        limit=10,
        provider=provider,
        model=model,
        paper_ids=paper_ids or None,
        project_id=project_id,
        metadata_db_path=metadata_db_path,
    )
    return answer.to_dict()


def citation_payload_for_text(text: str, evidence: list[Evidence], sources: list[Source]) -> dict[str, Any]:
    """Build an Answer-shaped dict for a free-text fragment (used if a variant's prose
    should be rendered with the rich citation UI). Kept for callers that need it."""
    links = _citation_links_for_answer(text, evidence)
    cited = {link["paper_id"] for link in links}
    chosen = [s for s in sources if s.paper_id in cited] or sources
    return {
        "question": "",
        "answer": text,
        "sources": [s.to_dict() for s in chosen],
        "evidence": [e.to_dict() for e in evidence],
        "citation_links": links,
        "no_answer": False,
    }
