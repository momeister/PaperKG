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
//
// Two further full-monitor, click-through windows built here: the "AI has control" border
// (Selbst-Steuerung takeover signal) and the pointer overlay (Assistent's "zeig mir" —
// highlights a grounded screen point, never dispatches input).

use std::sync::atomic::{AtomicU64, Ordering};
use std::thread;
use std::time::Duration;

use tauri::menu::{Menu, MenuItem};
use tauri::tray::TrayIconBuilder;
use tauri::{AppHandle, Emitter, Manager, State, WebviewUrl, WebviewWindowBuilder};

type SetupResult = Result<(), Box<dyn std::error::Error>>;

pub(crate) const OVERLAY_LABEL: &str = "overlay";
pub(crate) const CONTROL_BORDER_LABEL: &str = "control-border";
pub(crate) const POINTER_LABEL: &str = "pointer";
pub(crate) const SNIP_LABEL: &str = "snip";

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

/// Build the hidden "AI has control" border window: a full-monitor, click-through,
/// always-on-top frame that only becomes visible while Selbst-Steuerung is actively
/// driving the real mouse/keyboard, so the takeover is unmistakable even though it's
/// the user's own OS cursor being moved (there's no separate "AI-only" cursor — the
/// agent must control the real one to click on real windows).
pub fn build_control_border(app: &AppHandle, init_script: &str) -> SetupResult {
    let border_init = format!(
        "{init_script}\nwindow.__CONTROL_BORDER__ = true;\nwindow.location.hash = '#/control-border';"
    );

    let window = WebviewWindowBuilder::new(app, CONTROL_BORDER_LABEL, WebviewUrl::App("index.html".into()))
        .title("PaperKG – KI-Kontrollanzeige")
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .initialization_script(&border_init)
        .build()?;

    // Physical pixels on both sides sidesteps logical/DPI-scaling mismatches — this
    // window only needs to roughly cover the monitor, not be pixel-precise.
    if let Ok(Some(monitor)) = window.primary_monitor() {
        let _ = window.set_position(*monitor.position());
        let _ = window.set_size(*monitor.size());
    }
    let _ = window.set_ignore_cursor_events(true);
    Ok(())
}

/// Show the "AI has control" border — called when a Selbst-Steuerung run starts.
#[tauri::command]
pub fn control_border_show(app: AppHandle) {
    if let Some(window) = app.get_webview_window(CONTROL_BORDER_LABEL) {
        let _ = window.show();
    }
}

/// Hide the "AI has control" border — called on stop/abort/done and as part of the
/// hard-kill fallback, so it can never stay stuck on screen if the sidecar dies.
#[tauri::command]
pub fn control_border_hide(app: AppHandle) {
    if let Some(window) = app.get_webview_window(CONTROL_BORDER_LABEL) {
        let _ = window.hide();
    }
}

/// A grounded screen point pushed into the pointer overlay so it knows where to draw the
/// highlight. `space` says which coordinate space `x`/`y` live in: `"physical"` for the
/// Desktop Companion (physical monitor pixels — the pointer page divides by its own
/// devicePixelRatio), anything else/absent for the legacy UI-TARS bridge path (logical
/// pixels, drawn as-is) — see `pointer_show`.
#[derive(Clone, serde::Serialize)]
pub struct PointerShowPayload {
    pub x: f64,
    pub y: f64,
    pub label: Option<String>,
    pub space: Option<String>,
}

/// Build the hidden pointer-overlay window: the Assistent's "zeig mir" feature — a
/// full-monitor, click-through, always-on-top frame (same construction as
/// `build_control_border`) that highlights a grounded screen point. Unlike the control
/// border, this never signals a takeover — it only draws a marker; no input is ever
/// dispatched through this window or because of it.
pub fn build_pointer_overlay(app: &AppHandle, init_script: &str) -> SetupResult {
    let pointer_init =
        format!("{init_script}\nwindow.__POINTER_OVERLAY__ = true;\nwindow.location.hash = '#/pointer';");

    let window = WebviewWindowBuilder::new(app, POINTER_LABEL, WebviewUrl::App("index.html".into()))
        .title("PaperKG – Zeiger")
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .initialization_script(&pointer_init)
        .build()?;

    // Same physical-monitor sizing as `build_control_border`: the window's OS bounds are
    // set to the monitor's physical pixels, which makes the webview's own logical/CSS pixel
    // space line up with the monitor's logical resolution — the same coordinate space
    // `parseBoxToScreenCoords` (bridge/uitars) produces the point in, so `left/top: <x>px`
    // in the pointer page lands on the right spot without a separate DPI conversion here.
    if let Ok(Some(monitor)) = window.primary_monitor() {
        let _ = window.set_position(*monitor.position());
        let _ = window.set_size(*monitor.size());
    }
    let _ = window.set_ignore_cursor_events(true);
    Ok(())
}

