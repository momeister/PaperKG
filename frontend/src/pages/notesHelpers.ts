import { api, API_BASE_URL } from "../api";
import { decodeCitationId } from "./assistantHelpers";
import { colorVarsForPaperId, evidenceColorVars } from "../citationColors";
import type { CSSProperties, ReactNode } from "react";
import type { Note, NoteAiMessage, NoteAiThread, NoteCitation, VerificationEvidence } from "../types";
import type {
  ThreadAnchorMeta, NoteCitationRow, ThreadStoredRange, ThreadAnchorRange,
  CitationMarkdownRef, ThreadTextRange, ThreadTextDiff, SelectionRange, MarkdownBlock,
} from "./NotesPage";

// Pure helpers/constants (thread-range anchoring, markdown (de)serialization,
// citation refs, note-UI state storage) extracted from NotesPage.tsx. No JSX;
// re-exported from NotesPage for backward compatibility.

export const THREAD_RANGE_TEXT_LIMIT = 16000;


export const TABLE_PICKER_COLS = 6;


export const TABLE_PICKER_ROWS = 6;


export const IMAGE_PREVIEW_HIDDEN_KEY = "sciencekg.notes.hideImagePreview";

// Character index in a <textarea> under a screen point. Textareas expose no caret-from-
// point API, so measure against a throwaway mirror <div> laid out identically over the
// textarea (same font/padding/wrapping) and read the caret there. Used for the pointer-
// based image drag so images can be dropped exactly between characters.


export function caretIndexFromPoint(textarea: HTMLTextAreaElement, clientX: number, clientY: number): number | null {
  const doc = textarea.ownerDocument;
  const win = doc.defaultView ?? window;
  const rect = textarea.getBoundingClientRect();
  const style = win.getComputedStyle(textarea);
  const mirror = doc.createElement("div");
  const copy = [
    "boxSizing", "paddingTop", "paddingRight", "paddingBottom", "paddingLeft",
    "borderTopWidth", "borderRightWidth", "borderBottomWidth", "borderLeftWidth",
    "fontFamily", "fontSize", "fontWeight", "fontStyle", "fontVariant", "letterSpacing",
    "lineHeight", "textTransform", "wordSpacing", "textIndent", "tabSize", "wordBreak"
  ] as const;
  for (const property of copy) {
    mirror.style[property as any] = style[property as any];
  }
  mirror.style.position = "fixed";
  mirror.style.left = `${rect.left}px`;
  mirror.style.top = `${rect.top - textarea.scrollTop}px`;
  mirror.style.width = `${rect.width}px`;
  mirror.style.height = "auto";
  mirror.style.whiteSpace = "pre-wrap";
  mirror.style.overflowWrap = "break-word";
  mirror.style.overflow = "hidden";
  mirror.style.opacity = "0";
  // Muss hit-testbar sein: caretRangeFromPoint überspringt pointer-events:none-Elemente
  // (dann käme die Position der Textarea/Overlay dahinter zurück, nie der Spiegel).
  mirror.style.pointerEvents = "auto";
  mirror.style.zIndex = "2147483647";
  // A single text node → caret offset is directly the character index. Trailing newline
  // needs a sentinel char so the last line has measurable height.
  const value = textarea.value;
  mirror.textContent = value.endsWith("\n") ? `${value} ` : value;
  doc.body.appendChild(mirror);
  try {
    const anyDoc = doc as Document & {
      caretRangeFromPoint?: (x: number, y: number) => Range | null;
      caretPositionFromPoint?: (x: number, y: number) => { offsetNode: Node; offset: number } | null;
    };
    if (typeof anyDoc.caretRangeFromPoint === "function") {
      const range = anyDoc.caretRangeFromPoint(clientX, clientY);
      if (range && mirror.contains(range.startContainer)) {
        return Math.min(value.length, range.startOffset);
      }
    }
    if (typeof anyDoc.caretPositionFromPoint === "function") {
      const position = anyDoc.caretPositionFromPoint(clientX, clientY);
      if (position && mirror.contains(position.offsetNode)) {
        return Math.min(value.length, position.offset);
      }
    }
  } catch {
    // fall through
  } finally {
    mirror.remove();
  }
  return null;
}


export const TRANSLATE_LANGUAGES = ["Deutsch", "Englisch", "Französisch", "Spanisch", "Italienisch", "Portugiesisch", "Niederländisch", "Polnisch", "Chinesisch", "Japanisch"];


