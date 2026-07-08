"""Markdown synthesis + sources → a LaTeX document and a BibTeX ``.bib`` file.

The Tiefenanalyse synthesis is German Markdown with inline ``[arxiv:...]`` /
``[grey::...]`` citations (see ``query/research_tree.py``). This module turns it into
a thesis-/paper-style LaTeX source with a title page, table of contents and a
numeric biblatex bibliography ("Quellenverzeichnis"), keeping the citations visible
in the text (rendered as ``[1]``) and listed in the bibliography.
"""
from __future__ import annotations

import re
import unicodedata
from typing import Any, Iterable

# LaTeX special characters that must be escaped in body text. Order is irrelevant
# because every match is mapped in a single pass (no re-processing of replacements).
_ESCAPE = {
    "\\": r"\textbackslash{}",
    "&": r"\&",
    "%": r"\%",
    "$": r"\$",
    "#": r"\#",
    "_": r"\_",
    "{": r"\{",
    "}": r"\}",
    "~": r"\textasciitilde{}",
    "^": r"\textasciicircum{}",
}

# Non-ASCII symbol/format categories pdflatex (utf8/T1) cannot typeset: emoji & misc
# symbols (So, e.g. ⏳), math symbols (Sm, e.g. ∗ U+2217, ×, −, ≤, →), modifier/currency
# symbols (Sk/Sc), format & private/unassigned (Cf/Cs/Co/Cn). ASCII is never touched
# here (its specials — # & $ _ ~ ^ % — are escaped by ``latex_escape``), so legitimate
# ``+ = < > | ~`` (also category Sm) survive.
_DROP_CATEGORIES = {"So", "Sm", "Sk", "Sc", "Cf", "Cs", "Co", "Cn"}


def _strip_unsupported(text: str) -> str:
    """Drop non-ASCII characters pdflatex (utf8/T1) has no glyph for — emoji, symbols.

    Harvested web titles like "What Studies Say ⏳" or "…Nutzung? ∗" otherwise abort
    compilation (``! LaTeX Error: Unicode character … ``). German umlauts/ß, accented
    Latin letters, dashes, curly quotes and the ellipsis are kept; only pictographic and
    math/format symbols are removed.
    """
    out: list[str] = []
    for ch in text:
        cp = ord(ch)
        if cp < 0x80:
            out.append(ch)  # ASCII is always safe (specials escaped downstream)
            continue
        # Astral-plane emoji and joiners that ride along with emoji sequences.
        if cp >= 0x1F000 or cp == 0x200D or 0xFE00 <= cp <= 0xFE0F:
            continue
        if unicodedata.category(ch) in _DROP_CATEGORIES:
            continue
        out.append(ch)
    return "".join(out)


def latex_escape(text: str) -> str:
    """Escape LaTeX-special characters in plain text (after dropping emoji/symbols)."""
    return re.sub(r"[\\&%$#_{}~^]", lambda m: _ESCAPE[m.group()], _strip_unsupported(text))


def _citekey(paper_id: str) -> str:
    """Stable BibTeX cite key for a paper id (``arxiv:2310.1`` → ``arxiv_2310_1``)."""
    key = re.sub(r"[^A-Za-z0-9]+", "_", str(paper_id)).strip("_")
    return key or "src"


def _norm_id(value: str) -> str:
    """Normalize an id/citation token for tolerant matching (lowercase, no spaces,
    drop a trailing arXiv version suffix) — mirrors ``_strip_unknown_citations``."""
    return re.sub(r"v\d+$", "", str(value).lower().replace(" ", ""))


class CitationIndex:
    """Maps citation tokens found in the text to BibTeX cite keys."""

    def __init__(self, sources: list[dict[str, Any]]) -> None:
        self.sources = sources
        self.id_to_key: dict[str, str] = {}
        self._norm_to_key: dict[str, str] = {}
        for src in sources:
            pid = str(src.get("paper_id") or "")
            if not pid:
                continue
            key = _citekey(pid)
            self.id_to_key[pid] = key
            self._norm_to_key[_norm_id(pid)] = key

    def lookup(self, token: str) -> str | None:
        """Resolve a single citation token to a cite key (tolerant), or None."""
        norm = _norm_id(token)
        if not norm:
            return None
        if norm in self._norm_to_key:
            return self._norm_to_key[norm]
        for known, key in self._norm_to_key.items():
            if known.endswith(norm) or norm.endswith(known):
                return key
        return None


