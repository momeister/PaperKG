from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from query.context_budget import decide_whole_context, effective_generation_limits
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import Evidence, SearchHit, Source
from query.llm_router import LLMRouter
from query.source_verifier import (
    APPROX_REGION_CHARS,
    best_excerpt,
    best_excerpts,
    find_pdf_path,
    parse_pdf_text,
    verbatim_excerpt,
    verify_answer_sources,
)


@dataclass
class GroundedAnswer:
    question: str
    answer: str
    sources: list[Source] = field(default_factory=list)
    evidence: list[Evidence] = field(default_factory=list)
    citation_links: list[dict[str, Any]] = field(default_factory=list)
    no_answer: bool = False
    model: str | None = None
    generation_error: str | None = None
    context_diagnostics: dict[str, Any] = field(default_factory=dict)
    source_verification: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "answer": self.answer,
            "sources": [source.to_dict() for source in self.sources],
            "evidence": [item.to_dict() for item in self.evidence],
            "citation_links": self.citation_links,
            "no_answer": self.no_answer,
            "model": self.model,
            "generation_error": self.generation_error,
            "context_diagnostics": self.context_diagnostics,
            "source_verification": self.source_verification,
        }


_BOILERPLATE_RE = re.compile(
    r"^(copyright\b|©|\(c\)\s*\d{4}|all rights reserved|terms of use)",
    re.IGNORECASE,
)


def _is_boilerplate(text: str) -> bool:
    return bool(_BOILERPLATE_RE.match(text.strip()))


