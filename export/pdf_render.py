"""Compile a LaTeX project to PDF, with a graceful ZIP fallback.

Detects a LaTeX engine on PATH (``latexmk`` preferred — it orchestrates the multiple
bibtex/pdflatex passes a bibliography needs). If none is installed or compilation
fails, callers fall back to shipping the ``.tex`` + ``.bib`` + figures as a ZIP that
can be compiled elsewhere (e.g. Overleaf).
"""
from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CompileResult:
    pdf_bytes: bytes | None
    log: str
    engine: str | None


def _miktex_bin_dirs() -> list[Path]:
    """Known MiKTeX install dirs — a fresh install isn't on PATH in already-open shells."""
    home = Path(os.path.expanduser("~"))
    return [
        home / "AppData" / "Local" / "Programs" / "MiKTeX" / "miktex" / "bin" / "x64",
        Path(r"C:\Program Files\MiKTeX\miktex\bin\x64"),
        Path(r"C:\Program Files (x86)\MiKTeX\miktex\bin\x64"),
    ]


def find_engine() -> str | None:
    """Return the preferred available LaTeX driver (PATH first, then MiKTeX dirs), or None."""
    for tool in ("latexmk", "pdflatex", "xelatex"):
        found = shutil.which(tool)
        if found:
            return found
    for directory in _miktex_bin_dirs():
        for tool in ("pdflatex", "xelatex", "latexmk"):
            candidate = directory / f"{tool}.exe"
            if candidate.exists():
                return str(candidate)
    return None


def _commands(engine: str, tex_name: str) -> list[list[str]]:
    name = Path(engine).stem.lower()
    is_miktex = "miktex" in engine.lower()
    if name == "latexmk":
        return [["latexmk", "-pdf", "-interaction=nonstopmode", "-halt-on-error", tex_name]]
    # Raw pdflatex/xelatex: two passes around bibtex so \autocite + bibliography resolve.
    # ``--enable-installer`` lets MiKTeX auto-fetch missing packages (biblatex, forest, …)
    # on the first compile without an interactive prompt.
    stem = tex_name[:-4] if tex_name.endswith(".tex") else tex_name
    base = [engine, "-interaction=nonstopmode", "-halt-on-error"]
    if is_miktex:
        base.append("--enable-installer")
    base.append(tex_name)
    engine_dir = Path(engine).parent
    bibtex = engine_dir / "bibtex.exe"
    bibtex_cmd = [str(bibtex) if bibtex.exists() else "bibtex"]
    if is_miktex and bibtex.exists():
        bibtex_cmd.append("--enable-installer")
    bibtex_cmd.append(stem)
    return [base, bibtex_cmd, base, base]


def compile_to_pdf(work_dir: Path, tex_name: str = "main.tex", timeout: float = 600.0) -> CompileResult:
    """Compile ``work_dir/tex_name`` to PDF. Never raises — failures land in ``log``."""
    engine = find_engine()
    if engine is None:
        return CompileResult(None, "No LaTeX engine (latexmk/pdflatex/xelatex) found on PATH.", None)

    log_parts: list[str] = []
    try:
        for cmd in _commands(engine, tex_name):
            proc = subprocess.run(
                cmd, cwd=str(work_dir), capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
            log_parts.append(f"$ {' '.join(cmd)}\n{proc.stdout}\n{proc.stderr}")
            # bibtex returning non-zero on warnings is fine; the LaTeX passes matter.
    except subprocess.TimeoutExpired:
        return CompileResult(None, "LaTeX compilation timed out.\n" + "\n".join(log_parts), engine)
    except Exception as exc:  # noqa: BLE001 — surface any spawn error as log text
        return CompileResult(None, f"LaTeX compilation error: {exc}\n" + "\n".join(log_parts), engine)

    pdf_path = work_dir / (tex_name[:-4] + ".pdf" if tex_name.endswith(".tex") else tex_name + ".pdf")
    if pdf_path.exists():
        return CompileResult(pdf_path.read_bytes(), "\n".join(log_parts), engine)
    return CompileResult(None, "PDF was not produced.\n" + "\n".join(log_parts), engine)
