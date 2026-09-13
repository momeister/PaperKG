import { useEffect, useRef, useState } from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ChevronDown,
  ChevronRight,
  FileSearch,
  Loader2,
  Square,
  X,
} from "lucide-react";

import { api } from "../api";
import type {
  CreativityLevel,
  Task,
  TaskDeepSearchEvent,
  TaskDeepSearchResult,
  TaskResearchDirection,
} from "../types";

/**
 * Dialog für die Task-Modus-Tiefensuche.
 *
 * Vor dem Start lassen sich Tiefe (1-6) und Zweige (2-8) einstellen — analog
 * zur Tiefenanalyse. Während der Suche zeigt ein aufklappbarer Live-Feed
 * jede Phase, jede vom LLM gestellte Sub-Frage und jede geerntete Quelle.
 * Nach Abschluss kann das Ergebnis als neuer Turn in den Assistant-Pane
 * eingefügt werden (``onInsertAsTurn``).
 *
 * Modal-Bauweise folgt ``ParallelTransferDialog`` (modal-overlay/modal-card).
 * Live-Feed folgt ``AutoResearchProgress`` (aufklappbar, Loader2 + Stages).
 */
type Props = {
  task: Task;
  direction: TaskResearchDirection;
  /** Aktive Projekt-ID (oder null im globalen __all_papers__-Modus). */
  projectId: string | null;
  creativityLevel: CreativityLevel;
  provider?: string | null;
  model?: string | null;
  open: boolean;
  onClose: () => void;
  /** Wird beim Abschluss der Suche (done-Event) aufgerufen, damit der Aufrufer
   *  Status + Ergebnis sofort in seiner Richtungs-Liste übernehmen kann —
   *  unabhängig davon, ob der Nutzer danach "Als Turn einfügen" klickt. */
  onDone?: (result: TaskDeepSearchResult, direction: TaskResearchDirection) => void;
  onInsertAsTurn: (result: TaskDeepSearchResult, direction: TaskResearchDirection) => void;
};

type Phase = "idle" | "running" | "done" | "error";

/** Ein Eintrag im Live-Feed (append-only Log). */
type LogEntry = {
  id: number;
  kind: "phase" | "paper" | "grey" | "node" | "sub_questions" | "synthesis" | "error" | "done";
  text: string;
  depth?: number;
  path?: string[];
};

function phaseLabel(event: TaskDeepSearchEvent): { kind: LogEntry["kind"]; text: string } | null {
  switch (event.status) {
    case "planning":
      return { kind: "phase", text: `Plane Suche: "${event.query}"` };
    case "harvesting_papers":
      return { kind: "phase", text: `Suche Papers: "${event.query}"` };
    case "search_complete":
      return { kind: "phase", text: `${event.found} Paper-Treffer gefunden` };
    case "ingesting":
      return { kind: "paper", text: `Extrahiere: ${event.paper?.title ?? "Paper"}` };
    case "ingested":
      return { kind: "paper", text: `Extrahiert: ${event.paper?.title ?? "Paper"}` };
    case "ingest_failed":
      return { kind: "paper", text: `Fehlgeschlagen: ${event.paper?.title ?? "Paper"}` };
    case "papers_harvested":
      return { kind: "phase", text: `${event.count} Papers ins Projekt eingepflegt` };
    case "harvesting_grey":
      return { kind: "phase", text: `Suche Web-Quellen: "${event.query}"` };
    case "grey_search_complete":
      return { kind: "phase", text: `${event.found} Web-Treffer gefunden` };
    case "fetched":
      return { kind: "grey", text: `Web-Quelle: ${event.source?.title ?? event.source?.url ?? ""}` };
    case "grey_harvested":
      return { kind: "phase", text: `${event.count} Web-Quellen eingepflegt` };
    case "synthesizing":
      return { kind: "synthesis", text: `Synthese — Möglichkeitsprinzip (${event.papers} Papers, ${event.grey_sources} Web-Quellen)` };
    case "node_running":
      return { kind: "node", text: `Knoten (Tiefe ${event.depth}): ${event.question}` };
    case "node_done":
      return { kind: "node", text: `Knoten fertig (Tiefe ${event.depth}): ${event.question} — ${event.papers_count} Papers, ${event.grey_count} Web` };
    case "sub_questions":
      return { kind: "sub_questions", text: `Sub-Fragen (Tiefe ${event.depth}): ${event.questions.map((q) => `"${q}"`).join(", ")}` };
    case "harvest_error":
      return { kind: "error", text: `Harvest-Fehler (${event.phase}): ${event.error}` };
    case "error":
      return { kind: "error", text: `Fehler: ${event.error ?? "unbekannt"}` };
    case "done":
      return { kind: "done", text: `Fertig: ${event.papers_count} Papers, ${event.grey_count} Web-Quellen, ${event.node_count} Knoten` };
    default:
      return null;
  }
}

