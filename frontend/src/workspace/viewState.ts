/** DOM state belongs to the persistent view, not to its current window. */
export type ViewSnapshot = {
  focus: HTMLElement | null;
  selection: { start: Node; startOffset: number; end: Node; endOffset: number; backward: boolean } | null;
  scroll: Array<{ node: Element; top: number; left: number }>;
  editors: Array<{ node: HTMLInputElement | HTMLTextAreaElement; start: number; end: number; direction: "forward" | "backward" | "none" }>;
  reading: Array<{ root: HTMLElement; page: HTMLElement; fraction: number }>;
};
export function captureView(root: HTMLElement): ViewSnapshot {
  const doc = root.ownerDocument;
  const selection = doc.getSelection();
  const range = selection?.rangeCount ? selection.getRangeAt(0) : null;
  return {
    focus: root.contains(doc.activeElement) ? doc.activeElement as HTMLElement : null,
    // A live Range (including cloneRange) collapses when its nodes are adopted.
    // Keep explicit boundary nodes and rebuild the range in the destination.
    selection: range && root.contains(range.startContainer) && root.contains(range.endContainer)
      ? { start: range.startContainer, startOffset: range.startOffset, end: range.endContainer, endOffset: range.endOffset,
        backward: selection!.anchorNode === range.endContainer && selection!.anchorOffset === range.endOffset }
      : null,
    scroll: [root, ...root.querySelectorAll("*")].filter(n => n.scrollTop || n.scrollLeft)
      .map(node => ({ node, top: node.scrollTop, left: node.scrollLeft })),
    editors: [...root.querySelectorAll<HTMLInputElement | HTMLTextAreaElement>("input,textarea")]
      .filter(node => node.selectionStart !== null)
      .map(node => ({ node, start: node.selectionStart!, end: node.selectionEnd!, direction: node.selectionDirection ?? "none" })),
    reading: [...root.querySelectorAll<HTMLElement>(".pdf-canvas-wrap")].flatMap(root => {
      const top = root.getBoundingClientRect().top;
      const page = [...root.querySelectorAll<HTMLElement>(".pdf-page")].find(page => page.getBoundingClientRect().bottom > top + 16);
      if (!page) return [];
      const rect = page.getBoundingClientRect();
      return [{ root, page, fraction: (top - rect.top) / Math.max(1, rect.height) }];
    })
  };
}
export function restoreView(snapshot: ViewSnapshot, root: HTMLElement): void {
  snapshot.focus?.focus({ preventScroll: true });
  for (const e of snapshot.editors) e.node.setSelectionRange(e.start, e.end, e.direction);
  if (snapshot.selection) {
    const selection = root.ownerDocument.getSelection();
    const { start, startOffset, end, endOffset, backward } = snapshot.selection;
    const range = root.ownerDocument.createRange();
    range.setStart(start, startOffset); range.setEnd(end, endOffset);
    selection?.removeAllRanges();
    selection?.addRange(range);
    if (backward && selection?.setBaseAndExtent) selection.setBaseAndExtent(end, endOffset, start, startOffset);
  }
  for (const s of snapshot.scroll) { s.node.scrollTop = s.top; s.node.scrollLeft = s.left; }
}

/** PDF layout is asynchronous. Retain the page-relative reading position while
 * the adopted canvas is resized, stopping immediately on new user input. */
const readingRestorations = new WeakMap<HTMLElement, () => void>();
export function restoreReadingPosition(snapshot: ViewSnapshot) {
  for (const { root, page, fraction } of snapshot.reading) {
    readingRestorations.get(root)?.();
    const win = root.ownerDocument.defaultView as (Window & typeof globalThis) | null;
    if (!win?.ResizeObserver) continue;
    const restore = () => {
      const rect = page.getBoundingClientRect();
      root.scrollTop += (rect.top - root.getBoundingClientRect().top + fraction * rect.height) / (parseFloat(root.ownerDocument.documentElement.style.zoom) || 1);
    };
    const observer = new win.ResizeObserver(restore);
    const stop = () => { observer.disconnect(); for (const name of ["wheel", "pointerdown", "keydown"]) root.removeEventListener(name, stop); clearTimeout(timer); };
    readingRestorations.set(root, stop);
    const timer = setTimeout(stop, 5000);
    for (const name of ["wheel", "pointerdown", "keydown"]) root.addEventListener(name, stop, { once: true });
    // Earlier pages can finish resizing after this page: their heights move its
    // offset without changing its own size. Observe the whole page stack.
    for (const node of root.querySelectorAll(".pdf-page")) observer.observe(node);
    observer.observe(root);
    restore();
  }
}

