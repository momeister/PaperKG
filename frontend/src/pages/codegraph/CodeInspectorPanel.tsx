/**
 * Der Inspektor: was ein Symbol genau tut.
 *
 * Die Reihenfolge ist die Reihenfolge der Fragen, die man tatsächlich stellt:
 * *Wofür ist das da* (Steckbrief und, auf Wunsch, eine Erklärung vom Modell),
 * *was geht rein und was kommt raus* (der Datenfluss-Block), *worauf beruht
 * das* (Zahlen, Quelltext, Nachbarn).
 *
 * Vier Dinge, die keine Kosmetik sind:
 *
 *   * **Der Steckbrief ist immer da.** Vorher stand hier der Docstring, und wo
 *     keiner im Code steht, stand nichts — das las sich, als wüsste das Werkzeug
 *     nichts, dabei kennt es Signatur, Nebenwirkungen, Aufrufer und Historie.
 *   * `facts.pure` ist `Option<bool>`. `null` heisst **unentschieden**, nicht
 *     „nein" — deshalb erscheint „nebenwirkungsfrei" nur bei `true`.
 *   * `facts.weakest_edge` sagt, worauf die Aufruferzahl beruht. Steht dort
 *     `guessed`, ist „17 Aufrufer" keine Zahl, sondern eine Vermutung.
 *   * **Ist ein Nachbar wichtiger als das Betrachtete**, wird das gesagt. Man
 *     klickt oft eine Hilfsfunktion an und meint die Stelle darunter.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import {
  AlertTriangle,
  ArrowDownToLine,
  ArrowUpFromLine,
  ExternalLink,
  Map as MapIcon,
  Maximize2,
  Minimize2,
  Pencil,
  Route,
  Sparkles,
  TriangleAlert,
} from "lucide-react";

import { api, streamCodeExplain } from "../../api";
import { MetricCard } from "../../components/MetricCard";
import type { CodeExplainEvent, CodeExplanation, CodeNeighbour, CodeNodeId } from "../../types";
import { AnswerText } from "../CodeAskPanel";
import { CodeWhyPanel } from "./CodeWhyPanel";
import { Confidence, NeighbourList, kindLabel } from "./shared";
import { factSummary, looksLikeHelper, sideEffectLabel, strongerNeighbours } from "./summary";

export type CodeInspectorPanelProps = {
  projectId: string;
  nodeId: CodeNodeId | null;
  provider?: string | null;
  model?: string | null;
  onOpen: (path: string, line: number) => void;
  onEdit?: (nodeId: CodeNodeId) => void;
  onFocus: (nodeId: CodeNodeId) => void;
  onShowOnMap: (nodeId: CodeNodeId) => void;
  onPathTo: (nodeId: CodeNodeId) => void;
  /** Nebenspalten einklappen, damit ein Symbol die halbe Seite bekommt. */
  onToggleWide?: () => void;
  wide?: boolean;
};

