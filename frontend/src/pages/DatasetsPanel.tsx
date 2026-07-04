import { useCallback, useEffect, useState } from "react";
import { ChevronDown, ChevronRight, Database, Download, PanelRightClose, Search, Plus, Trash2, ExternalLink, RefreshCw } from "lucide-react";

import { api } from "../api";
import type { Dataset, DatasetDetails, DatasetHit, DatasetSource } from "../types";

/**
 * Datensatz-Panel (WP2).
 *
 * Sucht freie Forschungs-Datensatz-Registries (Zenodo/Figshare/Dryad/
 * ClinicalTrials/PWC), sammelt Referenzen ins Projekt und zeigt sie mit Quelle,
 * DOI und Lizenz — jeder Treffer verlinkt die Original-Landingpage, damit der
 * Nutzer die Rohdaten/Lizenz selbst prüfen kann. Gesammelte Datensätze lassen
 * sich in der Analyse-Werkstatt als Kontext nutzen.
 */

type Props = {
  projectId: string;
  onCollapse: () => void;
};

function sourceLabel(sources: DatasetSource[], id: string): string {
  return sources.find((s) => s.id === id)?.label ?? id;
}

/**
 * Einklappbare Detail-Ansicht: lädt beim ersten Aufklappen die Datei-Liste,
 * Beschreibung und Download-Links direkt aus der Registry (nur Metadaten;
 * der Download selbst läuft beim Anbieter).
 */
