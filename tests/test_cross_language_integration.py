"""Integration + regression tests for cross-language retrieval.

D3: A German question retrieves English-language papers from the KG when the
    QueryRewriter supplies an English translation (simulated LLM).
D4: Regression — the fallback path that previously fired for cross-language
    questions no longer triggers because the rewritten query finds the papers.
    Also verifies the ``_retrieve_grounded`` integration point is exercised.
D5: Offline dictionary fallback — a German question finds English papers even
    when the LLM provider is down (no router / LLM exception).
D6: Stub suppression — empty-claim "metadata"-model papers are not surfaced
    on title match alone; only papers with real extraction content rank.
"""

from __future__ import annotations

import shutil
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from uuid import uuid4

from query.grounded_responder import GroundedResponder
from query.hybrid_retriever import HybridRetriever
from query.kg_retriever import KGRetriever
from query.query_rewriter import QueryRewriter, _offline_rewrite
from storage.metadata_db import MetadataDB


@contextmanager
def _brain_fixture():
    """Seed a KG with English brain-neuroscience papers."""
    root = Path("test-output") / f"xlang-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "metadata.duckdb")
    db = MetadataDB(db_path)
    try:
        db.insert_paper(
            {
                "id": "brain-1",
                "source": "fixture",
                "source_id": "brain-1",
                "title": "How the Brain Processes Visual Information",
                "abstract": "Neurons in the visual cortex encode features of the brain.",
                "year": 2024,
            }
        )
        db.insert_paper(
            {
                "id": "brain-2",
                "source": "fixture",
                "source_id": "brain-2",
                "title": "Neural Mechanisms of Memory",
                "abstract": "Synaptic plasticity underlies learning in the brain.",
                "year": 2023,
            }
        )
        db.insert_paper(
            {
                "id": "unrelated",
                "source": "fixture",
                "source_id": "unrelated",
                "title": "Cloud Computing Economics",
                "abstract": "Cost models for distributed computing infrastructure.",
                "year": 2022,
            }
        )
        db.save_extraction_result(
            paper_id="brain-1",
            llm_provider="fake",
            llm_model="fake-model",
            concepts=[
                {"label": "Brain", "context": "visual cortex", "confidence": 0.9}
            ],
            claims=[
                {"statement": "The brain processes visual information hierarchically."}
            ],
        )
        db.close()
        yield db_path
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


class _RewriterRouter:
    """Fake router: returns a fixed English rewrite for ``chat_json`` calls
    made by QueryRewriter, and raises on any ``chat`` call so a grounded-answer
    generation is never attempted (we only test retrieval here)."""

    def __init__(self, rewrite_payload: dict[str, Any]) -> None:
        self._payload = rewrite_payload
        self.chat_json_calls = 0
        self.chat_calls = 0

    def chat_json(self, messages, provider=None, overrides=None):
        self.chat_json_calls += 1
        return self._payload

    def chat(self, messages, provider=None, overrides=None):
        self.chat_calls += 1
        raise RuntimeError(
            "grounded answer generation not exercised in retrieval tests"
        )


