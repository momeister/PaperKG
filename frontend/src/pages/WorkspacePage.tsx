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
  Sparkles,
  Square,
  Star,
  Trash2,
  Upload,
  X
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
  citationMetasFor,
  cleanAnswerQuote,
  EvidenceVerificationBadge,
  fetchAssistantSession,
  formatAnswerForNote,
  formatNoteQuote,
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
import type { CitationMeta } from "./AssistantPage";
import { NotesSurface } from "./NotesPage";
import type { NotesSurfaceActions, NotesSurfaceSnapshot } from "./NotesPage";
import { AnalysisPanel } from "./AnalysisPanel";
import { DatasetsPanel } from "./DatasetsPanel";

export type WorkspaceNavigatorTab = "notes" | "pdfs" | "assistantSessions";

export type WorkspacePdfTarget =
  | { kind: "assistant"; source: VerificationSource; evidenceIndex: number }
  | { kind: "noteCitation"; citation: NoteCitation }
  | { kind: "paper"; paper: Paper }
  | { kind: "grey"; source: GreySource }
  | { kind: "missing"; paperId: string; title?: string };

const ALL_PAPERS_SCOPES = new Set(["", "__all_papers__"]);

type PaperQuestionScope = "current" | "selected" | "all";
type WorkspaceAssistantMode = "pdf" | "notes";

type AssistantAnswerBlock = ReturnType<typeof turnBlocks>[number];
type AssistantTurn = Parameters<typeof turnBlocks>[0];

export type WorkspaceCommandDef = {
  name: string;
  aliases?: string[];
  args?: string;
  description: string;
  group: "frage" | "aktion";
};

/** Claude-Code-Stil: alles, was sonst in anderen Tabs passiert, ist hier als Slash-Befehl erreichbar. */
export const WORKSPACE_COMMANDS: WorkspaceCommandDef[] = [
  { name: "web", description: "Web-Recherche parallel zur Frage (Treffer manuell übernehmen)", group: "frage" },
  { name: "webfrage", aliases: ["webanswer"], args: "<frage>", description: "Web-Recherche + Antwort direkt aus den Treffern (speichert Grauquellen)", group: "frage" },
  { name: "auto", aliases: ["autorecherche"], args: "[frage]", description: "Auto-Recherche: findet lokal nichts, harvestet automatisch Paper + Web (inkl. verwandter Themen)", group: "frage" },
  { name: "new", aliases: ["neu"], args: "[frage]", description: "Neues Gespräch — mit Frage: sofort stellen; Weiterfragen bleibt an", group: "frage" },
  { name: "selected", aliases: ["auswahl"], args: "[frage]", description: "Auf ausgewählte Quellen eingrenzen", group: "frage" },
  { name: "alle", aliases: ["all"], args: "[frage]", description: "Auf alle Quellen des Projekts erweitern", group: "frage" },
  { name: "summary", args: "[fokus]", description: "Zusammenfassung des aktuellen Papers", group: "frage" },
  { name: "extract", args: "[fokus]", description: "Methoden, Ergebnisse & Schlussfolgerungen", group: "frage" },
  { name: "compare", args: "[fokus]", description: "Papers vergleichen", group: "frage" },
  { name: "projekt", args: "<name>", description: "Neues Projekt anlegen und aktivieren", group: "aktion" },
  { name: "notiz", args: "[titel]", description: "Neue Notiz anlegen", group: "aktion" },
  { name: "suche", aliases: ["papers", "import"], args: "<thema>", description: "Neue Papers suchen & herunterladen (arXiv)", group: "aktion" },
  { name: "extraktion", description: "Extraktion für aktuelles/ausgewählte Paper starten", group: "aktion" },
  { name: "hauptquelle", description: "Aktuell geöffnetes Paper als Hauptquelle setzen/entfernen", group: "aktion" },
  { name: "entfernen", aliases: ["loeschen"], description: "Aktuell geöffnetes Paper bzw. Grauquelle aus dem Projekt entfernen", group: "aktion" },
  { name: "help", aliases: ["hilfe"], description: "Befehlsübersicht anzeigen", group: "frage" }
];

