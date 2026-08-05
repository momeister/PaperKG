/**
 * Die Bereichskarte — das Erste, was man sieht.
 *
 * Vorher begann `/code` mit einer flachen Symbolliste: wer nicht schon wusste,
 * wonach er sucht, konnte nicht anfangen. Diese Karte beantwortet die Frage
 * davor — *woraus besteht das hier* — und lässt sich Ebene für Ebene öffnen, bis
 * die Symbolkarte übernimmt.
 *
 * Sie erbt die Regeln der Symbolkarte, weil sie dieselben sein müssen: eine
 * geratene Kante ist **gestrichelt** (`edgeStyleFor`, dieselbe Funktion), der
 * Vermutungsanteil steht dauerhaft im Bild, und die Zahl auf einem Pfeil lässt
 * sich anklicken und in die echten Kanten mit `datei:zeile` auflösen. Eine
 * aufsummierte Kante ohne diesen Weg wäre eine unüberprüfbare Behauptung an der
 * prominentesten Stelle des Werkzeugs.
 */
import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle, ChevronRight, Home, RefreshCw, Sparkles } from "lucide-react";

import { THEME_META, useAppState } from "../../state";
import type { CodeCluster, CodeClusterEdge, CodeClusterLevel } from "../../types";
import { ClusterFlowNode } from "./ClusterFlowNode";
import { clusterLayerLabel, clusterLayers, clusterWidthFor } from "./clusterLayout";
import { edgeStyleFor } from "./codeMap";
import { computeColumnLayout } from "./columnLayout";
import { CONFIDENCE_META, ConfidenceLegend } from "./shared";

const nodeTypes = { cluster: ClusterFlowNode };

/** Wie bei der Symbolkarte: `fitView` als Prop wirkt nur beim Einhängen. */
function FitOnLayout({ signature }: { signature: string }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.06, maxZoom: 1.3, minZoom: 0.05, duration: 220 });
    }, 60);
    return () => window.clearTimeout(timer);
  }, [signature, fitView]);
  return null;
}

export type ClusterMapPanelProps = {
  level: CodeClusterLevel | null;
  isLoading: boolean;
  error: string | null;
  selectedPath: string | null;
  namingBusy: boolean;
  onOpen: (prefix: string) => void;
  onSelect: (cluster: CodeCluster | null) => void;
  onSelectEdge: (edge: CodeClusterEdge | null) => void;
  onName: () => void;
};

