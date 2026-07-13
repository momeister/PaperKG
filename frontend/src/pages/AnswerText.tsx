// AnswerText + EvidenceVerificationBadge — aus AssistantPage.tsx extrahiert.
// AssistantPage re-exportiert beide (Konsumenten unveraendert). Typ-Importe aus
// der Page sind type-only (zyklusfrei).
import { useEffect, useMemo, useRef, useState } from "react";
import type { CSSProperties, FormEvent, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bold,
  Bot,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Code,
  Download,
  FilePlus2,
  Globe,
  HelpCircle,
  Highlighter,
  Italic,
  Link,
  List,
  ListChecks,
  Loader2,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Quote,
  Send,
  ShieldCheck,
  Table2,
  WandSparkles,
  X,
  XCircle
} from "lucide-react";

import { api } from "../api";
import { colorVarsForPaperId, evidenceColorVars, isGreySourcePaperId } from "../citationColors";
import { EmptyState } from "../components/EmptyState";
import { PdfPane } from "../components/PdfPane";
import { Status } from "../components/Status";
import { TextareaHighlightLayer } from "../components/TextareaHighlightLayer";
import { downloadMarkdownFile } from "../download";
import { noteProjectId } from "../projectScope";
import { useAppState, useOptionalAppState } from "../state";
import type { Answer, CitationLink, ClaimCheckResult, DeepResearchFinding, Note, ResearchNode, VerificationSource } from "../types";
import {
  loadAssistantSession,
  slimTurnForPersist,
  saveAssistantSession,
  fetchAssistantSession,
  loadNotes,
  saveNotes,
  noteTitleForSave,
  formatTurnTime,
} from "./assistantSession";

// Session/note persistence lives in ./assistantSession; re-export the public
// surface so existing importers (WorkspacePage, tests) keep working unchanged.
import {
  answerLimitFor,
  citationContext,
  citationHoverTextRange,
  citationMetasFor,
  citationQuoteFromParts,
  citationSegmentFromParts,
  claimVerdictClass,
  claimVerdictLabel,
  cleanAnswerQuote,
  formatAnswerForNote,
  formatNoteQuote,
  isCitationPart,
  meaningfulQuote,
  mergeVerification,
  noteCitation,
  rewriteInstruction,
  sentenceSiblingCitationIndexes,
  shortCitationLabel,
  shortEvidenceText,
  stripHighlightMarkers,
  turnBlocks,
  turnContext,
  uncitedTextSegments,
  verificationSourcesFor,
} from "./assistantHelpers";
import type {
  AssistantAnswerBlock,
  AssistantTurn,
  CitationInsertExtras,
  CitationMeta,
} from "./AssistantPage";

export function EvidenceVerificationBadge({
  source,
  evidence
}: {
  source: VerificationSource | null | undefined;
  evidence: VerificationSource["evidence"][number] | null | undefined;
}) {
  if (!source || !evidence || isGreySourcePaperId(source.paper_id)) {
    return null;
  }
  if (!source.pdf_available) {
    return <span className="evidence-verify-badge">Kein lokales PDF – Textstelle nicht prüfbar</span>;
  }
  if (!evidence.found_in_pdf_text) {
    return <span className="evidence-verify-badge">Textstelle nicht im PDF verifiziert</span>;
  }
  const metadata = (evidence.metadata ?? {}) as Record<string, unknown>;
  if (metadata.context_policy === "approx_region" || metadata.located === "approx_region") {
    return <span className="evidence-verify-badge">Ungefährer Bereich – genaue Textstelle nicht gefunden</span>;
  }
  if (metadata.located === "term_overlap_only") {
    return <span className="evidence-verify-badge">Ungefähre Stelle – nur thematisch verortet, kein wörtlicher Anker</span>;
  }
  if (metadata.context_policy === "whole") {
    return <span className="evidence-verify-badge">Ungefähre Stelle – Zitat nicht satzgenau lokalisiert</span>;
  }
  return null;
}