def _inline(text: str, citations: CitationIndex) -> str:
    """Convert one run of inline Markdown to LaTeX.

    Citations are extracted to placeholders *before* escaping so their cite keys
    (which contain ``_``) are not mangled; ``**bold**``/``*italic*`` survive escaping
    because ``*`` is not a LaTeX special char and are converted afterwards.
    """
    placeholders: list[str] = []

    def _stash(replacement: str) -> str:
        placeholders.append(replacement)
        return f"\x00{len(placeholders) - 1}\x00"

    def _cite(m: re.Match) -> str:
        parts = [p.strip() for p in re.split(r"[,;]\s*", m.group(1)) if p.strip()]
        keys: list[str] = []
        for part in parts:
            key = citations.lookup(part)
            if key and key not in keys:
                keys.append(key)
        if keys and len(keys) == len(parts):
            return _stash(r"\autocite{" + ",".join(keys) + "}")
        return m.group(0)  # not a (fully) resolvable citation → keep as literal text

    text = re.sub(r"\[([^\]]+)\]", _cite, text)
    text = latex_escape(text)
    text = re.sub(r"\*\*(.+?)\*\*", lambda m: r"\textbf{" + m.group(1) + "}", text)
    text = re.sub(r"\*(.+?)\*", lambda m: r"\textit{" + m.group(1) + "}", text)
    text = re.sub(r"\x00(\d+)\x00", lambda m: placeholders[int(m.group(1))], text)
    return text


def markdown_to_latex_body(doc: str, citations: CitationIndex) -> str:
    """Convert the synthesis Markdown (## / ### headings, paragraphs, lists) to LaTeX."""
    out: list[str] = []
    para: list[str] = []
    list_items: list[str] = []
    list_kind: str | None = None

    def flush_para() -> None:
        if para:
            out.append(_inline(" ".join(para).strip(), citations))
            out.append("")
            para.clear()

    def flush_list() -> None:
        nonlocal list_kind
        if list_items:
            env = "enumerate" if list_kind == "ol" else "itemize"
            out.append(f"\\begin{{{env}}}")
            out.extend(f"  \\item {it}" for it in list_items)
            out.append(f"\\end{{{env}}}")
            out.append("")
            list_items.clear()
        list_kind = None

    for raw in doc.split("\n"):
        line = raw.rstrip()
        h2 = re.match(r"^##\s+(.+)", line)
        h3 = re.match(r"^###\s+(.+)", line)
        h4 = re.match(r"^#{4,6}\s+(.+)", line)
        bullet = re.match(r"^\s*(?:[-*•])\s+(.+)", line)
        numbered = re.match(r"^\s*\d+\.\s+(.+)", line)
        heading = h2 or h3 or h4
        item = bullet or numbered
        if heading is not None:
            flush_para()
            flush_list()
            title = _inline(heading.group(1).strip(), citations)
            # h4+ → unnumbered \subsubsection* so deeper headings render as real
            # subheadings instead of leaking literal "####" into the body, without
            # cluttering the numbered ToC.
            cmd = "section" if h2 else "subsection" if h3 else "subsubsection*"
            out.append(f"\\{cmd}{{{title}}}")
            out.append("")
        elif item is not None:
            flush_para()
            kind = "ol" if numbered else "ul"
            if list_kind and list_kind != kind:
                flush_list()
            list_kind = kind
            list_items.append(_inline(item.group(1).strip(), citations))
        elif not line.strip():
            flush_para()
            flush_list()
        else:
            flush_list()
            para.append(line.strip())

    flush_para()
    flush_list()
    return "\n".join(out).strip()


def build_bibfile(sources: list[dict[str, Any]]) -> str:
    """Render a BibTeX ``.bib`` from the aggregated sources.

    arXiv papers get an ``eprint`` field; everything else becomes an ``@online``
    entry with whatever metadata (title/year/doi/url) is available.
    """
    entries: list[str] = []
    seen: set[str] = set()
    for src in sources:
        pid = str(src.get("paper_id") or "")
        if not pid:
            continue
        key = _citekey(pid)
        if key in seen:
            continue
        seen.add(key)
        title = str(src.get("title") or pid).strip()
        fields: list[str] = [f"  title = {{{_bib_value(title)}}}"]
        year = src.get("year")
        if year:
            fields.append(f"  year = {{{_bib_value(str(year))}}}")
        doi = src.get("doi")
        if doi:
            fields.append(f"  doi = {{{_bib_value(str(doi))}}}")
        url = src.get("url")
        if url:
            fields.append(f"  url = {{{_bib_value(str(url))}}}")
        arxiv = _arxiv_id(pid)
        if arxiv:
            fields.append("  eprinttype = {arxiv}")
            fields.append(f"  eprint = {{{arxiv}}}")
        fields.append(f"  note = {{{_bib_value(pid)}}}")
        entries.append("@online{" + key + ",\n" + ",\n".join(fields) + "\n}")
    return "\n\n".join(entries) + ("\n" if entries else "")


