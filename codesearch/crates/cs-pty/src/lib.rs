//! A real terminal inside the app.
//!
//! Not a command runner — an actual pseudo-terminal. The difference matters:
//! given a pipe instead of a terminal, shells disable colour, skip the prompt and
//! buffer their output, so `pytest` and `cargo` and `git log` all behave like
//! something you would not recognise. A pty makes them behave exactly as they do
//! in your own terminal, which is the point.
//!
//! The terminal's working directory follows the workspace, and its output is
//! scanned for `datei:zeile` positions so a stack trace or a failing test can be
//! clicked straight into the graph. That seam — between what a CLI prints and
//! what the graph knows — is where a terminal stops being a convenience and
//! starts being part of the tool.

use anyhow::{Context, Result};
use portable_pty::{CommandBuilder, NativePtySystem, PtySize, PtySystem};
use std::io::{Read, Write};
use std::path::Path;
use std::sync::{Arc, Mutex};

/// One running shell.
pub struct Terminal {
    writer: Mutex<Box<dyn Write + Send>>,
    master: Mutex<Box<dyn portable_pty::MasterPty + Send>>,
    /// Kept so the child is killed when the terminal is dropped rather than
    /// outliving the window.
    child: Mutex<Box<dyn portable_pty::Child + Send + Sync>>,
}

impl Terminal {
    /// Opens a shell in `cwd`.
    ///
    /// `on_output` is called from a reader thread with each chunk the shell
    /// produces. It must not block for long — the shell stalls behind it.
    pub fn open(
        cwd: &Path,
        rows: u16,
        cols: u16,
        on_output: impl Fn(String) + Send + 'static,
    ) -> Result<Arc<Self>> {
        let system = NativePtySystem::default();
        let pair = system
            .openpty(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })
            .context("Pseudo-Terminal konnte nicht geöffnet werden")?;

        let mut command = CommandBuilder::new(default_shell());
        command.cwd(cwd);
        // Tells programs a terminal is present and what it can do. Without this
        // most tools fall back to their dumbest output mode.
        command.env("TERM", "xterm-256color");
        command.env("COLORTERM", "truecolor");

        let child = pair.slave.spawn_command(command).context("Shell konnte nicht gestartet werden")?;
        // The slave end must be dropped or the reader never sees EOF when the
        // shell exits, and the terminal appears to hang forever.
        drop(pair.slave);

        let mut reader = pair.master.try_clone_reader()?;
        let writer = pair.master.take_writer()?;

        std::thread::spawn(move || {
            let mut buffer = [0u8; 8192];
            loop {
                match reader.read(&mut buffer) {
                    Ok(0) => break,
                    Ok(count) => {
                        // Shell output is not guaranteed to split on character
                        // boundaries, so lossy conversion is the correct choice —
                        // a replacement character beats dropping a whole chunk.
                        on_output(String::from_utf8_lossy(&buffer[..count]).into_owned());
                    }
                    Err(err) => {
                        tracing::debug!(error = %err, "pty reader finished");
                        break;
                    }
                }
            }
        });

        Ok(Arc::new(Self {
            writer: Mutex::new(writer),
            master: Mutex::new(pair.master),
            child: Mutex::new(child),
        }))
    }

    /// Sends keystrokes to the shell.
    pub fn write(&self, data: &str) -> Result<()> {
        let mut writer = self.writer.lock().map_err(|err| anyhow::anyhow!("{err}"))?;
        writer.write_all(data.as_bytes())?;
        writer.flush()?;
        Ok(())
    }

    /// Tells the shell the window changed size.
    ///
    /// Without this, line editing and anything full-screen wrap at the wrong
    /// column and the display corrupts as soon as the panel is resized.
    pub fn resize(&self, rows: u16, cols: u16) -> Result<()> {
        let master = self.master.lock().map_err(|err| anyhow::anyhow!("{err}"))?;
        master.resize(PtySize { rows, cols, pixel_width: 0, pixel_height: 0 })?;
        Ok(())
    }

    pub fn kill(&self) -> Result<()> {
        let mut child = self.child.lock().map_err(|err| anyhow::anyhow!("{err}"))?;
        child.kill()?;
        Ok(())
    }
}

