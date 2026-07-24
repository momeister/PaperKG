import { useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import * as pdfjs from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.mjs?url";
import { ChevronLeft, ChevronRight, ExternalLink, Languages, Layers, MapPin, Maximize2, PanelRightClose, Plus, Search, StickyNote, Trash2, X, ZoomIn, ZoomOut } from "lucide-react";

import { api } from "../api";
import { colorVarsForPaperId } from "../citationColors";
import { useAppState } from "../state";
import type { PaperMeta, PdfAnnotation, PdfAnnotationRect, VerificationEvidence } from "../types";

import {
  bestMatchFor,
  buildHighlightQuery,
  buildSearchQuery,
  clientRectsToPageRects,
  evidenceColorIndex,
  evidenceListSignature,
  findPageMatch,
  highlightQuerySignature,
  highlightScrollTop,
  pagesFor,
  shortLabel,
  topmostHighlightTop,
  type HighlightBox,
  type HighlightQuery,
  type PageMatch,
} from "./pdfHighlight";

// Rueckwaerts-kompatible Re-Exports (Tests + Konsumenten importieren aus PdfPane).
export {
  bestMatchFor,
  clientRectsToPageRects,
  findPageMatch,
  HIGHLIGHT_SCROLL_PADDING,
  highlightQuerySignature,
  highlightScrollTop,
  normalizeClientRects,
  normalizeHighlightBoxes,
  topmostHighlightTop,
} from "./pdfHighlight";
export type { PageMatch } from "./pdfHighlight";

const PDF_ANNOTATION_COLOR = "#ffb020"; // v1: single fixed, readable amber

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

type PdfDocument = {
  numPages: number;
  getPage: (pageNumber: number) => Promise<any>;
  destroy?: () => Promise<void>;
};

type MatchIndex = Record<number, Record<number, PageMatch>>;

// Search renders as its own highlight layer (parallel to evidence highlights)
// instead of replacing them.
export const SEARCH_LAYER_INDEX = -1;

export type HighlightLayer = {
  index: number;
  colorIndex: number;
  query: HighlightQuery;
  signature: string;
};

type PdfPaneProps = {
  url?: string | null;
  title?: string;
  unavailableMessage?: string;
  metaPaperId?: string;
  evidences?: VerificationEvidence[];
  activeEvidenceIndex?: number;
  onActiveEvidenceChange?: (index: number) => void;
  onCollapse?: () => void;
};

export function PdfPane({
  url,
  title,
  unavailableMessage,
  metaPaperId,
  evidences = [],
  activeEvidenceIndex = 0,
  onActiveEvidenceChange,
  onCollapse
}: PdfPaneProps) {
  const [document, setDocument] = useState<PdfDocument | null>(null);
  const [pageCount, setPageCount] = useState<number>(0);
  const [error, setError] = useState<string>("");
  const [sourceMeta, setSourceMeta] = useState<PaperMeta | null>(null);
  const [matches, setMatches] = useState<MatchIndex>({});
  const [scannedPages, setScannedPages] = useState<Record<string, Record<number, true>>>({});
  const [currentPage, setCurrentPage] = useState(1);
  const [viewportWidth, setViewportWidth] = useState(720);
  const [zoom, setZoom] = useState(1);
  const [fitMode, setFitMode] = useState<"width" | "page">("width");
  const [searchTerm, setSearchTerm] = useState("");
  const [showAllEvidences, setShowAllEvidences] = useState(false);
  const { provider, model } = useAppState();
  const [translateLanguage, setTranslateLanguage] = useState("Deutsch");
  const [translation, setTranslation] = useState("");
  const [translateError, setTranslateError] = useState("");
  const [isTranslating, setIsTranslating] = useState(false);
  const canvasWrapRef = useRef<HTMLDivElement | null>(null);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const resizeFrameRef = useRef<number | null>(null);
  const lastJumpKeyRef = useRef<string>("");
  // PDF-Notizen: persistent kleine Notizen an einer Textstelle/Punkt (nur mit paper_id).
  const [annotations, setAnnotations] = useState<PdfAnnotation[]>([]);
  const [pointMode, setPointMode] = useState(false);
  const annotationsEnabled = Boolean(metaPaperId);

  // Fetch the paper's metadata whenever an id is known — without local PDF it feeds the
  // abstract fallback, with PDF it provides the external link to the original source.
  useEffect(() => {
    if (!metaPaperId) {
      setSourceMeta(null);
      return;
    }
    let cancelled = false;
    api
      .paperMeta(metaPaperId)
      .then((data) => {
        if (!cancelled) setSourceMeta(data);
      })
      .catch(() => {
        if (!cancelled) setSourceMeta(null);
      });
    return () => {
      cancelled = true;
    };
  }, [metaPaperId]);

  // Load persisted PDF-Notizen for this paper; reset when the paper changes.
  useEffect(() => {
    setPointMode(false);
    if (!metaPaperId) {
      setAnnotations([]);
      return;
    }
    let cancelled = false;
    api
      .pdfAnnotations.list(metaPaperId)
      .then((data) => {
        if (!cancelled) setAnnotations(data.annotations ?? []);
      })
      .catch(() => {
        if (!cancelled) setAnnotations([]);
      });
    return () => {
      cancelled = true;
    };
  }, [metaPaperId]);

  const createAnnotation = useCallback(
    async (payload: { page_number: number; kind: "highlight" | "point"; rects: PdfAnnotationRect[]; quote?: string; body: string }) => {
      if (!metaPaperId) return undefined;
      const created = await api.pdfAnnotations.create(metaPaperId, { ...payload, color: PDF_ANNOTATION_COLOR });
      setAnnotations((current) => [...current, created.annotation]);
      return created.annotation;
    },
    [metaPaperId]
  );

  const updateAnnotation = useCallback(async (id: string, patch: { body?: string }) => {
    const updated = await api.pdfAnnotations.update(id, patch);
    setAnnotations((current) => current.map((ann) => (ann.id === id ? updated.annotation : ann)));
  }, []);

  const deleteAnnotation = useCallback(async (id: string) => {
    await api.pdfAnnotations.remove(id);
    setAnnotations((current) => current.filter((ann) => ann.id !== id));
  }, []);

  const annotationsByPage = useMemo(() => {
    const map: Record<number, PdfAnnotation[]> = {};
    for (const ann of annotations) {
      (map[ann.page_number] ??= []).push(ann);
    }
    return map;
  }, [annotations]);

  useEffect(() => {
    let cancelled = false;
    setDocument(null);
    setPageCount(0);
    setMatches({});
    setCurrentPage(1);
    setZoom(1);
    setError("");
    if (!url) {
      return;
    }

    const loadingTask = pdfjs.getDocument(url);
    loadingTask.promise
      .then((doc) => {
        if (cancelled) {
          void doc.destroy();
          return;
        }
        setDocument(doc as PdfDocument);
        setPageCount(doc.numPages);
      })
      .catch((err) => {
        if (!cancelled) {
          setError(err instanceof Error ? err.message : "PDF konnte nicht geladen werden.");
        }
      });

    return () => {
      cancelled = true;
      void loadingTask.destroy();
    };
  }, [url]);

  const evidenceSignature = evidenceListSignature(evidences);
  const evidenceQueries = useMemo(() => evidences.map(buildHighlightQuery), [evidenceSignature]);
  const searchQuery = useMemo(() => buildSearchQuery(searchTerm), [searchTerm]);
  const showingSearch = Boolean(searchTerm.trim());
  const activeEvidence = evidences[activeEvidenceIndex];
  const activeEvidenceColorIndex = evidenceColorIndex(activeEvidence, activeEvidenceIndex);
  const activeQuery = evidenceQueries[activeEvidenceIndex] ?? { phrases: [], terms: [] };
  const activeQuerySignature = highlightQuerySignature(activeQuery);
  // Highlight layers render in parallel: the active citation, optionally every other
  // citation ("Alle Zitate"), and the search — search no longer replaces the citation
  // highlight.
  const layers = useMemo<HighlightLayer[]>(() => {
    const list: HighlightLayer[] = [];
    evidenceQueries.forEach((query, index) => {
      if ((index === activeEvidenceIndex || showAllEvidences) && (query.phrases.length || query.terms.length)) {
        list.push({
          index,
          colorIndex: evidenceColorIndex(evidences[index], index),
          query,
          signature: highlightQuerySignature(query)
        });
      }
    });
    if (showingSearch) {
      list.push({
        index: SEARCH_LAYER_INDEX,
        colorIndex: SEARCH_LAYER_INDEX,
        query: searchQuery,
        signature: highlightQuerySignature(searchQuery)
      });
    }
    return list;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [evidenceQueries, evidenceSignature, activeEvidenceIndex, showAllEvidences, showingSearch, searchQuery]);
  const layersSignature = layers.map((layer) => `${layer.index}#${layer.signature}`).join("");
  // Scrolling follows the search while typing, otherwise the active citation.
  const scrollLayerIndex = showingSearch ? SEARCH_LAYER_INDEX : activeEvidenceIndex;
  const scrollSignature = showingSearch ? highlightQuerySignature(searchQuery) : activeQuerySignature;
  const activeMatch = bestMatchFor(matches[scrollLayerIndex], scrollSignature);
  const evidenceMatch = bestMatchFor(matches[activeEvidenceIndex], activeQuerySignature);
  const searchMatchPages = showingSearch ? pagesFor(matches[SEARCH_LAYER_INDEX], highlightQuerySignature(searchQuery)) : [];
  const evidencePages = pagesFor(matches[activeEvidenceIndex], activeQuerySignature);
  const targetPages = useMemo(() => {
    const result: Record<number, number | null> = {};
    for (const layer of layers) {
      result[layer.index] = bestMatchFor(matches[layer.index], layer.signature)?.pageNumber ?? null;
    }
    return result;
  }, [layers, matches]);
  const targetPagesSignature = layers.map((layer) => `${layer.index}:${targetPages[layer.index] ?? ""}`).join(",");
  const activeEvidenceText = evidenceMatch?.matchedText ?? "";
  // The panel shows the backend's faithful pdf_excerpt; the pdf.js-reconstructed
  // matchedText only drives highlight placement. When the location is uncertain,
  // say so instead of presenting the excerpt as exact.
  const activeLocated = activeEvidence?.metadata?.["located"];
  const excerptApproxHint =
    activeEvidence?.found_in_pdf_text === false
      ? "Textstelle nicht wörtlich im PDF verifiziert."
      : activeLocated === "approx_region" || activeLocated === "term_overlap_only"
        ? "Ungefähre Stelle — Zitat nicht satzgenau lokalisiert."
        : evidenceMatch && !evidenceMatch.exact
          ? "Markierung im PDF ist ungefähr."
          : "";
  // Pages report in asynchronously; until every page was scanned for the current query
  // the "best" match keeps changing — gate scrolling and "not found" messages on this.
  const activeScanKey = `${scrollLayerIndex}|${scrollSignature}`;
  const scanComplete = pageCount > 0 && Object.keys(scannedPages[activeScanKey] ?? {}).length >= pageCount;

  useEffect(() => {
    setMatches({});
    setScannedPages({});
  }, [evidenceSignature, searchTerm, url, showAllEvidences]);

  useEffect(() => {
    setTranslation("");
    setTranslateError("");
  }, [activeEvidenceIndex, evidenceSignature, url]);

  async function translateActiveExcerpt() {
    const text = (activeEvidence?.pdf_excerpt || activeEvidenceText || activeEvidence?.reference_text || "").trim();
    if (!text || isTranslating) {
      return;
    }
    setIsTranslating(true);
    setTranslateError("");
    try {
      const result = await api.rewriteNote({
        text,
        instruction: `Übersetze den folgenden Text nach ${translateLanguage}. Gib ausschließlich die Übersetzung aus, ohne Kommentar.`,
        provider,
        model
      });
      setTranslation(result.text);
    } catch (error) {
      setTranslateError(error instanceof Error ? error.message : "Übersetzung fehlgeschlagen");
    } finally {
      setIsTranslating(false);
    }
  }

  useEffect(() => {
    const node = canvasWrapRef.current;
    if (!node) {
      return;
    }
    const updateWidth = () => setViewportWidth(Math.max(320, node.clientWidth));
    updateWidth();
    if (typeof ResizeObserver === "undefined") {
      return;
    }
    const observer = new ResizeObserver(() => {
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
      }
      resizeFrameRef.current = window.requestAnimationFrame(() => {
        resizeFrameRef.current = null;
        updateWidth();
      });
    });
    observer.observe(node);
    return () => {
      observer.disconnect();
      if (resizeFrameRef.current !== null) {
        window.cancelAnimationFrame(resizeFrameRef.current);
      }
    };
  }, [document, url]);

  useEffect(() => {
    const targetPage = activeMatch?.pageNumber;
    if (!targetPage) {
      return;
    }
    // Jump once per query: either as soon as a confident (exact) match appears, or after
    // every page reported in. Jumping on every interim "best" match made the view hop
    // between pages while the document was still being scanned.
    if (!scanComplete && !activeMatch?.exact) {
      return;
    }
    const jumpKey = `${url ?? ""}|${activeScanKey}|${targetPage}`;
    if (lastJumpKeyRef.current === jumpKey) {
      return;
    }
    lastJumpKeyRef.current = jumpKey;
    jumpToPage(targetPage, "center", topmostHighlightTop(activeMatch?.boxes));
  }, [activeEvidenceIndex, activeMatch?.pageNumber, activeMatch?.exact, scanComplete, activeScanKey, showingSearch, url]);

  const updateMatch = useCallback((evidenceIndex: number, pageNumber: number, querySignature: string, match: Omit<PageMatch, "pageNumber" | "querySignature"> | null) => {
    setScannedPages((current) => {
      const key = `${evidenceIndex}|${querySignature}`;
      if (current[key]?.[pageNumber]) {
        return current;
      }
      return { ...current, [key]: { ...(current[key] ?? {}), [pageNumber]: true as const } };
    });
    setMatches((current) => {
      const existing = { ...(current[evidenceIndex] ?? {}) };
      const previous = existing[pageNumber];
      if (match) {
        existing[pageNumber] = { ...match, pageNumber, querySignature };
      } else if (!previous || previous.querySignature === querySignature) {
        delete existing[pageNumber];
      }
      const next = { ...current, [evidenceIndex]: existing };
      if (!Object.keys(existing).length) {
        delete next[evidenceIndex];
      }
      return next;
    });
  }, []);

  function jumpToEvidence(index: number) {
    onActiveEvidenceChange?.(index);
    const match = bestMatchFor(matches[index], highlightQuerySignature(evidenceQueries[index] ?? { phrases: [], terms: [] }));
    if (match) {
      jumpToPage(match.pageNumber, "center", topmostHighlightTop(match.boxes));
    }
  }

  function stepEvidence(direction: -1 | 1) {
    if (!evidences.length) {
      return;
    }
    const next = (activeEvidenceIndex + direction + evidences.length) % evidences.length;
    jumpToEvidence(next);
  }

  function jumpToPage(pageNumber: number, block: ScrollLogicalPosition = "start", highlightTop: number | null = null) {
    if (!pageCount) {
      return;
    }
    const page = Math.min(pageCount, Math.max(1, pageNumber));
    setCurrentPage(page);
    const root = canvasWrapRef.current;
    const pageNode = pageRefs.current[page];
    if (!root || !pageNode) {
      return;
    }
    const rootRect = root.getBoundingClientRect();
    const pageRect = pageNode.getBoundingClientRect();
    const relativeTop = pageRect.top - rootRect.top + root.scrollTop;
    if (highlightTop != null) {
      // `HighlightBox.top` is relative to `.pdf-page-surface` (inside `.pdf-page`,
      // below the "Seite N" label) — account for that offset so the computed scroll
      // target lines up with where the highlight is actually rendered.
      const surface = pageNode.querySelector<HTMLElement>(".pdf-page-surface");
      const surfaceOffset = surface ? surface.getBoundingClientRect().top - pageRect.top : 0;
      root.scrollTo({ top: highlightScrollTop(relativeTop + surfaceOffset, highlightTop), behavior: "smooth" });
      return;
    }
    const centeredTop = relativeTop - Math.max(0, (root.clientHeight - pageNode.clientHeight) / 2);
    root.scrollTo({
      top: Math.max(0, block === "center" ? centeredTop : relativeTop - 12),
      behavior: "smooth"
    });
  }

  const updateCurrentPageFromScroll = useCallback(() => {
    const root = canvasWrapRef.current;
    if (!root) {
      return;
    }
    const viewportCenter = root.scrollTop + root.clientHeight / 2;
    let nearestPage = currentPage;
    let nearestDistance = Number.POSITIVE_INFINITY;
    Object.entries(pageRefs.current).forEach(([page, node]) => {
      if (!node) {
        return;
      }
      const pageCenter = node.offsetTop + node.clientHeight / 2;
      const distance = Math.abs(pageCenter - viewportCenter);
      if (distance < nearestDistance) {
        nearestDistance = distance;
        nearestPage = Number(page);
      }
    });
    if (nearestPage !== currentPage) {
      setCurrentPage(nearestPage);
    }
  }, [currentPage]);

  return (
    <aside className="pdf-pane">
      <div className="pane-heading">
        <div>
          <span>PDF</span>
          <strong>{title || "Keine Quelle ausgewählt"}</strong>
        </div>
        <div className="button-row">
          {pageCount ? <small>{pageCount} Seiten</small> : null}
          {onCollapse ? (
            <button className="icon-button" type="button" aria-label="PDF einklappen" onClick={onCollapse}>
              <PanelRightClose size={17} />
            </button>
          ) : null}
        </div>
      </div>

      {evidences.length ? (
        <div className="pdf-evidence-nav" style={colorVarsForPaperId(activeEvidence?.paper_id, activeEvidenceColorIndex)}>
          <button className="icon-button" type="button" aria-label="Vorherige Zitation" onClick={() => stepEvidence(-1)}>
            <ChevronLeft size={18} />
          </button>
          <select value={activeEvidenceIndex} onChange={(event) => jumpToEvidence(Number(event.target.value))}>
            {evidences.map((evidence, index) => (
              <option key={`${evidence.reference_text}-${index}`} value={index}>
                {index + 1}. {shortLabel(evidence.reference_text || evidence.kind)}
              </option>
            ))}
          </select>
          <button className="icon-button" type="button" aria-label="Nächste Zitation" onClick={() => stepEvidence(1)}>
            <ChevronRight size={18} />
          </button>
          <button
            className={`icon-button ${showAllEvidences ? "icon-button--active" : ""}`}
            type="button"
            aria-pressed={showAllEvidences}
            aria-label={showAllEvidences ? "Nur aktives Zitat markieren" : "Alle Zitate parallel markieren"}
            title={showAllEvidences ? "Nur aktives Zitat markieren" : "Alle Zitate parallel markieren"}
            onClick={() => setShowAllEvidences((current) => !current)}
            disabled={evidences.length < 2}
          >
            <Layers size={16} />
          </button>
          <span>
            {evidenceMatch
              ? `Seite ${evidenceMatch.pageNumber}`
              : evidencePages.length
                ? `Seite ${evidencePages[0]}`
                : url && document && !scanComplete
                  ? "suche Textstelle…"
                  : "keine Textstelle gefunden"}
            {showAllEvidences && evidences.length > 1 ? " · alle Zitate markiert" : ""}
          </span>
        </div>
      ) : null}

      {pageCount ? (
        <div className="pdf-control-stack">
          <div className="pdf-page-nav">
            <button className="icon-button" type="button" aria-label="Vorherige Seite" onClick={() => jumpToPage(currentPage - 1)}>
              <ChevronLeft size={18} />
            </button>
            <select aria-label="Seite" value={currentPage} onChange={(event) => jumpToPage(Number(event.target.value))}>
              {Array.from({ length: pageCount }, (_, index) => (
                <option key={index + 1} value={index + 1}>
                  Seite {index + 1}
                </option>
              ))}
            </select>
            <button className="icon-button" type="button" aria-label="Naechste Seite" onClick={() => jumpToPage(currentPage + 1)}>
              <ChevronRight size={18} />
            </button>
          </div>
          <div className="pdf-search-row">
            <Search size={17} />
            <input value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} placeholder="In PDF suchen" />
            <button className={`icon-button ${searchTerm ? "" : "pdf-search-clear--hidden"}`} type="button" aria-label="Suche leeren" onClick={() => setSearchTerm("")} disabled={!searchTerm}>
              <X size={17} />
            </button>
            <span>{showingSearch ? (searchMatchPages.length ? `Treffer auf Seite ${searchMatchPages.join(", ")}` : "keine Treffer") : ""}</span>
          </div>
          <div className="pdf-zoom-nav">
            <button className="icon-button" type="button" aria-label="Verkleinern" onClick={() => setZoom((current) => Math.max(0.65, current - 0.1))}>
              <ZoomOut size={18} />
            </button>
            <button className={`button ${fitMode === "width" ? "button-primary" : ""}`} type="button" onClick={() => setFitMode("width")}>
              Breite
            </button>
            <button className={`button ${fitMode === "page" ? "button-primary" : ""}`} type="button" onClick={() => setFitMode("page")}>
              Seite
            </button>
            <button className="icon-button" type="button" aria-label="Vergroessern" onClick={() => setZoom((current) => Math.min(2.2, current + 0.1))}>
              <ZoomIn size={18} />
            </button>
            <button className="icon-button" type="button" aria-label="Zoom zuruecksetzen" onClick={() => setZoom(1)}>
              <Maximize2 size={17} />
            </button>
          </div>
          {annotationsEnabled ? (
            <div className="pdf-annotate-row">
              <button
                className={`button button-compact ${pointMode ? "button-primary" : "button-ghost"}`}
                type="button"
                aria-pressed={pointMode}
                title="Punkt-Notiz setzen: danach in die Seite klicken"
                onClick={() => setPointMode((current) => !current)}
              >
                <MapPin size={14} /> {pointMode ? "Punkt setzen: klicke in die Seite" : "Punkt-Notiz"}
              </button>
              <small>Text markieren → „Notiz hinzufügen"</small>
            </div>
          ) : null}
        </div>
      ) : null}

      {url && document ? (
        <div className="pdf-canvas-shell">
          <div className="pdf-canvas-wrap" ref={canvasWrapRef} onScroll={updateCurrentPageFromScroll}>
            {Array.from({ length: pageCount }, (_, index) => {
              const pageNumber = index + 1;
              return (
                <PdfPage
                  key={`${url}-${pageNumber}`}
                  document={document}
                  pageNumber={pageNumber}
                  containerWidth={viewportWidth}
                  zoom={zoom}
                  fitMode={fitMode}
                  layers={layers}
                  layersSignature={layersSignature}
                  activeEvidenceIndex={activeEvidenceIndex}
                  evidences={evidences}
                  targetPages={targetPages}
                  targetPagesSignature={targetPagesSignature}
                  onMatch={updateMatch}
                  annotationsEnabled={annotationsEnabled}
                  pointMode={pointMode}
                  annotations={annotationsByPage[pageNumber] ?? EMPTY_ANNOTATIONS}
                  onCreateAnnotation={createAnnotation}
                  onUpdateAnnotation={updateAnnotation}
                  onDeleteAnnotation={deleteAnnotation}
                  setPageRef={(node) => {
                    pageRefs.current[pageNumber] = node;
                  }}
                />
              );
            })}
          </div>
          {sourceMeta?.external_url ? (
            <div className="pdf-side-controls">
              <a
                className="icon-button"
                href={sourceMeta.external_url}
                target="_blank"
                rel="noreferrer"
                title="Original-Quelle öffnen (arXiv/DOI)"
                aria-label="Original-Quelle öffnen"
              >
                <ExternalLink size={16} />
              </a>
            </div>
          ) : null}
        </div>
      ) : url ? (
        <div className="pdf-placeholder">PDF wird geladen</div>
      ) : (
        <div className="pdf-placeholder">
          {title || sourceMeta ? (
            <>
              <strong>Kein PDF verfügbar</strong>
              <span>{sourceMeta?.title || title}</span>
              <p>{unavailableMessage || "Diese Quelle wurde zitiert, ist aber noch nicht als PDF heruntergeladen oder nicht im Projekt vorhanden."}</p>
              {sourceMeta?.abstract ? (
                <div className="pdf-placeholder-abstract">
                  <span>Abstract</span>
                  <p>{sourceMeta.abstract}</p>
                </div>
              ) : null}
              {sourceMeta?.external_url ? (
                <a className="pdf-placeholder-link" href={sourceMeta.external_url} target="_blank" rel="noreferrer">
                  Quelle öffnen ↗
                </a>
              ) : null}
            </>
          ) : "Quelle wählen"}
        </div>
      )}

      {error ? <div className="inline-error">{error}</div> : null}
      {activeEvidence ? (
        <div className="excerpt-panel" style={colorVarsForPaperId(activeEvidence?.paper_id, activeEvidenceColorIndex)}>
          <div className="excerpt-panel-topline">
            <span>Aktive Textstelle</span>
            <span className="excerpt-translate-controls">
              <Languages size={13} />
              <select
                aria-label="Zielsprache für Übersetzung"
                value={translateLanguage}
                onChange={(event) => setTranslateLanguage(event.target.value)}
              >
                {EXCERPT_TRANSLATE_LANGUAGES.map((language) => (
                  <option key={language} value={language}>
                    {language}
                  </option>
                ))}
              </select>
              <button
                className="button button-compact button-ghost"
                type="button"
                disabled={isTranslating || !(activeEvidence?.pdf_excerpt || activeEvidenceText || activeEvidence?.reference_text)}
                onClick={() => void translateActiveExcerpt()}
              >
                {isTranslating ? "Übersetzt…" : "Übersetzen"}
              </button>
            </span>
          </div>
          <p>
            {activeEvidence?.pdf_excerpt ||
              (url && document && !scanComplete ? "Suche Textstelle…" : "") ||
              activeEvidenceText ||
              "Keine Textstelle gefunden."}
          </p>
          {excerptApproxHint ? <small className="excerpt-approx-hint">{excerptApproxHint}</small> : null}
          {translateError ? <div className="inline-error">{translateError}</div> : null}
          {translation ? (
            <div className="excerpt-translation">
              <span>Übersetzung ({translateLanguage})</span>
              <p>{translation}</p>
            </div>
          ) : null}
        </div>
      ) : null}
    </aside>
  );
}

