import { describe, expect, it } from "vitest";

import { isGarbledExcerpt } from "./excerptSanity";

describe("isGarbledExcerpt", () => {
  it("flags the verbatim reversed PDF-column leak", () => {
    const garbled =
      "S ecnerefeR DP DS RP RC )%( )%( )%( )%( AN 11 AN 7 3 36 72 04 yG 6*6 " +
      "( TR citcatoerets detanoitcarfopyH ii esahP orumO iC %59 yG 4*6";
    expect(isGarbledExcerpt(garbled)).toBe(true);
  });

  it("passes normal scientific prose", () => {
    const prose =
      "Hypofractionated stereotactic radiotherapy was administered in 3 phases. " +
      "Patients received a total dose of 36 Gy in 6 fractions.";
    expect(isGarbledExcerpt(prose)).toBe(false);
  });

  it("passes a terse abstract with abbreviations and units", () => {
    const text =
      "Bevacizumab combined with radiotherapy improved OS in glioblastoma. " +
      "Median survival increased from 16 to 21 months (p < 0.05). " +
      "Adverse events included hypertension and fatigue.";
    expect(isGarbledExcerpt(text)).toBe(false);
  });

  it("does not flag very short text", () => {
    expect(isGarbledExcerpt("Gy NA")).toBe(false);
    expect(isGarbledExcerpt("")).toBe(false);
  });
});