import { useCallback, useEffect, useMemo, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  FlaskConical,
  PanelRightClose,
  RefreshCw,
  FileCode2,
  FolderGit2,
  Download,
  ClipboardCopy,
  AlertTriangle,
  CheckCircle2,
  Clock
} from "lucide-react";

import { api, API_BASE_URL } from "../api";
import type { AnalysisArtifact, AnalysisRun } from "../types";

/**
 * Analyse-Werkstatt-Panel (WP1).
 *
 * Self-contained surface embedded in the Workspace center column: the user asks for
 * an analysis in natural language, the backend writes + runs a Python script and
 * returns figures/tables. Everything stays traceable — each result links to its
 * script, its provenance folder and the Code-Werkstatt for editing. No hidden state:
 * the run folder on disk (script.py / inputs / outputs / run.json / README.md) is the
 * source of truth; this panel only renders it.
 */

type Props = {
  projectId: string;
  provider?: string | null;
  model?: string | null;
  paperIds?: string[];
  onCollapse: () => void;
};

function StatusBadge({ status }: { status: string }) {
  if (status === "ok") {
    return (
      <span className="analysis-badge analysis-badge--ok">
        <CheckCircle2 size={13} /> erfolgreich
      </span>
    );
  }
  if (status === "timeout") {
    return (
      <span className="analysis-badge analysis-badge--warn">
        <Clock size={13} /> Timeout
      </span>
    );
  }
  return (
    <span className="analysis-badge analysis-badge--err">
      <AlertTriangle size={13} /> Fehler
    </span>
  );
}

/** Minimal CSV → rows parser for a bounded preview (no quoting edge-cases needed here). */
function parseCsvPreview(text: string, maxRows = 25): string[][] {
  const lines = text.replace(/\r\n/g, "\n").split("\n").filter((l) => l.length > 0);
  return lines.slice(0, maxRows).map((line) => line.split(","));
}

