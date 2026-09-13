//! Related workspace webviews host DOM views owned by the main React controller.
//! No frontend app root or backend is started when a pane window opens.
use serde::{Deserialize, Serialize};
use std::collections::BTreeMap;
use std::sync::atomic::{AtomicBool, Ordering};
use tauri::{
    webview::NewWindowResponse, AppHandle, Emitter, Manager, WebviewUrl, WebviewWindow,
    WebviewWindowBuilder,
};

pub const PANES: [&str; 4] = ["navigator", "center", "assistant", "notes"];
#[derive(Default)]
pub struct WorkspaceWindowState {
    pub exiting: AtomicBool,
    geometry_lock: std::sync::Mutex<()>,
}

#[derive(Clone, Debug, Serialize, Deserialize)]
struct Geometry {
    width: f64,
    height: f64,
    x: i32,
    y: i32,
}
fn geometry_path(app: &AppHandle) -> Result<std::path::PathBuf, String> {
    Ok(app
        .path()
        .app_config_dir()
        .map_err(|e| e.to_string())?
        .join("workspace-windows.json"))
}
fn read_geometry(app: &AppHandle) -> BTreeMap<String, Geometry> {
    geometry_path(app)
        .ok()
        .and_then(|p| std::fs::read(p).ok())
        .and_then(|bytes| serde_json::from_slice(&bytes).ok())
        .unwrap_or_default()
}
fn save_geometry(app: &AppHandle, only_pane: Option<&str>) -> Result<(), String> {
    let state = app.state::<WorkspaceWindowState>();
    let _guard = state.geometry_lock.lock().map_err(|e| e.to_string())?;
    let mut geometries = read_geometry(app);
    for pane in PANES {
        // Concurrent docking may already have destroyed another related view.
        // Capture only this window when closing; capture all on app shutdown.
        if only_pane.is_some() && only_pane != Some(pane) {
            continue;
        }
        if let Some(window) = app.get_webview_window(&pane_label(pane)?) {
            let scale = window.scale_factor().map_err(|e| e.to_string())?;
            let size = window
                .inner_size()
                .map_err(|e| e.to_string())?
                .to_logical::<f64>(scale);
            let position = window.outer_position().unwrap_or_default();
            geometries.insert(
                pane.into(),
                Geometry {
                    width: size.width,
                    height: size.height,
                    x: position.x,
                    y: position.y,
                },
            );
        }
    }
    let path = geometry_path(app)?;
    std::fs::create_dir_all(path.parent().unwrap()).map_err(|e| e.to_string())?;
    let temporary = path.with_extension("json.tmp");
    std::fs::write(
        &temporary,
        serde_json::to_vec(&geometries).map_err(|e| e.to_string())?,
    )
    .map_err(|e| e.to_string())?;
    std::fs::rename(temporary, path).map_err(|e| e.to_string())
}
fn restore_geometry(window: &WebviewWindow, geometry: &Geometry) -> tauri::Result<()> {
    let monitors = window.available_monitors()?;
    let monitor = monitors.iter().find(|m| {
        let p = m.position();
        let s = m.size();
        geometry.x >= p.x
            && geometry.y >= p.y
            && i64::from(geometry.x) < i64::from(p.x) + i64::from(s.width)
            && i64::from(geometry.y) < i64::from(p.y) + i64::from(s.height)
    });
    let Some(monitor) = monitor.or_else(|| monitors.first()) else {
        return Ok(());
    };
    let size = monitor.size().to_logical::<f64>(monitor.scale_factor());
    let width = geometry.width.clamp(300.0, size.width.max(300.0));
    let height = geometry
        .height
        .clamp(240.0, (size.height - 64.0).max(240.0));
    window.set_size(tauri::LogicalSize::new(width, height))?;
    // Wayland intentionally delegates placement to the compositor.
    let wayland = cfg!(target_os = "linux")
        && std::env::var_os("WAYLAND_DISPLAY").is_some()
        && std::env::var("GDK_BACKEND").unwrap_or_default() != "x11";
    if !wayland {
        let origin = monitor.position();
        let right = origin.x
            + (monitor.size().width as f64 - width * monitor.scale_factor()).max(0.0) as i32;
        let bottom = origin.y
            + (monitor.size().height as f64 - height * monitor.scale_factor() - 48.0).max(0.0)
                as i32;
        window.set_position(tauri::PhysicalPosition::new(
            geometry.x.clamp(origin.x, right),
            geometry.y.clamp(origin.y, bottom),
        ))?;
    }
    Ok(())
}

