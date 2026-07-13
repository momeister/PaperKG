// Native Selbst-Steuerung (roadmap R7) — the "hands": real mouse/keyboard synthesis
// via enigo. Dumb, individually-invoked actions only; the *brain* (VLM planning
// loop) lives in the Python backend (`query/self_drive.py`, `POST /selfdrive/*`)
// and the sequencing lives in the overlay frontend.
//
// Safety model, in order:
//   1. `companion.self_drive.enabled` in config.yaml gates the backend planner.
//   2. Nothing here moves unless the session is ARMED (`self_drive_arm`), which also
//      shows the full-screen control border; disarm hides it again. Arm state dies
//      with the app — there is no persistence.
//   3. Emergency stop: Ctrl+Shift+Q (global, works even while an action runs)
//      disarms immediately, hides the border and tells the overlay to abort its
//      loop (`selfdrive://emergency-stop`, payload carries the reason).
//   4. Mouse-jerk stop: the user grabbing the mouse aborts the run. Race-free
//      pre-action check (cursor far from the last synthetic anchor → abort instead
//      of executing) plus a 30 ms watcher while an action is in flight. Cursor
//      positions on the overlay chat card are exempt — pressing "Ausführen" there
//      is interaction, not a takeover.
//   5. Action timeout: every enigo action runs on a worker thread with a hard
//      deadline; expiry triggers the emergency stop. The orphaned worker cannot be
//      killed (enigo has no cancellation) — it finishes or hangs harmlessly while
//      the session is already disarmed.
//
// Coordinates are physical virtual-desktop pixels (monitor origin + monitor-relative
// point — the same space `cursor_position` reports). enigo maps absolute moves onto
// the Windows virtual desktop itself.

use std::sync::atomic::{AtomicBool, AtomicU64, Ordering};
use std::sync::{mpsc, Mutex};
use std::thread;
use std::time::Duration;

use enigo::{Axis, Button, Coordinate, Direction, Enigo, Key, Keyboard, Mouse, Settings};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::overlay::OVERLAY_LABEL;

pub const DEFAULT_MOUSE_ABORT_PX: f64 = 150.0;
pub const DEFAULT_ACTION_TIMEOUT_MS: u64 = 5000;
const WATCH_INTERVAL_MS: u64 = 30;

pub struct ControlState {
    armed: AtomicBool,
    /// True only while an enigo action executes — the window the jerk watcher observes.
    in_flight: AtomicBool,
    /// Re-arm invalidates the previous session's watcher thread.
    generation: AtomicU64,
    /// Current + previous intended cursor anchor (physical px). Two anchors cover the
    /// sampling race between bookkeeping and the SendInput actually landing.
    anchors: Mutex<(Option<(f64, f64)>, Option<(f64, f64)>)>,
    threshold_px: Mutex<f64>,
}

impl Default for ControlState {
    fn default() -> Self {
        Self {
            armed: AtomicBool::new(false),
            in_flight: AtomicBool::new(false),
            generation: AtomicU64::new(0),
            anchors: Mutex::new((None, None)),
            threshold_px: Mutex::new(DEFAULT_MOUSE_ABORT_PX),
        }
    }
}

fn new_enigo() -> Result<Enigo, String> {
    Enigo::new(&Settings::default()).map_err(|err| format!("Eingabe-Synthese nicht verfügbar: {err}"))
}

fn ensure_armed(state: &ControlState) -> Result<(), String> {
    if state.armed.load(Ordering::SeqCst) {
        Ok(())
    } else {
        Err("Selbst-Steuerung ist nicht aktiviert (arm zuerst).".to_string())
    }
}

/// True iff the cursor is farther than `threshold` from every known anchor — the
/// signature of the *user* moving the mouse rather than enigo. No anchors → no
/// reference → never a deviation.
fn user_deviation(
    cursor: (f64, f64),
    current: Option<(f64, f64)>,
    previous: Option<(f64, f64)>,
    threshold: f64,
) -> bool {
    let known: Vec<(f64, f64)> = [current, previous].into_iter().flatten().collect();
    if known.is_empty() {
        return false;
    }
    known.iter().all(|anchor| {
        let (dx, dy) = (cursor.0 - anchor.0, cursor.1 - anchor.1);
        (dx * dx + dy * dy).sqrt() > threshold
    })
}

