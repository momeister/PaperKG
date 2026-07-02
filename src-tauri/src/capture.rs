// Desktop Companion screen capture (roadmap R6).
//
// Native screenshots for the screen-aware assistant: the overlay chat asks a
// question, this module grabs the primary monitor (physical pixels) and hands the
// PNG to the frontend, which POSTs it to the backend's `/companion/*` endpoints —
// the VLM call itself stays in Python (`query/screen_companion.py`, LLMRouter).
// Nothing here drives mouse or keyboard; the companion only *sees* and *points*.
//
// Two contamination problems are solved here:
//   * The companion's own windows (chat card, pointer ring) must not appear in the
//     screenshot the VLM reasons about. On Windows 10 2004+ they are excluded via
//     `SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`; where that fails the
//     capture deterministically hides them, waits a beat, captures, and restores.
//   * The "Bereich erklären" snip works on a **freeze-frame**: the full screen is
//     captured *first*, then the fullscreen snip window opens showing that frozen
//     image — its own dim/marquee can never leak into the selected region.

use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::Mutex;
use std::time::Duration;

use base64::Engine as _;
use image::RgbaImage;
use tauri::{AppHandle, Emitter, Manager, State};
use xcap::Monitor;

use crate::overlay::{CONTROL_BORDER_LABEL, OVERLAY_LABEL, POINTER_LABEL, SNIP_LABEL};

/// How long to give the compositor to actually unmap a freshly hidden window
/// before capturing (only needed on the no-WDA fallback path).
const HIDE_SETTLE: Duration = Duration::from_millis(180);

#[derive(Default)]
pub struct CaptureState {
    /// Whether WDA_EXCLUDEFROMCAPTURE took effect for the chat overlay — if so,
    /// captures skip the hide/restore fallback for it.
    wda_ok: AtomicBool,
    /// Freeze-frame held between `snip_start` and `snip_finish`/`snip_cancel`.
    snip_image: Mutex<Option<RgbaImage>>,
}

/// One physical display for the overlay's monitor picker. Coordinates are the
/// monitor's origin in the virtual desktop (physical pixels — Tauri runs
/// per-monitor-DPI-aware, so xcap bounds and `cursor_position` share that space).
/// xcap monitor ids are only stable for the current session — the picker reloads
/// the list every time the overlay opens.
#[derive(Clone, serde::Serialize)]
pub struct MonitorInfo {
    pub id: u32,
    pub name: String,
    pub x: i32,
    pub y: i32,
    pub width: u32,
    pub height: u32,
    pub scale_factor: f64,
    pub is_primary: bool,
}

/// A full-screen capture handed to the frontend. `width`/`height` are physical
/// monitor pixels — the same space the backend's guide steps come back in.
/// `origin_x`/`origin_y` locate the captured monitor in the virtual desktop so
/// the pointer/snip windows can be moved onto it.
#[derive(Clone, serde::Serialize)]
pub struct CaptureResult {
    pub image_base64: String,
    pub width: u32,
    pub height: u32,
    pub scale_factor: f64,
    pub monitor_id: u32,
    pub monitor_name: String,
    pub origin_x: i32,
    pub origin_y: i32,
}

/// The frozen full-screen frame pushed into the snip window for region selection.
#[derive(Clone, serde::Serialize)]
pub struct SnipBeginPayload {
    pub image_base64: String,
    pub width: u32,
    pub height: u32,
    pub scale_factor: f64,
    pub monitor_id: u32,
    pub monitor_name: String,
    pub origin_x: i32,
    pub origin_y: i32,
}

/// The cropped region pushed back into the chat overlay ("Bereich erklären").
#[derive(Clone, serde::Serialize)]
pub struct SnipResultPayload {
    pub image_base64: String,
    pub width: u32,
    pub height: u32,
}

#[derive(Clone, serde::Serialize)]
pub struct CursorPosition {
    pub x: f64,
    pub y: f64,
}

fn monitor_info(monitor: &Monitor) -> MonitorInfo {
    MonitorInfo {
        id: monitor.id().unwrap_or(0),
        name: monitor.name().unwrap_or_default(),
        x: monitor.x().unwrap_or(0),
        y: monitor.y().unwrap_or(0),
        width: monitor.width().unwrap_or(0),
        height: monitor.height().unwrap_or(0),
        scale_factor: monitor.scale_factor().map(f64::from).unwrap_or(1.0),
        is_primary: monitor.is_primary().unwrap_or(false),
    }
}

fn monitor_contains(monitor: &Monitor, x: f64, y: f64) -> bool {
    let mx = monitor.x().unwrap_or(0) as f64;
    let my = monitor.y().unwrap_or(0) as f64;
    let mw = monitor.width().unwrap_or(0) as f64;
    let mh = monitor.height().unwrap_or(0) as f64;
    x >= mx && x < mx + mw && y >= my && y < my + mh
}

