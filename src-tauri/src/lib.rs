// Native ScienceKG desktop shell.
//
// The React frontend and the FastAPI backend are unchanged. This shell:
//   1. picks a free localhost port,
//   2. starts the FastAPI backend as a managed child process (the "sidecar"),
//   3. injects the backend origin into the page as `window.__API_BASE__`,
//   4. opens the main window on the bundled frontend (Tauri asset protocol in a
//      release build, the Vite dev server during `tauri dev`),
//   5. terminates the backend when the app exits.
//
// Two sidecar flavours (selected by build profile):
//   * Debug (`tauri dev`): the project's `.venv` Python running uvicorn, CWD =
//     repo root — fast iteration against the live source tree (M1 behaviour).
//   * Release (bundled installer): the PyInstaller binary shipped as a Tauri
//     resource (`sciencekg-backend/sciencekg-backend.exe`), CWD = the per-user
//     data dir (`%APPDATA%/<identifier>`). The backend reads config.yaml /
//     ontology.yaml / data/ relative to that CWD, so on first run we seed the
//     bundled default config/ontology there (M2).

use std::net::{TcpListener, TcpStream};
use std::path::{Path, PathBuf};
use std::process::{Child, Command};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use tauri::path::BaseDirectory;
use tauri::{AppHandle, Manager, RunEvent, WebviewUrl, WebviewWindowBuilder};
use tauri_plugin_opener::OpenerExt;

mod jupyter;
mod overlay;
mod terminal;

/// Open a URL in the OS default application (browser for web sources, the system
/// PDF viewer/browser for PDF links). Called from the frontend when running in
/// the native shell, where a webview cannot open a "new tab" itself.
#[tauri::command]
fn open_external(app: tauri::AppHandle, url: String) -> Result<(), String> {
    app.opener()
        .open_url(url, None::<&str>)
        .map_err(|err| err.to_string())
}

/// Handle to the FastAPI sidecar so it can be killed on exit.
struct Backend(Mutex<Option<Child>>);

/// Repo root = parent of this `src-tauri` crate directory. Only meaningful in a
/// dev build, where the source tree is present.
pub(crate) fn project_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .parent()
        .map(Path::to_path_buf)
        .unwrap_or_else(|| PathBuf::from("."))
}

/// Prefer the project's local virtualenv interpreter; fall back to PATH `python`.
fn python_executable(root: &Path) -> PathBuf {
    #[cfg(windows)]
    let venv = root.join(".venv").join("Scripts").join("python.exe");
    #[cfg(not(windows))]
    let venv = root.join(".venv").join("bin").join("python");
    if venv.exists() {
        venv
    } else {
        PathBuf::from("python")
    }
}

/// Ask the OS for a free localhost TCP port.
pub(crate) fn pick_free_port() -> u16 {
    TcpListener::bind("127.0.0.1:0")
        .and_then(|l| l.local_addr())
        .map(|addr| addr.port())
        .unwrap_or(8000)
}

fn to_io_err(err: impl std::fmt::Display) -> std::io::Error {
    std::io::Error::new(std::io::ErrorKind::Other, err.to_string())
}

/// Don't pop up a separate console window for the child on Windows.
pub(crate) fn hide_console(cmd: &mut Command) {
    #[cfg(windows)]
    {
        use std::os::windows::process::CommandExt;
        const CREATE_NO_WINDOW: u32 = 0x0800_0000;
        cmd.creation_flags(CREATE_NO_WINDOW);
    }
    let _ = cmd;
}

/// Dev sidecar: run uvicorn from the project's `.venv` against the live source.
/// No `--reload`: a single process we can cleanly kill.
fn start_backend_dev(port: u16) -> std::io::Result<Child> {
    let root = project_root();
    let python = python_executable(&root);
    let mut cmd = Command::new(python);
    cmd.current_dir(&root).args([
        "-m",
        "uvicorn",
        "api.product_main:app",
        "--host",
        "127.0.0.1",
        "--port",
        &port.to_string(),
    ]);
    hide_console(&mut cmd);
    cmd.spawn()
}

/// Release sidecar: the bundled PyInstaller backend, run from the per-user data
/// dir so all of the backend's CWD-relative reads (config.yaml, ontology.yaml,
/// data/) resolve there. Seeds the default config/ontology on first run.
fn start_backend_bundled(app: &AppHandle, port: u16) -> std::io::Result<Child> {
    let data_dir = app.path().app_data_dir().map_err(to_io_err)?;
    std::fs::create_dir_all(data_dir.join("data"))?;
    seed_defaults(app, &data_dir);

    let rel = if cfg!(windows) {
        "sidecar/sciencekg-backend/sciencekg-backend.exe"
    } else {
        "sidecar/sciencekg-backend/sciencekg-backend"
    };
    let exe = app
        .path()
        .resolve(rel, BaseDirectory::Resource)
        .map_err(to_io_err)?;

    let mut cmd = Command::new(exe);
    cmd.current_dir(&data_dir)
        .args(["--port", &port.to_string()]);
    hide_console(&mut cmd);
    cmd.spawn()
}

