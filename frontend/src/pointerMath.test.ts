import { describe, expect, it } from "vitest";

import { dodgeOffset, physicalToCss } from "./pointerMath";

describe("physicalToCss", () => {
  it("divides physical pixels by the devicePixelRatio", () => {
    expect(physicalToCss(200, 2)).toBe(100);
    expect(physicalToCss(150, 1.5)).toBe(100);
    expect(physicalToCss(0, 2)).toBe(0);
  });

  it("passes values through for dpr 1 and guards against dpr <= 0", () => {
    expect(physicalToCss(123, 1)).toBe(123);
    expect(physicalToCss(123, 0)).toBe(123);
    expect(physicalToCss(123, -2)).toBe(123);
  });
});

describe("dodgeOffset", () => {
  it("stays inactive while the cursor keeps its distance", () => {
    expect(dodgeOffset({ x: 100, y: 100 }, { x: 300, y: 300 })).toEqual({
      dx: 0,
      dy: 0,
      active: false,
    });
    // Exactly at the radius boundary is still inactive.
    expect(dodgeOffset({ x: 100, y: 100 }, { x: 100 - 56, y: 100 }).active).toBe(false);
  });

  it("flees along the cursor→target direction with the push magnitude", () => {
    // Cursor 30px left of the target → ring pushed right by `push`.
    const dodge = dodgeOffset({ x: 100, y: 100 }, { x: 70, y: 100 });
    expect(dodge.active).toBe(true);
    expect(dodge.dx).toBeCloseTo(72);
    expect(dodge.dy).toBeCloseTo(0);
    // Magnitude is always `push`, regardless of how close the cursor is.
    const diagonal = dodgeOffset({ x: 100, y: 100 }, { x: 90, y: 90 });
    expect(Math.hypot(diagonal.dx, diagonal.dy)).toBeCloseTo(72);
    expect(diagonal.dx).toBeCloseTo(diagonal.dy); // 45° away
  });

  it("picks a fixed direction when the cursor sits exactly on the target", () => {
    expect(dodgeOffset({ x: 100, y: 100 }, { x: 100, y: 100 })).toEqual({
      dx: 72,
      dy: 0,
      active: true,
    });
  });

  it("honours custom radius and push", () => {
    expect(dodgeOffset({ x: 0, y: 0 }, { x: 10, y: 0 }, 5, 30).active).toBe(false);
    const dodge = dodgeOffset({ x: 0, y: 0 }, { x: 10, y: 0 }, 20, 30);
    expect(dodge).toEqual({ dx: -30, dy: 0, active: true });
  });
});
