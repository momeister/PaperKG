"""Auto-research answering for the normal workspace.

When a grounded answer cannot be supported from local sources, this orchestrator
mirrors the Tiefenanalyse auto-harvest loop (``research_tree.py``) for a *single*
question and additionally widens the context the way the Import tab does: an LLM
derives a handful of *related topics* from the question and those are harvested too.

Flow: answer → (if weak/forced) derive related topics → harvest papers (with real
Phase-3 extraction) + grey web sources for the question and each related topic →
re-answer with the freshly harvested evidence.

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


def _answer_has_citations(answer_dict: dict[str, Any]) -> bool:
    """True when the answer text carries a traceable local citation.

    Mirrors ``research_tree._answer_has_citations`` (kept local to avoid importing the
    heavy research-tree module just for a two-line regex).
    """
    text = str(answer_dict.get("answer") or "")
    return bool(re.search(r"\[(?:arxiv:|grey::)", text))


def _is_weak_answer(answer_dict: dict[str, Any]) -> bool:
    """Decide whether a grounded answer warrants an automatic web/paper harvest."""
    return bool(
        answer_dict.get("no_answer")
        or answer_dict.get("context_diagnostics", {}).get("low_relevance")
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
    - ``{"status": "harvesting", "scope": "main"|"related", "topic": str,
        "papers": [{id,title}], "grey": [{id,title,url}]}`` — one per harvest step.
    - ``{"status": "reanswering"}`` — re-answer started.
    - ``{"status": "harvest_error", "error": str, "topic": str}`` — non-fatal step error.
    - ``{"status": "done", "answer": <dict>, "harvest_summary": {...}}`` — final answer.
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

    # 3. Harvest the main question first, then each related topic. Sequential keeps
    # DuckDB's single writer happy (and matches the research-tree harvest loop).
    harvest_plan: list[tuple[str, str, int, int]] = [("main", harvest_question, main_papers, main_grey)]
    harvest_plan += [("related", topic, papers_per_topic, grey_per_topic) for topic in related_topics]

    for scope, topic, n_papers, n_grey in harvest_plan:
        step_papers: list[dict[str, str]] = []
        step_grey: list[dict[str, str]] = []
        try:
            paper_records = await harvest_for_question(
                question=topic,
                project_id=str(project_id or ""),
                db_path=metadata_db_path,
                pdf_base_dir=pdf_base_dir,
                projects_path=projects_path,
                max_papers=n_papers,
                llm_router=llm_router,
                provider=provider,
                model=model,
            )
            for record in paper_records:
                pid = str(record.get("id") or "")
                if pid and pid not in seen_pids:
                    seen_pids.add(pid)
                    new_paper_ids.append(pid)
                    entry = {"id": pid, "title": str(record.get("title") or pid)}
                    step_papers.append(entry)
                    harvested_papers.append(entry)
        except Exception as exc:  # noqa: BLE001 - keep going with remaining topics
            yield {"status": "harvest_error", "error": f"Paper-Suche fehlgeschlagen: {exc}", "topic": topic}

        if n_grey > 0:
            try:
                grey_records = await harvest_grey_sources_for_question(
                    question=topic,
                    project_id=str(project_id or ""),
                    db_path=metadata_db_path,
                    max_sources=n_grey,
                )
                for record in grey_records:
                    gid = str(record.get("id") or "")
                    if gid and gid not in seen_greys:
                        seen_greys.add(gid)
                        new_grey_ids.append(gid)
                        entry = {"id": gid, "title": str(record.get("title") or gid),
                                 "url": str(record.get("url") or "")}
                        step_grey.append(entry)
                        harvested_grey.append(entry)
            except Exception as exc:  # noqa: BLE001
                yield {"status": "harvest_error", "error": f"Web-Suche fehlgeschlagen: {exc}", "topic": topic}

        yield {"status": "harvesting", "scope": scope, "topic": topic,
               "papers": step_papers, "grey": step_grey}

    # 4. Re-answer with the harvested evidence merged in. Mirror the research-tree
    # merge: keep existing scope IDs, append the new papers; both empty → None.
    if new_paper_ids or new_grey_ids:
        existing_ids = [pid for pid in (paper_ids or []) if pid != "__none__"]
        effective_paper_ids = (existing_ids + new_paper_ids) if (existing_ids or new_paper_ids) else None
        effective_grey_ids = (list(grey_source_ids or []) + new_grey_ids) or None
        yield {"status": "reanswering"}
        answer_dict = await asyncio.to_thread(_answer, effective_paper_ids, effective_grey_ids)

    yield {
        "status": "done",
        "answer": answer_dict,
        "harvest_summary": {
            "harvested": bool(new_paper_ids or new_grey_ids),
            "papers": harvested_papers,
            "grey": harvested_grey,
            "related_topics": related_topics,
        },
    }
