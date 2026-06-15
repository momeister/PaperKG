from __future__ import annotations

import asyncio
import json
import re
import uuid
from collections import defaultdict
from typing import Any, AsyncIterator

from query.auto_harvester import harvest_for_question, harvest_grey_sources_for_question
from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever
from query.llm_router import LLMRouter


_DECOMPOSE_SYSTEM = (
    "You are a research assistant. "
    "You break complex questions into focused sub-questions that can each be answered from scientific literature."
)

_DECOMPOSE_USER = (
    'Research question: "{question}"\n\n'
    "Generate exactly {n} focused sub-questions that together give a comprehensive answer to the main question. "
    "Each sub-question must be independently answerable from scientific papers.\n"
    "Return ONLY a valid JSON array of strings, nothing else:\n"
    '["sub-question 1", "sub-question 2", ...]'
)


def _extract_questions(text: str, max_n: int) -> list[str]:
    """Parse sub-questions from LLM output; falls back to newline splitting."""
    match = re.search(r"\[.*?\]", text, re.DOTALL)
    if match:
        try:
            items = json.loads(match.group())
            if isinstance(items, list):
                return [str(q).strip() for q in items[:max_n] if str(q).strip()]
        except (json.JSONDecodeError, ValueError):
            pass
    lines = [
        re.sub(r"^[\s\d.\-)\]]+", "", line).strip()
        for line in text.splitlines()
        if line.strip()
    ]
    return [line for line in lines if len(line) > 10][:max_n]


