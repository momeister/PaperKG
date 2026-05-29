import { useEffect, useMemo, useRef, useState } from "react";
import type { FormEvent, KeyboardEvent as ReactKeyboardEvent, PointerEvent as ReactPointerEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bold,
  Bot,
  ChevronDown,
  ChevronUp,
  Code,
  Download,
  FilePlus2,
  Highlighter,
  Italic,
  Link,
  List,
  ListChecks,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Quote,
  Send,
  Table2,
  WandSparkles
} from "lucide-react";

import { api } from "../api";
import { evidenceColorVars } from "../citationColors";
import { EmptyState } from "../components/EmptyState";
import { PdfPane } from "../components/PdfPane";
import { Status } from "../components/Status";
import { TextareaHighlightLayer } from "../components/TextareaHighlightLayer";
import { downloadMarkdownFile } from "../download";
import { noteProjectId } from "../projectScope";
import { useAppState } from "../state";
import type { Answer, Note, VerificationSource } from "../types";

type AssistantAnswerBlock = {
  id: string;
  question: string;
  answer: Answer;
  verification: VerificationSource[];
  createdAt: string;
};

type AssistantTurn = {
  id: string;
  question: string;
  answer: Answer;
  verification: VerificationSource[];
  createdAt: string;
  blocks?: AssistantAnswerBlock[];
};

type NoteSelectionRange = {
  start: number;
  end: number;
  text: string;
};

type SelectedAnswerQuote = {
  paperId: string;
  evidenceIndex: number;
  text: string;
};

