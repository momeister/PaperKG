"""Reconstructible passage and claim-check caches in the primary DuckDB."""

import json


class PassagesMixin:
    def replace_passages(
        self, paper_id, fingerprint, parser_version, pdf_path, passages
    ):
        self._execute("BEGIN TRANSACTION")
        try:
            self._execute("DELETE FROM paper_passages WHERE paper_id = ?", [paper_id])
            for p in passages:
                self._execute(
                    "INSERT INTO paper_passages VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        p["passage_id"],
                        paper_id,
                        fingerprint,
                        parser_version,
                        p["page"],
                        p["start"],
                        p["end"],
                        p["text"],
                        p.get("section", ""),
                        json.dumps(p.get("positions", [])),
                    ],
                )
            self._execute(
                "INSERT OR REPLACE INTO passage_documents VALUES (?, ?, ?, ?)",
                [paper_id, fingerprint, parser_version, str(pdf_path)],
            )
            self._execute("COMMIT")
        except Exception:
            self._execute("ROLLBACK")
            raise

    def passage_document(self, paper_id):
        row = self._execute(
            "SELECT fingerprint, parser_version, pdf_path FROM passage_documents WHERE paper_id = ?",
            [paper_id],
        ).fetchone()
        return (
            dict(zip(("fingerprint", "parser_version", "pdf_path"), row))
            if row
            else None
        )

    def list_passages(self, paper_ids=None):
        where = " WHERE paper_id IN (SELECT unnest(?))" if paper_ids is not None else ""
        rows = self._execute(
            "SELECT * FROM paper_passages"
            + where
            + " ORDER BY paper_id, page, start_pos",
            [list(paper_ids)] if paper_ids is not None else [],
        ).fetchall()
        keys = [d[0] for d in self.conn.description]
        return [dict(zip(keys, row)) for row in rows]

    def cached_claim_check(self, key):
        row = self._execute(
            "SELECT result FROM claim_check_cache WHERE cache_key = ?", [key]
        ).fetchone()
        return json.loads(row[0]) if row else None

    def cache_claim_check(self, key, result):
        self._execute(
            "INSERT OR REPLACE INTO claim_check_cache VALUES (?, ?)",
            [key, json.dumps(result)],
        )
