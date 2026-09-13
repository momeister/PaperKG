"""Cross-language query rewriting for retrieval.

The local KG stores papers almost exclusively with English titles/abstracts, but
users ask questions in any language. The lexical retriever tokenizes on Unicode
letters (so non-ASCII terms survive), yet a German ``gehirn`` still does not
match an English ``brain`` token. This module bridges that gap with two layers:

1. **LLM rewrite (preferred):** ask the configured LLM to translate the question
   to English and extract topical keywords. Universal across languages/domains.
2. **Offline dictionary fallback:** a small German→English scientific term
   dictionary that fires when the LLM is unreachable (provider down, no
   router). Domain-agnostic enough to cover the KG's main topics (neuroscience,
   ML, medicine) without becoming a maintenance burden. Best-effort — unknown
   German content words pass through unchanged, which is no worse than before.

Design notes
------------
* **Best-effort.** Every failure mode degrades gracefully: an LLM error, a
  malformed JSON payload, or a missing router all fall back to the offline
  dictionary, then to the original question, so retrieval never gets *worse*
  than before.
* **Cheap.** One short LLM call (<= 256 tokens) per question. The result is
  cached per (question, provider) within a ``QueryRewriter`` instance so a
  single auto-research run (initial answer + re-answers after each harvest
  stage) reuses one rewrite.
* **Retrieval-only.** The original question is still sent to the answer LLM so
  the user gets a reply in their own language; only the retrieval query is
  rewritten.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from query.llm_router import LLMRouter

_SYSTEM_PROMPT = (
    "You are a cross-lingual retrieval assistant for a scientific knowledge graph "
    "whose documents are written almost entirely in English. The user asks a "
    "question in any language. Translate it to English AND extract the 3-8 most "
    "topically meaningful English keywords or keyphrases (nouns/noun phrases, "
    "no stopwords, no question words). Cover the question's concepts broadly "
    "enough to match related literature, not only exact synonyms. Respond ONLY "
    "with a compact JSON object."
)

_SCHEMA_HINT = (
    'Return JSON: {"en_query": "<full English question>", '
    '"keywords": ["<keyword or keyphrase>", ...], '
    '"language": "<ISO 639-1 code of the source language, e.g. de/en/fr/zh>"}'
)


# Offline German→English dictionary for the no-LLM fallback path.
#
# Kept deliberately small and domain-focused (neuroscience / ML / medicine /
# general science), covering only *content words* — function words are already
# filtered by ``kg_retriever.STOPWORDS``. Multiword English values are fine;
# ``retrieval_query`` tokenizes them. Umlauts are mapped on the keys so a user
# typing ``gehirn`` or ``Gehirn`` both hit the entry.
#
# This is a fallback, not a translator: unknown words pass through unchanged,
# which is correct because an untranslated German content word simply won't
# match English papers (the pre-existing behavior) rather than causing a
# wrong match.
_GERMAN_ENGLISH_DICT: dict[str, str] = {
    # neuroscience / brain
    "gehirn": "brain",
    "hirn": "brain",
    "neuronen": "neurons",
    "neuron": "neuron",
    "synapsen": "synapses",
    "synapse": "synapse",
    "kortex": "cortex",
    "hippocampus": "hippocampus",
    "bewusstsein": "consciousness",
    "kognition": "cognition",
    "kognitiv": "cognitive",
    "gedachtnis": "memory",
    "gedächtnis": "memory",
    "lernen": "learning",
    "aufmerksamkeit": "attention",
    "wahrnehmung": "perception",
    "denken": "thinking",
    "sprache": "language",
    "emotionen": "emotions",
    "emotion": "emotion",
    "verhalten": "behavior",
    "nerven": "nerves",
    "nervensystem": "nervous system",
    # ML / CS
    "maschine": "machine",
    "maschinelles": "machine",
    "daten": "data",
    "modell": "model",
    "modelle": "models",
    "netzwerk": "network",
    "netzwerke": "networks",
    "neuronales": "neural",
    "algorithmus": "algorithm",
    "algorithmen": "algorithms",
    "training": "training",
    "kunstliche": "artificial",
    "künstliche": "artificial",
    "intelligenz": "intelligence",
    "wissen": "knowledge",
    "reprasentation": "representation",
    "repräsentation": "representation",
    "einbettung": "embedding",
    "vektor": "vector",
    "vektoren": "vectors",
    # medicine / biology
    "krankheit": "disease",
    "krankheiten": "diseases",
    "patienten": "patients",
    "patient": "patient",
    "therapie": "therapy",
    "behandlung": "treatment",
    "diagnose": "diagnosis",
    "symptom": "symptom",
    "symptome": "symptoms",
    "studie": "study",
    "studien": "studies",
    "untersuchung": "examination",
    "zellen": "cells",
    "zelle": "cell",
    "protein": "protein",
    "proteine": "proteins",
    "gene": "genes",
    "genetik": "genetics",
    # general science
    "methode": "method",
    "methoden": "methods",
    "verfahren": "procedure",
    "experiment": "experiment",
    "ergebnis": "result",
    "ergebnisse": "results",
    "wirkung": "effect",
    "prozesse": "processes",
    "prozess": "process",
    "struktur": "structure",
    "funktionen": "functions",
    "funktion": "function",
    "mechanismus": "mechanism",
    "entwicklung": "development",
    "anwendung": "application",
    "theorie": "theory",
    "hypothese": "hypothesis",
    "nachweis": "evidence",
    "beweis": "proof",
}


def _offline_rewrite(question: str) -> str | None:
    """Translate a question via the German→English dictionary.

    Returns an English query string, or ``None`` when no dictionary term
    matched (so the caller falls back to the original question unchanged).
    Only content words are translated; stopwords and unknown terms pass
    through, which is safe — they either get filtered downstream or simply
    don't match anything.
    """
    if not question or not question.strip():
        return None
    # Normalize umlauts on the lookup key so the user can type either form.
    normalized = {k.casefold(): v for k, v in _GERMAN_ENGLISH_DICT.items()}
    words = re.findall(r"[^\W_]+", question, flags=re.UNICODE)
    if not words:
        return None
    translated: list[str] = []
    matched = False
    seen: set[str] = set()
    for word in words:
        key = word.casefold()
        # umlaut-normalized lookup (gehirn/Gehirn both match)
        english = normalized.get(key) or normalized.get(
            key.replace("ä", "a").replace("ö", "o").replace("ü", "u").replace("ß", "ss")
        )
        if english:
            matched = True
            for token in english.split():
                if token.casefold() not in seen:
                    seen.add(token.casefold())
                    translated.append(token)
        else:
            if key not in seen:
                seen.add(key)
                translated.append(word)
    return " ".join(translated) if matched else None


@dataclass(frozen=True)
class RewriteResult:
    """Outcome of a query rewrite attempt."""

    original: str
    en_query: str
    keywords: tuple[str, ...] = ()
    language: str = ""
    used_llm: bool = False
    error: str = ""

    @property
    def retrieval_query(self) -> str:
        """Single string optimized for lexical retrieval.

        Combines the translated question with the extracted keywords so both the
        full-phrase bonus in ``_score_text`` and the per-token IDF weighting can
        fire. Deduplicates while preserving order.
        """
        parts: list[str] = []
        seen: set[str] = set()
        for piece in (self.en_query, " ".join(self.keywords)):
            for token in re.findall(r"[^\W_]+", piece.casefold(), flags=re.UNICODE):
                if token and token not in seen:
                    seen.add(token)
                    parts.append(token)
        merged = " ".join(parts)
        return merged or self.original


def _cache_key(question: str, provider: str | None) -> str:
    raw = f"{(question or '').casefold().strip()}|{provider or ''}"
    return hashlib.blake2b(raw.encode("utf-8"), digest_size=16).hexdigest()


def _is_reasoning_model(model: str | None) -> bool:
    """Heuristic for models that spend tokens on chain-of-thought.

    Mirrors ``GroundedResponder._is_reasoning_model`` but kept module-local so
    the rewriter has no dependency on the responder. Disabling thinking for a
    non-reasoning model is harmless — the override is simply ignored.
    """
    name = (model or "").lower()
    if not name:
        return False
    return any(
        marker in name
        for marker in (
            "qwen3",
            "qwen-3",
            "deepseek-r1",
            "r1-",
            "glm-4.5",
            "glm-5",
            "gpt-oss",
            "reasoning",
        )
    )


def _apply_thinking_control(
    overrides: dict[str, Any], router: LLMRouter, provider: str | None
) -> None:
    """Disable thinking for reasoning models by injecting ``chat_template_kwargs``.

    The Ollama path in ``LLMRouter._ollama_request`` translates
    ``chat_template_kwargs.enable_thinking=False`` into the top-level
    ``think: false`` field. The OpenAI-compatible path passes it through. A
    caller-supplied ``chat_template_kwargs`` always wins.

    The model check must use the **effective** model — the explicit
    ``overrides["model"]`` (what the caller actually asked for), not
    the provider default. The default (e.g. ``qwen3.5:9b``) may be a reasoning
    model while the effective model (e.g. ``glm-5.3-flash:cloud``) is not, and
    a spurious ``enable_thinking=False`` makes cloud-managed Ollama models
    return an empty payload.
    """
    try:
        model = str(overrides.get("model") or "").strip()
        if not model:
            model = router.provider_default_model(provider) or ""
        # Cloud-managed Ollama models (``:cloud``) return an empty payload
        # when ``think: false`` is sent. Detecting "reasoning" via model name
        # alone is not enough — glm-5.x-flash:cloud is not a thinking model.
        # Exclude any cloud-hosted tag from thinking-control: the user picked
        # the cloud variant for convenience, and disabling thinking breaks it.
        if model.endswith(":cloud"):
            return
    except Exception:
        return
    if not _is_reasoning_model(model):
        return
    existing_extra = dict(overrides.get("extra") or {})
    if "chat_template_kwargs" in existing_extra:
        return
    existing_extra["chat_template_kwargs"] = {"enable_thinking": False}
    overrides["extra"] = existing_extra


def _coerce_keywords(value: Any) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return ()
    out: list[str] = []
    seen: set[str] = set()
    for item in value:
        text = str(item or "").strip()
        if not text or text.casefold() in seen:
            continue
        seen.add(text.casefold())
        out.append(text)
    return tuple(out[:8])


def _parse_payload(payload: Any) -> RewriteResult | None:
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except (ValueError, TypeError):
            return None
    if not isinstance(payload, dict):
        return None
    en_query = str(payload.get("en_query") or "").strip()
    keywords = _coerce_keywords(payload.get("keywords"))
    language = str(payload.get("language") or "").strip().lower()
    if not en_query and not keywords:
        return None
    if not en_query:
        en_query = " ".join(keywords)
    return RewriteResult(
        original="",
        en_query=en_query,
        keywords=keywords,
        language=language,
        used_llm=True,
    )


class QueryRewriter:
    """Rewrites a user question into an English retrieval query via the LLM.

    Instances are cheap and stateless except for an in-memory result cache keyed
    by (question, provider). A single rewriter can be shared across the initial
    answer and all auto-research harvest stages of one user turn.
    """

    def __init__(self, llm_router: LLMRouter | None) -> None:
        self._llm_router = llm_router
        self._cache: dict[str, RewriteResult] = {}

    def rewrite(
        self,
        question: str,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> RewriteResult:
        """Return a :class:`RewriteResult` for *question*.

        Falls back to the original question (``used_llm=False``) whenever the
        router is missing or the LLM call fails, so callers always get a usable
        ``retrieval_query``.
        """
        original = question or ""
        key = _cache_key(original, provider) + repr(sorted((overrides or {}).items()))
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        result = self._rewrite_uncached(original, provider, overrides)
        self._cache[key] = result
        return result

    def _rewrite_uncached(
        self,
        original: str,
        provider: str | None,
        overrides: dict[str, Any] | None,
    ) -> RewriteResult:
        if not original.strip():
            return _fallback(original)
        if self._llm_router is None:
            # No router at all → offline dictionary, then original.
            return _offline_fallback(original)
        # Reasoning models (Qwen3, deepseek-r1, …) would burn the small rewrite
        # token budget on chain-of-thought and leave ``content`` empty. Disable
        # thinking up front so the JSON rewrite comes back reliably. The Ollama
        # path translates this to the top-level ``think: false`` field; the
        # OpenAI-compatible path passes it through as ``chat_template_kwargs``.
        merged = {**(overrides or {}), "temperature": 0.0, "max_tokens": 256}
        _apply_thinking_control(merged, self._llm_router, provider)
        messages = [
            {"role": "system", "content": f"{_SYSTEM_PROMPT} {_SCHEMA_HINT}"},
            {"role": "user", "content": f"Question:\n{original.strip()}"},
        ]
        try:
            payload = self._llm_router.chat_json(
                messages, provider=provider, overrides=merged
            )
        except Exception as exc:
            # LLM call failed → try the offline dictionary before degrading to
            # the raw original. This is the path that fires when the configured
            # provider (e.g. LM Studio) is down.
            return _offline_fallback(original, error=str(exc))
        parsed = _parse_payload(payload)
        if parsed is None:
            return _offline_fallback(
                original,
                error=f"malformed payload: {json.dumps(payload)[:160]}",
            )
        return RewriteResult(
            original=original,
            en_query=parsed.en_query,
            keywords=parsed.keywords,
            language=parsed.language,
            used_llm=True,
        )


def _offline_fallback(original: str, error: str = "") -> RewriteResult:
    """Dictionary-based fallback used when the LLM is unreachable/missing.

    If the offline dictionary can translate at least one content word, the
    returned ``en_query`` (and thus ``retrieval_query``) differs from the
    original so the dual-query merge in ``_retrieve_grounded`` fires. If no
    dictionary term matched, degrade to the original question unchanged.
    """
    translated = _offline_rewrite(original)
    if translated and translated.strip() and translated.strip() != original.strip():
        return RewriteResult(
            original=original,
            en_query=translated,
            keywords=(),
            language="de" if _looks_german(original) else "",
            used_llm=False,
            error=error,
        )
    return _fallback(original, error=error)


def _looks_german(text: str) -> bool:
    """Cheap heuristic: German-specific characters or common function words."""
    if not text:
        return False
    if any(ch in text for ch in "äöüß"):
        return True
    lowered = text.casefold()
    return any(
        word in lowered
        for word in (
            " der ",
            " die ",
            " das ",
            " und ",
            " wie ",
            " was ",
            " ist ",
            " nicht ",
            " ein ",
            " eine ",
            " von ",
            " mit ",
            " zu ",
            " auf ",
        )
    )


def _fallback(original: str, error: str = "") -> RewriteResult:
    return RewriteResult(
        original=original,
        en_query=original,
        keywords=(),
        language="",
        used_llm=False,
        error=error,
    )
