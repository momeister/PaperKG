// Optional UI-TARS desktop-agent sidecar (Desktop-Agent v2: Selbst-Steuerung + Assistent).
//
// A lazily-started, app-managed Node.js process (`bridge/uitars/server.mjs`) that screenshots
// the screen and either drives mouse/keyboard (Selbst-Steuerung) or just describes what it
// sees (Assistent), reasoning via a local VLM. `agent_bridge_ensure` starts it (or reuses a
// live one) on a free localhost port and returns that port to the frontend, which then talks
// to it through the backend's `/agent/*` relay endpoints (`api/product_main.py`).
// `agent_bridge_stop` (and the app-exit hook) hard-kill it — the guaranteed fallback for
// "cancel at any time" even if a single in-flight step doesn't honor a graceful abort.
//
// Optional: if Node.js is not on PATH or `npm install` hasn't been run in `bridge/uitars`,
// `agent_bridge_ensure` fails with an actionable error and both modes fall back to the
// manual Kanal-A flow (copy the brief). Not bundled into the standalone installer yet — see
// the Risks section in docs/NATIVE_APP.md.
//
// Native only — the web app talks to a manually-started bridge over the `agent_bridge.url`
// fallback instead.

use std::io::Read;
use std::net::TcpStream;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::{Duration, Instant};

use tauri::{AppHandle, Manager, State};

/// App-managed handle to the single live bridge process.
#[derive(Default)]
pub struct AgentBridgeState(Mutex<AgentBridgeInner>);

#[derive(Default)]
struct AgentBridgeInner {
    child: Option<Child>,
    port: Option<u16>,
}

/// Keep at most this many bytes of recent bridge output (npm-install hints, stack traces).
const LOG_CAP: usize = 16 * 1024;

/// Continuously drain a child pipe into a shared, length-capped buffer — see
/// `jupyter.rs`'s identical helper for why (keeps OS pipe buffers from filling and
/// stalling the process, and lets us report the real error if it never binds).
fn drain_into(mut reader: impl Read, logs: Arc<Mutex<String>>) {
    let mut buf = [0u8; 4096];
    loop {
        match reader.read(&mut buf) {
            Ok(0) | Err(_) => break,
            Ok(n) => {
                if let Ok(mut s) = logs.lock() {
                    s.push_str(&String::from_utf8_lossy(&buf[..n]));
                    if s.len() > LOG_CAP {
                        let mut cut = s.len() - LOG_CAP;
                        while cut < s.len() && !s.is_char_boundary(cut) {
                            cut += 1;
                        }
                        *s = s.split_off(cut);
                    }
                }
            }
        }
    }
}

/// `node` on PATH, unless `SCIENCEKG_NODE_PATH` overrides it (e.g. a non-PATH install).
fn node_executable() -> String {
    std::env::var("SCIENCEKG_NODE_PATH").unwrap_or_else(|_| "node".to_string())
}