export function TaskDeepSearchDialog({
  task,
  direction,
  projectId,
  creativityLevel,
  provider = null,
  model = null,
  open,
  onClose,
  onDone,
  onInsertAsTurn,
}: Props) {
  const [depth, setDepth] = useState(3);
  const [branches, setBranches] = useState(3);
  const [maxPapers, setMaxPapers] = useState(20);
  const [maxWebSources, setMaxWebSources] = useState(10);
  const [advancedOpen, setAdvancedOpen] = useState(false);

  const [phase, setPhase] = useState<Phase>("idle");
  const [log, setLog] = useState<LogEntry[]>([]);
  const [currentText, setCurrentText] = useState<string>("");
  const [result, setResult] = useState<TaskDeepSearchResult | null>(null);
  const [feedExpanded, setFeedExpanded] = useState(true);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);

  const abortRef = useRef<AbortController | null>(null);
  const logIdRef = useRef(0);

  // Reset beim (Wieder-)Öffnen.
  useEffect(() => {
    if (open) {
      setDepth(3);
      setBranches(3);
      setMaxPapers(20);
      setMaxWebSources(10);
      setAdvancedOpen(false);
      setPhase("idle");
      setLog([]);
      setCurrentText("");
      setResult(null);
      setFeedExpanded(true);
      setErrorMsg(null);
      abortRef.current = null;
    }
  }, [open]);

  // Auto-scroll des Feeds nach unten.
  const feedRef = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (feedRef.current) {
      feedRef.current.scrollTop = feedRef.current.scrollHeight;
    }
  }, [log, currentText]);

  if (!open) return null;

  function appendLog(kind: LogEntry["kind"], text: string, opts?: { depth?: number; path?: string[] }) {
    logIdRef.current += 1;
    setLog((prev) => [...prev, { id: logIdRef.current, kind, text, depth: opts?.depth, path: opts?.path }]);
  }

  async function startSearch() {
    if (phase === "running") return;
    const controller = new AbortController();
    abortRef.current = controller;
    setPhase("running");
    setResult(null);
    setErrorMsg(null);
    setLog([]);
    appendLog("phase", `Starte Tiefensuche für "${direction.label}" (Tiefe ${depth}, ${branches} Zweige)`);

    const targetProjectId = projectId && projectId !== "__all_papers__" ? projectId : null;

    try {
      await api.tasks.streamDeepSearch(
        task.id,
        {
          direction,
          depth,
          branches,
          max_papers: maxPapers,
          max_web_sources: maxWebSources,
          creativity_level: creativityLevel,
          provider,
          model,
          target_project_id: targetProjectId,
        },
        (event) => {
          if (event.status === "done") {
            setResult(event);
            setPhase("done");
            setCurrentText("");
            appendLog("done", `${event.papers_count} Papers, ${event.grey_count} Web-Quellen, ${event.node_count} Knoten`);
            if (onDone) onDone(event, direction);
          } else if (event.status === "error" || event.status === "harvest_error") {
            const msg = event.status === "error" ? `Fehler: ${event.error ?? ""}` : `Harvest-Fehler (${event.phase}): ${event.error}`;
            setErrorMsg(msg);
            setPhase("error");
            setCurrentText("");
            appendLog("error", msg);
          } else {
            const label = phaseLabel(event);
            if (label) {
              setCurrentText(label.text);
              if (event.status === "node_running" || event.status === "node_done" || event.status === "sub_questions") {
                appendLog(label.kind, label.text, { depth: event.depth, path: event.path });
              } else {
                appendLog(label.kind, label.text);
              }
            }
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (controller.signal.aborted) {
        setPhase("idle");
        setCurrentText("Abgebrochen");
        appendLog("error", "Abgebrochen durch Nutzer");
        return;
      }
      const msg = err instanceof Error ? err.message : String(err);
      setErrorMsg(msg);
      setPhase("error");
      appendLog("error", msg);
    } finally {
      abortRef.current = null;
    }
  }

  function cancelSearch() {
    abortRef.current?.abort();
    abortRef.current = null;
    setPhase("idle");
    setCurrentText("Abgebrochen");
  }

  function handleInsert() {
    if (result) {
      onInsertAsTurn(result, direction);
      onClose();
    }
  }

  const running = phase === "running";

  return (
    <div
      className="harvest-dialog-overlay"
      role="dialog"
      aria-modal="true"
      aria-label="Tiefensuche konfigurieren"
      onMouseDown={(e) => e.target === e.currentTarget && phase !== "running" && onClose()}
    >
      <div className="harvest-dialog-card task-deep-search-dialog">
        <header className="task-deep-search-dialog-head">
          <div className="task-deep-search-dialog-title">
            <FileSearch size={16} />
            <strong>Tiefensuche — {direction.label}</strong>
          </div>
          <button
            type="button"
            className="icon-button"
            onClick={() => {
              cancelSearch();
              onClose();
            }}
            aria-label="Schließen"
          >
            <X size={16} />
          </button>
        </header>

        <div className="task-deep-search-dialog-body">
          {direction.rationale ? <p className="muted task-deep-search-direction-rationale">{direction.rationale}</p> : null}
          {direction.keywords?.length ? (
            <div className="task-research-direction-keywords">
              {direction.keywords.map((kw, i) => (
                <span key={i} className="task-research-direction-keyword">{kw}</span>
              ))}
            </div>
          ) : null}

          {/* Tiefe + Zweige — analog WorkspaceAssistantPane Tiefenanalyse-Controls */}
          <div className="task-deep-search-controls">
            <span className="chat-tool-wrap">
              <select
                aria-label="Tiefe"
                value={depth}
                onChange={(e) => setDepth(Number(e.target.value))}
                title="Rekursionstiefe — 1 = nur Wurzel, 6 = tiefer Baum"
                disabled={running}
                style={{ fontSize: "12px", padding: "2px 4px" }}
              >
                <option value={1}>Tiefe 1</option>
                <option value={2}>Tiefe 2</option>
                <option value={3}>Tiefe 3</option>
                <option value={4}>Tiefe 4</option>
                <option value={5}>Tiefe 5</option>
                <option value={6}>Tiefe 6</option>
              </select>
              <select
                aria-label="Verzweigungen"
                value={branches}
                onChange={(e) => setBranches(Number(e.target.value))}
                title="Anzahl Sub-Fragen pro Ebene"
                disabled={running}
                style={{ fontSize: "12px", padding: "2px 4px" }}
              >
                <option value={2}>2 Zweige</option>
                <option value={3}>3 Zweige</option>
                <option value={4}>4 Zweige</option>
                <option value={5}>5 Zweige</option>
                <option value={6}>6 Zweige</option>
                <option value={7}>7 Zweige</option>
                <option value={8}>8 Zweige</option>
              </select>
            </span>
            <button
              type="button"
              className="icon-button task-deep-search-advanced-toggle"
              onClick={() => setAdvancedOpen((v) => !v)}
              aria-label={advancedOpen ? "Erweiterte Optionen ausblenden" : "Erweiterte Optionen einblenden"}
              title="Erweitert: max. Papers / Web-Quellen pro Knoten"
            >
              {advancedOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span className="muted" style={{ fontSize: "12px" }}>Erweitert</span>
            </button>
          </div>

          {advancedOpen ? (
            <div className="task-deep-search-advanced">
              <label className="task-deep-search-advanced-label">
                <span>Max. Papers / Knoten</span>
                <input
                  type="number"
                  min={1}
                  max={50}
                  value={maxPapers}
                  onChange={(e) => setMaxPapers(Math.max(1, Math.min(50, Number(e.target.value) || 20)))}
                  disabled={running}
                  style={{ width: "64px", fontSize: "12px", padding: "2px 4px" }}
                />
              </label>
              <label className="task-deep-search-advanced-label">
                <span>Max. Web-Quellen / Knoten</span>
                <input
                  type="number"
                  min={0}
                  max={30}
                  value={maxWebSources}
                  onChange={(e) => setMaxWebSources(Math.max(0, Math.min(30, Number(e.target.value) || 10)))}
                  disabled={running}
                  style={{ width: "64px", fontSize: "12px", padding: "2px 4px" }}
                />
              </label>
            </div>
          ) : null}

          {/* Live-Progress-Feed — aufklappbar */}
          <div className="task-deep-search-progress-card">
            <div className="task-deep-search-progress-head">
              <div className="task-deep-search-progress-head-main">
                {running ? <Loader2 size={14} className="spin" /> : phase === "done" ? <CheckCircle2 size={14} /> : phase === "error" ? <AlertTriangle size={14} /> : null}
                <div className="task-deep-search-progress-head-text">
                  <strong>{running ? "Tiefensuche läuft …" : phase === "done" ? "Tiefensuche abgeschlossen" : phase === "error" ? "Fehler" : "Bereit"}</strong>
                  {currentText ? <span className="muted task-deep-search-progress-current">{currentText}</span> : null}
                </div>
              </div>
              <div className="task-deep-search-progress-head-actions">
                {log.length > 0 ? (
                  <button
                    type="button"
                    className="icon-button"
                    aria-label={feedExpanded ? "Feed ausblenden" : "Feed einblenden"}
                    title={feedExpanded ? "Feed ausblenden" : "Feed einblenden"}
                    onClick={() => setFeedExpanded((v) => !v)}
                  >
                    {feedExpanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
                  </button>
                ) : null}
                {running ? (
                  <button
                    type="button"
                    className="icon-button"
                    aria-label="Tiefensuche abbrechen"
                    title="Tiefensuche abbrechen"
                    onClick={cancelSearch}
                  >
                    <Square size={14} />
                  </button>
                ) : null}
              </div>
            </div>

            {feedExpanded && log.length > 0 ? (
              <div className="task-deep-search-feed" ref={feedRef}>
                <ol className="task-deep-search-feed-list">
                  {log.map((entry) => (
                    <li
                      key={entry.id}
                      className={`task-deep-search-feed-item task-deep-search-feed-${entry.kind}`}
                      data-depth={entry.depth ?? undefined}
                    >
                      <span className="task-deep-search-feed-icon">
                        {entry.kind === "done" ? (
                          <CheckCircle2 size={12} />
                        ) : entry.kind === "error" ? (
                          <AlertTriangle size={12} />
                        ) : entry.kind === "node" ? (
                          <FileSearch size={12} />
                        ) : entry.kind === "sub_questions" ? (
                          <ChevronRight size={12} />
                        ) : (
                          <ChevronRight size={12} />
                        )}
                      </span>
                      <span className="task-deep-search-feed-text">{entry.text}</span>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}
          </div>

          {errorMsg ? <div className="warning-row task-deep-search-error-row">{errorMsg}</div> : null}

          {phase === "done" && result ? (
            <div className="task-deep-search-result-summary">
              <strong>
                📎 {result.papers_count} Papers · {result.grey_count} Web-Quellen
                {result.node_count ? ` · ${result.node_count} Knoten` : ""}
              </strong>
              <p className="muted">
                Die Papiere und Web-Quellen wurden ins Projekt eingepflegt. Füge die Synthese als
                neuen Turn in den Assistant ein, oder starte eine neue Suche.
              </p>
            </div>
          ) : null}
        </div>

        <footer className="task-deep-search-dialog-foot">
          <button type="button" className="button button-ghost" onClick={onClose} disabled={running}>
            Schließen
          </button>
          {phase !== "done" ? (
            <button
              type="button"
              className="button button-primary"
              onClick={startSearch}
              disabled={running}
            >
              {running ? <Loader2 size={14} className="spin" /> : <FileSearch size={14} />}
              <span>{running ? "Läuft …" : "Tiefensuche starten"}</span>
            </button>
          ) : (
            <button
              type="button"
              className="button button-primary"
              onClick={handleInsert}
              title="Ergebnis als neuen Turn in den Assistant einfügen"
            >
              <FileSearch size={14} />
              <span>Als neuen Turn einfügen</span>
            </button>
          )}
        </footer>
      </div>
    </div>
  );
}