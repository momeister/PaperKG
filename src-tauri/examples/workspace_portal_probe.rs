//! Native related-document smoke probe, without a backend or user data.
//! Run with `xvfb-run cargo run --example workspace_portal_probe` on Linux/X11.
#[path = "../src/workspace_windows.rs"]
mod workspace_windows;
use std::sync::atomic::{AtomicI32, Ordering};
use tauri::{Manager, WebviewUrl, WebviewWindowBuilder};
static EXIT_STATUS: AtomicI32 = AtomicI32::new(2);

#[tauri::command]
async fn probe_close(app: tauri::AppHandle) -> Result<(), String> {
    app.get_webview_window("workspace-notes")
        .ok_or("notes window missing")?
        .close()
        .map_err(|e| e.to_string())
}

#[tauri::command]
fn probe_step(detail: String) {
    println!("WORKSPACE_PROBE_STEP {detail}");
}

#[tauri::command]
fn probe_result(app: tauri::AppHandle, ok: bool, detail: String) {
    println!("WORKSPACE_PORTAL_PROBE ok={ok} {detail}");
    EXIT_STATUS.store(if ok { 0 } else { 1 }, Ordering::SeqCst);
    app.exit(if ok { 0 } else { 1 });
}

fn main() {
    let mut context = tauri::generate_context!();
    // Native geometry commands must never touch the actual app's layout.
    context.config_mut().identifier = "com.sciencekg.workspaceprobe".into();
    let react_probe = std::env::var_os("SCIENCEKG_PORTAL_PROBE_REACT").is_some();
    let script = r#"
    addEventListener('DOMContentLoaded', () => {
      if (new URLSearchParams(location.search).has('pane')) return;
      const report = (ok, detail) => window.__TAURI_INTERNALS__.invoke('probe_result', {ok, detail});
      const source = document.createElement('div');
      const input = document.createElement('textarea'); input.value = 'ungesicherter Entwurf';
      source.append(input); document.body.append(source); input.setSelectionRange(2, 8);
      const url = new URL('workspace-pane.html?pane=notes&actionId=probe', location.href);
      let child;
      addEventListener('message', e => {
        if (e.data?.type !== 'ready' || e.data?.actionId !== 'probe') return;
        try {
          if (e.source !== child) throw new Error('opener relationship missing');
          const root = child.document.getElementById('workspace-pane-root');
          root.append(source);
          const adopted = source.ownerDocument === child.document && input.value === 'ungesicherter Entwurf';
          let clicks = 0; source.addEventListener('click', () => clicks++); source.click();
          document.body.append(source);
          const returned = source.ownerDocument === document && input.value === 'ungesicherter Entwurf';
          report(adopted && returned && clicks === 1, JSON.stringify({ adopted, returned, clicks, start: input.selectionStart, end: input.selectionEnd }));
        } catch (e) { report(false, String(e)); }
      });
      child = window.open(url.href, 'workspace-notes', 'popup,width=600,height=600');
      if (!child) report(false, 'window.open returned null: '+location.href+' target '+url.href);
      setTimeout(() => report(false, 'readiness timeout'), 10000);
    });
    "#;
    let react_script = r#"
    const report = (ok, detail) => window.__TAURI_INTERNALS__.invoke('probe_result', {ok, detail});
    const delay = ms => new Promise(resolve => setTimeout(resolve, ms));
    const errors = [];
    addEventListener('error', e => errors.push(e.message));
    addEventListener('unhandledrejection', e => errors.push(String(e.reason)));
    addEventListener('DOMContentLoaded', async () => {
      if (!location.pathname.endsWith('/workspaceWindowsHarness.html')) return;
      try {
        for (let i=0; i<200 && document.querySelector('textarea.markdown-editor')?.value !== 'Entwurf'; i++) await delay(50);
        if (!window.manager) throw new Error('React fixture not loaded: '+JSON.stringify({errors,text:document.body.innerText.slice(0,400)}));
        const step = detail => window.__TAURI_INTERNALS__.invoke('probe_step', {detail});
        await step('fixture loaded');
        const ink = doc => {
          const canvas = doc.querySelector('.pdf-page[data-page-number="5"] canvas');
          if (!canvas?.width) return 0;
          const data = canvas.getContext('2d').getImageData(0,0,canvas.width,canvas.height).data;
          let count=0; for(let i=0;i<data.length;i+=4) if(data[i+3]>200 && data[i]<180 && data[i+1]<180 && data[i+2]<180) count++;
          return count;
        };
        const waitInk = async doc => {
          for(let i=0;i<100 && ink(doc)<100;i++) await delay(50);
          if(ink(doc)<100) throw new Error('Visible PDF ink missing in '+doc.URL+' '+JSON.stringify({errors,detached:manager.isDetached('center'),failure:manager.error('center')}));
        };
        for(let i=0;i<100 && !document.querySelector('.pdf-page[data-page-number="5"] canvas');i++) await delay(50);
        const pdfPage = document.querySelector('.pdf-page[data-page-number="5"]');
        if(!pdfPage) throw new Error('PDF did not load');
        for(let i=0;i<100 && !pdfPage.querySelector('canvas')?.width;i++) await delay(50);
        await delay(200);
        pdfPage.scrollIntoView(); await waitInk(document); await step("main PDF ink");
        if (!window.manager) throw new Error('React fixture not loaded: '+JSON.stringify({url:location.href,errors,text:document.body.innerText.slice(0,200)}));
        const editor = document.querySelector('textarea.markdown-editor');
        if (!editor) throw new Error('Editor missing');
        [...document.querySelectorAll('button')].find(b => b.textContent === 'Arbeit starten').click();
        editor.focus(); editor.setSelectionRange(1, 3);
        await Promise.all(['navigator','center','assistant','notes'].map(pane => manager.detach(pane)));
        for (const pane of ['navigator','center','assistant','notes']) {
          if (!manager.isDetached(pane)) throw new Error(pane+': '+manager.error(pane));
        }
        await step('four views detached');
        document.querySelector('[data-panel-group]').parentElement.hidden = true;
        await waitInk(window.children.get('center').document);
        document.querySelector('[data-panel-group]').parentElement.hidden = false;
        const remote = window.children.get('notes').document.querySelector('textarea.markdown-editor');
        if (remote !== editor || remote.selectionStart !== 1 || remote.selectionEnd !== 3) throw new Error('Editor identity/selection lost: '+JSON.stringify({same:remote===editor,start:remote.selectionStart,end:remote.selectionEnd,value:remote.value}));
        Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype,'value').set.call(remote,'Native React draft');
        remote.dispatchEvent(new Event('input', {bubbles:true}));
        finish(); await delay(1800);
        const output = window.children.get('assistant').document.querySelector('output').textContent;
        if (output !== 'Antwort fertig' || saved.at(-1)?.markdown !== 'Native React draft') throw new Error('Remote React events or background work failed: '+JSON.stringify({output,saved}));
        await window.__TAURI_INTERNALS__.invoke('probe_close');
        for (let i=0; i<100 && manager.isDetached('notes'); i++) await delay(50);
        if (manager.isDetached('notes')) throw new Error('Native close was not acknowledged');
        await Promise.all(['navigator','center','assistant'].map(pane => manager.dock(pane)));
        if (document.querySelector('textarea.markdown-editor') !== editor || work.mounts !== 1 || work.started !== 1 || work.completed !== 1) throw new Error('Controller remounted');
        await waitInk(document); await step('first docking and ink: '+JSON.stringify(['navigator','center','assistant'].map(p=>[p,manager.error(p)])));
        for(let round=0;round<2;round++) {
          await step('repeat detach '+round); await manager.detach('center'); if(!manager.isDetached('center')) throw new Error('Repeated detach: '+manager.error('center')); await waitInk(window.children.get('center').document);
          await manager.dock('center'); await waitInk(document);
        }
        report(true, 'PDF page 5 canvas ink before/after three moves and hidden owner; React: four related views, editor identity/selection, events, autosave, ongoing work, native show/destroy/close, docking');
      } catch(e) { report(false,String(e)); }
    });
    "#;
    tauri::Builder::default()
        .plugin(
            tauri_plugin_log::Builder::default()
                .level(log::LevelFilter::Debug)
                .build(),
        )
        .manage(workspace_windows::WorkspaceWindowState::default())
        .invoke_handler(tauri::generate_handler![
            probe_result,
            probe_step,
            probe_close,
            workspace_windows::workspace_window_action
        ])
        .on_window_event(|window, event| {
            if let tauri::WindowEvent::CloseRequested { api, .. } = event {
                workspace_windows::close_requested(window, api);
            }
        })
        .setup(move |app| {
            let entry = if react_probe {
                WebviewUrl::External(
                    "http://127.0.0.1:5173/e2e/fixtures/workspaceWindowsHarness.html?native=1"
                        .parse()
                        .unwrap(),
                )
            } else {
                WebviewUrl::App("workspace-pane.html".into())
            };
            let builder = WebviewWindowBuilder::new(app.handle(), "main", entry)
                .visible(react_probe)
                .initialization_script(if react_probe { react_script } else { script });
            let window =
                workspace_windows::install(builder, app.handle().clone(), String::new()).build()?;
            workspace_windows::enable_related_windows(&window)?;
            let handle = app.handle().clone();
            std::thread::spawn(move || {
                std::thread::sleep(std::time::Duration::from_secs(60));
                handle.exit(2);
            });
            Ok(())
        })
        .build(context)
        .expect("native probe")
        .run(|_, event| {
            if let tauri::RunEvent::Exit = event {
                std::process::exit(EXIT_STATUS.load(Ordering::SeqCst));
            }
        });
}
