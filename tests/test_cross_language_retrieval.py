"""Unit tests for cross-language query rewriting and the Unicode tokenizer.

These cover the two universal fixes for the German-question/English-papers
retrieval gap:

* ``query.query_rewriter.QueryRewriter`` — LLM-driven translation to English +
  keyword extraction, with graceful fallbacks.
* ``query.kg_retriever._tokenize`` — Unicode-aware tokenization so non-ASCII
  terms (German umlauts, French accents, …) survive into the lexical scorer.
"""

from __future__ import annotations

from typing import Any

from query.kg_retriever import _tokenize
from query.query_rewriter import (
    QueryRewriter,
    RewriteResult,
    _coerce_keywords,
    _parse_payload,
)


# --------------------------------------------------------------------------- #
# Unicode tokenizer (A1)
# --------------------------------------------------------------------------- #


class TestUnicodeTokenizer:
    def test_german_question_keeps_content_word(self) -> None:
        tokens = _tokenize("Wie funktioniert das Gehirn?")
        assert tokens == ["gehirn"]

    def test_german_stopwords_filtered(self) -> None:
        tokens = _tokenize("der die das und oder ist wie was warum")
        assert tokens == []

    def test_umlauts_preserved(self) -> None:
        tokens = _tokenize("Überblick Kühe Mädchen")
        assert tokens == ["überblick", "kühe", "mädchen"]

    def test_french_accents_preserved(self) -> None:
        tokens = _tokenize("Café résumé naïve")
        assert tokens == ["café", "résumé", "naïve"]

    def test_chinese_characters_preserved(self) -> None:
        tokens = _tokenize("大脑如何工作")
        assert tokens == ["大脑如何工作"]

    def test_english_still_works(self) -> None:
        tokens = _tokenize("How does the brain work?")
        assert "brain" in tokens
        assert "work" in tokens
        assert "how" not in tokens
        assert "the" not in tokens

    def test_hyphenated_split(self) -> None:
        tokens = _tokenize("graph-based retrieval")
        assert "graph" in tokens
        assert "based" in tokens
        assert "retrieval" in tokens

    def test_casefold_unicode(self) -> None:
        # German sharp s casefolds to "ss"; uppercase umlauts to lowercase.
        # "über" is a German preposition (stopword) so it is filtered — use a
        # content word to verify umlaut preservation.
        tokens = _tokenize("STRAßE MÄDCHEN")
        assert "strasse" in tokens
        assert "mädchen" in tokens

    def test_empty_and_whitespace(self) -> None:
        assert _tokenize("") == []
        assert _tokenize("   ") == []
        assert _tokenize("???") == []


# --------------------------------------------------------------------------- #
# QueryRewriter helpers
# --------------------------------------------------------------------------- #


class TestCoerceKeywords:
    def test_list_of_strings(self) -> None:
        assert _coerce_keywords(["brain", "neuron", "synapse"]) == (
            "brain",
            "neuron",
            "synapse",
        )

    def test_dedup_case_insensitive(self) -> None:
        assert _coerce_keywords(["Brain", "brain", "BRAIN"]) == ("Brain",)

    def test_filters_empty_and_whitespace(self) -> None:
        assert _coerce_keywords(["", "  ", "brain"]) == ("brain",)

    def test_caps_at_eight(self) -> None:
        result = _coerce_keywords([f"k{i}" for i in range(20)])
        assert len(result) == 8

    def test_single_string_wraps(self) -> None:
        assert _coerce_keywords("brain") == ("brain",)

    def test_non_iterable_returns_empty(self) -> None:
        assert _coerce_keywords(42) == ()
        assert _coerce_keywords(None) == ()


