"""Spatial reading order. Every input word belongs to exactly one output block."""

from __future__ import annotations

import re

PARSER_VERSION = "spatial-blocks-v3"


def word_lines(words, tolerance=3.0):
    lines = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not lines or abs(word["top"] - lines[-1][0]["top"]) > tolerance:
            lines.append([])
        lines[-1].append(word)
    return [sorted(line, key=lambda w: w["x0"]) for line in lines]


def spatial_blocks(words, width, height, gutter=None, tables=()):
    """Separate margins, tables and spanning lines before ordering column bands.

    Table boxes come from the PDF's ruled geometry. Borderless tables retain their
    text, but are not claimed to have a reconstructed cell structure.
    """
    remaining = list(words)
    barriers = []
    for box in tables:
        group = [
            w
            for w in remaining
            if box[0] <= (w["x0"] + w["x1"]) / 2 <= box[2]
            and box[1] <= (w["top"] + w["bottom"]) / 2 <= box[3]
        ]
        ids = {id(w) for w in group}
        remaining = [w for w in remaining if id(w) not in ids]
        if group:
            barriers.append((box[1], "table", group))
    body = []
    header, footer = [], []
    for w in remaining:
        if w["top"] < height * 0.05:
            header.append(w)
        elif w["bottom"] > height * 0.95:
            footer.append(w)
        else:
            body.append(w)
    columns = []
    for line in word_lines(body):
        # A line bridging the gutter (including multi-word centered headings)
        # splits the reading order into bands, rather than joining either column.
        crosses = gutter is not None and any(w["x0"] < gutter < w["x1"] for w in line)
        bridge = gutter is not None and any(
            a["x1"] <= gutter <= b["x0"] and b["x0"] - a["x1"] < 6
            for a, b in zip(line, line[1:])
        )
        if crosses or bridge:
            barriers.append((line[0]["top"], "spanning", line))
        else:
            columns.extend(line)
    groups = [("header", header)]
    for top, kind, group in sorted(barriers):
        band = [w for w in columns if w["top"] < top]
        columns = [w for w in columns if w["top"] >= top]
        groups.extend(_columns(band, gutter))
        groups.append((kind, group))
    groups.extend(_columns(columns, gutter))
    groups.append(("footer", footer))
    result = []
    cursor = 0
    for kind, group in groups:
        if not group:
            continue
        lines = word_lines(group)
        parts = []
        last_bottom = None
        for line in lines:
            if last_bottom is not None and line[0]["top"] - last_bottom > 8:
                parts.append("")
            parts.append(" ".join(str(w["text"]) for w in line))
            last_bottom = max(w["bottom"] for w in line)
        text = re.sub(r"(?<=\w)-\n(?=[a-z])", "", "\n".join(parts))
        result.append(
            {
                "kind": kind,
                "text": text,
                "start": cursor,
                "end": cursor + len(text),
                "bbox": [
                    min(w["x0"] for w in group),
                    min(w["top"] for w in group),
                    max(w["x1"] for w in group),
                    max(w["bottom"] for w in group),
                ],
                "word_count": len(group),
            }
        )
        cursor += len(text) + 2
    assert sum(b["word_count"] for b in result) == len(words)
    return result


def _columns(words, gutter):
    if gutter is None:
        return [("body", words)]
    return [
        ("left", [w for w in words if (w["x0"] + w["x1"]) / 2 < gutter]),
        ("right", [w for w in words if (w["x0"] + w["x1"]) / 2 >= gutter]),
    ]


def words_from_chars(chars, gap_ratio=0.18):
    """Recover words from geometric gaps without losing explicit spaces or lines.

    Group by baseline first; comparing the next top to the previous bottom glues
    normally spaced lines when the font box is taller than the line leading.
    """
    lines = []
    for char in sorted(chars, key=lambda c: (c.get("bottom", 0), c["x0"])):
        baseline = float(char.get("bottom", 0))
        if not lines or abs(baseline - lines[-1][0].get("bottom", 0)) > 2.0:
            lines.append([])
        lines[-1].append(char)
    result = []
    for line in lines:
        current = []

        def flush():
            if current:
                result.append(
                    {
                        "text": "".join(c["text"] for c in current),
                        "x0": min(c["x0"] for c in current),
                        "x1": max(c["x1"] for c in current),
                        "top": min(c["top"] for c in current),
                        "bottom": max(c["bottom"] for c in current),
                        "size": max(c.get("size", 10) for c in current),
                    }
                )
                current.clear()

        for char in sorted(line, key=lambda c: c["x0"]):
            if not char.get("text", "").strip():
                flush()
                continue
            if current and char["x0"] - current[-1]["x1"] > gap_ratio * min(
                12, max(char.get("size", 10), current[-1].get("size", 10), 8)
            ):
                flush()
            current.append(char)
        flush()
    return result