class GroundedResponder:
    """
    Answers questions from retrieved KG evidence only.
    """

    MIN_ANSWER_TOKENS = 1200
    MAX_ANSWER_TOKENS = 8192
    MAX_INVALID_CITATION_RETRIES = 1

    SYSTEM_PROMPT = """You are ScienceKG's grounded research assistant.

Use only the evidence provided by the local knowledge graph. Do not add facts
from model training data. If the evidence is insufficient, say that the local
KG does not contain enough evidence. Cite paper IDs in square brackets when
making claims."""

    def __init__(
        self,
        retriever: HybridRetriever | None = None,
        llm_router: LLMRouter | None = None,
    ) -> None:
        self.retriever = retriever or HybridRetriever()
        self.llm_router = llm_router

    def answer(
        self,
        question: str,
        limit: int = 8,
        provider: str | None = None,
        model: str | None = None,
        overrides: dict[str, Any] | None = None,
        conversation_context: list[dict[str, Any]] | None = None,
        paper_ids: list[str] | set[str] | None = None,
        priority_paper_ids: list[str] | set[str] | None = None,
        answer_context_mode: str = "kg",
        pdf_base_dir: str = "data/pdfs",
        inline_context_texts: list[str] | None = None,
        project_id: str | None = None,
        metadata_db_path: str = "data/metadata.duckdb",
        grey_source_ids: list[str] | None = None,
        include_project_grey: bool = False,
    ) -> GroundedAnswer:
        context_diagnostics: dict[str, Any] = {
            "answer_context_mode": answer_context_mode or "kg",
            "project_id": project_id,
        }
        if str(answer_context_mode or "kg").strip().lower() == "pdf_if_fits":
            pdf_answer, pdf_diagnostics = self._answer_from_pdf_context_if_fits(
                question=question,
                provider=provider,
                model=model,
                overrides=overrides,
                conversation_context=conversation_context,
                paper_ids=paper_ids,
                pdf_base_dir=pdf_base_dir,
            )
            context_diagnostics.update(pdf_diagnostics)
            if pdf_answer is not None:
                return pdf_answer

        hits = self.retriever.search(question, limit=limit, paper_ids=paper_ids)
        priority_set = {str(pid) for pid in (priority_paper_ids or []) if pid}
        hits = _prioritize_hits(hits, priority_set)

        # Inject grey sources (saved web research findings) as citable evidence.
        # Two paths: explicitly selected grey sources (grey_source_ids, e.g. "Auswahl" mode)
        # are always injected and rank like papers; project-wide grey sources supplement the
        # answer when no explicit paper filter is set, or when the caller scoped retrieval to
        # the project's papers and asked for them via include_project_grey.
        has_grey = False
        selected_grey = [str(g).strip() for g in (grey_source_ids or []) if str(g or "").strip()]
        inject_project_grey = bool(
            project_id
            and project_id not in ("", "__all_papers__")
            and (include_project_grey or not paper_ids)
        )
        if selected_grey or inject_project_grey:
            try:
                from storage.metadata_db import MetadataDB  # local import to avoid circular deps
                grey_records: list[tuple[dict[str, Any], bool]] = []
                seen_grey_ids: set[str] = set()
                with MetadataDB(metadata_db_path) as _db:
                    for grey_id in selected_grey:
                        record = _db.get_grey_source(grey_id)
                        if record and str(record.get("id")) not in seen_grey_ids:
                            seen_grey_ids.add(str(record.get("id")))
                            grey_records.append((record, True))
                    if inject_project_grey:
                        for record in _db.list_grey_sources(str(project_id)):
                            if str(record.get("id")) not in seen_grey_ids:
                                seen_grey_ids.add(str(record.get("id")))
                                grey_records.append((record, False))
                for grey, selected in grey_records:
                    if grey.get("injection_flags"):
                        continue  # skip quarantined content
                    cited_id = f"grey::{grey['id']}"
                    grey_metadata = {"source_type": "grey", "url": grey.get("url", "")}
                    quote_score, snippet_score, summary_score = (
                        (5.5, 5.0, 4.0) if selected else (3.0, 2.5, 2.0)
                    )
                    grey_source = Source(
                        paper_id=cited_id,
                        title=grey.get("title") or grey.get("url", ""),
                        year=None,
                        doi=None,
                        url=grey.get("url"),
                    )
                    grey_hit = SearchHit(source=grey_source)
                    for quote in (grey.get("evidence") or []):
                        if not quote or _is_boilerplate(str(quote)):
                            continue
                        grey_hit.add_evidence(Evidence(
                            paper_id=cited_id,
                            kind="quote",
                            field="evidence",
                            text=str(quote),
                            score=quote_score,
                            metadata=dict(grey_metadata),
                        ))
                    full_text = str(grey.get("full_text") or "").strip()
                    if full_text:
                        snippet = _best_pdf_context_snippet(full_text, question)
                        if snippet and not _is_boilerplate(snippet):
                            grey_hit.add_evidence(Evidence(
                                paper_id=cited_id,
                                kind="quote",
                                field="full_text",
                                text=snippet,
                                score=snippet_score,
                                metadata=dict(grey_metadata),
                            ))
                    if not grey_hit.evidence and grey.get("summary"):
                        grey_hit.add_evidence(Evidence(
                            paper_id=cited_id,
                            kind="summary",
                            field="summary",
                            text=grey["summary"],
                            score=summary_score,
                            metadata=dict(grey_metadata),
                        ))
                    if grey_hit.evidence:
                        hits.append(grey_hit)
                        has_grey = True
                if has_grey:
                    context_diagnostics["grey_source_count"] = sum(
                        1 for h in hits if h.source.paper_id.startswith("grey::")
                    )
                    if selected_grey:
                        context_diagnostics["selected_grey_count"] = len(selected_grey)
            except Exception:
                pass  # never fail the answer because of grey source fetch

        evidence = self._evidence_for_answer(hits, max_items=_evidence_item_limit(limit, hits), priority_paper_ids=priority_set)
        # Web sources supplement papers (recency!): make sure ranking/caps never push every
        # grey item out of the evidence the LLM actually sees.
        if has_grey and not any(item.paper_id.startswith("grey::") for item in evidence):
            grey_pool = [
                item
                for hit in hits
                if hit.source.paper_id.startswith("grey::")
                for item in hit.evidence
            ]
            grey_pool.sort(key=lambda item: item.score, reverse=True)
            evidence.extend(grey_pool[:2])
        sources = [hit.source for hit in hits if hit.evidence]

        # Inject inline context (e.g. grey-source full_text) as synthetic evidence.
        inline_texts = [t for t in (inline_context_texts or []) if t and str(t).strip()]
        if inline_texts:
            inline_source = Source(paper_id="inline_context", title="Inline-Kontext", year=None, doi=None, url=None)
            inline_evidence = [
                Evidence(
                    paper_id="inline_context",
                    kind="inline",
                    field="context_text",
                    text=_best_pdf_context_snippet(text, question),
                    score=10.0,
                    metadata={"context_index": i},
                )
                for i, text in enumerate(inline_texts)
            ]
            context_diagnostics["inline_context_count"] = len(inline_texts)
            if not hits:
                inline_hit = SearchHit(source=inline_source)
                for ev in inline_evidence:
                    inline_hit.add_evidence(ev)
                hits = [inline_hit]
            evidence = inline_evidence + evidence
            if inline_source not in sources:
                sources = [inline_source] + sources

        if not evidence:
            return GroundedAnswer(
                question=question,
                answer=f"No matching evidence was found in the local KG for: {question}",
                sources=[],
                evidence=[],
                citation_links=[],
                no_answer=True,
                model=model,
                context_diagnostics={**context_diagnostics, "fallback_reason": "no_kg_evidence"},
            )

        answer_text, generation_error, gen_diagnostics = self._generate_answer(
            question=question,
            hits=hits,
            evidence=evidence,
            provider=provider,
            model=model,
            overrides=overrides,
            conversation_context=conversation_context,
            priority_paper_ids=priority_set,
        )
        known_ids = frozenset(s.paper_id for s in sources)
        cited_ids = _cited_paper_ids(answer_text, known_ids)
        if cited_ids:
            cited_sources = [source for source in sources if source.paper_id in cited_ids]
            cited_evidence = [item for item in evidence if item.paper_id in cited_ids]
            if cited_sources:
                sources = cited_sources
            if cited_evidence:
                evidence = cited_evidence
        citation_links = _citation_links_for_answer(answer_text, evidence)
        return GroundedAnswer(
            question=question,
            answer=answer_text,
            sources=sources,
            evidence=evidence,
            citation_links=citation_links,
            no_answer=False,
            model=model or self._default_model(provider),
            generation_error=generation_error,
            context_diagnostics={**context_diagnostics, **gen_diagnostics, "answer_context_mode": "kg"},
        )

    def _answer_from_pdf_context_if_fits(
        self,
        *,
        question: str,
        provider: str | None,
        model: str | None,
        overrides: dict[str, Any] | None,
        conversation_context: list[dict[str, Any]] | None,
        paper_ids: list[str] | set[str] | None,
        pdf_base_dir: str,
    ) -> tuple[GroundedAnswer | None, dict[str, Any]]:
        diagnostics: dict[str, Any] = {"answer_context_mode": "pdf_if_fits"}
        if self.llm_router is None:
            diagnostics["fallback_reason"] = "no_llm_router"
            return None, diagnostics

        requested_ids = [str(item) for item in (paper_ids or []) if str(item or "").strip()]
        requested_ids = list(dict.fromkeys(requested_ids))
        if not requested_ids:
            diagnostics["fallback_reason"] = "no_explicit_paper_scope"
            return None, diagnostics
        if len(requested_ids) > 3:
            diagnostics["fallback_reason"] = "too_many_papers_for_pdf_context"
            diagnostics["paper_count"] = len(requested_ids)
            return None, diagnostics

        sources: list[Source] = []
        parsed_texts: list[tuple[Source, str, str]] = []
        missing: list[str] = []
        for paper_id in requested_ids:
            detail = self.retriever.paper_detail(paper_id) or {}
            source_payload = detail.get("source") or {}
            source = Source(
                paper_id=str(source_payload.get("paper_id") or paper_id),
                title=str(source_payload.get("title") or paper_id),
                year=_coerce_int(source_payload.get("year")),
                doi=source_payload.get("doi"),
                url=source_payload.get("url"),
            )
            pdf_path = find_pdf_path(source.paper_id, source.title, pdf_base_dir)
            if pdf_path is None:
                missing.append(source.paper_id)
                continue
            try:
                pdf_text = parse_pdf_text(str(pdf_path), source.paper_id)
            except Exception as exc:
                diagnostics.setdefault("pdf_errors", {})[source.paper_id] = str(exc)
                continue
            if not pdf_text.strip():
                diagnostics.setdefault("pdf_errors", {})[source.paper_id] = "empty parsed PDF text"
                continue
            sources.append(source)
            parsed_texts.append((source, str(pdf_path), pdf_text))

        if missing:
            diagnostics["missing_pdf_ids"] = missing
        if not parsed_texts:
            diagnostics["fallback_reason"] = "no_parseable_pdf_text"
            return None, diagnostics

        sections = [
            f"[{source.paper_id}] {source.title}\nPDF path: {pdf_path}\n\n{pdf_text}"
            for source, pdf_path, pdf_text in parsed_texts
        ]
        combined_text = "\n\n--- PAPER TEXT ---\n\n".join(sections)
        merged_overrides = {
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": self._answer_max_tokens(provider),
            **(overrides or {}),
        }
        if model:
            merged_overrides["model"] = model
        context_size, max_tokens, resolved_model = effective_generation_limits(
            self.llm_router,
            provider,
            merged_overrides,
            default_max_tokens=self.MIN_ANSWER_TOKENS,
        )
        decision = decide_whole_context(
            text=combined_text,
            context_policy="whole",
            context_size=context_size,
            max_tokens=max_tokens,
            prompt_overhead_tokens=1800,
            output_reserve_tokens=max_tokens,
            chunk_count_if_fallback=len(parsed_texts),
            provider=provider,
            model=resolved_model,
        )
        diagnostics.update(decision.to_dict())
        diagnostics["paper_count"] = len(parsed_texts)
        if not decision.whole_context_used:
            diagnostics["fallback_reason"] = decision.fallback_reason or "context_budget_exceeded"
            return None, diagnostics

        texts_by_paper_id = {
            source.paper_id: (source, pdf_path, pdf_text) for source, pdf_path, pdf_text in parsed_texts
        }
        prompt = _build_pdf_context_prompt(question, combined_text, conversation_context=conversation_context)
        try:
            response = self._chat_with_transient_retry(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                provider=provider,
                overrides=merged_overrides,
            )
        except Exception as exc:
            diagnostics["fallback_reason"] = "pdf_context_generation_failed"
            diagnostics["generation_error"] = str(exc)
            return None, diagnostics

        known_ids = frozenset(source.paper_id for source in sources)
        answer_text = str(response or "").strip()
        # Pull the model's own verbatim quotes out of the answer BEFORE repair/strip: the
        # braces would confuse bracket handling, and "[CI]"-style brackets inside quotes
        # must never be treated as citations. Contexts are bracket-agnostic, so the
        # (paper_id, context) keys stay valid across the repair/strip steps below.
        answer_text, model_quotes = _extract_model_quotes(answer_text)
        quotes_by_context: dict[tuple[str, str], str] = {}
        for match, raw_citation, context in _citation_occurrences(answer_text):
            quote = model_quotes.get(match.end())
            if not quote:
                continue
            for paper_id_value in _citation_paper_ids(raw_citation, known_ids):
                quotes_by_context.setdefault((paper_id_value, context), quote)
        answer_text = self._repair_invalid_citations(
            response=answer_text,
            prompt=prompt,
            provider=provider,
            overrides=merged_overrides,
            known_ids=known_ids,
        )
        answer_text = _strip_invalid_citations(answer_text, known_ids)
        if not answer_text:
            diagnostics["fallback_reason"] = "empty_pdf_context_answer"
            return None, diagnostics

        unique_contexts = _unique_citation_contexts(answer_text, known_ids)

        # Reliable path first: verify each model-provided quote character-for-character in
        # the PDF. Only contexts without a verified quote need the translate-and-fuzzy-match
        # fallback (which also makes the extra translation LLM call rarer).
        verbatim_by_pair: dict[tuple[str, str], str] = {}
        for paper_id_value, citation_context in unique_contexts:
            located = texts_by_paper_id.get(paper_id_value)
            quote = quotes_by_context.get((paper_id_value, citation_context))
            if located is None or not quote:
                continue
            excerpt = verbatim_excerpt(located[2], quote)
            if excerpt:
                verbatim_by_pair[(paper_id_value, citation_context)] = excerpt
        if verbatim_by_pair:
            diagnostics["model_quote_verbatim_count"] = len(verbatim_by_pair)

        pending_contexts = [
            (paper_id_value, citation_context)
            for paper_id_value, citation_context in unique_contexts
            if (paper_id_value, citation_context) not in verbatim_by_pair
            and (paper_id_value, citation_context) not in quotes_by_context
        ]
        translations_by_paper = {
            paper_id_value: self._translate_claims_for_pdf_matching(
                contexts,
                texts_by_paper_id[paper_id_value][2],
                provider,
                merged_overrides,
            )
            for paper_id_value, contexts in _citation_contexts_by_paper(pending_contexts).items()
            if paper_id_value in texts_by_paper_id
        }

        claim_evidence: list[Evidence] = []
        evidence_by_excerpt: dict[tuple[str, str], Evidence] = {}
        unmatched_claim_contexts = 0
        approx_region_contexts = 0
        for paper_id_value, citation_context in unique_contexts:
            located = texts_by_paper_id.get(paper_id_value)
            if located is None:
                unmatched_claim_contexts += 1
                continue
            source, pdf_path, pdf_text = located

            anchor: str | None = None
            policy = "claim_excerpt"
            verbatim = verbatim_by_pair.get((paper_id_value, citation_context))
            if verbatim:
                excerpts = [verbatim]
                anchor = "model_quote"
            else:
                # An unverified model quote is still a better (same-language) anchor than
                # the paraphrased answer sentence.
                quote = quotes_by_context.get((paper_id_value, citation_context))
                match_text = quote or translations_by_paper.get(paper_id_value, {}).get(
                    citation_context, citation_context
                )
                # Scattered facts -> several distinct excerpts; adjacent facts -> one longer
                # merged excerpt (best_excerpts handles both).
                excerpts = best_excerpts(pdf_text, match_text, max_excerpts=3, strict=True)
                if excerpts and quote:
                    anchor = "model_quote_fuzzy"
                if not excerpts:
                    # No confident anchor: show one larger approximate region (flagged for
                    # the UI) rather than nothing or a wrong-looking single sentence.
                    region = best_excerpt(pdf_text, match_text, window_chars=APPROX_REGION_CHARS)
                    if not region:
                        unmatched_claim_contexts += 1
                        continue
                    excerpts = [region]
                    policy = "approx_region"
                    approx_region_contexts += 1

            for rank, excerpt in enumerate(excerpts):
                # The same passage may support several answer sentences: keep ONE evidence
                # item and remember every context, instead of listing duplicate quotes.
                dedupe_key = (source.paper_id, re.sub(r"\s+", " ", excerpt).strip().lower())
                existing = evidence_by_excerpt.get(dedupe_key)
                if existing is not None:
                    contexts = existing.metadata.setdefault("contexts", [existing.metadata.get("context")])
                    if citation_context not in contexts:
                        contexts.append(citation_context)
                    continue
                metadata: dict[str, Any] = {
                    "title": source.title,
                    "pdf_path": pdf_path,
                    "context": citation_context,
                    "context_policy": policy,
                }
                if policy == "claim_excerpt":
                    metadata["fragment_rank"] = rank
                if anchor:
                    metadata["anchor"] = anchor
                item = Evidence(
                    paper_id=source.paper_id,
                    kind="pdf",
                    field="answer_claim_excerpt" if policy == "claim_excerpt" else "answer_claim_region",
                    text=excerpt,
                    score=(11.0 - 0.1 * rank) if policy == "claim_excerpt" else 10.5,
                    metadata=metadata,
                )
                evidence_by_excerpt[dedupe_key] = item
                claim_evidence.append(item)

        if unmatched_claim_contexts:
            diagnostics["unmatched_claim_context_count"] = unmatched_claim_contexts
        if approx_region_contexts:
            diagnostics["approx_region_context_count"] = approx_region_contexts

        # Every paper keeps a whole-pdf snippet as an honest approximate target: citations
        # whose sentence could not be anchored link here (marked approximate) instead of
        # stealing another sentence's located excerpt.
        fallback_evidence = [
            Evidence(
                paper_id=source.paper_id,
                kind="pdf",
                field="parsed_pdf_text",
                text=_best_pdf_context_snippet(pdf_text, question),
                score=10.0,
                metadata={"title": source.title, "pdf_path": pdf_path, "context_policy": "whole"},
            )
            for source, pdf_path, pdf_text in parsed_texts
        ]
        evidence = claim_evidence + fallback_evidence

        cited_ids = _cited_paper_ids(answer_text, known_ids)
        filtered_sources = [source for source in sources if not cited_ids or source.paper_id in cited_ids]
        filtered_evidence = [item for item in evidence if not cited_ids or item.paper_id in cited_ids]
        answer = GroundedAnswer(
            question=question,
            answer=answer_text,
            sources=filtered_sources or sources,
            evidence=filtered_evidence or evidence,
            citation_links=_citation_links_for_answer(answer_text, filtered_evidence or evidence),
            no_answer=False,
            model=model or resolved_model or self._default_model(provider),
            generation_error=None,
            context_diagnostics=diagnostics,
        )
        try:
            answer.source_verification = verify_answer_sources(
                answer.to_dict(),
                pdf_base_dir=pdf_base_dir,
                parse_pdfs=True,
                max_sources=10,
                max_evidence_per_source=5,
            ).to_dict()
        except Exception as exc:
            answer.context_diagnostics["source_verification_error"] = str(exc)
        return answer, diagnostics

    def _generate_answer(
        self,
        question: str,
        hits: list[SearchHit],
        evidence: list[Evidence],
        provider: str | None,
        model: str | None,
        overrides: dict[str, Any] | None,
        conversation_context: list[dict[str, Any]] | None = None,
        priority_paper_ids: set[str] | None = None,
    ) -> tuple[str, str | None, dict[str, Any]]:
        if self.llm_router is None:
            return _extractive_answer(question, hits, evidence), None, {}

        prompt = _build_grounded_prompt(
            question,
            hits,
            evidence,
            conversation_context=conversation_context,
            priority_paper_ids=priority_paper_ids,
        )
        merged_overrides = {
            "temperature": 0.1,
            "top_p": 0.9,
            "max_tokens": self._answer_max_tokens(provider),
            **(overrides or {}),
        }
        if model:
            merged_overrides["model"] = model

        try:
            response = self._chat_with_transient_retry(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                provider=provider,
                overrides=merged_overrides,
            )
        except Exception as exc:
            fallback = _extractive_answer(question, hits, evidence)
            return (
                "I could not generate a synthesized answer because the configured LLM call failed. "
                "Evidence-only fallback:\n" + fallback,
                str(exc),
                {},
            )

        response = str(response or "").strip()
        if not response and self._should_retry_empty_response(merged_overrides):
            retry_overrides = dict(merged_overrides)
            current_tokens = int(retry_overrides.get("max_tokens") or self.MIN_ANSWER_TOKENS)
            retry_overrides["max_tokens"] = min(
                max(current_tokens * 2, 4096),
                self.MAX_ANSWER_TOKENS,
            )
            if retry_overrides["max_tokens"] > current_tokens:
                try:
                    response = self._chat_with_transient_retry(
                        [
                            {"role": "system", "content": self.SYSTEM_PROMPT},
                            {"role": "user", "content": prompt},
                        ],
                        provider=provider,
                        overrides=retry_overrides,
                    )
                except Exception as exc:
                    fallback = _extractive_answer(question, hits, evidence)
                    return (
                        "I could not generate a synthesized answer because the configured LLM call failed. "
                        "Evidence-only fallback:\n" + fallback,
                        str(exc),
                        {},
                    )
                response = str(response or "").strip()

        known_ids = frozenset(item.paper_id for item in evidence) | frozenset(
            hit.source.paper_id for hit in hits
        )
        response = _map_numeric_citations(response, evidence, known_ids)
        response = self._repair_invalid_citations(
            response=response,
            prompt=prompt,
            provider=provider,
            overrides=merged_overrides,
            known_ids=known_ids,
        )
        response = self._repair_sparse_citations(
            response=response,
            prompt=prompt,
            provider=provider,
            overrides=merged_overrides,
            evidence=evidence,
            known_ids=known_ids,
        )
        response = _strip_invalid_citations(response, known_ids)

        gen_diagnostics: dict[str, Any] = {}
        if response:
            if not _cited_paper_ids(response, known_ids):
                response, attached_count = _attach_citations_to_sentences(response, evidence)
                if attached_count:
                    gen_diagnostics["citation_enforcement"] = {"sentences_attached": attached_count}
            if not _cited_paper_ids(response, known_ids):
                # Hard guarantee: never return an answer without traceable citations.
                gen_diagnostics["fallback_reason"] = "no_traceable_citations"
                return (
                    "Hinweis: Für diese Antwort konnten keine Aussagen zuverlässig mit Quellen "
                    "verknüpft werden. Stattdessen folgt eine beleg-basierte Zusammenfassung:\n"
                    + _extractive_answer(question, hits, evidence),
                    None,
                    gen_diagnostics,
                )
            gen_diagnostics["uncited_sentence_count"] = _uncited_sentence_count(response, known_ids)
            return response, None, gen_diagnostics
        return (
            "I could not generate a synthesized answer because the configured LLM returned an empty response. "
            "Evidence-only fallback:\n" + _extractive_answer(question, hits, evidence),
            "empty_response",
            gen_diagnostics,
        )

    def _chat_with_transient_retry(
        self,
        messages: list[dict[str, str]],
        provider: str | None,
        overrides: dict[str, Any],
    ) -> str:
        assert self.llm_router is not None
        try:
            return self.llm_router.chat(messages, provider=provider, overrides=overrides)
        except Exception as exc:
            if not _is_transient_generation_error(str(exc)):
                raise
            return self.llm_router.chat(messages, provider=provider, overrides=overrides)

    def _default_model(self, provider: str | None) -> str | None:
        if self.llm_router is None:
            return None
        try:
            return self.llm_router.provider_settings(provider).model
        except Exception:
            return None

    def _answer_max_tokens(self, provider: str | None) -> int:
        if self.llm_router is None:
            return self.MIN_ANSWER_TOKENS
        try:
            configured = int(self.llm_router.provider_settings(provider).max_tokens)
        except Exception:
            configured = self.MIN_ANSWER_TOKENS
        return min(max(configured, self.MIN_ANSWER_TOKENS), self.MAX_ANSWER_TOKENS)

    def _should_retry_empty_response(self, overrides: dict[str, Any]) -> bool:
        if self.llm_router is None:
            return False
        metadata = getattr(self.llm_router, "last_response_metadata", {}) or {}
        if metadata.get("finish_reason") == "length":
            return True
        usage = metadata.get("usage") or {}
        completion_details = usage.get("completion_tokens_details") or {}
        reasoning_tokens = int(completion_details.get("reasoning_tokens") or 0)
        max_tokens = int(overrides.get("max_tokens") or self.MIN_ANSWER_TOKENS)
        return reasoning_tokens > 0 and reasoning_tokens >= max_tokens - 1

    def _evidence_for_answer(
        self,
        hits: list[SearchHit],
        max_items: int,
        priority_paper_ids: set[str] | None = None,
    ) -> list[Evidence]:
        evidence = _flatten_evidence(hits, max_items=max_items)
        paper_ids = [hit.source.paper_id for hit in hits[:3]]
        existing = {(item.paper_id, item.kind, item.text) for item in evidence}

        for paper_id in paper_ids:
            detail = self.retriever.paper_detail(paper_id)
            latest = (detail or {}).get("latest_extraction") or {}
            for item in _supplemental_evidence_from_extraction(paper_id, latest):
                key = (item.paper_id, item.kind, item.text)
                if key in existing:
                    continue
                existing.add(key)
                evidence.append(item)

        priority_ids = priority_paper_ids or set()
        evidence.sort(
            key=lambda item: _answer_evidence_rank(item) + (5.0 if item.paper_id in priority_ids else 0.0),
            reverse=True,
        )
        return evidence[:max_items]

    def _repair_invalid_citations(
        self,
        response: str,
        prompt: str,
        provider: str | None,
        overrides: dict[str, Any],
        known_ids: frozenset[str] = frozenset(),
    ) -> str:
        if self.llm_router is None or not response:
            return response
        if not _invalid_citations(response, known_ids):
            return response

        repair_prompt = (
            f"{prompt}\n\n"
            "Your previous answer used invalid citations. Rewrite the answer using only "
            "paper IDs exactly as shown in the evidence, for example [arxiv:2507.16947]. "
            "Do not cite evidence item numbers like [1] or [4].\n\n"
            f"Previous answer:\n{response}"
        )
        repair_overrides = dict(overrides)
        repair_overrides["temperature"] = min(float(repair_overrides.get("temperature", 0.1)), 0.05)
        try:
            repaired = self.llm_router.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": repair_prompt},
                ],
                provider=provider,
                overrides=repair_overrides,
            )
        except Exception:
            return response
        repaired = str(repaired or "").strip()
        if repaired and not _invalid_citations(repaired, known_ids):
            return repaired
        return response

    def _translate_claims_for_pdf_matching(
        self,
        contexts: list[str],
        pdf_text: str,
        provider: str | None,
        overrides: dict[str, Any],
    ) -> dict[str, str]:
        """Rewrite claim snippets into the cited paper's language so `best_excerpt` can
        anchor on them. Token-overlap matching (`highlightable_terms`) is reliable within
        one language but breaks down across languages — e.g. a German claim about
        "Glukokortikoiden"/"unerwünschten Ereignissen" shares essentially no tokens with
        the English PDF text it's citing, so the matcher falls back to whatever generic,
        ubiquitous terms (drug/disease names) happen to overlap, landing on an unrelated
        passage. Translating the claim into the PDF's language first lets the existing,
        validated same-language matching do its job. Returns {original_context: rewrite};
        on any failure (no router, empty input, generation error, unparseable response) it
        returns an empty mapping so the caller transparently keeps using the original text.
        """
        if self.llm_router is None or not contexts:
            return {}
        sample = re.sub(r"\s+", " ", pdf_text or "").strip()[:1500]
        if not sample:
            return {}
        numbered = "\n".join(f"{index + 1}. {context}" for index, context in enumerate(contexts))
        prompt = (
            "Paper excerpt (defines the target language for the rewrite below):\n"
            f'"""\n{sample}\n"""\n\n'
            "Claim summaries (possibly written in a different language than the excerpt above):\n"
            f"{numbered}\n\n"
            "For EACH numbered claim above, output exactly one line in the form "
            "`<number>: <rewrite>`, where <rewrite> restates that claim's key facts — "
            "terminology, names, qualitative findings, and any numbers — in the SAME "
            "LANGUAGE as the paper excerpt, phrased so it could plausibly appear verbatim "
            "in that paper. If a claim is already written in that language, repeat it "
            "unchanged. Output nothing else — no preamble, no commentary."
        )
        translate_overrides = dict(overrides)
        translate_overrides["temperature"] = 0.0
        translate_overrides["max_tokens"] = min(max(400, sum(len(item) for item in contexts) + 200), 2000)
        try:
            response = self.llm_router.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                provider=provider,
                overrides=translate_overrides,
            )
        except Exception:
            return {}
        return _parse_numbered_translations(str(response or ""), contexts)

    def _repair_sparse_citations(
        self,
        response: str,
        prompt: str,
        provider: str | None,
        overrides: dict[str, Any],
        evidence: list[Evidence],
        known_ids: frozenset[str] = frozenset(),
    ) -> str:
        if self.llm_router is None or not response or _invalid_citations(response, known_ids):
            return response
        available_ids = {item.paper_id for item in evidence if item.paper_id}
        if not available_ids:
            return response
        cited_ids = _cited_paper_ids(response, known_ids)
        if cited_ids:
            if len(available_ids) < 3:
                return response
            desired_count = min(3, len(available_ids))
            if len(cited_ids) >= desired_count:
                return response
        else:
            # An answer without any citation is never acceptable — always attempt a
            # repair, even when only one or two sources are available.
            desired_count = min(3, len(available_ids))

        repair_prompt = (
            f"{prompt}\n\n"
            "Your previous answer cited too few different papers for the available evidence. "
            f"Rewrite the answer so that, when relevant, it cites at least {desired_count} distinct paper IDs "
            "from the evidence. Keep the answer concise and do not add unsupported facts.\n\n"
            f"Previous answer:\n{response}"
        )
        repair_overrides = dict(overrides)
        repair_overrides["temperature"] = min(float(repair_overrides.get("temperature", 0.1)), 0.05)
        try:
            repaired = self.llm_router.chat(
                [
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": repair_prompt},
                ],
                provider=provider,
                overrides=repair_overrides,
            )
        except Exception:
            return response
        repaired = str(repaired or "").strip()
        if not repaired or _invalid_citations(repaired, known_ids):
            return response
        repaired_ids = _cited_paper_ids(repaired, known_ids)
        return repaired if len(repaired_ids) > len(cited_ids) else response


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
    if re.search(r"\b(ai consult|clinical|clinician|physician|patient|diagnos|treatment)\b", text or "", re.I):
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


