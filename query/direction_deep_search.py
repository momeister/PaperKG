"""Per-research-direction recursive deep search.

For one ``TaskResearchDirection`` (label/rationale/keywords) this runs a
Tiefenanalyse-style recursive tree:

  - Root question = the direction label (broadened by rationale + keywords).
  - Per node: harvest papers + grey sources for the node's question, then a
    grounded Machbarkeit/Ansätze/Risiken/Fazit synthesis over the harvested
    material. If the node is not a leaf (``current_depth < max_depth``), the
    question is decomposed into ``branches`` sub-questions and each is
    explored recursively.
  - After the tree, a root synthesis combines all per-node answers into one
    grounded Gesamtantwort.

The per-node harvest attaches papers to ``project_id`` (or
``target_project_id`` when the task lives in the global ``__all_papers__``
mode) so they show up in the project library and the KG sees them.

Everything is streamed as SSE ``data: <json>\\n\\n`` lines with a ``status``
field for state transitions. The shape is backward compatible with the
previous linear runner (``planning``/``harvesting_papers``/``ingesting``/…)
and adds three new event types for the live progress popup:

  - ``node_running``  — a new tree node started (depth, question, path)
  - ``node_done``     — a node finished (answer + harvested ids)
  - ``sub_questions`` — the decomposed sub-questions for a non-leaf node
"""

from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any, AsyncIterator

from query.auto_harvester import (
    harvest_for_question,
    harvest_grey_sources_for_question,
)
from query.decompose import (
    _normalize_question,
    decompose_sync,
    dedup_subquestions,
)
from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever


def _sse(data: dict[str, Any]) -> str:
    """Format one SSE ``data:`` line (same contract as research_tree._sse)."""
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


def _build_harvest_query(direction: dict[str, Any], question: str | None = None) -> str:
    """Build the search query for a node.

    For the root node ``question`` is ``None`` and the direction label +
    rationale + keywords are used. For sub-questions the sub-question text
    is appended to the direction keywords so the search stays scoped to the
    direction but targets the sub-question.
    """
    label = str(direction.get("label") or "").strip()
    rationale = str(direction.get("rationale") or "").strip()
    keywords = direction.get("keywords") or []
    if not isinstance(keywords, list):
        keywords = list(keywords)
    keywords = [str(k).strip() for k in keywords if str(k).strip()]

    if question is None:
        parts = [label]
        if rationale:
            parts.append(rationale)
        base = " | ".join(parts)
        if keywords:
            base = f"{base} | {' | '.join(keywords)}"
        return base
    # Sub-question: keep the direction's keyword scope but lead with the sub-question.
    parts = [question]
    if label:
        parts.append(label)
    base = " | ".join(parts)
    if keywords:
        base = f"{base} | {' | '.join(keywords)}"
    return base


