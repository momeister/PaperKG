import { describe, expect, it } from "vitest";

import { factSummary, looksLikeHelper, sideEffectLabel, strongerNeighbours } from "./summary";
import type { CodeFacts, CodeNeighbour, CodeNodeDetail } from "../../types";

/**
 * Der Steckbrief ist die Antwort auf „was macht das", wenn im Code kein
 * Kommentar steht. Er darf deshalb nie mehr behaupten, als der Index hergibt —
 * und er darf nie ganz leer bleiben, sonst ist man wieder da, wo man war.
 */
function facts(extra: Partial<CodeFacts> = {}): CodeFacts {
  return {
    signature: "def f(a, b)",
    params: [
      { name: "a", type_name: "int", default: null },
      { name: "b", type_name: null, default: "3" },
    ],
    returns: "bool",
    throws: [],
    side_effects: [],
    complexity: 3,
    loc: 20,
    max_nesting: 2,
    callers: 2,
    callees: 1,
    ...extra,
  };
}

function detail(extra: Partial<CodeNodeDetail> = {}, factExtra: Partial<CodeFacts> = {}): CodeNodeDetail {
  return {
    id: "abc123",
    name: "f",
    qualified: "mod.f",
    kind: "function",
    lang: "python",
    path: "src/x.py",
    span: { start_byte: 0, end_byte: 10, start_line: 1, end_line: 20 },
    parent: null,
    doc: null,
    facts: facts(factExtra),
    metrics: {
      pagerank: 0.01, fan_in: 2, fan_out: 1, reach_depth: 2, churn: 3, risk: 0,
      authors: 1, last_touched: null, coverage: null, hits: null, relevance: 0.3,
    },
    ...extra,
  };
}

function neighbour(id: string, relevance: number): CodeNeighbour {
  return {
    node: { id, name: id, qualified: `mod.${id}`, kind: "function", lang: "python", path: "src/x.py", line: 1, relevance },
    kind: "calls",
    confidence: "verified",
    evidence_path: "src/x.py",
    evidence_line: 4,
    candidates: 1,
    occurrences: 1,
  };
}

describe("factSummary", () => {
  it("sagt immer etwas — auch ohne Docstring im Code", () => {
    expect(factSummary(detail()).length).toBeGreaterThan(0);
  });

  it("nennt Parameterzahl und Rückgabe", () => {
    const text = factSummary(detail()).join(" ");
    expect(text).toContain("2 Parametern");
    expect(text).toContain("bool");
  });

  it("nennt Nebenwirkungen im Klartext", () => {
    const text = factSummary(detail({}, { side_effects: ["database", "network"] })).join(" ");
    expect(text).toContain("Datenbank");
    expect(text).toContain("Netz");
  });

  it("nennt `pure: null` nicht nebenwirkungsfrei", () => {
    // Option<bool> heisst hier „konnte nicht entschieden werden", nicht „nein".
    const text = factSummary(detail({}, { pure: null })).join(" ");
    expect(text).not.toContain("Ohne Nebenwirkungen");
  });

  it("schränkt Aussagen auf das ein, was der Index hergibt", () => {
    const text = factSummary(detail()).join(" ");
    expect(text).toContain("Laut Index");
  });

  it("kommt ohne facts aus", () => {
    expect(factSummary(detail({ facts: null })).length).toBeGreaterThan(0);
  });
});

describe("strongerNeighbours", () => {
  it("findet Nachbarn, die relevanter sind als das Betrachtete", () => {
    const found = strongerNeighbours(detail(), [
      { title: "Aufgerufen", items: [neighbour("wichtig", 0.8), neighbour("egal", 0.2)] },
    ]);
    expect(found.map((entry) => entry.item.node.id)).toEqual(["wichtig"]);
  });

  it("meldet nichts, wenn nichts deutlich stärker ist", () => {
    expect(strongerNeighbours(detail(), [{ title: "x", items: [neighbour("knapp", 0.35)] }])).toEqual([]);
  });
});

describe("looksLikeHelper", () => {
  it("erkennt kurze, einfache Funktionen", () => {
    expect(looksLikeHelper(detail({}, { loc: 4, complexity: 1 }))).toBe(true);
    expect(looksLikeHelper(detail({}, { loc: 90, complexity: 12 }))).toBe(false);
  });
});

describe("sideEffectLabel", () => {
  it("lässt unbekannte Kennungen stehen, statt sie zu verschlucken", () => {
    expect(sideEffectLabel("etwas_neues")).toBe("etwas_neues");
  });
});