class ResearchTreeRunner:
    def __init__(self, llm_router: LLMRouter) -> None:
        self.llm_router = llm_router

    def _decompose_sync(
        self,
        question: str,
        n: int,
        provider: str | None,
        model: str | None,
    ) -> list[str]:
        overrides: dict[str, Any] = {"max_tokens": 512, "temperature": 0.3}
        if model:
            overrides["model"] = model
        text = self.llm_router.chat(
            messages=[
                {"role": "system", "content": _DECOMPOSE_SYSTEM},
                {"role": "user", "content": _DECOMPOSE_USER.format(question=question, n=n)},
            ],
            provider=provider,
            overrides=overrides,
        )
        return _extract_questions(text or "", n)

    def _answer_sync(
        self,
        question: str,
        provider: str | None,
        model: str | None,
        paper_ids: list[str] | None,
        project_id: str | None,
        grey_source_ids: list[str] | None,
        include_project_grey: bool,
        metadata_db_path: str,
        pdf_base_dir: str,
        root_question: str | None = None,
    ) -> dict[str, Any]:
        retriever = HybridRetriever()
        responder = GroundedResponder(retriever=retriever, llm_router=self.llm_router)
        conversation_context: list[dict[str, Any]] | None = None
        if root_question and root_question != question:
            conversation_context = [{"role": "user", "content": f"Übergeordnete Forschungsfrage: {root_question}"}]
        answer = responder.answer(
            question=question,
            limit=12,
            provider=provider,
            model=model,
            overrides={"max_tokens": 3000, "verbose_mode": True},
            conversation_context=conversation_context,
            paper_ids=paper_ids,
            project_id=project_id,
            grey_source_ids=grey_source_ids,
            include_project_grey=include_project_grey,
            metadata_db_path=metadata_db_path,
            pdf_base_dir=pdf_base_dir,
        )
        return answer.to_dict()

    def _synthesize_sync(
        self,
        nodes: list[dict[str, Any]],
        root_question: str,
        provider: str | None,
        model: str | None,
    ) -> str:
        """Generate thesis-style document via section-by-section expansion.

        One LLM call per depth-1 chapter group preserves citations from individual
        node answers instead of compressing them in a single summarisation pass.
        """
        if not nodes or self.llm_router is None:
            return ""

        def _llm(system: str, user: str, max_tokens: int = 2500) -> str:
            ov: dict[str, Any] = {"max_tokens": max_tokens, "temperature": 0.3}
            if model:
                ov["model"] = model
            try:
                return str(self.llm_router.chat(
                    messages=[
                        {"role": "system", "content": system},
                        {"role": "user", "content": user},
                    ],
                    provider=provider,
                    overrides=ov,
                ) or "").strip()
            except Exception:
                return ""

        CITE_INSTR = (
            "Übernimm ALLE Quellenangaben ([arxiv:...], [grey::...]) "
            "EXAKT und VOLLSTÄNDIG aus den Antworten. Erfinde KEINE neuen Zitierungen. "
            "Zitiere jede einzelne Behauptung. Antworte auf Deutsch."
        )

        known_ids: frozenset[str] = frozenset(
            str(s.get("paper_id") or "")
            for n in nodes
            for s in (n["answer"].get("sources") or [])
            if s.get("paper_id")
        )

        root_answer = next(
            (n["answer"].get("answer", "") for n in nodes if n["depth"] == 0), ""
        )
        depth1_nodes = [n for n in nodes if n["depth"] == 1]
        deeper_by_chapter: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for n in nodes:
            if n["depth"] > 1:
                cq = n.get("chapter_question") or n["question"]
                deeper_by_chapter[cq].append(n)

        # Introduction
        intro = _strip_unknown_citations(_llm(
            f"Du bist ein wissenschaftlicher Autor. {CITE_INSTR}",
            f"Hauptforschungsfrage: {root_question}\n\n"
            f"Überblick:\n{root_answer}\n\n"
            "Schreibe eine wissenschaftliche Einleitung (200-300 Wörter), die die Forschungsfrage "
            "einführt, den wissenschaftlichen Kontext erläutert und den Aufbau der Arbeit beschreibt.",
            max_tokens=800,
        ), known_ids)

        # Chapters — one per depth-1 node; expanded, not compressed
        chapter_texts: list[str] = []
        for d1 in depth1_nodes:
            cq = d1["question"]
            d1_ans = d1["answer"].get("answer", "")
            deeper = deeper_by_chapter.get(cq, [])
            deeper_block = ""
            if deeper:
                deeper_block = "\n\nTiefere Analysen zu diesem Kapitel:\n" + "\n\n".join(
                    f"Unterfrage: {n['question']}\nBefund: {n['answer'].get('answer', '')}"
                    for n in deeper
                )
            chapter_text = _strip_unknown_citations(_llm(
                f"Du bist ein wissenschaftlicher Autor. Schreibe ein vollständiges Kapitel "
                f"einer Bachelorarbeit. {CITE_INSTR}",
                f"Kapitelthema: {cq}\n\n"
                f"Hauptantwort:\n{d1_ans}"
                f"{deeper_block}\n\n"
                "Schreibe ein vollständiges, detailliertes Kapitel (mindestens 400-600 Wörter) "
                "mit ### Unterabschnitten wo sinnvoll. Belege JEDE Aussage mit Quellenangaben. "
                "Verwende einen wissenschaftlichen, präzisen Schreibstil.",
                max_tokens=2500,
            ), known_ids)
            chapter_texts.append(f"## {cq}\n\n{chapter_text}")

        # Conclusion
        chapter_titles = "; ".join(n["question"] for n in depth1_nodes)
        conclusion = _strip_unknown_citations(_llm(
            f"Du bist ein wissenschaftlicher Autor. {CITE_INSTR}",
            f"Hauptforschungsfrage: {root_question}\n\n"
            f"Untersuchte Aspekte: {chapter_titles}\n\n"
            "Schreibe ein wissenschaftliches Fazit (200-300 Wörter), das die wichtigsten "
            "Erkenntnisse zusammenfasst, die Hauptforschungsfrage beantwortet und Implikationen "
            "sowie offene Fragen diskutiert.",
            max_tokens=800,
        ), known_ids)

        parts: list[str] = []
        if intro:
            parts.append(f"## Einleitung\n\n{intro}")
        parts.extend(chapter_texts)
        if conclusion:
            parts.append(f"## Fazit\n\n{conclusion}")
        return "\n\n".join(parts)

    async def stream_events(
        self,
        question: str,
        depth: int = 1,
        branches: int = 3,
        provider: str | None = None,
        model: str | None = None,
        paper_ids: list[str] | None = None,
        project_id: str | None = None,
        grey_source_ids: list[str] | None = None,
        include_project_grey: bool = False,
        metadata_db_path: str = "data/metadata.duckdb",
        pdf_base_dir: str = "data/pdfs",
        auto_harvest: bool = False,
        projects_path: str = "data/projects.json",
        max_nodes: int = 25,
    ) -> AsyncIterator[str]:
        """Yields SSE-formatted lines: ``data: <json>\\n\\n``."""
        kwargs: dict[str, Any] = dict(
            provider=provider,
            model=model,
            paper_ids=paper_ids,
            project_id=project_id,
            grey_source_ids=grey_source_ids,
            include_project_grey=include_project_grey,
            metadata_db_path=metadata_db_path,
            pdf_base_dir=pdf_base_dir,
            root_question=question,
            auto_harvest=auto_harvest,
            projects_path=projects_path,
        )
        nodes_cache: list[dict[str, Any]] = []
        nodes_done: list[int] = [0]
        async for event in self._node(
            question, None, 0, depth, branches, kwargs, nodes_cache,
            chapter_question=None, nodes_done=nodes_done, max_nodes=max_nodes,
        ):
            yield event
        if nodes_cache:
            try:
                doc = await asyncio.to_thread(
                    self._synthesize_sync, nodes_cache, question, provider, model
                )
                if doc:
                    yield _sse({"id": "synthesis", "parent_id": None, "question": question,
                                "depth": 0, "status": "synthesis", "answer": None,
                                "document": doc, "child_count": 0})
                else:
                    yield _llm_error_event(
                        "synthesis", question, 0, "empty_synthesis",
                        prefix="Gesamtantwort konnte nicht erzeugt werden (LLM lieferte keine Inhalte). ",
                    )
            except Exception as exc:
                yield _llm_error_event(
                    "synthesis", question, 0, str(exc),
                    prefix="Gesamtantwort konnte nicht erzeugt werden. ",
                )

    async def _node(
        self,
        question: str,
        parent_id: str | None,
        current_depth: int,
        max_depth: int,
        branches: int,
        kwargs: dict[str, Any],
        nodes_cache: list[dict[str, Any]] | None = None,
        chapter_question: str | None = None,
        nodes_done: list[int] | None = None,
        max_nodes: int = 50,
    ) -> AsyncIterator[str]:
        # Global node budget — stop spawning when exhausted
        if nodes_done is not None:
            if nodes_done[0] >= max_nodes:
                return
            nodes_done[0] += 1

        node_id = str(uuid.uuid4())

        yield _sse({"id": node_id, "parent_id": parent_id, "question": question,
                    "depth": current_depth, "status": "running", "answer": None})

        try:
            answer_dict = await asyncio.to_thread(
                self._answer_sync,
                question,
                kwargs.get("provider"),
                kwargs.get("model"),
                kwargs.get("paper_ids"),
                kwargs.get("project_id"),
                kwargs.get("grey_source_ids"),
                bool(kwargs.get("include_project_grey")),
                str(kwargs.get("metadata_db_path") or "data/metadata.duckdb"),
                str(kwargs.get("pdf_base_dir") or "data/pdfs"),
                str(kwargs.get("root_question") or question),
            )
        except Exception as exc:
            yield _sse({"id": node_id, "parent_id": parent_id, "question": question,
                        "depth": current_depth, "status": "error", "answer": None,
                        "error": str(exc)})
            return

        should_harvest = (
            bool(kwargs.get("auto_harvest"))
            and (answer_dict.get("no_answer") or answer_dict.get("context_diagnostics", {}).get("low_relevance"))
        )

        harvested_paper_info: list[dict[str, Any]] = []
        harvested_grey_info: list[dict[str, Any]] = []

        if should_harvest:
            yield _sse({"id": node_id, "parent_id": parent_id, "question": question,
                        "depth": current_depth, "status": "harvesting", "answer": None})
            try:
                # Harvest academic papers
                paper_records = await harvest_for_question(
                    question=question,
                    project_id=str(kwargs.get("project_id") or ""),
                    db_path=str(kwargs.get("metadata_db_path") or "data/metadata.duckdb"),
                    pdf_base_dir=str(kwargs.get("pdf_base_dir") or "data/pdfs"),
                    projects_path=str(kwargs.get("projects_path") or "data/projects.json"),
                )
                new_paper_ids = [r["id"] for r in paper_records]
                harvested_paper_info = [
                    {"id": r["id"], "title": r.get("title", r["id"])} for r in paper_records
                ]

                # Harvest grey (web) sources
                grey_records = await harvest_grey_sources_for_question(
                    question=question,
                    project_id=str(kwargs.get("project_id") or ""),
                    db_path=str(kwargs.get("metadata_db_path") or "data/metadata.duckdb"),
                    max_sources=2,
                )
                new_grey_ids = [r["id"] for r in grey_records]
                harvested_grey_info = [
                    {"id": r.get("id", ""), "title": r.get("title", ""), "url": r.get("url", "")}
                    for r in grey_records
                ]

                if new_paper_ids or new_grey_ids:
                    original_paper_ids = kwargs.get("paper_ids")
                    effective_paper_ids = (
                        list(original_paper_ids) + new_paper_ids if original_paper_ids else None
                    )
                    existing_grey = list(kwargs.get("grey_source_ids") or [])
                    effective_grey_ids = existing_grey + new_grey_ids if new_grey_ids else kwargs.get("grey_source_ids")
                    answer_dict = await asyncio.to_thread(
                        self._answer_sync,
                        question,
                        kwargs.get("provider"),
                        kwargs.get("model"),
                        effective_paper_ids,
                        str(kwargs.get("project_id") or ""),
                        effective_grey_ids,
                        bool(kwargs.get("include_project_grey")),
                        str(kwargs.get("metadata_db_path") or "data/metadata.duckdb"),
                        str(kwargs.get("pdf_base_dir") or "data/pdfs"),
                        str(kwargs.get("root_question") or question),
                    )
            except Exception:
                pass

        # Surface a degraded per-node answer (LLM failed → evidence-only fallback)
        # as a dedicated event so the user sees *why*, not just a fallback blob.
        gen_error = answer_dict.get("generation_error")
        if gen_error:
            yield _llm_error_event(node_id, question, current_depth, str(gen_error))

        sub_questions: list[str] = []
        if current_depth < max_depth:
            try:
                sub_questions = await asyncio.to_thread(
                    self._decompose_sync,
                    question,
                    branches,
                    kwargs.get("provider"),
                    kwargs.get("model"),
                )
            except Exception as exc:
                # The same LLM failure also blocks decomposition → the tree stays flat
                # (one node, depth 1). Tell the user instead of silently swallowing it.
                yield _llm_error_event(
                    node_id, question, current_depth, str(exc),
                    prefix="Teilfragen konnten nicht erzeugt werden, der Baum bleibt flach. ",
                )
                sub_questions = []

        yield _sse({
            "id": node_id,
            "parent_id": parent_id,
            "question": question,
            "depth": current_depth,
            "status": "done",
            "answer": answer_dict,
            "child_count": len(sub_questions),
            "harvested_papers": harvested_paper_info,
            "harvested_grey": harvested_grey_info,
        })

        if nodes_cache is not None and not answer_dict.get("no_answer"):
            nodes_cache.append({
                "question": question,
                "answer": answer_dict,
                "depth": current_depth,
                "chapter_question": chapter_question,
            })

        for sub_q in sub_questions:
            # Depth-1 nodes each start a new synthesis chapter; deeper nodes inherit the chapter key
            child_chapter = sub_q if current_depth == 0 else chapter_question
            async for event in self._node(
                sub_q, node_id, current_depth + 1, max_depth, branches, kwargs,
                nodes_cache, child_chapter, nodes_done, max_nodes,
            ):
                yield event


