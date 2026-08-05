/**
 * Der Beleg zur Zahl auf dem Pfeil.
 *
 * Auf der Bereichskarte steht „128 Aufrufe ◐". Das ist eine Zusammenfassung, und
 * eine Zusammenfassung, die sich nicht öffnen lässt, ist eine Behauptung. Hier
 * stehen die 128 einzeln, schwächster Beleg zuerst, jeweils mit `datei:zeile`.
 *
 * Bewusst eine eigene Komponente statt eines zweiten Modus in
 * `CodeEvidencePanel`: dort ist die Datenquelle der Kartenzustand im Speicher,
 * hier eine Abfrage. Das Vokabular teilen sich beide trotzdem — `Confidence` und
 * `edgeKindLabel` kommen aus `shared.tsx`, damit ●◐○◆ überall dasselbe heisst.
 */
import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { ArrowRight } from "lucide-react";

import { api } from "../../api";
import type { CodeClusterEdge, CodeEdgeKind, CodeNeighbour, CodeNodeId } from "../../types";
import { Confidence, edgeKindLabel } from "./shared";

export type ClusterEvidencePanelProps = {
  projectId: string | null;
  edge: CodeClusterEdge | null;
  edgeKinds: CodeEdgeKind[];
  onOpen: (path: string, line: number) => void;
  onSelectNode: (nodeId: CodeNodeId) => void;
};

const RANK = ["guessed", "resolved", "verified", "measured"];

export function ClusterEvidencePanel({
  projectId,
  edge,
  edgeKinds,
  onOpen,
  onSelectNode,
}: ClusterEvidencePanelProps) {
  const [onlyGuessed, setOnlyGuessed] = useState(false);

  const detail = useQuery({
    queryKey: ["codegraph", "clusterEdge", projectId, edge?.from, edge?.to, edgeKinds.join(",")],
    queryFn: () => api.codegraph.clusterEdge(projectId!, edge!.from, edge!.to, edgeKinds, 200),
    enabled: Boolean(projectId && edge),
  });

  const rows = useMemo(() => {
    const all: CodeNeighbour[] = detail.data ?? [];
    const filtered = onlyGuessed ? all.filter((one) => one.confidence === "guessed") : all;
    return [...filtered].sort(
      (a, b) => RANK.indexOf(a.confidence) - RANK.indexOf(b.confidence),
    );
  }, [detail.data, onlyGuessed]);

  return (
    <div className="cgp-pane cgp-evidence">
      <div className="cgp-pane-head">
        <strong>Kanten mit Beleg</strong>
        {edge && (
          <span className="cgp-edge-pair cgp-mono">
            {edge.from} <ArrowRight size={11} /> {edge.to}
          </span>
        )}
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
      </div>
      <div className="cgp-pane-body">
        {!edge ? (
          <p className="muted cg-empty">
            Klick einen Pfeil auf der Karte — hier stehen dann die echten Kanten dahinter, mit
            Belegstelle und Sicherheitsstufe.
          </p>
        ) : detail.isLoading ? (
          <p className="muted cg-empty">Belege werden geholt …</p>
        ) : detail.isError ? (
          <p className="cg-notice cg-notice--error">
            {detail.error instanceof Error ? detail.error.message : "Belege nicht ladbar"}
          </p>
        ) : rows.length === 0 ? (
          <p className="muted cg-empty">
            {onlyGuessed
              ? "Keine geratene Kante zwischen diesen Bereichen — alles hier ist belegt."
              : "Keine Kanten der gewählten Arten."}
          </p>
        ) : (
          <table className="cgp-edge-table">
            <tbody>
              {rows.map((one, index) => (
                <tr key={`${one.node.id}|${one.kind}|${index}`}>
                  <td className="cgp-edge-conf">
                    <Confidence value={one.confidence} />
                  </td>
                  <td className="cgp-edge-pair">
                    <button className="cgp-linkish" onClick={() => onSelectNode(one.node.id)}>
                      {one.node.name}
                    </button>
                  </td>
                  <td className="cgp-edge-kind">{edgeKindLabel(one.kind)}</td>
                  <td className="cgp-edge-evidence">
                    <button
                      className="cgp-linkish cgp-mono"
                      onClick={() => onOpen(one.evidence_path, one.evidence_line)}
                      title="Belegstelle in der Werkstatt öffnen"
                    >
                      {one.evidence_path}:{one.evidence_line}
                    </button>
                    {one.occurrences > 1 && <em> · {one.occurrences}×</em>}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </div>
  );
}

export default ClusterEvidencePanel;
