/** Knäuel — Spaghetti-Löser. Diagnose ohne LLM, Vorschlag mit LLM, geprüft.

 * Obere Hälfte (ohne Modell, immer da): Hotspots — jede Fundstelle trägt, welche
 * Regel sie gerissen hat und mit welchem Messwert. Darunter Ringe (Tarjan-SCC),
 * mit Belegkanten und schwächster Sicherheitsstufe.
 *
 * Untere Hälfte (mit Modell): zu einem *gewählten* Symbol ein Refactor-Vorschlag.
 * Vollständiger neuer Dateiinhalt, geprüft (Pfad + Syntax), und auf Knopfdruck
 * in einer Sandbox angewendet + getestet — Übernahme in den Hauptbaum ist ein
 * eigener Schritt. Ohne chosenes Symbol oder ohne Modell steht nur die Diagnose,
 * und das ist kein Bug, sondern der halbe Nutzen.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { GitBranch, Wand2 } from "lucide-react";
import { api, streamCodeRefactorPropose, streamCodeRefactorTry } from "../../api";
import type {
  CodeNodeId,
  CodeRefactorApplied,
  CodeRefactorEvent,
  CodeRefactorTryEvent,
  Cycle,
  Hotspot,
  RefactorProposal,
} from "../../types";
import { Confidence, ConfidenceLegend, Section, kindLabel } from "./shared";

type CycleLevel = "file" | "symbol";

export function TanglePanel({
  projectId,
  nodeId,
  provider,
  model,
  onFocus,
  onOpen,
}: {
  projectId: string | null;
  nodeId: CodeNodeId | null;
  provider: string | null;
  model: string | null;
  onFocus: (id: CodeNodeId) => void;
  onOpen: (path: string, line: number) => void;
}) {
  const [cycleLevel, setCycleLevel] = useState<CycleLevel>("file");
  const [activity, setActivity] = useState<string | null>(null);
  const [proposal, setProposal] = useState<RefactorProposal | null>(null);
  const [proposeError, setProposeError] = useState<string | null>(null);
  const [tryOutput, setTryOutput] = useState<string | null>(null);
  const [tryApplied, setTryApplied] = useState<CodeRefactorApplied | null>(null);
  const [busy, setBusy] = useState(false);

  const hotspots = useQuery({
    queryKey: ["codegraph", "hotspots", projectId],
    queryFn: () => api.codegraph.hotspots(projectId!),
    enabled: Boolean(projectId),
  });
  const cycles = useQuery({
    queryKey: ["codegraph", "cycles", projectId, cycleLevel],
    queryFn: () => api.codegraph.cycles(projectId!, { level: cycleLevel }),
    enabled: Boolean(projectId),
  });

  if (!projectId) return <div className="cgp-empty">Wähle ein Projekt.</div>;

  const cycleList: Cycle[] = cycles.data?.cycles ?? [];

  async function propose() {
    if (!projectId || !nodeId) return;
    setBusy(true);
    setProposal(null);
    setProposeError(null);
    setActivity("formuliere den Vorschlag");
    try {
      await streamCodeRefactorPropose(
        projectId,
        nodeId,
        { provider, model },
        (event: CodeRefactorEvent) => {
          if (event.event === "activity") setActivity(event.text);
          else if (event.event === "failed") setProposeError(event.error);
          else if (event.event === "done") setProposal(event.proposal);
        },
      );
    } catch (error) {
      setProposeError(String(error));
    } finally {
      setBusy(false);
      setActivity(null);
    }
  }

  async function tryInSandbox() {
    if (!projectId || !nodeId || !proposal) return;
    setBusy(true);
    setTryOutput(null);
    setTryApplied(null);
    setActivity("schreibe den Vorschlag in die Sandbox");
    try {
      await streamCodeRefactorTry(
        projectId,
        nodeId,
        { proposal, test_command: null, timeout: 300 },
        (event: CodeRefactorTryEvent) => {
          if (event.event === "activity") setActivity(event.text);
          else if (event.event === "applied") setTryApplied(event.applied);
          else if (event.event === "failed") setTryOutput(event.error);
          else if (event.event === "done") {
            const run = event.run;
            setTryOutput(
              `$ ${run.command.join(" ")}\n[exit ${run.returncode}, ${run.duration_s}s]\n\n${run.stdout}\n${run.stderr}`,
            );
          }
        },
      );
    } catch (error) {
      setTryOutput(String(error));
    } finally {
      setBusy(false);
      setActivity(null);
    }
  }

  return (
    <div className="cg-tangle">
      <Section icon={<Wand2 size={13} />} title="Hotspots" hint="Symbole, die eine gemessene Regel reißen. Jede Fundstelle trägt ihre Regel und ihren Messwert — keine erfundene Gesamtnote.">
        {hotspots.isLoading ? (
          <div className="cgp-empty">Messe…</div>
        ) : hotspots.error ? (
          <div className="cgp-empty cgp-error">{String(hotspots.error)}</div>
        ) : !hotspots.data || hotspots.data.length === 0 ? (
          <div className="cgp-empty">Keine Hotspots mit den Standard-Schwellen.</div>
        ) : (
          <ul className="cg-tangle-list">
            {hotspots.data.map((h: Hotspot) => (
              <li key={h.node.id} className="cg-tangle-row">
                <button className="cg-row-main" onClick={() => onFocus(h.node.id)} title="Im Inspektor öffnen">
                  <span className="cg-kind">{kindLabel(h.node.kind)}</span>
                  <span className="cg-qualified">{h.node.qualified}</span>
                </button>
                <button className="cg-row-meta cg-row-link" onClick={() => onOpen(h.node.path, h.node.line)}>
                  {h.node.path}:{h.node.line}
                </button>
                <span className="cg-tangle-rules">
                  {h.rules.map((r) => (
                    <span key={r.rule} className="cg-tangle-rule" title={`${r.value} > ${r.threshold}`}>
                      {r.rule} {r.value}
                    </span>
                  ))}
                </span>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section icon={<GitBranch size={13} />} title="Ringe" hint="Tarjan-SCC — Dateien über Imports, Symbole über Calls. Jeder Ring trägt Belegkanten und die schwächste Sicherheitsstufe.">
        <div className="cg-tangle-cyclebar">
          <button className={cycleLevel === "file" ? "active" : ""} onClick={() => setCycleLevel("file")}>
            Dateien
          </button>
          <button className={cycleLevel === "symbol" ? "active" : ""} onClick={() => setCycleLevel("symbol")}>
            Symbole
          </button>
        </div>
        {cycles.isLoading ? (
          <div className="cgp-empty">Suche Ringe…</div>
        ) : !cycleList.length ? (
          <div className="cgp-empty">Keine Ringe auf dieser Ebene — gut.</div>
        ) : (
          <ul className="cg-tangle-cycles">
            {cycleList.map((cycle, i) => (
              <li key={i} className="cg-tangle-cycle">
                <div className="cg-tangle-cycle-head">
                  <span className="cg-tangle-cycle-size">{cycle.size} Knoten</span>
                  <Confidence value={cycle.weakest} />
                </div>
                <div className="cg-tangle-cycle-nodes">
                  {cycle.nodes.map((n) => (
                    <button
                      key={`${n.id}-${n.path}-${n.line}`}
                      className="cg-row-meta cg-row-link"
                      onClick={() => onOpen(n.path, n.line)}
                    >
                      {n.path}:{n.line}
                    </button>
                  ))}
                </div>
                <ul className="cg-tangle-cycle-edges">
                  {cycle.edges.map((e, j) => (
                    <li key={j}>
                      <Confidence value={e.confidence} />
                      <code>{e.evidence_path}:{e.evidence_line}</code>
                      <span className="cg-tangle-edge-kind">{e.kind}</span>
                    </li>
                  ))}
                </ul>
              </li>
            ))}
          </ul>
        )}
      </Section>

      <Section icon={<Wand2 size={13} />} title="Vorschlag" hint="Refactor-Vorschlag zum gewählten Symbol — vollständiger neuer Dateiinhalt, geprüft (Pfad + Syntax), Probelauf in der Sandbox. Verhalten darf sich nicht ändern.">
        {!nodeId ? (
          <div className="cgp-empty">Wähle ein Symbol für einen Vorschlag.</div>
        ) : (
          <div className="cg-tangle-propose">
            <button className="wk-btn" disabled={busy} onClick={propose}>
              Vorschlag machen
            </button>
            {activity && <span className="cg-tangle-activity">{activity}</span>}
            {proposeError && <div className="cgp-empty cgp-error">{proposeError}</div>}
            {proposal && (
              <div className="cg-tangle-proposal">
                <p className="cg-tangle-begruendung">{proposal.begruendung}</p>
                {!proposal.valid && proposal.errors.length > 0 && (
                  <ul className="cg-tangle-errors">
                    {proposal.errors.map((err, i) => (
                      <li key={i}>{err}</li>
                    ))}
                  </ul>
                )}
                <div className="cg-tangle-dateien">
                  {proposal.dateien.map((d) => (
                    <code key={d.pfad}>{d.pfad}</code>
                  ))}
                  {proposal.geloescht.map((p) => (
                    <code key={p} className="cg-tangle-deleted">− {p}</code>
                  ))}
                </div>
                <button
                  className="wk-btn"
                  disabled={busy || !proposal.valid}
                  onClick={tryInSandbox}
                  title="Vorschlag in eine Sandbox schreiben und den Testbefehl laufen lassen"
                >
                  In Sandbox probelaufen
                </button>
                {tryApplied && (
                  <div className="cg-tangle-applied">
                    {tryApplied.written.map((w) => (
                      <span key={`w-${w.path}`}>{w.action === "written" ? "✓" : "⊘"} {w.path}</span>
                    ))}
                    {tryApplied.deleted.map((d) => (
                      <span key={`d-${d.path}`}>− {d.path}</span>
                    ))}
                  </div>
                )}
                {tryOutput && <pre className="cg-sandbox-output">{tryOutput}</pre>}
              </div>
            )}
          </div>
        )}
      </Section>

      <ConfidenceLegend />
    </div>
  );
}