const EXCERPT_TRANSLATE_LANGUAGES = ["Deutsch", "Englisch", "Französisch", "Spanisch", "Italienisch", "Portugiesisch", "Niederländisch", "Polnisch", "Chinesisch", "Japanisch"];

// Stable empty reference so pages without annotations don't re-render on every parent update.
const EMPTY_ANNOTATIONS: PdfAnnotation[] = [];

function PdfPage({
  document,
  pageNumber,
  containerWidth,
  zoom,
  fitMode,
  layers,
  layersSignature,
  activeEvidenceIndex,
  evidences,
  targetPages,
  targetPagesSignature,
  onMatch,
  annotationsEnabled,
  pointMode,
  annotations,
  onCreateAnnotation,
  onUpdateAnnotation,
  onDeleteAnnotation,
  setPageRef
}: {
  document: PdfDocument;
  pageNumber: number;
  containerWidth: number;
  zoom: number;
  fitMode: "width" | "page";
  layers: HighlightLayer[];
  layersSignature: string;
  activeEvidenceIndex: number;
  evidences: VerificationEvidence[];
  targetPages: Record<number, number | null>;
  targetPagesSignature: string;
  onMatch: (evidenceIndex: number, pageNumber: number, querySignature: string, match: Omit<PageMatch, "pageNumber" | "querySignature"> | null) => void;
  annotationsEnabled: boolean;
  pointMode: boolean;
  annotations: PdfAnnotation[];
  onCreateAnnotation: (payload: { page_number: number; kind: "highlight" | "point"; rects: PdfAnnotationRect[]; quote?: string; body: string }) => Promise<PdfAnnotation | undefined>;
  onUpdateAnnotation: (id: string, patch: { body?: string }) => Promise<void>;
  onDeleteAnnotation: (id: string) => Promise<void>;
  setPageRef: (node: HTMLDivElement | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const pageRef = useRef<HTMLDivElement | null>(null);
  const layersRef = useRef(layers);
  const targetPagesRef = useRef(targetPages);
  layersRef.current = layers;
  targetPagesRef.current = targetPages;
  const [isNearViewport, setIsNearViewport] = useState(pageNumber <= 2);
  const [boxes, setBoxes] = useState<HighlightBox[]>([]);
  const [size, setSize] = useState({ width: 0, height: 0 });
  const combinedPageRef = useCallback(
    (node: HTMLDivElement | null) => {
      pageRef.current = node;
      setPageRef(node);
    },
    [setPageRef]
  );

  useEffect(() => {
    const node = pageRef.current;
    if (!node || typeof IntersectionObserver === "undefined") {
      setIsNearViewport(true);
      return;
    }
    const observer = new IntersectionObserver(
      ([entry]) => {
        setIsNearViewport(entry.isIntersecting);
      },
      { root: null, rootMargin: "900px 0px" }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    let cancelled = false;
    let renderTask: { promise: Promise<unknown>; cancel?: () => void } | null = null;

    async function renderPage() {
      const canvas = canvasRef.current;
      if (!canvas) {
        return;
      }
      const page = await document.getPage(pageNumber);
      if (cancelled) {
        return;
      }
      const baseViewport = page.getViewport({ scale: 1 });
      const baseScale = fitMode === "page" ? Math.min((containerWidth - 42) / baseViewport.width, 0.95) : (containerWidth - 42) / baseViewport.width;
      const scale = Math.min(2.3, Math.max(0.7, baseScale * zoom));
      const viewport = page.getViewport({ scale });
      const context = canvas.getContext("2d");
      if (!context) {
        return;
      }

      // Ganzzahlige Layout-Maße: fraktionale pdf.js-Viewport-Breiten erzeugten sonst
      // Sub-Pixel-Überläufe (mit-)verantwortlich für die Phantom-Scrollbar.
      const pxWidth = Math.round(viewport.width);
      const pxHeight = Math.round(viewport.height);
      canvas.width = pxWidth;
      canvas.height = pxHeight;
      canvas.style.width = `${pxWidth}px`;
      canvas.style.height = `${pxHeight}px`;
      setSize({ width: pxWidth, height: pxHeight });
      if (cancelled) {
        return;
      }
      if (isNearViewport) {
        const task = page.render({ canvasContext: context, viewport });
        renderTask = task;
        try {
          await task.promise;
        } catch (error) {
          if (cancelled || String(error).toLowerCase().includes("cancel")) {
            return;
          }
          throw error;
        }
      } else {
        context.clearRect(0, 0, canvas.width, canvas.height);
      }

      const textContent = await page.getTextContent();
      if (cancelled) {
        return;
      }

      // Every highlight layer (active citation, parallel citations, search) scans the
      // page independently; their boxes render side by side.
      const collected: HighlightBox[] = [];
      for (const layer of layersRef.current) {
        const match = findPageMatch(textContent.items, layer.query, viewport, layer.index, layer.colorIndex);
        onMatch(layer.index, pageNumber, layer.signature, match);
        // Confident (exact) matches render on every page they appear on — an excerpt that
        // crosses a page boundary stays fully marked; weak term-window matches render only
        // on the single best page to avoid scattering noise across the document.
        if (match && (targetPagesRef.current[layer.index] === pageNumber || match.exact)) {
          collected.push(...match.boxes);
        }
      }
      setBoxes(collected);

      // Selectable text layer: pdf.js positions transparent spans over the canvas so
      // normal text selection/copy works in the PDF view.
      const textLayerNode = textLayerRef.current;
      if (textLayerNode) {
        textLayerNode.replaceChildren();
        if (isNearViewport) {
          textLayerNode.style.setProperty("--scale-factor", String(viewport.scale));
          textLayerNode.style.setProperty("--total-scale-factor", String(viewport.scale));
          try {
            const TextLayerCtor = (pdfjs as unknown as { TextLayer?: new (options: Record<string, unknown>) => { render: () => Promise<void> } }).TextLayer;
            if (TextLayerCtor) {
              const textLayer = new TextLayerCtor({
                textContentSource: textContent,
                container: textLayerNode,
                viewport
              });
              await textLayer.render();
            }
          } catch {
            // Selection layer is an enhancement — rendering continues without it.
          }
        }
      }
    }

    renderPage();
    return () => {
      cancelled = true;
      renderTask?.cancel?.();
    };
  }, [document, pageNumber, containerWidth, zoom, fitMode, layersSignature, targetPagesSignature, onMatch, isNearViewport]);

  return (
    <div className="pdf-page" ref={combinedPageRef} style={{ width: size.width || undefined }}>
      <div className="pdf-page-label">Seite {pageNumber}</div>
      <div className="pdf-page-surface" ref={surfaceRef} style={{ width: size.width || undefined, height: size.height || undefined }}>
        <canvas ref={canvasRef} />
        <div className="pdf-highlight-layer">
          {/* Boxes are pre-normalized per layer; normalizing across layers would merge
              differently colored highlights into one. */}
          {boxes.map((box) => (
            <span
              key={box.id}
              className={`pdf-highlight ${box.evidenceIndex === activeEvidenceIndex ? "pdf-highlight--active" : ""} ${box.evidenceIndex === SEARCH_LAYER_INDEX ? "pdf-highlight--search" : ""}`}
              style={{
                left: box.left,
                top: box.top,
                width: box.width,
                height: box.height,
                ...(box.evidenceIndex === SEARCH_LAYER_INDEX
                  ? {}
                  : colorVarsForPaperId(evidences[box.evidenceIndex]?.paper_id, box.colorIndex))
              }}
              aria-hidden="true"
            />
          ))}
        </div>
        <div className="pdf-text-layer" ref={textLayerRef} />
        {annotationsEnabled ? (
          <PdfAnnotations
            surfaceRef={surfaceRef}
            size={size}
            pageNumber={pageNumber}
            pointMode={pointMode}
            annotations={annotations}
            onCreate={onCreateAnnotation}
            onUpdate={onUpdateAnnotation}
            onDelete={onDeleteAnnotation}
          />
        ) : null}
      </div>
    </div>
  );
}

// --- PDF-Notizen (Highlight/Punkt an fester Stelle, persistent pro Paper) ---

type PendingSelection = { rects: PdfAnnotationRect[]; quote: string; left: number; top: number };
type AnnotationDraft = { kind: "highlight" | "point"; rects: PdfAnnotationRect[]; quote: string; left: number; top: number };

function PdfAnnotations({
  surfaceRef,
  size,
  pageNumber,
  pointMode,
  annotations,
  onCreate,
  onUpdate,
  onDelete
}: {
  surfaceRef: RefObject<HTMLDivElement | null>;
  size: { width: number; height: number };
  pageNumber: number;
  pointMode: boolean;
  annotations: PdfAnnotation[];
  onCreate: (payload: { page_number: number; kind: "highlight" | "point"; rects: PdfAnnotationRect[]; quote?: string; body: string }) => Promise<PdfAnnotation | undefined>;
  onUpdate: (id: string, patch: { body?: string }) => Promise<void>;
  onDelete: (id: string) => Promise<void>;
}) {
  const [pending, setPending] = useState<PendingSelection | null>(null);
  const [draft, setDraft] = useState<AnnotationDraft | null>(null);
  const [draftBody, setDraftBody] = useState("");
  const [saving, setSaving] = useState(false);
  const [openId, setOpenId] = useState<string | null>(null);
  const [openBody, setOpenBody] = useState("");
  // Rechtsklick-Kontextmenü zum Anlegen einer Punkt-Notiz an genau dieser Stelle.
  // x/y sind normiert (0..1), left/top Pixel relativ zur Surface (für die Menü-Position).
  const [menu, setMenu] = useState<{ x: number; y: number; left: number; top: number } | null>(null);

  // Everything below autosaves (debounced) instead of using explicit Speichern/Abbrechen
  // buttons. These refs track in-flight timers/ids synchronously (not via state) because a
  // stale closure here would mean typing into a fresh draft note creates duplicate annotations.
  const draftIdRef = useRef<string | null>(null);
  const draftCreatingRef = useRef(false);
  const draftSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const openSaveTimer = useRef<ReturnType<typeof setTimeout> | null>(null);

  const persistDraft = useCallback(
    async (body: string) => {
      const trimmed = body.trim();
      if (draftIdRef.current) {
        await onUpdate(draftIdRef.current, { body: trimmed });
        return;
      }
      if (!trimmed || draftCreatingRef.current || !draft) return;
      draftCreatingRef.current = true;
      try {
        const created = await onCreate({ page_number: pageNumber, kind: draft.kind, rects: draft.rects, quote: draft.quote || undefined, body: trimmed });
        if (created) draftIdRef.current = created.id;
      } finally {
        draftCreatingRef.current = false;
      }
    },
    [draft, onCreate, onUpdate, pageNumber]
  );

  const scheduleDraftSave = (body: string) => {
    if (draftSaveTimer.current) clearTimeout(draftSaveTimer.current);
    draftSaveTimer.current = setTimeout(() => {
      draftSaveTimer.current = null;
      void persistDraft(body);
    }, 500);
  };

  const closeDraft = () => {
    if (draftSaveTimer.current) {
      clearTimeout(draftSaveTimer.current);
      draftSaveTimer.current = null;
      void persistDraft(draftBody);
    }
    setDraft(null);
    setDraftBody("");
    draftIdRef.current = null;
  };

  const scheduleOpenSave = (id: string, body: string) => {
    if (openSaveTimer.current) clearTimeout(openSaveTimer.current);
    openSaveTimer.current = setTimeout(() => {
      openSaveTimer.current = null;
      void onUpdate(id, { body: body.trim() });
    }, 500);
  };

  const closeOpen = () => {
    if (openSaveTimer.current) {
      clearTimeout(openSaveTimer.current);
      openSaveTimer.current = null;
      if (openId) void onUpdate(openId, { body: openBody.trim() });
    }
    setOpenId(null);
  };

  const toggleOpen = (ann: PdfAnnotation) => {
    const wasOpen = openId === ann.id;
    closeOpen();
    if (!wasOpen) {
      setOpenId(ann.id);
      setOpenBody(ann.body || "");
    }
  };

  // Leaving/entering point mode clears any half-finished interaction.
  useEffect(() => {
    if (draftSaveTimer.current) {
      clearTimeout(draftSaveTimer.current);
      draftSaveTimer.current = null;
    }
    if (openSaveTimer.current) {
      clearTimeout(openSaveTimer.current);
      openSaveTimer.current = null;
    }
    draftIdRef.current = null;
    setPending(null);
    setDraft(null);
    setDraftBody("");
    setOpenId(null);
  }, [pointMode]);

  // Text selection → highlight note; plain click (point mode) → point note. Both anchor
  // to the page surface so the marker survives zoom (rects are normalized 0..1).
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;

    function handleMouseUp(event: MouseEvent) {
      // Marker/rect/popover/composer clicks are handled entirely by their own React
      // handlers — this native listener must not treat them as an "empty page" click.
      // (Native listeners fire before React's delegated synthetic ones, so a React
      // stopPropagation() inside those handlers is already too late to stop this.)
      if ((event.target as HTMLElement | null)?.closest(".pdf-annotation-layer")) return;
      if (pointMode) return;
      const surfaceEl = surfaceRef.current;
      if (!surfaceEl) return;
      const sel = window.getSelection();
      if (!sel || sel.isCollapsed || !sel.rangeCount) {
        setPending(null);
        return;
      }
      const range = sel.getRangeAt(0);
      if (!surfaceEl.contains(range.commonAncestorContainer)) return;
      const rects = clientRectsToPageRects(range, surfaceEl);
      if (!rects.length) {
        setPending(null);
        return;
      }
      const surfRect = surfaceEl.getBoundingClientRect();
      setPending({
        rects,
        quote: (sel.toString() || "").replace(/\s+/g, " ").trim().slice(0, 500),
        left: event.clientX - surfRect.left,
        top: event.clientY - surfRect.top
      });
      setDraft(null);
    }

    function handleClick(event: MouseEvent) {
      if ((event.target as HTMLElement | null)?.closest(".pdf-annotation-layer")) return;
      if (!pointMode) return;
      const surfaceEl = surfaceRef.current;
      if (!surfaceEl) return;
      const surfRect = surfaceEl.getBoundingClientRect();
      const x = (event.clientX - surfRect.left) / Math.max(1, surfRect.width);
      const y = (event.clientY - surfRect.top) / Math.max(1, surfRect.height);
      if (x < 0 || x > 1 || y < 0 || y > 1) return;
      draftIdRef.current = null;
      setDraft({ kind: "point", rects: [{ x, y, width: 0, height: 0 }], quote: "", left: event.clientX - surfRect.left, top: event.clientY - surfRect.top });
      setDraftBody("");
      setPending(null);
    }

    // Rechtsklick auf die Seite → Kontextmenü mit „Punkt-Notiz hier hinzufügen".
    // Unabhängig vom Punkt-Modus, damit man ohne Umweg an der Stelle notieren kann.
    function handleContextMenu(event: MouseEvent) {
      if ((event.target as HTMLElement | null)?.closest(".pdf-annotation-layer")) return;
      const surfaceEl = surfaceRef.current;
      if (!surfaceEl) return;
      const surfRect = surfaceEl.getBoundingClientRect();
      const left = event.clientX - surfRect.left;
      const top = event.clientY - surfRect.top;
      const x = left / Math.max(1, surfRect.width);
      const y = top / Math.max(1, surfRect.height);
      if (x < 0 || x > 1 || y < 0 || y > 1) return;
      event.preventDefault();
      setMenu({ x, y, left, top });
    }

    surface.addEventListener("mouseup", handleMouseUp);
    surface.addEventListener("click", handleClick);
    surface.addEventListener("contextmenu", handleContextMenu);
    return () => {
      surface.removeEventListener("mouseup", handleMouseUp);
      surface.removeEventListener("click", handleClick);
      surface.removeEventListener("contextmenu", handleContextMenu);
    };
  }, [surfaceRef, pointMode]);

  // Kontextmenü schließt bei Klick daneben oder Escape.
  useEffect(() => {
    if (!menu) return;
    const close = () => setMenu(null);
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key === "Escape") setMenu(null);
    };
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", onKey);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", onKey);
    };
  }, [menu]);

  const startDraftFromPending = () => {
    if (!pending) return;
    draftIdRef.current = null;
    setDraft({ kind: "highlight", rects: pending.rects, quote: pending.quote, left: pending.left, top: pending.top });
    setDraftBody("");
    setPending(null);
    window.getSelection()?.removeAllRanges();
  };

  const removeAnnotation = async (id: string) => {
    if (saving) return;
    if (openSaveTimer.current) {
      clearTimeout(openSaveTimer.current);
      openSaveTimer.current = null;
    }
    setSaving(true);
    try {
      await onDelete(id);
      if (openId === id) setOpenId(null);
    } finally {
      setSaving(false);
    }
  };

  const w = size.width;
  const h = size.height;
  if (!w || !h) return null;

  return (
    <div className="pdf-annotation-layer">
      {annotations.map((ann) => {
        const first = ann.rects[0];
        if (ann.kind === "point") {
          return (
            <button
              key={ann.id}
              type="button"
              className="pdf-annotation-marker"
              style={{ left: (first?.x ?? 0) * w, top: (first?.y ?? 0) * h }}
              title={ann.body || "Notiz"}
              onClick={(e) => { e.stopPropagation(); toggleOpen(ann); }}
            >
              <StickyNote size={12} />
            </button>
          );
        }
        return ann.rects.map((r, i) => (
          <div
            key={`${ann.id}-${i}`}
            className="pdf-annotation-rect"
            style={{ left: r.x * w, top: r.y * h, width: Math.max(3, r.width * w), height: Math.max(6, r.height * h) }}
            title={ann.body || "Notiz"}
            onClick={(e) => { e.stopPropagation(); toggleOpen(ann); }}
          />
        ));
      })}

      {openId ? (() => {
        const ann = annotations.find((a) => a.id === openId);
        if (!ann) return null;
        const first = ann.rects[0];
        const left = Math.min((first?.x ?? 0) * w, Math.max(0, w - 240));
        const top = ((first?.y ?? 0) + (first?.height ?? 0)) * h + 8;
        return (
          <div className="pdf-annotation-popover" style={{ left, top }} onClick={(e) => e.stopPropagation()}>
            <textarea
              value={openBody}
              onChange={(e) => {
                const v = e.target.value;
                setOpenBody(v);
                scheduleOpenSave(ann.id, v);
              }}
              rows={3}
              autoFocus
              placeholder="Notiz…"
            />
            <div className="pdf-annotation-popover-actions">
              <button className="button button-compact button-ghost" type="button" disabled={saving} onClick={() => void removeAnnotation(ann.id)}>
                <Trash2 size={13} /> Löschen
              </button>
              <button className="icon-button" type="button" aria-label="Schließen" onClick={closeOpen}><X size={14} /></button>
            </div>
          </div>
        );
      })() : null}

      {pending ? (
        <button
          type="button"
          className="pdf-annotation-add"
          style={{ left: Math.min(pending.left, Math.max(0, w - 150)), top: pending.top + 10 }}
          onClick={(e) => { e.stopPropagation(); startDraftFromPending(); }}
        >
          <Plus size={13} /> Notiz hinzufügen
        </button>
      ) : null}

      {draft ? (
        <div
          className="pdf-annotation-popover pdf-annotation-composer"
          style={{ left: Math.min(draft.left, Math.max(0, w - 240)), top: draft.top + 10 }}
          onClick={(e) => e.stopPropagation()}
        >
          <textarea
            value={draftBody}
            onChange={(e) => {
              const v = e.target.value;
              setDraftBody(v);
              scheduleDraftSave(v);
            }}
            rows={3}
            autoFocus
            placeholder={draft.kind === "point" ? "Punkt-Notiz…" : "Notiz zur markierten Stelle…"}
          />
          <div className="pdf-annotation-popover-actions">
            <button className="icon-button" type="button" aria-label="Schließen" onClick={closeDraft}><X size={14} /></button>
          </div>
        </div>
      ) : null}

      {menu ? (
        <div
          className="pdf-annotation-menu"
          style={{ left: Math.min(menu.left, Math.max(0, w - 210)), top: menu.top + 4 }}
          onMouseDown={(e) => e.stopPropagation()}
        >
          <button
            type="button"
            className="pdf-annotation-menu-item"
            onClick={() => {
              draftIdRef.current = null;
              setDraft({ kind: "point", rects: [{ x: menu.x, y: menu.y, width: 0, height: 0 }], quote: "", left: menu.left, top: menu.top });
              setDraftBody("");
              setPending(null);
              setMenu(null);
            }}
          >
            <Plus size={13} /> Punkt-Notiz hier hinzufügen
          </button>
        </div>
      ) : null}
    </div>
  );
}

