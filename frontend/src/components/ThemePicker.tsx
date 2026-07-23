import { useEffect, useRef, useState } from "react";
import { Check, Palette } from "lucide-react";

import { THEMES, THEME_META } from "../state";
import type { Theme } from "../state";

/** Reine Präsentation: drei Farbtupfer (Hintergrund/Fläche/Akzent) je Theme,
 *  damit die Karten ohne Theme-Wechsel eine Vorschau zeigen. */
const THEME_SWATCHES: Record<Theme, [string, string, string]> = {
  tag: ["#f4f5f7", "#ffffff", "#2f6feb"],
  nacht: ["#14161a", "#1c1f24", "#6f9dff"],
  observatorium: ["#0b1020", "#131a30", "#8b9cf5"],
  tiefsee: ["#0a1514", "#10201d", "#45c4d0"],
  manuskript: ["#f2efe7", "#fcfbf7", "#14697b"],
  laborlicht: ["#edf1f6", "#ffffff", "#2563eb"]
};

function ThemeCards({ theme, onSelect }: { theme: Theme; onSelect: (theme: Theme) => void }) {
  return (
    <div className="theme-card-grid" role="radiogroup" aria-label="Farbschema">
      {THEMES.map((item) => {
        const meta = THEME_META[item];
        const active = item === theme;
        return (
          <button
            key={item}
            type="button"
            role="radio"
            aria-checked={active}
            className={`theme-card ${active ? "theme-card--active" : ""}`}
            onClick={() => onSelect(item)}
          >
            <span className="theme-card-swatch" aria-hidden="true">
              {THEME_SWATCHES[item].map((color, index) => (
                <span key={index} className="theme-card-dot" style={{ background: color }} />
              ))}
            </span>
            <span className="theme-card-text">
              <strong>{meta.label}</strong>
              <span>{meta.hint}</span>
            </span>
            {active ? <Check size={15} className="theme-card-check" aria-hidden="true" /> : null}
          </button>
        );
      })}
    </div>
  );
}

/** Theme-Auswahl: als Topbar-Popover (Palette-Button) oder inline (Settings). */
export function ThemePicker({
  theme,
  onSelect,
  variant = "popover"
}: {
  theme: Theme;
  onSelect: (theme: Theme) => void;
  variant?: "popover" | "inline";
}) {
  const [open, setOpen] = useState(false);
  const wrapRef = useRef<HTMLSpanElement | null>(null);

  useEffect(() => {
    if (!open) {
      return;
    }
    function onPointerDown(event: MouseEvent) {
      if (wrapRef.current && !wrapRef.current.contains(event.target as Node)) {
        setOpen(false);
      }
    }
    function onKeyDown(event: KeyboardEvent) {
      if (event.key === "Escape") {
        setOpen(false);
      }
    }
    document.addEventListener("mousedown", onPointerDown);
    document.addEventListener("keydown", onKeyDown);
    return () => {
      document.removeEventListener("mousedown", onPointerDown);
      document.removeEventListener("keydown", onKeyDown);
    };
  }, [open]);

  if (variant === "inline") {
    return <ThemeCards theme={theme} onSelect={onSelect} />;
  }

  return (
    <span className="theme-picker-wrap" ref={wrapRef}>
      <button
        className={`icon-button theme-toggle ${open ? "icon-button--active" : ""}`}
        type="button"
        aria-label="Farbschema wählen"
        title={`Farbschema: ${THEME_META[theme].label}`}
        aria-expanded={open}
        onClick={() => setOpen((current) => !current)}
      >
        <Palette size={17} />
      </button>
      {open ? (
        <div className="theme-picker-popover pop-enter">
          <strong>Farbschema</strong>
          <ThemeCards
            theme={theme}
            onSelect={(next) => {
              onSelect(next);
              setOpen(false);
            }}
          />
        </div>
      ) : null}
    </span>
  );
}