/// Start (or reuse) the bridge and return its port. The `vlm_*`/`observe_*` args come
/// from the resolved `agent_bridge:` config (`/agent/config`) and are forwarded to the
/// spawned process as env vars, overriding `server.mjs`'s own hardcoded defaults; any
/// left `None`/empty just fall through to those defaults unchanged.
#[tauri::command]
pub fn agent_bridge_ensure(
    state: State<'_, AgentBridgeState>,
    vlm_base_url: Option<String>,
    vlm_model: Option<String>,
    helper_vlm_model: Option<String>,
    observe_interval_seconds: Option<u32>,
    observe_context_size: Option<u32>,
) -> Result<u16, String> {
    // Reuse a still-running bridge instead of spawning a second one.
    {
        let mut guard = state.0.lock().map_err(|err| err.to_string())?;
        if let Some(child) = guard.child.as_mut() {
            if matches!(child.try_wait(), Ok(None)) {
                if let Some(port) = guard.port {
                    return Ok(port);
                }
            }
        }
        // Stale handle (process gone or never had a port) — drop it before starting fresh.
        guard.child = None;
        guard.port = None;
    }

    let bridge_dir = crate::project_root().join("bridge").join("uitars");
    if !bridge_dir.join("server.mjs").exists() {
        return Err(format!(
            "Desktop-Agent-Bridge nicht gefunden unter {}. Vollständiges Repo-Checkout nötig.",
            bridge_dir.display()
        ));
    }
    if !bridge_dir.join("node_modules").exists() {
        return Err(format!(
            "Abhängigkeiten fehlen: `npm install` in {} ausführen.",
            bridge_dir.display()
        ));
    }

    let port = crate::pick_free_port();
    let mut cmd = Command::new(node_executable());
    cmd.arg("server.mjs")
        .current_dir(&bridge_dir)
        .env("BRIDGE_PORT", port.to_string());
    if let Some(v) = vlm_base_url.filter(|s| !s.is_empty()) {
        cmd.env("VLM_BASE_URL", v);
    }
    if let Some(v) = vlm_model.filter(|s| !s.is_empty()) {
        cmd.env("VLM_MODEL", v);
    }
    if let Some(v) = helper_vlm_model.filter(|s| !s.is_empty()) {
        cmd.env("HELPER_VLM_MODEL", v);
    }
    if let Some(v) = observe_interval_seconds.filter(|v| *v > 0) {
        cmd.env("OBSERVE_INTERVAL_MS", (v * 1000).to_string());
    }
    if let Some(v) = observe_context_size.filter(|v| *v > 0) {
        cmd.env("OBSERVE_CONTEXT_SIZE", v.to_string());
    }
    cmd.stdout(Stdio::piped())
        .stderr(Stdio::piped());
    crate::hide_console(&mut cmd);

    let mut child = cmd.spawn().map_err(|err| {
        format!(
            "Desktop-Agent-Bridge konnte nicht gestartet werden (Node.js nicht gefunden?): {err}.\n\
             Node.js >= 18 installieren (https://nodejs.org) oder SCIENCEKG_NODE_PATH setzen."
        )
    })?;

    // Drain both pipes into a capped buffer (kept for the process's lifetime).
    let logs = Arc::new(Mutex::new(String::new()));
    if let Some(out) = child.stdout.take() {
        let logs = Arc::clone(&logs);
        thread::spawn(move || drain_into(out, logs));
    }
    if let Some(err) = child.stderr.take() {
        let logs = Arc::clone(&logs);
        thread::spawn(move || drain_into(err, logs));
    }

    {
        let mut guard = state.0.lock().map_err(|err| err.to_string())?;
        guard.child = Some(child);
        guard.port = Some(port);
    }

    // Wait until the bridge actually accepts connections, but also poll the child's
    // exit status on every tick. Node's ESM loader throws on a broken import
    // synchronously at startup — well under a second — so a dead child should fail
    // fast instead of burning the full 20s timeout waiting for a port that will
    // never open. This is a local variant of `crate::wait_until_ready` (left
    // unmodified — the backend and Jupyter sidecars still use it as-is).
    let deadline = Instant::now() + Duration::from_secs(20);
    let mut died_early = false;
    loop {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return Ok(port);
        }
        {
            let mut guard = state.0.lock().map_err(|err| err.to_string())?;
            let still_alive = matches!(guard.child.as_mut().map(|c| c.try_wait()), Some(Ok(None)));
            if !still_alive {
                died_early = true;
            }
        }
        if died_early || Instant::now() >= deadline {
            break;
        }
        thread::sleep(Duration::from_millis(250));
    }

    let mut guard = state.0.lock().map_err(|err| err.to_string())?;
    if let Some(mut child) = guard.child.take() {
        let _ = child.kill();
    }
    guard.port = None;
    let captured = logs.lock().map(|s| s.trim().to_string()).unwrap_or_default();
    let detail = if !captured.is_empty() {
        captured
    } else if died_early {
        "Bridge-Prozess wurde beendet, bevor er bereit war.".to_string()
    } else {
        "Bridge hat innerhalb von 20s keinen Server gestartet (Zeitüberschreitung).".to_string()
    };
    Err(format!("{detail}\n\n(gestartet aus {})", bridge_dir.display()))
}

/// Hard-kill the bridge — the guaranteed fallback for "cancel at any time", used when
/// a graceful `/agent/cancel` or `/agent/observe/stop` doesn't land in time. Also hides
/// the "AI has control" border natively, so it can never stay stuck on screen even if
/// the frontend's own cleanup path didn't run (e.g. the sidecar died mid-step).
#[tauri::command]
pub fn agent_bridge_stop(app: AppHandle, state: State<'_, AgentBridgeState>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|err| err.to_string())?;
    if let Some(mut child) = guard.child.take() {
        let _ = child.kill();
    }
    guard.port = None;
    crate::overlay::control_border_hide(app);
    Ok(())
}

/// Kill the bridge on app exit so it is never orphaned.
pub fn kill(app: &AppHandle) {
    if let Some(state) = app.try_state::<AgentBridgeState>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.child.take() {
                let _ = child.kill();
            }
            guard.port = None;
        }
    }
}
