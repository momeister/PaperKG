"""Ein Projekt-Bundle pruefen (Dry-Run) und einspielen."""
from __future__ import annotations

import hashlib
import json
import logging
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from graph_bundle.schema import (
    MANIFEST_NAME,
    PDF_PREFIX,
    PROJECT_NAME,
    TABLE_FILES,
    BundleError,
    BundleManifest,
    ensure_zip,
    read_jsonl,
    safe_member_path,
)
from storage.metadata_db import MetadataDB

logger = logging.getLogger(__name__)

__all__ = ["ImportMode", "BundlePreview", "ImportReport", "preview_bundle", "import_bundle"]

ImportMode = Literal["merge", "replace"]


@dataclass
class BundlePreview:
    """Was das Bundle enthaelt und was ein Import damit tun wuerde."""

    project: str
    exported_at: str
    app_version: str
    bundle_version: int
    includes_pdfs: bool
    counts: dict[str, int] = field(default_factory=dict)
    #: Paper, die es lokal schon gibt (werden beim Merge nicht ueberschrieben).
    papers_existing: int = 0
    papers_new: int = 0
    #: Kollidiert der Projektname mit einem bestehenden Projekt?
    project_exists: bool = False
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "exported_at": self.exported_at,
            "app_version": self.app_version,
            "bundle_version": self.bundle_version,
            "includes_pdfs": self.includes_pdfs,
            "counts": self.counts,
            "papers_existing": self.papers_existing,
            "papers_new": self.papers_new,
            "project_exists": self.project_exists,
            "warnings": self.warnings,
        }


@dataclass
class ImportReport:
    project: str
    mode: str
    papers_imported: int = 0
    papers_skipped: int = 0
    extractions_imported: int = 0
    grey_sources_imported: int = 0
    embeddings_imported: int = 0
    pdfs_imported: int = 0
    paper_ids: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "project": self.project,
            "mode": self.mode,
            "papers_imported": self.papers_imported,
            "papers_skipped": self.papers_skipped,
            "extractions_imported": self.extractions_imported,
            "grey_sources_imported": self.grey_sources_imported,
            "embeddings_imported": self.embeddings_imported,
            "pdfs_imported": self.pdfs_imported,
            "paper_count": len(self.paper_ids),
            "warnings": self.warnings,
        }


def _read_manifest(archive: zipfile.ZipFile) -> BundleManifest:
    if MANIFEST_NAME not in archive.namelist():
        raise BundleError("Kein manifest.json im Archiv — das ist kein ScienceKG-Bundle.")
    with archive.open(MANIFEST_NAME) as handle:
        try:
            data = json.loads(handle.read().decode("utf-8"))
        except json.JSONDecodeError as error:
            raise BundleError(f"manifest.json ist beschaedigt: {error}") from error
    if not isinstance(data, dict):
        raise BundleError("manifest.json enthaelt kein Objekt.")
    return BundleManifest.from_dict(data)


def _read_project(archive: zipfile.ZipFile, manifest: BundleManifest) -> dict[str, Any]:
    if PROJECT_NAME not in archive.namelist():
        return {"id": manifest.project, "paper_ids": [], "primary_paper_id": None, "pinned": False}
    with archive.open(PROJECT_NAME) as handle:
        try:
            data = json.loads(handle.read().decode("utf-8"))
        except json.JSONDecodeError as error:
            raise BundleError(f"project.json ist beschaedigt: {error}") from error
    if not isinstance(data, dict):
        raise BundleError("project.json enthaelt kein Objekt.")
    return data


def _validate_members(archive: zipfile.ZipFile) -> None:
    """Jeden Eintrag gegen Path-Traversal pruefen, bevor irgendetwas geschrieben wird."""
    for name in archive.namelist():
        if name.endswith("/"):
            continue
        safe_member_path(name)


