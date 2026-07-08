import { api } from "../api";
import { isGreySourcePaperId } from "../citationColors";
import type { CSSProperties } from "react";
import type { Answer, CitationLink, ClaimCheckResult, VerificationSource } from "../types";
import type { AssistantAnswerBlock, AssistantTurn, CitationMeta, CitationInsertExtras } from "./AssistantPage";

// Pure helpers (citation parsing/scoring, text analysis, note formatting, answer
// shaping) extracted from AssistantPage.tsx. No JSX, no component state; safe to
// import anywhere. Re-exported from AssistantPage for backward compatibility.

export function answerLimitFor(question: string, mode: string, scopedPaperCount = 0) {
  if (mode !== "auto") {
    return Number(mode);
  }
  const terms = textTerms(question);
  if (scopedPaperCount === 0 && (question.length > 120 || terms.length > 12)) {
    return 25;
  }
  if (scopedPaperCount > 1) {
    return Math.min(25, Math.max(20, scopedPaperCount * 4));
  }
  if (question.length > 180 || terms.length > 18) {
    return 25;
  }
  if (question.length > 90 || terms.length > 10) {
    return 20;
  }
  return 20;
}


export function evidenceLocationUncertain(
  source: VerificationSource | null | undefined,
  evidence: VerificationSource["evidence"][number] | null | undefined
): boolean {
  if (!source || !evidence || isGreySourcePaperId(source.paper_id) || !source.pdf_available) {
    return false;
  }
  if (!evidence.found_in_pdf_text) {
    return true;
  }
  const located = ((evidence.metadata ?? {}) as Record<string, unknown>)["located"];
  return located === "approx_region" || located === "term_overlap_only";
}


export async function verificationSourcesFor(payload: Answer): Promise<VerificationSource[]> {
  // The pdf_if_fits path already ships a full verification report with the answer —
  // re-verifying via /sources/verify-answer would parse the PDFs a second time and is
  // the main reason highlighting the Textstellen felt slow.
  const embedded = payload.source_verification as { sources?: VerificationSource[] } | null | undefined;
  const embeddedSources = Array.isArray(embedded?.sources) ? embedded.sources : [];
  if (embeddedSources.length && embeddedSources.every((source) => Array.isArray(source.evidence))) {
    return embeddedSources;
  }
  const report = await api.verifyAnswer(payload, verificationLimits(payload));
  return report.sources;
}


export function verificationLimits(answer: Answer) {
  const sourceCount = Math.max(1, answer.sources.length);
  const evidenceCount = Math.max(answer.evidence.length, sourceCount * 4);
  return {
    max_sources: Math.min(25, Math.max(12, sourceCount + 4)),
    max_evidence_per_source: Math.min(20, Math.max(8, Math.ceil(evidenceCount / sourceCount) + 2))
  };
}


// Zitat-IDs sind intern ``cite_<hash>``. Im Roh-Markdown-Token wird das ``cite_``-Präfix
// weggelassen, damit der Link im Editor kompakt bleibt (``skg://c/<hash>``); beim Auflösen
// (parseMarkdownCitationRefs / renderInline) wird es wieder ergänzt. decodeCitationId ist
// idempotent, sodass das alte Schema (mit vollem ``cite_...``) unverändert weiter geparst wird.
export function encodeCitationId(id: string): string {
  return id.replace(/^cite_/, "");
}

export function decodeCitationId(short: string): string {
  const trimmed = short.trim();
  return trimmed.startsWith("cite_") ? trimmed : `cite_${trimmed}`;
}

/**
 * Kompaktes Sammel-Token für Zitate: rendert in den Notizen als EIN aufklappbarer
 * Quellen-Chip am Ende des Zitats (siehe CitationGroupButton / renderInline), statt die
 * Quellen als eigene Fließtext-Zeile darunter zu setzen und den Schreibfluss zu stören.
 * Format: ``[{n} Quellen](skg://c/{hash1,hash2,...})`` (IDs ohne ``cite_``-Präfix). Das
 * ältere Schema ``sciencekg://citations/{cite_id,...}`` wird beim Parsen weiterhin erkannt.
 */