class TestParsePayload:
    def test_well_formed_dict(self) -> None:
        payload = {
            "en_query": "How does the brain work?",
            "keywords": ["brain", "neuron"],
            "language": "de",
        }
        result = _parse_payload(payload)
        assert result is not None
        assert result.en_query == "How does the brain work?"
        assert result.keywords == ("brain", "neuron")
        assert result.language == "de"
        assert result.used_llm is True

    def test_missing_en_query_uses_keywords(self) -> None:
        result = _parse_payload({"keywords": ["brain", "neuron"]})
        assert result is not None
        assert result.en_query == "brain neuron"

    def test_missing_everything_returns_none(self) -> None:
        assert _parse_payload({}) is None
        assert _parse_payload({"language": "de"}) is None

    def test_json_string(self) -> None:
        result = _parse_payload('{"en_query": "test query", "keywords": ["a"]}')
        assert result is not None
        assert result.en_query == "test query"

    def test_invalid_json_string_returns_none(self) -> None:
        assert _parse_payload("not json") is None

    def test_non_dict_returns_none(self) -> None:
        assert _parse_payload(["a", "b"]) is None
        assert _parse_payload(42) is None


# --------------------------------------------------------------------------- #
# QueryRewriter
# --------------------------------------------------------------------------- #


class FakeRewriterRouter:
    """Minimal router stub implementing ``chat_json`` for the rewriter."""

    def __init__(self, payload: dict[str, Any] | Exception) -> None:
        self._payload = payload
        self.calls: list[dict[str, Any]] = []

    def chat_json(self, messages, provider=None, overrides=None):
        self.calls.append(
            {"messages": messages, "provider": provider, "overrides": overrides}
        )
        if isinstance(self._payload, Exception):
            raise self._payload
        return self._payload


class TestQueryRewriter:
    def test_successful_rewrite(self) -> None:
        router = FakeRewriterRouter(
            {
                "en_query": "How does the brain work?",
                "keywords": ["brain", "neuron", "synapse"],
                "language": "de",
            }
        )
        rewriter = QueryRewriter(router)

        result = rewriter.rewrite("Wie funktioniert das Gehirn?", provider="ollama")

        assert result.used_llm is True
        assert result.en_query == "How does the brain work?"
        assert result.keywords == ("brain", "neuron", "synapse")
        assert result.language == "de"
        assert result.error == ""
        # retrieval_query merges question + keywords dedup
        rq = result.retrieval_query
        assert "how" in rq and "brain" in rq and "neuron" in rq

    def test_llm_exception_falls_back_offline_dict(self) -> None:
        """When the LLM throws, the offline dictionary translates known terms
        instead of degrading to the raw original. 'gehirn'→'brain' so a German
        question still finds English papers even with the provider down."""
        router = FakeRewriterRouter(RuntimeError("model unavailable"))
        rewriter = QueryRewriter(router)

        result = rewriter.rewrite("Wie funktioniert das Gehirn?")

        assert result.used_llm is False
        assert "brain" in result.en_query
        assert "model unavailable" in result.error
        assert result.retrieval_query != "wie funktioniert das gehirn"
        assert "brain" in result.retrieval_query

    def test_llm_exception_no_dict_match_falls_back_to_original(self) -> None:
        """When the LLM throws AND the offline dictionary has no match, the
        rewriter degrades to the original question unchanged."""
        router = FakeRewriterRouter(RuntimeError("model unavailable"))
        rewriter = QueryRewriter(router)

        result = rewriter.rewrite("How does quantum tunneling work?")

        assert result.used_llm is False
        assert result.en_query == "How does quantum tunneling work?"
        assert "model unavailable" in result.error

    def test_malformed_payload_falls_back_offline_dict(self) -> None:
        router = FakeRewriterRouter({"unrelated": "field"})
        rewriter = QueryRewriter(router)

        result = rewriter.rewrite("Wie funktioniert das Gehirn?")

        assert result.used_llm is False
        assert "brain" in result.en_query

    def test_none_router_falls_back_offline_dict(self) -> None:
        """No router at all → offline dictionary translates 'gehirn'→'brain'
        so cross-language retrieval still works without any LLM available."""
        rewriter = QueryRewriter(None)

        result = rewriter.rewrite("Wie funktioniert das Gehirn?")

        assert result.used_llm is False
        assert "brain" in result.en_query
        assert "brain" in result.retrieval_query

    def test_empty_question_falls_back(self) -> None:
        router = FakeRewriterRouter({"en_query": "should not be used"})
        rewriter = QueryRewriter(router)

        result = rewriter.rewrite("")

        assert result.used_llm is False
        assert result.en_query == ""
        assert router.calls == []

    def test_caches_per_question_provider(self) -> None:
        router = FakeRewriterRouter(
            {"en_query": "How does the brain work?", "keywords": ["brain"]}
        )
        rewriter = QueryRewriter(router)

        rewriter.rewrite("Wie funktioniert das Gehirn?", provider="ollama")
        rewriter.rewrite("Wie funktioniert das Gehirn?", provider="ollama")

        assert len(router.calls) == 1

    def test_different_provider_separate_cache(self) -> None:
        router = FakeRewriterRouter(
            {"en_query": "How does the brain work?", "keywords": ["brain"]}
        )
        rewriter = QueryRewriter(router)

        rewriter.rewrite("Wie funktioniert das Gehirn?", provider="ollama")
        rewriter.rewrite("Wie funktioniert das Gehirn?", provider="openai")

        assert len(router.calls) == 2

    def test_temperature_zero_and_token_cap(self) -> None:
        """Rewrite calls should pin temperature=0 and cap max_tokens for cheapness."""
        router = FakeRewriterRouter({"en_query": "test", "keywords": ["a"]})
        rewriter = QueryRewriter(router)

        rewriter.rewrite("question", overrides={"temperature": 0.9})

        overrides = router.calls[0]["overrides"]
        assert overrides["temperature"] == 0.0
        assert overrides["max_tokens"] == 256

    def test_retrieval_query_dedup_preserves_order(self) -> None:
        result = RewriteResult(
            original="orig",
            en_query="brain how brain",
            keywords=("brain", "neuron"),
            used_llm=True,
        )
        rq = result.retrieval_query
        tokens = rq.split()
        assert tokens == ["brain", "how", "neuron"]

    def test_retrieval_query_falls_back_to_original_when_empty(self) -> None:
        result = RewriteResult(
            original="original question",
            en_query="",
            keywords=(),
            used_llm=False,
        )
        assert result.retrieval_query == "original question"


