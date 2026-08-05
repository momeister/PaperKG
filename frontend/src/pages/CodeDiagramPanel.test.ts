import { describe, expect, it } from "vitest";

import { classDiagramSource, sequenceDiagramSource } from "./CodeDiagramPanel";
import type { CodeDiagram } from "../types";

/**
 * Ein Diagramm sieht autoritativer aus als jede Textzeile. Wenn eine über
 * Namensgleichheit geratene Kante darin genauso gezeichnet wird wie eine
 * belegte, wäscht das Bild die Vermutung zur Tatsache — der eine Fehler, den
 * dieses Werkzeug nicht machen darf.
 */
const empty: CodeDiagram = { classes: [], inherits: [], sequence: [] };

describe("classDiagramSource", () => {
  it("zeichnet eine geratene Vererbung gestrichelt", () => {
    const solid = classDiagramSource({
      ...empty,
      inherits: [
        {
          from: "Circle",
          to: "Shape",
          kind: "inherits",
          confidence: "verified",
          evidence_path: "src/shapes.py",
          evidence_line: 6,
        },
      ],
    });
    expect(solid).toContain("Shape <|-- Circle");

    const guessed = classDiagramSource({
      ...empty,
      inherits: [
        {
          from: "Circle",
          to: "Shape",
          kind: "inherits",
          confidence: "guessed",
          evidence_path: "src/shapes.py",
          evidence_line: 6,
        },
      ],
    });
    expect(guessed).toContain("Shape <|.. Circle");
    expect(guessed).not.toContain("<|--");
  });

  it("macht aus Namen mit Punkten gültige mermaid-Bezeichner", () => {
    const source = classDiagramSource({
      ...empty,
      classes: [
        {
          id: "abc",
          name: "query.llm_router.LLMRouter",
          path: "query/llm_router.py",
          line: 53,
          kind: "class",
          members: [{ name: "chat_with_tools", kind: "method", signature: null }],
        },
      ],
    });
    expect(source).toContain("class query_llm_router_LLMRouter {");
    expect(source).toContain("chat_with_tools()");
  });
});

describe("sequenceDiagramSource", () => {
  it("zeichnet einen geratenen Aufruf gestrichelt und nennt die Kandidaten", () => {
    const source = sequenceDiagramSource({
      ...empty,
      sequence: [
        {
          from: "checkout",
          to: "apply_discount",
          to_id: "ff",
          confidence: "guessed",
          candidates: 3,
          evidence_path: "src/pricing.py",
          evidence_line: 6,
        },
      ],
    });
    expect(source).toContain("checkout-->>apply_discount");
    expect(source).toContain("(3 Kandidaten)");
  });

  it("nennt jeden Teilnehmer genau einmal", () => {
    const step = {
      to_id: "ff",
      confidence: "verified" as const,
      candidates: 1,
      evidence_path: "a.py",
      evidence_line: 1,
    };
    const source = sequenceDiagramSource({
      ...empty,
      sequence: [
        { ...step, from: "a", to: "b" },
        { ...step, from: "b", to: "c" },
      ],
    });
    const participants = source.split("\n").filter((line) => line.includes("participant"));
    expect(participants).toHaveLength(3);
  });
});
