# PaperKG → UI-TARS desktop-agent bridge (optional, Kanal B)

This is the **optional automation channel** behind the AI-Cursor overlay's two modes —
**Selbst-Steuerung** (autonomous) and **Assistent** (live screen Q&A, no control). PaperKG
stays the *brain* (it knows your research question, the chosen variant and the grounded
literature, and compiles a precise **task brief**). This bridge is the *eyes/hands*: it runs
on **this machine** using the [UI-TARS](https://github.com/bytedance/UI-TARS-desktop) GUI
agent — it screenshots the screen, reasons with a **local VLM**, and (in Selbst-Steuerung
only) drives mouse/keyboard.

> ⚠️ **Selbst-Steuerung actually controls your computer.** Run it only on your own machine,
> keep it in view, and use Stop/Cancel to interrupt it. **Assistent** mode never moves the
> mouse/keyboard but does capture everything visible on screen — it's explicit opt-in and
> should be stopped when you're done. PaperKG itself never drives the machine or persists
> screenshots — it only hands over the text brief and relays SSE events/short descriptions.

In the native desktop app, **Tauri manages this process for you** (`agent_bridge.
manage_sidecar: true`, the default): it spawns `node server.mjs` on first use and hard-kills
it on Stop/Cancel or app exit, so you no longer need to run `npm start` by hand. The manual
steps below are for the **web app** (no Tauri shell) or for running the bridge standalone.

## You don't strictly need this bridge

The feature works **without** any of this via **Kanal A**: PaperKG shows the compiled brief
with a **Copy** button — paste it straight into the input box of the
[UI-TARS-Desktop](https://github.com/bytedance/UI-TARS-desktop) app and run it there. Use
this bridge only if you want PaperKG to dispatch the brief automatically (or run Assistent
mode) and stream progress back live.

## Prerequisites

1. **Node.js ≥ 18.**
2. **A local VLM serving UI-TARS**, reachable on an OpenAI-compatible endpoint. With your
   ≥16 GB GPU, `ui-tars-1.5-7b` runs comfortably. Two easy options:
   - **LM Studio** — load a UI-TARS-1.5-7B GGUF, start the server → `http://127.0.0.1:1234/v1`.
   - **Ollama** — `ollama pull ui-tars` (or a UI-TARS GGUF) → `http://127.0.0.1:11434/v1`.

   This is the *same* local provider you already use for text models in `config.yaml`; the
   bridge just points the GUI agent at it.

## Install & run (PowerShell)

```powershell
cd bridge/uitars
npm.cmd install
# point it at your local VLM (defaults shown):
$env:VLM_BASE_URL = "http://127.0.0.1:1234/v1"
$env:VLM_MODEL    = "ui-tars-1.5-7b"
npm.cmd start
```

You should see `listening on http://127.0.0.1:8787`. Health check: open
`http://127.0.0.1:8787/health`.

## Wire it into PaperKG

In `config.yaml` flip the bridge on (it is **off by default**):

```yaml
agent_bridge:
  enabled: true
  url: "http://127.0.0.1:8787/run"   # must match BRIDGE_PORT
```

Restart the product API. In the Workspace Parallelmodus → *Ergebnisse* tab, „An AI-Cursor
übergeben" now spawns the overlay pre-loaded with the brief; „Starten" in Selbst-Steuerung
mode POSTs the brief here and streams progress back live (and as an assistant entry once
done), „Beobachtung starten" in Assistent mode starts the screenshot-loop below.

## How it talks to PaperKG

**Selbst-Steuerung:**
- PaperKG `POST {url}` (i.e. `/run`) with `{"task": "<plain-text brief>"}`.
- The bridge replies with `text/event-stream`; each event is `data: {json}` where `json`
  is `{status:"started"|"step"|"done"|"error"|"aborted", runId?, from?, value?, error?, model?}`.
  PaperKG forwards these to the frontend.
- `POST /cancel` with `{"runId": "..."}` aborts the matching in-flight run gracefully (via
  `AbortSignal`); the Tauri shell additionally hard-kills this whole process as a guaranteed
  fallback if a step doesn't honor the abort in time.

**Assistent (live screen Q&A, no control):**
- `POST /observe/start` with `{"sessionId"?, "intervalMs"?, "primer"?}` → SSE stream of
  `{status:"started", sessionId}` then periodic `{status:"observation", value, t}` —
  `value` is a short text description of the current screen (image never leaves this
  process).
- `POST /observe/ask` with `{"sessionId", "question"}` → JSON `{"answer": "..."}`, answered
  against a fresh screenshot plus the session's rolling description history.
- `POST /observe/stop` with `{"sessionId"}` → stops the loop and ends the SSE stream.

## Env vars

| Var | Default | Meaning |
|---|---|---|
| `BRIDGE_PORT` | `8787` | Port (match `agent_bridge.url`; the Tauri sidecar overrides this per-launch with a free port) |
| `VLM_BASE_URL` | `http://127.0.0.1:1234/v1` | OpenAI-compatible VLM endpoint |
| `VLM_API_KEY` | `lm-studio` | API key (LM Studio ignores it) |
| `VLM_MODEL` | `ui-tars-1.5-7b` | Model used for Selbst-Steuerung (action grounding) |
| `HELPER_VLM_MODEL` | = `VLM_MODEL` | Model used for Assistent (freeform description/Q&A) — a general vision model often does better here than an action-grounding one |
| `OBSERVE_INTERVAL_MS` | `4000` | Screenshot cadence for Assistent mode |
| `OBSERVE_CONTEXT_SIZE` | `8` | How many rolling descriptions are kept per session |

## Notes

- `server.mjs` is a small reference implementation against the documented
  [`@ui-tars/sdk`](https://www.npmjs.com/package/@ui-tars/sdk) API. After `npm install`, if a
  newer SDK major renames an export (`GUIAgent` / `NutJSOperator`), adjust the two imports.
- The NutJS operator drives the local desktop. On Windows it works out of the box; on
  Linux/macOS see the UI-TARS-desktop docs for accessibility permissions.
