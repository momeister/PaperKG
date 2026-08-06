import { useEffect, useRef, useState } from "react";
import { AlertTriangle, ArrowRight, Compass, Loader2, Plus, RefreshCcw, Search, X } from "lucide-react";

import { api } from "../api";
import { CreativitySlider } from "../components/CreativitySlider";
import type {
  CreativityLevel,
  Task,
  TaskDeepSearchEvent,
  TaskDeepSearchResult,
  TaskResearchDirection,
  TaskSuggestDirectionsResponse,
} from "../types";
import { AnswerWithCitations } from "./ParallelResearchPanel";

/**
 * Forschungsrichtungen-Sektion (Task-Focused Mode).
 *
 * Lädt via ``POST /tasks/{id}/suggest-directions`` eine Liste von Forschungs-
 * richtungen (Kreativitätsstufe 1–5). Der Nutzer wählt eine Richtung an →
 * der Aufrufer kann daraufhin einen Implementationsplan erzeugen.
 *
 * Zusätzlich kann pro Richtung eine **Tiefensuche** gestartet werden
 * (``POST /tasks/{id}/deep-search``): viele Paper + Web-Quellen werden
 * geerntet, extrahiert und ins Projekt eingepflegt, danach erzeugt der
 * Assistant ein geerdetes Möglichkeitsprinzip (Machbarkeit / Ansätze /
 * Risiken). Gefällt die Richtung, wird sie über ``onStartParallel`` in den
 * Parallel-Modus überführt.
 */
type Props = {
  task: Task;
  creativityLevel: CreativityLevel;
  provider?: string | null;
  model?: string | null;
  /** Wenn gesetzt, wird diese Richtung als "ausgewählt" markiert. */
  selectedLabel?: string | null;
  onSelect: (direction: TaskResearchDirection) => void;
  /** Tiefensuche abgeschlossen → Richtung in den Parallel-Modus überführen. */
  onStartParallel: (direction: TaskResearchDirection) => void;
  /** Optional: schon vorhandene Richtungen aus task_json.suggested_directions. */
  initialDirections?: TaskResearchDirection[];
};

type DeepStatus = "idle" | "running" | "done" | "error";

function progressTextFor(event: TaskDeepSearchEvent): string {
  switch (event.status) {
    case "planning":
      return "Plane Suche…";
    case "harvesting_papers":
      return "Suche Papers…";
    case "search_complete":
      return `${event.found} Paper-Treffer`;
    case "ingesting":
      return `Extrahiere: ${event.paper?.title ?? "Paper"}…`;
    case "ingested":
      return `Extrahiert: ${event.paper?.title ?? "Paper"}`;
    case "ingest_failed":
      return `Fehlgeschlagen: ${event.paper?.title ?? "Paper"}`;
    case "papers_harvested":
      return `${event.count} Papers eingepflegt`;
    case "harvesting_grey":
      return "Suche Web-Quellen…";
    case "grey_search_complete":
      return `${event.found} Web-Treffer`;
    case "fetched":
      return `Web-Quelle: ${event.source?.title ?? event.source?.url ?? ""}`;
    case "grey_harvested":
      return `${event.count} Web-Quellen eingepflegt`;
    case "synthesizing":
      return "Synthese — Möglichkeitsprinzip…";
    case "harvest_error":
      return `Harvest-Fehler (${event.phase})`;
    case "error":
      return `Fehler: ${event.error ?? "unbekannt"}`;
    case "done":
      return "Fertig";
    default:
      return "";
  }
}

