/**
 * Code-Graph: das Verstehen zum Bearbeiten dazu.
 *
 * Die Werkstatt konnte Dateien zeigen und ändern; was fehlte, war die Frage
 * "warum ist das so". Dieses Panel beantwortet sie aus dem Symbolgraphen, den
 * CodeSearch (Rust) aus demselben Projektordner baut.
 *
 * Die eine Regel, die jede Anzeige hier einhält: **keine Beziehung ohne ihre
 * Sicherheitsstufe und ihre Belegstelle.** Ein Aufrufer wird nie nackt
 * hingeschrieben — daneben steht ●/◐/○ und das `datei:zeile`, an dem die
 * Behauptung nachprüfbar ist. Ohne das wäre eine geratene Kante von einer
 * belegten nicht zu unterscheiden, und das Werkzeug verlöre seinen Zweck.
 */
import { Suspense, lazy, useEffect, useState } from "react";
import { Link } from "react-router-dom";
import {
  AlertTriangle,
  Boxes,
  FileWarning,
  Flame,
  Maximize2,
  MessageCircleQuestion,
  Network,
  RefreshCw,
  Search,
  Trash2,
  Zap,
} from "lucide-react";

import { api } from "../api";
import { CodeAskPanel } from "./CodeAskPanel";
import {
  ConfidenceLegend,
  NeighbourList,
  Section,
  Stat,
  SymbolRow,
  kindLabel,
  useCodeIndex,
} from "./codegraph/shared";

// Lazy: mermaid ist gross, und wer nie ein Diagramm öffnet, soll es nie laden.
const CodeDiagramPanel = lazy(() =>
  import("./CodeDiagramPanel").then((module) => ({ default: module.CodeDiagramPanel })),
);
import { useQuery } from "@tanstack/react-query";

type Tab = "overview" | "search" | "blueprint" | "diagram" | "ask";

export type CodeGraphPanelProps = {
  projectId: string | null;
  /** Springt in den Monaco-Editor der Werkstatt, auf Datei und Zeile. */
  onOpenSymbol?: (path: string, line: number) => void;
  /** Forschungsprojekt — nur für die Paper-Evidenz im Fragen-Tab. */
  researchProjectId?: string | null;
};