pub fn enable_related_windows(window: &WebviewWindow) -> tauri::Result<()> {
    #[cfg(target_os = "linux")]
    window.with_webview(|webview| {
        use webkit2gtk::{SettingsExt, WebViewExt};
        if let Some(settings) = webview.inner().settings() {
            settings.set_javascript_can_open_windows_automatically(true);
        }
    })?;
    let _ = window;
    Ok(())
}

fn pane_label(pane: &str) -> Result<String, String> {
    if PANES.contains(&pane) {
        Ok(format!("workspace-{pane}"))
    } else {
        Err("Unbekannter Arbeitsplatz-Bereich".into())
    }
}

fn requested_pane(url: &tauri::Url, main_url: &tauri::Url) -> Option<String> {
    // url::Origin is opaque for tauri://, so compare its concrete authority.
    if url.scheme() != main_url.scheme()
        || url.host_str() != main_url.host_str()
        || url.port_or_known_default() != main_url.port_or_known_default()
        || !url.username().is_empty()
        || url.password().is_some()
        || url.path() != "/workspace-pane.html"
    {
        return None;
    }
    let pane = url
        .query_pairs()
        .find(|(key, _)| key == "pane")?
        .1
        .into_owned();
    pane_label(&pane).ok()?;
    Some(pane)
}

pub fn install(
    builder: WebviewWindowBuilder<'_, tauri::Wry, AppHandle>,
    app: AppHandle,
    init: String,
) -> WebviewWindowBuilder<'_, tauri::Wry, AppHandle> {
    builder.on_new_window(move |url, features| {
        let Some(main) = app.get_webview_window("main") else {
            return NewWindowResponse::Deny;
        };
        let Ok(main_url) = main.url() else {
            return NewWindowResponse::Deny;
        };
        let Some(pane) = requested_pane(&url, &main_url) else {
            log::warn!("rejected workspace window URL {url}, main {main_url}");
            return NewWindowResponse::Deny;
        };
        let label = pane_label(&pane).unwrap();
        if let Some(window) = app.get_webview_window(&label) {
            let _ = window.show();
            let _ = window.set_focus();
            return NewWindowResponse::Deny;
        }
        // window_features links the opener (WebKit related view / WebView2
        // environment / WK configuration). This is essential for stable portals.
        let result = WebviewWindowBuilder::new(
            &app,
            &label,
            WebviewUrl::External("about:blank".parse().unwrap()),
        )
        .window_features(features)
        .title(format!("{pane} — ScienceKG"))
        .inner_size(800.0, 800.0)
        .min_inner_size(300.0, 240.0)
        .visible(false)
        .disable_drag_drop_handler()
        .initialization_script(&init)
        .on_navigation(move |target| {
            target.as_str() == "about:blank"
                || requested_pane(target, &main_url).as_deref() == Some(pane.as_str())
        })
        .build();
        match result {
            Ok(window) => {
                if let Some(geometry) =
                    read_geometry(&app).get(label.strip_prefix("workspace-").unwrap())
                {
                    if geometry.width.is_finite() && geometry.height.is_finite() {
                        if let Err(error) = restore_geometry(&window, geometry) {
                            log::warn!("workspace geometry: {error}");
                        }
                    }
                }
                NewWindowResponse::Create { window }
            }
            Err(error) => {
                log::error!("workspace window failed: {error}");
                NewWindowResponse::Deny
            }
        }
    })
}

