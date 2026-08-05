/**
 * Diagramme: Klassenhierarchie und Aufruffolge um ein Symbol.
 *
 * Ein Diagramm ist das Autoritativste, was dieses Werkzeug ausgeben kann — es
 * sieht aus wie etwas, das jemand nachgeschlagen hat. Genau deshalb ist die
 * Regel hier am wichtigsten: **eine geratene Kante darf nicht aussehen wie eine
 * belegte.** Umgesetzt doppelt: gestrichelt statt durchgezogen im Bild, und
 * darunter eine Liste jeder Kante mit ●◐○ und `datei:zeile`, über die man an die
 * Belegstelle springt. Das Bild allein könnte man nicht nachprüfen.
 *
 * mermaid wird **dynamisch** geladen. Es ist gross, und der Entry-Chunk der App
 * wird von fünf Webviews geparst; wer nie ein Diagramm öffnet, soll es nie
 * herunterladen.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { AlertTriangle, GitBranch, Workflow } from "lucide-react";

import { api } from "../api";
import { THEME_META, useAppState } from "../state";
import type { CodeConfidence, CodeDiagram } from "../types";

const CONFIDENCE_MARKER: Record<CodeConfidence, string> = {
  measured: "◆",
  verified: "●",
  resolved: "◐",
  guessed: "○",
};

/** Nur `verified`/`measured`/`resolved` sind belegt; `guessed` ist geraten. */
function isSolid(confidence: CodeConfidence): boolean {
  return confidence !== "guessed";
}

/** mermaid-Bezeichner vertragen keine Punkte, Klammern oder Bindestriche. */
function safeId(name: string): string {
  const cleaned = name.replace(/[^\w]/g, "_");
  return /^[A-Za-z_]/.test(cleaned) ? cleaned : `n_${cleaned}`;
}

export function classDiagramSource(diagram: CodeDiagram): string {
  const lines = ["classDiagram"];
  for (const entry of diagram.classes) {
    const id = safeId(entry.name);
    if (entry.members.length === 0) {
      lines.push(`  class ${id}`);
      continue;
    }
    lines.push(`  class ${id} {`);
    for (const member of entry.members.slice(0, 12)) {
      lines.push(`    ${safeId(member.name)}()`);
    }
    lines.push("  }");
  }
  for (const edge of diagram.inherits) {
    // Gestrichelt heisst geraten. mermaid kennt für Vererbung nur eine
    // Linienart, also übernimmt `..|>` (Realisierung) die Rolle des Zweifels.
    const arrow = isSolid(edge.confidence) ? "<|--" : "<|..";
    lines.push(`  ${safeId(edge.to)} ${arrow} ${safeId(edge.from)}`);
  }
  return lines.join("\n");
}

export function sequenceDiagramSource(diagram: CodeDiagram): string {
  const lines = ["sequenceDiagram"];
  const seen = new Set<string>();
  for (const step of diagram.sequence) {
    for (const name of [step.from, step.to]) {
      const id = safeId(name);
      if (!seen.has(id)) {
        seen.add(id);
        lines.push(`  participant ${id} as ${name}`);
      }
    }
  }
  for (const step of diagram.sequence) {
    // Gestrichelter Pfeil = über Namensgleichheit geraten.
    const arrow = isSolid(step.confidence) ? "->>" : "-->>";
    const suffix = step.candidates > 1 ? ` (${step.candidates} Kandidaten)` : "";
    lines.push(`  ${safeId(step.from)}${arrow}${safeId(step.to)}: ${step.to}${suffix}`);
  }
  return lines.join("\n");
}

export type CodeDiagramPanelProps = {
  projectId: string;
  nodeId: string | null;
  onOpenSymbol?: (path: string, line: number) => void;
};

