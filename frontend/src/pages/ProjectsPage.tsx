import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search, Trash2 } from "lucide-react";

import { api } from "../api";
import { MetricCard } from "../components/MetricCard";
import { Status } from "../components/Status";
import { useAppState } from "../state";
import type { Project } from "../types";

export function ProjectsPage() {
  const { activeProject, setActiveProject } = useAppState();
  const [query, setQuery] = useState("");
  const [name, setName] = useState("");
  const [deleteError, setDeleteError] = useState("");
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

  const filteredProjects = (projectsQuery.data?.projects ?? []).filter((project) => project.name.toLowerCase().includes(query.toLowerCase()));
  const metrics = dashboardQuery.data?.metrics;

  return (
    <section className="page">
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

      <div className="two-column two-column--wide-left">
        <section className="panel">
          <div className="panel-toolbar">
            <label className="search-field">
              <Search size={17} />
              <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" />
            </label>
          </div>
          <div className="list">
            {filteredProjects.map((project) => (
              <article className={`list-row project-list-row ${activeProject === project.id ? "list-row--active" : ""}`} key={project.id}>
                <button className="project-list-main" type="button" onClick={() => setActiveProject(project.id)}>
                  <strong>{project.name}</strong>
                  <span>{project.paper_count} Papers</span>
                  <small>
                    {project.year_min && project.year_max ? `${project.year_min}-${project.year_max}` : "ohne Jahrspanne"}
                  </small>
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
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Health</span>
              <strong>{activeProject ?? "Alle Papers"}</strong>
            </div>
            <Status value={dashboardQuery.data?.health.status ?? "loading"} />
          </div>
          <div className="warning-list">
            {(dashboardQuery.data?.health.warnings ?? []).map((warning) => (
              <div key={warning} className="warning-row">
                {warning}
              </div>
            ))}
            {!dashboardQuery.data?.health.warnings?.length ? <div className="muted-row">Keine Warnungen</div> : null}
          </div>
          <div className="compact-table">
            {(dashboardQuery.data?.latest_jobs ?? []).map((job) => (
              <div key={job.job_id} className="table-row">
                <span>{job.job_id}</span>
                <Status value={job.status} />
                <span>
                  {job.papers_processed}/{job.papers_total}
                </span>
              </div>
            ))}
          </div>
        </section>
      </div>
    </section>
  );
}
