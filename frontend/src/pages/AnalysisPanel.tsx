import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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
  Clock,
  SquareDashedMousePointer,
  ShieldCheck,
  Check,
  X
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

/**
 * Drag a rectangle over a figure + comment → produce a natural-language annotation
 * that tells the planner exactly which region to fix. Coordinates are normalized to
 * percent of the figure so they are resolution-independent.
 */
function FigureAnnotator({
  src,
  filename,
  busy,
  onSubmit,
  onCancel
}: {
  src: string;
  filename: string;
  busy: boolean;
  onSubmit: (annotation: string) => void;
  onCancel: () => void;
}) {
  const ref = useRef<HTMLDivElement | null>(null);
  const [rect, setRect] = useState<{ x: number; y: number; w: number; h: number } | null>(null);
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [comment, setComment] = useState("");

  const norm = (e: React.PointerEvent) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return { x: 0, y: 0 };
    return {
      x: Math.min(1, Math.max(0, (e.clientX - box.left) / box.width)),
      y: Math.min(1, Math.max(0, (e.clientY - box.top) / box.height))
    };
  };

  const pct = (n: number) => Math.round(n * 100);

  const submit = () => {
    if (!comment.trim()) return;
    const region = rect
      ? `Markierter Bereich (in % der Figur): links=${pct(rect.x)}%, oben=${pct(rect.y)}%, Breite=${pct(rect.w)}%, Höhe=${pct(rect.h)}%. `
      : "";
    onSubmit(`${region}Anmerkung/Problem: ${comment.trim()}`);
  };

  return (
    <div className="analysis-annotator">
      <div
        className="analysis-annotator-canvas"
        ref={ref}
        onPointerDown={(e) => {
          (e.target as HTMLElement).setPointerCapture(e.pointerId);
          const p = norm(e);
          setStart(p);
          setRect({ x: p.x, y: p.y, w: 0, h: 0 });
        }}
        onPointerMove={(e) => {
          if (!start) return;
          const p = norm(e);
          setRect({
            x: Math.min(start.x, p.x),
            y: Math.min(start.y, p.y),
            w: Math.abs(p.x - start.x),
            h: Math.abs(p.y - start.y)
          });
        }}
        onPointerUp={() => setStart(null)}
      >
        <img src={src} alt={filename} draggable={false} />
        {rect && rect.w > 0.01 && rect.h > 0.01 ? (
          <div
            className="analysis-annotator-rect"
            style={{
              left: `${rect.x * 100}%`,
              top: `${rect.y * 100}%`,
              width: `${rect.w * 100}%`,
              height: `${rect.h * 100}%`
            }}
          />
        ) : null}
      </div>
      <div className="analysis-annotator-controls">
        <input
          type="text"
          value={comment}
          onChange={(e) => setComment(e.target.value)}
          placeholder="Was stimmt hier nicht? z.B. „Ausreißer entfernen, Achse in mmol/l"
          onKeyDown={(e) => {
            if (e.key === "Enter") submit();
          }}
        />
        <button type="button" className="button button-primary" onClick={submit} disabled={busy || !comment.trim()}>
          <Check size={14} /> Verbessern
        </button>
        <button type="button" className="icon-button" title="Abbrechen" onClick={onCancel}>
          <X size={15} />
        </button>
      </div>
      <p className="analysis-hint">Ziehe ein Rechteck über die Stelle (optional) und beschreibe das Problem.</p>
    </div>
  );
}

function ArtifactView({
  artifact,
  busy,
  onAnnotate
}: {
  artifact: AnalysisArtifact;
  busy?: boolean;
  onAnnotate?: (annotation: string) => void;
}) {
  const href = artifact.url ? `${API_BASE_URL.replace(/\/$/, "")}${artifact.url}` : "#";
  const [annotating, setAnnotating] = useState(false);
  if (artifact.kind === "figure") {
    if (annotating && onAnnotate) {
      return (
        <FigureAnnotator
          src={href}
          filename={artifact.filename}
          busy={!!busy}
          onSubmit={(a) => {
            setAnnotating(false);
            onAnnotate(a);
          }}
          onCancel={() => setAnnotating(false)}
        />
      );
    }
    return (
      <figure className="analysis-figure">
        <img src={href} alt={artifact.filename} loading="lazy" />
        <figcaption>
          {artifact.filename}
          <span className="analysis-figure-actions">
            {onAnnotate ? (
              <button type="button" className="analysis-inline-link" onClick={() => setAnnotating(true)}>
                <SquareDashedMousePointer size={12} /> annotieren
              </button>
            ) : null}
            <a href={href} download={artifact.filename} className="analysis-inline-link">
              <Download size={12} /> Original
            </a>
          </span>
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
  const [verifying, setVerifying] = useState(false);
  const [verification, setVerification] = useState<{ reproducible: boolean; actual: string } | null>(null);

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
    setVerification(null);
    try {
      const res = await api.analysis.get(runId);
      setActive(res.run);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Lauf konnte nicht geladen werden.");
    }
  }, []);

  const doRevise = useCallback(
    async (payload: { request?: string; annotation?: string }) => {
      if (!active || busy) return;
      setBusy(true);
      setError(null);
      setVerification(null);
      try {
        const res = await api.analysis.revise(active.id, {
          ...payload,
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
    },
    [active, busy, provider, model, refreshList]
  );

  const revise = useCallback(() => {
    const instruction = reviseText.trim();
    if (instruction) void doRevise({ request: instruction });
  }, [reviseText, doRevise]);

  const verify = useCallback(async () => {
    if (!active || verifying) return;
    setVerifying(true);
    setError(null);
    try {
      const res = await api.analysis.verify(active.id);
      setVerification({ reproducible: res.verification.reproducible, actual: res.verification.actual });
    } catch (e) {
      setError(e instanceof Error ? e.message : "Verifikation fehlgeschlagen.");
    } finally {
      setVerifying(false);
    }
  }, [active, verifying]);

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
                  <button
                    type="button"
                    className="analysis-chip analysis-chip--action"
                    onClick={() => {
                      const params = new URLSearchParams();
                      if (active.code_project_id) params.set("project", active.code_project_id);
                      if (active.script_rel) params.set("file", active.script_rel);
                      navigate(params.toString() ? `/werkstatt?${params.toString()}` : "/werkstatt");
                    }}
                  >
                    <FileCode2 size={12} /> In Werkstatt öffnen
                  </button>
                  <button type="button" className="analysis-chip analysis-chip--action" onClick={copyMarkdown}>
                    <ClipboardCopy size={12} /> {copied ? "kopiert!" : "Markdown"}
                  </button>
                  <button
                    type="button"
                    className="analysis-chip analysis-chip--action"
                    onClick={() => void verify()}
                    disabled={verifying || active.status !== "ok"}
                    title="Skript erneut ausführen und Ausgaben vergleichen"
                  >
                    {verifying ? <RefreshCw size={12} className="spin" /> : <ShieldCheck size={12} />} Reproduzieren
                  </button>
                  {verification ? (
                    <span
                      className={`analysis-badge ${verification.reproducible ? "analysis-badge--ok" : "analysis-badge--err"}`}
                    >
                      {verification.reproducible ? (
                        <>
                          <ShieldCheck size={13} /> reproduzierbar
                        </>
                      ) : (
                        <>
                          <AlertTriangle size={13} /> abweichend
                        </>
                      )}
                    </span>
                  ) : null}
                </div>

                {figures.map((a) => (
                  <ArtifactView key={a.id} artifact={a} busy={busy} onAnnotate={(text) => void doRevise({ annotation: text })} />
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
