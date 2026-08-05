/**
 * „Warum bricht B, wenn ich A ändere" — der kürzeste Aufrufpfad.
 *
 * Die Rekursion in `cs-graph` verfolgt **nur** calls/reads/writes. Ein leeres
 * Ergebnis heisst deshalb „kein Aufrufpfad", nicht „keine Beziehung": zwei
 * Symbole können über Vererbung, Typen oder eine Route verbunden sein und hier
 * trotzdem nichts liefern. Die Anzeige sagt das ausdrücklich — sonst läse sich
 * ein leeres Ergebnis als Aussage, die der Graph gar nicht macht.
 */
import { useQuery } from "@tanstack/react-query";
import { ArrowDown, Route } from "lucide-react";

import { api } from "../../api";
import type { CodeNodeId } from "../../types";

/** Nur, was für die Enden gebraucht wird — passt auf Hit *und* Detail. */
export type PathEnd = { id: CodeNodeId; qualified: string };

export type CodePathPanelProps = {
  projectId: string;
  fromNode: PathEnd | null;
  toNode: PathEnd | null;
  onPickTarget: () => void;
  onSelectNode: (nodeId: CodeNodeId) => void;
  onOpen: (path: string, line: number) => void;
};

export function CodePathPanel({
  projectId,
  fromNode,
  toNode,
  onPickTarget,
  onSelectNode,
  onOpen,
}: CodePathPanelProps) {
  const query = useQuery({
    queryKey: ["codegraph", "path", projectId, fromNode?.id, toNode?.id],
    queryFn: () => api.codegraph.path(projectId, fromNode!.id, toNode!.id),
    enabled: Boolean(fromNode && toNode),
  });

  if (!fromNode) {
    return (
      <p className="muted cg-empty">
        Wähle links ein Symbol als Startpunkt — dann kannst du fragen, wie es mit einem zweiten
        zusammenhängt.
      </p>
    );
  }

  return (
    <div className="cgp-pane cgp-path">
      <div className="cgp-pane-head">
        <Route size={14} />
        <strong>Aufrufpfad</strong>
        <span className="cgp-spacer" />
        <button className="wk-btn" onClick={onPickTarget}>
          {toNode ? "Ziel neu wählen" : "Ziel wählen"}
        </button>
      </div>
      <div className="cgp-pane-body">
        <p className="cgp-path-ends">
          <span className="cgp-mono">{fromNode.qualified}</span>
          <ArrowDown size={13} />
          <span className="cgp-mono">{toNode ? toNode.qualified : "…"}</span>
        </p>

        {!toNode && (
          <p className="muted cg-fineprint">
            Der Zielknoten wird auf der Karte oder in der Suche mit „Pfad hierher" gesetzt.
          </p>
        )}

        {toNode && query.isLoading && <p className="muted">wird gesucht …</p>}

        {toNode && query.data && query.data.path === null && (
          <p className="muted cg-fineprint">
            <strong>Kein Aufrufpfad.</strong> Gesucht wurde entlang von <em>ruft auf</em>,{" "}
            <em>liest</em> und <em>schreibt</em> — andere Beziehungen (Vererbung, Typen, Routen)
            zählen hier nicht. Es kann also durchaus einen Zusammenhang geben, nur keinen, den
            diese Suche verfolgt. Oder der Weg läuft über eine dynamische Lücke.
          </p>
        )}

        {toNode && query.data?.path && query.data.path.length > 0 && (
          <ol className="cgp-path-steps">
            {query.data.path.map((step, index) => (
              <li key={`${step.id}-${index}`}>
                <button className="cgp-linkish" onClick={() => onSelectNode(step.id)}>
                  {step.qualified || step.name}
                </button>
                <button
                  className="cgp-linkish cgp-mono cgp-path-loc"
                  onClick={() => onOpen(step.path, step.line)}
                  title="In der Werkstatt öffnen"
                >
                  {step.path}:{step.line}
                </button>
              </li>
            ))}
          </ol>
        )}

        {query.isError && <p className="cg-notice cg-notice--error">{String(query.error)}</p>}
      </div>
    </div>
  );
}

export default CodePathPanel;
