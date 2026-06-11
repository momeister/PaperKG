import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.mjs?url";
import { ChevronLeft, ChevronRight, Languages, Layers, Maximize2, PanelRightClose, Search, X, ZoomIn, ZoomOut } from "lucide-react";

import { api } from "../api";
import { colorVarsForPaperId } from "../citationColors";
import { useAppState } from "../state";
import type { PaperMeta, VerificationEvidence } from "../types";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

type PdfDocument = {
  numPages: number;
  getPage: (pageNumber: number) => Promise<any>;
  destroy?: () => Promise<void>;
};

type HighlightBox = {
  id: string;
  evidenceIndex: number;
  colorIndex: number;
  left: number;
  top: number;
  width: number;
  height: number;
};

export type PageMatch = {
  pageNumber: number;
  score: number;
  exact: boolean;
  matchedText: string;
  boxes: HighlightBox[];
  querySignature: string;
};

type MatchIndex = Record<number, Record<number, PageMatch>>;

type HighlightQuery = {
  phrases: string[];
  terms: string[];
};

// Search renders as its own highlight layer (parallel to evidence highlights)
// instead of replacing them.
export const SEARCH_LAYER_INDEX = -1;

export type HighlightLayer = {
  index: number;
  colorIndex: number;
  query: HighlightQuery;
  signature: string;
};

type IndexedTextItem = {
  item: unknown;
  index: number;
  text: string;
  start: number;
  end: number;
};

const ANCHOR_STOPWORDS = new Set([
  "about",
  "also",
  "and",
  "are",
  "based",
  "between",
  "clinical",
  "clinicians",
  "decision",
  "during",
  "from",
  "have",
  "into",
  "paper",
  "primary",
  "study",
  "support",
  "that",
  "the",
  "their",
  "this",
  "through",
  "used",
  "using",
  "with",
  "without"
]);

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

  // When there is no local PDF, fetch the cited paper's metadata so the user can still
  // read the abstract and open the original source for verification.
  useEffect(() => {
    if (url || !metaPaperId) {
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
  }, [url, metaPaperId]);

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
    const text = (activeEvidenceText || activeEvidence?.pdf_excerpt || activeEvidence?.reference_text || "").trim();
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
        </div>
      ) : null}

      {url && document ? (
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
                setPageRef={(node) => {
                  pageRefs.current[pageNumber] = node;
                }}
              />
            );
          })}
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
                disabled={isTranslating || !(activeEvidenceText || activeEvidence?.pdf_excerpt || activeEvidence?.reference_text)}
                onClick={() => void translateActiveExcerpt()}
              >
                {isTranslating ? "Übersetzt…" : "Übersetzen"}
              </button>
            </span>
          </div>
          <p>
            {activeEvidenceText ||
              (url && document && !scanComplete
                ? "Suche Textstelle…"
                : activeEvidence?.pdf_excerpt || "Keine Textstelle gefunden.")}
          </p>
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
  setPageRef: (node: HTMLDivElement | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const textLayerRef = useRef<HTMLDivElement | null>(null);
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

      canvas.width = viewport.width;
      canvas.height = viewport.height;
      canvas.style.width = `${viewport.width}px`;
      canvas.style.height = `${viewport.height}px`;
      setSize({ width: viewport.width, height: viewport.height });
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
      <div className="pdf-page-surface" style={{ width: size.width || undefined, height: size.height || undefined }}>
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
      </div>
    </div>
  );
}

function textItemBox(item: IndexedTextItem, viewport: any, evidenceIndex: number, colorIndex = evidenceIndex, rangeStart = 0, rangeEnd = item.text.length): HighlightBox | null {
  const textItem = item.item as { transform?: number[]; width?: number; height?: number; str?: string };
  if (!textItem.transform) {
    return null;
  }
  const transform = pdfjs.Util.transform(viewport.transform, textItem.transform);
  const height = Math.max(8, Math.hypot(transform[2], transform[3]) || Number(textItem.height) || 10);
  const fullWidth = Math.max(10, Number(textItem.width || String(textItem.str ?? "").length * 5) * viewport.scale);
  const textLength = Math.max(1, item.text.length);
  const safeStart = Math.max(0, Math.min(textLength, rangeStart));
  const safeEnd = Math.max(safeStart + 1, Math.min(textLength, rangeEnd));
  const startRatio = safeStart / textLength;
  const endRatio = safeEnd / textLength;
  const width = Math.max(4, fullWidth * (endRatio - startRatio));
  const left = transform[4] + fullWidth * startRatio;
  const top = transform[5] - height;
  return {
    id: `${evidenceIndex}-${item.index}-${safeStart}-${safeEnd}-${left}-${top}`,
    evidenceIndex,
    colorIndex,
    left: Math.max(0, left - 1),
    top: Math.max(0, top - 2),
    width: width + 3,
    height: height + 4
  };
}

