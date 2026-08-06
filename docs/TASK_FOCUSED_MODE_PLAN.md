# Task-Focused Mode — Implementation Plan

Status: **in progress**  
Begonnen: 2026-08-06  
Safety-Commit (Pre-Feature): `c0f5a80` — `checkpoint: pre-task-focused-mode`

Rollback jederzeit möglich: `git reset --hard c0f5a80`

---

## Ziel

Workspace-Konzept (Arbeitsplatz, Route `/workspace`, `WorkspacePage.tsx`)
erhält Modus-Umschalter: **Allgemeine Recherche** (Status quo) ↔ **Task-Focused**
(neu). Task-Focused extrahiert aus URL/PDF/Freitext einen strukturierten
Task-Spec (Kaggle-Competition, Hackathon-Ticket, eigene Anweisung), schlägt
auto Forschungsrichtungen vor, kurbelt Tiefenanalyse + Parallelmodus mit
Task-Bezug an, lädt zitierte Quellen auto-herunter, bietet interaktive
"Wie umsetzen"-Guidance (Professor-Metapher). Neue freie Dataset-Quellen
(Kaggle, Hugging Face, OpenML, UCI, Mendeley Data). Notizen + PDF-Nachweis
standardmäßig eingeklappt.

## Design-Entscheidungen (vom User bestätigt)

- **Modus-Toggle = Workspace-Level** (gleiches Projekt, Modus umschaltbar,
  persistiert per-project in localStorage `sciencekg.workspace.mode.{projectId}`).
- **Kaggle-Login = Popup-Flow** (Anleitung + Upload-Feld für kaggle.json /
  username+key; Credentials nur in `.env` via `KAGGLE_USERNAME`/`KAGGLE_KEY`,
  gitignored; graceful Degradation bei fehlendem Key → nur Public-Scrape).
- **Creativity-Slider 5 Stufen** (1=konservativ Mainstream zuerst, 5=aggressiv
  cross-domain, non-obvious). Persistiert per-project in `project_meta.json`.
- **"Wie umsetzen"-Output = interaktiv** (Professor-Metapher): pro Variante
  Text-Guidance-Karte mit Schritten, User kann eigene Schritte hinzufügen,
  Ergebnisse zeigen, ablehnen, nachfragen. Implementationsplan = Summe aller
  akzeptierten Schritte. Optionale Code-Snippets + Agent-Handoff später
  erweiterbar.
- **Mendeley Data** (Catalog-Search, niedrig priorisiert, nicht Reference-Manager).

## Session-Aufteilung (4 Sessions)

### Session 1 — Backend Kern (Task-Spec + Tiefenanalyse-Integration)

Status: **in progress**

- [x] Safety-Commit `c0f5a80`
- [x] Plan nach `docs/TASK_FOCUSED_MODE_PLAN.md` schreiben
- [x] `tasks`-Tabelle + `storage/metadata_db/tasks.py` + schema + project_scope
- [x] `api/routers/tasks.py` + Include in `product_main.py`
- [x] `query/task_extractor.py` (LLM-Extraction URL/PDF/Text → Spec)
- [x] `query/task_research_suggester.py` (4-6 Richtungen aus Spec + KG)
- [x] `query/task_implementation_planner.py` (Synthese Plan aus KG + Spec)
- [x] `discovery.py`: `/research/tree` + `/research/clarify` akzeptieren
      `task_id`/`task_mode`; tree injiziert Task-Kontext + aktiviert auto_harvest
      bei `task_mode='auto_download'` (auto-Download ziterter Quellen via bestehendem
      `auto_harvest`-Pfad des Runners)
- [x] `grey_sources.py`: Support `source_kind='task'` (Task-Spec zitierbar als
      `grey::task_{id}` via `POST /tasks/{id}/as-grey-source`)
- [x] `phase4_main.py`: `AnswerRequest.task_id` (optional, injiziert Task-Spec
      als Inline-Kontext + ergänzt `grey_task_{id}` in den Antwort-Kontext)
- [x] Tests + commit

Session 1 abgeschlossen: 871 Tests grün, ruff clean, black-formatiert.
Auto-Download ziterter Papers läuft über den bestehenden `auto_harvest`-Pfad
des `ResearchTreeRunner` (Session 1 keeps it minimal; tiefere Integration folgt
in Session 3 mit dem Parallelmodus).

**Dateien Session 1:**

NEU:
- `api/routers/tasks.py`
- `query/task_extractor.py`
- `query/task_research_suggester.py`
- `query/task_implementation_planner.py`
- `storage/metadata_db/tasks.py`
- `tests/test_tasks.py`

