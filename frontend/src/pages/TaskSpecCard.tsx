import { useState } from "react";
import { ChevronDown, ChevronRight, ExternalLink, FileText, Pencil, Plus, Save, Trash2, X } from "lucide-react";

import { api } from "../api";
import type { Task, TaskSpec } from "../types";

/**
 * Collapsible + editable Task-Spec-Karte (Task-Focused Mode).
 *
 * Rendert alle nicht-leeren Spec-Felder dynamisch als eigene einklappbare
 * Unterkategorien. So wird jedes vom LLM extrahierte Feld sofort sichtbar —
 * egal ob es "Ziel", "Bewertung", "Ein-/Ausschlusskriterien" oder ein künftig
 * hinzukommendes Feld ist.
 *
 * Der Aufrufer übergibt den initialen Task; Änderungen werden via ``onUpdated``
 * weitergereicht, damit das Parent seinen State (z. B. react-query cache)
 * invalidieren kann.
 */
type Props = {
  task: Task;
  onUpdated?: (task: Task) => void;
  /** Wird beim Löschen-Button-Klick gerufen; Parent führt API-Call aus. */
  onDeleted?: (taskId: string) => void;
  /** Default eingeklappt? */
  defaultCollapsed?: boolean;
  /** Wenn true, kein Edit-Button (nur Anzeige). */
  readOnly?: boolean;
};

const EMPTY_SPEC: TaskSpec = {
  title: "",
  objective: "",
  background: "",
  evaluation: "",
  datasets: [],
  timeline: { start: "", end: "" },
  deadline: "",
  methodology: [],
  rules: [],
  constraints: [],
  inclusion_criteria: [],
  exclusion_criteria: [],
  suggested_directions: [],
  extra_sections: [],
  source_raw_text: "",
  error: "",
};

/** Mergt ein (ggf. unvollständiges/älteres) task_json mit EMPTY_SPEC und erzwingt
 *  sichere Typen — null/undefined-Felder werden zu Defaults, Arrays zu [].
 *  Verhindert White-Screens bei Spec-Zugriffen (.length/.join/.map). */
function normalizeSpec(raw: Partial<TaskSpec> | null | undefined): TaskSpec {
  const base: TaskSpec = { ...EMPTY_SPEC, ...(raw ?? {}) };
  return {
    ...base,
    title: base.title ?? "",
    objective: base.objective ?? "",
    background: base.background ?? "",
    evaluation: base.evaluation ?? "",
    datasets: Array.isArray(base.datasets) ? base.datasets : [],
    timeline: base.timeline && typeof base.timeline === "object"
      ? { start: base.timeline.start ?? "", end: base.timeline.end ?? "" }
      : { start: "", end: "" },
    deadline: base.deadline ?? "",
    methodology: Array.isArray(base.methodology) ? base.methodology : [],
    rules: Array.isArray(base.rules) ? base.rules : [],
    constraints: Array.isArray(base.constraints) ? base.constraints : [],
    inclusion_criteria: Array.isArray(base.inclusion_criteria) ? base.inclusion_criteria : [],
    exclusion_criteria: Array.isArray(base.exclusion_criteria) ? base.exclusion_criteria : [],
    suggested_directions: Array.isArray(base.suggested_directions) ? base.suggested_directions : [],
    extra_sections: Array.isArray(base.extra_sections)
      ? base.extra_sections.filter((s) => s && typeof s === "object" && s.label && s.body)
      : [],
    source_raw_text: base.source_raw_text ?? "",
    error: base.error ?? "",
  };
}

/** Metadaten je Spec-Feld: Label + wie der Wert als String/Liste/Objekt gerendert wird. */
type SectionKind = "text" | "list" | "datasets" | "timeline" | "directions" | "source" | "raw";

type SectionDef = {
  key: keyof TaskSpec;
  label: string;
  kind: SectionKind;
};

const SECTIONS: SectionDef[] = [
  { key: "objective", label: "Ziel", kind: "text" },
  { key: "background", label: "Hintergrund", kind: "text" },
  { key: "timeline", label: "Zeitraum", kind: "timeline" },
  { key: "deadline", label: "Deadline", kind: "text" },
  { key: "methodology", label: "Methodik", kind: "list" },
  { key: "evaluation", label: "Bewertung", kind: "text" },
  { key: "datasets", label: "Datensätze", kind: "datasets" },
  { key: "rules", label: "Regeln", kind: "list" },
  { key: "constraints", label: "Constraints", kind: "list" },
  { key: "inclusion_criteria", label: "Einschlusskriterien", kind: "list" },
  { key: "exclusion_criteria", label: "Ausschlusskriterien", kind: "list" },
  { key: "suggested_directions", label: "Forschungsrichtungen", kind: "directions" },
];

