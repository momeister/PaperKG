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
  AutoGreySource,
  AutoHarvestStage,
  CitationLink,
  ClaimCheckResult,
  DeepResearchFinding,
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
  bulletQuote,
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
import { ClarifyDialog } from "./ClarifyDialog";
import { WorkspaceAssistantPane } from "./WorkspaceAssistantPane";
import { NotesSurface } from "./NotesPage";
import type { NotesSurfaceActions, NotesSurfaceSnapshot } from "./NotesPage";
import { AnalysisPanel } from "./AnalysisPanel";
import { DatasetsPanel } from "./DatasetsPanel";

// Pure helpers/constants live in ./workspaceHelpers; re-export for back-compat and
// import the ones this file uses directly.
export * from "./workspaceHelpers";
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

export type WorkspaceNavigatorTab = "notes" | "pdfs" | "assistantSessions";

export type WorkspacePdfTarget =
  | { kind: "assistant"; source: VerificationSource; evidenceIndex: number }
  | { kind: "noteCitation"; citation: NoteCitation }
  | { kind: "paper"; paper: Paper }
  | { kind: "grey"; source: GreySource }
  | { kind: "missing"; paperId: string; title?: string };

export type PaperQuestionScope = "current" | "selected" | "all";
export type WorkspaceAssistantMode = "pdf" | "notes";

export type AssistantAnswerBlock = ReturnType<typeof turnBlocks>[number];
export type AssistantTurn = Parameters<typeof turnBlocks>[0];

export type WorkspaceCommandDef = {
  name: string;
  aliases?: string[];
  args?: string;
  description: string;
  group: "frage" | "aktion";
};

/** Claude-Code-Stil: alles, was sonst in anderen Tabs passiert, ist hier als Slash-Befehl erreichbar. */
export type WorkspaceActionEntry = {
  id: string;
  title: string;
  detail: string;
  status: "ok" | "error" | "pending";
  createdAt: string;
};

