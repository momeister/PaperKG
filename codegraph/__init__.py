"""Code-Graph: Anbindung des einvendorten CodeSearch-Kerns (`codesearch/`).

Die Analyse selbst ist Rust (tree-sitter, dreistufige Auflösung, PageRank) und
liegt im Cargo-Workspace unter ``codesearch/``. Dieses Paket ist alles, was
PaperKG davon sieht:

* :mod:`codegraph.binary`    — wo liegt das ``cs``-Binary?
* :mod:`codegraph.rpc`       — ein Kindprozess, NDJSON über stdin/stdout
* :mod:`codegraph.pool`      — ein lebendes Kind je Code-Projekt
* :mod:`codegraph.service`   — Indizieren, Abfragen, Buchführung in DuckDB
* :mod:`codegraph.positions` — ``datei:zeile`` in Terminal-Ausgabe finden

Die tragende Regel des Werkzeugs bleibt dabei über die Prozessgrenze hinweg
erhalten: **keine Beziehung ohne Beleg und Sicherheitsstufe**, und zitiert werden
darf nur, was in derselben Sitzung tatsächlich nachgeschlagen wurde. Beides
entscheidet die Rust-Seite (``cs_llm::citation``), nicht diese.
"""
from __future__ import annotations

from codegraph.binary import CodeSearchMissingError, find_binary
from codegraph.rpc import CodeGraphClient, CodeGraphError

__all__ = [
    "CodeGraphClient",
    "CodeGraphError",
    "CodeSearchMissingError",
    "find_binary",
]