export function buildMarkdownTable(cols: number, rows: number) {
  const safeCols = Math.max(1, cols);
  const safeRows = Math.max(1, rows);
  const header = `| ${Array.from({ length: safeCols }, (_, index) => `Spalte ${index + 1}`).join(" | ")} |`;
  const separator = `|${Array.from({ length: safeCols }, () => " --- ").join("|")}|`;
  const bodyRow = `| ${Array.from({ length: safeCols }, () => "    ").join(" | ")} |`;
  return `\n\n${header}\n${separator}\n${Array.from({ length: safeRows }, () => bodyRow).join("\n")}\n`;
}


export function previewElementToMarkdown(original: string, root: HTMLElement) {
  const directChild = Array.from(root.children).find((child): child is HTMLElement => child instanceof HTMLElement);
  if (!directChild) {
    return previewTextToMarkdown(original, root.innerText);
  }
  const tag = directChild.tagName.toLowerCase();
  const originalTrimmed = original.trim();
  if (tag === "h1") {
    return `# ${firstLine(serializePreviewInline(directChild))}`;
  }
  if (tag === "h2") {
    return `## ${firstLine(serializePreviewInline(directChild))}`;
  }
  if (tag === "blockquote") {
    return serializePreviewInline(directChild)
      .split(/\n+/)
      .map((line) => `> ${line}`)
      .join("\n");
  }
  if (tag === "ul") {
    const items = Array.from(directChild.querySelectorAll(":scope > li"));
    if (items.length) {
      return items.map((item) => `- ${serializePreviewInline(item).replace(/^[-*+]\s+/, "")}`).join("\n");
    }
  }
  if (tag === "p") {
    return serializePreviewInline(directChild).trimEnd();
  }
  if (originalTrimmed.startsWith("# ") || originalTrimmed.startsWith("## ") || originalTrimmed.startsWith(">") || /^- /m.test(originalTrimmed)) {
    return previewTextToMarkdown(original, root.innerText);
  }
  return serializePreviewInline(root).trimEnd();
}


export function serializePreviewInline(node: Node): string {
  return Array.from(node.childNodes).map(serializePreviewNode).join("").replace(/\u00a0/g, " ");
}


export function serializePreviewNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent ?? "";
  }
  if (!(node instanceof HTMLElement)) {
    return "";
  }
  const tag = node.tagName.toLowerCase();
  const inner = serializePreviewInline(node);
  if (tag === "br") {
    return "\n";
  }
  if (tag === "button" && node.dataset.citationId) {
    const label = (node.dataset.citationLabel || node.innerText || "Quelle").trim();
    return `[${label}](sciencekg://citation/${node.dataset.citationId})`;
  }
  if (tag === "a") {
    const href = node.dataset.linkHref || node.getAttribute("href") || "";
    return href ? `[${inner}](${href})` : inner;
  }
  if (tag === "strong" || tag === "b") {
    return `**${inner}**`;
  }
  if (tag === "em" || tag === "i") {
    return `*${inner}*`;
  }
  if (tag === "code") {
    return `\`${inner}\``;
  }
  if (tag === "mark") {
    return `==${inner}==`;
  }
  if (tag === "span" && (node.dataset.color || node.style.color)) {
    return `<span style="color:${node.dataset.color || node.style.color}">${inner}</span>`;
  }
  if (tag === "div" || tag === "p") {
    return inner;
  }
  return inner;
}


export function absoluteUrl(value: string) {
  if (/^https?:\/\//.test(value)) {
    return value;
  }
  return `${API_BASE_URL}${value}`;
}


export function stripHighlightMarkers(value: string) {
  return value.replace(/^==([\s\S]*)==$/, "$1");
}


export function parseMarkdownCitationRefs(markdown: string): CitationMarkdownRef[] {
  const refs: CitationMarkdownRef[] = [];
  // Matches the single-citation link (``sciencekg://citation/<id>``) and the grouped
  // Quellen-chip in both its long (``sciencekg://citations/<cite_id,...>``) and short
  // (``skg://c/<hash,...>``) form. For a group we emit one ref per contained id — all
  // sharing the token's span — so id-keyed consumers (citation panel, overlay highlight,
  // editor chip) keep working while the chip renders as a single button. Group ids are
  // normalized via decodeCitationId so ``refs[].id`` always carries the full ``cite_...`` key.
  const pattern = /\[([^\]]+)\]\((sciencekg:\/\/citation|sciencekg:\/\/citations|skg:\/\/c)\/([^)]+)\)/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(markdown)) !== null) {
    const label = match[1].trim();
    const start = match.index;
    const end = match.index + match[0].length;
    const isGroup = match[2] === "sciencekg://citations" || match[2] === "skg://c";
    if (isGroup) {
      const ids = match[3].split(",").map((id) => decodeCitationId(id)).filter(Boolean);
      for (const id of ids) {
        // Empty label/badge/title so the citation panel falls back to per-citation metadata
        // (its own badge/title) instead of the group label "n Quellen".
        refs.push({ id, label: "", badge: "", title: "", start, end, groupIds: ids });
      }
      continue;
    }
    refs.push({
      id: match[3],
      label,
      badge: citationBadgeFromLabel(label),
      title: citationTitleFromLabel(label),
      start,
      end
    });
  }
  return refs;
}

