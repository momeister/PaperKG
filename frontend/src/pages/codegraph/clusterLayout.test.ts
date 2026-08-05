import { describe, expect, it } from "vitest";

import {
  clusterLayerLabel,
  clusterLayers,
  clusterWidthFor,
  edgeId,
} from "./clusterLayout";

const nodes = (...paths: string[]) => paths.map((path) => ({ path }));
const edge = (from: string, to: string) => ({ from, to });

describe("clusterLayers", () => {
  it("schichtet nach Abhängigkeit: wer benutzt wird, steht rechts", () => {
    const { layers, backEdges } = clusterLayers(
      nodes("api", "query", "storage"),
      [edge("api", "query"), edge("query", "storage")],
    );

    expect(layers.get("api")).toBe(0);
    expect(layers.get("query")).toBe(1);
    expect(layers.get("storage")).toBe(2);
    expect(backEdges.size).toBe(0);
  });

  it("setzt einen Bereich hinter seinen spätesten Vorgänger, nicht seinen ersten", () => {
    // api → storage direkt *und* über query. storage muss hinter query stehen,
    // sonst zeigte eine Kante nach links und das Bild wäre gegen sich selbst.
    const { layers } = clusterLayers(
      nodes("api", "query", "storage"),
      [edge("api", "query"), edge("api", "storage"), edge("query", "storage")],
    );

    expect(layers.get("storage")).toBe(2);
  });

  it("bricht einen Ring deterministisch und meldet die Kante, statt sie zu schlucken", () => {
    const first = clusterLayers(nodes("api", "storage"), [
      edge("api", "storage"),
      edge("storage", "api"),
    ]);
    // Gleiche Eingabe in anderer Reihenfolge muss dasselbe ergeben.
    const second = clusterLayers(nodes("storage", "api"), [
      edge("storage", "api"),
      edge("api", "storage"),
    ]);

    expect(first.backEdges.size).toBe(1);
    expect([...first.backEdges]).toEqual([...second.backEdges]);
    expect(first.layers.get("api")).toBe(second.layers.get("api"));
    expect(first.layers.get("storage")).toBe(second.layers.get("storage"));

    // Und die Ringkante ist eine der beiden echten, keine erfundene.
    const [broken] = [...first.backEdges];
    expect(["api|storage", "storage|api"]).toContain(broken);
  });

  it("übersteht einen Ring aus drei Bereichen ohne Endlosschleife", () => {
    const { layers, backEdges } = clusterLayers(
      nodes("a", "b", "c"),
      [edge("a", "b"), edge("b", "c"), edge("c", "a")],
    );

    expect(backEdges.size).toBe(1);
    expect([...layers.values()].every((layer) => Number.isFinite(layer))).toBe(true);
  });

  it("ignoriert Kanten auf Bereiche, die es auf dieser Ebene nicht gibt", () => {
    const { layers, backEdges } = clusterLayers(nodes("api"), [
      edge("api", "gibtsnicht"),
      edge("auchnicht", "api"),
    ]);

    expect(layers.get("api")).toBe(0);
    expect(backEdges.size).toBe(0);
  });

  it("legt Bereiche ohne jede Kante in die erste Schicht", () => {
    const { layers } = clusterLayers(nodes("alleine", "auch"), []);
    expect(layers.get("alleine")).toBe(0);
    expect(layers.get("auch")).toBe(0);
  });
});

describe("edgeId", () => {
  it("unterscheidet Richtung", () => {
    expect(edgeId(edge("a", "b"))).not.toBe(edgeId(edge("b", "a")));
  });
});

describe("clusterLayerLabel", () => {
  it("sagt, was die Achse bedeutet, statt nur eine Zahl zu zeigen", () => {
    expect(clusterLayerLabel(0, 0)).toContain("keine Abhängigkeit");
    expect(clusterLayerLabel(0, 2)).toContain("nichts hängt hiervon ab");
    expect(clusterLayerLabel(2, 2)).toContain("trägt alles");
  });
});

describe("clusterWidthFor", () => {
  it("wächst mit der Grösse, aber gedämpft", () => {
    const small = clusterWidthFor(3);
    const medium = clusterWidthFor(300);
    const huge = clusterWidthFor(4000);

    expect(small).toBeLessThan(medium);
    expect(medium).toBeLessThan(huge);
    // Der grösste Bereich darf den kleinsten nicht um ein Vielfaches erschlagen.
    expect(huge / small).toBeLessThan(2);
  });

  it("kommt mit null Symbolen klar", () => {
    expect(Number.isFinite(clusterWidthFor(0))).toBe(true);
  });
});