export function CodeDiagramPanel({ projectId, nodeId, onOpenSymbol }: CodeDiagramPanelProps) {
  const { theme } = useAppState();
  const [kind, setKind] = useState<"class" | "sequence">("class");
  const [svg, setSvg] = useState<string>("");
  const [renderError, setRenderError] = useState<string | null>(null);
  const renderToken = useRef(0);

  const diagram = useQuery({
    queryKey: ["codegraph", "diagram", projectId, nodeId, kind],
    queryFn: () => api.codegraph.diagram(projectId, nodeId!, kind),
    enabled: Boolean(nodeId),
  });

  const source = useMemo(() => {
    if (!diagram.data) return "";
    return kind === "class" ? classDiagramSource(diagram.data) : sequenceDiagramSource(diagram.data);
  }, [diagram.data, kind]);

  const edges = useMemo(() => {
    if (!diagram.data) return [];
    return kind === "class"
      ? diagram.data.inherits.map((edge) => ({ ...edge, candidates: 1 }))
      : diagram.data.sequence.map((step) => ({ ...step, kind: "calls" as const }));
  }, [diagram.data, kind]);

  useEffect(() => {
    if (!source) {
      setSvg("");
      return;
    }
    const token = ++renderToken.current;
    setRenderError(null);
    (async () => {
      try {
        const mermaid = (await import("mermaid")).default;
        mermaid.initialize({
          startOnLoad: false,
          securityLevel: "strict",
          theme: THEME_META[theme].scheme === "dark" ? "dark" : "default",
        });
        const { svg: rendered } = await mermaid.render(`cg-diagram-${token}`, source);
        // Ein zwischenzeitlicher Wechsel darf das alte Bild nicht nachliefern.
        if (renderToken.current === token) setSvg(rendered);
      } catch (error) {
        if (renderToken.current === token) {
          setRenderError(error instanceof Error ? error.message : String(error));
        }
      }
    })();
  }, [source, theme]);

  if (!nodeId) {
    return <p className="muted cg-empty">Wähle in der Suche oder im Überblick ein Symbol.</p>;
  }

  return (
    <>
      <div className="cg-diagram-kinds">
        <button className={kind === "class" ? "active" : ""} onClick={() => setKind("class")}>
          <GitBranch size={13} /> Klassen
        </button>
        <button className={kind === "sequence" ? "active" : ""} onClick={() => setKind("sequence")}>
          <Workflow size={13} /> Aufruffolge
        </button>
      </div>

      <div className="cg-scroll">
        {diagram.isLoading && <p className="muted">wird geladen …</p>}
        {diagram.data && edges.length === 0 && (
          <p className="muted cg-fineprint">
            {kind === "class"
              ? "Für dieses Symbol gibt es keine Vererbungsbeziehungen."
              : "Von diesem Symbol geht kein Aufruf aus."}
          </p>
        )}
        {renderError && (
          <div className="cg-notice cg-notice--error">
            <AlertTriangle size={15} />
            <span>Diagramm nicht darstellbar: {renderError}</span>
          </div>
        )}
        {svg && (
          <div
            className="cg-diagram"
            // mermaid erzeugt das SVG selbst und läuft mit securityLevel "strict"
            // (kein Fremd-HTML, keine Klick-Handler aus dem Diagrammtext).
            dangerouslySetInnerHTML={{ __html: svg }}
          />
        )}

        {edges.length > 0 && (
          <section className="cg-section">
            <h4 title="Ohne diese Liste wäre im Bild nicht zu sehen, welche Kante belegt und welche geraten ist.">
              Kanten mit Beleg <span className="cg-count">{edges.length}</span>
            </h4>
            {edges.map((edge, index) => (
              <div key={`${edge.from}-${edge.to}-${index}`} className="cg-row cg-row--symbol">
                <span className="cg-row-main">
                  <span className={`cg-conf cg-conf--${edge.confidence}`}>
                    {CONFIDENCE_MARKER[edge.confidence]}
                  </span>{" "}
                  <span className="cg-qualified">
                    {edge.from} → {edge.to}
                  </span>
                </span>
                <button
                  className="cg-row-meta cg-row-link"
                  onClick={() => onOpenSymbol?.(edge.evidence_path, edge.evidence_line)}
                  title="Belegstelle im Editor öffnen"
                >
                  {edge.evidence_path}:{edge.evidence_line}
                  {edge.candidates > 1 && <em> · {edge.candidates} Kandidaten</em>}
                </button>
              </div>
            ))}
            <p className="cg-legend">
              ● verifiziert &nbsp; ◐ aufgelöst &nbsp; ○ vermutet (im Bild gestrichelt) &nbsp; ◆ gemessen
            </p>
          </section>
        )}
      </div>
    </>
  );
}

export default CodeDiagramPanel;
