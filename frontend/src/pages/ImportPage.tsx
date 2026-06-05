import { ChangeEvent, FormEvent, useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, CheckCircle2, Download, FileUp, Globe, Loader2, Search, Sparkles, Star, XCircle } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Status } from "../components/Status";
import { useAppState } from "../state";
import type {
  DeepResearchFinding,
  DiscoveryCandidate,
  HarvestDownloadResponse,
  Paper,
  ReferenceCandidate
} from "../types";

const sourceGroups: { group: string; options: { id: string; label: string; note?: string }[] }[] = [
  {
    group: "Allgemein",
    options: [
      { id: "arxiv", label: "arXiv" },
      { id: "semantic_scholar", label: "Semantic Scholar" },
      { id: "openalex", label: "OpenAlex" },
      { id: "crossref", label: "Crossref" }
    ]
  },
  {
    group: "Medizin & Biologie",
    options: [
      { id: "europepmc", label: "Europe PMC / PubMed" },
      { id: "biorxiv", label: "bioRxiv / medRxiv" }
    ]
  },
  {
    group: "Open Access (alle Fächer, inkl. Recht/Wirtschaft)",
    options: [
      { id: "core", label: "CORE", note: "API-Key (CORE_API_KEY) nötig" },
      { id: "doaj", label: "DOAJ" }
    ]
  }
];

const ALL_PAPERS_SCOPES = new Set(["", "__all_papers__"]);

function paperKey(paper: Paper): string {
  return paper.id || `${paper.source}:${paper.source_id}`;
}

