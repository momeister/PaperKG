import React, { ChangeEvent, DragEvent, FormEvent, useEffect, useMemo, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useNavigate } from "react-router-dom";
import { ArrowRight, CheckCircle2, ChevronDown, Download, FileUp, Globe, Loader2, Search, Sparkles, Star, X, XCircle } from "lucide-react";

import { api } from "../api";
import { PaperPickList } from "../components/PaperPickList";
import { QueryError } from "../components/QueryError";
import { Status } from "../components/Status";
import { FALLBACK_SOURCE_CATALOG, setSourceCatalog } from "../harvestSources";
import { useAppState } from "../state";
import { findingToGreyRecord } from "./workspaceHelpers";
import type {
  DeepResearchFinding,
  DiscoveryAnalysis,
  DiscoveryCandidate,
  HarvestDownloadResponse,
  Paper,
  ReferenceCandidate
} from "../types";

const ALL_PAPERS_SCOPES = new Set(["", "__all_papers__"]);
const SOURCES_STORAGE_KEY = "sciencekg.import.sources";

function loadStoredSources(fallback: string[]): string[] {
  try {
    const stored = localStorage.getItem(SOURCES_STORAGE_KEY);
    const parsed = stored ? (JSON.parse(stored) as unknown) : null;
    if (Array.isArray(parsed) && parsed.every((item) => typeof item === "string") && parsed.length) {
      return parsed as string[];
    }
  } catch {
    /* ignore */
  }
  return fallback;
}

type TopicGroup = {
  topic: string;
  isMain: boolean;
  findings: DeepResearchFinding[];
  collapsed: boolean;
  pending: boolean;
};

function useElapsedTimer(isPending: boolean): number {
  const [s, setS] = useState(0);
  useEffect(() => {
    if (!isPending) { setS(0); return; }
    setS(0);
    const id = setInterval(() => setS((x) => x + 1), 1000);
    return () => clearInterval(id);
  }, [isPending]);
  return s;
}

function useSessionState<T>(key: string, init: T): [T, React.Dispatch<React.SetStateAction<T>>] {
  const [val, setVal] = useState<T>(() => {
    try {
      const s = sessionStorage.getItem(key);
      return s ? (JSON.parse(s) as T) : init;
    } catch { return init; }
  });
  useEffect(() => {
    try { sessionStorage.setItem(key, JSON.stringify(val)); } catch { /* ignore */ }
  }, [key, val]);
  return [val, setVal];
}

function discoveryPhaseLabel(elapsed: number, maxN: number): string {
  if (elapsed < 5) return "Analysiere Paper-Inhalte …";
  if (elapsed < 14) return "Generiere Suchanfragen …";
  if (elapsed < 28) return `Suche in Quellen … (${elapsed}s)`;
  if (elapsed < 55) return `Verarbeite ${maxN} Vorschläge … (${elapsed}s)`;
  return `Läuft noch … ${elapsed}s`;
}

