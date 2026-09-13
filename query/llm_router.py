from __future__ import annotations

from contextvars import ContextVar

import json
import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

# Reasoning models (Qwen3, DeepSeek-R1, ...) emit <think>...</think> blocks before the
# actual answer. LM Studio and Ollama return them inline in `content`, which corrupts
# downstream parsing (citation brackets inside the reasoning get picked up, JSON
# extraction grabs the wrong braces). Strip them centrally so every caller is safe.
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
_THINK_OPEN_RE = re.compile(r"<think>", re.IGNORECASE)
_THINK_CLOSE_RE = re.compile(r"</think>", re.IGNORECASE)


def strip_reasoning_blocks(text: str, *, metadata: dict[str, Any] | None = None) -> str:
    cleaned = _THINK_BLOCK_RE.sub("", str(text or ""))
    # Some server templates prefill the opening tag, so only the closing tag
    # appears in content. The prefix is still reasoning, including any JSON in it.
    closing = list(_THINK_CLOSE_RE.finditer(cleaned))
    if closing:
        cleaned = cleaned[closing[-1].end() :]
    open_match = _THINK_OPEN_RE.search(cleaned)
    if open_match:
        # Unterminated think block: the model hit its token limit while reasoning.
        # Everything after the unmatched opening tag is reasoning, not answer.
        cleaned = cleaned[: open_match.start()]
        if metadata is not None:
            # Reasoning models (deepseek-r1, o3) burn their whole budget thinking
            # fairly often; the caller must distinguish that from "no answer".
            metadata["reasoning_truncated"] = True
    return cleaned.strip()


def _repair_json_safe(raw: str) -> dict[str, Any]:
    """Best-effort JSON-Reparatur via ``json_repair`` (optionale Abhängigkeit).

    Löst das "Expecting ',' delimiter: line N column M (char 2048)"-Problem,
    wenn ein lokales LLM (z. B. DeepSeek über Ollama) bei zu knapp gewähltem
    ``max_tokens`` mitten im JSON abgeschnitten wird. Liefert bei völligem
    Müll ein leeres Dict statt eine Exception zu werfen — der Aufrufer
    (``chat_json``) bekommt dann ein leeres Spec und kann kontrolliert reagieren.
    """
    try:
        from json_repair import repair_json  # type: ignore[import-not-found]
    except Exception:  # noqa: BLE001 — json_repair ist optional
        return {}
    try:
        fixed = repair_json(raw, return_objects=True)
    except Exception:  # noqa: BLE001
        return {}
    return fixed if isinstance(fixed, dict) else {}


@dataclass
class GenerationSettings:
    model: str
    temperature: float = 0.2
    top_p: float = 0.95
    max_tokens: int = 2048
    context_size: int = 32768
    repeat_penalty: float | None = 1.05
    seed: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass
class ToolCall:
    """Ein Werkzeugaufruf, so wie ein Anbieter ihn geliefert hat.

    ``arguments`` bleibt absichtlich der **rohe JSON-String**: OpenAI, LM Studio
    und NVIDIA liefern ihn so, Ollama und Anthropic liefern ein Objekt, das hier
    wieder serialisiert wird. Wer die Argumente an eine Gegenstelle weiterreicht,
    die selbst parst (der Code-Graph tut genau das), soll nicht erst raten
    müssen, ob er ein Dict oder einen Text in der Hand hält.
    """

    id: str
    name: str
    arguments: str


@dataclass
class ProviderConfig:
    provider_type: str
    base_url: str
    api_key: str | None = None
    timeout_seconds: float = 120.0
    settings: GenerationSettings | None = None
    available_models: list[str] = field(default_factory=list)


