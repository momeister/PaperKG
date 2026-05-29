import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bold,
  Code,
  ChevronLeft,
  ChevronRight,
  Download,
  FilePlus2,
  Highlighter,
  ImagePlus,
  Italic,
  Link,
  List,
  NotebookPen,
  PanelRightClose,
  PanelRightOpen,
  Quote,
  Redo2,
  Sparkles,
  Table2,
  Trash2,
  Undo2
} from "lucide-react";

import { api, API_BASE_URL } from "../api";
import { EmptyState } from "../components/EmptyState";
import { PdfPane } from "../components/PdfPane";
import { TextareaHighlightLayer } from "../components/TextareaHighlightLayer";
import { downloadMarkdownFile } from "../download";
import { noteProjectId, projectScopeLabel } from "../projectScope";
import { useAppState } from "../state";
import type { NoteAiThread, NoteCitation, VerificationEvidence } from "../types";

type SelectionRange = {
  start: number;
  end: number;
  text: string;
};

export function NotesPage() {
  const { activeProject, provider, model } = useAppState();
  const scopedProjectId = noteProjectId(activeProject);
  const scopeLabel = projectScopeLabel(activeProject);
  const queryClient = useQueryClient();
  const [activeNoteId, setActiveNoteId] = useState<string>("");
  const [title, setTitle] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [dirty, setDirty] = useState(false);
  const [editorMode, setEditorMode] = useState<"edit" | "preview">("edit");
  const [selection, setSelection] = useState<SelectionRange | null>(null);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiPreview, setAiPreview] = useState("");
  const [undoStack, setUndoStack] = useState<string[]>([]);
  const [selectedCitation, setSelectedCitation] = useState<NoteCitation | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [notesListOpen, setNotesListOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.notesListOpen`, true));
  const [contextOpen, setContextOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.contextOpen`, true));
  const [notePdfOpen, setNotePdfOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.notePdfOpen`, true));
  const [activeThreadId, setActiveThreadId] = useState("");
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const editorWrapRef = useRef<HTMLDivElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const loadedNoteIdRef = useRef("");
  const previewRef = useRef<HTMLElement | null>(null);
  const latestDraftRef = useRef({ noteId: "", title: "", markdown: "" });
  const dirtyRef = useRef(false);

  const notesQuery = useQuery({
    queryKey: ["notes", scopedProjectId],
    queryFn: () => api.listNotes(scopedProjectId)
  });
  const noteQuery = useQuery({
    queryKey: ["note", activeNoteId],
    queryFn: () => api.getNote(activeNoteId),
    enabled: Boolean(activeNoteId)
  });
  const threadsQuery = useQuery({
    queryKey: ["note-ai-threads", activeNoteId],
    queryFn: () => api.listNoteAiThreads(activeNoteId),
    enabled: Boolean(activeNoteId)
  });

  const createNote = useMutation({
    mutationFn: () => api.createNote(scopedProjectId, { title: "Neue Notiz", markdown: "# Neue Notiz\n\n" }),
    onSuccess: ({ note }) => {
      setActiveNoteId(note.id);
      setTitle(note.title);
      setMarkdown(note.markdown);
      setDirtyState(false);
      setUndoStack([]);
      setAiPreview("");
      setSelection(null);
      loadedNoteIdRef.current = note.id;
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    }
  });
  const saveNote = useMutation({
    mutationFn: (payload: { noteId: string; title: string; markdown: string }) =>
      api.updateNote(payload.noteId, { title: payload.title, markdown: payload.markdown }),
    onSuccess: ({ note }, variables) => {
      const latest = latestDraftRef.current;
      if (latest.noteId === note.id && latest.title === variables.title && latest.markdown === variables.markdown) {
        setDirtyState(false);
      }
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    }
  });
  const deleteNote = useMutation({
    mutationFn: () => api.deleteNote(activeNoteId),
    onSuccess: () => {
      setActiveNoteId("");
      setTitle("");
      setMarkdown("");
      setDirtyState(false);
      loadedNoteIdRef.current = "";
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    }
  });
  const restoreVersion = useMutation({
    mutationFn: () => api.restoreLatestNoteVersion(activeNoteId),
    onSuccess: ({ note }) => {
      setMarkdown(note.markdown);
      setTitle(note.title);
      setDirtyState(false);
      setUndoStack([]);
      loadedNoteIdRef.current = note.id;
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    }
  });
  const aiEdit = useMutation({
    mutationFn: () =>
      api.createNoteAiThread(activeNoteId, {
        selected_text: stripHighlightMarkers(selection?.text ?? ""),
        instruction: aiInstruction,
        provider,
        model,
        use_kg_evidence: true,
        anchor_start: selection?.start ?? null,
        anchor_end: selection?.end ?? null,
        anchor_quote: stripHighlightMarkers(selection?.text ?? "").slice(0, 2000) || null
      }),
    onSuccess: (payload) => {
      markSelectionInline();
      setAiPreview(payload.replacement_text);
      setActiveThreadId(payload.thread.id);
      setHistoryOpen(true);
      queryClient.invalidateQueries({ queryKey: ["note-ai-threads", activeNoteId] });
    }
  });
  const followUp = useMutation({
    mutationFn: ({ threadId, message }: { threadId: string; message: string }) =>
      api.appendNoteAiMessage(activeNoteId, threadId, {
        message,
        provider,
        model,
        use_kg_evidence: true
      }),
    onSuccess: (payload, variables) => {
      setActiveThreadId(payload.thread.id);
      setAiPreview(payload.replacement_text);
      setFollowUpDrafts((current) => ({ ...current, [variables.threadId]: "" }));
      queryClient.invalidateQueries({ queryKey: ["note-ai-threads", activeNoteId] });
    }
  });
  const updateThreadUi = useMutation({
    mutationFn: ({ thread, collapsed }: { thread: NoteAiThread; collapsed: boolean }) =>
      api.updateNoteAiThread(activeNoteId, thread.id, { ui_state: { ...(thread.ui_state ?? {}), collapsed } }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["note-ai-threads", activeNoteId] });
    }
  });
  const uploadAsset = useMutation({
    mutationFn: (file: File) => api.uploadNoteAsset(activeNoteId, file),
    onSuccess: ({ asset }) => {
      insertAtSelection(`![${asset.filename}](${absoluteUrl(asset.url)})`);
      queryClient.invalidateQueries({ queryKey: ["note", activeNoteId] });
    }
  });

  const notes = notesQuery.data?.items ?? [];
  const currentNote = noteQuery.data?.note;
  const citations = currentNote?.citations ?? [];

  useEffect(() => {
    latestDraftRef.current = { noteId: activeNoteId, title, markdown };
  }, [activeNoteId, markdown, title]);

  useEffect(() => {
    setActiveNoteId("");
    setSelectedCitation(null);
    loadedNoteIdRef.current = "";
  }, [scopedProjectId]);

  useEffect(() => {
    setNotesListOpen(loadBooleanUiState(`${scopedProjectId}.notesListOpen`, true));
    setContextOpen(loadBooleanUiState(`${scopedProjectId}.contextOpen`, true));
    setNotePdfOpen(loadBooleanUiState(`${scopedProjectId}.notePdfOpen`, true));
  }, [scopedProjectId]);

  useEffect(() => {
    saveBooleanUiState(`${scopedProjectId}.notesListOpen`, notesListOpen);
  }, [notesListOpen, scopedProjectId]);

  useEffect(() => {
    saveBooleanUiState(`${scopedProjectId}.contextOpen`, contextOpen);
  }, [contextOpen, scopedProjectId]);

  useEffect(() => {
    saveBooleanUiState(`${scopedProjectId}.notePdfOpen`, notePdfOpen);
  }, [notePdfOpen, scopedProjectId]);

  useEffect(() => {
    if (!selection) {
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && editorWrapRef.current?.contains(target)) {
        return;
      }
      setSelection(null);
      setAiPreview("");
      setAiInstruction("");
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [selection]);

  useEffect(() => {
    if (!activeNoteId && notes[0]) {
      setActiveNoteId(notes[0].id);
    }
  }, [activeNoteId, notes]);

  useEffect(() => {
    if (!currentNote) {
      return;
    }
    const switchedNote = loadedNoteIdRef.current !== currentNote.id;
    if (dirtyRef.current) {
      return;
    }
    if (!switchedNote && title === currentNote.title && markdown === currentNote.markdown) {
      return;
    }
    loadedNoteIdRef.current = currentNote.id;
    setTitle(currentNote.title);
    setMarkdown(currentNote.markdown);
    setDirtyState(false);
    setUndoStack([]);
    setAiPreview("");
    setSelection(null);
    setActiveThreadId("");
  }, [currentNote?.id, currentNote?.markdown, currentNote?.title, currentNote?.updated_timestamp, dirty, markdown, title]);

  useEffect(() => {
    if (!activeNoteId || !dirty || saveNote.isPending) {
      return;
    }
    const nextTitle = noteTitleForSave(title, markdown);
    if (nextTitle !== title) {
      setTitle(nextTitle);
    }
    const handle = window.setTimeout(() => {
      saveNote.mutate({ noteId: activeNoteId, title: nextTitle, markdown });
    }, 1400);
    return () => window.clearTimeout(handle);
  }, [activeNoteId, dirty, markdown, saveNote.isPending, title]);

  useEffect(() => {
    if (!activeNoteId || dirty || !isUntitledNoteTitle(title)) {
      return;
    }
    const suggestion = suggestNoteTitle(markdown);
    if (suggestion && suggestion !== title) {
      setTitle(suggestion);
      setDirtyState(true);
    }
  }, [activeNoteId, dirty, markdown, title]);

  const activeEvidence = useMemo<VerificationEvidence[]>(() => {
    if (!selectedCitation) {
      return [];
    }
    return [
      {
        paper_id: selectedCitation.paper_id,
        kind: selectedCitation.kind || "note",
        reference_text: selectedCitation.reference_text || "",
        pdf_excerpt: selectedCitation.pdf_excerpt || "",
        matched_terms: textTerms(`${selectedCitation.reference_text} ${selectedCitation.pdf_excerpt}`),
        found_in_pdf_text: Boolean(selectedCitation.pdf_excerpt)
      }
    ];
  }, [selectedCitation]);

  function setDirtyState(value: boolean) {
    dirtyRef.current = value;
    setDirty(value);
  }

  function updateMarkdown(value: string) {
    setMarkdown(value);
    setDirtyState(true);
  }

  function updateTitle(value: string) {
    setTitle(value);
    setDirtyState(true);
  }

  function exportCurrentNote() {
    if (!activeNoteId || !markdown.trim()) {
      return;
    }
    downloadMarkdownFile(noteTitleForSave(title, markdown), markdown);
  }

  function captureSelection() {
    const node = textareaRef.current;
    if (!node || node.selectionStart === node.selectionEnd) {
      setSelection(null);
      return null;
    }
    const next = {
      start: node.selectionStart,
      end: node.selectionEnd,
      text: markdown.slice(node.selectionStart, node.selectionEnd)
    };
    setSelection(next);
    return next;
  }

  function handleEditorKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.ctrlKey && event.shiftKey && event.key.toLowerCase() === "k") {
      const next = captureSelection();
      if (next) {
        event.preventDefault();
        setAiInstruction("");
        setAiPreview("");
      }
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      const key = event.key.toLowerCase();
      if (key === "b") {
        event.preventDefault();
        applyWrap("**");
        return;
      }
      if (key === "i") {
        event.preventDefault();
        applyWrap("*");
        return;
      }
      if (key === "e") {
        event.preventDefault();
        applyWrap("`");
        return;
      }
      if (key === "k" && !event.shiftKey) {
        event.preventDefault();
        applyWrap("[", "](https://)");
        return;
      }
    }
    if (event.key === "Enter" && !event.shiftKey) {
      const handled = continueMarkdownLine();
      if (handled) {
        event.preventDefault();
      }
    }
  }

  function pushUndo() {
    setUndoStack((current) => [...current.slice(-14), markdown]);
  }

  function replaceSelection(value: string) {
    if (!selection) {
      return;
    }
    pushUndo();
    updateMarkdown(`${markdown.slice(0, selection.start)}${value}${markdown.slice(selection.end)}`);
    setSelection(null);
    setAiPreview("");
    setAiInstruction("");
  }

  function markSelectionInline() {
    if (!selection) {
      return;
    }
    const selected = markdown.slice(selection.start, selection.end);
    if (!selected.trim()) {
      return;
    }
    const alreadyMarked = markdown.slice(Math.max(0, selection.start - 2), selection.start) === "==" && markdown.slice(selection.end, selection.end + 2) === "==";
    if (alreadyMarked) {
      return;
    }
    const marked = `==${selected}==`;
    pushUndo();
    updateMarkdown(`${markdown.slice(0, selection.start)}${marked}${markdown.slice(selection.end)}`);
    setSelection({
      start: selection.start,
      end: selection.start + marked.length,
      text: marked
    });
  }

  function appendThreadAnswer(thread: NoteAiThread) {
    const text = latestThreadAnswer(thread);
    if (text) {
      insertAtSelection(`\n\n${text}`);
    }
  }

  function submitFollowUp(thread: NoteAiThread) {
    const message = (followUpDrafts[thread.id] ?? "").trim();
    if (!message) {
      return;
    }
    followUp.mutate({ threadId: thread.id, message });
  }

  const workspaceColumns = `${notesListOpen ? "minmax(230px, 0.32fr)" : "46px"} minmax(420px, 1fr) ${contextOpen ? "minmax(320px, 0.44fr)" : "46px"}`;
  const threads = threadsQuery.data?.items ?? [];

  function insertAtSelection(value: string) {
    const node = textareaRef.current;
    const start = node?.selectionStart ?? markdown.length;
    const end = node?.selectionEnd ?? markdown.length;
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${value}${markdown.slice(end)}`);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(start + value.length, start + value.length);
    });
  }

  function insertAfterSelection(value: string) {
    if (!selection) {
      insertAtSelection(`\n\n${value}`);
      return;
    }
    const insertText = `\n\n${value}`;
    const start = selection.end;
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${insertText}${markdown.slice(start)}`);
    setSelection({
      start,
      end: start + insertText.length,
      text: insertText
    });
    setAiPreview("");
    setAiInstruction("");
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(start + insertText.length, start + insertText.length);
    });
  }

  function replaceTextareaRange(start: number, end: number, value: string, cursorOffset = value.length) {
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${value}${markdown.slice(end)}`);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      const cursor = start + cursorOffset;
      textareaRef.current?.setSelectionRange(cursor, cursor);
    });
  }

  function applyWrap(before: string, after = before) {
    const node = textareaRef.current;
    const start = node?.selectionStart ?? markdown.length;
    const end = node?.selectionEnd ?? markdown.length;
    const selected = markdown.slice(start, end) || "Text";
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${before}${selected}${after}${markdown.slice(end)}`);
  }

  function applyLinePrefix(prefix: string) {
    const node = textareaRef.current;
    const start = node?.selectionStart ?? markdown.length;
    const end = node?.selectionEnd ?? markdown.length;
    const selected = markdown.slice(start, end) || "Text";
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${selected.split("\n").map((line) => `${prefix}${line}`).join("\n")}${markdown.slice(end)}`);
  }

  function continueMarkdownLine() {
    const node = textareaRef.current;
    if (!node) {
      return false;
    }
    const start = node.selectionStart;
    const end = node.selectionEnd;
    const lineStart = markdown.lastIndexOf("\n", Math.max(0, start - 1)) + 1;
    const currentLine = markdown.slice(lineStart, start);
    const continuation = markdownContinuation(currentLine);
    if (!continuation) {
      return false;
    }
    if (continuation.removeCurrentPrefix) {
      replaceTextareaRange(lineStart, end, "", 0);
      return true;
    }
    const value = `\n${continuation.prefix}`;
    replaceTextareaRange(start, end, value);
    return true;
  }

  function switchEditorMode(mode: "edit" | "preview") {
    if (editorMode === "preview" && mode === "edit") {
      flushPreviewEdits();
    }
    setEditorMode(mode);
  }

  function flushPreviewEdits() {
    const root = previewRef.current;
    if (!root) {
      return;
    }
    const blocks = splitMarkdownBlocks(markdown);
    const edits = Array.from(root.querySelectorAll<HTMLElement>("[data-preview-block-index]"))
      .map((node) => {
        const blockIndex = Number(node.dataset.previewBlockIndex);
        const block = blocks[blockIndex];
        if (!block || isComplexPreviewBlock(block.raw)) {
          return null;
        }
        return { blockIndex, nextRaw: previewTextToMarkdown(block.raw, node.innerText) };
      })
      .filter((edit): edit is { blockIndex: number; nextRaw: string } => Boolean(edit));
    if (!edits.length) {
      return;
    }
    let nextMarkdown = markdown;
    let offset = 0;
    for (const edit of edits) {
      const block = blocks[edit.blockIndex];
      if (!block) {
        continue;
      }
      const start = block.start + offset;
      const end = block.end + offset;
      if (nextMarkdown.slice(start, end) === edit.nextRaw) {
        continue;
      }
      nextMarkdown = `${nextMarkdown.slice(0, start)}${edit.nextRaw}${nextMarkdown.slice(end)}`;
      offset += edit.nextRaw.length - block.raw.length;
    }
    if (nextMarkdown !== markdown) {
      pushUndo();
      updateMarkdown(nextMarkdown);
    }
  }

  function updatePreviewBlock(blockIndex: number, nextRaw: string) {
    const blocks = splitMarkdownBlocks(markdown);
    const block = blocks[blockIndex];
    if (!block) {
      return;
    }
    const nextMarkdown = `${markdown.slice(0, block.start)}${nextRaw}${markdown.slice(block.end)}`;
    if (nextMarkdown !== markdown) {
      pushUndo();
      updateMarkdown(nextMarkdown);
    }
  }

  function undo() {
    const previous = undoStack.length ? undoStack[undoStack.length - 1] : undefined;
    if (previous !== undefined) {
      setUndoStack((current) => current.slice(0, -1));
      setMarkdown(previous);
      setDirtyState(true);
      return;
    }
    if (activeNoteId) {
      restoreVersion.mutate();
    }
  }

  function openCitation(citation: NoteCitation) {
    setSelectedCitation(citation);
    setContextOpen(true);
    setHistoryOpen(false);
  }

  function clearSelectionAi() {
    setSelection(null);
    setAiInstruction("");
    setAiPreview("");
  }

  function handleSelectionQuestionKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (aiInstruction.trim() && !aiEdit.isPending) {
        aiEdit.mutate();
      }
      return;
    }
    if (event.key === "Escape") {
      event.preventDefault();
      clearSelectionAi();
    }
  }

  const selectionPreview = stripHighlightMarkers(selection?.text ?? "");
  const editorHighlightRanges = selection ? [{ start: selection.start, end: selection.end, className: "textarea-highlight-range--selection" }] : [];
  const editorGhostInsertions =
    selection && aiPreview
      ? [{ index: selection.end, content: `\n\n${aiPreview}`, className: "textarea-ghost-insertion--ai" }]
      : [];

  return (
    <section className="page notes-page">
      <div className="page-title">
        <div>
          <span>{scopeLabel}</span>
          <h1>Notizen</h1>
        </div>
        <div className="button-row">
          <button className="button" type="button" onClick={() => createNote.mutate()} disabled={createNote.isPending}>
            <FilePlus2 size={17} />
            <span>Neu</span>
          </button>
          <button className="button" type="button" disabled={!activeNoteId || !markdown.trim()} onClick={exportCurrentNote}>
            <Download size={17} />
            <span>Export</span>
          </button>
        </div>
      </div>
      {createNote.isError ? <div className="inline-error">Notiz konnte nicht angelegt werden: {formatError(createNote.error)}</div> : null}
      {notesQuery.isError ? <div className="inline-error">Notizen konnten nicht geladen werden: {formatError(notesQuery.error)}</div> : null}
      {saveNote.isError ? <div className="inline-error">Autosave fehlgeschlagen: {formatError(saveNote.error)}</div> : null}

      <div className={`notes-workspace ${notesListOpen ? "" : "notes-workspace--left-collapsed"} ${contextOpen ? "" : "notes-workspace--right-collapsed"}`} style={{ gridTemplateColumns: workspaceColumns }}>
        <aside className={`panel notes-list-panel ${notesListOpen ? "" : "notes-list-panel--collapsed"}`}>
          {notesListOpen ? (
            <>
              <div className="panel-heading">
                <div>
                  <span>Projektnotizen</span>
                  <strong>{notes.length}</strong>
                </div>
                <button className="icon-button" type="button" aria-label="Projektnotizen einklappen" onClick={() => setNotesListOpen(false)}>
                  <ChevronLeft size={17} />
                </button>
              </div>
              <div className="list">
                {notes.map((note) => (
                  <button
                    key={note.id}
                    type="button"
                    className={`list-row note-list-row ${activeNoteId === note.id ? "list-row--active" : ""}`}
                    onClick={() => setActiveNoteId(note.id)}
                  >
                    <strong>{note.title}</strong>
                    <span>{note.excerpt || "Leer"}</span>
                    <small>{note.citation_count ?? 0} Quellen</small>
                  </button>
                ))}
                {!notes.length ? <EmptyState title="Noch keine Notizen" /> : null}
              </div>
            </>
          ) : (
            <button className="collapsed-panel-tab" type="button" onClick={() => setNotesListOpen(true)}>
              <ChevronRight size={17} />
              <span>Notizen</span>
            </button>
          )}
        </aside>

        <main className="note-editor-shell">
          {activeNoteId ? (
            <>
              <div className="note-editor-header">
                <input className="note-title-input" value={title} onChange={(event) => updateTitle(event.target.value)} placeholder="Titel" />
                <div className="button-row">
                  <button className="icon-button" type="button" aria-label="Undo" onClick={undo}>
                    <Undo2 size={17} />
                  </button>
                  <button className="icon-button" type="button" aria-label="Redo" disabled>
                    <Redo2 size={17} />
                  </button>
                  <button className="icon-button" type="button" aria-label="KI-Verlauf" onClick={() => setHistoryOpen((current) => !current)}>
                    {historyOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
                  </button>
                  <button className="icon-button" type="button" aria-label="Notiz loeschen" onClick={() => deleteNote.mutate()} disabled={deleteNote.isPending}>
                    <Trash2 size={17} />
                  </button>
                </div>
              </div>

              <div className="markdown-toolbar">
                <button className="icon-button" type="button" aria-label="Fett" onClick={() => applyWrap("**")}>
                  <Bold size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Kursiv" onClick={() => applyWrap("*")}>
                  <Italic size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Zitat" onClick={() => applyLinePrefix("> ")}>
                  <Quote size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Liste" onClick={() => applyLinePrefix("- ")}>
                  <List size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Code" onClick={() => applyWrap("`")}>
                  <Code size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Link" onClick={() => applyWrap("[", "](https://)")}>
                  <Link size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Highlight" onClick={() => applyWrap("==")}>
                  <Highlighter size={17} />
                </button>
                <select aria-label="Textfarbe" onChange={(event) => event.target.value && applyWrap(`<span style="color:${event.target.value}">`, "</span>")} defaultValue="">
                  <option value="">Farbe</option>
                  <option value="#2563eb">Blau</option>
                  <option value="#16865a">Gruen</option>
                  <option value="#a76500">Amber</option>
                  <option value="#ba3434">Rot</option>
                </select>
                <button className="icon-button" type="button" aria-label="Tabelle" onClick={() => insertAtSelection("\n\n| Spalte 1 | Spalte 2 |\n|---|---|\n| Wert | Wert |\n")}>
                  <Table2 size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Bild einfuegen" onClick={() => imageInputRef.current?.click()} disabled={!activeNoteId}>
                  <ImagePlus size={17} />
                </button>
                <input ref={imageInputRef} className="hidden-input" type="file" accept="image/*" onChange={(event) => event.target.files?.[0] && uploadAsset.mutate(event.target.files[0])} />
                <div className="segmented markdown-mode-toggle">
                  <button type="button" className={editorMode === "edit" ? "active" : ""} onClick={() => switchEditorMode("edit")}>
                    Edit
                  </button>
                  <button type="button" className={editorMode === "preview" ? "active" : ""} onClick={() => switchEditorMode("preview")}>
                    Preview
                  </button>
                </div>
              </div>

              <div className="markdown-editor-grid">
                {editorMode === "edit" ? (
                  <div className="markdown-editor-wrap markdown-editor-wrap--highlighted" ref={editorWrapRef}>
                    <TextareaHighlightLayer text={markdown} ranges={editorHighlightRanges} insertions={editorGhostInsertions} scrollTop={editorScrollTop} />
                    <textarea
                      ref={textareaRef}
                      className="markdown-editor markdown-editor--highlighted"
                      value={markdown}
                      onChange={(event) => updateMarkdown(event.target.value)}
                      onSelect={captureSelection}
                      onScroll={(event) => setEditorScrollTop(event.currentTarget.scrollTop)}
                      onKeyDown={handleEditorKeyDown}
                      placeholder="Markdown schreiben"
                    />
                    {selection ? (
                      <div className="selection-ai-popover">
                        <div>
                          <Sparkles size={16} />
                          <strong>{selectionPreview.length} Zeichen markiert</strong>
                        </div>
                        <blockquote>{selectionPreview}</blockquote>
                        {!aiPreview ? (
                          <div className="selection-ai-question-row">
                            <input
                              value={aiInstruction}
                              onChange={(event) => setAiInstruction(event.target.value)}
                              onKeyDown={handleSelectionQuestionKeyDown}
                              placeholder="KI-Frage zu dieser Auswahl"
                            />
                            <button className="button button-primary" type="button" disabled={!aiInstruction.trim() || aiEdit.isPending} onClick={() => aiEdit.mutate()}>
                              Fragen
                            </button>
                          </div>
                        ) : null}
                        {aiPreview ? (
                          <div className="ai-preview-card">
                            <span>Antwort</span>
                            <pre>{aiPreview}</pre>
                            <div className="button-row">
                              <button className="button button-primary" type="button" onClick={() => replaceSelection(aiPreview)}>
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
                  <MarkdownPreview
                    previewRef={previewRef}
                    markdown={markdown}
                    citations={citations}
                    onCitationClick={openCitation}
                    editable
                    onBlockChange={updatePreviewBlock}
                  />
                )}
              </div>
            </>
          ) : (
            <EmptyState title={notesQuery.isLoading ? "Lade Notizen" : "Keine Notiz gewaehlt"} />
          )}
        </main>

        <aside className={`note-context-panel ${contextOpen ? "" : "note-context-panel--collapsed"}`}>
          {!contextOpen ? (
            <button className="collapsed-panel-tab" type="button" onClick={() => setContextOpen(true)}>
              <PanelRightOpen size={17} />
              <span>Quellen</span>
            </button>
          ) : historyOpen ? (
            <section className="panel note-history-panel">
              <div className="panel-heading">
                <div>
                  <span>KI-Verlauf</span>
                  <strong>{threadsQuery.data?.total ?? 0}</strong>
                </div>
                <button className="icon-button" type="button" aria-label="Quellen anzeigen" onClick={() => setHistoryOpen(false)}>
                  <Quote size={17} />
                </button>
              </div>
              <AiThreadList
                threads={threads}
                activeThreadId={activeThreadId}
                followUpDrafts={followUpDrafts}
                isSubmitting={followUp.isPending}
                onActiveThreadChange={setActiveThreadId}
                onDraftChange={(threadId, value) => setFollowUpDrafts((current) => ({ ...current, [threadId]: value }))}
                onFollowUp={submitFollowUp}
                onInsert={appendThreadAnswer}
                onToggleCollapse={(thread) => updateThreadUi.mutate({ thread, collapsed: !Boolean(thread.ui_state?.collapsed) })}
              />
            </section>
          ) : (
            <>
              <section className="panel citation-panel">
                <div className="panel-heading">
                  <div>
                    <span>Quellen</span>
                    <strong>{citations.length}</strong>
                  </div>
                  <div className="button-row">
                    <button className="icon-button" type="button" aria-label={notePdfOpen ? "PDF einklappen" : "PDF anzeigen"} onClick={() => setNotePdfOpen((current) => !current)}>
                      {notePdfOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
                    </button>
                    <button className="icon-button" type="button" aria-label="KI-Verlauf anzeigen" onClick={() => setHistoryOpen(true)}>
                      <Sparkles size={17} />
                    </button>
                  </div>
                </div>
                <div className="list">
                  {citations.map((citation) => (
                    <button className="list-row note-citation-row" type="button" key={citation.id} onClick={() => openCitation(citation)}>
                      <strong>{citation.title || citation.paper_id}</strong>
                      <span>{citation.reference_text || citation.kind}</span>
                      <small>{citation.paper_id}</small>
                    </button>
                  ))}
                  {!citations.length ? <div className="muted-row">Keine Quellen in dieser Notiz</div> : null}
                </div>
              </section>
              {notePdfOpen ? (
                <PdfPane
                  url={selectedCitation ? api.paperPdfUrl(selectedCitation.paper_id, selectedCitation.title ?? "") : null}
                  title={selectedCitation?.title ?? selectedCitation?.paper_id}
                  evidences={activeEvidence}
                  activeEvidenceIndex={0}
                  onCollapse={() => setNotePdfOpen(false)}
                />
              ) : (
                <button className="collapsed-panel-tab collapsed-panel-tab--horizontal" type="button" onClick={() => setNotePdfOpen(true)}>
                  <PanelRightOpen size={17} />
                  <span>PDF</span>
                </button>
              )}
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

function AiThreadList({
  threads,
  activeThreadId,
  followUpDrafts,
  isSubmitting,
  onActiveThreadChange,
  onDraftChange,
  onFollowUp,
  onInsert,
  onToggleCollapse
}: {
  threads: NoteAiThread[];
  activeThreadId: string;
  followUpDrafts: Record<string, string>;
  isSubmitting: boolean;
  onActiveThreadChange: (threadId: string) => void;
  onDraftChange: (threadId: string, value: string) => void;
  onFollowUp: (thread: NoteAiThread) => void;
  onInsert: (thread: NoteAiThread) => void;
  onToggleCollapse: (thread: NoteAiThread) => void;
}) {
  if (!threads.length) {
    return <div className="muted-row">Noch keine KI-Fragen</div>;
  }
  return (
    <div className="ai-thread-list">
      {threads.map((thread) => {
        const collapsed = Boolean(thread.ui_state?.collapsed);
        const answer = latestThreadAnswer(thread);
        const messages = threadDisplayMessages(thread);
        return (
          <article
            className={`note-thread-row ai-thread-card ${activeThreadId === thread.id ? "ai-thread-card--active" : ""}`}
            key={thread.id}
            style={threadSizeStyle(thread)}
            onFocus={() => onActiveThreadChange(thread.id)}
          >
            <button className="ai-thread-header" type="button" onClick={() => onActiveThreadChange(thread.id)}>
              <strong>{thread.instruction}</strong>
              <span>{thread.anchor_quote || thread.selected_text}</span>
            </button>
            <div className="button-row">
              <button className="button" type="button" onClick={() => onToggleCollapse(thread)}>
                {collapsed ? "Oeffnen" : "Einklappen"}
              </button>
              <button className="button" type="button" onClick={() => onInsert(thread)} disabled={!answer}>
                Einfuegen
              </button>
            </div>
            {!collapsed ? (
              <>
                <div className="ai-thread-messages">
                  {messages.map((message) => (
                    <div className={`ai-thread-message ai-thread-message--${message.role === "assistant" ? "assistant" : "user"}`} key={message.id}>
                      <span>{message.role === "assistant" ? "KI" : "Du"}</span>
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
                        onFollowUp(thread);
                      }
                    }}
                    placeholder="Folgefrage zu dieser Auswahl"
                  />
                  <button className="button button-primary" type="button" onClick={() => onFollowUp(thread)} disabled={isSubmitting || !(followUpDrafts[thread.id] ?? "").trim()}>
                    Fragen
                  </button>
                </div>
              </>
            ) : null}
          </article>
        );
      })}
    </div>
  );
}

function MarkdownPreview({
  previewRef,
  markdown,
  citations,
  onCitationClick,
  editable = false,
  onBlockChange
}: {
  previewRef?: RefObject<HTMLElement>;
  markdown: string;
  citations: NoteCitation[];
  onCitationClick: (citation: NoteCitation) => void;
  editable?: boolean;
  onBlockChange?: (blockIndex: number, nextRaw: string) => void;
}) {
  const citationById = useMemo(() => new Map(citations.map((citation) => [citation.id, citation])), [citations]);
  const blocks = useMemo(() => splitMarkdownBlocks(markdown), [markdown]);
  return (
    <article ref={previewRef} className={`markdown-preview ${editable ? "markdown-preview--editable" : ""}`}>
      {blocks.map((block, index) => {
        const rendered = renderBlock(block.raw, `${index}`, citationById, onCitationClick);
        if (!editable || !rendered) {
          return rendered;
        }
        const canEditBlock = !isComplexPreviewBlock(block.raw);
        return (
          <div
            key={`editable-${index}`}
            data-preview-block-index={index}
            className={`editable-preview-block ${canEditBlock ? "" : "editable-preview-block--readonly"}`}
            contentEditable={canEditBlock}
            suppressContentEditableWarning
            onBlur={(event) => {
              if (canEditBlock) {
                const editedText = event.currentTarget.innerText;
                window.setTimeout(() => {
                  onBlockChange?.(index, previewTextToMarkdown(block.raw, editedText));
                }, 0);
              }
            }}
            onKeyDown={(event) => {
              if ((event.ctrlKey || event.metaKey) && ["b", "i", "e", "k"].includes(event.key.toLowerCase())) {
                event.preventDefault();
              }
            }}
          >
            {rendered}
          </div>
        );
      })}
    </article>
  );
}

function renderBlock(block: string, key: string, citations: Map<string, NoteCitation>, onCitationClick: (citation: NoteCitation) => void) {
  const trimmed = block.trim();
  if (!trimmed) {
    return null;
  }
  if (/^!\[[^\]]*\]\([^)]+\)$/.test(trimmed)) {
    const match = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(trimmed);
    return <img key={key} className="markdown-preview-image" alt={match?.[1] ?? ""} src={match?.[2]} />;
  }
  if (trimmed.startsWith("# ")) {
    return <h1 key={key}>{renderInline(trimmed.slice(2), citations, onCitationClick)}</h1>;
  }
  if (trimmed.startsWith("## ")) {
    return <h2 key={key}>{renderInline(trimmed.slice(3), citations, onCitationClick)}</h2>;
  }
  if (trimmed.startsWith(">")) {
    return <blockquote key={key}>{renderInline(trimmed.replace(/^>\s?/gm, ""), citations, onCitationClick)}</blockquote>;
  }
  if (/^\|.+\|\n\|[-:|\s]+\|/.test(trimmed)) {
    const rows = trimmed.split("\n").filter((line) => line.trim().startsWith("|"));
    return (
      <table key={key}>
        <tbody>
          {rows.filter((_, rowIndex) => rowIndex !== 1).map((row, rowIndex) => (
            <tr key={`${key}-${rowIndex}`}>
              {row.split("|").slice(1, -1).map((cell, cellIndex) => {
                const Tag = rowIndex === 0 ? "th" : "td";
                return <Tag key={`${key}-${rowIndex}-${cellIndex}`}>{renderInline(cell.trim(), citations, onCitationClick)}</Tag>;
              })}
            </tr>
          ))}
        </tbody>
      </table>
    );
  }
  if (/^- /m.test(trimmed)) {
    return (
      <ul key={key}>
        {trimmed.split("\n").map((line, itemIndex) => (
          <li key={`${key}-${itemIndex}`}>{renderInline(line.replace(/^- /, ""), citations, onCitationClick)}</li>
        ))}
      </ul>
    );
  }
  return <p key={key}>{renderInline(trimmed, citations, onCitationClick)}</p>;
}

function renderInline(text: string, citations: Map<string, NoteCitation>, onCitationClick: (citation: NoteCitation) => void) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|==[^=]+==|<mark(?:\s+[^>]*)?>.*?<\/mark>|<span style="color:[^"]+">.*?<\/span>|\[[^\]]+\]\([^)]+\))/g);
  return parts.map((part, index) => {
    const citationMatch = /^\[([^\]]+)\]\(sciencekg:\/\/citation\/([^)]+)\)$/.exec(part);
    if (citationMatch) {
      const citation = citations.get(citationMatch[2]);
      return (
        <button key={`${part}-${index}`} type="button" className="citation-link citation-link--mapped" onClick={() => citation && onCitationClick(citation)}>
          {citationMatch[1]}
        </button>
      );
    }
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
    if (/^<mark(?:\s+[^>]*)?>/.test(part)) {
      return <mark key={`${part}-${index}`}>{part.replace(/^<mark(?:\s+[^>]*)?>|<\/mark>$/g, "")}</mark>;
    }
    const colorMatch = /^<span style="color:([^"]+)">(.*)<\/span>$/.exec(part);
    if (colorMatch) {
      return <span key={`${part}-${index}`} style={{ color: colorMatch[1] }}>{colorMatch[2]}</span>;
    }
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}

function absoluteUrl(value: string) {
  if (/^https?:\/\//.test(value)) {
    return value;
  }
  return `${API_BASE_URL}${value}`;
}

function stripHighlightMarkers(value: string) {
  return value.replace(/^==([\s\S]*)==$/, "$1");
}

function latestThreadAnswer(thread: NoteAiThread) {
  const messages = thread.messages?.length ? thread.messages : legacyThreadMessages(thread);
  const answer = [...messages].reverse().find((message) => message.role === "assistant")?.content;
  return (answer || thread.replacement_text || thread.response_text || "").trim();
}

function threadDisplayMessages(thread: NoteAiThread) {
  const messages = thread.messages?.length ? thread.messages : legacyThreadMessages(thread);
  const answer = (thread.replacement_text || thread.response_text || "").trim();
  const hasAssistantText = messages.some((message) => message.role === "assistant" && message.content.trim());
  if (!answer || hasAssistantText) {
    return messages;
  }
  return [
    ...messages.filter((message) => message.role !== "assistant" || message.content.trim()),
    {
      id: `${thread.id}:assistant:fallback`,
      thread_id: thread.id,
      note_id: thread.note_id,
      role: "assistant" as const,
      content: answer,
      created_timestamp: thread.updated_timestamp ?? thread.created_timestamp
    }
  ];
}

function legacyThreadMessages(thread: NoteAiThread) {
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

function threadSizeStyle(thread: NoteAiThread) {
  const width = Number(thread.ui_state?.width || 0);
  const height = Number(thread.ui_state?.height || 0);
  return {
    width: width > 260 ? `${width}px` : undefined,
    minHeight: height > 120 ? `${height}px` : undefined
  };
}

function uiStateKey(key: string) {
  return `sciencekg.notes.ui.${key}`;
}

function loadBooleanUiState(key: string, fallback: boolean) {
  try {
    const value = window.localStorage.getItem(uiStateKey(key));
    return value === null ? fallback : value === "true";
  } catch {
    return fallback;
  }
}

function saveBooleanUiState(key: string, value: boolean) {
  try {
    window.localStorage.setItem(uiStateKey(key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}

function formatError(error: unknown) {
  if (error instanceof Error) {
    return error.message;
  }
  return "Unbekannter Fehler";
}

function markdownContinuation(line: string) {
  const bullet = /^(\s*)([-*+])\s+(.*)$/.exec(line);
  if (bullet) {
    return bullet[3].trim() ? { prefix: `${bullet[1]}${bullet[2]} ` } : { prefix: "", removeCurrentPrefix: true };
  }
  const numbered = /^(\s*)(\d+)([.)])\s+(.*)$/.exec(line);
  if (numbered) {
    return numbered[4].trim()
      ? { prefix: `${numbered[1]}${Number(numbered[2]) + 1}${numbered[3]} ` }
      : { prefix: "", removeCurrentPrefix: true };
  }
  const quote = /^(\s*>\s?)(.*)$/.exec(line);
  if (quote) {
    return quote[2].trim() ? { prefix: quote[1] } : { prefix: "", removeCurrentPrefix: true };
  }
  return null;
}

type MarkdownBlock = {
  raw: string;
  start: number;
  end: number;
};

function splitMarkdownBlocks(value: string): MarkdownBlock[] {
  const blocks: MarkdownBlock[] = [];
  const pattern = /\S[\s\S]*?(?=\n{2,}|$)/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(value)) !== null) {
    blocks.push({ raw: match[0], start: match.index, end: match.index + match[0].length });
    if (match.index === pattern.lastIndex) {
      pattern.lastIndex += 1;
    }
  }
  return blocks;
}

function isComplexPreviewBlock(block: string) {
  const trimmed = block.trim();
  return /^!\[[^\]]*\]\([^)]+\)$/.test(trimmed) || /^\|.+\|\n\|[-:|\s]+\|/.test(trimmed);
}

function previewTextToMarkdown(original: string, editedText: string) {
  const text = editedText.replace(/\u00a0/g, " ").replace(/\n{3,}/g, "\n\n").trimEnd();
  const originalTrimmed = original.trim();
  if (originalTrimmed.startsWith("# ")) {
    return `# ${firstLine(text)}`;
  }
  if (originalTrimmed.startsWith("## ")) {
    return `## ${firstLine(text)}`;
  }
  if (originalTrimmed.startsWith(">")) {
    return text.split("\n").map((line) => `> ${line}`).join("\n");
  }
  if (/^- /m.test(originalTrimmed)) {
    return text.split("\n").filter(Boolean).map((line) => `- ${line.replace(/^[-*+]\s+/, "")}`).join("\n");
  }
  return text;
}

function firstLine(text: string) {
  return text.split("\n")[0]?.trim() ?? "";
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

function textTerms(text: string) {
  return Array.from(new Set(text.toLowerCase().replace(/[^a-z0-9-]+/g, " ").split(" "))).filter((term) => term.length >= 5).slice(0, 12);
}
