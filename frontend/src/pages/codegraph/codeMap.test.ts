import { describe, expect, it } from "vitest";

import type { CodeGraphSlice, CodeSliceEdge, CodeSymbolHit } from "../../types";
import {
  DEFAULT_EDGE_KINDS,
  EMPTY_MAP,
  MAX_MAP_NODES,
  collapseNode,
  edgeStyleFor,
  guessedShareOf,
  mergeSlice,
  pickMapRoots,
  visibleEdges,
} from "./codeMap";

function hit(id: string, extra: Partial<CodeSymbolHit> = {}): CodeSymbolHit {
  return {
    id,
    name: id,
    qualified: `mod.${id}`,
    kind: "function",
    lang: "python",
    path: "src/x.py",
    line: 1,
    relevance: 0.5,
    ...extra,
  };
}

function edge(from: string, to: string, extra: Partial<CodeSliceEdge> = {}): CodeSliceEdge {
  return {
    from,
    to,
    kind: "calls",
    confidence: "verified",
    occurrences: 1,
    evidence_line: 7,
    evidence_path: "src/x.py",
    ...extra,
  };
}

function slice(nodes: CodeSymbolHit[], edges: CodeSliceEdge[], truncated = false): CodeGraphSlice {
  return { nodes, edges, truncated };
}

describe("mergeSlice", () => {
  it("ist idempotent", () => {
    const input = slice([hit("a"), hit("b")], [edge("a", "b")]);
    const once = mergeSlice(EMPTY_MAP, input);
    const twice = mergeSlice(once, input);
    expect(twice.nodes.size).toBe(2);
    expect(twice.edges.size).toBe(1);
  });

  it("unterscheidet Kanten derselben zwei Knoten nach Art", () => {
    // Zwei Symbole können sich gleichzeitig aufrufen *und* lesen — würde über
    // die Knoten dedupliziert, verschwände eine der beiden Aussagen.
    const merged = mergeSlice(
      EMPTY_MAP,
      slice([hit("a"), hit("b")], [edge("a", "b", { kind: "calls" }), edge("a", "b", { kind: "reads" })]),
    );
    expect(merged.edges.size).toBe(2);
  });

  it("lässt Kanten auf Knoten weg, die nicht im Ausschnitt sind", () => {
    const merged = mergeSlice(EMPTY_MAP, slice([hit("a")], [edge("a", "fehlt")]));
    expect(merged.edges.size).toBe(0);
  });

  it("behält den kleineren Abstand zum Fokus", () => {
    const near = mergeSlice(EMPTY_MAP, slice([hit("a")], []), { hop: 1 });
    const far = mergeSlice(near, slice([hit("a")], []), { hop: 3 });
    expect(far.nodes.get("a")!.hop).toBe(1);
  });

  it("verwirft ein zu grosses Zusammenführen GANZ statt halb", () => {
    // Halb angewandt sähe die Karte aus wie ein vollständiger Graph, dem
    // stillschweigend Knoten fehlen — schlimmer als eine sichtbare Grenze.
    const many = Array.from({ length: MAX_MAP_NODES + 5 }, (_, index) => hit(`n${index}`));
    const before = mergeSlice(EMPTY_MAP, slice([hit("a")], []));
    const after = mergeSlice(before, slice(many, []));

    expect(after.atBudget).toBe(true);
    expect(after.nodes.size).toBe(before.nodes.size);
    expect(after.nodes.has("n0")).toBe(false);
  });

  it("lässt die Budgetmeldung stehen, wenn danach ein kleiner Ausschnitt kommt", () => {
    // Eine Karte über mehrere Wurzeln wird aus vielen Ausschnitten gebaut.
    // Setzte der nächste die Meldung zurück, verschwände der Hinweis auf die
    // fehlenden Knoten — die Knoten aber blieben fehlend.
    const many = Array.from({ length: MAX_MAP_NODES + 5 }, (_, index) => hit(`n${index}`));
    const busted = mergeSlice(EMPTY_MAP, slice(many, []));
    const after = mergeSlice(busted, slice([hit("a")], []));

    expect(busted.atBudget).toBe(true);
    expect(after.atBudget).toBe(true);
    expect(after.nodes.has("a")).toBe(true);
  });

  it("merkt sich das Kürzen des Backends getrennt vom eigenen Budget", () => {
    const merged = mergeSlice(EMPTY_MAP, slice([hit("a")], [], true));
    expect(merged.truncated).toBe(true);
    expect(merged.atBudget).toBe(false);
  });
});