function TablePreview({ artifact }: { artifact: AnalysisArtifact }) {
  const [rows, setRows] = useState<string[][] | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    const target = artifact.url ? `${API_BASE_URL.replace(/\/$/, "")}${artifact.url}` : "";
    if (!target) return;
    fetch(target)
      .then((r) => r.text())
      .then((t) => alive && setRows(parseCsvPreview(t)))
      .catch(() => alive && setError("Tabelle konnte nicht geladen werden."));
    return () => {
      alive = false;
    };
  }, [artifact.url]);
  if (error) return <p className="analysis-muted">{error}</p>;
  if (!rows) return <p className="analysis-muted">Lade Tabelle …</p>;
  const [head, ...body] = rows;
  return (
    <div className="analysis-table-scroll">
      <table className="analysis-table">
        {head ? (
          <thead>
            <tr>
              {head.map((cell, i) => (
                <th key={i}>{cell}</th>
              ))}
            </tr>
          </thead>
        ) : null}
        <tbody>
          {body.map((row, i) => (
            <tr key={i}>
              {row.map((cell, j) => (
                <td key={j}>{cell}</td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

function ArtifactView({ artifact }: { artifact: AnalysisArtifact }) {
  const href = artifact.url ? `${API_BASE_URL.replace(/\/$/, "")}${artifact.url}` : "#";
  if (artifact.kind === "figure") {
    return (
      <figure className="analysis-figure">
        <img src={href} alt={artifact.filename} loading="lazy" />
        <figcaption>
          {artifact.filename}
          <a href={href} download={artifact.filename} className="analysis-inline-link">
            <Download size={12} /> Original
          </a>
        </figcaption>
      </figure>
    );
  }
  if (artifact.kind === "table") {
    return (
      <div className="analysis-artifact-block">
        <div className="analysis-artifact-head">
          <span>{artifact.filename}</span>
          <a href={href} download={artifact.filename} className="analysis-inline-link">
            <Download size={12} /> CSV
          </a>
        </div>
        <TablePreview artifact={artifact} />
      </div>
    );
  }
  // data / log: offer a link (log inline-collapsible would be nice, kept simple here).
  return (
    <div className="analysis-artifact-block">
      <div className="analysis-artifact-head">
        <span>
          {artifact.filename} <em className="analysis-muted">({artifact.kind})</em>
        </span>
        <a href={href} download={artifact.filename} className="analysis-inline-link">
          <Download size={12} /> öffnen
        </a>
      </div>
    </div>
  );
}

export function AnalysisPanel({ projectId, provider, model, paperIds, onCollapse }: Props) {
  const navigate = useNavigate();
  const [runs, setRuns] = useState<AnalysisRun[]>([]);
  const [active, setActive] = useState<AnalysisRun | null>(null);
  const [requestText, setRequestText] = useState("");
  const [reviseText, setReviseText] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);

  const scopedProjectId = projectId || undefined;

  const refreshList = useCallback(async () => {
    try {
      const res = await api.analysis.list(scopedProjectId ?? null);
      setRuns(res.runs);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Läufe konnten nicht geladen werden.");
    }
  }, [scopedProjectId]);

  useEffect(() => {
    void refreshList();
  }, [refreshList]);

  const submit = useCallback(async () => {
    const text = requestText.trim();
    if (!text || busy) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.analysis.create({
        request: text,
        project_id: scopedProjectId ?? null,
        provider: provider ?? null,
        model: model ?? null,
        paper_ids: paperIds ?? []
      });
      setActive(res.run);
      setRequestText("");
      await refreshList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Analyse fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }, [requestText, busy, scopedProjectId, provider, model, paperIds, refreshList]);

  const openRun = useCallback(async (runId: string) => {
    setError(null);
    try {
      const res = await api.analysis.get(runId);
      setActive(res.run);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lauf konnte nicht geladen werden.");
    }
  }, []);

  const revise = useCallback(async () => {
    if (!active || busy) return;
    const instruction = reviseText.trim();
    if (!instruction) return;
    setBusy(true);
    setError(null);
    try {
      const res = await api.analysis.revise(active.id, {
        request: instruction,
        provider: provider ?? null,
        model: model ?? null
      });
      setActive(res.run);
      setReviseText("");
      await refreshList();
    } catch (e) {
      setError(e instanceof Error ? e.message : "Revision fehlgeschlagen.");
    } finally {
      setBusy(false);
    }
  }, [active, busy, reviseText, provider, model, refreshList]);

  const copyMarkdown = useCallback(() => {
    if (!active) return;
    const lines = (active.artifacts ?? [])
      .filter((a) => a.kind === "figure" && a.url)
      .map((a) => `![${active.title ?? a.filename}](${API_BASE_URL.replace(/\/$/, "")}${a.url})`);
    const md = [`### ${active.title ?? "Analyse"}`, active.description ?? "", ...lines].join("\n\n");
    void navigator.clipboard?.writeText(md);
    setCopied(true);
    window.setTimeout(() => setCopied(false), 1500);
  }, [active]);

  const figures = useMemo(
    () => (active?.artifacts ?? []).filter((a) => a.kind === "figure"),
    [active]
  );
  const tables = useMemo(() => (active?.artifacts ?? []).filter((a) => a.kind === "table"), [active]);
  const others = useMemo(
    () => (active?.artifacts ?? []).filter((a) => a.kind !== "figure" && a.kind !== "table"),
    [active]
  );

  return (
    <section className="workspace-pane analysis-pane">
      <div className="pane-heading workspace-pane-heading">
        <div>
          <span className="pane-eyebrow">Werkstatt</span>
          <h2>
            <FlaskConical size={16} /> Analyse
          </h2>
        </div>
        <button type="button" className="icon-button" title="Schließen" onClick={onCollapse}>
          <PanelRightClose size={17} />
        </button>
      </div>

      <div className="analysis-body">
        <div className="analysis-composer">
          <textarea
            value={requestText}
            onChange={(e) => setRequestText(e.target.value)}
            placeholder="Was soll analysiert/geplottet werden? Z.B. „Altersverteilung der Kohorte als Histogramm, plus Tabelle der Gruppengrößen."
            rows={3}
            onKeyDown={(e) => {
              if ((e.metaKey || e.ctrlKey) && e.key === "Enter") void submit();
            }}
          />
          <button type="button" className="button button-primary" onClick={() => void submit()} disabled={busy || !requestText.trim()}>
            {busy ? <RefreshCw size={15} className="spin" /> : <FlaskConical size={15} />}
            {busy ? "Analysiere …" : "Analysieren"}
          </button>
          <p className="analysis-hint">
            Läuft lokal als Python-Skript. Jedes Ergebnis ist ein git-versionierter Ordner
            (Skript · Daten · Ausgaben · Provenance) und in der Code-Werkstatt editierbar.
          </p>
        </div>

        {error ? <p className="analysis-error">{error}</p> : null}

        <div className="analysis-columns">
          <aside className="analysis-runs">
            <div className="analysis-runs-head">
              <span>Läufe</span>
              <button type="button" className="icon-button" title="Aktualisieren" onClick={() => void refreshList()}>
                <RefreshCw size={13} />
              </button>
            </div>
            <ul>
              {runs.map((r) => (
                <li key={r.id}>
                  <button
                    type="button"
                    className={active?.id === r.id ? "active" : ""}
                    onClick={() => void openRun(r.id)}
                  >
                    <span className="analysis-run-title">{r.title || r.request || r.id}</span>
                    <StatusBadge status={r.status} />
                  </button>
                </li>
              ))}
              {runs.length === 0 ? <li className="analysis-muted">Noch keine Analysen.</li> : null}
            </ul>
          </aside>

          <div className="analysis-detail">
            {active ? (
              <>
                <div className="analysis-detail-head">
                  <h3>{active.title}</h3>
                  <StatusBadge status={active.status} />
                </div>
                {active.description ? <p className="analysis-desc">{active.description}</p> : null}

                <div className="analysis-provenance">
                  <span className="analysis-chip">
                    <FolderGit2 size={12} /> {active.rel_dir}
                  </span>
                  {active.provider ? (
                    <span className="analysis-chip">
                      {active.provider}/{active.model || "default"}
                    </span>
                  ) : null}
                  <span className="analysis-chip" title="Fingerabdruck der Ausgaben (WP4)">
                    #{(active.output_hash || "").slice(0, 10)}
                  </span>
                  <button type="button" className="analysis-chip analysis-chip--action" onClick={() => navigate("/werkstatt")}>
                    <FileCode2 size={12} /> In Werkstatt öffnen
                  </button>
                  <button type="button" className="analysis-chip analysis-chip--action" onClick={copyMarkdown}>
                    <ClipboardCopy size={12} /> {copied ? "kopiert!" : "Markdown"}
                  </button>
                </div>

                {figures.map((a) => (
                  <ArtifactView key={a.id} artifact={a} />
                ))}
                {tables.map((a) => (
                  <ArtifactView key={a.id} artifact={a} />
                ))}
                {others.length ? (
                  <details className="analysis-others">
                    <summary>Weitere Dateien ({others.length})</summary>
                    {others.map((a) => (
                      <ArtifactView key={a.id} artifact={a} />
                    ))}
                  </details>
                ) : null}

                {active.status !== "ok" && active.stderr ? (
                  <details className="analysis-stderr" open>
                    <summary>Fehlerausgabe</summary>
                    <pre>{active.stderr}</pre>
                  </details>
                ) : (
                  <details className="analysis-stderr">
                    <summary>Log</summary>
                    <pre>{active.stdout || "(leer)"}</pre>
                  </details>
                )}

                <div className="analysis-revise">
                  <input
                    type="text"
                    value={reviseText}
                    onChange={(e) => setReviseText(e.target.value)}
                    placeholder="Anpassen: z.B. „Y-Achse logarithmisch, Titel ändern"
                    onKeyDown={(e) => {
                      if (e.key === "Enter") void revise();
                    }}
                  />
                  <button type="button" className="button button-ghost" onClick={() => void revise()} disabled={busy || !reviseText.trim()}>
                    <RefreshCw size={14} /> Revidieren
                  </button>
                </div>
              </>
            ) : (
              <div className="analysis-empty">
                <FlaskConical size={26} />
                <p>Formuliere eine Analyse oder wähle links einen Lauf.</p>
              </div>
            )}
          </div>
        </div>
      </div>
    </section>
  );
}

export default AnalysisPanel;
