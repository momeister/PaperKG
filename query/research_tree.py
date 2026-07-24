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

# Shared citation contract for every synthesis LLM call: keep the source IDs from
# the per-node answers verbatim, never invent new ones, write in German.
_CITE_INSTR = (
    "Übernimm ALLE Quellenangaben ([arxiv:...], [grey::...]) "
    "EXAKT und VOLLSTÄNDIG aus den Antworten. Erfinde KEINE neuen Zitierungen. "
    "Zitiere jede einzelne Behauptung. Antworte auf Deutsch."
)

# Upper bound on the number of depth-2 subsection LLM calls in one synthesis, so a
# very wide/deep tree cannot explode into hundreds of generation calls. Chapters past
# the budget are rendered as a single expanded chapter instead of per-subsection.
_MAX_SYNTH_SUBSECTIONS = 60


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


def _normalize_question(question: str) -> str:
    """Normalize a question for duplicate detection.

    Lowercases, collapses whitespace and strips surrounding punctuation so that
    re-phrasings that differ only in casing/spacing/trailing '?' collapse to the
    same key. Kept deliberately conservative to avoid merging genuinely distinct
    sub-questions.
    """
    norm = re.sub(r"\s+", " ", str(question or "")).strip().lower()
    return norm.strip(" .,;:!?“”«»'\"")


def _heading_level(line: str) -> int:
    """Markdown heading level of a line (1-6), or 0 if the line is not a heading."""
    m = re.match(r"^(#{1,6})\s+\S", line)
    return len(m.group(1)) if m else 0


# Appended to synthesis system prompts so the model does not restate the section title we
# already inject as its own heading (the cause of the duplicate-looking ToC entries).
_NO_HEADING_HINT = (
    " Beginne NICHT mit einer Überschrift und füge KEINE eigenen Kapitel-/Abschnitts"
    "überschriften (## oder ###) ein — die Abschnittsüberschrift wird automatisch gesetzt."
)
_SUBSECTION_HEADING_HINT = (
    " Wiederhole NICHT den Kapiteltitel als Überschrift; nutze `###` ausschließlich für die "
    "geforderten Unterabschnitte."
)


