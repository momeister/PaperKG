import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Globe, Plus, Search, Star } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Status } from "../components/Status";
import { useAppState } from "../state";
import type { GreySource } from "../types";

const ALL_PAPERS_SCOPES = new Set(["", "__all_papers__"]);

export function LibraryPage() {
  const { activeProject } = useAppState();
  const isRealProject = !!activeProject && !ALL_PAPERS_SCOPES.has(activeProject);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [openGrey, setOpenGrey] = useState<string | null>(null);
  const queryClient = useQueryClient();

  const papersQuery = useQuery({
    queryKey: ["papers", query, activeProject],
    queryFn: () => api.listPapers({ query, project_id: activeProject, limit: 200 })
  });
  const projectsQuery = useQuery({ queryKey: ["projects"], queryFn: api.getProjects });
  const greyQuery = useQuery({
    queryKey: ["grey-sources", activeProject],
    queryFn: () => api.listGreySources(activeProject as string),
    enabled: isRealProject
  });

  const project = projectsQuery.data?.projects.find((entry) => entry.id === activeProject);
  const primaryPaperId = project?.primary_paper_id ?? null;

  const setPrimary = useMutation({
    mutationFn: (paperId: string | null) => api.setPrimaryPaper(activeProject as string, paperId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["projects"] })
  });
  const addToProject = useMutation({
    mutationFn: () => api.addProjectPapers(activeProject!, selected),
    onSuccess: () => {
      setSelected([]);
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["papers"] });
    }
  });

  const allSelected = useMemo(() => new Set(selected), [selected]);

  const papers = useMemo(() => {
    const items = papersQuery.data?.items ?? [];
    if (!primaryPaperId) {
      return items;
    }
    return [...items].sort((a, b) => {
      if (a.id === primaryPaperId) return -1;
      if (b.id === primaryPaperId) return 1;
      return 0;
    });
  }, [papersQuery.data?.items, primaryPaperId]);

  const greySources = greyQuery.data?.grey_sources ?? [];

  function toggle(id: string) {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span>Paper</span>
          <h1>Library</h1>
        </div>
        <button className="button" disabled={!activeProject || !selected.length || addToProject.isPending} onClick={() => addToProject.mutate()}>
          <Plus size={17} />
          <span>Zum Projekt</span>
        </button>
      </div>

      <section className="panel">
        <div className="panel-toolbar">
          <label className="search-field">
            <Search size={17} />
            <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Search" />
          </label>
          <span>{papersQuery.data?.total ?? 0} Treffer</span>
        </div>

        {papers.length ? (
          <div className="data-table library-table">
            <div className="data-row data-row--head">
              <span />
              <span>Titel</span>
              <span>Jahr</span>
              <span>Quelle</span>
              <span>PDF</span>
              <span>Extraction</span>
              <span>Hauptquelle</span>
            </div>
            {papers.map((paper) => {
              const isPrimary = paper.id === primaryPaperId;
              return (
                <div className={`data-row ${isPrimary ? "data-row--primary" : ""}`} key={paper.id}>
                  <input type="checkbox" checked={allSelected.has(paper.id)} onChange={() => toggle(paper.id)} />
                  <strong>
                    {isPrimary ? <span className="primary-badge">Hauptquelle</span> : null}
                    {paper.title || paper.id}
                  </strong>
                  <span>{paper.year ?? "n/a"}</span>
                  <span>{paper.source}</span>
                  <Status value={paper.has_full_text ? "true" : "false"} />
                  <Status value={paper.latest_extraction_status ?? "missing"} />
                  <button
                    className={`button button-compact ${isPrimary ? "button-primary" : ""}`}
                    type="button"
                    title={isRealProject ? "Als Hauptquelle markieren" : "Erst ein echtes Projekt wählen"}
                    disabled={!isRealProject || setPrimary.isPending}
                    onClick={() => setPrimary.mutate(isPrimary ? null : paper.id)}
                  >
                    <Star size={14} />
                  </button>
                </div>
              );
            })}
          </div>
        ) : (
          <EmptyState title={papersQuery.isLoading ? "Lade Library" : "Keine Papers"} />
        )}
      </section>

      {isRealProject && greySources.length ? (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Graue Quellen</span>
              <strong>{greySources.length} Web-/Deep-Research-Treffer</strong>
            </div>
            <Globe size={18} />
          </div>
          <p className="muted">
            Projektgebundene Zusatzinfo aus dem Web — getrennt von echten PDFs und nicht im Knowledge Graph.
          </p>
          <div className="stack">
            {greySources.map((source) => (
              <GreySourceCard
                key={source.id}
                source={source}
                open={openGrey === source.id}
                onToggle={() => setOpenGrey((current) => (current === source.id ? null : source.id))}
              />
            ))}
          </div>
        </section>
      ) : null}
    </section>
  );
}

function GreySourceCard({ source, open, onToggle }: { source: GreySource; open: boolean; onToggle: () => void }) {
  return (
    <article className="grey-source-card grey-source-card--library">
      <button type="button" className="grey-source-toggle" onClick={onToggle}>
        <span className="grey-badge">Graue Quelle</span>
        <strong>{source.title || source.url}</strong>
      </button>
      {open ? (
        <div className="grey-source-detail">
          {source.injection_flags.length ? (
            <div className="warning-row">⚠ Prompt-Injection-Flags ignoriert: {source.injection_flags.join(", ")}</div>
          ) : null}
          {source.summary ? <p>{source.summary}</p> : null}
          {source.query ? <p className="muted">Frage: {source.query}</p> : null}
          <a className="muted" href={source.url} target="_blank" rel="noreferrer">
            {source.url}
          </a>
        </div>
      ) : null}
    </article>
  );
}