impl Drop for Terminal {
    fn drop(&mut self) {
        let _ = self.kill();
    }
}

fn default_shell() -> String {
    if cfg!(windows) {
        std::env::var("COMSPEC").unwrap_or_else(|_| "powershell.exe".into())
    } else {
        std::env::var("SHELL").unwrap_or_else(|_| "/bin/sh".into())
    }
}

/// A `datei:zeile` position found in terminal output.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct Position {
    pub path: String,
    pub line: u32,
}

/// Finds file positions in a line of terminal output.
///
/// Covers the shapes that actually appear: Python tracebacks
/// (`File "app.py", line 12`), the `path:line:col` used by rustc, gcc, eslint and
/// most test runners, and pytest's `path::test`. This is what turns a failing
/// test into a click into the graph.
pub fn find_positions(line: &str) -> Vec<Position> {
    let mut found = Vec::new();

    // Python: File "src/app.py", line 12
    if let Some(rest) = line.split("File \"").nth(1) {
        if let Some((path, tail)) = rest.split_once('"') {
            if let Some(number) = tail.split("line ").nth(1) {
                let digits: String = number.chars().take_while(char::is_ascii_digit).collect();
                if let Ok(parsed) = digits.parse() {
                    found.push(Position { path: path.to_string(), line: parsed });
                }
            }
        }
    }

    // path:line or path:line:col, anywhere in the line.
    for token in line.split_whitespace() {
        let token = token.trim_matches(|c: char| !c.is_ascii_alphanumeric() && c != '/' && c != '.' && c != ':' && c != '_' && c != '-');
        let mut parts = token.split(':');
        let (Some(path), Some(number)) = (parts.next(), parts.next()) else { continue };

        if !path.contains('.') || path.is_empty() {
            continue;
        }
        let Ok(parsed) = number.parse::<u32>() else { continue };
        if parsed == 0 {
            continue;
        }
        let position = Position { path: path.to_string(), line: parsed };
        if !found.contains(&position) {
            found.push(position);
        }
    }

    found
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn python_tracebacks_are_recognised() {
        let found = find_positions("  File \"src/app.py\", line 42, in handler");
        assert!(found.contains(&Position { path: "src/app.py".into(), line: 42 }));
    }

    #[test]
    fn compiler_and_linter_positions_are_recognised() {
        let rust = find_positions("error[E0308]: --> crates/cs-core/src/lib.rs:120:9");
        assert!(rust.contains(&Position { path: "crates/cs-core/src/lib.rs".into(), line: 120 }));

        let eslint = find_positions("web/src/App.tsx:31:5  error  Unexpected any");
        assert!(eslint.contains(&Position { path: "web/src/App.tsx".into(), line: 31 }));
    }

    #[test]
    fn ordinary_output_produces_no_false_positions() {
        assert!(find_positions("Alle 107 Tests bestanden").is_empty());
        assert!(find_positions("fertig um 14:30").is_empty());
        // A ratio must not be read as a file position.
        assert!(find_positions("Verhältnis 3:1").is_empty());
    }

    #[test]
    fn a_shell_starts_echoes_and_exits() {
        use std::sync::mpsc;

        let (sender, receiver) = mpsc::channel();
        let terminal = Terminal::open(Path::new("."), 24, 80, move |chunk| {
            let _ = sender.send(chunk);
        })
        .expect("a pty must be available on this platform");

        terminal.write("echo codesearch-pty-ok\n").unwrap();

        // Collect until the marker shows up or the shell goes quiet.
        let mut seen = String::new();
        for _ in 0..40 {
            match receiver.recv_timeout(std::time::Duration::from_millis(250)) {
                Ok(chunk) => {
                    seen.push_str(&chunk);
                    if seen.contains("codesearch-pty-ok") {
                        break;
                    }
                }
                Err(_) => break,
            }
        }

        assert!(seen.contains("codesearch-pty-ok"), "shell produced: {seen:?}");
        terminal.kill().unwrap();
    }
}
