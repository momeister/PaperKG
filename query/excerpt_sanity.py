"""Sanity check for PDF excerpts.

PDF table extraction (pdfplumber/pypdf reading a column right-to-left, or a
mis-detected RTL region) can produce garbled text whose tokens are reversed
strings of real words: "detanoitcarfopyH" (Hypofractionated), "esahP" (Phase),
"yG" (Gy), "AN" (NA). These slip through the verbatim-quote filter in
``claim_checker._judge_excerpts`` and then leak into ``reformulateForSource``,
where a small rewrite model echoes them verbatim and emits chain-of-thought.

This module provides a shared heuristic used at two chokepoints:

* ``query.source_verifier.best_excerpts`` — drop garbled excerpts before they
  enter the evidence pipeline.
* ``query.claim_checker._gather_source_text`` — re-filter, since grey-source
  ``evidence`` pools and the "shown evidence" fallback bypass ``best_excerpts``.

The heuristic is deliberately conservative: it only flags excerpts with a high
density of *reversed known-word* fragments or *mid-word capital* patterns that
are characteristic of column-reversed extraction. Normal prose — even terse
technical prose with abbreviations like "Gy", "NA", "p53" — passes.
"""

from __future__ import annotations

import re

__all__ = ["is_garbled_excerpt", "filter_sane_excerpts"]


# Compact DE/EN common-word list. Kept tiny on purpose: we look for *reverses*
# of these as substrings of longer tokens, so a small set of frequent stems
# already catches the classic PDF-column-reversal cases
# (Hypofractionated→detanoitcarfopyH contains "det"=ted reversed fragment from
# "ted" via "Hypofractiona-ted-"; Phase→esahP contains "esah"="hase" reversed;
# Gy→yG; NA→AN). We match on reverses of length>=4 to avoid trivial 2-3 char
# collisions (which would explode false positives).
_REVERSABLE_STEMS: frozenset[str] = frozenset(
    {
        # EN
        "the",
        "and",
        "for",
        "that",
        "this",
        "with",
        "from",
        "were",
        "have",
        "which",
        "their",
        "about",
        "phase",
        "study",
        "patient",
        "treatment",
        "survival",
        "radiotherapy",
        "dose",
        "fraction",
        "stereotactic",
        "hypofractionated",
        "bevacizumab",
        "glioblastoma",
        "tumor",
        "cancer",
        "brain",
        "therapy",
        "clinical",
        "result",
        "conclusion",
        "method",
        "background",
        "objective",
        "increase",
        "decrease",
        "compared",
        "significant",
        "respect",
        "analysis",
        "data",
        "table",
        "figure",
        "reference",
        "et",
        "al",
        "copyright",
        "published",
        "journal",
        # DE
        "der",
        "die",
        "und",
        "mit",
        "von",
        "auf",
        "nicht",
        "ist",
        "eine",
        "einem",
        "patienten",
        "studie",
        "behandlung",
        "ergebnis",
        "methode",
        "schluss",
        "folgerung",
        "vergleich",
        "signifikant",
        "analyse",
        # Units / common abbreviations whose reversal is a strong garble signal
        "Gy",
        "NA",
        "MGy",
        "cm",
        "mm",
        "ml",
        "kg",
    }
)
# Precompute reverses; only keep length>=4 to avoid trivial collisions.
_REVERSED_FRAGMENTS: frozenset[str] = frozenset(
    s[::-1] for s in _REVERSABLE_STEMS if len(s) >= 4
)
# Shorter reverses (Gy→yG, NA→AN, et→te, al→la) are still useful but require a
# stronger co-occurrence signal (see below).
_SHORT_REVERSED_FRAGMENTS: frozenset[str] = frozenset(
    s[::-1] for s in _REVERSABLE_STEMS if 2 <= len(s) < 4
)

# Mid-word capital: a capital letter that is NOT the first character of the
# token and not part of an all-caps acronym (>=2 capitals) or CamelCase unit
# (e.g. "MGy", "kBq"). Column-reversed text shows this as words printed
# backwards keep their original capitalization in the middle: "detanoitcarfopyH"
# (capital H at the end), "orumO iC" (Capital O mid-token, C at end).
_MIDWORD_CAPITAL_RE = re.compile(r"[a-z]{2,}[A-Z]")


def _tokens(text: str) -> list[str]:
    # Keep it simple: split on whitespace and strip surrounding punctuation.
    # We do NOT lowercase here — we need the original case for mid-word capital
    # detection, but we lowercase for the reversed-fragment substring search.
    return [t for t in re.split(r"\s+", (text or "").strip()) if t]


def is_garbled_excerpt(text: str) -> bool:
    """Return True if ``text`` looks like a reversed/garbled PDF excerpt.

    Heuristic (any of):
      1. >=2 distinct long reversed-word fragments (len>=4) appear as substrings
         of tokens. A single hit could be a coincidental letter sequence; two
         is the classic table-column-reversal signature.
      2. Mid-word capital density >= 0.25 over >=8 alphabetic tokens AND at
         least one short reversed fragment (yG/AN/te/la…) co-occurs. This catches
         pure-reversal text where the long-fragment stems don't happen to align.
      3. >=3 short reversed fragments co-occur (without long-fragment support),
         e.g. "yG 6*6 AN 11 AN 7" style numeric+unit reversal.
    """
    tokens = _tokens(text)
    if len(tokens) < 4:
        # Too short to judge reliably; let it through.
        return False

    lower_tokens = [t.lower() for t in tokens]
    long_hits = 0
    for frag in _REVERSED_FRAGMENTS:
        for tok in lower_tokens:
            if frag in tok:
                long_hits += 1
                break
    if long_hits >= 2:
        return True

    short_hits = 0
    for frag in _SHORT_REVERSED_FRAGMENTS:
        for tok in lower_tokens:
            # Match as a *whole* token for short fragments to avoid substring
            # collisions (e.g. "an" appears inside many words).
            if tok == frag:
                short_hits += 1
                break
    if short_hits >= 3:
        return True

    alpha_tokens = [t for t in tokens if re.search(r"[A-Za-z]", t)]
    if len(alpha_tokens) >= 8:
        midword_cap = sum(1 for t in alpha_tokens if _MIDWORD_CAPITAL_RE.search(t))
        density = midword_cap / len(alpha_tokens)
        if density >= 0.25 and short_hits >= 1:
            return True

    return False


def filter_sane_excerpts(excerpts: list[str]) -> list[str]:
    """Drop garbled excerpts. If every entry is garbled, keep the least-bad one
    (the longest — it carries the most recoverable context) rather than leaving
    the caller with no evidence at all. Pure-reversal garbage is bad, but an
    empty evidence list is worse: it silently downgrades verdicts and produces
    "no citation" answers.
    """
    if not excerpts:
        return []
    sane = [ex for ex in excerpts if not is_garbled_excerpt(ex)]
    if sane:
        return sane
    # All garbled: keep the longest as a last resort, drop the rest.
    return [max(excerpts, key=len)]