export function WorkspacePage() {
  const { activeProject, setActiveProject, provider, model, llmParams } = useAppState();
  const scopedProjectId = noteProjectId(activeProject);
  const scopeLabel = projectScopeLabel(activeProject);
  const queryClient = useQueryClient();
  const assistantScopeRef = useRef(scopedProjectId);
  // Erst speichern, wenn die Server-Session gelesen wurde. Ohne dieses Gate schrieb
  // ein Start vor erreichbarem Backend die (leere) localStorage-History zurueck und
  // loeschte die Unterhaltung serverseitig.
  const sessionHydratedRef = useRef(false);
  const [sessionLoadFailed, setSessionLoadFailed] = useState(false);
  // Nach einem Restore aus einer Sicherung die Hydrierung erneut anstoßen.
  const [sessionReloadNonce, setSessionReloadNonce] = useState(0);
  const notesActionsRef = useRef<NotesSurfaceActions | null>(null);
  const pageRef = useRef<HTMLElement | null>(null);
  const pdfCitationResizeFrameRef = useRef<number | null>(null);
  const greySourceResizeFrameRef = useRef<number | null>(null);

  const [navigatorTab, setNavigatorTab] = useState<WorkspaceNavigatorTab>("notes");
  const [notesSnapshot, setNotesSnapshot] = useState<NotesSurfaceSnapshot>(EMPTY_NOTES_SNAPSHOT);
  const [controlledNoteId, setControlledNoteId] = useState("");
  const [requestedCitationId, setRequestedCitationId] = useState("");
  const [navigatorQuery, setNavigatorQuery] = useState("");
  const [noteStatus, setNoteStatus] = useState("");

  const [question, setQuestion] = useState("");
  const questionInputRef = useRef<HTMLInputElement | null>(null);
  const [mentionState, setMentionState] = useState<{ query: string; start: number; end: number } | null>(null);
  const [mentionHighlight, setMentionHighlight] = useState(0);
  const [showCommandHelp, setShowCommandHelp] = useState(false);
  const [history, setHistory] = useState<AssistantTurn[]>(() => loadAssistantSession(scopedProjectId).history);
  // Läuft der Vorab-Nachcheck unsicherer Zuordnungen (zwischen Generierung und Anzeige)?
  const [citationVerifyPending, setCitationVerifyPending] = useState(false);
  const [activeTurnId, setActiveTurnId] = useState(() => loadAssistantSession(scopedProjectId).activeTurnId);
  const [selectedSource, setSelectedSource] = useState<VerificationSource | null>(null);
  const [activeEvidenceIndex, setActiveEvidenceIndex] = useState(0);
  // Cited sources whose PDF we downloaded on demand (so pdfProps resolves a real PDF URL even
  // though the original answer source reported pdf_available:false), and the paper currently
  // being downloaded so the pane shows a loading state instead of the "no PDF" abstract limbo.
  const [ingestedPdfIds, setIngestedPdfIds] = useState<Set<string>>(new Set());
  const [ingestingPaperId, setIngestingPaperId] = useState<string | null>(null);
  const [selectedAnswerQuote, setSelectedAnswerQuote] = useState<{ paperId: string; evidenceIndex: number; text: string } | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(true);
  const [evidenceMode, setEvidenceMode] = useState("auto");
  const [verbosity, setVerbosity] = useState<"kurz" | "standard" | "ausführlich">("standard");
  const [conversationMode, setConversationMode] = useState<"followup" | "new">("followup");
  // One-shot flag from "/new" without question: the next question starts a fresh
  // session, but the Weiterfragen mode itself stays untouched.
  const [nextTurnIsNew, setNextTurnIsNew] = useState(false);
  const [paperScope, setPaperScope] = useState<PaperQuestionScope>("all");
  const [includeGlobalSources, setIncludeGlobalSources] = useState(false);
  const [assistantMode, setAssistantMode] = useState<WorkspaceAssistantMode>("pdf");
  const [focusedNoteThreadId, setFocusedNoteThreadId] = useState("");
  const [selectedPaperIds, setSelectedPaperIds] = useState<string[]>([]);
  const [selectedGreyIds, setSelectedGreyIds] = useState<string[]>([]);
  const [useInternet, setUseInternet] = useState(() => loadWorkspaceBoolean(scopedProjectId, "useInternet", false));
  // Auto-Recherche: ist sie an und die lokale Antwort schwach, harvestet das System
  // automatisch Paper (inkl. Phase-3-Extraktion) + Webquellen — auch zu KI-abgeleiteten
  // verwandten Themen — und beantwortet die Frage neu.
  const [autoResearch, setAutoResearch] = useState(() => loadWorkspaceBoolean(scopedProjectId, "autoResearch", false));
  // Kritischer Modus (/kritisch): Antworten benennen Limitationen, Risiken und
  // Gegenbelege explizit (answer_style: "kritisch" im Backend).
  const [criticalMode, setCriticalMode] = useState(() => loadWorkspaceBoolean(scopedProjectId, "criticalMode", false));
  const [autoProgress, setAutoProgress] = useState<{
    phase: string;
    /** Aktive Stufe der Eskalation (wissenschaftlich → vertrauenswürdig → ungeprüft). */
    stage?: AutoHarvestStage;
    relatedTopics: string[];
    papers: { id: string; title: string }[];
    grey: AutoGreySource[];
  } | null>(null);
  const autoAbortRef = useRef<AbortController | null>(null);
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const [actionsMenuOpen, setActionsMenuOpen] = useState(false);
  const [actionLog, setActionLog] = useState<WorkspaceActionEntry[]>([]);
  // Full, non-dismissing history for the ausklappbares Log unter Quellen/Zitate.
  const [actionHistory, setActionHistory] = useState<WorkspaceActionEntry[]>([]);
  const actionDismissTimers = useRef<Record<string, ReturnType<typeof setTimeout>>>({});
  const [commandSearch, setCommandSearch] = useState<{ query: string; results: Paper[]; selected: string[] } | null>(null);
  const [webOfferDismissedFor, setWebOfferDismissedFor] = useState("");

  // Research Tree (Tiefenanalyse) — if the restored active turn is a deep-analysis session,
  // reopen directly in deep mode so its (answer-less) nodes never flow through the normal
  // answer-block renderer, which would dereference a null answer and white-screen the page.
  const [deepMode, setDeepMode] = useState(
    () => restoredActiveTurnFor(scopedProjectId)?.type === "research_tree",
  );
  const [deepDepth, setDeepDepth] = useState(3);
  const [deepBranches, setDeepBranches] = useState(4);
  const [researchNodes, setResearchNodes] = useState<ResearchNode[]>(() => {
    const turn = restoredActiveTurnFor(scopedProjectId);
    return turn?.type === "research_tree" ? (turn.researchNodes ?? []) : [];
  });
  const [researchLoading, setResearchLoading] = useState(false);
  const [researchLlmError, setResearchLlmError] = useState<{ kind: string; message: string; error: string } | null>(null);
  const [autoHarvest, setAutoHarvest] = useState(() => {
    try { return window.localStorage.getItem(`workspace.autoHarvest.${scopedProjectId}`) === "true"; } catch { return false; }
  });
  const [showHarvestDialog, setShowHarvestDialog] = useState(false);
  const [showClarifyDialog, setShowClarifyDialog] = useState(false);
  const [clarifyDirections, setClarifyDirections] = useState<string[]>([]);
  const [clarifySelected, setClarifySelected] = useState<number[]>([]);
  const [clarifyFreetext, setClarifyFreetext] = useState("");
  const [clarifyLoading, setClarifyLoading] = useState(false);
  // Pending deep-analysis question survives the async clarify roundtrip in a ref so
  // overlapping state setters in the dialog handlers can never reset it (see bug history).
  const pendingDeepRef = useRef<{ question: string; harvest: boolean } | null>(null);
  const researchAbortRef = useRef<AbortController | null>(null);
  const researchSessionIdRef = useRef<string>("");
  const researchNodesRef = useRef<ResearchNode[]>([]);
  const autoSavedTreeRef = useRef<string | null>(null);

  // Parallel Research — a third assistant mode (next to chat and Tiefenanalyse). The
  // session is server-persisted; the active turn (type "parallel") points at it by id.
  const [parallelMode, setParallelMode] = useState(
    () => restoredActiveTurnFor(scopedProjectId)?.type === "parallel",
  );
  const [parallelSession, setParallelSession] = useState<ParallelSession | null>(null);
  const [parallelLoading, setParallelLoading] = useState(false);
  // A follow-up question (weiterfragen) is in flight against the open session.
  const [parallelFollowupLoading, setParallelFollowupLoading] = useState(false);
  // One-shot "/new" in parallel mode: the next question starts a fresh parallel session
  // instead of continuing the current one.
  const [parallelNewArmed, setParallelNewArmed] = useState(false);
  const parallelSessionIdRef = useRef<string>("");

  // On mount, restore the research-tree refs for a reopened deep-analysis session so
  // "Fortsetzen" continues the same session instead of forking a new one.
  useEffect(() => {
    const turn = restoredActiveTurnFor(scopedProjectId);
    if (turn?.type === "research_tree") {
      researchSessionIdRef.current = turn.id;
      researchNodesRef.current = turn.researchNodes ?? [];
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const [navigatorOpen, setNavigatorOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "navigatorOpen", true));
  const [assistantOpen, setAssistantOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "assistantOpen", true));
  const [pdfOpen, setPdfOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "pdfOpen", true));
  // Center column can swap between the PDF viewer, the Analyse-Werkstatt and Datensätze.
  const [centerView, setCenterView] = useState<"pdf" | "analysis" | "datasets">("pdf");
  const [notesOpen, setNotesOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "notesOpen", true));
  // Notes pane sub-view: the normal note editor, or the Parallel-Research "Ergebnisse" view.
  const [notesTab, setNotesTab] = useState<"note" | "results">("note");
  const [navigatorWidth, setNavigatorWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "navigatorWidth.v3", 180));
  const [assistantWidth, setAssistantWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "assistantWidth.v3", 420));
  const [pdfWidth, setPdfWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "pdfWidth.v3", 220));
  // Live-Breite des Grid-Containers: die gespeicherten Pane-Breiten sind Wunschbreiten;
  // wird das Fenster schmaler als ihre Summe, schrumpfen die effektiven Breiten mit,
  // damit keine Pane (v.a. die Notizen) unerreichbar abgeschnitten wird.
  const [containerWidth, setContainerWidth] = useState(0);
  const [pdfCitationListHeight, setPdfCitationListHeight] = useState(() => loadWorkspaceNumber(scopedProjectId, "pdfCitationListHeight", 220));
  const [greySourceListHeight, setGreySourceListHeight] = useState(() => loadWorkspaceNumber(scopedProjectId, "greySourceListHeight", 180));
  const [pdfTarget, setPdfTarget] = useState<WorkspacePdfTarget | null>(null);

  const activeTurn = useMemo(() => {
    if (!history.length) {
      return null;
    }
    return history.find((turn) => turn.id === activeTurnId) ?? history[history.length - 1];
  }, [activeTurnId, history]);
  // Deep-analysis turns carry no answer block (their content lives in researchNodes), so
  // never build a phantom block for them — that block's null answer is what white-screened
  // the page once a Tiefenanalyse session was the restored active turn.
  const activeBlocks = useMemo(
    () => (activeTurn && activeTurn.type !== "research_tree" ? turnBlocks(activeTurn) : []),
    [activeTurn],
  );
  const latestBlock = activeBlocks[activeBlocks.length - 1] ?? null;
  const answer = latestBlock?.answer ?? null;
  const verification = useMemo(() => mergeVerification(activeBlocks.flatMap((block) => block.verification)), [activeBlocks]);
  const activeEvidence = selectedSource?.evidence[activeEvidenceIndex];
  const activeAnswerQuote =
    selectedAnswerQuote && selectedAnswerQuote.paperId === selectedSource?.paper_id && selectedAnswerQuote.evidenceIndex === activeEvidenceIndex
      ? selectedAnswerQuote.text
      : "";

  const isRealProject = !!activeProject && !ALL_PAPERS_SCOPES.has(activeProject);
  const papersQuery = useQuery({
    queryKey: ["workspace-pdfs", activeProject],
    queryFn: () => api.listPapers({ project_id: activeProject, has_full_text: true, limit: 200 })
  });
  const greyQuery = useQuery({
    queryKey: ["grey-sources", activeProject],
    queryFn: () => api.listGreySources(activeProject as string),
    enabled: isRealProject
  });
  // All papers of the project (not only those with a local PDF) — used to scope
  // "Alle Quellen" questions to the project instead of the global KG.
  const projectPapersQuery = useQuery({
    queryKey: ["workspace-project-paper-ids", activeProject],
    queryFn: () => api.listPapers({ project_id: activeProject, limit: 1000 }),
    enabled: isRealProject
  });
  const projectPaperIds = useMemo(
    () => (projectPapersQuery.data?.items ?? []).map((paper) => workspacePaperId(paper)).filter(Boolean),
    [projectPapersQuery.data]
  );
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
  const primaryPaperId = projectsQuery.data?.projects.find((entry) => entry.id === activeProject)?.primary_paper_id ?? null;
  const greySources = greyQuery.data?.grey_sources ?? [];

  /**
   * Scope derivation lives in a function (not render-time consts) so a slash command
   * such as "/summary fokus" can switch the scope and ask in the same Enter — the
   * request is built from the command's scope, not the stale render state.
   */
  function deriveScope(scope: PaperQuestionScope) {
    const currentId = activeScopePaperId(pdfTarget, selectedSource);
    const currentIsGrey = scope === "current" && pdfTarget?.kind === "grey";
    const projectOnly = scope === "all" && isRealProject && !includeGlobalSources;
    const papers =
      scope === "all"
        ? projectOnly
          ? projectPaperIds
          : []
        : scope === "selected"
          ? selectedPaperIds
          : currentId
            ? [currentId]
            : [];
    // Selected grey sources are sent by ID so the backend injects them as real grey::
    // sources (grey chips, Belegstellen, GreySourceView) instead of an anonymous
    // "Inline-Kontext" blob without text locations.
    const greyIds =
      currentIsGrey && pdfTarget?.kind === "grey"
        ? [pdfTarget.source.id]
        : scope === "selected" && selectedGreyIds.length
          ? selectedGreyIds
          : [];
    return {
      currentScopePaperId: currentId,
      currentScopeIsGrey: currentIsGrey,
      projectOnlyScope: projectOnly,
      scopedPaperIds: papers,
      greySourceIds: greyIds,
      blocked:
        (scope === "selected" && !selectedPaperIds.length && !selectedGreyIds.length) ||
        (scope === "current" && !currentId && !currentIsGrey),
      answerContextMode: (scope === "current" && currentId ? "pdf_if_fits" : "kg") as "kg" | "pdf_if_fits"
    };
  }

  const scopeInfo = deriveScope(paperScope);
  const { currentScopePaperId, currentScopeIsGrey } = scopeInfo;
  const questionBlockedByScope = scopeInfo.blocked;

  type AskVariables = { value: string; scope: PaperQuestionScope; newTurn: boolean; extraGreyIds?: string[]; critical?: boolean };

  const answerMutation = useMutation({
    mutationFn: (vars: AskVariables) => {
      const info = deriveScope(vars.scope);
      const greyIds = Array.from(new Set([...info.greySourceIds, ...(vars.extraGreyIds ?? [])]));
      return api.answer({
        question: vars.value,
        provider,
        model,
        limit: answerLimitFor(vars.value, evidenceMode, vars.scope === "all" ? 0 : Math.max(1, info.scopedPaperIds.length + (greyIds.length ? 1 : 0))),
        // "__none__" keeps the scope honest: without scoped papers the backend must not
        // fall back to the global KG — also when only grey sources are in scope.
        paper_ids: info.scopedPaperIds.length ? info.scopedPaperIds : info.projectOnlyScope || greyIds.length ? ["__none__"] : undefined,
        priority_paper_ids: primaryPaperId ? [primaryPaperId] : undefined,
        answer_context_mode: info.answerContextMode !== "kg" ? info.answerContextMode : undefined,
        grey_source_ids: greyIds.length ? greyIds : undefined,
        include_project_grey: vars.scope === "all" && isRealProject ? true : undefined,
        conversation_context: conversationMode === "followup" && !vars.newTurn && activeTurn && activeTurn.type !== "research_tree" ? turnContext(activeTurn) : undefined,
        project_id: activeProject || undefined,
        llm_overrides: Object.values(llmParams).some((value) => value !== undefined) ? llmParams : undefined,
        answer_style: vars.critical || criticalMode ? "kritisch" : undefined
      });
    },
    onSuccess: async (payload, vars) => {
      await commitAnswerTurn(payload, vars.newTurn);
    }
  });

  /** Commit a grounded answer as a new turn (or append it to the active turn in
   * Weiterfragen mode). Shared by the normal answer mutation and the streaming
   * auto-research flow so both produce identical turn/block structures. */
  async function commitAnswerTurn(payload: Answer, newTurn: boolean) {
    let sources: VerificationSource[] = [];
    try {
      sources = await verificationSourcesFor(payload);
    } catch {
      sources = [];
    }
    // Unsichere Zuordnungen nachprüfen + korrigieren, BEVOR die Antwort angezeigt wird.
    // Unsicher = vom Backend geflaggt (approximate) ODER die Belegstelle wurde im PDF
    // nicht/nur ungefähr verortet — auch lexikalisch plausible Fehlzuordnungen laufen so
    // durch den Nachcheck statt unmarkiert durchzurutschen.
    const anyUncertain =
      (payload.citation_links ?? []).some((link) => link.approximate) ||
      sources.some((source) => source.evidence.some((evidence) => evidenceLocationUncertain(source, evidence)));
    if (anyUncertain) {
      setCitationVerifyPending(true);
      try {
        const verified = await verifyUncertainCitations(payload, sources);
        payload = verified.payload;
        sources = verified.sources;
      } finally {
        setCitationVerifyPending(false);
      }
    }
    const block: AssistantAnswerBlock = {
      id: `block_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      question: payload.question,
      answer: payload,
      verification: sources,
      createdAt: new Date().toISOString()
    };
    if (conversationMode === "followup" && !newTurn && activeTurn) {
      const turnId = activeTurn.id;
      setHistory((current) =>
        current.map((turn) => {
          if (turn.id !== turnId) {
            return turn;
          }
          const blocks = [...turnBlocks(turn), block];
          return {
            ...turn,
            answer: payload,
            verification: mergeVerification(blocks.flatMap((item) => item.verification)),
            blocks
          };
        })
      );
      setActiveTurnId(turnId);
      openAssistantSource(sources[0] ?? verification[0] ?? null, 0);
      return;
    }
    const turn: AssistantTurn = {
      id: `turn_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      question: payload.question,
      answer: payload,
      verification: sources,
      createdAt: block.createdAt,
      blocks: [block]
    };
    setHistory((current) => [...current.slice(-24), turn]);
    setActiveTurnId(turn.id);
    openAssistantSource(sources[0] ?? null, 0);
  }

  /** Stream POST /query/auto-answer: show the local answer, auto-harvest papers + web
   * sources (plus KI-derived related topics) when it is weak, then commit the re-answer. */
  async function runAutoResearch(value: string, opts: { scope: PaperQuestionScope; newTurn: boolean; force?: boolean }) {
    const info = deriveScope(opts.scope);
    if (info.blocked) {
      logAction("Auto-Recherche", opts.scope === "selected" ? "Keine Quellen ausgewählt." : "Kein aktives Paper geöffnet.", "error");
      return;
    }
    autoAbortRef.current?.abort();
    const controller = new AbortController();
    autoAbortRef.current = controller;
    const greyIds = info.greySourceIds;
    setAutoProgress({ phase: "Lokale Quellen werden geprüft…", relatedTopics: [], papers: [], grey: [] });
    const actionId = logAction("Auto-Recherche", `Recherchiere zu „${value}" …`, "pending");
    try {
      await streamAutoAnswer(
        {
          question: value + verbosityInstruction(verbosity),
          search_question: value,
          project_id: activeProject || undefined,
          provider,
          model,
          limit: answerLimitFor(value, evidenceMode, opts.scope === "all" ? 0 : Math.max(1, info.scopedPaperIds.length + (greyIds.length ? 1 : 0))),
          paper_ids: info.scopedPaperIds.length ? info.scopedPaperIds : info.projectOnlyScope || greyIds.length ? ["__none__"] : undefined,
          priority_paper_ids: primaryPaperId ? [primaryPaperId] : undefined,
          answer_context_mode: info.answerContextMode !== "kg" ? info.answerContextMode : undefined,
          grey_source_ids: greyIds.length ? greyIds : undefined,
          include_project_grey: opts.scope === "all" && isRealProject ? true : undefined,
          conversation_context:
            conversationMode === "followup" && !opts.newTurn && activeTurn && activeTurn.type !== "research_tree" ? turnContext(activeTurn) : undefined,
          llm_overrides: Object.values(llmParams).some((v) => v !== undefined) ? llmParams : undefined,
          force: opts.force || undefined
        },
        (event) => {
          switch (event.status) {
            case "answer":
              setAutoProgress((p) => (p ? { ...p, phase: "Lokale Antwort geprüft …" } : p));
              break;
            case "planning":
              setAutoProgress((p) => ({
                phase: "Verwandte Themen werden abgeleitet …",
                relatedTopics: event.related_topics ?? [],
                papers: p?.papers ?? [],
                grey: p?.grey ?? []
              }));
              break;
            case "harvesting": {
              const stageLabel = event.stage_label ?? "Quellen";
              setAutoProgress((p) => ({
                phase:
                  event.scope === "main"
                    ? `${stageLabel} zur Frage werden geladen …`
                    : `${stageLabel}: verwandtes Thema „${event.topic}" …`,
                stage: event.stage ?? p?.stage,
                relatedTopics: p?.relatedTopics ?? [],
                papers: [...(p?.papers ?? []), ...(event.papers ?? [])],
                grey: [...(p?.grey ?? []), ...(event.grey ?? [])]
              }));
              break;
            }
            case "reanswering":
              setAutoProgress((p) => (p ? { ...p, phase: "Antwort wird mit den neuen Quellen erstellt …", stage: event.stage ?? p.stage } : p));
              break;
            case "harvest_error":
              logAction("Auto-Recherche", event.error ?? "Ein Rechercheschritt ist fehlgeschlagen.", "error");
              break;
            case "error":
              logAction("Auto-Recherche", event.error ?? "Recherche fehlgeschlagen.", "error");
              break;
            case "done": {
              const summary = event.harvest_summary;
              if (event.answer) {
                void commitAnswerTurn(event.answer, opts.newTurn);
              }
              // Toast darf der Antwort nicht widersprechen: meldet die finale Antwort selbst
              // noch eine Evidenz-Lücke, nie "reichte aus" behaupten.
              const gapRemains = answerSuggestsWebSearch(event.answer);
              const addedCount = (summary?.papers.length ?? 0) + (summary?.grey.length ?? 0);
              // Welche Stufe die Antwort getragen hat, ist die wichtigste Information:
              // "reichte wissenschaftlich" vs. "nur ungeprüftes Web".
              const lastStage = summary?.stages?.[summary.stages.length - 1];
              const stageNote = lastStage ? ` Zuletzt: ${lastStage.label}.` : "";
              updateAction(actionId, {
                status: "ok",
                detail: summary?.harvested
                  ? addedCount
                    ? `${summary.papers.length} Paper + ${summary.grey.length} Web-Quellen ergänzt.${stageNote}${gapRemains ? " Restlücke bleibt." : ""}`
                    : "Keine zusätzlichen Quellen gefunden."
                  : gapRemains
                    ? "Lokale Antwort meldet Evidenz-Lücke."
                    : "Lokale Quellen reichten aus."
              });
              if (summary?.harvested) {
                queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] });
                queryClient.invalidateQueries({ queryKey: ["workspace-pdfs", activeProject] });
                queryClient.invalidateQueries({ queryKey: ["projects"] });
              }
              break;
            }
          }
        },
        controller.signal
      );
    } catch (error) {
      updateAction(actionId, { status: "error", detail: error instanceof Error ? error.message : String(error) });
    } finally {
      setAutoProgress(null);
      if (autoAbortRef.current === controller) {
        autoAbortRef.current = null;
      }
    }
  }

  const webMutation = useMutation({
    mutationFn: (value: string) => api.deepResearch({ question: value, provider }),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] })
  });
  const saveGreyMutation = useMutation({
    mutationFn: (finding: DeepResearchFinding) =>
      api.addGreySources(activeProject as string, [findingToGreyRecord(finding)], webMutation.data?.question),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] })
  });
  const webResult = webMutation.data ?? null;

  const [isDraggingOverChat, setIsDraggingOverChat] = useState(false);
  const [pendingUrlSource, setPendingUrlSource] = useState<{ url: string } | null>(null);
  const [sourceIngestStatus, setSourceIngestStatus] = useState("");

  const uploadDroppedPdf = useMutation({
    mutationFn: (file: File) => api.uploadPdf(file, isRealProject ? { project_id: activeProject } : {}),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["workspace-pdfs", activeProject] });
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      setSourceIngestStatus(`PDF "${result.paper.title || result.paper.id}" hinzugefügt - in den Quellen sichtbar.`);
    },
    onError: (error) => setSourceIngestStatus(error instanceof Error ? error.message : "PDF-Upload fehlgeschlagen")
  });

  const extractDroppedSource = useMutation({
    mutationFn: (paperId: string) => api.runExtraction({ paper_id: paperId }),
    onSuccess: (_, paperId) => {
      queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
      setSourceIngestStatus(`Extraktion für "${paperId}" gestartet.`);
    },
    onError: (error) => setSourceIngestStatus(error instanceof Error ? error.message : "Extraktion fehlgeschlagen")
  });

  const addUrlSource = useMutation({
    mutationFn: (url: string) => api.addGreySourceFromUrl(activeProject as string, url),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] });
      setSourceIngestStatus(`Webseite "${result.saved.title || result.saved.url}" als Grauquelle gespeichert.`);
      setPendingUrlSource(null);
    },
    onError: (error) => setSourceIngestStatus(error instanceof Error ? error.message : "URL konnte nicht geladen werden")
  });

  const attachDroppedImage = useMutation({
    mutationFn: async (file: File) => {
      let noteId = notesSnapshot.activeNoteId || controlledNoteId;
      if (!noteId) {
        const created = await api.createNote(scopedProjectId, { title: "Neue Notiz", markdown: "" });
        noteId = created.note.id;
        setControlledNoteId(noteId);
        queryClient.setQueryData(["note", noteId], { note: created.note });
      }
      const { asset } = await api.uploadNoteAsset(noteId, file);
      const appended = await api.appendNote(noteId, { markdown: `![${asset.filename}](${api.noteAssetUrl(asset.id)})\n` });
      return { noteId: appended.note.id, asset };
    },
    onSuccess: ({ noteId, asset }) => {
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      queryClient.invalidateQueries({ queryKey: ["note", noteId] });
      setSourceIngestStatus(`Bild "${asset.filename}" als Notiz-Anhang gespeichert - nicht als durchsuchbare Quelle.`);
    },
    onError: (error) => setSourceIngestStatus(error instanceof Error ? error.message : "Bild konnte nicht gespeichert werden")
  });

  // Ein Toast verschwindet nach kurzer Zeit von selbst; solange er „pending" ist, bleibt
  // er stehen (z. B. „Auto-Recherche läuft …"). Der volle Verlauf lebt in actionHistory.
  function scheduleToastDismiss(id: string, status: WorkspaceActionEntry["status"]) {
    if (actionDismissTimers.current[id]) {
      clearTimeout(actionDismissTimers.current[id]);
      delete actionDismissTimers.current[id];
    }
    if (status === "pending") {
      return;
    }
    actionDismissTimers.current[id] = setTimeout(() => {
      setActionLog((current) => current.filter((entry) => entry.id !== id));
      delete actionDismissTimers.current[id];
    }, status === "error" ? 8000 : 4500);
  }

  function logAction(title: string, detail: string, status: WorkspaceActionEntry["status"] = "ok") {
    const entry: WorkspaceActionEntry = {
      id: `act_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      title,
      detail,
      status,
      createdAt: new Date().toISOString()
    };
    setActionLog((current) => [...current.slice(-4), entry]);
    setActionHistory((current) => [...current.slice(-60), entry]);
    scheduleToastDismiss(entry.id, status);
    return entry.id;
  }

  function updateAction(id: string, patch: Partial<Pick<WorkspaceActionEntry, "detail" | "status">>) {
    setActionLog((current) => current.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)));
    setActionHistory((current) => current.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)));
    if (patch.status) {
      scheduleToastDismiss(id, patch.status);
    }
  }

  useEffect(() => {
    const timers = actionDismissTimers.current;
    return () => {
      Object.values(timers).forEach(clearTimeout);
    };
  }, []);

  const createProjectCommand = useMutation({
    mutationFn: (name: string) => api.createProject(name),
    onSuccess: ({ project }) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setActiveProject(project.id);
      logAction("Projekt angelegt", `„${project.name}" ist jetzt aktiv.`);
    },
    onError: (error) => logAction("Projekt anlegen fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  const paperSearchCommand = useMutation({
    mutationFn: (query: string) => api.harvestSearch({ query, sources: ["arxiv"], max_results: 10 }),
    onSuccess: (payload) => {
      setCommandSearch({ query: payload.query, results: payload.results, selected: payload.results.slice(0, 3).map((paper) => paper.id) });
    },
    onError: (error) => logAction("Paper-Suche fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  const downloadPapersCommand = useMutation({
    mutationFn: (papers: Paper[]) => api.harvestDownload(papers, true, isRealProject ? (activeProject as string) : undefined),
    onSuccess: (payload) => {
      queryClient.invalidateQueries({ queryKey: ["workspace-pdfs", activeProject] });
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      logAction("Papers heruntergeladen", `${payload.downloaded} PDFs geladen, ${payload.inserted} Einträge gespeichert.`);
      setCommandSearch(null);
    },
    onError: (error) => logAction("Download fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  const createNoteCommand = useMutation({
    mutationFn: (noteTitle: string) => api.createNote(scopedProjectId, { title: noteTitle || "Neue Notiz", markdown: `# ${noteTitle || "Neue Notiz"}\n\n` }),
    onSuccess: ({ note }) => {
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      queryClient.setQueryData(["note", note.id], { note });
      setControlledNoteId(note.id);
      notesActionsRef.current?.selectNote(note.id);
      setNotesOpen(true);
      logAction("Notiz angelegt", `„${note.title}" geöffnet.`);
    },
    onError: (error) => logAction("Notiz anlegen fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  const removePaperMutation = useMutation({
    mutationFn: (paperId: string) => api.removeProjectPaper(activeProject as string, paperId),
    onSuccess: (_, paperId) => {
      queryClient.invalidateQueries({ queryKey: ["workspace-pdfs", activeProject] });
      queryClient.invalidateQueries({ queryKey: ["workspace-project-paper-ids", activeProject] });
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      setSelectedPaperIds((current) => current.filter((id) => id !== paperId));
      setPdfTarget((current) => (current?.kind === "paper" && workspacePaperId(current.paper) === paperId ? null : current));
      logAction("Paper entfernt", `${paperId} aus dem Projekt entfernt (bleibt in der Bibliothek).`);
    },
    onError: (error) => logAction("Paper entfernen fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  const deleteGreyMutation = useMutation({
    mutationFn: (greyId: string) => api.deleteGreySource(greyId),
    onSuccess: (_, greyId) => {
      queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] });
      setSelectedGreyIds((current) => current.filter((id) => id !== greyId));
      setPdfTarget((current) => (current?.kind === "grey" && current.source.id === greyId ? null : current));
      logAction("Grauquelle gelöscht", greyId);
    },
    onError: (error) => logAction("Grauquelle löschen fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  const setPrimaryMutation = useMutation({
    mutationFn: (paperId: string | null) => api.setPrimaryPaper(activeProject as string, paperId),
    onSuccess: (result) => {
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      logAction("Hauptquelle", result.primary_paper_id ? `„${result.primary_paper_id}" ist jetzt Hauptquelle.` : "Hauptquelle entfernt.");
    },
    onError: (error) => logAction("Hauptquelle fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  const deleteCitationMutation = useMutation({
    mutationFn: ({ noteId, citationId }: { noteId: string; citationId: string }) => api.deleteNoteCitation(noteId, citationId),
    onSuccess: (_, { noteId }) => {
      queryClient.invalidateQueries({ queryKey: ["note", noteId] });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      logAction("Notizquelle gelöscht", "Der Zitat-Eintrag wurde entfernt; der Notiztext bleibt unverändert.");
    },
    onError: (error) => logAction("Notizquelle löschen fehlgeschlagen", error instanceof Error ? error.message : String(error), "error")
  });

  /** "/webfrage": research the web, persist findings as grey sources, then answer from them. */
  async function runWebAnswerCommand(value: string) {
    const actionId = logAction("Web-Antwort", `Recherchiere im Web zu „${value}" …`, "pending");
    try {
      const research = await api.deepResearch({ question: value, provider });
      const findings = research.findings.slice(0, 8);
      if (!findings.length) {
        updateAction(actionId, { status: "error", detail: "Keine Web-Treffer gefunden." });
        return;
      }
      const saved = await api.addGreySources(activeProject as string, findings.map(findingToGreyRecord), research.question);
      queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] });
      const ids = saved.saved.map((source) => source.id).filter(Boolean);
      updateAction(actionId, { detail: `${ids.length} Grauquellen gespeichert — beantworte die Frage …` });
      answerMutation.mutate(
        { value: value + verbosityInstruction(verbosity), scope: paperScope, newTurn: nextTurnIsNew, extraGreyIds: ids },
        { onSettled: () => updateAction(actionId, { status: "ok", detail: `${ids.length} Grauquellen gespeichert und in der Antwort verwendet.` }) }
      );
      setNextTurnIsNew(false);
    } catch (error) {
      updateAction(actionId, { status: "error", detail: error instanceof Error ? error.message : String(error) });
    }
  }

  function runExtractionCommand() {
    const targets = paperScope === "selected" && selectedPaperIds.length ? selectedPaperIds : currentScopePaperId ? [currentScopePaperId] : [];
    if (!targets.length) {
      logAction("Extraktion", "Kein Paper aktiv oder ausgewählt — zuerst ein PDF öffnen oder Quellen anhaken.", "error");
      return;
    }
    for (const paperId of targets) {
      const actionId = logAction("Extraktion gestartet", paperId, "pending");
      api
        .runExtraction({ paper_id: paperId, provider, model })
        .then((result) => {
          updateAction(actionId, { status: result.status === "failed" ? "error" : "ok", detail: `${paperId}: ${result.status}` });
          queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
        })
        .catch((error) => updateAction(actionId, { status: "error", detail: `${paperId}: ${error instanceof Error ? error.message : String(error)}` }));
    }
  }

  function ingestDroppedFiles(files: File[]) {
    for (const file of files) {
      const kind = classifyDroppedFile(file);
      if (kind === "pdf") {
        setSourceIngestStatus(`Lade PDF "${file.name}" hoch …`);
        uploadDroppedPdf.mutate(file);
      } else if (kind === "image") {
        setSourceIngestStatus(`Speichere Bild "${file.name}" als Notiz-Anhang …`);
        attachDroppedImage.mutate(file);
      } else {
        const ext = fileExtension(file.name);
        setSourceIngestStatus(`Dateityp wird nicht unterstützt${ext ? `: .${ext}` : ""}.`);
      }
    }
  }

  function handleChatDragOver(event: ReactDragEvent<HTMLFormElement>) {
    if (!Array.from(event.dataTransfer.types || []).includes("Files")) {
      return;
    }
    event.preventDefault();
    setIsDraggingOverChat(true);
  }

  function handleChatDragLeave(event: ReactDragEvent<HTMLFormElement>) {
    if (event.currentTarget.contains(event.relatedTarget as Node | null)) {
      return;
    }
    setIsDraggingOverChat(false);
  }

  function handleChatDrop(event: ReactDragEvent<HTMLFormElement>) {
    const files = Array.from(event.dataTransfer.files || []);
    if (!files.length) {
      return;
    }
    event.preventDefault();
    setIsDraggingOverChat(false);
    ingestDroppedFiles(files);
  }

  function handleChatPaste(event: ReactClipboardEvent<HTMLFormElement>) {
    const files = Array.from(event.clipboardData.files || []);
    if (files.length) {
      event.preventDefault();
      ingestDroppedFiles(files);
      return;
    }
    const text = event.clipboardData.getData("text/plain");
    if (classifyPastedText(text) === "url") {
      event.preventDefault();
      setPendingUrlSource({ url: text.trim() });
    }
  }

  useEffect(() => saveWorkspaceBoolean(scopedProjectId, "useInternet", useInternet), [useInternet, scopedProjectId]);
  useEffect(() => saveWorkspaceBoolean(scopedProjectId, "autoResearch", autoResearch), [autoResearch, scopedProjectId]);
  useEffect(() => saveWorkspaceBoolean(scopedProjectId, "criticalMode", criticalMode), [criticalMode, scopedProjectId]);

  useEffect(() => {
    if (!chatSettingsOpen && !actionsMenuOpen) {
      return;
    }
    const closeOnOutsideClick = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Element && target.closest(".chat-tool-wrap")) {
        return;
      }
      setChatSettingsOpen(false);
      setActionsMenuOpen(false);
    };
    document.addEventListener("pointerdown", closeOnOutsideClick);
    return () => document.removeEventListener("pointerdown", closeOnOutsideClick);
  }, [chatSettingsOpen, actionsMenuOpen]);

  useEffect(() => {
    assistantScopeRef.current = scopedProjectId;
    sessionHydratedRef.current = false;
    setSessionLoadFailed(false);
    const session = loadAssistantSession(scopedProjectId);
    setHistory(session.history);
    setActiveTurnId(session.activeTurnId);
    setQuestion("");
    setSelectedSource(null);
    setActiveEvidenceIndex(0);
    setPdfTarget(null);
    setControlledNoteId("");
    setRequestedCitationId("");
    setSelectedPaperIds([]);
    setSelectedGreyIds([]);
    setFocusedNoteThreadId("");
    setActionLog([]);
    setActionHistory([]);
    setCommandSearch(null);
    setNextTurnIsNew(false);
    setWebOfferDismissedFor("");
    setChatSettingsOpen(false);
    setActionsMenuOpen(false);
    webMutation.reset();
    setParallelSession(null);
    parallelSessionIdRef.current = "";
    setParallelMode(restoredActiveTurnFor(scopedProjectId)?.type === "parallel");
    // The backend copy is authoritative: localStorage drops large sessions silently
    // (quota), so reloads must restore the conversation from the server. Until that
    // read has succeeded we must not write anything back (sessionHydratedRef) — an
    // unreachable backend at boot otherwise looked like "no session" and the empty
    // local state was persisted over the real one.
    let cancelled = false;
    let retryTimer = 0;
    const hydrate = (attempt: number) => {
      void fetchAssistantSession(scopedProjectId).then((result) => {
        if (cancelled || assistantScopeRef.current !== scopedProjectId) {
          return;
        }
        if (result.status === "error") {
          // Backend noch nicht erreichbar (z. B. Tauri-Sidecar bootet): erneut versuchen
          // und bis dahin nichts speichern.
          if (attempt < 3) {
            retryTimer = window.setTimeout(() => hydrate(attempt + 1), 1000 * 3 ** attempt);
          } else {
            setSessionLoadFailed(true);
          }
          return;
        }
        sessionHydratedRef.current = true;
        setSessionLoadFailed(false);
        const server = result.session;
        if (!server) {
          return;
        }
        if (!session.history.length || (server.savedAt ?? 0) >= (session.savedAt ?? 0)) {
          setHistory(server.history);
          setActiveTurnId(server.activeTurnId);
          // Hydrate the deep-analysis view from the authoritative server copy: localStorage
          // can silently drop the (large) research nodes on quota, so the live researchNodes
          // state — initialised synchronously from localStorage — would otherwise stay empty
          // even when the server has the full tree. This is what made reopened analyses blank.
          const activeId = server.activeTurnId || server.history[server.history.length - 1]?.id;
          const activeTurn = server.history.find((t) => t.id === activeId);
          if (activeTurn?.type === "research_tree") {
            const restoredNodes = activeTurn.researchNodes ?? [];
            setDeepMode(true);
            setResearchNodes(restoredNodes);
            researchNodesRef.current = restoredNodes;
            researchSessionIdRef.current = activeTurn.id;
            if (!restoredNodes.length) hydrateResearchSessionFromServer(activeTurn.id);
          } else if (activeTurn?.type === "parallel") {
            setParallelMode(true);
            parallelSessionIdRef.current = activeTurn.id;
            hydrateParallelSessionFromServer(activeTurn.id);
          }
        }
      });
    };
    hydrate(0);
    return () => {
      cancelled = true;
      if (retryTimer) window.clearTimeout(retryTimer);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopedProjectId, sessionReloadNonce]);

  useEffect(() => {
    if (assistantScopeRef.current === scopedProjectId && sessionHydratedRef.current) {
      saveAssistantSession(scopedProjectId, { history, activeTurnId });
    }
  }, [activeTurnId, history, scopedProjectId]);

  useEffect(() => saveWorkspaceBoolean(scopedProjectId, "navigatorOpen", navigatorOpen), [navigatorOpen, scopedProjectId]);
  useEffect(() => saveWorkspaceBoolean(scopedProjectId, "assistantOpen", assistantOpen), [assistantOpen, scopedProjectId]);
  useEffect(() => saveWorkspaceBoolean(scopedProjectId, "pdfOpen", pdfOpen), [pdfOpen, scopedProjectId]);
  useEffect(() => saveWorkspaceBoolean(scopedProjectId, "notesOpen", notesOpen), [notesOpen, scopedProjectId]);
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "navigatorWidth.v3", navigatorWidth), [navigatorWidth, scopedProjectId]);
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "assistantWidth.v3", assistantWidth), [assistantWidth, scopedProjectId]);
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "pdfWidth.v3", pdfWidth), [pdfWidth, scopedProjectId]);
  // Containerbreite live verfolgen, damit die effektiven Pane-Breiten bei Fenster-Resize
  // nachziehen (siehe effectivePaneWidths). Ohne das schneidet ein schmaleres Fenster die
  // rechte Pane ab (overflow: hidden auf .workspace-page) und macht sie unbedienbar.
  useEffect(() => {
    const page = pageRef.current;
    if (!page) return;
    const update = () => setContainerWidth(page.clientWidth);
    update();
    if (typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(update);
    observer.observe(page);
    return () => observer.disconnect();
  }, []);
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "pdfCitationListHeight", pdfCitationListHeight), [pdfCitationListHeight, scopedProjectId]);
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "greySourceListHeight", greySourceListHeight), [greySourceListHeight, scopedProjectId]);

  // Auto-save synthesis to notes when it arrives
  useEffect(() => {
    const synthNode = researchNodes.find((n) => n.status === "synthesis" && n.document);
    if (synthNode && autoSavedTreeRef.current !== synthNode.id) {
      autoSavedTreeRef.current = synthNode.id;
      void saveResearchTreeToNotes();
    }
  }, [researchNodes]); // eslint-disable-line react-hooks/exhaustive-deps

  useEffect(() => {
    if (focusedNoteThreadId && !notesSnapshot.threads.some((thread) => thread.id === focusedNoteThreadId)) {
      setFocusedNoteThreadId("");
    }
  }, [focusedNoteThreadId, notesSnapshot.threads]);

  const handleNotesStateChange = useCallback((snapshot: NotesSurfaceSnapshot) => {
    setNotesSnapshot((current) => (sameNotesSnapshot(current, snapshot) ? current : snapshot));
  }, []);

  const handleActiveNoteChange = useCallback((noteId: string) => {
    setControlledNoteId(noteId);
  }, []);

  const handleNoteCitationOpen = useCallback((citation: NoteCitation | null) => {
    if (!citation) return;
    if (isGreySourcePaperId(citation.paper_id)) {
      const greyId = citation.paper_id.slice(6);
      const source = greySources.find((s) => s.id === greyId);
      if (source) {
        setPdfTarget({ kind: "grey", source });
        setPdfOpen(true);
        setNavigatorTab("pdfs");
        return;
      }
    }
    setPdfTarget({ kind: "noteCitation", citation });
    setPdfOpen(true);
    setNavigatorTab("pdfs");
  }, [greySources]);

  function openGreySource(source: GreySource) {
    setPdfTarget({ kind: "grey", source });
    setPdfOpen(true);
    setNavigatorTab("pdfs");
  }

  function appendGreyQuote(text: string, source: GreySource) {
    const quote = text.trim();
    if (!quote) {
      return;
    }
    const label = source.title || source.url;
    const citation = {
      id: `grey_${Math.random().toString(36).slice(2, 10)}`,
      paper_id: `grey::${source.id}`,
      title: label,
      kind: "grey_source",
      evidence_id: null,
      reference_text: quote,
      pdf_excerpt: source.url,
      evidence_index: 0
    };
    const markdown = `${bulletQuote(quote)}\n\nQuelle: [Z1 - ${label}](sciencekg://citation/${citation.id}) (${source.url})`;
    notesActionsRef.current?.clearInsertPreview();
    void appendToActiveNote(markdown, [citation]);
  }

  function previewGreyQuote(text: string, source: GreySource) {
    const quote = text.trim();
    if (!quote) {
      return;
    }
    const label = source.title || source.url;
    notesActionsRef.current?.previewAppendMarkdown(`${bulletQuote(quote)}\n\nQuelle: [Z1 - ${label}](sciencekg://citation/preview) (${source.url})`);
  }

  function verbosityInstruction(v: typeof verbosity) {
    if (v === "kurz") return " [Bitte antworte präzise in 1–2 Sätzen pro Punkt.]";
    if (v === "ausführlich") return " [Bitte antworte ausführlich mit konkreten Details, Beispielen und Hintergründen.]";
    return "";
  }

  function mentionMarkerLabel(paper: Paper): string {
    const normalized = normalizeWorkspacePaper(paper);
    const title = workspacePaperTitle(normalized);
    const short = title.length > 42 ? `${title.slice(0, 39)}…` : title;
    return `@[${short}]`;
  }

  function placeCursorAfter(position: number) {
    const input = questionInputRef.current;
    if (!input) {
      return;
    }
    requestAnimationFrame(() => {
      input.focus();
      input.setSelectionRange(position, position);
    });
  }

  function applyMention(paper: Paper) {
    if (!mentionState) {
      return;
    }
    const paperId = workspacePaperId(normalizeWorkspacePaper(paper));
    const marker = `${mentionMarkerLabel(paper)} `;
    const before = question.slice(0, mentionState.start);
    const after = question.slice(mentionState.end);
    setQuestion(`${before}${marker}${after}`);
    if (paperId) {
      setPaperScope("selected");
      if (!selectedPaperIds.includes(paperId)) {
        toggleScopedPaper(paperId);
      }
    }
    setMentionState(null);
    setMentionHighlight(0);
    placeCursorAfter(before.length + marker.length);
  }

  function openMentionPicker() {
    const input = questionInputRef.current;
    const caret = input?.selectionStart ?? question.length;
    const before = question.slice(0, caret);
    const needsSpace = before.length > 0 && !/\s$/.test(before);
    const prefix = needsSpace ? " @" : "@";
    const start = before.length + (needsSpace ? 1 : 0);
    setQuestion(`${before}${prefix}${question.slice(caret)}`);
    setMentionState({ query: "", start, end: start + 1 });
    setMentionHighlight(0);
    placeCursorAfter(start + 1);
  }

  function handleQuestionChange(event: ChangeEvent<HTMLInputElement>) {
    const value = event.target.value;
    const caret = event.target.selectionStart ?? value.length;
    setQuestion(value);
    const before = value.slice(0, caret);
    const match = before.match(/(?:^|\s)@([^\s@[\]]*)$/);
    if (match) {
      const query = match[1];
      setMentionState({ query, start: caret - query.length - 1, end: caret });
      setMentionHighlight(0);
    } else if (mentionState) {
      setMentionState(null);
    }
    if (showCommandHelp && !value.trim().toLowerCase().startsWith("/help")) {
      setShowCommandHelp(false);
    }
  }

  function applyPaletteCommand(command: WorkspaceCommandDef) {
    if (command.args) {
      const next = `/${command.name} `;
      setQuestion(next);
      placeCursorAfter(next.length);
      return;
    }
    setQuestion("");
    handleSlashCommand(`/${command.name}`);
  }

  function handleQuestionKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (paletteQuery !== null && paletteCandidates.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setPaletteIndex((current) => (current + 1) % paletteCandidates.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setPaletteIndex((current) => (current - 1 + paletteCandidates.length) % paletteCandidates.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        const candidate = paletteCandidates[paletteIndex] ?? paletteCandidates[0];
        const next = `/${candidate.name} `;
        setQuestion(next);
        placeCursorAfter(next.length);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        applyPaletteCommand(paletteCandidates[paletteIndex] ?? paletteCandidates[0]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setQuestion("");
        return;
      }
    }
    if (mentionState && mentionCandidates.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMentionHighlight((current) => (current + 1) % mentionCandidates.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMentionHighlight((current) => (current - 1 + mentionCandidates.length) % mentionCandidates.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        if (mentionCandidates.length === 1) {
          applyMention(mentionCandidates[0]);
        } else {
          setMentionHighlight((current) => (current + 1) % mentionCandidates.length);
        }
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        applyMention(mentionCandidates[mentionHighlight] ?? mentionCandidates[0]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMentionState(null);
        return;
      }
    }
    if (event.key === "Escape" && showCommandHelp) {
      setShowCommandHelp(false);
    }
  }

  type SlashCommandResult = { handled: boolean; ask?: string; scope?: PaperQuestionScope; newTurn?: boolean; critical?: boolean };

  function handleSlashCommand(raw: string): SlashCommandResult {
    const match = raw.match(/^\/([\wäöüß]+)\s*([\s\S]*)$/i);
    if (!match) {
      return { handled: false };
    }
    const [, name, rest] = match;
    const remainder = rest.trim();
    switch (name.toLowerCase()) {
      case "new":
      case "neu":
        // One-shot: only this (or the next) question starts a new session — the
        // Weiterfragen mode stays active for everything afterwards.
        if (remainder) {
          return { handled: true, ask: remainder, newTurn: true };
        }
        setNextTurnIsNew(true);
        setQuestion("");
        logAction("Neues Gespräch", "Die nächste Frage startet eine neue Session — Weiterfragen bleibt aktiv.");
        return { handled: true };
      case "selected":
      case "auswahl":
        setPaperScope("selected");
        if (!selectedPaperIds.length && !selectedGreyIds.length) {
          setQuestion(remainder);
          openMentionPicker();
          return { handled: true };
        }
        if (remainder) {
          return { handled: true, ask: remainder, scope: "selected" };
        }
        setQuestion("");
        return { handled: true };
      case "alle":
      case "all":
        setPaperScope("all");
        if (remainder) {
          return { handled: true, ask: remainder, scope: "all" };
        }
        setQuestion("");
        return { handled: true };
      case "help":
      case "hilfe":
        setShowCommandHelp(true);
        setQuestion(remainder);
        return { handled: true };
      case "web":
        setUseInternet((v) => !v);
        setQuestion(remainder);
        return { handled: true };
      case "auto":
      case "autorecherche":
        // Mit Frage: einmalige Auto-Recherche (force). Ohne Frage: Schalter umlegen.
        if (remainder) {
          setQuestion("");
          void runAutoResearch(remainder, { scope: paperScope, newTurn: nextTurnIsNew, force: true });
          setNextTurnIsNew(false);
          return { handled: true };
        }
        setAutoResearch((v) => !v);
        setQuestion("");
        logAction("Auto-Recherche", autoResearch ? "Auto-Recherche ausgeschaltet." : "Auto-Recherche eingeschaltet — schwache Antworten lösen automatisch eine Paper-/Web-Recherche aus.");
        return { handled: true };
      case "webfrage":
      case "webanswer":
        if (!remainder) {
          logAction("Web-Antwort", "Frage fehlt — /webfrage <Frage> verwenden.", "error");
          return { handled: true };
        }
        if (!isRealProject) {
          logAction("Web-Antwort", "Internet-Recherche braucht ein echtes Projekt (nicht „Alle Papers“).", "error");
          return { handled: true };
        }
        setQuestion("");
        void runWebAnswerCommand(remainder);
        return { handled: true };
      case "summary":
        // Paper-targeted commands pin the scope to the current paper; without an open
        // paper the question is rejected ("Kein aktives Paper") instead of silently
        // answering from the global KG.
        setPaperScope("current");
        return { handled: true, ask: "Fasse die wichtigsten Erkenntnisse des aktuellen Papers zusammen." + (remainder ? ` ${remainder}` : ""), scope: "current" };
      case "extract":
        setPaperScope("current");
        return { handled: true, ask: "Extrahiere Methoden, Ergebnisse und Schlussfolgerungen." + (remainder ? ` ${remainder}` : ""), scope: "current" };
      case "compare":
        setPaperScope("all");
        return { handled: true, ask: "Vergleiche die wichtigsten Unterschiede und Gemeinsamkeiten der Papers." + (remainder ? ` ${remainder}` : ""), scope: "all" };
      case "kritisch":
      case "critical":
        // Mit Frage: einmalig kritisch beantworten. Ohne Frage: Modus umschalten.
        if (remainder) {
          return { handled: true, ask: remainder, critical: true };
        }
        setCriticalMode((current) => {
          logAction(
            "Kritischer Modus",
            current
              ? "Kritischer Modus ausgeschaltet."
              : "Kritischer Modus eingeschaltet — Antworten benennen Limitationen, Risiken und Gegenbelege explizit."
          );
          return !current;
        });
        setQuestion("");
        return { handled: true };
      case "projekt":
        if (!remainder) {
          logAction("Projekt anlegen", "Name fehlt — /projekt <Name> verwenden.", "error");
          return { handled: true };
        }
        createProjectCommand.mutate(remainder);
        setQuestion("");
        return { handled: true };
      case "notiz":
        createNoteCommand.mutate(remainder);
        setQuestion("");
        return { handled: true };
      case "suche":
      case "papers":
      case "import":
        if (!remainder) {
          logAction("Paper-Suche", "Thema fehlt — /suche <Thema> verwenden.", "error");
          return { handled: true };
        }
        logAction("Paper-Suche", `Suche nach „${remainder}" …`, "pending");
        paperSearchCommand.mutate(remainder);
        setQuestion("");
        return { handled: true };
      case "extraktion":
        runExtractionCommand();
        setQuestion("");
        return { handled: true };
      case "hauptquelle": {
        if (!isRealProject) {
          logAction("Hauptquelle", "Hauptquellen gibt es nur in echten Projekten.", "error");
          return { handled: true };
        }
        const target = currentScopePaperId || (selectedPaperIds.length === 1 ? selectedPaperIds[0] : "");
        if (!target) {
          logAction("Hauptquelle", "Kein Paper geöffnet oder eindeutig ausgewählt.", "error");
          return { handled: true };
        }
        setPrimaryMutation.mutate(target === primaryPaperId ? null : target);
        setQuestion("");
        return { handled: true };
      }
      case "entfernen":
      case "loeschen": {
        if (!isRealProject) {
          logAction("Entfernen", "Quellen entfernen gibt es nur in echten Projekten.", "error");
          return { handled: true };
        }
        if (pdfTarget?.kind === "grey") {
          deleteGreyMutation.mutate(pdfTarget.source.id);
        } else if (currentScopePaperId) {
          removePaperMutation.mutate(currentScopePaperId);
        } else {
          logAction("Entfernen", "Kein Paper oder Grauquelle geöffnet.", "error");
          return { handled: true };
        }
        setQuestion("");
        return { handled: true };
      }
      default:
        return { handled: false };
    }
  }

  /** Dispatch a question; scope/newTurn overrides come from slash commands. */
  function ask(value: string, options: { scope?: PaperQuestionScope; newTurn?: boolean; web?: boolean; auto?: boolean; force?: boolean; critical?: boolean } = {}) {
    const scope = options.scope ?? paperScope;
    const info = deriveScope(scope);
    if (info.blocked) {
      logAction("Frage nicht gestellt", scope === "selected" ? "Keine Quellen ausgewählt." : "Kein aktives Paper geöffnet.", "error");
      return;
    }
    const newTurn = options.newTurn ?? nextTurnIsNew;
    // Auto-Recherche übernimmt Antwort + (bei schwacher Antwort) Paper-/Web-Harvest in
    // einem gestreamten Lauf — der normale Antwort-/Web-Pfad entfällt dann.
    if (options.auto ?? autoResearch) {
      setNextTurnIsNew(false);
      setQuestion("");
      void runAutoResearch(value, { scope, newTurn, force: options.force });
      return;
    }
    answerMutation.mutate({ value: value + verbosityInstruction(verbosity), scope, newTurn, critical: options.critical });
    setNextTurnIsNew(false);
    setQuestion("");
    const runWeb = options.web ?? useInternet;
    if (runWeb && isRealProject) {
      webMutation.mutate(value);
    } else if (runWeb && !isRealProject) {
      logAction("Web-Recherche", "Internet-Recherche braucht ein echtes Projekt (nicht „Alle Papers“).", "error");
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    const raw = question.trim();
    if (!raw) {
      return;
    }
    if (parallelMode) {
      // "/new" (or "/neu") explicitly starts a fresh parallel session; with a question it
      // starts immediately, bare "/new" arms the next question.
      const newMatch = /^\/(new|neu)\b\s*([\s\S]*)$/i.exec(raw);
      if (newMatch) {
        const rest = newMatch[2].trim();
        if (rest) {
          startParallelSession(rest);
        } else {
          setParallelNewArmed(true);
          setQuestion("");
          logAction("Parallel Research", "Die nächste Frage startet eine neue Parallel-Session.");
        }
        return;
      }
      // Weiterfragen: an open session keeps the context (grounded answer + new Vorschlag),
      // it does NOT spin up a new session. Only the very first question (or after "/new")
      // starts a session.
      if (parallelSession && !parallelNewArmed) {
        askParallelFollowup(raw);
        return;
      }
      setParallelNewArmed(false);
      startParallelSession(raw);
      return;
    }
    if (deepMode) {
      submitResearchTree(raw);
      return;
    }
    // `/web` darf an beliebiger Stelle im Prompt stehen und wird zuerst angewendet.
    const inline = extractInlineWebToken(raw);
    if (inline.web && !inline.text) {
      setUseInternet((v) => !v);
      setQuestion("");
      return;
    }
    const value = inline.text;
    if (value.startsWith("/")) {
      // Befehl + Frage in einem Enter: erst der Befehl (Scope, neue Session, Aktion),
      // direkt danach die Frage — kein zweites Enter nötig.
      const command = handleSlashCommand(value);
      if (command.handled) {
        if (command.ask) {
          ask(command.ask, { scope: command.scope, newTurn: command.newTurn, web: inline.web || undefined, critical: command.critical });
        }
        return;
      }
    }
    if (inline.web) {
      setUseInternet(true);
    }
    ask(value, { web: inline.web || undefined });
  }

  /** Load a persisted deep-research tree from the server (used when the local/cached
   * copy lost its nodes — e.g. localStorage quota). Keyed by the session/turn id. */
  function hydrateResearchSessionFromServer(turnId: string) {
    void api
      .getResearchSession(turnId)
      .then((res) => {
        const nodes = res.session?.nodes ?? [];
        if (!nodes.length || researchSessionIdRef.current !== turnId) return;
        researchNodesRef.current = nodes;
        setResearchNodes(nodes);
        setDeepMode(true);
      })
      .catch(() => {});
  }

  /** Start a Parallel-Research session: the backend proposes grounded variants. A draft
   * turn appears immediately; it's reconciled to the server session id on success. */
  function startParallelSession(q: string) {
    const question = q.trim();
    if (!question) return;
    setParallelMode(true);
    setDeepMode(false);
    setParallelSession(null);
    setParallelLoading(true);
    setQuestion("");
    // The methods + result entry live in the Notes "Ergebnisse" tab — make sure it's visible.
    setNotesOpen(true);
    setNotesTab("results");

    const draftId = crypto.randomUUID();
    parallelSessionIdRef.current = draftId;
    const draft: AssistantTurn = {
      id: draftId,
      question,
      answer: null as unknown as AssistantTurn["answer"],
      verification: [],
      createdAt: new Date().toISOString(),
      type: "parallel",
      parallelSessionId: draftId,
      parallelVariantCount: 0,
    };
    setHistory((prev) => [draft, ...prev]);
    setActiveTurnId(draftId);

    const info = deriveScope(paperScope);
    api
      .createParallelSession(activeProject || scopedProjectId, {
        question,
        variant_count: 3,
        paper_ids: info.scopedPaperIds.length ? info.scopedPaperIds : undefined,
        provider: provider || undefined,
        model: model || undefined,
      })
      .then(({ session }) => {
        parallelSessionIdRef.current = session.id;
        setParallelSession(session);
        setHistory((prev) =>
          prev.map((t) =>
            t.id === draftId
              ? { ...draft, id: session.id, parallelSessionId: session.id, parallelVariantCount: session.variants.length }
              : t,
          ),
        );
        setActiveTurnId(session.id);
      })
      .catch((err: unknown) => {
        logAction("Parallel Research", err instanceof Error ? err.message : "Konnte Session nicht starten.", "error");
      })
      .finally(() => setParallelLoading(false));
  }

  /** Weiterfragen inside an open parallel session: grounded answer (threaded under the
   * overview) + a new structured Vorschlag appended to the Ergebnisse. No new session. */
  function askParallelFollowup(q: string) {
    const question = q.trim();
    const session = parallelSession;
    if (!question || !session || parallelFollowupLoading) return;
    setQuestion("");
    setParallelFollowupLoading(true);
    const info = deriveScope(paperScope);
    api
      .askParallelFollowup(session.id, {
        question,
        paper_ids: info.scopedPaperIds.length ? info.scopedPaperIds : undefined,
        provider: provider || undefined,
        model: model || undefined,
      })
      .then(({ session: updated }) => {
        if (parallelSessionIdRef.current !== updated.id) return;
        onParallelChange(updated);
      })
      .catch((err: unknown) => {
        logAction("Parallel Research", err instanceof Error ? err.message : "Folgefrage fehlgeschlagen.", "error");
      })
      .finally(() => setParallelFollowupLoading(false));
  }

  /** Open a server-persisted parallel session from the browser list: ensure a history
   * turn exists (older sessions may predate the localStorage history), then hydrate. */
  function openParallelServerSession(summary: ParallelSessionSummary) {
    setHistory((prev) => {
      if (prev.some((t) => t.id === summary.id)) return prev;
      const turn: AssistantTurn = {
        id: summary.id,
        question: summary.question,
        answer: null as unknown as AssistantTurn["answer"],
        verification: [],
        createdAt: summary.updated_timestamp ?? new Date().toISOString(),
        type: "parallel",
        parallelSessionId: summary.id,
        parallelVariantCount: summary.variant_count,
      };
      return [turn, ...prev];
    });
    setActiveTurnId(summary.id);
    parallelSessionIdRef.current = summary.id;
    setDeepMode(false);
    setParallelMode(true);
    setParallelSession(null);
    hydrateParallelSessionFromServer(summary.id);
    setNotesOpen(true);
    setNotesTab("results");
  }

  /** Load a persisted parallel-research session from the server (open/restore). */
  function hydrateParallelSessionFromServer(sessionId: string) {
    setParallelLoading(true);
    api
      .getParallelSession(sessionId)
      .then(({ session }) => {
        if (parallelSessionIdRef.current !== sessionId) return;
        setParallelSession(session);
      })
      .catch(() => {})
      .finally(() => setParallelLoading(false));
  }

  function onParallelChange(session: ParallelSession) {
    setParallelSession(session);
    setHistory((prev) =>
      prev.map((t) => (t.id === session.id ? { ...t, parallelVariantCount: session.variants.length } : t)),
    );
  }

  function launchResearchTree(q: string, useHarvest: boolean, clarificationContext: string, initialNodes?: ResearchNode[]) {
    const abort = new AbortController();
    researchAbortRef.current = abort;
    researchNodesRef.current = initialNodes ?? [];
    setResearchNodes(initialNodes ?? []);
    setResearchLlmError(null);
    setResearchLoading(true);
    setQuestion("");

    const info = deriveScope(paperScope);
    const greyIds = Array.from(new Set([...info.greySourceIds]));

    const enrichedQuestion = clarificationContext.trim()
      ? `${q}\n\nFokus und Kontext für die Analyse: ${clarificationContext.trim()}`
      : q;

    // Assign a stable session ID — fresh UUID for a new run, preserved for resume.
    const sessionId = initialNodes?.length && researchSessionIdRef.current
      ? researchSessionIdRef.current
      : crypto.randomUUID();
    researchSessionIdRef.current = sessionId;

    // Create a draft session entry immediately so it appears in the list while running.
    const draft: AssistantTurn = {
      id: sessionId,
      question: enrichedQuestion,
      answer: null as unknown as AssistantTurn["answer"],
      verification: [],
      createdAt: new Date().toISOString(),
      type: "research_tree",
      researchNodes: initialNodes ?? [],
    };
    setHistory((prev) => {
      const idx = prev.findIndex((t) => t.id === sessionId);
      if (idx >= 0) { const next = [...prev]; next[idx] = draft; return next; }
      return [draft, ...prev];
    });

    const payload: ResearchTreeRequest = {
      question: enrichedQuestion,
      project_id: activeProject || undefined,
      depth: deepDepth,
      branches: deepBranches,
      provider: provider || undefined,
      model: model || undefined,
      paper_ids: info.scopedPaperIds.length ? info.scopedPaperIds : info.projectOnlyScope || greyIds.length ? ["__none__"] : undefined,
      grey_source_ids: greyIds.length ? greyIds : undefined,
      include_project_grey: paperScope === "all" && isRealProject ? true : undefined,
      auto_harvest: useHarvest || undefined,
      initial_nodes: initialNodes?.length ? initialNodes : undefined,
      session_id: sessionId,
    };

    streamResearchTree(
      payload,
      async (node) => {
        if (node.status === "llm_error") {
          setResearchLlmError({
            kind: node.error_kind ?? "unknown",
            message: node.message ?? node.error ?? "LLM-Aufruf fehlgeschlagen.",
            error: node.error ?? "",
          });
          return;
        }
        if (node.status === "done" && node.answer) {
          try {
            const sources = await verificationSourcesFor(node.answer);
            node = { ...node, verification: sources };
          } catch {
            // verification optional
          }
        }
        setResearchNodes((prev) => {
          const idx = prev.findIndex((n) => n.id === node.id);
          let next: ResearchNode[];
          if (idx >= 0) {
            next = [...prev];
            next[idx] = node;
          } else {
            next = [...prev, node];
          }
          researchNodesRef.current = next;
          return next;
        });
      },
      abort.signal,
    )
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        console.error("Research tree error:", err);
      })
      .finally(() => {
        setResearchLoading(false);
        // Save final node state to session history (covers both completed and paused runs).
        // The useEffect at line ~775 auto-persists history changes to localStorage.
        const finalNodes = researchNodesRef.current;
        if (finalNodes.length > 0) {
          const saved: AssistantTurn = {
            id: sessionId,
            question: enrichedQuestion,
            answer: null as unknown as AssistantTurn["answer"],
            verification: [],
            createdAt: new Date().toISOString(),
            type: "research_tree",
            researchNodes: finalNodes,
          };
          setHistory((prev) => {
            const idx = prev.findIndex((t) => t.id === sessionId);
            if (idx >= 0) { const next = [...prev]; next[idx] = saved; return next; }
            return [saved, ...prev];
          });
          // Authoritative server copy (verification-enriched), independent of the
          // localStorage quota / debounce path — this is what makes a reopened deep
          // analysis show its full tree instead of an empty session.
          void api
            .upsertResearchSession(sessionId, {
              project_id: activeProject || scopedProjectId || null,
              question: enrichedQuestion,
              status: "done",
              nodes: finalNodes,
            })
            .catch(() => {});
        }
        // Invalidate paper/grey caches so auto-harvested items appear without manual refresh.
        queryClient.invalidateQueries({ queryKey: ["papers"] });
        queryClient.invalidateQueries({ queryKey: ["grey-sources"] });
      });
  }

  /** Ask the LLM for focus directions, then open the clarify dialog. */
  function startClarifyFlow(q: string, useHarvest: boolean) {
    pendingDeepRef.current = { question: q, harvest: useHarvest };
    setClarifyLoading(true);
    api.clarifyQuestion(q, provider || null, model || null)
      .then(({ directions }) => {
        if (!directions.length) {
          // Nothing to clarify — start directly.
          finishClarify(true);
          return;
        }
        setClarifyDirections(directions);
        setClarifySelected([]);
        setClarifyFreetext("");
        setShowClarifyDialog(true);
      })
      .catch(() => {
        // If clarify fails, start directly without focus directions.
        finishClarify(true);
      })
      .finally(() => {
        setClarifyLoading(false);
      });
  }

  /** Launch the analysis from the clarify dialog (or skip it). */
  function finishClarify(skip: boolean) {
    setShowClarifyDialog(false);
    const pending = pendingDeepRef.current;
    pendingDeepRef.current = null;
    if (!pending) {
      return;
    }
    const context = skip
      ? ""
      : [
          ...clarifySelected.map((i) => clarifyDirections[i]).filter(Boolean),
          clarifyFreetext.trim(),
        ]
          .filter(Boolean)
          .join("; ");
    launchResearchTree(pending.question, pending.harvest, context);
  }

  /** Entry point from the composer: step 1 is the auto-harvest question. */
  function submitResearchTree(q: string) {
    if (researchLoading) {
      researchAbortRef.current?.abort();
      return;
    }
    pendingDeepRef.current = { question: q, harvest: autoHarvest };
    setShowHarvestDialog(true);
  }

  /** Harvest dialog choice → continue into the clarify step. */
  function chooseHarvest(useHarvest: boolean) {
    setAutoHarvest(useHarvest);
    setShowHarvestDialog(false);
    const q = pendingDeepRef.current?.question;
    if (q) {
      startClarifyFlow(q, useHarvest);
    }
  }

  /** Toggle a focus direction by index (used by click + number keys). */
  function toggleClarifyDirection(index: number) {
    setClarifySelected((cur) =>
      cur.includes(index) ? cur.filter((i) => i !== index) : [...cur, index],
    );
  }

  function stopResearchTree() {
    researchAbortRef.current?.abort();
    setResearchLoading(false);
  }

  function drillDeeperInTree(nodeId: string, question: string) {
    const parentNode = researchNodes.find((n) => n.id === nodeId);
    const childDepth = (parentNode?.depth ?? 0) + 1;
    const abort = new AbortController();
    researchAbortRef.current = abort;
    setResearchLlmError(null);
    setResearchLoading(true);

    const info = deriveScope(paperScope);
    const greyIds = Array.from(new Set([...info.greySourceIds]));
    const payload: ResearchTreeRequest = {
      question,
      project_id: activeProject || undefined,
      depth: 1,
      branches: deepBranches,
      provider: provider || undefined,
      model: model || undefined,
      paper_ids: info.scopedPaperIds.length ? info.scopedPaperIds : info.projectOnlyScope || greyIds.length ? ["__none__"] : undefined,
      grey_source_ids: greyIds.length ? greyIds : undefined,
      include_project_grey: paperScope === "all" && isRealProject ? true : undefined,
    };

    streamResearchTree(
      payload,
      async (node) => {
        if (node.status === "llm_error") {
          setResearchLlmError({
            kind: node.error_kind ?? "unknown",
            message: node.message ?? node.error ?? "LLM-Aufruf fehlgeschlagen.",
            error: node.error ?? "",
          });
          return;
        }
        const remapped: ResearchNode = {
          ...node,
          parent_id: node.parent_id === null ? nodeId : node.parent_id,
          depth: node.depth + childDepth,
        };
        if (remapped.status === "done" && remapped.answer) {
          try {
            const sources = await verificationSourcesFor(remapped.answer);
            remapped.verification = sources;
          } catch { /* verification optional */ }
        }
        setResearchNodes((prev) => {
          const idx = prev.findIndex((n) => n.id === remapped.id);
          if (idx >= 0) {
            const next = [...prev];
            next[idx] = remapped;
            return next;
          }
          return [...prev, remapped];
        });
      },
      abort.signal,
    )
      .catch((err: unknown) => {
        if (err instanceof Error && err.name === "AbortError") return;
        console.error("Drill deeper error:", err);
      })
      .finally(() => {
        setResearchLoading(false);
      });
  }

  async function saveResearchTreeToNotes() {
    const synthesisNode = researchNodes.find((n) => n.status === "synthesis");
    const treeNodes = researchNodes.filter((n) => n.status !== "synthesis");
    const rootNodes = treeNodes.filter((n) => n.parent_id === null);
    if (!rootNodes.length && !synthesisNode) return;

    const rootQ = rootNodes[0]?.question ?? synthesisNode?.question ?? "";

    let markdown: string;
    if (synthesisNode?.document) {
      markdown = `# Tiefenanalyse: ${rootQ}\n\n${synthesisNode.document}`;
    } else {
      function fmtNode(node: ResearchNode, level: number): string {
        const heading = "#".repeat(Math.min(level, 6));
        const parts = [`${heading} ${node.question}`];
        if (node.answer?.answer) parts.push("", node.answer.answer);
        const children = treeNodes.filter((n) => n.parent_id === node.id);
        for (const child of children) parts.push("", fmtNode(child, level + 1));
        return parts.join("\n");
      }
      markdown = `# Tiefenanalyse: ${rootQ}\n\n` + rootNodes.map((n) => fmtNode(n, 2)).join("\n\n---\n\n");
    }

    notesActionsRef.current?.clearInsertPreview();
    await appendToActiveNote(markdown);
  }

  /** Die fertige Tiefenanalyse als zitierbare Projektquelle ablegen — inklusive der
   *  Paper, auf denen sie beruht (die landen in source_paper_ids und werden beim
   *  Auswaehlen der Analyse mit in den Frage-Kontext gezogen). */
  async function saveResearchTreeAsSource() {
    const synthesisNode = researchNodes.find((n) => n.status === "synthesis");
    const treeNodes = researchNodes.filter((n) => n.status !== "synthesis");
    if (!synthesisNode?.document || !isRealProject) {
      throw new Error(
        !isRealProject
          ? "Quellen lassen sich nur in einem echten Projekt speichern."
          : "Es gibt noch keine Gesamtantwort zum Speichern."
      );
    }
    const rootQuestion = treeNodes.find((n) => n.depth === 0)?.question ?? synthesisNode.question ?? "Tiefenanalyse";
    const seen = new Set<string>();
    const sources: VerificationSource[] = [];
    for (const node of treeNodes) {
      for (const source of node.verification ?? []) {
        if (!seen.has(source.paper_id)) {
          seen.add(source.paper_id);
          sources.push(source);
        }
      }
    }
    const result = await api.saveResearchTreeAsSource({
      project_id: activeProject as string,
      root_question: rootQuestion,
      document: synthesisNode.document,
      nodes: researchNodes,
      sources,
      session_id: activeTurnId || undefined,
    });
    queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] });
    logAction("Analyse als Quelle gespeichert", `„${rootQuestion}" ist jetzt zitierbar (${result.paper_count} Quell-Paper).`);
    return result;
  }

  function openAssistantSource(source: VerificationSource | null, evidenceIndex = 0, quote = "", options: { openPdf?: boolean; syncPdfTarget?: boolean } = {}) {
    setSelectedAnswerQuote(null);
    if (!source) {
      setSelectedSource(null);
      setActiveEvidenceIndex(0);
      return;
    }
    const nextIndex = Math.max(0, Math.min(evidenceIndex, source.evidence.length - 1));
    setSelectedSource(source);
    setActiveEvidenceIndex(nextIndex);
    if (options.openPdf || options.syncPdfTarget) {
      // Grey sources open in GreySourceView, not the PDF canvas
      if (isGreySourcePaperId(source.paper_id)) {
        const greyId = source.paper_id.slice(6);
        const greySource = greySources.find((s) => s.id === greyId);
        if (greySource) {
          setPdfTarget({ kind: "grey", source: greySource });
        }
      } else {
        setPdfTarget({ kind: "assistant", source, evidenceIndex: nextIndex });
        // A cited paper that was never downloaded would otherwise show the "Kein PDF
        // verfügbar" abstract limbo. Instead, pull the PDF into the project on demand
        // (→ falls back to the web/grey view when nothing is downloadable).
        if (!source.pdf_available && !ingestedPdfIds.has(source.paper_id)) {
          void ingestCitedSource(source);
        }
      }
    }
    if (options.openPdf) {
      setPdfOpen(true);
      setNavigatorTab("pdfs");
    }
    if (quote) {
      setSelectedAnswerQuote({ paperId: source.paper_id, evidenceIndex: nextIndex, text: cleanAnswerQuote(quote) });
    }
  }

  // Download (and extract, in the background) a cited paper that has no local PDF yet. On
  // success the PDF shows in the canvas; when no PDF is downloadable the source opens in the
  // grey/web view. Either way the user never lands in the "no PDF" abstract limbo.
  async function ingestCitedSource(source: VerificationSource) {
    if (isGreySourcePaperId(source.paper_id) || source.pdf_available || ingestedPdfIds.has(source.paper_id)) {
      return;
    }
    if (ingestingPaperId) {
      return;
    }
    setIngestingPaperId(source.paper_id);
    try {
      const res = await api.paperIngest({
        paper_id: source.paper_id,
        project_id: activeProject || undefined,
        provider: provider || undefined,
        model: model || undefined
      });
      if (res.has_local_pdf) {
        setIngestedPdfIds((prev) => new Set(prev).add(source.paper_id));
        void queryClient.invalidateQueries({ queryKey: ["papers"] });
        if (isRealProject) {
          void queryClient.invalidateQueries({ queryKey: ["workspace-project-paper-ids", activeProject] });
        }
        setSourceIngestStatus(`PDF zu "${source.title || source.paper_id}" geladen – Extraktion läuft im Hintergrund.`);
      } else if (res.external_url) {
        const saved = await api.addGreySourceFromUrl(activeProject as string, res.external_url);
        await queryClient.invalidateQueries({ queryKey: ["grey-sources", activeProject] });
        setPdfTarget({ kind: "grey", source: saved.saved });
        setSourceIngestStatus(`Quelle "${saved.saved.title || saved.saved.url}" als Webquelle geöffnet.`);
      } else {
        setSourceIngestStatus("Für diese Quelle ist kein PDF verfügbar.");
      }
    } catch (error) {
      setSourceIngestStatus(error instanceof Error ? error.message : "Quelle konnte nicht geladen werden.");
    } finally {
      setIngestingPaperId((current) => (current === source.paper_id ? null : current));
    }
  }

  function openSelectedAssistantPdf() {
    if (!selectedSource) {
      return;
    }
    setPdfTarget({ kind: "assistant", source: selectedSource, evidenceIndex: activeEvidenceIndex });
    setPdfOpen(true);
    setNavigatorTab("pdfs");
  }

  function citationMetaFor(pool: VerificationSource[], citation: string, context = "", links = answer?.citation_links ?? [], citationStart?: number) {
    return citationMetasFor(pool, citation, context, links, citationStart)[0] ?? null;
  }

  function jumpToCitationIn(pool: VerificationSource[], citation: string, context = "", quote = "", links = answer?.citation_links ?? [], citationStart?: number) {
    const meta = citationMetaFor(pool, citation, context, links, citationStart);
    if (meta) {
      openAssistantSource(meta.source, meta.evidenceIndex, quote || context, { syncPdfTarget: pdfOpen });
    }
  }

  function jumpToCitationMeta(meta: CitationMeta, context = "", quote = "") {
    openAssistantSource(meta.source, meta.evidenceIndex, quote || context, { syncPdfTarget: pdfOpen });
  }

  function handleUnresolvedCitationClick(citationId: string) {
    // Grey source citations from the answer pipeline use "grey::<id>" format
    if (isGreySourcePaperId(citationId)) {
      const greyId = citationId.slice(6);
      const source = greySources.find((s) => s.id === greyId);
      if (source) {
        openGreySource(source);
        return;
      }
    }
    // Try to find the paper in the project papers list
    const found = pdfPapers.find((p) => {
      const pid = workspacePaperId(normalizeWorkspacePaper(p));
      return pid && sameCitation(pid, citationId);
    });
    if (found) {
      const normalized = normalizeWorkspacePaper(found);
      setPdfTarget({ kind: "paper", paper: normalized });
      setPdfOpen(true);
      setNavigatorTab("pdfs");
    } else {
      setPdfTarget({ kind: "missing", paperId: citationId });
      setPdfOpen(true);
      setNavigatorTab("pdfs");
    }
  }

  function openThreadInNotesAssistant(threadId: string) {
    setAssistantMode("notes");
    setAssistantOpen(true);
    setNotesOpen(true);
    setFocusedNoteThreadId("");
    notesActionsRef.current?.locateThread(threadId);
    const thread = notesSnapshot.threads.find((item) => item.id === threadId);
    for (const item of notesSnapshot.threads) {
      if (item.id !== threadId && !threadCollapsed(item)) {
        notesActionsRef.current?.toggleThreadCollapsed(item.id);
      }
    }
    if (thread) {
      notesActionsRef.current?.toggleThreadCollapsed(threadId);
    }
  }

  // Baut Text + Quellenliste für die Notiz-Übernahme: bevorzugt nur das belegte
  // Segment (statt des ganzen Satzes) und nimmt ALLE Quellen des Satzes mit.
  function citationInsertPayload(
    source: VerificationSource,
    evidenceIndex: number,
    quote: string,
    extras?: CitationInsertExtras
  ): { markdown: string; citations: Record<string, unknown>[] } | null {
    const evidence = source.evidence[evidenceIndex];
    if (!evidence) {
      return null;
    }
    // The text that lands in the note is also stored on the citation, so clicking
    // the citation later shows exactly this passage again.
    const insertedText =
      meaningfulQuote(extras?.segment ?? "") || meaningfulQuote(quote) || evidence.pdf_excerpt || evidence.reference_text;
    const primary = noteCitation(source, evidence, evidenceIndex, insertedText);
    const citations: Record<string, unknown>[] = [primary];
    const entries = [{ source, evidenceIndex, citationId: String(primary.id) }];
    for (const sibling of extras?.siblings ?? []) {
      const siblingEvidence = sibling.source.evidence[sibling.evidenceIndex];
      if (!siblingEvidence) continue;
      const siblingCitation = noteCitation(sibling.source, siblingEvidence, sibling.evidenceIndex);
      if (citations.some((existing) => existing.id === siblingCitation.id)) continue;
      citations.push(siblingCitation);
      entries.push({ source: sibling.source, evidenceIndex: sibling.evidenceIndex, citationId: String(siblingCitation.id) });
    }
    return { markdown: formatNoteQuoteMulti(insertedText, entries), citations };
  }

  // ---- Selektion in der Antwort: Nachchecken / Weiterfragen / In Notiz / Korrigieren ----

  type AnswerSelectionState = { text: string; blockId: string; left: number; top: number };
  const [answerSelection, setAnswerSelection] = useState<AnswerSelectionState | null>(null);
  const [selectionCheck, setSelectionCheck] = useState<{
    status: "loading" | "done" | "error";
    results?: ClaimCheckResult[];
    error?: string;
  } | null>(null);
  const [correctionDraft, setCorrectionDraft] = useState<string | null>(null);
  const answerBlocksRef = useRef<HTMLDivElement | null>(null);

  /** Selektionstext ohne die gerenderten Zitat-Chips (Z12, Titel-Kürzel …) — sonst
   *  landet deren Beschriftung mitten im übernommenen Text. */
  function selectionPlainText(selection: Selection): string {
    try {
      const holder = document.createElement("div");
      holder.appendChild(selection.getRangeAt(0).cloneContents());
      holder
        .querySelectorAll(".citation-link-wrap, .citation-link, .citation-hover-card, .claim-check-card, .citation-more")
        .forEach((node) => node.remove());
      return (holder.textContent ?? "").replace(/\s+/g, " ").trim();
    } catch {
      return selection.toString().replace(/\s+/g, " ").trim();
    }
  }

  function handleAnswerMouseUp() {
    // getSelection erst nach dem Klick-Handling auslesen.
    window.setTimeout(() => {
      const selection = window.getSelection();
      const container = answerBlocksRef.current;
      if (!selection || selection.isCollapsed || !container) {
        return;
      }
      const text = selectionPlainText(selection);
      const anchor = selection.anchorNode;
      const anchorElement = anchor instanceof Element ? anchor : anchor?.parentElement ?? null;
      if (!text || text.length < 8 || !anchorElement || !container.contains(anchorElement)) {
        return;
      }
      const blockId = anchorElement.closest("[data-block-id]")?.getAttribute("data-block-id") ?? "";
      const rect = selection.getRangeAt(0).getBoundingClientRect();
      const width = 340;
      setSelectionCheck(null);
      setCorrectionDraft(null);
      setAnswerSelection({
        text,
        blockId,
        left: Math.min(Math.max(12, rect.left), Math.max(12, window.innerWidth - width - 12)),
        top: Math.min(rect.bottom + 8, window.innerHeight - 60)
      });
    }, 0);
  }

  function closeAnswerSelection() {
    setAnswerSelection(null);
    setSelectionCheck(null);
    setCorrectionDraft(null);
  }

  // Klick außerhalb schließt das Selektions-Popover (statt nur das X).
  useEffect(() => {
    if (!answerSelection) {
      return;
    }
    const onPointerDown = (event: globalThis.PointerEvent) => {
      const target = event.target as Element | null;
      if (!target?.closest(".answer-selection-popover")) {
        closeAnswerSelection();
      }
    };
    document.addEventListener("pointerdown", onPointerDown);
    return () => document.removeEventListener("pointerdown", onPointerDown);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [answerSelection]);

  const selectionBlock = answerSelection
    ? activeBlocks.find((block) => block.id === answerSelection.blockId) ?? activeBlocks[activeBlocks.length - 1]
    : undefined;

  /** Metas der Zitate, die zur Selektion gehören: Brackets in der Selektion selbst,
   *  sonst das erste Bracket direkt nach der Selektion im Antworttext. */
  function selectionCitationMetas(selection: AnswerSelectionState, block: AssistantAnswerBlock): CitationMeta[] {
    const metas: CitationMeta[] = [];
    const push = (raw: CitationMeta[] | CitationMeta | null) => {
      for (const meta of Array.isArray(raw) ? raw : raw ? [raw] : []) {
        if (!metas.some((existing) => existing.source.paper_id === meta.source.paper_id && existing.evidenceIndex === meta.evidenceIndex)) {
          metas.push(meta);
        }
      }
    };
    const links = block.answer.citation_links ?? [];
    const bracketRe = /\[([^\]]+)\]/g;
    for (const match of selection.text.matchAll(bracketRe)) {
      push(citationMetasFor(block.verification, match[1], selection.text, links));
    }
    if (!metas.length) {
      // Bracket direkt hinter der Selektion (der Satz endet meist mit dem Zitat).
      const answerText = block.answer.answer;
      const position = answerText.replace(/\s+/g, " ").indexOf(selection.text);
      if (position >= 0) {
        const tail = answerText.replace(/\s+/g, " ").slice(position + selection.text.length, position + selection.text.length + 160);
        const tailMatch = /^[^.!?\n]*?\[([^\]]+)\]/.exec(tail);
        if (tailMatch) {
          push(citationMetasFor(block.verification, tailMatch[1], selection.text, links));
        }
      }
    }
    if (!metas.length) {
      for (const source of block.verification.slice(0, 3)) {
        if (source.evidence.length) {
          push({ source, evidenceIndex: 0, evidenceId: source.evidence[0]?.evidence_id });
        }
      }
    }
    return metas.slice(0, 4);
  }

  async function checkAnswerSelection() {
    if (!answerSelection || !selectionBlock) {
      return;
    }
    const metas = selectionCitationMetas(answerSelection, selectionBlock);
    const statement = cleanAnswerQuote(answerSelection.text);
    if (!metas.length || !statement) {
      setSelectionCheck({ status: "error", error: "Keine zugehörigen Quellen gefunden." });
      return;
    }
    setSelectionCheck({ status: "loading" });
    try {
      const res = await api.claimCheck({
        statement,
        paper_ids: metas.map((meta) => meta.source.paper_id),
        titles: Object.fromEntries(metas.map((meta) => [meta.source.paper_id, meta.source.title || ""])),
        evidence_texts: Object.fromEntries(
          metas.map((meta) => {
            const evidence = meta.source.evidence[meta.evidenceIndex];
            // reference_text nur mitgeben, wenn es keine zirkuläre Kopie des
            // Antwortsatzes ist (claim_excerpt/approx_region).
            const evidenceMeta = (evidence?.metadata ?? {}) as Record<string, unknown>;
            const selfReferential =
              evidenceMeta.context_policy === "claim_excerpt" || evidenceMeta.context_policy === "approx_region";
            return [meta.source.paper_id, evidence ? evidence.pdf_excerpt || (selfReferential ? "" : evidence.reference_text) : ""];
          })
        ),
        provider: provider || undefined,
        model: model || undefined
      });
      setSelectionCheck({ status: "done", results: res.checks });
    } catch (error) {
      setSelectionCheck({ status: "error", error: error instanceof Error ? error.message : "Prüfung fehlgeschlagen" });
    }
  }

  function askAboutSelection() {
    if (!answerSelection) {
      return;
    }
    const quoted = answerSelection.text.length > 260 ? `${answerSelection.text.slice(0, 257)}…` : answerSelection.text;
    setQuestion(`Zu dieser Aussage aus deiner Antwort: „${quoted}" — `);
    closeAnswerSelection();
    questionInputRef.current?.focus();
  }

  function selectionNotePayload(): { markdown: string; citations: Record<string, unknown>[] } | null {
    if (!answerSelection || !selectionBlock) {
      return null;
    }
    const metas = selectionCitationMetas(answerSelection, selectionBlock);
    const quoteText = cleanAnswerQuote(answerSelection.text);
    const citations: Record<string, unknown>[] = [];
    const entries: Array<{ source: VerificationSource; evidenceIndex: number; citationId: string }> = [];
    for (const meta of metas) {
      const evidence = meta.source.evidence[meta.evidenceIndex];
      if (!evidence) continue;
      const citation = noteCitation(meta.source, evidence, meta.evidenceIndex, quoteText);
      if (citations.some((existing) => existing.id === citation.id)) continue;
      citations.push(citation);
      entries.push({ source: meta.source, evidenceIndex: meta.evidenceIndex, citationId: String(citation.id) });
    }
    const markdown = entries.length ? formatNoteQuoteMulti(quoteText, entries) : bulletQuote(quoteText);
    return { markdown, citations };
  }

  function insertSelectionIntoNote() {
    const payload = selectionNotePayload();
    if (!payload) {
      return;
    }
    notesActionsRef.current?.clearInsertPreview();
    closeAnswerSelection();
    void appendToActiveNote(payload.markdown, payload.citations);
  }

  function previewSelectionInNote() {
    const payload = selectionNotePayload();
    if (payload) {
      notesActionsRef.current?.previewAppendMarkdown(payload.markdown);
    }
  }

  /** Korrigiert die selektierte Passage im gespeicherten Antworttext (z.B. nachdem
   *  der Nachcheck sie als nicht gestützt entlarvt hat). */
  function applyAnswerCorrection() {
    if (!answerSelection || !selectionBlock || correctionDraft === null) {
      return;
    }
    const replacement = correctionDraft.trim();
    const original = answerSelection.text;
    const blockId = selectionBlock.id;
    setHistory((current) =>
      current.map((turn) => {
        const blocks = turnBlocks(turn);
        if (!blocks.some((block) => block.id === blockId)) {
          return turn;
        }
        const nextBlocks = blocks.map((block) => {
          if (block.id !== blockId) {
            return block;
          }
          const answerText = block.answer.answer;
          // Selektion stammt aus gerendertem Text (Whitespace normalisiert) — im
          // Roh-Markdown flexibel über Whitespace hinweg suchen.
          const pattern = new RegExp(
            original
              .split(/\s+/)
              .map((word) => word.replace(/[.*+?^${}()|[\]\\]/g, "\\$&"))
              .join("\\s+")
          );
          const nextAnswer = pattern.test(answerText)
            ? answerText.replace(pattern, replacement)
            : answerText;
          return { ...block, answer: { ...block.answer, answer: nextAnswer } };
        });
        const last = nextBlocks[nextBlocks.length - 1];
        return { ...turn, blocks: nextBlocks, answer: last ? last.answer : turn.answer };
      })
    );
    logAction("Antwort korrigiert", "Die markierte Passage wurde ersetzt und gespeichert.");
    closeAnswerSelection();
  }


  /** Nachcheck ergab "nicht gestützt": Satz + zugehörige Zitat-Brackets aus dem
   *  gespeicherten Antworttext entfernen. */
  function removeStatementFromBlock(blockId: string, statement: string) {
    const base = statementPattern(cleanAnswerQuote(statement));
    if (!base) {
      return;
    }
    const withTail = new RegExp(`${base.source}(?:\\s*\\[[^\\]]+\\])*[.!?]?\\s*`);
    let removed = false;
    setHistory((current) =>
      current.map((turn) => {
        const blocks = turnBlocks(turn);
        if (!blocks.some((block) => block.id === blockId)) {
          return turn;
        }
        const nextBlocks = blocks.map((block) => {
          if (block.id !== blockId || !withTail.test(block.answer.answer)) {
            return block;
          }
          removed = true;
          const nextAnswer = block.answer.answer.replace(withTail, " ").replace(/ {2,}/g, " ").trim();
          return { ...block, answer: { ...block.answer, answer: nextAnswer } };
        });
        const last = nextBlocks[nextBlocks.length - 1];
        return {
          ...turn,
          blocks: nextBlocks,
          answer: last ? last.answer : turn.answer,
          verification: mergeVerification(nextBlocks.flatMap((block) => block.verification))
        };
      })
    );
    logAction(
      "Aussage entfernt",
      removed ? "Die nicht gestützte Aussage wurde aus der Antwort entfernt." : "Aussage im Antworttext nicht gefunden.",
      removed ? "ok" : "error"
    );
  }

  /** Nachcheck "nicht gestützt": nur DIESES Zitat aus dem Satz entfernen. Belegt danach
   *  keine andere Quelle den Satz mehr, wird der ganze Satz entfernt. */
  function removeCitationFromBlock(blockId: string, paperId: string, statement: string) {
    const base = statementPattern(cleanAnswerQuote(statement));
    if (!base) {
      return;
    }
    // Satz + unmittelbar folgender Zitat-Cluster ([a][b] …).
    const combined = new RegExp(`(${base.source})((?:\\s*\\[[^\\]]+\\])+)`);
    // Objekt-Halter, damit TS die in der setHistory-Callback gesetzten Werte nicht auf das
    // Literal "none" verengt.
    const outcome: { value: "citation" | "statement" | "none" } = { value: "none" };
    setHistory((current) =>
      current.map((turn) => {
        const blocks = turnBlocks(turn);
        if (!blocks.some((block) => block.id === blockId)) {
          return turn;
        }
        const nextBlocks = blocks.map((block) => {
          if (block.id !== blockId) {
            return block;
          }
          const answerText = block.answer.answer;
          const match = combined.exec(answerText);
          if (!match) {
            return block;
          }
          const brackets = match[2].match(/\[[^\]]+\]/g) ?? [];
          // Innerhalb jedes Brackets nur die Referenz auf dieses Paper streichen; ein
          // Bracket, das dadurch leer wird, fällt weg.
          const rebuilt = brackets.map((bracket) => {
            const tokens = bracket.slice(1, -1).split(",").map((token) => token.trim()).filter(Boolean);
            const kept = tokens.filter((token) => !token.includes(paperId));
            if (kept.length === tokens.length) {
              return bracket;
            }
            return kept.length ? `[${kept.join(", ")}]` : "";
          });
          if (rebuilt.join("") === brackets.join("")) {
            return block; // Paper steckt in keinem Bracket dieses Satzes.
          }
          const keptBrackets = rebuilt.filter(Boolean);
          let nextAnswer: string;
          if (keptBrackets.length > 0) {
            outcome.value = "citation";
            nextAnswer = answerText.replace(combined, `$1${keptBrackets.join("")}`);
          } else {
            outcome.value = "statement";
            const withTail = new RegExp(`${base.source}(?:\\s*\\[[^\\]]+\\])*[.!?]?\\s*`);
            nextAnswer = answerText.replace(withTail, " ").replace(/ {2,}/g, " ").trim();
          }
          return { ...block, answer: { ...block.answer, answer: nextAnswer } };
        });
        const last = nextBlocks[nextBlocks.length - 1];
        return {
          ...turn,
          blocks: nextBlocks,
          answer: last ? last.answer : turn.answer,
          verification: mergeVerification(nextBlocks.flatMap((block) => block.verification))
        };
      })
    );
    if (outcome.value === "citation") {
      logAction("Zitat entfernt", "Das nicht gestützte Zitat wurde entfernt — der Satz bleibt, da ihn eine andere Quelle belegt.");
    } else if (outcome.value === "statement") {
      logAction("Aussage entfernt", "Der Satz hatte nur dieses (nicht gestützte) Zitat und wurde entfernt.", "ok");
    } else {
      // Kein direkt anhängendes Zitat mit dieser Paper-ID → ganzen Satz entfernen.
      removeStatementFromBlock(blockId, statement);
    }
  }

  /** Nachcheck fand die echte Belegstelle: Evidence des Zitats im Block ersetzen,
   *  damit Hover-Karte und PDF-Marker die richtige Stelle zeigen. */
  function updateCitationEvidenceInBlock(
    blockId: string,
    source: VerificationSource,
    evidenceIndex: number,
    quotes: string[],
    statement: string
  ) {
    const excerpt = quotes.join(" … ").trim();
    if (!excerpt) {
      return;
    }
    setHistory((current) =>
      current.map((turn) => {
        const blocks = turnBlocks(turn);
        if (!blocks.some((block) => block.id === blockId)) {
          return turn;
        }
        const nextBlocks = blocks.map((block) => {
          if (block.id !== blockId) {
            return block;
          }
          const nextVerification = block.verification.map((item) => {
            if (item.paper_id !== source.paper_id) {
              return item;
            }
            const evidence = item.evidence.map((entry, index) =>
              index === evidenceIndex
                ? { ...entry, pdf_excerpt: excerpt, reference_text: cleanAnswerQuote(statement) || entry.reference_text }
                : entry
            );
            return { ...item, evidence };
          });
          return { ...block, verification: nextVerification };
        });
        return {
          ...turn,
          blocks: nextBlocks,
          verification: mergeVerification(nextBlocks.flatMap((block) => block.verification))
        };
      })
    );
    clearCitationApproximateInBlock(blockId, source.paper_id, evidenceIndex);
    logAction("Zitat aktualisiert", "Die verifizierte Textstelle ist jetzt der Beleg dieses Zitats.");
  }

  /** Markiert die Zuordnung eines Zitats als geprüft (approximate=false), damit der
   *  Auto-Nachcheck sie nach einem Reload nicht erneut prüft/umformuliert. */
  function clearCitationApproximateInBlock(blockId: string, paperId: string, evidenceIndex: number) {
    setHistory((current) =>
      current.map((turn) => {
        const blocks = turnBlocks(turn);
        if (!blocks.some((block) => block.id === blockId)) {
          return turn;
        }
        const nextBlocks = blocks.map((block) => {
          if (block.id !== blockId) {
            return block;
          }
          const links = block.answer.citation_links;
          if (!links?.length) {
            return block;
          }
          let changed = false;
          const nextLinks = links.map((link) => {
            if (
              link.approximate &&
              link.paper_id === paperId &&
              (link.evidence_index == null || link.evidence_index === evidenceIndex)
            ) {
              changed = true;
              return { ...link, approximate: false };
            }
            return link;
          });
          return changed ? { ...block, answer: { ...block.answer, citation_links: nextLinks } } : block;
        });
        const last = nextBlocks[nextBlocks.length - 1];
        return { ...turn, blocks: nextBlocks, answer: last ? last.answer : turn.answer };
      })
    );
  }

  // ---- Vorab-Nachcheck unsicherer Zuordnungen (läuft, bevor die Antwort angezeigt wird) ----
  // Reine String/Datentransformationen, damit sie auf die noch nicht in der History
  // liegende, frisch generierte Antwort angewendet werden können.





  /** "teilweise gestützt": Aussage per LLM auf das reduzieren, was die Quelle belegt. */
  async function reformulateForSource(
    source: VerificationSource,
    evidenceIndex: number,
    statement: string,
    result: ClaimCheckResult
  ): Promise<string> {
    const evidence = source.evidence[evidenceIndex];
    const support =
      (result.supporting_quotes ?? []).join(" … ").trim() ||
      (result.excerpts ?? []).slice(0, 2).join(" … ").trim() ||
      evidence?.pdf_excerpt ||
      evidence?.reference_text ||
      "";
    if (!support) {
      return "";
    }
    try {
      const rewrite = await api.rewriteNote({
        text: `Aussage: ${cleanAnswerQuote(statement)}\n\nWas die Quelle [${source.paper_id}] tatsächlich belegt: ${support}`,
        instruction:
          "Die Aussage wird von ihrer Quelle nur teilweise gestützt. Formuliere sie so um, " +
          "dass sie ausschließlich das aussagt, was die Quelle wirklich belegt — nicht mehr. " +
          "Kurz, sachlich, auf Deutsch. Gib nur den korrigierten Satz aus, ohne Zitatmarker, " +
          "ohne Anführungszeichen, ohne Kommentar.",
        provider: provider || undefined,
        model: model || undefined
      });
      return (rewrite.text || "").trim();
    } catch {
      return "";
    }
  }

  /** Prüft alle als unsicher (approximate) markierten Zuordnungen der frisch generierten
   *  Antwort — VOR dem Anzeigen — und korrigiert Antworttext/Zitate entsprechend:
   *  nicht gestützt → Zitat/Aussage raus, teilweise → umformuliert, gestützt → Beleg fix.
   *  So sieht der Nutzer direkt die bereinigte Antwort statt nachträglicher Änderungen. */
  async function verifyUncertainCitations(
    payload: Answer,
    sources: VerificationSource[]
  ): Promise<{ payload: Answer; sources: VerificationSource[] }> {
    const links = payload.citation_links ?? [];
    // Kein Early-Return auf link.approximate: citationMetasFor markiert inzwischen auch
    // verification-unsichere Belege (nicht im PDF verortet) als approximate — die Items-
    // Sammlung unten entscheidet, ob es etwas zu prüfen gibt.
    if (!sources.length) {
      return { payload, sources };
    }
    const parts = payload.answer.split(/(\[[^\]]+\])/g);
    const seen = new Set<string>();
    const items: { paperId: string; source: VerificationSource; evidenceIndex: number; statement: string }[] = [];
    for (let index = 0; index < parts.length; index += 1) {
      const bracket = /^\[([^\]]+)\]$/.exec(parts[index]);
      if (!bracket) {
        continue;
      }
      const start = parts.slice(0, index).reduce((sum, item) => sum + item.length, 0);
      const raw = citationMetasFor(sources, bracket[1], citationContext(parts, index), links, start);
      const metas = Array.isArray(raw) ? raw : raw ? [raw] : [];
      const segment = citationSegmentFromParts(parts, index);
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
        items.push({ paperId: meta.source.paper_id, source: meta.source, evidenceIndex: meta.evidenceIndex, statement });
      }
    }
    if (!items.length) {
      return { payload, sources };
    }

    let answerText = payload.answer;
    let nextLinks = links.map((link) => ({ ...link }));
    let nextSources = sources.map((source) => ({ ...source, evidence: source.evidence.map((entry) => ({ ...entry })) }));

    for (const item of items.slice(0, MAX_PREVERIFY_CITATIONS)) {
      const evidence = item.source.evidence[item.evidenceIndex];
      // Bei claim_excerpt/approx_region ist reference_text der eigene Antwortsatz —
      // als "Quellen-Auszug" an den Judge gefüttert würde er die Prüfung zirkulär
      // Richtung "supported" biasen. Dann lieber gar kein Auszug (der Checker zieht
      // sich PDF/Abstract selbst).
      const evidenceMeta = (evidence?.metadata ?? {}) as Record<string, unknown>;
      const selfReferential =
        evidenceMeta.context_policy === "claim_excerpt" || evidenceMeta.context_policy === "approx_region";
      let check: ClaimCheckResult | undefined;
      try {
        const res = await api.claimCheck({
          statement: item.statement,
          paper_ids: [item.paperId],
          titles: { [item.paperId]: item.source.title || "" },
          evidence_texts: { [item.paperId]: evidence?.pdf_excerpt || (selfReferential ? "" : evidence?.reference_text || "") },
          provider: provider || undefined,
          model: model || undefined
        });
        check = res.checks[0];
      } catch {
        continue; // fail-soft: Zuordnung bleibt (mit Warnzeichen) manuell prüfbar
      }
      if (!check) {
        continue;
      }
      if (check.verdict === "not_supported") {
        const stripped = stripCitationFromAnswerText(answerText, item.paperId, item.statement);
        answerText = stripped.text;
        nextLinks = clearApproximateOnLinks(nextLinks, item.paperId, item.evidenceIndex);
        logAction(
          "Zitat vorab geprüft",
          stripped.outcome === "statement"
            ? `Nicht gestützte Aussage entfernt ([${item.paperId}]).`
            : `Nicht gestütztes Zitat [${item.paperId}] entfernt.`,
          "ok"
        );
      } else if (check.verdict === "partially_supported") {
        const replacement = await reformulateForSource(item.source, item.evidenceIndex, item.statement, check);
        if (replacement) {
          answerText = replaceStatementInAnswerText(answerText, item.statement, replacement);
        }
        nextLinks = clearApproximateOnLinks(nextLinks, item.paperId, item.evidenceIndex);
        logAction("Zitat vorab geprüft", `Aussage an Quelle [${item.paperId}] angepasst (nur teilweise gestützt).`, "ok");
      } else if (check.verdict === "supported") {
        if (check.supporting_quotes.length) {
          nextSources = updateEvidenceInSources(nextSources, item.paperId, item.evidenceIndex, check.supporting_quotes, item.statement);
        }
        nextLinks = clearApproximateOnLinks(nextLinks, item.paperId, item.evidenceIndex);
      }
      // insufficient_evidence: Warnzeichen bleibt — bleibt manuell nachprüfbar.
    }
    return { payload: { ...payload, answer: answerText, citation_links: nextLinks }, sources: nextSources };
  }

  function insertCitationFromAnswer(source: VerificationSource, evidenceIndex: number, quote = "", extras?: CitationInsertExtras) {
    const payload = citationInsertPayload(source, evidenceIndex, quote, extras);
    if (!payload) {
      return;
    }
    notesActionsRef.current?.clearInsertPreview();
    void appendToActiveNote(payload.markdown, payload.citations);
  }

  function previewCitationFromAnswer(source: VerificationSource, evidenceIndex: number, quote = "", extras?: CitationInsertExtras) {
    const payload = citationInsertPayload(source, evidenceIndex, quote, extras);
    if (!payload) {
      return;
    }
    notesActionsRef.current?.previewAppendMarkdown(payload.markdown);
  }

  function toggleScopedPaper(paperId: string) {
    setSelectedPaperIds((current) => (current.includes(paperId) ? current.filter((item) => item !== paperId) : [...current, paperId]));
  }

  function toggleScopedGrey(greyId: string) {
    const wasSelected = selectedGreyIds.includes(greyId);
    setSelectedGreyIds((current) => (wasSelected ? current.filter((item) => item !== greyId) : [...current, greyId]));
    // Eine Notiz-/Analyse-Quelle bringt die Paper mit, auf denen sie beruht: beim Anhaken
    // kommen sie in die Auswahl, beim Abwaehlen wieder heraus — es sei denn, eine andere
    // ausgewaehlte Quelle beansprucht sie ebenfalls.
    const derived = (greySources.find((item) => item.id === greyId)?.source_paper_ids ?? []).filter(Boolean);
    if (!derived.length) {
      return;
    }
    if (!wasSelected) {
      setSelectedPaperIds((papers) => Array.from(new Set([...papers, ...derived])));
      return;
    }
    const stillClaimed = new Set(
      greySources
        .filter((item) => item.id !== greyId && selectedGreyIds.includes(item.id))
        .flatMap((item) => item.source_paper_ids ?? [])
    );
    setSelectedPaperIds((papers) => papers.filter((paperId) => !derived.includes(paperId) || stillClaimed.has(paperId)));
  }

  async function appendToActiveNote(markdown: string, citations: Record<string, unknown>[] = []) {
    const content = markdown.trim();
    if (!content) {
      return;
    }
    setNoteStatus("Speichert");
    try {
      const insertedNoteId = await notesActionsRef.current?.insertMarkdownAtCursor(content, citations);
      if (insertedNoteId) {
        setControlledNoteId(insertedNoteId);
        setNoteStatus("In Notiz gespeichert");
        return;
      }
      let noteId = notesSnapshot.activeNoteId || controlledNoteId;
      if (!noteId) {
        const created = await api.createNote(scopedProjectId, { title: "Neue Notiz", markdown: "# Neue Notiz\n\n" });
        noteId = created.note.id;
        setControlledNoteId(noteId);
        queryClient.setQueryData(["note", noteId], { note: created.note });
      }
      const result = await api.appendNote(noteId, { markdown: content, citations });
      setControlledNoteId(result.note.id);
      queryClient.setQueryData(["note", result.note.id], { note: result.note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      queryClient.invalidateQueries({ queryKey: ["note", result.note.id] });
      setNoteStatus("In Notiz gespeichert");
    } catch (error) {
      setNoteStatus(error instanceof Error ? error.message : "Speichern fehlgeschlagen");
    }
  }

  function appendAnswerToNote() {
    if (!answer) {
      return;
    }
    const formatted = formatAnswerForNote(answer, verification);
    notesActionsRef.current?.clearInsertPreview();
    void appendToActiveNote(formatted.markdown, formatted.citations);
  }

  function appendActiveQuote(sourceKind: "reference" | "pdf") {
    if (!selectedSource || !activeEvidence) {
      return;
    }
    const quote = sourceKind === "pdf" ? activeEvidence.pdf_excerpt || activeEvidence.reference_text : meaningfulQuote(activeAnswerQuote) || activeEvidence.reference_text;
    const citation = noteCitation(selectedSource, activeEvidence, activeEvidenceIndex, quote);
    notesActionsRef.current?.clearInsertPreview();
    void appendToActiveNote(formatNoteQuote(quote, selectedSource, activeEvidenceIndex, citation.id), [citation]);
  }

  function previewAnswerToNote() {
    if (!answer) {
      return;
    }
    notesActionsRef.current?.previewAppendMarkdown(formatAnswerForNote(answer, verification).markdown);
  }

  function previewActiveQuote(sourceKind: "reference" | "pdf") {
    if (!selectedSource || !activeEvidence) {
      return;
    }
    const quote = sourceKind === "pdf" ? activeEvidence.pdf_excerpt || activeEvidence.reference_text : meaningfulQuote(activeAnswerQuote) || activeEvidence.reference_text;
    const citation = noteCitation(selectedSource, activeEvidence, activeEvidenceIndex, quote);
    notesActionsRef.current?.previewAppendMarkdown(formatNoteQuote(quote, selectedSource, activeEvidenceIndex, citation.id));
  }

  function deleteAssistantTurn(turnId: string) {
    const removed = history.find((item) => item.id === turnId);
    // Also drop the server-persisted session so it doesn't linger orphaned.
    if (removed?.type === "research_tree") {
      void api.deleteResearchSession(turnId).catch(() => {});
    } else if (removed?.type === "parallel") {
      void api.deleteParallelSession(turnId).catch(() => {});
      if (parallelSessionIdRef.current === turnId) {
        parallelSessionIdRef.current = "";
        setParallelSession(null);
      }
    }
    setHistory((current) => {
      const next = current.filter((item) => item.id !== turnId);
      // Ausdrueckliches Loeschen darf als einziger Pfad eine leere History speichern.
      saveAssistantSession(
        scopedProjectId,
        { history: next, activeTurnId: activeTurnId === turnId ? (next[0]?.id ?? "") : activeTurnId },
        { allowEmpty: true }
      );
      return next;
    });
    if (activeTurnId === turnId) {
      setActiveTurnId((prev) => {
        const idx = history.findIndex((item) => item.id === turnId);
        return history[idx - 1]?.id ?? history[idx + 1]?.id ?? "";
      });
    }
  }

  function activateAssistantTurn(turnId: string) {
    setActiveTurnId(turnId);
    const turn = history.find((item) => item.id === turnId);
    if (!turn) return;
    if (turn.type === "research_tree") {
      researchSessionIdRef.current = turnId;
      researchNodesRef.current = turn.researchNodes ?? [];
      setResearchNodes(turn.researchNodes ?? []);
      setParallelMode(false);
      setDeepMode(true);
      if (!(turn.researchNodes ?? []).length) hydrateResearchSessionFromServer(turnId);
      return;
    }
    if (turn.type === "parallel") {
      parallelSessionIdRef.current = turnId;
      setDeepMode(false);
      setParallelMode(true);
      setParallelSession(null);
      hydrateParallelSessionFromServer(turnId);
      return;
    }
    setParallelMode(false);
    setDeepMode(false);
    const sources = mergeVerification(turnBlocks(turn).flatMap((block) => block.verification));
    openAssistantSource(sources[0] ?? null, 0);
  }

  // Flüssiges Spalten-Resize wie in der Werkstatt: während des Drags wird das Grid
  // direkt am DOM aktualisiert (kein React-Rerender pro Frame), der Clamp ist dynamisch
  // gegen die Containerbreite statt eines festen Pixel-Maximums; State erst bei pointerup.
  function startColumnResize(event: ReactPointerEvent<HTMLDivElement>, column: "nav" | "pdf" | "assistant") {
    event.preventDefault();
    const page = pageRef.current;
    if (!page) return;
    try {
      event.currentTarget.setPointerCapture(event.pointerId);
    } catch {
      // Pointer capture ist optional; window-Listener übernehmen ohnehin.
    }
    const startX = event.clientX;
    const widths = {
      nav: navigatorOpen ? navigatorWidth : 46,
      pdf: centerView !== "pdf" || pdfOpen ? pdfWidth : 46,
      assistant: assistantOpen ? assistantWidth : 46,
    };
    const startWidth = widths[column];
    const minNotes = notesOpen ? 80 : 46;
    const othersTotal = (Object.keys(widths) as Array<keyof typeof widths>)
      .filter((key) => key !== column)
      .reduce((sum, key) => sum + widths[key], 0);
    const min = 110;
    const max = Math.max(min, page.clientWidth - 3 * 6 - othersTotal - minNotes);
    const notesTemplate = notesOpen ? "minmax(80px, 1fr)" : "46px";
    let next = startWidth;
    let frame: number | null = null;
    const apply = () => {
      frame = null;
      const cols = { ...widths, [column]: next };
      page.style.gridTemplateColumns = `${cols.nav}px 6px ${cols.pdf}px 6px ${cols.assistant}px 6px ${notesTemplate}`;
    };
    const move = (moveEvent: globalThis.PointerEvent) => {
      next = Math.min(max, Math.max(min, startWidth + moveEvent.clientX - startX));
      if (frame === null) frame = window.requestAnimationFrame(apply);
    };
    const setter = column === "nav" ? setNavigatorWidth : column === "pdf" ? setPdfWidth : setAssistantWidth;
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      if (frame !== null) window.cancelAnimationFrame(frame);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
      setter(next);
    };
    document.body.style.cursor = "col-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function startVerticalResize(
    event: ReactPointerEvent<HTMLDivElement>,
    value: number,
    setter: (value: number) => void,
    frameRef: MutableRefObject<number | null>,
    min: number
  ) {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = value;
    // Dynamischer Deckel statt fester Pixel: Liste darf fast die ganze Pane-Höhe einnehmen.
    const max = Math.max(min, window.innerHeight - 240);
    const move = (moveEvent: globalThis.PointerEvent) => {
      const next = Math.min(max, Math.max(min, startHeight + moveEvent.clientY - startY));
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        setter(next);
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      document.body.style.cursor = "";
      document.body.style.userSelect = "";
    };
    document.body.style.cursor = "row-resize";
    document.body.style.userSelect = "none";
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  const visibleNotes = useMemo(() => {
    const query = normalizeFilter(navigatorQuery);
    if (!query) {
      return notesSnapshot.notes;
    }
    return notesSnapshot.notes.filter((note) => normalizeFilter(`${note.title} ${note.excerpt ?? ""}`).includes(query));
  }, [navigatorQuery, notesSnapshot.notes]);

  const pdfPapers = papersQuery.data?.items ?? [];
  // Befehlspalette: sichtbar solange der erste Token noch getippt wird ("/su…").
  const paletteQuery = /^\/\S*$/.test(question) ? question.slice(1) : null;
  const paletteCandidates = useMemo(() => (paletteQuery !== null ? matchWorkspaceCommands(paletteQuery) : []), [paletteQuery]);
  const activeCommandHint = useMemo(() => {
    const match = question.match(/^\/([\wäöüß]+)\s/i);
    if (!match) {
      return null;
    }
    const name = match[1].toLowerCase();
    return WORKSPACE_COMMANDS.find((command) => command.name === name || (command.aliases ?? []).includes(name)) ?? null;
  }, [question]);
  useEffect(() => setPaletteIndex(0), [paletteQuery]);
  const latestAnswerNeedsWeb = useMemo(
    () => Boolean(latestBlock && answerSuggestsWebSearch(latestBlock.answer)),
    [latestBlock]
  );
  const mentionCandidates = useMemo(() => {
    if (!mentionState) {
      return [];
    }
    const needle = normalizeFilter(mentionState.query);
    const pool = needle
      ? pdfPapers.filter((paper) => {
          const normalized = normalizeWorkspacePaper(paper);
          const haystack = normalizeFilter(`${workspacePaperTitle(normalized)} ${workspacePaperId(normalized)}`);
          return haystack.includes(needle);
        })
      : pdfPapers;
    return pool.slice(0, 8);
  }, [mentionState, pdfPapers]);
  const pdfView = pdfProps(pdfTarget);
  // When no local PDF is available, resolve the cited paper id so the PDF pane can show
  // its abstract + a link to the original source. Grey sources render elsewhere.
  // True while we are downloading a cited PDF on demand: suppress the abstract-metadata
  // fallback and show a loading hint instead of the "Kein PDF verfügbar" abstract limbo.
  const isIngestingCurrentPdf =
    !!ingestingPaperId && pdfTarget?.kind === "assistant" && pdfTarget.source.paper_id === ingestingPaperId;
  const pdfMetaPaperId = (() => {
    if (!pdfTarget || isIngestingCurrentPdf) return undefined;
    if (pdfTarget.kind === "paper") return workspacePaperId(pdfTarget.paper) || undefined;
    if (pdfTarget.kind === "assistant") return isGreySourcePaperId(pdfTarget.source.paper_id) ? undefined : pdfTarget.source.paper_id;
    if (pdfTarget.kind === "noteCitation") return isGreySourcePaperId(pdfTarget.citation.paper_id) ? undefined : pdfTarget.citation.paper_id;
    if (pdfTarget.kind === "missing") return pdfTarget.paperId || undefined;
    return undefined;
  })();
  const pdfUnavailableMessage = isIngestingCurrentPdf
    ? "Quelle wird heruntergeladen und extrahiert …"
    : pdfTarget?.kind === "missing"
      ? "Diese Quelle wurde im Antworttext zitiert, ist aber nicht als PDF im Projekt vorhanden. Sie können das Paper importieren oder als Graue Quelle hinzufügen."
      : undefined;
  // Wunschbreiten (aus Drag/localStorage) → effektive Breiten, die bei zu schmalem
  // Container proportional schrumpfen, sodass nav+pdf+assistant+Lücken+Notizen-Minimum
  // in die Containerbreite passen. Verhindert das Abschneiden der rechten Pane.
  const pdfExpanded = centerView !== "pdf" || pdfOpen;
  const effective = useMemo(() => {
    const navW = navigatorOpen ? navigatorWidth : 46;
    const pdfW = pdfExpanded ? pdfWidth : 46;
    const assistW = assistantOpen ? assistantWidth : 46;
    if (containerWidth <= 0) return { nav: navW, pdf: pdfW, assistant: assistW };
    const minNotes = notesOpen ? 80 : 46;
    const needed = navW + pdfW + assistW + 3 * 6 + minNotes;
    if (needed <= containerWidth) return { nav: navW, pdf: pdfW, assistant: assistW };
    const MIN_PANE = 90;
    const shrinkable = [
      { key: "nav" as const, open: navigatorOpen, w: navW },
      { key: "pdf" as const, open: pdfExpanded, w: pdfW },
      { key: "assistant" as const, open: assistantOpen, w: assistW },
    ].filter((item) => item.open && item.w > MIN_PANE);
    const headroom = shrinkable.reduce((sum, item) => sum + (item.w - MIN_PANE), 0);
    const result = { nav: navW, pdf: pdfW, assistant: assistW };
    if (headroom > 0) {
      const ratio = Math.min(1, (needed - containerWidth) / headroom);
      for (const item of shrinkable) {
        result[item.key] = Math.round(item.w - (item.w - MIN_PANE) * ratio);
      }
    }
    return result;
  }, [containerWidth, navigatorOpen, pdfExpanded, assistantOpen, notesOpen, navigatorWidth, pdfWidth, assistantWidth]);

  const navColumn = navigatorOpen ? `${effective.nav}px` : "46px";
  const assistantColumn = assistantOpen ? `${effective.assistant}px` : "46px";
  const pdfColumn = pdfExpanded ? `${effective.pdf}px` : "46px";
  const notesColumn = notesOpen ? "minmax(80px, 1fr)" : "46px";

  return (
    <section
      ref={pageRef}
      className="workspace-page"
      style={{
        gridTemplateColumns: `${navColumn} 6px ${pdfColumn} 6px ${assistantColumn} 6px ${notesColumn}`
      }}
    >
      {navigatorOpen ? (
        <aside className="workspace-nav-pane">
          <PaneHeading eyebrow={scopeLabel} title="Arbeitsplatz" onCollapse={() => setNavigatorOpen(false)} collapseSide="left" />
          <div className="segmented workspace-nav-tabs" aria-label="Arbeitsplatz Navigation">
            <button type="button" className={centerView === "pdf" && navigatorTab === "notes" ? "active" : ""} onClick={() => { setNavigatorTab("notes"); setCenterView("pdf"); }}>
              <NotebookPen size={15} />
              <span>Notizen</span>
              <strong>{notesSnapshot.notes.length}</strong>
            </button>
            <button type="button" className={centerView === "pdf" && navigatorTab === "pdfs" ? "active" : ""} onClick={() => { setNavigatorTab("pdfs"); setCenterView("pdf"); }}>
              <FileText size={15} />
              <span>PDFs</span>
              <strong>{notesSnapshot.citations.length + pdfPapers.length}</strong>
            </button>
            <button type="button" className={`workspace-nav-tab--wide ${centerView === "pdf" && navigatorTab === "assistantSessions" ? "active" : ""}`} onClick={() => { setNavigatorTab("assistantSessions"); setCenterView("pdf"); }}>
              <MessageSquareText size={15} />
              <span>KI-Sessions</span>
              <strong>{history.length}</strong>
            </button>
            <button type="button" className={centerView === "analysis" ? "active" : ""} title="Analyse-Werkstatt: KI schreibt + führt Analyse-Skripte aus (reproduzierbar)" onClick={() => setCenterView("analysis")}>
              <FlaskConical size={15} />
              <span>Analyse</span>
            </button>
            <button type="button" className={centerView === "datasets" ? "active" : ""} title="Datensätze aus freien Registries suchen und sammeln" onClick={() => setCenterView("datasets")}>
              <Database size={15} />
              <span>Daten</span>
            </button>
          </div>
          {sessionLoadFailed ? (
            <div className="status-strip status-strip--error">
              <strong>Sitzung nicht geladen</strong>
              <span>Backend nicht erreichbar — es wird nichts gespeichert, bis die Verbindung steht.</span>
            </div>
          ) : null}
          <WorkspaceNavigatorBody
            tab={navigatorTab}
            query={navigatorQuery}
            setQuery={setNavigatorQuery}
            notes={visibleNotes}
            notesLoading={notesSnapshot.notesLoading}
            activeNoteId={notesSnapshot.activeNoteId}
            citations={notesSnapshot.citationRows}
            selectedCitation={notesSnapshot.selectedCitation}
            papers={pdfPapers}
            papersLoading={papersQuery.isLoading}
            greySources={greySources}
            primaryPaperId={primaryPaperId}
            pdfTarget={pdfTarget}
            activeAssistantSource={selectedSource}
            activeAssistantEvidenceIndex={activeEvidenceIndex}
            selectedPaperIds={selectedPaperIds}
            pdfCitationListHeight={pdfCitationListHeight}
            sessions={history}
            activeSessionId={activeTurnId}
            sessionProjectId={scopedProjectId}
            onSessionRestored={() => setSessionReloadNonce((current) => current + 1)}
            onCreateNote={() => notesActionsRef.current?.createNote()}
            onSelectNote={(noteId) => {
              setControlledNoteId(noteId);
              notesActionsRef.current?.selectNote(noteId);
              setNavigatorTab("notes");
            }}
            onOpenCitation={(citation) => {
              if (notesActionsRef.current) {
                setRequestedCitationId("");
                notesActionsRef.current.openCitation(citation);
              } else {
                setRequestedCitationId(citation.id);
              }
              setPdfTarget({ kind: "noteCitation", citation });
              setPdfOpen(true);
            }}
            onOpenPaper={(paper) => {
              setPdfTarget({ kind: "paper", paper: normalizeWorkspacePaper(paper) });
              setPdfOpen(true);
            }}
            onOpenGrey={openGreySource}
            onToggleScopedPaper={toggleScopedPaper}
            selectedGreyIds={selectedGreyIds}
            onToggleScopedGrey={toggleScopedGrey}
            greySourceListHeight={greySourceListHeight}
            onResizeCitationList={(event) => startVerticalResize(event, pdfCitationListHeight, setPdfCitationListHeight, pdfCitationResizeFrameRef, 90)}
            onResizeGreyList={(event) => startVerticalResize(event, greySourceListHeight, setGreySourceListHeight, greySourceResizeFrameRef, 80)}
            onOpenAssistantPdf={openSelectedAssistantPdf}
            onActivateSession={activateAssistantTurn}
            onDeleteSession={deleteAssistantTurn}
            onDeleteNote={(noteId) => notesActionsRef.current?.deleteNote(noteId)}
            isRealProject={isRealProject}
            onDeletePaper={(paperId) => removePaperMutation.mutate(paperId)}
            onDeleteGrey={(greyId) => deleteGreyMutation.mutate(greyId)}
            onSetPrimary={(paperId) => setPrimaryMutation.mutate(paperId)}
            onDeleteCitation={(citation) => {
              const noteId = notesSnapshot.activeNoteId || controlledNoteId;
              if (noteId) {
                deleteCitationMutation.mutate({ noteId, citationId: citation.id });
              }
            }}
          />
        </aside>
      ) : (
        <CollapsedPane label="Navigator" icon={<PanelLeftOpen size={17} />} onOpen={() => setNavigatorOpen(true)} />
      )}
      <div
        className={`split-handle ${navigatorOpen ? "" : "split-handle--idle"}`}
        role="separator"
        aria-label="Navigator Breite anpassen"
        onPointerDown={navigatorOpen ? (event) => startColumnResize(event, "nav") : undefined}
      />

      {centerView === "analysis" ? (
        <AnalysisPanel
          projectId={scopedProjectId}
          provider={provider}
          model={model}
          paperIds={selectedPaperIds}
          onCollapse={() => setCenterView("pdf")}
        />
      ) : centerView === "datasets" ? (
        <DatasetsPanel projectId={scopedProjectId} onCollapse={() => setCenterView("pdf")} />
      ) : pdfOpen ? (
        pdfTarget?.kind === "grey" ? (
          <GreySourceView
            source={pdfTarget.source}
            onCollapse={() => setPdfOpen(false)}
            onInsert={(text) => appendGreyQuote(text, pdfTarget.source)}
            onInsertPreview={(text) => previewGreyQuote(text, pdfTarget.source)}
            onInsertPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
          />
        ) : (
          <PdfPane
            url={pdfView.url}
            title={pdfView.title}
            metaPaperId={pdfMetaPaperId}
            unavailableMessage={pdfUnavailableMessage}
            evidences={pdfView.evidences}
            activeEvidenceIndex={pdfView.activeEvidenceIndex}
            onActiveEvidenceChange={pdfView.onActiveEvidenceChange}
            onCollapse={() => setPdfOpen(false)}
          />
        )
      ) : (
        <CollapsedPane label="PDF" icon={<PanelRightOpen size={17} />} onOpen={() => setPdfOpen(true)} />
      )}
      <div
        className={`split-handle ${pdfOpen ? "" : "split-handle--idle"}`}
        role="separator"
        aria-label="PDF Breite anpassen"
        onPointerDown={pdfOpen ? (event) => startColumnResize(event, "pdf") : undefined}
      />

      {assistantOpen ? (
        <section className={`workspace-assistant-pane ${assistantMode === "notes" ? "workspace-assistant-pane--notes" : ""}`}>
          <PaneHeading
            title="Assistant"
            onCollapse={() => setAssistantOpen(false)}
            collapseSide="left"
            status={answerMutation.isPending || citationVerifyPending ? "running" : answer?.generation_error ? "warning" : "idle"}
            actions={
              <div className="segmented workspace-assistant-mode-toggle" aria-label="Assistant-Modus">
                <button
                  type="button"
                  className={assistantMode === "pdf" ? "active" : ""}
                  title="PDF-Assistent: Fragen zu lokalen PDFs"
                  onClick={() => {
                    notesActionsRef.current?.clearInsertPreview();
                    setAssistantMode("pdf");
                  }}
                >
                  <FileText size={15} />
                  <span>PDF</span>
                </button>
                <button
                  type="button"
                  className={assistantMode === "notes" ? "active" : ""}
                  title="Notiz-Assistent: KI-Hilfe zu deinen Notizen"
                  onClick={() => {
                    notesActionsRef.current?.clearInsertPreview();
                    setAssistantMode("notes");
                  }}
                >
                  <Sparkles size={15} />
                  <span>Notizen</span>
                </button>
              </div>
            }
          />
          {assistantMode === "notes" ? (
            <WorkspaceNotesAssistant
              threads={notesSnapshot.threads}
              activeThreadId={notesSnapshot.activeThreadId}
              focusedThreadId={focusedNoteThreadId}
              threadMeta={notesSnapshot.threadMeta}
              followUpDrafts={notesSnapshot.followUpDrafts}
              isFollowUpPending={notesSnapshot.isFollowUpPending}
              deletingThreadId={notesSnapshot.deletingThreadId}
              onActivateThread={openThreadInNotesAssistant}
              onFocusThread={(threadId) => {
                setFocusedNoteThreadId(threadId);
                notesActionsRef.current?.locateThread(threadId);
                const thread = notesSnapshot.threads.find((item) => item.id === threadId);
                for (const item of notesSnapshot.threads) {
                  if (item.id !== threadId && !threadCollapsed(item)) {
                    notesActionsRef.current?.toggleThreadCollapsed(item.id);
                  }
                }
                if (thread && threadCollapsed(thread)) {
                  notesActionsRef.current?.toggleThreadCollapsed(threadId);
                }
              }}
              onClearFocus={() => setFocusedNoteThreadId("")}
              onToggleThread={(threadId) => notesActionsRef.current?.toggleThreadCollapsed(threadId)}
              onSetThreadPinned={(threadId, pinned) => notesActionsRef.current?.setThreadPinned(threadId, pinned)}
              onDraftChange={(threadId, value) => notesActionsRef.current?.setFollowUpDraft(threadId, value)}
              onFollowUp={(threadId) => notesActionsRef.current?.submitFollowUp(threadId)}
              onInsertThread={(threadId) => notesActionsRef.current?.insertThreadAnswer(threadId)}
              onPreviewThread={(threadId) => notesActionsRef.current?.previewThreadAnswer(threadId)}
              onInsertThreadMessage={(threadId, messageId) => notesActionsRef.current?.insertThreadMessage(threadId, messageId)}
              onPreviewThreadMessage={(threadId, messageId) => notesActionsRef.current?.previewThreadMessage(threadId, messageId)}
              onHideThreadMessage={(threadId, messageId) => notesActionsRef.current?.hideThreadMessage(threadId, messageId)}
              onPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
              onDeleteThread={(threadId) => {
                if (focusedNoteThreadId === threadId) {
                  setFocusedNoteThreadId("");
                }
                notesActionsRef.current?.deleteThread(threadId);
              }}
              onDeleteAllThreads={() => notesActionsRef.current?.deleteAllThreads()}
            />
          ) : (
            <WorkspaceAssistantPane
              {...{
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
              }}
            />
          )}
        </section>
      ) : (
        <CollapsedPane label="Assistant" icon={<PanelLeftOpen size={17} />} onOpen={() => setAssistantOpen(true)} />
      )}
      <div
        className={`split-handle ${assistantOpen ? "" : "split-handle--idle"}`}
        role="separator"
        aria-label="Assistant Breite anpassen"
        onPointerDown={assistantOpen ? (event) => startColumnResize(event, "assistant") : undefined}
      />

      {notesOpen ? (
        <section className="workspace-notes-pane">
          <div className="workspace-notes-topline">
            <div>
              <span>Notizen</span>
              <strong>{notesTab === "results" ? "Ergebnisse" : (notesSnapshot.title || "Keine Notiz gewaehlt")}</strong>
            </div>
            <div className="workspace-notes-topline__actions">
              {parallelMode && parallelSession ? (
                <div className="segmented workspace-notes-tabs">
                  <button type="button" className={notesTab === "note" ? "active" : ""} onClick={() => setNotesTab("note")}>
                    Notiz
                  </button>
                  <button type="button" className={notesTab === "results" ? "active" : ""} onClick={() => setNotesTab("results")}>
                    Ergebnisse
                  </button>
                </div>
              ) : null}
              <button className="icon-button" type="button" aria-label="Notizen einklappen" onClick={() => setNotesOpen(false)}>
                <PanelRightClose size={17} />
              </button>
            </div>
          </div>
          {/* The note editor stays mounted (so "In Notiz übernehmen" can insert) but is hidden
              while the Parallel-Research "Ergebnisse" view is active. */}
          <div
            className="workspace-notes-body"
            style={notesTab === "results" && parallelMode && parallelSession ? { display: "none" } : undefined}
          >
            <NotesSurface
              variant="workspace"
              controlledNoteId={controlledNoteId}
              requestedCitationId={requestedCitationId}
              onActiveNoteChange={handleActiveNoteChange}
              onCitationOpen={handleNoteCitationOpen}
              onStateChange={handleNotesStateChange}
              actionsRef={notesActionsRef}
            />
          </div>
          {notesTab === "results" && parallelMode && parallelSession ? (
            <ParallelResultsTab
              session={parallelSession}
              onChange={onParallelChange}
              scope={{
                paperIds: deriveScope(paperScope).scopedPaperIds,
                provider: provider || undefined,
                model: model || undefined,
              }}
              onOpenCitation={(source, evidenceIndex) => openAssistantSource(source, evidenceIndex, "", { openPdf: true })}
              onTakeIntoNote={(md, citations) =>
                notesActionsRef.current?.insertMarkdownAtCursor(md, citations ?? []) ?? Promise.resolve(null)
              }
            />
          ) : null}
        </section>
      ) : (
        <CollapsedPane label="Notizen" icon={<PanelRightOpen size={17} />} onOpen={() => setNotesOpen(true)} />
      )}
      {showHarvestDialog ? (
        <div className="harvest-dialog-overlay">
          <div className="harvest-dialog-card">
            <strong>Automatische Quellen-Suche</strong>
            <p>
              Darf der Assistent bei Teilfragen ohne lokale Quellen automatisch nach relevanten
              Papieren und Web-Quellen suchen, diese herunterladen, extrahieren und dem Projekt hinzufügen?
            </p>
            <div className="harvest-dialog-actions">
              <button
                type="button"
                className="button button-primary button-compact"
                onClick={() => chooseHarvest(true)}
              >
                Ja, Quellen suchen
              </button>
              <button
                type="button"
                className="button button-compact"
                onClick={() => chooseHarvest(false)}
              >
                Nein, ohne Suche
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {showClarifyDialog ? (
        <ClarifyDialog
          directions={clarifyDirections}
          selected={clarifySelected}
          freetext={clarifyFreetext}
          onToggleDirection={toggleClarifyDirection}
          onFreetextChange={setClarifyFreetext}
          onFinish={finishClarify}
        />
      ) : null}
    </section>
  );

  function pdfProps(target: WorkspacePdfTarget | null): {
    url: string | null;
    title?: string;
    evidences: VerificationEvidence[];
    activeEvidenceIndex: number;
    onActiveEvidenceChange?: (index: number) => void;
  } {
    if (!target) {
      return { url: null, title: undefined, evidences: [], activeEvidenceIndex: 0 };
    }
    if (target.kind === "assistant") {
      const liveSource =
        selectedSource?.paper_id === target.source.paper_id
          ? selectedSource
          : verification.find((source) => source.paper_id === target.source.paper_id) ?? target.source;
      const preferredIndex = selectedSource?.paper_id === liveSource.paper_id ? activeEvidenceIndex : target.evidenceIndex;
      const liveIndex = Math.max(0, Math.min(preferredIndex, liveSource.evidence.length - 1));
      const pdfReady = liveSource.pdf_available || ingestedPdfIds.has(liveSource.paper_id);
      return {
        url: pdfReady ? api.paperPdfUrl(liveSource.paper_id, liveSource.title) : null,
        title: liveSource.title || liveSource.paper_id,
        evidences: liveSource.evidence,
        activeEvidenceIndex: liveIndex,
        onActiveEvidenceChange: (index) => {
          setSelectedSource(liveSource);
          setActiveEvidenceIndex(index);
          setPdfTarget({ kind: "assistant", source: liveSource, evidenceIndex: index });
        }
      };
    }
    if (target.kind === "noteCitation") {
      if (isGreySourcePaperId(target.citation.paper_id)) {
        return { url: null, title: target.citation.title ?? target.citation.paper_id, evidences: [], activeEvidenceIndex: 0 };
      }
      return {
        url: api.paperPdfUrl(target.citation.paper_id, target.citation.title ?? ""),
        title: target.citation.title ?? target.citation.paper_id,
        evidences: noteCitationEvidence(target.citation),
        activeEvidenceIndex: 0
      };
    }
    if (target.kind === "grey") {
      // Grey sources render through <GreySourceView>, not the PDF canvas.
      return { url: null, title: target.source.title ?? target.source.url, evidences: [], activeEvidenceIndex: 0 };
    }
    if (target.kind === "missing") {
      return { url: null, title: target.title || target.paperId, evidences: [], activeEvidenceIndex: 0 };
    }
    return {
      url: target.paper.has_full_text && workspacePaperId(target.paper) ? api.paperPdfUrl(workspacePaperId(target.paper), workspacePaperTitle(target.paper)) : null,
      title: workspacePaperTitle(target.paper),
      evidences: [],
      activeEvidenceIndex: 0
    };
  }
}

export type DroppedSourceKind = "pdf" | "image" | "unsupported";

/** Classify a dropped/pasted file by MIME type, falling back to its extension. */
export type WorkspacePaperRecord = Paper & {
  paper_id?: string;
  paperId?: string;
  display_title?: string;
  name?: string;
  filename?: string;
  file_name?: string;
  pdf_filename?: string;
  pdf_path?: string;
  path?: string;
};

