/**
 * Die Karte über *mehrere* Wurzeln — der Graph zu einer Trefferliste.
 *
 * Geprüft wird hier nur, was dieser Hook selbst entscheidet: dass er nie
 * `direction=both` fragt (auf einer Pfeilkarte wäre die Seite eines Knotens
 * sonst nicht mehr feststellbar), dass die Wurzeln in Spalte 0 landen, dass
 * eine Budgetsprengung die Karte *ganz* verwirft statt sie halb zu füllen, und
 * dass eine leere Trefferliste keine Anfrage auslöst.
 */
import { renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import type { CodeGraphSlice, CodeSymbolHit } from "../../types";
import { DEFAULT_EDGE_KINDS, MAX_MAP_NODES } from "./codeMap";
import { useMultiFocusMap } from "./useCodeMap";

const slice = vi.fn();
vi.mock("../../api", () => ({
  api: { codegraph: { slice: (...args: unknown[]) => slice(...args) } },
}));

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

function answer(nodes: CodeSymbolHit[]): CodeGraphSlice {
  return { nodes, edges: [], truncated: false };
}

// Geschweifte Klammern mit Absicht: `mockReset()` gibt den Mock zurück, und
// vitest hielte einen zurückgegebenen Wert für eine Aufräumfunktion — sie
// riefe den Mock am Testende ein weiteres Mal auf, mit leeren Argumenten.
beforeEach(() => {
  slice.mockReset();
});

describe("useMultiFocusMap", () => {
  it("fragt je Wurzel getrennt ein und aus, nie beides zusammen", async () => {
    slice.mockImplementation((_project: string, id: string) =>
      Promise.resolve(answer([hit(id)])),
    );

    const { result } = renderHook(() =>
      useMultiFocusMap("p", ["aa", "bb"], DEFAULT_EDGE_KINDS, true),
    );

    await waitFor(() => expect(result.current.map.nodes.size).toBe(2));
    expect(slice).toHaveBeenCalledTimes(4);
    const directions = slice.mock.calls.map((call) => call[2].direction);
    expect(directions.filter((d) => d === "out")).toHaveLength(2);
    expect(directions.filter((d) => d === "in")).toHaveLength(2);
    expect(directions).not.toContain("both");
  });

  it("stellt alle Wurzeln in Spalte 0", async () => {
    // Sonst stünde eine Wurzel rechts, nur weil sie zufällig Nachbarin einer
    // anderen ist — und die Karte behauptete eine Aufrufrichtung, die es
    // zwischen zwei Treffern derselben Antwort nicht gibt.
    slice.mockImplementation((_project: string, id: string, options: { direction: string }) =>
      Promise.resolve(
        answer(options.direction === "out" ? [hit(id), hit("bb"), hit("nachbar")] : [hit(id)]),
      ),
    );

    const { result } = renderHook(() =>
      useMultiFocusMap("p", ["aa", "bb"], DEFAULT_EDGE_KINDS, true),
    );

    await waitFor(() => expect(result.current.map.nodes.size).toBeGreaterThan(2));
    expect(result.current.map.nodes.get("aa")?.column).toBe(0);
    expect(result.current.map.nodes.get("bb")?.column).toBe(0);
    expect(result.current.map.nodes.get("nachbar")?.column).toBe(1);
  });

  it("verwirft eine Budgetsprengung ganz", async () => {
    // Halb eingearbeitet sähe der Ausschnitt aus wie ein vollständiger Graph.
    const many = Array.from({ length: MAX_MAP_NODES + 5 }, (_, index) => hit(`n${index}`));
    slice.mockImplementation((_project: string, id: string, options: { direction: string }) =>
      Promise.resolve(answer(options.direction === "out" ? many : [hit(id)])),
    );

    const { result } = renderHook(() => useMultiFocusMap("p", ["aa"], DEFAULT_EDGE_KINDS, true));

    await waitFor(() => expect(result.current.map.atBudget).toBe(true));
    expect(result.current.map.nodes.size).toBeLessThanOrEqual(MAX_MAP_NODES);
  });

  it("fragt ohne Treffer gar nicht erst", async () => {
    const { result } = renderHook(() => useMultiFocusMap("p", [], DEFAULT_EDGE_KINDS, true));
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(slice).not.toHaveBeenCalled();
    expect(result.current.map.nodes.size).toBe(0);
  });

  it("fragt nicht, solange die Ansicht nicht offen ist", async () => {
    const { result } = renderHook(() =>
      useMultiFocusMap("p", ["aa"], DEFAULT_EDGE_KINDS, false),
    );
    await waitFor(() => expect(result.current.isLoading).toBe(false));
    expect(slice).not.toHaveBeenCalled();
  });

  it("meldet einen Fehler statt einer halben Karte", async () => {
    slice.mockRejectedValue(new Error("Index weg"));
    const { result } = renderHook(() => useMultiFocusMap("p", ["aa"], DEFAULT_EDGE_KINDS, true));
    await waitFor(() => expect(result.current.error).toBe("Index weg"));
    expect(result.current.map.nodes.size).toBe(0);
  });
});
