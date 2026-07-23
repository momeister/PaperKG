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

export const FORMAT_AS_MARKDOWN_INSTRUCTION =
  "Formatiere den markierten Text als sauberes Markdown gemäß der unterstützten Syntax " +
  "(Überschriften # bis ######, Listen mit - oder 1. inkl. Verschachtelung durch Einrückung, " +
  "Blockzitate mit >, **fett**, *kursiv*, `code`, Links, Tabellen, Trennlinien als ____ oder - - - -, " +
  "einklappbare Abschnitte als ':::toggle+ Titel' … ':::'). Ändere den Inhalt inhaltlich " +
  "nicht, nur die Formatierung. Zitatlinks bleiben unverändert.";


// --- Einklappbare Abschnitte (Notion-Toggles) --------------------------------------------
// Gespeichert als Fence-Block, damit Zustand + Body verlustfrei im Markdown round-trippen:
//   :::toggle+ Titel        (+ = offen, - = eingeklappt)
//   Body-Markdown (mehrere Blöcke erlaubt)
//   :::
export type ToggleBlock = { open: boolean; title: string; body: string };

const TOGGLE_OPEN_PATTERN = /^:::toggle([+-])?\s?(.*)$/;
const TOGGLE_CLOSE_PATTERN = /^:::\s*$/;

export function isToggleOpenLine(line: string): boolean {
  return TOGGLE_OPEN_PATTERN.test(line.trim());
}

export function parseToggleBlock(raw: string): ToggleBlock | null {
  const lines = raw.split("\n");
  const match = TOGGLE_OPEN_PATTERN.exec((lines[0] ?? "").trim());
  if (!match) return null;
  let end = lines.length;
  for (let index = 1; index < lines.length; index += 1) {
    if (TOGGLE_CLOSE_PATTERN.test(lines[index].trim())) {
      end = index;
      break;
    }
  }
  return { open: match[1] !== "-", title: match[2].trim(), body: lines.slice(1, end).join("\n") };
}

export function setToggleOpen(raw: string, open: boolean): string {
  return raw.replace(/^(\s*):::toggle[+-]?/, `$1:::toggle${open ? "+" : "-"}`);
}

/** Fresh toggle skeleton for the toolbar button / autoformat; the caret is placed after the marker. */
export function toggleSnippet(title = ""): string {
  return `:::toggle+ ${title}\n\n:::`;
}


// --- Block-Segmentierung -----------------------------------------------------------------
// Ein Markdown-Block (durch Leerzeilen getrennt) kann Text, Listenzeilen und Trennlinien mischen
// (einzelne \n, keine Leerzeile). Früher wurde ein solcher Block komplett als Liste gerendert und
// alle Nicht-Listen-Zeilen (inkl. Text darüber) still verworfen. segmentBlockLines zerlegt ihn in
// aufeinanderfolgende, typreine Runs, die renderBlock der Reihe nach ausgibt.
export type BlockSegment =
  | { type: "divider"; kind: DividerKind }
  | { type: "list"; lines: string[] }
  | { type: "text"; lines: string[] };

