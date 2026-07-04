// Standalone, prop-driven sub-components extracted from WorkspacePage.tsx (they
// close over no parent state). Kept together because they reference each other
// (navigator uses PaneHeading/CollapsedPane).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChangeEvent,
  ClipboardEvent as ReactClipboardEvent,
  DragEvent as ReactDragEvent,
  FormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  MutableRefObject,
  PointerEvent as ReactPointerEvent,
  ReactNode
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  AlertTriangle,
  Bot,
  ChevronDown,
  ChevronRight,
  ChevronUp,
  Command,
  DownloadCloud,
  FilePlus2,
  FileSearch,
  FileText,
  FolderPlus,
  GitBranch,
  GitMerge,
  Globe,
  Link2,
  ListChecks,
  Database,
  FlaskConical,
  Loader2,
  Maximize2,
  MessageSquareText,
  Minimize2,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  Plus,
  Quote,
  Search,
  Send,
  Settings2,
  ShieldAlert,
  ShieldCheck,
  Sparkles,
  Square,
  Star,
  Trash2,
  Upload,
  X,
  XCircle
} from "lucide-react";

import { api, streamResearchTree, streamAutoAnswer, exportResearchTree } from "../api";
import type { ResearchTreeRequest, ResearchTreeExportOptions } from "../api";
import { downloadBlob } from "../download";
import { colorVarsForPaperId, evidenceColorVars, isGreySourcePaperId } from "../citationColors";
import { EmptyState } from "../components/EmptyState";
import { GreySourceView } from "../components/GreySourceView";
import { PdfPane } from "../components/PdfPane";
import { Status } from "../components/Status";
import { noteProjectId, projectScopeLabel } from "../projectScope";
import { useAppState } from "../state";
import type {
  Answer,
  CitationLink,
  ClaimCheckResult,
  DeepResearchFinding,
  GreySource,
  NoteAiMessage,
  NoteAiThread,
  NoteCitation,
  Paper,
  ParallelSession,
  ResearchNode,
  VerificationEvidence,
  VerificationSource
} from "../types";
import { ParallelResearchPanel } from "./ParallelResearchPanel";
import { ParallelResultsTab } from "./ParallelResultsTab";
import {
  AnswerText,
  answerLimitFor,
  citationContext,
  citationMetasFor,
  citationSegmentFromParts,
  claimVerdictLabel,
  cleanAnswerQuote,
  EvidenceVerificationBadge,
  evidenceLocationUncertain,
  fetchAssistantSession,
  formatAnswerForNote,
  formatNoteQuote,
  formatNoteQuoteMulti,
  formatTurnTime,
  loadAssistantSession,
  meaningfulQuote,
  mergeVerification,
  noteCitation,
  sameCitation,
  saveAssistantSession,
  shortEvidenceText,
  turnBlocks,
  turnContext,
  verificationSourcesFor
} from "./AssistantPage";
import type { CitationInsertExtras, CitationMeta } from "./AssistantPage";
import { NotesSurface } from "./NotesPage";
import type { NotesSurfaceActions, NotesSurfaceSnapshot } from "./NotesPage";
import { AnalysisPanel } from "./AnalysisPanel";
import { DatasetsPanel } from "./DatasetsPanel";
import {
  ALL_PAPERS_SCOPES,
  EMPTY_NOTES_SNAPSHOT,
  MAX_PREVERIFY_CITATIONS,
  WORKSPACE_COMMANDS,
  activeScopePaperId,
  answerSuggestsWebSearch,
  citationPoolFor,
  classifyDroppedFile,
  classifyPastedText,
  extractInlineWebToken,
  fileExtension,
  findingToGreyRecord,
  findingToGreySource,
  latestThreadAnswer,
  loadWorkspaceBoolean,
  loadWorkspaceNumber,
  matchWorkspaceCommands,
  normalizeFilter,
  normalizeWorkspacePaper,
  noteCitationEvidence,
  restoredActiveTurnFor,
  sameNotesSnapshot,
  saveWorkspaceBoolean,
  saveWorkspaceNumber,
  shortSelectionPreview,
  sortPinnedThreads,
  stripThreadContext,
  threadCollapsed,
  threadMessages,
  threadPinned,
  workspacePaperId,
  workspacePaperTitle,
} from "./workspaceHelpers";
import type {
  WorkspaceNavigatorTab, WorkspacePdfTarget, WorkspaceCommandDef, WorkspaceActionEntry,
  WorkspacePaperRecord, PaperQuestionScope, WorkspaceAssistantMode, AssistantAnswerBlock,
  AssistantTurn, DroppedSourceKind,
} from "./WorkspacePage";