class TestCrossLanguageRetrieval:
    def test_german_question_finds_english_papers_with_rewrite(self) -> None:
        with _brain_fixture() as db_path:
            retriever = HybridRetriever(KGRetriever(metadata_db_path=db_path))
            router = _RewriterRouter(
                {
                    "en_query": "How does the brain work?",
                    "keywords": ["brain", "neuron", "visual cortex"],
                    "language": "de",
                }
            )
            responder = GroundedResponder(retriever=retriever, llm_router=router)

            hits = responder._retrieve_grounded(
                "Wie funktioniert das Gehirn?",
                limit=8,
                provider="ollama",
                overrides=None,
                paper_ids=None,
            )

            paper_ids = {h.source.paper_id for h in hits}
            assert "brain-1" in paper_ids
            assert "brain-2" in paper_ids
            assert "unrelated" not in paper_ids
            # The rewriter was actually consulted
            assert router.chat_json_calls >= 1

    def test_german_question_without_rewrite_finds_nothing(self) -> None:
        """Baseline: without the EN bridge a German query misses English papers.

        This documents the original failure and proves the rewrite is the
        fix (not the Unicode tokenizer alone — ``gehirn`` is not in the papers).
        """
        with _brain_fixture() as db_path:
            retriever = KGRetriever(metadata_db_path=db_path)
            hits = retriever.search("Wie funktioniert das Gehirn?", limit=8)
            brain_hits = [h for h in hits if h.source.paper_id.startswith("brain")]
            assert brain_hits == []

    def test_retrieval_query_property_contains_english_terms(self) -> None:
        rewriter = QueryRewriter(None)
        # None router → offline dictionary fallback translates 'gehirn'→'brain'
        result = rewriter.rewrite("Wie funktioniert das Gehirn?")
        assert "brain" in result.retrieval_query

    def test_original_and_rewritten_hits_merged(self) -> None:
        """When both queries hit, evidence accumulates on the same SearchHit."""
        with _brain_fixture() as db_path:
            retriever = HybridRetriever(KGRetriever(metadata_db_path=db_path))
            router = _RewriterRouter(
                {
                    "en_query": "brain neuron",
                    "keywords": ["brain", "neuron"],
                    "language": "de",
                }
            )
            responder = GroundedResponder(retriever=retriever, llm_router=router)

            hits = responder._retrieve_grounded(
                "brain",  # English original so both queries hit brain-1/brain-2
                limit=8,
                provider=None,
                overrides=None,
                paper_ids=None,
            )

            by_id = {h.source.paper_id: h for h in hits}
            assert "brain-1" in by_id
            # Merged hit should have non-zero score from accumulated evidence
            assert by_id["brain-1"].score > 0


class TestFallbackRegression:
    """D4: the ``too_many_unsourced`` fallback must not fire when retrieval
    succeeds via the rewritten query.

    We simulate the full grounded-answer path with a fake router that supplies
    both the rewrite JSON and a well-cited answer, then assert the answer dict
    does not carry the fallback reason.
    """

    def test_answer_not_marked_fallback_when_retrieval_succeeds(self) -> None:
        with _brain_fixture() as db_path:
            retriever = HybridRetriever(KGRetriever(metadata_db_path=db_path))

            class _DualRouter:
                def __init__(self) -> None:
                    self.json_calls = 0
                    self.chat_calls = 0

                def chat_json(self, messages, provider=None, overrides=None):
                    self.json_calls += 1
                    return {
                        "en_query": "How does the brain work?",
                        "keywords": ["brain", "neuron"],
                        "language": "de",
                    }

                def chat(self, messages, provider=None, overrides=None):
                    self.chat_calls += 1
                    return (
                        "The brain processes information hierarchically "
                        "[arxiv:brain-1]. Visual cortex neurons encode features [arxiv:brain-1]."
                    )

            router = _DualRouter()
            responder = GroundedResponder(retriever=retriever, llm_router=router)
            answer = responder.answer(
                "Wie funktioniert das Gehirn?",
                limit=8,
                provider="ollama",
            )
            d = answer.to_dict()
            diagnostics = d.get("context_diagnostics") or {}
            assert diagnostics.get("fallback_reason") != "too_many_unsourced"
            assert diagnostics.get("fallback_reason") != "no_traceable_citations"
            assert "[arxiv:brain-1]" in str(d.get("answer") or "")


# --------------------------------------------------------------------------- #
# D5: Offline dictionary fallback (no LLM available)
# --------------------------------------------------------------------------- #


