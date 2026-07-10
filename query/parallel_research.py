"""Parallel-Research mode: stage roadmap, grounded variant proposals, professor-style
structured reviews, and a final cross-stage synthesis.

The user poses a Forschungsvorhaben; the assistant splits it into sequential *Etappen*
(``propose_stages``) and proposes several concrete *Varianten* per stage (each with an
approach, a rationale and a ready-to-copy prompt for an external coding AI), grounded on
the local papers. The user tries the variants and feeds results back per variant; a
"Professor" reviews each result (``professor_review_entry``: Verständnis / Stärken /
Fehler & Probleme / Ideen / Nächste Schritte), reviews whole stages on demand
(``professor_review_stage``, incl. per-variant verdicts), and a final synthesis evaluates
the entire endeavour across stages (``synthesize``).

Everything reuses the grounded answer pipeline (``GroundedResponder`` +
``_citation_links_for_answer``) so the outputs carry ``[arxiv:...]`` citations and the
same ``citation_links``/``evidence`` shape the frontend already renders. Structured
reviews additionally carry a ``professor_review`` payload (schema_version 1); if the
LLM's JSON is unusable they degrade to the plain free-text grounded answer.
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
    stage: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    """Propose ``n`` grounded variants for a problem. Each is a plain-field dict:
    ``{name, approach, rationale, suggested_prompt}`` (texts may contain [arxiv:...]).
    With ``stage`` the variants target that Etappe only."""
    evidence, _ = _gather_evidence(retriever, question, paper_ids=paper_ids, limit=10)
    block = _evidence_block(evidence) or "(keine lokale Evidenz gefunden)"
    system = (
        "Du bist ein Forschungsassistent, der für ein Software-/Forschungsproblem mehrere "
        "konkrete, klar unterscheidbare Lösungs-VARIANTEN vorschlägt. Stütze dich auf die "
        "gegebene Evidenz und zitiere Paper-IDs in eckigen Klammern, z. B. [arxiv:1234.5678]. "
        "Erfinde keine Quellen."
    )
    stage_line = ""
    if stage:
        stage_name = str(stage.get("name") or "").strip()
        stage_goal = str(stage.get("goal") or "").strip()
        if stage_name or stage_goal:
            stage_line = (
                f"Aktuelle Etappe des Vorhabens: {stage_name}"
                + (f" — Ziel: {stage_goal}" if stage_goal else "")
                + ". Schlage Varianten NUR für diese Etappe vor.\n\n"
            )
    user = (
        f"Problem/Frage:\n{question}\n\n"
        f"{stage_line}"
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


def propose_stages(
    retriever: HybridRetriever,
    llm_router: Any,
    question: str,
    *,
    max_n: int = 5,
    existing_stages: list[dict[str, Any]] | None = None,
    paper_ids: list[str] | None = None,
    provider: str | None = None,
    model: str | None = None,
) -> list[dict[str, str]]:
    """Split a Forschungsvorhaben into 2–``max_n`` sequential Etappen.

    Returns plain-field dicts ``{name, goal}``. ``existing_stages`` (when proposing
    additional stages later) is shown to the model to avoid duplicates. Tolerant to
    bad LLM output — returns ``[]`` on failure so callers can fall back."""
    evidence, _ = _gather_evidence(retriever, question, paper_ids=paper_ids, limit=10)
    block = _evidence_block(evidence) or "(keine lokale Evidenz gefunden)"
    system = (
        "Du bist ein erfahrener Forschungs-Mentor, der ein Forschungsvorhaben in klar "
        "abgegrenzte, sequentielle ETAPPEN gliedert (wie die Arbeitspakete einer "
        "Abschlussarbeit). Stütze dich auf die gegebene Evidenz. Erfinde keine Quellen."
    )
    existing_block = ""
    if existing_stages:
        lines = [
            f"- {str(s.get('name') or '').strip()}: {str(s.get('goal') or '').strip()}"
            for s in existing_stages
            if str(s.get("name") or "").strip()
        ]
        if lines:
            existing_block = (
                "Bereits geplante Etappen (KEINE Duplikate davon vorschlagen):\n"
                + "\n".join(lines)
                + "\n\n"
            )
    user = (
        f"Forschungsvorhaben:\n{question}\n\n"
        f"{existing_block}"
        f"Lokale Evidenz (Paper-Auszüge):\n{block}\n\n"
        f"Zerlege dieses Forschungsvorhaben in 2–{max_n} sequentielle ETAPPEN, die "
        "aufeinander aufbauen und am Ende gemeinsam ausgewertet werden können. Antworte "
        "ausschließlich als JSON-Objekt in dieser Form:\n"
        '{"stages": [{'
        '"name": "kurzer prägnanter Etappen-Titel", '
        '"goal": "1-2 Sätze: was diese Etappe erreichen soll und woran man ihren Abschluss erkennt"'
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
    raw = data.get("stages") if isinstance(data, dict) else None
    if not isinstance(raw, list):
        raw = []
    stages: list[dict[str, str]] = []
    for item in raw[:max_n]:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        if not name:
            continue
        stages.append({"name": name, "goal": str(item.get("goal") or "").strip()})
    return stages


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


# --------------------------------------------------------------------------- #
# Professor reviews (structured critique with grounded citations)               #
# --------------------------------------------------------------------------- #

PROFESSOR_SCHEMA_VERSION = 1

_PROFESSOR_SYSTEM = (
    "Du bist ein kritischer, wohlwollender Professor, der ein Forschungsvorhaben betreut. "
    "Du verstehst die Arbeit des Nutzers, erklärst Zusammenhänge, findest Fehler und "
    "Probleme, entwickelst Ideen und schlägst konkrete nächste Schritte vor. Stütze dich "
    "auf die gegebene Evidenz und zitiere Paper-IDs in eckigen Klammern, z. B. "
    "[arxiv:1234.5678]. Erfinde keine Quellen. Antworte ausschließlich mit dem geforderten "
    "JSON-Objekt, ohne Text davor oder danach."
)

_PROFESSOR_SECTIONS: list[tuple[str, str]] = [
    ("verstaendnis", "Verständnis"),
    ("staerken", "Stärken"),
    ("probleme", "Fehler & Probleme"),
    ("ideen", "Ideen"),
    ("naechste_schritte", "Nächste Schritte"),
]


def _str_list(value: Any) -> list[str]:
    """Coerce an LLM list field to a clean list[str] (drops empties/non-strings)."""
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def _professor_chat_json(
    llm_router: Any,
    user: str,
    *,
    provider: str | None,
    model: str | None,
) -> dict[str, Any] | None:
    overrides: dict[str, Any] = {"temperature": 0.3}
    if model:
        overrides["model"] = model
    try:
        data = llm_router.chat_json(
            [{"role": "system", "content": _PROFESSOR_SYSTEM}, {"role": "user", "content": user}],
            provider=provider,
            overrides=overrides,
        )
    except Exception:
        return None
    return data if isinstance(data, dict) else None


def _render_professor_markdown(review: dict[str, Any], *, kind: str) -> str:
    """Render a professor_review payload to the markdown stored/copied as content."""
    parts: list[str] = []
    if kind == "final":
        head = str(review.get("gesamtverstaendnis") or "").strip()
        if head:
            parts.append(f"### Gesamtverständnis\n{head}")
        fazits = [
            f"- **{str(item.get('name') or '').strip() or 'Etappe'}**: {str(item.get('fazit') or '').strip()}"
            for item in (review.get("etappen_zusammenfassung") or [])
            if isinstance(item, dict) and str(item.get("fazit") or "").strip()
        ]
        if fazits:
            parts.append("### Etappen-Fazit\n" + "\n".join(fazits))
    else:
        head = str(review.get("verstaendnis") or "").strip()
        if head:
            parts.append(f"### Verständnis\n{head}")
    for key, title in _PROFESSOR_SECTIONS[1:]:
        items = _str_list(review.get(key))
        if items:
            parts.append(f"### {title}\n" + "\n".join(f"- {item}" for item in items))
    if kind == "stage":
        verdicts = [
            f"- **{str(item.get('name') or '').strip() or 'Variante'}**: "
            f"{str(item.get('urteil') or '').strip()} — {str(item.get('begruendung') or '').strip()}"
            for item in (review.get("varianten_bewertung") or [])
            if isinstance(item, dict)
        ]
        if verdicts:
            parts.append("### Varianten-Bewertung\n" + "\n".join(verdicts))
    if kind == "final":
        offen = _str_list(review.get("offene_punkte"))
        if offen:
            parts.append("### Offene Punkte\n" + "\n".join(f"- {item}" for item in offen))
        final = str(review.get("finale_antwort") or "").strip()
        if final:
            parts.append(f"### Finale Antwort\n{final}")
    return "\n\n".join(parts)


def _professor_payload(
    markdown: str,
    evidence: list[Evidence],
    sources: list[Source],
    review: dict[str, Any],
    *,
    kind: str,
    question: str,
) -> dict[str, Any]:
    """Answer-shaped dict (rich citation UI) + the structured ``professor_review``."""
    payload = citation_payload_for_text(markdown, evidence, sources)
    payload["question"] = question
    payload["professor_review"] = {"schema_version": PROFESSOR_SCHEMA_VERSION, "kind": kind, **review}
    return payload


def _json_schema_block(fields: str) -> str:
    return (
        "Antworte ausschließlich als JSON-Objekt in dieser Form:\n"
        "{" + fields + "}\n"
        "Belege Aussagen in jedem Feld mit Paper-IDs in eckigen Klammern, wo die Evidenz "
        "es hergibt. Lasse Listen leer, wenn es nichts Substanzielles zu sagen gibt. "
        "Nur das JSON, kein Text davor oder danach."
    )


_PROFESSOR_FIELDS = (
    '"verstaendnis": "2-4 Sätze: was wurde gemacht und was bedeutet das Ergebnis", '
    '"staerken": ["was ist gut/belastbar"], '
    '"probleme": ["Fehler, Schwächen, Risiken, methodische Probleme"], '
    '"ideen": ["neue Ansätze/Ideen, die sich daraus ergeben"], '
    '"naechste_schritte": ["konkrete nächste Schritte"]'
)


def professor_review_entry(
    retriever: HybridRetriever,
    llm_router: Any,
    *,
    question: str,
    variant: dict[str, Any],
    user_result: str,
    stage: dict[str, Any] | None = None,
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
) -> dict[str, Any]:
    """Structured professor critique of one submitted result.

    Returns an Answer-shaped dict whose ``professor_review`` key carries the structured
    sections; on unusable LLM JSON it degrades to the plain free-text grounded assessment
    (no ``professor_review`` key) so submitting a result never breaks."""
    variant_name = str(variant.get("name") or "Variante")
    composite = f"{question}\n{variant_name} {variant.get('approach') or ''}\n{user_result}"
    evidence, sources = _gather_evidence(retriever, composite, paper_ids=paper_ids, limit=8)
    block = _evidence_block(evidence) or "(keine lokale Evidenz gefunden)"
    stage_line = ""
    if stage and str(stage.get("name") or "").strip():
        stage_line = (
            f"Aktuelle Etappe: {str(stage.get('name') or '').strip()}"
            + (f" — Ziel: {str(stage.get('goal') or '').strip()}" if str(stage.get("goal") or "").strip() else "")
            + "\n\n"
        )
    user = (
        f"Forschungsvorhaben: {question}\n\n"
        f"{stage_line}"
        f"Variante \"{variant_name}\": {variant.get('approach') or ''}\n\n"
        f"Der Nutzer hat diese Variante ausprobiert und folgendes Ergebnis eingereicht:\n"
        f"{user_result}\n\n"
        f"Lokale Evidenz (Paper-Auszüge):\n{block}\n\n"
        "Begutachte dieses Ergebnis wie ein betreuender Professor. "
        + _json_schema_block(_PROFESSOR_FIELDS)
    )
    data = _professor_chat_json(llm_router, user, provider=provider, model=model)
    review = _sanitize_professor_fields(data)
    if review is not None:
        markdown = _render_professor_markdown(review, kind="entry")
        if markdown:
            return _professor_payload(
                markdown, evidence, sources, review, kind="entry", question=question
            )
    # Fallback: the previous free-text grounded assessment — submitting never breaks.
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    prompt = (
        f"Ausgangsfrage: {question}\n\n"
        f"Variante \"{variant_name}\": {variant.get('approach') or ''}\n\n"
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


def _sanitize_professor_fields(data: dict[str, Any] | None) -> dict[str, Any] | None:
    """Validate/coerce the shared professor sections; None when nothing usable."""
    if not isinstance(data, dict):
        return None
    review = {
        "verstaendnis": str(data.get("verstaendnis") or "").strip(),
        "staerken": _str_list(data.get("staerken")),
        "probleme": _str_list(data.get("probleme")),
        "ideen": _str_list(data.get("ideen")),
        "naechste_schritte": _str_list(data.get("naechste_schritte")),
    }
    if not review["verstaendnis"] and not any(
        review[key] for key in ("staerken", "probleme", "ideen", "naechste_schritte")
    ):
        return None
    return review


_STAGE_VERDICTS = ("weiterverfolgen", "anpassen", "verwerfen")


def _sanitize_stage_verdicts(
    raw: Any, variants: list[dict[str, Any]]
) -> list[dict[str, str]]:
    """Coerce varianten_bewertung items; map unknown variant_ids by name."""
    by_id = {str(v.get("id")): str(v.get("name") or "Variante") for v in variants}
    by_name = {str(v.get("name") or "").strip().lower(): str(v.get("id")) for v in variants}
    out: list[dict[str, str]] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        vid = str(item.get("variant_id") or "").strip()
        name = str(item.get("name") or "").strip()
        if vid not in by_id:
            vid = by_name.get(name.lower(), "")
        if vid in by_id and not name:
            name = by_id[vid]
        urteil = str(item.get("urteil") or "").strip().lower()
        if urteil not in _STAGE_VERDICTS:
            urteil = "anpassen"
        if not vid and not name:
            continue
        out.append({
            "variant_id": vid,
            "name": name or "Variante",
            "urteil": urteil,
            "begruendung": str(item.get("begruendung") or "").strip(),
        })
    return out


def _variant_result_block(variant: dict[str, Any]) -> str:
    results = [
        str(entry.get("content") or "").strip()
        for entry in variant.get("entries", [])
        if entry.get("role") == "user" and str(entry.get("content") or "").strip()
    ]
    body = "\n".join(f"- {r}" for r in results) or "(keine Ergebnisse eingesendet)"
    return (
        f"Variante \"{variant.get('name') or 'Variante'}\" (id: {variant.get('id')}): "
        f"{variant.get('approach') or ''}\n"
        f"Eingesendete Ergebnisse:\n{body}"
    )


def professor_review_stage(
    retriever: HybridRetriever,
    llm_router: Any,
    *,
    question: str,
    stage: dict[str, Any],
    variants: list[dict[str, Any]],
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
) -> dict[str, Any]:
    """Structured professor review over one Etappe (all its variants + results),
    incl. a per-variant verdict (weiterverfolgen/anpassen/verwerfen). Degrades to a
    free-text grounded answer on unusable LLM JSON."""
    stage_name = str(stage.get("name") or "Etappe")
    stage_goal = str(stage.get("goal") or "").strip()
    blocks = "\n\n".join(_variant_result_block(v) for v in variants) or "(keine Varianten)"
    composite = f"{question}\n{stage_name} {stage_goal}"
    evidence, sources = _gather_evidence(retriever, composite, paper_ids=paper_ids, limit=10)
    block = _evidence_block(evidence) or "(keine lokale Evidenz gefunden)"
    user = (
        f"Forschungsvorhaben: {question}\n\n"
        f"Zu begutachtende Etappe: {stage_name}"
        + (f" — Ziel: {stage_goal}" if stage_goal else "")
        + "\n\n"
        f"Varianten dieser Etappe mit eingesendeten Ergebnissen:\n\n{blocks}\n\n"
        f"Lokale Evidenz (Paper-Auszüge):\n{block}\n\n"
        "Begutachte diese Etappe wie ein betreuender Professor: Ist das Etappenziel "
        "erreicht? Was ist belastbar, wo liegen Fehler/Probleme, welche Ideen und "
        "nächsten Schritte ergeben sich? Bewerte außerdem jede Variante. "
        + _json_schema_block(
            _PROFESSOR_FIELDS
            + ', "varianten_bewertung": [{"variant_id": "id aus der Aufgabenstellung", '
            '"name": "Variantenname", "urteil": "weiterverfolgen|anpassen|verwerfen", '
            '"begruendung": "1 Satz"}]'
        )
    )
    data = _professor_chat_json(llm_router, user, provider=provider, model=model)
    review = _sanitize_professor_fields(data)
    if review is not None:
        review["varianten_bewertung"] = _sanitize_stage_verdicts(
            (data or {}).get("varianten_bewertung"), variants
        )
        markdown = _render_professor_markdown(review, kind="stage")
        if markdown:
            return _professor_payload(
                markdown, evidence, sources, review, kind="stage", question=question
            )
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    prompt = (
        f"Forschungsvorhaben: {question}\n\n"
        f"Etappe \"{stage_name}\"" + (f" — Ziel: {stage_goal}" if stage_goal else "") + "\n\n"
        f"Varianten mit Ergebnissen:\n\n{blocks}\n\n"
        "Begutachte diese Etappe wie ein betreuender Professor (gegroundet auf die lokalen "
        "Paper): Zielerreichung, Stärken, Fehler/Probleme, Ideen, nächste Schritte. Zitiere "
        "Paper-IDs in eckigen Klammern."
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


def _stage_overview_block(stages: list[dict[str, Any]], variants: list[dict[str, Any]]) -> str:
    """Roadmap recap + per-stage variants/results + short stage-review excerpt."""
    by_stage: dict[str, list[dict[str, Any]]] = {}
    orphans: list[dict[str, Any]] = []
    stage_ids = {str(s.get("id")) for s in stages}
    for variant in variants:
        sid = str(variant.get("stage_id") or "")
        if sid in stage_ids:
            by_stage.setdefault(sid, []).append(variant)
        else:
            orphans.append(variant)
    parts: list[str] = []
    for idx, stage in enumerate(stages, start=1):
        goal = str(stage.get("goal") or "").strip()
        head = (
            f"Etappe {idx}: {stage.get('name') or 'Etappe'} "
            f"[Status: {stage.get('status') or 'offen'}]"
            + (f" — Ziel: {goal}" if goal else "")
        )
        body = "\n\n".join(
            _variant_result_block(v) for v in by_stage.get(str(stage.get("id")), [])
        ) or "(keine Varianten)"
        review_md = " ".join(str(stage.get("review_markdown") or "").split())[:300]
        review_line = f"\nEtappen-Review (Auszug): {review_md}" if review_md else ""
        parts.append(f"{head}\n{body}{review_line}")
    if orphans:
        body = "\n\n".join(_variant_result_block(v) for v in orphans)
        parts.append(f"Weitere Varianten (ohne Etappe):\n{body}")
    return "\n\n".join(parts)


def synthesize(
    retriever: HybridRetriever,
    llm_router: Any,
    *,
    question: str,
    variants: list[dict[str, Any]],
    stages: list[dict[str, Any]] | None = None,
    paper_ids: list[str] | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    metadata_db_path: str = "data/metadata.duckdb",
) -> dict[str, Any]:
    """End review over the whole Forschungsvorhaben: with ``stages`` a structured
    professor evaluation across all Etappen (kind "final"); otherwise (or when the
    LLM JSON is unusable) the classic free-text cross-variant ranking."""
    stages = stages or []
    if stages:
        joined = _stage_overview_block(stages, variants)
        evidence, sources = _gather_evidence(retriever, question, paper_ids=paper_ids, limit=10)
        block = _evidence_block(evidence) or "(keine lokale Evidenz gefunden)"
        user = (
            f"Forschungsvorhaben: {question}\n\n"
            f"Alle Etappen mit Varianten und Ergebnissen:\n\n{joined}\n\n"
            f"Lokale Evidenz (Paper-Auszüge):\n{block}\n\n"
            "Werte das gesamte Vorhaben wie ein betreuender Professor aus: über alle "
            "Etappen hinweg. "
            + _json_schema_block(
                '"gesamtverstaendnis": "3-5 Sätze: was wurde insgesamt erreicht und was bedeutet es", '
                '"etappen_zusammenfassung": [{"stage_id": "id", "name": "Etappenname", '
                '"fazit": "1-2 Sätze Fazit dieser Etappe"}], '
                '"staerken": ["was ist belastbar"], '
                '"probleme": ["Fehler, Schwächen, offene Risiken"], '
                '"ideen": ["weiterführende Ideen"], '
                '"offene_punkte": ["was noch offen/ungeklärt ist"], '
                '"finale_antwort": "die finale, neu gestaltete Antwort auf die Ausgangsfrage als Markdown"'
            )
        )
        data = _professor_chat_json(llm_router, user, provider=provider, model=model)
        if isinstance(data, dict):
            stage_names = {str(s.get("id")): str(s.get("name") or "Etappe") for s in stages}
            fazits = [
                {
                    "stage_id": str(item.get("stage_id") or "").strip(),
                    "name": str(item.get("name") or "").strip()
                    or stage_names.get(str(item.get("stage_id") or "").strip(), "Etappe"),
                    "fazit": str(item.get("fazit") or "").strip(),
                }
                for item in (data.get("etappen_zusammenfassung") or [])
                if isinstance(item, dict) and str(item.get("fazit") or "").strip()
            ]
            review = {
                "gesamtverstaendnis": str(data.get("gesamtverstaendnis") or "").strip(),
                "etappen_zusammenfassung": fazits,
                "staerken": _str_list(data.get("staerken")),
                "probleme": _str_list(data.get("probleme")),
                "ideen": _str_list(data.get("ideen")),
                "offene_punkte": _str_list(data.get("offene_punkte")),
                "finale_antwort": str(data.get("finale_antwort") or "").strip(),
            }
            if review["finale_antwort"] or review["gesamtverstaendnis"]:
                markdown = _render_professor_markdown(review, kind="final")
                if markdown:
                    return _professor_payload(
                        markdown, evidence, sources, review, kind="final", question=question
                    )
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