class LLMRouter:
    """
    Unified router for Ollama, LM Studio, OpenAI-compatible and Anthropic endpoints.

    Message `content` may be a plain string or an OpenAI-style parts list
    (`{"type": "text", ...}` / `{"type": "image_url", "image_url": {"url": "data:..."}}`);
    parts are translated per provider (Ollama `images`, Anthropic content blocks).
    """

    def __init__(
        self,
        providers: dict[str, ProviderConfig],
        default_provider: str,
        client: httpx.Client | None = None,
    ) -> None:
        if default_provider not in providers:
            raise ValueError(f"Unknown default provider: {default_provider}")
        self.providers = providers
        self.default_provider = default_provider
        self._client = client
        self._response_metadata: ContextVar[dict[str, Any] | None] = ContextVar("llm_response_metadata", default=None)

    @property
    def last_response_metadata(self) -> dict[str, Any]:
        metadata = self._response_metadata.get()
        if metadata is None:
            metadata = {}
            self._response_metadata.set(metadata)
        return metadata

    @last_response_metadata.setter
    def last_response_metadata(self, value: dict[str, Any]) -> None:
        self._response_metadata.set(value)

    @classmethod
    def from_config_file(cls, config_path: str | Path = "config.yaml") -> "LLMRouter":
        path = Path(config_path)
        cls._load_dotenv(path.parent / ".env")
        with path.open("r", encoding="utf-8") as fh:
            config = yaml.safe_load(fh) or {}

        llm_cfg = config.get("llm") or {}
        default_provider = llm_cfg.get("default_provider", "ollama")

        providers: dict[str, ProviderConfig] = {}
        for name, raw in (llm_cfg.get("providers") or {}).items():
            # Handle None values for optional float fields
            repeat_penalty_val = raw.get("repeat_penalty")
            repeat_penalty = (
                float(repeat_penalty_val) if repeat_penalty_val is not None else None
            )
            available_models = [
                str(model) for model in (raw.get("models") or []) if model
            ]

            settings = GenerationSettings(
                model=raw.get("model", ""),
                temperature=(
                    float(raw.get("temperature", 0.2))
                    if raw.get("temperature") is not None
                    else 0.2
                ),
                top_p=(
                    float(raw.get("top_p", 0.95))
                    if raw.get("top_p") is not None
                    else 0.95
                ),
                max_tokens=(
                    int(raw.get("max_tokens", 2048))
                    if raw.get("max_tokens") is not None
                    else 2048
                ),
                context_size=(
                    int(raw.get("context_size", 32768))
                    if raw.get("context_size") is not None
                    else 32768
                ),
                repeat_penalty=repeat_penalty,
                seed=raw.get("seed"),
                extra=dict(raw.get("extra_options") or {}),
            )

            api_key = raw.get("api_key")
            env_name = raw.get("api_key_env")
            if env_name:
                api_key = os.getenv(env_name, api_key)
            base_url = str(raw.get("base_url", "http://localhost:11434"))
            if (
                not api_key
                and str(raw.get("provider_type", "")).lower() == "nvidia"
                and "integrate.api.nvidia.com" in base_url.lower()
            ):
                api_key = os.getenv("NGC_API_KEY")

            providers[name] = ProviderConfig(
                provider_type=str(raw.get("provider_type", "ollama")),
                base_url=base_url,
                api_key=api_key,
                timeout_seconds=float(raw.get("timeout_seconds", 120.0)),
                settings=settings,
                available_models=available_models,
            )

        if not providers:
            # Safe fallback when no llm section exists yet.
            providers = {
                "ollama": ProviderConfig(
                    provider_type="ollama",
                    base_url="http://localhost:11434",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                    available_models=["qwen3.6-35b"],
                )
            }
            default_provider = "ollama"

        return cls(providers=providers, default_provider=default_provider)

    def available_providers(self) -> list[str]:
        return sorted(self.providers.keys())

    def provider_config(self, provider: str | None = None) -> ProviderConfig:
        provider_name = provider or self.default_provider
        if provider_name not in self.providers:
            raise ValueError(f"Unknown provider: {provider_name}")
        return self.providers[provider_name]

    def provider_settings(self, provider: str | None = None) -> GenerationSettings:
        cfg = self.provider_config(provider)
        return cfg.settings or GenerationSettings(model="qwen3.6-35b")

    def discover_provider_models(self, provider: str | None = None) -> list[str]:
        cfg = self.provider_config(provider)
        client = self._client_for(cfg.timeout_seconds)
        models: list[str] = []

        try:
            if cfg.provider_type == "ollama":
                response = client.get(f"{cfg.base_url.rstrip('/')}/api/tags")
                response.raise_for_status()
                payload = response.json()
                models = [
                    str(item.get("name"))
                    for item in payload.get("models", [])
                    if item.get("name")
                ]
            elif cfg.provider_type in {
                "openai_compatible",
                "lm_studio",
                "openai",
                "nvidia",
            }:
                headers = {"Content-Type": "application/json"}
                if cfg.api_key:
                    headers["Authorization"] = f"Bearer {cfg.api_key}"
                response = client.get(
                    f"{cfg.base_url.rstrip('/')}/models", headers=headers
                )
                response.raise_for_status()
                payload = response.json()
                models = [
                    str(item.get("id") or item.get("name"))
                    for item in payload.get("data", [])
                    if item.get("id") or item.get("name")
                ]
            elif cfg.provider_type == "anthropic":
                headers = {"anthropic-version": "2023-06-01"}
                if cfg.api_key:
                    headers["x-api-key"] = cfg.api_key
                response = client.get(
                    f"{cfg.base_url.rstrip('/')}/v1/models", headers=headers
                )
                response.raise_for_status()
                payload = response.json()
                models = [
                    str(item.get("id"))
                    for item in payload.get("data", [])
                    if item.get("id")
                ]
        except Exception:
            models = []

        return models

    def provider_model_options(
        self, provider: str | None = None, refresh: bool = False
    ) -> list[str]:
        cfg = self.provider_config(provider)
        models = self.discover_provider_models(provider) if refresh else []
        models.extend([model for model in cfg.available_models if model])
        if cfg.settings and cfg.settings.model and cfg.settings.model not in models:
            models.insert(0, cfg.settings.model)
        if not models:
            models = [
                (
                    cfg.settings.model
                    if cfg.settings and cfg.settings.model
                    else "qwen3.6-35b"
                )
            ]
        seen: set[str] = set()
        unique_models: list[str] = []
        for model in models:
            if model not in seen:
                seen.add(model)
                unique_models.append(model)
        return unique_models

    def provider_default_model(self, provider: str | None = None) -> str:
        return self.provider_settings(provider).model

    def recommended_settings(
        self,
        provider: str | None = None,
        model: str | None = None,
        refresh: bool = True,
    ) -> GenerationSettings:
        """
        Return extraction-oriented defaults, enriched with provider model metadata when available.
        """
        cfg = self.provider_config(provider)
        base = self.provider_settings(provider)
        settings = GenerationSettings(
            model=model or base.model,
            temperature=base.temperature,
            top_p=base.top_p,
            max_tokens=max(base.max_tokens, 16384),
            context_size=base.context_size,
            repeat_penalty=base.repeat_penalty,
            seed=base.seed,
            extra=dict(base.extra),
        )

        model_lower = settings.model.lower()
        if "qwen" in model_lower:
            settings.temperature = min(settings.temperature, 0.2)
            settings.top_p = min(settings.top_p, 0.9)
        elif "llama" in model_lower:
            settings.temperature = min(settings.temperature, 0.2)
            settings.top_p = min(settings.top_p, 0.9)
        elif "mistral" in model_lower:
            settings.temperature = min(settings.temperature, 0.15)
            settings.top_p = min(settings.top_p, 0.9)
        elif "deepseek" in model_lower:
            # Reasoning-Varianten (r1, reasoner) brauchen hoehere Temperatur — unter
            # 0.5 fangen sie an, sich zu wiederholen (Herstellerempfehlung 0.6).
            if "r1" in model_lower or "reason" in model_lower:
                settings.temperature = max(settings.temperature, 0.6)
                settings.top_p = min(settings.top_p, 0.95)
            else:
                settings.temperature = min(settings.temperature, 0.3)
                settings.top_p = min(settings.top_p, 0.9)

        if refresh and cfg.provider_type == "ollama":
            model_settings = self.discover_ollama_model_settings(cfg, settings.model)
            if model_settings:
                settings = self._merged_settings(settings, model_settings)
                settings.max_tokens = max(settings.max_tokens, 16384)

        return settings

    def discover_ollama_model_settings(
        self, cfg: ProviderConfig, model: str
    ) -> dict[str, Any]:
        client = self._client_for(cfg.timeout_seconds)
        try:
            response = client.post(
                f"{cfg.base_url.rstrip('/')}/api/show", json={"model": model}
            )
            response.raise_for_status()
            payload = response.json()
        except Exception:
            return {}

        raw_parameters = payload.get("parameters") or ""
        if isinstance(raw_parameters, list):
            raw_parameters = "\n".join(str(item) for item in raw_parameters)
        if not isinstance(raw_parameters, str):
            return {}

        mapping = {
            "temperature": "temperature",
            "top_p": "top_p",
            "num_ctx": "context_size",
            "num_predict": "max_tokens",
            "repeat_penalty": "repeat_penalty",
        }
        settings: dict[str, Any] = {}
        for line in raw_parameters.splitlines():
            parts = line.strip().split(None, 1)
            if len(parts) != 2 or parts[0] not in mapping:
                continue
            key = mapping[parts[0]]
            value = parts[1].strip()
            try:
                settings[key] = (
                    int(value)
                    if key in {"context_size", "max_tokens"}
                    else float(value)
                )
            except ValueError:
                continue
        return settings

    def chat(
        self,
        messages: list[dict[str, Any]],
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> str:
        provider_name = provider or self.default_provider
        cfg = self.provider_config(provider_name)
        settings = self._merged_settings(cfg.settings, overrides)
        request_timeout_seconds = float(
            (overrides or {}).get("timeout_seconds", cfg.timeout_seconds)
        )

        self.last_response_metadata = {}
        try:
            if cfg.provider_type == "ollama":
                result = self._chat_ollama(cfg, messages, settings, request_timeout_seconds)
            elif cfg.provider_type == "anthropic":
                result = self._chat_anthropic(cfg, messages, settings, request_timeout_seconds)
            elif cfg.provider_type in {"openai_compatible", "lm_studio", "openai", "nvidia"}:
                result = self._chat_openai_compatible(cfg, messages, settings, request_timeout_seconds)
            else:
                raise ValueError(f"Unsupported provider type: {cfg.provider_type}")
        except Exception:
            self.last_response_metadata.update({"requested_provider": provider_name, "requested_model": settings.model, "request_failed": True})
            raise
        self.last_response_metadata.update({"provider": provider_name, "model": self.last_response_metadata.get("model") or settings.model})
        return result

    def chat_with_tools(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> tuple[str | None, list[ToolCall]]:
        """Wie :meth:`chat`, aber mit Werkzeugen — Text *und* Aufrufe zurück.

        Bewusst eine Schwestermethode und keine Erweiterung von :meth:`chat`:
        ``chat()`` gibt einen ``str`` zurück und wird im ganzen Repo so benutzt;
        ein anderer Rückgabetyp wäre ein Bruch quer durch alles.

        ``messages`` ist immer in **OpenAI-Form** — auch für Anthropic und Ollama,
        die hier übersetzt werden. Ein Aufrufer, der die Werkzeugschleife fährt,
        soll nicht pro Anbieter eine andere Nachrichtenform bauen müssen; er hängt
        die Antwort als ``{"role": "assistant", "tool_calls": [...]}`` und jedes
        Ergebnis als ``{"role": "tool", "tool_call_id": ..., "content": ...}`` an.

        Antwortet ein Server auf ``tools`` mit 400/422 — nicht jedes lokale Modell
        kann Tool-Calling, und manche Server lehnen das Feld ab, statt es zu
        ignorieren —, wird der Aufruf ohne Werkzeuge wiederholt. Dann steht
        ``tool_calling_fallback`` in :attr:`last_response_metadata`, und der
        Aufrufer weiss, dass er auf Prompt-and-Parse umschalten muss.
        """
        provider_name = provider or self.default_provider
        cfg = self.provider_config(provider_name)
        settings = self._merged_settings(cfg.settings, overrides)
        request_timeout_seconds = float(
            (overrides or {}).get("timeout_seconds", cfg.timeout_seconds)
        )

        if cfg.provider_type == "ollama":
            message = self._ollama_request(
                cfg, messages, settings, request_timeout_seconds, tools
            )
            return self._openai_style_result(message)
        if cfg.provider_type == "anthropic":
            return self._anthropic_request(
                cfg, messages, settings, request_timeout_seconds, tools
            )
        if cfg.provider_type in {"openai_compatible", "lm_studio", "openai", "nvidia"}:
            message = self._openai_request(
                cfg, messages, settings, request_timeout_seconds, tools
            )
            return self._openai_style_result(message)

        raise ValueError(f"Unsupported provider type: {cfg.provider_type}")

    def _openai_style_result(
        self, message: dict[str, Any]
    ) -> tuple[str | None, list[ToolCall]]:
        content = str(message.get("content", "") or "")
        if not content.strip():
            content = str(message.get("reasoning_content", "") or "")
            self.last_response_metadata["reasoning_fallback"] = bool(content.strip())
        text = strip_reasoning_blocks(content, metadata=self.last_response_metadata)
        return (text or None), self._tool_calls_from_openai(message)

    @staticmethod
    def _tool_calls_from_openai(message: dict[str, Any]) -> list[ToolCall]:
        calls: list[ToolCall] = []
        for index, raw in enumerate(message.get("tool_calls") or []):
            if not isinstance(raw, dict):
                continue
            function = raw.get("function") or {}
            name = str(function.get("name") or "")
            if not name:
                continue
            arguments = function.get("arguments")
            if not isinstance(arguments, str):
                # Ollama liefert ein Objekt; die Gegenstelle bekommt trotzdem Text.
                arguments = json.dumps(
                    arguments if arguments is not None else {}, ensure_ascii=False
                )
            # Ollama vergibt keine Aufruf-IDs. Eine synthetische reicht: sie muss
            # nur das Ergebnis wieder seinem Aufruf zuordnen koennen.
            calls.append(
                ToolCall(
                    id=str(raw.get("id") or f"call_{index}"),
                    name=name,
                    arguments=arguments,
                )
            )
        return calls

    def check_provider_auth(
        self,
        provider: str | None = None,
        model: str | None = None,
        timeout_seconds: float = 30.0,
    ) -> tuple[bool, str | None]:
        """Run a tiny chat request to catch auth/config failures before extraction."""
        try:
            overrides: dict[str, Any] = {
                "temperature": 0.0,
                "top_p": 1.0,
                "max_tokens": 8,
                "timeout_seconds": timeout_seconds,
            }
            if model:
                overrides["model"] = model
            self.chat(
                [{"role": "user", "content": "Reply with OK."}],
                provider=provider,
                overrides=overrides,
            )
        except Exception as exc:
            return False, str(exc)
        return True, None

    @staticmethod
    def is_auth_error(error: str | None) -> bool:
        """Return true when an upstream provider error is clearly authorization-related."""
        if not error:
            return False
        error_lower = error.lower()
        auth_markers = (
            "401 unauthorized",
            "403 forbidden",
            "authorization failed",
            "unauthorized",
            "forbidden",
            "invalid api key",
            "incorrect api key",
            "api key",
        )
        return any(marker in error_lower for marker in auth_markers)

    def chat_json(
        self,
        messages: list[dict[str, Any]],
        provider: str | None = None,
        overrides: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        response_text = self.chat(
            messages=messages, provider=provider, overrides=overrides
        )
        return self._extract_json(response_text)

    def _client_for(self, timeout_seconds: float) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=timeout_seconds)

    @staticmethod
    def _load_dotenv(env_path: Path) -> None:
        if not env_path.exists():
            return
        try:
            from dotenv import load_dotenv
        except Exception:
            return
        load_dotenv(dotenv_path=env_path, override=False)

    @staticmethod
    def _merged_settings(
        base: GenerationSettings | None, overrides: dict[str, Any] | None
    ) -> GenerationSettings:
        base = base or GenerationSettings(model="qwen3.6-35b")
        if not overrides:
            return base

        repeat_penalty_override = overrides.get("repeat_penalty", base.repeat_penalty)
        repeat_penalty = (
            float(repeat_penalty_override)
            if repeat_penalty_override is not None
            else None
        )

        return GenerationSettings(
            model=str(overrides.get("model", base.model)),
            temperature=float(overrides.get("temperature", base.temperature)),
            top_p=float(overrides.get("top_p", base.top_p)),
            max_tokens=int(overrides.get("max_tokens", base.max_tokens)),
            context_size=int(overrides.get("context_size", base.context_size)),
            repeat_penalty=repeat_penalty,
            seed=overrides.get("seed", base.seed),
            extra={**base.extra, **dict(overrides.get("extra", {}))},
        )

    def _chat_ollama(
        self,
        cfg: ProviderConfig,
        messages: list[dict[str, Any]],
        settings: GenerationSettings,
        request_timeout_seconds: float,
    ) -> str:
        message = self._ollama_request(cfg, messages, settings, request_timeout_seconds)
        content = str(message.get("content", "") or "")
        if not content.strip():
            # Ollama legt den Chain-of-Thought Reasoning-Modelle (deepseek-r1) in
            # ``message.thinking`` und laesst ``content`` leer — dasselbe Bild wie
            # ``reasoning_content`` auf dem OpenAI-Pfad.
            content = str(message.get("thinking", "") or "")
            self.last_response_metadata["reasoning_fallback"] = bool(content.strip())
        return strip_reasoning_blocks(content, metadata=self.last_response_metadata)

    def _ollama_request(
        self,
        cfg: ProviderConfig,
        messages: list[dict[str, Any]],
        settings: GenerationSettings,
        request_timeout_seconds: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Ein Aufruf gegen ``/api/chat`` — liefert die ganze ``message`` zurueck.

        :meth:`chat` braucht davon nur ``content``, die Werkzeugschleife braucht
        zusaetzlich ``tool_calls``. Deshalb die ganze Nachricht.
        """
        messages = [self._ollama_message(message) for message in messages]
        extra_options = dict(settings.extra)
        keep_alive = extra_options.pop("keep_alive", "0s")
        response_format = extra_options.pop("format", None)
        json_mode = bool(extra_options.pop("json_mode", False))
        extra_options.pop("response_format", None)
        # ``chat_template_kwargs`` ist ein OpenAI/LM-Studio-Konstrukt; Ollama kennt
        # es nicht. Werfen war die alte Loesung — damit hatte ``enable_thinking:
        # false`` fuer ein Reasoning-Modell (deepseek) unter Ollama *keine* Wirkung.
        # Ollama schaltet Denken seit 0.5+0 ueber das Top-Level-Feld ``think``
        # (POST /api/chat). Also uebersetzen, nicht verwerfen.
        chat_template_kwargs = extra_options.pop("chat_template_kwargs", None)
        if isinstance(chat_template_kwargs, dict):
            enable_thinking = chat_template_kwargs.get("enable_thinking")
            if enable_thinking is False:
                payload_think = False
            elif enable_thinking is True:
                payload_think = True
            else:
                payload_think = None
        else:
            payload_think = None
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
            "options": {
                "temperature": settings.temperature,
                "top_p": settings.top_p,
                "num_ctx": settings.context_size,
                "num_predict": settings.max_tokens,
                "repeat_penalty": settings.repeat_penalty,
                **extra_options,
            },
        }
        if response_format is not None:
            payload["format"] = response_format
        elif json_mode:
            payload["format"] = "json"
        if payload_think is not None:
            payload["think"] = payload_think
        payload["options"] = self._drop_none_values(payload["options"])
        if settings.seed is not None:
            payload["options"]["seed"] = settings.seed
        if tools:
            payload["tools"] = tools

        client = self._client_for(request_timeout_seconds)
        endpoint = f"{cfg.base_url.rstrip('/')}/api/chat"
        flags = {
            "tool_calling_fallback": False,
            "response_format_fallback": False,
            "thinking_control_fallback": False,
        }
        # Optional capabilities are negotiated from server errors, never model names.
        # Each rejected field is removed at most once; authentication/quota errors
        # retain their HTTP status and never enter this fallback.
        while True:
            response = client.post(endpoint, json=payload)
            try:
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                if exc.response.status_code not in {400, 422}:
                    raise self._http_status_runtime_error(exc) from exc
                detail = exc.response.text.casefold()
                field = None
                if "think" in payload and "think" in detail:
                    field, flag = "think", "thinking_control_fallback"
                elif "format" in payload and ("format" in detail or "schema" in detail):
                    field, flag = "format", "response_format_fallback"
                elif "tools" in payload:
                    field, flag = "tools", "tool_calling_fallback"
                if field is None:
                    raise self._http_status_runtime_error(exc) from exc
                payload = {key: value for key, value in payload.items() if key != field}
                flags[flag] = True
        data = response.json()
        self.last_response_metadata = {
            "model": data.get("model"),
            "provider_type": "ollama",
            "eval_count": data.get("eval_count"),
            "prompt_eval_count": data.get("prompt_eval_count"),
            "total_duration": data.get("total_duration"),
            "load_duration": data.get("load_duration"),
            "done_reason": data.get("done_reason"),
            **flags,
            "reasoning_fallback": False,
            "reasoning_truncated": False,
        }
        return data.get("message") or {}

    @classmethod
    def _ollama_message(cls, message: dict[str, Any]) -> dict[str, Any]:
        """Translate an OpenAI-style parts message into Ollama's content + images shape."""
        if message.get("tool_calls"):
            # Ollama will die Argumente als Objekt, OpenAI liefert sie als String.
            # Ohne diese Ruecknahme sieht das Modell in der naechsten Runde seinen
            # eigenen Aufruf als Zeichenkette und ruft munter noch einmal auf.
            return {
                **message,
                "content": cls._flatten_text_content(message.get("content")),
                "tool_calls": [
                    {
                        "function": {
                            "name": str((call.get("function") or {}).get("name", "")),
                            "arguments": cls._loads_or_empty(
                                (call.get("function") or {}).get("arguments")
                            ),
                        }
                    }
                    for call in message["tool_calls"]
                    if isinstance(call, dict)
                ],
            }
        content = message.get("content")
        if not isinstance(content, list):
            return message
        texts: list[str] = []
        images: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text", "")))
            elif part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url", ""))
                _, image_b64 = cls._split_data_url(url)
                if image_b64:
                    images.append(image_b64)
        converted = {**message, "content": "\n".join(text for text in texts if text)}
        if images:
            converted["images"] = images
        return converted

    def _chat_anthropic(
        self,
        cfg: ProviderConfig,
        messages: list[dict[str, Any]],
        settings: GenerationSettings,
        request_timeout_seconds: float,
    ) -> str:
        text, _calls = self._anthropic_request(
            cfg, messages, settings, request_timeout_seconds
        )
        return text or ""

    def _anthropic_request(
        self,
        cfg: ProviderConfig,
        messages: list[dict[str, Any]],
        settings: GenerationSettings,
        request_timeout_seconds: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> tuple[str | None, list[ToolCall]]:
        system_text, chat_messages = self._anthropic_messages(messages)

        # Sampling params (temperature/top_p/seed) are deliberately omitted: newer Claude
        # models reject non-default combinations with 400, and defaults work everywhere.
        payload: dict[str, Any] = {
            "model": settings.model,
            "max_tokens": settings.max_tokens,
            "messages": chat_messages,
        }
        if system_text:
            payload["system"] = system_text
        converted_tools = self._anthropic_tools(tools)
        if converted_tools:
            payload["tools"] = converted_tools

        headers = {
            "Content-Type": "application/json",
            "anthropic-version": "2023-06-01",
        }
        if cfg.api_key:
            headers["x-api-key"] = cfg.api_key

        client = self._client_for(request_timeout_seconds)
        response = client.post(
            f"{cfg.base_url.rstrip('/')}/v1/messages", headers=headers, json=payload
        )
        try:
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise self._http_status_runtime_error(exc) from exc
        data = response.json()
        self.last_response_metadata = {
            "model": data.get("model"),
            "provider_type": "anthropic",
            "usage": data.get("usage") or {},
            "stop_reason": data.get("stop_reason"),
            "tool_calling_fallback": False,
            "reasoning_truncated": False,
        }
        # Content may open with non-text blocks (e.g. thinking), so join all text blocks
        # instead of indexing content[0].
        blocks = data.get("content") or []
        text = "".join(
            str(block.get("text", ""))
            for block in blocks
            if isinstance(block, dict) and block.get("type") == "text"
        )
        calls = [
            ToolCall(
                id=str(block.get("id") or ""),
                name=str(block.get("name") or ""),
                arguments=json.dumps(block.get("input") or {}, ensure_ascii=False),
            )
            for block in blocks
            if isinstance(block, dict)
            and block.get("type") == "tool_use"
            and block.get("name")
        ]
        cleaned = strip_reasoning_blocks(text, metadata=self.last_response_metadata)
        return (cleaned or None), calls

    @staticmethod
    def _anthropic_tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        """OpenAI-Funktionsschema → Anthropics ``input_schema``-Form."""
        converted: list[dict[str, Any]] = []
        for tool in tools or []:
            if not isinstance(tool, dict):
                continue
            function = (
                tool.get("function") if isinstance(tool.get("function"), dict) else tool
            )
            name = str(function.get("name") or "")
            if not name:
                continue
            converted.append(
                {
                    "name": name,
                    "description": str(function.get("description") or ""),
                    "input_schema": function.get("parameters")
                    or {"type": "object", "properties": {}},
                }
            )
        return converted

    @classmethod
    def _anthropic_messages(
        cls, messages: list[dict[str, Any]]
    ) -> tuple[str, list[dict[str, Any]]]:
        """OpenAI-Nachrichten → (System-Text, Anthropic-Nachrichten).

        Der einzige heikle Punkt sind die Werkzeug-Ergebnisse: bei OpenAI ist jedes
        eine eigene ``tool``-Nachricht, Anthropic erwartet sie als Bloecke *einer*
        ``user``-Nachricht. Zwei Ergebnisse hintereinander als zwei Nachrichten zu
        schicken, verletzt den Rollenwechsel und wird abgelehnt.
        """
        system_parts: list[str] = []
        chat_messages: list[dict[str, Any]] = []
        for message in messages:
            role = str(message.get("role", "user"))
            if role == "system":
                system_parts.append(cls._flatten_text_content(message.get("content")))
                continue
            if role == "tool":
                block = {
                    "type": "tool_result",
                    "tool_use_id": str(message.get("tool_call_id") or ""),
                    "content": cls._flatten_text_content(message.get("content")),
                }
                last = chat_messages[-1] if chat_messages else None
                if (
                    last
                    and last["role"] == "user"
                    and isinstance(last["content"], list)
                ):
                    last["content"].append(block)
                else:
                    chat_messages.append({"role": "user", "content": [block]})
                continue
            if role == "assistant" and message.get("tool_calls"):
                blocks: list[dict[str, Any]] = []
                text = cls._flatten_text_content(message.get("content"))
                if text.strip():
                    blocks.append({"type": "text", "text": text})
                for call in message["tool_calls"]:
                    if not isinstance(call, dict):
                        continue
                    function = call.get("function") or {}
                    blocks.append(
                        {
                            "type": "tool_use",
                            "id": str(call.get("id") or ""),
                            "name": str(function.get("name") or ""),
                            "input": cls._loads_or_empty(function.get("arguments")),
                        }
                    )
                chat_messages.append({"role": "assistant", "content": blocks})
                continue
            chat_messages.append(
                {
                    "role": role,
                    "content": cls._anthropic_content(message.get("content")),
                }
            )
        return "\n\n".join(part for part in system_parts if part), chat_messages

    @staticmethod
    def _loads_or_empty(raw: Any) -> dict[str, Any]:
        """Werkzeug-Argumente als Objekt — egal, in welcher Form sie ankamen."""
        if isinstance(raw, dict):
            return raw
        try:
            parsed = json.loads(str(raw or "{}"))
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}

    @staticmethod
    def _split_data_url(url: str) -> tuple[str, str]:
        """Split a `data:<media_type>;base64,<data>` URL into (media_type, base64_data)."""
        if not url.startswith("data:"):
            return "", ""
        header, _, data = url.partition(",")
        media_type = header[len("data:") :].split(";", 1)[0] or "image/png"
        return media_type, data

    @classmethod
    def _flatten_text_content(cls, content: Any) -> str:
        if isinstance(content, list):
            return "\n".join(
                str(part.get("text", ""))
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            )
        return str(content or "")

    @classmethod
    def _anthropic_content(cls, content: Any) -> list[dict[str, Any]] | str:
        if not isinstance(content, list):
            return str(content or "")
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                blocks.append({"type": "text", "text": str(part.get("text", ""))})
            elif part.get("type") == "image_url":
                url = str((part.get("image_url") or {}).get("url", ""))
                media_type, image_b64 = cls._split_data_url(url)
                if image_b64:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media_type,
                                "data": image_b64,
                            },
                        }
                    )
                elif url:
                    blocks.append(
                        {"type": "image", "source": {"type": "url", "url": url}}
                    )
        return blocks

    def _chat_openai_compatible(
        self,
        cfg: ProviderConfig,
        messages: list[dict[str, Any]],
        settings: GenerationSettings,
        request_timeout_seconds: float,
    ) -> str:
        message = self._openai_request(cfg, messages, settings, request_timeout_seconds)
        content = str(message.get("content", "") or "")
        if not content.strip():
            # Some LM Studio builds put the entire output of reasoning models into
            # `reasoning_content` and leave `content` empty. Callers that must not
            # show raw chain-of-thought (e.g. the Desktop Companion) check this flag:
            # combined with finish_reason == "length" it means the model burned its
            # whole token budget thinking and never produced an answer.
            content = str(message.get("reasoning_content", "") or "")
            self.last_response_metadata["reasoning_fallback"] = bool(content.strip())
        return strip_reasoning_blocks(content, metadata=self.last_response_metadata)

    def _openai_request(
        self,
        cfg: ProviderConfig,
        messages: list[dict[str, Any]],
        settings: GenerationSettings,
        request_timeout_seconds: float,
        tools: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Ein Aufruf gegen ``/chat/completions`` — liefert die ganze ``message``.

        :meth:`chat` braucht davon nur ``content``, die Werkzeugschleife braucht
        ``tool_calls``. Der Text wird deshalb erst beim Aufrufer herausgezogen.
        """
        extra_options = dict(settings.extra)
        response_format = extra_options.pop("response_format", None)
        json_mode = bool(extra_options.pop("json_mode", False))
        force_response_format = bool(extra_options.pop("force_response_format", False))
        omit_extra_body = bool(
            extra_options.pop("omit_extra_body", cfg.provider_type == "nvidia")
        )
        top_level_chat_template = bool(
            extra_options.pop(
                "top_level_chat_template_kwargs", cfg.provider_type == "nvidia"
            )
        )
        chat_template_kwargs = extra_options.pop("chat_template_kwargs", None)
        if cfg.provider_type == "nvidia":
            chat_template_kwargs = self._nvidia_chat_template_kwargs(
                settings.model, chat_template_kwargs
            )
        extra_options.pop("format", None)
        use_response_format = (
            force_response_format
            or cfg.provider_type == "openai"
            or "api.openai.com" in cfg.base_url.lower()
        )
        payload: dict[str, Any] = {
            "model": settings.model,
            "messages": messages,
            "temperature": settings.temperature,
            "top_p": settings.top_p,
            "max_tokens": settings.max_tokens,
        }
        extra_body = self._drop_none_values(
            {
                "num_ctx": settings.context_size,
                "repeat_penalty": settings.repeat_penalty,
                **extra_options,
            }
        )
        if chat_template_kwargs:
            if top_level_chat_template:
                payload["chat_template_kwargs"] = chat_template_kwargs
            else:
                extra_body["chat_template_kwargs"] = chat_template_kwargs
        if extra_body and not omit_extra_body:
            payload["extra_body"] = extra_body
        elif omit_extra_body:
            payload.update(self._drop_none_values(extra_options))
        if response_format is not None and use_response_format:
            payload["response_format"] = response_format
        elif json_mode and use_response_format:
            payload["response_format"] = {"type": "json_object"}
        if settings.seed is not None:
            payload["seed"] = settings.seed
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"

        headers = {"Content-Type": "application/json"}
        if cfg.api_key:
            headers["Authorization"] = f"Bearer {cfg.api_key}"

        endpoint = f"{cfg.base_url.rstrip('/')}/chat/completions"
        client = self._client_for(request_timeout_seconds)
        flags = {"response_format_fallback": False, "tool_calling_fallback": False}
        attempt = payload
        while True:
            response = client.post(endpoint, headers=headers, json=attempt)
            try:
                response.raise_for_status()
                break
            except httpx.HTTPStatusError as exc:
                reduced = self._reduce_payload(attempt, exc.response.status_code, flags)
                if reduced is None:
                    raise self._http_status_runtime_error(exc) from exc
                attempt = reduced
        data = response.json()
        choices = data.get("choices") or []
        self.last_response_metadata = {
            "model": data.get("model"),
            "provider_type": cfg.provider_type,
            "usage": data.get("usage") or {},
            "finish_reason": choices[0].get("finish_reason") if choices else None,
            "reasoning_fallback": False,
            "reasoning_truncated": False,
            **flags,
        }
        if not choices:
            return {}
        return choices[0].get("message") or {}

    @staticmethod
    def _reduce_payload(
        payload: dict[str, Any], status_code: int, flags: dict[str, bool]
    ) -> dict[str, Any] | None:
        """Nimmt einzeln weg, was ein Server mit 400/422 abgelehnt haben kann.

        Reihenfolge: erst ``tools`` (nicht jedes lokale Modell kann Tool-Calling,
        und manche Server lehnen das Feld ab, statt es zu ignorieren), dann
        ``response_format``. ``None`` heisst: nichts mehr wegzunehmen — dann ist
        es ein echter Fehler und muss durchschlagen, statt unter einem Rueckfall
        begraben zu werden.
        """
        if status_code not in {400, 422}:
            return None
        reduced = dict(payload)
        if "tools" in reduced:
            reduced.pop("tools", None)
            reduced.pop("tool_choice", None)
            flags["tool_calling_fallback"] = True
            return reduced
        if "response_format" in reduced:
            reduced.pop("response_format", None)
            flags["response_format_fallback"] = True
            return reduced
        return None

    @staticmethod
    def _nvidia_chat_template_kwargs(model: str, value: Any) -> dict[str, Any] | None:
        """Normalize NVIDIA NIM chat-template kwargs to model-specific keys."""
        if not isinstance(value, dict):
            return None
        cleaned = dict(value)
        model_lower = (model or "").lower()
        if "kimi" in model_lower:
            if "thinking" not in cleaned and "enable_thinking" in cleaned:
                cleaned["thinking"] = bool(cleaned.get("enable_thinking"))
            allowed = {"thinking"}
            return {key: cleaned[key] for key in allowed if key in cleaned}
        return cleaned

    @staticmethod
    def _http_status_runtime_error(exc: httpx.HTTPStatusError) -> RuntimeError:
        # Der Statuscode und Retry-After muessen im Text landen: weiter oben faengt
        # die Extraktion jede Exception ab und behaelt nur noch den String. Ohne
        # "HTTP 429" darin kann query/llm_errors.py ein Rate-Limit spaeter nicht
        # mehr von einem beliebigen Fehler unterscheiden.
        response = exc.response
        detail = (response.text or "").strip()
        if len(detail) > 800:
            detail = detail[:800] + "..."
        parts = [f"HTTP {response.status_code}", str(exc)]
        retry_after = response.headers.get("retry-after")
        if retry_after:
            parts.append(f"retry_after={retry_after}s")
        if detail:
            parts.append(f"response_body={detail}")
        return RuntimeError("; ".join(parts))

    @staticmethod
    def _drop_none_values(data: dict[str, Any]) -> dict[str, Any]:
        return {key: value for key, value in data.items() if value is not None}

    @staticmethod
    def _extract_json(raw: str) -> dict[str, Any]:
        raw = raw.strip()
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end <= start:
            # Last resort: json_repair kann auch Markdown-Fences / fehlerhaftes
            # JSON reparieren. Liefert bei völligem Müll ein leeres Dict.
            return _repair_json_safe(raw)
        trimmed = raw[start : end + 1]
        try:
            return json.loads(trimmed)
        except json.JSONDecodeError:
            # JSON wurde abgeschnitten (max_tokens zu niedrig) oder enthält
            # Markup-Reste — json_repair schließt offene Klammern/Strings.
            return _repair_json_safe(trimmed)
