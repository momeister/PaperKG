# Native Desktop-App (Tauri)

Dieses Dokument ist der **lebende Umsetzungs-/Statusplan** für die Umstellung der ScienceKG/PaperKG-
Web-App auf ein **natives Desktop-Programm** (Windows zuerst, dann Linux, optional Mac). Es wird über
Sessions hinweg fortgeschrieben — beim Erledigen eines Bausteins hier den Status aktualisieren.

> Vollständiger Architektur-Plan inkl. Roadmap-Begründung: `~/.claude/plans/vivid-popping-harbor.md`.

## Idee in einem Satz

Frontend (React/Vite) und Backend (FastAPI, `api.product_main:app`) bleiben **unverändert in Aussehen &
Verhalten**. Eine schlanke **Tauri-2-Hülle (Rust)** öffnet ein natives Fenster, startet das Python-Backend
als **Sidecar** (Kindprozess) und injiziert dessen Adresse ins Frontend. Spätere Features (Desktop-AI-
Overlay, Terminal, Jupyter, Editor) kommen als **weitere Sidecars + Fenster** dazu, ohne das Bestehende
umzubauen.

## Wie es zusammenspielt

```
src-tauri/src/lib.rs (Rust)
 ├─ pick_free_port()                  → freier localhost-Port
 ├─ start_backend(port)               → .venv-Python: uvicorn api.product_main:app  (kein --reload)
 ├─ wait_until_ready(port, 40s)       → wartet auf TCP-Accept (Kaltstart ~2-3s)
 ├─ initialization_script             → setzt window.__API_BASE__ = "http://127.0.0.1:<port>"
 ├─ WebviewWindowBuilder "main"       → lädt index.html (Asset-Protokoll im Build, Vite-Dev-Server bei `tauri dev`)
 └─ RunEvent::ExitRequested/Exit      → Backend-Prozess killen (kein verwaister uvicorn)
```

- Das Frontend liest die Backend-Adresse in `frontend/src/api.ts` → `resolveApiBaseUrl()`:
  1. `window.__API_BASE__` (von Tauri injiziert),
  2. `VITE_API_BASE_URL` (manueller Override),
  3. Dev-Fallback `http://127.0.0.1:8000` (reines Web-Dev, unverändert).
- Alle API-Calls laufen ohnehin zentral über `API_BASE_URL` → eine Stelle genügt.
- Backend-CORS (`api/product_main.py`) erlaubt zusätzlich die Tauri-Origins
  (`tauri://localhost`, `http(s)://tauri.localhost`).

## Voraussetzungen (Windows, einmalig)

- **Rust-Toolchain** (`rustup`, MSVC Build Tools) — vorhanden (`cargo 1.93`).
- **WebView2-Runtime** — auf Windows 11 vorinstalliert.
- **Node/npm** — vorhanden.
- **Tauri-CLI** — als Dev-Dependency im Root-`package.json` (`@tauri-apps/cli`); einmal `npm install` im Repo-Root.
- Python-Umgebung (`.venv`) wie bisher (für den Backend-Sidecar in M1).

## Voraussetzungen (Linux / macOS)