export function findPageMatch(textItems: unknown[], query: HighlightQuery, viewport: any, evidenceIndex: number, colorIndex = evidenceIndex): Omit<PageMatch, "pageNumber" | "querySignature"> | null {
  const indexed = indexTextItems(textItems);
  if (!indexed.text || (!query.phrases.length && !query.terms.length)) {
    return null;
  }

  const phraseMatch = bestPhraseMatch(indexed.items, indexed.text, query.phrases, viewport, evidenceIndex, colorIndex);
  if (phraseMatch) {
    return phraseMatch;
  }

  return bestTermWindowMatch(indexed.items, indexed.text, query.terms, viewport, evidenceIndex, colorIndex);
}

function indexTextItems(items: unknown[]): { items: IndexedTextItem[]; text: string } {
  const indexed: IndexedTextItem[] = [];
  let text = "";
  items.forEach((item, index) => {
    const itemText = normalizeText((item as any).str ?? "");
    if (!itemText) {
      return;
    }
    if (text) {
      text += " ";
    }
    const start = text.length;
    text += itemText;
    indexed.push({ item, index, text: itemText, start, end: text.length });
  });
  return { items: indexed, text };
}

function bestPhraseMatch(
  items: IndexedTextItem[],
  pageText: string,
  phrases: string[],
  viewport: any,
  evidenceIndex: number,
  colorIndex = evidenceIndex
): Omit<PageMatch, "pageNumber" | "querySignature"> | null {
  // Union matching: a multi-sentence excerpt rarely matches the page text as ONE phrase
  // (hyphenation/line breaks split it), but its sentence chunks do. Highlighting only the
  // single best chunk marked just part of the passage — collect every matching phrase
  // span and mark them all.
  type PhraseSpan = { start: number; end: number; text: string };
  const spans: PhraseSpan[] = [];
  let longestPhraseLength = 0;
  for (const phrase of phrases) {
    const normalizedPhrase = normalizeText(phrase);
    if (!normalizedPhrase) {
      continue;
    }
    longestPhraseLength = Math.max(longestPhraseLength, normalizedPhrase.length);
    let position = pageText.indexOf(normalizedPhrase);
    while (position >= 0) {
      const end = position + normalizedPhrase.length;
      if (
        textRangeHasBoundary(pageText, position, end) &&
        // Sub-phrases of an already covered span add nothing (phrases arrive longest-first).
        !spans.some((span) => position >= span.start && end <= span.end)
      ) {
        spans.push({ start: position, end, text: compactText(phrase) });
      }
      position = pageText.indexOf(normalizedPhrase, end);
    }
  }
  if (!spans.length) {
    return null;
  }

  spans.sort((left, right) => left.start - right.start || right.end - left.end);
  const merged: PhraseSpan[] = [];
  for (const span of spans) {
    const previous = merged[merged.length - 1];
    if (previous && span.start <= previous.end + 1) {
      if (span.end > previous.end) {
        previous.text = `${previous.text} ${span.text}`;
        previous.end = span.end;
      }
      continue;
    }
    merged.push({ ...span });
  }

  const boxes = normalizeHighlightBoxes(
    merged.flatMap((span) => boxesForTextRange(items, span.start, span.end, viewport, evidenceIndex, colorIndex))
  ).slice(0, 36);
  if (!boxes.length) {
    return null;
  }
  const coveredLength = merged.reduce((sum, span) => sum + (span.end - span.start), 0);
  // "exact" gates early scrolling and cross-page rendering: require the match to cover a
  // meaningful share of the queried passage so a stray short chunk on another page does
  // not count as the passage itself.
  const exact = coveredLength >= Math.min(200, Math.max(40, Math.round(longestPhraseLength * 0.55)));
  return {
    score: (exact ? 10000 : 5000) + coveredLength * 2 - boxes.length,
    exact,
    matchedText: merged.map((span) => span.text).join(" … "),
    boxes
  };
}