/**
 * True when a citation's located evidence should be treated as uncertain: the passage was
 * not verified in the PDF, or it was only fuzzily located (approx region / plain term
 * overlap). Sources without a local PDF are excluded — there is nothing to check against,
 * and the badge already says so.
 */
export function AnswerText({
  answer,
  onCitationClick,
  onCitationMetaClick,
  onUnresolvedCitationClick,
  getCitationMeta,
  activeCitation,
  onCitationInsert,
  onCitationInsertPreview,
  onCitationInsertPreviewClear,
  onClaimRemove,
  onCitationRemove,
  onClaimEvidenceUpdate,
  onClaimReformulate,
  autoVerifyUncertain = false,
  markUncited = false
}: {
  answer: string;
  citationLinks?: CitationLink[];
  onCitationClick: (citation: string, context?: string, quote?: string, citationStart?: number) => void;
  // Preferred click target: receives the chip's own meta, so a bracket with several
  // fragment chips (Z3, Z7) jumps to the clicked fragment instead of re-resolving to
  // the first one.
  onCitationMetaClick?: (meta: CitationMeta, context?: string, quote?: string) => void;
  onUnresolvedCitationClick?: (citationId: string) => void;
  getCitationMeta: (citation: string, context?: string, citationStart?: number) => CitationMeta[] | CitationMeta | null;
  activeCitation?: { paperId: string; evidenceIndex: number };
  onCitationInsert?: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) => void;
  onCitationInsertPreview?: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) => void;
  onCitationInsertPreviewClear?: () => void;
  /** Nachcheck ergab "nicht gestützt": Aussage (Satz + Zitat) aus dem Antworttext entfernen. */
  onClaimRemove?: (statement: string) => void;
  /** Nachcheck ergab "nicht gestützt": nur dieses Zitat entfernen — und den Satz nur dann,
   *  wenn ihn keine andere Quelle mehr belegt. */
  onCitationRemove?: (paperId: string, statement: string) => void;
  /** Nachcheck fand die echte Belegstelle: Evidence des Zitats aktualisieren. */
  onClaimEvidenceUpdate?: (source: VerificationSource, evidenceIndex: number, quotes: string[], statement: string) => void;
  /** Nachcheck ergab "teilweise gestützt": Aussage an die Quelle anpassen (umformulieren). */
  onClaimReformulate?: (source: VerificationSource, evidenceIndex: number, statement: string, result: ClaimCheckResult) => void;
  /** Unsichere Zuordnungen (approximate) sofort automatisch nachprüfen + Antwort ggf.
   *  korrigieren — statt auf einen Klick auf "Nachchecken" zu warten. */
  autoVerifyUncertain?: boolean;
  /** Underline (dashed) sentences without an adjacent citation so the
   *  "N Aussage(n) ohne Quellenangabe" hint becomes locatable in the text. */
  markUncited?: boolean;
}) {
  const [pinnedCitation, setPinnedCitation] = useState<{
    key: string;
    paperId: string;
    evidenceIndex: number;
  } | null>(null);
  const [expandedCitations, setExpandedCitations] = useState<Set<string>>(new Set());
  const [hoverCitation, setHoverCitation] = useState<{
    key: string;
    label: string;
    title: string;
    subtitle: string;
    text: string;
    evidenceIndex: number;
    source: VerificationSource;
    quote: string;
    segment: string;
    siblings: CitationMeta[];
    approximate: boolean;
    left: number;
    top: number;
    width: number;
  } | null>(null);
  // Nachcheck-Karte: bleibt (anders als die Hover-Karte) offen, bis sie geschlossen
  // wird — per X oder Klick außerhalb.
  const [claimCard, setClaimCard] = useState<{
    key: string;
    left: number;
    top: number;
    width: number;
    statement: string;
    paperId: string;
    title: string;
    source: VerificationSource;
    evidenceIndex: number;
    status: "loading" | "done" | "error";
    result?: ClaimCheckResult;
    error?: string;
  } | null>(null);
  const appState = useOptionalAppState();
  const claimProvider = appState?.provider;
  const claimModel = appState?.model;
  const closeHoverTimerRef = useRef<number | null>(null);

  useEffect(() => {
    if (!claimCard) {
      return;
    }
    const onPointerDown = (event: PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest(".claim-check-card")) {
        setClaimCard(null);
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
  }, [claimCard]);
  const parts = answer.split(/(\[[^\]]+\])/g);
  let renderedOffset = 0;
  const contextCitation = hoverCitation ?? pinnedCitation;
  const contextCitationPaperId = contextCitation
    ? "source" in contextCitation
      ? contextCitation.source.paper_id
      : contextCitation.paperId
    : null;

  useEffect(() => {
    setPinnedCitation((current) => {
      if (!current || !activeCitation) {
        return current;
      }
      return current.paperId === activeCitation.paperId && current.evidenceIndex === activeCitation.evidenceIndex ? current : null;
    });
  }, [activeCitation?.paperId, activeCitation?.evidenceIndex]);

  function cancelCitationHoverClose() {
    if (closeHoverTimerRef.current !== null) {
      window.clearTimeout(closeHoverTimerRef.current);
      closeHoverTimerRef.current = null;
    }
  }

  function scheduleCitationHoverClose() {
    cancelCitationHoverClose();
    closeHoverTimerRef.current = window.setTimeout(() => {
      closeHoverTimerRef.current = null;
      setHoverCitation(null);
    }, 80);
  }

  function showCitationHover(
    key: string,
    label: string,
    meta: CitationMeta,
    quote: string,
    event: ReactPointerEvent<HTMLElement>,
    segment = "",
    siblings: CitationMeta[] = []
  ) {
    const evidence = meta.source.evidence[meta.evidenceIndex];
    if (!evidence) {
      return;
    }
    cancelCitationHoverClose();
    const rect = event.currentTarget.getBoundingClientRect();
    const width = Math.min(360, Math.max(240, window.innerWidth - 24));
    const expectedHeight = Math.min(220, Math.max(120, window.innerHeight - 24));
    const left = Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12));
    const belowTop = rect.bottom + 8;
    const top = belowTop + expectedHeight > window.innerHeight ? Math.max(12, rect.top - expectedHeight - 8) : belowTop;
    setHoverCitation({
      key,
      label,
      title: meta.source.title || meta.source.paper_id,
      subtitle: `${label} - ${evidence.kind || "Evidence"} - ${meta.source.paper_id}`,
      text: evidence.pdf_excerpt || evidence.reference_text,
      evidenceIndex: meta.evidenceIndex,
      source: meta.source,
      quote,
      segment,
      siblings,
      approximate: Boolean(meta.approximate),
      left,
      top,
      width
    });
  }

  async function runClaimCheck(hover: NonNullable<typeof hoverCitation>) {
    const statement = meaningfulQuote(hover.segment) || meaningfulQuote(hover.quote) || hover.quote || hover.segment;
    if (!statement) {
      return;
    }
    const key = hover.key;
    cancelCitationHoverClose();
    setHoverCitation(null);
    setClaimCard({
      key,
      left: hover.left,
      top: hover.top,
      width: Math.max(hover.width, 320),
      statement,
      paperId: hover.source.paper_id,
      title: hover.title,
      source: hover.source,
      evidenceIndex: hover.evidenceIndex,
      status: "loading"
    });
    // Bei claim_excerpt/approx_region wäre reference_text (Fallback von hover.text) der
    // eigene Antwortsatz — als Quellen-Auszug würde er den Judge zirkulär biasen.
    const hoverEvidence = hover.source.evidence[hover.evidenceIndex];
    const hoverMeta = (hoverEvidence?.metadata ?? {}) as Record<string, unknown>;
    const hoverSelfReferential =
      hoverMeta.context_policy === "claim_excerpt" || hoverMeta.context_policy === "approx_region";
    try {
      const res = await api.claimCheck({
        statement,
        paper_ids: [hover.source.paper_id],
        titles: { [hover.source.paper_id]: hover.source.title || "" },
        evidence_texts: {
          [hover.source.paper_id]: hoverEvidence?.pdf_excerpt || (hoverSelfReferential ? "" : hover.text || "")
        },
        provider: claimProvider || undefined,
        model: claimModel || undefined
      });
      setClaimCard((current) =>
        current?.key === key ? { ...current, status: "done", result: res.checks[0] } : current
      );
    } catch (error) {
      setClaimCard((current) =>
        current?.key === key
          ? { ...current, status: "error", error: error instanceof Error ? error.message : "Prüfung fehlgeschlagen" }
          : current
      );
    }
  }

  // Automatischer Nachcheck aller als unsicher markierten Zuordnungen — direkt beim
  // Anzeigen, ohne dass der Nutzer "Nachchecken" drücken muss.
  const uncertainCitations = useMemo(() => {
    type Item = { key: string; source: VerificationSource; evidenceIndex: number; paperId: string; statement: string };
    if (!autoVerifyUncertain) {
      return [] as Item[];
    }
    const collected: Item[] = [];
    const seen = new Set<string>();
    const scanParts = answer.split(/(\[[^\]]+\])/g);
    for (let index = 0; index < scanParts.length; index += 1) {
      const bracket = /^\[([^\]]+)\]$/.exec(scanParts[index]);
      if (!bracket) {
        continue;
      }
      const partStart = scanParts.slice(0, index).reduce((sum, item) => sum + item.length, 0);
      const rawMeta = getCitationMeta(bracket[1], citationContext(scanParts, index), partStart);
      const metas = Array.isArray(rawMeta) ? rawMeta : rawMeta ? [rawMeta] : [];
      const segment = citationSegmentFromParts(scanParts, index);
      const statement = meaningfulQuote(segment) || segment;
      if (!statement || statement.length < 8) {
        continue;
      }
      for (const meta of metas) {
        if (!meta.approximate) {
          continue;
        }
        const key = `${meta.source.paper_id}#${meta.evidenceIndex}`;
        if (seen.has(key)) {
          continue;
        }
        seen.add(key);
        collected.push({ key, source: meta.source, evidenceIndex: meta.evidenceIndex, paperId: meta.source.paper_id, statement });
      }
    }
    return collected;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answer, autoVerifyUncertain]);

  const autoVerifiedRef = useRef<Set<string>>(new Set());
  useEffect(() => {
    if (!autoVerifyUncertain || !uncertainCitations.length) {
      return;
    }
    let cancelled = false;
    void (async () => {
      for (const item of uncertainCitations) {
        if (cancelled) {
          return;
        }
        if (autoVerifiedRef.current.has(item.key)) {
          continue;
        }
        autoVerifiedRef.current.add(item.key);
        const evidence = item.source.evidence[item.evidenceIndex];
        // Bei claim_excerpt/approx_region ist reference_text der eigene Antwortsatz —
        // ihn als Quellen-Auszug mitzugeben würde den Judge zirkulär biasen.
        const evidenceMeta = (evidence?.metadata ?? {}) as Record<string, unknown>;
        const selfReferential =
          evidenceMeta.context_policy === "claim_excerpt" || evidenceMeta.context_policy === "approx_region";
        try {
          const res = await api.claimCheck({
            statement: item.statement,
            paper_ids: [item.paperId],
            titles: { [item.paperId]: item.source.title || "" },
            evidence_texts: { [item.paperId]: evidence?.pdf_excerpt || (selfReferential ? "" : evidence?.reference_text || "") },
            provider: claimProvider || undefined,
            model: claimModel || undefined
          });
          const check = res.checks[0];
          if (cancelled || !check) {
            continue;
          }
          if (check.verdict === "not_supported") {
            onCitationRemove?.(item.paperId, item.statement);
          } else if (check.verdict === "partially_supported") {
            onClaimReformulate?.(item.source, item.evidenceIndex, item.statement, check);
          } else if (check.verdict === "supported" && check.supporting_quotes.length) {
            onClaimEvidenceUpdate?.(item.source, item.evidenceIndex, check.supporting_quotes, item.statement);
          }
        } catch {
          // fail-soft: bleibt über die Hover-Karte manuell prüfbar
        }
      }
    })();
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [uncertainCitations, autoVerifyUncertain]);

  return (
    <span className="answer-text-content" onClick={() => setPinnedCitation(null)}>
      {parts.map((part, index) => {
        const partStart = renderedOffset;
        renderedOffset += part.length;
        const match = /^\[([^\]]+)\]$/.exec(part);
        if (!match) {
          const highlightRange = contextCitation ? citationHoverTextRange(parts, index, contextCitation.key) : null;
          if (highlightRange) {
            return (
              <span key={`${part}-${index}`}>{renderCitationContextPart(part, highlightRange, contextCitation ? colorVarsForPaperId(contextCitationPaperId, contextCitation.evidenceIndex) : undefined)}</span>
            );
          }
          if (markUncited && parts.length > 1) {
            return (
              <span key={`${part}-${index}`}>
                {renderUncitedPart(part, isCitationPart(parts[index - 1]), isCitationPart(parts[index + 1]))}
              </span>
            );
          }
          return <span key={`${part}-${index}`}>{part}</span>;
        }
        const context = citationContext(parts, index);
        const quote = citationQuoteFromParts(parts, index);
        // Nur der Abschnitt, den DIESES Zitat belegt (seit dem vorigen Zitat/Satzanfang),
        // plus die Metas aller weiteren Zitate desselben Satzes — für die Notiz-Übernahme.
        const segment = citationSegmentFromParts(parts, index);
        const siblingMetas: CitationMeta[] = [];
        for (const siblingIndex of sentenceSiblingCitationIndexes(parts, index)) {
          const siblingMatch = /^\[([^\]]+)\]$/.exec(parts[siblingIndex] ?? "");
          if (!siblingMatch) continue;
          const siblingStart = parts.slice(0, siblingIndex).reduce((sum, item) => sum + item.length, 0);
          const raw = getCitationMeta(siblingMatch[1], citationContext(parts, siblingIndex), siblingStart);
          for (const meta of Array.isArray(raw) ? raw : raw ? [raw] : []) {
            if (!siblingMetas.some((existing) => existing.source.paper_id === meta.source.paper_id && existing.evidenceIndex === meta.evidenceIndex)) {
              siblingMetas.push(meta);
            }
          }
        }
        const rawMeta = getCitationMeta(match[1], context, partStart);
        const metas = (Array.isArray(rawMeta) ? rawMeta : rawMeta ? [rawMeta] : []);
        const hoverKey = `${part}-${index}`;
        return (
          <span className="citation-link-wrap" key={hoverKey}>
            {metas.length ? (() => {
              const COLLAPSE_THRESHOLD = 3;
              const isExpanded = expandedCitations.has(hoverKey);
              const visibleMetas = metas.length > COLLAPSE_THRESHOLD && !isExpanded ? metas.slice(0, 2) : metas;
              const hiddenCount = metas.length - visibleMetas.length;
              return (
                <>
                  {visibleMetas.map((meta, metaIndex) => {
                    const label = `Z${meta.evidenceIndex + 1}`;
                    const chipKey = `${hoverKey}-${meta.source.paper_id}-${meta.evidenceIndex}-${metaIndex}`;
                    const isActive = Boolean(activeCitation?.paperId === meta.source.paper_id && activeCitation.evidenceIndex === meta.evidenceIndex);
                    const isGreySource = isGreySourcePaperId(meta.source.paper_id);
                    const chipColorVars = colorVarsForPaperId(meta.source.paper_id, meta.evidenceIndex);
                    return (
                      <button
                        className={`citation-link citation-link--mapped ${isActive ? "citation-link--active" : ""} ${isGreySource ? "citation-link--grey-source" : ""}`}
                        type="button"
                        key={chipKey}
                        onClick={(event) => {
                          event.stopPropagation();
                          cancelCitationHoverClose();
                          setHoverCitation(null);
                          setPinnedCitation((current) =>
                            current?.key === chipKey ? null : { key: chipKey, paperId: meta.source.paper_id, evidenceIndex: meta.evidenceIndex }
                          );
                          if (onCitationMetaClick) {
                            onCitationMetaClick(meta, context, quote);
                          } else {
                            onCitationClick(meta.source.paper_id, context, quote, partStart);
                          }
                        }}
                        onPointerEnter={(event) =>
                          showCitationHover(
                            chipKey,
                            label,
                            meta,
                            quote,
                            event,
                            segment,
                            siblingMetas.concat(metas.filter((other) => other !== meta))
                          )
                        }
                        onPointerLeave={scheduleCitationHoverClose}
                        style={chipColorVars}
                        title={`${meta.source.title || meta.source.paper_id} - Zitat ${meta.evidenceIndex + 1}${meta.approximate ? " - Ungefähre Zuordnung" : ""}`}
                      >
                        <span className="citation-index">{label}</span>
                        {meta.approximate ? <AlertTriangle size={11} className="citation-approx-icon" aria-label="Unsichere Zuordnung" /> : null}
                        <span className="citation-paper">{shortCitationLabel(meta.source.title || meta.source.paper_id)}</span>
                      </button>
                    );
                  })}
                  {!isExpanded && hiddenCount > 0 ? (
                    <button
                      className="citation-link citation-more"
                      type="button"
                      title="Weitere Zitate anzeigen"
                      onClick={(event) => {
                        event.stopPropagation();
                        setExpandedCitations((current) => {
                          const next = new Set(current);
                          next.add(hoverKey);
                          return next;
                        });
                      }}
                    >
                      +{hiddenCount}
                    </button>
                  ) : null}
                  {isExpanded && metas.length > COLLAPSE_THRESHOLD ? (
                    <button
                      className="citation-link citation-more citation-less"
                      type="button"
                      title="Weniger anzeigen"
                      onClick={(event) => {
                        event.stopPropagation();
                        setExpandedCitations((current) => {
                          const next = new Set(current);
                          next.delete(hoverKey);
                          return next;
                        });
                      }}
                    >
                      <ChevronUp size={13} />
                    </button>
                  ) : null}
                </>
              );
            })() : (
              <button
                className="citation-link citation-link--unresolved"
                type="button"
                title={`Quelle nicht verifiziert: ${match[1]}`}
                onClick={(event) => {
                  event.stopPropagation();
                  onUnresolvedCitationClick?.(match[1]);
                }}
              >
                <span className="citation-index">!</span>
                <span className="citation-paper">{shortCitationLabel(match[1])}</span>
              </button>
            )}
            {hoverCitation?.key === hoverKey || hoverCitation?.key.startsWith(`${hoverKey}-`) ? (
              <span
                className="citation-hover-card citation-hover-card--visible"
                role="tooltip"
                onClick={(event) => event.stopPropagation()}
                onPointerEnter={cancelCitationHoverClose}
                onPointerLeave={scheduleCitationHoverClose}
                style={{
                  ...colorVarsForPaperId(hoverCitation.source.paper_id, hoverCitation.evidenceIndex),
                  left: hoverCitation.left,
                  top: hoverCitation.top,
                  width: hoverCitation.width
                }}
              >
                <strong>{hoverCitation.title}</strong>
                <span>{hoverCitation.subtitle}</span>
                <span className="citation-hover-card__legacy" aria-hidden="true">
                  {hoverCitation.label} | {hoverCitation.source.evidence[hoverCitation.evidenceIndex]?.kind || "Evidence"} | {hoverCitation.source.paper_id}
                </span>
                {hoverCitation.approximate ? (
                  <span className="citation-hover-card__warn">
                    <AlertTriangle size={12} /> Unsichere Zuordnung — dieser Beleg passt womöglich nicht zur Aussage. Mit „Nachchecken" prüfen.
                  </span>
                ) : null}
                <p>{hoverCitation.text}</p>
                <span className="citation-hover-card__actions">
                  <button
                    className="button button-compact citation-hover-card__check"
                    type="button"
                    title="Prüfen, ob diese Aussage wirklich von der Quelle gestützt wird"
                    onClick={() => void runClaimCheck(hoverCitation)}
                  >
                    <ShieldCheck size={13} /> Nachchecken
                  </button>
                  {onCitationInsert ? (
                    <button
                      className="button button-compact citation-hover-card__insert"
                      type="button"
                      onMouseEnter={() => onCitationInsertPreview?.(hoverCitation.source, hoverCitation.evidenceIndex, hoverCitation.quote || hoverCitation.text, { segment: hoverCitation.segment, siblings: hoverCitation.siblings })}
                      onMouseOver={() => onCitationInsertPreview?.(hoverCitation.source, hoverCitation.evidenceIndex, hoverCitation.quote || hoverCitation.text, { segment: hoverCitation.segment, siblings: hoverCitation.siblings })}
                      onMouseOut={onCitationInsertPreviewClear}
                      onMouseLeave={onCitationInsertPreviewClear}
                      onPointerEnter={() => onCitationInsertPreview?.(hoverCitation.source, hoverCitation.evidenceIndex, hoverCitation.quote || hoverCitation.text, { segment: hoverCitation.segment, siblings: hoverCitation.siblings })}
                      onPointerOver={() => onCitationInsertPreview?.(hoverCitation.source, hoverCitation.evidenceIndex, hoverCitation.quote || hoverCitation.text, { segment: hoverCitation.segment, siblings: hoverCitation.siblings })}
                      onPointerOut={onCitationInsertPreviewClear}
                      onPointerLeave={onCitationInsertPreviewClear}
                      onFocus={() => onCitationInsertPreview?.(hoverCitation.source, hoverCitation.evidenceIndex, hoverCitation.quote || hoverCitation.text, { segment: hoverCitation.segment, siblings: hoverCitation.siblings })}
                      onBlur={onCitationInsertPreviewClear}
                      onClick={() => {
                        cancelCitationHoverClose();
                        setHoverCitation(null);
                        onCitationInsertPreviewClear?.();
                        onCitationInsert(hoverCitation.source, hoverCitation.evidenceIndex, hoverCitation.quote || hoverCitation.text, {
                          segment: hoverCitation.segment,
                          siblings: hoverCitation.siblings
                        });
                      }}
                    >
                      In Notiz
                    </button>
                  ) : null}
                </span>
              </span>
            ) : null}
            {claimCard?.key === hoverKey || claimCard?.key.startsWith(`${hoverKey}-`) ? (
              <span
                className="citation-hover-card citation-hover-card--visible claim-check-card"
                role="dialog"
                aria-label="Zitat-Nachcheck"
                onClick={(event) => event.stopPropagation()}
                style={{ left: claimCard.left, top: claimCard.top, width: claimCard.width }}
              >
                <span className="claim-check-card__head">
                  <strong>Nachcheck: {claimCard.title}</strong>
                  <button className="icon-button" type="button" aria-label="Schließen" onClick={() => setClaimCard(null)}>
                    <X size={14} />
                  </button>
                </span>
                <span className="claim-check-card__statement">„{claimCard.statement}"</span>
                {claimCard.status === "loading" ? (
                  <span className="claim-check-card__loading">
                    <Loader2 size={14} className="spin" /> Prüfe Aussage gegen die Quelle …
                  </span>
                ) : claimCard.status === "error" ? (
                  <span className="claim-check-card__verdict claim-check--unknown">
                    <HelpCircle size={14} /> {claimCard.error}
                  </span>
                ) : claimCard.result ? (
                  <>
                    <span className={`claim-check-card__verdict ${claimVerdictClass(claimCard.result.verdict)}`}>
                      {claimVerdictIcon(claimCard.result.verdict)} {claimVerdictLabel(claimCard.result.verdict)}
                      {claimCard.result.checked_scope === "whole_paper"
                        ? " (ganzes Paper geprüft)"
                        : claimCard.result.source_origin === "abstract"
                          ? " (nur Abstract geprüft)"
                          : ""}
                    </span>
                    {claimCard.result.explanation ? <p>{claimCard.result.explanation}</p> : null}
                    {claimCard.result.supporting_quotes.slice(0, 2).map((quoteText, quoteIndex) => (
                      <blockquote className="claim-check-card__quote" key={quoteIndex}>
                        {quoteText}
                      </blockquote>
                    ))}
                    {claimCard.result.verdict === "supported" && !claimCard.result.supporting_quotes.length ? (
                      <span className="claim-check-card__hint">
                        Aussage stimmt laut Quelle — die exakte Textstelle konnte aber nicht wörtlich identifiziert werden.
                      </span>
                    ) : null}
                    <span className="claim-check-card__actions">
                      {claimCard.result.verdict === "not_supported" && onCitationRemove ? (
                        <button
                          className="button button-compact claim-check-card__danger"
                          type="button"
                          title="Dieses Zitat entfernen — der Satz bleibt, wenn ihn eine andere Quelle belegt, sonst wird auch er entfernt"
                          onClick={() => {
                            onCitationRemove(claimCard.paperId, claimCard.statement);
                            setClaimCard(null);
                          }}
                        >
                          <XCircle size={13} /> Zitat entfernen
                        </button>
                      ) : null}
                      {claimCard.result.verdict === "not_supported" && onClaimRemove ? (
                        <button
                          className="button button-compact claim-check-card__danger"
                          type="button"
                          title="Ganzen Satz samt aller Zitate aus der Antwort entfernen"
                          onClick={() => {
                            onClaimRemove(claimCard.statement);
                            setClaimCard(null);
                          }}
                        >
                          <XCircle size={13} /> Aussage entfernen
                        </button>
                      ) : null}
                      {(claimCard.result.verdict === "supported" || claimCard.result.verdict === "partially_supported") &&
                      claimCard.result.supporting_quotes.length &&
                      onClaimEvidenceUpdate ? (
                        <button
                          className="button button-compact"
                          type="button"
                          title="Die verifizierte Textstelle als Beleg dieses Zitats übernehmen (Hover & PDF-Marker zeigen dann die richtige Stelle)"
                          onClick={() => {
                            onClaimEvidenceUpdate(
                              claimCard.source,
                              claimCard.evidenceIndex,
                              claimCard.result?.supporting_quotes ?? [],
                              claimCard.statement
                            );
                            setClaimCard(null);
                          }}
                        >
                          <CheckCircle2 size={13} /> Zitat aktualisieren
                        </button>
                      ) : null}
                    </span>
                  </>
                ) : null}
              </span>
            ) : null}
          </span>
        );
      })}
    </span>
  );
}