type Editor = HTMLInputElement | HTMLTextAreaElement;
type Edit = { value: string; start: number; end: number };
type History = { past: Edit[]; future: Edit[]; before?: Edit };
const histories = new WeakMap<Element, Map<string, History>>();
const editorKey = (node: Editor) => node.dataset.editorKey ?? "";
const readEdit = (node: Editor): Edit => ({ value: node.value, start: node.selectionStart ?? 0, end: node.selectionEnd ?? 0 });
function editorHistory(node: Editor, create = false): History | undefined {
  const byDocument = histories.get(node) ?? new Map<string, History>();
  if (create && !byDocument.has(editorKey(node))) {
    byDocument.set(editorKey(node), { past: [], future: [] }); histories.set(node, byDocument);
  }
  return byDocument.get(editorKey(node));
}
/** Include toolbar/AI transformations in the same history as ordinary typing. */
export function checkpointEditor(node: Editor | null) {
  if (!node?.closest("[data-workspace-history]")) return;
  const h = editorHistory(node, true)!;
  h.past.push(readEdit(node)); h.future = []; h.before = undefined;
  if (h.past.length > 200) h.past.shift();
}
/** Returns true when this editor's history is managed here, even at its boundary. */
export function undoEditor(node: Editor | null, redo = false): boolean {
  if (!node?.closest("[data-workspace-history]")) return false;
  const h = editorHistory(node);
  if (!h) return true;
  const from = redo ? h.future : h.past, to = redo ? h.past : h.future;
  const next = from.pop(); if (!next) return true;
  to.push(readEdit(node));
  Object.getOwnPropertyDescriptor(Object.getPrototypeOf(node), "value")?.set?.call(node, next.value);
  node.dispatchEvent(new Event("input", { bubbles: true }));
  node.setSelectionRange(next.start, next.end);
  return true;
}

/** Browsers keep native undo per document. Keep text history with the editor
 * and its document/session key so adoption and document switches are safe. */
export function retainEditorHistory(root: HTMLElement): () => void {
  root.dataset.workspaceHistory = "true";
  const editor = (event: Event) => {
    const node = event.target as Editor;
    return node && (node.tagName === "TEXTAREA" || node.tagName === "INPUT") && node.selectionStart !== null ? node : null;
  };
  const before = (event: Event) => {
    const node = editor(event); if (!node) return;
    editorHistory(node, true)!.before = readEdit(node);
  };
  const input = (event: Event) => {
    const node = editor(event); if (!node || !event.isTrusted) return;
    const h = editorHistory(node); if (!h?.before || h.before.value === node.value) return;
    h.past.push(h.before); if (h.past.length > 200) h.past.shift();
    h.future = []; h.before = undefined;
  };
  const undo = (event: KeyboardEvent) => {
    const node = editor(event);
    if (!node || !(event.ctrlKey || event.metaKey) || event.altKey || !["z", "y"].includes(event.key.toLowerCase())) return;
    if (undoEditor(node, event.shiftKey || event.key.toLowerCase() === "y")) { event.preventDefault(); event.stopPropagation(); }
  };
  root.addEventListener("beforeinput", before, true);
  root.addEventListener("input", input, true);
  root.addEventListener("keydown", undo, true);
  return () => {
    delete root.dataset.workspaceHistory;
    root.removeEventListener("beforeinput", before, true);
    root.removeEventListener("input", input, true);
    root.removeEventListener("keydown", undo, true);
  };
}
