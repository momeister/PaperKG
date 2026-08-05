/**
 * Das Gespräch über den Code — mehrere Züge, nicht eine Frage.
 *
 * `CodeAskPanel` war trotz des Namens nie ein Chat: ein einziger
 * Antwort-Zustand, bei jeder Frage ersetzt, keine Rückfrage, keine Liste. Es
 * bleibt so, weil es in der schmalen Werkstatt-Spalte genau richtig ist. Hier
 * geht es um die andere Frageform: *wo passiert dieses Feature* — und deren
 * Antwort ist nicht ein Absatz, sondern ein Absatz **plus einer Liste, die man
 * abarbeiten kann**.
 *
 * Drei Dinge trägt diese Ansicht deshalb, die die Einzelfrage nicht hat:
 *
 *   * **Der Faden.** Rückfragen sind der Normalfall beim Verstehen. Die
 *     Zitierlizenz wächst dabei mit dem Gespräch (siehe `context_build`
 *     `extend`) — eine Rückfrage darf zitieren, was Runde eins gezeigt bekam.
 *   * **Die Trefferliste** mit dem Grund je Zeile. „Zitiert" und „vorab-suche"
 *     sehen sonst gleich aus und sind es nicht.
 *   * **Der Wolkenhinweis**, wenn das Modell nicht auf diesem Rechner lief.
 *
 * `AnswerView` wird unverändert wiederverwendet — die Belegprüfung sieht in
 * beiden Ansichten gleich aus, weil sie dieselbe ist.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Maximize2,
  MessageSquarePlus,
  Minimize2,
  Pencil,
  Send,
  Sparkles,
  Trash2,
} from "lucide-react";

import { api, streamCodeChat } from "../../api";
import { noteProjectId } from "../../projectScope";
import type {
  CodeAnswer,
  CodeAskEvent,
  CodeChatTurn,
  CodeFocusNode,
  CodeNodeId,
} from "../../types";
import { AnswerView } from "../CodeAskPanel";
import { Relevance, kindLabel } from "./shared";

/** Der Grund, aus dem ein Symbol auf der Liste steht — stärkster zuerst. */
const WHY_META: Record<CodeFocusNode["why"], { label: string; hint: string }> = {
  zitiert: {
    label: "belegt zitiert",
    hint: "Diese Stelle steht mit Beleg in der Antwort.",
  },
  spur: {
    label: "in der Spur",
    hint: "Das Modell nennt diese Stelle als Schritt in seinem Weg.",
  },
  nachgeschlagen: {
    label: "nachgeschlagen",
    hint: "Das Modell hat diese Stelle im Gespräch tatsächlich geöffnet.",
  },
  "vorab-suche": {
    label: "nur gefunden",
    hint: "Die Suche hat diese Stelle in den Kontext gelegt. Ob das Modell sie gelesen hat, ist offen.",
  },
};

export type CodeChatPanelProps = {
  projectId: string;
  onOpen: (path: string, line: number) => void;
  onSelectNode: (nodeId: CodeNodeId) => void;
  /** Die Treffer eines Zugs auf der Karte zeigen. */
  onShowFeature: (nodes: CodeFocusNode[]) => void;
  /**
   * Diese eine Funktion bearbeiten. Öffnet den Editor in der Mittelspalte —
   * hier einen zweiten Monaco einzuhängen zöge fünf Worker-Chunks in den Chunk
   * dieser Seite, für einen Knopf, den man selten drückt.
   */
  onEditNode: (nodeId: CodeNodeId) => void;
  llm: { provider?: string; model?: string };
  expanded?: boolean;
  onToggleExpanded?: () => void;
};

/** Ein Zug, während er läuft oder nachdem er gespeichert wurde. */
type Turn = { question: string; answer: CodeAnswer | null; error?: string | null };

function turnFromRecord(record: CodeChatTurn): Turn {
  return {
    question: record.question,
    answer: {
      id: record.id,
      question: record.question,
      answer: record.answer,
      trail_text: "",
      citations: record.citations,
      trail: record.trail,
      verdict: record.verdict ?? "uncited",
      verdict_label: "",
      is_clean: record.verdict === "sound",
      quote_mismatches: [],
      uncited_sentences: 0,
      tool_calls: record.tool_calls,
      truncated: record.truncated,
      tool_calling_fallback: false,
      provider: record.provider ?? "",
      model: record.model ?? "",
      papers: record.papers,
      paper_citations: [],
      bare_citations: 0,
      focus_nodes: record.focus_nodes,
      remote_model: record.remote_model,
    },
  };
}