#[tauri::command]
pub async fn workspace_window_action(
    app: AppHandle,
    window: WebviewWindow,
    pane: String,
    action: String,
) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Nur das Hauptfenster verwaltet den Arbeitsplatz".into());
    }
    let label = pane_label(&pane)?;
    let target = app.get_webview_window(&label);
    if action == "destroy" && target.is_none() {
        return Ok(());
    }
    let target = target.ok_or("Bereichsfenster nicht vorhanden")?;
    match action.as_str() {
        "show" => {
            target.show().map_err(|e| e.to_string())?;
            target.set_focus()
        }
        "destroy" => {
            save_geometry(&app, Some(&pane))?;
            target.destroy().map_err(|e| e.to_string())?;
            // Destruction is queued on the UI thread. A new related window must
            // not race the old window's registration under the same label.
            return tauri::async_runtime::spawn_blocking(move || {
                for _ in 0..150 {
                    if app.get_webview_window(&label).is_none() {
                        return Ok(());
                    }
                    std::thread::sleep(std::time::Duration::from_millis(20));
                }
                Err("Bereichsfenster wurde noch nicht geschlossen".to_string())
            })
            .await
            .map_err(|e| e.to_string())?;
        }
        "drag" => target.start_dragging(),
        _ => return Err("Unbekannte Fensteraktion".into()),
    }
    .map_err(|e| e.to_string())
}

#[tauri::command]
pub async fn workspace_finish_exit(app: AppHandle, window: WebviewWindow) -> Result<(), String> {
    if window.label() != "main" {
        return Err("Nur das Hauptfenster beendet die App".into());
    }
    save_geometry(&app, None)?;
    app.state::<WorkspaceWindowState>()
        .exiting
        .store(true, Ordering::SeqCst);
    app.exit(0);
    Ok(())
}

pub fn close_requested(window: &tauri::Window, api: &tauri::CloseRequestApi) {
    let app = window.app_handle();
    if app
        .state::<WorkspaceWindowState>()
        .exiting
        .load(Ordering::SeqCst)
    {
        return;
    }
    if window.label() == "main" {
        api.prevent_close();
        let _ = app.emit_to("main", "workspace-exit-request", ());
    } else if let Some(pane) = window.label().strip_prefix("workspace-") {
        if PANES.contains(&pane) {
            api.prevent_close();
            let _ = app.emit_to("main", "workspace-dock-request", pane);
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn only_fixed_workspace_ids() {
        for pane in PANES {
            assert_eq!(pane_label(pane).unwrap(), format!("workspace-{pane}"));
        }
        for pane in ["main", "overlay", "../notes", "", "Notes"] {
            assert!(pane_label(pane).is_err());
        }
    }
    #[test]
    fn related_windows_require_our_origin_and_view_entry() {
        let main = "http://localhost:5173/index.html".parse().unwrap();
        assert_eq!(
            requested_pane(
                &"http://localhost:5173/workspace-pane.html?pane=notes"
                    .parse()
                    .unwrap(),
                &main
            ),
            Some("notes".into())
        );
        for url in [
            "https://example.org/workspace-pane.html?pane=notes",
            "http://localhost:5173/index.html?pane=notes",
            "http://localhost:5173/workspace-pane.html?pane=main",
        ] {
            assert!(requested_pane(&url.parse().unwrap(), &main).is_none());
        }
    }
    #[test]
    fn bundled_asset_protocol_keeps_its_authority() {
        let main = "tauri://localhost/index.html".parse().unwrap();
        assert_eq!(
            requested_pane(
                &"tauri://localhost/workspace-pane.html?pane=center"
                    .parse()
                    .unwrap(),
                &main
            ),
            Some("center".into())
        );
        for url in [
            "tauri://other/workspace-pane.html?pane=center",
            "https://localhost/workspace-pane.html?pane=center",
            "tauri://user@localhost/workspace-pane.html?pane=center",
        ] {
            assert!(requested_pane(&url.parse().unwrap(), &main).is_none());
        }
    }
}
