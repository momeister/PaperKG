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
_MAX_CHART_CHAPTERS = 12
_MAX_TOP_SOURCES = 10
_TREE_LABEL_LEN = 170


def _short(text: str, limit: int = _MAX_LABEL) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def breakable_id(paper_id: str, chunk: int = 8) -> str:
    """Typeset a source id so it can wrap inside a narrow table column.

    ``semantic_scholar:16cb4a482d0e00fb63886…`` has no natural break point, so LaTeX pushed
    it past the page margin. Break opportunities are inserted after the usual separators and
    at least every ``chunk`` characters.
    """
    escaped = latex_escape(str(paper_id or ""))
    out: list[str] = []
    since_break = 0
    index = 0
    while index < len(escaped):
        if escaped[index] == "\\":  # keep an escape sequence (\_, \&, …) intact
            out.append(escaped[index:index + 2])
            index += 2
            since_break += 1
        else:
            out.append(escaped[index])
            index += 1
            since_break += 1
        last = out[-1]
        if last in (":", "/", ".", "-") or last == r"\_" or since_break >= chunk:
            out.append(r"\allowbreak{}")
            since_break = 0
    return r"\texttt{" + "".join(out) + "}"


def _forest_label(text: str, limit: int = _MAX_LABEL) -> str:
    # forest node content is delimited by [ ]; strip them, then LaTeX-escape.
    cleaned = _short(text, limit).replace("[", "(").replace("]", ")")
    return latex_escape(cleaned)


