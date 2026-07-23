"""Tests for the Tiefenanalyse → LaTeX/PDF export (``export/`` package).

Covers the Markdown→LaTeX conversion and citation mapping, the BibTeX file, the
ZIP fallback when no LaTeX engine is present, and the graceful ComfyUI skip.
"""
from __future__ import annotations

import io
import unicodedata
import zipfile

import pytest

from export import ExportOptions, build_export, figures
from export.latex_builder import (
    CitationIndex,
    build_bibfile,
    build_latex_document,
    latex_escape,
    markdown_to_latex_body,
)
from export import pdf_render
from export.pdf_render import compile_to_pdf


SOURCES = [
    {"paper_id": "arxiv:2310.12345", "title": "Attention Paper", "year": 2023, "url": "http://x", "doi": "10.1/x"},
    {"paper_id": "grey::abc-1", "title": "A Web Source"},
]

DOC = """## Einleitung

Einleitung mit Beleg [arxiv:2310.12345] und **fett** und *kursiv*.

## Kapitel Eins

### Unterfrage 1a

Befund [arxiv:2310.12345]. Unbekannt [arxiv:9999.0000] entfaellt. Zeichen & 100% _x_.

- erstens
- zweitens

## Fazit

Schluss [grey::abc-1].
"""

NODES = [
    {"id": "r", "parent_id": None, "question": "Hauptfrage", "depth": 0, "chapter_question": None,
     "answer": {"answer": "x", "sources": [SOURCES[0]]}},
    {"id": "c1", "parent_id": "r", "question": "Kapitel Eins", "depth": 1, "chapter_question": "Kapitel Eins",
     "answer": {"answer": "y", "sources": [SOURCES[0]]}},
    {"id": "c1a", "parent_id": "c1", "question": "Unterfrage 1a", "depth": 2, "chapter_question": "Kapitel Eins",
     "answer": {"answer": "z", "sources": [SOURCES[1]]}},
]


# A tree exactly as the frontend posts it: the SSE events carry no ``chapter_question``
# (that field only exists server-side), and the synthesis event rides along in the list.
POSTED_NODES = [
    {"id": "r", "parent_id": None, "question": "Hauptfrage", "depth": 0, "status": "done",
     "answer": {"answer": "x", "sources": [SOURCES[0]]}},
    {"id": "c1", "parent_id": "r", "question": "Kapitel Eins", "depth": 1, "status": "done",
     "answer": {"answer": "y", "sources": [SOURCES[0]]}},
    {"id": "c1a", "parent_id": "c1", "question": "Unterfrage 1a", "depth": 2, "status": "done",
     "answer": {"answer": "z", "sources": [SOURCES[1]]}},
    {"id": "c1b", "parent_id": "c1a", "question": "Unterfrage 1b", "depth": 3, "status": "done",
     "answer": {"answer": "z", "sources": [SOURCES[0]]}},
    {"id": "synthesis", "parent_id": None, "question": "Hauptfrage", "depth": 0,
     "status": "synthesis", "answer": None, "document": "## Kapitel Eins\n\nText."},
]


def test_chapter_map_uses_parent_chain_not_chapter_question() -> None:
    # Without chapter_question every sub-question used to become its own "chapter".
    chapters = figures.chapter_map(POSTED_NODES)
    assert chapters["c1"] == "Kapitel Eins"
    assert chapters["c1a"] == "Kapitel Eins"
    assert chapters["c1b"] == "Kapitel Eins"  # depth 3 still belongs to the depth-1 chapter
    assert "r" not in chapters  # the root question is no chapter
    assert "synthesis" not in chapters  # the synthesis event is not a tree node


def test_overview_table_counts_subquestions_and_accumulated_sources() -> None:
    table = figures.overview_table_latex(POSTED_NODES)
    row = next(line for line in table.splitlines() if "Kapitel Eins" in line and "&" in line)
    cells = [cell.strip() for cell in row.rstrip("\\").split("&")]
    assert cells[2] == "2"  # two sub-questions (depth >= 2) below the chapter
    assert cells[3] == "2"  # distinct sources accumulated over the whole subtree
    # p-columns wrap instead of running past the page margin.
    assert r"\raggedright\arraybackslash" in table


