/** Auswirkungsanalyse — „was bricht, wenn ich das ändere?", vor der Ausführung.

 * Die Frage, die der Graph bisher nicht beantwortet hat: rückwärts vom
 * betrachteten Symbol aus, wer wird alles mitrissen. Hop-Distanz und
 * schwächste Sicherheitsstufe je erreichtem Knoten; dazu die erreichenden
 * Tests, die dynamischen Lücken als ausdrückliche Grenze und die betroffenen
 * Dateien mit churn/risk.
 *
 * Ohne LLM, ohne Modell — reine Graph-Traversierung. Genau das ist die
 * Kernanforderung: die Folge muss *vor* der Ausführung sichtbar sein, nicht
 * erst im roten Test danach.
 */
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle } from "lucide-react";
import { api } from "../../api";
import type { CodeImpact, CodeNodeId, CodeSymbolHit } from "../../types";
import { Confidence, ConfidenceLegend, Section, SymbolRow, kindLabel } from "./shared";

export function ImpactPanel({
  projectId,
  nodeId,
  onFocus,
  onOpen,
}: {
  projectId: string | null;
  nodeId: CodeNodeId | null;
  onFocus: (hit: CodeSymbolHit) => void;
  onOpen: (path: string, line: number) => void;
}) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["codegraph", "impact", projectId, nodeId],
    queryFn: () => api.codegraph.impact(projectId!, nodeId!),
    enabled: Boolean(projectId && nodeId),
  });

  if (!projectId || !nodeId) {
    return <div className="cgp-empty">Wähle ein Symbol, um sein Umfeld zu sehen.</div>;
  }
  if (isLoading) return <div className="cgp-empty">Berechne das Umfeld…</div>;
  if (error) return <div className="cgp-empty cgp-error">{String(error)}</div>;
  if (!data) return null;

  const direct = data.reached.filter((r) => r.hops === 1);
  const transitive = data.reached.filter((r) => r.hops > 1);
  return (
    <div className="cg-impact">
      <div className="cg-impact-summary">
        <span>
          <b>{data.reached.length}</b> Symbole erreicht
        </span>
        <span>
          <b>{data.files.length}</b> Dateien berührt
        </span>
        <span>
          <b>{data.tests.length}</b> Tests decken die Stelle ab
        </span>
        {data.dynamic_gaps.length > 0 && (
          <span>
            <b>{data.dynamic_gaps.length}</b> dynamische Lücken
          </span>
        )}
        {data.truncated && (
          <span className="cg-impact-truncated" title="Das Budget war erschöpft — der Radius ist gekürzt, nicht vollständig.">
            gekürzt
          </span>
        )}
      </div>

      <Section icon={<AlertTriangle size={13} />} title="Direkt betroffen" hint="Wer dieses Symbol unmittelbar aufruft, liest oder schreibt.">
        {data.direct_callers.length === 0 ? (
          <div className="cgp-empty">Keine direkten Aufrufer.</div>
        ) : (
          <ul className="cg-impact-list">
            {data.direct_callers.map((c) => (
              <li key={`${c.node.id}-${c.kind}`} className="cg-impact-row">
                <SymbolRow hit={c.node} onFocus={onFocus} onOpen={onOpen} />
                <Confidence value={c.confidence} candidates={c.candidates} />
              </li>
            ))}
          </ul>
        )}
      </Section>

      {transitive.length > 0 && (
        <Section icon={<AlertTriangle size={13} />} title="Transitiv erreicht" hint="Hop-Distanz und schwächste Kante entlang des besten Pfades.">
          <ul className="cg-impact-list">
            {transitive.map((entry) => (
              <li key={entry.node.id} className="cg-impact-row">
                <SymbolRow hit={entry.node} onFocus={onFocus} onOpen={onOpen} />
                <span className="cg-impact-hops">{entry.hops} Sprünge</span>
                <Confidence value={entry.confidence} />
              </li>
            ))}
          </ul>
        </Section>
      )}

      {data.tests.length > 0 && (
        <Section icon={<AlertTriangle size={13} />} title="Abdeckende Tests" hint="Erreichte Symbole der Art Test — was bei einem Fehler zuerst rot wird.">
          <ul className="cg-impact-list">
            {data.tests.map((t) => (
              <li key={t.id} className="cg-impact-row">
                <SymbolRow hit={t} onFocus={onFocus} onOpen={onOpen} />
                <span className="cg-impact-kind">{kindLabel(t.kind)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      {data.dynamic_gaps.length > 0 && (
        <Section
          icon={<AlertTriangle size={13} />}
          title="Dynamische Lücken"
          hint="Dort endet die statische Analyse — das Ziel steht erst zur Laufzeit fest. Keine Vermutung, sondern die Grenze."
        >
          <ul className="cg-impact-list">
            {data.dynamic_gaps.map((g) => (
              <li key={g.id} className="cg-impact-row">
                <SymbolRow hit={g} onFocus={onFocus} onOpen={onOpen} />
                <span className="cg-impact-kind">{kindLabel(g.kind)}</span>
              </li>
            ))}
          </ul>
        </Section>
      )}

      <Section icon={<AlertTriangle size={13} />} title="Betroffene Dateien" hint="git churn/risk summieren die erreichten Symbole der Datei.">
        <table className="cg-impact-files">
          <thead>
            <tr>
              <th>Datei</th>
              <th>Symbole</th>
              <th>churn</th>
              <th>risk</th>
            </tr>
          </thead>
          <tbody>
            {data.files.map((f) => (
              <tr key={f.path}>
                <td>
                  <code>{f.path}</code>
                </td>
                <td>{f.symbols}</td>
                <td>{f.churn}</td>
                <td>{f.risk}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </Section>

      <ConfidenceLegend />
    </div>
  );
}