import { useState } from "react";
import { Compass, Plus, RefreshCcw } from "lucide-react";

import { api } from "../api";
import type { CreativityLevel, Task, TaskResearchDirection, TaskSuggestDirectionsResponse } from "../types";
import { CreativitySlider } from "../components/CreativitySlider";

/**
 * Forschungsrichtungen-Sektion (Task-Focused Mode).
 *
 * Lädt via ``POST /tasks/{id}/suggest-directions`` eine Liste von Forschungs-
 * richtungen (Kreativitätsstufe 1–5). Der Nutzer wählt eine Richtung an →
 * der Aufrufer kann daraufhin einen Implementationsplan erzeugen.
 *
 * Rein praesentativ: ``onSelect`` reicht die gewählte Richtung ans Parent
 * weiter, das die Plan-Generierung triggert.
 */
type Props = {
  task: Task;
  creativityLevel: CreativityLevel;
  provider?: string | null;
  model?: string | null;
  /** Wenn gesetzt, wird diese Richtung als "ausgewählt" markiert. */
  selectedLabel?: string | null;
  onSelect: (direction: TaskResearchDirection) => void;
  /** Optional: schon vorhandene Richtungen aus task_json.suggested_directions. */
  initialDirections?: TaskResearchDirection[];
};

export function TaskResearchSuggestions({
  task, creativityLevel, provider = null, model = null,
  selectedLabel, onSelect, initialDirections = [],
}: Props) {
  const [directions, setDirections] = useState<TaskResearchDirection[]>(initialDirections);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [creativity, setCreativity] = useState<CreativityLevel>(creativityLevel);
  const [expanded, setExpanded] = useState(false);

  async function loadDirections() {
    setBusy(true);
    setError(null);
    try {
      const res: TaskSuggestDirectionsResponse = await api.tasks.suggestDirections(task.id, {
        creativity_level: creativity, provider, model,
      });
      setDirections(res.directions ?? []);
      setExpanded(true);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
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

      <CreativitySlider value={creativity} onChange={setCreativity} compact label="Kreativität für Vorschläge" />

      {error ? <div className="warning-row">{error}</div> : null}

      {directions.length ? (
        <div className={`task-research-direction-list${expanded ? " expanded" : ""}`}>
          {directions.map((dir, i) => {
            const isSelected = selectedLabel === dir.label;
            return (
              <button
                key={i}
                type="button"
                className={`task-research-direction${isSelected ? " selected" : ""}`}
                onClick={() => { onSelect(dir); }}
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