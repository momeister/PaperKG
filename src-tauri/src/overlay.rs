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
//
// All four overlay-family windows are created **lazily** via `ensure_window` (first
// hotkey/tray/feature use), not at startup — each one is a full WebView2 process
// parsing the whole frontend bundle, and five webviews at launch made fans audible
// while Task Manager looked idle. Events destined for a window that is still loading
// are queued (`emit_or_queue`) and drained once its page signals readiness
// (`overlay_window_ready`).

use std::collections::HashMap;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Mutex;
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

/// Init script shared by all lazily created overlay windows (`window.__API_BASE__`
/// injection) — managed in setup, read by `ensure_window`.
pub struct OverlayInit(pub String);

#[derive(Default)]
struct ReadyState {
    ready: bool,
    queue: Vec<(String, serde_json::Value)>,
}

/// Per-label ready/queue bookkeeping: `emit_to` against a window whose page has not
/// registered its JS listeners yet silently drops the event, so the first
/// `overlay://task` / `pointer://show` / `snip://begin` / `snip://result` after a
/// lazy creation is queued here instead. Windows are only ever hidden, never
/// destroyed — once ready, always ready.
#[derive(Default)]
pub struct OverlayReady(Mutex<HashMap<String, ReadyState>>);

/// Get the window with this label, building it on first use. Deliberately lock-free
/// around the build: a mutex held on a command thread while `build()` dispatches to
/// the main thread could deadlock; Tauri's label uniqueness is the atomicity
/// guarantee (a concurrent loser falls back to `get_webview_window`).
pub(crate) fn ensure_window(app: &AppHandle, label: &str) -> Result<tauri::WebviewWindow, String> {
    if let Some(window) = app.get_webview_window(label) {
        return Ok(window);
    }
    let init = app
        .try_state::<OverlayInit>()
        .map(|state| state.0.clone())
        .ok_or("Overlay-Init nicht verfügbar")?;
    let built = match label {
        OVERLAY_LABEL => build_overlay(app, &init),
        CONTROL_BORDER_LABEL => build_control_border(app, &init),
        POINTER_LABEL => build_pointer_overlay(app, &init),
        SNIP_LABEL => build_snip_overlay(app, &init),
        other => return Err(format!("Unbekanntes Overlay-Fenster: {other}")),
    };
    if let Err(err) = built {
        // A concurrent creator (double hotkey press) may have won the label race.
        if let Some(window) = app.get_webview_window(label) {
            return Ok(window);
        }
        return Err(err.to_string());
    }
    let window = app
        .get_webview_window(label)
        .ok_or_else(|| format!("Fenster '{label}' konnte nicht erzeugt werden"))?;
    // Companion windows must never contaminate the companion's own screenshots —
    // WDA exclusion has to be applied per window right after creation.
    let wda = crate::capture::exclude_from_capture(&window);
    if label == OVERLAY_LABEL {
        crate::capture::note_overlay_wda(app, wda);
    }
    if !wda {
        log::warn!("WDA_EXCLUDEFROMCAPTURE not applied for '{label}' window");
    }
    Ok(window)
}

/// Emit to an overlay window, queueing while its page is still loading. Never use
/// this for `selfdrive://emergency-stop`: if the overlay page does not exist, no
/// loop is running, and a stale stop delivered later would abort the wrong run.
pub(crate) fn emit_or_queue<T: serde::Serialize + Clone>(
    app: &AppHandle,
    label: &str,
    event: &str,
    payload: T,
) -> Result<(), String> {
    if let Some(ready) = app.try_state::<OverlayReady>() {
        if let Ok(mut map) = ready.0.lock() {
            let entry = map.entry(label.to_string()).or_default();
            if !entry.ready {
                let value = serde_json::to_value(&payload).map_err(|err| err.to_string())?;
                entry.queue.push((event.to_string(), value));
                return Ok(());
            }
        }
    }
    app.emit_to(label, event, payload).map_err(|err| err.to_string())
}

/// Called by an overlay page once its event listeners are live (`signalWindowReady`
/// in native.ts): marks the window ready and drains any queued events. The label is
/// taken from the calling window itself, not from a spoofable argument.
#[tauri::command]
pub fn overlay_window_ready(app: AppHandle, window: tauri::Window) {
    let label = window.label().to_string();
    let queued: Vec<(String, serde_json::Value)> = {
        let Some(ready) = app.try_state::<OverlayReady>() else { return };
        let Ok(mut map) = ready.0.lock() else { return };
        let entry = map.entry(label.clone()).or_default();
        entry.ready = true;
        std::mem::take(&mut entry.queue)
    };
    for (event, payload) in queued {
        let _ = app.emit_to(label.as_str(), event.as_str(), payload);
    }
}

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

