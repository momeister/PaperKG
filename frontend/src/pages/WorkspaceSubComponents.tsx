// Standalone, prop-driven sub-components extracted from WorkspacePage.tsx (they
// close over no parent state). Kept together because they reference each other
// (navigator uses PaneHeading/CollapsedPane).
import { Fragment, useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  History,
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


/** Abschnitte der Quellen-Navigation, die aus ``grey_sources`` gespeist werden. */
const GREY_SECTIONS = [
  { kind: "web", key: "grey", label: "Graue Quellen", badge: "Grauquelle", deleteTitle: "Grauquelle löschen" },
  { kind: "note", key: "grey-notes", label: "Notiz-Quellen", badge: "Notiz", deleteTitle: "Notiz-Quelle entfernen (Notiz bleibt erhalten)" },
  { kind: "analysis", key: "grey-analyses", label: "Analyse-Quellen", badge: "Tiefenanalyse", deleteTitle: "Analyse-Quelle entfernen (Analyse bleibt erhalten)" },
] as const;


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
  sessionProjectId,
  onSessionRestored,
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
  sessionProjectId: string;
  onSessionRestored: () => void;
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
  // Eine aufgeklappte Notizquelle zeigt den vollen Beleg statt der einzeiligen Kurzfassung.
  const [expandedCitationId, setExpandedCitationId] = useState("");
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
              {citations.map(({ citation, badge, title, evidence }, index) => {
                const expanded = expandedCitationId === citation.id;
                const fullText = citation.reference_text || citation.pdf_excerpt || evidence || citation.paper_id;
                return (
                  <div
                    className={`list-row note-citation-row workspace-nav-actionable-row ${expanded ? "note-citation-row--expanded" : ""} ${activeCitationId === citation.id ? "note-citation-row--active list-row--active" : ""}`}
                    key={citation.id}
                    style={colorVarsForPaperId(citation.paper_id, Number(citation.evidence_index ?? index))}
                  >
                    <button className="note-citation-row__body" type="button" onClick={() => onOpenCitation(citation)}>
                      <span className="note-citation-row__title">
                        <span className="citation-badge">{badge}</span>
                        <strong>{title}</strong>
                      </span>
                      <span className={expanded ? "note-citation-row__text note-citation-row__text--full" : "note-citation-row__text"}>
                        {expanded ? fullText : evidence || citation.paper_id}
                      </span>
                    </button>
                    <span className="workspace-row-actions" onClick={(event) => event.stopPropagation()}>
                      <button
                        className="icon-button nav-action-btn"
                        type="button"
                        title={expanded ? "Beleg einklappen" : "Vollen Beleg anzeigen"}
                        aria-expanded={expanded}
                        onClick={(event) => {
                          event.stopPropagation();
                          setExpandedCitationId(expanded ? "" : citation.id);
                        }}
                      >
                        {expanded ? <ChevronUp size={12} /> : <ChevronDown size={12} />}
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
                    </span>
                  </div>
                );
              })}
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
        {/* Web-Funde, veroeffentlichte Notizen und gespeicherte Tiefenanalysen liegen in
            derselben Tabelle (grey_sources) und sind alle als [grey::…] zitierbar — hier
            werden sie nach Herkunft getrennt aufgelistet. */}
        {GREY_SECTIONS.map(({ kind, key, label, badge, deleteTitle }) => {
          const items = greySources.filter((source) => (source.source_kind || "web") === kind);
          if (!items.length) {
            return null;
          }
          return (
            <Fragment key={key}>
              <button className="workspace-nav-subheading workspace-nav-subheading--toggle" type="button" onClick={() => toggleSection(key)}>
                <span>{label}</span>
                <strong>{items.length}</strong>
                {sectionCollapsed(key) ? <ChevronDown size={13} /> : <ChevronUp size={13} />}
              </button>
              {!sectionCollapsed(key) ? (
                <>
                  <div className="list workspace-nav-list workspace-nav-list--short" style={{ maxHeight: greySourceListHeight }}>
                    {items.map((source) => {
                      const isActiveGrey = pdfTarget?.kind === "grey" && pdfTarget.source.id === source.id;
                      const selectedForScope = selectedGreyIds.includes(source.id);
                      const derivedCount = source.source_paper_ids?.length ?? 0;
                      const subtitle = source.url || (derivedCount ? `${derivedCount} zugrundeliegende Paper` : source.summary || "");
                      return (
                        <div
                          className={`list-row workspace-paper-row workspace-grey-row workspace-nav-actionable-row ${isActiveGrey ? "workspace-paper-row--active-source list-row--active" : ""}`}
                          key={source.id}
                          role="button"
                          tabIndex={0}
                          aria-label={`${label} ${source.title || source.url} öffnen`}
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
                              <span className="grey-badge grey-badge--mini">{badge}</span>
                              {subtitle}
                            </span>
                          </div>
                          {isRealProject ? (
                            <span className="workspace-row-actions" onClick={(event) => event.stopPropagation()} onKeyDown={(event) => event.stopPropagation()}>
                              <button
                                className="icon-button nav-delete-btn"
                                type="button"
                                title={deleteTitle}
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
                  <div className="workspace-list-resize-handle" role="separator" aria-label={`${label} Hoehe anpassen`} onPointerDown={onResizeGreyList} />
                </>
              ) : null}
            </Fragment>
          );
        })}
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
        <SessionBackupsPanel projectId={sessionProjectId} onRestored={onSessionRestored} />
      </div>
    </section>
  );
}


/** Zugriff auf die rollierenden Server-Sicherungen der Workspace-Session.
 *  Sichtbares Gegenstueck zum Ueberschreibschutz im Backend: ging trotzdem etwas
 *  verloren, laesst sich hier ein frueherer Stand zurueckholen. */
function SessionBackupsPanel({ projectId, onRestored }: { projectId: string; onRestored: () => void }) {
  const [open, setOpen] = useState(false);
  const backupsQuery = useQuery({
    queryKey: ["workspace-session-backups", projectId],
    queryFn: () => api.listWorkspaceSessionBackups(projectId),
    enabled: open && Boolean(projectId)
  });
  const restore = useMutation({
    mutationFn: (savedAt: string) => api.restoreWorkspaceSession(projectId, savedAt),
    onSuccess: () => {
      setOpen(false);
      onRestored();
    }
  });
  const backups = backupsQuery.data?.backups ?? [];

  if (!open) {
    return (
      <button className="button button-compact button-ghost session-restore-toggle" type="button" onClick={() => setOpen(true)}>
        <History size={14} />
        <span>Frühere Sitzung wiederherstellen</span>
      </button>
    );
  }
  return (
    <div className="session-restore-panel">
      <div className="session-restore-head">
        <strong>Sicherungen</strong>
        <button className="icon-button icon-button--compact" type="button" aria-label="Schließen" onClick={() => setOpen(false)}>
          <X size={14} />
        </button>
      </div>
      {backupsQuery.isLoading ? <span className="muted">Lade…</span> : null}
      {!backupsQuery.isLoading && !backups.length ? (
        <span className="muted">Keine Sicherungen vorhanden.</span>
      ) : null}
      {backups.map((backup) => (
        <button
          key={backup.saved_at}
          className="button button-compact session-restore-row"
          type="button"
          disabled={restore.isPending}
          onClick={() => restore.mutate(backup.saved_at)}
        >
          <span>{new Date(backup.saved_at).toLocaleString("de-DE")}</span>
          <small>{backup.turn_count} Sitzungen</small>
        </button>
      ))}
      {restore.isError ? <span className="inline-error">Wiederherstellen fehlgeschlagen.</span> : null}
    </div>
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



export { ResearchTreeView } from "./ResearchTreeView";
