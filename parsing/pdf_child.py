"""Kindprozess-Einstieg fürs geschützte PDF-Parsen (siehe parsing/pdf_guard.py).

Bewusst ein eigenes Modul und *kein* multiprocessing-Target: `spawn` importiert im
Kind das `__main__` des Elternprozesses neu — unter uvicorn ist das dessen CLI-Modul,
und das erneut auszuführen ist nichts, was man im Backend haben will. Als `python -m
parsing.pdf_child` gibt es dieses Problem nicht.

Aufruf: python -m parsing.pdf_child <pdf> <paper_id> <progress.jsonl> <result.json> <ram_limit_bytes>
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    if len(argv) != 5:
        print("usage: python -m parsing.pdf_child <pdf> <paper_id> <progress> <result> <mem_bytes>", file=sys.stderr)
        return 2
    file_path, paper_id, progress_path, result_path, memory_cap_raw = argv
    memory_cap = int(memory_cap_raw)

    os.environ["SCIENCEKG_PDF_CHILD"] = "1"
    try:  # POSIX: harte Adressraum-Grenze → MemoryError statt OOM-Kill der Maschine
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (memory_cap, memory_cap))
    except Exception:
        pass  # Windows/unsupported: der Eltern-Watchdog übernimmt

    from parsing.marker_parser import MarkerParser

    document = MarkerParser().parse_direct(file_path, paper_id, progress_path=progress_path)
    Path(result_path).write_text(
        json.dumps(
            {
                "paper_id": document.paper_id,
                "parser": document.parser,
                "text": document.text,
                "page_count": document.page_count,
                "meta": document.meta,
            }
        ),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
