// Standalone, prop-driven sub-components + markdown-render helpers extracted from
// NotesPage.tsx. Re-imported by NotesPage; reference each other and notesHelpers.
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
  NotebookPen,
  PanelRightClose,
  PanelRightOpen,
  Pin,
  Quote,
  Redo2,
  Search,
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
  IMAGE_PREVIEW_HIDDEN_KEY,
  TABLE_PICKER_COLS,
  TABLE_PICKER_ROWS,
  THREAD_RANGE_TEXT_LIMIT,
  TRANSLATE_LANGUAGES,
  absoluteUrl,
  assignMissingThreadMeta,
  buildMarkdownTable,
  caretIndexFromPoint,
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
  markdownContinuation,
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
  withPreservedCitationLinks,
} from "./notesHelpers";
import { decodeCitationId, encodeCitationId } from "./assistantHelpers";
import type { Note, NoteAiMessage, NoteAiThread, NoteCitation, VerificationEvidence } from "../types";

import type {
  SelectionRange, EditorMode, NoteAiThreadsResult, CitationMarkdownRef, ThreadAnchorMeta,
  ThreadTextRange, ThreadTextDiff, ThreadStoredRange, ThreadAnchorRange, NoteCitationRow,
  NotesSurfaceSnapshot, NotesSurfaceActions, MarkdownBlock,
} from "./NotesPage";

export function ThreadAnchorMarker({
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


export function NoteContextToolbar({
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


export function AiThreadList({
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


export function MarkdownPreview({
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


export function renderBlock(
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


/**
 * Ein aufklappbarer Quellen-Chip am Ende eines Zitats. Zeigt kompakt „📖 n Quellen" und
 * öffnet auf Klick ein Popover mit allen enthaltenen Zitaten (jeweils anklickbar → Quelle
 * öffnen). Ersetzt die frühere Fließtext-Quellenzeile, die den Schreibfluss störte.
 */
export function CitationGroupButton({
  ids,
  citations,
  citationColorById,
  onCitationClick,
  groupRef,
  activeCitationId
}: {
  ids: string[];
  citations: Map<string, NoteCitation>;
  citationColorById: Map<string, number>;
  onCitationClick: (citation: NoteCitation, ref?: CitationMarkdownRef | null) => void;
  groupRef: CitationMarkdownRef | null;
  activeCitationId: string;
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement>(null);
  const resolved = ids.map((id) => citations.get(id)).filter((item): item is NoteCitation => Boolean(item));
  const label = ids.length === 1 ? "1 Quelle" : `${ids.length} Quellen`;
  const isActive = ids.some((id) => id === activeCitationId);

  useEffect(() => {
    if (!open) {
      return;
    }
    const onDocMouseDown = (event: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    };
    document.addEventListener("mousedown", onDocMouseDown);
    return () => document.removeEventListener("mousedown", onDocMouseDown);
  }, [open]);

  return (
    <span
      className="citation-group"
      ref={wrapRef}
      contentEditable={false}
      data-citation-group-ids={ids.map(encodeCitationId).join(",")}
      data-citation-label={groupRef?.label ?? label}
    >
      <button
        type="button"
        className={`citation-group-button ${isActive ? "citation-group-button--active" : ""}`}
        contentEditable={false}
        aria-expanded={open}
        title="Quellen anzeigen"
        onMouseDown={(event) => event.preventDefault()}
        onClick={() => setOpen((current) => !current)}
      >
        <Quote size={12} aria-hidden />
        <span>{label}</span>
      </button>
      {open ? (
        <span className="citation-group-popover" role="menu">
          {resolved.length ? (
            resolved.map((citation, i) => {
              const colorIndex = citationColorById.get(citation.id) ?? citationColorIndex(citation, i);
              const badge = `Z${Number(citation.evidence_index ?? i) + 1}`;
              return (
                <button
                  key={citation.id}
                  type="button"
                  className="citation-group-item"
                  role="menuitem"
                  style={colorVarsForPaperId(citation.paper_id, colorIndex)}
                  onMouseDown={(event) => event.preventDefault()}
                  onClick={() => {
                    setOpen(false);
                    onCitationClick(citation, groupRef);
                  }}
                >
                  <span className="citation-group-item-badge">{badge}</span>
                  <span className="citation-group-item-text">
                    <span className="citation-group-item-title">{citation.title || citation.paper_id}</span>
                    <span className="citation-group-item-paper">{citation.paper_id}</span>
                  </span>
                </button>
              );
            })
          ) : (
            <span className="citation-group-empty">Quellen nicht gefunden</span>
          )}
        </span>
      ) : null}
    </span>
  );
}

export function renderInline(
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
    const groupMatch = /^\[([^\]]+)\]\((?:sciencekg:\/\/citations|skg:\/\/c)\/([^)]+)\)$/.exec(part);
    if (groupMatch) {
      const ids = groupMatch[2].split(",").map((id) => decodeCitationId(id)).filter(Boolean);
      const groupRef: CitationMarkdownRef | null =
        baseOffset === null
          ? null
          : {
              id: ids.join(","),
              label: groupMatch[1],
              badge: "",
              title: "",
              start: baseOffset + partStart,
              end: baseOffset + partStart + part.length,
              groupIds: ids
            };
      return (
        <CitationGroupButton
          key={`${part}-${index}`}
          ids={ids}
          citations={citations}
          citationColorById={citationColorById}
          onCitationClick={onCitationClick}
          groupRef={groupRef}
          activeCitationId={activeCitationId}
        />
      );
    }
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
          style={citation ? colorVarsForPaperId(citation.paper_id, colorIndex) : undefined}
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
    // Einzelne Zeilenumbrüche sichtbar machen: eine neue Zeile im Editor ist auch in
    // der Preview eine neue Zeile (serializePreviewNode mappt <br> zurück auf \n).
    const lines = part.split("\n");
    return (
      <span key={`${part}-${index}`}>
        {lines.map((line, lineIndex) => (
          <Fragment key={lineIndex}>
            {highlightPreviewSearch(line, searchQuery)}
            {lineIndex < lines.length - 1 ? <br /> : null}
          </Fragment>
        ))}
      </span>
    );
  });
}


export function highlightPreviewSearch(text: string, query: string) {
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