/**
 * Guarantee that every sciencekg://citation link of the replaced text survives an AI
 * rewrite. Links the rewrite kept (same citation id) stay untouched; dropped ones are
 * appended as a compact source line so the quote's provenance is never lost.
 */


export function withPreservedCitationLinks(originalText: string, replacementText: string): string {
  const pattern = /\[([^\]]+)\]\(sciencekg:\/\/citation\/([^)]+)\)/g;
  const lost: string[] = [];
  const seen = new Set<string>();
  for (const match of originalText.matchAll(pattern)) {
    const citationId = match[2];
    if (citationId === "preview" || seen.has(citationId)) {
      continue;
    }
    seen.add(citationId);
    if (!replacementText.includes(`sciencekg://citation/${citationId}`)) {
      lost.push(`[${match[1].trim()}](sciencekg://citation/${citationId})`);
    }
  }
  if (!lost.length) {
    return replacementText;
  }
  return `${replacementText.trimEnd()}\n\nQuellen: ${lost.join(" · ")}`;
}


export function findNoteSearchRanges(markdown: string, query: string) {
  const needle = query.toLowerCase().trim();
  if (!needle) {
    return [] as Array<{ start: number; end: number }>;
  }
  const haystack = markdown.toLowerCase();
  const ranges: Array<{ start: number; end: number }> = [];
  let index = haystack.indexOf(needle);
  while (index >= 0 && ranges.length < 500) {
    ranges.push({ start: index, end: index + needle.length });
    index = haystack.indexOf(needle, index + Math.max(1, needle.length));
  }
  return ranges;
}


export function citationRefAtPosition(refs: CitationMarkdownRef[], position: number) {
  // End-exclusive: a caret sitting right after the closing ``)`` is *outside* the
  // citation, so typing there is treated as new text rather than editing the token.
  return refs.find((ref) => position >= ref.start && position < ref.end) ?? null;
}


export function threadAnchorRange(thread: NoteAiThread, markdown: string, localRange?: ThreadStoredRange): ThreadAnchorRange | null {
  const resultRange = localRange ?? threadResultRange(thread);
  const resolvedResult = resultRange ? resolveStoredThreadRange(markdown, resultRange, true) : null;
  if (resolvedResult) {
    return resolvedResult;
  }

  const quote = stripHighlightMarkers(thread.anchor_quote || thread.selected_text || "").trim();
  const anchorStart = thread.anchor_start === null || thread.anchor_start === undefined ? NaN : Number(thread.anchor_start);
  const anchorEnd = thread.anchor_end === null || thread.anchor_end === undefined ? NaN : Number(thread.anchor_end);
  if (Number.isFinite(anchorStart) && Number.isFinite(anchorEnd) && anchorStart >= 0 && anchorEnd > anchorStart && anchorEnd <= markdown.length) {
    if (!quote || markdown.slice(anchorStart, anchorEnd).includes(quote) || quote.includes(markdown.slice(anchorStart, anchorEnd).trim())) {
      return { start: anchorStart, end: anchorEnd, text: markdown.slice(anchorStart, anchorEnd), changedRanges: [], manualRanges: [] };
    }
  }
  if (quote) {
    const index = markdown.indexOf(quote);
    if (index >= 0) {
      return { start: index, end: index + quote.length, text: quote, changedRanges: [], manualRanges: [] };
    }
    const fuzzy = resolveStoredThreadRange(markdown, { start: Number.isFinite(anchorStart) ? anchorStart : 0, end: Number.isFinite(anchorEnd) ? anchorEnd : 0, text: quote }, false);
    if (fuzzy) {
      return fuzzy;
    }
  }
  return null;
}


export function threadResultRange(thread: NoteAiThread): ThreadStoredRange | null {
  const start = Number(thread.ui_state?.result_anchor_start);
  const end = Number(thread.ui_state?.result_anchor_end);
  const text = String(thread.ui_state?.result_anchor_text ?? "").trim();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || !text) {
    return null;
  }
  return { start, end, text, manualRanges: readThreadManualRanges(thread.ui_state?.result_manual_ranges) };
}


