import { describe, expect, it } from "vitest";

import {
  absoluteUrl,
  applyTabIndent,
  buildDividerLine,
  buildMarkdownTable,
  clampSplitRatio,
  continueMarkdownLine,
  dividerSnippetForTextarea,
  isDividerBlock,
  normalizeAssetUrl,
  parseListLines,
  parseMarkdownCitationRefs,
  parseToggleBlock,
  previewElementToMarkdown,
  segmentBlockLines,
  setToggleOpen,
  splitMarkdownBlocks,
  toggleWrap,
  withPreservedCitationLinks
} from "./NotesPage";

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

describe("applyTabIndent", () => {
  it("inserts a tab at the caret when nothing is selected", () => {
    const result = applyTabIndent("abcdef", 3, 3, false);
    expect(result?.next).toBe("abc\tdef");
    expect(result).toMatchObject({ selStart: 4, selEnd: 4 });
  });

  it("outdents a tab-indented line at the caret", () => {
    const result = applyTabIndent("\tabc", 2, 2, true);
    expect(result?.next).toBe("abc");
    expect(result).toMatchObject({ selStart: 1, selEnd: 1 });
  });

  it("returns null outdenting a line with no leading whitespace", () => {
    expect(applyTabIndent("abc", 1, 1, true)).toBeNull();
  });

  it("indents every line of a multi-line selection", () => {
    const value = "one\ntwo\nthree";
    const result = applyTabIndent(value, 0, value.length, false);
    expect(result?.next).toBe("\tone\n\ttwo\n\tthree");
  });

  it("outdents every line of a multi-line selection", () => {
    const value = "\tone\n\ttwo\n\tthree";
    const result = applyTabIndent(value, 0, value.length, true);
    expect(result?.next).toBe("one\ntwo\nthree");
  });

  it("indents the whole line (moves the marker) when the caret is on a list line", () => {
    const result = applyTabIndent("- Punkt A", 3, 3, false);
    expect(result?.next).toBe("\t- Punkt A");
    expect(result).toMatchObject({ selStart: 4, selEnd: 4 });
  });

  it("indents a freshly-continued empty bullet line the same way", () => {
    const value = "- Punkt A\n- ";
    const result = applyTabIndent(value, value.length, value.length, false);
    expect(result?.next).toBe("- Punkt A\n\t- ");
  });

  it("still inserts at the caret on a non-list line", () => {
    const result = applyTabIndent("Punkt A", 3, 3, false);
    expect(result?.next).toBe("Pun\tkt A");
  });
});

describe("isDividerBlock", () => {
  it("recognizes a solid underscore-fill divider", () => {
    expect(isDividerBlock("_".repeat(20))).toBe("solid");
  });

  it("still recognizes the legacy plain-dash divider", () => {
    expect(isDividerBlock("---")).toBe("solid");
    expect(isDividerBlock("-----")).toBe("solid");
  });

  it("recognizes a spaced-dash divider", () => {
    expect(isDividerBlock("- - - - - - - - - -")).toBe("dashed");
    expect(isDividerBlock("- -")).toBe("dashed");
  });

  it("does not misclassify a list item or other blocks", () => {
    expect(isDividerBlock("- item")).toBeNull();
    expect(isDividerBlock("# Heading")).toBeNull();
    expect(isDividerBlock("--")).toBeNull();
    expect(isDividerBlock("-")).toBeNull();
  });
});

describe("buildDividerLine", () => {
  it("builds a run of underscores for solid", () => {
    expect(buildDividerLine("solid", 15)).toBe("_".repeat(15));
  });

  it("builds space-separated dashes for dashed", () => {
    expect(buildDividerLine("dashed", 12)).toBe(Array.from({ length: 12 }, () => "-").join(" "));
  });

  it("clamps to a minimum length so a divider never collapses to nothing", () => {
    expect(buildDividerLine("solid", 2).length).toBeGreaterThanOrEqual(10);
  });

  it("round-trips through isDividerBlock", () => {
    expect(isDividerBlock(buildDividerLine("solid", 20))).toBe("solid");
    expect(isDividerBlock(buildDividerLine("dashed", 10))).toBe("dashed");
  });
});

