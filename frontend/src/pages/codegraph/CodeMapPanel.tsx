/**
 * Die Karte: Symbole als Knoten, Beziehungen als Pfeile.
 *
 * Ein Bild ist das Autoritativste, was dieses Werkzeug ausgeben kann — es sieht
 * wahrer aus als jede Liste. Deshalb trägt hier jede Kante ihre Sicherheitsstufe
 * *zweimal*: als Strichart im Bild (geraten = gestrichelt) und als Zeile in der
 * Kantentabelle darunter, mit ●◐○◆ und `datei:zeile`. Das Bild allein liesse
 * sich nicht nachprüfen.
 *
 * Die Legende steht dauerhaft unter der Karte, nie in einem Menü.
 */
import { useEffect, useMemo } from "react";
import {
  Background,
  Controls,
  MarkerType,
  MiniMap,
  Panel,
  ReactFlow,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { AlertTriangle, ArrowRight, Maximize2, Minimize2, RotateCcw, Scissors } from "lucide-react";

import { THEME_META, useAppState } from "../../state";
import type { CodeEdgeKind, CodeNodeId, CodeSliceEdge } from "../../types";
import { CodeFlowNode } from "./CodeFlowNode";
import { edgeGroupOf, edgeStyleFor, guessedShareOf, nodeWidthFor, visibleEdges } from "./codeMap";
import type { MapState } from "./codeMap";
import { CONFIDENCE_META, ConfidenceLegend, EDGE_GROUPS, edgeKindLabel } from "./shared";

// Modul-Konstante: als Objektliteral im JSX würde xyflow bei jedem Render alle
// Knotenkomponenten neu einhängen.
const nodeTypes = { code: CodeFlowNode };

/**
 * Nach jedem Layout neu einpassen — und dabei das Feld wirklich ausfüllen.
 *
 * `fitView` als Prop wirkt nur beim Einhängen; die Positionen ändern sich
 * danach noch. Das kleine Padding und der grosszügige `maxZoom` sind Absicht:
 * mit den Vorgabewerten stand ein Ausschnitt aus zwanzig Knoten als
 * briefmarkengrosses Knäuel in der Mitte einer leeren Fläche, und man musste
 * jedes Mal von Hand heranzoomen, um die Namen zu lesen.
 */
function FitOnLayout({ signature }: { signature: string }) {
  const { fitView } = useReactFlow();
  useEffect(() => {
    const timer = window.setTimeout(() => {
      void fitView({ padding: 0.04, maxZoom: 1.6, minZoom: 0.05, duration: 220 });
    }, 60);
    return () => window.clearTimeout(timer);
  }, [signature, fitView]);
  return null;
}

export type CodeMapPanelProps = {
  map: MapState;
  positions: Map<string, { x: number; y: number }>;
  focusId: CodeNodeId | null;
  /**
   * Mehrere Wurzeln — die Karte über eine Trefferliste („welche Funktionen
   * gehören zu diesem Feature"). Dann gibt es keinen einen Fokus, und alle
   * Wurzeln stehen gleichwertig in Spalte 0.
   */
  focusIds?: CodeNodeId[];
  selectedId: CodeNodeId | null;
  edgeKinds: CodeEdgeKind[];
  isLoading: boolean;
  error: string | null;
  /** Signatur des Fokus, als Kurzform im Knoten. */
  focusSignature?: string | null;
  expanded: boolean;
  onToggleEdgeKind: (kind: CodeEdgeKind) => void;
  onSelect: (nodeId: CodeNodeId) => void;
  onExpand: (nodeId: CodeNodeId) => void;
  onCollapse: (nodeId: CodeNodeId) => void;
  onSelectEdge: (edge: CodeSliceEdge | null) => void;
  onReset: () => void;
  onDismissBudget: () => void;
  onToggleExpanded: () => void;
};

export function CodeMapPanel({
  map,
  positions,
  focusId,
  focusIds,
  selectedId,
  edgeKinds,
  isLoading,
  error,
  focusSignature,
  expanded,
  onToggleEdgeKind,
  onSelect,
  onExpand,
  onCollapse,
  onSelectEdge,
  onReset,
  onDismissBudget,
  onToggleExpanded,
}: CodeMapPanelProps) {
  const { theme } = useAppState();
  const shownEdges = useMemo(() => visibleEdges(map, edgeKinds), [map, edgeKinds]);
  const guessShare = useMemo(() => guessedShareOf(shownEdges), [shownEdges]);

  // Wer ist wichtiger als das, was man angeklickt hat? Eine Hilfsfunktion mit
  // Relevanz 0,1 und ein Nachbar mit 0,7 sollen nicht gleich aussehen.
  const focusRelevance = focusId ? (map.nodes.get(focusId)?.relevance ?? 0) : 0;

  // Ein Set, kein `includes`: bei acht Wurzeln und sechshundert Knoten wäre das
  // eine lineare Suche je Knoten und Render.
  const focusSet = useMemo(
    () => new Set<CodeNodeId>(focusIds ?? (focusId ? [focusId] : [])),
    [focusIds, focusId],
  );

  const flowNodes: Node[] = useMemo(
    () =>
      [...map.nodes.values()].map((node) => ({
        id: node.id,
        type: "code",
        position: positions.get(node.id) ?? { x: 0, y: 0 },
        selected: node.id === selectedId,
        // Ausdrücklich, nicht nur als CSS-Breite: die Minimap zeichnet nur
        // Knoten, deren Masse sie kennt — ohne das blieb sie ein leeres
        // weisses Rechteck.
        width: nodeWidthFor(node.relevance),
        height: 58,
        data: {
          label: node.name,
          qualified: node.qualified,
          kind: node.kind,
          path: node.path,
          line: node.line,
          relevance: node.relevance,
          width: nodeWidthFor(node.relevance),
          column: node.column,
          expanded: node.expanded,
          isFocus: focusSet.has(node.id),
          // Bei mehreren Wurzeln gibt es kein „stärker als der Fokus" — jede
          // Wurzel wäre ein anderer Vergleichspunkt, und der Pfeil nach oben
          // behauptete dann etwas, das von der Reihenfolge abhinge.
          stronger:
            !focusIds && node.id !== focusId && node.relevance > focusRelevance + 0.1,
          signature: node.id === focusId ? focusSignature : null,
          onExpand: () => onExpand(node.id),
        },
      })),
    [
      map.nodes,
      positions,
      selectedId,
      focusId,
      focusIds,
      focusSet,
      focusRelevance,
      focusSignature,
      onExpand,
    ],
  );

  const flowEdges: Edge[] = useMemo(
    () =>
      shownEdges.map((edge) => {
        const style = edgeStyleFor(edge.confidence);
        const meta = CONFIDENCE_META[edge.confidence] ?? CONFIDENCE_META.guessed;
        return {
          id: `${edge.from}|${edge.to}|${edge.kind}`,
          source: edge.from,
          target: edge.to,
          className: `cgp-edge cgp-edge--${edgeGroupOf(edge.kind)} cgp-edge--${edge.confidence}`,
          style,
          // Der Titel ist die kurze Fassung der Belegzeile in der Tabelle.
          label: `${meta.marker} ${edgeKindLabel(edge.kind)}`,
          labelShowBg: false,
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
          data: { edge },
        };
      }),
    [shownEdges],
  );

  const nodeCount = map.nodes.size;

  return (
    <div className="cgp-map">
      <div className="cgp-map-toolbar">
        {EDGE_GROUPS.map((group) => (
          <span key={group.title} className="cgp-facet-group" title={group.title}>
            <span className="cgp-facet-group-title">{group.title}</span>
            {group.kinds.map((kind) => {
              const on = edgeKinds.includes(kind);
              return (
                <button
                  key={kind}
                  className={`cgp-facet${on ? " cgp-facet--on" : ""}`}
                  onClick={() => onToggleEdgeKind(kind)}
                  title={
                    kind === "contains"
                      ? "Enthält-Kanten: eine Datei enthält hunderte Symbole — das Budget ist damit schnell erreicht."
                      : `Kanten der Art „${edgeKindLabel(kind)}" ein-/ausblenden`
                  }
                >
                  {edgeKindLabel(kind)}
                </button>
              );
            })}
          </span>
        ))}
        <span className="cgp-spacer" />
        {guessShare !== null && (
          <span className="cg-gauge" title="Anteil geratener Kanten im aktuellen Bild">
            {guessShare}% vermutet
          </span>
        )}
        <span className="cgp-count" title="Knoten im Bild">
          {nodeCount}
        </span>
        <button className="wk-iconbtn" title="Karte zurücksetzen" onClick={onReset}>
          <RotateCcw size={14} />
        </button>
        <button
          className="wk-iconbtn"
          title={expanded ? "Karte wieder verkleinern" : "Karte vergrössern"}
          onClick={onToggleExpanded}
        >
          {expanded ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
        </button>
      </div>

      {map.atBudget && (
        <div className="cgp-map-banner">
          <AlertTriangle size={14} />
          <span>
            Knotengrenze erreicht — die letzte Erweiterung wurde <strong>ganz</strong> verworfen,
            damit das Bild nicht stillschweigend unvollständig ist. Schränke die Kantenarten ein
            oder setze den Fokus neu.
          </span>
          <button className="wk-btn" onClick={onDismissBudget}>
            Verstanden
          </button>
        </div>
      )}
      {map.truncated && (
        <div className="cgp-map-banner cgp-map-banner--info">
          <Scissors size={14} />
          <span>
            Ausschnitt gekürzt: schon der Graph selbst hat sein Budget erreicht — es gibt mehr
            Nachbarn, als hier stehen.
          </span>
        </div>
      )}
      {error && (
        <div className="cgp-map-banner cgp-map-banner--error">
          <AlertTriangle size={14} /> <span>{error}</span>
        </div>
      )}

      <div className="cgp-map-canvas">
        {focusSet.size === 0 ? (
          <p className="muted cg-empty">
            Wähle links ein Symbol — die Karte zeigt dann, was es aufruft und wer es aufruft.
          </p>
        ) : isLoading && nodeCount === 0 ? (
          <p className="muted cg-empty">Karte wird geladen …</p>
        ) : nodeCount === 0 ? (
          <p className="muted cg-empty">
            Keine Beziehungen dieser Arten. Schalte oben weitere Kantenarten dazu.
          </p>
        ) : (
          <ReactFlow
            nodes={flowNodes}
            edges={flowEdges}
            nodeTypes={nodeTypes}
            colorMode={THEME_META[theme].scheme === "dark" ? "dark" : "light"}
            fitView
            fitViewOptions={{ padding: 0.04, maxZoom: 1.6 }}
            minZoom={0.05}
            maxZoom={2.5}
            proOptions={{ hideAttribution: true }}
            // Ab 200 Knoten kostet das Zeichnen aller Elemente mehr als das
            // Ausrechnen, was gerade sichtbar ist; ab 300 wird Ziehen zäh.
            onlyRenderVisibleElements={nodeCount > 200}
            nodesDraggable={nodeCount <= 300}
            nodesConnectable={false}
            edgesFocusable
            onNodeClick={(_, node) => onSelect(node.id)}
            onNodeDoubleClick={(_, node) => onExpand(node.id)}
            onNodeContextMenu={(event, node) => {
              event.preventDefault();
              onCollapse(node.id);
            }}
            onEdgeClick={(_, edge) =>
              onSelectEdge((edge.data as { edge?: CodeSliceEdge } | undefined)?.edge ?? null)
            }
          >
            <Background gap={22} />
            <Controls showInteractive={false} />
            {/* Die Karte muss selbst sagen, was ihre Seiten bedeuten. Ohne das
                bliebe die Richtung eine Konvention, die man kennen muss. */}
            <Panel position="top-left" className="cgp-axis cgp-axis--in">
              <ArrowRight size={12} /> ruft dieses Symbol auf
            </Panel>
            <Panel position="top-right" className="cgp-axis cgp-axis--out">
              wird von hier aufgerufen <ArrowRight size={12} />
            </Panel>
            {/* Erst ab einer Grösse, ab der man sich verlieren kann. Bei fünf
                Knoten verdeckte die Minimap zwei davon und half bei keinem.

                Farben kommen aus dem Stylesheet, nicht aus Props: React Flow
                schreibt sie sonst als `fill`-Attribut, und ein `var(--accent)`
                löst als Attributwert nicht auf — die Minimap blieb ein weisses
                Rechteck, das wie ein Defekt aussah. */}
            {nodeCount >= 25 && (
              <MiniMap pannable zoomable className="cgp-minimap" nodeStrokeWidth={2} />
            )}
            <FitOnLayout signature={`${nodeCount}:${positions.size}`} />
          </ReactFlow>
        )}
      </div>

      <div className="cgp-legend">
        <ConfidenceLegend className="cgp-legend-marks" />
        <span className="cgp-legend-dash">
          <svg width="34" height="8" aria-hidden="true">
            <line x1="0" y1="4" x2="34" y2="4" strokeDasharray="5 4" strokeWidth="1.4" />
          </svg>
          gestrichelt = geraten
        </span>
        <span className="muted">Doppelklick klappt aus · Rechtsklick klappt ein</span>
      </div>
    </div>
  );
}

export default CodeMapPanel;