export function threadRangeUiState(range: ThreadStoredRange) {
  const manualRanges = normalizeManualRanges(range.manualRanges, range.start, range.end);
  return {
    result_anchor_start: range.start,
    result_anchor_end: range.end,
    result_anchor_text: range.text.slice(0, THREAD_RANGE_TEXT_LIMIT),
    result_manual_ranges: manualRanges
  };
}


export function threadRangeMatchesUi(thread: NoteAiThread, range: ThreadStoredRange) {
  const start = Number(thread.ui_state?.result_anchor_start);
  const end = Number(thread.ui_state?.result_anchor_end);
  const text = String(thread.ui_state?.result_anchor_text ?? "");
  const manualRanges = normalizeManualRanges(range.manualRanges, range.start, range.end);
  return start === range.start && end === range.end && text === range.text.slice(0, THREAD_RANGE_TEXT_LIMIT) && sameThreadRanges(readThreadManualRanges(thread.ui_state?.result_manual_ranges), manualRanges);
}


export function resolveStoredThreadRange(markdown: string, stored: ThreadStoredRange, acceptEditedRange: boolean): ThreadAnchorRange | null {
  const start = Math.max(0, Math.min(markdown.length, stored.start));
  const end = Math.max(start, Math.min(markdown.length, stored.end));
  const original = stripHighlightMarkers(stored.text || "").trim();
  const manualRanges = normalizeManualRanges(stored.manualRanges, start, end);
  if (acceptEditedRange && end > start) {
    const current = markdown.slice(start, end);
    const comparableCurrent = textWithoutAbsoluteRanges(current, start, manualRanges);
    if (current.trim() && storedThreadRangeMatchesCurrent(comparableCurrent, original)) {
      const changedRanges = storedThreadTextLooksTruncated(comparableCurrent, original) ? [] : changedThreadRanges(start, comparableCurrent, original);
      return { start, end, text: current, changedRanges, manualRanges };
    }
  }
  if (original) {
    const exactIndex = markdown.indexOf(original);
    if (exactIndex >= 0 && !manualRanges.length) {
      return { start: exactIndex, end: exactIndex + original.length, text: original, changedRanges: [], manualRanges: [] };
    }
  }
  if (!original) {
    return null;
  }
  const fragments = meaningfulThreadFragments(original);
  for (const fragment of fragments) {
    const index = markdown.indexOf(fragment);
    if (index >= 0) {
      const range = expandThreadFragmentRange(markdown, index, fragment.length, original);
      const current = markdown.slice(range.start, range.end);
      return { ...range, text: current, changedRanges: changedThreadRanges(range.start, current, original), manualRanges: [] };
    }
  }
  return null;
}


