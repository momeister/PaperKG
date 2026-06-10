import { describe, expect, it } from "vitest";

import {
  bestMatchFor,
  findPageMatch,
  highlightQuerySignature,
  highlightScrollTop,
  normalizeHighlightBoxes,
  topmostHighlightTop,
  type PageMatch
} from "./PdfPane";

const viewport = { transform: [1, 0, 0, 1, 0, 0], scale: 1 };

function textItem(str: string, x = 10, y = 100, width = Math.max(40, str.length * 6)) {
  return {
    str,
    transform: [1, 0, 0, 1, x, y],
    width,
    height: 10
  };
}

describe("PdfPane matching", () => {
  it("marks only the overlapping part of an exact phrase text item", () => {
    const match = findPageMatch(
      [
        textItem("These results required a clinical workflow-aligned AI Consult implementation and active deployment to encourage uptake.", 10, 100, 620),
        textItem("Deployment appears again far away from the exact quote.", 10, 130, 360)
      ],
      {
        phrases: ["clinical workflow-aligned AI Consult implementation"],
        terms: ["implementation", "deployment"]
      },
      viewport,
      0,
      0
    );

    expect(match?.exact).toBe(true);
    expect(match?.matchedText).toBe("clinical workflow-aligned AI Consult implementation");
    expect(match?.boxes).toHaveLength(1);
    expect(match?.boxes[0].left).toBeGreaterThan(80);
    expect(match?.boxes[0].width).toBeLessThan(360);
    expect(match?.boxes[0].top).toBeLessThan(120);
  });

  it("marks every matching sentence chunk of a long excerpt, not only the best one", () => {
    // A multi-sentence excerpt rarely matches the page text as one phrase; its sentence
    // chunks do. The union of all chunk matches must be highlighted — previously only
    // the single best chunk was marked, so part of the passage stayed unmarked.
    const sentenceA = "The median overall survival was 16.8 months in the treatment group.";
    const sentenceB = "Patients in the treatment group reported more fatigue and headaches than placebo.";
    const match = findPageMatch(
      [
        textItem(sentenceA, 10, 100, 480),
        textItem("Unrelated filler text between the two passages keeps them apart on the page.", 10, 130, 480),
        textItem(sentenceB, 10, 160, 540)
      ],
      {
        phrases: [`${sentenceA} ${sentenceB}`, sentenceA, sentenceB],
        terms: []
      },
      viewport,
      0,
      0
    );

    expect(match?.exact).toBe(true);
    expect(match?.matchedText).toContain("16.8 months");
    expect(match?.matchedText).toContain("fatigue and headaches");
    const rowTops = new Set(match?.boxes.map((box) => Math.round(box.top / 10)));
    expect(rowTops.size).toBeGreaterThanOrEqual(2);
  });

  it("does not treat one short chunk of a long excerpt as a confident match", () => {
    // "exact" gates early scrolling and cross-page highlights: a stray short chunk on
    // another page must not pass as the passage itself.
    const longExcerpt =
      "The trial enrolled four hundred patients across twelve centers and randomized them " +
      "to treatment or placebo for eighteen months under blinded conditions, with more " +
      "fatigue and headaches reported in the treatment group during routine follow-up.";
    const match = findPageMatch(
      [textItem("Background mentions more fatigue and headaches in passing here.", 10, 100, 420)],
      {
        phrases: [longExcerpt, "more fatigue and headaches"],
        terms: []
      },
      viewport,
      0,
      0
    );

    expect(match).not.toBeNull();
    expect(match?.exact).toBe(false);
  });

  it("uses compact term windows and rejects terms spread across a broad page", () => {
    const compact = findPageMatch(
      [textItem("Alert fatigue requires classification thresholds that protect sensitivity in clinical deployment.", 10, 100, 560)],
      {
        phrases: [],
        terms: ["alert", "fatigue", "classification", "thresholds", "sensitivity", "deployment"]
      },
      viewport,
      1,
      1
    );
    const broad = findPageMatch(
      [
        textItem(
          `Alert ${"background ".repeat(35)} fatigue ${"background ".repeat(35)} classification ${"background ".repeat(35)} thresholds ${"background ".repeat(35)} sensitivity ${"background ".repeat(35)} deployment.`,
          10,
          100,
          2800
        )
      ],
      {
        phrases: [],
        terms: ["alert", "fatigue", "classification", "thresholds", "sensitivity", "deployment"]
      },
      viewport,
      1,
      1
    );

    expect(compact?.exact).toBe(false);
    expect(compact?.matchedText).toContain("alert fatigue");
    expect(compact?.boxes.length).toBeGreaterThan(0);
    expect(compact?.boxes[0].width).toBeGreaterThan(300);
    expect(broad).toBeNull();
  });

  it("normalizes overlapping highlight boxes into one non-darkening row segment", () => {
    const normalized = normalizeHighlightBoxes([
      { id: "a", evidenceIndex: 0, colorIndex: 0, left: 10, top: 20, width: 60, height: 12 },
      { id: "b", evidenceIndex: 0, colorIndex: 0, left: 40, top: 21, width: 80, height: 12 },
      { id: "c", evidenceIndex: 0, colorIndex: 0, left: 118, top: 20, width: 30, height: 12 }
    ]);

    expect(normalized).toHaveLength(1);
    expect(normalized[0]).toMatchObject({ left: 10, top: 20, width: 138 });
  });

  it("ignores stale page matches with old query signatures", () => {
    const currentSignature = highlightQuerySignature({ phrases: ["current evidence"], terms: ["current", "evidence"] });
    const staleSignature = highlightQuerySignature({ phrases: ["old evidence"], terms: ["old", "evidence"] });
    const matches: Record<number, PageMatch> = {
      1: {
        pageNumber: 1,
        score: 99999,
        exact: true,
        matchedText: "old evidence",
        boxes: [{ id: "old", evidenceIndex: 0, colorIndex: 0, left: 0, top: 0, width: 10, height: 10 }],
        querySignature: staleSignature
      },
      2: {
        pageNumber: 2,
        score: 1,
        exact: false,
        matchedText: "current evidence",
        boxes: [{ id: "new", evidenceIndex: 0, colorIndex: 0, left: 0, top: 0, width: 10, height: 10 }],
        querySignature: currentSignature
      }
    };

    expect(bestMatchFor(matches, currentSignature)?.pageNumber).toBe(2);
  });
});

