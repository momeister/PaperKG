// Pure PDF-Highlight-Logik (Matching, Box-Geometrie, Query-Bau) —
// aus PdfPane.tsx extrahiert; PdfPane re-exportiert die oeffentlichen Namen.
import * as pdfjs from "pdfjs-dist";

import type { PdfAnnotationRect, VerificationEvidence } from "../types";

export type HighlightBox = {
  id: string;
  evidenceIndex: number;
  colorIndex: number;
  left: number;
  top: number;
  width: number;
  height: number;
};

export type PageMatch = {
  pageNumber: number;
  score: number;
  exact: boolean;
  matchedText: string;
  boxes: HighlightBox[];
  querySignature: string;
};


export type HighlightQuery = {
  phrases: string[];
  terms: string[];
};

type IndexedTextItem = {
  item: unknown;
  index: number;
  text: string;
  start: number;
  end: number;
};

const ANCHOR_STOPWORDS = new Set([
  "about",
  "also",
  "and",
  "are",
  "based",
  "between",
  "clinical",
  "clinicians",
  "decision",
  "during",
  "from",
  "have",
  "into",
  "paper",
  "primary",
  "study",
  "support",
  "that",
  "the",
  "their",
  "this",
  "through",
  "used",
  "using",
  "with",
  "without"
]);


type ClientRectLike = { left: number; top: number; width: number; height: number };

function clamp01(value: number) {
  return Math.max(0, Math.min(1, value));
}

// Convert absolute client rects to page-surface-relative 0..1 rects (zoom-independent),
// merging adjacent fragments on the same text line. Pure + DOM-free so it is unit-testable.
export function normalizeClientRects(clientRects: ClientRectLike[], surface: ClientRectLike): PdfAnnotationRect[] {
  const w = surface.width;
  const h = surface.height;
  if (w <= 0 || h <= 0) return [];
  const normalized: PdfAnnotationRect[] = [];
  for (const r of clientRects) {
    if (r.width <= 0 || r.height <= 0) continue;
    const x = (r.left - surface.left) / w;
    const y = (r.top - surface.top) / h;
    const width = r.width / w;
    const height = r.height / h;
    if (x > 1 || y > 1 || x + width < 0 || y + height < 0) continue; // fully outside
    const cx = clamp01(x);
    const cy = clamp01(y);
    normalized.push({ x: cx, y: cy, width: Math.min(width, 1 - cx), height: Math.min(height, 1 - cy) });
  }
  return mergeRowRects(normalized);
}

export function clientRectsToPageRects(range: Range, surfaceEl: HTMLElement): PdfAnnotationRect[] {
  return normalizeClientRects(Array.from(range.getClientRects()), surfaceEl.getBoundingClientRect());
}

function mergeRowRects(rects: PdfAnnotationRect[]): PdfAnnotationRect[] {
  const sorted = [...rects].sort((a, b) => a.y - b.y || a.x - b.x);
  const out: PdfAnnotationRect[] = [];
  for (const r of sorted) {
    const prev = out[out.length - 1];
    const sameRow =
      prev !== undefined &&
      Math.abs(prev.y + prev.height / 2 - (r.y + r.height / 2)) < Math.min(prev.height, r.height) * 0.7 + 0.002;
    if (prev !== undefined && sameRow && r.x <= prev.x + prev.width + 0.01) {
      const right = Math.max(prev.x + prev.width, r.x + r.width);
      prev.x = Math.min(prev.x, r.x);
      prev.width = right - prev.x;
      prev.y = Math.min(prev.y, r.y);
      prev.height = Math.max(prev.height, r.height);
      continue;
    }
    out.push({ ...r });
  }
  return out;
}

function textItemBox(item: IndexedTextItem, viewport: any, evidenceIndex: number, colorIndex = evidenceIndex, rangeStart = 0, rangeEnd = item.text.length): HighlightBox | null {
  const textItem = item.item as { transform?: number[]; width?: number; height?: number; str?: string };
  if (!textItem.transform) {
    return null;
  }
  const transform = pdfjs.Util.transform(viewport.transform, textItem.transform);
  const height = Math.max(8, Math.hypot(transform[2], transform[3]) || Number(textItem.height) || 10);
  const fullWidth = Math.max(10, Number(textItem.width || String(textItem.str ?? "").length * 5) * viewport.scale);
  const textLength = Math.max(1, item.text.length);
  const safeStart = Math.max(0, Math.min(textLength, rangeStart));
  const safeEnd = Math.max(safeStart + 1, Math.min(textLength, rangeEnd));
  const startRatio = safeStart / textLength;
  const endRatio = safeEnd / textLength;
  const width = Math.max(4, fullWidth * (endRatio - startRatio));
  const left = transform[4] + fullWidth * startRatio;
  const top = transform[5] - height;
  return {
    id: `${evidenceIndex}-${item.index}-${safeStart}-${safeEnd}-${left}-${top}`,
    evidenceIndex,
    colorIndex,
    left: Math.max(0, left - 1),
    top: Math.max(0, top - 2),
    width: width + 3,
    height: height + 4
  };
}