def test_sources_table_breaks_long_ids() -> None:
    long_id = "semantic_scholar:16cb4a482d0e00fb63886abcdef0123456789"
    table = figures.sources_table_latex([{"paper_id": long_id, "title": "T", "year": 2024}])
    assert r"\allowbreak{}" in table
    assert r"\texttt{" in table
    assert r"\raggedright\arraybackslash" in table


def test_breakable_id_keeps_escapes_intact() -> None:
    out = figures.breakable_id("grey::grey_5dda6fe4")
    assert r"\_" in out  # underscore stays escaped, never split into a lone backslash
    assert "\\allow" in out
    assert "grey" in out


def test_research_tree_forest_is_landscape_and_scaled() -> None:
    tree = figures.research_tree_forest(POSTED_NODES)
    assert r"\begin{landscape}" in tree
    assert r"\resizebox{\linewidth}{!}" in tree
    # text width/align only wrap when passed through node options — set directly in
    # "for tree" forest ignores them and the labels run out of their boxes.
    assert "node options={align=left, text width=" in tree
    assert "align=flush left" not in tree  # aborts compilation together with the array pkg
    # Only the chapter level is drawn; deeper questions go into the outline instead.
    assert "Kapitel Eins" in tree and "Unterfrage 1a" not in tree


def test_outline_lists_chapters_with_subquestions() -> None:
    outline = figures.outline_latex(POSTED_NODES)
    assert "Kapitel Eins" in outline
    assert "Unterfrage 1a" in outline and "Unterfrage 1b" in outline


def test_build_latex_document_loads_landscape_packages() -> None:
    tex = build_latex_document(
        title="T", body_latex="Body", has_bibliography=False, use_forest=True,
        use_graphics=False, use_landscape=True, appendix_blocks=[r"\begin{landscape}\end{landscape}"],
    )
    assert r"\usepackage{pdflscape}" in tex
    assert r"\usepackage{graphicx}" in tex  # \resizebox needs it even without images
    assert r"\usepackage{array}" in tex and r"\usepackage{enumitem}" in tex


def test_markdown_to_latex_body_sections_and_citations() -> None:
    body = markdown_to_latex_body(DOC, CitationIndex(SOURCES))
    assert r"\section{Einleitung}" in body
    assert r"\subsection{Unterfrage 1a}" in body
    assert r"\autocite{arxiv_2310_12345}" in body
    assert r"\autocite{grey_abc_1}" in body
    # Unknown citation is left as escaped literal, never mapped to a fake key.
    assert "arxiv_9999_0000" not in body
    assert r"\textbf{fett}" in body and r"\textit{kursiv}" in body
    # LaTeX special chars escaped.
    assert r"100\%" in body and r"\_x\_" in body and r"\&" in body
    # Markdown bullets become an itemize environment.
    assert r"\begin{itemize}" in body


def test_markdown_to_latex_body_h4_becomes_subsubsection() -> None:
    # A `####` heading must render as a real (unnumbered) subsubsection, never leak as the
    # literal "####" into the body text.
    body = markdown_to_latex_body("#### Die Rolle der Verstärkung\n\nText.", CitationIndex([]))
    assert r"\subsubsection*{Die Rolle der Verstärkung}" in body
    assert "####" not in body


