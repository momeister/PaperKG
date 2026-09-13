import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import httpx
import pytest

from query.llm_router import GenerationSettings, LLMRouter, ProviderConfig


def router(handler):
    return LLMRouter(
        providers={
            name: ProviderConfig(
                provider_type="openai_compatible",
                base_url="http://fixture",
                settings=GenerationSettings(model=f"{name}-default"),
            )
            for name in ["glm", "deepseek"]
        },
        default_provider="glm",
        client=httpx.Client(transport=httpx.MockTransport(handler)),
    )


def response(request):
    model = json.loads(request.content)["model"]
    return httpx.Response(
        200,
        json={
            "model": f"served-{model}",
            "choices": [{"message": {"content": model}, "finish_reason": "stop"}],
        },
    )


def test_provider_defaults_and_actual_server_model_are_recorded_without_leaking_on_error():
    def handler(request):
        if json.loads(request.content)["model"] == "broken":
            return httpx.Response(401, text="Invalid API key")
        return response(request)

    llm = router(handler)
    messages = [{"role": "user", "content": "Same question and same source"}]
    llm.chat(messages, provider="glm")
    switched = llm.chat(messages, provider="deepseek")
    direct = router(handler).chat(messages, provider="deepseek")
    assert switched == direct == "deepseek-default"
    assert llm.last_response_metadata["provider"] == "deepseek"
    assert llm.last_response_metadata["model"] == "served-deepseek-default"
    with pytest.raises(RuntimeError, match="HTTP 401"):
        llm.chat(messages, provider="glm", overrides={"model": "broken"})
    assert llm.last_response_metadata["request_failed"]
    assert "provider" not in llm.last_response_metadata
    assert llm.last_response_metadata["requested_model"] == "broken"


def test_concurrent_requests_keep_separate_response_metadata():
    llm = router(response)
    barrier = Barrier(2)

    def run(provider):
        llm.chat([{"role": "user", "content": "Fixture"}], provider=provider)
        barrier.wait(timeout=5)
        return dict(llm.last_response_metadata)

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(run, ["glm", "deepseek"]))
    assert [m["provider"] for m in results] == ["glm", "deepseek"]
    assert [m["model"] for m in results] == [
        "served-glm-default",
        "served-deepseek-default",
    ]
