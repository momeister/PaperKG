from __future__ import annotations

import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from storage.metadata_db import MetadataDB

    _Base = MetadataDB
else:
    _Base = object


class SchemaMixin(_Base):
    """MetadataDB schema operations (mixin)."""

    def _init_schema(self) -> None:
        """
        Initialize all required tables if they don't exist.
        """
        self._execute("CREATE SEQUENCE IF NOT EXISTS seq_dedup_id")

        self._execute("""
            CREATE TABLE IF NOT EXISTS papers (
                id VARCHAR PRIMARY KEY,
                source VARCHAR NOT NULL,
                source_id VARCHAR NOT NULL,
                title VARCHAR,
                abstract VARCHAR,
                authors JSON,
                year INTEGER,
                doi VARCHAR,
                pdf_url VARCHAR,
                landing_page_url VARCHAR,
                "references" JSON,
                citations JSON,
                citation_count INTEGER DEFAULT 0,
                superseded_by VARCHAR,
                peer_reviewed BOOLEAN DEFAULT false,
                retracted BOOLEAN DEFAULT false,
                language_original VARCHAR DEFAULT 'unknown',
                confidence_score FLOAT DEFAULT 0.5,
                obsolescence_score FLOAT DEFAULT 0.0,
                conflict_flag BOOLEAN DEFAULT false,
                embedding_model VARCHAR,
                embedding_version INTEGER DEFAULT 0,
                has_full_text BOOLEAN DEFAULT false,
                version INTEGER DEFAULT 1,
                added_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._migrate_schema()

        self._execute("""
            CREATE TABLE IF NOT EXISTS paper_sources (
                paper_id VARCHAR NOT NULL,
                source VARCHAR NOT NULL,
                source_id VARCHAR,
                source_url VARCHAR,
                added_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (paper_id, source)
            )
        """)

    def _migrate_schema(self) -> None:
        """
        Add Phase 2/3 columns when opening older DuckDB files.
        """
        columns = {
            "references": "JSON",
            "citations": "JSON",
            "citation_count": "INTEGER DEFAULT 0",
            "superseded_by": "VARCHAR",
            "peer_reviewed": "BOOLEAN DEFAULT false",
            "retracted": "BOOLEAN DEFAULT false",
            "language_original": "VARCHAR DEFAULT 'unknown'",
            "confidence_score": "FLOAT DEFAULT 0.5",
            "obsolescence_score": "FLOAT DEFAULT 0.0",
            "conflict_flag": "BOOLEAN DEFAULT false",
            "embedding_model": "VARCHAR",
            "embedding_version": "INTEGER DEFAULT 0",
        }
        self._add_missing_columns("papers", columns, quoted_names={"references"})

        self._execute("""
            CREATE TABLE IF NOT EXISTS dedup_log (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_dedup_id'),
                kept_id VARCHAR NOT NULL,
                dropped_id VARCHAR NOT NULL,
                reason VARCHAR,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS extraction_results (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_dedup_id'),
                paper_id VARCHAR NOT NULL,
                extraction_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                llm_provider VARCHAR NOT NULL,
                llm_model VARCHAR NOT NULL,
                extraction_status VARCHAR DEFAULT 'pending',
                paper_type VARCHAR,
                concepts JSON,
                methods JSON,
                claims JSON,
                cross_domain_hints JSON,
                terminology_conflicts JSON,
                temporal_coverage JSON,
                mathematical_content JSON,
                raw_response VARCHAR,
                error_message VARCHAR,
                extraction_duration_seconds FLOAT
            )
        """)
        extraction_columns = {
            "paper_type": "VARCHAR",
            "concept_candidates": "JSON",
            "method_candidates": "JSON",
            "relations": "JSON",
            "terminology_conflicts": "JSON",
            "temporal_coverage": "JSON",
            "mathematical_content": "JSON",
        }
        self._add_missing_columns("extraction_results", extraction_columns)

        self._execute("""
            CREATE TABLE IF NOT EXISTS batch_jobs (
                job_id VARCHAR PRIMARY KEY,
                status VARCHAR NOT NULL,
                papers_total INTEGER DEFAULT 0,
                papers_processed INTEGER DEFAULT 0,
                papers_failed INTEGER DEFAULT 0,
                error_message VARCHAR,
                request_payload JSON,
                llm_provider VARCHAR,
                superseded_by VARCHAR,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS batch_job_items (
                job_id VARCHAR NOT NULL,
                paper_id VARCHAR NOT NULL,
                pdf_path VARCHAR,
                status VARCHAR NOT NULL DEFAULT 'pending',
                attempts INTEGER DEFAULT 0,
                error_message VARCHAR,
                started_timestamp TIMESTAMP,
                completed_timestamp TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (job_id, paper_id)
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS entity_embeddings (
                label_norm VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                model VARCHAR NOT NULL,
                backend VARCHAR NOT NULL,
                dimension INTEGER NOT NULL,
                embedding_version INTEGER DEFAULT 1,
                vector JSON NOT NULL,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (label_norm, model, embedding_version)
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS extraction_quality (
                paper_id TEXT NOT NULL,
                concept_count INTEGER NOT NULL,
                method_count INTEGER NOT NULL,
                claim_count INTEGER NOT NULL,
                has_formulas BOOLEAN NOT NULL,
                auto_detected_concepts INTEGER NOT NULL,
                parse_quality TEXT NOT NULL,
                call_1_tokens_used INTEGER,
                call_2_tokens_used INTEGER,
                duration_seconds FLOAT,
                model TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._add_missing_columns(
            "extraction_quality",
            {
                "provider": "TEXT",
                "context_policy": "TEXT",
                "whole_context_used": "BOOLEAN DEFAULT false",
                "chunk_count": "INTEGER",
                "estimated_prompt_tokens": "INTEGER",
                "context_margin_tokens": "INTEGER",
                "context_fallback_reason": "TEXT",
            },
        )

        self._execute("""
            CREATE TABLE IF NOT EXISTS entity_review_queue (
                id INTEGER PRIMARY KEY DEFAULT nextval('seq_dedup_id'),
                paper_id VARCHAR NOT NULL,
                label VARCHAR NOT NULL,
                entity_type VARCHAR,
                canonical_id VARCHAR,
                suggested_canonical VARCHAR,
                review_status VARCHAR DEFAULT 'pending',
                evidence VARCHAR,
                merge_candidates JSON,
                source_field VARCHAR,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS notes (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                title VARCHAR NOT NULL,
                markdown TEXT NOT NULL,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS note_citations (
                id VARCHAR PRIMARY KEY,
                note_id VARCHAR NOT NULL,
                paper_id VARCHAR NOT NULL,
                title VARCHAR,
                kind VARCHAR,
                reference_text TEXT,
                pdf_excerpt TEXT,
                evidence_id VARCHAR,
                evidence_index INTEGER DEFAULT 0,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._add_missing_columns(
            "note_citations",
            {
                "evidence_id": "VARCHAR",
            },
        )

        self._execute("""
            CREATE TABLE IF NOT EXISTS note_assets (
                id VARCHAR PRIMARY KEY,
                note_id VARCHAR NOT NULL,
                filename VARCHAR NOT NULL,
                content_type VARCHAR,
                asset_path VARCHAR NOT NULL,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS note_ai_threads (
                id VARCHAR PRIMARY KEY,
                note_id VARCHAR NOT NULL,
                selected_text TEXT,
                instruction TEXT NOT NULL,
                response_text TEXT NOT NULL,
                replacement_text TEXT,
                answer_payload JSON,
                anchor_start INTEGER,
                anchor_end INTEGER,
                anchor_quote TEXT,
                ui_state JSON,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._add_missing_columns(
            "note_ai_threads",
            {
                "anchor_start": "INTEGER",
                "anchor_end": "INTEGER",
                "anchor_quote": "TEXT",
                "ui_state": "JSON",
                "updated_timestamp": "TIMESTAMP DEFAULT CURRENT_TIMESTAMP",
            },
        )

        self._execute("""
            CREATE TABLE IF NOT EXISTS note_ai_messages (
                id VARCHAR PRIMARY KEY,
                thread_id VARCHAR NOT NULL,
                note_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                content TEXT NOT NULL,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("""
            CREATE TABLE IF NOT EXISTS note_versions (
                id VARCHAR PRIMARY KEY,
                note_id VARCHAR NOT NULL,
                markdown TEXT NOT NULL,
                reason VARCHAR,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Grey (web) sources from deep research. Deliberately kept OUT of the
        # knowledge graph: project-scoped supplementary context only.
        self._execute("""
            CREATE TABLE IF NOT EXISTS grey_sources (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR NOT NULL,
                query TEXT,
                url VARCHAR NOT NULL,
                title VARCHAR,
                summary TEXT,
                raw_excerpt TEXT,
                full_text TEXT,
                evidence JSON,
                injection_flags JSON,
                status VARCHAR DEFAULT 'saved',
                source_kind VARCHAR DEFAULT 'web',
                origin_id VARCHAR,
                source_paper_ids JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Migrate older grey_sources tables that predate full-article capture, and the
        # later "internal sources" kinds: notes and deep-analysis syntheses are stored in
        # the same table so they are citable as ``grey::…`` without a second pipeline.
        # ``source_kind`` is web | note | analysis, ``origin_id`` points back to the note /
        # analysis, ``source_paper_ids`` holds the papers that source itself was built from.
        self._add_missing_columns(
            "grey_sources",
            {
                "full_text": "TEXT",
                "evidence": "JSON",
                "source_kind": "VARCHAR",
                "origin_id": "VARCHAR",
                "source_paper_ids": "JSON",
                # trusted | unknown — Domain-Stufe der Auto-Recherche
                # (research/source_tiers.py). Beeinflusst Ranking und Prompt.
                "trust_tier": "VARCHAR",
            },
        )
        self._execute("CREATE INDEX IF NOT EXISTS idx_grey_sources_project ON grey_sources(project_id)")

        self._execute("""
            CREATE TABLE IF NOT EXISTS benchmark_runs (
                id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                provider VARCHAR,
                model VARCHAR,
                summary JSON,
                report JSON,
                duration_ms INTEGER,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Workspace assistant sessions (chat history incl. verification payloads).
        # Persisted server-side because the payloads routinely exceed the browser's
        # localStorage quota, which silently dropped sessions on reload.
        self._execute("""
            CREATE TABLE IF NOT EXISTS workspace_sessions (
                project_id VARCHAR PRIMARY KEY,
                payload JSON,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Rollierende Sicherungen der Workspace-Sessions. `workspace_sessions` hat genau
        # eine Zeile pro Projekt und wurde bedingungslos ueberschrieben — ein Client, der
        # (z. B. nach einem fehlgeschlagenen GET beim Start) eine leere History schickte,
        # loeschte die Unterhaltung unwiederbringlich. Jeder Schreibvorgang legt jetzt
        # vorher den bisherigen Stand hier ab; die letzten Staende bleiben erhalten.
        self._execute("""
            CREATE TABLE IF NOT EXISTS workspace_session_backups (
                project_id VARCHAR,
                saved_at TIMESTAMP,
                payload JSON,
                turn_count INTEGER
            )
        """)

        # Deep-research (Tiefensuche) trees, persisted server-side *during* the run so the
        # KI-Session is never empty on reload. The streaming endpoint upserts the full node
        # list (incl. answers/verification) as the tree grows; the frontend hydrates from here.
        self._execute("""
            CREATE TABLE IF NOT EXISTS research_sessions (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR,
                question VARCHAR,
                status VARCHAR,
                payload JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Parallel-Research mode: the AI proposes (and the user adds) several "Varianten"
        # for a question; the user feeds back results per variant, the AI comments, and a
        # final synthesis ranks them. Durable, sub-divided, editable — own tables per the
        # "new feature ⇒ new table" convention.
        self._execute("""
            CREATE TABLE IF NOT EXISTS parallel_sessions (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR,
                question VARCHAR,
                status VARCHAR,
                overview_markdown VARCHAR,
                overview_payload JSON,
                synthesis_markdown VARCHAR,
                synthesis_payload JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Older DBs predate the upfront "overview" (task explanation + how-to) — backfill.
        self._add_missing_columns(
            "parallel_sessions",
            {"overview_markdown": "VARCHAR", "overview_payload": "JSON"},
        )
        self._execute("""
            CREATE TABLE IF NOT EXISTS parallel_variants (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR,
                name VARCHAR,
                approach VARCHAR,
                rationale VARCHAR,
                suggested_prompt VARCHAR,
                origin VARCHAR,
                status VARCHAR,
                position INTEGER,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Etappen: ein Forschungsvorhaben (= Session) gliedert sich in sequentielle
        # Stages; Varianten hängen an einer Stage. review_* trägt den Professor-
        # Etappen-Review (strukturierte Kritik über alle Ergebnisse der Etappe).
        self._execute("""
            CREATE TABLE IF NOT EXISTS parallel_stages (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR,
                name VARCHAR,
                goal VARCHAR,
                status VARCHAR,
                position INTEGER,
                review_markdown VARCHAR,
                review_payload JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Older DBs predate stages — add the FK column and adopt orphan variants below.
        self._add_missing_columns("parallel_variants", {"stage_id": "VARCHAR"})
        self._backfill_parallel_stages()
        self._execute("""
            CREATE TABLE IF NOT EXISTS parallel_entries (
                id VARCHAR PRIMARY KEY,
                variant_id VARCHAR,
                session_id VARCHAR,
                role VARCHAR,
                content VARCHAR,
                answer_payload JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Follow-up questions asked while a parallel session is open: a grounded chat thread
        # shown under the overview (own table — they're session-scoped, not variant-scoped).
        self._execute("""
            CREATE TABLE IF NOT EXISTS parallel_followups (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR,
                question VARCHAR,
                answer_payload JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Desktop-Companion / Selbst-Steuerung (R7): durable chat + step log so the
        # overlay can list, reopen and continue sessions across app restarts. ``kind``
        # discriminates the two modes; the in-flight planner state (history, pending
        # expectation) stays in the in-memory stores — only the transcript persists.
        self._execute("""
            CREATE TABLE IF NOT EXISTS companion_sessions (
                id VARCHAR PRIMARY KEY,
                kind VARCHAR NOT NULL,
                title VARCHAR,
                goal VARCHAR,
                status VARCHAR DEFAULT 'active',
                provider VARCHAR,
                model VARCHAR,
                monitor INTEGER,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS companion_messages (
                id VARCHAR PRIMARY KEY,
                session_id VARCHAR NOT NULL,
                role VARCHAR NOT NULL,
                content VARCHAR,
                payload JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        # Code-Werkstatt: registered coding-project folders on disk. ``kind`` is
        # 'managed' (created + git-init'd by us under the workspaces base dir) or
        # 'external' (an existing folder the user opened). Only the *registration*
        # lives here; the files stay on disk so other editors can open them too.
        self._execute("""
            CREATE TABLE IF NOT EXISTS code_projects (
                id VARCHAR PRIMARY KEY,
                name VARCHAR NOT NULL,
                path VARCHAR NOT NULL,
                kind VARCHAR DEFAULT 'managed',
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Analyse-Werkstatt (WP1): ein Lauf = ein Provenance-Ordner auf der Platte
        # (script.py/inputs/outputs/run.json). Hier liegt nur die Registrierung +
        # Metadaten; die Dateien bleiben im verwalteten Werkstatt-Projekt (git).
        self._execute("""
            CREATE TABLE IF NOT EXISTS analysis_runs (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR,
                code_project_id VARCHAR,
                run_dir VARCHAR NOT NULL,
                rel_dir VARCHAR,
                title VARCHAR,
                description TEXT,
                request TEXT,
                script_rel VARCHAR,
                status VARCHAR DEFAULT 'ok',
                provider VARCHAR,
                model VARCHAR,
                seed INTEGER,
                output_hash VARCHAR,
                verified_hash VARCHAR,
                stdout TEXT,
                stderr TEXT,
                duration_s FLOAT,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        self._execute("""
            CREATE TABLE IF NOT EXISTS analysis_artifacts (
                id VARCHAR PRIMARY KEY,
                run_id VARCHAR NOT NULL,
                kind VARCHAR,
                filename VARCHAR,
                rel_path VARCHAR,
                caption VARCHAR,
                size INTEGER,
                sha256 VARCHAR,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # Datensätze (WP2): mit Papern gesammelte Forschungs-Datensätze (Metadaten +
        # Link/DOI/Lizenz). Große Daten werden NICHT auto-heruntergeladen (Privacy) —
        # nur die nachvollziehbare Referenz. Nutzbar als Eingabe für die Analyse-Werkstatt.
        self._execute("""
            CREATE TABLE IF NOT EXISTS datasets (
                id VARCHAR PRIMARY KEY,
                project_id VARCHAR,
                source VARCHAR,
                external_id VARCHAR,
                title VARCHAR,
                description TEXT,
                url VARCHAR,
                doi VARCHAR,
                license VARCHAR,
                size VARCHAR,
                year INTEGER,
                linked_paper_id VARCHAR,
                metadata JSON,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # PDF-Notizen: pro Paper an einer Textstelle (Highlight) oder einem Punkt verankerte
        # kleine Notiz. Rects sind auf 0..1 relativ zur Seiten-Oberfläche normalisiert (zoom-
        # unabhängig); quote = markierter Text (späterer Re-Anchor-Fallback), body = Notiztext.
        self._execute("""
            CREATE TABLE IF NOT EXISTS pdf_annotations (
                id VARCHAR PRIMARY KEY,
                paper_id VARCHAR NOT NULL,
                page_number INTEGER NOT NULL,
                kind VARCHAR DEFAULT 'highlight',
                rects JSON,
                quote TEXT,
                body TEXT,
                color VARCHAR,
                created_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        self._execute("CREATE INDEX IF NOT EXISTS idx_benchmark_runs_kind ON benchmark_runs(kind)")

        self._execute("CREATE INDEX IF NOT EXISTS idx_batch_jobs_status ON batch_jobs(status)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_batch_items_status ON batch_job_items(job_id, status)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_embeddings_label ON entity_embeddings(label_norm)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_extraction_quality_paper ON extraction_quality(paper_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_entity_review_status ON entity_review_queue(review_status)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_notes_project ON notes(project_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_note_citations_note ON note_citations(note_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_note_ai_threads_note ON note_ai_threads(note_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_note_ai_messages_thread ON note_ai_messages(thread_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_research_sessions_project ON research_sessions(project_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_parallel_sessions_project ON parallel_sessions(project_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_parallel_variants_session ON parallel_variants(session_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_parallel_stages_session ON parallel_stages(session_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_parallel_entries_variant ON parallel_entries(variant_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_parallel_entries_session ON parallel_entries(session_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_parallel_followups_session ON parallel_followups(session_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_code_projects_path ON code_projects(path)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_analysis_runs_project ON analysis_runs(project_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_analysis_artifacts_run ON analysis_artifacts(run_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_datasets_project ON datasets(project_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_pdf_annotations_paper ON pdf_annotations(paper_id)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_companion_sessions_kind ON companion_sessions(kind)")
        self._execute("CREATE INDEX IF NOT EXISTS idx_companion_messages_session ON companion_messages(session_id)")

    def _backfill_parallel_stages(self) -> None:
        """Adopt pre-stage variants: every session with stage-less variants and no stage
        yet gets a default "Etappe 1" (aktiv) that its variants are attached to. Runs on
        every open; a no-op after the first migration."""
        rows = self._execute("""
            SELECT DISTINCT v.session_id FROM parallel_variants v
            WHERE v.stage_id IS NULL
              AND NOT EXISTS (SELECT 1 FROM parallel_stages s WHERE s.session_id = v.session_id)
        """).fetchall()
        for (session_id,) in rows:
            stage_id = f"stg_{uuid.uuid4().hex}"
            self._execute("""
                INSERT INTO parallel_stages (id, session_id, name, goal, status, position)
                VALUES (?, ?, 'Etappe 1', '', 'aktiv', 0)
            """, [stage_id, str(session_id)])
            self._execute(
                "UPDATE parallel_variants SET stage_id = ? WHERE session_id = ? AND stage_id IS NULL",
                [stage_id, str(session_id)],
            )
        # Sessions that gained stages later can still hold stragglers: attach any
        # remaining stage-less variant to its session's first stage.
        self._execute("""
            UPDATE parallel_variants v SET stage_id = (
                SELECT s.id FROM parallel_stages s
                WHERE s.session_id = v.session_id
                ORDER BY s.position ASC, s.created_timestamp ASC LIMIT 1
            )
            WHERE v.stage_id IS NULL
              AND EXISTS (SELECT 1 FROM parallel_stages s WHERE s.session_id = v.session_id)
        """)

    def _add_missing_columns(
        self,
        table_name: str,
        columns: dict[str, str],
        quoted_names: set[str] | None = None,
    ) -> None:
        existing = {
            row[1]
            for row in self._execute(f"PRAGMA table_info('{table_name}')").fetchall()
        }
        quoted_names = quoted_names or set()
        for name, column_type in columns.items():
            if name not in existing:
                column_name = f'"{name}"' if name in quoted_names else name
                self._execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
