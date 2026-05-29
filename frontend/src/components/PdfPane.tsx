import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import * as pdfjs from "pdfjs-dist";
import pdfWorker from "pdfjs-dist/build/pdf.worker.mjs?url";
import { ChevronLeft, ChevronRight, Maximize2, PanelRightClose, Search, X, ZoomIn, ZoomOut } from "lucide-react";

import { evidenceColorVars } from "../citationColors";
import type { VerificationEvidence } from "../types";

pdfjs.GlobalWorkerOptions.workerSrc = pdfWorker;

type PdfDocument = {
  numPages: number;
  getPage: (pageNumber: number) => Promise<any>;
  destroy?: () => Promise<void>;
};

type HighlightBox = {
  id: string;
  evidenceIndex: number;
  left: number;
  top: number;
  width: number;
  height: number;
};

type MatchIndex = Record<number, number[]>;

type HighlightQuery = {
  phrases: string[];
  terms: string[];
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
  evidences?: VerificationEvidence[];
  activeEvidenceIndex?: number;
  onActiveEvidenceChange?: (index: number) => void;
  onCollapse?: () => void;
};

export function PdfPane({
  url,
  title,
  evidences = [],
  activeEvidenceIndex = 0,
  onActiveEvidenceChange,
  onCollapse
}: PdfPaneProps) {
  const [document, setDocument] = useState<PdfDocument | null>(null);
  const [pageCount, setPageCount] = useState<number>(0);
  const [error, setError] = useState<string>("");
  const [matches, setMatches] = useState<MatchIndex>({});
  const [currentPage, setCurrentPage] = useState(1);
  const [viewportWidth, setViewportWidth] = useState(720);
  const [zoom, setZoom] = useState(1);
  const [fitMode, setFitMode] = useState<"width" | "page">("width");
  const [searchTerm, setSearchTerm] = useState("");
  const canvasWrapRef = useRef<HTMLDivElement | null>(null);
  const pageRefs = useRef<Record<number, HTMLDivElement | null>>({});

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

  const evidenceQueries = useMemo(() => evidences.map(buildHighlightQuery), [evidences]);
  const searchQuery = useMemo(() => buildSearchQuery(searchTerm), [searchTerm]);
  const showingSearch = Boolean(searchTerm.trim());
  const visibleHighlightIndex = showingSearch ? -1 : activeEvidenceIndex;
  const activeEvidence = evidences[activeEvidenceIndex];
  const activePages = matches[visibleHighlightIndex] ?? [];
  const evidencePages = matches[activeEvidenceIndex] ?? [];
  const activeQuery = evidenceQueries[activeEvidenceIndex] ?? { phrases: [], terms: [] };
  const visibleQuery = showingSearch ? searchQuery : activeQuery;

  useEffect(() => {
    setMatches({});
  }, [evidenceQueries, searchTerm]);

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
    const observer = new ResizeObserver(updateWidth);
    observer.observe(node);
    return () => observer.disconnect();
  }, [document, url]);

  useEffect(() => {
    const firstPage = activePages[0];
    if (firstPage) {
      jumpToPage(firstPage, "center");
    }
  }, [activeEvidenceIndex, activePages.join(","), showingSearch]);

  const updateMatch = useCallback((evidenceIndex: number, pageNumber: number, hasMatch: boolean) => {
    setMatches((current) => {
      const existing = new Set(current[evidenceIndex] ?? []);
      hasMatch ? existing.add(pageNumber) : existing.delete(pageNumber);
      const next = { ...current, [evidenceIndex]: Array.from(existing).sort((a, b) => a - b) };
      if (!next[evidenceIndex].length) {
        delete next[evidenceIndex];
      }
      return next;
    });
  }, []);

  function jumpToEvidence(index: number) {
    onActiveEvidenceChange?.(index);
    const page = matches[index]?.[0];
    if (page) {
      jumpToPage(page, "center");
    }
  }

  function stepEvidence(direction: -1 | 1) {
    if (!evidences.length) {
      return;
    }
    const next = (activeEvidenceIndex + direction + evidences.length) % evidences.length;
    jumpToEvidence(next);
  }

  function jumpToPage(pageNumber: number, block: ScrollLogicalPosition = "start") {
    if (!pageCount) {
      return;
    }
    const page = Math.min(pageCount, Math.max(1, pageNumber));
    setCurrentPage(page);
    pageRefs.current[page]?.scrollIntoView({ block, behavior: "smooth" });
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
        <div className="pdf-evidence-nav" style={evidenceColorVars(activeEvidenceIndex)}>
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
          <span>{evidencePages.length ? `Seite ${evidencePages.join(", ")}` : "keine Textstelle gefunden"}</span>
        </div>
      ) : null}

      {pageCount ? (
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
      ) : null}

      {pageCount ? (
        <div className="pdf-search-row">
          <Search size={17} />
          <input value={searchTerm} onChange={(event) => setSearchTerm(event.target.value)} placeholder="In PDF suchen" />
          <button className={`icon-button ${searchTerm ? "" : "pdf-search-clear--hidden"}`} type="button" aria-label="Suche leeren" onClick={() => setSearchTerm("")} disabled={!searchTerm}>
            <X size={17} />
          </button>
          <span>{showingSearch ? (activePages.length ? `Treffer auf Seite ${activePages.join(", ")}` : "keine Treffer") : ""}</span>
        </div>
      ) : null}

      {pageCount ? (
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
                evidenceQuery={visibleQuery}
                activeEvidenceIndex={visibleHighlightIndex}
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
        <div className="pdf-placeholder">Quelle wählen</div>
      )}

      {error ? <div className="inline-error">{error}</div> : null}
      {activeEvidence ? (
        <div className="excerpt-panel" style={evidenceColorVars(activeEvidenceIndex)}>
          <span>Aktive Textstelle</span>
          <p>{highlightTerms(activeEvidence.pdf_excerpt || activeEvidence.reference_text, activeQuery.terms)}</p>
        </div>
      ) : null}
    </aside>
  );
}

