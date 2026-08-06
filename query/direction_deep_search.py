"""Per-research-direction deep search.

For one ``TaskResearchDirection`` (label/rationale/keywords) this fans out:
  1. ``harvest_for_question`` over the keywords — many papers, downloaded+extracted,
     attached to the project so the KG sees them.
  2. ``harvest_grey_sources_for_question`` — web search → sanitized grey sources
     saved per-project.
  3. A grounded "Möglichkeitsprinzip" synthesis covering Machbarkeit, Ansätze,
     Risiken — grounded against the freshly harvested papers + grey sources
     (``[arxiv:...]`` and ``[grey::...]`` citations).

Everything is streamed as SSE ``data: <json>\\n\\n`` lines (the same format the
Research Tree uses), with a ``status`` field inside the JSON payload for state
transitions. No recursion, no tree — this is a breadth-first harvest + synthesis.
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator

from query.auto_harvester import (
    harvest_for_question,
    harvest_grey_sources_for_question,
)
from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever


def _sse(data: dict[str, Any]) -> str:
    """Format one SSE ``data:`` line (same contract as research_tree._sse)."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


class DirectionDeepSearchRunner:
    """Runs a per-direction deep search and streams progress over SSE."""

    def __init__(self, llm_router: Any) -> None:
        self.llm_router = llm_router

    async def stream(
        self,
        direction: dict[str, Any],
        project_id: str | None,
        task_spec: dict[str, Any] | None = None,
        *,
        max_papers: int = 20,
        max_web_sources: int = 10,
        provider: str | None = None,
        model: str | None = None,
        creativity_level: int | None = None,
        metadata_db_path: str = "data/metadata.duckdb",
        pdf_base_dir: str = "data/pdfs",
        projects_path: str = "data/projects.json",
    ) -> AsyncIterator[str]:
        """Stream the deep search for one research direction.

        Yields SSE lines (``data: {json}\\n\\n``). Event payloads carry a
        ``status`` field; phases:

          - ``planning``         — building the harvest query
          - ``harvesting_papers`` — start of paper harvest
          - ``search_complete``  — paper search returned N candidates
          - ``ingesting``        — one paper is being downloaded/extracted
          - ``ingested`` / ``ingest_failed`` — per-paper outcome
          - ``harvesting_grey``  — start of web-source harvest
          - ``grey_search_complete`` — web search returned N hits
          - ``fetched``          — one grey source saved
          - ``synthesizing``     — grounded summary being generated
          - ``done``             — final summary attached
          - ``error``            — unrecoverable failure
        """
        label = str(direction.get("label") or "").strip()
        rationale = str(direction.get("rationale") or "").strip()
        keywords = direction.get("keywords") or []
        if not isinstance(keywords, list):
            keywords = list(keywords)
        keywords = [str(k).strip() for k in keywords if str(k).strip()]

        if not label:
            yield _sse({"status": "error", "error": "Direction label is required."})
            return

        # Build the harvest query. Keywords lead so the search matches the
        # technical vocabulary; the label broadens it so related work is found
        # even when keywords are narrow.
        query_parts = [label]
        if rationale:
            query_parts.append(rationale)
        harvest_query = " | ".join(query_parts)
        if keywords:
            harvest_query = f"{harvest_query} | {' | '.join(keywords)}"

        yield _sse(
            {
                "status": "planning",
                "direction": {
                    "label": label,
                    "rationale": rationale,
                    "keywords": keywords,
                },
                "query": harvest_query,
            }
        )

        # ----- Phase 1: paper harvest -------------------------------------
        yield _sse({"status": "harvesting_papers", "query": harvest_query})
        harvested_papers: list[dict[str, Any]] = []
        paper_queue: asyncio.Queue = asyncio.Queue()

        def _paper_progress(payload: dict[str, Any]) -> None:
            # Called synchronously inside harvest_for_question (same loop) so
            # put_nowait is safe. The stream loop below drains the queue while
            # the producer task runs, so per-paper events stream live.
            paper_queue.put_nowait(payload)

        producer = asyncio.create_task(
            harvest_for_question(
                question=harvest_query,
                project_id=project_id,
                db_path=metadata_db_path,
                pdf_base_dir=pdf_base_dir,
                projects_path=projects_path,
                max_papers=max_papers,
                llm_router=self.llm_router,
                provider=provider,
                model=model,
                progress_callback=_paper_progress,
            )
        )
        try:
            while True:
                if producer.done() and paper_queue.empty():
                    break
                try:
                    payload = await asyncio.wait_for(paper_queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                phase = payload.get("phase")
                if phase == "search_complete":
                    yield _sse(
                        {
                            "status": "search_complete",
                            "phase": "papers",
                            "found": payload.get("found", 0),
                        }
                    )
                elif phase == "ingesting":
                    yield _sse({"status": "ingesting", "paper": payload.get("paper")})
                elif phase == "ingested":
                    yield _sse({"status": "ingested", "paper": payload.get("paper")})
                elif phase == "ingest_failed":
                    yield _sse(
                        {"status": "ingest_failed", "paper": payload.get("paper")}
                    )
            harvested_papers = producer.result()
            yield _sse(
                {
                    "status": "papers_harvested",
                    "count": len(harvested_papers),
                    "papers": [
                        {"id": p.get("id"), "title": p.get("title")}
                        for p in harvested_papers
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001 — surface to client
            if not producer.done():
                producer.cancel()
            yield _sse(
                {"status": "harvest_error", "phase": "papers", "error": str(exc)}
            )

        # ----- Phase 2: grey-source / web harvest -------------------------
        yield _sse({"status": "harvesting_grey", "query": harvest_query})
        grey_queue: asyncio.Queue = asyncio.Queue()

        def _grey_progress(payload: dict[str, Any]) -> None:
            grey_queue.put_nowait(payload)

        grey_producer = asyncio.create_task(
            harvest_grey_sources_for_question(
                question=harvest_query,
                project_id=project_id,
                db_path=metadata_db_path,
                max_sources=max_web_sources,
                tiers=None,
                progress_callback=_grey_progress,
            )
        )
        grey_sources: list[dict[str, Any]] = []
        try:
            while True:
                if grey_producer.done() and grey_queue.empty():
                    break
                try:
                    payload = await asyncio.wait_for(grey_queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                phase = payload.get("phase")
                if phase == "search_complete":
                    yield _sse(
                        {
                            "status": "grey_search_complete",
                            "found": payload.get("found", 0),
                        }
                    )
                elif phase == "fetched":
                    yield _sse({"status": "fetched", "source": payload.get("source")})
            grey_sources = grey_producer.result()
            yield _sse(
                {
                    "status": "grey_harvested",
                    "count": len(grey_sources),
                    "sources": [
                        {
                            "id": s.get("id"),
                            "title": s.get("title"),
                            "url": s.get("url"),
                        }
                        for s in grey_sources
                    ],
                }
            )
        except Exception as exc:  # noqa: BLE001
            if not grey_producer.done():
                grey_producer.cancel()
            yield _sse({"status": "harvest_error", "phase": "grey", "error": str(exc)})

        # ----- Phase 3: grounded possibility-principle synthesis ----------
        paper_ids = [str(p["id"]) for p in harvested_papers if p.get("id")]
        grey_ids = [str(s["id"]) for s in grey_sources if s.get("id")]

        yield _sse(
            {
                "status": "synthesizing",
                "papers": len(paper_ids),
                "grey_sources": len(grey_ids),
            }
        )

        try:
            summary = await self._synthesize(
                direction=direction,
                task_spec=task_spec,
                paper_ids=paper_ids,
                grey_ids=grey_ids,
                project_id=project_id,
                provider=provider,
                model=model,
                creativity_level=creativity_level,
                metadata_db_path=metadata_db_path,
            )
        except Exception as exc:  # noqa: BLE001
            yield _sse({"status": "error", "phase": "synthesis", "error": str(exc)})
            return

        yield _sse(
            {
                "status": "done",
                "summary": summary,
                "papers_count": len(paper_ids),
                "grey_count": len(grey_ids),
                "paper_ids": paper_ids,
                "grey_ids": grey_ids,
                "direction": direction,
            }
        )

    async def _synthesize(
        self,
        direction: dict[str, Any],
        task_spec: dict[str, Any] | None,
        paper_ids: list[str],
        grey_ids: list[str],
        project_id: str | None,
        *,
        provider: str | None,
        model: str | None,
        creativity_level: int | None,
        metadata_db_path: str,
    ) -> dict[str, Any]:
        """Grounded Machbarkeit/Ansätze/Risiken summary for one direction."""
        retriever = HybridRetriever()
        responder = GroundedResponder(retriever=retriever, llm_router=self.llm_router)

        label = direction.get("label", "")
        rationale = direction.get("rationale", "")
        keywords = direction.get("keywords") or []

        task_block = ""
        if task_spec:
            title = task_spec.get("title", "")
            objective = task_spec.get("objective", "")
            evaluation = task_spec.get("evaluation", "")
            task_block = (
                (
                    f"Kontext — Task-Spec:\n"
                    f"Titel: {title}\n"
                    f"Ziel: {objective}\n"
                    f"Eval {evaluation}\n\n"
                )
                if (title or objective or evaluation)
                else ""
            )

        creativity_hint = ""
        if creativity_level is not None:
            if creativity_level >= 4:
                creativity_hint = (
                    "Berücksichtige auch unkonventionelle oder cross-domain Ansätze, "
                    "die vielversprechend sein könnten. "
                )
            elif creativity_level <= 2:
                creativity_hint = "Fokussiere auf etablierte, gut belegte Ansätze. "

        prompt = (
            f"{task_block}"
            f"Forschungsrichtung: {label}\n"
            f"Rationale: {rationale}\n"
            f"Keywords: {', '.join(keywords) if keywords else '—'}\n\n"
            "Erstelle ein Möglichkeitsprinzip für diese Forschungsrichtung, "
            "gegroundet auf die lokalen Paper und Web-Quellen. Gliedere deine Antwort "
            "in genau vier Markdown-Abschnitte mit diesen Überschriften:\n"
            "## Machbarkeit\n"
            "Bewerte, wie machbar diese Richtung ist — welche Voraussetzungen erfüllt "
            "sein müssen, was bereits gezeigt wurde, was realistisch in diesem Rahmen "
            "liegt. Beziehe die Task-Spec ein, wenn vorhanden.\n"
            "## Ansätze\n"
            "Nenne konkrete technische Ansätze aus der Literatur, die diese Richtung "
            "verfolgen. Beschreibe jeden Ansatz kurz (was er macht, worauf er aufbaut).\n"
            "## Risiken\n"
            "Nenne die Hauptrisiken und offenen Gaps: was schwierig ist, was schiefgehen "
            "kann, welche Annahmen noch nicht geprüft sind.\n"
            "## Fazit\n"
            "Eine kompakte Gesamteinschätzung: lohnt sich diese Richtung, und warum.\n\n"
            f"{creativity_hint}"
            "Belege alle Aussagen mit Paper-IDs in eckigen Klammern "
            "(z. B. [arxiv:1234.5678]) und Web-Quellen (z. B. [grey::task_abc] oder "
            "[grey::abc123]). Schreibe verständlich und konkret, erfinde keine Quellen — "
            "wenn du keine Belege für eine Aussage hast, sag das offen."
        )

        answer = await asyncio.to_thread(
            responder.answer,
            prompt,
            limit=12,
            provider=provider,
            model=model,
            paper_ids=paper_ids or None,
            project_id=project_id,
            metadata_db_path=metadata_db_path,
            grey_source_ids=grey_ids or None,
            include_project_grey=True,
        )
        return answer.to_dict()