function FocusList({
  nodes,
  onOpen,
  onSelectNode,
  onShowFeature,
  onEditNode,
}: {
  nodes: CodeFocusNode[];
  onOpen: (path: string, line: number) => void;
  onSelectNode: (nodeId: CodeNodeId) => void;
  onShowFeature: (nodes: CodeFocusNode[]) => void;
  onEditNode: (nodeId: CodeNodeId) => void;
}) {
  // Nach Bereich gruppiert: „diese vier liegen in parsing/, die zwei in api/"
  // sagt mehr über ein Feature als eine flache Liste von zwölf Namen.
  const groups = useMemo(() => {
    const byArea = new Map<string, CodeFocusNode[]>();
    for (const node of nodes) {
      const area = node.path.includes("/") ? node.path.split("/")[0] : ".";
      const list = byArea.get(area);
      if (list) list.push(node);
      else byArea.set(area, [node]);
    }
    return [...byArea.entries()].sort((a, b) => b[1].length - a[1].length);
  }, [nodes]);

  if (!nodes.length) return null;

  return (
    <div className="cgp-focus">
      <div className="cgp-focus-head">
        <strong>Zugehörige Funktionen</strong>
        <span className="cg-count">{nodes.length}</span>
        <span className="cgp-spacer" />
        <button className="wk-btn" onClick={() => onShowFeature(nodes)}>
          auf der Karte zeigen
        </button>
      </div>
      {groups.map(([area, members]) => (
        <div key={area} className="cgp-focus-group">
          <span className="cgp-focus-area">{area}</span>
          {members.map((node) => {
            const meta = WHY_META[node.why];
            return (
              <div key={node.id} className="cg-row cg-row--symbol">
                <button
                  className="cg-row-main"
                  onClick={() => onSelectNode(node.id)}
                  title="Bauplan öffnen"
                >
                  <Relevance value={node.relevance} />
                  <span className="cg-kind">{kindLabel(node.kind)}</span>
                  <span className="cg-qualified">{node.qualified}</span>
                </button>
                <span
                  className={`cgp-why cgp-why--${node.why.replace("-", "")}`}
                  title={meta.hint}
                >
                  {meta.label}
                </span>
                <button
                  className="cgp-iconlink"
                  onClick={() => onEditNode(node.id)}
                  title="Nur diese Funktion bearbeiten"
                >
                  <Pencil size={12} />
                </button>
                <button
                  className="cg-row-meta cg-row-link"
                  onClick={() => onOpen(node.path, node.line)}
                  title="In der Werkstatt öffnen"
                >
                  {node.path}:{node.line}
                </button>
              </div>
            );
          })}
        </div>
      ))}
    </div>
  );
}

