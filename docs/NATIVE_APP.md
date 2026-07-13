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
| R1   | Desktop-AI-Overlay (transparent/always-on-top, Hotkey, Tray) | ✅ (native; Hotkey `Ctrl/Cmd+Shift+Space` + Tray; jetzt die echte Live-Agent-Oberfläche mit Task-Injection-IPC statt eines leeren Dispatch-Textfelds, siehe R5) | `src-tauri/src/overlay.rs`, `src-tauri/src/lib.rs`, `frontend/src/pages/OverlayPage.tsx` |
| R2   | Eingebettetes Terminal (PTY) | ✅ (native; portable-pty + xterm.js) | `src-tauri/src/terminal.rs`, `frontend/src/components/WerkstattTerminal.tsx` |
| R3   | Jupyter als Sidecar + Tab | ✅ (native; optionaler `jupyter lab`-Sidecar + iframe-Tab) | `src-tauri/src/jupyter.rs`, `frontend/src/pages/JupyterPage.tsx` |
| R4   | Code-Editor (Monaco) | ✅ (Werkstatt-Tab, offline gebündelt) | `frontend/src/pages/WorkstationPage.tsx`, `frontend/src/monaco-setup.ts` |
| R5   | Desktop-Agent v2 (Selbst-Steuerung + Assistent; verwalteter Bridge-Sidecar; jederzeit abbrechbar) | ✅ (native; Tauri spawnt/killt `bridge/uitars`, Overlay bekommt vorgeladene Aufgaben per Event, Assistent beobachtet den Bildschirm periodisch) | `bridge/uitars/server.mjs`, `src-tauri/src/agent_bridge.rs`, `frontend/src/pages/OverlayPage.tsx`, `api/product_main.py` |
| R5.1 | Nachbesserung: Sprachsteuerung (Mitigation), Assistent-Antworten entfesselt, "KI hat Kontrolle"-Bildschirmrand, Modell-Sichtbarkeit; UI-TARS-Alternativen recherchiert (nicht umgesetzt) | ✅ | `bridge/uitars/server.mjs`, `src-tauri/src/overlay.rs`, `frontend/src/pages/{OverlayPage,ControlBorderPage}.tsx` |
| R5.2 | Assistent-Pointer ("zeig mir, wo ich klicken kann"): Grounding-Aufruf + eigenes Zeiger-Overlay-Fenster, klickt nie | ✅ | `bridge/uitars/server.mjs`, `api/product_main.py`, `src-tauri/src/overlay.rs`, `frontend/src/pages/{OverlayPage,PointerOverlayPage}.tsx` |
| R5.3 | Pointer-Reparatur: Assistent-Umbau (ein Frage-Feld, "Zeigen & Antworten" liefert Text **und** Ring; stateless `/observe/point`) + **Koordinaten-Bugfix** (Screenshot vor dem Grounding client-seitig auf ein bekanntes Budget herunterskalieren und daran normalisieren — sonst staucht LM Studios stiller Downscale die Klick-Koordinaten um ~0,6×) | ✅ | `bridge/uitars/server.mjs`, `frontend/src/pages/OverlayPage.tsx`, `src-tauri/src/overlay.rs` |
| R6   | **Desktop Companion** (UI-TARS-frei): screen-aware Chat mit freier Modellwahl (LM Studio Qwen3-VL / Claude via `anthropic`-Provider), gleitender + ausweichender AI-Pointer, „Bereich erklären"-Snip (Freeze-Frame); Rust-Capture (`xcap`, WDA-Exclusion) → `/companion/*` → LLMRouter-Vision. Selbst-Steuerung/UI-TARS bleibt als Legacy-Modus | ✅ (Code; manuelle End-to-End-Verifikation am Gerät offen) | `src-tauri/src/capture.rs`, `src-tauri/src/overlay.rs`, `query/screen_companion.py`, `query/llm_router.py`, `api/product_main.py`, `frontend/src/pages/{OverlayPage,PointerOverlayPage,SnipOverlayPage}.tsx`, `frontend/src/pointerMath.ts` |
| R6.1 | Companion-Nachbesserung: Live-Modell-Discovery + lesbares Dropdown (`color-scheme:dark` + `option{}`); Thinking-Modelle (`max_tokens`-Headroom, `/no_think` für Qwen, Budget-erschöpft→klarer Fehler statt Denkprotokoll); **0-1000-Grounding-Kontrakt** statt Sent-Frame-Pixel (Ursache des konstant falschen Rings) + Zeig-Entscheidungsregel (nur bei Wo/Wie-Fragen) + Debug-Dumps (`companion.debug_capture`) | ✅ (Code; Geräte-Verifikation offen) | `frontend/src/pages/OverlayPage.tsx`, `frontend/src/styles.css`, `query/screen_companion.py`, `query/llm_router.py`, `api/product_main.py`, `tests/test_companion_api.py` |
| R6.2 | Multi-Monitor: `list_monitors` + `target_monitor` (Default Monitor unter Cursor, sonst Picker-Id), `CaptureResult`/`SnipBegin` tragen Monitor-Origin/-Name, Pointer-/Snip-Fenster werden auf den Zielmonitor gesetzt, DPR-unabhängige Skalierung (`monitor_width/innerWidth`), Overlay-Picker + Anzeige des aktiven Bildschirms | ✅ (Code; Geräte-Verifikation offen) | `src-tauri/src/capture.rs`, `src-tauri/src/overlay.rs`, `frontend/src/pages/{OverlayPage,PointerOverlayPage,SnipOverlayPage}.tsx`, `frontend/src/pointerMath.ts` |
| R6.3 | Quellen-Modus: Companion-Antworten optional mit lokalen Papern (`HybridRetriever`, `[arxiv:...]`-Zitate) und/oder Websuche (`run_web_search`, sanitisiert, Untrusted-Data-Regel) belegen; Toggle-Chips + Quellenliste im Overlay; best-effort (Ausfall bricht Antwort nicht) | ✅ (Code; Geräte-Verifikation offen) | `query/screen_companion.py`, `api/product_main.py`, `frontend/src/pages/OverlayPage.tsx` |
| R7   | **Native Selbst-Steuerung** (enigo statt UI-TARS-Bridge): Backend-Planer (`query/self_drive.py`, 1 Aktion/Screenshot, 0-1000-Koordinaten), native Aktions-Commands (`control.rs`, armed-gated), `/selfdrive/*`-Endpoints, Overlay-**Bestätigungsmodus** (jede Aktion einzeln), Not-Aus `Ctrl+Shift+Q`. Design + Skeleton — noch kein autonomer Loop | ✅ (durch R7.1 zum Autopilot ausgebaut) | `src-tauri/src/control.rs`, `query/self_drive.py`, `api/routers/companion.py`, `frontend/src/pages/overlay/`, dieses Dokument (Abschnitt „R7") |
| R7.1 | **R7-Rework: Autopilot + Verify-Pipeline + geführter Modus + Sessions.** Planner wird Pipeline (Verify des Nachher-Screenshots gegen `expectation` + Fehler-Feedback in die History, Stall-Detection → erzwungene Rückfrage, `lookup`-Recherche-Aktion, `ask`-Rückfrage); **Zoom-Refine** (2-stufiges Grounding auf Original-Crop, `query/screen_grounding.py`) für Klick- und Zeiger-Punkte; **Autopilot** (Default, Bestätigung bleibt Schalter, Pause/Weiter); **geführter Companion-Modus** (`query/guide_flow.py` + globaler Klick-Watcher `click_watch.rs`: Ring zeigt, Nutzer klickt, auto-advance + Klick-Verifikation); **DuckDB-Sessions** (`companion_sessions`/`companion_messages`, Session-Liste/Umbenennen/Löschen im minimalistischen Chat-Overlay); Companion/Selfdrive-Routen nach `api/routers/companion.py` extrahiert | ✅ (Code; Geräte-Verifikation offen) | `query/{screen_grounding,self_drive,guide_flow}.py`, `api/routers/companion.py`, `storage/metadata_db/companion.py`, `src-tauri/src/{click_watch,control}.rs`, `frontend/src/pages/overlay/`, `tests/test_{screen_grounding,self_drive,guide_flow,companion_sessions}.py` |
| R7.2 | **Sicherheits-Trio + Performance.** Maus-Ruck-Not-Aus (Pre-Action-Check + 30-ms-Watcher, Anker-Buchführung, Overlay-Ausnahme), Aktions-/Schritt-Timeouts (Rust-Worker-Deadline + Overlay-Deadline, Not-Aus mit `reason`-Payload, Session-Status in DuckDB), Absicherung sensibler Ziele (`classify_sensitive`, DE+EN-Keywords, Autopilot-Downgrade + Warn-Confirm); **Lärm-Fix**: die 4 Overlay-Fenster entstehen lazy per `ensure_window` (Event-Queue bis `overlay_window_ready`) statt beim Start, alle Haupt-Shell-Seiten sind code-gesplittet (`React.lazy`, Entry-Chunk ohne Monaco/pdf.js/xterm/xyflow), toter `PdfPane`-Import aus `NotesSubComponents` entfernt | ✅ (Code; Geräte-Verifikation offen) | `src-tauri/src/{control,overlay,capture,lib}.rs`, `query/self_drive.py`, `api/routers/companion.py`, `frontend/src/{App.tsx,native.ts}`, `frontend/src/pages/overlay/`, `config.yaml`, `tests/test_self_drive.py` |

## R7 — Native Selbst-Steuerung: Design + Skeleton

**Ziel.** Der Nutzer gibt ein Ziel vor; die KI setzt es mit echten Maus-/Tastatur­eingaben um.
Bewusst getrennte Rollen (wie beim Companion): **Backend = Gehirn** (plant), **Rust = dumme Hände**
(`enigo` führt aus), **Frontend = Sequenzer + Consent**. Kein UI-TARS mehr auf diesem Pfad — der
Legacy-Bridge-Modus bleibt daneben bestehen.

**Loop-Protokoll.**
1. `POST /selfdrive/start {goal, monitor, provider, model}` → `session_id` (gated auf
   `companion.self_drive.enabled`).
2. Frontend: `capture_screen` (Zielmonitor) → `POST /selfdrive/step {session_id, image_base64}`
   → VLM liefert `{"thought", "action": {"type","x","y","text","keys","dx","dy"}, "done"}`.
   `action.type ∈ {click, double_click, type, key, scroll, move, wait, done, fail}`;
   `x/y` auf 0-1000-Raster, backend-seitig in Original-Screenshot-Pixel skaliert.
3. Frontend führt die Aktion via `control.rs`-Command aus (Original-Pixel + Monitor-Origin →
   physische Desktop-Koordinaten) und ruft das nächste `step` — bis `done`/`fail`/Schrittbudget
   (`max_steps`, Default 15)/Abbruch.

**Sicherheitsmodell (in dieser Reihenfolge).**
- `companion.self_drive.enabled` (Default **false**) gibt überhaupt erst den Backend-Planer frei.
- Nichts synthetisiert Eingaben, solange die Sitzung nicht **armed** ist (`self_drive_arm`); das
  Armen blendet zugleich den „KI hat Kontrolle"-Bildschirmrand ein. Der Arm-Zustand ist rein
  im Speicher und stirbt mit der App.
- **Bestätigungsmodus (dieser Stand):** jede geplante Aktion wird angezeigt und einzeln per
  „Ausführen" bestätigt (oder „Überspringen"). Ein autonomer Loop ist bewusst noch nicht drin.