def preview_bundle(
    bundle_path: Path,
    *,
    metadata_db_path: str,
    existing_projects: dict[str, list[str]],
) -> BundlePreview:
    """Lies das Bundle, ohne irgendetwas zu schreiben."""
    with ensure_zip(bundle_path) as archive:
        _validate_members(archive)
        manifest = _read_manifest(archive)
        project = _read_project(archive, manifest)
        bundle_paper_ids = [str(pid) for pid in (project.get("paper_ids") or [])]
        paper_rows = list(read_jsonl(archive, TABLE_FILES["papers"]))

    preview = BundlePreview(
        project=str(project.get("id") or manifest.project),
        exported_at=manifest.exported_at,
        app_version=manifest.app_version,
        bundle_version=manifest.bundle_version,
        includes_pdfs=manifest.includes_pdfs,
        counts=dict(manifest.counts),
        project_exists=str(project.get("id") or manifest.project) in existing_projects,
    )

    incoming_ids = {str(row.get("id") or "") for row in paper_rows if row.get("id")}
    try:
        with MetadataDB(metadata_db_path) as db:
            known = {str(p.get("id") or "") for p in db.list_papers(limit=200_000)}
    except Exception as error:  # noqa: BLE001 - Vorschau soll auch ohne DB etwas sagen
        preview.warnings.append(f"Bestand konnte nicht geprueft werden: {error}")
        known = set()
    preview.papers_existing = len(incoming_ids & known)
    preview.papers_new = len(incoming_ids - known)

    missing_ids = [pid for pid in bundle_paper_ids if pid not in incoming_ids]
    if missing_ids:
        preview.warnings.append(
            f"{len(missing_ids)} Paper stehen in der Projektliste, aber nicht in papers.jsonl — "
            "sie werden dem Projekt trotzdem zugeordnet, falls sie lokal existieren."
        )
    if preview.project_exists:
        preview.warnings.append(
            f"Ein Projekt namens „{preview.project}“ existiert bereits. "
            "„Zusammenfuehren“ ergaenzt es, „Ersetzen“ setzt seine Paperliste neu."
        )
    return preview


def import_bundle(
    bundle_path: Path,
    *,
    metadata_db_path: str,
    existing_projects: dict[str, list[str]],
    mode: ImportMode = "merge",
    target_project: str | None = None,
    pdf_base_dir: str | None = None,
) -> tuple[ImportReport, dict[str, list[str]], dict[str, Any]]:
    """Spiele das Bundle ein.

    Gibt ``(report, aktualisierte_projects, projekt_sidecar_infos)`` zurueck.
    Das Schreiben von ``projects.json`` bleibt beim Aufrufer (dem Router), damit
    es ueber denselben atomaren Helfer laeuft wie alle anderen Projektaenderungen.
    """
    with ensure_zip(bundle_path) as archive:
        _validate_members(archive)
        manifest = _read_manifest(archive)
        project = _read_project(archive, manifest)
        project_id = (target_project or str(project.get("id") or manifest.project)).strip()
        if not project_id:
            raise BundleError("Das Bundle nennt keinen Projektnamen.")

        report = ImportReport(project=project_id, mode=mode)
        bundle_paper_ids = [str(pid) for pid in (project.get("paper_ids") or [])]

        with MetadataDB(metadata_db_path) as db:
            known = {str(p.get("id") or "") for p in db.list_papers(limit=200_000)}

            for row in read_jsonl(archive, TABLE_FILES["papers"]):
                paper_id = str(row.get("id") or "").strip()
                if not paper_id:
                    continue
                if mode == "merge" and paper_id in known:
                    # Lokale Aenderungen (Titel-Reparatur, Downloads) nicht ueberschreiben.
                    report.papers_skipped += 1
                    continue
                db.insert_paper(_paper_record(row))
                known.add(paper_id)
                report.papers_imported += 1

            for row in read_jsonl(archive, TABLE_FILES["paper_sources"]):
                paper_id = str(row.get("paper_id") or "")
                source = str(row.get("source") or "")
                if not paper_id or not source:
                    continue
                db._execute(
                    "INSERT OR REPLACE INTO paper_sources (paper_id, source, source_id, source_url) VALUES (?, ?, ?, ?)",
                    [paper_id, source, row.get("source_id"), row.get("source_url")],
                )

            existing_extractions = {
                _extraction_fingerprint(e) for e in db.list_extraction_results(limit=200_000)
            }
            for row in read_jsonl(archive, TABLE_FILES["extraction_results"]):
                paper_id = str(row.get("paper_id") or "").strip()
                if not paper_id:
                    continue
                fingerprint = _extraction_fingerprint(row)
                if mode == "merge" and fingerprint in existing_extractions:
                    continue
                db.save_extraction_result(**_extraction_kwargs(row))
                existing_extractions.add(fingerprint)
                report.extractions_imported += 1

            for row in read_jsonl(archive, TABLE_FILES["grey_sources"]):
                if not str(row.get("url") or "").strip():
                    continue
                # add_grey_source ist ein Upsert auf der id — doppelte Importe
                # erzeugen daher keine Duplikate.
                db.add_grey_source(project_id, dict(row))
                report.grey_sources_imported += 1

            for row in read_jsonl(archive, TABLE_FILES["entity_embeddings"]):
                vector = row.get("vector")
                if isinstance(vector, str):
                    try:
                        vector = json.loads(vector)
                    except json.JSONDecodeError:
                        continue
                if not isinstance(vector, list) or not vector:
                    continue
                db.upsert_entity_embedding(
                    label=str(row.get("label") or row.get("label_norm") or ""),
                    vector=[float(v) for v in vector],
                    model=str(row.get("model") or "unknown"),
                    backend=str(row.get("backend") or "unknown"),
                    dimension=int(row.get("dimension") or len(vector)),
                    embedding_version=int(row.get("embedding_version") or 1),
                )
                report.embeddings_imported += 1

        if manifest.includes_pdfs:
            report.pdfs_imported = _restore_pdfs(archive, pdf_base_dir or "data/pdfs")

    # Projektzuordnung zusammenfuehren bzw. ersetzen.
    projects = dict(existing_projects)
    current = projects.get(project_id, []) if mode == "merge" else []
    merged = list(current)
    for paper_id in bundle_paper_ids:
        if paper_id not in merged:
            merged.append(paper_id)
    projects[project_id] = merged
    report.paper_ids = merged

    sidecars = {
        "primary_paper_id": project.get("primary_paper_id"),
        "pinned": bool(project.get("pinned")),
    }
    return report, projects, sidecars


