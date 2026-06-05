import { describe, expect, it } from "vitest";

import { bestMatchFor, findPageMatch, highlightQuerySignature, normalizeHighlightBoxes, type PageMatch } from "./PdfPane";

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
