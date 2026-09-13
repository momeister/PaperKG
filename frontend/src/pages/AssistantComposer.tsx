import { AppStateContext } from "../state";
import { useContext } from "react";
import { registerWorkspaceShutdown } from "../workspace/ShutdownGuard";
import { forwardRef, useImperativeHandle, useMemo, useRef, useState, useEffect } from "react";
import type { ChangeEvent, KeyboardEvent as ReactKeyboardEvent } from "react";
import { Bot, Command, Send } from "lucide-react";
import type { Paper } from "../types";
import { WORKSPACE_COMMANDS, matchWorkspaceCommands, normalizeFilter, normalizeWorkspacePaper, workspacePaperId, workspacePaperTitle } from "./workspaceHelpers";
type WorkspaceCommandDef = (typeof WORKSPACE_COMMANDS)[number];

export type AssistantComposerHandle = { getDocument: () => Document; setDraft: (value: string) => void; focus: (position?: number) => void; openMention: () => void; submit: () => void };

/** Keystrokes stay here: the saved conversation, notes and PDF never render on input. */
export const AssistantComposer = forwardRef<AssistantComposerHandle, {
  sessionId?: string; papers: Paper[]; disabled: boolean; onSubmit: (value: string) => void; onSelectPaper: (paperId: string) => void;
}>(function AssistantComposer({ sessionId, papers: pdfPapers, disabled, onSubmit, onSelectPaper }, ref) {
  const activeProject = useContext(AppStateContext)?.activeProject;
  const draftKey = `sciencekg.workspace.composer.${activeProject ?? ""}.${sessionId || "new"}`;
  const drafts = useRef(new Map<string, string>());
  if (!drafts.current.has(draftKey)) {
    try { drafts.current.set(draftKey, localStorage.getItem(draftKey) ?? ""); }
    catch { drafts.current.set(draftKey, ""); }
  }
  const question = drafts.current.get(draftKey)!;
  const [, refreshDraft] = useState(0);
  const [draftError, setDraftError] = useState("");
  const setQuestion = (value: string) => { drafts.current.set(draftKey, value); refreshDraft(v => v + 1); };
  useEffect(() => registerWorkspaceShutdown(() => {
    for (const [key, value] of drafts.current) localStorage.setItem(key, value);
  }), []);
  useEffect(() => {
    const timer = setTimeout(() => {
      try { localStorage.setItem(draftKey, question); setDraftError(""); }
      catch { setDraftError("Assistant-Entwurf konnte nicht lokal gesichert werden."); }
    }, 800);
    return () => clearTimeout(timer);
  }, [draftKey, question]);
  const questionInputRef = useRef<HTMLInputElement | null>(null);
  const [mentionState, setMentionState] = useState<{ query: string; start: number; end: number } | null>(null);
  const [mentionHighlight, setMentionHighlight] = useState(0);
  const [paletteIndex, setPaletteIndex] = useState(0);
  function send() { if (!disabled && question.trim()) onSubmit(question.trim()); }
  useImperativeHandle(ref, () => ({ getDocument: () => questionInputRef.current?.ownerDocument ?? document, setDraft: setQuestion, focus: (position) => { questionInputRef.current?.focus(); if (position !== undefined) placeCursorAfter(position); }, openMention: openMentionPicker, submit: send }));
  // Befehlspalette: sichtbar solange der erste Token noch getippt wird ("/su…").
  const paletteQuery = /^\/\S*$/.test(question) ? question.slice(1) : null;
  const paletteCandidates = useMemo(() => (paletteQuery !== null ? matchWorkspaceCommands(paletteQuery) : []), [paletteQuery]);
  const activeCommandHint = useMemo(() => {
    const match = question.match(/^\/([\wäöüß]+)\s/i);
    if (!match) {
      return null;
    }
    const name = match[1].toLowerCase();
    return WORKSPACE_COMMANDS.find((command) => command.name === name || (command.aliases ?? []).includes(name)) ?? null;
  }, [question]);
  useEffect(() => setPaletteIndex(0), [paletteQuery]);
  const mentionCandidates = useMemo(() => {
    if (!mentionState) {
      return [];
    }
    const needle = normalizeFilter(mentionState.query);
    const pool = needle
      ? pdfPapers.filter((paper) => {
          const normalized = normalizeWorkspacePaper(paper);
          const haystack = normalizeFilter(`${workspacePaperTitle(normalized)} ${workspacePaperId(normalized)}`);
          return haystack.includes(needle);
        })
      : pdfPapers;
    return pool.slice(0, 8);
  }, [mentionState, pdfPapers]);
  function mentionMarkerLabel(paper: Paper): string {
    const normalized = normalizeWorkspacePaper(paper);
    const title = workspacePaperTitle(normalized);
    const short = title.length > 42 ? `${title.slice(0, 39)}…` : title;
    return `@[${short}]`;
  }

  function placeCursorAfter(position: number) {
    const input = questionInputRef.current;
    if (!input) {
      return;
    }
    requestAnimationFrame(() => {
      input.focus();
      input.setSelectionRange(position, position);
    });
  }

  function applyMention(paper: Paper) {
    if (!mentionState) {
      return;
    }
    const paperId = workspacePaperId(normalizeWorkspacePaper(paper));
    const marker = `${mentionMarkerLabel(paper)} `;
    const before = question.slice(0, mentionState.start);
    const after = question.slice(mentionState.end);
    setQuestion(`${before}${marker}${after}`);
    if (paperId) {
      onSelectPaper(paperId);
    }
    setMentionState(null);
    setMentionHighlight(0);
    placeCursorAfter(before.length + marker.length);
  }

  function openMentionPicker() {
    const input = questionInputRef.current;
    const caret = input?.selectionStart ?? question.length;
    const before = question.slice(0, caret);
    const needsSpace = before.length > 0 && !/\s$/.test(before);
    const prefix = needsSpace ? " @" : "@";
    const start = before.length + (needsSpace ? 1 : 0);
    setQuestion(`${before}${prefix}${question.slice(caret)}`);
    setMentionState({ query: "", start, end: start + 1 });
    setMentionHighlight(0);
    placeCursorAfter(start + 1);
  }

  function handleQuestionChange(event: ChangeEvent<HTMLInputElement>) {
    const value = event.target.value;
    const caret = event.target.selectionStart ?? value.length;
    setQuestion(value);
    const before = value.slice(0, caret);
    const match = before.match(/(?:^|\s)@([^\s@[\]]*)$/);
    if (match) {
      const query = match[1];
      setMentionState({ query, start: caret - query.length - 1, end: caret });
      setMentionHighlight(0);
    } else if (mentionState) {
      setMentionState(null);
    }

  }

  function applyPaletteCommand(command: WorkspaceCommandDef) {
    if (command.args) {
      const next = `/${command.name} `;
      setQuestion(next);
      placeCursorAfter(next.length);
      return;
    }
    if (disabled) return;
    setQuestion("");
    onSubmit(`/${command.name}`);
  }

  function handleQuestionKeyDown(event: ReactKeyboardEvent<HTMLInputElement>) {
    if (event.nativeEvent.isComposing) return;
    if (paletteQuery !== null && paletteCandidates.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setPaletteIndex((current) => (current + 1) % paletteCandidates.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setPaletteIndex((current) => (current - 1 + paletteCandidates.length) % paletteCandidates.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        const candidate = paletteCandidates[paletteIndex] ?? paletteCandidates[0];
        const next = `/${candidate.name} `;
        setQuestion(next);
        placeCursorAfter(next.length);
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        applyPaletteCommand(paletteCandidates[paletteIndex] ?? paletteCandidates[0]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setQuestion("");
        return;
      }
    }
    if (mentionState && mentionCandidates.length) {
      if (event.key === "ArrowDown") {
        event.preventDefault();
        setMentionHighlight((current) => (current + 1) % mentionCandidates.length);
        return;
      }
      if (event.key === "ArrowUp") {
        event.preventDefault();
        setMentionHighlight((current) => (current - 1 + mentionCandidates.length) % mentionCandidates.length);
        return;
      }
      if (event.key === "Tab") {
        event.preventDefault();
        if (mentionCandidates.length === 1) {
          applyMention(mentionCandidates[0]);
        } else {
          setMentionHighlight((current) => (current + 1) % mentionCandidates.length);
        }
        return;
      }
      if (event.key === "Enter") {
        event.preventDefault();
        applyMention(mentionCandidates[mentionHighlight] ?? mentionCandidates[0]);
        return;
      }
      if (event.key === "Escape") {
        event.preventDefault();
        setMentionState(null);
        return;
      }
    }

  }

  return <>
    {draftError ? <div role="alert" className="inline-error">{draftError}</div> : null}
          {mentionState && mentionCandidates.length > 0 ? (
            <div className="mention-popover">
              {mentionCandidates.map((paper, i) => {
                const norm = normalizeWorkspacePaper(paper);
                return (
                  <button
                    key={workspacePaperId(norm)}
                    type="button"
                    className={`mention-popover-row ${i === mentionHighlight ? "mention-popover-row--active" : ""}`}
                    onMouseDown={(e) => { e.preventDefault(); applyMention(paper); }}
                  >
                    {workspacePaperTitle(norm)}
                  </button>
                );
              })}
              <span className="mention-popover-hint">Tab · Pfeiltasten · Enter zum Übernehmen</span>
            </div>
          ) : null}
          {paletteQuery !== null && paletteCandidates.length > 0 ? (
            <div className="command-palette-popover" role="listbox" aria-label="Befehle">
              <div className="command-palette-head">
                <Command size={13} />
                <span>Befehle</span>
              </div>
              {paletteCandidates.map((command, index) => (
                <button
                  type="button"
                  key={command.name}
                  className={`command-palette-row ${index === paletteIndex ? "command-palette-row--active" : ""}`}
                  onMouseDown={(event) => {
                    event.preventDefault();
                    applyPaletteCommand(command);
                  }}
                >
                  <code>
                    /{command.name}
                    {command.args ? <em> {command.args}</em> : null}
                  </code>
                  <span>{command.description}</span>
                  {command.group === "aktion" ? <small>Aktion</small> : null}
                </button>
              ))}
              <span className="mention-popover-hint">↑↓ wählen · Tab vervollständigen · Enter ausführen</span>
            </div>
          ) : null}
          {activeCommandHint ? (
            <div className="command-arg-hint">
              <code>/{activeCommandHint.name}</code>
              {activeCommandHint.args ? <em>{activeCommandHint.args}</em> : null}
              <span>{activeCommandHint.description}</span>
            </div>
          ) : null}
    <div className="chat-composer-input">
      <Bot size={18} />
      <input ref={questionInputRef} data-editor-key={draftKey} value={question} onChange={handleQuestionChange} onKeyDown={handleQuestionKeyDown} placeholder="Frage stellen — / für Befehle, @ für Papers" />
      <button className="icon-button chat-send-button" aria-label="Senden" disabled={disabled}><Send size={17} /></button>
    </div>
  </>;
});
