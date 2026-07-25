"""Format, Namen und Sicherheitsregeln fuer Projekt-Bundles."""
from __future__ import annotations

import hashlib
import json
import re
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Iterator

__all__ = [
    "BUNDLE_VERSION",
    "BundleError",
    "MANIFEST_NAME",
    "PROJECT_NAME",
    "PDF_PREFIX",
    "TABLE_FILES",
    "BundleManifest",
    "safe_member_path",
    "read_jsonl",
    "write_jsonl",
    "sha256_bytes",
    "slugify",
]

#: Erhoeht sich, wenn ein Import aelterer Bundles nicht mehr funktionieren wuerde.
#: Zusaetzliche Felder allein rechtfertigen das nicht — Leser ignorieren Unbekanntes.
BUNDLE_VERSION = 1

MANIFEST_NAME = "manifest.json"
PROJECT_NAME = "project.json"
PDF_PREFIX = "pdfs/"

#: Tabelle -> Datei im ZIP. Reihenfolge = Importreihenfolge (Paper zuerst, weil
#: Extraktionen und Embeddings sich auf sie beziehen).
TABLE_FILES: dict[str, str] = {
    "papers": "papers.jsonl",
    "paper_sources": "paper_sources.jsonl",
    "extraction_results": "extraction_results.jsonl",
    "grey_sources": "grey_sources.jsonl",
    "entity_embeddings": "entity_embeddings.jsonl",
}


class BundleError(RuntimeError):
    """Das Bundle ist unbrauchbar (kaputt, zu neu oder nicht vertrauenswuerdig)."""


@dataclass
class BundleManifest:
    """Kopfdaten eines Bundles — die einzige Datei, die vor allem anderen gelesen wird."""

    bundle_version: int
    project: str
    exported_at: str
    app_version: str
    counts: dict[str, int] = field(default_factory=dict)
    files: dict[str, str] = field(default_factory=dict)  # Dateiname -> sha256
    includes_pdfs: bool = False

    def to_json(self) -> str:
        return json.dumps(
            {
                "bundle_version": self.bundle_version,
                "project": self.project,
                "exported_at": self.exported_at,
                "app_version": self.app_version,
                "counts": self.counts,
                "files": self.files,
                "includes_pdfs": self.includes_pdfs,
            },
            indent=2,
            ensure_ascii=False,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BundleManifest":
        try:
            version = int(data["bundle_version"])
        except (KeyError, TypeError, ValueError) as error:
            raise BundleError("manifest.json fehlt oder hat keine bundle_version.") from error
        if version > BUNDLE_VERSION:
            raise BundleError(
                f"Das Bundle wurde mit einer neueren Version erstellt (Format {version}, "
                f"unterstuetzt wird bis {BUNDLE_VERSION}). Aktualisiere ScienceKG."
            )
        return cls(
            bundle_version=version,
            project=str(data.get("project") or "Importiertes Projekt"),
            exported_at=str(data.get("exported_at") or ""),
            app_version=str(data.get("app_version") or ""),
            counts={str(k): int(v) for k, v in (data.get("counts") or {}).items()},
            files={str(k): str(v) for k, v in (data.get("files") or {}).items()},
            includes_pdfs=bool(data.get("includes_pdfs")),
        )


def safe_member_path(name: str) -> str:
    """Pruefe einen ZIP-Eintrag gegen Path-Traversal (``../``, absolute Pfade).

    ``zipfile.extractall`` auf rohe Namen loszulassen erlaubt einem praeparierten
    Bundle, ausserhalb des Zielordners zu schreiben. Bundles kommen von aussen —
    genau wie das Vorbild dieser Pruefung in ``storage/path_safety.py``.
    """
    cleaned = name.replace("\\", "/")
    if cleaned.startswith("/") or re.match(r"^[a-zA-Z]:/", cleaned):
        raise BundleError(f"Absoluter Pfad im Bundle abgelehnt: {name}")
    parts = [part for part in cleaned.split("/") if part not in ("", ".")]
    if any(part == ".." for part in parts):
        raise BundleError(f"Pfad-Traversal im Bundle abgelehnt: {name}")
    if not parts:
        raise BundleError(f"Leerer Pfad im Bundle: {name}")
    return "/".join(parts)


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def write_jsonl(rows: Iterable[dict[str, Any]]) -> bytes:
    """Eine Zeile JSON je Datensatz — auch 10.000 Paper bleiben so streambar."""
    buffer: list[str] = []
    for row in rows:
        buffer.append(json.dumps(row, ensure_ascii=False, default=str))
    return ("\n".join(buffer) + ("\n" if buffer else "")).encode("utf-8")


def read_jsonl(archive: zipfile.ZipFile, name: str) -> Iterator[dict[str, Any]]:
    """Lies eine JSONL-Datei; fehlende Dateien sind leer, kaputte Zeilen ein Fehler."""
    if name not in archive.namelist():
        return
    with archive.open(name) as handle:
        for number, raw in enumerate(handle, start=1):
            line = raw.decode("utf-8").strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as error:
                raise BundleError(f"{name}, Zeile {number}: ungueltiges JSON ({error}).") from error
            if isinstance(row, dict):
                yield row


def slugify(value: str, fallback: str = "projekt") -> str:
    """Dateinamens-tauglicher Projektname fuer den Download."""
    cleaned = re.sub(r"[^\w\s-]", "", str(value), flags=re.UNICODE).strip()
    cleaned = re.sub(r"[\s_]+", "-", cleaned)
    return cleaned[:60].strip("-") or fallback


def bundle_filename(project_id: str, timestamp: str) -> str:
    return f"paperkg-export_{slugify(project_id)}_{timestamp}.zip"


def ensure_zip(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path, "r")
    except zipfile.BadZipFile as error:
        raise BundleError("Die Datei ist kein gueltiges ZIP-Archiv.") from error
