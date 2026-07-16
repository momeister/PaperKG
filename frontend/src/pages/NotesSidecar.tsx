// NotesSidecar (Notiz-Seitenpanel des Assistenten) — aus AssistantPage.tsx extrahiert.
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
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
  Minus,
  NotebookPen,
  PanelLeftClose,
  PanelLeftOpen,
  PanelRightClose,
  PanelRightOpen,
  Quote,
  Send,
  SeparatorHorizontal,
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
import { MarkdownPreview } from "./NotesSubComponents";
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

import {
  applyTabIndent,
  attachTextareaAutoSync,
  clampSplitRatio,
  continueMarkdownLine,
  dividerSnippetForTextarea,
  FORMAT_AS_MARKDOWN_INSTRUCTION,
  loadNumberUiState,
  saveNumberUiState,
  toggleWrap
} from "./notesHelpers";

// Session/note persistence lives in ./assistantSession; re-export the public
// surface so existing importers (WorkspacePage, tests) keep working unchanged.
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
import type {
  AssistantAnswerBlock,
  AssistantTurn,
  CitationInsertExtras,
  CitationMeta,
  NoteSelectionRange,
} from "./AssistantPage";
import { EvidenceVerificationBadge } from "./AnswerText";

export function NotesSidecar({
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
  const [editorMode, setEditorMode] = useState<"edit" | "preview" | "split">("edit");
  const [selection, setSelection] = useState<NoteSelectionRange | null>(null);
  const [aiInstruction, setAiInstruction] = useState("");
  const [aiPreview, setAiPreview] = useState("");
  const [aiError, setAiError] = useState("");
  const [isAskingSelection, setIsAskingSelection] = useState(false);
  const [splitRatio, setSplitRatio] = useState(() => loadNumberUiState("sidecar.splitRatio", 0.5));
  const splitGridRef = useRef<HTMLDivElement | null>(null);
  const [editorScrollTop, setEditorScrollTop] = useState(0);
  const [editorScrollLeft, setEditorScrollLeft] = useState(0);
  const noteEditorRef = useRef<HTMLTextAreaElement | null>(null);
  const editorWrapRef = useRef<HTMLDivElement | null>(null);
  const previewRef = useRef<HTMLElement | null>(null);
  const previewScrollRef = useRef({ top: 0, left: 0 });

  const selectionPreview = stripHighlightMarkers(selection?.text ?? "");
  const highlightRanges = selection
    ? [{ start: selection.start, end: selection.end, className: "textarea-highlight-range--selection" }]
    : [];
  const ghostInsertions =
    selection && aiPreview
      ? [{ index: selection.end, content: `\n\n${aiPreview}`, className: "textarea-ghost-insertion--ai" }]
      : [];

  function startSplitResize(event: ReactPointerEvent<HTMLDivElement>) {
    event.preventDefault();
    const container = splitGridRef.current;
    if (!container) return;
    const rect = container.getBoundingClientRect();
    let frame: number | null = null;
    let latest = splitRatio;
    const move = (moveEvent: PointerEvent) => {
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
      saveNumberUiState("sidecar.splitRatio", latest);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", stop);
  }

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

  async function formatSelectionAsMarkdown() {
    if (!selection) {
      return;
    }
    setIsAskingSelection(true);
    setAiError("");
    try {
      const response = await askSelection(selection, FORMAT_AS_MARKDOWN_INSTRUCTION);
      setAiPreview(response);
    } catch (error) {
      setAiError(error instanceof Error ? error.message : "KI-Formatierung fehlgeschlagen");
    } finally {
      setIsAskingSelection(false);
    }
  }

  function wrapSelection(before: string, after = before) {
    const node = noteEditorRef.current;
    const start = node?.selectionStart ?? notes.length;
    const end = node?.selectionEnd ?? notes.length;
    const result = toggleWrap(notes, start, end, before, after);
    setNotes(result.next);
    requestAnimationFrame(() => {
      noteEditorRef.current?.focus();
      noteEditorRef.current?.setSelectionRange(result.selStart, result.selEnd);
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

  function handleNotesKeyDown(event: ReactKeyboardEvent<HTMLTextAreaElement>) {
    if (event.key === "Tab") {
      event.preventDefault();
      const node = noteEditorRef.current;
      if (!node) return;
      const result = applyTabIndent(notes, node.selectionStart, node.selectionEnd, event.shiftKey);
      if (!result) return;
      setNotes(result.next);
      requestAnimationFrame(() => {
        node.focus();
        node.setSelectionRange(result.selStart, result.selEnd);
      });
      return;
    }
    if (event.key === "Enter" && !event.shiftKey) {
      const node = noteEditorRef.current;
      if (!node) return;
      const result = continueMarkdownLine(notes, node.selectionStart, node.selectionEnd);
      if (!result) return;
      event.preventDefault();
      setNotes(result.next);
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
        wrapSelection("**");
        return;
      }
      if (key === "i") {
        event.preventDefault();
        wrapSelection("*");
        return;
      }
      if (key === "e") {
        event.preventDefault();
        wrapSelection("`");
        return;
      }
      if (key === "k" && !event.shiftKey) {
        event.preventDefault();
        wrapSelection("[", "](https://)");
        return;
      }
    }
  }

  // Edit/Preview/Split each conditionally mount their pane, so switching modes remounts a
  // fresh DOM node whose scroll resets to 0 — restore the last known offset instead of always
  // jumping back to the top.
  useLayoutEffect(() => {
    const node = noteEditorRef.current;
    if (!node) return;
    node.scrollTop = editorScrollTop;
    node.scrollLeft = editorScrollLeft;
    return attachTextareaAutoSync(node, () => notes, setNotes);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editorMode]);

  useLayoutEffect(() => {
    const node = previewRef.current;
    if (!node) return;
    node.scrollTop = previewScrollRef.current.top;
    node.scrollLeft = previewScrollRef.current.left;
  }, [editorMode]);

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
        <button
          className="icon-button"
          type="button"
          aria-label="Durchgehende Trennlinie einfügen"
          title="Durchgehende Trennlinie einfügen"
          onClick={() => insertAtCursor(dividerSnippetForTextarea("solid", noteEditorRef.current))}
        >
          <SeparatorHorizontal size={16} />
        </button>
        <button
          className="icon-button"
          type="button"
          aria-label="Gestrichelte Trennlinie einfügen"
          title="Gestrichelte Trennlinie einfügen"
          onClick={() => insertAtCursor(dividerSnippetForTextarea("dashed", noteEditorRef.current))}
        >
          <Minus size={16} />
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
          <button type="button" className={editorMode === "split" ? "active" : ""} onClick={() => setEditorMode("split")}>
            Split
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
      <div
        className={`markdown-editor-grid markdown-editor-grid--${editorMode}`}
        ref={splitGridRef}
        style={editorMode === "split" ? { gridTemplateColumns: `minmax(0, ${splitRatio}fr) 6px minmax(0, ${1 - splitRatio}fr)`, gap: 0 } : undefined}
      >
      {editorMode === "edit" || editorMode === "split" ? (
        <div className="notes-editor-wrap notes-editor-wrap--highlighted" ref={editorWrapRef}>
          <TextareaHighlightLayer text={notes} ranges={highlightRanges} insertions={ghostInsertions} scrollTop={editorScrollTop} scrollLeft={editorScrollLeft} />
          <textarea
            ref={noteEditorRef}
            className="notes-editor notes-editor--highlighted"
            value={notes}
            onChange={(event) => setNotes(event.target.value)}
            onSelect={captureSelection}
            onPointerDown={clearEditorSelection}
            onKeyDown={handleNotesKeyDown}
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
                  <button
                    className="button button-compact"
                    type="button"
                    disabled={isAskingSelection}
                    title="Formatiert die Auswahl als sauberes Markdown, ohne den Inhalt zu ändern"
                    onClick={() => {
                      pinSelectionForQuestion();
                      void formatSelectionAsMarkdown();
                    }}
                  >
                    Als Markdown formatieren
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
            saveNumberUiState("sidecar.splitRatio", 0.5);
          }}
        />
      ) : null}
      {editorMode === "preview" || editorMode === "split" ? (
        notes.trim() ? (
          <MarkdownPreview
            previewRef={previewRef}
            markdown={notes}
            citations={[]}
            onCitationClick={() => {}}
            editable={false}
            className="notes-sidecar-preview"
            onScroll={(event) => {
              previewScrollRef.current = { top: event.currentTarget.scrollTop, left: event.currentTarget.scrollLeft };
            }}
          />
        ) : (
          <article className="markdown-preview notes-sidecar-preview muted-row">Keine Notizen</article>
        )
      ) : null}
      </div>
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