export function citationGroupToken(citationIds: string[]): string {
  const unique = Array.from(new Set(citationIds.filter(Boolean)));
  if (!unique.length) {
    return "";
  }
  const label = unique.length === 1 ? "1 Quelle" : `${unique.length} Quellen`;
  return `[${label}](skg://c/${unique.map(encodeCitationId).join(",")})`;
}


export function formatNoteQuote(quote: string, _source: VerificationSource, _evidenceIndex: number, citationId: string) {
  const text = cleanCitationText(quote);
  const token = citationGroupToken([citationId]);
  return token ? `> ${text} ${token}` : `> ${text}`;
}

/** Wie formatNoteQuote, aber mit ALLEN Quellen des Satzes im Sammel-Chip. */


export function formatNoteQuoteMulti(
  quote: string,
  entries: Array<{ source: VerificationSource; evidenceIndex: number; citationId: string }>
) {
  const text = cleanCitationText(quote);
  const token = citationGroupToken(entries.map((entry) => entry.citationId));
  return token ? `> ${text} ${token}` : `> ${text}`;
}


export function noteCitation(
  source: VerificationSource,
  evidence: VerificationSource["evidence"][number],
  evidenceIndex: number,
  quote = ""
) {
  // The inserted quote is what the user saw and saved; persisting it as the
  // citation's reference_text guarantees that clicking the citation later shows
  // the same passage instead of an unrelated evidence excerpt.
  const quoteText = cleanCitationText(meaningfulQuote(quote));
  const referenceText = quoteText || cleanCitationText(evidence.reference_text);
  return {
    id: stableCitationId(source, evidence, evidenceIndex, referenceText),
    paper_id: source.paper_id,
    title: source.title,
    kind: evidence.kind,
    evidence_id: evidence.evidence_id ?? null,
    reference_text: referenceText,
    pdf_excerpt: cleanCitationText(evidence.pdf_excerpt),
    evidence_index: evidenceIndex
  };
}


export function formatAnswerForNote(answer: Answer, verification: VerificationSource[]) {
  const citations = new Map<string, Record<string, unknown>>();
  const markdown = answer.answer.replace(/\[([^\]]+)\]/g, (match, rawCitation: string, offset: number, fullText: string) => {
    const context = fullText.slice(Math.max(0, offset - 350), Math.min(fullText.length, offset + match.length + 350));
    const metas = citationMetasFor(verification, rawCitation, context, answer.citation_links ?? [], offset);
    if (!metas.length) {
      return match;
    }
    const ids = metas.map((meta) => {
      const evidence = meta.source.evidence[meta.evidenceIndex];
      const citation = noteCitation(meta.source, evidence, meta.evidenceIndex);
      citations.set(String(citation.id), citation);
      return String(citation.id);
    });
    return citationGroupToken(ids);
  });
  return { markdown, citations: Array.from(citations.values()) };
}


export function stableCitationId(source: VerificationSource, evidence: VerificationSource["evidence"][number], evidenceIndex: number, referenceText?: string) {
  return `cite_${stableHash([source.paper_id, evidence.evidence_id ?? evidenceIndex, referenceText ?? evidence.reference_text, evidence.pdf_excerpt].join("|"))}`;
}


export function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}


export function turnBlocks(turn: AssistantTurn): AssistantAnswerBlock[] {
  if (turn.blocks?.length) {
    return turn.blocks;
  }
  return [
    {
      id: `${turn.id}:root`,
      question: turn.question,
      answer: turn.answer,
      verification: turn.verification,
      createdAt: turn.createdAt
    }
  ];
}


export function mergeVerification(sources: VerificationSource[]) {
  const merged = new Map<string, VerificationSource>();
  for (const source of sources) {
    const existing = merged.get(source.paper_id);
    if (!existing) {
      merged.set(source.paper_id, { ...source, evidence: [...source.evidence] });
      continue;
    }
    const seenEvidence = new Set(existing.evidence.map((item) => `${item.kind}|${item.reference_text}|${item.pdf_excerpt}`));
    const nextEvidence = [...existing.evidence];
    for (const evidence of source.evidence) {
      const key = `${evidence.kind}|${evidence.reference_text}|${evidence.pdf_excerpt}`;
      if (!seenEvidence.has(key)) {
        seenEvidence.add(key);
        nextEvidence.push(evidence);
      }
    }
    merged.set(source.paper_id, { ...existing, evidence: nextEvidence });
  }
  return Array.from(merged.values());
}


