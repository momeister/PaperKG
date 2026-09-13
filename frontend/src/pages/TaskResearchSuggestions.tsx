import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, ArrowRight, Compass, Loader2, Plus, RefreshCcw, Search } from "lucide-react";

import { api } from "../api";
import { CreativitySlider } from "../components/CreativitySlider";
import type {
  CreativityLevel,
  Task,
  TaskDeepSearchResult,
  TaskResearchDirection,
  TaskSuggestDirectionsResponse,
} from "../types";
import { AnswerWithCitations } from "./ParallelResearchPanel";
import { TaskDeepSearchDialog } from "./TaskDeepSearchDialog";

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
  /** Aktuelle Projekt-ID (für target_project_id beim Bugfix-Attach). */
  projectId?: string | null;
  /** Wenn gesetzt, wird diese Richtung als "ausgewählt" markiert. */
  selectedLabel?: string | null;
  onSelect: (direction: TaskResearchDirection) => void;
  /** Tiefensuche abgeschlossen → Richtung in den Parallel-Modus überführen. */
  onStartParallel: (direction: TaskResearchDirection) => void;
  /** Tiefensuche-Ergebnis als neuen Assistant-Turn einfügen (Bibliothek + Chat). */
  onInsertAsTurn?: (result: TaskDeepSearchResult, direction: TaskResearchDirection) => void;
  /** Optional: schon vorhandene Richtungen aus task_json.suggested_directions. */
  initialDirections?: TaskResearchDirection[];
};

type DeepStatus = "idle" | "running" | "done" | "error";

export function TaskResearchSuggestions({
  task,
  creativityLevel,
  provider = null,
  model = null,
  projectId = null,
  selectedLabel,
  onSelect,
  onStartParallel,
  onInsertAsTurn,
  initialDirections = [],
}: Props) {
  const [directions, setDirections] = useState<TaskResearchDirection[]>(initialDirections);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creativity, setCreativity] = useState<CreativityLevel>(creativityLevel);
  const [expanded, setExpanded] = useState(false);

  // Per-Richtung Tiefensuche-Status (wird vom Dialog gesetzt).
  const [deepStatus, setDeepStatus] = useState<Record<string, DeepStatus>>({});
  const [deepResult, setDeepResult] = useState<Record<string, TaskDeepSearchResult>>(
    () => task.task_json.deep_searches ?? {},
  );
  const [deepOpen, setDeepOpen] = useState<Record<string, boolean>>({});
  // Aktuell im Dialog geöffnete Richtung (null = Dialog zu).
  const [dialogDirection, setDialogDirection] = useState<TaskResearchDirection | null>(null);

  // Wenn sich die Task ändert, die gespeicherten Tiefensuchen übernehmen.
  useEffect(() => {
    setDeepResult(task.task_json.deep_searches ?? {});
    setDeepStatus({});
    setDeepOpen({});
  }, [task.id]);

  // Stabile Dialog-Callbacks — verhindern, dass der Dialog bei jedem Render
  // neu gemountet wird und seinen State verliert.
  const dialogProps = useMemo(
    () => ({
      task,
      projectId: projectId ?? null,
      creativityLevel: creativity,
      provider,
      model,
    }),
    [task, projectId, creativity, provider, model],
  );

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

  /** Vom Dialog nach Abschluss aufgerufen: Status + Ergebnis übernehmen. */
  function handleDialogResult(dir: TaskResearchDirection, result: TaskDeepSearchResult | null, status: DeepStatus) {
    if (result) {
      setDeepResult((r) => ({ ...r, [dir.label]: result }));
    }
    setDeepStatus((s) => ({ ...s, [dir.label]: status }));
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
                    onClick={() => setDialogDirection(dir)}
                    disabled={busy}
                    title={running ? "Tiefensuche läuft im Dialog" : "Tiefensuche: viele Paper + Web-Quellen extrahieren, Möglichkeitsprinzip erzeugen"}
                  >
                    {running ? <Loader2 size={13} className="spin" /> : status === "done" ? <RefreshCcw size={13} /> : <Search size={13} />}
                    <span>
                      {running ? "Läuft …" : status === "done" ? "Erneute Tiefensuche" : "Tiefensuche"}
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

                {running ? (
                  <div className="task-deep-search-progress">
                    <Loader2 size={12} className="spin" />
                    <span>Tiefensuche läuft im Dialog …</span>
                  </div>
                ) : null}

                {status === "error" ? (
                  <div className="task-deep-search-error">
                    <AlertTriangle size={12} />
                    <span>Suche fehlgeschlagen — bitte im Dialog erneut versuchen.</span>
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
                        {onInsertAsTurn ? (
                          <button
                            type="button"
                            className="button button-compact task-deep-search-insert-turn-btn"
                            onClick={() => onInsertAsTurn(result, dir)}
                            title="Dieses Ergebnis als neuen Turn in den Assistant einfügen"
                          >
                            <ArrowRight size={13} />
                            <span>Als Turn einfügen</span>
                          </button>
                        ) : null}
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

      {dialogDirection ? (
        <TaskDeepSearchDialog
          {...dialogProps}
          direction={dialogDirection}
          open={dialogDirection !== null}
          onClose={() => {
            setDialogDirection(null);
          }}
          onDone={(result, dir) => {
            handleDialogResult(dir, result, "done");
          }}
          onInsertAsTurn={(result, dir) => {
            handleDialogResult(dir, result, "done");
            if (onInsertAsTurn) onInsertAsTurn(result, dir);
            setDialogDirection(null);
          }}
        />
      ) : null}
    </div>
  );
}