import { KeyboardEvent, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import type { CSSProperties, MutableRefObject, RefObject } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Bold,
  Code,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
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
  SpellCheck2,
  Sparkles,
  Table2,
  Trash2,
  Undo2,
  X
} from "lucide-react";

import { api, API_BASE_URL } from "../api";
import { evidenceColorVars } from "../citationColors";
import { EmptyState } from "../components/EmptyState";
import { PdfPane } from "../components/PdfPane";
import { TextareaHighlightLayer } from "../components/TextareaHighlightLayer";
import { downloadMarkdownFile } from "../download";
import { noteProjectId, projectScopeLabel } from "../projectScope";
import { useAppState } from "../state";
import type { Note, NoteAiMessage, NoteAiThread, NoteCitation, VerificationEvidence } from "../types";

type SelectionRange = {
  start: number;
  end: number;
  text: string;
};

type EditorMode = "edit" | "preview" | "split";

type NoteAiThreadsResult = {
  items: NoteAiThread[];
  total: number;
};

type CitationMarkdownRef = {
  id: string;
  label: string;
  badge: string;
  title: string;
  start: number;
  end: number;
};

export type ThreadAnchorMeta = {
  label: string;
  colorIndex: number;
};

export type NoteCitationRow = {
  citation: NoteCitation;
  badge: string;
  label: string;
  title: string;
  evidence: string;
};

export type NotesSurfaceSnapshot = {
  activeNoteId: string;
  title: string;
  notes: Array<{ id: string; title: string; excerpt?: string; citation_count?: number; updated_timestamp?: string }>;
  notesLoading: boolean;
  currentNote: Note | null;
  citations: NoteCitation[];
  citationRows: NoteCitationRow[];
  selectedCitation: NoteCitation | null;
  threads: NoteAiThread[];
  activeThreadId: string;
  threadMeta: Map<string, ThreadAnchorMeta>;
  followUpDrafts: Record<string, string>;
  isFollowUpPending: boolean;
  deletingThreadId: string;
};

export type NotesSurfaceActions = {
  createNote: () => void;
  selectNote: (noteId: string) => void;
  openCitation: (citation: NoteCitation) => void;
  clearCitation: () => void;
  activateThread: (threadId: string) => void;
  setActiveThread: (threadId: string) => void;
  toggleThreadCollapsed: (threadId: string) => void;
  setFollowUpDraft: (threadId: string, value: string) => void;
  submitFollowUp: (threadId: string) => void;
  insertThreadAnswer: (threadId: string) => void;
  previewThreadAnswer: (threadId: string) => void;
  insertThreadMessage: (threadId: string, messageId: string) => void;
  previewThreadMessage: (threadId: string, messageId: string) => void;
  hideThreadMessage: (threadId: string, messageId: string) => void;
  previewAppendMarkdown: (markdown: string) => void;
  insertMarkdownAtCursor: (markdown: string, citations?: Record<string, unknown>[]) => Promise<string | null>;
  clearInsertPreview: () => void;
  deleteThread: (threadId: string) => void;
  deleteAllThreads: () => void;
};

type NotesSurfaceProps = {
  variant?: "page" | "workspace";
  controlledNoteId?: string;
  requestedCitationId?: string;
  onActiveNoteChange?: (noteId: string) => void;
  onCitationOpen?: (citation: NoteCitation | null) => void;
  onStateChange?: (snapshot: NotesSurfaceSnapshot) => void;
  actionsRef?: MutableRefObject<NotesSurfaceActions | null>;
};

export function NotesPage() {
  return <NotesSurface />;
}