export function turnContext(turn: AssistantTurn) {
  return turnBlocks(turn)
    .flatMap((block) => [
      { role: "user", content: block.question },
      { role: "assistant", content: block.answer.answer }
    ])
    .slice(-8);
}


export function cleanCitationText(value: string) {
  return dedupeRepeatedText(value)
    .replace(/\b(?:authors?|author names?)\s*:\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}


export function cleanAnswerQuote(value: string) {
  return value
    .replace(/\[[^\]]+\]/g, "")
    .replace(/\s+/g, " ")
    .replace(/^\s*[-*]\s+/, "")
    .trim();
}


export function stripHighlightMarkers(value: string) {
  return value.replace(/^==([\s\S]*)==$/m, "$1");
}


export function dedupeRepeatedText(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  if (!text) {
    return "";
  }
  const half = Math.floor(text.length / 2);
  const left = text.slice(0, half).trim();
  const right = text.slice(half).trim();
  if (left && normalizeSentence(left) === normalizeSentence(right)) {
    return left;
  }
  const sentences = text.match(/[^.!?]+[.!?]+(?:\s|$)|[^.!?]+$/g) ?? [text];
  const output: string[] = [];
  for (const sentence of sentences.map((item) => item.trim()).filter(Boolean)) {
    const previous = output[output.length - 1];
    if (previous && normalizeSentence(previous) === normalizeSentence(sentence)) {
      continue;
    }
    output.push(sentence);
  }
  return output.join(" ").trim();
}


export function normalizeSentence(value: string) {
  return value.toLowerCase().replace(/[\s.?!;:,]+/g, " ").trim();
}


export function rewriteInstruction(mode: string) {
  if (mode === "kuerzer") {
    return "Kuerze den Text, behalte alle fachlichen Aussagen und vorhandenen Zitationsmarker.";
  }
  if (mode === "wissenschaftlich") {
    return "Formuliere den Text wissenschaftlicher, praezise und ohne neue Fakten.";
  }
  return "Schreibe den Text klarer und fluessiger um, ohne neue Fakten oder Zitate hinzuzufuegen.";
}


export function claimVerdictLabel(verdict: ClaimCheckResult["verdict"]) {
  switch (verdict) {
    case "supported":
      return "Gestützt durch die Quelle";
    case "partially_supported":
      return "Teilweise gestützt";
    case "not_supported":
      return "Nicht durch die Quelle gestützt";
    default:
      return "Nicht beurteilbar (zu wenig Quelltext)";
  }
}


export function claimVerdictClass(verdict: ClaimCheckResult["verdict"]) {
  switch (verdict) {
    case "supported":
      return "claim-check--ok";
    case "partially_supported":
      return "claim-check--warn";
    case "not_supported":
      return "claim-check--bad";
    default:
      return "claim-check--unknown";
  }
}


export function isCitationPart(value: string | undefined) {
  return Boolean(value && /^\[[^\]]+\]$/.test(value));
}

/**
 * Split a text part (between citation brackets) into sentence segments and flag the
 * ones no citation is adjacent to. The sentence directly before a bracket is covered
 * by that bracket; the fragment directly after a bracket still belongs to the cited
 * sentence. Everything substantial in between has no Quellenangabe.
 */


export function uncitedTextSegments(
  part: string,
  prevIsCitation: boolean,
  nextIsCitation: boolean
): Array<{ text: string; uncited: boolean }> {
  if (!part) {
    return [];
  }
  const boundaries: number[] = [];
  for (let index = 0; index < part.length; index += 1) {
    if (".!?".includes(part[index]) && isSentenceBoundary(part, index)) {
      boundaries.push(index);
    }
  }
  const segments: Array<{ text: string; first: boolean; last: boolean }> = [];
  let cursor = 0;
  for (const boundary of boundaries) {
    segments.push({ text: part.slice(cursor, boundary + 1), first: cursor === 0, last: false });
    cursor = boundary + 1;
  }
  if (cursor < part.length) {
    segments.push({ text: part.slice(cursor), first: cursor === 0, last: true });
  } else if (segments.length) {
    segments[segments.length - 1].last = true;
  }
  return segments.map((segment) => {
    const coveredByPrev = segment.first && prevIsCitation;
    const coveredByNext = segment.last && nextIsCitation;
    return { text: segment.text, uncited: !coveredByPrev && !coveredByNext && isSubstantialStatement(segment.text) };
  });
}

