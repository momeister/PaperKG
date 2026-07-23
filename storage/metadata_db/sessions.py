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


class SessionsMixin(_Base):
    """MetadataDB sessions operations (mixin)."""

    def get_workspace_session(self, project_id: str) -> dict[str, Any] | None:
        rows = self._execute(
            "SELECT payload, updated_timestamp FROM workspace_sessions WHERE project_id = ?",
            [str(project_id)],
        ).fetchall()
        if not rows:
            return None
        payload = rows[0][0]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                payload = {}
        return {
            "project_id": str(project_id),
            "payload": payload if isinstance(payload, dict) else {},
            "updated_timestamp": rows[0][1],
        }

    WORKSPACE_SESSION_BACKUPS_KEPT = 20

    @staticmethod
    def _workspace_turn_count(payload: Any) -> int:
        history = (payload or {}).get("history") if isinstance(payload, dict) else None
        return len(history) if isinstance(history, list) else 0

    def save_workspace_session(
        self, project_id: str, payload: dict[str, Any], force: bool = False
    ) -> dict[str, Any]:
        """Persist the workspace session, keeping a rolling backup of the previous state.

        Refuses to replace a non-empty conversation with an empty one unless ``force`` is
        set: a client that boots before the backend is reachable used to fall back to an
        empty history and then wrote that over the server copy, destroying the session.
        The explicit "delete session" path passes ``force=True``.
        """
        current = self.get_workspace_session(project_id)
        previous_turns = self._workspace_turn_count(current.get("payload") if current else None)
        incoming_turns = self._workspace_turn_count(payload)

        if not force and incoming_turns == 0 and previous_turns > 0:
            return current or {"project_id": str(project_id), "payload": {}}

        if current and previous_turns > 0:
            self._backup_workspace_session(project_id, current, previous_turns)

        self._execute("""
            INSERT INTO workspace_sessions (project_id, payload, updated_timestamp)
            VALUES (?, ?, ?)
            ON CONFLICT (project_id) DO UPDATE SET
                payload = EXCLUDED.payload,
                updated_timestamp = EXCLUDED.updated_timestamp
        """, [str(project_id), json.dumps(payload or {}), datetime.now()])
        return self.get_workspace_session(project_id) or {"project_id": str(project_id), "payload": {}}

    def _backup_workspace_session(
        self, project_id: str, session: dict[str, Any], turn_count: int
    ) -> None:
        saved_at = session.get("updated_timestamp") or datetime.now()
        self._execute(
            "INSERT INTO workspace_session_backups (project_id, saved_at, payload, turn_count)"
            " VALUES (?, ?, ?, ?)",
            [str(project_id), saved_at, json.dumps(session.get("payload") or {}), int(turn_count)],
        )
        self._execute(
            """
            DELETE FROM workspace_session_backups
            WHERE project_id = ? AND saved_at NOT IN (
                SELECT saved_at FROM workspace_session_backups
                WHERE project_id = ? ORDER BY saved_at DESC LIMIT ?
            )
            """,
            [str(project_id), str(project_id), self.WORKSPACE_SESSION_BACKUPS_KEPT],
        )

    def list_workspace_session_backups(self, project_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT saved_at, turn_count FROM workspace_session_backups"
            " WHERE project_id = ? ORDER BY saved_at DESC",
            [str(project_id)],
        ).fetchall()
        return [{"saved_at": row[0], "turn_count": int(row[1] or 0)} for row in rows]

    def restore_workspace_session(self, project_id: str, saved_at: Any = None) -> dict[str, Any] | None:
        """Write a backup back into ``workspace_sessions`` (newest one when no timestamp)."""
        if saved_at is None:
            rows = self._execute(
                "SELECT payload FROM workspace_session_backups WHERE project_id = ?"
                " ORDER BY saved_at DESC LIMIT 1",
                [str(project_id)],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT payload FROM workspace_session_backups WHERE project_id = ? AND saved_at = ?"
                " ORDER BY saved_at DESC LIMIT 1",
                [str(project_id), saved_at],
            ).fetchall()
        if not rows:
            return None
        payload = rows[0][0]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except (ValueError, TypeError):
                return None
        if not isinstance(payload, dict):
            return None
        return self.save_workspace_session(project_id, payload, force=True)

    # ------------------------------------------------------------------ #
    # Deep-research (Tiefensuche) sessions                                 #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _decode_json(value: Any, default: Any) -> Any:
        """DuckDB JSON columns come back as str (or already-parsed) — normalise both."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return default
        return value if value is not None else default

    def upsert_research_session(
        self,
        session_id: str,
        project_id: str | None,
        question: str,
        status: str,
        nodes: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Create or update a deep-research tree (full node list incl. answers)."""
        now = datetime.now()
        self._execute("""
            INSERT INTO research_sessions
            (id, project_id, question, status, payload, created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (id) DO UPDATE SET
                project_id = EXCLUDED.project_id,
                question = EXCLUDED.question,
                status = EXCLUDED.status,
                payload = EXCLUDED.payload,
                updated_timestamp = EXCLUDED.updated_timestamp
        """, [
            str(session_id),
            str(project_id) if project_id else None,
            str(question or ""),
            str(status or "running"),
            json.dumps(nodes or []),
            now,
            now,
        ])
        return self.get_research_session(session_id) or {"id": str(session_id), "nodes": []}

    def get_research_session(self, session_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM research_sessions WHERE id = ?", [str(session_id)]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        record = dict(zip(cols, row))
        record["nodes"] = self._decode_json(record.pop("payload", None), [])
        return record

    def list_research_sessions(self, project_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        """List sessions for the sidebar — node counts only, not the heavy payload."""
        if project_id:
            rows = self._execute(
                "SELECT * FROM research_sessions WHERE project_id = ? "
                "ORDER BY updated_timestamp DESC LIMIT ?",
                [str(project_id), limit],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM research_sessions ORDER BY updated_timestamp DESC LIMIT ?",
                [limit],
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            nodes = self._decode_json(rec.pop("payload", None), [])
            nodes = nodes if isinstance(nodes, list) else []
            rec["node_count"] = len(nodes)
            rec["done_count"] = sum(1 for n in nodes if isinstance(n, dict) and n.get("status") == "done")
            rec["has_synthesis"] = any(isinstance(n, dict) and n.get("status") == "synthesis" for n in nodes)
            out.append(rec)
        return out

    def delete_research_session(self, session_id: str) -> bool:
        if self.get_research_session(session_id) is None:
            return False
        self._execute("DELETE FROM research_sessions WHERE id = ?", [str(session_id)])
        return True

    # ------------------------------------------------------------------ #
    # Parallel-Research sessions / variants / entries                     #
    # ------------------------------------------------------------------ #

    def _touch_parallel_session(self, session_id: str) -> None:
        self._execute(
            "UPDATE parallel_sessions SET updated_timestamp = ? WHERE id = ?",
            [datetime.now(), str(session_id)],
        )

    def create_parallel_session(
        self, project_id: str | None, question: str, session_id: str | None = None
    ) -> dict[str, Any]:
        now = datetime.now()
        sid = session_id or f"par_{uuid.uuid4().hex}"
        self._execute("""
            INSERT INTO parallel_sessions
            (id, project_id, question, status, synthesis_markdown, synthesis_payload,
             created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [sid, str(project_id) if project_id else None, str(question or ""),
              "active", None, None, now, now])
        return self.get_parallel_session(sid) or {"id": sid, "variants": []}

    def get_parallel_session(self, session_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM parallel_sessions WHERE id = ?", [str(session_id)]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        session = dict(zip(cols, row))
        session["synthesis_payload"] = self._decode_json(session.get("synthesis_payload"), None)
        session["overview_payload"] = self._decode_json(session.get("overview_payload"), None)
        session["stages"] = self.list_parallel_stages(session_id)
        variants = self.list_parallel_variants(session_id)
        entries_by_variant: dict[str, list[dict[str, Any]]] = {}
        for entry in self.list_parallel_entries(session_id=session_id):
            entries_by_variant.setdefault(str(entry.get("variant_id")), []).append(entry)
        for variant in variants:
            variant["entries"] = entries_by_variant.get(str(variant.get("id")), [])
        session["variants"] = variants
        session["followups"] = self.list_parallel_followups(session_id)
        return session

    def list_parallel_sessions(self, project_id: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        if project_id:
            rows = self._execute(
                "SELECT id, project_id, question, status, created_timestamp, updated_timestamp "
                "FROM parallel_sessions WHERE project_id = ? ORDER BY updated_timestamp DESC LIMIT ?",
                [str(project_id), limit],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT id, project_id, question, status, created_timestamp, updated_timestamp "
                "FROM parallel_sessions ORDER BY updated_timestamp DESC LIMIT ?",
                [limit],
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            count = self._execute(
                "SELECT COUNT(*) FROM parallel_variants WHERE session_id = ?", [rec["id"]]
            ).fetchone()
            rec["variant_count"] = int(count[0]) if count else 0
            stage_count = self._execute(
                "SELECT COUNT(*) FROM parallel_stages WHERE session_id = ?", [rec["id"]]
            ).fetchone()
            rec["stage_count"] = int(stage_count[0]) if stage_count else 0
            out.append(rec)
        return out

    def update_parallel_session(
        self,
        session_id: str,
        status: str | None = None,
        synthesis_markdown: str | None = None,
        synthesis_payload: dict[str, Any] | None = None,
        overview_markdown: str | None = None,
        overview_payload: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        current = self.get_parallel_session(session_id)
        if current is None:
            return None
        next_status = status if status is not None else current.get("status")
        next_md = synthesis_markdown if synthesis_markdown is not None else current.get("synthesis_markdown")
        next_payload = synthesis_payload if synthesis_payload is not None else current.get("synthesis_payload")
        next_overview_md = overview_markdown if overview_markdown is not None else current.get("overview_markdown")
        next_overview_payload = overview_payload if overview_payload is not None else current.get("overview_payload")
        self._execute("""
            UPDATE parallel_sessions
            SET status = ?, synthesis_markdown = ?, synthesis_payload = ?,
                overview_markdown = ?, overview_payload = ?, updated_timestamp = ?
            WHERE id = ?
        """, [next_status, next_md,
              json.dumps(next_payload) if next_payload is not None else None,
              next_overview_md,
              json.dumps(next_overview_payload) if next_overview_payload is not None else None,
              datetime.now(), str(session_id)])
        return self.get_parallel_session(session_id)

    def delete_parallel_session(self, session_id: str) -> bool:
        if self.get_parallel_session(session_id) is None:
            return False
        self._execute("DELETE FROM parallel_followups WHERE session_id = ?", [str(session_id)])
        self._execute("DELETE FROM parallel_entries WHERE session_id = ?", [str(session_id)])
        self._execute("DELETE FROM parallel_variants WHERE session_id = ?", [str(session_id)])
        self._execute("DELETE FROM parallel_stages WHERE session_id = ?", [str(session_id)])
        self._execute("DELETE FROM parallel_sessions WHERE id = ?", [str(session_id)])
        return True

    # ------------------------------------------------------------------ #
    # Parallel-Research stages (Etappen)                                  #
    # ------------------------------------------------------------------ #

    def add_parallel_stage(
        self,
        session_id: str,
        name: str,
        goal: str = "",
        status: str = "offen",
        position: int | None = None,
        stage_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = datetime.now()
        sid = stage_id or f"stg_{uuid.uuid4().hex}"
        if position is None:
            mx = self._execute(
                "SELECT COALESCE(MAX(position), -1) FROM parallel_stages WHERE session_id = ?",
                [str(session_id)],
            ).fetchone()
            position = (int(mx[0]) + 1) if mx else 0
        self._execute("""
            INSERT INTO parallel_stages
            (id, session_id, name, goal, status, position, created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, [sid, str(session_id), str(name or "Etappe"), str(goal or ""),
              str(status or "offen"), int(position), now, now])
        self._touch_parallel_session(session_id)
        return self.get_parallel_stage(sid)

    def get_parallel_stage(self, stage_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM parallel_stages WHERE id = ?", [str(stage_id)]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        stage = dict(zip(cols, row))
        stage["review_payload"] = self._decode_json(stage.get("review_payload"), None)
        return stage

    def list_parallel_stages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM parallel_stages WHERE session_id = ? "
            "ORDER BY position ASC, created_timestamp ASC",
            [str(session_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            rec["review_payload"] = self._decode_json(rec.get("review_payload"), None)
            out.append(rec)
        return out

    def update_parallel_stage(self, stage_id: str, **fields: Any) -> dict[str, Any] | None:
        current = self.get_parallel_stage(stage_id)
        if current is None:
            return None
        allowed = ("name", "goal", "status", "position", "review_markdown", "review_payload")
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return current
        if "review_payload" in updates:
            updates["review_payload"] = json.dumps(updates["review_payload"])
        set_clause = ", ".join(f"{k} = ?" for k in updates) + ", updated_timestamp = ?"
        params = list(updates.values()) + [datetime.now(), str(stage_id)]
        self._execute(f"UPDATE parallel_stages SET {set_clause} WHERE id = ?", params)
        self._touch_parallel_session(str(current.get("session_id")))
        return self.get_parallel_stage(stage_id)

    def delete_parallel_stage(self, stage_id: str) -> bool:
        """Delete a stage; its variants move to the session's first remaining stage.
        Raises ValueError for the session's last stage (a session always keeps one)."""
        current = self.get_parallel_stage(stage_id)
        if current is None:
            return False
        session_id = str(current.get("session_id"))
        remaining = [
            s for s in self.list_parallel_stages(session_id) if str(s.get("id")) != str(stage_id)
        ]
        if not remaining:
            raise ValueError("Letzte Etappe kann nicht gelöscht werden")
        target = str(remaining[0]["id"])
        self._execute(
            "UPDATE parallel_variants SET stage_id = ? WHERE stage_id = ?",
            [target, str(stage_id)],
        )
        self._execute("DELETE FROM parallel_stages WHERE id = ?", [str(stage_id)])
        self._touch_parallel_session(session_id)
        return True

    def add_parallel_variant(
        self,
        session_id: str,
        name: str,
        approach: str = "",
        rationale: str = "",
        suggested_prompt: str = "",
        origin: str = "ai",
        status: str = "vorgeschlagen",
        variant_id: str | None = None,
        position: int | None = None,
        stage_id: str | None = None,
    ) -> dict[str, Any] | None:
        now = datetime.now()
        vid = variant_id or f"var_{uuid.uuid4().hex}"
        if position is None:
            mx = self._execute(
                "SELECT COALESCE(MAX(position), -1) FROM parallel_variants WHERE session_id = ?",
                [str(session_id)],
            ).fetchone()
            position = (int(mx[0]) + 1) if mx else 0
        self._execute("""
            INSERT INTO parallel_variants
            (id, session_id, stage_id, name, approach, rationale, suggested_prompt, origin, status,
             position, created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [vid, str(session_id), str(stage_id) if stage_id else None,
              str(name or "Variante"), str(approach or ""),
              str(rationale or ""), str(suggested_prompt or ""), str(origin or "ai"),
              str(status or "vorgeschlagen"), int(position), now, now])
        self._touch_parallel_session(session_id)
        return self.get_parallel_variant(vid)

    def get_parallel_variant(self, variant_id: str) -> dict[str, Any] | None:
        row = self._execute("SELECT * FROM parallel_variants WHERE id = ?", [str(variant_id)]).fetchone()
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        variant = dict(zip(cols, row))
        variant["entries"] = self.list_parallel_entries(variant_id=variant_id)
        return variant

    def list_parallel_variants(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM parallel_variants WHERE session_id = ? "
            "ORDER BY position ASC, created_timestamp ASC",
            [str(session_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def update_parallel_variant(self, variant_id: str, **fields: Any) -> dict[str, Any] | None:
        current = self.get_parallel_variant(variant_id)
        if current is None:
            return None
        allowed = ("name", "approach", "rationale", "suggested_prompt", "origin", "status", "position", "stage_id")
        updates = {k: v for k, v in fields.items() if k in allowed and v is not None}
        if not updates:
            return current
        set_clause = ", ".join(f"{k} = ?" for k in updates) + ", updated_timestamp = ?"
        params = list(updates.values()) + [datetime.now(), str(variant_id)]
        self._execute(f"UPDATE parallel_variants SET {set_clause} WHERE id = ?", params)
        self._touch_parallel_session(str(current.get("session_id")))
        return self.get_parallel_variant(variant_id)

    def delete_parallel_variant(self, variant_id: str) -> bool:
        current = self.get_parallel_variant(variant_id)
        if current is None:
            return False
        self._execute("DELETE FROM parallel_entries WHERE variant_id = ?", [str(variant_id)])
        self._execute("DELETE FROM parallel_variants WHERE id = ?", [str(variant_id)])
        self._touch_parallel_session(str(current.get("session_id")))
        return True

    def add_parallel_entry(
        self,
        variant_id: str,
        session_id: str,
        role: str,
        content: str = "",
        answer_payload: dict[str, Any] | None = None,
        entry_id: str | None = None,
    ) -> dict[str, Any]:
        now = datetime.now()
        eid = entry_id or f"pe_{uuid.uuid4().hex}"
        self._execute("""
            INSERT INTO parallel_entries
            (id, variant_id, session_id, role, content, answer_payload, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, [eid, str(variant_id), str(session_id), str(role or "user"), str(content or ""),
              json.dumps(answer_payload) if answer_payload is not None else None, now])
        self._touch_parallel_session(session_id)
        row = self._execute("SELECT * FROM parallel_entries WHERE id = ?", [eid]).fetchone()
        cols = [desc[0] for desc in self.conn.description]
        rec = dict(zip(cols, row))
        rec["answer_payload"] = self._decode_json(rec.get("answer_payload"), None)
        return rec

    def list_parallel_entries(
        self, session_id: str | None = None, variant_id: str | None = None
    ) -> list[dict[str, Any]]:
        if variant_id:
            rows = self._execute(
                "SELECT * FROM parallel_entries WHERE variant_id = ? ORDER BY created_timestamp ASC",
                [str(variant_id)],
            ).fetchall()
        else:
            rows = self._execute(
                "SELECT * FROM parallel_entries WHERE session_id = ? ORDER BY created_timestamp ASC",
                [str(session_id)],
            ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            rec["answer_payload"] = self._decode_json(rec.get("answer_payload"), None)
            out.append(rec)
        return out

    def delete_parallel_entry(self, entry_id: str) -> bool:
        row = self._execute(
            "SELECT session_id FROM parallel_entries WHERE id = ?", [str(entry_id)]
        ).fetchone()
        if row is None:
            return False
        self._execute("DELETE FROM parallel_entries WHERE id = ?", [str(entry_id)])
        self._touch_parallel_session(str(row[0]))
        return True

    def add_parallel_followup(
        self,
        session_id: str,
        question: str,
        answer_payload: dict[str, Any] | None = None,
        followup_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist a follow-up question (grounded chat answer) asked while the session is open."""
        now = datetime.now()
        fid = followup_id or f"pf_{uuid.uuid4().hex}"
        self._execute("""
            INSERT INTO parallel_followups
            (id, session_id, question, answer_payload, created_timestamp)
            VALUES (?, ?, ?, ?, ?)
        """, [fid, str(session_id), str(question or ""),
              json.dumps(answer_payload) if answer_payload is not None else None, now])
        self._touch_parallel_session(session_id)
        return self._followup_row(fid)

    def _followup_row(self, followup_id: str) -> dict[str, Any]:
        row = self._execute(
            "SELECT * FROM parallel_followups WHERE id = ?", [str(followup_id)]
        ).fetchone()
        cols = [desc[0] for desc in self.conn.description]
        rec = dict(zip(cols, row))
        rec["answer_payload"] = self._decode_json(rec.get("answer_payload"), None)
        return rec

    def list_parallel_followups(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM parallel_followups WHERE session_id = ? ORDER BY created_timestamp ASC",
            [str(session_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        out: list[dict[str, Any]] = []
        for row in rows:
            rec = dict(zip(cols, row))
            rec["answer_payload"] = self._decode_json(rec.get("answer_payload"), None)
            out.append(rec)
        return out

    def delete_parallel_followup(self, followup_id: str) -> bool:
        row = self._execute(
            "SELECT session_id FROM parallel_followups WHERE id = ?", [str(followup_id)]
        ).fetchone()
        if row is None:
            return False
        self._execute("DELETE FROM parallel_followups WHERE id = ?", [str(followup_id)])
        self._touch_parallel_session(str(row[0]))
        return True

    # ------------------------------------------------------------------ #
    # Code-Werkstatt projects (registered coding-project folders on disk) #
    # ------------------------------------------------------------------ #
