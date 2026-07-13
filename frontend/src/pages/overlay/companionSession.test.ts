import { describe, expect, it } from "vitest";

import type { CompanionSessionMessage } from "../../types";
import {
  describeSelfDriveAction,
  looksLikeGuidedQuestion,
  mapServerMessages,
  sessionTitleFromText,
  shouldAutoExecute,
} from "./companionSession";

describe("describeSelfDriveAction", () => {
  it("describes clicks with label", () => {
    expect(describeSelfDriveAction({ type: "click", x: 100.4, y: 200.6, label: "Startmenü" })).toBe(
      "Klick auf (100, 201) — „Startmenü“",
    );
  });
  it("describes meta verbs", () => {
    expect(describeSelfDriveAction({ type: "ask", question: "Welche Datei?" })).toBe(
      "Rückfrage: „Welche Datei?“",
    );
    expect(describeSelfDriveAction({ type: "lookup", query: "GIMP Ebenen" })).toBe(
      "Recherche: „GIMP Ebenen“",
    );
    expect(describeSelfDriveAction({ type: "wait" })).toContain("Warten");
  });
});

describe("shouldAutoExecute", () => {
  const click = { type: "click" as const, x: 10, y: 10, label: "Startmenü" };
  it("executes harmless actions in autopilot", () => {
    expect(shouldAutoExecute(click, true, false)).toBe(true);
  });
  it("downgrades sensitive actions to confirmation even in autopilot", () => {
    expect(shouldAutoExecute({ ...click, sensitive: true }, true, false)).toBe(false);
  });
  it("never auto-executes when paused or without autopilot", () => {
    expect(shouldAutoExecute(click, true, true)).toBe(false);
    expect(shouldAutoExecute(click, false, false)).toBe(false);
  });
});

describe("sessionTitleFromText", () => {
  it("passes short text through", () => {
    expect(sessionTitleFromText("Öffne den Editor")).toBe("Öffne den Editor");
  });
  it("collapses whitespace and truncates with ellipsis", () => {
    const long = "a".repeat(80);
    const title = sessionTitleFromText(`  ${long}  `);
    expect(title.length).toBeLessThanOrEqual(60);
    expect(title.endsWith("…")).toBe(true);
  });
});

describe("mapServerMessages", () => {
  const messages: CompanionSessionMessage[] = [
    { id: "1", session_id: "s", role: "user", content: "Wo ist Speichern?" },
    {
      id: "2",
      session_id: "s",
      role: "assistant",
      content: "Oben links.",
      payload: { sources: [{ type: "web", title: "Doku", url: "https://x" }] },
    },
    {
      id: "3",
      session_id: "s",
      role: "action",
      content: "Ich klicke das Menü.",
      payload: {
        action: { type: "click", x: 10, y: 20, label: "Menü" },
        verification: { ok: false, note: "Nichts passiert." },
      },
    },
    { id: "4", session_id: "s", role: "system", content: "Recherche: GIMP" },
    { id: "5", session_id: "s", role: "user", content: "   " },
  ];

  it("maps roles, sources, actions and verification lines", () => {
    const entries = mapServerMessages(messages);
    expect(entries).toHaveLength(5); // empty user line dropped, action → verify + action line
    expect(entries[0]).toEqual({ role: "user", text: "Wo ist Speichern?" });
    expect(entries[1].role).toBe("assistant");
    expect(entries[1].sources?.[0].url).toBe("https://x");
    expect(entries[2].role).toBe("system");
    expect(entries[2].text).toContain("fehlgeschlagen");
    expect(entries[2].verification?.ok).toBe(false);
    expect(entries[3].text).toContain("Klick auf (10, 20)");
    expect(entries[4]).toEqual({ role: "system", text: "Recherche: GIMP" });
  });
});

describe("looksLikeGuidedQuestion", () => {
  it("detects how-to phrasings", () => {
    expect(looksLikeGuidedQuestion("Wie öffne ich die Einstellungen?")).toBe(true);
    expect(looksLikeGuidedQuestion("Zeig mir den Export")).toBe(true);
    expect(looksLikeGuidedQuestion("Was ist das hier?")).toBe(false);
  });
});