MODIFY:
- `api/product_main.py` (Router-Include)
- `api/routers/discovery.py` (`task_id`, `task_mode`)
- `api/routers/grey_sources.py` (`source_kind='task'`)
- `api/phase4_main.py` (`AnswerRequest.task_id`)
- `storage/metadata_db/schema.py` (`tasks`-Tabelle)
- `storage/metadata_db/__init__.py` (`TasksMixin`)
- `storage/metadata_db/project_scope.py` (`tasks` zu
  `PROJECT_SCOPED_TABLES`)

### Session 2 — Dataset-Quellen + Kaggle-Login

- [x] `harvester/kaggle_client.py` (auth via `.env`, Competition- + Dataset-Download)
- [x] `harvester/huggingface_datasets_client.py` (frei, optional Token)
- [x] `harvester/openml_client.py` (frei)
- [x] `harvester/uci_client.py` (scrape)
- [x] `harvester/mendeley_client.py` (Catalog-Search, niedrig priorisiert)
- [x] `harvester/source_registry.py` (neue Quellen via `DATASET_SOURCE_IDS`)
- [x] `config.yaml` (neue harvester-Sections mit `api_key_env`)
- [x] `api/routers/datasets.py` (neuer Dispatch + Download-Endpoint + Source-Status)
- [x] `.env.example` (`KAGGLE_USERNAME`, `KAGGLE_KEY`, `HF_TOKEN`)
- [x] `api/routers/settings.py` (Kaggle-Login-Endpoint `POST /settings/kaggle`)
- [x] Tests + commit

Session 2 abgeschlossen: 892 Tests grün (21 neu), ruff clean, black-formatiert.
Neue Quellen: Kaggle (Competitions + Datasets, auth-pflichtig), Hugging Face
(optional Token), OpenML (frei), UCI (frei), Mendeley Data (frei).
Kaggle-Login schreibt Credentials in gitignored `.env`, lädt sie in den
laufenden Prozess — Download sofort möglich ohne Backend-Neustart.

### Session 3 — Parallelmodus + Interaktive Steps

- [x] `parallel.py`: `ParallelStartRequest.task_id` + `creativity_level` (1-5)
- [x] `query/parallel_research.py`: Task-Spec-Injection in `propose_overview`,
      `propose_stages`, `propose_variants`; Slider-Logik (konservativ→aggressiv)
- [x] `parallel_variants.user_steps` JSON-Spalte (schema migration)
- [x] Interaktive Step-Aktionen: `POST /parallel/variants/{id}/steps`,
      `PATCH/DELETE /parallel/variants/{id}/steps/{step_id}`,
      `POST /parallel/variants/{id}/steps/{step_id}/result`
- [x] "Weg nichts für mich" = Variant `rejected` + Grund
- [x] Tests + commit

Session 3 abgeschlossen: 912 Tests grün (20 neu), ruff clean, black-formatiert.

### Session 4 — Frontend

- [ ] `frontend/src/types.ts`: `TaskSpec`, `Task`, `WorkspaceMode`,
      `CreativityLevel`, `TaskResearchDirection`
- [ ] `frontend/src/state.tsx`: `workspaceMode`, `creativityLevel` in
      `AppStateContext` (per-project localStorage)
- [ ] `frontend/src/pages/TaskFocusedPane.tsx` (Hauptoverlay-Container)
- [ ] `frontend/src/pages/TaskSpecCard.tsx` (Spec-Karte, einklappbar, editierbar)
- [ ] `frontend/src/pages/TaskIngestDialog.tsx` (URL/PDF/Text-Eingabe)
- [ ] `frontend/src/pages/TaskResearchSuggestions.tsx` (Forschungsrichtungen)
- [ ] `frontend/src/pages/CreativitySlider.tsx` (wiederverwendbar)
- [ ] `frontend/src/pages/KaggleLoginDialog.tsx` (Popup-Anleitung + Upload)
- [ ] `frontend/src/pages/WorkspacePage.tsx`: Modus-Toggle im Header, rendere
      `TaskFocusedPane` wenn `workspaceMode==='task'`, Navigator-Tabs
      Default-collapsed in Task-Modus
- [ ] `frontend/src/pages/ResearchTreeView.tsx`: Task-Modus-Badge +
      Auto-Download-Status-Toast
- [ ] `frontend/src/pages/ParallelResearchPanel.tsx`: Creativity-Slider +
      "Wie umsetzen"-Karten pro Variante + interaktive Step-Aktionen
      (+eigener Schritt, "Das probiere ich", "Ergebnis zeigen", "Weg nichts",
      "Frage an Professor")
- [ ] `frontend/src/pages/SettingsPage.tsx`: "Kaggle verbinden"-Button +
      Dialog-Trigger
- [ ] `frontend/src/pages/DatasetsPanel.tsx`: Neue Quellen im Filter
- [ ] `frontend/src/api.ts`: `tasks/*`-Bindings, `kaggleLogin`-Binding,
      `creativityLevel`-Binding, neue Dataset-Quellen