export function textWithoutAbsoluteRanges(text: string, baseStart: number, ranges: ThreadTextRange[]) {
  if (!ranges.length) {
    return text;
  }
  let cursor = 0;
  const parts: string[] = [];
  for (const range of ranges) {
    const start = Math.max(0, Math.min(text.length, range.start - baseStart));
    const end = Math.max(start, Math.min(text.length, range.end - baseStart));
    if (start > cursor) {
      parts.push(text.slice(cursor, start));
    }
    cursor = Math.max(cursor, end);
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return parts.join("");
}


export function storedThreadRangeMatchesCurrent(current: string, original: string) {
  if (!original) {
    return Boolean(current.trim());
  }
  const normalizedCurrent = normalizeThreadText(current);
  const normalizedOriginal = normalizeThreadText(original);
  if (!normalizedOriginal) {
    return Boolean(normalizedCurrent);
  }
  const leadingOriginal = normalizedOriginal.slice(0, Math.min(160, normalizedOriginal.length));
  return normalizedCurrent.includes(normalizedOriginal) || normalizedCurrent.startsWith(leadingOriginal) || textSimilarity(current, original) >= 0.22;
}


export function storedThreadTextLooksTruncated(current: string, original: string) {
  if (!original || current.length <= original.length) {
    return false;
  }
  const normalizedOriginal = normalizeThreadText(original);
  return Boolean(normalizedOriginal && normalizeThreadText(current).startsWith(normalizedOriginal.slice(0, Math.min(160, normalizedOriginal.length))));
}


export function threadHighlightSegments(anchor: ThreadAnchorRange) {
  return rangeSegmentsExcluding({ start: anchor.start, end: anchor.end }, anchor.manualRanges);
}


export function rangeSegmentsExcluding(range: ThreadTextRange, exclusions: ThreadTextRange[]) {
  const normalized = normalizeManualRanges(exclusions, range.start, range.end);
  const segments: ThreadTextRange[] = [];
  let cursor = range.start;
  for (const exclusion of normalized) {
    if (exclusion.start > cursor) {
      segments.push({ start: cursor, end: exclusion.start });
    }
    cursor = Math.max(cursor, exclusion.end);
  }
  if (cursor < range.end) {
    segments.push({ start: cursor, end: range.end });
  }
  return segments.filter((segment) => segment.end > segment.start);
}


export function changedThreadRanges(start: number, current: string, original: string) {
  if (!original || normalizeThreadText(current) === normalizeThreadText(original)) {
    return [];
  }
  let prefix = 0;
  while (prefix < current.length && prefix < original.length && current[prefix] === original[prefix]) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix + prefix < current.length &&
    suffix + prefix < original.length &&
    current[current.length - 1 - suffix] === original[original.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const changedStart = start + prefix;
  const changedEnd = start + Math.max(prefix, current.length - suffix);
  return changedEnd > changedStart ? [{ start: changedStart, end: changedEnd }] : [];
}


export function shiftThreadRanges(ranges: Record<string, ThreadStoredRange>, previous: string, next: string) {
  if (previous === next || !Object.keys(ranges).length) {
    return ranges;
  }
  const diff = textDiffWindow(previous, next);
  if (!diff) {
    return ranges;
  }
  let changed = false;
  const shifted: Record<string, ThreadStoredRange> = {};
  for (const [threadId, range] of Object.entries(ranges)) {
    let start = range.start;
    let end = range.end;
    let manualRanges = shiftManualRanges(range.manualRanges ?? [], diff, next.length);
    const editOverlapsRange = diff.beforeEnd > start && diff.start < end;
    if (diff.beforeEnd <= start) {
      start += diff.delta;
      end += diff.delta;
    } else if (diff.start >= end) {
      // Edit happened after this range.
    } else {
      if (diff.start < start) {
        start = diff.start;
      }
      end = Math.max(start, end + diff.delta);
      if (diff.afterEnd > end) {
        end = diff.afterEnd;
      }
    }
    start = Math.max(0, Math.min(next.length, start));
    end = Math.max(start, Math.min(next.length, end));
    if (editOverlapsRange && diff.afterEnd > diff.start) {
      manualRanges.push({ start: Math.max(start, diff.start), end: Math.min(end, diff.afterEnd) });
    }
    manualRanges = normalizeManualRanges(manualRanges, start, end);
    shifted[threadId] = { ...range, start, end, manualRanges };
    changed = changed || start !== range.start || end !== range.end || !sameThreadRanges(manualRanges, range.manualRanges ?? []);
  }
  return changed ? shifted : ranges;
}


export function readThreadManualRanges(value: unknown): ThreadTextRange[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      const record = item as Record<string, unknown>;
      return { start: Number(record.start), end: Number(record.end) };
    })
    .filter((range) => Number.isFinite(range.start) && Number.isFinite(range.end) && range.end > range.start);
}


export function shiftManualRanges(ranges: ThreadTextRange[], diff: ThreadTextDiff, docLength: number) {
  if (!ranges.length) {
    return [];
  }
  const shifted: ThreadTextRange[] = [];
  for (const range of ranges) {
    let start = range.start;
    let end = range.end;
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      continue;
    }
    if (diff.beforeEnd <= start) {
      start += diff.delta;
      end += diff.delta;
    } else if (diff.start >= end) {
      // Edit happened after this manual segment.
    } else {
      if (diff.start < start) {
        start = diff.start;
      }
      end = Math.max(start, end + diff.delta);
      if (diff.afterEnd > end) {
        end = diff.afterEnd;
      }
    }
    start = Math.max(0, Math.min(docLength, start));
    end = Math.max(start, Math.min(docLength, end));
    if (end > start) {
      shifted.push({ start, end });
    }
  }
  return shifted;
}


