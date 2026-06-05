"""Tests for the discovery analysis normalizer (pure, no network/LLM)."""
from __future__ import annotations

from query.discovery import normalize_analysis


def test_normalize_analysis_full_payload():
    out = normalize_analysis(
        {
            "topic_summary": "Attention models",
            "methods": ["transformers", "  ", "self-attention"],
            "queries": [
                {"query": "transformer attention", "reason": "core method"},
                "bert pretraining",
            ],
        }
    )
    assert out["topic_summary"] == "Attention models"
    assert out["methods"] == ["transformers", "self-attention"]
    assert out["queries"][0] == {"query": "transformer attention", "reason": "core method"}
    assert out["queries"][1] == {"query": "bert pretraining", "reason": ""}


def test_normalize_analysis_accepts_json_string():
    out = normalize_analysis('{"queries": [{"query": "x"}]}')
    assert out["queries"] == [{"query": "x", "reason": ""}]


def test_normalize_analysis_handles_garbage():
    out = normalize_analysis("not json")
    assert out == {"topic_summary": "", "methods": [], "queries": []}


def test_normalize_analysis_caps_queries_at_eight():
    out = normalize_analysis({"queries": [{"query": f"q{i}"} for i in range(20)]})
    assert len(out["queries"]) == 8