/** Headings, bullets-only fragments and short connectors are not "Aussagen". */


export function isSubstantialStatement(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  if (text.length < 30 || /^#{1,6}\s/.test(value.trim())) {
    return false;
  }
  const words = text.split(/\s+/).filter((word) => /[\p{L}]{2,}/u.test(word));
  return words.length >= 5;
}


export function citationHoverTextRange(parts: string[], index: number, hoverKey: string) {
  const previousCitationKey = `${parts[index - 1] ?? ""}-${index - 1}`;
  const nextCitationKey = `${parts[index + 1] ?? ""}-${index + 1}`;
  if (sameCitationHighlightKey(nextCitationKey, hoverKey)) {
    return trailingSentenceRange(parts[index] ?? "");
  }
  if (sameCitationHighlightKey(previousCitationKey, hoverKey)) {
    return leadingSentenceRange(parts[index] ?? "");
  }
  return null;
}


export function sameCitationHighlightKey(baseKey: string, candidateKey: string) {
  return candidateKey === baseKey || candidateKey.startsWith(`${baseKey}-`);
}


export function trailingSentenceRange(value: string) {
  const end = value.trimEnd().length;
  if (end <= 0) {
    return null;
  }
  let start = 0;
  const blockBoundary = value.slice(0, end).lastIndexOf("\n\n");
  if (blockBoundary >= 0) {
    start = blockBoundary + 2;
  }
  for (const match of value.slice(0, end).matchAll(/[.!?]\s+/g)) {
    start = Math.max(start, match.index + match[0].length);
  }
  while (start < end && /\s/.test(value[start])) {
    start += 1;
  }
  return hasSemanticText(value.slice(start, end)) ? { start, end } : null;
}


export function leadingSentenceRange(value: string) {
  const start = value.length - value.trimStart().length;
  if (start >= value.length) {
    return null;
  }
  const text = value.slice(start);
  const stop = text.search(/[.!?](?:\s|$)|\n{2,}/);
  const end = stop >= 0 ? start + stop + 1 : value.length;
  return hasSemanticText(value.slice(start, end)) ? { start, end } : null;
}


export function hasSemanticText(value: string) {
  return /[\p{L}\p{N}]/u.test(value);
}


