"""Generated figures and tables for the LaTeX export.

- ``research_tree_forest`` — the Tiefenanalyse tree (question → sub-questions) as a
  native TikZ/``forest`` diagram (no external tools).
- ``make_charts`` — matplotlib statistics (sources per chapter, publication years).
- ``overview_table_latex`` / ``sources_table_latex`` — auto-generated tables.

All inputs are the plain research-tree node dicts as posted by the frontend, each with
``id``/``parent_id``/``depth``/``question`` and ``answer.sources`` (paper metadata).
"""
from __future__ import annotations

from collections import defaultdict
from pathlib import Path
from typing import Any

from export.latex_builder import latex_escape

_MAX_LABEL = 90


def _short(text: str, limit: int = _MAX_LABEL) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def _forest_label(text: str) -> str:
    # forest node content is delimited by [ ]; strip them, then LaTeX-escape.
    cleaned = _short(text).replace("[", "(").replace("]", ")")
    return latex_escape(cleaned)


def _children_map(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        pid = n.get("parent_id")
        if pid:
            by_parent[str(pid)].append(n)
    return by_parent


def research_tree_forest(nodes: list[dict[str, Any]], max_depth: int = 2) -> str:
    """Return a ``figure`` with a ``forest`` diagram of the tree, or "" if unavailable."""
    root = next((n for n in nodes if int(n.get("depth", 0)) == 0), None)
    if not root or not any(n.get("id") for n in nodes):
        return ""
    by_parent = _children_map(nodes)

    def emit(node: dict[str, Any], depth: int) -> str:
        label = _forest_label(node.get("question", ""))
        children = by_parent.get(str(node.get("id")), []) if depth < max_depth else []
        inner = "".join(emit(c, depth + 1) for c in children)
        return f"[{{{label}}}{inner}]"

    tree = emit(root, 0)
    return "\n".join([
        r"\begin{figure}[h]",
        r"\centering",
        r"\begin{forest}",
        r"for tree={draw, rounded corners, font=\footnotesize, grow=east,"
        r" edge={->, >=latex}, anchor=west, align=left, text width=3.2cm,"
        r" l sep=12mm, s sep=2mm}",
        tree,
        r"\end{forest}",
        r"\caption{Struktur der Tiefenanalyse: Forschungsfrage und untersuchte Teilfragen.}",
        r"\end{figure}",
    ])


def _chapter_of(node: dict[str, Any]) -> str:
    depth = int(node.get("depth", 0))
    if depth == 0:
        return ""
    return str(node.get("chapter_question") or node.get("question") or "")


def _sources_by_chapter(nodes: list[dict[str, Any]]) -> dict[str, set[str]]:
    by_chapter: dict[str, set[str]] = defaultdict(set)
    for n in nodes:
        chapter = _chapter_of(n)
        if not chapter:
            continue
        for s in (n.get("answer") or {}).get("sources") or []:
            pid = str(s.get("paper_id") or "")
            if pid:
                by_chapter[chapter].add(pid)
    return by_chapter


def make_charts(
    nodes: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    workdir: Path,
) -> list[tuple[str, str]]:
    """Render statistic charts as PNGs in ``workdir``.

    Returns a list of ``(caption, filename)`` for the charts that had data. Failures
    (e.g. matplotlib missing) are swallowed — charts are optional.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except Exception:
        return []

    out: list[tuple[str, str]] = []

    by_chapter = _sources_by_chapter(nodes)
    if by_chapter:
        labels = [_short(c, 28) for c in by_chapter]
        counts = [len(v) for v in by_chapter.values()]
        try:
            fig, ax = plt.subplots(figsize=(7, max(2.2, 0.5 * len(labels) + 1)))
            ax.barh(range(len(labels)), counts, color="#3b6ea5")
            ax.set_yticks(range(len(labels)))
            ax.set_yticklabels(labels, fontsize=8)
            ax.invert_yaxis()
            ax.set_xlabel("Anzahl belegter Quellen")
            ax.set_title("Quellen pro Kapitel")
            fig.tight_layout()
            fig.savefig(workdir / "chart_sources.png", dpi=150)
            plt.close(fig)
            out.append(("Anzahl der belegten Quellen je Kapitel.", "chart_sources.png"))
        except Exception:
            pass

    years = [int(s["year"]) for s in sources if str(s.get("year") or "").isdigit()]
    if years:
        try:
            fig, ax = plt.subplots(figsize=(7, 3))
            lo, hi = min(years), max(years)
            bins = range(lo, hi + 2)
            ax.hist(years, bins=list(bins), color="#a5683b", edgecolor="white")
            ax.set_xlabel("Publikationsjahr")
            ax.set_ylabel("Anzahl Quellen")
            ax.set_title("Verteilung der Publikationsjahre")
            fig.tight_layout()
            fig.savefig(workdir / "chart_years.png", dpi=150)
            plt.close(fig)
            out.append(("Verteilung der Publikationsjahre der zitierten Quellen.", "chart_years.png"))
        except Exception:
            pass

    return out


def charts_latex(charts: list[tuple[str, str]]) -> str:
    """Wrap rendered chart PNGs in LaTeX ``figure`` blocks."""
    blocks: list[str] = []
    for caption, filename in charts:
        blocks.append("\n".join([
            r"\begin{figure}[h]",
            r"\centering",
            rf"\includegraphics[width=0.85\textwidth]{{{filename}}}",
            rf"\caption{{{latex_escape(caption)}}}",
            r"\end{figure}",
        ]))
    return "\n\n".join(blocks)


def overview_table_latex(nodes: list[dict[str, Any]]) -> str:
    """Per-chapter overview: number of analysed sub-questions and distinct sources."""
    depth1 = [n for n in nodes if int(n.get("depth", 0)) == 1]
    if not depth1:
        return ""
    by_chapter = _sources_by_chapter(nodes)
    subq_count: dict[str, int] = defaultdict(int)
    for n in nodes:
        if int(n.get("depth", 0)) >= 2:
            subq_count[_chapter_of(n)] += 1

    rows: list[str] = []
    for d1 in depth1:
        chapter = str(d1.get("question") or "")
        rows.append(
            f"{latex_escape(_short(chapter, 70))} & "
            f"{subq_count.get(chapter, 0)} & {len(by_chapter.get(chapter, set()))} \\\\"
        )
    return "\n".join([
        r"\begin{longtable}{p{0.62\textwidth} r r}",
        r"\caption{Überblick der untersuchten Kapitel.}\\",
        r"\toprule",
        r"\textbf{Kapitel} & \textbf{Teilfragen} & \textbf{Quellen} \\",
        r"\midrule",
        r"\endhead",
        *rows,
        r"\bottomrule",
        r"\end{longtable}",
    ])


def sources_table_latex(sources: list[dict[str, Any]]) -> str:
    """Compact numbered overview of all cited sources (title, year, id)."""
    if not sources:
        return ""
    rows: list[str] = []
    seen: set[str] = set()
    idx = 0
    for s in sources:
        pid = str(s.get("paper_id") or "")
        if not pid or pid in seen:
            continue
        seen.add(pid)
        idx += 1
        title = _short(str(s.get("title") or pid), 70)
        year = s.get("year") or ""
        rows.append(
            f"{idx} & {latex_escape(title)} & {latex_escape(str(year))} & "
            f"{latex_escape(pid)} \\\\"
        )
    return "\n".join([
        r"\begin{longtable}{r p{0.5\textwidth} c p{0.22\textwidth}}",
        r"\caption{Übersicht der einbezogenen Quellen.}\\",
        r"\toprule",
        r"\# & \textbf{Titel} & \textbf{Jahr} & \textbf{ID} \\",
        r"\midrule",
        r"\endhead",
        *rows,
        r"\bottomrule",
        r"\end{longtable}",
    ])
