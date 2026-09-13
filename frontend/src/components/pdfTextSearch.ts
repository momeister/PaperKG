import type { PdfAnnotationRect } from "../types";
import { normalizeClientRects } from "./pdfHighlight";

export type TextFragment = { str: string; hasEOL?: boolean; transform?: number[]; width?: number };
export type TextPosition = { item: number; offset: number };
export type TextOccurrence = { start: TextPosition; end: TextPosition; text: string; context: string };
export type PdfSearchHit = TextOccurrence & { page: number; rects: PdfAnnotationRect[] };

/** Each normalized UTF-16 unit retains its original PDF item/character span. */
export function indexPdfText(items: TextFragment[]) {
  let text = "";
  const starts: TextPosition[] = [], ends: TextPosition[] = [];
  const append = (value: string, start: TextPosition, end: TextPosition) => {
    for (const ch of value) {
      if (/\s/u.test(ch) && (!text || text.endsWith(" "))) continue;
      text += /\s/u.test(ch) ? " " : ch;
      for (let j = 0; j < ch.length; j++) { starts.push(start); ends.push(end); }
    }
  };
  items.forEach((item, i) => {
    const prev = items[i - 1];
    if (prev) {
      const hyphenated = prev.hasEOL && /[-\u00ad]$/.test(prev.str);
      if (hyphenated && text.endsWith("-")) { text = text.slice(0, -1); starts.pop(); ends.pop(); }
      // Adjacent fragments on the same baseline can split a word; a physical gap
      // or EOL represents whitespace. Synthetic fixtures may omit geometry.
      const gap = prev.transform && item.transform
        ? Math.abs(item.transform[4] - (prev.transform[4] + (prev.width ?? 0))) > 1 || Math.abs(item.transform[5] - prev.transform[5]) > 1
        : true;
      if (!hyphenated && (prev.hasEOL || gap)) append(" ", { item: i - 1, offset: prev.str.length }, { item: i, offset: 0 });
    }
    let offset = 0;
    for (const ch of item.str) {
      const end = offset + ch.length;
      if (ch !== "\u00ad") append(ch.normalize("NFKC").toLowerCase(), { item: i, offset }, { item: i, offset: end });
      offset = end;
    }
  });
  return { text, starts, ends };
}

export function findTextOccurrences(items: TextFragment[], query: string): TextOccurrence[] {
  const needle = query.normalize("NFKC").toLowerCase().replace(/\u00ad/g, "").replace(/\s+/g, " ").trim();
  if (!needle) return [];
  const indexed = indexPdfText(items);
  const hits: TextOccurrence[] = [];
  for (let pos = indexed.text.indexOf(needle); pos >= 0; pos = indexed.text.indexOf(needle, pos + needle.length)) {
    const start = indexed.starts[pos], end = indexed.ends[pos + needle.length - 1];
    const original = items.slice(start.item, end.item + 1).map((item, i) => item.str.slice(i === 0 ? start.offset : 0, start.item + i === end.item ? end.offset : undefined)).join(" ");
    hits.push({ start, end, text: original, context: indexed.text.slice(Math.max(0, pos - 35), pos + needle.length + 45) });
  }
  return hits;
}

/** DOM Range measures glyph positions including pdf.js font transforms/rotation. */
export function occurrenceRects(hit: TextOccurrence, textDivs: HTMLElement[], surface: HTMLElement): PdfAnnotationRect[] {
  const rects: DOMRect[] = [];
  for (let i = hit.start.item; i <= hit.end.item; i++) {
    const node = textDivs[i]?.firstChild;
    if (!node || node.nodeType !== Node.TEXT_NODE) continue;
    const range = document.createRange();
    range.setStart(node, i === hit.start.item ? hit.start.offset : 0);
    range.setEnd(node, i === hit.end.item ? hit.end.offset : node.textContent?.length ?? 0);
    rects.push(...Array.from(range.getClientRects()));
  }
  return normalizeClientRects(rects, surface.getBoundingClientRect());
}

export function selectionRectsOnPage(selection: Range, surface: HTMLElement): PdfAnnotationRect[] {
  const rects: DOMRect[] = [];
  const walker = document.createTreeWalker(surface.querySelector(".pdf-text-layer") ?? surface, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    if (!selection.intersectsNode(node)) continue;
    const part = document.createRange();
    part.selectNodeContents(node);
    if (node === selection.startContainer) part.setStart(node, selection.startOffset);
    if (node === selection.endContainer) part.setEnd(node, selection.endOffset);
    if (!part.collapsed) rects.push(...Array.from(part.getClientRects()));
  }
  return normalizeClientRects(rects, surface.getBoundingClientRect());
}
