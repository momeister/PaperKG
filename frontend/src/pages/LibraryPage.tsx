import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import type { PointerEvent as ReactPointerEvent } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { DownloadCloud, ExternalLink, FileText, Globe, Maximize2, Minimize2, Plus, Search, Star, Trash2 } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { QueryError } from "../components/QueryError";
import { PdfPane } from "../components/PdfPane";
import { Status } from "../components/Status";
import { externalPaperUrl } from "../paperLinks";
import { useAppState } from "../state";
import type { GreySource, Paper } from "../types";

const ALL_PAPERS_SCOPES = new Set(["", "__all_papers__"]);

const PDF_DOCK_MIN_WIDTH = 380;
const PDF_DOCK_STORAGE_KEY = "sciencekg.library.pdfWidth";

function loadPdfDockWidth(): number {
  const stored = Number(localStorage.getItem(PDF_DOCK_STORAGE_KEY));
  return Number.isFinite(stored) && stored >= PDF_DOCK_MIN_WIDTH ? stored : 560;
}

export { externalPaperUrl };

export function LibraryPage() {
  const { activeProject, provider, model } = useAppState();
  const isRealProject = !!activeProject && !ALL_PAPERS_SCOPES.has(activeProject);
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState<string[]>([]);
  const [openGrey, setOpenGrey] = useState<string | null>(null);
  // Angedockte Lesespalte statt Modal: das Overlay war für längeres Lesen zu klein
  // und verdeckte die Trefferliste.
  const [pdfView, setPdfView] = useState<{ url: string | null; title: string; paperId: string } | null>(null);
  const [pdfWidth, setPdfWidth] = useState(loadPdfDockWidth);
  const [pdfFullscreen, setPdfFullscreen] = useState(false);
  const pageRef = useRef<HTMLElement | null>(null);
  const queryClient = useQueryClient();

  // Close the in-app PDF viewer with Escape (Vollbild zuerst).
  useEffect(() => {
    if (!pdfView) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key !== "Escape") return;
      if (pdfFullscreen) setPdfFullscreen(false);
      else setPdfView(null);
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [pdfView, pdfFullscreen]);

  useEffect(() => {
    localStorage.setItem(PDF_DOCK_STORAGE_KEY, String(pdfWidth));
  }, [pdfWidth]);

  // Wie in WorkspacePage: während des Drags direkt am DOM, State erst bei pointerup —
  // sonst rendert die (lange) Tabelle pro Frame neu.
  const startPdfResize = useCallback((event: ReactPointerEvent<HTMLDivElement>) => {
    event.preventDefault();
    const page = pageRef.current;
    if (!page) return;
    const maxWidth = Math.max(PDF_DOCK_MIN_WIDTH, page.getBoundingClientRect().width * 0.75);
    let next = pdfWidth;
    const onMove = (moveEvent: PointerEvent) => {
      const fromRight = page.getBoundingClientRect().right - moveEvent.clientX;
      next = Math.min(maxWidth, Math.max(PDF_DOCK_MIN_WIDTH, fromRight));
      page.style.setProperty("--library-pdf-width", `${next}px`);
    };
    const onUp = () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onUp);
      setPdfWidth(next);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onUp);
  }, [pdfWidth]);

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

  const deletePaper = useMutation({
    mutationFn: (paperId: string) => api.deletePaper(paperId),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
      queryClient.invalidateQueries({ queryKey: ["extraction-library"] });
    }
  });

  const deleteGrey = useMutation({
    mutationFn: (greyId: string) => api.deleteGreySource(greyId),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["grey-sources"] })
  });

  // "PDF holen": lädt das Open-Access-PDF nach (Endpunkt löst notfalls über die DOI
  // auf) und hängt es ans Projekt — statt die Zeile nur mit "false" abzuwerten.
  const ingestPdf = useMutation({
    mutationFn: (paperId: string) =>
      api.paperIngest({ paper_id: paperId, project_id: isRealProject ? activeProject : null, provider, model }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["papers"] });
      queryClient.invalidateQueries({ queryKey: ["projects"] });
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
    <section
      className={`page library-page ${pdfView ? "library-page--reading" : ""}`}
      ref={pageRef}
      style={{ "--library-pdf-width": `${pdfWidth}px` } as React.CSSProperties}
    >
      <div className="library-main">
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
              {/* Stern-/Löschen-Spalte: Buttons erklären sich per title;
                  "Hauptquelle" passt nicht in die schmale Spalte. */}
              <span />
              <span />
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
                  {paper.has_full_text ? (
                    <button
                      className="button button-compact button-ghost"
                      type="button"
                      title="PDF in der App öffnen"
                      onClick={() =>
                        setPdfView({
                          url: api.paperPdfUrl(paper.id, paper.title || ""),
                          title: paper.title || paper.id,
                          paperId: paper.id
                        })
                      }
                    >
                      <FileText size={14} />
                      <span>Öffnen</span>
                    </button>
                  ) : (
                    <PaperWithoutPdfActions
                      paper={paper}
                      pending={ingestPdf.isPending && ingestPdf.variables === paper.id}
                      onFetch={() => ingestPdf.mutate(paper.id)}
                      onPreview={() =>
                        setPdfView({ url: null, title: paper.title || paper.id, paperId: paper.id })
                      }
                    />
                  )}
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
                  <button
                    className="button button-compact button-danger"
                    type="button"
                    title="Paper löschen"
                    disabled={deletePaper.isPending}
                    onClick={() => {
                      if (window.confirm(`Paper "${paper.title || paper.id}" wirklich löschen?`)) {
                        deletePaper.mutate(paper.id);
                      }
                    }}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              );
            })}
          </div>
        ) : papersQuery.isError ? (
          // Frueher rutschte ein Backend-Fehler hier als "Keine Papers" durch —
          // genau die Anzeige, die einen DuckDB-Lock wie Datenverlust aussehen liess.
          <QueryError
            error={papersQuery.error}
            title="Bibliothek konnte nicht geladen werden"
            onRetry={() => papersQuery.refetch()}
          />
        ) : (
          <EmptyState title={papersQuery.isLoading ? "Lade Library" : "Keine Papers"} />
        )}
      </section>

      {isRealProject && greyQuery.isError ? (
        <section className="panel">
          <QueryError
            error={greyQuery.error}
            title="Graue Quellen konnten nicht geladen werden"
            onRetry={() => greyQuery.refetch()}
          />
        </section>
      ) : null}

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
                onDelete={() => {
                  if (window.confirm(`Quelle "${source.title || source.url}" wirklich löschen?`)) {
                    deleteGrey.mutate(source.id);
                  }
                }}
              />
            ))}
          </div>
        </section>
      ) : null}

      </div>

      {pdfView ? (
        <>
          <div
            className="split-handle library-pdf-handle"
            role="separator"
            aria-label="PDF Breite anpassen"
            onPointerDown={startPdfResize}
          />
          <aside className={`library-pdf-dock ${pdfFullscreen ? "library-pdf-dock--full" : ""}`}>
            <div className="library-pdf-dock-bar">
              <button
                className="icon-button"
                type="button"
                aria-label={pdfFullscreen ? "Vollbild verlassen" : "Vollbild"}
                title={pdfFullscreen ? "Vollbild verlassen" : "Vollbild"}
                onClick={() => setPdfFullscreen((current) => !current)}
              >
                {pdfFullscreen ? <Minimize2 size={16} /> : <Maximize2 size={16} />}
              </button>
            </div>
            <PdfPane
              url={pdfView.url}
              title={pdfView.title}
              metaPaperId={pdfView.paperId}
              onCollapse={() => {
                setPdfFullscreen(false);
                setPdfView(null);
              }}
            />
          </aside>
        </>
      ) : null}
    </section>
  );
}

