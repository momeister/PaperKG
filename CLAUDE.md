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

- **Phase 1** — harvesting, dedup, DuckDB + local PDFs. The searchable sources live in
  **`harvester/source_registry.py`** (one list, fachlich gruppiert) — the dispatch in
  `api/routers/harvest.py::_run_harvest_search` and `GET /harvest/sources` (frontend picker) both read it,
  so add a source in exactly those two places. `harvester/http_client.py` is the shared throttled base for
  newer clients. Unpaywall is an OA-PDF *resolver*, not a search source.
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
  **DuckDB allows exactly one writing process per file.** A second backend (Docker *and* host, a
  stray Tauri sidecar, Streamlit) used to surface as `duckdb.IOException` → HTTP 500 → the frontend's
  `data ?? []` → *"all projects are gone"*. Three guards now exist: `MetadataDBLockedError`
  (`storage/metadata_db/base.py`, served as **503** with a German explanation), a startup
  `InstanceLock` (`storage/instance_lock.py`), and `GET /projects` no longer needing the DB at all.
  `InstanceLock` deliberately uses **two** mechanisms on `data/.backend.lock`: an OS advisory
  `flock` (exact, self-releasing — but same-kernel only) *plus* a heartbeat timestamp written into
  the file every 10s, stale after 45s. The heartbeat is not redundant: Docker Desktop runs the
  daemon in a VM, so a bind mount passes through virtiofs/gRPC-FUSE and **propagates no POSIX
  locks at all — not even DuckDB's own**. Measured: with the container up, the host could open the
  same `metadata.duckdb` read-write. Two writers on one file is worse than a visible error, so the
  file *content* (which does propagate) is what actually guards the container↔host case.
- **`storage/atomic_json.py`** — the JSON sidecars (`data/projects.json`, `project_primary.json`,
  `project_meta.json`) hold the *only* copy of the project→paper mapping and are gitignored. Always write
  them via `write_json_atomic` (tmp + `fsync` + `os.replace`) and read via `read_json_dict`, which raises
  `CorruptJsonError` instead of silently returning `{}` — a truncated file read as `{}` used to be
  overwritten on the next save, turning a crash into real data loss.
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

The repo is developed on **both Windows (PowerShell) and Linux**. Commands below are written in the
PowerShell form; on Linux/macOS use `npm` wherever they say `npm.cmd`, and `.venv/bin/python` instead of
`.venv\Scripts\python`. The repo uses a local **`.venv`**; `uv` is also available.

**Always make sure only one backend is running.** Docker (`./data` is bind-mounted) and a host backend
contend for the same DuckDB file; the second one now refuses to start (`storage/instance_lock.py`).
Set `SCIENCEKG_DISABLE_INSTANCE_LOCK=1` only if you know what you are doing (the test suite sets it
implicitly by detecting pytest).

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

### Run as a native desktop app (Tauri)
The same frontend+backend also ship as a **native desktop program** via a Tauri 2 (Rust) shell in
`src-tauri/`. The shell opens an OS window, starts `api.product_main:app` as a **managed sidecar** on a
free localhost port, and injects that origin into the page as `window.__API_BASE__` (read in
`frontend/src/api.ts → resolveApiBaseUrl()`). Tooling lives in the **root** `package.json` (Tauri CLI
only — the web frontend stays in `frontend/package.json`).
```powershell
npm.cmd install            # once: installs @tauri-apps/cli (root)
npm.cmd run tauri dev      # native window + Vite + backend sidecar (self-contained; no run_product.py needed)
npm.cmd run tauri:build    # M2 standalone NSIS installer: builds the PyInstaller sidecar + frontend + bundle
```
`tauri dev` (debug) runs the backend from the `.venv`. `npm run tauri:build` (note the colon; it passes
`--config src-tauri/tauri.bundle.conf.json`) produces a **standalone NSIS installer** that needs no
pre-installed Python: `beforeBuildCommand` runs `python packaging/build_sidecar.py` (PyInstaller one-dir
→ `src-tauri/sidecar/`), which is shipped as a Tauri resource and spawned in release builds. The
installed app stores data in **`%APPDATA%/com.sciencekg.desktop`** (config.yaml/ontology.yaml seeded from
`src-tauri/defaults/` on first run); dev is unchanged (repo-root `data/`). The **full** bundle needs the
heavy runtime deps installed first (torch is *not* in the `.venv` by default — `embedding.backend` is
`hash-fallback`): `pip install -r requirements.txt -r requirements-build.txt` + CPU torch; or set
`SCIENCEKG_BUNDLE_LEAN=1` for a small hash-fallback bundle. **Full build details + status:
`docs/NATIVE_APP.md`** — update its tracker when advancing a milestone. The web app
(`scripts/run_product.py`) is unchanged.

**Native-only shell features** (each gated on `isTauri()` with a web-mode hint; reuse
`nativeInvoke`/`nativeListen` in `frontend/src/native.ts` ↔ `#[tauri::command]`s in `src-tauri/src/`):
the **Code-Werkstatt** tab (`/werkstatt`, `WorkstationPage.tsx`) pairs a `portable-pty` terminal
(`src-tauri/src/terminal.rs`, for `claude`/opencode/codex/git) with a Monaco editor + git diff over the
`/workspaces*` backend (`workspace/manager.py`); the **AI-Cursor overlay** (`src-tauri/src/overlay.rs`,
transparent always-on-top window, hotkey `Ctrl/Cmd+Shift+Space` + tray) hosts the **Desktop Companion**
(R6, default mode: native `xcap` screenshots in `src-tauri/src/capture.rs` → `/companion/*` →
LLMRouter vision with LM Studio Qwen3-VL or the `anthropic` provider; gliding/dodging pointer ring +
"Bereich erklären" freeze-frame snip; screenshots leave the machine only when `anthropic` is explicitly
selected). Pointer grounding uses a **0-1000 normalized grid** (Qwen-VL native space — pixel coords put
the ring systematically wrong); the companion is **multi-monitor** (captures the monitor under the cursor
by default, or a picked one via `list_monitors`) and can optionally ground answers in **local papers**
(`HybridRetriever`, `[arxiv:...]` citations) and/or **web search** (`use_papers`/`use_web`). Its legacy
mode re-fires the UI-TARS handoff; **native Selbst-Steuerung** (R7, skeleton, off by default via
`companion.self_drive.enabled`) plans one action per screenshot in the backend (`query/self_drive.py`) and
executes it through `src-tauri/src/control.rs` (`enigo`) in a per-action confirmation mode, emergency-stop
`Ctrl+Shift+Q`. The
**Jupyter** tab (`/jupyter`, `src-tauri/src/jupyter.rs`) runs an optional `jupyter lab` sidecar in an
iframe. The **Linux (deb/AppImage) + macOS (dmg) bundles** are built only in CI
(`.github/workflows/native-build.yml`, `workflow_dispatch`/tag `v*`); Windows NSIS builds locally.