export function matchWorkspaceCommands(query: string): WorkspaceCommandDef[] {
  const needle = query.trim().toLowerCase().split(/\s+/)[0] ?? "";
  if (!needle) {
    return WORKSPACE_COMMANDS;
  }
  return WORKSPACE_COMMANDS.filter(
    (command) => command.name.startsWith(needle) || (command.aliases ?? []).some((alias) => alias.startsWith(needle))
  );
}

/**
 * `/web` (und nur /web) wirkt inline: egal wo es im Prompt steht, es wird entfernt und
 * aktiviert die Web-Recherche für genau diese Frage.
 */
export function extractInlineWebToken(value: string): { text: string; web: boolean } {
  let web = false;
  const text = value
    .replace(/(^|\s)\/web\b/gi, (_match, prefix: string) => {
      web = true;
      return prefix;
    })
    .replace(/\s{2,}/g, " ")
    .trim();
  return { text, web };
}

/** The active turn restored from a persisted session (used to reopen the right view). */
export function restoredActiveTurnFor(projectId: string): AssistantTurn | null {
  const session = loadAssistantSession(projectId);
  return (
    session.history.find((turn) => turn.id === session.activeTurnId) ??
    session.history[session.history.length - 1] ??
    null
  );
}

/** Erkennvermerk, dass eine Antwort faktisch leer ausging und das Web helfen könnte. */
export function answerSuggestsWebSearch(answer: Answer | null | undefined): boolean {
  if (!answer) {
    return false;
  }
  if (answer.no_answer) {
    return true;
  }
  if (answer.context_diagnostics?.fallback_reason === "no_traceable_citations") {
    return true;
  }
  return /not contain enough evidence|does not contain enough|nicht genug (?:Evidenz|Belege)|keine ausreichenden? (?:Evidenz|Belege)|konnte keine .{0,40}finden/i.test(
    answer.answer || ""
  );
}

type WorkspaceActionEntry = {
  id: string;
  title: string;
  detail: string;
  status: "ok" | "error" | "pending";
  createdAt: string;
};

const EMPTY_NOTES_SNAPSHOT: NotesSurfaceSnapshot = {
  activeNoteId: "",
  title: "",
  notes: [],
  notesLoading: false,
  currentNote: null,
  citations: [],
  citationRows: [],
  selectedCitation: null,
  threads: [],
  activeThreadId: "",
  historyOpen: false,
  threadMeta: new Map(),
  followUpDrafts: {},
  isFollowUpPending: false,
  deletingThreadId: ""
};