# --------------------------------------------------------------------------- #
# Thinking-control for reasoning models
# --------------------------------------------------------------------------- #


class FakeRewriterRouterWithModel(FakeRewriterRouter):
    """Router stub that also reports a default model, for thinking-control tests."""

    def __init__(self, payload, model: str = "qwen3.5:9b") -> None:
        super().__init__(payload)
        self._model = model

    def provider_default_model(self, provider=None):
        return self._model


class TestThinkingControl:
    def test_disables_thinking_for_qwen3(self) -> None:
        """A Qwen3 reasoning model must get ``chat_template_kwargs.enable_thinking=False``
        so the small rewrite token budget is not spent on chain-of-thought."""
        router = FakeRewriterRouterWithModel(
            {"en_query": "How does the brain work?", "keywords": ["brain"]},
            model="qwen3.5:9b",
        )
        rewriter = QueryRewriter(router)

        rewriter.rewrite("Wie funktioniert das Gehirn?", provider="ollama")

        overrides = router.calls[0]["overrides"]
        assert overrides["extra"]["chat_template_kwargs"]["enable_thinking"] is False

    def test_no_thinking_control_for_non_reasoning_model(self) -> None:
        """A non-reasoning model gets no thinking override — the field would be ignored
        anyway, but keeping the payload clean avoids surprising provider behavior."""
        router = FakeRewriterRouterWithModel(
            {"en_query": "How does the brain work?", "keywords": ["brain"]},
            model="llama3.1:8b",
        )
        rewriter = QueryRewriter(router)

        rewriter.rewrite("Wie funktioniert das Gehirn?", provider="ollama")

        overrides = router.calls[0]["overrides"]
        assert "chat_template_kwargs" not in overrides.get("extra", {})

    def test_caller_chat_template_kwargs_wins(self) -> None:
        """A caller-supplied ``chat_template_kwargs`` is never clobbered — even if
        the model is a reasoning model and the caller explicitly enabled thinking."""
        router = FakeRewriterRouterWithModel(
            {"en_query": "test", "keywords": ["a"]},
            model="qwen3.5:9b",
        )
        rewriter = QueryRewriter(router)

        rewriter.rewrite(
            "question",
            overrides={"extra": {"chat_template_kwargs": {"enable_thinking": True}}},
        )

        overrides = router.calls[0]["overrides"]
        assert overrides["extra"]["chat_template_kwargs"]["enable_thinking"] is True
