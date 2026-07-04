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
export type { AssistantSession } from "./assistantSession";
export { loadAssistantSession, slimTurnForPersist, saveAssistantSession, fetchAssistantSession, formatTurnTime };

// Pure helpers live in ./assistantHelpers; re-export them all for back-compat and
// import the ones this file's components use directly.
export * from "./assistantHelpers";
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

export type AssistantAnswerBlock = {
  id: string;
  question: string;
  answer: Answer;
  verification: VerificationSource[];
  createdAt: string;
};

export type AssistantTurn = {
  id: string;
  question: string;
  answer: Answer;
  verification: VerificationSource[];
  createdAt: string;
  blocks?: AssistantAnswerBlock[];
  type?: "chat" | "research_tree" | "parallel";
  researchNodes?: ResearchNode[];
  /** For ``type === "parallel"``: the server-side parallel-research session id and a
   * cached variant count for the session-list label. */
  parallelSessionId?: string;
  parallelVariantCount?: number;
};

export type CitationMeta = {
  source: VerificationSource;
  evidenceIndex: number;
  evidenceId?: string;
  approximate?: boolean;
};

/** Zusatzinfos beim Übernehmen eines Zitats in die Notiz. */
export type CitationInsertExtras = {
  /** Nur der Textabschnitt, den DIESES Zitat belegt (statt des ganzen Satzes). */
  segment?: string;
  /** Alle weiteren Quellen desselben Satzes — wandern mit in die Notiz. */
  siblings?: CitationMeta[];
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
  const [webFindings, setWebFindings] = useState<DeepResearchFinding[]>([]);
  const [savedGreyUrls, setSavedGreyUrls] = useState<string[]>([]);
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
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
  const primaryPaperId = useMemo(
    () => projectsQuery.data?.projects.find((project) => project.id === activeProject)?.primary_paper_id ?? null,
    [projectsQuery.data?.projects, activeProject]
  );
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
        priority_paper_ids: primaryPaperId ? [primaryPaperId] : undefined,
        conversation_context: conversationMode === "followup" && activeTurn ? turnContext(activeTurn) : undefined,
        project_id: activeProject || undefined
      }),
    onSuccess: async (payload) => {
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

  const webResearchMutation = useMutation({
    mutationFn: (value: string) => api.deepResearch({ question: value, provider, max_sources: 6 }),
    onSuccess: (payload) => setWebFindings(payload.findings)
  });
  const saveGreyMutation = useMutation({
    mutationFn: (finding: DeepResearchFinding) =>
      api.addGreySources(
        activeProject as string,
        [
          {
            url: finding.url,
            title: finding.title,
            summary: finding.summary,
            raw_excerpt: finding.raw_excerpt,
            injection_flags: finding.injection_flags
          }
        ],
        question.trim() || undefined
      ),
    onSuccess: (_data, finding) => setSavedGreyUrls((current) => [...current, finding.url])
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
    // Grey-source bookkeeping is project-scoped: a URL saved in project A must stay
    // saveable after switching to project B.
    setWebFindings([]);
    setSavedGreyUrls([]);
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

  function citationMeta(citation: string, context = "", links: CitationLink[] = [], citationStart?: number) {
    return citationMetaFor(verification, citation, context, links, citationStart);
  }

  function citationMetaFor(pool: VerificationSource[], citation: string, context = "", links: CitationLink[] = [], citationStart?: number) {
    return citationMetasFor(pool, citation, context, links, citationStart)[0] ?? null;
  }

  function jumpToCitation(citation: string, context = "", links: CitationLink[] = [], citationStart?: number) {
    const meta = citationMeta(citation, context, links, citationStart);
    if (meta) {
      setEvidenceOpen(true);
      selectSource(meta.source, meta.evidenceIndex);
    }
  }

  function jumpToCitationIn(pool: VerificationSource[], citation: string, context = "", quote = "", links: CitationLink[] = [], citationStart?: number) {
    const meta = citationMetaFor(pool, citation, context, links, citationStart);
    if (meta) {
      jumpToCitationMeta(meta, context, quote);
    }
  }

  function jumpToCitationMeta(meta: CitationMeta, context = "", quote = "") {
    setEvidenceOpen(true);
    selectSource(meta.source, meta.evidenceIndex);
    setSelectedAnswerQuote({
      paperId: meta.source.paper_id,
      evidenceIndex: meta.evidenceIndex,
      text: cleanAnswerQuote(quote || context)
    });
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
    const quote = source === "pdf" ? activeEvidence.pdf_excerpt || activeEvidence.reference_text : meaningfulQuote(answerQuote) || activeEvidence.reference_text;
    const citation = noteCitation(selectedSource, activeEvidence, activeEvidenceIndex, quote);
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
          <button
            className="icon-button"
            type="button"
            aria-label="Web-Recherche (graue Quelle)"
            title="Web-Recherche (graue Quelle)"
            disabled={webResearchMutation.isPending || !question.trim()}
            onClick={() => webResearchMutation.mutate(question.trim())}
          >
            <Globe size={18} />
          </button>
        </form>

        {webFindings.length ? (
          <section className="grey-research-panel">
            <div className="assistant-history-heading">
              <span>Graue Quellen (Web)</span>
              <button className="button" type="button" onClick={() => setWebFindings([])}>
                Schließen
              </button>
            </div>
            {!activeProject || activeProject === "__all_papers__" ? (
              <div className="warning-row">Wähle ein echtes Projekt, um graue Quellen zu speichern.</div>
            ) : null}
            {webFindings.map((finding) => (
              <article key={finding.url} className="grey-source-card">
                <div className="assistant-history-heading">
                  <strong>{finding.title}</strong>
                  <button
                    className="button"
                    type="button"
                    disabled={
                      !activeProject ||
                      activeProject === "__all_papers__" ||
                      saveGreyMutation.isPending ||
                      savedGreyUrls.includes(finding.url)
                    }
                    onClick={() => saveGreyMutation.mutate(finding)}
                  >
                    {savedGreyUrls.includes(finding.url) ? "Gespeichert" : "Zum Projekt"}
                  </button>
                </div>
                {finding.quarantined ? (
                  <div className="warning-row">⚠ Prompt-Injection erkannt &amp; ignoriert: {finding.injection_flags.join(", ")}</div>
                ) : null}
                <p>{finding.summary}</p>
                <a className="muted" href={finding.url} target="_blank" rel="noreferrer">
                  {finding.url}
                </a>
              </article>
            ))}
          </section>
        ) : null}

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
                        citationLinks={block.answer.citation_links ?? []}
                        onCitationClick={(citation, context, quote, citationStart) =>
                          jumpToCitationIn(block.verification, citation, context, quote, block.answer.citation_links ?? [], citationStart)
                        }
                        onCitationMetaClick={(meta, context, quote) => jumpToCitationMeta(meta, context, quote)}
                        getCitationMeta={(citation, context, citationStart) =>
                          citationMetasFor(block.verification, citation, context, block.answer.citation_links ?? [], citationStart)
                        }
                        activeCitation={selectedSource ? { paperId: selectedSource.paper_id, evidenceIndex: activeEvidenceIndex } : undefined}
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
          <EvidenceVerificationBadge source={selectedSource} evidence={activeEvidence} />
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

