// Minimal Notizen tab for the AI-Cursor overlay: a plain title + textarea editor instead
// of the full desktop NotesSurface (toolbar/citation panel/highlight-layer/thread anchors)
// that used to be crammed into this small floating window. Images (paste/drop) and a
// one-shot "AI adjust selection" action stay available — everything else is dropped.

import { useEffect, useLayoutEffect, useRef, useState } from "react";
import type {
  ClipboardEvent as ReactClipboardEvent,
  DragEvent as ReactDragEvent,
  KeyboardEvent as ReactKeyboardEvent,
  PointerEvent as ReactPointerEvent
} from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Check,
  ChevronDown,
  ChevronRight,
  Columns2,
  Eye,
  ImagePlus,
  Loader2,
  Minus,
  Pencil,
  Plus,
  SeparatorHorizontal,
  Sparkles,
  Trash2,
  X
} from "lucide-react";

import { api } from "../../api";
import { isTauri, nativeInvoke } from "../../native";
import { noteProjectId } from "../../projectScope";
import { useAppState } from "../../state";
import type { Note } from "../../types";
import { MarkdownPreview } from "../NotesSubComponents";
import {
  absoluteUrl,
  applyTabIndent,
  attachTextareaAutoSync,
  clampSplitRatio,
  continueMarkdownLine,
  dividerSnippetForTextarea,
  formatError,
  isUntitledNoteTitle,
  loadBooleanUiState,
  loadNumberUiState,
  noteTitleForSave,
  saveBooleanUiState,
  saveNumberUiState,
  shortText,
  suggestNoteTitle,
  toggleWrap,
  withPreservedCitationLinks
} from "../notesHelpers";
import { clampNotesSize, loadOverlayNotesSize, saveOverlayNotesSize, type OverlaySize } from "./overlayNotesSize";

type SelectionState = { start: number; end: number; text: string };

