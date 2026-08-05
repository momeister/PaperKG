/**
 * Die Bereichskarte schichten: was oben aufsetzt, links — worauf es ruht, rechts.
 *
 * `columnLayout.ts` bleibt unangetastet und wird *benutzt*. Es kann eine Spalte
 * ordnen, sobald jemand sagt, welche Spalte ein Knoten hat — und genau das ist
 * hier die einzige offene Frage. Bei der Symbolkarte kommt die Spalte aus dem
 * Abstand zum Fokus. Eine Bereichskarte hat keinen Fokus: sie zeigt alle
 * Bereiche gleichzeitig, und die Aussage ist nicht „wie weit weg", sondern
 * **wer hängt von wem ab**.
 *
 * Also: Schicht 0 sind die Bereiche, von denen nichts abhängt — die Aussenkante,
 * die Einstiege. Jede Kante schiebt ihr Ziel eine Schicht nach rechts. Wer ganz
 * rechts steht, wird von vielen benutzt und benutzt selbst nichts mehr: das
 * Fundament.
 *
 * **Ringe werden gezeigt, nicht versteckt.** `api` ↔ `storage` kommt in echten
 * Projekten vor, und eine Schichtung müsste dafür eine der beiden Kanten
 * ignorieren. Welche, wird deterministisch entschieden (Tiefensuche in
 * alphabetischer Reihenfolge), und die ignorierte Kante kommt als Rückkante
 * zurück, damit die Karte sie als Ringabhängigkeit zeichnen kann statt sie
 * verschwinden zu lassen. Eine stillschweigend weggelassene Kante wäre auf einer
 * Karte, deren ganzer Zweck Abhängigkeiten sind, der schlimmste Fehler.
 *
 * Deterministisch, synchron, ohne Kräftespiel — wie überall in diesem Werkzeug.
 */

export type ClusterLayoutNode = { path: string };
export type ClusterLayoutEdge = { from: string; to: string };

export type ClusterLayering = {
  /** Bereichspfad → Schicht (0 = ganz links). */
  layers: Map<string, number>;
  /** `from|to` jeder Kante, die einen Ring geschlossen hätte. */
  backEdges: Set<string>;
};

export function edgeId(edge: ClusterLayoutEdge): string {
  return `${edge.from}|${edge.to}`;
}

/**
 * Schichten zuweisen und Ringkanten benennen.
 *
 * Zwei Durchgänge: erst eine Tiefensuche in fester Reihenfolge, die jede Kante
 * auf einen Knoten *im aktuellen Pfad* als Rückkante markiert; dann längster
 * Pfad über den verbleibenden kreisfreien Rest.
 */
export function clusterLayers(
  nodes: ClusterLayoutNode[],
  edges: ClusterLayoutEdge[],
): ClusterLayering {
  const ids = nodes.map((node) => node.path).sort((a, b) => a.localeCompare(b));
  const known = new Set(ids);
  const outgoing = new Map<string, string[]>();
  for (const id of ids) outgoing.set(id, []);
  for (const edge of edges) {
    if (!known.has(edge.from) || !known.has(edge.to)) continue;
    outgoing.get(edge.from)!.push(edge.to);
  }
  for (const list of outgoing.values()) list.sort((a, b) => a.localeCompare(b));

  // Durchgang 1: Ringkanten finden. `onPath` ist der aktuelle Suchpfad — eine
  // Kante dorthin zurück schliesst einen Ring.
  const backEdges = new Set<string>();
  const visited = new Set<string>();
  const onPath = new Set<string>();

  const walk = (id: string) => {
    visited.add(id);
    onPath.add(id);
    for (const next of outgoing.get(id) ?? []) {
      if (onPath.has(next)) {
        backEdges.add(`${id}|${next}`);
        continue;
      }
      if (!visited.has(next)) walk(next);
    }
    onPath.delete(id);
  };
  for (const id of ids) if (!visited.has(id)) walk(id);

  // Durchgang 2: längster Pfad über den kreisfreien Rest. Ein Knoten sitzt eine
  // Schicht rechts von seinem spätesten Vorgänger — so steht nie ein Bereich
  // links von etwas, das ihn benutzt.
  const predecessors = new Map<string, string[]>();
  for (const id of ids) predecessors.set(id, []);
  for (const [from, targets] of outgoing) {
    for (const to of targets) {
      if (backEdges.has(`${from}|${to}`)) continue;
      predecessors.get(to)!.push(from);
    }
  }

  const layers = new Map<string, number>();
  const resolving = new Set<string>();
  const layerOf = (id: string): number => {
    const cached = layers.get(id);
    if (cached !== undefined) return cached;
    // Kann nach dem ersten Durchgang nicht mehr auftreten; die Bremse steht hier,
    // damit ein künftiger Fehler eine falsche Zahl liefert und keinen Absturz.
    if (resolving.has(id)) return 0;
    resolving.add(id);
    let layer = 0;
    for (const previous of predecessors.get(id) ?? []) {
      layer = Math.max(layer, layerOf(previous) + 1);
    }
    resolving.delete(id);
    layers.set(id, layer);
    return layer;
  };
  for (const id of ids) layerOf(id);

  return { layers, backEdges };
}

/**
 * Was die Achse bedeutet. Steht auf der Karte, weil „links" sonst nichts sagt.
 */
export function clusterLayerLabel(layer: number, maxLayer: number): string {
  if (maxLayer === 0) return "keine Abhängigkeit zwischen den Bereichen";
  if (layer === 0) return "nichts hängt hiervon ab";
  if (layer === maxLayer) return "trägt alles darüber";
  return `Stufe ${layer}`;
}

/**
 * Kachelbreite aus der Symbolzahl.
 *
 * Logarithmisch, weil die Zahlen um Grössenordnungen auseinanderliegen (drei
 * Symbole in `scripts/`, viertausend in `frontend/`). Linear wäre der grosse
 * Bereich fensterbreit und alle anderen gleich schmal — man sähe genau einen
 * Unterschied und sonst keinen.
 */
export function clusterWidthFor(symbols: number): number {
  const scaled = Math.log10(Math.max(1, symbols) + 1) / Math.log10(4001);
  return Math.round(150 + Math.min(1, scaled) * 110);
}