export function segmentBlockLines(lines: string[]): BlockSegment[] {
  const segments: BlockSegment[] = [];
  let textRun: string[] = [];
  let listRun: string[] = [];
  const flushText = () => {
    if (textRun.length) {
      segments.push({ type: "text", lines: textRun });
      textRun = [];
    }
  };
  const flushList = () => {
    if (listRun.length) {
      segments.push({ type: "list", lines: listRun });
      listRun = [];
    }
  };
  for (const line of lines) {
    const dividerKind = isDividerBlock(line.trim());
    if (dividerKind) {
      flushText();
      flushList();
      segments.push({ type: "divider", kind: dividerKind });
      continue;
    }
    if (LIST_LINE_PATTERN.test(line)) {
      flushText();
      listRun.push(line);
      continue;
    }
    flushList();
    textRun.push(line);
  }
  flushText();
  flushList();
  return segments;
}


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
  const headingLevel = /^h([1-6])$/.exec(tag)?.[1];
  if (headingLevel) {
    return `${"#".repeat(Number(headingLevel))} ${firstLine(serializePreviewInline(directChild))}`;
  }
  if (tag === "blockquote") {
    return serializePreviewInline(directChild)
      .split(/\n+/)
      .map((line) => `> ${line}`)
      .join("\n");
  }
  if (tag === "ul" || tag === "ol") {
    const hasItems = Array.from(directChild.children).some((child) => child.tagName === "LI");
    if (hasItems) {
      return serializeListContainer(directChild);
    }
  }
  if (tag === "p") {
    return serializePreviewInline(directChild).trimEnd();
  }
  if (
    /^#{1,6}\s/.test(originalTrimmed) ||
    originalTrimmed.startsWith(">") ||
    /^(\s*)([-*+]|\d+[.)])\s+/m.test(originalTrimmed)
  ) {
    return previewTextToMarkdown(original, root.innerText);
  }
  return serializePreviewInline(root).trimEnd();
}


/**
 * Recursively serializes a <ul>/<ol> back to indented markdown: each <li>'s own inline content
 * (excluding any nested <ul>/<ol> inside it) becomes one line, then nested lists render as
 * further-indented lines beneath it — mirroring parseListLines' indentation-stack model.
 */
function serializeListContainer(container: Element, depth = 0): string {
  const ordered = container.tagName.toLowerCase() === "ol";
  const indent = "\t".repeat(depth);
  const items = Array.from(container.children).filter((child) => child.tagName === "LI");
  return items
    .map((item, index) => {
      const nestedLists = Array.from(item.children).filter((child) => child.tagName === "UL" || child.tagName === "OL");
      const ownText = Array.from(item.childNodes)
        .filter((child) => !(child instanceof HTMLElement && (child.tagName === "UL" || child.tagName === "OL")))
        .map(serializePreviewNode)
        .join("")
        .replace(/ /g, " ")
        .replace(/^(\s*)([-*+]|\d+[.)])\s+/, "")
        .trim();
      const marker = ordered ? `${index + 1}.` : "-";
      const ownLine = `${indent}${marker} ${ownText}`;
      const childLines = nestedLists.map((nested) => serializeListContainer(nested as Element, depth + 1)).join("\n");
      return childLines ? `${ownLine}\n${childLines}` : ownLine;
    })
    .join("\n");
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
  // Sammel-Zitat-Chip (CitationGroupButton): vor der Kind-Rekursion abfangen, sonst würde
  // nur der sichtbare Label-Text ("3 Quellen") — bei offenem Popover sogar dessen Einträge —
  // serialisiert und der skg://c-Marker beim Blur/Moduswechsel unwiederbringlich zerstört.
  if (node.dataset.citationGroupIds) {
    const label = (node.dataset.citationLabel || "Quellen").trim();
    return `[${label}](skg://c/${node.dataset.citationGroupIds})`;
  }
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
  if (/^(https?:|data:|blob:)/.test(value)) {
    return value;
  }
  return `${API_BASE_URL}${value}`;
}


