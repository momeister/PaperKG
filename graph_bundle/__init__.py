"""Projekt-Bundles: ein Projekt samt Graph-Inhalt exportieren und importieren.

Ein Bundle ist ein ZIP mit einer ``manifest.json`` und je einer JSONL-Datei pro
Tabelle (Paper, Extraktionsergebnisse, Grauquellen, Embeddings), optional plus den
PDFs. Es dient dem Umzug auf einen anderen Rechner und als Sicherung — die
Sidecar-Dateien in ``data/`` sind gitignored und haben sonst keinerlei Historie.

Warum kein Kuzu-Dump: der in der UI sichtbare Graph (``/graph/explorer``) wird bei
jedem Aufruf aus DuckDB berechnet, und die Kuzu-Datenbank ist ein reiner Cache,
der sich mit ``POST /jobs/graph-rebuild`` jederzeit neu erzeugen laesst. Damit
reichen die DuckDB-Zeilen plus die Projektzuordnung vollstaendig aus.
"""
from graph_bundle.exporter import export_project
from graph_bundle.importer import ImportMode, import_bundle, preview_bundle
from graph_bundle.schema import BUNDLE_VERSION, BundleError

__all__ = [
    "BUNDLE_VERSION",
    "BundleError",
    "ImportMode",
    "export_project",
    "import_bundle",
    "preview_bundle",
]