function bestTermWindowMatch(
  items: IndexedTextItem[],
  pageText: string,
  terms: string[],
  viewport: any,
  evidenceIndex: number,
  colorIndex: number
): Omit<PageMatch, "pageNumber" | "querySignature"> | null {
  if (!terms.length) {
    return null;
  }
  const occurrences = termOccurrences(pageText, terms);
  const matchedTerms = new Set(occurrences.map((item) => item.term));
  const requiredHits = terms.length <= 4 ? terms.length : Math.min(5, Math.max(3, Math.ceil(terms.length * 0.35)));
  if (matchedTerms.size < requiredHits) {
    return null;
  }

  const maxWindow = 280;
  let bestWindow: { start: number; end: number; score: number; occurrences: typeof occurrences } | null = null;
  const ordered = [...occurrences].sort((left, right) => left.start - right.start || left.end - right.end);
  for (let left = 0; left < ordered.length; left += 1) {
    const windowOccurrences: typeof occurrences = [];
    for (let right = left; right < ordered.length; right += 1) {
      const start = ordered[left].start;
      const end = ordered[right].end;
      if (end - start > maxWindow) {
        break;
      }
      windowOccurrences.push(ordered[right]);
      const distinct = new Set(windowOccurrences.map((item) => item.term));
      if (distinct.size < requiredHits) {
        continue;
      }
      const strongHits = windowOccurrences.filter((item) => isStrongFallbackTerm(item.term)).length;
      if (!strongHits && distinct.size < Math.max(4, requiredHits + 1)) {
        continue;
      }
      const span = Math.max(1, end - start);
      const score = distinct.size * 100 + strongHits * 20 - span / 4;
      if (!bestWindow || score > bestWindow.score) {
        bestWindow = { start, end, score, occurrences: [...windowOccurrences] };
      }
    }
  }
  if (!bestWindow) {
    return null;
  }
  const boxes = normalizeHighlightBoxes(boxesForTextRange(items, bestWindow.start, bestWindow.end, viewport, evidenceIndex, colorIndex)).slice(0, 18);
  if (!boxes.length) {
    return null;
  }
  return {
    score: bestWindow.score,
    exact: false,
    matchedText: pageText.slice(bestWindow.start, bestWindow.end).trim(),
    boxes
  };
}

function boxesForTextRange(items: IndexedTextItem[], start: number, end: number, viewport: any, evidenceIndex: number, colorIndex: number): HighlightBox[] {
  const boxes: HighlightBox[] = [];
  for (const item of items) {
    if (item.end <= start || item.start >= end) {
      continue;
    }
    const rangeStart = Math.max(0, start - item.start);
    const rangeEnd = Math.min(item.text.length, end - item.start);
    const box = textItemBox(item, viewport, evidenceIndex, colorIndex, rangeStart, rangeEnd);
    if (box) {
      boxes.push(box);
    }
  }
  return uniqueBoxes(boxes);
}

function termOccurrences(pageText: string, terms: string[]) {
  return Array.from(new Set(terms))
    .flatMap((term) => findTermOccurrences(pageText, term).slice(0, 8).map((start) => ({ term, start, end: start + term.length })))
    .sort((left, right) => left.start - right.start || left.end - right.end);
}

function findTermOccurrences(text: string, term: string) {
  const positions: number[] = [];
  if (!term) {
    return positions;
  }
  let position = text.indexOf(term);
  while (position >= 0) {
    const end = position + term.length;
    if (textRangeHasBoundary(text, position, end)) {
      positions.push(position);
    }
    position = text.indexOf(term, Math.max(end, position + 1));
  }
  return positions;
}

function textRangeHasBoundary(text: string, start: number, end: number) {
  return isTextBoundary(text[start - 1]) && isTextBoundary(text[end]);
}

function isTextBoundary(value: string | undefined) {
  return !value || !/[\p{L}\p{N}-]/u.test(value);
}

