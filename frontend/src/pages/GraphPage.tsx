import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Background, Controls, MiniMap, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { MetricCard } from "../components/MetricCard";
import { useAppState } from "../state";

const edgeOptions = ["cites", "concept", "method", "similar"];

export function GraphPage() {
  const { activeProject } = useAppState();
  const [query, setQuery] = useState("");
  const [edges, setEdges] = useState(edgeOptions);
  const graphQuery = useQuery({
    queryKey: ["graph", activeProject, query, edges],
    queryFn: () => api.getGraph({ project_id: activeProject, query, edge_types: edges.join(","), limit: 120 })
  });

  const flowNodes = useMemo(
    () =>
      (graphQuery.data?.nodes ?? []).map((node, index) => ({
        id: node.id,
        position: {
          x: (index % 8) * 180,
          y: Math.floor(index / 8) * 110
        },
        data: { label: node.label },
        className: `flow-node flow-node--${node.type}`
      })),
    [graphQuery.data?.nodes]
  );

  const flowEdges = useMemo(
    () =>
      (graphQuery.data?.edges ?? []).map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.label,
        animated: edge.type === "similar",
        className: `flow-edge flow-edge--${edge.type}`
      })),
    [graphQuery.data?.edges]
  );

  function toggleEdge(edge: string) {
    setEdges((current) => (current.includes(edge) ? current.filter((item) => item !== edge) : [...current, edge]));
  }

  return (
    <section className="page graph-page">
      <div className="page-title">
        <div>
          <span>Network</span>
          <h1>Graph</h1>
        </div>
        <input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="Node Search" />
      </div>

      <div className="metrics-grid compact-metrics">
        <MetricCard label="Papers" value={String(graphQuery.data?.stats.paper_count ?? "—")} tone="blue" />
        <MetricCard label="Nodes" value={String(graphQuery.data?.stats.node_count ?? "—")} tone="green" />
        <MetricCard label="Edges" value={String(graphQuery.data?.stats.edge_count ?? "—")} tone="amber" />
      </div>

      <div className="segmented">
        {edgeOptions.map((edge) => (
          <button key={edge} className={edges.includes(edge) ? "active" : ""} onClick={() => toggleEdge(edge)}>
            {edge}
          </button>
        ))}
      </div>

      <section className="graph-surface">
        {flowNodes.length ? (
          <ReactFlow nodes={flowNodes} edges={flowEdges} fitView minZoom={0.2}>
            <Background />
            <MiniMap pannable zoomable />
            <Controls />
          </ReactFlow>
        ) : (
          <EmptyState title={graphQuery.isLoading ? "Lade Graph" : "Kein Graph"} />
        )}
      </section>
    </section>
  );
}