class DirectionDeepSearchRunner:
    """Runs a per-direction recursive deep search and streams progress over SSE."""

    def __init__(self, llm_router: Any) -> None:
        self.llm_router = llm_router

    async def stream(
        self,
        direction: dict[str, Any],
        project_id: str | None,
        task_spec: dict[str, Any] | None = None,
        *,
        depth: int = 1,
        branches: int = 3,
        max_papers: int = 20,
        max_web_sources: int = 10,
        provider: str | None = None,
        model: str | None = None,
        creativity_level: int | None = None,
        metadata_db_path: str = "data/metadata.duckdb",
        pdf_base_dir: str = "data/pdfs",
        projects_path: str = "data/projects.json",
        target_project_id: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream the recursive deep search for one research direction.

        Yields SSE lines (``data: {json}\\n\\n``). Event payloads carry a
        ``status`` field. Phases (backward compatible + new tree events):

          - ``planning``         — building the root harvest query
          - ``harvesting_papers`` — start of paper harvest (per node)
          - ``search_complete``  — paper search returned N candidates
          - ``ingesting``        — one paper is being downloaded/extracted
          - ``ingested`` / ``ingest_failed`` — per-paper outcome
          - ``harvesting_grey``  — start of web-source harvest (per node)
          - ``grey_search_complete`` — web search returned N hits
          - ``fetched``          — one grey source saved
          - ``synthesizing``     — per-node synthesis running
          - ``node_running``     — (new) a tree node started
          - ``node_done``        — (new) a tree node finished
          - ``sub_questions``    — (new) decomposed sub-questions for a node
          - ``done``             — final root summary attached
          - ``error``            — unrecoverable failure
        """
        label = str(direction.get("label") or "").strip()
        if not label:
            yield _sse({"status": "error", "error": "Direction label is required."})
            return

        # Effective project for harvest attach: prefer the explicit target override
        # (lets a __all_papers__ task attach papers to a real project), else the
        # task's own project_id.
        attach_project_id = target_project_id or project_id

        root_query = _build_harvest_query(direction)
        yield _sse(
            {
                "status": "planning",
                "direction": {
                    "label": label,
                    "rationale": str(direction.get("rationale") or "").strip(),
                    "keywords": direction.get("keywords") or [],
                },
                "query": root_query,
                "depth": depth,
                "branches": branches,
            }
        )

        # Shared collection accumulators across the whole tree.
        all_paper_ids: list[str] = []
        all_grey_ids: list[str] = []
        all_node_answers: list[dict[str, Any]] = []
        seen_questions: set[str] = {_normalize_question(root_query)}

        try:
            async for event in self._node(
                direction=direction,
                question=root_query,
                parent_id=None,
                current_depth=0,
                max_depth=max(0, depth - 1),
                branches=branches,
                task_spec=task_spec,
                attach_project_id=attach_project_id,
                max_papers=max_papers,
                max_web_sources=max_web_sources,
                provider=provider,
                model=model,
                creativity_level=creativity_level,
                metadata_db_path=metadata_db_path,
                pdf_base_dir=pdf_base_dir,
                projects_path=projects_path,
                all_paper_ids=all_paper_ids,
                all_grey_ids=all_grey_ids,
                all_node_answers=all_node_answers,
                seen_questions=seen_questions,
                path=[label],
            ):
                yield event
        except Exception as exc:  # noqa: BLE001 — surface to client
            yield _sse({"status": "error", "phase": "tree", "error": str(exc)})
            return

        # ----- Root synthesis over the accumulated node answers + sources -----
        yield _sse(
            {
                "status": "synthesizing",
                "papers": len(all_paper_ids),
                "grey_sources": len(all_grey_ids),
                "phase": "root_synthesis",
            }
        )

        try:
            summary = await self._root_synthesis(
                direction=direction,
                task_spec=task_spec,
                paper_ids=all_paper_ids,
                grey_ids=all_grey_ids,
                node_answers=all_node_answers,
                project_id=attach_project_id,
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
                "papers_count": len(all_paper_ids),
                "grey_count": len(all_grey_ids),
                "paper_ids": all_paper_ids,
                "grey_ids": all_grey_ids,
                "direction": direction,
                "node_count": len(all_node_answers),
            }
        )

    async def _node(
        self,
        *,
        direction: dict[str, Any],
        question: str,
        parent_id: str | None,
        current_depth: int,
        max_depth: int,
        branches: int,
        task_spec: dict[str, Any] | None,
        attach_project_id: str | None,
        max_papers: int,
        max_web_sources: int,
        provider: str | None,
        model: str | None,
        creativity_level: int | None,
        metadata_db_path: str,
        pdf_base_dir: str,
        projects_path: str,
        all_paper_ids: list[str],
        all_grey_ids: list[str],
        all_node_answers: list[dict[str, Any]],
        seen_questions: set[str],
        path: list[str],
    ) -> AsyncIterator[str]:
        """Recursively explore one node: harvest → synthesize → decompose → recurse."""
        node_id = str(uuid.uuid4())
        is_root = current_depth == 0
        # The root question is the harvest query; sub-questions are the decomposed text.
        harvest_query = (
            question if is_root else _build_harvest_query(direction, question)
        )

        yield _sse(
            {
                "status": "node_running",
                "id": node_id,
                "parent_id": parent_id,
                "depth": current_depth,
                "question": (
                    question if not is_root else direction.get("label", question)
                ),
                "path": path,
            }
        )

        # ----- Phase 1: paper harvest -------------------------------------
        yield _sse(
            {
                "status": "harvesting_papers",
                "node_id": node_id,
                "depth": current_depth,
                "query": harvest_query,
            }
        )
        harvested_papers: list[dict[str, Any]] = []
        paper_queue: asyncio.Queue = asyncio.Queue()

        def _paper_progress(payload: dict[str, Any]) -> None:
            paper_queue.put_nowait(payload)

        producer = asyncio.create_task(
            harvest_for_question(
                question=harvest_query,
                project_id=attach_project_id,
                db_path=metadata_db_path,
                pdf_base_dir=pdf_base_dir,
                projects_path=projects_path,
                max_papers=max_papers,
                llm_router=self.llm_router,
                provider=provider,
                model=model,
                progress_callback=_paper_progress,
                target_project_id=attach_project_id,
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
                            "node_id": node_id,
                            "found": payload.get("found", 0),
                        }
                    )
                elif phase == "ingesting":
                    yield _sse(
                        {
                            "status": "ingesting",
                            "node_id": node_id,
                            "paper": payload.get("paper"),
                        }
                    )
                elif phase == "ingested":
                    yield _sse(
                        {
                            "status": "ingested",
                            "node_id": node_id,
                            "paper": payload.get("paper"),
                        }
                    )
                elif phase == "ingest_failed":
                    yield _sse(
                        {
                            "status": "ingest_failed",
                            "node_id": node_id,
                            "paper": payload.get("paper"),
                        }
                    )
            harvested_papers = producer.result()
            yield _sse(
                {
                    "status": "papers_harvested",
                    "node_id": node_id,
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
                {
                    "status": "harvest_error",
                    "phase": "papers",
                    "node_id": node_id,
                    "error": str(exc),
                }
            )

        node_paper_ids = [str(p["id"]) for p in harvested_papers if p.get("id")]
        for pid in node_paper_ids:
            if pid not in all_paper_ids:
                all_paper_ids.append(pid)

        # ----- Phase 2: grey-source / web harvest -------------------------
        yield _sse(
            {
                "status": "harvesting_grey",
                "node_id": node_id,
                "query": harvest_query,
            }
        )
        grey_queue: asyncio.Queue = asyncio.Queue()

        def _grey_progress(payload: dict[str, Any]) -> None:
            grey_queue.put_nowait(payload)

        grey_producer = asyncio.create_task(
            harvest_grey_sources_for_question(
                question=harvest_query,
                project_id=attach_project_id,
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
                            "node_id": node_id,
                            "found": payload.get("found", 0),
                        }
                    )
                elif phase == "fetched":
                    yield _sse(
                        {
                            "status": "fetched",
                            "node_id": node_id,
                            "source": payload.get("source"),
                        }
                    )
            grey_sources = grey_producer.result()
            yield _sse(
                {
                    "status": "grey_harvested",
                    "node_id": node_id,
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
            yield _sse(
                {
                    "status": "harvest_error",
                    "phase": "grey",
                    "node_id": node_id,
                    "error": str(exc),
                }
            )

        node_grey_ids = [str(s["id"]) for s in grey_sources if s.get("id")]
        for gid in node_grey_ids:
            if gid not in all_grey_ids:
                all_grey_ids.append(gid)

        # ----- Phase 3: grounded per-node synthesis ----------------------
        yield _sse(
            {
                "status": "synthesizing",
                "node_id": node_id,
                "papers": len(node_paper_ids),
                "grey_sources": len(node_grey_ids),
                "phase": "node_synthesis",
            }
        )

        try:
            node_answer = await self._synthesize_node(
                direction=direction,
                task_spec=task_spec,
                question=question if not is_root else None,
                paper_ids=node_paper_ids,
                grey_ids=node_grey_ids,
                project_id=attach_project_id,
                provider=provider,
                model=model,
                creativity_level=creativity_level,
                metadata_db_path=metadata_db_path,
            )
        except Exception as exc:  # noqa: BLE001
            yield _sse(
                {
                    "status": "error",
                    "phase": "node_synthesis",
                    "node_id": node_id,
                    "error": str(exc),
                }
            )
            node_answer = None

        all_node_answers.append(
            {
                "id": node_id,
                "parent_id": parent_id,
                "depth": current_depth,
                "question": question,
                "answer": node_answer,
                "paper_ids": node_paper_ids,
                "grey_ids": node_grey_ids,
                "path": path,
            }
        )

        yield _sse(
            {
                "status": "node_done",
                "id": node_id,
                "parent_id": parent_id,
                "depth": current_depth,
                "question": (
                    question if not is_root else direction.get("label", question)
                ),
                "path": path,
                "answer": node_answer,
                "papers_count": len(node_paper_ids),
                "grey_count": len(node_grey_ids),
            }
        )

        # ----- Phase 4: decompose + recurse -------------------------------
        sub_questions: list[str] = []
        if current_depth < max_depth:
            try:
                raw_sub_questions = await asyncio.to_thread(
                    decompose_sync,
                    self.llm_router,
                    question,
                    branches,
                    provider,
                    model,
                )
                sub_questions = dedup_subquestions(raw_sub_questions, seen_questions)
            except Exception:  # noqa: BLE001 — stay flat on decomposition failure
                sub_questions = []

        if sub_questions:
            yield _sse(
                {
                    "status": "sub_questions",
                    "node_id": node_id,
                    "depth": current_depth,
                    "questions": sub_questions,
                    "path": path,
                }
            )

        for sub_q in sub_questions:
            async for event in self._node(
                direction=direction,
                question=sub_q,
                parent_id=node_id,
                current_depth=current_depth + 1,
                max_depth=max_depth,
                branches=branches,
                task_spec=task_spec,
                attach_project_id=attach_project_id,
                max_papers=max_papers,
                max_web_sources=max_web_sources,
                provider=provider,
                model=model,
                creativity_level=creativity_level,
                metadata_db_path=metadata_db_path,
                pdf_base_dir=pdf_base_dir,
                projects_path=projects_path,
                all_paper_ids=all_paper_ids,
                all_grey_ids=all_grey_ids,
                all_node_answers=all_node_answers,
                seen_questions=seen_questions,
                path=path + [sub_q],
            ):
                yield event

    async def _synthesize_node(
        self,
        *,
        direction: dict[str, Any],
        task_spec: dict[str, Any] | None,
        question: str | None,
        paper_ids: list[str],
        grey_ids: list[str],
        project_id: str | None,
        provider: str | None,
        model: str | None,
        creativity_level: int | None,
        metadata_db_path: str,
    ) -> dict[str, Any]:
        """Grounded Machbarkeit/Ansätze/Risiken summary for one node."""
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

        question_block = ""
        if question:
            question_block = f"Teilfrage: {question}\n\n"

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
            f"{question_block}"
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

    async def _root_synthesis(
        self,
        *,
        direction: dict[str, Any],
        task_spec: dict[str, Any] | None,
        paper_ids: list[str],
        grey_ids: list[str],
        node_answers: list[dict[str, Any]],
        project_id: str | None,
        provider: str | None,
        model: str | None,
        creativity_level: int | None,
        metadata_db_path: str,
    ) -> dict[str, Any]:
        """Combine per-node answers into one grounded Gesamtantwort.

        Falls back to a single node's synthesis when only one node exists (the
        common ``depth=1`` case) so we don't double-spend an LLM call.
        """
        answered = [n for n in node_answers if n.get("answer")]
        if len(answered) == 1:
            return answered[0]["answer"]

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

        node_digest = "\n\n".join(
            f"### Teilfrage {i + 1}: {n.get('question') or label}\n"
            f"{(n.get('answer') or {}).get('answer') or ''}"
            for i, n in enumerate(answered)
        )

        prompt = (
            f"{task_block}"
            f"Forschungsrichtung: {label}\n"
            f"Rationale: {rationale}\n"
            f"Keywords: {', '.join(keywords) if keywords else '—'}\n\n"
            f"Im Folgenden sind Antworten auf {len(answered)} Teilfragen dieser Richtung:\n\n"
            f"{node_digest}\n\n"
            "Fasse diese Teilantworten zu einer Gesamtantwort für die Forschungsrichtung "
            "zusammen. Gliedere deine Antwort in genau vier Markdown-Abschnitte:\n"
            "## Machbarkeit\n"
            "## Ansätze\n"
            "## Risiken\n"
            "## Fazit\n\n"
            f"{creativity_hint}"
            "Belege alle Aussagen mit Paper-IDs in eckigen Klammern "
            "(z. B. [arxiv:1234.5678]) und Web-Quellen (z. B. [grey::task_abc]). "
            "Übernimm die Zitierungen aus den Teilantworten exakt, erfinde keine. "
            "Schreibe verständlich und konkret."
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
