import { describe, expect, it } from "vitest";

import { buildMarkdownTable, parseMarkdownCitationRefs, previewElementToMarkdown, withPreservedCitationLinks } from "./NotesPage";

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

describe("parseMarkdownCitationRefs group schemes", () => {
  it("resolves the short skg://c/ scheme to full cite_ ids", () => {
    const refs = parseMarkdownCitationRefs("> Text [2 Quellen](skg://c/aaa,bbb)");
    expect(refs.map((ref) => ref.id)).toEqual(["cite_aaa", "cite_bbb"]);
    expect(refs.every((ref) => ref.groupIds?.join(",") === "cite_aaa,cite_bbb")).toBe(true);
  });

  it("still parses the legacy sciencekg://citations/ scheme identically", () => {
    const legacy = parseMarkdownCitationRefs("> Text [2 Quellen](sciencekg://citations/cite_aaa,cite_bbb)");
    expect(legacy.map((ref) => ref.id)).toEqual(["cite_aaa", "cite_bbb"]);
  });

  it("keeps the single-citation scheme working", () => {
    const refs = parseMarkdownCitationRefs("Quelle: [Z1 - A](sciencekg://citation/cite_abc)");
    expect(refs.map((ref) => ref.id)).toEqual(["cite_abc"]);
  });
});

describe("previewElementToMarkdown citation-group round-trip", () => {
  const raw = "> Zitat [2 Quellen](skg://c/aaa,bbb)";

  function groupChip(withOpenPopover = false) {
    return `<span class="citation-group" contenteditable="false" data-citation-group-ids="aaa,bbb" data-citation-label="2 Quellen">` +
      `<button type="button" class="citation-group-button"><span>2 Quellen</span></button>` +
      (withOpenPopover
        ? `<span class="citation-group-popover" role="menu"><button type="button" class="citation-group-item">Z1 Paper A</button><button type="button" class="citation-group-item">Z2 Paper B</button></span>`
        : "") +
      `</span>`;
  }

  it("reconstructs the skg://c marker instead of the visible label", () => {
    const root = document.createElement("div");
    root.innerHTML = `<blockquote>Zitat ${groupChip()}</blockquote>`;
    expect(previewElementToMarkdown(raw, root)).toBe(raw);
  });

  it("ignores an open popover during serialization", () => {
    const root = document.createElement("div");
    root.innerHTML = `<blockquote>Zitat ${groupChip(true)}</blockquote>`;
    expect(previewElementToMarkdown(raw, root)).toBe(raw);
  });

  it("round-trips through parseMarkdownCitationRefs", () => {
    const root = document.createElement("div");
    root.innerHTML = `<blockquote>Zitat ${groupChip()}</blockquote>`;
    const refs = parseMarkdownCitationRefs(previewElementToMarkdown(raw, root));
    expect(refs.map((ref) => ref.id)).toEqual(["cite_aaa", "cite_bbb"]);
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
