import { KeyboardEvent, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import { createPortal } from "react-dom";
import type { PointerEvent as ReactPointerEvent } from "react";
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
  Italic,
  Languages,
  Link,
  List,
  NotebookPen,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  Quote,
  Redo2,
  Search,
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

type ThreadTextRange = {
  start: number;
  end: number;
};

type ThreadTextDiff = {
  start: number;
  beforeEnd: number;
  afterEnd: number;
  delta: number;
};

const THREAD_RANGE_TEXT_LIMIT = 16000;

const TABLE_PICKER_COLS = 6;
const TABLE_PICKER_ROWS = 6;
const TRANSLATE_LANGUAGES = ["Deutsch", "Englisch", "Französisch", "Spanisch", "Italienisch", "Portugiesisch", "Niederländisch", "Polnisch", "Chinesisch", "Japanisch"];

export function buildMarkdownTable(cols: number, rows: number) {
  const safeCols = Math.max(1, cols);
  const safeRows = Math.max(1, rows);
  const header = `| ${Array.from({ length: safeCols }, (_, index) => `Spalte ${index + 1}`).join(" | ")} |`;
  const separator = `|${Array.from({ length: safeCols }, () => " --- ").join("|")}|`;
  const bodyRow = `| ${Array.from({ length: safeCols }, () => "    ").join(" | ")} |`;
  return `\n\n${header}\n${separator}\n${Array.from({ length: safeRows }, () => bodyRow).join("\n")}\n`;
}

type ThreadStoredRange = {
  start: number;
  end: number;
  text: string;
  manualRanges?: ThreadTextRange[];
};

type ThreadAnchorRange = ThreadStoredRange & {
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
  const [noteQuestion, setNoteQuestion] = useState("");
  const [noteSearchQuery, setNoteSearchQuery] = useState("");
  const [noteSearchIndex, setNoteSearchIndex] = useState(0);
  const [tableMenuOpen, setTableMenuOpen] = useState(false);
  const [tableHover, setTableHover] = useState({ cols: 0, rows: 0 });
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
  const [notesListOpen, setNotesListOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.notesListOpen`, true));
  const [contextOpen, setContextOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.contextOpen`, true));
  const [notePdfOpen, setNotePdfOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.notePdfOpen`, true));
  const [citationListOpen, setCitationListOpen] = useState(() => loadBooleanUiState(`${scopedProjectId}.citationListOpen`, true));
  const [spellcheckEnabled, setSpellcheckEnabled] = useState(() => loadBooleanUiState("spellcheckEnabled", true));
  const [contextWidth, setContextWidth] = useState(() => loadNumberUiState(`${scopedProjectId}.contextWidth`, 430));
  const [activeThreadId, setActiveThreadId] = useState("");
  const [locatedThreadId, setLocatedThreadId] = useState("");
  const [threadMetaById, setThreadMetaById] = useState<Record<string, ThreadAnchorMeta>>({});
  const [localThreadRanges, setLocalThreadRanges] = useState<Record<string, ThreadStoredRange>>({});
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
  const handledRequestedCitationIdRef = useRef("");
  const loadedNoteIdRef = useRef("");
  const previewRef = useRef<HTMLElement | null>(null);
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
        const evidence = citation.pdf_excerpt || citation.reference_text || citation.kind || "";
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
    setInsertPreview(null);
  }, [activeNoteId, activeThreadId, editorMode, historyOpen]);

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

  function replaceSelection(value: string) {
    if (!selection) {
      return;
    }
    // AI rewrites tend to shorten quotes and silently drop their
    // [Zx - ...](sciencekg://citation/...) anchors — the note then loses its source
    // trail. Re-attach every citation link of the replaced range that the rewrite
    // did not carry over.
    const replaced = withPreservedCitationLinks(markdown.slice(selection.start, selection.end), value);
    const thread = activeThreadId ? threads.find((item) => item.id === activeThreadId) : null;
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
    const insertText = markdownBlockInsertion(currentMarkdown, start, content);
    const nextMarkdown = `${currentMarkdown.slice(0, start)}${insertText}${currentMarkdown.slice(end)}`;
    const nextCursor = start + insertText.length;
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
      restoreEditorViewport(viewportSnapshot, note.markdown.length);
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
    restoreEditorViewport(viewportSnapshot, note.markdown.length);
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
    pendingEditorViewportRestoreRef.current = { snapshot, markdownLength };
    restoreEditorViewportNow(snapshot, markdownLength);
    requestAnimationFrame(() => {
      restoreEditorViewportNow(snapshot, markdownLength);
      requestAnimationFrame(() => restoreEditorViewportNow(snapshot, markdownLength));
    });
  }

  function restoreEditorViewportNow(snapshot: EditorViewportSnapshot, markdownLength = markdownRef.current.length) {
    const cursor = Math.max(0, Math.min(markdownLength, snapshot.cursor));
    const node = textareaRef.current;
    if (!node) {
      return;
    }
    node.focus({ preventScroll: true });
    node.setSelectionRange(cursor, cursor);
    node.scrollTop = Math.min(snapshot.scrollTop, Math.max(0, node.scrollHeight - node.clientHeight));
    node.scrollLeft = snapshot.scrollLeft;
    setEditorScrollTop(node.scrollTop);
    setEditorScrollLeft(node.scrollLeft);
    lastCursorRef.current = cursor;
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
    const thread = activeThreadId ? threads.find((item) => item.id === activeThreadId) : null;
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
      applyMarkdownChange(previous);
      return;
    }
    if (activeNoteId) {
      restoreVersion.mutate();
    }
  }

  function openCitation(citation: NoteCitation, ref?: CitationMarkdownRef | null) {
    setSelectedCitation(citation);
    setSelectedCitationRef(ref ?? null);
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
      style: evidenceColorVars(selectedCitationColorIndex)
    })),
    ...noteSearchRanges.map((range, index) => ({
      start: range.start,
      end: range.end,
      className: index === noteSearchIndex ? "textarea-highlight-range--note-search textarea-highlight-range--note-search-active" : "textarea-highlight-range--note-search"
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
  const pageClassName = embedded ? `page notes-page notes-page--embedded ${historyOpen ? "notes-page--embedded-history" : ""}` : "page notes-page";

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
                        style={evidenceColorVars(activeEditorCitationColorIndex)}
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
                {showPreview ? (
                  <MarkdownPreview
                    previewRef={editorMode === "preview" ? previewRef : undefined}
                    markdown={markdown}
                    citations={citations}
                    activeCitationId={selectedCitation?.id ?? ""}
                    activeCitationRef={selectedCitationRef}
                    onCitationClick={openCitation}
                    searchQuery={noteSearchQuery}
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
                          style={evidenceColorVars(colorIndex)}
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
  onHoverChange,
  onDraftChange,
  onFollowUp,
  onDelete,
  onInsertMessage,
  onPreviewMessage,
  onPreviewClear,
  onHideMessage
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
  onHoverChange: (hovered: boolean) => void;
  onDraftChange: (value: string) => void;
  onFollowUp: () => void;
  onDelete: () => void;
  onInsertMessage: (message: NoteAiMessage) => void;
  onPreviewMessage: (message: NoteAiMessage) => void;
  onPreviewClear: () => void;
  onHideMessage: (messageId: string) => void;
}) {
  const messages = threadDisplayMessages(thread);
  const context = shortThreadContext(thread.anchor_quote || thread.selected_text);
  const wrapRef = useRef<HTMLSpanElement | null>(null);
  const buttonRef = useRef<HTMLButtonElement | null>(null);
  const [buttonPlacement, setButtonPlacement] = useState<"above" | "below">("above");
  const [portalPosition, setPortalPosition] = useState<{ left: number; top: number; width: number } | null>(null);

  function setHoveredState(value: boolean) {
    onHoverChange(value);
  }

  useEffect(() => {
    const updatePosition = () => {
      const button = buttonRef.current;
      const wrap = wrapRef.current;
      if (!button || !wrap) {
        return;
      }
      const rect = button.getBoundingClientRect();
      const wrapRect = wrap.getBoundingClientRect();
      const editor = wrap.closest(".markdown-editor-wrap");
      const editorRect = editor?.getBoundingClientRect();
      const nextButtonPlacement = editorRect && wrapRect.top < editorRect.top + 30 ? "below" : "above";
      setButtonPlacement((current) => (current === nextButtonPlacement ? current : nextButtonPlacement));

      const viewportWidth = window.innerWidth || 1024;
      const viewportHeight = window.innerHeight || 768;
      const preferredWidth = open ? 430 : 320;
      const preferredHeight = open ? 420 : 130;
      const width = Math.min(preferredWidth, Math.max(240, viewportWidth - 24));
      const expectedHeight = Math.min(preferredHeight, Math.max(120, viewportHeight - 24));
      const maxLeft = Math.max(12, viewportWidth - width - 12);
      const preferredLeft = placement === "left" ? rect.right - width : rect.left;
      const left = Math.min(Math.max(12, preferredLeft), maxLeft);
      const belowTop = rect.bottom + 8;
      const top = belowTop + expectedHeight > viewportHeight ? Math.max(12, rect.top - expectedHeight - 8) : belowTop;
      setPortalPosition({ left, top, width });
    };
    updatePosition();
    window.addEventListener("resize", updatePosition);
    window.addEventListener("scroll", updatePosition, true);
    return () => {
      window.removeEventListener("resize", updatePosition);
      window.removeEventListener("scroll", updatePosition, true);
    };
  }, [open, placement]);

  const portalTarget = typeof document === "undefined" ? null : document.body;
  const popover =
    portalTarget && open && portalPosition
      ? createPortal(
          <span
            className="thread-anchor-popover thread-anchor-popover--portal"
            role="dialog"
            aria-label={`KI-Notiz ${label}`}
            style={{ ...style, left: portalPosition.left, top: portalPosition.top, width: portalPosition.width }}
            onPointerDown={(event) => event.stopPropagation()}
          >
            <span className="thread-anchor-popover__header">
              <span>
                <strong>{label}</strong>
                <small>{context || "Markierter Bereich"}</small>
              </span>
              <span className="thread-anchor-popover__actions">
                <button className="icon-button" type="button" aria-label="KI-Notiz einklappen" onMouseDown={(event) => event.preventDefault()} onClick={onClose}>
                  <ChevronDown size={16} />
                </button>
                <button className="icon-button" type="button" aria-label="KI-Notiz löschen" disabled={deleting} onMouseDown={(event) => event.preventDefault()} onClick={onDelete}>
                  <Trash2 size={16} />
                </button>
              </span>
            </span>
            <span className="thread-anchor-messages">
              {messages.map((message) => (
                <span className={`thread-anchor-message thread-anchor-message--${message.role === "assistant" ? "assistant" : "user"}`} key={message.id}>
                  <span className="thread-anchor-message__topline">
                    <small>{message.role === "assistant" ? "Antwort" : "Frage"}</small>
                    {message.role === "assistant" ? (
                      <span className="thread-anchor-message__actions">
                        <button
                          className="button button-compact"
                          type="button"
                          onMouseDown={(event) => event.preventDefault()}
                          onClick={() => {
                            onInsertMessage(message);
                            onPreviewClear();
                          }}
                          onMouseEnter={() => onPreviewMessage(message)}
                          onMouseOut={onPreviewClear}
                          onMouseLeave={onPreviewClear}
                          onPointerEnter={() => onPreviewMessage(message)}
                          onPointerOut={onPreviewClear}
                          onPointerLeave={onPreviewClear}
                          onFocus={() => onPreviewMessage(message)}
                          onBlur={onPreviewClear}
                        >
                          Einfügen
                        </button>
                        <button className="icon-button icon-button--compact" type="button" aria-label="KI-Antwort ausblenden" onMouseDown={(event) => event.preventDefault()} onClick={() => onHideMessage(message.id)}>
                          <Trash2 size={15} />
                        </button>
                      </span>
                    ) : null}
                  </span>
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
          </span>,
          portalTarget
        )
      : null;
  return (
    <span
      ref={wrapRef}
      className="textarea-thread-anchor-wrap"
      style={style}
      onPointerEnter={() => setHoveredState(true)}
      onPointerLeave={() => setHoveredState(false)}
    >
      <button
        ref={buttonRef}
        className={`textarea-thread-anchor-button textarea-thread-anchor-button--${buttonPlacement} ${active ? "textarea-thread-anchor-button--active" : ""}`}
        type="button"
        aria-label={`KI-Notiz ${label} öffnen`}
        aria-expanded={open}
        onMouseDown={(event) => event.preventDefault()}
        onFocus={() => setHoveredState(true)}
        onBlur={() => setHoveredState(false)}
        onClick={(event) => {
          event.stopPropagation();
          onToggle();
        }}
      >
        {label}
      </button>
      {false && open ? (
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
                <span className="thread-anchor-message__topline">
                  <small>{message.role === "assistant" ? "Antwort" : "Frage"}</small>
                  {message.role === "assistant" ? (
                    <span className="thread-anchor-message__actions">
                      <button
                        className="button button-compact"
                        type="button"
                        onMouseDown={(event) => event.preventDefault()}
                        onClick={() => {
                          onInsertMessage(message);
                          onPreviewClear();
                        }}
                        onMouseEnter={() => onPreviewMessage(message)}
                        onMouseOut={onPreviewClear}
                        onMouseLeave={onPreviewClear}
                        onPointerEnter={() => onPreviewMessage(message)}
                        onPointerOut={onPreviewClear}
                        onPointerLeave={onPreviewClear}
                        onFocus={() => onPreviewMessage(message)}
                        onBlur={onPreviewClear}
                      >
                        Einfügen
                      </button>
                      <button className="icon-button icon-button--compact" type="button" aria-label="KI-Antwort ausblenden" onMouseDown={(event) => event.preventDefault()} onClick={() => onHideMessage(message.id)}>
                        <Trash2 size={15} />
                      </button>
                    </span>
                  ) : null}
                </span>
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
      {popover}
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
  onPinChange,
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
  onPinChange: (thread: NoteAiThread, pinned: boolean) => void;
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
        const pinned = threadPinned(thread);
        return (
          <article
            className={`note-thread-row ai-thread-card ${meta ? "ai-thread-card--anchored" : ""} ${collapsed ? "ai-thread-card--compact" : ""} ${activeThreadId === thread.id ? "ai-thread-card--active" : ""} ${pinned ? "ai-thread-card--pinned" : ""}`}
            key={thread.id}
            style={{ ...(meta ? evidenceColorVars(meta.colorIndex) : {}), ...threadSizeStyle(thread) }}
          >
            <div className="ai-thread-topline">
              <button className="ai-thread-header" type="button" onClick={() => onLocateThread(thread.id)}>
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
                  className={`icon-button icon-button--compact ${pinned ? "icon-button--active" : ""}`}
                  type="button"
                  aria-label={pinned ? "KI-Notiz loesen" : "KI-Notiz anpinnen"}
                  onClick={() => onPinChange(thread, !pinned)}
                >
                  <Pin size={15} />
                </button>
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
                  onClick={() => {
                    onInsert(thread);
                    onPreviewClear();
                  }}
                  onMouseEnter={() => onPreviewInsert(thread)}
                  onMouseOver={() => onPreviewInsert(thread)}
                  onMouseOut={onPreviewClear}
                  onMouseLeave={onPreviewClear}
                  onPointerEnter={() => onPreviewInsert(thread)}
                  onPointerOver={() => onPreviewInsert(thread)}
                  onPointerOut={onPreviewClear}
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
                            onClick={() => {
                              onInsertMessage(thread, message);
                              onPreviewClear();
                            }}
                            onMouseEnter={() => onPreviewMessage(thread, message)}
                            onMouseOver={() => onPreviewMessage(thread, message)}
                            onMouseOut={onPreviewClear}
                            onMouseLeave={onPreviewClear}
                            onPointerEnter={() => onPreviewMessage(thread, message)}
                            onPointerOver={() => onPreviewMessage(thread, message)}
                            onPointerOut={onPreviewClear}
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
  activeCitationRef,
  onCitationClick,
  searchQuery = "",
  editable = false,
  onBlockChange
}: {
  previewRef?: RefObject<HTMLElement>;
  markdown: string;
  citations: NoteCitation[];
  activeCitationId?: string;
  activeCitationRef?: CitationMarkdownRef | null;
  onCitationClick: (citation: NoteCitation, ref?: CitationMarkdownRef | null) => void;
  searchQuery?: string;
  editable?: boolean;
  onBlockChange?: (blockIndex: number, nextRaw: string, expectedRaw?: string) => void;
}) {
  const citationById = useMemo(() => new Map(citations.map((citation) => [citation.id, citation])), [citations]);
  const citationColorById = useMemo(() => new Map(citations.map((citation, index) => [citation.id, citationColorIndex(citation, index)])), [citations]);
  const blocks = useMemo(() => splitMarkdownBlocks(markdown), [markdown]);
  return (
    <article ref={previewRef} className={`markdown-preview ${editable ? "markdown-preview--editable" : ""}`}>
      {blocks.map((block, index) => {
        const rendered = renderBlock(block.raw, `${index}`, citationById, citationColorById, onCitationClick, activeCitationId ?? "", searchQuery, block.start, activeCitationRef ?? null);
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

function renderBlock(
  block: string,
  key: string,
  citations: Map<string, NoteCitation>,
  citationColorById: Map<string, number>,
  onCitationClick: (citation: NoteCitation, ref?: CitationMarkdownRef | null) => void,
  activeCitationId = "",
  searchQuery = "",
  blockStart = 0,
  activeCitationRef: CitationMarkdownRef | null = null
) {
  const trimmed = block.trim();
  if (!trimmed) {
    return null;
  }
  const trimOffset = Math.max(0, block.indexOf(trimmed));
  if (/^!\[[^\]]*\]\([^)]+\)$/.test(trimmed)) {
    const match = /^!\[([^\]]*)\]\(([^)]+)\)$/.exec(trimmed);
    return <img key={key} className="markdown-preview-image" alt={match?.[1] ?? ""} src={match?.[2]} />;
  }
  if (trimmed.startsWith("# ")) {
    return <h1 key={key}>{renderInline(trimmed.slice(2), citations, citationColorById, onCitationClick, activeCitationId, searchQuery, blockStart + trimOffset + 2, activeCitationRef)}</h1>;
  }
  if (trimmed.startsWith("## ")) {
    return <h2 key={key}>{renderInline(trimmed.slice(3), citations, citationColorById, onCitationClick, activeCitationId, searchQuery, blockStart + trimOffset + 3, activeCitationRef)}</h2>;
  }
  if (trimmed.startsWith(">")) {
    return <blockquote key={key}>{renderInline(trimmed.replace(/^>\s?/gm, ""), citations, citationColorById, onCitationClick, activeCitationId, searchQuery, null, activeCitationRef)}</blockquote>;
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
                return <Tag key={`${key}-${rowIndex}-${cellIndex}`}>{renderInline(cell.trim(), citations, citationColorById, onCitationClick, activeCitationId, searchQuery, null, activeCitationRef)}</Tag>;
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
          <li key={`${key}-${itemIndex}`}>{renderInline(line.replace(/^- /, ""), citations, citationColorById, onCitationClick, activeCitationId, searchQuery, null, activeCitationRef)}</li>
        ))}
      </ul>
    );
  }
  return <p key={key}>{renderInline(trimmed, citations, citationColorById, onCitationClick, activeCitationId, searchQuery, blockStart + trimOffset, activeCitationRef)}</p>;
}

function renderInline(
  text: string,
  citations: Map<string, NoteCitation>,
  citationColorById: Map<string, number>,
  onCitationClick: (citation: NoteCitation, ref?: CitationMarkdownRef | null) => void,
  activeCitationId = "",
  searchQuery = "",
  baseOffset: number | null = null,
  activeCitationRef: CitationMarkdownRef | null = null
) {
  const parts = text.split(/(\*\*[^*]+\*\*|\*[^*]+\*|`[^`]+`|==[^=]+==|<mark(?:\s+[^>]*)?>.*?<\/mark>|<span style="color:[^"]+">.*?<\/span>|\[[^\]]+\]\([^)]+\))/g);
  let cursor = 0;
  return parts.map((part, index) => {
    const partStart = cursor;
    cursor += part.length;
    const citationMatch = /^\[([^\]]+)\]\(sciencekg:\/\/citation\/([^)]+)\)$/.exec(part);
    if (citationMatch) {
      const citation = citations.get(citationMatch[2]);
      const colorIndex = citationColorById.get(citationMatch[2]) ?? citationColorIndex(citation);
      const ref =
        baseOffset === null
          ? null
          : {
              id: citationMatch[2],
              label: citationMatch[1],
              badge: citationBadgeFromLabel(citationMatch[1]),
              title: citationTitleFromLabel(citationMatch[1]),
              start: baseOffset + partStart,
              end: baseOffset + partStart + part.length
            };
      const isActive =
        citationMatch[2] === activeCitationId &&
        (!activeCitationRef || (ref !== null && activeCitationRef.start === ref.start && activeCitationRef.end === ref.end));
      return (
        <button
          key={`${part}-${index}`}
          type="button"
          className={`citation-link citation-link--mapped ${isActive ? "citation-link--active" : ""}`}
          contentEditable={false}
          data-citation-id={citationMatch[2]}
          data-citation-label={citationMatch[1]}
          style={citation ? evidenceColorVars(colorIndex) : undefined}
          onMouseDown={(event) => event.preventDefault()}
          onClick={() => citation && onCitationClick(citation, ref)}
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
    return <span key={`${part}-${index}`}>{highlightPreviewSearch(part, searchQuery)}</span>;
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

/**
 * Guarantee that every sciencekg://citation link of the replaced text survives an AI
 * rewrite. Links the rewrite kept (same citation id) stay untouched; dropped ones are
 * appended as a compact source line so the quote's provenance is never lost.
 */
export function withPreservedCitationLinks(originalText: string, replacementText: string): string {
  const pattern = /\[([^\]]+)\]\(sciencekg:\/\/citation\/([^)]+)\)/g;
  const lost: string[] = [];
  const seen = new Set<string>();
  for (const match of originalText.matchAll(pattern)) {
    const citationId = match[2];
    if (citationId === "preview" || seen.has(citationId)) {
      continue;
    }
    seen.add(citationId);
    if (!replacementText.includes(`sciencekg://citation/${citationId}`)) {
      lost.push(`[${match[1].trim()}](sciencekg://citation/${citationId})`);
    }
  }
  if (!lost.length) {
    return replacementText;
  }
  return `${replacementText.trimEnd()}\n\nQuellen: ${lost.join(" · ")}`;
}

function findNoteSearchRanges(markdown: string, query: string) {
  const needle = query.toLowerCase().trim();
  if (!needle) {
    return [] as Array<{ start: number; end: number }>;
  }
  const haystack = markdown.toLowerCase();
  const ranges: Array<{ start: number; end: number }> = [];
  let index = haystack.indexOf(needle);
  while (index >= 0 && ranges.length < 500) {
    ranges.push({ start: index, end: index + needle.length });
    index = haystack.indexOf(needle, index + Math.max(1, needle.length));
  }
  return ranges;
}

function highlightPreviewSearch(text: string, query: string) {
  const needle = query.trim();
  if (!needle) {
    return text;
  }
  const lowerText = text.toLowerCase();
  const lowerNeedle = needle.toLowerCase();
  const parts: Array<string | ReactNode> = [];
  let cursor = 0;
  let index = lowerText.indexOf(lowerNeedle);
  while (index >= 0) {
    if (index > cursor) {
      parts.push(text.slice(cursor, index));
    }
    const end = index + needle.length;
    parts.push(
      <mark className="markdown-preview-search-hit" key={`${index}-${end}`}>
        {text.slice(index, end)}
      </mark>
    );
    cursor = end;
    index = lowerText.indexOf(lowerNeedle, cursor);
  }
  if (!parts.length) {
    return text;
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return parts;
}

function citationRefAtPosition(refs: CitationMarkdownRef[], position: number) {
  return refs.find((ref) => position >= ref.start && position <= ref.end) ?? null;
}

function threadAnchorRange(thread: NoteAiThread, markdown: string, localRange?: ThreadStoredRange): ThreadAnchorRange | null {
  const resultRange = localRange ?? threadResultRange(thread);
  const resolvedResult = resultRange ? resolveStoredThreadRange(markdown, resultRange, true) : null;
  if (resolvedResult) {
    return resolvedResult;
  }

  const quote = stripHighlightMarkers(thread.anchor_quote || thread.selected_text || "").trim();
  const anchorStart = thread.anchor_start === null || thread.anchor_start === undefined ? NaN : Number(thread.anchor_start);
  const anchorEnd = thread.anchor_end === null || thread.anchor_end === undefined ? NaN : Number(thread.anchor_end);
  if (Number.isFinite(anchorStart) && Number.isFinite(anchorEnd) && anchorStart >= 0 && anchorEnd > anchorStart && anchorEnd <= markdown.length) {
    if (!quote || markdown.slice(anchorStart, anchorEnd).includes(quote) || quote.includes(markdown.slice(anchorStart, anchorEnd).trim())) {
      return { start: anchorStart, end: anchorEnd, text: markdown.slice(anchorStart, anchorEnd), changedRanges: [], manualRanges: [] };
    }
  }
  if (quote) {
    const index = markdown.indexOf(quote);
    if (index >= 0) {
      return { start: index, end: index + quote.length, text: quote, changedRanges: [], manualRanges: [] };
    }
    const fuzzy = resolveStoredThreadRange(markdown, { start: Number.isFinite(anchorStart) ? anchorStart : 0, end: Number.isFinite(anchorEnd) ? anchorEnd : 0, text: quote }, false);
    if (fuzzy) {
      return fuzzy;
    }
  }
  return null;
}

function threadResultRange(thread: NoteAiThread): ThreadStoredRange | null {
  const start = Number(thread.ui_state?.result_anchor_start);
  const end = Number(thread.ui_state?.result_anchor_end);
  const text = String(thread.ui_state?.result_anchor_text ?? "").trim();
  if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start || !text) {
    return null;
  }
  return { start, end, text, manualRanges: readThreadManualRanges(thread.ui_state?.result_manual_ranges) };
}

function threadRangeUiState(range: ThreadStoredRange) {
  const manualRanges = normalizeManualRanges(range.manualRanges, range.start, range.end);
  return {
    result_anchor_start: range.start,
    result_anchor_end: range.end,
    result_anchor_text: range.text.slice(0, THREAD_RANGE_TEXT_LIMIT),
    result_manual_ranges: manualRanges
  };
}

function threadRangeMatchesUi(thread: NoteAiThread, range: ThreadStoredRange) {
  const start = Number(thread.ui_state?.result_anchor_start);
  const end = Number(thread.ui_state?.result_anchor_end);
  const text = String(thread.ui_state?.result_anchor_text ?? "");
  const manualRanges = normalizeManualRanges(range.manualRanges, range.start, range.end);
  return start === range.start && end === range.end && text === range.text.slice(0, THREAD_RANGE_TEXT_LIMIT) && sameThreadRanges(readThreadManualRanges(thread.ui_state?.result_manual_ranges), manualRanges);
}

function resolveStoredThreadRange(markdown: string, stored: ThreadStoredRange, acceptEditedRange: boolean): ThreadAnchorRange | null {
  const start = Math.max(0, Math.min(markdown.length, stored.start));
  const end = Math.max(start, Math.min(markdown.length, stored.end));
  const original = stripHighlightMarkers(stored.text || "").trim();
  const manualRanges = normalizeManualRanges(stored.manualRanges, start, end);
  if (acceptEditedRange && end > start) {
    const current = markdown.slice(start, end);
    const comparableCurrent = textWithoutAbsoluteRanges(current, start, manualRanges);
    if (current.trim() && storedThreadRangeMatchesCurrent(comparableCurrent, original)) {
      const changedRanges = storedThreadTextLooksTruncated(comparableCurrent, original) ? [] : changedThreadRanges(start, comparableCurrent, original);
      return { start, end, text: current, changedRanges, manualRanges };
    }
  }
  if (original) {
    const exactIndex = markdown.indexOf(original);
    if (exactIndex >= 0 && !manualRanges.length) {
      return { start: exactIndex, end: exactIndex + original.length, text: original, changedRanges: [], manualRanges: [] };
    }
  }
  if (!original) {
    return null;
  }
  const fragments = meaningfulThreadFragments(original);
  for (const fragment of fragments) {
    const index = markdown.indexOf(fragment);
    if (index >= 0) {
      const range = expandThreadFragmentRange(markdown, index, fragment.length, original);
      const current = markdown.slice(range.start, range.end);
      return { ...range, text: current, changedRanges: changedThreadRanges(range.start, current, original), manualRanges: [] };
    }
  }
  return null;
}

function textWithoutAbsoluteRanges(text: string, baseStart: number, ranges: ThreadTextRange[]) {
  if (!ranges.length) {
    return text;
  }
  let cursor = 0;
  const parts: string[] = [];
  for (const range of ranges) {
    const start = Math.max(0, Math.min(text.length, range.start - baseStart));
    const end = Math.max(start, Math.min(text.length, range.end - baseStart));
    if (start > cursor) {
      parts.push(text.slice(cursor, start));
    }
    cursor = Math.max(cursor, end);
  }
  if (cursor < text.length) {
    parts.push(text.slice(cursor));
  }
  return parts.join("");
}

function storedThreadRangeMatchesCurrent(current: string, original: string) {
  if (!original) {
    return Boolean(current.trim());
  }
  const normalizedCurrent = normalizeThreadText(current);
  const normalizedOriginal = normalizeThreadText(original);
  if (!normalizedOriginal) {
    return Boolean(normalizedCurrent);
  }
  const leadingOriginal = normalizedOriginal.slice(0, Math.min(160, normalizedOriginal.length));
  return normalizedCurrent.includes(normalizedOriginal) || normalizedCurrent.startsWith(leadingOriginal) || textSimilarity(current, original) >= 0.22;
}

function storedThreadTextLooksTruncated(current: string, original: string) {
  if (!original || current.length <= original.length) {
    return false;
  }
  const normalizedOriginal = normalizeThreadText(original);
  return Boolean(normalizedOriginal && normalizeThreadText(current).startsWith(normalizedOriginal.slice(0, Math.min(160, normalizedOriginal.length))));
}

function threadHighlightSegments(anchor: ThreadAnchorRange) {
  return rangeSegmentsExcluding({ start: anchor.start, end: anchor.end }, anchor.manualRanges);
}

function rangeSegmentsExcluding(range: ThreadTextRange, exclusions: ThreadTextRange[]) {
  const normalized = normalizeManualRanges(exclusions, range.start, range.end);
  const segments: ThreadTextRange[] = [];
  let cursor = range.start;
  for (const exclusion of normalized) {
    if (exclusion.start > cursor) {
      segments.push({ start: cursor, end: exclusion.start });
    }
    cursor = Math.max(cursor, exclusion.end);
  }
  if (cursor < range.end) {
    segments.push({ start: cursor, end: range.end });
  }
  return segments.filter((segment) => segment.end > segment.start);
}

function changedThreadRanges(start: number, current: string, original: string) {
  if (!original || normalizeThreadText(current) === normalizeThreadText(original)) {
    return [];
  }
  let prefix = 0;
  while (prefix < current.length && prefix < original.length && current[prefix] === original[prefix]) {
    prefix += 1;
  }
  let suffix = 0;
  while (
    suffix + prefix < current.length &&
    suffix + prefix < original.length &&
    current[current.length - 1 - suffix] === original[original.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const changedStart = start + prefix;
  const changedEnd = start + Math.max(prefix, current.length - suffix);
  return changedEnd > changedStart ? [{ start: changedStart, end: changedEnd }] : [];
}

function shiftThreadRanges(ranges: Record<string, ThreadStoredRange>, previous: string, next: string) {
  if (previous === next || !Object.keys(ranges).length) {
    return ranges;
  }
  const diff = textDiffWindow(previous, next);
  if (!diff) {
    return ranges;
  }
  let changed = false;
  const shifted: Record<string, ThreadStoredRange> = {};
  for (const [threadId, range] of Object.entries(ranges)) {
    let start = range.start;
    let end = range.end;
    let manualRanges = shiftManualRanges(range.manualRanges ?? [], diff, next.length);
    const editOverlapsRange = diff.beforeEnd > start && diff.start < end;
    if (diff.beforeEnd <= start) {
      start += diff.delta;
      end += diff.delta;
    } else if (diff.start >= end) {
      // Edit happened after this range.
    } else {
      if (diff.start < start) {
        start = diff.start;
      }
      end = Math.max(start, end + diff.delta);
      if (diff.afterEnd > end) {
        end = diff.afterEnd;
      }
    }
    start = Math.max(0, Math.min(next.length, start));
    end = Math.max(start, Math.min(next.length, end));
    if (editOverlapsRange && diff.afterEnd > diff.start) {
      manualRanges.push({ start: Math.max(start, diff.start), end: Math.min(end, diff.afterEnd) });
    }
    manualRanges = normalizeManualRanges(manualRanges, start, end);
    shifted[threadId] = { ...range, start, end, manualRanges };
    changed = changed || start !== range.start || end !== range.end || !sameThreadRanges(manualRanges, range.manualRanges ?? []);
  }
  return changed ? shifted : ranges;
}

function readThreadManualRanges(value: unknown): ThreadTextRange[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value
    .map((item) => {
      const record = item as Record<string, unknown>;
      return { start: Number(record.start), end: Number(record.end) };
    })
    .filter((range) => Number.isFinite(range.start) && Number.isFinite(range.end) && range.end > range.start);
}

function shiftManualRanges(ranges: ThreadTextRange[], diff: ThreadTextDiff, docLength: number) {
  if (!ranges.length) {
    return [];
  }
  const shifted: ThreadTextRange[] = [];
  for (const range of ranges) {
    let start = range.start;
    let end = range.end;
    if (!Number.isFinite(start) || !Number.isFinite(end) || end <= start) {
      continue;
    }
    if (diff.beforeEnd <= start) {
      start += diff.delta;
      end += diff.delta;
    } else if (diff.start >= end) {
      // Edit happened after this manual segment.
    } else {
      if (diff.start < start) {
        start = diff.start;
      }
      end = Math.max(start, end + diff.delta);
      if (diff.afterEnd > end) {
        end = diff.afterEnd;
      }
    }
    start = Math.max(0, Math.min(docLength, start));
    end = Math.max(start, Math.min(docLength, end));
    if (end > start) {
      shifted.push({ start, end });
    }
  }
  return shifted;
}

function normalizeManualRanges(ranges: ThreadTextRange[] | undefined, start: number, end: number) {
  const clipped = (ranges ?? [])
    .map((range) => ({
      start: Math.max(start, Math.min(end, Number(range.start))),
      end: Math.max(start, Math.min(end, Number(range.end)))
    }))
    .filter((range) => Number.isFinite(range.start) && Number.isFinite(range.end) && range.end > range.start)
    .sort((left, right) => left.start - right.start || left.end - right.end);
  const merged: ThreadTextRange[] = [];
  for (const range of clipped) {
    const previous = merged[merged.length - 1];
    if (previous && range.start <= previous.end) {
      previous.end = Math.max(previous.end, range.end);
    } else {
      merged.push({ ...range });
    }
  }
  return merged;
}

function sameThreadRanges(left: ThreadTextRange[], right: ThreadTextRange[]) {
  if (left.length !== right.length) {
    return false;
  }
  return left.every((range, index) => range.start === right[index].start && range.end === right[index].end);
}

function seedThreadRanges(current: Record<string, ThreadStoredRange>, threads: NoteAiThread[]) {
  if (!threads.length) {
    return current;
  }
  let next = current;
  for (const thread of threads) {
    if (next[thread.id]) {
      continue;
    }
    const range = threadResultRange(thread);
    if (!range) {
      continue;
    }
    if (next === current) {
      next = { ...current };
    }
    next[thread.id] = range;
  }
  return next;
}

function textDiffWindow(previous: string, next: string): ThreadTextDiff | null {
  let start = 0;
  while (start < previous.length && start < next.length && previous[start] === next[start]) {
    start += 1;
  }
  if (start === previous.length && start === next.length) {
    return null;
  }
  let suffix = 0;
  while (
    suffix + start < previous.length &&
    suffix + start < next.length &&
    previous[previous.length - 1 - suffix] === next[next.length - 1 - suffix]
  ) {
    suffix += 1;
  }
  const beforeEnd = previous.length - suffix;
  const afterEnd = next.length - suffix;
  return { start, beforeEnd, afterEnd, delta: afterEnd - beforeEnd };
}

function meaningfulThreadFragments(text: string) {
  const normalized = text.replace(/\s+/g, " ").trim();
  if (!normalized) {
    return [];
  }
  const fragments = [
    normalized.slice(0, 140),
    normalized.slice(Math.max(0, Math.floor(normalized.length / 2) - 70), Math.min(normalized.length, Math.floor(normalized.length / 2) + 70)),
    normalized.slice(Math.max(0, normalized.length - 140))
  ];
  return Array.from(new Set(fragments.map((fragment) => fragment.trim()).filter((fragment) => fragment.length >= 24))).sort((left, right) => right.length - left.length);
}

function expandThreadFragmentRange(markdown: string, fragmentStart: number, fragmentLength: number, original: string) {
  const targetLength = Math.max(fragmentLength, Math.min(markdown.length, original.length));
  let start = fragmentStart;
  let end = fragmentStart + fragmentLength;
  while (start > 0 && end - start < targetLength && markdown[start - 1] !== "\n") {
    start -= 1;
  }
  while (end < markdown.length && end - start < targetLength && markdown[end] !== "\n") {
    end += 1;
  }
  return { start, end };
}

function textSimilarity(left: string, right: string) {
  const leftTerms = new Set(normalizeThreadText(left).split(" ").filter((term) => term.length >= 4));
  const rightTerms = new Set(normalizeThreadText(right).split(" ").filter((term) => term.length >= 4));
  if (!leftTerms.size || !rightTerms.size) {
    return 0;
  }
  let overlap = 0;
  leftTerms.forEach((term) => {
    if (rightTerms.has(term)) {
      overlap += 1;
    }
  });
  return overlap / Math.max(leftTerms.size, rightTerms.size);
}

function normalizeThreadText(value: string) {
  return value.toLowerCase().replace(/[^\p{L}\p{N}]+/gu, " ").replace(/\s+/g, " ").trim();
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

function shortThreadContext(value: string) {
  const text = stripHighlightMarkers(value || "").replace(/\s+/g, " ").trim();
  return text.length <= 170 ? text : `${text.slice(0, 167)}...`;
}

function citationColorIndex(citation?: Pick<NoteCitation, "evidence_index"> | null, fallback = 0) {
  const index = Number(citation?.evidence_index);
  return Number.isFinite(index) ? index : fallback;
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