/// Resolve which monitor a companion action targets: an explicit picker id wins,
/// otherwise the monitor under the cursor ("the screen I'm looking at"), otherwise
/// the primary, otherwise the first one xcap knows.
fn target_monitor(app: &AppHandle, selector: Option<u32>) -> Result<Monitor, String> {
    let monitors = Monitor::all().map_err(|err| err.to_string())?;
    let cursor = if selector.is_none() { app.cursor_position().ok() } else { None };
    let mut primary = None;
    let mut fallback = None;
    for monitor in monitors {
        if let Some(id) = selector {
            if monitor.id().map(|value| value == id).unwrap_or(false) {
                return Ok(monitor);
            }
        }
        if let Some(position) = cursor.as_ref() {
            if monitor_contains(&monitor, position.x, position.y) {
                return Ok(monitor);
            }
        }
        if primary.is_none() && monitor.is_primary().unwrap_or(false) {
            primary = Some(monitor);
            continue;
        }
        if fallback.is_none() {
            fallback = Some(monitor);
        }
    }
    primary.or(fallback).ok_or_else(|| "Kein Monitor gefunden".to_string())
}

/// All displays for the overlay's monitor picker.
#[tauri::command]
pub fn list_monitors() -> Result<Vec<MonitorInfo>, String> {
    let monitors = Monitor::all().map_err(|err| err.to_string())?;
    Ok(monitors.iter().map(monitor_info).collect())
}

fn encode_png(image: &RgbaImage) -> Result<String, String> {
    let mut buffer = Vec::new();
    image
        .write_to(&mut std::io::Cursor::new(&mut buffer), image::ImageFormat::Png)
        .map_err(|err| err.to_string())?;
    Ok(base64::engine::general_purpose::STANDARD.encode(buffer))
}

/// Capture the target monitor with the companion's own windows kept out of frame:
/// the pointer ring is always hidden (and restored), the chat overlay only when
/// WDA exclusion isn't in effect for it.
fn grab_screen(
    app: &AppHandle,
    state: &CaptureState,
    selector: Option<u32>,
) -> Result<(RgbaImage, MonitorInfo), String> {
    let pointer = app.get_webview_window(POINTER_LABEL);
    let pointer_was_visible = pointer
        .as_ref()
        .and_then(|window| window.is_visible().ok())
        .unwrap_or(false);
    if pointer_was_visible {
        if let Some(window) = pointer.as_ref() {
            let _ = window.hide();
        }
    }

    let overlay = app.get_webview_window(OVERLAY_LABEL);
    let overlay_needs_hide = !state.wda_ok.load(Ordering::SeqCst)
        && overlay
            .as_ref()
            .and_then(|window| window.is_visible().ok())
            .unwrap_or(false);
    if overlay_needs_hide {
        if let Some(window) = overlay.as_ref() {
            let _ = window.hide();
        }
    }
    if pointer_was_visible || overlay_needs_hide {
        std::thread::sleep(HIDE_SETTLE);
    }

    let captured = (|| {
        let monitor = target_monitor(app, selector)?;
        let info = monitor_info(&monitor);
        let image = monitor.capture_image().map_err(|err| err.to_string())?;
        Ok((image, info))
    })();

    if overlay_needs_hide {
        if let Some(window) = overlay.as_ref() {
            let _ = window.show();
        }
    }
    if pointer_was_visible {
        if let Some(window) = pointer.as_ref() {
            let _ = window.show();
        }
    }
    captured
}

/// Screenshot one monitor for a companion question (`monitor` = picker id, `None` =
/// monitor under the cursor). Runs on a blocking thread — capture + PNG encode of a
/// 4K frame takes visible milliseconds.
#[tauri::command]
pub async fn capture_screen(app: AppHandle, monitor: Option<u32>) -> Result<CaptureResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<CaptureState>();
        let (image, info) = grab_screen(&app, &state, monitor)?;
        let (width, height) = image.dimensions();
        Ok(CaptureResult {
            image_base64: encode_png(&image)?,
            width,
            height,
            scale_factor: info.scale_factor,
            monitor_id: info.id,
            monitor_name: info.name,
            origin_x: info.x,
            origin_y: info.y,
        })
    })
    .await
    .map_err(|err| err.to_string())?
}

/// Begin a "Bereich erklären" region selection (invoked from the overlay button).
#[tauri::command]
pub async fn snip_start(app: AppHandle, monitor: Option<u32>) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || snip_start_impl(&app, monitor))
        .await
        .map_err(|err| err.to_string())?
}

