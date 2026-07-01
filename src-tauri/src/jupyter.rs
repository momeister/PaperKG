// Optional JupyterLab sidecar (roadmap R3).
//
// A lazily-started, app-managed JupyterLab server. `jupyter_start` runs
// `python -m jupyter lab` from the project's `.venv` on a free localhost port
// with a random token and hands the frontend a token URL it embeds in an
// iframe; `jupyter_stop` (and the app-exit hook) kill it so no Jupyter process
// is orphaned. Jupyter stays *optional* — it is not bundled into the standalone
// installer; if `jupyterlab` is not installed the server never binds and we
// surface the captured stderr + a `pip install jupyterlab` hint.
//
// Native only — the web app shows a hint instead (there is no managed sidecar in
// a plain browser).

use std::io::Read;
use std::process::{Child, Command, Stdio};
use std::sync::{Arc, Mutex};
use std::thread;
use std::time::Duration;

use tauri::{AppHandle, Manager, State};

/// App-managed handle to the single live JupyterLab server.
#[derive(Default)]
pub struct JupyterState(Mutex<JupyterInner>);

#[derive(Default)]
struct JupyterInner {
    child: Option<Child>,
    url: Option<String>,
}

/// Keep at most this many bytes of recent server output, so a long-running
/// server's logs cannot grow the buffer without bound.
const LOG_CAP: usize = 16 * 1024;

/// 16 random bytes as hex, used as the Jupyter access token.
fn random_token() -> String {
    let mut buf = [0u8; 16];
    if getrandom::getrandom(&mut buf).is_err() {
        // OS entropy unavailable (very rare) — fall back to a time seed. The token
        // only guards a loopback-bound server, so this is acceptable.
        let nanos = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .map(|d| d.as_nanos())
            .unwrap_or(0);
        buf = nanos.to_le_bytes();
    }
    buf.iter().map(|b| format!("{b:02x}")).collect()
}

/// Continuously drain a child pipe into a shared, length-capped buffer. Running
/// this for both stdout and stderr keeps the OS pipe buffers from filling (which
/// would stall the server) and lets us report the real error if it never binds.
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

/// Start (or reuse) the JupyterLab server and return its token URL.
#[tauri::command]
pub fn jupyter_start(app: AppHandle, state: State<'_, JupyterState>) -> Result<String, String> {
    // Reuse a still-running server instead of spawning a second one.
    {
        let mut guard = state.0.lock().map_err(|err| err.to_string())?;
        if let Some(child) = guard.child.as_mut() {
            if matches!(child.try_wait(), Ok(None)) {
                if let Some(url) = guard.url.clone() {
                    return Ok(url);
                }
            }
        }
        // Stale handle (process gone or never had a URL) — drop it before starting fresh.
        guard.child = None;
        guard.url = None;
    }

    let root = crate::project_root();
    let python = crate::python_executable(&root);
    let port = crate::pick_free_port();
    let token = random_token();

    // Run via the venv interpreter (`python -m jupyter lab`) so PATH/venv
    // resolution matches the backend sidecar and the right `jupyterlab` is used.
    let mut cmd = Command::new(&python);
    cmd.arg("-m")
        .arg("jupyter")
        .arg("lab")
        .arg("--no-browser")
        .arg("--ServerApp.ip=127.0.0.1")
        .arg(format!("--ServerApp.port={port}"))
        .arg(format!("--IdentityProvider.token={token}"))
        // Override Jupyter's default `frame-ancestors 'self'` so the lab UI can be
        // framed by the Tauri webview (a different origin: tauri://localhost, or the
        // Vite dev server). The server is loopback-only and token-protected, so
        // allowing any framer here is fine. Passed as a single argv element (we spawn
        // without a shell), so the quotes survive; traitlets literal_eval-parses the dict.
        .arg(
            "--ServerApp.tornado_settings={'headers': {'Content-Security-Policy': \"frame-ancestors *\"}}",
        )
        .stdout(Stdio::piped())
        .stderr(Stdio::piped());
    if let Ok(home) = app.path().home_dir() {
        cmd.current_dir(home);
    }
    crate::hide_console(&mut cmd);

    let mut child = cmd.spawn().map_err(|err| {
        format!(
            "JupyterLab konnte nicht gestartet werden ({}): {err}.\nIst es im Backend-venv installiert? -> pip install jupyterlab",
            python.display()
        )
    })?;

    // Drain both pipes into a capped buffer (kept for the server's lifetime).
    let logs = Arc::new(Mutex::new(String::new()));
    if let Some(out) = child.stdout.take() {
        let logs = Arc::clone(&logs);
        thread::spawn(move || drain_into(out, logs));
    }
    if let Some(err) = child.stderr.take() {
        let logs = Arc::clone(&logs);
        thread::spawn(move || drain_into(err, logs));
    }

    let url = format!("http://127.0.0.1:{port}/lab?token={token}");
    {
        let mut guard = state.0.lock().map_err(|err| err.to_string())?;
        guard.child = Some(child);
        guard.url = Some(url.clone());
    }

    // Wait until the server actually accepts connections. If it never binds the
    // process is dead/broken — kill it and surface the captured output instead of
    // returning a URL that would load a blank/refused iframe.
    if crate::wait_until_ready(port, Duration::from_secs(30)) {
        return Ok(url);
    }

    let mut guard = state.0.lock().map_err(|err| err.to_string())?;
    if let Some(mut child) = guard.child.take() {
        let _ = child.kill();
    }
    guard.url = None;
    let captured = logs.lock().map(|s| s.trim().to_string()).unwrap_or_default();
    let detail = if captured.is_empty() {
        "JupyterLab hat innerhalb von 30s keinen Server gestartet (Zeitüberschreitung).".to_string()
    } else {
        captured
    };
    Err(format!(
        "{detail}\n\n(Start über {}; falls nötig im Backend-venv installieren: pip install jupyterlab)",
        python.display()
    ))
}

/// Stop the JupyterLab server (invoked by the "Stoppen" / restart controls).
#[tauri::command]
pub fn jupyter_stop(state: State<'_, JupyterState>) -> Result<(), String> {
    let mut guard = state.0.lock().map_err(|err| err.to_string())?;
    if let Some(mut child) = guard.child.take() {
        let _ = child.kill();
    }
    guard.url = None;
    Ok(())
}

/// Kill the JupyterLab server — called on app exit so it is never orphaned.
pub fn kill(app: &AppHandle) {
    if let Some(state) = app.try_state::<JupyterState>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(mut child) = guard.child.take() {
                let _ = child.kill();
            }
            guard.url = None;
        }
    }
}
