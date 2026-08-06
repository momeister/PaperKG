import { useEffect, useMemo, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Compass,
  GraduationCap,
  ListChecks,
  Loader2,
  Plus,
  Sparkles,
  Target,
  X,
} from "lucide-react";

import { api } from "../api";
import type {
  CreativityLevel,
  Task,
  TaskImplementationPlan,
  TaskResearchDirection,
  VerificationSource,
} from "../types";
import { CreativitySlider } from "../components/CreativitySlider";
import { TaskIngestDialog } from "./TaskIngestDialog";
import { TaskResearchSuggestions } from "./TaskResearchSuggestions";
import { TaskSpecCard } from "./TaskSpecCard";

/**
 * Task-Focused Pane — Overlay-Container für den Task-Modus (Hackathon/Kaggle/Anweisung).
 *
 * Plan §Session 4: Zeigt die Task-Spec, lädt Forschungsrichtungen, erzeugt einen
 * Implementationsplan und kann eine parallele Research-Session starten
 * (verkabelt via ``onStartParallelSession`` mit der Task als grey source).
 *
 * Der Container ist ein Overlay *innerhalb* der Workspace-Seite, kein eigener
 * Panel-Slot — im Task-Modus kollabieren die Navigator-Tabs (Plan: nav-tabs
 * default-collapsed) und dieses Pane überdeckt die Center-+Assistant-Spalten.
 */
type Props = {
  projectId: string;
  provider?: string | null;
  model?: string | null;
  creativityLevel: CreativityLevel;
  setCreativityLevel: (level: CreativityLevel) => void;
  /** Startet eine parallele Research-Session mit der Task als Kontext. */
  onStartParallelSession: (question: string, taskId: string) => void;
  /** Schaltet zurück in den Research-Modus. */
  onClose: () => void;
};

