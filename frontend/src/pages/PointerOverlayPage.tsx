import { useEffect, useState } from "react";

import { isTauri, nativeInvoke, nativeListen, signalWindowReady } from "../native";
import { dodgeOffset, physicalToCss, physicalToViewport } from "../pointerMath";
import type { PointerShowPayload } from "../types";

// AI pointer overlay (see src-tauri/src/overlay.rs → build_pointer_overlay /
// pointer_show / pointer_hide). A full-monitor, click-through, always-on-top window that
// highlights a screen point. Pure annotation: this window never receives input itself and
// never dispatches any mouse/keyboard event — it only shows the user where they could click.
//
// Two coordinate spaces arrive via `pointer://show` (see PointerShowPayload):
//   - `space: "physical"` (Desktop Companion, R6): physical monitor pixels → divided by
//     this window's devicePixelRatio (its OS bounds equal the monitor's physical size).
//   - absent/anything else (legacy UI-TARS bridge): logical pixels, drawn as-is.
//
// The ring *glides* to each new point (CSS transform transition on the anchor) and
// *dodges* the real cursor (R6 "wegschieben"): the window is click-through, so the ring
// can't be dragged — instead it polls the global cursor position while visible and flees
// when the cursor gets close. The poll stops on `pointer://hide` (emitted by both
// pointer_hide and the Rust auto-hide timer) and on unmount; show()/hide() of the OS
// window itself stay in Rust.
export function PointerOverlayPage() {
  const [point, setPoint] = useState<PointerShowPayload | null>(null);
  const [visible, setVisible] = useState(false);
  const [dodge, setDodge] = useState({ dx: 0, dy: 0, active: false });

  useEffect(() => {
    document.documentElement.classList.add("overlay-mode");
    document.body.classList.add("overlay-mode");
    return () => {
      document.documentElement.classList.remove("overlay-mode");
      document.body.classList.remove("overlay-mode");
    };
  }, []);

  // The window is created lazily on the first pointer_show — the shell queues that
  // event until we signal readiness *after* both listeners are registered.
  useEffect(() => {
    let disposed = false;
    const unlisteners: (() => void)[] = [];
    void (async () => {
      const offShow = await nativeListen<PointerShowPayload>("pointer://show", (payload) => {
        setPoint(payload);
        setVisible(true);
        setDodge({ dx: 0, dy: 0, active: false });
      });
      const offHide = await nativeListen<unknown>("pointer://hide", () => setVisible(false));
      if (disposed) {
        offShow();
        offHide();
        return;
      }
      unlisteners.push(offShow, offHide);
      await signalWindowReady();
    })();
    return () => {
      disposed = true;
      unlisteners.forEach((fn) => fn());
    };
  }, []);

  // Physical → CSS: prefer the viewport↔monitor ratio when the payload carries the
  // monitor's physical width (multi-monitor; devicePixelRatio can lag right after the
  // window moved between screens with different DPI), else divide by devicePixelRatio.
  const dpr = window.devicePixelRatio || 1;
  const toCss = (value: number): number =>
    point?.monitor_width
      ? physicalToViewport(value, point.monitor_width, window.innerWidth)
      : physicalToCss(value, dpr);
  const targetX = point ? (point.space === "physical" ? toCss(point.x) : point.x) : 0;
  const targetY = point ? (point.space === "physical" ? toCss(point.y) : point.y) : 0;

  // Cursor-dodge poll — only while a point is visible, so an idle overlay costs nothing.
  useEffect(() => {
    if (!point || !visible || !isTauri()) return;
    const id = window.setInterval(() => {
      nativeInvoke<{ x: number; y: number }>("cursor_position")
        .then((cursor) => {
          // cursor_position is global (virtual desktop) — make it monitor-relative
          // before converting, so dodging works on non-primary screens too.
          const relX = cursor.x - (point.origin_x ?? 0);
          const relY = cursor.y - (point.origin_y ?? 0);
          const ratio = window.devicePixelRatio || 1;
          const cursorCss = point.monitor_width
            ? {
                x: physicalToViewport(relX, point.monitor_width, window.innerWidth),
                y: physicalToViewport(relY, point.monitor_width, window.innerWidth),
              }
            : { x: physicalToCss(relX, ratio), y: physicalToCss(relY, ratio) };
          setDodge(dodgeOffset({ x: targetX, y: targetY }, cursorCss));
        })
        .catch(() => {
          /* transient invoke failure — keep the last offset */
        });
    }, 50);
    return () => window.clearInterval(id);
  }, [point, visible, targetX, targetY]);

  if (!point) return null;

  return (
    <div className="pointer-overlay-frame">
      <div
        className={`pointer-overlay-anchor ${dodge.active ? "pointer-overlay-anchor--dodge" : ""}`}
        style={{
          transform: `translate3d(${targetX + dodge.dx}px, ${targetY + dodge.dy}px, 0)`,
          opacity: visible ? (dodge.active ? 0.35 : 1) : 0,
        }}
      >
        <div className="pointer-overlay-ring" />
        {point.label ? <div className="pointer-overlay-label">{point.label}</div> : null}
      </div>
    </div>
  );
}
