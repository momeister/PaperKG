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

/// A full-screen capture handed to the frontend. `width`/`height` are physical
/// monitor pixels — the same space the backend's guide steps come back in.
#[derive(Clone, serde::Serialize)]
pub struct CaptureResult {
    pub image_base64: String,
    pub width: u32,
    pub height: u32,
    pub scale_factor: f64,
}

/// The frozen full-screen frame pushed into the snip window for region selection.
#[derive(Clone, serde::Serialize)]
pub struct SnipBeginPayload {
    pub image_base64: String,
    pub width: u32,
    pub height: u32,
    pub scale_factor: f64,
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

fn primary_monitor() -> Result<Monitor, String> {
    let monitors = Monitor::all().map_err(|err| err.to_string())?;
    let mut fallback = None;
    for monitor in monitors {
        if monitor.is_primary().unwrap_or(false) {
            return Ok(monitor);
        }
        if fallback.is_none() {
            fallback = Some(monitor);
        }
    }
    fallback.ok_or_else(|| "Kein Monitor gefunden".to_string())
}

fn encode_png(image: &RgbaImage) -> Result<String, String> {
    let mut buffer = Vec::new();
    image
        .write_to(&mut std::io::Cursor::new(&mut buffer), image::ImageFormat::Png)
        .map_err(|err| err.to_string())?;
    Ok(base64::engine::general_purpose::STANDARD.encode(buffer))
}

/// Capture the primary monitor with the companion's own windows kept out of frame:
/// the pointer ring is always hidden (and restored), the chat overlay only when
/// WDA exclusion isn't in effect for it.
fn grab_primary(app: &AppHandle, state: &CaptureState) -> Result<(RgbaImage, f64), String> {
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
        let monitor = primary_monitor()?;
        let scale = monitor.scale_factor().map(f64::from).unwrap_or(1.0);
        let image = monitor.capture_image().map_err(|err| err.to_string())?;
        Ok((image, scale))
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

/// Screenshot the primary monitor for a companion question. Runs on a blocking
/// thread — capture + PNG encode of a 4K frame takes visible milliseconds.
#[tauri::command]
pub async fn capture_screen(app: AppHandle) -> Result<CaptureResult, String> {
    tauri::async_runtime::spawn_blocking(move || {
        let state = app.state::<CaptureState>();
        let (image, scale_factor) = grab_primary(&app, &state)?;
        let (width, height) = image.dimensions();
        Ok(CaptureResult {
            image_base64: encode_png(&image)?,
            width,
            height,
            scale_factor,
        })
    })
    .await
    .map_err(|err| err.to_string())?
}

/// Begin a "Bereich erklären" region selection (invoked from the overlay button).
#[tauri::command]
pub async fn snip_start(app: AppHandle) -> Result<(), String> {
    tauri::async_runtime::spawn_blocking(move || snip_start_impl(&app))
        .await
        .map_err(|err| err.to_string())?
}

/// Freeze-frame snip: capture the full screen FIRST, stash the frame, then open the
/// fullscreen snip window drawing that frozen image. Also called from the tray menu
/// (on its own thread — this blocks for the capture).
pub fn snip_start_impl(app: &AppHandle) -> Result<(), String> {
    let state = app.state::<CaptureState>();
    let (image, scale_factor) = grab_primary(app, &state)?;
    let (width, height) = image.dimensions();
    let payload = SnipBeginPayload {
        image_base64: encode_png(&image)?,
        width,
        height,
        scale_factor,
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
