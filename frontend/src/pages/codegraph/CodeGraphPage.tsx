/**
 * Code-Graph — eine Seite statt einer Spalte.
 *
 * Dasselbe Werkzeug gab es vorher in der dritten, schmalsten Spalte der
 * Werkstatt: fünf Tabs auf 26 % Fensterbreite, qualifizierte Namen abgeschnitten,
 * eine Suche ohne Filter, kein Bild. Das Panel dort bleibt, weil Nachschlagen
 * *neben dem Editor* eine andere Aufgabe ist.
 *
 * Hier geht es ums Verstehen — und ums Ändern, wenn man verstanden hat: eine
 * gerichtete Karte (wer ruft auf links, was aufgerufen wird rechts), ein
 * Inspektor mit Datenfluss und Steckbrief, ein Editor für den Quelltext des
 * gewählten Symbols, eine Suche mit Facetten über alle Symbolarten und ein
 * Fragen-Bereich mit Modellwahl. Der Weg über die Werkstatt bleibt einen Klick
 * entfernt (Terminal, git-Diff, Dateibaum) — er ist nur nicht mehr nötig, um
 * eine Zeile zu ändern.
 *
 * Die Regel des Werkzeugs gilt unverändert und an jeder Stelle: **keine
 * Beziehung ohne Sicherheitsstufe und Belegstelle** — gezeichnet *und* als
 * nachprüfbare Zeile daneben.
 */
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { Panel, PanelGroup, PanelResizeHandle } from "react-resizable-panels";
import type { ImperativePanelHandle } from "react-resizable-panels";
import {
  AlertTriangle,
  RefreshCw,
  Trash2,
  Waypoints,
  Zap,
} from "lucide-react";

import { api, streamClusterNames } from "../../api";
import { LlmPicker } from "../../components/LlmPicker";
import { useAppState } from "../../state";
import type {
  CodeCluster,
  CodeClusterEdge,
  CodeEdgeKind,
  CodeFocusNode,
  CodeNodeId,
  CodeSliceEdge,
  CodeSymbolHit,
} from "../../types";
import { ClusterEvidencePanel } from "./ClusterEvidencePanel";
import { ClusterMapPanel } from "./ClusterMapPanel";
import { CodeChatPanel } from "./CodeChatPanel";
import { CodeEvidencePanel } from "./CodeEvidencePanel";
import { CodeInspectorPanel } from "./CodeInspectorPanel";
import { CodeMapPanel } from "./CodeMapPanel";
import { CodeNavigatorPanel } from "./CodeNavigatorPanel";
import { CheckpointBar } from "./CheckpointBar";
import { ImpactPanel } from "./ImpactPanel";
import { SandboxPanel } from "./SandboxPanel";
import { TanglePanel } from "./TanglePanel";
import { CodePathPanel } from "./CodePathPanel";
import { DEFAULT_EDGE_KINDS, pickMapRoots } from "./codeMap";
import { useCodeIndex } from "./shared";
import { useCodeMap, useMultiFocusMap } from "./useCodeMap";

// mermaid ist gross; wer nie ein Diagramm öffnet, soll es nie laden.
const CodeDiagramPanel = lazy(() =>
  import("../CodeDiagramPanel").then((module) => ({ default: module.CodeDiagramPanel })),
);
// Monaco zieht fünf Worker-Chunks nach — dasselbe Argument, nur grösser.
const CodeEditorPanel = lazy(() =>
  import("./CodeEditorPanel").then((module) => ({ default: module.CodeEditorPanel })),
);

/** Derselbe Schlüssel wie in der Werkstatt — der Wechsel soll nie neu auswählen. */
const PROJECT_KEY = "sciencekg.werkstatt.project";
const LLM_KEY = "sciencekg.code.llm";

/**
 * `clusters` steht zuerst und ist der Standard, solange kein Symbol im
 * Deep-Link steht: wer die Seite öffnet, weiss meistens noch nicht, wonach er
 * sucht — und eine Suchmaske ist auf diese Frage keine Antwort.
 *
 * `feature` ist die Karte über *mehrere* Wurzeln: die Trefferliste einer
 * Chat-Antwort. Sie steht nicht in der Leiste, weil man sie nicht auswählt,
 * sondern aus einer Antwort heraus betritt.
 */
