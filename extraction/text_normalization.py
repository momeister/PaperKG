from __future__ import annotations

import re
import unicodedata
from typing import Any


LIGATURE_REPLACEMENTS = {
    "\ufb00": "ff",
    "\ufb01": "fi",
    "\ufb02": "fl",
    "\ufb03": "ffi",
    "\ufb04": "ffl",
    "\ufb05": "st",
    "\ufb06": "st",
}

MOJIBAKE_REPLACEMENTS = {
    "ÃŽÂ»": "lambda",
    "Î»": "lambda",
    "λ": "lambda",
    "Â": "",
}

DASH_REPLACEMENTS = {
    "\u2010": "-",
    "\u2011": "-",
    "\u2012": "-",
    "\u2013": "-",
    "\u2014": "-",
    "\u2212": "-",
}


def normalize_scientific_text(value: Any) -> str:
    """Normalize parser/LLM text before label matching, slugging, and storage."""
    text = str(value or "")
    for source, target in LIGATURE_REPLACEMENTS.items():
        text = text.replace(source, target)
    text = unicodedata.normalize("NFKC", text)
    for source, target in MOJIBAKE_REPLACEMENTS.items():
        text = text.replace(source, target)
    for source, target in DASH_REPLACEMENTS.items():
        text = text.replace(source, target)
    text = text.replace("\u00ad", "")
    return text


def normalize_whitespace(value: Any) -> str:
    return re.sub(r"\s+", " ", normalize_scientific_text(value)).strip()


def normalize_key(value: Any) -> str:
    return re.sub(r"[^a-z0-9]+", "", normalize_scientific_text(value).lower())


def slugify_label(value: Any, max_length: int = 96) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", normalize_scientific_text(value).lower()).strip("-")
    return slug[:max_length]
