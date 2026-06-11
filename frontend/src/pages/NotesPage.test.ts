import { describe, expect, it } from "vitest";

import { buildMarkdownTable, withPreservedCitationLinks } from "./NotesPage";

describe("withPreservedCitationLinks", () => {
  const original =
    "> Die Erde ist rund.\n\nQuelle: [Z1 - Earth Study](sciencekg://citation/cite_abc) (arxiv:1)";

  it("re-attaches citation links the AI rewrite dropped", () => {
    const result = withPreservedCitationLinks(original, "Die Erde ist kugelförmig.");
    expect(result).toContain("Die Erde ist kugelförmig.");
    expect(result).toContain("[Z1 - Earth Study](sciencekg://citation/cite_abc)");
  });

  it("leaves replacements alone when the link survived", () => {
    const replacement = "Kürzer gesagt rund [Z1 - Earth Study](sciencekg://citation/cite_abc).";
    expect(withPreservedCitationLinks(original, replacement)).toBe(replacement);
  });

  it("ignores preview placeholders and duplicate ids", () => {
    const withPreview = "[x](sciencekg://citation/preview) [Z1 - A](sciencekg://citation/c1) [Z1 - A](sciencekg://citation/c1)";
    const result = withPreservedCitationLinks(withPreview, "neuer Text");
    expect(result.match(/sciencekg:\/\/citation\/c1/g)).toHaveLength(1);
    expect(result).not.toContain("citation/preview");
  });
});

describe("buildMarkdownTable", () => {
  it("builds a header, separator, and the requested body rows", () => {
    const table = buildMarkdownTable(3, 2).trim().split("\n");
    expect(table[0]).toBe("| Spalte 1 | Spalte 2 | Spalte 3 |");
    expect(table[1]).toBe("| --- | --- | --- |");
    expect(table).toHaveLength(4);
  });

  it("clamps to at least one column and row", () => {
    const table = buildMarkdownTable(0, 0).trim().split("\n");
    expect(table[0]).toBe("| Spalte 1 |");
    expect(table).toHaveLength(3);
  });
});