export function ClusterMapPanel({
  level,
  isLoading,
  error,
  selectedPath,
  namingBusy,
  onOpen,
  onSelect,
  onSelectEdge,
  onName,
}: ClusterMapPanelProps) {
  const { theme } = useAppState();
  const clusters = useMemo(() => level?.nodes ?? [], [level]);
  const edges = useMemo(() => level?.edges ?? [], [level]);

  const { layers, backEdges } = useMemo(
    () => clusterLayers(clusters, edges),
    [clusters, edges],
  );
  const maxLayer = useMemo(
    () => (layers.size ? Math.max(...layers.values()) : 0),
    [layers],
  );

  // Dasselbe Spaltenlayout wie die Symbolkarte, nur mit der Abhängigkeitsstufe
  // als Spalte statt des Abstands zum Fokus.
  const positions = useMemo(
    () =>
      computeColumnLayout(
        clusters.map((cluster) => ({
          id: cluster.path,
          column: layers.get(cluster.path) ?? 0,
          weight: cluster.symbols,
        })),
        { columnGap: 340, rowGap: 132, maxPerColumn: 7 },
      ),
    [clusters, layers],
  );

  const flowNodes: Node[] = useMemo(
    () =>
      clusters.map((cluster) => ({
        id: cluster.path,
        type: "cluster",
        position: positions.get(cluster.path) ?? { x: 0, y: 0 },
        selected: cluster.path === selectedPath,
        width: clusterWidthFor(cluster.symbols),
        height: 118,
        data: {
          path: cluster.path,
          label: cluster.label,
          purpose: cluster.purpose,
          labelSource: cluster.label_source,
          labelStale: cluster.label_stale,
          symbols: cluster.symbols,
          files: cluster.files,
          kinds: cluster.kinds,
          guessedShare: cluster.guessed_share,
          hasChildren: cluster.has_children,
          width: clusterWidthFor(cluster.symbols),
          onOpen: () => onOpen(cluster.path),
        },
      })),
    [clusters, positions, selectedPath, onOpen],
  );

  const flowEdges: Edge[] = useMemo(
    () =>
      edges.map((edge) => {
        const style = edgeStyleFor(edge.weakest);
        const meta = CONFIDENCE_META[edge.weakest] ?? CONFIDENCE_META.guessed;
        const isBack = backEdges.has(`${edge.from}|${edge.to}`);
        return {
          id: `${edge.from}|${edge.to}`,
          source: edge.from,
          target: edge.to,
          className: `cgp-cluster-edge cgp-cluster-edge--${edge.weakest}${
            isBack ? " cgp-cluster-edge--ring" : ""
          }`,
          style,
          // Die Zahl ist der Punkt der Karte — und `meta.marker` daneben sagt,
          // wie sicher der schwächste Teil davon ist.
          label: `${meta.marker} ${edge.count}`,
          labelShowBg: false,
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
          data: { edge },
        };
      }),
    [edges, backEdges],
  );

  const guessShare = useMemo(() => {
    if (!edges.length) return null;
    const guessed = edges.filter((edge) => edge.weakest === "guessed").length;
    return Math.round((guessed / edges.length) * 100);
  }, [edges]);

  const crumbs = useMemo(() => {
    const prefix = level?.prefix ?? "";
    if (!prefix) return [];
    const parts = prefix.split("/");
    return parts.map((part, index) => ({
      label: part,
      prefix: parts.slice(0, index + 1).join("/"),
    }));
  }, [level?.prefix]);

  const signature = `${level?.prefix ?? ""}|${clusters.length}|${edges.length}`;

  return (
    <div className="cgp-map cgp-clustermap">
      <div className="cgp-map-toolbar">
        <button className="cgp-crumb" onClick={() => onOpen("")} title="Zur obersten Ebene">
          <Home size={13} /> Projekt
        </button>
        {crumbs.map((crumb) => (
          <span key={crumb.prefix} className="cgp-crumb-wrap">
            <ChevronRight size={12} className="cgp-crumb-sep" />
            <button className="cgp-crumb" onClick={() => onOpen(crumb.prefix)}>
              {crumb.label}
            </button>
          </span>
        ))}

        <span className="cgp-spacer" />
        {guessShare !== null && (
          <span className="cg-gauge" title="Anteil der Bereichs-Kanten, deren schwächster Beleg geraten ist">
            {guessShare}% vermutet
          </span>
        )}
        <span className="cgp-count" title="Bereiche auf dieser Ebene">
          {clusters.length}
        </span>
        <button
          className="wk-iconbtn"
          title="Bereiche vom Modell benennen lassen — die Struktur bleibt unverändert"
          disabled={namingBusy || !clusters.length}
          onClick={onName}
        >
          {namingBusy ? <RefreshCw size={14} className="cg-spin" /> : <Sparkles size={14} />}
        </button>
      </div>

      {backEdges.size > 0 && (
        <div className="cgp-map-banner cgp-map-banner--info">
          <AlertTriangle size={14} />
          <span>
            {backEdges.size === 1 ? "Eine Beziehung schliesst" : `${backEdges.size} Beziehungen schliessen`}{" "}
            einen Ring — sie {backEdges.size === 1 ? "ist" : "sind"} gestrichelt-rot gezeichnet und
            von der Schichtung ausgenommen. Weggelassen {backEdges.size === 1 ? "wurde sie" : "wurden sie"} nicht.
          </span>
        </div>
      )}
      {error && (
        <div className="cgp-map-banner cgp-map-banner--error">
          <AlertTriangle size={14} /> <span>{error}</span>
        </div>
      )}

      <div className="cgp-map-canvas">
        {isLoading && !clusters.length ? (
          <p className="muted cg-empty">Bereiche werden gelesen …</p>
        ) : !clusters.length ? (
          <p className="muted cg-empty">
            Hier liegen keine indizierten Dateien. Eine Ebene zurück oder ein anderes Projekt.
          </p>
        ) : (
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            colorMode={THEME_META[theme].scheme}
            nodesDraggable={clusters.length <= 60}
            nodesConnectable={false}
            elementsSelectable
            minZoom={0.05}
            maxZoom={2}
            proOptions={{ hideAttribution: true }}
            onNodeClick={(_event, node) => {
              onSelectEdge(null);
              onSelect(clusters.find((cluster) => cluster.path === node.id) ?? null);
            }}
            onNodeDoubleClick={(_event, node) => {
              const cluster = clusters.find((item) => item.path === node.id);
              if (cluster?.has_children) onOpen(cluster.path);
            }}
            onEdgeClick={(_event, edge) => {
              onSelect(null);
              onSelectEdge((edge.data as { edge: CodeClusterEdge } | undefined)?.edge ?? null);
            }}
            onPaneClick={() => {
              onSelect(null);
              onSelectEdge(null);
            }}
          >
            <Background gap={22} size={1} />
            <Controls showInteractive={false} />
            {clusters.length >= 8 && <MiniMap pannable zoomable />}
            <FitOnLayout signature={signature} />
          </ReactFlow>
        )}
      </div>

      <div className="cgp-map-legend">
        <span className="cgp-axis">
          links: {clusterLayerLabel(0, maxLayer)} · rechts: {clusterLayerLabel(maxLayer, maxLayer)}
        </span>
        <ConfidenceLegend />
      </div>
    </div>
  );
}

export default ClusterMapPanel;