export function WorkspacePage() {
  const { activeProject, setActiveProject, provider, model, llmParams } = useAppState();
  const scopedProjectId = noteProjectId(activeProject);
  const scopeLabel = projectScopeLabel(activeProject);
  const queryClient = useQueryClient();
  const assistantScopeRef = useRef(scopedProjectId);
  const notesActionsRef = useRef<NotesSurfaceActions | null>(null);
  const navResizeFrameRef = useRef<number | null>(null);
  const assistantResizeFrameRef = useRef<number | null>(null);
  const pdfResizeFrameRef = useRef<number | null>(null);
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
  const [autoProgress, setAutoProgress] = useState<{
    phase: string;
    relatedTopics: string[];
    papers: { id: string; title: string }[];
    grey: { id: string; title: string; url: string }[];
  } | null>(null);
  const autoAbortRef = useRef<AbortController | null>(null);
  const [paletteIndex, setPaletteIndex] = useState(0);
  const [chatSettingsOpen, setChatSettingsOpen] = useState(false);
  const [actionsMenuOpen, setActionsMenuOpen] = useState(false);
  const [actionLog, setActionLog] = useState<WorkspaceActionEntry[]>([]);
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

  type AskVariables = { value: string; scope: PaperQuestionScope; newTurn: boolean; extraGreyIds?: string[] };

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
        llm_overrides: Object.values(llmParams).some((value) => value !== undefined) ? llmParams : undefined
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
            case "harvesting":
              setAutoProgress((p) => ({
                phase: event.scope === "main" ? "Paper & Web zur Frage werden geladen …" : `Verwandtes Thema „${event.topic}" wird recherchiert …`,
                relatedTopics: p?.relatedTopics ?? [],
                papers: [...(p?.papers ?? []), ...(event.papers ?? [])],
                grey: [...(p?.grey ?? []), ...(event.grey ?? [])]
              }));
              break;
            case "reanswering":
              setAutoProgress((p) => (p ? { ...p, phase: "Antwort wird mit den neuen Quellen erstellt …" } : p));
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
              updateAction(actionId, {
                status: "ok",
                detail: summary?.harvested
                  ? `${summary.papers.length} Paper + ${summary.grey.length} Web-Quellen ergänzt.`
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

  function logAction(title: string, detail: string, status: WorkspaceActionEntry["status"] = "ok") {
    const entry: WorkspaceActionEntry = {
      id: `act_${Date.now()}_${Math.random().toString(16).slice(2)}`,
      title,
      detail,
      status,
      createdAt: new Date().toISOString()
    };
    setActionLog((current) => [...current.slice(-7), entry]);
    return entry.id;
  }

  function updateAction(id: string, patch: Partial<Pick<WorkspaceActionEntry, "detail" | "status">>) {
    setActionLog((current) => current.map((entry) => (entry.id === id ? { ...entry, ...patch } : entry)));
  }

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
    // (quota), so reloads must restore the conversation from the server.
    let cancelled = false;
    void fetchAssistantSession(scopedProjectId).then((server) => {
      if (cancelled || !server || assistantScopeRef.current !== scopedProjectId) {
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
    return () => {
      cancelled = true;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [scopedProjectId]);

  useEffect(() => {
    if (assistantScopeRef.current === scopedProjectId) {
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
    const markdown = `> ${quote.replace(/\n+/g, "\n> ")}\n\nQuelle: [Z1 - ${label}](sciencekg://citation/${citation.id}) (${source.url})`;
    notesActionsRef.current?.clearInsertPreview();
    void appendToActiveNote(markdown, [citation]);
  }

  function previewGreyQuote(text: string, source: GreySource) {
    const quote = text.trim();
    if (!quote) {
      return;
    }
    const label = source.title || source.url;
    notesActionsRef.current?.previewAppendMarkdown(`> ${quote.replace(/\n+/g, "\n> ")}\n\nQuelle: [Z1 - ${label}](sciencekg://citation/preview) (${source.url})`);
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

  type SlashCommandResult = { handled: boolean; ask?: string; scope?: PaperQuestionScope; newTurn?: boolean };

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
  function ask(value: string, options: { scope?: PaperQuestionScope; newTurn?: boolean; web?: boolean; auto?: boolean; force?: boolean } = {}) {
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
    answerMutation.mutate({ value: value + verbosityInstruction(verbosity), scope, newTurn });
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
          ask(command.ask, { scope: command.scope, newTurn: command.newTurn, web: inline.web || undefined });
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

  function insertCitationFromAnswer(source: VerificationSource, evidenceIndex: number, quote = "") {
    const evidence = source.evidence[evidenceIndex];
    if (!evidence) {
      return;
    }
    // The text that lands in the note is also stored on the citation, so clicking
    // the citation later shows exactly this passage again.
    const insertedText = meaningfulQuote(quote) || evidence.pdf_excerpt || evidence.reference_text;
    const citation = noteCitation(source, evidence, evidenceIndex, insertedText);
    notesActionsRef.current?.clearInsertPreview();
    void appendToActiveNote(formatNoteQuote(insertedText, source, evidenceIndex, citation.id), [citation]);
  }

  function previewCitationFromAnswer(source: VerificationSource, evidenceIndex: number, quote = "") {
    const evidence = source.evidence[evidenceIndex];
    if (!evidence) {
      return;
    }
    const insertedText = meaningfulQuote(quote) || evidence.pdf_excerpt || evidence.reference_text;
    const citation = noteCitation(source, evidence, evidenceIndex, insertedText);
    notesActionsRef.current?.previewAppendMarkdown(formatNoteQuote(insertedText, source, evidenceIndex, citation.id));
  }

  function toggleScopedPaper(paperId: string) {
    setSelectedPaperIds((current) => (current.includes(paperId) ? current.filter((item) => item !== paperId) : [...current, paperId]));
  }

  function toggleScopedGrey(greyId: string) {
    setSelectedGreyIds((current) => (current.includes(greyId) ? current.filter((item) => item !== greyId) : [...current, greyId]));
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
      saveAssistantSession(scopedProjectId, { history: next, activeTurnId: activeTurnId === turnId ? (next[0]?.id ?? "") : activeTurnId });
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

  function startColumnResize(
    event: ReactPointerEvent<HTMLDivElement>,
    value: number,
    setter: (value: number) => void,
    frameRef: MutableRefObject<number | null>,
    min: number,
    max: number
  ) {
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = value;
    const move = (moveEvent: globalThis.PointerEvent) => {
      const next = Math.min(max, Math.max(min, startWidth + moveEvent.clientX - startX));
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
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function startVerticalResize(
    event: ReactPointerEvent<HTMLDivElement>,
    value: number,
    setter: (value: number) => void,
    frameRef: MutableRefObject<number | null>,
    min: number,
    max: number
  ) {
    event.preventDefault();
    const startY = event.clientY;
    const startHeight = value;
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
    };
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
    if (pdfView.url || !pdfTarget || isIngestingCurrentPdf) return undefined;
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
  const navColumn = navigatorOpen ? `${navigatorWidth}px` : "46px";
  const assistantColumn = assistantOpen ? `${assistantWidth}px` : "46px";
  const pdfColumn = centerView !== "pdf" || pdfOpen ? `${pdfWidth}px` : "46px";
  const notesColumn = notesOpen ? "minmax(80px, 1fr)" : "46px";

  return (
    <section
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
            onResizeCitationList={(event) => startVerticalResize(event, pdfCitationListHeight, setPdfCitationListHeight, pdfCitationResizeFrameRef, 90, 520)}
            onResizeGreyList={(event) => startVerticalResize(event, greySourceListHeight, setGreySourceListHeight, greySourceResizeFrameRef, 80, 400)}
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
        onPointerDown={navigatorOpen ? (event) => startColumnResize(event, navigatorWidth, setNavigatorWidth, navResizeFrameRef, 110, 520) : undefined}
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
        onPointerDown={pdfOpen ? (event) => startColumnResize(event, pdfWidth, setPdfWidth, pdfResizeFrameRef, 110, 900) : undefined}
      />

      {assistantOpen ? (
        <section className={`workspace-assistant-pane ${assistantMode === "notes" ? "workspace-assistant-pane--notes" : ""}`}>
          <PaneHeading
            title="Assistant"
            onCollapse={() => setAssistantOpen(false)}
            collapseSide="left"
            status={answerMutation.isPending ? "running" : answer?.generation_error ? "warning" : "idle"}
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
              <button className="icon-button chat-send-button" aria-label="Senden" disabled={answerMutation.isPending || questionBlockedByScope}>
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
              parallelSession ? (
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
                  <span>Varianten werden vorgeschlagen…</span>
                </div>
              ) : (
                <EmptyState title="Parallel Research">Stelle eine Frage, zu der du Varianten ausprobieren willst.</EmptyState>
              )
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
                onCitationInsert={(source, evidenceIndex, quote) => insertCitationFromAnswer(source, evidenceIndex, quote)}
                onCitationInsertPreview={(source, evidenceIndex, quote) => previewCitationFromAnswer(source, evidenceIndex, quote)}
                onCitationInsertPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
                onDrillDeeper={(nodeId, question) => drillDeeperInTree(nodeId, question)}
                onSaveToNotes={() => void saveResearchTreeToNotes()}
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
            {!parallelMode && !deepMode && activeTurn && activeTurn.type !== "research_tree" ? (
              <div className="answer-blocks">
                {activeBlocks.filter((block) => block.answer).map((block, index) => (
                  <article className={`answer-block ${index > 0 ? "answer-block--followup" : ""}`} key={block.id}>
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
                        onCitationInsert={(source, evidenceIndex, quote) => insertCitationFromAnswer(source, evidenceIndex, quote)}
                        onCitationInsertPreview={(source, evidenceIndex, quote) => previewCitationFromAnswer(source, evidenceIndex, quote)}
                        onCitationInsertPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
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
              </>
            )}
          </section>
            </>
          )}
        </section>
      ) : (
        <CollapsedPane label="Assistant" icon={<PanelLeftOpen size={17} />} onOpen={() => setAssistantOpen(true)} />
      )}
      <div
        className={`split-handle ${assistantOpen ? "" : "split-handle--idle"}`}
        role="separator"
        aria-label="Assistant Breite anpassen"
        onPointerDown={assistantOpen ? (event) => startColumnResize(event, assistantWidth, setAssistantWidth, assistantResizeFrameRef, 110, 1100) : undefined}
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
              onTakeIntoNote={(md) => notesActionsRef.current?.insertMarkdownAtCursor(md) ?? Promise.resolve(null)}
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
        <div className="harvest-dialog-overlay">
          <div
            className="harvest-dialog-card clarify-dialog-card"
            tabIndex={-1}
            ref={(el) => {
              if (el && !el.contains(document.activeElement)) el.focus();
            }}
            onKeyDown={(e) => {
              const inInput = (e.target as HTMLElement).tagName === "INPUT";
              if (e.key === "Enter") {
                e.preventDefault();
                finishClarify(false);
              } else if (e.key === "Escape") {
                e.preventDefault();
                finishClarify(true);
              } else if (!inInput && /^[1-9]$/.test(e.key)) {
                const idx = Number(e.key) - 1;
                if (idx < clarifyDirections.length) {
                  e.preventDefault();
                  toggleClarifyDirection(idx);
                }
              }
            }}
          >
            <strong>In welche Richtung soll die Analyse gehen?</strong>
            <p>
              Wähle Schwerpunkte mit den Zahlentasten <kbd>1</kbd>–<kbd>9</kbd> oder per Klick,
              ergänze optional eine eigene Richtung und starte mit <kbd>Enter</kbd>.
            </p>
            <div className="clarify-directions">
              {clarifyDirections.map((dir, i) => {
                const active = clarifySelected.includes(i);
                return (
                  <button
                    key={i}
                    type="button"
                    className={`clarify-direction${active ? " clarify-direction--active" : ""}`}
                    onClick={() => toggleClarifyDirection(i)}
                  >
                    <span className="clarify-direction-num">{i + 1}</span>
                    <span className="clarify-direction-label">{dir}</span>
                    {active ? <span className="clarify-direction-check">✓</span> : null}
                  </button>
                );
              })}
            </div>
            <div className="clarify-question-block">
              <label className="clarify-question-label">Eigene Richtung / Anmerkungen</label>
              <input
                type="text"
                className="clarify-question-input"
                placeholder="z.B. Fokus auf klinische Anwendungen, ab 2020, ..."
                value={clarifyFreetext}
                onChange={(e) => setClarifyFreetext(e.target.value)}
              />
            </div>
            <div className="harvest-dialog-actions">
              <button
                type="button"
                className="button button-primary button-compact"
                onClick={() => finishClarify(false)}
              >
                Analyse starten
              </button>
              <button
                type="button"
                className="button button-compact"
                onClick={() => finishClarify(true)}
              >
                Überspringen
              </button>
            </div>
          </div>
        </div>
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

function WorkspaceNotesAssistant({
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

function WorkspaceNavigatorBody({
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

function PaneHeading({
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

function CollapsedPane({ label, icon, onOpen }: { label: string; icon: ReactNode; onOpen: () => void }) {
  return (
    <aside className="assistant-collapsed-panel workspace-collapsed-pane">
      <button className="collapsed-panel-tab" type="button" onClick={onOpen}>
        {icon}
        <span>{label}</span>
      </button>
    </aside>
  );
}

function sameNotesSnapshot(left: NotesSurfaceSnapshot, right: NotesSurfaceSnapshot) {
  return (
    left.activeNoteId === right.activeNoteId &&
    left.title === right.title &&
    left.notes === right.notes &&
    left.notesLoading === right.notesLoading &&
    left.currentNote === right.currentNote &&
    left.citations === right.citations &&
    left.citationRows === right.citationRows &&
    left.selectedCitation === right.selectedCitation &&
    left.threads === right.threads &&
    left.activeThreadId === right.activeThreadId &&
    left.historyOpen === right.historyOpen &&
    left.threadMeta === right.threadMeta &&
    left.followUpDrafts === right.followUpDrafts &&
    left.isFollowUpPending === right.isFollowUpPending &&
    left.deletingThreadId === right.deletingThreadId
  );
}

function findingToGreyRecord(finding: DeepResearchFinding): Record<string, unknown> {
  return {
    url: finding.url,
    title: finding.title,
    summary: finding.summary,
    raw_excerpt: finding.raw_excerpt,
    full_text: finding.full_text ?? finding.raw_excerpt,
    evidence: finding.evidence ?? [],
    injection_flags: finding.injection_flags ?? []
  };
}

/** Adapt a (not-yet-saved) web finding to the GreySource shape for the viewer. */
function findingToGreySource(finding: DeepResearchFinding): GreySource {
  return {
    id: `pending_${finding.url}`,
    project_id: "",
    url: finding.url,
    title: finding.title,
    summary: finding.summary,
    raw_excerpt: finding.raw_excerpt,
    full_text: finding.full_text ?? finding.raw_excerpt,
    evidence: finding.evidence ?? [],
    injection_flags: finding.injection_flags ?? []
  };
}

export type DroppedSourceKind = "pdf" | "image" | "unsupported";

/** Classify a dropped/pasted file by MIME type, falling back to its extension. */
export function classifyDroppedFile(file: File): DroppedSourceKind {
  const type = (file.type || "").toLowerCase();
  if (type === "application/pdf") return "pdf";
  if (type.startsWith("image/")) return "image";
  const name = (file.name || "").toLowerCase();
  if (name.endsWith(".pdf")) return "pdf";
  if (/\.(png|jpe?g|gif|webp|svg|bmp)$/.test(name)) return "image";
  return "unsupported";
}

const PASTED_URL_PATTERN = /^https?:\/\/\S+$/i;

/** A pasted/dropped plain-text snippet counts as a URL only if it's a single bare link. */
export function classifyPastedText(text: string): "url" | null {
  const trimmed = text.trim();
  return trimmed && !trimmed.includes("\n") && PASTED_URL_PATTERN.test(trimmed) ? "url" : null;
}

function fileExtension(filename: string) {
  const match = /\.([a-z0-9]+)$/i.exec(filename || "");
  return match ? match[1].toLowerCase() : "";
}

function activeScopePaperId(target: WorkspacePdfTarget | null, activeAssistantSource: VerificationSource | null) {
  if (target) {
    if (target.kind === "assistant") {
      return target.source.paper_id;
    }
    if (target.kind === "noteCitation") {
      return target.citation.paper_id;
    }
    if (target.kind === "grey") {
      return "";
    }
    if (target.kind === "missing") {
      return target.paperId;
    }
    return workspacePaperId(target.paper);
  }
  return activeAssistantSource?.paper_id ?? "";
}

type WorkspacePaperRecord = Paper & {
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

function normalizeWorkspacePaper(paper: Paper): Paper {
  const id = workspacePaperId(paper);
  const title = workspacePaperTitle(paper);
  return { ...paper, id, title };
}

function workspacePaperId(paper: Paper | null | undefined) {
  if (!paper) {
    return "";
  }
  const record = paper as WorkspacePaperRecord;
  return cleanWorkspacePaperText(paper.id) || cleanWorkspacePaperText(record.paper_id) || cleanWorkspacePaperText(record.paperId) || cleanWorkspacePaperText(paper.source_id) || "";
}

function workspacePaperTitle(paper: Paper | null | undefined) {
  if (!paper) {
    return "Unbenanntes Paper";
  }
  const record = paper as WorkspacePaperRecord;
  return (
    cleanWorkspacePaperText(paper.title) ||
    cleanWorkspacePaperText(record.display_title) ||
    cleanWorkspacePaperText(record.name) ||
    workspacePaperFileTitle(record) ||
    workspacePaperId(paper) ||
    "Unbenanntes Paper"
  );
}

function cleanWorkspacePaperText(value: unknown) {
  const text = String(value ?? "").replace(/\s+/g, " ").trim();
  if (!text || ["undefined", "null", "none", "nan"].includes(text.toLowerCase())) {
    return "";
  }
  return text;
}

function workspacePaperFileTitle(paper: WorkspacePaperRecord) {
  const raw =
    cleanWorkspacePaperText(paper.pdf_filename) ||
    cleanWorkspacePaperText(paper.file_name) ||
    cleanWorkspacePaperText(paper.filename) ||
    cleanWorkspacePaperText(paper.pdf_path) ||
    cleanWorkspacePaperText(paper.path) ||
    cleanWorkspacePaperText(paper.pdf_url);
  if (!raw) {
    return "";
  }
  const withoutQuery = raw.split(/[?#]/)[0] || raw;
  const fileName = withoutQuery.split(/[\\/]/).pop() || withoutQuery;
  const decoded = decodeWorkspacePaperText(fileName);
  return cleanWorkspacePaperText(decoded.replace(/\.pdf$/i, "").replace(/[_-]+/g, " "));
}

function decodeWorkspacePaperText(value: string) {
  try {
    return decodeURIComponent(value);
  } catch {
    return value;
  }
}

function noteCitationEvidence(citation: NoteCitation): VerificationEvidence[] {
  return [
    {
      evidence_id: citation.evidence_id || undefined,
      paper_id: citation.paper_id,
      kind: citation.kind || "note",
      reference_text: citation.reference_text || "",
      pdf_excerpt: citation.pdf_excerpt || "",
      matched_terms: textTerms(`${citation.reference_text ?? ""} ${citation.pdf_excerpt ?? ""}`),
      found_in_pdf_text: Boolean(citation.pdf_excerpt),
      evidence_index: citation.evidence_index ?? 0
    }
  ];
}

function latestThreadAnswer(thread: NoteAiThread) {
  const messages = threadMessages(thread);
  const answer = [...messages].reverse().find((message) => message.role === "assistant")?.content;
  return (answer || thread.replacement_text || thread.response_text || "").trim();
}

function threadMessages(thread: NoteAiThread): NoteAiMessage[] {
  if (thread.messages?.length) {
    const hidden = new Set((Array.isArray(thread.ui_state?.hidden_message_ids) ? thread.ui_state?.hidden_message_ids : []).map((item) => String(item)));
    return thread.messages.filter((message) => !hidden.has(message.id));
  }
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

function threadCollapsed(thread: NoteAiThread) {
  return thread.ui_state?.collapsed !== false;
}

function threadPinned(thread: NoteAiThread) {
  return thread.ui_state?.pinned === true;
}

function sortPinnedThreads(threads: NoteAiThread[]) {
  return threads
    .map((thread, index) => ({ thread, index }))
    .sort((left, right) => {
      const pinnedDelta = Number(threadPinned(right.thread)) - Number(threadPinned(left.thread));
      return pinnedDelta || left.index - right.index;
    })
    .map((item) => item.thread);
}

function shortSelectionPreview(value: string) {
  const text = stripThreadContext(value).replace(/\s+/g, " ").trim();
  return text.length <= 260 ? text : `${text.slice(0, 257)}...`;
}

function stripThreadContext(value: string) {
  return String(value || "").replace(/^==([\s\S]*)==$/, "$1").trim();
}

function textTerms(text: string) {
  return Array.from(new Set(text.toLowerCase().replace(/[^a-z0-9-]+/g, " ").split(" "))).filter((term) => term.length >= 5).slice(0, 12);
}

function normalizeFilter(value: string) {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function workspaceUiKey(projectId: string, key: string) {
  return `sciencekg.workspace.ui.${projectId}.${key}`;
}

function loadWorkspaceBoolean(projectId: string, key: string, fallback: boolean) {
  try {
    const value = window.localStorage.getItem(workspaceUiKey(projectId, key));
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}

function saveWorkspaceBoolean(projectId: string, key: string, value: boolean) {
  try {
    window.localStorage.setItem(workspaceUiKey(projectId, key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}

function loadWorkspaceNumber(projectId: string, key: string, fallback: number) {
  try {
    const value = Number(window.localStorage.getItem(workspaceUiKey(projectId, key)));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

function saveWorkspaceNumber(projectId: string, key: string, value: number) {
  try {
    window.localStorage.setItem(workspaceUiKey(projectId, key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}

// ── Research Tree View ─────────────────────────────────────────────────────

/**
 * Build the citation-resolution pool for a reloaded research tree.
 *
 * Citations only render as coloured/clickable chips when their token resolves to a
 * source in the pool; otherwise they fall back to the muted "!"-chip. The heavy
 * verification payload is trimmed when a session is persisted (localStorage/server
 * quota), so on reload `node.verification` can be empty — which used to grey out
 * *every* citation. `answer.sources` is small and always survives persistence, so we
 * fold it in as a minimal fallback: verification wins (richer evidence), and any
 * source not already covered is added so its citation still resolves and keeps its
 * colour (real papers vivid, `grey::` sources grey by intent).
 */
function citationPoolFor(
  verification: VerificationSource[] | undefined,
  answer: ResearchNode["answer"] | null | undefined,
): VerificationSource[] {
  const pool: VerificationSource[] = [...(verification ?? [])];
  const seen = new Set(pool.map((s) => s.paper_id));
  for (const src of answer?.sources ?? []) {
    if (!src.paper_id || seen.has(src.paper_id)) continue;
    seen.add(src.paper_id);
    pool.push({ paper_id: src.paper_id, title: src.title || src.paper_id, pdf_available: false, evidence: [] });
  }
  return pool;
}

function ResearchTreeView({
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
  onCitationInsert: (source: VerificationSource, evidenceIndex: number, quote: string) => void;
  onCitationInsertPreview: (source: VerificationSource, evidenceIndex: number, quote: string) => void;
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
              onCitationInsert={(source, evidenceIndex, quote) => onCitationInsert(source, evidenceIndex, quote)}
              onCitationInsertPreview={(source, evidenceIndex, quote) => onCitationInsertPreview(source, evidenceIndex, quote)}
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
      onCitationInsert: (source: VerificationSource, evidenceIndex: number, quote: string) => onCitationInsert(source, evidenceIndex, quote),
      onCitationInsertPreview: (source: VerificationSource, evidenceIndex: number, quote: string) => onCitationInsertPreview(source, evidenceIndex, quote),
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
