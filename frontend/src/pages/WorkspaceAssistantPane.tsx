// WorkspaceAssistantPane — der Inline-PDF/Research-Assistent (rechte Spalte) aus
// WorkspacePage.tsx extrahiert. Reine Praesentation: ALLER State/Handler bleibt in
// der Page und kommt als flache Props. Superset-Importe der Quelldatei;
// Page-Typ-Importe sind type-only (zyklusfrei).
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type {
  ChangeEvent,
  ClipboardEvent as ReactClipboardEvent,
  DragEvent as ReactDragEvent,
  FormEvent,
  KeyboardEvent as ReactKeyboardEvent,
  Dispatch,
  MutableRefObject,
  SetStateAction,
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
  DeepResearchResponse,
  GreySource,
  NoteAiMessage,
  NoteAiThread,
  NoteCitation,
  Paper,
  ParallelSession,
  ParallelSessionSummary,
  ResearchNode,
  VerificationEvidence,
  VerificationSource
} from "../types";
import { ParallelResearchPanel, ParallelSessionBrowser } from "./ParallelResearchPanel";
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
import type { AssistantAnswerBlock, AssistantTurn, CitationInsertExtras, CitationMeta } from "./AssistantPage";
import type { PaperQuestionScope, WorkspaceActionEntry, WorkspaceCommandDef } from "./WorkspacePage";
import { NotesSurface } from "./NotesPage";
import type { NotesSurfaceActions, NotesSurfaceSnapshot } from "./NotesPage";
import { AnalysisPanel } from "./AnalysisPanel";
import { DatasetsPanel } from "./DatasetsPanel";

// Pure helpers/constants live in ./workspaceHelpers; re-export for back-compat and
// import the ones this file uses directly.
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
  clearApproximateOnLinks,
  replaceStatementInAnswerText,
  statementPattern,
  stripCitationFromAnswerText,
  updateEvidenceInSources,
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

import {
  CollapsedPane,
  PaneHeading,
  ResearchTreeView,
  WorkspaceNavigatorBody,
  WorkspaceNotesAssistant,
} from "./WorkspaceSubComponents";

// Flache Props: der gesamte State/alle Handler leben weiter in WorkspacePage.
// Mutations/Ableitungen sind strukturell auf die hier genutzte Oberflaeche typisiert.
export interface WorkspaceAssistantPaneProps {
  actionHistory: WorkspaceActionEntry[];
  actionLog: WorkspaceActionEntry[];
  actionsMenuOpen: boolean;
  activeBlocks: AssistantAnswerBlock[];
  activeCommandHint: WorkspaceCommandDef | null;
  activeEvidence: VerificationEvidence | undefined;
  activeEvidenceIndex: number;
  activeProject: string | undefined;
  activeTurn: AssistantTurn | null;
  addUrlSource: { isPending: boolean; mutate: (url: string) => void };
  answer: Answer | null;
  answerBlocksRef: MutableRefObject<HTMLDivElement | null>;
  answerMutation: { isPending: boolean };
  answerSelection: { text: string; blockId: string; left: number; top: number } | null;
  appendActiveQuote: (sourceKind: "reference" | "pdf") => void;
  appendAnswerToNote: () => void;
  applyAnswerCorrection: () => void;
  applyMention: (paper: Paper) => void;
  applyPaletteCommand: (command: WorkspaceCommandDef) => void;
  askAboutSelection: () => void;
  autoAbortRef: MutableRefObject<AbortController | null>;
  autoProgress: {
    phase: string;
    relatedTopics: string[];
    papers: { id: string; title: string }[];
    grey: { id: string; title: string; url: string }[];
  } | null;
  autoResearch: boolean;
  chatSettingsOpen: boolean;
  checkAnswerSelection: () => void;
  citationVerifyPending: boolean;
  clarifyLoading: boolean;
  closeAnswerSelection: () => void;
  commandSearch: { query: string; results: Paper[]; selected: string[] } | null;
  conversationMode: "followup" | "new";
  correctionDraft: string | null;
  createNoteCommand: { mutate: (noteTitle: string) => void };
  criticalMode: boolean;
  deepBranches: number;
  deepDepth: number;
  deepMode: boolean;
  deriveScope: (scope: PaperQuestionScope) => { scopedPaperIds: string[] };
  downloadPapersCommand: { isPending: boolean; mutate: (papers: Paper[]) => void };
  drillDeeperInTree: (nodeId: string, question: string) => void;
  evidenceMode: string;
  evidenceOpen: boolean;
  extractDroppedSource: { isPending: boolean; mutate: (paperId: string) => void };
  handleAnswerMouseUp: () => void;
  handleChatDragLeave: (event: ReactDragEvent<HTMLFormElement>) => void;
  handleChatDragOver: (event: ReactDragEvent<HTMLFormElement>) => void;
  handleChatDrop: (event: ReactDragEvent<HTMLFormElement>) => void;
  handleChatPaste: (event: ReactClipboardEvent<HTMLFormElement>) => void;
  handleQuestionChange: (event: ChangeEvent<HTMLInputElement>) => void;
  handleQuestionKeyDown: (event: ReactKeyboardEvent<HTMLInputElement>) => void;
  handleUnresolvedCitationClick: (citationId: string) => void;
  includeGlobalSources: boolean;
  insertCitationFromAnswer: (source: VerificationSource, evidenceIndex: number, quote?: string, extras?: CitationInsertExtras) => void;
  insertSelectionIntoNote: () => void;
  isDraggingOverChat: boolean;
  isRealProject: boolean;
  jumpToCitationIn: (pool: VerificationSource[], citation: string, context?: string, quote?: string, links?: CitationLink[], citationStart?: number) => void;
  jumpToCitationMeta: (meta: CitationMeta, context?: string, quote?: string) => void;
  latestAnswerNeedsWeb: boolean;
  latestBlock: AssistantAnswerBlock | null;
  launchResearchTree: (q: string, useHarvest: boolean, clarificationContext: string, initialNodes?: ResearchNode[]) => void;
  mentionCandidates: Paper[];
  mentionHighlight: number;
  mentionState: { query: string; start: number; end: number } | null;
  model: string | undefined;
  noteStatus: string;
  notesActionsRef: MutableRefObject<NotesSurfaceActions | null>;
  onParallelChange: (session: ParallelSession) => void;
  openAssistantSource: (source: VerificationSource | null, evidenceIndex?: number, quote?: string, options?: { openPdf?: boolean; syncPdfTarget?: boolean }) => void;
  openGreySource: (source: GreySource) => void;
  openParallelServerSession: (summary: ParallelSessionSummary) => void;
  openSelectedAssistantPdf: () => void;
  paletteCandidates: WorkspaceCommandDef[];
  paletteIndex: number;
  paletteQuery: string | null;
  paperScope: PaperQuestionScope;
  paperSearchCommand: { isPending: boolean };
  parallelFollowupLoading: boolean;
  parallelLoading: boolean;
  parallelMode: boolean;
  parallelSession: ParallelSession | null;
  pdfPapers: Paper[];
  pendingUrlSource: { url: string } | null;
  placeCursorAfter: (position: number) => void;
  previewActiveQuote: (sourceKind: "reference" | "pdf") => void;
  previewAnswerToNote: () => void;
  previewCitationFromAnswer: (source: VerificationSource, evidenceIndex: number, quote?: string, extras?: CitationInsertExtras) => void;
  previewSelectionInNote: () => void;
  provider: string | undefined;
  question: string;
  questionBlockedByScope: boolean;
  questionInputRef: MutableRefObject<HTMLInputElement | null>;
  removeCitationFromBlock: (blockId: string, paperId: string, statement: string) => void;
  removeStatementFromBlock: (blockId: string, statement: string) => void;
  researchLlmError: { kind: string; message: string; error: string } | null;
  researchLoading: boolean;
  researchNodes: ResearchNode[];
  runAutoResearch: (value: string, opts: { scope: PaperQuestionScope; newTurn: boolean; force?: boolean }) => Promise<void>;
  runExtractionCommand: () => void;
  saveGreyMutation: { isPending: boolean; mutate: (finding: DeepResearchFinding) => void };
  saveResearchTreeToNotes: () => Promise<void>;
  saveResearchTreeAsSource: () => Promise<{ paper_count: number }>;
  scopedProjectId: string;
  selectedGreyIds: string[];
  selectedPaperIds: string[];
  selectedSource: VerificationSource | null;
  selectionBlock: AssistantAnswerBlock | undefined;
  selectionCheck: { status: "loading" | "done" | "error"; results?: ClaimCheckResult[]; error?: string } | null;
  setActionLog: Dispatch<SetStateAction<WorkspaceActionEntry[]>>;
  setActionsMenuOpen: Dispatch<SetStateAction<boolean>>;
  setAutoResearch: Dispatch<SetStateAction<boolean>>;
  setChatSettingsOpen: Dispatch<SetStateAction<boolean>>;
  setCommandSearch: Dispatch<SetStateAction<{ query: string; results: Paper[]; selected: string[] } | null>>;
  setConversationMode: Dispatch<SetStateAction<"followup" | "new">>;
  setCorrectionDraft: Dispatch<SetStateAction<string | null>>;
  setCriticalMode: Dispatch<SetStateAction<boolean>>;
  setDeepBranches: Dispatch<SetStateAction<number>>;
  setDeepDepth: Dispatch<SetStateAction<number>>;
  setDeepMode: Dispatch<SetStateAction<boolean>>;
  setEvidenceMode: Dispatch<SetStateAction<string>>;
  setEvidenceOpen: Dispatch<SetStateAction<boolean>>;
  setIncludeGlobalSources: Dispatch<SetStateAction<boolean>>;
  setPaperScope: Dispatch<SetStateAction<PaperQuestionScope>>;
  setParallelMode: Dispatch<SetStateAction<boolean>>;
  setPendingUrlSource: Dispatch<SetStateAction<{ url: string } | null>>;
  setQuestion: Dispatch<SetStateAction<string>>;
  setShowCommandHelp: Dispatch<SetStateAction<boolean>>;
  setUseInternet: Dispatch<SetStateAction<boolean>>;
  setVerbosity: Dispatch<SetStateAction<"kurz" | "standard" | "ausführlich">>;
  setWebOfferDismissedFor: Dispatch<SetStateAction<string>>;
  showCommandHelp: boolean;
  sourceIngestStatus: string;
  stopResearchTree: () => void;
  submit: (event: FormEvent) => void;
  toggleScopedGrey: (greyId: string) => void;
  toggleScopedPaper: (paperId: string) => void;
  updateCitationEvidenceInBlock: (blockId: string, source: VerificationSource, evidenceIndex: number, quotes: string[], statement: string) => void;
  uploadDroppedPdf: { isSuccess: boolean; data: { paper: Paper } | undefined };
  useInternet: boolean;
  verbosity: "kurz" | "standard" | "ausführlich";
  verification: VerificationSource[];
  webMutation: { isPending: boolean; isError: boolean; mutate: (value: string) => void };
  webOfferDismissedFor: string;
  webResult: DeepResearchResponse | null;
}

