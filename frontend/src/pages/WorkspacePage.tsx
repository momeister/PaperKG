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
  MessageSquareText,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
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
  bestEvidenceIndex,
  citationIds,
  cleanAnswerQuote,
  formatAnswerForNote,
  formatNoteQuote,
  formatTurnTime,
  loadAssistantSession,
  mergeVerification,
  noteCitation,
  sameCitation,
  saveAssistantSession,
  shortEvidenceText,
  turnBlocks,
  turnContext,
  verificationLimits
} from "./AssistantPage";
import { NotesSurface } from "./NotesPage";
import type { NotesSurfaceActions, NotesSurfaceSnapshot } from "./NotesPage";

export type WorkspaceNavigatorTab = "notes" | "pdfs" | "noteAi" | "assistantSessions";

export type WorkspacePdfTarget =
  | { kind: "assistant"; source: VerificationSource; evidenceIndex: number }
  | { kind: "noteCitation"; citation: NoteCitation }
  | { kind: "paper"; paper: Paper };

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

  const [navigatorOpen, setNavigatorOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "navigatorOpen", true));
  const [assistantOpen, setAssistantOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "assistantOpen", true));
  const [pdfOpen, setPdfOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "pdfOpen", true));
  const [notesOpen, setNotesOpen] = useState(() => loadWorkspaceBoolean(scopedProjectId, "notesOpen", true));
  const [navigatorWidth, setNavigatorWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "navigatorWidth.v3", 180));
  const [assistantWidth, setAssistantWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "assistantWidth.v3", 420));
  const [pdfWidth, setPdfWidth] = useState(() => loadWorkspaceNumber(scopedProjectId, "pdfWidth.v3", 220));
  const [pdfCitationLimit, setPdfCitationLimit] = useState(() => loadWorkspaceNumber(scopedProjectId, "pdfCitationLimit", 8));
  const [pdfPaperLimit, setPdfPaperLimit] = useState(() => loadWorkspaceNumber(scopedProjectId, "pdfPaperLimit", 12));
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

  const answerMutation = useMutation({
    mutationFn: (value: string) =>
      api.answer({
        question: value,
        provider,
        model,
        limit: answerLimitFor(value, evidenceMode),
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
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "pdfCitationLimit", pdfCitationLimit), [pdfCitationLimit, scopedProjectId]);
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "pdfPaperLimit", pdfPaperLimit), [pdfPaperLimit, scopedProjectId]);
  useEffect(() => saveWorkspaceNumber(scopedProjectId, "pdfCitationListHeight", pdfCitationListHeight), [pdfCitationListHeight, scopedProjectId]);

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
    if (question.trim()) {
      answerMutation.mutate(question.trim());
    }
  }

  function openAssistantSource(source: VerificationSource | null, evidenceIndex = 0, quote = "", options: { openPdf?: boolean } = {}) {
    setSelectedAnswerQuote(null);
    if (!source) {
      setSelectedSource(null);
      setActiveEvidenceIndex(0);
      return;
    }
    const nextIndex = Math.max(0, Math.min(evidenceIndex, source.evidence.length - 1));
    setSelectedSource(source);
    setActiveEvidenceIndex(nextIndex);
    if (options.openPdf) {
      setPdfTarget({ kind: "assistant", source, evidenceIndex: nextIndex });
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

  function citationMetaFor(pool: VerificationSource[], citation: string, context = "") {
    const candidates = citationIds(citation);
    const source = pool.find((item) => candidates.some((candidate) => sameCitation(item.paper_id, candidate)));
    if (source) {
      return { source, evidenceIndex: bestEvidenceIndex(source, context) };
    }
    return null;
  }

  function jumpToCitationIn(pool: VerificationSource[], citation: string, context = "", quote = "") {
    const meta = citationMetaFor(pool, citation, context);
    if (meta) {
      openAssistantSource(meta.source, meta.evidenceIndex, quote || context);
    }
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
            <button type="button" className={navigatorTab === "noteAi" ? "active" : ""} onClick={() => setNavigatorTab("noteAi")}>
              <Sparkles size={15} />
              <span>KI-Notizen</span>
              <strong>{notesSnapshot.threads.length}</strong>
            </button>
            <button type="button" className={navigatorTab === "assistantSessions" ? "active" : ""} onClick={() => setNavigatorTab("assistantSessions")}>
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
            pdfCitationLimit={pdfCitationLimit}
            pdfPaperLimit={pdfPaperLimit}
            pdfCitationListHeight={pdfCitationListHeight}
            threads={notesSnapshot.threads}
            activeThreadId={notesSnapshot.activeThreadId}
            threadMeta={notesSnapshot.threadMeta}
            followUpDrafts={notesSnapshot.followUpDrafts}
            isFollowUpPending={notesSnapshot.isFollowUpPending}
            deletingThreadId={notesSnapshot.deletingThreadId}
            sessions={history}
            activeSessionId={activeTurnId}
            onCreateNote={() => notesActionsRef.current?.createNote()}
            onSelectNote={(noteId) => {
              setControlledNoteId(noteId);
              notesActionsRef.current?.selectNote(noteId);
              setNavigatorTab("notes");
            }}
            onOpenCitation={(citation) => {
              setRequestedCitationId(citation.id);
              notesActionsRef.current?.openCitation(citation);
              setPdfTarget({ kind: "noteCitation", citation });
              setPdfOpen(true);
            }}
            onOpenPaper={(paper) => {
              setPdfTarget({ kind: "paper", paper });
              setPdfOpen(true);
            }}
            onCitationLimitChange={(value) => setPdfCitationLimit(clampWorkspaceNumber(value, 1, 50))}
            onPaperLimitChange={(value) => setPdfPaperLimit(clampWorkspaceNumber(value, 1, 100))}
            onResizeCitationList={(event) => startVerticalResize(event, pdfCitationListHeight, setPdfCitationListHeight, pdfCitationResizeFrameRef, 90, 520)}
            onOpenAssistantPdf={openSelectedAssistantPdf}
            onActivateThread={(threadId) => notesActionsRef.current?.activateThread(threadId)}
            onToggleThread={(threadId) => notesActionsRef.current?.toggleThreadCollapsed(threadId)}
            onDraftChange={(threadId, value) => notesActionsRef.current?.setFollowUpDraft(threadId, value)}
            onFollowUp={(threadId) => notesActionsRef.current?.submitFollowUp(threadId)}
            onInsertThread={(threadId) => notesActionsRef.current?.insertThreadAnswer(threadId)}
            onPreviewThread={(threadId) => notesActionsRef.current?.previewThreadAnswer(threadId)}
            onInsertThreadMessage={(threadId, messageId) => notesActionsRef.current?.insertThreadMessage(threadId, messageId)}
            onPreviewThreadMessage={(threadId, messageId) => notesActionsRef.current?.previewThreadMessage(threadId, messageId)}
            onHideThreadMessage={(threadId, messageId) => notesActionsRef.current?.hideThreadMessage(threadId, messageId)}
            onPreviewClear={() => notesActionsRef.current?.clearInsertPreview()}
            onDeleteThread={(threadId) => notesActionsRef.current?.deleteThread(threadId)}
            onDeleteAllThreads={() => notesActionsRef.current?.deleteAllThreads()}
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
        <section className="workspace-assistant-pane">
          <PaneHeading eyebrow="Grounded KG" title="Assistant" onCollapse={() => setAssistantOpen(false)} collapseSide="left" status={answerMutation.isPending ? "running" : answer?.generation_error ? "warning" : "idle"} />
          <form className="chat-box" onSubmit={submit}>
            <Bot size={20} />
            <input value={question} onChange={(event) => setQuestion(event.target.value)} placeholder="Frage an den lokalen KG" />
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
            <button className="icon-button" aria-label="Senden" disabled={answerMutation.isPending}>
              <Send size={18} />
            </button>
          </form>
          <section className="answer-panel workspace-answer-panel">
            {activeTurn ? (
              <div className="answer-blocks">
                {activeBlocks.map((block, index) => (
                  <article className={`answer-block ${index > 0 ? "answer-block--followup" : ""}`} key={block.id}>
                    <div className="answer-question">{block.question}</div>
                    <div className="answer-text">
                      <AnswerText
                        answer={block.answer.answer}
                        onCitationClick={(citation, context, quote) => jumpToCitationIn(block.verification, citation, context, quote)}
                        getCitationMeta={(citation, context) => citationMetaFor(block.verification, citation, context)}
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
      return {
        url: target.source.pdf_available ? api.paperPdfUrl(target.source.paper_id, target.source.title) : null,
        title: target.source.title || target.source.paper_id,
        evidences: target.source.evidence,
        activeEvidenceIndex: target.evidenceIndex,
        onActiveEvidenceChange: (index) => {
          setActiveEvidenceIndex(index);
          setPdfTarget({ kind: "assistant", source: target.source, evidenceIndex: index });
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
      url: target.paper.has_full_text ? api.paperPdfUrl(target.paper.id, target.paper.title) : null,
      title: target.paper.title || target.paper.id,
      evidences: [],
      activeEvidenceIndex: 0
    };
  }
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
  pdfCitationLimit,
  pdfPaperLimit,
  pdfCitationListHeight,
  threads,
  activeThreadId,
  threadMeta,
  followUpDrafts,
  isFollowUpPending,
  deletingThreadId,
  sessions,
  activeSessionId,
  onCreateNote,
  onSelectNote,
  onOpenCitation,
  onOpenPaper,
  onCitationLimitChange,
  onPaperLimitChange,
  onResizeCitationList,
  onOpenAssistantPdf,
  onActivateThread,
  onToggleThread,
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
  pdfCitationLimit: number;
  pdfPaperLimit: number;
  pdfCitationListHeight: number;
  threads: NoteAiThread[];
  activeThreadId: string;
  threadMeta: NotesSurfaceSnapshot["threadMeta"];
  followUpDrafts: Record<string, string>;
  isFollowUpPending: boolean;
  deletingThreadId: string;
  sessions: AssistantTurn[];
  activeSessionId: string;
  onCreateNote: () => void;
  onSelectNote: (noteId: string) => void;
  onOpenCitation: (citation: NoteCitation) => void;
  onOpenPaper: (paper: Paper) => void;
  onCitationLimitChange: (value: number) => void;
  onPaperLimitChange: (value: number) => void;
  onResizeCitationList: (event: ReactPointerEvent<HTMLDivElement>) => void;
  onOpenAssistantPdf: () => void;
  onActivateThread: (threadId: string) => void;
  onToggleThread: (threadId: string) => void;
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
    const activePaperId =
      activeAssistantSource?.paper_id ??
      (pdfTarget?.kind === "paper" ? pdfTarget.paper.id : pdfTarget?.kind === "noteCitation" ? pdfTarget.citation.paper_id : "");
    const activeAssistantEvidence = activeAssistantSource?.evidence[activeAssistantEvidenceIndex];
    const selectedCitationIndex = Number(selectedCitation?.evidence_index ?? 0);
    const visibleCitations = citations.slice(0, pdfCitationLimit);
    const visiblePapers = papers.slice(0, pdfPaperLimit);
    return (
      <section className="workspace-nav-body">
        {activeAssistantSource && activeAssistantEvidence ? (
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
          <label className="workspace-count-control">
            <input
              type="number"
              min={1}
              max={50}
              value={pdfCitationLimit}
              onChange={(event) => onCitationLimitChange(Number(event.target.value))}
              aria-label="Anzahl Notizquellen"
            />
            <strong>{citations.length}</strong>
          </label>
        </div>
        <div className="list workspace-nav-list workspace-nav-list--short" style={{ maxHeight: pdfCitationListHeight }}>
          {visibleCitations.map(({ citation, badge, title, evidence }, index) => (
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
          <label className="workspace-count-control">
            <input
              type="number"
              min={1}
              max={100}
              value={pdfPaperLimit}
              onChange={(event) => onPaperLimitChange(Number(event.target.value))}
              aria-label="Anzahl Projekt-PDFs"
            />
            <strong>{papers.length}</strong>
          </label>
        </div>
        <div className="list workspace-nav-list">
          {visiblePapers.map((paper) => (
            <button className={`list-row note-list-row ${activePaperId === paper.id ? "list-row--active" : ""}`} type="button" key={paper.id} onClick={() => onOpenPaper(paper)}>
              <strong>{paper.title || paper.id}</strong>
              <span>{paper.id}</span>
              <small>{paper.year ?? "n/a"}</small>
            </button>
          ))}
          {!papers.length ? <EmptyState title={papersLoading ? "Lade PDFs" : "Keine PDFs"} /> : null}
        </div>
      </section>
    );
  }
  if (tab === "noteAi") {
    return (
      <section className="workspace-nav-body">
        <div className="workspace-nav-toolbar">
          <span>{threads.length} KI-Notizen</span>
          <button className="button button-compact" type="button" disabled={!threads.length} onClick={onDeleteAllThreads}>
            <Trash2 size={15} />
            <span>Alle</span>
          </button>
        </div>
        <div className="ai-thread-list workspace-nav-list">
          {threads.map((thread) => {
            const answer = latestThreadAnswer(thread);
            const meta = threadMeta.get(thread.id);
            const collapsed = threadCollapsed(thread);
            const messages = threadMessages(thread);
            return (
              <article
                className={`note-thread-row ai-thread-card ${meta ? "ai-thread-card--anchored" : ""} ${collapsed ? "ai-thread-card--compact" : ""} ${activeThreadId === thread.id ? "ai-thread-card--active" : ""}`}
                key={thread.id}
                style={meta ? evidenceColorVars(meta.colorIndex) : undefined}
              >
                <div className="ai-thread-topline">
                  <button className="ai-thread-header" type="button" onClick={() => onActivateThread(thread.id)}>
                    <span className="ai-thread-header-line">
                      {meta ? <span className="ai-thread-anchor-badge">{meta.label}</span> : null}
                      <strong>{thread.instruction}</strong>
                    </span>
                  </button>
                  <div className="ai-thread-actions">
                    <button className="button button-compact" type="button" onClick={() => onToggleThread(thread.id)}>
                      {collapsed ? "Oeffnen" : "Einklappen"}
                    </button>
                    <button
                      className="button button-compact"
                      type="button"
                      onClick={() => onInsertThread(thread.id)}
                      onMouseEnter={() => onPreviewThread(thread.id)}
                      onMouseLeave={onPreviewClear}
                      onPointerEnter={() => onPreviewThread(thread.id)}
                      onPointerLeave={onPreviewClear}
                      onFocus={() => onPreviewThread(thread.id)}
                      onBlur={onPreviewClear}
                      disabled={!answer}
                    >
                      Einfuegen
                    </button>
                    <button className="icon-button icon-button--compact" type="button" aria-label="KI-Verlauf loeschen" disabled={deletingThreadId === thread.id} onClick={() => onDeleteThread(thread.id)}>
                      <Trash2 size={15} />
                    </button>
                  </div>
                </div>
                <div className="ai-thread-preview">
                  <p>{shortThreadContext(thread.anchor_quote || thread.selected_text)}</p>
                  {answer ? <p className="ai-thread-answer-preview">{shortThreadContext(answer)}</p> : null}
                </div>
                {!collapsed ? (
                  <>
                    <div className="ai-thread-messages">
                      {messages.map((message) => (
                        <div className={`ai-thread-message ai-thread-message--${message.role === "assistant" ? "assistant" : "user"}`} key={message.id}>
                          <div className="ai-thread-message-topline">
                            <span>{message.role === "assistant" ? "KI" : "Du"}</span>
                            {message.role === "assistant" ? (
                              <div className="ai-thread-message-actions">
                                <button
                                  className="button button-compact"
                                  type="button"
                                  onClick={() => onInsertThreadMessage(thread.id, message.id)}
                                  onMouseEnter={() => onPreviewThreadMessage(thread.id, message.id)}
                                  onMouseLeave={onPreviewClear}
                                  onPointerEnter={() => onPreviewThreadMessage(thread.id, message.id)}
                                  onPointerLeave={onPreviewClear}
                                  onFocus={() => onPreviewThreadMessage(thread.id, message.id)}
                                  onBlur={onPreviewClear}
                                >
                                  Einfuegen
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
          {!threads.length ? <div className="muted-row">Noch keine KI-Fragen</div> : null}
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
  eyebrow: string;
  title: string;
  status?: string;
  collapseSide: "left" | "right";
  onCollapse: () => void;
}) {
  return (
    <div className="pane-heading workspace-pane-heading">
      <div>
        <span>{eyebrow}</span>
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
    left.threadMeta === right.threadMeta &&
    left.followUpDrafts === right.followUpDrafts &&
    left.isFollowUpPending === right.isFollowUpPending &&
    left.deletingThreadId === right.deletingThreadId
  );
}

function noteCitationEvidence(citation: NoteCitation): VerificationEvidence[] {
  return [
    {
      paper_id: citation.paper_id,
      kind: citation.kind || "note",
      reference_text: citation.reference_text || "",
      pdf_excerpt: citation.pdf_excerpt || "",
      matched_terms: textTerms(`${citation.reference_text ?? ""} ${citation.pdf_excerpt ?? ""}`),
      found_in_pdf_text: Boolean(citation.pdf_excerpt)
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

function shortThreadContext(value: string) {
  const text = String(value || "").replace(/^==([\s\S]*)==$/, "$1").replace(/\s+/g, " ").trim();
  return text.length <= 170 ? text : `${text.slice(0, 167)}...`;
}

function textTerms(text: string) {
  return Array.from(new Set(text.toLowerCase().replace(/[^a-z0-9-]+/g, " ").split(" "))).filter((term) => term.length >= 5).slice(0, 12);
}

function normalizeFilter(value: string) {
  return value.toLowerCase().replace(/\s+/g, " ").trim();
}

function clampWorkspaceNumber(value: number, min: number, max: number) {
  if (!Number.isFinite(value)) {
    return min;
  }
  return Math.min(max, Math.max(min, Math.round(value)));
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
