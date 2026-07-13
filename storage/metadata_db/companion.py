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


class CompanionMixin(_Base):
    """Desktop-Companion / Selbst-Steuerung sessions + durable chat/step log (R7).

    ``kind`` is ``'companion'`` (screen chat / guided sequences) or ``'selfdrive'``
    (autonomous runs). Messages hold the transcript; structured extras (steps,
    actions, sources, verification) go into ``payload``.
    """

    def _touch_companion_session(self, session_id: str) -> None:
        self._execute(
            "UPDATE companion_sessions SET updated_timestamp = ? WHERE id = ?",
            [datetime.now(), str(session_id)],
        )

    def create_companion_session(
        self,
        kind: str,
        title: str = "",
        goal: str = "",
        provider: str | None = None,
        model: str | None = None,
        monitor: int | None = None,
        session_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now()
        sid = session_id or f"cs_{uuid.uuid4().hex}"
        self._execute("""
            INSERT INTO companion_sessions
            (id, kind, title, goal, status, provider, model, monitor,
             created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [sid, str(kind or "companion"), str(title or ""), str(goal or ""), "active",
              provider, model, int(monitor) if monitor is not None else None, now, now])
        return self.get_companion_session(sid) or {"id": sid, "messages": []}

    def get_companion_session(self, session_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM companion_sessions WHERE id = ?", [str(session_id)]
        ).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        session = dict(zip(cols, row))
        session["messages"] = self.list_companion_messages(session_id)
        return session

    def list_companion_sessions(
        self, kind: str | None = None, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Session summaries for the overlay list — message counts, no payloads."""
        if kind:
            rows = self._execute(
                "SELECT * FROM companion_sessions WHERE kind = ? "
                "ORDER BY updated_timestamp DESC LIMIT ?",
                [str(kind), limit],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM companion_sessions ORDER BY updated_timestamp DESC LIMIT ?",
                [limit],
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            count = self._execute(
                "SELECT COUNT(*) FROM companion_messages WHERE session_id = ?", [rec["id"]]
            ).fetchone()
            rec["message_count"] = int(count[0]) if count else 0
            out.append(rec)
        return out

    def update_companion_session(self, session_id: str, **fields: Any) -> dict[str, Any] | None:
        current = self.get_companion_session(session_id)
        if current is None:
            return None
        allowed = ("title", "goal", "status", "provider", "model", "monitor")
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return current
        set_clause = ", ".join(f"{k} = ?" for k in updates) + ", updated_timestamp = ?"
        params = list(updates.values()) + [datetime.now(), str(session_id)]
        self._execute(f"UPDATE companion_sessions SET {set_clause} WHERE id = ?", params)
        return self.get_companion_session(session_id)

    def delete_companion_session(self, session_id: str) -> bool:
        if self.get_companion_session(session_id) is None:
            return False
        self._execute("DELETE FROM companion_messages WHERE session_id = ?", [str(session_id)])
        self._execute("DELETE FROM companion_sessions WHERE id = ?", [str(session_id)])
        return True

    def add_companion_message(
        self,
        session_id: str,
        role: str,
        content: str = "",
        payload: dict[str, Any] | None = None,
        message_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now()
        mid = message_id or f"cm_{uuid.uuid4().hex}"
        self._execute("""
            INSERT INTO companion_messages
            (id, session_id, role, content, payload, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        """, [mid, str(session_id), str(role or "user"), str(content or ""),
              json.dumps(payload) if payload is not None else None, now])
        self._touch_companion_session(session_id)
        row = self._execute("SELECT * FROM companion_messages WHERE id = ?", [mid]).fetchone()
        cols = [desc[0] for desc in self.conn.description]
        rec = dict(zip(cols, row))
        rec["payload"] = self._decode_json(rec.get("payload"), None)
        return rec

    def list_companion_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM companion_messages WHERE session_id = ? ORDER BY created_timestamp ASC, id ASC",
            [str(session_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            rec["payload"] = self._decode_json(rec.get("payload"), None)
            out.append(rec)
        return out