def test_build_latex_document_paginates_appendix_and_bibliography() -> None:
    tex = build_latex_document(
        title="T",
        body_latex="Body",
        has_bibliography=True,
        use_forest=False,
        use_graphics=True,
        appendix_blocks=[r"\begin{figure}[H]\end{figure}"],
    )
    # Geometry/float/raggedbottom keep figures placed and pages from stretching into gaps.
    assert r"\usepackage[a4paper,margin=2.5cm]{geometry}" in tex
    assert r"\usepackage{float}" in tex
    assert r"\raggedbottom" in tex
    # Appendix and bibliography each start on a fresh page (no float-starvation gaps).
    appendix_at = tex.index(r"\appendix")
    bib_at = tex.index(r"\printbibliography")
    assert tex.rindex(r"\clearpage", 0, appendix_at) >= 0
    assert tex.rindex(r"\clearpage", 0, bib_at) < bib_at
    assert tex.count(r"\clearpage") >= 2


def test_latex_escape_strips_emoji_keeps_german() -> None:
    # An emoji-bearing harvested title used to abort pdflatex
    # (``! LaTeX Error: Unicode character ⏳ (U+23F3)``). Emoji/symbols must be
    # dropped while German umlauts/ß, accented Latin and dashes survive.
    title = "Impact Of Social Media: What Studies Say ⏳ \U0001f600 — Über Größe"
    escaped = latex_escape(title)
    assert "⏳" not in escaped and "\U0001f600" not in escaped
    assert all(unicodedata.category(c) != "So" for c in escaped)
    assert "Über Größe" in escaped and "—" in escaped


def test_latex_escape_strips_math_symbols_keeps_ascii_operators() -> None:
    # ∗ (U+2217, category Sm) is a footnote-marker artifact that aborts pdflatex
    # (``! LaTeX Error: Unicode character ∗``). Non-ASCII Sm symbols are dropped,
    # but ASCII operators (also category Sm) such as + = < > must be preserved.
    escaped = latex_escape("…Nutzung? ∗ × − ≤ → und a + b = c < d > e")
    assert "∗" not in escaped and "×" not in escaped and "≤" not in escaped and "→" not in escaped
    assert "a + b = c < d > e" in escaped


def test_build_latex_document_title_has_no_emoji() -> None:
    tex = build_latex_document(
        title="Studie ⏳ zur Aufmerksamkeit",
        body_latex="x",
        has_bibliography=False,
        use_forest=False,
        use_graphics=False,
    )
    assert "⏳" not in tex
    assert all(unicodedata.category(c) != "So" for c in tex)


def test_build_bibfile_strips_emoji_from_title() -> None:
    bib = build_bibfile([{"paper_id": "grey::x", "title": "What Studies Say ⏳"}])
    assert "⏳" not in bib
    assert all(unicodedata.category(c) != "So" for c in bib)


def test_build_bibfile_escapes_hash_and_underscore() -> None:
    # A raw '#' in a title reached the engine via the bibliography and aborted with
    # "Illegal parameter number in definition of \\NewValue"; '_' in a grey:: note
    # field would trigger a math-mode error. Both must be escaped.
    bib = build_bibfile([{"paper_id": "grey::grey_5d5c2", "title": "C# and A∗ Formative Analysis"}])
    assert "∗" not in bib
    assert "C\\#" in bib  # hash escaped in the title
    assert "grey::grey\\_5d5c2" in bib  # underscore escaped in the note field
    # No unescaped '#' or bare '_' survive anywhere in the .bib.
    assert "C#" not in bib


def test_build_bibfile_entries() -> None:
    bib = build_bibfile(SOURCES)
    assert "@online{arxiv_2310_12345," in bib
    assert "eprint = {2310.12345}" in bib
    assert "@online{grey_abc_1," in bib
    assert "title = {A Web Source}" in bib


def test_build_export_zip_contains_sources(tmp_path) -> None:
    result = build_export(
        root_question="Hauptfrage", document=DOC, nodes=NODES,
        export_format="zip", exports_dir=tmp_path,
        options=ExportOptions(charts=False, comfyui_images=False),
    )
    assert result.media_type == "application/zip"
    assert result.filename.endswith(".zip")
    names = zipfile.ZipFile(io.BytesIO(result.content)).namelist()
    assert "main.tex" in names
    assert "refs.bib" in names


