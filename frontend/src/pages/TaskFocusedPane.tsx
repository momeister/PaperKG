import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  ChevronDown,
  ChevronRight,
  Compass,
  FileText,
  GraduationCap,
  Link as LinkIcon,
  ListChecks,
  Loader2,
  Plus,
  Sparkles,
  Target,
  Type,
  X,
} from "lucide-react";

import { api } from "../api";
import type {
  CreativityLevel,
  Task,
  TaskDeepSearchResult,
  TaskImplementationPlan,
  TaskResearchDirection,
  VerificationSource,
} from "../types";
import { CreativitySlider } from "../components/CreativitySlider";
import { TaskIngestDialog } from "./TaskIngestDialog";
import { ParallelTransferDialog } from "./ParallelTransferDialog";
import { TaskResearchSuggestions } from "./TaskResearchSuggestions";
import { TaskSpecCard } from "./TaskSpecCard";

/**
 * Task-Focused Pane — Overlay-Container für den Task-Modus (Hackathon/Kaggle/Anweisung).
 *
 * Zeigt die Task-Spec, lädt Forschungsrichtungen, erzeugt einen
 * Implementationsplan und kann eine parallele Research-Session starten
 * (verkabelt via ``onStartParallelSession`` mit der Task als grey source).
 *
 * Im Leerzustand wird die Fokus-Auswahl (PDF/URL/Text) inline angezeigt —
 * kein Modal. Sobald eine Task existiert, erscheinen TaskSpecCard + Aktionen.
 */
type Props = {
  projectId: string;
  provider?: string | null;
  model?: string | null;
  creativityLevel: CreativityLevel;
  setCreativityLevel: (level: CreativityLevel) => void;
  /** Startet eine parallele Research-Session mit der Task als Kontext. */
  onStartParallelSession: (question: string, taskId: string) => void;
  /** Tiefensuche-Ergebnis als neuen Assistant-Turn einfügen (Bibliothek + Chat). */
  onInsertAsTurn?: (result: TaskDeepSearchResult, direction: TaskResearchDirection) => void;
  /** Schaltet zurück in den Research-Modus. */
  onClose: () => void;
};

type Source = "url" | "pdf" | "text";