export function AssistantPage() {
  const { activeProject, provider, model } = useAppState();
  const scopedProjectId = noteProjectId(activeProject);
  const queryClient = useQueryClient();
  const assistantScopeRef = useRef(scopedProjectId);
  const latestSidecarRef = useRef({ noteId: "", title: "", markdown: "" });
  const splitFrameRef = useRef<number | null>(null);
  const notesResizeFrameRef = useRef<number | null>(null);
  const [question, setQuestion] = useState("");
  const [history, setHistory] = useState<AssistantTurn[]>(() => loadAssistantSession(scopedProjectId).history);
  const [activeTurnId, setActiveTurnId] = useState(() => loadAssistantSession(scopedProjectId).activeTurnId);
  const [selectedSource, setSelectedSource] = useState<VerificationSource | null>(null);
  const [activeEvidenceIndex, setActiveEvidenceIndex] = useState(0);
  const [split, setSplit] = useState(52);
  const [notesWidth, setNotesWidth] = useState(520);
  const [evidenceOpen, setEvidenceOpen] = useState(true);
  const [assistantOpen, setAssistantOpen] = useState(true);
  const [pdfOpen, setPdfOpen] = useState(true);
  const [notesOpen, setNotesOpen] = useState(true);
  const [evidenceMode, setEvidenceMode] = useState("auto");
  const [conversationMode, setConversationMode] = useState<"followup" | "new">("followup");
  const [workspaceMode, setWorkspaceMode] = useState<"questions" | "notes">("questions");
  const [notes, setNotes] = useState(() => loadNotes(scopedProjectId));
  const [notesDirty, setNotesDirty] = useState(false);
  const [targetNoteId, setTargetNoteId] = useState("");
  const [newNoteTitle, setNewNoteTitle] = useState("Neue Notiz");
  const [rewriteMode, setRewriteMode] = useState("klarer");
  const [noteStatus, setNoteStatus] = useState("");
  const [selectedAnswerQuote, setSelectedAnswerQuote] = useState<SelectedAnswerQuote | null>(null);
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
  const notesQuery = useQuery({
    queryKey: ["notes", scopedProjectId],
    queryFn: () => api.listNotes(scopedProjectId)
  });
  const selectedTargetNote = useMemo(
    () => (targetNoteId ? notesQuery.data?.items.find((note) => note.id === targetNoteId) : undefined),
    [notesQuery.data?.items, targetNoteId]
  );

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
        setSelectedSource(sources[0] ?? verification[0] ?? null);
        setActiveEvidenceIndex(0);
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
      setSelectedSource(sources[0] ?? null);
      setActiveEvidenceIndex(0);
    }
  });
  const rewriteMutation = useMutation({
    mutationFn: () =>
      api.rewriteNote({
        text: notes.trim(),
        instruction: rewriteInstruction(rewriteMode),
        provider,
        model
    }),
    onSuccess: (payload) => {
      updateNotesDraft(payload.text);
      setNoteStatus("Umschrieben");
    },
    onError: (error) => {
      setNoteStatus(error instanceof Error ? error.message : "Umschreiben fehlgeschlagen");
    }
  });
  const appendPersistentNote = useMutation({
    mutationFn: async (payload: { markdown: string; citations?: Record<string, unknown>[] }) => {
      const title = noteTitleForSave(newNoteTitle, payload.markdown);
      const noteId = targetNoteId || (await api.createNote(scopedProjectId, { title, markdown: "" })).note.id;
      const result = await api.appendNote(noteId, payload);
      return result.note;
    },
    onSuccess: (note) => {
      setTargetNoteId(note.id);
      setNewNoteTitle(note.title);
      setNotes(note.markdown);
      setNotesDirty(false);
      saveNotes(scopedProjectId, "");
      setNoteStatus("In Notiz gespeichert");
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      queryClient.invalidateQueries({ queryKey: ["note", note.id] });
    },
    onError: (error) => {
      setNoteStatus(error instanceof Error ? error.message : "Speichern fehlgeschlagen");
    }
  });
  const saveSidecarNote = useMutation({
    mutationFn: async (payload: { noteId?: string; title: string; markdown: string }) => {
      if (payload.noteId) {
        return (await api.updateNote(payload.noteId, { title: payload.title, markdown: payload.markdown })).note;
      }
      return (await api.createNote(scopedProjectId, { title: payload.title, markdown: payload.markdown })).note;
    },
    onSuccess: (note, variables) => {
      setTargetNoteId(note.id);
      setNewNoteTitle(note.title);
      const latest = latestSidecarRef.current;
      if ((latest.noteId === note.id || (!variables.noteId && !latest.noteId)) && latest.markdown === variables.markdown) {
        setNotesDirty(false);
      }
      if (!variables.noteId) {
        saveNotes(scopedProjectId, "");
      }
      setNoteStatus("");
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    },
    onError: (error) => {
      setNoteStatus(error instanceof Error ? error.message : "Autosave fehlgeschlagen");
    }
  });

  useEffect(() => {
    assistantScopeRef.current = scopedProjectId;
    const session = loadAssistantSession(scopedProjectId);
    setHistory(session.history);
    setActiveTurnId(session.activeTurnId);
    setQuestion("");
    setNotes(loadNotes(scopedProjectId));
    setNotesDirty(false);
    setTargetNoteId("");
    setNoteStatus("");
    setConversationMode("followup");
    setWorkspaceMode("questions");
  }, [scopedProjectId]);

  useEffect(() => {
    latestSidecarRef.current = { noteId: targetNoteId, title: newNoteTitle, markdown: notes };
  }, [newNoteTitle, notes, targetNoteId]);

  useEffect(() => {
    if (assistantScopeRef.current === scopedProjectId) {
      saveAssistantSession(scopedProjectId, { history, activeTurnId });
    }
  }, [activeTurnId, history, scopedProjectId]);

  useEffect(() => {
    if (!targetNoteId) {
      saveNotes(scopedProjectId, notes);
    }
  }, [notes, scopedProjectId, targetNoteId]);

  useEffect(() => {
    if (!activeTurn) {
      setSelectedSource(null);
      setActiveEvidenceIndex(0);
      return;
    }
    setSelectedSource((current) => {
      const matching = current ? verification.find((source) => source.paper_id === current.paper_id) : undefined;
      return matching ?? verification[0] ?? null;
    });
    setActiveEvidenceIndex(0);
  }, [activeTurn?.id, verification]);

  useEffect(() => {
    if (!selectedTargetNote || notesDirty) {
      return;
    }
    setNotes(selectedTargetNote.markdown ?? "");
    setNewNoteTitle(selectedTargetNote.title ?? "Neue Notiz");
  }, [notesDirty, selectedTargetNote?.id, selectedTargetNote?.markdown, selectedTargetNote?.title]);

  useEffect(() => {
    if (!notesDirty || !notes.trim()) {
      return;
    }
    const suggestedTitle = noteTitleForSave(newNoteTitle, notes);
    if (suggestedTitle !== newNoteTitle) {
      setNewNoteTitle(suggestedTitle);
    }
    const handle = window.setTimeout(() => {
      saveSidecarNote.mutate({
        noteId: targetNoteId || undefined,
        title: suggestedTitle,
        markdown: notes
      });
    }, 1400);
    return () => window.clearTimeout(handle);
  }, [newNoteTitle, notes, notesDirty, targetNoteId]);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (question.trim()) {
      answerMutation.mutate(question.trim());
    }
  }

  function startResize(event: ReactPointerEvent<HTMLDivElement>) {
    const startX = event.clientX;
    const startSplit = split;
    const move = (moveEvent: globalThis.PointerEvent) => {
      const delta = ((moveEvent.clientX - startX) / window.innerWidth) * 100;
      const nextSplit = Math.min(70, Math.max(35, startSplit + delta));
      if (splitFrameRef.current !== null) {
        window.cancelAnimationFrame(splitFrameRef.current);
      }
      splitFrameRef.current = window.requestAnimationFrame(() => {
        splitFrameRef.current = null;
        setSplit(nextSplit);
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function startNotesResize(event: ReactPointerEvent<HTMLDivElement>) {
    const startX = event.clientX;
    const startWidth = notesWidth;
    const move = (moveEvent: globalThis.PointerEvent) => {
      const delta = startX - moveEvent.clientX;
      const maxWidth = Math.max(360, Math.round(window.innerWidth * 0.72));
      const nextWidth = Math.min(maxWidth, Math.max(320, startWidth + delta));
      if (notesResizeFrameRef.current !== null) {
        window.cancelAnimationFrame(notesResizeFrameRef.current);
      }
      notesResizeFrameRef.current = window.requestAnimationFrame(() => {
        notesResizeFrameRef.current = null;
        setNotesWidth(nextWidth);
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  const sourceMeta = answer?.sources.find((source) => source.paper_id === selectedSource?.paper_id);
  const activeEvidence = selectedSource?.evidence[activeEvidenceIndex];

  function selectSource(source: VerificationSource, evidenceIndex = 0) {
    setSelectedAnswerQuote(null);
    setSelectedSource(source);
    setActiveEvidenceIndex(Math.max(0, Math.min(evidenceIndex, source.evidence.length - 1)));
  }

  function citationMeta(citation: string, context = "") {
    return citationMetaFor(verification, citation, context);
  }

  function citationMetaFor(pool: VerificationSource[], citation: string, context = "") {
    const candidates = citationIds(citation);
    const source = pool.find((item) => candidates.some((candidate) => sameCitation(item.paper_id, candidate)));
    if (source) {
      return { source, evidenceIndex: bestEvidenceIndex(source, context) };
    }
    return null;
  }

  function jumpToCitation(citation: string, context = "") {
    const meta = citationMeta(citation, context);
    if (meta) {
      selectSource(meta.source, meta.evidenceIndex);
    }
  }

  function jumpToCitationIn(pool: VerificationSource[], citation: string, context = "", quote = "") {
    const meta = citationMetaFor(pool, citation, context);
    if (meta) {
      selectSource(meta.source, meta.evidenceIndex);
      setSelectedAnswerQuote({
        paperId: meta.source.paper_id,
        evidenceIndex: meta.evidenceIndex,
        text: cleanAnswerQuote(quote || context)
      });
    }
  }

  function updateNotesDraft(value: string) {
    setNotes(value);
    setNotesDirty(true);
    setNoteStatus("");
  }

  function selectTargetNote(noteId: string) {
    setTargetNoteId(noteId);
    setNotesDirty(false);
    setNoteStatus("");
    if (!noteId) {
      setNewNoteTitle("Neue Notiz");
      setNotes(loadNotes(scopedProjectId));
      return;
    }
    const note = notesQuery.data?.items.find((item) => item.id === noteId);
    if (note) {
      setNewNoteTitle(note.title ?? "Neue Notiz");
      setNotes(note.markdown ?? "");
    }
  }

  function appendNote(text: string) {
    if (!text.trim()) {
      return;
    }
    const next = notes.trim() ? `${notes.trimEnd()}\n\n${text.trim()}` : text.trim();
    updateNotesDraft(next);
    setNoteStatus("");
  }

  function appendToProjectNote(text: string, citations: Record<string, unknown>[] = []) {
    if (!text.trim()) {
      return;
    }
    appendPersistentNote.mutate({ markdown: text.trim(), citations });
  }

  async function askSidecarSelection(selection: NoteSelectionRange, instruction: string) {
    const title = noteTitleForSave(newNoteTitle, notes);
    let noteId = targetNoteId;
    let savedNote: Note | null = null;
    if (noteId) {
      savedNote = (await api.updateNote(noteId, { title, markdown: notes })).note;
    } else {
      savedNote = (await api.createNote(scopedProjectId, { title, markdown: notes })).note;
      noteId = savedNote.id;
      saveNotes(scopedProjectId, "");
    }
    setTargetNoteId(savedNote.id);
    setNewNoteTitle(savedNote.title);
    setNotes(savedNote.markdown);
    setNotesDirty(false);
    queryClient.setQueryData(["note", savedNote.id], { note: savedNote });
    queryClient.invalidateQueries({ queryKey: ["notes"] });
    const payload = await api.createNoteAiThread(savedNote.id, {
      selected_text: stripHighlightMarkers(selection.text),
      instruction,
      provider,
      model,
      use_kg_evidence: true,
      anchor_start: selection.start,
      anchor_end: selection.end,
      anchor_quote: stripHighlightMarkers(selection.text).slice(0, 2000)
    });
    queryClient.invalidateQueries({ queryKey: ["note-ai-threads", savedNote.id] });
    return payload.replacement_text;
  }

  function insertActiveQuote(source: "reference" | "pdf") {
    if (!selectedSource || !activeEvidence) {
      return;
    }
    const answerQuote =
      selectedAnswerQuote?.paperId === selectedSource.paper_id && selectedAnswerQuote.evidenceIndex === activeEvidenceIndex
        ? selectedAnswerQuote.text
        : "";
    const quote = source === "pdf" ? activeEvidence.pdf_excerpt || activeEvidence.reference_text : answerQuote || activeEvidence.reference_text;
    const citation = noteCitation(selectedSource, activeEvidence, activeEvidenceIndex);
    appendToProjectNote(formatNoteQuote(quote, selectedSource, activeEvidenceIndex, citation.id), [citation]);
  }

  const activeAnswerQuote =
    selectedAnswerQuote && selectedAnswerQuote.paperId === selectedSource?.paper_id && selectedAnswerQuote.evidenceIndex === activeEvidenceIndex
      ? selectedAnswerQuote.text
      : "";
  const activeQuoteText = activeAnswerQuote || activeEvidence?.reference_text || "";
  const assistantColumn = assistantOpen ? (workspaceMode === "notes" ? "minmax(330px, 34%)" : `${split}%`) : "46px";
  const pdfColumn = pdfOpen ? (workspaceMode === "notes" ? "minmax(280px, 28%)" : "minmax(0, 1fr)") : "46px";
  const notesColumn = notesOpen ? `minmax(320px, ${notesWidth}px)` : "46px";

  return (
    <section
      className={`assistant-layout ${notesOpen ? "assistant-layout--notes-open" : ""} assistant-layout--${workspaceMode}`}
      style={{
        gridTemplateColumns: `${assistantColumn} 6px ${pdfColumn} 6px ${notesColumn}`
      }}
    >
      {assistantOpen ? (
      <div className="assistant-left">
        <div className="page-title compact">
          <div>
            <span>Grounded KG</span>
            <h1>Assistant</h1>
          </div>
          <div className="button-row">
            <Status value={answerMutation.isPending ? "running" : answer?.generation_error ? "warning" : "idle"} />
            <button className="icon-button" type="button" aria-label="Assistant einklappen" onClick={() => setAssistantOpen(false)}>
              <PanelLeftClose size={17} />
            </button>
          </div>
        </div>

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

        <section className="answer-panel">
          {activeTurn ? (
            <>
              <div className="assistant-history">
                <div className="assistant-history-heading">
                  <span>Verlauf</span>
                  <strong>{history.length}</strong>
                </div>
                <div className="assistant-history-list">
                  {history.map((turn) => (
                    <button
                      key={turn.id}
                      type="button"
                      className={`assistant-history-item ${turn.id === activeTurn.id ? "assistant-history-item--active" : ""}`}
                      onClick={() => setActiveTurnId(turn.id)}
                    >
                      <span>{turn.question}</span>
                      <small>
                        {formatTurnTime(turn.createdAt)}
                        {turnBlocks(turn).length > 1 ? ` | ${turnBlocks(turn).length} Antworten` : ""}
                      </small>
                    </button>
                  ))}
                </div>
              </div>
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
            </>
          ) : (
            <EmptyState title="Keine Antwort" />
          )}
        </section>

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
                  onClick={() => selectSource(source)}
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
                  onClick={() => setActiveEvidenceIndex(index)}
                  onPointerDown={() => setSelectedAnswerQuote(null)}
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
      </div>
      ) : (
        <aside className="assistant-collapsed-panel">
          <button className="collapsed-panel-tab" type="button" onClick={() => setAssistantOpen(true)}>
            <PanelLeftOpen size={17} />
            <span>Assistant</span>
          </button>
        </aside>
      )}
      <div className={`split-handle ${assistantOpen && pdfOpen ? "" : "split-handle--idle"}`} onPointerDown={assistantOpen && pdfOpen ? startResize : undefined} />
      {pdfOpen ? (
        <PdfPane
          url={selectedSource?.pdf_available ? api.paperPdfUrl(selectedSource.paper_id, sourceMeta?.title ?? selectedSource.title) : null}
          title={selectedSource?.title}
          evidences={selectedSource?.evidence ?? []}
          activeEvidenceIndex={activeEvidenceIndex}
          onActiveEvidenceChange={setActiveEvidenceIndex}
          onCollapse={() => setPdfOpen(false)}
        />
      ) : (
        <aside className="assistant-collapsed-panel">
          <button className="collapsed-panel-tab" type="button" onClick={() => setPdfOpen(true)}>
            <PanelRightOpen size={17} />
          <span>PDF</span>
          </button>
        </aside>
      )}
      <div
        className={`split-handle ${notesOpen ? "" : "split-handle--idle"}`}
        onPointerDown={notesOpen ? startNotesResize : undefined}
        aria-label="Notizen vergroessern oder verkleinern"
      />
      <NotesSidecar
        open={notesOpen}
        onOpenChange={setNotesOpen}
        answer={answer}
        activeEvidence={activeEvidence}
        selectedSource={selectedSource}
        activeEvidenceIndex={activeEvidenceIndex}
        notes={notes}
        setNotes={updateNotesDraft}
        noteStatus={noteStatus}
        isRewriting={rewriteMutation.isPending}
        isAutosaving={saveSidecarNote.isPending}
        rewriteMode={rewriteMode}
        setRewriteMode={setRewriteMode}
        notesList={notesQuery.data?.items ?? []}
        targetNoteId={targetNoteId}
        setTargetNoteId={selectTargetNote}
        newNoteTitle={newNoteTitle}
        setNewNoteTitle={setNewNoteTitle}
        appendNote={appendNote}
        appendToProjectNote={appendToProjectNote}
        insertActiveQuote={insertActiveQuote}
        askSelection={askSidecarSelection}
        rewrite={() => rewriteMutation.mutate()}
        canSaveToProject
        isSavingToProject={appendPersistentNote.isPending}
        workspaceMode={workspaceMode}
        setWorkspaceMode={setWorkspaceMode}
        verification={verification}
        activeQuoteText={activeQuoteText}
      />
    </section>
  );
}

function NotesSidecar({
  open,
  onOpenChange,
  answer,
  activeEvidence,
  selectedSource,
  activeEvidenceIndex,
  notes,
  setNotes,
  noteStatus,
  isRewriting,
  isAutosaving,
  rewriteMode,
  setRewriteMode,
  notesList,
  targetNoteId,
  setTargetNoteId,
  newNoteTitle,
  setNewNoteTitle,
  appendNote,
  appendToProjectNote,
  insertActiveQuote,
  askSelection,
  rewrite,
  canSaveToProject,
  isSavingToProject,
  workspaceMode,
  setWorkspaceMode,
  verification,
  activeQuoteText
}: {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  answer: Answer | null;
  activeEvidence?: VerificationSource["evidence"][number];
  selectedSource: VerificationSource | null;
  activeEvidenceIndex: number;
  notes: string;
  setNotes: (value: string) => void;
  noteStatus: string;
  isRewriting: boolean;
  isAutosaving: boolean;
  rewriteMode: string;
  setRewriteMode: (value: string) => void;
  notesList: Array<{ id: string; title: string }>;
  targetNoteId: string;
  setTargetNoteId: (value: string) => void;
  newNoteTitle: string;
  setNewNoteTitle: (value: string) => void;
  appendNote: (text: string) => void;
  appendToProjectNote: (text: string, citations?: Record<string, unknown>[]) => void;
  insertActiveQuote: (source: "reference" | "pdf") => void;
  askSelection: (selection: NoteSelectionRange, instruction: string) => Promise<string>;
  rewrite: () => void;
  canSaveToProject: boolean;
  isSavingToProject: boolean;
  workspaceMode: "questions" | "notes";
  setWorkspaceMode: (mode: "questions" | "notes") => void;
  verification: VerificationSource[];
  activeQuoteText: string;
}) {
  const [editorMode, setEditorMode] = useState<"edit" | "preview">("edit");
  const [selection, setSelection] = useState<NoteSelectionRange | null>(null);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiPreview, setAiPreview] = useState("");
  const [aiError, setAiError] = useState("");
  const [isAskingSelection, setIsAskingSelection] = useState(false);
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const [editorScrollLeft, setEditorScrollLeft] = useState(0);
  const noteEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const editorWrapRef = useRef<HTMLDivElement | null>(null);

  const selectionPreview = stripHighlightMarkers(selection?.text ?? "");
  const highlightRanges = selection ? [{ start: selection.start, end: selection.end, className: "textarea-highlight-range--selection" }] : [];
  const ghostInsertions =
    selection && aiPreview
      ? [{ index: selection.end, content: `\n\n${aiPreview}`, className: "textarea-ghost-insertion--ai" }]
      : [];

  function captureSelection() {
    const node = noteEditorRef.current;
    if (!node || node.selectionStart === node.selectionEnd) {
      return;
    }
    const next = {
      start: node.selectionStart,
      end: node.selectionEnd,
      text: notes.slice(node.selectionStart, node.selectionEnd)
    };
    setSelection(next);
    setAiPreview("");
    setAiError("");
  }

  function clearEditorSelection() {
    setSelection(null);
    setAiInstruction("");
    setAiPreview("");
    setAiError("");
  }

  function markSelectionInline() {
    if (!selection) {
      return null;
    }
    const selected = notes.slice(selection.start, selection.end);
    if (!selected.trim()) {
      return null;
    }
    const alreadyMarked =
      (selected.startsWith("==") && selected.endsWith("==")) ||
      (notes.slice(Math.max(0, selection.start - 2), selection.start) === "==" && notes.slice(selection.end, selection.end + 2) === "==");
    if (alreadyMarked) {
      return selection;
    }
    const marked = `==${selected}==`;
    const nextSelection = {
      start: selection.start,
      end: selection.start + marked.length,
      text: marked
    };
    setNotes(`${notes.slice(0, selection.start)}${marked}${notes.slice(selection.end)}`);
    setSelection(nextSelection);
    return nextSelection;
  }

  function pinSelectionForQuestion() {
    if (!selection) {
      return;
    }
    markSelectionInline();
  }

  useEffect(() => {
    if (!selection) {
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && editorWrapRef.current?.contains(target)) {
        return;
      }
      clearSelectionAi();
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [selection]);

  function insertAtCursor(value: string) {
    const node = noteEditorRef.current;
    const start = node?.selectionStart ?? notes.length;
    const end = node?.selectionEnd ?? notes.length;
    setNotes(`${notes.slice(0, start)}${value}${notes.slice(end)}`);
    requestAnimationFrame(() => {
      noteEditorRef.current?.focus();
      noteEditorRef.current?.setSelectionRange(start + value.length, start + value.length);
    });
  }

  function replaceSelectionWith(value: string) {
    if (!selection) {
      return;
    }
    setNotes(`${notes.slice(0, selection.start)}${value}${notes.slice(selection.end)}`);
    setSelection(null);
    setAiPreview("");
    setAiInstruction("");
  }

  function insertAfterSelection(value: string) {
    if (!selection) {
      insertAtCursor(`\n\n${value}`);
      return;
    }
    const insertText = `\n\n${value}`;
    setNotes(`${notes.slice(0, selection.end)}${insertText}${notes.slice(selection.end)}`);
    setSelection({
      start: selection.end,
      end: selection.end + insertText.length,
      text: insertText
    });
    setAiPreview("");
    setAiInstruction("");
  }

  function clearSelectionAi() {
    clearEditorSelection();
  }

  function handleSelectionQuestionKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      pinSelectionForQuestion();
      void askAiAboutSelection();
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      clearSelectionAi();
    }
  }

  async function askAiAboutSelection() {
    if (!selection || !aiInstruction.trim()) {
      return;
    }
    setIsAskingSelection(true);
    setAiError("");
    try {
      const response = await askSelection(selection, aiInstruction.trim());
      setAiPreview(response);
    } catch (error) {
      setAiError(error instanceof Error ? error.message : "KI-Frage fehlgeschlagen");
    } finally {
      setIsAskingSelection(false);
    }
  }

  function wrapSelection(before: string, after = before) {
    const node = noteEditorRef.current;
    const start = node?.selectionStart ?? notes.length;
    const end = node?.selectionEnd ?? notes.length;
    const selected = notes.slice(start, end) || "Text";
    const next = `${before}${selected}${after}`;
    setNotes(`${notes.slice(0, start)}${next}${notes.slice(end)}`);
    requestAnimationFrame(() => {
      noteEditorRef.current?.focus();
      noteEditorRef.current?.setSelectionRange(start + before.length, start + before.length + selected.length);
    });
  }

  function prefixLines(prefix: string) {
    const node = noteEditorRef.current;
    const start = node?.selectionStart ?? notes.length;
    const end = node?.selectionEnd ?? notes.length;
    const selected = notes.slice(start, end) || "Text";
    const next = selected.split("\n").map((line) => `${prefix}${line}`).join("\n");
    setNotes(`${notes.slice(0, start)}${next}${notes.slice(end)}`);
  }

  if (!open) {
    return (
      <aside className="notes-sidecar notes-sidecar--collapsed">
        <button className="icon-button" type="button" aria-label="Notizen oeffnen" onClick={() => onOpenChange(true)}>
          <PanelRightOpen size={18} />
        </button>
        <span>Notizen</span>
      </aside>
    );
  }

  return (
    <aside className="notes-sidecar">
      <div className="notes-heading">
        <div>
          <span>Workspace</span>
          <strong>Notizen</strong>
        </div>
        <button className="icon-button" type="button" aria-label="Notizen einklappen" onClick={() => onOpenChange(false)}>
          <PanelRightClose size={18} />
        </button>
      </div>
      <div className="segmented assistant-mode-toggle">
        <button type="button" className={workspaceMode === "questions" ? "active" : ""} onClick={() => setWorkspaceMode("questions")}>
          Fragen
        </button>
        <button type="button" className={workspaceMode === "notes" ? "active" : ""} onClick={() => setWorkspaceMode("notes")}>
          Notizen
        </button>
      </div>
      <div className="markdown-toolbar markdown-toolbar--compact">
        <button className="icon-button" type="button" aria-label="Fett" onClick={() => wrapSelection("**")}>
          <Bold size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Kursiv" onClick={() => wrapSelection("*")}>
          <Italic size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Zitat" onClick={() => prefixLines("> ")}>
          <Quote size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Liste" onClick={() => prefixLines("- ")}>
          <List size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Code" onClick={() => wrapSelection("`")}>
          <Code size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Link" onClick={() => wrapSelection("[", "](https://)")}>
          <Link size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Highlight" onClick={() => wrapSelection("==")}>
          <Highlighter size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Tabelle" onClick={() => insertAtCursor("\n\n| Spalte 1 | Spalte 2 |\n|---|---|\n| Wert | Wert |\n")}>
          <Table2 size={16} />
        </button>
        <button className="icon-button" type="button" aria-label="Exportieren" onClick={() => downloadMarkdownFile(newNoteTitle, notes)} disabled={!notes.trim()}>
          <Download size={16} />
        </button>
        <div className="segmented markdown-mode-toggle">
          <button type="button" className={editorMode === "edit" ? "active" : ""} onClick={() => setEditorMode("edit")}>
            Edit
          </button>
          <button type="button" className={editorMode === "preview" ? "active" : ""} onClick={() => setEditorMode("preview")}>
            Preview
          </button>
        </div>
      </div>
      {activeEvidence && selectedSource ? (
        <div className="active-quote-preview" style={evidenceColorVars(activeEvidenceIndex)}>
          <span>
            Aktives Zitat Z{activeEvidenceIndex + 1} - {selectedSource.title || selectedSource.paper_id}
          </span>
          <p>{activeQuoteText || activeEvidence.reference_text}</p>
        </div>
      ) : null}
      {editorMode === "edit" ? (
        <div className="notes-editor-wrap notes-editor-wrap--highlighted" ref={editorWrapRef}>
          <TextareaHighlightLayer text={notes} ranges={highlightRanges} insertions={ghostInsertions} scrollTop={editorScrollTop} scrollLeft={editorScrollLeft} />
          <textarea
            ref={noteEditorRef}
            className="notes-editor notes-editor--highlighted"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            onSelect={captureSelection}
            onPointerDown={clearEditorSelection}
            onScroll={(event) => {
              setEditorScrollTop(event.currentTarget.scrollTop);
              setEditorScrollLeft(event.currentTarget.scrollLeft);
            }}
            placeholder="Notizen"
          />
          {selection ? (
            <div className="selection-ai-popover selection-ai-popover--sidecar">
              <div>
                <WandSparkles size={16} />
                <strong>{selectionPreview.length} Zeichen markiert</strong>
              </div>
              <blockquote>{selectionPreview}</blockquote>
              {!aiPreview ? (
                <div className="selection-ai-question-row">
                  <input
                    value={aiInstruction}
                    onChange={(event) => setAiInstruction(event.target.value)}
                    onFocus={pinSelectionForQuestion}
                    onKeyDown={handleSelectionQuestionKeyDown}
                    placeholder="KI-Frage zu dieser Auswahl"
                  />
                  <button
                    className="button button-primary"
                    type="button"
                    disabled={!aiInstruction.trim() || isAskingSelection}
                    onClick={() => {
                      pinSelectionForQuestion();
                      void askAiAboutSelection();
                    }}
                  >
                    Fragen
                  </button>
                </div>
              ) : null}
              {aiError ? <div className="inline-error">{aiError}</div> : null}
              {aiPreview ? (
                <div className="ai-preview-card">
                  <span>Antwort</span>
                  <pre>{aiPreview}</pre>
                  <div className="button-row">
                    <button className="button button-primary" type="button" onClick={() => replaceSelectionWith(aiPreview)}>
                      Ersetzen
                    </button>
                    <button className="button" type="button" onClick={() => insertAfterSelection(aiPreview)}>
                      Darunter einfuegen
                    </button>
                    <button className="button" type="button" onClick={() => setAiPreview("")}>
                      Verwerfen
                    </button>
                    <button className="button" type="button" onClick={clearSelectionAi}>
                      Schliessen
                    </button>
                  </div>
                </div>
              ) : null}
            </div>
          ) : null}
        </div>
      ) : (
        <AssistantMarkdownPreview markdown={notes} />
      )}
      <div className="note-target-row">
        <select aria-label="Zielnotiz" value={targetNoteId} onChange={(event) => setTargetNoteId(event.target.value)} disabled={!canSaveToProject}>
          <option value="">Neue Notiz</option>
          {notesList.map((note) => (
            <option key={note.id} value={note.id}>
              {note.title}
            </option>
          ))}
        </select>
        {!targetNoteId ? (
          <input value={newNoteTitle} onChange={(event) => setNewNoteTitle(event.target.value)} placeholder="Titel" disabled={!canSaveToProject} />
        ) : null}
      </div>
      <div className="notes-actions">
        <button className="button" type="button" onClick={() => answer && appendNote(formatAnswerForNote(answer, verification).markdown)} disabled={!answer}>
          <FilePlus2 size={16} />
          <span>Entwurf</span>
        </button>
        <button
          className="button"
          type="button"
          onClick={() => {
            if (!answer) {
              return;
            }
            const formatted = formatAnswerForNote(answer, verification);
            appendToProjectNote(formatted.markdown, formatted.citations);
          }}
          disabled={!answer || !canSaveToProject || isSavingToProject}
        >
          <NotebookPen size={16} />
          <span>In Notiz</span>
        </button>
        <button className="button" type="button" onClick={() => insertActiveQuote("reference")} disabled={!activeEvidence || !selectedSource}>
          <Quote size={16} />
          <span>Zitat Z{activeEvidenceIndex + 1}</span>
        </button>
        <button className="button" type="button" onClick={() => insertActiveQuote("pdf")} disabled={!activeEvidence?.pdf_excerpt || !selectedSource}>
          <NotebookPen size={16} />
          <span>PDF Z{activeEvidenceIndex + 1}</span>
        </button>
        <select aria-label="Umschreibmodus" value={rewriteMode} onChange={(event) => setRewriteMode(event.target.value)}>
          <option value="klarer">Klarer</option>
          <option value="kuerzer">Kuerzer</option>
          <option value="wissenschaftlich">Wissenschaftlich</option>
        </select>
        <button className="icon-button" type="button" aria-label="Notizen umschreiben" onClick={rewrite} disabled={!notes.trim() || isRewriting}>
          <WandSparkles size={17} />
        </button>
      </div>
      {isRewriting || isAutosaving || noteStatus ? <span className="notes-status">{isRewriting ? "Umschreiben laeuft" : isAutosaving ? "Speichert" : noteStatus}</span> : null}
    </aside>
  );
}

function AssistantMarkdownPreview({ markdown }: { markdown: string }) {
  const blocks = markdown.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean);
  if (!blocks.length) {
    return <article className="markdown-preview notes-sidecar-preview muted-row">Keine Notizen</article>;
  }
  return (
    <article className="markdown-preview notes-sidecar-preview">
      {blocks.map((block, index) => {
        if (block.startsWith("# ")) {
          return <h1 key={index}>{renderAssistantInline(block.slice(2))}</h1>;
        }
        if (block.startsWith("## ")) {
          return <h2 key={index}>{renderAssistantInline(block.slice(3))}</h2>;
        }
        if (block.startsWith(">")) {
          return <blockquote key={index}>{renderAssistantInline(block.replace(/^>\s?/gm, ""))}</blockquote>;
        }
        if (/^- /m.test(block)) {
          return (
            <ul key={index}>
              {block.split("\n").map((line, itemIndex) => (
                <li key={itemIndex}>{renderAssistantInline(line.replace(/^- /, ""))}</li>
              ))}
            </ul>
          );
        }
        return <p key={index}>{renderAssistantInline(block)}</p>;
      })}
    </article>
  );
}

function renderAssistantInline(text: string) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|==[^=]+==|\[[^\]]+\]\([^)]+\))/g);
  return parts.map((part, index) => {
    const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
    if (linkMatch) {
      return (
        <a key={`${part}-${index}`} href={linkMatch[2]} target="_blank" rel="noreferrer">
          {linkMatch[1]}
        </a>
      );
    }
    if (/^\*\*[^*]+\*\*$/.test(part)) {
      return <strong key={`${part}-${index}`}>{part.slice(2, -2)}</strong>;
    }
    if (/^\*[^*]+\*$/.test(part)) {
      return <em key={`${part}-${index}`}>{part.slice(1, -1)}</em>;
    }
    if (/^`[^`]+`$/.test(part)) {
      return <code key={`${part}-${index}`}>{part.slice(1, -1)}</code>;
    }
    if (/^==[^=]+==$/.test(part)) {
      return <mark key={`${part}-${index}`}>{part.slice(2, -2)}</mark>;
    }
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}

function answerLimitFor(question: string, mode: string) {
  if (mode !== "auto") {
    return Number(mode);
  }
  const terms = textTerms(question);
  if (question.length > 180 || terms.length > 18) {
    return 25;
  }
  if (question.length > 90 || terms.length > 10) {
    return 20;
  }
  return 16;
}

function verificationLimits(answer: Answer) {
  const sourceCount = Math.max(1, answer.sources.length);
  const evidenceCount = Math.max(answer.evidence.length, sourceCount * 4);
  return {
    max_sources: Math.min(25, Math.max(12, sourceCount + 4)),
    max_evidence_per_source: Math.min(20, Math.max(8, Math.ceil(evidenceCount / sourceCount) + 2))
  };
}

function formatNoteQuote(quote: string, source: VerificationSource, evidenceIndex: number, citationId: string) {
  const text = cleanCitationText(quote);
  const title = source.title || source.paper_id;
  return `> ${text}\n\nQuelle: [Z${evidenceIndex + 1} - ${title}](sciencekg://citation/${citationId}) (${source.paper_id})`;
}

function noteCitation(source: VerificationSource, evidence: VerificationSource["evidence"][number], evidenceIndex: number) {
  return {
    id: stableCitationId(source, evidence, evidenceIndex),
    paper_id: source.paper_id,
    title: source.title,
    kind: evidence.kind,
    reference_text: cleanCitationText(evidence.reference_text),
    pdf_excerpt: cleanCitationText(evidence.pdf_excerpt),
    evidence_index: evidenceIndex
  };
}

function formatAnswerForNote(answer: Answer, verification: VerificationSource[]) {
  const citations = new Map<string, Record<string, unknown>>();
  const markdown = answer.answer.replace(/\[([^\]]+)\]/g, (match, rawCitation: string, offset: number, fullText: string) => {
    const context = fullText.slice(Math.max(0, offset - 350), Math.min(fullText.length, offset + match.length + 350));
    const source = verification.find((item) => citationIds(rawCitation).some((candidate) => sameCitation(item.paper_id, candidate)));
    if (!source) {
      return match;
    }
    const evidenceIndex = bestEvidenceIndex(source, context);
    const evidence = source.evidence[evidenceIndex];
    if (!evidence) {
      return match;
    }
    const citation = noteCitation(source, evidence, evidenceIndex);
    citations.set(String(citation.id), citation);
    return `[Z${evidenceIndex + 1} - ${shortTitle(source.title || source.paper_id)}](sciencekg://citation/${citation.id})`;
  });
  return { markdown, citations: Array.from(citations.values()) };
}

function stableCitationId(source: VerificationSource, evidence: VerificationSource["evidence"][number], evidenceIndex: number) {
  return `cite_${stableHash([source.paper_id, evidenceIndex, evidence.reference_text, evidence.pdf_excerpt].join("|"))}`;
}

function stableHash(value: string) {
  let hash = 2166136261;
  for (let index = 0; index < value.length; index += 1) {
    hash ^= value.charCodeAt(index);
    hash = Math.imul(hash, 16777619);
  }
  return (hash >>> 0).toString(16);
}

function turnBlocks(turn: AssistantTurn): AssistantAnswerBlock[] {
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

function mergeVerification(sources: VerificationSource[]) {
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

function turnContext(turn: AssistantTurn) {
  return turnBlocks(turn)
    .flatMap((block) => [
      { role: "user", content: block.question },
      { role: "assistant", content: block.answer.answer }
    ])
    .slice(-8);
}

function cleanCitationText(value: string) {
  return dedupeRepeatedText(value)
    .replace(/\b(?:authors?|author names?)\s*:\s*/gi, "")
    .replace(/\s+/g, " ")
    .trim();
}

function cleanAnswerQuote(value: string) {
  return value
    .replace(/\[[^\]]+\]/g, "")
    .replace(/\s+/g, " ")
    .replace(/^\s*[-*]\s+/, "")
    .trim();
}

function stripHighlightMarkers(value: string) {
  return value.replace(/^==([\s\S]*)==$/m, "$1");
}

function dedupeRepeatedText(value: string) {
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

function normalizeSentence(value: string) {
  return value.toLowerCase().replace(/[\s.?!;:,]+/g, " ").trim();
}

function rewriteInstruction(mode: string) {
  if (mode === "kuerzer") {
    return "Kuerze den Text, behalte alle fachlichen Aussagen und vorhandenen Zitationsmarker.";
  }
  if (mode === "wissenschaftlich") {
    return "Formuliere den Text wissenschaftlicher, praezise und ohne neue Fakten.";
  }
  return "Schreibe den Text klarer und fluessiger um, ohne neue Fakten oder Zitate hinzuzufuegen.";
}

function assistantStorageKey(projectId: string) {
  return `sciencekg.assistant.session.${projectId}`;
}

function notesStorageKey(projectId: string) {
  return `sciencekg.assistant.notes.${projectId}`;
}

function loadAssistantSession(projectId: string): { history: AssistantTurn[]; activeTurnId: string } {
  try {
    const raw = window.localStorage.getItem(assistantStorageKey(projectId));
    if (!raw) {
      return { history: [], activeTurnId: "" };
    }
    const payload = JSON.parse(raw) as Partial<{ history: AssistantTurn[]; activeTurnId: string }>;
    const history = Array.isArray(payload.history) ? payload.history : [];
    return {
      history,
      activeTurnId: payload.activeTurnId || history[history.length - 1]?.id || ""
    };
  } catch {
    return { history: [], activeTurnId: "" };
  }
}

function saveAssistantSession(projectId: string, session: { history: AssistantTurn[]; activeTurnId: string }) {
  try {
    window.localStorage.setItem(
      assistantStorageKey(projectId),
      JSON.stringify({ history: session.history.slice(-25), activeTurnId: session.activeTurnId })
    );
  } catch {
    // Local storage can be disabled in private/browser test contexts.
  }
}

function loadNotes(projectId: string) {
  try {
    return window.localStorage.getItem(notesStorageKey(projectId)) ?? window.localStorage.getItem("sciencekg.assistant.notes") ?? "";
  } catch {
    return "";
  }
}

function saveNotes(projectId: string, value: string) {
  try {
    window.localStorage.setItem(notesStorageKey(projectId), value);
  } catch {
    // Local storage can be disabled in private/browser test contexts.
  }
}

function noteTitleForSave(title: string, markdown: string) {
  const trimmed = title.trim();
  if (trimmed && !isUntitledNoteTitle(trimmed)) {
    return trimmed;
  }
  const suggestion = suggestNoteTitle(markdown);
  return suggestion || trimmed || "Neue Notiz";
}

function isUntitledNoteTitle(title: string) {
  return ["", "Neue Notiz", "Assistant Notiz"].includes(title.trim());
}

function suggestNoteTitle(markdown: string) {
  const heading = markdown.match(/^#{1,3}\s+(.+)$/m)?.[1]?.trim();
  const source = heading || markdown;
  const text = source
    .replace(/!\[[^\]]*\]\([^)]+\)/g, " ")
    .replace(/\[[^\]]+\]\([^)]+\)/g, " ")
    .replace(/[#>*_`|[\]()]/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (text.length < 90 && !heading) {
    return "";
  }
  return text.split(/\s+/).slice(0, 8).join(" ").slice(0, 72);
}

function formatTurnTime(value: string) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "";
  }
  return date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

function AnswerText({
  answer,
  onCitationClick,
  getCitationMeta
}: {
  answer: string;
  onCitationClick: (citation: string, context?: string, quote?: string) => void;
  getCitationMeta: (citation: string, context?: string) => { source: VerificationSource; evidenceIndex: number } | null;
}) {
  const parts = answer.split(/(\[[^\]]+\])/g);
  return (
    <>
      {parts.map((part, index) => {
        const match = /^\[([^\]]+)\]$/.exec(part);
        if (!match) {
          return <span key={`${part}-${index}`}>{part}</span>;
        }
        const context = citationContext(parts, index);
        const quote = citationQuoteFromParts(parts, index);
        const meta = getCitationMeta(match[1], context);
        const label = meta ? `Z${meta.evidenceIndex + 1}` : "?";
        return (
          <button
            key={`${part}-${index}`}
            className={`citation-link ${meta ? "citation-link--mapped" : ""}`}
            type="button"
            onClick={() => onCitationClick(match[1], context, quote)}
            style={meta ? evidenceColorVars(meta.evidenceIndex) : undefined}
            title={meta ? `${meta.source.title || meta.source.paper_id} - Zitat ${meta.evidenceIndex + 1}` : undefined}
          >
            <span className="citation-index">{label}</span>
            <span className="citation-paper">{shortCitationLabel(match[1])}</span>
          </button>
        );
      })}
    </>
  );
}

function shortCitationLabel(value: string) {
  const clean = value.replace(/^https?:\/\/arxiv\.org\/abs\//, "arxiv:").trim();
  if (clean.length <= 18) {
    return clean;
  }
  return `${clean.slice(0, 16)}...`;
}

function shortTitle(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length <= 56 ? text : `${text.slice(0, 53)}...`;
}

function citationContext(parts: string[], citationIndex: number) {
  return `${parts[citationIndex - 1] ?? ""} ${parts[citationIndex + 1] ?? ""}`.trim().slice(-700);
}

function citationQuoteFromParts(parts: string[], citationIndex: number) {
  const before = parts.slice(0, citationIndex).join("");
  const after = parts.slice(citationIndex + 1).join("");
  return cleanAnswerQuote(`${trailingSentenceFragment(before)}${leadingSentenceFragment(after)}`);
}

function trailingSentenceFragment(value: string) {
  const trimmed = value.trimEnd();
  const boundaries = ["\n\n", "\n- ", "\n* ", ". ", "! ", "? "].map((boundary) => trimmed.lastIndexOf(boundary));
  const start = Math.max(...boundaries);
  const offset = start >= 0 ? start + 1 : 0;
  return trimmed.slice(offset).replace(/^\s*[-*]\s+/, "").trimStart();
}

function leadingSentenceFragment(value: string) {
  const trimmed = value.trimStart();
  if (/^[.!?]/.test(trimmed)) {
    return trimmed[0];
  }
  const stop = trimmed.search(/[.!?](?:\s|$)|\n{2,}/);
  return stop >= 0 ? trimmed.slice(0, stop + 1) : "";
}

function citationIds(citation: string) {
  return citation
    .split(/[;,]/)
    .map((item) => item.trim())
    .filter(Boolean);
}

function sameCitation(sourceId: string, citation: string) {
  const left = normalizeCitation(sourceId);
  const right = normalizeCitation(citation);
  return left === right || left.endsWith(right) || right.endsWith(left);
}

function normalizeCitation(value: string) {
  return value.toLowerCase().replace(/^https?:\/\/arxiv\.org\/abs\//, "arxiv:").replace(/\s+/g, "");
}

function bestEvidenceIndex(source: VerificationSource, context: string) {
  if (!source.evidence.length) {
    return 0;
  }
  const terms = textTerms(context);
  if (!terms.length) {
    return 0;
  }
  let bestIndex = 0;
  let bestScore = -1;
  source.evidence.forEach((evidence, index) => {
    const target = normalizeText(`${evidence.reference_text} ${evidence.pdf_excerpt} ${evidence.matched_terms.join(" ")}`);
    const score = terms.reduce((total, term) => total + (target.includes(term) ? 1 : 0), 0);
    if (score > bestScore) {
      bestIndex = index;
      bestScore = score;
    }
  });
  return bestScore > 0 ? bestIndex : 0;
}

function textTerms(text: string) {
  const stopwords = new Set(["about", "after", "also", "and", "are", "based", "from", "have", "into", "not", "that", "the", "their", "this", "used", "with"]);
  return Array.from(new Set(normalizeText(text).split(" "))).filter((term) => term.length >= 5 && !stopwords.has(term)).slice(0, 36);
}

function shortEvidenceText(value: string) {
  const text = value.replace(/\s+/g, " ").trim();
  return text.length > 92 ? `${text.slice(0, 89)}...` : text;
}

function normalizeText(text: string) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}-]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}