function uniqueBoxes(boxes: HighlightBox[]) {
  const seen = new Set<string>();
  const output: HighlightBox[] = [];
  for (const box of boxes) {
    const key = `${Math.round(box.left)}:${Math.round(box.top)}:${Math.round(box.width)}:${Math.round(box.height)}`;
    if (seen.has(key)) {
      continue;
    }
    seen.add(key);
    output.push(box);
  }
  return output;
}

function mergeOverlappingBoxes(boxes: HighlightBox[]) {
  let current = uniqueBoxes(boxes);
  let changed = true;
  while (changed) {
    changed = false;
    const ordered = [...current].sort((left, right) => left.top - right.top || left.left - right.left);
    const merged: HighlightBox[] = [];
    for (const box of ordered) {
      const target = merged.find((candidate) => boxesTouch(candidate, box));
      if (!target) {
        merged.push({ ...box });
        continue;
      }
      const left = Math.min(target.left, box.left);
      const top = Math.min(target.top, box.top);
      const right = Math.max(target.left + target.width, box.left + box.width);
      const bottom = Math.max(target.top + target.height, box.top + box.height);
      target.left = left;
      target.top = top;
      target.width = right - left;
      target.height = bottom - top;
      changed = true;
    }
    current = merged;
  }
  return current;
}

function boxesTouch(left: HighlightBox, right: HighlightBox) {
  const pad = 2;
  return (
    left.left <= right.left + right.width + pad &&
    left.left + left.width + pad >= right.left &&
    left.top <= right.top + right.height + pad &&
    left.top + left.height + pad >= right.top
  );
}

export function normalizeHighlightBoxes(boxes: HighlightBox[]) {
  const merged = mergeOverlappingBoxes(boxes);
  const rows: HighlightBox[][] = [];
  for (const box of merged.sort((left, right) => left.top - right.top || left.left - right.left)) {
    const row = rows.find((candidate) => candidate.some((item) => verticalOverlapRatio(item, box) > 0.45));
    if (row) {
      row.push({ ...box });
    } else {
      rows.push([{ ...box }]);
    }
  }
  return rows.flatMap((row) => mergeRowSegments(row));
}

function mergeRowSegments(row: HighlightBox[]) {
  const ordered = row.sort((left, right) => left.left - right.left);
  const output: HighlightBox[] = [];
  for (const box of ordered) {
    const previous = output[output.length - 1];
    if (!previous || box.left > previous.left + previous.width + 2) {
      output.push({ ...box });
      continue;
    }
    const right = Math.max(previous.left + previous.width, box.left + box.width);
    previous.left = Math.min(previous.left, box.left);
    previous.top = Math.min(previous.top, box.top);
    previous.width = right - previous.left;
    previous.height = Math.max(previous.height, box.height);
  }
  return output;
}

function verticalOverlapRatio(left: HighlightBox, right: HighlightBox) {
  const top = Math.max(left.top, right.top);
  const bottom = Math.min(left.top + left.height, right.top + right.height);
  const overlap = Math.max(0, bottom - top);
  return overlap / Math.max(1, Math.min(left.height, right.height));
}

function buildHighlightQuery(evidence: VerificationEvidence): HighlightQuery {
  const explicit = (evidence.matched_terms ?? []).map(normalizeText).filter(Boolean);
  const excerpt = compactText(evidence.pdf_excerpt);
  const reference = compactText(evidence.reference_text);
  const referenceTerms = extractTerms(`${excerpt} ${reference}`);
  // Allow the FULL excerpt as a phrase (excerpts are typically 260–700 chars; the old
  // 180-char cap silently dropped it, so only one sentence chunk ended up highlighted).
  const phrases = [...extractPhrases(excerpt, 800), ...extractPhrases(reference, 160)].slice(0, 9);
  const terms = extractTerms(`${explicit.join(" ")} ${excerpt} ${reference}`).filter(isAnchorTerm);
  return {
    phrases: Array.from(new Set(phrases)).slice(0, 8),
    terms: Array.from(new Set([...explicit, ...referenceTerms, ...terms])).filter(isAnchorTerm).slice(0, 18)
  };
}