fn read_anchors(state: &ControlState) -> (Option<(f64, f64)>, Option<(f64, f64)>) {
    state.anchors.lock().map(|guard| *guard).unwrap_or((None, None))
}

fn read_threshold(state: &ControlState) -> f64 {
    state.threshold_px.lock().map(|guard| *guard).unwrap_or(DEFAULT_MOUSE_ABORT_PX)
}

/// Record where the next synthetic move will put the cursor — called *before* the
/// enigo call so the watcher always has the fresher of the two positions.
fn note_synthetic_target(state: &ControlState, x: f64, y: f64) {
    if let Ok(mut anchors) = state.anchors.lock() {
        anchors.1 = anchors.0;
        anchors.0 = Some((x, y));
    }
}

/// Pre-action takeover check: if the user has moved the cursor away from where the
/// last action left it (and it is not parked on the overlay chat card), abort the
/// whole run instead of fighting them for the mouse.
fn guard_user_takeover(app: &AppHandle, state: &ControlState) -> Result<(), String> {
    let Ok(cursor) = app.cursor_position() else {
        return Ok(()); // no cursor info (e.g. secure desktop) — cannot judge, don't block
    };
    let (current, previous) = read_anchors(state);
    if user_deviation((cursor.x, cursor.y), current, previous, read_threshold(state))
        && !crate::overlay::point_on_window(app, OVERLAY_LABEL, cursor.x, cursor.y)
    {
        emergency_stop_with_reason(app, "mouse");
        return Err("Not-Aus: Nutzer hat die Maus bewegt — Selbst-Steuerung gestoppt.".to_string());
    }
    Ok(())
}

/// Run `f` on a worker thread with a hard deadline. `None` = timeout; the worker is
/// orphaned in that case (see the module comment on why that is acceptable).
fn with_timeout(
    timeout_ms: u64,
    f: impl FnOnce() -> Result<(), String> + Send + 'static,
) -> Option<Result<(), String>> {
    let (tx, rx) = mpsc::channel();
    thread::spawn(move || {
        let _ = tx.send(f());
    });
    rx.recv_timeout(Duration::from_millis(timeout_ms)).ok()
}

/// Shared execution path of every control_* command: takeover pre-check, anchor
/// bookkeeping, in-flight marking for the jerk watcher, hard deadline.
fn run_action(
    app: &AppHandle,
    state: &ControlState,
    timeout_ms: Option<u64>,
    new_anchor: Option<(f64, f64)>,
    f: impl FnOnce() -> Result<(), String> + Send + 'static,
) -> Result<(), String> {
    guard_user_takeover(app, state)?;
    if let Some((x, y)) = new_anchor {
        note_synthetic_target(state, x, y);
    }
    let timeout = timeout_ms.unwrap_or(DEFAULT_ACTION_TIMEOUT_MS).max(1);
    state.in_flight.store(true, Ordering::SeqCst);
    let result = with_timeout(timeout, f);
    state.in_flight.store(false, Ordering::SeqCst);
    match result {
        Some(inner) => inner,
        None => {
            emergency_stop_with_reason(app, "timeout");
            Err(format!("Aktions-Timeout nach {timeout} ms — Selbst-Steuerung gestoppt."))
        }
    }
}