describe("dividerSnippetForTextarea", () => {
  it("falls back to a fixed-width line when no textarea is mounted yet", () => {
    const solid = dividerSnippetForTextarea("solid", null);
    const dashed = dividerSnippetForTextarea("dashed", null);
    expect(solid.trim()).toMatch(/^_+$/);
    expect(isDividerBlock(solid.trim())).toBe("solid");
    expect(isDividerBlock(dashed.trim())).toBe("dashed");
    expect(solid.startsWith("\n\n")).toBe(true);
    expect(solid.endsWith("\n\n")).toBe(true);
  });
});

describe("clampSplitRatio", () => {
  it("passes through a ratio inside the allowed range", () => {
    expect(clampSplitRatio(0.4)).toBe(0.4);
  });

  it("clamps ratios outside [0.2, 0.8]", () => {
    expect(clampSplitRatio(0.05)).toBe(0.2);
    expect(clampSplitRatio(0.95)).toBe(0.8);
  });

  it("falls back to 0.5 for a non-finite ratio", () => {
    expect(clampSplitRatio(NaN)).toBe(0.5);
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

describe("toggleWrap", () => {
  it("wraps a plain selection", () => {
    const result = toggleWrap("hello world", 0, 5, "**");
    expect(result).toEqual({ next: "**hello** world", selStart: 2, selEnd: 7 });
  });

  it("unwraps when the selection includes the markers", () => {
    const result = toggleWrap("**hello** world", 0, 9, "**");
    expect(result).toEqual({ next: "hello world", selStart: 0, selEnd: 5 });
  });

  it("unwraps when the markers sit just outside the selection", () => {
    const result = toggleWrap("**hello** world", 2, 7, "**");
    expect(result).toEqual({ next: "hello world", selStart: 0, selEnd: 5 });
  });

  it("does not unwrap a plain selection twice in a row without the markers", () => {
    const first = toggleWrap("hello", 0, 5, "**");
    const second = toggleWrap(first.next, first.selStart, first.selEnd, "**");
    expect(second).toEqual({ next: "hello", selStart: 0, selEnd: 5 });
  });

  it("supports asymmetric before/after markers for links", () => {
    const result = toggleWrap("hello", 0, 5, "[", "](https://)");
    expect(result).toEqual({ next: "[hello](https://)", selStart: 1, selEnd: 6 });
  });
});

describe("continueMarkdownLine", () => {
  it("continues a bullet list line and keeps its indentation", () => {
    const value = "\t- item";
    const result = continueMarkdownLine(value, value.length, value.length);
    expect(result?.next).toBe("\t- item\n\t- ");
    expect(result).toMatchObject({ selStart: 11, selEnd: 11 });
  });

  it("removes the prefix on an empty list line instead of continuing it", () => {
    const value = "\t- ";
    const result = continueMarkdownLine(value, value.length, value.length);
    expect(result?.next).toBe("");
    expect(result).toMatchObject({ selStart: 0, selEnd: 0 });
  });

  it("increments a numbered list item", () => {
    const value = "1. first";
    const result = continueMarkdownLine(value, value.length, value.length);
    expect(result?.next).toBe("1. first\n2. ");
  });

  it("returns null on a plain non-list line", () => {
    const value = "plain text";
    expect(continueMarkdownLine(value, value.length, value.length)).toBeNull();
  });
});

describe("parseListLines", () => {
  it("builds a flat list from unindented bullet lines", () => {
    const nodes = parseListLines(["- a", "- b", "- c"]);
    expect(nodes.map((node) => node.content)).toEqual(["a", "b", "c"]);
    expect(nodes.every((node) => !node.ordered && node.children.length === 0)).toBe(true);
  });

  it("nests indented bullet lines under their parent", () => {
    const nodes = parseListLines(["- parent", "\t- child"]);
    expect(nodes).toHaveLength(1);
    expect(nodes[0].content).toBe("parent");
    expect(nodes[0].children).toMatchObject([{ content: "child", ordered: false }]);
  });

  it("treats numbered markers as ordered with parsed numbers", () => {
    const nodes = parseListLines(["1. one", "2. two"]);
    expect(nodes).toMatchObject([
      { ordered: true, number: 1, content: "one" },
      { ordered: true, number: 2, content: "two" }
    ]);
  });

  it("returns to a shallower level after a deeper nested run", () => {
    const nodes = parseListLines(["- a", "\t- a1", "\t\t- a1x", "- b"]);
    expect(nodes).toHaveLength(2);
    expect(nodes[0].children).toHaveLength(1);
    expect(nodes[0].children[0].children).toMatchObject([{ content: "a1x" }]);
    expect(nodes[1].children).toHaveLength(0);
  });
});

describe("previewElementToMarkdown headings and lists", () => {
  it("round-trips an H3 heading", () => {
    const root = document.createElement("div");
    root.innerHTML = "<h3>Title</h3>";
    expect(previewElementToMarkdown("### Title", root)).toBe("### Title");
  });

  it("round-trips an ordered list with renumbered items", () => {
    const root = document.createElement("div");
    root.innerHTML = "<ol><li>one</li><li>two</li></ol>";
    expect(previewElementToMarkdown("1. one\n2. two", root)).toBe("1. one\n2. two");
  });

  it("round-trips a nested unordered list with indentation", () => {
    const root = document.createElement("div");
    root.innerHTML = "<ul><li>parent<ul><li>child</li></ul></li></ul>";
    expect(previewElementToMarkdown("- parent\n\t- child", root)).toBe("- parent\n\t- child");
  });
});

describe("segmentBlockLines (text above list/divider is never dropped)", () => {
  it("keeps text preceding a bullet list in the same block", () => {
    const segments = segmentBlockLines(["Normaler Text", "- eins", "- zwei"]);
    expect(segments.map((s) => s.type)).toEqual(["text", "list"]);
    expect((segments[0] as { lines: string[] }).lines).toEqual(["Normaler Text"]);
    expect((segments[1] as { lines: string[] }).lines).toEqual(["- eins", "- zwei"]);
  });

  it("splits a dashed divider out of a mixed text/list block", () => {
    const segments = segmentBlockLines(["Text darüber", "- - - -", "- Punkt"]);
    expect(segments.map((s) => s.type)).toEqual(["text", "divider", "list"]);
    expect(segments[1]).toMatchObject({ type: "divider", kind: "dashed" });
  });

  it("treats a pure paragraph as a single text run", () => {
    const segments = segmentBlockLines(["nur", "text"]);
    expect(segments).toHaveLength(1);
    expect(segments[0].type).toBe("text");
  });
});

describe("collapsible toggle blocks", () => {
  it("splitMarkdownBlocks keeps a toggle fence (incl. blank lines) as one block", () => {
    const md = "Intro\n\n:::toggle+ Titel\n\nAbsatz eins\n\nAbsatz zwei\n:::\n\nOutro";
    const blocks = splitMarkdownBlocks(md);
    const toggle = blocks.find((b) => b.raw.startsWith(":::toggle"));
    expect(toggle).toBeTruthy();
    expect(toggle?.raw).toContain("Absatz eins");
    expect(toggle?.raw).toContain("Absatz zwei");
    expect(toggle?.raw.trimEnd().endsWith(":::")).toBe(true);
    // offsets must round-trip: slicing the source by the block's range returns its raw text.
    expect(md.slice(toggle!.start, toggle!.end)).toBe(toggle!.raw);
  });

  it("parseToggleBlock reads open state, title and body", () => {
    const parsed = parseToggleBlock(":::toggle- Mein Titel\nZeile A\nZeile B\n:::");
    expect(parsed).toEqual({ open: false, title: "Mein Titel", body: "Zeile A\nZeile B" });
  });

  it("setToggleOpen flips the marker without touching the rest", () => {
    expect(setToggleOpen(":::toggle+ T\nx\n:::", false)).toBe(":::toggle- T\nx\n:::");
    expect(setToggleOpen(":::toggle- T\nx\n:::", true)).toBe(":::toggle+ T\nx\n:::");
  });
});

describe("asset URL resolution (images survive a restart)", () => {
  it("strips a stale localhost origin+port from an asset URL", () => {
    expect(normalizeAssetUrl("http://127.0.0.1:53821/notes/assets/abc123")).toBe("/notes/assets/abc123");
    expect(normalizeAssetUrl("http://localhost:61140/notes/assets/xyz")).toBe("/notes/assets/xyz");
  });

  it("leaves relative asset paths and external/data URLs untouched", () => {
    expect(normalizeAssetUrl("/notes/assets/abc")).toBe("/notes/assets/abc");
    expect(normalizeAssetUrl("https://example.com/pic.png")).toBe("https://example.com/pic.png");
    expect(normalizeAssetUrl("data:image/png;base64,AAAA")).toBe("data:image/png;base64,AAAA");
  });

  it("absoluteUrl passes data/blob/http through unchanged", () => {
    expect(absoluteUrl("data:image/png;base64,AAAA")).toBe("data:image/png;base64,AAAA");
    expect(absoluteUrl("https://example.com/x.png")).toBe("https://example.com/x.png");
  });
});
