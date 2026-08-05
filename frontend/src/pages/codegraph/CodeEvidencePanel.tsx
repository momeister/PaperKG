/**
 * Die Kantentabelle — das nachprüfbare Gegenstück zur Karte.
 *
 * Jede Kante, die oben gezeichnet ist, steht hier als Zeile mit ihrer
 * Sicherheitsstufe und der Zeile, an der die Behauptung steht. Das ist der
 * eigentliche Grund, warum das Panel existiert: ein Pfeil im Bild lässt sich
 * nicht anklicken und nachlesen, eine Zeile schon.
 */
import { useMemo, useState } from "react";
import { ArrowRight } from "lucide-react";

import type { CodeEdgeKind, CodeNodeId, CodeSliceEdge } from "../../types";
import { Confidence, edgeKindLabel } from "./shared";
import { visibleEdges } from "./codeMap";
import type { MapState } from "./codeMap";

export type CodeEvidencePanelProps = {
  map: MapState;
  edgeKinds: CodeEdgeKind[];
  selectedEdge: CodeSliceEdge | null;
  onOpen: (path: string, line: number) => void;
  onSelectNode: (nodeId: CodeNodeId) => void;
};

const SORTS = {
  /** Schwächster Beleg zuerst — das ist, was man prüfen will. */
  weakest: (a: CodeSliceEdge, b: CodeSliceEdge) =>
    confidenceRank(a.confidence) - confidenceRank(b.confidence),
  path: (a: CodeSliceEdge, b: CodeSliceEdge) =>
    a.evidence_path.localeCompare(b.evidence_path) || a.evidence_line - b.evidence_line,
  kind: (a: CodeSliceEdge, b: CodeSliceEdge) => a.kind.localeCompare(b.kind),
};

function confidenceRank(value: string): number {
  return ["guessed", "resolved", "verified", "measured"].indexOf(value);
}

export function CodeEvidencePanel({
  map,
  edgeKinds,
  selectedEdge,
  onOpen,
  onSelectNode,
}: CodeEvidencePanelProps) {
  const [sort, setSort] = useState<keyof typeof SORTS>("weakest");
  const [onlyGuessed, setOnlyGuessed] = useState(false);

  const rows = useMemo(() => {
    const all = visibleEdges(map, edgeKinds);
    const filtered = onlyGuessed ? all.filter((edge) => edge.confidence === "guessed") : all;
    return [...filtered].sort(SORTS[sort]);
  }, [map, edgeKinds, onlyGuessed, sort]);

  const name = (id: CodeNodeId) => map.nodes.get(id)?.name ?? id.slice(0, 8);

  return (
    <div className="cgp-pane cgp-evidence">
      <div className="cgp-pane-head">
        <strong>Kanten mit Beleg</strong>
        <span className="cg-count">{rows.length}</span>
        <span className="cgp-spacer" />
        <label className="cgp-inline-check">
          <input
            type="checkbox"
            checked={onlyGuessed}
            onChange={(event) => setOnlyGuessed(event.target.checked)}
          />
          nur geratene
        </label>
        <select
          className="cgp-mini-select"
          value={sort}
          onChange={(event) => setSort(event.target.value as keyof typeof SORTS)}
          aria-label="Sortierung"
        >
          <option value="weakest">schwächster Beleg zuerst</option>
          <option value="path">nach Datei</option>
          <option value="kind">nach Art</option>
        </select>
      </div>
      <div className="cgp-pane-body">
        {rows.length === 0 ? (
          <p className="muted cg-empty">Keine Kanten im aktuellen Bild.</p>
        ) : (
          <table className="cgp-edge-table">
            <tbody>
              {rows.map((edge) => {
                const key = `${edge.from}|${edge.to}|${edge.kind}`;
                const active =
                  selectedEdge &&
                  selectedEdge.from === edge.from &&
                  selectedEdge.to === edge.to &&
                  selectedEdge.kind === edge.kind;
                return (
                  <tr key={key} className={active ? "cgp-edge-row--active" : undefined}>
                    <td className="cgp-edge-conf">
                      <Confidence value={edge.confidence} />
                    </td>
                    <td className="cgp-edge-pair">
                      <button className="cgp-linkish" onClick={() => onSelectNode(edge.from)}>
                        {name(edge.from)}
                      </button>
                      <ArrowRight size={11} />
                      <button className="cgp-linkish" onClick={() => onSelectNode(edge.to)}>
                        {name(edge.to)}
                      </button>
                    </td>
                    <td className="cgp-edge-kind">{edgeKindLabel(edge.kind)}</td>
                    <td className="cgp-edge-evidence">
                      <button
                        className="cgp-linkish cgp-mono"
                        onClick={() => onOpen(edge.evidence_path, edge.evidence_line)}
                        title="Belegstelle in der Werkstatt öffnen"
                      >
                        {edge.evidence_path}:{edge.evidence_line}
                      </button>
                      {edge.occurrences > 1 && <em> · {edge.occurrences}×</em>}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default CodeEvidencePanel;
