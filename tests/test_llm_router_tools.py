"""Tool-Calling im :class:`LLMRouter` — ein Schema rein, drei Drahtformate raus.

Vollstaendig offline: jeder Anbieteraufruf laeuft ueber einen ``httpx.MockTransport``,
der ueber das ``client=``-Argument des Routers hineingereicht wird.

Der Punkt dieser Tests ist nicht, dass ein Aufruf ankommt, sondern dass der Aufrufer
**eine** Nachrichtenform schreiben darf. Die Werkzeugschleife des Code-Graphen haengt
ihre Zwischenschritte in OpenAI-Form an; wenn Anthropic daraus keine ``tool_use``-/
``tool_result``-Bloecke macht oder Ollama die Argumente als Zeichenkette
zurueckbekommt, faellt das nicht als Fehler auf — das Modell ruft dann einfach
dasselbe Werkzeug noch einmal auf.
"""

from __future__ import annotations

import json
from typing import Any

import httpx

from query.llm_router import GenerationSettings, LLMRouter, ProviderConfig

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_symbols",
            "description": "Sucht Funktionen und Klassen nach Namen.",
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        },
    }
]


def _router(provider_type: str, handler, api_key: str | None = None) -> LLMRouter:
    cfg = ProviderConfig(
        provider_type=provider_type,
        base_url="http://test.local",
        api_key=api_key,
        settings=GenerationSettings(model="test-model", max_tokens=512),
    )
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return LLMRouter(providers={"p": cfg}, default_provider="p", client=client)


# --------------------------------------------------------------------------- #
# OpenAI-kompatibel (LM Studio, OpenAI, NVIDIA)                                #
# --------------------------------------------------------------------------- #


def test_openai_compatible_offers_tools_and_reads_the_calls_back() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "choices": [
                    {
                        "message": {
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "call_abc",
                                    "type": "function",
                                    "function": {
                                        "name": "search_symbols",
                                        "arguments": '{"query": "parse"}',
                                    },
                                }
                            ],
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
                "usage": {},
            },
        )

    router = _router("lm_studio", handler)
    text, calls = router.chat_with_tools(
        [{"role": "user", "content": "was parst hier?"}], TOOLS
    )

    assert captured["payload"]["tools"] == TOOLS
    assert captured["payload"]["tool_choice"] == "auto"
    assert text is None
    assert [(call.id, call.name) for call in calls] == [("call_abc", "search_symbols")]
    # Roher String, nicht geparst: die Gegenstelle (Rust) parst selbst.
    assert calls[0].arguments == '{"query": "parse"}'


def test_a_server_that_rejects_tools_gets_asked_again_without_them() -> None:
    """400 auf ``tools`` ist keine Sackgasse, aber es muss sichtbar sein.

    Ohne den Vermerk in ``last_response_metadata`` saehe der Aufrufer nur eine
    Antwort ohne Werkzeugaufrufe und hielte das fuer eine Entscheidung des
    Modells, statt auf Prompt-and-Parse umzuschalten.
    """
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        payload = json.loads(request.content)
        seen.append(payload)
        if "tools" in payload:
            return httpx.Response(
                400, json={"error": "tools are not supported by this model"}
            )
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "ohne Werkzeuge"}}], "usage": {}},
        )

    router = _router("lm_studio", handler)
    text, calls = router.chat_with_tools([{"role": "user", "content": "hallo"}], TOOLS)

    assert len(seen) == 2
    assert "tools" not in seen[1]
    assert text == "ohne Werkzeuge"
    assert calls == []
    assert router.last_response_metadata["tool_calling_fallback"] is True


def test_a_real_error_still_surfaces_instead_of_being_retried_away() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(401, json={"error": "invalid api key"})

    router = _router("lm_studio", handler)
    try:
        router.chat_with_tools([{"role": "user", "content": "hallo"}], TOOLS)
    except RuntimeError as error:
        assert "HTTP 401" in str(error)
    else:  # pragma: no cover - der Aufruf muss scheitern
        raise AssertionError("ein 401 darf nicht als Werkzeug-Rueckfall durchgehen")


def test_chat_without_tools_is_unchanged() -> None:
    """Die Auftrennung von ``_openai_request`` darf ``chat()`` nicht anfassen."""
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={"choices": [{"message": {"content": "  Antwort  "}}], "usage": {}},
        )

    router = _router("lm_studio", handler)
    assert router.chat([{"role": "user", "content": "frage"}]) == "Antwort"
    assert "tools" not in captured["payload"]
    assert "tool_choice" not in captured["payload"]


# --------------------------------------------------------------------------- #
# Ollama                                                                       #
# --------------------------------------------------------------------------- #


