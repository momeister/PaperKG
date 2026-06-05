from __future__ import annotations

from dataclasses import dataclass
from typing import Any


VALID_CONTEXT_POLICIES = {"auto", "whole", "chunk"}


@dataclass(frozen=True)
class ContextBudgetDecision:
    context_policy: str
    whole_context_used: bool
    chunk_count: int
    estimated_prompt_tokens: int
    context_margin_tokens: int
    fallback_reason: str | None
    context_size: int
    max_tokens: int
    provider: str | None = None
    model: str | None = None
    estimated_text_tokens: int = 0
    prompt_overhead_tokens: int = 0
    output_reserve_tokens: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "context_policy": self.context_policy,
            "whole_context_used": self.whole_context_used,
            "chunk_count": self.chunk_count,
            "estimated_prompt_tokens": self.estimated_prompt_tokens,
            "context_margin_tokens": self.context_margin_tokens,
            "fallback_reason": self.fallback_reason,
            "context_size": self.context_size,
            "max_tokens": self.max_tokens,
            "provider": self.provider,
            "model": self.model,
            "estimated_text_tokens": self.estimated_text_tokens,
            "prompt_overhead_tokens": self.prompt_overhead_tokens,
            "output_reserve_tokens": self.output_reserve_tokens,
        }


def normalize_context_policy(value: Any) -> str:
    policy = str(value or "auto").strip().lower()
    return policy if policy in VALID_CONTEXT_POLICIES else "auto"


def estimate_text_tokens(text: str, chars_per_token: float = 3.6) -> int:
    clean_length = len(str(text or ""))
    if clean_length <= 0:
        return 0
    return max(1, int(clean_length / max(chars_per_token, 0.1)) + 1)


def effective_generation_limits(
    llm_router: Any | None,
    provider: str | None,
    overrides: dict[str, Any] | None = None,
    *,
    default_context_size: int = 32768,
    default_max_tokens: int = 8192,
) -> tuple[int, int, str | None]:
    overrides = dict(overrides or {})
    model = str(overrides.get("model") or "").strip() or None
    try:
        settings = llm_router.provider_settings(provider) if llm_router is not None else None
    except Exception:
        settings = None

    configured_context = getattr(settings, "context_size", default_context_size)
    configured_max_tokens = getattr(settings, "max_tokens", default_max_tokens)
    configured_model = getattr(settings, "model", None)

    context_size = _safe_int(overrides.get("context_size"), _safe_int(configured_context, default_context_size))
    max_tokens = _safe_int(overrides.get("max_tokens"), _safe_int(configured_max_tokens, default_max_tokens))
    return max(1024, context_size), max(1, max_tokens), model or (str(configured_model) if configured_model else None)


def decide_whole_context(
    *,
    text: str,
    context_policy: str,
    context_size: int,
    max_tokens: int,
    prompt_overhead_tokens: int,
    output_reserve_tokens: int | None = None,
    chunk_count_if_fallback: int = 1,
    provider: str | None = None,
    model: str | None = None,
) -> ContextBudgetDecision:
    policy = normalize_context_policy(context_policy)
    text_tokens = estimate_text_tokens(text)
    reserve = max(int(output_reserve_tokens if output_reserve_tokens is not None else max_tokens), max_tokens)
    estimated_prompt_tokens = int(prompt_overhead_tokens) + text_tokens
    context_margin = int(context_size) - estimated_prompt_tokens - reserve
    fits = context_margin >= 0

    if policy == "chunk":
        return ContextBudgetDecision(
            context_policy=policy,
            whole_context_used=False,
            chunk_count=max(1, int(chunk_count_if_fallback)),
            estimated_prompt_tokens=estimated_prompt_tokens,
            context_margin_tokens=context_margin,
            fallback_reason="forced_chunk_policy",
            context_size=int(context_size),
            max_tokens=int(max_tokens),
            provider=provider,
            model=model,
            estimated_text_tokens=text_tokens,
            prompt_overhead_tokens=int(prompt_overhead_tokens),
            output_reserve_tokens=reserve,
        )

    if fits:
        return ContextBudgetDecision(
            context_policy=policy,
            whole_context_used=True,
            chunk_count=1,
            estimated_prompt_tokens=estimated_prompt_tokens,
            context_margin_tokens=context_margin,
            fallback_reason=None,
            context_size=int(context_size),
            max_tokens=int(max_tokens),
            provider=provider,
            model=model,
            estimated_text_tokens=text_tokens,
            prompt_overhead_tokens=int(prompt_overhead_tokens),
            output_reserve_tokens=reserve,
        )

    return ContextBudgetDecision(
        context_policy=policy,
        whole_context_used=False,
        chunk_count=max(1, int(chunk_count_if_fallback)),
        estimated_prompt_tokens=estimated_prompt_tokens,
        context_margin_tokens=context_margin,
        fallback_reason="context_budget_exceeded",
        context_size=int(context_size),
        max_tokens=int(max_tokens),
        provider=provider,
        model=model,
        estimated_text_tokens=text_tokens,
        prompt_overhead_tokens=int(prompt_overhead_tokens),
        output_reserve_tokens=reserve,
    )


def _safe_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)
