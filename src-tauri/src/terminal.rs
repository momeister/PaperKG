// Embedded terminal for the Code-Werkstatt (the "Agent" half of the dual view).
//
// A real cross-platform PTY via `portable-pty` (WezTerm's crate): Windows ConPTY /
// Unix openpty under one API. Each spawned terminal gets a reader thread that
// streams raw bytes to the frontend as a `terminal://output/<id>` event; the
// frontend renders them with xterm.js and sends keystrokes back via
// `terminal_write`. This is what lets AI coding CLIs (claude / Claude Code,
// opencode, codex), git and the shell run *inside* PaperKG.
//
// Only wired in the native shell — the web app shows a hint instead.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;

use portable_pty::{native_pty_system, Child, CommandBuilder, MasterPty, PtySize};
use tauri::{AppHandle, Emitter, Manager, State};

/// One live terminal: the PTY master (for resize), its writer, and the child.
struct TermSession {
    master: Box<dyn MasterPty + Send>,
    writer: Box<dyn Write + Send>,
    child: Box<dyn Child + Send + Sync>,
}

/// App-managed registry of live terminals, keyed by id.
#[derive(Default)]
pub struct TerminalState(Mutex<HashMap<String, TermSession>>);

static NEXT_ID: AtomicU64 = AtomicU64::new(1);

/// Pick the shell: an explicit override, else PowerShell on Windows / `$SHELL` on Unix.
fn resolve_shell(custom: Option<String>) -> String {
    if let Some(shell) = custom {
        if !shell.trim().is_empty() {
            return shell;
        }
    }
    #[cfg(windows)]
    {
        "powershell.exe".to_string()
    }
    #[cfg(not(windows))]
    {
        std::env::var("SHELL").unwrap_or_else(|_| "/bin/bash".to_string())
    }
}

#[tauri::command]
pub fn terminal_spawn(
    app: AppHandle,
    state: State<'_, TerminalState>,
    cwd: Option<String>,
    shell: Option<String>,
    cols: Option<u16>,
    rows: Option<u16>,
) -> Result<String, String> {
    let pty_system = native_pty_system();
    let size = PtySize {
        rows: rows.unwrap_or(24),
        cols: cols.unwrap_or(80),
        pixel_width: 0,
        pixel_height: 0,
    };
    let pair = pty_system.openpty(size).map_err(|err| err.to_string())?;

    let mut cmd = CommandBuilder::new(resolve_shell(shell));
    if let Some(dir) = cwd.as_ref() {
        if !dir.trim().is_empty() && std::path::Path::new(dir).is_dir() {
            cmd.cwd(dir);
        }
    }
    // Let CLIs enable colors / cursor control.
    cmd.env("TERM", "xterm-256color");

    let child = pair
        .slave
        .spawn_command(cmd)
        .map_err(|err| err.to_string())?;
    // The slave is owned by the child now; we only need the master in the parent.
    drop(pair.slave);

    let mut reader = pair
        .master
        .try_clone_reader()
        .map_err(|err| err.to_string())?;
    let writer = pair.master.take_writer().map_err(|err| err.to_string())?;

    let id = format!("term_{}", NEXT_ID.fetch_add(1, Ordering::Relaxed));
    let out_event = format!("terminal://output/{id}");
    let exit_event = format!("terminal://exit/{id}");

    // Reader thread: stream raw bytes to the frontend until EOF / process exit.
    let app_for_thread = app.clone();
    std::thread::spawn(move || {
        let mut buf = [0u8; 4096];
        loop {
            match reader.read(&mut buf) {
                Ok(0) => break,
                Ok(n) => {
                    // Vec<u8> serializes to a JSON number array; xterm writes it as
                    // a Uint8Array, which is binary-safe across UTF-8 boundaries.
                    let _ = app_for_thread.emit(&out_event, buf[..n].to_vec());
                }
                Err(_) => break,
            }
        }
        let _ = app_for_thread.emit(&exit_event, ());
    });

    state
        .0
        .lock()
        .map_err(|err| err.to_string())?
        .insert(id.clone(), TermSession { master: pair.master, writer, child });
    Ok(id)
}

#[tauri::command]
pub fn terminal_write(
    state: State<'_, TerminalState>,
    id: String,
    data: String,
) -> Result<(), String> {
    let mut map = state.0.lock().map_err(|err| err.to_string())?;
    let session = map.get_mut(&id).ok_or("unbekanntes Terminal")?;
    session
        .writer
        .write_all(data.as_bytes())
        .map_err(|err| err.to_string())?;
    session.writer.flush().map_err(|err| err.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn terminal_resize(
    state: State<'_, TerminalState>,
    id: String,
    cols: u16,
    rows: u16,
) -> Result<(), String> {
    let map = state.0.lock().map_err(|err| err.to_string())?;
    let session = map.get(&id).ok_or("unbekanntes Terminal")?;
    session
        .master
        .resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
        .map_err(|err| err.to_string())?;
    Ok(())
}

#[tauri::command]
pub fn terminal_kill(state: State<'_, TerminalState>, id: String) -> Result<(), String> {
    if let Some(mut session) = state.0.lock().map_err(|err| err.to_string())?.remove(&id) {
        let _ = session.child.kill();
    }
    Ok(())
}

/// Kill every live terminal — called on app exit so no PTY child is orphaned.
pub fn kill_all(app: &AppHandle) {
    if let Some(state) = app.try_state::<TerminalState>() {
        if let Ok(mut map) = state.0.lock() {
            for (_, mut session) in map.drain() {
                let _ = session.child.kill();
            }
        }
    }
}