export function WorkspaceNotesAssistant({
  threads,
  activeThreadId,
  focusedThreadId,
  threadMeta,
  followUpDrafts,
  isFollowUpPending,
  deletingThreadId,
  onActivateThread,
  onToggleThread,
  onSetThreadPinned,
  onDraftChange,
  onFollowUp,
  onInsertThread,
  onPreviewThread,
  onInsertThreadMessage,
  onPreviewThreadMessage,
  onHideThreadMessage,
  onPreviewClear,
  onDeleteThread,
  onDeleteAllThreads,
  onFocusThread,
  onClearFocus
}: {
  threads: NoteAiThread[];
  activeThreadId: string;
  focusedThreadId: string;
  threadMeta: NotesSurfaceSnapshot["threadMeta"];
  followUpDrafts: Record<string, string>;
  isFollowUpPending: boolean;
  deletingThreadId: string;
  onActivateThread: (threadId: string) => void;
  onToggleThread: (threadId: string) => void;
  onSetThreadPinned: (threadId: string, pinned: boolean) => void;
  onDraftChange: (threadId: string, value: string) => void;
  onFollowUp: (threadId: string) => void;
  onInsertThread: (threadId: string) => void;
  onPreviewThread: (threadId: string) => void;
  onInsertThreadMessage: (threadId: string, messageId: string) => void;
  onPreviewThreadMessage: (threadId: string, messageId: string) => void;
  onHideThreadMessage: (threadId: string, messageId: string) => void;
  onPreviewClear: () => void;
  onDeleteThread: (threadId: string) => void;
  onDeleteAllThreads: () => void;
  onFocusThread: (threadId: string) => void;
  onClearFocus: () => void;
}) {
  const orderedThreads = sortPinnedThreads(threads);
  const focusedThread = focusedThreadId ? orderedThreads.find((thread) => thread.id === focusedThreadId) ?? null : null;
  const visibleThreads = focusedThread ? [focusedThread] : orderedThreads;
  const [expandedSelectionIds, setExpandedSelectionIds] = useState<Record<string, boolean>>({});
  return (
    <section className={`workspace-notes-assistant-panel ${focusedThread ? "workspace-notes-assistant-panel--focused" : ""}`}>
      <div className="workspace-notes-assistant-heading">
        <div>
          <span>{focusedThread ? "KI-Notiz gross" : "KI-Notizen"}</span>
          <strong>{focusedThread ? focusedThread.instruction : `${threads.length} Verlaufseintraege`}</strong>
        </div>
        <div className="button-row">
          {focusedThread ? (
            <button className="button button-compact" type="button" onClick={onClearFocus}>
              <Minimize2 size={15} />
              <span>Liste</span>
            </button>
          ) : null}
          <button className="button button-compact" type="button" disabled={!threads.length} onClick={onDeleteAllThreads}>
            <Trash2 size={15} />
            <span>Alle</span>
          </button>
        </div>
      </div>
      <div className="ai-thread-list workspace-notes-assistant-list">
        {visibleThreads.map((thread) => {
          const answer = latestThreadAnswer(thread);
          const meta = threadMeta.get(thread.id);
          const collapsed = focusedThread ? false : threadCollapsed(thread);
          const pinned = threadPinned(thread);
          const messages = threadMessages(thread);
          const contextText = stripThreadContext(thread.selected_text || thread.anchor_quote || "");
          const selectionExpanded = focusedThread ? expandedSelectionIds[thread.id] !== false : expandedSelectionIds[thread.id] === true;
          const threadTimestamp = thread.updated_timestamp || thread.created_timestamp || "";
          return (
            <article
              className={`note-thread-row ai-thread-card workspace-notes-assistant-card ${focusedThread ? "workspace-notes-assistant-card--focused" : ""} ${meta ? "ai-thread-card--anchored" : ""} ${collapsed ? "ai-thread-card--compact" : ""} ${activeThreadId === thread.id ? "ai-thread-card--active" : ""} ${pinned ? "ai-thread-card--pinned" : ""}`}
              key={thread.id}
              style={meta ? evidenceColorVars(meta.colorIndex) : undefined}
            >
              <div className="ai-thread-topline">
                <button className="ai-thread-header" type="button" onClick={() => onActivateThread(thread.id)}>
                  <span className="ai-thread-header-line">
                    {meta ? <span className="ai-thread-anchor-badge">{meta.label}</span> : null}
                    <strong>{thread.instruction}</strong>
                  </span>
                  <span className="ai-thread-card-meta">
                    {messages.length} Nachrichten
                    {threadTimestamp ? ` - ${formatTurnTime(threadTimestamp)}` : ""}
                  </span>
                </button>
                <div className="ai-thread-actions">
                  <button
                    className={`icon-button icon-button--compact ${pinned ? "icon-button--active" : ""}`}
                    type="button"
                    aria-label={pinned ? "KI-Notiz loesen" : "KI-Notiz anpinnen"}
                    onClick={() => onSetThreadPinned(thread.id, !pinned)}
                  >
                    <Pin size={15} />
                  </button>
                  <button className="button button-compact" type="button" onClick={() => (focusedThread ? onClearFocus() : collapsed ? onActivateThread(thread.id) : onToggleThread(thread.id))}>
                    {focusedThread ? "Liste" : collapsed ? "Öffnen" : "Einklappen"}
                  </button>
                  {!focusedThread ? (
                    <button className="icon-button icon-button--compact" type="button" aria-label="KI-Notiz gross anzeigen" onClick={() => onFocusThread(thread.id)}>
                      <Maximize2 size={15} />
                    </button>
                  ) : null}
                  <button
                    className="button button-compact"
                    type="button"
                    onClick={() => {
                      onInsertThread(thread.id);
                      onPreviewClear();
                    }}
                    onMouseEnter={() => onPreviewThread(thread.id)}
                    onMouseOut={onPreviewClear}
                    onMouseLeave={onPreviewClear}
                    onPointerEnter={() => onPreviewThread(thread.id)}
                    onPointerOut={onPreviewClear}
                    onPointerLeave={onPreviewClear}
                    onFocus={() => onPreviewThread(thread.id)}
                    onBlur={onPreviewClear}
                    disabled={!answer}
                  >
                    Einfügen
                  </button>
                  <button className="icon-button icon-button--compact" type="button" aria-label="KI-Verlauf loeschen" disabled={deletingThreadId === thread.id} onClick={() => onDeleteThread(thread.id)}>
                    <Trash2 size={15} />
                  </button>
                </div>
              </div>
              <div className={`ai-thread-preview ${!collapsed ? "ai-thread-preview--expanded" : ""}`}>
                {contextText ? (
                  <section className="ai-thread-info-block ai-thread-info-block--selection">
                    <span>
                      Markierter Bereich
                      {!collapsed ? (
                        <button
                          className="button button-compact ai-thread-context-toggle"
                          type="button"
                          onClick={() => setExpandedSelectionIds((current) => ({ ...current, [thread.id]: !selectionExpanded }))}
                        >
                          {selectionExpanded ? "Einklappen" : "Anzeigen"}
                        </button>
                      ) : null}
                    </span>
                    <p>{selectionExpanded || collapsed ? contextText : shortSelectionPreview(contextText)}</p>
                  </section>
                ) : null}
              </div>
              {!collapsed ? (
                <>
                  <div className={`ai-thread-messages ${focusedThread ? "workspace-notes-chat-stream" : ""}`}>
                    {messages.map((message) => (
                      <div className={`ai-thread-message ai-thread-message--${message.role === "assistant" ? "assistant" : "user"}`} key={message.id}>
                        <div className="ai-thread-message-topline">
                          <span>{message.role === "assistant" ? "KI-Antwort" : "Deine Frage"}</span>
                          {message.role === "assistant" ? (
                            <div className="ai-thread-message-actions">
                              <button
                                className="button button-compact"
                                type="button"
                                onClick={() => {
                                  onInsertThreadMessage(thread.id, message.id);
                                  onPreviewClear();
                                }}
                                onMouseEnter={() => onPreviewThreadMessage(thread.id, message.id)}
                                onMouseOut={onPreviewClear}
                                onMouseLeave={onPreviewClear}
                                onPointerEnter={() => onPreviewThreadMessage(thread.id, message.id)}
                                onPointerOut={onPreviewClear}
                                onPointerLeave={onPreviewClear}
                                onFocus={() => onPreviewThreadMessage(thread.id, message.id)}
                                onBlur={onPreviewClear}
                              >
                                Einfügen
                              </button>
                              <button className="icon-button icon-button--compact" type="button" aria-label="KI-Antwort ausblenden" onClick={() => onHideThreadMessage(thread.id, message.id)}>
                                <Trash2 size={15} />
                              </button>
                            </div>
                          ) : null}
                        </div>
                        <p>{message.content}</p>
                      </div>
                    ))}
                  </div>
                  <div className="ai-follow-up-row">
                    <input
                      value={followUpDrafts[thread.id] ?? ""}
                      onChange={(event) => onDraftChange(thread.id, event.target.value)}
                      onKeyDown={(event) => {
                        if (event.key === "Enter" && (followUpDrafts[thread.id] ?? "").trim()) {
                          event.preventDefault();
                          onFollowUp(thread.id);
                        }
                      }}
                      placeholder="Folgefrage zu dieser Auswahl"
                    />
                    <button className="button button-primary" type="button" disabled={isFollowUpPending || !(followUpDrafts[thread.id] ?? "").trim()} onClick={() => onFollowUp(thread.id)}>
                      Fragen
                    </button>
                  </div>
                </>
              ) : null}
            </article>
          );
        })}
        {!threads.length ? <EmptyState title="Noch keine KI-Fragen" /> : null}
      </div>
    </section>
  );
}