### Analyse-Werkstatt (reproducible code execution)
A **Claude-Science-style auditable-artifact** surface: the user asks for an analysis in natural
language, the backend (`analysis/` package) uses `LLMRouter` to **write a Python script**
(`analysis/planner.py`), runs it locally as a **subprocess** (`analysis/runner.py` — no shell,
fixed seed, `Agg`/headless, hard timeout, path-contained via `workspace.manager.resolve_within`)
and returns figures/tables. `analysis/service.py` ties it together: **every run is a real,
git-versioned folder** inside the managed Werkstatt project `PaperKG-Analysen`
(`<run>/script.py`, `inputs/`, `outputs/`, `run.json` = env/seed/provider/model/planning-history/
output-hashes, `README.md` = plain-language). Output is deterministic — same seed ⇒ identical
`output_hash` (the WP4 reproducibility badge). Persistence lives in DuckDB (`analysis_runs`,
`analysis_artifacts` in `storage/metadata_db.py`); artifacts are served via
`GET /analysis/artifacts/{id}` (`FileResponse` + `path_safety`, **no** `StaticFiles`). Routes:
`POST /analysis/runs`, `GET /analysis/runs[/{id}]`, `POST /analysis/runs/{id}/revise` (NL edit or
figure annotation → new script version + git commit), `POST /analysis/runs/{id}/verify`
(`analysis/verify.py` re-runs the committed script in a temp copy → reproducibility badge),
`DELETE`. UI: the **Analyse** tab in the
Workspace center column (`frontend/src/pages/AnalysisPanel.tsx`, swapped in via `WorkspacePage`'s
`centerView`) renders figures/tables inline with provenance chips (open in Werkstatt, download
data, revise, copy Markdown). Config: the `analysis:` block in `config.yaml` (`timeout_seconds`,
`seed`). **No sandbox** — the subprocess runs with backend rights (like the Werkstatt terminal /
Jupyter); a Docker `--network none` mode is a later option.

### Code-Graph (CodeSearch)
Der Code-Graph beantwortet zu einem Werkstatt-Projekt die Frage, die der Editor nicht beantwortet:
*warum ist das so*. Grundlage ist der einvendorte Rust-Workspace **`codesearch/`** (tree-sitter
für 13 Sprachen, dreistufige Auflösung, PageRank + git-Churn). Er hat **zwei Oberflächen**:

- **`/code`** (`frontend/src/pages/codegraph/`, eigener Sidebar-Eintrag in der Gruppe „Arbeiten") ist die
  grosse: Navigator mit Facetten über alle 15 Symbolarten links, in der Mitte **Bereiche** |
  **Karte** (`@xyflow/react`, gespeist aus `graph_slice`) | **Code** | **Diagramm** | **Pfad** und unter
  der Karte die Kantentabelle, rechts Inspektor und **Gespräch**. Deep-Link
  `?project=&node=<hex>&view=&cluster=&depth=&edges=&q=&to=`; das Projekt teilt sich den
  `localStorage`-Schlüssel `sciencekg.werkstatt.project` mit der Werkstatt.
  Karte, Inspektor und Gespräch haben je einen ⤢-Knopf, der die Nebenspalten einklappt — nur einer kann
  gross sein, sonst müsste man raten, welcher Klick was zurücksetzt.
- **„Bereiche" ist der Einstieg, nicht die Karte** (`ClusterMapPanel.tsx`, `clusterLayout.ts`): ohne
  `?node=` beginnt die Seite mit der Cluster-Landkarte statt mit einer Suchmaske — wer ein fremdes
  Projekt öffnet, weiss noch nicht, wonach er sucht. Ein Cluster ist ein **Pfad-Präfix**, der Rollup ist
  reines SQL (`cs-graph/src/cluster.rs`, `clusters`/`cluster_edges`/`cluster_members` in `serve.rs`) und
  wird **nie persistiert**, kann also nicht veralten. Eine aggregierte Kante trägt die **schwächste**
  enthaltene Sicherheitsstufe (Minimum von `conf_rank`, nicht den Modus — sonst würde eine Vermutung auf
  der Zoomstufe zur Tatsache, auf der sie niemand prüfen kann) und lässt sich per Klick in die echten
  Kanten mit `datei:zeile` aufklappen (`ClusterEvidencePanel.tsx`). Das Layout ist eine
  **Abhängigkeitsschichtung** mit deterministischem Zyklenbruch; Rückkanten sind rot gestrichelt und
  ausgewiesen. Der LLM benennt nur (`codegraph/clusters.py`, `code_cluster_labels`, SSE
  `POST …/clusters/name`) — die Struktur ist vom Modell nicht beeinflussbar, und ohne Modell steht
  überall der Ordnername. Ein `fingerprint` über die *Gestalt* des Clusters (nicht den Dateiinhalt)
  entwertet einen Namen nach echter Veränderung, nicht nach jeder geänderten Zeile.
- **Der Code-Tab ist ein echter Editor mit zwei Betriebsarten** (`CodeEditorPanel.tsx` →
  `MonacoHost.tsx` + `SymbolEditor`/`FileEditor`; Monaco `lazy` *innerhalb* der schon lazy geladenen
  Seite und **genau einmal** importiert — der Entry-Chunk, den fünf Webviews parsen, bleibt frei; deshalb
  hängt der Stift-Knopf in der Chat-Trefferliste und im Inspektor nur die Mittelspalte um, statt einen
  zweiten Editor einzuhängen). **Funktion** ist der Standard und schreibt über
  `PATCH /codegraph/{id}/symbol/{node_id}/source`: der Zeilenbereich kommt aus dem Graphen (nicht vom
  Client — ein länger offener Tab schriebe sonst an die falsche Stelle), der `content_hash` ist eine
  optimistische Sperre (**409** statt stillem Überschreiben), und ein **veralteter Index** ist ebenfalls
  409, weil die Zeilennummern dann woandershin zeigen. `_splice_lines` erhält die Zeilenenden der Datei
  (CRLF bleibt CRLF, sonst sähe jede Zeile im git-Diff geändert aus) und setzt am Dateiende keinen
  Umbruch dazu. **Datei** ist das bisherige Verhalten über `PUT /workspaces/{id}/file`. Danach steht der
  Hinweis, dass der Index den alten Stand kennt, samt Knopf zum Neuindizieren.
- **Der Inspektor beantwortet vier Fragen in dieser Reihenfolge**: *wofür ist das da* (Docstring, dazu
  ein **immer vorhandener Steckbrief** aus `summary.ts` — vorher stand hier nichts, wenn im Code kein
  Kommentar stand; plus „erklären" für eine LLM-Erklärung), *warum ist das so gebaut* (siehe unten),
  *was geht rein und was kommt raus* (Parameter links, Rückgabe/Ausnahmen/Nebenwirkungen rechts),
  *worauf beruht das* (Zahlen, Nachbarn).
  Ist ein Nachbar deutlich relevanter als das Betrachtete, wird das gesagt — man klickt oft eine
  Hilfsfunktion an und meint die Stelle darunter (`strongerNeighbours`, auf der Karte ▲).
- **„Warum wurde das so gebaut?"** (`codegraph/rationale.py`, `CodeWhyPanel.tsx`) hat **zwei Hälften,
  die nie in einem Absatz landen**. Der Grund ist ein Ehrlichkeitsvorbehalt: der Prompt einer
  erzeugenden KI ist in diesem Repository nirgends aufgezeichnet, und eine erfundene *Absicht* fällt —
  anders als eine erfundene `datei:zeile` — beim Nachschlagen nicht auf.
  *Aufgezeichnet* (`GET /codegraph/{id}/why/{node_id}`, ohne Modell, sofort da) ist
  `workspace/manager.py::git_log_for_lines` — `git log -L<von>,<bis>:<datei>`, also die Commits über
  **genau diese Zeilen** statt der ganzen Datei — plus die selbst hinterlegten Begründungen aus
  `code_rationale` (`POST`/`DELETE …/rationale`, `content_hash` → `stale` wie bei `note_citations`).
  Die vier „es gibt nichts"-Fälle (`no_git`/`no_repo`/`no_commits`/`untracked`) werden über
  **Rückgabewerte** unterschieden, nicht über den Meldungstext: git ist übersetzt, und auf einem
  deutschen System hiesse eine Suche nach englischen Wortfetzen „Fehler", wo nur nichts aufgezeichnet
  ist. *Hergeleitet* (`POST …/why/{node_id}`, SSE) sind genau zwei Sätze aus Code, Tests, Aufrufern und
  Commit-Betreffen, gestrichelt umrandet wie eine geratene Kante, mit `verify_citations` geprüft, mit
  `based_on`-Zahlen daneben und **nicht persistiert**. Tests werden über die **Symbolart**
  `NodeKind::Test` der Aufrufer gefunden, nicht über die Kantenart `tested_by`: die ist in `cs-core`
  deklariert, wird aber nirgends erzeugt, und eine Abfrage darauf sähe aus wie „keine Tests".
- **`POST /codegraph/{id}/explain/{node_id}`** (`codegraph/explain.py`, SSE) erklärt ein einzelnes Symbol.
  Getrennt von `/ask`, weil hier nicht gesucht, sondern nachgeschlagen wird: `get_node` liefert Fakten
  *und* Quelltext, und genau dieses Nachschlagen ist die Lizenz zum Zitieren. Ohne Werkzeuge (der
  Kontext steht schon im Prompt), mit `verify_citations` wie jede andere Antwort, ohne Persistenz — eine
  Erklärung veraltet mit der nächsten Dateiänderung.
- **Das Gespräch** (`CodeChatPanel.tsx`, `code_chats`/`code_chat_turns`, `POST …/chats/{id}/ask` als
  SSE) ist die rechte Spalte von `/code` und beantwortet die andere Frageform: *wo passiert Feature X*.
  Antwort **plus Trefferliste plus Karte** — ein Absatz allein ist auf diese Frage keine Antwort. Jeder
  Eintrag der Trefferliste trägt, **warum** er dort steht (`zitiert` > `spur` > `nachgeschlagen` >
  `vorab-suche`), weil „belegt zitiert" und „lag im Kontext" sonst gleich aussähen; „auf der Karte
  zeigen" baut daraus eine Mehrwurzel-Karte (`useMultiFocusMap`, `pickMapRoots` — höchstens
  `MAX_MAP_ROOTS = 8` Wurzeln, jede kostet zwei Anfragen, und wie viele es insgesamt waren steht
  daneben). Zwei Dinge dahinter sind heikel und deshalb ausdrücklich: `context_build` nimmt ein
  **`extend`** (Standard weiter `false`), damit die *Zitierlizenz mit dem Gespräch wächst* statt mit
  jeder Frage zu verfallen — eine Rückfrage darf zitieren, was Runde eins gezeigt bekam; und
  `build_with(broad=true)` fährt Namens- **und** Volltextsuche immer zusammen statt Volltext nur als
  Notnagel, plus `GERMAN_TO_CODE` in `cs-llm/src/context.rs` (~45 Stämme: `passwor` → `password`/`hash`/
  `credential`), weil „Wo werden die Passwörter verschlüsselt?" in einer englischen Codebasis sonst nie
  ankommt. **Kein Embedding-Index** — er wäre ein zweiter, alternder Bestand neben einem exakten und
  bräche die Offline-Zusage.
- **Der Tab in der Werkstatt** (`frontend/src/pages/CodeGraphPanel.tsx`, dritter `resultTab` in
  `WorkstationPage.tsx`) und `CodeAskPanel.tsx` (Einzelfrage) bleiben unverändert für das Nachschlagen
  *neben dem Editor* — in einer 26 % breiten Spalte ist ein Faden mit Trefferliste falsch. Beide teilen sich
  `pages/codegraph/shared.tsx` (`Confidence`, `Relevance`, `SymbolRow`, `NeighbourList`,
  `ConfidenceLegend`, `useCodeIndex`) — die Marker ●◐○◆ und die Bezeichnungen sind Vokabular und dürfen
  nicht zweimal existieren.

### Code-Graph Stufe 2 — Schreiben, ohne dass Schreiben gefährlich wird

Stufe 1 („Verstehen") macht den Code lesbar. Stufe 2 macht das Werkzeug
**schreibfähig**, und dermassen, dass jeder Schreibschritt einen Rückweg hat
und eine Änderung *vor* der Ausführung zeigt, was sie mitreisst. Vier Bausteine,
die aufeinander aufbauen; alle fail-soft ohne `cs`-Binary **und** ohne LLM.

- **Git-Checkpoints** (`workspace/checkpoints.py`, `POST /workspaces/{id}/checkpoints`,
  `PATCH /codegraph/{id}/symbol/{node_id}/source` und `PUT /workspaces/{id}/file` lösen
  automatisch aus). Ein Checkpoint berührt **weder HEAD noch Index noch Arbeitsbaum**
  des Nutzers: alternative Index-Datei via `GIT_INDEX_FILE=<tmp>` → `git add -A` →
  `write-tree` → `commit-tree [-p HEAD]` → `update-ref refs/paperkg/checkpoints/<id>`.
  Die Refs liegen bewusst unter `refs/paperkg/`, damit `git gc` die Objekte nicht
  einsammelt. Repo ohne Commits (kein HEAD) → elternloser Commit. `.gitignore` wird
  respektiert (eine ignorierte, aber wichtige Datei ist **nicht** gesichert — das
  steht in der UI). Rücksprung: Vorschau (`plan_hash`) → Bestätigung → vorher einen
  `pre_restore`-Checkpoint → pfadweises `git restore --source=<sha> --worktree`.
  Aufräumen: pro Projekt die letzten 50 automatischen behalten, manuelle nie.
  Tabelle `code_checkpoints` in der DuckDB; **nicht** in `PROJECT_SCOPED_TABLES`.
- **Auswirkungsanalyse** (`GET /codegraph/{id}/impact/{node_id}`, `ImpactPanel.tsx`,
  Mittelspalten-Tab „Auswirkung"). `Graph::impact` in `cs-graph/src/query.rs` läuft
  **rückwärts** (`WITH RECURSIVE back`), mit Hop-Distanz und mitgeführter schwächster
  Sicherheit (`MIN(conf_rank)` auf dem Pfad, `MAX` über Pfade — eine Kette mit einer
  geratenen Kante ist geraten; ein verifizierter Weg genügt). `tests` über
  `kind='test'` (nicht `tested_by`, die nie erzeugt wird), `dynamic_gaps` als
  ausdrückliche Grenze, `truncated` sichtbar. **Vorschaltdialog** beim Speichern im
  Code-Editor (`CodeEditorPanel.tsx::gateSave`, localStorage `sciencekg.code.impactSkip`).
  Mitgefixt: `cs-index/src/parse.rs` klassifiziert `test_*`-**Methoden** als `Test`
  (war nur `Function`) → `SCHEMA_VERSION` 2 → alter Index wird verworfen.
- **Was-wäre-wenn-Sandbox** (`workspace/sandbox.py`, `/workspaces/{id}/sandboxes*`,
  `SandboxPanel.tsx`, Tab „Probelauf"). `git worktree add --detach
  data/sandboxes/<id> <checkpoint_sha>` — nur ein Checkpoint-Commit sichert die
  unversionierten Änderungen; ein Worktree auf `HEAD` verlöre sie. Ignorierte Ordner
  (`node_modules`, `.venv`, `target`) fehlen im Worktree (nicht verlinkt — ein Symlink
  machte Sandbox-Schreiben im Original wirksam). Testbefehl erkannt
  (pytest/npm/cargo), argv-Liste ohne Shell, Timeout. Übernahme in den Hauptbaum nur
  auf Knopfdruck, mit Checkpoint davor. Tabelle `code_sandboxes`. Der Pool
  (`codegraph/pool.py`) indexiert einen Sandbox-Stand unter
  `data/codegraph/<id>__sb_<sandbox_id>/` ohne Rust-Änderung.
- **Spaghetti-Löser** (Tab „Knäuel", `TanglePanel.tsx`). Diagnose ohne LLM:
  `GET /codegraph/{id}/hotspots` (`Graph::hotspots` in `cs-graph/src/hotspots.rs` —
  jede Fundstelle trägt, *welche* Regel sie gerissen hat und mit welchem Messwert;
  `churn=0` fällt auf das obere Dezil dieses Index) und
  `GET /codegraph/{id}/cycles?level=file|symbol` (`Graph::cycles` in
  `cs-graph/src/cycles.rs` — iterativer Tarjan-SCC, Dateien über `Imports`, Symbole
  über `Calls`, jeder Ring mit Belegkanten und schwächster Sicherheit; ein
  `build_*_adjacency`-Zweig pro Ebene). Vorschlag mit LLM:
  `POST /codegraph/{id}/refactor/{node_id}` (SSE, `codegraph/refactor.py`) verlangt
  JSON mit **vollständigem neuem Dateiinhalt** (keine Zeilenoperationen) und prüft
  Pfade (`resolve_within` — `../../etc/passwd` fällt) + Python-Syntax (`compile`)
  in `validate_proposal`, **bevor** etwas geschrieben wird. `POST …/refactor/{node_id}/try`
  wendet den geprüften Vorschlag in einer Sandbox an + läuft den Testbefehl (SSE);
  Übernahme ist der getrennte `…/sandboxes/{id}/apply`-Schritt. Systemprompt wörtlich
  in `codegraph/refactor.py:SYSTEM_PROMPT`.

Zwei Konventionen aus Stufe 2, die tragend sind: **Checkpoints** werden vor jedem
Schreiben gezogen (`auto_symbol_write`/`auto_file_write`/`auto_refactor`/
`auto_sandbox_apply`/`pre_restore`), und ein Fehlschlag blockiert das Schreiben
**nicht** — die Antwort trägt `checkpoint: null` und `checkpoint_reason`, und die UI
sagt es statt es zu verschweigen (git ist lokalisiert, deshalb Rückgabewerte, nicht
Meldungstexte — wie `git_log_for_lines`). Und **keine erfundene Gesamtnote** bei
Hotspots: jede Fundstelle führt ihre gerissene Regel und ihren Messwert, das
Belegprinzip auf Zahlen übertragen.

**Die Karte ist gerichtet, nicht kräftebasiert** (`columnLayout.ts`): wer aufruft steht links, was
aufgerufen wird rechts, das betrachtete Symbol in der Mitte. Bei einem Aufrufgraphen *ist* die Richtung
die Aussage; ein Knäuel, in dem man Pfeilspitzen einzeln absucht, beantwortet die Frage nicht, für die
man die Karte aufgemacht hat. Das Layout läuft synchron (eine Sortierung je Spalte, keine Simulation —
ein Worker brächte nur Verzögerung), ist deterministisch, und eine Spalte mit mehr als zwölf Knoten
zerfällt in Unterspalten *vom Fokus weg*, damit aus zweiundzwanzig Aufrufern kein fensterhoher Streifen
wird.

Drei weitere Dinge sind Messung, nicht Geschmack: die Karte fragt **nie `direction=both`**
(`Graph::slice` trägt im Both-Zweig auch eingehende Kanten als ausgehende ein,
`cs-graph/src/query.rs:411` — in einer Liste unauffällig, auf einer Pfeilkarte falsch; ausserdem wäre die
Seite eines Knotens dann gar nicht mehr feststellbar); ein Zusammenführen über `MAX_MAP_NODES = 600` wird
**ganz** verworfen statt halb angewandt, weil ein halb erweiterter Graph aussieht wie ein vollständiger;
und `contains` ist standardmässig aus (eine Datei enthält hunderte Symbole). Reine Helfer liegen in
`codeMap.ts`, `columnLayout.ts` und `summary.ts` und sind getestet.

**Die Regel des Werkzeugs: keine Kante ohne Beleg.** Jede Beziehung trägt ihre Quellzeile *und* ihre
Sicherheitsstufe — `verified` ● / `resolved` ◐ / `guessed` ○ / `measured` ◆ —, und was statisch nicht
auflösbar ist (Reflection, `eval`, DI), steht als `dynamic_gap` sichtbar im Graphen statt zu fehlen. Jede
Anzeige muss beides danebenstellen; ohne das ist eine geratene Kante von einer belegten nicht zu
unterscheiden, und das Werkzeug verliert seinen Zweck. Der Vermutungsanteil steht dauerhaft in der Tableiste.

- **Die Naht ist `cs serve`** (`codesearch/crates/cs-workspace/src/serve.rs`): NDJSON über stdin/stdout,
  eine Zeile rein, eine Zeile raus, Fortschritt als `{"event":"progress"}`-Zeilen dazwischen. Bewusst kein
  HTTP (kein Port, kein Auth, keine neue Rust-Abhängigkeit; das Kind stirbt mit dem Elternprozess).
  `Server::call` ist von der stdio-Schleife getrennt, damit die Dispatch-Tabelle testbar bleibt.
- **Python-Seite `codegraph/`**: `binary.py` (wo liegt `cs`), `rpc.py` (ein Kindprozess), `pool.py` (ein
  lebendes Kind je Projekt, Leerlauf-Räumung), `service.py` (Indizieren + Buchführung), `positions.py`.
  In `rpc.py` liest ein **Thread** stdout in eine Queue — ein blockierendes `readline` im Aufrufer hieße,
  dass ein hängendes Kind das Backend mitnimmt (derselbe Keil wie beim PDF-Parsen). Läuft ein Aufruf in den
  Timeout, wird der Client **beendet**: käme die Antwort später doch, würde sie dem *nächsten* Aufruf
  zugeordnet und das Protokoll wäre dauerhaft verschoben.
- **Indizes liegen unter `data/codegraph/<code_project_id>/index.csdb`**, nie im Repository des Nutzers.
  Dafür gibt es `Workspace::open_with_db` — `Workspace::open` verdrahtet `<root>/.codesearch/`, was in
  fremden Checkouts Müll hinterließe. Reiner Cache: gitignored, per `DELETE /codegraph/{id}/index` weg,
  **nicht** Teil von Projekt-Bundles (wie Kuzu). Eigene SQLite-Dateien, weil DuckDB genau einen Schreiber
  verträgt und diese hier ein Kindprozess schreibt; in `metadata.duckdb` steht nur die Buchführung
  (`code_indexes`, `code_paper_links`, `code_answers`).
- **Kantenarten und Symbolarten werden validiert, nicht durchgereicht.** `ALL_EDGE_KINDS`/`ALL_NODE_KINDS`
  in `api/routers/codegraph.py` spiegeln `cs-core/src/lib.rs:140-236`; `EdgeKind::from_str` verwirft auf
  der Rust-Seite still, was es nicht kennt, und fällt dann auf einen Standardsatz zurück — ohne die
  Prüfung sähe `?edges=call` aus wie ein Ergebnis. Die Routen `GET …/neighbours/{id}` (alle 14
  Kantenarten, `blueprint` kennt nur drei), `GET …/path?from=&to=` (kürzester **Aufruf**pfad; `null`
  heisst nicht „keine Beziehung", die Rekursion verfolgt nur calls/reads/writes) und `GET …/top?kind=`
  (auch route/db_table/test/config_key/dynamic_gap, die `overview` fest verdrahtet weglässt) machen
  erreichbar, was vorher indiziert und an der API-Grenze verworfen wurde.
- **Knoten-IDs sind immer Hex-Strings**, nie Zahlen. Es sind 64-Bit-blake3-Hashes; als JS-`number` verlieren
  sie stillschweigend ihre unteren Bits und zeigen auf ein anderes Symbol. Durchgesetzt in
  `cs-core/src/ids.rs` (eigenes `Serialize`/`Deserialize` statt `#[serde(transparent)]`), mit Test.
- **Die Belegprüfung bleibt in Rust.** `tool_call` (die 8 Graph-Werkzeuge), `context_build` und
  `verify_citations` teilen sich eine `Session` pro Gespräch — die *Lizenz zum Zitieren*: zitiert werden darf
  nur, was in derselben Sitzung nachgeschlagen wurde. Ein Modell könnte sonst eine echte Datei mit einer
  plausiblen Zeilennummer erfinden. Vier Prüfungen: Datei im Index? Zeilen nachgeschlagen? Datei seither
  unverändert? Wörtliches Zitat byte-gleich? (`cs-llm/src/citation.rs`, Tests in `tests/hallucination.rs`.)
- **Der Begleiter** (`codegraph/companion.py`, `POST /codegraph/{id}/ask` als SSE, Fragen-Tab in
  `CodeAskPanel.tsx` — Anbieter/Modell kommen aus `components/LlmPicker.tsx`, dieselbe Komponente wie in
  der Kopfzeile; „erbt global" sendet `null`, damit der Router bei `config.yaml` bleibt statt eine alte
  Wahl einzufrieren) fährt die Werkzeugschleife: `session_reset` → `context_build` → `tool_specs` →
  `LLMRouter.chat_with_tools` → `tool_call` → `verify_citations`. Systemprompt wörtlich aus
  `cs-llm/src/lib.rs`. Drei Dinge stehen dort aus Messung, nicht aus Geschmack: **Werkzeuge nur bei dünner
  Vorab-Suche** (`matched_by_name`/`symbols`) — mit vollständigem Kontext *und* Werkzeugen schlägt ein
  kleines lokales Modell trotzdem neunmal nach; **`MAX_ROUNDS = 3`**, weil jede Runde den gewachsenen
  Prompt neu verarbeitet; und die **Byte→UTF-16-Umrechnung** der Zitat-Versätze (`_utf16_offsets`), weil
  Rust in Bytes und JavaScript in UTF-16 zählt und die Beleg-Chips sonst um jedes Umlaut-Byte verrutschen.
  Ein Spur-Schritt auf nie abgerufenen Code wird **markiert, nicht entfernt**. Geprüfte Antworten landen
  in `code_answers` (`GET/DELETE …/answers`).
- **`LLMRouter.chat_with_tools`** ist die Schwestermethode zu `chat()` (die gibt `str` zurück und wird
  überall so benutzt — ein anderer Rückgabetyp wäre ein Bruch quer durchs Repo). Aufrufer schreiben immer
  **OpenAI-Nachrichten**; Anthropic (`tool_use`/`tool_result`-Blöcke, aufeinanderfolgende Ergebnisse in
  *einer* `user`-Nachricht) und Ollama (Argumente als Objekt, nicht als String) werden im Router
  übersetzt. 400/422 auf `tools` → einmal ohne wiederholen und `tool_calling_fallback` in
  `last_response_metadata` vermerken; ohne den Vermerk hielte der Aufrufer eine werkzeuglose Antwort für
  eine Entscheidung des Modells. Tests: `tests/test_llm_router_tools.py`.
- **Diagramme** (`class_diagram`/`sequence_diagram` in `serve.rs`, `GET …/diagram/{node_id}?kind=`,
  `CodeDiagramPanel.tsx` mit dynamisch importiertem mermaid). Ein Bild ist das Autoritativste, was das
  Werkzeug ausgeben kann — deshalb ist eine geratene Kante **gestrichelt** *und* steht zusätzlich in einer
  Liste mit ●◐○ und `datei:zeile`. Das Bild allein ließe sich nicht nachprüfen.
- **Papers ↔ Code**: `use_papers` im Fragen-Tab hängt `HybridRetriever`-Auszüge in den Prompt und schaltet
  `PAPERS_PROMPT` dazu. Papers werden mit `[arxiv:…]` zitiert, Code mit `pfad:zeile`; ein nacktes `[1]`
  wird gezählt und gemeldet (`bare_citations`), wie `CLAUDE.md` es verlangt. Eine Paper-ID, die nicht
  abgerufen wurde, zählt nicht als Zitat.
- **Code-Zitate in Notizen** ohne zweite Tabelle: `POST …/cite` schreibt nach `note_citations` mit
  `source_kind='code'`, der synthetischen `paper_id` `code:<projekt>:<pfad>:<zeile>` (die Spalte ist
  `NOT NULL`, alle bestehenden Leser laufen unverändert weiter) und dem `content_hash` der Datei zum
  Zeitpunkt des Zitierens. `GET …/citations?note_id=` vergleicht ihn mit dem aktuellen Stand → `stale`.
  Der Zeilenbereich gehört in die Zitat-ID, aber **nur wenn gesetzt** — sonst bekämen alle bestehenden
  Paper-Zitate neue IDs und das nächste Anhängen legte Dubletten an.
- **Terminal-Sprungmarken**: `codegraph/positions.py` (Port von `cs_pty::find_positions`, ohne Regex) hängt
  am selben Ausgabe-Puffer wie die Dev-URL-Erkennung in `WorkstationPage.tsx`. Die *Ablehnungen* sind der
  Inhalt — `14:30 Uhr`, `Verhältnis 3:1`, `107 Tests` dürfen keine Dateipositionen werden.
- **Angedockt**: der Desktop-Companion kennt `use_code` + `code_project_id` neben `use_papers`/`use_web`
  (`_companion_context` in `api/product_main.py`, Schalter im Overlay); die Analyse-Werkstatt nimmt
  `code_project_id` und bekommt Kennzahlen + wichtigste Symbole als Planer-Kontext
  (`_code_graph_context` in `api/routers/analysis.py`) — damit wird das Repository selbst zum Gegenstand
  einer Analyse. Beides fail-soft: ohne Binary oder Index gibt es eben keinen Zusatz.
- Ohne gebautes Binary ist nichts kaputt: `GET /codegraph/{id}` meldet `binary_available: false` samt
  Baubefehl, das Panel zeigt den Hinweis, der Rest der App läuft. Bauen:
  `python packaging/build_codesearch.py` (→ `src-tauri/sidecar/codesearch/`, als Tauri-Resource gebündelt).
  Der Build braucht **kein** libwebkit2gtk — die einzige Crate, die `tauri` zog, war CodeSearchs eigene
  Desktop-Schale, und die wurde nicht mit einvendoret.
- `codesearch/` ist ab dem Import die maßgebliche Kopie (Upstream hat kein git-Remote). Tests:
  `cargo test --manifest-path codesearch/Cargo.toml --workspace`, `tests/test_codegraph.py` und
  `tests/test_codegraph_companion.py` (beide überspringen sich selbst ohne Binary),
  `tests/test_llm_router_tools.py` (offline), `frontend/src/pages/CodeAskPanel.test.tsx` +
  `CodeDiagramPanel.test.ts` sowie in `pages/codegraph/` `codeMap.test.ts`, `columnLayout.test.ts`,
  `clusterLayout.test.ts`, `summary.test.ts` und `useCodeMap.test.tsx`.

### Datensätze (dataset registries)
Alongside papers, the app harvests **dataset references** from free registries via
`harvester/dataset_clients.py` (`search_datasets` aggregates Zenodo, Figshare, Dryad,
ClinicalTrials.gov, PapersWithCode — **fail-soft per source**, DOI-based landing URLs so the
user can inspect the raw data/licence). Only metadata + link/DOI/licence are stored (no bulk
downloads — privacy); table `datasets` in `storage/metadata_db.py`, routes `/datasets/search`,
`/datasets/import`, `GET /datasets[/{id}]`, `DELETE`. UI: the **Daten** tab in the Workspace
center column (`frontend/src/pages/DatasetsPanel.tsx`). Collected datasets can be passed to the
Analyse-Werkstatt as `dataset_ids` → their metadata becomes planner context.

### Tiefenanalyse LaTeX/PDF export
The deep-analysis "Gesamtantwort" can be exported to a thesis-/paper-style document via the
**PDF/LaTeX** button (backend `POST /research/tree/export`, package `export/`). It builds LaTeX
with title page, ToC, BibTeX `Quellenverzeichnis`, plus optional TikZ research-tree, matplotlib
charts, auto-tables and ComfyUI images. PDF compilation needs a LaTeX engine on PATH (`latexmk`/
`pdflatex`) — install **MiKTeX** (`winget install MiKTeX.MiKTeX`, auto-installs packages on demand).
Without an engine the endpoint gracefully returns a ZIP of `.tex`+`.bib`+figures instead (compile on
Overleaf). ComfyUI (port 8188) is optional and best-effort. Requires `matplotlib` in the `.venv`.

### Auto-Recherche (Quellen-Stufenleiter)
The Workspace **Auto-Recherche** toggle (and `/auto`) answers, and when the answer is weak escalates
**one source class at a time**, re-answering after each stage and stopping as soon as the answer holds
(`query/auto_answer.py::HARVEST_STAGES`): `scientific` (papers via the full source registry, real Phase-3
extraction) → `trusted` (institutions/universities/publishers) → `unverified` (rest of the web, and the
prompt makes the answer say so). Domain tiers come from **`research/source_tiers.py`** (built-in list plus
additive `research.trusted_domains` / `trusted_suffixes` in `config.yaml`); the tier rides on `SearchHit.tier`,
is stored in `grey_sources.trust_tier`, lowers the evidence score for unverified sources
(`query/grounded_responder.py`) and changes the `(Webquelle · …)` marker in the prompt
(`query/grounded_helpers.py`). `query/research_tree.py` uses the same ordering without a per-stage re-answer.

### Desktop-agent hand-off (Parallelmodus)
In the Workspace Parallel mode, each *Variante* in the Notes "Ergebnisse" tab has an
**„An Desktop-Agent übergeben"** button. The backend (`query/agent_handoff.py`,
`POST /parallel/variants/{id}/handoff`) compiles the variant + grounded context into a
**task brief** (goal/steps/constraints/success criteria) and renders it as one copy-/POST-
ready instruction. PaperKG stays the *brain*; it never drives the machine itself. Two
channels: **Kanal A** (copy the brief into [UI-TARS-Desktop](https://github.com/bytedance/UI-TARS-desktop)
manually — always available) and **Kanal B** (`POST /agent/dispatch` SSE → the optional
local bridge in `bridge/uitars/`, which runs `@ui-tars/sdk` against a local VLM and streams
progress back as a variant entry). Off by default; enable via the `agent_bridge:` block in
`config.yaml`. The VLM (e.g. `ui-tars-1.5-7b`) is served by your existing LM Studio/Ollama
provider — this legacy bridge path never goes through `LLMRouter`. (The router *does* speak
vision since R6, but only for the Desktop Companion's `/companion/*` endpoints.)

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
- A project's **id is its name** (`data/projects.json` is `{name: [paper_ids]}`). Renaming therefore moves every
  project-scoped row: `PATCH /projects/{id}` calls `MetadataDB.rename_project`
  (`storage/metadata_db/project_scope.py`, one UPDATE per table in `PROJECT_SCOPED_TABLES`) and migrates the
  sidecars `data/project_primary.json` / `data/project_meta.json` (pinning). Anything else keyed by `project_id`
  must be added to that tuple. The sidecar helpers resolve their path at call time so tests can redirect them.
  **Order matters:** migrate the DB and sidecars *first*, write `projects.json` last — the reverse leaves the
  project renamed while its `grey_sources`/`notes` still point at the old id if the DB call fails.
- A paper without a downloadable PDF is still useful: its abstract feeds **abstract-only extraction**
  (`api/routers/extraction.py::_abstract_only_extraction_text`, an unconditional fallback when PDF resolution
  404s) and `landing_page_url`/`doi` stay the durable link to the original (`_external_paper_url` in
  `api/routers/harvest.py`, `externalPaperUrl` in `frontend/src/paperLinks.ts`). Keep normalizers filling both
  fields; `pdf_url` is overwritten with the local path after a download and is *not* a reliable external link.
  Everything that counts "how many papers can I extract?" goes through **`frontend/src/extractionCounts.ts`** —
  one formula for the page badge, the batch panel and the pipeline tile, because two independent counts (PDF-only
  vs. PDF+abstract) read as a contradiction.
- **PDF-Parsing läuft im Kindprozess** (`parsing/pdf_guard.py` + `parsing/pdf_child.py`). Eine einzelne Seite
  mit grosser Vektor-Grafik lässt pdfplumber *und* pypdf unbegrenzt Speicher allokieren, ohne je fertig zu
  werden (gemessen an einem 19-MB-EuropePMC-PDF: ~11 MB/s, kein Ende) — im Backend-Prozess endete das in
  10 GB RSS, totem uvicorn-Listener und OOM. `MarkerParser.parse()` ist deshalb nur noch die Schutzhülle,
  der echte Parser heisst `parse_direct()`. Grenzen kommen aus dem `parsing:`-Block in `config.yaml`: das
  RAM-Budget skaliert mit dem *freien* Speicher (`memory_fraction`, geklemmt zwischen `memory_min_mb` und
  `memory_max_mb`), der Timeout ist absichtlich gross (`pdf_timeout_seconds`, Default 40 min), weil auf
  langsamer Hardware auch legitime PDFs lange brauchen. Jede fertige Seite wird sofort in eine JSONL-Datei
  geschrieben, damit ein abgeschossenes Kind nicht alles verliert (Seiten 0–21 bleiben, wenn Seite 22 hängt).
  Bei `sys.frozen` (PyInstaller-Sidecar) gibt es keinen `-m`-Start, dort wird ungeschützt geparst.
  **Nicht** `multiprocessing` verwenden: `spawn` importiert im Kind das `__main__` des Elternprozesses neu —
  unter uvicorn ist das dessen CLI-Modul.
- **`POST /extraction/batch` legt die `batch_jobs`-Zeile sofort an**, bevor PDFs aufgelöst werden. Das
  Frontend erzeugt die `job_id` selbst (`ExtractionPage.tsx`) und pollt `/extraction/batch/{id}/items` ab
  dem Abschicken; wird die Zeile erst in `process_papers` geschrieben, antwortet jeder Poll bis dahin 404.
- **LLM failures are classified in `query/llm_errors.py`** (`quota | rate_limit | auth | context_length |
  connection | empty | unknown`). The kind rides through `batch_job_items.error_message` /
  `extraction_results.error_message` as a `"[llm:<kind>] …"` prefix (`tag_error`/`parse_tagged_error`, mirrored in
  `frontend/src/llmErrors.ts`) — no schema column needed. `BatchProcessor` **stops** the run on
  `quota`/`rate_limit`/`auth` and leaves the remaining items `pending`; without that, one exhausted quota burned
  through every selected paper. `LLMRouter._http_status_runtime_error` must keep putting `HTTP <status>` in the
  message, otherwise the classifier has nothing to match on.
- **Projekt-Bundles** (`graph_bundle/`, routes in `api/routers/graph_bundle.py`): a project as a portable ZIP —
  `manifest.json` + one JSONL per table + optional PDFs. `GET /projects/{id}/export`, `POST /bundles/preview`
  (dry run), `POST /bundles/import` (`merge`|`replace`). Kuzu is deliberately *not* in the bundle: it is a cache,
  rebuilt via `POST /jobs/graph-rebuild`. Imports are idempotent — extractions dedupe on a content fingerprint,
  not on `extraction_timestamp` (which `save_extraction_result` reassigns). Every ZIP entry goes through
  `safe_member_path` before anything is written; never `extractall` a bundle.
- A graphify `hook-check` runs on every Bash call (`.codex/hooks.json`); `.codex/` is gitignored and unrelated
  to your task — let the hook run, don't modify it.