def _arxiv_id(paper_id: str) -> str | None:
    m = re.match(r"^arxiv:(.+)$", str(paper_id), re.IGNORECASE)
    return m.group(1) if m else None


def _bib_value(value: str) -> str:
    """Make a string safe inside a brace-delimited BibTeX field.

    Braces are structural in BibTeX, so they are neutralized to parentheses; the
    remaining LaTeX specials are escaped exactly as in body text. A raw ``#`` in a
    title (e.g. "C# …") otherwise reaches the engine via the bibliography and aborts
    with ``! Illegal parameter number in definition of \\NewValue``.
    """
    value = _strip_unsupported(value).replace("{", "(").replace("}", ")")
    return re.sub(r"[\\&%$#_~^]", lambda m: _ESCAPE[m.group()], value)


def build_latex_document(
    *,
    title: str,
    body_latex: str,
    has_bibliography: bool,
    use_forest: bool,
    use_graphics: bool,
    appendix_blocks: Iterable[str] = (),
    unicode_engine: bool = False,
) -> str:
    """Assemble the full ``.tex`` source (preamble, title page, ToC, body, appendix).

    ``unicode_engine`` selects the font stack: when the document will be compiled with
    xelatex/lualatex it uses ``fontspec`` (Latin Modern), which renders arbitrary Unicode
    letters such as Greek ``α``/``β`` found in harvested paper text. Otherwise it keeps the
    classic pdflatex ``inputenc``/``fontenc``/``lmodern`` stack (no glyph for non-Latin
    letters — those are dropped upstream by ``_strip_unsupported``).
    """
    if unicode_engine:
        # fontspec must load before babel; Latin Modern covers Latin + Greek glyphs.
        font_packages = [
            r"\usepackage{fontspec}",
            r"\setmainfont{Latin Modern Roman}",
            r"\usepackage[ngerman]{babel}",
        ]
    else:
        font_packages = [
            r"\usepackage[utf8]{inputenc}",
            r"\usepackage[T1]{fontenc}",
            r"\usepackage[ngerman]{babel}",
            r"\usepackage{lmodern}",
        ]
    packages = [
        *font_packages,
        r"\usepackage[a4paper,margin=2.5cm]{geometry}",
        r"\usepackage{microtype}",
        r"\usepackage{booktabs}",
        r"\usepackage{longtable}",
        r"\usepackage{float}",
        r"\usepackage[hidelinks]{hyperref}",
    ]
    if use_graphics:
        packages.append(r"\usepackage{graphicx}")
    if use_forest:
        packages.append(r"\usepackage{forest}")
    if has_bibliography:
        packages.append(r"\usepackage[backend=bibtex,style=numeric,sorting=none]{biblatex}")
        packages.append(r"\addbibresource{refs.bib}")

    safe_title = _inline(title, CitationIndex([]))
    appendix = [b for b in appendix_blocks if b and b.strip()]

    parts: list[str] = [
        r"\documentclass[11pt,a4paper]{article}",
        *packages,
        f"\\title{{{safe_title}}}",
        r"\author{ScienceKG -- Automatisch generierte Tiefenanalyse}",
        r"\date{\today}",
        r"\begin{document}",
        r"\begin{titlepage}",
        r"\centering",
        r"{\large ScienceKG \textendash{} Tiefenanalyse\par}",
        r"\vspace{2.5cm}",
        f"{{\\huge\\bfseries {safe_title}\\par}}",
        r"\vspace{2cm}",
        r"{\large Automatisch generierte wissenschaftliche Ausarbeitung\par}",
        r"\vfill",
        r"{\large \today\par}",
        r"\end{titlepage}",
        # Ragged bottom stops LaTeX from vertically stretching short pages into large gaps.
        r"\raggedbottom",
        r"\tableofcontents",
        r"\newpage",
        body_latex,
    ]
    if appendix:
        parts.append(r"\clearpage")
        parts.append(r"\appendix")
        parts.append(r"\section{Anhang: Forschungsstruktur, Diagramme und Tabellen}")
        parts.extend(appendix)
    if has_bibliography:
        parts.append(r"\clearpage")
        parts.append(r"\printbibliography[title={Quellenverzeichnis}]")
    parts.append(r"\end{document}")
    return "\n".join(parts) + "\n"