export function TaskResearchSuggestions({
  task,
  creativityLevel,
  provider = null,
  model = null,
  selectedLabel,
  onSelect,
  onStartParallel,
  initialDirections = [],
}: Props) {
  const [directions, setDirections] = useState<TaskResearchDirection[]>(initialDirections);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creativity, setCreativity] = useState<CreativityLevel>(creativityLevel);
  const [expanded, setExpanded] = useState(false);

  // Per-Richtung Tiefensuche-Status.
  const [deepStatus, setDeepStatus] = useState<Record<string, DeepStatus>>({});
  const [deepProgress, setDeepProgress] = useState<Record<string, string>>({});
  const [deepResult, setDeepResult] = useState<Record<string, TaskDeepSearchResult>>(
    () => task.task_json.deep_searches ?? {},
  );
  const [deepOpen, setDeepOpen] = useState<Record<string, boolean>>({});
  const deepAbortRef = useRef<Record<string, AbortController | null>>({});

  // Wenn sich die Task ändert, die gespeicherten Tiefensuchen übernehmen.
  useEffect(() => {
    setDeepResult(task.task_json.deep_searches ?? {});
    setDeepStatus({});
    setDeepProgress({});
    setDeepOpen({});
  }, [task.id]);

  async function loadDirections() {
    setBusy(true);
    setError(null);
    try {
      const res: TaskSuggestDirectionsResponse = await api.tasks.suggestDirections(task.id, {
        creativity_level: creativity,
        provider,
        model,
      });
      setDirections(res.directions ?? []);
      setExpanded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  async function runDeepSearch(dir: TaskResearchDirection) {
    // Bereits laufende Suche für diese Richtung abbrechen (Toggle).
    const existing = deepAbortRef.current[dir.label];
    if (existing) {
      existing.abort();
      deepAbortRef.current[dir.label] = null;
      setDeepStatus((s) => ({ ...s, [dir.label]: "idle" }));
      return;
    }

    const controller = new AbortController();
    deepAbortRef.current[dir.label] = controller;
    setDeepStatus((s) => ({ ...s, [dir.label]: "running" }));
    setDeepProgress((p) => ({ ...p, [dir.label]: "Starte Tiefensuche…" }));
    setDeepOpen((o) => ({ ...o, [dir.label]: true }));

    try {
      await api.tasks.streamDeepSearch(
        task.id,
        { direction: dir, creativity_level: creativity, provider, model },
        (event) => {
          if (event.status === "done") {
            setDeepResult((r) => ({ ...r, [dir.label]: event }));
            setDeepStatus((s) => ({ ...s, [dir.label]: "done" }));
            setDeepProgress((p) => ({ ...p, [dir.label]: "Fertig" }));
          } else if (event.status === "error" || event.status === "harvest_error") {
            setDeepStatus((s) => ({ ...s, [dir.label]: "error" }));
            setDeepProgress((p) => ({
              ...p,
              [dir.label]: event.status === "error" ? `Fehler: ${event.error ?? ""}` : `Harvest-Fehler`,
            }));
          } else {
            setDeepProgress((p) => ({ ...p, [dir.label]: progressTextFor(event) }));
          }
        },
        controller.signal,
      );
    } catch (err) {
      if (controller.signal.aborted) return;
      setDeepStatus((s) => ({ ...s, [dir.label]: "error" }));
      setDeepProgress((p) => ({ ...p, [dir.label]: err instanceof Error ? err.message : String(err) }));
    } finally {
      deepAbortRef.current[dir.label] = null;
    }
  }

  return (
    <div className="task-research-suggestions">
      <div className="task-research-suggestions-head">
        <div className="task-research-suggestions-title">
          <Compass size={16} />
          <strong>Forschungsrichtungen</strong>
          {directions.length ? <span className="muted">· {directions.length} Vorschläge</span> : null}
        </div>
        <button type="button" className="button button-compact" onClick={loadDirections} disabled={busy}>
          {busy ? <RefreshCcw size={13} className={busy ? "spin" : ""} /> : <Plus size={13} />}
          <span>{busy ? "Lade…" : directions.length ? "Neu vorschlagen" : "Vorschlagen"}</span>
        </button>
      </div>

      <CreativitySlider
        value={creativity}
        onChange={setCreativity}
        compact
        label="Kreativität für Vorschläge"
        hint="Nur für diese Vorschlag-Generierung — beeinflusst nicht den Implementationsplan oder Parallele Sessions."
        tooltip="Lokale Kreativität — gilt ausschließlich für die Forschungsrichtungen-Vorschläge dieser Task."
      />

      {error ? <div className="warning-row">{error}</div> : null}

      {directions.length ? (
        <div className={`task-research-direction-list${expanded ? " expanded" : ""}`}>
          {directions.map((dir, i) => {
            const isSelected = selectedLabel === dir.label;
            const status = deepStatus[dir.label] ?? (deepResult[dir.label] ? "done" : "idle");
            const progress = deepProgress[dir.label];
            const result = deepResult[dir.label];
            const isOpen = deepOpen[dir.label] ?? false;
            const running = status === "running";

            return (
              <div
                key={i}
                className={`task-research-direction${isSelected ? " selected" : ""}${status === "done" ? " deep-done" : ""}`}
              >
                <button
                  type="button"
                  className="task-research-direction-select"
                  onClick={() => onSelect(dir)}
                  disabled={busy}
                >
                  <div className="task-research-direction-head">
                    <span className="task-research-direction-label">{dir.label}</span>
                    {isSelected ? <span className="task-research-direction-check">✓</span> : null}
                  </div>
                  {dir.rationale ? <p className="task-research-direction-rationale">{dir.rationale}</p> : null}
                  {dir.keywords?.length ? (
                    <div className="task-research-direction-keywords">
                      {dir.keywords.map((kw, j) => (
                        <span key={j} className="task-research-direction-keyword">{kw}</span>
                      ))}
                    </div>
                  ) : null}
                </button>

                <div className="task-research-direction-actions">
                  <button
                    type="button"
                    className="button button-compact task-deep-search-btn"
                    onClick={() => runDeepSearch(dir)}
                    disabled={busy && !running}
                    title={running ? "Tiefensuche abbrechen" : "Tiefensuche: viele Paper + Web-Quellen extrahieren, Möglichkeitsprinzip erzeugen"}
                  >
                    {running ? <X size={13} /> : status === "done" ? <RefreshCcw size={13} /> : <Search size={13} />}
                    <span>
                      {running ? "Abbrechen" : status === "done" ? "Erneute Tiefensuche" : "Tiefensuche"}
                    </span>
                  </button>
                  {status === "done" && result ? (
                    <button
                      type="button"
                      className="button button-compact task-transfer-parallel-btn"
                      onClick={() => onStartParallel(dir)}
                      title="Diese Richtung in den Parallel-Modus überführen — Variante ausarbeiten"
                    >
                      <ArrowRight size={13} />
                      <span>In Parallel überführen</span>
                    </button>
                  ) : null}
                </div>

                {running && progress ? (
                  <div className="task-deep-search-progress">
                    <Loader2 size={12} className="spin" />
                    <span>{progress}</span>
                  </div>
                ) : null}

                {status === "error" && progress ? (
                  <div className="task-deep-search-error">
                    <AlertTriangle size={12} />
                    <span>{progress}</span>
                  </div>
                ) : null}

                {status === "done" && result ? (
                  <div className="task-deep-search-result">
                    <button
                      type="button"
                      className="task-deep-search-toggle"
                      onClick={() => setDeepOpen((o) => ({ ...o, [dir.label]: !isOpen }))}
                    >
                      <strong>
                        📎 {result.papers_count} Papers · {result.grey_count} Web-Quellen ins Projekt eingepflegt
                      </strong>
                      <span className="muted">{isOpen ? "einklappen" : "anzeigen"}</span>
                    </button>
                    {isOpen ? (
                      <div className="task-deep-search-summary">
                        {result.summary ? <AnswerWithCitations answer={result.summary} onOpenCitation={() => {}} /> : null}
                      </div>
                    ) : null}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : !busy ? (
        <p className="muted task-research-empty">
          Noch keine Vorschläge. Mit „Vorschlagen“ lässt der Assistant Forschungsrichtungen aus der Task-Spec ableiten —
          die Kreativitätsstufe steuert, wie konventionell (1) oder cross-domain (5) sie ausfallen.
        </p>
      ) : null}
    </div>
  );
}