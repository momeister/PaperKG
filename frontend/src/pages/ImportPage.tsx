import { ChangeEvent, FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Download, FileUp, Search } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Status } from "../components/Status";
import type { Paper } from "../types";

const sourceOptions = [
  { id: "arxiv", label: "arXiv" },
  { id: "semantic_scholar", label: "Semantic Scholar" },
  { id: "openalex", label: "OpenAlex" }
];

export function ImportPage() {
  const [topic, setTopic] = useState("");
  const [sources, setSources] = useState<string[]>(["arxiv"]);
  const [maxResults, setMaxResults] = useState(10);
  const [results, setResults] = useState<Paper[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const queryClient = useQueryClient();

  const search = useMutation({
    mutationFn: api.harvestSearch,
    onSuccess: (payload) => {
      setResults(payload.results);
      setWarnings(payload.warnings);
    }
  });
  const download = useMutation({
    mutationFn: (downloadPdfs: boolean) => api.harvestDownload(results, downloadPdfs),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    }
  });
  const upload = useMutation({
    mutationFn: ({ file, title }: { file: File; title?: string }) => api.uploadPdf(file, { title }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    }
  });

  function submit(event: FormEvent) {
    event.preventDefault();
    if (topic.trim()) {
      search.mutate({ query: topic.trim(), sources, max_results: maxResults });
    }
  }

  function toggleSource(source: string) {
    setSources((current) => (current.includes(source) ? current.filter((item) => item !== source) : [...current, source]));
  }

  function onFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    files.forEach((file) => upload.mutate({ file, title: file.name.replace(/\.pdf$/i, "") }));
  }

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span>Harvest</span>
          <h1>Import</h1>
        </div>
      </div>

      <div className="two-column">
        <section className="panel import-panel">
          <form onSubmit={submit} className="stack">
            <label>
              Thema
              <input value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="Topic oder Frage" />
            </label>
            <div className="checkbox-grid">
              {sourceOptions.map((source) => (
                <label key={source.id} className="check-row">
                  <input type="checkbox" checked={sources.includes(source.id)} onChange={() => toggleSource(source.id)} />
                  <span>{source.label}</span>
                </label>
              ))}
            </div>
            <label>
              Anzahl Paper
              <input type="number" min={1} max={50} value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} />
            </label>
            <button className="button button-primary" type="submit" disabled={search.isPending || !sources.length}>
              <Search size={17} />
              <span>Suchen</span>
            </button>
          </form>

          {warnings.map((warning) => (
            <div key={warning} className="warning-row">
              {warning}
            </div>
          ))}
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>PDF</span>
              <strong>Upload</strong>
            </div>
            <Status value={upload.isPending ? "running" : upload.isSuccess ? "success" : "idle"} />
          </div>
          <label className="drop-zone">
            <FileUp size={34} />
            <span>PDF auswählen</span>
            <input type="file" accept="application/pdf" multiple onChange={onFiles} />
          </label>
        </section>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Treffer</span>
            <strong>{results.length} Papers</strong>
          </div>
          <div className="button-row">
            <button className="button" type="button" disabled={!results.length || download.isPending} onClick={() => download.mutate(false)}>
              <Download size={16} />
              <span>Metadaten</span>
            </button>
            <button className="button button-primary" type="button" disabled={!results.length || download.isPending} onClick={() => download.mutate(true)}>
              <Download size={16} />
              <span>PDFs</span>
            </button>
          </div>
        </div>
        {results.length ? (
          <div className="paper-grid">
            {results.map((paper) => (
              <article key={`${paper.source}:${paper.source_id}`} className="paper-card">
                <strong>{paper.title || paper.id}</strong>
                <span>
                  {paper.source} · {paper.year ?? "n/a"}
                </span>
                <p>{paper.abstract || paper.doi || paper.source_id}</p>
              </article>
            ))}
          </div>
        ) : (
          <EmptyState title="Keine Treffer" />
        )}
      </section>
    </section>
  );
}
