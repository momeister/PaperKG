import { createContext, useContext } from "react";

export type LlmParams = {
  temperature?: number;
  top_p?: number;
  max_tokens?: number;
  context_size?: number;
};

export type Theme = "light" | "dark";

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
