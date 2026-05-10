# PaperKG / ScienceKG Memory

## Current State

- Phase 1 is implemented: harvester clients, deduplication, DuckDB metadata storage, and local PDF storage.
- Phase 2 is implemented as a local Kuzu-backed citation graph pipeline: metadata ingestion, citation/reference persistence, reference stub nodes, obsolescence scoring, co-citation similarity, API endpoints, and Streamlit graph visualization.
- Phase 3 is implemented as the extraction layer: LLM router, PDF parser routing, ontology-aware entity extraction, canonical entity linking, vocabulary/review management, embeddings, conflict detection, batch processing, FastAPI endpoints, and Streamlit UI. The current extraction direction is precision-first: uncertain/high-recall entities are candidates or review items, not automatic KG nodes.
- Phase 4 is implemented as a first local query-assistant layer: deterministic KG retrieval, hybrid retrieval with stored entity embeddings, grounded responses, hypothesis generation, FastAPI endpoints, Streamlit chat/detail/project UIs, and a one-command runner.
- Phase 5 is not implemented as a production feature yet. Some files may exist as scaffolding or early placeholders.

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
- `graph/paper_ingestion.py` also ingests successful extraction rows into semantic `Concept`, `Method`, `HAS_CONCEPT`, and `HAS_METHOD` graph records when graph builds include extractions.
- `graph/kuzu_schema.py` owns the Kuzu schema and graph writes, including Paper/Citation writes and semantic Concept/Method edge writes.
- `api/phase3_main.py` serves Phase 3 extraction endpoints and persists API batch extraction results to DuckDB.
- `ui/graph_visualization.py` is the Phase 2 graph UI.
- `ui/phase3_extraction.py` is the Phase 3 extraction UI with PDF Library, Extract, Batch, Harvest, History, and Vocabulary tabs.
- `api/phase4_main.py` serves Phase 4 query endpoints: `/query/search`, `/query/answer`, `/query/hypotheses`, `/papers/{paper_id}`, and `/papers/{paper_id}/neighborhood`.
- `query/kg_retriever.py`, `query/hybrid_retriever.py`, `query/grounded_responder.py`, and `query/hypothesis_generator.py` implement local grounded retrieval and answer generation over DuckDB extraction history, optional Kuzu Cypher, and stored entity embeddings.
- `ui/chat_interface.py`, `ui/paper_detail.py`, and `ui/project_manager.py` are the Phase 4 Streamlit entry points. `scripts/run_phase4.py` starts the Phase 4 API and selected UI.
- `query/llm_router.py` supports Ollama, LM Studio, OpenAI, and generic OpenAI-compatible providers via `config.yaml`.
- Ollama requests set `keep_alive: "0s"` by default so local models unload after each extraction/generation call. The Phase 3 Streamlit UI no longer queries Ollama for model metadata on page load; use the manual refresh button when the local model list changes.
- `scripts/run_phase3.py` must not start model work on launch. Demo extraction is opt-in via `--run-demo`; the old implicit demo request was removed because it loaded Ollama/GPU memory without a user pressing Extract. On shutdown, the runner best-effort unloads configured Ollama models unless `--keep-ollama-loaded` is passed.
- `parsing/parser_router.py` selects Marker, Nougat, Table Transformer, or VLM paths. Specialized parsers still degrade gracefully when model infrastructure is unavailable.
- `parsing/parser_router.py` now also falls back to Marker when a selected specialized parser raises at runtime. This keeps Harvest/PDF Library parsing usable even when Nougat/Table/VLM placeholder or remote-parser infrastructure is registered but fails during the actual parse.
- `extraction/entity_extractor.py` uses structural extraction plus a deterministic high-recall scan. It chunks only when the parsed paper exceeds a large context budget, so normal ~40k-char papers stay at one structural LLM call plus one semantic LLM call. The scan backfills domain concepts/methods such as concept drift, bias, data availability/validity/accuracy/completeness, regulation, privacy, monitoring, model retraining, and data-pipeline monitoring so a parsed full paper cannot silently become an empty KG payload when the LLM under-extracts.
- `ontology.yaml` is the controlled ontology seed for production extraction. It defines allowed entity types, relation types, merge thresholds, and seed labels/aliases for ML/RL/statistics/affective-computing concepts such as `Q-learning`, `Markov Decision Process`, `Appraisal theory`, `Homeostasis`, `Concept Drift`, and data-quality terms. Relation types now include finer controlled predicates such as `USED_IN`, `USED_FOR`, `PART_OF`, `MODULATED_BY`, and `CORRESPONDS_TO` so deterministic relation repair does not have to misuse `IS_A`.
- `extraction/ontology.py` implements ontology loading, entity/relation validation, stable canonical IDs, and `CanonicalResolver`. Exact ontology alias matches now override noisy LLM-provided entity types before fallback matching, so labels such as `POMDP` and `Bayesian Affect Control Theory` keep ontology-owned canonical IDs and types even when the extractor calls them algorithms or theories. Embedding-based auto-merge is still allowed only when the embedding backend is real `sentence-transformers`; `hash-fallback` is treated as degraded and does not auto-merge semantically similar labels.
- `extraction/entity_linker.py` now enriches accepted concepts, methods, and candidate arrays with `entity_type`, `canonical_id`, `canonical_label`, `review_status`, evidence fields, and merge metadata. OpenAlex cache matches still work, but the local canonical resolver is now the main normalization layer. The linker also dedupes accepted concept/method entities across `concept:` and `method:` prefixes by canonical label, preserving `extracted_roles`, so author-year/system artifacts and duplicated phenomenon nodes do not become two KG nodes just because they were extracted in both arrays.
- `storage/metadata_db.py` now includes an `entity_review_queue` table. Successful extraction saves pending concepts/methods/candidates into this queue with suggested canonical labels, evidence, and merge candidates for later approval/rejection/aliasing.
- `graph/paper_ingestion.py` now uses `canonical_id`/`canonical_label` when available and blocks explicit `review_status="pending"` or `rejected` entities from automatic Kuzu writes. Legacy rows without review metadata remain conservatively supported for compatibility.
- `quality/benchmark.py` is implemented as a local benchmark CLI over curated JSON gold files. It reports precision/recall/F1 for accepted concepts, candidates, and methods, plus duplicate canonical rate, claim attribution errors, parser warning counts, and precision/duplicate gates. `quality/gold/emotion_rl_survey.json` is the first gold fixture for the Emotion-RL survey.
- `storage/metadata_db.py` resolves canonical paper IDs from aliases such as arXiv IDs, DOI strings, and FileManager PDF storage names. New UI/batch extraction saves under canonical IDs where possible and fills missing paper year/title/PDF metadata without overwriting existing metadata.
- `ui/phase3_extraction.py` opens short-lived DuckDB connections with `with init_metadata_db() as metadata_db:` instead of caching a write connection in Streamlit. This avoids locking `data/metadata.duckdb` across reruns. Automatic post-extraction conflict detection is disabled by default because it can create many extra LLM calls; enable the sidebar checkbox only when needed.
- `ui/phase3_extraction.py` resolves `config.yaml`, `data/pdfs`, `data/metadata.duckdb`, `data/graphs`, and `data/vocabulary.json` from the repository root, not the process working directory. This keeps the Extract tab's "From Harvest" PDF loading/parsing path pointed at the same local PDF library regardless of where Streamlit was launched from.
- `scripts/run_phase3.py` starts both FastAPI and Streamlit with `cwd=PROJECT_ROOT` and passes the Streamlit UI file as an absolute path. This prevents a second accidental `data/` tree from being used when the runner is invoked from outside the repo root.
- `extraction/embedding_engine.py` defaults to deterministic `hash-fallback` embeddings for offline repeatability. It can use `sentence-transformers` BGE-M3 when explicitly initialized with `backend="sentence-transformers"` and dependencies/model files are available.
- `scheduler/nightly_jobs.py` provides a local callable graph rebuild plus optional Celery wiring. Redis/Celery are optional runtime infrastructure, not required for local tests.