- [ ] `npm run build` (TS typecheck) + `npm test` + commit

## Schema-Änderungen

### Neue Tabelle `tasks` (DuckDB, projekt-scoped)

```sql
CREATE TABLE IF NOT EXISTS tasks (
  id              VARCHAR PRIMARY KEY,
  project_id      VARCHAR NOT NULL,
  title           VARCHAR NOT NULL,
  source_kind     VARCHAR,            -- 'url' | 'pdf' | 'text'
  source_url      VARCHAR,
  source_pdf_path VARCHAR,
  task_json       JSON,               -- voller TaskSpec
  created_timestamp TIMESTAMP NOT NULL,
  updated_timestamp TIMESTAMP NOT NULL
);
```

`task_json` Struktur:
```json
{
  "title": "RSNA Knee Abnormality Detection",
  "objective": "...",
  "evaluation": "...",
  "datasets": [
    {"name": "...", "install_url": "...", "size": "...", "license": "..."}
  ],
  "timeline": {"start": "...", "end": "..."},
  "rules": ["..."],
  "constraints": ["..."],
  "suggested_directions": [
    {"label": "...", "rationale": "...", "keywords": ["..."]}
  ]
}
```

### Migration `parallel_variants.user_steps`

```sql
ALTER TABLE parallel_variants ADD COLUMN IF NOT EXISTS user_steps JSON;
```

`user_steps` Struktur:
```json
[
  {"id": "...", "label": "...", "status": "pending|in_progress|done|rejected",
   "result_note": "...", "result_attachment_id": "...", "rejected_reason": "..."}
]
```

## Konventionen (aus CLAUDE.md / AGENTS.md einhalten)

- LLM-Zugriff **immer** via `LLMRouter.from_config_file("config.yaml")`,
  nie direkter SDK-Call.
- Zitierformat: lokale Paper-IDs `[arxiv:...]`, Grey-Sources `grey::...`,
  Task-Spec `grey::task_{id}`. Keine bloßen `[1]`.
- Neue Persistenz → Tabelle in `storage/metadata_db` (nicht neuer Store).
- Projekt-scoped → in `PROJECT_SCOPED_TABLES` (`project_scope.py`) aufnehmen,
  Rename migriert automatisch.
- `pdf_url` wird nach Download mit lokalem Pfad überschrieben → nicht als
  externer Link nutzen; `landing_page_url`/`doi` sind dauerhaft.
- PDF-Parsing im Child-Process (`parsing/pdf_guard.py`), kein `multiprocessing`.
- `InstanceLock`: nur ein Backend gleichzeitig.
- `POST /extraction/batch` schreibt `batch_jobs`-Row sofort vor PDF-Resolve.
- LLM-Fehler klassifiziert (`query/llm_errors.py`); BatchProcessor stoppt bei
  `quota`/`rate_limit`/`auth`.
- Config: `api_key_env:`-Pattern, nie inline. `.env` gitignored.
- SSRF-Guard (`harvester/url_guard.py`) auf jedem externen Fetch.

## Offene Punkte / Risiko

- **Kaggle ToS**: Competition-Daten sind competition-gebunden, nur für
  Teilnehmer. Download nur nach Login + Regeln-Check. Dokumentieren im UI.
- **HF Rate Limits**: ohne Token niedrige Rate, Token optional. Hinweis im UI.
- **Cross-Domain Creativity**: bei Stufe 5 sucht LLM in fremden Fachgebieten —
  Ergebnisse können irrelevant sein, aber User kann ablehnen. Default-Stufe
  für neue Projekte: 3 (Mitte).
- **Auto-Download in Tiefenanalyse**: kann viele Papers ziehen → Rate-Limits,
  Speicher. Begrenzen (max 20 Papers auto-import pro Tiefenanalyse-Run,
  konfigurierbar in `config.yaml`).

## Status-Tracking

Nach jeder Session: Update dieses Files (`- [x]` setzen, Status-Zeile oben
ändern). Commit-message-Format: `task-focused: session N — <thema>`.

Letzter Stand: **Session 3 abgeschlossen** — Parallelmodus + interaktive
Steps (Task-Spec-Injection in `propose_overview`/`propose_stages`/
`propose_variants`, 5-Stufen-Creativity-Slider mit temperaturgeführtem
Override, `parallel_variants.user_steps` JSON-Spalte, Step-Aktionen
hinzufügen/patchen/löschen/Ergebnis-zeigen, "Weg nichts"-Reject mit Grund,
Implementationsplan-Export, Task-ID-Bindung der Session). 912 Tests grün.
Nächste Session: Session 4 (Frontend).