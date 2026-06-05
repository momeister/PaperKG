import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, Database, FileSearch, ListChecks, Play, Plus, RefreshCw, Search } from "lucide-react";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Status } from "../components/Status";
import { useAppState } from "../state";
import type { ExtractionHistoryItem, ExtractionLibraryItem, ExtractionResultPayload, ExtractionRunResponse } from "../types";

type ExtractionTab = "run" | "library" | "vocabulary" | "history";

const parserOptions = [
  { value: "", label: "Auto" },
  { value: "marker", label: "Marker" },
  { value: "nougat", label: "Nougat" },
  { value: "table_transformer", label: "Table Transformer" },
  { value: "vlm", label: "VLM" }
];

const modeOptions = [
  { value: "quality", label: "Quality" },
  { value: "quick", label: "Quick" }
];

export function ExtractionPage() {
  const { provider, model, activeProject } = useAppState();
  const queryClient = useQueryClient();
  const [activeTab, setActiveTab] = useState<ExtractionTab>("run");
  const [libraryQuery, setLibraryQuery] = useState("");
  const [historyPaperId, setHistoryPaperId] = useState("");
  const [selectedPdf, setSelectedPdf] = useState<ExtractionLibraryItem | null>(null);
  const [paperId, setPaperId] = useState("");
  const [text, setText] = useState("");
  const [parser, setParser] = useState("");
  const [extractionMode, setExtractionMode] = useState("quality");
  const [linkConcepts, setLinkConcepts] = useState(true);
  const [temperature, setTemperature] = useState(0.2);
  const [topP, setTopP] = useState(0.9);
  const [contextSize, setContextSize] = useState(32768);
  const [maxTokens, setMaxTokens] = useState(16384);
  const [selectedBatchPaths, setSelectedBatchPaths] = useState<string[]>([]);
  const [lastResult, setLastResult] = useState<ExtractionRunResponse | null>(null);
  const [vocabCanonical, setVocabCanonical] = useState("");
  const [vocabAliases, setVocabAliases] = useState("");
  const [vocabDomain, setVocabDomain] = useState("");
  const [vocabOpenAlex, setVocabOpenAlex] = useState("");

  const libraryQueryResult = useQuery({
    queryKey: ["extraction-library", libraryQuery, activeProject ?? ""],
    queryFn: () => api.getExtractionLibrary(libraryQuery, activeProject || undefined)
  });
  const historyQuery = useQuery({
    queryKey: ["extraction-history", historyPaperId],
    queryFn: () => api.getExtractionHistory(historyPaperId)
  });
  const vocabularyQuery = useQuery({
    queryKey: ["extraction-vocabulary"],
    queryFn: api.getExtractionVocabulary
  });
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: api.getJobs, refetchInterval: 5000 });

  const options = useMemo(
    () => ({
      provider,
      model,
      parser: parser || undefined,
      temperature,
      top_p: topP,
      context_size: contextSize,
      max_tokens: maxTokens,
      extraction_mode: extractionMode,
      link_concepts: linkConcepts
    }),
    [contextSize, extractionMode, linkConcepts, maxTokens, model, parser, provider, temperature, topP]
  );

  const parsePdf = useMutation({
    mutationFn: () =>
      api.parseExtractionPdf({
        paper_id: paperId.trim() || selectedPdf?.paper_id || "document",
        pdf_path: selectedPdf?.pdf_path,
        parser: parser || undefined
      }),
    onSuccess: (payload) => {
      setPaperId(payload.paper_id);
      setText(payload.text);
    }
  });

  const extract = useMutation({
    mutationFn: () =>
      api.runExtraction({
        paper_id: paperId.trim() || selectedPdf?.paper_id || "document",
        text: text.trim() || undefined,
        pdf_path: selectedPdf?.pdf_path,
        ...options
      }),
    onSuccess: (payload) => {
      setLastResult(payload);
      queryClient.invalidateQueries({ queryKey: ["extraction-history"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    }
  });

  const batch = useMutation({
    mutationFn: () =>
      api.runExtractionBatch({
        items: selectedBatchItems(libraryQueryResult.data?.items ?? [], selectedBatchPaths),
        ...options,
        resume: true
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-history"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    }
  });

  const addVocabulary = useMutation({
    mutationFn: () =>
      api.addExtractionVocabulary({
        canonical_label: vocabCanonical.trim(),
        aliases: vocabAliases.split(",").map((item) => item.trim()).filter(Boolean),
        domain: vocabDomain.trim() || undefined,
        openalx_id: vocabOpenAlex.trim() || undefined
      }),
    onSuccess: () => {
      setVocabCanonical("");
      setVocabAliases("");
      setVocabDomain("");
      setVocabOpenAlex("");
      queryClient.invalidateQueries({ queryKey: ["extraction-vocabulary"] });
    }
  });

  function selectPdf(item: ExtractionLibraryItem) {
    setSelectedPdf(item);
    setPaperId(item.paper_id);
    setActiveTab("run");
  }

  function toggleBatch(path: string) {
    setSelectedBatchPaths((current) => (current.includes(path) ? current.filter((item) => item !== path) : [...current, path]));
  }

  function toggleAllBatch() {
    const paths = (libraryQueryResult.data?.items ?? []).map((item) => item.pdf_path);
    setSelectedBatchPaths((current) => (current.length === paths.length ? [] : paths));
  }

  function submitExtract(event: FormEvent) {
    event.preventDefault();
    if (paperId.trim() || selectedPdf || text.trim()) {
      extract.mutate();
    }
  }

  const libraryItems = libraryQueryResult.data?.items ?? [];
  const historyItems = historyQuery.data?.items ?? [];
  const vocabularyItems = vocabularyQuery.data?.items ?? [];

  return (
    <section className="page extraction-page">
      <div className="page-title">
        <div>
          <h1>Extraktion</h1>
          <p className="page-subtitle">
            {activeProject ? `Projekt: ${activeProject} — nur Projekt-PDFs` : "Alle Papers — globale Bibliothek"}
          </p>
        </div>
        <div className="segmented extraction-tabs">
          <button className={activeTab === "run" ? "active" : ""} type="button" onClick={() => setActiveTab("run")}>
            <Play size={16} />
            <span>Ausführen</span>
          </button>
          <button className={activeTab === "library" ? "active" : ""} type="button" onClick={() => setActiveTab("library")}>
            <FileSearch size={16} />
            <span>PDFs</span>
          </button>
          <button className={activeTab === "vocabulary" ? "active" : ""} type="button" onClick={() => setActiveTab("vocabulary")}>
            <BookOpenCheck size={16} />
            <span>Vokabular</span>
          </button>
          <button className={activeTab === "history" ? "active" : ""} type="button" onClick={() => setActiveTab("history")}>
            <Database size={16} />
            <span>Historie</span>
          </button>
        </div>
      </div>

      {activeTab === "run" ? (
        <div className="extraction-run-stack">
        <div className="extraction-workbench">
          <section className="panel extraction-input-panel">
            <div className="panel-heading">
              <div>
                <span>Input</span>
                <strong>{selectedPdf?.filename ?? "Text oder PDF"}</strong>
              </div>
              <Status value={parsePdf.isPending || extract.isPending ? "running" : lastResult?.status ?? "idle"} />
            </div>
            <form className="stack" onSubmit={submitExtract}>
              <label>
                Paper ID
                <input value={paperId} onChange={(event) => setPaperId(event.target.value)} placeholder="paper-id" />
              </label>
              <label>
                PDF
                <select
                  value={selectedPdf?.pdf_path ?? ""}
                  onChange={(event) => {
                    const item = libraryItems.find((candidate) => candidate.pdf_path === event.target.value) ?? null;
                    setSelectedPdf(item);
                    if (item) {
                      setPaperId(item.paper_id);
                    }
                  }}
                >
                  <option value="">Keine PDF ausgewaehlt</option>
                  {libraryItems.map((item) => (
                    <option key={item.pdf_path} value={item.pdf_path}>
                      {item.title || item.filename}
                    </option>
                  ))}
                </select>
              </label>
              <div className="extraction-options-grid">
                <label>
                  Parser
                  <select value={parser} onChange={(event) => setParser(event.target.value)}>
                    {parserOptions.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
                <label>
                  Mode
                  <select value={extractionMode} onChange={(event) => setExtractionMode(event.target.value)}>
                    {modeOptions.map((item) => (
                      <option key={item.value} value={item.value}>
                        {item.label}
                      </option>
                    ))}
                  </select>
                </label>
              </div>
              <textarea className="extraction-textarea" value={text} onChange={(event) => setText(event.target.value)} placeholder="Paper-Text" />
              <div className="extraction-options-grid extraction-options-grid--wide">
                <label>
                  Temperature
                  <input type="number" min={0} max={2} step={0.05} value={temperature} onChange={(event) => setTemperature(Number(event.target.value))} />
                </label>
                <label>
                  Top P
                  <input type="number" min={0} max={1} step={0.05} value={topP} onChange={(event) => setTopP(Number(event.target.value))} />
                </label>
                <label>
                  Context
                  <input type="number" min={1024} step={1024} value={contextSize} onChange={(event) => setContextSize(Number(event.target.value))} />
                </label>
                <label>
                  Max Tokens
                  <input type="number" min={256} step={256} value={maxTokens} onChange={(event) => setMaxTokens(Number(event.target.value))} />
                </label>
              </div>
              <label className="check-row">
                <input type="checkbox" checked={linkConcepts} onChange={() => setLinkConcepts((current) => !current)} />
                <span>Entity Linking</span>
              </label>
              <div className="button-row">
                <button className="button" type="button" disabled={!selectedPdf || parsePdf.isPending} onClick={() => parsePdf.mutate()}>
                  <FileSearch size={16} />
                  <span>Parsen</span>
                </button>
                <button className="button button-primary" type="submit" disabled={extract.isPending || (!text.trim() && !selectedPdf)}>
                  <Play size={16} />
                  <span>Ausführen</span>
                </button>
              </div>
            </form>
            <ErrorBox error={parsePdf.error || extract.error} />
          </section>

          <section className="panel extraction-result-panel">
            <div className="panel-heading">
              <div>
                <span>Result</span>
                <strong>{lastResult?.paper_id ?? "Noch keine Extraktion"}</strong>
              </div>
              {lastResult ? <Status value={lastResult.status} /> : null}
            </div>
            {lastResult ? <ExtractionResultView response={lastResult} /> : <EmptyState title="Kein Ergebnis" />}
          </section>
        </div>
        <section className="panel extraction-batch-panel">
          <div className="panel-heading">
            <div>
              <span>Mehrere PDFs</span>
              <strong>{selectedBatchPaths.length} ausgewählt</strong>
            </div>
            <div className="button-row">
              <button className="button" type="button" onClick={toggleAllBatch}>
                <ListChecks size={16} />
                <span>{selectedBatchPaths.length === libraryItems.length && libraryItems.length ? "Leeren" : "Alle"}</span>
              </button>
              <button className="button button-primary" type="button" disabled={!selectedBatchPaths.length || batch.isPending} onClick={() => batch.mutate()}>
                <Play size={16} />
                <span>Auswahl ausführen</span>
              </button>
            </div>
          </div>
          <ErrorBox error={batch.error} />
          {batch.data ? (
            <div className="status-strip">
              <Status value={batch.data.job.status} />
              <span>
                {batch.data.job.papers_processed}/{batch.data.job.papers_total}
              </span>
              <span>{batch.data.job.papers_failed} failed</span>
            </div>
          ) : null}
          <ExtractionLibraryTable items={libraryItems} selectedPath={selectedPdf?.pdf_path ?? ""} selectedBatchPaths={selectedBatchPaths} onSelect={selectPdf} onToggleBatch={toggleBatch} batchMode />
          <JobsMiniList jobs={jobsQuery.data?.jobs ?? []} />
        </section>
        </div>
      ) : null}

      {activeTab === "library" ? (
        <section className="panel">
          <LibraryToolbar query={libraryQuery} setQuery={setLibraryQuery} onRefresh={() => libraryQueryResult.refetch()} />
          <ExtractionLibraryTable items={libraryItems} selectedPath={selectedPdf?.pdf_path ?? ""} selectedBatchPaths={selectedBatchPaths} onSelect={selectPdf} onToggleBatch={toggleBatch} />
        </section>
      ) : null}

      {activeTab === "vocabulary" ? (
        <div className="two-column extraction-vocabulary-grid">
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span>Vocabulary</span>
                <strong>{vocabularyItems.length} Eintraege</strong>
              </div>
            </div>
            {vocabularyItems.length ? (
              <div className="data-table extraction-vocabulary-table">
                <div className="data-row data-row--head">
                  <span>Canonical</span>
                  <span>Aliases</span>
                  <span>Domain</span>
                  <span>OpenAlex</span>
                </div>
                {vocabularyItems.map((item) => (
                  <div className="data-row" key={item.canonical_label}>
                    <strong>{item.canonical_label}</strong>
                    <span>{item.aliases.join(", ")}</span>
                    <span>{item.domain ?? ""}</span>
                    <span>{item.openalx_id ?? ""}</span>
                  </div>
                ))}
              </div>
            ) : (
              <EmptyState title="Kein Vocabulary" />
            )}
          </section>
          <section className="panel">
            <div className="panel-heading">
              <div>
                <span>Neu</span>
                <strong>Eintrag</strong>
              </div>
            </div>
            <form
              className="stack"
              onSubmit={(event) => {
                event.preventDefault();
                if (vocabCanonical.trim()) {
                  addVocabulary.mutate();
                }
              }}
            >
              <label>
                Canonical Label
                <input value={vocabCanonical} onChange={(event) => setVocabCanonical(event.target.value)} />
              </label>
              <label>
                Aliases
                <input value={vocabAliases} onChange={(event) => setVocabAliases(event.target.value)} placeholder="alias 1, alias 2" />
              </label>
              <label>
                Domain
                <input value={vocabDomain} onChange={(event) => setVocabDomain(event.target.value)} />
              </label>
              <label>
                OpenAlex ID
                <input value={vocabOpenAlex} onChange={(event) => setVocabOpenAlex(event.target.value)} />
              </label>
              <button className="button button-primary" type="submit" disabled={!vocabCanonical.trim() || addVocabulary.isPending}>
                <Plus size={16} />
                <span>Hinzufuegen</span>
              </button>
            </form>
            <ErrorBox error={addVocabulary.error} />
          </section>
        </div>
      ) : null}

      {activeTab === "history" ? (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>History</span>
              <strong>{historyItems.length} Runs</strong>
            </div>
            <label className="search-field extraction-history-search">
              <Search size={16} />
              <input value={historyPaperId} onChange={(event) => setHistoryPaperId(event.target.value)} placeholder="Paper ID" />
            </label>
          </div>
          <ExtractionHistoryTable items={historyItems} />
        </section>
      ) : null}
    </section>
  );
}

function LibraryToolbar({ query, setQuery, onRefresh }: { query: string; setQuery: (value: string) => void; onRefresh: () => void }) {
  return (
    <div className="panel-heading">
      <div>
        <span>PDF Library</span>
        <strong>Lokale PDFs</strong>
      </div>
      <div className="button-row">
        <label className="search-field">
          <Search size={16} />
          <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="PDF suchen" />
        </label>
        <button className="button" type="button" onClick={onRefresh}>
          <RefreshCw size={16} />
          <span>Refresh</span>
        </button>
      </div>
    </div>
  );
}

function ExtractionLibraryTable({
  items,
  selectedPath,
  selectedBatchPaths,
  batchMode = false,
  onSelect,
  onToggleBatch
}: {
  items: ExtractionLibraryItem[];
  selectedPath: string;
  selectedBatchPaths: string[];
  batchMode?: boolean;
  onSelect: (item: ExtractionLibraryItem) => void;
  onToggleBatch: (path: string) => void;
}) {
  if (!items.length) {
    return <EmptyState title="Keine PDFs" />;
  }
  return (
    <div className="data-table extraction-library-table">
      <div className="data-row data-row--head">
        <span>Auswahl</span>
        <span>PDF</span>
        <span>Paper ID</span>
        <span>Status</span>
        <span>Groesse</span>
        <span>Aktion</span>
      </div>
      {items.map((item) => (
        <div className={`data-row ${selectedPath === item.pdf_path ? "data-row--active" : ""}`} key={item.pdf_path}>
          <label className="check-row extraction-row-check">
            <input type="checkbox" checked={selectedBatchPaths.includes(item.pdf_path)} onChange={() => onToggleBatch(item.pdf_path)} />
          </label>
          <strong>{item.title || item.filename}</strong>
          <span>{item.paper_id}</span>
          <span>{item.latest_extraction_status ? <Status value={item.latest_extraction_status} /> : "missing"}</span>
          <span>{formatBytes(item.size_bytes)}</span>
          <button className={batchMode ? "button button-compact" : "button button-primary button-compact"} type="button" onClick={() => onSelect(item)}>
            <FileSearch size={15} />
            <span>{batchMode ? "Oeffnen" : "Auswaehlen"}</span>
          </button>
        </div>
      ))}
    </div>
  );
}

function ExtractionResultView({ response }: { response: ExtractionRunResponse }) {
  const result = response.result;
  return (
    <div className="extraction-result-stack">
      <div className="metric-grid">
        <Metric label="Concepts" value={result.concepts.length} />
        <Metric label="Methods" value={result.methods.length} />
        <Metric label="Claims" value={result.claims.length} />
        <Metric label="Candidates" value={result.candidate_count || result.concept_candidates.length + result.method_candidates.length} />
      </div>
      {response.error_message ? <div className="inline-error">{response.error_message}</div> : null}
      {result.quality_warnings.map((warning) => (
        <div className="warning-row" key={warning}>
          {warning}
        </div>
      ))}
      <ExtractionItems title="Concepts" items={result.concepts} />
      <ExtractionItems title="Methods" items={result.methods} />
      <ExtractionClaims items={result.claims} />
      <ExtractionItems title="Relations" items={result.relations} />
      <details className="extraction-json-details">
        <summary>Diagnostics</summary>
        <pre>{JSON.stringify(result.extraction_diagnostics || {}, null, 2)}</pre>
      </details>
    </div>
  );
}

function Metric({ label, value }: { label: string; value: number | string }) {
  return (
    <div className="metric-card metric-card--compact">
      <span>{label}</span>
      <strong>{value}</strong>
    </div>
  );
}

function ExtractionItems({ title, items }: { title: string; items: Array<Record<string, unknown>> }) {
  return (
    <section className="extraction-result-section">
      <h2>{title}</h2>
      {items.length ? (
        <div className="extraction-chip-list">
          {items.slice(0, 24).map((item, index) => (
            <article className="extraction-chip" key={`${title}-${index}-${itemLabel(item)}`}>
              <strong>{itemLabel(item)}</strong>
              <span>{itemMeta(item)}</span>
              {textValue(item.context ?? item.evidence ?? item.evidence_span ?? item.description) ? <p>{textValue(item.context ?? item.evidence ?? item.evidence_span ?? item.description)}</p> : null}
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title={`Keine ${title}`} />
      )}
    </section>
  );
}

function ExtractionClaims({ items }: { items: Array<Record<string, unknown>> }) {
  return (
    <section className="extraction-result-section">
      <h2>Claims</h2>
      {items.length ? (
        <div className="extraction-claim-list">
          {items.slice(0, 16).map((item, index) => (
            <article className="extraction-claim" key={`claim-${index}`}>
              <p>{textValue(item.statement ?? item.claim ?? item.text)}</p>
              <span>{itemMeta(item)}</span>
            </article>
          ))}
        </div>
      ) : (
        <EmptyState title="Keine Claims" />
      )}
    </section>
  );
}

function ExtractionHistoryTable({ items }: { items: ExtractionHistoryItem[] }) {
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const selected = items.find((item) => item.id === selectedId) ?? items[0] ?? null;
  if (!items.length) {
    return <EmptyState title="Keine History" />;
  }
  return (
    <div className="extraction-history-layout">
      <div className="data-table extraction-history-table">
        <div className="data-row data-row--head">
          <span>ID</span>
          <span>Paper</span>
          <span>Status</span>
          <span>Model</span>
          <span>Counts</span>
        </div>
        {items.map((item) => (
          <button className={`data-row ${selected?.id === item.id ? "data-row--active" : ""}`} type="button" key={item.id} onClick={() => setSelectedId(item.id)}>
            <strong>#{item.id}</strong>
            <span>{item.paper_id}</span>
            <span>{item.extraction_status ? <Status value={item.extraction_status} /> : "unknown"}</span>
            <span>{item.llm_model ?? item.llm_provider ?? ""}</span>
            <span>
              {(item.concepts ?? []).length}/{(item.methods ?? []).length}/{(item.claims ?? []).length}
            </span>
          </button>
        ))}
      </div>
      {selected ? (
        <div className="extraction-history-detail">
          <strong>{selected.paper_id}</strong>
          <span>{selected.extraction_timestamp ?? ""}</span>
          {selected.error_message ? <div className="inline-error">{selected.error_message}</div> : null}
          <ExtractionItems title="Concepts" items={selected.concepts ?? []} />
          <ExtractionItems title="Methods" items={selected.methods ?? []} />
          <ExtractionClaims items={selected.claims ?? []} />
        </div>
      ) : null}
    </div>
  );
}

function JobsMiniList({ jobs }: { jobs: Array<{ job_id: string; status: string; papers_processed: number; papers_total: number; papers_failed: number }> }) {
  if (!jobs.length) {
    return null;
  }
  return (
    <div className="extraction-jobs-strip">
      {jobs.slice(0, 4).map((job) => (
        <div className="status-strip" key={job.job_id}>
          <Status value={job.status} />
          <strong>{job.job_id}</strong>
          <span>
            {job.papers_processed}/{job.papers_total}
          </span>
          <span>{job.papers_failed} failed</span>
        </div>
      ))}
    </div>
  );
}

function ErrorBox({ error }: { error: unknown }) {
  if (!error) {
    return null;
  }
  const message = error instanceof ApiError ? formatApiError(error) : error instanceof Error ? error.message : String(error);
  return <div className="inline-error">{message}</div>;
}

function formatApiError(error: ApiError) {
  const detail = error.detail;
  if (typeof detail === "string") {
    return detail;
  }
  if (detail && typeof detail === "object") {
    const data = detail as Record<string, unknown>;
    const message = String(data.message ?? error.message);
    const parser = data.parser ? ` Parser: ${data.parser}.` : "";
    const paperId = data.paper_id ? ` Paper: ${data.paper_id}.` : "";
    const path = data.pdf_path ? ` Datei: ${data.pdf_path}.` : "";
    const reason = data.error ? ` Fehler: ${data.error}.` : "";
    return `${message}.${parser}${paperId}${path}${reason}`;
  }
  return error.message;
}

function selectedBatchItems(items: ExtractionLibraryItem[], selectedPaths: string[]) {
  return items.filter((item) => selectedPaths.includes(item.pdf_path)).map((item) => ({ paper_id: item.paper_id, pdf_path: item.pdf_path }));
}

function itemLabel(item: Record<string, unknown>) {
  return textValue(item.canonical_label ?? item.label ?? item.subject_label ?? item.subject_id ?? item.relation_type ?? item.id ?? "Eintrag");
}

function itemMeta(item: Record<string, unknown>) {
  const parts = [
    textValue(item.entity_type ?? item.type ?? item.relation_type),
    typeof item.confidence === "number" ? item.confidence.toFixed(2) : "",
    textValue(item.review_status)
  ].filter(Boolean);
  return parts.join(" | ");
}

function textValue(value: unknown) {
  return String(value ?? "").trim();
}

function formatBytes(value?: number | null) {
  if (!value) {
    return "";
  }
  if (value < 1024 * 1024) {
    return `${Math.round(value / 1024)} KB`;
  }
  return `${(value / 1024 / 1024).toFixed(1)} MB`;
}
