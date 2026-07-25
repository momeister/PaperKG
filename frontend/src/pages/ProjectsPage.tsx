import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { Check, ChevronDown, Download, Loader2, Pencil, Pin, PinOff, Plus, Search, Trash2, Upload, X } from "lucide-react";

import { api, exportProjectBundle, importProjectBundle, previewProjectBundle } from "../api";
import { downloadBlob } from "../download";
import { MetricCard } from "../components/MetricCard";
import { DegradedNotice, QueryError } from "../components/QueryError";
import { Status } from "../components/Status";
import { useAppState } from "../state";
import type { Project } from "../types";

export function ProjectsPage({ embedded = false }: { embedded?: boolean }) {
  const { activeProject, setActiveProject } = useAppState();
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [renameId, setRenameId] = useState<string | null>(null);
  const [renameValue, setRenameValue] = useState("");
  const [healthOpen, setHealthOpen] = useState(false);
  // Bundle-Export/-Import: ein Projekt samt Papern, Extraktionen, Grauquellen und
  // Embeddings als ZIP mitnehmen — Umzug auf einen anderen Rechner und Sicherung
  // in einem. data/projects.json ist gitignored und hat sonst keine Historie.
  const [includePdfsInExport, setIncludePdfsInExport] = useState(false);
  const [bundleFile, setBundleFile] = useState<File | null>(null);
  const [importMode, setImportMode] = useState<"merge" | "replace">("merge");
  const [importTarget, setImportTarget] = useState("");
  const [bundleDragOver, setBundleDragOver] = useState(false);
  const queryClient = useQueryClient();
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
  const dashboardQuery = useQuery({
    queryKey: ["dashboard", activeProject],
    queryFn: () => api.getDashboard(activeProject!),
    enabled: Boolean(activeProject)
  });
  const createProject = useMutation({
    mutationFn: api.createProject,
    onSuccess: (result) => {
      setName("");
      setActiveProject(result.project.id);
      queryClient.setQueryData<{ projects: Project[] }>(["projects"], (current) => {
        if (!current) {
          return { projects: [result.project] };
        }
        const existing = current.projects.filter((project) => project.id !== result.project.id);
        return { projects: [...existing, result.project] };
      });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
    }
  });
  const deleteProject = useMutation({
    mutationFn: api.deleteProject,
    onMutate: () => setDeleteError(""),
    onSuccess: (_, projectId) => {
      if (activeProject === projectId) {
        setActiveProject(undefined);
      }
      queryClient.setQueryData<{ projects: Project[] }>(["projects"], (current) =>
        current ? { projects: current.projects.filter((project) => project.id !== projectId) } : current
      );
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.removeQueries({ queryKey: ["dashboard", projectId] });
    },
    onError: (error) => setDeleteError(error instanceof Error ? error.message : "Projekt konnte nicht geloescht werden")
  });
  // Umbenennen verschiebt die Projekt-ID: das Backend zieht Notizen, Web-Quellen,
  // Sessions und Analysen mit um, hier muss nur die aktive Auswahl nachziehen.
  const patchProject = useMutation({
    mutationFn: ({ projectId, ...payload }: { projectId: string; name?: string; pinned?: boolean }) =>
      api.patchProject(projectId, payload),
    onMutate: () => setDeleteError(""),
    onSuccess: (result, variables) => {
      if (activeProject === variables.projectId && result.project.id !== variables.projectId) {
        setActiveProject(result.project.id);
      }
      setRenameId(null);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    },
    onError: (error) => setDeleteError(error instanceof Error ? error.message : "Projekt konnte nicht geändert werden")
  });

  const exportBundle = useMutation({
    mutationFn: (projectId: string) => exportProjectBundle(projectId, includePdfsInExport),
    onSuccess: ({ blob, filename }) => downloadBlob(filename, blob)
  });

  const previewBundle = useMutation({
    mutationFn: (file: File) => previewProjectBundle(file),
    onSuccess: ({ preview }) => setImportTarget(preview.project)
  });

  const runImport = useMutation({
    mutationFn: ({ file, mode, target }: { file: File; mode: "merge" | "replace"; target: string }) =>
      importProjectBundle(file, mode, target.trim() || undefined),
    onSuccess: ({ report }) => {
      setBundleFile(null);
      previewBundle.reset();
      setActiveProject(report.project);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
      queryClient.invalidateQueries({ queryKey: ["dashboard"] });
    }
  });

  function selectBundle(file: File | null) {
    setBundleFile(file);
    runImport.reset();
    previewBundle.reset();
    if (file) {
      previewBundle.mutate(file);
    }
  }

  function submit(event: FormEvent) {
    event.preventDefault();
    if (name.trim()) {
      createProject.mutate(name.trim());
    }
  }

  function removeProject(projectId: string) {
    if (projectId === "__all_papers__") {
      setDeleteError("Alle Papers ist der globale Modus und kann nicht geloescht werden.");
      return;
    }
    if (window.confirm("Projekt loeschen? Die Paper bleiben in der Bibliothek erhalten.")) {
      deleteProject.mutate(projectId);
    }
  }

  function startRename(project: Project) {
    setRenameId(project.id);
    setRenameValue(project.name);
  }

  function commitRename(projectId: string) {
    const next = renameValue.trim();
    if (!next || next === projectId) {
      setRenameId(null);
      return;
    }
    patchProject.mutate({ projectId, name: next });
  }

  const filteredProjects = (projectsQuery.data?.projects ?? []).filter((project) => project.name.toLowerCase().includes(query.toLowerCase()));
  const metrics = dashboardQuery.data?.metrics;
  const health = dashboardQuery.data?.health;
  const warnings = health?.warnings ?? [];
  const latestJobs = dashboardQuery.data?.latest_jobs ?? [];

  return (
    <section className={embedded ? "research-stage-panel" : "page"}>
      <div className="page-title">
        <div>
          <span>Workspace</span>
          <h1>Projektübersicht</h1>
        </div>
        <form className="inline-form" onSubmit={submit}>
          <input value={name} onChange={(event) => setName(event.target.value)} placeholder="Neues Projekt" />
          <button className="button button-primary" type="submit">
            <Plus size={17} />
            <span>Anlegen</span>
          </button>
        </form>
      </div>

      <div className="metrics-grid">
        <MetricCard label="Papers" value={metrics?.papers ?? "—"} tone="blue" />
        <MetricCard label="PDFs" value={metrics?.pdfs ?? "—"} tone="green" />
        <MetricCard label="Extraction" value={metrics ? `${Math.round(metrics.extraction_coverage * 100)}%` : "—"} tone="amber" />
        <MetricCard label="Review" value={metrics?.pending_review ?? "—"} tone={metrics?.pending_review ? "red" : "neutral"} />
        <MetricCard label="Embeddings" value={metrics?.embeddings ?? "—"} tone="neutral" />
        <MetricCard label="Warnungen" value={metrics?.warnings ?? "—"} tone={metrics?.warnings ? "amber" : "neutral"} />
      </div>

      <section className="panel">
        <div className="panel-toolbar">
          <label className="search-field">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" />
          </label>
        </div>
        {/* Ohne diese beiden Bausteine wurde ein Backend-Fehler als "keine Projekte"
            gerendert — der Grund, warum ein DuckDB-Lock wie Datenverlust aussah. */}
        {projectsQuery.isError ? (
          <QueryError
            error={projectsQuery.error}
            title="Projekte konnten nicht geladen werden"
            onRetry={() => projectsQuery.refetch()}
          />
        ) : null}
        <DegradedNotice reason={projectsQuery.data?.degraded} onRetry={() => projectsQuery.refetch()} />
        <div className="list">
          {filteredProjects.map((project) => (
            <article
              className={`list-row project-list-row ${activeProject === project.id ? "list-row--active" : ""} ${project.pinned ? "project-list-row--pinned" : ""}`}
              key={project.id}
            >
              <button
                className="icon-button project-pin-button"
                type="button"
                aria-label={project.pinned ? "Nicht mehr anheften" : "Anheften"}
                title={project.pinned ? "Nicht mehr anheften" : "Oben anheften"}
                onClick={() => patchProject.mutate({ projectId: project.id, pinned: !project.pinned })}
                disabled={patchProject.isPending}
              >
                {project.pinned ? <Pin size={15} /> : <PinOff size={15} />}
              </button>
              {renameId === project.id ? (
                <div className="project-rename">
                  <input
                    autoFocus
                    value={renameValue}
                    aria-label="Projektname"
                    onChange={(event) => setRenameValue(event.target.value)}
                    onKeyDown={(event) => {
                      if (event.key === "Enter") commitRename(project.id);
                      if (event.key === "Escape") setRenameId(null);
                    }}
                  />
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Namen speichern"
                    onClick={() => commitRename(project.id)}
                    disabled={patchProject.isPending}
                  >
                    <Check size={16} />
                  </button>
                  <button className="icon-button" type="button" aria-label="Abbrechen" onClick={() => setRenameId(null)}>
                    <X size={16} />
                  </button>
                </div>
              ) : (
                <button className="project-list-main" type="button" onClick={() => setActiveProject(project.id)}>
                  <strong>{project.name}</strong>
                  <span>{project.paper_count} Papers</span>
                  <small>
                    {project.year_min && project.year_max ? `${project.year_min}-${project.year_max}` : "ohne Jahrspanne"}
                  </small>
                </button>
              )}
              {renameId === project.id ? null : (
                <button
                  className="icon-button"
                  type="button"
                  aria-label="Umbenennen"
                  title="Projekt umbenennen"
                  onClick={() => startRename(project)}
                >
                  <Pencil size={16} />
                </button>
              )}
              <button
                className="icon-button"
                type="button"
                aria-label="Exportieren"
                title={`Projekt als Bundle exportieren${includePdfsInExport ? " (mit PDFs)" : ""}`}
                onClick={() => exportBundle.mutate(project.id)}
                disabled={exportBundle.isPending}
              >
                {exportBundle.isPending && exportBundle.variables === project.id ? (
                  <Loader2 size={16} className="spin" />
                ) : (
                  <Download size={16} />
                )}
              </button>
              <button
                className="icon-button project-delete-button"
                type="button"
                aria-label="Loeschen"
                title="Projekt loeschen"
                onClick={() => removeProject(project.id)}
                disabled={deleteProject.isPending}
              >
                <Trash2 size={17} />
              </button>
            </article>
          ))}
        </div>
        {deleteError ? <div className="inline-error">{deleteError}</div> : null}
        {exportBundle.isError ? <QueryError error={exportBundle.error} title="Export fehlgeschlagen" /> : null}
        <label className="check-row project-export-option">
          <input
            type="checkbox"
            checked={includePdfsInExport}
            onChange={(event) => setIncludePdfsInExport(event.target.checked)}
          />
          <span>PDFs mitnehmen (größeres Bundle, dafür vollständig)</span>
        </label>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Bundle</span>
            <strong>Projekt importieren</strong>
          </div>
          <Upload size={18} />
        </div>
        <p className="muted">
          Spielt ein exportiertes Bundle ein: Paper, Extraktionsergebnisse, Web-Quellen und Embeddings.
          Der Knowledge Graph wird daraus neu berechnet — ein separater Graph-Export ist nicht nötig.
        </p>
        <label
          className={`drop-zone ${bundleDragOver ? "drop-zone--drag-over" : ""}`}
          onDragOver={(event) => {
            event.preventDefault();
            setBundleDragOver(true);
          }}
          onDragLeave={() => setBundleDragOver(false)}
          onDrop={(event) => {
            event.preventDefault();
            setBundleDragOver(false);
            selectBundle(event.dataTransfer.files?.[0] ?? null);
          }}
        >
          <input
            type="file"
            accept=".zip,application/zip"
            onChange={(event) => selectBundle(event.target.files?.[0] ?? null)}
          />
          <span>{bundleFile ? bundleFile.name : "Bundle-ZIP hierher ziehen oder auswählen"}</span>
        </label>

        {previewBundle.isPending ? (
          <div className="import-status-row">
            <Loader2 size={14} className="spin" />
            <span className="muted">Bundle wird gelesen …</span>
          </div>
        ) : null}
        {previewBundle.isError ? <QueryError error={previewBundle.error} title="Bundle konnte nicht gelesen werden" /> : null}

        {/* Erst zeigen, was drin ist — dann entscheiden lassen. */}
        {previewBundle.data ? (
          <div className="stack bundle-preview">
            <div className="bundle-preview-facts">
              <strong>{previewBundle.data.preview.project}</strong>
              <span className="muted">
                exportiert {previewBundle.data.preview.exported_at || "unbekannt"}
                {previewBundle.data.preview.includes_pdfs ? " · mit PDFs" : ""}
              </span>
            </div>
            <div className="bundle-preview-counts">
              <span>{previewBundle.data.preview.papers_new} neue Paper</span>
              <span>{previewBundle.data.preview.papers_existing} bereits vorhanden</span>
              <span>{previewBundle.data.preview.counts.extraction_results ?? 0} Extraktionen</span>
              <span>{previewBundle.data.preview.counts.grey_sources ?? 0} Web-Quellen</span>
            </div>
            {previewBundle.data.preview.warnings.map((warning) => (
              <div key={warning} className="warning-row">{warning}</div>
            ))}
            <div className="button-row">
              <label>
                Zielprojekt
                <input
                  value={importTarget}
                  onChange={(event) => setImportTarget(event.target.value)}
                  placeholder={previewBundle.data.preview.project}
                />
              </label>
              <div className="segmented">
                <button className={importMode === "merge" ? "active" : ""} type="button" onClick={() => setImportMode("merge")} title="Vorhandene Paper behalten, fehlende ergänzen">
                  Zusammenführen
                </button>
                <button className={importMode === "replace" ? "active" : ""} type="button" onClick={() => setImportMode("replace")} title="Die Paperliste des Zielprojekts durch die aus dem Bundle ersetzen">
                  Ersetzen
                </button>
              </div>
              <button
                className="button button-primary"
                type="button"
                disabled={!bundleFile || runImport.isPending}
                onClick={() => bundleFile && runImport.mutate({ file: bundleFile, mode: importMode, target: importTarget })}
              >
                {runImport.isPending ? <Loader2 size={16} className="spin" /> : <Upload size={16} />}
                <span>Importieren</span>
              </button>
            </div>
          </div>
        ) : null}

        {runImport.isError ? <QueryError error={runImport.error} title="Import fehlgeschlagen" /> : null}
        {runImport.data ? (
          <div className="hint-row">
            „{runImport.data.report.project}“ importiert: {runImport.data.report.papers_imported} Paper neu,
            {" "}{runImport.data.report.papers_skipped} übersprungen,
            {" "}{runImport.data.report.extractions_imported} Extraktionen,
            {" "}{runImport.data.report.grey_sources_imported} Web-Quellen
            {runImport.data.report.pdfs_imported ? `, ${runImport.data.report.pdfs_imported} PDFs` : ""}.
            {" "}Das Projekt hat jetzt {runImport.data.report.paper_count} Paper.
          </div>
        ) : null}
      </section>

      {/* Health ist hier Nebeninformation: eingeklappt eine Zeile, die volle
          KG-Health liegt in der Qualitaets-Seite. */}
      <section className="panel project-health">
        <button className="project-health-summary" type="button" onClick={() => setHealthOpen((open) => !open)}>
          <ChevronDown size={15} className={healthOpen ? "topic-group-chevron" : "topic-group-chevron--collapsed"} />
          <span className="project-health-label">Health</span>
          <Status value={health?.status ?? (activeProject ? "loading" : "idle")} />
          <span className="muted">
            {warnings.length} Warnungen · {latestJobs.length} Jobs
          </span>
        </button>
        {healthOpen ? (
          <>
            <div className="muted project-health-scope">{activeProject ?? "Alle Papers"}</div>
            <div className="warning-list">
              {warnings.map((warning) => (
                <div key={warning} className="warning-row">
                  {warning}
                </div>
              ))}
              {!warnings.length ? <div className="muted-row">Keine Warnungen</div> : null}
            </div>
            <div className="compact-table">
              {latestJobs.map((job) => (
                <div key={job.job_id} className="table-row">
                  <span>{job.job_id}</span>
                  <Status value={job.status} />
                  <span>
                    {job.papers_processed}/{job.papers_total}
                  </span>
                </div>
              ))}
            </div>
            <Link className="project-health-link" to="/quality">
              Vollständige KG-Health in Qualität
            </Link>
          </>
        ) : null}
      </section>
    </section>
  );
}