function claimVerdictIcon(verdict: ClaimCheckResult["verdict"]) {
  switch (verdict) {
    case "supported":
      return <CheckCircle2 size={14} />;
    case "partially_supported":
      return <AlertTriangle size={14} />;
    case "not_supported":
      return <XCircle size={14} />;
    default:
      return <HelpCircle size={14} />;
  }
}

function renderUncitedPart(part: string, prevIsCitation: boolean, nextIsCitation: boolean) {
  const segments = uncitedTextSegments(part, prevIsCitation, nextIsCitation);
  if (!segments.some((segment) => segment.uncited)) {
    return part;
  }
  return (
    <>
      {segments.map((segment, index) =>
        segment.uncited ? (
          <span
            key={`${segment.text.slice(0, 24)}-${index}`}
            className="uncited-sentence"
            title="Aussage ohne Quellenangabe — nicht direkt aus den lokalen Quellen belegt"
          >
            {segment.text}
          </span>
        ) : (
          <span key={`${segment.text.slice(0, 24)}-${index}`}>{segment.text}</span>
        )
      )}
    </>
  );
}

function renderCitationContextPart(value: string, range: { start: number; end: number } | null, style?: CSSProperties) {
  if (!range) {
    return value;
  }
  return (
    <>
      {value.slice(0, range.start)}
      <span className="citation-context-highlight" style={style}>
        {value.slice(range.start, range.end)}
      </span>
      {value.slice(range.end)}
    </>
  );
}

