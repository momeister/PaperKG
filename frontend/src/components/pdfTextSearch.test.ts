import { describe, expect, it } from "vitest";
import { findTextOccurrences } from "./pdfTextSearch";

describe("PDF occurrence search", () => {
  it("finds short words and separate occurrences on one page", () => {
    const hits = findTextOccurrences([{ str: "AI, AI and AI." }], "AI");
    expect(hits.map(h => h.start.offset)).toEqual([0, 4, 11]);
    expect(findTextOccurrences([{ str: "only another term" }], "AI term")).toEqual([]);
  });
  it("maps expanded ligatures and collapsed spaces to original characters", () => {
    const hits = findTextOccurrences([{ str: "  ﬁnd   ﬂow" }], "find flow");
    expect(hits[0].start.offset).toBe(2);
    expect(hits[0].end.offset).toBe(11);
    expect(hits[0].text).toBe("ﬁnd   ﬂow");
  });
  it("joins hyphenated lines and contiguous word fragments", () => {
    expect(findTextOccurrences([{ str: "inter-", hasEOL: true }, { str: "vention" }], "intervention")).toHaveLength(1);
    const fragments = [{ str: "work", transform: [1, 0, 0, 1, 10, 10], width: 20 }, { str: "flow", transform: [1, 0, 0, 1, 30, 10], width: 20 }];
    expect(findTextOccurrences(fragments, "workflow")[0].end).toEqual({ item: 1, offset: 4 });
    expect(findTextOccurrences([{ str: "a\t b" }], "a b")[0].end.offset).toBe(4);
  });
});
