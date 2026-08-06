"""Pure helper functions for grounded answering: evidence shaping, prompt
assembly, and citation parsing / repair. Split out of grounded_responder.py;
all functions are side-effect-free and import-cheap (no LLM/retriever deps).
"""

from __future__ import annotations

import re
from typing import Any

from query.kg_retriever import Evidence, SearchHit


_BOILERPLATE_RE = re.compile(
    r"^(copyright\b|©|\(c\)\s*\d{4}|all rights reserved|terms of use)",
    re.IGNORECASE,
)


def _is_boilerplate(text: str) -> bool:
    return bool(_BOILERPLATE_RE.match(text.strip()))


def _flatten_evidence(hits: list[SearchHit], max_items: int) -> list[Evidence]:
    evidence: list[Evidence] = []
    for hit in hits:
        evidence.extend(hit.evidence)
    evidence.sort(key=lambda item: item.score, reverse=True)
    return evidence[:max_items]


def _evidence_item_limit(limit: int, hits: list[SearchHit]) -> int:
    hit_count = max(1, len(hits))
    requested = max(1, int(limit))
    return min(80, max(24, requested * 4, hit_count * 5))


def _supplemental_evidence_from_extraction(
    paper_id: str,
    extraction: dict[str, Any],
) -> list[Evidence]:
    evidence: list[Evidence] = []
    for field_name, kind, base_score in [
        ("claims", "claim", 7.0),
        ("methods", "method", 5.0),
        ("concepts", "concept", 4.0),
        ("relations", "relation", 4.5),
    ]:
        for item in extraction.get(field_name) or []:
            text = _evidence_item_text(item)
            if not text:
                continue
            score = base_score + _evidence_specificity_bonus(text)
            evidence.append(
                Evidence(
                    paper_id=paper_id,
                    kind=kind,
                    field=field_name,
                    text=text,
                    score=score,
                    metadata=item if isinstance(item, dict) else {},
                )
            )
    return evidence


def _evidence_item_text(item: Any) -> str:
    if isinstance(item, dict):
        preferred = [
            "statement",
            "evidence_span",
            "label",
            "context",
            "description",
            "relation_type",
            "subject_id",
            "object_id",
        ]
        parts = [str(item.get(key) or "") for key in preferred]
        return " ".join(part for part in parts if part).strip()
    return str(item or "").strip()


def _evidence_specificity_bonus(text: str) -> float:
    bonus = 0.0
    if re.search(r"\d", text or ""):
        bonus += 2.0
    if re.search(
        r"\b(ai consult|clinical|clinician|physician|patient|diagnos|treatment)\b",
        text or "",
        re.I,
    ):
        bonus += 1.5
    return bonus


def _answer_evidence_rank(item: Evidence) -> float:
    kind_bonus = {
        "claim": 3.0,
        "relation": 2.0,
        "method": 1.0,
        "concept": 0.5,
        "paper": 0.25,
    }.get(item.kind, 0.0)
    return float(item.score) + kind_bonus + _evidence_specificity_bonus(item.text)


def _prioritize_hits(
    hits: list[SearchHit], priority_paper_ids: set[str]
) -> list[SearchHit]:
    """Stable-sort hits so evidence from the main source(s) ranks first."""
    if not priority_paper_ids:
        return hits
    return sorted(
        hits,
        key=lambda hit: (
            0 if str(getattr(hit.source, "paper_id", "")) in priority_paper_ids else 1
        ),
    )


_CRITICAL_MODE_INSTRUCTIONS = [
    "",
    "KRITISCHER MODUS — der Nutzer will eine bewusst skeptische Einordnung:",
    "Benenne zu jeder zentralen Aussage explizit Limitationen, Risiken/Nebenwirkungen, "
    "methodische Schwächen (Stichprobengröße, Setting, fehlende Replikation), widersprüchliche "
    "Befunde und offene Fragen — soweit die Belege dazu etwas enthalten.",
    "Stelle vielversprechend klingende Ergebnisse nicht als gesichert dar; kennzeichne die "
    "Beleglage (einzelne Studie vs. mehrere unabhängige Quellen, Preprint vs. begutachtet).",
    "Wenn die lokalen Belege keine kritische Gegenposition enthalten, sage das ausdrücklich — "
    "erfinde keine.",
    "Schließe mit einem kurzen Abschnitt 'Kritische Einordnung' (2-5 Sätze) mit den wichtigsten "
    "Vorbehalten und was zur Absicherung noch geprüft werden müsste.",
]


