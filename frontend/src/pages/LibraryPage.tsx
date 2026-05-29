import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Plus, Search } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Status } from "../components/Status";
import { useAppState } from "../state";

export function LibraryPage() {
  const { activeProject } = useAppState();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const queryClient = useQueryClient();
  const papersQuery = useQuery({
    queryKey: ["papers", query, activeProject],
    queryFn: () => api.listPapers({ query, project_id: activeProject, limit: 200 })
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

        {(papersQuery.data?.items ?? []).length ? (
          <div className="data-table">
            <div className="data-row data-row--head">
              <span />
              <span>Titel</span>
              <span>Jahr</span>
              <span>Quelle</span>
              <span>PDF</span>
              <span>Extraction</span>
            </div>
            {(papersQuery.data?.items ?? []).map((paper) => (
              <div className="data-row" key={paper.id}>
                <input type="checkbox" checked={allSelected.has(paper.id)} onChange={() => toggle(paper.id)} />
                <strong>{paper.title || paper.id}</strong>
                <span>{paper.year ?? "n/a"}</span>
                <span>{paper.source}</span>
                <Status value={paper.has_full_text ? "true" : "false"} />
                <Status value={paper.latest_extraction_status ?? "missing"} />
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title={papersQuery.isLoading ? "Lade Library" : "Keine Papers"} />
        )}
      </section>
    </section>
  );
}
