import { useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { NotebookPen, PanelRightClose } from "lucide-react";

import { NotesSurface } from "../pages/NotesPage";

const WIDTH_KEY = "sciencekg.notesSidePanel.width";

/**
 * Einklappbares Notizen-Panel für Werkstatt & Jupyter: dieselbe NotesSurface wie im
 * Workspace (Projekt-Notizen inkl. Zitate/KI-Threads), als schmale Seitenleiste —
 * damit Notizen beim Coden/Notebook-Arbeiten sichtbar und editierbar bleiben.
 * Die Breite ist über die linke Kante ziehbar (persistiert).
 */
export function NotesSidePanel({ onClose }: { onClose: () => void }) {
  const [width, setWidth] = useState(() => {
    const stored = Number(localStorage.getItem(WIDTH_KEY));
    return Number.isFinite(stored) && stored >= 260 ? stored : 420;
  });
  const panelRef = useRef<HTMLElement | null>(null);

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = width;
    const panel = panelRef.current;
    const max = Math.max(320, window.innerWidth - 420);
    let next = startWidth;
    let frame: number | null = null;
    const apply = () => {
      frame = null;
      if (panel) panel.style.width = `${next}px`;
    };
    const move = (moveEvent: globalThis.PointerEvent) => {
      // Panel sitzt rechts: nach links ziehen = breiter.
      next = Math.min(max, Math.max(260, startWidth + (startX - moveEvent.clientX)));
      if (frame === null) frame = window.requestAnimationFrame(apply);
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      if (frame !== null) window.cancelAnimationFrame(frame);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setWidth(next);
      localStorage.setItem(WIDTH_KEY, String(next));
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  return (
    <aside className="notes-side-panel" ref={panelRef} style={{ width }}>
      <div
        className="notes-side-panel__resize"
        role="separator"
        aria-label="Notizen-Panel Breite anpassen"
        onPointerDown={startResize}
      />
      <div className="notes-side-panel__head">
        <NotebookPen size={14} />
        <strong>Notizen</strong>
        <button className="icon-button" type="button" aria-label="Notizen einklappen" title="Notizen einklappen" onClick={onClose}>
          <PanelRightClose size={15} />
        </button>
      </div>
      <div className="notes-side-panel__body">
        <NotesSurface variant="workspace" />
      </div>
    </aside>
  );
}

export default NotesSidePanel;