// Strip a baked-in localhost origin (with its ephemeral Tauri port) from asset URLs. Older notes
// stored `![](http://127.0.0.1:<port>/notes/assets/...)`; that port changes every app launch, so
// after a restart the URL 404s and the image vanishes. Reducing it to the relative path lets
// absoluteUrl() re-attach the *current* API_BASE_URL at render time — the file on disk is intact.
export function normalizeAssetUrl(value: string): string {
  return value.replace(
    /^https?:\/\/(?:127\.0\.0\.1|localhost)(?::\d+)?(?=\/(?:notes\/assets|note_assets|companion|assets)\/)/i,
    ""
  );
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


export type ContinueLineResult = { next: string; selStart: number; selEnd: number };

/**
 * Enter inside a plain <textarea>: continue the current line's list/quote marker (and its
 * indentation) onto the new line, or — on an already-empty marker line — strip the marker
 * instead of adding another empty item. Returns null when the current line isn't a
 * list/quote line, so callers fall back to the browser's default newline.
 */
export function continueMarkdownLine(value: string, selectionStart: number, selectionEnd: number): ContinueLineResult | null {
  const lineStart = value.lastIndexOf("\n", Math.max(0, selectionStart - 1)) + 1;
  const currentLine = value.slice(lineStart, selectionStart);
  const continuation = markdownContinuation(currentLine);
  if (!continuation) {
    return null;
  }
  if (continuation.removeCurrentPrefix) {
    const next = `${value.slice(0, lineStart)}${value.slice(selectionEnd)}`;
    return { next, selStart: lineStart, selEnd: lineStart };
  }
  const insertion = `\n${continuation.prefix}`;
  const next = `${value.slice(0, selectionStart)}${insertion}${value.slice(selectionEnd)}`;
  const caret = selectionStart + insertion.length;
  return { next, selStart: caret, selEnd: caret };
}


export type ToggleWrapResult = { next: string; selStart: number; selEnd: number };

/**
 * Bold/italic/code/link shortcuts: toggle instead of always wrapping, so pressing Ctrl+B twice
 * on the same text undoes it instead of nesting markers ("**text**" -> "****text****"). Checks
 * both cases — markers inside the selection ("**bold**" fully selected) and markers just
 * outside it (selecting "bold" inside "**bold**") — before falling back to wrapping.
 */
export function toggleWrap(value: string, start: number, end: number, before: string, after: string = before): ToggleWrapResult {
  const selected = value.slice(start, end) || "Text";
  if (selected.length >= before.length + after.length && selected.startsWith(before) && selected.endsWith(after)) {
    const inner = selected.slice(before.length, selected.length - after.length);
    const next = `${value.slice(0, start)}${inner}${value.slice(end)}`;
    return { next, selStart: start, selEnd: start + inner.length };
  }
  const beforeSlice = value.slice(Math.max(0, start - before.length), start);
  const afterSlice = value.slice(end, end + after.length);
  if (before && beforeSlice === before && afterSlice === after) {
    const outStart = start - before.length;
    const next = `${value.slice(0, outStart)}${selected}${value.slice(end + after.length)}`;
    return { next, selStart: outStart, selEnd: outStart + selected.length };
  }
  const next = `${value.slice(0, start)}${before}${selected}${after}${value.slice(end)}`;
  return { next, selStart: start + before.length, selEnd: start + before.length + selected.length };
}


export type ListNode = { ordered: boolean; number?: number; content: string; children: ListNode[] };

const LIST_LINE_PATTERN = /^(\s*)([-*+]|\d+[.)])\s+(.*)$/;

/**
 * Turns a flat run of list-marker lines into a nested tree by comparing each line's leading
 * whitespace length against a stack of open ancestors — the same indentation Tab/Shift+Tab and
 * markdownContinuation already produce while editing, just structured for rendering.
 */
export function parseListLines(lines: string[]): ListNode[] {
  const root: ListNode[] = [];
  const stack: { indent: number; children: ListNode[] }[] = [{ indent: -1, children: root }];
  for (const line of lines) {
    const match = LIST_LINE_PATTERN.exec(line);
    if (!match) continue;
    const indent = match[1].length;
    const marker = match[2];
    const ordered = /^\d/.test(marker);
    const node: ListNode = {
      ordered,
      number: ordered ? Number(marker.slice(0, -1)) : undefined,
      content: match[3],
      children: []
    };
    while (stack.length > 1 && stack[stack.length - 1].indent >= indent) {
      stack.pop();
    }
    stack[stack.length - 1].children.push(node);
    stack.push({ indent, children: node.children });
  }
  return root;
}


