import type { GlossaryEntry } from "../types";

export type GlossaryMatch = { start: number; end: number; entry: GlossaryEntry };
const escapeRegex = (text: string) => text.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");

/** Preserve source offsets; never match syntax, code, destinations or citation labels. */
export function excludedRanges(text: string): Array<[number, number]> {
  const ranges: Array<[number, number]> = [];
  const fences = /^ {0,3}(`{3,}|~{3,})[^\n]*(?:\n|$)/gm;
  for (let match = fences.exec(text); match; match = fences.exec(text)) {
    const closing = new RegExp(`^ {0,3}${match[1][0]}{${match[1].length},}[ \t]*(?:\\n|$)`, "gm");
    closing.lastIndex = fences.lastIndex;
    const end = closing.exec(text);
    const stop = end ? closing.lastIndex : text.length;
    ranges.push([match.index, stop]);
    fences.lastIndex = stop;
  }
  const ticks = /`+/g;
  for (let match = ticks.exec(text); match; match = ticks.exec(text)) {
    if (ranges.some(([start, end]) => match!.index >= start && match!.index < end)) continue;
    const closing = new RegExp("(?<!`)`{" + match[0].length + "}(?!`)", "g");
    closing.lastIndex = ticks.lastIndex;
    const end = closing.exec(text);
    const stop = end ? closing.lastIndex : text.length;
    ranges.push([match.index, stop]);
    ticks.lastIndex = stop;
  }
  const patterns = [
    /<(pre|code)\b[^>]*>[\s\S]*?<\/\1>/gi,
    /!?\[[^\]\n]*\]\([^\n]*?\)/g,
    /!?\[[^\]\n]*\]\[[^\]\n]*\]/g,
    /^ {0,3}\[[^\]\n]+\]:[^\n]*$/gm,
    /\[(?:(?:arxiv:|doi:|paper:|Z\d)[^\]\n]*|\d+)\]/gi,
    /(?:[a-z][a-z\d+.-]*:\/\/|www\.)[^\s<>]+/gi,
    /<[^>]*>/g,
    /\\[^\s]|[*_~#>|=[\]{}]+/g,
    /^(?:[ \t]*[-+] |[ \t]*\d+[.)] |:::toggle[+-]?|:::$)/gm,
  ];
  return [...ranges, ...patterns.flatMap(pattern => Array.from(text.matchAll(pattern), m => [m.index!, m.index! + m[0].length] as [number, number]))];
}

export function findGlossaryMatches(text: string, entries: GlossaryEntry[]): GlossaryMatch[] {
  if (!text || !entries.length) return [];
  const occupied = new Uint8Array(text.length);
  for (const [start, end] of excludedRanges(text)) occupied.fill(1, start, end);
  const candidates: GlossaryMatch[] = [];
  for (const entry of entries) {
    const term = entry.term.trim();
    if (!term) continue;
    const pattern = new RegExp(escapeRegex(term).replace(/\s+/g, "\\s+"), "giu");
    for (const match of text.matchAll(pattern)) {
      const start = match.index!;
      const end = start + match[0].length;
      const before = Array.from(text.slice(Math.max(0, start - 2), start)).slice(-1)[0] ?? "";
      const after = Array.from(text.slice(end, end + 2))[0] ?? "";
      if (/[\p{L}\p{N}\p{M}_]/u.test(before) || /[\p{L}\p{N}\p{M}_]/u.test(after)) continue;
      if (occupied.subarray(start, end).some(Boolean)) continue;
      candidates.push({ start, end, entry });
    }
  }
  // Resolve overlaps by length first, including a longer phrase starting later.
  candidates.sort((a, b) => (b.end - b.start) - (a.end - a.start) || a.start - b.start);
  const result: GlossaryMatch[] = [];
  for (const candidate of candidates) {
    if (!occupied.subarray(candidate.start, candidate.end).some(Boolean)) {
      result.push(candidate);
      occupied.fill(1, candidate.start, candidate.end);
    }
  }
  return result.sort((a, b) => a.start - b.start);
}
