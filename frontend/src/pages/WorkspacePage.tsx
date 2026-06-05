import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, MutableRefObject, PointerEvent as ReactPointerEvent, ReactNode } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bot,
  ChevronDown,
  ChevronUp,
  FilePlus2,
  FileText,
  ListChecks,
  Maximize2,
  MessageSquareText,
  Minimize2,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  Quote,
  Search,
  Send,
  Sparkles,
  Trash2
} from "lucide-react";

import { api } from "../api";
import { evidenceColorVars } from "../citationColors";
import { EmptyState } from "../components/EmptyState";
import { PdfPane } from "../components/PdfPane";
import { Status } from "../components/Status";
import { noteProjectId, projectScopeLabel } from "../projectScope";
import { useAppState } from "../state";
import type { Answer, NoteAiMessage, NoteAiThread, NoteCitation, Paper, VerificationEvidence, VerificationSource } from "../types";
import {
  AnswerText,
  answerLimitFor,
  citationMetasFor,
  cleanAnswerQuote,
  formatAnswerForNote,
  formatNoteQuote,
  formatTurnTime,
  loadAssistantSession,
  mergeVerification,
  noteCitation,
  saveAssistantSession,
  shortEvidenceText,
  turnBlocks,
  turnContext,
  verificationLimits
} from "./AssistantPage";
import { NotesSurface } from "./NotesPage";
import type { NotesSurfaceActions, NotesSurfaceSnapshot } from "./NotesPage";

export type WorkspaceNavigatorTab = "notes" | "pdfs" | "assistantSessions";

export type WorkspacePdfTarget =
  | { kind: "assistant"; source: VerificationSource; evidenceIndex: number }
  | { kind: "noteCitation"; citation: NoteCitation }
  | { kind: "paper"; paper: Paper };

type PaperQuestionScope = "current" | "selected" | "all";
type WorkspaceAssistantMode = "pdf" | "notes";