export function WorkspaceAssistantPane(props: WorkspaceAssistantPaneProps) {
  const {
    actionHistory,
    actionLog,
    actionsMenuOpen,
    activeBlocks,
    activeCommandHint,
    activeEvidence,
    activeEvidenceIndex,
    activeProject,
    activeTurn,
    addUrlSource,
    answer,
    answerBlocksRef,
    answerMutation,
    answerSelection,
    appendActiveQuote,
    appendAnswerToNote,
    applyAnswerCorrection,
    applyMention,
    applyPaletteCommand,
    askAboutSelection,
    autoAbortRef,
    autoProgress,
    autoResearch,
    chatSettingsOpen,
    checkAnswerSelection,
    citationVerifyPending,
    clarifyLoading,
    closeAnswerSelection,
    commandSearch,
    conversationMode,
    correctionDraft,
    createNoteCommand,
    criticalMode,
    deepBranches,
    deepDepth,
    deepMode,
    deriveScope,
    downloadPapersCommand,
    drillDeeperInTree,
    evidenceMode,
    evidenceOpen,
    extractDroppedSource,
    handleAnswerMouseUp,
    handleChatDragLeave,
    handleChatDragOver,
    handleChatDrop,
    handleChatPaste,
    handleQuestionChange,
    handleQuestionKeyDown,
    handleUnresolvedCitationClick,
    includeGlobalSources,
    insertCitationFromAnswer,
    insertSelectionIntoNote,
    isDraggingOverChat,
    isRealProject,
    jumpToCitationIn,
    jumpToCitationMeta,
    latestAnswerNeedsWeb,
    latestBlock,
    launchResearchTree,
    mentionCandidates,
    mentionHighlight,
    mentionState,
    model,
    noteStatus,
    notesActionsRef,
    onParallelChange,
    openAssistantSource,
    openGreySource,
    openParallelServerSession,
    openSelectedAssistantPdf,
    paletteCandidates,
    paletteIndex,
    paletteQuery,
    paperScope,
    paperSearchCommand,
    parallelFollowupLoading,
    parallelLoading,
    parallelMode,
    parallelSession,
    pdfPapers,
    pendingUrlSource,
    placeCursorAfter,
    previewActiveQuote,
    previewAnswerToNote,
    previewCitationFromAnswer,
    previewSelectionInNote,
    provider,
    question,
    questionBlockedByScope,
    questionInputRef,
    removeCitationFromBlock,
    removeStatementFromBlock,
    researchLlmError,
    researchLoading,
    researchNodes,
    runAutoResearch,
    runExtractionCommand,
    saveGreyMutation,
    saveResearchTreeToNotes,
    saveResearchTreeAsSource,
    scopedProjectId,
    selectedGreyIds,
    selectedPaperIds,
    selectedSource,
    selectionBlock,
    selectionCheck,
    setActionLog,
    setActionsMenuOpen,
    setAutoResearch,
    setChatSettingsOpen,
    setCommandSearch,
    setConversationMode,
    setCorrectionDraft,
    setCriticalMode,
    setDeepBranches,
    setDeepDepth,
    setDeepMode,
    setEvidenceMode,
    setEvidenceOpen,
    setIncludeGlobalSources,
    setPaperScope,
    setParallelMode,
    setPendingUrlSource,
    setQuestion,
    setShowCommandHelp,
    setUseInternet,
    setVerbosity,
    setWebOfferDismissedFor,
    showCommandHelp,
    sourceIngestStatus,
    stopResearchTree,
    submit,
    toggleScopedGrey,
    toggleScopedPaper,
    updateCitationEvidenceInBlock,
    uploadDroppedPdf,
    useInternet,
    verbosity,
    verification,
    webMutation,
    webOfferDismissedFor,
    webResult,
  } = props;
  return (
            <>
          <div className="chat-input-area">
          {mentionState && mentionCandidates.length > 0 ? (
            <div className="mention-popover">
              {mentionCandidates.map((paper, i) => {
                const norm = normalizeWorkspacePaper(paper);
                return (
                  <button
                    key={workspacePaperId(norm)}
                    type="button"
                    className={`mention-popover-row ${i === mentionHighlight ? "mention-popover-row--active" : ""}`}
                    onMouseDown={(e) => { e.preventDefault(); applyMention(paper); }}
                  >
                    {workspacePaperTitle(norm)}
                  </button>
                );
              })}
              <span className="mention-popover-hint">Tab · Pfeiltasten · Enter zum Übernehmen</span>
            </div>
          ) : null}
          {paletteQuery !== null && paletteCandidates.length > 0 ? (
            <div className="command-palette-popover" role="listbox" aria-label="Befehle">
              <div className="command-palette-head">
                <Command size={13} />
                <span>Befehle</span>
              </div>
              {paletteCandidates.map((command, index) => (
                <button
                  type="button"
                  key={command.name}
                  className={`command-palette-row ${index === paletteIndex ? "command-palette-row--active" : ""}`}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    applyPaletteCommand(command);
                  }}
                >
                  <code>
                    /{command.name}
                    {command.args ? <em> {command.args}</em> : null}
                  </code>
                  <span>{command.description}</span>
                  {command.group === "aktion" ? <small>Aktion</small> : null}
                </button>
              ))}
              <span className="mention-popover-hint">↑↓ wählen · Tab vervollständigen · Enter ausführen</span>
            </div>
          ) : null}
          {activeCommandHint ? (
            <div className="command-arg-hint">
              <code>/{activeCommandHint.name}</code>
              {activeCommandHint.args ? <em>{activeCommandHint.args}</em> : null}
              <span>{activeCommandHint.description}</span>
            </div>
          ) : null}
          {showCommandHelp ? (
            <div className="command-help-popover">
              <div className="command-help-head">
                <span>Verfügbare Befehle</span>
                <button className="icon-button icon-button--compact" type="button" aria-label="Hilfe schließen" onClick={() => setShowCommandHelp(false)}>
                  <X size={13} />
                </button>
              </div>
              {WORKSPACE_COMMANDS.map((command) => (
                <div className="command-help-row" key={command.name}>
                  <code>
                    /{command.name}
                    {command.args ? ` ${command.args}` : ""}
                  </code>
                  {command.description}
                </div>
              ))}
              <div className="command-help-row"><code>@Titel</code> Paper zur Auswahl hinzufügen</div>
            </div>
          ) : null}
          <form
            className={`chat-composer ${isDraggingOverChat ? "chat-box--drag-over" : ""}`}
            onSubmit={submit}
            onDragOver={handleChatDragOver}
            onDragLeave={handleChatDragLeave}
            onDrop={handleChatDrop}
            onPaste={handleChatPaste}
          >
            {isDraggingOverChat ? (
              <div className="chat-box-drop-hint" aria-hidden="true">
                <Upload size={18} />
                <span>PDF, Bild oder Link hier ablegen</span>
              </div>
            ) : null}
            <div className="chat-composer-input">
              <Bot size={18} />
              <input
                ref={questionInputRef}
                value={question}
                onChange={handleQuestionChange}
                onKeyDown={handleQuestionKeyDown}
                placeholder="Frage stellen — / für Befehle, @ für Papers"
              />
              <button className="icon-button chat-send-button" aria-label="Senden" disabled={answerMutation.isPending || citationVerifyPending || questionBlockedByScope}>
                <Send size={17} />
              </button>
            </div>
            <div className="chat-composer-toolbar">
              <div className="segmented workspace-scope-segment" aria-label="Paper-Scope">
                <button type="button" className={paperScope === "current" ? "active" : ""} onClick={() => setPaperScope("current")} title="Nur das gerade geöffnete Paper">
                  Dieses
                </button>
                <button type="button" className={paperScope === "selected" ? "active" : ""} onClick={() => setPaperScope("selected")} title="Nur angehakte Quellen">
                  Auswahl
                </button>
                <button type="button" className={paperScope === "all" ? "active" : ""} onClick={() => setPaperScope("all")} title="Alle Quellen des Projekts">
                  Alle
                </button>
              </div>
              {paperScope === "all" && isRealProject ? (
                <div className="segmented workspace-scope-segment" aria-label="Quellenbasis">
                  <button
                    type="button"
                    className={!includeGlobalSources ? "active" : ""}
                    onClick={() => setIncludeGlobalSources(false)}
                    title="Nur Paper und Quellen aus diesem Projekt verwenden"
                  >
                    Projekt
                  </button>
                  <button
                    type="button"
                    className={includeGlobalSources ? "active" : ""}
                    onClick={() => setIncludeGlobalSources(true)}
                    title="Zusätzlich Paper aus dem globalen Wissensgraphen (alle Projekte) heranziehen"
                  >
                    + Global
                  </button>
                </div>
              ) : null}
              <button
                type="button"
                className={`internet-toggle ${useInternet ? "internet-toggle--on" : ""}`}
                onClick={() => setUseInternet((v) => !v)}
                disabled={!isRealProject}
                title="Zusätzlich das Web durchsuchen (Grauquellen, nicht im Knowledge Graph) — auch per /web"
              >
                <Globe size={14} />
                <span>Web</span>
              </button>
              <button
                type="button"
                className={`internet-toggle ${autoResearch ? "internet-toggle--on" : ""}`}
                onClick={() => setAutoResearch((v) => !v)}
                title="Auto-Recherche: findet die Frage lokal keine Antwort, werden automatisch Paper (mit Extraktion) und Webquellen — auch zu verwandten Themen — geladen und die Frage neu beantwortet. Auch per /auto."
              >
                <Sparkles size={14} />
                <span>Auto-Recherche</span>
              </button>
              <button
                type="button"
                className={`internet-toggle ${criticalMode ? "internet-toggle--on" : ""}`}
                onClick={() => setCriticalMode((v) => !v)}
                title="Kritischer Modus: Antworten benennen Limitationen, Risiken und Gegenbelege explizit. Auch per /kritisch."
              >
                <ShieldAlert size={14} />
                <span>Kritisch</span>
              </button>
              <button
                type="button"
                className={`internet-toggle ${deepMode ? "internet-toggle--on" : ""}`}
                onClick={() => setDeepMode((v) => { const next = !v; if (next) setParallelMode(false); return next; })}
                title="Tiefenanalyse: Frage wird in Sub-Fragen zerlegt und jede separat beantwortet"
              >
                <GitBranch size={14} />
                <span>Tiefenanalyse</span>
              </button>
              <button
                type="button"
                className={`internet-toggle ${parallelMode ? "internet-toggle--on" : ""}`}
                onClick={() => setParallelMode((v) => { const next = !v; if (next) setDeepMode(false); return next; })}
                title="Parallel Research: KI schlägt Varianten vor, du probierst sie aus und sendest Ergebnisse zurück"
              >
                <GitMerge size={14} />
                <span>Parallel</span>
              </button>
              {deepMode ? (
                <span className="chat-tool-wrap">
                  <select
                    aria-label="Tiefe"
                    value={deepDepth}
                    onChange={(e) => setDeepDepth(Number(e.target.value))}
                    title="Wie tief soll die Analyse gehen?"
                    style={{ fontSize: "12px", padding: "2px 4px" }}
                  >
                    <option value={1}>Tiefe 1</option>
                    <option value={2}>Tiefe 2</option>
                    <option value={3}>Tiefe 3</option>
                    <option value={4}>Tiefe 4</option>
                    <option value={5}>Tiefe 5</option>
                    <option value={6}>Tiefe 6</option>
                  </select>
                  <select
                    aria-label="Verzweigungen"
                    value={deepBranches}
                    onChange={(e) => setDeepBranches(Number(e.target.value))}
                    title="Anzahl Sub-Fragen pro Ebene"
                    style={{ fontSize: "12px", padding: "2px 4px" }}
                  >
                    <option value={2}>2 Zweige</option>
                    <option value={3}>3 Zweige</option>
                    <option value={4}>4 Zweige</option>
                    <option value={5}>5 Zweige</option>
                    <option value={6}>6 Zweige</option>
                    <option value={7}>7 Zweige</option>
                    <option value={8}>8 Zweige</option>
                  </select>
                </span>
              ) : null}
              {clarifyLoading ? (
                <span className="muted-row" style={{ fontSize: "11px", display: "flex", alignItems: "center", gap: "4px" }}>
                  <Loader2 size={12} className="spin" /> Klärungsfragen…
                </span>
              ) : null}
              <span className="chat-composer-spacer" />
              <span className="chat-tool-wrap">
                <button
                  type="button"
                  className={`icon-button ${chatSettingsOpen ? "icon-button--active" : ""}`}
                  aria-label="Antwort-Einstellungen"
                  title="Antwort-Einstellungen (Chatmodus, Evidenz, Länge)"
                  onClick={() => {
                    setChatSettingsOpen((v) => !v);
                    setActionsMenuOpen(false);
                  }}
                >
                  <Settings2 size={16} />
                </button>
                {chatSettingsOpen ? (
                  <div className="chat-tool-popover">
                    <label>
                      Chatmodus
                      <select aria-label="Chatmodus" value={conversationMode} onChange={(event) => setConversationMode(event.target.value as "followup" | "new")}>
                        <option value="followup">Weiterfragen</option>
                        <option value="new">Neu starten</option>
                      </select>
                    </label>
                    <label>
                      Evidenzmenge
                      <select aria-label="Evidenzmenge" value={evidenceMode} onChange={(event) => setEvidenceMode(event.target.value)}>
                        <option value="auto">Auto</option>
                        <option value="12">12</option>
                        <option value="20">20</option>
                        <option value="25">25</option>
                      </select>
                    </label>
                    <label>
                      Antwortlänge
                      <select aria-label="Antwortlänge" value={verbosity} onChange={(event) => setVerbosity(event.target.value as typeof verbosity)}>
                        <option value="kurz">Kurz</option>
                        <option value="standard">Standard</option>
                        <option value="ausführlich">Ausführlich</option>
                      </select>
                    </label>
                  </div>
                ) : null}
              </span>
              <span className="chat-tool-wrap">
                <button
                  type="button"
                  className={`icon-button ${actionsMenuOpen ? "icon-button--active" : ""}`}
                  aria-label="Workspace-Aktionen"
                  title="Workspace-Aktionen: Papers importieren, Projekt anlegen, Extraktion …"
                  onClick={() => {
                    setActionsMenuOpen((v) => !v);
                    setChatSettingsOpen(false);
                  }}
                >
                  <Plus size={16} />
                </button>
                {actionsMenuOpen ? (
                  <div className="chat-tool-popover chat-actions-menu">
                    <button
                      type="button"
                      onClick={() => {
                        setActionsMenuOpen(false);
                        setQuestion("/suche ");
                        placeCursorAfter(7);
                      }}
                    >
                      <DownloadCloud size={15} />
                      <span>Papers suchen &amp; importieren</span>
                      <code>/suche</code>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setActionsMenuOpen(false);
                        setQuestion("/projekt ");
                        placeCursorAfter(9);
                      }}
                    >
                      <FolderPlus size={15} />
                      <span>Neues Projekt anlegen</span>
                      <code>/projekt</code>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setActionsMenuOpen(false);
                        createNoteCommand.mutate("");
                      }}
                    >
                      <NotebookPen size={15} />
                      <span>Neue Notiz</span>
                      <code>/notiz</code>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setActionsMenuOpen(false);
                        runExtractionCommand();
                      }}
                    >
                      <FileSearch size={15} />
                      <span>Extraktion starten</span>
                      <code>/extraktion</code>
                    </button>
                    <button
                      type="button"
                      onClick={() => {
                        setActionsMenuOpen(false);
                        setShowCommandHelp(true);
                      }}
                    >
                      <Command size={15} />
                      <span>Alle Befehle anzeigen</span>
                      <code>/help</code>
                    </button>
                  </div>
                ) : null}
              </span>
            </div>
            {paperScope === "selected" && (selectedPaperIds.length > 0 || selectedGreyIds.length > 0) ? (
              <div className="mention-chip-row">
                {selectedPaperIds.map((pid) => {
                  const paper = pdfPapers.find((p) => workspacePaperId(normalizeWorkspacePaper(p)) === pid);
                  const title = paper ? workspacePaperTitle(normalizeWorkspacePaper(paper)) : pid;
                  const short = title.length > 32 ? `${title.slice(0, 29)}…` : title;
                  return (
                    <span key={pid} className="mention-chip">
                      <span className="mention-chip-label">{short}</span>
                      <button type="button" className="icon-button" aria-label="Entfernen" onClick={() => toggleScopedPaper(pid)}>
                        <X size={11} />
                      </button>
                    </span>
                  );
                })}
                {selectedGreyIds.map((gid) => {
                  const short = gid.replace(/^grey::/, "").slice(0, 32);
                  return (
                    <span key={gid} className="mention-chip mention-chip--grey">
                      <span className="mention-chip-label">{short}</span>
                      <button type="button" className="icon-button" aria-label="Entfernen" onClick={() => toggleScopedGrey(gid)}>
                        <X size={11} />
                      </button>
                    </span>
                  );
                })}
              </div>
            ) : null}
          </form>
          </div>
          {pendingUrlSource ? (
            <div className="source-drop-confirm">
              <Link2 size={15} />
              <div className="source-drop-confirm-body">
                <strong title={pendingUrlSource.url}>{pendingUrlSource.url}</strong>
                {!isRealProject ? (
                  <span className="muted">Web-Quellen brauchen ein echtes Projekt (nicht „Alle Papers“).</span>
                ) : (
                  <span className="muted">Als Grauquelle speichern? Inhalt wird bereinigt, nicht in den Knowledge Graph aufgenommen.</span>
                )}
              </div>
              <button
                className="button button-compact"
                type="button"
                disabled={!isRealProject || addUrlSource.isPending}
                onClick={() => addUrlSource.mutate(pendingUrlSource.url)}
              >
                {addUrlSource.isPending ? "Lädt …" : "Speichern"}
              </button>
              <button className="icon-button" type="button" aria-label="Verwerfen" onClick={() => setPendingUrlSource(null)}>
                <X size={15} />
              </button>
            </div>
          ) : null}
          {sourceIngestStatus ? (
            <div className="scope-status source-ingest-status">
              <span>{sourceIngestStatus}</span>
              {uploadDroppedPdf.isSuccess && uploadDroppedPdf.data ? (
                <button
                  className="button button-compact button-ghost"
                  type="button"
                  title="Extraktion für die neu hinzugefügte Quelle starten"
                  disabled={extractDroppedSource.isPending}
                  onClick={() => extractDroppedSource.mutate(uploadDroppedPdf.data!.paper.id)}
                >
                  <FileSearch size={13} />
                  <span>Extrahieren</span>
                </button>
              ) : null}
            </div>
          ) : null}
          {useInternet && !isRealProject ? (
            <div className="scope-status">Internet-Recherche braucht ein echtes Projekt (nicht „Alle Papers“).</div>
          ) : null}
          {questionBlockedByScope ? (
            <div className="scope-status">
              {paperScope === "selected" ? "Keine Quellen ausgewählt" : "Kein aktives Paper"}
            </div>
          ) : null}
          {actionLog.length ? (
            <div className="workspace-action-log">
              {actionLog.map((entry) => (
                <div className={`workspace-action-entry workspace-action-entry--${entry.status}`} key={entry.id}>
                  <strong>{entry.title}</strong>
                  <span>{entry.detail}</span>
                  <button
                    className="icon-button icon-button--compact"
                    type="button"
                    aria-label="Eintrag entfernen"
                    onClick={() => setActionLog((current) => current.filter((item) => item.id !== entry.id))}
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
          ) : null}
          {paperSearchCommand.isPending ? <div className="scope-status">Suche Papers …</div> : null}
          {commandSearch ? (
            <section className="panel command-search-panel">
              <div className="panel-heading">
                <div>
                  <span>Paper-Suche</span>
                  <strong>„{commandSearch.query}" — {commandSearch.results.length} Treffer</strong>
                </div>
                <button className="icon-button" type="button" aria-label="Suche schließen" onClick={() => setCommandSearch(null)}>
                  <X size={15} />
                </button>
              </div>
              <div className="list command-search-list">
                {commandSearch.results.map((paper) => {
                  const checked = commandSearch.selected.includes(paper.id);
                  return (
                    <label className="list-row command-search-row" key={paper.id}>
                      <input
                        type="checkbox"
                        checked={checked}
                        onChange={() =>
                          setCommandSearch((current) =>
                            current
                              ? {
                                  ...current,
                                  selected: checked
                                    ? current.selected.filter((id) => id !== paper.id)
                                    : [...current.selected, paper.id]
                                }
                              : current
                          )
                        }
                      />
                      <div>
                        <strong>{paper.title}</strong>
                        <span>{[paper.id, paper.year ?? ""].filter(Boolean).join(" · ")}</span>
                      </div>
                    </label>
                  );
                })}
                {!commandSearch.results.length ? <EmptyState title="Keine Treffer" /> : null}
              </div>
              <div className="button-row">
                <button
                  className="button button-primary button-compact"
                  type="button"
                  disabled={!commandSearch.selected.length || downloadPapersCommand.isPending}
                  onClick={() => downloadPapersCommand.mutate(commandSearch.results.filter((paper) => commandSearch.selected.includes(paper.id)))}
                >
                  <DownloadCloud size={14} />
                  <span>{downloadPapersCommand.isPending ? "Lädt …" : `${commandSearch.selected.length} herunterladen${isRealProject ? " → Projekt" : ""}`}</span>
                </button>
              </div>
            </section>
          ) : null}
          <section className="answer-panel workspace-answer-panel">
            {parallelMode ? (
              <>
                <ParallelSessionBrowser
                  projectId={activeProject || scopedProjectId}
                  activeSessionId={parallelSession?.id}
                  defaultOpen={!parallelSession && !parallelLoading}
                  onOpen={openParallelServerSession}
                />
                {parallelSession ? (
                  <ParallelResearchPanel
                    session={parallelSession}
                    loading={parallelLoading}
                    followupLoading={parallelFollowupLoading}
                    provider={provider || undefined}
                    model={model || undefined}
                    paperIds={deriveScope(paperScope).scopedPaperIds}
                    onChange={onParallelChange}
                    onOpenCitation={(source, evidenceIndex) => openAssistantSource(source, evidenceIndex, "", { openPdf: true })}
                  />
                ) : parallelLoading ? (
                  <div className="parallel-loading">
                    <Loader2 size={18} className="spin" />
                    <span>Etappen &amp; Varianten werden vorgeschlagen…</span>
                  </div>
                ) : (
                  <EmptyState title="Parallel Research">
                    Stelle eine Frage, zu der du ein Forschungsvorhaben in Etappen aufbauen willst —
                    oder öffne oben eine frühere Session.
                  </EmptyState>
                )}
              </>
            ) : null}
            {!parallelMode && deepMode && (researchLoading || researchNodes.length > 0 || researchLlmError) ? (
              <ResearchTreeView
                nodes={researchNodes}
                loading={researchLoading}
                llmError={researchLlmError}
                onStop={stopResearchTree}
                onResume={() => {
                  const rootQ = researchNodes.find((n) => n.parent_id === null)?.question ?? "";
                  if (rootQ) launchResearchTree(rootQ, false, "", researchNodes);
                }}
                onCitationClick={(source, evidenceIndex) => openAssistantSource(source, evidenceIndex, "", { openPdf: true })}
                onCitationInsert={(source, evidenceIndex, quote, extras) => insertCitationFromAnswer(source, evidenceIndex, quote, extras)}
                onCitationInsertPreview={(source, evidenceIndex, quote, extras) => previewCitationFromAnswer(source, evidenceIndex, quote, extras)}
                onCitationInsertPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
                onDrillDeeper={(nodeId, question) => drillDeeperInTree(nodeId, question)}
                onSaveToNotes={() => void saveResearchTreeToNotes()}
                onSaveAsSource={saveResearchTreeAsSource}
              />
            ) : null}
            {!parallelMode && !deepMode && autoProgress ? (
              <div className="web-offer-card auto-research-card">
                <Loader2 size={15} className="spin" />
                <div>
                  <strong>Auto-Recherche läuft …</strong>
                  <span>{autoProgress.phase}</span>
                  {autoProgress.relatedTopics.length ? (
                    <div className="web-research-topics" style={{ marginTop: "4px" }}>
                      <span className="muted">Verwandte Themen:</span>
                      {autoProgress.relatedTopics.slice(0, 8).map((topic) => (
                        <span className="topic-chip" key={topic}>{topic}</span>
                      ))}
                    </div>
                  ) : null}
                  {autoProgress.papers.length || autoProgress.grey.length ? (
                    <span className="muted" style={{ marginTop: "4px" }}>
                      Bisher: {autoProgress.papers.length} Paper · {autoProgress.grey.length} Web-Quellen
                    </span>
                  ) : null}
                </div>
                <button
                  className="icon-button"
                  type="button"
                  aria-label="Auto-Recherche abbrechen"
                  title="Auto-Recherche abbrechen"
                  onClick={() => autoAbortRef.current?.abort()}
                >
                  <Square size={14} />
                </button>
              </div>
            ) : null}
            {!parallelMode && !deepMode && !autoProgress && activeTurn && latestBlock && latestAnswerNeedsWeb && isRealProject && !webMutation.isPending && webOfferDismissedFor !== latestBlock.id ? (
              <div className="web-offer-card">
                <Globe size={15} />
                <div>
                  <strong>Lokal keine ausreichende Antwort gefunden.</strong>
                  <span>Soll ich im Internet nachschlagen? Treffer landen als Grauquellen, nicht im Knowledge Graph.</span>
                </div>
                <button
                  className="button button-compact button-primary"
                  type="button"
                  disabled={!!autoProgress}
                  onClick={() => {
                    setWebOfferDismissedFor(latestBlock.id);
                    void runAutoResearch(latestBlock.question, { scope: paperScope, newTurn: false, force: true });
                  }}
                  title="Automatisch Paper (mit Extraktion) und Webquellen — auch zu verwandten Themen — laden und neu beantworten"
                >
                  <Sparkles size={14} /> Automatisch recherchieren
                </button>
                <button
                  className="button button-compact"
                  type="button"
                  onClick={() => {
                    setUseInternet(true);
                    webMutation.mutate(latestBlock.question);
                  }}
                >
                  Nur im Web nachschlagen
                </button>
                <button className="icon-button" type="button" aria-label="Hinweis ausblenden" onClick={() => setWebOfferDismissedFor(latestBlock.id)}>
                  <X size={14} />
                </button>
              </div>
            ) : null}
            {citationVerifyPending ? (
              <div className="scope-status">
                <Loader2 size={13} className="spin" /> Prüfe unsichere Zitate gegen die Quellen …
              </div>
            ) : null}
            {!parallelMode && !deepMode && activeTurn && activeTurn.type !== "research_tree" ? (
              <div className="answer-blocks" ref={answerBlocksRef} onMouseUp={handleAnswerMouseUp}>
                {activeBlocks.filter((block) => block.answer).map((block, index) => (
                  <article className={`answer-block ${index > 0 ? "answer-block--followup" : ""}`} key={block.id} data-block-id={block.id}>
                    <div className="answer-question">{block.question}</div>
                    <div className="answer-text">
                      <AnswerText
                        answer={block.answer.answer}
                        citationLinks={block.answer.citation_links ?? []}
                        onCitationClick={(citation, context, quote, citationStart) =>
                          jumpToCitationIn(block.verification, citation, context, quote, block.answer.citation_links ?? [], citationStart)
                        }
                        onCitationMetaClick={(meta, context, quote) => jumpToCitationMeta(meta, context, quote)}
                        onUnresolvedCitationClick={handleUnresolvedCitationClick}
                        getCitationMeta={(citation, context, citationStart) =>
                          citationMetasFor(block.verification, citation, context, block.answer.citation_links ?? [], citationStart)
                        }
                        activeCitation={selectedSource ? { paperId: selectedSource.paper_id, evidenceIndex: activeEvidenceIndex } : undefined}
                        onCitationInsert={(source, evidenceIndex, quote, extras) => insertCitationFromAnswer(source, evidenceIndex, quote, extras)}
                        onCitationInsertPreview={(source, evidenceIndex, quote, extras) => previewCitationFromAnswer(source, evidenceIndex, quote, extras)}
                        onCitationInsertPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
                        onClaimRemove={(statement) => removeStatementFromBlock(block.id, statement)}
                        onCitationRemove={(paperId, statement) => removeCitationFromBlock(block.id, paperId, statement)}
                        onClaimEvidenceUpdate={(source, evidenceIndex, quotes, statement) =>
                          updateCitationEvidenceInBlock(block.id, source, evidenceIndex, quotes, statement)
                        }
                        markUncited={Number(block.answer.context_diagnostics?.uncited_sentence_count ?? 0) > 0}
                      />
                    </div>
                    {block.answer.generation_error ? <div className="warning-row">{block.answer.generation_error}</div> : null}
                    {block.answer.context_diagnostics?.fallback_reason === "no_traceable_citations" ? (
                      <div className="warning-row">Keine verknüpfbaren Zitate – beleg-basierte Zusammenfassung angezeigt.</div>
                    ) : null}
                    {Number(block.answer.context_diagnostics?.uncited_sentence_count ?? 0) > 0 ? (
                      <div className="hint-row">
                        {String(block.answer.context_diagnostics?.uncited_sentence_count)} Aussage(n) ohne Quellenangabe — im Text gestrichelt unterstrichen.
                      </div>
                    ) : null}
                  </article>
                ))}
              </div>
            ) : (
              !deepMode && !parallelMode ? <EmptyState title="Keine Antwort" /> : null
            )}
            {answerSelection ? (
              <div
                className="answer-selection-popover"
                style={{ left: answerSelection.left, top: answerSelection.top }}
                onMouseUp={(event) => event.stopPropagation()}
              >
                <div className="answer-selection-popover__head">
                  <strong>{answerSelection.text.length} Zeichen markiert</strong>
                  <button className="icon-button" type="button" aria-label="Schließen" onClick={closeAnswerSelection}>
                    <X size={14} />
                  </button>
                </div>
                {correctionDraft === null ? (
                  <div className="answer-selection-popover__actions">
                    <button className="button button-compact" type="button" title="Aussage gegen ihre Quellen prüfen" onClick={() => void checkAnswerSelection()}>
                      <ShieldCheck size={13} /> Nachchecken
                    </button>
                    <button className="button button-compact" type="button" title="Weiterfragen zu dieser Passage" onClick={askAboutSelection}>
                      <MessageSquareText size={13} /> Weiterfragen
                    </button>
                    <button
                      className="button button-compact"
                      type="button"
                      title="Passage samt Quellen in die Notiz übernehmen"
                      onMouseEnter={previewSelectionInNote}
                      onMouseLeave={() => notesActionsRef.current?.clearInsertPreview()}
                      onFocus={previewSelectionInNote}
                      onBlur={() => notesActionsRef.current?.clearInsertPreview()}
                      onClick={insertSelectionIntoNote}
                    >
                      <NotebookPen size={13} /> In Notiz
                    </button>
                    {selectionCheck?.status === "done" &&
                    (selectionCheck.results ?? []).some(
                      (check) => check.verdict === "not_supported" || check.verdict === "partially_supported"
                    ) ? (
                      <button
                        className="button button-compact"
                        type="button"
                        title="Der Nachcheck hat die Aussage nicht bestätigt — jetzt richtigstellen"
                        onClick={() => setCorrectionDraft(answerSelection.text)}
                      >
                        <Settings2 size={13} /> Korrigieren
                      </button>
                    ) : null}
                  </div>
                ) : (
                  <div className="answer-selection-popover__correction">
                    <textarea
                      value={correctionDraft}
                      rows={3}
                      onChange={(event) => setCorrectionDraft(event.target.value)}
                      aria-label="Korrigierter Text"
                    />
                    <div className="answer-selection-popover__actions">
                      <button className="button button-compact button-primary" type="button" onClick={applyAnswerCorrection}>
                        Ersetzen
                      </button>
                      <button className="button button-compact" type="button" onClick={() => setCorrectionDraft(null)}>
                        Abbrechen
                      </button>
                    </div>
                  </div>
                )}
                {selectionCheck?.status === "loading" ? (
                  <div className="answer-selection-popover__status">
                    <Loader2 size={13} className="spin" /> Prüfe gegen die Quellen …
                  </div>
                ) : null}
                {selectionCheck?.status === "error" ? (
                  <div className="answer-selection-popover__status">{selectionCheck.error}</div>
                ) : null}
                {selectionCheck?.status === "done"
                  ? (selectionCheck.results ?? []).map((check) => (
                      <div className="answer-selection-popover__result" key={check.paper_id}>
                        <span
                          className={`claim-check-card__verdict ${
                            check.verdict === "supported"
                              ? "claim-check--ok"
                              : check.verdict === "partially_supported"
                                ? "claim-check--warn"
                                : check.verdict === "not_supported"
                                  ? "claim-check--bad"
                                  : "claim-check--unknown"
                          }`}
                        >
                          [{check.paper_id}] {claimVerdictLabel(check.verdict)}
                          {check.checked_scope === "whole_paper" ? " (ganzes Paper geprüft)" : ""}
                        </span>
                        {check.explanation ? <p>{check.explanation}</p> : null}
                        {check.supporting_quotes.slice(0, 1).map((quoteText, quoteIndex) => (
                          <blockquote className="claim-check-card__quote" key={quoteIndex}>
                            {quoteText}
                          </blockquote>
                        ))}
                        {check.verdict === "not_supported" && selectionBlock ? (
                          <button
                            className="button button-compact claim-check-card__danger"
                            type="button"
                            title="Dieses Zitat entfernen — der Satz bleibt, wenn ihn eine andere Quelle belegt, sonst wird auch er entfernt"
                            onClick={() => {
                              removeCitationFromBlock(selectionBlock.id, check.paper_id, answerSelection.text);
                              closeAnswerSelection();
                            }}
                          >
                            <XCircle size={13} /> Zitat entfernen
                          </button>
                        ) : null}
                      </div>
                    ))
                  : null}
                {selectionCheck?.status === "done" &&
                (selectionCheck.results ?? []).some((check) => check.verdict === "not_supported" || check.verdict === "partially_supported") &&
                correctionDraft === null ? (
                  <div className="answer-selection-popover__status">
                    Aussage nicht (voll) gedeckt — mit „Korrigieren" kannst du sie direkt richtigstellen.
                  </div>
                ) : null}
              </div>
            ) : null}
          </section>
          {useInternet && (webMutation.isPending || webResult || webMutation.isError) ? (
            <section className="panel web-research-panel">
              <div className="panel-heading">
                <div>
                  <span>Aus dem Internet</span>
                  <strong>{webMutation.isPending ? "Recherchiere…" : `${webResult?.findings.length ?? 0} Grauquellen`}</strong>
                </div>
                <Globe size={16} />
              </div>
              <p className="muted">
                Web-Treffer als untrusted Grauquellen — getrennt von der lokalen, quellenbasierten Antwort und nicht im Knowledge Graph.
              </p>
              {webMutation.isError ? <div className="warning-row">Internet-Recherche fehlgeschlagen.</div> : null}
              {webResult?.warnings?.length ? <div className="warning-row">{webResult.warnings.join(" · ")}</div> : null}
              {webResult?.related_topics?.length ? (
                <div className="web-research-topics">
                  <span className="muted">Verwandte Themen:</span>
                  {webResult.related_topics.slice(0, 8).map((topic) => (
                    <span className="topic-chip" key={topic}>
                      {topic}
                    </span>
                  ))}
                </div>
              ) : null}
              <div className="stack web-research-findings">
                {(webResult?.findings ?? []).map((finding, index) => (
                  <article className="web-research-finding" key={`${finding.url}-${index}`}>
                    <div className="web-research-finding-head">
                      <span className="grey-badge grey-badge--mini">Grauquelle</span>
                      <strong title={finding.title || finding.url}>{finding.title || finding.url}</strong>
                    </div>
                    {finding.summary ? <p>{finding.summary}</p> : null}
                    {finding.injection_flags?.length ? (
                      <div className="warning-row">⚠ Prompt-Injection-Flags ignoriert: {finding.injection_flags.join(", ")}</div>
                    ) : null}
                    <div className="web-research-finding-actions">
                      <button className="button button-compact button-ghost" type="button" onClick={() => openGreySource(findingToGreySource(finding))}>
                        <FileText size={14} />
                        <span>Im Viewer öffnen</span>
                      </button>
                      <a className="button button-compact button-ghost" href={finding.url} target="_blank" rel="noreferrer">
                        <Globe size={14} />
                        <span>Website</span>
                      </a>
                      <button
                        className="button button-compact"
                        type="button"
                        disabled={!isRealProject || saveGreyMutation.isPending}
                        onClick={() => saveGreyMutation.mutate(finding)}
                      >
                        <Star size={14} />
                        <span>Als Grauquelle speichern</span>
                      </button>
                    </div>
                  </article>
                ))}
                {webResult && !webResult.findings.length && !webMutation.isPending ? <EmptyState title="Keine Web-Treffer" /> : null}
              </div>
            </section>
          ) : null}
          <div className="workspace-assistant-actions">
            <button
              className="button button-compact button-ghost"
              type="button"
              title="Ganze Antwort als Zitat in die aktive Notiz einfügen"
              onClick={appendAnswerToNote}
              onMouseEnter={previewAnswerToNote}
              onMouseLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onPointerEnter={previewAnswerToNote}
              onPointerLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onFocus={previewAnswerToNote}
              onBlur={() => notesActionsRef.current?.clearInsertPreview()}
              disabled={!answer}
            >
              <NotebookPen size={14} />
              <span>Antwort in Notiz</span>
            </button>
            <button
              className="button button-compact button-ghost"
              type="button"
              title={`Aktives Zitat Z${activeEvidenceIndex + 1} in die aktive Notiz einfügen`}
              onClick={() => appendActiveQuote("reference")}
              onMouseEnter={() => previewActiveQuote("reference")}
              onMouseLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onPointerEnter={() => previewActiveQuote("reference")}
              onPointerLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onFocus={() => previewActiveQuote("reference")}
              onBlur={() => notesActionsRef.current?.clearInsertPreview()}
              disabled={!activeEvidence || !selectedSource}
            >
              <Quote size={14} />
              <span>Zitat Z{activeEvidenceIndex + 1}</span>
            </button>
            <button
              className="button button-compact button-ghost"
              type="button"
              title={`PDF-Ausschnitt zu Z${activeEvidenceIndex + 1} in die aktive Notiz einfügen`}
              onClick={() => appendActiveQuote("pdf")}
              onMouseEnter={() => previewActiveQuote("pdf")}
              onMouseLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onPointerEnter={() => previewActiveQuote("pdf")}
              onPointerLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onFocus={() => previewActiveQuote("pdf")}
              onBlur={() => notesActionsRef.current?.clearInsertPreview()}
              disabled={!activeEvidence?.pdf_excerpt || !selectedSource}
            >
              <FilePlus2 size={14} />
              <span>PDF Z{activeEvidenceIndex + 1}</span>
            </button>
            <button
              className="button button-compact button-ghost"
              type="button"
              title="PDF der ausgewählten Quelle öffnen"
              onClick={openSelectedAssistantPdf}
              disabled={!selectedSource}
            >
              <FileText size={14} />
              <span>PDF öffnen</span>
            </button>
            {noteStatus ? <span className="notes-status">{noteStatus}</span> : null}
          </div>
          <section className={`panel evidence-dock ${evidenceOpen ? "" : "evidence-dock--collapsed"}`}>
            <div className="evidence-dock-heading">
              <div>
                <span>Belege</span>
                <strong>
                  {verification.length} Quellen / {selectedSource?.evidence.length ?? 0} Zitate
                </strong>
              </div>
              <button className="icon-button" type="button" aria-label="Belege ein- oder ausklappen" onClick={() => setEvidenceOpen((current) => !current)}>
                {evidenceOpen ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
              </button>
            </div>
            {!evidenceOpen ? (
              <button className="evidence-summary" type="button" onClick={() => setEvidenceOpen(true)}>
                <ListChecks size={16} />
                <strong>{selectedSource?.title || selectedSource?.paper_id || "Keine Quelle"}</strong>
                <span>{activeEvidence ? `Z${activeEvidenceIndex + 1} - ${shortEvidenceText(activeEvidence.reference_text)}` : "Keine Evidence"}</span>
              </button>
            ) : (
              <>
                {selectedSource && activeEvidence ? (
                  <div className="workspace-active-source evidence-dock-active-quote" style={colorVarsForPaperId(selectedSource.paper_id, activeEvidenceIndex)}>
                    <span>Aktives Zitat</span>
                    <strong>
                      Z{activeEvidenceIndex + 1} · {selectedSource.title || selectedSource.paper_id}
                    </strong>
                    <p>{activeEvidence.pdf_excerpt || activeEvidence.reference_text}</p>
                    <EvidenceVerificationBadge source={selectedSource} evidence={activeEvidence} />
                    {selectedSource.pdf_available ? (
                      <button className="button button-compact" type="button" onClick={openSelectedAssistantPdf}>
                        <FileText size={15} />
                        <span>PDF öffnen</span>
                      </button>
                    ) : null}
                  </div>
                ) : null}
                <div className="two-column evidence-columns">
                <section className="evidence-panel">
                  <div className="panel-heading">
                    <div>
                      <span>Quellen</span>
                      <strong>{verification.length}</strong>
                    </div>
                  </div>
                  <div className="list">
                    {verification.map((source) => (
                      <button
                        className={`list-row ${selectedSource?.paper_id === source.paper_id ? "list-row--active" : ""}`}
                        key={source.paper_id}
                        onClick={() => openAssistantSource(source)}
                      >
                        <strong>{source.title || source.paper_id}</strong>
                        <span>{source.paper_id}</span>
                        <Status value={source.pdf_available ? "true" : "false"} />
                      </button>
                    ))}
                  </div>
                </section>
                <section className="evidence-panel">
                  <div className="panel-heading">
                    <div>
                      <span>Evidence</span>
                      <strong>{selectedSource?.evidence.length ?? 0}</strong>
                    </div>
                  </div>
                  <div className="list">
                    {(selectedSource?.evidence ?? []).map((item, index) => (
                      <button
                        className={`list-row evidence-row ${activeEvidenceIndex === index ? "list-row--active" : ""}`}
                        key={`${item.reference_text}-${index}`}
                        onClick={() => openAssistantSource(selectedSource, index)}
                        style={colorVarsForPaperId(selectedSource?.paper_id, index)}
                      >
                        <strong className="evidence-row-title">
                          <span className="evidence-swatch" aria-hidden="true" />
                          Zitat {index + 1} - {item.kind}
                        </strong>
                        <span>{item.reference_text}</span>
                      </button>
                    ))}
                  </div>
                </section>
                </div>
                {actionHistory.length ? (
                  <details className="evidence-action-log">
                    <summary>Log · {actionHistory.length} Aktionen</summary>
                    <div className="evidence-action-log__list">
                      {[...actionHistory].reverse().map((entry) => (
                        <div className={`evidence-action-log__row evidence-action-log__row--${entry.status}`} key={entry.id}>
                          <strong>{entry.title}</strong>
                          <span>{entry.detail}</span>
                        </div>
                      ))}
                    </div>
                  </details>
                ) : null}
              </>
            )}
          </section>
            </>
  );
}
