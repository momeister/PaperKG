/**
 * Ein Knoten auf der Karte.
 *
 * `memo` ist hier nicht Feintuning: xyflow rendert bei jedem Zoomschritt alle
 * sichtbaren Knoten neu, und bei 600 Stück wäre das spürbar.
 *
 * Drei Dinge, die der Knoten aussagen muss:
 *
 *   * **Wo er steht.** Links kommt herein, rechts geht hinaus — der Ausklapppfeil
 *     zeigt deshalb in die Richtung, in die er weiterführt, nicht nach unten.
 *   * **Eine dynamische Lücke sieht anders aus als alles andere** — gestrichelt,
 *     rot, mit ◆. Sie wegzulassen wäre die bequemere Karte und die unehrlichere:
 *     an dieser Stelle *endet* die Analyse, und das ist eine Aussage über den
 *     Code, keine Lücke in der Anzeige.
 *   * **Wenn ein Nachbar wichtiger ist als das betrachtete Symbol**, wird er
 *     markiert. Man klickt oft auf eine Hilfsfunktion und will eigentlich die
 *     Stelle darunter.
 */
import { memo } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";
import { Handle, Position } from "@xyflow/react";

import type { CodeNodeKind } from "../../types";
import { kindLabel } from "./shared";

export type CodeFlowNodeData = {
  label: string;
  qualified: string;
  kind: CodeNodeKind;
  path: string;
  line: number;
  relevance: number;
  width: number;
  column: number;
  expanded: boolean;
  isFocus: boolean;
  /** Relevanter als das betrachtete Symbol — lohnt einen zweiten Blick. */
  stronger: boolean;
  /** Kurzform der Signatur; nur am Fokus, sonst würde die Karte zur Textwand. */
  signature?: string | null;
  onExpand: () => void;
};

function CodeFlowNodeInner({ data, selected }: { data: CodeFlowNodeData; selected?: boolean }) {
  const gap = data.kind === "dynamic_gap";
  const leftward = data.column < 0;
  const classes = [
    "cgp-node",
    `cgp-node--${data.kind}`,
    data.isFocus ? "cgp-node--focus" : "",
    selected ? "cgp-node--selected" : "",
    gap ? "cgp-node--gap" : "",
    data.stronger ? "cgp-node--stronger" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      className={classes}
      style={{ width: data.width }}
      title={
        gap
          ? "Hier endet die statische Analyse — Reflection, eval oder Dependency Injection."
          : `${data.qualified}\n${data.path}:${data.line}${
              data.stronger ? "\n\nRelevanter als das betrachtete Symbol." : ""
            }`
      }
    >
      <Handle type="target" position={Position.Left} className="cgp-handle" />
      <div className="cgp-node-head">
        <span className="cgp-node-kind">
          {gap ? "◆ " : ""}
          {kindLabel(data.kind)}
        </span>
        {data.stronger && (
          <span className="cgp-node-flag" title="Relevanter als das betrachtete Symbol">
            ▲
          </span>
        )}
        {!data.expanded && !gap && !data.isFocus && (
          <button
            className="cgp-node-expand"
            title={leftward ? "Aufrufer dieses Symbols nachladen" : "Was dieses Symbol aufruft nachladen"}
            onClick={(event) => {
              event.stopPropagation();
              data.onExpand();
            }}
          >
            {leftward ? <ChevronLeft size={12} /> : <ChevronRight size={12} />}
          </button>
        )}
      </div>
      <span className="cgp-node-name">{data.label}</span>
      {data.isFocus && data.signature && (
        <span className="cgp-node-sig" title={data.signature}>
          {data.signature}
        </span>
      )}
      <span className="cgp-node-bar">
        <span
          className="cgp-node-bar-fill"
          style={{ width: `${Math.max(2, Math.round((data.relevance ?? 0) * 100))}%` }}
        />
      </span>
      <Handle type="source" position={Position.Right} className="cgp-handle" />
    </div>
  );
}

export const CodeFlowNode = memo(CodeFlowNodeInner);
export default CodeFlowNode;
