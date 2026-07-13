"""EntityExtractor: Text-Aufbereitung und Chunking des Papertexts. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from extraction.text_normalization import normalize_scientific_text

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class TextPrepMixin(_Base):
    """Text-Aufbereitung und Chunking des Papertexts."""

    @classmethod
    def _build_extraction_text(cls, paper_text: str, max_chars: int = 60000) -> str:
        """
        Build a bounded paper text that preserves full-paper coverage.

        The previous extractor capped input at 12k characters, which can remove
        algorithm mentions from long surveys. This keeps much more text while
        still staying below a 32k-token context for typical parsed papers.
        """
        text = cls._clean_extraction_source_text(paper_text)
        if len(text) <= max_chars:
            return text

        head_chars = max_chars // 3
        tail_chars = max_chars // 6
        middle_budget = max_chars - head_chars - tail_chars
        keywords = [
            r"Q[\s-]?learning",
            r"SARSA",
            r"TD\s*\(?\s*(?:lambda|\\lambda|λ)\s*\)?",
            r"REINFORCE",
            r"Actor[-\s]?Critic",
            r"Dynamic Programming",
            r"baseline",
            r"taxonomy",
            r"survey",
            r"method",
            r"Table\s+\d+",
            r"equation|formula|theorem|proof",
        ]

        excerpts: list[str] = [text[:head_chars].strip()]
        seen_spans: set[tuple[int, int]] = set()
        window = 1800

        for pattern in keywords:
            for match in re.finditer(pattern, text, flags=re.IGNORECASE):
                start = max(0, match.start() - window // 2)
                end = min(len(text), match.end() + window // 2)
                span = (start, end)
                if span in seen_spans:
                    continue
                seen_spans.add(span)
                excerpts.append(text[start:end].strip())
                if sum(len(item) for item in excerpts) >= head_chars + middle_budget:
                    break
            if sum(len(item) for item in excerpts) >= head_chars + middle_budget:
                break

        excerpts.append(text[-tail_chars:].strip())
        return "\n\n---\n\n".join(item for item in excerpts if item)[:max_chars]

    @classmethod
    def _build_extraction_chunks(cls, 
        paper_text: str,
        context_size: int = 32768,
        max_chunk_chars: int | None = None,
        overlap_chars: int = 900,
        max_chunks: int = 8,
    ) -> list[str]:
        """
        Split long papers into independent extraction windows.

        Local models often under-enumerate when a whole paper is supplied even
        when it fits into context. Chunking structural extraction keeps each
        call focused and guarantees a fresh message list per chunk/paper.
        """
        text = cls._clean_extraction_source_text(paper_text)
        if not text:
            return [""]

        budget = max_chunk_chars or cls._chunk_char_budget(context_size)
        if len(text) <= budget:
            return [text]

        raw_units = re.split(r"\n\s*(?:---PAGE BREAK---|\f)\s*\n|\n(?=\d+(?:\.\d+)*\s+[A-Z][^\n]{3,100}\n)", text)
        units = [unit.strip() for unit in raw_units if unit and unit.strip()]
        if not units:
            units = [text]

        chunks: list[str] = []
        current = ""
        for unit in units:
            if len(unit) > budget:
                if current:
                    chunks.append(current.strip())
                    current = ""
                for start in range(0, len(unit), budget - overlap_chars):
                    part = unit[start : start + budget]
                    if part.strip():
                        chunks.append(part.strip())
                continue

            candidate = f"{current}\n\n{unit}".strip() if current else unit
            if len(candidate) > budget and current:
                chunks.append(current.strip())
                overlap = current[-overlap_chars:] if overlap_chars else ""
                current = f"{overlap}\n\n{unit}".strip()
            else:
                current = candidate

        if current.strip():
            chunks.append(current.strip())
        chunks = chunks or [text[:budget]]
        if len(chunks) <= max_chunks:
            return chunks

        selected_indexes = [
            round(index * (len(chunks) - 1) / (max_chunks - 1))
            for index in range(max_chunks)
        ]
        return [chunks[index] for index in selected_indexes]

    @staticmethod
    def _chunk_char_budget(context_size: int) -> int:
        """
        Estimate a safe per-call paper-text budget for local OpenAI-compatible servers.

        LM Studio/llama.cpp rejects requests when the prompt alone exceeds the
        loaded model context. The configured context can be larger than the
        server's actual slot, so keep a conservative prompt reserve for the
        structural instructions, candidate hints, and chat template overhead.
        """
        try:
            ctx = max(4096, int(context_size))
        except (TypeError, ValueError):
            ctx = 32768

        prompt_overhead_tokens = 5200
        usable_prompt_tokens = max(1200, ctx - prompt_overhead_tokens)
        estimated_chars_per_token = 1.25
        return max(6000, min(18000, int(usable_prompt_tokens * estimated_chars_per_token)))

    @staticmethod
    def _clean_extraction_source_text(paper_text: str) -> str:
        """Remove parser page-break artifacts before LLM and deterministic extraction."""
        text = normalize_scientific_text(paper_text)
        page_break = r"(?:---\s*PAGE\s*BREAK\s*---|---\s*Page\s*Break\s*---|\f)"
        text = re.sub(
            rf"\bModi\s*{page_break}\s*Cation\b",
            "Modification",
            text,
            flags=re.IGNORECASE,
        )

        def join_suffix(match: re.Match[str]) -> str:
            prefix = match.group(1)
            suffix = match.group(2)
            return prefix + suffix.lower()

        text = re.sub(
            rf"\b([A-Za-z]{{3,}})\s*{page_break}\s*(Cation|Fication|Tion|Zation|Sation|Ment|Ness|Able|Ible|Ing|Ed|Al|Ity)\b",
            join_suffix,
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(page_break, "\n\n", text, flags=re.IGNORECASE)
        text = re.sub(r"\bModi\s+Cation\b", "Modification", text, flags=re.IGNORECASE)
        text = re.sub(
            r"\b([A-Za-z]{3,})\s+(Cation|Fication|Tion|Zation|Sation)\b",
            lambda match: match.group(1) + match.group(2).lower(),
            text,
            flags=re.IGNORECASE,
        )
        text = re.sub(r"[ \t]+", " ", text)
        text = re.sub(r"\n{3,}", "\n\n", text)
        return text.strip()