/// Whether a physical desktop point lies inside a *visible* window with this label.
/// Used to exempt clicks/cursor positions on the companion chat card from the
/// guided-mode click watcher and the Selbst-Steuerung mouse-jerk stop.
pub(crate) fn point_on_window(app: &AppHandle, label: &str, x: f64, y: f64) -> bool {
    let Some(window) = app.get_webview_window(label) else {
        return false;
    };
    if !window.is_visible().unwrap_or(false) {
        return false;
    }
    let (Ok(position), Ok(size)) = (window.outer_position(), window.outer_size()) else {
        return false;
    };
    x >= position.x as f64
        && x < position.x as f64 + size.width as f64
        && y >= position.y as f64
        && y < position.y as f64 + size.height as f64
}

/// Show the overlay if hidden (building it on first use), hide it if visible.
/// Called from the global hotkey and the tray menu.
pub fn toggle_overlay(app: &AppHandle) {
    if let Some(window) = app.get_webview_window(OVERLAY_LABEL) {
        if window.is_visible().unwrap_or(false) {
            let _ = window.hide();
            return;
        }
    }
    match ensure_window(app, OVERLAY_LABEL) {
        Ok(window) => {
            let _ = window.show();
            let _ = window.set_focus();
        }
        Err(err) => log::warn!("AI-Cursor-Fenster konnte nicht erzeugt werden: {err}"),
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

/// Resize the overlay window — used when the frontend switches between the compact
/// Companion/Selbst-Steuerung view and the wider Notizen tab. Logical size, consistent
/// with the `.inner_size(420.0, 540.0)` the window was originally built with.
#[tauri::command]
pub fn overlay_resize(app: AppHandle, width: f64, height: f64) -> Result<(), String> {
    let window = app
        .get_webview_window(OVERLAY_LABEL)
        .ok_or("Overlay-Fenster nicht verfügbar")?;
    window
        .set_size(tauri::LogicalSize::new(width, height))
        .map_err(|err| err.to_string())
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
    let window = ensure_window(&app, OVERLAY_LABEL)?;
    let _ = window.show();
    let _ = window.set_focus();
    emit_or_queue(
        &app,
        OVERLAY_LABEL,
        "overlay://task",
        OverlayTaskPayload { task, goal, mode, variant_id },
    )
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

/// Show the "AI has control" border (building it on first use) — called when a
/// Selbst-Steuerung run starts. The page is static JSX with no event listeners,
/// so no ready handshake is needed.
#[tauri::command]
pub fn control_border_show(app: AppHandle) {
    match ensure_window(&app, CONTROL_BORDER_LABEL) {
        Ok(window) => {
            let _ = window.show();
        }
        Err(err) => log::warn!("Kontrollrahmen konnte nicht erzeugt werden: {err}"),
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

/// Move a full-monitor overlay window (pointer/snip/control-border) onto a specific
/// monitor: physical position first, then physical size — the order matters on
/// Windows, where the size is interpreted in the DPI of the monitor the window is
/// currently on.
pub(crate) fn fit_window_to_monitor(
    window: &tauri::WebviewWindow,
    origin_x: i32,
    origin_y: i32,
    width: u32,
    height: u32,
) {
    let _ = window.set_position(tauri::PhysicalPosition::new(origin_x, origin_y));
    let _ = window.set_size(tauri::PhysicalSize::new(width, height));
}

/// A grounded screen point pushed into the pointer overlay so it knows where to draw the
/// highlight. `space` says which coordinate space `x`/`y` live in: `"physical"` for the
/// Desktop Companion (monitor-relative physical pixels), anything else/absent for the
/// legacy UI-TARS bridge path (logical pixels, drawn as-is) — see `pointer_show`.
/// For multi-monitor captures the companion also passes the monitor's desktop origin
/// and physical width: the page derives its scale from `monitor_width / innerWidth`
/// (devicePixelRatio can lag right after the window moved between monitors with
/// different DPI) and corrects the global cursor position by the origin when dodging.
#[derive(Clone, serde::Serialize)]
pub struct PointerShowPayload {
    pub x: f64,
    pub y: f64,
    pub label: Option<String>,
    pub space: Option<String>,
    pub origin_x: Option<f64>,
    pub origin_y: Option<f64>,
    pub monitor_width: Option<f64>,
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
    origin_x: Option<f64>,
    origin_y: Option<f64>,
    monitor_width: Option<f64>,
    monitor_height: Option<f64>,
) -> Result<(), String> {
    let window = ensure_window(&app, POINTER_LABEL)?;
    // Multi-monitor: the capture tells us which monitor the point lives on — move the
    // ring window there before showing (build-time bounds cover only the primary).
    if let (Some(ox), Some(oy), Some(mw), Some(mh)) = (origin_x, origin_y, monitor_width, monitor_height)
    {
        fit_window_to_monitor(&window, ox as i32, oy as i32, mw as u32, mh as u32);
    }
    let _ = window.show();
    emit_or_queue(
        &app,
        POINTER_LABEL,
        "pointer://show",
        PointerShowPayload { x, y, label, space, origin_x, origin_y, monitor_width },
    )?;

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
                    // None = monitor under the cursor — tray users mean "this screen".
                    if let Err(err) = crate::capture::snip_start_impl(&app, None) {
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
