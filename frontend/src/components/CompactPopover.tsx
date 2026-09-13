import { useEffect, useId, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { usePaneEnvironment } from "../workspace/PortablePane";

/** A small, nonmodal settings surface, including in adopted workspace views. */
export function CompactPopover({ label, children, className = "", open: controlled, onOpenChange }: {
  label: ReactNode; children: ReactNode; className?: string; open?: boolean; onOpenChange?: (open: boolean) => void;
}) {
  const [localOpen, setLocalOpen] = useState(false);
  const open = controlled ?? localOpen;
  const setOpen = (value: boolean) => { setLocalOpen(value); onOpenChange?.(value); };
  const trigger = useRef<HTMLButtonElement>(null);
  const panel = useRef<HTMLDivElement>(null);
  const id = useId();
  const { document, window } = usePaneEnvironment();
  useLayoutEffect(() => {
    if (!open) return;
    const element = panel.current;
    const position = () => {
      if (!trigger.current || !panel.current) return;
      const rect = trigger.current.getBoundingClientRect();
      const scale = parseFloat(window.getComputedStyle(document.documentElement).zoom) || 1;
      const width = window.innerWidth / scale, height = window.innerHeight / scale;
      panel.current.style.maxWidth = `${width - 16}px`;
      panel.current.style.maxHeight = `${height - 16}px`;
      panel.current.style.left = `${Math.max(8, Math.min(rect.left / scale, width - panel.current.offsetWidth - 8))}px`;
      panel.current.style.top = `${Math.max(8, Math.min(rect.bottom / scale + 4, height - panel.current.offsetHeight - 8))}px`;
    };
    position();
    panel.current?.focus({ preventScroll: true });
    const observer = new window.ResizeObserver(position);
    if (panel.current) observer.observe(panel.current);
    window.addEventListener("resize", position);
    return () => {
      observer.disconnect(); window.removeEventListener("resize", position);
      if (element?.contains(document.activeElement)) trigger.current?.focus({ preventScroll: true });
    };
  }, [open, document, window]);
  useEffect(() => {
    if (!open) return;
    const dismiss = (event: PointerEvent) => {
      if (!panel.current?.contains(event.target as Node) && !trigger.current?.contains(event.target as Node)) setOpen(false);
    };
    const escape = (event: KeyboardEvent) => {
      if (event.key !== "Escape" || document.querySelector("dialog[open]")) return;
      event.preventDefault(); event.stopPropagation(); setOpen(false); trigger.current?.focus({ preventScroll: true });
    };
    document.addEventListener("pointerdown", dismiss);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("pointerdown", dismiss); document.removeEventListener("keydown", escape); };
  }, [open, document]);
  return <span className={`compact-popover ${className}`}>
    <button ref={trigger} type="button" className="button button-compact" aria-expanded={open} aria-controls={open ? id : undefined} onClick={() => setOpen(!open)}>{label}</button>
    {open && createPortal(<div id={id} ref={panel} className="compact-popover-panel" tabIndex={-1}>{children}</div>, document.body)}
  </span>;
}