export function normalizeManualRanges(ranges: ThreadTextRange[] | undefined, start: number, end: number) {
  const clipped = (ranges ?? [])
    .map((range) => ({
      start: Math.max(start, Math.min(end, Number(range.start))),
      end: Math.max(start, Math.min(end, Number(range.end)))
    }))
    .filter((range) => Number.isFinite(range.start) && Number.isFinite(range.end) && range.end > range.start)
    .sort((left, right) => left.start - right.start || left.end - right.end);
  const merged: ThreadTextRange[] = [];
  for (const range of clipped) {
    const previous = merged[merged.length - 1];
    if (previous && range.start <= previous.end) {
      previous.end = Math.max(previous.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }
  return merged;
}


export function sameThreadRanges(left: ThreadTextRange[], right: ThreadTextRange[]) {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((range, index) => range.start === right[index].start && range.end === right[index].end);
}


export function seedThreadRanges(current: Record<string, ThreadStoredRange>, threads: NoteAiThread[]) {
  if (!threads.length) {
    return current;
  }
  let next = current;
  for (const thread of threads) {
    if (next[thread.id]) {
      continue;
    }
    const range = threadResultRange(thread);
    if (!range) {
      continue;
    }
    if (next === current) {
      next = { ...current };
    }
    next[thread.id] = range;
  }
  return next;
}


export function textDiffWindow(previous: string, next: string): ThreadTextDiff | null {
  let start = 0;
  while (start < previous.length && start < next.length && previous[start] === next[start]) {
    start += 1;
  }
  if (start === previous.length && start === next.length) {
    return null;
  }
  let suffix = 0;
  while (
    suffix + start < previous.length &&
    suffix + start < next.length &&
    previous[previous.length - 1 - suffix] === next[next.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const beforeEnd = previous.length - suffix;
  const afterEnd = next.length - suffix;
  return { start, beforeEnd, afterEnd, delta: afterEnd - beforeEnd };
}


export function meaningfulThreadFragments(text: string) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return [];
  }
  const fragments = [
    normalized.slice(0, 140),
    normalized.slice(Math.max(0, Math.floor(normalized.length / 2) - 70), Math.min(normalized.length, Math.floor(normalized.length / 2) + 70)),
    normalized.slice(Math.max(0, normalized.length - 140))
  ];
  return Array.from(new Set(fragments.map((fragment) => fragment.trim()).filter((fragment) => fragment.length >= 24))).sort((left, right) => right.length - left.length);
}


export function expandThreadFragmentRange(markdown: string, fragmentStart: number, fragmentLength: number, original: string) {
  const targetLength = Math.max(fragmentLength, Math.min(markdown.length, original.length));
  let start = fragmentStart;
  let end = fragmentStart + fragmentLength;
  while (start > 0 && end - start < targetLength && markdown[start - 1] !== "\n") {
    start -= 1;
  }
  while (end < markdown.length && end - start < targetLength && markdown[end] !== "\n") {
    end += 1;
  }
  return { start, end };
}


export function textSimilarity(left: string, right: string) {
  const leftTerms = new Set(normalizeThreadText(left).split(" ").filter((term) => term.length >= 4));
  const rightTerms = new Set(normalizeThreadText(right).split(" ").filter((term) => term.length >= 4));
  if (!leftTerms.size || !rightTerms.size) {
    return 0;
  }
  let overlap = 0;
  leftTerms.forEach((term) => {
    if (rightTerms.has(term)) {
      overlap += 1;
    }
  });
  return overlap / Math.max(leftTerms.size, rightTerms.size);
}


export function normalizeThreadText(value: string) {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").replace(/\s+/g, " ").trim();
}


export function threadAnchorPlacement(markdown: string, index: number): "left" | "right" {
  const lineStart = markdown.lastIndexOf("\n", Math.max(0, index - 1)) + 1;
  return index - lineStart > 48 ? "left" : "right";
}


export function citationBadgeFromLabel(label: string) {
  return label.match(/\bZ\d+\b/i)?.[0].toUpperCase() ?? "Quelle";
}


export function citationTitleFromLabel(label: string) {
  const stripped = label.replace(/^\s*Z\d+\s*[-:]\s*/i, "").trim();
  return stripped || label.trim();
}


export function shortText(value: string | null | undefined, max = 88) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, Math.max(0, max - 3))}...` : text;
}


export function estimatedTextareaScrollTop(markdown: string, index: number, node: HTMLTextAreaElement) {
  const lineHeight = parseFloat(window.getComputedStyle(node).lineHeight) || 22;
  const linesBefore = markdown.slice(0, Math.max(0, index)).split("\n").length - 1;
  return Math.max(0, linesBefore * lineHeight - node.clientHeight * 0.32);
}


export function latestThreadAnswer(thread: NoteAiThread) {
  const messages = visibleThreadMessages(thread);
  const answer = [...messages].reverse().find((message) => message.role === "assistant")?.content;
  return (answer || "").trim();
}


export function threadDisplayMessages(thread: NoteAiThread) {
  const hiddenIds = hiddenThreadMessageIds(thread);
  const messages = visibleThreadMessages(thread);
  const cleanedMessages = messages.filter((message) => message.role !== "assistant" || message.content.trim());
  const answer = (thread.replacement_text || thread.response_text || "").trim();
  const hasAssistantText = cleanedMessages.some((message) => message.role === "assistant" && message.content.trim());
  const hiddenStoredAssistant = Boolean(thread.messages?.some((message) => message.role === "assistant" && hiddenIds.has(message.id)));
  const fallbackId = `${thread.id}:assistant:fallback`;
  if (!answer || hasAssistantText || hiddenStoredAssistant || hiddenIds.has(fallbackId)) {
    return cleanedMessages;
  }
  return [
    ...cleanedMessages,
    {
      id: `${thread.id}:assistant:fallback`,
      thread_id: thread.id,
      note_id: thread.note_id,
      role: "assistant" as const,
      content: answer,
      created_timestamp: thread.updated_timestamp ?? thread.created_timestamp
    }
  ];
}


export function visibleThreadMessages(thread: NoteAiThread) {
  const hiddenIds = hiddenThreadMessageIds(thread);
  return (thread.messages?.length ? thread.messages : legacyThreadMessages(thread)).filter((message) => !hiddenIds.has(message.id));
}


export function hiddenThreadMessageIds(thread: NoteAiThread) {
  const value = thread.ui_state?.hidden_message_ids;
  if (!Array.isArray(value)) {
    return new Set<string>();
  }
  return new Set(value.map((item) => String(item)));
}


export function threadCollapsed(thread: NoteAiThread) {
  return thread.ui_state?.collapsed !== false;
}


export function threadPinned(thread: NoteAiThread) {
  return thread.ui_state?.pinned === true;
}


export function sortPinnedThreads(threads: NoteAiThread[]) {
  return threads
    .map((thread, index) => ({ thread, index }))
    .sort((left, right) => {
      const pinnedDelta = Number(threadPinned(right.thread)) - Number(threadPinned(left.thread));
      return pinnedDelta || left.index - right.index;
    })
    .map((item) => item.thread);
}


export function shortThreadContext(value: string) {
  const text = stripHighlightMarkers(value || "").replace(/\s+/g, " ").trim();
  return text.length <= 170 ? text : `${text.slice(0, 167)}...`;
}


export function citationColorIndex(citation?: Pick<NoteCitation, "evidence_index"> | null, fallback = 0) {
  const index = Number(citation?.evidence_index);
  return Number.isFinite(index) ? index : fallback;
}


export function legacyThreadMessages(thread: NoteAiThread) {
  return [
    {
      id: `${thread.id}:user`,
      thread_id: thread.id,
      note_id: thread.note_id,
      role: "user",
      content: thread.instruction,
      created_timestamp: thread.created_timestamp
    },
    {
      id: `${thread.id}:assistant`,
      thread_id: thread.id,
      note_id: thread.note_id,
      role: "assistant",
      content: thread.response_text,
      created_timestamp: thread.created_timestamp
    }
  ];
}


export function threadSizeStyle(thread: NoteAiThread) {
  const width = Number(thread.ui_state?.width || 0);
  return {
    width: width > 260 ? `${width}px` : undefined,
  };
}


export function assignMissingThreadMeta(current: Record<string, ThreadAnchorMeta>, threadIds: string[]) {
  let next = current;
  let maxNumber = Object.values(current).reduce((max, meta) => Math.max(max, threadMetaNumber(meta)), 0);
  for (const threadId of threadIds) {
    if (next[threadId]) {
      continue;
    }
    if (next === current) {
      next = { ...current };
    }
    maxNumber += 1;
    next[threadId] = {
      label: `N${maxNumber}`,
      colorIndex: maxNumber - 1
    };
  }
  return next;
}


export function threadMetaNumber(meta: ThreadAnchorMeta) {
  const match = /^N(\d+)$/i.exec(meta.label);
  return match ? Number(match[1]) : meta.colorIndex + 1;
}


export function uiStateKey(key: string) {
  return `sciencekg.notes.ui.${key}`;
}


export function loadBooleanUiState(key: string, fallback: boolean) {
  try {
    const value = window.localStorage.getItem(uiStateKey(key));
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}


export function saveBooleanUiState(key: string, value: boolean) {
  try {
    window.localStorage.setItem(uiStateKey(key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}


export function loadNumberUiState(key: string, fallback: number) {
  try {
    const value = Number(window.localStorage.getItem(uiStateKey(key)));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}


export function saveNumberUiState(key: string, value: number) {
  try {
    window.localStorage.setItem(uiStateKey(key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}


export function loadThreadMetaUiState(key: string, fallback: Record<string, ThreadAnchorMeta>) {
  try {
    const raw = window.localStorage.getItem(uiStateKey(key));
    if (!raw) {
      return fallback;
    }
    const parsed = JSON.parse(raw) as Record<string, ThreadAnchorMeta>;
    if (!parsed || typeof parsed !== "object") {
      return fallback;
    }
    return Object.fromEntries(
      Object.entries(parsed)
        .filter(([, value]) => value && typeof value.label === "string" && Number.isFinite(value.colorIndex))
        .map(([threadId, value]) => [threadId, { label: value.label, colorIndex: value.colorIndex }])
    );
  } catch {
    return fallback;
  }
}


export function saveThreadMetaUiState(key: string, value: Record<string, ThreadAnchorMeta>) {
  try {
    window.localStorage.setItem(uiStateKey(key), JSON.stringify(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}


export function formatError(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unbekannter Fehler";
}


export function markdownContinuation(line: string) {
  const bullet = /^(\s*)([-*+])\s+(.*)$/.exec(line);
  if (bullet) {
    return bullet[3].trim() ? { prefix: `${bullet[1]}${bullet[2]} ` } : { prefix: "", removeCurrentPrefix: true };
  }
  const numbered = /^(\s*)(\d+)([.)])\s+(.*)$/.exec(line);
  if (numbered) {
    return numbered[4].trim()
      ? { prefix: `${numbered[1]}${Number(numbered[2]) + 1}${numbered[3]} ` }
      : { prefix: "", removeCurrentPrefix: true };
  }
  const quote = /^(\s*>\s?)(.*)$/.exec(line);
  if (quote) {
    return quote[2].trim() ? { prefix: quote[1] } : { prefix: "", removeCurrentPrefix: true };
  }
  return null;
}


export function splitMarkdownBlocks(value: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const pattern = /\S[\s\S]*?(?=\n{2,}|$)/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(value)) !== null) {
    blocks.push({ raw: match[0], start: match.index, end: match.index + match[0].length });
    if (match.index === pattern.lastIndex) {
      pattern.lastIndex += 1;
    }
  }
  return blocks;
}


export function isComplexPreviewBlock(block: string) {
  const trimmed = block.trim();
  return /^!\[[^\]]*\]\([^)]+\)$/.test(trimmed) || /^\|.+\|\n\|[-:|\s]+\|/.test(trimmed);
}


export function previewTextToMarkdown(original: string, editedText: string) {
  const text = editedText.replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trimEnd();
  const originalTrimmed = original.trim();
  if (originalTrimmed.startsWith("# ")) {
    return `# ${firstLine(text)}`;
  }
  if (originalTrimmed.startsWith("## ")) {
    return `## ${firstLine(text)}`;
  }
  if (originalTrimmed.startsWith(">")) {
    return text.split("\n").map((line) => `> ${line}`).join("\n");
  }
  if (/^- /m.test(originalTrimmed)) {
    return text.split("\n").filter(Boolean).map((line) => `- ${line.replace(/^[-*+]\s+/, "")}`).join("\n");
  }
  return text;
}


