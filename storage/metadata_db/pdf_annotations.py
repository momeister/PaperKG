from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object

_DEFAULT_COLOR = "#f6b73c"  # amber; v1 uses a single fixed color


class PdfAnnotationsMixin(_Base):
    """MetadataDB PDF-annotation operations (mixin).

    A PDF annotation is a small note anchored to a spot in a paper's PDF: either a
    text highlight (``kind='highlight'``) or a point marker (``kind='point'``). Rects
    are normalized 0..1 relative to the page surface so they survive zoom changes.
    """

    def _pdf_annotation_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        rec = dict(zip(cols, row))
        rects = rec.get("rects")
        if isinstance(rects, str):
            try:
                rec["rects"] = json.loads(rects)
            except (json.JSONDecodeError, ValueError):
                rec["rects"] = []
        elif rects is None:
            rec["rects"] = []
        return rec

    def add_pdf_annotation(self, ann: dict[str, Any]) -> dict[str, Any]:
        """Persist a PDF annotation. Returns the stored record."""
        annotation_id = str(ann.get("id") or f"pdfann_{uuid.uuid4().hex}")
        now = datetime.now()
        self._execute("""
            INSERT INTO pdf_annotations
            (id, paper_id, page_number, kind, rects, quote, body, color,
             created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            annotation_id,
            str(ann.get("paper_id") or ""),
            int(ann.get("page_number") or 1),
            str(ann.get("kind") or "highlight"),
            json.dumps(ann.get("rects") or [], ensure_ascii=False),
            ann.get("quote"),
            ann.get("body"),
            str(ann.get("color") or _DEFAULT_COLOR),
            now,
            now,
        ])
        return self.get_pdf_annotation(annotation_id)  # type: ignore[return-value]

    def get_pdf_annotation(self, annotation_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM pdf_annotations WHERE id = ?", [str(annotation_id)]
        ).fetchone()
        return self._pdf_annotation_row(row)

    def list_pdf_annotations(self, paper_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM pdf_annotations WHERE paper_id = ? "
            "ORDER BY page_number ASC, created_timestamp ASC",
            [str(paper_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            rects = rec.get("rects")
            if isinstance(rects, str):
                try:
                    rec["rects"] = json.loads(rects)
                except (json.JSONDecodeError, ValueError):
                    rec["rects"] = []
            elif rects is None:
                rec["rects"] = []
            out.append(rec)
        return out

    def update_pdf_annotation(
        self, annotation_id: str, fields: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Patch mutable fields (body, color, rects, kind, quote). Unknown keys ignored."""
        if self.get_pdf_annotation(annotation_id) is None:
            return None
        allowed = {"body", "color", "kind", "quote", "rects"}
        sets: list[str] = []
        params: list[Any] = []
        for key, value in fields.items():
            if key not in allowed:
                continue
            if key == "rects":
                sets.append("rects = ?")
                params.append(json.dumps(value or [], ensure_ascii=False))
            else:
                sets.append(f"{key} = ?")
                params.append(value)
        if sets:
            sets.append("updated_timestamp = ?")
            params.append(datetime.now())
            params.append(str(annotation_id))
            self._execute(
                f"UPDATE pdf_annotations SET {', '.join(sets)} WHERE id = ?", params
            )
        return self.get_pdf_annotation(annotation_id)

    def delete_pdf_annotation(self, annotation_id: str) -> bool:
        if self.get_pdf_annotation(annotation_id) is None:
            return False
        self._execute("DELETE FROM pdf_annotations WHERE id = ?", [str(annotation_id)])
        return True
