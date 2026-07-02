import { useCallback, useEffect, useState } from "react";

import { isTauri, nativeInvoke, nativeListen } from "../native";
import type { SnipBeginPayload } from "../types";

// Desktop-Companion "Bereich erklären" (R6) — the Snipping-Tool-like region selector.
// Rendered only in the fullscreen `snip` Tauri window (see src-tauri/src/capture.rs →
// snip_start_impl / snip_finish / snip_cancel). It works on a FROZEN frame: Rust captures
// the screen first and pushes the PNG here via `snip://begin`, so the dim/marquee drawn
// by this page can never leak into the selected region. Mouse coordinates are CSS pixels;
// the frozen image is physical pixels — snip_finish gets CSS × devicePixelRatio, and Rust
// crops the stored frame (clamped) and emits `snip://result` to the chat overlay.
export function SnipOverlayPage() {
  const [frame, setFrame] = useState<SnipBeginPayload | null>(null);
  const [start, setStart] = useState<{ x: number; y: number } | null>(null);
  const [current, setCurrent] = useState<{ x: number; y: number } | null>(null);

  useEffect(() => {
    document.documentElement.classList.add("overlay-mode");
    document.body.classList.add("overlay-mode");
    return () => {
      document.documentElement.classList.remove("overlay-mode");
      document.body.classList.remove("overlay-mode");
    };
  }, []);

  useEffect(() => {
    let unlisten: (() => void) | undefined;
    nativeListen<SnipBeginPayload>("snip://begin", (payload) => {
      setFrame(payload);
      setStart(null);
      setCurrent(null);
    }).then((fn) => {
      unlisten = fn;
    });
    return () => unlisten?.();
  }, []);

  const cancel = useCallback(() => {
    setFrame(null);
    setStart(null);
    setCurrent(null);
    if (isTauri()) void nativeInvoke("snip_cancel").catch(() => {});
  }, []);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") cancel();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [cancel]);

  const rect =
    start && current
      ? {
          x: Math.min(start.x, current.x),
          y: Math.min(start.y, current.y),
          w: Math.abs(current.x - start.x),
          h: Math.abs(current.y - start.y),
        }
      : null;

  function finish() {
    if (!rect || rect.w < 8 || rect.h < 8) {
      cancel();
      return;
    }
    const dpr = window.devicePixelRatio || 1;
    setFrame(null);
    setStart(null);
    setCurrent(null);
    void nativeInvoke("snip_finish", {
      x: rect.x * dpr,
      y: rect.y * dpr,
      width: rect.w * dpr,
      height: rect.h * dpr,
    }).catch(() => {});
  }

  if (!frame) return null;

  return (
    <div
      className="snip-overlay-root"
      style={{ backgroundImage: `url(data:image/png;base64,${frame.image_base64})` }}
      onMouseDown={(event) => {
        setStart({ x: event.clientX, y: event.clientY });
        setCurrent({ x: event.clientX, y: event.clientY });
      }}
      onMouseMove={(event) => {
        if (start) setCurrent({ x: event.clientX, y: event.clientY });
      }}
      onMouseUp={finish}
    >
      {/* While dragging, the marquee's huge box-shadow provides the dim outside the hole. */}
      {rect ? (
        <div
          className="snip-overlay-marquee"
          style={{ left: rect.x, top: rect.y, width: rect.w, height: rect.h }}
        />
      ) : (
        <div className="snip-overlay-dim" />
      )}
      <div className="snip-overlay-hint">Bereich aufziehen, loslassen zum Fragen — Esc bricht ab</div>
    </div>
  );
}
