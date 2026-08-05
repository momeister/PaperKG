import { describe, expect, it } from "vitest";

import { columnLabel, computeColumnLayout } from "./columnLayout";

/**
 * Die Karte behauptet mit ihrer Anordnung etwas: links kommt herein, rechts geht
 * hinaus. Kippt diese Zuordnung, zeigt das Bild das Gegenteil dessen, was es
 * soll — und zwar ohne dass irgendwo eine Fehlermeldung erschiene.
 */
const node = (id: string, column: number, weight = 0.5) => ({ id, column, weight });

describe("computeColumnLayout", () => {
  it("stellt Aufrufer links und Aufgerufene rechts vom Fokus", () => {
    const positions = computeColumnLayout([
      node("fokus", 0),
      node("ruft-auf", -1),
      node("wird-gerufen", 1),
    ]);

    expect(positions.get("ruft-auf")!.x).toBeLessThan(positions.get("fokus")!.x);
    expect(positions.get("wird-gerufen")!.x).toBeGreaterThan(positions.get("fokus")!.x);
  });

  it("hält Knoten derselben Spalte auf derselben Senkrechten", () => {
    const positions = computeColumnLayout([node("a", -1), node("b", -1), node("c", -1)]);
    const xs = new Set(["a", "b", "c"].map((id) => positions.get(id)!.x));
    expect(xs.size).toBe(1);
  });

  it("zentriert jede Spalte für sich", () => {
    // Eine Spalte mit drei Knoten soll neben einer mit dreissig mittig stehen,
    // nicht oben kleben.
    const many = Array.from({ length: 21 }, (_, index) => node(`m${index}`, 1));
    const positions = computeColumnLayout([node("fokus", 0), ...many]);

    const ys = many.map((entry) => positions.get(entry.id)!.y);
    const mid = (Math.min(...ys) + Math.max(...ys)) / 2;
    expect(Math.abs(mid - positions.get("fokus")!.y)).toBeLessThan(1);
  });

  it("ordnet das Relevanteste in die Mitte der Spalte, nicht an den Rand", () => {
    const positions = computeColumnLayout([
      node("stark", 1, 0.9),
      node("mittel", 1, 0.5),
      node("schwach", 1, 0.1),
    ]);
    const ys = ["stark", "mittel", "schwach"].map((id) => Math.abs(positions.get(id)!.y));
    expect(ys[0]).toBeLessThan(ys[1]);
    expect(ys[0]).toBeLessThan(ys[2]);
  });

  it("ist deterministisch — gleiche Eingabe, gleiche Positionen", () => {
    const input = [node("a", -1, 0.4), node("b", -1, 0.4), node("fokus", 0)];
    expect([...computeColumnLayout(input)]).toEqual([...computeColumnLayout(input)]);
  });

  it("kommt mit einer leeren Karte klar", () => {
    expect(computeColumnLayout([]).size).toBe(0);
  });
});

describe("columnLabel", () => {
  it("benennt die Richtungen ausdrücklich", () => {
    expect(columnLabel(-1)).toContain("ruft");
    expect(columnLabel(1)).toContain("aufgerufen");
    expect(columnLabel(0)).toBe("betrachtet");
    expect(columnLabel(-3)).toContain("davor");
    expect(columnLabel(2)).toContain("danach");
  });
});

describe("Unterspalten", () => {
  it("bricht eine überlange Spalte um, statt einen Streifen zu bauen", () => {
    // 30 Aufrufer in einer Reihe wären höher als jedes Fenster und zwängen die
    // Ansicht so weit heraus, dass kein Name mehr lesbar ist.
    const many = Array.from({ length: 30 }, (_, index) => node(`a${index}`, -1, 1 - index / 30));
    const positions = computeColumnLayout([node("fokus", 0), ...many], { maxPerColumn: 12 });

    const xs = new Set(many.map((entry) => positions.get(entry.id)!.x));
    expect(xs.size).toBeGreaterThan(1);

    const höchsteReihe = Math.max(
      ...[...xs].map((x) => many.filter((entry) => positions.get(entry.id)!.x === x).length),
    );
    expect(höchsteReihe).toBeLessThanOrEqual(12);
  });

  it("lässt Unterspalten vom Fokus weg wandern, nie über ihn hinweg", () => {
    const many = Array.from({ length: 30 }, (_, index) => node(`a${index}`, -1));
    const positions = computeColumnLayout([node("fokus", 0), ...many], { maxPerColumn: 12 });
    const fokusX = positions.get("fokus")!.x;

    // Alle Aufrufer bleiben links vom Fokus — sonst wäre die Aussage der Karte
    // verloren, sobald es viele werden.
    for (const entry of many) {
      expect(positions.get(entry.id)!.x).toBeLessThan(fokusX);
    }
  });
});
