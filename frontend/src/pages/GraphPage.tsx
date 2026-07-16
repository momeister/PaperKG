import { useEffect, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Background, Controls, MarkerType, MiniMap, ReactFlow } from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import { api } from "../api";
import { EmptyState } from "../components/EmptyState";
import { MetricCard } from "../components/MetricCard";
import GraphLayoutWorker from "../graphLayoutWorker?worker";
import { THEME_META, useAppState } from "../state";
import type { Point } from "../types";

const edgeOptions = ["cites", "concept", "method", "similar"];
// Kontext-Budget für zusätzliche, nicht-extrahierte Papers (Cites-/Similarity-
// Kanten) — kein Hard-Cap auf extrahierte Papers: die liefert das Backend immer
// vollständig (graph_explorer: effective_limit = max(limit, extracted_count)).
const GRAPH_PAPER_LIMIT = 2000;

export function GraphPage() {
  const { activeProject, theme } = useAppState();
  const [query, setQuery] = useState("");
  const [edges, setEdges] = useState(edgeOptions);
  const graphQuery = useQuery({
    queryKey: ["graph", activeProject, query, edges],
    queryFn: () => api.getGraph({ project_id: activeProject, query, edge_types: edges.join(","), limit: GRAPH_PAPER_LIMIT })
  });

  // Das Force-Layout läuft in graphLayoutWorker (Web Worker), nicht synchron
  // im Render — damit Titel/Suchfeld/Metric-Cards sofort erscheinen, egal wie
  // groß der Graph ist, und nur die Canvas-Fläche auf das Layout wartet.
  const [layout, setLayout] = useState<Map<string, Point>>(new Map());
  const [isLayingOut, setIsLayingOut] = useState(false);

  useEffect(() => {
    const graphNodes = graphQuery.data?.nodes ?? [];
    const graphEdges = graphQuery.data?.edges ?? [];
    if (!graphNodes.length) {
      setLayout(new Map());
      setIsLayingOut(false);
      return;
    }
    setIsLayingOut(true);
    const worker = new GraphLayoutWorker();
    worker.onmessage = (event: MessageEvent<{ positions: Array<[string, Point]> }>) => {
      setLayout(new Map(event.data.positions));
      setIsLayingOut(false);
    };
    worker.postMessage({ nodes: graphNodes, edges: graphEdges });
    return () => worker.terminate();
  }, [graphQuery.data?.nodes, graphQuery.data?.edges]);

  const flowNodes = useMemo(
    () =>
      (graphQuery.data?.nodes ?? []).map((node) => ({
        id: node.id,
        position: layout.get(node.id) ?? { x: 0, y: 0 },
        data: { label: node.label },
        className: `flow-node flow-node--${node.type}`
      })),
    [graphQuery.data?.nodes, layout]
  );

  const flowEdges = useMemo(
    () =>
      (graphQuery.data?.edges ?? []).map((edge) => ({
        id: edge.id,
        source: edge.source,
        target: edge.target,
        label: edge.label,
        type: "default",
        animated: edge.type === "similar",
        className: `flow-edge flow-edge--${edge.type}`,
        markerEnd: { type: MarkerType.ArrowClosed, width: 16, height: 16 }
      })),
    [graphQuery.data?.edges]
  );

  function toggleEdge(edge: string) {
    setEdges((current) => (current.includes(edge) ? current.filter((item) => item !== edge) : [...current, edge]));
  }

  const stats = graphQuery.data?.stats;
  const truncated = Boolean(stats?.truncated);
  const totalPaperCount = stats?.total_paper_count;
  const extractedPaperCount = stats?.extracted_paper_count;

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
        <MetricCard
          label="Papers"
          value={String(stats?.paper_count ?? "—")}
          tone="blue"
          detail={truncated ? `von ${String(totalPaperCount)}` : undefined}
        />
        <MetricCard label="Extrahiert" value={String(extractedPaperCount ?? "—")} tone="neutral" />
        <MetricCard label="Nodes" value={String(stats?.node_count ?? "—")} tone="green" />
        <MetricCard label="Edges" value={String(stats?.edge_count ?? "—")} tone="amber" />
      </div>

      <div className="segmented">
        {edgeOptions.map((edge) => (
          <button key={edge} className={edges.includes(edge) ? "active" : ""} onClick={() => toggleEdge(edge)}>
            {edge}
          </button>
        ))}
      </div>

      <section className="graph-surface">
        {graphQuery.isLoading ? (
          <EmptyState title="Lade Graph" />
        ) : isLayingOut ? (
          <EmptyState title="Layout wird berechnet…" />
        ) : flowNodes.length ? (
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            fitView
            fitViewOptions={{ padding: 0.1 }}
            minZoom={0.02}
            colorMode={THEME_META[theme].scheme}
            proOptions={{ hideAttribution: true }}
          >
            <Background gap={22} size={1.4} />
            <MiniMap pannable zoomable />
            <Controls />
          </ReactFlow>
        ) : (
          <EmptyState title="Kein Graph" />
        )}
      </section>
    </section>
  );
}
