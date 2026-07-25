import { describe, expect, it } from "vitest";

import { firstLlmError, isProviderLimit, llmErrorHeadline, parseLlmError } from "./llmErrors";

describe("parseLlmError", () => {
  it("trennt das Backend-Präfix von der Meldung", () => {
    expect(parseLlmError("[llm:quota] Kontingent aufgebraucht.")).toEqual({
      kind: "quota",
      message: "Kontingent aufgebraucht."
    });
  });

  it("lässt untagged Meldungen unverändert", () => {
    // Alte Zeilen in der DB haben kein Präfix und dürfen nicht verstümmelt werden.
    expect(parseLlmError("Missing PDF path for p1")).toEqual({ kind: null, message: "Missing PDF path for p1" });
    expect(parseLlmError(null)).toEqual({ kind: null, message: "" });
    expect(parseLlmError(undefined)).toEqual({ kind: null, message: "" });
  });

  it("fängt unbekannte Arten als 'unknown' ab", () => {
    expect(parseLlmError("[llm:irgendwas] Text").kind).toBe("unknown");
  });

  it("bricht bei fehlender schließender Klammer nicht", () => {
    expect(parseLlmError("[llm:quota ohne Ende")).toEqual({ kind: null, message: "[llm:quota ohne Ende" });
  });
});

describe("isProviderLimit", () => {
  it("erkennt genau die Fälle, in denen das Backend den Batch stoppt", () => {
    expect(isProviderLimit("quota")).toBe(true);
    expect(isProviderLimit("rate_limit")).toBe(true);
    expect(isProviderLimit("auth")).toBe(true);
    expect(isProviderLimit("connection")).toBe(false);
    expect(isProviderLimit(null)).toBe(false);
  });
});

describe("llmErrorHeadline", () => {
  it("gibt deutsche Überschriften", () => {
    expect(llmErrorHeadline("quota")).toBe("KI-Kontingent erschöpft");
    expect(llmErrorHeadline("rate_limit")).toBe("Rate-Limit des KI-Anbieters erreicht");
  });
});

describe("firstLlmError", () => {
  it("findet den ersten eingeordneten Fehler und ignoriert rohen Text", () => {
    const found = firstLlmError([null, "Missing PDF path", "[llm:rate_limit] HTTP 429", "[llm:quota] später"]);
    expect(found?.kind).toBe("rate_limit");
  });

  it("liefert null, wenn nichts eingeordnet ist", () => {
    expect(firstLlmError([null, "kaputtes PDF", undefined])).toBeNull();
  });
});
