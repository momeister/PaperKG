// Optional JupyterLab sidecar (roadmap R3).
//
// A lazily-started, app-managed JupyterLab server. `jupyter_start` spawns
// `jupyter lab` on a free localhost port with a random token and hands the
// frontend a token URL it embeds in an iframe; `jupyter_stop` (and the app-exit
// hook) kill it so no Jupyter process is orphaned. Jupyter stays *optional* — it
// is not bundled into the standalone installer; if it is not installed the spawn
// fails and the frontend shows a `pip install jupyterlab` hint.
//
// Native only — the web app shows a hint instead (there is no managed sidecar in
// a plain browser).

use std::path::PathBuf;
use std::process::{Child, Command};
use std::sync::Mutex;
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

/// Locate the `jupyter` launcher: prefer the project's `.venv` (dev), else PATH.
/// In a release bundle there is no `.venv`, so it falls back to PATH — and if
/// Jupyter is not installed the spawn simply fails (surfaced as a hint).
fn jupyter_executable() -> PathBuf {
    let root = crate::project_root();
    #[cfg(windows)]
    let venv = root.join(".venv").join("Scripts").join("jupyter.exe");
    #[cfg(not(windows))]
    let venv = root.join(".venv").join("bin").join("jupyter");
    if venv.exists() {
        venv
    } else {
        PathBuf::from("jupyter")
    }
}

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

/// Start (or reuse) the JupyterLab server and return its token URL.
#[tauri::command]
pub fn jupyter_start(app: AppHandle, state: State<'_, JupyterState>) -> Result<String, String> {
    let mut guard = state.0.lock().map_err(|err| err.to_string())?;

    // Reuse a still-running server instead of spawning a second one.
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

    let exe = jupyter_executable();
    let port = crate::pick_free_port();
    let token = random_token();

    let mut cmd = Command::new(&exe);
    cmd.arg("lab")
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
        );
    if let Ok(home) = app.path().home_dir() {
        cmd.current_dir(home);
    }
    crate::hide_console(&mut cmd);

    let child = cmd.spawn().map_err(|err| {
        format!(
            "JupyterLab konnte nicht gestartet werden ({}): {err}. \
             Ist es installiert? -> pip install jupyterlab",
            exe.display()
        )
    })?;

    let url = format!("http://127.0.0.1:{port}/lab?token={token}");
    guard.child = Some(child);
    guard.url = Some(url.clone());

    // Best-effort: give the server a moment to bind so the first iframe load already
    // reaches a live page. If it is slow the URL is still valid — a retry returns
    // this same (by then live) URL rather than spawning a duplicate server.
    drop(guard);
    let _ = crate::wait_until_ready(port, Duration::from_secs(30));

    Ok(url)
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