/// Mouse-jerk watcher (one per armed session): 30 ms poll, only judging while an
/// action is in flight — between actions the pre-check in `run_action` is the
/// race-free authority. Ends itself on disarm or when a newer session re-arms.
fn watch_mouse(app: AppHandle, generation: u64) {
    loop {
        thread::sleep(Duration::from_millis(WATCH_INTERVAL_MS));
        let Some(state) = app.try_state::<ControlState>() else { return };
        if !state.armed.load(Ordering::SeqCst)
            || state.generation.load(Ordering::SeqCst) != generation
        {
            return;
        }
        if !state.in_flight.load(Ordering::SeqCst) {
            continue;
        }
        let Ok(cursor) = app.cursor_position() else { continue };
        let (current, previous) = read_anchors(&state);
        if user_deviation((cursor.x, cursor.y), current, previous, read_threshold(&state))
            && !crate::overlay::point_on_window(&app, OVERLAY_LABEL, cursor.x, cursor.y)
        {
            emergency_stop_with_reason(&app, "mouse");
            return;
        }
    }
}

/// Arm input synthesis for one confirmed session, seed the takeover anchors from the
/// current cursor position, start the jerk watcher and show the takeover border.
/// `threshold_px` comes from `companion.self_drive.mouse_abort_px` via the frontend.
#[tauri::command]
pub fn self_drive_arm(app: AppHandle, state: State<'_, ControlState>, threshold_px: Option<f64>) {
    let threshold = threshold_px.unwrap_or(DEFAULT_MOUSE_ABORT_PX).max(1.0);
    if let Ok(mut guard) = state.threshold_px.lock() {
        *guard = threshold;
    }
    if let Ok(mut anchors) = state.anchors.lock() {
        *anchors = (app.cursor_position().ok().map(|p| (p.x, p.y)), None);
    }
    state.in_flight.store(false, Ordering::SeqCst);
    state.armed.store(true, Ordering::SeqCst);
    let generation = state.generation.fetch_add(1, Ordering::SeqCst) + 1;
    let watcher_app = app.clone();
    thread::spawn(move || watch_mouse(watcher_app, generation));
    crate::overlay::control_border_show(app);
}

/// Disarm input synthesis and hide the takeover border. Always safe to call.
#[tauri::command]
pub fn self_drive_disarm(app: AppHandle, state: State<'_, ControlState>) {
    state.armed.store(false, Ordering::SeqCst);
    state.in_flight.store(false, Ordering::SeqCst);
    crate::overlay::control_border_hide(app);
}

/// Move the real cursor to an absolute physical desktop position.
#[tauri::command]
pub fn control_move(
    app: AppHandle,
    state: State<'_, ControlState>,
    x: f64,
    y: f64,
    timeout_ms: Option<u64>,
) -> Result<(), String> {
    ensure_armed(&state)?;
    run_action(&app, &state, timeout_ms, Some((x, y)), move || {
        let mut enigo = new_enigo()?;
        enigo
            .move_mouse(x as i32, y as i32, Coordinate::Abs)
            .map_err(|err| err.to_string())
    })
}

/// Move to (x, y) and click (`button`: "left"/"right"/"middle"; `double` for a
/// double click). The move-first keeps the click visible to the user.
#[tauri::command]
pub fn control_click(
    app: AppHandle,
    state: State<'_, ControlState>,
    x: f64,
    y: f64,
    button: Option<String>,
    double: Option<bool>,
    timeout_ms: Option<u64>,
) -> Result<(), String> {
    ensure_armed(&state)?;
    run_action(&app, &state, timeout_ms, Some((x, y)), move || {
        let mut enigo = new_enigo()?;
        enigo
            .move_mouse(x as i32, y as i32, Coordinate::Abs)
            .map_err(|err| err.to_string())?;
        let button = match button.as_deref() {
            Some("right") => Button::Right,
            Some("middle") => Button::Middle,
            _ => Button::Left,
        };
        let clicks = if double.unwrap_or(false) { 2 } else { 1 };
        for _ in 0..clicks {
            enigo.button(button, Direction::Click).map_err(|err| err.to_string())?;
        }
        Ok(())
    })
}

/// Type literal text at the current focus.
#[tauri::command]
pub fn control_type(
    app: AppHandle,
    state: State<'_, ControlState>,
    text: String,
    timeout_ms: Option<u64>,
) -> Result<(), String> {
    ensure_armed(&state)?;
    run_action(&app, &state, timeout_ms, None, move || {
        let mut enigo = new_enigo()?;
        enigo.text(&text).map_err(|err| err.to_string())
    })
}

