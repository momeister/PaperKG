import { PaneHeader, usePaneEnvironment } from "../workspace/PortablePane";
import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type RefObject } from "react";
import * as pdfjs from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.mjs?url";
import { ChevronLeft, ChevronRight, ExternalLink, Languages, Layers, Loader2, MapPin, Maximize2, PanelRightClose, Plus, Search, StickyNote, Trash2, X, ZoomIn, ZoomOut } from "lucide-react";

import { ResizableSection } from "./ResizableSection";
import { PdfSelectionActions } from "./PdfSelectionActions";
import { findTextOccurrences, occurrenceRects, selectionRectsOnPage, type PdfSearchHit, type TextFragment } from "./pdfTextSearch";
import { api } from "../api";
import { colorVarsForPaperId } from "../citationColors";
import { useAppState } from "../state";
import type { PaperMeta, PdfAnnotation, PdfAnnotationRect, PdfAnchor, PdfSelection, VerificationEvidence } from "../types";

import {
  bestMatchFor,
  buildHighlightQuery,
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
  headerInPaneToolbar?: boolean;
  selection?: PdfSelection | null;
  onSelectionChange?: (selection: PdfSelection | null) => void;
  onInsertSelection?: (selection: PdfSelection, text: string, language?: string) => Promise<void>;
  anchors?: PdfAnchor[] | null;
  anchorRequestKey?: unknown;
  url?: string | null;
  title?: string;
  unavailableMessage?: string;
  metaPaperId?: string;
  evidences?: VerificationEvidence[];
  activeEvidenceIndex?: number;
  onActiveEvidenceChange?: (index: number) => void;
  onCollapse?: () => void;
  onIngestMissing?: (paperId: string) => void;
  ingestPending?: boolean;
  /** Status text from the ingest flow (success / error / progress hint). Shown
   *  inside the "Kein PDF verfügbar" placeholder so the user sees feedback right
   *  where they clicked "PDF nachladen". */
  ingestStatus?: string;
};

