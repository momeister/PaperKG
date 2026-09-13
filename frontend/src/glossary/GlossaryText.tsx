import { Fragment, forwardRef, useCallback, useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type ReactNode, type TextareaHTMLAttributes } from "react";
import { createPortal } from "react-dom";
import type { GlossaryEntry } from "../types";
import { findGlossaryMatches } from "./matching";
import { useGlossary } from "./GlossaryProvider";
import { usePaneEnvironment } from "../workspace/PortablePane";

export function GlossaryText({ text, sourceText = text, sourceOffset = 0, render = value => value }: { text: string; sourceText?: string; sourceOffset?: number; render?: (text: string) => ReactNode }) {
  const { entries, enabled } = useGlossary();
  const hits = useMemo(() => enabled ? findGlossaryMatches(sourceText, entries)
    .filter(hit => hit.start >= sourceOffset && hit.end <= sourceOffset + text.length)
    .map(hit => ({ ...hit, start: hit.start - sourceOffset, end: hit.end - sourceOffset })) : [], [text, sourceText, sourceOffset, entries, enabled]);
  let cursor = 0;
  return <>{hits.map(hit => {
    const prefix = text.slice(cursor, hit.start); cursor = hit.end;
    return <Fragment key={hit.start}>{render(prefix)}<GlossaryTerm entry={hit.entry}>{render(text.slice(hit.start, hit.end))}</GlossaryTerm></Fragment>;
  })}{render(text.slice(cursor))}</>;
}

function Tooltip({ entry, rect, id }: { entry: GlossaryEntry; rect: { left: number; bottom: number }; id?: string }) {
  const { document, window } = usePaneEnvironment();
  return createPortal(<div id={id} role="tooltip" className="glossary-tooltip" style={{ left: Math.max(8, Math.min(rect.left, window.innerWidth - 340)), top: Math.min(rect.bottom + 6, window.innerHeight - 170) }}><strong>{entry.term}</strong><div>{entry.explanation}</div></div>, document.body);
}
function GlossaryTerm({ entry, children }: { entry: GlossaryEntry; children: ReactNode }) {
  const { document, window } = usePaneEnvironment();
  const [rect, setRect] = useState<DOMRect | null>(null);
  const id = useId();
  useEffect(() => {
    if (!rect) return;
    const dismiss = (event: KeyboardEvent) => { if (event.key === "Escape") setRect(null); };
    const scroll = () => setRect(null);
    document.addEventListener("keydown", dismiss);
    window.addEventListener("scroll", scroll, true);
    return () => { document.removeEventListener("keydown", dismiss); window.removeEventListener("scroll", scroll, true); };
  }, [rect, document, window]);
  return <span className="glossary-term" tabIndex={0} aria-describedby={rect ? id : undefined}
    onMouseEnter={e => setRect(e.currentTarget.getBoundingClientRect())} onMouseLeave={() => setRect(null)}
    onFocus={e => setRect(e.currentTarget.getBoundingClientRect())} onBlur={() => setRect(null)}
    onKeyDown={e => { if (e.key === "Escape") { e.stopPropagation(); setRect(null); } }}>
    {children}{rect && <Tooltip entry={entry} rect={rect} id={id} />}
  </span>;
}

type Selection = { text: string; left: number; bottom: number };
export function GlossarySelectionAction({ selection, clear }: { selection: Selection | null; clear: () => void }) {
  const { document, window } = usePaneEnvironment();
  const { open } = useGlossary();
  if (!selection) return null;
  return createPortal(<button type="button" className="button glossary-selection-action" style={{ left: Math.max(8, Math.min(selection.left, window.innerWidth - 270)), top: Math.max(8, Math.min(selection.bottom + 8, window.innerHeight - 50)) }} onMouseDown={e => e.preventDefault()} onClick={() => { open(selection.text); clear(); }}>Zum Wörterbuch hinzufügen</button>, document.body);
}

export function usePreviewGlossarySelection() {
  const { document, window } = usePaneEnvironment();
  const [selection, setSelection] = useState<Selection | null>(null);
  useEffect(() => {
    const clear = () => { if (!window.getSelection()?.toString().trim()) setSelection(null); };
    const escape = (e: KeyboardEvent) => { if (e.key === "Escape") setSelection(null); };
    document.addEventListener("selectionchange", clear);
    document.addEventListener("keydown", escape);
    return () => { document.removeEventListener("selectionchange", clear); document.removeEventListener("keydown", escape); };
  }, [document, window]);
  return {
    capture: (event: { currentTarget: HTMLElement; target: EventTarget }) => {
      if ((event.target as Element)?.tagName === "TEXTAREA") return;
      const selected = window.getSelection();
      if (!selected?.rangeCount || !selected.toString().trim() || !event.currentTarget.contains(selected.anchorNode) || !event.currentTarget.contains(selected.focusNode)) { setSelection(null); return; }
      const rect = selected.getRangeAt(0).getBoundingClientRect();
      setSelection({ text: selected.toString(), left: rect.left, bottom: rect.bottom });
    },
    action: <GlossarySelectionAction selection={selection} clear={() => setSelection(null)} />,
    clear: () => setSelection(null),
  };
}

