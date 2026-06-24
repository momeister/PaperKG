# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

ScienceKG (repo name PaperKG) is a **fully local, privacy-preserving** system for harvesting scientific
papers, building a knowledge graph from them, and answering research questions with grounded,
source-cited LLM responses. Everything runs against local data and local/self-hosted or
API-key'd LLMs — there is no cloud backend of our own.

The codebase grew in numbered **phases** (1–5). Phases are a historical/feature axis, not separate
deployments — they share one DuckDB, one PDF store, and one config. Most docs (`README.md`,
`QUICKSTART_PHASE3.md`, `ScienceKG_Projektplan.md`, `MEMORY.md`) are in German; code and identifiers
are English.

- **Phase 1** — harvesting (arXiv, Semantic Scholar, OpenAlex, Unpaywall, Papers with Code), dedup, DuckDB + local PDFs
- **Phase 2** — Kuzu citation graph, co-citation similarity
- **Phase 3** — PDF parsing, LLM entity/claim extraction, entity linking, embeddings, batch jobs
- **Phase 4** — local query assistant: KG/hybrid retrieval, grounded answers, hypotheses, source verification
- **Phase 5** — quality benchmarks, KG health, maintenance jobs, **and the React product frontend** (current focus)

## Architecture

Data flows roughly left to right; each stage is a top-level Python package:

```
harvester/  → storage/  → parsing/  → extraction/  → graph/  → query/  → quality/ + maintenance/ + scheduler/
 (fetch)      (persist)    (PDF→text) (LLM→entities) (Kuzu)   (retrieve/answer)   (eval / upkeep / cron)
```

- **`storage/metadata_db.py`** — the central DuckDB layer. One DB (`data/metadata.duckdb`) holds *everything*:
  `papers`, `paper_sources`, `extraction_results`, `batch_jobs`/`batch_job_items`, `entity_embeddings`,
  `extraction_quality`, `entity_review_queue`, and the notes tables (`notes`, `note_citations`,
  `note_assets`, `note_ai_threads`/`note_ai_messages`, `note_versions`). When adding a feature with
  persistence, add a table here, not a new store.
- **`storage/file_manager.py`** — versioned local PDF store under `data/pdfs/`.
- **`query/llm_router.py`** — `LLMRouter.from_config_file("config.yaml")`. Single abstraction over all
  providers (`ollama`, `lm_studio`, `openai`/`openai_compatible`, `gemini`, `nvidia`, `nvidia_local_nim`).
  Provider differences (response_format quirks, extra_body, chat templates) are handled here. Never call
  an LLM SDK directly elsewhere — go through the router so provider config and overrides apply.
- **`query/`** — `kg_retriever` (deterministic KG lookup), `hybrid_retriever` (adds stored embeddings),
  `grounded_responder` (answers using only local evidence; citations are `[arxiv:...]`-style IDs, never `[1]`),
  `hypothesis_generator`, `source_verifier` (locate the cited PDF + evidence text), `context_budget`.
- **`graph/`** — Kuzu schema + ingestion. **Kuzu only ships a wheel for Python < 3.14.** On 3.14 everything
  else works but the real graph build is unavailable; retrieval has a non-Kuzu fallback, so don't assume the
  graph is present.

### Backends (FastAPI) — there are several `app`s in `api/`

- **`api/product_main.py` → `api.product_main:app`** is the **current, unified product backend** used by the
  React frontend. It includes the phase-4 router (`app.include_router(phase4_main.app.router)`) and adds
  projects, import/harvest, extraction, notes, jobs, quality. **This is the one to extend for product work.**
- `api/main.py` (Phase 2 graph build), `api/phase3_main.py`, `api/phase4_main.py` are the older
  phase-specific apps. Treat them as building blocks; product features land in `product_main`.

### Frontends — two, with different roles

- **`frontend/`** — React + TypeScript + Vite. **This is the product UI.** Talks to `api.product_main:app`.
  `frontend/src/api.ts` is the typed client; `frontend/src/types.ts` the shared types; pages in
  `frontend/src/pages/`.
- **`ui/`** — Streamlit. **Dev/debug workbench only.** Do not put new product logic here; reusable logic
  belongs in `query/`, `quality/`, `api/` so both frontends can use it.