export function ImportPage({ embedded = false }: { embedded?: boolean }) {
  const { activeProject, provider } = useAppState();
  const navigate = useNavigate();
  const isRealProject = !!activeProject && !ALL_PAPERS_SCOPES.has(activeProject);
  const downloadProjectId = isRealProject ? (activeProject as string) : undefined;

  const [topic, setTopic] = useState("");
  const [sources, setSources] = useState<string[]>(() => loadStoredSources(FALLBACK_SOURCE_CATALOG.default));
  const [maxResults, setMaxResults] = useState(10);
  const [results, setResults] = useState<Paper[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);

  const [uploaded, setUploaded] = useSessionState<Paper[]>("import-uploaded", []);
  const [references, setReferences] = useSessionState<Record<string, ReferenceCandidate[]>>("import-references", {});
  const [topicCandidates, setTopicCandidates] = useSessionState<DiscoveryCandidate[]>("import-topic-candidates", []);
  // Die LLM-Analyse hinter den KI-Vorschlägen wurde bisher weggeworfen — dabei
  // stecken darin die eigentlich interessanten Vorschlagsthemen (`queries`), aus
  // denen die Kandidaten überhaupt erst entstanden sind.
  const [topicAnalysis, setTopicAnalysis] = useSessionState<DiscoveryAnalysis | null>("import-topic-analysis", null);
  const [paperCandidates, setPaperCandidates] = useSessionState<Record<string, DiscoveryCandidate[]>>("import-paper-candidates", {});
  const [topicGroups, setTopicGroups] = useSessionState<TopicGroup[]>("import-topic-groups", []);
  const [includeRelatedTopics, setIncludeRelatedTopics] = useState(false);
  const [researchQuestion, setResearchQuestion] = useState("");
  const [primaryPaperId, setPrimaryPaperId] = useState<string | null>(null);
  // Saved findings are tracked PER PROJECT — a grey source saved in one project must
  // stay saveable in every other project (a global list blocked that).
  const [savedFindingsByProject, setSavedFindingsByProject] = useSessionState<Record<string, string[]>>(
    "import-saved-findings.v2",
    {}
  );
  const savedFindingUrls = savedFindingsByProject[activeProject ?? ""] ?? [];

  const [maxPerQuery, setMaxPerQuery] = useState(5);
  const [maxResearchSources, setMaxResearchSources] = useState(12);
  const [maxRelatedTopics, setMaxRelatedTopics] = useState(5);
  const [downloadDismissed, setDownloadDismissed] = useState(false);
  const [dropZoneDragOver, setDropZoneDragOver] = useState(false);

  const queryClient = useQueryClient();

  // Quellenkatalog kommt aus dem Backend (harvester/source_registry.py), damit
  // Liste und Dispatch nicht wieder auseinanderlaufen.
  const sourcesQuery = useQuery({ queryKey: ["harvest-sources"], queryFn: api.getHarvestSources, staleTime: 60 * 60 * 1000 });
  const catalog = sourcesQuery.data ?? FALLBACK_SOURCE_CATALOG;
  useEffect(() => setSourceCatalog(sourcesQuery.data), [sourcesQuery.data]);
  useEffect(() => {
    try {
      localStorage.setItem(SOURCES_STORAGE_KEY, JSON.stringify(sources));
    } catch {
      /* ignore */
    }
  }, [sources]);
  const groupedSources = useMemo(
    () =>
      catalog.groups.map((group) => ({
        ...group,
        options: catalog.sources.filter((source) => source.group === group.id)
      })),
    [catalog]
  );
  const allSourceIds = useMemo(() => catalog.sources.map((source) => source.id), [catalog]);

  const extractRefsAbort = useRef<AbortController | null>(null);
  const discoverPaperAbort = useRef<AbortController | null>(null);
  const discoverTopicAbort = useRef<AbortController | null>(null);
  const cancelResearchRef = useRef(false);

  function invalidateLibrary() {
    queryClient.invalidateQueries({ queryKey: ["papers"] });
    queryClient.invalidateQueries({ queryKey: ["health"] });
    queryClient.invalidateQueries({ queryKey: ["workspace-pdfs"] });
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
  // Reset overlay dismiss state when a new download starts
  useEffect(() => { if (download.isPending) setDownloadDismissed(false); }, [download.isPending]);
  const upload = useMutation({
    mutationFn: ({ file, title }: { file: File; title?: string }) =>
      api.uploadPdf(file, { title, project_id: downloadProjectId }),
    onSuccess: (payload) => {
      setUploaded((current) => [payload.paper, ...current.filter((p) => p.id !== payload.paper.id)]);
      invalidateLibrary();
    }
  });
  const extractRefs = useMutation({
    mutationFn: (paperId: string) => {
      extractRefsAbort.current = new AbortController();
      return api.extractReferences({ paper_id: paperId, max_references: 40 }, extractRefsAbort.current.signal);
    },
    onSuccess: (payload) => {
      setReferences((current) => ({ ...current, [payload.paper_id]: payload.references }));
    }
  });
  const discoverTopic = useMutation({
    mutationFn: () => {
      discoverTopicAbort.current = new AbortController();
      return api.discoveryFromTopic({ topic: topic.trim(), sources, provider, max_per_query: maxPerQuery }, discoverTopicAbort.current.signal);
    },
    onSuccess: (payload) => {
      setTopicCandidates(payload.candidates);
      setTopicAnalysis(payload.analysis ?? null);
    }
  });
  const discoverPaper = useMutation({
    mutationFn: (paperId: string) => {
      discoverPaperAbort.current = new AbortController();
      return api.discoveryFromPaper({ paper_id: paperId, sources, provider, max_per_query: maxPerQuery }, discoverPaperAbort.current.signal);
    },
    onSuccess: (payload, paperId) => {
      setPaperCandidates((current) => ({ ...current, [paperId]: payload.candidates }));
    }
  });
  const research = useMutation({
    mutationFn: ({ question }: { question: string; withRelated: boolean }) => {
      cancelResearchRef.current = false;
      return api.deepResearch({ question, provider, max_sources: maxResearchSources });
    },
    onSuccess: async (payload, { question, withRelated }) => {
      const mainGroup: TopicGroup = {
        topic: question,
        isMain: true,
        findings: payload.findings,
        collapsed: false,
        pending: false,
      };
      const relTopics = (payload.related_topics ?? []).slice(0, maxRelatedTopics);
      if (withRelated && relTopics.length > 0) {
        const subGroups: TopicGroup[] = relTopics.map((t) => ({
          topic: t,
          isMain: false,
          findings: [],
          collapsed: true,
          pending: true,
        }));
        setTopicGroups([mainGroup, ...subGroups]);
        for (const subTopic of relTopics) {
          if (cancelResearchRef.current) break;
          try {
            const subPayload = await api.deepResearch({ question: subTopic, provider, max_sources: Math.max(6, maxResearchSources - 4) });
            setTopicGroups((current) =>
              current.map((g) => (g.topic === subTopic ? { ...g, findings: subPayload.findings, pending: false } : g))
            );
          } catch {
            setTopicGroups((current) =>
              current.map((g) => (g.topic === subTopic ? { ...g, pending: false } : g))
            );
          }
        }
        if (cancelResearchRef.current) {
          setTopicGroups((current) => current.map((g) => (g.pending ? { ...g, pending: false } : g)));
        }
      } else {
        setTopicGroups([mainGroup]);
      }
    }
  });
  /** Merkt gespeicherte URLs pro Projekt, damit der Knopf „Gespeichert" anzeigt. */
  function rememberSaved(urls: string[]) {
    setSavedFindingsByProject((current) => {
      const key = activeProject ?? "";
      const existing = current[key] ?? [];
      const merged = [...existing, ...urls.filter((url) => !existing.includes(url))];
      return merged.length === existing.length ? current : { ...current, [key]: merged };
    });
  }

  // `findingToGreyRecord` statt einer handgeschriebenen Kopie: die lokale Variante
  // liess den `full_text ?? raw_excerpt`-Fallback aus, wodurch Quellen ohne Volltext
  // leer im Projekt landeten.
  const saveGrey = useMutation({
    mutationFn: (findings: DeepResearchFinding[]) =>
      api.addGreySources(activeProject as string, findings.map(findingToGreyRecord), researchQuestion),
    onSuccess: (_data, findings) => rememberSaved(findings.map((finding) => finding.url))
  });

  /** Noch nicht gespeicherte Treffer — Grundlage für die „Alle speichern"-Knöpfe. */
  function unsavedFindings(groups: TopicGroup[]): DeepResearchFinding[] {
    const seen = new Set(savedFindingUrls);
    const out: DeepResearchFinding[] = [];
    for (const group of groups) {
      for (const finding of group.findings) {
        if (!seen.has(finding.url)) {
          seen.add(finding.url);
          out.push(finding);
        }
      }
    }
    return out;
  }
  const markPrimary = useMutation({
    mutationFn: (paperId: string | null) => api.setPrimaryPaper(activeProject as string, paperId),
    onSuccess: (payload) => setPrimaryPaperId(payload.primary_paper_id)
  });

  const searchElapsed = useElapsedTimer(search.isPending);
  const extractRefsElapsed = useElapsedTimer(extractRefs.isPending);
  const discoverPaperElapsed = useElapsedTimer(discoverPaper.isPending);
  const discoverTopicElapsed = useElapsedTimer(discoverTopic.isPending);
  const researchElapsed = useElapsedTimer(research.isPending);

  function submit(event: FormEvent) {
    event.preventDefault();
    if (topic.trim()) {
      search.mutate({ query: topic.trim(), sources, max_results: maxResults });
    }
  }

  function toggleSource(source: string) {
    setSources((current) => (current.includes(source) ? current.filter((item) => item !== source) : [...current, source]));
  }

  /** Ganze Fachgruppe an/aus — „nur Medizin & Biologie" ist damit ein Klick. */
  function toggleGroupSources(ids: string[], enable: boolean) {
    setSources((current) => {
      const next = new Set(current);
      ids.forEach((id) => (enable ? next.add(id) : next.delete(id)));
      return Array.from(next);
    });
  }

  function toggleGroup(topic: string) {
    setTopicGroups((current) =>
      current.map((g) => (g.topic === topic ? { ...g, collapsed: !g.collapsed } : g))
    );
  }

  function onFiles(event: ChangeEvent<HTMLInputElement>) {
    const files = Array.from(event.target.files ?? []);
    files.forEach((file) => upload.mutate({ file, title: file.name.replace(/\.pdf$/i, "") }));
  }

  function onDropZoneDragOver(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDropZoneDragOver(true);
  }

  function onDropZoneDragLeave() {
    setDropZoneDragOver(false);
  }

  function onDropZoneDrop(event: DragEvent<HTMLLabelElement>) {
    event.preventDefault();
    setDropZoneDragOver(false);
    const files = Array.from(event.dataTransfer.files).filter((f) => f.type === "application/pdf" || f.name.toLowerCase().endsWith(".pdf"));
    files.forEach((file) => upload.mutate({ file, title: file.name.replace(/\.pdf$/i, "") }));
  }

  return (
    <section className={embedded ? "research-stage-panel" : "page"}>
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
            <div className="stack source-groups">
              <div className="source-group-all">
                <span className="source-group-title">Quellen ({sources.length}/{allSourceIds.length})</span>
                <div className="button-row">
                  <button className="button" type="button" onClick={() => setSources(allSourceIds)}>
                    Alle
                  </button>
                  <button className="button" type="button" onClick={() => setSources([])}>
                    Keine
                  </button>
                </div>
              </div>
              {groupedSources.map((entry) => {
                const ids = entry.options.map((option) => option.id);
                const activeCount = ids.filter((id) => sources.includes(id)).length;
                return (
                  <div key={entry.id} className="source-group">
                    <label className="check-row source-group-head">
                      <input
                        type="checkbox"
                        checked={activeCount === ids.length && ids.length > 0}
                        ref={(node) => {
                          if (node) node.indeterminate = activeCount > 0 && activeCount < ids.length;
                        }}
                        onChange={(event) => toggleGroupSources(ids, event.target.checked)}
                      />
                      <span className="source-group-title">{entry.label}</span>
                    </label>
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
                );
              })}
            </div>
            <label>
              Anzahl Paper
              <input type="number" min={1} max={50} value={maxResults} onChange={(event) => setMaxResults(Number(event.target.value))} />
            </label>
            <div className="button-row">
              <button className="button button-primary" type="submit" disabled={search.isPending || !sources.length}>
                {search.isPending ? <Loader2 size={17} className="spin" /> : <Search size={17} />}
                <span>{search.isPending ? "Sucht …" : "Suchen"}</span>
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
              {/* Die Anzahl gehoert an den Knopf, den sie steuert — nicht in ein eigenes Panel. */}
              <label className="import-count-label" title="Wie viele Paper die KI je generierter Suchanfrage holt (gilt auch für KI-Kontext).">
                <span className="import-count-caption">je Anfrage</span>
                <input
                  className="import-count-input"
                  type="number"
                  min={1}
                  max={20}
                  aria-label="Paper je Suchanfrage"
                  value={maxPerQuery}
                  onChange={(event) => setMaxPerQuery(Number(event.target.value))}
                />
              </label>
              {discoverTopic.isPending && (
                <button
                  className="button"
                  type="button"
                  onClick={() => { discoverTopicAbort.current?.abort(); discoverTopic.reset(); }}
                >
                  <X size={15} />
                  <span>Abbrechen</span>
                </button>
              )}
            </div>
            {/* Die normale Suche hatte bisher keinerlei Rückmeldung: der Knopf war
                deaktiviert, sonst passierte sichtbar nichts — bei zwölf Quellen
                gut eine halbe Minute lang. */}
            {search.isPending && (
              <div className="import-status-row">
                <Loader2 size={14} className="spin" />
                <span className="muted">
                  Durchsucht {sources.length} {sources.length === 1 ? "Quelle" : "Quellen"} … {searchElapsed}s
                </span>
              </div>
            )}
            {discoverTopic.isPending && (
              <div className="import-status-row">
                <Loader2 size={14} className="spin" />
                <span className="muted">{discoveryPhaseLabel(discoverTopicElapsed, maxPerQuery)}</span>
              </div>
            )}
          </form>

          {/* search.isError war bisher nirgends sichtbar — eine gescheiterte Suche
              sah aus wie eine Suche ohne Treffer. */}
          {search.isError ? (
            <QueryError error={search.error} title="Suche fehlgeschlagen" onRetry={() => search.mutate({ query: topic.trim(), sources, max_results: maxResults })} />
          ) : null}
          {discoverTopic.isError ? (
            <QueryError error={discoverTopic.error} title="KI-Vorschläge fehlgeschlagen" onRetry={() => discoverTopic.mutate()} />
          ) : null}

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
          <label
            className={`drop-zone ${dropZoneDragOver ? "drop-zone--drag-over" : ""}`}
            onDragOver={onDropZoneDragOver}
            onDragLeave={onDropZoneDragLeave}
            onDrop={onDropZoneDrop}
          >
            <FileUp size={34} />
            <span>{dropZoneDragOver ? "Loslassen zum Hochladen" : "PDF hierher ziehen oder auswählen"}</span>
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
                      title="Zitate aus PDF erkennen"
                      disabled={extractRefs.isPending}
                      onClick={() => extractRefs.mutate(paper.id)}
                    >
                      <Download size={15} />
                      <span>Zitate</span>
                    </button>
                    {extractRefs.isPending && (
                      <button
                        className="button"
                        type="button"
                        onClick={() => { extractRefsAbort.current?.abort(); extractRefs.reset(); }}
                      >
                        <X size={14} />
                      </button>
                    )}
                    <button
                      className="button"
                      type="button"
                      title="KI-Kontext: ähnliche Paper vorschlagen"
                      disabled={discoverPaper.isPending}
                      onClick={() => discoverPaper.mutate(paper.id)}
                    >
                      <Sparkles size={15} />
                      <span>KI-Kontext</span>
                    </button>
                    {discoverPaper.isPending && (
                      <button
                        className="button"
                        type="button"
                        onClick={() => { discoverPaperAbort.current?.abort(); discoverPaper.reset(); }}
                      >
                        <X size={14} />
                      </button>
                    )}
                    <button
                      className={`button ${primaryPaperId === paper.id ? "button-primary" : ""}`}
                      type="button"
                      title={isRealProject ? "Als Hauptquelle priorisieren" : "Erst ein echtes Projekt wählen"}
                      disabled={!isRealProject || markPrimary.isPending}
                      onClick={() => markPrimary.mutate(primaryPaperId === paper.id ? null : paper.id)}
                    >
                      <Star size={15} />
                      <span>{primaryPaperId === paper.id ? "Hauptquelle" : "Prio"}</span>
                    </button>
                  </div>
                </div>
                {extractRefs.isPending && (
                  <div className="import-status-row">
                    <Loader2 size={14} className="spin" />
                    <span className="muted">Zitate werden erkannt … {extractRefsElapsed}s</span>
                  </div>
                )}
                {discoverPaper.isPending && (
                  <div className="import-status-row">
                    <Loader2 size={14} className="spin" />
                    <span className="muted">{discoveryPhaseLabel(discoverPaperElapsed, maxPerQuery)}</span>
                  </div>
                )}
                {!discoverPaper.isPending && !paperCandidates[paper.id]?.length && (
                  <p className="muted" style={{ fontSize: "0.78rem", margin: "0.15rem 0 0" }}>
                    KI analysiert das Paper, generiert Suchanfragen und findet ähnliche Paper in den gewählten Quellen.
                  </p>
                )}
                {references[paper.id]?.length ? (
                  <PaperPickList
                    title="Erkannte Quellen"
                    papers={references[paper.id]}
                    onDownload={(papers, pdfs) => download.mutate({ papers, downloadPdfs: pdfs })}
                    disabled={download.isPending}
                  />
                ) : null}
                {paperCandidates[paper.id]?.length ? (
                  <PaperPickList
                    title="KI-Kontext-Vorschläge"
                    papers={paperCandidates[paper.id]}
                    onDownload={(papers, pdfs) => download.mutate({ papers, downloadPdfs: pdfs })}
                    disabled={download.isPending}
                    onClear={() => setPaperCandidates((prev) => { const next = { ...prev }; delete next[paper.id]; return next; })}
                  />
                ) : null}
              </article>
            ))}
          </div>
        </section>
      ) : null}

      {/* Reihenfolge nach dem Suchen: erst die ganz normalen Treffer, dann die
          KI-Vorschläge, dann die Web-Recherche. Die Treffer standen vorher ganz
          unten, hinter zwei Panels, die man gar nicht angestoßen hatte. */}
      <section className="panel">
        <PaperPickList
          title="Treffer"
          papers={results}
          onDownload={(papers, pdfs) => download.mutate({ papers, downloadPdfs: pdfs })}
          disabled={download.isPending}
          showMetadataAction
          emptyTitle="Keine Treffer"
        />
      </section>

      {topicCandidates.length || topicAnalysis ? (
        <section className="panel">
          {/* Die Vorschlagsthemen selbst — bisher verworfen, obwohl das Backend sie
              mitliefert. Ein Klick übernimmt das Thema und startet die Suche. */}
          {topicAnalysis ? (
            <div className="discovery-analysis">
              {topicAnalysis.topic_summary ? <p className="muted">{topicAnalysis.topic_summary}</p> : null}
              {topicAnalysis.queries?.length ? (
                <>
                  <span className="discovery-analysis-label">Vorschlagsthemen — anklicken, um danach zu suchen</span>
                  <div className="discovery-topic-chips">
                    {topicAnalysis.queries.map((entry) => (
                      <button
                        key={entry.query}
                        className="topic-chip topic-chip--action"
                        type="button"
                        title={entry.reason || entry.query}
                        disabled={search.isPending || !sources.length}
                        onClick={() => {
                          setTopic(entry.query);
                          search.mutate({ query: entry.query, sources, max_results: maxResults });
                        }}
                      >
                        <Search size={12} />
                        <span>{entry.query}</span>
                      </button>
                    ))}
                  </div>
                </>
              ) : null}
              {topicAnalysis.methods?.length ? (
                <span className="muted discovery-analysis-methods">Methoden: {topicAnalysis.methods.join(", ")}</span>
              ) : null}
            </div>
          ) : null}
          {topicCandidates.length ? (
            <PaperPickList
              title="KI-Vorschläge zum Thema"
              papers={topicCandidates}
              onDownload={(papers, pdfs) => download.mutate({ papers, downloadPdfs: pdfs })}
              disabled={download.isPending}
              onClear={() => {
                setTopicCandidates([]);
                setTopicAnalysis(null);
              }}
            />
          ) : null}
        </section>
      ) : null}

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Graue Quellen</span>
            <strong>Web-Recherche (Deep Research)</strong>
          </div>
          <div className="button-row" style={{ gap: "0.5rem", alignItems: "center" }}>
            {(() => {
              const pendingCount = topicGroups.filter((g) => g.pending).length;
              const doneCount = topicGroups.filter((g) => !g.pending).length;
              const totalFindings = topicGroups.reduce((sum, g) => sum + g.findings.length, 0);
              if (research.isPending) return <Status value="running" />;
              if (pendingCount > 0) return (
                <span className="muted" style={{ fontSize: "0.8rem" }}>
                  {doneCount}/{doneCount + pendingCount} Themen · {totalFindings} Treffer
                </span>
              );
              if (topicGroups.length > 0) return (
                <span className="muted" style={{ fontSize: "0.8rem" }}>
                  {totalFindings} Treffer in {topicGroups.length} {topicGroups.length === 1 ? "Thema" : "Themen"}
                </span>
              );
              return <Status value="idle" />;
            })()}
            {/* Alle Web-Treffer auf einmal ins Projekt — die API nimmt seit jeher
                eine Liste entgegen, nur die UI schickte immer genau einen. */}
            {(() => {
              const pending = unsavedFindings(topicGroups);
              if (!isRealProject || research.isPending || topicGroups.some((g) => g.pending) || !pending.length) {
                return null;
              }
              return (
                <button
                  className="button button-primary"
                  type="button"
                  disabled={saveGrey.isPending}
                  title={`${pending.length} noch nicht gespeicherte Webquellen zum Projekt „${activeProject}" hinzufügen`}
                  onClick={() => saveGrey.mutate(pending)}
                >
                  {saveGrey.isPending ? <Loader2 size={14} className="spin" /> : <Download size={14} />}
                  <span>Alle speichern ({pending.length})</span>
                </button>
              );
            })()}
            {topicGroups.length > 0 && !research.isPending && !topicGroups.some((g) => g.pending) ? (
              <button
                className="button"
                type="button"
                title="Ergebnisse löschen"
                onClick={() => {
                  setTopicGroups([]);
                  setSavedFindingsByProject((current) => {
                    const key = activeProject ?? "";
                    if (!current[key]?.length) {
                      return current;
                    }
                    return { ...current, [key]: [] };
                  });
                }}
              >
                <X size={14} />
                <span>Leeren</span>
              </button>
            ) : null}
          </div>
        </div>
        <p className="muted">
          Durchsucht das Web, behandelt Inhalte als nicht vertrauenswürdige Daten (Prompt-Injection-Schutz) und speichert
          bestätigte Treffer nur als projektgebundene Zusatzinfo — nicht im großen Knowledge Graph.
        </p>
        <div className="button-row" style={{ flexWrap: "wrap" }}>
          <label className="check-row" style={{ marginRight: "0.3rem" }}>
            <input
              type="checkbox"
              checked={includeRelatedTopics}
              onChange={(event) => setIncludeRelatedTopics(event.target.checked)}
            />
            <span>Verwandte Themen</span>
          </label>
          {includeRelatedTopics && (
            <label className="import-count-label" title="Anzahl verwandter Themen">
              <span>Max Themen</span>
              <input
                className="import-count-input"
                type="number"
                min={1}
                max={10}
                value={maxRelatedTopics}
                onChange={(e) => setMaxRelatedTopics(Number(e.target.value))}
              />
            </label>
          )}
          <label className="import-count-label" title="Anzahl Web-Quellen pro Thema">
            <span>Quellen</span>
            <input
              className="import-count-input"
              type="number"
              min={3}
              max={30}
              value={maxResearchSources}
              onChange={(e) => setMaxResearchSources(Number(e.target.value))}
            />
          </label>
        </div>
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
            disabled={research.isPending || topicGroups.some((g) => g.pending) || !researchQuestion.trim()}
            onClick={() => research.mutate({ question: researchQuestion.trim(), withRelated: includeRelatedTopics })}
          >
            <Globe size={16} />
            <span>Recherchieren</span>
          </button>
          {(research.isPending || topicGroups.some((g) => g.pending)) && (
            <button
              className="button"
              type="button"
              onClick={() => {
                cancelResearchRef.current = true;
                research.reset();
                setTopicGroups((current) => current.map((g) => (g.pending ? { ...g, pending: false } : g)));
              }}
            >
              <X size={15} />
              <span>Abbrechen</span>
            </button>
          )}
        </div>
        {research.isPending && (
          <div className="research-progress-row">
            <Loader2 size={14} className="spin" />
            <span>
              LLM generiert Suchanfragen und fasst Quellen zusammen … {researchElapsed}s
            </span>
          </div>
        )}
        {topicGroups.some((g) => g.pending) && (
          <div className="research-progress-row">
            <Loader2 size={14} className="spin" />
            <span>
              Verwandte Themen werden durchsucht ({topicGroups.filter((g) => g.pending).length} ausstehend) …
            </span>
          </div>
        )}
        {!isRealProject ? (
          <div className="warning-row">Wähle oben ein echtes Projekt, um graue Quellen zu speichern.</div>
        ) : null}
        {topicGroups.length ? (
          <div className="topic-groups">
            {topicGroups.map((group) => (
              <div key={group.topic} className="topic-group">
                {/* Auch das Hauptthema klappt zu: es war als einziges festgenagelt,
                    obwohl gerade es die längste Trefferliste hat. */}
                <button
                  type="button"
                  className={`topic-group-header ${group.isMain ? "topic-group-header--main" : ""}`}
                  aria-expanded={!group.collapsed}
                  onClick={() => toggleGroup(group.topic)}
                >
                  <ChevronDown size={14} className={group.collapsed ? "topic-group-chevron--collapsed" : "topic-group-chevron"} />
                  <span className="topic-group-label">
                    {group.isMain ? group.topic : `Verwandtes Thema: ${group.topic}`}
                  </span>
                  {group.pending ? (
                    <span className="topic-group-count topic-group-count--pending">
                      <Loader2 size={12} className="spin" />
                      <span>Sucht …</span>
                    </span>
                  ) : (
                    <span className="topic-group-count">{group.findings.length} Treffer</span>
                  )}
                </button>
                {(() => {
                  const pending = unsavedFindings([group]);
                  if (group.collapsed || group.pending || !isRealProject || !pending.length) {
                    return null;
                  }
                  return (
                    <div className="topic-group-actions">
                      <button
                        className="button button-compact"
                        type="button"
                        disabled={saveGrey.isPending}
                        onClick={() => saveGrey.mutate(pending)}
                      >
                        <Download size={13} />
                        <span>Dieses Thema speichern ({pending.length})</span>
                      </button>
                    </div>
                  );
                })()}
                {!group.collapsed ? (
                  <div className="topic-group-findings">
                    {group.pending ? (
                      <div className="research-progress-row" style={{ padding: "0.5rem 0" }}>
                        <Loader2 size={13} className="spin" />
                        <span>Suche im Web …</span>
                      </div>
                    ) : group.findings.length ? (
                      <div className="stack">
                        {group.findings.map((finding) => (
                          <article key={finding.url} className="grey-source-card">
                            <div className="uploaded-row-head">
                              <strong>{finding.title}</strong>
                              <button
                                className="button"
                                type="button"
                                disabled={!isRealProject || saveGrey.isPending || savedFindingUrls.includes(finding.url)}
                                onClick={() => saveGrey.mutate([finding])}
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
                    ) : (
                      <div className="muted" style={{ padding: "0.5rem 0" }}>Keine Treffer</div>
                    )}
                  </div>
                ) : null}
              </div>
            ))}
          </div>
        ) : null}
      </section>

      <DownloadProgress
        pending={download.isPending}
        data={download.data}
        error={download.error}
        totalPapers={download.variables?.papers.length ?? 0}
        scopeLabel={isRealProject ? `Projekt: ${activeProject}` : "Alle Papers (global)"}
        onGoToExtraction={() => navigate("/forschung/extraktion")}
        dismissed={downloadDismissed}
        onDismiss={() => setDownloadDismissed(true)}
      />
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
  error,
  totalPapers,
  scopeLabel,
  onGoToExtraction,
  dismissed,
  onDismiss
}: {
  pending: boolean;
  data?: HarvestDownloadResponse;
  error?: unknown;
  totalPapers?: number;
  scopeLabel: string;
  onGoToExtraction: () => void;
  dismissed?: boolean;
  onDismiss?: () => void;
}) {
  if (dismissed && !pending) return null;
  if (!pending && !data && !error) return null;
  const downloaded = data?.downloaded ?? 0;
  const failed = data?.failed_downloads?.length ?? 0;
  const total = data?.results?.length ?? totalPapers ?? 0;
  const errorMessage = error instanceof Error ? error.message : error ? String(error) : null;
  return (
    <div className="download-progress-overlay">
    <section className="panel download-progress">
      <div className="panel-heading">
        <div>
          <span>Download</span>
          <strong>
            {errorMessage ? "Download fehlgeschlagen" : pending ? `Lädt ${total > 0 ? `0 von ${total} ` : ""}Paper …` : `${downloaded} von ${total} geladen`}
          </strong>
        </div>
        <div className="download-progress-meta">
          <span className="muted">Ziel: {scopeLabel}</span>
          {pending ? (
            <Loader2 className="spin" size={18} />
          ) : (
            <>
              <Status value={errorMessage ? "error" : failed ? "warning" : "success"} />
              {onDismiss && (
                <button className="download-progress-dismiss" type="button" onClick={onDismiss} title="Schließen">
                  <X size={15} />
                </button>
              )}
            </>
          )}
        </div>
      </div>
      {pending && total > 50 ? (
        <p className="muted" style={{ fontSize: "0.8rem", margin: "0.2rem 0 0" }}>
          Große Mengen ({total} Paper) können mehrere Minuten dauern. Seite offen lassen.
        </p>
      ) : null}
      {pending ? (
        <div className="download-progress-bar">
          <div className="download-progress-bar-fill" />
        </div>
      ) : null}
      {errorMessage ? (
        <div className="warning-row" style={{ marginTop: "0.4rem" }}>
          <strong>Fehler:</strong> {errorMessage}
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
    </div>
  );
}
