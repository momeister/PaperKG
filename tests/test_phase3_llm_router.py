import httpx
from unittest.mock import MagicMock
from typing import Any

from query.llm_router import LLMRouter, ProviderConfig, GenerationSettings
from query.nim_container import NIMContainerConfig, NIMContainerManager

class TestLLMRouter:
    """Test LLM router configuration helpers."""

    def test_from_config_file_parses_model_lists(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
llm:
  default_provider: ollama
  providers:
    ollama:
      provider_type: ollama
      base_url: http://localhost:11434
      model: qwen3.6-35b
      models:
        - qwen3.6-35b
        - llama3.1:8b
""".strip(),
            encoding="utf-8",
        )

        router = LLMRouter.from_config_file(config_path)

        assert router.provider_model_options("ollama") == ["qwen3.6-35b", "llama3.1:8b"]
        assert router.provider_default_model("ollama") == "qwen3.6-35b"

    def test_from_config_file_loads_nvidia_key_from_env(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
llm:
  default_provider: nvidia
  providers:
    nvidia:
      provider_type: nvidia
      base_url: https://integrate.api.nvidia.com/v1
      api_key_env: NVIDIA_API_KEY
      model: moonshotai/kimi-k2.6
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-test")

        router = LLMRouter.from_config_file(config_path)

        assert router.provider_config("nvidia").api_key == "nvapi-test"
        assert router.provider_default_model("nvidia") == "moonshotai/kimi-k2.6"

    def test_from_config_file_uses_ngc_key_fallback_for_nvidia(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
llm:
  default_provider: nvidia
  providers:
    nvidia:
      provider_type: nvidia
      base_url: https://integrate.api.nvidia.com/v1
      api_key_env: NVIDIA_API_KEY
      model: moonshotai/kimi-k2.6
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv("NGC_API_KEY", "nvapi-ngc-test")

        router = LLMRouter.from_config_file(config_path)

        assert router.provider_config("nvidia").api_key == "nvapi-ngc-test"

    def test_from_config_file_keeps_ngc_key_out_of_self_hosted_nvidia(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
llm:
  default_provider: nvidia_local_nim
  providers:
    nvidia_local_nim:
      provider_type: nvidia
      base_url: http://localhost:8000/v1
      model: moonshotai/kimi-k2.6
      api_key: null
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.delenv("NVIDIA_API_KEY", raising=False)
        monkeypatch.setenv("NGC_API_KEY", "nvapi-ngc-test")

        router = LLMRouter.from_config_file(config_path)

        assert router.provider_config("nvidia_local_nim").api_key is None

    def test_from_config_file_loads_gemini_key_from_env(self, tmp_path, monkeypatch):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
llm:
  default_provider: gemini
  providers:
    gemini:
      provider_type: openai_compatible
      base_url: https://generativelanguage.googleapis.com/v1beta/openai
      api_key_env: GEMINI_API_KEY
      model: gemini-3.1-flash-lite
      models:
        - gemini-3.1-flash-lite
""".strip(),
            encoding="utf-8",
        )
        monkeypatch.setenv("GEMINI_API_KEY", "gemini-test-key")

        router = LLMRouter.from_config_file(config_path)

        assert router.provider_config("gemini").api_key == "gemini-test-key"
        assert router.provider_default_model("gemini") == "gemini-3.1-flash-lite"

    def test_discover_provider_models_from_ollama(self):
        response = MagicMock()
        response.json.return_value = {"models": [{"name": "qwen3.6-35b"}, {"name": "llama3.1:8b"}]}
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.get.return_value = response

        router = LLMRouter(
            providers={
                "ollama": ProviderConfig(
                    provider_type="ollama",
                    base_url="http://localhost:11434",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="ollama",
            client=client,
        )

        assert router.discover_provider_models("ollama") == ["qwen3.6-35b", "llama3.1:8b"]

    def test_recommended_settings_reads_ollama_parameters(self):
        tags_response = MagicMock()
        tags_response.json.return_value = {"models": [{"name": "qwen3.6-35b"}]}
        tags_response.raise_for_status.return_value = None
        show_response = MagicMock()
        show_response.json.return_value = {
            "parameters": "temperature 0.1\ntop_p 0.8\nnum_ctx 65536\nnum_predict 4096\nrepeat_penalty 1.1"
        }
        show_response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = show_response

        router = LLMRouter(
            providers={
                "ollama": ProviderConfig(
                    provider_type="ollama",
                    base_url="http://localhost:11434",
                    settings=GenerationSettings(model="qwen3.6-35b", max_tokens=2048),
                )
            },
            default_provider="ollama",
            client=client,
        )

        settings = router.recommended_settings("ollama", "qwen3.6-35b")

        assert settings.temperature == 0.1
        assert settings.top_p == 0.8
        assert settings.context_size == 65536
        assert settings.max_tokens == 16384
        assert settings.repeat_penalty == 1.1

    def test_discover_provider_models_from_openai_compatible(self):
        response = MagicMock()
        response.json.return_value = {"data": [{"id": "gpt-4o"}, {"id": "gpt-4.1-mini"}]}
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.get.return_value = response

        router = LLMRouter(
            providers={
                "lm_studio": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="http://localhost:1234/v1",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="lm_studio",
            client=client,
        )

        assert router.discover_provider_models("lm_studio") == ["gpt-4o", "gpt-4.1-mini"]

    def test_merged_settings_keeps_none_repeat_penalty(self):
        base = GenerationSettings(model="gpt-4o", repeat_penalty=None)

        merged = LLMRouter._merged_settings(base, {"temperature": 0.4})

        assert merged.repeat_penalty is None
        assert merged.temperature == 0.4

    def test_ollama_json_mode_uses_top_level_format(self):
        response = MagicMock()
        response.json.return_value = {
            "message": {"content": '{"ok": true}'},
            "done_reason": "stop",
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "ollama": ProviderConfig(
                    provider_type="ollama",
                    base_url="http://localhost:11434",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="ollama",
            client=client,
        )

        text = router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={
                "extra": {
                    "json_mode": True,
                    "format": "json",
                    "response_format": {"type": "json_object"},
                    "chat_template_kwargs": {"enable_thinking": False},
                }
            },
        )

        payload = client.post.call_args.kwargs["json"]
        assert text == '{"ok": true}'
        assert payload["format"] == "json"
        assert "response_format" not in payload["options"]
        assert "chat_template_kwargs" not in payload["options"]
        assert router.last_response_metadata["done_reason"] == "stop"

    def test_lm_studio_json_mode_does_not_force_response_format(self):
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "lm": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="http://localhost:1234/v1",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="lm",
            client=client,
        )

        text = router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={"extra": {"json_mode": True, "format": "json"}},
        )

        payload = client.post.call_args.kwargs["json"]
        assert text == '{"ok": true}'
        assert "response_format" not in payload
        assert "format" not in payload["extra_body"]
        assert router.last_response_metadata["finish_reason"] == "stop"

    def test_lm_studio_response_format_falls_back_when_server_rejects_it(self):
        request = httpx.Request("POST", "http://localhost:1234/v1/chat/completions")
        rejected_response = httpx.Response(400, request=request, json={"error": "unsupported"})
        accepted = MagicMock()
        accepted.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        accepted.raise_for_status.return_value = None
        client = MagicMock()
        client.post.side_effect = [
            rejected_response,
            accepted,
        ]
        router = LLMRouter(
            providers={
                "lm": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="http://localhost:1234/v1",
                    settings=GenerationSettings(model="qwen3.6-35b"),
                )
            },
            default_provider="lm",
            client=client,
        )

        text = router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={"extra": {"json_mode": True, "force_response_format": True}},
        )

        first_payload = client.post.call_args_list[0].kwargs["json"]
        second_payload = client.post.call_args_list[1].kwargs["json"]
        assert first_payload["response_format"] == {"type": "json_object"}
        assert "response_format" not in second_payload
        assert text == '{"ok": true}'
        assert router.last_response_metadata["response_format_fallback"] is True

    def test_openai_endpoint_json_mode_uses_response_format(self):
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "openai": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="https://api.openai.com/v1",
                    settings=GenerationSettings(model="gpt-4o"),
                )
            },
            default_provider="openai",
            client=client,
        )

        router.chat(
            [{"role": "user", "content": "JSON"}],
            overrides={"extra": {"json_mode": True}},
        )

        payload = client.post.call_args.kwargs["json"]
        assert payload["response_format"] == {"type": "json_object"}

    def test_gemini_endpoint_uses_openai_compat_without_extra_body(self):
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "gemini": ProviderConfig(
                    provider_type="openai_compatible",
                    base_url="https://generativelanguage.googleapis.com/v1beta/openai",
                    api_key="gemini-test-key",
                    settings=GenerationSettings(
                        model="gemini-3.1-flash-lite",
                        repeat_penalty=None,
                        extra={"json_mode": True, "force_response_format": True, "omit_extra_body": True},
                    ),
                )
            },
            default_provider="gemini",
            client=client,
        )

        text = router.chat([{"role": "user", "content": "JSON"}])

        call = client.post.call_args
        payload = call.kwargs["json"]
        headers = call.kwargs["headers"]
        assert call.args[0] == "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
        assert text == '{"ok": true}'
        assert headers["Authorization"] == "Bearer gemini-test-key"
        assert payload["model"] == "gemini-3.1-flash-lite"
        assert payload["response_format"] == {"type": "json_object"}
        assert "extra_body" not in payload

    def test_nvidia_endpoint_omits_extra_body_and_uses_bearer_key(self):
        response = MagicMock()
        response.json.return_value = {
            "choices": [{"message": {"content": '{"ok": true}'}, "finish_reason": "stop"}],
            "usage": {"completion_tokens": 4},
        }
        response.raise_for_status.return_value = None
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "nvidia": ProviderConfig(
                    provider_type="nvidia",
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key="nvapi-test",
                    settings=GenerationSettings(
                        model="moonshotai/kimi-k2.6",
                        repeat_penalty=None,
                        extra={
                            "json_mode": True,
                            "include_reasoning": False,
                            "response_format": {"type": "json_object"},
                            "chat_template_kwargs": {"thinking": False, "enable_thinking": False},
                        },
                    ),
                )
            },
            default_provider="nvidia",
            client=client,
        )

        text = router.chat([{"role": "user", "content": "JSON"}])

        call = client.post.call_args
        payload = call.kwargs["json"]
        headers = call.kwargs["headers"]
        assert call.args[0] == "https://integrate.api.nvidia.com/v1/chat/completions"
        assert text == '{"ok": true}'
        assert headers["Authorization"] == "Bearer nvapi-test"
        assert "extra_body" not in payload
        assert "response_format" not in payload
        assert payload["include_reasoning"] is False
        assert payload["chat_template_kwargs"] == {"thinking": False}

    def test_provider_auth_check_surfaces_forbidden_response_body(self):
        request = httpx.Request("POST", "https://integrate.api.nvidia.com/v1/chat/completions")
        response = httpx.Response(
            403,
            request=request,
            json={"status": 403, "title": "Forbidden", "detail": "Authorization failed"},
        )
        client = MagicMock()
        client.post.return_value = response
        router = LLMRouter(
            providers={
                "nvidia": ProviderConfig(
                    provider_type="nvidia",
                    base_url="https://integrate.api.nvidia.com/v1",
                    api_key="nvapi-test",
                    settings=GenerationSettings(model="moonshotai/kimi-k2.6", repeat_penalty=None),
                )
            },
            default_provider="nvidia",
            client=client,
        )

        ok, error = router.check_provider_auth("nvidia", model="moonshotai/kimi-k2.6")

        assert ok is False
        assert error is not None
        assert "403 Forbidden" in error
        assert "Authorization failed" in error

    def test_provider_auth_error_classification_distinguishes_timeouts(self):
        assert LLMRouter.is_auth_error("403 Forbidden; response_body={\"detail\":\"Authorization failed\"}")
        assert not LLMRouter.is_auth_error("The read operation timed out")