def _prioritize_hits(hits: list[SearchHit], priority_paper_ids: set[str]) -> list[SearchHit]:
    """Stable-sort hits so evidence from the main source(s) ranks first."""
    if not priority_paper_ids:
        return hits
    return sorted(
        hits,
        key=lambda hit: 0 if str(getattr(hit.source, "paper_id", "")) in priority_paper_ids else 1,
    )


def _build_grounded_prompt(
    question: str,
    hits: list[SearchHit],
    evidence: list[Evidence],
    conversation_context: list[dict[str, Any]] | None = None,
    priority_paper_ids: set[str] | None = None,
) -> str:
    source_titles = {
        hit.source.paper_id: hit.source.title or hit.source.paper_id
        for hit in hits
    }
    lines = [f"Question: {question}"]
    context_lines = _conversation_context_lines(conversation_context)
    if context_lines:
        lines.extend(["", "Previous conversation context:", *context_lines])
    present_priority = [pid for pid in (priority_paper_ids or set()) if pid in source_titles]
    if present_priority:
        joined = ", ".join(f"[{pid}]" for pid in present_priority)
        lines.extend(
            [
                "",
                f"Primary source(s) / Hauptquelle: {joined}.",
                "Answer primarily from the primary source(s). Use the other evidence only to "
                "support, contradict, or deepen points the primary source does not fully explain, "
                "and make clear which role each supporting source plays.",
            ]
        )
    lines.extend(["", "Evidence:"])
    has_grey_evidence = False
    for index, item in enumerate(evidence, start=1):
        title = source_titles.get(item.paper_id, item.paper_id)
        item_metadata = getattr(item, "metadata", None)
        is_grey = item.paper_id.startswith("grey::") or (
            isinstance(item_metadata, dict) and item_metadata.get("source_type") == "grey"
        )
        marker = "(Webquelle) " if is_grey else ""
        has_grey_evidence = has_grey_evidence or is_grey
        lines.append(
            f"{index}. [{item.paper_id}] {title} | {item.kind} | {marker}{_sanitize_evidence_text(item.text)}"
        )
    if has_grey_evidence:
        lines.append(
            "Evidence marked (Webquelle) comes from web sources, which are often more current than papers. "
            "Papers are the primary, verified sources: when paper evidence supports a claim, cite the "
            "paper, and add a (Webquelle) ID after the paper ID in the same bracket (e.g. [p1, grey::abc]) "
            "only as supporting or updating context. Cite a (Webquelle) alone only for information that no "
            "paper evidence covers, such as recent developments or current events."
        )
    lines.extend(
        [
            "",
            "Answer concisely using only this evidence.",
            "Include source paper IDs in square brackets for each substantive claim.",
            "Place each citation marker at the end of the sentence or claim it supports — never before the claim or in the middle of it.",
            "When multiple papers support different parts of the answer, cite multiple distinct paper IDs instead of reusing only one source.",
            "When one claim is supported by multiple papers, cite the supporting paper IDs together in one bracket, separated by commas, for example [p1, p2].",
            "Use only paper IDs shown in the evidence as citations; never cite evidence item numbers like [1] or [4].",
            "Do not copy reference or citation numbers from the source text (superscripts like [17-22] or [26, 29]); cite only the paper IDs shown above.",
            "When quantitative findings or metrics are present, include the most important numbers.",
            "When evidence contains both positive findings and important caveats — such as side "
            "effects, adverse events, limitations, subgroup-specific results, or contradicting "
            "outcomes — include both. If a finding applies only to a specific subgroup (e.g., by "
            "age, treatment arm, or patient category), state the qualifier explicitly.",
        ]
    )
    if _needs_clinical_model_role_instruction(question, evidence):
        lines.append(
            "Distinguish deployed clinical systems from models used only for evaluation, rating, or robustness checks."
        )
    return "\n".join(lines)


