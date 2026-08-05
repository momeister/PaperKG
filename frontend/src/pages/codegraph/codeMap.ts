/**
 * Die Rechenregeln der Karte — ohne React, damit sie prüfbar sind.
 *
 * Die Karte ist die autoritativste Anzeige des ganzen Werkzeugs: ein Bild wirkt
 * wahrer als eine Liste. Deshalb sind die Regeln hier ausdrücklich und getestet
 * und nicht in einem Render-Zweig versteckt:
 *
 *   * Eine geratene Kante ist **gestrichelt**, immer.
 *   * Ein Zusammenführen, das das Knotenbudget sprengt, wird **ganz** verworfen —
 *     eine halb angewandte Erweiterung sähe aus wie ein vollständiger Graph.
 *   * Kanten werden über `von|nach|art` dedupliziert, nicht über die Zielknoten:
 *     dieselben zwei Symbole können sich gleichzeitig aufrufen *und* lesen.
 */
import type {
  CodeConfidence,
  CodeEdgeKind,
  CodeGraphSlice,
  CodeNodeId,
  CodeSliceEdge,
  CodeSymbolHit,
} from "../../types";

/**
 * Obergrenze der Karte. 600 ist keine technische Schranke, sondern eine
 * Lesbarkeitsgrenze: darüber ist das Bild ein Knäuel, und ein Knäuel erklärt
 * nichts. Das Layout ist O(n²) — bei 600 Knoten regelt `iterationsFor()` auf
 * ~19 Iterationen herunter, das bleibt im Worker unter etwa 200 ms.
 */
export const MAX_MAP_NODES = 600;

/** Standardauswahl der Kantenarten. `contains` fehlt hier mit Absicht. */
export const DEFAULT_EDGE_KINDS: CodeEdgeKind[] = [
  "calls",
  "reads",
  "writes",
  "inherits",
  "implements",
];

export type MapNode = CodeSymbolHit & {
  /** Abstand vom Fokus in Sprüngen — steuert Deckkraft und Startposition. */
  hop: number;
  /**
   * **Vorzeichenbehafteter** Abstand: negativ links (wer ruft mich auf),
   * positiv rechts (wen rufe ich auf), 0 ist der Fokus selbst. Das ist die
   * Spalte auf der Karte — und der Grund, warum die beiden Richtungen getrennt
   * geholt werden müssen: aus einer zusammengeworfenen Nachbarliste liesse sich
   * nicht mehr sagen, auf welche Seite ein Knoten gehört.
   */
  column: number;
  /** Wurde von hier aus schon nachgeladen? */
  expanded: boolean;
};

export type MapState = {
  nodes: Map<CodeNodeId, MapNode>;
  edges: Map<string, CodeSliceEdge>;
  /** Das Backend hat seinen eigenen Ausschnitt gekürzt. */
  truncated: boolean;
  /** *Unsere* Grenze wurde erreicht — ein anderer Sachverhalt als `truncated`. */
  atBudget: boolean;
};

export const EMPTY_MAP: MapState = {
  nodes: new Map(),
  edges: new Map(),
  truncated: false,
  atBudget: false,
};

export function edgeKey(edge: CodeSliceEdge): string {
  return `${edge.from}|${edge.to}|${edge.kind}`;
}

/**
 * Einen geholten Ausschnitt in den Bestand einarbeiten.
 *
 * Passt das Ergebnis nicht ins Budget, kommt der **alte** Zustand mit
 * `atBudget: true` zurück. Halb einarbeiten wäre schlimmer als gar nicht: der
 * Betrachter sähe einen Graphen, dem stillschweigend Knoten fehlen.
 */
export function mergeSlice(
  state: MapState,
  slice: CodeGraphSlice,
  options: { hop?: number; column?: number; expandedFrom?: CodeNodeId } = {},
): MapState {
  const hop = options.hop ?? 1;
  const column = options.column ?? hop;
  const nextNodes = new Map(state.nodes);
  const nextEdges = new Map(state.edges);

  for (const hit of slice.nodes) {
    const existing = nextNodes.get(hit.id);
    if (existing) {
      // Kleinerer Abstand gewinnt: ein Knoten, der auch direkt am Fokus hängt,
      // ist kein Randknoten, nur weil er zusätzlich weiter draußen auftaucht.
      // Die Spalte zieht mit — sonst stünde ein Aufrufer rechts, nur weil er
      // zufällig zuerst über einen Umweg hereinkam.
      if (hop < existing.hop) nextNodes.set(hit.id, { ...existing, hop, column });
      continue;
    }
    nextNodes.set(hit.id, { ...hit, hop, column, expanded: false });
  }

  if (nextNodes.size > MAX_MAP_NODES) {
    return { ...state, atBudget: true };
  }

  for (const edge of slice.edges) {
    // Kanten auf Knoten, die es nicht in den Ausschnitt geschafft haben, würden
    // in xyflow als unsichtbare Verbindung enden — und dort eine Warnung werfen.
    if (!nextNodes.has(edge.from) || !nextNodes.has(edge.to)) continue;
    nextEdges.set(edgeKey(edge), edge);
  }

  if (options.expandedFrom) {
    const source = nextNodes.get(options.expandedFrom);
    if (source) nextNodes.set(options.expandedFrom, { ...source, expanded: true });
  }

  return {
    nodes: nextNodes,
    edges: nextEdges,
    truncated: state.truncated || slice.truncated,
    // Bleibt stehen, bis der Betrachter es wegklickt. Eine Karte wird aus
    // mehreren Ausschnitten zusammengesetzt; hätte einer davon das Budget
    // gesprengt und der nächste setzte die Meldung zurück, verschwände der
    // Hinweis auf die fehlenden Knoten, die Knoten aber blieben fehlend.
    atBudget: state.atBudget,
  };
}

