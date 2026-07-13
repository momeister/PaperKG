"""EntityExtractor: Paper-Typ, Titel, arXiv-IDs und Jahr. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any

from extraction.text_normalization import normalize_scientific_text

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class PaperMetaMixin(_Base):
    """Paper-Typ, Titel, arXiv-IDs und Jahr."""

    @staticmethod
    def _normalize_paper_type(value: Any) -> str:
        """Normalize paper type to the supported controlled vocabulary."""
        paper_type = str(value or "research").strip().lower()
        return paper_type if paper_type in {"research", "survey", "theoretical", "benchmark"} else "research"

    @classmethod
    def _detect_paper_type(cls, text: str) -> str | None:
        sample = (text or "")[:12000].lower()
        title = cls._paper_title_from_text(text).lower()
        benchmark_markers = (
            "benchmark",
            "benchmark task",
            "benchmark suite",
            "discovery engine",
            "open-source framework",
        )
        framework_intro = bool(
            re.search(r"\b(we|this paper|we introduce|we present|we propose)\b", sample)
            and re.search(r"\b(framework|engine|toolkit|library|platform)\b", sample)
            and re.search(r"\b(benchmark|dataset|task|evaluate|evaluation|reproduce|reproduces|mnist|sst-?2)\b", sample)
        )
        if any(marker in title for marker in benchmark_markers) or framework_intro:
            return "benchmark"
        survey_markers = (
            r"\ba survey\b",
            r"\bthis survey\b",
            r"\bsurvey of\b",
            r"\breview paper\b",
            r"\bsystematic review\b",
            r"\bliterature review\b",
            r"\bwe survey\b",
            r"\bwe review the literature\b",
        )
        if any(re.search(marker, title) for marker in survey_markers):
            return "survey"
        if any(re.search(marker, sample) for marker in survey_markers):
            return "survey"
        return None

    @classmethod
    def _resolve_paper_type(cls, semantic_type: Any, detected_type: str | None, text: str) -> str:
        """Combine model paper-type output with deterministic safeguards."""
        paper_type = cls._normalize_paper_type(semantic_type)
        detected = cls._normalize_paper_type(detected_type) if detected_type else None
        if detected == "benchmark" and paper_type in {"research", "survey", "benchmark"}:
            return "benchmark"
        if detected == "survey" and paper_type == "research":
            return "survey"
        return paper_type

    @classmethod
    def _paper_title_from_text(cls, text: str) -> str:
        """Best-effort title extraction from the parsed paper header."""
        for raw_line in (text or "").splitlines()[:30]:
            line = re.sub(r"\s+", " ", raw_line).strip(" #\t")
            if not line:
                continue
            lowered = line.lower()
            if cls._is_header_noise_title_line(line):
                continue
            if lowered in {"abstract", "introduction"}:
                continue
            if lowered in {"article", "research article", "original article", "original research", "open access"}:
                continue
            if re.match(r"^(?:arxiv|doi|http|www\.|journal|conference)\b", lowered):
                continue
            if re.match(r"^(?:downloaded from|published by|available online)\b", lowered):
                continue
            if len(line) < 6 or len(line) > 180:
                continue
            return line.title() if line.isupper() else line
        return ""

    @staticmethod
    def _is_header_noise_title_line(line: str) -> bool:
        """Reject common PDF header/footer lines that precede the real title."""
        cleaned = re.sub(r"\s+", " ", line or "").strip()
        lowered = cleaned.lower()
        if not lowered:
            return True
        exact_noise = {
            "preprint",
            "draft",
            "accepted manuscript",
            "noname manuscript no.",
            "noname manuscript no",
            "science china",
            "conference paper",
            "technical report",
            "springer nature latex template",
        }
        if lowered in exact_noise:
            return True
        noisy_patterns = [
            r"^draft version\b",
            r"^accepted for publication\b",
            r"^accepted manuscript\b",
            r"^arxiv preprint\b",
            r"^preprint submitted\b",
            r"^submitted to\b",
            r"^published in\b",
            r"^proceedings of\b",
            r"^copyright\b",
            r"^©",
            r"^cern[-\s]open[-\s]\d",
            r"^european organization for nuclear research",
            r"^journal of\b",
            r"^springer nature\b.*latex template",
            r"^information research\s*-\s*vol\.",
            r"^\d+(?:st|nd|rd|th)\s+conte?csi\b",
            r"^\d+(?:st|nd|rd|th)\s+.*international conference on\b",
            r"^draft version\s*(?:january|february|march|april|may|june|july|august|september|october|november|december)\b",
            r"^\d{1,2}\s+(?:january|february|march|april|may|june|july|august|september|october|november|december)\s+\d{4}$",
        ]
        return any(re.search(pattern, lowered) for pattern in noisy_patterns)

    @classmethod
    def _title_tokens(cls, title: str) -> set[str]:
        normalized = normalize_scientific_text(title).lower()
        tokens = re.findall(r"[a-z0-9]+", normalized)
        return {
            token
            for token in tokens
            if len(token) >= 3 and token not in cls.TITLE_STOPWORDS and not token.isdigit()
        }

    @classmethod
    def _titles_conflict(cls, first: str, second: str) -> bool:
        """Return true only for strong title conflicts, not small formatting drift."""
        first_clean = re.sub(r"\s+", " ", normalize_scientific_text(first)).strip().lower()
        second_clean = re.sub(r"\s+", " ", normalize_scientific_text(second)).strip().lower()
        if not first_clean or not second_clean:
            return False
        if cls._normalize_label(first_clean) == cls._normalize_label(second_clean):
            return False

        first_tokens = cls._title_tokens(first_clean)
        second_tokens = cls._title_tokens(second_clean)
        if len(first_tokens) < 3 or len(second_tokens) < 3:
            return False

        shared = first_tokens & second_tokens
        overlap = len(shared) / max(1, min(len(first_tokens), len(second_tokens)))
        jaccard = len(shared) / max(1, len(first_tokens | second_tokens))
        sequence_similarity = SequenceMatcher(None, first_clean, second_clean).ratio()
        return overlap < 0.35 and jaccard < 0.25 and sequence_similarity < 0.55

    @staticmethod
    def _coerce_year(value: Any) -> int | None:
        try:
            year = int(value)
        except (TypeError, ValueError):
            return None
        return year if 1500 <= year <= 3000 else None

    @classmethod
    def _extract_arxiv_identifier(cls, value: str) -> str:
        match = re.search(
            r"\b(?:arxiv:\s*)?(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?\b",
            value,
            re.IGNORECASE,
        )
        if match:
            return f"arxiv:{match.group(1)}{match.group(2)}.{match.group(3)}"
        return cls._extract_legacy_arxiv_identifier(value)

    @classmethod
    def _extract_legacy_arxiv_identifier(cls, value: str) -> str:
        match = re.search(
            rf"(?<![A-Za-z0-9])({cls.LEGACY_ARXIV_CATEGORY_RE})\s*[/_:.-]\s*(\d{{7}})(?:v\d+)?(?!\d)",
            value or "",
            re.IGNORECASE,
        )
        if not match:
            return ""
        return f"arxiv:{match.group(1).lower()}/{match.group(2)}"

    @classmethod
    def _extract_front_matter_arxiv_identifier(cls, paper_text: str) -> str:
        body_text = cls._text_before_references(paper_text or "")
        front_matter = body_text[:20000]
        explicit = cls._extract_explicit_arxiv_identifier(front_matter[:8000])
        if explicit:
            return explicit

        introduction_match = re.search(
            r"(?:^|\n)\s*(?:#{1,6}\s*)?(?:\d+\.?\s*)?(?:introduction|i\.\s+introduction)\b",
            front_matter,
            re.IGNORECASE,
        )
        if introduction_match:
            explicit = cls._extract_explicit_arxiv_identifier(front_matter[: introduction_match.start()])
            if explicit:
                return explicit

        explicit = cls._extract_explicit_arxiv_identifier(front_matter)
        if explicit:
            return explicit

        abstract_match = re.search(r"\babstract\b", front_matter, re.IGNORECASE)
        fallback_window = front_matter[: abstract_match.end() + 250] if abstract_match else front_matter[:2500]
        return cls._extract_arxiv_identifier(fallback_window)

    @classmethod
    def _extract_explicit_arxiv_identifier(cls, value: str) -> str:
        match = re.search(
            r"\barxiv\s*:\s*(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?\b",
            value or "",
            re.IGNORECASE,
        )
        if not match:
            match = re.search(
                r"\barxiv\.org/(?:abs|pdf)/(\d{2})(\d{2})\.(\d{4,5})(?:v\d+)?\b",
                value or "",
                re.IGNORECASE,
            )
        if match:
            return f"arxiv:{match.group(1)}{match.group(2)}.{match.group(3)}"
        return cls._extract_legacy_arxiv_identifier(value or "")

    @classmethod
    def _arxiv_publication_year(cls, arxiv_id: str) -> int | None:
        match = re.search(r"\b(?:arxiv:\s*)?(\d{2})(\d{2})\.\d{4,5}", arxiv_id, re.IGNORECASE)
        if not match:
            match = re.search(
                rf"(?<![A-Za-z0-9])(?:arxiv:\s*)?{cls.LEGACY_ARXIV_CATEGORY_RE}\s*/\s*(\d{{2}})(\d{{2}})\d{{3}}",
                arxiv_id or "",
                re.IGNORECASE,
            )
        if not match:
            return None
        year = int(match.group(1))
        month = int(match.group(2))
        if not 1 <= month <= 12:
            return None
        return 2000 + year if year < 90 else 1900 + year

    @staticmethod
    def _detect_paper_year(text: str) -> int | None:
        header = (text or "")[:6000]
        current_year = datetime.now().year + 1
        candidates = [
            int(match.group(0))
            for match in re.finditer(r"\b(?:19|20)\d{2}\b", header)
            if 1900 <= int(match.group(0)) <= current_year
        ]
        if not candidates:
            return None

        dated = re.search(
            r"\b(?:January|February|March|April|May|June|July|August|September|October|November|December)\s+\d{1,2},\s+((?:19|20)\d{2})\b",
            header,
            flags=re.IGNORECASE,
        )
        if dated:
            return int(dated.group(1))
        return candidates[0]