class TestOfflineDictionaryFallback:
    """The rewriter's offline German→English dictionary fires when the LLM is
    unreachable, so a German question still produces an English retrieval query
    and the dual-query merge in ``_retrieve_grounded`` still runs."""

    def test_offline_rewrite_translates_gehirn_to_brain(self) -> None:
        translated = _offline_rewrite("Wie funktioniert das Gehirn?")
        assert translated is not None
        assert "brain" in translated

    def test_offline_rewrite_returns_none_for_english(self) -> None:
        # No German content words → no dictionary match → None (caller keeps
        # the original).
        assert _offline_rewrite("How does the brain work?") is None

    def test_offline_rewrite_handles_umlauts(self) -> None:
        # "Gedächtnis" with umlaut should still match "gedächtnis" key.
        translated = _offline_rewrite("Wie funktioniert das Gedächtnis?")
        assert translated is not None
        assert "memory" in translated

    def test_none_router_german_finds_english_papers(self) -> None:
        """End-to-end: no LLM router at all, yet a German question retrieves
        English brain papers via the offline dictionary."""
        with _brain_fixture() as db_path:
            retriever = HybridRetriever(KGRetriever(metadata_db_path=db_path))
            responder = GroundedResponder(retriever=retriever, llm_router=None)

            hits = responder._retrieve_grounded(
                "Wie funktioniert das Gehirn?",
                limit=8,
                provider=None,
                overrides=None,
                paper_ids=None,
            )

            paper_ids = {h.source.paper_id for h in hits}
            assert "brain-1" in paper_ids
            assert "brain-2" in paper_ids
            assert "unrelated" not in paper_ids

    def test_llm_exception_german_finds_english_papers(self) -> None:
        """LLM call fails (provider down) → offline dictionary still bridges
        the German→English gap so retrieval succeeds."""
        with _brain_fixture() as db_path:
            retriever = HybridRetriever(KGRetriever(metadata_db_path=db_path))

            class _DeadRouter:
                def chat_json(self, messages, provider=None, overrides=None):
                    raise ConnectionError("LM Studio not running")

                def chat(self, messages, provider=None, overrides=None):
                    raise ConnectionError("LM Studio not running")

            responder = GroundedResponder(retriever=retriever, llm_router=_DeadRouter())
            hits = responder._retrieve_grounded(
                "Wie funktioniert das Gehirn?",
                limit=8,
                provider="lm_studio",
                overrides=None,
                paper_ids=None,
            )

            paper_ids = {h.source.paper_id for h in hits}
            assert "brain-1" in paper_ids
            assert "brain-2" in paper_ids


# --------------------------------------------------------------------------- #
# D6: Stub suppression (empty-claim papers don't pollute results)
# --------------------------------------------------------------------------- #


@contextmanager
def _stub_fixture():
    """A KG with one real paper (brain + claims) and one stub paper (German
    title, no abstract, empty 'metadata'-model extraction)."""
    root = Path("test-output") / f"stub-{uuid4().hex}"
    root.mkdir(parents=True, exist_ok=True)
    db_path = str(root / "metadata.duckdb")
    db = MetadataDB(db_path)
    try:
        db.insert_paper(
            {
                "id": "real-brain",
                "source": "fixture",
                "source_id": "real-brain",
                "title": "How the Brain Processes Visual Information",
                "abstract": "Neurons in the visual cortex encode features of the brain.",
                "year": 2024,
            }
        )
        db.insert_paper(
            {
                "id": "gehirn-stub",
                "source": "crossref",
                "source_id": "gehirn-stub",
                "title": "Wie funktioniert das Gehirn?",
                "abstract": "",  # no abstract — stub
                "year": 2022,
            }
        )
        db.save_extraction_result(
            paper_id="real-brain",
            llm_provider="fake",
            llm_model="fake-model",
            claims=[
                {"statement": "The brain processes visual information hierarchically."}
            ],
        )
        # Stub extraction: empty claims/concepts/methods (like a 'metadata'-model row).
        db.save_extraction_result(
            paper_id="gehirn-stub",
            llm_provider="metadata",
            llm_model="metadata",
            claims=[],
            concepts=[],
            methods=[],
        )
        db.close()
        yield db_path
    finally:
        if not db.is_closed:
            db.close()
        shutil.rmtree(root, ignore_errors=True)


class TestStubSuppression:
    def test_stub_paper_not_surfaced_on_title_match(self) -> None:
        """The German-titled stub (which has 'gehirn' in its title but no
        abstract and no claims) must not appear in results for a German brain
        query — only the real-brain paper should surface."""
        with _stub_fixture() as db_path:
            retriever = KGRetriever(metadata_db_path=db_path)
            # English query that matches the real paper's abstract/title.
            hits = retriever.search("brain neuron", limit=8)
            ids = {h.source.paper_id for h in hits}
            assert "real-brain" in ids
            assert "gehirn-stub" not in ids

    def test_stub_paper_not_surfaced_for_german_query_via_offline_dict(self) -> None:
        """Even with the offline dictionary translating 'gehirn'→'brain',
        the stub must not surface; only the real paper should."""
        with _stub_fixture() as db_path:
            retriever = HybridRetriever(KGRetriever(metadata_db_path=db_path))
            responder = GroundedResponder(retriever=retriever, llm_router=None)
            hits = responder._retrieve_grounded(
                "Wie funktioniert das Gehirn?",
                limit=8,
                provider=None,
                overrides=None,
                paper_ids=None,
            )
            ids = {h.source.paper_id for h in hits}
            assert "real-brain" in ids
            assert "gehirn-stub" not in ids
