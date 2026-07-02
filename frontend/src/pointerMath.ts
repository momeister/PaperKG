// Pure coordinate helpers for the Desktop-Companion pointer overlay (R6).
// Kept free of DOM/Tauri so they are unit-testable (pointerMath.test.ts).

export type Point = { x: number; y: number };

/** Convert a physical-pixel coordinate (screenshot / cursor_position space) into this
 * window's CSS pixels. The pointer window's OS bounds are set to the monitor's physical
 * size, so CSS px × devicePixelRatio = physical px. */
export function physicalToCss(value: number, devicePixelRatio: number): number {
  return devicePixelRatio > 0 ? value / devicePixelRatio : value;
}

/** Convert a monitor-relative physical-pixel coordinate into this window's CSS pixels
 * using the actual viewport↔monitor ratio instead of devicePixelRatio. Robust right
 * after the full-monitor window was moved to a screen with a different scale factor,
 * where devicePixelRatio can still report the old monitor's value. The ratio is
 * uniform, so the width-derived factor applies to y as well. */
export function physicalToViewport(
  value: number,
  monitorPhysicalWidth: number,
  viewportCssWidth: number,
): number {
  return monitorPhysicalWidth > 0 ? value * (viewportCssWidth / monitorPhysicalWidth) : value;
}

/** Dodge offset for the pointer ring: when the real cursor comes within `radius` of the
 * ring's target, the ring flees `push` pixels along the cursor→target direction (the
 * window is click-through, so "wegschieben" can't be a drag — the ring yields instead).
 * Returns a zero offset with `active: false` while the cursor keeps its distance. */
export function dodgeOffset(
  target: Point,
  cursor: Point,
  radius = 56,
  push = 72,
): { dx: number; dy: number; active: boolean } {
  const dx = target.x - cursor.x;
  const dy = target.y - cursor.y;
  const distance = Math.hypot(dx, dy);
  if (distance >= radius) {
    return { dx: 0, dy: 0, active: false };
  }
  if (distance < 1e-6) {
    // Cursor sits exactly on the target — no direction to flee along; pick one.
    return { dx: push, dy: 0, active: true };
  }
  return { dx: (dx / distance) * push, dy: (dy / distance) * push, active: true };
}