function PdfPage({
  document,
  pageNumber,
  containerWidth,
  zoom,
  fitMode,
  evidenceQuery,
  activeEvidenceIndex,
  onMatch,
  setPageRef
}: {
  document: PdfDocument;
  pageNumber: number;
  containerWidth: number;
  zoom: number;
  fitMode: "width" | "page";
  evidenceQuery: HighlightQuery;
  activeEvidenceIndex: number;
  onMatch: (evidenceIndex: number, pageNumber: number, hasMatch: boolean) => void;
  setPageRef: (node: HTMLDivElement | null) => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const [boxes, setBoxes] = useState<HighlightBox[]>([]);
  const [size, setSize] = useState({ width: 0, height: 0 });

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

      const textContent = await page.getTextContent();
      if (cancelled) {
        return;
      }

      const match = findPageMatch(textContent.items, evidenceQuery, viewport, activeEvidenceIndex);
      onMatch(activeEvidenceIndex, pageNumber, match.hasMatch);
      setBoxes(match.boxes);
    }

    renderPage();
    return () => {
      cancelled = true;
      renderTask?.cancel?.();
    };
  }, [document, pageNumber, containerWidth, zoom, fitMode, evidenceQuery, activeEvidenceIndex, onMatch]);

  return (
    <div className="pdf-page" ref={setPageRef} style={{ width: size.width || undefined }}>
      <div className="pdf-page-label">Seite {pageNumber}</div>
      <div className="pdf-page-surface" style={{ width: size.width || undefined, height: size.height || undefined }}>
        <canvas ref={canvasRef} />
        <div className="pdf-highlight-layer">
          {boxes.map((box) => (
            <span
              key={box.id}
              className={`pdf-highlight ${box.evidenceIndex === activeEvidenceIndex ? "pdf-highlight--active" : ""}`}
              style={{ left: box.left, top: box.top, width: box.width, height: box.height, ...evidenceColorVars(box.evidenceIndex) }}
              aria-hidden="true"
            />
          ))}
        </div>
      </div>
    </div>
  );
}

function textItemBox(item: unknown, viewport: any, itemIndex: number, evidenceIndex: number): HighlightBox | null {
  const textItem = item as { transform?: number[]; width?: number; height?: number; str?: string };
  if (!textItem.transform) {
    return null;
  }
  const transform = pdfjs.Util.transform(viewport.transform, textItem.transform);
  const height = Math.max(8, Math.hypot(transform[2], transform[3]) || Number(textItem.height) || 10);
  const width = Math.max(10, Number(textItem.width || String(textItem.str ?? "").length * 5) * viewport.scale);
  const left = transform[4];
  const top = transform[5] - height;
  return {
    id: `${evidenceIndex}-${itemIndex}-${left}-${top}`,
    evidenceIndex,
    left: Math.max(0, left - 1),
    top: Math.max(0, top - 2),
    width: width + 3,
    height: height + 4
  };
}