export function PdfPane({
  headerInPaneToolbar = false,
  selection: controlledSelection, onSelectionChange, onInsertSelection, anchors, anchorRequestKey,
  url,
  title,
  unavailableMessage,
  metaPaperId,
  evidences = [],
  activeEvidenceIndex = 0,
  onActiveEvidenceChange,
  onCollapse,
  onIngestMissing,
  ingestPending,
  ingestStatus
}: PdfPaneProps) {
  const Header = headerInPaneToolbar ? PaneHeader : Fragment;
  const { window } = usePaneEnvironment();
  const [localSelection, setLocalSelection] = useState<PdfSelection | null>(null);
  const selection = controlledSelection === undefined ? localSelection : controlledSelection;
  const setSelection = useCallback((value: PdfSelection | null) => {
    if (!value) {
      const native = canvasWrapRef.current?.ownerDocument.getSelection();
      if (native?.anchorNode && canvasWrapRef.current?.contains(native.anchorNode)) native.removeAllRanges();
    }
    setLocalSelection(value);
    onSelectionChange?.(value);
  }, [onSelectionChange]);
  const [searchResults, setSearchResults] = useState<Record<number, { hits: PdfSearchHit[]; hasText: boolean; error?: string }>>({});
  const [searchIndex, setSearchIndex] = useState(0);
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
  const [searchOpen, setSearchOpen] = useState(!headerInPaneToolbar);
  const [excerptRequest, setExcerptRequest] = useState(0);
  const [showAllEvidences, setShowAllEvidences] = useState(false);
  const { provider, model } = useAppState();
  const [translateLanguage, setTranslateLanguage] = useState("Deutsch");
  const [translation, setTranslation] = useState("");
  const [translateError, setTranslateError] = useState("");
  const [isTranslating, setIsTranslating] = useState(false);
  const translateVersion = useRef(0);
  const canvasWrapRef = useRef<HTMLDivElement | null>(null);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});
  const resizeFrameRef = useRef<number | null>(null);
  const lastJumpKeyRef = useRef<string>("");
  // PDF-Notizen: persistent kleine Notizen an einer Textstelle/Punkt (nur mit paper_id).
  const currentPaperRef = useRef(metaPaperId);
  currentPaperRef.current = metaPaperId;
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
      if (currentPaperRef.current === metaPaperId) setAnnotations((current) => [...current, created.annotation]);
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
  const showingSearch = Boolean(searchTerm.trim());
  const activeEvidence = evidences[activeEvidenceIndex];
  const activeEvidenceColorIndex = evidenceColorIndex(activeEvidence, activeEvidenceIndex);
  const activeQuery = evidenceQueries[activeEvidenceIndex] ?? { phrases: [], terms: [] };
  const activeQuerySignature = highlightQuerySignature(activeQuery);
  const previousExcerpt = useRef({ query: activeQuerySignature, index: activeEvidenceIndex, request: anchorRequestKey });
  useEffect(() => {
    const previous = previousExcerpt.current;
    if (activeEvidence && (previous.query !== activeQuerySignature || previous.index !== activeEvidenceIndex || previous.request !== anchorRequestKey)) {
      setExcerptRequest(value => value + 1);
    }
    previousExcerpt.current = { query: activeQuerySignature, index: activeEvidenceIndex, request: anchorRequestKey };
  }, [activeQuerySignature, activeEvidenceIndex, anchorRequestKey]);
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
    return list;
  }, [evidenceQueries, evidenceSignature, activeEvidenceIndex, showAllEvidences]);
  const layersSignature = layers.map((layer) => `${layer.index}#${layer.signature}`).join("");
  // Scrolling follows the search while typing, otherwise the active citation.
  const scrollLayerIndex = activeEvidenceIndex;
  const scrollSignature = activeQuerySignature;
  const activeMatch = bestMatchFor(matches[scrollLayerIndex], scrollSignature);
  const evidenceMatch = bestMatchFor(matches[activeEvidenceIndex], activeQuerySignature);
  const searchKey = `${url}|${searchTerm}|${zoom}|${viewportWidth}|${fitMode}`;
  const searchKeyRef = useRef(searchKey);
  searchKeyRef.current = searchKey;
  const searchHits = Object.entries(searchResults).sort(([a], [b]) => Number(a) - Number(b)).flatMap(([, result]) => result.hits);
  const searchScanned = Object.keys(searchResults).length;
  const reportSearch = useCallback((key: string, page: number, hits: PdfSearchHit[], hasText: boolean, error?: string) => {
    if (key !== searchKeyRef.current) return;
    setSearchResults(current => ({ ...current, [page]: { hits, hasText, error } }));
  }, []);
  useEffect(() => { setSearchResults({}); setSearchIndex(0); }, [searchKey]);
  useEffect(() => { setSelection(null); }, [url, metaPaperId, setSelection]);
  useEffect(() => {
    const clear = (event: KeyboardEvent) => { if (event.key === "Escape") setSelection(null); };
    window.addEventListener("keydown", clear);
    return () => window.removeEventListener("keydown", clear);
  }, [window, setSelection]);
  const activeSearchHit = searchHits[searchIndex];
  useEffect(() => {
    if (showingSearch && searchScanned === pageCount && activeSearchHit) {
      const surface = pageRefs.current[activeSearchHit.page]?.querySelector<HTMLElement>(".pdf-page-surface");
      jumpToPage(activeSearchHit.page, "start", (activeSearchHit.rects[0]?.y ?? 0) * (surface?.clientHeight ?? 0), (activeSearchHit.rects[0]?.x ?? 0) * (surface?.clientWidth ?? 0));
    }
  }, [searchIndex, searchScanned, pageCount, searchKey]);
  const stepSearch = (direction: number) => setSearchIndex(i => searchHits.length ? (i + direction + searchHits.length) % searchHits.length : 0);
  function capturePdfSelection(event: React.MouseEvent<HTMLDivElement>) {
    if (pointMode || (event.target as HTMLElement).closest(".pdf-annotation-layer")) return;
    const sel = window.getSelection();
    if (!sel || sel.isCollapsed || !sel.rangeCount) { setSelection(null); return; }
    const range = sel.getRangeAt(0);
    const root = canvasWrapRef.current;
    if (!root || !root.contains(range.startContainer) || !root.contains(range.endContainer) || !metaPaperId) return;
    const selectedAnchors: PdfAnchor[] = [];
    for (const [page, node] of Object.entries(pageRefs.current)) {
      const surface = node?.querySelector<HTMLElement>(".pdf-page-surface");
      if (!surface) continue;
      const rects = selectionRectsOnPage(range, surface);
      if (rects.length) selectedAnchors.push({ page_number: Number(page), rects });
    }
    if (selectedAnchors.length) setSelection({ paperId: metaPaperId, originalText: sel.toString(), anchors: selectedAnchors });
  }
  const anchorSignature = JSON.stringify(anchors);
  useEffect(() => {
    const first = anchors?.[0];
    if (!first || !pageCount || showingSearch) return;
    const frame = window.requestAnimationFrame(() => {
      const surface = pageRefs.current[first.page_number]?.querySelector<HTMLElement>(".pdf-page-surface");
      if (surface?.clientHeight) jumpToPage(first.page_number, "start", (first.rects[0]?.y ?? 0) * surface.clientHeight);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [anchorSignature, anchorRequestKey, showingSearch, pageCount, searchScanned, zoom, viewportWidth, window]);
  const evidencePages = pagesFor(matches[activeEvidenceIndex], activeQuerySignature);
  const targetPages = useMemo(() => {
    const result: Record<number, number | null> = {};
    for (const layer of layers) {
      result[layer.index] = Number(evidences[layer.index]?.metadata?.page) || bestMatchFor(matches[layer.index], layer.signature)?.pageNumber || null;
    }
    return result;
  }, [layers, matches, evidences]);
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
  }, [evidenceSignature, url, showAllEvidences]);

  useEffect(() => {
    translateVersion.current++;
    setIsTranslating(false);
    setTranslation("");
    setTranslateError("");
  }, [translateLanguage, activeEvidenceIndex, evidenceSignature, url]);

  async function translateActiveExcerpt() {
    const text = (activeEvidence?.pdf_excerpt || activeEvidenceText || activeEvidence?.reference_text || "").trim();
    if (!text || isTranslating) {
      return;
    }
    const version = ++translateVersion.current;
    setIsTranslating(true);
    setTranslateError("");
    try {
      const result = await api.rewriteNote({
        text,
        instruction: `Übersetze den folgenden Text nach ${translateLanguage}. Gib ausschließlich die Übersetzung aus, ohne Kommentar.`,
        provider,
        model
      });
      if (version === translateVersion.current) setTranslation(result.text);
    } catch (error) {
      if (version === translateVersion.current) setTranslateError(error instanceof Error ? error.message : "Übersetzung fehlgeschlagen");
    } finally {
      if (version === translateVersion.current) setIsTranslating(false);
    }
  }

  useEffect(() => {
    const node = canvasWrapRef.current;
    if (!node) {
      return;
    }
    const updateWidth = () => setViewportWidth(Math.max(320, node.clientWidth));
    updateWidth();
    if (typeof window.ResizeObserver === "undefined") {
      return;
    }
    const observer = new window.ResizeObserver(() => {
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
  }, [document, url, window]);

  useEffect(() => {
    if (showingSearch || anchors?.length) return;
    const indexedPage = !showingSearch ? Number(activeEvidence?.metadata?.page) : 0;
    const targetPage = indexedPage || activeMatch?.pageNumber;
    if (!targetPage || !pageCount || !pageRefs.current[targetPage]) {
      return;
    }
    if (indexedPage && Array.from({ length: targetPage }, (_, i) => i + 1).some(page => !scannedPages[activeScanKey]?.[page])) {
      return;
    }
    // Jump once per query: either as soon as a confident (exact) match appears, or after
    // every page reported in. Jumping on every interim "best" match made the view hop
    // between pages while the document was still being scanned.
    if (!indexedPage && !scanComplete && !activeMatch?.exact) {
      return;
    }
    const jumpKey = `${url ?? ""}|${activeScanKey}|${targetPage}`;
    if (lastJumpKeyRef.current === jumpKey) {
      return;
    }
    // The PDF pages mount before their asynchronous dimensions are known. A jump
    // to those zero-height placeholders must not consume the once-per-claim jump.
    const frame = window.requestAnimationFrame(() => {
      for (let page = 1; page <= targetPage; page++) {
        const surface = pageRefs.current[page]?.querySelector<HTMLElement>(".pdf-page-surface");
        if (!surface || !parseFloat(surface.style.height)) return;
      }
      lastJumpKeyRef.current = jumpKey;
      jumpToPage(targetPage, "center", activeMatch?.pageNumber === targetPage ? topmostHighlightTop(activeMatch.boxes) : null);
    });
    return () => window.cancelAnimationFrame(frame);
  }, [activeEvidence?.metadata?.page, activeEvidenceIndex, activeMatch?.pageNumber, activeMatch?.exact, scanComplete, scannedPages, pageCount, activeScanKey, showingSearch, url, window]);

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
    const targetPage = Number(evidences[index]?.metadata?.page) || match?.pageNumber;
    if (targetPage) {
      jumpToPage(targetPage, "center", match?.pageNumber === targetPage ? topmostHighlightTop(match.boxes) : null);
    }
  }

  function stepEvidence(direction: -1 | 1) {
    if (!evidences.length) {
      return;
    }
    const next = (activeEvidenceIndex + direction + evidences.length) % evidences.length;
    jumpToEvidence(next);
  }

  function jumpToPage(pageNumber: number, block: ScrollLogicalPosition = "start", highlightTop: number | null = null, highlightLeft: number | null = null) {
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
    const uiScale = rootRect.width / Math.max(1, root.offsetWidth);
    const relativeTop = (pageRect.top - rootRect.top) / uiScale + root.scrollTop;
    if (highlightTop != null) {
      // `HighlightBox.top` is relative to `.pdf-page-surface` (inside `.pdf-page`,
      // below the "Seite N" label) — account for that offset so the computed scroll
      // target lines up with where the highlight is actually rendered.
      const surface = pageNode.querySelector<HTMLElement>(".pdf-page-surface");
      const surfaceOffset = surface ? (surface.getBoundingClientRect().top - pageRect.top) / uiScale : 0;
      root.scrollTo({ top: highlightScrollTop(relativeTop + surfaceOffset, highlightTop), left: highlightLeft == null ? root.scrollLeft : Math.max(0, highlightLeft - 64), behavior: "smooth" });
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
    <aside className={`pdf-pane ${headerInPaneToolbar ? "pdf-pane--compact" : ""}`}>
      <Header><div className="pane-heading">
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
      </div></Header>

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
          <button className="button button-compact pdf-search-toggle" type="button" aria-expanded={searchOpen || !!searchTerm} onClick={() => setSearchOpen(value => !value)}><Search size={16} /> Suche</button>
          <div className="pdf-search-row" hidden={!searchOpen && !searchTerm}>
            <Search size={17} />
            <input value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} onKeyDown={event => { if (event.key === "Enter") { event.preventDefault(); stepSearch(event.shiftKey ? -1 : 1); } }} placeholder="In PDF suchen" />
            <button className={`icon-button ${searchTerm ? "" : "pdf-search-clear--hidden"}`} type="button" aria-label="Suche leeren" onClick={() => setSearchTerm("")} disabled={!searchTerm}>
              <X size={17} />
            </button>
            <button type="button" aria-label="Vorheriger Suchtreffer" disabled={!searchHits.length} onClick={() => stepSearch(-1)}>↑</button>
            <button type="button" aria-label="Nächster Suchtreffer" disabled={!searchHits.length} onClick={() => stepSearch(1)}>↓</button>
            <span role="status">{showingSearch ? `${searchHits.length ? searchIndex + 1 : 0} / ${searchHits.length} Treffer${searchScanned < pageCount ? ` · Suche ${searchScanned}/${pageCount} Seiten` : ""}` : ""}</span>
          </div>
          {showingSearch && <div className="pdf-search-results">
            {searchHits.map((hit, i) => <button type="button" key={`${hit.page}-${i}`} aria-pressed={i === searchIndex} onClick={() => setSearchIndex(i)}>Seite {hit.page}: {hit.context}</button>)}
            {searchScanned === pageCount && !Object.values(searchResults).some(r => r.hasText) && <p>Dieses PDF hat keine durchsuchbare Textschicht (Bild-PDF). OCR ist nicht verfügbar.</p>}
            {Object.values(searchResults).some(r => r.error) && <p role="alert">Einige Seiten konnten nicht durchsucht werden.</p>}
          </div>}
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
              <small>Text markieren → PDF-Notiz, Übersetzung oder Zitat</small>
            </div>
          ) : null}
        </div>
      ) : null}

      {selection && selection.paperId === metaPaperId && <PdfSelectionActions key={JSON.stringify(selection)} selection={selection} onClear={() => setSelection(null)} onInsert={onInsertSelection}
        onAnnotate={async body => {
          for (const anchor of selection.anchors) {
            if (annotations.some(ann => ann.page_number === anchor.page_number && ann.quote === selection.originalText && ann.body === body && JSON.stringify(ann.rects) === JSON.stringify(anchor.rects))) continue;
            await createAnnotation({ ...anchor, kind: "highlight", quote: selection.originalText, body });
          }
        }} />}
      {url && document ? (
        <div className="pdf-canvas-shell">
          <div className="pdf-canvas-wrap" ref={canvasWrapRef} onMouseUp={capturePdfSelection} onScroll={updateCurrentPageFromScroll}>
            {Array.from({ length: pageCount }, (_, index) => {
              const pageNumber = index + 1;
              return (
                <PdfPage
                  key={`${url}-${pageNumber}`}
                  activeSearchRects={activeSearchHit?.page === pageNumber ? activeSearchHit.rects : undefined}
                  searchTerm={searchTerm} searchKey={searchKey} onSearch={reportSearch}
                  selectionRects={selection && selection.paperId === metaPaperId ? selection.anchors.find(a => a.page_number === pageNumber)?.rects : undefined}
                  anchorRects={anchors?.find(a => a.page_number === pageNumber)?.rects}
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
              {onIngestMissing && metaPaperId ? (
                <button
                  type="button"
                  className="button"
                  disabled={ingestPending}
                  onClick={() => onIngestMissing(metaPaperId)}
                >
                  {ingestPending ? (
                    <>
                      <Loader2 size={14} className="spin" /> Lädt…
                    </>
                  ) : (
                    "PDF nachladen"
                  )}
                </button>
              ) : null}
              {ingestStatus ? (
                <p className={ingestPending ? "pdf-ingest-status pdf-ingest-status--pending" : "pdf-ingest-status"}>
                  {ingestStatus}
                </p>
              ) : null}
            </>
          ) : "Quelle wählen"}
        </div>
      )}

      {error ? <div className="inline-error">{error}</div> : null}
      {activeEvidence ? (
        <ResizableSection storageKey="sciencekg.pdf.excerpt" title="Aktive Textstelle" initialCollapsed={headerInPaneToolbar} openRequestKey={String(excerptRequest)}><div className="excerpt-panel" style={colorVarsForPaperId(activeEvidence?.paper_id, activeEvidenceColorIndex)}>
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
        </div></ResizableSection>
      ) : null}
    </aside>
  );
}

