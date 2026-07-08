import { FormEvent, useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BookOpenCheck, ChevronDown, ChevronRight, Database, FileSearch, FilterX, Globe, ListChecks, Play, Plus, RefreshCw, Search, X } from "lucide-react";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Status } from "../components/Status";
import { useAppState } from "../state";
import type { BatchJobItem, ExtractionHistoryItem, ExtractionLibraryItem, ExtractionResultPayload, ExtractionRunResponse } from "../types";

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
  const [workbenchOpen, setWorkbenchOpen] = useState(false);
  const [pendingJobId, setPendingJobId] = useState<string | null>(null);
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
    mutationFn: () => {
      const jobId = crypto.randomUUID();
      setPendingJobId(jobId);
      return api.runExtractionBatch({
        items: selectedBatchItems(libraryQueryResult.data?.items ?? [], selectedBatchPaths),
        job_id: jobId,
        ...options,
        resume: true
      });
    },
    onSettled: () => setPendingJobId(null),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-history"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    }
  });

  const batchItemsQuery = useQuery({
    queryKey: ["batch-items", pendingJobId],
    queryFn: () => api.getExtractionBatchItems(pendingJobId!),
    enabled: !!pendingJobId,
    refetchInterval: 2000,
  });

  const cancelBatch = useMutation({
    mutationFn: (jobId: string) => api.cancelExtractionBatch(jobId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
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
    if (item.source_type === "grey" && item.text) {
      setText(item.text);
    }
    setActiveTab("run");
    setWorkbenchOpen(true);
  }

  function toggleBatch(id: string) {
    setSelectedBatchPaths((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  function toggleAllBatch() {
    const ids = (libraryQueryResult.data?.items ?? [])
      .filter((item) => isBatchable(item) && item.latest_extraction_status !== "success")
      .map((item) => item.paper_id);
    setSelectedBatchPaths((current) => (current.length === ids.length ? [] : ids));
  }

  function selectUnextracted() {
    const ids = (libraryQueryResult.data?.items ?? [])
      .filter((item) => isBatchable(item) && item.latest_extraction_status !== "success")
      .map((item) => item.paper_id);
    setSelectedBatchPaths(ids);
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
  const hasPdfItems = libraryItems.filter((i) => i.source_type !== "grey" && i.pdf_path && i.pdf_available !== false);
  const noPdfCount = libraryItems.filter((i) => i.source_type !== "grey" && (!i.pdf_path || i.pdf_available === false)).length;
  const totalPdfCount = hasPdfItems.length;
  const extractedPdfCount = hasPdfItems.filter((i) => i.latest_extraction_status === "success").length;
  const unextractedCount = libraryItems.filter((i) => isBatchable(i) && i.latest_extraction_status !== "success").length;
  const batchItems: BatchJobItem[] = batchItemsQuery.data?.items ?? [];
  const currentItem = batchItems.find((i) => i.status === "processing");
  const runningJob = pendingJobId ? jobsQuery.data?.jobs.find((j) => j.job_id === pendingJobId) : null;

  return (
    <section className="page extraction-page">
      <div className="page-title">
        <div>
          <h1>Extraktion</h1>
          <p className="page-subtitle">
            {activeProject ? `Projekt: ${activeProject} — PDFs & Webquellen` : "Alle Papers — globale Bibliothek"}
          </p>
        </div>
        <div className="extraction-header-actions">
          {totalPdfCount > 0 && (
            <div className="extraction-overview-badge">
              <strong>{extractedPdfCount}/{totalPdfCount}</strong>
              <span>extrahiert</span>
              {noPdfCount > 0 && <span className="muted">+{noPdfCount} ohne PDF</span>}
            </div>
          )}
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
      </div>

      {activeTab === "run" ? (
        <div className="extraction-run-stack">
          {/* Collapsible single-paper workbench */}
          <div className="panel extraction-workbench-wrapper">
            <button
              className="extraction-workbench-toggle"
              type="button"
              onClick={() => setWorkbenchOpen((o) => !o)}
            >
              {workbenchOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
              <span>Einzelextraktion</span>
              {lastResult && <Status value={lastResult.status} />}
            </button>
            {workbenchOpen && (
              <div className="extraction-workbench">
                <section className="extraction-input-panel">
                  <div className="panel-heading">
                    <div>
                      <span>Input</span>
                      <strong>{selectedPdf?.title || selectedPdf?.filename || "Text oder PDF"}</strong>
                    </div>
                    <Status value={parsePdf.isPending || extract.isPending ? "running" : lastResult?.status ?? "idle"} />
                  </div>
                  <form className="stack" onSubmit={submitExtract}>
                    <label>
                      Paper ID
                      <input value={paperId} onChange={(event) => setPaperId(event.target.value)} placeholder="paper-id" />
                    </label>
                    <label>
                      {selectedPdf?.source_type === "grey" ? "Webquelle" : "PDF"}
                      <select
                        value={selectedPdf?.paper_id ?? ""}
                        onChange={(event) => {
                          const item = libraryItems.find((candidate) => candidate.paper_id === event.target.value) ?? null;
                          setSelectedPdf(item);
                          if (item) {
                            setPaperId(item.paper_id);
                            if (item.source_type === "grey" && item.text) {
                              setText(item.text);
                            }
                          }
                        }}
                      >
                        <option value="">Keine Quelle ausgewählt</option>
                        {libraryItems.filter((item) => item.source_type !== "grey" && item.pdf_path).map((item) => (
                          <option key={item.paper_id} value={item.paper_id}>
                            {item.title || item.filename}
                          </option>
                        ))}
                        {libraryItems.some((item) => item.source_type === "grey") && (
                          <>
                            <option disabled value="">— Webquellen —</option>
                            {libraryItems.filter((item) => item.source_type === "grey").map((item) => (
                              <option key={item.paper_id} value={item.paper_id}>
                                {item.title}
                              </option>
                            ))}
                          </>
                        )}
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
                      <button className="button" type="button" disabled={!selectedPdf || selectedPdf.source_type === "grey" || selectedPdf.pdf_available === false || parsePdf.isPending} onClick={() => parsePdf.mutate()}>
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

                <section className="extraction-result-panel">
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
            )}
          </div>

          {/* Batch panel */}
          <section className="panel extraction-batch-panel">
            <div className="panel-heading">
              <div>
                <span>Batch-Extraktion</span>
                <strong>{extractedPdfCount}/{totalPdfCount} extrahiert · {selectedBatchPaths.length} ausgewählt</strong>
              </div>
              <div className="button-row">
                <button className="button" type="button" disabled={unextractedCount === 0} onClick={selectUnextracted} title="Alle noch nicht erfolgreich extrahierten PDFs auswählen">
                  <FilterX size={16} />
                  <span>Nicht extrahiert ({unextractedCount})</span>
                </button>
                <button className="button" type="button" onClick={toggleAllBatch}>
                  <ListChecks size={16} />
                  <span>{selectedBatchPaths.length === unextractedCount && unextractedCount > 0 ? "Leeren" : "Alle"}</span>
                </button>
                <button className="button button-primary" type="button" disabled={!selectedBatchPaths.length || batch.isPending} onClick={() => batch.mutate()}>
                  <Play size={16} />
                  <span>Ausführen</span>
                </button>
              </div>
            </div>
            <ErrorBox error={batch.error} />

            {/* Live status during batch */}
            {batch.isPending && pendingJobId && (
              <div className="status-strip status-strip--active">
                <Status value="running" />
                {currentItem ? (
                  <span className="current-paper" title={currentItem.paper_id}>⚙ {currentItem.paper_id}</span>
                ) : (
                  <span className="muted">Vorbereitung…</span>
                )}
                <span>
                  {runningJob ? `${runningJob.papers_processed}/${runningJob.papers_total}` : `0/${selectedBatchPaths.length}`}
                </span>
                <button
                  className="button button-compact"
                  type="button"
                  disabled={cancelBatch.isPending}
                  onClick={() => cancelBatch.mutate(pendingJobId)}
                  title="Batch-Job abbrechen"
                >
                  <X size={14} />
                  <span>Abbrechen</span>
                </button>
              </div>
            )}

            {/* Result after batch completes */}
            {batch.data && !batch.isPending && (
              <div className={`status-strip ${batch.data.job.papers_failed > 0 ? "status-strip--error" : ""}`}>
                <Status value={batch.data.job.status} />
                <span>{batch.data.job.papers_processed}/{batch.data.job.papers_total} verarbeitet</span>
                {batch.data.job.papers_failed > 0 && (
                  <span className="error-badge">{batch.data.job.papers_failed} Fehler</span>
                )}
              </div>
            )}

            {/* Extraction log */}
            {batchItems.length > 0 && (
              <details className="extraction-log" open={batchItems.some((i) => i.status === "failed")}>
                <summary>
                  Log · {batchItems.filter((i) => i.status === "completed").length} OK
                  {batchItems.filter((i) => i.status === "failed").length > 0 && (
                    <span className="error-badge"> · {batchItems.filter((i) => i.status === "failed").length} Fehler</span>
                  )}
                </summary>
                <div className="extraction-log-list">
                  {batchItems.map((item) => (
                    <div key={item.paper_id} className={`log-row log-row--${item.status}`}>
                      <Status value={item.status} />
                      <span className="log-paper-id">{item.paper_id}</span>
                      {item.error_message && <span className="log-error">{item.error_message}</span>}
                    </div>
                  ))}
                </div>
              </details>
            )}

            <ExtractionLibraryTable items={libraryItems} selectedPath={selectedPdf?.paper_id ?? ""} selectedBatchPaths={selectedBatchPaths} onSelect={selectPdf} onToggleBatch={toggleBatch} batchMode />
            <JobsMiniList jobs={jobsQuery.data?.jobs ?? []} />
          </section>
        </div>
      ) : null}

      {activeTab === "library" ? (
        <section className="panel">
          <LibraryToolbar query={libraryQuery} setQuery={setLibraryQuery} onRefresh={() => libraryQueryResult.refetch()} />
          <ExtractionLibraryTable items={libraryItems} selectedPath={selectedPdf?.paper_id ?? ""} selectedBatchPaths={selectedBatchPaths} onSelect={selectPdf} onToggleBatch={toggleBatch} />
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
  onToggleBatch: (id: string) => void;
}) {
  const [pdfsOpen, setPdfsOpen] = useState(true);
  const [webOpen, setWebOpen] = useState(true);
  // Filter/Sortierung für die PDF-Liste: Status (auch "ohne PDF"/fehlgeschlagen) + Datum.
  const [statusFilter, setStatusFilter] = useState<"alle" | "unextracted" | "success" | "failed" | "nopdf">("alle");
  const [sortBy, setSortBy] = useState<"title" | "status" | "date">("title");

  if (!items.length) {
    return <EmptyState title="Keine PDFs oder Webquellen" />;
  }

  const matchesStatus = (item: ExtractionLibraryItem) => {
    const noPdf = item.pdf_available === false;
    switch (statusFilter) {
      case "unextracted":
        return item.latest_extraction_status !== "success";
      case "success":
        return item.latest_extraction_status === "success";
      case "failed":
        return item.latest_extraction_status === "failed";
      case "nopdf":
        return noPdf;
      default:
        return true;
    }
  };
  const pdfItems = items
    .filter((item) => item.source_type !== "grey")
    .filter(matchesStatus)
    .sort((left, right) => {
      if (sortBy === "status") {
        return String(left.latest_extraction_status ?? "").localeCompare(String(right.latest_extraction_status ?? ""));
      }
      if (sortBy === "date") {
        return String(right.modified_timestamp ?? "").localeCompare(String(left.modified_timestamp ?? ""));
      }
      return String(left.title || left.filename).localeCompare(String(right.title || right.filename));
    });
  const greyItems = items.filter((item) => item.source_type === "grey");

  const renderRow = (item: ExtractionLibraryItem) => {
    const isGrey = item.source_type === "grey";
    const noPdf = !isGrey && item.pdf_available === false;
    const batchable = isBatchable(item);
    const alreadyExtracted = !isGrey && !noPdf && item.latest_extraction_status === "success";
    return (
      <div className={`data-row ${selectedPath === item.paper_id ? "data-row--active" : ""} ${noPdf ? "data-row--muted" : ""}`} key={item.paper_id}>
        <label className="check-row extraction-row-check">
          {!batchable ? (
            <Globe size={14} style={{ opacity: 0.5 }} />
          ) : (
            <>
              <input type="checkbox" checked={selectedBatchPaths.includes(item.paper_id)} onChange={() => onToggleBatch(item.paper_id)} />
              {alreadyExtracted && batchMode ? (
                <span title="Bereits extrahiert — erneutes Anhaken extrahiert neu" style={{ color: "var(--success, #16a34a)", fontSize: "0.9rem" }}>✓</span>
              ) : null}
            </>
          )}
        </label>
        <strong>
          {isGrey && <Globe size={13} style={{ marginRight: 4, verticalAlign: "middle", opacity: 0.7 }} />}
          {item.title || item.filename}
        </strong>
        <span>{item.paper_id}</span>
        <span>{item.latest_extraction_status ? <Status value={item.latest_extraction_status} /> : "missing"}</span>
        <span>
          {noPdf ? (
            <span className="muted" title="Kein PDF — Extraktion nutzt Titel + Abstract">nur Abstract</span>
          ) : (
            formatBytes(item.size_bytes)
          )}
        </span>
        <button className={batchMode ? "button button-compact" : "button button-primary button-compact"} type="button" onClick={() => onSelect(item)}>
          <FileSearch size={15} />
          <span>{batchMode ? "Öffnen" : "Auswählen"}</span>
        </button>
      </div>
    );
  };

  const tableHeader = (
    <div className="data-row data-row--head">
      <span>Auswahl</span>
      <span>Quelle</span>
      <span>Paper ID</span>
      <span>Status</span>
      <span>Größe</span>
      <span>Aktion</span>
    </div>
  );

  return (
    <div className="extraction-library-sections">
      <div className="extraction-library-filters">
        <label>
          Status
          <select value={statusFilter} onChange={(event) => setStatusFilter(event.target.value as typeof statusFilter)}>
            <option value="alle">Alle</option>
            <option value="unextracted">Nicht extrahiert</option>
            <option value="success">Extrahiert</option>
            <option value="failed">Fehlgeschlagen</option>
            <option value="nopdf">Ohne PDF (nur Abstract)</option>
          </select>
        </label>
        <label>
          Sortierung
          <select value={sortBy} onChange={(event) => setSortBy(event.target.value as typeof sortBy)}>
            <option value="title">Titel</option>
            <option value="status">Status</option>
            <option value="date">Datum (neueste zuerst)</option>
          </select>
        </label>
      </div>
      <div>
        <button className="extraction-section-toggle" type="button" onClick={() => setPdfsOpen((o) => !o)}>
          {pdfsOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
          <span>PDFs</span>
          <strong>{pdfItems.length}</strong>
        </button>
        {pdfsOpen && (
          <div className="data-table extraction-library-table">
            {tableHeader}
            {pdfItems.map(renderRow)}
          </div>
        )}
      </div>
      {greyItems.length > 0 && (
        <div>
          <button className="extraction-section-toggle" type="button" onClick={() => setWebOpen((o) => !o)}>
            {webOpen ? <ChevronDown size={14} /> : <ChevronRight size={14} />}
            <span>Webquellen</span>
            <strong>{greyItems.length}</strong>
          </button>
          {webOpen && (
            <div className="data-table extraction-library-table">
              {tableHeader}
              {greyItems.map(renderRow)}
            </div>
          )}
        </div>
      )}
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

function isBatchable(item: ExtractionLibraryItem) {
  if (item.source_type === "grey") {
    return false;
  }
  if (item.pdf_available === false || !item.pdf_path) {
    // Ohne PDF nur batchbar, wenn ein Abstract für die Abstract-only-Extraktion existiert.
    return item.abstract_available === true;
  }
  return true;
}

function selectedBatchItems(items: ExtractionLibraryItem[], selectedIds: string[]) {
  return items
    .filter((item) => selectedIds.includes(item.paper_id) && isBatchable(item))
    .map((item) => ({ paper_id: item.paper_id, pdf_path: item.pdf_path || undefined }));
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
