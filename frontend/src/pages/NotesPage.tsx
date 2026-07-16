import { Fragment, KeyboardEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { ClipboardEvent as ReactClipboardEvent, DragEvent as ReactDragEvent, PointerEvent as ReactPointerEvent } from "react";
import type { CSSProperties, MutableRefObject, ReactNode, RefObject } from "react";
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
  Globe,
  Highlighter,
  ImagePlus,
  ImageOff,
  Italic,
  Languages,
  Link,
  List,
  Minus,
  NotebookPen,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  Quote,
  Redo2,
  Search,
  SeparatorHorizontal,
  MessageSquareText,
  SpellCheck2,
  Sparkles,
  Table2,
  Trash2,
  Undo2,
  X
} from "lucide-react";

import { api, API_BASE_URL } from "../api";
import { colorVarsForPaperId, evidenceColorVars } from "../citationColors";
import { EmptyState } from "../components/EmptyState";
import { PdfPane } from "../components/PdfPane";
import { TextareaHighlightLayer } from "../components/TextareaHighlightLayer";
import { downloadMarkdownFile } from "../download";
import { noteProjectId, projectScopeLabel } from "../projectScope";
import { useAppState } from "../state";

import {
  AiThreadList,
  MarkdownPreview,
  NoteContextToolbar,
  ThreadAnchorMarker,
  highlightPreviewSearch,
  renderBlock,
  renderInline,
} from "./NotesSubComponents";
export * from "./notesHelpers";
// Pure helpers live in ./notesHelpers; re-export for back-compat and import the used ones.
import {
  FORMAT_AS_MARKDOWN_INSTRUCTION,
  IMAGE_PREVIEW_HIDDEN_KEY,
  TABLE_PICKER_COLS,
  TABLE_PICKER_ROWS,
  THREAD_RANGE_TEXT_LIMIT,
  TRANSLATE_LANGUAGES,
  absoluteUrl,
  applyTabIndent,
  assignMissingThreadMeta,
  attachTextareaAutoSync,
  buildMarkdownTable,
  caretIndexFromPoint,
  clampSplitRatio,
  continueMarkdownLine as continueMarkdownLineAt,
  dividerSnippetForTextarea,
  citationBadgeFromLabel,
  citationColorIndex,
  citationRefAtPosition,
  citationTitleFromLabel,
  estimatedTextareaScrollTop,
  findNoteSearchRanges,
  formatError,
  hiddenThreadMessageIds,
  isComplexPreviewBlock,
  isUntitledNoteTitle,
  latestThreadAnswer,
  loadBooleanUiState,
  loadNumberUiState,
  loadThreadMetaUiState,
  noteTitleForSave,
  parseMarkdownCitationRefs,
  previewElementToMarkdown,
  saveBooleanUiState,
  saveNumberUiState,
  saveThreadMetaUiState,
  seedThreadRanges,
  shiftThreadRanges,
  shortText,
  shortThreadContext,
  sortPinnedThreads,
  splitMarkdownBlocks,
  stripHighlightMarkers,
  suggestNoteTitle,
  textTerms,
  threadAnchorPlacement,
  threadAnchorRange,
  threadCollapsed,
  threadDisplayMessages,
  threadHighlightSegments,
  threadPinned,
  threadRangeMatchesUi,
  threadRangeUiState,
  threadResultRange,
  threadSizeStyle,
  toggleWrap,
  withPreservedCitationLinks,
} from "./notesHelpers";
import type { Note, NoteAiMessage, NoteAiThread, NoteCitation, VerificationEvidence } from "../types";

export type SelectionRange = {
  start: number;
  end: number;
  text: string;
};

export type EditorMode = "edit" | "preview" | "split";

export type NoteAiThreadsResult = {
  items: NoteAiThread[];
  total: number;
};

export type CitationMarkdownRef = {
  id: string;
  label: string;
  badge: string;
  title: string;
  start: number;
  end: number;
  /** Present when this ref comes from a grouped Quellen-chip: all citation ids it holds. */
  groupIds?: string[];
};

export type ThreadAnchorMeta = {
  label: string;
  colorIndex: number;
};

export type ThreadTextRange = {
  start: number;
  end: number;
};

export type ThreadTextDiff = {
  start: number;
  beforeEnd: number;
  afterEnd: number;
  delta: number;
};

export type ThreadStoredRange = {
  start: number;
  end: number;
  text: string;
  manualRanges?: ThreadTextRange[];
};

export type ThreadAnchorRange = ThreadStoredRange & {
  changedRanges: ThreadTextRange[];
  manualRanges: ThreadTextRange[];
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
  historyOpen: boolean;
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
  showHistory: () => void;
  showSources: () => void;
  activateThread: (threadId: string) => void;
  locateThread: (threadId: string) => void;
  setActiveThread: (threadId: string) => void;
  toggleThreadCollapsed: (threadId: string) => void;
  setThreadPinned: (threadId: string, pinned: boolean) => void;
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
  deleteNote: (noteId: string) => void;
};

type EditorViewportSnapshot = {
  scrollTop: number;
  scrollLeft: number;
  cursor: number;
};