type AssistantAnswerBlock = ReturnType<typeof turnBlocks>[number];
type AssistantTurn = Parameters<typeof turnBlocks>[0];

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
  const { activeProject, provider, model } = useAppState();
  const scopedProjectId = noteProjectId(activeProject);
  const scopeLabel = projectScopeLabel(activeProject);
  const queryClient = useQueryClient();
  const assistantScopeRef = useRef(scopedProjectId);
  const notesActionsRef = useRef<NotesSurfaceActions | null>(null);
  const navResizeFrameRef = useRef<number | null>(null);
  const assistantResizeFrameRef = useRef<number | null>(null);
  const pdfResizeFrameRef = useRef<number | null>(null);
  const pdfCitationResizeFrameRef = useRef<number | null>(null);

  const [navigatorTab, setNavigatorTab] = useState<WorkspaceNavigatorTab>("notes");
  const [notesSnapshot, setNotesSnapshot] = useState<NotesSurfaceSnapshot>(EMPTY_NOTES_SNAPSHOT);
  const [controlledNoteId, setControlledNoteId] = useState("");
  const [requestedCitationId, setRequestedCitationId] = useState("");
  const [navigatorQuery, setNavigatorQuery] = useState("");
  const [noteStatus, setNoteStatus] = useState("");

  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<AssistantTurn[]>(() => loadAssistantSession(scopedProjectId).history);
  const [activeTurnId, setActiveTurnId] = useState(() => loadAssistantSession(scopedProjectId).activeTurnId);
  const [selectedSource, setSelectedSource] = useState<VerificationSource | null>(null);
  const [activeEvidenceIndex, setActiveEvidenceIndex] = useState(0);
  const [selectedAnswerQuote, setSelectedAnswerQuote] = useState<{ paperId: string; evidenceIndex: number; text: string } | null>(null);
  const [evidenceOpen, setEvidenceOpen] = useState(true);
  const [evidenceMode, setEvidenceMode] = useState("auto");
  const [conversationMode, setConversationMode] = useState<"followup" | "new">("followup");
  const [paperScope, setPaperScope] = useState<PaperQuestionScope>("all");
  const [assistantMode, setAssistantMode] = useState<WorkspaceAssistantMode>("pdf");
  const [focusedNoteThreadId, setFocusedNoteThreadId] = useState("");
  const [selectedPaperIds, setSelectedPaperIds] = useState<string[]>([]);

  const [navigatorOpen, setNavigatorOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "navigatorOpen", true));
  const [assistantOpen, setAssistantOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "assistantOpen", true));
  const [pdfOpen, setPdfOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "pdfOpen", true));
  const [notesOpen, setNotesOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "notesOpen", true));
  const [navigatorWidth, setNavigatorWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "navigatorWidth.v3", 180));
  const [assistantWidth, setAssistantWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "assistantWidth.v3", 420));
  const [pdfWidth, setPdfWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "pdfWidth.v3", 220));
  const [pdfCitationListHeight, setPdfCitationListHeight] = useState(() => loadWorkspaceNumber(scopedProjectId, "pdfCitationListHeight", 220));
  const [pdfTarget, setPdfTarget] = useState<WorkspacePdfTarget | null>(null);

  const activeTurn = useMemo(() => {
    if (!history.length) {
      return null;
    }
    return history.find((turn) => turn.id === activeTurnId) ?? history[history.length - 1];
  }, [activeTurnId, history]);
  const activeBlocks = useMemo(() => (activeTurn ? turnBlocks(activeTurn) : []), [activeTurn]);
  const latestBlock = activeBlocks[activeBlocks.length - 1] ?? null;
  const answer = latestBlock?.answer ?? null;
  const verification = useMemo(() => mergeVerification(activeBlocks.flatMap((block) => block.verification)), [activeBlocks]);
  const activeEvidence = selectedSource?.evidence[activeEvidenceIndex];
  const activeAnswerQuote =
    selectedAnswerQuote && selectedAnswerQuote.paperId === selectedSource?.paper_id && selectedAnswerQuote.evidenceIndex === activeEvidenceIndex
      ? selectedAnswerQuote.text
      : "";

  const papersQuery = useQuery({
    queryKey: ["workspace-pdfs", activeProject],
    queryFn: () => api.listPapers({ project_id: activeProject, has_full_text: true, limit: 200 })
  });

  const currentScopePaperId = activeScopePaperId(pdfTarget, selectedSource);
  const scopedPaperIds =
    paperScope === "all" ? [] : paperScope === "selected" ? selectedPaperIds : currentScopePaperId ? [currentScopePaperId] : [];
  const questionBlockedByScope = (paperScope === "selected" && !selectedPaperIds.length) || (paperScope === "current" && !currentScopePaperId);

  const answerMutation = useMutation({
    mutationFn: (value: string) =>
      api.answer({
        question: value,
        provider,
        model,
        limit: answerLimitFor(value, evidenceMode, paperScope === "all" ? 0 : Math.max(1, scopedPaperIds.length)),
        paper_ids: scopedPaperIds.length ? scopedPaperIds : undefined,
        conversation_context: conversationMode === "followup" && activeTurn ? turnContext(activeTurn) : undefined
      }),
    onSuccess: async (payload) => {
      let sources: VerificationSource[] = [];
      try {
        const report = await api.verifyAnswer(payload, verificationLimits(payload));
        sources = report.sources;
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
      if (conversationMode === "followup" && activeTurn) {
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
  });

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
    setFocusedNoteThreadId("");
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
    if (citation) {
      setPdfTarget({ kind: "noteCitation", citation });
      setPdfOpen(true);
      setNavigatorTab("pdfs");
    }
  }, []);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (question.trim() && !questionBlockedByScope) {
      answerMutation.mutate(question.trim());
    }
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
      setPdfTarget({ kind: "assistant", source, evidenceIndex: nextIndex });
    }
    if (options.openPdf) {
      setPdfOpen(true);
      setNavigatorTab("pdfs");
    }
    if (quote) {
      setSelectedAnswerQuote({ paperId: source.paper_id, evidenceIndex: nextIndex, text: cleanAnswerQuote(quote) });
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
      setEvidenceOpen(true);
      openAssistantSource(meta.source, meta.evidenceIndex, quote || context);
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
    const citation = noteCitation(source, evidence, evidenceIndex);
    notesActionsRef.current?.clearInsertPreview();
    void appendToActiveNote(formatNoteQuote(quote || evidence.pdf_excerpt || evidence.reference_text, source, evidenceIndex, citation.id), [citation]);
  }

  function previewCitationFromAnswer(source: VerificationSource, evidenceIndex: number, quote = "") {
    const evidence = source.evidence[evidenceIndex];
    if (!evidence) {
      return;
    }
    const citation = noteCitation(source, evidence, evidenceIndex);
    notesActionsRef.current?.previewAppendMarkdown(formatNoteQuote(quote || evidence.pdf_excerpt || evidence.reference_text, source, evidenceIndex, citation.id));
  }

  function toggleScopedPaper(paperId: string) {
    setSelectedPaperIds((current) => (current.includes(paperId) ? current.filter((item) => item !== paperId) : [...current, paperId]));
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
    const quote = sourceKind === "pdf" ? activeEvidence.pdf_excerpt || activeEvidence.reference_text : activeAnswerQuote || activeEvidence.reference_text;
    const citation = noteCitation(selectedSource, activeEvidence, activeEvidenceIndex);
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
    const quote = sourceKind === "pdf" ? activeEvidence.pdf_excerpt || activeEvidence.reference_text : activeAnswerQuote || activeEvidence.reference_text;
    const citation = noteCitation(selectedSource, activeEvidence, activeEvidenceIndex);
    notesActionsRef.current?.previewAppendMarkdown(formatNoteQuote(quote, selectedSource, activeEvidenceIndex, citation.id));
  }

  function activateAssistantTurn(turnId: string) {
    setActiveTurnId(turnId);
    const turn = history.find((item) => item.id === turnId);
    if (!turn) {
      return;
    }
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
  const pdfView = pdfProps(pdfTarget);
  const navColumn = navigatorOpen ? `${navigatorWidth}px` : "46px";
  const assistantColumn = assistantOpen ? `${assistantWidth}px` : "46px";
  const pdfColumn = pdfOpen ? `${pdfWidth}px` : "46px";
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
            <button type="button" className={navigatorTab === "notes" ? "active" : ""} onClick={() => setNavigatorTab("notes")}>
              <NotebookPen size={15} />
              <span>Notizen</span>
              <strong>{notesSnapshot.notes.length}</strong>
            </button>
            <button type="button" className={navigatorTab === "pdfs" ? "active" : ""} onClick={() => setNavigatorTab("pdfs")}>
              <FileText size={15} />
              <span>PDFs</span>
              <strong>{notesSnapshot.citations.length + pdfPapers.length}</strong>
            </button>
            <button type="button" className={`workspace-nav-tab--wide ${navigatorTab === "assistantSessions" ? "active" : ""}`} onClick={() => setNavigatorTab("assistantSessions")}>
              <MessageSquareText size={15} />
              <span>KI-Sessions</span>
              <strong>{history.length}</strong>
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
            onToggleScopedPaper={toggleScopedPaper}
            onResizeCitationList={(event) => startVerticalResize(event, pdfCitationListHeight, setPdfCitationListHeight, pdfCitationResizeFrameRef, 90, 520)}
            onOpenAssistantPdf={openSelectedAssistantPdf}
            onActivateSession={activateAssistantTurn}
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

      {pdfOpen ? (
        <PdfPane
          url={pdfView.url}
          title={pdfView.title}
          evidences={pdfView.evidences}
          activeEvidenceIndex={pdfView.activeEvidenceIndex}
          onActiveEvidenceChange={pdfView.onActiveEvidenceChange}
          onCollapse={() => setPdfOpen(false)}
        />
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
          <PaneHeading title="Assistant" onCollapse={() => setAssistantOpen(false)} collapseSide="left" status={answerMutation.isPending ? "running" : answer?.generation_error ? "warning" : "idle"} />
          <div className="segmented workspace-assistant-mode-toggle" aria-label="Assistant-Modus">
            <button
              type="button"
              className={assistantMode === "pdf" ? "active" : ""}
              onClick={() => {
                notesActionsRef.current?.clearInsertPreview();
                setAssistantMode("pdf");
              }}
            >
              <FileText size={15} />
              <span>PDF-Assistent</span>
            </button>
            <button
              type="button"
              className={assistantMode === "notes" ? "active" : ""}
              onClick={() => {
                notesActionsRef.current?.clearInsertPreview();
                setAssistantMode("notes");
              }}
            >
              <Sparkles size={15} />
              <span>Notiz-Assistent</span>
            </button>
          </div>
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
          <form className="chat-box" onSubmit={submit}>
            <Bot size={20} />
            <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Frage an den lokalen KG" />
            <div className="segmented workspace-scope-segment" aria-label="Paper-Scope">
              <button type="button" className={paperScope === "current" ? "active" : ""} onClick={() => setPaperScope("current")}>
                Dieses
              </button>
              <button type="button" className={paperScope === "selected" ? "active" : ""} onClick={() => setPaperScope("selected")}>
                Auswahl
              </button>
              <button type="button" className={paperScope === "all" ? "active" : ""} onClick={() => setPaperScope("all")}>
                Alle
              </button>
            </div>
            <select aria-label="Chatmodus" value={conversationMode} onChange={(event) => setConversationMode(event.target.value as "followup" | "new")}>
              <option value="followup">Weiterfragen</option>
              <option value="new">Neu starten</option>
            </select>
            <select aria-label="Evidenzmenge" value={evidenceMode} onChange={(event) => setEvidenceMode(event.target.value)}>
              <option value="auto">Auto</option>
              <option value="12">12</option>
              <option value="20">20</option>
              <option value="25">25</option>
            </select>
            <button className="icon-button" aria-label="Senden" disabled={answerMutation.isPending || questionBlockedByScope}>
              <Send size={18} />
            </button>
          </form>
          {questionBlockedByScope ? (
            <div className="scope-status">
              {paperScope === "selected" ? "Keine Paper ausgewählt" : "Kein aktives Paper"}
            </div>
          ) : null}
          <section className="answer-panel workspace-answer-panel">
            {activeTurn ? (
              <div className="answer-blocks">
                {activeBlocks.map((block, index) => (
                  <article className={`answer-block ${index > 0 ? "answer-block--followup" : ""}`} key={block.id}>
                    <div className="answer-question">{block.question}</div>
                    <div className="answer-text">
                      <AnswerText
                        answer={block.answer.answer}
                        citationLinks={block.answer.citation_links ?? []}
                        onCitationClick={(citation, context, quote, citationStart) =>
                          jumpToCitationIn(block.verification, citation, context, quote, block.answer.citation_links ?? [], citationStart)
                        }
                        getCitationMeta={(citation, context, citationStart) =>
                          citationMetasFor(block.verification, citation, context, block.answer.citation_links ?? [], citationStart)
                        }
                        activeCitation={selectedSource ? { paperId: selectedSource.paper_id, evidenceIndex: activeEvidenceIndex } : undefined}
                        onCitationInsert={(source, evidenceIndex, quote) => insertCitationFromAnswer(source, evidenceIndex, quote)}
                        onCitationInsertPreview={(source, evidenceIndex, quote) => previewCitationFromAnswer(source, evidenceIndex, quote)}
                        onCitationInsertPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
                      />
                    </div>
                    {block.answer.generation_error ? <div className="warning-row">{block.answer.generation_error}</div> : null}
                  </article>
                ))}
              </div>
            ) : (
              <EmptyState title="Keine Antwort" />
            )}
          </section>
          <div className="workspace-assistant-actions">
            <button
              className="button"
              type="button"
              onClick={appendAnswerToNote}
              onMouseEnter={previewAnswerToNote}
              onMouseLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onPointerEnter={previewAnswerToNote}
              onPointerLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onFocus={previewAnswerToNote}
              onBlur={() => notesActionsRef.current?.clearInsertPreview()}
              disabled={!answer}
            >
              <NotebookPen size={16} />
              <span>Antwort in Notiz</span>
            </button>
            <button
              className="button"
              type="button"
              onClick={() => appendActiveQuote("reference")}
              onMouseEnter={() => previewActiveQuote("reference")}
              onMouseLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onPointerEnter={() => previewActiveQuote("reference")}
              onPointerLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onFocus={() => previewActiveQuote("reference")}
              onBlur={() => notesActionsRef.current?.clearInsertPreview()}
              disabled={!activeEvidence || !selectedSource}
            >
              <Quote size={16} />
              <span>Zitat Z{activeEvidenceIndex + 1}</span>
            </button>
            <button
              className="button"
              type="button"
              onClick={() => appendActiveQuote("pdf")}
              onMouseEnter={() => previewActiveQuote("pdf")}
              onMouseLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onPointerEnter={() => previewActiveQuote("pdf")}
              onPointerLeave={() => notesActionsRef.current?.clearInsertPreview()}
              onFocus={() => previewActiveQuote("pdf")}
              onBlur={() => notesActionsRef.current?.clearInsertPreview()}
              disabled={!activeEvidence?.pdf_excerpt || !selectedSource}
            >
              <FilePlus2 size={16} />
              <span>PDF Z{activeEvidenceIndex + 1}</span>
            </button>
            <button className="button" type="button" onClick={openSelectedAssistantPdf} disabled={!selectedSource}>
              <FileText size={16} />
              <span>PDF-Nachweis öffnen</span>
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
                        style={evidenceColorVars(index)}
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
              <strong>{notesSnapshot.title || "Keine Notiz gewaehlt"}</strong>
            </div>
            <button className="icon-button" type="button" aria-label="Notizen einklappen" onClick={() => setNotesOpen(false)}>
              <PanelRightClose size={17} />
            </button>
          </div>
          <NotesSurface
            variant="workspace"
            controlledNoteId={controlledNoteId}
            requestedCitationId={requestedCitationId}
            onActiveNoteChange={handleActiveNoteChange}
            onCitationOpen={handleNoteCitationOpen}
            onStateChange={handleNotesStateChange}
            actionsRef={notesActionsRef}
          />
        </section>
      ) : (
        <CollapsedPane label="Notizen" icon={<PanelRightOpen size={17} />} onOpen={() => setNotesOpen(true)} />
      )}
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
      return {
        url: liveSource.pdf_available ? api.paperPdfUrl(liveSource.paper_id, liveSource.title) : null,
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
      return {
        url: api.paperPdfUrl(target.citation.paper_id, target.citation.title ?? ""),
        title: target.citation.title ?? target.citation.paper_id,
        evidences: noteCitationEvidence(target.citation),
        activeEvidenceIndex: 0
      };
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
  pdfTarget,
  activeAssistantSource,
  activeAssistantEvidenceIndex,
  selectedPaperIds,
  pdfCitationListHeight,
  sessions,
  activeSessionId,
  onCreateNote,
  onSelectNote,
  onOpenCitation,
  onOpenPaper,
  onToggleScopedPaper,
  onResizeCitationList,
  onOpenAssistantPdf,
  onActivateSession
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
  pdfTarget: WorkspacePdfTarget | null;
  activeAssistantSource: VerificationSource | null;
  activeAssistantEvidenceIndex: number;
  selectedPaperIds: string[];
  pdfCitationListHeight: number;
  sessions: AssistantTurn[];
  activeSessionId: string;
  onCreateNote: () => void;
  onSelectNote: (noteId: string) => void;
  onOpenCitation: (citation: NoteCitation) => void;
  onOpenPaper: (paper: Paper) => void;
  onToggleScopedPaper: (paperId: string) => void;
  onResizeCitationList: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onOpenAssistantPdf: () => void;
  onActivateSession: (turnId: string) => void;
}) {
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
            <button
              className={`list-row note-list-row ${activeNoteId === note.id ? "list-row--active" : ""}`}
              type="button"
              key={note.id}
              onClick={() => onSelectNote(note.id)}
            >
              <strong>{note.title}</strong>
              <span>{note.excerpt || "Leer"}</span>
              <small>{note.citation_count ?? 0} Quellen</small>
            </button>
          ))}
          {!notes.length ? <EmptyState title={notesLoading ? "Lade Notizen" : "Noch keine Notizen"} /> : null}
        </div>
      </section>
    );
  }
  if (tab === "pdfs") {
    const activeCitationId = selectedCitation?.id ?? (pdfTarget?.kind === "noteCitation" ? pdfTarget.citation.id : "");
    const targetPaperId = pdfTarget?.kind === "paper" ? workspacePaperId(pdfTarget.paper) : pdfTarget?.kind === "noteCitation" ? pdfTarget.citation.paper_id : pdfTarget?.kind === "assistant" ? pdfTarget.source.paper_id : "";
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
    return (
      <section className="workspace-nav-body">
        {pdfTarget?.kind === "assistant" && activeAssistantSource && activeAssistantEvidence ? (
          <div className="workspace-active-source" style={evidenceColorVars(activeAssistantEvidenceIndex)}>
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
          <div className="workspace-active-source" style={evidenceColorVars(selectedCitationIndex)}>
            <span>Aktive Notizquelle</span>
            <strong>
              Z{selectedCitationIndex + 1} - {selectedCitation.title || selectedCitation.paper_id}
            </strong>
            <p>{selectedCitation.pdf_excerpt || selectedCitation.reference_text || selectedCitation.paper_id}</p>
          </div>
        ) : null}
        <div className="workspace-nav-subheading">
          <span>Notizquellen</span>
        </div>
        <div className="list workspace-nav-list workspace-nav-list--short" style={{ maxHeight: pdfCitationListHeight }}>
          {citations.map(({ citation, badge, title, evidence }, index) => (
            <button
              className={`list-row note-citation-row ${activeCitationId === citation.id ? "note-citation-row--active list-row--active" : ""}`}
              type="button"
              key={citation.id}
              onClick={() => onOpenCitation(citation)}
              style={evidenceColorVars(Number(citation.evidence_index ?? index))}
            >
              <span className="note-citation-row__title">
                <span className="citation-badge">{badge}</span>
                <strong>{title}</strong>
              </span>
              <span>{evidence || citation.paper_id}</span>
            </button>
          ))}
          {!citations.length ? <div className="muted-row">Keine Quellen in der aktiven Notiz</div> : null}
        </div>
        <div className="workspace-list-resize-handle" role="separator" aria-label="Notizquellen Hoehe anpassen" onPointerDown={onResizeCitationList} />
        <div className="workspace-nav-subheading">
          <span>Projekt-PDFs</span>
        </div>
        <div className="list workspace-nav-list">
          {papers.map((paper) => {
            const normalizedPaper = normalizeWorkspacePaper(paper);
            const paperId = workspacePaperId(normalizedPaper);
            const title = workspacePaperTitle(normalizedPaper);
            const selectedForScope = Boolean(paperId && selectedPaperIds.includes(paperId));
            const isActivePaper = Boolean(paperId && activePaperId === paperId);
            return (
              <div
                className={`list-row workspace-paper-row ${isActivePaper ? "workspace-paper-row--active-source list-row--active" : ""}`}
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
                style={isActivePaper ? evidenceColorVars(activePaperEvidenceIndex) : undefined}
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
                  <strong>{title}</strong>
                  <span>{[paperId || "keine ID", normalizedPaper.year ?? ""].filter(Boolean).join(" - ")}</span>
                </div>
              </div>
            );
          })}
          {!papers.length ? <EmptyState title={papersLoading ? "Lade PDFs" : "Keine PDFs"} /> : null}
        </div>
      </section>
    );
  }
  return (
    <section className="workspace-nav-body">
      <div className="list workspace-nav-list">
        {sessions.map((turn) => (
          <button
            className={`assistant-history-item workspace-session-item ${activeSessionId === turn.id ? "assistant-history-item--active" : ""}`}
            type="button"
            key={turn.id}
            onClick={() => onActivateSession(turn.id)}
          >
            <span>{turn.question}</span>
            <small>
              {formatTurnTime(turn.createdAt)}
              {turnBlocks(turn).length > 1 ? ` | ${turnBlocks(turn).length} Antworten` : ""}
            </small>
          </button>
        ))}
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
  onCollapse
}: {
  eyebrow?: string;
  title: string;
  status?: string;
  collapseSide: "left" | "right";
  onCollapse: () => void;
}) {
  return (
    <div className="pane-heading workspace-pane-heading">
      <div>
        {eyebrow ? <span>{eyebrow}</span> : null}
        <strong>{title}</strong>
      </div>
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

function activeScopePaperId(target: WorkspacePdfTarget | null, activeAssistantSource: VerificationSource | null) {
  if (target) {
    if (target.kind === "assistant") {
      return target.source.paper_id;
    }
    if (target.kind === "noteCitation") {
      return target.citation.paper_id;
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