export function findPageMatch(textItems: unknown[], query: HighlightQuery, viewport: any, evidenceIndex: number, colorIndex = evidenceIndex): Omit<PageMatch, "pageNumber" | "querySignature"> | null {
  const indexed = indexTextItems(textItems);
  if (!indexed.text || (!query.phrases.length && !query.terms.length)) {
    return null;
  }

  const phraseMatch = bestPhraseMatch(indexed.items, indexed.text, query.phrases, viewport, evidenceIndex, colorIndex);
  if (phraseMatch) {
    return phraseMatch;
  }

  return bestTermWindowMatch(indexed.items, indexed.text, query.terms, viewport, evidenceIndex, colorIndex);
}

function indexTextItems(items: unknown[]): { items: IndexedTextItem[]; text: string } {
  const indexed: IndexedTextItem[] = [];
  let text = "";
  items.forEach((item, index) => {
    const itemText = normalizeText((item as any).str ?? "");
    if (!itemText) {
      return;
    }
    if (text) {
      text += " ";
    }
    const start = text.length;
    text += itemText;
    indexed.push({ item, index, text: itemText, start, end: text.length });
  });
  return { items: indexed, text };
}

function bestPhraseMatch(
  items: IndexedTextItem[],
  pageText: string,
  phrases: string[],
  viewport: any,
  evidenceIndex: number,
  colorIndex = evidenceIndex
): Omit<PageMatch, "pageNumber" | "querySignature"> | null {
  // Union matching: a multi-sentence excerpt rarely matches the page text as ONE phrase
  // (hyphenation/line breaks split it), but its sentence chunks do. Highlighting only the
  // single best chunk marked just part of the passage — collect every matching phrase
  // span and mark them all.
  type PhraseSpan = { start: number; end: number; text: string };
  const spans: PhraseSpan[] = [];
  let longestPhraseLength = 0;
  for (const phrase of phrases) {
    const normalizedPhrase = normalizeText(phrase);
    if (!normalizedPhrase) {
      continue;
    }
    longestPhraseLength = Math.max(longestPhraseLength, normalizedPhrase.length);
    let position = pageText.indexOf(normalizedPhrase);
    while (position >= 0) {
      const end = position + normalizedPhrase.length;
      if (
        textRangeHasBoundary(pageText, position, end) &&
        // Sub-phrases of an already covered span add nothing (phrases arrive longest-first).
        !spans.some((span) => position >= span.start && end <= span.end)
      ) {
        spans.push({ start: position, end, text: compactText(phrase) });
      }
      position = pageText.indexOf(normalizedPhrase, end);
    }
  }
  if (!spans.length) {
    return null;
  }

  spans.sort((left, right) => left.start - right.start || right.end - left.end);
  const merged: PhraseSpan[] = [];
  for (const span of spans) {
    const previous = merged[merged.length - 1];
    if (previous && span.start <= previous.end + 1) {
      if (span.end > previous.end) {
        previous.text = `${previous.text} ${span.text}`;
        previous.end = span.end;
      }
      continue;
    }
    merged.push({ ...span });
  }

  const boxes = normalizeHighlightBoxes(
    merged.flatMap((span) => boxesForTextRange(items, span.start, span.end, viewport, evidenceIndex, colorIndex))
  ).slice(0, 36);
  if (!boxes.length) {
    return null;
  }
  const coveredLength = merged.reduce((sum, span) => sum + (span.end - span.start), 0);
  // "exact" gates early scrolling and cross-page rendering: require the match to cover a
  // meaningful share of the queried passage so a stray short chunk on another page does
  // not count as the passage itself.
  const exact = coveredLength >= Math.min(200, Math.max(40, Math.round(longestPhraseLength * 0.55)));
  return {
    score: (exact ? 10000 : 5000) + coveredLength * 2 - boxes.length,
    exact,
    matchedText: merged.map((span) => span.text).join(" … "),
    boxes
  };
}