def _build_grounded_prompt(
    question: str,
    hits: list[SearchHit],
    evidence: list[Evidence],
    conversation_context: list[dict[str, Any]] | None = None,
    priority_paper_ids: set[str] | None = None,
    verbose: bool = False,
    critical: bool = False,
) -> str:
    source_titles = {
        hit.source.paper_id: hit.source.title or hit.source.paper_id for hit in hits
    }
    lines = [f"Question: {question}"]
    context_lines = _conversation_context_lines(conversation_context)
    if context_lines:
        lines.extend(["", "Previous conversation context:", *context_lines])
    present_priority = [
        pid for pid in (priority_paper_ids or set()) if pid in source_titles
    ]
    if present_priority:
        joined = ", ".join(f"[{pid}]" for pid in present_priority)
        lines.extend(
            [
                "",
                f"Primary source(s) / Hauptquelle: {joined}.",
                "Answer primarily from the primary source(s). Use the other evidence only to "
                "support, contradict, or deepen points the primary source does not fully explain, "
                "and make clear which role each supporting source plays.",
                "If the primary source does not cover a specific sentence, state that explicitly in "
                "that sentence (e.g. 'Die primäre Quelle [files] deckt X nicht ab; [arxiv:...] zeigt "
                "...') and cite the supporting source there.",
                "Do not announce the primary source in the opening sentence if you then cite other "
                "sources instead — either lead with the primary source's actual findings or omit the "
                "announcement.",
            ]
        )
    lines.extend(["", "Evidence:"])
    has_grey_evidence = False
    has_unverified_evidence = False
    for index, item in enumerate(evidence, start=1):
        title = source_titles.get(item.paper_id, item.paper_id)
        item_metadata = getattr(item, "metadata", None)
        is_grey = item.paper_id.startswith("grey::") or (
            isinstance(item_metadata, dict)
            and item_metadata.get("source_type") == "grey"
        )
        # Webquellen tragen ihre Vertrauensstufe (research/source_tiers.py) im Marker:
        # Behoerden/Hochschulen/Fachverlage vs. beliebige Seiten.
        trust_tier = ""
        if isinstance(item_metadata, dict):
            trust_tier = str(item_metadata.get("trust_tier") or "")
        marker = ""
        if is_grey:
            if trust_tier == "trusted":
                marker = "(Webquelle · vertrauenswürdig) "
            else:
                marker = "(Webquelle · ungeprüft) "
                has_unverified_evidence = True
        has_grey_evidence = has_grey_evidence or is_grey
        lines.append(
            f"{index}. [{item.paper_id}] {title} | {item.kind} | {marker}{_sanitize_evidence_text(item.text)}"
        )
    if has_grey_evidence:
        lines.append(
            "Evidence marked (Webquelle) comes from web sources, which are often more current than papers. "
            "Rank sources in this order: papers first, then (Webquelle · vertrauenswürdig) — official "
            "institutions, universities and established publishers — and only last "
            "(Webquelle · ungeprüft). When paper evidence supports a claim, cite the paper, and add a "
            "web-source ID after the paper ID in the same bracket (e.g. [p1, grey::abc]) only as "
            "supporting or updating context. Cite a web source alone only for information that no paper "
            "evidence covers, such as recent developments or current events."
        )
    if has_unverified_evidence:
        lines.append(
            "When a statement rests only on (Webquelle · ungeprüft) evidence, say so in that sentence "
            "(e.g. 'laut einer ungeprüften Webquelle') so the reader can weigh it."
        )
    answer_instruction = (
        "Gib eine ausführliche, gut strukturierte Antwort auf Basis der Belege. "
        "Erkläre alle relevanten Aspekte und gehe auf Zusammenhänge ein."
        if verbose
        else "Answer concisely using only this evidence."
    )
    lines.extend(
        [
            "",
            answer_instruction,
            "Include source paper IDs in square brackets for each substantive claim.",
            "Place each citation marker at the end of the sentence or claim it supports — never before the claim or in the middle of it.",
            "When multiple papers support different parts of the answer, cite multiple distinct paper IDs instead of reusing only one source.",
            "When one claim is supported by multiple papers, cite the supporting paper IDs together in one bracket, separated by commas, for example [p1, p2].",
            "Cite per sentence ONLY the paper IDs whose evidence actually states that specific fact. "
            "Never copy the citation list of a neighboring sentence: two adjacent sentences usually "
            "have different supporting sources. Before adding a second ID to a bracket, check that "
            "this second paper's evidence really contains the same statement — if you are unsure, "
            "cite only the single best-supporting paper for that sentence.",
            "However, when a sentence combines findings that no single source fully covers, cite "
            "every source whose evidence is required, together in one bracket, so the citations "
            "fully support the sentence on their own.",
            "Use only paper IDs shown in the evidence as citations; never cite evidence item numbers like [1] or [4].",
            "Each evidence line above starts with its item number. Inside every citation bracket, "
            "append '#' plus the number of the ONE evidence item that contains that fact, directly "
            "after the paper ID, e.g. [arxiv:2301.12345#3] or [p1#2, p2#7]. The '#number' suffix is "
            "removed before the answer is shown; still never cite a bare number like [3] on its own. "
            "If no single evidence item clearly contains the fact, use the plain paper ID without '#'.",
            "Do not copy reference or citation numbers from the source text (superscripts like [17-22] or [26, 29]); cite only the paper IDs shown above.",
            "When quantitative findings or metrics are present, include the most important numbers.",
            "When evidence contains both positive findings and important caveats — such as side "
            "effects, adverse events, limitations, subgroup-specific results, or contradicting "
            "outcomes — include both. If a finding applies only to a specific subgroup (e.g., by "
            "age, treatment arm, or patient category), state the qualifier explicitly.",
            "Do not contradict yourself within one answer. If an earlier sentence states a "
            "benefit only for a specific endpoint (e.g. PFS but not OS), a later sentence must "
            "not claim the same treatment improves the excluded endpoint (e.g. 'patients live "
            "longer') unless a cited source actually shows it. Always name BOTH directions "
            "explicitly: when you report a positive result, also state any negative/null result "
            "the same source reports, and vice versa.",
        ]
    )
    if _needs_clinical_model_role_instruction(question, evidence):
        lines.append(
            "Distinguish deployed clinical systems from models used only for evaluation, rating, or robustness checks."
        )
    if critical:
        lines.extend(_CRITICAL_MODE_INSTRUCTIONS)
    return "\n".join(lines)


def _conversation_context_lines(
    conversation_context: list[dict[str, Any]] | None
) -> list[str]:
    lines: list[str] = []
    for item in (conversation_context or [])[-6:]:
        if not isinstance(item, dict):
            continue
        role = "Assistant" if item.get("role") == "assistant" else "User"
        content = re.sub(r"\s+", " ", str(item.get("content") or "")).strip()
        if content:
            lines.append(f"- {role}: {content[:900]}")
    return lines


def _build_pdf_context_prompt(
    question: str,
    combined_text: str,
    conversation_context: list[dict[str, Any]] | None = None,
    critical: bool = False,
) -> str:
    lines = [f"Question: {question}"]
    context_lines = _conversation_context_lines(conversation_context)
    if context_lines:
        lines.extend(["", "Previous conversation context:", *context_lines])
    lines.extend(
        [
            "",
            "Whole parsed PDF context:",
            combined_text,
            "",
            "Answer using only the PDF context above.",
            "Cite the exact paper IDs shown in square brackets for every substantive claim.",
            "Directly after each citation bracket, append the supporting passage copied verbatim "
            "(character-for-character, in the PDF's own language) from the PDF context, wrapped in "
            "double curly braces, e.g. [p1]{{exact passage from the PDF}}. Never paraphrase, "
            "translate, or shorten the text inside the braces; it is removed before the answer is shown.",
            "When one claim combines information from several separate passages, append each passage "
            "in its own double-brace block directly after the same citation, e.g. "
            "[p1]{{first passage}}{{second passage}} — do not merge distant passages into one block.",
            "Place each citation marker at the end of the sentence or claim it supports — never before the claim or in the middle of it.",
            "When multiple papers support one claim, cite them together, for example [p1, p2].",
            "Cite per sentence ONLY the papers whose text actually contains that specific fact; never "
            "copy the citation list of a neighboring sentence. If only one paper supports a sentence, "
            "cite exactly that one paper.",
            "Do not copy reference or citation numbers from the source text (superscripts like [17-22] or [26, 29]); cite only the paper IDs shown above.",
            "If the PDF context is insufficient — fully or for part of the question — say that the "
            "local PDF context does not contain enough evidence for the missing part AND append the "
            "exact token [NO_LOCAL_EVIDENCE] at the very end of your answer (it is removed before display).",
            "When the PDF contains both a positive result and an important caveat — side effects, "
            "adverse events, subgroup qualifier, or contradicting result — report both. Do not omit the caveat.",
            "Do not contradict yourself within one answer. If an earlier sentence states a "
            "benefit only for a specific endpoint (e.g. PFS but not OS), a later sentence must "
            "not claim the same treatment improves the excluded endpoint (e.g. 'patients live "
            "longer') unless a cited source actually shows it. Always name BOTH directions "
            "explicitly: when you report a positive result, also state any negative/null result "
            "the same source reports, and vice versa.",
        ]
    )
    if critical:
        lines.extend(_CRITICAL_MODE_INSTRUCTIONS)
    return "\n".join(lines)