export function CodeInspectorPanel({
  projectId,
  nodeId,
  provider = null,
  model = null,
  onOpen,
  onEdit,
  onFocus,
  onShowOnMap,
  onPathTo,
  onToggleWide,
  wide = false,
}: CodeInspectorPanelProps) {
  const [explanation, setExplanation] = useState<CodeExplanation | null>(null);
  const [explainState, setExplainState] = useState<"idle" | "running" | "failed">("idle");
  const [explainNote, setExplainNote] = useState<string | null>(null);
  const abortRef = useRef<AbortController | null>(null);

  const blueprint = useQuery({
    queryKey: ["codegraph", "blueprint", projectId, nodeId],
    queryFn: () => api.codegraph.blueprint(projectId, nodeId!),
    enabled: Boolean(nodeId),
  });

  const focus = blueprint.data?.focus;

  // Nur wegen des `stale`-Flags. Der Quelltext selbst steht im Code-Tab — aber
  // ob die Datei sich seit dem Indizieren geändert hat, entscheidet über *jede*
  // Zeilennummer auf dieser Seite, nicht nur über einen Ausschnitt. Ein Panel,
  // das falsche Zeilen zeigt, ohne es zu sagen, wäre schlimmer als eines, das
  // gar nichts zeigt.
  const source = useQuery({
    queryKey: ["codegraph", "source", projectId, focus?.path],
    queryFn: () => api.codegraph.source(projectId, focus!.path),
    enabled: Boolean(focus?.path),
    gcTime: 60_000,
  });

  // Eine Erklärung gilt für genau ein Symbol auf genau einem Stand. Bleibt sie
  // beim Wechsel stehen, liest man sie zum falschen Code.
  useEffect(() => {
    abortRef.current?.abort();
    setExplanation(null);
    setExplainState("idle");
    setExplainNote(null);
  }, [nodeId, projectId]);

  useEffect(() => () => abortRef.current?.abort(), []);

  function explain() {
    if (!nodeId || explainState === "running") return;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    setExplainState("running");
    setExplanation(null);
    setExplainNote("lese das Symbol");
    void streamCodeExplain(
      projectId,
      nodeId,
      { provider, model },
      (event: CodeExplainEvent) => {
        if (event.event === "activity") setExplainNote(event.text);
        else if (event.event === "failed") {
          setExplainState("failed");
          setExplainNote(event.error);
        } else {
          setExplanation(event.answer);
          setExplainState("idle");
          setExplainNote(null);
        }
      },
      controller.signal,
    ).catch((error: unknown) => {
      if (controller.signal.aborted) return;
      setExplainState("failed");
      setExplainNote(error instanceof Error ? error.message : String(error));
    });
  }

  const groups = useMemo(
    () =>
      blueprint.data
        ? [
            { title: "Aufrufer", items: blueprint.data.callers },
            { title: "Aufgerufen", items: blueprint.data.callees },
            { title: "Enthält", items: blueprint.data.children },
          ]
        : [],
    [blueprint.data],
  );

  const stronger = useMemo(
    () => (focus ? strongerNeighbours(focus, groups) : []),
    [focus, groups],
  );

  if (!nodeId) {
    return (
      <div className="cgp-pane cgp-inspector">
        <p className="muted cg-empty">
          Wähle ein Symbol — hier steht dann, was es tut, was hinein- und herausgeht und wie sicher
          das ist.
        </p>
      </div>
    );
  }

  if (blueprint.isLoading) {
    return (
      <div className="cgp-pane cgp-inspector">
        <p className="muted cg-empty">wird geladen …</p>
      </div>
    );
  }

  if (!focus) {
    return (
      <div className="cgp-pane cgp-inspector">
        <p className="muted cg-empty">Symbol nicht im Index.</p>
      </div>
    );
  }

  const facts = focus.facts;
  const metrics = focus.metrics;
  const weakest = facts?.weakest_edge ?? null;

  const onMap = (item: CodeNeighbour) => (
    <button
      className="cgp-iconlink"
      title="Auf der Karte zeigen"
      onClick={() => onShowOnMap(item.node.id)}
    >
      <MapIcon size={12} />
    </button>
  );

  return (
    <div className="cgp-pane cgp-inspector">
      <div className="cgp-pane-head">
        <span className="cg-kind">{kindLabel(focus.kind)}</span>
        <strong className="cgp-inspector-title" title={focus.qualified}>
          {focus.qualified}
        </strong>
        <span className="cgp-spacer" />
        {onToggleWide && (
          <button
            className="wk-iconbtn"
            title={wide ? "Wieder verkleinern" : "Dieses Symbol gross anzeigen"}
            onClick={onToggleWide}
          >
            {wide ? <Minimize2 size={14} /> : <Maximize2 size={14} />}
          </button>
        )}
        {onEdit && (
          <button className="wk-iconbtn" title="Bearbeiten" onClick={() => onEdit(focus.id)}>
            <Pencil size={14} />
          </button>
        )}
        <button
          className="wk-iconbtn"
          title="Pfad von hier zu einem anderen Symbol"
          onClick={() => onPathTo(focus.id)}
        >
          <Route size={14} />
        </button>
        <button
          className="wk-iconbtn"
          title="In der Werkstatt öffnen"
          onClick={() => onOpen(focus.path, focus.span.start_line)}
        >
          <ExternalLink size={14} />
        </button>
      </div>

      <div className="cgp-pane-body">
        <button
          className="cgp-linkish cgp-mono cgp-inspector-loc"
          onClick={() => onOpen(focus.path, focus.span.start_line)}
        >
          {focus.path}:{focus.span.start_line}
          {focus.span.end_line > focus.span.start_line ? `–${focus.span.end_line}` : ""}
        </button>

        {source.data?.stale && (
          <p className="cg-notice cg-notice--error cgp-stale">
            <AlertTriangle size={14} />
            <span>
              Datei hat sich seit dem Indizieren geändert — die Zeilennummern im Graphen passen
              nicht mehr zu dem, was in der Datei steht.
            </span>
          </p>
        )}

        {facts?.signature && <pre className="cgp-sig">{facts.signature}</pre>}

        {/* --- Wofür ist das da ------------------------------------------- */}
        <section className="cg-section cgp-what">
          <h4>
            Wofür das da ist
            <button
              className="wk-btn cgp-explain-btn"
              onClick={explain}
              disabled={explainState === "running"}
              title="Ein Modell erklärt dieses Symbol — mit denselben geprüften Belegen wie eine Antwort."
            >
              <Sparkles size={13} />
              {explainState === "running" ? "erklärt …" : explanation ? "neu erklären" : "erklären"}
            </button>
          </h4>

          {focus.doc ? (
            <p className="cg-doc cgp-doc">{focus.doc}</p>
          ) : (
            <p className="muted cg-fineprint cgp-nodoc">
              Im Code steht kein Kommentar zu diesem Symbol. Was darunter steht, ist aus dem Index
              zusammengefasst — nicht aus dem Code gelesen.
            </p>
          )}

          <ul className="cgp-summary">
            {factSummary(focus).map((line, index) => (
              <li key={index}>{line}</li>
            ))}
          </ul>

          {explainState === "running" && (
            <p className="muted cgp-explain-note">
              <Sparkles size={12} /> {explainNote}
            </p>
          )}
          {explainState === "failed" && (
            <p className="cg-notice cg-notice--error cgp-explain-note">
              <AlertTriangle size={14} /> <span>{explainNote}</span>
            </p>
          )}
          {explanation && (
            <div className="cgp-explanation">
              <AnswerText
                text={explanation.text}
                citations={explanation.citations}
                onOpen={onOpen}
              />
              <p className="cg-row-meta">
                {explanation.verdict_label ?? "geprüft"} · {explanation.model}
              </p>
            </div>
          )}
        </section>

        {/* --- Warum so gebaut ---------------------------------------------
            Eigener Abschnitt und nicht Teil von „wofür das da ist": das eine
            beschreibt, was der Code tut, das andere behauptet eine Absicht.
            Zusammengelegt liesse sich beides nicht mehr auseinanderhalten. */}
        <CodeWhyPanel
          projectId={projectId}
          nodeId={focus.id}
          provider={provider}
          model={model}
          onOpen={onOpen}
        />

        {/* --- Was rein, was raus ------------------------------------------ */}
        {facts && (
          <section className="cg-section cgp-flow">
            <h4>Was hinein- und herausgeht</h4>
            <div className="cgp-flow-grid">
              <div className="cgp-flow-side cgp-flow-side--in">
                <span className="cgp-flow-label">
                  <ArrowDownToLine size={12} /> hinein
                </span>
                {facts.params.length ? (
                  <table className="cgp-params">
                    <tbody>
                      {facts.params.map((param, index) => (
                        <tr key={`${param.name}-${index}`}>
                          <td className="cgp-mono">{param.name}</td>
                          <td className="cgp-mono muted">{param.type_name ?? "—"}</td>
                          <td className="cgp-mono muted">
                            {param.default != null ? `= ${param.default}` : ""}
                          </td>
                        </tr>
                      ))}
                    </tbody>
                  </table>
                ) : (
                  <p className="muted cg-fineprint">Keine Parameter.</p>
                )}
              </div>

              <div className="cgp-flow-side cgp-flow-side--out">
                <span className="cgp-flow-label">
                  <ArrowUpFromLine size={12} /> heraus
                </span>
                <div className="cg-chips">
                  {facts.returns ? (
                    <span className="cg-chip cgp-chip--ok">{facts.returns}</span>
                  ) : (
                    <span className="muted cg-fineprint">Kein Rückgabetyp angegeben.</span>
                  )}
                  {facts.throws.map((item) => (
                    <span key={item} className="cg-chip cgp-chip--warn">
                      wirft {item}
                    </span>
                  ))}
                </div>
                <span className="cgp-flow-label cgp-flow-label--effects">nach aussen</span>
                <div className="cg-chips">
                  {facts.side_effects.length ? (
                    facts.side_effects.map((effect) => (
                      <span key={effect} className="cg-chip cgp-chip--warn" title={effect}>
                        {sideEffectLabel(effect)}
                      </span>
                    ))
                  ) : facts.pure === true ? (
                    <span className="cg-chip cgp-chip--ok">nebenwirkungsfrei</span>
                  ) : (
                    <span className="muted cg-fineprint">
                      Keine gefunden — das heisst nicht „keine". Statisch nicht entscheidbar.
                    </span>
                  )}
                </div>
              </div>
            </div>
          </section>
        )}

        {/* --- Wichtigeres in der Nähe -------------------------------------- */}
        {stronger.length > 0 && (
          <section className="cg-section cgp-stronger">
            <h4 title="Nach Relevanz: PageRank, Reichweite, Änderungshäufigkeit, Risiko.">
              <TriangleAlert size={13} />
              {looksLikeHelper(focus)
                ? "Das hier ist eine kurze Hilfsfunktion — das Gewicht liegt woanders"
                : "Relevanter als dieses Symbol"}
            </h4>
            {stronger.map(({ title, item }) => (
              <div key={`${title}-${item.node.id}`} className="cg-row cg-row--symbol">
                <button className="cg-row-main" onClick={() => onFocus(item.node.id)}>
                  <Confidence value={item.confidence} candidates={item.candidates} />
                  <span className="cg-kind">{title}</span>
                  <span className="cg-qualified">{item.node.qualified}</span>
                </button>
                <button
                  className="cgp-iconlink"
                  title="Auf der Karte zeigen"
                  onClick={() => onShowOnMap(item.node.id)}
                >
                  <MapIcon size={12} />
                </button>
                <span className="cg-row-meta">
                  {Math.round((item.node.relevance ?? 0) * 100)}%
                </span>
              </div>
            ))}
          </section>
        )}

        {/* --- Zahlen ------------------------------------------------------- */}
        <section className="cg-section">
          <h4>Zahlen</h4>
          <div className="metrics-grid compact-metrics cgp-metrics">
            <MetricCard
              label="Relevanz"
              value={`${Math.round((metrics.relevance ?? 0) * 100)}%`}
              tone="blue"
              detail="Struktur, Reichweite, Änderungen, Risiko"
            />
            <MetricCard
              label="Aufrufer"
              value={String(metrics.fan_in)}
              tone={weakest === "guessed" ? "amber" : "neutral"}
              detail={
                weakest
                  ? `schwächster Beleg: ${weakest === "guessed" ? "○ vermutet" : weakest === "resolved" ? "◐ aufgelöst" : weakest === "measured" ? "◆ gemessen" : "● verifiziert"}`
                  : undefined
              }
            />
            <MetricCard label="Ruft auf" value={String(metrics.fan_out)} tone="neutral" />
            <MetricCard
              label="Reichweite"
              value={metrics.reach_depth != null ? String(metrics.reach_depth) : "—"}
              tone="neutral"
              detail="Sprünge bis in die Tiefe"
            />
            <MetricCard label="PageRank" value={metrics.pagerank.toFixed(4)} tone="neutral" />
            <MetricCard
              label="Änderungen"
              value={String(metrics.churn)}
              tone="neutral"
              detail={`${metrics.authors} Autoren`}
            />
            <MetricCard
              label="Fehlerbehebungen"
              value={String(metrics.risk)}
              tone={metrics.risk > 0 ? "amber" : "neutral"}
              detail="Commits mit fix/bug/revert"
            />
            {facts && (
              <MetricCard
                label="Komplexität"
                value={String(facts.complexity)}
                tone={facts.complexity > 15 ? "amber" : "neutral"}
                detail={`${facts.loc} Zeilen · Tiefe ${facts.max_nesting}`}
              />
            )}
          </div>
          {weakest === "guessed" && (
            <p className="muted cg-fineprint">
              <Confidence value="guessed" /> Die Aufruferzahl stützt sich auch auf geratene Kanten
              — Namensgleichheit, kein Beleg. Die Liste unten zeigt, welche.
            </p>
          )}
        </section>

        <NeighbourList
          title="Wird aufgerufen von"
          items={blueprint.data!.callers}
          onFocus={(hit) => onFocus(hit.id)}
          onOpen={onOpen}
          extra={onMap}
        />
        <NeighbourList
          title="Ruft auf"
          items={blueprint.data!.callees}
          onFocus={(hit) => onFocus(hit.id)}
          onOpen={onOpen}
          extra={onMap}
        />
        <NeighbourList
          title="Enthält"
          items={blueprint.data!.children}
          onFocus={(hit) => onFocus(hit.id)}
          onOpen={onOpen}
          extra={onMap}
        />
      </div>
    </div>
  );
}

export default CodeInspectorPanel;
