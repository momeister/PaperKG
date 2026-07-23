"""Orchestrates a Tiefenanalyse → LaTeX/PDF (or .tex/.zip) export.

Takes the synthesis Markdown plus the research-tree nodes and aggregated sources,
renders the optional figures/tables/ComfyUI images, assembles a LaTeX project, and
either compiles it to PDF (MiKTeX/latexmk) or ships the sources as a ZIP fallback.
"""
from __future__ import annotations

import io
import re
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from export import comfyui_client, figures
from export.latex_builder import (
    CitationIndex,
    build_bibfile,
    build_latex_document,
    markdown_to_latex_body,
)
from export.pdf_render import compile_to_pdf, engine_is_unicode, find_engine, latex_error_excerpt


@dataclass
class ExportOptions:
    tikz_tree: bool = True
    charts: bool = True
    tables: bool = True
    comfyui_images: bool = False

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExportOptions":
        data = data or {}
        return cls(
            tikz_tree=bool(data.get("tikz_tree", True)),
            charts=bool(data.get("charts", True)),
            tables=bool(data.get("tables", True)),
            comfyui_images=bool(data.get("comfyui_images", False)),
        )


@dataclass
class ExportResult:
    content: bytes
    media_type: str
    filename: str
    warnings: list[str] = field(default_factory=list)


def _safe_stem(text: str, fallback: str = "tiefenanalyse") -> str:
    stem = re.sub(r"[^A-Za-z0-9_-]+", "_", str(text or "").strip()).strip("_").lower()
    return (stem[:60] or fallback)


def aggregate_sources(
    nodes: list[dict[str, Any]], explicit: list[dict[str, Any]] | None = None
) -> list[dict[str, Any]]:
    """Merge sources from the posted list and every node answer, de-duped by paper_id.

    Fields are filled in across occurrences so e.g. a title from ``verification`` and a
    year/doi/url from ``answer.sources`` end up on the same entry.
    """
    merged: dict[str, dict[str, Any]] = {}

    def add(src: dict[str, Any]) -> None:
        pid = str(src.get("paper_id") or "")
        if not pid:
            return
        cur = merged.setdefault(pid, {"paper_id": pid})
        for key in ("title", "year", "doi", "url"):
            value = src.get(key)
            if value and not cur.get(key):
                cur[key] = value

    for src in (explicit or []):
        add(src)
    for n in nodes:
        for src in (n.get("answer") or {}).get("sources") or []:
            add(src)
    return list(merged.values())


def _image_prompt(llm_router: Any, root_question: str, provider: str | None, model: str | None) -> str:
    base = f"clean professional scientific illustration about: {root_question}"
    if llm_router is None:
        return base
    try:
        overrides: dict[str, Any] = {"max_tokens": 120, "temperature": 0.7}
        if model:
            overrides["model"] = model
        text = llm_router.chat(
            messages=[
                {"role": "system", "content": "You write concise English text-to-image prompts."},
                {"role": "user", "content": (
                    "Write a single vivid, clean, professional cover-illustration prompt "
                    f"(max 40 words, no quotes) for a scientific report about: {root_question}"
                )},
            ],
            provider=provider,
            overrides=overrides,
        )
        return (str(text or "").strip() or base)
    except Exception:
        return base


