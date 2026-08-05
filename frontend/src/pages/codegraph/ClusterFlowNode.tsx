/**
 * Eine Kachel auf der Bereichskarte.
 *
 * Ein Bereich muss vier Fragen beantworten, bevor man ihn anklickt: **wie
 * heisst er**, **wie gross ist er**, **woraus besteht er** und **wie viel davon
 * ist geraten**. Die letzte steht hier aus demselben Grund wie überall sonst:
 * eine Zusammenfassung, deren Unsicherheit man erst zwei Klicks später sieht,
 * wird als Tatsache gelesen.
 *
 * Der vom Modell vergebene Name steht gross, der Ordnerpfad klein darunter —
 * nie nur der Name. Wer prüfen will, ob „Datenbeschaffung" stimmt, braucht den
 * Ordner, in dem er nachsehen kann.
 */
import { memo } from "react";
import { ChevronRight } from "lucide-react";
import { Handle, Position } from "@xyflow/react";

import type { CodeNodeKind } from "../../types";
import { kindLabel } from "./shared";

export type ClusterFlowNodeData = {
  path: string;
  label: string;
  purpose: string | null;
  labelSource: "directory" | "llm";
  labelStale: boolean;
  symbols: number;
  files: number;
  kinds: [CodeNodeKind, number][];
  guessedShare: number;
  hasChildren: boolean;
  width: number;
  onOpen: () => void;
};

/** Die drei häufigsten Symbolarten als Balken — das Profil eines Bereichs. */
function KindBars({ kinds, total }: { kinds: [CodeNodeKind, number][]; total: number }) {
  const top = kinds.slice(0, 3);
  if (!top.length || total <= 0) return null;
  return (
    <span className="cgp-cluster-bars">
      {top.map(([kind, count]) => (
        <span
          key={kind}
          className={`cgp-cluster-bar cgp-cluster-bar--${kind}`}
          style={{ flexGrow: Math.max(1, count) }}
          title={`${count}× ${kindLabel(kind)}`}
        />
      ))}
    </span>
  );
}

function ClusterFlowNodeInner({
  data,
  selected,
}: {
  data: ClusterFlowNodeData;
  selected?: boolean;
}) {
  const guessPercent = Math.round((data.guessedShare ?? 0) * 100);
  const classes = [
    "cgp-cluster",
    selected ? "cgp-cluster--selected" : "",
    data.labelStale ? "cgp-cluster--stale" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      style={{ width: data.width }}
      title={[
        data.path,
        data.purpose ?? "",
        `${data.symbols} Symbole in ${data.files} Dateien`,
        data.labelStale
          ? "Der hinterlegte Name stammt von einem früheren Stand dieses Bereichs."
          : "",
      ]
        .filter(Boolean)
        .join("\n")}
    >
      <Handle type="target" position={Position.Left} className="cgp-handle" />

      <div className="cgp-cluster-head">
        <span className="cgp-cluster-name">{data.label}</span>
        {data.labelStale && (
          <span className="cgp-cluster-flag" title="Name stammt von einem früheren Stand">
            veraltet
          </span>
        )}
        {data.hasChildren && (
          <button
            className="cgp-cluster-open"
            title="Eine Ebene tiefer"
            onClick={(event) => {
              event.stopPropagation();
              data.onOpen();
            }}
          >
            <ChevronRight size={12} />
          </button>
        )}
      </div>

      {/* Immer der Pfad, auch wenn ein Modell einen schöneren Namen gefunden hat:
          ohne ihn liesse sich der Name nicht nachprüfen. */}
      <span className="cgp-cluster-path">{data.path}</span>
      {data.purpose && <span className="cgp-cluster-purpose">{data.purpose}</span>}

      <KindBars kinds={data.kinds} total={data.symbols} />

      <span className="cgp-cluster-foot">
        <span>{data.symbols} Symbole</span>
        <span>{data.files} Dateien</span>
        {guessPercent > 0 && (
          <span className="cg-gauge" title="Anteil geratener Beziehungen aus diesem Bereich">
            {guessPercent}% vermutet
          </span>
        )}
      </span>

      <Handle type="source" position={Position.Right} className="cgp-handle" />
    </div>
  );
}

export const ClusterFlowNode = memo(ClusterFlowNodeInner);
export default ClusterFlowNode;