export function firstLine(text: string) {
  return text.split("\n")[0]?.trim() ?? "";
}


export function noteTitleForSave(title: string, markdown: string) {
  const trimmed = title.trim();
  if (trimmed && !isUntitledNoteTitle(trimmed)) {
    return trimmed;
  }
  const suggestion = suggestNoteTitle(markdown);
  return suggestion || trimmed || "Neue Notiz";
}


export function isUntitledNoteTitle(title: string) {
  return ["", "Neue Notiz", "Assistant Notiz"].includes(title.trim());
}


export function suggestNoteTitle(markdown: string) {
  const heading = markdown.match(/^#{1,3}\s+(.+)$/m)?.[1]?.trim();
  const source = heading || markdown;
  const text = source
    .replace(/!\[[^\]]*\]\([^)]+\)/g, " ")
    .replace(/\[[^\]]+\]\([^)]+\)/g, " ")
    .replace(/[#>*_`|[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (text.length < 90 && !heading) {
    return "";
  }
  return text.split(/\s+/).slice(0, 8).join(" ").slice(0, 72);
}


export function textTerms(text: string) {
  return Array.from(new Set(text.toLowerCase().replace(/[^a-z0-9-]+/g, " ").split(" "))).filter((term) => term.length >= 5).slice(0, 12);
}