type CenterView = "clusters" | "map" | "feature" | "code" | "diagram" | "path" | "impact" | "sandbox" | "tangle";

function loadLlmOverride(): { provider?: string; model?: string } {
  try {
    return JSON.parse(localStorage.getItem(LLM_KEY) ?? "{}");
  } catch {
    return {};
  }
}

export function CodeGraphPage() {
  const { provider: globalProvider, model: globalModel } = useAppState();
  const [searchParams, setSearchParams] = useSearchParams();

  const [projectId, setProjectId] = useState<string | null>(
    () => searchParams.get("project") ?? localStorage.getItem(PROJECT_KEY),
  );
  // Knoten-IDs sind 16-stellige Hex-Strings. Nie `Number()` — als JS-Zahl
  // verlören sie stillschweigend ihre unteren Bits und zeigten auf ein anderes
  // Symbol.
  const [focusId, setFocusId] = useState<CodeNodeId | null>(() => searchParams.get("node"));
  const [selectedId, setSelectedId] = useState<CodeNodeId | null>(() => searchParams.get("node"));
  const [view, setView] = useState<CenterView>(() => {
    const wanted = searchParams.get("view") as CenterView | null;
    if (wanted) return wanted;
    // Mit `?node=` im Link ist die Frage schon gestellt — dann direkt zur Karte.
    return searchParams.get("node") ? "map" : "clusters";
  });
  const [clusterPrefix, setClusterPrefix] = useState(() => searchParams.get("cluster") ?? "");
  const [selectedCluster, setSelectedCluster] = useState<CodeCluster | null>(null);
  /** Die Wurzeln der Feature-Karte, plus wie viele Treffer es insgesamt waren. */
  const [feature, setFeature] = useState<{ roots: CodeFocusNode[]; total: number } | null>(null);
  const [selectedClusterEdge, setSelectedClusterEdge] = useState<CodeClusterEdge | null>(null);
  const [naming, setNaming] = useState(false);
  const [term, setTerm] = useState(() => searchParams.get("q") ?? "");
  const [depth, setDepth] = useState(() => Number(searchParams.get("depth") ?? 1) || 1);
  const [edgeKinds, setEdgeKinds] = useState<CodeEdgeKind[]>(() => {
    const raw = searchParams.get("edges");
    return raw ? (raw.split(",") as CodeEdgeKind[]) : DEFAULT_EDGE_KINDS;
  });
  const [pathTargetId, setPathTargetId] = useState<CodeNodeId | null>(
    () => searchParams.get("to"),
  );
  const [pickingTarget, setPickingTarget] = useState(false);
  const [selectedEdge, setSelectedEdge] = useState<CodeSliceEdge | null>(null);
  const [llm, setLlm] = useState<{ provider?: string; model?: string }>(loadLlmOverride);
  const [askBig, setAskBig] = useState(false);
  const [mapBig, setMapBig] = useState(false);
  const [inspectorBig, setInspectorBig] = useState(false);

  const navPanelRef = useRef<ImperativePanelHandle>(null);
  const evidencePanelRef = useRef<ImperativePanelHandle>(null);
  const rightPanelRef = useRef<ImperativePanelHandle>(null);
  const inspectorPanelRef = useRef<ImperativePanelHandle>(null);

  const projects = useQuery({ queryKey: ["werkstatt", "list"], queryFn: api.werkstatt.list });
  const { status, ready, progress, guessShare, indexMutation, dropMutation, busy } =
    useCodeIndex(projectId);

  const focusNode = useRef<CodeSymbolHit | null>(null);
  const nodeDetail = useQuery({
    queryKey: ["codegraph", "node", projectId, selectedId],
    queryFn: () => api.codegraph.node(projectId!, selectedId!),
    enabled: Boolean(projectId && selectedId),
  });
  const pathTarget = useQuery({
    queryKey: ["codegraph", "node", projectId, pathTargetId],
    queryFn: () => api.codegraph.node(projectId!, pathTargetId!),
    enabled: Boolean(projectId && pathTargetId),
  });

  const map = useCodeMap(projectId, focusId, edgeKinds, depth, ready && view === "map");

  const featureRootIds = useMemo(
    () => (feature?.roots ?? []).map((node) => node.id),
    [feature],
  );
  const featureMap = useMultiFocusMap(
    projectId,
    featureRootIds,
    edgeKinds,
    ready && view === "feature",
  );

  const clusterLevel = useQuery({
    queryKey: ["codegraph", "clusters", projectId, clusterPrefix],
    queryFn: () => api.codegraph.clusters(projectId!, clusterPrefix),
    enabled: Boolean(projectId && ready && view === "clusters"),
  });

  // Projektwahl gültig halten und teilen (derselbe Schlüssel wie die Werkstatt).
  useEffect(() => {
    const list = projects.data?.projects ?? [];
    if (!list.length) return;
    if (!projectId || !list.some((item) => item.id === projectId)) {
      setProjectId(list[0].id);
    }
  }, [projects.data, projectId]);

  useEffect(() => {
    if (projectId) localStorage.setItem(PROJECT_KEY, projectId);
  }, [projectId]);

  useEffect(() => {
    try {
      localStorage.setItem(LLM_KEY, JSON.stringify(llm));
    } catch {
      // Speicher nicht verfügbar — die Wahl bleibt eben sitzungslokal.
    }
  }, [llm]);

  // URL nachführen, entprellt: ein gezogener Filterchip soll nicht die Historie
  // füllen. `replace`, damit der Zurück-Knopf die Seite verlässt statt durch
  // zwanzig Zwischenzustände zu laufen.
  useEffect(() => {
    const timer = window.setTimeout(() => {
      const next = new URLSearchParams();
      if (projectId) next.set("project", projectId);
      if (focusId) next.set("node", focusId);
      // `feature` steht bewusst nicht im Link: die Wurzeln kommen aus einer
      // Antwort, nicht aus der URL. Ein Link darauf führte zu einer leeren Karte
      // und sähe aus, als sei etwas kaputt.
      if (view !== "clusters" && view !== "feature") next.set("view", view);
      if (clusterPrefix) next.set("cluster", clusterPrefix);
      if (term.trim()) next.set("q", term.trim());
      if (depth !== 1) next.set("depth", String(depth));
      if (edgeKinds.join(",") !== DEFAULT_EDGE_KINDS.join(",")) next.set("edges", edgeKinds.join(","));
      if (pathTargetId) next.set("to", pathTargetId);
      setSearchParams(next, { replace: true });
    }, 250);
    return () => window.clearTimeout(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, focusId, view, term, depth, edgeKinds, pathTargetId, clusterPrefix]);

  useEffect(() => {
    setFocusId(null);
    setSelectedId(null);
    setPathTargetId(null);
    setSelectedEdge(null);
    setClusterPrefix("");
    setSelectedCluster(null);
    setSelectedClusterEdge(null);
    setFeature(null);
  }, [projectId]);

  /**
   * Sprung in die Werkstatt: Terminal, git-Diff und Dateibaum liegen dort.
   * Zum blossen Ändern einer Zeile reicht der Code-Tab hier.
   */
  const openInWorkstation = useCallback(
    (path: string, line: number) => {
      if (!projectId) return;
      const target = `/werkstatt?project=${encodeURIComponent(projectId)}&file=${encodeURIComponent(path)}&line=${line}`;
      window.location.hash = `#${target}`;
    },
    [projectId],
  );

  const selectNode = useCallback(
    (nodeId: CodeNodeId) => {
      if (pickingTarget) {
        setPathTargetId(nodeId);
        setPickingTarget(false);
        setView("path");
        return;
      }
      setSelectedId(nodeId);
    },
    [pickingTarget],
  );

  const focusOn = useCallback(
    (hit: CodeSymbolHit) => {
      if (pickingTarget) {
        setPathTargetId(hit.id);
        setPickingTarget(false);
        setView("path");
        return;
      }
      focusNode.current = hit;
      setFocusId(hit.id);
      setSelectedId(hit.id);
      setSelectedEdge(null);
    },
    [pickingTarget],
  );

  const showOnMap = useCallback((nodeId: CodeNodeId) => {
    setFocusId(nodeId);
    setSelectedId(nodeId);
    setView("map");
  }, []);

  /**
   * Die Trefferliste einer Antwort als Karte.
   *
   * Gekürzt wird hier, nicht im Hook: `pickMapRoots` behält die relevantesten
   * und meldet die Gesamtzahl zurück, damit „8 von 19" danebenstehen kann.
   * Eine stillschweigend gekürzte Karte sähe aus wie das ganze Feature.
   */
  /**
   * Eine Funktion aus einer Trefferliste heraus ändern.
   *
   * Der Editor öffnet in der Mittelspalte statt im Panel daneben: Monaco zieht
   * fünf Worker-Chunks nach und darf genau einen `lazy`-Einstieg haben.
   */
  const editNode = useCallback((nodeId: CodeNodeId) => {
    setSelectedId(nodeId);
    setView("code");
  }, []);

  const showFeature = useCallback((nodes: CodeFocusNode[]) => {
    if (!nodes.length) return;
    setFeature(pickMapRoots(nodes));
    setSelectedEdge(null);
    setView("feature");
  }, []);

  /** Eine Ebene tiefer. Auswahl fällt weg — sie gehörte zur Ebene darüber. */
  const openCluster = useCallback((prefix: string) => {
    setClusterPrefix(prefix);
    setSelectedCluster(null);
    setSelectedClusterEdge(null);
  }, []);

  /**
   * Von einem Bereich in die Symbolkarte.
   *
   * Der Einstieg ist das relevanteste Symbol des Bereichs — nicht das erste
   * alphabetisch: wer einen Bereich anklickt, will wissen, worum es dort geht,
   * und das steht am wichtigsten Symbol.
   */
  const enterCluster = useCallback(
    async (cluster: CodeCluster) => {
      if (!projectId) return;
      const top =
        cluster.top_symbols[0] ??
        (await api.codegraph.clusterMembers(projectId, cluster.path, 1))[0];
      if (!top) return;
      focusNode.current = top;
      setFocusId(top.id);
      setSelectedId(top.id);
      setSelectedEdge(null);
      setView("map");
    },
    [projectId],
  );

  /**
   * Benennen lassen. Läuft nebenher — die Karte steht schon mit Ordnernamen,
   * und ohne Modell fehlt hinterher nur die Beschriftung.
   */
  const nameClusters = useCallback(() => {
    if (!projectId || naming) return;
    setNaming(true);
    streamClusterNames(
      projectId,
      {
        prefix: clusterPrefix,
        provider: llm.provider ?? null,
        model: llm.model ?? null,
      },
      (event) => {
        if (event.event === "done" || event.event === "failed") {
          setNaming(false);
          void clusterLevel.refetch();
        }
      },
    ).catch(() => setNaming(false));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [projectId, clusterPrefix, naming, llm.provider, llm.model]);

  function toggleEdgeKind(kind: CodeEdgeKind) {
    setEdgeKinds((current) =>
      current.includes(kind) ? current.filter((item) => item !== kind) : [...current, kind],
    );
  }

  /**
   * Nebenspalten einklappen, damit ein Bereich die halbe Seite bekommt.
   *
   * Drei Bereiche, dieselbe Geste: Karte, Inspektor, Fragen. Nur einer kann
   * gross sein — sonst müsste man raten, welcher Klick was zurücksetzt.
   */
  function toggleBig(which: "ask" | "map" | "inspector") {
    const current = which === "ask" ? askBig : which === "map" ? mapBig : inspectorBig;
    const growing = !current;
    setAskBig(which === "ask" && growing);
    setMapBig(which === "map" && growing);
    setInspectorBig(which === "inspector" && growing);

    if (!growing) {
      navPanelRef.current?.expand();
      evidencePanelRef.current?.expand();
      rightPanelRef.current?.expand();
      rightPanelRef.current?.resize(25);
      inspectorPanelRef.current?.resize(56);
      return;
    }

    navPanelRef.current?.collapse();
    evidencePanelRef.current?.collapse();
    if (which === "map") {
      rightPanelRef.current?.collapse();
      return;
    }
    rightPanelRef.current?.expand();
    rightPanelRef.current?.resize(52);
    // Der Bereich, den man gross haben will, bekommt auch senkrecht den Platz —
    // sonst stünde ein 52 % breiter Inspektor weiterhin auf halber Höhe.
    inspectorPanelRef.current?.resize(which === "inspector" ? 82 : 18);
  }

  const projectList = projects.data?.projects ?? [];

  // --- Zustände ohne Index -------------------------------------------------

  const header = (
    <header className="cgp-head">
      <div className="cgp-head-title">
        <Waypoints size={18} />
        <div>
          <span>Struktur</span>
          <h1>Code-Graph</h1>
        </div>
      </div>
      <label className="cgp-head-project">
        Projekt
        <select
          value={projectId ?? ""}
          onChange={(event) => setProjectId(event.target.value || null)}
        >
          {projectList.length === 0 && <option value="">— kein Werkstatt-Projekt —</option>}
          {projectList.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      {status.data && (
        <span className="cgp-status" title={status.data.path ?? undefined}>
          <span className={`cgp-dot cgp-dot--${status.data.status}`} />
          {ready
            ? `${status.data.stats.nodes.toLocaleString("de-DE")} Symbole · ${status.data.stats.edges.toLocaleString("de-DE")} Kanten`
            : status.data.status === "failed"
              ? "fehlgeschlagen"
              : "nicht indiziert"}
          {guessShare !== null && (
            <span
              className="cg-gauge"
              title={`${status.data.stats.guessed_edges} von ${status.data.stats.edges} Kanten sind über Namensgleichheit geraten, nicht belegt.`}
            >
              {guessShare}% vermutet
            </span>
          )}
        </span>
      )}
      <span className="cgp-spacer" />
      <LlmPicker
        variant="compact"
        provider={llm.provider}
        model={llm.model}
        onProviderChange={(next) => setLlm({ provider: next, model: undefined })}
        onModelChange={(next) => setLlm((current) => ({ ...current, model: next }))}
        inheritFrom={{ provider: globalProvider, model: globalModel }}
      />
      <button
        className="wk-iconbtn"
        title={ready ? "Neu indizieren (nur Geändertes)" : "Indizieren"}
        onClick={() => indexMutation.mutate()}
        disabled={busy || !projectId}
      >
        <RefreshCw size={15} className={busy ? "cg-spin" : ""} />
      </button>
      {ready && (
        <button
          className="wk-iconbtn"
          title="Index verwerfen (reiner Cache — wird beim nächsten Indizieren neu gebaut)"
          onClick={() => dropMutation.mutate()}
          disabled={busy || dropMutation.isPending}
        >
          <Trash2 size={15} />
        </button>
      )}
    </header>
  );

  if (!projectId) {
    return (
      <section className="page codegraph-page">
        {header}
        <div className="cgp-body cgp-body--single">
          <p className="muted cg-empty">
            Lege in der <Link to="/werkstatt">Werkstatt</Link> ein Projekt an oder öffne einen
            Ordner — der Code-Graph erklärt dessen Code.
          </p>
        </div>
      </section>
    );
  }

  if (status.data && !status.data.binary_available) {
    return (
      <section className="page codegraph-page">
        {header}
        <div className="cgp-body cgp-body--single">
          <div className="cg-notice">
            <AlertTriangle size={16} />
            <div>
              <strong>Code-Graph nicht verfügbar</strong>
              <p className="muted cg-fineprint">
                Der Analyseteil ist ein Zusatz in Rust und noch nicht gebaut. Alles andere im
                Programm läuft davon unberührt weiter.
              </p>
              <pre className="cg-hint">{status.data.binary_hint}</pre>
            </div>
          </div>
        </div>
      </section>
    );
  }

  if (!ready && !busy) {
    return (
      <section className="page codegraph-page">
        {header}
        <div className="cgp-body cgp-body--single">
          <div className="cg-empty">
            <Waypoints size={26} />
            <p>Dieses Projekt ist noch nicht indiziert.</p>
            <button className="wk-btn wk-btn--primary" onClick={() => indexMutation.mutate()}>
              <Zap size={15} /> Jetzt indizieren
            </button>
            {progress && <p className="cg-progress">{progress}</p>}
            {status.data?.status === "failed" && status.data.error_message && (
              <p className="cg-notice cg-notice--error">{status.data.error_message}</p>
            )}
            <p className="muted cg-fineprint">
              Liest den Ordner einmal durch und legt einen Symbolgraphen an. Der Index landet unter
              <code> data/codegraph/</code> — nicht im Projekt selbst, und er ist reiner Cache.
            </p>
          </div>
        </div>
      </section>
    );
  }

  // --- Die eigentliche Seite ------------------------------------------------

  return (
    <section className="page codegraph-page">
      {header}
      {progress && <div className="cg-progress cgp-progress">{progress}</div>}
      <CheckpointBar projectId={projectId} />

      <div className="cgp-body">
        <PanelGroup direction="horizontal" autoSaveId="sciencekg.code.cols">
          <Panel
            ref={navPanelRef}
            order={1}
            defaultSize={21}
            minSize={14}
            collapsible
            className="cgp-slot"
          >
            <CodeNavigatorPanel
              projectId={projectId}
              ready={ready}
              selectedId={selectedId}
              term={term}
              onTermChange={setTerm}
              onFocus={focusOn}
              onOpen={openInWorkstation}
            />
          </Panel>
          <PanelResizeHandle className="wk-resize wk-resize--v" />

          <Panel order={2} minSize={30} className="cgp-slot">
            <PanelGroup direction="vertical" autoSaveId="sciencekg.code.center">
              <Panel id="cg-center" order={1} minSize={25} className="cgp-slot">
                <div className="cgp-pane cgp-center">
                  <div className="cgp-pane-head">
                    <div className="segmented cgp-views">
                      <button
                        className={view === "clusters" ? "active" : ""}
                        onClick={() => setView("clusters")}
                        title="Woraus besteht dieses Projekt — Bereiche und ihre Abhängigkeiten"
                      >
                        Bereiche
                      </button>
                      <button
                        className={view === "map" ? "active" : ""}
                        onClick={() => setView("map")}
                      >
                        Karte
                      </button>
                      <button
                        className={view === "code" ? "active" : ""}
                        onClick={() => setView("code")}
                        disabled={!selectedId}
                        title="Quelltext des gewählten Symbols — lesen und ändern"
                      >
                        Code
                      </button>
                      <button
                        className={view === "diagram" ? "active" : ""}
                        onClick={() => setView("diagram")}
                        disabled={!selectedId}
                        title="Klassenhierarchie und Aufruffolge um das gewählte Symbol"
                      >
                        Diagramm
                      </button>
                      <button
                        className={view === "path" ? "active" : ""}
                        onClick={() => setView("path")}
                        disabled={!selectedId}
                        title="Kürzester Aufrufpfad zu einem zweiten Symbol"
                      >
                        Pfad
                      </button>
                      <button
                        className={view === "impact" ? "active" : ""}
                        onClick={() => setView("impact")}
                        disabled={!selectedId}
                        title="Was bricht, wenn ich das ändere? — vor der Ausführung"
                      >
                        Auswirkung
                      </button>
                      <button
                        className={view === "sandbox" ? "active" : ""}
                        onClick={() => setView("sandbox")}
                        title="Probelauf in einer git-worktree-Kopie"
                      >
                        Probelauf
                      </button>
                      <button
                        className={view === "tangle" ? "active" : ""}
                        onClick={() => setView("tangle")}
                        title="Spaghetti-Löser: Hotspots, Ringe, Refactor-Vorschlag"
                      >
                        Knäuel
                      </button>
                    </div>
                    {view === "map" && (
                      <label className="cgp-depth" title="Wie viele Sprünge weit die Karte reicht">
                        Tiefe
                        <select
                          value={depth}
                          onChange={(event) => setDepth(Number(event.target.value))}
                        >
                          <option value={1}>1</option>
                          <option value={2}>2</option>
                          <option value={3}>3</option>
                        </select>
                      </label>
                    )}
                    {pickingTarget && (
                      <span className="cgp-picking">
                        Ziel wählen: klick ein Symbol
                        <button className="wk-btn" onClick={() => setPickingTarget(false)}>
                          Abbrechen
                        </button>
                      </span>
                    )}
                  </div>

                  <div className="cgp-pane-body cgp-center-body">
                    {view === "clusters" && (
                      <ClusterMapPanel
                        level={clusterLevel.data ?? null}
                        isLoading={clusterLevel.isLoading}
                        error={
                          clusterLevel.isError
                            ? clusterLevel.error instanceof Error
                              ? clusterLevel.error.message
                              : "Bereiche nicht ladbar"
                            : null
                        }
                        selectedPath={selectedCluster?.path ?? null}
                        namingBusy={naming}
                        onOpen={openCluster}
                        onSelect={(cluster) => {
                          setSelectedCluster(cluster);
                          if (cluster && !cluster.has_children) void enterCluster(cluster);
                        }}
                        onSelectEdge={setSelectedClusterEdge}
                        onName={nameClusters}
                      />
                    )}
                    {view === "map" && (
                      <CodeMapPanel
                        map={map.map}
                        positions={map.positions}
                        focusId={focusId}
                        selectedId={selectedId}
                        edgeKinds={edgeKinds}
                        isLoading={map.isLoading}
                        error={map.error}
                        focusSignature={nodeDetail.data?.facts?.signature ?? null}
                        expanded={mapBig}
                        onToggleEdgeKind={toggleEdgeKind}
                        onSelect={selectNode}
                        onExpand={map.expand}
                        onCollapse={map.collapse}
                        onSelectEdge={setSelectedEdge}
                        onReset={map.reset}
                        onDismissBudget={map.dismissBudgetWarning}
                        onToggleExpanded={() => toggleBig("map")}
                      />
                    )}
                    {view === "feature" && (
                      <div className="cgp-feature">
                        <div className="cgp-feature-note">
                          <Waypoints size={13} />
                          <span>
                            Karte über die Treffer einer Antwort:{" "}
                            <strong>{feature?.roots.length ?? 0}</strong>
                            {feature && feature.total > feature.roots.length
                              ? ` der ${feature.total} Funktionen`
                              : " Funktionen"}{" "}
                            in der Mitte, links wer sie aufruft, rechts was sie benutzen.
                          </span>
                          <span className="cgp-spacer" />
                          <button className="wk-btn" onClick={() => setView("clusters")}>
                            Zurück zu den Bereichen
                          </button>
                        </div>
                        <CodeMapPanel
                          map={featureMap.map}
                          positions={featureMap.positions}
                          focusId={null}
                          focusIds={featureRootIds}
                          selectedId={selectedId}
                          edgeKinds={edgeKinds}
                          isLoading={featureMap.isLoading}
                          error={featureMap.error}
                          expanded={mapBig}
                          onToggleEdgeKind={toggleEdgeKind}
                          onSelect={selectNode}
                          onExpand={featureMap.expand}
                          onCollapse={featureMap.collapse}
                          onSelectEdge={setSelectedEdge}
                          onReset={() => setFeature(null)}
                          onDismissBudget={featureMap.dismissBudgetWarning}
                          onToggleExpanded={() => toggleBig("map")}
                        />
                      </div>
                    )}
                    {view === "code" && (
                      <Suspense fallback={<p className="muted cg-empty">Editor wird geladen …</p>}>
                        <CodeEditorPanel
                          projectId={projectId}
                          node={nodeDetail.data ?? null}
                          onReindex={() => indexMutation.mutate()}
                          onOpenInWorkstation={openInWorkstation}
                        />
                      </Suspense>
                    )}
                    {view === "diagram" && (
                      <Suspense fallback={<p className="muted cg-empty">Diagramm wird geladen …</p>}>
                        <CodeDiagramPanel
                          projectId={projectId}
                          nodeId={selectedId}
                          onOpenSymbol={openInWorkstation}
                        />
                      </Suspense>
                    )}
                    {view === "path" && (
                      <CodePathPanel
                        projectId={projectId}
                        fromNode={nodeDetail.data ?? null}
                        toNode={pathTarget.data ?? null}
                        onPickTarget={() => setPickingTarget(true)}
                        onSelectNode={selectNode}
                        onOpen={openInWorkstation}
                      />
                    )}
                    {view === "impact" && (
                      <ImpactPanel
                        projectId={projectId}
                        nodeId={selectedId}
                        onFocus={(hit) => selectNode(hit.id)}
                        onOpen={openInWorkstation}
                      />
                    )}
                    {view === "sandbox" && <SandboxPanel projectId={projectId} />}
                    {view === "tangle" && (
                      <TanglePanel
                        projectId={projectId}
                        nodeId={selectedId}
                        provider={llm.provider ?? globalProvider ?? null}
                        model={llm.model ?? globalModel ?? null}
                        onFocus={(id) => selectNode(id)}
                        onOpen={openInWorkstation}
                      />
                    )}
                  </div>
                </div>
              </Panel>
              {/* Die Kantentabelle gehört zur Karte: sie führt genau die Kanten
                  auf, die oben gezeichnet sind. Neben dem Diagramm — das seine
                  eigene Belegliste mitbringt — stand sie auf „0 Kanten" und las
                  sich wie ein Widerspruch zu den siebzehn darüber. */}
              {(view === "map" || view === "feature" || view === "clusters") && (
                <>
                  <PanelResizeHandle className="wk-resize wk-resize--h" />
                  <Panel
                    id="cg-evidence"
                    ref={evidencePanelRef}
                    order={2}
                    defaultSize={24}
                    minSize={12}
                    collapsible
                    className="cgp-slot"
                  >
                    {/* Dieselbe Aufgabe, zwei Datenquellen: auf der Symbolkarte
                        liegen die Kanten schon im Speicher, auf der
                        Bereichskarte müssen die echten hinter einem Aggregat
                        erst geholt werden. */}
                    {view === "map" || view === "feature" ? (
                      <CodeEvidencePanel
                        map={view === "map" ? map.map : featureMap.map}
                        edgeKinds={edgeKinds}
                        selectedEdge={selectedEdge}
                        onOpen={openInWorkstation}
                        onSelectNode={selectNode}
                      />
                    ) : (
                      <ClusterEvidencePanel
                        projectId={projectId}
                        edge={selectedClusterEdge}
                        edgeKinds={edgeKinds}
                        onOpen={openInWorkstation}
                        onSelectNode={(nodeId) => {
                          setSelectedId(nodeId);
                          setFocusId(nodeId);
                          setView("map");
                        }}
                      />
                    )}
                  </Panel>
                </>
              )}
            </PanelGroup>
          </Panel>
          <PanelResizeHandle className="wk-resize wk-resize--v" />

          <Panel
            ref={rightPanelRef}
            order={3}
            defaultSize={25}
            minSize={16}
            collapsible
            className="cgp-slot"
          >
            <PanelGroup direction="vertical" autoSaveId="sciencekg.code.right">
              <Panel
                ref={inspectorPanelRef}
                order={1}
                defaultSize={56}
                minSize={18}
                className="cgp-slot"
              >
                <CodeInspectorPanel
                  projectId={projectId}
                  nodeId={selectedId}
                  provider={llm.provider ?? globalProvider ?? null}
                  model={llm.model ?? globalModel ?? null}
                  onEdit={() => setView("code")}
                  onOpen={openInWorkstation}
                  onFocus={selectNode}
                  onShowOnMap={showOnMap}
                  onPathTo={() => {
                    setPickingTarget(true);
                    setView("path");
                  }}
                  onToggleWide={() => toggleBig("inspector")}
                  wide={inspectorBig}
                />
              </Panel>
              <PanelResizeHandle className="wk-resize wk-resize--h" />
              <Panel order={2} defaultSize={44} minSize={16} className="cgp-slot">
                {/* Hier steht der Faden, nicht die Einzelfrage: auf dieser Seite
                    ist die übliche Frage „wo passiert X", und darauf folgen
                    Rückfragen. Die Einzelfrage-Form (`CodeAskPanel`) bleibt für
                    die schmale Werkstatt-Spalte. */}
                <CodeChatPanel
                  projectId={projectId}
                  onOpen={openInWorkstation}
                  onSelectNode={selectNode}
                  onShowFeature={showFeature}
                  onEditNode={editNode}
                  llm={{
                    provider: llm.provider ?? globalProvider ?? undefined,
                    model: llm.model ?? globalModel ?? undefined,
                  }}
                  expanded={askBig}
                  onToggleExpanded={() => toggleBig("ask")}
                />
              </Panel>
            </PanelGroup>
          </Panel>
        </PanelGroup>
      </div>
    </section>
  );
}

export default CodeGraphPage;