/// Press a key combo like "ctrl+s", "alt+tab", "enter", "ctrl+shift+t".
/// Modifiers are held around the final key; unknown single characters are sent
/// as unicode keys.
#[tauri::command]
pub fn control_key(
    app: AppHandle,
    state: State<'_, ControlState>,
    combo: String,
    timeout_ms: Option<u64>,
) -> Result<(), String> {
    ensure_armed(&state)?;
    let parts: Vec<String> = combo
        .split('+')
        .map(|part| part.trim().to_lowercase())
        .filter(|part| !part.is_empty())
        .collect();
    if parts.is_empty() {
        return Err("Leere Tastenkombination".to_string());
    }
    let (modifiers, key_name) = parts.split_at(parts.len() - 1);
    let key = parse_key(&key_name[0])?;
    let modifier_keys = modifiers.iter().map(|name| parse_key(name)).collect::<Result<Vec<_>, _>>()?;

    run_action(&app, &state, timeout_ms, None, move || {
        let mut enigo = new_enigo()?;
        for modifier in &modifier_keys {
            enigo.key(*modifier, Direction::Press).map_err(|err| err.to_string())?;
        }
        let result = enigo.key(key, Direction::Click).map_err(|err| err.to_string());
        // Always release modifiers, even when the main key failed — a stuck Ctrl would
        // be worse than the failed action.
        for modifier in modifier_keys.iter().rev() {
            let _ = enigo.key(*modifier, Direction::Release);
        }
        result
    })
}

/// Scroll vertically (`dy`, positive = down) and/or horizontally (`dx`) in lines.
#[tauri::command]
pub fn control_scroll(
    app: AppHandle,
    state: State<'_, ControlState>,
    dx: f64,
    dy: f64,
    timeout_ms: Option<u64>,
) -> Result<(), String> {
    ensure_armed(&state)?;
    run_action(&app, &state, timeout_ms, None, move || {
        let mut enigo = new_enigo()?;
        if dy as i32 != 0 {
            enigo.scroll(dy as i32, Axis::Vertical).map_err(|err| err.to_string())?;
        }
        if dx as i32 != 0 {
            enigo.scroll(dx as i32, Axis::Horizontal).map_err(|err| err.to_string())?;
        }
        Ok(())
    })
}

fn parse_key(name: &str) -> Result<Key, String> {
    Ok(match name {
        "ctrl" | "control" | "strg" => Key::Control,
        "shift" | "umschalt" => Key::Shift,
        "alt" => Key::Alt,
        "meta" | "win" | "cmd" | "super" => Key::Meta,
        "enter" | "return" => Key::Return,
        "esc" | "escape" => Key::Escape,
        "tab" => Key::Tab,
        "space" | "leertaste" => Key::Space,
        "backspace" => Key::Backspace,
        "delete" | "del" | "entf" => Key::Delete,
        "home" | "pos1" => Key::Home,
        "end" | "ende" => Key::End,
        "pageup" => Key::PageUp,
        "pagedown" => Key::PageDown,
        "up" | "hoch" => Key::UpArrow,
        "down" | "runter" => Key::DownArrow,
        "left" | "links" => Key::LeftArrow,
        "right" | "rechts" => Key::RightArrow,
        "f1" => Key::F1,
        "f2" => Key::F2,
        "f3" => Key::F3,
        "f4" => Key::F4,
        "f5" => Key::F5,
        "f6" => Key::F6,
        "f7" => Key::F7,
        "f8" => Key::F8,
        "f9" => Key::F9,
        "f10" => Key::F10,
        "f11" => Key::F11,
        "f12" => Key::F12,
        other => {
            let mut chars = other.chars();
            match (chars.next(), chars.next()) {
                (Some(ch), None) => Key::Unicode(ch),
                _ => return Err(format!("Unbekannte Taste: {other}")),
            }
        }
    })
}