def _conversation_context_lines(conversation_context: list[dict[str, Any]] | None) -> list[str]:
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
            "Place each citation marker at the end of the sentence or claim it supports — never before the claim or in the middle of it.",
            "When multiple papers support one claim, cite them together, for example [p1, p2].",
            "Do not copy reference or citation numbers from the source text (superscripts like [17-22] or [26, 29]); cite only the paper IDs shown above.",
            "If the PDF context is insufficient, say that the local PDF context does not contain enough evidence.",
            "When the PDF contains both a positive result and an important caveat — side effects, "
            "adverse events, subgroup qualifier, or contradicting result — report both. Do not omit the caveat.",
        ]
    )
    return "\n".join(lines)


def _best_pdf_context_snippet(pdf_text: str, question: str, max_chars: int = 900) -> str:
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
        hit.source.paper_id: hit.source.title or hit.source.paper_id
        for hit in hits
    }
    lines = [f"Local KG evidence for '{question}':"]
    for item in evidence[:5]:
        title = source_titles.get(item.paper_id, item.paper_id)
        lines.append(f"- [{item.paper_id}] {title}: {_sanitize_evidence_text(item.text)}")
    return "\n".join(lines)


def _coerce_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _sanitize_evidence_text(text: str) -> str:
    return re.sub(
        r"\[([^\]]+)\]",
        lambda match: match.group(0)
        if _is_allowed_citation_label(match.group(1).strip())
        else f"({match.group(1).strip()})",
        str(text or ""),
    )


