// Global click watcher (R7 guided sequences): detects the user's *real* mouse
// clicks so the Companion can auto-advance to the next pointer step — the user
// clicks where the ring points, we notice, capture a fresh frame, plan the next
// step. No hook is installed: a 30 ms polling thread reads the left button via
// `GetAsyncKeyState` (same polling house style as the pointer page's 50 ms
// `cursor_position` loop), which avoids the well-known Windows low-level-hook
// pitfalls (hook timeouts under load, unstoppable listen loops).
//
// On release-edge (button up after down) the watcher emits `companion://click`
// to the overlay with the cursor position in **physical virtual-desktop pixels**
// (the same space capture.rs and control.rs use) plus an `on_overlay` flag so
// clicks on the chat card itself can be ignored. Drag releases count as clicks
// and double clicks emit twice — the frontend busy-gates while a step round trip
// is in flight, and the expectation verify catches the rest.
//
// Windows-only for now; other platforms get a clear German error. The emergency
// stop (Ctrl+Shift+Q, control.rs) also stops the watcher.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Arc;

use tauri::{AppHandle, State};

#[cfg(windows)]
use tauri::Emitter;

#[cfg(windows)]
use crate::overlay::OVERLAY_LABEL;

#[cfg(windows)]
const POLL_INTERVAL_MS: u64 = 30;

#[derive(Default)]
pub struct ClickWatchState {
    running: Arc<AtomicBool>,
}

impl ClickWatchState {
    /// Ask the polling thread to end (it exits within one poll interval).
    pub fn stop(&self) {
        self.running.store(false, Ordering::SeqCst);
    }
}

#[derive(Clone, serde::Serialize)]
struct ClickEvent {
    x: f64,
    y: f64,
    on_overlay: bool,
}

/// Button transition that counts as one completed click. The release edge (not
/// the press) is used so the click has already reached the target application
/// when the frontend reacts.
fn release_edge(prev_down: bool, now_down: bool) -> bool {
    prev_down && !now_down
}

/// Current left-button state via the async key map. Only the 0x8000 "currently
/// down" bit is reliable (the low "was pressed" bit is shared global state);
/// transitions are tracked by the caller. VK_LBUTTON follows a swapped-buttons
/// setting, so this is always the user's primary click.
#[cfg(windows)]
fn left_button_down() -> bool {
    use windows::Win32::UI::Input::KeyboardAndMouse::{GetAsyncKeyState, VK_LBUTTON};
    (unsafe { GetAsyncKeyState(VK_LBUTTON.0 as i32) } as u16 & 0x8000) != 0
}

/// Whether a physical desktop point lies on the visible companion chat card —
/// those clicks are UI interaction, not a guided step.
#[cfg(windows)]
fn point_on_overlay(app: &AppHandle, x: f64, y: f64) -> bool {
    crate::overlay::point_on_window(app, OVERLAY_LABEL, x, y)
}

/// Start watching for global left clicks (idempotent — a second call while
/// running is a no-op). Emits `companion://click` to the overlay window.
#[tauri::command]
pub fn click_watch_start(app: AppHandle, state: State<'_, ClickWatchState>) -> Result<(), String> {
    #[cfg(windows)]
    {
        if state.running.swap(true, Ordering::SeqCst) {
            return Ok(()); // already running
        }
        let running = state.running.clone();
        std::thread::spawn(move || {
            let mut prev_down = left_button_down();
            while running.load(Ordering::SeqCst) {
                std::thread::sleep(std::time::Duration::from_millis(POLL_INTERVAL_MS));
                let now_down = left_button_down();
                if release_edge(prev_down, now_down) {
                    if let Ok(position) = app.cursor_position() {
                        let payload = ClickEvent {
                            x: position.x,
                            y: position.y,
                            on_overlay: point_on_overlay(&app, position.x, position.y),
                        };
                        let _ = app.emit_to(OVERLAY_LABEL, "companion://click", payload);
                    }
                }
                prev_down = now_down;
            }
        });
        Ok(())
    }
    #[cfg(not(windows))]
    {
        let _ = (app, state);
        Err("Klick-Erkennung ist nur unter Windows verfügbar".to_string())
    }
}

/// Stop watching (idempotent).
#[tauri::command]
pub fn click_watch_stop(state: State<'_, ClickWatchState>) {
    state.stop();
}

#[cfg(test)]
mod tests {
    use super::release_edge;

    #[test]
    fn release_edge_only_on_down_to_up() {
        assert!(release_edge(true, false));
        assert!(!release_edge(false, true)); // press
        assert!(!release_edge(true, true)); // held (drag)
        assert!(!release_edge(false, false)); // idle
    }
}
