"""Auto-research answering for the normal workspace.

When a grounded answer cannot be supported from local sources, this orchestrator
mirrors the Tiefenanalyse auto-harvest loop (``research_tree.py``) for a *single*
question and additionally widens the context the way the Import tab does: an LLM
derives a handful of *related topics* from the question and those are harvested too.

Flow: answer → (if weak/forced) derive related topics → harvest in three stages,
re-answering after each and stopping as soon as the answer holds:

1. ``scientific``  — Paper aus den wissenschaftlichen Quellen (mit echter Phase-3-Extraktion)
2. ``trusted``     — Webquellen von Behörden, Hochschulen, Fachverlagen
3. ``unverified``  — der Rest des Webs, als ungeprüft markiert

Damit landen unsichere Quellen nur dann in der Antwort, wenn die belastbareren
Stufen die Frage nicht decken konnten.

Deliberately minimal dependencies — no ``api/`` imports — so it stays importable and
testable without pulling the FastAPI app into the import graph.
"""
from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING, Any

from query.auto_harvester import (
    harvest_for_question,
    harvest_grey_sources_for_question,
)
from query.discovery import analyze_topic
from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever

if TYPE_CHECKING:
    from query.llm_router import LLMRouter


#: Eskalationsleiter der Auto-Recherche. ``tier`` ist die Domain-Stufe aus
#: research/source_tiers.py und gilt nur fuer die Web-Stufen.
HARVEST_STAGES: tuple[dict[str, str], ...] = (
    {"id": "scientific", "label": "Wissenschaftliche Quellen", "tier": ""},
    {"id": "trusted", "label": "Vertrauenswürdige Webquellen", "tier": "trusted"},
    {"id": "unverified", "label": "Ungeprüfte Webquellen", "tier": "unknown"},
)


def _answer_has_citations(answer_dict: dict[str, Any]) -> bool:
    """True when the answer text carries a traceable local citation.

    Mirrors ``research_tree._answer_has_citations`` (kept local to avoid importing the
    heavy research-tree module just for a two-line regex).
    """
    text = str(answer_dict.get("answer") or "")
    return bool(re.search(r"\[(?:arxiv:|grey::)", text))


def _is_weak_answer(answer_dict: dict[str, Any]) -> bool:
    """Decide whether a grounded answer warrants an automatic web/paper harvest.

    ``insufficient_evidence`` is the responder's own verdict (Sentinel-Token bzw.
    „keine Informationen"-Prosa, siehe grounded_responder.detect_insufficient_evidence):
    eine Antwort kann Quellen zitieren UND trotzdem eine Evidenz-Lücke melden — vorher
    galt die dann fälschlich als ausreichend („Lokale Quellen reichten aus"-Widerspruch).
    """
    diagnostics = answer_dict.get("context_diagnostics") or {}
    return bool(
        answer_dict.get("no_answer")
        or diagnostics.get("low_relevance")
        or diagnostics.get("insufficient_evidence")
        or diagnostics.get("fallback_reason") == "no_traceable_citations"
        or not _answer_has_citations(answer_dict)
    )


def _run_answer_sync(
    *,
    llm_router: "LLMRouter",
    question: str,
    provider: str | None,
    model: str | None,
    overrides: dict[str, Any] | None,
    conversation_context: list[dict[str, Any]] | None,
    paper_ids: list[str] | None,
    priority_paper_ids: list[str] | None,
    answer_context_mode: str,
    limit: int,
    project_id: str | None,
    grey_source_ids: list[str] | None,
    include_project_grey: bool,
    metadata_db_path: str,
    pdf_base_dir: str,
) -> dict[str, Any]:
    """Blocking grounded answer — run via ``asyncio.to_thread`` from the async flow."""
    retriever = HybridRetriever()
    responder = GroundedResponder(retriever=retriever, llm_router=llm_router)
    answer = responder.answer(
        question=question,
        limit=limit,
        provider=provider,
        model=model,
        overrides=overrides,
        conversation_context=conversation_context,
        paper_ids=paper_ids,
        priority_paper_ids=priority_paper_ids,
        answer_context_mode=answer_context_mode,
        pdf_base_dir=pdf_base_dir,
        project_id=project_id,
        grey_source_ids=grey_source_ids,
        include_project_grey=include_project_grey,
        metadata_db_path=metadata_db_path,
    )
    return answer.to_dict()