def _normalize_synthesis_body(body: str, heading: str | None, keep_subsections: bool) -> str:
    """Tidy an LLM-generated synthesis fragment so it nests cleanly under its injected heading.

    1. Drop a leading heading that merely restates the title we already inject (the cause of
       the duplicate question/statement pairs in the ToC): the first non-empty line is removed
       when it is a heading at the same or a higher level than ``heading``.
    2. Demote stray in-body headings so they never collide with the chapter/section ToC nor
       leak as literal ``####``: normal fragments cap at ``####`` (h4); the flat-chapter
       fragment keeps ``###`` for its requested Unterabschnitte and pulls ``##``/``####+`` onto
       that level.
    """
    if not body:
        return ""
    our_level = _heading_level(heading or "") or 2
    lines = body.split("\n")
    # 1) strip a leading restated heading (after any blank lines)
    i = 0
    while i < len(lines) and not lines[i].strip():
        i += 1
    if i < len(lines) and 0 < _heading_level(lines[i]) <= our_level:
        del lines[i]
        while i < len(lines) and not lines[i].strip():
            del lines[i]
    # 2) demote remaining headings to a safe level
    target = "###" if keep_subsections else "####"
    out: list[str] = []
    for line in lines:
        if _heading_level(line):
            text = re.sub(r"^#{1,6}\s+", "", line)
            out.append(f"{target} {text}")
        else:
            out.append(line)
    return "\n".join(out).strip()


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

    @staticmethod
    def _dedup_subquestions(
        sub_questions: list[str], seen_questions: set[str] | None
    ) -> list[str]:
        """Keep only sub-questions not already asked, recording survivors in ``seen_questions``.

        Prevents the same question from being branched/searched twice — both across
        sibling branches in one run and when resuming a previous run.
        """
        if seen_questions is None:
            return sub_questions
        unique: list[str] = []
        for sub_q in sub_questions:
            key = _normalize_question(sub_q)
            if not key or key in seen_questions:
                continue
            seen_questions.add(key)
            unique.append(sub_q)
        return unique

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

    @staticmethod
    def _synthesis_known_ids(nodes: list[dict[str, Any]]) -> frozenset[str]:
        """Paper IDs actually cited by the node answers — the only citations the
        synthesis is allowed to keep (see ``_strip_unknown_citations``)."""
        return frozenset(
            str(s.get("paper_id") or "")
            for n in nodes
            for s in (n["answer"].get("sources") or [])
            if s.get("paper_id")
        )

    def _synthesis_steps(
        self, nodes: list[dict[str, Any]], root_question: str
    ) -> list[dict[str, Any]]:
        """Plan the synthesis as an ordered list of LLM steps (no LLM calls here).

        The document length now scales with the *depth and breadth* of the research
        tree instead of collapsing to one chapter per top-level branch:

        - Einleitung
        - per depth-1 node → a ``## Kapitel`` lead-in, then one expanded
          ``### Unterabschnitt`` per depth-2 child (with that child's depth-3+
          descendants folded in as context); a chapter with no depth-2 children is
          expanded as a single longer chapter instead.
        - Fazit

        Each step is rendered by its own LLM call (``_render_step``) so citations
        from the individual node answers are preserved verbatim.
        """
        if not nodes:
            return []

        root_answer = next(
            (n["answer"].get("answer", "") for n in nodes if n.get("depth") == 0), ""
        )
        depth1_nodes = [n for n in nodes if n.get("depth") == 1]

        # Reconstruct parent→children topology from id/parent_id (new caches); fall
        # back to chapter_question/depth grouping for older saved runs without ids.
        by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
        has_ids = any(n.get("id") for n in nodes)
        for n in nodes:
            pid = n.get("parent_id")
            if pid:
                by_parent[str(pid)].append(n)

        def _children(node: dict[str, Any]) -> list[dict[str, Any]]:
            depth = int(node.get("depth", 0))
            if has_ids and node.get("id"):
                return [
                    c for c in by_parent.get(str(node["id"]), [])
                    if int(c.get("depth", 0)) == depth + 1
                ]
            cq = node.get("question")
            return [
                n for n in nodes
                if int(n.get("depth", 0)) == depth + 1
                and (n.get("chapter_question") or n.get("question")) == cq
            ]

        def _descendants(node: dict[str, Any]) -> list[dict[str, Any]]:
            out: list[dict[str, Any]] = []
            for child in _children(node):
                out.append(child)
                out.extend(_descendants(child))
            return out

        def _dedup(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
            seen: set[str] = set()
            uniq: list[dict[str, Any]] = []
            for it in items:
                key = _normalize_question(it.get("question", ""))
                if key and key not in seen:
                    seen.add(key)
                    uniq.append(it)
            return uniq

        author_sys = f"Du bist ein wissenschaftlicher Autor. {_CITE_INSTR}{_NO_HEADING_HINT}"
        chapter_titles = "; ".join(d1["question"] for d1 in depth1_nodes)
        # Scale the write-up to the breadth/depth of the tree: a 50-answer analysis
        # should read like a long Deep-Research report, not a one-pager. The counts are
        # injected into every prompt so the model writes proportionally to the material.
        total_answers = len(nodes)
        scope_note = (
            f"Diese Tiefenanalyse beruht auf {total_answers} untersuchten Teilfragen über "
            f"{len(depth1_nodes)} Themenkapitel. Schöpfe das vorhandene Material voll aus "
            "und schreibe entsprechend ausführlich und detailliert."
        )
        steps: list[dict[str, Any]] = []

        # Einleitung
        steps.append({
            "heading": "## Einleitung",
            "system": author_sys,
            "user": (
                f"Hauptforschungsfrage: {root_question}\n\n"
                f"{scope_note}\n\n"
                f"Überblick:\n{root_answer}\n\n"
                f"Geplante Kapitel: {chapter_titles}\n\n"
                "Schreibe eine ausführliche wissenschaftliche Einleitung (500-700 Wörter), "
                "die die Forschungsfrage motiviert, den wissenschaftlichen Kontext und die "
                "Relevanz erläutert, zentrale Begriffe einordnet und den Aufbau der Arbeit "
                "entlang der geplanten Kapitel beschreibt."
            ),
            "max_tokens": 1600,
        })

        # Chapters
        budget = _MAX_SYNTH_SUBSECTIONS
        for d1 in depth1_nodes:
            cq = d1["question"]
            d1_ans = d1["answer"].get("answer", "")
            subs = _dedup(_children(d1))

            if subs and budget > 0:
                sub_titles = "; ".join(s["question"] for s in subs)
                steps.append({
                    "heading": f"## {cq}",
                    "system": author_sys,
                    "user": (
                        f"Kapitelthema: {cq}\n\n"
                        f"Kernbefund des Kapitels:\n{d1_ans}\n\n"
                        f"Dieses Kapitel gliedert sich in folgende {len(subs)} Unterabschnitte: {sub_titles}\n\n"
                        "Schreibe eine einordnende Hinführung zu diesem Kapitel "
                        "(200-350 Wörter), die das Thema rahmt und die folgenden Unterabschnitte "
                        "ankündigt. Belege Aussagen mit den vorhandenen Quellenangaben."
                    ),
                    "max_tokens": 900,
                    "fallback": d1_ans,
                })
                for s in subs:
                    if budget <= 0:
                        break
                    budget -= 1
                    deeper = _descendants(s)
                    deeper_block = ""
                    if deeper:
                        deeper_block = (
                            f"\n\nVertiefende Befunde ({len(deeper)} Aspekte, in diesen "
                            "Unterabschnitt integrieren):\n"
                            + "\n\n".join(
                                f"Aspekt: {d['question']}\nBefund: {d['answer'].get('answer', '')}"
                                for d in deeper
                            )
                        )
                    steps.append({
                        "heading": f"### {s['question']}",
                        "system": (
                            "Du bist ein wissenschaftlicher Autor. Schreibe einen ausführlichen "
                            f"Unterabschnitt einer Bachelorarbeit. {_CITE_INSTR}{_NO_HEADING_HINT}"
                        ),
                        "user": (
                            f"Übergeordnetes Kapitel: {cq}\n"
                            f"Unterabschnittsthema: {s['question']}\n\n"
                            f"Hauptantwort:\n{s['answer'].get('answer', '')}"
                            f"{deeper_block}\n\n"
                            "Schreibe einen vollständigen, detaillierten Unterabschnitt "
                            "(900-1400 Wörter) mit präziser wissenschaftlicher Sprache. Arbeite "
                            "Mechanismen, Belege, Differenzierungen und ggf. Gegenpositionen heraus. "
                            "Integriere alle vertiefenden Befunde vollständig. "
                            "Belege JEDE Aussage mit den vorhandenen Quellenangaben."
                        ),
                        "max_tokens": 3500,
                        "fallback": s["answer"].get("answer", ""),
                    })
            else:
                # No depth-2 children (or budget exhausted): one longer chapter that
                # spells out each deeper finding as its own ### subsection, so the ToC
                # gains entries instead of collapsing to a single flat chapter.
                deeper = _descendants(d1)
                deeper_block = ""
                sub_hint = ""
                if deeper:
                    deeper_block = (
                        f"\n\nTiefere Analysen zu diesem Kapitel ({len(deeper)} Aspekte):\n"
                        + "\n\n".join(
                            f"Unterfrage: {d['question']}\nBefund: {d['answer'].get('answer', '')}"
                            for d in deeper
                        )
                    )
                    sub_hint = (
                        f" Gliedere das Kapitel in je einen `### Unterabschnitt` pro der "
                        f"{len(deeper)} oben genannten Unterfragen und arbeite jeden Befund "
                        "ausführlich aus."
                    )
                steps.append({
                    "heading": f"## {cq}",
                    "keep_subsections": True,
                    "system": (
                        "Du bist ein wissenschaftlicher Autor. Schreibe ein vollständiges Kapitel "
                        f"einer Bachelorarbeit. {_CITE_INSTR}{_SUBSECTION_HEADING_HINT}"
                    ),
                    "user": (
                        f"Kapitelthema: {cq}\n\n"
                        f"Hauptantwort:\n{d1_ans}"
                        f"{deeper_block}\n\n"
                        "Schreibe ein vollständiges, detailliertes Kapitel (1100-1600 Wörter)"
                        f"{sub_hint or ' mit ### Unterabschnitten wo sinnvoll.'} Belege JEDE Aussage mit "
                        "Quellenangaben. Verwende einen wissenschaftlichen, präzisen Schreibstil."
                    ),
                    "max_tokens": 3800,
                    "fallback": d1_ans,
                })

        # Fazit
        steps.append({
            "heading": "## Fazit",
            "system": author_sys,
            "user": (
                f"Hauptforschungsfrage: {root_question}\n\n"
                f"{scope_note}\n\n"
                f"Untersuchte Aspekte: {chapter_titles}\n\n"
                "Schreibe ein ausführliches wissenschaftliches Fazit (450-650 Wörter), das die "
                "wichtigsten Erkenntnisse über alle Kapitel hinweg zusammenführt, die "
                "Hauptforschungsfrage explizit beantwortet, Implikationen ableitet und offene "
                "Forschungsfragen benennt."
            ),
            "max_tokens": 1500,
        })
        return steps

    def _render_step(
        self,
        step: dict[str, Any],
        known_ids: frozenset[str],
        provider: str | None,
        model: str | None,
    ) -> str:
        """Run one synthesis step's LLM call and return its formatted markdown fragment."""
        if self.llm_router is None:
            return ""
        ov: dict[str, Any] = {"max_tokens": int(step.get("max_tokens", 2000)), "temperature": 0.3}
        if model:
            ov["model"] = model
        try:
            body = str(self.llm_router.chat(
                messages=[
                    {"role": "system", "content": step["system"]},
                    {"role": "user", "content": step["user"]},
                ],
                provider=provider,
                overrides=ov,
            ) or "").strip()
        except Exception:
            body = ""
        heading = step.get("heading")
        keep_subs = bool(step.get("keep_subsections"))
        body = _normalize_synthesis_body(_strip_unknown_citations(body, known_ids), heading, keep_subs)
        if not body:
            # The synthesis call came back empty: fall back to the node's own answer so an
            # already-answered question never renders as a heading with no content.
            fallback = _strip_unknown_citations(str(step.get("fallback") or "").strip(), known_ids)
            body = _normalize_synthesis_body(fallback, heading, keep_subs)
        if not body:
            return ""
        return f"{heading}\n\n{body}" if heading else body

    def _synthesize_sync(
        self,
        nodes: list[dict[str, Any]],
        root_question: str,
        provider: str | None,
        model: str | None,
    ) -> str:
        """Generate the full thesis-style document (non-streaming convenience).

        Builds the section plan and renders each section with its own LLM call. The
        streaming path in :meth:`stream_events` renders the same steps incrementally.
        """
        if not nodes or self.llm_router is None:
            return ""
        known_ids = self._synthesis_known_ids(nodes)
        parts: list[str] = []
        for step in self._synthesis_steps(nodes, root_question):
            frag = self._render_step(step, known_ids, provider, model)
            if frag:
                parts.append(frag)
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
        initial_nodes: list[dict[str, Any]] | None = None,
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

        # Pre-populate cache from a previous paused run so completed nodes are re-emitted
        # without calling the LLM again (resume / quota-saving). Also reconstruct the saved
        # parent→child topology and the set of questions already asked, so resume replays
        # the existing tree deterministically instead of re-decomposing (and re-searching) it.
        done_by_question: dict[str, dict[str, Any]] = {}
        seen_questions: set[str] = {_normalize_question(question)}
        id_to_question: dict[str, str] = {}
        children_by_parent_q: dict[str, list[str]] = defaultdict(list)
        saved_nodes = [
            n for n in (initial_nodes or [])
            if n.get("question") and n.get("status") not in ("synthesis", "llm_error")
        ]
        for saved in saved_nodes:
            q = str(saved["question"])
            seen_questions.add(_normalize_question(q))
            if saved.get("id"):
                id_to_question[str(saved["id"])] = q
            if saved.get("status") == "done" and saved.get("answer"):
                done_by_question[_normalize_question(q)] = saved
                nodes_cache.append(saved)
                nodes_done[0] += 1
        for saved in saved_nodes:
            pid = saved.get("parent_id")
            if pid and str(pid) in id_to_question:
                parent_key = _normalize_question(id_to_question[str(pid)])
                children_by_parent_q[parent_key].append(str(saved["question"]))

        async for event in self._node(
            question, None, 0, depth, branches, kwargs, nodes_cache,
            chapter_question=None, nodes_done=nodes_done, max_nodes=max_nodes,
            done_by_question=done_by_question, seen_questions=seen_questions,
            children_by_parent_q=children_by_parent_q,
        ):
            yield event
        if nodes_cache:
            try:
                # Render the synthesis section-by-section and re-emit the synthesis
                # node (constant id) with the growing document, so the Gesamtantwort
                # visibly builds up instead of appearing only after a long wait.
                steps = self._synthesis_steps(nodes_cache, question)
                known_ids = self._synthesis_known_ids(nodes_cache)
                rendered: list[str] = []
                for step in steps:
                    frag = await asyncio.to_thread(
                        self._render_step, step, known_ids, provider, model
                    )
                    if not frag:
                        continue
                    rendered.append(frag)
                    yield _sse({"id": "synthesis", "parent_id": None, "question": question,
                                "depth": 0, "status": "synthesis", "answer": None,
                                "document": "\n\n".join(rendered), "child_count": 0})
                if not rendered:
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
        done_by_question: dict[str, dict[str, Any]] | None = None,
        seen_questions: set[str] | None = None,
        children_by_parent_q: dict[str, list[str]] | None = None,
    ) -> AsyncIterator[str]:
        # Global node budget — stop spawning when exhausted
        if nodes_done is not None:
            if nodes_done[0] >= max_nodes:
                return
            nodes_done[0] += 1

        # Resume: if this question was already answered in a previous run, re-emit the
        # cached result immediately without calling the LLM for the answer again.
        cached = (done_by_question or {}).get(_normalize_question(question))
        if cached:
            node_id = str(cached.get("id") or uuid.uuid4())
            yield _sse({**cached, "parent_id": parent_id, "depth": current_depth})
            # Replay the saved subtree verbatim so resume is deterministic and never
            # re-asks an answered branch. Only decompose afresh for nodes that were
            # genuine leaves when the run was paused (no saved children).
            saved_children = list((children_by_parent_q or {}).get(_normalize_question(question), []))
            if not saved_children and current_depth < max_depth:
                sub_questions: list[str] = []
                try:
                    sub_questions = await asyncio.to_thread(
                        self._decompose_sync,
                        question,
                        branches,
                        kwargs.get("provider"),
                        kwargs.get("model"),
                    )
                except Exception:
                    sub_questions = []
                saved_children = self._dedup_subquestions(sub_questions, seen_questions)
            for sub_q in saved_children:
                child_chapter = sub_q if current_depth == 0 else chapter_question
                async for event in self._node(
                    sub_q, node_id, current_depth + 1, max_depth, branches, kwargs,
                    nodes_cache, child_chapter, nodes_done, max_nodes, done_by_question,
                    seen_questions, children_by_parent_q,
                ):
                    yield event
            return

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
            and (
                answer_dict.get("no_answer")
                or answer_dict.get("context_diagnostics", {}).get("low_relevance")
                or not _answer_has_citations(answer_dict)
            )
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
                    llm_router=self.llm_router,
                    provider=kwargs.get("provider"),
                    model=kwargs.get("model"),
                )
                new_paper_ids = [r["id"] for r in paper_records]
                harvested_paper_info = [
                    {"id": r["id"], "title": r.get("title", r["id"])} for r in paper_records
                ]

                # Harvest grey (web) sources. Wie in query/auto_answer.py gilt die
                # Rangfolge Paper → vertrauenswürdige Domains → ungeprüftes Web; hier
                # ohne Zwischenantwort pro Stufe (ein Baum hat viele Knoten), also:
                # ungeprüfte Treffer nur, wenn die vertrauenswürdigen nichts hergaben.
                grey_db_path = str(kwargs.get("metadata_db_path") or "data/metadata.duckdb")
                grey_project_id = str(kwargs.get("project_id") or "")
                grey_records = await harvest_grey_sources_for_question(
                    question=question,
                    project_id=grey_project_id,
                    db_path=grey_db_path,
                    max_sources=2,
                    tiers=("trusted",),
                )
                if not grey_records:
                    grey_records = await harvest_grey_sources_for_question(
                        question=question,
                        project_id=grey_project_id,
                        db_path=grey_db_path,
                        max_sources=2,
                        tiers=("unknown",),
                    )
                new_grey_ids = [r["id"] for r in grey_records]
                harvested_grey_info = [
                    {
                        "id": r.get("id", ""),
                        "title": r.get("title", ""),
                        "url": r.get("url", ""),
                        "trust_tier": r.get("trust_tier", "unknown"),
                    }
                    for r in grey_records
                ]

                if new_paper_ids or new_grey_ids:
                    original_paper_ids = kwargs.get("paper_ids")
                    existing_ids = [pid for pid in (original_paper_ids or []) if pid != "__none__"]
                    effective_paper_ids = (existing_ids + new_paper_ids) if (existing_ids or new_paper_ids) else None
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
            except Exception as harvest_exc:
                yield _sse({"id": node_id, "parent_id": parent_id, "question": question,
                            "depth": current_depth, "status": "harvest_error",
                            "error": str(harvest_exc)})

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

        # Drop sub-questions already asked elsewhere in this run (or in a resumed run)
        # so the same question is never searched twice.
        sub_questions = self._dedup_subquestions(sub_questions, seen_questions)

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
                "id": node_id,
                "parent_id": parent_id,
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
                nodes_cache, child_chapter, nodes_done, max_nodes, done_by_question,
                seen_questions, children_by_parent_q,
            ):
                yield event


def _strip_unknown_citations(text: str, known_ids: frozenset[str]) -> str:
    """Remove [xxx] citation brackets whose ID is not among the known paper IDs."""
    if not known_ids:
        return text

    def _keep(m: re.Match) -> str:
        # Split combined citations like [grey::abc, arxiv:123] before matching
        parts = [p.strip() for p in re.split(r"[,;]\s*", m.group(1)) if p.strip()]
        for part in parts:
            norm = re.sub(r"v\d+$", "", part.lower().replace(" ", ""))
            for kid in known_ids:
                kid_norm = re.sub(r"v\d+$", "", kid.lower().replace(" ", ""))
                if norm == kid_norm or kid_norm.endswith(norm) or norm.endswith(kid_norm):
                    return m.group(0)
        return ""

    return re.sub(r"\[([^\]]+)\]", _keep, text)


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


def _answer_has_citations(answer_dict: dict[str, Any]) -> bool:
    text = str(answer_dict.get("answer") or "")
    return bool(re.search(r"\[(?:arxiv:|grey::)", text))
