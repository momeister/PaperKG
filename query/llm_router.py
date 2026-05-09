from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml


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
class ProviderConfig:
	provider_type: str
	base_url: str
	api_key: str | None = None
	timeout_seconds: float = 120.0
	settings: GenerationSettings | None = None
	available_models: list[str] = field(default_factory=list)


class LLMRouter:
	"""
	Unified router for Ollama, LM Studio and OpenAI-compatible cloud endpoints.
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
		self.last_response_metadata: dict[str, Any] = {}

	@classmethod
	def from_config_file(cls, config_path: str | Path = "config.yaml") -> "LLMRouter":
		path = Path(config_path)
		with path.open("r", encoding="utf-8") as fh:
			config = yaml.safe_load(fh) or {}

		llm_cfg = config.get("llm") or {}
		default_provider = llm_cfg.get("default_provider", "ollama")

		providers: dict[str, ProviderConfig] = {}
		for name, raw in (llm_cfg.get("providers") or {}).items():
			# Handle None values for optional float fields
			repeat_penalty_val = raw.get("repeat_penalty")
			repeat_penalty = float(repeat_penalty_val) if repeat_penalty_val is not None else None
			available_models = [str(model) for model in (raw.get("models") or []) if model]
			
			settings = GenerationSettings(
				model=raw.get("model", ""),
				temperature=float(raw.get("temperature", 0.2)) if raw.get("temperature") is not None else 0.2,
				top_p=float(raw.get("top_p", 0.95)) if raw.get("top_p") is not None else 0.95,
				max_tokens=int(raw.get("max_tokens", 2048)) if raw.get("max_tokens") is not None else 2048,
				context_size=int(raw.get("context_size", 32768)) if raw.get("context_size") is not None else 32768,
				repeat_penalty=repeat_penalty,
				seed=raw.get("seed"),
				extra=dict(raw.get("extra_options") or {}),
			)

			api_key = raw.get("api_key")
			env_name = raw.get("api_key_env")
			if env_name:
				api_key = os.getenv(env_name, api_key)

			providers[name] = ProviderConfig(
				provider_type=str(raw.get("provider_type", "ollama")),
				base_url=str(raw.get("base_url", "http://localhost:11434")),
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
				models = [str(item.get("name")) for item in payload.get("models", []) if item.get("name")]
			elif cfg.provider_type in {"openai_compatible", "lm_studio", "openai"}:
				headers = {"Content-Type": "application/json"}
				if cfg.api_key:
					headers["Authorization"] = f"Bearer {cfg.api_key}"
				response = client.get(f"{cfg.base_url.rstrip('/')}/models", headers=headers)
				response.raise_for_status()
				payload = response.json()
				models = [
					str(item.get("id") or item.get("name"))
					for item in payload.get("data", [])
					if item.get("id") or item.get("name")
				]
		except Exception:
			models = []

		return models

	def provider_model_options(self, provider: str | None = None, refresh: bool = False) -> list[str]:
		cfg = self.provider_config(provider)
		models = self.discover_provider_models(provider) if refresh else []
		models.extend([model for model in cfg.available_models if model])
		if cfg.settings and cfg.settings.model and cfg.settings.model not in models:
			models.insert(0, cfg.settings.model)
		if not models:
			models = [cfg.settings.model if cfg.settings and cfg.settings.model else "qwen3.6-35b"]
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

		if refresh and cfg.provider_type == "ollama":
			model_settings = self.discover_ollama_model_settings(cfg, settings.model)
			if model_settings:
				settings = self._merged_settings(settings, model_settings)
				settings.max_tokens = max(settings.max_tokens, 16384)

		return settings

	def discover_ollama_model_settings(self, cfg: ProviderConfig, model: str) -> dict[str, Any]:
		client = self._client_for(cfg.timeout_seconds)
		try:
			response = client.post(f"{cfg.base_url.rstrip('/')}/api/show", json={"model": model})
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
				settings[key] = int(value) if key in {"context_size", "max_tokens"} else float(value)
			except ValueError:
				continue
		return settings

	def chat(
		self,
		messages: list[dict[str, str]],
		provider: str | None = None,
		overrides: dict[str, Any] | None = None,
	) -> str:
		provider_name = provider or self.default_provider
		cfg = self.provider_config(provider_name)
		settings = self._merged_settings(cfg.settings, overrides)
		request_timeout_seconds = float((overrides or {}).get("timeout_seconds", cfg.timeout_seconds))

		if cfg.provider_type == "ollama":
			return self._chat_ollama(cfg, messages, settings, request_timeout_seconds)
		if cfg.provider_type in {"openai_compatible", "lm_studio", "openai"}:
			return self._chat_openai_compatible(cfg, messages, settings, request_timeout_seconds)

		raise ValueError(f"Unsupported provider type: {cfg.provider_type}")

	def chat_json(
		self,
		messages: list[dict[str, str]],
		provider: str | None = None,
		overrides: dict[str, Any] | None = None,
	) -> dict[str, Any]:
		response_text = self.chat(messages=messages, provider=provider, overrides=overrides)
		return self._extract_json(response_text)

	def _client_for(self, timeout_seconds: float) -> httpx.Client:
		if self._client is not None:
			return self._client
		return httpx.Client(timeout=timeout_seconds)

	@staticmethod
	def _merged_settings(base: GenerationSettings | None, overrides: dict[str, Any] | None) -> GenerationSettings:
		base = base or GenerationSettings(model="qwen3.6-35b")
		if not overrides:
			return base

		repeat_penalty_override = overrides.get("repeat_penalty", base.repeat_penalty)
		repeat_penalty = float(repeat_penalty_override) if repeat_penalty_override is not None else None

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
		messages: list[dict[str, str]],
		settings: GenerationSettings,
		request_timeout_seconds: float,
	) -> str:
		extra_options = dict(settings.extra)
		keep_alive = extra_options.pop("keep_alive", "0s")
		response_format = extra_options.pop("format", None)
		json_mode = bool(extra_options.pop("json_mode", False))
		extra_options.pop("response_format", None)
		extra_options.pop("chat_template_kwargs", None)
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
		payload["options"] = self._drop_none_values(payload["options"])
		if settings.seed is not None:
			payload["options"]["seed"] = settings.seed

		client = self._client_for(request_timeout_seconds)
		response = client.post(f"{cfg.base_url.rstrip('/')}/api/chat", json=payload)
		response.raise_for_status()
		data = response.json()
		self.last_response_metadata = {
			"provider_type": "ollama",
			"eval_count": data.get("eval_count"),
			"prompt_eval_count": data.get("prompt_eval_count"),
			"total_duration": data.get("total_duration"),
			"load_duration": data.get("load_duration"),
			"done_reason": data.get("done_reason"),
		}
		message = data.get("message") or {}
		return str(message.get("content", "")).strip()

	def _chat_openai_compatible(
		self,
		cfg: ProviderConfig,
		messages: list[dict[str, str]],
		settings: GenerationSettings,
		request_timeout_seconds: float,
	) -> str:
		extra_options = dict(settings.extra)
		response_format = extra_options.pop("response_format", None)
		json_mode = bool(extra_options.pop("json_mode", False))
		force_response_format = bool(extra_options.pop("force_response_format", False))
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
			"extra_body": {
				"num_ctx": settings.context_size,
				"repeat_penalty": settings.repeat_penalty,
				**extra_options,
			},
		}
		if response_format is not None and use_response_format:
			payload["response_format"] = response_format
		elif json_mode and use_response_format:
			payload["response_format"] = {"type": "json_object"}
		payload["extra_body"] = self._drop_none_values(payload["extra_body"])
		if settings.seed is not None:
			payload["seed"] = settings.seed

		headers = {"Content-Type": "application/json"}
		if cfg.api_key:
			headers["Authorization"] = f"Bearer {cfg.api_key}"

		endpoint = f"{cfg.base_url.rstrip('/')}/chat/completions"
		client = self._client_for(request_timeout_seconds)
		response = client.post(endpoint, headers=headers, json=payload)
		response_format_fallback = False
		try:
			response.raise_for_status()
		except httpx.HTTPStatusError as exc:
			if "response_format" not in payload or exc.response.status_code not in {400, 422}:
				raise
			fallback_payload = dict(payload)
			fallback_payload.pop("response_format", None)
			response = client.post(endpoint, headers=headers, json=fallback_payload)
			response.raise_for_status()
			response_format_fallback = True
		data = response.json()
		choices = data.get("choices") or []
		self.last_response_metadata = {
			"provider_type": cfg.provider_type,
			"usage": data.get("usage") or {},
			"finish_reason": choices[0].get("finish_reason") if choices else None,
			"response_format_fallback": response_format_fallback,
		}
		if not choices:
			return ""
		message = choices[0].get("message") or {}
		return str(message.get("content", "")).strip()

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
			raise ValueError("No JSON object found in model response.")
		return json.loads(raw[start : end + 1])