## What Is Still Not Fully Production Grade

- Celery/Redis scheduling is optional wiring, not a deployed always-on scheduler.
- Specialized parser integrations for Nougat, Table Transformer, and VLM are framework/fallback implementations unless their external model services are installed and configured.
- BGE-M3 is supported as an optional backend, but the default remains `hash-fallback` to keep local tests offline and deterministic.
- OpenAlex concept linking is cache/local-strategy based. It is not yet a full live OpenAlex embedding index.
- The ontology/review workflow is now present but still v1: there is no polished Streamlit review UI yet for approving, rejecting, merging, or writing review decisions back into `ontology.yaml`/`data/vocabulary.json`.
- `quality/benchmark.py` has the working harness and first gold fixture, but the benchmark set is not yet representative. Production-quality tuning still needs roughly 10-20 curated papers before relying on the reported aggregate metrics.
- Relation extraction remains controlled by design and now includes deterministic repair for common ontology/taxonomy edges in the Emotion-RL survey family, including appraisal submodels, dimensional emotion dimensions, reward-shaping subtypes, POMDP extensions, dopamine/TD-error correspondence, and algorithm `USED_IN` RL edges. It is still not a broad production relation extractor; REBEL/UniRel/scispaCy-style external candidate generators are intentionally not integrated until benchmark evidence shows they improve quality.
- Phase 4 is implemented as a local first pass, not a fully polished production assistant. It intentionally degrades gracefully without Kuzu, Redis/Celery, or a live LLM service. If answer generation fails while evidence exists, the UI/API now surface the generation error and return an evidence-only fallback instead of silently pretending that the evidence list is the generated answer.
- Phase 5 quality automation remains future work.