async def auto_research_answer(
    *,
    question: str,
    llm_router: "LLMRouter",
    search_question: str | None = None,
    project_id: str | None = None,
    provider: str | None = None,
    model: str | None = None,
    overrides: dict[str, Any] | None = None,
    conversation_context: list[dict[str, Any]] | None = None,
    paper_ids: list[str] | None = None,
    priority_paper_ids: list[str] | None = None,
    answer_context_mode: str = "kg",
    limit: int = 12,
    grey_source_ids: list[str] | None = None,
    include_project_grey: bool = False,
    force: bool = False,
    max_related_topics: int = 5,
    main_papers: int = 3,
    main_grey: int = 2,
    papers_per_topic: int = 2,
    grey_per_topic: int = 1,
    metadata_db_path: str = "data/metadata.duckdb",
    pdf_base_dir: str = "data/pdfs",
    projects_path: str = "data/projects.json",
) -> AsyncIterator[dict[str, Any]]:
    """Answer *question*, auto-harvesting papers + web sources when the answer is weak.

    Yields framework-agnostic event dicts (the endpoint serialises them as SSE):

    - ``{"status": "answer", "answer": <dict>}`` — the initial grounded answer.
    - ``{"status": "planning", "related_topics": [...]}`` — derived related topics.
    - ``{"status": "harvesting", "stage": str, "stage_label": str,
        "scope": "main"|"related", "topic": str,
        "papers": [{id,title}], "grey": [{id,title,url,trust_tier}]}`` — one per harvest step.
    - ``{"status": "reanswering", "stage": str}`` — re-answer after a stage.
    - ``{"status": "harvest_error", "error": str, "topic": str, "stage": str}`` — non-fatal error.
    - ``{"status": "done", "answer": <dict>, "harvest_summary": {..., "stages": [...]}}``.
    """

    def _answer(
        pid_list: list[str] | None,
        grey_list: list[str] | None,
    ) -> Any:
        return _run_answer_sync(
            llm_router=llm_router,
            question=question,
            provider=provider,
            model=model,
            overrides=overrides,
            conversation_context=conversation_context,
            paper_ids=pid_list,
            priority_paper_ids=priority_paper_ids,
            answer_context_mode=answer_context_mode,
            limit=limit,
            project_id=project_id,
            grey_source_ids=grey_list,
            include_project_grey=include_project_grey,
            metadata_db_path=metadata_db_path,
            pdf_base_dir=pdf_base_dir,
        )

    # 1. First pass over local sources only.
    answer_dict = await asyncio.to_thread(_answer, paper_ids, grey_source_ids)
    yield {"status": "answer", "answer": answer_dict}

    if not (force or _is_weak_answer(answer_dict)):
        yield {"status": "done", "answer": answer_dict,
               "harvest_summary": {"harvested": False, "papers": [], "grey": [], "related_topics": []}}
        return

    # The question carries an answer-style hint (e.g. a verbosity instruction); harvest and
    # related-topic analysis must use the *clean* question so the search is not polluted.
    harvest_question = (search_question or question).strip()

    # 2. Derive related topics to widen the harvest (best-effort; never fatal).
    related_topics: list[str] = []
    try:
        analysis = await asyncio.to_thread(analyze_topic, llm_router, harvest_question, provider)
        related_topics = [t for t in (analysis.get("related_topics") or []) if t][:max_related_topics]
    except Exception as exc:  # noqa: BLE001 - planning is optional, keep harvesting
        yield {"status": "harvest_error", "error": f"Themenanalyse fehlgeschlagen: {exc}", "topic": ""}
    yield {"status": "planning", "related_topics": related_topics}

    new_paper_ids: list[str] = []
    new_grey_ids: list[str] = []
    harvested_papers: list[dict[str, str]] = []
    harvested_grey: list[dict[str, str]] = []
    seen_pids: set[str] = set(paper_ids or [])
    seen_greys: set[str] = set(grey_source_ids or [])
    stage_summaries: list[dict[str, Any]] = []

    # Topics for every stage: the main question first, then each related topic.
    # Sequential keeps DuckDB's single writer happy (and matches the research-tree loop).
    topic_plan: list[tuple[str, str, int, int]] = [("main", harvest_question, main_papers, main_grey)]
    topic_plan += [("related", topic, papers_per_topic, grey_per_topic) for topic in related_topics]

    async def _harvest_papers(topic: str, count: int) -> tuple[list[dict[str, str]], str | None]:
        entries: list[dict[str, str]] = []
        try:
            records = await harvest_for_question(
                question=topic,
                project_id=str(project_id or ""),
                db_path=metadata_db_path,
                pdf_base_dir=pdf_base_dir,
                projects_path=projects_path,
                max_papers=count,
                llm_router=llm_router,
                provider=provider,
                model=model,
            )
        except Exception as exc:  # noqa: BLE001 - keep going with remaining topics
            return entries, f"Paper-Suche fehlgeschlagen: {exc}"
        for record in records:
            pid = str(record.get("id") or "")
            if pid and pid not in seen_pids:
                seen_pids.add(pid)
                new_paper_ids.append(pid)
                entry = {"id": pid, "title": str(record.get("title") or pid)}
                entries.append(entry)
                harvested_papers.append(entry)
        return entries, None

    async def _harvest_grey(topic: str, count: int, tier: str) -> tuple[list[dict[str, str]], str | None]:
        entries: list[dict[str, str]] = []
        if count <= 0:
            return entries, None
        try:
            records = await harvest_grey_sources_for_question(
                question=topic,
                project_id=str(project_id or ""),
                db_path=metadata_db_path,
                max_sources=count,
                tiers=(tier,),
            )
        except Exception as exc:  # noqa: BLE001
            return entries, f"Web-Suche fehlgeschlagen: {exc}"
        for record in records:
            gid = str(record.get("id") or "")
            if gid and gid not in seen_greys:
                seen_greys.add(gid)
                new_grey_ids.append(gid)
                entry = {
                    "id": gid,
                    "title": str(record.get("title") or gid),
                    "url": str(record.get("url") or ""),
                    "trust_tier": str(record.get("trust_tier") or tier),
                }
                entries.append(entry)
                harvested_grey.append(entry)
        return entries, None

    # 3. Escalate one source class at a time: scientific sources first, then
    # trustworthy institutions/publishers, and only if the answer still does not
    # hold, the rest of the web. After each stage the question is answered again —
    # a stage that already carries the answer stops the ladder.
    for stage_index, stage in enumerate(HARVEST_STAGES):
        stage_id = stage["id"]
        stage_papers = 0
        stage_grey = 0
        for scope, topic, n_papers, n_grey in topic_plan:
            step_papers: list[dict[str, str]] = []
            step_grey: list[dict[str, str]] = []
            if stage_id == "scientific":
                step_papers, error = await _harvest_papers(topic, n_papers)
            else:
                step_grey, error = await _harvest_grey(topic, n_grey, str(stage["tier"]))
            if error:
                yield {"status": "harvest_error", "error": error, "topic": topic, "stage": stage_id}
            stage_papers += len(step_papers)
            stage_grey += len(step_grey)
            yield {"status": "harvesting", "stage": stage_id, "stage_label": stage["label"],
                   "scope": scope, "topic": topic, "papers": step_papers, "grey": step_grey}

        found_here = bool(stage_papers or stage_grey)
        is_last_stage = stage_index == len(HARVEST_STAGES) - 1
        if found_here:
            # 4. Re-answer with everything harvested so far. Mirror the research-tree
            # merge: keep existing scope IDs, append the new ones; both empty → None.
            existing_ids = [pid for pid in (paper_ids or []) if pid != "__none__"]
            effective_paper_ids = (existing_ids + new_paper_ids) if (existing_ids or new_paper_ids) else None
            effective_grey_ids = (list(grey_source_ids or []) + new_grey_ids) or None
            yield {"status": "reanswering", "stage": stage_id}
            answer_dict = await asyncio.to_thread(_answer, effective_paper_ids, effective_grey_ids)

        sufficient = found_here and not _is_weak_answer(answer_dict)
        stage_summaries.append({
            "stage": stage_id,
            "label": stage["label"],
            "papers": stage_papers,
            "grey": stage_grey,
            "sufficient": sufficient,
        })
        if sufficient or is_last_stage:
            break

    yield {
        "status": "done",
        "answer": answer_dict,
        "harvest_summary": {
            "harvested": bool(new_paper_ids or new_grey_ids),
            "papers": harvested_papers,
            "grey": harvested_grey,
            "related_topics": related_topics,
            "stages": stage_summaries,
        },
    }
