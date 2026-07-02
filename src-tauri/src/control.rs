// Native Selbst-Steuerung (roadmap R7) — the "hands": real mouse/keyboard synthesis
// via enigo. Skeleton scope: dumb, individually-invoked actions only; the *brain*
// (VLM planning loop) lives in the Python backend (`query/self_drive.py`,
// `POST /selfdrive/*`) and the sequencing lives in the overlay frontend, which asks
// the user to confirm every single action ("Bestätigungsmodus") in this stage.
//
// Safety model, in order:
//   1. `companion.self_drive.enabled` in config.yaml gates the backend planner.
//   2. Nothing here moves unless the session is ARMED (`self_drive_arm`), which also
//      shows the full-screen control border; disarm hides it again. Arm state dies
//      with the app — there is no persistence.
//   3. Emergency stop: Ctrl+Shift+Q (global, works even while an action runs)
//      disarms immediately, hides the border and tells the overlay to abort its
//      loop (`selfdrive://emergency-stop`).
//
// Coordinates are physical virtual-desktop pixels (monitor origin + monitor-relative
// point — the same space `cursor_position` reports). enigo maps absolute moves onto
// the Windows virtual desktop itself.

use std::sync::atomic::{AtomicBool, Ordering};

use enigo::{Axis, Button, Coordinate, Direction, Enigo, Key, Keyboard, Mouse, Settings};
use tauri::{AppHandle, Emitter, Manager, State};

use crate::overlay::OVERLAY_LABEL;

#[derive(Default)]
pub struct ControlState {
    armed: AtomicBool,
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

/// Arm input synthesis for one confirmed session and show the takeover border.
#[tauri::command]
pub fn self_drive_arm(app: AppHandle, state: State<'_, ControlState>) {
    state.armed.store(true, Ordering::SeqCst);
    crate::overlay::control_border_show(app);
}

/// Disarm input synthesis and hide the takeover border. Always safe to call.
#[tauri::command]
pub fn self_drive_disarm(app: AppHandle, state: State<'_, ControlState>) {
    state.armed.store(false, Ordering::SeqCst);
    crate::overlay::control_border_hide(app);
}

/// Move the real cursor to an absolute physical desktop position.
#[tauri::command]
pub fn control_move(state: State<'_, ControlState>, x: f64, y: f64) -> Result<(), String> {
    ensure_armed(&state)?;
    let mut enigo = new_enigo()?;
    enigo
        .move_mouse(x as i32, y as i32, Coordinate::Abs)
        .map_err(|err| err.to_string())
}

/// Move to (x, y) and click (`button`: "left"/"right"/"middle"; `double` for a
/// double click). The move-first keeps the click visible to the user.
#[tauri::command]
pub fn control_click(
    state: State<'_, ControlState>,
    x: f64,
    y: f64,
    button: Option<String>,
    double: Option<bool>,
) -> Result<(), String> {
    ensure_armed(&state)?;
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
}

/// Type literal text at the current focus.
#[tauri::command]
pub fn control_type(state: State<'_, ControlState>, text: String) -> Result<(), String> {
    ensure_armed(&state)?;
    let mut enigo = new_enigo()?;
    enigo.text(&text).map_err(|err| err.to_string())
}

/// Press a key combo like "ctrl+s", "alt+tab", "enter", "ctrl+shift+t".
/// Modifiers are held around the final key; unknown single characters are sent
/// as unicode keys.
#[tauri::command]
pub fn control_key(state: State<'_, ControlState>, combo: String) -> Result<(), String> {
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
}

/// Scroll vertically (`dy`, positive = down) and/or horizontally (`dx`) in lines.
#[tauri::command]
pub fn control_scroll(state: State<'_, ControlState>, dx: f64, dy: f64) -> Result<(), String> {
    ensure_armed(&state)?;
    let mut enigo = new_enigo()?;
    if dy as i32 != 0 {
        enigo.scroll(dy as i32, Axis::Vertical).map_err(|err| err.to_string())?;
    }
    if dx as i32 != 0 {
        enigo.scroll(dx as i32, Axis::Horizontal).map_err(|err| err.to_string())?;
    }
    Ok(())
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

/// Emergency stop (global Ctrl+Shift+Q): disarm, hide the border, tell the overlay
/// to abort its loop. Must work even when the frontend is busy or wedged.
pub fn emergency_stop(app: &AppHandle) {
    if let Some(state) = app.try_state::<ControlState>() {
        state.armed.store(false, Ordering::SeqCst);
    }
    crate::overlay::control_border_hide(app.clone());
    let _ = app.emit_to(OVERLAY_LABEL, "selfdrive://emergency-stop", ());
    log::warn!("Selbst-Steuerung: Not-Aus ausgelöst (Ctrl+Shift+Q)");
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