def _cited_paper_ids(answer_text: str, known_ids: frozenset[str] = frozenset()) -> set[str]:
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


def _unique_citation_contexts(answer_text: str, known_ids: frozenset[str] = frozenset()) -> list[tuple[str, str]]:
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


def _citation_links_for_answer(answer_text: str, evidence: list[Evidence]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    known_ids = frozenset(item.paper_id for item in evidence if item.paper_id)
    used_by_paper: dict[str, set[str]] = {}
    for match, raw_citation, context in _citation_occurrences(answer_text):
        for paper_id_value in _citation_paper_ids(raw_citation, known_ids):
            used = used_by_paper.setdefault(paper_id_value, set())
            best_index, best_evidence, score = _best_citation_evidence(paper_id_value, context, evidence, used)
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
            link_metadata = best_evidence.metadata if isinstance(best_evidence.metadata, dict) else {}
            if score < _CONTEXT_MATCH_SCORE and link_metadata.get("context_policy") in ("whole", "claim_excerpt"):
                link["approximate"] = True
            links.append(link)
    return links


def _citation_paper_ids(citation: str, known_ids: frozenset[str] = frozenset()) -> list[str]:
    ids: list[str] = []
    for value in re.split(r"[,;]\s*|\s+(?:and|und)\s+", citation or ""):
        value = value.strip()
        if value and _is_allowed_citation_label(value, known_ids):
            ids.append(value)
    return ids


def _citation_context(answer_text: str, start: int, end: int, window: int = 500) -> str:
    before = str(answer_text or "")[:start]
    after = str(answer_text or "")[end:]
    left_boundary = max(before.rfind(". "), before.rfind("! "), before.rfind("? "), before.rfind("\n\n"))
    right_candidates = [idx for idx in [after.find(". "), after.find("! "), after.find("? "), after.find("\n\n")] if idx >= 0]
    left = left_boundary + 2 if left_boundary >= 0 else max(0, start - window // 2)
    right = end + (min(right_candidates) + 1 if right_candidates else min(len(after), window // 2))
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
    candidates = [(index, item) for index, item in enumerate(evidence) if _same_paper_id(item.paper_id, paper_id_value)]
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
            and item.metadata.get("context_policy") in ("claim_excerpt", "approx_region")
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
        matched_numbers = sum(1 for token in quantitative if token in _quantitative_tokens(target))
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
    return left_norm == right_norm or left_norm.endswith(right_norm) or right_norm.endswith(left_norm)


def _normalize_citation_id(value: str) -> str:
    normalized = re.sub(r"^https?://arxiv\.org/abs/", "arxiv:", str(value or "").lower())
    return re.sub(r"\s+", "", normalized)


def _match_normalize(value: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^\w.%+-]+", " ", str(value or "").lower())).strip()


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
            phrase = " ".join(words[index:index + size])
            if any(char.isdigit() for char in phrase) or len(phrase) >= 28:
                phrases.append(phrase)
    return phrases[:12]


def _invalid_citations(answer_text: str, known_ids: frozenset[str] = frozenset()) -> list[str]:
    invalid: list[str] = []
    for bracketed in re.findall(r"\[([^\]]+)\]", answer_text or ""):
        parts = [part.strip() for part in re.split(r"[,;]\s*", bracketed) if part.strip()]
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


def _is_allowed_citation_label(value: str, known_ids: frozenset[str] = frozenset()) -> bool:
    # An exact match against a paper ID actually present in this answer's context is
    # always valid, regardless of format (local uploads can have bare IDs like "files").
    if value in known_ids:
        return True
    # Accept any source:id format (e.g. arxiv:, crossref:, openalex:, semantic_scholar:, files:, local:, doi:)
    # and legacy short p-prefixed IDs. Bare numbers or random tokens are rejected.
    return bool(re.match(r'^[a-z][a-z0-9_]*:[^\s]', value)) or value.startswith("p")


# Sentence matcher that treats bracketed citations as atomic, so paper IDs containing
# dots (e.g. [arxiv:2507.16947]) do not split a sentence in the middle of the marker.
_SENTENCE_RE = re.compile(r"(?:\[[^\]\n]*\]|[^.!?\n])+[.!?]*")

# `[paper_id]{{verbatim supporting passage}}` blocks emitted by the PDF-context prompt.
_MODEL_QUOTE_RE = re.compile(r"[ \t]*\{\{(.*?)\}\}", re.DOTALL)


def _extract_model_quotes(answer_text: str) -> tuple[str, dict[int, str]]:
    """Strip `{{...}}` quote blocks from the answer and collect their quotes.

    The PDF-context prompt asks the model to append, after each citation bracket, the
    exact passage it used — copied verbatim from the PDF. That removes the guesswork of
    re-locating a paraphrase: we verify the quote character-for-character and only fall
    back to fuzzy anchoring when the model misquoted. Returns the cleaned answer plus
    quotes keyed by the end offset of the citation bracket directly before each block
    (matching `_citation_occurrences`' match.end() on the cleaned text).
    """
    text = str(answer_text or "")
    if "{{" not in text:
        return text, {}
    quotes: dict[int, str] = {}
    cleaned_parts: list[str] = []
    last = 0
    for match in _MODEL_QUOTE_RE.finditer(text):
        cleaned_parts.append(text[last:match.start()])
        quote = re.sub(r"\s+", " ", match.group(1)).strip().strip("\"„“«»'").strip()
        before = "".join(cleaned_parts).rstrip()
        if quote and before.endswith("]"):
            quotes[len(before)] = quote
        last = match.end()
    cleaned_parts.append(text[last:])
    return "".join(cleaned_parts), quotes


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

    def _replace(match: re.Match[str]) -> str:
        parts = [part.strip() for part in re.split(r"[,;]\s*", match.group(1)) if part.strip()]
        mapped: list[str] = []
        changed = False
        for part in parts:
            if not _is_allowed_citation_label(part, known_ids) and re.fullmatch(r"\d+", part):
                index = int(part)
                if 1 <= index <= len(evidence) and evidence[index - 1].paper_id:
                    mapped.append(evidence[index - 1].paper_id)
                    changed = True
                    continue
            mapped.append(part)
        if not changed:
            return match.group(0)
        return f"[{', '.join(dict.fromkeys(mapped))}]"

    return re.sub(r"\[([^\]]+)\]", _replace, text)


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
            score += 2.0 * sum(1 for token in quantitative if token in _quantitative_tokens(target))
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
        trailing = sentence[len(stripped):]
        body = stripped.rstrip(".!?")
        punctuation = stripped[len(body):]
        attached += 1
        return f"{body} [{best_id}]{punctuation}{trailing}"

    return _SENTENCE_RE.sub(_replace, text), attached


def _uncited_sentence_count(answer_text: str, known_ids: frozenset[str] = frozenset()) -> int:
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


def _strip_invalid_citations(answer_text: str, known_ids: frozenset[str] = frozenset()) -> str:
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
        parts = [part.strip() for part in re.split(r"[,;]\s*", match.group(1)) if part.strip()]
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


def _needs_clinical_model_role_instruction(question: str, evidence: list[Evidence]) -> bool:
    text = " ".join([question, *(item.text for item in evidence[:8])]).lower()
    has_clinical = any(term in text for term in ("clinical", "clinic", "clinician", "patient", "physician"))
    has_model_role = any(
        term in text
        for term in ("deployed", "deployment", "evaluation", "rating", "rater", "grader", "gpt-4", "o3", "ai consult")
    )
    return has_clinical and has_model_role