/// Copy the bundled default config.yaml / ontology.yaml into the data dir if they
/// are not already present (first launch). Existing user files are never touched.
fn seed_defaults(app: &AppHandle, data_dir: &Path) {
    for name in ["config.yaml", "ontology.yaml"] {
        let dest = data_dir.join(name);
        if dest.exists() {
            continue;
        }
        if let Ok(src) = app
            .path()
            .resolve(format!("defaults/{name}"), BaseDirectory::Resource)
        {
            if let Err(err) = std::fs::copy(&src, &dest) {
                log::warn!("could not seed {name}: {err}");
            }
        }
    }
}

/// Start the appropriate sidecar for this build profile.
fn start_backend(app: &AppHandle, port: u16) -> std::io::Result<Child> {
    if cfg!(debug_assertions) {
        start_backend_dev(port)
    } else {
        start_backend_bundled(app, port)
    }
}

/// Wait until a localhost service accepts connections (the backend / Jupyter bind
/// their port only once fully started).
pub(crate) fn wait_until_ready(port: u16, timeout: Duration) -> bool {
    let deadline = Instant::now() + timeout;
    while Instant::now() < deadline {
        if TcpStream::connect(("127.0.0.1", port)).is_ok() {
            return true;
        }
        std::thread::sleep(Duration::from_millis(250));
    }
    false
}

fn kill_backend(app: &AppHandle) {
    if let Some(state) = app.try_state::<Backend>() {
        if let Ok(mut guard) = state.0.lock() {
            if let Some(child) = guard.as_mut() {
                let _ = child.kill();
            }
        }
    }
}

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    let port = pick_free_port();
    let api_base = format!("http://127.0.0.1:{port}");

    // Runs before any page script, so the frontend already sees window.__API_BASE__
    // when api.ts evaluates.
    let init_script = format!("window.__API_BASE__ = {api_base:?};");

    tauri::Builder::default()
        .plugin(tauri_plugin_opener::init())
        .manage(terminal::TerminalState::default())
        .manage(jupyter::JupyterState::default())
        .invoke_handler(tauri::generate_handler![
            open_external,
            terminal::terminal_spawn,
            terminal::terminal_write,
            terminal::terminal_resize,
            terminal::terminal_kill,
            overlay::overlay_hide,
            overlay::overlay_toggle,
            jupyter::jupyter_start,
            jupyter::jupyter_stop,
        ])
        .setup(move |app| {
            if cfg!(debug_assertions) {
                app.handle().plugin(
                    tauri_plugin_log::Builder::default()
                        .level(log::LevelFilter::Info)
                        .build(),
                )?;
            }

            // AI-Cursor overlay (R1): a global hotkey toggles the always-on-top
            // window. Desktop only — the plugin is unavailable on mobile.
            #[cfg(desktop)]
            {
                use tauri_plugin_global_shortcut::ShortcutState;
                app.handle().plugin(
                    tauri_plugin_global_shortcut::Builder::new()
                        .with_handler(|app, _shortcut, event| {
                            if event.state() == ShortcutState::Pressed {
                                overlay::toggle_overlay(app);
                            }
                        })
                        .build(),
                )?;
                overlay::register_global_shortcut(app.handle())?;
            }

            // Spawn the backend (release: bundled binary needs the app handle to
            // resolve resource + data-dir paths, so it happens here, not earlier).
            let backend = start_backend(app.handle(), port).ok();
            app.manage(Backend(Mutex::new(backend)));

            // Cold start is a few seconds (heavy deps are lazy). Wait briefly so the
            // first page load already reaches a live API; cap it so a broken backend
            // can never hang the launch forever.
            let _ = wait_until_ready(port, Duration::from_secs(40));

            WebviewWindowBuilder::new(app.handle(), "main", WebviewUrl::App("index.html".into()))
                .title("ScienceKG")
                .inner_size(1440.0, 900.0)
                .min_inner_size(960.0, 600.0)
                .initialization_script(&init_script)
                .build()?;

            // AI-Cursor overlay (R1): hidden second window (shares the backend
            // origin via the same init script) + system-tray toggle.
            overlay::build_overlay(app.handle(), &init_script)?;
            overlay::setup_tray(app.handle())?;
            Ok(())
        })
        .build(tauri::generate_context!())
        .expect("error while building tauri application")
        .run(|app_handle, event| {
            if let RunEvent::ExitRequested { .. } | RunEvent::Exit = event {
                terminal::kill_all(app_handle);
                jupyter::kill(app_handle);
                kill_backend(app_handle);
            }
        });
}