export function TaskSpecCard({ task, onUpdated, onDeleted, defaultCollapsed = false, readOnly = false }: Props) {
  const [collapsed, setCollapsed] = useState(defaultCollapsed);
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState<TaskSpec>(normalizeSpec(task.task_json));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const spec: TaskSpec = normalizeSpec(task.task_json);

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

  /** Hat ein Spec-Feld einen sichtbaren Wert (Read-Modus)? */
  function sectionHasValue(def: SectionDef): boolean {
    const v = spec[def.key];
    if (def.kind === "text") return Boolean((v as string).trim());
    if (def.kind === "timeline") return Boolean(spec.timeline?.start || spec.timeline?.end);
    if (def.kind === "datasets") return (v as TaskSpec["datasets"]).length > 0;
    if (def.kind === "list") return (v as string[]).length > 0;
    if (def.kind === "directions") return (v as TaskSpec["suggested_directions"]).length > 0;
    return false;
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
          <>
            <button type="button" className="button button-compact" onClick={(e) => { e.stopPropagation(); startEdit(); }} title="Task-Spec bearbeiten">
              <Pencil size={13} /> <span>Bearbeiten</span>
            </button>
            <button
              type="button"
              className="button button-compact task-spec-card-delete"
              onClick={(e) => {
                e.stopPropagation();
                if (window.confirm(`Aufgabe „${task.title || spec.title || "Aufgabenstellung"}" wirklich löschen?`)) {
                  onDeleted?.(task.id);
                }
              }}
              title="Aufgabe löschen"
            >
              <Trash2 size={13} /> <span>Löschen</span>
            </button>
          </>
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
          {spec.error ? (
            <div className="task-spec-error-banner" role="alert">
              <strong>Extraktion fehlgeschlagen:</strong> {spec.error}
            </div>
          ) : null}
          {error ? <div className="warning-row">{error}</div> : null}

          {/* Dynamische Sections ZUERST — Prizes, Score, Submission File,
              Code-Requirements, Efficiency Prize, … Vom LLM via extra_sections
              erzeugt; jeder als eigene einklappbare Section (label=Titel, body=Text). */}
          <ExtraSectionsRenderer
            sections={spec.extra_sections ?? []}
            draftSections={draft.extra_sections ?? []}
            editing={editing}
            patchDraft={patchDraft}
          />

          {SECTIONS.filter((def) => editing || sectionHasValue(def)).map((def) => (
            <SectionRenderer
              key={def.key}
              def={def}
              spec={spec}
              draft={draft}
              editing={editing}
              patchDraft={patchDraft}
            />
          ))}

          {!editing && (task.source_url || task.source_pdf_path) ? (
            <div className="task-spec-section task-spec-source">
              <label>Quelle</label>
              <div className="task-spec-source-row">
                {task.source_url ? (
                  <a href={task.source_url} target="_blank" rel="noopener noreferrer" className="task-spec-source-link">
                    <ExternalLink size={13} />
                    <span>{task.source_url}</span>
                  </a>
                ) : null}
                {task.source_pdf_path ? (
                  <span className="task-spec-source-file">
                    <FileText size={13} />
                    <span>{task.source_pdf_path}</span>
                  </span>
                ) : null}
              </div>
            </div>
          ) : null}

          {!editing && spec.source_raw_text ? <RawTextSection text={spec.source_raw_text} /> : null}
        </div>
      ) : null}
    </div>
  );
}