## Verification Snapshot

- Last full test run in this session after ontology exact-type override, cross-role entity dedupe, and richer controlled relation repair: `.venv\Scripts\python.exe -m pytest -q --basetemp=tmp_codex_rel_full` -> `116 passed`.
- Last focused Phase 3 extraction test run after ontology exact-type override, cross-role entity dedupe, and richer controlled relation repair: `.venv\Scripts\python.exe -m pytest tests\test_phase3_extraction.py -q --basetemp=tmp_codex_rel_phase3` -> `75 passed`.
- Last compile check after ontology/relation repair: `.venv\Scripts\python.exe -m compileall extraction tests -q` -> passed.
- Last Harvest PDF path fix verification: `.venv\Scripts\python.exe -m compileall ui scripts parsing extraction tests -q` -> passed; `.venv\Scripts\python.exe scripts\run_phase3.py --help` -> passed; latest harvested PDF smoke parse selected `marker` and produced `35` pages / `125,133` chars; `.venv\Scripts\python.exe -m pytest -q --basetemp=tmp_codex_harvest_fix_full` -> `116 passed`.
- Earlier full test run after the Harvest PDF parser fallback fix: `.venv\Scripts\python.exe -m pytest -q --basetemp=tmp_codex_harvest_full` -> `113 passed`.
- Earlier focused Phase 3/parser test run after the Harvest PDF parser fallback fix: `.venv\Scripts\python.exe -m pytest tests/test_phase3_extraction.py -q --basetemp=tmp_codex_harvest_phase3` -> `72 passed`.
- Last Harvest PDF parse smoke test used the first PDF found under `data/pdfs`, selected Marker, parsed `22` pages and `71,554` chars successfully.
- Last compile check after the Harvest PDF parser fallback fix: `.venv\Scripts\python.exe -m compileall parsing ui tests extraction` -> passed.
- Earlier focused extraction/graph/metadata/benchmark test run in this session: `.venv\Scripts\python.exe -m pytest tests/test_phase3_extraction.py tests/test_phase2_graph.py tests/test_metadata_db.py tests/test_quality_benchmark.py -q --tb=short --basetemp=tmp_codex_plan_pytest` -> `92 passed`.
- A first attempt to run the same focused suite without `--basetemp` failed only at pytest `tmp_path` setup with Windows ACL `PermissionError` on `C:\Users\morit\AppData\Local\Temp\pytest-of-morit`; the local workspace basetemp run passed.
- Last compile check in this session: `.venv\Scripts\python.exe -m compileall -q extraction quality graph storage api ui tests` -> passed.
- Last benchmark check in this session: `.venv\Scripts\python.exe -m quality.benchmark --run` -> 1 gold case, concept/method/candidate precision and recall all `1.0`, duplicate canonical rate `0.0`, gates passed. This is only a smoke/gold-fixture check until more curated papers are added.
- Phase 3 runner no-autostart fix verified with `.venv\Scripts\python.exe -m compileall -q scripts\run_phase3.py ui\phase3_extraction.py query\llm_router.py` and `.venv\Scripts\python.exe scripts\run_phase3.py --help`. `ollama ps` showed no loaded model after the fix.
- The specific parsed paper `CHANGING DATA SOURCES IN THE AGE OF MACHINE LEARNING FOR OFFICIAL STATISTICS` was smoke-tested with an empty fake LLM response: parsed text had `37,756` chars and deterministic extraction produced `86` concepts, `7` methods, and `paper_year=2023`.
- Sandbox pytest runs that need `tmp_path` still hit Windows ACL issues on temp directories. The focused 68-test suite passed when run outside the sandbox/escalated.
- Phase 4 API/UI startup was smoke-tested once: `api.phase4_main` imported, `/health` returned status `ok`, and Streamlit `ui/chat_interface.py` returned HTTP 200. The background preview processes did not persist from the tool session.
- `graphify update .` was attempted after the extraction/metadata/query/UI runtime changes, after the ontology/review/benchmark changes, after the ontology/relation repair, and after the Harvest PDF path fix, but `graphify` was not available in PATH (`CommandNotFoundException`). The previous graph report remains stale until Graphify is installed or PATH is fixed.
- If test counts are quoted elsewhere, rerun the focused Phase 4 suite first, then rerun the full suite after fixing the local pytest temp-directory ACL issue.

## Maintenance Rules

- Keep root documentation lean: `README.md`, `QUICKSTART_PHASE3.md`, `ScienceKG_Projektplan.md`, `MEMORY.md`, and `AGENTS.md`.
- Do not recreate phase completion/status reports in the repo root. Put temporary notes in commits/issues or fold durable facts into `MEMORY.md`.
- Do not commit local data artifacts: PDFs, DuckDB files, Kuzu graph directories, Graphify output, pycache, or test temp directories.
- After modifying code files, run `graphify update .`.