function evidenceListSignature(evidences: VerificationEvidence[]) {
  return evidences
    .map((evidence) =>
      [
        evidence.paper_id,
        evidence.kind,
        evidence.evidence_index ?? "",
        evidence.reference_text,
        evidence.pdf_excerpt,
        (evidence.matched_terms ?? []).join(",")
      ].join("|")
    )
    .join("\u001f");
}

function evidenceColorIndex(evidence: VerificationEvidence | undefined, fallbackIndex: number) {
  const index = Number(evidence?.evidence_index);
  return Number.isFinite(index) ? index : fallbackIndex;
}

export function highlightQuerySignature(query: HighlightQuery) {
  return `${query.phrases.map(normalizeText).join("\u001e")}|\u001f|${query.terms.map(normalizeText).join("\u001e")}`;
}

export function bestMatchFor(pageMatches: Record<number, PageMatch> | undefined, querySignature: string) {
  const candidates = Object.values(pageMatches ?? {}).filter((match) => match.querySignature === querySignature && match.boxes.length);
  return candidates.sort((left, right) => right.score - left.score || Number(right.exact) - Number(left.exact) || left.pageNumber - right.pageNumber)[0] ?? null;
}

export const HIGHLIGHT_SCROLL_PADDING = 64;

// Scroll target so the highlight's top edge lands `padding` px below the viewport's
// top — not just the page centered, which can leave a highlight near a large/zoomed
// page's bottom edge off-screen. `pageRelativeTop` is the highlighted page surface's
// offset within the scroll container; `highlightTop` is the box's offset within that
// surface (same coordinate space `HighlightBox.top` is rendered in).
export function highlightScrollTop(pageRelativeTop: number, highlightTop: number, padding = HIGHLIGHT_SCROLL_PADDING): number {
  return Math.max(0, pageRelativeTop + highlightTop - padding);
}

// Topmost (smallest `top`) box of a match — the part of the highlight that should
// be brought into view first when scrolling to it.
export function topmostHighlightTop(boxes: { top: number }[] | null | undefined): number | null {
  if (!boxes || !boxes.length) {
    return null;
  }
  return boxes.reduce((min, box) => (box.top < min ? box.top : min), boxes[0].top);
}

function pagesFor(pageMatches: Record<number, PageMatch> | undefined, querySignature: string) {
  return Object.values(pageMatches ?? {})
    .filter((match) => match.querySignature === querySignature && match.boxes.length)
    .map((match) => match.pageNumber)
    .sort((left, right) => left - right);
}

function buildSearchQuery(term: string): HighlightQuery {
  const text = compactText(term);
  if (!text) {
    return { phrases: [], terms: [] };
  }
  return {
    phrases: [text],
    terms: extractTerms(text).slice(0, 12)
  };
}

function extractPhrases(text: string, maxLength = 220): string[] {
  if (!text) {
    return [];
  }
  const chunks = [text, ...text.split(/(?:[.!?;:]\s+|\n+)/g)];
  return chunks
    .map((chunk) => compactText(chunk))
    .filter((chunk) => {
      const tokens = chunk.split(" ").filter(Boolean);
      return chunk.length >= 12 && chunk.length <= maxLength && tokens.length >= 2;
    })
    .sort((left, right) => right.length - left.length);
}

function extractTerms(text: string): string[] {
  return Array.from(new Set(normalizeText(text).split(" ")))
    .filter((term) => term.length >= 5 && !ANCHOR_STOPWORDS.has(term) && !/^\d+$/.test(term))
    .slice(0, 28);
}

function isAnchorTerm(term: string) {
  return !ANCHOR_STOPWORDS.has(term) && (term.length >= 7 || /\d/.test(term) || term.includes("-"));
}

function isStrongFallbackTerm(term: string) {
  return /\d/.test(term) || term.includes("-") || term.length >= 10;
}

function compactText(text: string) {
  return text.replace(/\s+/g, " ").trim();
}

function normalizeText(text: string) {
  return text
    .toLowerCase()
    .replace(/[^\p{L}\p{N}-]+/gu, " ")
    .replace(/\s+/g, " ")
    .trim();
}

function shortLabel(value: string) {
  return value.length > 72 ? `${value.slice(0, 69)}...` : value;
}