def test_ollama_arguments_arrive_as_an_object_and_leave_as_text() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "tool_calls": [
                        {
                            "function": {
                                "name": "search_symbols",
                                "arguments": {"query": "parse"},
                            }
                        }
                    ],
                }
            },
        )

    router = _router("ollama", handler)
    _text, calls = router.chat_with_tools([{"role": "user", "content": "frage"}], TOOLS)

    assert captured["payload"]["tools"] == TOOLS
    assert len(calls) == 1
    assert json.loads(calls[0].arguments) == {"query": "parse"}
    # Ollama vergibt keine IDs; ohne eine synthetische liesse sich das Ergebnis
    # seinem Aufruf nicht mehr zuordnen.
    assert calls[0].id


def test_ollama_gets_its_own_tool_call_back_as_an_object() -> None:
    """Der Rueckweg ist die Haelfte, die man leicht vergisst.

    Sieht das Modell in Runde zwei seinen eigenen Aufruf als Zeichenkette statt
    als Objekt, ruft es dasselbe Werkzeug noch einmal auf — und die Schleife
    laeuft in ihr Rundenlimit, ohne dass irgendwo ein Fehler entsteht.
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "fertig"}})

    router = _router("ollama", handler)
    router.chat_with_tools(
        [
            {"role": "user", "content": "frage"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_0",
                        "function": {
                            "name": "search_symbols",
                            "arguments": '{"query": "parse"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_0", "content": '{"hits": []}'},
        ],
        TOOLS,
    )

    assistant = captured["payload"]["messages"][1]
    assert assistant["tool_calls"][0]["function"]["arguments"] == {"query": "parse"}
    assert captured["payload"]["messages"][2]["role"] == "tool"


# --------------------------------------------------------------------------- #
# Anthropic                                                                    #
# --------------------------------------------------------------------------- #


def test_anthropic_gets_input_schema_and_returns_tool_use_blocks() -> None:
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "content": [
                    {"type": "text", "text": "Ich sehe nach."},
                    {
                        "type": "tool_use",
                        "id": "toolu_1",
                        "name": "search_symbols",
                        "input": {"query": "parse"},
                    },
                ],
                "usage": {},
                "stop_reason": "tool_use",
            },
        )

    router = _router("anthropic", handler, api_key="k")
    text, calls = router.chat_with_tools([{"role": "user", "content": "frage"}], TOOLS)

    tool = captured["payload"]["tools"][0]
    assert tool["name"] == "search_symbols"
    assert tool["input_schema"] == TOOLS[0]["function"]["parameters"]
    assert "parameters" not in tool
    assert text == "Ich sehe nach."
    assert (calls[0].id, calls[0].name) == ("toolu_1", "search_symbols")
    assert json.loads(calls[0].arguments) == {"query": "parse"}


def test_anthropic_merges_consecutive_tool_results_into_one_user_message() -> None:
    """Zwei Ergebnisse hintereinander sind bei OpenAI zwei Nachrichten.

    Anthropic verlangt abwechselnde Rollen — unveraendert durchgereicht waeren das
    zwei ``user``-Nachrichten in Folge, und der Aufruf wird abgelehnt.
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200, json={"content": [{"type": "text", "text": "fertig"}], "usage": {}}
        )

    router = _router("anthropic", handler, api_key="k")
    router.chat_with_tools(
        [
            {"role": "system", "content": "sei knapp"},
            {"role": "user", "content": "frage"},
            {
                "role": "assistant",
                "content": "ich sehe nach",
                "tool_calls": [
                    {
                        "id": "t1",
                        "function": {
                            "name": "search_symbols",
                            "arguments": '{"query": "a"}',
                        },
                    },
                    {
                        "id": "t2",
                        "function": {
                            "name": "search_symbols",
                            "arguments": '{"query": "b"}',
                        },
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "t1", "content": "erstes Ergebnis"},
            {"role": "tool", "tool_call_id": "t2", "content": "zweites Ergebnis"},
        ],
        TOOLS,
    )

    payload = captured["payload"]
    assert payload["system"] == "sei knapp"
    roles = [message["role"] for message in payload["messages"]]
    assert roles == ["user", "assistant", "user"]

    assistant_blocks = payload["messages"][1]["content"]
    assert assistant_blocks[0] == {"type": "text", "text": "ich sehe nach"}
    assert [block["type"] for block in assistant_blocks[1:]] == ["tool_use", "tool_use"]
    assert assistant_blocks[1]["input"] == {"query": "a"}

    results = payload["messages"][2]["content"]
    assert [block["tool_use_id"] for block in results] == ["t1", "t2"]


# --------------------------------------------------------------------------- #
# DeepSeek / Reasoning-Modelle unter Ollama                                    #
# --------------------------------------------------------------------------- #


def test_ollama_translates_chat_template_kwargs_enable_thinking_to_top_level_think() -> (
    None
):
    """``chat_template_kwargs.enable_thinking: false`` muss Denken abschalten.

    Bisher wurde das Feld auf dem Ollama-Pfad stillschweigend weggeworfen — ein
    Reasoning-Modell (deepseek) dachte also ungeachtet der Konfiguration weiter.
    Ollama schaltet Denken seit 0.5+0 ueber das Top-Level-Feld ``think``.
    """
    captured: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["payload"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"content": "fertig"}})

    router = _router("ollama", handler)
    router.chat_with_tools(
        [{"role": "user", "content": "frage"}],
        None,
        overrides={
            "model": "deepseek-r1",
            "extra": {
                "chat_template_kwargs": {"enable_thinking": False},
                "keep_alive": "0s",
            },
        },
    )
    assert captured["payload"].get("think") is False
    # chat_template_kwargs darf nicht durchgereicht werden — Ollama kennt es nicht.
    assert "chat_template_kwargs" not in captured["payload"]
    assert "chat_template_kwargs" not in captured["payload"].get("options", {})


