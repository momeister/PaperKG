import { isTauri, nativeInvoke, nativeListen, openLinkedWorkspaceWindow, openExternal } from "../native";
import { isPaneMessage, PANE_IDS, PANE_PROTOCOL, PANE_TITLES, type PaneId, type PaneMessage } from "./protocol";
import { captureView, restoreReadingPosition, restoreView } from "./viewState";

export const RESTORE_LAYOUT_KEY = "sciencekg.workspace.restoreWindows";
const LAYOUT_KEY = "sciencekg.workspace.detachedWindows";
type Slot = { owner: string; view: HTMLElement; dock: HTMLElement };
type Floating = { window: Window; actionId: string; revision: number };
type Waiter = { resolve: () => void; reject: (error: Error) => void };
type Layout = { read: () => number[]; write: (sizes: number[]) => void };
export type WindowAdapter = {
  enabled: boolean;
  open: (url: URL, name: string) => Window | null;
  command: (pane: PaneId, action: "show" | "destroy" | "drag") => Promise<void>;
};
const nativeAdapter: WindowAdapter = {
  enabled: isTauri(), open: openLinkedWorkspaceWindow,
  command: (pane, action) => nativeInvoke("workspace_window_action", { pane, action })
};

/** The only workspace window controller. React, API calls and editor nodes stay
 * in the main JS realm; related webviews host views, never a second app root. */
