import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";
import { ExternalLink, Globe, Monitor, PanelRightClose, Quote } from "lucide-react";

import { evidenceColorVars } from "../citationColors";
import type { GreySource } from "../types";

type GreySourceViewProps = {
  source: GreySource;
  onCollapse?: () => void;
  onInsert?: (text: string) => void;
  onInsertPreview?: (text: string) => void;
  onInsertPreviewClear?: () => void;
};

/**
 * Renders a saved grey (web) source inside the PDF pane area: the stored full
 * article text with evidence highlights showing where the facts came from, a link
 * to the live website, and selection/evidence insertion into the active note.
 *
 * Grey sources are untrusted web data and are deliberately kept out of the KG; the
 * viewer therefore surfaces any prompt-injection flags and never executes content.
 */
export function GreySourceView({ source, onCollapse, onInsert, onInsertPreview, onInsertPreviewClear }: GreySourceViewProps) {
  const bodyRef = useRef<HTMLDivElement | null>(null);
  const [selection, setSelection] = useState("");
  const [viewMode, setViewMode] = useState<"website" | "fulltext">("fulltext");
  const evidence = useMemo(() => (source.evidence ?? []).filter((quote) => quote && quote.trim()), [source.evidence]);
  const fullText = source.full_text || source.raw_excerpt || source.summary || "";
  const segments = useMemo(() => highlightSegments(fullText, evidence), [fullText, evidence]);

  useEffect(() => {
    setViewMode("fulltext");
    setSelection("");
  }, [source.id]);

  function captureSelection() {
    const text = window.getSelection()?.toString().trim() ?? "";
    // Only keep selections that are inside this viewer.
    if (text && bodyRef.current && selectionWithin(bodyRef.current)) {
      setSelection(text);
    } else if (!text) {
      setSelection("");
    }
  }

  return (
    <section className="grey-view">
      <div className="grey-view-heading">
        <div className="grey-view-title">
          <span className="grey-badge">Graue Quelle</span>
          <strong title={source.title || source.url}>{source.title || source.url}</strong>
        </div>
        <div className="button-row">
          <button
            className={`icon-button ${viewMode === "website" ? "icon-button--active" : ""}`}
            type="button"
            title="Website-Ansicht"
            aria-label="Website anzeigen"
            onClick={() => setViewMode("website")}
          >
            <Monitor size={16} />
          </button>
          <button
            className={`icon-button ${viewMode === "fulltext" ? "icon-button--active" : ""}`}
            type="button"
            title="Volltext-Ansicht"
            aria-label="Volltext anzeigen"
            onClick={() => setViewMode("fulltext")}
          >
            <Globe size={16} />
          </button>
          <a className="icon-button" href={source.url} target="_blank" rel="noreferrer" aria-label="Website oeffnen" title="Website in neuem Tab oeffnen">
            <ExternalLink size={16} />
          </a>
          {onCollapse ? (
            <button className="icon-button" type="button" aria-label="Quelle einklappen" onClick={onCollapse}>
              <PanelRightClose size={17} />
            </button>
          ) : null}
        </div>
      </div>

      <a className="grey-view-url" href={source.url} target="_blank" rel="noreferrer">
        <Globe size={13} />
        <span>{source.url}</span>
      </a>

      {source.injection_flags?.length ? (
        <div className="warning-row">Prompt-Injection-Flags ignoriert: {source.injection_flags.join(", ")}</div>
      ) : null}

      {viewMode === "website" ? (
        <div className="grey-view-iframe-wrap">
          <div className="grey-view-iframe-hint">
            Falls die Seite leer erscheint, blockiert der Anbieter iframe-Einbettungen &mdash; wechsle zum Volltext-Tab.
          </div>
          <iframe
            key={source.url}
            src={source.url}
            title={source.title || source.url}
            className="grey-view-iframe"
            sandbox="allow-scripts allow-same-origin allow-forms allow-popups allow-pointer-lock"
          />
        </div>
      ) : (
        <>
          {source.summary ? (
            <div className="grey-view-summary">
              <span className="grey-view-label">Zusammenfassung</span>
              <p>{source.summary}</p>
            </div>
          ) : null}

          {evidence.length ? (
            <div className="grey-view-evidence">
              <span className="grey-view-label">Belegstellen ({evidence.length})</span>
              {evidence.map((quote, index) => (
                <div className="grey-view-evidence-row" key={index} style={evidenceColorVars(index)}>
                  <span className="citation-badge">Z{index + 1}</span>
                  <blockquote>{quote}</blockquote>
                  {onInsert ? (
                    <button
                      className="button button-compact button-ghost"
                      type="button"
                      onClick={() => onInsert(quote)}
                      onMouseEnter={() => onInsertPreview?.(quote)}
                      onMouseLeave={() => onInsertPreviewClear?.()}
                      onFocus={() => onInsertPreview?.(quote)}
                      onBlur={() => onInsertPreviewClear?.()}
                    >
                      <Quote size={13} />
                      <span>Einf&uuml;gen</span>
                    </button>
                  ) : null}
                </div>
              ))}
            </div>
          ) : null}

          {onInsert ? (
            <div className="grey-view-toolbar">
              <span className="muted">{selection ? `${selection.length} Zeichen markiert` : "Text im Volltext markieren, um ihn zu zitieren"}</span>
              <button
                className="button button-compact"
                type="button"
                disabled={!selection}
                onClick={() => {
                  onInsert(selection);
                  onInsertPreviewClear?.();
                }}
                onMouseEnter={() => selection && onInsertPreview?.(selection)}
                onMouseLeave={() => onInsertPreviewClear?.()}
              >
                <Quote size={14} />
                <span>Auswahl in Notiz</span>
              </button>
            </div>
          ) : null}

          <div className="grey-view-label grey-view-body-label">Volltext ({fullText.length.toLocaleString("de-DE")} Zeichen)</div>
          <div className="grey-view-body" ref={bodyRef} onMouseUp={captureSelection} onKeyUp={captureSelection}>
            {fullText ? segments : <p className="muted">Kein gespeicherter Volltext.</p>}
          </div>
        </>
      )}
    </section>
  );
}