/// Auto-hide generation counter for the pointer overlay. The window itself stays purely
/// passive (like `control-border`, it never invokes a command on itself) — the auto-hide
/// timer lives here in Rust instead. Each `pointer_show` bumps the generation and spawns a
/// delayed hide that only acts if no newer point has superseded it in the meantime.
#[derive(Default)]
pub struct PointerState(AtomicU64);

const POINTER_AUTO_HIDE: Duration = Duration::from_secs(25);

/// Show the pointer overlay at a grounded screen point (Assistent "zeig mir"). Never
/// dispatches mouse/keyboard input — purely visual annotation. Auto-hides after
/// `POINTER_AUTO_HIDE` unless a newer point arrives first.
#[tauri::command]
pub fn pointer_show(
    app: AppHandle,
    state: State<'_, PointerState>,
    x: f64,
    y: f64,
    label: Option<String>,
    space: Option<String>,
) -> Result<(), String> {
    let window = app
        .get_webview_window(POINTER_LABEL)
        .ok_or("Zeiger-Fenster nicht verfügbar")?;
    let _ = window.show();
    app.emit_to(POINTER_LABEL, "pointer://show", PointerShowPayload { x, y, label, space })
        .map_err(|err| err.to_string())?;

    let generation = state.0.fetch_add(1, Ordering::SeqCst) + 1;
    let app_for_timer = app.clone();
    thread::spawn(move || {
        thread::sleep(POINTER_AUTO_HIDE);
        if let Some(state) = app_for_timer.try_state::<PointerState>() {
            if state.0.load(Ordering::SeqCst) == generation {
                if let Some(window) = app_for_timer.get_webview_window(POINTER_LABEL) {
                    let _ = window.hide();
                }
                // The pointer page stops its cursor-dodge poll on this event.
                let _ = app_for_timer.emit_to(POINTER_LABEL, "pointer://hide", ());
            }
        }
    });
    Ok(())
}

/// Hide the pointer overlay immediately — called when Assistent stops.
#[tauri::command]
pub fn pointer_hide(app: AppHandle) {
    if let Some(window) = app.get_webview_window(POINTER_LABEL) {
        let _ = window.hide();
    }
    // The pointer page stops its cursor-dodge poll on this event.
    let _ = app.emit_to(POINTER_LABEL, "pointer://hide", ());
}

/// Build the hidden snip window for the companion's "Bereich erklären": a full-monitor,
/// always-on-top window that shows the frozen screenshot and lets the user drag a
/// region. Unlike the other overlay windows it is **interactive** (no click-through) —
/// but it only draws a marquee over a frozen image; no input ever reaches other apps.
pub fn build_snip_overlay(app: &AppHandle, init_script: &str) -> SetupResult {
    let snip_init =
        format!("{init_script}\nwindow.__SNIP_OVERLAY__ = true;\nwindow.location.hash = '#/snip';");

    let window = WebviewWindowBuilder::new(app, SNIP_LABEL, WebviewUrl::App("index.html".into()))
        .title("PaperKG – Bereich erklären")
        .transparent(true)
        .decorations(false)
        .shadow(false)
        .always_on_top(true)
        .skip_taskbar(true)
        .resizable(false)
        .focused(false)
        .visible(false)
        .initialization_script(&snip_init)
        .build()?;

    // Physical-monitor sizing like the pointer overlay: the frozen screenshot is in
    // physical pixels, so CSS pixels × devicePixelRatio map 1:1 onto image pixels.
    if let Ok(Some(monitor)) = window.primary_monitor() {
        let _ = window.set_position(*monitor.position());
        let _ = window.set_size(*monitor.size());
    }
    Ok(())
}

/// System-tray icon with a menu to toggle the overlay, start a region-explain
/// selection, or quit the app.
pub fn setup_tray(app: &AppHandle) -> SetupResult {
    let toggle_item = MenuItem::with_id(app, "overlay-toggle", "AI-Cursor ein/aus", true, None::<&str>)?;
    let snip_item = MenuItem::with_id(app, "companion-snip", "Bereich erklären", true, None::<&str>)?;
    let quit_item = MenuItem::with_id(app, "quit", "Beenden", true, None::<&str>)?;
    let menu = Menu::with_items(app, &[&toggle_item, &snip_item, &quit_item])?;

    let mut builder = TrayIconBuilder::new()
        .tooltip("ScienceKG / PaperKG")
        .menu(&menu)
        .on_menu_event(|app, event| match event.id.as_ref() {
            "overlay-toggle" => toggle_overlay(app),
            "companion-snip" => {
                // snip_start_impl blocks for the capture — keep it off this thread.
                let app = app.clone();
                thread::spawn(move || {
                    if let Err(err) = crate::capture::snip_start_impl(&app) {
                        log::warn!("Bereich erklären failed: {err}");
                    }
                });
            }
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