type NotesSurfaceProps = {
  variant?: "page" | "workspace" | "overlay";
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
  const embedded = variant !== "page";
  const scopedProjectId = noteProjectId(activeProject);
  const scopeLabel = projectScopeLabel(activeProject);
  // Namespaced separately so the overlay's compact panel defaults don't inherit
  // (or clobber) whatever the standalone/workspace Notes view left collapsed/expanded.
  const uiKeyPrefix = variant === "overlay" ? `${scopedProjectId}.overlay` : scopedProjectId;
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
  const [noteQuestion, setNoteQuestion] = useState("");
  const [noteSearchQuery, setNoteSearchQuery] = useState("");
  const [noteSearchIndex, setNoteSearchIndex] = useState(0);
  const [tableMenuOpen, setTableMenuOpen] = useState(false);
  const [tableHover, setTableHover] = useState({ cols: 0, rows: 0 });
  const [splitRatio, setSplitRatio] = useState(() => loadNumberUiState("editor.splitRatio", 0.5));
  const splitGridRef = useRef<HTMLDivElement | null>(null);
  const [translateLanguage, setTranslateLanguage] = useState("Deutsch");
  const [aiPopoverBottomPadding, setAiPopoverBottomPadding] = useState(0);
  const [undoStack, setUndoStack] = useState<string[]>([]);
  const [selectedCitation, setSelectedCitation] = useState<NoteCitation | null>(null);
  const [selectedCitationRef, setSelectedCitationRef] = useState<CitationMarkdownRef | null>(null);
  const [activeEditorCitationId, setActiveEditorCitationId] = useState("");
  const [activeEditorCitationRef, setActiveEditorCitationRef] = useState<CitationMarkdownRef | null>(null);
  const [inlineThreadId, setInlineThreadId] = useState("");
  const [hoverThreadId, setHoverThreadId] = useState("");
  const [historyOpen, setHistoryOpen] = useState(false);
  const [notesListOpen, setNotesListOpen] = useState(() => loadBooleanUiState(`${uiKeyPrefix}.notesListOpen`, true));
  const [contextOpen, setContextOpen] = useState(() => loadBooleanUiState(`${uiKeyPrefix}.contextOpen`, variant !== "overlay"));
  const [notePdfOpen, setNotePdfOpen] = useState(() => loadBooleanUiState(`${uiKeyPrefix}.notePdfOpen`, true));
  const [citationListOpen, setCitationListOpen] = useState(() => loadBooleanUiState(`${uiKeyPrefix}.citationListOpen`, true));
  const [spellcheckEnabled, setSpellcheckEnabled] = useState(() => loadBooleanUiState("spellcheckEnabled", true));
  // N-Marker (KI-Thread-Anker) im Editor ein-/ausblendbar.
  const [threadAnchorsVisible, setThreadAnchorsVisible] = useState(() => loadBooleanUiState("threadAnchorsVisible", true));
  const [contextWidth, setContextWidth] = useState(() => loadNumberUiState(`${uiKeyPrefix}.contextWidth`, 430));
  const [activeThreadId, setActiveThreadId] = useState("");
  const [locatedThreadId, setLocatedThreadId] = useState("");
  const [threadMetaById, setThreadMetaById] = useState<Record<string, ThreadAnchorMeta>>({});
  const [localThreadRanges, setLocalThreadRanges] = useState<Record<string, ThreadStoredRange>>({});
  const lastAiThreadRef = useRef<NoteAiThread | null>(null);
  const [followUpDrafts, setFollowUpDrafts] = useState<Record<string, string>>({});
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const [editorScrollLeft, setEditorScrollLeft] = useState(0);
  const [insertPreview, setInsertPreview] = useState<{ index: number; content: string } | null>(null);
  // Bild-Thumbnails im Edit-Modus verdecken sonst den Text darunter — abschaltbar (persistiert).
  const [imagePreviewHidden, setImagePreviewHidden] = useState(
    () => typeof localStorage !== "undefined" && localStorage.getItem(IMAGE_PREVIEW_HIDDEN_KEY) === "1"
  );
  const textareaRef = useRef<HTMLTextAreaElement | null>(null);
  const editorWrapRef = useRef<HTMLDivElement | null>(null);
  const selectionPopoverRef = useRef<HTMLDivElement | null>(null);
  const citationPanelRef = useRef<HTMLElement | null>(null);
  const citationRowRefs = useRef<Record<string, HTMLButtonElement | null>>({});
  const imageInputRef = useRef<HTMLInputElement | null>(null);
  const handledRequestedCitationIdRef = useRef("");
  const loadedNoteIdRef = useRef("");
  const previewRef = useRef<HTMLElement | null>(null);
  const previewScrollRef = useRef({ top: 0, left: 0 });
  const latestDraftRef = useRef({ noteId: "", title: "", markdown: "" });
  const markdownRef = useRef(markdown);
  const localThreadRangesRef = useRef<Record<string, ThreadStoredRange>>({});
  const pendingEditorViewportRestoreRef = useRef<{ snapshot: EditorViewportSnapshot; markdownLength: number } | null>(null);
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
      localThreadRangesRef.current = {};
      setLocalThreadRanges({});
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
      persistLocalThreadRanges();
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
      localThreadRangesRef.current = {};
      setLocalThreadRanges({});
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    }
  });
  const deleteNoteById = useMutation({
    mutationFn: (noteId: string) => api.deleteNote(noteId),
    onSuccess: (_data, noteId) => {
      if (activeNoteId === noteId) {
        setActiveNoteId("");
        setTitle("");
        setMarkdown("");
        setDirtyState(false);
        loadedNoteIdRef.current = "";
        localThreadRangesRef.current = {};
        setLocalThreadRanges({});
      }
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
      localThreadRangesRef.current = {};
      setLocalThreadRanges({});
      queryClient.setQueryData(["note", note.id], { note });
      queryClient.invalidateQueries({ queryKey: ["notes"] });
    }
  });
  const aiEdit = useMutation({
    // `instructionOverride` lets quick actions (Übersetzen) fire without waiting for
    // the `aiInstruction` state update.
    mutationFn: (instructionOverride?: string | void) =>
      api.createNoteAiThread(activeNoteId, {
        selected_text: stripHighlightMarkers(selection?.text ?? ""),
        instruction: typeof instructionOverride === "string" && instructionOverride ? instructionOverride : aiInstruction,
        provider,
        model,
        use_kg_evidence: true,
        anchor_start: selection?.start ?? null,
        anchor_end: selection?.end ?? null,
        anchor_quote: stripHighlightMarkers(selection?.text ?? "").slice(0, 2000) || null
      }),
    onSuccess: (payload) => {
      // Direktreferenz auf den frisch erzeugten Thread: „Ersetzen"/„Darunter einfügen"
      // dürfen nicht davon abhängen, dass der Threads-Refetch schon durch ist — sonst
      // landet der Ergebnis-Anker auf der alten Selektion statt auf dem KI-Text.
      lastAiThreadRef.current = payload.thread;
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
  const askNote = useMutation({
    mutationFn: () =>
      api.askNote(activeNoteId, {
        question: noteQuestion.trim(),
        provider,
        model,
        use_kg_evidence: true
      }),
    onSuccess: (payload) => {
      setNoteQuestion("");
      setSelection(null);
      setSelectionPinned(false);
      setAiPreview("");
      setAiInstruction("");
      setInlineThreadId("");
      setActiveThreadId(payload.thread.id);
      setHistoryOpen(true);
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
    onMutate: async ({ thread, uiState }) => {
      await queryClient.cancelQueries({ queryKey: threadsQueryKey });
      const previous = queryClient.getQueryData<NoteAiThreadsResult>(threadsQueryKey);
      queryClient.setQueryData<NoteAiThreadsResult>(threadsQueryKey, (current) => {
        if (!current) {
          return current;
        }
        const optimisticThread = { ...thread, ui_state: { ...(thread.ui_state ?? {}), ...uiState } };
        const nextItems = current.items.map((item) => (item.id === thread.id ? optimisticThread : item));
        return { ...current, items: nextItems };
      });
      return { previous };
    },
    onError: (_error, _variables, context) => {
      if (context?.previous) {
        queryClient.setQueryData(threadsQueryKey, context.previous);
      }
    },
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
      localThreadRangesRef.current = Object.fromEntries(Object.entries(localThreadRangesRef.current).filter(([id]) => id !== threadId));
      setLocalThreadRanges(localThreadRangesRef.current);
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
      localThreadRangesRef.current = {};
      setLocalThreadRanges({});
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
  const displayedThreads = useMemo(() => sortPinnedThreads(threads), [threads]);
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
        // reference_text first: it holds the passage that was actually inserted into
        // the note; pdf_excerpt may be a (different) located excerpt or even a URL.
        const evidence = citation.reference_text || citation.pdf_excerpt || citation.kind || "";
        return { citation, badge, label, title: shortText(title, 96), evidence: shortText(evidence, 150), colorIndex: citationColorIndex(citation, index) };
      }),
    [citationRefs, citations]
  );
  const citationColorIndexById = useMemo(
    () => new Map(citationRows.map((row) => [row.citation.id, row.colorIndex])),
    [citationRows]
  );
  const activeEditorCitation = activeEditorCitationId ? citationLookup.get(activeEditorCitationId) ?? null : null;
  const activeEditorCitationRow = activeEditorCitation
    ? citationRows.find((row) => row.citation.id === activeEditorCitation.id) ?? null
    : null;
  const selectedCitationColorIndex = selectedCitation ? citationColorIndexById.get(selectedCitation.id) ?? citationColorIndex(selectedCitation) : 0;
  const activeEditorCitationColorIndex = activeEditorCitation ? citationColorIndexById.get(activeEditorCitation.id) ?? citationColorIndex(activeEditorCitation) : 0;
  const activeCitationRefs = useMemo(() => {
    if (!selectedCitation) {
      return [];
    }
    const refs = citationRefs.filter((item) => item.id === selectedCitation.id);
    if (!refs.length) {
      return [];
    }
    if (selectedCitationRef?.id === selectedCitation.id) {
      const exact = refs.find((item) => item.start === selectedCitationRef.start && item.end === selectedCitationRef.end);
      if (exact) {
        return [exact];
      }
      // Preview-Offsets passen nicht exakt zum Markdown (z.B. Zitat in Fett/Liste):
      // nächstgelegene echte Fundstelle markieren statt stumpf der ersten.
      const nearest = [...refs].sort(
        (left, right) => Math.abs(left.start - selectedCitationRef.start) - Math.abs(right.start - selectedCitationRef.start)
      )[0];
      return [nearest];
    }
    return [refs[0]];
  }, [citationRefs, selectedCitation, selectedCitationRef]);
  const threadAnchors = useMemo(
    () =>
      threads
        .map((thread, index) => {
          const range = threadAnchorRange(thread, markdown, localThreadRanges[thread.id]);
          return range ? { thread, index, ...range } : null;
        })
        .filter((anchor): anchor is { thread: NoteAiThread; index: number } & ThreadAnchorRange => Boolean(anchor)),
    [localThreadRanges, markdown, threads]
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
  const noteSearchRanges = useMemo(() => findNoteSearchRanges(markdown, noteSearchQuery), [markdown, noteSearchQuery]);
  const activeNoteSearchRange = noteSearchRanges[noteSearchIndex] ?? null;

  useEffect(() => {
    if (!noteSearchRanges.length) {
      setNoteSearchIndex(0);
      return;
    }
    if (noteSearchIndex >= noteSearchRanges.length) {
      setNoteSearchIndex(0);
    }
  }, [noteSearchIndex, noteSearchRanges.length]);

  useEffect(() => {
    if (!activeNoteSearchRange) {
      return;
    }
    requestAnimationFrame(() => {
      const node = textareaRef.current;
      if (!node) {
        return;
      }
      node.scrollTop = estimatedTextareaScrollTop(markdown, activeNoteSearchRange.start, node);
      setEditorScrollTop(node.scrollTop);
      setEditorScrollLeft(node.scrollLeft);
    });
  }, [activeNoteSearchRange, markdown]);

  useEffect(() => {
    if (!activeNoteId || !threadAnchors.length) {
      return;
    }
    setThreadMetaById((current) => assignMissingThreadMeta(current, threadAnchors.map((anchor) => anchor.thread.id)));
  }, [activeNoteId, threadAnchors]);

  useEffect(() => {
    if (!activeNoteId || !threads.length) {
      return;
    }
    const seeded = { ...localThreadRangesRef.current };
    let changed = false;
    for (const thread of threads) {
      if (seeded[thread.id]) {
        continue;
      }
      const range = threadResultRange(thread);
      if (range) {
        seeded[thread.id] = range;
        changed = true;
      }
    }
    if (changed) {
      localThreadRangesRef.current = seeded;
      setLocalThreadRanges(seeded);
    }
  }, [activeNoteId, threads]);

  useEffect(() => {
    markdownRef.current = markdown;
    latestDraftRef.current = { noteId: activeNoteId, title, markdown };
  }, [activeNoteId, markdown, title]);

  useLayoutEffect(() => {
    const pending = pendingEditorViewportRestoreRef.current;
    if (!pending) {
      return;
    }
    pendingEditorViewportRestoreRef.current = null;
    restoreEditorViewportNow(pending.snapshot, pending.markdownLength);
  }, [markdown]);

  useEffect(() => {
    setActiveNoteId("");
    setSelectedCitation(null);
    setSelectedCitationRef(null);
    setInlineThreadId("");
    setHoverThreadId("");
    loadedNoteIdRef.current = "";
  }, [scopedProjectId]);

  useEffect(() => {
    if (controlledNoteId && controlledNoteId !== activeNoteId) {
      setActiveNoteId(controlledNoteId);
    }
  }, [activeNoteId, controlledNoteId]);

  useEffect(() => {
    setSelectedCitation(null);
    setSelectedCitationRef(null);
    setActiveEditorCitationId("");
    setActiveEditorCitationRef(null);
    handledRequestedCitationIdRef.current = "";
  }, [activeNoteId]);

  useEffect(() => {
    onActiveNoteChange?.(activeNoteId);
  }, [activeNoteId, onActiveNoteChange]);

  useEffect(() => {
    setNotesListOpen(loadBooleanUiState(`${uiKeyPrefix}.notesListOpen`, true));
    setContextOpen(loadBooleanUiState(`${uiKeyPrefix}.contextOpen`, variant !== "overlay"));
    setNotePdfOpen(loadBooleanUiState(`${uiKeyPrefix}.notePdfOpen`, true));
    setCitationListOpen(loadBooleanUiState(`${uiKeyPrefix}.citationListOpen`, true));
    setContextWidth(loadNumberUiState(`${uiKeyPrefix}.contextWidth`, 430));
  }, [uiKeyPrefix, variant]);

  useEffect(() => {
    if (!activeNoteId) {
      setThreadMetaById({});
      return;
    }
    setThreadMetaById(loadThreadMetaUiState(`${scopedProjectId}.${activeNoteId}.threadMeta`, {}));
  }, [activeNoteId, scopedProjectId]);

  useEffect(() => {
    saveBooleanUiState(`${uiKeyPrefix}.notesListOpen`, notesListOpen);
  }, [notesListOpen, uiKeyPrefix]);

  useEffect(() => {
    saveBooleanUiState(`${uiKeyPrefix}.contextOpen`, contextOpen);
  }, [contextOpen, uiKeyPrefix]);

  useEffect(() => {
    saveBooleanUiState(`${uiKeyPrefix}.notePdfOpen`, notePdfOpen);
  }, [notePdfOpen, uiKeyPrefix]);

  useEffect(() => {
    saveBooleanUiState(`${uiKeyPrefix}.citationListOpen`, citationListOpen);
  }, [citationListOpen, uiKeyPrefix]);

  useEffect(() => {
    saveNumberUiState(`${uiKeyPrefix}.contextWidth`, contextWidth);
  }, [contextWidth, uiKeyPrefix]);

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
    saveBooleanUiState("threadAnchorsVisible", threadAnchorsVisible);
  }, [threadAnchorsVisible]);

  useEffect(() => {
    setInsertPreview(null);
  }, [activeNoteId, activeThreadId, editorMode, historyOpen]);

  // Edit/Preview/Split each conditionally mount their pane, so switching modes remounts a
  // fresh DOM node whose scroll resets to 0 — restore the last known offset instead of always
  // jumping back to the top.
  useLayoutEffect(() => {
    const node = textareaRef.current;
    if (!node) return;
    node.scrollTop = editorScrollTop;
    node.scrollLeft = editorScrollLeft;
    return attachTextareaAutoSync(node, () => markdownRef.current, (next) => updateMarkdown(next));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorMode]);

  useLayoutEffect(() => {
    const node = previewRef.current;
    if (!node) return;
    node.scrollTop = previewScrollRef.current.top;
    node.scrollLeft = previewScrollRef.current.left;
  }, [editorMode]);

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
    if (switchedNote) {
      setActiveThreadId("");
      setInlineThreadId("");
      setHoverThreadId("");
      localThreadRangesRef.current = {};
      setLocalThreadRanges({});
    }
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
        found_in_pdf_text: Boolean(selectedCitation.pdf_excerpt),
        evidence_index: selectedCitationColorIndex
      }
    ];
  }, [selectedCitation, selectedCitationColorIndex]);

  function setDirtyState(value: boolean) {
    dirtyRef.current = value;
    setDirty(value);
  }

  function storeLocalThreadRanges(ranges: Record<string, ThreadStoredRange>) {
    localThreadRangesRef.current = ranges;
    setLocalThreadRanges(ranges);
    mirrorThreadRangesInCache(ranges);
  }

  function mirrorThreadRangesInCache(ranges: Record<string, ThreadStoredRange>) {
    if (!activeNoteId || !Object.keys(ranges).length) {
      return;
    }
    queryClient.setQueryData<NoteAiThreadsResult>(threadsQueryKey, (current) => {
      if (!current) {
        return current;
      }
      let changed = false;
      const nextItems = current.items.map((thread) => {
        const range = ranges[thread.id];
        if (!range || threadRangeMatchesUi(thread, range)) {
          return thread;
        }
        changed = true;
        return {
          ...thread,
          ui_state: {
            ...(thread.ui_state ?? {}),
            ...threadRangeUiState(range)
          }
        };
      });
      return changed ? { ...current, items: nextItems } : current;
    });
  }

  function applyMarkdownChange(value: string, options: { markDirty?: boolean; clearPreview?: boolean } = {}) {
    const shiftedRanges = shiftThreadRanges(seedThreadRanges(localThreadRangesRef.current, threads), markdownRef.current, value);
    storeLocalThreadRanges(shiftedRanges);
    markdownRef.current = value;
    setMarkdown(value);
    if (options.markDirty ?? true) {
      setDirtyState(true);
    }
    if (options.clearPreview ?? true) {
      setInsertPreview(null);
    }
    setActiveEditorCitationId("");
    setActiveEditorCitationRef(null);
  }

  function updateMarkdown(value: string) {
    applyMarkdownChange(value);
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
        const ref = citationRefAtPosition(citationRefs, node.selectionStart);
        setActiveEditorCitationId(ref?.id ?? "");
        setActiveEditorCitationRef(ref ?? null);
      }
      return null;
    }
    lastCursorRef.current = node.selectionEnd;
    setActiveEditorCitationId("");
    setActiveEditorCitationRef(null);
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
    if (event.key === "Tab") {
      event.preventDefault();
      const node = textareaRef.current;
      if (!node) return;
      const result = applyTabIndent(markdown, node.selectionStart, node.selectionEnd, event.shiftKey);
      if (!result) return;
      pushUndo();
      updateMarkdown(result.next);
      requestAnimationFrame(() => {
        textareaRef.current?.focus();
        textareaRef.current?.setSelectionRange(result.selStart, result.selEnd);
      });
      return;
    }
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

  function rememberThreadResultRange(thread: NoteAiThread | null | undefined, start: number, end: number, text: string) {
    if (!thread || end <= start) {
      return;
    }
    const range: ThreadStoredRange = { start, end, text, manualRanges: [] };
    storeLocalThreadRanges({ ...localThreadRangesRef.current, [thread.id]: range });
    updateThreadUi.mutate({
      thread,
      uiState: {
        result_anchor_start: start,
        result_anchor_end: end,
        result_anchor_text: text.slice(0, THREAD_RANGE_TEXT_LIMIT),
        result_manual_ranges: []
      }
    });
  }

  /** Aktiver Thread — bevorzugt aus der Liste, sonst die frische aiEdit-Antwort. */
  function resolveActiveThread(): NoteAiThread | null {
    if (!activeThreadId) {
      return null;
    }
    return (
      threads.find((item) => item.id === activeThreadId) ??
      (lastAiThreadRef.current?.id === activeThreadId ? lastAiThreadRef.current : null)
    );
  }

  function replaceSelection(value: string) {
    if (!selection) {
      return;
    }
    // AI rewrites tend to shorten quotes and silently drop their
    // [Zx - ...](sciencekg://citation/...) anchors — the note then loses its source
    // trail. Re-attach every citation link of the replaced range that the rewrite
    // did not carry over.
    const replaced = withPreservedCitationLinks(markdown.slice(selection.start, selection.end), value);
    const thread = resolveActiveThread();
    pushUndo();
    updateMarkdown(`${markdown.slice(0, selection.start)}${replaced}${markdown.slice(selection.end)}`);
    rememberThreadResultRange(thread, selection.start, selection.start + replaced.length, replaced);
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

  // ---- Bilder: Strg+V / Drag&Drop → Note-Asset hochladen + Markdown einfügen ----

  function handleEditorPaste(event: ReactClipboardEvent<HTMLTextAreaElement>) {
    const files = Array.from(event.clipboardData?.files ?? []).filter((file) => file.type.startsWith("image/"));
    if (!files.length) {
      return;
    }
    event.preventDefault();
    for (const file of files) {
      uploadAsset.mutate(file);
    }
  }

  function handleEditorDragOver(event: ReactDragEvent<HTMLTextAreaElement>) {
    const types = event.dataTransfer.types;
    if (types.includes("Files") || types.includes("application/x-paperkg-image")) {
      event.preventDefault();
      event.dataTransfer.dropEffect = types.includes("Files") ? "copy" : "move";
    }
  }

  function handleEditorDrop(event: ReactDragEvent<HTMLTextAreaElement>) {
    const moveSnippet = event.dataTransfer.getData("application/x-paperkg-image");
    const files = Array.from(event.dataTransfer.files ?? []).filter((file) => file.type.startsWith("image/"));
    if (!moveSnippet && !files.length) {
      return; // normaler Text-Drop → Browser-Default
    }
    event.preventDefault();
    const node = event.currentTarget;
    const caret = node.selectionStart ?? markdown.length;
    if (moveSnippet) {
      // Bild-Referenz verschieben: alte Stelle entfernen, an der Drop-Position einfügen.
      const snippetIndex = markdown.indexOf(moveSnippet);
      if (snippetIndex < 0) {
        return;
      }
      const without = `${markdown.slice(0, snippetIndex)}${markdown.slice(snippetIndex + moveSnippet.length)}`;
      const adjusted = snippetIndex < caret ? Math.max(0, caret - moveSnippet.length) : caret;
      pushUndo();
      updateMarkdown(`${without.slice(0, adjusted)}${moveSnippet}${without.slice(adjusted)}`);
      requestAnimationFrame(() => {
        textareaRef.current?.focus();
        textareaRef.current?.setSelectionRange(adjusted + moveSnippet.length, adjusted + moveSnippet.length);
      });
      return;
    }
    // Datei-Drop: Cursor auf die Drop-Position setzen, dann hochladen + einfügen.
    lastCursorRef.current = caret;
    node.focus();
    node.setSelectionRange(caret, caret);
    for (const file of files) {
      uploadAsset.mutate(file);
    }
  }

  /** Bild-Referenz (```![](url)```) an eine Zeichenposition im Markdown verschieben.
   *  ``sourceIndex`` ist die exakte Start-Position des zu ziehenden Vorkommens (damit
   *  bei mehreren identischen Bildern das richtige verschoben wird). */
  function moveImageSnippet(snippet: string, sourceIndex: number, targetIndex: number) {
    const snippetIndex =
      markdown.slice(sourceIndex, sourceIndex + snippet.length) === snippet ? sourceIndex : markdown.indexOf(snippet);
    if (snippetIndex < 0) {
      return;
    }
    // Innerhalb des Bild-Snippets abgelegt → nichts zu tun.
    if (targetIndex >= snippetIndex && targetIndex <= snippetIndex + snippet.length) {
      return;
    }
    const without = `${markdown.slice(0, snippetIndex)}${markdown.slice(snippetIndex + snippet.length)}`;
    // Beim Verschieben nach hinten die entfernte Länge kompensieren.
    const clampedTarget = Math.max(0, Math.min(without.length, targetIndex));
    const adjusted = snippetIndex < targetIndex ? Math.max(0, clampedTarget - snippet.length) : clampedTarget;
    if (adjusted === snippetIndex) {
      return; // Position unverändert.
    }
    pushUndo();
    updateMarkdown(`${without.slice(0, adjusted)}${snippet}${without.slice(adjusted)}`);
    requestAnimationFrame(() => {
      textareaRef.current?.focus();
      textareaRef.current?.setSelectionRange(adjusted + snippet.length, adjusted + snippet.length);
    });
  }

  /** Pointer-basiertes Ziehen eines Bild-Thumbnails (Maus gedrückt halten + ziehen) —
   *  unabhängig von der nativen HTML5-DnD, die im WebView oft blockiert ist. Der Caret
   *  im Textfeld zeigt live die Ziel-Einfügeposition. */
  function startImagePointerDrag(event: ReactPointerEvent<HTMLImageElement>, snippet: string, src: string, sourceIndex: number) {
    if (event.button !== 0) {
      return;
    }
    event.preventDefault();
    event.stopPropagation();
    const textarea = textareaRef.current;
    if (!textarea) {
      return;
    }
    const ghost = document.createElement("img");
    ghost.src = src;
    ghost.className = "note-image-drag-ghost";
    document.body.appendChild(ghost);
    const place = (x: number, y: number) => {
      ghost.style.left = `${x + 12}px`;
      ghost.style.top = `${y + 12}px`;
    };
    place(event.clientX, event.clientY);
    textarea.focus();
    let lastCaret = -1;
    const onMove = (moveEvent: PointerEvent) => {
      place(moveEvent.clientX, moveEvent.clientY);
      const index = caretIndexFromPoint(textarea, moveEvent.clientX, moveEvent.clientY);
      if (index != null) {
        lastCaret = index;
        textarea.setSelectionRange(index, index);
      }
    };
    const onUp = (upEvent: PointerEvent) => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      ghost.remove();
      document.body.style.cursor = "";
      const index = caretIndexFromPoint(textarea, upEvent.clientX, upEvent.clientY) ?? lastCaret;
      if (index != null && index >= 0) {
        moveImageSnippet(snippet, sourceIndex, index);
      }
    };
    document.body.style.cursor = "grabbing";
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }

  function toggleImagePreview() {
    setImagePreviewHidden((current) => {
      const next = !current;
      try {
        localStorage.setItem(IMAGE_PREVIEW_HIDDEN_KEY, next ? "1" : "0");
      } catch {
        // ignore storage failures
      }
      return next;
    });
  }

  function handleEditorPointerDown() {
    if (aiPreview) {
      setInsertPreview(null);
      return;
    }
    setInlineThreadId("");
    setLocatedThreadId("");
    setHoverThreadId("");
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
      setLocatedThreadId("");
      return;
    }
    setInlineThreadId(threadId);
    setLocatedThreadId("");
    setActiveThreadId(threadId);
    setSelection(null);
    setSelectionPinned(false);
    setAiPreview("");
    setAiInstruction("");
  }

  function locateThreadInEditor(threadId: string, openInline = false) {
    setActiveThreadId(threadId);
    setInlineThreadId(openInline ? threadId : "");
    setLocatedThreadId(openInline ? "" : threadId);
    setSelection(null);
    setSelectionPinned(false);
    setAiPreview("");
    setAiInstruction("");
    const anchor = threadAnchors.find((item) => item.thread.id === threadId);
    if (!anchor) {
      setLocatedThreadId("");
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

  function activateThreadFromHistory(threadId: string) {
    locateThreadInEditor(threadId, false);
  }

  function appendThreadAnswer(thread: NoteAiThread) {
    const text = latestThreadAnswer(thread);
    if (text) {
      insertThreadAnswer(thread, text);
    } else {
      clearInsertPreview();
    }
  }

  function submitFollowUp(thread: NoteAiThread, source: "inline" | "history" = "inline") {
    const message = (followUpDrafts[thread.id] ?? "").trim();
    if (!message) {
      return;
    }
    followUp.mutate({ threadId: thread.id, message, source });
  }

  function stepNoteSearch(direction: -1 | 1) {
    if (!noteSearchRanges.length) {
      return;
    }
    setNoteSearchIndex((current) => (current + direction + noteSearchRanges.length) % noteSearchRanges.length);
  }

  function askWholeNote() {
    if (!activeNoteId || !noteQuestion.trim() || askNote.isPending) {
      return;
    }
    askNote.mutate();
  }

  function setThreadCollapsed(thread: NoteAiThread, collapsed: boolean) {
    updateThreadUi.mutate({ thread, uiState: { collapsed } });
  }

  function setThreadPinned(thread: NoteAiThread, pinned: boolean) {
    updateThreadUi.mutate({ thread, uiState: { pinned } });
  }

  function hideThreadMessage(thread: NoteAiThread, messageId: string) {
    updateThreadUi.mutate({
      thread,
      uiState: {
        hidden_message_ids: Array.from(new Set([...hiddenThreadMessageIds(thread), messageId]))
      }
    });
  }

  function persistLocalThreadRanges() {
    if (!activeNoteId || !threads.length) {
      return;
    }
    for (const thread of threads) {
      const range = localThreadRangesRef.current[thread.id];
      if (!range || threadRangeMatchesUi(thread, range)) {
        continue;
      }
      updateThreadUi.mutate({ thread, uiState: threadRangeUiState(range) });
    }
  }

  function appendThreadMessage(thread: NoteAiThread, message: NoteAiMessage) {
    insertThreadAnswer(thread, message.content);
    clearInsertPreview();
  }

  function previewThreadMessage(thread: NoteAiThread, message: NoteAiMessage) {
    setInsertPreview({ index: insertionPointForThread(thread), content: threadInsertionContent(message.content) });
  }

  // Overlay's window size is app-controlled (fixed, not user-resizable), so its context
  // column uses a fixed literal instead of the shared, user-dragged contextWidth — that
  // width was tuned for the much wider standalone/workspace layouts and could overflow.
  const contextColumnWidth = variant === "overlay" ? "280px" : `minmax(320px, ${contextWidth}px)`;
  const workspaceColumns = `${notesListOpen ? "minmax(230px, 0.32fr)" : "46px"} minmax(360px, 1fr) 6px ${contextOpen ? contextColumnWidth : "46px"}`;

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

  function startSplitResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const container = splitGridRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    let frame: number | null = null;
    let latest = splitRatio;
    const move = (moveEvent: globalThis.PointerEvent) => {
      latest = clampSplitRatio((moveEvent.clientX - rect.left) / rect.width);
      if (frame !== null) window.cancelAnimationFrame(frame);
      frame = window.requestAnimationFrame(() => {
        frame = null;
        setSplitRatio(latest);
      });
    };
    const stop = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", stop);
      saveNumberUiState("editor.splitRatio", latest);
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
    const existingResultRange = localThreadRangesRef.current[thread.id] ?? threadResultRange(thread);
    const existingResultAnchor = existingResultRange ? threadAnchorRange(thread, markdown, existingResultRange) : null;
    const insertsInsideExistingAnchor = Boolean(existingResultAnchor && index >= existingResultAnchor.start && index <= existingResultAnchor.end);
    insertAtRange(index, index, content);
    if (!insertsInsideExistingAnchor) {
      const range = insertedContentRange(index, content);
      rememberThreadResultRange(thread, range.start, range.end, range.text);
    }
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
    let insertText = markdownBlockInsertion(currentMarkdown, start, content);
    // Guarantee a blank line after the inserted quote/citation block so the caret lands
    // on a fresh paragraph — not fused onto the trailing citation token, which is what
    // made the next keystrokes appear "inside" the citation's parentheses.
    if (!currentMarkdown.slice(end).trim()) {
      insertText = `${insertText.replace(/\n+$/, "")}\n\n`;
    }
    const nextMarkdown = `${currentMarkdown.slice(0, start)}${insertText}${currentMarkdown.slice(end)}`;
    // Move the caret past the blank line that follows the block.
    let nextCursor = start + insertText.length;
    while (nextCursor < nextMarkdown.length && nextMarkdown[nextCursor] === "\n") {
      nextCursor += 1;
    }
    const viewportSnapshot = captureEditorViewport(nextCursor);
    pushUndo();
    applyMarkdownChange(nextMarkdown);
    lastCursorRef.current = nextCursor;
    restoreEditorViewport(viewportSnapshot, nextMarkdown.length);

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
      // No second restore here: re-forcing the caret after the async round-trip is what
      // yanked it back into the citation even after the user clicked elsewhere.
      return note.id;
    }

    if (nextTitle !== title) {
      setTitle(nextTitle);
    }
    const saved = await api.updateNote(activeNoteId, { title: nextTitle, markdown: nextMarkdown });
    const note = citations.length ? (await api.appendNote(activeNoteId, { markdown: " ", citations })).note : saved.note;
    setTitle(note.title);
    applyMarkdownChange(note.markdown, { markDirty: false, clearPreview: false });
    setDirtyState(false);
    loadedNoteIdRef.current = note.id;
    queryClient.setQueryData(["note", note.id], { note });
    queryClient.invalidateQueries({ queryKey: ["notes"] });
    queryClient.invalidateQueries({ queryKey: ["note", note.id] });
    persistLocalThreadRanges();
    // No second restore here (see create branch): it re-stole the caret after the save.
    return note.id;
  }

  function threadInsertionContent(answer: string) {
    return `\n\n${answer.trim()}`;
  }

  function insertedContentRange(index: number, content: string) {
    const leading = content.length - content.trimStart().length;
    const trailing = content.trimEnd().length;
    const start = index + leading;
    const end = index + trailing;
    return { start, end, text: content.slice(leading, trailing) };
  }

  function externalInsertRange() {
    const currentMarkdown = markdownRef.current;
    const node = textareaRef.current;
    const fallback = Math.max(0, Math.min(currentMarkdown.length, lastCursorRef.current ?? currentMarkdown.length));
    const start = Math.max(0, Math.min(currentMarkdown.length, node?.selectionStart ?? fallback));
    const end = Math.max(start, Math.min(currentMarkdown.length, node?.selectionEnd ?? start));
    return { start, end };
  }

  function captureEditorViewport(cursor: number): EditorViewportSnapshot {
    const node = textareaRef.current;
    return {
      scrollTop: node?.scrollTop ?? editorScrollTop,
      scrollLeft: node?.scrollLeft ?? editorScrollLeft,
      cursor
    };
  }

  function restoreEditorViewport(snapshot: EditorViewportSnapshot, markdownLength = markdownRef.current.length) {
    // Restore once now and once after the next render (via the layout effect). The old
    // multi-frame loop kept re-forcing the selection for several frames, which stole focus
    // and the caret back from wherever the user had clicked after an insert.
    pendingEditorViewportRestoreRef.current = { snapshot, markdownLength };
    restoreEditorViewportNow(snapshot, markdownLength);
  }

  function restoreEditorViewportNow(snapshot: EditorViewportSnapshot, markdownLength = markdownRef.current.length) {
    const cursor = Math.max(0, Math.min(markdownLength, snapshot.cursor));
    const node = textareaRef.current;
    if (!node) {
      return;
    }
    // Only move the caret when the editor still owns focus. If the insert came from another
    // panel (e.g. the Assistant) or the user has since clicked elsewhere, forcing focus +
    // selection here would drag the caret back into the just-inserted citation.
    const editorHasFocus = document.activeElement === node;
    if (editorHasFocus) {
      node.setSelectionRange(cursor, cursor);
      lastCursorRef.current = cursor;
    }
    node.scrollTop = Math.min(snapshot.scrollTop, Math.max(0, node.scrollHeight - node.clientHeight));
    node.scrollLeft = snapshot.scrollLeft;
    setEditorScrollTop(node.scrollTop);
    setEditorScrollLeft(node.scrollLeft);
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
    const thread = resolveActiveThread();
    const start = selection.end;
    pushUndo();
    updateMarkdown(`${markdown.slice(0, start)}${insertText}${markdown.slice(start)}`);
    lastCursorRef.current = start + insertText.length;
    const range = insertedContentRange(start, insertText);
    rememberThreadResultRange(thread, range.start, range.end, range.text);
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
    const result = toggleWrap(markdown, start, end, before, after);
    pushUndo();
    updateMarkdown(result.next);
    requestAnimationFrame(() => {
      node?.focus();
      node?.setSelectionRange(result.selStart, result.selEnd);
    });
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
    const result = continueMarkdownLineAt(markdown, node.selectionStart, node.selectionEnd);
    if (!result) {
      return false;
    }
    pushUndo();
    updateMarkdown(result.next);
    requestAnimationFrame(() => {
      node.focus();
      node.setSelectionRange(result.selStart, result.selEnd);
    });
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
      applyMarkdownChange(previous);
      return;
    }
    if (activeNoteId) {
      restoreVersion.mutate();
    }
  }

  function openCitation(citation: NoteCitation, ref?: CitationMarkdownRef | null) {
    // Ref nur übernehmen, wenn seine Offsets wirklich auf den Zitat-Link im Markdown
    // zeigen — sonst markiert der Edit-Modus später einen falschen Bereich.
    let safeRef = ref ?? null;
    if (safeRef && !markdown.slice(safeRef.start, safeRef.end).includes(`sciencekg://citation/${citation.id}`)) {
      const anchor = safeRef.start;
      const candidates = citationRefs.filter((item) => item.id === citation.id);
      safeRef = candidates.length
        ? [...candidates].sort((left, right) => Math.abs(left.start - anchor) - Math.abs(right.start - anchor))[0]
        : null;
    }
    setSelectedCitation(citation);
    setSelectedCitationRef(safeRef);
    setContextOpen(true);
    setHistoryOpen(false);
    setCitationListOpen(true);
    setNotePdfOpen(true);
    onCitationOpen?.(citation);
  }

  function clearCitation() {
    setSelectedCitation(null);
    setSelectedCitationRef(null);
    onCitationOpen?.(null);
  }

  useEffect(() => {
    if (!requestedCitationId || handledRequestedCitationIdRef.current === requestedCitationId) {
      return;
    }
    const citation = citations.find((item) => item.id === requestedCitationId);
    if (citation) {
      handledRequestedCitationIdRef.current = requestedCitationId;
      openCitation(citation);
    }
  }, [citations, requestedCitationId]);

  useEffect(() => {
    if (!selectedCitation) {
      return;
    }
    const freshCitation = citations.find((item) => item.id === selectedCitation.id);
    if (!freshCitation) {
      setSelectedCitation(null);
      setSelectedCitationRef(null);
      onCitationOpen?.(null);
    } else if (freshCitation !== selectedCitation) {
      setSelectedCitation(freshCitation);
    }
  }, [citations, onCitationOpen, selectedCitation]);

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
    ...threadAnchors.flatMap((anchor) => {
      if (!threadAnchorsVisible) {
        return [];
      }
      const isActive = anchor.thread.id === inlineThreadId || anchor.thread.id === locatedThreadId;
      const isHovered = anchor.thread.id === hoverThreadId;
      if (!isActive && !isHovered) {
        return [];
      }
      return threadHighlightSegments(anchor).map((range) => ({
        start: range.start,
        end: range.end,
        className: isActive
          ? "textarea-highlight-range--thread-anchor textarea-highlight-range--thread-anchor-active"
          : "textarea-highlight-range--thread-anchor",
        style: evidenceColorVars(threadAnchorMeta.get(anchor.thread.id)?.colorIndex ?? 0)
      }));
    }),
    ...activeCitationRefs.map((ref) => ({
      start: ref.start,
      end: ref.end,
      className: "textarea-highlight-range--citation-active",
      // Gleiche Farbfunktion wie die PDF-Marker (paper_id-Hash), damit Notiz- und
      // PDF-Highlight einer Quelle identisch gefärbt sind.
      style: colorVarsForPaperId(selectedCitation?.paper_id, selectedCitationColorIndex)
    })),
    ...noteSearchRanges.map((range, index) => ({
      start: range.start,
      end: range.end,
      className: index === noteSearchIndex ? "textarea-highlight-range--note-search textarea-highlight-range--note-search-active" : "textarea-highlight-range--note-search"
    }))
  ];
  const threadAnchorInsertions = (threadAnchorsVisible ? threadAnchors : []).map((anchor) => {
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
          onClose={() => {
            setInlineThreadId("");
            setLocatedThreadId("");
          }}
          onHoverChange={(hovered) => setHoverThreadId((current) => (hovered ? anchor.thread.id : current === anchor.thread.id ? "" : current))}
          onDraftChange={(value) => setFollowUpDrafts((current) => ({ ...current, [anchor.thread.id]: value }))}
          onFollowUp={() => submitFollowUp(anchor.thread)}
          onDelete={() => deleteThread.mutate(anchor.thread.id)}
          onInsertMessage={(message) => appendThreadMessage(anchor.thread, message)}
          onPreviewMessage={(message) => previewThreadMessage(anchor.thread, message)}
          onPreviewClear={clearInsertPreview}
          onHideMessage={(messageId) => hideThreadMessage(anchor.thread, messageId)}
        />
      )
    };
  });
  // Bilder auch im Edit-Modus zeigen: pro ![alt](url) ein verankertes Thumbnail
  // (nimmt im Textfluss keine Breite ein — gleiche Technik wie die N-Marker).
  // Per Drag lässt sich die Bild-Referenz an eine andere Stelle ziehen.
  const imageInsertions = useMemo(() => {
    const insertions: { index: number; className: string; content: ReactNode }[] = [];
    if (imagePreviewHidden) {
      return insertions;
    }
    const pattern = /!\[[^\]]*\]\(([^)\s]+)\)/g;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(markdown)) !== null) {
      const snippet = match[0];
      const src = match[1];
      const sourceIndex = match.index;
      insertions.push({
        index: sourceIndex + snippet.length,
        className: "textarea-image-insertion",
        content: (
          <span className="textarea-image-wrap" key={`img-${sourceIndex}`}>
            <img
              src={src}
              alt=""
              draggable={false}
              title="Gedrückt halten und ziehen, um das Bild an eine andere Stelle zu verschieben"
              onPointerDown={(event) => startImagePointerDrag(event, snippet, src, sourceIndex)}
            />
          </span>
        )
      });
    }
    return insertions;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [markdown, imagePreviewHidden]);
  const editorGhostInsertions = [
    ...(insertPreview ? [{ ...insertPreview, className: "textarea-ghost-insertion--ai" }] : []),
    ...threadAnchorInsertions,
    ...imageInsertions
  ];
  const sourcePanelRows = citationListOpen ? undefined : "auto minmax(0, 1fr)";
  const editorBottomStyle = {
    "--note-editor-extra-bottom": selection ? `max(68vh, ${Math.max(aiPopoverBottomPadding, 96)}px)` : "68vh"
  } as CSSProperties;
  const showEditor = editorMode === "edit" || editorMode === "split";
  const showPreview = editorMode === "preview" || editorMode === "split";
  const pageClassName =
    variant === "overlay"
      ? "page notes-page notes-page--overlay"
      : embedded
        ? `page notes-page notes-page--embedded ${historyOpen ? "notes-page--embedded-history" : ""}`
        : "page notes-page";

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
      showHistory: () => {
        setContextOpen(true);
        setHistoryOpen(true);
      },
      showSources: () => {
        setContextOpen(true);
        setHistoryOpen(false);
      },
      activateThread: activateThreadFromHistory,
      locateThread: (threadId: string) => locateThreadInEditor(threadId, false),
      setActiveThread: setActiveThreadId,
      toggleThreadCollapsed: (threadId: string) => {
        const thread = findThread(threadId);
        if (thread) {
          setThreadCollapsed(thread, !threadCollapsed(thread));
        }
      },
      setThreadPinned: (threadId: string, pinned: boolean) => {
        const thread = findThread(threadId);
        if (thread) {
          setThreadPinned(thread, pinned);
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
      deleteAllThreads: () => deleteAllThreads.mutate(),
      deleteNote: (noteId: string) => deleteNoteById.mutate(noteId)
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
      threads: displayedThreads,
      activeThreadId,
      historyOpen,
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
    displayedThreads,
    activeThreadId,
    historyOpen,
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

      <div
        className={`notes-workspace ${notesListOpen ? "" : "notes-workspace--left-collapsed"} ${contextOpen ? "" : "notes-workspace--right-collapsed"}`}
        style={{ gridTemplateColumns: workspaceColumns, "--overlay-grid-columns": workspaceColumns } as CSSProperties}
      >
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
                <button
                  className={`icon-button ${threadAnchorsVisible ? "active" : ""}`}
                  type="button"
                  aria-label={threadAnchorsVisible ? "KI-Marker (N…) ausblenden" : "KI-Marker (N…) einblenden"}
                  aria-pressed={threadAnchorsVisible}
                  title={threadAnchorsVisible ? "KI-Marker (N…) ausblenden" : "KI-Marker (N…) einblenden"}
                  onClick={() => setThreadAnchorsVisible((current) => !current)}
                >
                  <MessageSquareText size={17} />
                </button>
                <select aria-label="Textfarbe" onChange={(event) => event.target.value && applyWrap(`<span style="color:${event.target.value}">`, "</span>")} defaultValue="">
                  <option value="">Farbe</option>
                  <option value="#2563eb">Blau</option>
                  <option value="#16865a">Gruen</option>
                  <option value="#a76500">Amber</option>
                  <option value="#ba3434">Rot</option>
                </select>
                <span className="table-builder-wrap">
                  <button
                    className={`icon-button ${tableMenuOpen ? "active" : ""}`}
                    type="button"
                    aria-label="Tabelle einfügen"
                    aria-expanded={tableMenuOpen}
                    onClick={() => setTableMenuOpen((current) => !current)}
                  >
                    <Table2 size={17} />
                  </button>
                  {tableMenuOpen ? (
                    <div className="table-builder-popover" onMouseLeave={() => setTableHover({ cols: 0, rows: 0 })}>
                      <div className="table-builder-grid" role="grid" aria-label="Tabellengröße wählen">
                        {Array.from({ length: TABLE_PICKER_ROWS }, (_, rowIndex) =>
                          Array.from({ length: TABLE_PICKER_COLS }, (_, colIndex) => {
                            const isActive = colIndex < tableHover.cols && rowIndex < tableHover.rows;
                            return (
                              <button
                                key={`${rowIndex}-${colIndex}`}
                                type="button"
                                className={`table-builder-cell ${isActive ? "table-builder-cell--active" : ""}`}
                                aria-label={`${colIndex + 1} Spalten, ${rowIndex + 1} Zeilen`}
                                onMouseEnter={() => setTableHover({ cols: colIndex + 1, rows: rowIndex + 1 })}
                                onFocus={() => setTableHover({ cols: colIndex + 1, rows: rowIndex + 1 })}
                                onClick={() => {
                                  insertAtSelection(buildMarkdownTable(colIndex + 1, rowIndex + 1));
                                  setTableMenuOpen(false);
                                }}
                              />
                            );
                          })
                        )}
                      </div>
                      <span className="table-builder-label">
                        {tableHover.cols > 0 ? `${tableHover.cols} × ${tableHover.rows}` : "Größe wählen"}
                      </span>
                    </div>
                  ) : null}
                </span>
                <button
                  className="icon-button"
                  type="button"
                  aria-label="Durchgehende Trennlinie einfügen"
                  title="Durchgehende Trennlinie einfügen"
                  onClick={() => insertAtSelection(dividerSnippetForTextarea("solid", textareaRef.current))}
                >
                  <SeparatorHorizontal size={17} />
                </button>
                <button
                  className="icon-button"
                  type="button"
                  aria-label="Gestrichelte Trennlinie einfügen"
                  title="Gestrichelte Trennlinie einfügen"
                  onClick={() => insertAtSelection(dividerSnippetForTextarea("dashed", textareaRef.current))}
                >
                  <Minus size={17} />
                </button>
                <button className="icon-button" type="button" aria-label="Bild einfuegen" onClick={() => imageInputRef.current?.click()} disabled={!activeNoteId}>
                  <ImagePlus size={17} />
                </button>
                <button
                  className={`icon-button ${imagePreviewHidden ? "icon-button--active" : ""}`}
                  type="button"
                  aria-pressed={imagePreviewHidden}
                  aria-label={imagePreviewHidden ? "Bildvorschau im Editor einblenden" : "Bildvorschau im Editor ausblenden"}
                  title={imagePreviewHidden ? "Bildvorschau im Editor einblenden" : "Bildvorschau im Editor ausblenden (verdeckt sonst den Text)"}
                  onClick={toggleImagePreview}
                >
                  <ImageOff size={17} />
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
                <label className="note-search-field">
                  <Search size={15} />
                  <input
                    value={noteSearchQuery}
                    onChange={(event) => {
                      setNoteSearchQuery(event.target.value);
                      setNoteSearchIndex(0);
                    }}
                    placeholder="Notiz suchen"
                  />
                  <span>{noteSearchQuery.trim() ? `${noteSearchRanges.length ? noteSearchIndex + 1 : 0}/${noteSearchRanges.length}` : ""}</span>
                  <button className="icon-button icon-button--compact" type="button" aria-label="Vorheriger Suchtreffer" onClick={() => stepNoteSearch(-1)} disabled={!noteSearchRanges.length}>
                    <ChevronUp size={14} />
                  </button>
                  <button className="icon-button icon-button--compact" type="button" aria-label="Naechster Suchtreffer" onClick={() => stepNoteSearch(1)} disabled={!noteSearchRanges.length}>
                    <ChevronDown size={14} />
                  </button>
                </label>
                <label className="note-question-field">
                  <Sparkles size={15} />
                  <input
                    value={noteQuestion}
                    onChange={(event) => setNoteQuestion(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") {
                        event.preventDefault();
                        askWholeNote();
                      }
                    }}
                    placeholder="Ganze Notiz fragen"
                  />
                  <button
                    className="button button-compact"
                    type="button"
                    aria-label="Ganze Notiz"
                    onClick={askWholeNote}
                    disabled={!noteQuestion.trim() || askNote.isPending || !activeNoteId}
                  >
                    Notiz
                  </button>
                </label>
              </div>
              {askNote.isError ? <div className="inline-error">Notizfrage fehlgeschlagen: {formatError(askNote.error)}</div> : null}

              <div
                className={`markdown-editor-grid markdown-editor-grid--${editorMode}`}
                ref={splitGridRef}
                style={editorMode === "split" ? { gridTemplateColumns: `minmax(0, ${splitRatio}fr) 6px minmax(0, ${1 - splitRatio}fr)`, gap: 0 } : undefined}
              >
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
                      interactive={threadAnchorInsertions.length > 0 || imageInsertions.length > 0}
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
                      onPaste={handleEditorPaste}
                      onDragOver={handleEditorDragOver}
                      onDrop={handleEditorDrop}
                      spellCheck={spellcheckEnabled}
                      placeholder="Markdown schreiben"
                    />
                    {activeEditorCitation && activeEditorCitationRow && !selection ? (
                      <button
                        className="editor-citation-chip"
                        type="button"
                        style={colorVarsForPaperId(activeEditorCitation.paper_id, activeEditorCitationColorIndex)}
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => openCitation(activeEditorCitation, activeEditorCitationRef)}
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
                          <>
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
                            <div className="selection-ai-quick-row">
                              <Languages size={14} />
                              <select
                                aria-label="Zielsprache"
                                value={translateLanguage}
                                onChange={(event) => setTranslateLanguage(event.target.value)}
                              >
                                {TRANSLATE_LANGUAGES.map((language) => (
                                  <option key={language} value={language}>
                                    {language}
                                  </option>
                                ))}
                              </select>
                              <button
                                className="button button-compact"
                                type="button"
                                disabled={aiEdit.isPending}
                                onClick={() => {
                                  pinSelectionForQuestion();
                                  aiEdit.mutate(
                                    `Übersetze den markierten Text nach ${translateLanguage}. Behalte Markdown-Formatierung und alle Zitatlinks ([Zx - ...](sciencekg://citation/...)) unverändert bei. Gib nur die Übersetzung aus.`
                                  );
                                }}
                              >
                                {aiEdit.isPending ? "Übersetzt…" : "Übersetzen"}
                              </button>
                              <button
                                className="button button-compact"
                                type="button"
                                disabled={aiEdit.isPending}
                                title="Formatiert die Auswahl als sauberes Markdown, ohne den Inhalt zu ändern"
                                onClick={() => {
                                  pinSelectionForQuestion();
                                  aiEdit.mutate(FORMAT_AS_MARKDOWN_INSTRUCTION);
                                }}
                              >
                                Als Markdown formatieren
                              </button>
                            </div>
                          </>
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
                {editorMode === "split" ? (
                  <div
                    className="split-handle markdown-editor-split-handle"
                    role="separator"
                    aria-orientation="vertical"
                    aria-label="Aufteilung Editor/Vorschau anpassen"
                    onPointerDown={startSplitResize}
                    onDoubleClick={() => {
                      setSplitRatio(0.5);
                      saveNumberUiState("editor.splitRatio", 0.5);
                    }}
                  />
                ) : null}
                {showPreview ? (
                  <MarkdownPreview
                    previewRef={previewRef}
                    markdown={markdown}
                    citations={citations}
                    activeCitationId={selectedCitation?.id ?? ""}
                    activeCitationRef={selectedCitationRef}
                    onCitationClick={openCitation}
                    searchQuery={noteSearchQuery}
                    editable={editorMode === "preview"}
                    onBlockChange={editorMode === "preview" ? updatePreviewBlock : undefined}
                    onScroll={(event) => {
                      previewScrollRef.current = { top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft };
                    }}
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
                threads={displayedThreads}
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
                  threads={displayedThreads}
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
                  onPinChange={setThreadPinned}
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
                threads={displayedThreads}
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
                      {citationRows.map(({ citation, badge, label, title, evidence, colorIndex }) => (
                        <button
                          className={`list-row note-citation-row ${selectedCitation?.id === citation.id ? "note-citation-row--active" : ""}`}
                          type="button"
                          key={citation.id}
                          ref={(node) => {
                            citationRowRefs.current[citation.id] = node;
                          }}
                          onClick={() => openCitation(citation)}
                          style={colorVarsForPaperId(citation.paper_id, colorIndex)}
                          aria-label={`Quelle ${badge} öffnen`}
                          aria-pressed={selectedCitation?.id === citation.id}
                        >
                          <span className="note-citation-row__title">
                            {citation.paper_id.startsWith("grey::") ? (
                              <span className="citation-badge citation-badge--grey"><Globe size={10} /></span>
                            ) : (
                              <span className="citation-badge">{badge}</span>
                            )}
                            <strong>{title}</strong>
                          </span>
                          <span>{evidence || label}</span>
                          <small>
                            {citation.paper_id.startsWith("grey::") ? (citation.pdf_excerpt || citation.paper_id) : citation.paper_id}
                            {label !== badge && !citation.paper_id.startsWith("grey::") ? ` - ${label}` : ""}
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

export type MarkdownBlock = {
  raw: string;
  start: number;
  end: number;
};

