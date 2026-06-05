import { useMemo, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical, Gauge, Play, Trash2 } from "lucide-react";

import { api, ApiError } from "../api";
import { EmptyState } from "../components/EmptyState";
import { MetricCard } from "../components/MetricCard";
import { Status } from "../components/Status";
import { useAppState } from "../state";
import type { BenchmarkRun } from "../types";

export function BenchmarksPage() {
  const { provider: globalProvider, model: globalModel } = useAppState();
  const queryClient = useQueryClient();

  const providersQuery = useQuery({ queryKey: ["providers"], queryFn: api.getProviders });
  const runsQuery = useQuery({ queryKey: ["benchmark-runs"], queryFn: () => api.getBenchmarkRuns() });

  const [provider, setProvider] = useState<string>("");
  const [model, setModel] = useState<string>("");
  const [selectedRunId, setSelectedRunId] = useState<string | null>(null);

  const providers = providersQuery.data?.providers ?? [];
  const activeProviderName = provider || globalProvider || providersQuery.data?.default_provider || providers[0]?.name || "";
  const activeProvider = providers.find((entry) => entry.name === activeProviderName);
  const modelOptions = activeProvider?.models ?? [];
  const activeModel = model || globalModel || activeProvider?.default_model || "";

  const invalidate = () => queryClient.invalidateQueries({ queryKey: ["benchmark-runs"] });

  const runExtraction = useMutation({
    mutationFn: () => api.runBenchmarkJob(),
    onSuccess: (payload) => {
      invalidate();
      if (payload.run?.id) {
        setSelectedRunId(payload.run.id);
      }
    }
  });
  const runQa = useMutation({
    mutationFn: () => api.runEvalJob(activeProviderName, activeModel || undefined),
    onSuccess: (payload) => {
      invalidate();
      if (payload.run?.id) {
        setSelectedRunId(payload.run.id);
      }
    }
  });
  const removeRun = useMutation({
    mutationFn: (runId: string) => api.deleteBenchmarkRun(runId),
    onSuccess: invalidate
  });

  const runs = runsQuery.data?.items ?? [];
  const selectedRun = useMemo(
    () => runs.find((run) => run.id === selectedRunId) ?? runs[0] ?? null,
    [runs, selectedRunId]
  );

  const busy = runExtraction.isPending || runQa.isPending;
  const error = runExtraction.error || runQa.error;

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span>Quality</span>
          <h1>Benchmarks</h1>
          <p className="page-subtitle">Modell wählen, Läufe starten und jede Metadaten/Metrik nachverfolgen.</p>
        </div>
        <Status value={busy ? "running" : "idle"} />
      </div>

      <div className="two-column">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Extraktions-Benchmark</span>
              <strong>Gold-Fixtures (Precision / Recall / F1)</strong>
            </div>
            <FlaskConical size={18} />
          </div>
          <p className="muted">
            Vergleicht die Extraktion gegen kuratierte Gold-Paper. Deterministisch, kein Live-Modell nötig.
          </p>
          <div className="button-row">
            <button className="button button-primary" type="button" disabled={busy} onClick={() => runExtraction.mutate()}>
              <Play size={16} />
              <span>Extraktions-Benchmark starten</span>
            </button>
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Q&amp;A-Eval</span>
              <strong>Antwortqualität pro Modell</strong>
            </div>
            <Gauge size={18} />
          </div>
          <div className="benchmark-model-row">
            <label>
              Provider
              <select value={activeProviderName} onChange={(event) => { setProvider(event.target.value); setModel(""); }}>
                {providers.map((entry) => (
                  <option key={entry.name} value={entry.name}>
                    {entry.name}
                  </option>
                ))}
              </select>
            </label>
            <label>
              Modell
              <select value={activeModel} onChange={(event) => setModel(event.target.value)}>
                {activeModel && !modelOptions.includes(activeModel) ? <option value={activeModel}>{activeModel}</option> : null}
                {modelOptions.map((name) => (
                  <option key={name} value={name}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
          </div>
          <div className="button-row">
            <button
              className="button button-primary"
              type="button"
              disabled={busy || !activeProviderName}
              onClick={() => runQa.mutate()}
            >
              <Play size={16} />
              <span>Q&amp;A-Eval starten</span>
            </button>
          </div>
          <p className="muted">Nutzt ein Live-Modell — kann je nach Provider einige Sekunden bis Minuten dauern.</p>
        </section>
      </div>

      {error ? <div className="inline-error">{formatError(error)}</div> : null}

      {selectedRun ? <RunDetail run={selectedRun} /> : null}

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Verlauf</span>
            <strong>{runs.length} Läufe</strong>
          </div>
        </div>
        {runs.length ? (
          <div className="data-table benchmark-runs-table">
            <div className="data-row data-row--head">
              <span>Typ</span>
              <span>Modell</span>
              <span>Kennzahl</span>
              <span>Dauer</span>
              <span>Zeitpunkt</span>
              <span />
            </div>
            {runs.map((run) => (
              <div
                className={`data-row ${selectedRun?.id === run.id ? "data-row--active" : ""}`}
                key={run.id}
                role="button"
                tabIndex={0}
                onClick={() => setSelectedRunId(run.id)}
                onKeyDown={(event) => {
                  if (event.key === "Enter") setSelectedRunId(run.id);
                }}
              >
                <span>{run.kind === "qa" ? "Q&A" : "Extraktion"}</span>
                <strong>{run.model || run.provider || "—"}</strong>
                <span>{headlineMetric(run)}</span>
                <span>{formatDuration(run.duration_ms)}</span>
                <span>{formatTimestamp(run.created_timestamp)}</span>
                <button
                  className="button button-compact"
                  type="button"
                  onClick={(event) => {
                    event.stopPropagation();
                    removeRun.mutate(run.id);
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="Noch keine Läufe" />
        )}
      </section>
    </section>
  );
}

function RunDetail({ run }: { run: BenchmarkRun }) {
  const summary = run.summary ?? {};
  const entries = Object.entries(summary);
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <span>{run.kind === "qa" ? "Q&A-Eval" : "Extraktions-Benchmark"}</span>
          <strong>{run.model || run.provider || "Letzter Lauf"}</strong>
        </div>
        <span className="muted">{formatTimestamp(run.created_timestamp)} · {formatDuration(run.duration_ms)}</span>
      </div>
      <div className="metrics-grid">
        {entries.slice(0, 6).map(([key, value]) => (
          <MetricCard key={key} label={prettyKey(key)} value={formatMetricValue(value)} tone="blue" />
        ))}
      </div>
      <details className="extraction-json-details">
        <summary>Alle Metadaten</summary>
        <pre>{JSON.stringify(run.report, null, 2)}</pre>
      </details>
    </section>
  );
}

function headlineMetric(run: BenchmarkRun): string {
  const summary = run.summary ?? {};
  if (run.kind === "qa") {
    const avg = summary.average_score;
    const passes = summary.pass_count;
    const cases = summary.case_count;
    const parts: string[] = [];
    if (typeof avg === "number") parts.push(`Ø ${avg.toFixed(2)}`);
    if (passes !== undefined && cases !== undefined) parts.push(`${passes}/${cases}`);
    return parts.join(" · ") || "—";
  }
  const precision = summary.concept_precision;
  const recall = summary.relation_recall;
  const parts: string[] = [];
  if (typeof precision === "number") parts.push(`P ${Math.round(precision * 100)}%`);
  if (typeof recall === "number") parts.push(`R ${Math.round(recall * 100)}%`);
  return parts.join(" · ") || "—";
}

function formatMetricValue(value: unknown): string {
  if (typeof value === "number") {
    return Number.isInteger(value) ? String(value) : value < 1 ? `${Math.round(value * 100)}%` : value.toFixed(2);
  }
  if (typeof value === "boolean") {
    return value ? "ja" : "nein";
  }
  return String(value ?? "—");
}

function prettyKey(key: string): string {
  return key.replace(/_/g, " ");
}

function formatDuration(ms?: number | null): string {
  if (!ms) return "—";
  if (ms < 1000) return `${ms} ms`;
  return `${(ms / 1000).toFixed(1)} s`;
}

function formatTimestamp(value?: string): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function formatError(error: unknown): string {
  if (error instanceof ApiError) {
    return typeof error.detail === "string" ? error.detail : error.message;
  }
  return error instanceof Error ? error.message : String(error);
}
