import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useRef, useState, useSyncExternalStore, type ReactNode, type RefObject } from "react";
import { createPortal } from "react-dom";
import type { ImperativePanelHandle } from "react-resizable-panels";
import { ExternalLink, GripVertical, PanelLeftClose } from "lucide-react";
import { WorkspaceWindows } from "./windows";
import { PANE_TITLES, type PaneId } from "./protocol";
import { retainEditorHistory } from "./viewState";

export const WorkspaceWindowsContext = createContext<WorkspaceWindows | null>(null);
export const WorkspaceOwnerContext = createContext("");
const ViewDocumentContext = createContext<{ view: HTMLElement; document: Document } | null>(null);

export const PaneVisibilityContext = createContext(true);
const PaneToolbarContext = createContext<HTMLElement | null>(null);

/** Share a single title/action row with the window controls. */
export function PaneHeader({ children }: { children: ReactNode }) {
  const target = useContext(PaneToolbarContext);
  const visible = useContext(PaneVisibilityContext);
  if (!visible) return null;
  return target ? createPortal(children, target) : <>{children}</>;
}

/** Always use the document hosting the view for selection, focus and listeners.
 * API effects still run in the main JS realm and are independent of this value. */
export function usePaneEnvironment() {
  const environment = useContext(ViewDocumentContext);
  const document = environment?.view.ownerDocument ?? globalThis.document;
  return { document, window: (document.defaultView ?? globalThis.window) as Window & typeof globalThis };
}
export function useWorkspaceWindows() {
  const manager = useContext(WorkspaceWindowsContext);
  useSyncExternalStore(manager?.subscribe ?? (() => () => {}), manager?.snapshot ?? (() => 0));
  return manager;
}

/** A stable portal target is moved between documents. React never remounts its
 * children, so hooks, refs, undo history and ongoing promises retain ownership. */
export function PortablePane({ pane, children, panelRef }: {
  pane: PaneId; children: ReactNode; panelRef: RefObject<ImperativePanelHandle>;
}) {
  const manager = useWorkspaceWindows();
  const owner = useContext(WorkspaceOwnerContext);
  const [view] = useState(() => {
    const node = document.createElement("div"); node.className = "workspace-portable-view";
    node.dataset.pane = pane; return node;
  });
  const dock = useRef<HTMLDivElement>(null);
  const [headingTarget, setHeadingTarget] = useState<HTMLDivElement | null>(null);
  const environment = useMemo(() => ({ view, document: view.ownerDocument }), [view, view.ownerDocument]);
  const previousSize = useRef<number | null>(null);
  const detached = manager?.isDetached(pane) ?? false;
  const busy = manager?.isBusy(pane) ?? false;
  const error = manager?.error(pane);
  useLayoutEffect(() => {
    if (!dock.current) return;
    if (!manager) { dock.current.append(view); return () => view.remove(); }
    return manager.register(pane, owner, view, dock.current);
  }, [manager, owner, pane, view]);
  useEffect(() => retainEditorHistory(view), [view]);
  useLayoutEffect(() => {
    const panel = panelRef.current;
    if (!panel) return;
    if (detached && previousSize.current === null) {
      previousSize.current = panel.getSize(); panel.collapse();
    } else if (!detached && previousSize.current !== null) {
      const size = previousSize.current; previousSize.current = null; panel.resize(size);
    }
  }, [detached, panelRef]);
  const dragStart = useRef<{ x: number; y: number } | null>(null);
  const toolbar = manager?.adapter.enabled ? (
    <div className="workspace-window-tools">
      <button type="button" className="workspace-pane-grip" aria-label={`${PANE_TITLES[pane]} ziehen`}
        disabled={busy}
        onPointerDown={event => {
          if (event.button !== 0) return;
          dragStart.current = { x: event.clientX, y: event.clientY };
          event.currentTarget.setPointerCapture(event.pointerId);
        }}
        onPointerMove={event => {
          const start = dragStart.current;
          if (!start || Math.hypot(event.clientX - start.x, event.clientY - start.y) < 12) return;
          dragStart.current = null;
          event.currentTarget.releasePointerCapture(event.pointerId);
          if (detached) manager.drag(pane); else void manager.detach(pane);
        }}
        onPointerUp={() => { dragStart.current = null; }} onPointerCancel={() => { dragStart.current = null; }}>
        <GripVertical size={14} /><span>{PANE_TITLES[pane]}</span>
      </button>
      <div ref={setHeadingTarget} className="workspace-window-heading" />
      <button type="button" className="icon-button" disabled={busy}
        draggable={detached}
        onDragStart={event => {
          if (!detached) { event.preventDefault(); return; }
          event.dataTransfer.setData("application/x-sciencekg-pane", pane);
          event.dataTransfer.effectAllowed = "move";
        }}
        aria-label={`${PANE_TITLES[pane]} ${detached ? "andocken" : "loslösen"}`}
        title={detached ? "Andocken" : "In eigenem Fenster öffnen"}
        onClick={() => { void (detached ? manager.dock(pane) : manager.detach(pane)); }}>
        {detached ? <PanelLeftClose size={15} /> : <ExternalLink size={15} />}
      </button>
    </div>
  ) : null;
  return <>
    <div ref={dock} className="workspace-dock-slot" data-pane={pane} hidden={detached} />
    {detached ? <div className="workspace-dock-target" aria-label={`${PANE_TITLES[pane]} Andockfläche`}
      onDragOver={event => {
        if (event.dataTransfer.types.includes("application/x-sciencekg-pane")) { event.preventDefault(); event.dataTransfer.dropEffect = "move"; }
      }}
      onDrop={event => {
        if (event.dataTransfer.getData("application/x-sciencekg-pane") === pane) { event.preventDefault(); void manager?.dock(pane); }
      }}>
      <strong>{PANE_TITLES[pane]}</strong>
      <button type="button" onClick={() => { void manager?.show(pane); }} title="Fenster anzeigen"><ExternalLink size={16} /><span>Fenster anzeigen</span></button>
      <button type="button" onClick={() => { void manager?.dock(pane); }} title="Andocken"><PanelLeftClose size={16} /><span>Andocken</span></button>
    </div> : null}
    {error ? <div role="alert" className="workspace-window-error">{error}</div> : null}
    {createPortal(<ViewDocumentContext.Provider value={environment}>
      {toolbar}
      <PaneToolbarContext.Provider value={headingTarget}><div className="workspace-portable-content">{children}</div></PaneToolbarContext.Provider>
    </ViewDocumentContext.Provider>, view)}
  </>;
}