/**
 * Einen Knoten wieder einklappen: alles wegwerfen, was danach nur noch über ihn
 * am Fokus hing. Breitensuche vom Fokus über die verbleibenden Kanten — was sie
 * nicht erreicht, gehörte zu diesem Ast.
 */
export function collapseNode(state: MapState, focusId: CodeNodeId, nodeId: CodeNodeId): MapState {
  if (nodeId === focusId) return state;

  const adjacency = new Map<CodeNodeId, CodeNodeId[]>();
  const link = (from: CodeNodeId, to: CodeNodeId) => {
    const list = adjacency.get(from);
    if (list) list.push(to);
    else adjacency.set(from, [to]);
  };
  for (const edge of state.edges.values()) {
    link(edge.from, edge.to);
    link(edge.to, edge.from);
  }

  // Der eingeklappte Knoten bleibt — er ist erreichbar, aber die Suche geht
  // nicht *durch* ihn weiter. Was danach unerreicht bleibt, hing nur an ihm.
  const reachable = new Set<CodeNodeId>([focusId]);
  const queue: CodeNodeId[] = [focusId];
  while (queue.length) {
    const current = queue.shift()!;
    if (current === nodeId) continue;
    for (const next of adjacency.get(current) ?? []) {
      if (reachable.has(next)) continue;
      reachable.add(next);
      queue.push(next);
    }
  }

  const nodes = new Map<CodeNodeId, MapNode>();
  for (const [id, node] of state.nodes) {
    if (!reachable.has(id)) continue;
    nodes.set(id, id === nodeId ? { ...node, expanded: false } : node);
  }
  const edges = new Map(
    [...state.edges].filter(([, edge]) => nodes.has(edge.from) && nodes.has(edge.to)),
  );

  return { ...state, nodes, edges, atBudget: false };
}

/** Nur die Kanten der eingeschalteten Arten. Knoten bleiben stehen. */
export function visibleEdges(state: MapState, kinds: CodeEdgeKind[]): CodeSliceEdge[] {
  const allowed = new Set(kinds);
  return [...state.edges.values()].filter((edge) => allowed.has(edge.kind));
}

/**
 * Der Strichstil einer Kante. **Geraten heißt gestrichelt** — das ist der Punkt,
 * an dem die Regel des Werkzeugs im Bild ankommt, und deshalb steht sie hier
 * einzeln und nicht als Ternär in einem JSX-Attribut.
 */
export function edgeStyleFor(confidence: CodeConfidence): {
  strokeDasharray?: string;
  strokeWidth: number;
  opacity: number;
} {
  switch (confidence) {
    case "measured":
    case "verified":
      return { strokeWidth: 1.6, opacity: 1 };
    case "resolved":
      return { strokeWidth: 1.2, opacity: 0.75 };
    default:
      return { strokeDasharray: "5 4", strokeWidth: 1.2, opacity: 0.6 };
  }
}

/** Gruppe einer Kantenart — bestimmt die Farbe; siehe EDGE_GROUPS in shared.tsx. */
export function edgeGroupOf(kind: CodeEdgeKind): "reference" | "structure" | "type" | "runtime" {
  switch (kind) {
    case "calls":
    case "reads":
    case "writes":
      return "reference";
    case "contains":
    case "imports":
    case "inherits":
    case "implements":
      return "structure";
    case "param_type":
    case "returns_type":
    case "throws":
      return "type";
    default:
      return "runtime";
  }
}

/** Knotenbreite aus der Relevanz. Klein genug zum Lesen, gross genug zum Sehen. */
export function nodeWidthFor(relevance: number): number {
  const clamped = Math.max(0, Math.min(1, relevance ?? 0));
  return Math.round(108 + clamped * 60);
}

/**
 * Wie viele Treffer als Wurzeln auf die Karte dürfen.
 *
 * Jede Wurzel kostet **zwei** Anfragen (ein und aus, nie `both`). Bei
 * vierundzwanzig Treffern wären das achtundvierzig gleichzeitige Abfragen und
 * ein Bild, in dem nichts mehr auseinanderzuhalten ist. Acht ist die Grenze, ab
 * der eine Feature-Karte noch eine Aussage macht — und dass gekürzt wurde, steht
 * daneben.
 */
export const MAX_MAP_ROOTS = 8;

/** Die relevantesten Treffer als Kartenwurzeln, mit der Gesamtzahl daneben. */
export function pickMapRoots<T extends { id: CodeNodeId; relevance?: number }>(
  hits: T[],
  max: number = MAX_MAP_ROOTS,
): { roots: T[]; total: number } {
  const unique = new Map<CodeNodeId, T>();
  for (const hit of hits) if (!unique.has(hit.id)) unique.set(hit.id, hit);
  const ordered = [...unique.values()].sort(
    (a, b) => (b.relevance ?? 0) - (a.relevance ?? 0) || a.id.localeCompare(b.id),
  );
  return { roots: ordered.slice(0, max), total: unique.size };
}

/** Zählt, wie viel des sichtbaren Bildes geraten ist. Gehört dauerhaft ins Bild. */
export function guessedShareOf(edges: CodeSliceEdge[]): number | null {
  if (!edges.length) return null;
  const guessed = edges.filter((edge) => edge.confidence === "guessed").length;
  return Math.round((guessed / edges.length) * 100);
}
