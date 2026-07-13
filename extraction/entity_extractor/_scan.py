"""EntityExtractor: Deterministischer Regex-Scan des Papertexts. (Mixin)

Split out of extraction/entity_extractor.py. Behaviour unchanged.
"""
from __future__ import annotations

import logging
import re
from collections import Counter
from typing import TYPE_CHECKING, Any

from extraction.entity_extractor._shared import (
    DeterministicScanResult,
)
from extraction.text_normalization import normalize_scientific_text

if TYPE_CHECKING:
    from extraction.entity_extractor import EntityExtractor

    _Base = EntityExtractor
else:
    _Base = object

logger = logging.getLogger(__name__)


class ScanMixin(_Base):
    """Deterministischer Regex-Scan des Papertexts."""

    @classmethod
    def _scan_paper_text(cls, paper_text: str) -> DeterministicScanResult:
        """
        Mine obvious entities locally before asking the LLM.

        This is deliberately high-recall. Items are marked as auto-detected so
        downstream review can distinguish deterministic catches from model
        judgments.
        """
        text = paper_text or ""
        body_text = cls._text_before_references(text)
        is_official_statistics = cls._looks_like_official_statistics(body_text)
        is_rl_emotion = cls._looks_like_rl_emotion_paper(body_text)
        is_qml = cls._looks_like_quantum_ml_paper(body_text)
        concepts: list[dict[str, Any]] = []
        methods: list[dict[str, Any]] = []
        seen_concepts: set[str] = set()
        seen_methods: set[str] = set()

        def add_concept(label: str, context: str, confidence: float = 0.72) -> None:
            normalized = cls._normalize_label(label)
            if not normalized or normalized in seen_concepts:
                return
            seen_concepts.add(normalized)
            concepts.append(
                {
                    "label": cls._clean_label(label),
                    "context": context[:360] or "auto-detected from paper text",
                    "confidence": confidence,
                    "auto_detected": True,
                    "candidate_source": "deterministic_scan",
                }
            )

        def add_method(label: str, description: str, confidence: float = 0.70) -> None:
            normalized = cls._normalize_label(label)
            if not normalized or normalized in seen_methods:
                return
            seen_methods.add(normalized)
            methods.append(
                {
                    "label": cls._clean_label(label),
                    "domain": "unknown",
                    "description": description[:360] or "auto-detected from paper text",
                    "source_type": "background",
                    "confidence": confidence,
                    "auto_detected": True,
                    "candidate_source": "deterministic_scan",
                }
            )

        for label, pattern in cls.KNOWN_CONCEPT_PATTERNS:
            if label in cls.OFFICIAL_STATISTICS_LABELS and not is_official_statistics:
                continue
            if label in cls.RL_EMOTION_LABELS and not is_rl_emotion:
                continue
            if label in cls.QML_LABELS and not is_qml:
                continue
            match = re.search(pattern, body_text, flags=re.IGNORECASE)
            if match:
                add_concept(label, cls._context_for_match(body_text, match), 0.74)

        method_patterns = (
            ("Checklist for Changing Data Sources", r"\bchecklist\b"),
            ("Data Source Monitoring", r"\bmonitor(?:ing)? changes? in (?:incoming )?data\b|\bmonitoring\b"),
            ("Robust Data Sourcing", r"\brobust(?:ness)? in (?:both )?data sourcing\b|\brobust data sourcing\b"),
            ("Robust Statistical Techniques", r"\brobust(?:ness)? in .*statistical techniques\b|\brobust statistical techniques\b"),
            ("Model Retraining", r"\bperiodically reevaluate and retrain models\b|\bretrain(?:ing)? models\b"),
            ("Data Pipeline Monitoring", r"\bdata pipelines? should (?:be designed to )?monitor\b|\bpipeline monitoring\b"),
            ("Precautionary Measures", r"\bprecautionary measures\b"),
        )
        for label, pattern in method_patterns:
            if not is_official_statistics:
                continue
            match = re.search(pattern, body_text, flags=re.IGNORECASE)
            if match:
                add_method(label, cls._context_for_match(body_text, match), 0.74)

        rl_method_patterns = (
            ("Q-learning", r"\bQ[\s-]?learning\b"),
            ("SARSA", r"\bSARSA\b"),
            ("TD(lambda)", r"\bTD\s*\(?\s*(?:lambda|\\lambda|Î»|λ)\s*\)?|\bTD\s*\(\s*(?:Î»|λ)\s*\)"),
            ("Actor-Critic", r"\bActor[-\s]?Critic\b"),
            ("Dynamic Programming", r"\bDynamic Programming\b"),
            ("Reward shaping", r"\breward shaping\b|\bshap(?:e|ed|ing)\s+rewards?\b"),
            ("Policy gradient", r"\bpolicy gradient(?:s)?\b"),
            ("Value iteration", r"\bvalue iteration\b"),
            ("Homeostatic reinforcement learning", r"\bhomeostatic reinforcement learning\b"),
            ("Appraisal-based reward modulation", r"\bappraisal\b.{0,80}\breward\b|\breward\b.{0,80}\bappraisal\b"),
        )
        for label, pattern in rl_method_patterns:
            if not is_rl_emotion:
                continue
            match = re.search(pattern, body_text, flags=re.IGNORECASE)
            if match:
                add_method(label, cls._context_for_match(body_text, match), 0.76)

        for match in re.finditer(r"\b([A-Z][A-Za-z][A-Za-z0-9 /-]{2,80}?)\s+\(([A-Z][A-Z0-9-]{1,12})\)", body_text):
            long_form, short_form = match.group(1).strip(), match.group(2).strip()
            long_form = cls._trim_acronym_long_form(long_form, short_form)
            if cls._is_good_acronym_pair(long_form, short_form):
                add_concept(long_form, cls._context_for_match(body_text, match), 0.68)
                add_concept(short_form, cls._context_for_match(body_text, match), 0.62)

        heading_candidates = cls._heading_candidates(body_text)
        for label, context in heading_candidates:
            add_concept(label, context, 0.64)

        if is_official_statistics:
            for phrase, count in cls._repeated_domain_phrases(body_text).most_common(20):
                if count >= 2:
                    add_concept(phrase, f"Repeated phrase in parsed paper text ({count} mentions).", 0.60)
        if is_rl_emotion:
            for phrase, count in cls._repeated_rl_emotion_phrases(body_text).most_common(20):
                if count >= 2:
                    add_concept(phrase, f"Repeated phrase in parsed paper text ({count} mentions).", 0.60)

        return DeterministicScanResult(
            concepts=concepts,
            methods=methods,
            paper_year=cls._detect_paper_year(text),
        )

    @classmethod
    def _heading_candidates(cls, text: str) -> list[tuple[str, str]]:
        rows: list[tuple[str, str]] = []
        for raw_line in (text or "").splitlines()[:500]:
            numbered_heading = re.match(r"^\s*\d+(?:\.\d+)*\s+([A-Z][^.!?]{3,70})\s*$", raw_line)
            if numbered_heading:
                line = numbered_heading.group(1).strip()
            else:
                line = raw_line.strip()
            line = re.sub(r"\s+", " ", line)
            words = line.split()
            if not (4 <= len(line) <= 70 and 1 <= len(words) <= 8):
                continue
            if raw_line.rstrip().endswith((".", ",", ";", ":")) and not numbered_heading:
                continue
            if len(words) > 3 and sum(1 for word in words if word[:1].isupper()) < max(2, len(words) // 2):
                continue
            if not re.search(
                r"\b(data|source|statistics|machine learning|bias|validity|accuracy|availability|ownership|ethics|regulation|privacy|monitoring|robustness|concept drift|frequency|completeness|neutrality)\b",
                line,
                flags=re.IGNORECASE,
            ):
                continue
            rows.append((line.title() if line.isupper() else line, f"Section or heading: {line}"))
        return rows

    @staticmethod
    def _text_before_references(text: str) -> str:
        raw = text or ""
        match = re.search(
            r"(?im)^\s*(?:#+\s*)?(?:\d+\.?\s*)?(?:references|bibliography|works cited|literature cited)\s*$",
            raw,
        )
        if match:
            return raw[: match.start()]
        match = re.search(r"\n\s*(?:references|bibliography)\s*\n", raw, flags=re.IGNORECASE)
        if not match:
            return raw
        return raw[: match.start()]

    @classmethod
    def _looks_like_official_statistics(cls, text: str) -> bool:
        normalized = (text or "").lower()
        return (
            "official statistics" in normalized
            or ("data source" in normalized and "statistical" in normalized)
            or ("data sources" in normalized and "statistics" in normalized)
        )

    @classmethod
    def _looks_like_rl_emotion_paper(cls, text: str) -> bool:
        normalized = (text or "").lower()
        has_rl = "reinforcement learning" in normalized or re.search(r"\bRL\b", text or "") is not None
        has_emotion = (
            "emotion" in normalized
            or "affective" in normalized
            or "valence" in normalized
            or "appraisal" in normalized
        )
        return bool(has_rl and has_emotion)

    @classmethod
    def _looks_like_quantum_ml_paper(cls, text: str) -> bool:
        normalized = (text or "").lower()
        has_quantum_context = (
            "quantum" in normalized
            or "photonic" in normalized
            or "fock space" in normalized
            or "linear-optical" in normalized
            or re.search(r"\bQML\b", text or "") is not None
        )
        has_ml_context = (
            "machine learning" in normalized
            or "neural network" in normalized
            or "neural networks" in normalized
            or "classification" in normalized
            or "benchmark" in normalized
            or "dataset" in normalized
        )
        return bool(has_quantum_context and has_ml_context)

    @classmethod
    def _is_good_acronym_pair(cls, long_form: str, short_form: str) -> bool:
        if not (2 <= len(short_form) <= 8 and 2 <= len(long_form.split()) <= 8):
            return False
        lowered = long_form.lower()
        reject_terms = {
            "conference",
            "proceedings",
            "journal",
            "transactions",
            "symposium",
            "congress",
            "workshop",
            "vol",
            "pp",
        }
        if any(term in lowered for term in reject_terms):
            return False
        first_word = re.match(r"[A-Za-z]+", long_form)
        if first_word and first_word.group(0).lower() in {"for", "in", "the", "this", "these", "those", "a", "an", "we"}:
            return False
        if len(long_form) > 70:
            return False
        initials = "".join(word[0] for word in re.findall(r"[A-Za-z]+", long_form)).upper()
        return short_form.upper() == initials[: len(short_form)] or short_form.upper() in initials

    @staticmethod
    def _trim_acronym_long_form(long_form: str, short_form: str) -> str:
        words = re.findall(r"[A-Za-z][A-Za-z0-9-]*", long_form or "")
        acronym = (short_form or "").upper()
        for index in range(len(words)):
            suffix = words[index:]
            initials = "".join(word[0] for word in suffix).upper()
            if initials == acronym:
                return " ".join(suffix)
        return long_form

    @staticmethod
    def _repeated_domain_phrases(text: str) -> Counter[str]:
        normalized = re.sub(r"[^A-Za-z0-9\s-]", " ", text or "").lower()
        words = [word for word in normalized.split() if len(word) > 2]
        domain_heads = {
            "data",
            "statistical",
            "statistics",
            "machine",
            "model",
            "source",
            "quality",
            "concept",
            "public",
            "privacy",
            "regulation",
            "ownership",
        }
        stop = {"the", "and", "for", "with", "from", "that", "this", "are", "can", "will", "have", "has"}
        counts: Counter[str] = Counter()
        for size in (2, 3, 4):
            for index in range(0, max(0, len(words) - size + 1)):
                phrase_words = words[index : index + size]
                if phrase_words[0] not in domain_heads and not any(word in domain_heads for word in phrase_words):
                    continue
                if any(word in stop for word in (phrase_words[0], phrase_words[-1])):
                    continue
                phrase = " ".join(phrase_words)
                if len(phrase) >= 8:
                    counts[phrase.title()] += 1
        return counts

    @staticmethod
    def _repeated_rl_emotion_phrases(text: str) -> Counter[str]:
        normalized = re.sub(r"[^A-Za-z0-9\s-]", " ", text or "").lower()
        words = [word for word in normalized.split() if len(word) > 2]
        domain_heads = {
            "reinforcement",
            "learning",
            "emotion",
            "emotional",
            "affective",
            "appraisal",
            "reward",
            "policy",
            "value",
            "agent",
            "robot",
            "human",
            "motivation",
            "homeostatic",
            "drive",
        }
        stop = {"the", "and", "for", "with", "from", "that", "this", "are", "can", "will", "have", "has", "paper", "article"}
        counts: Counter[str] = Counter()
        for size in (2, 3, 4):
            for index in range(0, max(0, len(words) - size + 1)):
                phrase_words = words[index : index + size]
                if not any(word in domain_heads for word in phrase_words):
                    continue
                if phrase_words[0] in stop or phrase_words[-1] in stop:
                    continue
                phrase = " ".join(phrase_words)
                generic_words = {
                    "reinforcement",
                    "learning",
                    "agent",
                    "agents",
                    "robot",
                    "robots",
                    "human",
                    "humans",
                }
                if all(word in generic_words for word in phrase_words):
                    continue
                if 10 <= len(phrase) <= 70:
                    counts[phrase.title()] += 1
        return counts

    @staticmethod
    def _context_for_match(text: str, match: re.Match[str], window: int = 180) -> str:
        start_floor = max(0, match.start() - window)
        end_ceiling = min(len(text), match.end() + window)
        prefix = text[start_floor: match.start()]
        suffix = text[match.end(): end_ceiling]

        start = start_floor
        sentence_start = max(prefix.rfind(". "), prefix.rfind("! "), prefix.rfind("? "), prefix.rfind("\n"))
        if sentence_start >= 0:
            start = start_floor + sentence_start + 1

        end = end_ceiling
        suffix_boundary = re.search(r"(?:[.!?]\s+|\n)", suffix)
        if suffix_boundary:
            end = match.end() + suffix_boundary.end()

        context = re.sub(r"\s+", " ", text[start:end]).strip()
        if context and context[0].islower() and match.start() > start_floor:
            fallback = re.sub(r"\s+", " ", text[match.start():end]).strip()
            if fallback:
                context = fallback
        return context

    @classmethod
    def _clean_label(cls, label: str) -> str:
        cleaned = re.sub(r"\s+", " ", normalize_scientific_text(label)).strip(" .,:;[]{}")
        cleaned = cleaned.replace("---PAGE BREAK---", " ").replace("---Page Break---", " ")
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" .,:;[]{}")
        cleaned = cls._repair_label_fragments(cleaned)
        if cleaned.count("(") < cleaned.count(")"):
            cleaned = cleaned.rstrip(")")
        if cleaned.count(")") < cleaned.count("(") and ")" in str(label or ""):
            cleaned = f"{cleaned})"
        return cleaned[:120]

    @classmethod
    def _merge_entity_lists(cls, *entity_lists: list[Any]) -> list[dict[str, Any]]:
        merged: dict[str, dict[str, Any]] = {}
        order: list[str] = []
        for entity_list in entity_lists:
            for item in entity_list:
                if not isinstance(item, dict):
                    continue
                label = cls._clean_label(str(item.get("label") or item.get("term") or ""))
                normalized = cls._normalize_label(label)
                if not normalized:
                    continue
                candidate = dict(item)
                candidate["label"] = label
                if normalized not in merged:
                    merged[normalized] = candidate
                    order.append(normalized)
                    continue

                existing = merged[normalized]
                existing_conf = cls._coerce_float(existing.get("confidence"), 0.0)
                candidate_conf = cls._coerce_float(candidate.get("confidence"), 0.0)
                if candidate_conf > existing_conf:
                    candidate, existing = existing, candidate
                    merged[normalized] = existing
                if (
                    existing.get("candidate_source") == "deterministic_scan"
                    and candidate.get("candidate_source") != "deterministic_scan"
                ):
                    existing.pop("candidate_source", None)
                for key in ("context", "description"):
                    extra = str(candidate.get(key) or "").strip()
                    current = str(existing.get(key) or "").strip()
                    if extra and extra not in current:
                        existing[key] = (current + " | " + extra).strip(" |")[:700]
                if candidate.get("auto_detected"):
                    existing["auto_detected"] = existing.get("auto_detected", False) or candidate.get("auto_detected")
        return [merged[key] for key in order]
