# AGENTS.md

Compact guide for OpenCode agents working in this repo. For architecture,
conventions, and gotchas in depth, read **`CLAUDE.md`** — it is the authoritative
source and intentionally long; this file is the short index an agent needs first.

## What this is

ScienceKG (repo name `PaperKG`) — fully local, privacy-preserving pipeline:
harvest papers → DuckDB + local PDFs → LLM entity/claim extraction → Kuzu
citation graph → grounded query assistant → React product UI + Tauri desktop
shell. No cloud backend of our own; LLMs are local (Ollama/LM Studio) or
API-key'd. Python backend, TypeScript frontend, Rust desktop shell + Rust
CodeSearch workspace.

## Environment

- **Python: 3.10–3.13 only.** Kuzu (graph DB) has no wheel for 3.14; on 3.14 the
  graph build is unavailable but retrieval has a non-Kuzu fallback. CI uses 3.11
  (matplotlib 3.11 pin needs ≥3.11 and kuzu still has a wheel).
- Local `.venv/` is the canonical env. On Linux use `.venv/bin/python`; on
  Windows `.venv\Scripts\python`. `uv` is also available. Torch is **not** in the
  default venv — `embedding.backend` falls back to hash unless you install it.
  `EmbeddingEngine(backend="auto")` tries `sentence-transformers` + BGE-M3 and
  silently falls back to hash when unavailable; the default stays `hash-fallback`
  so a normal startup never triggers a multi-GB model download.
- Secrets via `.env` (gitignored; see `.env.example`). `config.yaml` references
  keys by env-var name (`api_key_env:`), never inline. Don't commit `.env`.
- Only **one** backend may run at a time — DuckDB allows a single writer per
  file. The startup `InstanceLock` (`storage/instance_lock.py`) refuses a second
  process; a stray Docker container, Tauri sidecar, or Streamlit also counts.
  `SCIENCEKG_DISABLE_INSTANCE_LOCK=1` opts out (the test suite does implicitly).

## Commands (Linux form)

```bash
# Python — run from repo root, using the local venv
.venv/bin/python -m pytest -q                          # whole suite (testpaths=tests in pytest.ini)
.venv/bin/python -m pytest tests/test_product_api.py -q # one file
.venv/bin/python -m pytest tests/test_phase4_query.py::test_name --tb=short
uv run pytest tests/test_phase4_query.py -q --basetemp=.pytest-tmp-current/phase4   # as README shows

# Lint / format / types
ruff check .
black .
mypy .

# Product stack (preferred): auto-uses .venv, starts API + Vite
.venv/bin/python scripts/run_product.py
# Or manually:
uvicorn api.product_main:app --reload --port 8000        # API → http://127.0.0.1:8000
# Frontend (separate dir, separate package.json):
npm install --prefix frontend && npm run dev --prefix frontend -- --port 5173   # → http://127.0.0.1:5173

# Frontend checks (run inside frontend/)
npm --prefix frontend run build      # tsc --noEmit + vite build  (this is the typecheck)
npm --prefix frontend test            # vitest unit
npm --prefix frontend run test:e2e    # playwright
```

Windows notes from `CLAUDE.md`: use `npm.cmd` instead of `npm`, and
`.venv\Scripts\python` instead of `.venv/bin/python`.

## Where things live / app entrypoints

- **Backend (FastAPI):** `api/product_main.py` → `api.product_main:app` is the
  **current unified product backend** the React UI talks to. It includes
  `api/phase4_main.py`'s router. `api/main.py`, `api/phase3_main.py`,
  `api/phase4_main.py` are older phase-specific apps — building blocks, not the
  product surface. New product routes go in `product_main` (or a router under
  `api/routers/`).
- **Frontend (React + Vite + TS):** `frontend/` — the product UI. Typed client
  in `frontend/src/api.ts`, shared types in `frontend/src/types.ts`, pages in
  `frontend/src/pages/`. Talks only to `api.product_main`.
- **`ui/` (Streamlit):** dev/debug workbench only. Don't add product logic here
  — put reusable logic in `query/`, `quality/`, `api/` so both UIs share it.
- **Desktop shell (Tauri 2, Rust):** `src-tauri/`. Runs `api.product_main:app` as
  a managed sidecar, injects its origin as `window.__API_BASE__`. Root
  `package.json` has only the Tauri CLI; the web app stays in
  `frontend/package.json`.
- **Code-Graph backend (Rust workspace):** `codesearch/` — the authoritative
  vendored copy (no upstream git remote). The `cs` binary is built via
  `python packaging/build_codesearch.py` → `src-tauri/sidecar/codesearch/cs`.
  Building it is **optional**; without it the Code-Graph tab shows a hint and
  the rest of the app works. Tests: `cargo test --manifest-path
  codesearch/Cargo.toml --workspace`.
- **Data flow:** `harvester/ → storage/ → parsing/ → extraction/ → graph/ →
  query/ → quality/ + maintenance/ + scheduler/`.
- **Storage:** one DuckDB file `data/metadata.duckdb` holds everything (papers,
  extraction_results, batch_jobs, notes tables, …). New persistence → add a
  table in `storage/metadata_db.py`, not a new store. Project→paper mapping and
  project metadata live in gitignored JSON sidecars (`data/projects.json`,
  `project_primary.json`, `project_meta.json`); always write them via
  `write_json_atomic` (`storage/atomic_json.py`).