export function OverlayNotesPanel() {
  const { activeProject, setActiveProject, provider, model } = useAppState();
  const queryClient = useQueryClient();
  const scopedProjectId = noteProjectId(activeProject);

  const [activeNoteId, setActiveNoteId] = useState("");
  const [title, setTitle] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [dirty, setDirty] = useState(false);
  const [editorMode, setEditorMode] = useState<"edit" | "preview" | "split">("edit");
  const [selection, setSelection] = useState<SelectionState | null>(null);
  const [aiInstruction, setAiInstruction] = useState("");
  const [pickerOpen, setPickerOpen] = useState(() => loadBooleanUiState("overlay.pickerOpen", true));
  const [splitRatio, setSplitRatio] = useState(() => loadNumberUiState("overlay.splitRatio", 0.5));
  const splitContainerRef = useRef<HTMLDivElement | null>(null);
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const [editorScrollLeft, setEditorScrollLeft] = useState(0);
  const previewRef = useRef<HTMLElement | null>(null);
  const previewScrollRef = useRef({ top: 0, left: 0 });

  const dirtyRef = useRef(false);
  const markdownRef = useRef("");
  const latestDraftRef = useRef({ noteId: "", title: "", markdown: "" });
  const loadedNoteIdRef = useRef("");
  // Last server markdown for the active note — guards against an empty overwrite wiping content.
  const loadedServerMarkdownRef = useRef("");
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const sizeRef = useRef<OverlaySize>(loadOverlayNotesSize());

  function setDirtyState(value: boolean) {
    dirtyRef.current = value;
    setDirty(value);
  }

  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
  const projects = projectsQuery.data?.projects ?? [];

  const notesQuery = useQuery({
    queryKey: ["notes", scopedProjectId],
    queryFn: () => api.listNotes(scopedProjectId)
  });
  const notes: Note[] = notesQuery.data?.items ?? [];

  const noteQuery = useQuery({
    queryKey: ["note", activeNoteId],
    queryFn: () => api.getNote(activeNoteId),
    enabled: Boolean(activeNoteId)
  });
  const currentNote = noteQuery.data?.note;

  const createNote = useMutation({
    mutationFn: () => api.createNote(scopedProjectId, { title: "Neue Notiz", markdown: "# Neue Notiz\n\n" }),
    onSuccess: ({ note }) => {
      loadedNoteIdRef.current = note.id;
      setActiveNoteId(note.id);
      setTitle(note.title);
      setMarkdown(note.markdown);
      setDirtyState(false);
      setSelection(null);
      setAiInstruction("");
      aiEdit.reset();
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes", scopedProjectId] });
    }
  });

  const deleteNoteMutation = useMutation({
    mutationFn: (noteId: string) => api.deleteNote(noteId),
    onSuccess: (_data, noteId) => {
      if (activeNoteId === noteId) {
        loadedNoteIdRef.current = "";
        setActiveNoteId("");
        setTitle("");
        setMarkdown("");
        setDirtyState(false);
        setSelection(null);
        setAiInstruction("");
        aiEdit.reset();
      }
      queryClient.invalidateQueries({ queryKey: ["notes", scopedProjectId] });
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
      loadedServerMarkdownRef.current = note.markdown;
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes", scopedProjectId] });
    }
  });

  const uploadAsset = useMutation({
    mutationFn: (file: File) => api.uploadNoteAsset(activeNoteId, file),
    onSuccess: ({ asset }) => {
      // Relative asset path (not absoluteUrl) — the baked ephemeral Tauri port went stale on restart
      // and the image vanished; the renderer re-attaches the live API base at display time.
      insertAtCaret(`![${asset.filename}](${asset.url})`);
      queryClient.invalidateQueries({ queryKey: ["note", activeNoteId] });
    }
  });

  const aiEdit = useMutation({
    mutationFn: () =>
      api.noteAiEdit(activeNoteId, {
        selected_text: selection?.text ?? "",
        instruction: aiInstruction,
        provider,
        model,
        use_kg_evidence: true
      })
  });

  useEffect(() => {
    saveBooleanUiState("overlay.pickerOpen", pickerOpen);
  }, [pickerOpen]);

  // Project switch: this window has no PDF/citation panel to keep in sync — just drop
  // whatever was open and let the "select first note" effect below pick a fresh one.
  useEffect(() => {
    loadedNoteIdRef.current = "";
    setActiveNoteId("");
    setTitle("");
    setMarkdown("");
    setDirtyState(false);
  }, [scopedProjectId]);

  useEffect(() => {
    if (!activeNoteId && notes[0]) {
      setActiveNoteId(notes[0].id);
    }
  }, [activeNoteId, notes]);

  useEffect(() => {
    if (!currentNote) return;
    const switchedNote = loadedNoteIdRef.current !== currentNote.id;
    if (dirtyRef.current) return;
    if (!switchedNote && title === currentNote.title && markdown === currentNote.markdown) return;
    loadedNoteIdRef.current = currentNote.id;
    loadedServerMarkdownRef.current = currentNote.markdown;
    setTitle(currentNote.title);
    setMarkdown(currentNote.markdown);
    setDirtyState(false);
    if (switchedNote) {
      setSelection(null);
      setAiInstruction("");
      aiEdit.reset();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [currentNote?.id, currentNote?.markdown, currentNote?.title, currentNote?.updated_timestamp, dirty, markdown, title]);

  useEffect(() => {
    markdownRef.current = markdown;
    latestDraftRef.current = { noteId: activeNoteId, title, markdown };
  }, [activeNoteId, markdown, title]);

  // Autosave — same 1400ms debounce as the full Notes editor.
  useEffect(() => {
    if (!activeNoteId || !dirty || saveNote.isPending) return;
    // Never let a background/empty state overwrite a note that has content unless the editor is
    // focused (i.e. the user is deliberately clearing it).
    if (
      markdown.trim() === "" &&
      loadedServerMarkdownRef.current.trim() !== "" &&
      document.activeElement !== textareaRef.current
    ) {
      return;
    }
    const nextTitle = noteTitleForSave(title, markdown);
    if (nextTitle !== title) setTitle(nextTitle);
    const handle = window.setTimeout(() => {
      saveNote.mutate({ noteId: activeNoteId, title: nextTitle, markdown });
    }, 1400);
    return () => window.clearTimeout(handle);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeNoteId, dirty, markdown, saveNote.isPending, title]);

  useEffect(() => {
    if (!activeNoteId || dirty || !isUntitledNoteTitle(title)) return;
    const suggestion = suggestNoteTitle(markdown);
    if (suggestion && suggestion !== title) {
      setTitle(suggestion);
      setDirtyState(true);
    }
  }, [activeNoteId, dirty, markdown, title]);

  // Flush an unsaved edit if the tab is switched away inside the debounce window — but never flush an
  // empty draft over a note that had content (that silent wipe is the reported "section went blank").
  useEffect(() => {
    return () => {
      const draft = latestDraftRef.current;
      if (draft.markdown.trim() === "" && loadedServerMarkdownRef.current.trim() !== "") {
        return;
      }
      if (dirtyRef.current && draft.noteId) {
        void api.updateNote(draft.noteId, { title: draft.title, markdown: draft.markdown }).catch(() => {});
      }
    };
  }, []);

  function insertAtCaret(snippet: string) {
    const node = textareaRef.current;
    const fallback = markdownRef.current.length;
    const start = node?.selectionStart ?? fallback;
    const end = node?.selectionEnd ?? fallback;
    const base = markdownRef.current;
    const next = `${base.slice(0, start)}${snippet}${base.slice(end)}`;
    markdownRef.current = next;
    setMarkdown(next);
    setDirtyState(true);
    requestAnimationFrame(() => {
      if (!node) return;
      const caret = start + snippet.length;
      node.focus();
      node.setSelectionRange(caret, caret);
    });
  }

  function applyOverlayWrap(before: string, after = before) {
    const node = textareaRef.current;
    const fallback = markdownRef.current.length;
    const start = node?.selectionStart ?? fallback;
    const end = node?.selectionEnd ?? fallback;
    const result = toggleWrap(markdownRef.current, start, end, before, after);
    markdownRef.current = result.next;
    setMarkdown(result.next);
    setDirtyState(true);
    requestAnimationFrame(() => {
      if (!node) return;
      node.focus();
      node.setSelectionRange(result.selStart, result.selEnd);
    });
  }

  function handleOverlayEditorKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Tab") {
      event.preventDefault();
      const node = textareaRef.current;
      if (!node) return;
      const result = applyTabIndent(markdownRef.current, node.selectionStart, node.selectionEnd, event.shiftKey);
      if (!result) return;
      markdownRef.current = result.next;
      setMarkdown(result.next);
      setDirtyState(true);
      requestAnimationFrame(() => {
        node.focus();
        node.setSelectionRange(result.selStart, result.selEnd);
      });
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      const node = textareaRef.current;
      if (!node) return;
      const result = continueMarkdownLine(markdownRef.current, node.selectionStart, node.selectionEnd);
      if (!result) return;
      event.preventDefault();
      markdownRef.current = result.next;
      setMarkdown(result.next);
      setDirtyState(true);
      requestAnimationFrame(() => {
        node.focus();
        node.setSelectionRange(result.selStart, result.selEnd);
      });
      return;
    }
    if (event.ctrlKey || event.metaKey) {
      const key = event.key.toLowerCase();
      if (key === "b") {
        event.preventDefault();
        applyOverlayWrap("**");
        return;
      }
      if (key === "i") {
        event.preventDefault();
        applyOverlayWrap("*");
        return;
      }
      if (key === "e") {
        event.preventDefault();
        applyOverlayWrap("`");
        return;
      }
      if (key === "k" && !event.shiftKey) {
        event.preventDefault();
        applyOverlayWrap("[", "](https://)");
        return;
      }
    }
  }

  // Edit/Preview/Split each conditionally mount their pane, so switching modes remounts a
  // fresh DOM node whose scroll resets to 0 — restore the last known offset instead of always
  // jumping back to the top.
  useLayoutEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.scrollTop = editorScrollTop;
    node.scrollLeft = editorScrollLeft;
    return attachTextareaAutoSync(node, () => markdownRef.current, (next) => {
      markdownRef.current = next;
      setMarkdown(next);
      setDirtyState(true);
    });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorMode]);

  useLayoutEffect(() => {
    const node = previewRef.current;
    if (!node) return;
    node.scrollTop = previewScrollRef.current.top;
    node.scrollLeft = previewScrollRef.current.left;
  }, [editorMode]);

  function captureSelection() {
    const node = textareaRef.current;
    if (!node) return;
    if (node.selectionStart === node.selectionEnd) {
      setSelection(null);
      return;
    }
    setSelection({ start: node.selectionStart, end: node.selectionEnd, text: markdown.slice(node.selectionStart, node.selectionEnd) });
    aiEdit.reset();
  }

  function handlePaste(event: ReactClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith("image/"));
    if (!files.length || !activeNoteId) return;
    event.preventDefault();
    for (const file of files) uploadAsset.mutate(file);
  }

  function handleDragOver(event: ReactDragEvent<HTMLTextAreaElement>) {
    if (event.dataTransfer.types.includes("Files")) {
      event.preventDefault();
      event.dataTransfer.dropEffect = "copy";
    }
  }

  function handleDrop(event: ReactDragEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.dataTransfer.files ?? []).filter((file) => file.type.startsWith("image/"));
    if (!files.length || !activeNoteId) return;
    event.preventDefault();
    for (const file of files) uploadAsset.mutate(file);
  }

  function applyAiEdit() {
    if (!selection || !aiEdit.data) return;
    const base = markdownRef.current;
    const replaced = withPreservedCitationLinks(base.slice(selection.start, selection.end), aiEdit.data.replacement_text);
    const next = `${base.slice(0, selection.start)}${replaced}${base.slice(selection.end)}`;
    markdownRef.current = next;
    setMarkdown(next);
    setDirtyState(true);
    setSelection(null);
    setAiInstruction("");
    aiEdit.reset();
  }

  function discardAiEdit() {
    aiEdit.reset();
    setAiInstruction("");
  }

  function startNotesResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const startX = event.clientX;
    const startY = event.clientY;
    const start = sizeRef.current;
    let frame: number | null = null;
    let latest = start;
    const move = (moveEvent: PointerEvent) => {
      latest = clampNotesSize({
        width: start.width + (moveEvent.clientX - startX),
        height: start.height + (moveEvent.clientY - startY)
      });
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = null;
        void nativeInvoke("overlay_resize", latest);
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      sizeRef.current = latest;
      saveOverlayNotesSize(latest);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function startSplitResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const container = splitContainerRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    // Narrow overlay widths stack the panes into a column (styles.css, 560px breakpoint) —
    // follow whichever axis is actually the split direction at drag-start.
    const stacked = window.getComputedStyle(container).flexDirection === "column";
    let frame: number | null = null;
    let latest = splitRatio;
    const move = (moveEvent: PointerEvent) => {
      latest = stacked
        ? clampSplitRatio((moveEvent.clientY - rect.top) / rect.height)
        : clampSplitRatio((moveEvent.clientX - rect.left) / rect.width);
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = null;
        setSplitRatio(latest);
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      saveNumberUiState("overlay.splitRatio", latest);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  const saveStatus = saveNote.isPending
    ? "Speichert…"
    : saveNote.isError
      ? `Fehler: ${formatError(saveNote.error)}`
      : dirty
        ? "Ungespeichert"
        : activeNoteId
          ? "Gespeichert"
          : "";

  const activeProjectLabel = activeProject ? projects.find((project) => project.id === activeProject)?.name ?? "Projekt" : "Alle Papers";
  const activeNoteLabel = activeNoteId ? notes.find((note) => note.id === activeNoteId)?.title || "Ohne Titel" : "Keine Notiz";

  return (
    <div className="overlay-notes-view">
      <button
        type="button"
        className="overlay-notes-picker-toggle"
        onClick={() => setPickerOpen((current) => !current)}
        aria-expanded={pickerOpen}
      >
        {pickerOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        <span>{activeProjectLabel} · {activeNoteLabel}</span>
      </button>
      {pickerOpen ? (
        <>
          <select
            className="overlay-notes-project-select"
            aria-label="Projekt"
            value={activeProject ?? ""}
            onChange={(event) => setActiveProject(event.target.value || undefined)}
          >
            <option value="">Alle Papers</option>
            {projects.map((project) => (
              <option key={project.id} value={project.id}>
                {project.name}
              </option>
            ))}
          </select>

          <div className="overlay-notes-toolbar">
            <select
              className="overlay-notes-project-select overlay-notes-note-select"
              aria-label="Notiz"
              value={activeNoteId}
              onChange={(event) => setActiveNoteId(event.target.value)}
            >
              {!notes.length ? <option value="">Keine Notizen</option> : null}
              {notes.map((note) => (
                <option key={note.id} value={note.id}>
                  {note.title || "Ohne Titel"}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="overlay-close"
              title="Neue Notiz"
              aria-label="Neue Notiz"
              disabled={createNote.isPending}
              onClick={() => createNote.mutate()}
            >
              <Plus size={15} />
            </button>
            <button
              type="button"
              className="overlay-close"
              title="Notiz löschen"
              aria-label="Notiz löschen"
              disabled={!activeNoteId || deleteNoteMutation.isPending}
              onClick={() => activeNoteId && deleteNoteMutation.mutate(activeNoteId)}
            >
              <Trash2 size={15} />
            </button>
            <button
              type="button"
              className="overlay-close"
              title="Durchgehende Trennlinie einfügen"
              aria-label="Durchgehende Trennlinie einfügen"
              disabled={!activeNoteId}
              onClick={() => insertAtCaret(dividerSnippetForTextarea("solid", textareaRef.current))}
            >
              <SeparatorHorizontal size={15} />
            </button>
            <button
              type="button"
              className="overlay-close"
              title="Gestrichelte Trennlinie einfügen"
              aria-label="Gestrichelte Trennlinie einfügen"
              disabled={!activeNoteId}
              onClick={() => insertAtCaret(dividerSnippetForTextarea("dashed", textareaRef.current))}
            >
              <Minus size={15} />
            </button>
          </div>
        </>
      ) : null}

      <input
        className="overlay-notes-title-input"
        value={title}
        placeholder="Titel"
        disabled={!activeNoteId}
        onChange={(event) => {
          setTitle(event.target.value);
          setDirtyState(true);
        }}
      />

      <div
        className={`overlay-notes-editor ${editorMode === "split" ? "overlay-notes-editor--split" : ""}`}
        ref={splitContainerRef}
      >
        {!activeNoteId ? (
          <p className="overlay-muted">{notesQuery.isLoading ? "Lade Notizen…" : "Keine Notiz gewählt."}</p>
        ) : (
          <>
            {editorMode === "edit" || editorMode === "split" ? (
              <div
                className="overlay-notes-editor-wrap"
                style={editorMode === "split" ? { flex: `${splitRatio} 1 0%`, minWidth: 0 } : undefined}
              >
                <textarea
                  ref={textareaRef}
                  className="overlay-notes-textarea"
                  value={markdown}
                  placeholder="Schreib los…"
                  onChange={(event) => {
                    setMarkdown(event.target.value);
                    setDirtyState(true);
                  }}
                  onSelect={captureSelection}
                  onMouseUp={captureSelection}
                  onKeyUp={captureSelection}
                  onKeyDown={handleOverlayEditorKeyDown}
                  onPaste={handlePaste}
                  onDragOver={handleDragOver}
                  onDrop={handleDrop}
                  onScroll={(event) => {
                    setEditorScrollTop(event.currentTarget.scrollTop);
                    setEditorScrollLeft(event.currentTarget.scrollLeft);
                  }}
                />
              </div>
            ) : null}
            {editorMode === "split" ? (
              <div
                className="split-handle overlay-notes-split-handle"
                role="separator"
                aria-orientation="vertical"
                aria-label="Aufteilung Editor/Vorschau anpassen"
                onPointerDown={startSplitResize}
                onDoubleClick={() => {
                  setSplitRatio(0.5);
                  saveNumberUiState("overlay.splitRatio", 0.5);
                }}
              />
            ) : null}
            {editorMode === "preview" || editorMode === "split" ? (
              <div className="overlay-notes-preview" style={editorMode === "split" ? { flex: `${1 - splitRatio} 1 0%`, minWidth: 0 } : undefined}>
                <MarkdownPreview
                  previewRef={previewRef}
                  markdown={markdown}
                  citations={currentNote?.citations ?? []}
                  onCitationClick={() => {}}
                  editable={false}
                  onScroll={(event) => {
                    previewScrollRef.current = { top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft };
                  }}
                />
              </div>
            ) : null}
          </>
        )}
      </div>

      {selection && activeNoteId ? (
        <div className="overlay-notes-ai-panel">
          <div className="overlay-notes-ai-selection">
            <Sparkles size={13} />
            <span>{shortText(selection.text, 140)}</span>
          </div>
          {!aiEdit.data ? (
            <div className="overlay-chat-input">
              <input
                value={aiInstruction}
                placeholder="Wie soll die Auswahl angepasst werden?"
                onChange={(event) => setAiInstruction(event.target.value)}
                onKeyDown={(event) => {
                  if (event.key === "Enter" && aiInstruction.trim() && !aiEdit.isPending) {
                    event.preventDefault();
                    aiEdit.mutate();
                  }
                }}
              />
              <button
                type="button"
                className="button button-primary button-compact"
                disabled={!aiInstruction.trim() || aiEdit.isPending}
                onClick={() => aiEdit.mutate()}
              >
                {aiEdit.isPending ? <Loader2 size={14} className="overlay-spin" /> : <Sparkles size={14} />}
                Anwenden
              </button>
            </div>
          ) : (
            <>
              <div className="overlay-notes-ai-preview">{aiEdit.data.replacement_text}</div>
              <div className="button-row">
                <button type="button" className="button button-primary button-compact" onClick={applyAiEdit}>
                  <Check size={13} /> Ersetzen
                </button>
                <button type="button" className="button button-compact" onClick={discardAiEdit}>
                  <X size={13} /> Verwerfen
                </button>
              </div>
            </>
          )}
          {aiEdit.isError ? <div className="inline-error">{formatError(aiEdit.error)}</div> : null}
        </div>
      ) : null}

      <div className="overlay-notes-footer">
        <div className="overlay-notes-mode-toggle">
          <button
            type="button"
            className={editorMode === "edit" ? "active" : ""}
            title="Bearbeiten"
            aria-label="Bearbeiten"
            aria-pressed={editorMode === "edit"}
            disabled={!activeNoteId}
            onClick={() => setEditorMode("edit")}
          >
            <Pencil size={14} />
          </button>
          <button
            type="button"
            className={editorMode === "preview" ? "active" : ""}
            title="Vorschau"
            aria-label="Vorschau"
            aria-pressed={editorMode === "preview"}
            disabled={!activeNoteId}
            onClick={() => setEditorMode("preview")}
          >
            <Eye size={14} />
          </button>
          <button
            type="button"
            className={editorMode === "split" ? "active" : ""}
            title="Geteilte Ansicht"
            aria-label="Geteilte Ansicht"
            aria-pressed={editorMode === "split"}
            disabled={!activeNoteId}
            onClick={() => setEditorMode("split")}
          >
            <Columns2 size={14} />
          </button>
        </div>
        <input
          ref={imageInputRef}
          type="file"
          accept="image/*"
          style={{ display: "none" }}
          onChange={(event) => {
            const file = event.target.files?.[0];
            if (file) uploadAsset.mutate(file);
            event.target.value = "";
          }}
        />
        <button
          type="button"
          className="overlay-close"
          title="Bild einfügen"
          aria-label="Bild einfügen"
          disabled={!activeNoteId}
          onClick={() => imageInputRef.current?.click()}
        >
          <ImagePlus size={15} />
        </button>
        <span className="overlay-notes-status">{saveStatus}</span>
      </div>

      {isTauri() ? (
        <div
          className="overlay-notes-resize-handle"
          role="separator"
          aria-label="Fenstergröße anpassen"
          onPointerDown={startNotesResize}
        />
      ) : null}
    </div>
  );
}