def _tree_nodes(nodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Real question nodes only — the synthesis event rides along in the same list."""
    return [
        n for n in nodes
        if str(n.get("status") or "") != "synthesis" and str(n.get("id") or "") != "synthesis"
    ]


def _children_map(nodes: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    by_parent: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for n in nodes:
        pid = n.get("parent_id")
        if pid:
            by_parent[str(pid)].append(n)
    return by_parent


def research_tree_forest(nodes: list[dict[str, Any]], max_depth: int = 1) -> str:
    """Return a landscape ``figure`` with a ``forest`` diagram of the tree, or "".

    Only the chapter level is drawn by default: with every sub-question in the picture the
    diagram grew far past the page, node labels overlapped and nothing was readable. The
    complete structure is listed by :func:`outline_latex` instead. What is drawn is put on a
    landscape page and scaled to the line width, so the figure can never overflow again.
    """
    tree_nodes = _tree_nodes(nodes)
    root = next((n for n in tree_nodes if int(n.get("depth", 0)) == 0), None)
    if not root or not any(n.get("id") for n in tree_nodes):
        return ""
    by_parent = _children_map(tree_nodes)

    def emit(node: dict[str, Any], depth: int) -> str:
        label = _forest_label(node.get("question", ""), _TREE_LABEL_LEN)
        children = by_parent.get(str(node.get("id")), []) if depth < max_depth else []
        inner = "".join(emit(c, depth + 1) for c in children)
        return f"[{{{label}}}{inner}]"

    tree = emit(root, 0)
    return "\n".join([
        r"\begin{landscape}",
        r"\begin{figure}[H]",
        r"\centering",
        r"\resizebox{\linewidth}{!}{%",
        r"\begin{forest}",
        # ``text width``/``align`` MUST travel via ``node options``: passed directly in
        # ``for tree`` forest ignores them, the label is typeset as one long line and runs
        # out of its box (that is why the diagram used to be an unreadable overlap).
        # ``align=left`` and not ``flush left``: with the array package loaded TikZ parses
        # the latter as a tabular preamble and aborts with "Illegal pream-token".
        r"for tree={draw, rounded corners, font=\footnotesize, grow=east,"
        r" edge={->, >=latex}, anchor=west,"
        r" node options={align=left, text width=5.4cm, inner sep=3pt},"
        r" l sep=18mm, s sep=5mm}",
        tree,
        r"\end{forest}%",
        r"}",
        r"\caption{Struktur der Tiefenanalyse: Forschungsfrage und untersuchte Kapitel.}",
        r"\end{figure}",
        r"\end{landscape}",
    ])


def outline_latex(nodes: list[dict[str, Any]]) -> str:
    """Full chapter → sub-question outline as a nested list (always readable)."""
    tree = _tree_nodes(nodes)
    depth1 = [n for n in tree if int(n.get("depth", 0)) == 1]
    if not depth1:
        return ""
    chapters = chapter_map(nodes)
    lines: list[str] = [
        r"\subsection*{Gliederung der Teilfragen}",
        r"\begin{enumerate}[leftmargin=*]",
    ]
    for d1 in depth1:
        chapter = str(d1.get("question") or "")
        lines.append(r"\item " + latex_escape(_short(chapter, 220)))
        subs = [
            n for n in tree
            if int(n.get("depth", 0)) >= 2 and chapters.get(str(n.get("id") or "")) == chapter
        ]
        if subs:
            lines.append(r"\begin{itemize}[leftmargin=*]")
            lines.extend(r"\item " + latex_escape(_short(str(s.get("question") or ""), 220)) for s in subs)
            lines.append(r"\end{itemize}")
    lines.append(r"\end{enumerate}")
    return "\n".join(lines)


def chapter_map(nodes: list[dict[str, Any]]) -> dict[str, str]:
    """Map every node id to the depth-1 ancestor ("chapter") it belongs to.

    The tree events streamed to the frontend (and posted back for the export) carry no
    ``chapter_question`` — that field only exists in the server-side node cache. Relying on
    it made every sub-question its own "chapter", so the per-chapter chart exploded into
    dozens of bars and the overview table always reported 0 sub-questions. The parent chain
    is authoritative and always present, so it is walked here; ``chapter_question`` is only
    a fallback for nodes whose parent is missing.
    """
    tree = _tree_nodes(nodes)
    by_id = {str(n.get("id")): n for n in tree if n.get("id")}
    chapters: dict[str, str] = {}
    for node in tree:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        current = node
        seen: set[str] = set()
        while current is not None:
            current_id = str(current.get("id") or "")
            if current_id in seen:  # defensive: never loop on a malformed tree
                current = None
                break
            seen.add(current_id)
            if int(current.get("depth", 0) or 0) == 1:
                break
            parent_id = str(current.get("parent_id") or "")
            current = by_id.get(parent_id) if parent_id else None
        if current is not None and int(current.get("depth", 0) or 0) == 1:
            chapters[node_id] = str(current.get("question") or "")
        else:
            fallback = str(node.get("chapter_question") or "")
            if fallback and int(node.get("depth", 0) or 0) > 0:
                chapters[node_id] = fallback
    return chapters


def _sources_by_chapter(nodes: list[dict[str, Any]]) -> dict[str, set[str]]:
    """Distinct source ids per chapter, accumulated over the chapter's whole subtree."""
    chapters = chapter_map(nodes)
    by_chapter: dict[str, set[str]] = defaultdict(set)
    for n in _tree_nodes(nodes):
        chapter = chapters.get(str(n.get("id") or ""), "")
        if not chapter:
            continue
        by_chapter.setdefault(chapter, set())
        for s in (n.get("answer") or {}).get("sources") or []:
            pid = str(s.get("paper_id") or "")
            if pid:
                by_chapter[chapter].add(pid)
    return by_chapter


def _top_sources(
    nodes: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> list[tuple[str, int]]:
    """(title, usage count) of the most frequently cited sources across all answers."""
    titles = {
        str(s.get("paper_id") or ""): str(s.get("title") or s.get("paper_id") or "")
        for s in sources
    }
    usage: dict[str, int] = defaultdict(int)
    for node in _tree_nodes(nodes):
        used: set[str] = set()
        for s in (node.get("answer") or {}).get("sources") or []:
            pid = str(s.get("paper_id") or "")
            if pid:
                used.add(pid)
                titles.setdefault(pid, str(s.get("title") or pid))
        for pid in used:
            usage[pid] += 1
    ranked = sorted(usage.items(), key=lambda item: (-item[1], titles.get(item[0], item[0])))
    return [(titles.get(pid, pid) or pid, count) for pid, count in ranked[:_MAX_TOP_SOURCES] if count > 1] or [
        (titles.get(pid, pid) or pid, count) for pid, count in ranked[:_MAX_TOP_SOURCES]
    ]


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
        # Kapitel-Fragen sind zu lang für eine y-Achse: auf K1…Kn kürzen und die Zuordnung
        # in die Bildunterschrift schreiben. Höhe hart begrenzt, damit das Bild auf die
        # Seite passt (früher wuchs es linear und lief über den Seitenrand hinaus).
        ranked = sorted(by_chapter.items(), key=lambda item: len(item[1]), reverse=True)[:_MAX_CHART_CHAPTERS]
        order = list(by_chapter)
        keys = [f"K{order.index(chapter) + 1}" for chapter, _ in ranked]
        counts = [len(sources) for _, sources in ranked]
        legend = "; ".join(f"{key} = {_short(chapter, 60)}" for key, (chapter, _) in zip(keys, ranked))
        try:
            fig, ax = plt.subplots(figsize=(7, min(7.0, max(2.2, 0.34 * len(keys) + 1.2))))
            bars = ax.barh(range(len(keys)), counts, color="#3b6ea5")
            ax.bar_label(bars, padding=3, fontsize=8)
            ax.set_yticks(range(len(keys)))
            ax.set_yticklabels(keys, fontsize=9)
            ax.invert_yaxis()
            ax.set_xlim(0, max(counts) * 1.15 + 0.5)
            ax.set_xlabel("Anzahl belegter Quellen")
            ax.set_title("Quellen pro Kapitel")
            fig.tight_layout()
            fig.savefig(workdir / "chart_sources.png", dpi=150)
            plt.close(fig)
            out.append((f"Anzahl der belegten Quellen je Kapitel. {legend}", "chart_sources.png"))
        except Exception:
            pass

    top_sources = _top_sources(nodes, sources)
    if top_sources:
        try:
            fig, ax = plt.subplots(figsize=(7, min(6.0, max(2.2, 0.4 * len(top_sources) + 1.2))))
            labels = [_short(title, 46) for title, _ in top_sources]
            counts = [count for _, count in top_sources]
            bars = ax.barh(range(len(labels)), counts, color="#4a7f5c")
            ax.bar_label(bars, padding=3, fontsize=8)
            ax.set_yticks(range(len(labels)))
            ax.set_yticklabels(labels, fontsize=7.5)
            ax.invert_yaxis()
            ax.set_xlim(0, max(counts) * 1.18 + 0.5)
            ax.set_xlabel("Anzahl Teilfragen, die diese Quelle belegen")
            ax.set_title("Meistgenutzte Quellen")
            fig.tight_layout()
            fig.savefig(workdir / "chart_top_sources.png", dpi=150)
            plt.close(fig)
            out.append((
                "Quellen, die in den meisten Teilfragen als Beleg herangezogen wurden.",
                "chart_top_sources.png",
            ))
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
            r"\begin{figure}[H]",
            r"\centering",
            rf"\includegraphics[width=0.85\textwidth]{{{filename}}}",
            rf"\caption{{{latex_escape(caption)}}}",
            r"\end{figure}",
        ]))
    return "\n\n".join(blocks)