export function shortCitationLabel(value: string) {
  const clean = value.replace(/^https?:\/\/arxiv\.org\/abs\//, "arxiv:").trim();
  if (clean.length <= 22) {
    return clean;
  }
  return `${clean.slice(0, 20)}…`;
}


export function shortTitle(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length <= 56 ? text : `${text.slice(0, 53)}...`;
}


export function citationContext(parts: string[], citationIndex: number) {
  const before = (parts[citationIndex - 1] ?? "").slice(-560);
  const after = (parts[citationIndex + 1] ?? "").slice(0, 140);
  return `${before} ${after}`.trim();
}


export function citationQuoteFromParts(parts: string[], citationIndex: number) {
  const before = parts.slice(0, citationIndex).join("");
  const after = parts.slice(citationIndex + 1).join("");
  const beforeFragment = trailingSentenceFragment(before);
  const beforeIsComplete = /[.!?]\s*$/.test(beforeFragment);
  const combined = beforeIsComplete ? beforeFragment : `${beforeFragment}${leadingSentenceFragment(after)}`;
  return cleanAnswerQuote(combined);
}

/**
 * Nur der Textabschnitt, den DIESES Zitat belegt: vom vorigen Zitat-Bracket
 * (bzw. Satzanfang) bis zum Bracket. `parts` wechselt Text/Bracket ab, daher ist
 * `parts[citationIndex - 1]` genau der Text seit dem letzten Bracket.
 */


export function citationSegmentFromParts(parts: string[], citationIndex: number) {
  const previous = parts[citationIndex - 1] ?? "";
  if (isCitationPart(previous)) {
    return "";
  }
  return cleanAnswerQuote(trailingSentenceFragment(previous));
}

/**
 * Indizes aller weiteren Zitat-Brackets, die zum selben Satz gehören (kein
 * Satzende zwischen ihnen und dem Bracket bei `citationIndex`).
 */


export function sentenceSiblingCitationIndexes(parts: string[], citationIndex: number): number[] {
  const result: number[] = [];
  const hasBoundary = (text: string) => {
    for (let index = 0; index < text.length; index += 1) {
      if (".!?".includes(text[index]) && isSentenceBoundary(text, index)) {
        return true;
      }
    }
    return false;
  };
  for (let index = citationIndex - 1; index >= 0; index -= 1) {
    const part = parts[index] ?? "";
    if (isCitationPart(part)) {
      result.push(index);
      continue;
    }
    if (hasBoundary(part)) {
      break;
    }
  }
  for (let index = citationIndex + 1; index < parts.length; index += 1) {
    const part = parts[index] ?? "";
    if (isCitationPart(part)) {
      result.push(index);
      continue;
    }
    if (hasBoundary(part)) {
      break;
    }
  }
  return result.sort((left, right) => left - right);
}

// Tokens before a period that do NOT end a sentence ("vs.", "z. B.", "Abb. 3", "et al.").


export const SENTENCE_ABBREVIATIONS = new Set([
  "vs", "bzw", "ca", "etc", "et", "al", "z", "b", "u", "a", "d", "h", "i", "e", "o", "ä",
  "nr", "no", "fig", "abb", "tab", "s", "p", "pp", "vol", "ed", "eds", "resp", "approx",
  "vgl", "ggf", "evtl", "inkl", "max", "min", "sog", "str", "jr", "dr", "prof", "st"
]);

/**
 * True when `[.!?]` at `index` really ends a sentence. Guards against the truncation
 * bug where quotes collapsed to fragments like "10)." or "8% vs. 5% vs. 8%).":
 * a period after an abbreviation, between digits, or inside an open parenthesis is
 * punctuation inside the sentence, not its end.
 */


export function isSentenceBoundary(text: string, index: number) {
  const char = text[index];
  if (!char || !".!?".includes(char)) {
    return false;
  }
  if (char === ".") {
    const before = text.slice(0, index);
    const wordMatch = /([\p{L}\p{N}%]+)$/u.exec(before);
    const word = wordMatch ? wordMatch[1] : "";
    if (word && SENTENCE_ABBREVIATIONS.has(word.toLowerCase().replace(/[^a-zä-ü]/g, ""))) {
      return false;
    }
    // Single letters ("z.", "B.") and pure numbers ("3." / "2.5") rarely end sentences.
    if (/^[\p{L}]$/u.test(word)) {
      return false;
    }
    const after = text.slice(index + 1).trimStart();
    if (/^\d/.test(text.slice(index + 1)) || /^[a-zäöüß,;)]/.test(after)) {
      return false;
    }
  }
  // A boundary inside an unclosed parenthesis is part of a parenthetical, not the end.
  let depth = 0;
  for (let i = 0; i < index; i += 1) {
    if (text[i] === "(") depth += 1;
    else if (text[i] === ")") depth = Math.max(0, depth - 1);
  }
  return depth === 0;
}


export function trailingSentenceFragment(value: string) {
  const trimmed = value.trimEnd();
  let start = -1;
  for (const boundary of ["\n\n", "\n- ", "\n* "]) {
    start = Math.max(start, trimmed.lastIndexOf(boundary));
  }
  for (const match of trimmed.matchAll(/[.!?](?=\s)/g)) {
    const index = match.index ?? -1;
    if (index > start && isSentenceBoundary(trimmed, index)) {
      start = index;
    }
  }
  const offset = start >= 0 ? start + 1 : 0;
  return trimmed.slice(offset).replace(/^\s*[-*]\s+/, "").trimStart();
}