function selectionWithin(container: HTMLElement) {
  const sel = window.getSelection();
  if (!sel || sel.rangeCount === 0) {
    return false;
  }
  const node = sel.getRangeAt(0).commonAncestorContainer;
  return container.contains(node.nodeType === Node.TEXT_NODE ? node.parentNode : node);
}

/** Build text nodes with evidence quotes wrapped in colored mark elements. */
function highlightSegments(text: string, evidence: string[]): ReactNode[] {
  if (!text) {
    return [];
  }
  const ranges: { start: number; end: number; colorIndex: number }[] = [];
  const lower = text.toLowerCase();
  evidence.forEach((quote, colorIndex) => {
    const needle = quote.trim().toLowerCase();
    if (needle.length < 12) {
      return;
    }
    const at = lower.indexOf(needle);
    if (at >= 0) {
      ranges.push({ start: at, end: at + needle.length, colorIndex });
    }
  });
  if (!ranges.length) {
    return [<span key="all">{text}</span>];
  }
  ranges.sort((a, b) => a.start - b.start);
  const nodes: ReactNode[] = [];
  let cursor = 0;
  ranges.forEach((range, index) => {
    if (range.start < cursor) {
      return; // skip overlapping match
    }
    if (range.start > cursor) {
      nodes.push(<span key={`t${index}`}>{text.slice(cursor, range.start)}</span>);
    }
    nodes.push(
      <mark key={`m${index}`} className="grey-view-mark" style={evidenceColorVars(range.colorIndex)}>
        {text.slice(range.start, range.end)}
      </mark>
    );
    cursor = range.end;
  });
  if (cursor < text.length) {
    nodes.push(<span key="tail">{text.slice(cursor)}</span>);
  }
  return nodes;
}
