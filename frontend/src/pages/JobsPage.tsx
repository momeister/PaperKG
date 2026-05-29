import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { BarChart3, GitBranch, Play, Wrench } from "lucide-react";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { Status } from "../components/Status";
import { useAppState } from "../state";

export function JobsPage() {
  const { provider, model } = useAppState();
  const queryClient = useQueryClient();
  const jobsQuery = useQuery({ queryKey: ["jobs"], queryFn: api.getJobs, refetchInterval: 5000 });
  const benchmark = useMutation({ mutationFn: api.runBenchmarkJob });
  const graph = useMutation({
    mutationFn: api.runGraphRebuild,
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["jobs"] })
  });
  const healthRepair = useMutation({
    mutationFn: api.runHealthRepair,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["jobs"] });
      queryClient.invalidateQueries({ queryKey: ["health"] });
    }
  });
  const evalJob = useMutation({ mutationFn: () => api.runEvalJob(provider!, model) });

  return (
    <section className="page">
      <div className="page-title">
        <div>
          <span>Automation</span>
          <h1>Jobs</h1>
        </div>
      </div>

      <div className="job-actions">
        <button className="action-tile" onClick={() => graph.mutate()} disabled={graph.isPending}>
          <GitBranch size={22} />
          <strong>Graph Rebuild</strong>
          <Status value={graph.isPending ? "running" : graph.data?.status ?? "idle"} />
        </button>
        <button className="action-tile" onClick={() => benchmark.mutate()} disabled={benchmark.isPending}>
          <BarChart3 size={22} />
          <strong>Benchmark</strong>
          <Status value={benchmark.isPending ? "running" : benchmark.data?.status ?? "idle"} />
        </button>
        <button className="action-tile" onClick={() => healthRepair.mutate()} disabled={healthRepair.isPending}>
          <Wrench size={22} />
          <strong>Health Repair</strong>
          <Status value={healthRepair.isPending ? "running" : healthRepair.data?.status ?? "idle"} />
        </button>
        <button className="action-tile" onClick={() => evalJob.mutate()} disabled={!provider || evalJob.isPending}>
          <Play size={22} />
          <strong>Answer Eval</strong>
          <Status value={evalJob.isPending ? "running" : evalJob.data?.status ?? "idle"} />
        </button>
      </div>

      <section className="panel">
        <div className="panel-heading">
          <div>
            <span>Batch Jobs</span>
            <strong>{jobsQuery.data?.jobs.length ?? 0}</strong>
          </div>
        </div>
        {(jobsQuery.data?.jobs ?? []).length ? (
          <div className="data-table">
            <div className="data-row data-row--head">
              <span>Job</span>
              <span>Status</span>
              <span>Progress</span>
              <span>Failed</span>
              <span>Error</span>
            </div>
            {(jobsQuery.data?.jobs ?? []).map((job) => (
              <div className="data-row" key={job.job_id}>
                <strong>{job.job_id}</strong>
                <Status value={job.status} />
                <span>
                  {job.papers_processed}/{job.papers_total}
                </span>
                <span>{job.papers_failed}</span>
                <span>{job.error_message ?? ""}</span>
              </div>
            ))}
          </div>
        ) : (
          <EmptyState title="Keine Jobs" />
        )}
      </section>
    </section>
  );
}