export function CodeChatPanel({
  projectId,
  onOpen,
  onSelectNode,
  onShowFeature,
  onEditNode,
  llm,
  expanded,
  onToggleExpanded,
}: CodeChatPanelProps) {
  const queryClient = useQueryClient();
  const [chatId, setChatId] = useState<string | null>(null);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [question, setQuestion] = useState("");
  const [usePapers, setUsePapers] = useState(false);
  const [activity, setActivity] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);
  const endRef = useRef<HTMLDivElement>(null);

  const chats = useQuery({
    queryKey: ["codegraph", "chats", projectId],
    queryFn: () => api.codegraph.chats(projectId, 30),
    enabled: Boolean(projectId),
  });

  // Projektwechsel: alles zurück. Ein Gespräch gehört zu genau einem Projekt,
  // und die Zitierlizenz dahinter erst recht.
  useEffect(() => {
    setChatId(null);
    setTurns([]);
    setActivity([]);
    setError(null);
  }, [projectId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [turns.length, activity.length]);

  const openChat = useCallback(
    async (id: string) => {
      const body = await api.codegraph.chat(projectId, id);
      setChatId(id);
      setTurns(body.turns.map(turnFromRecord));
      setActivity([]);
      setError(null);
    },
    [projectId],
  );

  const ask = useMutation({
    mutationFn: async (text: string) => {
      abortRef.current?.abort();
      const controller = new AbortController();
      abortRef.current = controller;

      // Das Gespräch entsteht erst mit der ersten Frage — sonst sammelten sich
      // leere Fäden an, sobald jemand den Tab nur aufmacht.
      let id = chatId;
      if (!id) {
        const created = await api.codegraph.createChat(projectId, {
          project_id: noteProjectId() ?? null,
        });
        id = created.id;
        setChatId(id);
      }

      setActivity([]);
      setError(null);
      setTurns((current) => [...current, { question: text, answer: null }]);

      await streamCodeChat(
        projectId,
        id,
        {
          question: text,
          provider: llm.provider ?? null,
          model: llm.model ?? null,
          project_id: noteProjectId() ?? null,
          use_papers: usePapers,
        },
        (event: CodeAskEvent) => {
          if (event.event === "activity") {
            setActivity((current) => [...current, event.text]);
          } else if (event.event === "failed") {
            setError(event.error);
            setTurns((current) =>
              current.map((turn, index) =>
                index === current.length - 1 ? { ...turn, error: event.error } : turn,
              ),
            );
          } else if (event.event === "done") {
            setTurns((current) =>
              current.map((turn, index) =>
                index === current.length - 1 ? { ...turn, answer: event.answer } : turn,
              ),
            );
          }
        },
        controller.signal,
      );
    },
    onSettled: () => {
      setActivity([]);
      void queryClient.invalidateQueries({ queryKey: ["codegraph", "chats", projectId] });
    },
  });

  function submit() {
    const text = question.trim();
    if (!text || ask.isPending) return;
    setQuestion("");
    ask.mutate(text);
  }

  const remote = turns.some((turn) => turn.answer?.remote_model);

  return (
    <div className="cgp-pane cgp-chat">
      <div className="cgp-pane-head">
        <strong>Gespräch</strong>
        <span className="cgp-spacer" />
        {remote && (
          <span
            className="topbar-hint"
            title="Mindestens ein Zug lief auf fremden Servern (:cloud-Modell)."
          >
            ☁ nicht lokal
          </span>
        )}
        <select
          className="cgp-mini-select"
          value={chatId ?? ""}
          onChange={(event) => {
            const id = event.target.value;
            if (!id) {
              setChatId(null);
              setTurns([]);
              return;
            }
            void openChat(id);
          }}
          aria-label="Gespräch wählen"
        >
          <option value="">neues Gespräch</option>
          {(chats.data?.chats ?? []).map((chat) => (
            <option key={chat.id} value={chat.id}>
              {chat.title || chat.id.slice(0, 10)}
            </option>
          ))}
        </select>
        <button
          className="wk-iconbtn"
          title="Neues Gespräch — die Zitierlizenz beginnt dann wieder leer"
          onClick={() => {
            setChatId(null);
            setTurns([]);
            setActivity([]);
            setError(null);
          }}
        >
          <MessageSquarePlus size={14} />
        </button>
        {chatId && (
          <button
            className="wk-iconbtn"
            title="Gespräch löschen"
            onClick={async () => {
              await api.codegraph.removeChat(projectId, chatId);
              setChatId(null);
              setTurns([]);
              void queryClient.invalidateQueries({
                queryKey: ["codegraph", "chats", projectId],
              });
            }}
          >
            <Trash2 size={14} />
          </button>
        )}
        {onToggleExpanded && (
          <button
            className="wk-iconbtn"
            title={expanded ? "Wieder verkleinern" : "Gespräch vergrössern"}
            onClick={onToggleExpanded}
          >
            {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        )}
      </div>

      <div className="cgp-pane-body cgp-chat-thread">
        {!turns.length && !ask.isPending && (
          <p className="muted cg-empty">
            Frag nach einem Feature, nicht nach einem Namen — etwa „Wo werden die Passwörter
            verschlüsselt?". Die Antwort kommt mit Belegen, einer Liste aller beteiligten
            Funktionen und einer Karte darüber.
          </p>
        )}

        {turns.map((turn, index) => (
          <div key={index} className="cgp-turn">
            <div className="cgp-turn-question">{turn.question}</div>
            {turn.error ? (
              <p className="cg-notice cg-notice--error">{turn.error}</p>
            ) : turn.answer ? (
              <>
                <AnswerView
                  answer={turn.answer}
                  onOpen={onOpen}
                  projectId={projectId}
                  notesProjectId={noteProjectId() ?? undefined}
                />
                <FocusList
                  nodes={turn.answer.focus_nodes ?? []}
                  onOpen={onOpen}
                  onSelectNode={onSelectNode}
                  onShowFeature={onShowFeature}
                  onEditNode={onEditNode}
                />
              </>
            ) : (
              <ul className="cgp-activity">
                {activity.map((line, position) => (
                  <li key={position}>
                    <Sparkles size={12} /> {line}
                  </li>
                ))}
                {!activity.length && <li className="muted">denkt nach …</li>}
              </ul>
            )}
          </div>
        ))}
        {error && !turns.length && <p className="cg-notice cg-notice--error">{error}</p>}
        <div ref={endRef} />
      </div>

      <div className="cgp-chat-input">
        <label className="cgp-inline-check" title="Lokale Papers als zusätzliche Quelle heranziehen">
          <input
            type="checkbox"
            checked={usePapers}
            onChange={(event) => setUsePapers(event.target.checked)}
          />
          Papers
        </label>
        <textarea
          value={question}
          rows={2}
          placeholder="Wo passiert …?"
          onChange={(event) => setQuestion(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.shiftKey) {
              event.preventDefault();
              submit();
            }
          }}
        />
        <button
          className="wk-btn wk-btn--primary"
          disabled={ask.isPending || !question.trim()}
          onClick={submit}
        >
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}

export default CodeChatPanel;