def test_ollama_falls_back_to_thinking_field_when_content_is_empty() -> None:
    """Ollama legt Chain-of-Thought in ``message.thinking`` und laesst content leer.

    Ohne den Fallback saehe der Aufrufer einen leeren String und meldete
    „keine Antwort", obwohl das Modell Reasoning geliefert hat.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": "",
                    "thinking": "ich ueberlege... die Antwort ist 42",
                }
            },
        )

    router = _router("ollama", handler)
    text = router.chat([{"role": "user", "content": "frage"}])
    # Der Fallback reicht das Reasoning als Text durch (spiegelt den OpenAI-Pfad
    # mit ``reasoning_content``); das Flag erlaubt Aufrufern, das zu erkennen.
    assert "die Antwort ist 42" in text
    assert router.last_response_metadata["reasoning_fallback"] is True


def test_unterminated_think_block_sets_reasoning_truncated() -> None:
    """Ein abgeschnittener ``<think>``-Block darf nicht als Antwort erscheinen,

    aber der Aufrufer muss erfahren, *warum* der Text leer ist — sonst ist
    „Token-Budget im Denken verbraucht" von „keine Antwort" nicht zu unterscheiden.
    """

    def handler(request: httpx.Request) -> httpx.Response:
        # Ollama gibt den Content inkl. oeffnendem think-Tag zurueck, wenn das
        # Modell mitten im Denken das Token-Limit trifft (kein schliessendes Tag).
        return httpx.Response(
            200,
            json={"message": {"content": "<think>also zuerst muss ich"}},
        )

    router = _router("ollama", handler)
    text = router.chat([{"role": "user", "content": "frage"}])
    assert text == ""
    assert router.last_response_metadata["reasoning_truncated"] is True


def test_ollama_optional_json_and_thinking_controls_fall_back_without_model_rules():
    seen = []

    def handler(request):
        payload = json.loads(request.content)
        seen.append(payload)
        if "think" in payload:
            return httpx.Response(
                400, json={"error": "model does not support thinking"}
            )
        if "format" in payload:
            return httpx.Response(422, json={"error": "JSON format is unsupported"})
        return httpx.Response(200, json={"message": {"content": '{"ok":true}'}})

    router = _router("ollama", handler)
    result = router.chat(
        [{"role": "user", "content": "Return JSON"}],
        overrides={
            "extra": {
                "json_mode": True,
                "chat_template_kwargs": {"enable_thinking": False},
            }
        },
    )
    assert result == '{"ok":true}'
    assert len(seen) == 3
    assert all(p["model"] == "test-model" for p in seen)
    assert router.last_response_metadata["response_format_fallback"]
    assert router.last_response_metadata["thinking_control_fallback"]


def test_server_prefilled_thinking_tag_never_leaks_reasoning_or_its_json():
    def handler(request):
        return httpx.Response(
            200,
            json={
                "message": {
                    "content": 'Consider {"wrong":true} first.</think>{"status":"ready"}'
                }
            },
        )

    router = _router("ollama", handler)
    assert (
        router.chat([{"role": "user", "content": "Return JSON"}])
        == '{"status":"ready"}'
    )


def test_prefilled_thinking_without_a_final_answer_stays_empty():
    from query.llm_router import strip_reasoning_blocks

    assert strip_reasoning_blocks('Draft {"wrong":true}</THINK>') == ""
    metadata = {}
    assert (
        strip_reasoning_blocks(
            "First thought.</think><think>Unfinished", metadata=metadata
        )
        == ""
    )
    assert metadata["reasoning_truncated"]