export function WorkspaceNavigatorBody({
  tab,
  query,
  setQuery,
  notes,
  notesLoading,
  activeNoteId,
  citations,
  selectedCitation,
  papers,
  papersLoading,
  greySources,
  primaryPaperId,
  pdfTarget,
  activeAssistantSource,
  activeAssistantEvidenceIndex,
  selectedPaperIds,
  pdfCitationListHeight,
  greySourceListHeight,
  sessions,
  activeSessionId,
  onCreateNote,
  onSelectNote,
  onOpenCitation,
  onOpenPaper,
  onOpenGrey,
  onToggleScopedPaper,
  selectedGreyIds,
  onToggleScopedGrey,
  onResizeCitationList,
  onResizeGreyList,
  onOpenAssistantPdf,
  onActivateSession,
  onDeleteSession,
  onDeleteNote,
  isRealProject,
  onDeletePaper,
  onDeleteGrey,
  onSetPrimary,
  onDeleteCitation
}: {
  tab: WorkspaceNavigatorTab;
  query: string;
  setQuery: (value: string) => void;
  notes: NotesSurfaceSnapshot["notes"];
  notesLoading: boolean;
  activeNoteId: string;
  citations: NotesSurfaceSnapshot["citationRows"];
  selectedCitation: NoteCitation | null;
  papers: Paper[];
  papersLoading: boolean;
  greySources: GreySource[];
  primaryPaperId: string | null;
  pdfTarget: WorkspacePdfTarget | null;
  activeAssistantSource: VerificationSource | null;
  activeAssistantEvidenceIndex: number;
  selectedPaperIds: string[];
  pdfCitationListHeight: number;
  greySourceListHeight: number;
  sessions: AssistantTurn[];
  activeSessionId: string;
  onCreateNote: () => void;
  onSelectNote: (noteId: string) => void;
  onOpenCitation: (citation: NoteCitation) => void;
  onOpenPaper: (paper: Paper) => void;
  onOpenGrey: (source: GreySource) => void;
  onToggleScopedPaper: (paperId: string) => void;
  selectedGreyIds: string[];
  onToggleScopedGrey: (greyId: string) => void;
  onResizeCitationList: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onResizeGreyList: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onOpenAssistantPdf: () => void;
  onActivateSession: (turnId: string) => void;
  onDeleteSession: (turnId: string) => void;
  onDeleteNote: (noteId: string) => void;
  isRealProject: boolean;
  onDeletePaper: (paperId: string) => void;
  onDeleteGrey: (greyId: string) => void;
  onSetPrimary: (paperId: string | null) => void;
  onDeleteCitation: (citation: NoteCitation) => void;
}) {
  // Collapsible sections keep the PDFs tab tidy when many sources pile up.
  const [collapsedSections, setCollapsedSections] = useState<Record<string, boolean>>({});
  const sectionCollapsed = (key: string) => collapsedSections[key] === true;
  const toggleSection = (key: string) => setCollapsedSections((current) => ({ ...current, [key]: !current[key] }));
  if (tab === "notes") {
    return (
      <section className="workspace-nav-body">
        <div className="workspace-nav-toolbar">
          <label className="search-field">
            <Search size={16} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Notizen suchen" />
          </label>
          <button className="button button-compact" type="button" onClick={onCreateNote} aria-label="Neu">
            <FilePlus2 size={15} />
            <span>Neu</span>
          </button>
        </div>
        <div className="list workspace-nav-list">
          {notes.map((note) => (
            <div
              className={`list-row note-list-row ${activeNoteId === note.id ? "list-row--active" : ""}`}
              key={note.id}
            >
              <button
                className="note-list-row__body"
                type="button"
                onClick={() => onSelectNote(note.id)}
              >
                <strong>{note.title}</strong>
                <span>{note.excerpt || "Leer"}</span>
                <small>{note.citation_count ?? 0} Quellen</small>
              </button>
              <button
                className="icon-button nav-delete-btn"
                type="button"
                title="Notiz löschen"
                onClick={(e) => { e.stopPropagation(); onDeleteNote(note.id); }}
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          {!notes.length ? <EmptyState title={notesLoading ? "Lade Notizen" : "Noch keine Notizen"} /> : null}
        </div>
      </section>
    );
  }
  if (tab === "pdfs") {
    const activeCitationId = selectedCitation?.id ?? (pdfTarget?.kind === "noteCitation" ? pdfTarget.citation.id : "");
    const targetPaperId = pdfTarget?.kind === "paper" ? workspacePaperId(pdfTarget.paper) : pdfTarget?.kind === "noteCitation" ? pdfTarget.citation.paper_id : pdfTarget?.kind === "assistant" ? pdfTarget.source.paper_id : pdfTarget?.kind === "missing" ? pdfTarget.paperId : "";
    const activePaperId = targetPaperId || activeAssistantSource?.paper_id || "";
    const activeAssistantEvidence = activeAssistantSource?.evidence[activeAssistantEvidenceIndex];
    const selectedCitationIndex = Number(selectedCitation?.evidence_index ?? 0);
    const activePaperEvidenceIndex =
      pdfTarget?.kind === "assistant"
        ? pdfTarget.evidenceIndex
        : pdfTarget?.kind === "noteCitation"
          ? selectedCitationIndex
          : activeAssistantSource
            ? activeAssistantEvidenceIndex
            : 0;
    const orderedPapers = primaryPaperId
      ? [...papers].sort((a, b) => {
          const aPrimary = workspacePaperId(normalizeWorkspacePaper(a)) === primaryPaperId;
          const bPrimary = workspacePaperId(normalizeWorkspacePaper(b)) === primaryPaperId;
          return aPrimary === bPrimary ? 0 : aPrimary ? -1 : 1;
        })
      : papers;
    return (
      <section className="workspace-nav-body">
        {pdfTarget?.kind === "assistant" && activeAssistantSource && activeAssistantEvidence ? (
          <div className="workspace-active-source" style={colorVarsForPaperId(activeAssistantSource.paper_id, activeAssistantEvidenceIndex)}>
            <span>Aktiver Assistant-Nachweis</span>
            <strong>
              Z{activeAssistantEvidenceIndex + 1} · {activeAssistantSource.title || activeAssistantSource.paper_id}
            </strong>
            <p>{activeAssistantEvidence.pdf_excerpt || activeAssistantEvidence.reference_text}</p>
            <button className="button button-compact" type="button" onClick={onOpenAssistantPdf}>
              <FileText size={15} />
              <span>PDF öffnen</span>
            </button>
          </div>
        ) : selectedCitation ? (
          <div className="workspace-active-source" style={colorVarsForPaperId(selectedCitation.paper_id, selectedCitationIndex)}>
            <span>Aktive Notizquelle</span>
            <strong>
              Z{selectedCitationIndex + 1} - {selectedCitation.title || selectedCitation.paper_id}
            </strong>
            <p>{selectedCitation.reference_text || selectedCitation.pdf_excerpt || selectedCitation.paper_id}</p>
          </div>
        ) : null}
        <button className="workspace-nav-subheading workspace-nav-subheading--toggle" type="button" onClick={() => toggleSection("citations")}>
          <span>Notizquellen</span>
          <strong>{citations.length}</strong>
          {sectionCollapsed("citations") ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
        {!sectionCollapsed("citations") ? (
          <>
            <div className="list workspace-nav-list workspace-nav-list--short" style={{ maxHeight: pdfCitationListHeight }}>
              {citations.map(({ citation, badge, title, evidence }, index) => (
                <div
                  className={`list-row note-citation-row workspace-nav-actionable-row ${activeCitationId === citation.id ? "note-citation-row--active list-row--active" : ""}`}
                  key={citation.id}
                  style={colorVarsForPaperId(citation.paper_id, Number(citation.evidence_index ?? index))}
                >
                  <button className="note-citation-row__body" type="button" onClick={() => onOpenCitation(citation)}>
                    <span className="note-citation-row__title">
                      <span className="citation-badge">{badge}</span>
                      <strong>{title}</strong>
                    </span>
                    <span>{evidence || citation.paper_id}</span>
                  </button>
                  <button
                    className="icon-button nav-delete-btn"
                    type="button"
                    title="Notizquelle löschen (Notiztext bleibt erhalten)"
                    onClick={(event) => {
                      event.stopPropagation();
                      onDeleteCitation(citation);
                    }}
                  >
                    <Trash2 size={12} />
                  </button>
                </div>
              ))}
              {!citations.length ? <div className="muted-row">Keine Quellen in der aktiven Notiz</div> : null}
            </div>
            <div className="workspace-list-resize-handle" role="separator" aria-label="Notizquellen Hoehe anpassen" onPointerDown={onResizeCitationList} />
          </>
        ) : null}
        <button className="workspace-nav-subheading workspace-nav-subheading--toggle" type="button" onClick={() => toggleSection("papers")}>
          <span>Projekt-PDFs</span>
          <strong>{papers.length}</strong>
          {sectionCollapsed("papers") ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
        </button>
        {!sectionCollapsed("papers") ? (
        <div className="list workspace-nav-list">
          {orderedPapers.map((paper) => {
            const normalizedPaper = normalizeWorkspacePaper(paper);
            const paperId = workspacePaperId(normalizedPaper);
            const title = workspacePaperTitle(normalizedPaper);
            const selectedForScope = Boolean(paperId && selectedPaperIds.includes(paperId));
            const isActivePaper = Boolean(paperId && activePaperId && (activePaperId === paperId || sameCitation(activePaperId, paperId)));
            const isPrimary = Boolean(paperId && primaryPaperId && paperId === primaryPaperId);
            return (
              <div
                className={`list-row workspace-paper-row workspace-nav-actionable-row ${isPrimary ? "workspace-paper-row--primary" : ""} ${isActivePaper ? "workspace-paper-row--active-source list-row--active" : ""}`}
                key={paperId || `${title}-${paper.year ?? "n/a"}`}
                role="button"
                tabIndex={0}
                aria-label={`PDF ${title} öffnen`}
                onClick={() => onOpenPaper(normalizedPaper)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" || event.key === " ") {
                    event.preventDefault();
                    onOpenPaper(normalizedPaper);
                  }
                }}
                style={isActivePaper ? colorVarsForPaperId(paperId, activePaperEvidenceIndex) : undefined}
              >
                <label className="workspace-paper-select" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                  <input
                    type="checkbox"
                    checked={selectedForScope}
                    disabled={!paperId}
                    aria-label={`${title} auswaehlen`}
                    onChange={() => paperId && onToggleScopedPaper(paperId)}
                  />
                </label>
                <div className="workspace-paper-main" title={title}>
                  <strong>
                    {isPrimary ? <span className="primary-badge"><Star size={11} /> Hauptquelle</span> : null}
                    {title}
                  </strong>
                  <span>{[paperId || "keine ID", normalizedPaper.year ?? ""].filter(Boolean).join(" - ")}</span>
                </div>
                {isRealProject && paperId ? (
                  <span className="workspace-row-actions" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                    <button
                      className={`icon-button nav-action-btn ${isPrimary ? "icon-button--active" : ""}`}
                      type="button"
                      title={isPrimary ? "Hauptquelle entfernen" : "Als Hauptquelle setzen"}
                      onClick={() => onSetPrimary(isPrimary ? null : paperId)}
                    >
                      <Star size={12} />
                    </button>
                    <button
                      className="icon-button nav-delete-btn"
                      type="button"
                      title="Paper aus dem Projekt entfernen (bleibt in der Bibliothek)"
                      onClick={() => onDeletePaper(paperId)}
                    >
                      <Trash2 size={12} />
                    </button>
                  </span>
                ) : null}
              </div>
            );
          })}
          {!papers.length ? <EmptyState title={papersLoading ? "Lade PDFs" : "Keine PDFs"} /> : null}
        </div>
        ) : null}
        {greySources.length ? (
          <>
            <button className="workspace-nav-subheading workspace-nav-subheading--toggle" type="button" onClick={() => toggleSection("grey")}>
              <span>Graue Quellen</span>
              <strong>{greySources.length}</strong>
              {sectionCollapsed("grey") ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
            </button>
            {!sectionCollapsed("grey") ? (
            <>
            <div className="list workspace-nav-list workspace-nav-list--short" style={{ maxHeight: greySourceListHeight }}>
              {greySources.map((source) => {
                const isActiveGrey = pdfTarget?.kind === "grey" && pdfTarget.source.id === source.id;
                const selectedForScope = selectedGreyIds.includes(source.id);
                return (
                  <div
                    className={`list-row workspace-paper-row workspace-grey-row workspace-nav-actionable-row ${isActiveGrey ? "workspace-paper-row--active-source list-row--active" : ""}`}
                    key={source.id}
                    role="button"
                    tabIndex={0}
                    aria-label={`Graue Quelle ${source.title || source.url} öffnen`}
                    onClick={() => onOpenGrey(source)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter" || event.key === " ") {
                        event.preventDefault();
                        onOpenGrey(source);
                      }
                    }}
                  >
                    <label className="workspace-paper-select" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                      <input
                        type="checkbox"
                        checked={selectedForScope}
                        aria-label={`${source.title || source.url} auswaehlen`}
                        onChange={() => onToggleScopedGrey(source.id)}
                      />
                    </label>
                    <div className="workspace-paper-main" title={source.title || source.url}>
                      <strong>{source.title || source.url}</strong>
                      <span>
                        <span className="grey-badge grey-badge--mini">Grauquelle</span>
                        {source.url}
                      </span>
                    </div>
                    {isRealProject ? (
                      <span className="workspace-row-actions" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                        <button
                          className="icon-button nav-delete-btn"
                          type="button"
                          title="Grauquelle löschen"
                          onClick={() => onDeleteGrey(source.id)}
                        >
                          <Trash2 size={12} />
                        </button>
                      </span>
                    ) : null}
                  </div>
                );
              })}
            </div>
            <div className="workspace-list-resize-handle" role="separator" aria-label="Graue Quellen Hoehe anpassen" onPointerDown={onResizeGreyList} />
            </>
            ) : null}
          </>
        ) : null}
      </section>
    );
  }
  return (
    <section className="workspace-nav-body">
      <div className="list workspace-nav-list">
        {sessions.map((turn) => {
          const isTree = turn.type === "research_tree";
          const isParallel = turn.type === "parallel";
          const doneNodes = isTree ? (turn.researchNodes ?? []).filter((n) => n.status === "done").length : 0;
          const hasSynthesis = isTree && (turn.researchNodes ?? []).some((n) => n.status === "synthesis");
          return (
            <div
              className={`assistant-history-item workspace-session-item ${activeSessionId === turn.id ? "assistant-history-item--active" : ""}`}
              key={turn.id}
            >
              <button
                className="session-item__body"
                type="button"
                onClick={() => onActivateSession(turn.id)}
              >
                <span className="session-item__title">
                  {isTree ? <GitBranch size={12} style={{ flexShrink: 0, marginRight: "3px", verticalAlign: "middle" }} /> : null}
                  {isParallel ? <GitMerge size={12} style={{ flexShrink: 0, marginRight: "3px", verticalAlign: "middle" }} /> : null}
                  {turn.question}
                </span>
                <small>
                  {formatTurnTime(turn.createdAt)}
                  {isTree
                    ? ` | ${doneNodes} Knoten${hasSynthesis ? " ✓" : ""}`
                    : isParallel
                      ? ` | ${turn.parallelVariantCount ?? 0} Varianten`
                      : turnBlocks(turn).length > 1 ? ` | ${turnBlocks(turn).length} Antworten` : ""}
                </small>
              </button>
              <button
                className="icon-button nav-delete-btn"
                type="button"
                title="Session löschen"
                onClick={(e) => { e.stopPropagation(); onDeleteSession(turn.id); }}
              >
                <Trash2 size={13} />
              </button>
            </div>
          );
        })}
        {!sessions.length ? <EmptyState title="Noch keine KI-Sessions" /> : null}
      </div>
    </section>
  );
}


export function PaneHeading({
  eyebrow,
  title,
  status,
  collapseSide,
  onCollapse,
  actions
}: {
  eyebrow?: string;
  title: string;
  status?: string;
  collapseSide: "left" | "right";
  onCollapse: () => void;
  actions?: ReactNode;
}) {
  return (
    <div className="pane-heading workspace-pane-heading">
      <div>
        {eyebrow ? <span>{eyebrow}</span> : null}
        <strong>{title}</strong>
      </div>
      {actions ? <div className="pane-heading-actions">{actions}</div> : null}
      <div className="button-row">
        {status ? <Status value={status} /> : null}
        <button className="icon-button" type="button" aria-label={`${title} einklappen`} onClick={onCollapse}>
          {collapseSide === "left" ? <PanelLeftClose size={17} /> : <PanelRightClose size={17} />}
        </button>
      </div>
    </div>
  );
}


export function CollapsedPane({ label, icon, onOpen }: { label: string; icon: ReactNode; onOpen: () => void }) {
  return (
    <aside className="assistant-collapsed-panel workspace-collapsed-pane">
      <button className="collapsed-panel-tab" type="button" onClick={onOpen}>
        {icon}
        <span>{label}</span>
      </button>
    </aside>
  );
}


export function ResearchTreeView({
  nodes,
  loading,
  llmError,
  onStop,
  onResume,
  onCitationClick,
  onCitationInsert,
  onCitationInsertPreview,
  onCitationInsertPreviewClear,
  onDrillDeeper,
  onSaveToNotes,
}: {
  nodes: ResearchNode[];
  loading: boolean;
  llmError: { kind: string; message: string; error: string } | null;
  onStop: () => void;
  onResume: () => void;
  onCitationClick: (source: VerificationSource, evidenceIndex: number) => void;
  onCitationInsert: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) => void;
  onCitationInsertPreview: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) => void;
  onCitationInsertPreviewClear: () => void;
  onDrillDeeper: (nodeId: string, question: string) => void;
  onSaveToNotes: () => void;
}) {
  const [collapsed, setCollapsed] = useState<Set<string>>(new Set());
  const [activeTab, setActiveTab] = useState<"tree" | "synthesis">("tree");
  const [exportOpen, setExportOpen] = useState(false);
  const [exportFormat, setExportFormat] = useState<"pdf" | "zip">("pdf");
  const [exportOpts, setExportOpts] = useState<ResearchTreeExportOptions>({
    tikz_tree: true,
    charts: true,
    tables: true,
    comfyui_images: false,
  });
  const [exporting, setExporting] = useState(false);
  const [exportMsg, setExportMsg] = useState<{ kind: "error" | "warn" | "ok"; text: string } | null>(null);

  const treeNodes = nodes.filter((n) => n.status !== "synthesis");
  const synthesisNode = nodes.find((n) => n.status === "synthesis");

  // Auto-switch to synthesis tab when it arrives
  useEffect(() => {
    if (synthesisNode?.document) setActiveTab("synthesis");
  }, [synthesisNode?.document]);

  async function handleExport() {
    if (!synthesisNode?.document || exporting) return;
    setExporting(true);
    setExportMsg(null);
    const rootQuestion =
      treeNodes.find((n) => n.depth === 0)?.question ?? synthesisNode.question ?? "Tiefenanalyse";
    // De-dupe the verification sources across all nodes for the bibliography; the
    // backend enriches year/doi/url from each node's answer.sources.
    const seen = new Set<string>();
    const sources: VerificationSource[] = [];
    for (const n of treeNodes) {
      for (const s of n.verification ?? []) {
        if (!seen.has(s.paper_id)) {
          seen.add(s.paper_id);
          sources.push(s);
        }
      }
    }
    try {
      const result = await exportResearchTree({
        root_question: rootQuestion,
        document: synthesisNode.document,
        nodes,
        sources,
        format: exportFormat,
        options: exportOpts,
      });
      downloadBlob(result.filename, result.blob);
      if (result.warnings.length > 0) {
        setExportMsg({ kind: "warn", text: result.warnings.join(" ") });
      } else {
        setExportMsg({ kind: "ok", text: `Heruntergeladen: ${result.filename}` });
      }
    } catch (e) {
      setExportMsg({ kind: "error", text: e instanceof Error ? e.message : "Export fehlgeschlagen" });
    } finally {
      setExporting(false);
    }
  }

  const rootNodes = treeNodes.filter((n) => n.parent_id === null);
  const childrenOf = (id: string) => treeNodes.filter((n) => n.parent_id === id);

  function toggle(id: string) {
    setCollapsed((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }

  function renderNode(node: ResearchNode): ReactNode {
    const children = childrenOf(node.id);
    const isCollapsed = collapsed.has(node.id);
    const hasChildren = children.length > 0 || (node.status === "done" && (node.child_count ?? 0) > 0);
    const verification = node.verification ?? [];
    // Fall back to answer.sources so a reloaded session (trimmed verification) still
    // resolves/colours citations instead of greying them all out.
    const citationPool = citationPoolFor(verification, node.answer);
    const isHarvesting = node.status === "harvesting";

    return (
      <div key={node.id} className="research-tree-node" style={{ marginLeft: node.depth > 0 ? `${node.depth * 20}px` : 0 }}>
        <div className="research-tree-node-header">
          {hasChildren ? (
            <button type="button" className="icon-button research-tree-toggle" onClick={() => toggle(node.id)}>
              {isCollapsed ? <ChevronRight size={13} /> : <ChevronDown size={13} />}
            </button>
          ) : (
            <span className="research-tree-toggle-spacer" />
          )}
          <span className="research-tree-node-depth">T{node.depth + 1}</span>
          <strong className="research-tree-question">{node.question}</strong>
          {(node.status === "running" || isHarvesting) ? <Loader2 size={13} className="spin" /> : null}
          {isHarvesting ? <span className="muted-row" style={{ fontSize: "11px" }}>Suche Quellen… (Paper + Web)</span> : null}
          {node.status === "error" ? <span className="warning-row" style={{ fontSize: "11px" }}>Fehler</span> : null}
          {node.status === "done" && !loading ? (
            <button
              type="button"
              className="icon-button"
              style={{ marginLeft: "auto", fontSize: "11px", display: "flex", alignItems: "center", gap: "3px", opacity: 0.7 }}
              onClick={() => onDrillDeeper(node.id, node.question)}
              title="Tiefer in diese Frage einsteigen"
            >
              <Plus size={11} />
              <span>Tiefer</span>
            </button>
          ) : null}
        </div>
        {!isCollapsed && node.answer ? (
          <div className="research-tree-answer">
            <AnswerText
              answer={node.answer.answer}
              getCitationMeta={(citation, context, start) =>
                citationMetasFor(citationPool, citation, context, node.answer?.citation_links ?? [], start)
              }
              onCitationClick={() => {}}
              onCitationMetaClick={(meta) => onCitationClick(meta.source, meta.evidenceIndex)}
              onCitationInsert={(source, evidenceIndex, quote, extras) => onCitationInsert(source, evidenceIndex, quote, extras)}
              onCitationInsertPreview={(source, evidenceIndex, quote, extras) => onCitationInsertPreview(source, evidenceIndex, quote, extras)}
              onCitationInsertPreviewClear={onCitationInsertPreviewClear}
            />
            {verification.length > 0 ? (
              <div className="research-tree-sources">
                {verification.map((src) => (
                  <button
                    key={src.paper_id}
                    type="button"
                    className="citation-link citation-link--mapped"
                    style={colorVarsForPaperId(src.paper_id, 0)}
                    onClick={() => onCitationClick(src, 0)}
                    title={src.title || src.paper_id}
                  >
                    {src.title ? src.title.slice(0, 40) + (src.title.length > 40 ? "…" : "") : src.paper_id}
                  </button>
                ))}
              </div>
            ) : null}
            {((node.harvested_papers?.length ?? 0) > 0 || (node.harvested_grey?.length ?? 0) > 0) ? (
              <div className="research-tree-harvested">
                <span className="research-tree-harvested-label">Neu gefunden:</span>
                {node.harvested_papers?.map((p) => (
                  <span key={p.id} className="research-tree-harvested-item research-tree-harvested-paper" title={p.id}>
                    {p.title.slice(0, 50) + (p.title.length > 50 ? "…" : "")}
                  </span>
                ))}
                {node.harvested_grey?.map((g) => (
                  <a key={g.id} href={g.url} target="_blank" rel="noopener noreferrer"
                    className="research-tree-harvested-item research-tree-harvested-web" title={g.url}>
                    {(g.title || g.url).slice(0, 50) + ((g.title || g.url).length > 50 ? "…" : "")}
                  </a>
                ))}
              </div>
            ) : null}
          </div>
        ) : null}
        {!isCollapsed ? children.map((child) => renderNode(child)) : null}
      </div>
    );
  }

  function renderSynthesis(doc: string): ReactNode {
    // Parse markdown into sections (heading + content) for anchor-based TOC
    type Section = { level: number; title: string; content: string };
    const sections: Section[] = [];
    let preamble = "";
    let currentSection: { level: number; title: string; lines: string[] } | null = null;
    for (const line of doc.split("\n")) {
      const h2 = line.match(/^## (.+)/);
      const h3 = line.match(/^### (.+)/);
      if (h2 || h3) {
        if (currentSection) {
          sections.push({ level: currentSection.level, title: currentSection.title, content: currentSection.lines.join("\n").trim() });
        }
        currentSection = { level: h2 ? 2 : 3, title: (h2?.[1] ?? h3?.[1] ?? ""), lines: [] };
      } else {
        if (currentSection) currentSection.lines.push(line);
        else preamble += line + "\n";
      }
    }
    if (currentSection) sections.push({ level: currentSection.level, title: currentSection.title, content: currentSection.lines.join("\n").trim() });
    preamble = preamble.trim();

    // Strip basic markdown syntax that AnswerText renders as literals (bold, bullets).
    // Citation brackets [arxiv:...] are preserved for chip rendering.
    function stripMd(text: string): string {
      return text
        .replace(/\*\*(.+?)\*\*/g, "$1")  // **bold** → bold
        .replace(/\*(.+?)\*/g, "$1")       // *italic* → italic
        .replace(/^- /gm, "• ")           // - bullet → • bullet
        .replace(/^\d+\. /gm, "");        // 1. item → item
    }

    // Aggregate verification sources and citation links from all done nodes so
    // citations in the synthesis document resolve to real paper evidence. Falls back
    // to answer.sources (always persisted) so a reloaded session keeps its citations
    // coloured instead of greying every one out as unresolved "!".
    const synthVerification: VerificationSource[] = [];
    const synthCitationLinks: CitationLink[] = [];
    const seenPaperIds = new Set<string>();
    for (const node of treeNodes) {
      for (const src of citationPoolFor(node.verification, node.answer)) {
        if (!seenPaperIds.has(src.paper_id)) {
          seenPaperIds.add(src.paper_id);
          synthVerification.push(src);
        }
      }
      if (node.answer?.citation_links) {
        synthCitationLinks.push(...node.answer.citation_links);
      }
    }

    const sharedAnswerProps = {
      getCitationMeta: (citation: string, context?: string, citationStart?: number) =>
        citationMetasFor(synthVerification, citation, context, synthCitationLinks, citationStart),
      onCitationClick: () => {},
      onCitationMetaClick: (meta: CitationMeta) => onCitationClick(meta.source, meta.evidenceIndex),
      onCitationInsert: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) =>
        onCitationInsert(source, evidenceIndex, quote, extras),
      onCitationInsertPreview: (source: VerificationSource, evidenceIndex: number, quote: string, extras?: CitationInsertExtras) =>
        onCitationInsertPreview(source, evidenceIndex, quote, extras),
      onCitationInsertPreviewClear,
    };

    // Render a section's content, turning `####` lines into <h4> subheadings instead of
    // leaving the literal hashes in the prose (sections are split on ##/### above, so only
    // h4+ reaches here).
    function renderSectionContent(content: string, keyPrefix: string): ReactNode {
      const blocks: ReactNode[] = [];
      let buffer: string[] = [];
      let counter = 0;
      const flush = () => {
        const text = buffer.join("\n").trim();
        buffer = [];
        if (text) blocks.push(<AnswerText key={`${keyPrefix}-t${counter++}`} answer={stripMd(text)} {...sharedAnswerProps} />);
      };
      for (const line of content.split("\n")) {
        const h4 = line.match(/^#{4,6}\s+(.+)/);
        if (h4) {
          flush();
          blocks.push(<h4 key={`${keyPrefix}-h${counter++}`} className="research-synthesis-h4">{stripMd(h4[1].trim())}</h4>);
        } else {
          buffer.push(line);
        }
      }
      flush();
      return blocks;
    }

    return (
      <div className="research-synthesis-panel">
        {sections.length > 0 ? (
          <nav className="research-synthesis-toc">
            <strong>Inhalt</strong>
            {sections.map((s, i) => (
              <a
                key={i}
                href={`#synth-sec-${i}`}
                className={`research-synthesis-toc-item research-synthesis-toc-level-${s.level}`}
                onClick={(e) => {
                  e.preventDefault();
                  document.getElementById(`synth-sec-${i}`)?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                {s.title}
              </a>
            ))}
            {synthVerification.length > 0 ? (
              <a
                href="#synth-sec-bibliography"
                className="research-synthesis-toc-item research-synthesis-toc-level-2"
                onClick={(e) => {
                  e.preventDefault();
                  document.getElementById("synth-sec-bibliography")?.scrollIntoView({ behavior: "smooth", block: "start" });
                }}
              >
                Quellenverzeichnis
              </a>
            ) : null}
          </nav>
        ) : null}
        <div className="research-synthesis-body">
          {preamble ? renderSectionContent(preamble, "synth-pre") : null}
          {sections.map((s, i) => (
            <div key={i} id={`synth-sec-${i}`} className="research-synthesis-section">
              {s.level === 2 ? <h2 className="research-synthesis-h2">{s.title}</h2> : <h3 className="research-synthesis-h3">{s.title}</h3>}
              {s.content ? renderSectionContent(s.content, `synth-sec-${i}`) : null}
            </div>
          ))}
          {synthVerification.length > 0 ? (
            <div id="synth-sec-bibliography" className="research-synthesis-section">
              <h2 className="research-synthesis-h2">Quellenverzeichnis</h2>
              <ol className="research-synthesis-bibliography">
                {synthVerification.map((src) => (
                  <li key={src.paper_id}>
                    <button
                      type="button"
                      className="citation-link citation-link--mapped"
                      style={colorVarsForPaperId(src.paper_id, 0)}
                      onClick={() => onCitationClick(src, 0)}
                      title={src.paper_id}
                    >
                      {src.title || src.paper_id}
                    </button>
                    <span className="muted-row" style={{ marginLeft: 6, fontSize: "11px" }}>
                      {src.paper_id}
                    </span>
                  </li>
                ))}
              </ol>
            </div>
          ) : null}
        </div>
      </div>
    );
  }

  const doneCount = treeNodes.filter((n) => n.status === "done").length;

  return (
    <div className="research-tree-panel">
      <div className="research-tree-header">
        <GitBranch size={15} />
        <strong>Tiefenanalyse</strong>
        <div className="segmented research-tree-tabs" style={{ marginLeft: "8px" }}>
          <button
            type="button"
            className={activeTab === "tree" ? "active" : ""}
            onClick={() => setActiveTab("tree")}
          >
            Baumansicht
          </button>
          <button
            type="button"
            className={activeTab === "synthesis" ? "active" : ""}
            disabled={!synthesisNode}
            onClick={() => setActiveTab("synthesis")}
          >
            Gesamtantwort
          </button>
        </div>
        <span className="muted-row" style={{ flex: 1, fontSize: "12px" }}>
          {loading ? `${treeNodes.length} Antworten erhalten…` : `${treeNodes.length} Antworten`}
        </span>
        {!loading && doneCount > 0 ? (
          <button type="button" className="icon-button" style={{ fontSize: "11px", display: "flex", alignItems: "center", gap: "3px" }} onClick={onSaveToNotes} title="Gesamten Baum in aktive Notiz speichern">
            <NotebookPen size={12} />
            <span>In Notiz</span>
          </button>
        ) : null}
        {synthesisNode?.document ? (
          <div style={{ position: "relative" }}>
            <button
              type="button"
              className="icon-button"
              style={{ fontSize: "11px", display: "flex", alignItems: "center", gap: "3px" }}
              onClick={() => { setExportOpen((o) => !o); setExportMsg(null); }}
              title="Gesamtantwort als LaTeX-PDF oder .tex/.zip exportieren"
            >
              <DownloadCloud size={12} />
              <span>PDF/LaTeX</span>
            </button>
            {exportOpen ? (
              <div
                role="dialog"
                style={{
                  position: "absolute", top: "calc(100% + 6px)", right: 0, zIndex: 50, width: 272,
                  background: "var(--surface, #ffffff)", border: "1px solid var(--border, #d4d4d4)",
                  borderRadius: 8, boxShadow: "0 6px 24px rgba(0,0,0,0.18)", padding: 12,
                  display: "flex", flexDirection: "column", gap: 8, fontSize: 12,
                  color: "var(--text, #222)", cursor: "default",
                }}
              >
                <strong style={{ fontSize: 12 }}>Als Dokument exportieren</strong>
                <div className="segmented" style={{ display: "flex" }}>
                  <button type="button" className={exportFormat === "pdf" ? "active" : ""} style={{ flex: 1 }} onClick={() => setExportFormat("pdf")}>PDF</button>
                  <button type="button" className={exportFormat === "zip" ? "active" : ""} style={{ flex: 1 }} onClick={() => setExportFormat("zip")}>LaTeX (.zip)</button>
                </div>
                {([
                  ["tikz_tree", "Forschungsbaum (TikZ)"],
                  ["charts", "Statistik-Diagramme"],
                  ["tables", "Tabellen"],
                  ["comfyui_images", "KI-Bilder (ComfyUI)"],
                ] as [keyof ResearchTreeExportOptions, string][]).map(([key, label]) => (
                  <label key={key} style={{ display: "flex", alignItems: "center", gap: 6, cursor: "pointer" }}>
                    <input
                      type="checkbox"
                      checked={exportOpts[key]}
                      onChange={(e) => setExportOpts((o) => ({ ...o, [key]: e.target.checked }))}
                    />
                    {label}
                  </label>
                ))}
                {exportOpts.comfyui_images ? (
                  <span className="muted-row" style={{ fontSize: 10 }}>
                    ComfyUI muss lokal laufen (Port 8188), sonst wird dieser Schritt übersprungen.
                  </span>
                ) : null}
                <button
                  type="button"
                  className="button button-compact"
                  disabled={exporting}
                  onClick={handleExport}
                  style={{ justifyContent: "center" }}
                >
                  {exporting ? (<><Loader2 size={12} className="spin" /> Erzeuge…</>) : (<><FileText size={12} /> Erstellen</>)}
                </button>
                {exportMsg ? (
                  <span style={{
                    fontSize: 11,
                    color: exportMsg.kind === "error" ? "var(--danger, #b00020)"
                      : exportMsg.kind === "warn" ? "var(--warning, #a85d00)"
                      : "var(--success, #1a7f37)",
                  }}>
                    {exportMsg.text}
                  </span>
                ) : null}
              </div>
            ) : null}
          </div>
        ) : null}
        {loading && !synthesisNode ? (
          <button type="button" className="button button-compact" onClick={onStop} title="Analyse stoppen">
            <Square size={12} />
            <span>Stopp</span>
          </button>
        ) : null}
        {!loading && !synthesisNode && nodes.some((n) => n.status === "done") ? (
          <button type="button" className="button button-compact" onClick={onResume} title="Analyse fortsetzen (bereits beantwortete Knoten werden übersprungen)">
            <GitBranch size={12} />
            <span>Fortsetzen</span>
          </button>
        ) : null}
        {loading && synthesisNode ? (
          <span
            className="muted-row"
            style={{ fontSize: "11px", display: "flex", alignItems: "center", gap: "4px" }}
            title="Die Gesamtantwort wird Abschnitt für Abschnitt aus den Teilantworten geschrieben."
          >
            <Loader2 size={12} className="spin" />
            {(() => {
              const written = (synthesisNode.document?.match(/^#{2,3}\s/gm) ?? []).length;
              return written > 0 ? `Synthese… (Abschnitt ${written})` : "Synthese…";
            })()}
          </span>
        ) : null}
      </div>
      {llmError ? (
        <div className={`research-tree-llm-error research-tree-llm-error--${llmError.kind}`} role="alert">
          <AlertTriangle size={15} />
          <div className="research-tree-llm-error-body">
            <strong>
              {llmError.kind === "quota"
                ? "KI-Kontingent erschöpft"
                : llmError.kind === "rate_limit"
                  ? "KI-Rate-Limit erreicht"
                  : llmError.kind === "auth"
                    ? "KI-Authentifizierung fehlgeschlagen"
                    : llmError.kind === "connection"
                      ? "Keine Verbindung zum KI-Modell"
                      : "KI-Anfrage fehlgeschlagen"}
            </strong>
            <span>{llmError.message}</span>
            <span className="research-tree-llm-error-detail">
              Die unten gezeigten Antworten sind nur evidenzbasiert (ohne KI-Synthese) und der Baum
              ist evtl. nicht vollständig verzweigt.
              {llmError.error ? ` Details: ${llmError.error.slice(0, 200)}` : ""}
            </span>
          </div>
        </div>
      ) : null}
      {activeTab === "tree" ? (
        <div className="research-tree-body">
          {rootNodes.length ? rootNodes.map((n) => renderNode(n)) : (
            loading ? <div className="muted-row"><Loader2 size={14} className="spin" /> Zerlege Frage…</div> : null
          )}
        </div>
      ) : (
        synthesisNode?.document ? renderSynthesis(synthesisNode.document) : (
          <div className="muted-row" style={{ padding: "16px" }}>
            <Loader2 size={14} className="spin" /> Gesamtantwort wird generiert…
          </div>
        )
      )}
    </div>
  );
}