def build_export(
    *,
    root_question: str,
    document: str,
    nodes: list[dict[str, Any]],
    sources: list[dict[str, Any]] | None = None,
    options: ExportOptions | None = None,
    export_format: str = "pdf",
    exports_dir: str | Path = "data/exports",
    llm_router: Any = None,
    provider: str | None = None,
    model: str | None = None,
) -> ExportResult:
    """Build the export artifact. ``export_format`` is ``"pdf"``, ``"zip"`` or ``"tex"``."""
    document = (document or "").strip()
    if not document:
        raise ValueError("Kein Synthese-Dokument zum Exportieren vorhanden.")

    opts = options or ExportOptions()
    nodes = nodes or []
    all_sources = aggregate_sources(nodes, sources)
    warnings: list[str] = []

    export_id = uuid.uuid4().hex[:12]
    work_dir = Path(exports_dir) / export_id
    work_dir.mkdir(parents=True, exist_ok=True)

    # --- citations + body ----------------------------------------------------
    citations = CitationIndex(all_sources)
    body_latex = markdown_to_latex_body(document, citations)
    bib_text = build_bibfile(all_sources)
    has_bib = bool(bib_text.strip())
    if has_bib:
        (work_dir / "refs.bib").write_text(bib_text, encoding="utf-8")

    # --- optional figures / tables ------------------------------------------
    appendix: list[str] = []
    image_files: list[str] = []

    if opts.tikz_tree:
        tree = figures.research_tree_forest(nodes)
        if tree:
            appendix.append(tree)
        # The diagram only shows the chapter level; the full structure follows as an
        # outline so nothing is lost to the depth limit or the scaling.
        outline = figures.outline_latex(nodes)
        if outline:
            appendix.append(outline)

    if opts.charts:
        charts = figures.make_charts(nodes, all_sources, work_dir)
        if charts:
            appendix.append(figures.charts_latex(charts))
            image_files.extend(fname for _, fname in charts)
        else:
            warnings.append("Keine Statistik-Diagramme erzeugt (keine auswertbaren Daten).")

    if opts.comfyui_images:
        if comfyui_client.is_available():
            prompt = _image_prompt(llm_router, root_question, provider, model)
            png = comfyui_client.generate_image(prompt, work_dir / "comfyui_cover.png")
            if png is not None:
                appendix.append("\n".join([
                    r"\begin{figure}[H]", r"\centering",
                    r"\includegraphics[width=0.8\textwidth]{comfyui_cover.png}",
                    r"\caption{KI-generierte Illustration (ComfyUI).}", r"\end{figure}",
                ]))
                image_files.append("comfyui_cover.png")
            else:
                warnings.append("ComfyUI-Bild konnte nicht erzeugt werden.")
        else:
            warnings.append("ComfyUI ist nicht erreichbar (Port 8188) – KI-Bilder übersprungen.")

    if opts.tables:
        overview = figures.overview_table_latex(nodes)
        if overview:
            appendix.append(overview)
        src_table = figures.sources_table_latex(all_sources)
        if src_table:
            appendix.append(src_table)

    # --- assemble .tex -------------------------------------------------------
    # Resolve the engine up front so the preamble matches how we compile below: xelatex
    # (Unicode via fontspec) is preferred and renders Greek/CJK letters that would abort a
    # pdflatex build. For tex/zip output without any engine installed, fall back to the
    # broadly-compatible pdflatex preamble (Overleaf's default compiler).
    engine = find_engine()
    tex = build_latex_document(
        title=root_question or "Tiefenanalyse",
        body_latex=body_latex,
        has_bibliography=has_bib,
        use_forest=opts.tikz_tree and any(r"\begin{forest}" in b for b in appendix),
        use_landscape=any(r"\begin{landscape}" in b for b in appendix),
        use_graphics=bool(image_files),
        appendix_blocks=appendix,
        unicode_engine=engine_is_unicode(engine),
    )
    (work_dir / "main.tex").write_text(tex, encoding="utf-8")

    stem = _safe_stem(root_question)

    if export_format == "tex":
        return ExportResult(tex.encode("utf-8"), "application/x-tex", f"{stem}.tex", warnings)

    if export_format == "pdf":
        result = compile_to_pdf(work_dir, engine=engine)
        if result.pdf_bytes is not None:
            (work_dir / "main.pdf").write_bytes(result.pdf_bytes)
            return ExportResult(result.pdf_bytes, "application/pdf", f"{stem}.pdf", warnings)
        (work_dir / "compile.log").write_text(result.log, encoding="utf-8")
        if result.engine is None:
            warnings.append(
                "Keine LaTeX-Engine gefunden – ZIP mit .tex/.bib geliefert. Installiere MiKTeX "
                "(winget install MiKTeX.MiKTeX) und starte das Backend neu."
            )
        else:
            reason = latex_error_excerpt(result.log)
            warnings.append(
                f"PDF-Kompilierung mit {Path(result.engine).name} fehlgeschlagen – ZIP mit "
                ".tex/.bib + compile.log geliefert. Beim ersten Lauf kann die MiKTeX-"
                "Paket-Nachinstallation länger dauern; ggf. erneut exportieren."
                + (f"\nGrund (Auszug):\n{reason}" if reason.strip() else "")
            )

    # zip fallback (and explicit "zip" format)
    zip_bytes = _zip_project(work_dir)
    return ExportResult(zip_bytes, "application/zip", f"{stem}.zip", warnings)


def _zip_project(work_dir: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for path in sorted(work_dir.iterdir()):
            if path.is_file():
                zf.write(path, arcname=path.name)
    return buf.getvalue()