- **Not-Aus** `Ctrl+Shift+Q` (global, wirkt auch während einer Aktion): disarmt, blendet den Rand
  aus, sendet `selfdrive://emergency-stop` ans Overlay (bricht die Schleife ab und verwirft die
  Sitzung). Zusätzlicher Overlay-Stopp-Button.

**Skeleton-Umfang (umgesetzt).** `enigo`-Dependency; `control.rs` mit armed-gated Commands
(`self_drive_arm/disarm`, `control_move/click/type/key/scroll`, `emergency_stop` + Hotkey-Registrierung);
`query/self_drive.py` (Planer + Action-Grammar + In-Memory-`SelfDriveStore`); `/selfdrive/{start,step,stop}`;
Overlay-Tab-Umschalter „Nativ" ↔ „UI-TARS-Bridge (Legacy)" mit Bestätigungspanel;
`companion.self_drive`-Config; Tests `tests/test_self_drive.py` (Action-Parsing, Koord-Skalierung,
Schrittbudget, Endpoint-Stubs).

### R7.1 — Rework: Autopilot, Verify-Pipeline, geführter Modus, Sessions

**Planner-Pipeline (`query/self_drive.py`).** `plan_step` ist jetzt pro Screenshot:
1. **Verify**: der eingehende Screenshot ist zugleich das Nachher-Bild der letzten Aktion.
   Billiger Pixel-Diff (`companion.verify.pixel_diff_threshold`, „Bildschirm unverändert" ohne
   VLM-Call) + ein Verify-VLM-Call (`screen_grounding.verify_expectation`, Describe-then-Judge
   gegen Ja-Bias) gegen die vom Planer angesagte `expectation`. Fehlschläge landen als explizites
   deutsches Feedback in der History („…hat NICHT funktioniert — anderes Element/anderer Weg") —
   damit lokale Modelle merken, dass sie falsch geklickt haben, statt sich zu wundern.
2. **Stall-Detection**: 3× dieselbe Klick-Position oder `max_consecutive_failures` verifizierte
   Fehlschläge in Folge → erzwungene `ask`-Rückfrage an den Nutzer (ohne VLM-Call); zweiter Stall
   → terminales `fail`. Antworten laufen über `POST /selfdrive/answer`.
3. **Plan**: Aktions-Grammar erweitert um `label` (Pflicht bei click/move — füttert Refine + Verify),
   `expectation`, `lookup` (Recherche, wenn Wissen fehlt) und `ask` (Nutzer-Entscheidung).
   `lookup` löst der Endpoint auf (`_companion_context` → Web/Paper, sanitisiert + Untrusted-Data-
   Framing) und plant auf demselben Frame neu — budgetiert (`lookup.max_per_session`).
4. **Zoom-Refine** (`query/screen_grounding.py`): zweistufiges Grounding — Crop (`refine.crop_px`,
   aus dem **Original in voller Auflösung**) um den groben Punkt, hochskaliert (`refine.zoom`),
   ein Nachfrage-Call auf 0-1000-Raster über den Ausschnitt → deutlich präzisere Klicks mit
   kleinen lokalen VLMs. Degradiert bei Parse-Fehlern auf den groben Punkt.

**Autopilot (Frontend `useSelfDriveLoop`).** Default an (`companion.self_drive.autopilot`):
plan → ausführen → `verify.settle_ms` warten → neu planen, ohne Einzelbestätigung. Schalter
„Autopilot" im Panel stellt den Bestätigungsmodus (Ausführen/Überspringen) wieder her; Pause/Weiter
mitten im Loop; Not-Aus `Ctrl+Shift+Q` disarmt in Rust (inkl. Klick-Watcher-Stopp) und bricht
beide Loops im Overlay ab.

**Geführter Companion-Modus (`query/guide_flow.py` + `src-tauri/src/click_watch.rs`).**
„Schritt für Schritt"-Chip: pro Screenshot genau EIN Zeiger-Schritt
(`/companion/guide/{start,step,stop}`); der globale Klick-Watcher (30-ms-`GetAsyncKeyState`-Polling,
Release-Edge, `companion://click` mit `on_overlay`-Filter) meldet den echten Nutzer-Klick →
`click_settle_ms` warten → frischer Screenshot mit `event:"click"` + Klick-Koordinaten → Backend
verifiziert die Wirkung des Klicks (gleiches Verify wie oben, guidance-Feedback) und plant den
nächsten Ring — komplett automatisch bis `done`. `step: null` = reiner Hinweis-Schritt („scrolle
nach unten"). Der alte One-Shot-`/companion/guide` bleibt für „Wo ist X?"-Einzelzeiger.

**Sessions + minimalistisches Chat-Overlay.** DuckDB-Tabellen `companion_sessions` +
`companion_messages` (`storage/metadata_db/companion.py`): Chat-/Step-Transkripte überleben den
Neustart; Session-CRUD unter `/companion/sessions*`. Das Overlay (`frontend/src/pages/overlay/`)
ist jetzt ein Chat-Shell mit Kopfzeile (Session-Titel, Session-Liste, „Neue Session", Zahnrad-
Popover für Provider/Modell/Monitor/Quellen), einem gemeinsamen Eingabefeld (Companion-Frage,
geführtes Ziel, Selbst-Steuerungs-Ziel oder Antwort auf eine Rückfrage) und System-Zeilen für
Aktionen/Prüfungen im Stream. In-Flight-Planner-State bleibt im Speicher — nach Backend-Neustart
ist eine wiedergeöffnete Session reiner Verlauf („neu starten"-Pfad).

**Routen-Extraktion.** `/companion/*` + `/selfdrive/*` leben jetzt in `api/routers/companion.py`
(Muster `api/routers/parallel.py`); die patchbaren Singletons (`llm_router`,
`_COMPANION_CONFIG_CACHE`, `_companion_context`, `_SELF_DRIVE_STORE`, `_GUIDE_STORE`) bleiben in
`product_main` und werden zur Laufzeit als `pm.<name>` aufgelöst — bestehende Monkeypatch-Tests
unverändert.

**Sicherheits-Trio (R7.2, umgesetzt).** Drei zuvor aufgeschobene Schutzmechanismen sind jetzt drin:

- **Maus-Ruck-Not-Aus** (`src-tauri/src/control.rs`): bewegt der *Nutzer* die Maus >
  `companion.self_drive.mouse_abort_px` (Default 150 px) vom erwarteten Punkt, stoppt alles.
  Zwei Mechanismen: ein race-freier Pre-Action-Check vor jeder `control_*`-Ausführung (Cursor
  weit weg vom letzten synthetischen Anker → Abbruch statt Ausführung) plus ein 30-ms-Watcher-
  Thread, der während laufender Aktionen wacht. Cursor-Positionen auf der Chat-Karte sind
  ausgenommen („Ausführen" klicken ist Interaktion, keine Übernahme). Zwei-Anker-Buchführung
  (aktueller + voriger Zielpunkt) deckt das Sampling-Race zwischen Bookkeeping und `SendInput` ab.
- **Aktions-Timeouts**: jede enigo-Aktion läuft auf einem Worker-Thread mit harter Deadline
  (`action_timeout_ms`, Default 5 s) — Ablauf löst den Not-Aus aus. Der Overlay-Loop hat
  zusätzlich eine Schritt-Deadline (`step_timeout_ms`, Default 120 s) über Capture+Planung.
  Das `selfdrive://emergency-stop`-Event trägt jetzt `{ reason: "hotkey"|"mouse"|"timeout" }`;
  das Overlay zeigt die passende Meldung und persistiert den Session-Status (`stopped`/`done`/
  `failed`) in DuckDB.
- **Absicherung sensibler Ziele** (`query/self_drive.py::classify_sensitive`): geplante Aktionen,
  deren Label/Eingabetext/Begründung Passwort-/Zahlungs-/Kauf-/Lösch-Schlüsselwörter (DE+EN,
  Wortgrenzen-Regex, erweiterbar via `companion.self_drive.sensitive.keywords`) treffen, kommen
  mit `sensitive: true` + Begründung zurück — der Autopilot degradiert für genau diese Aktion in
  den Bestätigungsmodus (Warnzeile im `SelfDrivePanel`).

**Offene Folgearbeit (bewusst später).** Klick-Watcher für Nicht-Windows (`device_query`);
Geräte-Verifikation über mehrere Monitore/DPI (Autopilot, geführter Modus, Refine-Treffsicherheit,
Maus-Ruck-Not-Aus).

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

**UI-Ausbau (dunkles IDE-Redesign):** Die Werkstatt-Seite ist ein **dunkles IDE-Theme** (gescoped auf
`.werkstatt-page`, Rest der App bleibt hell), füllt **genau einen Screen** (`height: calc(100vh - 64px)`
+ `overflow:hidden` — kein Body-Scroll mehr) und hat eine **kompakte horizontale Toolbar**
(Projekt-Picker · Segmented-Control · Aktions-Buttons). Zwei **Modi** (persistiert in
`localStorage["sciencekg.werkstatt.mode"]`): **Manuell** (Datei-Baum + Monaco + Diff oben, Terminal
unten) und **Agent · Vorschau** (Terminal links, **Live-Vorschau-iframe** mittig, Diff rechts). Alle
Panels sind per **`react-resizable-panels`** frei verschieb-/einklappbar (Layout via `autoSaveId`
gemerkt). **Mehrere Terminals** in einer Tab-Leiste (`frontend/src/components/TerminalTabs.tsx`; das
Rust-Backend war bereits multi-session). Die **Vorschau** (`PreviewPane.tsx`) hat eine Adressleiste
(Reload/extern) und **erkennt die Dev-Server-URL automatisch** aus der Terminal-Ausgabe
(`http://localhost:<port>`). Geänderte Dateien: `frontend/src/pages/WorkstationPage.tsx`,
`components/{TerminalTabs,PreviewPane,WerkstattTerminal}.tsx`, `styles.css`.

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
  `src-tauri/capabilities/overlay.json` (Fenster „overlay", `core:default` +
  `core:window:allow-start-dragging` — ohne letzteres ignoriert das randlose Fenster den
  `data-tauri-drag-region`-Header lautlos und lässt sich nicht per Maus verschieben).
- **Frontend** (`frontend/src/pages/OverlayPage.tsx`): kompakte UI. `App.tsx` erkennt das Overlay
  (`window.__OVERLAY__`/Route `#/overlay`), rendert **nur** die Overlay-Seite (ohne Sidebar/Topbar) und
  überspringt die schweren Shell-Queries. Wiederverwendet **`getAgentConfig` + `streamAgentDispatch`**
  (`frontend/src/api.ts`) und das Event-Streaming-Muster aus `ParallelResultsTab.tsx`. Schließen via
  Header-Button oder **Escape** (Fenster wird nur versteckt, lebt im Hintergrund weiter). Siehe R5
  unten für die aktuelle Gate-Logik (nicht mehr an `agent_bridge.enabled` gekoppelt).

## Desktop-Agent v2: Selbst-Steuerung + Assistent, verwalteter Bridge-Sidecar (R5)

Vorher öffnete „An Desktop-Agent übergeben" nur ein Inline-Panel mit „Brief kopieren" — das
AI-Cursor-Overlay (R1) war davon komplett abgekoppelt (öffnete immer leer). R5 schließt diese
Lücke: der Handoff-Button **spawnt** im nativen Modus das Overlay tatsächlich, vorgeladen mit
dem Aufgaben-Brief, und lässt zwischen zwei Modi wählen. Nichts läuft automatisch los — der
Nutzer muss im Overlay explizit „Starten" klicken (Sicherheits-/Datenschutz-Gate für einen
Agenten, der die Maus/Tastatur steuert bzw. den Bildschirm beobachtet).

- **Selbst-Steuerung**: autonomer Lauf wie zuvor (Kanal B), jetzt aber richtig gespawnt statt nur
  copy-paste, und mit echtem Abbruch: die Bridge (`bridge/uitars/server.mjs`) hält pro Lauf einen
  `AbortController` (`runId`), `POST /cancel` bricht ihn graceful ab; `agent_bridge_stop` (Rust)
  killt den Sidecar-Prozess hart als garantierten Fallback, falls ein einzelner Schritt den Abort
  nicht rechtzeitig honoriert.
- **Assistent**: neue Fähigkeit — beobachtet den Bildschirm periodisch (Screenshot →
  lokales VLM → 1-2-Satz-Beschreibung, rollierender Kontext) und beantwortet live Fragen dazu,
  steuert aber nie Maus/Tastatur. Läuft komplett im Node-Bridge-Prozess (`POST /observe/start|
  ask|stop`) — Screenshots verlassen den Prozess nie und werden nicht persistiert; nur kurze
  Text-Beschreibungen. Explizites Opt-in („Beobachtung starten") und ein durchgehend sichtbarer
  „Ich sehe deinen Bildschirm"-Indikator solange aktiv; Escape kollabiert das Overlay während einer
  aktiven Beobachtung nur zu einer minimalen Pille statt es zu verstecken — vollständiges Beenden
  braucht den expliziten „Stoppen"-Klick.
- **Rust** (`src-tauri/src/agent_bridge.rs`, neu, nach dem Muster von `jupyter.rs`):
  `AgentBridgeState(Mutex<…>)` hält den einen Bridge-Prozess. `agent_bridge_ensure` startet (oder
  reused) `node server.mjs` aus `bridge/uitars/` auf einem freien Port (`pick_free_port` +
  `wait_until_ready`, stdout/stderr gedraint wie bei Jupyter) und gibt den Port zurück; fehlt
  Node.js oder `node_modules`, kommt ein klarer, actionable Fehler (kein automatisches
  `npm install`). `agent_bridge_stop` killt hart — der garantierte Abbruch-Fallback für beide
  Modi. `overlay.rs` bekommt `overlay_dispatch_task` (zeigt+fokussiert das Overlay-Fenster, dann
  `app.emit_to("overlay", "overlay://task", …)`) — die fehlende Verbindung zwischen dem
  Handoff-Button und dem Overlay. Alle drei Commands + der Exit-Hook (`agent_bridge::kill`) sind
  in `lib.rs` registriert.
- **Backend** (`api/product_main.py`): neue Relay-Endpunkte `POST /agent/cancel`,
  `/agent/observe/start` (SSE), `/agent/observe/ask`, `/agent/observe/stop` — gleiches
  Loopback-Vertrauensmodell wie das bestehende `/agent/dispatch` (`_validate_bridge_url`), über
  einen neuen `_resolve_bridge_origin`-Helper, der entweder den vom Tauri-Sidecar zurückgegebenen
  Port (`bridge_base`) oder die konfigurierte Kanal-B-`url` (Web-Modus-Fallback) auflöst.
  `GET /agent/config` liefert zusätzlich `manage_sidecar`/`helper_enabled`/
  `observe_interval_seconds` aus dem erweiterten `agent_bridge:`-Block in `config.yaml`.
- **Frontend**: `OverlayPage.tsx` (deutlicher Umbau) — Modus-Umschalter, `nativeListen(
  "overlay://task")` befüllt Aufgabe + Modus vor, `agent_bridge_ensure` vor jedem „Starten",
  durchgehend sichtbarer „Stoppen"-Button, Selbst-Steuerung-Log (wiederverwendet `EventLine`),
  Assistent-Ansicht (Beobachtungs-Indikator + Chat). `ParallelResultsTab.tsx`: „An AI-Cursor
  übergeben" ersetzt im nativen Modus das alte Inline-Panel; Web-Modus (kein Tauri) bleibt
  unverändert beim bisherigen Kanal-A/B-Panel.
- **Gate-Logik:** der Klick auf „Starten" im Overlay ist selbst die Zustimmung —
  `agent_bridge.enabled` wird dort **nicht** mehr geprüft (das gate't nur noch den
  Web-Modus-Fallback in `ParallelResultsTab.tsx`, der keinen On-Demand-Sidecar hat). Einzig
  `agent_bridge.helper_enabled` bleibt als eigener Schalter, um gezielt nur Assistent zu
  deaktivieren. Schlägt `agent_bridge_ensure` fehl (z. B. fehlendes Node.js), zeigt das Overlay
  die konkrete Fehlermeldung **und** einen „Brief kopieren"-Button (Kanal-A-Fallback).
- **Bekannter offener Punkt:** der Node-Sidecar wird (anders als der Python-Backend-Sidecar)
  **nicht** ins Standalone-Bundle gepackt — `agent_bridge_ensure` setzt Node.js auf PATH und
  bereits ausgeführtes `npm install` in `bridge/uitars` voraus. Vollständiges Node-Vendoring ist
  Folgearbeit.

## AI-Cursor: Sprachsteuerung, Kontroll-Anzeige, VLM-Alternativen (Nachbesserung zu R5)

Direktes Feedback nach dem ersten produktiven Einsatz von R5 (Chat auf Chinesisch, Unklarheit über
die Cursor-Übernahme und die Modellwahl, Assistent lehnte bildschirmbezogene Fragen ab) führte zu
vier gezielten Nachbesserungen, ohne die R5-Architektur zu verändern:

- **Sprache (Mitigation, keine Garantie):** `ui-tars-1.5-7b` (Qwen-basiert) schreibt den
  `Thought`-Teil manchmal auf Chinesisch, obwohl `@ui-tars/sdk`s Default-`SYSTEM_PROMPT` komplett
  englischsprachig ist und keinerlei Sprachvorgabe enthält. `server.mjs` baut für
  Selbst-Steuerung jetzt ein eigenes `systemPrompt` (`@ui-tars/sdk/constants`-Export + ein
  `## Language`-Abschnitt **vor** `## User Instruction` — sonst würde die Anweisung hinter dem
  eigentlichen Auftrag landen und vermutlich ignoriert), das Deutsch für `Thought` vorschreibt;
  `Action:`-Zeilen bleiben exakt in der geparsten Syntax. Die Assistent-Prompts (`observeTick`,
  `/observe/ask`) bekamen dieselbe explizite Deutsch-Anweisung. Da das Modell trotzdem gelegentlich
  abweichen kann, bleibt ein Nicht-UI-TARS `helper_vlm_model` die zuverlässigere Lösung für
  Assistent — es folgt Sprachanweisungen deutlich besser, weil es nicht auf GUI-Grounding
  spezialisiert ist.
- **Assistent nicht mehr an die ursprüngliche Aufgabe gefesselt:** `/observe/ask` behandelte die
  einmal gesetzte `Aufgabe` (`session.primer`) wie eine harte Grenze und lehnte z. B. "Wo ist der
  Download-Button?" ab, obwohl der Button im aktuellen Screenshot sichtbar war — kein Hard-Filter,
  reines Prompt-Framing. Der System-Prompt behandelt `Aufgabe` jetzt nur noch als Hintergrund­
  kontext; der Assistent beantwortet beliebige bildschirmbezogene Fragen. Er bewegt dabei
  weiterhin **nichts** — reine Text-Antwort; für echtes Hinklicken bleibt Selbst-Steuerung
  zuständig.
- **Sichtbares "KI hat Kontrolle"-Signal:** Es gibt keinen separaten "KI-eigenen" Cursor — die
  Selbst-Steuerung bewegt den echten OS-Cursor (`@ui-tars/operator-nut-js`/`NutJSOperator`), weil
  Klicks auf echte Fenster treffen müssen. Damit die Übernahme trotzdem unmissverständlich ist,
  gibt es jetzt ein zweites, minimales, klickdurchlässiges Always-on-Top-Fenster
  (`CONTROL_BORDER_LABEL` in `src-tauri/src/overlay.rs`: `build_control_border` +
  `control_border_show`/`_hide`), das einen farbigen, pulsierenden Bildschirmrand über den
  gesamten primären Monitor legt, solange ein Selbst-Steuerung-Lauf aktiv ist
  (`frontend/src/pages/ControlBorderPage.tsx`, Route `#/control-border`). Ein-/Ausblenden ist an
  `runActive` in `OverlayPage.tsx` gekoppelt; `agent_bridge_stop` (der Rust-seitige Hard-Kill-
  Fallback) blendet den Rand zusätzlich nativ aus, falls der Sidecar abrupt stirbt, bevor das
  Frontend selbst aufräumen kann. **Bekannte Grenzen:** es ist ein echtes (wenn auch transparentes)
  OS-Fenster, taucht also potenziell auch in den Screenshots auf, die die Selbst-Steuerung selbst
  für ihr Grounding macht — deshalb sitzt das Label bewusst unten rechts statt oben mittig (dort
  liegen typischerweise Navigationsleisten/Klickziele). Der sauberere Fix wäre, das Fenster aktiv
  von der Bildschirmaufnahme auszuschließen (Windows: `SetWindowDisplayAffinity` /
  `WDA_EXCLUDEFROMCAPTURE`) — plattformspezifische Zusatzarbeit, nicht Teil dieser Nachbesserung.
  Außerdem deckt der Rand nur den primären Monitor ab, nicht alle angeschlossenen Displays.
- **Modellwahl pro Modus sichtbar gemacht:** `config.yaml`s `agent_bridge.helper_vlm_model` war
  schon vorher frei wählbar (jedes vision-fähige lokale/Cloud-Modell aus `llm.providers`) — nur
  auskommentiert und im Overlay nicht sichtbar, wodurch es wie eine feste Ein-Modell-Bindung
  wirkte. Das Overlay zeigt jetzt unter dem Modus-Umschalter das aktive Modell je Modus
  (`config.vlm_model` bzw. `config.helper_vlm_model`) inklusive Hinweis, dass Letzteres änderbar
  ist.

### UI-TARS-Alternativen für Selbst-Steuerung — Recherche-Stand (keine Umsetzung)

Selbst-Steuerungs `vlm_model` **muss** UI-TARS-kompatibel bleiben — die Kopplung sitzt tief in
`@ui-tars/sdk`s internem `Model.js`, das `@ui-tars/action-parser`s strikte
`Thought:`/`Action: fn(args)`-Regex-Grammatik aufruft; `GUIAgent` ruft das intern auf, ohne Hook für
einen eigenen Parser. Ein beliebiges anderes lokales/Cloud-VLM lässt sich deshalb nicht einfach in
`vlm_model` eintragen.

Der einzige gefundene Entkopplungspfad: der offiziell exportierte Subpath `@ui-tars/sdk/core`
stellt die wiederverwendbaren Low-Level-Bausteine bereits bereit (`Operator`-Basisklasse,
`parseBoxToScreenCoords`, `convertToOpenAIMessages`), und `NutJSOperator.execute()`
(`@ui-tars/operator-nut-js`) erwartet nur eine generische, bereits geparste Struktur
(`ExecuteParams`: `{ prediction, parsedPrediction, screenWidth, screenHeight, scaleFactor,
factors }`, siehe `@ui-tars/sdk/dist/types.d.ts`). Ein eigener, schlanker Agent-Loop (Screenshot →
Aufruf eines beliebigen tool-calling-fähigen VLMs mit eigenem JSON-Aktionsschema → Mapping auf
`parsedPrediction` → `operator.execute()`) könnte `GUIAgent` ersetzen und die UI-TARS-Bindung
vollständig aufheben — auf Kosten von eigenem Loop-/Prompt-/Schema-Unterhalt und dem Risiko, die
UI-TARS-spezifische Trainings-Güte für GUI-Grounding (Klick-Zielgenauigkeit) zu verlieren. Als
eigenständige Folgearbeit vorgemerkt, nicht Teil dieser Nachbesserung.

## Assistent-Pointer: „zeig mir, wo ich klicken kann" (R5.2)

Erste, risikoarme Umsetzung aus dem Nachbesserungs-Plan (`~/.claude/plans/die-ai-cursor-funktion-
ich-frolicking-seahorse.md`) zum obigen UI-TARS-Feedback. Assistent beschreibt bisher nur in Text,
wo ein Element ist — jetzt kann er zusätzlich einen echten Bildschirm-Marker zeigen, ohne jemals
zu klicken.

- **Grounding statt Freitext** (`bridge/uitars/server.mjs`, `groundPoint()`): ein Single-Shot-Aufruf
  gegen **`VLM_MODEL`** (das UI-TARS-Grounding-Modell, nicht `HELPER_VLM_MODEL` — ein allgemeines
  Vision-Modell liefert keine verlässlichen Klick-Koordinaten). Nutzt dafür bewusst dieselben schon
  vorhandenen, offiziell exportierten Bausteine wie Selbst-Steuerung: `@ui-tars/action-parser`s
  `actionParser()` parst die `Thought:`/`Action: click(start_box='[x1,y1,x2,y2]')`-Antwort (Action-
  Space auf nur `click` verengt, da hier nichts ausgeführt wird), `@ui-tars/sdk/core`s
  `parseBoxToScreenCoords()` rechnet die Box in echte Bildschirmkoordinaten um — dieselbe
  Koordinaten-Mathematik, die `@ui-tars/operator-nut-js` für echte Klicks nutzt, damit kein zweites,
  abweichendes Koordinatensystem entsteht. `width`/`height` (Screenshot-Pixelgröße) werden wie in
  `GUIAgent` selbst per `Jimp` aus dem Screenshot decodiert. Neuer Endpoint `POST /observe/point`
  (Bridge) → Relay `POST /agent/observe/point` (`api/product_main.py`, gleiches Muster wie
  `/observe/ask`).
- **Eigenes Zeiger-Fenster** (`src-tauri/src/overlay.rs`): drittes full-monitor, klickdurchlässiges,
  always-on-top-Fenster (`PointerState`/`build_pointer_overlay`/`pointer_show`/`pointer_hide`,
  gleiche physical-pixel-Größe/-Position wie der bestehende „KI hat Kontrolle"-Rand — dadurch fällt
  die logische/CSS-Pixel-Ebene des Webviews mit der Koordinatenebene zusammen, in der
  `parseBoxToScreenCoords` misst, ohne eigene DPI-Umrechnung). **Bleibt rein passiv** wie
  `ControlBorderPage` — ruft selbst nie einen Command auf (kein Capability-Eintrag nötig): das
  Auto-Hide (25 s) läuft rein in Rust über einen Generationszähler, damit ein alter Timer nie ein
  inzwischen neu gezeigtes Ziel wieder verbirgt. `frontend/src/pages/PointerOverlayPage.tsx` hört
  nur auf `pointer://show` und zeichnet einen pulsierenden Ring + Label an der gemeldeten Position.
- **Frontend-Trigger:** `OverlayPage.tsx`s Assistent-Chat hat jetzt neben „Senden" einen
  „Zeigen"-Button (`handleShowPointer`), der `askObservePoint` aufruft und bei Erfolg
  `nativeInvoke("pointer_show", {x, y, label})` feuert; `handleStop()` blendet den Zeiger beim
  Beenden zusätzlich aus.
- **Nebenbei gefundener Korrektheitsfehler (mitbehoben):** `@ui-tars/action-parser` skaliert die
  vom Modell gemeldeten `start_box`-Zahlen je nach UI-TARS-Generation unterschiedlich — v1.0 nutzt
  ein festes 1000×1000-Raster, v1.5 ein screenshotgrößenabhängiges „Smart-Resize"-Raster — und
  entscheidet das über `uiTarsVersion`/`modelVer`, das aber **weder `GUIAgent` noch `actionParser`
  selbst aus dem Modellnamen ableiten**; ohne diesen Parameter wird still auf v1.0-Mathematik
  zurückgefallen. Der bisherige Selbst-Steuerung-Code (`new GUIAgent({...})`) setzte
  `uiTarsVersion` nie, obwohl `vlm_model` standardmäßig `ui-tars-1.5-7b` ist — vermutlich ein
  Mitverursacher der gemeldeten Fehlklicks. Behoben: `UI_TARS_VERSION` (aus `VLM_MODEL`s Namen
  hergeleitet, `/1\.5/` → `V1_5`) wird jetzt sowohl an `GUIAgent` (`uiTarsVersion`) als auch an
  `groundPoint()`s `actionParser()`-Aufruf (`modelVer`) durchgereicht.
- **Bekannte, bewusst nicht behobene Grenzen:** wie der „KI hat Kontrolle"-Rand deckt auch dieses
  Fenster nur den **primären Monitor**, und `NutJSOperator.screenshot()` erfasst ebenfalls nur
  diesen — auf Mehrschirm-Setups zeigt der Zeiger also nur korrekt, wenn das Zielelement dort sitzt.
  Getestet werden sollte das insbesondere auf dem ungewöhnlichen Seitenverhältnis-Monitor, auf dem
  das ursprüngliche Feedback entstand (DPI-/Scale-Mapping-Fehler zeigen sich dort eher).
- **Nächster Schritt (nicht Teil dieser Umsetzung):** der größere Selbst-Steuerung-Umbau
  (Accessibility-/DOM-first-Automatisierung statt reinem Vision-Grounding, austauschbare
  Grounding-Backends inkl. Cloud-Opt-in) — siehe der oben verlinkte Plan, Phase 2.

## AI-Cursor-Pointer: Assistent-Umbau + Koordinaten-Bugfix (R5.3)

Nach R5.2 zeigte der Ring in der Praxis **komplett falsch** (auf einem 3440×1440-Ultrawide ~0,6×
zu weit links/oben), und der Frage-/Zeigen-Pfad blieb oft stumm. Beides ist hier behoben:

- **Assistent-UX vereinfacht** (`OverlayPage.tsx`): statt Zwei-Knopf-Split (Mini-Pin „Zeigen" vs.
  blaues „Senden") jetzt **ein** immer sichtbares Frage-Feld + ein „Zeigen & Antworten"-Button
  (`handleAssist`), der **standardmäßig beides** liefert — Text-Antwort **und** Ring (Redundanz gegen
  ungenaues 7B-Grounding). Die Live-Beobachtungsschleife ist optional/aus per Default; ein leeres
  Session-Guard führt nicht mehr zu wortlosem Nichts, sondern startet die Bridge on-demand
  (`ensureBridge`). Die `/observe/point`- und `/observe/ask`-Endpoints der Bridge sind jetzt
  **stateless** (brauchen keine laufende Observe-Session mehr).
- **Root-Cause des Fehlzeigers — stiller Downscale des VLM-Servers:** UI-TARS-1.5 schreibt
  Klick-Koordinaten im Pixelraum **des Bildes, das es tatsächlich bekommt**. Wird der rohe
  Screenshot an einen OpenAI-kompatiblen Server (LM Studio/Ollama) geschickt, skaliert **dieser** ihn
  vor dem Modell still auf sein eigenes Vision-Budget herunter (hier gemessen ~2055 px Breite statt
  3440), das Modell zählt in diesem kleineren Rahmen — `actionParser` normalisiert die Zahlen aber
  gegen die **native** Größe (`smartResizeForV15` gibt bei <12,8 MP unverändert ≈3444×1428 zurück).
  Ergebnis: konstant um ~0,6× gestauchte Koordinaten (empirisch bestätigt — kein Modell-Streuen,
  sondern konstanter Faktor).
- **Fix (`groundPoint`, `server.mjs`):** den Screenshot **selbst** per Qwen2.5-VL-„Smart-Resize" auf
  ein bekanntes Budget herunterskalieren (Default `1280·28²` ≈ 1,0 MP, sicher unter LM Studios Cap;
  per `GROUNDING_MAX_PIXELS` überschreibbar), **dieses** Bild senden, die Modell-Koordinaten gegen die
  **gesendete** Größe normalisieren (`actionParser` mit `screenContext={sentWidth,sentHeight}`) und die
  resultierende [0,1]-Box mit den **nativen** Dims auf den Bildschirm abbilden
  (`parseBoxToScreenCoords`, dessen Faktor sich herauskürzt). Verifiziert gegen echten Screenshot +
  echtes VLM: ein korrekt erkanntes Ziel (Telegram-Icon) landet jetzt auf ~8 px genau statt ~620 px
  daneben. Reststreuung bei winzigen, dicht gepackten Desktop-Icons ist ein **separates**
  Modell-Erkennungsproblem (7B), unabhängig von der Koordinaten-Mathematik.

## Desktop Companion: screen-aware Chat + AI-Pointer + Bereich erklären (R6)

Der **Companion** ist der neue Default-Modus des AI-Cursor-Overlays (Hotkey
`Ctrl/Cmd+Shift+Space`): ein Assistent, der den Bildschirm *sieht*, Fragen auf Deutsch
beantwortet und mit dem Zeiger-Ring *zeigt*, wo man klicken kann — er klickt/tippt **niemals**
selbst. Er ist komplett **UI-TARS-frei**; der bisherige UI-TARS-Pfad (Selbst-Steuerung über
`bridge/uitars/`) bleibt unverändert als Legacy-Modus im selben Overlay erhalten.

**Architektur (pro Frage genau ein Vision-Roundtrip):**

1. **Rust-Capture** (`src-tauri/src/capture.rs`): `capture_screen` schießt den Primärmonitor
   nativ via `xcap` (physische Pixel + `scale_factor`). Die eigenen Overlay-Fenster sind per
   `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)` (Win10 2004+) von Captures ausgenommen;
   schlägt das fehl (`wda_ok=false`, wird pro Fenster geloggt), greift deterministisch der
   Fallback verstecken → ~180 ms warten → capturen → wiederherstellen. Der Pointer-Ring wird
   vor jedem Capture immer versteckt.
2. **Backend** (`query/screen_companion.py`, `POST /companion/guide|ask`, `GET /companion/config`):
   skaliert den Screenshot **selbst** per Qwen-Smart-Resize auf ein bekanntes Pixelbudget
   (28er-Vielfache, Default `1280·28²` ≈ 1,0 MP — dieselbe Kur wie der R5.3-Koordinaten-Bugfix:
   LM Studio/Ollama dürfen nie still weiterskalieren), schickt ihn mit einem strikten
   Absolut-Pixel-JSON-Prompt durch den **LLMRouter** und skaliert die Antwort-Koordinaten auf
   Original-Pixel zurück (geclippt, ≤6 Schritte). Antwortet das Modell ohne valides JSON,
   degradiert `guide` zu einer Text-Antwort statt zu scheitern. Konfiguration im
   `companion:`-Block der `config.yaml` (Default-Provider/-Modell, `grounding_max_pixels`,
   `history_turns`).
3. **Modelle über den normalen Provider-Matrix-Picker** (im Overlay, persistiert in
   localStorage): lokal z. B. **Qwen3-VL** in LM Studio (Empfehlung; `qwen/qwen3-vl-8b`) oder
   **Claude** über den neuen `anthropic`-Provider in `query/llm_router.py`
   (`/v1/messages`, top-level `system`, Base64-Image-Blöcke, `max_tokens` Pflicht; Sampling-
   Parameter werden bewusst weggelassen, weil neuere Claude-Modelle Nicht-Defaults ablehnen).
   Key nur per `ANTHROPIC_API_KEY` in `.env`.
4. **Pointer** (`PointerOverlayPage.tsx` + `pointer_show {space:"physical"}`): der Ring
   **gleitet** per CSS-Transform-Transition zum Ziel (Koordinaten ÷ eigener
   `devicePixelRatio`, da das Fenster physisch auf Monitorgröße gesetzt ist). Mehrschritt-
   Antworten laufen über den „Weiter (n/m)"-Button. **Wegschieben:** das Fenster ist
   click-through, also weicht der Ring dem echten Cursor aus (50-ms-Poll von
   `cursor_position`, nur solange sichtbar; Mathe in `frontend/src/pointerMath.ts`) und faded
   dabei auf ~0,35. Auto-Hide nach 25 s + `pointer://hide` stoppt den Poll garantiert.
5. **Bereich erklären** (Overlay-Button + Tray-Menü): `snip_start` capturet **zuerst** den
   ganzen Bildschirm (Freeze-Frame), zeigt dann das `snip`-Fenster mit dem eingefrorenen Bild
   + Marquee (Snipping-Tool-Look; Dim/Fadenkreuz können die Aufnahme nie verschmutzen).
   Rechteck loslassen → Rust croppt aus dem State → `snip://result` hängt den Ausschnitt als
   Chip an die nächste Frage (`/companion/ask` mit `region=true`). Esc oder Mini-Drag (<8 px)
   bricht ab.

**Privacy:** Screenshots verlassen den Rechner **nur**, wenn im Picker explizit der
`anthropic`-Provider gewählt ist (Opt-in durch Auswahl; ein Hinweis erscheint direkt unter dem
Picker). Default ist der lokale Provider aus `companion.provider` (LM Studio). Es wird nur der
**Primärmonitor** erfasst (bekannte Limitation, wie beim Legacy-Pfad). Chat-Verlauf wird als
Text-Historie mitgeschickt (`history_turns`), Bilder werden nie erneut übertragen.

**Web-Modus:** ohne Tauri-Shell erklärt der Companion im Chat, dass er den Bildschirm nur in
der nativen App sehen kann; „Bereich erklären" zeigt denselben Hinweis.

## Jupyter-Sidecar (R3)

Ein eigener Tab **„Jupyter"** (`/jupyter`) startet **JupyterLab** als **optionalen, verwalteten
Sidecar** und bettet es als iframe ein. *Optional*: Jupyter wird **nicht** ins Standalone-Bundle
gepackt — fehlt es, zeigt der Tab einen `pip install jupyterlab`-Hinweis. **Nur nativ** (im Web-Modus
ein Hinweis), da es einen Kindprozess braucht.

- **Rust** (`src-tauri/src/jupyter.rs`): `JupyterState(Mutex<…>)` hält den einen Server. Command
  `jupyter_start` startet (oder reused) den Server über das **venv-Python** (`python -m jupyter lab
  --no-browser --ServerApp.ip=127.0.0.1 --ServerApp.port=<frei> --IdentityProvider.token=<rand>`) —
  dieselbe `.venv`-Auflösung wie der Backend-Sidecar (`crate::python_executable`, jetzt `pub(crate)`).
  stdout/stderr werden in einen gekappten Puffer **mitgelesen** (Drain-Threads); danach wartet der
  Command auf `wait_until_ready` und **wertet das Ergebnis aus**: bindet der Server nicht, wird das Kind
  gekillt und der **echte stderr** als Fehler zurückgegeben (statt einer URL für einen toten Server).
  Gibt sonst die Token-URL `http://127.0.0.1:<port>/lab?token=…` zurück; `jupyter_stop` killt ihn, der
  Exit-Hook (`jupyter::kill`) ebenso → kein verwaister Prozess. **Wiederverwendung:** `python_executable`/
  `pick_free_port`/`wait_until_ready`/`hide_console`/`project_root` aus `lib.rs`, Token via `getrandom`.
  *Hinweis:* JupyterLab muss im `.venv` installiert sein (`pip install jupyterlab`) — das Meta-Paket
  `jupyter` allein reicht für `jupyter lab` nicht.
- **CSP/Framing:** Jupyters Default `frame-ancestors 'self'` würde das Einbetten im Tauri-Webview
  (anderer Origin) blockieren. Deshalb überschreibt der Start es per
  `--ServerApp.tornado_settings={'headers': {'Content-Security-Policy': "frame-ancestors *"}}` (ein
  argv-Element, ohne Shell → kein Quoting-Problem; traitlets parst das Dict). Der Server ist
  loopback-gebunden und token-geschützt; `tauri.conf.json` hat `csp: null`, `127.0.0.1` gilt als
  secure context → kein Mixed-Content-Block. Custom-Commands brauchen **keinen** Capability-Eintrag.
- **Frontend** (`frontend/src/pages/JupyterPage.tsx`): startet beim Mount automatisch
  (`nativeInvoke("jupyter_start")`), rendert die Token-URL im `<iframe>`; Buttons „Neu starten"/
  „Stoppen". Tab-Wechsel killt den Server **nicht** (Kernel überleben Navigation; Aufräumen via
  Exit-Hook). Nav-Eintrag + Route in `App.tsx`.

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
