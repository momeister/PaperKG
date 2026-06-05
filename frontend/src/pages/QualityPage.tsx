import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Check, Play, X } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { MetricCard } from "../components/MetricCard";
import { Status } from "../components/Status";

export function QualityPage() {
  const [reviewQuery, setReviewQuery] = useState("");
  const [selected, setSelected] = useState<number[]>([]);
  const queryClient = useQueryClient();
  const healthQuery = useQuery({ queryKey: ["health"], queryFn: api.getHealth, refetchInterval: 30000 });
  const benchmarkQuery = useQuery({ queryKey: ["benchmark"], queryFn: api.getBenchmark });
  const benchmarkSuiteLatestQuery = useQuery({ queryKey: ["benchmark-suite-latest"], queryFn: api.getBenchmarkSuiteLatest });
  const reviewQueryResult = useQuery({ queryKey: ["review", reviewQuery], queryFn: () => api.getReview("pending", reviewQuery) });
  const benchmarkSuite = useMutation({
    mutationFn: () =>
      api.runBenchmarkSuite({
        suite: "core",
        context_policy: "auto",
        compare_context_policies: ["auto", "whole", "chunk"],
        answer_context_mode: "kg"
      }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["benchmark-suite-latest"] });
    }
  });
  const reviewAction = useMutation({
    mutationFn: (action: "approve" | "reject") => api.reviewAction(selected, action),
    onSuccess: () => {
      setSelected([]);
      queryClient.invalidateQueries({ queryKey: ["review"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    }
  });

  function toggle(id: number) {
    setSelected((current) => (current.includes(id) ? current.filter((item) => item !== id) : [...current, id]));
  }

  const health = healthQuery.data;
  const summary = benchmarkQuery.data?.summary ?? {};
  const suiteReport = benchmarkSuite.data?.report ?? benchmarkSuiteLatestQuery.data?.report ?? {};
  const suiteSummary = suiteReport.summary ?? {};

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span>Metrics</span>
          <h1>Quality</h1>
        </div>
        <Status value={health?.status ?? "loading"} />
      </div>

      <div className="metrics-grid">
        <MetricCard label="Papers" value={health?.metadata_db?.paper_count ?? "—"} tone="blue" />
        <MetricCard label="PDFs" value={health?.pdf_library?.pdf_count ?? "—"} tone="green" />
        <MetricCard label="Pending Review" value={health?.review_queue?.pending ?? "—"} tone="amber" />
        <MetricCard label="Embeddings" value={health?.embeddings?.total ?? "—"} tone="neutral" />
        <MetricCard label="Concept Precision" value={formatMetric(summary.concept_precision)} tone="green" />
        <MetricCard label="Relation Recall" value={formatMetric(summary.relation_recall)} tone="blue" />
      </div>

      <div className="two-column">
        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Health</span>
              <strong>{health?.warnings?.length ?? 0} Warnungen</strong>
            </div>
          </div>
          <div className="warning-list">
            {(health?.warnings ?? []).map((warning) => (
              <div className="warning-row" key={warning}>
                {warning}
              </div>
            ))}
            {!health?.warnings?.length ? <EmptyState title="Keine Warnungen" /> : null}
          </div>
        </section>

        <section className="panel">
          <div className="panel-heading">
            <div>
              <span>Benchmark</span>
              <strong>{String(summary.case_count ?? "—")} Cases</strong>
            </div>
            <button className="button" disabled={benchmarkSuite.isPending} onClick={() => benchmarkSuite.mutate()}>
              <Play size={16} />
              <span>Full Suite</span>
            </button>
          </div>
          <div className="compact-table">
            {Object.entries(summary).slice(0, 10).map(([key, value]) => (
              <div className="table-row" key={key}>
                <span>{key}</span>
                <strong>{String(value)}</strong>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Full Benchmark</span>
            <strong>{String(suiteSummary.context_policy ?? "auto")}</strong>
          </div>
          <Status value={benchmarkSuite.isPending ? "running" : benchmarkSuite.data ? "complete" : "idle"} />
        </div>
        <div className="metrics-grid">
          <MetricCard label="Provider" value={String(suiteSummary.provider ?? "--")} tone="neutral" />
          <MetricCard label="Model" value={String(suiteSummary.model ?? "--")} tone="blue" />
          <MetricCard label="Extraction" value={formatMetric(suiteSummary.extraction_score)} tone="green" />
          <MetricCard label="Answer" value={formatMetric(suiteSummary.answer_score)} tone="blue" />
          <MetricCard label="Cross Paper" value={formatMetric(suiteSummary.cross_paper_score)} tone="amber" />
          <MetricCard label="Duration" value={formatSeconds(suiteSummary.duration_seconds)} tone="neutral" />
        </div>
        <div className="compact-table">
          <div className="table-row">
            <span>Warnings</span>
            <strong>{String(suiteSummary.warning_count ?? 0)}</strong>
          </div>
          <div className="table-row">
            <span>Run</span>
            <strong>{String(suiteReport.run_id ?? "--")}</strong>
          </div>
        </div>
      </section>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Review Queue</span>
            <strong>{reviewQueryResult.data?.total ?? 0} pending</strong>
          </div>
          <div className="button-row">
            <input value={reviewQuery} onChange={(event) => setReviewQuery(event.target.value)} placeholder="Filter" />
            <button className="button" disabled={!selected.length || reviewAction.isPending} onClick={() => reviewAction.mutate("reject")}>
              <X size={16} />
              <span>Reject</span>
            </button>
            <button className="button button-primary" disabled={!selected.length || reviewAction.isPending} onClick={() => reviewAction.mutate("approve")}>
              <Check size={16} />
              <span>Approve</span>
            </button>
          </div>
        </div>
        <div className="data-table review-table">
          <div className="data-row data-row--head">
            <span />
            <span>Label</span>
            <span>Canonical</span>
            <span>Paper</span>
            <span>Status</span>
          </div>
          {(reviewQueryResult.data?.items ?? []).map((item) => (
            <div className="data-row" key={item.id}>
              <input type="checkbox" checked={selected.includes(item.id)} onChange={() => toggle(item.id)} />
              <strong>{item.label}</strong>
              <span>{item.suggested_canonical ?? item.canonical_id}</span>
              <span>{item.paper_id}</span>
              <Status value={item.review_status} />
            </div>
          ))}
        </div>
      </section>
    </section>
  );
}

function formatSeconds(value: unknown) {
  return typeof value === "number" ? `${value.toFixed(1)}s` : "--";
}

function formatMetric(value: unknown) {
  return typeof value === "number" ? `${Math.round(value * 100)}%` : "—";
}