/** Native textarea stays the input surface. An inert mirror only measures text positions. */
export const GlossaryTextarea = forwardRef<HTMLTextAreaElement, TextareaHTMLAttributes<HTMLTextAreaElement>>(function GlossaryTextarea(props, forwardedRef) {
  const { document, window } = usePaneEnvironment();
  const { entries, enabled } = useGlossary();
  const nodeRef = useRef<HTMLTextAreaElement | null>(null);
  const mirrorRef = useRef<HTMLDivElement>(null);
  const dismissedPosition = useRef<number | null>(null);
  const [text, setText] = useState(String(props.value ?? props.defaultValue ?? ""));
  const [tip, setTip] = useState<{ entry: GlossaryEntry; rect: { left: number; bottom: number } } | null>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const hits = useMemo(() => enabled ? findGlossaryMatches(text, entries) : [], [text, entries, enabled]);
  const ref = useCallback((node: HTMLTextAreaElement | null) => {
    nodeRef.current = node;
    if (typeof forwardedRef === "function") forwardedRef(node); else if (forwardedRef) forwardedRef.current = node;
  }, [forwardedRef]);
  useEffect(() => { if (props.value !== undefined) setText(String(props.value)); }, [props.value]);
  useEffect(() => { setTip(null); }, [hits]);
  useEffect(() => {
    if (!tip) return;
    const dismiss = (event: KeyboardEvent) => {
      if (event.key === "Escape") { dismissedPosition.current = nodeRef.current?.selectionStart ?? null; setTip(null); }
    };
    document.addEventListener("keydown", dismiss);
    return () => document.removeEventListener("keydown", dismiss);
  }, [tip, document]);
  useLayoutEffect(() => {
    const node = nodeRef.current, mirror = mirrorRef.current;
    if (!node || !mirror) return;
    const sync = () => {
      const css = window.getComputedStyle(node);
      for (const property of ["font-family", "font-size", "font-weight", "font-style", "line-height", "letter-spacing", "word-spacing", "text-indent", "text-transform", "tab-size", "padding-top", "padding-right", "padding-bottom", "padding-left", "border-top-width", "border-right-width", "border-bottom-width", "border-left-width", "word-break", "overflow-wrap"]) mirror.style.setProperty(property, css.getPropertyValue(property));
      mirror.style.width = `${node.clientWidth + parseFloat(css.borderLeftWidth) + parseFloat(css.borderRightWidth)}px`;
    };
    sync();
    const observer = new ResizeObserver(sync); observer.observe(node);
    return () => observer.disconnect();
  }, [text, props.className, window]);
  function rects(index: number) {
    const node = nodeRef.current, mirror = mirrorRef.current;
    const span = mirror?.querySelector(`[data-hit="${index}"]`);
    if (!node || !mirror || !span) return [];
    const origin = node.getBoundingClientRect(), base = mirror.getBoundingClientRect();
    return Array.from(span.getClientRects()).map(r => ({ left: r.left - base.left + origin.left - node.scrollLeft, top: r.top - base.top + origin.top - node.scrollTop, right: r.right - base.left + origin.left - node.scrollLeft, bottom: r.bottom - base.top + origin.top - node.scrollTop }));
  }
  function cursorTip(node: HTMLTextAreaElement) {
    if (dismissedPosition.current === node.selectionStart) { setTip(null); return; }
    dismissedPosition.current = null;
    const index = hits.findIndex(hit => node.selectionStart >= hit.start && node.selectionStart < hit.end);
    const rect = rects(index)[0];
    setTip(document.activeElement === node && node.selectionStart === node.selectionEnd && index >= 0 && rect ? { entry: hits[index].entry, rect } : null);
  }
  function capture(node: HTMLTextAreaElement) {
    cursorTip(node);
    if (node.selectionStart !== node.selectionEnd) {
      const rect = node.getBoundingClientRect();
      setSelection({ text: node.value.slice(node.selectionStart, node.selectionEnd), left: rect.left, bottom: rect.top + 38 });
    } else setSelection(null);
  }
  let cursor = 0;
  return <><textarea {...props} ref={ref}
    onChange={e => { setText(e.currentTarget.value); dismissedPosition.current = null; setTip(null); props.onChange?.(e); }}
    onSelect={e => { capture(e.currentTarget); props.onSelect?.(e); }}
    onFocus={e => { cursorTip(e.currentTarget); props.onFocus?.(e); }}
    onBlur={e => { setTip(null); setSelection(null); props.onBlur?.(e); }}
    onScroll={e => { setTip(null); setSelection(null); props.onScroll?.(e); }}
    onKeyDown={e => { if (e.key === "Escape" && (tip || selection)) { dismissedPosition.current = e.currentTarget.selectionStart; setTip(null); setSelection(null); e.preventDefault(); e.stopPropagation(); return; } props.onKeyDown?.(e); }}
    onKeyUp={e => { if (e.key !== "Escape") capture(e.currentTarget); props.onKeyUp?.(e); }}
    onMouseMove={e => {
      if (e.buttons) { setTip(null); return; }
      dismissedPosition.current = null;
      const index = hits.findIndex((_, index) => rects(index).some(rect => e.clientX >= rect.left && e.clientX <= rect.right && e.clientY >= rect.top && e.clientY <= rect.bottom));
      setTip(index < 0 ? null : { entry: hits[index].entry, rect: { left: e.clientX, bottom: e.clientY } });
      props.onMouseMove?.(e);
    }}
    onMouseLeave={e => { setTip(null); props.onMouseLeave?.(e); }}
  /><div ref={mirrorRef} className="glossary-text-mirror" aria-hidden="true">{hits.map((hit, index) => {
    const prefix = text.slice(cursor, hit.start); cursor = hit.end;
    return <Fragment key={hit.start}>{prefix}<span data-hit={index}>{text.slice(hit.start, hit.end)}</span></Fragment>;
  })}{text.slice(cursor)}{"\u200b"}</div>
  {enabled && tip && <Tooltip {...tip} />}
  <GlossarySelectionAction selection={selection} clear={() => setSelection(null)} />
  </>;
});
