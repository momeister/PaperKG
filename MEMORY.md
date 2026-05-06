# PaperKG / ScienceKG Memory

## Current State

- Phase 1 is implemented: harvester clients, deduplication, DuckDB metadata storage, and local PDF storage.
- Phase 2 is implemented as a local Kuzu-backed citation graph pipeline: metadata ingestion, citation/reference persistence, reference stub nodes, obsolescence scoring, co-citation similarity, API endpoints, and Streamlit graph visualization.
- Phase 3 is implemented as the extraction layer: LLM router, PDF parser routing, entity extraction, entity linking, vocabulary management, embeddings, conflict detection, batch processing, FastAPI endpoints, and Streamlit UI.
- Phase 4 and Phase 5 are not implemented as production features yet. Some files may exist as scaffolding or early placeholders.

## Canonical Documentation

- `README.md`: main overview, install/start commands, and current phase status.
- `QUICKSTART_PHASE3.md`: detailed Phase 3 usage guide for UI, API, providers, and troubleshooting.
- `ScienceKG_Projektplan.md`: roadmap and target architecture. It is not the authoritative implementation status.
- `AGENTS.md`: agent and Graphify workflow rules.
- `MEMORY.md`: current implementation memory and maintenance notes.

## Architecture Notes

- `storage/metadata_db.py` is the DuckDB metadata contract for Phase 1/2/3. It persists paper metadata, references/citations, extraction history, and resettable local state.
- `api/main.py` serves Phase 2 graph endpoints. `POST /graph/phase2/build` writes Paper, CITES, and co-citation SIMILAR_TO edges.
- `graph/paper_ingestion.py` normalizes Paper nodes and citation IDs from metadata records.
- `graph/kuzu_schema.py` owns the Kuzu schema and graph writes.
- `api/phase3_main.py` serves Phase 3 extraction endpoints and persists API batch extraction results to DuckDB.
- `ui/graph_visualization.py` is the Phase 2 graph UI.
- `ui/phase3_extraction.py` is the Phase 3 extraction UI with PDF Library, Extract, Batch, Harvest, History, and Vocabulary tabs.
- `query/llm_router.py` supports Ollama, LM Studio, OpenAI, and generic OpenAI-compatible providers via `config.yaml`.
- `parsing/parser_router.py` selects Marker, Nougat, Table Transformer, or VLM paths. Specialized parsers still degrade gracefully when model infrastructure is unavailable.
- `extraction/embedding_engine.py` defaults to deterministic `hash-fallback` embeddings for offline repeatability. It can use `sentence-transformers` BGE-M3 when explicitly initialized with `backend="sentence-transformers"` and dependencies/model files are available.
- `scheduler/nightly_jobs.py` provides a local callable graph rebuild plus optional Celery wiring. Redis/Celery are optional runtime infrastructure, not required for local tests.

## What Is Still Not Fully Production Grade

- Celery/Redis scheduling is optional wiring, not a deployed always-on scheduler.
- Specialized parser integrations for Nougat, Table Transformer, and VLM are framework/fallback implementations unless their external model services are installed and configured.
- BGE-M3 is supported as an optional backend, but the default remains `hash-fallback` to keep local tests offline and deterministic.
- OpenAlex concept linking is cache/local-strategy based. It is not yet a full live OpenAlex embedding index.
- Phase 4 query/chat and Phase 5 quality automation remain future work.

## Verification Snapshot

- Last full test run in this session: `51 passed`.
- Graphify was updated after code changes: latest rebuild reported `970 nodes`, `2072 edges`, `64 communities`.
- If test counts are quoted elsewhere, rerun `python -m pytest -q --basetemp .pytest-tmp-current` first.

## Maintenance Rules

- Keep root documentation lean: `README.md`, `QUICKSTART_PHASE3.md`, `ScienceKG_Projektplan.md`, `MEMORY.md`, and `AGENTS.md`.
- Do not recreate phase completion/status reports in the repo root. Put temporary notes in commits/issues or fold durable facts into `MEMORY.md`.
- Do not commit local data artifacts: PDFs, DuckDB files, Kuzu graph directories, Graphify output, pycache, or test temp directories.
- After modifying code files, run `graphify update .`.