export function TaskFocusedPane({
  projectId,
  provider = null,
  model = null,
  creativityLevel,
  setCreativityLevel,
  onStartParallelSession,
  onClose,
}: Props) {
  const queryClient = useQueryClient();
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [showIngest, setShowIngest] = useState(false);
  const [selectedDirection, setSelectedDirection] = useState<TaskResearchDirection | null>(null);
  const [plan, setPlan] = useState<TaskImplementationPlan | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [greyBusy, setGreyBusy] = useState(false);
  const [greyCitation, setGreyCitation] = useState<string | null>(null);
  const [greyError, setGreyError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);

  // Tasks des Projekts laden
  const tasksQuery = useQuery({
    queryKey: ["tasks", projectId],
    queryFn: () => api.tasks.list(projectId),
    enabled: !!projectId,
  });

  const tasks = useMemo(() => tasksQuery.data?.tasks ?? [], [tasksQuery.data]);
  const activeTask = useMemo<Task | null>(() => {
    if (!tasks.length) return null;
    if (activeTaskId) return tasks.find((t) => t.id === activeTaskId) ?? null;
    return tasks[0] ?? null;
  }, [tasks, activeTaskId]);

  // Wenn die Liste sich ändert und keine aktive Task gesetzt ist, nimm die erste.
  useEffect(() => {
    if (tasks.length && !activeTaskId) setActiveTaskId(tasks[0].id);
  }, [tasks, activeTaskId]);

  // Bei Task-Wechsel: Plan + Auswahl zurücksetzen.
  useEffect(() => {
    setSelectedDirection(null);
    setPlan(null);
    setPlanError(null);
    setGreyCitation(null);
    setGreyError(null);
  }, [activeTask?.id]);

  function refreshTasks() {
    void queryClient.invalidateQueries({ queryKey: ["tasks", projectId] });
  }

  function handleTaskCreated(task: Task) {
    refreshTasks();
    setActiveTaskId(task.id);
    setToast(`Task „${task.title}“ aufgenommen`);
    window.setTimeout(() => setToast(null), 2500);
  }

  async function handleSelectDirection(dir: TaskResearchDirection) {
    if (!activeTask) return;
    setSelectedDirection(dir);
    setPlan(null);
    setPlanError(null);
    setPlanBusy(true);
    try {
      const res = await api.tasks.plan(activeTask.id, {
        direction: dir,
        creativity_level: creativityLevel,
        provider,
        model,
      });
      setPlan(res);
    } catch (err) {
      setPlanError(err instanceof Error ? err.message : String(err));
    } finally {
      setPlanBusy(false);
    }
  }

  async function handlePublishAsGreySource() {
    if (!activeTask || greyCitation) return;
    setGreyBusy(true);
    setGreyError(null);
    try {
      const res = await api.tasks.publishAsGreySource(activeTask.id);
      setGreyCitation(res.citation);
      setToast("Task als zitierfähige Grey-Source veröffentlicht");
      window.setTimeout(() => setToast(null), 2500);
    } catch (err) {
      setGreyError(err instanceof Error ? err.message : String(err));
    } finally {
      setGreyBusy(false);
    }
  }

  function handleStartParallel() {
    if (!activeTask) return;
    const question = selectedDirection
      ? `${activeTask.title} — Richtung: ${selectedDirection.label}`
      : activeTask.title;
    onStartParallelSession(question, activeTask.id);
  }

  return (
    <section className="task-focused-pane" aria-label="Task-Focused Mode">
      <header className="task-focused-pane__head">
        <div className="task-focused-pane__head-title">
          <Target size={18} />
          <div>
            <span className="task-focused-pane__eyebrow muted">Task-Focused Mode</span>
            <strong>Aufgabe &amp; Umsetzung</strong>
          </div>
        </div>
        <div className="task-focused-pane__head-actions">
          <button
            type="button"
            className="button button-compact button-primary"
            onClick={() => setShowIngest(true)}
            title="Neue Aufgabe aufnehmen (URL, PDF oder Freitext)"
          >
            <Plus size={13} />
            <span>Aufgabe aufnehmen</span>
          </button>
          <button
            type="button"
            className="icon-button"
            title="Zurück in den Research-Modus"
            onClick={onClose}
          >
            <X size={16} />
          </button>
        </div>
      </header>

      <div className="task-focused-pane__creativity">
        <CreativitySlider
          value={creativityLevel}
          onChange={setCreativityLevel}
          label="Kreativität für dieses Projekt"
          id="task-creativity"
        />
        <span className="muted task-focused-pane__creativity-hint">
          Steuert, wie konventionell (1) oder cross-domain (5) Vorschläge, Richtungen und Varianten ausfallen.
        </span>
      </div>

      {tasksQuery.isLoading ? (
        <div className="parallel-loading">
          <Loader2 size={16} className="spin" />
          <span>Aufgaben werden geladen…</span>
        </div>
      ) : tasks.length === 0 ? (
        <div className="task-focused-pane__empty">
          <Target size={28} />
          <p>Noch keine Aufgabe aufgenommen.</p>
          <p className="muted">
            Lade eine Aufgabenstellung als URL, PDF oder Freitext — der Assistant extrahiert daraus eine
            strukturierte Task-Spec (Ziel, Evaluation, Datensätze, Constraints).
          </p>
          <button type="button" className="button button-primary" onClick={() => setShowIngest(true)}>
            <Plus size={13} />
            <span>Aufgabe aufnehmen</span>
          </button>
        </div>
      ) : (
        <>
          {tasks.length > 1 ? (
            <div className="task-focused-pane__task-switcher">
              <label htmlFor="task-active-select">Aktive Aufgabe</label>
              <select
                id="task-active-select"
                value={activeTask?.id ?? ""}
                onChange={(e) => setActiveTaskId(e.target.value)}
              >
                {tasks.map((t) => (
                  <option key={t.id} value={t.id}>
                    {t.title}
                  </option>
                ))}
              </select>
            </div>
          ) : null}

          {activeTask ? (
            <>
              <TaskSpecCard task={activeTask} onUpdated={refreshTasks} />

              <TaskResearchSuggestions
                task={activeTask}
                creativityLevel={creativityLevel}
                provider={provider}
                model={model}
                selectedLabel={selectedDirection?.label ?? null}
                onSelect={handleSelectDirection}
                initialDirections={activeTask.task_json?.suggested_directions ?? []}
              />

              {planBusy ? (
                <div className="parallel-loading">
                  <Loader2 size={16} className="spin" />
                  <span>Implementationsplan wird erstellt…</span>
                </div>
              ) : planError ? (
                <div className="warning-row">{planError}</div>
              ) : plan ? (
                <section className="task-implementation-plan">
                  <div className="task-implementation-plan__head">
                    <ListChecks size={15} />
                    <strong>Implementationsplan</strong>
                    <span className="muted">· {plan.steps.length} Schritte · Kreativität {plan.creativity_level}</span>
                  </div>
                  {plan.plan_markdown.trim() ? (
                    <pre className="task-implementation-plan__markdown">{plan.plan_markdown}</pre>
                  ) : null}
                  {plan.steps.length ? (
                    <ol className="task-implementation-plan__steps">
                      {plan.steps.map((step, i) => (
                        <li key={i}>
                          <strong>{step.text}</strong>
                          {step.rationale ? <p className="muted">{step.rationale}</p> : null}
                          {step.citation ? <p className="muted task-implementation-plan__citation">Quelle: {step.citation}</p> : null}
                        </li>
                      ))}
                    </ol>
                  ) : null}
                </section>
              ) : null}

              <section className="task-grey-source">
                <div className="task-grey-source__head">
                  <Compass size={15} />
                  <strong>Als zitierfähige Quelle veröffentlichen</strong>
                </div>
                <p className="muted">
                  Veröffentlicht die Task-Spec als Grey-Source ({`grey::task_${activeTask.id}`}), damit
                  Assistant-Antworten die Aufgabenstellung zitieren können — analog zu Papers und Web-Quellen.
                </p>
                {greyCitation ? (
                  <div className="task-grey-source__done">
                    <span>Veröffentlicht als </span>
                    <code>{greyCitation}</code>
                  </div>
                ) : (
                  <button
                    type="button"
                    className="button button-compact"
                    onClick={handlePublishAsGreySource}
                    disabled={greyBusy}
                  >
                    {greyBusy ? <Loader2 size={13} className="spin" /> : <Compass size={13} />}
                    <span>{greyBusy ? "Veröffentliche…" : "Als Grey-Source veröffentlichen"}</span>
                  </button>
                )}
                {greyError ? <div className="warning-row">{greyError}</div> : null}
              </section>

              <section className="task-handoff-parallel">
                <div className="task-handoff-parallel__head">
                  <Sparkles size={15} />
                  <strong>Parallele Varianten starten</strong>
                </div>
                <p className="muted">
                  Startet eine parallele Research-Session aus der Task-Spec — der Assistant schlägt mehrere
                  Umsetzungs-Varianten vor, die du im „Ergebnisse"-Tab einzeln ausprobieren und begutachten lassen kannst.
                  {selectedDirection ? ` Richtung: ${selectedDirection.label}.` : null}
                </p>
                <button
                  type="button"
                  className="button button-primary"
                  onClick={handleStartParallel}
                  title="Parallele Research-Session mit dieser Task starten"
                >
                  <GraduationCap size={14} />
                  <span>Parallele Varianten</span>
                </button>
              </section>
            </>
          ) : null}
        </>
      )}

      {toast ? <div className="task-focused-toast">{toast}</div> : null}

      <TaskIngestDialog
        projectId={projectId}
        open={showIngest}
        onClose={() => setShowIngest(false)}
        onTaskCreated={handleTaskCreated}
      />
    </section>
  );
}