def _extraction_fingerprint(row: dict[str, Any]) -> str:
    """Inhaltlicher Fingerabdruck einer Extraktion.

    Der ``extraction_timestamp`` taugt nicht als Schluessel: ``save_extraction_result``
    vergibt beim Schreiben einen neuen. Ein zweiter Import haette sonst jedes Mal
    dieselbe Extraktion erneut angelegt. Der Fingerabdruck bleibt dagegen ueber
    Export und Import hinweg gleich.
    """
    def _labels(field: str) -> list[str]:
        values = row.get(field) or []
        if isinstance(values, str):
            try:
                values = json.loads(values)
            except json.JSONDecodeError:
                return []
        return sorted(
            str(entry.get("label") or "") for entry in values if isinstance(entry, dict)
        )

    payload = json.dumps(
        {
            "paper_id": str(row.get("paper_id") or ""),
            "llm_model": str(row.get("llm_model") or ""),
            "paper_type": str(row.get("paper_type") or ""),
            "concepts": _labels("concepts"),
            "methods": _labels("methods"),
            "claims": len(row.get("claims") or []),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _paper_record(row: dict[str, Any]) -> dict[str, Any]:
    """DuckDB-Zeile -> ``insert_paper``-Record (JSON-Spalten sind schon dekodiert)."""
    record = dict(row)
    for field_name in ("authors", "references", "citations"):
        value = record.get(field_name)
        if isinstance(value, str):
            try:
                record[field_name] = json.loads(value)
            except json.JSONDecodeError:
                record[field_name] = []
        elif value is None:
            record[field_name] = []
    # pdf_url zeigt nach einem Download auf einen *lokalen* Pfad der Quellmaschine.
    # Der existiert hier nicht; ohne PDFs im Bundle waere der Link tot.
    pdf_url = str(record.get("pdf_url") or "")
    if pdf_url and not pdf_url.lower().startswith(("http://", "https://")):
        record["pdf_url"] = None
    return record


_EXTRACTION_FIELDS = (
    "paper_type", "concepts", "methods", "concept_candidates", "method_candidates",
    "relations", "claims", "cross_domain_hints", "terminology_conflicts",
    "temporal_coverage", "mathematical_content", "error_message", "duration_seconds",
)


def _extraction_kwargs(row: dict[str, Any]) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "paper_id": str(row.get("paper_id") or ""),
        "llm_provider": str(row.get("llm_provider") or "imported"),
        "llm_model": str(row.get("llm_model") or "unknown"),
    }
    for name in _EXTRACTION_FIELDS:
        if name in row and row[name] is not None:
            kwargs[name] = row[name]
    raw = row.get("raw_response")
    if raw is not None:
        kwargs["raw_response"] = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    return kwargs


def _restore_pdfs(archive: zipfile.ZipFile, pdf_base_dir: str) -> int:
    """Schreibe die beigelegten PDFs nach ``<pdf_base_dir>/<paper_id>/<datei>``."""
    base = Path(pdf_base_dir)
    base.mkdir(parents=True, exist_ok=True)
    root = base.resolve()
    count = 0
    for name in archive.namelist():
        if not name.startswith(PDF_PREFIX) or name.endswith("/"):
            continue
        relative = safe_member_path(name)[len(PDF_PREFIX):]
        target = (base / relative).resolve()
        # Zweiter Riegel nach safe_member_path: auch symlink-/normalisierungsbedingte
        # Ausbrueche landen so nicht ausserhalb der PDF-Bibliothek.
        if root != target and root not in target.parents:
            raise BundleError(f"PDF-Pfad ausserhalb der Bibliothek abgelehnt: {name}")
        target.parent.mkdir(parents=True, exist_ok=True)
        with archive.open(name) as source, open(target, "wb") as sink:
            sink.write(source.read())
        count += 1
    return count