function findPageMatch(textItems: unknown[], query: HighlightQuery, viewport: any, evidenceIndex: number) {
  const indexed = indexTextItems(textItems);
  if (!indexed.text || (!query.phrases.length && !query.terms.length)) {
    return { hasMatch: false, boxes: [] as HighlightBox[] };
  }

  const phraseBoxes = boxesForPhraseMatches(indexed.items, indexed.text, query.phrases, viewport, evidenceIndex);
  if (phraseBoxes.length) {
    const supplementalTerms = query.terms.filter((term) => indexed.text.includes(term)).filter(isStrongFallbackTerm).slice(0, 4);
    const supplementalBoxes = boxesForTerms(indexed.items, supplementalTerms, viewport, evidenceIndex, Math.max(0, 30 - phraseBoxes.length));
    return { hasMatch: true, boxes: uniqueBoxes([...phraseBoxes, ...supplementalBoxes]).slice(0, 34) };
  }

  const matchedTerms = query.terms.filter((term) => indexed.text.includes(term));
  const strongTerms = matchedTerms.filter(isStrongFallbackTerm);
  const requiredHits = query.terms.length <= 4 ? query.terms.length : Math.min(6, Math.max(4, Math.ceil(query.terms.length * 0.45)));
  if (!matchedTerms.length || matchedTerms.length < requiredHits) {
    return { hasMatch: false, boxes: [] as HighlightBox[] };
  }
  if (query.terms.length < 2 && !strongTerms.length) {
    return { hasMatch: false, boxes: [] as HighlightBox[] };
  }

  const anchors = strongTerms.slice(0, 5);
  const fallbackTerms = anchors.length ? anchors : matchedTerms.slice(0, 3);
  return { hasMatch: true, boxes: boxesForTerms(indexed.items, fallbackTerms, viewport, evidenceIndex, 14) };
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

function boxesForPhraseMatches(
  items: IndexedTextItem[],
  pageText: string,
  phrases: string[],
  viewport: any,
  evidenceIndex: number
): HighlightBox[] {
  const boxes: HighlightBox[] = [];
  for (const phrase of phrases) {
    const normalizedPhrase = normalizeText(phrase);
    if (!normalizedPhrase) {
      continue;
    }
    let position = pageText.indexOf(normalizedPhrase);
    while (position >= 0) {
      const end = position + normalizedPhrase.length;
      for (const item of items) {
        if (item.end < position || item.start > end) {
          continue;
        }
        const box = textItemBox(item.item, viewport, item.index, evidenceIndex);
        if (box) {
          boxes.push(box);
        }
        if (boxes.length >= 20) {
          return uniqueBoxes(boxes);
        }
      }
      position = pageText.indexOf(normalizedPhrase, end);
    }
  }
  return uniqueBoxes(boxes);
}

function boxesForTerms(
  items: IndexedTextItem[],
  terms: string[],
  viewport: any,
  evidenceIndex: number,
  limit: number
): HighlightBox[] {
  if (!terms.length || limit <= 0) {
    return [];
  }
  const boxes: HighlightBox[] = [];
  for (const item of items) {
    if (!terms.some((term) => item.text.includes(term))) {
      continue;
    }
    const box = textItemBox(item.item, viewport, item.index, evidenceIndex);
    if (box) {
      boxes.push(box);
    }
    if (boxes.length >= limit) {
      break;
    }
  }
  return uniqueBoxes(boxes);
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

function buildHighlightQuery(evidence: VerificationEvidence): HighlightQuery {
  const explicit = (evidence.matched_terms ?? []).map(normalizeText).filter(Boolean);
  const reference = compactText(evidence.reference_text);
  const excerpt = compactText(evidence.pdf_excerpt);
  const referenceTerms = extractTerms(reference);
  const phrases = extractPhrases(reference, 170).slice(0, 4);
  const excerptPhrases = extractPhrases(excerpt)
    .filter((phrase) => phrase.length <= 190)
    .slice(0, 4);
  const namedPhrases = extractNamedPhrases(`${reference} ${excerpt}`).slice(0, 4);
  const terms = extractTerms(`${explicit.join(" ")} ${reference} ${namedPhrases.join(" ")}`).filter(isAnchorTerm);
  return {
    phrases: Array.from(new Set([...phrases, ...excerptPhrases, ...namedPhrases])).slice(0, 10),
    terms: Array.from(new Set([...explicit, ...referenceTerms, ...terms])).filter(isAnchorTerm).slice(0, 18)
  };
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

function extractNamedPhrases(text: string): string[] {
  const matches = text.match(/\b[A-Z][A-Za-z.'-]+(?:\s+[A-Z][A-Za-z.'-]+){1,5}\b/g) ?? [];
  const blocked = new Set(["Artificial Intelligence", "Clinical Decision", "Primary Care"]);
  return Array.from(new Set(matches.map((item) => compactText(item))))
    .filter((item) => !blocked.has(item) && item.length <= 120)
    .sort((left, right) => right.split(" ").length - left.split(" ").length);
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

function highlightTerms(text: string, terms: string[]) {
  if (!terms.length) {
    return text;
  }
  const visibleTerms = terms.filter((term) => term.length >= 4).slice(0, 12);
  if (!visibleTerms.length) {
    return text;
  }
  const pattern = new RegExp(`(${visibleTerms.map(escapeRegExp).join("|")})`, "ig");
  return text.split(pattern).map((part, index) =>
    visibleTerms.some((term) => term.toLowerCase() === part.toLowerCase()) ? <mark key={`${part}-${index}`}>{part}</mark> : part
  );
}

function escapeRegExp(value: string) {
  return value.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
}

function shortLabel(value: string) {
  return value.length > 72 ? `${value.slice(0, 69)}...` : value;
}
