from __future__ import annotations

import json
import re
from datetime import datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class ExtractionMixin(_Base):
    """MetadataDB extraction operations (mixin)."""

    def save_extraction_result(
        self,
        paper_id: str,
        llm_provider: str,
        llm_model: str,
        paper_type: str | None = None,
        concepts: list[dict[str, Any]] | None = None,
        methods: list[dict[str, Any]] | None = None,
        concept_candidates: list[dict[str, Any]] | None = None,
        method_candidates: list[dict[str, Any]] | None = None,
        relations: list[dict[str, Any]] | None = None,
        claims: list[dict[str, Any]] | None = None,
        cross_domain_hints: list[dict[str, Any]] | None = None,
        terminology_conflicts: list[dict[str, Any]] | None = None,
        temporal_coverage: dict[str, Any] | None = None,
        mathematical_content: dict[str, Any] | None = None,
        raw_response: str | None = None,
        error_message: str | None = None,
        duration_seconds: float | None = None,
    ) -> int:
        """
        Save extraction results to database. Returns the result ID.
        """
        if error_message is None:
            error_message = self._infer_extraction_error_message(raw_response)
        status = "success" if error_message is None else "failed"
        
        result_id = self._execute("""
            INSERT INTO extraction_results
            (paper_id, llm_provider, llm_model, extraction_status, paper_type, concepts, methods,
             concept_candidates, method_candidates, relations, claims,
             cross_domain_hints, terminology_conflicts, temporal_coverage, mathematical_content,
             raw_response, error_message, extraction_duration_seconds)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            RETURNING id
        """, [
            paper_id,
            llm_provider,
            llm_model,
            status,
            paper_type,
            json.dumps(concepts or []),
            json.dumps(methods or []),
            json.dumps(concept_candidates or []),
            json.dumps(method_candidates or []),
            json.dumps(relations or []),
            json.dumps(claims or []),
            json.dumps(cross_domain_hints or []),
            json.dumps(terminology_conflicts or []),
            json.dumps(temporal_coverage or {}),
            json.dumps(mathematical_content or {}),
            raw_response,
            error_message,
            duration_seconds,
        ]).fetchone()

        if status == "success":
            self.enqueue_pending_entities(
                paper_id=paper_id,
                entities=list(concepts or []) + list(methods or []) + list(concept_candidates or []) + list(method_candidates or []),
            )

        return int(result_id[0]) if result_id else 0

    @staticmethod
    def _infer_extraction_error_message(raw_response: str | None) -> str | None:
        """Infer failed status from extraction payloads that carry fatal diagnostics."""
        if not raw_response:
            return None
        try:
            payload = json.loads(raw_response)
        except (TypeError, json.JSONDecodeError):
            return None
        if not isinstance(payload, dict):
            return None

        reason = str(payload.get("failure_reason") or "").strip()
        if payload.get("fatal_llm_error"):
            return reason or "LLM extraction failed before usable JSON could be produced."

        parse_quality = payload.get("extraction_parse_quality") or payload.get("parse_quality")
        if parse_quality != "failed":
            return None
        calls = [
            call
            for call in (payload.get("call_diagnostics") or payload.get("calls") or [])
            if isinstance(call, dict) and str(call.get("call_type") or "") != "claims_retry"
        ]
        if calls and all(str(call.get("parse_quality") or "") == "failed" for call in calls):
            excerpts = " ".join(str(call.get("raw_excerpt") or "") for call in calls)
            if "No models loaded" in excerpts:
                return "LLM extraction failed: LM Studio has no model loaded."
            concepts = payload.get("concepts") or []
            methods = payload.get("methods") or []
            if concepts or methods:
                return None
            return "LLM extraction failed for every extraction call; no KG-safe entities were produced."
        return None

    def enqueue_pending_entities(
        self,
        paper_id: str,
        entities: list[dict[str, Any]],
    ) -> int:
        """Persist pending entity review items for later approval/merge."""
        inserted = 0
        now = datetime.now()
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            if str(entity.get("review_status") or "").lower() != "pending":
                continue
            label = str(entity.get("label") or "").strip()
            if not label:
                continue
            self._execute("""
                INSERT INTO entity_review_queue
                (
                    paper_id, label, entity_type, canonical_id, suggested_canonical,
                    review_status, evidence, merge_candidates, source_field,
                    created_timestamp, updated_timestamp
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, [
                paper_id,
                label,
                entity.get("entity_type"),
                entity.get("canonical_id"),
                entity.get("suggested_canonical") or entity.get("canonical_label") or label,
                "pending",
                entity.get("evidence") or entity.get("evidence_span") or entity.get("context") or entity.get("description") or "",
                json.dumps(entity.get("merge_candidates") or []),
                entity.get("candidate_reason") or entity.get("acceptance_reason") or "",
                now,
                now,
            ])
            inserted += 1
        return inserted

    def save_extraction_quality(
        self,
        paper_id: str,
        concept_count: int,
        method_count: int,
        claim_count: int,
        has_formulas: bool,
        auto_detected_concepts: int,
        parse_quality: str,
        call_1_tokens_used: int | None = None,
        call_2_tokens_used: int | None = None,
        duration_seconds: float | None = None,
        model: str | None = None,
        provider: str | None = None,
        context_policy: str | None = None,
        whole_context_used: bool | None = None,
        chunk_count: int | None = None,
        estimated_prompt_tokens: int | None = None,
        context_margin_tokens: int | None = None,
        context_fallback_reason: str | None = None,
    ) -> None:
        """
        Persist quality telemetry for one extraction run.

        This table is intentionally append-only so quality trends can be
        inspected after prompt, parser, or model changes.
        """
        self._execute("""
            INSERT INTO extraction_quality
            (
                paper_id, concept_count, method_count, claim_count, has_formulas,
                auto_detected_concepts, parse_quality, call_1_tokens_used,
                call_2_tokens_used, duration_seconds, model, provider,
                context_policy, whole_context_used, chunk_count,
                estimated_prompt_tokens, context_margin_tokens,
                context_fallback_reason, timestamp
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            paper_id,
            int(concept_count),
            int(method_count),
            int(claim_count),
            bool(has_formulas),
            int(auto_detected_concepts),
            parse_quality,
            call_1_tokens_used,
            call_2_tokens_used,
            duration_seconds,
            model,
            provider,
            context_policy,
            bool(whole_context_used) if whole_context_used is not None else False,
            chunk_count,
            estimated_prompt_tokens,
            context_margin_tokens,
            context_fallback_reason,
            datetime.now(),
        ])

    def list_extraction_quality(
        self,
        paper_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        List recent extraction quality telemetry rows.
        """
        if paper_id is None:
            rows = self._execute("""
                SELECT * FROM extraction_quality
                ORDER BY timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        else:
            rows = self._execute("""
                SELECT * FROM extraction_quality
                WHERE paper_id = ?
                ORDER BY timestamp DESC
                LIMIT ?
            """, [paper_id, limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        return [dict(zip(cols, row)) for row in rows]

    def get_extraction_result(self, result_id: int) -> dict[str, Any] | None:
        """
        Retrieve an extraction result by ID.
        """
        result = self._execute(
            "SELECT * FROM extraction_results WHERE id = ?",
            [result_id]
        ).fetchone()
        
        if result is None:
            return None
        
        cols = [desc[0] for desc in self.conn.description]
        data = dict(zip(cols, result))
        
        # Parse JSON fields
        for field in self.EXTRACTION_JSON_FIELDS:
            if data.get(field):
                try:
                    data[field] = json.loads(data[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        
        return data

    def get_paper_extractions(self, paper_id: str, limit: int = 10) -> list[dict[str, Any]]:
        """
        Get all extraction results for a specific paper.
        """
        aliases = {paper_id}
        resolved = self.resolve_paper(paper_id)
        if resolved is not None:
            canonical_id = str(resolved.get("id") or paper_id)
            aliases.add(canonical_id)
            raw_aliases = {
                str(resolved.get("id") or ""),
                str(resolved.get("source_id") or ""),
                str(resolved.get("doi") or ""),
            }
            source = str(resolved.get("source") or "")
            source_id = str(resolved.get("source_id") or "")
            if source and source_id:
                raw_aliases.add(f"{source}:{source_id}")
                title_slug = self._slug(resolved.get("title") or "")
                if title_slug:
                    raw_aliases.add(f"{source}__{title_slug}__{source_id}")
            arxiv_id = self._extract_arxiv_id(" ".join(raw_aliases))
            if arxiv_id:
                bare_arxiv_id = re.sub(r"v\d+$", "", arxiv_id, flags=re.IGNORECASE)
                raw_aliases.add(f"arxiv:{bare_arxiv_id}")
            aliases.update(alias for alias in raw_aliases if alias)

        placeholders = ", ".join("?" for _ in aliases)
        results = self._execute(f"""
            SELECT * FROM extraction_results
            WHERE paper_id IN ({placeholders})
            ORDER BY extraction_timestamp DESC
            LIMIT ?
        """, [*aliases, limit]).fetchall()
        
        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        
        for row in results:
            data = dict(zip(cols, row))
            # Parse JSON fields
            for field in self.EXTRACTION_JSON_FIELDS:
                if data.get(field):
                    try:
                        data[field] = json.loads(data[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            data_list.append(data)
        
        return data_list

    def list_extraction_results(self, limit: int = 50) -> list[dict[str, Any]]:
        """
        List recent extraction results across all papers.
        """
        results = self._execute("""
            SELECT * FROM extraction_results
            ORDER BY extraction_timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()

        cols = [desc[0] for desc in self.conn.description]
        data_list = []
        for row in results:
            data = dict(zip(cols, row))
            for field in self.EXTRACTION_JSON_FIELDS:
                if data.get(field):
                    try:
                        data[field] = json.loads(data[field])
                    except (json.JSONDecodeError, TypeError):
                        pass
            data_list.append(data)
        return data_list

    def list_extraction_statuses(self, limit: int = 50000) -> list[dict[str, Any]]:
        """Newest-first (paper_id, extraction_status) pairs without the heavy JSON columns."""
        rows = self._execute("""
            SELECT paper_id, extraction_status FROM extraction_results
            ORDER BY extraction_timestamp DESC
            LIMIT ?
        """, [limit]).fetchall()
        return [{"paper_id": row[0], "extraction_status": row[1]} for row in rows]

    def list_entity_review_queue(
        self,
        status: str | None = "pending",
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List entity review queue items for approval/merge workflows."""
        if status is None:
            rows = self._execute("""
                SELECT * FROM entity_review_queue
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [limit]).fetchall()
        else:
            rows = self._execute("""
                SELECT * FROM entity_review_queue
                WHERE review_status = ?
                ORDER BY updated_timestamp DESC
                LIMIT ?
            """, [status, limit]).fetchall()
        cols = [desc[0] for desc in self.conn.description]
        output = []
        for row in rows:
            item = dict(zip(cols, row))
            try:
                item["merge_candidates"] = json.loads(item.get("merge_candidates") or "[]")
            except (TypeError, json.JSONDecodeError):
                item["merge_candidates"] = []
            output.append(item)
        return output

    def clear_extraction_results(self) -> None:
        """
        Delete stored extraction runs while keeping harvested paper metadata.
        """
        self._execute("DELETE FROM extraction_results")
        self._execute("DELETE FROM entity_review_queue")
