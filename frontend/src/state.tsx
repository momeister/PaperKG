import { createContext, useContext } from "react";

export type LlmParams = {
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  context_size?: number;
};

/** Die wählbaren Theme-Presets (siehe styles/themes.css). "tag"/"nacht" sind die
 *  schlichten Standardthemes und stehen deshalb vorn; die vier charaktervolleren
 *  Presets bleiben als Alternativen erhalten. */
export const THEMES = ["tag", "nacht", "observatorium", "tiefsee", "manuskript", "laborlicht"] as const;
export type Theme = (typeof THEMES)[number];

export type ThemeScheme = "dark" | "light";

/** Anzeige-Metadaten pro Theme; `counterpart` ist das Ziel des Hell/Dunkel-
 *  Schnellwechsels (behält den Charakter der Theme-Familie bei). */
export const THEME_META: Record<Theme, { label: string; hint: string; scheme: ThemeScheme; counterpart: Theme }> = {
  tag: { label: "Tag", hint: "Neutral hell", scheme: "light", counterpart: "nacht" },
  nacht: { label: "Nacht", hint: "Neutral dunkel", scheme: "dark", counterpart: "tag" },
  observatorium: { label: "Observatorium", hint: "Tiefes Blau, Periwinkle", scheme: "dark", counterpart: "manuskript" },
  tiefsee: { label: "Tiefsee", hint: "Grün-Schiefer, Cyan", scheme: "dark", counterpart: "laborlicht" },
  manuskript: { label: "Manuskript", hint: "Warmes Papier, Petrol", scheme: "light", counterpart: "observatorium" },
  laborlicht: { label: "Laborlicht", hint: "Kühles Weiß, Blau", scheme: "light", counterpart: "tiefsee" }
};

/** Gespeicherte/übergebene Theme-Namen normalisieren; mappt die Alt-Werte
 *  "dark"/"light" auf die neuen Standard-Presets. */
export function normalizeTheme(value: string | null | undefined): Theme | null {
  if (!value) {
    return null;
  }
  if ((THEMES as readonly string[]).includes(value)) {
    return value as Theme;
  }
  if (value === "dark") {
    return "observatorium";
  }
  if (value === "light") {
    return "manuskript";
  }
  return null;
}

export type AppState = {
  activeProject?: string;
  setActiveProject: (project?: string) => void;
  provider?: string;
  setProvider: (provider?: string) => void;
  model?: string;
  setModel: (model?: string) => void;
  llmParams: LlmParams;
  setLlmParams: (params: LlmParams) => void;
  theme: Theme;
  setTheme: (theme: Theme) => void;
  /** Schneller Hell/Dunkel-Wechsel innerhalb der Theme-Familie. */
  toggleTheme: () => void;
  /** Globaler UI-Zoom (1 = 100%); skaliert das gesamte Programm. */
  fontScale: number;
  setFontScale: (scale: number) => void;
};

/** Grenzen + Schrittweite für den globalen UI-Zoom (siehe App.tsx / SettingsPage). */
export const FONT_SCALE_MIN = 0.8;
export const FONT_SCALE_MAX = 1.6;
export const FONT_SCALE_STEP = 0.1;

/** Auf den erlaubten Bereich begrenzen und auf 2 Nachkommastellen runden. */
export function clampFontScale(scale: number): number {
  if (!Number.isFinite(scale)) {
    return 1;
  }
  const bounded = Math.min(FONT_SCALE_MAX, Math.max(FONT_SCALE_MIN, scale));
  return Math.round(bounded * 100) / 100;
}

export const AppStateContext = createContext<AppState | null>(null);

export function useAppState() {
  const state = useContext(AppStateContext);
  if (!state) {
    throw new Error("AppStateContext is missing.");
  }
  return state;
}

/** Wie useAppState, aber ohne Provider-Zwang (z.B. für wiederverwendbare
 *  Komponenten, die auch in Tests ohne App-Shell gerendert werden). */
export function useOptionalAppState(): AppState | null {
  return useContext(AppStateContext);
}
