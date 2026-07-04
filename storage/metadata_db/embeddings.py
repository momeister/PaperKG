from __future__ import annotations

import json
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class EmbeddingsMixin(_Base):
    """MetadataDB embeddings operations (mixin)."""

    @staticmethod
    def _normalize_embedding_label(label: str) -> str:
        return " ".join(label.lower().split())

    def upsert_entity_embedding(
        self,
        label: str,
        vector: list[float],
        model: str,
        backend: str,
        dimension: int,
        embedding_version: int = 1,
    ) -> None:
        """
        Persist a normalized entity embedding for reuse across batch runs.
        """
        now = datetime.now()
        self._execute("""
            INSERT INTO entity_embeddings
            (label_norm, label, model, backend, dimension, embedding_version, vector, updated_timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (label_norm, model, embedding_version) DO UPDATE SET
                label = EXCLUDED.label,
                backend = EXCLUDED.backend,
                dimension = EXCLUDED.dimension,
                vector = EXCLUDED.vector,
                updated_timestamp = EXCLUDED.updated_timestamp
        """, [
            self._normalize_embedding_label(label),
            label,
            model,
            backend,
            int(dimension),
            int(embedding_version),
            json.dumps(vector),
            now,
        ])

    def get_entity_embedding(
        self,
        label: str,
        model: str,
        embedding_version: int = 1,
    ) -> dict[str, Any] | None:
        result = self._execute("""
            SELECT * FROM entity_embeddings
            WHERE label_norm = ? AND model = ? AND embedding_version = ?
        """, [self._normalize_embedding_label(label), model, int(embedding_version)]).fetchone()
        if result is None:
            return None
        cols = [desc[0] for desc in self.conn.description]
        data = dict(zip(cols, result))
        data["vector"] = json.loads(data["vector"])
        return data

    def list_entity_embeddings(self, model: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        if model is None:
            results = self._execute("""
                SELECT * FROM entity_embeddings
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        else:
            results = self._execute("""
                SELECT * FROM entity_embeddings
                WHERE model = ?
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [model, limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        for row in results:
            data = dict(zip(cols, row))
            data["vector"] = json.loads(data["vector"])
            data_list.append(data)
        return data_list
