"""EntityExtractor: Robustes Parsen/Sanitizen von LLM-JSON. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import json
import logging
import re
from typing import TYPE_CHECKING, Any

from extraction.entity_extractor._shared import (
    ParsedLLMResponse,
)

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class JsonParseMixin(_Base):
    """Robustes Parsen/Sanitizen von LLM-JSON."""

    @staticmethod
    def _parsed_payload_score(data: dict[str, Any]) -> int:
        score = 0
        for value in data.values():
            if isinstance(value, list):
                score += len(value)
            elif isinstance(value, dict):
                score += len(value)
            elif value:
                score += 1
        return score

    @classmethod
    def _parse_json_robust(cls, raw_text: str, default: dict[str, Any]) -> ParsedLLMResponse:
        """
        Parse model JSON with clean, trimmed, and partial fallbacks.

        Fallback order:
        1. Direct json.loads.
        2. Trim from first "{" to last "}".
        3. Partial reconstruction from obvious top-level scalar and array keys.
        """
        raw = cls._sanitize_json_text(raw_text)
        if not raw:
            return ParsedLLMResponse(data=dict(default), parse_quality="partial", raw_text=raw_text)

        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                return ParsedLLMResponse(data=parsed, parse_quality="clean", raw_text=raw_text)
        except json.JSONDecodeError:
            pass

        start = raw.find("{")
        end = raw.rfind("}")
        if start != -1 and end != -1 and end > start:
            trimmed = raw[start : end + 1]
            try:
                parsed = json.loads(trimmed)
                if isinstance(parsed, dict):
                    return ParsedLLMResponse(data=parsed, parse_quality="trimmed", raw_text=raw_text)
            except json.JSONDecodeError:
                pass

        partial = dict(default)
        for key in partial:
            value = cls._extract_partial_json_value(raw, key)
            if value is not None:
                partial[key] = value
        for key in ("paper_type", "language_detected"):
            match = re.search(rf'"{key}"\s*:\s*"([^"]+)"', raw)
            if match:
                partial[key] = match.group(1)
        return ParsedLLMResponse(data=partial, parse_quality="partial", raw_text=raw_text)

    @staticmethod
    def _sanitize_json_text(raw_text: str) -> str:
        """Remove common local-model wrappers before JSON parsing."""
        raw = (raw_text or "").strip()
        raw = re.sub(r"<think>[\s\S]*?</think>", "", raw, flags=re.IGNORECASE).strip()
        fenced = re.search(r"```(?:json)?\s*([\s\S]*?)\s*```", raw, flags=re.IGNORECASE)
        if fenced:
            raw = fenced.group(1).strip()
        return raw

    @classmethod
    def _parse_json_array_robust(cls, raw_text: str) -> ParsedLLMResponse:
        """Parse a model response expected to be a JSON array."""
        raw = cls._sanitize_json_text(raw_text)
        if not raw:
            return ParsedLLMResponse(data=[], parse_quality="partial", raw_text=raw_text)

        try:
            parsed = json.loads(raw)
            return ParsedLLMResponse(
                data=parsed if isinstance(parsed, list) else [],
                parse_quality="clean" if isinstance(parsed, list) else "partial",
                raw_text=raw_text,
            )
        except json.JSONDecodeError:
            pass

        start = raw.find("[")
        end = raw.rfind("]")
        if start != -1 and end != -1 and end > start:
            trimmed = raw[start : end + 1]
            try:
                parsed = json.loads(trimmed)
                return ParsedLLMResponse(
                    data=parsed if isinstance(parsed, list) else [],
                    parse_quality="trimmed" if isinstance(parsed, list) else "partial",
                    raw_text=raw_text,
                )
            except json.JSONDecodeError:
                pass
        return ParsedLLMResponse(data=[], parse_quality="partial", raw_text=raw_text)

    @staticmethod
    def _raw_has_json_key(raw_text: str, key: str) -> bool:
        """Check whether a key appears in the raw JSON-ish response text."""
        return re.search(rf'"{re.escape(key)}"\s*:', raw_text or "") is not None

    @staticmethod
    def _extract_partial_json_value(raw: str, key: str) -> Any | None:
        """Extract a single top-level JSON-ish value for partial recovery."""
        key_match = re.search(rf'"{re.escape(key)}"\s*:\s*', raw)
        if not key_match:
            return None

        value_start = key_match.end()
        opener = raw[value_start : value_start + 1]
        if opener not in {"[", "{"}:
            scalar = re.match(r'"([^"]*)"|true|false|null|-?\d+(?:\.\d+)?', raw[value_start:])
            if not scalar:
                return None
            text = scalar.group(0)
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return None

        closer = "]" if opener == "[" else "}"
        depth = 0
        in_string = False
        escaped = False
        for index in range(value_start, len(raw)):
            char = raw[index]
            if escaped:
                escaped = False
                continue
            if char == "\\" and in_string:
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if in_string:
                continue
            if char == opener:
                depth += 1
            elif char == closer:
                depth -= 1
                if depth == 0:
                    candidate = raw[value_start : index + 1]
                    try:
                        return json.loads(candidate)
                    except json.JSONDecodeError:
                        return None
        return None