/** PDF-Spalte für Papers ohne lokale Datei: die Zeile zeigte bisher nur "false",
 *  obwohl fast immer ein DOI-/Landing-Link vorhanden ist und der Volltext per
 *  `/paper/ingest` nachgeladen werden kann. */
function PaperWithoutPdfActions({
  paper,
  pending,
  onFetch,
  onPreview
}: {
  paper: Paper;
  pending: boolean;
  onFetch: () => void;
  onPreview: () => void;
}) {
  const external = externalPaperUrl(paper);
  if (!external) {
    return <Status value="false" />;
  }
  return (
    <span className="library-pdf-actions">
      <button
        className="button button-compact button-ghost"
        type="button"
        title="PDF nachladen (Open Access, ggf. über die DOI aufgelöst)"
        disabled={pending}
        onClick={onFetch}
      >
        <DownloadCloud size={14} />
        <span>{pending ? "Lade…" : "Holen"}</span>
      </button>
      <a
        className="button button-compact button-ghost"
        href={external}
        target="_blank"
        rel="noreferrer"
        title="Originalquelle im Browser öffnen"
        onClick={(event) => event.stopPropagation()}
      >
        <ExternalLink size={14} />
        <span>Link</span>
      </a>
      <button
        className="icon-button icon-button--compact"
        type="button"
        title="Abstract + Quelle in der Lesespalte anzeigen"
        aria-label="Abstract anzeigen"
        onClick={onPreview}
      >
        <FileText size={14} />
      </button>
    </span>
  );
}

function GreySourceCard({ source, open, onToggle, onDelete }: { source: GreySource; open: boolean; onToggle: () => void; onDelete: () => void }) {
  const [showFull, setShowFull] = useState(false);
  const evidence = source.evidence ?? [];
  const fullText = source.full_text || source.raw_excerpt || "";
  const charCount = fullText.length;
  return (
    <article className="grey-source-card grey-source-card--library">
      <div className="grey-source-header">
        <button type="button" className="grey-source-toggle" onClick={onToggle}>
          <span className="grey-badge">Graue Quelle</span>
          <strong>{source.title || source.url}</strong>
        </button>
        <button type="button" className="button button-compact button-danger" title="Quelle löschen" onClick={onDelete}>
          <Trash2 size={13} />
        </button>
      </div>
      {open ? (
        <div className="grey-source-detail">
          {source.injection_flags.length ? (
            <div className="warning-row">⚠ Prompt-Injection-Flags ignoriert: {source.injection_flags.join(", ")}</div>
          ) : null}
          {source.summary ? <p>{source.summary}</p> : null}
          {evidence.length ? (
            <div className="grey-evidence">
              <span className="muted">Belegstellen aus der Quelle:</span>
              {evidence.map((quote, index) => (
                <blockquote key={index} className="grey-evidence-quote">
                  {quote}
                </blockquote>
              ))}
            </div>
          ) : null}
          {source.query ? <p className="muted">Frage: {source.query}</p> : null}
          <div className="grey-source-actions">
            <a className="button button-compact button-ghost" href={source.url} target="_blank" rel="noreferrer">
              <ExternalLink size={14} />
              <span>Website öffnen</span>
            </a>
            {fullText ? (
              <button type="button" className="button button-compact button-ghost" onClick={() => setShowFull((v) => !v)}>
                <FileText size={14} />
                <span>{showFull ? "Volltext ausblenden" : `Volltext anzeigen (${charCount.toLocaleString("de-DE")} Zeichen)`}</span>
              </button>
            ) : null}
          </div>
          {showFull && fullText ? <pre className="grey-source-fulltext">{fullText}</pre> : null}
        </div>
      ) : null}
    </article>
  );
}