export function splitMarkdownBlocks(value: string): MarkdownBlock[] {
  // Line-based scan (was a single lazy regex). Normal blocks still break on blank lines, but a
  // ':::toggle …' fence is kept as ONE block through its blank lines up to the closing ':::', so
  // a collapsible section's multi-block body round-trips and edits/citations keep exact offsets.
  const blocks: MarkdownBlock[] = [];
  const lines = value.split("\n");
  const lineOffset: number[] = [];
  let acc = 0;
  for (const line of lines) {
    lineOffset.push(acc);
    acc += line.length + 1; // + the "\n" that split removed
  }
  const pushBlock = (startLine: number, endLine: number) => {
    const first = lines[startLine];
    const lead = first.length - first.trimStart().length; // mirror old regex's \S start
    const start = lineOffset[startLine] + lead;
    const end = lineOffset[endLine] + lines[endLine].length;
    if (end > start) {
      blocks.push({ raw: value.slice(start, end), start, end });
    }
  };
  let index = 0;
  while (index < lines.length) {
    if (lines[index].trim() === "") {
      index += 1;
      continue;
    }
    if (isToggleOpenLine(lines[index])) {
      let close = index + 1;
      while (close < lines.length && !TOGGLE_CLOSE_PATTERN.test(lines[close].trim())) {
        close += 1;
      }
      const endLine = close < lines.length ? close : lines.length - 1;
      pushBlock(index, endLine);
      index = endLine + 1;
      continue;
    }
    let end = index;
    while (end < lines.length && lines[end].trim() !== "") {
      end += 1;
    }
    pushBlock(index, end - 1);
    index = end;
  }
  return blocks;
}


export function isComplexPreviewBlock(block: string) {
  const trimmed = block.trim();
  return (
    /^!\[[^\]]*\]\([^)]+\)$/.test(trimmed) ||
    /^\|.+\|\n\|[-:|\s]+\|/.test(trimmed) ||
    isDividerBlock(trimmed) !== null
  );
}


// Full-width divider between topic sections, written as literal fill characters ("____" /
// "- - - -") sized to the editor's current width — so it already looks like a line in the raw
// markdown, not only in the rendered preview, with no overlay/mirror trick involved.
export type DividerKind = "solid" | "dashed";

const DIVIDER_UNIT: Record<DividerKind, string> = { solid: "_", dashed: "- " };
const DIVIDER_MIN_UNITS = 10;
const DIVIDER_FALLBACK_UNITS: Record<DividerKind, number> = { solid: 40, dashed: 20 };

let dividerMeasureCanvas: HTMLCanvasElement | null = null;

function dividerUnitWidth(font: string, kind: DividerKind): number {
  if (typeof document === "undefined") return 0;
  dividerMeasureCanvas = dividerMeasureCanvas ?? document.createElement("canvas");
  const ctx = dividerMeasureCanvas.getContext("2d");
  if (!ctx) return 0;
  ctx.font = font;
  return ctx.measureText(DIVIDER_UNIT[kind]).width;
}

export function buildDividerLine(kind: DividerKind, unitCount: number): string {
  const count = Math.max(DIVIDER_MIN_UNITS, Math.round(unitCount));
  return kind === "solid" ? "_".repeat(count) : Array.from({ length: count }, () => "-").join(" ");
}

export function dividerLineForWidth(kind: DividerKind, font: string, widthPx: number): string {
  const unit = dividerUnitWidth(font, kind);
  const count = unit > 0 ? Math.floor(widthPx / unit) : DIVIDER_FALLBACK_UNITS[kind];
  return buildDividerLine(kind, count);
}

function dividerTextareaMetrics(node: HTMLTextAreaElement) {
  const style = window.getComputedStyle(node);
  const paddingX = (parseFloat(style.paddingLeft) || 0) + (parseFloat(style.paddingRight) || 0);
  return { font: style.font, widthPx: Math.max(0, node.clientWidth - paddingX) };
}

/** Builds an insertable divider snippet sized to `node`'s current width; falls back to a fixed
 * length when no textarea is mounted yet (e.g. inserting from Preview-only mode). */
export function dividerSnippetForTextarea(kind: DividerKind, node: HTMLTextAreaElement | null): string {
  if (!node) return `\n\n${buildDividerLine(kind, DIVIDER_FALLBACK_UNITS[kind])}\n\n`;
  const { font, widthPx } = dividerTextareaMetrics(node);
  return `\n\n${dividerLineForWidth(kind, font, widthPx)}\n\n`;
}