export class WorkspaceWindows {
  private slots = new Map<PaneId, Map<string, Slot>>();
  private floating = new Map<PaneId, Floating>();
  private busy = new Map<PaneId, Promise<void>>();
  private waiters = new Map<string, Waiter>();
  private listeners = new Set<() => void>();
  private revision = 0;
  private owner = "";
  private failures = new Map<PaneId, string>();
  private detached = new Set<PaneId>();
  private layouts = new Map<string, Layout>();
  private dockedLayouts = new Map<string, number[]>();
  private disposed = false;
  private inputLocked = false;
  constructor(readonly adapter = nativeAdapter, readonly timeoutMs = 8000) {}
  subscribe = (listener: () => void) => { this.listeners.add(listener); return () => { this.listeners.delete(listener); }; };
  snapshot = () => this.revision;
  private changed() { this.revision++; this.listeners.forEach(fn => fn()); }
  isDetached(pane: PaneId) { return this.detached.has(pane); }
  isBusy(pane: PaneId) { return this.busy.has(pane); }
  canSaveDockedLayout() { return this.detached.size === 0 && this.busy.size === 0; }
  hasActiveWindows() { return this.detached.size > 0 || this.busy.size > 0; }
  setInputLocked(locked: boolean) {
    this.inputLocked = locked;
    for (const slots of this.slots.values()) for (const slot of slots.values()) slot.view.inert = locked;
  }
  error(pane: PaneId) { return this.failures.get(pane); }
  private slot(pane: PaneId) { return this.slots.get(pane)?.get(this.owner); }
  registerLayout(owner: string, layout: Layout) {
    this.layouts.set(owner, layout);
    return () => { if (this.layouts.get(owner) === layout) this.layouts.delete(owner); };
  }
  register(pane: PaneId, owner: string, view: HTMLElement, dock: HTMLElement) {
    const slots = this.slots.get(pane) ?? new Map<string, Slot>();
    const slot = { owner, view, dock };
    slots.set(owner, slot); this.slots.set(pane, slots);
    view.inert = this.inputLocked;
    dock.append(view);
    if (owner === this.owner && this.detached.has(pane)) {
      this.place(slot, this.floating.get(pane)?.window.document.getElementById("workspace-pane-root") ?? dock);
      this.changed();
    }
    return () => { if (slots.get(owner) === slot) slots.delete(owner); view.remove(); };
  }
  setOwner(owner: string) {
    if (this.owner === owner) return;
    for (const pane of PANE_IDS) {
      const slot = this.slot(pane);
      if (slot) this.place(slot, slot.dock);
    }
    this.owner = owner;
    if (this.detached.size && !this.dockedLayouts.has(owner)) {
      const layout = this.layouts.get(owner)?.read(); if (layout) this.dockedLayouts.set(owner, layout);
    }
    for (const pane of PANE_IDS) {
      const slot = this.slot(pane), floating = this.floating.get(pane);
      if (slot && floating && this.detached.has(pane)) this.place(slot, floating.window.document.getElementById("workspace-pane-root")!);
    }
    this.changed();
  }
  private place(slot: Slot, destination: HTMLElement) {
    if (slot.view.parentElement === destination) return;
    const snapshot = captureView(slot.view);
    destination.appendChild(slot.view); // same node / same React portal target
    restoreView(snapshot, slot.view);
    restoreReadingPosition(snapshot);
  }
  private wait(key: string): Promise<void> {
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.waiters.delete(key); reject(new Error("Fensterübergabe wurde nicht bestätigt.")); }, this.timeoutMs);
      this.waiters.set(key, {
        resolve: () => { clearTimeout(timer); this.waiters.delete(key); resolve(); },
        reject: error => { clearTimeout(timer); this.waiters.delete(key); reject(error); }
      });
    });
  }
  receive = (event: MessageEvent) => {
    if (event.origin !== window.location.origin || !isPaneMessage(event.data)) return;
    const message = event.data, floating = this.floating.get(message.pane);
    if (!floating || event.source !== floating.window || message.actionId !== floating.actionId) return;
    if (message.type === "ready" && message.revision === 0) this.waiters.get(`${message.actionId}:ready`)?.resolve();
    if (message.type === "committed" && message.revision === floating.revision) this.waiters.get(`${message.actionId}:committed`)?.resolve();
    if (message.type === "dock" && message.revision === floating.revision) void this.dock(message.pane);
    if (message.type === "resync" && this.detached.has(message.pane) && !this.busy.has(message.pane)) {
      // A reloaded related document has no independent state to merge. Reattach
      // the complete, current view from the authoritative controller.
      void this.resync(message.pane);
    }
  };
  private async resync(pane: PaneId) {
    try {
      const floating = this.floating.get(pane), slot = this.slot(pane);
      if (!floating || !slot) return;
      await this.prepareDocument(floating.window.document, pane);
      this.place(slot, floating.window.document.getElementById("workspace-pane-root")!);
      this.changed();
    } catch { await this.dock(pane); }
  }
  private async prepareDocument(doc: Document, pane: PaneId) {
    doc.title = `${PANE_TITLES[pane]} — ScienceKG`;
    if (!doc.getElementById("workspace-pane-root")) throw new Error("Zielansicht fehlt.");
    if (doc.head.querySelector("[data-workspace-styles]")) return;
    const styles = [...document.head.querySelectorAll<HTMLLinkElement | HTMLStyleElement>('link[rel="stylesheet"],style')];
    await Promise.all(styles.map(source => new Promise<void>((resolve, reject) => {
      const clone = source.cloneNode(true) as HTMLLinkElement | HTMLStyleElement;
      clone.setAttribute("data-workspace-styles", "");
      if (source.tagName === "LINK") {
        (clone as HTMLLinkElement).href = (source as HTMLLinkElement).href;
        const timer = setTimeout(() => reject(new Error("Fensterdarstellung konnte nicht geladen werden.")), this.timeoutMs);
        clone.onload = () => { clearTimeout(timer); resolve(); };
        clone.onerror = () => { clearTimeout(timer); reject(new Error("Fensterdarstellung konnte nicht geladen werden.")); };
      }
      doc.head.append(clone);
      if (source.tagName !== "LINK") resolve();
    })));
    this.syncAppearance();
    doc.addEventListener("click", event => {
      const a = (event.target as Element)?.closest?.('a[target="_blank"]') as HTMLAnchorElement | null;
      if (a && /^(https?|file):/i.test(a.href)) { event.preventDefault(); void openExternal(a.href); }
    }, true);
  }
  syncAppearance = () => {
    for (const floating of this.floating.values()) {
      try {
        const root = floating.window.document.documentElement;
        for (const name of ["data-theme", "data-scheme", "style"]) {
          const value = document.documentElement.getAttribute(name);
          if (value !== null) root.setAttribute(name, value); else root.removeAttribute(name);
        }
        root.style.setProperty("--workspace-font-scale", document.documentElement.style.zoom || "1");
      } catch { /* navigation recovery goes through resync */ }
    }
  };
  private persist() { localStorage.setItem(LAYOUT_KEY, JSON.stringify([...this.detached])); }
  detach(pane: PaneId): Promise<void> {
    if (this.inputLocked) return Promise.resolve();
    if (!this.adapter.enabled) return Promise.resolve();
    if (this.busy.has(pane)) return this.busy.get(pane)!;
    if (this.detached.has(pane)) return this.show(pane);
    const operation = this.detachNow(pane).catch(error => { this.failures.set(pane, String(error instanceof Error ? error.message : error)); })
      .finally(() => { this.busy.delete(pane); this.changed(); });
    this.busy.set(pane, operation); this.changed(); return operation;
  }
  private async detachNow(pane: PaneId) {
    const slot = this.slot(pane); if (!slot) throw new Error("Bereich ist noch nicht bereit.");
    if (!this.dockedLayouts.has(this.owner)) {
      const sizes = this.layouts.get(this.owner)?.read(); if (sizes) this.dockedLayouts.set(this.owner, sizes);
    }
    this.failures.delete(pane);
    const actionId = crypto.randomUUID(), revision = this.revision + 1;
    const url = new URL("/workspace-pane.html", window.location.href);
    url.hash = ""; url.search = new URLSearchParams({ pane, actionId }).toString();
    const ready = this.wait(`${actionId}:ready`);
    // Attach a rejection handler before open can throw or return null.
    void ready.catch(() => {});
    let floating: Floating | undefined;
    try {
      const child = this.adapter.open(url, `workspace-${pane}`);
      if (!child) throw new Error("Zusatzfenster konnte nicht geöffnet werden.");
      floating = { window: child, actionId, revision }; this.floating.set(pane, floating);
      await ready;
      if (this.disposed) throw new Error("Arbeitsplatz wurde beendet.");
      await this.prepareDocument(child.document, pane);
      // A project may have changed while the webview was starting.
      const current = this.slot(pane); if (!current) throw new Error("Bereich nicht mehr verfügbar.");
      current.view.inert = true;
      const snapshot = captureView(current.view);
      try {
        this.place(current, child.document.getElementById("workspace-pane-root")!);
        this.changed();
        const committed = this.wait(`${actionId}:committed`);
        const message: PaneMessage = { protocol: PANE_PROTOCOL, pane, actionId, revision, type: "commit" };
        child.postMessage(message, window.location.origin);
        await committed;
        await this.adapter.command(pane, "show");
        this.detached.add(pane); this.persist();
        const activeSlot = this.slot(pane);
        if (activeSlot && activeSlot !== current) this.place(activeSlot, child.document.getElementById("workspace-pane-root")!);
        restoreView(snapshot, current.view);
        restoreReadingPosition(snapshot);
      } finally { current.view.inert = this.inputLocked; }
    } catch (error) {
      for (const slot of this.slots.get(pane)?.values() ?? []) this.place(slot, slot.dock);
      this.detached.delete(pane);
      this.floating.delete(pane);
      if (floating) await this.adapter.command(pane, "destroy").catch(() => {});
      throw error;
    } finally { this.waiters.get(`${actionId}:ready`)?.resolve(); }
  }
  async show(pane: PaneId) {
    try { await this.adapter.command(pane, "show"); }
    catch (error) { this.failures.set(pane, String(error)); this.changed(); }
  }
  dock(pane: PaneId): Promise<void> {
    const active = this.busy.get(pane);
    if (active) return active.then(() => this.dock(pane));
    const operation = this.dockNow(pane).finally(() => { this.busy.delete(pane); this.changed(); });
    this.busy.set(pane, operation); this.changed(); return operation;
  }
  private async dockNow(pane: PaneId) {
    const floating = this.floating.get(pane); if (!floating) return;
    try {
      // All editor work already belongs to the main controller. Moving the
      // current nodes back is the acknowledgement; destruction happens last.
      for (const slot of this.slots.get(pane)?.values() ?? []) this.place(slot, slot.dock);
      this.detached.delete(pane); this.changed(); this.persist();
      if (!this.detached.size) {
        // Individual collapse operations redistribute space. Restore the whole
        // original layout once all views are back, including simultaneous docks.
        const layouts = [...this.dockedLayouts]; this.dockedLayouts.clear();
        requestAnimationFrame(() => { for (const [owner, sizes] of layouts) this.layouts.get(owner)?.write(sizes); });
      }
      await this.adapter.command(pane, "destroy");
      this.floating.delete(pane); this.failures.delete(pane);
    } catch (error) { this.failures.set(pane, String(error)); }
    this.changed();
  }
  drag(pane: PaneId) { void this.adapter.command(pane, "drag").catch(error => { this.failures.set(pane, String(error)); this.changed(); }); }
  async restore() {
    if (!this.adapter.enabled || localStorage.getItem(RESTORE_LAYOUT_KEY) !== "true") return;
    try {
      const stored: unknown = JSON.parse(localStorage.getItem(LAYOUT_KEY) ?? "[]");
      if (Array.isArray(stored)) await Promise.all(PANE_IDS.filter(p => stored.includes(p)).map(p => this.detach(p)));
    } catch (error) { this.failures.set("navigator", String(error)); this.changed(); }
  }
  start() {
    this.disposed = false;
    window.addEventListener("message", this.receive);
    const observer = new MutationObserver(this.syncAppearance);
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-theme", "data-scheme", "style"] });
    let unlisten: (() => void) | undefined, stopped = false;
    if (this.adapter === nativeAdapter && this.adapter.enabled) void nativeListen<PaneId>("workspace-dock-request", pane => { if (PANE_IDS.includes(pane)) void this.dock(pane); })
      .then(off => { if (stopped) off(); else unlisten = off; })
      .catch(error => { this.failures.set("navigator", String(error)); this.changed(); });
    const timer = setInterval(() => {
      for (const [pane, floating] of this.floating) if (floating.window.closed && !this.busy.has(pane)) void this.dock(pane);
    }, 1000);
    return () => { stopped = true; this.disposed = true; unlisten?.(); clearInterval(timer); observer.disconnect(); window.removeEventListener("message", this.receive); };
  }
}
