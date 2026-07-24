import { useMemo, useState } from "react";
import { AlertTriangle, ChevronDown, Download, ExternalLink, X } from "lucide-react";

import { EmptyState } from "./EmptyState";
import { sourceLabel, sourceTier, TIER_LABEL } from "../harvestSources";
import { externalPaperUrl } from "../paperLinks";
import type { DiscoveryCandidate, Paper } from "../types";

export function paperKey(paper: Paper): string {
  return paper.id || `${paper.source}:${paper.source_id}`;
}

type PaperPickListProps = {
  title: string;
  papers: Paper[];
  /** Laedt die uebergebenen Paper; `withPdfs=false` speichert nur Metadaten. */
  onDownload: (papers: Paper[], withPdfs: boolean) => void;
  disabled?: boolean;
  /** Zusaetzlicher „Metadaten"-Knopf (Trefferliste — anderer Vorgang, keine Option). */
  showMetadataAction?: boolean;
  onClear?: () => void;
  emptyTitle?: string;
  defaultCollapsed?: boolean;
};

/** Kompakte, auswaehlbare Paper-Liste: eine Zeile pro Paper (Titel + Quelle),
 *  Details erst auf Klick. Ersetzt die frueheren Karten, die den kompletten
 *  Abstract gerendert und die Liste dadurch unlesbar lang gemacht haben. */
export function PaperPickList({
  title,
  papers,
  onDownload,
  disabled = false,
  showMetadataAction = false,
  onClear,
  emptyTitle = "Keine Treffer",
  defaultCollapsed = false
}: PaperPickListProps) {
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [expanded, setExpanded] = useState<Set<string>>(new Set());
  const [collapsed, setCollapsed] = useState(defaultCollapsed);

  const keys = useMemo(() => papers.map((paper) => paperKey(paper)), [papers]);
  const selectedCount = keys.filter((key) => selected.has(key)).length;
  const allSelected = keys.length > 0 && selectedCount === keys.length;

  function toggleOne(key: string) {
    setSelected((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleExpanded(key: string) {
    setExpanded((current) => {
      const next = new Set(current);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  }

  function toggleAll() {
    setSelected(allSelected ? new Set() : new Set(keys));
  }

  // Ohne Auswahl gelten die Aktionen fuer die ganze Liste — sonst muesste man
  // fuer den haeufigsten Fall („alles laden") erst „Alle" klicken.
  const targets = selectedCount ? papers.filter((paper) => selected.has(paperKey(paper))) : papers;
  const actionLabel = selectedCount ? `${selectedCount} laden` : "Alle laden";

  return (
    <div className="pick-list">
      <div className="panel-heading pick-list-heading">
        <button
          type="button"
          className="pick-list-toggle"
          onClick={() => setCollapsed((value) => !value)}
          aria-expanded={!collapsed}
          title={collapsed ? "Aufklappen" : "Einklappen"}
        >
          <ChevronDown size={14} className={collapsed ? "topic-group-chevron--collapsed" : "topic-group-chevron"} />
          <strong>{title}</strong>
          <span className="muted">{papers.length}</span>
        </button>
        <div className="button-row">
          {!collapsed && papers.length ? (
            <>
              <button className="button" type="button" onClick={toggleAll}>
                {allSelected ? "Keine" : "Alle"}
              </button>
              {targets.length > 50 ? (
                <span className="muted pick-list-warning">
                  <AlertTriangle size={12} /> &gt;50 Paper = mehrere Min.
                </span>
              ) : null}
              {showMetadataAction ? (
                <button
                  className="button"
                  type="button"
                  disabled={disabled || !targets.length}
                  onClick={() => onDownload(targets, false)}
                  title="Nur Metadaten speichern, ohne PDF-Download"
                >
                  <Download size={15} />
                  <span>Metadaten</span>
                </button>
              ) : null}
              <button
                className="button button-primary"
                type="button"
                disabled={disabled || !targets.length}
                onClick={() => onDownload(targets, true)}
              >
                <Download size={15} />
                <span>{actionLabel}</span>
              </button>
            </>
          ) : null}
          {onClear ? (
            <button className="button" type="button" title="Liste leeren" aria-label="Liste leeren" onClick={onClear}>
              <X size={14} />
            </button>
          ) : null}
        </div>
      </div>

      {collapsed ? null : papers.length ? (
        <div className="pick-rows">
          {papers.map((paper) => {
            const key = paperKey(paper);
            const isOpen = expanded.has(key);
            const tier = sourceTier(paper.source);
            const reason = (paper as DiscoveryCandidate).discovery_reason;
            const external = externalPaperUrl(paper);
            return (
              <article key={key} className={`pick-item ${selected.has(key) ? "pick-item--selected" : ""}`}>
                <div className="pick-row">
                  <input
                    type="checkbox"
                    checked={selected.has(key)}
                    onChange={() => toggleOne(key)}
                    aria-label={`${paper.title || key} auswählen`}
                  />
                  <button
                    type="button"
                    className="pick-row-main"
                    onClick={() => toggleExpanded(key)}
                    aria-expanded={isOpen}
                  >
                    <span className="pick-row-title">{paper.title || paper.id}</span>
                    <span className="source-badge" data-tier={tier} title={TIER_LABEL[tier]}>
                      {sourceLabel(paper.source)}
                    </span>
                    <span className="pick-row-year">{paper.year ?? "—"}</span>
                    {paper.has_full_text ? (
                      <span className="pick-row-pdf" title="Freier Volltext verfügbar">
                        PDF
                      </span>
                    ) : (
                      <span className="pick-row-pdf pick-row-pdf--none" title="Kein freier Volltext — Abstract wird trotzdem gespeichert">
                        —
                      </span>
                    )}
                    <ChevronDown size={14} className={isOpen ? "topic-group-chevron" : "topic-group-chevron--collapsed"} />
                  </button>
                </div>
                {isOpen ? (
                  <div className="pick-row-detail">
                    {paper.authors?.length ? <p className="muted pick-row-authors">{paper.authors.join(", ")}</p> : null}
                    {reason ? <p className="pick-row-reason">{reason}</p> : null}
                    <p className="pick-row-abstract">
                      {paper.abstract || "Kein Abstract in den Metadaten der Quelle."}
                    </p>
                    <div className="pick-row-links">
                      {paper.doi ? (
                        <span className="muted">DOI: {String(paper.doi).replace("https://doi.org/", "")}</span>
                      ) : null}
                      {external ? (
                        <a href={external} target="_blank" rel="noreferrer">
                          <ExternalLink size={13} /> Original öffnen
                        </a>
                      ) : null}
                    </div>
                  </div>
                ) : null}
              </article>
            );
          })}
        </div>
      ) : (
        <EmptyState title={emptyTitle} />
      )}
    </div>
  );
}