export function isDividerBlock(trimmedBlock: string): DividerKind | null {
  if (/^_{3,}$/.test(trimmedBlock)) return "solid";
  if (/^-{3,}$/.test(trimmedBlock)) return "solid";
  if (/^-(?: -)+$/.test(trimmedBlock)) return "dashed";
  return null;
}

/**
 * Re-fits every divider line already in the markdown to the textarea's current width. Called
 * from a resize observer so a divider inserted at one pane width keeps touching both margins
 * after the pane (or window) is resized. Returns null when nothing needs to change.
 */
export function resyncDividerLines(markdown: string, node: HTMLTextAreaElement): string | null {
  if (!/[_-]/.test(markdown)) return null;
  const { font, widthPx } = dividerTextareaMetrics(node);
  if (widthPx <= 0) return null;
  let changed = false;
  const next = markdown
    .split("\n")
    .map((line) => {
      const kind = isDividerBlock(line.trim());
      if (!kind) return line;
      const rebuilt = dividerLineForWidth(kind, font, widthPx);
      if (rebuilt === line) return line;
      changed = true;
      return rebuilt;
    })
    .join("\n");
  return changed ? next : null;
}

/**
 * VS-Code-style "scroll past end" (padding-bottom so the last line can reach the pane's top)
 * plus keeping divider lines fitted to width — both need to react to the same pane resizes
 * (window resize, split-drag, overlay resize), so one ResizeObserver drives both.
 */
export function attachTextareaAutoSync(node: HTMLTextAreaElement, getValue: () => string, setValue: (next: string) => void): () => void {
  const applyScrollPastEnd = () => {
    const style = window.getComputedStyle(node);
    const lineHeight = parseFloat(style.lineHeight) || parseFloat(style.fontSize) * 1.4 || 20;
    node.style.paddingBottom = `${Math.max(0, node.clientHeight - lineHeight)}px`;
  };
  const applyDividerResync = () => {
    const current = getValue();
    const resynced = resyncDividerLines(current, node);
    if (resynced === null) return;
    const focused = document.activeElement === node;
    const selStart = node.selectionStart;
    const selEnd = node.selectionEnd;
    const diff = textDiffWindow(current, resynced);
    setValue(resynced);
    if (focused && diff) {
      const shift = (pos: number) => (diff.beforeEnd <= pos ? pos + diff.delta : diff.start >= pos ? pos : diff.start);
      requestAnimationFrame(() => node.setSelectionRange(shift(selStart), shift(selEnd)));
    }
  };
  let frame: number | null = null;
  const run = () => {
    if (frame !== null) return;
    frame = window.requestAnimationFrame(() => {
      frame = null;
      applyScrollPastEnd();
      applyDividerResync();
    });
  };
  run();
  const observer = new ResizeObserver(run);
  observer.observe(node);
  return () => {
    observer.disconnect();
    if (frame !== null) window.cancelAnimationFrame(frame);
  };
}

// Draggable split-view gutter: keep the smaller pane from collapsing to nothing.
export const SPLIT_RATIO_MIN = 0.2;
export const SPLIT_RATIO_MAX = 0.8;

export function clampSplitRatio(ratio: number): number {
  if (!Number.isFinite(ratio)) return 0.5;
  return Math.min(SPLIT_RATIO_MAX, Math.max(SPLIT_RATIO_MIN, ratio));
}


export type TabIndentResult = { next: string; selStart: number; selEnd: number };

/** Leading whitespace stripped by one outdent level: one tab, else up to 4 spaces. */
function stripLeadingIndent(line: string): { stripped: string; removed: number } {
  if (line.startsWith("\t")) return { stripped: line.slice(1), removed: 1 };
  const spaceMatch = /^ {1,4}/.exec(line);
  if (spaceMatch) return { stripped: line.slice(spaceMatch[0].length), removed: spaceMatch[0].length };
  return { stripped: line, removed: 0 };
}