export function TaskFocusedPane({
  projectId,
  provider = null,
  model = null,
  creativityLevel,
  setCreativityLevel,
  onStartParallelSession,
  onInsertAsTurn,
  onClose,
}: Props) {
  const queryClient = useQueryClient();
  const [activeTaskId, setActiveTaskId] = useState<string | null>(null);
  const [selectedDirection, setSelectedDirection] = useState<TaskResearchDirection | null>(null);
  const [plan, setPlan] = useState<TaskImplementationPlan | null>(null);
  const [planBusy, setPlanBusy] = useState(false);
  const [planError, setPlanError] = useState<string | null>(null);
  const [greyBusy, setGreyBusy] = useState(false);
  const [greyCitation, setGreyCitation] = useState<string | null>(null);
  const [greyError, setGreyError] = useState<string | null>(null);
  const [toast, setToast] = useState<string | null>(null);
  // Inline-Erfassung (Leerzustand)
  const [inlineSource, setInlineSource] = useState<Source>("text");
  const [inlineUrl, setInlineUrl] = useState("");
  const [inlineText, setInlineText] = useState("");
  const [inlinePdfFile, setInlinePdfFile] = useState<File | null>(null);
  const [inlineBusy, setInlineBusy] = useState(false);
  const [inlineError, setInlineError] = useState<string | null>(null);
  const [showIngest, setShowIngest] = useState(false);
  // Dialog: Forschungsrichtung in den Parallel-Modus überführen.
  const [transferDirection, setTransferDirection] = useState<TaskResearchDirection | null>(null);

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

  async function handleDeleteTask(taskId: string) {
    try {
      await api.tasks.remove(taskId);
      if (activeTaskId === taskId) setActiveTaskId(null);
      refreshTasks();
      setToast("Aufgabe gelöscht");
      window.setTimeout(() => setToast(null), 2500);
    } catch (err) {
      setToast(err instanceof Error ? err.message : String(err));
      window.setTimeout(() => setToast(null), 4000);
    }
  }

  function handleTaskCreated(task: Task) {
    refreshTasks();
    setActiveTaskId(task.id);
    setInlineUrl("");
    setInlineText("");
    setInlinePdfFile(null);
    setInlineError(null);
    setToast(`Task „${task.title}“ aufgenommen`);
    window.setTimeout(() => setToast(null), 2500);
  }

  const canSubmitInline =
    !inlineBusy &&
    ((inlineSource === "url" && inlineUrl.trim().length > 0) ||
      (inlineSource === "text" && inlineText.trim().length > 0) ||
      (inlineSource === "pdf" && inlinePdfFile !== null));

  async function handleInlineSubmit() {
    if (!canSubmitInline) return;
    setInlineBusy(true);
    setInlineError(null);
    try {
      let pdfPath: string | null = null;
      if (inlineSource === "pdf" && inlinePdfFile) {
        const up = await api.uploadPdf(inlinePdfFile, { title: inlinePdfFile.name, project_id: projectId });
        pdfPath = up.pdf_path;
      }
      const task = await api.tasks.ingest(projectId, {
        source_kind: inlineSource,
        source_url: inlineSource === "url" ? inlineUrl.trim() : null,
        source_text: inlineSource === "text" ? inlineText.trim() : null,
        source_pdf_path: pdfPath,
        provider,
        model,
      });
      handleTaskCreated(task);
    } catch (err) {
      setInlineError(err instanceof Error ? err.message : String(err));
    } finally {
      setInlineBusy(false);
    }
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

  function handleStartParallelFromDirection(direction: TaskResearchDirection) {
    if (!activeTask) return;
    setSelectedDirection(direction);
    setTransferDirection(direction);
  }

  function handleConfirmTransfer(question: string, _creativity: CreativityLevel) {
    if (!activeTask || !transferDirection) return;
    // creativity wird projektweit übernommen, damit die Session sie erbt.
    setCreativityLevel(_creativity);
    setTransferDirection(null);
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
          {tasks.length > 0 ? (
            <button
              type="button"
              className="button button-compact button-primary"
              onClick={() => setShowIngest(true)}
              title="Neue Aufgabe aufnehmen (URL, PDF oder Freitext)"
            >
              <Plus size={13} />
              <span>Neue Aufgabe</span>
            </button>
          ) : null}
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
            strukturierte Task-Spec (Ziel, Evaluation, Datensätze, Kriterien).
          </p>

          <div className="task-focused-inline-ingest">
            <div className="segmented task-ingest-source-tabs" role="tablist">
              <button
                type="button"
                role="tab"
                aria-selected={inlineSource === "text"}
                className={inlineSource === "text" ? "active" : ""}
                onClick={() => setInlineSource("text")}
              >
                <Type size={14} />
                <span>Freitext</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={inlineSource === "url"}
                className={inlineSource === "url" ? "active" : ""}
                onClick={() => setInlineSource("url")}
              >
                <LinkIcon size={14} />
                <span>URL</span>
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={inlineSource === "pdf"}
                className={inlineSource === "pdf" ? "active" : ""}
                onClick={() => setInlineSource("pdf")}
              >
                <FileText size={14} />
                <span>PDF</span>
              </button>
            </div>

            {inlineSource === "text" ? (
              <textarea
                rows={6}
                placeholder="z. B. „Baue einen Klassifikator für arrhythmiefreie EKG-Abschnitte aus dem MIT-BIH-Subset. Ziel: F1 ≥ 0.85 auf einer gehaltenen Patient-Disjunkt-Testmenge. Constraint: Modell muss auf CPU unter 200 ms pro 10-s-Abschnitt inferieren.“"
                value={inlineText}
                onChange={(e) => setInlineText(e.target.value)}
                disabled={inlineBusy}
              />
            ) : null}
            {inlineSource === "url" ? (
              <>
                <input
                  type="url"
                  placeholder="https://www.kaggle.com/competitions/…"
                  value={inlineUrl}
                  onChange={(e) => setInlineUrl(e.target.value)}
                  disabled={inlineBusy}
                />
                <span className="muted task-ingest-hint">
                  Der Backend lädt die Seite herunter und parst den sichtbaren Text.
                </span>
              </>
            ) : null}
            {inlineSource === "pdf" ? (
              <input
                type="file"
                accept="application/pdf"
                onChange={(e) => setInlinePdfFile(e.target.files?.[0] ?? null)}
                disabled={inlineBusy}
              />
            ) : null}

            {inlineError ? <div className="warning-row">{inlineError}</div> : null}

            <button
              type="button"
              className="button button-primary"
              onClick={handleInlineSubmit}
              disabled={!canSubmitInline}
            >
              {inlineBusy ? <Loader2 size={14} className="spin" /> : <Target size={14} />}
              <span>{inlineBusy ? "Extrahiere…" : "Aufgabe aufnehmen"}</span>
            </button>
          </div>
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
              <TaskSpecCard task={activeTask} onUpdated={refreshTasks} onDeleted={handleDeleteTask} />

              <TaskResearchSuggestions
                task={activeTask}
                creativityLevel={creativityLevel}
                provider={provider}
                model={model}
                projectId={projectId}
                selectedLabel={selectedDirection?.label ?? null}
                onSelect={handleSelectDirection}
                onStartParallel={handleStartParallelFromDirection}
                onInsertAsTurn={onInsertAsTurn}
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
                <div className="task-focused-pane__creativity">
                  <span className="task-focused-pane__creativity-label muted">Konservativ → Creativ</span>
                  <CreativitySlider
                    value={creativityLevel}
                    onChange={setCreativityLevel}
                    id="task-creativity"
                    compact
                    hint="Gilt projektweit für den Implementationsplan und Parallele Sessions (1 = konservativ, 5 = cross-domain)."
                    tooltip="Kreativität projektweit — steuert Implementationsplan und Parallele Sessions dieser Aufgabe."
                  />
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
        provider={provider}
        model={model}
      />

      <ParallelTransferDialog
        direction={transferDirection ?? ({} as TaskResearchDirection)}
        taskTitle={activeTask?.title ?? ""}
        creativityLevel={creativityLevel}
        open={!!transferDirection}
        onClose={() => setTransferDirection(null)}
        onConfirm={handleConfirmTransfer}
      />
    </section>
  );
}