def _strip_unknown_citations(text: str, known_ids: frozenset[str]) -> str:
    """Remove [xxx] citation brackets whose ID is not among the known paper IDs."""
    if not known_ids:
        return text

    def _keep(m: re.Match) -> str:
        cid = m.group(1).strip()
        norm = re.sub(r"v\d+$", "", cid.lower().replace(" ", ""))
        for kid in known_ids:
            kid_norm = re.sub(r"v\d+$", "", kid.lower().replace(" ", ""))
            if norm == kid_norm or kid_norm.endswith(norm) or norm.endswith(kid_norm):
                return m.group(0)
        return ""

    return re.sub(r"\[[^\]]+\]", _keep, text)


def _classify_llm_error(error: str) -> tuple[str, str]:
    """Map a raw LLM error string to (kind, human-readable German message).

    Lets the UI tell the user *why* the analysis degraded — quota vs. rate limit
    vs. auth vs. connection — instead of a generic "evidence-only fallback".
    """
    e = (error or "").lower()
    if e in ("empty_synthesis", "empty_response") or "empty response" in e:
        return (
            "empty",
            "Das Modell lieferte eine leere Antwort (evtl. abgeschnitten oder überlastet). "
            "Erneut versuchen oder ein anderes Modell wählen.",
        )
    if any(k in e for k in ("insufficient_quota", "quota", "resource_exhausted", "billing", "credit", "exhausted")):
        return (
            "quota",
            "LLM-Kontingent/Guthaben aufgebraucht. Deine KI-Anfragen für diesen Anbieter sind "
            "vorerst erschöpft — warte (z.B. bis morgen) oder wechsle Provider/API-Key.",
        )
    if any(k in e for k in ("rate limit", "rate_limit", "ratelimit", "429", "too many requests")):
        return (
            "rate_limit",
            "Rate-Limit des LLM-Anbieters erreicht (HTTP 429). Zu viele Anfragen in kurzer Zeit — "
            "kurz warten und erneut versuchen, oder Tiefe/Zweige reduzieren.",
        )
    if any(k in e for k in ("401", "403", "unauthorized", "invalid api key", "invalid_api_key", "authentication", "api key", "permission")):
        return (
            "auth",
            "Authentifizierung fehlgeschlagen — API-Key fehlt oder ist ungültig. "
            "Prüfe den Key in .env / config.yaml.",
        )
    if any(k in e for k in ("context length", "maximum context", "context_length", "too many tokens", "reduce the length", "context window")):
        return (
            "context_length",
            "Anfrage überschreitet das Kontextfenster des Modells. Reduziere Tiefe/Zweige oder die "
            "Anzahl der einbezogenen Quellen.",
        )
    if any(k in e for k in ("timeout", "timed out", "connection", "connect", "refused", "max retries", "name or service", "unreachable", "econnrefused")):
        return (
            "connection",
            "Keine Verbindung zum LLM (Timeout/Connection). Läuft LM Studio bzw. der konfigurierte "
            "Anbieter und ist er erreichbar?",
        )
    return ("unknown", f"LLM-Aufruf fehlgeschlagen: {(error or '').strip()[:300]}")


def _llm_error_event(node_id: str, question: str, depth: int, raw: str, prefix: str = "") -> str:
    kind, human = _classify_llm_error(raw)
    return _sse({
        "id": f"err-{node_id}",
        "parent_id": None,
        "question": question,
        "depth": depth,
        "status": "llm_error",
        "error": raw,
        "error_kind": kind,
        "message": (prefix + human) if prefix else human,
    })


def _sse(data: dict[str, Any]) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"
