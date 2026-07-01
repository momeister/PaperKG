import type { GraphEdge, GraphNode, Point } from "./types";

// `lib` in tsconfig.json is DOM-only (no "webworker"), so the ambient `self`
// from lib.dom.d.ts (Window) doesn't have the Worker-scope members we need.
// Shadow it locally with the exact shape this module relies on — module-scoped
// because this file has imports/exports, so it doesn't leak into the rest of
// the program or collide with the DOM lib's `self`.
declare const self: {
  onmessage: ((event: MessageEvent<{ nodes: GraphNode[]; edges: GraphEdge[] }>) => void) | null;
  postMessage: (message: { positions: Array<[string, Point]> }) => void;
};

const BASE_NODE_COUNT = 150;
const BASE_ITERATIONS = 300;
const MIN_ITERATIONS = 40;
// "Settledness" the original fixed 300-iterations-at-0.985-cooling reaches.
const FINAL_TEMPERATURE_RATIO = 0.985 ** BASE_ITERATIONS;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/**
 * Keeps total layout work (n² × iterations) roughly constant as node counts
 * grow past BASE_NODE_COUNT, so worst-case wall-clock stays bounded instead
 * of blowing up quadratically with node count.
 */
function iterationsFor(n: number): number {
  if (n <= BASE_NODE_COUNT) {
    return BASE_ITERATIONS;
  }
  const scaled = Math.round((BASE_ITERATIONS * BASE_NODE_COUNT * BASE_NODE_COUNT) / (n * n));
  return clamp(scaled, MIN_ITERATIONS, BASE_ITERATIONS);
}

/**
 * Deterministisches kräftebasiertes Layout (Fruchterman–Reingold).
 * Statt eines starren Rasters (das "wie eine Straße" aussieht) ordnen sich
 * verbundene Knoten organisch zu Clustern – das wirkt runder und lesbarer.
 * Reines JS, O(n²) pro Iteration; die Iterationszahl skaliert über
 * iterationsFor() mit der Knotenzahl, damit große Graphen nicht zu
 * mehrsekündigen Läufen führen. Läuft in graphLayoutWorker (Web Worker),
 * nicht auf dem Main-Thread.
 * Bei regelmäßig >3000 Knoten lohnt sich eine räumliche Annäherung
 * (z. B. Barnes-Hut/Quadtree) für die Abstoßung statt der reinen
 * Iterationsreduktion hier.
 */
function computeForceLayout(nodes: GraphNode[], edges: GraphEdge[]): Map<string, Point> {
  const n = nodes.length;
  const result = new Map<string, Point>();
  if (n === 0) {
    return result;
  }

  // Start auf einer Spirale (deterministisch → stabiles Layout pro Datensatz).
  const pos: Point[] = nodes.map((_, i) => {
    const angle = i * 2.399963; // goldener Winkel → gleichmäßige Verteilung
    const radius = 60 + 26 * Math.sqrt(i);
    return { x: Math.cos(angle) * radius, y: Math.sin(angle) * radius };
  });

  const index = new Map(nodes.map((node, i) => [node.id, i]));
  const links = edges
    .map((edge) => [index.get(edge.source), index.get(edge.target)] as const)
    .filter((pair): pair is readonly [number, number] => pair[0] !== undefined && pair[1] !== undefined && pair[0] !== pair[1]);

  const k = 150; // gewünschter Knotenabstand
  const iterations = iterationsFor(n);
  const coolingFactor = FINAL_TEMPERATURE_RATIO ** (1 / iterations);
  let temperature = 340;

  for (let step = 0; step < iterations; step++) {
    const disp: Point[] = pos.map(() => ({ x: 0, y: 0 }));

    // Abstoßung zwischen allen Knotenpaaren.
    for (let i = 0; i < n; i++) {
      for (let j = i + 1; j < n; j++) {
        let dx = pos[i].x - pos[j].x;
        let dy = pos[i].y - pos[j].y;
        let dist = Math.sqrt(dx * dx + dy * dy);
        if (dist < 0.01) {
          // identische Position aufbrechen
          dx = (Math.random() - 0.5) * 0.1;
          dy = (Math.random() - 0.5) * 0.1;
          dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
        }
        const force = (k * k) / dist;
        const ux = dx / dist;
        const uy = dy / dist;
        disp[i].x += ux * force;
        disp[i].y += uy * force;
        disp[j].x -= ux * force;
        disp[j].y -= uy * force;
      }
    }

    // Anziehung entlang der Kanten.
    for (const [a, b] of links) {
      const dx = pos[a].x - pos[b].x;
      const dy = pos[a].y - pos[b].y;
      const dist = Math.sqrt(dx * dx + dy * dy) || 0.01;
      const force = (dist * dist) / k;
      const ux = dx / dist;
      const uy = dy / dist;
      disp[a].x -= ux * force;
      disp[a].y -= uy * force;
      disp[b].x += ux * force;
      disp[b].y += uy * force;
    }

    // Sanfte Schwerkraft zur Mitte, damit nichts wegdriftet.
    for (let i = 0; i < n; i++) {
      disp[i].x -= pos[i].x * 0.018;
      disp[i].y -= pos[i].y * 0.018;
    }

    // Bewegung pro Schritt durch die Temperatur begrenzen (Abkühlung).
    for (let i = 0; i < n; i++) {
      const length = Math.sqrt(disp[i].x * disp[i].x + disp[i].y * disp[i].y) || 0.01;
      const limited = Math.min(length, temperature);
      pos[i].x += (disp[i].x / length) * limited;
      pos[i].y += (disp[i].y / length) * limited;
    }
    temperature *= coolingFactor;
  }

  nodes.forEach((node, i) => result.set(node.id, pos[i]));
  return result;
}

self.onmessage = (event) => {
  const { nodes, edges } = event.data;
  const layout = computeForceLayout(nodes, edges);
  self.postMessage({ positions: Array.from(layout.entries()) });
};