function bestTermWindowMatch(
  items: IndexedTextItem[],
  pageText: string,
  terms: string[],
  viewport: any,
  evidenceIndex: number,
  colorIndex: number
): Omit<PageMatch, "pageNumber" | "querySignature"> | null {
  if (!terms.length) {
    return null;
  }
  const occurrences = termOccurrences(pageText, terms);
  const matchedTerms = new Set(occurrences.map((item) => item.term));
  const requiredHits = terms.length <= 4 ? terms.length : Math.min(5, Math.max(3, Math.ceil(terms.length * 0.35)));
  if (matchedTerms.size < requiredHits) {
    return null;
  }

  const maxWindow = 280;
  let bestWindow: { start: number; end: number; score: number; occurrences: typeof occurrences } | null = null;
  const ordered = [...occurrences].sort((left, right) => left.start - right.start || left.end - right.end);
  for (let left = 0; left < ordered.length; left += 1) {
    const windowOccurrences: typeof occurrences = [];
    for (let right = left; right < ordered.length; right += 1) {
      const start = ordered[left].start;
      const end = ordered[right].end;
      if (end - start > maxWindow) {
        break;
      }
      windowOccurrences.push(ordered[right]);
      const distinct = new Set(windowOccurrences.map((item) => item.term));
      if (distinct.size < requiredHits) {
        continue;
      }
      const strongHits = windowOccurrences.filter((item) => isStrongFallbackTerm(item.term)).length;
      if (!strongHits && distinct.size < Math.max(4, requiredHits + 1)) {
        continue;
      }
      const span = Math.max(1, end - start);
      const score = distinct.size * 100 + strongHits * 20 - span / 4;
      if (!bestWindow || score > bestWindow.score) {
        bestWindow = { start, end, score, occurrences: [...windowOccurrences] };
      }
    }
  }
  if (!bestWindow) {
    return null;
  }
  const boxes = normalizeHighlightBoxes(boxesForTextRange(items, bestWindow.start, bestWindow.end, viewport, evidenceIndex, colorIndex)).slice(0, 18);
  if (!boxes.length) {
    return null;
  }
  return {
    score: bestWindow.score,
    exact: false,
    matchedText: pageText.slice(bestWindow.start, bestWindow.end).trim(),
    boxes
  };
}

function boxesForTextRange(items: IndexedTextItem[], start: number, end: number, viewport: any, evidenceIndex: number, colorIndex: number): HighlightBox[] {
  const boxes: HighlightBox[] = [];
  for (const item of items) {
    if (item.end <= start || item.start >= end) {
      continue;
    }
    const rangeStart = Math.max(0, start - item.start);
    const rangeEnd = Math.min(item.text.length, end - item.start);
    const box = textItemBox(item, viewport, evidenceIndex, colorIndex, rangeStart, rangeEnd);
    if (box) {
      boxes.push(box);
    }
  }
  return uniqueBoxes(boxes);
}

function termOccurrences(pageText: string, terms: string[]) {
  return Array.from(new Set(terms))
    .flatMap((term) => findTermOccurrences(pageText, term).slice(0, 8).map((start) => ({ term, start, end: start + term.length })))
    .sort((left, right) => left.start - right.start || left.end - right.end);
}

function findTermOccurrences(text: string, term: string) {
  const positions: number[] = [];
  if (!term) {
    return positions;
  }
  let position = text.indexOf(term);
  while (position >= 0) {
    const end = position + term.length;
    if (textRangeHasBoundary(text, position, end)) {
      positions.push(position);
    }
    position = text.indexOf(term, Math.max(end, position + 1));
  }
  return positions;
}

function textRangeHasBoundary(text: string, start: number, end: number) {
  return isTextBoundary(text[start - 1]) && isTextBoundary(text[end]);
}

function isTextBoundary(value: string | undefined) {
  return !value || !/[\p{L}\p{N}-]/u.test(value);
}

