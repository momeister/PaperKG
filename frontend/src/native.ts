// Native (Tauri) integration helpers.
//
// In a webview there is no "new browser tab": clicking an `<a target="_blank">`
// or calling `window.open` does nothing useful. When running inside the Tauri
// shell we instead hand those URLs to the OS via the opener plugin, so external
// web sources open in the default browser and PDF links open in the system's
// default PDF viewer/browser. In a normal browser (pure web mode) none of this
// is installed, so default behaviour is unchanged.

/** True when the page runs inside the Tauri desktop shell. */
export function isTauri(): boolean {
  return typeof window !== "undefined" && "__TAURI_INTERNALS__" in window;
}

type TauriInvoke = (cmd: string, args?: Record<string, unknown>) => Promise<unknown>;

function tauriInvoke(): TauriInvoke | null {
  const w = window as unknown as {
    __TAURI_INTERNALS__?: { invoke?: TauriInvoke };
    __TAURI__?: { core?: { invoke?: TauriInvoke } };
  };
  return w.__TAURI_INTERNALS__?.invoke ?? w.__TAURI__?.core?.invoke ?? null;
}

/**
 * Invoke a Rust command via the official Tauri API (dynamically imported so it
 * stays out of the pure-web bundle). Used by the Code-Werkstatt terminal.
 */
export async function nativeInvoke<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  const { invoke } = await import("@tauri-apps/api/core");
  return invoke<T>(cmd, args);
}

/** Listen to a Rust-emitted event; resolves to an unlisten function. */
export async function nativeListen<T>(
  event: string,
  handler: (payload: T) => void,
): Promise<() => void> {
  const { listen } = await import("@tauri-apps/api/event");
  return listen<T>(event, (e) => handler(e.payload));
}

/** Open a URL in the OS default application (browser / PDF viewer). */
export async function openExternal(target: string): Promise<void> {
  const invoke = tauriInvoke();
  if (!invoke) {
    window.open(target, "_blank", "noopener");
    return;
  }
  try {
    // Custom Rust command registered by the Tauri shell (see src-tauri/src/lib.rs).
    await invoke("open_external", { url: target });
  } catch (err) {
    console.error("openExternal failed", target, err);
  }
}

/**
 * Route external-intent navigations to the OS when running under Tauri:
 *  - clicks on `<a target="_blank">` (external sites, PDF links, …)
 *  - programmatic `window.open(...)` calls for http(s)/file URLs
 * Internal SPA navigation (react-router `<Link>`, no target) is untouched.
 */
export function installNativeExternalLinks(): void {
  if (!isTauri()) return;

  document.addEventListener(
    "click",
    (event) => {
      if (event.defaultPrevented || event.button !== 0) return;
      const start = event.target as Element | null;
      const anchor = start?.closest?.("a[target=\"_blank\"]") as HTMLAnchorElement | null;
      if (!anchor) return;
      const href = anchor.href; // resolved absolute URL
      if (!href || !/^(https?|file):/i.test(href)) return;
      event.preventDefault();
      void openExternal(href);
    },
    true,
  );

  const nativeOpen = window.open.bind(window);
  window.open = ((url?: string | URL, target?: string, features?: string): Window | null => {
    const u = url?.toString() ?? "";
    if (/^(https?|file):/i.test(u)) {
      void openExternal(u);
      return null;
    }
    return nativeOpen(url as string, target as string, features as string);
  }) as typeof window.open;
}
