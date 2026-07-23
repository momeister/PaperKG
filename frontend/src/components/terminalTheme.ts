import type { ITheme } from "@xterm/xterm";

// Terminal-Paletten für die Code-Werkstatt.
//
// Bewusst *nicht* aus den App-Tokens abgeleitet: --surface & Co. beschreiben
// Karten- und Panelflächen, keine Terminalfläche, und eine Shell braucht darüber
// hinaus die vollen ANSI-16 Farben. Die Ableitung ergab je nach Auflösungsreihenfolge
// ein weißes Terminal im Nacht-Theme — hier stehen stattdessen zwei feste,
// kontrastgeprüfte Paletten.

/** Was der Nutzer in der Werkstatt einstellen kann. */
export const TERMINAL_APPEARANCES = ["auto", "dark", "light"] as const;
export type TerminalAppearance = (typeof TERMINAL_APPEARANCES)[number];

export const TERMINAL_APPEARANCE_LABEL: Record<TerminalAppearance, string> = {
  auto: "Terminal: wie Theme",
  dark: "Terminal: dunkel",
  light: "Terminal: hell"
};

export const TERMINAL_APPEARANCE_STORAGE_KEY = "sciencekg.werkstatt.terminalAppearance";

export function loadTerminalAppearance(): TerminalAppearance {
  const stored = localStorage.getItem(TERMINAL_APPEARANCE_STORAGE_KEY);
  return (TERMINAL_APPEARANCES as readonly string[]).includes(stored ?? "")
    ? (stored as TerminalAppearance)
    : "auto";
}

/** "auto" folgt dem App-Scheme, sonst gewinnt die ausdrückliche Wahl. */
export function resolveTerminalScheme(
  appearance: TerminalAppearance,
  appScheme: "dark" | "light"
): "dark" | "light" {
  return appearance === "auto" ? appScheme : appearance;
}

export const TERMINAL_PALETTES: Record<"dark" | "light", ITheme> = {
  dark: {
    background: "#14161a",
    foreground: "#d9dee5",
    cursor: "#d9dee5",
    cursorAccent: "#14161a",
    selectionBackground: "rgba(111, 157, 255, 0.32)",
    black: "#2b3138",
    red: "#f08a83",
    green: "#7fd39a",
    yellow: "#e0b45a",
    blue: "#6f9dff",
    magenta: "#c99bf0",
    cyan: "#5fc9dc",
    white: "#d9dee5",
    brightBlack: "#6b7480",
    brightRed: "#ff9f98",
    brightGreen: "#95e2b0",
    brightYellow: "#f0c975",
    brightBlue: "#8fb4ff",
    brightMagenta: "#dcb2ff",
    brightCyan: "#7fdcec",
    brightWhite: "#f2f5f8"
  },
  light: {
    background: "#fbfbfc",
    foreground: "#1f242b",
    cursor: "#1f242b",
    cursorAccent: "#fbfbfc",
    selectionBackground: "rgba(47, 111, 235, 0.22)",
    black: "#1f242b",
    red: "#b3322c",
    green: "#16794f",
    yellow: "#8a5c05",
    blue: "#2f6feb",
    magenta: "#8a3ec0",
    cyan: "#0f766e",
    white: "#d5d9df",
    brightBlack: "#5f6874",
    brightRed: "#cc4038",
    brightGreen: "#1c8f5e",
    brightYellow: "#a06d07",
    brightBlue: "#3f7ff0",
    brightMagenta: "#9d4bd6",
    brightCyan: "#118a80",
    brightWhite: "#12171d"
  }
};
