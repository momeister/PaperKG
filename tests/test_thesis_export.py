"""Tests for the Tiefenanalyse → LaTeX/PDF export (``export/`` package).

Covers the Markdown→LaTeX conversion and citation mapping, the BibTeX file, the
ZIP fallback when no LaTeX engine is present, and the graceful ComfyUI skip.
"""
from __future__ import annotations

import io
import zipfile

import pytest

from export import ExportOptions, build_export
from export.latex_builder import (
    CitationIndex,
    build_bibfile,
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