export function leadingSentenceFragment(value: string) {
  let trimmed = value.trimStart();
  if (/^[.!?]/.test(trimmed)) {
    return trimmed[0];
  }
  const paragraphStop = trimmed.search(/\n{2,}/);
  if (paragraphStop >= 0) {
    trimmed = trimmed.slice(0, paragraphStop);
  }
  for (const match of trimmed.matchAll(/[.!?](?=\s|$)/g)) {
    const index = match.index ?? -1;
    if (index >= 0 && isSentenceBoundary(trimmed, index)) {
      return trimmed.slice(0, index + 1);
    }
  }
  return paragraphStop >= 0 ? trimmed : "";
}

/**
 * Quote text is only worth storing when it carries real content. Fragments such as
 * "10)." slipped through when sentence detection failed — callers fall back to the
 * evidence excerpt instead of persisting a meaningless fragment.
 */


export function meaningfulQuote(quote: string) {
  const text = String(quote || "").trim();
  if (!text) {
    return "";
  }
  const letters = text.replace(/[^\p{L}]/gu, "");
  const words = text.split(/\s+/).filter((word) => /[\p{L}]{2,}/u.test(word));
  return letters.length >= 12 && words.length >= 3 ? text : "";
}


export function citationIds(citation: string) {
  return citation
    .replace(/\s+(?:and|und)\s+/gi, ",")
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}


export function citationMetasFor(
  pool: VerificationSource[],
  citation: string,
  context = "",
  links: CitationLink[] = [],
  citationStart?: number
): CitationMeta[] {
  const metas: CitationMeta[] = [];
  const seen = new Set<string>();
  for (const candidate of citationIds(citation)) {
    const source = pool.find((item) => sameCitation(item.paper_id, candidate));
    if (!source || seen.has(source.paper_id)) {
      continue;
    }
    seen.add(source.paper_id);
    // A claim located to several PDF passages ships one link per fragment, all at the
    // same citation offset — resolve every fragment so each Belegstelle gets a chip.
    const occurrenceLinks =
      typeof citationStart === "number"
        ? links.filter((link) => sameCitation(link.paper_id, candidate) && link.citation_start === citationStart)
        : [];
    const resolvedLinks = occurrenceLinks.length
      ? occurrenceLinks
      : (() => {
          const best = bestCitationLink(links, candidate, citationStart, context);
          return best ? [best] : [];
        })();
    const seenIndices = new Set<number>();
    let resolvedAny = false;
    for (const link of resolvedLinks) {
      const linkedIndex = evidenceIndexForCitationLink(source, link, context);
      if (linkedIndex < 0 || seenIndices.has(linkedIndex)) {
        continue;
      }
      seenIndices.add(linkedIndex);
      resolvedAny = true;
      metas.push({
        source,
        evidenceIndex: linkedIndex,
        evidenceId: source.evidence[linkedIndex]?.evidence_id,
        // Verification uncertainty (not found in PDF / fuzzy region) marks the chip
        // too, so confidently-but-wrongly matched citations get warned proactively.
        approximate: Boolean(link.approximate) || evidenceLocationUncertain(source, source.evidence[linkedIndex])
      });
    }
    if (!resolvedAny) {
      const evidenceIndex = bestEvidenceIndex(source, context);
      metas.push({
        source,
        evidenceIndex,
        evidenceId: source.evidence[evidenceIndex]?.evidence_id,
        approximate:
          Boolean(resolvedLinks[0]?.approximate) || evidenceLocationUncertain(source, source.evidence[evidenceIndex])
      });
    }
  }
  return metas;
}


export function sameCitation(sourceId: string, citation: string) {
  const left = normalizeCitation(sourceId);
  const right = normalizeCitation(citation);
  return left === right || left.endsWith(right) || right.endsWith(left);
}