/**
 * Tab/Shift+Tab inside a plain <textarea>: insert/remove indentation instead of the browser's
 * default focus-cycling. Returns null when there's nothing to outdent — callers still call
 * preventDefault() unconditionally on Tab so focus never escapes the textarea either way.
 */
export function applyTabIndent(
  value: string,
  selectionStart: number,
  selectionEnd: number,
  outdent: boolean
): TabIndentResult | null {
  if (selectionStart === selectionEnd) {
    const lineStart = value.lastIndexOf("\n", selectionStart - 1) + 1;
    const lineEnd = (() => {
      const idx = value.indexOf("\n", selectionStart);
      return idx === -1 ? value.length : idx;
    })();
    if (!outdent) {
      // On a list line, Tab indents the whole line (moves the marker) regardless of where the
      // caret sits inside it — a plain mid-line tab insert would leave the bullet in place.
      if (LIST_LINE_PATTERN.test(value.slice(lineStart, lineEnd))) {
        const next = `${value.slice(0, lineStart)}\t${value.slice(lineStart)}`;
        const caret = selectionStart + 1;
        return { next, selStart: caret, selEnd: caret };
      }
      const next = `${value.slice(0, selectionStart)}\t${value.slice(selectionStart)}`;
      return { next, selStart: selectionStart + 1, selEnd: selectionStart + 1 };
    }
    const { stripped, removed } = stripLeadingIndent(value.slice(lineStart, lineEnd));
    if (removed === 0) return null;
    const next = `${value.slice(0, lineStart)}${stripped}${value.slice(lineEnd)}`;
    const caret = Math.max(lineStart, selectionStart - removed);
    return { next, selStart: caret, selEnd: caret };
  }

  const lineStart = value.lastIndexOf("\n", selectionStart - 1) + 1;
  const effectiveEnd = selectionEnd > selectionStart && value[selectionEnd - 1] === "\n" ? selectionEnd - 1 : selectionEnd;
  const lineEndIdx = value.indexOf("\n", effectiveEnd);
  const lineEnd = lineEndIdx === -1 ? value.length : lineEndIdx;
  const lines = value.slice(lineStart, lineEnd).split("\n");

  if (!outdent) {
    const nextLines = lines.map((line) => `\t${line}`);
    const next = `${value.slice(0, lineStart)}${nextLines.join("\n")}${value.slice(lineEnd)}`;
    return { next, selStart: selectionStart + 1, selEnd: selectionEnd + lines.length };
  }

  let firstLineRemoved = 0;
  let totalRemoved = 0;
  const nextLines = lines.map((line, index) => {
    const { stripped, removed } = stripLeadingIndent(line);
    if (index === 0) firstLineRemoved = removed;
    totalRemoved += removed;
    return stripped;
  });
  if (totalRemoved === 0) return null;
  const next = `${value.slice(0, lineStart)}${nextLines.join("\n")}${value.slice(lineEnd)}`;
  const selStart = Math.max(lineStart, selectionStart - firstLineRemoved);
  const selEnd = Math.max(selStart, selectionEnd - totalRemoved);
  return { next, selStart, selEnd };
}


export function previewTextToMarkdown(original: string, editedText: string) {
  const text = editedText.replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trimEnd();
  const originalTrimmed = original.trim();
  const headingLevel = /^(#{1,6})\s/.exec(originalTrimmed)?.[1]?.length;
  if (headingLevel) {
    return `${"#".repeat(headingLevel)} ${firstLine(text)}`;
  }
  if (originalTrimmed.startsWith(">")) {
    return text.split("\n").map((line) => `> ${line}`).join("\n");
  }
  const listMarker = /^(\s*)([-*+]|\d+[.)])\s+/m.exec(originalTrimmed)?.[2];
  if (listMarker) {
    const ordered = /^\d/.test(listMarker);
    return text
      .split("\n")
      .filter(Boolean)
      .map((line, index) => `${ordered ? `${index + 1}.` : "-"} ${line.replace(/^(\s*)([-*+]|\d+[.)])\s+/, "")}`)
      .join("\n");
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