function DatasetDetailsBlock({ source, externalId }: { source: string; externalId: string }) {
  const [open, setOpen] = useState(false);
  const [details, setDetails] = useState<DatasetDetails | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const toggle = useCallback(() => {
    setOpen((current) => {
      const next = !current;
      if (next && !details && !loading) {
        setLoading(true);
        api.datasets
          .details(source, externalId)
          .then((res) => setDetails(res))
          .catch((e) => setError(e instanceof Error ? e.message : "Details nicht abrufbar."))
          .finally(() => setLoading(false));
      }
      return next;
    });
  }, [details, loading, source, externalId]);

  return (
    <div className="dataset-details">
      <button type="button" className="dataset-details-toggle" onClick={toggle}>
        {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        Details & Dateien
      </button>
      {open ? (
        <div className="dataset-details-body">
          {loading ? <p className="analysis-muted">Lade Details …</p> : null}
          {error ? <p className="analysis-error">{error}</p> : null}
          {details?.warning ? <p className="analysis-muted">{details.warning}</p> : null}
          {details?.license ? <p className="analysis-muted">Lizenz: {details.license}</p> : null}
          {details?.description ? (
            <p className="dataset-details-desc">{details.description.replace(/<[^>]+>/g, " ").slice(0, 1200)}</p>
          ) : null}
          {details?.files.length ? (
            <ul className="dataset-file-list">
              {details.files.map((file, index) => (
                <li key={`${file.name}-${index}`}>
                  <span className="dataset-file-name">{file.name}</span>
                  {file.size ? <span className="analysis-muted">{file.size}</span> : null}
                  {file.download_url ? (
                    <a href={file.download_url} target="_blank" rel="noreferrer" className="analysis-inline-link" title="Bei der Registry herunterladen">
                      <Download size={12} /> Download
                    </a>
                  ) : null}
                </li>
              ))}
            </ul>
          ) : null}
          {details && !details.files.length && details.download_url ? (
            <a href={details.download_url} target="_blank" rel="noreferrer" className="analysis-inline-link">
              <Download size={12} /> Kompletten Datensatz herunterladen
            </a>
          ) : null}
          {details && !details.files.length && !details.download_url && !loading && !details.warning ? (
            <p className="analysis-muted">Keine Datei-Liste verfügbar — Landingpage nutzen.</p>
          ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function DatasetsPanel({ projectId, onCollapse }: Props) {
  const [sources, setSources] = useState<DatasetSource[]>([]);
  const [selectedSources, setSelectedSources] = useState<string[]>([]);
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState<DatasetHit[]>([]);
  const [warnings, setWarnings] = useState<string[]>([]);
  const [saved, setSaved] = useState<Dataset[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const scopedProjectId = projectId || undefined;

  useEffect(() => {
    api.datasets
      .sources()
      .then((res) => {
        setSources(res.sources);
        setSelectedSources(res.default);
      })
      .catch(() => setError("Quellen konnten nicht geladen werden."));
  }, []);

  const refreshSaved = useCallback(async () => {
    try {
      const res = await api.datasets.list(scopedProjectId ?? null);
      setSaved(res.datasets);
    } catch {
      /* ignore */
    }
  }, [scopedProjectId]);

  useEffect(() => {
    void refreshSaved();
  }, [refreshSaved]);

  const runSearch = useCallback(async () => {
    const q = query.trim();
    if (!q || busy) return;
    setBusy(true);
    setError(null);
    setWarnings([]);
    try {
      const res = await api.datasets.search({ query: q, sources: selectedSources });
      setHits(res.results);
      setWarnings(res.warnings);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Suche fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }, [query, busy, selectedSources]);

  const importOne = useCallback(
    async (hit: DatasetHit) => {
      try {
        await api.datasets.import([hit], scopedProjectId ?? null);
        await refreshSaved();
      } catch (e) {
        setError(e instanceof Error ? e.message : "Import fehlgeschlagen.");
      }
    },
    [scopedProjectId, refreshSaved]
  );

  const removeOne = useCallback(
    async (id: string) => {
      try {
        await api.datasets.remove(id);
        await refreshSaved();
      } catch {
        /* ignore */
      }
    },
    [refreshSaved]
  );

  const toggleSource = (id: string) =>
    setSelectedSources((prev) => (prev.includes(id) ? prev.filter((s) => s !== id) : [...prev, id]));

  const savedIds = new Set(saved.map((d) => `${d.source}:${d.external_id}`));

  return (
    <section className="workspace-pane analysis-pane">
      <div className="pane-heading workspace-pane-heading">
        <div>
          <span className="pane-eyebrow">Werkstatt</span>
          <h2>
            <Database size={16} /> Datensätze
          </h2>
        </div>
        <button type="button" className="icon-button" title="Schließen" onClick={onCollapse}>
          <PanelRightClose size={17} />
        </button>
      </div>

      <div className="analysis-body">
        <div className="analysis-composer">
          <div className="dataset-search-row">
            <input
              type="text"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Datensätze suchen, z.B. „EEG seizure detection oder „diabetes cohort"
              onKeyDown={(e) => {
                if (e.key === "Enter") void runSearch();
              }}
            />
            <button type="button" className="button button-primary" onClick={() => void runSearch()} disabled={busy || !query.trim()}>
              {busy ? <RefreshCw size={15} className="spin" /> : <Search size={15} />}
              Suchen
            </button>
          </div>
          <div className="dataset-sources">
            {sources.map((s) => (
              <label key={s.id} className="dataset-source-chip" title={s.domain}>
                <input type="checkbox" checked={selectedSources.includes(s.id)} onChange={() => toggleSource(s.id)} />
                {s.label}
              </label>
            ))}
          </div>
          <p className="analysis-hint">
            Nur Metadaten + Link/DOI/Lizenz werden gespeichert (keine Massendaten-Downloads).
            Gesammelte Datensätze lassen sich in der Analyse als Kontext nutzen.
          </p>
        </div>

        {error ? <p className="analysis-error">{error}</p> : null}
        {warnings.length ? (
          <p className="analysis-hint">Nicht erreichbar: {warnings.join(", ")}</p>
        ) : null}

        {hits.length ? (
          <div className="dataset-list">
            <h3 className="dataset-section-title">Treffer ({hits.length})</h3>
            {hits.map((hit) => {
              const already = savedIds.has(`${hit.source}:${hit.external_id}`);
              return (
                <article className="dataset-card" key={`${hit.source}:${hit.external_id}`}>
                  <div className="dataset-card-main">
                    <div className="dataset-card-head">
                      <span className="dataset-source-tag">{sourceLabel(sources, hit.source)}</span>
                      {hit.year ? <span className="analysis-muted">{hit.year}</span> : null}
                      {hit.license ? <span className="dataset-license">{hit.license}</span> : null}
                    </div>
                    <p className="dataset-title">{hit.title || hit.external_id}</p>
                    {hit.description ? <p className="dataset-desc">{hit.description}</p> : null}
                    {hit.url ? (
                      <a href={hit.url} target="_blank" rel="noreferrer" className="analysis-inline-link">
                        <ExternalLink size={12} /> {hit.doi || "Quelle öffnen"}
                      </a>
                    ) : null}
                    <DatasetDetailsBlock source={hit.source} externalId={hit.external_id} />
                  </div>
                  <button
                    type="button"
                    className="icon-button"
                    title={already ? "bereits gespeichert" : "Ins Projekt übernehmen"}
                    onClick={() => void importOne(hit)}
                    disabled={already}
                  >
                    <Plus size={16} />
                  </button>
                </article>
              );
            })}
          </div>
        ) : null}

        <div className="dataset-list">
          <div className="dataset-section-head">
            <h3 className="dataset-section-title">Gesammelt ({saved.length})</h3>
            <button type="button" className="icon-button" title="Aktualisieren" onClick={() => void refreshSaved()}>
              <RefreshCw size={13} />
            </button>
          </div>
          {saved.length === 0 ? <p className="analysis-muted">Noch keine Datensätze gesammelt.</p> : null}
          {saved.map((ds) => (
            <article className="dataset-card" key={ds.id}>
              <div className="dataset-card-main">
                <div className="dataset-card-head">
                  <span className="dataset-source-tag">{sourceLabel(sources, ds.source)}</span>
                  {ds.year ? <span className="analysis-muted">{ds.year}</span> : null}
                  {ds.license ? <span className="dataset-license">{ds.license}</span> : null}
                </div>
                <p className="dataset-title">{ds.title || ds.external_id}</p>
                {ds.url ? (
                  <a href={ds.url} target="_blank" rel="noreferrer" className="analysis-inline-link">
                    <ExternalLink size={12} /> {ds.doi || "Quelle öffnen"}
                  </a>
                ) : null}
                {ds.description ? <p className="dataset-desc">{ds.description}</p> : null}
                <DatasetDetailsBlock source={ds.source} externalId={ds.external_id} />
              </div>
              <button type="button" className="icon-button" title="Entfernen" onClick={() => void removeOne(ds.id)}>
                <Trash2 size={15} />
              </button>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}

export default DatasetsPanel;