def test_build_export_empty_document_raises(tmp_path) -> None:
    with pytest.raises(ValueError):
        build_export(root_question="X", document="   ", nodes=NODES, exports_dir=tmp_path)


def test_compile_to_pdf_without_engine_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(pdf_render, "find_engine", lambda: None)
    (tmp_path / "main.tex").write_text(r"\documentclass{article}\begin{document}x\end{document}", encoding="utf-8")
    result = compile_to_pdf(tmp_path)
    assert result.pdf_bytes is None
    assert result.engine is None


def test_find_engine_prefers_pdflatex_over_latexmk_without_perl(monkeypatch) -> None:
    # On a MiKTeX box the backend's PATH usually has no Perl, so latexmk (a Perl script)
    # would fail. find_engine must therefore pick the direct pdflatex engine.
    def which(tool):
        return {"pdflatex": r"C:\miktex\pdflatex.exe", "latexmk": r"C:\miktex\latexmk.exe"}.get(tool)

    monkeypatch.setattr(pdf_render.shutil, "which", which)
    monkeypatch.setattr(pdf_render, "_miktex_bin_dirs", lambda: [])
    assert pdf_render.find_engine() == r"C:\miktex\pdflatex.exe"


def test_find_engine_uses_latexmk_only_when_perl_present(monkeypatch) -> None:
    def which(tool):
        return {"latexmk": r"C:\tl\latexmk.exe", "perl": r"C:\tl\perl.exe"}.get(tool)

    monkeypatch.setattr(pdf_render.shutil, "which", which)
    monkeypatch.setattr(pdf_render, "_miktex_bin_dirs", lambda: [])
    assert pdf_render.find_engine() == r"C:\tl\latexmk.exe"


def test_latex_error_excerpt_pulls_error_lines() -> None:
    log = "blah\n! LaTeX Error: File `forest.sty' not found.\nl.12 \\usepackage{forest}\nmore noise"
    excerpt = pdf_render.latex_error_excerpt(log)
    assert "! LaTeX Error" in excerpt
    assert "forest.sty" in excerpt


def test_build_export_pdf_failure_surfaces_log_excerpt(tmp_path, monkeypatch) -> None:
    # An engine *is* present but compilation fails: the warning must include the reason.
    monkeypatch.setattr(
        "export.builder.compile_to_pdf",
        lambda *a, **k: pdf_render.CompileResult(None, "! LaTeX Error: something broke", "pdflatex"),
    )
    result = build_export(
        root_question="Hauptfrage", document=DOC, nodes=NODES,
        export_format="pdf", exports_dir=tmp_path,
        options=ExportOptions(charts=False, comfyui_images=False),
    )
    assert result.media_type == "application/zip"
    assert any("something broke" in w for w in result.warnings)


def test_build_export_pdf_falls_back_to_zip_without_engine(tmp_path, monkeypatch) -> None:
    # Force "no LaTeX engine" so PDF requests degrade to a ZIP with a clear warning.
    monkeypatch.setattr("export.builder.compile_to_pdf",
                        lambda *a, **k: pdf_render.CompileResult(None, "no engine", None))
    result = build_export(
        root_question="Hauptfrage", document=DOC, nodes=NODES,
        export_format="pdf", exports_dir=tmp_path,
        options=ExportOptions(charts=False, comfyui_images=False),
    )
    assert result.media_type == "application/zip"
    assert any("MiKTeX" in w for w in result.warnings)


def test_build_export_comfyui_unreachable_is_graceful(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("export.comfyui_client.is_available", lambda *a, **k: False)
    result = build_export(
        root_question="Hauptfrage", document=DOC, nodes=NODES,
        export_format="tex", exports_dir=tmp_path,
        options=ExportOptions(charts=False, comfyui_images=True),
    )
    # No exception, .tex still produced, and a warning explains the skip.
    assert result.media_type == "application/x-tex"
    assert any("ComfyUI" in w for w in result.warnings)
