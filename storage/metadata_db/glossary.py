"""Global, project-independent dictionary."""

from __future__ import annotations

import unicodedata
import uuid

import duckdb
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


def normalize_term(term: str) -> str:
    return unicodedata.normalize("NFKC", " ".join(term.split())).casefold()


class GlossaryDuplicateError(ValueError):
    def __init__(self, entry: dict):
        super().__init__("Dieser Begriff ist bereits im Wörterbuch vorhanden.")
        self.entry = entry


class GlossaryMixin(_Base):
    def list_glossary(self) -> list[dict]:
        rows = self._execute("SELECT * FROM glossary ORDER BY term_key").fetchall()
        return [
            dict(
                zip(
                    (
                        "id",
                        "term",
                        "term_key",
                        "explanation",
                        "created_at",
                        "updated_at",
                    ),
                    row,
                )
            )
            for row in rows
        ]

    def save_glossary(
        self, term: str, explanation: str, entry_id: str | None = None
    ) -> dict:
        term, explanation = " ".join(term.split()), explanation.strip()
        if not term or not explanation:
            raise ValueError("Begriff und Erklärung dürfen nicht leer sein.")
        key = normalize_term(term)
        entries = self.list_glossary()
        if entry_id and not any(e["id"] == entry_id for e in entries):
            raise KeyError(entry_id)
        duplicate = next(
            (e for e in entries if e["term_key"] == key and e["id"] != entry_id), None
        )
        if duplicate:
            raise GlossaryDuplicateError(duplicate)
        try:
            if entry_id:
                self._execute(
                    "UPDATE glossary SET term=?, term_key=?, explanation=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                    [term, key, explanation, entry_id],
                )
            else:
                entry_id = uuid.uuid4().hex
                self._execute(
                    "INSERT INTO glossary (id, term, term_key, explanation) VALUES (?, ?, ?, ?)",
                    [entry_id, term, key, explanation],
                )
        except duckdb.ConstraintException as error:
            # Another request can win between the lookup and the UNIQUE-constrained write.
            duplicate = next(
                (
                    e
                    for e in self.list_glossary()
                    if e["term_key"] == key and e["id"] != entry_id
                ),
                None,
            )
            if duplicate:
                raise GlossaryDuplicateError(duplicate) from error
            raise
        return next(e for e in self.list_glossary() if e["id"] == entry_id)

    def delete_glossary(self, entry_id: str) -> bool:
        return (
            self._execute(
                "DELETE FROM glossary WHERE id=? RETURNING id", [entry_id]
            ).fetchone()
            is not None
        )