- **LLM access:** always go through `query/llm_router.py`
  (`LLMRouter.from_config_file("config.yaml")`). Never call an LLM SDK directly.
- Config: `config.yaml` (harvester limits, paths, `llm:` provider matrix),
  `ontology.yaml` (extraction ontology).

## Conventions that bite if missed

- **Grounded answers must cite local paper IDs** (`[arxiv:...]`), never bare
  `[1]` — `quality/phase4_eval.py` treats bare citations as a quality failure.
- **PDF parsing runs in a child process** (`parsing/pdf_guard.py` +
  `parsing/pdf_child.py`) because a single complex page makes pdfplumber/pypdf
  allocate unbounded memory. `MarkerParser.parse()` is the guard shell; the real
  parser is `parse_direct()`. **Do not use `multiprocessing`** — `spawn`
  re-imports the parent's `__main__` under uvicorn.
- **`POST /extraction/batch` creates the `batch_jobs` row immediately**, before
  PDFs are resolved; the frontend polls `/extraction/batch/{id}/items` from
  submit. Don't defer the row write to `process_papers` (every poll 404s until
  then).
- **LLM errors are classified** in `query/llm_errors.py`
  (`quota|rate_limit|auth|context_length|connection|empty|unknown`). The kind is
  tagged as a `[llm:<kind>] …` prefix in error messages; `BatchProcessor`
  **stops** the run on `quota`/`rate_limit`/`auth`. `LLMRouter` must keep
  putting `HTTP <status>` in the message or the classifier has nothing to match.
- **Project id == project name**. Renaming migrates every project-scoped row
  (`storage/metadata_db/project_scope.py`, `PROJECT_SCOPED_TABLES`) plus the
  sidecars. Order matters: migrate DB + sidecars **first**, write `projects.json`
  last. `__all_papers__` is a reserved global project mode (no `project_id`
  sent); it can't be deleted or recreated as a normal project.
- A paper without a PDF still has value: abstract feeds abstract-only
  extraction; `landing_page_url`/`doi` are the durable external link (keep
  normalizers filling both — `pdf_url` is overwritten with a local path after
  download and is **not** a reliable external link). Counts of "extractable
  papers" all go through `frontend/src/extractionCounts.ts` (one formula).
- **Code-Graph node IDs are hex strings**, never JS `number` (64-bit blake3
  hashes lose low bits as a number). Don't pass `direction=both` to the graph
  slice. Every edge carries its source line **and** a confidence rank
  (`verified`/`resolved`/`guessed`/`measured`); unresolvable calls appear as
  `dynamic_gap`, never missing.
- **Checkpoints before writes** (Code-Graph stage 2): every symbol/file write,
  refactor apply, and sandbox apply pulls a git checkpoint first via
  `refs/paperkg/checkpoints/`; a checkpoint failure does **not** block the write
  — the response carries `checkpoint: null` + `checkpoint_reason`. `PATCH
  /codegraph/{id}/symbol/{node_id}/source` uses `content_hash` as an optimistic
  lock (409 on stale index or hash mismatch).
- Project bundles (`graph_bundle/`): Kuzu is deliberately **not** in the bundle
  (it's a cache, rebuilt via `POST /jobs/graph-rebuild`). Bundle imports are
  idempotent (extractions dedupe on content fingerprint, not timestamp). Never
  `zip.extractall` — every entry goes through `safe_member_path`.
- A graphify `hook-check` runs on every Bash call (`.codex/hooks.json`); `.codex/`
  is gitignored and unrelated to your task — let it run, don't modify it.

## Testing notes

- `pytest.ini`: `asyncio_mode = auto` (async tests need no decorator),
  `testpaths = tests`. The latter matters — without it a bare `pytest -q`
  recurses into build artifacts (the PyInstaller sidecar bundles matplotlib's
  own test suite) and collection dies silently.
- `tests/conftest.py` only puts the repo root on `sys.path`.
- Scratch dirs like `tmp_codex_*/`, `.pytest-tmp*`, `.pytest-tmp-current/` are
  gitignored output from prior runs — ignore them, don't clean them up.
- Code-Graph tests (`tests/test_codegraph.py`, `test_codegraph_companion.py`,
  `test_codegraph_refactor.py`) skip themselves if the `cs` binary isn't built.
  `tests/test_llm_router_tools.py` is offline.
- Frontend tests: vitest unit (`*.test.ts(x)`) and playwright e2e, both in
  `frontend/`. `npm run build` is the TS typecheck (runs `tsc --noEmit`).

## Native bundle (only if asked)

- `npm run tauri dev` — native window + Vite + backend sidecar (self-contained).
- `npm run tauri:build` — standalone NSIS installer (Windows, built locally);
  Linux deb/AppImage + macOS dmg are built **only in CI**
  (`.github/workflows/native-build.yml`, on tag `v*` or `workflow_dispatch`).
- Full bundle needs heavy deps installed first: `pip install -r requirements.txt
  -r requirements-build.txt` + CPU torch; or set `SCIENCEKG_BUNDLE_LEAN=1` for a
  small hash-fallback bundle. Installed app stores data in
  `%APPDATA%/com.sciencekg.desktop` (Windows); dev uses repo-root `data/`.
- Status tracker: `docs/NATIVE_APP.md` — update it when advancing a milestone.