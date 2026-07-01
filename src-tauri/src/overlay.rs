// AI-Cursor desktop overlay (roadmap R1).
//
// A second, transparent, always-on-top window that floats over the desktop —
// even outside the main app window — so the user can fire the existing UI-TARS
// hand-off (`POST /agent/dispatch`) from anywhere. PaperKG stays "the brain":
// the overlay only reuses the frontend's `streamAgentDispatch` / `getAgentConfig`
// against the same backend sidecar; no new VLM plumbing is added here.
//
// Toggled by a global hotkey (CmdOrCtrl+Shift+Space) and a system-tray menu.
// The overlay window loads the same bundled frontend; its init script flips it
// into the compact overlay route (`window.__OVERLAY__` + `#/overlay`).

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, WebviewUrl, WebviewWindowBuilder};

type SetupResult = Result<(), Box<dyn std::error::Error>>;

const OVERLAY_LABEL: &str = "overlay";

/// A compiled task brief pushed into the overlay so it shows up pre-loaded — the
/// overlay only acts on it once the user clicks "Starten" inside the window.
#[derive(Clone, serde::Serialize)]
pub struct OverlayTaskPayload {
    pub task: String,
    pub goal: String,
    pub mode: String,
    pub variant_id: Option<String>,
}

/// Build the hidden overlay window. Shares the main window's init script (so it
/// knows `window.__API_BASE__`) and appends the two lines that switch the React
/// app into overlay mode.
pub fn build_overlay(app: &AppHandle, init_script: &str) -> SetupResult {
    let overlay_init =
        format!("{init_script}\nwindow.__OVERLAY__ = true;\nwindow.location.hash = '#/overlay';");

    WebviewWindowBuilder::new(app, OVERLAY_LABEL, WebviewUrl::App("index.html".into()))
        .title("PaperKG AI-Cursor")
        .inner_size(420.0, 540.0)
        .transparent(true)
        .decorations(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .visible(false)
        .initialization_script(&overlay_init)
        .build()?;
    Ok(())
}

/// Show the overlay if hidden, hide it if visible. Called from the global hotkey
/// and the tray menu.
pub fn toggle_overlay(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(OVERLAY_LABEL) {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
        } else {
            let _ = window.show();
            let _ = window.set_focus();
        }
    }
}

/// Hide the overlay — invoked by its own close button.
#[tauri::command]
pub fn overlay_hide(app: AppHandle) {
    if let Some(window) = app.get_webview_window(OVERLAY_LABEL) {
        let _ = window.hide();
    }
}

/// Toggle the overlay from the frontend (kept for an in-app trigger).
#[tauri::command]
pub fn overlay_toggle(app: AppHandle) {
    toggle_overlay(&app);
}

/// Show + focus the overlay and push a pre-compiled task brief into it — the missing
/// link between "An AI-Cursor übergeben" and the overlay actually doing something.
/// Nothing executes until the user clicks "Starten" inside the overlay itself.
#[tauri::command]
pub fn overlay_dispatch_task(
    app: AppHandle,
    task: String,
    goal: String,
    mode: String,
    variant_id: Option<String>,
) -> Result<(), String> {
    let window = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or("Overlay-Fenster nicht verfügbar")?;
    let _ = window.show();
    let _ = window.set_focus();
    app.emit_to(
        OVERLAY_LABEL,
        "overlay://task",
        OverlayTaskPayload { task, goal, mode, variant_id },
    )
    .map_err(|err| err.to_string())
}

/// System-tray icon with a menu to toggle the overlay or quit the app.
pub fn setup_tray(app: &AppHandle) -> SetupResult {
    let toggle_item = MenuItem::with_id(app, "overlay-toggle", "AI-Cursor ein/aus", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Beenden", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&toggle_item, &quit_item])?;

    let mut builder = TrayIconBuilder::new()
        .tooltip("ScienceKG / PaperKG")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "overlay-toggle" => toggle_overlay(app),
            "quit" => app.exit(0),
            _ => {}
        });
    if let Some(icon) = app.default_window_icon() {
        builder = builder.icon(icon.clone());
    }
    builder.build(app)?;
    Ok(())
}

/// Register the global hotkey (CmdOrCtrl+Shift+Space) that toggles the overlay.
/// Desktop only — the global-shortcut plugin is not available on mobile.
#[cfg(desktop)]
pub fn register_global_shortcut(app: &AppHandle) -> SetupResult {
    use tauri_plugin_global_shortcut::{Code, GlobalShortcutExt, Modifiers, Shortcut};

    #[cfg(target_os = "macos")]
    let modifiers = Modifiers::SUPER | Modifiers::SHIFT;
    #[cfg(not(target_os = "macos"))]
    let modifiers = Modifiers::CONTROL | Modifiers::SHIFT;

    let shortcut = Shortcut::new(Some(modifiers), Code::Space);
    app.global_shortcut().register(shortcut)?;
    Ok(())
}
