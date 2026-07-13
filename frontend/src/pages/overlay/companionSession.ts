// Pure helpers for the AI-Cursor overlay (R7 rework): chat-entry mapping between
// the durable DuckDB transcript and the rendered chat stream, plus the German
// one-liners for planned actions. No React, no Tauri — the vitest target.

import type {
  CompanionSessionMessage,
  CompanionSource,
  SelfDriveAction,
  SelfDriveVerification,
} from "../../types";

/** One rendered chat line. `system` lines are the small meta rows (actions,
 * verification verdicts, research notes) between the user/assistant bubbles. */
export type OverlayChatEntry = {
  role: "user" | "assistant" | "system";
  text: string;
  sources?: CompanionSource[];
  verification?: SelfDriveVerification | null;
};

/** Human-readable one-liner for a planned Selbst-Steuerung action. */
export function describeSelfDriveAction(action: SelfDriveAction): string {
  const label = action.label ? ` — „${action.label}“` : "";
  switch (action.type) {
    case "click":
      return `Klick auf (${Math.round(action.x ?? 0)}, ${Math.round(action.y ?? 0)})${label}`;
    case "double_click":
      return `Doppelklick auf (${Math.round(action.x ?? 0)}, ${Math.round(action.y ?? 0)})${label}`;
    case "move":
      return `Maus bewegen nach (${Math.round(action.x ?? 0)}, ${Math.round(action.y ?? 0)})${label}`;
    case "type":
      return `Tippen: „${action.text ?? ""}“`;
    case "key":
      return `Taste: ${action.keys ?? ""}`;
    case "scroll":
      return `Scrollen (dx ${action.dx ?? 0}, dy ${action.dy ?? 0})`;
    case "wait":
      return "Warten / erneut beobachten";
    case "lookup":
      return `Recherche: „${action.query ?? ""}“`;
    case "ask":
      return `Rückfrage: „${action.question ?? ""}“`;
    default:
      return action.type;
  }
}

/** Autopilot gate for one planned action: sensitive actions (password fields,
 * buy buttons, …) always downgrade to per-action confirmation, as does a paused
 * loop or disabled autopilot. */
export function shouldAutoExecute(
  action: SelfDriveAction,
  autopilot: boolean,
  paused: boolean,
): boolean {
  return autopilot && !paused && !action.sensitive;
}

/** Session title from the first user text (list display + auto-naming). */
export function sessionTitleFromText(text: string, maxLength = 60): string {
  const clean = text.replace(/\s+/g, " ").trim();
  if (clean.length <= maxLength) return clean;
  return `${clean.slice(0, maxLength - 1).trimEnd()}…`;
}

/** Map the persisted transcript back into rendered chat entries (reopening a
 * session from the list). Structured payloads become the same system lines the
 * live loops produce. */
export function mapServerMessages(messages: CompanionSessionMessage[]): OverlayChatEntry[] {
  const entries: OverlayChatEntry[] = [];
  for (const message of messages) {
    const text = (message.content ?? "").trim();
    const payload = (message.payload ?? {}) as {
      sources?: CompanionSource[];
      action?: SelfDriveAction;
      verification?: SelfDriveVerification | null;
      step?: unknown;
    };
    switch (message.role) {
      case "user":
        if (text) entries.push({ role: "user", text });
        break;
      case "assistant":
        if (text) entries.push({ role: "assistant", text, sources: payload.sources });
        break;
      case "action": {
        if (payload.verification && payload.verification.ok === false) {
          entries.push({
            role: "system",
            text: `Prüfung: fehlgeschlagen${payload.verification.note ? ` — ${payload.verification.note}` : ""}`,
            verification: payload.verification,
          });
        }
        const described = payload.action ? describeSelfDriveAction(payload.action) : "";
        const line = [text, described].filter(Boolean).join(" → ");
        if (line) entries.push({ role: "system", text: line });
        break;
      }
      case "verification":
      case "system":
        if (text) entries.push({ role: "system", text });
        break;
    }
  }
  return entries;
}

/** Whether a heuristically "guided" question should offer the step-by-step flow
 * ("Wie…", "Zeig mir…", "Führe mich…"). The user always has the explicit chip. */
export function looksLikeGuidedQuestion(text: string): boolean {
  return /^(wie\s|zeig\s|zeige\s|führe\smich|wo\sfinde|hilf\smir\sbei)/i.test(text.trim());
}
