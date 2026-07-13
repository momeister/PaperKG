"""Shared fake LLM routers for phase-3 tests."""
import json
from typing import Any


class FakeLLMRouter:
    """Mock LLMRouter for testing."""

    def __init__(self, response_json: dict[str, Any] | None = None):
        self.response_json = response_json or {
            "paper_type": "research",
            "concepts": [{"label": "test", "context": "test context", "confidence": 0.9}],
            "methods": [],
            "claims": [],
            "cross_domain_hints": [],
            "terminology_conflicts": [],
            "temporal_coverage": {"paper_year": None, "reviewed_period": None},
            "mathematical_content": {"has_formulas": False, "formula_types": []},
            "language_detected": "en",
        }
        self.last_messages = None
        self.last_provider = None
        self.last_overrides = None
        self.chat_json_calls = 0

    def chat_json(self, messages, provider=None, overrides=None):
        self.chat_json_calls += 1
        self.last_messages = messages
        self.last_provider = provider
        self.last_overrides = overrides
        return self.response_json

    def chat(self, messages, provider=None, overrides=None):
        self.last_messages = messages
        self.last_provider = provider
        self.last_overrides = overrides
        return json.dumps(self.response_json)


class SequenceLLMRouter:
    """Mock router that returns one raw chat response per call."""

    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls: list[dict[str, Any]] = []
        self.last_response_metadata = {}

    def chat(self, messages, provider=None, overrides=None):
        self.calls.append({"messages": messages, "provider": provider, "overrides": overrides})
        if not self.responses:
            raise AssertionError("No fake LLM responses left")
        return self.responses.pop(0)