function uniqueBoxes(boxes: HighlightBox[]) {
  const seen = new Set<string>();
  const output: HighlightBox[] = [];
  for (const box of boxes) {
    const key = `${Math.round(box.left)}:${Math.round(box.top)}:${Math.round(box.width)}:${Math.round(box.height)}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    output.push(box);
  }
  return output;
}

function mergeOverlappingBoxes(boxes: HighlightBox[]) {
  let current = uniqueBoxes(boxes);
  let changed = true;
  while (changed) {
    changed = false;
    const ordered = [...current].sort((left, right) => left.top - right.top || left.left - right.left);
    const merged: HighlightBox[] = [];
    for (const box of ordered) {
      const target = merged.find((candidate) => boxesTouch(candidate, box));
      if (!target) {
        merged.push({ ...box });
        continue;
      }
      const left = Math.min(target.left, box.left);
      const top = Math.min(target.top, box.top);
      const right = Math.max(target.left + target.width, box.left + box.width);
      const bottom = Math.max(target.top + target.height, box.top + box.height);
      target.left = left;
      target.top = top;
      target.width = right - left;
      target.height = bottom - top;
      changed = true;
    }
    current = merged;
  }
  return current;
}

function boxesTouch(left: HighlightBox, right: HighlightBox) {
  const pad = 2;
  return (
    left.left <= right.left + right.width + pad &&
    left.left + left.width + pad >= right.left &&
    left.top <= right.top + right.height + pad &&
    left.top + left.height + pad >= right.top
  );
}

export function normalizeHighlightBoxes(boxes: HighlightBox[]) {
  const merged = mergeOverlappingBoxes(boxes);
  const rows: HighlightBox[][] = [];
  for (const box of merged.sort((left, right) => left.top - right.top || left.left - right.left)) {
    const row = rows.find((candidate) => candidate.some((item) => verticalOverlapRatio(item, box) > 0.45));
    if (row) {
      row.push({ ...box });
    } else {
      rows.push([{ ...box }]);
    }
  }
  return rows.flatMap((row) => mergeRowSegments(row));
}

function mergeRowSegments(row: HighlightBox[]) {
  const ordered = row.sort((left, right) => left.left - right.left);
  const output: HighlightBox[] = [];
  for (const box of ordered) {
    const previous = output[output.length - 1];
    if (!previous || box.left > previous.left + previous.width + 2) {
      output.push({ ...box });
      continue;
    }
    const right = Math.max(previous.left + previous.width, box.left + box.width);
    previous.left = Math.min(previous.left, box.left);
    previous.top = Math.min(previous.top, box.top);
    previous.width = right - previous.left;
    previous.height = Math.max(previous.height, box.height);
  }
  return output;
}

function verticalOverlapRatio(left: HighlightBox, right: HighlightBox) {
  const top = Math.max(left.top, right.top);
  const bottom = Math.min(left.top + left.height, right.top + right.height);
  const overlap = Math.max(0, bottom - top);
  return overlap / Math.max(1, Math.min(left.height, right.height));
}

export function buildHighlightQuery(evidence: VerificationEvidence): HighlightQuery {
  const explicit = (evidence.matched_terms ?? []).map(normalizeText).filter(Boolean);
  const excerpt = stripExcerptNoise(compactText(evidence.pdf_excerpt));
  const reference = compactText(evidence.reference_text);
  const referenceTerms = extractTerms(`${excerpt} ${reference}`);
  // The pdf_excerpt is the passage actually located in this source; reference_text is the
  // assistant's own sentence, which frequently appears VERBATIM elsewhere in the paper
  // (e.g. the intro) and would otherwise hijack the highlight to the wrong region. So
  // build phrases from the excerpt and only fall back to the reference when the excerpt
  // yields none — this keeps "Aktive Textstelle" in sync with the cited excerpt.
  const excerptPhrases = extractPhrases(excerpt, 800);
  const phrases = (excerptPhrases.length ? excerptPhrases : extractPhrases(reference, 200)).slice(0, 9);
  const terms = extractTerms(`${explicit.join(" ")} ${excerpt} ${reference}`).filter(isAnchorTerm);
  return {
    phrases: Array.from(new Set(phrases)).slice(0, 8),
    terms: Array.from(new Set([...explicit, ...referenceTerms, ...terms])).filter(isAnchorTerm).slice(0, 18)
  };
}

// Located excerpts often carry a leading noise prefix that never matches the body text as
// one phrase (author block + "Abstract—…", or a running header) — strip it so the clean
// sentence remains and matches the source at the right place.
function stripExcerptNoise(excerpt: string): string {
  if (!excerpt) {
    return excerpt;
  }
  let text = excerpt;
  // Drop everything up to and including an "Abstract" header near the start.
  const abstract = /^(.{0,240}?)\babstract\b[\s:—–-]*/i.exec(text);
  if (abstract && abstract[0].length < text.length - 20) {
    text = text.slice(abstract[0].length);
  }
  return compactText(text);
}

export function evidenceListSignature(evidences: VerificationEvidence[]) {
  return evidences
    .map((evidence) =>
      [
        evidence.paper_id,
        evidence.kind,
        evidence.evidence_index ?? "",
        evidence.reference_text,
        evidence.pdf_excerpt,
        (evidence.matched_terms ?? []).join(",")
      ].join("|")
    )
    .join("\u001f");
}

export function evidenceColorIndex(evidence: VerificationEvidence | undefined, fallbackIndex: number) {
  const index = Number(evidence?.evidence_index);
  return Number.isFinite(index) ? index : fallbackIndex;
}

export function highlightQuerySignature(query: HighlightQuery) {
  return `${query.phrases.map(normalizeText).join("\u001e")}|\u001f|${query.terms.map(normalizeText).join("\u001e")}`;
}

export function bestMatchFor(pageMatches: Record<number, PageMatch> | undefined, querySignature: string) {
  const candidates = Object.values(pageMatches ?? {}).filter((match) => match.querySignature === querySignature && match.boxes.length);
  return candidates.sort((left, right) => right.score - left.score || Number(right.exact) - Number(left.exact) || left.pageNumber - right.pageNumber)[0] ?? null;
}

export const HIGHLIGHT_SCROLL_PADDING = 64;

// Scroll target so the highlight's top edge lands `padding` px below the viewport's
// top — not just the page centered, which can leave a highlight near a large/zoomed
// page's bottom edge off-screen. `pageRelativeTop` is the highlighted page surface's
// offset within the scroll container; `highlightTop` is the box's offset within that
// surface (same coordinate space `HighlightBox.top` is rendered in).
export function highlightScrollTop(pageRelativeTop: number, highlightTop: number, padding = HIGHLIGHT_SCROLL_PADDING): number {
  return Math.max(0, pageRelativeTop + highlightTop - padding);
}

// Topmost (smallest `top`) box of a match — the part of the highlight that should
// be brought into view first when scrolling to it.
export function topmostHighlightTop(boxes: { top: number }[] | null | undefined): number | null {
  if (!boxes || !boxes.length) {
    return null;
  }
  return boxes.reduce((min, box) => (box.top < min ? box.top : min), boxes[0].top);
}

export function pagesFor(pageMatches: Record<number, PageMatch> | undefined, querySignature: string) {
  return Object.values(pageMatches ?? {})
    .filter((match) => match.querySignature === querySignature && match.boxes.length)
    .map((match) => match.pageNumber)
    .sort((left, right) => left - right);
}

export function buildSearchQuery(term: string): HighlightQuery {
  const text = compactText(term);
  if (!text) {
    return { phrases: [], terms: [] };
  }
  return {
    phrases: [text],
    terms: extractTerms(text).slice(0, 12)
  };
}

function extractPhrases(text: string, maxLength = 220): string[] {
  if (!text) {
    return [];
  }
  const chunks = [text, ...text.split(/(?:[.!?;:]\s+|\n+)/g)];
  return chunks
    .map((chunk) => compactText(chunk))
    .filter((chunk) => {
      const tokens = chunk.split(" ").filter(Boolean);
      return chunk.length >= 12 && chunk.length <= maxLength && tokens.length >= 2;
    })
    .sort((left, right) => right.length - left.length);
}

function extractTerms(text: string): string[] {
  return Array.from(new Set(normalizeText(text).split(" ")))
    .filter((term) => term.length >= 5 && !ANCHOR_STOPWORDS.has(term) && !/^\d+$/.test(term))
    .slice(0, 28);
}

function isAnchorTerm(term: string) {
  return !ANCHOR_STOPWORDS.has(term) && (term.length >= 7 || /\d/.test(term) || term.includes("-"));
}

function isStrongFallbackTerm(term: string) {
  return /\d/.test(term) || term.includes("-") || term.length >= 10;
}

function compactText(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

function normalizeText(text: string) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}-]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

export function shortLabel(value: string) {
  return value.length > 72 ? `${value.slice(0, 69)}...` : value;
}