class TestNIMContainerManager:
    def test_config_infers_local_nim_port_and_uses_kimi_image(self, tmp_path):
        config_path = tmp_path / "config.yaml"
        config_path.write_text(
            """
llm:
  providers:
    nvidia_local_nim:
      provider_type: nvidia
      base_url: http://localhost:9090/v1
      model: moonshotai/kimi-k2.6
""".strip(),
            encoding="utf-8",
        )

        config = NIMContainerConfig.from_config_file(config_path)

        assert config.host_port == 9090
        assert config.image == "nvcr.io/nim/moonshotai/kimi-k2.6:1.7.0-variant"
        assert config.base_url == "http://localhost:9090/v1"

    def test_config_reads_nvidia_key_without_exposing_it_in_command(self, tmp_path, monkeypatch):
        monkeypatch.delenv("NGC_API_KEY", raising=False)
        monkeypatch.setenv("NVIDIA_API_KEY", "nvapi-secret-value")
        config = NIMContainerConfig(cache_dir=str(tmp_path / "nim-cache"))

        command = NIMContainerManager(config).redacted_run_command()

        assert config.api_key == "nvapi-secret-value"
        assert "NGC_API_KEY" in command
        assert "nvapi-secret-value" not in " ".join(command)

    def test_start_container_passes_key_by_environment_only(self, tmp_path, monkeypatch):
        monkeypatch.setenv("NGC_API_KEY", "nvapi-secret-value")
        calls: list[tuple[list[str], dict[str, Any]]] = []

        def runner(args, **kwargs):
            calls.append((args, kwargs))
            if args[1] == "--version":
                return MagicMock(returncode=0, stdout="Docker version 27", stderr="")
            if args[1] == "info":
                return MagicMock(returncode=0, stdout="27.0.0", stderr="")
            if args[1] == "inspect":
                return MagicMock(returncode=1, stdout="", stderr="No such object")
            if args[1] == "run":
                return MagicMock(returncode=0, stdout="container-id", stderr="")
            raise AssertionError(f"Unexpected docker command: {args}")

        config = NIMContainerConfig(cache_dir=str(tmp_path / "nim-cache"))
        result = NIMContainerManager(config, runner=runner).start_container()

        run_args, run_kwargs = calls[-1]
        assert result.ok is True
        assert run_args[1] == "run"
        assert "nvapi-secret-value" not in " ".join(run_args)
        assert run_kwargs["env"]["NGC_API_KEY"] == "nvapi-secret-value"
        assert (tmp_path / "nim-cache").exists()


