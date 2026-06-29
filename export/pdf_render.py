"""Compile a LaTeX project to PDF, with a graceful ZIP fallback.

Detects a LaTeX engine and runs the bibtex/pdflatex passes a numeric biblatex
bibliography needs. A *direct* engine (``pdflatex``/``xelatex``) is preferred over
``latexmk``: ``latexmk`` is a Perl script, and on a typical Windows/MiKTeX box the
backend's PATH has no Perl, so launching it fails before anything compiles. Our own
multi-pass sequence (see ``_commands``) resolves the bibliography and table of
contents without latexmk. On MiKTeX we additionally pass ``--enable-installer`` so the
first compile auto-fetches missing packages (biblatex, forest, pgfplots, fonts)
non-interactively instead of blocking on the "install package?" prompt.

If no engine is installed or compilation fails, callers fall back to shipping the
``.tex`` + ``.bib`` + figures as a ZIP that can be compiled elsewhere (e.g. Overleaf).
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
    """Return the preferred available LaTeX driver, or None.

    Order: a direct engine (``pdflatex``/``xelatex``) on PATH, then in the known MiKTeX
    dirs, then — only if a real Perl interpreter is present to run it — ``latexmk``.
    Preferring the direct engine avoids the common Windows/MiKTeX failure where
    ``latexmk`` can't find Perl and the export silently degrades to a ZIP.
    """
    for tool in ("pdflatex", "xelatex"):
        found = shutil.which(tool)
        if found:
            return found
    for directory in _miktex_bin_dirs():
        for tool in ("pdflatex", "xelatex"):
            candidate = directory / f"{tool}.exe"
            if candidate.exists():
                return str(candidate)
    # Last resort: latexmk, but only when Perl is available (e.g. a TeX Live install).
    if shutil.which("perl"):
        found = shutil.which("latexmk")
        if found:
            return found
        for directory in _miktex_bin_dirs():
            candidate = directory / "latexmk.exe"
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


def latex_error_excerpt(log: str, max_lines: int = 12) -> str:
    """Pull the most informative lines out of a LaTeX log for a short UI message.

    Prefers explicit TeX error lines (``! ...`` / ``l.<n> ...``); falls back to the
    tail of the log. Used to tell the user *why* the PDF could not be built.
    """
    errors = [ln.strip() for ln in log.splitlines() if ln.startswith("!") or ln.startswith("l.")]
    if errors:
        return "\n".join(errors[:max_lines])
    tail = [ln for ln in log.splitlines() if ln.strip()]
    return "\n".join(tail[-max_lines:])


def compile_to_pdf(work_dir: Path, tex_name: str = "main.tex", timeout: float = 600.0) -> CompileResult:
    """Compile ``work_dir/tex_name`` to PDF. Never raises — failures land in ``log``.

    Security: commands are run as argument lists (no shell) and ``-shell-escape`` is
    deliberately NOT passed, so even though the document embeds user-derived text
    (escaped via ``latex_builder.latex_escape``) it cannot execute shell commands
    through TeX ``\\write18``.
    """
    engine = find_engine()
    if engine is None:
        return CompileResult(None, "No LaTeX engine (pdflatex/xelatex/latexmk) found on PATH.", None)

    log_parts: list[str] = []
    last_tex_returncode = 0
    try:
        for cmd in _commands(engine, tex_name):
            proc = subprocess.run(
                cmd, cwd=str(work_dir), capture_output=True, text=True,
                timeout=timeout, encoding="utf-8", errors="replace",
            )
            log_parts.append(f"$ {' '.join(cmd)} (exit {proc.returncode})\n{proc.stdout}\n{proc.stderr}")
            # bibtex returning non-zero on warnings is fine; only the LaTeX passes decide
            # success, so we track the return code of the pdflatex/xelatex/latexmk runs.
            if "bibtex" not in Path(cmd[0]).stem.lower():
                last_tex_returncode = proc.returncode
    except subprocess.TimeoutExpired:
        return CompileResult(None, "LaTeX compilation timed out.\n" + "\n".join(log_parts), engine)
    except Exception as exc:  # noqa: BLE001 — surface any spawn error as log text
        return CompileResult(None, f"LaTeX compilation error: {exc}\n" + "\n".join(log_parts), engine)

    log = "\n".join(log_parts)
    pdf_path = work_dir / (tex_name[:-4] + ".pdf" if tex_name.endswith(".tex") else tex_name + ".pdf")
    # A produced PDF is the source of truth, but flag a non-zero final pass so the caller
    # can warn that the document may be incomplete.
    if pdf_path.exists() and pdf_path.stat().st_size > 0:
        if last_tex_returncode != 0:
            log = f"(LaTeX finished with exit {last_tex_returncode}; PDF was still produced)\n{log}"
        return CompileResult(pdf_path.read_bytes(), log, engine)
    return CompileResult(None, "PDF was not produced.\n" + log, engine)
