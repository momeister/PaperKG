from __future__ import annotations

import json
import re
import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class NotesMixin(_Base):
    """MetadataDB notes operations (mixin)."""

    def create_note(
        self,
        project_id: str,
        title: str,
        markdown: str = "",
        note_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now()
        note_id = note_id or f"note_{uuid.uuid4().hex}"
        self._execute("""
            INSERT INTO notes (id, project_id, title, markdown, created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [note_id, project_id, title.strip() or "Neue Notiz", markdown, now, now])
        note = self.get_note(note_id)
        if note is None:
            raise RuntimeError(f"Failed to create note: {note_id}")
        return note

    def get_note(self, note_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM notes WHERE id = ?", [note_id]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        note = dict(zip(cols, row))
        note["citations"] = self.list_note_citations(note_id)
        note["assets"] = self.list_note_assets(note_id)
        return note

    def list_notes(self, project_id: str | None = None, limit: int = 500) -> list[dict[str, Any]]:
        if project_id:
            rows = self._execute("""
                SELECT * FROM notes
                WHERE project_id = ?
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [project_id, limit]).fetchall()
        else:
            rows = self._execute("""
                SELECT * FROM notes
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def save_note_version(self, note_id: str, markdown: str, reason: str = "edit") -> dict[str, Any]:
        version_id = f"ver_{uuid.uuid4().hex}"
        now = datetime.now()
        self._execute("""
            INSERT INTO note_versions (id, note_id, markdown, reason, created_timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, [version_id, note_id, markdown, reason, now])
        return {"id": version_id, "note_id": note_id, "markdown": markdown, "reason": reason, "created_timestamp": now}

    def update_note(
        self,
        note_id: str,
        title: str | None = None,
        markdown: str | None = None,
        version_reason: str = "edit",
    ) -> dict[str, Any] | None:
        current = self.get_note(note_id)
        if current is None:
            return None
        if markdown is not None and markdown != current.get("markdown"):
            self.save_note_version(note_id, str(current.get("markdown") or ""), version_reason)
        next_title = title.strip() if title is not None and title.strip() else str(current.get("title") or "Neue Notiz")
        next_markdown = markdown if markdown is not None else str(current.get("markdown") or "")
        self._execute("""
            UPDATE notes
            SET title = ?, markdown = ?, updated_timestamp = ?
            WHERE id = ?
        """, [next_title, next_markdown, datetime.now(), note_id])
        return self.get_note(note_id)

    def append_note_markdown(
        self,
        note_id: str,
        markdown: str,
        title: str | None = None,
        citations: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | None:
        note = self.get_note(note_id)
        if note is None:
            return None
        current = str(note.get("markdown") or "")
        addition = str(markdown or "").strip()
        next_markdown = current.rstrip()
        if addition:
            next_markdown = f"{next_markdown}\n\n{addition}".strip() if next_markdown else addition
        updated = self.update_note(note_id, title=title, markdown=next_markdown, version_reason="append")
        for citation in citations or []:
            self.add_note_citation(note_id, citation)
        return self.get_note(note_id) or updated

    def delete_note(self, note_id: str) -> bool:
        if self.get_note(note_id) is None:
            return False
        self._execute("DELETE FROM note_ai_messages WHERE note_id = ?", [note_id])
        self._execute("DELETE FROM note_ai_threads WHERE note_id = ?", [note_id])
        self._execute("DELETE FROM note_versions WHERE note_id = ?", [note_id])
        self._execute("DELETE FROM note_assets WHERE note_id = ?", [note_id])
        self._execute("DELETE FROM note_citations WHERE note_id = ?", [note_id])
        self._execute("DELETE FROM notes WHERE id = ?", [note_id])
        return True

    def add_note_citation(self, note_id: str, citation: dict[str, Any]) -> dict[str, Any]:
        citation_id = str(citation.get("id") or self._stable_note_citation_id(note_id, citation))
        self._execute("""
            INSERT INTO note_citations
            (id, note_id, paper_id, title, kind, reference_text, pdf_excerpt, evidence_id, evidence_index, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                paper_id = EXCLUDED.paper_id,
                title = EXCLUDED.title,
                kind = EXCLUDED.kind,
                reference_text = EXCLUDED.reference_text,
                pdf_excerpt = EXCLUDED.pdf_excerpt,
                evidence_id = EXCLUDED.evidence_id,
                evidence_index = EXCLUDED.evidence_index
        """, [
            citation_id,
            note_id,
            str(citation.get("paper_id") or ""),
            citation.get("title"),
            citation.get("kind"),
            citation.get("reference_text"),
            citation.get("pdf_excerpt"),
            citation.get("evidence_id"),
            int(citation.get("evidence_index") or 0),
            datetime.now(),
        ])
        return self.get_note_citation(citation_id) or {"id": citation_id}

    def _stable_note_citation_id(self, note_id: str, citation: dict[str, Any]) -> str:
        paper_id = str(citation.get("paper_id") or "")
        reference = self._normalize_citation_text(str(citation.get("reference_text") or ""))
        excerpt = self._normalize_citation_text(str(citation.get("pdf_excerpt") or ""))
        evidence_id = str(citation.get("evidence_id") or "")
        evidence_index = str(citation.get("evidence_index") or 0)
        basis = "|".join([note_id, paper_id, evidence_id, reference[:500], excerpt[:500], evidence_index])
        return f"cite_{uuid.uuid5(uuid.NAMESPACE_URL, basis).hex}"

    @staticmethod
    def _normalize_citation_text(value: str) -> str:
        return re.sub(r"\s+", " ", value).strip().lower()

    def get_note_citation(self, citation_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM note_citations WHERE id = ?", [citation_id]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return dict(zip(cols, row))

    def delete_note_citation(self, note_id: str, citation_id: str) -> bool:
        existing = self.get_note_citation(citation_id)
        if existing is None or str(existing.get("note_id")) != str(note_id):
            return False
        self._execute("DELETE FROM note_citations WHERE id = ? AND note_id = ?", [citation_id, note_id])
        return True

    def list_note_citations(self, note_id: str) -> list[dict[str, Any]]:
        rows = self._execute("""
            SELECT * FROM note_citations
            WHERE note_id = ?
            ORDER BY created_timestamp ASC
        """, [note_id]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def add_note_asset(self, note_id: str, filename: str, content_type: str, asset_path: str) -> dict[str, Any]:
        asset_id = f"asset_{uuid.uuid4().hex}"
        self._execute("""
            INSERT INTO note_assets (id, note_id, filename, content_type, asset_path, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [asset_id, note_id, filename, content_type, asset_path, datetime.now()])
        return self.get_note_asset(asset_id) or {"id": asset_id}

    def get_note_asset(self, asset_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM note_assets WHERE id = ?", [asset_id]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return dict(zip(cols, row))

    def list_note_assets(self, note_id: str) -> list[dict[str, Any]]:
        rows = self._execute("""
            SELECT id, note_id, filename, content_type, asset_path, created_timestamp
            FROM note_assets
            WHERE note_id = ?
            ORDER BY created_timestamp ASC
        """, [note_id]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def add_note_ai_thread(
        self,
        note_id: str,
        selected_text: str,
        instruction: str,
        response_text: str,
        replacement_text: str | None = None,
        answer_payload: dict[str, Any] | None = None,
        anchor_start: int | None = None,
        anchor_end: int | None = None,
        anchor_quote: str | None = None,
        ui_state: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        thread_id = f"thread_{uuid.uuid4().hex}"
        now = datetime.now()
        self._execute("""
            INSERT INTO note_ai_threads
            (
                id, note_id, selected_text, instruction, response_text, replacement_text,
                answer_payload, anchor_start, anchor_end, anchor_quote, ui_state,
                updated_timestamp, created_timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            thread_id,
            note_id,
            selected_text,
            instruction,
            response_text,
            replacement_text,
            json.dumps(answer_payload or {}),
            anchor_start,
            anchor_end,
            anchor_quote,
            json.dumps(ui_state or {}),
            now,
            now,
        ])
        self.add_note_ai_message(thread_id, note_id, "user", instruction, created_timestamp=now)
        self.add_note_ai_message(thread_id, note_id, "assistant", response_text, created_timestamp=now)
        return self.get_note_ai_thread(thread_id) or {
            "id": thread_id,
            "note_id": note_id,
            "selected_text": selected_text,
            "instruction": instruction,
            "response_text": response_text,
            "replacement_text": replacement_text,
            "answer_payload": answer_payload or {},
            "messages": [],
            "created_timestamp": now,
            "updated_timestamp": now,
        }

    def get_note_ai_thread(self, thread_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM note_ai_threads WHERE id = ?", [thread_id]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        item = self._decode_note_ai_thread(dict(zip(cols, row)))
        item["messages"] = self.list_note_ai_messages(thread_id)
        if not item["messages"]:
            item["messages"] = self._legacy_thread_messages(item)
        return item

    def update_note_ai_thread(
        self,
        thread_id: str,
        ui_state: dict[str, Any] | None = None,
        replacement_text: str | None = None,
        response_text: str | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_note_ai_thread(thread_id)
        if current is None:
            return None
        next_ui_state = ui_state if ui_state is not None else current.get("ui_state") or {}
        next_replacement = replacement_text if replacement_text is not None else current.get("replacement_text")
        next_response = response_text if response_text is not None else current.get("response_text")
        content_changed = replacement_text is not None or response_text is not None
        next_updated = datetime.now() if content_changed else current.get("updated_timestamp")
        self._execute("""
            UPDATE note_ai_threads
            SET ui_state = ?, replacement_text = ?, response_text = ?, updated_timestamp = ?
            WHERE id = ?
        """, [json.dumps(next_ui_state), next_replacement, next_response, next_updated, thread_id])
        return self.get_note_ai_thread(thread_id)

    def delete_note_ai_thread(self, thread_id: str) -> bool:
        current = self.get_note_ai_thread(thread_id)
        if current is None:
            return False
        self._execute("DELETE FROM note_ai_messages WHERE thread_id = ?", [thread_id])
        self._execute("DELETE FROM note_ai_threads WHERE id = ?", [thread_id])
        return True

    def delete_note_ai_threads(self, note_id: str) -> int:
        threads = self.list_note_ai_threads(note_id, limit=10000)
        if not threads:
            return 0
        self._execute("DELETE FROM note_ai_messages WHERE note_id = ?", [note_id])
        self._execute("DELETE FROM note_ai_threads WHERE note_id = ?", [note_id])
        return len(threads)

    def add_note_ai_message(
        self,
        thread_id: str,
        note_id: str,
        role: str,
        content: str,
        created_timestamp: datetime | None = None,
    ) -> dict[str, Any]:
        message_id = f"msg_{uuid.uuid4().hex}"
        now = created_timestamp or datetime.now()
        self._execute("""
            INSERT INTO note_ai_messages (id, thread_id, note_id, role, content, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [message_id, thread_id, note_id, role, content, now])
        self._execute("UPDATE note_ai_threads SET updated_timestamp = ? WHERE id = ?", [now, thread_id])
        return {
            "id": message_id,
            "thread_id": thread_id,
            "note_id": note_id,
            "role": role,
            "content": content,
            "created_timestamp": now,
        }

    def list_note_ai_messages(self, thread_id: str) -> list[dict[str, Any]]:
        rows = self._execute("""
            SELECT * FROM note_ai_messages
            WHERE thread_id = ?
            ORDER BY created_timestamp ASC
        """, [thread_id]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def list_note_ai_threads(self, note_id: str, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._execute("""
            SELECT * FROM note_ai_threads
            WHERE note_id = ?
            ORDER BY updated_timestamp DESC, created_timestamp DESC
            LIMIT ?
        """, [note_id, limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        output = []
        for row in rows:
            item = self._decode_note_ai_thread(dict(zip(cols, row)))
            item["messages"] = self.list_note_ai_messages(str(item.get("id") or ""))
            if not item["messages"]:
                item["messages"] = self._legacy_thread_messages(item)
            output.append(item)
        return output

    def _decode_note_ai_thread(self, item: dict[str, Any]) -> dict[str, Any]:
        try:
            item["answer_payload"] = json.loads(item.get("answer_payload") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["answer_payload"] = {}
        try:
            item["ui_state"] = json.loads(item.get("ui_state") or "{}")
        except (TypeError, json.JSONDecodeError):
            item["ui_state"] = {}
        return item

    @staticmethod
    def _legacy_thread_messages(item: dict[str, Any]) -> list[dict[str, Any]]:
        created = item.get("created_timestamp")
        return [
            {
                "id": f"{item.get('id')}:user",
                "thread_id": item.get("id"),
                "note_id": item.get("note_id"),
                "role": "user",
                "content": item.get("instruction") or "",
                "created_timestamp": created,
            },
            {
                "id": f"{item.get('id')}:assistant",
                "thread_id": item.get("id"),
                "note_id": item.get("note_id"),
                "role": "assistant",
                "content": item.get("response_text") or "",
                "created_timestamp": created,
            },
        ]

    def restore_latest_note_version(self, note_id: str) -> dict[str, Any] | None:
        row = self._execute("""
            SELECT * FROM note_versions
            WHERE note_id = ?
            ORDER BY created_timestamp DESC
            LIMIT 1
        """, [note_id]).fetchone()
        if row is None:
            return self.get_note(note_id)
        cols = [desc[0] for desc in self.conn.description]
        version = dict(zip(cols, row))
        current = self.get_note(note_id)
        if current is None:
            return None
        self.save_note_version(note_id, str(current.get("markdown") or ""), "redo-snapshot")
        self._execute("""
            UPDATE notes
            SET markdown = ?, updated_timestamp = ?
            WHERE id = ?
        """, [str(version.get("markdown") or ""), datetime.now(), note_id])
        return self.get_note(note_id)