export function NotesSurface({
  variant = "page",
  controlledNoteId,
  requestedCitationId,
  onActiveNoteChange,
  onCitationOpen,
  onStateChange,
  actionsRef
}: NotesSurfaceProps = {}) {
  const { activeProject, provider, model } = useAppState();
  const embedded = variant === "workspace";
  const scopedProjectId = noteProjectId(activeProject);
  const scopeLabel = projectScopeLabel(activeProject);
  const queryClient = useQueryClient();
  const [activeNoteId, setActiveNoteId] = useState<string>("");
  const [title, setTitle] = useState("");
  const [markdown, setMarkdown] = useState("");
  const [dirty, setDirty] = useState(false);
  const [editorMode, setEditorMode] = useState<EditorMode>("edit");
  const [selection, setSelection] = useState<SelectionRange | null>(null);
  const [selectionPinned, setSelectionPinned] = useState(false);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiPreview, setAiPreview] = useState("");
  const [aiPopoverBottomPadding, setAiPopoverBottomPadding] = useState(0);
  const [undoStack, setUndoStack] = useState<string[]>([]);
  const [selectedCitation, setSelectedCitation] = useState<NoteCitation | null>(null);
  const [activeEditorCitationId, setActiveEditorCitationId] = useState("");
  const [inlineThreadId, setInlineThreadId] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [notesListOpen, setNotesListOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.notesListOpen`, true));
  const [contextOpen, setContextOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.contextOpen`, true));
  const [notePdfOpen, setNotePdfOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.notePdfOpen`, true));
  const [citationListOpen, setCitationListOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.citationListOpen`, true));
  const [spellcheckEnabled, setSpellcheckEnabled] = useState(() => loadBooleanUiState("spellcheckEnabled", true));
  const [contextWidth, setContextWidth] = useState(() => loadNumberUiState(`${scopedProjectId}.contextWidth`, 430));
  const [activeThreadId, setActiveThreadId] = useState("");
  const [threadMetaById, setThreadMetaById] = useState<Record<string, ThreadAnchorMeta>>({});
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const [editorScrollLeft, setEditorScrollLeft] = useState(0);
  const [insertPreview, setInsertPreview] = useState<{ index: number; content: string } | null>(null);
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const editorWrapRef = useRef<HTMLDivElement | null>(null);
  const selectionPopoverRef = useRef<HTMLDivElement | null>(null);
  const citationPanelRef = useRef<HTMLElement | null>(null);
  const citationRowRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const loadedNoteIdRef = useRef("");
  const previewRef = useRef<HTMLElement | null>(null);
  const latestDraftRef = useRef({ noteId: "", title: "", markdown: "" });
  const markdownRef = useRef(markdown);
  const contextResizeFrameRef = useRef<number | null>(null);
  const dirtyRef = useRef(false);
  const lastCursorRef = useRef<number | null>(null);
  const threadsQueryKey = ["note-ai-threads", activeNoteId] as const;

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
    queryKey: threadsQueryKey,
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
      setSelectionPinned(false);
      setInlineThreadId("");
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
      const currentMarkdown = markdownRef.current;
      const anchor = threadAnchorRange(payload.thread, currentMarkdown);
      if (anchor) {
        setSelection({
          start: anchor.start,
          end: anchor.end,
          text: currentMarkdown.slice(anchor.start, anchor.end)
        });
      } else {
        setSelection(null);
      }
      setSelectionPinned(Boolean(anchor));
      setAiInstruction("");
      setAiPreview(payload.replacement_text || latestThreadAnswer(payload.thread));
      setActiveThreadId(payload.thread.id);
      setInlineThreadId("");
      setThreadMetaById((current) => assignMissingThreadMeta(current, [payload.thread.id]));
      queryClient.setQueryData<NoteAiThreadsResult>(threadsQueryKey, (current) => {
        if (!current) {
          return { items: [payload.thread], total: 1 };
        }
        const nextItems = [payload.thread, ...current.items.filter((thread) => thread.id !== payload.thread.id)];
        return { ...current, items: nextItems, total: nextItems.length };
      });
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
    }
  });
  const followUp = useMutation({
    mutationFn: ({ threadId, message }: { threadId: string; message: string; source?: "inline" | "history" }) =>
      api.appendNoteAiMessage(activeNoteId, threadId, {
        message,
        provider,
        model,
        use_kg_evidence: true
      }),
    onSuccess: (payload, variables) => {
      setActiveThreadId(payload.thread.id);
      if (variables.source === "inline") {
        setInlineThreadId(payload.thread.id);
        setAiPreview(payload.replacement_text);
      } else {
        setInlineThreadId("");
        setSelection(null);
        setSelectionPinned(false);
        setAiPreview("");
      }
      setFollowUpDrafts((current) => ({ ...current, [variables.threadId]: "" }));
      queryClient.setQueryData<NoteAiThreadsResult>(threadsQueryKey, (current) => {
        if (!current) {
          return { items: [payload.thread], total: 1 };
        }
        const nextItems = current.items.map((thread) => (thread.id === payload.thread.id ? payload.thread : thread));
        return { ...current, items: nextItems, total: nextItems.length };
      });
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
    }
  });
  const updateThreadUi = useMutation({
    mutationFn: ({ thread, uiState }: { thread: NoteAiThread; uiState: Record<string, unknown> }) =>
      api.updateNoteAiThread(activeNoteId, thread.id, { ui_state: { ...(thread.ui_state ?? {}), ...uiState } }),
    onSuccess: ({ thread }) => {
      queryClient.setQueryData<NoteAiThreadsResult>(threadsQueryKey, (current) => {
        if (!current) {
          return current;
        }
        const nextItems = current.items.map((item) => (item.id === thread.id ? thread : item));
        return { ...current, items: nextItems };
      });
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
    }
  });
  const deleteThread = useMutation({
    mutationFn: (threadId: string) => api.deleteNoteAiThread(activeNoteId, threadId),
    onMutate: async (threadId) => {
      await queryClient.cancelQueries({ queryKey: threadsQueryKey });
      const previous = queryClient.getQueryData<NoteAiThreadsResult>(threadsQueryKey);
      queryClient.setQueryData<NoteAiThreadsResult>(threadsQueryKey, (current) => {
        if (!current) {
          return current;
        }
        const nextItems = current.items.filter((thread) => thread.id !== threadId);
        return { ...current, items: nextItems, total: nextItems.length };
      });
      setActiveThreadId((current) => (current === threadId ? "" : current));
      setInlineThreadId((current) => (current === threadId ? "" : current));
      setFollowUpDrafts((current) => {
        const next = { ...current };
        delete next[threadId];
        return next;
      });
      return { previous };
    },
    onError: (_error, _threadId, context) => {
      if (context?.previous) {
        queryClient.setQueryData(threadsQueryKey, context.previous);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
    }
  });
  const deleteAllThreads = useMutation({
    mutationFn: () => api.deleteNoteAiThreads(activeNoteId),
    onMutate: async () => {
      await queryClient.cancelQueries({ queryKey: threadsQueryKey });
      const previous = queryClient.getQueryData<NoteAiThreadsResult>(threadsQueryKey);
      queryClient.setQueryData<NoteAiThreadsResult>(threadsQueryKey, (current) => current ? { ...current, items: [], total: 0 } : current);
      setActiveThreadId("");
      setInlineThreadId("");
      setFollowUpDrafts({});
      setAiPreview("");
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(threadsQueryKey, context.previous);
      }
    },
    onSettled: () => {
      queryClient.invalidateQueries({ queryKey: threadsQueryKey });
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
  const threads = threadsQuery.data?.items ?? [];
  const citationRefs = useMemo(() => parseMarkdownCitationRefs(markdown), [markdown]);
  const citationLookup = useMemo(() => new Map(citations.map((citation) => [citation.id, citation])), [citations]);
  const citationRows = useMemo(
    () =>
      citations.map((citation, index) => {
        const ref = citationRefs.find((item) => item.id === citation.id);
        const fallbackBadge = `Z${Number(citation.evidence_index ?? index) + 1}`;
        const badge = ref?.badge || fallbackBadge;
        const label = ref?.label || badge;
        const title = ref?.title || citation.title || citation.paper_id;
        const evidence = citation.pdf_excerpt || citation.reference_text || citation.kind || "";
        return { citation, badge, label, title: shortText(title, 96), evidence: shortText(evidence, 150) };
      }),
    [citationRefs, citations]
  );
  const activeEditorCitation = activeEditorCitationId ? citationLookup.get(activeEditorCitationId) ?? null : null;
  const activeEditorCitationRow = activeEditorCitation
    ? citationRows.find((row) => row.citation.id === activeEditorCitation.id) ?? null
    : null;
  const activeCitationRefs = selectedCitation ? citationRefs.filter((item) => item.id === selectedCitation.id) : [];
  const threadAnchors = useMemo(
    () =>
      threads
        .map((thread, index) => {
          const range = threadAnchorRange(thread, markdown);
          return range ? { thread, index, ...range } : null;
        })
        .filter((anchor): anchor is { thread: NoteAiThread; index: number; start: number; end: number } => Boolean(anchor)),
    [markdown, threads]
  );
  const threadAnchorMeta = useMemo(
    () =>
      new Map<string, ThreadAnchorMeta>(
        threadAnchors.map((anchor, index) => [
          anchor.thread.id,
          threadMetaById[anchor.thread.id] ?? {
            label: `N${index + 1}`,
            colorIndex: index
          }
        ])
      ),
    [threadAnchors, threadMetaById]
  );

  useEffect(() => {
    if (!activeNoteId || !threadAnchors.length) {
      return;
    }
    setThreadMetaById((current) => assignMissingThreadMeta(current, threadAnchors.map((anchor) => anchor.thread.id)));
  }, [activeNoteId, threadAnchors]);

  useEffect(() => {
    markdownRef.current = markdown;
    latestDraftRef.current = { noteId: activeNoteId, title, markdown };
  }, [activeNoteId, markdown, title]);

  useEffect(() => {
    setActiveNoteId("");
    setSelectedCitation(null);
    setInlineThreadId("");
    loadedNoteIdRef.current = "";
  }, [scopedProjectId]);

  useEffect(() => {
    if (controlledNoteId && controlledNoteId !== activeNoteId) {
      setActiveNoteId(controlledNoteId);
    }
  }, [activeNoteId, controlledNoteId]);

  useEffect(() => {
    onActiveNoteChange?.(activeNoteId);
  }, [activeNoteId, onActiveNoteChange]);

  useEffect(() => {
    setNotesListOpen(loadBooleanUiState(`${scopedProjectId}.notesListOpen`, true));
    setContextOpen(loadBooleanUiState(`${scopedProjectId}.contextOpen`, true));
    setNotePdfOpen(loadBooleanUiState(`${scopedProjectId}.notePdfOpen`, true));
    setCitationListOpen(loadBooleanUiState(`${scopedProjectId}.citationListOpen`, true));
    setContextWidth(loadNumberUiState(`${scopedProjectId}.contextWidth`, 430));
  }, [scopedProjectId]);

  useEffect(() => {
    if (!activeNoteId) {
      setThreadMetaById({});
      return;
    }
    setThreadMetaById(loadThreadMetaUiState(`${scopedProjectId}.${activeNoteId}.threadMeta`, {}));
  }, [activeNoteId, scopedProjectId]);

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
    saveBooleanUiState(`${scopedProjectId}.citationListOpen`, citationListOpen);
  }, [citationListOpen, scopedProjectId]);

  useEffect(() => {
    saveNumberUiState(`${scopedProjectId}.contextWidth`, contextWidth);
  }, [contextWidth, scopedProjectId]);

  useEffect(() => {
    if (!activeNoteId) {
      return;
    }
    saveThreadMetaUiState(`${scopedProjectId}.${activeNoteId}.threadMeta`, threadMetaById);
  }, [activeNoteId, scopedProjectId, threadMetaById]);

  useEffect(() => {
    saveBooleanUiState("spellcheckEnabled", spellcheckEnabled);
  }, [spellcheckEnabled]);

  useEffect(() => {
    if (!selectedCitation || !citationListOpen || historyOpen) {
      return;
    }
    window.requestAnimationFrame(() => {
      const row = citationRowRefs.current[selectedCitation.id];
      const panel = citationPanelRef.current;
      if (!row || !panel) {
        return;
      }
      const rowRect = row.getBoundingClientRect();
      const panelRect = panel.getBoundingClientRect();
      if (rowRect.top < panelRect.top + 10 || rowRect.bottom > panelRect.bottom - 10) {
        panel.scrollTo({
          top: Math.max(0, panel.scrollTop + rowRect.top - panelRect.top - 12),
          behavior: "smooth"
        });
      }
    });
  }, [citationListOpen, historyOpen, selectedCitation]);

  useEffect(() => {
    if (!selection) {
      setAiPopoverBottomPadding(0);
      return;
    }
    const handlePointerDown = (event: PointerEvent) => {
      const target = event.target;
      if (target instanceof Node && editorWrapRef.current?.contains(target)) {
        return;
      }
      setSelection(null);
      setSelectionPinned(false);
      setAiPreview("");
      setAiInstruction("");
    };
    document.addEventListener("pointerdown", handlePointerDown);
    return () => document.removeEventListener("pointerdown", handlePointerDown);
  }, [selection]);

  useEffect(() => {
    if (!selection) {
      setAiPopoverBottomPadding(0);
      return;
    }
    const popover = selectionPopoverRef.current;
    const wrap = editorWrapRef.current;
    if (!popover || !wrap) {
      return;
    }
    let frame: number | null = null;
    const measure = () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
      }
      frame = window.requestAnimationFrame(() => {
        frame = null;
        const popoverRect = popover.getBoundingClientRect();
        const wrapRect = wrap.getBoundingClientRect();
        const bottomGap = Math.max(0, wrapRect.bottom - popoverRect.bottom);
        const popoverRoom = popoverRect.height + bottomGap + 24;
        setAiPopoverBottomPadding(Math.ceil(popoverRoom));
      });
    };
    measure();
    const ResizeObserverCtor = window.ResizeObserver;
    const observer = ResizeObserverCtor ? new ResizeObserverCtor(measure) : null;
    observer?.observe(popover);
    observer?.observe(wrap);
    window.addEventListener("resize", measure);
    return () => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
      }
      observer?.disconnect();
      window.removeEventListener("resize", measure);
    };
  }, [aiEdit.isPending, aiInstruction, aiPreview, selection]);

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
    setSelectionPinned(false);
    setActiveThreadId("");
    setInlineThreadId("");
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
    markdownRef.current = value;
    setMarkdown(value);
    setDirtyState(true);
    setInsertPreview(null);
    setActiveEditorCitationId("");
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
      if (node) {
        lastCursorRef.current = node.selectionStart;
        setActiveEditorCitationId(citationRefAtPosition(citationRefs, node.selectionStart)?.id ?? "");
      }
      return null;
    }
    lastCursorRef.current = node.selectionEnd;
    setActiveEditorCitationId("");
    const next = {
      start: node.selectionStart,
      end: node.selectionEnd,
      text: markdown.slice(node.selectionStart, node.selectionEnd)
    };
    setSelection(next);
    setSelectionPinned(false);
    setAiPreview("");
    setAiInstruction("");
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
      if (key === "z") {
        event.preventDefault();
        undo();
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

  function clearEditorSelection() {
    setSelection(null);
    setSelectionPinned(false);
    setAiInstruction("");
    setAiPreview("");
  }

  function handleEditorPointerDown() {
    if (aiPreview) {
      setInsertPreview(null);
      return;
    }
    setInlineThreadId("");
    clearEditorSelection();
  }

  function pinSelectionForQuestion() {
    if (!selection) {
      return;
    }
    setSelectionPinned(true);
  }

  function toggleInlineThread(threadId: string) {
    if (inlineThreadId === threadId) {
      setInlineThreadId("");
      return;
    }
    setInlineThreadId(threadId);
    setActiveThreadId(threadId);
    setSelection(null);
    setSelectionPinned(false);
    setAiPreview("");
    setAiInstruction("");
  }

  function activateThreadFromHistory(threadId: string) {
    setActiveThreadId(threadId);
    setInlineThreadId(threadId);
    setSelection(null);
    setSelectionPinned(false);
    setAiPreview("");
    setAiInstruction("");
    const anchor = threadAnchors.find((item) => item.thread.id === threadId);
    if (!anchor) {
      return;
    }
    requestAnimationFrame(() => {
      const node = textareaRef.current;
      if (!node) {
        return;
      }
      node.focus();
      node.setSelectionRange(anchor.start, anchor.start);
      node.scrollTop = estimatedTextareaScrollTop(markdown, anchor.start, node);
      setEditorScrollTop(node.scrollTop);
      setEditorScrollLeft(node.scrollLeft);
    });
  }

  function appendThreadAnswer(thread: NoteAiThread) {
    const text = latestThreadAnswer(thread);
    if (text) {
      insertThreadAnswer(thread, text);
    }
  }

  function submitFollowUp(thread: NoteAiThread, source: "inline" | "history" = "inline") {
    const message = (followUpDrafts[thread.id] ?? "").trim();
    if (!message) {
      return;
    }
    followUp.mutate({ threadId: thread.id, message, source });
  }

  function setThreadCollapsed(thread: NoteAiThread, collapsed: boolean) {
    updateThreadUi.mutate({ thread, uiState: { collapsed } });
  }

  function hideThreadMessage(thread: NoteAiThread, messageId: string) {
    updateThreadUi.mutate({
      thread,
      uiState: {
        hidden_message_ids: Array.from(new Set([...hiddenThreadMessageIds(thread), messageId]))
      }
    });
  }

  function appendThreadMessage(thread: NoteAiThread, message: NoteAiMessage) {
    insertThreadAnswer(thread, message.content);
  }

  function previewThreadMessage(thread: NoteAiThread, message: NoteAiMessage) {
    setInsertPreview({ index: insertionPointForThread(thread), content: threadInsertionContent(message.content) });
  }

  const workspaceColumns = `${notesListOpen ? "minmax(230px, 0.32fr)" : "46px"} minmax(360px, 1fr) 6px ${contextOpen ? `minmax(320px, ${contextWidth}px)` : "46px"}`;

  function startContextResize(event: ReactPointerEvent<HTMLDivElement>) {
    if (!contextOpen) {
      return;
    }
    event.preventDefault();
    const startX = event.clientX;
    const startWidth = contextWidth;
    const move = (moveEvent: globalThis.PointerEvent) => {
      const delta = startX - moveEvent.clientX;
      const maxWidth = Math.max(360, Math.round(window.innerWidth * 0.68));
      const nextWidth = Math.min(maxWidth, Math.max(320, startWidth + delta));
      if (contextResizeFrameRef.current !== null) {
        window.cancelAnimationFrame(contextResizeFrameRef.current);
      }
      contextResizeFrameRef.current = window.requestAnimationFrame(() => {
        contextResizeFrameRef.current = null;
        setContextWidth(nextWidth);
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

  function insertAtSelection(value: string) {
    const node = textareaRef.current;
    const fallback = lastCursorRef.current ?? markdown.length;
    const start = node?.selectionStart ?? fallback;
    const end = node?.selectionEnd ?? fallback;
    insertAtRange(start, end, value);
  }

  function insertAtRange(start: number, end: number, value: string) {
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${value}${markdown.slice(end)}`);
    lastCursorRef.current = start + value.length;
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(start + value.length, start + value.length);
    });
  }

  function insertThreadAnswer(thread: NoteAiThread, answer: string) {
    const content = threadInsertionContent(answer);
    const index = insertionPointForThread(thread);
    insertAtRange(index, index, content);
  }

  function previewThreadAnswer(thread: NoteAiThread) {
    const answer = latestThreadAnswer(thread);
    if (!answer) {
      setInsertPreview(null);
      return;
    }
    setInsertPreview({ index: insertionPointForThread(thread), content: threadInsertionContent(answer) });
  }

  function clearInsertPreview() {
    setInsertPreview(null);
  }

  function previewAppendMarkdown(value: string) {
    const content = value.trim();
    if (!content) {
      setInsertPreview(null);
      return;
    }
    const { start } = externalInsertRange();
    setInsertPreview({ index: start, content: markdownBlockInsertion(markdown, start, content) });
  }

  async function insertMarkdownAtCursor(value: string, citations: Record<string, unknown>[] = []) {
    const content = value.trim();
    if (!content) {
      return activeNoteId || null;
    }
    const currentMarkdown = markdownRef.current;
    const { start, end } = externalInsertRange();
    const insertText = markdownBlockInsertion(currentMarkdown, start, content);
    const nextMarkdown = `${currentMarkdown.slice(0, start)}${insertText}${currentMarkdown.slice(end)}`;
    const nextCursor = start + insertText.length;
    pushUndo();
    markdownRef.current = nextMarkdown;
    setMarkdown(nextMarkdown);
    setInsertPreview(null);
    setActiveEditorCitationId("");
    setDirtyState(true);
    lastCursorRef.current = nextCursor;
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(nextCursor, nextCursor);
    });

    const nextTitle = noteTitleForSave(title, nextMarkdown);
    if (!activeNoteId) {
      const created = await api.createNote(scopedProjectId, { title: nextTitle, markdown: nextMarkdown });
      const note = citations.length ? (await api.appendNote(created.note.id, { markdown: " ", citations })).note : created.note;
      setActiveNoteId(note.id);
      setTitle(note.title);
      setMarkdown(note.markdown);
      markdownRef.current = note.markdown;
      setDirtyState(false);
      loadedNoteIdRef.current = note.id;
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
      queryClient.invalidateQueries({ queryKey: ["note", note.id] });
      return note.id;
    }

    if (nextTitle !== title) {
      setTitle(nextTitle);
    }
    const saved = await api.updateNote(activeNoteId, { title: nextTitle, markdown: nextMarkdown });
    const note = citations.length ? (await api.appendNote(activeNoteId, { markdown: " ", citations })).note : saved.note;
    setTitle(note.title);
    setMarkdown(note.markdown);
    markdownRef.current = note.markdown;
    setDirtyState(false);
    loadedNoteIdRef.current = note.id;
    queryClient.setQueryData(["note", note.id], { note });
    queryClient.invalidateQueries({ queryKey: ["notes"] });
    queryClient.invalidateQueries({ queryKey: ["note", note.id] });
    return note.id;
  }

  function threadInsertionContent(answer: string) {
    return `\n\n${answer.trim()}`;
  }

  function externalInsertRange() {
    const currentMarkdown = markdownRef.current;
    const node = textareaRef.current;
    const fallback = Math.max(0, Math.min(currentMarkdown.length, lastCursorRef.current ?? currentMarkdown.length));
    const start = Math.max(0, Math.min(currentMarkdown.length, node?.selectionStart ?? fallback));
    const end = Math.max(start, Math.min(currentMarkdown.length, node?.selectionEnd ?? start));
    return { start, end };
  }

  function markdownBlockInsertion(source: string, index: number, content: string) {
    const before = source.slice(0, index);
    const after = source.slice(index);
    const prefix = before.trim() ? (before.endsWith("\n\n") ? "" : before.endsWith("\n") ? "\n" : "\n\n") : "";
    const suffix = after.trim() ? (after.startsWith("\n\n") ? "" : after.startsWith("\n") ? "\n" : "\n\n") : "";
    return `${prefix}${content.trim()}${suffix}`;
  }

  function insertionPointForThread(thread: NoteAiThread) {
    if (lastCursorRef.current !== null) {
      return Math.max(0, Math.min(markdown.length, lastCursorRef.current));
    }
    const quote = stripHighlightMarkers(thread.anchor_quote || thread.selected_text || "").trim();
    if (quote) {
      const quoteIndex = markdown.indexOf(quote);
      if (quoteIndex >= 0) {
        const end = quoteIndex + quote.length;
        return markdown.slice(end, end + 2) === "==" ? end + 2 : end;
      }
    }
    const anchorEnd = thread.anchor_end === null || thread.anchor_end === undefined ? NaN : Number(thread.anchor_end);
    if (Number.isFinite(anchorEnd) && anchorEnd >= 0 && anchorEnd <= markdown.length) {
      return anchorEnd;
    }
    const node = textareaRef.current;
    const cursor = node ? node.selectionStart : lastCursorRef.current;
    return Math.max(0, Math.min(markdown.length, cursor ?? markdown.length));
  }

  function insertAfterSelection(value: string) {
    const insertText = `\n\n${value.trim()}`;
    if (!selection) {
      insertAtSelection(insertText);
      return;
    }
    const start = selection.end;
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${insertText}${markdown.slice(start)}`);
    lastCursorRef.current = start + insertText.length;
    setSelection({
      start,
      end: start + insertText.length,
      text: insertText
    });
    setSelectionPinned(false);
    setAiPreview("");
    setAiInstruction("");
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(start + insertText.length, start + insertText.length);
    });
  }

  function previewAfterSelection(value: string) {
    const content = `\n\n${value.trim()}`;
    const index = selection ? selection.end : Math.max(0, Math.min(markdown.length, lastCursorRef.current ?? markdown.length));
    setInsertPreview({ index, content });
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

  function switchEditorMode(mode: EditorMode) {
    if (editorMode === "preview" && mode !== "preview") {
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
        const nextRaw = previewElementToMarkdown(block.raw, node);
        if (nextRaw === block.raw) {
          return null;
        }
        return { blockIndex, nextRaw };
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

  function updatePreviewBlock(blockIndex: number, nextRaw: string, expectedRaw?: string) {
    const currentMarkdown = markdownRef.current;
    const blocks = splitMarkdownBlocks(currentMarkdown);
    const block = blocks[blockIndex];
    if (!block) {
      return;
    }
    if (expectedRaw !== undefined && block.raw !== expectedRaw) {
      return;
    }
    const nextMarkdown = `${currentMarkdown.slice(0, block.start)}${nextRaw}${currentMarkdown.slice(block.end)}`;
    if (nextMarkdown !== currentMarkdown) {
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
    setCitationListOpen(true);
    setNotePdfOpen(true);
    onCitationOpen?.(citation);
  }

  function clearCitation() {
    setSelectedCitation(null);
    onCitationOpen?.(null);
  }

  useEffect(() => {
    if (!requestedCitationId || selectedCitation?.id === requestedCitationId) {
      return;
    }
    const citation = citations.find((item) => item.id === requestedCitationId);
    if (citation) {
      openCitation(citation);
    }
  }, [citations, requestedCitationId, selectedCitation?.id]);

  function clearSelectionAi() {
    clearEditorSelection();
  }

  function handleSelectionQuestionKeyDown(event: KeyboardEvent<HTMLInputElement>) {
    if (event.key === "Enter") {
      event.preventDefault();
      if (aiInstruction.trim() && !aiEdit.isPending) {
        pinSelectionForQuestion();
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
  const editorHighlightRanges = [
    ...(selection
      ? [
          {
            start: selection.start,
            end: selection.end,
            className: selectionPinned ? "textarea-highlight-range--selection textarea-highlight-range--selection-pinned" : "textarea-highlight-range--selection"
          }
        ]
      : []),
    ...threadAnchors
      .filter((anchor) => anchor.thread.id === inlineThreadId)
      .map((anchor) => ({
        start: anchor.start,
        end: anchor.end,
        className: "textarea-highlight-range--thread-anchor textarea-highlight-range--thread-anchor-active",
        style: evidenceColorVars(threadAnchorMeta.get(anchor.thread.id)?.colorIndex ?? 0)
      })),
    ...activeCitationRefs.map((ref) => ({
      start: ref.start,
      end: ref.end,
      className: "textarea-highlight-range--citation-active"
    }))
  ];
  const threadAnchorInsertions = threadAnchors.map((anchor) => {
    const meta = threadAnchorMeta.get(anchor.thread.id) ?? { label: "N?", colorIndex: 0 };
    return {
      index: anchor.end,
      className: "textarea-thread-anchor-insertion",
      content: (
        <ThreadAnchorMarker
          key={anchor.thread.id}
          label={meta.label}
          thread={anchor.thread}
          placement={threadAnchorPlacement(markdown, anchor.end)}
          open={inlineThreadId === anchor.thread.id}
          active={inlineThreadId === anchor.thread.id}
          style={evidenceColorVars(meta.colorIndex)}
          followUpDraft={followUpDrafts[anchor.thread.id] ?? ""}
          isSubmitting={followUp.isPending && followUp.variables?.threadId === anchor.thread.id}
          deleting={deleteThread.isPending && deleteThread.variables === anchor.thread.id}
          onToggle={() => toggleInlineThread(anchor.thread.id)}
          onClose={() => setInlineThreadId("")}
          onDraftChange={(value) => setFollowUpDrafts((current) => ({ ...current, [anchor.thread.id]: value }))}
          onFollowUp={() => submitFollowUp(anchor.thread)}
          onDelete={() => deleteThread.mutate(anchor.thread.id)}
        />
      )
    };
  });
  const editorGhostInsertions = [
    ...(insertPreview ? [{ ...insertPreview, className: "textarea-ghost-insertion--ai" }] : []),
    ...threadAnchorInsertions
  ];
  const sourcePanelRows = citationListOpen ? undefined : "auto minmax(0, 1fr)";
  const editorBottomStyle = {
    "--note-editor-extra-bottom": selection ? `max(68vh, ${Math.max(aiPopoverBottomPadding, 96)}px)` : "68vh"
  } as CSSProperties;
  const showEditor = editorMode === "edit" || editorMode === "split";
  const showPreview = editorMode === "preview" || editorMode === "split";
  const pageClassName = embedded ? "page notes-page notes-page--embedded" : "page notes-page";

  useEffect(() => {
    if (!actionsRef) {
      return;
    }
    const findThread = (threadId: string) => threads.find((thread) => thread.id === threadId);
    actionsRef.current = {
      createNote: () => createNote.mutate(),
      selectNote: setActiveNoteId,
      openCitation,
      clearCitation,
      activateThread: activateThreadFromHistory,
      setActiveThread: setActiveThreadId,
      toggleThreadCollapsed: (threadId: string) => {
        const thread = findThread(threadId);
        if (thread) {
          setThreadCollapsed(thread, !threadCollapsed(thread));
        }
      },
      setFollowUpDraft: (threadId: string, value: string) => setFollowUpDrafts((current) => ({ ...current, [threadId]: value })),
      submitFollowUp: (threadId: string) => {
        const thread = findThread(threadId);
        if (thread) {
          submitFollowUp(thread, "history");
        }
      },
      insertThreadAnswer: (threadId: string) => {
        const thread = findThread(threadId);
        if (thread) {
          appendThreadAnswer(thread);
        }
      },
      previewThreadAnswer: (threadId: string) => {
        const thread = findThread(threadId);
        if (thread) {
          previewThreadAnswer(thread);
        }
      },
      insertThreadMessage: (threadId: string, messageId: string) => {
        const thread = findThread(threadId);
        const message = thread ? threadDisplayMessages(thread).find((item) => item.id === messageId) : null;
        if (thread && message) {
          appendThreadMessage(thread, message);
        }
      },
      previewThreadMessage: (threadId: string, messageId: string) => {
        const thread = findThread(threadId);
        const message = thread ? threadDisplayMessages(thread).find((item) => item.id === messageId) : null;
        if (thread && message) {
          previewThreadMessage(thread, message);
        }
      },
      hideThreadMessage: (threadId: string, messageId: string) => {
        const thread = findThread(threadId);
        if (thread) {
          hideThreadMessage(thread, messageId);
        }
      },
      previewAppendMarkdown,
      insertMarkdownAtCursor,
      clearInsertPreview,
      deleteThread: (threadId: string) => deleteThread.mutate(threadId),
      deleteAllThreads: () => deleteAllThreads.mutate()
    };
    return () => {
      actionsRef.current = null;
    };
  });

  useEffect(() => {
    onStateChange?.({
      activeNoteId,
      title,
      notes,
      notesLoading: notesQuery.isLoading,
      currentNote: currentNote ?? null,
      citations,
      citationRows,
      selectedCitation,
      threads,
      activeThreadId,
      threadMeta: threadAnchorMeta,
      followUpDrafts,
      isFollowUpPending: followUp.isPending,
      deletingThreadId: deleteThread.isPending ? deleteThread.variables ?? "" : ""
    });
  }, [
    activeNoteId,
    title,
    notes,
    notesQuery.isLoading,
    currentNote,
    citations,
    citationRows,
    selectedCitation,
    threads,
    activeThreadId,
    threadAnchorMeta,
    followUpDrafts,
    followUp.isPending,
    deleteThread.isPending,
    deleteThread.variables,
    onStateChange
  ]);

  return (
    <section className={pageClassName}>
      <div className="page-title">
        <div>
          <span>{scopeLabel}</span>
          <h1>Notizen</h1>
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
                <div className="button-row">
                  <button className="button button-compact" type="button" onClick={() => createNote.mutate()} disabled={createNote.isPending} aria-label="Neu">
                    <FilePlus2 size={16} />
                    <span>Neu</span>
                  </button>
                  <button className="icon-button" type="button" aria-label="Projektnotizen einklappen" onClick={() => setNotesListOpen(false)}>
                    <ChevronLeft size={17} />
                  </button>
                </div>
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
                  <button className="button button-compact" type="button" disabled={!activeNoteId || !markdown.trim()} onClick={exportCurrentNote} aria-label="Export">
                    <Download size={16} />
                    <span>Export</span>
                  </button>
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
                <button
                  className={`icon-button ${spellcheckEnabled ? "active" : ""}`}
                  type="button"
                  aria-label={spellcheckEnabled ? "Rechtschreibkontrolle ausschalten" : "Rechtschreibkontrolle einschalten"}
                  aria-pressed={spellcheckEnabled}
                  onClick={() => setSpellcheckEnabled((current) => !current)}
                >
                  <SpellCheck2 size={17} />
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
                  <button type="button" className={editorMode === "split" ? "active" : ""} onClick={() => switchEditorMode("split")}>
                    Split
                  </button>
                </div>
              </div>

              <div className={`markdown-editor-grid markdown-editor-grid--${editorMode}`}>
                {showEditor ? (
                  <div
                    className={`markdown-editor-wrap markdown-editor-wrap--highlighted ${selection ? "markdown-editor-wrap--selection-active" : ""}`}
                    data-insert-preview={insertPreview ? "true" : undefined}
                    ref={editorWrapRef}
                    style={editorBottomStyle}
                  >
                    <TextareaHighlightLayer
                      text={markdown}
                      ranges={editorHighlightRanges}
                      insertions={editorGhostInsertions}
                      scrollTop={editorScrollTop}
                      scrollLeft={editorScrollLeft}
                      interactive={threadAnchorInsertions.length > 0}
                    />
                    <textarea
                      ref={textareaRef}
                      className="markdown-editor markdown-editor--highlighted"
                      value={markdown}
                      onChange={(event) => updateMarkdown(event.target.value)}
                      onSelect={captureSelection}
                      onPointerDown={handleEditorPointerDown}
                      onScroll={(event) => {
                        setEditorScrollTop(event.currentTarget.scrollTop);
                        setEditorScrollLeft(event.currentTarget.scrollLeft);
                      }}
                      onKeyDown={handleEditorKeyDown}
                      spellCheck={spellcheckEnabled}
                      placeholder="Markdown schreiben"
                    />
                    {activeEditorCitation && activeEditorCitationRow && !selection ? (
                      <button
                        className="editor-citation-chip"
                        type="button"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => openCitation(activeEditorCitation)}
                      >
                        <Quote size={15} />
                        <span>{activeEditorCitationRow.badge} öffnen</span>
                      </button>
                    ) : null}
                    {selection ? (
                      <div className="selection-ai-popover" ref={selectionPopoverRef} onPointerDown={pinSelectionForQuestion}>
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
                              onFocus={pinSelectionForQuestion}
                              onKeyDown={handleSelectionQuestionKeyDown}
                              placeholder="KI-Frage zu dieser Auswahl"
                            />
                            <button
                              className="button button-primary"
                              type="button"
                              disabled={!aiInstruction.trim() || aiEdit.isPending}
                              onClick={() => {
                                pinSelectionForQuestion();
                                aiEdit.mutate();
                              }}
                            >
                              Fragen
                            </button>
                          </div>
                        ) : null}
                        {aiEdit.isError ? <div className="inline-error">KI-Antwort fehlgeschlagen: {formatError(aiEdit.error)}</div> : null}
                        {aiPreview ? (
                          <div className="ai-preview-card">
                            <span>Antwort</span>
                            <textarea className="ai-preview-output" value={aiPreview} readOnly aria-label="KI-Antwort" />
                            <div className="button-row">
                              <button className="button button-primary" type="button" onClick={() => replaceSelection(aiPreview)}>
                                Ersetzen
                              </button>
                              <button
                                className="button"
                                type="button"
                                onClick={() => insertAfterSelection(aiPreview)}
                                onMouseEnter={() => previewAfterSelection(aiPreview)}
                                onMouseLeave={clearInsertPreview}
                                onPointerEnter={() => previewAfterSelection(aiPreview)}
                                onPointerLeave={clearInsertPreview}
                                onFocus={() => previewAfterSelection(aiPreview)}
                                onBlur={clearInsertPreview}
                              >
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
                ) : null}
                {showPreview ? (
                  <MarkdownPreview
                    previewRef={editorMode === "preview" ? previewRef : undefined}
                    markdown={markdown}
                    citations={citations}
                    activeCitationId={selectedCitation?.id ?? ""}
                    onCitationClick={openCitation}
                    editable={editorMode === "preview"}
                    onBlockChange={editorMode === "preview" ? updatePreviewBlock : undefined}
                  />
                ) : null}
              </div>
            </>
          ) : (
            <EmptyState title={notesQuery.isLoading ? "Lade Notizen" : "Keine Notiz gewaehlt"} />
          )}
        </main>

        <div
          className={`split-handle notes-context-resize-handle ${contextOpen ? "" : "split-handle--idle"}`}
          role="separator"
          aria-label="Notizen und Quellen/PDF Breite anpassen"
          aria-orientation="vertical"
          onPointerDown={contextOpen ? startContextResize : undefined}
        />

        <aside className={`note-context-panel ${contextOpen ? "" : "note-context-panel--collapsed"}`}>
          {!contextOpen ? (
            <button className="collapsed-panel-tab" type="button" onClick={() => setContextOpen(true)}>
              <PanelRightOpen size={17} />
              <span>Quellen</span>
            </button>
          ) : historyOpen ? (
            <>
              <NoteContextToolbar
                historyOpen={historyOpen}
                citationCount={citations.length}
                threadCount={threadsQuery.data?.total ?? 0}
                selectedCitation={selectedCitation}
                citationListOpen={citationListOpen}
                notePdfOpen={notePdfOpen}
                threads={threads}
                deleteAllPending={deleteAllThreads.isPending}
                onShowSources={() => setHistoryOpen(false)}
                onShowHistory={() => setHistoryOpen(true)}
                onToggleCitationList={() => setCitationListOpen((current) => !current)}
                onTogglePdf={() => setNotePdfOpen((current) => !current)}
                onClearCitation={clearCitation}
                onDeleteAllThreads={() => deleteAllThreads.mutate()}
              />
              <section className="panel note-history-panel">
                {deleteThread.isError ? <div className="inline-error">KI-Verlauf konnte nicht geloescht werden: {formatError(deleteThread.error)}</div> : null}
                {deleteAllThreads.isError ? <div className="inline-error">KI-Verlaeufe konnten nicht geloescht werden: {formatError(deleteAllThreads.error)}</div> : null}
                {followUp.isError ? <div className="inline-error">Folgefrage fehlgeschlagen: {formatError(followUp.error)}</div> : null}
                <AiThreadList
                  threads={threads}
                  activeThreadId={activeThreadId}
                  threadMeta={threadAnchorMeta}
                  followUpDrafts={followUpDrafts}
                  isSubmitting={followUp.isPending}
                  onActiveThreadChange={setActiveThreadId}
                  onLocateThread={activateThreadFromHistory}
                  onDraftChange={(threadId, value) => setFollowUpDrafts((current) => ({ ...current, [threadId]: value }))}
                  onFollowUp={(thread) => submitFollowUp(thread, "history")}
                  onInsert={appendThreadAnswer}
                  onPreviewInsert={previewThreadAnswer}
                  onPreviewClear={clearInsertPreview}
                  onCollapseChange={setThreadCollapsed}
                  onInsertMessage={appendThreadMessage}
                  onPreviewMessage={previewThreadMessage}
                  onHideMessage={hideThreadMessage}
                  onDelete={(thread) => deleteThread.mutate(thread.id)}
                  deletingThreadId={deleteThread.isPending ? deleteThread.variables ?? "" : ""}
                />
              </section>
            </>
          ) : (
            <>
              <NoteContextToolbar
                historyOpen={historyOpen}
                citationCount={citations.length}
                threadCount={threadsQuery.data?.total ?? 0}
                selectedCitation={selectedCitation}
                citationListOpen={citationListOpen}
                notePdfOpen={notePdfOpen}
                threads={threads}
                deleteAllPending={deleteAllThreads.isPending}
                onShowSources={() => setHistoryOpen(false)}
                onShowHistory={() => setHistoryOpen(true)}
                onToggleCitationList={() => setCitationListOpen((current) => !current)}
                onTogglePdf={() => setNotePdfOpen((current) => !current)}
                onClearCitation={clearCitation}
                onDeleteAllThreads={() => deleteAllThreads.mutate()}
              />
              <div className="note-source-view" style={sourcePanelRows ? { gridTemplateRows: sourcePanelRows } : undefined}>
                <section ref={citationPanelRef} className={`panel citation-panel ${citationListOpen ? "" : "citation-panel--compact"}`}>
                  {citationListOpen ? (
                    <div className="list">
                      {citationRows.map(({ citation, badge, label, title, evidence }) => (
                        <button
                          className={`list-row note-citation-row ${selectedCitation?.id === citation.id ? "note-citation-row--active" : ""}`}
                          type="button"
                          key={citation.id}
                          ref={(node) => {
                            citationRowRefs.current[citation.id] = node;
                          }}
                          onClick={() => openCitation(citation)}
                          aria-label={`Quelle ${badge} öffnen`}
                          aria-pressed={selectedCitation?.id === citation.id}
                        >
                          <span className="note-citation-row__title">
                            <span className="citation-badge">{badge}</span>
                            <strong>{title}</strong>
                          </span>
                          <span>{evidence || label}</span>
                          <small>
                            {citation.paper_id}
                            {label !== badge ? ` - ${label}` : ""}
                          </small>
                        </button>
                      ))}
                      {!citations.length ? <div className="muted-row">Keine Quellen in dieser Notiz</div> : null}
                    </div>
                  ) : (
                    <div className="muted-row">Quellenliste eingeklappt</div>
                  )}
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
              </div>
            </>
          )}
        </aside>
      </div>
    </section>
  );
}

function ThreadAnchorMarker({
  label,
  thread,
  placement,
  open,
  active,
  style,
  followUpDraft,
  isSubmitting,
  deleting,
  onToggle,
  onClose,
  onDraftChange,
  onFollowUp,
  onDelete
}: {
  label: string;
  thread: NoteAiThread;
  placement: "left" | "right";
  open: boolean;
  active: boolean;
  style?: CSSProperties;
  followUpDraft: string;
  isSubmitting: boolean;
  deleting: boolean;
  onToggle: () => void;
  onClose: () => void;
  onDraftChange: (value: string) => void;
  onFollowUp: () => void;
  onDelete: () => void;
}) {
  const messages = threadDisplayMessages(thread);
  const context = shortThreadContext(thread.anchor_quote || thread.selected_text);
  return (
    <span className="textarea-thread-anchor-wrap" style={style}>
      <button
        className={`textarea-thread-anchor-button ${active ? "textarea-thread-anchor-button--active" : ""}`}
        type="button"
        aria-label={`KI-Notiz ${label} öffnen`}
        aria-expanded={open}
        title={context || "KI-Notiz"}
        onMouseDown={(event) => event.preventDefault()}
        onClick={(event) => {
          event.stopPropagation();
          onToggle();
        }}
      >
        {label}
      </button>
      {open ? (
        <span className={`thread-anchor-popover thread-anchor-popover--${placement}`} role="dialog" aria-label={`KI-Notiz ${label}`}>
          <span className="thread-anchor-popover__header">
            <span>
              <strong>{label}</strong>
              <small>{context || "Markierter Bereich"}</small>
            </span>
            <span className="thread-anchor-popover__actions">
              <button className="icon-button" type="button" aria-label="KI-Notiz einklappen" onMouseDown={(event) => event.preventDefault()} onClick={onClose}>
                <ChevronDown size={16} />
              </button>
              <button className="icon-button" type="button" aria-label="KI-Notiz loeschen" disabled={deleting} onMouseDown={(event) => event.preventDefault()} onClick={onDelete}>
                <Trash2 size={16} />
              </button>
            </span>
          </span>
          <span className="thread-anchor-messages">
            {messages.map((message) => (
              <span className={`thread-anchor-message thread-anchor-message--${message.role === "assistant" ? "assistant" : "user"}`} key={message.id}>
                <small>{message.role === "assistant" ? "Antwort" : "Frage"}</small>
                <span>{message.content}</span>
              </span>
            ))}
          </span>
          <form
            className="thread-anchor-followup"
            onSubmit={(event) => {
              event.preventDefault();
              if (followUpDraft.trim() && !isSubmitting) {
                onFollowUp();
              }
            }}
          >
            <input
              value={followUpDraft}
              onChange={(event) => onDraftChange(event.target.value)}
              onMouseDown={(event) => event.stopPropagation()}
              placeholder="Weiterfragen"
            />
            <button className="button button-primary" type="submit" disabled={!followUpDraft.trim() || isSubmitting}>
              Fragen
            </button>
          </form>
        </span>
      ) : null}
    </span>
  );
}

function NoteContextToolbar({
  historyOpen,
  citationCount,
  threadCount,
  selectedCitation,
  citationListOpen,
  notePdfOpen,
  threads,
  deleteAllPending,
  onShowSources,
  onShowHistory,
  onToggleCitationList,
  onTogglePdf,
  onClearCitation,
  onDeleteAllThreads
}: {
  historyOpen: boolean;
  citationCount: number;
  threadCount: number;
  selectedCitation: NoteCitation | null;
  citationListOpen: boolean;
  notePdfOpen: boolean;
  threads: NoteAiThread[];
  deleteAllPending: boolean;
  onShowSources: () => void;
  onShowHistory: () => void;
  onToggleCitationList: () => void;
  onTogglePdf: () => void;
  onClearCitation: () => void;
  onDeleteAllThreads: () => void;
}) {
  return (
    <div className="note-context-toolbar">
      <div className="segmented note-context-tabs" aria-label="Kontextansicht">
        <button type="button" className={!historyOpen ? "active" : ""} onClick={onShowSources}>
          <Quote size={15} />
          <span>Quellen</span>
          <strong>{citationCount}</strong>
        </button>
        <button type="button" className={historyOpen ? "active" : ""} onClick={onShowHistory}>
          <Sparkles size={15} />
          <span>KI</span>
          <strong>{threadCount}</strong>
        </button>
      </div>
      <div className="note-context-actions">
        {!historyOpen ? (
          <>
            <button
              className="icon-button"
              type="button"
              aria-label={citationListOpen ? "Quellenliste einklappen" : "Quellenliste ausklappen"}
              onClick={onToggleCitationList}
            >
              {citationListOpen ? <ChevronUp size={17} /> : <ChevronDown size={17} />}
            </button>
            <button className="icon-button" type="button" aria-label={notePdfOpen ? "PDF einklappen" : "PDF anzeigen"} onClick={onTogglePdf}>
              {notePdfOpen ? <PanelRightClose size={17} /> : <PanelRightOpen size={17} />}
            </button>
            <button className="button button-compact" type="button" onClick={onClearCitation} disabled={!selectedCitation} aria-label="Keine Quelle aktiv">
              <X size={15} />
              <span>Keine</span>
            </button>
          </>
        ) : (
          <button
            className="button button-compact"
            type="button"
            aria-label="Alle KI-Verlaeufe loeschen"
            onClick={onDeleteAllThreads}
            disabled={!threads.length || deleteAllPending}
          >
            <Trash2 size={16} />
            <span>Alle</span>
          </button>
        )}
      </div>
    </div>
  );
}

function AiThreadList({
  threads,
  activeThreadId,
  threadMeta,
  followUpDrafts,
  isSubmitting,
  onActiveThreadChange,
  onLocateThread,
  onDraftChange,
  onFollowUp,
  onInsert,
  onPreviewInsert,
  onPreviewClear,
  onCollapseChange,
  onInsertMessage,
  onPreviewMessage,
  onHideMessage,
  onDelete,
  deletingThreadId
}: {
  threads: NoteAiThread[];
  activeThreadId: string;
  threadMeta: Map<string, ThreadAnchorMeta>;
  followUpDrafts: Record<string, string>;
  isSubmitting: boolean;
  onActiveThreadChange: (threadId: string) => void;
  onLocateThread: (threadId: string) => void;
  onDraftChange: (threadId: string, value: string) => void;
  onFollowUp: (thread: NoteAiThread) => void;
  onInsert: (thread: NoteAiThread) => void;
  onPreviewInsert: (thread: NoteAiThread) => void;
  onPreviewClear: () => void;
  onCollapseChange: (thread: NoteAiThread, collapsed: boolean) => void;
  onInsertMessage: (thread: NoteAiThread, message: NoteAiMessage) => void;
  onPreviewMessage: (thread: NoteAiThread, message: NoteAiMessage) => void;
  onHideMessage: (thread: NoteAiThread, messageId: string) => void;
  onDelete: (thread: NoteAiThread) => void;
  deletingThreadId?: string;
}) {
  if (!threads.length) {
    return <div className="muted-row">Noch keine KI-Fragen</div>;
  }
  return (
    <div className="ai-thread-list">
      {threads.map((thread) => {
        const storedCollapsed = threadCollapsed(thread);
        const collapsed = storedCollapsed;
        const answer = latestThreadAnswer(thread);
        const messages = threadDisplayMessages(thread);
        const context = shortThreadContext(thread.anchor_quote || thread.selected_text);
        const answerPreview = shortThreadContext(answer);
        const meta = threadMeta.get(thread.id);
        return (
          <article
            className={`note-thread-row ai-thread-card ${meta ? "ai-thread-card--anchored" : ""} ${collapsed ? "ai-thread-card--compact" : ""} ${activeThreadId === thread.id ? "ai-thread-card--active" : ""}`}
            key={thread.id}
            style={{ ...(meta ? evidenceColorVars(meta.colorIndex) : {}), ...threadSizeStyle(thread) }}
          >
            <div className="ai-thread-topline">
              <button className="ai-thread-header" type="button" title={context || undefined} onClick={() => onLocateThread(thread.id)}>
                <span className="ai-thread-header-line">
                  {meta ? (
                    <span className="ai-thread-anchor-badge" aria-label={`KI-Notiz ${meta.label}`}>
                      {meta.label}
                    </span>
                  ) : null}
                  <strong>{thread.instruction}</strong>
                </span>
              </button>
              <div className="ai-thread-actions">
                <button
                  className="button button-compact"
                  type="button"
                  onClick={() => {
                    onActiveThreadChange(thread.id);
                    onCollapseChange(thread, !collapsed);
                  }}
                >
                  {collapsed ? "Öffnen" : "Einklappen"}
                </button>
                <button
                  className="button button-compact"
                  type="button"
                  onClick={() => onInsert(thread)}
                  onMouseEnter={() => onPreviewInsert(thread)}
                  onMouseOver={() => onPreviewInsert(thread)}
                  onMouseLeave={onPreviewClear}
                  onPointerEnter={() => onPreviewInsert(thread)}
                  onPointerOver={() => onPreviewInsert(thread)}
                  onPointerLeave={onPreviewClear}
                  onFocus={() => onPreviewInsert(thread)}
                  onBlur={onPreviewClear}
                  disabled={!answer}
                >
                  Einfügen
                </button>
                <button
                  className="icon-button icon-button--compact"
                  type="button"
                  aria-label="KI-Verlauf loeschen"
                  onClick={(event) => {
                    event.stopPropagation();
                    onDelete(thread);
                  }}
                  disabled={deletingThreadId === thread.id}
                >
                  <Trash2 size={15} />
                </button>
              </div>
            </div>
            <div className="ai-thread-preview">
              {context ? <p>{context}</p> : null}
              {answerPreview ? <p className="ai-thread-answer-preview">{answerPreview}</p> : null}
            </div>
            {!collapsed ? (
              <>
                <div className="ai-thread-messages">
                  {messages.map((message) => (
                    <div className={`ai-thread-message ai-thread-message--${message.role === "assistant" ? "assistant" : "user"}`} key={message.id}>
                      <div className="ai-thread-message-topline">
                        <span>{message.role === "assistant" ? "KI" : "Du"}</span>
                      </div>
                      <p>{message.content}</p>
                      {message.role === "assistant" ? (
                        <div className="ai-thread-message-actions">
                          <button
                            className="button button-compact"
                            type="button"
                            onClick={() => onInsertMessage(thread, message)}
                            onMouseEnter={() => onPreviewMessage(thread, message)}
                            onMouseOver={() => onPreviewMessage(thread, message)}
                            onMouseLeave={onPreviewClear}
                            onPointerEnter={() => onPreviewMessage(thread, message)}
                            onPointerOver={() => onPreviewMessage(thread, message)}
                            onPointerLeave={onPreviewClear}
                            onFocus={() => onPreviewMessage(thread, message)}
                            onBlur={onPreviewClear}
                          >
                            Einfügen
                          </button>
                          <button
                            className="icon-button icon-button--compact"
                            type="button"
                            aria-label="KI-Antwort ausblenden"
                            onClick={() => onHideMessage(thread, message.id)}
                          >
                            <Trash2 size={15} />
                          </button>
                        </div>
                      ) : null}
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
  activeCitationId,
  onCitationClick,
  editable = false,
  onBlockChange
}: {
  previewRef?: RefObject<HTMLElement>;
  markdown: string;
  citations: NoteCitation[];
  activeCitationId?: string;
  onCitationClick: (citation: NoteCitation) => void;
  editable?: boolean;
  onBlockChange?: (blockIndex: number, nextRaw: string, expectedRaw?: string) => void;
}) {
  const citationById = useMemo(() => new Map(citations.map((citation) => [citation.id, citation])), [citations]);
  const blocks = useMemo(() => splitMarkdownBlocks(markdown), [markdown]);
  return (
    <article ref={previewRef} className={`markdown-preview ${editable ? "markdown-preview--editable" : ""}`}>
      {blocks.map((block, index) => {
        const rendered = renderBlock(block.raw, `${index}`, citationById, onCitationClick, activeCitationId ?? "");
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
                const nextRaw = previewElementToMarkdown(block.raw, event.currentTarget);
                if (nextRaw === block.raw) {
                  return;
                }
                window.setTimeout(() => {
                  onBlockChange?.(index, nextRaw, block.raw);
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

function renderBlock(block: string, key: string, citations: Map<string, NoteCitation>, onCitationClick: (citation: NoteCitation) => void, activeCitationId = "") {
  const trimmed = block.trim();
  if (!trimmed) {
    return null;
  }
  if (/^!\[[^\]]*\]\([^)]+\)$/.test(trimmed)) {
    const match = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(trimmed);
    return <img key={key} className="markdown-preview-image" alt={match?.[1] ?? ""} src={match?.[2]} />;
  }
  if (trimmed.startsWith("# ")) {
    return <h1 key={key}>{renderInline(trimmed.slice(2), citations, onCitationClick, activeCitationId)}</h1>;
  }
  if (trimmed.startsWith("## ")) {
    return <h2 key={key}>{renderInline(trimmed.slice(3), citations, onCitationClick, activeCitationId)}</h2>;
  }
  if (trimmed.startsWith(">")) {
    return <blockquote key={key}>{renderInline(trimmed.replace(/^>\s?/gm, ""), citations, onCitationClick, activeCitationId)}</blockquote>;
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
                return <Tag key={`${key}-${rowIndex}-${cellIndex}`}>{renderInline(cell.trim(), citations, onCitationClick, activeCitationId)}</Tag>;
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
          <li key={`${key}-${itemIndex}`}>{renderInline(line.replace(/^- /, ""), citations, onCitationClick, activeCitationId)}</li>
        ))}
      </ul>
    );
  }
  return <p key={key}>{renderInline(trimmed, citations, onCitationClick, activeCitationId)}</p>;
}

function renderInline(text: string, citations: Map<string, NoteCitation>, onCitationClick: (citation: NoteCitation) => void, activeCitationId = "") {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|==[^=]+==|<mark(?:\s+[^>]*)?>.*?<\/mark>|<span style="color:[^"]+">.*?<\/span>|\[[^\]]+\]\([^)]+\))/g);
  return parts.map((part, index) => {
    const citationMatch = /^\[([^\]]+)\]\(sciencekg:\/\/citation\/([^)]+)\)$/.exec(part);
    if (citationMatch) {
      const citation = citations.get(citationMatch[2]);
      return (
        <button
          key={`${part}-${index}`}
          type="button"
          className={`citation-link citation-link--mapped ${citationMatch[2] === activeCitationId ? "citation-link--active" : ""}`}
          contentEditable={false}
          data-citation-id={citationMatch[2]}
          data-citation-label={citationMatch[1]}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => citation && onCitationClick(citation)}
        >
          {citationMatch[1]}
        </button>
      );
    }
    const linkMatch = /^\[([^\]]+)\]\(([^)]+)\)$/.exec(part);
    if (linkMatch) {
      return (
        <a key={`${part}-${index}`} href={linkMatch[2]} target="_blank" rel="noreferrer" data-link-href={linkMatch[2]}>
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
      return <span key={`${part}-${index}`} style={{ color: colorMatch[1] }} data-color={colorMatch[1]}>{colorMatch[2]}</span>;
    }
    return <span key={`${part}-${index}`}>{part}</span>;
  });
}

function previewElementToMarkdown(original: string, root: HTMLElement) {
  const directChild = Array.from(root.children).find((child): child is HTMLElement => child instanceof HTMLElement);
  if (!directChild) {
    return previewTextToMarkdown(original, root.innerText);
  }
  const tag = directChild.tagName.toLowerCase();
  const originalTrimmed = original.trim();
  if (tag === "h1") {
    return `# ${firstLine(serializePreviewInline(directChild))}`;
  }
  if (tag === "h2") {
    return `## ${firstLine(serializePreviewInline(directChild))}`;
  }
  if (tag === "blockquote") {
    return serializePreviewInline(directChild)
      .split(/\n+/)
      .map((line) => `> ${line}`)
      .join("\n");
  }
  if (tag === "ul") {
    const items = Array.from(directChild.querySelectorAll(":scope > li"));
    if (items.length) {
      return items.map((item) => `- ${serializePreviewInline(item).replace(/^[-*+]\s+/, "")}`).join("\n");
    }
  }
  if (tag === "p") {
    return serializePreviewInline(directChild).trimEnd();
  }
  if (originalTrimmed.startsWith("# ") || originalTrimmed.startsWith("## ") || originalTrimmed.startsWith(">") || /^- /m.test(originalTrimmed)) {
    return previewTextToMarkdown(original, root.innerText);
  }
  return serializePreviewInline(root).trimEnd();
}

function serializePreviewInline(node: Node): string {
  return Array.from(node.childNodes).map(serializePreviewNode).join("").replace(/\u00a0/g, " ");
}

function serializePreviewNode(node: Node): string {
  if (node.nodeType === Node.TEXT_NODE) {
    return node.textContent ?? "";
  }
  if (!(node instanceof HTMLElement)) {
    return "";
  }
  const tag = node.tagName.toLowerCase();
  const inner = serializePreviewInline(node);
  if (tag === "br") {
    return "\n";
  }
  if (tag === "button" && node.dataset.citationId) {
    const label = (node.dataset.citationLabel || node.innerText || "Quelle").trim();
    return `[${label}](sciencekg://citation/${node.dataset.citationId})`;
  }
  if (tag === "a") {
    const href = node.dataset.linkHref || node.getAttribute("href") || "";
    return href ? `[${inner}](${href})` : inner;
  }
  if (tag === "strong" || tag === "b") {
    return `**${inner}**`;
  }
  if (tag === "em" || tag === "i") {
    return `*${inner}*`;
  }
  if (tag === "code") {
    return `\`${inner}\``;
  }
  if (tag === "mark") {
    return `==${inner}==`;
  }
  if (tag === "span" && (node.dataset.color || node.style.color)) {
    return `<span style="color:${node.dataset.color || node.style.color}">${inner}</span>`;
  }
  if (tag === "div" || tag === "p") {
    return inner;
  }
  return inner;
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

function parseMarkdownCitationRefs(markdown: string): CitationMarkdownRef[] {
  const refs: CitationMarkdownRef[] = [];
  const pattern = /\[([^\]]+)\]\(sciencekg:\/\/citation\/([^)]+)\)/g;
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(markdown)) !== null) {
    const label = match[1].trim();
    refs.push({
      id: match[2],
      label,
      badge: citationBadgeFromLabel(label),
      title: citationTitleFromLabel(label),
      start: match.index,
      end: match.index + match[0].length
    });
  }
  return refs;
}

function citationRefAtPosition(refs: CitationMarkdownRef[], position: number) {
  return refs.find((ref) => position >= ref.start && position <= ref.end) ?? null;
}

function threadAnchorRange(thread: NoteAiThread, markdown: string) {
  const quote = stripHighlightMarkers(thread.anchor_quote || thread.selected_text || "").trim();
  const anchorStart = thread.anchor_start === null || thread.anchor_start === undefined ? NaN : Number(thread.anchor_start);
  const anchorEnd = thread.anchor_end === null || thread.anchor_end === undefined ? NaN : Number(thread.anchor_end);
  if (Number.isFinite(anchorStart) && Number.isFinite(anchorEnd) && anchorStart >= 0 && anchorEnd > anchorStart && anchorEnd <= markdown.length) {
    if (!quote || markdown.slice(anchorStart, anchorEnd).includes(quote) || quote.includes(markdown.slice(anchorStart, anchorEnd).trim())) {
      return { start: anchorStart, end: anchorEnd };
    }
  }
  if (quote) {
    const index = markdown.indexOf(quote);
    if (index >= 0) {
      return { start: index, end: index + quote.length };
    }
  }
  return null;
}

function threadAnchorPlacement(markdown: string, index: number): "left" | "right" {
  const lineStart = markdown.lastIndexOf("\n", Math.max(0, index - 1)) + 1;
  return index - lineStart > 48 ? "left" : "right";
}

function citationBadgeFromLabel(label: string) {
  return label.match(/\bZ\d+\b/i)?.[0].toUpperCase() ?? "Quelle";
}

function citationTitleFromLabel(label: string) {
  const stripped = label.replace(/^\s*Z\d+\s*[-:]\s*/i, "").trim();
  return stripped || label.trim();
}

function shortText(value: string | null | undefined, max = 88) {
  const text = String(value || "").replace(/\s+/g, " ").trim();
  return text.length > max ? `${text.slice(0, Math.max(0, max - 3))}...` : text;
}

function estimatedTextareaScrollTop(markdown: string, index: number, node: HTMLTextAreaElement) {
  const lineHeight = parseFloat(window.getComputedStyle(node).lineHeight) || 22;
  const linesBefore = markdown.slice(0, Math.max(0, index)).split("\n").length - 1;
  return Math.max(0, linesBefore * lineHeight - node.clientHeight * 0.32);
}

function latestThreadAnswer(thread: NoteAiThread) {
  const messages = visibleThreadMessages(thread);
  const answer = [...messages].reverse().find((message) => message.role === "assistant")?.content;
  return (answer || "").trim();
}

function threadDisplayMessages(thread: NoteAiThread) {
  const hiddenIds = hiddenThreadMessageIds(thread);
  const messages = visibleThreadMessages(thread);
  const cleanedMessages = messages.filter((message) => message.role !== "assistant" || message.content.trim());
  const answer = (thread.replacement_text || thread.response_text || "").trim();
  const hasAssistantText = cleanedMessages.some((message) => message.role === "assistant" && message.content.trim());
  const hiddenStoredAssistant = Boolean(thread.messages?.some((message) => message.role === "assistant" && hiddenIds.has(message.id)));
  const fallbackId = `${thread.id}:assistant:fallback`;
  if (!answer || hasAssistantText || hiddenStoredAssistant || hiddenIds.has(fallbackId)) {
    return cleanedMessages;
  }
  return [
    ...cleanedMessages,
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

function visibleThreadMessages(thread: NoteAiThread) {
  const hiddenIds = hiddenThreadMessageIds(thread);
  return (thread.messages?.length ? thread.messages : legacyThreadMessages(thread)).filter((message) => !hiddenIds.has(message.id));
}

function hiddenThreadMessageIds(thread: NoteAiThread) {
  const value = thread.ui_state?.hidden_message_ids;
  if (!Array.isArray(value)) {
    return new Set<string>();
  }
  return new Set(value.map((item) => String(item)));
}

function threadCollapsed(thread: NoteAiThread) {
  return thread.ui_state?.collapsed !== false;
}

function shortThreadContext(value: string) {
  const text = stripHighlightMarkers(value || "").replace(/\s+/g, " ").trim();
  return text.length <= 170 ? text : `${text.slice(0, 167)}...`;
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
  return {
    width: width > 260 ? `${width}px` : undefined,
  };
}

function assignMissingThreadMeta(current: Record<string, ThreadAnchorMeta>, threadIds: string[]) {
  let next = current;
  let maxNumber = Object.values(current).reduce((max, meta) => Math.max(max, threadMetaNumber(meta)), 0);
  for (const threadId of threadIds) {
    if (next[threadId]) {
      continue;
    }
    if (next === current) {
      next = { ...current };
    }
    maxNumber += 1;
    next[threadId] = {
      label: `N${maxNumber}`,
      colorIndex: maxNumber - 1
    };
  }
  return next;
}

function threadMetaNumber(meta: ThreadAnchorMeta) {
  const match = /^N(\d+)$/i.exec(meta.label);
  return match ? Number(match[1]) : meta.colorIndex + 1;
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

function loadNumberUiState(key: string, fallback: number) {
  try {
    const value = Number(window.localStorage.getItem(uiStateKey(key)));
    return Number.isFinite(value) && value > 0 ? value : fallback;
  } catch {
    return fallback;
  }
}

function saveNumberUiState(key: string, value: number) {
  try {
    window.localStorage.setItem(uiStateKey(key), String(value));
  } catch {
    // Local storage can be unavailable in private/browser test contexts.
  }
}

function loadThreadMetaUiState(key: string, fallback: Record<string, ThreadAnchorMeta>) {
  try {
    const raw = window.localStorage.getItem(uiStateKey(key));
    if (!raw) {
      return fallback;
    }
    const parsed = JSON.parse(raw) as Record<string, ThreadAnchorMeta>;
    if (!parsed || typeof parsed !== "object") {
      return fallback;
    }
    return Object.fromEntries(
      Object.entries(parsed)
        .filter(([, value]) => value && typeof value.label === "string" && Number.isFinite(value.colorIndex))
        .map(([threadId, value]) => [threadId, { label: value.label, colorIndex: value.colorIndex }])
    );
  } catch {
    return fallback;
  }
}

function saveThreadMetaUiState(key: string, value: Record<string, ThreadAnchorMeta>) {
  try {
    window.localStorage.setItem(uiStateKey(key), JSON.stringify(value));
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
