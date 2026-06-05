from __future__ import annotations

from query.context_budget import decide_whole_context, normalize_context_policy


def test_auto_uses_whole_context_when_text_fits() -> None:
    decision = decide_whole_context(
        text="short paper text " * 40,
        context_policy="auto",
        context_size=16000,
        max_tokens=1000,
        prompt_overhead_tokens=500,
        output_reserve_tokens=1000,
        chunk_count_if_fallback=3,
        provider="lm_studio",
        model="local-model",
    )

    assert decision.whole_context_used is True
    assert decision.chunk_count == 1
    assert decision.fallback_reason is None
    assert decision.context_margin_tokens > 0
    assert decision.provider == "lm_studio"
    assert decision.model == "local-model"


def test_auto_falls_back_to_chunks_when_margin_is_negative() -> None:
    decision = decide_whole_context(
        text="long paper text " * 3000,
        context_policy="auto",
        context_size=4096,
        max_tokens=2048,
        prompt_overhead_tokens=1500,
        output_reserve_tokens=2048,
        chunk_count_if_fallback=7,
    )

    assert decision.whole_context_used is False
    assert decision.chunk_count == 7
    assert decision.fallback_reason == "context_budget_exceeded"
    assert decision.context_margin_tokens < 0


def test_chunk_policy_never_uses_whole_context() -> None:
    decision = decide_whole_context(
        text="short paper text",
        context_policy="chunk",
        context_size=16000,
        max_tokens=1000,
        prompt_overhead_tokens=500,
        output_reserve_tokens=1000,
        chunk_count_if_fallback=2,
    )

    assert decision.whole_context_used is False
    assert decision.chunk_count == 2
    assert decision.fallback_reason == "forced_chunk_policy"


def test_invalid_context_policy_defaults_to_auto_for_direct_calls() -> None:
    assert normalize_context_policy("tiny") == "auto"