describe("collapseNode", () => {
  it("wirft weg, was nur über den eingeklappten Knoten hing — ihn selbst aber nicht", () => {
    // fokus → b → c ;  fokus → d
    const state = mergeSlice(
      EMPTY_MAP,
      slice(
        [hit("fokus"), hit("b"), hit("c"), hit("d")],
        [edge("fokus", "b"), edge("b", "c"), edge("fokus", "d")],
      ),
    );
    const collapsed = collapseNode(state, "fokus", "b");

    expect([...collapsed.nodes.keys()].sort()).toEqual(["b", "d", "fokus"]);
    expect(collapsed.nodes.get("b")!.expanded).toBe(false);
  });

  it("lässt den Fokus selbst unberührt", () => {
    const state = mergeSlice(EMPTY_MAP, slice([hit("fokus"), hit("b")], [edge("fokus", "b")]));
    expect(collapseNode(state, "fokus", "fokus")).toBe(state);
  });
});

describe("visibleEdges", () => {
  it("filtert nach Art und lässt die Knoten stehen", () => {
    const state = mergeSlice(
      EMPTY_MAP,
      slice(
        [hit("a"), hit("b")],
        [edge("a", "b", { kind: "calls" }), edge("a", "b", { kind: "contains" })],
      ),
    );
    const shown = visibleEdges(state, DEFAULT_EDGE_KINDS);
    expect(shown).toHaveLength(1);
    expect(shown[0].kind).toBe("calls");
    expect(state.nodes.size).toBe(2);
  });
});

describe("edgeStyleFor", () => {
  it("zeichnet Geratenes gestrichelt und Belegtes nicht", () => {
    // Die Regel des Werkzeugs, im Bild angekommen.
    expect(edgeStyleFor("guessed").strokeDasharray).toBeTruthy();
    expect(edgeStyleFor("verified").strokeDasharray).toBeUndefined();
    expect(edgeStyleFor("measured").strokeDasharray).toBeUndefined();
    expect(edgeStyleFor("resolved").strokeDasharray).toBeUndefined();
  });

  it("nimmt Geratenem Deckkraft", () => {
    expect(edgeStyleFor("guessed").opacity).toBeLessThan(edgeStyleFor("verified").opacity);
  });
});

describe("guessedShareOf", () => {
  it("zählt den Vermutungsanteil des sichtbaren Bildes", () => {
    expect(
      guessedShareOf([
        edge("a", "b", { confidence: "guessed" }),
        edge("b", "c", { confidence: "verified" }),
      ]),
    ).toBe(50);
  });

  it("meldet nichts statt 0 %, wenn es keine Kanten gibt", () => {
    expect(guessedShareOf([])).toBeNull();
  });
});

describe("pickMapRoots", () => {
  it("behält die relevantesten und meldet die Gesamtzahl", () => {
    // Die Gesamtzahl ist der eigentliche Punkt: eine stillschweigend gekürzte
    // Karte sähe aus wie das vollständige Feature.
    const hits = Array.from({ length: 12 }, (_, index) =>
      hit(`n${index}`, { relevance: index / 12 }),
    );
    const { roots, total } = pickMapRoots(hits, 3);
    expect(total).toBe(12);
    expect(roots.map((item) => item.id)).toEqual(["n11", "n10", "n9"]);
  });

  it("zählt dasselbe Symbol nur einmal", () => {
    // `focus_nodes` kann eine Stelle mehrfach nennen — zitiert *und* in der
    // Spur. Zwei Wurzeln daraus wären zwei Anfragen auf denselben Knoten.
    const { roots, total } = pickMapRoots([hit("a"), hit("a"), hit("b")]);
    expect(total).toBe(2);
    expect(roots).toHaveLength(2);
  });

  it("ist bei gleicher Relevanz stabil sortiert", () => {
    // Ohne den zweiten Schlüssel sprängen die Wurzeln bei jedem Rendern um.
    const first = pickMapRoots([hit("b"), hit("a"), hit("c")], 2);
    const second = pickMapRoots([hit("c"), hit("a"), hit("b")], 2);
    expect(first.roots.map((item) => item.id)).toEqual(second.roots.map((item) => item.id));
  });

  it("kommt mit einer leeren Liste zurecht", () => {
    expect(pickMapRoots([])).toEqual({ roots: [], total: 0 });
  });
});