describe("PdfPane highlight scroll targeting", () => {
  // Regression coverage for: clicking a citation jumped to the right PAGE but left
  // the highlighted passage itself off-screen on large/zoomed pages — the user had
  // to scroll manually to find it. The fix scrolls so the highlight's top edge lands
  // a fixed padding below the viewport top, not just the page centered.

  it("positions the highlight's top edge a fixed padding below the viewport top", () => {
    // Page starts 200px into the scroll container; highlight sits 900px down the
    // page (e.g. near the bottom of a tall, zoomed-in page) — far below the fold.
    expect(highlightScrollTop(200, 900, 64)).toBe(200 + 900 - 64);
  });

  it("uses the default padding when none is supplied", () => {
    expect(highlightScrollTop(0, 500)).toBe(500 - 64);
  });

  it("never scrolls past the top of the container", () => {
    // A highlight near the very top of a page that itself starts near (or above)
    // the container's current scroll position must clamp to 0, not a negative offset.
    expect(highlightScrollTop(10, 5, 64)).toBe(0);
    expect(highlightScrollTop(0, 0)).toBe(0);
  });

  it("picks the topmost box so multi-line highlights scroll to their first line", () => {
    const boxes = [
      { top: 240 },
      { top: 80 },
      { top: 160 }
    ];

    expect(topmostHighlightTop(boxes)).toBe(80);
  });

  it("returns null for empty or missing box lists so callers can fall back to centering", () => {
    expect(topmostHighlightTop([])).toBeNull();
    expect(topmostHighlightTop(undefined)).toBeNull();
    expect(topmostHighlightTop(null)).toBeNull();
  });
});