export function normalizeCitation(value: string) {
  return value
    .toLowerCase()
    .replace(/^https?:\/\/arxiv\.org\/abs\//, "arxiv:")
    .replace(/v\d+$/, "")
    .replace(/\s+/g, "");
}


export function bestEvidenceIndex(source: VerificationSource, context: string) {
  if (!source.evidence.length) {
    return 0;
  }
  const scores = source.evidence.map((evidence, index) => ({ index, score: evidenceContextScore(evidence, context) }));
  scores.sort((left, right) => right.score - left.score || left.index - right.index);
  return scores[0]?.score > 0 ? scores[0].index : 0;
}


export function bestCitationLink(links: CitationLink[], citation: string, citationStart: number | undefined, context: string) {
  const candidates = links.filter((link) => sameCitation(link.paper_id, citation));
  if (!candidates.length) {
    return null;
  }
  return candidates
    .map((link) => ({
      link,
      score:
        (typeof citationStart === "number" && link.citation_start === citationStart ? 1000 : 0) +
        (typeof citationStart === "number" ? Math.max(0, 120 - Math.abs((link.citation_start ?? -99999) - citationStart)) : 0) +
        evidenceLinkContextScore(link, context)
    }))
    .sort((left, right) => right.score - left.score)[0].link;
}


export function evidenceIndexForCitationLink(source: VerificationSource, link: CitationLink, context: string) {
  const evidenceId = link.evidence_id ?? "";
  if (evidenceId) {
    const candidates = source.evidence
      .map((evidence, index) => ({ evidence, index }))
      .filter((item) => item.evidence.evidence_id === evidenceId);
    if (candidates.length) {
      return bestEvidenceIndexFromCandidates(candidates, context);
    }
  }
  if (typeof link.evidence_index === "number") {
    const sourceIndex = source.evidence.findIndex((evidence) => evidence.source_evidence_index === link.evidence_index);
    if (sourceIndex >= 0) {
      return sourceIndex;
    }
  }
  return -1;
}


export function bestEvidenceIndexFromCandidates(candidates: Array<{ evidence: VerificationSource["evidence"][number]; index: number }>, context: string) {
  return candidates
    .map((item) => ({ index: item.index, score: evidenceContextScore(item.evidence, context) }))
    .sort((left, right) => right.score - left.score || left.index - right.index)[0].index;
}


export function evidenceContextScore(evidence: VerificationSource["evidence"][number], context: string) {
  const terms = textTerms(context);
  const quantitative = quantitativeTokens(context);
  if (!terms.length && !quantitative.size) {
    return 0;
  }
  const targetText = `${evidence.reference_text} ${evidence.pdf_excerpt} ${(evidence.matched_terms ?? []).join(" ")}`;
  const target = normalizeText(targetText);
  const targetNumbers = quantitativeTokens(targetText);
  let score = terms.reduce((total, term) => total + (target.includes(term) ? 1 : 0), 0);
  if (quantitative.size) {
    let matchedNumbers = 0;
    quantitative.forEach((token) => {
      if (targetNumbers.has(token)) {
        matchedNumbers += 1;
      }
    });
    score += matchedNumbers * 6;
    if (!matchedNumbers) {
      score -= 6;
    }
  }
  if (evidence.kind === "claim") {
    score += 5;
  } else if (evidence.kind === "paper") {
    score -= 4;
  }
  return score;
}


export function evidenceLinkContextScore(link: CitationLink, context: string) {
  const terms = textTerms(`${context} ${link.context ?? ""}`);
  const target = normalizeText(link.context ?? "");
  return terms.reduce((total, term) => total + (target.includes(term) ? 1 : 0), 0);
}


export function quantitativeTokens(text: string) {
  const tokens = new Set<string>();
  for (const match of text.match(/\d+(?:\.\d+)?\s*%?/g) ?? []) {
    const clean = match.replace(/\s+/g, "");
    if (clean) {
      tokens.add(clean.toLowerCase());
      tokens.add(clean.replace(/%$/, "").toLowerCase());
    }
  }
  return tokens;
}


export function textTerms(text: string) {
  const stopwords = new Set(["about", "after", "also", "and", "are", "based", "from", "have", "into", "not", "that", "the", "their", "this", "used", "with"]);
  return Array.from(new Set(normalizeText(text).split(" "))).filter((term) => term.length >= 5 && !stopwords.has(term)).slice(0, 36);
}


export function shortEvidenceText(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > 92 ? `${text.slice(0, 89)}...` : text;
}


export function normalizeText(text: string) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}-]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}
