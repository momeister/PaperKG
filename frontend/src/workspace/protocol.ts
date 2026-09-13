export const PANE_IDS = ["navigator", "center", "assistant", "notes"] as const;
export type PaneId = typeof PANE_IDS[number];
export const PANE_TITLES: Record<PaneId, string> = {
  navigator: "Navigator", center: "PDF · Daten · Analyse", assistant: "Assistant", notes: "Notizen"
};
export const PANE_PROTOCOL = "sciencekg.workspace.v1";
export type PaneMessage = {
  protocol: typeof PANE_PROTOCOL;
  pane: PaneId;
  actionId: string;
  revision: number;
  type: "ready" | "commit" | "committed" | "dock" | "resync";
};
export function isPaneId(value: unknown): value is PaneId {
  return PANE_IDS.includes(value as PaneId);
}
export function isPaneMessage(value: unknown): value is PaneMessage {
  if (!value || typeof value !== "object") return false;
  const m = value as PaneMessage;
  return m.protocol === PANE_PROTOCOL && isPaneId(m.pane) && typeof m.actionId === "string"
    && Number.isSafeInteger(m.revision) && m.revision >= 0
    && ["ready", "commit", "committed", "dock", "resync"].includes(m.type);
}