def _best_pdf_context_snippet(
    pdf_text: str, question: str, max_chars: int = 900
) -> str:
    clean = re.sub(r"\s+", " ", str(pdf_text or "")).strip()
    if len(clean) <= max_chars:
        return clean
    terms = _match_terms(question)
    lower = clean.lower()
    best_pos = -1
    for term in terms:
        best_pos = lower.find(term.lower())
        if best_pos >= 0:
            break
    if best_pos < 0:
        return clean[: max_chars - 3].rstrip() + "..."
    start = max(0, best_pos - max_chars // 3)
    end = min(len(clean), start + max_chars)
    prefix = "..." if start > 0 else ""
    suffix = "..." if end < len(clean) else ""
    return prefix + clean[start:end].strip() + suffix


def _extractive_answer(
    question: str,
    hits: list[SearchHit],
    evidence: list[Evidence],
) -> str:
    source_titles = {
        hit.source.paper_id: hit.source.title or hit.source.paper_id for hit in hits
    }
    lines = [f"Local KG evidence for '{question}':"]
    for item in evidence[:5]:
        title = source_titles.get(item.paper_id, item.paper_id)
        lines.append(
            f"- [{item.paper_id}] {title}: {_sanitize_evidence_text(item.text)}"
        )
    return "\n".join(lines)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _sanitize_evidence_text(text: str) -> str:
    return re.sub(
        r"\[([^\]]+)\]",
        lambda match: (
            match.group(0)
            if _is_allowed_citation_label(match.group(1).strip())
            else f"({match.group(1).strip()})"
        ),
        str(text or ""),
    )


def _cited_paper_ids(
    answer_text: str, known_ids: frozenset[str] = frozenset()
) -> set[str]:
    ids: set[str] = set()
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        for value in re.split(r"[,;]\s*", bracketed):
            value = value.strip()
            if _is_allowed_citation_label(value, known_ids):
                ids.add(value)
    return ids


def _citation_occurrences(answer_text: str) -> list[tuple[re.Match[str], str, str]]:
    occurrences: list[tuple[re.Match[str], str, str]] = []
    for match in re.finditer(r"\[([^\]]+)\]", answer_text or ""):
        raw_citation = match.group(1).strip()
        context = _citation_context(answer_text, match.start(), match.end())
        occurrences.append((match, raw_citation, context))
    return occurrences


def _unique_citation_contexts(
    answer_text: str, known_ids: frozenset[str] = frozenset()
) -> list[tuple[str, str]]:
    seen: set[tuple[str, str]] = set()
    pairs: list[tuple[str, str]] = []
    for _match, raw_citation, context in _citation_occurrences(answer_text):
        for paper_id_value in _citation_paper_ids(raw_citation, known_ids):
            pair = (paper_id_value, context)
            if pair not in seen:
                seen.add(pair)
                pairs.append(pair)
    return pairs


def _citation_contexts_by_paper(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    grouped: dict[str, list[str]] = {}
    for paper_id_value, context in pairs:
        contexts = grouped.setdefault(paper_id_value, [])
        if context not in contexts:
            contexts.append(context)
    return grouped


def _parse_numbered_translations(response: str, originals: list[str]) -> dict[str, str]:
    """Parse a `<number>: <rewrite>` per-line response back onto the original strings by index."""
    translations: dict[str, str] = {}
    for line in str(response or "").splitlines():
        match = re.match(r"\s*(\d+)\s*[.:)]\s*(.+?)\s*$", line)
        if not match:
            continue
        index = int(match.group(1)) - 1
        if not (0 <= index < len(originals)):
            continue
        rewrite = match.group(2).strip().strip('"').strip()
        if rewrite:
            translations[originals[index]] = rewrite
    return translations


def _citation_links_for_answer(
    answer_text: str,
    evidence: list[Evidence],
    model_bindings: dict[tuple[str, str], list[str]] | None = None,
) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    known_ids = frozenset(item.paper_id for item in evidence if item.paper_id)
    used_by_paper: dict[str, set[str]] = {}
    for match, raw_citation, context in _citation_occurrences(answer_text):
        for paper_id_value in _citation_paper_ids(raw_citation, known_ids):
            used = used_by_paper.setdefault(paper_id_value, set())
            # A claim whose facts were located in several PDF passages gets one link per
            # located fragment, so the UI can offer every Belegstelle — not only the best.
            exact_items = (
                [
                    (index, item)
                    for index, item in enumerate(evidence)
                    if _same_paper_id(item.paper_id, paper_id_value)
                    and context in _evidence_claim_contexts(item)
                ]
                if context
                else []
            )
            if exact_items:
                for exact_index, exact_evidence in exact_items:
                    used.add(exact_evidence.evidence_id)
                    links.append(
                        {
                            "citation": raw_citation,
                            "citation_start": match.start(),
                            "citation_end": match.end(),
                            "paper_id": paper_id_value,
                            "evidence_id": exact_evidence.evidence_id,
                            "evidence_index": exact_index,
                            "score": round(_CONTEXT_MATCH_SCORE, 4),
                            "context": context,
                            "confidence": "high",
                        }
                    )
                continue
            # The model itself declared which evidence item(s) this citation draws on
            # ([pid#N] in the raw answer) — bind deterministically instead of re-guessing
            # by lexical overlap.
            bound_ids = (model_bindings or {}).get((paper_id_value, context)) or []
            bound_items = [
                (index, item)
                for index, item in enumerate(evidence)
                if item.evidence_id in bound_ids
                and _same_paper_id(item.paper_id, paper_id_value)
            ]
            if bound_items:
                for bound_index, bound_evidence in bound_items:
                    used.add(bound_evidence.evidence_id)
                    meaningful = _has_meaningful_overlap(context, bound_evidence)
                    # A model binding with zero content overlap is still honestly
                    # flagged low/approximate — models can misnumber items too.
                    confidence = "high" if meaningful else "low"
                    link = {
                        "citation": raw_citation,
                        "citation_start": match.start(),
                        "citation_end": match.end(),
                        "paper_id": paper_id_value,
                        "evidence_id": bound_evidence.evidence_id,
                        "evidence_index": bound_index,
                        "score": round(_CONTEXT_MATCH_SCORE, 4),
                        "context": context,
                        "binding": "model",
                        "confidence": confidence,
                    }
                    if confidence == "low":
                        link["approximate"] = True
                    links.append(link)
                continue
            best_index, best_evidence, score = _best_citation_evidence(
                paper_id_value, context, evidence, used
            )
            if best_evidence is None:
                continue
            used.add(best_evidence.evidence_id)
            link: dict[str, Any] = {
                "citation": raw_citation,
                "citation_start": match.start(),
                "citation_end": match.end(),
                "paper_id": paper_id_value,
                "evidence_id": best_evidence.evidence_id,
                "evidence_index": best_index,
                "score": round(score, 4),
                "context": context,
            }
            link_metadata = (
                best_evidence.metadata
                if isinstance(best_evidence.metadata, dict)
                else {}
            )
            meaningful_overlap = _has_meaningful_overlap(context, best_evidence)
            confidence = _confidence_for_score(
                score, meaningful_overlap, link_metadata.get("context_policy")
            )
            link["confidence"] = confidence
            if confidence == "low":
                # Honest flag: this citation could not be tied to a passage that shares
                # concrete content with its sentence — the UI shows it as approximate
                # instead of presenting a confident-looking but unrelated excerpt.
                link["approximate"] = True
            links.append(link)
    return links


def _confidence_for_score(
    score: float, meaningful_overlap: bool, context_policy: str | None = None
) -> str:
    """Map a citation-evidence score to a three-level confidence label.

    - high: exact context match (score == _CONTEXT_MATCH_SCORE), or a strong lexical
      match (score >= 15) with meaningful content overlap.
    - medium: a usable lexical match (score >= 5) with meaningful overlap.
    - low: everything else (weak/no overlap) — the UI shows this as approximate.
    """
    if score >= _CONTEXT_MATCH_SCORE:
        return "high"
    if meaningful_overlap and score >= 15.0:
        return "high"
    if meaningful_overlap and score >= 5.0:
        return "medium"
    return "low"


def _citation_paper_ids(
    citation: str, known_ids: frozenset[str] = frozenset()
) -> list[str]:
    ids: list[str] = []
    for value in re.split(r"[,;]\s*|\s+(?:and|und)\s+", citation or ""):
        value = value.strip()
        if value and _is_allowed_citation_label(value, known_ids):
            ids.append(value)
    return ids


def _citation_context(answer_text: str, start: int, end: int, window: int = 500) -> str:
    before = str(answer_text or "")[:start]
    after = str(answer_text or "")[end:]
    left_boundary = max(
        before.rfind(". "), before.rfind("! "), before.rfind("? "), before.rfind("\n\n")
    )
    right_candidates = [
        idx
        for idx in [
            after.find(". "),
            after.find("! "),
            after.find("? "),
            after.find("\n\n"),
        ]
        if idx >= 0
    ]
    left = left_boundary + 2 if left_boundary >= 0 else max(0, start - window // 2)
    right = end + (
        min(right_candidates) + 1 if right_candidates else min(len(after), window // 2)
    )
    context = str(answer_text or "")[left:right]
    context = re.sub(r"\[[^\]]+\]", " ", context)
    return re.sub(r"\s+", " ", context).strip()


# Score returned when a citation is matched to the exact evidence located for its sentence.
_CONTEXT_MATCH_SCORE = 1000.0


def _best_citation_evidence(
    paper_id_value: str,
    context: str,
    evidence: list[Evidence],
    used_evidence_ids: set[str] | None = None,
) -> tuple[int, Evidence | None, float]:
    candidates = [
        (index, item)
        for index, item in enumerate(evidence)
        if _same_paper_id(item.paper_id, paper_id_value)
    ]
    if not candidates:
        return -1, None, 0.0

    used = used_evidence_ids or set()
    # Prefer the evidence located for exactly this citation context. The PDF-context path
    # stores the originating sentence(s) in metadata["context"]/["contexts"], so we can
    # reconnect each citation to its own excerpt instead of re-guessing it by lexical overlap.
    if context:
        exact = [
            (index, item)
            for index, item in candidates
            if context in _evidence_claim_contexts(item)
        ]
        if exact:
            unused_exact = [row for row in exact if row[1].evidence_id not in used]
            index, item = (unused_exact or exact)[0]
            return index, item, _CONTEXT_MATCH_SCORE

    # A claim excerpt/region located for a DIFFERENT sentence must not win the lexical
    # fallback — that is how an unmatched citation ends up displaying another sentence's
    # passage. Prefer the whole-pdf fallback (or any non-claim evidence) instead.
    non_foreign = [
        (index, item)
        for index, item in candidates
        if not (
            isinstance(item.metadata, dict)
            and item.metadata.get("context_policy")
            in ("claim_excerpt", "approx_region")
            and _evidence_claim_contexts(item)
            and context not in _evidence_claim_contexts(item)
        )
    ]
    if non_foreign:
        candidates = non_foreign

    scored = [
        (index, item, _citation_evidence_score(context, item))
        for index, item in candidates
    ]
    scored.sort(key=lambda row: (row[2], row[1].score, -row[0]), reverse=True)
    top_score = scored[0][2]
    tied = [row for row in scored if row[2] == top_score]
    unused_tied = [row for row in tied if row[1].evidence_id not in used]
    index, item, score = (unused_tied or tied)[0]
    return index, item, score


def _citation_evidence_score(context: str, evidence: Evidence) -> float:
    target = _evidence_match_text(evidence)
    context_norm = _match_normalize(context)
    target_norm = _match_normalize(target)
    if not context_norm or not target_norm:
        return 0.0

    # Heavy penalty for copyright/boilerplate text so it never wins a citation slot.
    if _is_boilerplate(target):
        return -50.0

    score = 0.0
    context_terms = _match_terms(context)
    target_terms = set(_match_terms(target))
    if context_terms:
        score += sum(1.0 for term in context_terms if term in target_terms)

    quantitative = _quantitative_tokens(context)
    if quantitative:
        matched_numbers = sum(
            1 for token in quantitative if token in _quantitative_tokens(target)
        )
        score += matched_numbers * 6.0
        if matched_numbers == 0:
            score -= 6.0

    if evidence.kind == "claim":
        score += 8.0
    elif evidence.kind == "relation":
        score += 3.0
    elif evidence.kind == "paper":
        score -= 5.0

    if context_norm and context_norm in target_norm:
        score += 20.0
    for phrase in _distinctive_phrases(context_norm):
        if phrase in target_norm:
            score += 8.0
    return score


def _has_meaningful_overlap(context: str, evidence: Evidence) -> bool:
    """True when the cited sentence shares concrete content with the evidence text.

    Used for the honest `approximate` flag: a claim-kind bonus alone can rank an
    evidence item first without a single shared term, which is how one passage ended
    up displayed for two or three unrelated citations.
    """
    target = _evidence_match_text(evidence)
    context_norm = _match_normalize(context)
    target_norm = _match_normalize(target)
    if not context_norm or not target_norm:
        return False
    if context_norm in target_norm or target_norm in context_norm:
        return True
    quantitative = _quantitative_tokens(context)
    if quantitative and any(
        token in _quantitative_tokens(target) for token in quantitative
    ):
        return True
    target_terms = set(_match_terms(target))
    shared = sum(1 for term in _match_terms(context) if term in target_terms)
    return shared >= 2


def _add_distinct_excerpt(excerpts: list[str], excerpt: str) -> None:
    """Append an excerpt unless it duplicates (or is contained in) one already kept."""
    new_norm = re.sub(r"\s+", " ", excerpt or "").strip().lower()
    if not new_norm:
        return
    for index, existing in enumerate(excerpts):
        existing_norm = re.sub(r"\s+", " ", existing).strip().lower()
        if new_norm in existing_norm:
            return
        if existing_norm in new_norm:
            excerpts[index] = excerpt
            return
    excerpts.append(excerpt)


def _evidence_claim_contexts(item: Evidence) -> list[str]:
    """All answer contexts this evidence was located for (primary + deduped extras)."""
    metadata = item.metadata if isinstance(item.metadata, dict) else {}
    values: list[str] = []
    primary = metadata.get("context")
    if primary:
        values.append(primary)
    for extra in metadata.get("contexts") or []:
        if extra and extra not in values:
            values.append(extra)
    return values


def _evidence_match_text(evidence: Evidence) -> str:
    metadata = evidence.metadata if isinstance(evidence.metadata, dict) else {}
    parts = [
        evidence.text,
        str(metadata.get("statement") or ""),
        str(metadata.get("evidence_span") or ""),
        str(metadata.get("context") or ""),
        str(metadata.get("description") or ""),
        str(metadata.get("label") or ""),
    ]
    return " ".join(part for part in parts if part)


def _same_paper_id(left: str, right: str) -> bool:
    left_norm = _normalize_citation_id(left)
    right_norm = _normalize_citation_id(right)
    return (
        left_norm == right_norm
        or left_norm.endswith(right_norm)
        or right_norm.endswith(left_norm)
    )


def _normalize_citation_id(value: str) -> str:
    normalized = re.sub(
        r"^https?://arxiv\.org/abs/", "arxiv:", str(value or "").lower()
    )
    return re.sub(r"\s+", "", normalized)


def _match_normalize(value: str) -> str:
    return re.sub(
        r"\s+", " ", re.sub(r"[^\w.%+-]+", " ", str(value or "").lower())
    ).strip()


def _match_terms(value: str) -> list[str]:
    stopwords = {
        "about",
        "also",
        "and",
        "are",
        "based",
        "from",
        "have",
        "into",
        "paper",
        "that",
        "the",
        "their",
        "this",
        "used",
        "with",
    }
    terms = re.findall(r"[a-z0-9][a-z0-9-]{3,}", _match_normalize(value))
    return [term for term in terms if term not in stopwords]


def _quantitative_tokens(value: str) -> set[str]:
    tokens = set()
    normalized = re.sub(r"(\d+),(\d+)", r"\1.\2", str(value or ""))
    for match in re.findall(r"\d+(?:\.\d+)?\s*%?", normalized):
        clean = re.sub(r"\s+", "", match)
        if clean:
            tokens.add(clean)
            tokens.add(clean.rstrip("%"))
    return tokens


def _distinctive_phrases(value: str) -> list[str]:
    words = [word for word in value.split() if len(word) >= 4]
    phrases: list[str] = []
    for size in (5, 4, 3):
        for index in range(0, max(len(words) - size + 1, 0)):
            phrase = " ".join(words[index : index + size])
            if any(char.isdigit() for char in phrase) or len(phrase) >= 28:
                phrases.append(phrase)
    return phrases[:12]


def _invalid_citations(
    answer_text: str, known_ids: frozenset[str] = frozenset()
) -> list[str]:
    invalid: list[str] = []
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        parts = [
            part.strip() for part in re.split(r"[,;]\s*", bracketed) if part.strip()
        ]
        if not parts:
            continue
        for part in parts:
            if part in known_ids:
                continue
            if re.fullmatch(r"\d+", part):
                invalid.append(part)
            elif re.fullmatch(r"\d+(?:\s*[-,]\s*\d+)+", part):
                invalid.append(part)
            elif not _is_allowed_citation_label(part, known_ids):
                invalid.append(part)
    return invalid


def _is_allowed_citation_label(
    value: str, known_ids: frozenset[str] = frozenset()
) -> bool:
    # An exact match against a paper ID actually present in this answer's context is
    # always valid, regardless of format (local uploads can have bare IDs like "files").
    if value in known_ids:
        return True
    # Grey source IDs are UUIDs assigned by the DB — require exact match, never allow
    # hallucinated short forms like grey::grey_29 through the namespace regex below.
    if value.startswith("grey::"):
        return False
    # Accept any source:id format (e.g. arxiv:, crossref:, openalex:, semantic_scholar:, files:, local:, doi:)
    # and legacy short p-prefixed IDs. Bare numbers or random tokens are rejected.
    return bool(re.match(r"^[a-z][a-z0-9_]*:[^\s]", value)) or value.startswith("p")


# Sentence matcher that treats bracketed citations as atomic, so paper IDs containing
# dots (e.g. [arxiv:2507.16947]) do not split a sentence in the middle of the marker.
_SENTENCE_RE = re.compile(r"(?:\[[^\]\n]*\]|[^.!?\n])+[.!?]*")

# A citation bracket — `[...]` not crossing newlines. Kept narrow so non-citation
# brackets (e.g. `[NO_LOCAL_EVIDENCE]`) are also matched; the relocation function
# below only moves brackets that resolve to a known paper id.
_CITATION_BRACKET_RE = re.compile(r"\[[^\]\n]*\]")


def _relocate_mid_sentence_citations(
    answer_text: str, known_ids: frozenset[str] = frozenset()
) -> str:
    """Move citation brackets that sit in the middle of a sentence to its end.

    The grounded prompt asks the model to place every citation marker at the end of
    the claim it supports, but smaller models occasionally emit a bracket mid-sentence
    (e.g. ``Die Studie [arxiv:2507.16947] zeigte, dass ...``). Downstream sentence
    splitting and evidence-binding extraction then attribute the citation to the wrong
    claim, and the rendered Z-chip lands awkwardly in the middle of a clause.

    For each sentence, citation brackets that are NOT part of the trailing cluster
    (the run of citations sitting right before the sentence-ending punctuation) are
    moved to the end, preserving their left-to-right order. Non-citation brackets and
    brackets that already sit at the sentence end are left in place. Paragraph breaks
    are respected: a citation never jumps across a blank line (``_SENTENCE_RE`` stops
    at newlines).
    """
    text = str(answer_text or "")
    if not text or "[" not in text:
        return text

    def _is_known_citation(bracket_inner: str) -> bool:
        return bool(_citation_paper_ids(bracket_inner, known_ids))

    def _replace_sentence(match: re.Match[str]) -> str:
        sentence = match.group(0)
        brackets = list(_CITATION_BRACKET_RE.finditer(sentence))
        if not brackets:
            return sentence
        citation_spans = [
            (m.start(), m.end(), m.group(0))
            for m in brackets
            if _is_known_citation(m.group(0)[1:-1])
        ]
        if not citation_spans:
            return sentence
        # Trailing cluster: contiguous known citations ending at the sentence's
        # substantive end (right before the trailing punctuation/whitespace).
        stripped = sentence.rstrip()
        body = stripped.rstrip(".!?")
        body_end = len(body)
        cursor = body_end
        already_at_end: list[tuple[int, int, str]] = []
        for start, end, bracket in reversed(citation_spans):
            if end == cursor:
                already_at_end.append((start, end, bracket))
                cursor = start
            else:
                break
        already_at_end.reverse()
        to_move = [s for s in citation_spans if s not in already_at_end]
        if not to_move:
            return sentence
        # Rebuild the sentence with ALL known citations removed from their original
        # positions (mid + already-at-end), then re-append them all before the
        # trailing punctuation in their original left-to-right order. This keeps a
        # moved mid-sentence citation from landing after an already-placed trailing
        # citation, and preserves the codebase convention `{body} [{id}]{punct}`.
        all_citation_starts = {s for s, _, _ in citation_spans}
        rebuilt_chars: list[str] = []
        i = 0
        n = len(sentence)
        while i < n:
            if i in all_citation_starts:
                if rebuilt_chars and rebuilt_chars[-1] == " ":
                    rebuilt_chars.pop()
                for s, e, _ in citation_spans:
                    if s == i:
                        i = e
                        break
            else:
                rebuilt_chars.append(sentence[i])
                i += 1
        rebuilt = "".join(rebuilt_chars)
        rb_stripped = rebuilt.rstrip()
        rb_trailing = rebuilt[len(rb_stripped) :]
        rb_body = rb_stripped.rstrip(".!?")
        rb_punct = rb_stripped[len(rb_body) :]
        prefix = "" if not rb_body or rb_body.endswith(" ") else " "
        all_join = " ".join(b for _, _, b in citation_spans)
        return f"{rb_body}{prefix}{all_join}{rb_punct}{rb_trailing}"

    return _SENTENCE_RE.sub(_replace_sentence, text)


# `[paper_id]{{verbatim supporting passage}}` blocks emitted by the PDF-context prompt.
_MODEL_QUOTE_RE = re.compile(r"[ \t]*\{\{(.*?)\}\}", re.DOTALL)


def _extract_model_quotes(answer_text: str) -> tuple[str, dict[int, list[str]]]:
    """Strip `{{...}}` quote blocks from the answer and collect their quotes.

    The PDF-context prompt asks the model to append, after each citation bracket, the
    exact passage(s) it used — copied verbatim from the PDF. That removes the guesswork
    of re-locating a paraphrase: we verify each quote character-for-character and only
    fall back to fuzzy anchoring when the model misquoted. A claim synthesized from
    several passages ships several consecutive blocks ([p1]{{a}}{{b}}); all of them are
    collected under the same citation. Returns the cleaned answer plus quote lists keyed
    by the end offset of the citation bracket directly before each block (matching
    `_citation_occurrences`' match.end() on the cleaned text).
    """
    text = str(answer_text or "")
    if "{{" not in text:
        return text, {}
    quotes: dict[int, list[str]] = {}
    cleaned_parts: list[str] = []
    last = 0
    for match in _MODEL_QUOTE_RE.finditer(text):
        cleaned_parts.append(text[last : match.start()])
        quote = re.sub(r"\s+", " ", match.group(1)).strip().strip("\"„“«»'").strip()
        before = "".join(cleaned_parts).rstrip()
        if quote and before.endswith("]"):
            bucket = quotes.setdefault(len(before), [])
            if quote not in bucket:
                bucket.append(quote)
        last = match.end()
    cleaned_parts.append(text[last:])
    return "".join(cleaned_parts), quotes


def _normalize_citation_brackets(answer_text: str) -> str:
    """Normalize model-specific citation bracket variants to ASCII `[...]`.

    Qwen-family models (especially via LM Studio) often emit fullwidth CJK brackets
    (【p1】) or lenticular variants around citations; downstream parsing only knows
    ASCII brackets, so those citations rendered as broken text instead of Z-chips.
    """
    text = str(answer_text or "")
    if not text:
        return text
    return (
        text.replace("【", "[")
        .replace("】", "]")
        .replace("〔", "[")
        .replace("〕", "]")
        .replace("［", "[")
        .replace("］", "]")
    )


def _map_numeric_citations(
    answer_text: str,
    evidence: list[Evidence],
    known_ids: frozenset[str] = frozenset(),
) -> str:
    """Deterministically resolve numeric citations against the numbered evidence list.

    The grounded prompt enumerates evidence as `1. [paper_id] ...`, so a bare `[3]` in the
    response refers to evidence item 3 — map it to that item's paper ID without an LLM
    round trip. Ranges (e.g. [17-22]) and out-of-range numbers are left untouched for the
    LLM repair / strip stages. Only used in the KG path; in the PDF-context path numeric
    brackets are bibliography numbers copied from the paper text and must be stripped.
    """
    text = str(answer_text or "")
    if not text or not evidence:
        return text

    # Pre-pass: some smaller cloud models (Deepseek-v4-flash via Ollama) copy the
    # frontend's Z-chip label into PROSE rather than into a citation bracket —
    # "Basierend auf Z1 und unterstützender Evidenz". The bracket pass below cannot
    # reach those, so first rewrite in-range Z<N> (and z<N>) standing OUTSIDE any
    # bracket into a proper citation. Word boundaries protect real words; labels
    # outside the evidence range are left for the strip/repair stages to handle.
    bracket_spans = [m.span() for m in re.finditer(r"\[[^\]]*\]", text)]

    def _in_bracket(off: int) -> bool:
        return any(s <= off < e for s, e in bracket_spans)

    def _prose_z(match: re.Match[str]) -> str:
        if _in_bracket(match.start()):
            return match.group(0)
        index = int(match.group(2))
        if not (1 <= index <= len(evidence) and evidence[index - 1].paper_id):
            return match.group(0)
        pid = evidence[index - 1].paper_id
        return f"[{pid}#{index}]"

    text = re.sub(r"\b([Zz])\s*(\d+)\b", _prose_z, text)

    def _replace(match: re.Match[str]) -> str:
        parts = [
            part.strip()
            for part in re.split(r"[,;]\s*", match.group(1))
            if part.strip()
        ]
        mapped: list[str] = []
        changed = False
        for part in parts:
            # Bare numbers refer to the numbered evidence list; some models (Qwen via
            # LM Studio) also copy the UI's Z-labels and emit [Z1] — map both forms.
            numeric = re.fullmatch(r"(?:[Zz]\s*)?(\d+)", part)
            if not _is_allowed_citation_label(part, known_ids) and numeric:
                index = int(numeric.group(1))
                if 1 <= index <= len(evidence) and evidence[index - 1].paper_id:
                    # Keep the item number as a `#N` suffix: a bare [3] IS a statement
                    # about which evidence item was used, so it feeds the same binding
                    # extraction as model-declared [pid#N] (stripped before display).
                    mapped.append(f"{evidence[index - 1].paper_id}#{index}")
                    changed = True
                    continue
            mapped.append(part)
        if not changed:
            return match.group(0)
        return f"[{', '.join(dict.fromkeys(mapped))}]"

    return re.sub(r"\[([^\]]+)\]", _replace, text)


_EVIDENCE_BINDING_RE = re.compile(r"^(?P<pid>.+?)\s*#\s*(?P<num>\d{1,3})$")


def _extract_evidence_bindings(
    answer_text: str,
    evidence: list[Evidence],
    known_ids: frozenset[str] = frozenset(),
) -> tuple[str, dict[tuple[str, str], list[str]]]:
    """Strip `#N` evidence-item suffixes from citation brackets and collect the bindings.

    The grounded prompt asks the model to declare, per citation, WHICH numbered evidence
    item it drew the fact from ([arxiv:x#3]) — the KG-path counterpart of the PDF path's
    verbatim `{{quote}}` blocks. Returns the cleaned answer (no `#N` ever reaches the
    display) plus {(paper_id, citation_context): [evidence_id, ...]} for deterministic
    citation→evidence linking. Suffixes whose index is out of range or points at a
    different paper are stripped but produce no binding, so downstream falls back to
    lexical matching exactly as before.
    """
    text = str(answer_text or "")
    if not text or "#" not in text or not evidence:
        return text, {}
    bracket_bindings: list[dict[str, list[str]]] = []

    def _replace(match: re.Match[str]) -> str:
        parts = [
            part.strip()
            for part in re.split(r"[,;]\s*", match.group(1))
            if part.strip()
        ]
        bindings: dict[str, list[str]] = {}
        cleaned: list[str] = []
        changed = False
        for part in parts:
            suffixed = _EVIDENCE_BINDING_RE.fullmatch(part)
            # A real paper ID that happens to end in `#digits` must never be split.
            if not suffixed or part in known_ids:
                cleaned.append(part)
                continue
            pid = suffixed.group("pid").strip()
            changed = True
            cleaned.append(pid)
            index = int(suffixed.group("num"))
            if (
                _is_allowed_citation_label(pid, known_ids)
                and 1 <= index <= len(evidence)
                and evidence[index - 1].evidence_id
                and _same_paper_id(evidence[index - 1].paper_id, pid)
            ):
                bucket = bindings.setdefault(pid, [])
                if evidence[index - 1].evidence_id not in bucket:
                    bucket.append(evidence[index - 1].evidence_id)
        bracket_bindings.append(bindings)
        if not changed:
            return match.group(0)
        return f"[{', '.join(dict.fromkeys(cleaned))}]"

    cleaned_text = re.sub(r"\[([^\]]+)\]", _replace, text)
    result: dict[tuple[str, str], list[str]] = {}
    # Re-scan the cleaned text: bracket count and order are preserved by _replace, and the
    # contexts must be computed on the final text — the same one _citation_links_for_answer
    # will see — or the (paper_id, context) keys would never match.
    for occurrence, bindings in zip(
        _citation_occurrences(cleaned_text), bracket_bindings
    ):
        if not bindings:
            continue
        _match, _raw, context = occurrence
        for pid, evidence_ids in bindings.items():
            bucket = result.setdefault((pid, context), [])
            for evidence_id in evidence_ids:
                if evidence_id not in bucket:
                    bucket.append(evidence_id)
    return cleaned_text, result


def _attach_citations_to_sentences(
    answer_text: str,
    evidence: list[Evidence],
    min_score: float = 3.0,
) -> tuple[str, int]:
    """Deterministically append the best-matching paper ID to uncited sentences.

    Last resort before the extractive fallback when an answer ends up without any valid
    citation. Scores by plain term/number overlap (not `_citation_evidence_score`, whose
    kind bonus of +8 for claims would clear any threshold without a single shared term)
    and only attaches when the overlap is meaningfully strong.
    """
    text = str(answer_text or "")
    if not text or not evidence:
        return text, 0
    attached = 0

    def _attachment_score(sentence: str, item: Evidence) -> float:
        target = _evidence_match_text(item)
        if _is_boilerplate(target):
            return 0.0
        target_terms = set(_match_terms(target))
        sentence_terms = _match_terms(sentence)
        if not sentence_terms or not target_terms:
            return 0.0
        score = sum(1.0 for term in sentence_terms if term in target_terms)
        quantitative = _quantitative_tokens(sentence)
        if quantitative:
            score += 2.0 * sum(
                1 for token in quantitative if token in _quantitative_tokens(target)
            )
        return score

    def _replace(match: re.Match[str]) -> str:
        nonlocal attached
        sentence = match.group(0)
        if "[" in sentence or len(sentence.strip()) < 40:
            return sentence
        best_id = ""
        best_score = 0.0
        for item in evidence:
            if not item.paper_id:
                continue
            score = _attachment_score(sentence, item)
            if score > best_score:
                best_score = score
                best_id = item.paper_id
        if not best_id or best_score < min_score:
            return sentence
        stripped = sentence.rstrip()
        trailing = sentence[len(stripped) :]
        body = stripped.rstrip(".!?")
        punctuation = stripped[len(body) :]
        attached += 1
        return f"{body} [{best_id}]{punctuation}{trailing}"

    return _SENTENCE_RE.sub(_replace, text), attached


def _uncited_sentence_count(
    answer_text: str, known_ids: frozenset[str] = frozenset()
) -> int:
    """Count substantive sentences that carry no valid citation marker (transparency metric)."""
    count = 0
    for match in _SENTENCE_RE.finditer(str(answer_text or "")):
        sentence = match.group(0).strip()
        if len(sentence) < 40:
            continue
        cited = any(
            _citation_paper_ids(bracketed, known_ids)
            for bracketed in re.findall(r"\[([^\]]+)\]", sentence)
        )
        if not cited:
            count += 1
    return count


def _is_substantial_statement(
    sentence: str, min_chars: int = 30, min_words: int = 5
) -> bool:
    """A sentence worth citing — long enough and wordy enough to be a substantive claim."""
    stripped = re.sub(r"\s+", " ", (sentence or "")).strip()
    if len(stripped) < min_chars:
        return False
    word_count = sum(1 for _ in re.findall(r"\S+", stripped))
    return word_count >= min_words


def _per_sentence_citation_repair(
    answer_text: str,
    evidence: list[Evidence],
    known_ids: frozenset[str] = frozenset(),
    min_score: float = 3.0,
    unsourced_ratio_threshold: float = 0.5,
) -> tuple[str, dict[str, Any]]:
    """Safety layer that runs on EVERY answer, not just the zero-citation case.

    For each substantive uncited sentence:
      - if a best-matching evidence item scores >= `min_score`, append `[best_id]`;
      - otherwise mark the sentence inline with `‹unsourced›...‹/unsourced›` so the
        UI can flag it honestly instead of presenting an uncited claim as grounded.

    Returns the (possibly modified) answer text plus a diagnostics dict with
    `attached_count`, `unsourced_count`, `substantive_sentence_count`, and
    `fallback_reason="too_many_unsourced"` when the unsourced ratio exceeds the
    threshold. The caller is responsible for the hard fallback when
    `fallback_reason` is set.
    """
    text = str(answer_text or "")
    if not text:
        return text, {
            "attached_count": 0,
            "unsourced_count": 0,
            "substantive_sentence_count": 0,
        }
    if not evidence:
        substantive = 0
        unsourced = 0
        for match in _SENTENCE_RE.finditer(text):
            sentence = match.group(0)
            if _is_substantial_statement(sentence):
                substantive += 1
                if not _sentence_has_citation(sentence, known_ids):
                    unsourced += 1
        fallback = (
            "too_many_unsourced"
            if substantive and (unsourced / substantive) > unsourced_ratio_threshold
            else None
        )
        diag = {
            "attached_count": 0,
            "unsourced_count": unsourced,
            "substantive_sentence_count": substantive,
        }
        if fallback:
            diag["fallback_reason"] = fallback
        return text, diag

    def _attachment_score(sentence: str, item: Evidence) -> float:
        target = _evidence_match_text(item)
        if _is_boilerplate(target):
            return 0.0
        target_terms = set(_match_terms(target))
        sentence_terms = _match_terms(sentence)
        if not sentence_terms or not target_terms:
            return 0.0
        score = sum(1.0 for term in sentence_terms if term in target_terms)
        quantitative = _quantitative_tokens(sentence)
        if quantitative:
            score += 2.0 * sum(
                1 for token in quantitative if token in _quantitative_tokens(target)
            )
        return score

    attached = 0
    unsourced = 0
    substantive = 0

    def _replace(match: re.Match[str]) -> str:
        nonlocal attached, unsourced, substantive
        sentence = match.group(0)
        if not _is_substantial_statement(sentence):
            return sentence
        substantive += 1
        if _sentence_has_citation(sentence, known_ids):
            return sentence
        # Try to attach the best-matching paper ID.
        best_id = ""
        best_score = 0.0
        for item in evidence:
            if not item.paper_id:
                continue
            score = _attachment_score(sentence, item)
            if score > best_score:
                best_score = score
                best_id = item.paper_id
        if best_id and best_score >= min_score:
            stripped = sentence.rstrip()
            trailing = sentence[len(stripped) :]
            body = stripped.rstrip(".!?")
            punctuation = stripped[len(body) :]
            attached += 1
            return f"{body} [{best_id}]{punctuation}{trailing}"
        # No confident source: mark honestly so the UI can flag the gap.
        unsourced += 1
        stripped = sentence.rstrip()
        trailing = sentence[len(stripped) :]
        return f"‹unsourced›{stripped}‹/unsourced›{trailing}"

    repaired = _SENTENCE_RE.sub(_replace, text)
    fallback = (
        "too_many_unsourced"
        if substantive and (unsourced / substantive) > unsourced_ratio_threshold
        else None
    )
    diag: dict[str, Any] = {
        "attached_count": attached,
        "unsourced_count": unsourced,
        "substantive_sentence_count": substantive,
    }
    if fallback:
        diag["fallback_reason"] = fallback
    return repaired, diag


def _sentence_has_citation(sentence: str, known_ids: frozenset[str]) -> bool:
    """True when the sentence contains at least one bracket resolving to a known paper ID."""
    return any(
        _citation_paper_ids(bracketed, known_ids)
        for bracketed in re.findall(r"\[([^\]]+)\]", sentence)
    )


def _extract_substantive_claims(answer_text: str) -> list[str]:
    """Extract substantive sentences (with citations stripped) for conflict detection.

    Used by the contradiction guard to feed `ConflictDetector.analyze_claims_batch`.
    Short connector fragments and headings are skipped; citations are removed so the
    detector compares the claim wording, not the paper IDs.
    """
    text = str(answer_text or "")
    if not text:
        return []
    claims: list[str] = []
    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0)
        if not _is_substantial_statement(sentence):
            continue
        cleaned = re.sub(r"\s*\[[^\]]*\]\s*", " ", sentence)
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        if cleaned and len(cleaned) >= 30:
            claims.append(cleaned)
    return claims


# Direction tokens for the consistency heuristic. Each tuple groups tokens that
# assert the SAME direction; a sentence asserting a direction from group A and
# the opposite direction (from group B) without a qualifier is a candidate
# contradiction. The check is lexical, not semantic — it flags candidates for
# the LLM repair pass, it does not decide on its own.
_DIRECTION_POSITIVE = (
    "länger",
    "laenger",
    "longer",
    "verlängert",
    "verlaengert",
    "verbessert",
    "improved",
    "improves",
    "höher",
    "hoher",
    "higher",
    "increased",
    "increase",
    "anstieg",
    "steigt",
    "reduziert",
    "reduced",
    "reduces",
    "reduction",
    "senkt",
    "lowers",
    "lower",
    "vorteil",
    "benefit",
    "wirksam",
    "effective",
    "efficacy",
)
_DIRECTION_NEGATIVE = (
    "kürzer",
    "kuerzer",
    "shorter",
    "verschlechtert",
    "worsened",
    "worse",
    "niedriger",
    "decreased",
    "decrease",
    "rückgang",
)


# Negation tokens. When one of these appears within a small window BEFORE a
# positive direction token, that positive is flipped to negative (e.g.
# "kein Vorteil" / "not improved" / "ohne Benefit" → negative).
_NEGATION_TOKENS = (
    "kein",
    "keine",
    "keinen",
    "keiner",
    "keines",
    "nicht",
    "ohne",
    "no ",
    "not ",
    "without",
    "failed",
    "failure",
    "ohne",
)


def _extract_directions(sentence: str) -> set[str]:
    """Return the set of direction tokens ('positive'|'negative') found in a
    sentence, with simple negation awareness: a negation token within ~40
    characters before a positive token flips it to negative.
    """
    lowered = " " + re.sub(r"\s+", " ", str(sentence or "").lower()).strip() + " "
    positives: list[tuple[int, str]] = []
    for token in _DIRECTION_POSITIVE:
        idx = lowered.find(token)
        while idx >= 0:
            positives.append((idx, token))
            idx = lowered.find(token, idx + 1)
    negatives: list[tuple[int, str]] = []
    for token in _DIRECTION_NEGATIVE:
        if token in _NEGATION_TOKENS:
            continue  # negation tokens handled separately
        idx = lowered.find(token)
        while idx >= 0:
            negatives.append((idx, token))
            idx = lowered.find(token, idx + 1)
    found: set[str] = set()
    for pos, _tok in positives:
        # Look for a negation token within 40 chars before this positive.
        window = lowered[max(0, pos - 40) : pos]
        negated = any(neg in window for neg in _NEGATION_TOKENS)
        found.add("negative" if negated else "positive")
    for _pos, _tok in negatives:
        found.add("negative")
    # Bare negation tokens (no positive nearby) also count as negative.
    for token in _NEGATION_TOKENS:
        if token in lowered:
            found.add("negative")
            break
    return found


def _directional_consistency_check(
    answer_text: str,
    evidence: list[Evidence],
    known_ids: frozenset[str] = frozenset(),
) -> list[dict[str, Any]]:
    """Lexical heuristic that flags sentences whose direction may contradict
    the evidence they cite (or an earlier sentence in the same answer).

    A sentence is flagged when:
      - it asserts a direction (positive or negative) AND
      - the cited evidence's match text asserts the OPPOSITE direction, OR
      - an earlier substantive sentence in the answer asserted the opposite
        direction about the same subject (cheap subject proxy: shared
        distinctive terms).

    Returns a list of flagged issues, each with `sentence`, `direction`,
    `conflict_with` ('evidence'|'earlier_sentence'), and `detail`. The caller
    feeds these into an LLM repair prompt. This is intentionally conservative:
    it only flags candidates, it does not edit the answer.
    """
    text = str(answer_text or "")
    if not text:
        return []

    issues: list[dict[str, Any]] = []
    earlier_directions: list[tuple[set[str], set[str], str]] = (
        []
    )  # (terms, directions, sentence)

    evidence_by_id: dict[str, list[Evidence]] = {}
    for item in evidence:
        if item.paper_id:
            evidence_by_id.setdefault(item.paper_id, []).append(item)

    for match in _SENTENCE_RE.finditer(text):
        sentence = match.group(0)
        if not _is_substantial_statement(sentence):
            continue
        sentence_dirs = _extract_directions(sentence)
        if not sentence_dirs:
            continue
        # Check against cited evidence.
        cited_ids = []
        for bracketed in re.findall(r"\[([^\]]+)\]", sentence):
            cited_ids.extend(_citation_paper_ids(bracketed, known_ids))
        for pid in cited_ids:
            for item in evidence_by_id.get(pid, []):
                evidence_dirs = _extract_directions(_evidence_match_text(item))
                if evidence_dirs and sentence_dirs.isdisjoint(evidence_dirs):
                    issues.append(
                        {
                            "sentence": sentence.strip(),
                            "direction": sorted(sentence_dirs),
                            "conflict_with": "evidence",
                            "detail": f"evidence [{pid}] asserts {sorted(evidence_dirs)}",
                            "paper_id": pid,
                        }
                    )
        # Check against earlier sentences (same-subject proxy: shared terms).
        sentence_terms = set(_match_terms(sentence))
        for earlier_terms, earlier_dirs, earlier_sentence in earlier_directions:
            if not earlier_dirs:
                continue
            # Contradiction candidate: opposite directions (disjoint) on the
            # same subject (>=2 shared distinctive terms).
            if not sentence_dirs.isdisjoint(earlier_dirs):
                continue
            shared = sentence_terms & earlier_terms
            if len(shared) >= 2:
                issues.append(
                    {
                        "sentence": sentence.strip(),
                        "direction": sorted(sentence_dirs),
                        "conflict_with": "earlier_sentence",
                        "detail": f"earlier: {earlier_sentence.strip()[:120]}",
                    }
                )
                break
        earlier_directions.append((sentence_terms, sentence_dirs, sentence))

    return issues


def _strip_invalid_citations(
    answer_text: str, known_ids: frozenset[str] = frozenset()
) -> str:
    """Remove citation markers that do not resolve to a real source.

    The LLM sometimes copies bibliography reference numbers out of the paper text
    (e.g. [17-22] or [26, 29]). Those point to a paper's own reference list, not to any
    source in this system, so they render as dead "!" chips. Keep only allowed paper-ID
    labels inside each bracket and drop the bracket entirely when none remain.
    """
    text = str(answer_text or "")
    if not text:
        return text

    def _replace(match: re.Match[str]) -> str:
        parts = [
            part.strip()
            for part in re.split(r"[,;]\s*", match.group(1))
            if part.strip()
        ]
        kept = [part for part in parts if _is_allowed_citation_label(part, known_ids)]
        return f"[{', '.join(kept)}]" if kept else ""

    text = re.sub(r"\[([^\]]+)\]", _replace, text)
    # Tidy artifacts left by removed brackets: stray space before punctuation, doubled spaces.
    text = re.sub(r"[ \t]+([.,;:!?])", r"\1", text)
    text = re.sub(r"[ \t]{2,}", " ", text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return text.strip()


def _is_transient_generation_error(error: str) -> bool:
    error_lower = str(error or "").lower()
    return any(
        marker in error_lower
        for marker in (
            "429",
            "503",
            "502",
            "504",
            "service unavailable",
            "temporarily",
            "timeout",
            "timed out",
            "rate limit",
            "high demand",
        )
    )


def _needs_clinical_model_role_instruction(
    question: str, evidence: list[Evidence]
) -> bool:
    text = " ".join([question, *(item.text for item in evidence[:8])]).lower()
    has_clinical = any(
        term in text
        for term in ("clinical", "clinic", "clinician", "patient", "physician")
    )
    has_model_role = any(
        term in text
        for term in (
            "deployed",
            "deployment",
            "evaluation",
            "rating",
            "rater",
            "grader",
            "gpt-4",
            "o3",
            "ai consult",
        )
    )
    return has_clinical and has_model_role
