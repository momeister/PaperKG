import { useEffect, useRef, useState, type ReactNode } from "react";
import { usePaneEnvironment } from "../workspace/PortablePane";

export function ResizableSection({ storageKey, title, children, initialHeight = 160, initialCollapsed = false, openRequestKey }: {
  storageKey: string; title: string; children: ReactNode; initialHeight?: number; initialCollapsed?: boolean; openRequestKey?: string;
}) {
  const [collapsed, setCollapsed] = useState(() => initialCollapsed || localStorage.getItem(`${storageKey}.collapsed`) === "true");
  const [height, setHeight] = useState(() => Math.max(64, Number(localStorage.getItem(`${storageKey}.height`)) || initialHeight));
  const [available, setAvailable] = useState(400);
  const section = useRef<HTMLElement>(null);
  const previousRequest = useRef(openRequestKey);
  const { window } = usePaneEnvironment();
  useEffect(() => {
    if (previousRequest.current !== openRequestKey) { previousRequest.current = openRequestKey; setCollapsed(false); }
  }, [openRequestKey]);
  useEffect(() => { localStorage.setItem(`${storageKey}.collapsed`, String(collapsed)); }, [storageKey, collapsed]);
  useEffect(() => {
    const parent = section.current?.parentElement;
    if (!parent) return;
    const update = () => setAvailable(Math.max(64, Math.min(400, parent.clientHeight * 0.35)));
    update();
    const observer = new window.ResizeObserver(update); observer.observe(parent);
    return () => observer.disconnect();
  }, [window]);
  const displayedHeight = Math.min(height, available);
  const resize = (value: number) => {
    const next = Math.max(64, Math.min(400, value));
    setHeight(next); localStorage.setItem(`${storageKey}.height`, String(next));
  };
  return <section ref={section} className="resizable-section">
    <button type="button" className="resizable-section-heading" aria-expanded={!collapsed} onClick={() => setCollapsed(v => !v)}>{collapsed ? "▸" : "▾"} {title}</button>
    {!collapsed && <><div className="resizable-section-body" style={{ height: displayedHeight }}>{children}</div>
      <div className="resizable-section-grip" role="separator" tabIndex={0} aria-label={`${title}: Höhe anpassen`} aria-orientation="horizontal" aria-valuenow={displayedHeight} aria-valuemin={64} aria-valuemax={400}
        onKeyDown={e => { if (e.key === "ArrowUp" || e.key === "ArrowDown") { e.preventDefault(); resize(displayedHeight + (e.key === "ArrowUp" ? -16 : 16)); } }}
        onPointerDown={e => { e.preventDefault(); e.currentTarget.setPointerCapture(e.pointerId); e.currentTarget.dataset.startY = String(e.clientY); e.currentTarget.dataset.startHeight = String(displayedHeight); }}
        onPointerMove={e => { if (e.currentTarget.hasPointerCapture(e.pointerId)) resize(Number(e.currentTarget.dataset.startHeight) + e.clientY - Number(e.currentTarget.dataset.startY)); }}
        onPointerUp={e => e.currentTarget.releasePointerCapture(e.pointerId)} />
    </>}
  </section>;
}
