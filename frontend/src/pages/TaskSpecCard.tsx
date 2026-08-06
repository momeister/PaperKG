import { useState } from "react";
import { ChevronDown, ChevronRight, Pencil, Save, X } from "lucide-react";

import { api } from "../api";
import type { Task, TaskSpec } from "../types";

/**
 * Collapsible + editable Task-Spec-Karte (Task-Focused Mode).
 *
 * Zeigt die extrahierte Task-Spec eines gespeicherten Task. Im Edit-Modus
 * werden die Felder zu Textareas, Speichern geht via ``api.tasks.update``.
 *
 * Der Aufrufer übergibt den initialen Task;Änderungen werden via ``onUpdated``
 * weitergereicht, damit das Parent seinen State (z. B. react-query cache)
 * invalidieren kann.
 */
type Props = {
  task: Task;
  onUpdated?: (task: Task) => void;
  /** Default eingeklappt? */
  defaultCollapsed?: boolean;
  /** Wenn true, kein Edit-Button (nur Anzeige). */
  readOnly?: boolean;
};

const EMPTY_SPEC: TaskSpec = {
  title: "",
  objective: "",
  evaluation: "",
  datasets: [],
  timeline: { start: "", end: "" },
  rules: [],
  constraints: [],
  suggested_directions: [],
};

export function TaskSpecCard({ task, onUpdated, defaultCollapsed = false, readOnly = false }: Props) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<TaskSpec>(task.task_json ?? EMPTY_SPEC);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const spec: TaskSpec = task.task_json ?? EMPTY_SPEC;

  function startEdit() {
    setDraft(spec);
    setEditing(true);
    setError(null);
  }

  function cancelEdit() {
    setEditing(false);
    setDraft(spec);
    setError(null);
  }

  async function saveEdit() {
    setBusy(true);
    setError(null);
    try {
      const updated = await api.tasks.update(task.id, { task_json: draft });
      setEditing(false);
      onUpdated?.(updated);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  }

  function patchDraft(patch: Partial<TaskSpec>) {
    setDraft((current) => ({ ...current, ...patch }));
  }

  return (
    <div className="task-spec-card">
      <div className="task-spec-card-head" onClick={() => !editing && setCollapsed((c) => !c)} role="button" tabIndex={0}
        onKeyDown={(e) => { if (!editing && (e.key === "Enter" || e.key === " ")) { e.preventDefault(); setCollapsed((c) => !c); } }}
      >
        <span className="task-spec-card-toggle">
          {collapsed ? <ChevronRight size={16} /> : <ChevronDown size={16} />}
        </span>
        <div className="task-spec-card-title">
          <strong>{task.title || spec.title || "Aufgabenstellung"}</strong>
          <span className="muted task-spec-card-meta">
            {task.source_kind === "url" ? "URL" : task.source_kind === "pdf" ? "PDF" : "Text"}
            {spec.timeline?.start ? ` · ${spec.timeline.start}` : ""}
          </span>
        </div>
        {!readOnly && !editing ? (
          <button type="button" className="button button-compact" onClick={(e) => { e.stopPropagation(); startEdit(); }} title="Task-Spec bearbeiten">
            <Pencil size={13} /> <span>Bearbeiten</span>
          </button>
        ) : null}
        {editing ? (
          <>
            <button type="button" className="button button-primary button-compact" onClick={(e) => { e.stopPropagation(); saveEdit(); }} disabled={busy} title="Speichern">
              <Save size={13} /> <span>{busy ? "…" : "Speichern"}</span>
            </button>
            <button type="button" className="button button-compact" onClick={(e) => { e.stopPropagation(); cancelEdit(); }} disabled={busy} title="Abbrechen">
              <X size={13} /> <span>Abbrechen</span>
            </button>
          </>
        ) : null}
      </div>

      {!collapsed ? (
        <div className="task-spec-card-body">
          {error ? <div className="warning-row">{error}</div> : null}

          <TaskSpecField label="Ziel" value={spec.objective} editing={editing}
            draftValue={draft.objective}
            onChange={(v) => patchDraft({ objective: v })}
          />
          <TaskSpecField label="Evaluation" value={spec.evaluation} editing={editing}
            draftValue={draft.evaluation}
            onChange={(v) => patchDraft({ evaluation: v })}
          />

          <div className="task-spec-section">
            <label>Datensätze</label>
            {editing ? (
              <textarea rows={3} value={draft.datasets.map((d) => d.name).join("\n")}
                onChange={(e) => patchDraft({ datasets: e.target.value.split("\n").filter(Boolean).map((name) => ({ name })) })}
                placeholder="Ein Datensatz pro Zeile"
              />
            ) : spec.datasets.length ? (
              <ul className="task-spec-dataset-list">
                {spec.datasets.map((d, i) => (
                  <li key={i}>
                    <span>{d.name}</span>
                    {d.size ? <span className="muted"> · {d.size}</span> : null}
                    {d.license ? <span className="muted"> · {d.license}</span> : null}
                  </li>
                ))}
              </ul>
            ) : <span className="muted">Keine Datensätze hinterlegt.</span>}
          </div>

          <TaskSpecField label="Regeln" value={spec.rules.join("\n")} editing={editing}
            draftValue={draft.rules.join("\n")}
            onChange={(v) => patchDraft({ rules: v.split("\n").filter(Boolean) })}
            multiline
          />
          <TaskSpecField label="Constraints" value={spec.constraints.join("\n")} editing={editing}
            draftValue={draft.constraints.join("\n")}
            onChange={(v) => patchDraft({ constraints: v.split("\n").filter(Boolean) })}
            multiline
          />

          {spec.timeline?.start || spec.timeline?.end || editing ? (
            <div className="task-spec-section task-spec-timeline">
              <label>Zeitraum</label>
              {editing ? (
                <div className="task-spec-timeline-edit">
                  <input type="date" value={draft.timeline?.start ?? ""} onChange={(e) => patchDraft({ timeline: { ...draft.timeline, start: e.target.value } })} />
                  <span>→</span>
                  <input type="date" value={draft.timeline?.end ?? ""} onChange={(e) => patchDraft({ timeline: { ...draft.timeline, end: e.target.value } })} />
                </div>
              ) : (
                <span>{spec.timeline.start || "?"} → {spec.timeline.end || "?"}</span>
              )}
            </div>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

function TaskSpecField({
  label, value, editing, draftValue, onChange, multiline,
}: {
  label: string; value: string; editing: boolean;
  draftValue: string; onChange: (v: string) => void; multiline?: boolean;
}) {
  if (editing) {
    return (
      <div className="task-spec-section">
        <label>{label}</label>
        {multiline ? (
          <textarea rows={3} value={draftValue} onChange={(e) => onChange(e.target.value)} />
        ) : (
          <textarea rows={2} value={draftValue} onChange={(e) => onChange(e.target.value)} />
        )}
      </div>
    );
  }
  if (!value.trim()) return null;
  return (
    <div className="task-spec-section">
      <label>{label}</label>
      <p className="task-spec-section-text">{value}</p>
    </div>
  );
}