## Commands

This is a **Windows / PowerShell** environment. The repo uses a local **`.venv`**; `uv` is also available.

### Python tests (pytest)
```powershell
python -m pytest -q                 # whole suite
uv run pytest tests/test_phase4_query.py -q --tb=short    # one file (README often uses uv)
python -m pytest tests/test_product_api.py -v             # one file, verbose
python -m pytest tests/test_product_api.py::test_name     # single test
```
`pytest.ini` sets `asyncio_mode = auto` (async tests need no decorator). `tests/conftest.py` only puts the
repo root on `sys.path`. Tests that write scratch data take `--basetemp=.pytest-tmp-current/...`; the many
`tmp_codex_*` / `.pytest-tmp*` dirs are gitignored scratch output from prior runs — ignore them.

### Run the product stack (backend + React UI)
```powershell
python scripts/run_product.py        # preferred: auto-uses .venv, starts API + Vite
# or manually:
uvicorn api.product_main:app --reload --port 8000
cd frontend; npm.cmd install; npm.cmd run dev -- --port 5173
```
API → `http://127.0.0.1:8000`, frontend → `http://127.0.0.1:5173`. Use `npm.cmd` (not `npm`) on Windows.

### Tiefenanalyse LaTeX/PDF export
The deep-analysis "Gesamtantwort" can be exported to a thesis-/paper-style document via the
**PDF/LaTeX** button (backend `POST /research/tree/export`, package `export/`). It builds LaTeX
with title page, ToC, BibTeX `Quellenverzeichnis`, plus optional TikZ research-tree, matplotlib
charts, auto-tables and ComfyUI images. PDF compilation needs a LaTeX engine on PATH (`latexmk`/
`pdflatex`) — install **MiKTeX** (`winget install MiKTeX.MiKTeX`, auto-installs packages on demand).
Without an engine the endpoint gracefully returns a ZIP of `.tex`+`.bib`+figures instead (compile on
Overleaf). ComfyUI (port 8188) is optional and best-effort. Requires `matplotlib` in the `.venv`.

### Frontend checks
```powershell
cd frontend
npm.cmd run build      # tsc --noEmit (typecheck) + vite build
npm.cmd test           # vitest (unit)
npm.cmd run test:e2e   # playwright
```

### Older phase runners (mostly dev/Streamlit)
```powershell
python scripts/run_phase2.py     # graph build + Streamlit graph UI
python scripts/run_phase3.py     # extraction API + Streamlit
python scripts/run_phase4.py     # query API + Streamlit chat
python scripts/try_phase1.py "machine learning" --max-results 10 --full-phase1 --download
```

### Lint / format / types
```powershell
ruff check .
black .
mypy .
```

### Quality / health CLIs
```powershell
python -m quality.benchmark --run --output data/eval/quality_benchmark.json
python -m quality.kg_health --output data/eval/kg_health.json
python -m quality.phase4_eval --provider lm_studio --output data/eval/phase4_lm_studio.json
```

## Configuration & secrets

- **`config.yaml`** drives harvester rate limits, storage paths, and the full `llm:` provider matrix
  (`default_provider` + `providers:`). LLM behavior is config-first.
- **API keys are referenced by env-var name** in `config.yaml` (`api_key_env: "GEMINI_API_KEY"`), loaded from
  the shell or a local `.env` (gitignored; see `.env.example`). Keys never go in `config.yaml` or commits.
- `ontology.yaml` defines the extraction ontology used in `extraction/`.

## Conventions worth knowing

- Grounded answers must cite local paper IDs (`[arxiv:...]`); emitting bare `[1]` is treated as a quality
  failure by `quality/phase4_eval.py`. Preserve this when touching `grounded_responder` or prompts.
- `Alle Papers` is a **reserved global project mode** (`__all_papers__`): when active, Library/Graph/Assistant
  send no `project_id` and notes go to a global collection. It cannot be deleted or recreated as a normal project.
- A graphify `hook-check` runs on every Bash call (`.codex/hooks.json`); `.codex/` is gitignored and unrelated
  to your task — let the hook run, don't modify it.