/** Einzelne einklappbare Unterkategorie für ein Spec-Feld. */
function SectionRenderer({
  def, spec, draft, editing, patchDraft,
}: {
  def: SectionDef;
  spec: TaskSpec;
  draft: TaskSpec;
  editing: boolean;
  patchDraft: (patch: Partial<TaskSpec>) => void;
}) {
  const [open, setOpen] = useState(true);
  const value = spec[def.key];
  const hasValue = editing || (() => {
    if (def.kind === "text") return Boolean((value as string).trim());
    if (def.kind === "timeline") return Boolean(spec.timeline?.start || spec.timeline?.end);
    if (def.kind === "datasets") return (value as TaskSpec["datasets"]).length > 0;
    if (def.kind === "list") return (value as string[]).length > 0;
    if (def.kind === "directions") return (value as TaskSpec["suggested_directions"]).length > 0;
    return false;
  })();

  if (!hasValue && !editing) return null;

  return (
    <CollapsibleSection
      label={def.label}
      open={open}
      onToggle={() => setOpen((o) => !o)}
    >
      {def.kind === "text" ? (
        editing ? (
          <textarea
            rows={2}
            value={(draft[def.key] as string) ?? ""}
            onChange={(e) => patchDraft({ [def.key]: e.target.value } as Partial<TaskSpec>)}
          />
        ) : (
          <p className="task-spec-section-text">{value as string}</p>
        )
      ) : def.kind === "list" ? (
        editing ? (
          <textarea
            rows={3}
            value={(draft[def.key] as string[]).join("\n")}
            onChange={(e) => patchDraft({ [def.key]: e.target.value.split("\n").filter(Boolean) } as Partial<TaskSpec>)}
            placeholder="Ein Eintrag pro Zeile"
          />
        ) : (
          <ul className="task-spec-list">
            {(value as string[]).map((item, i) => (
              <li key={i}>{item}</li>
            ))}
          </ul>
        )
      ) : def.kind === "datasets" ? (
        editing ? (
          <textarea
            rows={3}
            value={(draft.datasets).map((d) => d.name).join("\n")}
            onChange={(e) => patchDraft({ datasets: e.target.value.split("\n").filter(Boolean).map((name) => ({ name })) })}
            placeholder="Ein Datensatz pro Zeile"
          />
        ) : (
          <ul className="task-spec-dataset-list">
            {(value as TaskSpec["datasets"]).map((d, i) => (
              <li key={i}>
                <span>{d.name}</span>
                {d.size ? <span className="muted"> · {d.size}</span> : null}
                {d.license ? <span className="muted"> · {d.license}</span> : null}
              </li>
            ))}
          </ul>
        )
      ) : def.kind === "timeline" ? (
        editing ? (
          <div className="task-spec-timeline-edit">
            <input type="date" value={draft.timeline?.start ?? ""} onChange={(e) => patchDraft({ timeline: { ...draft.timeline, start: e.target.value } })} />
            <span>→</span>
            <input type="date" value={draft.timeline?.end ?? ""} onChange={(e) => patchDraft({ timeline: { ...draft.timeline, end: e.target.value } })} />
          </div>
        ) : (
          <>
            <span>{spec.timeline.start || "?"} → {spec.timeline.end || "?"}</span>
            <TaskTimelineBar start={spec.timeline?.start} end={spec.timeline?.end} />
          </>
        )
      ) : def.kind === "directions" ? (
        <ul className="task-spec-direction-list">
          {(value as TaskSpec["suggested_directions"]).map((d, i) => (
            <li key={i} className="task-spec-direction">
              <strong>{d.label}</strong>
              {d.rationale ? <p className="muted task-spec-direction-rationale">{d.rationale}</p> : null}
              {d.keywords?.length ? (
                <div className="task-spec-keywords">
                  {d.keywords.map((k, j) => (
                    <span key={j} className="chip">{k}</span>
                  ))}
                </div>
              ) : null}
            </li>
          ))}
        </ul>
      ) : null}
    </CollapsibleSection>
  );
}

/** Einklappbarer Abschnitt mit Chevron + Label. */
function CollapsibleSection({
  label, open, onToggle, children,
}: {
  label: string;
  open: boolean;
  onToggle: () => void;
  children: React.ReactNode;
}) {
  return (
    <div className="task-spec-section">
      <button type="button" className="task-spec-section-toggle" onClick={onToggle}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <label>{label}</label>
      </button>
      {open ? <div className="task-spec-section-content">{children}</div> : null}
    </div>
  );
}

