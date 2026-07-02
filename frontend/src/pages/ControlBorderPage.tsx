import { useEffect } from "react";

// "AI has control" border — a full-monitor, click-through, always-on-top window shown
// only while Selbst-Steuerung is actively driving the real mouse/keyboard (see
// src-tauri/src/overlay.rs → build_control_border / control_border_show / _hide).
// There is no separate "AI-only" cursor — the agent must move the user's real OS
// cursor to click on real windows — so this border is the visible signal that makes
// the takeover unmistakable while it's happening.
export function ControlBorderPage() {
  useEffect(() => {
    document.documentElement.classList.add("overlay-mode");
    document.body.classList.add("overlay-mode");
    return () => {
      document.documentElement.classList.remove("overlay-mode");
      document.body.classList.remove("overlay-mode");
    };
  }, []);

  return (
    <div className="control-border-frame">
      <span className="control-border-label">KI steuert gerade Maus &amp; Tastatur (Selbst-Steuerung)</span>
    </div>
  );
}
