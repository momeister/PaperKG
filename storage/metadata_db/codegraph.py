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


class CodeGraphMixin(_Base):
    """MetadataDB code_indexes / code_paper_links / code_answers operations (mixin).

    Enthält nur die Buchführung. Der Graph selbst liegt in einer eigenen
    SQLite-Datei je Projekt und wird vom ``cs serve``-Kindprozess geschrieben —
    DuckDB verträgt genau einen Schreiber, und das ist dieser Prozess hier.
    """

    def _codegraph_row(self, row: Any) -> dict[str, Any] | None:
        if row is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        return dict(zip(cols, row))

    # --- code_indexes ------------------------------------------------------

    def get_code_index(self, code_project_id: str) -> dict[str, Any] | None:
        """Der (einzige) Indexeintrag eines Code-Projekts."""
        row = self._execute(
            "SELECT * FROM code_indexes WHERE code_project_id = ?",
            [str(code_project_id)],
        ).fetchone()
        record = self._codegraph_row(row)
        if record is not None and record.get("skipped_json"):
            try:
                record["skipped"] = json.loads(str(record["skipped_json"]))
            except (TypeError, ValueError):
                record["skipped"] = {}
        elif record is not None:
            record["skipped"] = {}
        return record

    def upsert_code_index(
        self,
        code_project_id: str,
        db_path: str,
        *,
        status: str = "pending",
        stats: dict[str, Any] | None = None,
        report: dict[str, Any] | None = None,
        error_message: str | None = None,
    ) -> dict[str, Any] | None:
        """Anlegen oder aktualisieren. Ein Projekt hat höchstens einen Index.

        ``stats``/``report`` sind genau die Rückgaben von ``cs serve`` — sie
        werden hier flach übernommen, damit kein zweites Vokabular entsteht.
        """
        stats = stats or {}
        report = report or {}
        now = datetime.now()
        existing = self.get_code_index(code_project_id)

        fields: dict[str, Any] = {
            "db_path": str(db_path),
            "status": str(status),
            "error_message": error_message,
            "updated_timestamp": now,
        }
        for key in (
            "files",
            "parsed_files",
            "nodes",
            "edges",
            "guessed_edges",
            "dynamic_gaps",
        ):
            if key in stats:
                fields[key] = int(stats[key] or 0)
        if "duration_ms" in report:
            fields["duration_ms"] = int(report.get("duration_ms") or 0)
        if "commits_walked" in report:
            fields["commits_walked"] = int(report.get("commits_walked") or 0)
        if "skipped" in report:
            fields["skipped_json"] = json.dumps(
                report.get("skipped") or {}, ensure_ascii=False
            )
        if status == "ready":
            fields["last_indexed_timestamp"] = now

        if existing is None:
            fields["id"] = f"ci_{uuid.uuid4().hex}"
            fields["code_project_id"] = str(code_project_id)
            fields["created_timestamp"] = now
            columns = ", ".join(fields)
            placeholders = ", ".join("?" for _ in fields)
            self._execute(
                f"INSERT INTO code_indexes ({columns}) VALUES ({placeholders})",
                list(fields.values()),
            )
        else:
            assignments = ", ".join(f"{name} = ?" for name in fields)
            self._execute(
                f"UPDATE code_indexes SET {assignments} WHERE code_project_id = ?",
                [*fields.values(), str(code_project_id)],
            )
        return self.get_code_index(code_project_id)

    def delete_code_index(self, code_project_id: str) -> bool:
        if self.get_code_index(code_project_id) is None:
            return False
        self._execute(
            "DELETE FROM code_indexes WHERE code_project_id = ?", [str(code_project_id)]
        )
        return True

    # --- code_paper_links --------------------------------------------------

    def add_code_paper_link(
        self,
        code_project_id: str,
        *,
        project_id: str | None = None,
        paper_id: str | None = None,
        symbol_id: str | None = None,
        rel_path: str | None = None,
        start_line: int | None = None,
        end_line: int | None = None,
        kind: str = "implements",
        note: str | None = None,
    ) -> dict[str, Any] | None:
        link_id = f"cpl_{uuid.uuid4().hex}"
        self._execute(
            """
            INSERT INTO code_paper_links
                (id, code_project_id, project_id, paper_id, symbol_id, rel_path,
                 start_line, end_line, kind, note, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                link_id,
                str(code_project_id),
                str(project_id) if project_id else None,
                str(paper_id) if paper_id else None,
                str(symbol_id) if symbol_id else None,
                str(rel_path) if rel_path else None,
                int(start_line) if start_line is not None else None,
                int(end_line) if end_line is not None else None,
                str(kind or "implements"),
                str(note) if note else None,
                datetime.now(),
            ],
        )
        return self.get_code_paper_link(link_id)

    def get_code_paper_link(self, link_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM code_paper_links WHERE id = ?", [str(link_id)]
        ).fetchone()
        return self._codegraph_row(row)

    def list_code_paper_links(
        self, code_project_id: str | None = None, project_id: str | None = None
    ) -> list[dict[str, Any]]:
        clauses, params = [], []
        if code_project_id:
            clauses.append("code_project_id = ?")
            params.append(str(code_project_id))
        if project_id:
            clauses.append("project_id = ?")
            params.append(str(project_id))
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        rows = self._execute(
            f"SELECT * FROM code_paper_links {where} ORDER BY created_timestamp DESC",
            params,
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def delete_code_paper_link(self, link_id: str) -> bool:
        if self.get_code_paper_link(link_id) is None:
            return False
        self._execute("DELETE FROM code_paper_links WHERE id = ?", [str(link_id)])
        return True

    # --- code_answers ------------------------------------------------------

    def add_code_answer(self, answer: dict[str, Any]) -> dict[str, Any] | None:
        answer_id = f"ca_{uuid.uuid4().hex}"
        self._execute(
            """
            INSERT INTO code_answers
                (id, code_project_id, project_id, question, answer, citations_json,
                 trail_json, verdict, tool_calls, provider, model, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                answer_id,
                str(answer.get("code_project_id") or ""),
                str(answer.get("project_id")) if answer.get("project_id") else None,
                str(answer.get("question") or ""),
                str(answer.get("answer") or ""),
                json.dumps(answer.get("citations") or [], ensure_ascii=False),
                json.dumps(answer.get("trail") or [], ensure_ascii=False),
                str(answer.get("verdict") or ""),
                int(answer.get("tool_calls") or 0),
                str(answer.get("provider")) if answer.get("provider") else None,
                str(answer.get("model")) if answer.get("model") else None,
                datetime.now(),
            ],
        )
        return self.get_code_answer(answer_id)

    def get_code_answer(self, answer_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM code_answers WHERE id = ?", [str(answer_id)]
        ).fetchone()
        record = self._codegraph_row(row)
        return _decode_answer(record)

    def list_code_answers(
        self, code_project_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            SELECT * FROM code_answers WHERE code_project_id = ?
            ORDER BY created_timestamp DESC LIMIT ?
            """,
            [str(code_project_id), int(limit)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [_decode_answer(dict(zip(cols, row))) or {} for row in rows]

    def delete_code_answer(self, answer_id: str) -> bool:
        if self.get_code_answer(answer_id) is None:
            return False
        self._execute("DELETE FROM code_answers WHERE id = ?", [str(answer_id)])
        return True

    # --- code_chats / code_chat_turns --------------------------------------

    def create_code_chat(
        self,
        code_project_id: str,
        *,
        project_id: str | None = None,
        title: str | None = None,
    ) -> dict[str, Any] | None:
        """Ein neues Gespräch. Die Sitzungskennung ist zugleich die Zitierlizenz."""
        chat_id = f"cc_{uuid.uuid4().hex}"
        self._execute(
            """
            INSERT INTO code_chats
                (id, code_project_id, project_id, title, session_key,
                 created_timestamp, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                chat_id,
                str(code_project_id),
                str(project_id) if project_id else None,
                str(title) if title else None,
                f"chat_{uuid.uuid4().hex[:16]}",
                datetime.now(),
                datetime.now(),
            ],
        )
        return self.get_code_chat(chat_id)

    def get_code_chat(self, chat_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT * FROM code_chats WHERE id = ?", [str(chat_id)]
        ).fetchone()
        return self._codegraph_row(row)

    def list_code_chats(
        self, code_project_id: str, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = self._execute(
            """
            SELECT * FROM code_chats WHERE code_project_id = ?
            ORDER BY updated_timestamp DESC LIMIT ?
            """,
            [str(code_project_id), int(limit)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def delete_code_chat(self, chat_id: str) -> bool:
        if self.get_code_chat(chat_id) is None:
            return False
        self._execute("DELETE FROM code_chat_turns WHERE chat_id = ?", [str(chat_id)])
        self._execute("DELETE FROM code_chats WHERE id = ?", [str(chat_id)])
        return True

    def add_code_chat_turn(
        self, chat_id: str, turn: dict[str, Any]
    ) -> dict[str, Any] | None:
        """Einen Zug anhängen. Die Reihenfolge wird hier vergeben, nicht vom Aufrufer."""
        row = self._execute(
            "SELECT coalesce(max(ordinal), -1) FROM code_chat_turns WHERE chat_id = ?",
            [str(chat_id)],
        ).fetchone()
        ordinal = int(row[0]) + 1 if row else 0

        turn_id = f"cct_{uuid.uuid4().hex}"
        self._execute(
            """
            INSERT INTO code_chat_turns
                (id, chat_id, ordinal, question, answer, citations_json, trail_json,
                 focus_nodes_json, papers_json, verdict, tool_calls, truncated,
                 provider, model, remote_model, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                turn_id,
                str(chat_id),
                ordinal,
                str(turn.get("question") or ""),
                str(turn.get("answer") or ""),
                json.dumps(turn.get("citations") or [], ensure_ascii=False),
                json.dumps(turn.get("trail") or [], ensure_ascii=False),
                json.dumps(turn.get("focus_nodes") or [], ensure_ascii=False),
                json.dumps(turn.get("papers") or [], ensure_ascii=False),
                str(turn.get("verdict") or ""),
                int(turn.get("tool_calls") or 0),
                bool(turn.get("truncated")),
                str(turn.get("provider")) if turn.get("provider") else None,
                str(turn.get("model")) if turn.get("model") else None,
                bool(turn.get("remote_model")),
                datetime.now(),
            ],
        )
        self._execute(
            "UPDATE code_chats SET updated_timestamp = ? WHERE id = ?",
            [datetime.now(), str(chat_id)],
        )
        # Der erste Zug gibt dem Gespräch seinen Namen — sonst hiessen in der
        # Liste alle gleich.
        if ordinal == 0:
            question = str(turn.get("question") or "").strip()
            if question:
                self._execute(
                    "UPDATE code_chats SET title = ? WHERE id = ? AND (title IS NULL OR title = '')",
                    [question[:120], str(chat_id)],
                )

        found = self._execute(
            "SELECT * FROM code_chat_turns WHERE id = ?", [turn_id]
        ).fetchone()
        return _decode_turn(self._codegraph_row(found))

    def list_code_chat_turns(self, chat_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT * FROM code_chat_turns WHERE chat_id = ? ORDER BY ordinal ASC",
            [str(chat_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [_decode_turn(dict(zip(cols, row))) or {} for row in rows]

    # --- code_cluster_labels -----------------------------------------------

    def get_cluster_labels(self, code_project_id: str) -> dict[str, dict[str, Any]]:
        """Alle hinterlegten Bereichsnamen eines Projekts, nach Pfad."""
        rows = self._execute(
            "SELECT * FROM code_cluster_labels WHERE code_project_id = ?",
            [str(code_project_id)],
        ).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return {
            str(record["cluster_path"]): record
            for record in (dict(zip(cols, row)) for row in rows)
        }

    def upsert_cluster_label(
        self,
        code_project_id: str,
        cluster_path: str,
        *,
        fingerprint: str,
        label: str,
        purpose: str | None = None,
        source: str = "llm",
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any] | None:
        """Einen Bereichsnamen setzen. Höchstens einer je Pfad und Projekt."""
        self._execute(
            "DELETE FROM code_cluster_labels WHERE code_project_id = ? AND cluster_path = ?",
            [str(code_project_id), str(cluster_path)],
        )
        label_id = f"ccl_{uuid.uuid4().hex}"
        self._execute(
            """
            INSERT INTO code_cluster_labels
                (id, code_project_id, cluster_path, fingerprint, label, purpose,
                 source, provider, model, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                label_id,
                str(code_project_id),
                str(cluster_path),
                str(fingerprint),
                str(label),
                str(purpose) if purpose else None,
                str(source),
                str(provider) if provider else None,
                str(model) if model else None,
                datetime.now(),
            ],
        )
        row = self._execute(
            "SELECT * FROM code_cluster_labels WHERE id = ?", [label_id]
        ).fetchone()
        return self._codegraph_row(row)

    def delete_cluster_labels(self, code_project_id: str) -> int:
        """Alle Namen eines Projekts verwerfen — etwa nach einem Neuaufbau."""
        existing = len(self.get_cluster_labels(code_project_id))
        self._execute(
            "DELETE FROM code_cluster_labels WHERE code_project_id = ?",
            [str(code_project_id)],
        )
        return existing

    # --- Selbst hinterlegte Begründungen -------------------------------------

    def add_code_rationale(
        self,
        code_project_id: str,
        *,
        rel_path: str,
        text: str,
        start_line: int | None = None,
        end_line: int | None = None,
        symbol_id: str | None = None,
        content_hash: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any] | None:
        """Eine Begründung festhalten — der Teil, der das Problem langfristig löst.

        Anders als die Bereichsnamen wird hier **nichts überschrieben**: zwei
        Menschen können zu derselben Funktion zwei verschiedene Dinge zu sagen
        haben, und die spätere Begründung entwertet die frühere nicht.
        """
        rationale_id = f"crat_{uuid.uuid4().hex}"
        self._execute(
            """
            INSERT INTO code_rationale
                (id, code_project_id, rel_path, start_line, end_line, symbol_id,
                 text, content_hash, author, created_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                rationale_id,
                str(code_project_id),
                str(rel_path),
                int(start_line) if start_line else None,
                int(end_line) if end_line else None,
                str(symbol_id) if symbol_id else None,
                str(text),
                str(content_hash) if content_hash else None,
                str(author) if author else None,
                datetime.now(),
            ],
        )
        row = self._execute(
            "SELECT * FROM code_rationale WHERE id = ?", [rationale_id]
        ).fetchone()
        return self._codegraph_row(row)

    def list_code_rationale(
        self,
        code_project_id: str,
        *,
        rel_path: str | None = None,
        symbol_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Begründungen zu einem Projekt, neueste zuerst.

        Nach ``rel_path`` *oder* ``symbol_id`` filterbar: eine Begründung
        überlebt ein Umbenennen der Funktion (die Symbol-ID ändert sich), und
        eine Begründung zur Datei gehört auch zu jedem Symbol darin.
        """
        clauses = ["code_project_id = ?"]
        params: list[Any] = [str(code_project_id)]
        if rel_path:
            clauses.append("rel_path = ?")
            params.append(str(rel_path))
        if symbol_id:
            clauses.append("symbol_id = ?")
            params.append(str(symbol_id))
        rows = self._execute(
            f"SELECT * FROM code_rationale WHERE {' AND '.join(clauses)} "
            "ORDER BY created_timestamp DESC LIMIT ?",
            [*params, max(1, int(limit))],
        ).fetchall()
        return [record for row in rows if (record := self._codegraph_row(row))]

    def delete_code_rationale(self, rationale_id: str) -> bool:
        existing = self._execute(
            "SELECT id FROM code_rationale WHERE id = ?", [str(rationale_id)]
        ).fetchone()
        if existing is None:
            return False
        self._execute("DELETE FROM code_rationale WHERE id = ?", [str(rationale_id)])
        return True

    # --- Checkpoints (Stufe 2) --------------------------------------------- #

    def _checkpoint_row(self, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row[0],
            "code_project_id": row[1],
            "ref_name": row[2],
            "commit_sha": row[3],
            "tree_sha": row[4],
            "parent_sha": row[5],
            "label": row[6],
            "reason": row[7],
            "file_count": row[8],
            "created_timestamp": (
                self._to_iso(row[9]) if hasattr(self, "_to_iso") else row[9]
            ),
        }

    def add_code_checkpoint(
        self,
        code_project_id: str,
        *,
        ref_name: str,
        commit_sha: str,
        tree_sha: str | None = None,
        parent_sha: str | None = None,
        label: str | None = None,
        reason: str = "manual",
        file_count: int = 0,
    ) -> dict[str, Any]:
        checkpoint_id = "ckp_" + uuid.uuid4().hex
        self._execute(
            """
            INSERT INTO code_checkpoints
                (id, code_project_id, ref_name, commit_sha, tree_sha, parent_sha,
                 label, reason, file_count)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            [
                checkpoint_id,
                str(code_project_id),
                str(ref_name),
                str(commit_sha),
                tree_sha,
                parent_sha,
                label,
                str(reason),
                int(file_count),
            ],
        )
        return self.get_code_checkpoint(checkpoint_id) or {
            "id": checkpoint_id,
            "code_project_id": code_project_id,
            "ref_name": ref_name,
            "commit_sha": commit_sha,
            "reason": reason,
        }

    def get_code_checkpoint(self, checkpoint_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT id, code_project_id, ref_name, commit_sha, tree_sha, parent_sha, "
            "label, reason, file_count, created_timestamp "
            "FROM code_checkpoints WHERE id = ?",
            [str(checkpoint_id)],
        ).fetchone()
        return self._checkpoint_row(row)

    def list_code_checkpoints(
        self, code_project_id: str, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT id, code_project_id, ref_name, commit_sha, tree_sha, parent_sha, "
            "label, reason, file_count, created_timestamp "
            "FROM code_checkpoints WHERE code_project_id = ? "
            "ORDER BY created_timestamp DESC LIMIT ?",
            [str(code_project_id), int(limit)],
        ).fetchall()
        return [record for row in rows if (record := self._checkpoint_row(row))]

    def delete_code_checkpoint(self, checkpoint_id: str) -> bool:
        existing = self._execute(
            "SELECT id FROM code_checkpoints WHERE id = ?", [str(checkpoint_id)]
        ).fetchone()
        if existing is None:
            return False
        self._execute("DELETE FROM code_checkpoints WHERE id = ?", [str(checkpoint_id)])
        return True

    def list_code_checkpoints_by_reason(
        self, code_project_id: str, reason: str, *, limit: int = 200
    ) -> list[dict[str, Any]]:
        """Aeltere *automatische* Checkpoints, die das Aufraeumen erfasst."""
        rows = self._execute(
            "SELECT id, code_project_id, ref_name, commit_sha, tree_sha, parent_sha, "
            "label, reason, file_count, created_timestamp "
            "FROM code_checkpoints WHERE code_project_id = ? AND reason = ? "
            "ORDER BY created_timestamp ASC LIMIT ?",
            [str(code_project_id), str(reason), int(limit)],
        ).fetchall()
        return [record for row in rows if (record := self._checkpoint_row(row))]

    # --- Sandboxen (Stufe 2) ---------------------------------------------- #

    def _sandbox_row(self, row: tuple[Any, ...] | None) -> dict[str, Any] | None:
        if row is None:
            return None
        return {
            "id": row[0],
            "code_project_id": row[1],
            "checkpoint_id": row[2],
            "base_sha": row[3],
            "path": row[4],
            "status": row[5],
            "test_command": row[6],
            "last_exit_code": row[7],
            "last_run_timestamp": row[8],
            "created_timestamp": row[9],
        }

    def add_code_sandbox(
        self,
        code_project_id: str,
        *,
        checkpoint_id: str | None = None,
        base_sha: str | None = None,
        path: str | None = None,
        status: str = "created",
        test_command: str | None = None,
    ) -> dict[str, Any]:
        sandbox_id = "sb_" + uuid.uuid4().hex
        self._execute(
            """
            INSERT INTO code_sandboxes
                (id, code_project_id, checkpoint_id, base_sha, path, status, test_command)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            [
                sandbox_id,
                str(code_project_id),
                checkpoint_id,
                base_sha,
                path,
                status,
                test_command,
            ],
        )
        return self.get_code_sandbox(sandbox_id) or {
            "id": sandbox_id,
            "code_project_id": code_project_id,
        }

    def get_code_sandbox(self, sandbox_id: str) -> dict[str, Any] | None:
        row = self._execute(
            "SELECT id, code_project_id, checkpoint_id, base_sha, path, status, "
            "test_command, last_exit_code, last_run_timestamp, created_timestamp "
            "FROM code_sandboxes WHERE id = ?",
            [str(sandbox_id)],
        ).fetchone()
        return self._sandbox_row(row)

    def list_code_sandboxes(self, code_project_id: str) -> list[dict[str, Any]]:
        rows = self._execute(
            "SELECT id, code_project_id, checkpoint_id, base_sha, path, status, "
            "test_command, last_exit_code, last_run_timestamp, created_timestamp "
            "FROM code_sandboxes WHERE code_project_id = ? "
            "ORDER BY created_timestamp DESC",
            [str(code_project_id)],
        ).fetchall()
        return [record for row in rows if (record := self._sandbox_row(row))]

    def update_code_sandbox(
        self,
        sandbox_id: str,
        *,
        status: str | None = None,
        last_exit_code: int | None = None,
        test_command: str | None = None,
    ) -> bool:
        sets: list[str] = []
        binds: list[Any] = []
        if status is not None:
            sets.append("status = ?")
            binds.append(status)
        if last_exit_code is not None:
            sets.append("last_exit_code = ?")
            binds.append(int(last_exit_code))
        if test_command is not None:
            sets.append("test_command = ?")
            binds.append(test_command)
        if not sets:
            return False
        sets.append("last_run_timestamp = CURRENT_TIMESTAMP")
        binds.append(str(sandbox_id))
        self._execute(
            f"UPDATE code_sandboxes SET {', '.join(sets)} WHERE id = ?", binds
        )
        return True

    def delete_code_sandbox(self, sandbox_id: str) -> bool:
        existing = self._execute(
            "SELECT id FROM code_sandboxes WHERE id = ?", [str(sandbox_id)]
        ).fetchone()
        if existing is None:
            return False
        self._execute("DELETE FROM code_sandboxes WHERE id = ?", [str(sandbox_id)])
        return True


def _decode_turn(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """Wie ``_decode_answer``, nur mit den zwei zusätzlichen Listen des Chats."""
    if record is None:
        return None
    for column, target in (
        ("citations_json", "citations"),
        ("trail_json", "trail"),
        ("focus_nodes_json", "focus_nodes"),
        ("papers_json", "papers"),
    ):
        try:
            record[target] = json.loads(str(record.get(column) or "[]"))
        except (TypeError, ValueError):
            record[target] = []
    return record


def _decode_answer(record: dict[str, Any] | None) -> dict[str, Any] | None:
    """JSON-Spalten aufklappen, damit der Router nichts nachparsen muss."""
    if record is None:
        return None
    for column, target in (("citations_json", "citations"), ("trail_json", "trail")):
        try:
            record[target] = json.loads(str(record.get(column) or "[]"))
        except (TypeError, ValueError):
            record[target] = []
    return record