/// Freeze-frame snip: capture the full screen FIRST, stash the frame, then open the
/// fullscreen snip window (moved onto the captured monitor) drawing that frozen
/// image. Also called from the tray menu (on its own thread — this blocks for the
/// capture; `None` = monitor under the cursor).
pub fn snip_start_impl(app: &AppHandle, monitor: Option<u32>) -> Result<(), String> {
    let state = app.state::<CaptureState>();
    let (image, info) = grab_screen(app, &state, monitor)?;
    let (width, height) = image.dimensions();
    let payload = SnipBeginPayload {
        image_base64: encode_png(&image)?,
        width,
        height,
        scale_factor: info.scale_factor,
        monitor_id: info.id,
        monitor_name: info.name.clone(),
        origin_x: info.x,
        origin_y: info.y,
    };
    *state
        .snip_image
        .lock()
        .map_err(|_| "Snip-Status nicht verfügbar".to_string())? = Some(image);

    // The always-on-top chat card would float over the frozen frame and fight the
    // marquee for z-order; it comes back with the result (or on cancel).
    if let Some(window) = app.get_webview_window(OVERLAY_LABEL) {
        let _ = window.hide();
    }
    let window = app
        .get_webview_window(SNIP_LABEL)
        .ok_or("Snip-Fenster nicht verfügbar")?;
    // The frozen frame belongs to one specific monitor — the marquee window must
    // sit exactly on it or the crop coordinates would map to the wrong screen.
    crate::overlay::fit_window_to_monitor(&window, info.x, info.y, info.width, info.height);
    let _ = window.show();
    let _ = window.set_focus();
    app.emit_to(SNIP_LABEL, "snip://begin", payload)
        .map_err(|err| err.to_string())
}

fn close_snip_window(app: &AppHandle, state: &CaptureState) {
    if let Ok(mut guard) = state.snip_image.lock() {
        *guard = None;
    }
    if let Some(window) = app.get_webview_window(SNIP_LABEL) {
        let _ = window.hide();
    }
    if let Some(window) = app.get_webview_window(OVERLAY_LABEL) {
        let _ = window.show();
        let _ = window.set_focus();
    }
}

/// Finish the region selection: crop the frozen frame (coordinates in physical
/// pixels, clamped to the image) and push the region into the chat overlay.
#[tauri::command]
pub fn snip_finish(
    app: AppHandle,
    state: State<'_, CaptureState>,
    x: f64,
    y: f64,
    width: f64,
    height: f64,
) -> Result<(), String> {
    let image = state
        .snip_image
        .lock()
        .map_err(|_| "Snip-Status nicht verfügbar".to_string())?
        .take()
        .ok_or("Kein eingefrorener Screenshot vorhanden")?;
    close_snip_window(&app, &state);

    let (img_w, img_h) = image.dimensions();
    let x0 = (x.max(0.0) as u32).min(img_w.saturating_sub(1));
    let y0 = (y.max(0.0) as u32).min(img_h.saturating_sub(1));
    let crop_w = (width.max(1.0) as u32).min(img_w - x0);
    let crop_h = (height.max(1.0) as u32).min(img_h - y0);
    let region = image::imageops::crop_imm(&image, x0, y0, crop_w, crop_h).to_image();

    app.emit_to(
        OVERLAY_LABEL,
        "snip://result",
        SnipResultPayload {
            image_base64: encode_png(&region)?,
            width: crop_w,
            height: crop_h,
        },
    )
    .map_err(|err| err.to_string())
}

/// Abort the region selection (Esc / too-small drag) — restores the chat overlay.
#[tauri::command]
pub fn snip_cancel(app: AppHandle, state: State<'_, CaptureState>) {
    close_snip_window(&app, &state);
}

/// Global cursor position in physical pixels — polled by the pointer overlay for the
/// dodge behaviour (its window is click-through, so it never gets mouse events itself).
#[tauri::command]
pub fn cursor_position(app: AppHandle) -> Result<CursorPosition, String> {
    let position = app.cursor_position().map_err(|err| err.to_string())?;
    Ok(CursorPosition { x: position.x, y: position.y })
}

/// Exclude a window from screen captures so it never contaminates what the VLM sees.
/// Windows-only (`SetWindowDisplayAffinity(WDA_EXCLUDEFROMCAPTURE)`, Win10 2004+);
/// returns whether the exclusion actually took effect.
fn exclude_from_capture(window: &tauri::WebviewWindow) -> bool {
    #[cfg(windows)]
    {
        use windows::Win32::UI::WindowsAndMessaging::{
            SetWindowDisplayAffinity, WDA_EXCLUDEFROMCAPTURE,
        };
        if let Ok(hwnd) = window.hwnd() {
            return unsafe { SetWindowDisplayAffinity(hwnd, WDA_EXCLUDEFROMCAPTURE) }.is_ok();
        }
        false
    }
    #[cfg(not(windows))]
    {
        let _ = window;
        false
    }
}

/// Apply capture-exclusion to all companion overlay windows at startup. Only the chat
/// overlay's result is remembered (`wda_ok`) — it decides whether `grab_primary` needs
/// the hide/restore fallback; the pointer ring is hidden during captures regardless.
pub fn setup_capture_exclusion(app: &AppHandle) {
    let state = app.state::<CaptureState>();
    for label in [OVERLAY_LABEL, CONTROL_BORDER_LABEL, POINTER_LABEL, SNIP_LABEL] {
        if let Some(window) = app.get_webview_window(label) {
            let ok = exclude_from_capture(&window);
            if label == OVERLAY_LABEL {
                state.wda_ok.store(ok, Ordering::SeqCst);
            }
            if !ok {
                log::warn!("WDA_EXCLUDEFROMCAPTURE not applied for '{label}' window");
            }
        }
    }
}
