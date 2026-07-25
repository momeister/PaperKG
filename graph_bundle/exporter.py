"""Ein Projekt als portables ZIP-Bundle schreiben."""
from __future__ import annotations

import json
import logging
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any

from graph_bundle.schema import (
    BUNDLE_VERSION,
    MANIFEST_NAME,
    PDF_PREFIX,
    PROJECT_NAME,
    TABLE_FILES,
    BundleError,
    BundleManifest,
    bundle_filename,
    sha256_bytes,
    write_jsonl,
)
from storage.metadata_db import MetadataDB

logger = logging.getLogger(__name__)

__all__ = ["export_project"]

#: Grosszuegig, aber endlich — schuetzt vor einem versehentlichen Vollexport.
_ROW_LIMIT = 200_000


def export_project(
    project_id: str,
    *,
    paper_ids: list[str],
    metadata_db_path: str,
    output_dir: Path,
    primary_paper_id: str | None = None,
    pinned: bool = False,
    include_pdfs: bool = False,
    pdf_base_dir: str | None = None,
    app_version: str = "5.0.0",
) -> Path:
    """Schreibe ``<output_dir>/paperkg-export_<projekt>_<datum>.zip`` und gib den Pfad zurueck.

    ``paper_ids`` kommt aus ``data/projects.json`` — die Projektzugehoerigkeit lebt
    dort, nicht in DuckDB. Fuer die globale Bibliothek (``Alle Papers``) uebergibt
    der Aufrufer einfach alle Paper-IDs.
    """
    member_ids = [str(pid) for pid in paper_ids if str(pid or "").strip()]
    member_set = set(member_ids)
    if not member_set:
        raise BundleError(f"Projekt „{project_id}“ enthaelt keine Paper — nichts zu exportieren.")

    output_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now()
    target = output_dir / bundle_filename(project_id, timestamp.strftime("%Y-%m-%d_%H%M%S"))

    with MetadataDB(metadata_db_path) as db:
        papers = [p for p in db.list_papers(limit=_ROW_LIMIT) if str(p.get("id") or "") in member_set]
        found_ids = {str(p.get("id") or "") for p in papers}
        extractions = [
            e for e in db.list_extraction_results(limit=_ROW_LIMIT)
            if str(e.get("paper_id") or "") in found_ids
        ]
        grey_sources = db.list_grey_sources(project_id, limit=_ROW_LIMIT)
        paper_sources = _paper_sources(db, found_ids)
        # Embeddings sind nicht projektgebunden; nur die mitnehmen, die zu den
        # exportierten Extraktionen gehoeren — sonst waechst jedes Bundle auf die
        # Groesse der gesamten Bibliothek.
        embeddings = _relevant_embeddings(db, extractions)

    missing = sorted(member_set - found_ids)
    if missing:
        logger.info("Export %s: %d Projekt-Paper ohne DB-Eintrag uebersprungen", project_id, len(missing))

    payloads: dict[str, bytes] = {
        TABLE_FILES["papers"]: write_jsonl(papers),
        TABLE_FILES["paper_sources"]: write_jsonl(paper_sources),
        TABLE_FILES["extraction_results"]: write_jsonl(extractions),
        TABLE_FILES["grey_sources"]: write_jsonl(grey_sources),
        TABLE_FILES["entity_embeddings"]: write_jsonl(embeddings),
    }
    project_payload = json.dumps(
        {
            "id": project_id,
            "name": project_id,
            "paper_ids": member_ids,
            "primary_paper_id": primary_paper_id,
            "pinned": bool(pinned),
        },
        indent=2,
        ensure_ascii=False,
    ).encode("utf-8")
    payloads[PROJECT_NAME] = project_payload

    manifest = BundleManifest(
        bundle_version=BUNDLE_VERSION,
        project=project_id,
        exported_at=timestamp.isoformat(timespec="seconds"),
        app_version=app_version,
        counts={
            "papers": len(papers),
            "paper_sources": len(paper_sources),
            "extraction_results": len(extractions),
            "grey_sources": len(grey_sources),
            "entity_embeddings": len(embeddings),
            "paper_ids": len(member_ids),
            "papers_missing_in_db": len(missing),
        },
        files={name: sha256_bytes(data) for name, data in payloads.items()},
        includes_pdfs=bool(include_pdfs),
    )

    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        archive.writestr(MANIFEST_NAME, manifest.to_json())
        for name, data in payloads.items():
            archive.writestr(name, data)
        if include_pdfs:
            pdf_count = _add_pdfs(archive, papers, pdf_base_dir)
            logger.info("Export %s: %d PDFs beigelegt", project_id, pdf_count)

    return target


def _paper_sources(db: MetadataDB, paper_ids: set[str]) -> list[dict[str, Any]]:
    """Provenienz je Paper (welche Quelle es geliefert hat)."""
    if not paper_ids:
        return []
    rows = db._execute("SELECT paper_id, source, source_id, source_url FROM paper_sources").fetchall()
    return [
        {"paper_id": r[0], "source": r[1], "source_id": r[2], "source_url": r[3]}
        for r in rows
        if str(r[0]) in paper_ids
    ]


def _relevant_embeddings(db: MetadataDB, extractions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nur Embeddings zu Konzept-/Methoden-Labels aus diesem Projekt."""
    wanted: set[str] = set()
    for extraction in extractions:
        for field in ("concepts", "methods"):
            for entry in extraction.get(field) or []:
                if isinstance(entry, dict):
                    label = str(entry.get("label") or "").strip().lower()
                    if label:
                        wanted.add(label)
    if not wanted:
        return []
    return [
        row
        for row in db.list_entity_embeddings(limit=_ROW_LIMIT)
        if str(row.get("label_norm") or row.get("label") or "").strip().lower() in wanted
    ]


def _add_pdfs(archive: zipfile.ZipFile, papers: list[dict[str, Any]], pdf_base_dir: str | None) -> int:
    """Lege die lokalen PDFs unter ``pdfs/<paper_id>/<datei>`` bei."""
    from api.routers.papers import _paper_local_pdf_path
    from query.source_verifier import build_pdf_index

    base = pdf_base_dir or "data/pdfs"
    index = build_pdf_index(base)
    added = 0
    for paper in papers:
        local = _paper_local_pdf_path(paper, base, pdf_index=index)
        if not local:
            continue
        source = Path(local)
        if not source.is_file():
            continue
        # paper_id als Ordner: eindeutig und ohne Kollisionen bei gleichen Dateinamen.
        safe_id = str(paper.get("id") or "").replace("/", "_").replace("\\", "_").replace(":", "_")
        archive.write(source, f"{PDF_PREFIX}{safe_id}/{source.name}")
        added += 1
    return added