Diese werden für M3 gebraucht — lokal gebaut wird auf der Windows-Maschine nicht, die echten Linux-/Mac-
Artefakte entstehen in der CI (siehe „M3 — Linux/Mac via CI"). Wer es nativ auf einer Linux-/Mac-Box
selbst baut, braucht:

- **Linux (Debian/Ubuntu):** `libwebkit2gtk-4.1-dev`, `libappindicator3-dev` (Tray-Icon),
  `librsvg2-dev`, `patchelf`, `build-essential`, plus `curl`/`wget`/`file`. WebView = **WebKitGTK**
  (nicht WebView2). Rust-Toolchain via `rustup`, Node/npm, Python 3.10+ für den Backend-Sidecar.
- **macOS:** **Xcode Command Line Tools** (`xcode-select --install`), Rust-Toolchain, Node/npm,
  Python 3.10+. WebView = WKWebView (system). Transparente Fenster fürs AI-Overlay brauchen
  `macOSPrivateApi: true` in `tauri.conf.json` (ist in der Overlay-Ausbaustufe gesetzt).
- **Bundle-Targets je OS:** Windows `nsis`, Linux `deb` + `appimage`, macOS `dmg`. Die CI-Matrix setzt
  das passende Target je Runner; das Resource-Globbing (`defaults/*`, `sidecar/**`) ist OS-neutral.

## Befehle

```powershell
# Einmalig: Tauri-CLI installieren (Root-package.json)
npm.cmd install

# Native App im Dev-Modus (startet Vite + Backend-Sidecar + natives Fenster)
npm.cmd run tauri dev

# Natives Build/Bundle (nutzt frontend/dist; Backend-Sidecar siehe M2-Hinweis)
npm.cmd run tauri build
```

`npm run tauri dev` ist **self-contained**: Tauri startet via `beforeDevCommand` den Vite-Dev-Server und
der Rust-Code startet zusätzlich den Python-Backend-Sidecar. Man muss `scripts/run_product.py` dafür
**nicht** separat laufen lassen.

- **UI-Iteration:** Vite-HMR ist aktiv (Fenster lädt den Dev-Server) → Änderungen am Frontend sofort sichtbar.
- **Backend-Iteration:** Der Sidecar läuft **ohne `--reload`**. Für schnelles Backend-Hot-Reload weiterhin
  parallel `python scripts/run_product.py --api-only` nutzen und im Fenster die DevTools/Reload verwenden,
  oder die App neu starten.

Die reine Web-App (Browser) funktioniert unverändert über `python scripts/run_product.py`.

## Status-Tracker

Legende: ⬜ offen · 🟡 in Arbeit · ✅ fertig & verifiziert

| # | Baustein | Status | Ort |
|---|----------|--------|-----|
| M1.1 | Frontend: dynamischer API-Origin (`window.__API_BASE__`) | ✅ | `frontend/src/api.ts` |
| M1.2 | Backend: CORS um Tauri-Origin erweitert | ✅ | `api/product_main.py` |
| M1.3 | Tauri-Projekt angelegt (Root-`package.json`, `src-tauri/`) | ✅ | `package.json`, `src-tauri/` |
| M1.4 | Sidecar start/stop + Port-Injektion (Rust) | ✅ | `src-tauri/src/lib.rs` |
| M1.5 | Frontend-Build eingebunden (`frontendDist`) | ✅ | `src-tauri/tauri.conf.json` |
| M1.6 | Downloads/Export im WebView (Blob-Muster) | ✅ geprüft (Code) | `frontend/src/download.ts`, `api.ts` |
| M1.7 | Dev-Workflow + Doku | ✅ | dieses Dokument, `CLAUDE.md` |
| M1.8 | Externe Web-Links → OS-Browser | ✅ | `frontend/src/native.ts`, `src-tauri/src/lib.rs` |
| M1.9 | Library-PDF inline in der App (Reuse `PdfPane`) | ✅ | `frontend/src/pages/LibraryPage.tsx`, `styles.css` |
| M1.V | Verifikation: `tauri dev` läuft, Fenster↔Sidecar live | ✅ | — |
| M2   | Standalone-Installer (PyInstaller-Sidecar + Tauri-Bundle) | 🟡 Installer gebaut+getestet; Clean-Install offen | `packaging/`, `src-tauri/` |
| M3   | Linux (WebKitGTK) + optional Mac bauen/verifizieren | 🟡 per CI gebaut (Phase F: `native-build.yml`, win/ubuntu/macos); Real-Hardware-Smoke offen | `.github/workflows/native-build.yml` |
| R1   | Desktop-AI-Overlay (transparent/always-on-top, Hotkey, Tray) | ✅ (native; Hotkey `Ctrl/Cmd+Shift+Space` + Tray; reuse UI-TARS-Handoff) | `src-tauri/src/overlay.rs`, `src-tauri/src/lib.rs`, `frontend/src/pages/OverlayPage.tsx` |
| R2   | Eingebettetes Terminal (PTY) | ✅ (native; portable-pty + xterm.js) | `src-tauri/src/terminal.rs`, `frontend/src/components/WerkstattTerminal.tsx` |
| R3   | Jupyter als Sidecar + Tab | ⬜ | `src-tauri/`, Frontend-Tab |
| R4   | Code-Editor (Monaco) | ✅ (Werkstatt-Tab, offline gebündelt) | `frontend/src/pages/WorkstationPage.tsx`, `frontend/src/monaco-setup.ts` |

## Verifikation M1 (Stand)

- ✅ `npm.cmd --prefix frontend run build` — Typecheck inkl. `window.__API_BASE__` + Vite-Build grün.
- ✅ `python -m pytest tests/test_product_api.py -q` — 21 passed (CORS-Änderung unkritisch).
- ✅ `cargo build --manifest-path src-tauri/Cargo.toml` — Rust-Shell kompiliert.
- ✅ `npm.cmd run tauri dev` — Fenster öffnet, Backend-Sidecar startet automatisch auf freiem Port,
  das Frontend ruft ihn über den injizierten `window.__API_BASE__` live auf (beobachtet: `GET /projects`,
  `/models/providers`, `/system/health-report`, `POST /models/lm_studio/discover` → alle `200 OK`).
- ☐ Verbleibende manuelle Kür beim normalen Arbeiten: grounded Antwort streamt, Parallelmodus-Variante,
  Tiefenanalyse-**Export** lädt eine Datei, PDF-Viewer rendert; **nach Schließen kein verwaister
  python/uvicorn-Prozess** (Close-Handler ist verdrahtet: `RunEvent::ExitRequested | Exit → kill_backend`).

## M2 — Standalone-Installer (PyInstaller-Sidecar + NSIS)

Ziel: ein Windows-Installer, der **ohne vorinstalliertes Python** läuft. Das Backend wird per
**PyInstaller** (one-dir) als eigenständiges Programm verpackt und als Tauri-Resource gebündelt;
die Shell startet im Release-Build dieses Binary statt der `.venv`.

**Bausteine (implementiert):**
- `packaging/sidecar_entry.py` — PyInstaller-Einstiegspunkt: importiert `api.product_main:app` und
  serviert es per `uvicorn.run(app)` (kein `python -m`, kein `--reload`).
- `packaging/sidecar.spec` — one-dir-Spec; `collect_all` für torch/sentence-transformers/transformers/
  duckdb/kuzu/pdfplumber/matplotlib (jeweils guarded), `collect_submodules` für die eigenen Pakete +
  uvicorn-Protokolle; `excludes` für streamlit/celery/redis/pyvis/tkinter/Test-Tooling.
- `packaging/build_sidecar.py` — Build-Wrapper → Ausgabe `src-tauri/sidecar/sciencekg-backend/`. Hat
  einen **Preflight**: bricht laut ab, wenn Voll-Bundle-Deps (torch …) fehlen, statt heimlich ein
  schlankes Bundle zu bauen. `SCIENCEKG_BUNDLE_LEAN=1` erzwingt das schlanke hash-fallback-Bundle.
- `requirements-build.txt` — Build-Deps (`pyinstaller`).
- `src-tauri/defaults/{config.yaml,ontology.yaml}` — Seed-Vorlagen (env-referenzierte Keys, keine Secrets).
- `src-tauri/src/lib.rs` — Sidecar-Wahl nach Build-Profil: **Debug** = `.venv`-Python (CWD=Repo-Root,
  wie M1); **Release** = gebündelte Exe aus dem Resource-Verzeichnis, **CWD = `app_data_dir()`**
  (`%APPDATA%/com.sciencekg.desktop`); beim ersten Start werden `config.yaml`/`ontology.yaml` aus
  `defaults/` dorthin geseedet, `data/` wird angelegt. So funktionieren alle CWD-relativen Pfade des
  Backends unverändert — keine Python-Pfadänderung nötig.
- Bundle-Config liegt **getrennt** in `src-tauri/tauri.bundle.conf.json` (targets=`nsis`, `resources`
  = `defaults/*` + `sidecar/sciencekg-backend/**/*`). Sie wird **nur** beim Bauen via `--config`
  gemergt, damit die `tauri dev`-Build-Validierung nicht das (erst zur Build-Zeit existierende)
  Sidecar-Glob verlangt. Convenience: `npm run tauri:build`.

**Bauen (Voll-Bundle, einmalige Voraussetzungen):** torch ist im Repo-`.venv` standardmäßig **nicht**
installiert (der Default ist `embedding.backend: hash-fallback`). Für das Voll-Bundle zuerst:
```powershell
.venv\Scripts\pip install -r requirements.txt -r requirements-build.txt
.venv\Scripts\pip install torch --index-url https://download.pytorch.org/whl/cpu  # CPU-Wheel
npm.cmd run tauri:build   # baut Sidecar (beforeBuildCommand) + Frontend + NSIS-Installer
```
Ausgabe: `src-tauri/target/release/bundle/nsis/`. Bundle wird wegen torch groß (~1–2 GB).

**Build-Stolpersteine (gelöst, im Bau verifiziert):**
- **Metadaten:** duckdb (u. a.) liest beim Import seine Version via `importlib.metadata`; `collect_all`
  kopiert die `.dist-info` **nicht** → `PackageNotFoundError`. Gelöst per `copy_metadata(...)` in der Spec.
- **Build-Env unvollständig:** Das Build-`python` muss **alle** Runtime-Deps haben (PyInstaller bündelt
  nur Installiertes). Der `CORE_RUNTIME_DEPS`-Preflight fängt das jetzt vor dem Bau ab. Hinweis: das
  globale Python 3.10 hier hatte torch, aber nicht duckdb/sentence-transformers — beide nachinstalliert.
- **matplotlib-Pin:** `requirements.txt` pinnt `matplotlib==3.11.0` (braucht Py ≥ 3.11), Build-`python`
  ist 3.10 → für 3.10 eine kompatible matplotlib (3.10.x) nutzen, oder Build auf Py 3.11+ stellen.
- **Größe:** `transformers` zieht transitiv tensorflow/keras (~1 GB) — via `excludes` raus → Bundle
  **~2,4 GB → ~1,44 GB**.

**Verifikation M2 (Stand):**
- ✅ `cargo check --manifest-path src-tauri/Cargo.toml` — neue Rust-Shell (Bundle-Branch inkl.
  `app_data_dir`/`resolve`/`BaseDirectory`) kompiliert; `tauri dev` bleibt resource-frei lauffähig.
- ✅ `python -m pytest tests/test_product_api.py -q` — 21 passed (Backend-Logik unverändert).
- ✅ `npm --prefix frontend run build` — Frontend baut.
- ✅ `python packaging/build_sidecar.py` — PyInstaller one-dir baut sauber (~1,44 GB).
- ✅ **Standalone-Sidecar-Smoke:** `sciencekg-backend.exe --port <p> --data-dir <leer>` aus einem leeren
  Datenverzeichnis (nur geseedete `config.yaml`/`ontology.yaml`) → `GET /health`, `/projects`,
  `/papers` (echte **duckdb**-Query) und `/models/providers` alle **200**; Prozess endet ohne Orphan.
- ✅ **`npm run tauri:build`** → NSIS-Installer gebaut:
  `src-tauri/target/release/bundle/nsis/ScienceKG_0.1.0_x64-setup.exe` (**367 MB**, LZMA-komprimiert aus
  dem ~1,44-GB-Bundle). Pipeline: Sidecar-Rebuild → Frontend → Rust-Release → makensis, alles grün.
- ☐ **Offen (nur noch der „Clean-Machine"-Test, Nutzer-Schritt):** Den Installer auf einem System **ohne**
  Repo/.venv installieren und starten; Kernfeatures + Export + PDF testen; Daten landen in
  `%APPDATA%/com.sciencekg.desktop` (config/ontology geseedet, DuckDB unter `data/`); nach App-Schließen
  kein verwaister `sciencekg-backend`-Prozess. Erst danach M2 → ✅.
- ✅ **Prod-Routing-Caveat gelöst:** `frontend/src/main.tsx` nutzt jetzt `HashRouter` (statt
  `BrowserRouter`). Hard-Reloads auf Unterrouten können im Asset-Protokoll-Build nicht mehr 404en, und
  Mehrfenster-Routing fürs AI-Overlay (`index.html#/overlay`) wird damit trivial.

## M3 — Linux/Mac via GitHub-Actions-CI (Phase F)

Die Windows-NSIS wird lokal gebaut (siehe M2). Die **Linux- (deb/AppImage) und macOS-Artefakte
(dmg)** lassen sich auf der Windows-Maschine *nicht* cross-bauen → sie entstehen ausschließlich in
der CI: **`.github/workflows/native-build.yml`**.

- **Matrix:** `windows-latest` → `nsis`, `ubuntu-latest` → `deb,appimage`, `macos-latest` → `dmg`
  (`fail-fast: false`). Trigger: `workflow_dispatch` + Tags `v*`.
- **Schritte je OS:** checkout · (nur Linux) `apt-get` WebKitGTK/Tray/AppImage-Deps
  (`libwebkit2gtk-4.1-dev`, `libappindicator3-dev`, `librsvg2-dev`, `patchelf`, `build-essential`,
  `file`, `libssl-dev`) · `setup-python` **3.11** (Pflicht wegen `matplotlib==3.11.0`-Pin; 3.11 hat
  noch ein `kuzu`-Wheel) · `pip install -r requirements.txt -r requirements-build.txt` · **CPU-torch**
  (Windows/Linux via `--index-url …/whl/cpu`, macOS Default-Wheel) · `setup-node` 20 · Rust-Toolchain
  + `rust-cache` · `npm install` (Root) + `npm install --prefix frontend` ·
  `npm run tauri:build -- --bundles <targets>` · Artefakt-Upload (`src-tauri/target/release/bundle/**`).
- **Full-Bundle:** `SCIENCEKG_BUNDLE_LEAN` wird **nicht** gesetzt → der `beforeBuildCommand`-Sidecar
  enthält torch/sentence-transformers (Embeddings out-of-the-box; Bundles ~1–2 GB). `--bundles`
  überschreibt das harte `targets:["nsis"]` aus `tauri.bundle.conf.json` je OS.
- **Offene Punkte (bewusst):** Real-Hardware-Smoke (Installer auf echtem Linux/Mac starten,
  Kernfeatures testen, keine verwaisten Sidecar-Prozesse) und das **Download-Verhalten im
  WebKitGTK-WebView** (Linux) bleiben unverifiziert, bis die Artefakte auf echter Hardware getestet
  werden.

## Code-Werkstatt (R2 + R4): Doppelansicht Terminal + Editor

Eigener Tab **„Werkstatt"** (`/werkstatt`). Prämisse: *die KI programmiert das meiste*. Zwei Hälften:

1. **Agent/Terminal** — ein echtes eingebettetes Terminal (Rust `portable-pty`: Windows ConPTY /
   Unix PTY) im Projektordner. Darin laufen KI-Coding-CLIs (**Claude Code** `claude`, **opencode**,
   **codex**), `git` und die Shell. Rust-Commands `terminal_spawn/write/resize/kill`
   (`src-tauri/src/terminal.rs`), Output je Terminal als Event `terminal://output/<id>`, Frontend
   `@xterm/xterm` (`frontend/src/components/WerkstattTerminal.tsx`). Beim App-Exit werden alle PTYs
   gekillt (kein verwaister Prozess). **Nur native** — im Web-Modus erscheint ein Hinweis.
2. **Manuelle Datei-Ansicht** — Datei-Baum + **Monaco-Editor** (VS Codes Editor-Engine, vollständig
   **offline** gebündelt, kein CDN; `frontend/src/monaco-setup.ts`). Öffnen/Bearbeiten/Speichern
   (Ctrl+S). Plus **Ergebnis-Ansicht**: `git status`/`git diff` zeigt „was wurde gebaut".

**Projekte = offene Git-Ordner** an wiederfindbarer Stelle (Default `~/Documents/PaperKG-Projekte`,
`config.yaml → code_workspaces.base_dir`), von anderen Editoren (VS Code …) öffenbar. PaperKG
**registriert** sie nur (DuckDB `code_projects`); **„Ordner öffnen"** lädt einen bestehenden Ordner als
externes Projekt. Backend-Logik ist web-/native-identisch (`workspace/manager.py`, Endpoints
`/workspaces*` in `api/product_main.py`) und **pfadgesichert** (kein Ausbruch aus dem Projekt-Root).

**Integration:** Per **„In Workspace einfügen"** wandert die aktuelle Datei/Selektion bzw. der Diff als
Notiz ins aktive PaperKG-Projekt (Workspace/Parallelmode). Der **AI-Cursor** (R1) hilft zusätzlich.

## AI-Cursor-Overlay (R1)

Ein **zweites, transparentes, immer-im-Vordergrund**-Fenster (`overlay`), das über dem Desktop
schwebt — auch außerhalb des Hauptfensters. Zweck: den bestehenden **UI-TARS-Handoff**
(`query/agent_handoff.py`, `POST /agent/dispatch`, `bridge/uitars/`) von überall aus auslösen.
PaperKG bleibt „das Gehirn"; es wurde **keine** neue VLM-Plumbing ergänzt.

- **Rust** (`src-tauri/src/overlay.rs`): `build_overlay` (verstecktes `WebviewWindow` „overlay":
  `transparent` + `decorations(false)` + `always_on_top` + `skip_taskbar`, teilt das init-script des
  Hauptfensters und hängt `window.__OVERLAY__ = true` + `#/overlay` an), `toggle_overlay`,
  Tauri-Commands `overlay_hide`/`overlay_toggle`, `setup_tray` (Tray-Menü „AI-Cursor ein/aus"/„Beenden")
  und `register_global_shortcut`. In `lib.rs` registriert; das Plugin `tauri-plugin-global-shortcut`
  toggelt per **`Ctrl/Cmd+Shift+Space`**. `tauri.conf.json` setzt `app.macOSPrivateApi: true` (für
  transparente Fenster auf macOS; Cargo-Feature `macos-private-api`). Eigene Capability
  `src-tauri/capabilities/overlay.json` (Fenster „overlay", `core:default`).
- **Frontend** (`frontend/src/pages/OverlayPage.tsx`): kompakte UI. `App.tsx` erkennt das Overlay
  (`window.__OVERLAY__`/Route `#/overlay`), rendert **nur** die Overlay-Seite (ohne Sidebar/Topbar) und
  überspringt die schweren Shell-Queries. Wiederverwendet **`getAgentConfig` + `streamAgentDispatch`**
  (`frontend/src/api.ts`) und das Event-Streaming-Muster aus `ParallelResultsTab.tsx`. Ist die
  `agent_bridge:` aus, zeigt das Overlay einen Hinweis. Schließen via Header-Button oder **Escape**
  (Fenster wird nur versteckt, lebt im Hintergrund weiter).

## Externe Links & PDF im nativen Fenster (M1.8)

Ein Webview kann keinen „neuen Tab" öffnen. Alle `<a target="_blank">`-Klicks und `window.open`-Aufrufe
werden im nativen Modus deshalb abgefangen (`frontend/src/native.ts`, installiert in `main.tsx`) und an
den **OS-Browser** übergeben — über das eigene Rust-Command `open_external`
(`src-tauri/src/lib.rs`, nutzt die Rust-API von `tauri-plugin-opener`; bewusst *nicht* die JS-Seite des
Plugins, um die URL-Scope-Glob-Fallstricke zu umgehen). So öffnen externe Web-Quellen (graue Quellen,
Findings-Links etc.) im Standardbrowser. Interne SPA-Navigation (react-router `<Link>` ohne `target`)
bleibt unberührt; reiner Web-Modus ebenfalls (Interceptor wird nur unter Tauri installiert).

**Library-PDF inline (M1.9):** Der „Öffnen"-Button der Library öffnet die PDF jetzt **in der App** in
einem Modal, das den bestehenden `PdfPane` wiederverwendet (pdf.js-Canvas, Seiten/Zoom/Suche) — statt im
Browser. Kein neuer Viewer-Code: `PdfPane` rendert mit `url`+`title` und leerem `evidences` sauber ohne
Evidenz-/Übersetzungs-UI. Schließen via Backdrop-Klick, Einklappen-Button oder Escape.

## Bekannte Punkte / nächste Schritte

- **Dev-Hot-Reload-Artefakt:** Beim automatischen Rebuild von `tauri dev` kann der alte Backend-Sidecar
  kurz mit dem neuen überlappen → einmalige DuckDB-Meldung „Unique file handle conflict". Löst sich von
  selbst (alte Instanz wird beendet). Tritt im Normalbetrieb (ein Start/Stop) nicht auf. Kandidat zur
  Härtung: den Python-Sidecar per Windows-Job-Object strikt an den App-Prozess koppeln (auch bei Hard-Kill).

- **M2 (Distribution) — Code steht, Voll-Build offen:** PyInstaller-Verpackung + Bundle-Einbindung
  sind implementiert (siehe Abschnitt „M2" oben). Statt `bundle.externalBin` (nur Einzeldateien) wird
  der **one-dir**-Ordner als `bundle.resources` mitgeliefert und per `std::process` gespawnt — das
  reuse't die M1-Spawn/Kill-Logik. Verbleibend: torch (CPU) installieren und den eigentlichen
  `npm run tauri:build` + Clean-Machine-Install verifizieren. Bundle wird wegen torch groß (~1–2 GB) →
  ggf. Embeddings später lazy/optional (`SCIENCEKG_BUNDLE_LEAN=1` baut schon heute schlank).
- **Prod-Routing-Caveat — gelöst:** Das Frontend nutzt seit der Werkstatt-Ausbaustufe `HashRouter`
  (`frontend/src/main.tsx`). Damit kann ein Hard-Reload auf einer Unterroute im Asset-Protokoll-Build
  nicht mehr 404en; zusätzlich macht es das Mehrfenster-Routing fürs AI-Overlay (`#/overlay`) einfach.
- **Downloads auf Linux** beim WebKitGTK-Webview erneut prüfen (M3).
- **Tauri-IPC auf der Seite** erst ab R1 nötig → dann Capabilities/CSP in `src-tauri/capabilities/`
  erweitern. In M1 ruft das Frontend ausschließlich per HTTP den Sidecar; keine Tauri-Permissions nötig.