/// Why the run was killed: `"hotkey"` (Ctrl+Shift+Q), `"mouse"` (jerk stop) or
/// `"timeout"` (action deadline). The overlay shows a matching message.
#[derive(Clone, serde::Serialize)]
struct EmergencyStopPayload {
    reason: String,
}

/// Emergency stop (global Ctrl+Shift+Q): disarm, hide the border, tell the overlay
/// to abort its loop. Must work even when the frontend is busy or wedged.
pub fn emergency_stop(app: &AppHandle) {
    emergency_stop_with_reason(app, "hotkey");
}

/// Emergency stop with an explicit trigger — also used by the mouse-jerk watcher
/// and the action-timeout path. Never queued: a stale stop delivered to a window
/// that was created later would abort the wrong run.
pub fn emergency_stop_with_reason(app: &AppHandle, reason: &str) {
    if let Some(state) = app.try_state::<ControlState>() {
        state.armed.store(false, Ordering::SeqCst);
        state.in_flight.store(false, Ordering::SeqCst);
    }
    // Also end the guided-mode click watcher — the emergency stop kills everything.
    if let Some(state) = app.try_state::<crate::click_watch::ClickWatchState>() {
        state.stop();
    }
    crate::overlay::control_border_hide(app.clone());
    let _ = app.emit_to(
        OVERLAY_LABEL,
        "selfdrive://emergency-stop",
        EmergencyStopPayload { reason: reason.to_string() },
    );
    log::warn!("Selbst-Steuerung: Not-Aus ausgelöst ({reason})");
}

/// The global emergency-stop shortcut (desktop only).
#[cfg(desktop)]
pub fn kill_shortcut() -> tauri_plugin_global_shortcut::Shortcut {
    use tauri_plugin_global_shortcut::{Code, Modifiers, Shortcut};
    Shortcut::new(Some(Modifiers::CONTROL | Modifiers::SHIFT), Code::KeyQ)
}

#[cfg(desktop)]
pub fn register_kill_shortcut(app: &AppHandle) -> Result<(), Box<dyn std::error::Error>> {
    use tauri_plugin_global_shortcut::GlobalShortcutExt;
    app.global_shortcut().register(kill_shortcut())?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{user_deviation, with_timeout};

    #[test]
    fn deviation_false_without_anchors() {
        assert!(!user_deviation((500.0, 500.0), None, None, 150.0));
    }

    #[test]
    fn deviation_false_near_current_anchor() {
        assert!(!user_deviation((510.0, 505.0), Some((500.0, 500.0)), None, 150.0));
    }

    #[test]
    fn deviation_false_near_previous_anchor() {
        // Sampling race: cursor still at the previous anchor while the new target is
        // already booked — must not count as a user movement.
        assert!(!user_deviation(
            (100.0, 100.0),
            Some((900.0, 900.0)),
            Some((105.0, 95.0)),
            150.0
        ));
    }

    #[test]
    fn deviation_true_far_from_all_anchors() {
        assert!(user_deviation(
            (100.0, 100.0),
            Some((500.0, 500.0)),
            Some((600.0, 600.0)),
            150.0
        ));
    }

    #[test]
    fn deviation_false_at_exact_threshold() {
        // > threshold, not >= — exactly 150 px is still tolerated.
        assert!(!user_deviation((650.0, 500.0), Some((500.0, 500.0)), None, 150.0));
    }

    #[test]
    fn with_timeout_returns_result_in_time() {
        assert_eq!(with_timeout(1000, || Ok(())), Some(Ok(())));
        assert_eq!(
            with_timeout(1000, || Err("kaputt".to_string())),
            Some(Err("kaputt".to_string()))
        );
    }

    #[test]
    fn with_timeout_none_on_deadline() {
        let result = with_timeout(30, || {
            std::thread::sleep(std::time::Duration::from_millis(500));
            Ok(())
        });
        assert_eq!(result, None);
    }
}