const EXCERPT_TRANSLATE_LANGUAGES = ["Deutsch", "Englisch", "Französisch", "Spanisch", "Italienisch", "Portugiesisch", "Niederländisch", "Polnisch", "Chinesisch", "Japanisch"];

// Stable empty reference so pages without annotations don't re-render on every parent update.
const EMPTY_ANNOTATIONS: PdfAnnotation[] = [];

function PdfPage({
  searchTerm, searchKey, onSearch, selectionRects, anchorRects, activeSearchRects,
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
  searchTerm: string; searchKey: string;
  onSearch: (key: string, page: number, hits: PdfSearchHit[], hasText: boolean, error?: string) => void;
  selectionRects?: PdfAnnotationRect[]; anchorRects?: PdfAnnotationRect[]; activeSearchRects?: PdfAnnotationRect[];
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
  const { window } = usePaneEnvironment();
  const [pixelRatio, setPixelRatio] = useState(() => window.devicePixelRatio || 1);
  useEffect(() => {
    const update = () => setPixelRatio(window.devicePixelRatio || 1);
    update();
    const media = window.matchMedia(`(resolution: ${window.devicePixelRatio || 1}dppx)`);
    media.addEventListener("change", update);
    window.addEventListener("resize", update);
    return () => { media.removeEventListener("change", update); window.removeEventListener("resize", update); };
  }, [window, pixelRatio]);
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  // Serialize cancellation and redraw: pdf.js may still own this canvas until its task settles.
  const renderQueue = useRef<Promise<unknown>>(Promise.resolve());
  const textLayerRef = useRef<HTMLDivElement | null>(null);
  const surfaceRef = useRef<HTMLDivElement | null>(null);
  const pageRef = useRef<HTMLDivElement | null>(null);
  const layersRef = useRef(layers);
  const targetPagesRef = useRef(targetPages);
  layersRef.current = layers;
  targetPagesRef.current = targetPages;
  const [isNearViewport, setIsNearViewport] = useState(pageNumber <= 2);
  const [searchBoxes, setSearchBoxes] = useState<PdfAnnotationRect[]>([]);
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
    if (!node || typeof window.IntersectionObserver === "undefined") {
      setIsNearViewport(true);
      return;
    }
    const observer = new window.IntersectionObserver(
      ([entry]) => {
        setIsNearViewport(entry.isIntersecting);
      },
      { root: node.closest(".pdf-canvas-wrap"), rootMargin: "900px 0px" }
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, [window]);

  useEffect(() => {
    let cancelled = false;
    let textTask: { cancel: () => void } | null = null;
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
      canvas.width = Math.round(pxWidth * pixelRatio);
      canvas.height = Math.round(pxHeight * pixelRatio);
      canvas.style.width = `${pxWidth}px`;
      canvas.style.height = `${pxHeight}px`;
      setSize({ width: pxWidth, height: pxHeight });
      if (cancelled) {
        return;
      }
      if (isNearViewport) {
        const task = page.render({ canvasContext: context, viewport, transform: [pixelRatio, 0, 0, pixelRatio, 0, 0] });
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

      // Selectable text layer: pdf.js positions transparent spans over the canvas so
      // normal text selection/copy works in the PDF view.
      const textLayerNode = textLayerRef.current;
      if (textLayerNode) {
        textLayerNode.replaceChildren();
        {
          textLayerNode.style.setProperty("--scale-factor", String(viewport.scale));
          textLayerNode.style.setProperty("--total-scale-factor", String(viewport.scale));
          try {
            const TextLayerCtor = (pdfjs as unknown as { TextLayer?: new (options: Record<string, unknown>) => { render: () => Promise<void>; cancel: () => void; textDivs: HTMLElement[] } }).TextLayer;
            if (TextLayerCtor) {
              const textLayer = new TextLayerCtor({
                textContentSource: textContent,
                container: textLayerNode,
                viewport
              });
              textTask = textLayer;
              await textLayer.render();
              if (cancelled) return;
              const surface = surfaceRef.current!;
              const surfaceBox = surface.getBoundingClientRect();
              const divByItem = new Map<number, HTMLElement>();
              let textIndex = 0;
              textContent.items.forEach((item: any, index: number) => { if (typeof item.str === "string") divByItem.set(index, textLayer.textDivs[textIndex++]); });
              const matchingViewport = { ...viewport, textRangeRect: (item: number, start: number, end: number) => {
                const node = divByItem.get(item)?.firstChild;
                if (!node) return null;
                const range = window.document.createRange();
                range.setStart(node, start); range.setEnd(node, end);
                const rect = range.getBoundingClientRect();
                const scaleX = surface.clientWidth / Math.max(1, surfaceBox.width);
                const scaleY = surface.clientHeight / Math.max(1, surfaceBox.height);
                return rect.width && rect.height ? { left: (rect.left - surfaceBox.left) * scaleX, top: (rect.top - surfaceBox.top) * scaleY, width: rect.width * scaleX, height: rect.height * scaleY } : null;
              } };
              const collected: HighlightBox[] = [];
              for (const layer of layersRef.current) {
                const match = findPageMatch(textContent.items, layer.query, matchingViewport, layer.index, layer.colorIndex);
                onMatch(layer.index, pageNumber, layer.signature, match);
                if (match && (targetPagesRef.current[layer.index] === pageNumber || match.exact)) collected.push(...match.boxes);
              }
              setBoxes(collected);
              const items = textContent.items.filter((item: any) => typeof item.str === "string") as TextFragment[];
              const hits = findTextOccurrences(items, searchTerm).map(hit => ({ ...hit, page: pageNumber, rects: occurrenceRects(hit, textLayer.textDivs, surfaceRef.current!) }));
              setSearchBoxes(hits.flatMap(hit => hit.rects));
              onSearch(searchKey, pageNumber, hits, items.some(item => Boolean(item.str.trim())));
            }
          } catch (error) {
            if (!cancelled) onSearch(searchKey, pageNumber, [], true, String(error));
            // Selection layer is an enhancement — rendering continues without it.
          }
        }
      }
    }

    renderQueue.current = renderQueue.current.catch(() => {}).then(() => {
      if (!cancelled) return renderPage();
    }).catch(error => { if (!cancelled) onSearch(searchKey, pageNumber, [], true, String(error)); });
    return () => {
      cancelled = true;
      renderTask?.cancel?.();
      textTask?.cancel();
    };
  }, [document, pageNumber, containerWidth, zoom, fitMode, layersSignature, targetPagesSignature, onMatch, isNearViewport, searchTerm, searchKey, onSearch, pixelRatio, window]);

  return (
    <div className="pdf-page" data-page-number={pageNumber} ref={combinedPageRef} style={{ width: size.width || undefined }}>
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
        <div className="pdf-highlight-layer">
          {[...searchBoxes.map(rect => ({ rect, kind: "search" })), ...(activeSearchRects ?? []).map(rect => ({ rect, kind: "search-active" })), ...(selectionRects ?? []).map(rect => ({ rect, kind: "selection" })), ...(anchorRects ?? []).map(rect => ({ rect, kind: "anchor" }))].map(({ rect, kind }, i) => <span key={`${kind}-${i}`} className={`pdf-highlight pdf-highlight--${kind}`} style={{ left: `${rect.x * 100}%`, top: `${rect.y * 100}%`, width: `${rect.width * 100}%`, height: `${rect.height * 100}%` }} />)}
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
  const { window, document } = usePaneEnvironment();
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
    setDraft(null);
    setDraftBody("");
    setOpenId(null);
  }, [pointMode]);

  // Text selection → highlight note; plain click (point mode) → point note. Both anchor
  // to the page surface so the marker survives zoom (rects are normalized 0..1).
  useEffect(() => {
    const surface = surfaceRef.current;
    if (!surface) return;

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

    // Selection is owned by PdfPane, including cross-page ranges.
    surface.addEventListener("click", handleClick);
    surface.addEventListener("contextmenu", handleContextMenu);
    return () => {

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
  }, [window, menu]);

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