/** Einklappbarer Quelltext. */
function RawTextSection({ text }: { text: string }) {
  const [open, setOpen] = useState(false);
  return (
    <div className="task-spec-section task-spec-raw">
      <button type="button" className="task-spec-raw-toggle" onClick={() => setOpen((o) => !o)}>
        {open ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
        <span>Quelltext</span>
        <span className="muted">{text.length.toLocaleString("de-DE")} Zeichen</span>
      </button>
      {open ? <pre className="task-spec-raw-text">{text}</pre> : null}
    </div>
  );
}

function TaskTimelineBar({ start, end }: { start?: string; end?: string }) {
  if (!start && !end) return null;
  const today = new Date();
  const startD = start ? new Date(start) : null;
  const endD = end ? new Date(end) : null;
  const min = startD || endD || today;
  const max = endD || startD || today;
  if (min.getTime() > max.getTime()) return null;
  const span = max.getTime() - min.getTime() || 1;
  const now = Math.min(Math.max(today.getTime(), min.getTime()), max.getTime());
  const progress = ((now - min.getTime()) / span) * 100;
  return (
    <div className="task-spec-timeline-bar" title={`${start || "?"} → ${end || "?"}`}>
      <div className="task-spec-timeline-track" />
      <div className="task-spec-timeline-progress" style={{ width: `${progress}%` }} />
      {startD ? <span className="task-spec-timeline-marker task-spec-timeline-marker--start" /> : null}
      {endD ? <span className="task-spec-timeline-marker task-spec-timeline-marker--end" /> : null}
    </div>
  );
}

/**
 * Rendert die dynamischen extra_sections (Prizes, Score, Submission File, …).
 * Read-Modus: jede Section als einklappbare CollapsibleSection mit label=Titel,
 * body=Text. Edit-Modus: label-Input + body-Textarea + Löschen-Button pro
 * Section, plus ein »+ Abschnitt«-Button zum Hinzufügen neuer Sections.
 *
 * Die Sections erscheinen VOR den Fixed Fields (Ziel, Hintergrund, …), da
 * der Nutzer sie als die dynamischsten/spezifischsten Inhalte der jeweiligen
 * Aufgabenquelle wahrnimmt.
 */
function ExtraSectionsRenderer({
  sections,
  draftSections,
  editing,
  patchDraft,
}: {
  sections: { label: string; body: string }[];
  draftSections: { label: string; body: string }[];
  editing: boolean;
  patchDraft: (patch: Partial<TaskSpec>) => void;
}) {
  if (!editing && sections.length === 0) return null;

  function updateSection(index: number, patch: Partial<{ label: string; body: string }>) {
    const next = draftSections.map((s, i) => (i === index ? { ...s, ...patch } : s));
    patchDraft({ extra_sections: next });
  }
  function removeSection(index: number) {
    const next = draftSections.filter((_, i) => i !== index);
    patchDraft({ extra_sections: next });
  }
  function addSection() {
    patchDraft({ extra_sections: [...draftSections, { label: "", body: "" }] });
  }

  return (
    <>
      {editing ? (
        <div className="task-spec-section task-spec-extra-edit">
          <label className="task-spec-section-title">Weitere Abschnitte (dynamisch)</label>
          {draftSections.map((s, i) => (
            <div key={i} className="task-spec-extra-edit-row">
              <input
                type="text"
                value={s.label}
                placeholder="Titel (z. B. Prizes)"
                onChange={(e) => updateSection(i, { label: e.target.value })}
              />
              <textarea
                rows={3}
                value={s.body}
                placeholder="Inhalt des Abschnitts"
                onChange={(e) => updateSection(i, { body: e.target.value })}
              />
              <button
                type="button"
                className="icon-button task-spec-extra-remove"
                onClick={() => removeSection(i)}
                title="Abschnitt entfernen"
              >
                <Trash2 size={13} />
              </button>
            </div>
          ))}
          <button type="button" className="button button-compact task-spec-extra-add" onClick={addSection}>
            <Plus size={13} /> <span>Abschnitt hinzufügen</span>
          </button>
        </div>
      ) : (
        sections.map((s, i) => (
          <ExtraSectionItem key={`${s.label}-${i}`} label={s.label} body={s.body} />
        ))
      )}
    </>
  );
}

/** Einzelne dynamische Section (Read-Modus) als einklappbare Section. */
function ExtraSectionItem({ label, body }: { label: string; body: string }) {
  const [open, setOpen] = useState(true);
  return (
    <CollapsibleSection label={label} open={open} onToggle={() => setOpen((o) => !o)}>
      <p className="task-spec-section-text">{body}</p>
    </CollapsibleSection>
  );
}