export function ImportPage() {
  const { activeProject, provider } = useAppState();
  const navigate = useNavigate();
  const isRealProject = !!activeProject && !ALL_PAPERS_SCOPES.has(activeProject);
  const downloadProjectId = isRealProject ? (activeProject as string) : undefined;

  const [topic, setTopic] = useState("");
  const [sources, setSources] = useState<string[]>(["arxiv"]);
  const [maxResults, setMaxResults] = useState(10);
  const [results, setResults] = useState<Paper[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);

  const [uploaded, setUploaded] = useState<Paper[]>([]);
  const [references, setReferences] = useState<Record<string, ReferenceCandidate[]>>({});
  const [topicCandidates, setTopicCandidates] = useState<DiscoveryCandidate[]>([]);
  const [paperCandidates, setPaperCandidates] = useState<Record<string, DiscoveryCandidate[]>>({});
  const [findings, setFindings] = useState<DeepResearchFinding[]>([]);
  const [researchQuestion, setResearchQuestion] = useState("");
  const [autoDownload, setAutoDownload] = useState(false);
  const [primaryPaperId, setPrimaryPaperId] = useState<string | null>(null);
  const [savedFindingUrls, setSavedFindingUrls] = useState<string[]>([]);

  const queryClient = useQueryClient();

  function invalidateLibrary() {
    queryClient.invalidateQueries({ queryKey: ["papers"] });
    queryClient.invalidateQueries({ queryKey: ["health"] });
  }

  const search = useMutation({
    mutationFn: api.harvestSearch,
    onSuccess: (payload) => {
      setResults(payload.results);
      setWarnings(payload.warnings);
    }
  });
  const download = useMutation({
    mutationFn: ({ papers, downloadPdfs }: { papers: Paper[]; downloadPdfs: boolean }) =>
      api.harvestDownload(papers, downloadPdfs, downloadProjectId),
    onSuccess: invalidateLibrary
  });
  const upload = useMutation({
    mutationFn: ({ file, title }: { file: File; title?: string }) =>
      api.uploadPdf(file, { title, project_id: downloadProjectId }),
    onSuccess: (payload) => {
      setUploaded((current) => [payload.paper, ...current.filter((p) => p.id !== payload.paper.id)]);
      invalidateLibrary();
    }
  });
  const extractRefs = useMutation({
    mutationFn: (paperId: string) => api.extractReferences({ paper_id: paperId, max_references: 40 }),
    onSuccess: async (payload) => {
      setReferences((current) => ({ ...current, [payload.paper_id]: payload.references }));
      if (autoDownload && payload.references.length) {
        await api.harvestDownload(payload.references, true, downloadProjectId);
        invalidateLibrary();
      }
    }
  });
  const discoverTopic = useMutation({
    mutationFn: () => api.discoveryFromTopic({ topic: topic.trim(), sources, provider, max_per_query: 5 }),
    onSuccess: async (payload) => {
      setTopicCandidates(payload.candidates);
      if (autoDownload && payload.candidates.length) {
        await api.harvestDownload(payload.candidates.slice(0, 10), true, downloadProjectId);
        invalidateLibrary();
      }
    }
  });
  const discoverPaper = useMutation({
    mutationFn: (paperId: string) => api.discoveryFromPaper({ paper_id: paperId, sources, provider, max_per_query: 5 }),
    onSuccess: async (payload, paperId) => {
      setPaperCandidates((current) => ({ ...current, [paperId]: payload.candidates }));
      if (autoDownload && payload.candidates.length) {
        await api.harvestDownload(payload.candidates.slice(0, 10), true, downloadProjectId);
        invalidateLibrary();
      }
    }
  });
  const research = useMutation({
    mutationFn: (question: string) => api.deepResearch({ question, provider, max_sources: 6 }),
    onSuccess: (payload) => setFindings(payload.findings)
  });
  const saveGrey = useMutation({
    mutationFn: (finding: DeepResearchFinding) =>
      api.addGreySources(
        activeProject as string,
        [
          {
            url: finding.url,
            title: finding.title,
            summary: finding.summary,
            raw_excerpt: finding.raw_excerpt,
            injection_flags: finding.injection_flags
          }
        ],
        researchQuestion
      ),
    onSuccess: (_data, finding) => setSavedFindingUrls((current) => [...current, finding.url])
  });
  const markPrimary = useMutation({
    mutationFn: (paperId: string | null) => api.setPrimaryPaper(activeProject as string, paperId),
    onSuccess: (payload) => setPrimaryPaperId(payload.primary_paper_id)
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
        <label className="check-row">
          <input type="checkbox" checked={autoDownload} onChange={(event) => setAutoDownload(event.target.checked)} />
          <span>KI-Vorschlägen vertrauen: Top-10 automatisch laden</span>
        </label>
      </div>

      <DownloadProgress
        pending={download.isPending}
        data={download.data}
        scopeLabel={isRealProject ? `Projekt: ${activeProject}` : "Alle Papers (global)"}
        onGoToExtraction={() => navigate("/extraction")}
      />

      <div className="two-column">
        <section className="panel import-panel">
          <form onSubmit={submit} className="stack">
            <label>
              Thema
              <input value={topic} onChange={(event) => setTopic(event.target.value)} placeholder="Topic oder Frage" />
            </label>
            <div className="stack source-groups">
              {sourceGroups.map((entry) => (
                <div key={entry.group} className="source-group">
                  <span className="source-group-title">{entry.group}</span>
                  <div className="checkbox-grid">
                    {entry.options.map((source) => (
                      <label key={source.id} className="check-row" title={source.note ?? undefined}>
                        <input
                          type="checkbox"
                          checked={sources.includes(source.id)}
                          onChange={() => toggleSource(source.id)}
                        />
                        <span>
                          {source.label}
                          {source.note ? <em className="source-note"> · {source.note}</em> : null}
                        </span>
                      </label>
                    ))}
                  </div>
                </div>
              ))}
            </div>
            <label>
              Anzahl Paper
              <input type="number" min={1} max={50} value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} />
            </label>
            <div className="button-row">
              <button className="button button-primary" type="submit" disabled={search.isPending || !sources.length}>
                <Search size={17} />
                <span>Suchen</span>
              </button>
              <button
                className="button"
                type="button"
                disabled={discoverTopic.isPending || !topic.trim() || !sources.length}
                onClick={() => discoverTopic.mutate()}
              >
                <Sparkles size={16} />
                <span>KI-Vorschläge</span>
              </button>
            </div>
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

      {uploaded.length ? (
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Hochgeladen</span>
              <strong>{uploaded.length} PDF(s)</strong>
            </div>
          </div>
          <div className="stack">
            {uploaded.map((paper) => (
              <article key={paper.id} className="uploaded-row">
                <div className="uploaded-row-head">
                  <strong>{paper.title || paper.id}</strong>
                  <div className="button-row">
                    <button
                      className="button"
                      type="button"
                      disabled={extractRefs.isPending}
                      onClick={() => extractRefs.mutate(paper.id)}
                    >
                      <Download size={15} />
                      <span>Quellen erkennen</span>
                    </button>
                    <button
                      className="button"
                      type="button"
                      disabled={discoverPaper.isPending}
                      onClick={() => discoverPaper.mutate(paper.id)}
                    >
                      <Sparkles size={15} />
                      <span>KI-Kontext</span>
                    </button>
                    <button
                      className={`button ${primaryPaperId === paper.id ? "button-primary" : ""}`}
                      type="button"
                      title={isRealProject ? "Als Hauptquelle priorisieren" : "Erst ein echtes Projekt wählen"}
                      disabled={!isRealProject || markPrimary.isPending}
                      onClick={() => markPrimary.mutate(primaryPaperId === paper.id ? null : paper.id)}
                    >
                      <Star size={15} />
                      <span>{primaryPaperId === paper.id ? "Hauptquelle" : "Priorisieren"}</span>
                    </button>
                  </div>
                </div>
                {references[paper.id]?.length ? (
                  <CandidateList
                    title={`${references[paper.id].length} Quellen erkannt`}
                    candidates={references[paper.id]}
                    onDownload={(papers, pdfs) => download.mutate({ papers, downloadPdfs: pdfs })}
                    disabled={download.isPending}
                  />
                ) : null}
                {paperCandidates[paper.id]?.length ? (
                  <CandidateList
                    title={`${paperCandidates[paper.id].length} KI-Kontext-Vorschläge`}
                    candidates={paperCandidates[paper.id]}
                    onDownload={(papers, pdfs) => download.mutate({ papers, downloadPdfs: pdfs })}
                    disabled={download.isPending}
                  />
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {topicCandidates.length ? (
        <section className="panel">
          <CandidateList
            title={`${topicCandidates.length} KI-Vorschläge zum Thema`}
            candidates={topicCandidates}
            onDownload={(papers, pdfs) => download.mutate({ papers, downloadPdfs: pdfs })}
            disabled={download.isPending}
          />
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Graue Quellen</span>
            <strong>Web-Recherche (Deep Research)</strong>
          </div>
          <Status value={research.isPending ? "running" : research.isSuccess ? "success" : "idle"} />
        </div>
        <p className="muted">
          Durchsucht das Web, behandelt Inhalte als nicht vertrauenswürdige Daten (Prompt-Injection-Schutz) und speichert
          bestätigte Treffer nur als projektgebundene Zusatzinfo — nicht im großen Knowledge Graph.
        </p>
        <div className="button-row">
          <input
            value={researchQuestion}
            onChange={(event) => setResearchQuestion(event.target.value)}
            placeholder="Frage für die Web-Recherche"
            style={{ flex: 1 }}
          />
          <button
            className="button button-primary"
            type="button"
            disabled={research.isPending || !researchQuestion.trim()}
            onClick={() => research.mutate(researchQuestion.trim())}
          >
            <Globe size={16} />
            <span>Recherchieren</span>
          </button>
        </div>
        {!isRealProject ? (
          <div className="warning-row">Wähle oben ein echtes Projekt, um graue Quellen zu speichern.</div>
        ) : null}
        {findings.length ? (
          <div className="stack">
            {findings.map((finding) => (
              <article key={finding.url} className="grey-source-card">
                <div className="uploaded-row-head">
                  <strong>{finding.title}</strong>
                  <button
                    className="button"
                    type="button"
                    disabled={!isRealProject || saveGrey.isPending || savedFindingUrls.includes(finding.url)}
                    onClick={() => saveGrey.mutate(finding)}
                  >
                    {savedFindingUrls.includes(finding.url) ? "Gespeichert" : "Zum Projekt"}
                  </button>
                </div>
                {finding.quarantined ? (
                  <div className="warning-row">⚠ Mögliche Prompt-Injection erkannt &amp; ignoriert: {finding.injection_flags.join(", ")}</div>
                ) : null}
                <p>{finding.summary}</p>
                <a className="muted" href={finding.url} target="_blank" rel="noreferrer">
                  {finding.url}
                </a>
              </article>
            ))}
          </div>
        ) : null}
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Treffer</span>
            <strong>{results.length} Papers</strong>
          </div>
          <div className="button-row">
            <button className="button" type="button" disabled={!results.length || download.isPending} onClick={() => download.mutate({ papers: results, downloadPdfs: false })}>
              <Download size={16} />
              <span>Metadaten</span>
            </button>
            <button className="button button-primary" type="button" disabled={!results.length || download.isPending} onClick={() => download.mutate({ papers: results, downloadPdfs: true })}>
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

function statusLabel(status: string): string {
  switch (status) {
    case "downloaded":
      return "geladen";
    case "failed":
      return "fehlgeschlagen";
    case "no_pdf":
      return "kein freier Volltext";
    case "inserted":
      return "Metadaten";
    default:
      return status;
  }
}

function DownloadProgress({
  pending,
  data,
  scopeLabel,
  onGoToExtraction
}: {
  pending: boolean;
  data?: HarvestDownloadResponse;
  scopeLabel: string;
  onGoToExtraction: () => void;
}) {
  if (!pending && !data) {
    return null;
  }
  const downloaded = data?.downloaded ?? 0;
  const failed = data?.failed_downloads?.length ?? 0;
  const total = data?.results?.length ?? 0;
  return (
    <section className="panel download-progress">
      <div className="panel-heading">
        <div>
          <span>Download</span>
          <strong>{pending ? "Lädt Paper …" : `${downloaded} von ${total} geladen`}</strong>
        </div>
        <div className="download-progress-meta">
          <span className="muted">Ziel: {scopeLabel}</span>
          {pending ? <Loader2 className="spin" size={18} /> : <Status value={failed ? "warning" : "success"} />}
        </div>
      </div>
      {pending ? (
        <div className="download-progress-bar">
          <div className="download-progress-bar-fill" />
        </div>
      ) : null}
      {!pending && data ? (
        <>
          <div className="download-progress-counts">
            <span className="pill pill-success">{downloaded} PDF(s)</span>
            {data.results.filter((r) => r.status === "no_pdf").length ? (
              <span className="pill">{data.results.filter((r) => r.status === "no_pdf").length} ohne PDF</span>
            ) : null}
            {failed ? <span className="pill pill-error">{failed} fehlgeschlagen</span> : null}
            {data.attached ? <span className="pill pill-info">dem Projekt zugeordnet</span> : null}
          </div>
          <div className="download-result-list">
            {data.results.map((result) => (
              <div className="download-result-row" key={result.paper_id} title={result.detail ?? undefined}>
                {result.status === "downloaded" ? (
                  <CheckCircle2 size={15} className="ok" />
                ) : result.status === "failed" ? (
                  <XCircle size={15} className="err" />
                ) : (
                  <Download size={15} className="muted" />
                )}
                <span className="download-result-title">{result.title}</span>
                <span className="muted download-result-status">{statusLabel(result.status)}</span>
                {result.landing_url && result.status !== "downloaded" ? (
                  <a className="download-result-link" href={result.landing_url} target="_blank" rel="noreferrer">
                    DOI öffnen
                  </a>
                ) : null}
              </div>
            ))}
          </div>
          {data.results.some((result) => result.status === "no_pdf") ? (
            <p className="muted download-progress-hint">
              „Kein freier Volltext" heißt: zu diesem Paper wurde keine frei zugängliche (Open-Access-)PDF gefunden — meist
              hinter einer Paywall. Über „DOI öffnen" kannst du es ggf. via Bibliothek/Institution abrufen und dann manuell hochladen.
            </p>
          ) : null}
          {downloaded ? (
            <div className="button-row">
              <button className="button button-primary" type="button" onClick={onGoToExtraction}>
                <span>Zur Extraktion</span>
                <ArrowRight size={16} />
              </button>
            </div>
          ) : null}
        </>
      ) : null}
    </section>
  );
}

function CandidateList({
  title,
  candidates,
  onDownload,
  disabled
}: {
  title: string;
  candidates: DiscoveryCandidate[] | ReferenceCandidate[];
  onDownload: (papers: Paper[], downloadPdfs: boolean) => void;
  disabled: boolean;
}) {
  const [selected, setSelected] = useState<Set<string>>(new Set());

  function toggle(key: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) {
        next.delete(key);
      } else {
        next.add(key);
      }
      return next;
    });
  }

  function selectAll() {
    setSelected(new Set(candidates.map((candidate) => paperKey(candidate))));
  }

  const selectedPapers = candidates.filter((candidate) => selected.has(paperKey(candidate)));

  return (
    <div className="candidate-list">
      <div className="panel-heading">
        <strong>{title}</strong>
        <div className="button-row">
          <button className="button" type="button" onClick={selectAll}>
            Alle wählen
          </button>
          <button
            className="button button-primary"
            type="button"
            disabled={disabled || !selectedPapers.length}
            onClick={() => onDownload(selectedPapers, true)}
          >
            <Download size={15} />
            <span>{selectedPapers.length} laden</span>
          </button>
        </div>
      </div>
      <div className="paper-grid">
        {candidates.map((candidate) => {
          const key = paperKey(candidate);
          const reason = (candidate as DiscoveryCandidate).discovery_reason;
          return (
            <label key={key} className={`paper-card candidate-card ${selected.has(key) ? "candidate-selected" : ""}`}>
              <div className="candidate-head">
                <input type="checkbox" checked={selected.has(key)} onChange={() => toggle(key)} />
                <strong>{candidate.title || candidate.id}</strong>
              </div>
              <span>
                {candidate.source} · {candidate.year ?? "n/a"}
                {candidate.has_full_text ? " · PDF" : ""}
              </span>
              {reason ? <p className="muted">{reason}</p> : <p>{candidate.abstract || candidate.doi || ""}</p>}
            </label>
          );
        })}
      </div>
    </div>
  );
}
