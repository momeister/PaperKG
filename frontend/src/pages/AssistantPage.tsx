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
import { NotesSidecar } from "./NotesSidecar";
// Antwort-Rendering lebt in ./AnswerText; Re-Export fuer bestehende Importer.
import { AnswerText, EvidenceVerificationBadge } from "./AnswerText";
export { AnswerText, EvidenceVerificationBadge };
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

export type NoteSelectionRange = {
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