def overview_table_latex(nodes: list[dict[str, Any]]) -> str:
    """Per-chapter overview: number of analysed sub-questions and distinct sources."""
    tree = _tree_nodes(nodes)
    depth1 = [n for n in tree if int(n.get("depth", 0)) == 1]
    if not depth1:
        return ""
    by_chapter = _sources_by_chapter(nodes)
    chapters = chapter_map(nodes)
    subq_count: dict[str, int] = defaultdict(int)
    for n in tree:
        if int(n.get("depth", 0)) >= 2:
            subq_count[chapters.get(str(n.get("id") or ""), "")] += 1

    rows: list[str] = []
    for index, d1 in enumerate(depth1, start=1):
        chapter = str(d1.get("question") or "")
        rows.append(
            f"K{index} & {latex_escape(_short(chapter, 150))} & "
            f"{subq_count.get(chapter, 0)} & {len(by_chapter.get(chapter, set()))} \\\\"
        )
    return "\n".join([
        r"\begingroup\small",
        r"\begin{longtable}{@{}l >{\raggedright\arraybackslash}p{0.58\textwidth} r r@{}}",
        r"\caption{Überblick der untersuchten Kapitel.}\\",
        r"\toprule",
        r"\textbf{\#} & \textbf{Kapitel} & \textbf{Teilfragen} & \textbf{Quellen} \\",
        r"\midrule",
        r"\endhead",
        *rows,
        r"\bottomrule",
        r"\end{longtable}",
        r"\endgroup",
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
        title = _short(str(s.get("title") or pid), 80)
        year = s.get("year") or ""
        rows.append(
            f"{idx} & {latex_escape(title)} & {latex_escape(str(year))} & "
            f"{breakable_id(pid)} \\\\"
        )
    return "\n".join([
        r"\begingroup\small",
        r"\begin{longtable}{@{}r >{\raggedright\arraybackslash}p{0.44\textwidth} c"
        r" >{\raggedright\arraybackslash}p{0.26\textwidth}@{}}",
        r"\caption{Übersicht der einbezogenen Quellen.}\\",
        r"\toprule",
        r"\# & \textbf{Titel} & \textbf{Jahr} & \textbf{ID} \\",
        r"\midrule",
        r"\endhead",
        *rows,
        r"\bottomrule",
        r"\end{longtable}",
        r"\endgroup",
    ])