export function CodeGraphPanel({ projectId, onOpenSymbol, researchProjectId }: CodeGraphPanelProps) {
  const [tab, setTab] = useState<Tab>("overview");
  const [term, setTerm] = useState("");
  const [debounced, setDebounced] = useState("");
  const [focusId, setFocusId] = useState<string | null>(null);

  const { status, ready, progress, guessShare, indexMutation, dropMutation, busy } =
    useCodeIndex(projectId);

  const overview = useQuery({
    queryKey: ["codegraph", "overview", projectId],
    queryFn: () => api.codegraph.overview(projectId!),
    enabled: Boolean(projectId) && ready && tab === "overview",
  });

  // 140 ms: kurz genug, dass es sich wie Tippen anfühlt, lang genug, dass ein
  // Wort nicht acht Abfragen auslöst.
  useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(term.trim()), 140);
    return () => window.clearTimeout(timer);
  }, [term]);

  const symbols = useQuery({
    queryKey: ["codegraph", "search", projectId, debounced],
    queryFn: () => api.codegraph.search(projectId!, debounced, undefined, 30),
    enabled: Boolean(projectId) && ready && tab === "search" && debounced.length > 0,
  });

  const texts = useQuery({
    queryKey: ["codegraph", "search-text", projectId, debounced],
    queryFn: () => api.codegraph.searchText(projectId!, debounced, 15),
    enabled: Boolean(projectId) && ready && tab === "search" && debounced.length >= 3,
  });

  const blueprint = useQuery({
    queryKey: ["codegraph", "blueprint", projectId, focusId],
    queryFn: () => api.codegraph.blueprint(projectId!, focusId!),
    enabled: Boolean(projectId) && Boolean(focusId) && ready,
  });

  useEffect(() => {
    if (dropMutation.isSuccess) setFocusId(null);
  }, [dropMutation.isSuccess]);

  useEffect(() => {
    setFocusId(null);
    setTerm("");
  }, [projectId]);

  function focus(hit: { id: string; path?: string; line?: number }) {
    setFocusId(hit.id);
    setTab("blueprint");
  }

  function openInEditor(path: string, line: number) {
    onOpenSymbol?.(path, line);
  }

  if (!projectId) {
    return <p className="muted cg-empty">Wähle links ein Projekt.</p>;
  }

  if (status.data && !status.data.binary_available) {
    return (
      <div className="cg-pane">
        <div className="cg-notice">
          <AlertTriangle size={15} />
          <div>
            <strong>Code-Graph nicht verfügbar</strong>
            <pre className="cg-hint">{status.data.binary_hint}</pre>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="cg-pane">
      <div className="cg-tabs">
        <button className={tab === "overview" ? "active" : ""} onClick={() => setTab("overview")}>
          Überblick
        </button>
        <button className={tab === "search" ? "active" : ""} onClick={() => setTab("search")}>
          Suche
        </button>
        <button
          className={tab === "blueprint" ? "active" : ""}
          onClick={() => setTab("blueprint")}
          disabled={!focusId}
        >
          Bauplan
        </button>
        <button
          className={tab === "diagram" ? "active" : ""}
          onClick={() => setTab("diagram")}
          disabled={!focusId}
          title="Klassenhierarchie und Aufruffolge um das Fokus-Symbol"
        >
          Diagramm
        </button>
        <button
          className={tab === "ask" ? "active" : ""}
          onClick={() => setTab("ask")}
          title="Fragen an den Code — jede Antwort mit geprüften Belegstellen"
        >
          <MessageCircleQuestion size={13} /> Fragen
        </button>
        <span className="cg-tabs-spacer" />
        <Link
          className="wk-iconbtn"
          to={`/code?project=${encodeURIComponent(projectId)}${focusId ? `&node=${encodeURIComponent(focusId)}` : ""}&view=map`}
          title="Im grossen Code-Graph öffnen — Karte, Inspektor und Fragen auf voller Breite"
        >
          <Maximize2 size={14} />
        </Link>
        {guessShare !== null && (
          <span
            className="cg-gauge"
            title={`${status.data?.stats.guessed_edges} von ${status.data?.stats.edges} Kanten sind über Namensgleichheit geraten, nicht belegt.`}
          >
            {guessShare}% vermutet
          </span>
        )}
        <button
          className="wk-iconbtn"
          title={ready ? "Neu indizieren (nur Geändertes)" : "Indizieren"}
          onClick={() => indexMutation.mutate()}
          disabled={busy}
        >
          <RefreshCw size={14} className={busy ? "cg-spin" : ""} />
        </button>
        {ready && (
          <button
            className="wk-iconbtn"
            title="Index verwerfen (reiner Cache)"
            onClick={() => dropMutation.mutate()}
            disabled={busy || dropMutation.isPending}
          >
            <Trash2 size={14} />
          </button>
        )}
      </div>

      {progress && <div className="cg-progress">{progress}</div>}
      {status.data?.status === "failed" && !progress && (
        <div className="cg-notice cg-notice--error">
          <AlertTriangle size={15} />
          <span>{status.data.error_message}</span>
        </div>
      )}

      {!ready && !busy && (
        <div className="cg-empty">
          <Network size={22} />
          <p>Dieses Projekt ist noch nicht indiziert.</p>
          <button className="wk-btn" onClick={() => indexMutation.mutate()}>
            <Zap size={15} /> Jetzt indizieren
          </button>
          <p className="muted cg-fineprint">
            Liest den Ordner einmal durch und legt einen Symbolgraphen an. Der Index landet unter
            <code> data/codegraph/</code> — nicht im Projekt selbst.
          </p>
        </div>
      )}

      {ready && tab === "overview" && (
        <div className="cg-scroll">
          {overview.isLoading && <p className="muted">wird geladen …</p>}
          {overview.data && (
            <>
              <div className="cg-stats">
                <Stat label="Dateien" value={overview.data.stats.files} sub={`${overview.data.stats.parsed_files} geparst`} />
                <Stat label="Symbole" value={overview.data.stats.nodes} />
                <Stat label="Kanten" value={overview.data.stats.edges} sub={`${overview.data.stats.guessed_edges} vermutet`} />
                <Stat label="Lücken" value={overview.data.stats.dynamic_gaps} sub="dynamisch" />
              </div>

              <Section icon={<Boxes size={13} />} title="Wichtigste Symbole" hint="nach PageRank, Reichweite, Änderungshäufigkeit und Risiko">
                {overview.data.important.map((hit) => (
                  <SymbolRow key={hit.id} hit={hit} onFocus={focus} onOpen={openInEditor} />
                ))}
              </Section>

              <Section icon={<Flame size={13} />} title="Heiße Dateien" hint="am häufigsten geändert — dort sitzt meist auch der Ärger">
                {overview.data.hot_files.map((file) => (
                  <button
                    key={file.path}
                    className="cg-row"
                    onClick={() => openInEditor(file.path, 1)}
                    title={`${file.churn} Änderungen · ${file.risk} Fehlerbehebungen`}
                  >
                    <span className="cg-row-main">{file.path}</span>
                    <span className="cg-row-meta">{file.churn}× · {file.risk} Fixes</span>
                  </button>
                ))}
              </Section>

              {overview.data.gaps.length > 0 && (
                <Section
                  icon={<FileWarning size={13} />}
                  title="Dynamische Lücken"
                  hint="Hier endet die statische Analyse — Reflection, eval, Dependency Injection. Sichtbar gemacht statt weggelassen."
                >
                  {overview.data.gaps.map((gap) => (
                    <SymbolRow key={gap.id} hit={gap} onFocus={focus} onOpen={openInEditor} />
                  ))}
                </Section>
              )}

              <Section icon={<Boxes size={13} />} title="Benutzte Pakete" hint="was der Code tatsächlich importiert — nicht, was in der Paketliste steht">
                <div className="cg-chips">
                  {overview.data.dependencies.map((dep) => (
                    <span key={dep.id} className="cg-chip">{dep.name}</span>
                  ))}
                </div>
              </Section>
            </>
          )}
        </div>
      )}

      {ready && tab === "search" && (
        <>
          <div className="cg-searchbar">
            <Search size={14} />
            <input
              autoFocus
              value={term}
              placeholder="Symbol oder Text suchen …"
              onChange={(event) => setTerm(event.target.value)}
            />
          </div>
          <div className="cg-scroll">
            {debounced.length === 0 && (
              <p className="muted cg-fineprint">
                Bezeichner im Code sind fast immer englisch — such nach <code>total</code>, nicht nach „Endbetrag".
              </p>
            )}
            {(symbols.data?.length ?? 0) > 0 && (
              <Section icon={<Boxes size={13} />} title="Symbole">
                {symbols.data!.map((hit) => (
                  <SymbolRow key={hit.id} hit={hit} onFocus={focus} onOpen={openInEditor} />
                ))}
              </Section>
            )}
            {(texts.data?.length ?? 0) > 0 && (
              <Section icon={<Search size={13} />} title="Im Text">
                {texts.data!.map((hit, index) => (
                  <button
                    key={`${hit.path}:${hit.line}:${index}`}
                    className="cg-row"
                    onClick={() => openInEditor(hit.path, hit.line)}
                  >
                    <span className="cg-row-main cg-mono">{hit.text.trim().slice(0, 120)}</span>
                    <span className="cg-row-meta">{hit.path}:{hit.line}</span>
                  </button>
                ))}
              </Section>
            )}
            {debounced.length > 0 &&
              !symbols.isLoading &&
              (symbols.data?.length ?? 0) === 0 &&
              (texts.data?.length ?? 0) === 0 && (
                <p className="muted">Nichts gefunden für „{debounced}".</p>
              )}
          </div>
        </>
      )}

      {ready && tab === "blueprint" && (
        <div className="cg-scroll">
          {!focusId && <p className="muted">Wähle in der Suche oder im Überblick ein Symbol.</p>}
          {blueprint.isLoading && <p className="muted">wird geladen …</p>}
          {blueprint.data && (
            <>
              <div className="cg-focus">
                <button
                  className="cg-focus-title"
                  onClick={() =>
                    openInEditor(blueprint.data!.focus.path, blueprint.data!.focus.span.start_line)
                  }
                >
                  <strong>{blueprint.data.focus.qualified}</strong>
                  <span className="cg-row-meta">
                    {kindLabel(blueprint.data.focus.kind)} · {blueprint.data.focus.path}:
                    {blueprint.data.focus.span.start_line}
                  </span>
                </button>
                {blueprint.data.focus.doc && (
                  <p className="cg-doc">{blueprint.data.focus.doc.slice(0, 400)}</p>
                )}
                {blueprint.data.focus.facts && (
                  <p className="cg-row-meta">
                    Komplexität {blueprint.data.focus.facts.complexity} ·{" "}
                    {blueprint.data.focus.facts.loc} Zeilen · Tiefe{" "}
                    {blueprint.data.focus.facts.max_nesting}
                    {blueprint.data.focus.facts.side_effects.length > 0 &&
                      ` · ${blueprint.data.focus.facts.side_effects.join(", ")}`}
                  </p>
                )}
                <p className="cg-row-meta">
                  Relevanz {Math.round(blueprint.data.focus.metrics.relevance * 100)}% ·{" "}
                  {blueprint.data.focus.metrics.fan_in} Aufrufer ·{" "}
                  {blueprint.data.focus.metrics.churn} Änderungen ·{" "}
                  {blueprint.data.focus.metrics.risk} Fixes
                </p>
              </div>

              <NeighbourList
                title="Wird aufgerufen von"
                items={blueprint.data.callers}
                onFocus={focus}
                onOpen={openInEditor}
              />
              <NeighbourList
                title="Ruft auf"
                items={blueprint.data.callees}
                onFocus={focus}
                onOpen={openInEditor}
              />
              <NeighbourList
                title="Enthält"
                items={blueprint.data.children}
                onFocus={focus}
                onOpen={openInEditor}
              />

              <ConfidenceLegend />
            </>
          )}
        </div>
      )}

      {ready && tab === "diagram" && (
        <Suspense fallback={<p className="muted cg-empty">Diagramm wird geladen …</p>}>
          <CodeDiagramPanel projectId={projectId} nodeId={focusId} onOpenSymbol={openInEditor} />
        </Suspense>
      )}

      {ready && tab === "ask" && (
        <CodeAskPanel
          projectId={projectId}
          onOpenSymbol={onOpenSymbol}
          researchProjectId={researchProjectId}
        />
      )}
    </div>
  );
}

export default CodeGraphPanel;
